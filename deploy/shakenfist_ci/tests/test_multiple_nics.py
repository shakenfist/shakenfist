import base64

from shakenfist_ci import base


class TestMultipleNics(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'multinic'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net_one = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace)
        self.net_two = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net-two' % self.namespace)
        self._await_networks_ready([self.net_one['uuid'],
                                    self.net_two['uuid']])

    def test_simple(self):
        self.skip('systemctl says degraded via sf-agent, requires debugging')

        ud = """#!/bin/sh
sudo echo ''                      >  /etc/network/interfaces
sudo echo 'auto eth0'             >> /etc/network/interfaces
sudo echo 'iface eth0 inet dhcp'  >> /etc/network/interfaces
sudo echo 'auto eth1'             >> /etc/network/interfaces
sudo echo 'iface eth1 inet dhcp'  >> /etc/network/interfaces
sudo /etc/init.d/S40network restart"""

        inst = self.test_client.create_instance(
            'test-multiple-nics', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                },
                {
                    'network_uuid': self.net_two['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, str(base64.b64encode(ud.encode('utf-8')), 'utf-8'))

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        ifaces = self.test_client.get_instance_interfaces(inst['uuid'])
        self.assertEqual(2, len(ifaces))
        for iface in ifaces:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        for iface in ifaces:
            self._test_ping(
                inst['uuid'], iface['network_uuid'], iface['ipv4'], True)
