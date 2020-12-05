import mock

from shakenfist import exceptions
from shakenfist import scheduler
from shakenfist.tests import test_shakenfist
from shakenfist.config import SFConfig
from shakenfist.virt import Instance


class FakeInstance(Instance):
    def add_event(self, operation, phase, duration=None, message=None):
        pass


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
    def get_nodes(self, seen_recently=False):
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

        self.mock_config = mock.patch('shakenfist.db.config', fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        self.mock_see_this_node = mock.patch('shakenfist.db.see_this_node')
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

    @mock.patch('shakenfist.images.Image.from_url')
    @mock.patch('shakenfist.db.get_image_metadata_all', return_value=None)
    def test_not_enough_disk(self, mock_get_image_meta, mock_image_from_url):
        self.fake_db.set_node_metrics_same({
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'memory_available': 22000,
            'memory_max': 24000,
            'disk_free': 20*1024*1024*1024
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

    @mock.patch('shakenfist.images.Image.from_url')
    @mock.patch('shakenfist.db.get_image_metadata_all', return_value=None)
    def test_ok(self, mock_get_image_meta, mock_image_from_url):
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

        nodes = scheduler.Scheduler().place_instance(fake_inst, [])
        self.assertSetEqual(set(self.fake_db.nodes)-{'node1_net', },
                            set(nodes))


class CorrectAllocationTestCase(SchedulerTestCase):
    """Test correct node allocation."""

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

        self.mock_get_instances = mock.patch(
            'shakenfist.db.get_instances',
            side_effect=self.fake_db.get_instances)
        self.mock_get_instances.start()
        self.addCleanup(self.mock_get_instances.stop)

        self.mock_get_instances = mock.patch(
            'shakenfist.db.get_instance_interfaces',
            side_effect=self.fake_db.get_instance_interfaces)
        self.mock_get_instances.start()
        self.addCleanup(self.mock_get_instances.stop)

    @ mock.patch('shakenfist.images.Image.from_url')
    @ mock.patch('shakenfist.db.get_image_metadata_all', return_value=None)
    def test_any_node_but_not_network_node(self, mock_get_image_meta, mock_image_from_url):
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
        nets = [{'network_uuid': 'uuid-net2'}]

        nodes = scheduler.Scheduler().place_instance(fake_inst, nets)
        self.assertSetEqual(set(self.fake_db.nodes)-{'node1_net', },
                            set(nodes))

    @ mock.patch('shakenfist.images.Image.from_url')
    @ mock.patch('shakenfist.db.get_image_metadata_all', return_value=None)
    def test_single_node_that_has_network(self, mock_get_image_meta, mock_image_from_url):
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
        nets = [{'network_uuid': 'uuid-net1'}]

        nodes = scheduler.Scheduler().place_instance(fake_inst, nets)
        self.assertSetEqual(set(['node3']), set(nodes))


class FindMostTestCase(SchedulerTestCase):
    """Test basic information source to scheduler."""

    def setUp(self):
        super(FindMostTestCase, self).setUp()

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

    @ mock.patch('shakenfist.db.get_image_metadata_all',
                 return_value={
                     '/sf/image/095fdd2b66625412aa/node2': {
                         'checksum': None,
                         'fetched': 'Tue, 20 Oct 2020 23:02:29 -0000',
                         'file_version': 1,
                         'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                         'size': 200000,
                         'url': 'req_image1',
                         'version': 1,
                     },
                     '/sf/image/aca41cefa18b052074e092/node3': {
                         'checksum': None,
                         'fetched': 'Tue, 20 Oct 2020 23:02:29 -0000',
                         'file_version': 1,
                         'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                         'size': 200000,
                         'url': 'http://example.com',
                         'version': 1,
                     }
                 })
    def test_most_matching_images(self, mock_get_meta_all):
        req_images = ['req_image1']
        candidates = ['node1_net', 'node2', 'node3', 'node4']

        finalists = scheduler.Scheduler()._find_most_matching_images(
            req_images, candidates)
        self.assertSetEqual(set(['node2']), set(finalists))

    @mock.patch('shakenfist.db.get_image_metadata_all',
                return_value={
                    '/sf/image/095fdd2b66625412aa/node1_net': {
                        'url': 'req_image1',
                    },
                    '/sf/image/095fdd2b66635412aa/node1_net': {
                        'url': 'req_image2',
                    },
                    '/sf/image/095fdd2b66625412aa/node2': {
                        'url': 'req_image2',
                    },
                    '/sf/image/095fdd2b66625712aa/node3': {
                        'url': 'req_image2',
                    },
                    '/sf/image/095fdd2b66625482aa/node4': {
                        'url': 'req_image4',
                    },
                    '/sf/image/aca41cefa18b052974e092/node4': {
                        'url': 'req_image5',
                    }
                })
    def test_most_matching_images_big(self, mock_get_meta_all):
        candidates = ['node1_net', 'node2', 'node3', 'node4']

        finalists = scheduler.Scheduler()._find_most_matching_images(
            ['req_image1'], candidates)
        self.assertSetEqual(set(['node1_net']), set(finalists))

        finalists = scheduler.Scheduler()._find_most_matching_images(
            ['req_image1', 'req_image2'], candidates)
        self.assertSetEqual(set(['node1_net']), set(finalists))

        finalists = scheduler.Scheduler()._find_most_matching_images(
            ['req_image2'], candidates)
        self.assertSetEqual(
            set(['node1_net', 'node2', 'node3']), set(finalists))
