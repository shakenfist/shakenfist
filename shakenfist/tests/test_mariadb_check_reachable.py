# Copyright 2019 Michael Still and contributors
#
# Tests for mariadb.check_reachable(), the bounded health-probe helper.

from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base


def _make_mock_engine(execute_raises=None):
    """Build a mock SA engine for check_reachable tests.

    If ``execute_raises`` is an exception instance, the mock connection's
    ``execute`` will raise it; otherwise execute succeeds silently.
    """
    mock_engine = mock.MagicMock(spec=sa.Engine)
    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.Mock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.Mock(return_value=False)

    if execute_raises is not None:
        mock_conn.execute.side_effect = execute_raises
    else:
        mock_conn.execute.return_value = mock.MagicMock()

    return mock_engine, mock_conn


class TestCheckReachable(base.ShakenFistTestCase):
    """Unit tests for mariadb.check_reachable()."""

    def _patch_connection_url(self, url='mariadb+mysqldb://user:pass@host/db'):
        return mock.patch(
            'shakenfist.mariadb._get_connection_url', return_value=url)

    def _patch_create_engine(self, mock_engine):
        return mock.patch(
            'shakenfist.mariadb.sa.create_engine', return_value=mock_engine)

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_returns_true_when_select_succeeds(self):
        mock_engine, _conn = _make_mock_engine()
        with self._patch_connection_url():
            with self._patch_create_engine(mock_engine):
                result = mariadb.check_reachable()
        self.assertTrue(result)

    def test_create_engine_called_with_null_pool_and_timeout(self):
        """Engine must be unpooled and carry the connect_timeout arg."""
        mock_engine, _conn = _make_mock_engine()
        with self._patch_connection_url(url='mariadb+mysqldb://u:p@h/d'):
            with mock.patch(
                'shakenfist.mariadb.sa.create_engine',
                return_value=mock_engine,
            ) as mock_create:
                mariadb.check_reachable(timeout_seconds=5)

        mock_create.assert_called_once_with(
            'mariadb+mysqldb://u:p@h/d',
            connect_args={'connect_timeout': 5},
            poolclass=sa.pool.NullPool,
        )

    def test_dispose_called_on_success(self):
        mock_engine, _conn = _make_mock_engine()
        with self._patch_connection_url():
            with self._patch_create_engine(mock_engine):
                mariadb.check_reachable()
        mock_engine.dispose.assert_called_once()

    # ------------------------------------------------------------------
    # Failure paths
    # ------------------------------------------------------------------

    def test_returns_false_when_execute_raises(self):
        mock_engine, _conn = _make_mock_engine(
            execute_raises=Exception('connection refused'))
        with self._patch_connection_url():
            with self._patch_create_engine(mock_engine):
                result = mariadb.check_reachable()
        self.assertFalse(result)

    def test_dispose_called_even_when_execute_raises(self):
        mock_engine, _conn = _make_mock_engine(
            execute_raises=Exception('gone'))
        with self._patch_connection_url():
            with self._patch_create_engine(mock_engine):
                mariadb.check_reachable()
        mock_engine.dispose.assert_called_once()

    def test_returns_false_when_create_engine_raises(self):
        with self._patch_connection_url():
            with mock.patch(
                'shakenfist.mariadb.sa.create_engine',
                side_effect=Exception('bad url'),
            ):
                result = mariadb.check_reachable()
        self.assertFalse(result)

    def test_dispose_not_called_when_create_engine_raises(self):
        """If engine was never created, dispose must not be called."""
        mock_engine, _conn = _make_mock_engine()
        with self._patch_connection_url():
            with mock.patch(
                'shakenfist.mariadb.sa.create_engine',
                side_effect=Exception('bad url'),
            ):
                mariadb.check_reachable()
        mock_engine.dispose.assert_not_called()

    def test_returns_false_when_get_connection_url_raises(self):
        """MARIADB_HOST unset or similar causes _get_connection_url to raise."""
        with mock.patch(
            'shakenfist.mariadb._get_connection_url',
            side_effect=RuntimeError('MARIADB_HOST not configured'),
        ):
            result = mariadb.check_reachable()
        self.assertFalse(result)

    def test_never_raises_when_get_connection_url_raises(self):
        """check_reachable must not propagate any exception."""
        with mock.patch(
            'shakenfist.mariadb._get_connection_url',
            side_effect=RuntimeError('MARIADB_HOST not configured'),
        ):
            try:
                mariadb.check_reachable()
            except Exception as exc:
                self.fail(f'check_reachable raised unexpectedly: {exc}')

    def test_never_raises_when_execute_raises_operational_error(self):
        """OperationalError (e.g. host unreachable) must not propagate."""
        from sqlalchemy.exc import OperationalError
        exc = OperationalError('statement', {}, Exception('TCP error'))
        mock_engine, _conn = _make_mock_engine(execute_raises=exc)
        with self._patch_connection_url():
            with self._patch_create_engine(mock_engine):
                try:
                    result = mariadb.check_reachable()
                except Exception as e:
                    self.fail(f'check_reachable raised unexpectedly: {e}')
        self.assertFalse(result)

    def test_default_timeout_is_three_seconds(self):
        """Default timeout_seconds must be 3."""
        mock_engine, _conn = _make_mock_engine()
        with self._patch_connection_url(url='mariadb+mysqldb://u:p@h/d'):
            with mock.patch(
                'shakenfist.mariadb.sa.create_engine',
                return_value=mock_engine,
            ) as mock_create:
                mariadb.check_reachable()

        _call_kwargs = mock_create.call_args[1]
        self.assertEqual(_call_kwargs['connect_args']['connect_timeout'], 3)
