# Copyright 2019 Michael Still and contributors
"""Regression tests for NodeInstNetdescOp — ensure_mesh caller update (step 2g).

Verifies that when ``Network.ensure_mesh()`` returns an op whose
``raise_for_error()`` raises ``NetworkOperationFailed``, the exception
propagates through ``_instance_start`` and is captured by
``dispatch_task``'s generic exception handler, ending the op in
``STATE_ERROR``.
"""

from contextlib import contextmanager
from unittest import mock
from uuid import uuid4

from shakenfist.exceptions import NetworkOperationFailed
from shakenfist.operations.error_report import ErrorReport
from shakenfist.operations.node_inst_netdesc_op import NodeInstNetdescOp
from shakenfist.schema.operations.node_inst_netdesc_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_netdesc_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


def _make_fake_error_report():
    """Build a minimal ErrorReport for NetworkOperationFailed."""
    return ErrorReport(
        code='network.ensure_mesh.failed',
        message='mesh could not be ensured',
        details={},
        origin_class='shakenfist.exceptions.EnsureMeshFailed',
        traceback='',
    )


def _make_inst_mock():
    """Build a minimal Instance-like mock in a non-terminal, non-error state."""
    inst = mock.MagicMock()
    mock_state = mock.MagicMock()
    mock_state.value = 'created'
    inst.state = mock_state
    # Ensure TERMINAL_STATES check passes (inst.state.value not in TERMINAL_STATES)
    inst.TERMINAL_STATES = {'deleted', 'delete-wait', 'error'}

    @contextmanager
    def fake_lock(**kwargs):
        yield

    inst.get_lock = fake_lock
    return inst


def _make_net_mock(network_uuid):
    """Build a minimal Network-like mock in STATE_CREATED."""
    n = mock.MagicMock()
    mock_state = mock.MagicMock()
    mock_state.value = 'created'
    n.state = mock_state
    n.STATE_CREATED = 'created'
    n.uuid = network_uuid
    return n


def _make_ni_mock():
    """Build a minimal NetworkInterface-like mock in an active state."""
    ni = mock.MagicMock()
    mock_state = mock.MagicMock()
    mock_state.value = 'created'
    ni.state = mock_state
    ni.ACTIVE_STATES = {'initial', 'creating', 'created', 'error', 'delete-wait'}
    ni.floating = {'floating_address': None}
    return ni


def _make_op(test_case, mock_etcd, node_uuid, instance_uuid, network_uuid, iface_uuid):
    """Create a NodeInstNetdescOp with a single network in net_desc."""
    net_desc = [{'network_uuid': str(network_uuid), 'iface_uuid': str(iface_uuid)}]
    _, op_uuid = create_and_enqueue(
        node_uuid=str(node_uuid),
        instance_uuid=str(instance_uuid),
        net_desc=net_desc,
        tasks=[model_tasks.instance_start],
        priority=PRIORITY.user_facing,
    )
    op = NodeInstNetdescOp.from_db(op_uuid)
    test_case.assertIsNotNone(op)
    return op


class EnsureMeshRaiseForErrorPropagatesTestCase(base.ShakenFistTestCase):
    """When raise_for_error() raises NetworkOperationFailed, dispatch_task
    sets STATE_ERROR on the NodeInstNetdescOp."""

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

        self.node_uuid = uuid4()
        self.instance_uuid = uuid4()
        self.network_uuid = uuid4()
        self.iface_uuid = uuid4()

    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_hypervisor')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Network.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Instance.from_db')
    def test_ensure_mesh_network_op_failed_sets_state_error(
            self, mock_inst_from_db, mock_net_from_db, mock_ni_from_db,
            mock_apply_create):
        """NetworkOperationFailed from raise_for_error() results in STATE_ERROR."""
        inst_mock = _make_inst_mock()
        mock_inst_from_db.return_value = inst_mock

        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock

        ni_mock = _make_ni_mock()
        mock_ni_from_db.return_value = ni_mock

        # ensure_mesh() returns an op handle; raise_for_error() raises
        error_report = _make_fake_error_report()
        fake_mesh_op = mock.MagicMock()
        fake_mesh_op.raise_for_error.side_effect = NetworkOperationFailed(error_report)
        net_mock.ensure_mesh.return_value = fake_mesh_op

        op = _make_op(
            self, self.mock_etcd,
            self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid,
        )
        op.state = NodeInstNetdescOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.instance_start)

        # NetworkOperationFailed is not AbortInstanceStart; it hits the
        # generic except branch and sets STATE_ERROR.
        self.assertEqual(NodeInstNetdescOp.STATE_ERROR, op.state.value)
        fake_mesh_op.raise_for_error.assert_called_once()

    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_update_dnsmasq')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_hypervisor')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Network.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Instance.from_db')
    def test_ensure_mesh_success_continues_normally(
            self, mock_inst_from_db, mock_net_from_db, mock_ni_from_db,
            mock_apply_create, mock_apply_update_dnsmasq):
        """When raise_for_error() returns None, execution continues past ensure_mesh."""
        inst_mock = _make_inst_mock()
        mock_inst_from_db.return_value = inst_mock

        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock

        ni_mock = _make_ni_mock()
        mock_ni_from_db.return_value = ni_mock

        # ensure_mesh() returns an op handle whose raise_for_error() is a no-op
        fake_mesh_op = mock.MagicMock()
        fake_mesh_op.raise_for_error.return_value = None
        net_mock.ensure_mesh.return_value = fake_mesh_op

        op = _make_op(
            self, self.mock_etcd,
            self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid,
        )
        op.state = NodeInstNetdescOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.instance_start)

        # ensure_mesh was called and raise_for_error was invoked
        net_mock.ensure_mesh.assert_called_once()
        fake_mesh_op.raise_for_error.assert_called_once()
        # The op should NOT be in STATE_ERROR (it reached further into _instance_start)
        self.assertNotEqual(NodeInstNetdescOp.STATE_ERROR, op.state.value)


class UpdateDnsmasqRoutesThroughNetworkUpdateDnsmasqTestCase(
        base.ShakenFistTestCase):
    """Dnsmasq updates from ``instance_start`` enqueue a network-node op.

    ``_instance_start`` runs on the hypervisor where the instance is
    being placed. dnsmasq config files only live on the elected network
    node, so calling ``BridgedVXLanNetwork._apply_update_dnsmasq``
    directly here silently writes to an empty/absent local dnsmasq
    state -- the bug ``test_provided_dns`` was tripping over (see the
    ``_require_network_node`` guard added in
    ``BridgedVXLanNetwork``). The correct path is to call
    ``Network.update_dnsmasq`` which enqueues a ``net_op`` against the
    cluster-wide ``networknode-*`` queue. This is not a self-enqueue
    deadlock because the calling op is on a per-node queue, not on the
    network-node queue.
    """

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

        self.node_uuid = uuid4()
        self.instance_uuid = uuid4()
        self.network_uuid = uuid4()
        self.iface_uuid = uuid4()

    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_hypervisor')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_update_dnsmasq')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Network.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Instance.from_db')
    def test_update_dnsmasq_enqueues_via_network_update_dnsmasq(
            self, mock_inst_from_db, mock_net_from_db, mock_ni_from_db,
            mock_apply_update_dnsmasq, mock_apply_create):
        """_instance_start enqueues Network.update_dnsmasq, not the
        worker-only _apply_ method."""
        inst_mock = _make_inst_mock()
        mock_inst_from_db.return_value = inst_mock

        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock

        ni_mock = _make_ni_mock()
        mock_ni_from_db.return_value = ni_mock

        fake_mesh_op = mock.MagicMock()
        fake_mesh_op.raise_for_error.return_value = None
        net_mock.ensure_mesh.return_value = fake_mesh_op

        # update_dnsmasq returns either an op (to poll) or None
        # (network has neither dhcp nor dns). The handler must call
        # raise_for_error on the non-None case.
        fake_dnsmasq_op = mock.MagicMock()
        fake_dnsmasq_op.raise_for_error.return_value = None
        net_mock.update_dnsmasq.return_value = fake_dnsmasq_op

        op = _make_op(
            self, self.mock_etcd,
            self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid,
        )
        op.state = NodeInstNetdescOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.instance_start)

        net_mock.update_dnsmasq.assert_called_once_with()
        fake_dnsmasq_op.raise_for_error.assert_called_once()
        # The worker-only direct apply must NOT be called from a
        # hypervisor-side op -- the guard would raise if it were.
        mock_apply_update_dnsmasq.assert_not_called()


class CreateOnHypervisorRoutesThroughBridgedVXLanNetworkTestCase(base.ShakenFistTestCase):
    """The in-worker create_on_hypervisor call routes through BridgedVXLanNetwork.

    After step 5d flips ``Network.create_on_hypervisor`` to enqueue, an
    inline ``n.create_on_hypervisor()`` call here would enqueue from
    inside the dispatcher and deadlock. Step 5c switches the call to
    ``BridgedVXLanNetwork(n)._apply_create_on_hypervisor()`` to keep it
    synchronous.
    """

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

        self.node_uuid = uuid4()
        self.instance_uuid = uuid4()
        self.network_uuid = uuid4()
        self.iface_uuid = uuid4()

    @mock.patch('shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_update_dnsmasq')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_hypervisor')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Network.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Instance.from_db')
    def test_create_on_hypervisor_calls_apply_not_n_create_on_hypervisor(
            self, mock_inst_from_db, mock_net_from_db, mock_ni_from_db,
            mock_apply_create, mock_apply_update_dnsmasq):
        """_instance_start invokes BridgedVXLanNetwork._apply_create_on_hypervisor."""
        inst_mock = _make_inst_mock()
        mock_inst_from_db.return_value = inst_mock

        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock

        ni_mock = _make_ni_mock()
        mock_ni_from_db.return_value = ni_mock

        fake_mesh_op = mock.MagicMock()
        fake_mesh_op.raise_for_error.return_value = None
        net_mock.ensure_mesh.return_value = fake_mesh_op

        op = _make_op(
            self, self.mock_etcd,
            self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid,
        )
        op.state = NodeInstNetdescOp.STATE_EXECUTING
        op.dispatch_task(model_tasks.instance_start)

        mock_apply_create.assert_called_once_with()
        # Network.create_on_hypervisor must never be invoked from this
        # in-worker dispatcher path -- after step 5d it would enqueue
        # and deadlock.
        net_mock.create_on_hypervisor.assert_not_called()
