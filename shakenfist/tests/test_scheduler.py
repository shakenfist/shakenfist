import random
import time
from unittest import mock

from shakenfist import exceptions
from shakenfist import scheduler
from shakenfist.config import SFConfig
from shakenfist.constants import DISK_BUSY_PER_SECOND_METRIC
from shakenfist.constants import GiB
from shakenfist.instance import Instance
from shakenfist.node import Node
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
    """Test load-per-thread bucketing and headroom-weighted selection."""

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
                self._node_uuids_set('node3', 'node4'), set(nodes))

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
            nodes = s.find_candidates(fake_inst)
            self.assertSetEqual(
                self._node_uuids_set('node2', 'node3'), set(nodes))
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
        # others are in bucket 0.
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

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
            self._node_uuids_set('node2', 'node3'), set(nodes))


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


class PlacementLedgerAdmissionTestCase(SchedulerTestCase):
    """Test admission against vCPUs committed by placement (issue 3498).

    cpu_total_instance_vcpus only counts *running* libvirt domains and is
    republished once a minute, so a node which has just been given work
    still measures as idle. Admission must charge a node for what it has
    been placed with, or a burst of creates all land on the same node and
    push it past its hard maximum.
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

    def test_unbooted_placements_are_charged_to_their_node(self):
        # node2 has been placed with 16 vCPUs which have not booted yet,
        # so the measurement still reads zero. It must not be a candidate.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.create_instance(
            'placed-1', cpus=8, place_on_node='node2')
        self.mock_mariadb.create_instance(
            'placed-2', cpus=8, place_on_node='node2')

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_unbooted_placements_can_exhaust_the_cluster(self):
        # The same, on every hypervisor: a targeted or untargeted create
        # is refused at the CPU stage rather than overfilling a node.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        for node in ('node2', 'node3', 'node4'):
            self.mock_mariadb.create_instance(
                'placed-%s' % node, cpus=16, place_on_node=node)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_cpu',
            str(exc))

    def test_measurement_still_wins_when_it_is_higher(self):
        # The ledger misses nothing the measurement sees, but the
        # measurement can be higher (a domain a node started for itself,
        # or a placement row already removed), so admission takes the
        # larger of the two.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.update_node_metrics(
            'node2', {'cpu_total_instance_vcpus': 16})
        self.mock_mariadb.create_instance(
            'placed-1', cpus=1, place_on_node='node2')

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_instance_is_not_charged_for_itself(self):
        # The preflight path reschedules an instance which is already
        # placed on the node being considered. Charging it for its own
        # vCPUs as well as the request would reject a node which fits.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        fake_inst = self.mock_mariadb.create_instance(
            'fake-inst', cpus=9, place_on_node='node2')

        nodes = scheduler.Scheduler().find_candidates(
            fake_inst, candidates=[self._node_uuid('node2')])
        self.assertSetEqual(self._node_uuids_set('node2'), set(nodes))

    def test_summarize_resources_reports_committed_capacity(self):
        # The admin resources API must not advertise headroom that
        # admission will refuse to use.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.create_instance(
            'placed-1', cpus=5, place_on_node='node2')

        resources = scheduler.Scheduler().summarize_resources()
        self.assertEqual(
            16.0 - 5,
            resources['per_node'][self._node_uuid('node2')]['cpu_available'])
        self.assertEqual(
            16.0,
            resources['per_node'][self._node_uuid('node3')]['cpu_available'])

    def test_deleted_instances_are_not_charged(self):
        # A deleted instance's placement row outlives it whenever the
        # normal teardown does not reach _delete_globally() -- a node
        # which died mid-delete is the obvious case. Charging for it
        # would take capacity away from a node permanently, with no
        # self-healing path, so the ledger skips deleted instances
        # exactly as _RECONCILE_USAGE_SQL does.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        self.mock_mariadb.create_instance(
            'gone', cpus=16, place_on_node='node2',
            set_state=Instance.STATE_DELETED)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node3', 'node4'), set(nodes))

    def test_only_the_authoritative_placement_is_charged(self):
        # place_instance() removes the old node's reference on a
        # best-effort basis (it skips a node whose row has gone), so an
        # instance which has moved can leave a reference behind on the
        # node it left. The instance's own placement attribute is the
        # authority for where it actually is, and a node is charged only
        # for the instances which agree they are on it.
        self.mock_mariadb.set_node_metrics_same(self._baseline())
        inst = self.mock_mariadb.create_instance(
            'moved', cpus=16, place_on_node='node3')
        Node.from_db(self._node_uuid('node2')).add_instance(inst.uuid)

        fake_inst = self.mock_mariadb.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node2', 'node4'), set(nodes))


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
