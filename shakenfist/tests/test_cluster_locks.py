# Copyright 2026 Michael Still and contributors
#
# Tests for the cluster_locks _direct_* helpers and the
# _cluster_lock_key() utility. The _direct_* functions are
# tested by mocking _get_engine() with a mock engine that
# simulates the connection/result interface.

import time
from unittest import mock

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
    """

    def __init__(self, result=None):
        self.result = result or _MockResult()
        self.executed = []
        self.committed = False

    def execute(self, stmt):
        self.executed.append(stmt)
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
    """Tests for _direct_acquire_cluster_lock."""

    def _patch_engine(self, rowcount):
        conn = _MockConnection(result=_MockResult(rowcount=rowcount))
        engine = _MockEngine(conn)
        patcher = mock.patch(
            'shakenfist.mariadb._get_engine', return_value=engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        return conn

    def test_acquire_succeeds(self):
        conn = self._patch_engine(rowcount=1)
        result = mariadb._direct_acquire_cluster_lock(
            'instance/parent/uuid1', HOLDER, 'node1', 42,
            'lock-abc', time.time())
        self.assertTrue(result)
        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.executed), 1)

    def test_acquire_fails_when_held(self):
        conn = self._patch_engine(rowcount=0)
        result = mariadb._direct_acquire_cluster_lock(
            'instance/parent/uuid1', HOLDER, 'node1', 42,
            'lock-abc', time.time())
        self.assertFalse(result)
        self.assertTrue(conn.committed)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_acquire_returns_false_on_operational_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_acquire_cluster_lock(
            'key', HOLDER, 'node1', 42, 'lock-abc', time.time())
        self.assertFalse(result)


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
    def test_release_returns_false_on_operational_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_release_cluster_lock(
            'key', 'lock-abc')
        self.assertFalse(result)


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
        from sqlalchemy.exc import OperationalError
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
        from sqlalchemy.exc import OperationalError
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
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_locks()
        self.assertEqual(result, {})
