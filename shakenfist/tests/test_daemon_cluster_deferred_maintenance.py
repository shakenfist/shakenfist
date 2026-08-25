# Copyright 2026 Michael Still and contributors
#
# Tests for the cluster maintainer's deferred-maintenance handling
# (issue 3802): an unstable cluster must defer maintenance while
# staying elected and holding the lock, and _await_election must never
# orphan a lock the process still holds -- an orphaned lock's
# refresher thread renews the lease forever, deadlocking the daemon
# against itself.

from unittest import mock

from shakenfist import exceptions
from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base


class ClusterDeferredMaintenanceTestCase(base.ShakenFistTestCase):
    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = mock.MagicMock()
        m.lock.is_acquired.return_value = False
        m.is_elected = True
        m.abort_path = '/nonexistent/sf-test-cluster-abort-path'
        m.pet_watchdog = mock.MagicMock()
        m.check_daemon_state = mock.MagicMock()
        m._await_election = mock.MagicMock()
        m._anchor_scheduled_jobs = mock.MagicMock()
        m._run_due_scheduled_jobs = mock.MagicMock()
        m._cluster_wide_cleanup = mock.MagicMock()
        return m

    @mock.patch('shakenfist.daemons.cluster.main.scheduled_tasks')
    @mock.patch('shakenfist.daemons.cluster.main.schedule')
    @mock.patch('shakenfist.daemons.cluster.main.daemon.check_abort_path',
                side_effect=[True, False])
    def test_defer_keeps_lock_and_election(
            self, mock_abort, mock_schedule, mock_tasks):
        # While the cluster is unstable the elected node defers
        # maintenance, but stays elected and keeps the lock -- it must
        # not release it, and must not re-enter the election.
        m = self._make_monitor()
        m.cluster_stable = mock.MagicMock(return_value=False)
        m.lock.lost_event.wait.side_effect = [False, False, True]

        m._run_inner()

        self.assertEqual(1, m._await_election.call_count)
        m.lock.release.assert_not_called()
        m._run_due_scheduled_jobs.assert_not_called()
        m._cluster_wide_cleanup.assert_not_called()
        # The stability re-check ran on every pass of the elected loop.
        self.assertEqual(3, m.cluster_stable.call_count)

    @mock.patch('shakenfist.daemons.cluster.main.scheduled_tasks')
    @mock.patch('shakenfist.daemons.cluster.main.schedule')
    @mock.patch('shakenfist.daemons.cluster.main.daemon.check_abort_path',
                side_effect=[True, False])
    def test_maintenance_resumes_when_stability_returns(
            self, mock_abort, mock_schedule, mock_tasks):
        # Stability returning on a later pass starts maintenance without
        # any round trip through the election.
        m = self._make_monitor()
        m.cluster_stable = mock.MagicMock(side_effect=[False, True])
        m.lock.lost_event.wait.side_effect = [False, True]

        m._run_inner()

        self.assertEqual(1, m._await_election.call_count)
        m.lock.release.assert_not_called()
        m._run_due_scheduled_jobs.assert_called_once()
        m._cluster_wide_cleanup.assert_called_once()


class ClusterAwaitElectionLockGuardTestCase(base.ShakenFistTestCase):
    """_await_election must release a still-held lock before creating a
    new candidate lock, so a held lock object can never be orphaned with
    its refresher still renewing the lease."""

    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.is_elected = False
        m.abort_path = '/nonexistent/sf-test-cluster-abort-path'
        return m

    @mock.patch('shakenfist.daemons.cluster.main.daemon.check_abort_path',
                return_value=False)
    def test_held_lock_released_before_election(self, mock_abort):
        m = self._make_monitor()
        held = mock.MagicMock()
        held.is_acquired.return_value = True
        m.lock = held

        m._await_election()

        held.release.assert_called_once()
        self.assertIsNone(m.lock)

    @mock.patch('shakenfist.daemons.cluster.main.daemon.check_abort_path',
                return_value=False)
    def test_lost_lock_not_released(self, mock_abort):
        # A lock whose lease has already lapsed (another node stole it)
        # is not released -- that would emit a spurious "not holding"
        # error on every normal re-election.
        m = self._make_monitor()
        lost = mock.MagicMock()
        lost.is_acquired.return_value = False
        m.lock = lost

        m._await_election()

        lost.release.assert_not_called()
        self.assertIsNone(m.lock)

    @mock.patch('shakenfist.daemons.cluster.main.daemon.check_abort_path',
                return_value=False)
    def test_lock_not_held_race_swallowed(self, mock_abort):
        # The lease can lapse between the is_acquired check and the
        # release; the resulting LockNotHeld is benign here.
        m = self._make_monitor()
        held = mock.MagicMock()
        held.is_acquired.return_value = True
        held.release.side_effect = exceptions.LockNotHeld('gone')
        m.lock = held

        m._await_election()

        held.release.assert_called_once()
        self.assertIsNone(m.lock)
