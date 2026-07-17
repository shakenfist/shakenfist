# Copyright 2019 Michael Still and contributors
"""Regression tests for NodeInstNetIfaceOp hot-plug.

``hot_plug_instance_interface`` reconciles the network onto this node and
enqueues the mesh op it needs, then hands the actual libvirt attach off to
a deferred ``attach_instance_interface`` op that depends on that mesh op --
rather than blocking the worker on ``raise_for_error()``. (That synchronous
wait parked a sf-queues worker per op and, with the per-hypervisor
ensure_mesh fan-out, starved the pool under load -- causing agent execute
ops sharing the same queues to time out, surfacing as AgentAwaitTimeout in
the interface hot-plug functional test.) These tests guard that behaviour:
no synchronous wait, and a deferred attach op carrying the mesh op as a
dependency.
"""

from unittest import mock
from uuid import uuid4

from shakenfist.operations.node_inst_net_iface_op import NodeInstNetIfaceOp
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.node_inst_net_iface_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_net_iface_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


# The operation looks ``create_and_enqueue`` up on its imported schema module
# at call time, so this is the path to patch to intercept the hand-off enqueue.
_ENQUEUE_PATH = (
    'shakenfist.schema.operations.node_inst_net_iface_op.create_and_enqueue')


def _make_inst_mock():
    """Build a minimal Instance-like mock."""
    inst = mock.MagicMock()
    inst.uuid = str(uuid4())
    mock_state = mock.MagicMock()
    mock_state.value = 'created'
    inst.state = mock_state
    return inst


def _make_net_mock(network_uuid):
    """Build a minimal Network-like mock."""
    n = mock.MagicMock()
    n.uuid = network_uuid
    return n


def _make_ni_mock():
    """Build a minimal NetworkInterface-like mock with no floating IP."""
    ni = mock.MagicMock()
    ni.uuid = str(uuid4())
    ni.floating = {'floating_address': None}
    return ni


def _make_net_op_mock():
    """A mock network op (mesh) usable as a depends_on entry.

    ``_hot_plug_instance_interface`` builds a ``dependency`` from the
    returned op's ``object_type`` and ``uuid``, which the schema validates,
    so both must be real values rather than bare MagicMock attributes.
    """
    op = mock.MagicMock()
    op.object_type = ObjectType.NET_OP
    op.uuid = uuid4()
    return op


def _make_op(test_case, node_uuid, instance_uuid, network_uuid, iface_uuid):
    """Create a NodeInstNetIfaceOp carrying the hot-plug task."""
    _, op_uuid = create_and_enqueue(
        node_uuid=str(node_uuid),
        instance_uuid=str(instance_uuid),
        network_uuid=str(network_uuid),
        interface_uuid=str(iface_uuid),
        tasks=[model_tasks.hot_plug_instance_interface],
        priority=PRIORITY.user_facing,
    )
    op = NodeInstNetIfaceOp.from_db(op_uuid)
    test_case.assertIsNotNone(op)
    return op


class HotPlugHandsOffToDeferredAttachTestCase(base.ShakenFistTestCase):
    """hot_plug enqueues a deferred attach op instead of blocking."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        self.node_uuid = uuid4()
        self.instance_uuid = uuid4()
        self.network_uuid = uuid4()
        self.iface_uuid = uuid4()

    @mock.patch(
        'shakenfist.network.bridged_vxlan_network.BridgedVXLanNetwork._apply_create_on_hypervisor')
    @mock.patch('shakenfist.operations.node_inst_net_iface_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.node_inst_net_iface_op.Network.from_db')
    @mock.patch('shakenfist.operations.node_inst_net_iface_op.Instance.from_db')
    def test_does_not_block_and_enqueues_deferred_attach(
            self, mock_inst_from_db, mock_net_from_db, mock_ni_from_db,
            mock_apply_create):
        """The mesh op is enqueued but never waited on inline; a deferred
        attach op is enqueued depending on it, and libvirt is not touched."""
        inst_mock = _make_inst_mock()
        mock_inst_from_db.return_value = inst_mock
        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock
        mock_ni_from_db.return_value = _make_ni_mock()

        mesh_op = _make_net_op_mock()
        net_mock.ensure_mesh.return_value = mesh_op

        op = _make_op(
            self, self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid)
        op.state = NodeInstNetIfaceOp.STATE_EXECUTING

        with mock.patch(_ENQUEUE_PATH) as mock_enqueue:
            op.dispatch_task(model_tasks.hot_plug_instance_interface)

        # The network is reconciled and the mesh op enqueued ...
        mock_apply_create.assert_called_once_with()
        net_mock.ensure_mesh.assert_called_once()
        # ... but the worker is never parked blocking on it, and the actual
        # libvirt attach has not happened yet.
        mesh_op.raise_for_error.assert_not_called()
        inst_mock.hot_plug_interface.assert_not_called()

        # A single deferred attach op is enqueued, depending on the mesh op.
        mock_enqueue.assert_called_once()
        call = mock_enqueue.call_args
        self.assertEqual(
            [model_tasks.attach_instance_interface], call.args[4])
        deps = call.kwargs['depends_on']
        self.assertEqual(1, len(deps))
        self.assertEqual(mesh_op.uuid, deps[0].op_uuid)

        self.assertNotEqual(NodeInstNetIfaceOp.STATE_ERROR, op.state.value)

    @mock.patch('shakenfist.operations.node_inst_net_iface_op.NetworkInterface.from_db')
    @mock.patch('shakenfist.operations.node_inst_net_iface_op.Network.from_db')
    @mock.patch('shakenfist.operations.node_inst_net_iface_op.Instance.from_db')
    def test_attach_task_performs_libvirt_attach(
            self, mock_inst_from_db, mock_net_from_db, mock_ni_from_db):
        """The follow-up attach task performs the libvirt hot plug and does
        not re-reconcile the network."""
        inst_mock = _make_inst_mock()
        mock_inst_from_db.return_value = inst_mock
        net_mock = _make_net_mock(self.network_uuid)
        mock_net_from_db.return_value = net_mock
        ni_mock = _make_ni_mock()
        mock_ni_from_db.return_value = ni_mock

        op = _make_op(
            self, self.node_uuid, self.instance_uuid,
            self.network_uuid, self.iface_uuid)
        op.state = NodeInstNetIfaceOp.STATE_EXECUTING

        with mock.patch(_ENQUEUE_PATH) as mock_enqueue:
            op.dispatch_task(model_tasks.attach_instance_interface)

        inst_mock.hot_plug_interface.assert_called_once_with(net_mock, ni_mock)
        # The attach task must not reconcile the network again.
        net_mock.ensure_mesh.assert_not_called()
        # No floating address on this interface, so no float op is enqueued.
        mock_enqueue.assert_not_called()
        self.assertNotEqual(NodeInstNetIfaceOp.STATE_ERROR, op.state.value)
