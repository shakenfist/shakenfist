# Copyright 2026 Michael Still and contributors
#
# Tests for the cluster_locks _direct_* helpers and the
# _cluster_lock_key() utility. The _direct_* functions are
# tested by mocking _get_engine() with a mock engine that
# simulates the connection/result interface.

import time
from unittest import mock

from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.tests import base


class ClusterLockKeyTestCase(base.ShakenFistTestCase):
    """Tests for _cluster_lock_key() key construction."""

    def test_all_three_parts(self):
        self.assertEqual(
            mariadb._cluster_lock_key('instance', 'parent', 'uuid1'),
            'instance/parent/uuid1')

    def test_name_only(self):
        self.assertEqual(
            mariadb._cluster_lock_key('vsock_cids', '', 'global'),
            'vsock_cids/global')

    def test_subtype_only(self):
        self.assertEqual(
            mariadb._cluster_lock_key('something', 'sub', ''),
            'something/sub/')

    def test_neither_subtype_nor_name(self):
        self.assertEqual(
            mariadb._cluster_lock_key('cluster', '', ''),
            'cluster/')

    def test_none_subtype_and_name(self):
        # The gRPC layer sends empty strings for None subtype/name.
        self.assertEqual(
            mariadb._cluster_lock_key('cluster', '', ''),
            'cluster/')

    def test_attribute_lock_pattern(self):
        self.assertEqual(
            mariadb._cluster_lock_key(
                'attribute/instance', 'uuid1', 'power_state'),
            'attribute/instance/uuid1/power_state')


class _MockResult:
    """Minimal mock for a SQLAlchemy CursorResult."""

    def __init__(self, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _MockConnection:
    """Minimal mock for a SQLAlchemy Connection.

    Records executed statements and returns a configurable result.
    Pass a single ``result`` for all calls, or ``results`` (a list)
    to return a different result per execute() in order -- useful
    when a single function issues several statements in sequence
    (e.g. INSERT-then-steal-UPDATE in _direct_acquire_cluster_lock).
    """

    def __init__(self, result=None, results=None):
        self.results = list(results) if results is not None else None
        self.result = result or _MockResult()
        self.executed = []
        self.committed = False

    def execute(self, stmt):
        self.executed.append(stmt)
        if self.results is not None:
            return self.results.pop(0)
        return self.result

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _MockEngine:
    """Minimal mock for a SQLAlchemy Engine."""

    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        return self._connection


HOLDER = {
    'node': 'node1',
    'pid': 42,
    'thread': 12345,
    'line': 'test.py:10',
    'operation': 'test_op',
    'id': 'lock-abc',
}


class DirectAcquireClusterLockTestCase(base.ShakenFistTestCase):
    """Tests for _direct_acquire_cluster_lock.

    The function issues a single ``INSERT ... ON DUPLICATE KEY
    UPDATE`` followed by a primary-key SELECT to confirm whether
    our lock_id ended up in the row, so the mock connection
    returns one rowcount-only result and one row-bearing result.
    """

    def _patch_engine(self, lock_id_in_row):
        # Two execute() calls per acquire: the upsert (rowcount
        # ignored by the new logic) and a confirming SELECT. Pass
        # ``lock_id_in_row=None`` to simulate "row was deleted out
        # from under us between upsert and SELECT" -- otherwise the
        # SELECT returns one row containing the given lock_id.
        rows = [(lock_id_in_row,)] if lock_id_in_row is not None else []
        results = [_MockResult(rowcount=1), _MockResult(rows=rows)]
        conn = _MockConnection(results=results)
        engine = _MockEngine(conn)
        patcher = mock.patch(
            'shakenfist.mariadb._get_engine', return_value=engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        return conn

    def test_acquire_succeeds_on_fresh_insert(self):
        # Cold acquire: upsert inserts, SELECT confirms our lock_id.
        conn = self._patch_engine(lock_id_in_row='lock-abc')
        result = mariadb._direct_acquire_cluster_lock(
            'instance/parent/uuid1', HOLDER, 'node1', 42,
            'lock-abc', time.time())
        self.assertTrue(result)
        self.assertTrue(conn.committed)
        # Upsert + confirming SELECT.
        self.assertEqual(len(conn.executed), 2)

    def test_acquire_steals_expired_lock(self):
        # Steal: upsert overwrote the expired row in place, SELECT
        # confirms our lock_id is now in the row. Indistinguishable
        # from cold acquire at this layer -- and that's by design,
        # the IF()-gated SET clause does both.
        conn = self._patch_engine(lock_id_in_row='lock-abc')
        result = mariadb._direct_acquire_cluster_lock(
            'instance/parent/uuid1', HOLDER, 'node1', 42,
            'lock-abc', time.time())
        self.assertTrue(result)
        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.executed), 2)

    def test_acquire_fails_when_held_and_alive(self):
        # Held with valid lease: upsert was a no-op, SELECT shows the
        # incumbent's lock_id rather than ours.
        conn = self._patch_engine(lock_id_in_row='other-holder')
        result = mariadb._direct_acquire_cluster_lock(
            'instance/parent/uuid1', HOLDER, 'node1', 42,
            'lock-abc', time.time())
        self.assertFalse(result)
        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.executed), 2)

    def test_acquire_fails_when_select_returns_no_row(self):
        # Belt-and-braces: a missing row after upsert should not
        # crash and should be treated as "didn't get it".
        conn = self._patch_engine(lock_id_in_row=None)
        result = mariadb._direct_acquire_cluster_lock(
            'instance/parent/uuid1', HOLDER, 'node1', 42,
            'lock-abc', time.time())
        self.assertFalse(result)
        self.assertTrue(conn.committed)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_acquire_returns_false_on_non_deadlock_error(self, mock_engine):
        # Non-deadlock OperationalErrors short-circuit out of the
        # retry helper and are reported as "couldn't acquire".
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_acquire_cluster_lock(
            'key', HOLDER, 'node1', 42, 'lock-abc', time.time())
        self.assertFalse(result)
        # No retry attempts on non-deadlock errors.
        self.assertEqual(conn.execute.call_count, 1)


def _make_deadlock_error():
    """Build a SQLAlchemy OperationalError whose orig is shaped
    like the mysqldb driver's deadlock exception (errno 1213)."""
    orig = mock.Mock()
    orig.args = (1213, 'Deadlock found when trying to get lock; '
                       'try restarting transaction')
    return OperationalError('stmt', {}, orig)


class IsInnodbDeadlockTestCase(base.ShakenFistTestCase):
    """Tests for the _is_innodb_deadlock errno classifier."""

    def test_true_on_1213(self):
        self.assertTrue(
            mariadb._is_innodb_deadlock(_make_deadlock_error()))

    def test_false_on_other_errno(self):
        orig = mock.Mock()
        orig.args = (1062, 'Duplicate entry')
        exc = OperationalError('stmt', {}, orig)
        self.assertFalse(mariadb._is_innodb_deadlock(exc))

    def test_false_when_orig_missing(self):
        # SQLAlchemy can synthesize OperationalErrors without an
        # underlying DB-API exception in some test paths.
        exc = OperationalError('stmt', {}, None)
        self.assertFalse(mariadb._is_innodb_deadlock(exc))

    def test_false_when_args_empty(self):
        orig = mock.Mock()
        orig.args = ()
        exc = OperationalError('stmt', {}, orig)
        self.assertFalse(mariadb._is_innodb_deadlock(exc))


class RetryOnDeadlockTestCase(base.ShakenFistTestCase):
    """Tests for the _retry_on_deadlock helper.

    ``time.sleep`` is patched out so the jittered backoff doesn't
    actually slow the suite down, but we keep the call-count
    assertions so a regression that drops the backoff would show
    up here.
    """

    def setUp(self):
        super().setUp()
        sleep_patcher = mock.patch('shakenfist.mariadb.time.sleep')
        self.mock_sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_returns_value_on_first_success(self):
        fn = mock.Mock(return_value='ok')
        result = mariadb._retry_on_deadlock(fn, 'op')
        self.assertEqual(result, 'ok')
        self.assertEqual(fn.call_count, 1)
        self.assertEqual(self.mock_sleep.call_count, 0)

    def test_retries_then_succeeds(self):
        # Two deadlocks then a success: caller never sees the
        # transient errors.
        fn = mock.Mock(side_effect=[
            _make_deadlock_error(),
            _make_deadlock_error(),
            'ok',
        ])
        result = mariadb._retry_on_deadlock(fn, 'op')
        self.assertEqual(result, 'ok')
        self.assertEqual(fn.call_count, 3)
        # One sleep before each retry, two retries here.
        self.assertEqual(self.mock_sleep.call_count, 2)

    def test_raises_after_all_attempts_deadlock(self):
        # Sustained storm: every attempt deadlocks. Helper raises
        # the last 1213 so callers can run their existing handling.
        fn = mock.Mock(side_effect=[
            _make_deadlock_error() for _ in range(
                mariadb._DEADLOCK_MAX_ATTEMPTS)
        ])
        self.assertRaises(
            OperationalError, mariadb._retry_on_deadlock, fn, 'op')
        self.assertEqual(fn.call_count, mariadb._DEADLOCK_MAX_ATTEMPTS)
        # No sleep after the final attempt.
        self.assertEqual(
            self.mock_sleep.call_count,
            mariadb._DEADLOCK_MAX_ATTEMPTS - 1)

    def test_propagates_non_deadlock_error_immediately(self):
        # A non-1213 OperationalError is not retryable -- the helper
        # must surface it on the first attempt rather than burning
        # the retry budget on something a retry can't fix.
        non_deadlock = OperationalError('stmt', {}, Exception())
        fn = mock.Mock(side_effect=non_deadlock)
        self.assertRaises(
            OperationalError, mariadb._retry_on_deadlock, fn, 'op')
        self.assertEqual(fn.call_count, 1)
        self.assertEqual(self.mock_sleep.call_count, 0)


class DirectRefreshClusterLockTestCase(base.ShakenFistTestCase):
    """Tests for _direct_refresh_cluster_lock."""

    def _patch_engine(self, rowcount):
        conn = _MockConnection(result=_MockResult(rowcount=rowcount))
        engine = _MockEngine(conn)
        patcher = mock.patch(
            'shakenfist.mariadb._get_engine', return_value=engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        return conn

    def test_refresh_succeeds(self):
        conn = self._patch_engine(rowcount=1)
        ok = mariadb._direct_refresh_cluster_lock(
            'instance/parent/uuid1', 'lock-abc')
        self.assertTrue(ok)
        self.assertTrue(conn.committed)

    def test_refresh_returns_false_on_lost(self):
        # No row matched -- our lock_id is no longer the holder.
        conn = self._patch_engine(rowcount=0)
        ok = mariadb._direct_refresh_cluster_lock(
            'instance/parent/uuid1', 'lock-abc')
        self.assertFalse(ok)
        self.assertTrue(conn.committed)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_refresh_raises_on_operational_error(self, mock_engine):
        # Transient errors must propagate so the caller can retry
        # rather than mistake them for confirmed loss.
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        self.assertRaises(
            OperationalError,
            mariadb._direct_refresh_cluster_lock, 'key', 'lock-abc')


class GrpcRefreshClusterLockTestCase(base.ShakenFistTestCase):
    """Tests for _grpc_refresh_cluster_lock.

    The dispatch from refresh_cluster_lock() based on
    _use_database_service() is exercised here too -- it is the only
    distinguishing feature between the gRPC and direct paths.
    """

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_refresh_returns_true_on_server_success(
            self, mock_stub, mock_call):
        mock_stub.return_value = mock.MagicMock()
        mock_call.return_value = mock.MagicMock(success=True)

        ok = mariadb._grpc_refresh_cluster_lock(
            'cluster', '', '', 'lock-abc')

        self.assertTrue(ok)
        # The proto field name is part of the contract -- if a future
        # rename misses this site the test catches it.
        request = mock_call.call_args[0][1]
        self.assertEqual(request.lock_id, 'lock-abc')

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_refresh_returns_false_on_server_loss(
            self, mock_stub, mock_call):
        mock_stub.return_value = mock.MagicMock()
        mock_call.return_value = mock.MagicMock(success=False)

        ok = mariadb._grpc_refresh_cluster_lock(
            'cluster', '', '', 'lock-abc')

        self.assertFalse(ok)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_refresh_propagates_rpc_error(
            self, mock_stub, mock_call):
        # Transient gRPC errors must propagate so the refresh loop
        # treats them as retryable rather than confirmed loss.
        import grpc
        mock_stub.return_value = mock.MagicMock()
        mock_call.side_effect = grpc.RpcError('boom')

        self.assertRaises(
            grpc.RpcError,
            mariadb._grpc_refresh_cluster_lock,
            'cluster', '', '', 'lock-abc')


class DirectReleaseClusterLockTestCase(base.ShakenFistTestCase):
    """Tests for _direct_release_cluster_lock."""

    def _patch_engine(self, rowcount):
        conn = _MockConnection(result=_MockResult(rowcount=rowcount))
        engine = _MockEngine(conn)
        patcher = mock.patch(
            'shakenfist.mariadb._get_engine', return_value=engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        return conn

    def test_release_succeeds_with_matching_id(self):
        conn = self._patch_engine(rowcount=1)
        result = mariadb._direct_release_cluster_lock(
            'instance/parent/uuid1', 'lock-abc')
        self.assertTrue(result)
        self.assertTrue(conn.committed)

    def test_release_fails_with_wrong_id(self):
        self._patch_engine(rowcount=0)
        result = mariadb._direct_release_cluster_lock(
            'instance/parent/uuid1', 'wrong-id')
        self.assertFalse(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_release_raises_on_operational_error(self, mock_engine):
        # Transient deadlocks must propagate so the daemon handler can
        # signal UNAVAILABLE and let the gRPC client retry. Returning
        # False would look identical to "lease was stolen" and trigger
        # a spurious LockNotHeld error in the caller.
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        self.assertRaises(
            OperationalError,
            mariadb._direct_release_cluster_lock, 'key', 'lock-abc')


class DirectGetClusterLockTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_cluster_lock."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_returns_holder_when_held(self, mock_engine):
        conn = _MockConnection(
            result=_MockResult(rows=[(HOLDER,)]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_cluster_lock(
            'instance/parent/uuid1')
        self.assertEqual(result, HOLDER)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_returns_none_when_not_held(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_cluster_lock(
            'instance/parent/uuid1')
        self.assertIsNone(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_returns_none_on_operational_error(self, mock_engine):
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_cluster_lock('key')
        self.assertIsNone(result)


class DirectClearStaleClusterLocksTestCase(base.ShakenFistTestCase):
    """Tests for _direct_clear_stale_cluster_locks."""

    def _patch_engine(self, rowcount):
        conn = _MockConnection(result=_MockResult(rowcount=rowcount))
        engine = _MockEngine(conn)
        patcher = mock.patch(
            'shakenfist.mariadb._get_engine', return_value=engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        return conn

    def test_clears_stale_locks(self):
        conn = self._patch_engine(rowcount=2)
        result = mariadb._direct_clear_stale_cluster_locks(
            'node1', [1, 2, 3])
        self.assertEqual(result, 2)
        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.executed), 1)

    def test_no_live_pids_clears_all_for_node(self):
        conn = self._patch_engine(rowcount=3)
        result = mariadb._direct_clear_stale_cluster_locks(
            'node1', [])
        self.assertEqual(result, 3)
        self.assertTrue(conn.committed)

    def test_no_stale_returns_zero(self):
        self._patch_engine(rowcount=0)
        result = mariadb._direct_clear_stale_cluster_locks(
            'node1', [42])
        self.assertEqual(result, 0)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_zero_on_operational_error(self, mock_engine):
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_clear_stale_cluster_locks(
            'node1', [1])
        self.assertEqual(result, 0)


class DirectGetAllClusterLocksTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_all_cluster_locks."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_all_locks(self, mock_engine):
        holder_a = {'node': 'a', 'pid': 1, 'id': 'x'}
        holder_b = {'node': 'b', 'pid': 2, 'id': 'y'}
        conn = _MockConnection(
            result=_MockResult(rows=[
                ('key/a', holder_a),
                ('key/b', holder_b),
            ]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_locks()
        self.assertEqual(result, {
            'key/a': holder_a,
            'key/b': holder_b,
        })

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_empty_when_no_locks(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_locks()
        self.assertEqual(result, {})

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_empty_on_operational_error(self, mock_engine):
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_locks()
        self.assertEqual(result, {})
