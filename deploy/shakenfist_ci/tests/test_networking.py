from shakenfist_ci import base


class TestNetworking(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'net'
        super(TestNetworking, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestNetworking, self).setUp()
        self.net_one = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace)
        self.net_two = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net-two' % self.namespace)
        self.net_three = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-three' % self.namespace)
        self.net_four = self.test_client.allocate_network(
            '192.168.10.0/24', True, True, '%s-net-four' % self.namespace)

    def test_virtual_networks_are_separate(self):
        inst1 = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        inst2 = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_two['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst2['uuid'])

        self._await_login_prompt(inst1['uuid'])
        self._await_login_prompt(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])

        console = base.LoggingSocket(inst1['node'], inst1['console_port'])
        out = console.execute('ping -c 3 %s' % nics[0]['ipv4'])
        if not out.find('100% packet'):
            self.fail('Ping should have failed!\n\n%s' % out)

    def test_overlapping_virtual_networks_are_separate(self):
        inst1 = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        inst2 = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_three['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst2['uuid'])

        self._await_login_prompt(inst1['uuid'])
        self._await_login_prompt(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])

        console = base.LoggingSocket(inst1['node'], inst1['console_port'])
        out = console.execute('ping -c 3 %s' % nics[0]['ipv4'])
        if not out.find('100% packet'):
            self.fail('Ping should have failed!\n\n%s' % out)

    def test_single_virtual_networks_work(self):
        inst1 = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        inst2 = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst2['uuid'])

        self._await_login_prompt(inst1['uuid'])
        self._await_login_prompt(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])

        # Ping the other instance on this network
        console = base.LoggingSocket(inst1['node'], inst1['console_port'])
        out = console.execute('ping -c 3 %s' % nics[0]['ipv4'])
        if not out.find(' 0% packet'):
            self.fail('Ping should have worked!\n\n%s' % out)

        # Ping google (prove NAT works)
        out = console.execute('ping -c 3 8.8.8.8')
        if not out.find(' 0% packet'):
            self.fail('Ping should have worked!\n\n%s' % out)

    def test_specific_ip_request(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_four['uuid'],
                    'address': '192.168.10.56'
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
        self.assertIsNotNone(inst['node'])

        nics = self.test_client.get_instance_interfaces(inst['uuid'])
        ips = []
        for nic in nics:
            ips.append(nic['ipv4'])

        self.assertEqual(['192.168.10.56'], ips)

    def test_specific_ip_request_invalid(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_four['uuid'],
                    'address': '192.168.100.56'
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertEqual('error', inst['state'])
