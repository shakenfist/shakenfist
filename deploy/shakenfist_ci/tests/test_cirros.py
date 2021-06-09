from shakenfist_ci import base


class TestCirros(base.BaseNamespacedTestCase):
    """Make sure instances boot under various configurations."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'cirros'
        super(TestCirros, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestCirros, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_network_ready(self.net['uuid'])

    def test_cirros_boot_no_network(self):
        """Check that instances without a network still boot.

        Once we had a bug that only stopped instance creation when no network
        was specified.
        """
        inst = self.test_client.create_instance(
            'test-cirros-boot-no-network', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self._await_login_prompt(inst['uuid'])

        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)

    def test_cirros_boot_network(self):
        inst = self.test_client.create_instance(
            'test-cirros-boot-network', 1, 1024,
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

        self._await_login_prompt(inst['uuid'])

        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)

    def test_cirros_boot_large_disk(self):
        inst = self.test_client.create_instance(
            'test-cirros-boot-large-disk', 1, 1024, None,
            [
                {
                    'size': 30,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self._await_login_prompt(inst['uuid'])
