# Copyright 2019 Michael Still and contributors

import os
import shutil
import signal
import tempfile
import time
from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.tests import base


class IdlePollBackoffTestCase(base.ShakenFistTestCase):
    def test_starts_fast(self):
        b = daemon.IdlePollBackoff(fast=0.2, maximum=2.0, factor=2.0)
        # The first empty poll still sleeps the fast interval so a brief lull
        # is cheap.
        self.assertEqual(0.2, b.next_empty_interval())

    def test_grows_geometrically_and_caps(self):
        b = daemon.IdlePollBackoff(fast=0.2, maximum=2.0, factor=2.0)
        self.assertEqual(
            [0.2, 0.4, 0.8, 1.6, 2.0, 2.0],
            [b.next_empty_interval() for _ in range(6)])

    def test_reset_returns_to_fast(self):
        b = daemon.IdlePollBackoff(fast=0.2, maximum=2.0, factor=2.0)
        for _ in range(4):
            b.next_empty_interval()
        b.reset()
        # After work is found the next empty poll is fast again.
        self.assertEqual(0.2, b.next_empty_interval())

    def test_defaults_match_module_constants(self):
        b = daemon.IdlePollBackoff()
        self.assertEqual(daemon.IDLE_POLL_FAST_SECONDS, b.next_empty_interval())


class IdleRoundingTestCase(base.ShakenFistTestCase):
    def _make_daemon(self):
        d = daemon.Daemon.__new__(daemon.Daemon)
        d._last_watchdog = 0.0
        d.abort_path = '/run/sf/_test.abort'
        return d

    @mock.patch('shakenfist.daemons.daemon.os.path.exists', return_value=False)
    @mock.patch('shakenfist.daemons.daemon.Daemon.pet_watchdog')
    @mock.patch('shakenfist.daemons.daemon.Daemon.check_daemon_state')
    @mock.patch('shakenfist.daemons.daemon.time.sleep')
    def test_fractional_interval_rounds_to_nearest_chunk(
            self, mock_sleep, _state, _watchdog, _exists):
        d = self._make_daemon()
        # 0.8s is four 0.2s chunks; int() truncation used to yield three.
        d.idle(0.8)
        self.assertEqual(4, mock_sleep.call_count)

    @mock.patch('shakenfist.daemons.daemon.os.path.exists', return_value=False)
    @mock.patch('shakenfist.daemons.daemon.Daemon.pet_watchdog')
    @mock.patch('shakenfist.daemons.daemon.Daemon.check_daemon_state')
    @mock.patch('shakenfist.daemons.daemon.time.sleep')
    def test_fast_interval_sleeps_one_chunk(
            self, mock_sleep, _state, _watchdog, _exists):
        d = self._make_daemon()
        d.idle(0.2)
        self.assertEqual(1, mock_sleep.call_count)

    @mock.patch('shakenfist.daemons.daemon.Daemon.pet_watchdog')
    @mock.patch('shakenfist.daemons.daemon.Daemon.check_daemon_state')
    @mock.patch('shakenfist.daemons.daemon.time.sleep')
    def test_abort_path_breaks_idle_early(
            self, mock_sleep, _state, _watchdog):
        d = self._make_daemon()
        # The abort path appears after the second chunk of a ten chunk sleep:
        # idle() must stop there rather than sleeping out the remaining 1.6s.
        # This is what keeps shutdown prompt even at the backoff cap. See
        # issue #3943.
        with mock.patch('shakenfist.daemons.daemon.os.path.exists',
                        side_effect=[False, True]):
            d.idle(2.0)
        self.assertEqual(2, mock_sleep.call_count)


class ExitGracefullyTestCase(base.ShakenFistTestCase):
    """The SIGTERM handler only writes the abort file.

    It runs on the main thread between any two bytecodes, so logging or
    database access from it could wait on a lock the main thread holds
    (#4601, #3495).
    """

    def _make_daemon(self):
        tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tempdir)
        d = daemon.Daemon.__new__(daemon.Daemon)
        d.abort_path = os.path.join(tempdir, '_test.abort')
        d.daemon_name = 'unittest'
        d.log = mock.MagicMock()
        return d

    @mock.patch('shakenfist.daemons.daemon.LOG')
    @mock.patch('shakenfist.daemons.daemon.Node')
    def test_sigterm_writes_the_abort_file_and_nothing_else(self, mock_node, mock_log):
        d = self._make_daemon()
        d.exit_gracefully(signal.SIGTERM, None)
        self.assertFalse(daemon.check_abort_path(d.abort_path))
        mock_node.assert_not_called()
        mock_node.this_node.assert_not_called()
        self.assertEqual([], d.log.mock_calls)
        self.assertEqual([], mock_log.mock_calls)

    @mock.patch('shakenfist.daemons.daemon.Node')
    def test_other_signals_are_ignored(self, mock_node):
        d = self._make_daemon()
        d.exit_gracefully(signal.SIGINT, None)
        self.assertTrue(daemon.check_abort_path(d.abort_path))
        self.assertFalse(d.sigterm_unlogged)

    def test_the_main_thread_logs_the_sigterm_once(self):
        d = self._make_daemon()
        d.exit_gracefully(signal.SIGTERM, None)

        # Rate limited, so check_daemon_state() returns before any read.
        d._last_daemon_state_check = time.time()
        d._daemon_state_poll_interval = 3600
        d.check_daemon_state()
        d.check_daemon_state()
        d.log.info.assert_called_once_with('Caught SIGTERM, terminating')

    @mock.patch('shakenfist.daemons.daemon.send_systemd_status')
    @mock.patch('shakenfist.daemons.daemon.Node')
    def test_an_exit_without_another_state_check_still_logs_the_sigterm(self, mock_node, mock_status):
        # A loop which sees the abort file and exits never calls
        # check_daemon_state() again; record_exit() is on every exit path.
        d = self._make_daemon()
        d.exit_gracefully(signal.SIGTERM, None)
        d.record_exit()
        d.record_exit()
        d.log.info.assert_called_once_with('Caught SIGTERM, terminating')
