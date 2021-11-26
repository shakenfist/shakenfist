from shakenfist_client import apiclient

from shakenfist_ci import base


class TestConsoleLog(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'console'
        super(TestConsoleLog, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestConsoleLog, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_console_log(self):
        # Start our test instance
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for our test instance to boot
        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        # Get 1000 bytes of console log
        c = self.test_client.get_console_data(inst['uuid'], 1000)
        self.assertGreaterEqual(len(c), 1000)

        # Get 2000 bytes of console log
        c = self.test_client.get_console_data(inst['uuid'], 2000)
        self.assertGreaterEqual(len(c), 2000)

        # Get the default amount of the console log
        c = self.test_client.get_console_data(inst['uuid'])
        self.assertGreaterEqual(len(c), 10240)

        # Get all of the console log
        c = self.test_client.get_console_data(inst['uuid'], -1)
        self.assertGreaterEqual(len(c), 11000)

        # Check we handle non-numbers reasonably
        self.assertRaises(
            apiclient.RequestMalformedException,
            self.test_client.get_console_data, inst['uuid'], 'banana')
