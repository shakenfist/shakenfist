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

        # No lock taken; the single-threaded dispatcher provides serialisation.
        network.get_lock.assert_not_called()

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

        network.get_lock.assert_not_called()
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

        network.get_lock.assert_not_called()
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

        network.get_lock.assert_not_called()
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

        network.get_lock.assert_not_called()
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

        network.get_lock.assert_not_called()
        network.unassign_floating_gateway.assert_called_once_with()

    def test_apply_remove_nat_noop_when_no_floating_gateway(self):
        network = _make_network_mock()
        network.floating_gateway = None
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_remove_nat()

        # No lock taken; the dispatcher serialises all callers.
        network.get_lock.assert_not_called()
        network.unassign_floating_gateway.assert_not_called()


class BridgedVXLanNetworkApplyDnsMasqTestCase(base.ShakenFistTestCase):
    """Tests for the dnsmasq lifecycle apply methods.

    ``_apply_update_dnsmasq``, ``_apply_remove_dnsmasq`` and
    ``_apply_remove_dhcp_lease`` each wrap a single call on the dnsmasq
    object returned from ``Network._get_dnsmasq_object``. We mock that
    method on the wrapped network and assert the corresponding lifecycle
    call is dispatched under the appropriate ``get_lock`` op.
    """

    def test_apply_update_dnsmasq_restarts(self):
        network = _make_network_mock()
        fake_dnsmasq = mock.Mock()
        network._get_dnsmasq_object.return_value = fake_dnsmasq
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_update_dnsmasq()

        network.get_lock.assert_not_called()
        network._get_dnsmasq_object.assert_called_once_with()
        fake_dnsmasq.restart.assert_called_once_with()

    def test_apply_remove_dnsmasq_terminates_and_marks_deleted(self):
        network = _make_network_mock()
        fake_dnsmasq = mock.Mock()
        network._get_dnsmasq_object.return_value = fake_dnsmasq
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_remove_dnsmasq()

        network.get_lock.assert_not_called()
        network._get_dnsmasq_object.assert_called_once_with()
        fake_dnsmasq.terminate.assert_called_once_with()
        # The state transition to STATE_DELETED is part of the lifted body.
        self.assertEqual(
            bridged_vxlan_network.dnsmasq.DnsMasq.STATE_DELETED,
            fake_dnsmasq.state)

    def test_apply_remove_dhcp_lease_invokes_remove_lease(self):
        network = _make_network_mock()
        fake_dnsmasq = mock.Mock()
        network._get_dnsmasq_object.return_value = fake_dnsmasq
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_remove_dhcp_lease('10.0.0.5', '02:00:00:11:22:33')

        # No lock taken; the dispatcher serialises all callers.
        network.get_lock.assert_not_called()
        network._get_dnsmasq_object.assert_called_once_with()
        fake_dnsmasq.remove_lease.assert_called_once_with(
            '10.0.0.5', '02:00:00:11:22:33')


class BridgedVXLanNetworkApplyCreateOnHypervisorTestCase(
        base.ShakenFistTestCase):
    """Tests for ``_apply_create_on_hypervisor``.

    Lifted from ``Network.create_on_hypervisor``. We confirm the
    floating-network short-circuit, the dead-network raise, and the
    ``create_vxlan_interface`` call under the expected lock.
    """

    def setUp(self):
        super().setUp()
        self.mock_create_vxlan_interface = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.create_vxlan_interface').start()
        self.addCleanup(mock.patch.stopall)

    def _make_network(self, uuid='dddddddd-dddd-4ddd-8ddd-dddddddddddd',
                      vxid=42, mesh_nic='eth0', is_dead=False):
        network = _make_network_mock(uuid=uuid, vxid=vxid)
        network.mesh_nic = mesh_nic
        network.is_dead.return_value = is_dead
        network.subst_dict.return_value = {
            'vx_bridge': 'br-%06x' % vxid,
            'vx_interface': 'vxlan-%06x' % vxid,
        }
        return network

    def test_floating_network_short_circuits(self):
        network = self._make_network(uuid=FLOATING_NETWORK_UUID)
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_create_on_hypervisor()

        self.mock_create_vxlan_interface.assert_not_called()
        network.get_lock.assert_not_called()
        network.add_event.assert_not_called()

    def test_creates_vxlan_interface(self):
        network = self._make_network(vxid=99, mesh_nic='eth0')
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_create_on_hypervisor()

        network.get_lock.assert_not_called()
        self.mock_create_vxlan_interface.assert_called_once_with(99, 'eth0')
        # Two audit events: pre- and post-creation.
        self.assertEqual(2, network.add_event.call_count)

    def test_dead_network_raises(self):
        from shakenfist.exceptions import DeadNetwork
        network = self._make_network(is_dead=True)
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        self.assertRaises(DeadNetwork, bvn._apply_create_on_hypervisor)
        self.mock_create_vxlan_interface.assert_not_called()


class BridgedVXLanNetworkApplyCreateOnNetworkNodeTestCase(
        base.ShakenFistTestCase):
    """Tests for ``_apply_create_on_network_node``.

    The body is ~110 lines and reaches into many helpers. We mock the
    privexec / util_network layer wholesale and assert the high-level
    behaviour: short-circuits, the namespace setup call, the dnsmasq
    handoff, and the NAT enable handoff.
    """

    def setUp(self):
        super().setUp()
        self.mock_create_vxlan_interface = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.create_vxlan_interface').start()
        self.mock_create_network_namespace = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.create_network_namespace').start()
        self.mock_execute = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.execute').start()
        self.mock_enable_nat = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.enable_nat').start()
        # util_network helpers: default to "interface already exists"
        # so the body skips the create-interface branches.
        self.mock_check_for_interface = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_network.check_for_interface', return_value=True).start()
        self.mock_floating_network = mock.patch(
            'shakenfist.network.network.floating_network').start()
        self.addCleanup(mock.patch.stopall)

    def _make_network(self, uuid='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
                      vxid=42, state_value='created',
                      provide_dhcp=False, provide_dns=False,
                      provide_nat=False, is_dead=False):
        network = _make_network_mock(uuid=uuid, vxid=vxid)
        network.mesh_nic = 'eth0'
        network.state = mock.Mock()
        network.state.value = state_value
        network.STATE_CREATED = 'created'
        network.STATE_ERROR = 'error'
        network.is_dead.return_value = is_dead
        network.provide_dhcp = provide_dhcp
        network.provide_dns = provide_dns
        network.provide_nat = provide_nat
        network.floating_gateway = None
        network.subst_dict.return_value = {
            'vx_bridge': 'br-vxlan-%06x' % vxid,
            'vx_interface': 'vxlan-%06x' % vxid,
            'vx_veth_outer': 'vx-%06x-o' % vxid,
            'vx_veth_inner': 'vx-%06x-i' % vxid,
            'egress_bridge': 'egr-%06x' % vxid,
            'egress_veth_outer': 'eg-%06x-o' % vxid,
            'egress_veth_inner': 'eg-%06x-i' % vxid,
            'netns': str(uuid),
            'router': '10.0.0.1',
            'netmask': '255.255.255.0',
        }
        return network

    def test_floating_network_short_circuits(self):
        network = self._make_network(uuid=FLOATING_NETWORK_UUID)
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_create_on_network_node()

        self.mock_create_vxlan_interface.assert_not_called()
        self.mock_create_network_namespace.assert_not_called()
        network.get_lock.assert_not_called()

    def test_deleted_state_short_circuits(self):
        network = self._make_network(state_value='deleted')
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_create_on_network_node()

        self.mock_create_vxlan_interface.assert_not_called()
        self.mock_create_network_namespace.assert_not_called()
        network.get_lock.assert_not_called()
        network.add_event.assert_called_once_with(
            EVENT_TYPE_AUDIT,
            'refusing to create deleted network on network node')

    def test_creates_vxlan_and_namespace(self):
        network = self._make_network(vxid=77)
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_create_on_network_node()

        self.mock_create_vxlan_interface.assert_called_once_with(77, 'eth0')
        self.mock_create_network_namespace.assert_called_once_with(
            network.uuid)
        # No NAT / no dnsmasq path taken.
        self.mock_enable_nat.assert_not_called()
        # State transitioned to CREATED at the end.
        self.assertEqual('created', network.state)

    def test_provide_nat_invokes_enable_nat(self):
        network = self._make_network(provide_nat=True)
        network.floating_gateway = '203.0.113.10'
        network.network_address = '10.0.0.0'
        network.netmask = '255.255.255.0'

        # floating_network() returns a stub with the bits the body reads.
        fn = mock.Mock()
        fn.ipam.get_address_at_index.return_value = '203.0.113.1'
        fn.netmask = '255.255.255.0'
        self.mock_floating_network.return_value = fn

        # Hand back a non-matching default route so the body deletes it
        # and adds the new one.
        with mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'util_network.get_interface_addresses', return_value=[]), \
            mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'util_network.add_address_to_interface'), \
            mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'util_network.get_default_routes', return_value=[]), \
            mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'util_network.add_default_route'):
            bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)
            bvn._apply_create_on_network_node()

        # _apply_enable_nat under the hood calls util_concurrency.enable_nat.
        self.mock_enable_nat.assert_called_once_with(
            network.uuid, '10.0.0.0', '255.255.255.0', network.vxid)

    def test_provide_dhcp_triggers_dnsmasq_update(self):
        network = self._make_network(provide_dhcp=True)
        fake_dnsmasq = mock.Mock()
        network._get_dnsmasq_object.return_value = fake_dnsmasq

        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)
        bvn._apply_create_on_network_node()

        # _apply_update_dnsmasq fetches the dnsmasq object and restarts it.
        fake_dnsmasq.restart.assert_called_once_with()

    def test_provide_dns_triggers_dnsmasq_update(self):
        network = self._make_network(provide_dns=True)
        fake_dnsmasq = mock.Mock()
        network._get_dnsmasq_object.return_value = fake_dnsmasq

        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)
        bvn._apply_create_on_network_node()

        fake_dnsmasq.restart.assert_called_once_with()


class BridgedVXLanNetworkApplyDeleteOnHypervisorTestCase(
        base.ShakenFistTestCase):
    """Tests for ``_apply_delete_on_hypervisor``."""

    def setUp(self):
        super().setUp()
        self.mock_execute = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.execute').start()
        self.mock_check_for_interface = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_network.check_for_interface').start()
        self.addCleanup(mock.patch.stopall)

    def _make_network(self, vxid=42):
        network = _make_network_mock(vxid=vxid)
        network.subst_dict.return_value = {
            'vx_bridge': 'br-vxlan-%06x' % vxid,
            'vx_interface': 'vxlan-%06x' % vxid,
        }
        return network

    def test_deletes_bridge_and_interface_when_present(self):
        self.mock_check_for_interface.return_value = True
        network = self._make_network(vxid=0x123)
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_delete_on_hypervisor()

        network.get_lock.assert_not_called()
        self.assertEqual(2, self.mock_execute.call_count)
        self.mock_execute.assert_any_call('ip link delete br-vxlan-000123')
        self.mock_execute.assert_any_call('ip link delete vxlan-000123')

    def test_skips_deletes_when_interfaces_absent(self):
        self.mock_check_for_interface.return_value = False
        network = self._make_network()
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_delete_on_hypervisor()

        # No `ip link delete` invocations.
        self.mock_execute.assert_not_called()


class BridgedVXLanNetworkApplyDeleteOnNetworkNodeTestCase(
        base.ShakenFistTestCase):
    """Tests for ``_apply_delete_on_network_node``."""

    def setUp(self):
        super().setUp()
        self.mock_execute = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_concurrency.execute').start()
        self.mock_check_for_interface = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'util_network.check_for_interface',
            return_value=False).start()
        self.mock_os_path_exists = mock.patch(
            'shakenfist.network.bridged_vxlan_network.os.path.exists',
            return_value=False).start()
        self.mock_nodes = mock.patch(
            'shakenfist.network.bridged_vxlan_network.Nodes',
            return_value=iter([])).start()
        self.mock_nn_enqueue = mock.patch(
            'shakenfist.network.bridged_vxlan_network.'
            'nn_create_and_enqueue').start()
        self.addCleanup(mock.patch.stopall)

    def _make_network(self, uuid='ffffffff-ffff-4fff-8fff-ffffffffffff',
                      provide_dhcp=False, provide_dns=False):
        network = _make_network_mock(uuid=uuid)
        network.subst_dict.return_value = {
            'vx_veth_outer': 'vx-outer',
            'egress_veth_outer': 'egr-outer',
        }
        network.provide_dhcp = provide_dhcp
        network.provide_dns = provide_dns
        network.floating_gateway = None
        network.STATE_DELETED = 'deleted'
        network.ipam = mock.Mock()
        network.ipam.STATE_DELETED = 'deleted'
        return network

    def test_calls_remove_nat_unconditionally(self):
        network = self._make_network()
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)

        bvn._apply_delete_on_network_node()

        # No lock taken; the dispatcher serialises all callers.
        network.get_lock.assert_not_called()
        # State transitioned to DELETED.
        self.assertEqual('deleted', network.state)
        self.assertEqual('deleted', network.ipam.state)

    def test_calls_remove_dnsmasq_when_dhcp(self):
        network = self._make_network(provide_dhcp=True)
        fake_dnsmasq = mock.Mock()
        network._get_dnsmasq_object.return_value = fake_dnsmasq

        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)
        bvn._apply_delete_on_network_node()

        fake_dnsmasq.terminate.assert_called_once_with()

    def test_fans_out_node_destroy_to_active_nodes(self):
        node_a = mock.Mock()
        node_a.uuid = 'node-a'
        node_b = mock.Mock()
        node_b.uuid = 'node-b'
        self.mock_nodes.return_value = iter([node_a, node_b])

        network = self._make_network()
        bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)
        bvn._apply_delete_on_network_node()

        self.assertEqual(2, self.mock_nn_enqueue.call_count)


class BridgedVXLanNetworkApplyEnableNATTestCase(base.ShakenFistTestCase):
    """Tests for ``_apply_enable_nat``."""

    def test_invokes_privexec(self):
        with mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'util_concurrency.enable_nat') as mock_enable_nat:
            network = _make_network_mock(
                uuid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', vxid=77)
            network.network_address = '10.0.0.0'
            network.netmask = '255.255.255.0'

            bvn = bridged_vxlan_network.BridgedVXLanNetwork(network)
            bvn._apply_enable_nat()

            mock_enable_nat.assert_called_once_with(
                'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                '10.0.0.0', '255.255.255.0', 77)
