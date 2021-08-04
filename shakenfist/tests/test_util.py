import mock

from shakenfist.config import SFConfig
from shakenfist import util
from shakenfist.tests import test_shakenfist


net_node_conf = SFConfig(NODE_MESH_IP='1.1.1.1', NETWORK_NODE_IP='1.1.1.1')
not_net_node_conf = SFConfig(
    NODE_MESH_IP='1.1.1.1', NETWORK_NODE_IP='1.1.1.2')


class UtilTestCase(test_shakenfist.ShakenFistTestCase):
    @mock.patch('shakenfist.util.config', net_node_conf)
    def test_is_network_node_yes(self):
        self.assertTrue(util.is_network_node())

    @mock.patch('shakenfist.util.config', not_net_node_conf)
    def test_is_network_node_no(self):
        self.assertFalse(util.is_network_node())

    @mock.patch('shakenfist.util.execute',
                return_value=(None, 'Device "banana0" does not exist.'))
    def test_check_for_interface_missing_interface(self, mock_execute):
        found = util.check_for_interface('banana0')
        self.assertEqual(False, found)
        mock_execute.assert_called_with(None, 'ip link show banana0',
                                        check_exit_code=[0, 1])

    @mock.patch(
        'shakenfist.util.execute',
        return_value=(
            'eth0: flags=4099<UP,BROADCAST,MULTICAST>  mtu 1500\n'
            'inet 172.17.0.1  netmask 255.255.0.0  broadcast 172.17.255.255\n'
            'ether 02:42:0b:c9:2b:03  txqueuelen 0  (Ethernet)\n'
            'RX packets 0  bytes 0 (0.0 B)\n'
            'RX errors 0  dropped 0  overruns 0  frame 0\n'
            'TX packets 0  bytes 0 (0.0 B)\n'
            'TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0\n',
            ''))
    def test_check_for_interface_present_interface(self, mock_execute):
        found = util.check_for_interface('eth0')
        self.assertEqual(True, found)
        mock_execute.assert_called_with(None, 'ip link show eth0',
                                        check_exit_code=[0, 1])

    @mock.patch(
        'shakenfist.util.execute',
        return_value=(None, 'Device "banana0" does not exist.'))
    def test_get_interface_addresses_missing_interface(self, mock_execute):
        found = list(util.get_interface_addresses(None, 'eth0'))
        self.assertEqual([], found)
        mock_execute.assert_called_with(None, 'ip addr show eth0',
                                        check_exit_code=[0, 1])

    @mock.patch(
        'shakenfist.util.execute',
        return_value=(
            '19: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000\n'
            '    link/ether 8c:ae:4c:f1:4c:31 brd ff:ff:ff:ff:ff:ff\n'
            '    inet 192.168.1.28/24 brd 192.168.1.255 scope global dynamic noprefixroute eth0\n'
            '       valid_lft 2563sec preferred_lft 2563sec\n'
            '    inet6 fe80::7323:4d91:332c:6a76/64 scope link noprefixroute\n'
            '       valid_lft forever preferred_lft forever\n',
            ''))
    def test_get_interface_addresses_no_namespace(self, mock_execute):
        found = list(util.get_interface_addresses(None, 'eth0'))
        self.assertEqual(['192.168.1.28'], found)
        mock_execute.assert_called_with(None, 'ip addr show eth0',
                                        check_exit_code=[0, 1])

    @mock.patch(
        'shakenfist.util.execute',
        return_value=(
            '19: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000\n'
            '    link/ether 8c:ae:4c:f1:4c:31 brd ff:ff:ff:ff:ff:ff\n'
            '    inet 192.168.1.28/24 brd 192.168.1.255 scope global dynamic noprefixroute eth0\n'
            '       valid_lft 2563sec preferred_lft 2563sec\n'
            '    inet6 fe80::7323:4d91:332c:6a76/64 scope link noprefixroute\n'
            '       valid_lft forever preferred_lft forever\n',
            ''))
    def test_get_interface_addresses_namespace(self, mock_execute):
        found = list(util.get_interface_addresses('bananarama', 'eth0'))
        self.assertEqual(['192.168.1.28'], found)
        mock_execute.assert_called_with(
            None, 'ip netns exec bananarama ip addr show eth0',
            check_exit_code=[0, 1])

    @mock.patch(
        'shakenfist.util.execute',
        return_value=('default via 192.168.1.247 dev enx8cae4cf14c31 proto dhcp metric 100\n'
                      'default via 192.168.1.247 dev wlp2s0 proto dhcp metric 600\n',
                      ''))
    def test_get_default_routes(self, mock_execute):
        found = util.get_default_routes('mynamespace')
        self.assertEqual(['192.168.1.247'], found)
        mock_execute.assert_called_with(
            None, 'ip netns exec mynamespace ip route list default')

    @mock.patch('shakenfist.util.execute')
    def test_create_interface_bridge(self, mock_execute):
        util.create_interface('eth0', 'bridge', '')
        mock_execute.assert_called_with(None, 'ip link add eth0 type bridge ')

    @mock.patch('shakenfist.util.execute')
    def test_create_interface_bridge_truncates(self, mock_execute):
        util.create_interface('eth0rjkghjkfshgjksfhdjkghfdsjkg', 'bridge', '')
        mock_execute.assert_called_with(
            None, 'ip link add eth0rjkghjkfshg type bridge ')

    @mock.patch('shakenfist.util.execute')
    def test_create_interface_vxlan(self, mock_execute):
        util.create_interface('vxlan1', 'vxlan', 'id 123 dev eth0 dstport 0')
        mock_execute.assert_called_with(
            None, 'ip link add vxlan1 type vxlan id 123 dev eth0 dstport 0')

    @mock.patch('shakenfist.util.execute')
    def test_create_interface_veth(self, mock_execute):
        util.create_interface('veth-foo-o', 'veth', 'peer name veth-foo-i')
        mock_execute.assert_called_with(
            None, 'ip link add veth-foo-o type veth peer name veth-foo-i')

    @mock.patch(
        'shakenfist.util.execute',
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

    def test_random_macaddr(self):
        self.assertTrue(util.random_macaddr().startswith('02:00:00'))
