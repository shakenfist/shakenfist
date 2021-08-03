import mock
import time

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import exceptions
from shakenfist.instance import Instance
from shakenfist import scheduler
from shakenfist.tests import test_shakenfist
from shakenfist.config import SFConfig


class FakeInstance(Instance):
    def add_event(self, operation, phase, duration=None, message=None):
        print('op = %s, phase = %s, message = %s'
              % (operation, phase, message))


class FakeNode(object):
    def __init__(self, fqdn, ip):
        self.uuid = fqdn
        self.ip = ip
        self.state = dbo.STATE_CREATED

    def unique_label(self):
        return ('fake_node', self.uuid)


class FakeDB(object):
    def __init__(self, nodes):
        self.metrics = {}
        self.nodes = nodes

    def set_node_metrics_same(self, metrics):
        for n in self.nodes:
            self.metrics[n] = metrics

    def get_metrics(self, node_name):
        if node_name not in self.metrics:
            raise exceptions.ReadException
        return self.metrics[node_name]


class FakeInterface(object):
    def __init__(self, network_uuid):
        self.network_uuid = network_uuid


fake_config = SFConfig(
    NODE_NAME='node01',
    SCHEDULER_CACHE_TIMEOUT=30,
    CPU_OVERCOMMIT_RATIO=16.0,
    RAM_OVERCOMMIT_RATIO=1.5,
    RAM_SYSTEM_RESERVATION=5.0,
    NETWORK_NODE_IP='10.0.0.1',
    LOG_METHOD_TRACE=1,
)


class SchedulerTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super(SchedulerTestCase, self).setUp()

        self.recorded_op = mock.patch(
            'shakenfist.util.RecordedOperation')
        self.recorded_op.start()
        self.addCleanup(self.recorded_op.stop)

        self.mock_config = mock.patch(
            'shakenfist.scheduler.config', fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        self.mock_see_this_node = mock.patch(
            'shakenfist.node.Node.observe_this_node')
        self.mock_see_this_node.start()
        self.addCleanup(self.mock_see_this_node.stop)

        self.mock_add_event = mock.patch('shakenfist.db.add_event')
        self.mock_add_event.start()
        self.addCleanup(self.mock_add_event.stop)


class LowResourceTestCase(SchedulerTestCase):
    """Test low resource exceptions."""

    def setUp(self):
        super(LowResourceTestCase, self).setUp()

        self.fake_db = FakeDB(['node1_net', 'node2', 'node3', 'node4'])

        mock_db_get_metrics = mock.patch('shakenfist.db.get_metrics',
                                         side_effect=self.fake_db.get_metrics)
        mock_db_get_metrics.start()
        self.addCleanup(mock_db_get_metrics.stop)

        self.mock_config = mock.patch(
            'shakenfist.scheduler.config', fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        mock_get_nodes = mock.patch(
            'shakenfist.scheduler.Nodes',
            return_value=[FakeNode('node1_net', '10.0.0.1'),
                          FakeNode('node2', '10.0.0.2'),
                          FakeNode('node3', '10.0.0.3'),
                          FakeNode('node4', '10.0.0.4')])
        mock_get_nodes.start()
        self.addCleanup(mock_get_nodes.stop)

        self.mock_get_instances = mock.patch('shakenfist.instance.Instances')
        self.mock_get_instances.start()
        self.addCleanup(self.mock_get_instances.stop)

    def test_no_metrics(self):
        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with metrics', str(exc))

    def test_requested_too_many_cpu(self):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 5,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 6,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('Requested vCPUs exceeds vCPU limit', str(exc))

    def test_not_enough_cpu(self):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'cpu_total_instance_vcpus': 4*16,
            'memory_available': 5*1024+1024-1,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024,
            'cpu_available': 4
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough idle CPU', str(exc))

    def test_not_enough_ram_for_system(self):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 5*1024+1024-1,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough idle RAM', str(exc))

    def test_not_enough_ram_on_node(self):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 10000,
            'memory_max': 10000,
            'memory_total_instance_actual': 15001,
            'disk_free': 2000*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough idle RAM', str(exc))

    @mock.patch('shakenfist.images.Image.new')
    @mock.patch('shakenfist.artifact.Artifacts', return_value=[])
    def test_not_enough_disk(self, mock_get_image_meta, mock_image_from_url):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 20*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 21
            }]})

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough disk space', str(exc))

    @mock.patch('shakenfist.images.Image.new')
    @mock.patch('shakenfist.artifact.Artifacts', return_value=[])
    def test_ok(self, mock_get_image_meta, mock_image_from_url):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        nodes = scheduler.Scheduler().place_instance(fake_inst, [])
        self.assertSetEqual(set(self.fake_db.nodes)-{'node1_net', },
                            set(nodes))


class CorrectAllocationTestCase(SchedulerTestCase):
    """Test correct node allocation."""

    def setUp(self):
        super(CorrectAllocationTestCase, self).setUp()

        self.fake_db = FakeDB(['node1_net', 'node2', 'node3', 'node4'])

        mock_db_get_metrics = mock.patch('shakenfist.db.get_metrics',
                                         side_effect=self.fake_db.get_metrics)
        mock_db_get_metrics.start()
        self.addCleanup(mock_db_get_metrics.stop)

        self.mock_config = mock.patch(
            'shakenfist.scheduler.config', fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        mock_get_nodes = mock.patch(
            'shakenfist.scheduler.Nodes',
            return_value=[FakeNode('node1_net', '10.0.0.1'),
                          FakeNode('node2', '10.0.0.2'),
                          FakeNode('node3', '10.0.0.3'),
                          FakeNode('node4', '10.0.0.4')])
        mock_get_nodes.start()
        self.addCleanup(mock_get_nodes.stop)

    @mock.patch('shakenfist.instance.Instance._db_get_attribute')
    @mock.patch('shakenfist.instance.Instance._db_get',
                return_value={
                    'uuid': 'inst-1',
                    'cpus': 1,
                    'memory': 1024,
                    'node': 'node3',
                    'disk_spec': [{'base': 'cirros', 'size': 21}]
                })
    @mock.patch('shakenfist.etcd.get_all',
                return_value=[(None, {
                    'uuid': 'inst-1',
                    'cpus': 1,
                    'memory': 1024,
                    'node': 'node3',
                    'disk_spec': [{'base': 'cirros', 'size': 21}]
                })])
    @mock.patch('shakenfist.images.Image.new')
    @mock.patch('shakenfist.artifact.Artifacts', return_value=[])
    def test_any_node_but_not_network_node(
            self, mock_get_image_meta, mock_image_from_url, mock_get_instances,
            mock_get_instance, mock_instance_attribute):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})
        nets = [{'network_uuid': 'uuid-net2'}]

        nodes = scheduler.Scheduler().place_instance(fake_inst, nets)
        self.assertSetEqual(set(self.fake_db.nodes)-{'node1_net', },
                            set(nodes))

    @mock.patch('shakenfist.networkinterface.interfaces_for_instance',
                return_value=[FakeInterface('uuid-net1')])
    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                return_value={
                    'node': 'node3'
                })
    @mock.patch('shakenfist.instance.Instance._db_get',
                return_value={
                    'uuid': 'inst-1',
                    'cpus': 1,
                    'memory': 1024,
                    'disk_spec': [{'base': 'cirros', 'size': 21}]
                })
    @mock.patch('shakenfist.etcd.get_all',
                return_value=[(None, {
                    'uuid': 'inst-1',
                    'cpus': 1,
                    'memory': 1024,
                    'disk_spec': [{'base': 'cirros', 'size': 21}]
                })])
    @mock.patch('shakenfist.images.Image.new')
    @mock.patch('shakenfist.artifact.Artifacts', return_value=[])
    def test_single_node_that_has_network(
            self, mock_get_image_meta, mock_image_from_url, mock_get_instances,
            mock_get_instance, mock_instance_attribute, mock_instance_interfaces):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{'base': 'cirros', 'size': 8}]
        })
        nets = [{'network_uuid': 'uuid-net1'}]

        nodes = scheduler.Scheduler().place_instance(fake_inst, nets)
        self.assertSetEqual(set(['node3']), set(nodes))


class ForcedCandidatesTestCase(SchedulerTestCase):
    """Test when we force candidates."""

    def setUp(self):
        super(ForcedCandidatesTestCase, self).setUp()

        self.fake_db = FakeDB(['node1_net', 'node2', 'node3', 'node4'])

        mock_db_get_metrics = mock.patch('shakenfist.db.get_metrics',
                                         side_effect=self.fake_db.get_metrics)
        mock_db_get_metrics.start()
        self.addCleanup(mock_db_get_metrics.stop)

        self.mock_config = mock.patch(
            'shakenfist.scheduler.config', fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        mock_get_nodes = mock.patch(
            'shakenfist.scheduler.Nodes',
            return_value=[FakeNode('node1_net', '10.0.0.1'),
                          FakeNode('node2', '10.0.0.2'),
                          FakeNode('node3', '10.0.0.3'),
                          FakeNode('node4', '10.0.0.4')])
        mock_get_nodes.start()
        self.addCleanup(mock_get_nodes.stop)

        self.mock_get_instances = mock.patch('shakenfist.instance.Instances')
        self.mock_get_instances.start()
        self.addCleanup(self.mock_get_instances.stop)

    @mock.patch('shakenfist.images.Image.new')
    @mock.patch('shakenfist.artifact.Artifacts', return_value=[])
    def test_only_network_node(self, mock_get_image_meta, mock_image_from_url):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        nodes = scheduler.Scheduler().place_instance(
            fake_inst, [], candidates=['node1_net'])
        self.assertSetEqual({'node1_net', }, set(nodes))

    @mock.patch('shakenfist.images.Image.new')
    @mock.patch('shakenfist.artifact.Artifacts', return_value=[])
    def test_only_two(self, mock_get_image_meta, mock_image_from_url):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        nodes = scheduler.Scheduler().place_instance(
            fake_inst, [], candidates=['node1_net', 'node2'])
        self.assertSetEqual({'node2', }, set(nodes))

    @mock.patch('shakenfist.images.Image.new')
    @mock.patch('shakenfist.artifact.Artifacts', return_value=[])
    def test_no_such_node(self, mock_get_image_meta, mock_image_from_url):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024
        })

        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        self.assertRaises(
            exceptions.CandidateNodeNotFoundException,
            scheduler.Scheduler().place_instance,
            fake_inst, [], candidates=['barry'])


class MetricsRefreshTestCase(SchedulerTestCase):
    """Test that we refresh metrics."""

    def setUp(self):
        super(MetricsRefreshTestCase, self).setUp()

        self.fake_db = FakeDB(['node1_net', 'node2', 'node3', 'node4'])

        mock_db_get_metrics = mock.patch('shakenfist.db.get_metrics',
                                         side_effect=self.fake_db.get_metrics)
        mock_db_get_metrics.start()
        self.addCleanup(mock_db_get_metrics.stop)

        self.mock_config = mock.patch(
            'shakenfist.scheduler.config', fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        mock_get_nodes = mock.patch(
            'shakenfist.scheduler.Nodes',
            return_value=[FakeNode('node1_net', '10.0.0.1'),
                          FakeNode('node2', '10.0.0.2'),
                          FakeNode('node3', '10.0.0.3'),
                          FakeNode('node4', '10.0.0.4')])
        mock_get_nodes.start()
        self.addCleanup(mock_get_nodes.stop)

        self.mock_get_instances = mock.patch('shakenfist.instance.Instances')
        self.mock_get_instances.start()
        self.addCleanup(self.mock_get_instances.stop)

    @mock.patch('shakenfist.images.Image.new')
    @mock.patch('shakenfist.artifact.Artifacts', return_value=[])
    def test_refresh(self, mock_get_image_meta, mock_image_from_url):
        fake_inst = FakeInstance({
            'uuid': 'fakeuuid',
            'cpus': 1,
            'memory': 1024,
            'disk_spec': [{
                'base': 'cirros',
                        'size': 8
            }]})

        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })

        s = scheduler.Scheduler()
        s.place_instance(fake_inst, [])
        self.assertEqual(22000, s.metrics['node1_net']['memory_available'])

        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 11000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024,
            'cpu_total_instance_vcpus': 4,
            'cpu_available': 12
        })
        s.metrics_updated = time.time() - 400
        s.place_instance(fake_inst, [])
        self.assertEqual(11000, s.metrics['node1_net']['memory_available'])
