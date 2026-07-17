import random
import time
from unittest import mock

from shakenfist import exceptions
from shakenfist import scheduler
from shakenfist.config import SFConfig
from shakenfist.constants import GiB
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


fake_config = SFConfig(
    NODE_NAME='node01',
    SCHEDULER_CACHE_TIMEOUT=30,
    CPU_OVERCOMMIT_RATIO=16.0,
    RAM_OVERCOMMIT_RATIO=1.5,
    RAM_SYSTEM_RESERVATION=5.0,
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

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

    def _node_uuid(self, fqdn):
        """Helper to get node UUID from FQDN."""
        return self.mock_etcd.node_uuids[fqdn]

    def _node_uuids_set(self, *fqdns):
        """Helper to get a set of node UUIDs from FQDNs."""
        return {self.mock_etcd.node_uuids[f] for f in fqdns}

    def _all_hypervisor_uuids(self):
        """Helper to get UUIDs of all hypervisor nodes (excludes node1_net)."""
        return {self.mock_etcd.node_uuids[n]
                for n in self.mock_etcd.node_names
                if n != 'node1_net'}


class LowResourceTestCase(SchedulerTestCase):
    """Test low resource exceptions."""

    def test_no_metrics(self):
        fake_inst = self.mock_etcd.create_instance('fake-inst')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage is_hypervisor', str(exc))

    def test_requested_too_many_cpu(self):
        self.mock_etcd.set_node_metrics_same({
            'cpu_max_per_instance': 5,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_etcd.create_instance('fake-inst', cpus=6)
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage cpu_max_per_instance',
            str(exc))

    def test_not_enough_cpu(self):
        self.mock_etcd.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'cpu_total_instance_vcpus': 4*16,
            'memory_available': 5*1024+1024-1,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_available': 4
        })

        fake_inst = self.mock_etcd.create_instance('fake-inst')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_cpu',
            str(exc))

    def test_not_enough_ram_for_system(self):
        self.mock_etcd.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 5*1024+1024-1,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_etcd.create_instance('fake-inst')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_memory',
            str(exc))

    def test_not_enough_ram_on_node(self):
        self.mock_etcd.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 10000,
            'memory_max': 10000,
            'memory_total_instance_actual': 15001,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_etcd.create_instance('fake-inst')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_memory',
            str(exc))

    def test_not_enough_disk(self):
        self.mock_etcd.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 20*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_etcd.create_instance(
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
        self.mock_etcd.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 200*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12,
            'disk_busy_time_delta_per_sec': 2000
        })

        fake_inst = self.mock_etcd.create_instance(
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

    def test_ok(self):
        self.mock_etcd.set_node_metrics_same()

        fake_inst = self.mock_etcd.create_instance('fake-inst')

        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))


class CorrectAllocationTestCase(SchedulerTestCase):
    """Test correct node allocation."""

    def test_any_node_but_not_network_node(self):
        self.mock_etcd.create_instance('instance-1',
                                       place_on_node='node3')
        self.mock_etcd.set_node_metrics_same()

        fake_inst = self.mock_etcd.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))


class ForcedCandidatesTestCase(SchedulerTestCase):
    """Test when we force candidates."""

    def setUp(self):
        super().setUp()
        self.mock_etcd.set_node_metrics_same()

    def test_only_two(self):
        fake_inst = self.mock_etcd.create_instance('fake-inst')
        candidates = [self._node_uuid('node1_net'),
                      self._node_uuid('node2')]
        nodes = scheduler.Scheduler().find_candidates(
            fake_inst, candidates=candidates)
        self.assertSetEqual(
            self._node_uuids_set('node2'), set(nodes))

    def test_no_such_node(self):
        fake_inst = self.mock_etcd.create_instance('fake-inst')
        self.assertRaises(
            exceptions.CandidateNodeNotFoundException,
            scheduler.Scheduler().find_candidates,
            fake_inst, candidates=['barry'])


class MetricsRefreshTestCase(SchedulerTestCase):
    """Test that we refresh metrics."""

    def test_refresh(self):
        self.mock_etcd.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free_instances': 2000*GiB,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_etcd.create_instance('fake-inst')

        net_uuid = self._node_uuid('node1_net')
        s = scheduler.Scheduler()
        s.find_candidates(fake_inst)
        self.assertEqual(22000, s.metrics[net_uuid]['memory_available'])

        self.mock_etcd.set_node_metrics_same({
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
        self.mock_etcd.set_node_metrics_same()
        self.mock_etcd.update_node_metrics('node2', {
            'cpu_max': 12, 'cpu_schedulable': 8, 'cpu_load_1': 9.0})
        for n in ['node3', 'node4']:
            self.mock_etcd.update_node_metrics(n, {
                'cpu_max': 24, 'cpu_schedulable': 22, 'cpu_load_1': 1.0})

        fake_inst = self.mock_etcd.create_instance('fake-inst')
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
        self.mock_etcd.set_node_metrics_same()
        for n, load in [('node2', 0.3), ('node3', 0.5), ('node4', 0.1)]:
            self.mock_etcd.update_node_metrics(n, {
                'cpu_max': 24, 'cpu_schedulable': 22, 'cpu_load_1': load})

        fake_inst = self.mock_etcd.create_instance('fake-inst')
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
        self.mock_etcd.set_node_metrics_same()
        # weight = 0.75 * 12 - 2.9 = 6.1, normalised load 0.24 (bucket 0)
        self.mock_etcd.update_node_metrics('node2', {
            'cpu_max': 12, 'cpu_schedulable': 12, 'cpu_load_1': 2.9})
        # weight = 0.75 * 22 - 4.4 = 12.1, normalised load 0.2 (bucket 0)
        self.mock_etcd.update_node_metrics('node3', {
            'cpu_max': 24, 'cpu_schedulable': 22, 'cpu_load_1': 4.4})
        # Push node4 out of the winning bucket entirely.
        self.mock_etcd.update_node_metrics('node4', {
            'cpu_max': 12, 'cpu_schedulable': 12, 'cpu_load_1': 11.0})

        fake_inst = self.mock_etcd.create_instance('fake-inst')
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
        # synthetic reservation approximated from their role flags (one
        # core, two threads, for these plain hypervisors), per-node,
        # without error.
        self.mock_etcd.set_node_metrics_same()
        self.mock_etcd.update_node_metrics('node2', {
            'cpu_max': 12, 'cpu_load_1': 4.0})
        self.mock_etcd.update_node_metrics('node3', {
            'cpu_max': 12, 'cpu_load_1': 0.5})
        self.mock_etcd.update_node_metrics('node4', {
            'cpu_max': 12, 'cpu_load_1': 0.4})

        fake_inst = self.mock_etcd.create_instance('fake-inst')
        random.seed(1)
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        # node2 has normalised load 4.0 / 10 = 0.40 (bucket 1); the
        # others are in bucket 0.
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_old_dialect_infra_node_not_favoured(self):
        # A not-yet-upgraded infra-role node must not look bigger and
        # idler than an identical upgraded one: the synthetic fallback
        # applies the role-aware reservation to old-dialect rows, so
        # both size to 8 schedulable threads and share a bucket.
        self.mock_etcd.set_node_metrics_same()
        self.mock_etcd.update_node_metrics('node2', {
            'cpu_max': 12, 'cpu_schedulable': 8, 'cpu_load_1': 2.5,
            'is_database_node': True})
        self.mock_etcd.update_node_metrics('node3', {
            'cpu_max': 12, 'cpu_load_1': 2.5, 'is_database_node': True})
        self.mock_etcd.update_node_metrics('node4', {
            'cpu_max': 12, 'cpu_load_1': 12.0})

        fake_inst = self.mock_etcd.create_instance('fake-inst')
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
        # RAM_SYSTEM_RESERVATION=5.0.
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
        self.mock_etcd.set_node_metrics_same(self._baseline(
            cpu_schedulable=2, cpu_total_instance_vcpus=30))

        fits = self.mock_etcd.create_instance('fits', cpus=2)
        nodes = scheduler.Scheduler().find_candidates(fits)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))

        does_not_fit = self.mock_etcd.create_instance('does-not-fit', cpus=3)
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
        self.mock_etcd.set_node_metrics_same(self._baseline(
            cpu_max=12, cpu_schedulable=10, cpu_total_instance_vcpus=120))
        self.mock_etcd.update_node_metrics('node2', {'cpu_schedulable': 8})

        fake_inst = self.mock_etcd.create_instance('fake-inst', cpus=10)
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_ram_check_honours_published_reservation(self):
        # memory_reserved_mb=6144 leaves exactly 1024 MB for the default
        # 1024 MB instance; one fewer MB available must reject.
        self.mock_etcd.set_node_metrics_same(self._baseline(
            memory_available=6144+1024, memory_reserved_mb=6144))
        fake_inst = self.mock_etcd.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))

        self.mock_etcd.set_node_metrics_same(self._baseline(
            memory_available=6144+1024-1, memory_reserved_mb=6144))
        fake_inst2 = self.mock_etcd.create_instance('fake-inst2')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().find_candidates,
                                fake_inst2)
        self.assertEqual(
            'No nodes remaining at scheduling stage sufficient_idle_memory',
            str(exc))

    def test_ram_check_falls_back_to_config_reservation(self):
        # Without a published memory_reserved_mb the check falls back to
        # RAM_SYSTEM_RESERVATION (5 GB in the fake config).
        self.mock_etcd.set_node_metrics_same(self._baseline(
            memory_available=5*1024+1024))
        fake_inst = self.mock_etcd.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(self._all_hypervisor_uuids(), set(nodes))

        self.mock_etcd.set_node_metrics_same(self._baseline(
            memory_available=5*1024+1024-1))
        fake_inst2 = self.mock_etcd.create_instance('fake-inst2')
        self.assertRaises(exceptions.LowResourceException,
                          scheduler.Scheduler().find_candidates,
                          fake_inst2)

    def test_missing_memory_max_rejects_node(self):
        # A metrics row without memory_max must reject the node with a
        # reason, not raise ZeroDivisionError out of find_candidates.
        self.mock_etcd.set_node_metrics_same(self._baseline())
        node2_metrics = self.mock_etcd.node_metrics_store[
            self._node_uuid('node2')]['metrics']
        del node2_metrics['memory_max']

        fake_inst = self.mock_etcd.create_instance('fake-inst')
        nodes = scheduler.Scheduler().find_candidates(fake_inst)
        self.assertSetEqual(
            self._node_uuids_set('node3', 'node4'), set(nodes))

    def test_summarize_totals_exclude_overpacked_nodes(self):
        # A node packed beyond the admission cap reports negative
        # per-node headroom (honestly), but the cluster totals must only
        # sum the genuine headroom of the other nodes.
        self.mock_etcd.set_node_metrics_same(self._baseline(
            cpu_max=12, cpu_schedulable=10, cpu_total_instance_vcpus=0))
        self.mock_etcd.update_node_metrics('node2', {
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
        self.mock_etcd.set_node_metrics_same(self._baseline(
            cpu_max=12, cpu_schedulable=10, cpu_total_instance_vcpus=7,
            memory_reserved_mb=6144))
        self.mock_etcd.update_node_metrics('node3', {
            'cpu_max': 24, 'cpu_schedulable': 22, 'memory_reserved_mb': 8192})
        # node4 is an old-dialect row: fallback arithmetic applies.
        node4_metrics = self.mock_etcd.node_metrics_store[
            self._node_uuid('node4')]['metrics']
        del node4_metrics['cpu_schedulable']
        del node4_metrics['memory_reserved_mb']

        s = scheduler.Scheduler()
        resources = s.summarize_resources()

        for n, per_node in resources['per_node'].items():
            metrics = s.metrics[n]
            # The expected base must mirror _schedulable_threads(): the
            # published value when present, otherwise the role-aware
            # synthetic reservation (two threads per reserved core).
            expected_base = metrics.get('cpu_schedulable')
            if not expected_base:
                reserved_cores = 1
                if (metrics.get('is_network_node') or
                        metrics.get('is_database_node')):
                    reserved_cores += 1
                expected_base = max(
                    1, metrics.get('cpu_max', 0) - reserved_cores * 2)
            expected_reserved = metrics.get('memory_reserved_mb', 5.0 * 1024)

            self.assertEqual(expected_base, per_node['cpu_schedulable'])
            self.assertEqual(expected_reserved, per_node['memory_reserved_mb'])
            self.assertEqual(
                expected_base * 16.0 -
                metrics.get('cpu_total_instance_vcpus', 0),
                per_node['cpu_available'])
            self.assertEqual(
                metrics.get('memory_available', 0) - expected_reserved,
                per_node['ram_max_per_instance'])


class AffinityTestCase(SchedulerTestCase):
    """Test CPU load affinity."""

    def setUp(self):
        super().setUp()
        self.mock_etcd.set_node_metrics_same()

    def test_affinity_to_same_node(self):
        self.mock_etcd.create_instance('instance-1',
                                       place_on_node='node3',
                                       metadata={'tags': ['socialite']})

        # Start test
        inst = self.mock_etcd.create_instance(
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
        self.mock_etcd.create_instance('instance-1',
                                       place_on_node='node3',
                                       metadata={'tags': ['nerd']})

        # Start test
        inst = self.mock_etcd.create_instance(
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
        self.mock_etcd.create_instance('instance-1',
                                       place_on_node='node3',
                                       metadata={'tags': ['nerd']})

        self.mock_etcd.create_instance('instance-2',
                                       place_on_node='node4',
                                       metadata={'tags': ['nerd']})

        # Start test
        inst = self.mock_etcd.create_instance(
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
        self.mock_etcd.create_instance('instance-1',
                                       place_on_node='node3',
                                       metadata={'tags': ['socialite']})

        self.mock_etcd.create_instance('instance-2',
                                       place_on_node='node4',
                                       metadata={'tags': ['nerd']})

        # Start test
        inst = self.mock_etcd.create_instance(
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
