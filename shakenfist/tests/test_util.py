import ipaddress
import mock
import testtools


from shakenfist import util


class UtilTestCase(testtools.TestCase):
    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, 'Device "banana0" does not exist.'))
    def test_check_for_interface(self, mock_execute):
        found = util.check_for_interface('banana0')
        self.assertEqual(False, found)

    @mock.patch(
        'oslo_concurrency.processutils.execute',
        return_value=(
            'eth0: flags=4099<UP,BROADCAST,MULTICAST>  mtu 1500\n'
            'inet 172.17.0.1  netmask 255.255.0.0  broadcast 172.17.255.255\n'
            'ether 02:42:0b:c9:2b:03  txqueuelen 0  (Ethernet)\n'
            'RX packets 0  bytes 0 (0.0 B)\n'
            'RX errors 0  dropped 0  overruns 0  frame 0\n'
            'TX packets 0  bytes 0 (0.0 B)\n'
            'TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0\n',
            ''))
    def test_check_for_interface(self, mock_execute):
        found = util.check_for_interface('eth0')
        self.assertEqual(True, found)

    @mock.patch(
        'oslo_concurrency.processutils.execute',
        return_value=(
            'Chain POSTROUTING (policy ACCEPT 199 packets, 18189 bytes)\n'
            ' pkts bytes target     prot opt in     out     source               destination\n'
            '   23  1736 MASQUERADE  all  --  *     ens4    192.168.242.0/24     0.0.0.0/0\n',
            ''))
    def test_nat_rules_for_ipblock(self, mock_execute):
        found = util.nat_rules_for_ipblock('192.168.242.0')
        self.assertEqual(True, found)

        found = util.nat_rules_for_ipblock('10.0.0.0')
        self.assertEqual(False, found)

    def test_get_network_fundamentals(self):
        router, dhcpstart = util.get_network_fundamentals(
            '192.168.0.0/24')
        self.assertEqual('192.168.0.1', str(router))
        self.assertEqual('192.168.0.2', str(dhcpstart))

    def test_get_random_ip(self):
        ip = util.get_random_ip('192.168.0.0/24')
        router, _ = util.get_network_fundamentals(
            '192.168.0.0/24')

        self.assertIsNot(str(router), ip)
        self.assertTrue(ipaddress.IPv4Address(ip) in
                        ipaddress.ip_network('192.168.0.0/24'))
