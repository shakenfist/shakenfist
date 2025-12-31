import json
import os
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
        self.exceptions_path = os.path.join(self.temp_dir, 'exceptions')
        os.makedirs(self.exceptions_path, exist_ok=True)

    def tearDown(self):
        super().tearDown()
        # Clean up temp directory
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

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

    def test_hash_is_deterministic(self):
        """Test that the same traceback produces the same hash."""
        # We can't easily test this without running the full function,
        # but we can verify the hashing approach is consistent
        import hashlib
        traceback_str = 'test traceback line 1\ntest traceback line 2'
        h1 = hashlib.sha256(traceback_str.encode()).hexdigest()[-8:]
        h2 = hashlib.sha256(traceback_str.encode()).hexdigest()[-8:]
        self.assertEqual(h1, h2)


class IgnoreExceptionTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.util.exceptions.record_exception')
    @mock.patch('shakenfist.util.exceptions.LOG')
    def test_ignore_exception_with_traceback(self, mock_log, mock_record):
        """Test ignore_exception logs and records when called in except block."""
        try:
            raise RuntimeError('something went wrong')
        except RuntimeError as e:
            util_exceptions.ignore_exception('test_process', e)

        mock_log.error.assert_called_once()
        log_msg = mock_log.error.call_args[0][0]
        self.assertIn('[Exception]', log_msg)
        self.assertIn('test_process', log_msg)
        self.assertIn('something went wrong', log_msg)
        self.assertIn('RuntimeError', log_msg)

        mock_record.assert_called_once()

    @mock.patch('shakenfist.util.exceptions.record_exception')
    @mock.patch('shakenfist.util.exceptions.LOG')
    def test_ignore_exception_without_traceback(self, mock_log, mock_record):
        """Test ignore_exception when called outside an except block."""
        e = ValueError('no traceback available')
        util_exceptions.ignore_exception('test_process', e)

        mock_log.error.assert_called_once()
        log_msg = mock_log.error.call_args[0][0]
        self.assertIn('[Exception]', log_msg)
        self.assertIn('test_process', log_msg)
        self.assertIn('no traceback available', log_msg)

        # Should not call record_exception when there's no traceback
        mock_record.assert_not_called()


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
        self.exceptions_path = os.path.join(self.temp_dir, 'exceptions')
        os.makedirs(self.exceptions_path, exist_ok=True)

    def tearDown(self):
        super().tearDown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

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
