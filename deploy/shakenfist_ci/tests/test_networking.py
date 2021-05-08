import time

from shakenfist_client import apiclient

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
        self._await_network_ready(self.net_one['uuid'])
        self._await_network_ready(self.net_two['uuid'])
        self._await_network_ready(self.net_three['uuid'])
        self._await_network_ready(self.net_four['uuid'])

    def test_network_validity(self):
        self.assertRaises(apiclient.APIException, self.test_client.allocate_network,
                          '192.168.242.2', True, True, '%s-validity1' % self.namespace)
        self.assertRaises(apiclient.APIException, self.test_client.allocate_network,
                          '192.168.242.2/32', True, True, '%s-validity2' % self.namespace)
        self.assertRaises(apiclient.APIException, self.test_client.allocate_network,
                          '192.168.242.0/30', True, True, '%s-validity3' % self.namespace)
        n = self.test_client.allocate_network(
            '192.168.10.0/29', True, True, '%s-validity2' % self.namespace)
        self.test_client.delete_network(n['uuid'])

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

        # We need to refresh our view of the instances, as it might have
        # changed as they started up
        inst1 = self.test_client.get_instance(inst1['uuid'])
        inst2 = self.test_client.get_instance(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

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

        # We need to refresh our view of the instances, as it might have
        # changed as they started up
        inst1 = self.test_client.get_instance(inst1['uuid'])
        inst2 = self.test_client.get_instance(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

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

        # We need to refresh our view of the instances, as it might have
        # changed as they started up
        inst1 = self.test_client.get_instance(inst1['uuid'])
        inst2 = self.test_client.get_instance(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

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

        while inst['state'] not in ['created', 'error']:
            time.sleep(1)
            inst = self.test_client.get_instance(inst['uuid'])

        if inst['state'] != 'created':
            self.fail('Instance is not in created state: %s' % inst)

        nics = self.test_client.get_instance_interfaces(inst['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        ips = []
        for nic in nics:
            ips.append(nic['ipv4'])

        self.assertEqual(['192.168.10.56'], ips)

    def test_specific_ip_request_invalid(self):
        self.assertRaises(
            apiclient.RequestMalformedException,
            self.test_client.create_instance,
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

    def test_specific_macaddress_request(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_four['uuid'],
                    'macaddress': '04:ed:33:c0:2e:6c'
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        while inst['state'] not in ['created', 'error']:
            time.sleep(1)
            inst = self.test_client.get_instance(inst['uuid'])

        # Sometimes the console port is missing. Explicitly check for
        # that.
        if 'console_port' not in inst:
            self.fail('Missing console port: %s' % inst)

        console = base.LoggingSocket(inst['node'], inst['console_port'])
        out = console.execute('ip link')
        if not out.find('04:ed:33:c0:2e:6c'):
            self.fail('Requested macaddress not used!\n\n%s' % out)

    def test_interface_delete(self):
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

        self.assertIsNotNone(inst1['uuid'])
        self._await_login_prompt(inst1['uuid'])

        # We need to refresh our view of the instances, as it might have
        # changed as they started up
        inst1 = self.test_client.get_instance(inst1['uuid'])

        nics = self.test_client.get_instance_interfaces(inst1['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        # Delete the instance
        self.test_client.delete_instance(inst1['uuid'])

        # Allow some time for propogation
        time.sleep(10)

        # Ensure that interfaces are now marked as deleted
        for iface in nics:
            self.assertEqual(
                'deleted', self.test_client.get_interface(iface['uuid'])['state'])
