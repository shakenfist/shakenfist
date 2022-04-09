from shakenfist_ci import base


class TestBoot(base.BaseNamespacedTestCase):
    """Make sure instances boot under various configurations."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'cirros'
        super(TestBoot, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestBoot, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_boot_no_network(self):
        """Check that instances without a network still boot.

        Once we had a bug that only stopped instance creation when no network
        was specified.
        """
        inst = self.test_client.create_instance(
            'test-boot-no-network', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])

        self._await_instance_ready(inst['uuid'])

        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)

    def test_boot_network(self):
        inst = self.test_client.create_instance(
            'test-boot-network', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])

        self._await_instance_ready(inst['uuid'])

        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)

    def test_boot_large_disk(self):
        inst = self.test_client.create_instance(
            'test-boot-large-disk', 1, 1024, None,
            [
                {
                    'size': 30,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])

        self._await_instance_ready(inst['uuid'])
