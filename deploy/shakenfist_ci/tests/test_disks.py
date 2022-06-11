from shakenfist_ci import base


class TestDisks(base.BaseNamespacedTestCase):
    """Make sure instances boot under various configurations."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'disks'
        super(TestDisks, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestDisks, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_boot_nvme(self):
        self.skip('This test is flakey in CI for reasons I do not understand.')
        inst = self.test_client.create_instance(
            'test-cirros-boot-nvme', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/ubuntu-2004',
                    'type': 'disk',
                    'bus': 'nvme'
                }
            ], None, None, side_channels=['sf-agent'])

        self._await_instance_ready(inst['uuid'])
        inst = self.test_client.get_instance(inst['uuid'])
        self.assertNotIn(inst['agent_system_boot_time'], [None, 0])

        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)
