from shakenfist_ci import base


class TestSerialConsole(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'serialconsole'
        super(TestSerialConsole, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestSerialConsole, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_serial_console(self):
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
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self.assertIsNotNone(inst['node'])

        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)
        self.assertTrue(console.execute('uptime').find('load average'))
