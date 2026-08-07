# Copyright 2019 Michael Still and contributors
import logging

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


class RequestLoggingTestCase(base.ShakenFistTestCase):
    """log_request must redact the Authorization header, not the headers.

    Issue 3615: the redaction replaced the entire headers dict with the
    string 'Bearer *****', so every authenticated request lost
    User-Agent, X-Request-Id, Content-Type and everything else from the
    logged headers field. Only the Authorization value should be
    redacted; the rest of the headers must survive.
    """

    def setUp(self):
        super().setUp()

        app = flask.Flask(__name__)
        api = flask_restful.Api(app)

        class _Thing(api_base.Resource):
            # Marked public because this app is a bare Flask app with no
            # JWT configuration -- authentication would fail before
            # log_request's output could be observed. log_request runs
            # for public and authenticated routes alike, so the headers
            # handling under test is the same either way.
            @api_base.public
            def get(self):
                return {}

        api.add_resource(_Thing, '/thing')
        app.testing = True
        self.client = app.test_client()

        self.capture = _CaptureHandler()
        logger = logging.getLogger('shakenfist.external_api.base')
        logger.addHandler(self.capture)
        self.addCleanup(logger.removeHandler, self.capture)

        # 'API request parsed' is emitted at INFO, below the default
        # effective level, so it never reaches the handler unless the
        # logger is opened up.
        original_level = logger.level
        logger.setLevel(logging.INFO)
        self.addCleanup(logger.setLevel, original_level)

    def _request_parsed_records(self):
        return [r for r in self.capture.records
                if r.getMessage() == 'API request parsed']

    def test_authorization_header_is_redacted_others_survive(self):
        resp = self.client.get('/thing', headers={
            'Authorization': 'Bearer secret-jwt-value',
            'User-Agent': 'sf-test-agent/1.0',
            'X-Request-Id': 'req-1234'
        })
        self.assertEqual(200, resp.status_code)

        records = self._request_parsed_records()
        self.assertEqual(1, len(records))
        headers = records[0].extra_fields['headers']

        # The headers field must still be a dict, with only the
        # Authorization value replaced.
        self.assertIsInstance(headers, dict)
        self.assertEqual('Bearer *****', headers['Authorization'])
        self.assertEqual('sf-test-agent/1.0', headers['User-Agent'])
        self.assertEqual('req-1234', headers['X-Request-Id'])

        # The credential itself must not appear anywhere in the record.
        self.assertNotIn('secret-jwt-value', str(records[0].extra_fields))

    def test_unauthenticated_request_logs_headers_unchanged(self):
        resp = self.client.get('/thing', headers={
            'User-Agent': 'sf-test-agent/1.0'
        })
        self.assertEqual(200, resp.status_code)

        records = self._request_parsed_records()
        self.assertEqual(1, len(records))
        headers = records[0].extra_fields['headers']

        self.assertIsInstance(headers, dict)
        self.assertNotIn('Authorization', headers)
        self.assertEqual('sf-test-agent/1.0', headers['User-Agent'])
