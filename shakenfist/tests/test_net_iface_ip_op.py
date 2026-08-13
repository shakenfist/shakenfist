# Copyright 2019 Michael Still and contributors
"""Tests for ``shakenfist.operations.net_iface_ip_op.NetIfaceIPOp.dispatch_task``.

Covers the Phase 3 migration of ``_interface_defloat`` to
BridgedVXLanNetwork and the multi-target audit event emitted at dispatch
time. Also covers ErrorReport persistence on the outer except.
"""

from unittest import mock
from uuid import uuid4

from shakenfist.exceptions import RemoveFloatingIPFailed
from shakenfist.operations.net_iface_ip_op import NetIfaceIPOp
from shakenfist.operations.net_iface_ip_op import NoSuchNetworkInterface
from shakenfist.schema.operations.net_iface_ip_op import create_and_enqueue
from shakenfist.schema.operations.net_iface_ip_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


def _make_network_mock():
    network = mock.MagicMock()
    network.uuid = str(uuid4())
    return network


def _make_interface_mock(inner_ipv4='10.0.0.30'):
    ni = mock.MagicMock()
    ni.ipv4 = inner_ipv4
    ni.instance_uuid = str(uuid4())
    return ni


def _make_net_iface_ip_op(test_case, tasks, ip='192.0.2.30',
                          network_uuid=None, interface_uuid=None):
    if network_uuid is None:
        network_uuid = str(uuid4())
    if interface_uuid is None:
        interface_uuid = str(uuid4())
    _, op_uuid = create_and_enqueue(
        network_uuid=network_uuid,
        interface_uuid=interface_uuid,
        ip=ip,
        tasks=tasks,
        priority=PRIORITY.user_waiting,
    )
    op = NetIfaceIPOp.from_db(op_uuid)
    test_case.assertIsNotNone(op)
    return op, network_uuid, interface_uuid


class InterfaceDefloatTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_interface_defloat`` routes through BridgedVXLanNetwork and emits the audit event."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    @mock.patch('shakenfist.operations.net_iface_ip_op.add_event_multi')
    @mock.patch('shakenfist.operations.net_iface_ip_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_remove_floating_ip')
    @mock.patch('shakenfist.operations.net_iface_ip_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.net_iface_ip_op.Network.from_db')
    def test_interface_defloat_calls_apply_remove_floating_ip(
            self, mock_network_from_db, mock_iface_from_db, mock_apply,
            mock_set_error, mock_add_event_multi):
        """Dispatching interface_defloat delegates to BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network
        ni = _make_interface_mock(inner_ipv4='10.0.0.31')
        mock_iface_from_db.return_value = ni

        op, _, _ = _make_net_iface_ip_op(
            self, [model_tasks.interface_defloat], ip='192.0.2.31')
        op.state = NetIfaceIPOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.interface_defloat)

        mock_apply.assert_called_once()
        called_args = mock_apply.call_args[0]
        self.assertEqual(str(op.ip), str(called_args[0]))
        self.assertEqual('10.0.0.31', called_args[1])
        # Network.remove_floating_ip must never be called from the dispatcher.
        network.remove_floating_ip.assert_not_called()
        mock_set_error.assert_not_called()

        # Multi-target audit event should include the interface and the
        # instance tuple at minimum.
        mock_add_event_multi.assert_called_once()
        args, kwargs = mock_add_event_multi.call_args
        targets = args[1]
        self.assertIn(ni, targets)
        self.assertIn(('instance', ni.instance_uuid), targets)

    @mock.patch('shakenfist.operations.net_iface_ip_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.net_iface_ip_op.Network.from_db')
    def test_missing_interface_raises_no_such_network_interface(
            self, mock_network_from_db, mock_iface_from_db):
        """A hard-deleted interface raises NoSuchNetworkInterface, not AttributeError."""
        mock_network_from_db.return_value = _make_network_mock()
        mock_iface_from_db.return_value = None

        op, _, _ = _make_net_iface_ip_op(
            self, [model_tasks.interface_defloat])
        op.state = NetIfaceIPOp.STATE_EXECUTING
        self.assertRaises(
            NoSuchNetworkInterface, op.dispatch_task,
            model_tasks.interface_defloat)


class InterfaceDefloatExceptionTestCase(base.ShakenFistTestCase):
    """Exceptions from _apply_remove_floating_ip persist an ErrorReport."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    @mock.patch('shakenfist.operations.net_iface_ip_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_remove_floating_ip')
    @mock.patch('shakenfist.operations.net_iface_ip_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.net_iface_ip_op.Network.from_db')
    def test_remove_floating_ip_failed_persists_report_and_sets_error(
            self, mock_network_from_db, mock_iface_from_db, mock_apply,
            mock_set_error):
        """RemoveFloatingIPFailed persists a report and sets STATE_ERROR."""
        mock_network_from_db.return_value = _make_network_mock()
        mock_iface_from_db.return_value = _make_interface_mock()
        mock_apply.side_effect = RemoveFloatingIPFailed('rule del failed')

        op, _, _ = _make_net_iface_ip_op(
            self, [model_tasks.interface_defloat])
        op.state = NetIfaceIPOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.interface_defloat)

        self.assertEqual(NetIfaceIPOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()
        report_arg = mock_set_error.call_args[0][1]
        self.assertEqual('network.floating.remove_failed', report_arg.code)
