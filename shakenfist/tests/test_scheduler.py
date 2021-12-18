import mock
import time

from shakenfist.constants import GiB
from shakenfist import exceptions
from shakenfist import scheduler
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd
from shakenfist.config import SFConfig


fake_config = SFConfig(
    NODE_NAME='node01',
    SCHEDULER_CACHE_TIMEOUT=30,
    CPU_OVERCOMMIT_RATIO=16.0,
    RAM_OVERCOMMIT_RATIO=1.5,
    RAM_SYSTEM_RESERVATION=5.0,
    NETWORK_NODE_IP='10.0.0.1',
    LOG_METHOD_TRACE=1,
)


class SchedulerTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(SchedulerTestCase, self).setUp()

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


class LowResourceTestCase(SchedulerTestCase):
    """Test low resource exceptions."""

    def test_no_metrics(self):
        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with metrics', str(exc))

    def test_requested_too_many_cpu(self):
        self.mock_etcd.set_node_metrics_same({
            'cpu_max_per_instance': 5,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid', cpus=6)
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('Requested vCPUs exceeds vCPU limit', str(exc))

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

        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough idle CPU', str(exc))

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

        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough idle RAM', str(exc))

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

        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid')
        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough idle RAM', str(exc))

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

        fake_inst = self.mock_etcd.createInstance(
            'fake-inst', 'fakeuuid', disk_spec=[{
                    'base': 'cirros',
                            'size': 21
                }])

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough disk space', str(exc))

    def test_ok(self):
        self.mock_etcd.set_node_metrics_same()

        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid')

        nodes = scheduler.Scheduler().place_instance(fake_inst, [])
        self.assertSetEqual(set(self.mock_etcd.node_names)-{'node1_net', },
                            set(nodes))


class CorrectAllocationTestCase(SchedulerTestCase):
    """Test correct node allocation."""

    def test_any_node_but_not_network_node(self):
        self.mock_etcd.createInstance('instance-1', 'uuid-inst-1',
                                      place_on_node='node3')
        self.mock_etcd.set_node_metrics_same()

        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid')
        nets = [{'network_uuid': 'uuid-net2'}]

        nodes = scheduler.Scheduler().place_instance(fake_inst, nets)
        self.assertSetEqual(set(self.mock_etcd.node_names)-{'node1_net', },
                            set(nodes))


class ForcedCandidatesTestCase(SchedulerTestCase):
    """Test when we force candidates."""

    def setUp(self):
        super(ForcedCandidatesTestCase, self).setUp()
        self.mock_etcd.set_node_metrics_same()

    def test_only_two(self):
        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid')
        nodes = scheduler.Scheduler().place_instance(
            fake_inst, [], candidates=['node1_net', 'node2'])
        self.assertSetEqual({'node2', }, set(nodes))

    def test_no_such_node(self):
        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid')
        self.assertRaises(
            exceptions.CandidateNodeNotFoundException,
            scheduler.Scheduler().place_instance,
            fake_inst, [], candidates=['barry'])


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

        fake_inst = self.mock_etcd.createInstance('fake-inst', 'fakeuuid')

        s = scheduler.Scheduler()
        s.place_instance(fake_inst, None)
        self.assertEqual(22000, s.metrics['node1_net']['memory_available'])

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
        s.place_instance(fake_inst, None)
        self.assertEqual(11000, s.metrics['node1_net']['memory_available'])


class CPUloadAffinityTestCase(SchedulerTestCase):
    """Test CPU load affinity."""

    def setUp(self):
        super().setUp()
        self.mock_etcd.set_node_metrics_same()

    def test_affinity_to_same_node(self):
        self.mock_etcd.createInstance('instance-1', 'uuid-inst-1',
                                      place_on_node='node3',
                                      metadata={'tags': ['socialite']})

        # Start test
        inst = self.mock_etcd.createInstance('instance-3', 'uuid-inst-3',
                                             metadata={
                                                "affinity": {
                                                    "cpu": {
                                                        "socialite": 2,
                                                        "nerd": -100,
                                                    }
                                                },
                                             })

        nodes = scheduler.Scheduler().place_instance(inst, [])
        self.assertSetEqual({'node3'}, set(nodes))

    def test_anti_affinity_single_inst(self):
        self.mock_etcd.createInstance('instance-1', 'uuid-inst-1',
                                      place_on_node='node3',
                                      metadata={'tags': ['nerd']})

        # Start test
        inst = self.mock_etcd.createInstance('instance-3', 'uuid-inst-3',
                                             metadata={
                                                "affinity": {
                                                    "cpu": {
                                                        "socialite": 2,
                                                        "nerd": -100,
                                                    }
                                                },
                                             })
        nodes = scheduler.Scheduler().place_instance(inst, [])
        self.assertSetEqual({'node2', 'node4'}, set(nodes))

    def test_anti_affinity_multiple_inst(self):
        self.mock_etcd.createInstance('instance-1', 'uuid-inst-1',
                                      place_on_node='node3',
                                      metadata={'tags': ['nerd']})

        self.mock_etcd.createInstance('instance-2', 'uuid-inst-2',
                                      place_on_node='node4',
                                      metadata={'tags': ['nerd']})

        # Start test
        inst = self.mock_etcd.createInstance('instance-3', 'uuid-inst-3',
                                             metadata={
                                                "affinity": {
                                                    "cpu": {
                                                        "socialite": 2,
                                                        "nerd": -100,
                                                    }
                                                },
                                             })
        nodes = scheduler.Scheduler().place_instance(inst, [])
        self.assertSetEqual({'node2'}, set(nodes))

    def test_anti_affinity_multiple_inst_different_tags(self):
        self.mock_etcd.createInstance('instance-1', 'uuid-inst-1',
                                      place_on_node='node3',
                                      metadata={'tags': ['socialite']})

        self.mock_etcd.createInstance('instance-2', 'uuid-inst-2',
                                      place_on_node='node4',
                                      metadata={'tags': ['nerd']})

        # Start test
        inst = self.mock_etcd.createInstance('instance-3', 'uuid-inst-3',
                                             metadata={
                                                "affinity": {
                                                    "cpu": {
                                                        "socialite": 2,
                                                        "nerd": -100,
                                                    }
                                                },
                                             })
        nodes = scheduler.Scheduler().place_instance(inst, [])
        self.assertSetEqual({'node3'}, set(nodes))
