# Copyright 2026 Michael Still and contributors
#
# Tests for the mariadb._direct_work_queue_* functions introduced
# in phase 2 of the etcd-removal ops-queues plan.
#
# Pattern follows test_mariadb_blob_hashes.py: mock _get_engine and
# assert the direct-layer functions issue the expected SQL shape.
# Contract-level tests (race safety, deferred-job eligibility) are
# validated by inspecting the compiled SQL for the required clauses,
# since a pure engine mock cannot exercise row locking behaviour.

from unittest import mock
from uuid import UUID

from sqlalchemy.exc import OperationalError

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    MARIADB_GATEWAY_HOSTS: list[str] = ['192.168.1.1']
    MARIADB_GATEWAY_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()


def _make_mock_engine():
    """Build a mock engine whose connect() works as a context manager."""
    mock_engine = mock.MagicMock()
    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.Mock(
        return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.Mock(
        return_value=False)
    return mock_engine, mock_conn


class WorkQueueEnqueueTestCase(base.ShakenFistTestCase):
    """Tests for _direct_work_queue_enqueue()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb.time.time', return_value=1000.0)
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_enqueue_inserts_row(self, mock_get_engine, _mock_time):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        mariadb._direct_work_queue_enqueue(
            'my-queue', {'task': 'do-thing'})

        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()
        # Inspect the compiled statement values so we know the
        # insert is shaped correctly.
        stmt = mock_conn.execute.call_args[0][0]
        compiled = stmt.compile()
        params = compiled.params
        self.assertEqual(params['queue_name'], 'my-queue')
        self.assertEqual(params['scheduled_at'], 1000.0)
        self.assertEqual(params['created_at'], 1000.0)
        self.assertEqual(params['claimed_at'], None)
        self.assertEqual(params['claimed_by'], None)
        self.assertEqual(params['attempts'], 0)
        self.assertEqual(params['payload'], {'task': 'do-thing'})

    @mock.patch('shakenfist.mariadb.time.time', return_value=1000.0)
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_enqueue_with_delay_sets_scheduled_at(
            self, mock_get_engine, _mock_time):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        mariadb._direct_work_queue_enqueue(
            'my-queue', {'task': 'do-thing'}, delay=60.0)

        stmt = mock_conn.execute.call_args[0][0]
        params = stmt.compile().params
        self.assertEqual(params['scheduled_at'], 1060.0)
        self.assertEqual(params['created_at'], 1000.0)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_enqueue_raises_cannot_enqueue_work_on_error(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'insert', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        self.assertRaises(
            exceptions.CannotEnqueueWork,
            mariadb._direct_work_queue_enqueue,
            'my-queue',
            {'task': 'x'})


class WorkQueueDequeueBatchTestCase(base.ShakenFistTestCase):
    """Tests for _direct_work_queue_dequeue_batch()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_dequeue_empty_returns_empty_list(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_work_queue_dequeue_batch(
            ['my-queue'], 'worker-1', 10)

        self.assertEqual([], result)
        # Only the SELECT runs when the queue is empty; no UPDATE.
        self.assertEqual(mock_conn.execute.call_count, 1)
        mock_conn.commit.assert_not_called()

    def test_dequeue_empty_queue_names_is_no_op(self):
        # No engine call, no SQL emitted -- a defensive short-circuit.
        self.assertEqual(
            [], mariadb._direct_work_queue_dequeue_batch(
                [], 'worker-1', 10))

    def test_dequeue_zero_limit_is_no_op(self):
        self.assertEqual(
            [], mariadb._direct_work_queue_dequeue_batch(
                ['my-queue'], 'worker-1', 0))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_dequeue_limit_is_clamped(self, mock_get_engine):
        # ``limit`` above MAX_DEQUEUE_BATCH must be silently clamped
        # so a misbehaving caller cannot stage an unbounded in-flight
        # batch. The SELECT statement's LIMIT clause should carry the
        # clamped value, not the caller-supplied one.
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_get_engine.return_value = mock_engine

        mariadb._direct_work_queue_dequeue_batch(
            ['my-queue'], 'worker-1',
            mariadb.MAX_DEQUEUE_BATCH * 10)

        select_stmt = mock_conn.execute.call_args[0][0]
        compiled = select_stmt.compile(
            dialect=__import__(
                'sqlalchemy.dialects.mysql',
                fromlist=['dialect']).dialect())
        # The LIMIT bind param name varies across SQLAlchemy
        # versions; assert the value is present rather than the name.
        self.assertIn(
            mariadb.MAX_DEQUEUE_BATCH, compiled.params.values())

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_dequeue_claims_rows_and_returns_payloads(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        row_a = mock.Mock()
        row_a.id = 42
        row_a.queue_name = 'my-queue'
        row_a.payload = {'task': 'do-thing-a'}
        row_b = mock.Mock()
        row_b.id = 43
        row_b.queue_name = 'my-queue'
        row_b.payload = {'task': 'do-thing-b'}
        mock_select_result = mock.Mock()
        mock_select_result.fetchall.return_value = [row_a, row_b]
        mock_update_result = mock.Mock()
        mock_conn.execute.side_effect = [
            mock_select_result, mock_update_result]
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_work_queue_dequeue_batch(
            ['my-queue'], 'worker-1', 10)

        self.assertEqual(
            [
                ('my-queue', '42', {'task': 'do-thing-a'}),
                ('my-queue', '43', {'task': 'do-thing-b'}),
            ],
            result)
        self.assertEqual(mock_conn.execute.call_count, 2)
        mock_conn.commit.assert_called_once()

        update_stmt = mock_conn.execute.call_args_list[1][0][0]
        # The UPDATE must claim both rows in a single statement and
        # bump attempts.
        compiled = update_stmt.compile()
        self.assertEqual(compiled.params['claimed_by'], 'worker-1')
        compiled_sql = str(compiled).replace('`', '').replace(' ', '')
        self.assertIn(
            'attempts=(work_queue.attempts+',
            compiled_sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_dequeue_sql_uses_skip_locked(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_get_engine.return_value = mock_engine

        mariadb._direct_work_queue_dequeue_batch(
            ['my-queue'], 'worker-1', 10)

        select_stmt = mock_conn.execute.call_args[0][0]
        compiled_sql = str(select_stmt.compile(
            dialect=__import__(
                'sqlalchemy.dialects.mysql',
                fromlist=['dialect']).dialect()
        )).upper()
        self.assertIn('FOR UPDATE', compiled_sql)
        self.assertIn('SKIP LOCKED', compiled_sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_dequeue_sql_filters_deferred_jobs(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_get_engine.return_value = mock_engine

        mariadb._direct_work_queue_dequeue_batch(
            ['my-queue'], 'worker-1', 10)

        select_stmt = mock_conn.execute.call_args[0][0]
        compiled_sql = str(select_stmt.compile(
            dialect=__import__(
                'sqlalchemy.dialects.mysql',
                fromlist=['dialect']).dialect()
        )).lower()
        # The SELECT must contain scheduled_at <= unix_timestamp(now(...))
        # so that future jobs stay deferred.
        self.assertIn('scheduled_at <=', compiled_sql)
        self.assertIn('unix_timestamp', compiled_sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_dequeue_sql_orders_by_priority_then_age(
            self, mock_get_engine):
        # The FIELD(queue_name, ...) ordering is what makes the batched
        # SELECT honour the caller-supplied priority list.
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_get_engine.return_value = mock_engine

        mariadb._direct_work_queue_dequeue_batch(
            ['high', 'low'], 'worker-1', 10)

        select_stmt = mock_conn.execute.call_args[0][0]
        compiled_sql = str(select_stmt.compile(
            dialect=__import__(
                'sqlalchemy.dialects.mysql',
                fromlist=['dialect']).dialect()
        )).lower()
        self.assertIn('order by field(', compiled_sql)
        # scheduled_at is the secondary sort key (tie-break within
        # the same priority).
        self.assertIn('scheduled_at asc', compiled_sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_dequeue_returns_empty_list_on_error(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'select', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_work_queue_dequeue_batch(
            ['my-queue'], 'worker-1', 10)

        self.assertEqual([], result)


class WorkQueueResolveTestCase(base.ShakenFistTestCase):
    """Tests for _direct_work_queue_resolve()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_resolve_deletes_row(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        mariadb._direct_work_queue_resolve('my-queue', '42')

        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_resolve_non_numeric_is_noop(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        # Non-numeric job_name must not crash and must not execute
        # a DELETE (otherwise we would silently delete id=0 or similar).
        mariadb._direct_work_queue_resolve('my-queue', 'not-a-number')

        mock_conn.execute.assert_not_called()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_resolve_swallows_operational_error(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'delete', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        # Must not raise.
        mariadb._direct_work_queue_resolve('my-queue', '42')


class WorkQueueLengthTestCase(base.ShakenFistTestCase):
    """Tests for _direct_work_queue_length()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_length_returns_tuple(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        processing_result = mock.Mock()
        processing_result.scalar.return_value = 3
        queued_result = mock.Mock()
        queued_result.scalar.return_value = 7
        deferred_result = mock.Mock()
        deferred_result.scalar.return_value = 2
        mock_conn.execute.side_effect = [
            processing_result, queued_result, deferred_result]
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_work_queue_length('my-queue')

        self.assertEqual(result, (3, 7, 2))
        self.assertEqual(mock_conn.execute.call_count, 3)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_length_returns_zeros_on_error(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'select', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_work_queue_length('my-queue')

        self.assertEqual(result, (0, 0, 0))


class WorkQueueRestartTestCase(base.ShakenFistTestCase):
    """Tests for _direct_work_queue_restart()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_restart_clears_claims_and_returns_rowcount(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_result = mock.Mock()
        mock_result.rowcount = 4
        mock_conn.execute.return_value = mock_result
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_work_queue_restart('my-queue')

        self.assertEqual(result, 4)
        mock_conn.commit.assert_called_once()

        # Assert the UPDATE clears claim fields but does NOT touch
        # attempts -- phase 7's reaper needs to keep seeing them.
        update_stmt = mock_conn.execute.call_args[0][0]
        compiled = update_stmt.compile()
        self.assertIn('claimed_at', compiled.params)
        self.assertEqual(compiled.params['claimed_at'], None)
        self.assertIn('claimed_by', compiled.params)
        self.assertEqual(compiled.params['claimed_by'], None)
        self.assertNotIn('attempts', compiled.params)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_restart_returns_zero_on_error(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'update', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_work_queue_restart('my-queue')

        self.assertEqual(result, 0)


class WorkQueueListStuckTestCase(base.ShakenFistTestCase):
    """Tests for _direct_work_queue_list_stuck()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_list_stuck_returns_rows(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        row1 = mock.Mock(
            id=1, queue_name='q', claimed_at=500.0,
            claimed_by='worker-a', attempts=2,
            payload={'op': 'x'})
        row2 = mock.Mock(
            id=2, queue_name='q', claimed_at=600.0,
            claimed_by='worker-b', attempts=5,
            payload={'op': 'y'})
        mock_conn.execute.return_value.fetchall.return_value = [
            row1, row2]
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_work_queue_list_stuck(1800.0)

        self.assertEqual(2, len(result))
        self.assertEqual(1, result[0]['id'])
        self.assertEqual('q', result[0]['queue_name'])
        self.assertEqual(500.0, result[0]['claimed_at'])
        self.assertEqual('worker-a', result[0]['claimed_by'])
        self.assertEqual(2, result[0]['attempts'])
        self.assertEqual({'op': 'x'}, result[0]['payload'])
        self.assertEqual(5, result[1]['attempts'])

        stmt = mock_conn.execute.call_args[0][0]
        compiled_sql = str(stmt.compile()).replace(
            '`', '').replace(' ', '')
        self.assertIn('claimed_atISNOTNULL', compiled_sql)
        self.assertIn('ORDERBYwork_queue.claimed_atASC', compiled_sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_list_stuck_returns_empty_on_error(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'select', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        self.assertEqual([], mariadb._direct_work_queue_list_stuck(1.0))


class WorkQueueClearClaimTestCase(base.ShakenFistTestCase):
    """Tests for _direct_work_queue_clear_claim()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_clear_claim_updates_row(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_result = mock.Mock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result
        mock_get_engine.return_value = mock_engine

        self.assertTrue(
            mariadb._direct_work_queue_clear_claim(42))

        stmt = mock_conn.execute.call_args[0][0]
        compiled = stmt.compile()
        self.assertEqual(compiled.params['claimed_at'], None)
        self.assertEqual(compiled.params['claimed_by'], None)
        self.assertNotIn('attempts', compiled.params)
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_clear_claim_returns_false_when_no_match(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_result = mock.Mock()
        mock_result.rowcount = 0
        mock_conn.execute.return_value = mock_result
        mock_get_engine.return_value = mock_engine

        self.assertFalse(
            mariadb._direct_work_queue_clear_claim(99))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_clear_claim_returns_false_on_error(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'update', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        self.assertFalse(
            mariadb._direct_work_queue_clear_claim(1))


class WorkQueueDeleteRowTestCase(base.ShakenFistTestCase):
    """Tests for _direct_work_queue_delete_row()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_row_true_when_removed(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_result = mock.Mock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result
        mock_get_engine.return_value = mock_engine

        self.assertTrue(
            mariadb._direct_work_queue_delete_row(42))
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_row_false_when_missing(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_result = mock.Mock()
        mock_result.rowcount = 0
        mock_conn.execute.return_value = mock_result
        mock_get_engine.return_value = mock_engine

        self.assertFalse(
            mariadb._direct_work_queue_delete_row(99))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_row_false_on_error(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'delete', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        self.assertFalse(
            mariadb._direct_work_queue_delete_row(1))


class FindExistingCoalescibleOpTestCase(base.ShakenFistTestCase):
    """Tests for ``_direct_find_existing_coalescible_op``.

    Read-only enqueue-side dedup lookup. The mock cannot exercise
    JOIN behaviour, so the contract is verified by inspecting the
    compiled SQL for the guard clauses (target column equality,
    state filter, JSON_LENGTH single-task constraint, exclusion
    via uuid != / IN, and ORDER BY created_at ASC).
    """

    def setUp(self):
        super().setUp()
        self.config = mock.patch(
            'shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_invalid_target_column_returns_none_without_query(self):
        # Whitelist: only network_uuid / instance_uuid / node_uuid
        # are valid target columns. Anything else is rejected
        # before any SQL is emitted -- the function must not
        # touch the engine at all.
        with mock.patch(
                'shakenfist.mariadb._get_engine') as mock_get_engine:
            result = mariadb._direct_find_existing_coalescible_op(
                'net_op',
                [('arbitrary_user_supplied_column', 'some-uuid')],
                'some_task')
            self.assertIsNone(result)
            mock_get_engine.assert_not_called()

    def test_malformed_uuid_returns_none_without_query(self):
        # The *_uuid columns are SQLAlchemy Uuid-typed; an unparseable
        # uuid would otherwise blow up in the bind processor and kill
        # the worker thread, so it is skipped before any query runs.
        with mock.patch(
                'shakenfist.mariadb._get_engine') as mock_get_engine:
            self.assertIsNone(
                mariadb._direct_find_existing_coalescible_op(
                    'net_op', [('network_uuid', 'not-a-uuid')],
                    'network_apply_update_dnsmasq'))
            mock_get_engine.assert_not_called()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_none_when_no_match(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_find_existing_coalescible_op(
            'net_op',
            [('network_uuid', '11111111-1111-4111-8111-111111111111')],
            'network_apply_update_dnsmasq')

        self.assertIsNone(result)
        # One SELECT, no commit needed (read-only).
        self.assertEqual(mock_conn.execute.call_count, 1)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_uuid_when_match(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_row = mock.Mock()
        mock_row.uuid = '99999999-9999-4999-8999-999999999999'
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_find_existing_coalescible_op(
            'net_op',
            [('network_uuid', '11111111-1111-4111-8111-111111111111')],
            'network_apply_update_dnsmasq')

        self.assertEqual(
            '99999999-9999-4999-8999-999999999999', result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_sql_filters_to_queued_state_and_single_task(
            self, mock_get_engine):
        # The lookup must NOT match ops in EXECUTING / COMPLETE /
        # ERROR / DELETED states, and must NOT match multi-task ops.
        # Both filters live in the SELECT WHERE clause.
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_get_engine.return_value = mock_engine

        mariadb._direct_find_existing_coalescible_op(
            'net_op',
            [('network_uuid', '11111111-1111-4111-8111-111111111111')],
            'network_apply_update_dnsmasq')

        select_stmt = mock_conn.execute.call_args[0][0]
        compiled_sql = str(select_stmt.compile(
            dialect=__import__(
                'sqlalchemy.dialects.mysql',
                fromlist=['dialect']).dialect()
        )).lower()
        self.assertIn("state_value", compiled_sql)
        self.assertIn("json_length", compiled_sql)
        self.assertIn("json_extract", compiled_sql)
        # Ordered oldest-first so concurrent storms converge on
        # the same survivor.
        self.assertIn("order by", compiled_sql)
        self.assertIn("created_at", compiled_sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_none_on_error(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'select', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_find_existing_coalescible_op(
            'net_op',
            [('network_uuid', '11111111-1111-4111-8111-111111111111')],
            'network_apply_update_dnsmasq')

        self.assertIsNone(result)


class ClaimCoalescibleSiblingsTestCase(base.ShakenFistTestCase):
    """Tests for ``_direct_claim_coalescible_siblings``.

    Atomic worker-side fold: SELECT FOR UPDATE the matching
    sibling rows then UPDATE their object_states to ``complete``
    in the same transaction. The mock cannot exercise FOR UPDATE
    locking, so the SQL-shape assertions cover the WHERE/JOIN
    clauses, the FOR UPDATE marker, and that the survivor is
    excluded from the UPDATE.
    """

    def setUp(self):
        super().setUp()
        self.config = mock.patch(
            'shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_empty_task_names_returns_empty_without_query(self):
        with mock.patch(
                'shakenfist.mariadb._get_engine') as mock_get_engine:
            self.assertEqual(
                [], mariadb._direct_claim_coalescible_siblings(
                    'net_op', [('network_uuid', 'some-uuid')],
                    [], 'exclude-uuid'))
            mock_get_engine.assert_not_called()

    def test_invalid_target_column_returns_empty_without_query(self):
        with mock.patch(
                'shakenfist.mariadb._get_engine') as mock_get_engine:
            self.assertEqual(
                [], mariadb._direct_claim_coalescible_siblings(
                    'net_op', [('malicious_column', 'some-uuid')],
                    ['task'], 'exclude-uuid'))
            mock_get_engine.assert_not_called()

    def test_malformed_uuid_returns_empty_without_query(self):
        # The *_uuid columns are SQLAlchemy Uuid-typed; an unparseable
        # uuid would otherwise blow up in the bind processor and kill
        # the worker thread, so it is skipped before any query runs.
        with mock.patch(
                'shakenfist.mariadb._get_engine') as mock_get_engine:
            self.assertEqual(
                [], mariadb._direct_claim_coalescible_siblings(
                    'net_op', [('network_uuid', 'not-a-uuid')],
                    ['network_apply_update_dnsmasq'], 'also-not-a-uuid'))
            mock_get_engine.assert_not_called()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_empty_when_no_siblings(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_claim_coalescible_siblings(
            'net_op',
            [('network_uuid', '11111111-1111-4111-8111-111111111111')],
            ['network_apply_update_dnsmasq'],
            '99999999-9999-4999-8999-999999999999')

        self.assertEqual([], result)
        # Only the SELECT runs when there's nothing to fold; no
        # UPDATE and no commit.
        self.assertEqual(mock_conn.execute.call_count, 1)
        mock_conn.commit.assert_not_called()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_folded_uuids_and_runs_update(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        sibling_a = mock.Mock()
        sibling_a.uuid = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        sibling_b = mock.Mock()
        sibling_b.uuid = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
        mock_select_result = mock.Mock()
        mock_select_result.fetchall.return_value = [sibling_a, sibling_b]
        mock_update_result = mock.Mock()
        mock_conn.execute.side_effect = [
            mock_select_result, mock_update_result]
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_claim_coalescible_siblings(
            'net_op',
            [('network_uuid', '11111111-1111-4111-8111-111111111111')],
            ['network_apply_update_dnsmasq'],
            '99999999-9999-4999-8999-999999999999')

        self.assertEqual(
            [
                'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
            ],
            result)
        # SELECT + UPDATE.
        self.assertEqual(mock_conn.execute.call_count, 2)
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_sql_uses_for_update_and_excludes_survivor(
            self, mock_get_engine):
        # FOR UPDATE serialises the row read against the dispatcher
        # on every other node writing object_states. uuid != survivor
        # makes sure the surviving op is not folded against itself.
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_get_engine.return_value = mock_engine

        mariadb._direct_claim_coalescible_siblings(
            'net_op',
            [('network_uuid', '11111111-1111-4111-8111-111111111111')],
            ['network_apply_update_dnsmasq'],
            '99999999-9999-4999-8999-999999999999')

        select_stmt = mock_conn.execute.call_args[0][0]
        compiled_sql = str(select_stmt.compile(
            dialect=__import__(
                'sqlalchemy.dialects.mysql',
                fromlist=['dialect']).dialect()
        )).upper()
        self.assertIn('FOR UPDATE', compiled_sql)
        # exclude_op_uuid is a bind param; check it shows up in
        # the compiled params rather than the SQL text. It is coerced
        # to a uuid.UUID before binding because the column is Uuid-typed.
        params = select_stmt.compile().params
        self.assertIn(
            UUID('99999999-9999-4999-8999-999999999999'), params.values())

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_empty_on_error(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = OperationalError(
            'select', {}, Exception('DB down'))
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_claim_coalescible_siblings(
            'net_op',
            [('network_uuid', '11111111-1111-4111-8111-111111111111')],
            ['network_apply_update_dnsmasq'],
            '99999999-9999-4999-8999-999999999999')

        self.assertEqual([], result)
