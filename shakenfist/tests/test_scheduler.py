import random
import time
from unittest import mock

from shakenfist import exceptions
from shakenfist import instance
from shakenfist import scheduler
from shakenfist.config import SFConfig
from shakenfist.constants import DISK_BUSY_PER_SECOND_METRIC
from shakenfist.constants import GiB
from shakenfist.node import nodes_by_free_disk_descending
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


fake_config = SFConfig(
    NODE_NAME='node01',
    SCHEDULER_CACHE_TIMEOUT=30,
    CPU_OVERCOMMIT_RATIO=16.0,
    RAM_OVERCOMMIT_RATIO=1.5,
    NODE_RAM_RESERVATION_GB=5.0,
    NODE_CPU_RESERVATION_THREADS=2,
    NETWORK_NODE_IP='10.0.0.1',
)


class SchedulerTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.recorded_op = mock.patch(
            'shakenfist.util.general.RecordedOperation')
        self.recorded_op.start()
        self.addCleanup(self.recorded_op.stop)

        self.mock_config = mock.patch(
            'shakenfist.scheduler.config', fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

    def _node_uuid(self, fqdn):
        """Helper to get node UUID from FQDN."""
        return self.mock_mariadb.node_uuids[fqdn]

    def _node_uuids_set(self, *fqdns):
        """Helper to get a set of node UUIDs from FQDNs."""
        return {self.mock_mariadb.node_uuids[f] for f in fqdns}

    def _all_hypervisor_uuids(self):
        """Helper to get UUIDs of all hypervisor nodes (excludes node1_net)."""
        return {self.mock_mariadb.node_uuids[n]
                for n in self.mock_mariadb.node_names
                if n != 'node1_net'}


class LowResourceTestCase(SchedulerTestCase):
    """Test low resource exceptions."""

    def test_no_metrics(self):
        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage is_hypervisor', str(exc))

    def test_requested_too_many_cpu(self):
        self.mock_mariadb.set_node_metrics_same({
            'cpu_max_per_instance': 5,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_mariadb.create_instance('fake-inst', cpus=6)
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage cpu_max_per_instance',
            str(exc))

    def test_not_enough_cpu(self):
        self.mock_mariadb.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'cpu_total_instance_vcpus': 4*16,
            'memory_available': 5*1024+1024-1,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_available': 4
        })

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_cpu',
            str(exc))

    def test_not_enough_ram_for_system(self):
        self.mock_mariadb.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 5*1024+1024-1,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_memory',
            str(exc))

    def test_not_enough_ram_on_node(self):
        self.mock_mariadb.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 10000,
            'memory_max': 10000,
            'memory_total_instance_actual': 15001,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_memory',
            str(exc))

    def test_not_enough_disk(self):
        # No disk_reservation_gb is published, so admission falls back to the
        # config default NODE_DISK_RESERVATION_GB (20): 20 GiB free - 20 = 0 GB
        # of headroom, and a 21 GB request is rejected. This is numerically
        # identical to the retired MINIMUM_FREE_DISK behaviour.
        self.mock_mariadb.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 20*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_mariadb.create_instance(
            'fake-inst', disk_spec=[{
                    'base': 'cirros',
                            'size': 21
                }])

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_free_disk',
            str(exc))

    def test_not_enough_disk_bandwidth(self):
        # The resources daemon serialises this metric as a float string, so
        # the value here deliberately isn't int() parseable.
        self.mock_mariadb.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 200*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12,
            DISK_BUSY_PER_SECOND_METRIC: '2000.5'
        })

        fake_inst = self.mock_mariadb.create_instance(
            'fake-inst', disk_spec=[{
                'base': 'cirros',
                'size': 21
            }])

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_disk',
            str(exc))

    def test_disk_bandwidth_below_threshold(self):
        # The resources daemon publishes this metric as a float (delta divided
        # by sample spacing), which may round-trip as a string like '16.6'.
        # int() would raise ValueError here, so ensure we parse it as a float.
        self.mock_mariadb.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 200*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12,
            DISK_BUSY_PER_SECOND_METRIC: '16.6'
        })

        fake_inst = self.mock_mariadb.create_instance(
            'fake-inst', disk_spec=[{
                'base': 'cirros',
                'size': 21
            }])

        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))

    def test_ok(self):
        self.mock_mariadb.set_node_metrics_same()

        fake_inst = self.mock_mariadb.create_instance('fake-inst')

        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))


class CorrectAllocationTestCase(SchedulerTestCase):
    """Test correct node allocation."""

    def test_any_node_but_not_network_node(self):
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3')
        self.mock_mariadb.set_node_metrics_same()

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))


class ForcedCandidatesTestCase(SchedulerTestCase):
    """Test when we force candidates."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb.set_node_metrics_same()

    def test_only_two(self):
        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        candidates = [self._node_uuid('node1_net'),
                      self._node_uuid('node2')]
        nodes = scheduler.Scheduler().find_candidates(
            fake_inst, candidates=candidates)
        self.assertSetEqual(
            self._node_uuids_set('node2'), set(nodes))

    def test_no_such_node(self):
        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        self.assertRaises(
            exceptions.CandidateNodeNotFoundException,
            scheduler.Scheduler().find_candidates,
            fake_inst, candidates=['barry'])

    def test_empty_forced_list_is_nowhere_not_everywhere(self):
        # The preflight redirect builds "every node except this one",
        # and on a single-node metrics snapshot that set is empty. An
        # empty forced list must mean there is nowhere else to go, not
        # fall open to the whole cluster -- which re-offers the one
        # node the caller built the list to exclude (issue 4001).
        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        exc = self.assertRaises(
            exceptions.LowResourceException,
            scheduler.Scheduler().find_candidates,
            fake_inst, candidates=[])
        self.assertEqual(
            'No nodes remaining at scheduling stage pre_schedule', str(exc))

    def test_empty_forced_list_is_published_as_forced(self):
        # The schedule events are the primary diagnostic surface for
        # placement decisions: a redirect with nowhere to go must not
        # publish itself as a fresh whole-cluster decision (issue 4001).
        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        with mock.patch('shakenfist.scheduler.add_event_multi') as aem:
            self.assertRaises(
                exceptions.LowResourceException,
                scheduler.Scheduler().find_candidates,
                fake_inst, candidates=[])

        inputs = [c for c in aem.call_args_list
                  if c.args[2] == 'schedule inputs']
        self.assertEqual(1, len(inputs))
        self.assertTrue(inputs[0].kwargs['extra']['forced_candidates'])

        initial = [c for c in aem.call_args_list
                   if c.args[2] == 'schedule initial candidates']
        self.assertEqual(1, len(initial))
        self.assertEqual([], initial[0].kwargs['extra']['candidates'])

    def test_unforced_call_is_published_as_unforced(self):
        # The other side of the discriminator: passing no candidates at
        # all must still read as an open, whole-cluster decision.
        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        with mock.patch('shakenfist.scheduler.add_event_multi') as aem:
            scheduler.Scheduler().find_candidates(fake_inst)

        inputs = [c for c in aem.call_args_list
                  if c.args[2] == 'schedule inputs']
        self.assertEqual(1, len(inputs))
        self.assertFalse(inputs[0].kwargs['extra']['forced_candidates'])


class MetricsRefreshTestCase(SchedulerTestCase):
    """Test that we refresh metrics."""

    def test_refresh(self):
        self.mock_mariadb.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_mariadb.create_instance('fake-inst')

        net_uuid = self._node_uuid('node1_net')
        s = scheduler.Scheduler()
        s.find_candidates(fake_inst)
        self.assertEqual(22000, s.metrics[net_uuid]['memory_available'])

        self.mock_mariadb.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 11000,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })
        s.metrics_updated = time.time() - 400
        s.find_candidates(fake_inst)
        self.assertEqual(11000, s.metrics[net_uuid]['memory_available'])


class LoadAwareOrderingTestCase(SchedulerTestCase):
    """Test load-per-thread bucketing and headroom-weighted selection.

    The bucketing orders the candidate list; it does not shorten it.
    Every node here has passed every admission filter, so a busier one
    goes to the tail rather than being discarded -- the caller walks
    the list against a capacity guard which can refuse the head of it.
    """

    def test_loaded_small_node_loses_to_idle_big_nodes(self):
        # The sfcbr 2026-07-17 incident shape: a small node already under
        # heavy load must not share the winning bucket with idle large
        # nodes, even though every raw load here rounds to a floor() value
        # that the old bucketing would have needed >= 1.0 differences to
        # separate.
        self.mock_mariadb.set_node_metrics_same()
        self.mock_mariadb.update_node_metrics('node2', {
            'cpu_max': 12, 'cpu_schedulable': 8, 'cpu_load_1': 9.0})
        for n in ['node3', 'node4']:
            self.mock_mariadb.update_node_metrics(n, {
                'cpu_max': 24, 'cpu_schedulable': 22, 'cpu_load_1': 1.0})

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        for seed in range(20):
            random.seed(seed)
            nodes = scheduler.Scheduler().find_candidates(fake_inst)
            self.assertSetEqual(
                self._node_uuids_set('node3', 'node4'), set(nodes[:2]))
            self.assertEqual(self._node_uuid('node2'), nodes[-1])

    def test_a_busier_node_is_ordered_last_not_dropped(self):
        # The phase 3 regression which cost merge CI five creates on
        # 2026-08-14: the busiest node won the bucket ordering, the
        # capacity guard refused it, and because the ordering had
        # discarded the other two viable nodes the walk had nothing left
        # to try and the create 507ed. Ordering must leave them in the
        # list, behind the preferred node, for the walk to fall through
        # to.
        self.mock_mariadb.set_node_metrics_same()
        for n, load in [('node2', 8.0), ('node3', 0.2), ('node4', 4.0)]:
            self.mock_mariadb.update_node_metrics(n, {
                'cpu_max': 12, 'cpu_schedulable': 8, 'cpu_load_1': load})

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        random.seed(1)
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertEqual(
            [self._node_uuid(n) for n in ['node3', 'node4', 'node2']], nodes)

    def test_similar_nodes_share_bucket_and_spread(self):
        # Nodes with similar normalised load land in the same coarse
        # bucket, and every one of them leads the candidate list for some
        # seed -- a burst scheduled against one stale snapshot still
        # spreads rather than stacking on a single "best" node.
        self.mock_mariadb.set_node_metrics_same()
        for n, load in [('node2', 0.3), ('node3', 0.5), ('node4', 0.1)]:
            self.mock_mariadb.update_node_metrics(n, {
                'cpu_max': 24, 'cpu_schedulable': 22, 'cpu_load_1': load})

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        firsts = set()
        for seed in range(50):
            random.seed(seed)
            nodes = scheduler.Scheduler().find_candidates(fake_inst)
            self.assertSetEqual(
                self._node_uuids_set('node2', 'node3', 'node4'), set(nodes))
            firsts.add(nodes[0])
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3', 'node4'), firsts)

    def test_selection_is_weighted_by_headroom(self):
        # Two nodes in the same bucket but with roughly 2x different load
        # headroom: the bigger node should lead the list about twice as
        # often. Fixed seed makes the draw deterministic; the band is
        # deliberately loose.
        self.mock_mariadb.set_node_metrics_same()
        # weight = 0.75 * 12 - 2.9 = 6.1, normalised load 0.24 (bucket 0)
        self.mock_mariadb.update_node_metrics('node2', {
            'cpu_max': 12, 'cpu_schedulable': 12, 'cpu_load_1': 2.9})
        # weight = 0.75 * 22 - 4.4 = 12.1, normalised load 0.2 (bucket 0)
        self.mock_mariadb.update_node_metrics('node3', {
            'cpu_max': 24, 'cpu_schedulable': 22, 'cpu_load_1': 4.4})
        # Push node4 out of the winning bucket entirely.
        self.mock_mariadb.update_node_metrics('node4', {
            'cpu_max': 12, 'cpu_schedulable': 12, 'cpu_load_1': 11.0})

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        random.seed(42)
        s = scheduler.Scheduler()
        wins = {self._node_uuid('node2'): 0, self._node_uuid('node3'): 0}
        for _ in range(1000):
            # These draws can take minutes on a loaded CI worker; keep
            # the mock's metrics rows fresh so the scheduler's periodic
            # refresh does not discard them as stale mid-test.
            for row in self.mock_mariadb.node_metrics_store.values():
                row['timestamp'] = time.time()
            nodes = s.find_candidates(fake_inst)
            self.assertSetEqual(
                self._node_uuids_set('node2', 'node3'), set(nodes[:2]))
            self.assertEqual(self._node_uuid('node4'), nodes[-1])
            wins[nodes[0]] += 1

        ratio = (wins[self._node_uuid('node3')] /
                 wins[self._node_uuid('node2')])
        self.assertTrue(
            1.5 <= ratio <= 3.0,
            f'Expected node3 to win 1.5-3x as often as node2, got {ratio} '
            f'({wins})')

    def test_missing_cpu_schedulable_falls_back_to_synthetic(self):
        # Metrics rows written by an older resources daemon lack the
        # reservation-aware cpu_schedulable field; those nodes get a
        # synthetic reservation by subtracting the configured per-node
        # thread reservation (2 threads here), per-node, without error.
        self.mock_mariadb.set_node_metrics_same()
        self.mock_mariadb.update_node_metrics('node2', {
            'cpu_max': 12, 'cpu_load_1': 4.0})
        self.mock_mariadb.update_node_metrics('node3', {
            'cpu_max': 12, 'cpu_load_1': 0.5})
        self.mock_mariadb.update_node_metrics('node4', {
            'cpu_max': 12, 'cpu_load_1': 0.4})

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        random.seed(1)
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        # node2 has normalised load 4.0 / 10 = 0.40 (bucket 1); the
        # others are in bucket 0 and so lead the ordering.
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes[:2]))
        self.assertEqual(self._node_uuid('node2'), nodes[-1])

    def test_old_dialect_fallback_ignores_infra_role(self):
        # The synthetic fallback no longer applies a role-aware bump: it
        # subtracts the flat per-node thread reservation regardless of the
        # role flags an old-dialect row carries. So an old-dialect
        # infra-role node and an old-dialect plain node with identical
        # cpu_max size identically (max(1, 12 - 2) = 10 schedulable
        # threads) and share a bucket -- the infra node is neither
        # favoured nor penalised. (Under the retired role-aware fallback
        # the infra node would have sized to 8 threads, normalised load
        # 2.0 / 8 = 0.25 -> bucket 1, splitting it from the plain node's
        # 2.0 / 10 = 0.20 -> bucket 0.)
        self.mock_mariadb.set_node_metrics_same()
        self.mock_mariadb.update_node_metrics('node2', {
            'cpu_max': 12, 'cpu_load_1': 2.0, 'is_database_node': True})
        self.mock_mariadb.update_node_metrics('node3', {
            'cpu_max': 12, 'cpu_load_1': 2.0})
        self.mock_mariadb.update_node_metrics('node4', {
            'cpu_max': 12, 'cpu_load_1': 12.0})

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        random.seed(1)
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3'), set(nodes[:2]))
        self.assertEqual(self._node_uuid('node4'), nodes[-1])


class ReservedCapacityAdmissionTestCase(SchedulerTestCase):
    """Test admission against reserved, schedulable capacity."""

    def _baseline(self, **overrides):
        # A metrics baseline that passes every admission stage, built on
        # the same shapes the LowResourceTestCase tests use. The fake
        # config pins CPU_OVERCOMMIT_RATIO=16.0 and
        # NODE_RAM_RESERVATION_GB=5.0.
        metrics = {
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12,
        }
        metrics.update(overrides)
        return metrics

    def test_admission_flips_at_schedulable_boundary(self):
        # cpu_schedulable=2 and a ratio of 16 cap a node at 32 vCPUs,
        # even though cpu_max=4 would historically have allowed 64.
        self.mock_mariadb.set_node_metrics_same(self._baseline(
            cpu_schedulable=2, cpu_total_instance_vcpus=30))

        fits = self.mock_mariadb.create_instance('fits', cpus=2)
        nodes = scheduler.Scheduler().find_candidates(fits)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))

        does_not_fit = self.mock_mariadb.create_instance('does-not-fit', cpus=3)
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                does_not_fit)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_cpu',
            str(exc))

    def test_infra_role_node_admits_less(self):
        # Two identically sized machines, but one has an extra core
        # reserved for cluster-wide daemons (smaller cpu_schedulable). A
        # request that fits the plain nodes must not land on the infra
        # node.
        self.mock_mariadb.set_node_metrics_same(self._baseline(
            cpu_max=12, cpu_schedulable=10, cpu_total_instance_vcpus=120))
        self.mock_mariadb.update_node_metrics('node2', {'cpu_schedulable': 8})

        fake_inst = self.mock_mariadb.create_instance('fake-inst', cpus=10)
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_ram_check_honours_published_reservation(self):
        # memory_reserved_mb=6144 leaves exactly 1024 MB for the default
        # 1024 MB instance; one fewer MB available must reject.
        self.mock_mariadb.set_node_metrics_same(self._baseline(
            memory_available=6144+1024, memory_reserved_mb=6144))
        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))

        self.mock_mariadb.set_node_metrics_same(self._baseline(
            memory_available=6144+1024-1, memory_reserved_mb=6144))
        fake_inst2 = self.mock_mariadb.create_instance('fake-inst2')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst2)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_memory',
            str(exc))

    def test_ram_check_falls_back_to_config_reservation(self):
        # Without a published memory_reserved_mb the check falls back to
        # NODE_RAM_RESERVATION_GB (5 GB in the fake config).
        self.mock_mariadb.set_node_metrics_same(self._baseline(
            memory_available=5*1024+1024))
        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))

        self.mock_mariadb.set_node_metrics_same(self._baseline(
            memory_available=5*1024+1024-1))
        fake_inst2 = self.mock_mariadb.create_instance('fake-inst2')
        self.assertRaises(exceptions.LowResourceException,
                          scheduler.Scheduler().find_candidates,
                          fake_inst2)

    def test_missing_memory_max_rejects_node(self):
        # A metrics row without memory_max must reject the node with a
        # reason, not raise ZeroDivisionError out of find_candidates.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        node2_metrics = self.mock_mariadb.node_metrics_store[
            self._node_uuid('node2')]['metrics']
        del node2_metrics['memory_max']

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_summarize_totals_exclude_overpacked_nodes(self):
        # A node packed beyond the admission cap reports negative
        # per-node headroom (honestly), but the cluster totals must only
        # sum the genuine headroom of the other nodes.
        self.mock_mariadb.set_node_metrics_same(self._baseline(
            cpu_max=12, cpu_schedulable=10, cpu_total_instance_vcpus=0))
        self.mock_mariadb.update_node_metrics('node2', {
            'cpu_total_instance_vcpus': 500,
            'memory_total_instance_actual': 1000000})

        s = scheduler.Scheduler()
        resources = s.summarize_resources()

        node2 = resources['per_node'][self._node_uuid('node2')]
        self.assertLess(node2['cpu_available'], 0)
        self.assertLess(node2['ram_available'], 0)

        expected_cpu = sum(
            max(0, per_node['cpu_available'])
            for per_node in resources['per_node'].values())
        expected_ram = sum(
            max(0, per_node['ram_available'])
            for per_node in resources['per_node'].values())
        self.assertEqual(expected_cpu, resources['total']['cpu_available'])
        self.assertEqual(expected_ram, resources['total']['ram_available'])
        self.assertGreater(resources['total']['cpu_available'], 0)

    def test_summarize_resources_matches_admission(self):
        # The admin resources API must report numbers computed with the
        # same arithmetic the admission checks use, for both new-dialect
        # and old-dialect (fallback) metrics rows.
        self.mock_mariadb.set_node_metrics_same(self._baseline(
            cpu_max=12, cpu_schedulable=10, cpu_total_instance_vcpus=7,
            memory_reserved_mb=6144))
        self.mock_mariadb.update_node_metrics('node3', {
            'cpu_max': 24, 'cpu_schedulable': 22, 'memory_reserved_mb': 8192})
        # node4 is an old-dialect row: fallback arithmetic applies.
        node4_metrics = self.mock_mariadb.node_metrics_store[
            self._node_uuid('node4')]['metrics']
        del node4_metrics['cpu_schedulable']
        del node4_metrics['memory_reserved_mb']

        s = scheduler.Scheduler()
        resources = s.summarize_resources()

        for n, per_node in resources['per_node'].items():
            metrics = s.metrics[n]
            # The expected base must mirror _schedulable_threads(): the
            # published value when present, otherwise the synthetic
            # reservation subtracting the flat per-node thread reservation
            # (NODE_CPU_RESERVATION_THREADS=2), with no infra-role bump.
            expected_base = metrics.get('cpu_schedulable')
            if not expected_base:
                expected_base = max(1, metrics.get('cpu_max', 0) - 2)
            expected_reserved = metrics.get(
                'memory_reserved_mb', int(5.0 * 1024))

            self.assertEqual(expected_base, per_node['cpu_schedulable'])
            self.assertEqual(expected_reserved, per_node['memory_reserved_mb'])
            self.assertEqual(
                expected_base * 16.0 -
                metrics.get('cpu_total_instance_vcpus', 0),
                per_node['cpu_available'])
            self.assertEqual(
                metrics.get('memory_available', 0) - expected_reserved,
                per_node['ram_max_per_instance'])


class CapacityCounterTestCase(SchedulerTestCase):
    """The pre-filter reads the counters the guard will draw down (P2).

    Admission is the guarded UPDATE against ``scheduler_node_capacity``
    which ``place_instance()`` makes. ``find_candidates()`` is a cheap
    pre-filter standing in front of it, and it charges a node
    ``max(measured, committed)`` -- the issue-3498 arithmetic, but read
    from the counters in one query rather than rebuilt in Python from
    every candidate's placement rows. Reading only the measurement is
    what let a node whose ledger was full stay in the list until the
    guard refused it.
    """

    def _baseline(self, **overrides):
        # cpu_schedulable=1 and the fake config's ratio of 16 cap each
        # node at 16 vCPUs, and the measurement claims none are in use.
        metrics = {
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'cpu_schedulable': 1,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 0,
            'cpu_available': 12,
        }
        metrics.update(overrides)
        return metrics

    def test_prefilter_drops_a_node_whose_ledger_is_full(self):
        # node2 has been placed with 16 vCPUs which have not booted yet,
        # so its measurement still reads zero -- the whole time an
        # instance spends fetching its image it is invisible to
        # cpu_total_instance_vcpus. The placement did draw those vCPUs
        # down from node2's counters, and the pre-filter reads those, so
        # node2 leaves the candidate list here rather than surviving to
        # be refused by the guard.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=16, limit_memory_mb=100000,
            used_cpus=16)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_prefilter_keeps_a_node_with_no_capacity_row(self):
        # A node the reconciler has not sized (P7) is guarded by
        # nothing: admission will let it through unguarded, so the
        # pre-filter must not refuse it on a ledger which does not
        # exist. Only the measurement applies there.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.create_instance(
            'placed-1', cpus=16, place_on_node='node2')

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3', 'node4'), set(nodes))

    def test_prefilter_does_not_charge_an_instance_for_itself(self):
        # A reschedule (the start redirect, or preflight) runs against
        # an instance which is already placed, and its vCPUs are already
        # in that node's used_cpus. Charging them a second time would
        # drop the node the instance is on from its own candidate list.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        fake_inst = self.mock_mariadb.create_instance(
            'fake-inst', cpus=4, place_on_node='node2')
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=16, limit_memory_mb=100000,
            used_cpus=16)

        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertIn(self._node_uuid('node2'), nodes)

    def test_prefilter_reads_the_counters_once(self):
        # One query for the whole decision, not one per candidate: the
        # per-placed-instance reads the issue-3498 stopgap made are the
        # cost the counters exist to remove.
        self.mock_mariadb.set_node_metrics_same(self._baseline())

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        with mock.patch(
                'shakenfist.mariadb.get_scheduler_node_capacity',
                side_effect=(
                    self.mock_mariadb._mariadb_get_scheduler_node_capacity
                )) as gsnc:
            nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertEqual(1, gsnc.call_count)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3', 'node4'), set(nodes))

    def test_prefilter_reads_the_placement_attribute_once(self):
        # The self-charge check calls placement_filter(), which reads
        # inst.placement -- an instance_attributes fetch -- and it runs
        # for every candidate carrying a non-zero used_cpus. The value is
        # identical for all of them, so the decision memoises the row
        # rather than re-fetching it per candidate.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        for node in ('node2', 'node3', 'node4'):
            self.mock_mariadb.set_node_capacity(
                self._node_uuid(node), limit_cpus=16,
                limit_memory_mb=100000, used_cpus=1)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        with mock.patch(
                'shakenfist.mariadb.get_instance_attributes',
                side_effect=(
                    self.mock_mariadb._mariadb_get_instance_attributes
                )) as gia:
            nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertLessEqual(gia.call_count, 1)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3', 'node4'), set(nodes))

    def test_measurement_still_excludes_a_busy_node(self):
        # What the pre-filter does see it still acts on: a node whose
        # running domains already fill its hard maximum is dropped
        # before the guard is ever asked.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.update_node_metrics(
            'node2', {'cpu_total_instance_vcpus': 16})

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_summarize_resources_publishes_the_counters(self):
        # The admin resources API must not advertise headroom that
        # admission will refuse to use, and admission is the counters.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=16, limit_memory_mb=100000,
            used_cpus=5)

        resources = scheduler.Scheduler().summarize_resources()
        node2 = resources['per_node'][self._node_uuid('node2')]
        self.assertEqual(5, node2['cpu_committed'])
        self.assertTrue(node2['cpu_committed_row_present'])
        self.assertEqual(0, node2['cpu_measured'])
        self.assertEqual(16, node2['cpu_limit'])
        self.assertEqual(16.0 - 5, node2['cpu_available'])

    def test_summarize_resources_reports_a_node_with_no_row(self):
        # A node the reconciler has not sized yet (P7) is guarded by
        # nothing, so it is charged nothing -- but the response says
        # which kind of zero that is.
        self.mock_mariadb.set_node_metrics_same(self._baseline())

        resources = scheduler.Scheduler().summarize_resources()
        node3 = resources['per_node'][self._node_uuid('node3')]
        self.assertEqual(0, node3['cpu_committed'])
        self.assertFalse(node3['cpu_committed_row_present'])
        self.assertIsNone(node3['cpu_limit'])
        self.assertEqual(16.0, node3['cpu_available'])

    def test_summarize_resources_takes_the_larger_of_the_two(self):
        # The counters are an allocation ledger and the measurement
        # counts running domains, so either can be the larger. Published
        # headroom is bounded by whichever binds.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.update_node_metrics(
            'node2', {'cpu_total_instance_vcpus': 9})
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=16, limit_memory_mb=100000,
            used_cpus=3)
        self.mock_mariadb.update_node_metrics(
            'node3', {'cpu_total_instance_vcpus': 2})
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node3'), limit_cpus=16, limit_memory_mb=100000,
            used_cpus=7)

        resources = scheduler.Scheduler().summarize_resources()
        node2 = resources['per_node'][self._node_uuid('node2')]
        node3 = resources['per_node'][self._node_uuid('node3')]
        self.assertEqual(16.0 - 9, node2['cpu_available'])
        self.assertEqual(16.0 - 7, node3['cpu_available'])


class DegradedCapacityReadTestCase(SchedulerTestCase):
    """A read which failed is published; an empty table is not (G5).

    ``mariadb.get_scheduler_node_capacity()`` swallows a database
    outage and returns no rows, deliberately: the read is on the
    instance create hot path and on the queues daemon's preflight
    redirect, so restoring the default retry budget would reopen the
    watchdog window issue 3586 closed. That swallow stays. What it
    cannot be allowed to stay is invisible -- a cluster whose counters
    nobody could read pre-filters on measurement alone, and nothing in
    the instance's event trail said so.

    The other half matters just as much. A cluster the reconciler has
    not reached yet has an empty table, which is an ordinary state (P7:
    a node with no row admits unguarded rather than refusing every
    create mid-upgrade), so an implementation which evented on
    emptiness would fire on every single create there.
    """

    EVENT = 'schedule could not read the capacity counters'

    def _capacity_events(self, aem):
        return [c for c in aem.call_args_list if c[0][2] == self.EVENT]

    def _baseline(self):
        # As CapacityCounterTestCase: cpu_schedulable=1 and the fake
        # config's ratio of 16 cap each node at 16 vCPUs, with the
        # measurement claiming none are in use.
        return {
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'cpu_schedulable': 1,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 0,
            'cpu_available': 12,
        }

    def test_a_failed_read_publishes_exactly_one_event(self):
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.node_capacity_read_degraded = True

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        with mock.patch('shakenfist.scheduler.add_event_multi') as aem:
            nodes = scheduler.Scheduler().find_candidates(fake_inst)

        published = self._capacity_events(aem)
        self.assertEqual(1, len(published), aem.call_args_list)
        extra = published[0][1]['extra']
        self.assertEqual(['sufficient_idle_cpu', 'sufficient_idle_memory'],
                         extra['degraded_stages'])
        self.assertIn('admission is unchanged', extra['effect'])
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3', 'node4'),
            set(extra['candidates']))

        # Observability only: the same nodes come out as would have with
        # a readable but unpopulated table. Nothing here filters.
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3', 'node4'), set(nodes))

    def test_an_empty_but_readable_table_publishes_nothing(self):
        # The cluster mid-upgrade, or any cluster before its first
        # reconcile: no rows, no failure, and so no event -- otherwise
        # every create on it would carry a false alarm.
        self.mock_mariadb.set_node_metrics_same(self._baseline())

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        with mock.patch('shakenfist.scheduler.add_event_multi') as aem:
            nodes = scheduler.Scheduler().find_candidates(fake_inst)

        self.assertEqual([], self._capacity_events(aem), aem.call_args_list)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3', 'node4'), set(nodes))

    def test_a_populated_table_publishes_nothing(self):
        # The event is keyed on the read, not on what the counters then
        # do: a row which actually prunes a candidate is a working read.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=16, limit_memory_mb=100000,
            used_cpus=16)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        with mock.patch('shakenfist.scheduler.add_event_multi') as aem:
            nodes = scheduler.Scheduler().find_candidates(fake_inst)

        self.assertEqual([], self._capacity_events(aem), aem.call_args_list)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))


class RamCapacityCounterTestCase(SchedulerTestCase):
    """The RAM pre-filter reads the counters the guard will draw down.

    Both RAM measurements (memory_available and
    memory_total_instance_actual) lag placement: an instance which has
    not yet booted and faulted its allocation in reduces neither, so a
    burst of near-simultaneous creates passes them all against the same
    stale snapshot -- the issue 3636 OOM shape. The committed ledger
    moves at admission time, so the pre-filter charges it against the
    ledger's own limit, exactly as the guard will.
    """

    def _baseline(self, **overrides):
        metrics = {
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'cpu_schedulable': 2,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 0,
            'cpu_available': 12,
        }
        metrics.update(overrides)
        return metrics

    def test_prefilter_drops_a_node_whose_ram_ledger_is_full(self):
        # node2 has been placed with instances filling its memory limit,
        # none of which have booted: both memory measurements still look
        # idle, but the ledger is full and the guard would refuse, so
        # the node leaves the candidate list here instead of attracting
        # the create.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=32, limit_memory_mb=8192,
            used_memory_mb=8192)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_prefilter_keeps_a_node_with_ram_ledger_headroom(self):
        # Exactly enough ledger headroom for the default 1024 MB
        # instance admits.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=32, limit_memory_mb=8192,
            used_memory_mb=8192-1024)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertIn(self._node_uuid('node2'), nodes)

    def test_ram_prefilter_does_not_charge_an_instance_for_itself(self):
        # A reschedule runs against an instance which is already placed,
        # and its memory is already in that node's used_memory_mb.
        # Charging it a second time would drop the node the instance is
        # on from its own candidate list.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        fake_inst = self.mock_mariadb.create_instance(
            'fake-inst', place_on_node='node2')
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=32, limit_memory_mb=8192,
            used_memory_mb=8192)

        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertIn(self._node_uuid('node2'), nodes)

    def test_summarize_resources_bounds_ram_available_by_the_ledger(self):
        # node2's ledger says 25000 of its 30000 MB limit is committed,
        # even though nothing has booted: published headroom is the
        # ledger's 5000, not the measurement's 36000. node3 has no row
        # and keeps the measurement-based figure.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=66, limit_memory_mb=30000,
            used_memory_mb=25000)

        resources = scheduler.Scheduler().summarize_resources()
        node2 = resources['per_node'][self._node_uuid('node2')]
        node3 = resources['per_node'][self._node_uuid('node3')]
        self.assertEqual(25000, node2['ram_committed'])
        self.assertEqual(30000 - 25000, node2['ram_available'])
        self.assertEqual(0, node3['ram_committed'])
        self.assertEqual(24000 * 1.5, node3['ram_available'])


class RamAwareOrderingTestCase(SchedulerTestCase):
    """RAM commitment participates in ranking alongside CPU load.

    The issue 3636 funnel: a node whose instances are RAM-heavy but
    CPU-idle wins the load ranking precisely because of the workload
    that makes it dangerous, and attracts every large instance in a
    burst until the capacity guard finally refuses it -- observed as
    one 64 GB node carrying 96 GB of nominal guest RAM into OOM kills
    while its peers sat a third full. Committed RAM therefore bands
    and weights the ordering too, read from the counters admission
    draws down rather than the stale metrics.
    """

    def _seed_metrics(self):
        self.mock_mariadb.set_node_metrics_same()
        for n in ('node2', 'node3', 'node4'):
            self.mock_mariadb.update_node_metrics(n, {
                'cpu_max': 24, 'cpu_schedulable': 22})

    def test_ram_committed_node_is_ordered_last_not_dropped(self):
        # node2 is the funnel shape: CPU-idle (its RAM-heavy instances
        # contribute almost no load, normalised load 0.02 -> band 0)
        # but 87% RAM committed (band 3). Its peers carry moderate CPU
        # load (band 1) and little RAM. node2 must not win the ordering
        # -- but must stay in the list for the walk to fall through to.
        self._seed_metrics()
        self.mock_mariadb.update_node_metrics('node2', {'cpu_load_1': 0.5})
        for n in ('node3', 'node4'):
            self.mock_mariadb.update_node_metrics(n, {'cpu_load_1': 6.0})
        for n in ('node2', 'node3', 'node4'):
            self.mock_mariadb.set_node_capacity(
                self._node_uuid(n), limit_cpus=66, limit_memory_mb=64000,
                used_memory_mb=56000 if n == 'node2' else 8000)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        for seed in range(20):
            random.seed(seed)
            nodes = scheduler.Scheduler().find_candidates(fake_inst)
            self.assertSetEqual(
                self._node_uuids_set('node3', 'node4'), set(nodes[:2]))
            self.assertEqual(self._node_uuid('node2'), nodes[-1])

    def test_ram_commitment_weights_selection_within_a_band(self):
        # node2 and node3 share the winning band with equal CPU load
        # headroom; node2 is 90% RAM committed, node3 at 5%. node3 must
        # draw a clearly larger share of first places. Fixed seed makes
        # the draw deterministic; the bound is deliberately loose.
        self._seed_metrics()
        for n in ('node2', 'node3'):
            self.mock_mariadb.update_node_metrics(n, {'cpu_load_1': 17.6})
        self.mock_mariadb.update_node_metrics('node4', {'cpu_load_1': 25.0})
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node2'), limit_cpus=66, limit_memory_mb=64000,
            used_memory_mb=57600)
        self.mock_mariadb.set_node_capacity(
            self._node_uuid('node3'), limit_cpus=66, limit_memory_mb=64000,
            used_memory_mb=3200)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        random.seed(42)
        s = scheduler.Scheduler()
        wins = {self._node_uuid('node2'): 0, self._node_uuid('node3'): 0}
        for _ in range(1000):
            # These draws can take minutes on a loaded CI worker; keep
            # the mock's metrics rows fresh so the scheduler's periodic
            # refresh does not discard them as stale mid-test.
            for row in self.mock_mariadb.node_metrics_store.values():
                row['timestamp'] = time.time()
            nodes = s.find_candidates(fake_inst)
            self.assertSetEqual(
                self._node_uuids_set('node2', 'node3'), set(nodes[:2]))
            self.assertEqual(self._node_uuid('node4'), nodes[-1])
            wins[nodes[0]] += 1

        ratio = (wins[self._node_uuid('node3')] /
                 wins[self._node_uuid('node2')])
        self.assertGreater(
            ratio, 3.0,
            f'Expected node3 to lead far more often than node2, got {ratio} '
            f'({wins})')

    def test_measured_allocation_also_feeds_the_committed_fraction(self):
        # The ranking charges whichever ledger is larger, as the CPU
        # pre-filter does: a node whose counters have drifted low but
        # whose running domains measure large must still rank as
        # committed.
        self._seed_metrics()
        for n in ('node2', 'node3', 'node4'):
            self.mock_mariadb.update_node_metrics(
                n, {'cpu_load_1': 0.5, 'memory_max': 64000})
            self.mock_mariadb.set_node_capacity(
                self._node_uuid(n), limit_cpus=66, limit_memory_mb=64000,
                used_memory_mb=0)
        self.mock_mariadb.update_node_metrics(
            'node2', {'memory_total_instance_actual': 56000})

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        for seed in range(20):
            random.seed(seed)
            nodes = scheduler.Scheduler().find_candidates(fake_inst)
            self.assertEqual(self._node_uuid('node2'), nodes[-1])


class DiskReservationAdmissionTestCase(SchedulerTestCase):
    """Test disk admission against the candidate node's published reservation."""

    def _baseline(self, **overrides):
        # A metrics baseline that passes every non-disk admission stage.
        metrics = {
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 45*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12,
        }
        metrics.update(overrides)
        return metrics

    def test_admission_honours_published_disk_reservation(self):
        # Every node starts with 45 GiB free on the instances filesystem, so
        # under the default 20 GB reservation each has 25 GB of headroom. node2
        # advertises MORE raw free disk (60 GiB) but a much larger published
        # reservation (45 GB), leaving it only 15 GB of headroom -- so a 20 GB
        # instance must skip node2 and land on the default-reservation nodes.
        # This proves the per-node published reservation, not raw free disk,
        # drives admission in _has_sufficient_disk.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.update_node_metrics('node2', {
            'disk_free_instances': 60*GiB,
            'disk_reservation_gb': 45,
        })

        fake_inst = self.mock_mariadb.create_instance(
            'fake-inst', disk_spec=[{'base': 'cirros', 'size': 20}])
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_admission_falls_back_to_config_reservation(self):
        # Without a published disk_reservation_gb the check falls back to
        # NODE_DISK_RESERVATION_GB (20 GB default): 20 GiB free - 20 = 0 GB of
        # headroom, so even a 1 GB instance is rejected on every node.
        self.mock_mariadb.set_node_metrics_same(self._baseline(
            disk_free_instances=20*GiB))
        fake_inst = self.mock_mariadb.create_instance(
            'fake-inst', disk_spec=[{'base': 'cirros', 'size': 1}])
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_free_disk',
            str(exc))


class NodesByFreeDiskDescendingTestCase(SchedulerTestCase):
    """Test reservation-aware blob-placement ranking (nodes_by_free_disk_descending)."""

    def _set_blob_disk(self, fqdn, free_gib, reservation_gb):
        self.mock_mariadb.update_node_metrics(fqdn, {
            'disk_free_blobs': free_gib * GiB,
            'disk_reservation_gb': reservation_gb,
        })

    def test_ranking_and_filtering_respects_per_node_reservation(self):
        # Every node has an identical 100 GiB free on the blobs filesystem, so
        # ranking is driven purely by each node's own published reservation:
        # headroom = 100 - reservation.
        self.mock_mariadb.set_node_metrics_same()
        self._set_blob_disk('node2', 100, 20)      # headroom 80
        self._set_blob_disk('node3', 100, 50)      # headroom 50
        self._set_blob_disk('node4', 100, 70)      # headroom 30
        self._set_blob_disk('node1_net', 100, 95)  # headroom 5

        self.assertEqual(
            ['node2', 'node3', 'node4', 'node1_net'],
            nodes_by_free_disk_descending(intention='blobs'))

        # A minimum-headroom filter drops the high-reservation nodes even though
        # their raw free disk is identical to the survivors'.
        self.assertEqual(
            ['node2', 'node3'],
            nodes_by_free_disk_descending(minimum=45, intention='blobs'))

        # A maximum-headroom band keeps only the tightest nodes.
        self.assertEqual(
            ['node4', 'node1_net'],
            nodes_by_free_disk_descending(maximum=40, intention='blobs'))

    def test_missing_reservation_falls_back_to_config_default(self):
        # A metrics row without disk_reservation_gb falls back to the config
        # default (NODE_DISK_RESERVATION_GB=20): node3 with 40 GiB free has 20 GB
        # headroom and outranks node2 with 25 GiB free (5 GB headroom); node4
        # with 21 GiB free just clears the minimum, and node1_net with 5 GiB free
        # has negative headroom and is filtered out.
        self.mock_mariadb.set_node_metrics_same()
        self.mock_mariadb.update_node_metrics('node2', {
            'disk_free_blobs': 25 * GiB})
        self.mock_mariadb.update_node_metrics('node3', {
            'disk_free_blobs': 40 * GiB})
        self.mock_mariadb.update_node_metrics('node4', {
            'disk_free_blobs': 21 * GiB})
        self.mock_mariadb.update_node_metrics('node1_net', {
            'disk_free_blobs': 5 * GiB})

        self.assertEqual(
            ['node3', 'node2', 'node4'],
            nodes_by_free_disk_descending(minimum=1, intention='blobs'))

    def test_low_disk_band_includes_negative_headroom(self):
        # The blob rebalancer calls this helper with only a maximum headroom
        # band and no lower bound, so a node that has fallen BELOW its own
        # reservation (negative headroom) -- the most urgent to relieve -- must
        # still be returned. A default minimum of 0 would wrongly drop it.
        self.mock_mariadb.set_node_metrics_same()
        self._set_blob_disk('node2', 5, 20)       # headroom -15, critically low
        self._set_blob_disk('node3', 50, 20)      # headroom 30, low
        self._set_blob_disk('node4', 100, 20)     # headroom 80, plenty
        self._set_blob_disk('node1_net', 25, 20)  # headroom 5, low

        # Mirrors the rebalance call: a maximum headroom band, no minimum. node4
        # is excluded as not low; the three low nodes rank by headroom
        # descending, with the negative-headroom node last but present.
        self.assertEqual(
            ['node3', 'node1_net', 'node2'],
            nodes_by_free_disk_descending(maximum=40, intention='blobs'))


class AffinityTestCase(SchedulerTestCase):
    """Test CPU load affinity."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb.set_node_metrics_same()

    def test_affinity_to_same_node(self):
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['socialite']})

        # Start test
        inst = self.mock_mariadb.create_instance(
            'instance-3',
            metadata={
                "affinity": {
                    "socialite": 2,
                    "nerd": -100
                },
            })

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node3'), set(nodes))

    def test_anti_affinity_single_inst(self):
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['nerd']})

        # Start test
        inst = self.mock_mariadb.create_instance(
            'instance-3',
            metadata={
                "affinity": {
                    "socialite": 2,
                    "nerd": -100
                },
            })
        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node4'), set(nodes))

    def test_anti_affinity_multiple_inst(self):
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['nerd']})

        self.mock_mariadb.create_instance('instance-2',
                                          place_on_node='node4',
                                          metadata={'tags': ['nerd']})

        # Start test
        inst = self.mock_mariadb.create_instance(
            'instance-3',
            metadata={
                "affinity": {
                    "socialite": 2,
                    "nerd": -100
                },
            })
        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node2'), set(nodes))

    def test_anti_affinity_multiple_inst_different_tags(self):
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['socialite']})

        self.mock_mariadb.create_instance('instance-2',
                                          place_on_node='node4',
                                          metadata={'tags': ['nerd']})

        # Start test
        inst = self.mock_mariadb.create_instance(
            'instance-3',
            metadata={
                "affinity": {
                    "socialite": 2,
                    "nerd": -100
                },
            })
        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node3'), set(nodes))


class AffinityVersusLoadSheddingTestCase(SchedulerTestCase):
    """Affinity outranks the transient load shedding filters.

    Queue depth and disk bandwidth say a node is momentarily busy, not
    that it cannot host the instance. If they were allowed to eliminate
    the winning affinity group the user's placement request would be
    silently discarded -- which is exactly how the test_affinity
    functional test flaked under suite concurrency (issue 3565).
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb.set_node_metrics_same()

    def _saturate_disk(self, *fqdns):
        for fqdn in fqdns:
            self.mock_mariadb.update_node_metrics(
                fqdn, {DISK_BUSY_PER_SECOND_METRIC: '2000.5'})

    def _saturate_queue(self, *fqdns):
        for fqdn in fqdns:
            self.mock_mariadb.update_node_metrics(
                fqdn, {'node_queue_waiting': 100})

    def test_affinity_survives_saturated_disk(self):
        # The node we want to be near is the node which just built the
        # instance we want to be near, so it is precisely the node most
        # likely to be transiently thrashing its disk.
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['socialite']})
        self._saturate_disk('node3')

        inst = self.mock_mariadb.create_instance(
            'instance-2', metadata={'affinity': {'socialite': 100}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._node_uuids_set('node3'), set(nodes))

    def test_affinity_survives_long_queue(self):
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['socialite']})
        self._saturate_queue('node3')

        inst = self.mock_mariadb.create_instance(
            'instance-2', metadata={'affinity': {'socialite': 100}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._node_uuids_set('node3'), set(nodes))

    def test_anti_affinity_survives_saturated_disk(self):
        # The mirror case: every node the instance is allowed to be on is
        # busy, so the node it was asked to avoid used to be the only
        # survivor and won by default.
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['nerd']})
        self._saturate_disk('node2', 'node4')

        inst = self.mock_mariadb.create_instance(
            'instance-2', metadata={'affinity': {'nerd': -100}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node4'), set(nodes))

    def test_load_shedding_still_narrows_within_a_tier(self):
        # Two nodes score the same affinity, only one of them is busy. The
        # tier is big enough to absorb the exclusion, so the busy node is
        # dropped exactly as it always was.
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['nerd']})
        self._saturate_disk('node2')

        inst = self.mock_mariadb.create_instance(
            'instance-2', metadata={'affinity': {'nerd': -100}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._node_uuids_set('node4'), set(nodes))

    def test_load_shedding_still_applies_without_affinity(self):
        self._saturate_disk('node2')

        inst = self.mock_mariadb.create_instance('instance-1')

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_all_nodes_saturated_still_fails_with_affinity(self):
        # Affinity narrows the candidate list, it does not resurrect a
        # cluster where nothing is willing to take work.
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['socialite']})
        self._saturate_disk('node2', 'node3', 'node4')

        inst = self.mock_mariadb.create_instance(
            'instance-2', metadata={'affinity': {'socialite': 100}})

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_disk',
            str(exc))

    def test_admission_still_outranks_affinity(self):
        # A node which cannot fit the instance is not a candidate at all,
        # however much affinity would like it to be.
        self.mock_mariadb.create_instance('instance-1',
                                          place_on_node='node3',
                                          metadata={'tags': ['socialite']})
        self.mock_mariadb.update_node_metrics(
            'node3', {'memory_available': 0})

        inst = self.mock_mariadb.create_instance(
            'instance-2', metadata={'affinity': {'socialite': 100}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node4'), set(nodes))


class BinaryAffinityTestCase(SchedulerTestCase):
    """The four binary affinity constraints.

    require_* are admission: a node which does not satisfy them cannot
    host the instance, so they filter. prefer_* are ranking: they
    contribute +/-1 per matching co-located instance into the score the
    weighted form already produced.

    Every tag here is an *instance* tag carried by an instance already
    placed on a candidate node. Shaken Fist has no node capability tags,
    so a constraint naming a property of the hardware would be naming
    something that does not exist.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb.set_node_metrics_same()

    def test_require_with_tag_narrows_to_matching_nodes(self):
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['database']})

        inst = self.mock_mariadb.create_instance(
            'instance-2',
            metadata={'affinity': {'require_with_tag': ['database']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._node_uuids_set('node3'), set(nodes))

    def test_require_without_tag_excludes_matching_nodes(self):
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['batch']})

        inst = self.mock_mariadb.create_instance(
            'instance-2',
            metadata={'affinity': {'require_without_tag': ['batch']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node4'), set(nodes))

    def test_unsatisfiable_require_raises_the_affinity_subclass(self):
        # No instance anywhere carries this tag, so every candidate is
        # ejected. The exception must be the affinity subclass and not a
        # bare LowResourceException, because the create path answers the
        # two with different status codes -- 409 for a constraint nothing
        # satisfies, 507 for a cluster that is genuinely full.
        inst = self.mock_mariadb.create_instance(
            'instance-2',
            metadata={'affinity': {'require_with_tag': ['nonexistent']}})

        self.assertRaises(
            exceptions.AffinityConstraintUnsatisfiable,
            scheduler.Scheduler().find_candidates, inst)

    def test_the_affinity_subclass_is_still_a_low_resource_exception(self):
        # Preflight catches LowResourceException to redirect the instance
        # to another node, which is the right behaviour for a constraint
        # some other node may satisfy. A sibling exception would escape
        # that handler as a traceback, so the subclassing is load bearing
        # rather than tidiness.
        self.assertTrue(issubclass(
            exceptions.AffinityConstraintUnsatisfiable,
            exceptions.LowResourceException))

    def test_unsatisfiable_require_names_the_constraint(self):
        inst = self.mock_mariadb.create_instance(
            'instance-2',
            metadata={'affinity': {'require_with_tag': ['nonexistent']}})

        try:
            scheduler.Scheduler().find_candidates(inst)
            self.fail('expected AffinityConstraintUnsatisfiable')
        except exceptions.AffinityConstraintUnsatisfiable as e:
            # The caller has to be able to tell which constraint refused
            # them, and at which stage, or a 409 is no more actionable
            # than the 507 it replaces.
            self.assertIn('affinity_constraints', str(e))
            self.assertIn('nonexistent', str(e))

    def test_require_ignores_instances_in_another_namespace(self):
        # The hard filter inherits the scorer's namespace scope. Crossing
        # it would let a caller learn what another tenant is running by
        # watching where their own instances refuse to land.
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['database']}, namespace='other')

        inst = self.mock_mariadb.create_instance(
            'instance-2',
            metadata={'affinity': {'require_with_tag': ['database']}})

        self.assertRaises(
            exceptions.AffinityConstraintUnsatisfiable,
            scheduler.Scheduler().find_candidates, inst)

    def test_require_without_ignores_the_rescheduling_instance_itself(self):
        # find_candidates() is not create-only: preflight calls it on
        # every restart, by which time the instance is placed and is one
        # of its own node's neighbours. Counting itself would make an
        # instance carrying an excluded tag refuse the node it is
        # already running on, and every other node too once it moved.
        inst = self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['batch'],
                      'affinity': {'require_without_tag': ['batch']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertIn(self._node_uuid('node3'), nodes)

    def test_require_without_ignores_instances_in_another_namespace(self):
        # The other direction of the same trust boundary, and the one
        # which leaks if it is ever crossed: a caller who could trip
        # require_without_tag on another tenant's instances would learn
        # their tags by watching which nodes refuse to take theirs.
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['batch']}, namespace='other')

        inst = self.mock_mariadb.create_instance(
            'instance-2',
            metadata={'affinity': {'require_without_tag': ['batch']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertIn(self._node_uuid('node3'), nodes)

    def test_prefer_with_tag_ranks_matching_nodes_first(self):
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['socialite']})

        inst = self.mock_mariadb.create_instance(
            'instance-2',
            metadata={'affinity': {'prefer_with_tag': ['socialite']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._node_uuids_set('node3'), set(nodes))

    def test_prefer_without_tag_ranks_matching_nodes_last(self):
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['nerd']})

        inst = self.mock_mariadb.create_instance(
            'instance-2',
            metadata={'affinity': {'prefer_without_tag': ['nerd']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node4'), set(nodes))

    def test_prefer_scoring_is_count_proportional(self):
        # Two instances carrying the tag on node3, one on node2. A node
        # carrying more of the group really is more "with the group", so
        # node3 wins outright rather than tying at set membership.
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['socialite']})
        self.mock_mariadb.create_instance(
            'instance-2', place_on_node='node3',
            metadata={'tags': ['socialite']})
        self.mock_mariadb.create_instance(
            'instance-3', place_on_node='node2',
            metadata={'tags': ['socialite']})

        inst = self.mock_mariadb.create_instance(
            'instance-4',
            metadata={'affinity': {'prefer_with_tag': ['socialite']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._node_uuids_set('node3'), set(nodes))

    def test_prefer_terms_sum_across_tags(self):
        # node3 hosts two 'web' and one 'batch', scoring +2 -1 = +1.
        # node2 hosts one 'web' and no 'batch', scoring +1. They tie, so
        # a prefer_without_tag match really can be outvoted by neighbour
        # count on the other axis. This is the intended consequence of
        # count proportional scoring, and it is asserted rather than left
        # for an operator to discover from a placement.
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3', metadata={'tags': ['web']})
        self.mock_mariadb.create_instance(
            'instance-2', place_on_node='node3', metadata={'tags': ['web']})
        self.mock_mariadb.create_instance(
            'instance-3', place_on_node='node3', metadata={'tags': ['batch']})
        self.mock_mariadb.create_instance(
            'instance-4', place_on_node='node2', metadata={'tags': ['web']})

        inst = self.mock_mariadb.create_instance(
            'instance-5',
            metadata={'affinity': {'prefer_with_tag': ['web'],
                                   'prefer_without_tag': ['batch']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3'), set(nodes))

    def test_require_and_prefer_compose(self):
        # node2 and node3 both satisfy the requirement; only node3 also
        # attracts the preference.
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node2',
            metadata={'tags': ['database']})
        self.mock_mariadb.create_instance(
            'instance-2', place_on_node='node3',
            metadata={'tags': ['database', 'socialite']})

        inst = self.mock_mariadb.create_instance(
            'instance-3',
            metadata={'affinity': {'require_with_tag': ['database'],
                                   'prefer_with_tag': ['socialite']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._node_uuids_set('node3'), set(nodes))

    def test_weighted_form_is_untouched_by_the_binary_model(self):
        # The two shapes share one metadata key, so the weighted path has
        # to keep behaving exactly as it did.
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['socialite']})

        inst = self.mock_mariadb.create_instance(
            'instance-2', metadata={'affinity': {'socialite': 100}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._node_uuids_set('node3'), set(nodes))

    def test_empty_binary_spec_places_anywhere(self):
        inst = self.mock_mariadb.create_instance(
            'instance-1',
            metadata={'affinity': {'require_with_tag': [],
                                   'prefer_with_tag': []}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))


class WeightedAffinityMappingTestCase(SchedulerTestCase):
    """The weighted form, mapped onto the binary one.

    The weighted form stays working for one more release by being
    mapped mechanically at the point the scheduler reads it, so there is
    one scoring path rather than two which can drift apart.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb.set_node_metrics_same()

    def test_positive_maps_to_prefer_with(self):
        self.assertEqual(
            {'require_with_tag': [], 'require_without_tag': [],
             'prefer_with_tag': ['a'], 'prefer_without_tag': []},
            instance.map_weighted_affinity({'a': 100}))

    def test_negative_maps_to_prefer_without(self):
        self.assertEqual(
            {'require_with_tag': [], 'require_without_tag': [],
             'prefer_with_tag': [], 'prefer_without_tag': ['a']},
            instance.map_weighted_affinity({'a': -100}))

    def test_zero_maps_to_nothing(self):
        # Zero already meant nothing: a zero contribution never changed
        # a score, so mapping it to a preference would invent a request
        # the caller did not make.
        self.assertEqual(
            {'require_with_tag': [], 'require_without_tag': [],
             'prefer_with_tag': [], 'prefer_without_tag': []},
            instance.map_weighted_affinity({'a': 0}))

    def test_mixed_signs_map_to_both_lists(self):
        mapped = instance.map_weighted_affinity({'a': 5, 'b': -5, 'c': 0})
        self.assertEqual(['a'], mapped['prefer_with_tag'])
        self.assertEqual(['b'], mapped['prefer_without_tag'])

    def test_uniform_magnitude_preserves_ordering(self):
        # With uniform magnitude M the weighted score is exactly M times
        # the binary score for every candidate, so the ordering cannot
        # differ. This is the real condition, and it is about magnitude
        # rather than tag count -- every single-tag spec satisfies it,
        # but so does any spec whose weights all share a magnitude.
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3', metadata={'tags': ['a']})
        self.mock_mariadb.create_instance(
            'instance-2', place_on_node='node2', metadata={'tags': ['b']})

        weighted = self.mock_mariadb.create_instance(
            'instance-3', metadata={'affinity': {'a': 50, 'b': -50}})
        binary = self.mock_mariadb.create_instance(
            'instance-4',
            metadata={'affinity': {'prefer_with_tag': ['a'],
                                   'prefer_without_tag': ['b']}})

        self.assertEqual(
            scheduler.Scheduler().find_candidates(weighted),
            scheduler.Scheduler().find_candidates(binary))

    def test_mixed_magnitudes_diverge_and_that_is_intended(self):
        # {'a': 100, 'b': 1} maps to prefer_with_tag ['a', 'b'], so a
        # node carrying only b ties with a node carrying only a, where
        # the weighted form ranked them 1 against 100. F4 discards the
        # magnitude deliberately, so this divergence is asserted rather
        # than left as an unstated exception -- a test demanding
        # identical ordering in every case would be a gate the mapping
        # could only pass by not existing.
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3', metadata={'tags': ['a']})
        self.mock_mariadb.create_instance(
            'instance-2', place_on_node='node2', metadata={'tags': ['b']})

        inst = self.mock_mariadb.create_instance(
            'instance-3', metadata={'affinity': {'a': 100, 'b': 1}})

        # Both nodes now score +1, so both are in the winning tier --
        # where the unmapped weighted form would have preferred node3
        # outright.
        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3'), set(nodes))

    def test_the_affinity_event_is_still_published_when_skipped(self):
        # The skip must not take the event with it. It is what step 1's
        # cluster test reads and what the operator guide's diagnostic
        # recipe reads, and a create requesting no affinity is the one
        # an operator diagnosing an unexpected placement looks at
        # first. So: published, with candidates and a zero tier, and an
        # empty affinity_detail because there was nothing to score.
        inst = self.mock_mariadb.create_instance('instance-1')

        with mock.patch('shakenfist.scheduler.add_event_multi') as events:
            scheduler.Scheduler().find_candidates(inst)

        published = [c for c in events.call_args_list
                     if c[0][2] == 'schedule have highest affinity']
        self.assertEqual(1, len(published), events.call_args_list)
        extra = published[0][1]['extra']
        self.assertEqual({}, extra['affinity_detail'])
        self.assertEqual(0, extra['highest_affinity'])
        self.assertSetEqual(
            self._all_hypervisor_uuids(), set(extra['candidates']))

    def test_a_binary_spec_does_not_reach_the_weighted_coercion(self):
        # The scorer coerces weighted values with int(). This phase
        # teaches the same metadata key to hold lists, so a binary
        # specification reaching that coercion is a TypeError raised
        # inside find_candidates() -- which the create path does not
        # catch, making it a 500 on instance create: the failure class
        # the validator fix exists to remove, one layer down.
        inst = self.mock_mariadb.create_instance(
            'instance-1',
            metadata={'affinity': {'prefer_with_tag': ['a'],
                                   'require_without_tag': ['b']}})

        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))

    def test_a_stored_spec_the_validator_would_refuse_still_places(self):
        # Every instance whose affinity metadata was written before the
        # validator fix landed was never validated at all, so the
        # scheduler has to survive shapes the API now refuses. Skipping
        # the value rather than raising is what keeps such an instance
        # schedulable instead of permanently stuck -- a 500 here would
        # be on both create and every later reschedule.
        inst = self.mock_mariadb.create_instance(
            'instance-1',
            metadata={'affinity': {'a': ['not-an-int'], 'b': None,
                                   'c': float('inf'), 'd': 5}})

        with mock.patch('shakenfist.scheduler.add_event_multi') as events:
            nodes = scheduler.Scheduler().find_candidates(inst)

        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))
        # 'd' is the only coercible entry, so it is the only one which
        # survives the mapping -- the rest are dropped, not guessed at.
        published = [c for c in events.call_args_list
                     if c[0][2] == 'schedule have highest affinity']
        self.assertEqual(1, len(published), events.call_args_list)

    def test_a_weighted_spec_still_populates_affinity_detail(self):
        # The other edge of the short-circuit, and the one that fails
        # silently. The skip is keyed on the prefer_* lists, which are
        # empty for a weighted specification until the mapping fills
        # them -- so a short circuit shipped without the mapping stops
        # scoring affinity for every existing caller, with no create
        # failing and test_affinity merely skipping green. This is why
        # the two belong in one commit.
        self.mock_mariadb.create_instance(
            'instance-1', place_on_node='node3',
            metadata={'tags': ['first-node']})
        inst = self.mock_mariadb.create_instance(
            'instance-2', metadata={'affinity': {'first-node': 100}})

        with mock.patch('shakenfist.scheduler.add_event_multi') as events:
            scheduler.Scheduler().find_candidates(inst)

        published = [c for c in events.call_args_list
                     if c[0][2] == 'schedule have highest affinity']
        self.assertEqual(1, len(published), events.call_args_list)
        extra = published[0][1]['extra']
        self.assertNotEqual({}, extra['affinity_detail'])
        self.assertEqual(1, extra['highest_affinity'])

    def test_no_affinity_still_places_anywhere(self):
        # The scorer is skipped entirely when there is nothing to score,
        # so this asserts the skip left the outcome unchanged: one tier,
        # every candidate, rather than an empty tier and an IndexError.
        inst = self.mock_mariadb.create_instance('instance-1')
        nodes = scheduler.Scheduler().find_candidates(inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))
