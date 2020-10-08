import time

from shakenfist_ci import base


class TestStateChanges(base.BaseTestCase):
    def setUp(self):
        super(TestStateChanges, self).setUp()

        self.namespace = 'ci-statechanges-%s' % self._uniquifier()
        self.namespace_key = self._uniquifier()
        self.test_client = self._make_namespace(
            self.namespace, self.namespace_key)
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace)

    def tearDown(self):
        super(TestStateChanges, self).tearDown()
        for inst in self.test_client.get_instances():
            self.test_client.delete_instance(inst['uuid'])
        for net in self.test_client.get_networks():
            self.test_client.delete_network(net['uuid'])
        self._remove_namespace(self.namespace)

    def test_lifecycle_events(self):
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
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']

        # We need to start a second instance on the same node / network so that
        # the network doesn't get torn down during any of the tests.
        self.test_client.create_instance(
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
            ], None, None, force_placement=inst['node'])

        # Wait for our test instance to boot
        self.assertIsNotNone(inst['uuid'])
        last_prompt = self._await_login_prompt(inst['uuid'])
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

        # Soft reboot
        self.test_client.reboot_instance(inst['uuid'])
        time.sleep(20)
        this_prompt = self._await_login_prompt(inst['uuid'], after=last_prompt)
        self.assertNotEqual(last_prompt, this_prompt)
        last_prompt = this_prompt
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

        # Hard reboot
        self.test_client.reboot_instance(inst['uuid'], hard=True)
        time.sleep(20)
        this_prompt = self._await_login_prompt(inst['uuid'], after=last_prompt)
        self.assertNotEqual(last_prompt, this_prompt)
        last_prompt = this_prompt
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

        # Power off
        self.test_client.power_off_instance(inst['uuid'])
        time.sleep(10)
        self._test_ping(inst['uuid'], self.net['uuid'], ip, False)

        # Power on
        self.test_client.power_on_instance(inst['uuid'])
        time.sleep(1)
        this_prompt = self._await_login_prompt(inst['uuid'], after=last_prompt)
        self.assertNotEqual(last_prompt, this_prompt)
        last_prompt = this_prompt
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

        # Pause
        self.test_client.pause_instance(inst['uuid'])
        time.sleep(10)
        self._test_ping(inst['uuid'], self.net['uuid'], ip, False)

        # Unpause
        self.test_client.unpause_instance(inst['uuid'])
        time.sleep(1)
        this_prompt = self._await_login_prompt(inst['uuid'], after=last_prompt)
        self.assertNotEqual(last_prompt, this_prompt)
        last_prompt = this_prompt
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)
