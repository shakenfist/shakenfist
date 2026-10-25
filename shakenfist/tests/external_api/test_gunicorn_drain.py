# Copyright 2019 Michael Still and contributors
"""Tests for the SIGTERM drain handler installed by post_worker_init.

post_worker_init installs a *real* process-wide SIGTERM handler, so each
test captures and restores the original handler via addCleanup to avoid
leaking a drain handler across the rest of the suite.
"""
import logging
import signal
import threading
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


class _FakeThread:
    """Stand-in for threading.Thread that records its args and never runs.

    post_worker_init starts the drain thread itself; capturing it lets
    tests run its target deterministically, after the signal handler,
    without a real thread or a real grace period.
    """
    instances = []

    def __init__(self, target, args, name, daemon):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.started = False
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True

    def run_target(self):
        self.target(*self.args)


class _FakeWorker:
    pid = 4242


class _LockedLogHandler(logging.Handler):
    """A log handler which needs a non-reentrant lock, as the logship spool's does."""

    def __init__(self, lock):
        super().__init__()
        self.spool_lock = lock
        self.blocked = []

    def emit(self, record):
        # A real spool would wait forever here; a bounded wait lets the test
        # report the deadlock instead of hanging.
        if not self.spool_lock.acquire(timeout=0.5):
            self.blocked.append(record.getMessage())
            return
        self.spool_lock.release()


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

        _FakeThread.instances = []
        self.addCleanup(lambda: setattr(_FakeThread, 'instances', []))

    def _install(self, orig):
        signal.signal(signal.SIGTERM, orig)
        with mock.patch.object(gunicorn_config.threading, 'Thread', _FakeThread):
            gunicorn_config.post_worker_init(_FakeWorker())
        self.assertEqual(1, len(_FakeThread.instances))
        return signal.getsignal(signal.SIGTERM), _FakeThread.instances[0]

    def test_post_worker_init_installs_handler_and_starts_the_drainer(self):
        sentinel_orig = mock.Mock()
        installed, drainer = self._install(sentinel_orig)
        self.assertNotEqual(sentinel_orig, installed)
        self.assertTrue(callable(installed))
        # Started before any SIGTERM: the handler must not create threads.
        self.assertTrue(drainer.started)
        self.assertTrue(drainer.daemon)

    def test_sigterm_begins_drain_and_defers(self):
        sentinel_orig = mock.Mock()
        handler, drainer = self._install(sentinel_orig)
        self.assertFalse(health.is_draining())

        handler(signal.SIGTERM, None)

        # Drain flag flipped, /readyz will now report 503, and the original
        # handler is NOT called synchronously.
        self.assertTrue(health.is_draining())
        sentinel_orig.assert_not_called()

        # The drain thread sleeps the grace period, then chains to the
        # original handler with the captured signum/frame.
        with mock.patch.object(gunicorn_config.time, 'sleep') as mock_sleep:
            drainer.run_target()
        mock_sleep.assert_called_once_with(config.API_DRAIN_GRACE)
        sentinel_orig.assert_called_once_with(signal.SIGTERM, None)

    def test_the_drainer_waits_for_sigterm(self):
        sentinel_orig = mock.Mock()
        _, drainer = self._install(sentinel_orig)
        _worker, _orig, requested, _received = drainer.args
        self.assertFalse(requested.is_set())

    def test_second_sigterm_is_idempotent(self):
        sentinel_orig = mock.Mock()
        handler, drainer = self._install(sentinel_orig)

        handler(signal.SIGTERM, None)
        _worker, _orig, _requested, received = drainer.args
        first = received['signal']
        handler(signal.SIGTERM, object())

        self.assertTrue(health.is_draining())
        self.assertIs(first, received['signal'])

    def test_the_handler_does_not_log(self):
        # The handler runs between two bytecodes of the worker's main
        # thread, which logs on every request through a handler that holds
        # a non-reentrant lock. Logging from the handler while that lock was
        # held deadlocked the worker, which then served nothing until
        # gunicorn SIGKILLed it (#3495).
        handler, _ = self._install(mock.Mock())
        lock = threading.Lock()
        log_handler = _LockedLogHandler(lock)
        root = logging.getLogger()
        root.addHandler(log_handler)
        self.addCleanup(root.removeHandler, log_handler)
        loggers = [logging.getLogger(name) for name in
                   ('shakenfist.external_api.gunicorn_config', 'shakenfist.external_api.health')]
        for logger in loggers:
            logger.addHandler(log_handler)
            self.addCleanup(logger.removeHandler, log_handler)

        with lock:
            # As if the signal landed while the main thread was mid-log.
            handler(signal.SIGTERM, None)

        self.assertEqual([], log_handler.blocked)
        self.assertTrue(health.is_draining())

    def test_non_callable_orig_falls_back_to_systemexit(self):
        # If gunicorn left SIG_DFL / SIG_IGN installed (not callable), the
        # drain thread raises SystemExit to unwind itself. This path is
        # unreachable under normal gunicorn; it cannot itself stop the
        # process from a non-main thread, so systemd's TimeoutStopSec is the
        # real backstop.
        handler, drainer = self._install(signal.SIG_DFL)
        handler(signal.SIGTERM, None)
        with mock.patch.object(gunicorn_config.time, 'sleep'):
            self.assertRaises(SystemExit, drainer.run_target)
