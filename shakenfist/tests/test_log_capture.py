# Copyright 2026 Michael Still and contributors
"""A test's log records are captured, and reported only if it fails.

``shakenfist/tests/__init__.py`` has always said that logging goes to
stdout so stestr can capture it and show it only for a failing test.
Nothing captured it: the handlers ``logs.setup()`` installs bind the
real stdout at import time, which in a stestr worker is also the subunit
result stream, so every record went to the console and a test which
logged heavily ran at whatever speed that console could be drained
(issue #4991). These tests pin the capture, the restore, the "only on
failure" condition, and the guard against a test leaving a handler
behind which writes to the real stdout again.

``SyslogCaptureTestCase`` covers the other half of the same problem
(issue #5106): a run which does not inherit ``tox.ini``'s setenv gets a
``SysLogHandler('/dev/log')`` instead of a stdout handler, and the
capture above could not see it -- it is not a ``StreamHandler`` and has
no ``.stream``, so every record went to the host's syslog for the life
of the run.

The cases exercised through ``ShakenFistTestCase`` itself are defined
inside the test methods rather than at module level, so the unittest
loader does not collect and run them in their own right -- one of them
is meant to fail.
"""

import io
import logging
from logging import handlers as logging_handlers
import os
import subprocess
import sys
from unittest import mock
import weakref

import testtools

from shakenfist.tests import base
from shakenfist.tests import logcapture


class _Recorded(testtools.TestResult):
    """Remember the outcome and the details of one test run."""

    def __init__(self):
        super().__init__()
        self.outcome = None
        self.details = {}

    def addSuccess(self, test, details=None):
        self.outcome = 'success'
        self.details = details or {}

    def addFailure(self, test, err=None, details=None):
        self.outcome = 'failure'
        self.details = details or {}

    def addError(self, test, err=None, details=None):
        self.outcome = 'error'
        self.details = details or {}

    def addSkip(self, test, reason=None, details=None):
        self.outcome = 'skip'
        self.details = details or {}


class _ProbeMixin:
    """A fake stdout, and a logger handled the way logs.setup() does."""

    def _fake_stdout(self):
        """Stand in for the process's real stdout.

        Patching what logcapture considers to be stdout keeps these
        tests off the worker's actual stdout, which is the result
        stream. The patch is reverted before this test's own capture is
        checked for leaks, so a probe handler left attached is not
        reported as one.
        """
        fake = io.StringIO()
        patcher = mock.patch.object(logcapture, '_REAL_STDOUT', fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def _logger_writing_to_syslog(self, address,
                                  name='shakenfist.tests.syslogprobe'):
        """A logger handled the way logs.setup() handles one off tox.

        The unix address deliberately does not exist. SysLogHandler's
        createSocket() swallows OSError for a unix socket -- C's
        openlog() ignores connection errors too -- so the handler is
        built, reports unixsocket True, and is indistinguishable from one
        bound to /dev/log as far as logcapture is concerned, without this
        test ever writing a record to the real host journal.
        """
        logger = logging.getLogger(name)
        handler = logging_handlers.SysLogHandler(address=address)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(handler.close)
        original_level = logger.level
        logger.setLevel(logging.INFO)
        self.addCleanup(logger.setLevel, original_level)
        return logger, handler

    def _logger_writing_to(self, stream, name='shakenfist.tests.probe'):
        """A logger handled the way logs.setup() handles one."""
        logger = logging.getLogger(name)
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        original_level = logger.level
        logger.setLevel(logging.INFO)
        self.addCleanup(logger.setLevel, original_level)
        return logger, handler


class LogCaptureMechanismTestCase(_ProbeMixin, base.ShakenFistTestCase):
    def test_console_handler_is_found(self):
        fake = self._fake_stdout()
        logger, handler = self._logger_writing_to(fake)
        self.assertIn((logger, handler), logcapture.console_handlers())

    def test_a_stderr_handler_is_found_too(self):
        # shakenfist/client/ctl.py calls logging.basicConfig() at import
        # time, which hands the root logger a stderr handler for the
        # rest of the worker's life.
        fake = io.StringIO()
        patcher = mock.patch.object(logcapture, '_REAL_STDERR', fake)
        patcher.start()
        self.addCleanup(patcher.stop)

        logger, handler = self._logger_writing_to(fake)
        self.assertIn((logger, handler), logcapture.console_handlers())

    def test_forget_logger_drops_the_handlers(self):
        fake = self._fake_stdout()
        logger, handler = self._logger_writing_to(fake)

        logcapture.forget_logger(logger.name)
        self.assertEqual([], logger.handlers)
        self.assertNotIn((logger, handler), logcapture.console_handlers())

    def test_capture_redirects_then_restores(self):
        fake = self._fake_stdout()
        logger, handler = self._logger_writing_to(fake)

        capture = logcapture.LogCapture().start()
        logger.info('while captured')
        self.assertEqual('', fake.getvalue())

        captured = capture.stop(keep=True)
        self.assertIn('while captured', captured)
        self.assertIs(fake, handler.stream)

        logger.info('after capture')
        self.assertIn('after capture', fake.getvalue())
        self.assertNotIn('while captured', fake.getvalue())

    def test_stop_discards_the_buffer_unless_asked(self):
        fake = self._fake_stdout()
        logger, _handler = self._logger_writing_to(fake)

        capture = logcapture.LogCapture().start()
        logger.info('not wanted')
        self.assertIsNone(capture.stop())
        self.assertEqual('', fake.getvalue())

    def test_a_second_handler_on_a_handled_logger_is_a_duplicate(self):
        fake = self._fake_stdout()
        logger, _handler = self._logger_writing_to(fake)

        capture = logcapture.LogCapture().start()
        self.assertEqual([], capture.duplicate_handlers())

        second = logging.StreamHandler(fake)
        logger.addHandler(second)
        self.addCleanup(logger.removeHandler, second)
        self.assertEqual(['%s (StreamHandler)' % logger.name],
                         capture.duplicate_handlers())
        capture.stop()

    def test_a_first_handler_on_a_fresh_logger_is_not(self):
        # A module imported for the first time inside a test calls
        # logs.setup(), which is how that logger gets its one handler.
        # Failing the test which happened to import it first would make
        # the guard depend on how stestr split the suite.
        fake = self._fake_stdout()

        capture = logcapture.LogCapture().start()
        self._logger_writing_to(fake, name='shakenfist.tests.probe_fresh')
        self.assertEqual([], capture.duplicate_handlers())
        capture.stop()


class CapturedLogReportingTestCase(_ProbeMixin, base.ShakenFistTestCase):
    def _run_case(self, body):
        """Run one ShakenFistTestCase whose test method is `body`."""
        class _Case(base.ShakenFistTestCase):
            def runTest(inner):
                body(inner)

        recorded = _Recorded()
        _Case().run(recorded)
        return recorded

    def test_failing_test_reports_its_log(self):
        fake = self._fake_stdout()
        logger, _handler = self._logger_writing_to(fake)

        def body(inner):
            logger.info('why it broke')
            inner.fail('deliberate')

        recorded = self._run_case(body)
        self.assertEqual('failure', recorded.outcome)
        self.assertIn('log', recorded.details)
        self.assertIn('why it broke', recorded.details['log'].as_text())
        # Still nothing on stdout: the log came back as a detail, which
        # stestr prints for a failing test and nothing else.
        self.assertEqual('', fake.getvalue())

    def test_passing_test_reports_no_log(self):
        fake = self._fake_stdout()
        logger, _handler = self._logger_writing_to(fake)

        recorded = self._run_case(lambda inner: logger.info('routine'))
        self.assertEqual('success', recorded.outcome)
        self.assertNotIn('log', recorded.details)
        self.assertEqual('', fake.getvalue())

    def test_skipped_test_reports_no_log(self):
        fake = self._fake_stdout()
        logger, _handler = self._logger_writing_to(fake)

        def body(inner):
            logger.info('on the way to a skip')
            inner.skipTest('not applicable')

        recorded = self._run_case(body)
        self.assertEqual('skip', recorded.outcome)
        self.assertNotIn('log', recorded.details)

    def test_the_root_logger_keeps_the_handlers_it_had(self):
        # Importing a module which calls logging.basicConfig() mid test
        # -- shakenfist/client/ctl.py does -- hands the root logger a
        # stderr handler. That is not a deliberate act, so the base
        # class restores the root logger rather than failing the test.
        fake = self._fake_stdout()
        before = list(logging.root.handlers)

        recorded = self._run_case(
            lambda inner: logging.root.addHandler(
                logging.StreamHandler(fake)))

        self.assertEqual('success', recorded.outcome)
        self.assertEqual(before, logging.root.handlers)

    def test_a_test_instance_does_not_outlive_its_run(self):
        # testtools keeps a test's exception handlers for the life of
        # the test object, so registering a bound method there puts the
        # instance in a reference cycle: everything it allocated then
        # stays resident until the cycle collector runs, and the
        # per-test memory bound charges it to whichever test is running
        # by then (shakenfist/tests/memory.py and issue #4928).
        recorded = _Recorded()

        class _Case(base.ShakenFistTestCase):
            def runTest(inner):
                inner.held = bytearray(1024)

        case = _Case()
        case.run(recorded)
        self.assertEqual('success', recorded.outcome)

        reference = weakref.ref(case)
        del case
        del recorded
        self.assertIsNone(
            reference(),
            'a test instance survived its own run, so what it allocated '
            'is only reclaimable by the cycle collector')

    def test_duplicating_a_log_handler_fails_the_test(self):
        fake = self._fake_stdout()
        logger, _handler = self._logger_writing_to(
            fake, name='shakenfist.tests.probe_duplicate')
        added = []

        def body(inner):
            handler = logging.StreamHandler(fake)
            logger.addHandler(handler)
            added.append(handler)

        recorded = self._run_case(body)
        for handler in added:
            logger.removeHandler(handler)

        self.assertEqual('failure', recorded.outcome)
        self.assertIn('added a second log handler',
                      recorded.details['traceback'].as_text())


class SyslogCaptureTestCase(_ProbeMixin, base.ShakenFistTestCase):
    """The host's syslog socket is redirected on the same terms as stdout.

    Issue #5106. These pin the discrimination (a unix socket is the
    host's, a network address is a test's own), the substitution, the
    restore, and that a handler installed mid-test is still reported.
    """

    # Somewhere that is certainly not a live socket, so the probe handler
    # has a unix address without being able to deliver anything.
    DEAD_SOCKET = '/nonexistent/shakenfist-test-not-a-socket'

    def test_a_unix_syslog_handler_is_found(self):
        logger, handler = self._logger_writing_to_syslog(self.DEAD_SOCKET)
        self.assertTrue(handler.unixsocket)
        self.assertIn((logger, handler), logcapture.syslog_handlers())

    def test_a_network_syslog_handler_is_left_alone(self):
        # test_logship.py builds exactly this to assert that logship
        # leaves the per-module handler in place. If the capture started
        # taking it away, that test would be asserting against a handler
        # this module had removed, so the discrimination is load-bearing
        # for a test in another file.
        logger, handler = self._logger_writing_to_syslog(('localhost', 0))
        self.assertFalse(handler.unixsocket)
        self.assertNotIn((logger, handler), logcapture.syslog_handlers())

        capture = logcapture.LogCapture().start()
        self.addCleanup(capture.stop)
        self.assertIn(handler, logger.handlers)

    def test_capture_substitutes_then_restores(self):
        logger, handler = self._logger_writing_to_syslog(self.DEAD_SOCKET)

        capture = logcapture.LogCapture().start()
        # The real handler is off the logger while the test runs, so
        # nothing it emits can reach the socket: there is no "wrote to
        # syslog" to assert the absence of, which is the point.
        self.assertNotIn(handler, logger.handlers)
        logger.info('while captured')

        captured = capture.stop(keep=True)
        self.assertIn('while captured', captured)

        # Restored, and the stand-in is gone rather than left beside it.
        self.assertIn(handler, logger.handlers)
        self.assertEqual([handler], [h for h in logger.handlers
                                     if isinstance(h, (logging_handlers.SysLogHandler,
                                                       logging.StreamHandler))])

    def test_the_substitute_carries_the_formatter_and_level(self):
        logger, handler = self._logger_writing_to_syslog(self.DEAD_SOCKET)
        handler.setFormatter(logging.Formatter('prefixed: %(message)s'))
        handler.setLevel(logging.WARNING)

        capture = logcapture.LogCapture().start()
        logger.info('below the handler level')
        logger.warning('at the handler level')
        captured = capture.stop(keep=True)

        # What a failing test reports should be what the handler would
        # have written, so a handler-level filter has to come across too.
        self.assertIn('prefixed: at the handler level', captured)
        self.assertNotIn('below the handler level', captured)

    def test_a_syslog_handler_added_during_a_test_is_a_duplicate(self):
        logger, _handler = self._logger_writing_to_syslog(self.DEAD_SOCKET)

        capture = logcapture.LogCapture().start()
        self.addCleanup(capture.stop)
        late = logging_handlers.SysLogHandler(address=self.DEAD_SOCKET)
        logger.addHandler(late)
        self.addCleanup(logger.removeHandler, late)
        self.addCleanup(late.close)

        self.assertIn('%s (SysLogHandler)' % logger.name,
                      capture.duplicate_handlers())


class LogToStdoutDefaultTestCase(base.ShakenFistTestCase):
    """Importing the tests package defaults SHAKENFIST_LOG_TO_STDOUT.

    Issue #5107. Asserted in a subprocess because this process has long
    since imported the package, and because the whole point is what
    happens to an environment that did not set the variable -- which is
    not this one, running under tox.
    """

    PROBE = ('import os, shakenfist.tests; '
             'print(os.environ.get("SHAKENFIST_LOG_TO_STDOUT"))')

    def _import_with(self, value):
        env = dict(os.environ)
        if value is None:
            env.pop('SHAKENFIST_LOG_TO_STDOUT', None)
        else:
            env['SHAKENFIST_LOG_TO_STDOUT'] = value
        return subprocess.run(
            [sys.executable, '-c', self.PROBE],
            env=env, capture_output=True, text=True, timeout=120)

    def test_an_unset_variable_becomes_one(self):
        result = self._import_with(None)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('1', result.stdout.strip())

    def test_an_explicit_value_is_not_overridden(self):
        # setdefault, not assignment: someone deliberately exercising the
        # syslog branch has to be able to ask for it.
        result = self._import_with('0')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('0', result.stdout.strip())
