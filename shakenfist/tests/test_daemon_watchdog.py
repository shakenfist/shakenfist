# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.tests import base


class DaemonWatchdogTestCase(base.ShakenFistTestCase):
    def _make_daemon(self):
        # Construct a Daemon without running __init__ (which touches setproctitle,
        # logging and the filesystem). We only need the attributes pet_watchdog()
        # and idle() actually read.
        d = daemon.Daemon.__new__(daemon.Daemon)
        d._last_watchdog = 0.0
        d.abort_path = '/run/sf/_test.abort'
        return d

    @mock.patch('shakenfist.daemons.daemon.send_systemd_watchdog')
    @mock.patch('shakenfist.daemons.daemon.time.time')
    def test_first_pet_emits(self, mock_time, mock_watchdog):
        d = self._make_daemon()
        mock_time.return_value = 100.0
        d.pet_watchdog()
        self.assertEqual(1, mock_watchdog.call_count)
        self.assertEqual(100.0, d._last_watchdog)

    @mock.patch('shakenfist.daemons.daemon.send_systemd_watchdog')
    @mock.patch('shakenfist.daemons.daemon.time.time')
    def test_rate_limited_within_window(self, mock_time, mock_watchdog):
        d = self._make_daemon()

        # First pet at t=100 emits.
        mock_time.return_value = 100.0
        d.pet_watchdog()

        # Repeated pets within the WATCHDOG_PET_INTERVAL window do not emit.
        for t in (100.2, 105.0, 109.9):
            mock_time.return_value = t
            d.pet_watchdog()

        self.assertEqual(1, mock_watchdog.call_count)

    @mock.patch('shakenfist.daemons.daemon.send_systemd_watchdog')
    @mock.patch('shakenfist.daemons.daemon.time.time')
    def test_emit_again_after_interval(self, mock_time, mock_watchdog):
        d = self._make_daemon()

        mock_time.return_value = 100.0
        d.pet_watchdog()

        # Exactly at the interval boundary a second pet is allowed.
        mock_time.return_value = 100.0 + daemon.WATCHDOG_PET_INTERVAL
        d.pet_watchdog()

        self.assertEqual(2, mock_watchdog.call_count)
        self.assertEqual(110.0, d._last_watchdog)

    @mock.patch('shakenfist.daemons.daemon.os.path.exists')
    @mock.patch('shakenfist.daemons.daemon.send_systemd_watchdog')
    @mock.patch('shakenfist.daemons.daemon.time.sleep')
    @mock.patch('shakenfist.daemons.daemon.time.time')
    def test_idle_pets_on_cadence(self, mock_time, mock_sleep, mock_watchdog,
                                  mock_exists):
        d = self._make_daemon()
        d.check_daemon_state = mock.MagicMock()
        # Never abort during the simulated idle.
        mock_exists.return_value = False

        # idle(60) loops 300 times on a 0.2s tick. Advance the simulated clock
        # by 0.2s per tick so pet_watchdog() observes real elapsed time. sleep()
        # is a no-op so the test does not actually wait.
        ticks = [0.2 * i for i in range(301)]
        mock_time.side_effect = ticks

        d.idle(60)

        # 300 ticks of 0.2s == 60s of simulated time. With a 10s pet interval we
        # expect roughly one emit every 10s, not one per tick (which would be
        # 300). The first tick at t=0.2 emits (since _last_watchdog starts at
        # 0.0), then every ~10s thereafter.
        self.assertGreater(mock_watchdog.call_count, 1)
        self.assertLess(mock_watchdog.call_count, 10)

    def test_helper_noop_without_notify_socket(self):
        # The underlying helper is gated on NOTIFY_SOCKET; with it unset
        # send_systemd_watchdog() must be a silent no-op rather than raising.
        with mock.patch.dict('shakenfist.daemons.daemon.os.environ',
                             clear=True):
            daemon.send_systemd_watchdog()
