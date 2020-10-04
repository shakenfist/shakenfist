from shakenfist_ci import base


class TestInstanceMetadata(base.BaseTestCase):
    def setUp(self):
        super(TestInstanceMetadata, self).setUp()

        self.namespace = 'ci-metadata-%s' % self._uniquifier()
        self.namespace_key = self._uniquifier()
        self.test_client = self._make_namespace(
            self.namespace, self.namespace_key)
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)

    def tearDown(self):
        super(TestInstanceMetadata, self).tearDown()
        for inst in self.test_client.get_instances():
            self.test_client.delete_instance(inst['uuid'])
        for net in self.test_client.get_networks():
            self.test_client.delete_network(net['uuid'])
        self._remove_namespace(self.namespace)

    def test_simple(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])

        self.assertEqual(
            {}, self.test_client.get_instance_metadata(inst['uuid']))
        self.test_client.set_instance_metadata_item(
            inst['uuid'], 'foo', 'bar')
        self.assertEqual({'foo': 'bar'},
                         self.test_client.get_instance_metadata(inst['uuid']))
