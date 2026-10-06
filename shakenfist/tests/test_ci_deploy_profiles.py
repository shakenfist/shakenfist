# Copyright 2026 Michael Still and contributors

"""The CI deploy profiles render, and render to something that can deploy.

A deploy profile (tools/ci-deploy-profiles/*.j2) is rendered and applied by
shakenfist/actions' tools/ci-apply-deploy-profile.py, in another repository,
at the start of a merge-queue run. A mistake in one would otherwise surface
there, half an hour into a cluster build, or not at all: a kerbside_url whose
host is not the proxy certificate's name deploys cleanly and then fails every
console, and a loopback api_url on a Kerbside host scrapes the wrong sf-api.

So each profile is rendered here the way actions renders it -- Jinja2 with
StrictUndefined, against a context built from a facts file shaped like the
one actions' topology playbook writes (ansible/ci-include-common-localhost.yml
there) -- and checked against what can be checked offline:

* the schema actions accepts: only its five top-level keys, groups that are
  new and name only hosts the topology has, string test_env values, plain
  unit globs;
* for a profile which deploys Kerbside, the constraints the kerbside and node
  roles' argument_specs and the kerbside role's validate entry point would
  otherwise enforce mid-deploy, plus the two that only a CI profile can get
  wrong: a kerbside_system_key equal to actions' CI system_key, and a database
  password which differs between kerbside_sql_url and the SQL that creates the
  user.

docs/plans/PLAN-kerbside-deployer-phase-04-ci.md (D1, D2 and D6) is the
design.
"""

import glob
import os
import re
import urllib.parse

import jinja2
import yaml

from shakenfist.tests import base


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


PROFILE_DIR = os.path.join(_repo_root(), 'tools', 'ci-deploy-profiles')

# Facts files shaped like the one actions' topology playbooks write, by
# topology. The mesh addresses are the real ones (actions'
# ansible/ci-topology-<topology>.yml); the egress addresses are under-cloud
# addresses, different on every run, so any will do. A profile's topology is
# the suffix of its name, so kerbside-slim-tier.yml.j2 renders against
# slim-tier.
TOPOLOGY_FACTS = {
    'slim-tier': {
        'nodes': [
            {'name': 'primary', 'egress_ip': '192.168.71.20', 'mesh_ip': '10.0.1.10',
             'is_hypervisor': True, 'is_network_node': True, 'is_database_node': True},
            {'name': 'sf1', 'egress_ip': '192.168.71.21', 'mesh_ip': '10.0.1.11',
             'is_hypervisor': True, 'is_network_node': False, 'is_database_node': True},
            {'name': 'sf2', 'egress_ip': '192.168.71.22', 'mesh_ip': '10.0.1.12',
             'is_hypervisor': True, 'is_network_node': False, 'is_database_node': False},
        ],
    },
}

# The groups actions' tools/ci-make-inventory.py already writes, which a
# profile may not redefine, plus the two ansible reserves.
EXISTING_GROUPS = ('all', 'ungrouped', 'allsf', 'hypervisors', 'network_node', 'database_node',
                   'etcd_master')

# The schema, from tools/ci-apply-deploy-profile.py in shakenfist/actions.
TOP_LEVEL_KEYS = ('groups', 'extra_vars', 'mariadb_sql', 'redeploy_check', 'test_env')
IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
UNIT_GLOB = re.compile(r'^[A-Za-z0-9_.@:*?\[\]-]+$')

# The system_key every CI cluster is deployed with: the default of the
# system_key input of build-smoke-cluster/action.yml in shakenfist/actions,
# which smoke-cluster.yml does not override. The node role refuses a
# kerbside_system_key equal to system_key, since both are minted into the
# system namespace.
CI_SYSTEM_KEY = 'ci-system-key-do-not-use-in-production-ci-system-key'

# Kerbside's shipped default seed, which the kerbside role refuses.
KERBSIDE_SEED_SENTINEL = '~~unconfigured~~'

# The kerbside role's default api_url (roles/kerbside/defaults/main.yml).
ROLE_DEFAULT_API_URL = 'http://localhost:13000'

# Variables the kerbside role's validate entry point requires once the
# kerbside group is not empty.
KERBSIDE_REQUIRED = ('kerbside_url', 'kerbside_system_key', 'kerbside_public_fqdn', 'kerbside_sql_url',
                     'kerbside_auth_secret_seed')

# Variables the Shaken Fist nodes read as well as the Kerbside hosts
# (examples/_shared/site.yml), so a profile must set them for every host --
# as extra vars -- and not only as vars of its kerbside group.
KERBSIDE_CLUSTER_WIDE = ('kerbside_url', 'kerbside_system_key')

CREATE_USER = re.compile(r"CREATE USER (?:IF NOT EXISTS )?'([^']*)'@'[^']*' IDENTIFIED BY '([^']*)'",
                         re.IGNORECASE)
CREATE_DATABASE = re.compile(r'CREATE DATABASE (?:IF NOT EXISTS )?`?(\w+)`?', re.IGNORECASE)


def facts_context(facts):
    """The template context actions builds from a facts file.

    As facts_context() in actions' tools/ci-apply-deploy-profile.py: nodes by
    name, with mesh_ip falling back to egress_ip. workspace is left out, so a
    profile which uses it fails here as it would on a run that lacks it.
    """
    nodes = {}
    for spec in facts['nodes']:
        node = dict(spec)
        node['mesh_ip'] = spec.get('mesh_ip') or spec['egress_ip']
        nodes[spec['name']] = node
    return {'nodes': nodes}


def render(path, facts):
    with open(path) as f:
        template = f.read()
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    return yaml.safe_load(environment.from_string(template).render(**facts_context(facts)))


def topology_of(path):
    name = os.path.basename(path)
    for topology in TOPOLOGY_FACTS:
        if name.endswith('-%s.yml.j2' % topology):
            return topology
    return None


def is_loopback_host(host):
    """Whether a URL's host is loopback, by the kerbside role's own rule.

    validate.yml's loopback api_url check: localhost and any name under it,
    127.0.0.0/8 including short forms such as 127.1, ::1 in any spelling, its
    IPv4-mapped form, and the unspecified addresses, which Linux connects to
    the local host.
    """
    host = (host or '').lower()
    return (host in ('0.0.0.0', '::')
            or re.match(r'^(.*[.])?localhost[.]?$', host) is not None
            or re.match(r'^127([.][0-9]+){0,3}$', host) is not None
            or re.match(r'^[0:]*:0{0,3}1$', host) is not None
            or re.match(r'^(0{0,4}:)*:?ffff:127[.]', host) is not None)


class CiDeployProfilesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.profiles = {}
        for path in sorted(glob.glob(os.path.join(PROFILE_DIR, '*.j2'))):
            name = os.path.basename(path)
            topology = topology_of(path)
            self.assertIsNotNone(
                topology,
                '%s does not end in -<topology>.yml.j2 for a topology this test has '
                'facts for (%s). Add the topology to TOPOLOGY_FACTS.'
                % (name, ', '.join(sorted(TOPOLOGY_FACTS))))
            profile = render(path, TOPOLOGY_FACTS[topology])
            self.profiles[name] = (topology, {} if profile is None else profile)

    def _kerbside_profiles(self):
        """The profiles which deploy Kerbside, with their kerbside group."""
        for name, (topology, profile) in self.profiles.items():
            group = (profile.get('groups') or {}).get('kerbside')
            if group:
                yield name, topology, profile, group

    def _effective(self, profile, group, var, default=None):
        """A variable as a Kerbside host sees it: extra vars beat group vars."""
        extra_vars = profile.get('extra_vars') or {}
        if var in extra_vars:
            return extra_vars[var]
        return (group.get('vars') or {}).get(var, default)

    def test_there_are_profiles(self):
        self.assertTrue(
            self.profiles,
            'No profile under %s, so this test checks nothing. If they moved, '
            'update PROFILE_DIR.' % PROFILE_DIR)

    def test_only_schema_keys(self):
        for name, (_, profile) in self.profiles.items():
            self.assertIsInstance(profile, dict, '%s does not render to a mapping.' % name)
            unknown = sorted(set(profile) - set(TOP_LEVEL_KEYS))
            self.assertEqual(
                [], unknown,
                '%s has top-level keys actions refuses; allowed: %s.' % (name, ', '.join(TOP_LEVEL_KEYS)))

    def test_groups_are_new_and_name_real_hosts(self):
        for name, (topology, profile) in self.profiles.items():
            hosts = {node['name'] for node in TOPOLOGY_FACTS[topology]['nodes']}
            for group_name, group in (profile.get('groups') or {}).items():
                self.assertRegex(group_name, IDENTIFIER, '%s: group name is not an identifier.' % name)
                self.assertNotIn(
                    group_name, EXISTING_GROUPS,
                    '%s redefines group %s, which the inventory already has.' % (name, group_name))
                self.assertTrue(group.get('hosts'), '%s: group %s has no hosts.' % (name, group_name))
                for host in group['hosts']:
                    self.assertIn(
                        host, hosts,
                        '%s: group %s names %s, which %s does not have (%s).'
                        % (name, group_name, host, topology, ', '.join(sorted(hosts))))

    def test_test_env_and_redeploy_units_are_well_formed(self):
        for name, (_, profile) in self.profiles.items():
            for var, value in (profile.get('test_env') or {}).items():
                self.assertRegex(var, IDENTIFIER, '%s: test_env name is not a shell identifier.' % name)
                self.assertIsInstance(value, str, '%s: test_env %s is not a string; quote it.' % (name, var))
            for unit in (profile.get('redeploy_check') or {}).get('units') or []:
                self.assertRegex(unit, UNIT_GLOB, '%s: redeploy unit %r is not a plain glob.' % (name, unit))

    def test_kerbside_required_variables_are_set(self):
        for name, _, profile, group in self._kerbside_profiles():
            for var in KERBSIDE_REQUIRED:
                self.assertTrue(
                    self._effective(profile, group, var),
                    '%s deploys Kerbside without %s, which the kerbside role requires.' % (name, var))
            for var in KERBSIDE_CLUSTER_WIDE:
                self.assertIn(
                    var, profile.get('extra_vars') or {},
                    '%s does not set %s as an extra var. The Shaken Fist nodes read it too, so '
                    'a kerbside group var alone would leave the two sides disagreeing.' % (name, var))

    def test_kerbside_seed(self):
        for name, _, profile, group in self._kerbside_profiles():
            seed = str(self._effective(profile, group, 'kerbside_auth_secret_seed', ''))
            self.assertNotEqual(
                KERBSIDE_SEED_SENTINEL, seed,
                "%s: kerbside_auth_secret_seed is Kerbside's unconfigured sentinel." % name)
            self.assertGreaterEqual(
                len(seed), 32, '%s: kerbside_auth_secret_seed is shorter than 32 characters.' % name)

    def test_kerbside_system_key(self):
        for name, _, profile, group in self._kerbside_profiles():
            key = str(self._effective(profile, group, 'kerbside_system_key', ''))
            self.assertGreaterEqual(
                len(key), 16, '%s: kerbside_system_key is shorter than 16 characters.' % name)
            self.assertNotEqual(
                CI_SYSTEM_KEY, key,
                "%s: kerbside_system_key equals actions' CI system_key, which the node role "
                'refuses.' % name)

    def test_kerbside_url_host_is_the_public_fqdn(self):
        # The proxy certificate is issued for kerbside_public_fqdn, and the
        # console token audience is kerbside_url, so a client following one
        # must land on a name the other certifies.
        for name, _, profile, group in self._kerbside_profiles():
            url = str(self._effective(profile, group, 'kerbside_url', ''))
            fqdn = str(self._effective(profile, group, 'kerbside_public_fqdn', ''))
            self.assertEqual(
                fqdn, urllib.parse.urlsplit(url).hostname,
                "%s: kerbside_url's host is not kerbside_public_fqdn." % name)

    def test_kerbside_api_url_is_not_loopback(self):
        # Co-located Kerbside hosts may legally use loopback, but then the
        # scrape never leaves the host, which is not what the lane is for
        # (the plan's S4).
        for name, _, profile, group in self._kerbside_profiles():
            api_url = str(self._effective(profile, group, 'api_url', ROLE_DEFAULT_API_URL))
            host = urllib.parse.urlsplit(api_url).hostname
            self.assertFalse(
                is_loopback_host(host),
                '%s: api_url on the Kerbside hosts is loopback (%s), so Kerbside would scrape '
                'its own node. Point it at another node.' % (name, host))

    def test_kerbside_database_matches_its_sql(self):
        for name, _, profile, group in self._kerbside_profiles():
            url = urllib.parse.urlsplit(str(self._effective(profile, group, 'kerbside_sql_url', '')))
            sql = profile.get('mariadb_sql') or ''
            users = CREATE_USER.findall(sql)
            self.assertEqual(
                1, len(users),
                '%s deploys Kerbside but its mariadb_sql does not create exactly one user.' % name)
            user, password = users[0]
            self.assertEqual(
                user, url.username,
                "%s: kerbside_sql_url's user is not the one mariadb_sql creates." % name)
            # Compared, never printed.
            self.assertTrue(
                password == url.password,
                "%s: kerbside_sql_url's password is not the one mariadb_sql creates the user "
                'with.' % name)
            self.assertIn(
                url.path.lstrip('/'), CREATE_DATABASE.findall(sql),
                "%s: kerbside_sql_url's database is not one mariadb_sql creates." % name)
