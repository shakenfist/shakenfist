import shutil
import tempfile
from unittest import mock

from pydantic import SecretStr
import testtools


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

        See docs/developer_guide/authentication.md and
        docs/plans/PLAN-auth-federation-phase-06-secret-types.md.
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
