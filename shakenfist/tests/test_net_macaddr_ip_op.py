# Copyright 2019 Michael Still and contributors
"""Tests for ``shakenfist.operations.net_macaddr_ip_op.NetMacaddrIPOp``.

Covers the ``_remove_dhcp_lease`` handler routing through
``BridgedVXLanNetwork._apply_remove_dhcp_lease`` and the
ErrorReport-persistence wiring on the dispatcher's outer except.
"""

from unittest import mock
from uuid import uuid4

from shakenfist.operations.net_macaddr_ip_op import NetMacaddrIPOp
from shakenfist.schema.operations.net_macaddr_ip_op import create_and_enqueue
from shakenfist.schema.operations.net_macaddr_ip_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


def _make_network_mock(is_dead=False):
    """Build a minimal Network-like mock for dispatch_task tests."""
    network = mock.MagicMock()
    network.is_dead.return_value = is_dead
    return network


def _make_op(test_case, network_uuid=None, mac_address='52:54:00:12:34:56',
             ip='10.0.0.5'):
    """Enqueue and load a NetMacaddrIPOp with the remove_dhcp_lease task."""
    if network_uuid is None:
        network_uuid = str(uuid4())
    _, op_uuid = create_and_enqueue(
        network_uuid=network_uuid,
        mac_address=mac_address,
        ip=ip,
        tasks=[model_tasks.remove_dhcp_lease],
        priority=PRIORITY.user_facing,
    )
    op = NetMacaddrIPOp.from_db(op_uuid)
    test_case.assertIsNotNone(op)
    return op


class RemoveDhcpLeaseTaskDispatchTestCase(base.ShakenFistTestCase):
    """``_remove_dhcp_lease`` routes through BridgedVXLanNetwork."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_macaddr_ip_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_remove_dhcp_lease')
    @mock.patch('shakenfist.operations.net_macaddr_ip_op.Network.from_db')
    def test_remove_dhcp_lease_handler_calls_apply_remove_dhcp_lease(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """Dispatching remove_dhcp_lease delegates to BridgedVXLanNetwork."""
        network = _make_network_mock(is_dead=False)
        mock_network_from_db.return_value = network

        op = _make_op(self, mac_address='52:54:00:12:34:56', ip='10.0.0.5')
        op.state = NetMacaddrIPOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.remove_dhcp_lease)

        # _apply_remove_dhcp_lease must be invoked with the op's static values.
        # IPv4Address is parsed by Pydantic; compare the string form.
        mock_apply.assert_called_once()
        args = mock_apply.call_args[0]
        self.assertEqual('10.0.0.5', str(args[0]))
        self.assertEqual('52:54:00:12:34:56', str(args[1]))
        # Network.remove_dhcp_lease must never be invoked from the dispatcher.
        network.remove_dhcp_lease.assert_not_called()
        mock_set_error.assert_not_called()

    @mock.patch('shakenfist.operations.net_macaddr_ip_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_remove_dhcp_lease')
    @mock.patch('shakenfist.operations.net_macaddr_ip_op.Network.from_db')
    def test_dead_network_skips_apply_remove_dhcp_lease(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """A dead network short-circuits before calling BridgedVXLanNetwork."""
        network = _make_network_mock(is_dead=True)
        mock_network_from_db.return_value = network

        op = _make_op(self)
        op.state = NetMacaddrIPOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.remove_dhcp_lease)

        mock_apply.assert_not_called()
        mock_set_error.assert_not_called()


class RemoveDhcpLeaseExceptionTestCase(base.ShakenFistTestCase):
    """Exceptions from _apply_remove_dhcp_lease persist an ErrorReport."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    @mock.patch('shakenfist.operations.net_macaddr_ip_op.mariadb.set_cluster_operation_error')
    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_remove_dhcp_lease')
    @mock.patch('shakenfist.operations.net_macaddr_ip_op.Network.from_db')
    def test_generic_exception_sets_error_and_persists_internal_unknown(
            self, mock_network_from_db, mock_apply, mock_set_error):
        """An unregistered exception produces code='internal.unknown' in the report."""
        network = _make_network_mock(is_dead=False)
        mock_network_from_db.return_value = network
        mock_apply.side_effect = RuntimeError('lease removal failed')

        op = _make_op(self)
        op.state = NetMacaddrIPOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.remove_dhcp_lease)

        self.assertEqual(NetMacaddrIPOp.STATE_ERROR, op.state.value)
        mock_set_error.assert_called_once()
        report_arg = mock_set_error.call_args[0][1]
        self.assertEqual('internal.unknown', report_arg.code)
