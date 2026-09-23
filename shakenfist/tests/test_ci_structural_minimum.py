# Copyright 2019 Michael Still and contributors
"""The structural minimum a cluster CI topology has to meet, checked here.

``cluster_ci_tests/test_nodes.py``'s
``test_cluster_topology_meets_the_structural_minimum`` asserts, against a
deployed cluster, that the topology is big enough for the rest of the
cluster suite to mean anything
(PLAN-ci-cloud-sizing-phase-05-guardrails.md's D6). Cluster CI only runs
in the merge queue, so nothing in that file is checkable on a
``pull_request`` -- and an assertion which is *itself* wrong is the worst
kind to discover there, because it fails jobs on clusters which are
fine.

The bounds and the arithmetic therefore live in
``shakenfist_ci/sizing.py``, a module deliberately free of suite and
``shakenfist_client`` imports so this file can load it by path, exactly
as ``test_ci_saturation.py`` already does. What is covered here is the
pure function: both real topologies, every bound at its edge and one
unit below it, and the two readings a young cluster publishes which must
not be mistaken for a small one -- a capacity row which has not been
written yet (``cpu_limit: None``), and a hypervisor which has not
published metrics yet (absent from ``per_node`` altogether).

The topology figures asserted below are read from
``shakenfist/actions``'s ``ansible/ci-topology-slim-primary.yml`` and
``ansible/ci-topology-slim-tier.yml``. They are duplicated rather than
derived, for the same reason ``test_ci_saturation.py`` duplicates the
apiclient symbol names: that repository is not importable from here, so
this catches a bound which no longer describes the fleet only when a
human updates it -- which is the intent, because a topology shrinking
below the floor is supposed to require a deliberate edit.
"""

import importlib.util
import os

import yaml

from shakenfist.tests import base


SIZING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci', 'sizing.py')


def _load_sizing():
    spec = importlib.util.spec_from_file_location(
        'shakenfist_ci_sizing_structural_under_test', SIZING_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sizing = _load_sizing()


def _node(uuid, hypervisor=False, network=False, database=False):
    """One entry as ``GET /nodes`` publishes it, cut down to the role flags."""
    return {
        'uuid': uuid,
        'name': uuid,
        'is_hypervisor': hypervisor,
        'is_network_node': network,
        'is_database_node': database,
    }


def _entry(ledger, publish_cpu_limit=True):
    """One ``/admin/resources`` ``per_node`` entry with this vCPU ledger.

    A guarded node publishes the capacity row's ``limit_cpus`` as an int
    and its live ``cpu_hard_max`` as an unfloored float
    (``cpu_schedulable * CPU_OVERCOMMIT_RATIO``); a node with no capacity
    row publishes ``cpu_limit: None`` and the float alone. Both shapes
    are built here from the same figure, because the point of the
    fallback is that they read the same.
    """
    return {
        'cpu_limit': ledger if publish_cpu_limit else None,
        'cpu_hard_max': float(ledger),
    }


def _topology(ledgers, network_node=0, database_nodes=(),
              database_only_nodes=0, publish_cpu_limit=True,
              roster_omits=()):
    """A (per_node, nodes) reading for a cluster with these hypervisors.

    ``ledgers`` is one figure per hypervisor. ``network_node`` is the
    index of the hypervisor carrying the network role, or None for a
    cluster which reports none. ``database_nodes`` names hypervisor
    indexes which are also in the database tier, and
    ``database_only_nodes`` adds nodes which are in it and are not
    hypervisors -- which is what ``slim-primary``'s primary is. Neither
    is asserted on by the function under test; they are modelled so the
    fixtures are the real topologies rather than a sketch of them.
    ``roster_omits`` names hypervisor indexes which are absent from
    ``per_node`` -- a node whose metrics are not fresh yet.
    """
    nodes = []
    per_node = {}
    for index, ledger in enumerate(ledgers):
        uuid = 'hv%d' % index
        nodes.append(_node(
            uuid, hypervisor=True, network=(index == network_node),
            database=(index in database_nodes)))
        if index not in roster_omits:
            per_node[uuid] = _entry(ledger, publish_cpu_limit)
    for index in range(database_only_nodes):
        nodes.append(_node('db%d' % index, database=True))
    return per_node, nodes


def _requirements(violations):
    return [v['requirement'] for v in violations]


# The two topologies which run cluster-ci.conf, as
# shakenfist/actions deploys them today. slim-primary: a primary which
# is a database node and not a hypervisor, sf1 a hypervisor carrying the
# network role (4 vCPU less a 4 thread infra reservation, clamped to one
# schedulable thread, times the overcommit ratio of 3), and sf2 to sf5
# plain hypervisors (4 vCPU less 2, times 3).
SLIM_PRIMARY = _topology([3, 6, 6, 6, 6], network_node=0,
                         database_only_nodes=1)

# slim-tier, after phase 4 doubled it: a primary carrying network,
# database and hypervisor roles and sf1 carrying database and hypervisor
# (6 vCPU less 4, times 3), and sf2 a plain hypervisor (6 vCPU less 2,
# times 3).
SLIM_TIER = _topology([6, 6, 12], network_node=0, database_nodes=(0, 1))


class StructuralMinimumConstantsTestCase(base.ShakenFistTestCase):
    def test_the_bounds_are_the_ones_the_phase_decided(self):
        """Pinned deliberately, because loosening one is a topology decision.

        D6 chose a ledger floor with no tolerance at all: 24 is exactly
        ``slim-tier``'s present total, so any reduction to that topology
        fails immediately and by name. A future, smaller-on-purpose
        cloud is supposed to have to edit this constant in the same
        change which shrinks it, and a test which followed the constant
        wherever it went would let that happen silently.
        """
        self.assertEqual(3, sizing.MINIMUM_NODES)
        self.assertEqual(3, sizing.MINIMUM_HYPERVISORS)
        self.assertEqual(2, sizing.MINIMUM_NON_NETWORK_HYPERVISORS)
        self.assertEqual(24, sizing.MINIMUM_HYPERVISOR_LEDGER)


class RealTopologyTestCase(base.ShakenFistTestCase):
    """Both clouds which run this suite clear every bound."""

    def test_slim_primary_clears_every_bound(self):
        per_node, nodes = SLIM_PRIMARY
        self.assertEqual(6, len(nodes))
        self.assertEqual(27, sizing.hypervisor_ledger(per_node))
        self.assertEqual([], sizing.structural_minimum_violations(
            per_node, nodes))

    def test_slim_tier_clears_every_bound_with_nothing_to_spare(self):
        """And sits exactly on the floor, which is what D6 chose it to do."""
        per_node, nodes = SLIM_TIER
        self.assertEqual(3, len(nodes))
        self.assertEqual(
            sizing.MINIMUM_HYPERVISOR_LEDGER,
            sizing.hypervisor_ledger(per_node))
        self.assertEqual([], sizing.structural_minimum_violations(
            per_node, nodes))

    def test_slim_primary_would_fail_a_database_node_bound(self):
        """Which is why D6 does not assert one.

        ``slim-primary``'s primary is the only member of its database
        tier, so a minimum of two database nodes would fail the
        ``Debian 12 cluster`` and ``Ubuntu 24.04 cluster`` jobs on every
        run. This asserts the asymmetry the decision rests on rather
        than any behaviour of the function.
        """
        _, primary_nodes = SLIM_PRIMARY
        _, tier_nodes = SLIM_TIER
        self.assertEqual(
            1, len([n for n in primary_nodes if n['is_database_node']]))
        self.assertEqual(
            2, len([n for n in tier_nodes if n['is_database_node']]))


class BoundaryTestCase(base.ShakenFistTestCase):
    """Each bound at its edge, and one unit below it.

    The four counts nest -- every hypervisor is a node, and every
    non-network hypervisor is a hypervisor -- so three of them cannot be
    violated in isolation by any cluster which could actually be
    deployed. Where that is so, the case below is the smallest realistic
    cluster which violates the bound under test, and the assertion names
    every requirement it expects to see rather than pretending the
    violation arrives alone.
    """

    def test_a_topology_exactly_on_every_bound_passes(self):
        # Three hypervisors, one of them the network node, 8 of ledger
        # each: every count and the ledger land exactly on their minimum.
        per_node, nodes = _topology([8, 8, 8], network_node=0)
        self.assertEqual(24, sizing.hypervisor_ledger(per_node))
        self.assertEqual([], sizing.structural_minimum_violations(
            per_node, nodes))

    def test_one_node_short_is_reported_as_a_node_shortfall(self):
        # Two nodes, both hypervisors, neither carrying the network
        # role, and between them enough ledger: the node count and the
        # hypervisor count fall together because the second is a subset
        # of the first, and nothing else does.
        per_node, nodes = _topology([12, 12], network_node=None)
        violations = sizing.structural_minimum_violations(per_node, nodes)
        self.assertEqual(['nodes', 'hypervisors'], _requirements(violations))
        self.assertEqual(2, violations[0]['observed'])
        self.assertEqual(3, violations[0]['minimum'])

    def test_one_hypervisor_short_is_reported_on_its_own(self):
        # Three nodes, but one of them is a database node which
        # schedules nothing -- the shape a node count alone would miss.
        per_node, nodes = _topology(
            [12, 12], network_node=None, database_only_nodes=1)
        violations = sizing.structural_minimum_violations(per_node, nodes)
        self.assertEqual(['hypervisors'], _requirements(violations))
        self.assertEqual(2, violations[0]['observed'])

    def test_one_non_network_hypervisor_short_is_reported(self):
        # Three nodes, two hypervisors, one of which is the network
        # node: test_network_lifecycle.py's precondition fails, and so
        # does the hypervisor count, because a cluster cannot be one
        # short of two non-network hypervisors while still having three
        # hypervisors unless it reports two network nodes.
        per_node, nodes = _topology(
            [12, 12], network_node=0, database_only_nodes=1)
        violations = sizing.structural_minimum_violations(per_node, nodes)
        self.assertEqual(
            ['hypervisors', 'non_network_hypervisors'],
            _requirements(violations))
        self.assertEqual(1, violations[1]['observed'])
        self.assertEqual(2, violations[1]['minimum'])

    def test_the_non_network_bound_is_read_from_the_role_flags_alone(self):
        # A shape no deploy produces -- three hypervisors of which two
        # claim the network role -- which is the only way to isolate
        # this bound. It proves the count comes from is_network_node on
        # each node record and not from the hypervisor count minus one.
        per_node, nodes = _topology([8, 8, 8], network_node=0)
        nodes[1]['is_network_node'] = True
        violations = sizing.structural_minimum_violations(per_node, nodes)
        self.assertEqual(
            ['non_network_hypervisors'], _requirements(violations))

    def test_one_unit_of_ledger_short_is_reported_on_its_own(self):
        # Every count met, and 23 of ledger: the failure slim-tier at a
        # total of 12 would have shown and that no node count can.
        per_node, nodes = _topology([6, 6, 11], network_node=0)
        violations = sizing.structural_minimum_violations(per_node, nodes)
        self.assertEqual(['hypervisor_ledger'], _requirements(violations))
        self.assertEqual(23, violations[0]['observed'])
        self.assertEqual(24, violations[0]['minimum'])

    def test_a_violation_says_what_stops_being_proved(self):
        per_node, nodes = _topology([6, 6, 11], network_node=0)
        violations = sizing.structural_minimum_violations(per_node, nodes)
        self.assertIn('hypervisor_ledger: 23', violations[0]['message'])
        self.assertIn('below the minimum of 24', violations[0]['message'])


class YoungClusterTestCase(base.ShakenFistTestCase):
    """Two readings a healthy cluster publishes while it is warming up.

    Neither is evidence of a small cloud, and phase 2 measured the first
    one in every single job-run it looked at, so a function which
    mistook either for a topology fault would fail every cluster job.
    """

    def test_a_cluster_with_no_capacity_rows_yet_still_sums_its_ledger(self):
        # The window phase 2 measured: 9 to 14 samples, 135 to 210
        # seconds, in every one of 204 job-runs, where every node
        # publishes cpu_limit None with capacity_degraded false. The
        # fallback to the live cpu_hard_max is what stops that reading
        # as a zero ledger.
        per_node, nodes = _topology(
            [6, 6, 12], network_node=0, publish_cpu_limit=False)
        for entry in per_node.values():
            self.assertIsNone(entry['cpu_limit'])
        self.assertEqual(24, sizing.hypervisor_ledger(per_node))
        self.assertEqual([], sizing.structural_minimum_violations(
            per_node, nodes))

    def test_a_single_unguarded_node_contributes_its_hard_max(self):
        per_node, nodes = _topology([6, 6, 12], network_node=0)
        per_node['hv2'] = _entry(12, publish_cpu_limit=False)
        self.assertEqual(24, sizing.hypervisor_ledger(per_node))
        self.assertEqual([], sizing.structural_minimum_violations(
            per_node, nodes))

    def test_a_hypervisor_absent_from_the_roster_is_still_a_hypervisor(self):
        # A node which has not published fresh metrics is missing from
        # per_node but present in GET /nodes. Its ledger is genuinely
        # unknown and reads as nothing, which the caller waits out; its
        # role is not in doubt and must not read as a missing
        # hypervisor, or a transient metrics gap would be reported as a
        # topology fault.
        per_node, nodes = _topology(
            [6, 6, 12], network_node=0, roster_omits=(2,))
        violations = sizing.structural_minimum_violations(per_node, nodes)
        self.assertEqual(['hypervisor_ledger'], _requirements(violations))
        self.assertEqual(12, violations[0]['observed'])

    def test_a_lowered_capacity_row_binds_below_the_hard_max(self):
        # cpu_limit is preferred whenever it is published, so a cluster
        # whose rows were reconciled down to less than its live sizing
        # is reported at the smaller figure -- which is what admission
        # would actually allow.
        per_node, nodes = _topology([6, 6, 12], network_node=0)
        per_node['hv2']['cpu_limit'] = 11
        self.assertEqual(23, sizing.hypervisor_ledger(per_node))
        self.assertEqual(
            ['hypervisor_ledger'],
            _requirements(sizing.structural_minimum_violations(
                per_node, nodes)))


class EmptyReadingTestCase(base.ShakenFistTestCase):
    def test_a_cluster_which_reports_nothing_violates_everything(self):
        violations = sizing.structural_minimum_violations({}, [])
        self.assertEqual(
            ['nodes', 'hypervisors', 'non_network_hypervisors',
             'hypervisor_ledger'],
            _requirements(violations))

    def test_missing_readings_are_treated_as_empty_rather_than_raising(self):
        # /admin/resources can answer without a per_node key at all, and
        # a client error can leave the node list as None. Neither is a
        # reason to raise out of an assertion whose whole job is to
        # report what it saw.
        self.assertEqual(
            4, len(sizing.structural_minimum_violations(None, None)))


class SingleMachineTestCase(base.ShakenFistTestCase):
    """The one reading the assertion declines to judge.

    ``cluster-ci.conf`` is not run only against cluster topologies --
    ``scheduled-tests.yml`` runs it against ``localhost`` on purpose --
    and every minimum here is unmeetable on one node. The carve-out is
    the whole single-machine signature rather than a node count, so a
    cluster which has lost every node but one still fails.
    """

    def test_one_node_holding_every_role_is_a_single_machine(self):
        self.assertTrue(sizing.is_single_machine(
            [_node('sf1', hypervisor=True, network=True, database=True)]))

    def test_a_cluster_is_not_a_single_machine(self):
        per_node, nodes = _topology([6, 6, 12], network_node=0,
                                    database_nodes=(1, 2))
        self.assertFalse(sizing.is_single_machine(nodes))

    def test_one_node_which_is_not_the_whole_shape_is_not_excused(self):
        # A one-node reading that is not the single-machine shape is
        # something nobody deployed on purpose, so it is reported rather
        # than excused. (This is belt and braces: the roster is what
        # actually carries the argument, since it does not shrink when a
        # node goes quiet. slim-tier's primary does hold all three roles.)
        for node in (_node('sf2', hypervisor=True),
                     _node('sf1', hypervisor=True, network=True),
                     _node('primary', database=True),
                     _node('sf3', hypervisor=True, database=True)):
            with self.subTest(node=node['uuid']):
                self.assertFalse(sizing.is_single_machine([node]))

    def test_an_empty_or_absent_roster_is_not_a_single_machine(self):
        # An unreadable cluster must not be excused as a single machine.
        self.assertFalse(sizing.is_single_machine([]))
        self.assertFalse(sizing.is_single_machine(None))

    def test_a_single_machine_would_otherwise_violate_everything(self):
        # The carve-out is load-bearing: without it this reading fails the
        # job by name on every bound.
        nodes = [_node('sf1', hypervisor=True, network=True, database=True)]
        per_node = {'sf1': _entry(3)}
        self.assertEqual(
            ['nodes', 'hypervisors', 'non_network_hypervisors',
             'hypervisor_ledger'],
            _requirements(
                sizing.structural_minimum_violations(per_node, nodes)))


class LedgerDiagnosticsTestCase(base.ShakenFistTestCase):
    """A short ledger has two causes and the message has to separate them.

    ``per_node`` is a liveness statement, so a hypervisor whose metrics
    have gone stale is simply absent from it and its ledger with it. The
    total alone reads identically to a topology which really did shrink,
    and the two send an operator to different places.
    """

    def _ledger_message(self, violations):
        for violation in violations:
            if violation['requirement'] == 'hypervisor_ledger':
                return violation['message']
        self.fail('no hypervisor_ledger violation was reported')

    def test_a_missing_hypervisor_is_named_in_the_message(self):
        per_node, nodes = _topology([6, 6, 12], network_node=0,
                                    database_nodes=(1, 2),
                                    roster_omits=(2,))
        message = self._ledger_message(
            sizing.structural_minimum_violations(per_node, nodes))
        self.assertIn('hypervisor_ledger: 12', message)
        self.assertIn('summed over 2 of 3 rostered hypervisors', message)
        self.assertIn('node missing rather than a topology shrinking',
                      message)

    def test_a_complete_roster_says_nothing_about_missing_nodes(self):
        # A topology which really is too small must not be reported as a
        # possible metrics gap, or the message stops meaning anything.
        per_node, nodes = _topology([3, 3, 6], network_node=0,
                                    database_nodes=(1, 2))
        message = self._ledger_message(
            sizing.structural_minimum_violations(per_node, nodes))
        self.assertIn('hypervisor_ledger: 12', message)
        self.assertNotIn('rostered hypervisors', message)

    def test_the_ledger_total_is_unchanged_by_the_extra_wording(self):
        # The message gained a clause; the observed figure and the verdict
        # are the same reading they were.
        per_node, nodes = _topology([6, 6, 12], network_node=0,
                                    database_nodes=(1, 2),
                                    roster_omits=(2,))
        violations = sizing.structural_minimum_violations(per_node, nodes)
        ledger = [v for v in violations
                  if v['requirement'] == 'hypervisor_ledger'][0]
        self.assertEqual(12, ledger['observed'])
        self.assertEqual(sizing.MINIMUM_HYPERVISOR_LEDGER, ledger['minimum'])


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# Every topology which deploys a cluster the structural minimum applies
# to, and every topology which deploys something that is not a cluster at
# all and is carved out by sizing.is_single_machine(). A topology running
# cluster-ci.conf and named in neither is the defect this pair of tests
# exists to catch: the assertion fails the job, by name, after waiting
# seven minutes, on a deployment it was never written about.
CLUSTER_TOPOLOGIES = {'slim-primary', 'slim-tier'}
SINGLE_MACHINE_TOPOLOGIES = {'localhost'}


def _cluster_ci_matrix_entries():
    """Every workflow matrix entry which runs the cluster test config.

    Found by shape rather than by path -- any mapping anywhere in a
    workflow which names both a topology and a stestr config -- because
    the matrices are nested differently in each workflow and a new one
    should be picked up without being registered here.
    """
    workflows = os.path.join(REPO_ROOT, '.github', 'workflows')
    entries = []

    def walk(node, source):
        if isinstance(node, dict):
            if 'topology' in node and 'stestr_config' in node:
                entries.append((source, node))
            for value in node.values():
                walk(value, source)
        elif isinstance(node, list):
            for value in node:
                walk(value, source)

    for name in sorted(os.listdir(workflows)):
        if not name.endswith(('.yml', '.yaml')):
            continue
        with open(os.path.join(workflows, name)) as f:
            walk(yaml.safe_load(f), name)

    return [(source, entry) for source, entry in entries
            if entry.get('stestr_config') == 'cluster-ci.conf']


class WorkflowTopologyTestCase(base.ShakenFistTestCase):
    """What the deployed assertion will actually meet.

    ``test_cluster_topology_meets_the_structural_minimum`` fails rather
    than skips, so what it is run against is part of its correctness and
    not a detail of the workflows. This is the check which would have
    caught it being run against ``localhost``.
    """

    def test_the_entries_are_found_at_all(self):
        # Guards every assertion below against passing vacuously if the
        # matrices move or the parse silently returns nothing.
        self.assertNotEqual([], _cluster_ci_matrix_entries())

    def test_every_cluster_ci_topology_is_one_this_assertion_handles(self):
        known = CLUSTER_TOPOLOGIES | SINGLE_MACHINE_TOPOLOGIES
        for source, entry in _cluster_ci_matrix_entries():
            with self.subTest(source=source,
                              description=entry.get('description')):
                self.assertIn(
                    entry['topology'], known,
                    '%s runs cluster-ci.conf against the %s topology, which '
                    'test_cluster_topology_meets_the_structural_minimum '
                    'neither applies to nor carves out. It will wait out '
                    'STRUCTURAL_MINIMUM_WAIT and then fail that job by '
                    'name. Add the topology to CLUSTER_TOPOLOGIES if it '
                    'meets the minimums, or to SINGLE_MACHINE_TOPOLOGIES '
                    'and sizing.is_single_machine() if it is not a cluster.'
                    % (source, entry['topology']))

    def test_the_single_machine_carve_out_is_still_load_bearing(self):
        # If this fails, nothing runs cluster-ci.conf on a single machine
        # any more and sizing.is_single_machine() has become dead code --
        # which is a thing to notice deliberately, not to discover later.
        single_machine = [
            entry for _, entry in _cluster_ci_matrix_entries()
            if entry['topology'] in SINGLE_MACHINE_TOPOLOGIES]
        self.assertNotEqual([], single_machine)
