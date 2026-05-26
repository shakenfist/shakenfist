# Copyright 2019 Michael Still and contributors
"""Tests for ``shakenfist.operations.net_ip_op.NetIPOp.dispatch_task``.

Covers the Phase 3 migration to BridgedVXLanNetwork and the
ErrorReport-persistence wiring added to the dispatcher's outer except.
"""

from unittest import mock
from uuid import uuid4

from shakenfist.exceptions import DeadNetwork
from shakenfist.operations.net_ip_op import NetIPOp
from shakenfist.schema.operations.net_ip_op import create_and_enqueue
from shakenfist.schema.operations.net_ip_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


def _make_network_mock(is_dead=False):
    network = mock.MagicMock()
    network.is_dead.return_value = is_dead
    return network


def _make_net_ip_op(test_case, tasks, ip='192.0.2.10', network_uuid=None):
    if network_uuid is None:
        network_uuid = str(uuid4())
    _, op_uuid = create_and_enqueue(
        network_uuid=network_uuid,
        ip=ip,
        tasks=tasks,
        priority=PRIORITY.user_waiting,
    )
    op = NetIPOp.from_db(op_uuid)
    test_case.assertIsNotNone(op)
    return op, network_uuid


class RouteAddressTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_route_address`` routes through BridgedVXLanNetwork._apply_route_address."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_ip_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_route_address')
    @mock.patch('shakenfist.operations.net_ip_op.Network.from_db')
    def test_route_address_handler_calls_apply_route_address(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching route_address delegates to BridgedVXLanNetwork with self.ip."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_ip_op(self, [model_tasks.route_address], ip='192.0.2.11')
        op.state = NetIPOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.route_address)

        mock_apply.assert_called_once()
        # The argument should be the ip — the IPvAnyAddress object stored on self.ip.
        called_with = mock_apply.call_args[0][0]
        self.assertEqual(str(op.ip), str(called_with))
        # Network.route_address must never be called from the dispatcher.
        network.route_address.assert_not_called()
        mock_set_error.assert_not_called()


class UnrouteAddressTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_unroute_address`` routes through BridgedVXLanNetwork._apply_unroute_address."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_ip_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_unroute_address')
    @mock.patch('shakenfist.operations.net_ip_op.Network.from_db')
    def test_unroute_address_handler_calls_apply_unroute_address(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching unroute_address delegates to BridgedVXLanNetwork with self.ip."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _ = _make_net_ip_op(self, [model_tasks.unroute_address], ip='192.0.2.12')
        op.state = NetIPOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.unroute_address)

        mock_apply.assert_called_once()
        called_with = mock_apply.call_args[0][0]
        self.assertEqual(str(op.ip), str(called_with))
        network.unroute_address.assert_not_called()
        mock_set_error.assert_not_called()


class ExceptionErrorReportPersistenceTestCase(base.ShakenFistTestCase):
    """Outer except persists an ErrorReport before setting STATE_ERROR."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_ip_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_route_address')
    @mock.patch('shakenfist.operations.net_ip_op.Network.from_db')
    def test_dead_network_exception_persists_report_and_sets_error(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """DeadNetwork from _apply_route_address persists an ErrorReport."""
        mock_network_from_db.return_value = _make_network_mock()
        mock_apply.side_effect = DeadNetwork('network is dead')

        op, _ = _make_net_ip_op(self, [model_tasks.route_address])
        op.state = NetIPOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.route_address)

        self.assertEqual(NetIPOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()

    @mock.patch('shakenfist.operations.net_ip_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_unroute_address')
    @mock.patch('shakenfist.operations.net_ip_op.Network.from_db')
    def test_generic_exception_persists_internal_unknown_report(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """An unregistered exception produces code='internal.unknown' in the report."""
        mock_network_from_db.return_value = _make_network_mock()
        mock_apply.side_effect = RuntimeError('boom')

        op, _ = _make_net_ip_op(self, [model_tasks.unroute_address])
        op.state = NetIPOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.unroute_address)

        self.assertEqual(NetIPOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()
        report_arg = mock_set_error.call_args[0][1]
        self.assertEqual('internal.unknown', report_arg.code)
