# Copyright 2026 Michael Still and contributors
#
# Instance.enqueue_delete() cancels the outstanding cluster operations which
# target the instance before it enqueues the delete. Snapshots are spared,
# and the delete waits for them as well as for the newest mutating
# operation. The mocked tests pin each case; the last test case runs the
# walk over sqlite copies of the cluster operation tables (see
# test_observational_current_state for the fixture).

from unittest import mock
from uuid import uuid4

from shakenfist.baseobject import TargetedOperation
from shakenfist import exceptions
from shakenfist import instance
from shakenfist.operations.baseoperation import BaseClusterOperation as bco
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations import node_inst_net_iface_op
from shakenfist.schema.operations import node_inst_op
from shakenfist.schema.operations import node_inst_snap_op
from shakenfist.schema.operations import node_net_op
from shakenfist.tests import base
from shakenfist.tests.test_observational_current_state import ClusterOperationTablesFixture


NODE_UUID = 'aaaa4444-4444-4444-8444-444444444444'
INSTANCE_UUID = 'bbbb4444-4444-4444-8444-444444444444'
NETWORK_UUID = 'cccc4444-4444-4444-8444-444444444444'
INTERFACE_UUID = 'dddd4444-4444-4444-8444-444444444444'

HOT_PLUG_TASKS = [
    node_inst_net_iface_op.model_tasks.hot_plug_instance_interface]
SWEEP_TASKS = [
    node_inst_op.model_tasks.collect_billing_statistics,
    node_inst_op.model_tasks.health_check_kvm_process,
]
SNAPSHOT_TASKS = [node_inst_snap_op.model_tasks.instance_snapshot]


def _instance():
    return instance.Instance({
        'uuid': INSTANCE_UUID,
        'version': instance.Instance.current_version,
        'disk_spec': [{'base': 'debian:12', 'size': 8, 'bus': None,
                       'type': 'disk'}],
    })


def _op(object_type, tasks, abort_raises=False):
    op = mock.MagicMock()
    op.object_type = object_type
    op.uuid = uuid4()
    op.tasks = tasks
    op.states = []

    def set_state(value):
        if abort_raises:
            raise exceptions.InvalidStateException('already terminal')
        op.states.append(value)

    type(op).state = mock.PropertyMock(side_effect=set_state)
    return op


def _pointer(op):
    return {'op_type': op.object_type, 'op_uuid': str(op.uuid)}


class InstanceEnqueueDeleteTestCase(base.ShakenFistTestCase):

    def _delete(self, targeted, pointer=None):
        """Run enqueue_delete() over a mocked target listing.

        Returns the runs_after the delete was enqueued with.
        """
        inst = _instance()
        with mock.patch.object(
                instance.Instance, 'placement',
                new_callable=mock.PropertyMock,
                return_value={'node': NODE_UUID}), \
                mock.patch.object(
                    instance.Instance, 'last_cluster_operation',
                    new_callable=mock.PropertyMock, return_value=pointer), \
                mock.patch.object(
                    instance.Instance, 'get_cluster_operations',
                    return_value=targeted) as self.get_ops, \
                mock.patch('shakenfist.instance.add_event_multi') as self.events, \
                mock.patch('shakenfist.instance.nio_create_and_enqueue') as enqueue:
            inst.enqueue_delete()

        enqueue.assert_called_once()
        args, kwargs = enqueue.call_args
        self.assertEqual(NODE_UUID, args[0])
        self.assertEqual(
            [node_inst_op.model_tasks.instance_delete], args[2])
        return kwargs['runs_after']

    def _aborted_ops(self):
        return [c.args[1][1] for c in self.events.call_args_list]

    def test_queued_hot_plug_and_sweep_are_aborted(self):
        hot_plug = _op(ObjectType.NODE_INST_NET_IFACE_OP, HOT_PLUG_TASKS)
        sweep = _op(ObjectType.NODE_INST_OP, SWEEP_TASKS)

        runs_after = self._delete(
            [TargetedOperation(sweep, True),
             TargetedOperation(hot_plug, False)],
            pointer=_pointer(hot_plug))

        self.assertEqual([bco.STATE_ABORT], hot_plug.states)
        self.assertEqual([bco.STATE_ABORT], sweep.states)
        self.assertEqual([sweep, hot_plug], self._aborted_ops())
        self.assertEqual([_pointer(hot_plug)], runs_after)

    def test_snapshot_is_spared_and_waited_for(self):
        snap = _op(ObjectType.NODE_INST_SNAP_OP, SNAPSHOT_TASKS)

        runs_after = self._delete(
            [TargetedOperation(snap, False)], pointer=_pointer(snap))

        self.assertEqual([], snap.states)
        self.assertEqual([], self._aborted_ops())
        # The snapshot is both a survivor and the pointer, so it is listed
        # once.
        self.assertEqual([_pointer(snap)], runs_after)

    def test_snapshot_behind_newer_hot_plug_is_waited_for(self):
        # The pointer is the hot-plug, so waiting only for the pointer would
        # let the delete run under the snapshot.
        snap = _op(ObjectType.NODE_INST_SNAP_OP, SNAPSHOT_TASKS)
        hot_plug = _op(ObjectType.NODE_INST_NET_IFACE_OP, HOT_PLUG_TASKS)

        runs_after = self._delete(
            [TargetedOperation(hot_plug, False),
             TargetedOperation(snap, False)],
            pointer=_pointer(hot_plug))

        self.assertEqual([bco.STATE_ABORT], hot_plug.states)
        self.assertEqual([], snap.states)
        self.assertEqual([_pointer(snap), _pointer(hot_plug)], runs_after)

    def test_op_which_cannot_be_aborted_is_not_waited_for(self):
        finished = _op(ObjectType.NODE_INST_OP, SWEEP_TASKS,
                       abort_raises=True)

        runs_after = self._delete([TargetedOperation(finished, True)])

        finished.add_event.assert_called_once()
        self.assertEqual([None], runs_after)

    def test_nothing_outstanding_waits_for_the_pointer(self):
        pointer = {'op_type': ObjectType.NODE_INST_OP,
                   'op_uuid': str(uuid4())}
        self.assertEqual([pointer], self._delete([], pointer=pointer))
        self.assertEqual([None], self._delete([]))

    def test_only_outstanding_ops_are_listed(self):
        self._delete([])
        self.get_ops.assert_called_once_with(outstanding_only=True)


class InstanceEnqueueDeleteSqlTestCase(
        ClusterOperationTablesFixture, base.ShakenFistTestCase):

    def test_delete_walk(self):
        _, snap = node_inst_snap_op.create_and_enqueue(
            NODE_UUID, INSTANCE_UUID,
            [node_inst_snap_op.snapshot(
                disk={'device': 'vda'}, artifact_uuid=str(uuid4()),
                blob_uuid=str(uuid4()), thin=False)],
            SNAPSHOT_TASKS, PRIORITY.user_facing_high_io)
        _, net = node_net_op.create_and_enqueue(
            NODE_UUID, NETWORK_UUID,
            [node_net_op.model_tasks.network_apply_create_hypervisor],
            PRIORITY.user_facing)
        _, hot_plug = node_inst_net_iface_op.create_and_enqueue(
            NODE_UUID, INSTANCE_UUID, NETWORK_UUID, INTERFACE_UUID,
            HOT_PLUG_TASKS, PRIORITY.user_facing,
            depends_on=[{'op_type': ObjectType.NODE_NET_OP, 'op_uuid': net}])
        _, sweep = node_inst_op.create_and_enqueue(
            NODE_UUID, INSTANCE_UUID, SWEEP_TASKS, PRIORITY.background)

        inst = _instance()
        with mock.patch.object(
                instance.Instance, 'placement',
                new_callable=mock.PropertyMock,
                return_value={'node': NODE_UUID}), \
                mock.patch('shakenfist.instance.add_event_multi'), \
                mock.patch('shakenfist.eventlog.add_event'):
            inst.enqueue_delete()

            # The delete is now the newest mutating operation.
            delete = inst.get_cluster_operations()[0].op

        self.assertEqual('queued', self._op_state(snap))
        self.assertEqual('abort', self._op_state(hot_plug))
        self.assertEqual('abort', self._op_state(sweep))
        self.assertEqual('queued', self._op_state(net))

        self.assertEqual(
            [node_inst_op.model_tasks.instance_delete], delete.tasks)
        self.assertEqual(
            [(ObjectType.NODE_INST_SNAP_OP, snap),
             (ObjectType.NODE_INST_NET_IFACE_OP, hot_plug)],
            [(d['op_type'], d['op_uuid']) for d in delete.runs_after])
