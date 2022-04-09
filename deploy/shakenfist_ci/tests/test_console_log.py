from shakenfist_client import apiclient
import time

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
            'test-console', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, base.load_userdata('console_scribbler'),
            side_channels=['sf-agent'])

        # Wait for our test instance to boot
        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])
        time.sleep(30)

        # Get 1000 bytes of console log
        c = self.test_client.get_console_data(inst['uuid'], 1000, decode=None)
        if len(c) < 1000:
            self.fail(
                'Console response was not 1000 characters (%d instead):\n\n%s'
                % (len(c), c))

        # Get 2000 bytes of console log
        c = self.test_client.get_console_data(inst['uuid'], 2000, decode=None)
        if len(c) < 2000:
            self.fail(
                'Console response was not 2000 characters (%d instead):\n\n%s'
                % (len(c), c))

        # Get the default amount of the console log
        c = self.test_client.get_console_data(inst['uuid'], decode=None)
        if len(c) < 10240:
            self.fail(
                'Console response was not 10240 characters (%d instead):\n\n%s'
                % (len(c), c))

        # Get all of the console log
        c = self.test_client.get_console_data(inst['uuid'], -1, decode=None)
        self.assertGreaterEqual(len(c), 11000)

        # Check we handle non-numbers reasonably
        self.assertRaises(
            apiclient.RequestMalformedException,
            self.test_client.get_console_data, inst['uuid'], 'banana')
