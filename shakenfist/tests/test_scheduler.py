import mock
import testtools

from shakenfist import exceptions
from shakenfist import scheduler


class FakeInstance(object):
    def __init__(self, uuid='fake_uuid'):
        self.db_entry = {
            'uuid': uuid,
        }

    def db_setup(self, **kwargs):
        for k in kwargs:
            self.db_entry[k] = kwargs[k]

    def unique_label(self):
        return ('instance', self.db_entry['uuid'])


class FakeDB(object):
    def __init__(self, nodes, instances=None, interfaces=None):
        self.metrics = {}
        self.nodes = nodes
        self.instances = instances
        self.interfaces = interfaces

    def set_node_metrics_same(self, metrics):
        for n in self.nodes:
            self.metrics[n] = metrics

    # Faked methods from the db class
    def get_nodes(self):
        node_data = []
        for i in range(len(self.nodes)):
            n = self.nodes[i]
            node_data.append({'fqdn': n, 'ip': '10.0.0.'+str(i+1)})
        return node_data

    def get_instances(self, only_node=None):
        if only_node:
            if only_node in self.instances:
                return self.instances[only_node]
            return []
        ret = []
        for i in self.instances.keys():
            ret += self.instances[i]
        return ret

    def get_instance_interfaces(self, inst_uuid):
        return self.interfaces[inst_uuid]

    def get_metrics(self, node_name):
        if node_name not in self.metrics:
            raise exceptions.ReadException
        return self.metrics[node_name]


class SchedulerTestCase(testtools.TestCase):
    def setUp(self):
        super(SchedulerTestCase, self).setUp()

        self.recorded_op = mock.patch(
            'shakenfist.util.RecordedOperation')
        self.recorded_op.start()
        self.addCleanup(self.recorded_op.stop)

        # Fake system configuration
        def fake_config(key):
            data = {
                'NODE_NAME': 'node01',
                'SCHEDULER_CACHE_TIMEOUT': 30,
                'CPU_OVERCOMMIT_RATIO': 16,
                'RAM_OVERCOMMIT_RATIO': 1.5,
                'RAM_SYSTEM_RESERVATION': 5.0,
                'NETWORK_NODE_IP': '10.0.0.1',
                'LOG_METHOD_TRACE': 1,
            }

            if key not in data:
                raise Exception('fake_config() Unknown key')
            return data[key]

        self.mock_config = mock.patch('shakenfist.config.parsed.get',
                                      fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        self.mock_see_this_node = mock.patch('shakenfist.db.see_this_node')
        self.mock_see_this_node.start()
        self.addCleanup(self.mock_see_this_node.stop)

        self.mock_add_event = mock.patch('shakenfist.db.add_event')
        self.mock_add_event.start()
        self.addCleanup(self.mock_add_event.stop)


class LowResourceTestCase(SchedulerTestCase):
    """Test low resource exceptions"""

    def setUp(self):
        super(LowResourceTestCase, self).setUp()

        self.fake_db = FakeDB(['node1_net', 'node2', 'node3', 'node4'])

        mock_db_get_nodes = mock.patch('shakenfist.db.get_nodes',
                                       side_effect=self.fake_db.get_nodes)
        mock_db_get_nodes.start()
        self.addCleanup(mock_db_get_nodes.stop)

        mock_db_get_metrics = mock.patch('shakenfist.db.get_metrics',
                                         side_effect=self.fake_db.get_metrics)
        mock_db_get_metrics.start()
        self.addCleanup(mock_db_get_metrics.stop)

        self.mock_get_instances = mock.patch('shakenfist.db.get_instances')
        self.mock_get_instances.start()
        self.addCleanup(self.mock_get_instances.stop)

    def test_no_metrics(self):
        fake_inst = FakeInstance()
        fake_inst.db_setup(cpus=1)

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with metrics', str(exc))

    def test_requested_too_many_cpu(self):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 5,
        })

        fake_inst = FakeInstance()
        fake_inst.db_setup(cpus=6)

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
            'disk_free': 2000*1024*1024*1024
        })

        fake_inst = FakeInstance()
        fake_inst.db_setup(cpus=1)

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
            'disk_free': 2000*1024*1024*1024
        })

        fake_inst = FakeInstance()
        fake_inst.db_setup(cpus=1, memory=1024,
                           block_devices={'devices': [
                               {'size': 21, 'base': 'some-os'}
                           ]})

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
        })

        fake_inst = FakeInstance()
        fake_inst.db_setup(cpus=1, memory=1,
                           block_devices={'devices': [
                               {'size': 21, 'base': 'some-os'}
                           ]})

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough idle RAM', str(exc))

    def test_not_enough_disk(self):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 20*1024*1024*1024
        })

        fake_inst = FakeInstance()
        fake_inst.db_setup(cpus=1, memory=1024,
                           block_devices={'devices': [
                               {'size': 21, 'base': 'some-os'}
                           ]})

        exc = self.assertRaises(exceptions.LowResourceException,
                                scheduler.Scheduler().place_instance,
                                fake_inst,
                                [])
        self.assertEqual('No nodes with enough disk space', str(exc))

    def test_ok(self):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024
        })

        fake_inst = FakeInstance()
        fake_inst.db_setup(cpus=1, memory=1024,
                           block_devices={'devices': [
                               {'size': 8, 'base': 'some-os'}
                           ]})

        nodes = scheduler.Scheduler().place_instance(fake_inst, [])
        self.assertSetEqual(set(self.fake_db.nodes)-{'node1_net', },
                            set(nodes))


class CorrectAllocationTestCase(SchedulerTestCase):
    """Test correct node allocation"""

    def setUp(self):
        super(CorrectAllocationTestCase, self).setUp()

        self.fake_db = FakeDB(['node1_net', 'node2', 'node3', 'node4'],
                              {'node3': [{'uuid': 'inst-1',
                                          'node': 'node3',
                                          'block_devices': [],
                                          },
                                         ],
                               },
                              {'inst-1': [{'network_uuid': 'uuid-net1'},
                                          ],
                               })

        mock_db_get_nodes = mock.patch('shakenfist.db.get_nodes',
                                       side_effect=self.fake_db.get_nodes)
        mock_db_get_nodes.start()
        self.addCleanup(mock_db_get_nodes.stop)

        mock_db_get_metrics = mock.patch('shakenfist.db.get_metrics',
                                         side_effect=self.fake_db.get_metrics)
        mock_db_get_metrics.start()
        self.addCleanup(mock_db_get_metrics.stop)

        self.mock_get_instances = mock.patch('shakenfist.db.get_instances',
                                             side_effect=self.fake_db.get_instances)
        self.mock_get_instances.start()
        self.addCleanup(self.mock_get_instances.stop)

        self.mock_get_instances = mock.patch('shakenfist.db.get_instance_interfaces',
                                             side_effect=self.fake_db.get_instance_interfaces)
        self.mock_get_instances.start()
        self.addCleanup(self.mock_get_instances.stop)

    def test_any_node_but_not_network_node(self):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024
        })

        fake_inst = FakeInstance()
        fake_inst.db_setup(cpus=1, memory=1024,
                           block_devices={'devices': [
                               {'size': 8, 'base': 'some-os'}
                           ]},
                           )
        nets = [{'network_uuid': 'uuid-net2'}]

        nodes = scheduler.Scheduler().place_instance(fake_inst, nets)
        self.assertSetEqual(set(self.fake_db.nodes)-{'node1_net', },
                            set(nodes))

    def test_single_node_that_has_network(self):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 2000*1024*1024*1024
        })

        fake_inst = FakeInstance()
        fake_inst.db_setup(cpus=1, memory=1024,
                           block_devices={'devices': [
                               {'size': 8, 'base': 'some-os'}
                           ]},
                           )
        nets = [{'network_uuid': 'uuid-net1'}]

        nodes = scheduler.Scheduler().place_instance(fake_inst, nets)
        self.assertSetEqual(set(['node3']), set(nodes))
