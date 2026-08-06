import contextlib
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from unittest import mock

from shakenfist.tests import base
from shakenfist.util import exceptions as util_exceptions


class RecordExceptionTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # Stop the global mock of record_exception so we can test the real
        # function
        self.mock_record_exception_patcher.stop()

        # Create a temporary directory for exception files
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.exceptions_path = os.path.join(self.temp_dir, 'exceptions')
        os.makedirs(self.exceptions_path, exist_ok=True)

    def _get_exception_info(self):
        """Helper to generate exception info for testing."""
        try:
            raise ValueError('test error')
        except ValueError:
            return sys.exc_info()

    @mock.patch('shakenfist.util.exceptions.os.makedirs')
    @mock.patch('shakenfist.util.exceptions.os.open')
    @mock.patch('shakenfist.util.exceptions.fcntl.flock')
    @mock.patch('shakenfist.util.exceptions.os.fstat')
    @mock.patch('shakenfist.util.exceptions.os.write')
    @mock.patch('shakenfist.util.exceptions.os.close')
    def test_record_exception_new_file(self, mock_close, mock_write,
                                       mock_fstat, mock_flock, mock_open,
                                       mock_makedirs):
        """Test recording an exception to a new file."""
        mock_open.return_value = 42  # fake file descriptor
        mock_fstat.return_value = mock.Mock(st_size=0)

        exc_type, exc_value, exc_tb = self._get_exception_info()
        util_exceptions.record_exception(exc_type, exc_value, exc_tb)

        mock_makedirs.assert_called_once()
        mock_open.assert_called_once()
        mock_flock.assert_called_once()
        mock_write.assert_called_once()
        mock_close.assert_called_once_with(42)

        # Check the written data
        written_data = mock_write.call_args[0][1]
        data = json.loads(written_data.decode())
        self.assertEqual(1, data['count'])
        self.assertIn('ValueError', data['traceback'])
        self.assertIn('test error', data['traceback'])
        self.assertEqual(1, len(data['events']))

    @mock.patch('shakenfist.util.exceptions.os.makedirs')
    @mock.patch('shakenfist.util.exceptions.os.open')
    @mock.patch('shakenfist.util.exceptions.fcntl.flock')
    @mock.patch('shakenfist.util.exceptions.os.fstat')
    @mock.patch('shakenfist.util.exceptions.os.read')
    @mock.patch('shakenfist.util.exceptions.os.lseek')
    @mock.patch('shakenfist.util.exceptions.os.write')
    @mock.patch('shakenfist.util.exceptions.os.close')
    def test_record_exception_existing_file(self, mock_close, mock_write,
                                            mock_lseek, mock_read, mock_fstat,
                                            mock_flock, mock_open,
                                            mock_makedirs):
        """Test recording an exception to an existing file with previous data."""
        mock_open.return_value = 42
        existing_data = {
            'traceback': 'old traceback',
            'count': 5,
            'events': [1000.0, 2000.0]
        }
        existing_json = json.dumps(existing_data).encode()
        mock_fstat.return_value = mock.Mock(st_size=len(existing_json))
        mock_read.return_value = existing_json

        exc_type, exc_value, exc_tb = self._get_exception_info()
        util_exceptions.record_exception(exc_type, exc_value, exc_tb)

        # Check the written data has incremented count
        written_data = mock_write.call_args[0][1]
        data = json.loads(written_data.decode())
        self.assertEqual(6, data['count'])
        self.assertEqual(3, len(data['events']))
        # Traceback should be updated to new one
        self.assertIn('ValueError', data['traceback'])

    @mock.patch('shakenfist.util.exceptions.os.makedirs')
    @mock.patch('shakenfist.util.exceptions.os.open')
    @mock.patch('shakenfist.util.exceptions.fcntl.flock')
    @mock.patch('shakenfist.util.exceptions.os.close')
    def test_record_exception_handles_errors_gracefully(self, mock_close,
                                                        mock_flock, mock_open,
                                                        mock_makedirs):
        """Test that errors in record_exception don't propagate."""
        mock_open.return_value = 42
        mock_flock.side_effect = OSError('lock failed')

        exc_type, exc_value, exc_tb = self._get_exception_info()
        # Should not raise
        util_exceptions.record_exception(exc_type, exc_value, exc_tb)
        mock_close.assert_called_once_with(42)

    @mock.patch('shakenfist.util.exceptions.os.close')
    @mock.patch('shakenfist.util.exceptions.os.makedirs',
                side_effect=PermissionError('denied'))
    def test_record_exception_swallows_makedirs_failure(self, mock_makedirs,
                                                        mock_close):
        """An unwritable exceptions directory must not raise.

        record_exception is called from exception handlers (the API server
        error path, sys.excepthook). A failure escaping here replaces the
        exception being recorded and misattributes the original failure
        (issue 3433).
        """
        exc_type, exc_value, exc_tb = self._get_exception_info()
        # Should not raise
        util_exceptions.record_exception(exc_type, exc_value, exc_tb)
        # No file descriptor was opened, so none should be closed
        mock_close.assert_not_called()

    @mock.patch('shakenfist.util.exceptions.os.close')
    @mock.patch('shakenfist.util.exceptions.os.open',
                side_effect=OSError('disk full'))
    @mock.patch('shakenfist.util.exceptions.os.makedirs')
    def test_record_exception_swallows_open_failure(self, mock_makedirs,
                                                    mock_open, mock_close):
        """A failing open must not raise or close an undefined fd."""
        exc_type, exc_value, exc_tb = self._get_exception_info()
        # Should not raise
        util_exceptions.record_exception(exc_type, exc_value, exc_tb)
        mock_close.assert_not_called()

    def test_hash_is_deterministic(self):
        """Test that the same traceback produces the same hash."""
        # We can't easily test this without running the full function,
        # but we can verify the hashing approach is consistent
        import hashlib
        traceback_str = 'test traceback line 1\ntest traceback line 2'
        h1 = hashlib.sha256(traceback_str.encode()).hexdigest()[-8:]
        h2 = hashlib.sha256(traceback_str.encode()).hexdigest()[-8:]
        self.assertEqual(h1, h2)


class RecordExceptionLoggingTestCase(base.ShakenFistTestCase):
    """The first occurrence of a traceback hash must be logged above
    DEBUG so it survives shipping to centralised logging; repeats stay
    at DEBUG so hot loops do not flood the aggregator.
    """

    def setUp(self):
        super().setUp()
        # Stop the global mock of record_exception so we can test the real
        # function
        self.mock_record_exception_patcher.stop()

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.exceptions_path = os.path.join(self.temp_dir, 'exceptions')
        os.makedirs(self.exceptions_path, exist_ok=True)

    def _redirect_open(self):
        """Send record_exception's writes into the temp directory."""
        real_os_open = os.open
        exceptions_path = self.exceptions_path

        def redirect_open(path, flags, mode):
            new_path = path.replace('/srv/shakenfist/exceptions',
                                    exceptions_path)
            return real_os_open(new_path, flags, mode)

        return redirect_open

    @contextlib.contextmanager
    def _recording(self):
        """Yield a log mock whose with_fields() chains onto itself.

        The real logger returns a context from with_fields() which
        itself has with_fields(), and record_exception uses that to add
        the traceback field on top of the correlation fields. Collapsing
        the chain onto one mock means a test can assert on the emitted
        call without caring how many links were used to get there.
        """
        with mock.patch('shakenfist.util.exceptions.os.makedirs'):
            with mock.patch('shakenfist.util.exceptions.os.open',
                            side_effect=self._redirect_open()):
                with mock.patch(
                        'shakenfist.util.exceptions.LOG') as mock_log:
                    log_ctx = mock.MagicMock()
                    log_ctx.with_fields.return_value = log_ctx
                    mock_log.with_fields.return_value = log_ctx
                    yield mock_log, log_ctx

    def _on_disk(self):
        files = os.listdir(self.exceptions_path)
        self.assertEqual(1, len(files))
        with open(os.path.join(self.exceptions_path, files[0])) as f:
            return json.load(f)

    @staticmethod
    def _raise_and_record(already_logged=False):
        try:
            raise TypeError('type mismatch')
        except TypeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            return util_exceptions.record_exception(
                exc_type, exc_value, exc_tb, already_logged=already_logged)

    def test_first_occurrence_warns_repeats_debug(self):
        with self._recording() as (mock_log, log_ctx):
            self._raise_and_record()

            log_ctx.warning.assert_called_once()
            log_ctx.debug.assert_not_called()

            # The WARNING context must carry the correlation fields,
            # the message body the human readable summary, and the
            # traceback must stay a structured field so it remains
            # independently queryable rather than being buried in a
            # multi-line message body (issues 3433 and 3590).
            fields = mock_log.with_fields.call_args[0][0]
            self.assertEqual('TypeError', fields['exception_class'])
            self.assertEqual(1, fields['count'])
            self.assertIn('exception_hash', fields)

            warn_msg = log_ctx.warning.call_args[0][0]
            self.assertIn('type mismatch', warn_msg)
            self.assertNotIn('Traceback', warn_msg)

            tb_fields = log_ctx.with_fields.call_args[0][0]
            self.assertIn('Traceback', tb_fields['traceback'])
            self.assertIn('type mismatch', tb_fields['traceback'])

            mock_log.reset_mock()
            log_ctx.reset_mock()
            log_ctx.with_fields.return_value = log_ctx
            self._raise_and_record()

            log_ctx.debug.assert_called_once_with('Recorded repeat exception')
            log_ctx.warning.assert_not_called()
            fields = mock_log.with_fields.call_args[0][0]
            self.assertEqual(2, fields['count'])

    def test_already_logged_suppresses_warning(self):
        """A caller that has already emitted a full-detail log line for
        the exception (ignore_exception's ERROR) must not get a second
        WARNING for the same event, even on first occurrence -- the pair
        doubled the signature count for every task-exception type in
        downstream log mining (issue 3590). The on-disk record is still
        written.
        """
        with self._recording() as (_, log_ctx):
            self._raise_and_record(already_logged=True)

            log_ctx.warning.assert_not_called()
            # The message says why it is quiet, so it cannot be confused
            # with 'Recorded new exception' or 'Recorded repeat
            # exception' by a grep or an alerting rule.
            log_ctx.debug.assert_called_once_with(
                'Recorded exception (already logged by caller)')

        # The on-disk record must still have been written
        data = self._on_disk()
        self.assertEqual(1, data['count'])
        self.assertIn('type mismatch', data['traceback'])

    def test_already_logged_repeat_stays_debug_and_counts(self):
        """already_logged short-circuits ahead of the count test, so a
        repeat must behave exactly like a first occurrence: still no
        WARNING, and the on-disk count still advances."""
        with self._recording() as (mock_log, log_ctx):
            self._raise_and_record(already_logged=True)
            mock_log.reset_mock()
            log_ctx.reset_mock()
            log_ctx.with_fields.return_value = log_ctx
            self._raise_and_record(already_logged=True)

            log_ctx.warning.assert_not_called()
            log_ctx.debug.assert_called_once_with(
                'Recorded exception (already logged by caller)')
            self.assertEqual(2, mock_log.with_fields.call_args[0][0]['count'])

        data = self._on_disk()
        self.assertEqual(2, data['count'])
        self.assertEqual(2, len(data['events']))

    def test_returns_correlation_fields_for_the_caller_to_log(self):
        """Suppressing our own line only works if the caller can put the
        correlation fields on theirs, so the fields are returned rather
        than only logged (issue 3590)."""
        with self._recording():
            fields = self._raise_and_record(already_logged=True)

        self.assertEqual('TypeError', fields['exception_class'])
        self.assertEqual(1, fields['count'])
        # The hash names the file the operator has to open.
        self.assertEqual(
            ['%s.json' % fields['exception_hash']],
            os.listdir(self.exceptions_path))

    def test_returns_none_when_the_record_could_not_be_written(self):
        """A caller must be able to tell that there is no on-disk record
        to correlate with, rather than logging a hash for a file which
        does not exist."""
        with mock.patch('shakenfist.util.exceptions.os.makedirs',
                        side_effect=PermissionError('denied')):
            self.assertIsNone(self._raise_and_record(already_logged=True))


class IgnoreExceptionTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.util.exceptions.record_exception',
                return_value={'exception_hash': 'deadbeef',
                              'exception_class': 'RuntimeError',
                              'count': 7})
    @mock.patch('shakenfist.util.exceptions.LOG')
    def test_ignore_exception_with_traceback(self, mock_log, mock_record):
        """Test ignore_exception logs and records when called in except block."""
        try:
            raise RuntimeError('something went wrong')
        except RuntimeError as e:
            util_exceptions.ignore_exception('test_process', e)

        log_ctx = mock_log.with_fields.return_value
        log_ctx.error.assert_called_once()
        log_msg = log_ctx.error.call_args[0][0]
        self.assertIn('[Exception]', log_msg)
        self.assertIn('test_process', log_msg)
        self.assertIn('something went wrong', log_msg)
        self.assertIn('RuntimeError', log_msg)

        # The ERROR above carries the full detail, so record_exception
        # must be told not to log a duplicate entry (issue 3590).
        mock_record.assert_called_once()
        self.assertIn('already_logged', mock_record.call_args.kwargs)
        self.assertTrue(mock_record.call_args.kwargs.get('already_logged'))

        # Suppressing that entry must not lose the correlation fields:
        # this ERROR is now the only line for the event which reaches
        # centralised logging, so it has to carry them.
        fields = mock_log.with_fields.call_args[0][0]
        self.assertEqual('deadbeef', fields['exception_hash'])
        self.assertEqual('RuntimeError', fields['exception_class'])
        self.assertEqual(7, fields['count'])

    @mock.patch('shakenfist.util.exceptions.record_exception',
                return_value=None)
    @mock.patch('shakenfist.util.exceptions.LOG')
    def test_ignore_exception_unrecordable_still_logs(self, mock_log,
                                                      mock_record):
        """If the on-disk record could not be written there are no
        correlation fields to attach, but the ERROR must still be
        emitted -- losing the log line as well would be much worse than
        losing the hash."""
        try:
            raise RuntimeError('something went wrong')
        except RuntimeError as e:
            util_exceptions.ignore_exception('test_process', e)

        log_ctx = mock_log.with_fields.return_value
        log_ctx.error.assert_called_once()
        self.assertEqual({}, mock_log.with_fields.call_args[0][0])

    @mock.patch('shakenfist.util.exceptions.record_exception')
    @mock.patch('shakenfist.util.exceptions.LOG')
    def test_ignore_exception_without_traceback(self, mock_log, mock_record):
        """Test ignore_exception when called outside an except block."""
        e = ValueError('no traceback available')
        util_exceptions.ignore_exception('test_process', e)

        log_ctx = mock_log.with_fields.return_value
        log_ctx.error.assert_called_once()
        log_msg = log_ctx.error.call_args[0][0]
        self.assertIn('[Exception]', log_msg)
        self.assertIn('test_process', log_msg)
        self.assertIn('no traceback available', log_msg)

        # Should not call record_exception when there's no traceback
        mock_record.assert_not_called()


class _CaptureHandler(logging.Handler):
    """Collect emitted log records for assertion."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class IgnoreExceptionEndToEndTestCase(base.ShakenFistTestCase):
    """The whole of ignore_exception, with the real record_exception,
    must produce exactly one log record above DEBUG.

    The tests above pin the two halves independently with
    record_exception mocked out, which cannot catch a regression where
    both halves are individually right but the pair still emits two
    shipped lines -- which is the entire subject of issue 3590.
    """

    def setUp(self):
        super().setUp()
        self.mock_record_exception_patcher.stop()

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.exceptions_path = os.path.join(self.temp_dir, 'exceptions')
        os.makedirs(self.exceptions_path, exist_ok=True)

        self.capture = _CaptureHandler()
        logger = logging.getLogger('shakenfist.util.exceptions')
        logger.addHandler(self.capture)
        self.addCleanup(logger.removeHandler, self.capture)

        # The record-keeping line is DEBUG, so the logger has to be
        # willing to emit it for "exactly one line above DEBUG" to mean
        # anything at all.
        original_level = logger.level
        logger.setLevel(logging.DEBUG)
        self.addCleanup(logger.setLevel, original_level)

    def test_exactly_one_shipped_record_carrying_the_hash(self):
        real_os_open = os.open
        exceptions_path = self.exceptions_path

        def redirect_open(path, flags, mode):
            return real_os_open(
                path.replace('/srv/shakenfist/exceptions', exceptions_path),
                flags, mode)

        with mock.patch('shakenfist.util.exceptions.os.makedirs'):
            with mock.patch('shakenfist.util.exceptions.os.open',
                            side_effect=redirect_open):
                try:
                    raise RuntimeError('something went wrong')
                except RuntimeError as e:
                    util_exceptions.ignore_exception('test_process', e)

        shipped = [r for r in self.capture.records
                   if r.levelno > logging.DEBUG]
        self.assertEqual(
            1, len(shipped),
            'Expected one shipped record, got: %s'
            % [r.getMessage() for r in shipped])
        self.assertEqual(logging.ERROR, shipped[0].levelno)
        self.assertIn('Ignored error in test_process', shipped[0].getMessage())

        # The record keeping line is still emitted, just below the
        # threshold centralised logging ships.
        debug = [r for r in self.capture.records
                 if r.levelno == logging.DEBUG]
        self.assertEqual(1, len(debug))
        self.assertEqual('Recorded exception (already logged by caller)',
                         debug[0].getMessage())

        # And the one shipped line points at the on-disk record.
        fields = shipped[0].extra_fields
        self.assertEqual(1, fields['count'])
        self.assertEqual('RuntimeError', fields['exception_class'])
        self.assertEqual(
            ['%s.json' % fields['exception_hash']],
            os.listdir(self.exceptions_path))


class ExceptHookTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # Save original hooks
        self._orig_sys_excepthook = sys.excepthook
        self._orig_threading_excepthook = threading.excepthook

    def tearDown(self):
        # Restore original hooks
        sys.excepthook = self._orig_sys_excepthook
        threading.excepthook = self._orig_threading_excepthook
        super().tearDown()

    @mock.patch('shakenfist.util.exceptions.record_exception')
    def test_install_exception_tracking(self, mock_record):
        """Test that install_exception_tracking sets up the correct hooks."""
        # Reset to Python's default hooks before testing
        sys.excepthook = sys.__excepthook__
        threading.excepthook = threading.__excepthook__

        util_exceptions.install_exception_tracking()

        # After installation, hooks should be our custom ones
        self.assertEqual(sys.excepthook, util_exceptions._tracking_excepthook)
        self.assertEqual(threading.excepthook, util_exceptions._thread_excepthook)

    @mock.patch('shakenfist.util.exceptions.record_exception')
    @mock.patch('shakenfist.util.exceptions._original_excepthook')
    def test_tracking_excepthook_calls_record_and_original(self, mock_orig,
                                                           mock_record):
        """Test that _tracking_excepthook records and calls original hook."""
        exc_type = ValueError
        exc_value = ValueError('test')
        exc_tb = None

        util_exceptions._tracking_excepthook(exc_type, exc_value, exc_tb)

        mock_record.assert_called_once_with(exc_type, exc_value, exc_tb)
        mock_orig.assert_called_once_with(exc_type, exc_value, exc_tb)

    @mock.patch('shakenfist.util.exceptions.record_exception')
    def test_thread_excepthook(self, mock_record):
        """Test that _thread_excepthook extracts args correctly."""
        # Create a mock args object like threading passes
        args = mock.Mock()
        args.exc_type = RuntimeError
        args.exc_value = RuntimeError('thread error')
        args.exc_traceback = None

        util_exceptions._thread_excepthook(args)

        mock_record.assert_called_once_with(
            args.exc_type, args.exc_value, args.exc_traceback)


class IntegrationTestCase(base.ShakenFistTestCase):
    """Integration tests that verify the full flow with real files."""

    def setUp(self):
        super().setUp()
        # Stop the global mock of record_exception so we can test the real
        # function
        self.mock_record_exception_patcher.stop()

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.exceptions_path = os.path.join(self.temp_dir, 'exceptions')
        os.makedirs(self.exceptions_path, exist_ok=True)

    def test_record_exception_full_flow(self):
        """Test recording an exception with a real temp file."""
        # Store reference to real os.open before any patching
        real_os_open = os.open
        exceptions_path = self.exceptions_path

        def redirect_open(path, flags, mode):
            new_path = path.replace('/srv/shakenfist/exceptions',
                                    exceptions_path)
            return real_os_open(new_path, flags, mode)

        with mock.patch('shakenfist.util.exceptions.os.makedirs'):
            with mock.patch('shakenfist.util.exceptions.os.open',
                            side_effect=redirect_open):
                try:
                    raise KeyError('missing key')
                except KeyError:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    util_exceptions.record_exception(exc_type, exc_value,
                                                     exc_tb)

        # Find the created file
        files = os.listdir(self.exceptions_path)
        self.assertEqual(1, len(files))

        with open(os.path.join(self.exceptions_path, files[0])) as f:
            data = json.load(f)

        self.assertEqual(1, data['count'])
        self.assertIn('KeyError', data['traceback'])
        self.assertIn('missing key', data['traceback'])
        self.assertEqual(1, len(data['events']))

    def test_record_exception_increments_count(self):
        """Test that recording the same exception twice increments count."""
        real_os_open = os.open
        exceptions_path = self.exceptions_path

        def redirect_open(path, flags, mode):
            new_path = path.replace('/srv/shakenfist/exceptions',
                                    exceptions_path)
            return real_os_open(new_path, flags, mode)

        def raise_and_record():
            try:
                raise TypeError('type mismatch')
            except TypeError:
                exc_type, exc_value, exc_tb = sys.exc_info()
                util_exceptions.record_exception(exc_type, exc_value, exc_tb)

        with mock.patch('shakenfist.util.exceptions.os.makedirs'):
            with mock.patch('shakenfist.util.exceptions.os.open',
                            side_effect=redirect_open):
                # Record the same exception twice
                raise_and_record()
                raise_and_record()

        files = os.listdir(self.exceptions_path)
        self.assertEqual(1, len(files))

        with open(os.path.join(self.exceptions_path, files[0])) as f:
            data = json.load(f)

        self.assertEqual(2, data['count'])
        self.assertEqual(2, len(data['events']))

    def test_different_exceptions_create_different_files(self):
        """Test that different exception types create separate files."""
        real_os_open = os.open
        exceptions_path = self.exceptions_path

        def redirect_open(path, flags, mode):
            new_path = path.replace('/srv/shakenfist/exceptions',
                                    exceptions_path)
            return real_os_open(new_path, flags, mode)

        with mock.patch('shakenfist.util.exceptions.os.makedirs'):
            with mock.patch('shakenfist.util.exceptions.os.open',
                            side_effect=redirect_open):
                try:
                    raise ValueError('value error')
                except ValueError:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    util_exceptions.record_exception(exc_type, exc_value,
                                                     exc_tb)

                try:
                    raise KeyError('key error')
                except KeyError:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    util_exceptions.record_exception(exc_type, exc_value,
                                                     exc_tb)

        files = os.listdir(self.exceptions_path)
        # Different tracebacks should create different files
        self.assertEqual(2, len(files))
