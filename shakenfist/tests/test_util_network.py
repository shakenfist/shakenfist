from unittest import mock

from shakenfist.config import BaseSettings
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.tests import base
from shakenfist.util import network as util_network


class FakeConfigNetworkNode(BaseSettings):
    NODE_MESH_IP: str = '1.1.1.1'
    NETWORK_NODE_IP: str = '1.1.1.1'


class FakeConfigNormalNode(BaseSettings):
    NODE_MESH_IP: str = '1.1.1.1'
    NETWORK_NODE_IP: str = '1.1.1.2'


class UtilTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.util.concurrency.execute',
                return_value=(None, 'Device "banana0" does not exist.'))
    def test_check_for_interface_missing_interface(self, mock_execute):
        found = util_network.check_for_interface('banana0')
        self.assertEqual(False, found)
        mock_execute.assert_called_with(
            'ip -pretty -json link show banana0',
            check_exit_code=[0, 1], netns=None, suppress_command_logging=True)

    @mock.patch(
        'shakenfist.util.concurrency.execute',
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
            'ip -pretty -json link show eth0',
            check_exit_code=[0, 1], netns=None, suppress_command_logging=True)

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        return_value=(
            """[ {},{
        "ifindex": 19,
        "ifname": "vxlan-000123",
        "link_type": "ether",
        "master": "br-vxlan-000123"
    },{
        "ifindex": 22,
        "ifname": "vnet7",
        "link_type": "ether",
        "master": "br-vxlan-000123"
    } ]""", ''))
    def test_get_bridge_members(self, mock_execute):
        members = util_network.get_bridge_members('br-vxlan-000123')
        self.assertEqual(['vxlan-000123', 'vnet7'], members)
        mock_execute.assert_called_with(
            'ip -pretty -json link show master br-vxlan-000123',
            check_exit_code=[0, 1], netns=None, suppress_command_logging=True)

    @mock.patch('shakenfist.util.concurrency.execute',
                return_value=('[ ]', ''))
    def test_get_bridge_members_empty_bridge(self, mock_execute):
        self.assertEqual(
            [], util_network.get_bridge_members('br-vxlan-000123'))

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        side_effect=ProcessExecutionError(
            stdout='',
            stderr=('Error: argument "br-vxlan-000123" is wrong: Device '
                    'does not exist\n'),
            exit_code=255,
            cmd='ip -pretty -json link show master br-vxlan-000123'))
    def test_get_bridge_members_missing_bridge(self, mock_execute):
        """A bridge which is not there has nothing attached to it. The
        stray vxlan reaper asks about bridges which may well already be
        gone -- indeed that is the common shape, because hypervisor
        teardown deletes the bridge before the vxlan interface -- so
        this must not be an error.

        The mocked failure is exactly what iproute2 produces here, which
        is *not* the message ``ip link show <dev>`` produces for the same
        condition: a missing filter argument exits 255 with 'Error:
        argument ... is wrong: Device does not exist', not 1 with
        'Device "..." does not exist.'.
        """
        self.assertEqual(
            [], util_network.get_bridge_members('br-vxlan-000123'))

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        return_value=(None, 'Device "br-vxlan-000123" does not exist.'))
    def test_get_bridge_members_missing_bridge_subject_wording(
            self, mock_execute):
        """Older iproute2 releases word a missing device the same way
        whether it is the subject of the command or a filter argument to
        it. Accept that form too."""
        self.assertEqual(
            [], util_network.get_bridge_members('br-vxlan-000123'))

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        side_effect=ProcessExecutionError(
            stdout='', stderr='RTNETLINK answers: Operation not permitted',
            exit_code=255,
            cmd='ip -pretty -json link show master br-vxlan-000123'))
    def test_get_bridge_members_error_raises(self, mock_execute):
        """Anything other than a missing bridge must raise. A caller
        about to delete a device has to be able to tell "no members"
        from "could not ask" -- and 255 is iproute2's catch-all failure
        code, so exit status alone cannot make that distinction."""
        self.assertRaises(
            ProcessExecutionError, util_network.get_bridge_members,
            'br-vxlan-000123')

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        return_value=(None, 'Device "banana0" does not exist.'))
    def test_get_interface_addresses_missing_interface(self, mock_execute):
        found = list(util_network.get_interface_addresses('eth0'))
        self.assertEqual([], found)
        mock_execute.assert_called_with(
            'ip -pretty -json addr show eth0',
            check_exit_code=[0, 1], netns=None)

    @mock.patch(
        'shakenfist.util.concurrency.execute',
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
            'ip -pretty -json addr show eth0',
            check_exit_code=[0, 1], netns=None)

    @mock.patch(
        'shakenfist.util.concurrency.execute',
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
            'eth0', netns='bananarama'))
        self.assertEqual(['192.168.1.28'], found)
        mock_execute.assert_called_with(
            'ip -pretty -json addr show eth0',
            check_exit_code=[0, 1], netns='bananarama')

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        return_value=('default via 192.168.1.247 dev enx8cae4cf14c31 proto dhcp metric 100\n'
                      'default via 192.168.1.247 dev wlp2s0 proto dhcp metric 600\n',
                      ''))
    def test_get_default_routes(self, mock_execute):
        found = util_network.get_default_routes('mynamespace')
        self.assertEqual(['192.168.1.247'], found)
        mock_execute.assert_called_with(
            'ip route list default', netns='mynamespace')

    @mock.patch('shakenfist.util.concurrency.execute')
    def test_create_interface_bridge(self, mock_execute):
        util_network.create_interface('eth0', 'bridge', '')
        mock_execute.assert_called_with(
            'ip link add eth0 mtu 7950 type bridge ')

    @mock.patch('shakenfist.util.concurrency.execute')
    def test_create_interface_bridge_truncates(self, mock_execute):
        util_network.create_interface(
            'eth0rjkghjkfshgjksfhdjkghfdsjkg', 'bridge', '')
        mock_execute.assert_called_with(
            'ip link add eth0rjkghjkfshg mtu 7950 type bridge ')

    @mock.patch('shakenfist.util.concurrency.execute')
    def test_create_interface_vxlan(self, mock_execute):
        util_network.create_interface(
            'vxlan1', 'vxlan', 'id 123 dev eth0 dstport 0')
        mock_execute.assert_called_with(
            'ip link add vxlan1 mtu 7950 type vxlan id 123 dev eth0 dstport 0')

    @mock.patch('shakenfist.util.concurrency.execute')
    def test_create_interface_veth(self, mock_execute):
        util_network.create_interface(
            'veth-foo-o', 'veth', 'peer name veth-foo-i')
        mock_execute.assert_called_with(
            'ip link add veth-foo-o mtu 7950 type veth peer name veth-foo-i')

    def test_random_macaddr(self):
        self.assertTrue(util_network.random_macaddr().startswith('02:00:00'))


class DiscoverMeshFloodIPsTestCase(base.ShakenFistTestCase):
    """Tests for ``discover_mesh_flood_ips``, the read-only side of the
    VXLAN mesh used by the ``Network.is_mesh_okay`` audit."""

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        return_value=(
            '00:00:00:00:00:00 dst 192.168.21.51 self permanent\n'
            '00:00:00:00:00:00 dst 192.168.21.56 self permanent\n'
            '02:00:00:b4:f4:b4 dst 192.168.21.56 self \n'
            '33:33:00:00:00:01 self permanent\n',
            ''))
    def test_parses_flood_entries_only(self, mock_execute):
        found = util_network.discover_mesh_flood_ips('vxlan-453a3e')
        self.assertEqual({'192.168.21.51', '192.168.21.56'}, found)
        mock_execute.assert_called_with(
            'bridge fdb show brport vxlan-453a3e',
            suppress_command_logging=True)

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        return_value=('', ''))
    def test_empty_fdb_returns_empty_set(self, mock_execute):
        found = util_network.discover_mesh_flood_ips('vxlan-000001')
        self.assertEqual(set(), found)

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        side_effect=ProcessExecutionError(
            stderr='Cannot find device "vxlan-000002"', exit_code=255,
            cmd='bridge fdb show brport vxlan-000002'))
    def test_missing_interface_returns_none(self, mock_execute):
        found = util_network.discover_mesh_flood_ips('vxlan-000002')
        self.assertIsNone(found)
