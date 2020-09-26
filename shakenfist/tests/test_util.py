import mock
import testtools


from shakenfist import util
from shakenfist import config


class UtilTestCase(testtools.TestCase):
    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_NODE_IP': '1.1.1.1',
                      'SHAKENFIST_NETWORK_NODE_IP': '1.1.1.1'})
    def test_is_network_node_yes(self):
        config.parsed.parse()
        self.assertTrue(util.is_network_node())

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_NODE_IP': '1.1.1.1',
                      'SHAKENFIST_NETWORK_NODE_IP': '1.1.1.2'})
    def test_is_network_node_no(self):
        config.parsed.parse()
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
