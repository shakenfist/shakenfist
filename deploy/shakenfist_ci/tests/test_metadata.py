from shakenfist_ci import base


class TestInstanceMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'metadata'
        super(TestInstanceMetadata, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestInstanceMetadata, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)

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
