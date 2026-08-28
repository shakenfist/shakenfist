# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.daemons.transfers import main as transfers_main
from shakenfist.tests import base


class TransfersBackoffTestCase(base.ShakenFistTestCase):
    def _make_monitor(self):
        # Construct a Monitor without running __init__ (which touches
        # setproctitle, logging and the filesystem). _run_inner only reads the
        # attributes and instance methods stubbed below.
        m = transfers_main.Monitor.__new__(transfers_main.Monitor)
        m.abort_path = '/run/sf/_test.abort'
        m.workers = {}
        m.wait_for_nodelock = mock.MagicMock()
        m.reap_workers = mock.MagicMock()
        return m

    def _run_schedule(self, m, schedule):
        # Run one loop iteration per schedule entry, where each entry is the
        # transfer list that poll returns. Returns the idle() sleeps requested.
        idle_calls = []
        m.idle = mock.MagicMock(side_effect=lambda s: idle_calls.append(s))

        polls = {'n': 0}

        def fake_abort(path):
            polls['n'] += 1
            return polls['n'] <= len(schedule)

        with mock.patch.object(
                daemon, 'check_abort_path', side_effect=fake_abort), \
                mock.patch(
                    'shakenfist.daemons.transfers.main.mariadb') as mock_mdb, \
                mock.patch('shakenfist.daemons.transfers.main.config'):
            mock_mdb.get_blob_transfers_for_node.side_effect = list(schedule)
            m._run_inner()

        return idle_calls

    def _run_n_iterations(self, m, transfers, iterations):
        return self._run_schedule(m, [transfers] * iterations)

    def test_backs_off_when_idle(self):
        m = self._make_monitor()
        # No transfers on every poll: the sleep grows geometrically to the cap.
        idle_calls = self._run_n_iterations(m, [], 5)
        self.assertEqual([0.2, 0.4, 0.8, 1.6, 2.0], idle_calls)

    def test_stays_fast_while_work_present(self):
        m = self._make_monitor()
        # A transfer whose worker already exists, so no thread is spawned but
        # the poll is non-empty: the loop keeps polling at the fast interval.
        transfer = mock.MagicMock()
        transfer.transfer_name = 't1'
        m.workers = {'t1': {'object': None, 'thread': None}}

        idle_calls = self._run_n_iterations(m, [transfer], 4)
        self.assertEqual(
            [daemon.IDLE_POLL_FAST_SECONDS] * 4, idle_calls)

    def test_wakes_from_backoff_when_work_arrives(self):
        # The transition case between the two tests above: backoff has grown
        # to the cap, then work arrives, and the very next empty poll is fast
        # again. Waking is what makes the longer idle sleeps safe. See issue
        # #3943.
        m = self._make_monitor()
        transfer = mock.MagicMock()
        transfer.transfer_name = 't1'
        m.workers = {'t1': {'object': None, 'thread': None}}

        idle_calls = self._run_schedule(m, [[]] * 5 + [[transfer]] + [[]])
        self.assertEqual(
            [0.2, 0.4, 0.8, 1.6, 2.0, daemon.IDLE_POLL_FAST_SECONDS, 0.2],
            idle_calls)
