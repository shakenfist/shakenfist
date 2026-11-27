# Copyright 2026 Michael Still and contributors

"""Where the human login coverage runs, and why that is part of its correctness.

``cluster_ci_tests/test_oidc_login.py`` covers human login against a real
identity provider. The provider is a container stood up on the host the
functional suite runs on, by ``tools/ci-idp-keycloak.sh``, from a step in
``shakenfist/actions``' ``smoke-cluster.yml`` -- and those tests **fail**, rather
than skip, when the descriptor that step writes is absent on a deployment which
is supposed to have one. That is deliberate: a test which skips when its fixture
is missing, where the thing that installs the fixture lives in a repository the
test cannot see, cannot be audited from either side.

Which makes two things part of that assertion's correctness rather than details
of the workflows, and neither is checkable from a cluster job -- the lane in
question runs neither on ``pull_request`` nor in the merge queue, so no pull
request ever exercises it. This file is what checks them on every pull request
instead:

* **The provider runs on one topology, and that topology is weekly.** Its
  primary carries the hypervisor, network and database roles and is sized at
  12288 MiB; the topology the merge-gating cluster lanes deploy gives its
  primary none of those roles and 4096 MiB, shared with sf-api, the node's own
  services, the suite's virtualenv and five test workers. Moving the provider's
  lane into the merge queue would start a container on that smaller primary on
  every merge, which is a flaky merge gate on the fleet's smallest host. It must
  fail a test here rather than be discovered in production.

* **Every deployment the cluster suite runs against is one the guard has an
  answer for.** The guard decides "is a provider expected here?" from the
  cluster: the host running the suite is identified in the node roster and
  asked whether it is also a hypervisor, with the single machine deployment
  carved out first. A topology running ``cluster-ci.conf`` which is neither the
  one carrying a provider nor one of the two that carve out would be judged by a
  rule nobody wrote about it, and on a weekly lane that failure arrives as an
  issue up to a week later.

The topology figures are duplicated here rather than derived, for the reason
``test_ci_structural_minimum.py`` gives for duplicating its own: that repository
is not importable from here, so this catches a figure which no longer describes
the fleet only when a human updates it -- which is the intent, because moving a
provider onto a smaller primary is supposed to require a deliberate edit. The
role-flag models of both topologies are reused from that file rather than
written again, so there is one copy of them.

The descriptor's key set and the discovery capture are held here too, for the
same reason: both are read by a suite which runs weekly against a deployed
cluster, and a mismatch in either is a ``KeyError`` or a vacuous comparison
discovered there.
"""

import ast
import glob
import json
import os

import yaml

from shakenfist.tests import base
from shakenfist.tests import test_ci_idp_keycloak_seams as idp_seams
from shakenfist.tests import test_ci_structural_minimum as structural


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
WORKFLOWS = os.path.join(REPO_ROOT, '.github', 'workflows')

LOGIN_TEST = os.path.join(
    REPO_ROOT, 'shakenfist', 'deploy', 'shakenfist_ci', 'cluster_ci_tests',
    'test_oidc_login.py')
DISCOVERY_CAPTURE = os.path.join(
    REPO_ROOT, 'shakenfist', 'tests', 'files', 'idp',
    'keycloak-discovery.json')

# The lane the provider is stood up on, and the two callers which decide when a
# lane runs. The merge-gating caller is the one a provider must stay out of.
PROVIDER_LANE = 'debian-13-slim-tier'
PROVIDER_TOPOLOGY = 'slim-tier'
MERGE_CALLER = ('functional-tests.yml', 'functional_lanes')
WEEKLY_CALLER = ('scheduled-tests.yml', 'weekly_lanes')
LANES_USES = './.github/workflows/functional-lanes.yml'

# Every other topology the cluster suite is run against, and what the guard
# decides for each. ``slim-primary``'s primary is a database node and not a
# hypervisor, so the guard reads "no provider expected" from the cluster;
# ``localhost`` is one node holding every role, which the guard carves out with
# sizing.is_single_machine() before it looks at any role at all. A topology in
# neither this set nor PROVIDER_TOPOLOGY is the defect these tests exist to
# catch.
TOPOLOGIES_WITHOUT_A_PROVIDER = {'slim-primary', 'localhost'}

# What the primary is sized at in each, from shakenfist/actions'
# ansible/ci-topology-slim-tier.yml and ansible/ci-topology-slim-primary.yml.
# Quoted in the failure below, because "do not run a container there" is an
# argument about a number and a message which does not carry it is an argument
# the reader has to go and reconstruct.
PROVIDER_PRIMARY_RAM_MB = 12288
MERGE_PRIMARY_RAM_MB = 4096


def _load_workflows():
    workflows = {}
    for path in sorted(glob.glob(os.path.join(WORKFLOWS, '*.y*ml'))):
        with open(path) as f:
            workflows[os.path.basename(path)] = yaml.safe_load(f)
    return workflows


def _lanes_a_caller_selects(workflows, caller):
    """The lane names one call of the lanes workflow passes."""
    name, job_id = caller
    job = workflows[name]['jobs'][job_id]
    return json.loads(job['with']['lanes'])


def _module_constant(path, name):
    """One module-level literal assignment, read without importing the module.

    ``cluster_ci_tests/test_oidc_login.py`` imports the functional suite and the
    shipped client, neither of which is installed where the unit tests run, so
    it is parsed rather than imported.
    """
    with open(path) as f:
        tree = ast.parse(f.read(), filename=path)

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError('%s assigns no module-level %s' % (path, name))


class ProviderLaneTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.workflows = _load_workflows()
        self.entries = structural._cluster_ci_matrix_entries()

    def test_the_cluster_ci_entries_are_found_at_all(self):
        # Guards every assertion below against passing vacuously if the
        # matrices move or the parse silently returns nothing.
        self.assertNotEqual([], self.entries)

    def test_exactly_one_lane_carries_the_provider(self):
        carrying = [entry for _, entry in self.entries
                    if entry['topology'] == PROVIDER_TOPOLOGY]
        self.assertEqual(
            1, len(carrying),
            'The provider bring-up step is conditioned on the %s topology, so '
            'every lane deploying it starts a container on its primary. %d '
            'lanes do: %s.'
            % (PROVIDER_TOPOLOGY, len(carrying),
               [e.get('concurrency_key') for e in carrying]))
        self.assertEqual(PROVIDER_LANE, carrying[0]['concurrency_key'])

    def test_the_provider_lane_is_weekly_and_gates_no_merge(self):
        weekly = _lanes_a_caller_selects(self.workflows, WEEKLY_CALLER)
        merge = _lanes_a_caller_selects(self.workflows, MERGE_CALLER)

        self.assertIn(
            PROVIDER_LANE, weekly,
            'The %s lane is where human login is covered against a real '
            'identity provider, and it is no longer selected by %s:%s, so '
            'that coverage runs nowhere at all.'
            % (PROVIDER_LANE, WEEKLY_CALLER[0], WEEKLY_CALLER[1]))

        self.assertNotIn(
            PROVIDER_LANE, merge,
            'The %s lane now runs in the merge queue. It is the lane which '
            'stands an identity provider container up on its primary, and it '
            'was chosen because that primary has %d MiB; a merge-gating '
            'cluster lane deploys a primary with %d MiB, shared with sf-api, '
            'the node\'s own services, the suite\'s virtualenv and five test '
            'workers. If that primary has really been resized, change these '
            'figures in the same commit and say so; otherwise this lane '
            'belongs in %s:%s.'
            % (PROVIDER_LANE, PROVIDER_PRIMARY_RAM_MB, MERGE_PRIMARY_RAM_MB,
               WEEKLY_CALLER[0], WEEKLY_CALLER[1]))

    def test_no_merge_gating_cluster_lane_deploys_the_provider_topology(self):
        # The same property read from the other side, because a lane could be
        # added to the merge-gating list under a new name. What must not happen
        # is a merge-gating lane deploying the topology the bring-up step is
        # conditioned on, whatever that lane is called.
        merge = set(_lanes_a_caller_selects(self.workflows, MERGE_CALLER))
        for source, entry in self.entries:
            if entry.get('concurrency_key') not in merge:
                continue
            self.assertNotEqual(
                PROVIDER_TOPOLOGY, entry['topology'],
                '%s lane %s deploys the %s topology and runs in the merge '
                'queue, so the provider bring-up step would run on every '
                'merge.'
                % (source, entry.get('concurrency_key'), PROVIDER_TOPOLOGY))

    def test_every_cluster_ci_topology_is_one_the_guard_answers_for(self):
        known = TOPOLOGIES_WITHOUT_A_PROVIDER | {PROVIDER_TOPOLOGY}
        for source, entry in self.entries:
            with self.subTest(source=source,
                              description=entry.get('description')):
                self.assertIn(
                    entry['topology'], known,
                    '%s runs cluster-ci.conf against the %s topology, which '
                    'test_oidc_login.py\'s guard has nothing to say about. It '
                    'decides whether a provider is expected by asking whether '
                    'the host running the suite is also a hypervisor, so on '
                    'an unconsidered topology it will either fail every test '
                    'in that file or skip them all, and nothing says which it '
                    'should have done. Add the topology here, and to the '
                    'guard if the answer is not already the right one.'
                    % (source, entry['topology']))

    def test_the_discriminator_separates_the_two_cluster_topologies(self):
        """The reading the guard is built on, checked against both topologies.

        The guard finds the host running the suite in the node roster and asks
        whether it is a hypervisor. That works only because the two cluster
        topologies really do differ in the roles their primary carries, which is
        a fact about another repository's deploy and is modelled here.
        """
        _, tier = structural.SLIM_TIER
        _, primary = structural.SLIM_PRIMARY

        def holds_every_role(nodes):
            return [n for n in nodes
                    if n['is_hypervisor'] and n['is_network_node']
                    and n['is_database_node']]

        self.assertEqual(
            1, len(holds_every_role(tier)),
            'The %s topology no longer has a node carrying the hypervisor, '
            'network and database roles together. That node is its primary, '
            'and it being a hypervisor is the whole discriminator.'
            % PROVIDER_TOPOLOGY)

        self.assertEqual(
            [], holds_every_role(primary),
            'A merge-gating cluster topology now has a node carrying every '
            'role, which is the shape the guard reads as "a provider is '
            'expected here".')
        self.assertEqual(
            1, len([n for n in primary
                    if n['is_database_node'] and not n['is_hypervisor']]),
            'The merge-gating cluster topology no longer has exactly one '
            'database node which is not a hypervisor. That node is its '
            'primary, and it not being a hypervisor is what makes these tests '
            'skip there rather than fail.')

    def test_the_single_machine_carve_out_is_still_needed(self):
        # The one node of the single machine deployment holds every role, so
        # without the carve-out the guard would read it as a deployment which
        # should have a provider and fail every test in that file. If nothing
        # runs cluster-ci.conf on one node any more, that is a thing to notice
        # deliberately rather than to leave as dead reasoning.
        single = [entry for _, entry in self.entries
                  if entry['topology'] in structural.SINGLE_MACHINE_TOPOLOGIES]
        self.assertNotEqual([], single)
        self.assertTrue(
            structural.SINGLE_MACHINE_TOPOLOGIES
            <= TOPOLOGIES_WITHOUT_A_PROVIDER,
            'A single machine topology is not in this file\'s set of '
            'topologies without a provider, so the two files disagree about '
            'what runs where.')


class DescriptorAndCaptureTestCase(base.ShakenFistTestCase):
    """The two files the weekly suite reads, held where a pull request runs.

    Both are read only by a suite which runs against a deployed cluster, so a
    disagreement between what the bring-up writes and what the tests read, or a
    capture which no longer has the shape the comparison assumes, would surface
    as a weekly failure reported as an issue rather than as a red pull request.
    """

    def test_the_descriptor_keys_agree_on_both_sides(self):
        self.assertEqual(
            idp_seams.DESCRIPTOR_KEYS,
            set(_module_constant(LOGIN_TEST, 'DESCRIPTOR_KEYS')),
            'The descriptor is the whole interface between a shell script on '
            'the runner and the cluster suite on a cluster node, and neither '
            'can see the other. A key in one and not the other is a KeyError '
            'in a weekly job, or a value the script writes and nothing reads.')

    def test_the_capture_carries_every_endpoint_a_login_flow_needs(self):
        with open(DISCOVERY_CAPTURE) as f:
            captured = json.load(f)

        issuer = captured['issuer']
        for field in _module_constant(LOGIN_TEST, 'LOGIN_ENDPOINTS'):
            self.assertIn(field, captured)
            self.assertTrue(
                captured[field].startswith(issuer + '/'),
                '%s is %r, which does not sit under the issuer URL %r this '
                'document belongs to -- so the shape comparison the cluster '
                'test makes, which strips that prefix, has nothing to '
                'compare.' % (field, captured[field], issuer))

    def test_the_device_endpoint_is_named_twice_in_the_capture(self):
        """Which is why the cluster test reads it by name out of parsed JSON.

        The name appears twice in the document: once at the top level, and
        once inside ``mtls_endpoint_aliases``, which is the mutual-TLS alias
        set no flow here uses. The two values happen to be equal in this
        provider's default configuration, so a count which found the second
        one would look right today and stop being right the moment an operator
        configures a mutual-TLS alias -- and a count is also what reports "two
        device endpoints" to anyone asserting there is one. Reading the parsed
        document's own member cannot be wrong either way.
        """
        with open(DISCOVERY_CAPTURE) as f:
            body = f.read()
        captured = json.loads(body)

        self.assertEqual(
            2, body.count('"device_authorization_endpoint"'),
            'the capture names device_authorization_endpoint %d times, so the '
            'reason the cluster test indexes the parsed document rather than '
            'counting occurrences is no longer demonstrated here'
            % body.count('"device_authorization_endpoint"'))
        self.assertIn('device_authorization_endpoint', captured)
        self.assertIn('device_authorization_endpoint',
                      captured.get('mtls_endpoint_aliases') or {})
