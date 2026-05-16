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


def _make_net_op(test_case, mock_etcd, tasks, network_uuid=None):
    """Enqueue and load a NetOp with the given tasks."""
    if network_uuid is None:
        network_uuid = str(uuid4())
    _, op_uuid = create_and_enqueue(
        network_uuid=network_uuid,
        tasks=tasks,
        priority=PRIORITY.user_waiting,
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
