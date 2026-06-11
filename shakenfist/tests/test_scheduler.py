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
