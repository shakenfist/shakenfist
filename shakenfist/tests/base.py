import logging
import shutil
import tempfile
from unittest import mock

from pydantic import SecretStr
import testtools
from testtools import content

from shakenfist.config import config
from shakenfist.tests import logcapture
from shakenfist.tests import memory


class ShakenFistTestCase(testtools.TestCase):
    def _reject_secret_operand(self, needle, haystack, method):
        """Refuse a SecretStr on either side of a containment assertion.

        ``assertNotIn(attrs.key, some_string)`` passes unconditionally,
        however much of the secret the haystack contains. The mechanism
        is worth knowing, because it is not the one people assume:
        SecretStr implements no __contains__, __iter__ or __getitem__,
        so ``secret in string`` raises TypeError -- and testtools'
        Contains matcher catches TypeError and reports "does not
        contain" (see testtools.matchers.Contains.match, which
        documents the case as "e.g. 1 in 2"). The assertion is
        therefore not merely likely to pass, it cannot fail. The same
        is true with the SecretStr as the haystack, so both operands
        are checked here.

        Assertions of this shape are almost always leak guards, which
        makes a vacuous one a test reporting that no secret escaped
        while checking nothing at all.

        This is not hypothetical. Wrapping the namespace key fields in
        SecretStr silently emptied six such guards across three files,
        and not one of them failed as a result -- every one was found
        by deliberately going looking. Rather than fix each and hope,
        the shape is rejected here, so the next one fails the first
        time it runs rather than whenever somebody next thinks to
        check.

        Compare ``.get_secret_value()`` instead. Do not reach for
        ``str(secret)``: that asserts the literal '**********' is
        absent, which is true of a haystack containing the real secret.

        See docs/developer_guide/authentication.md.
        """
        for operand, side in ((needle, 'needle'), (haystack, 'haystack')):
            if isinstance(operand, SecretStr):
                raise TypeError(
                    f'{method}() was given a SecretStr as its {side}. '
                    'Containment against a SecretStr raises TypeError, '
                    'which testtools reports as "does not contain", so '
                    'the assertion can never fail. Compare '
                    'get_secret_value() instead -- see '
                    'ShakenFistTestCase._reject_secret_operand().')

    def assertIn(self, needle, haystack, message=''):
        self._reject_secret_operand(needle, haystack, 'assertIn')
        return super().assertIn(needle, haystack, message)

    def assertNotIn(self, needle, haystack, message=''):
        self._reject_secret_operand(needle, haystack, 'assertNotIn')
        return super().assertNotIn(needle, haystack, message)

    def setUp(self):
        super().setUp()

        # Fail the test which grows the worker rather than leaving the
        # growth to be carried by whichever test the worker runs next
        # (issue #4928). Registered before any other cleanup so that
        # LIFO ordering samples last, after everything this test holds
        # has been released. See shakenfist/tests/memory.py.
        self.addCleanup(self._assert_memory_within_bounds,
                        memory.resident_bytes())

        # Logging is configured in shakenfist/tests/__init__.py to write
        # to stdout. Nothing captured it, so every record went into the
        # worker's subunit stream and out to the console, and a test
        # which logged heavily took as long as that console needed to
        # drain it -- 82 MB in one run, and a 1.2 second test taking 165
        # seconds (issue #4991). Point those handlers at memory for the
        # duration of this test, and hand the buffer back as a detail
        # only if the test does not pass. See
        # shakenfist/tests/logcapture.py.
        self._log_capture = logcapture.LogCapture().start()
        # A closure over a list, not a bound method: testtools never
        # clears a test's exception handlers, so a bound method there
        # would leave the test instance in a reference cycle and
        # everything it allocated resident until the cycle collector
        # next ran -- which the memory bound above would then charge to
        # some later test. See logcapture.note_failure().
        wanted = []
        self.addOnException(
            lambda exc_info: logcapture.note_failure(wanted, exc_info))
        self.addCleanup(self._finish_log_capture, wanted)

        # The root logger's handlers are restored for every test, rather
        # than by the tests which disturb them, because the usual way to
        # disturb them is not a deliberate act: importing a module which
        # calls logging.basicConfig() -- shakenfist/client/ctl.py does
        # -- hands the root logger a stderr handler, and every record
        # which propagates there is then written to the worker's stderr
        # once per such import for the rest of the run. Registered
        # after the capture so it is undone before the duplicate-handler
        # check in _finish_log_capture().
        self.preserve_log_handlers('')

        self.mock_add_event_multi = mock.patch(
            'shakenfist.eventlog.add_event_multi')
        self.mock_add_event_multi.start()
        self.addCleanup(self.mock_add_event_multi.stop)

        # Mock exception recording to avoid filesystem access during
        # tests. record_exception returns the correlation fields it
        # wrote, or None when nothing was recorded; None is the honest
        # answer for a mock which records nothing, and it keeps callers
        # which merge the result into their own log fields from being
        # handed a MagicMock.
        self.mock_record_exception_patcher = mock.patch(
            'shakenfist.util.exceptions.record_exception',
            return_value=None)
        self.mock_record_exception = self.mock_record_exception_patcher.start()
        self.addCleanup(self.mock_record_exception_patcher.stop)

    def _finish_log_capture(self, wanted):
        """Report what the capture caught, if this test did not pass.

        `wanted` is the list logcapture.note_failure() appends to. The
        detail is attached here rather than from the exception handler
        so that logging from the cleanups which ran after the exception
        is in it too.
        """
        duplicates = self._log_capture.duplicate_handlers()
        captured = self._log_capture.stop(keep=bool(wanted))
        if captured:
            self.addDetailUniqueName('log', content.text_content(captured))

        # This test gave a logger which was already handled a second
        # handler on the real stdout or stderr. Its records go into the
        # worker's output whatever this capture does, and one added in a
        # setUp() without a matching cleanup is inherited by every test
        # the worker runs next, each of which adds another. Attach a
        # cleanup which removes it (self.preserve_log_handlers(), or
        # logcapture.forget_logger() for a logger something else
        # configured), or capture with a handler of your own -- see
        # shakenfist/tests/external_api/test_request_logging.py.
        self.assertEqual(
            [], duplicates,
            'This test added a second log handler writing to the process\'s '
            'real stdout or stderr: %s. See '
            'ShakenFistTestCase._finish_log_capture() and '
            'shakenfist/tests/logcapture.py.' % ', '.join(duplicates))

    def _assert_memory_within_bounds(self, before):
        problem = memory.problem_since(before)
        if problem:
            self.fail(problem)

    def preserve_log_handlers(self, *names):
        """Restore these loggers' handler lists after this test.

        For a test which runs something that configures logging as a
        side effect: importing ``shakenfist/client/ctl.py`` calls
        ``logging.basicConfig()``, and constructing a gunicorn logger
        attaches stream handlers. Those handlers are process-global, so
        without this the test leaves one writing to the real stdout or
        stderr, and the next test in the worker inherits it and adds
        another -- which ``_finish_log_capture()`` fails that one for.

        A name is whatever ``logging.getLogger()`` takes, so '' is the
        root logger. 'root' is not: that is a logger of its own.
        """
        for name in names:
            logger = logging.getLogger(name)
            self.addCleanup(setattr, logger, 'handlers', list(logger.handlers))

    def set_validation_mode(self, mode):
        """Run the rest of this test with API_VALIDATION_MODE at `mode`.

        The mode defaults to 'enforce', which means several handlers' own
        input guards are no longer reachable in the default
        configuration -- request validation answers ahead of them. They
        are still what answers when an operator rolls back to 'warn',
        so the tests which assert their messages say which mode they
        are about rather than depending on a default. A rollback path
        asserted only in prose is a rollback path that rots.

        Restored on cleanup: config is a process-wide singleton, so a
        test which leaves it changed changes every test that runs
        after it in the same worker.
        """
        saved = config.API_VALIDATION_MODE
        self.addCleanup(setattr, config, 'API_VALIDATION_MODE', saved)
        config.API_VALIDATION_MODE = mode


class SpoolRootMixin:
    """Redirect a spool module's ``SPOOL_ROOT`` to a per-test tempdir.

    Subclasses set ``spool_module`` (a module exposing ``SPOOL_ROOT``
    and ``reset_for_tests()``) and ``spool_prefix`` (the tempdir name
    prefix). Modules with additional singletons to reset between tests
    (for example ``shakenfist.logship``) list the reset callables in
    ``extra_resets``.
    """

    spool_module = None
    spool_prefix = None
    extra_resets = ()

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix=self.spool_prefix)
        # Registered first so LIFO cleanup ordering runs it last, after
        # reset_for_tests() has closed the spool's sqlite connection.
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._original_root = self.spool_module.SPOOL_ROOT
        self.spool_module.SPOOL_ROOT = self.tmp
        self.spool_module.reset_for_tests()
        self.addCleanup(self.spool_module.reset_for_tests)
        for reset in self.extra_resets:
            reset()
            self.addCleanup(reset)
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        self.spool_module.SPOOL_ROOT = self._original_root
