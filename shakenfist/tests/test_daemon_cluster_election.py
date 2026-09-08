# Copyright 2026 Michael Still and contributors
#
# Tests for the cluster maintainer election cadence. The election is a
# fixed-period lock poll, and issue 3663 showed that without jitter the
# candidates' relative poll order is nearly stable for minutes -- so a
# freed lock always went to whichever candidate's standing phase came
# next, never a fair draw. These tests pin the two fairness properties
# the fix introduced: every wait (including the one before the first
# acquire attempt) is drawn from a jittered range, and the range's mean
# preserves the budgeted acquire cadence.

import os
import tempfile
from unittest import mock

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base


class ClusterElectionJitterTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.abort_path = os.path.join(self.tempdir.name, 'cluster.abort')

    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = None
        m.is_elected = False
        m.abort_path = self.abort_path
        m.check_daemon_state = mock.MagicMock()
        return m

    @mock.patch('shakenfist.daemons.cluster.main.random.uniform',
                return_value=3.25)
    @mock.patch('shakenfist.daemons.cluster.main.locks.ClusterLock')
    def test_first_acquire_waits_a_jittered_interval(
            self, mock_lock_class, mock_uniform):
        # A daemon entering the election -- freshly restarted, or the
        # just-unseated holder -- must idle before its first acquire
        # attempt, so it cannot race ahead of the standing candidates.
        m = self._make_monitor()
        calls = []
        m.idle = mock.MagicMock(
            side_effect=lambda seconds: calls.append(('idle', seconds)))
        lock = mock_lock_class.return_value
        lock.acquire.side_effect = lambda: calls.append(('acquire',)) or True

        m._await_election()

        self.assertTrue(m.is_elected)
        self.assertEqual(('idle', 3.25), calls[0])
        self.assertEqual(('acquire',), calls[1])
        mock_uniform.assert_called_with(
            cluster_main.ELECTION_POLL_MINIMUM_SECONDS,
            cluster_main.ELECTION_POLL_MAXIMUM_SECONDS)

    @mock.patch('shakenfist.daemons.cluster.main.random.uniform',
                return_value=6.5)
    @mock.patch('shakenfist.daemons.cluster.main.locks.ClusterLock')
    def test_poll_between_attempts_is_jittered(
            self, mock_lock_class, mock_uniform):
        m = self._make_monitor()
        m.idle = mock.MagicMock()
        lock = mock_lock_class.return_value
        lock.acquire.side_effect = [False, False, True]

        m._await_election()

        self.assertTrue(m.is_elected)
        # One idle before the first attempt, one after each of the two
        # failed attempts -- every one of them jittered, never a bare
        # constant.
        self.assertEqual(
            [mock.call(6.5)] * 3, m.idle.call_args_list)
        for args in mock_uniform.call_args_list:
            self.assertEqual(
                mock.call(cluster_main.ELECTION_POLL_MINIMUM_SECONDS,
                          cluster_main.ELECTION_POLL_MAXIMUM_SECONDS),
                args)

    @mock.patch('shakenfist.daemons.cluster.main.locks.ClusterLock')
    def test_abort_during_initial_idle_prevents_acquire(
            self, mock_lock_class):
        # If the daemon is asked to stop while idling before its first
        # attempt, it must not go on to acquire the lock it would then
        # never service. The real idle() breaks early when the abort
        # file appears; simulate that by writing the file from idle.
        m = self._make_monitor()

        def _abort_during_idle(_seconds):
            with open(self.abort_path, 'w') as f:
                f.write('1')

        m.idle = mock.MagicMock(side_effect=_abort_during_idle)

        m._await_election()

        self.assertFalse(m.is_elected)
        mock_lock_class.assert_not_called()

    def test_jitter_range_mean_preserves_budget_cadence(self):
        # The AcquireLock/cluster entry in
        # shakenfist/data/database_load_budget.yaml is arithmetic over
        # one attempt per candidate per five seconds. The jitter range
        # may move, but its mean must stay at that cadence or the
        # budget entry and this constant are wrong together.
        self.assertEqual(
            5.0,
            (cluster_main.ELECTION_POLL_MINIMUM_SECONDS +
             cluster_main.ELECTION_POLL_MAXIMUM_SECONDS) / 2)
        self.assertLess(
            cluster_main.ELECTION_POLL_MINIMUM_SECONDS,
            cluster_main.ELECTION_POLL_MAXIMUM_SECONDS)
