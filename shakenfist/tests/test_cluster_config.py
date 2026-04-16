# Copyright 2026 Michael Still and contributors
#
# Tests for the cluster_config _direct_* helpers in mariadb.py.

from unittest import mock

from shakenfist import mariadb
from shakenfist.tests import base


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
    """Minimal mock for a SQLAlchemy Connection."""

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


class DirectGetAllClusterConfigTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_all_cluster_config."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_all_config(self, mock_engine):
        conn = _MockConnection(
            result=_MockResult(rows=[
                ('AUTH_SECRET_SEED', 'secret123'),
                ('DNS_SERVER', '8.8.8.8'),
                ('RAM_SYSTEM_RESERVATION', 5),
            ]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_config()
        self.assertEqual(result, {
            'AUTH_SECRET_SEED': 'secret123',
            'DNS_SERVER': '8.8.8.8',
            'RAM_SYSTEM_RESERVATION': 5,
        })

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_empty_when_no_config(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_config()
        self.assertEqual(result, {})

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_empty_on_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_config()
        self.assertEqual(result, {})


class DirectSetClusterConfigTestCase(base.ShakenFistTestCase):
    """Tests for _direct_set_cluster_config."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_upserts_key(self, mock_engine):
        conn = _MockConnection()
        mock_engine.return_value = _MockEngine(conn)

        mariadb._direct_set_cluster_config('DNS_SERVER', '8.8.8.8')
        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.executed), 1)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_handles_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        # Should not raise
        mariadb._direct_set_cluster_config('key', 'val')


class DirectDeleteClusterConfigTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_cluster_config."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_returns_true_on_success(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rowcount=1))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_delete_cluster_config('DNS_SERVER')
        self.assertTrue(result)
        self.assertTrue(conn.committed)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_returns_false_on_missing(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rowcount=0))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_delete_cluster_config('MISSING')
        self.assertFalse(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_returns_false_on_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_delete_cluster_config('key')
        self.assertFalse(result)
