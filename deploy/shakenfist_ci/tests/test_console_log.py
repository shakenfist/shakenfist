import time

from shakenfist_ci import base


class TestConsoleLog(base.BaseTestCase):
    def setUp(self):
        super(TestConsoleLog, self).setUp()

        self.namespace = 'ci-consolelog-%s' % self._uniquifier()
        self.namespace_key = self._uniquifier()
        self.test_client = self._make_namespace(
            self.namespace, self.namespace_key)
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace)

    def tearDown(self):
        super(TestConsoleLog, self).tearDown()
        for inst in self.test_client.get_instances():
            self.test_client.delete_instance(inst['uuid'])
        for net in self.test_client.get_networks():
            self.test_client.delete_network(net['uuid'])
        self._remove_namespace(self.namespace)

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
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for our test instance to boot
        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        # Get 2000 bytes of console log
        c = self.test_client.get_console_data(inst['uuid'], 2000)
        self.assertGreaterEqual(len(c), 2000)

        # Get 1000 bytes of console log
        c = self.test_client.get_console_data(inst['uuid'], 1000)
        self.assertGreaterEqual(len(c), 1000)

        # Get the default amount of the console log
        c = self.test_client.get_console_data(inst['uuid'])
        self.assertGreaterEqual(len(c), 10240)

        # Get all of the console log
        c = self.test_client.get_console_data(inst['uuid'], -1)
        self.assertGreaterEqual(len(c), 11000)

        # Check we handle non-numbers reasonably
        c = self.test_client.get_console_data(inst['uuid'], 'banana')
