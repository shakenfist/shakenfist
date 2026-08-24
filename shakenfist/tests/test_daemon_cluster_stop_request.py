# Copyright 2026 Michael Still and contributors
#
# Tests for the elected cluster maintainer noticing an externally
# written stop request (issue 3874): sf-ctl stop cluster writes
# DAEMON_STATE_STOPPING to the daemon's node_daemon_states row, which
# is only noticed by check_daemon_state(). The elected loop sleeps on
# lock.lost_event.wait() rather than idle(), so it must call
# check_daemon_state() itself -- without that call the request was
# ignored until the node lost election, which on a healthy cluster may
# be never.

import os
import tempfile
from unittest import mock

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base


class ClusterElectedStopRequestTestCase(base.ShakenFistTestCase):
    def _make_monitor(self, abort_path):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = mock.MagicMock()
        m.lock.is_acquired.return_value = False
        m.is_elected = True
        m.abort_path = abort_path
        m.pet_watchdog = mock.MagicMock()
        m._await_election = mock.MagicMock()
        m._run_due_scheduled_jobs = mock.MagicMock()
        m._cluster_wide_cleanup = mock.MagicMock()
        return m

    @mock.patch('shakenfist.daemons.cluster.main.scheduled_tasks')
    @mock.patch('shakenfist.daemons.cluster.main.schedule')
    @mock.patch('shakenfist.daemons.cluster.main.daemon.check_abort_path',
                side_effect=[True, False])
    def test_elected_loop_polls_daemon_state(
            self, mock_abort, mock_schedule, mock_tasks):
        # The elected loop must call check_daemon_state() on every pass,
        # exactly as it pets the watchdog on every pass -- it is the only
        # reader of an externally written stop request.
        m = self._make_monitor('/nonexistent/sf-test-cluster-abort-path')
        m.check_daemon_state = mock.MagicMock()
        m.cluster_stable = mock.MagicMock(return_value=False)
        m.lock.lost_event.wait.side_effect = [False, False, True]

        m._run_inner()

        self.assertEqual(3, m.check_daemon_state.call_count)

    @mock.patch('shakenfist.daemons.cluster.main.scheduled_tasks')
    @mock.patch('shakenfist.daemons.cluster.main.schedule')
    def test_stop_request_exits_elected_loop(
            self, mock_schedule, mock_tasks):
        # End to end within _run_inner(): when check_daemon_state()
        # notices the stop request and sets the abort path, the elected
        # loop and then the outer loop must both exit promptly -- while
        # still elected, without waiting to lose the election.
        with tempfile.TemporaryDirectory() as tmpdir:
            abort_path = os.path.join(tmpdir, 'cluster.abort')
            m = self._make_monitor(abort_path)
            m.cluster_stable = mock.MagicMock(return_value=False)

            def write_abort():
                with open(abort_path, 'w') as f:
                    f.write('1')
            m.check_daemon_state = mock.MagicMock(side_effect=write_abort)

            # The lease is never lost; a finite side_effect list means a
            # regression loops until StopIteration fails the test rather
            # than hanging it.
            m.lock.lost_event.wait.side_effect = [False] * 10

            m._run_inner()

            m.check_daemon_state.assert_called_once()
            self.assertEqual(1, m.lock.lost_event.wait.call_count)
            self.assertTrue(m.is_elected)
