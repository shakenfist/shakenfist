# Copyright 2019 Michael Still and contributors
"""Tests for ``shakenfist.operations.net_iface_op.NetIfaceOp.dispatch_task``.

Covers the Phase 3 migration of ``_interface_float`` to
BridgedVXLanNetwork and the multi-target audit event emitted at dispatch
time to preserve event correlation. Also covers ErrorReport persistence
on the outer except.
"""

from unittest import mock
from uuid import uuid4

from shakenfist.exceptions import AddFloatingIPFailed
from shakenfist.operations.net_iface_op import NetIfaceOp
from shakenfist.operations.net_iface_op import NoSuchNetworkInterface
from shakenfist.schema.operations.net_iface_op import create_and_enqueue
from shakenfist.schema.operations.net_iface_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


def _make_network_mock(is_dead=False):
    network = mock.MagicMock()
    network.is_dead.return_value = is_dead
    network.uuid = str(uuid4())
    return network


def _make_interface_mock(floating_address='192.0.2.20',
                         inner_ipv4='10.0.0.20'):
    ni = mock.MagicMock()
    ni.floating = {'floating_address': floating_address} if floating_address else {}
    ni.ipv4 = inner_ipv4
    ni.instance_uuid = str(uuid4())
    return ni


def _make_net_iface_op(test_case, tasks, network_uuid=None,
                       interface_uuid=None):
    if network_uuid is None:
        network_uuid = str(uuid4())
    if interface_uuid is None:
        interface_uuid = str(uuid4())
    _, op_uuid = create_and_enqueue(
        network_uuid=network_uuid,
        interface_uuid=interface_uuid,
        tasks=tasks,
        priority=PRIORITY.user_waiting,
    )
    op = NetIfaceOp.from_db(op_uuid)
    test_case.assertIsNotNone(op)
    return op, network_uuid, interface_uuid


class InterfaceFloatTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_interface_float`` routes through BridgedVXLanNetwork and emits the audit event."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    @mock.patch('shakenfist.operations.net_iface_op.add_event_multi')
    @mock.patch('shakenfist.operations.net_iface_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_add_floating_ip')
    @mock.patch('shakenfist.operations.net_iface_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.net_iface_op.Network.from_db')
    def test_interface_float_calls_apply_add_floating_ip(
            self, mock_network_from_db, mock_iface_from_db, mock_apply,
            mock_set_error, mock_add_event_multi):
        """Dispatching interface_float delegates to BridgedVXLanNetwork."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network
        ni = _make_interface_mock(
            floating_address='192.0.2.21', inner_ipv4='10.0.0.21')
        mock_iface_from_db.return_value = ni

        op, _, _ = _make_net_iface_op(self, [model_tasks.interface_float])
        op.state = NetIfaceOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.interface_float)

        mock_apply.assert_called_once_with('192.0.2.21', '10.0.0.21')
        # Network.add_floating_ip must never be called from the dispatcher.
        network.add_floating_ip.assert_not_called()
        mock_set_error.assert_not_called()

        # Multi-target audit event should include the op, network and ni.
        mock_add_event_multi.assert_called_once()
        args, kwargs = mock_add_event_multi.call_args
        targets = args[1]
        self.assertIn(op, targets)
        self.assertIn(network, targets)
        self.assertIn(ni, targets)

    @mock.patch('shakenfist.operations.net_iface_op.add_event_multi')
    @mock.patch('shakenfist.operations.net_iface_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_add_floating_ip')
    @mock.patch('shakenfist.operations.net_iface_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.net_iface_op.Network.from_db')
    def test_interface_float_skipped_when_address_released(
            self, mock_network_from_db, mock_iface_from_db, mock_apply,
            mock_set_error, mock_add_event_multi):
        """A float whose address was released by a concurrent defloat or delete is skipped cleanly."""
        network = _make_network_mock()
        mock_network_from_db.return_value = network
        ni = _make_interface_mock(floating_address=None)
        mock_iface_from_db.return_value = ni

        op, _, _ = _make_net_iface_op(self, [model_tasks.interface_float])
        op.state = NetIfaceOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.interface_float)

        # No plumbing, no error report, and the op is not in an error state.
        mock_apply.assert_not_called()
        mock_set_error.assert_not_called()
        self.assertEqual(NetIfaceOp.STATE_EXECUTING, op.state.value)

        # A single audit event records the skip against the op, network,
        # interface and instance.
        mock_add_event_multi.assert_called_once()
        args, kwargs = mock_add_event_multi.call_args
        targets = args[1]
        self.assertIn(op, targets)
        self.assertIn(network, targets)
        self.assertIn(ni, targets)
        self.assertIn(('instance', ni.instance_uuid), targets)
        self.assertEqual(
            'add floating IP superseded by defloat or delete, skipped',
            args[2])

    @mock.patch('shakenfist.operations.net_iface_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.net_iface_op.Network.from_db')
    def test_missing_interface_raises_no_such_network_interface(
            self, mock_network_from_db, mock_iface_from_db):
        """A hard-deleted interface raises NoSuchNetworkInterface, not AttributeError."""
        mock_network_from_db.return_value = _make_network_mock()
        mock_iface_from_db.return_value = None

        op, _, _ = _make_net_iface_op(self, [model_tasks.interface_float])
        op.state = NetIfaceOp.STATE_EXECUTING
        self.assertRaises(
            NoSuchNetworkInterface, op.dispatch_task,
            model_tasks.interface_float)


class InterfaceFloatExceptionTestCase(base.ShakenFistTestCase):
    """Exceptions from _apply_add_floating_ip persist an ErrorReport."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    @mock.patch('shakenfist.operations.net_iface_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_add_floating_ip')
    @mock.patch('shakenfist.operations.net_iface_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.net_iface_op.Network.from_db')
    def test_add_floating_ip_failed_persists_report_and_sets_error(
            self, mock_network_from_db, mock_iface_from_db, mock_apply,
            mock_set_error):
        """AddFloatingIPFailed persists a report and sets STATE_ERROR."""
        mock_network_from_db.return_value = _make_network_mock()
        mock_iface_from_db.return_value = _make_interface_mock()
        mock_apply.side_effect = AddFloatingIPFailed('rule add failed')

        op, _, _ = _make_net_iface_op(self, [model_tasks.interface_float])
        op.state = NetIfaceOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.interface_float)

        self.assertEqual(NetIfaceOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()
        report_arg = mock_set_error.call_args[0][1]
        self.assertEqual('network.floating.add_failed', report_arg.code)
