# Copyright 2019 Michael Still and contributors
"""Tests for the SIGTERM drain handler installed by post_worker_init.

post_worker_init installs a *real* process-wide SIGTERM handler, so each
test captures and restores the original handler via addCleanup to avoid
leaking a drain handler across the rest of the suite.
"""
import signal
from unittest import mock

from gunicorn.config import Config
from gunicorn.config import KNOWN_SETTINGS

from shakenfist.config import config
from shakenfist.external_api import gunicorn_config
from shakenfist.external_api import health
from shakenfist.tests import base


class GunicornConfigGlobalsTestCase(base.ShakenFistTestCase):
    """Guard against the "Error: Not a string" gunicorn startup failure.

    gunicorn loads gunicorn_config.py as its --config module and applies any
    module-level global whose name matches one of its own settings. A stray
    global named after a setting (notably `config`) makes gunicorn abort at
    startup -- and unit tests that merely import the module never catch it,
    because only gunicorn scans the globals. This test asserts that no
    module global collides with a gunicorn setting except the hooks we
    deliberately define.
    """

    def test_no_module_global_collides_with_gunicorn_setting(self):
        setting_names = {s.name for s in KNOWN_SETTINGS}
        module_globals = {
            name for name in vars(gunicorn_config) if not name.startswith('_')}
        # post_fork / post_worker_init are gunicorn server hooks we define on
        # purpose. logger_class is also a gunicorn setting we set on purpose:
        # gunicorn reads it from the config module's globals to install our
        # mode-aware SFGunicornLogger (it is a dotted-path string, not the
        # "Error: Not a string" trap the bare `config` global causes).
        allowed_hooks = {'post_fork', 'post_worker_init', 'logger_class'}
        collisions = (module_globals & setting_names) - allowed_hooks
        self.assertEqual(
            set(), collisions,
            f'gunicorn_config exposes module globals that collide with '
            f'gunicorn settings and will break sf-api startup: {collisions}. '
            f'Rename or alias them (e.g. config -> sf_config).')


class SFGunicornLoggerTestCase(base.ShakenFistTestCase):
    """The mode-aware gunicorn logger_class (5b of the Loki plan).

    With LOKI_BASE_URL set (Mode A), gunicorn's access/error loggers must
    propagate to the root logger (where logship attaches the Loki handler)
    and have their own stream handlers cleared, so dropping --log-syslog
    loses nothing. With LOKI_BASE_URL empty (Mode B), gunicorn's defaults
    (stderr -> journald) are left untouched.
    """

    def test_mode_a_propagates_and_clears_handlers(self):
        with mock.patch.object(
                gunicorn_config.sf_config, 'LOKI_BASE_URL',
                'http://loki:3100'):
            logger = gunicorn_config.SFGunicornLogger(Config())

        self.assertTrue(logger.error_log.propagate)
        self.assertTrue(logger.access_log.propagate)
        self.assertEqual([], logger.error_log.handlers)
        self.assertEqual([], logger.access_log.handlers)

    def test_mode_b_keeps_gunicorn_defaults(self):
        # Construct a default gunicorn logger to capture its out-of-the-box
        # propagate/handler posture, then assert our subclass matches it
        # when no Loki endpoint is configured.
        default = gunicorn_config.gunicorn.glogging.Logger(Config())

        with mock.patch.object(
                gunicorn_config.sf_config, 'LOKI_BASE_URL', ''):
            logger = gunicorn_config.SFGunicornLogger(Config())

        # gunicorn sets propagate=False on both in __init__; we must not
        # touch that in Mode B.
        self.assertFalse(logger.error_log.propagate)
        self.assertFalse(logger.access_log.propagate)
        self.assertEqual(
            len(default.error_log.handlers), len(logger.error_log.handlers))
        self.assertEqual(
            len(default.access_log.handlers), len(logger.access_log.handlers))

    def test_logger_class_points_at_subclass(self):
        # gunicorn loads this dotted path from the config module's globals.
        self.assertEqual(
            'shakenfist.external_api.gunicorn_config.SFGunicornLogger',
            gunicorn_config.logger_class)


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
        # deferred target raises SystemExit to unwind the timer thread. This
        # path is unreachable under normal gunicorn; it cannot itself stop the
        # process from a non-main thread, so systemd's TimeoutStopSec is the
        # real backstop.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        gunicorn_config.post_worker_init(_FakeWorker())
        handler = signal.getsignal(signal.SIGTERM)

        with mock.patch.object(
                gunicorn_config.threading, 'Timer', _FakeTimer):
            handler(signal.SIGTERM, None)

        self.assertEqual(1, len(_FakeTimer.instances))
        timer = _FakeTimer.instances[0]
        self.assertRaises(SystemExit, timer.function)
