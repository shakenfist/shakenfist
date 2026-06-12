# Copyright 2019 Michael Still and contributors
"""Regression tests for NodeInstNetdescOp instance-start.

``instance_start`` reconciles the instance's networks onto this node and
enqueues the mesh/dnsmasq ops they need, then hands the actual creation
off to a deferred ``instance_create`` op that depends on those ops --
rather than blocking the worker on ``raise_for_error()``. (That
synchronous wait parked a sf-queues worker per op and, with the
per-hypervisor ensure_mesh fan-out, starved the pool under load.) These
tests guard that behaviour -- no synchronous wait, and a deferred create
op carrying the network ops as dependencies -- and the dnsmasq /
create_on_hypervisor routing.
"""

from contextlib import contextmanager
from unittest import mock
from uuid import uuid4

from shakenfist.operations.node_inst_netdesc_op import NodeInstNetdescOp
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.node_inst_netdesc_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_netdesc_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


# The operation looks ``create_and_enqueue`` up on its imported schema module
# at call time, so this is the path to patch to intercept the hand-off enqueue.
_ENQUEUE_PATH = (
    'shakenfist.schema.operations.node_inst_netdesc_op.create_and_enqueue')


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


def _make_net_op_mock():
    """A mock network op (mesh/dnsmasq) usable as a depends_on entry.

    ``_instance_start`` builds a ``dependency`` from the returned op's
    ``object_type`` and ``uuid``, which the schema validates, so both must
    be real values rather than bare MagicMock attributes.
    """
    op = mock.MagicMock()
    op.object_type = ObjectType.NET_OP
    op.uuid = uuid4()
    return op


def _make_op(test_case, node_uuid, instance_uuid, network_uuid, iface_uuid):
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


class InstanceStartHandsOffToDeferredCreateTestCase(base.ShakenFistTestCase):
    """instance_start enqueues a deferred create op instead of blocking."""

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
    def test_does_not_block_and_enqueues_deferred_create(
            self, mock_inst_from_db, mock_net_from_db, mock_ni_from_db,
            mock_apply_create):
        """The network ops are enqueued but never waited on inline; a
        deferred instance_create op is enqueued depending on them."""
        mock_inst_from_db.return_value = _make_inst_mock()
        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock
        mock_ni_from_db.return_value = _make_ni_mock()

        mesh_op = _make_net_op_mock()
        net_mock.ensure_mesh.return_value = mesh_op
        dnsmasq_op = _make_net_op_mock()
        net_mock.update_dnsmasq.return_value = dnsmasq_op

        op = _make_op(
            self, self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid)
        op.state = NodeInstNetdescOp.STATE_EXECUTING

        with mock.patch(_ENQUEUE_PATH) as mock_enqueue:
            op.dispatch_task(model_tasks.instance_start)

        # The networks are reconciled -- mesh and dnsmasq ops enqueued ...
        net_mock.ensure_mesh.assert_called_once()
        net_mock.update_dnsmasq.assert_called_once_with()
        # ... but the worker is never parked blocking on them.
        mesh_op.raise_for_error.assert_not_called()
        dnsmasq_op.raise_for_error.assert_not_called()

        # A single deferred instance_create op is enqueued, depending on
        # both network ops.
        mock_enqueue.assert_called_once()
        call = mock_enqueue.call_args
        self.assertEqual([model_tasks.instance_create], call.args[3])
        deps = call.kwargs['depends_on']
        self.assertEqual(2, len(deps))
        self.assertEqual(
            {mesh_op.uuid, dnsmasq_op.uuid}, {d.op_uuid for d in deps})

        self.assertNotEqual(NodeInstNetdescOp.STATE_ERROR, op.state.value)

    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_hypervisor')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Network.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Instance.from_db')
    def test_no_dnsmasq_op_yields_single_dependency(
            self, mock_inst_from_db, mock_net_from_db, mock_ni_from_db,
            mock_apply_create):
        """When update_dnsmasq returns None (no dhcp/dns) the create op
        depends on the mesh op alone."""
        mock_inst_from_db.return_value = _make_inst_mock()
        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock
        mock_ni_from_db.return_value = _make_ni_mock()

        mesh_op = _make_net_op_mock()
        net_mock.ensure_mesh.return_value = mesh_op
        net_mock.update_dnsmasq.return_value = None

        op = _make_op(
            self, self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid)
        op.state = NodeInstNetdescOp.STATE_EXECUTING

        with mock.patch(_ENQUEUE_PATH) as mock_enqueue:
            op.dispatch_task(model_tasks.instance_start)

        deps = mock_enqueue.call_args.kwargs['depends_on']
        self.assertEqual(1, len(deps))
        self.assertEqual(mesh_op.uuid, deps[0].op_uuid)


class UpdateDnsmasqRoutesThroughNetworkUpdateDnsmasqTestCase(
        base.ShakenFistTestCase):
    """Dnsmasq updates from ``instance_start`` enqueue a network-node op.

    ``_instance_start`` runs on the hypervisor where the instance is
    being placed. dnsmasq config files only live on the elected network
    node, so calling ``BridgedVXLanNetwork._apply_update_dnsmasq``
    directly here silently writes to an empty/absent local dnsmasq
    state -- the bug ``test_provided_dns`` was tripping over. The correct
    path is ``Network.update_dnsmasq``, which enqueues a ``net_op``
    against the cluster-wide ``networknode-*`` queue.
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
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_update_dnsmasq')
    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_hypervisor')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Network.from_db')
    @mock.patch('shakenfist.operations.node_inst_netdesc_op.Instance.from_db')
    def test_update_dnsmasq_enqueues_via_network_update_dnsmasq(
            self, mock_inst_from_db, mock_net_from_db, mock_ni_from_db,
            mock_apply_create, mock_apply_update_dnsmasq):
        """_instance_start enqueues Network.update_dnsmasq, not the
        worker-only _apply_ method, and does not block on it."""
        mock_inst_from_db.return_value = _make_inst_mock()
        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock
        mock_ni_from_db.return_value = _make_ni_mock()

        net_mock.ensure_mesh.return_value = _make_net_op_mock()
        dnsmasq_op = _make_net_op_mock()
        net_mock.update_dnsmasq.return_value = dnsmasq_op

        op = _make_op(
            self, self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid)
        op.state = NodeInstNetdescOp.STATE_EXECUTING

        with mock.patch(_ENQUEUE_PATH):
            op.dispatch_task(model_tasks.instance_start)

        net_mock.update_dnsmasq.assert_called_once_with()
        # The op is a dependency now, not a synchronous wait.
        dnsmasq_op.raise_for_error.assert_not_called()
        # The worker-only direct apply must NOT be called from a
        # hypervisor-side op -- the guard would raise if it were.
        mock_apply_update_dnsmasq.assert_not_called()


class CreateOnHypervisorRoutesThroughBridgedVXLanNetworkTestCase(base.ShakenFistTestCase):
    """The in-worker create_on_hypervisor call routes through BridgedVXLanNetwork.

    An inline ``n.create_on_hypervisor()`` call here would enqueue from
    inside the dispatcher and deadlock; the op uses
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
        mock_inst_from_db.return_value = _make_inst_mock()
        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock
        mock_ni_from_db.return_value = _make_ni_mock()

        net_mock.ensure_mesh.return_value = _make_net_op_mock()
        net_mock.update_dnsmasq.return_value = _make_net_op_mock()

        op = _make_op(
            self, self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid)
        op.state = NodeInstNetdescOp.STATE_EXECUTING

        with mock.patch(_ENQUEUE_PATH):
            op.dispatch_task(model_tasks.instance_start)

        mock_apply_create.assert_called_once_with()
        # Network.create_on_hypervisor must never be invoked from this
        # in-worker dispatcher path -- it would enqueue and deadlock.
        net_mock.create_on_hypervisor.assert_not_called()
