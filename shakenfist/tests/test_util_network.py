import mock

from shakenfist.config import BaseSettings
from shakenfist.tests import base
from shakenfist.util import network as util_network


class FakeConfigNetworkNode(BaseSettings):
    NODE_MESH_IP: str = '1.1.1.1'
    NETWORK_NODE_IP: str = '1.1.1.1'


class FakeConfigNormalNode(BaseSettings):
    NODE_MESH_IP: str = '1.1.1.1'
    NETWORK_NODE_IP: str = '1.1.1.2'


class UtilTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.util.process.execute',
                return_value=(None, 'Device "banana0" does not exist.'))
    def test_check_for_interface_missing_interface(self, mock_execute):
        found = util_network.check_for_interface('banana0')
        self.assertEqual(False, found)
        mock_execute.assert_called_with(
            None, 'ip -pretty -json link show banana0',
            check_exit_code=[0, 1], namespace=None)

    @mock.patch(
        'shakenfist.util.process.execute',
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
        found = util_network.check_for_interface('eth0')
        self.assertEqual(True, found)
        mock_execute.assert_called_with(
            None, 'ip -pretty -json link show eth0',
            check_exit_code=[0, 1], namespace=None)

    @mock.patch(
        'shakenfist.util.process.execute',
        return_value=(None, 'Device "banana0" does not exist.'))
    def test_get_interface_addresses_missing_interface(self, mock_execute):
        found = list(util_network.get_interface_addresses('eth0'))
        self.assertEqual([], found)
        mock_execute.assert_called_with(
            None, 'ip -pretty -json addr show eth0',
            check_exit_code=[0, 1], namespace=None)

    @mock.patch(
        'shakenfist.util.process.execute',
        return_value=(
            """[ {},{
        "ifindex": 19,
        "ifname": "eth0",
        "flags": [ "BROADCAST","MULTICAST","UP","LOWER_UP" ],
        "mtu": 1500,
        "qdisc": "fq_codel",
        "operstate": "UP",
        "group": "default",
        "txqlen": 1000,
        "link_type": "ether",
        "address": "8c:ae:4c:f1:4c:31",
        "broadcast": "ff:ff:ff:ff:ff:ff",
        "addr_info": [ {
                "family": "inet",
                "local": "192.168.1.28",
                "prefixlen": 24,
                "broadcast": "192.168.1.255",
                "scope": "global",
                "dynamic": true,
                "noprefixroute": true,
                "label": "enp0s31f6",
                "valid_life_time": 3049,
                "preferred_life_time": 3049
            } ]
    },{},{},{} ]""", ''))
    def test_get_interface_addresses_no_namespace(self, mock_execute):
        found = list(util_network.get_interface_addresses('eth0'))
        self.assertEqual(['192.168.1.28'], found)
        mock_execute.assert_called_with(
            None, 'ip -pretty -json addr show eth0',
            check_exit_code=[0, 1], namespace=None)

    @mock.patch(
        'shakenfist.util.process.execute',
        return_value=(
            """[ {},{
        "ifindex": 19,
        "ifname": "eth0",
        "flags": [ "BROADCAST","MULTICAST","UP","LOWER_UP" ],
        "mtu": 1500,
        "qdisc": "fq_codel",
        "operstate": "UP",
        "group": "default",
        "txqlen": 1000,
        "link_type": "ether",
        "address": "8c:ae:4c:f1:4c:31",
        "broadcast": "ff:ff:ff:ff:ff:ff",
        "addr_info": [ {
                "family": "inet",
                "local": "192.168.1.28",
                "prefixlen": 24,
                "broadcast": "192.168.1.255",
                "scope": "global",
                "dynamic": true,
                "noprefixroute": true,
                "label": "enp0s31f6",
                "valid_life_time": 3049,
                "preferred_life_time": 3049
            } ]
    },{},{},{} ]""", ''))
    def test_get_interface_addresses_namespace(self, mock_execute):
        found = list(util_network.get_interface_addresses(
            'eth0', namespace='bananarama'))
        self.assertEqual(['192.168.1.28'], found)
        mock_execute.assert_called_with(
            None, 'ip -pretty -json addr show eth0',
            check_exit_code=[0, 1], namespace='bananarama')

    @mock.patch(
        'shakenfist.util.process.execute',
        return_value=('default via 192.168.1.247 dev enx8cae4cf14c31 proto dhcp metric 100\n'
                      'default via 192.168.1.247 dev wlp2s0 proto dhcp metric 600\n',
                      ''))
    def test_get_default_routes(self, mock_execute):
        found = util_network.get_default_routes('mynamespace')
        self.assertEqual(['192.168.1.247'], found)
        mock_execute.assert_called_with(
            None, 'ip route list default', namespace='mynamespace')

    @mock.patch('shakenfist.util.process.execute')
    def test_create_interface_bridge(self, mock_execute):
        util_network.create_interface('eth0', 'bridge', '')
        mock_execute.assert_called_with(
            None, 'ip link add eth0 mtu 7950 type bridge ')

    @mock.patch('shakenfist.util.process.execute')
    def test_create_interface_bridge_truncates(self, mock_execute):
        util_network.create_interface(
            'eth0rjkghjkfshgjksfhdjkghfdsjkg', 'bridge', '')
        mock_execute.assert_called_with(
            None, 'ip link add eth0rjkghjkfshg mtu 7950 type bridge ')

    @mock.patch('shakenfist.util.process.execute')
    def test_create_interface_vxlan(self, mock_execute):
        util_network.create_interface(
            'vxlan1', 'vxlan', 'id 123 dev eth0 dstport 0')
        mock_execute.assert_called_with(
            None,
            'ip link add vxlan1 mtu 7950 type vxlan id 123 dev eth0 dstport 0')

    @mock.patch('shakenfist.util.process.execute')
    def test_create_interface_veth(self, mock_execute):
        util_network.create_interface(
            'veth-foo-o', 'veth', 'peer name veth-foo-i')
        mock_execute.assert_called_with(
            None,
            'ip link add veth-foo-o mtu 7950 type veth peer name veth-foo-i')

    @mock.patch(
        'shakenfist.util.process.execute',
        return_value=(
            'Chain POSTROUTING (policy ACCEPT 199 packets, 18189 bytes)\n'
            ' pkts bytes target     prot opt in     out     source               destination\n'
            '   23  1736 MASQUERADE  all  --  *     ens4    192.168.242.0/24     0.0.0.0/0\n',
            ''))
    def test_nat_rules_for_ipblock(self, mock_execute):
        found = util_network.nat_rules_for_ipblock('192.168.242.0')
        self.assertEqual(True, found)

        found = util_network.nat_rules_for_ipblock('10.0.0.0')
        self.assertEqual(False, found)

    def test_random_macaddr(self):
        self.assertTrue(util_network.random_macaddr().startswith('02:00:00'))
