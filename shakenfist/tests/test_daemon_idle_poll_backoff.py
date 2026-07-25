# Copyright 2019 Michael Still and contributors

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
