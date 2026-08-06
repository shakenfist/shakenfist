import shutil
import tempfile
from unittest import mock

import testtools


class ShakenFistTestCase(testtools.TestCase):
    def setUp(self):
        super().setUp()

        # Logging is configured in shakenfist/tests/__init__.py to write to
        # stdout, which stestr captures and only displays for failing tests.

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
