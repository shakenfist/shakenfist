# Copyright 2026 Michael Still and contributors
"""Tests for sf-queues re-running the work its previous process abandoned.

When sf-queues stopped with a worker still running -- SIGKILLed by systemd
during a deploy, for one -- the worker's operation stayed executing and its
work item claimed. The restarted daemon never cleared that claim (it reset
queues named after the node's name, but they are named after its uuid), and
had it done so the next worker would have skipped the executing operation and
resolved its item, stranding it. The reaper errored it 30 minutes later, long
after the instance waiting on it had given up (#4543).
"""

import types
from unittest import mock

from shakenfist import exceptions
from shakenfist.constants import NoSuchObject
from shakenfist.daemons.queues import startup_tasks
from shakenfist.exceptions import InvalidStateException
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_node_background_node_queues
from shakenfist.operations.baseoperation import get_node_user_facing_node_queues
from shakenfist.tests import base


NODE_UUID = '6a3c5a8e-0c8a-4a8f-9d77-6f1f4f0f8d11'
OWN_QUEUE = f'{NODE_UUID}-clusteroperation-user_facing'
OTHER_NODE_QUEUE = 'f00dfeed-0000-4000-8000-000000000000-clusteroperation-user_facing'
SHARED_QUEUE = 'any-clusteroperation-user_facing'


class FakeOp:
    def __init__(self, state, refuse=False):
        self._state = state
        self.refuse = refuse
        self.events = []

    @property
    def state(self):
        return types.SimpleNamespace(value=self._state)

    @state.setter
    def state(self, new_state):
        if self.refuse:
            raise InvalidStateException('already terminal')
        self._state = new_state

    def add_event(self, event_type, message, extra=None):
        self.events.append(message)


def row(queue_name, op_uuid, attempts=1, row_id=1):
    return {
        'id': row_id,
        'queue_name': queue_name,
        'claimed_at': 1000.0,
        'claimed_by': 'sf-1',
        'attempts': attempts,
        'payload': {'operation_type': 'artifact_fetch_op', 'operation_uuid': op_uuid},
    }


class RecoverInterruptedOperationsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.ops = {}
        self.rows = []

        fake_config = mock.MagicMock()
        fake_config.CLUSTER_OP_MAX_ATTEMPTS = 5
        self.mariadb = mock.MagicMock()
        self.mariadb.list_stuck_work_queue_rows.side_effect = lambda threshold, strict=False: self.rows
        self.mariadb.delete_work_queue_row.return_value = True
        op_class = mock.MagicMock()
        op_class.from_db.side_effect = lambda op_uuid: self.ops.get(op_uuid)

        def get_object_class(op_type):
            if op_type == 'retired_op':
                raise NoSuchObject(op_type)
            return op_class

        for p in [
                mock.patch.object(startup_tasks, 'config', fake_config),
                mock.patch.object(startup_tasks, 'mariadb', self.mariadb),
                mock.patch.object(startup_tasks, 'get_object_class', side_effect=get_object_class)]:
            p.start()
            self.addCleanup(p.stop)

    def test_lists_every_claim(self):
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        # Strictly: a listing which failed must not read as "no claims", or
        # startup clears claims nobody checked.
        self.mariadb.list_stuck_work_queue_rows.assert_called_once_with(0, strict=True)

    def test_executing_op_on_own_queue_is_requeued(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OWN_QUEUE, 'op'))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, op.state.value)
        self.assertEqual(1, len(op.events))
        self.mariadb.delete_work_queue_row.assert_not_called()

    def test_background_queue_is_also_ours(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(f'{NODE_UUID}-clusteroperation-background_high_io', 'op'))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, op.state.value)

    def test_shared_and_other_node_queues_are_left_alone(self):
        # An executing operation behind a shared queue's claim may be running
        # on another node right now.
        shared = self.ops['shared'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        other = self.ops['other'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.extend([row(SHARED_QUEUE, 'shared'), row(OTHER_NODE_QUEUE, 'other')])
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_EXECUTING, shared.state.value)
        self.assertEqual(BaseClusterOperation.STATE_EXECUTING, other.state.value)

    def test_ops_not_executing_are_left_alone(self):
        # A queued operation re-runs once its claim is cleared anyway.
        queued = self.ops['queued'] = FakeOp(BaseClusterOperation.STATE_QUEUED)
        done = self.ops['done'] = FakeOp(BaseClusterOperation.STATE_COMPLETE)
        self.rows.extend([row(OWN_QUEUE, 'queued'), row(OWN_QUEUE, 'done', row_id=2)])
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual([], queued.events)
        self.assertEqual([], done.events)
        self.assertEqual(BaseClusterOperation.STATE_COMPLETE, done.state.value)

    def test_missing_op_and_bad_payload_are_skipped(self):
        self.rows.append(row(OWN_QUEUE, 'missing'))
        self.rows.append(dict(row(OWN_QUEUE, 'x', row_id=2), payload={}))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.mariadb.delete_work_queue_row.assert_not_called()

    def test_op_which_keeps_killing_sf_queues_is_errored(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OWN_QUEUE, 'op', attempts=5, row_id=42))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.mariadb.delete_work_queue_row.assert_called_once_with(42)
        self.assertEqual(BaseClusterOperation.STATE_ERROR, op.state.value)

    def test_last_attempt_below_the_ceiling_is_still_requeued(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OWN_QUEUE, 'op', attempts=4))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, op.state.value)

    def test_row_already_gone_leaves_op_alone(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OWN_QUEUE, 'op', attempts=5))
        self.mariadb.delete_work_queue_row.return_value = False
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_EXECUTING, op.state.value)

    def test_an_item_which_cannot_be_recovered_does_not_stop_the_pass(self):
        # An operation type which no longer exists, or the database failing
        # part way through, is left for the reaper rather than escaping and
        # stopping sf-queues' startup before it restores this node's instances.
        retired = row(OWN_QUEUE, 'retired')
        retired['payload']['operation_type'] = 'retired_op'
        broken = self.ops['broken'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        broken.add_event = mock.MagicMock(side_effect=exceptions.DatabaseUnavailable('gone'))
        later = self.ops['later'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.extend([retired, row(OWN_QUEUE, 'broken', row_id=2), row(OWN_QUEUE, 'later', row_id=3)])
        with mock.patch.object(startup_tasks.util_exceptions, 'ignore_exception') as ignored:
            startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, later.state.value)
        self.assertEqual([NoSuchObject, exceptions.DatabaseUnavailable],
                         [type(c.args[1]) for c in ignored.call_args_list])

    def test_failing_to_list_the_claims_is_raised(self):
        # The caller must then not clear claims nobody has checked.
        self.mariadb.list_stuck_work_queue_rows.side_effect = exceptions.DatabaseUnavailable('gone')
        self.assertRaises(exceptions.DatabaseUnavailable,
                          startup_tasks.recover_interrupted_operations, NODE_UUID)

    def test_op_gone_terminal_meanwhile_does_not_stop_the_pass(self):
        self.ops['refuses'] = FakeOp(BaseClusterOperation.STATE_EXECUTING, refuse=True)
        later = self.ops['later'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.extend([row(OWN_QUEUE, 'refuses'), row(OWN_QUEUE, 'later', row_id=2)])
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, later.state.value)


class StartupResetTestCase(base.ShakenFistTestCase):
    """startup_tasks() recovers first, then resets the node's own queues."""

    def setUp(self):
        super().setUp()
        self.calls = mock.MagicMock()
        self.config = fake_config = mock.MagicMock()
        fake_config.NODE_UUID = NODE_UUID
        fake_config.NODE_NAME = 'sf-6'
        self.mariadb = self.calls.mariadb
        self.mariadb.get_work_queue_length.return_value = (0, 0, 0)

        for p in [
                mock.patch.object(startup_tasks, 'config', fake_config),
                mock.patch.object(startup_tasks, 'mariadb', self.mariadb),
                mock.patch.object(startup_tasks, 'recover_interrupted_operations',
                                  self.calls.recover),
                mock.patch.object(startup_tasks, 'redacted_config_items', return_value=[]),
                mock.patch.object(startup_tasks, '_resolve_node_uuid'),
                mock.patch.object(startup_tasks, 'Node'),
                mock.patch.object(startup_tasks, 'daemon'),
                mock.patch.object(startup_tasks, 'locks'),
                mock.patch.object(startup_tasks, 'util_general'),
                mock.patch.object(startup_tasks, 'util_concurrency'),
                mock.patch.object(startup_tasks, 'upgrade_blob_datastore'),
                mock.patch.object(startup_tasks, 'restore_instances')]:
            p.start()
            self.addCleanup(p.stop)

    def test_resets_queues_named_by_uuid_after_recovering(self):
        startup_tasks.startup_tasks().join(10)

        reset = [c.args[0] for c in self.mariadb.restart_work_queue.call_args_list]
        self.assertIn(OWN_QUEUE, reset)
        self.assertNotIn('sf-6-clusteroperation-user_facing', reset)

        names = [c[0] for c in self.calls.mock_calls]
        self.assertLess(names.index('recover'), names.index('mariadb.restart_work_queue'))
        self.calls.recover.assert_called_once_with(NODE_UUID)

    def test_leaves_the_shared_queues_to_the_reaper(self):
        # Other nodes' workers hold live claims on the any-* queues; clearing
        # one hands an item which is still running to a second worker.
        startup_tasks.startup_tasks().join(10)

        reset = [c.args[0] for c in self.mariadb.restart_work_queue.call_args_list]
        self.assertNotIn(SHARED_QUEUE, reset)
        self.assertCountEqual(
            get_node_user_facing_node_queues(NODE_UUID) + get_node_background_node_queues(NODE_UUID), reset)

    def test_resets_nothing_without_a_node_uuid(self):
        # Not "None-clusteroperation-*", and not the shared queues either.
        self.config.NODE_UUID = None
        startup_tasks.startup_tasks().join(10)

        self.mariadb.restart_work_queue.assert_not_called()
        self.calls.recover.assert_not_called()

    def test_claims_which_could_not_be_checked_are_left_to_the_reaper(self):
        # Clearing them unchecked strands any executing operation behind
        # them, as #4543 did. The rest of startup still runs.
        self.calls.recover.side_effect = exceptions.DatabaseUnavailable('gone')
        with mock.patch.object(startup_tasks.util_exceptions, 'ignore_exception'):
            restore = startup_tasks.startup_tasks()
        restore.join(10)

        self.mariadb.restart_work_queue.assert_not_called()
        startup_tasks.upgrade_blob_datastore.assert_called_once()
        startup_tasks.restore_instances.assert_called_once()
