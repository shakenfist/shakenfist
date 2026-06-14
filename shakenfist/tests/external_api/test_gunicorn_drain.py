# Copyright 2019 Michael Still and contributors
"""Tests for the SIGTERM drain handler installed by post_worker_init.

post_worker_init installs a *real* process-wide SIGTERM handler, so each
test captures and restores the original handler via addCleanup to avoid
leaking a drain handler across the rest of the suite.
"""
import signal
from unittest import mock

from shakenfist.config import config
from shakenfist.external_api import gunicorn_config
from shakenfist.external_api import health
from shakenfist.tests import base


class _FakeTimer:
    """Stand-in for threading.Timer that records its args and never runs.

    Captures the interval and target function so tests can assert how the
    timer was armed and invoke the target deterministically, without
    actually waiting for the grace period to elapse.
    """
    instances = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.started = False
        self.daemon = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True


class _FakeWorker:
    pid = 4242


class GunicornDrainTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        # Reset readiness/draining state and restore it afterwards.
        health._reset_for_test()
        self.addCleanup(health._reset_for_test)

        # Capture and restore the process SIGTERM handler so the drain
        # handler installed by post_worker_init does not leak into the
        # rest of the test suite.
        self._orig_sigterm = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, self._orig_sigterm)

        # Reset the captured timers between tests.
        _FakeTimer.instances = []
        self.addCleanup(lambda: setattr(_FakeTimer, 'instances', []))

    def test_post_worker_init_installs_handler(self):
        # A sentinel original handler so we can confirm it is captured and
        # not invoked synchronously.
        sentinel_orig = mock.Mock()
        signal.signal(signal.SIGTERM, sentinel_orig)

        gunicorn_config.post_worker_init(_FakeWorker())

        installed = signal.getsignal(signal.SIGTERM)
        self.assertNotEqual(sentinel_orig, installed)
        self.assertTrue(callable(installed))

    def test_sigterm_begins_drain_and_defers(self):
        sentinel_orig = mock.Mock()
        signal.signal(signal.SIGTERM, sentinel_orig)

        gunicorn_config.post_worker_init(_FakeWorker())
        handler = signal.getsignal(signal.SIGTERM)

        self.assertFalse(health.is_draining())

        with mock.patch.object(
                gunicorn_config.threading, 'Timer', _FakeTimer):
            handler(signal.SIGTERM, None)

        # Drain flag flipped, /readyz will now report 503.
        self.assertTrue(health.is_draining())

        # The original handler is NOT called synchronously -- it is
        # deferred onto the timer.
        sentinel_orig.assert_not_called()

        # Exactly one timer armed, with the grace interval.
        self.assertEqual(1, len(_FakeTimer.instances))
        timer = _FakeTimer.instances[0]
        self.assertEqual(config.API_DRAIN_GRACE, timer.interval)
        self.assertTrue(timer.started)
        self.assertTrue(timer.daemon)

        # Invoking the timer's target chains to the original handler with
        # the captured signum/frame.
        timer.function()
        sentinel_orig.assert_called_once_with(signal.SIGTERM, None)

    def test_second_sigterm_is_idempotent(self):
        sentinel_orig = mock.Mock()
        signal.signal(signal.SIGTERM, sentinel_orig)

        gunicorn_config.post_worker_init(_FakeWorker())
        handler = signal.getsignal(signal.SIGTERM)

        with mock.patch.object(
                gunicorn_config.threading, 'Timer', _FakeTimer):
            handler(signal.SIGTERM, None)
            # A second SIGTERM during drain must not arm another timer.
            handler(signal.SIGTERM, None)

        self.assertTrue(health.is_draining())
        self.assertEqual(1, len(_FakeTimer.instances))

    def test_non_callable_orig_falls_back_to_systemexit(self):
        # If gunicorn left SIG_DFL / SIG_IGN installed (not callable), the
        # deferred target falls back to SystemExit so the worker still stops.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        gunicorn_config.post_worker_init(_FakeWorker())
        handler = signal.getsignal(signal.SIGTERM)

        with mock.patch.object(
                gunicorn_config.threading, 'Timer', _FakeTimer):
            handler(signal.SIGTERM, None)

        self.assertEqual(1, len(_FakeTimer.instances))
        timer = _FakeTimer.instances[0]
        self.assertRaises(SystemExit, timer.function)
