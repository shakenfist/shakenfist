# Copyright 2026 Michael Still and contributors
#
# Tests for ClusterLock's refresh thread, lost_event signalling, and
# LockNotHeld behaviour. The refresh thread is real and exercised
# end-to-end here -- mariadb.refresh_cluster_lock is mocked, but
# ClusterLock starts its actual threading.Thread on acquire().

import threading
from unittest import mock

from shakenfist import exceptions
from shakenfist import locks
from shakenfist.tests import base


def _wait_for(predicate, timeout=2.0):
    """Spin until ``predicate()`` is true or the timeout fires."""
    end = threading.Event()
    timer = threading.Timer(timeout, end.set)
    timer.daemon = True
    timer.start()
    try:
        while not end.is_set():
            if predicate():
                return True
            end.wait(0.01)
        return predicate()
    finally:
        timer.cancel()


class ClusterLockRefreshTestCase(base.ShakenFistTestCase):
    """ClusterLock starts a refresher on acquire and stops it on release."""

    def _make_lock(self):
        return locks.ClusterLock(
            'cluster', None, None, op='Test', log_ctx=mock.MagicMock())

    @mock.patch('shakenfist.locks.mariadb.acquire_cluster_lock',
                return_value=True)
    @mock.patch('shakenfist.locks.mariadb.release_cluster_lock',
                return_value=True)
    @mock.patch('shakenfist.locks.mariadb.refresh_cluster_lock',
                return_value=True)
    def test_release_stops_refresher(
            self, mock_refresh, mock_release, mock_acquire):
        lock = self._make_lock()
        self.assertTrue(lock.acquire())
        self.assertIsNotNone(lock._refresher)
        lock.release()
        # Thread has been joined -- the handle is cleared and the
        # daemon thread is no longer alive.
        self.assertIsNone(lock._refresher)

    @mock.patch('shakenfist.locks.mariadb.acquire_cluster_lock',
                return_value=True)
    @mock.patch('shakenfist.locks.mariadb.release_cluster_lock',
                return_value=False)
    @mock.patch('shakenfist.locks.mariadb.refresh_cluster_lock',
                return_value=True)
    @mock.patch('shakenfist.locks.mariadb.get_cluster_lock_holder',
                return_value={'holder': None})
    def test_release_raises_lock_not_held_when_row_absent(
            self, mock_get, mock_refresh, mock_release, mock_acquire):
        lock = self._make_lock()
        self.assertTrue(lock.acquire())
        # release_cluster_lock returning False means the row didn't
        # match our lock_id -- e.g. the lease expired and somebody
        # else now holds the row. ClusterLock should treat that as
        # exceptional rather than logging and moving on.
        self.assertRaises(exceptions.LockNotHeld, lock.release)

    @mock.patch('shakenfist.locks.mariadb.acquire_cluster_lock',
                return_value=True)
    @mock.patch('shakenfist.locks.mariadb.release_cluster_lock',
                return_value=True)
    def test_lost_event_set_when_refresh_returns_false(
            self, mock_release, mock_acquire):
        # First refresh returns False -> confirmed loss. Subsequent
        # ones never run (refresher exits).
        with mock.patch(
                'shakenfist.locks.mariadb.refresh_cluster_lock',
                return_value=False):
            with mock.patch.object(
                    locks, 'REFRESH_INTERVAL', new=0):
                lock = self._make_lock()
                self.assertTrue(lock.acquire())
                self.assertTrue(_wait_for(lock.lost_event.is_set))
        # Tidy up so the daemon thread joins cleanly.
        lock.release()

    @mock.patch('shakenfist.locks.mariadb.acquire_cluster_lock',
                return_value=True)
    @mock.patch('shakenfist.locks.mariadb.release_cluster_lock',
                return_value=True)
    def test_transient_refresh_error_does_not_set_lost(
            self, mock_release, mock_acquire):
        # Refresh raises a transient error twice, then succeeds -- the
        # refresher should retry rather than declare the lock lost.
        from sqlalchemy.exc import OperationalError
        call_count = {'n': 0}

        def flaky(*_args, **_kwargs):
            call_count['n'] += 1
            if call_count['n'] <= 2:
                raise OperationalError('stmt', {}, Exception('oops'))
            return True

        with mock.patch(
                'shakenfist.locks.mariadb.refresh_cluster_lock',
                side_effect=flaky):
            with mock.patch.object(locks, 'REFRESH_INTERVAL', new=0):
                with mock.patch.object(locks, 'REFRESH_RETRY', new=0):
                    lock = self._make_lock()
                    self.assertTrue(lock.acquire())
                    # Wait until we have observed at least three
                    # refresh attempts.
                    _wait_for(lambda: call_count['n'] >= 3)
                    self.assertFalse(lock.lost_event.is_set())
        lock.release()
