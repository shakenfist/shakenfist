# Copyright 2026 Michael Still and contributors
from unittest import mock

from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import CannotDeferUnqueued
from shakenfist.operations.net_op import NetOp
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations import artifact_fetch_op as fetch_schema
from shakenfist.schema.operations import net_op as net_op_schema
from shakenfist.tests import base


OP_UUID = '11111111-1111-4111-8111-111111111111'
NETWORK_UUID = '22222222-2222-4222-8222-222222222222'


class _StubOp(BaseClusterOperation):
    object_type = ObjectType.ARTIFACT_FETCH_OP
    initial_version = 1
    current_version = 1

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, fetch_schema)


def _make_static_values():
    return {
        'uuid': OP_UUID,
        'priority': 'background',
        'request_id': None,
        'depends_on': None,
        'runs_after': None,
        'tasks': ['image_fetch'],
        'version': 1,
    }


def _make_net_op_static_values(tasks):
    return {
        'uuid': OP_UUID,
        'network_uuid': NETWORK_UUID,
        'priority': 'user_facing_high_io',
        'request_id': None,
        'depends_on': None,
        'runs_after': None,
        'tasks': tasks,
        'version': net_op_schema.current_version,
    }


class DeferWithBackoffTestCase(base.ShakenFistTestCase):
    """Tests for the retry-with-backoff helper on cluster operations.

    defer() and defer_with_backoff() touch MariaDB (state, work queue)
    and the eventlog. We patch all three so we can assert purely on the
    payloads handed to mariadb.enqueue_work_item.
    """

    def setUp(self):
        super().setUp()

        self.add_event_patcher = mock.patch.object(
            BaseClusterOperation, 'add_event')
        self.add_event_patcher.start()
        self.addCleanup(self.add_event_patcher.stop)

        self.state_patcher = mock.patch.object(
            BaseClusterOperation, '_state_update')
        self.state_patcher.start()
        self.addCleanup(self.state_patcher.stop)

        self.enqueue_patcher = mock.patch(
            'shakenfist.operations.baseoperation.mariadb.enqueue_work_item')
        self.mock_enqueue = self.enqueue_patcher.start()
        self.addCleanup(self.enqueue_patcher.stop)

    def _make_op(self):
        op = _StubOp(_make_static_values())
        op.queue_name = 'test-queue'
        return op

    def test_defer_without_queue_name_raises(self):
        op = _StubOp(_make_static_values())
        self.assertRaises(CannotDeferUnqueued, op.defer)

    def test_defer_writes_initial_defer_count(self):
        op = self._make_op()
        op.defer(delay=15)

        self.mock_enqueue.assert_called_once()
        args, kwargs = self.mock_enqueue.call_args
        self.assertEqual('test-queue', args[0])
        work_item = args[1]
        self.assertEqual(OP_UUID, work_item['operation_uuid'])
        self.assertEqual(
            ObjectType.ARTIFACT_FETCH_OP, work_item['operation_type'])
        self.assertEqual(1, work_item['defer_count'])
        self.assertEqual(15, kwargs['delay'])

    def test_defer_increments_existing_defer_count(self):
        op = self._make_op()
        op.current_defer_count = 2
        op.defer(delay=42)

        work_item = self.mock_enqueue.call_args.args[1]
        self.assertEqual(3, work_item['defer_count'])

    def test_defer_with_backoff_schedules_first_retry(self):
        op = self._make_op()
        self.assertTrue(op.defer_with_backoff())

        args, kwargs = self.mock_enqueue.call_args
        self.assertEqual(15, kwargs['delay'])
        self.assertEqual(1, args[1]['defer_count'])

    def test_defer_with_backoff_uses_default_schedule_in_order(self):
        delays_seen = []
        for n in range(3):
            op = self._make_op()
            op.current_defer_count = n
            self.mock_enqueue.reset_mock()
            self.assertTrue(op.defer_with_backoff())
            delays_seen.append(self.mock_enqueue.call_args.kwargs['delay'])
        self.assertEqual([15, 30, 60], delays_seen)

    def test_defer_with_backoff_exhausts_budget(self):
        op = self._make_op()
        op.current_defer_count = 3

        self.assertFalse(op.defer_with_backoff(reason='upstream 503'))
        self.mock_enqueue.assert_not_called()

    def test_defer_with_backoff_honours_custom_delays(self):
        op = self._make_op()
        op.current_defer_count = 1

        self.assertTrue(op.defer_with_backoff(delays=(5, 10, 20, 40)))
        self.assertEqual(10, self.mock_enqueue.call_args.kwargs['delay'])

    def test_current_defer_count_starts_at_zero(self):
        # The dispatcher relies on this being a sensible default for
        # ops loaded outside the queue dispatch path.
        op = self._make_op()
        self.assertEqual(0, op.current_defer_count)


class CoalescingExecuteTestCase(base.ShakenFistTestCase):
    """Tests for the within-job and cross-op coalescing branches in
    ``BaseClusterOperation.execute``.

    The two passes are wired in ``execute()`` itself rather than the
    dispatcher, so a unit test against a real NetOp class is enough --
    no need to stand up either of the daemon workers.
    """

    def setUp(self):
        super().setUp()

        # State writes hit MariaDB; the test only cares about the
        # behavioural surface (tasks dispatched, events emitted,
        # claim_coalescible_siblings called with the right shape).
        self.state_patcher = mock.patch.object(
            BaseClusterOperation, '_state_update')
        self.state_patcher.start()
        self.addCleanup(self.state_patcher.stop)
        self.state_value_patcher = mock.patch.object(
            BaseClusterOperation, 'state',
            new_callable=mock.PropertyMock)
        mock_state = self.state_value_patcher.start()
        # The post-dispatch loop in execute() short-circuits if
        # state.value is in {ABORT, DELETED, ERROR, QUEUED}. We want
        # the loop to run every task, so return EXECUTING -- the
        # value execute() itself set before iterating.
        mock_state.return_value = mock.MagicMock(
            value=BaseClusterOperation.STATE_EXECUTING)
        self.addCleanup(self.state_value_patcher.stop)

        self.add_event_patcher = mock.patch.object(
            BaseClusterOperation, 'add_event')
        self.mock_add_event = self.add_event_patcher.start()
        self.addCleanup(self.add_event_patcher.stop)

        self.claim_patcher = mock.patch(
            'shakenfist.operations.baseoperation.mariadb.'
            'claim_coalescible_siblings')
        self.mock_claim = self.claim_patcher.start()
        self.mock_claim.return_value = []
        self.addCleanup(self.claim_patcher.stop)

    def _make_net_op(self, task_names):
        op = NetOp(_make_net_op_static_values(task_names))
        # ``dispatch_task`` is wired by the concrete op; patch it so
        # we observe which tasks would actually run.
        op.dispatch_task = mock.MagicMock()
        return op

    def test_within_job_drops_duplicate_coalescible_tasks(self):
        # update_dnsmasq is coalescible, so [update, update, update]
        # collapses to one dispatch.
        op = self._make_net_op([
            'network_apply_update_dnsmasq',
            'network_apply_update_dnsmasq',
            'network_apply_update_dnsmasq',
        ])
        op.execute()

        self.assertEqual(1, op.dispatch_task.call_count)
        # Two "dropped duplicate" events for the two skipped tasks.
        drop_events = [
            c for c in self.mock_add_event.call_args_list
            if c.args[1].startswith('within-job: dropped duplicate')]
        self.assertEqual(2, len(drop_events))

    def test_within_job_does_not_drop_non_coalescible_duplicates(self):
        # network_remove_dnsmasq is *not* coalescible -- a repeat would
        # be intentional, so the loop runs it twice.
        op = self._make_net_op([
            'network_remove_dnsmasq',
            'network_remove_dnsmasq',
        ])
        op.execute()

        self.assertEqual(2, op.dispatch_task.call_count)

    def test_cross_op_coalescing_calls_mariadb_with_target(self):
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.execute()

        self.mock_claim.assert_called_once()
        kwargs = self.mock_claim.call_args.kwargs
        self.assertEqual(ObjectType.NET_OP, kwargs['operation_type'])
        self.assertEqual('network_uuid', kwargs['target_column'])
        self.assertEqual(NETWORK_UUID, kwargs['target_uuid'])
        self.assertEqual(
            ['network_apply_update_dnsmasq'], kwargs['task_names'])
        self.assertEqual(OP_UUID, kwargs['exclude_op_uuid'])

    def test_cross_op_coalescing_records_sibling_uuids(self):
        sibling = '33333333-3333-4333-8333-333333333333'
        self.mock_claim.return_value = [sibling]

        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.execute()

        coalesced_events = [
            c for c in self.mock_add_event.call_args_list
            if c.args[1] == 'coalesced sibling ops']
        self.assertEqual(1, len(coalesced_events))
        extra = coalesced_events[0].kwargs['extra']
        self.assertEqual(1, extra['sibling_count'])
        self.assertEqual([sibling], extra['sibling_uuids'])

    def test_no_coalescing_call_when_task_not_coalescible(self):
        # network_remove_dnsmasq isn't in COALESCIBLE_TASKS, so the
        # mariadb call must not happen at all.
        op = self._make_net_op(['network_remove_dnsmasq'])
        op.execute()

        self.mock_claim.assert_not_called()

    def test_no_coalescing_call_when_dispatcher_batch_was_one(self):
        # Dispatcher hint says we were the only item in the batch, so
        # no sibling can possibly be ready. Skip the SQL round-trip.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.dispatcher_batch_size = 1
        op.execute()

        self.mock_claim.assert_not_called()

    def test_coalescing_runs_when_dispatcher_batch_was_multiple(self):
        # Dispatcher hint says >1 ready items, so contention is
        # possible -- the fold runs.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.dispatcher_batch_size = 5
        op.execute()

        self.mock_claim.assert_called_once()

    def test_coalescing_runs_when_dispatcher_batch_unknown(self):
        # ``None`` means the op was loaded outside the queue path
        # (e.g. a unit test or REST endpoint); be conservative and
        # run the fold.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.dispatcher_batch_size = None
        op.execute()

        self.mock_claim.assert_called_once()
