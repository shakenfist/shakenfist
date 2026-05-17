# Copyright 2026 Michael Still and contributors
from unittest import mock

from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import CannotDeferUnqueued
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations import artifact_fetch_op as fetch_schema
from shakenfist.tests import base


OP_UUID = '11111111-1111-4111-8111-111111111111'


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
