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
            'eth0: flags=4099<UP,BROADCAST,MULTICAST>  mtu 1500'
            'inet 172.17.0.1  netmask 255.255.0.0  broadcast 172.17.255.255'
            'ether 02:42:0b:c9:2b:03  txqueuelen 0  (Ethernet)'
            'RX packets 0  bytes 0 (0.0 B)'
            'RX errors 0  dropped 0  overruns 0  frame 0'
            'TX packets 0  bytes 0 (0.0 B)'
            'TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0',
            ''))
    def test_check_for_interface(self, mock_execute):
        found = util.check_for_interface('eth0')
        self.assertEqual(True, found)

    def test_get_network_fundamentals(self):
        router, dhcpserver, dhcpstart = util.get_network_fundamentals(
            '192.168.0.0/24')
        self.assertEqual('192.168.0.1', str(router))
        self.assertEqual('192.168.0.2', str(dhcpserver))
        self.assertEqual('192.168.0.3', str(dhcpstart))

    def test_get_random_ip(self):
        ip = util.get_random_ip('192.168.0.0/24')
        router, dhcpserver, _ = util.get_network_fundamentals(
            '192.168.0.0/24')

        self.assertIsNot(str(router), ip)
        self.assertIsNot(str(dhcpserver), ip)
        self.assertTrue(ipaddress.IPv4Address(ip) in
                        ipaddress.ip_network('192.168.0.0/24'))
