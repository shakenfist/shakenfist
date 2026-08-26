# Copyright 2019 Michael Still and contributors

"""Tests for watchdog petting from the database retry loop (issue 3789).

A database stall must degrade into retries and DatabaseUnavailable, not
into a watchdog SIGABRT of every non-database daemon. _grpc_call() blocks
for up to a full deadline per attempt, so a daemon main loop correctly
waiting out a slow database pets the systemd watchdog between attempts
via the callback Daemon.__init__ installs with set_watchdog_petter().
"""

import os
import re
import threading
from unittest import mock

import grpc

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.daemons import daemon
from shakenfist.tests import base


class FakeRpcError(grpc.RpcError):
    def __init__(self, code, details=None):
        self._code = code
        self._details = details

    def code(self):
        return self._code

    def details(self):
        return self._details


class GrpcCallWatchdogPettingTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # The petter is process-global; never leak one into other tests.
        self.addCleanup(mariadb.set_watchdog_petter, None)

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_petter_called_before_every_attempt(
            self, mock_stub, mock_reset, mock_time):
        petter = mock.MagicMock()
        mariadb.set_watchdog_petter(petter)

        method = mock.MagicMock(
            side_effect=[FakeRpcError(grpc.StatusCode.UNAVAILABLE)] * 2 +
            ['ok'])
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertEqual('ok', mariadb._grpc_call(method, mock.MagicMock()))
        self.assertEqual(3, petter.call_count)

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_petter_called_during_deadline_exhaustion(
            self, mock_stub, mock_reset, mock_time):
        # The issue 3789 shape: every attempt burns the full deadline. The
        # pet before each attempt is what keeps the caller inside
        # WatchdogSec while that plays out.
        petter = mock.MagicMock()
        mariadb.set_watchdog_petter(petter)

        method = mock.MagicMock(
            side_effect=FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED))
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb._grpc_call, method, mock.MagicMock())
        self.assertEqual(mariadb.GRPC_RETRIES, petter.call_count)

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_no_petter_installed_is_a_noop(
            self, mock_stub, mock_reset, mock_time):
        # sf-ctl, gunicorn workers and tests never install a petter; the
        # retry loop must work identically without one.
        mariadb.set_watchdog_petter(None)

        method = mock.MagicMock(
            side_effect=[FakeRpcError(grpc.StatusCode.UNAVAILABLE), 'ok'])
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertEqual('ok', mariadb._grpc_call(method, mock.MagicMock()))


class DaemonDatabaseWaitPetTestCase(base.ShakenFistTestCase):
    def _make_daemon(self):
        # Construct a Daemon without running __init__ (which touches
        # setproctitle, signal handlers and the filesystem).
        d = daemon.Daemon.__new__(daemon.Daemon)
        d._last_watchdog = 0.0
        return d

    def test_main_thread_pets(self):
        d = self._make_daemon()
        d.pet_watchdog = mock.MagicMock()
        d._pet_watchdog_from_database_wait()
        d.pet_watchdog.assert_called_once()

    def test_worker_thread_does_not_pet(self):
        # The watchdog exists to catch a wedged main loop: a worker thread
        # retrying against a slow database must not keep one alive.
        d = self._make_daemon()
        d.pet_watchdog = mock.MagicMock()

        t = threading.Thread(target=d._pet_watchdog_from_database_wait)
        t.start()
        t.join()

        d.pet_watchdog.assert_not_called()

    @mock.patch('shakenfist.daemons.daemon.clear_abort_path')
    @mock.patch('shakenfist.daemons.daemon.faulthandler.register')
    @mock.patch('shakenfist.daemons.daemon.signal.signal')
    @mock.patch('shakenfist.daemons.daemon.setproctitle')
    @mock.patch('shakenfist.daemons.daemon.set_caller_identity')
    def test_init_installs_the_petter(
            self, mock_identity, mock_proctitle, mock_signal, mock_fault,
            mock_clear):
        self.addCleanup(mariadb.set_watchdog_petter, None)
        d = daemon.Daemon('queues')
        self.assertEqual(
            d._pet_watchdog_from_database_wait, mariadb._watchdog_petter)


class WatchdogBudgetInvariantTestCase(base.ShakenFistTestCase):
    def test_default_budget_pets_inside_smallest_watchdogsec(self):
        # The structural invariant behind issue 3789: with the retry loop
        # petting before every attempt, the longest a default-budget
        # _grpc_call can go without a pet is one stale pet window on entry
        # (the pet is rate-limited to WATCHDOG_PET_INTERVAL) plus one full
        # attempt deadline plus the largest backoff sleep. That stretch
        # must fit inside the smallest WatchdogSec any daemon unit arms,
        # or a database stall is converted back into a SIGABRT.
        service_template = os.path.join(
            os.path.dirname(__file__), '..', 'deploy', 'collection',
            'roles', 'node', 'templates', 'sf.service')
        with open(service_template) as f:
            watchdog_secs = [
                int(m) for m in re.findall(r'WatchdogSec=(\d+)s', f.read())]
        self.assertTrue(watchdog_secs, 'no WatchdogSec found in sf.service')

        worst_unpetted_stretch = (
            daemon.WATCHDOG_PET_INTERVAL +
            mariadb.GRPC_TIMEOUT +
            mariadb.GRPC_RETRY_DELAY * (mariadb.GRPC_UNAVAILABLE_RETRIES - 1))
        self.assertLess(worst_unpetted_stretch, min(watchdog_secs))
