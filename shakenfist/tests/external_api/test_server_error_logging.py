# Copyright 2019 Michael Still and contributors
import logging
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
