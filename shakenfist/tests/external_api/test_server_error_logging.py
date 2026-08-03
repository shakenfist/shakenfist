# Copyright 2019 Michael Still and contributors
import logging
import os
import shutil
import tempfile
from unittest import mock

import flask
import flask_restful

from shakenfist.external_api import base as api_base
from shakenfist.tests import base


class _CaptureHandler(logging.Handler):
    """Collect emitted log records for assertion."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class ServerErrorLoggingTestCase(base.ShakenFistTestCase):
    """The 'Server error' path must emit attributable records.

    Issue 3433: 'Server error' events reached centralised logging with no
    exception class or traceback attached, making them a dead end for log
    mining. The fields must be present as explicit structured fields on the
    record, not only via exc_info-driven formatter enrichment.
    """

    def setUp(self):
        super().setUp()

        app = flask.Flask(__name__)
        api = flask_restful.Api(app)

        class _Boom(api_base.Resource):
            # Marked public because this app is a bare Flask app with no
            # JWT configuration -- authentication would raise KeyError
            # ('JWT_HEADER_NAME') before get() ever runs, and the
            # exception under test would never be reached. What is being
            # tested is attribution on the server error path, which is
            # the same whether or not the caller was authenticated.
            @api_base.public
            def get(self):
                raise ValueError('boom')

        api.add_resource(_Boom, '/boom')
        app.testing = True
        self.client = app.test_client()

        self.capture = _CaptureHandler()
        logging.getLogger('shakenfist.external_api.base').addHandler(
            self.capture)
        self.addCleanup(
            logging.getLogger('shakenfist.external_api.base').removeHandler,
            self.capture)

    def _server_error_records(self):
        return [r for r in self.capture.records
                if r.getMessage() == 'Server error']

    def test_server_error_carries_structured_attribution(self):
        resp = self.client.get('/boom')

        self.assertEqual(500, resp.status_code)
        self.assertIn('ValueError', resp.get_data(as_text=True))

        records = self._server_error_records()
        self.assertEqual(1, len(records))
        record = records[0]

        self.assertEqual(logging.ERROR, record.levelno)

        # The explicit structured fields must be present so the shipped
        # record is attributable even if exc_info enrichment is lost.
        fields = record.extra_fields
        self.assertEqual('ValueError', fields['exception_class'])
        self.assertIn('ValueError: boom', fields['traceback'])
        self.assertEqual('GET', fields['method'])
        self.assertEqual('/boom', fields['path'])

        # exc_info is also still attached for the formatter's own
        # exception_class / stack_trace enrichment.
        self.assertIsNotNone(record.exc_info)
        self.assertIs(ValueError, record.exc_info[0])

    def test_server_error_is_the_only_shipped_line_and_carries_the_hash(self):
        """The record_exception decorator sits immediately inside
        suppress_exceptions_to_client, so before issue 3590 an endpoint
        exception produced two shipped lines with two different message
        signatures: 'Recorded new exception' at WARNING and 'Server
        error' at ERROR. Only the ERROR should now be emitted, and it
        must carry the correlation fields the WARNING used to, or the
        link to /srv/shakenfist/exceptions/<hash>.json is lost.
        """
        exceptions_capture = _CaptureHandler()
        util_log = logging.getLogger('shakenfist.util.exceptions')
        util_log.addHandler(exceptions_capture)
        self.addCleanup(util_log.removeHandler, exceptions_capture)
        original_level = util_log.level
        util_log.setLevel(logging.DEBUG)
        self.addCleanup(util_log.setLevel, original_level)

        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        real_os_open = os.open

        def redirect_open(path, flags, mode):
            return real_os_open(
                path.replace('/srv/shakenfist/exceptions', temp_dir),
                flags, mode)

        self.mock_record_exception_patcher.stop()
        try:
            with mock.patch('shakenfist.util.exceptions.os.makedirs'):
                with mock.patch('shakenfist.util.exceptions.os.open',
                                side_effect=redirect_open):
                    resp = self.client.get('/boom')
        finally:
            self.mock_record_exception_patcher.start()

        self.assertEqual(500, resp.status_code)

        # The recorder must not have emitted anything above DEBUG.
        shipped = [r for r in exceptions_capture.records
                   if r.levelno > logging.DEBUG]
        self.assertEqual(
            [], [r.getMessage() for r in shipped],
            'The recorder emitted a duplicate shipped line')

        records = self._server_error_records()
        self.assertEqual(1, len(records))
        fields = records[0].extra_fields
        self.assertEqual(1, fields['count'])
        self.assertEqual('ValueError', fields['exception_class'])
        self.assertEqual(['%s.json' % fields['exception_hash']],
                         os.listdir(temp_dir))

    def test_recorder_failure_does_not_misattribute(self):
        # If the on-disk exception recorder itself fails (for example
        # /srv/shakenfist/exceptions is unwritable), the 'Server error'
        # record and the client response must still attribute the original
        # exception, not the recorder's failure.
        self.mock_record_exception_patcher.stop()
        try:
            with mock.patch(
                    'shakenfist.util.exceptions.os.makedirs',
                    side_effect=PermissionError('denied')):
                resp = self.client.get('/boom')
        finally:
            self.mock_record_exception_patcher.start()

        self.assertEqual(500, resp.status_code)
        body = resp.get_data(as_text=True)
        self.assertIn('ValueError', body)
        self.assertNotIn('PermissionError', body)

        records = self._server_error_records()
        self.assertEqual(1, len(records))
        self.assertEqual('ValueError',
                         records[0].extra_fields['exception_class'])
