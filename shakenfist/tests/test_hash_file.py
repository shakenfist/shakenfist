# Copyright 2026 Michael Still and contributors
"""Tests for the privexec hash file path.

A bare HashFailed with no message discarded the HashFileReply error
detail, which made a blob replica whose checksum could not be verified
fail invisibly (issue 3744). These tests confirm that the privexec
side distinguishes a missing file from a failing hasher, and that the
client side surfaces the reply detail on the exception.
"""

from unittest import mock

from shakenfist import exceptions
from shakenfist.daemons.privexec import main as privexec_main
from shakenfist.protos import privexec_pb2
from shakenfist.tests import base
from shakenfist.util import concurrency as util_concurrency


class PrivExecHashFileTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.job = privexec_main.PrivExecJob(mock.MagicMock())

    def _request(self, path):
        return privexec_pb2.HashFileRequest(
            path=path, algorithm=privexec_pb2.HashAlgorithm.SHA512)

    def test_missing_file_reports_file_not_found(self):
        # A missing file must be reported as FILE_NOT_FOUND, not as a
        # generic hasher failure -- "file not found" and "disk is dying"
        # are very different operator problems.
        reply = self.job._hash_file(
            self._request('/nonexistent/path/to/blob'))
        self.assertEqual(privexec_pb2.HashFileReply.FILE_NOT_FOUND,
                         reply.hash_file_reply.error)
        self.assertEqual('/nonexistent/path/to/blob',
                         reply.hash_file_reply.path)

    @mock.patch('shakenfist.daemons.privexec.util.locate_command',
                side_effect=lambda c: c)
    @mock.patch('shakenfist.daemons.privexec.main.os.path.exists',
                return_value=True)
    def test_hasher_failure_reports_stderr(self, mock_exists, mock_locate):
        with mock.patch(
                'shakenfist.daemons.privexec.util.command_helper',
                return_value=('', 'Input/output error', 1)):
            reply = self.job._hash_file(self._request('/some/blob'))
        self.assertEqual(privexec_pb2.HashFileReply.ALGORITHM_FAILED,
                         reply.hash_file_reply.error)
        self.assertIn('Input/output error',
                      reply.hash_file_reply.error_text)
        # The return code is the one fact that distinguishes "the hasher
        # failed" from every other failure, so it must survive into the
        # reply even when stderr is empty (issue 3983).
        self.assertIn('exited 1', reply.hash_file_reply.error_text)

    @mock.patch('shakenfist.daemons.privexec.util.locate_command',
                side_effect=lambda c: c)
    @mock.patch('shakenfist.daemons.privexec.main.os.path.exists',
                return_value=True)
    def test_hasher_failure_with_empty_stderr_still_diagnosable(
            self, mock_exists, mock_locate):
        # The issue 3983 failure mode: non-zero exit with empty stderr
        # used to produce 'ALGORITHM_FAILED: ' with nothing after the
        # colon at all.
        with mock.patch(
                'shakenfist.daemons.privexec.util.command_helper',
                return_value=('', '', 137)):
            reply = self.job._hash_file(self._request('/some/blob'))
        self.assertEqual(privexec_pb2.HashFileReply.ALGORITHM_FAILED,
                         reply.hash_file_reply.error)
        self.assertIn('exited 137', reply.hash_file_reply.error_text)
        self.assertIn('sha512sum', reply.hash_file_reply.error_text)

    @mock.patch('shakenfist.daemons.privexec.util.locate_command',
                side_effect=lambda c: c)
    @mock.patch('shakenfist.daemons.privexec.main.os.path.exists',
                return_value=True)
    def test_hasher_no_output_is_distinct_error(
            self, mock_exists, mock_locate):
        # A clean exit with no output is a different fault from a
        # failing hasher, and must not be conflated with it.
        with mock.patch(
                'shakenfist.daemons.privexec.util.command_helper',
                return_value=('', '', 0)):
            reply = self.job._hash_file(self._request('/some/blob'))
        self.assertEqual(privexec_pb2.HashFileReply.HASHER_NO_OUTPUT,
                         reply.hash_file_reply.error)
        self.assertIn('produced no output',
                      reply.hash_file_reply.error_text)


class ConcurrencyHashFileTestCase(base.ShakenFistTestCase):
    """The client side must surface the error detail from the reply."""

    def test_hash_file_raises_with_detail(self):
        reply = privexec_pb2.PrivExecReply(
            hash_file_reply=privexec_pb2.HashFileReply(
                path='/some/blob',
                algorithm=privexec_pb2.HashAlgorithm.SHA512,
                error=privexec_pb2.HashFileReply.ALGORITHM_FAILED,
                error_text='Input/output error'))
        with mock.patch(
                'shakenfist.util.concurrency._marshal_privexec_request',
                return_value=reply):
            exc = self.assertRaises(
                exceptions.HashFailed,
                util_concurrency.hash_file, '/some/blob', 'sha512')

        self.assertEqual('ALGORITHM_FAILED', exc.error)
        self.assertEqual('Input/output error', exc.error_text)
        self.assertEqual('/some/blob', exc.path)
        self.assertEqual('sha512', exc.algorithm)
        self.assertIn('ALGORITHM_FAILED', str(exc))
        self.assertIn('Input/output error', str(exc))
        self.assertIn('/some/blob', str(exc))

    def test_hash_file_raises_no_output_with_name(self):
        reply = privexec_pb2.PrivExecReply(
            hash_file_reply=privexec_pb2.HashFileReply(
                path='/some/blob',
                algorithm=privexec_pb2.HashAlgorithm.SHA512,
                error=privexec_pb2.HashFileReply.HASHER_NO_OUTPUT,
                error_text='hasher /usr/bin/sha512sum exited 0 but produced no output; stderr: '))
        with mock.patch(
                'shakenfist.util.concurrency._marshal_privexec_request',
                return_value=reply):
            exc = self.assertRaises(
                exceptions.HashFailed,
                util_concurrency.hash_file, '/some/blob', 'sha512')

        self.assertEqual('HASHER_NO_OUTPUT', exc.error)
        self.assertIn('produced no output', str(exc))

    def test_hash_file_returns_hash_on_ok(self):
        reply = privexec_pb2.PrivExecReply(
            hash_file_reply=privexec_pb2.HashFileReply(
                path='/some/blob',
                algorithm=privexec_pb2.HashAlgorithm.SHA512,
                hash='cafebeef',
                error=privexec_pb2.HashFileReply.OK))
        with mock.patch(
                'shakenfist.util.concurrency._marshal_privexec_request',
                return_value=reply):
            self.assertEqual(
                'cafebeef',
                util_concurrency.hash_file('/some/blob', 'sha512'))
