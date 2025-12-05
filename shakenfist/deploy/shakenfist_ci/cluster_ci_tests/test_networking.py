from shakenfist_ci import base
from shakenfist_client import apiclient


class TestNetworking(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'net'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net_one = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace,
            provide_dns=True)
        self.net_two = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net-two' % self.namespace)
        self.net_three = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-three' % self.namespace)
        self.net_four = self.test_client.allocate_network(
            '192.168.10.0/24', True, True, '%s-net-four' % self.namespace)
        self._await_networks_ready([self.net_one['uuid'],
                                    self.net_two['uuid'],
                                    self.net_three['uuid'],
                                    self.net_four['uuid']])

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
            'test-networks-separate-1', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        inst2 = self.test_client.create_instance(
            'test-networks-separate-1', 1, 1024,
            [
                {
                    'network_uuid': self.net_two['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst2['uuid'])

        self._await_instance_ready(inst1['uuid'])
        self._await_instance_ready(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        results = self._await_command(inst1['uuid'], 'ping -c 3 %s' % nics[0]['ipv4'])
        self.assertEqual(1, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue(' 100% packet' in results['stdout'])

    def test_overlapping_virtual_networks_are_separate(self):
        inst1 = self.test_client.create_instance(
            'test-overlap-cidr-1', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self._emit_tracing_event({
            'msg': (f'inst1 is uuid {inst1["uuid"]} on network '
                    f'{self.net_one["uuid"]}')
        }
        )

        inst2 = self.test_client.create_instance(
            'test-overlap-cidr-2', 1, 1024,
            [
                {
                    'network_uuid': self.net_three['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self._emit_tracing_event({
            'msg': (f'inst2 is uuid {inst2["uuid"]} on network '
                    f'{self.net_three["uuid"]}')
        }
        )

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst2['uuid'])

        self._await_instance_ready(inst1['uuid'])
        self._await_instance_ready(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual(
                'created',
                iface['state'],
                'Interface %s is not in correct state' % iface['uuid'])

        results = self._await_command(
            inst1['uuid'], 'ping -c 3 %s' % nics[0]['ipv4'])
        self.assertEqual(
            1, results['return-code'], 'Incorrect return code: %s' % results)
        self.assertEqual('', results['stderr'])
        self.assertTrue(' 100% packet' in results['stdout'])

    def test_single_virtual_networks_work(self):
        inst1 = self.test_client.create_instance(
            'test-networks-1', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent2'])

        inst2 = self.test_client.create_instance(
            'test-networks-2', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent2'])

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst2['uuid'])

        self._await_instance_ready(inst1['uuid'])
        self._await_instance_ready(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        # Ping the other instance on this network
        results = self._await_command(inst1['uuid'], 'ping -c 3 %s' % nics[0]['ipv4'])
        self.assertEqual(0, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue(' 0% packet' in results['stdout'], results['stdout'])

        # Ping google (prove NAT works)
        results = self._await_command(inst1['uuid'], 'ping -c 3 8.8.8.8')
        self.assertEqual(0, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue(' 0% packet' in results['stdout'], results['stdout'])
