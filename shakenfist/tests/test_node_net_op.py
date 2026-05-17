# Copyright 2019 Michael Still and contributors
"""Tests for ``shakenfist.operations.node_net_op.NodeNetOp.dispatch_task``.

Covers the step 5c dispatcher updates that route lifecycle operations
through ``BridgedVXLanNetwork``, the new
``network_apply_create_hypervisor`` task handler, and the newly-wired
ErrorReport persistence on the outer ``except Exception`` branch.
"""

from unittest import mock
from uuid import uuid4

from shakenfist.operations.node_net_op import NodeNetOp
from shakenfist.schema.operations.node_net_op import create_and_enqueue
from shakenfist.schema.operations.node_net_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


def _make_network_mock():
    """Build a minimal Network-like mock."""
    network = mock.MagicMock()
    mock_state = mock.MagicMock()
    mock_state.value = 'created'
    network.state = mock_state
    return network


def _make_node_net_op(test_case, tasks, node_uuid=None, network_uuid=None):
    """Enqueue and load a NodeNetOp with the given tasks."""
    if node_uuid is None:
        node_uuid = str(uuid4())
    if network_uuid is None:
        network_uuid = str(uuid4())
    _, op_uuid = create_and_enqueue(
        node_uuid=node_uuid,
        network_uuid=network_uuid,
        tasks=tasks,
        priority=PRIORITY.user_facing,
    )
    op = NodeNetOp.from_db(op_uuid)
    test_case.assertIsNotNone(op)
    return op, node_uuid, network_uuid


class NetworkDestroyRoutesThroughBridgedVXLanNetworkTestCase(base.ShakenFistTestCase):
    """``_network_destroy`` routes through BridgedVXLanNetwork._apply_delete_on_hypervisor."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.node_net_op.mariadb.set_cluster_operation_error')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_delete_on_hypervisor')
    @mock.patch('shakenfist.operations.node_net_op.Network.from_db')
    def test_network_destroy_calls_apply_delete_on_hypervisor(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching network_destroy delegates to BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _, _ = _make_node_net_op(self, [model_tasks.network_destroy])
        op.state = NodeNetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_destroy)

        mock_apply.assert_called_once_with()
        # Network.delete_on_hypervisor must never be invoked from the dispatcher.
        network.delete_on_hypervisor.assert_not_called()
        mock_set_error.assert_not_called()
        self.assertNotEqual(NodeNetOp.STATE_ERROR, op.state.value)


class NetworkApplyCreateHypervisorTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_network_apply_create_hypervisor`` routes through BridgedVXLanNetwork."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.node_net_op.mariadb.set_cluster_operation_error')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_hypervisor')
    @mock.patch('shakenfist.operations.node_net_op.Network.from_db')
    def test_apply_create_hypervisor_handler_calls_apply(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching network_apply_create_hypervisor routes through BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network

        op, _, _ = _make_node_net_op(
            self, [model_tasks.network_apply_create_hypervisor])
        op.state = NodeNetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_apply_create_hypervisor)

        mock_apply.assert_called_once_with()
        network.create_on_hypervisor.assert_not_called()
        mock_set_error.assert_not_called()
        self.assertNotEqual(NodeNetOp.STATE_ERROR, op.state.value)


class GenericExceptionPersistsErrorReportTestCase(base.ShakenFistTestCase):
    """Exceptions in the dispatcher persist an ErrorReport before STATE_ERROR.

    Step 5c added ErrorReport persistence to the outer ``except Exception``
    branch of ``NodeNetOp.dispatch_task``. Before step 5c the branch only
    set ``STATE_ERROR`` and did not record the failing exception in MariaDB.
    """

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.node_net_op.mariadb.set_cluster_operation_error')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_delete_on_hypervisor')
    @mock.patch('shakenfist.operations.node_net_op.Network.from_db')
    def test_generic_exception_sets_error_and_persists_report(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """An unregistered exception produces code='internal.unknown' in the report."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network
        mock_apply.side_effect = RuntimeError('unexpected failure')

        op, _, _ = _make_node_net_op(self, [model_tasks.network_destroy])
        op.state = NodeNetOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.network_destroy)

        self.assertEqual(NodeNetOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()
        report_arg = mock_set_error.call_args[0][1]
        self.assertEqual('internal.unknown', report_arg.code)
