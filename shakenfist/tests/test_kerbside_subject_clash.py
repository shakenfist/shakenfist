# Copyright 2026 Michael Still and contributors

"""The Kerbside public FQDN clash refusal covers the subjects a hypervisor
certificate carries.

The Deploy Kerbside play issues the proxy certificate with
``cert_cn: kerbside_public_fqdn``, from the same CA which signs the
hypervisors' SPICE certificates, so a public FQDN equal to a subject one of
those certificates carries hands the kerbside group a key which satisfies
that hypervisor's pinned host subject. ``kerbside_source.yml`` refuses that
before any host is changed.

Two names per ``allsf`` host reach a certificate subject, which is why the
refusal compares two and not one (#4817):

* the inventory host name, which ``internal_ca`` makes the CN because the
  play which issues the per-host certificates passes only ``hostname``; and
* ``node_name``, which is the Shaken Fist name Kerbside substitutes as
  ``CN=<node>`` when ``synthesize_host_subject`` is set and a node publishes
  no ``spice_server_cert_subject``.

Nothing else in this repository evaluates that refusal offline:
``tools/ci-test-kerbside-wiring.sh`` drives it with a real ansible, but it
needs ansible-core and network access to Ansible Galaxy, so it does not run
on a pull request. The conditions are therefore read out of the task here and
evaluated with Jinja2 -- the same filters ansible would apply, all of them
Jinja builtins -- against inventories shaped like the ones the refusal is
for. A revert to comparing the inventory name alone fails
``test_a_node_name_clash_is_refused``; a comparison which forgets to
normalise case fails ``test_a_differently_cased_name_is_refused``; and
widening it to the hosts' addresses, which carry no certificate, fails
``test_a_host_address_is_accepted``.
"""

import os

import jinja2
import yaml

from shakenfist.tests import base


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


SOURCE_TASKS = os.path.join(
    _repo_root(), 'shakenfist', 'deploy', 'collection', 'roles', 'node', 'tasks',
    'kerbside_source.yml')
SITE_YML = os.path.join(_repo_root(), 'examples', '_shared', 'site.yml')

# The inventory the refusal is evaluated against below: one Shaken Fist host
# whose inventory name is short and whose Shaken Fist name is fully qualified,
# which is the inventory the narrow check missed, plus one which sets no
# node_name at all.
HOSTVARS = {
    'sf1': {'node_name': 'sf1.example.com', 'node_mesh_ip': '10.0.0.1',
            'ansible_host': 'sf1.mgmt.example.com'},
    'sf2': {'node_mesh_ip': '10.0.0.2'},
}
ALLSF = sorted(HOSTVARS)


def _clash_conditions():
    """The conditions of the task which refuses a clashing public FQDN."""
    with open(SOURCE_TASKS) as f:
        tasks = yaml.safe_load(f)
    found = []
    for task in tasks:
        assertion = task.get('ansible.builtin.assert') or {}
        conditions = assertion.get('that') or []
        if isinstance(conditions, str):
            conditions = [conditions]
        if any('kerbside_public_fqdn' in c and 'item' in c for c in conditions):
            found.append((task, [str(c) for c in conditions]))
    if len(found) != 1:
        raise AssertionError(
            'kerbside_source.yml has %d tasks comparing kerbside_public_fqdn '
            'against a looped host; this test can no longer find the clash '
            'refusal.' % len(found))
    return found[0]


def _refuses(fqdn, hostvars=None, allsf=None):
    """Whether the task's conditions refuse fqdn, as ansible would evaluate them.

    Every condition is rendered as a Jinja expression per loop item, exactly
    as ansible evaluates an assert with a loop: the refusal fires when any
    condition is false for any host.
    """
    task, conditions = _clash_conditions()
    hostvars = HOSTVARS if hostvars is None else hostvars
    allsf = ALLSF if allsf is None else allsf
    environment = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    for item in allsf:
        for condition in conditions:
            rendered = environment.from_string(
                '{{ (%s) }}' % condition).render(
                    kerbside_public_fqdn=fqdn, item=item, hostvars=hostvars)
            if rendered != 'True':
                return True
    return False


class KerbsideSubjectClashTestCase(base.ShakenFistTestCase):
    def test_the_task_is_looped_over_allsf(self):
        task, _ = _clash_conditions()
        self.assertIn(
            'allsf', str(task.get('loop')),
            'The Kerbside public FQDN clash refusal no longer loops over the '
            'allsf group, so it no longer compares the public FQDN against '
            'the hosts whose certificates could collide with it.')
        self.assertIn(
            'kerbside_public_fqdn', str(task.get('when')),
            'The clash refusal no longer skips an unset kerbside_public_fqdn, '
            'so a cluster with no Kerbside host would be judged on a value it '
            'does not have.')

    def test_an_inventory_name_clash_is_refused(self):
        # What the refusal has always caught, kept as a floor.
        self.assertTrue(_refuses('sf1'))
        self.assertTrue(_refuses('sf2'))

    def test_a_node_name_clash_is_refused(self):
        # The widening: sf1's certificate is issued as CN=sf1, but Kerbside
        # synthesizes CN=sf1.example.com for it from the node's Shaken Fist
        # name, so a proxy certificate with that CN satisfies the pin.
        self.assertTrue(_refuses('sf1.example.com'))

    def test_a_differently_cased_name_is_refused(self):
        # The SPICE verifier compares subject values case- and
        # whitespace-normalised, so these are the same subject.
        self.assertTrue(_refuses('SF1'))
        self.assertTrue(_refuses('SF1.Example.COM'))
        self.assertTrue(_refuses(' sf1 '))

    def test_a_distinct_name_is_accepted(self):
        # The remedy the refusal's own message recommends.
        self.assertFalse(_refuses('console.sf1.example.com'))
        self.assertFalse(_refuses('kerbside.example.com'))

    def test_a_host_address_is_accepted(self):
        # Deliberately not refused: an address is where a hypervisor is
        # reached, not what its certificate says it is, and the message
        # recommends it on a single-host cluster. The Kerbside CI lane
        # deploys with kerbside_public_fqdn set to a node's mesh address
        # (tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2), so refusing
        # one would refuse that lane's own deploy.
        self.assertFalse(_refuses('10.0.0.1'))
        self.assertFalse(_refuses('sf1.mgmt.example.com'))

    def test_a_host_without_a_node_name_is_judged_on_its_inventory_name(self):
        # hostvars carries no node_name for sf2, so the second comparison
        # falls back to the inventory name rather than erroring the play or
        # inventing a name no certificate carries.
        self.assertTrue(_refuses('sf2', hostvars={'sf2': {}}, allsf=['sf2']))
        self.assertFalse(_refuses('sf2.example.com', hostvars={'sf2': {}}, allsf=['sf2']))

    def test_an_empty_allsf_group_refuses_nothing(self):
        # A cluster whose inventory has no allsf group at all: the loop is
        # empty, so nothing is compared and nothing is refused.
        self.assertFalse(_refuses('sf1', hostvars={}, allsf=[]))

    def test_the_hypervisor_certificates_carry_the_names_compared(self):
        """The subjects site.yml issues are the ones the refusal compares.

        The refusal hard-codes which variables reach a hypervisor
        certificate's subject. The per-host certificate play passes only
        ``hostname``, so ``cert_cn`` defaults to it and the CN is the
        inventory host name; the Kerbside play overrides ``cert_cn`` with
        ``kerbside_public_fqdn``. Issue a hypervisor's certificate for
        anything else -- an ``ansible_host``, say -- and the refusal must
        learn that name too, so fail here rather than silently leaving the
        collision unchecked.
        """
        with open(SITE_YML) as f:
            plays = yaml.safe_load(f)
        issued = []
        for play in plays:
            for task in play.get('tasks') or []:
                role = task.get('ansible.builtin.include_role') or {}
                if role.get('tasks_from') != 'host_certificate':
                    continue
                issued.append((play.get('hosts'), task.get('vars') or {}))
        self.assertTrue(
            issued,
            'No play in site.yml issues a host certificate, so this test '
            'cannot tell what subject a hypervisor certificate carries.')
        for hosts, task_vars in issued:
            if 'kerbside' in str(hosts):
                self.assertEqual(
                    '{{ kerbside_public_fqdn }}', task_vars.get('cert_cn'),
                    'The Kerbside proxy certificate is no longer issued with '
                    "kerbside_public_fqdn as its CN, so kerbside_source.yml's "
                    'clash refusal compares the wrong value.')
                continue
            self.assertEqual(
                '{{ inventory_hostname }}', task_vars.get('hostname'),
                "site.yml issues a Shaken Fist host's certificate for %r "
                'rather than its inventory name; the clash refusal in '
                'kerbside_source.yml compares the inventory name.'
                % task_vars.get('hostname'))
            self.assertIsNone(
                task_vars.get('cert_cn'),
                'site.yml now overrides cert_cn when it issues a Shaken Fist '
                "host's certificate, so the hypervisor subject is no longer "
                'the inventory name. Teach the clash refusal in '
                'kerbside_source.yml the new name.')
