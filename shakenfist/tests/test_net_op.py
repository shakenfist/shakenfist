# Copyright 2019 Michael Still and contributors
"""Tests for ``shakenfist.operations.net_op.NetOp.dispatch_task``.

Covers the new ``network_ensure_mesh`` task handler and the
ErrorReport-persistence wiring added to all exception branches in
``dispatch_task``.
"""

from unittest import mock
from uuid import uuid4

from shakenfist.exceptions import CreateVXLANInterfaceFailed
from shakenfist.exceptions import EnsureMeshFailed
from shakenfist.operations.net_op import NetOp
from shakenfist.schema.operations.net_op import create_and_enqueue
from shakenfist.schema.operations.net_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


def _make_network_mock(state_value='created', active=True):
    """Build a minimal Network-like mock for dispatch_task tests."""
    network = mock.MagicMock()
    mock_state = mock.MagicMock()
    mock_state.value = state_value
    network.state = mock_state
    # ACTIVE_STATES on Network (from baseobject): 'initial', 'creating',
    # 'created', 'error', ...  For tests we control via `active` flag.
    network.ACTIVE_STATES = {'created', 'initial', 'creating', 'error'} if active else set()
    return network


def _make_net_op(test_case, mock_etcd, tasks, network_uuid=None,
                 floating_address=None, inner_address=None):
    """Enqueue and load a NetOp with the given tasks."""
    if network_uuid is None:
        network_uuid = str(uuid4())
    _, op_uuid = create_and_enqueue(
        network_uuid=network_uuid,
        tasks=tasks,
        priority=PRIORITY.user_waiting,
        floating_address=floating_address,
        inner_address=inner_address,
    )
    op = NetOp.from_db(op_uuid)
    test_case.assertIsNotNone(op)
    return op, network_uuid


class NetworkEnsureMeshTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_ensure_mesh`` handler is invoked for the new task."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_ensure_mesh')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_ensure_mesh_handler_calls_apply_ensure_mesh(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching network_ensure_mesh delegates to BridgedVXLanNetwork."""
        mock_network_from_db.return_value = _make_network_mock()

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_ensure_mesh])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_ensure_mesh)

        mock_apply.assert_called_once_with()
        mock_set_error.assert_not_called()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_ensure_mesh')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_ensure_mesh_success_does_not_set_error_state(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """A successful _apply_ensure_mesh leaves the op in EXECUTING state."""
        mock_network_from_db.return_value = _make_network_mock()
        mock_apply.return_value = None

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_ensure_mesh])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_ensure_mesh)

        # State should not have been set to ERROR
        self.assertNotEqual(NetOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_not_called()


class EnsureMeshFailedExceptionTestCase(base.ShakenFistTestCase):
    """EnsureMeshFailed from _apply_ensure_mesh triggers ErrorReport persistence."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_ensure_mesh')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_ensure_mesh_failed_in_non_active_network_sets_error_and_persists_report(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """EnsureMeshFailed on a non-active network: STATE_ERROR + ErrorReport."""
        mock_network_from_db.return_value = _make_network_mock(
            state_value='deleted', active=False)
        mock_apply.side_effect = EnsureMeshFailed('mesh broke')

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_ensure_mesh])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_ensure_mesh)

        self.assertEqual(NetOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()
        report_arg = mock_set_error.call_args[0][1]
        self.assertEqual('network.ensure_mesh.failed', report_arg.code)

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_ensure_mesh')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_ensure_mesh_failed_in_active_network_sets_error_and_persists_report(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """EnsureMeshFailed on an active network still sets STATE_ERROR and persists report.

        The active-network carve-out in the except branch only controls whether
        ignore_exception is called; STATE_ERROR is always set, and the report is
        always persisted.
        """
        mock_network_from_db.return_value = _make_network_mock(
            state_value='created', active=True)
        mock_apply.side_effect = EnsureMeshFailed('mesh broke on active network')

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_ensure_mesh])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_ensure_mesh)

        self.assertEqual(NetOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()
        report_arg = mock_set_error.call_args[0][1]
        self.assertEqual('network.ensure_mesh.failed', report_arg.code)


class GenericExceptionTestCase(base.ShakenFistTestCase):
    """Generic Exception from a handler transitions to ERROR with internal.unknown report."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_ensure_mesh')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_generic_exception_sets_error_and_persists_internal_unknown(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """An unregistered exception produces code='internal.unknown' in the report."""
        mock_network_from_db.return_value = _make_network_mock()
        mock_apply.side_effect = RuntimeError('unexpected failure')

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_ensure_mesh])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_ensure_mesh)

        self.assertEqual(NetOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()
        report_arg = mock_set_error.call_args[0][1]
        self.assertEqual('internal.unknown', report_arg.code)


class CreateVXLANInterfaceFailedTestCase(base.ShakenFistTestCase):
    """CreateVXLANInterfaceFailed persists a report with the right code."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_ensure_mesh')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_create_vxlan_failed_sets_error_and_persists_report(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """CreateVXLANInterfaceFailed maps to network.create_vxlan.failed."""
        mock_network_from_db.return_value = _make_network_mock()
        mock_apply.side_effect = CreateVXLANInterfaceFailed('vxlan create failed')

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_ensure_mesh])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_ensure_mesh)

        self.assertEqual(NetOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()
        report_arg = mock_set_error.call_args[0][1]
        self.assertEqual('network.create_vxlan.failed', report_arg.code)


class NetworkDeployEnsureMeshRoutingTestCase(base.ShakenFistTestCase):
    """_network_deploy uses BridgedVXLanNetwork._apply_ensure_mesh, never n.ensure_mesh()."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_network_node')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_ensure_mesh')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_network_deploy_calls_apply_ensure_mesh_not_n_ensure_mesh(
            self, mock_network_from_db, mock_apply_mesh, mock_apply_create,
            mock_set_error):
        """_network_deploy delegates mesh setup to BridgedVXLanNetwork, not Network.ensure_mesh."""
        network = _make_network_mock()
        network.is_dead.return_value = False
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_deploy])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_deploy)

        mock_apply_mesh.assert_called_once_with()
        mock_apply_create.assert_called_once_with()
        network.ensure_mesh.assert_not_called()
        network.create_on_network_node.assert_not_called()
        mock_set_error.assert_not_called()


class NetworkRemoveNatTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_remove_nat`` routes through BridgedVXLanNetwork._apply_remove_nat."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_remove_nat')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_remove_nat_handler_calls_apply_remove_nat(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching network_remove_nat delegates to BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_remove_nat])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_remove_nat)

        mock_apply.assert_called_once_with()
        # Network.remove_nat must never be invoked from the dispatcher.
        network.remove_nat.assert_not_called()
        mock_set_error.assert_not_called()


class NetworkAddFloatingIPTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_add_floating_ip`` reads static-value fields and applies them."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.add_event_multi')
    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_add_floating_ip')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_add_floating_ip_handler_calls_apply_with_static_values(
            self, mock_network_from_db, mock_apply, mock_set_error,
            mock_add_event_multi):
        """The handler passes floating_address and inner_address through to BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(
            self, self.mock_etcd, [model_tasks.network_add_floating_ip],
            floating_address='192.0.2.5', inner_address='10.0.0.5')
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_add_floating_ip)

        mock_apply.assert_called_once_with('192.0.2.5', '10.0.0.5')
        # The dispatcher must emit a multi-target audit event referencing
        # the wrapped network and the floating-network metadata object.
        mock_add_event_multi.assert_called_once()
        args, kwargs = mock_add_event_multi.call_args
        targets = args[1]
        self.assertIn(network, targets)
        mock_set_error.assert_not_called()

    def test_floating_address_and_inner_address_properties_expose_static_values(self):
        """NetOp.floating_address / inner_address read from static_values."""
        op, _ = _make_net_op(
            self, self.mock_etcd, [model_tasks.network_add_floating_ip],
            floating_address='192.0.2.6', inner_address='10.0.0.6')
        self.assertEqual('192.0.2.6', op.floating_address)
        self.assertEqual('10.0.0.6', op.inner_address)

    def test_floating_fields_default_to_none_for_other_tasks(self):
        """Tasks that don't carry floating fields see None for those properties."""
        op, _ = _make_net_op(
            self, self.mock_etcd, [model_tasks.network_ensure_mesh])
        self.assertIsNone(op.floating_address)
        self.assertIsNone(op.inner_address)


class NetworkRemoveFloatingIPTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_remove_floating_ip`` reads static-value fields and applies them."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.add_event_multi')
    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_remove_floating_ip')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_remove_floating_ip_handler_calls_apply_with_static_values(
            self, mock_network_from_db, mock_apply, mock_set_error,
            mock_add_event_multi):
        """The handler passes floating_address and inner_address through to BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(
            self, self.mock_etcd, [model_tasks.network_remove_floating_ip],
            floating_address='192.0.2.7', inner_address='10.0.0.7')
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_remove_floating_ip)

        mock_apply.assert_called_once_with('192.0.2.7', '10.0.0.7')
        mock_add_event_multi.assert_called_once()
        args, kwargs = mock_add_event_multi.call_args
        targets = args[1]
        self.assertIn(network, targets)
        mock_set_error.assert_not_called()


class NetworkRemoveDnsmasqTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_remove_dnsmasq`` routes through BridgedVXLanNetwork._apply_remove_dnsmasq."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_remove_dnsmasq')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_remove_dnsmasq_handler_calls_apply_remove_dnsmasq(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching network_remove_dnsmasq delegates to BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_remove_dnsmasq])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_remove_dnsmasq)

        mock_apply.assert_called_once_with()
        # Network.remove_dnsmasq must never be invoked from the dispatcher.
        network.remove_dnsmasq.assert_not_called()
        mock_set_error.assert_not_called()


class NetworkApplyUpdateDnsmasqTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_apply_update_dnsmasq`` routes through BridgedVXLanNetwork._apply_update_dnsmasq."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_update_dnsmasq')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_apply_update_dnsmasq_handler_calls_apply_update_dnsmasq(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching network_apply_update_dnsmasq delegates to BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(
            self, self.mock_etcd, [model_tasks.network_apply_update_dnsmasq])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_apply_update_dnsmasq)

        mock_apply.assert_called_once_with()
        # Network.update_dnsmasq must never be invoked from the dispatcher.
        network.update_dnsmasq.assert_not_called()
        mock_set_error.assert_not_called()


class NetworkApplyRemoveDnsmasqTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_apply_remove_dnsmasq`` routes through BridgedVXLanNetwork._apply_remove_dnsmasq."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_remove_dnsmasq')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_apply_remove_dnsmasq_handler_calls_apply_remove_dnsmasq(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching network_apply_remove_dnsmasq delegates to BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(
            self, self.mock_etcd, [model_tasks.network_apply_remove_dnsmasq])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_apply_remove_dnsmasq)

        mock_apply.assert_called_once_with()
        # Network.remove_dnsmasq must never be invoked from the dispatcher.
        network.remove_dnsmasq.assert_not_called()
        mock_set_error.assert_not_called()


class NetworkDeployCreateOnNetworkNodeRoutingTestCase(base.ShakenFistTestCase):
    """_network_deploy routes through BridgedVXLanNetwork._apply_create_on_network_node."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_ensure_mesh')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_network_node')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_network_deploy_calls_apply_create_on_network_node(
            self, mock_network_from_db, mock_apply_create, mock_apply_mesh,
            mock_set_error):
        """_network_deploy must route create through BridgedVXLanNetwork."""
        network = _make_network_mock()
        network.is_dead.return_value = False
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_deploy])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_deploy)

        mock_apply_create.assert_called_once_with()
        mock_apply_mesh.assert_called_once_with()
        # Network.create_on_network_node must never be invoked directly.
        network.create_on_network_node.assert_not_called()
        mock_set_error.assert_not_called()


class NetworkDestroyDeleteOnNetworkNodeRoutingTestCase(base.ShakenFistTestCase):
    """_network_destroy routes through BridgedVXLanNetwork._apply_delete_on_network_node."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_delete_on_network_node')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_network_destroy_calls_apply_delete_on_network_node(
            self, mock_network_from_db, mock_apply_delete, mock_set_error):
        """_network_destroy must route delete through BridgedVXLanNetwork."""
        network = _make_network_mock()
        # No outstanding network interfaces so the handler proceeds.
        network.networkinterfaces = []
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_destroy])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_destroy)

        mock_apply_delete.assert_called_once_with()
        network.delete_on_network_node.assert_not_called()
        mock_set_error.assert_not_called()


class NetworkApplyCreateNetworkNodeTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_apply_create_network_node`` calls _apply_create_on_network_node."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_network_node')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_apply_create_network_node_handler_calls_apply(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching the new task routes through BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(
            self, self.mock_etcd,
            [model_tasks.network_apply_create_network_node])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_apply_create_network_node)

        mock_apply.assert_called_once_with()
        network.create_on_network_node.assert_not_called()
        mock_set_error.assert_not_called()


class NetworkApplyDeleteNetworkNodeTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_apply_delete_network_node`` calls _apply_delete_on_network_node."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_delete_on_network_node')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_apply_delete_network_node_handler_calls_apply(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching the new task routes through BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(
            self, self.mock_etcd,
            [model_tasks.network_apply_delete_network_node])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_apply_delete_network_node)

        mock_apply.assert_called_once_with()
        network.delete_on_network_node.assert_not_called()
        mock_set_error.assert_not_called()


class NetworkUpdateDnsmasqEnsureMeshRoutingTestCase(base.ShakenFistTestCase):
    """_network_update_dnsmasq uses BridgedVXLanNetwork._apply_ensure_mesh, never n.ensure_mesh()."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_op.mariadb.set_cluster_operation_error')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_network_node')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_ensure_mesh')
    @mock.patch('shakenfist.operations.net_op.Network.from_db')
    def test_network_update_dnsmasq_calls_apply_ensure_mesh_not_n_ensure_mesh(
            self, mock_network_from_db, mock_apply_mesh, mock_apply_create,
            mock_set_error):
        """_network_update_dnsmasq delegates mesh setup to BridgedVXLanNetwork, not Network.ensure_mesh."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_op(self, self.mock_etcd, [model_tasks.network_update_dnsmasq])
        op.state = NetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_update_dnsmasq)

        mock_apply_create.assert_called_once_with()
        mock_apply_mesh.assert_called_once_with()
        network.ensure_mesh.assert_not_called()
        network.create_on_network_node.assert_not_called()
        mock_set_error.assert_not_called()
