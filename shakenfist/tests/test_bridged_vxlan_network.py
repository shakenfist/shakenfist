# Copyright 2019 Michael Still and contributors
"""Tests for `shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork`.

These tests construct a `BridgedVXLanNetwork` around a `mock.MagicMock`
network, patch the heavy collaborators (`util_concurrency`,
`Instance.from_db`, `Node.from_db`), and confirm that the lifted
``_apply_ensure_mesh`` body invokes the privexec call with the right
arguments and that event emission is gated on non-empty diff sets.
"""

from unittest import mock

from shakenfist.config import SFConfig
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.network import bridged_vxlan_network
from shakenfist.tests import base


def _make_network_mock(uuid='11111111-1111-4111-8111-111111111111', vxid=42,
                       networkinterfaces=()):
    """Build a mock object with the attributes _apply_ensure_mesh reads."""
    network = mock.MagicMock()
    network.uuid = uuid
    network.vxid = vxid
    network.networkinterfaces = list(networkinterfaces)
    # get_lock is used as a context manager
    network.get_lock.return_value.__enter__ = mock.Mock(return_value=None)
    network.get_lock.return_value.__exit__ = mock.Mock(return_value=False)
    return network


def _make_interface(instance_uuid):
    iface = mock.Mock()
    iface.instance_uuid = instance_uuid
    return iface


class BridgedVXLanNetworkApplyEnsureMeshTestCase(base.ShakenFistTestCase):

    def setUp(self):
        super().setUp()

        # Most tests assume we are *not* the network node, so the network
        # node's IP gets added to the mesh.
        fake_config = SFConfig(NODE_EGRESS_IP='10.0.0.2',
                               NODE_MESH_IP='10.0.0.2',
                               NETWORK_NODE_IP='10.0.0.1',
                               NODE_IS_NETWORK_NODE=False)
        self.config_patcher = mock.patch(
            'shakenfist.network.bridged_vxlan_network.config', fake_config)
        self.config_patcher.start()
        self.addCleanup(self.config_patcher.stop)

        self.mock_ensure_vxlan_mesh = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.ensure_vxlan_mesh').start()
        self.addCleanup(mock.patch.stopall)
        # Default: nothing added or removed.
        self.mock_ensure_vxlan_mesh.return_value = ([], [])

        self.mock_instance_from_db = mock.patch(
            'shakenfist.network.bridged_vxlan_network.instance.Instance.'
            'from_db').start()
        self.mock_node_from_db = mock.patch(
            'shakenfist.network.bridged_vxlan_network.Node.from_db').start()

    def test_floating_network_short_circuits(self):
        network = _make_network_mock(uuid=FLOATING_NETWORK_UUID)
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_ensure_mesh()

        # No host mutation, no event, no lock.
        self.mock_ensure_vxlan_mesh.assert_not_called()
        network.get_lock.assert_not_called()
        network.add_event.assert_not_called()

    def test_apply_ensure_mesh_collects_node_ips(self):
        iface_a = _make_interface('inst-a')
        iface_b = _make_interface('inst-b')
        # Duplicate iface for inst-a to confirm dedupe of instance_uuids.
        iface_a_dup = _make_interface('inst-a')

        network = _make_network_mock(
            uuid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            vxid=99,
            networkinterfaces=[iface_a, iface_b, iface_a_dup])
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        def instance_from_db(uuid):
            inst = mock.Mock()
            if uuid == 'inst-a':
                inst.placement = {'node': 'sf1.example.com'}
            elif uuid == 'inst-b':
                inst.placement = {'node': 'sf2.example.com'}
            else:
                return None
            return inst

        def node_from_db(fqdn):
            n = mock.Mock()
            if fqdn == 'sf1.example.com':
                n.ip = '10.0.0.3'
            elif fqdn == 'sf2.example.com':
                n.ip = '10.0.0.4'
            else:
                return None
            return n

        self.mock_instance_from_db.side_effect = instance_from_db
        self.mock_node_from_db.side_effect = node_from_db

        bvn._apply_ensure_mesh()

        # Lock acquired with the expected op name.
        network.get_lock.assert_called_once_with(
            op='Network ensure mesh', global_scope=False)

        # The expected node IP set:
        # - 10.0.0.1 (NETWORK_NODE_IP, since != NODE_MESH_IP)
        # - 10.0.0.3 (sf1)
        # - 10.0.0.4 (sf2)
        self.mock_ensure_vxlan_mesh.assert_called_once_with(
            'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            99,
            {'10.0.0.1', '10.0.0.3', '10.0.0.4'})

        # No events emitted because added/removed are both empty.
        network.add_event.assert_not_called()

    def test_apply_ensure_mesh_skips_missing_instance(self):
        iface = _make_interface('inst-missing')
        network = _make_network_mock(networkinterfaces=[iface])
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        self.mock_instance_from_db.return_value = None

        bvn._apply_ensure_mesh()

        # Only NETWORK_NODE_IP makes it onto the mesh.
        self.mock_ensure_vxlan_mesh.assert_called_once_with(
            network.uuid, network.vxid, {'10.0.0.1'})

    def test_apply_ensure_mesh_skips_unplaced_instance(self):
        iface = _make_interface('inst-unplaced')
        network = _make_network_mock(networkinterfaces=[iface])
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        unplaced = mock.Mock()
        unplaced.placement = None
        self.mock_instance_from_db.return_value = unplaced

        bvn._apply_ensure_mesh()

        self.mock_ensure_vxlan_mesh.assert_called_once_with(
            network.uuid, network.vxid, {'10.0.0.1'})

    def test_apply_ensure_mesh_emits_added_event(self):
        network = _make_network_mock()
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        self.mock_ensure_vxlan_mesh.return_value = (['10.0.0.7'], [])

        bvn._apply_ensure_mesh()

        network.add_event.assert_called_once_with(
            EVENT_TYPE_MUTATE, 'add mesh elements',
            extra={'added': ['10.0.0.7']})

    def test_apply_ensure_mesh_emits_removed_event(self):
        network = _make_network_mock()
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        self.mock_ensure_vxlan_mesh.return_value = ([], ['10.0.0.8'])

        bvn._apply_ensure_mesh()

        network.add_event.assert_called_once_with(
            EVENT_TYPE_MUTATE, 'remove mesh elements',
            extra={'removed': ['10.0.0.8']})

    def test_apply_ensure_mesh_emits_both_events(self):
        network = _make_network_mock()
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        self.mock_ensure_vxlan_mesh.return_value = (
            ['10.0.0.7'], ['10.0.0.8'])

        bvn._apply_ensure_mesh()

        # The implementation logs the removed event first, then the added
        # event. We assert that both calls happen but do not pin the order
        # beyond what the current implementation guarantees, to keep the
        # test resilient to a later reorder.
        network.add_event.assert_any_call(
            EVENT_TYPE_MUTATE, 'remove mesh elements',
            extra={'removed': ['10.0.0.8']})
        network.add_event.assert_any_call(
            EVENT_TYPE_MUTATE, 'add mesh elements',
            extra={'added': ['10.0.0.7']})
        self.assertEqual(2, network.add_event.call_count)

    def test_apply_ensure_mesh_omits_network_node_when_self(self):
        # When this node *is* the network node, NETWORK_NODE_IP must not be
        # added (it would be us, and we never include ourselves).
        fake_config = SFConfig(NODE_EGRESS_IP='10.0.0.1',
                               NODE_MESH_IP='10.0.0.1',
                               NETWORK_NODE_IP='10.0.0.1',
                               NODE_IS_NETWORK_NODE=True)
        with mock.patch(
                'shakenfist.network.bridged_vxlan_network.config',
                fake_config):
            iface = _make_interface('inst-a')
            network = _make_network_mock(networkinterfaces=[iface])
            bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

            inst = mock.Mock()
            inst.placement = {'node': 'sf2.example.com'}
            self.mock_instance_from_db.return_value = inst

            node = mock.Mock()
            node.ip = '10.0.0.4'
            self.mock_node_from_db.return_value = node

            bvn._apply_ensure_mesh()

            # NETWORK_NODE_IP not in the set; only sf2's IP.
            self.mock_ensure_vxlan_mesh.assert_called_once_with(
                network.uuid, network.vxid, {'10.0.0.4'})

    def test_apply_ensure_mesh_omits_node_when_self(self):
        # Nodes whose IP equals NODE_MESH_IP are intentionally not added to
        # the mesh (no self-loop in the FDB).
        iface = _make_interface('inst-a')
        network = _make_network_mock(networkinterfaces=[iface])
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        inst = mock.Mock()
        inst.placement = {'node': 'self.example.com'}
        self.mock_instance_from_db.return_value = inst

        node = mock.Mock()
        node.ip = '10.0.0.2'  # equals NODE_MESH_IP in the default config
        self.mock_node_from_db.return_value = node

        bvn._apply_ensure_mesh()

        # Only NETWORK_NODE_IP (10.0.0.1) ends up in the mesh.
        self.mock_ensure_vxlan_mesh.assert_called_once_with(
            network.uuid, network.vxid, {'10.0.0.1'})


class BridgedVXLanNetworkApplyFloatingIPTestCase(base.ShakenFistTestCase):
    """Tests for ``_apply_add_floating_ip`` and ``_apply_remove_floating_ip``.

    Both methods are simple lift-and-shifts of the floating-IP host-mutation
    bodies. The dispatcher (not the apply layer) is responsible for event
    correlation under the migrated design, so these methods do **not** emit
    audit events themselves.
    """

    def setUp(self):
        super().setUp()
        self.mock_add_floating_ip = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.add_floating_ip').start()
        self.mock_remove_floating_ip = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.remove_floating_ip').start()
        self.addCleanup(mock.patch.stopall)

    def test_apply_add_floating_ip_invokes_privexec(self):
        network = _make_network_mock(
            uuid='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_add_floating_ip('203.0.113.5', '10.0.0.5')

        network.get_lock.assert_called_once_with(
            op='Network add floating IP', global_scope=False)
        self.mock_add_floating_ip.assert_called_once_with(
            'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
            '203.0.113.5', '10.0.0.5')
        # The apply layer does NOT emit events; that is the dispatcher's job.
        network.add_event.assert_not_called()

    def test_apply_remove_floating_ip_invokes_privexec(self):
        network = _make_network_mock(
            uuid='cccccccc-cccc-4ccc-8ccc-cccccccccccc')
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_remove_floating_ip('203.0.113.6', '10.0.0.6')

        network.get_lock.assert_called_once_with(
            op='Network remove floating IP', global_scope=False)
        # remove_floating_ip in util_concurrency only takes uuid + floating
        # address (the inner address is informational at the dispatcher
        # level only).
        self.mock_remove_floating_ip.assert_called_once_with(
            'cccccccc-cccc-4ccc-8ccc-cccccccccccc', '203.0.113.6')
        network.add_event.assert_not_called()


class BridgedVXLanNetworkApplyRouteAddressTestCase(base.ShakenFistTestCase):
    """Tests for ``_apply_route_address`` and ``_apply_unroute_address``."""

    def setUp(self):
        super().setUp()
        self.mock_execute = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.execute').start()
        self.addCleanup(mock.patch.stopall)

    def _make_network_with_subst(self, vxid=0x123):
        network = _make_network_mock(vxid=vxid)
        network.subst_dict.return_value = {
            'vx_bridge': 'br-vxlan-%06x' % vxid,
        }
        return network

    def test_apply_route_address_runs_ip_route_add(self):
        network = self._make_network_with_subst(vxid=0xabc)
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_route_address('203.0.113.10')

        network.get_lock.assert_called_once_with(
            op='Network route address', global_scope=False)
        self.mock_execute.assert_called_once_with(
            'ip route add 203.0.113.10/32 dev br-vxlan-000abc')
        # The single-target audit event is preserved at the apply layer.
        network.add_event.assert_called_once_with(
            EVENT_TYPE_AUDIT, 'routing floating ip to network',
            extra={'floating': '203.0.113.10'})

    def test_apply_unroute_address_runs_ip_route_del(self):
        network = self._make_network_with_subst(vxid=0xdef)
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_unroute_address('203.0.113.11')

        network.get_lock.assert_called_once_with(
            op='Network unroute address', global_scope=False)
        self.mock_execute.assert_called_once_with(
            'ip route del 203.0.113.11/32 dev br-vxlan-000def')
        network.add_event.assert_called_once_with(
            EVENT_TYPE_AUDIT, 'unrouting floating ip to network',
            extra={'floating': '203.0.113.11'})


class BridgedVXLanNetworkApplyRemoveNATTestCase(base.ShakenFistTestCase):
    """Tests for ``_apply_remove_nat``.

    The body unassigns the floating gateway if one is currently assigned,
    inside the existing ``Network remove NAT`` lock.
    """

    def test_apply_remove_nat_unassigns_floating_gateway(self):
        network = _make_network_mock()
        network.floating_gateway = '203.0.113.100'
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_remove_nat()

        network.get_lock.assert_called_once_with(
            op='Network remove NAT', global_scope=False)
        network.unassign_floating_gateway.assert_called_once_with()

    def test_apply_remove_nat_noop_when_no_floating_gateway(self):
        network = _make_network_mock()
        network.floating_gateway = None
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_remove_nat()

        # Lock is still acquired (we may race with assignment), but no
        # gateway-release call is made.
        network.get_lock.assert_called_once_with(
            op='Network remove NAT', global_scope=False)
        network.unassign_floating_gateway.assert_not_called()
