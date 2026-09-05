# Copyright 2019 Michael Still and contributors
import json
import logging

import flask
from flask_jwt_extended import JWTManager
from flask_jwt_extended.exceptions import NoAuthorizationError
from jwt.exceptions import DecodeError
from jwt.exceptions import ExpiredSignatureError

from shakenfist.external_api import base as api_base
from shakenfist.tests import base


class _CaptureHandler(logging.Handler):
    """Collect emitted log records for assertion."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class AuthorizationFailureLoggingTestCase(base.ShakenFistTestCase):
    """Every auth-failure response carries structured request attribution.

    Issue 4069: handle_authorization_exceptions turned every
    authentication failure into a response without logging a single
    structured field, so the record saying *why* a request was rejected
    had no key in common with the records saying *which* request it
    was. The join had to be reconstructed by hand from pid and
    sub-second timestamps, which only works while gunicorn runs sync
    workers. Each rejection must log the request-id (joining it to the
    'API request parsed' and audit records), the method, path and peer,
    and the exception class so the branches are distinguishable in a
    query rather than only by their message text.
    """

    REQUEST_ID = '450ba182-594e-4db9-87c2-904bdab0a4dc'

    def setUp(self):
        super().setUp()

        self.app = flask.Flask(__name__)
        self.app.config['JWT_SECRET_KEY'] = 'test-key'
        JWTManager(self.app)

        self.capture = _CaptureHandler()
        logging.getLogger('shakenfist.external_api.base').addHandler(
            self.capture)
        self.addCleanup(
            logging.getLogger('shakenfist.external_api.base').removeHandler,
            self.capture)

    def _raise_through_handler(self, exc, accept):
        @api_base.handle_authorization_exceptions
        def _boom():
            raise exc

        with self.app.test_request_context(
                '/auth/namespaces', method='GET',
                headers={'Accept': accept},
                environ_base={
                    'FLASK_REQUEST_ID': self.REQUEST_ID,
                    'REMOTE_ADDR': '192.168.21.7',
                }):
            return _boom()

    def _sole_record(self, message):
        records = [r for r in self.capture.records
                   if r.getMessage() == message]
        self.assertEqual(
            1, len(records),
            'expected exactly one %r record, saw %s'
            % (message, [r.getMessage() for r in self.capture.records]))
        return records[0]

    def _assert_attribution(self, record, error_class):
        # The request-id is the one field which makes the existing
        # audit trail joinable, so it is the load-bearing assertion.
        fields = record.extra_fields
        self.assertEqual(self.REQUEST_ID, fields['request-id'])
        self.assertEqual('GET', fields['method'])
        self.assertEqual('/auth/namespaces', fields['path'])
        self.assertEqual('192.168.21.7', fields['remote-address'])
        self.assertEqual(error_class, fields['error-class'])
        self.assertIn('error', fields)

        # A rejection is caused by the credential the client presented,
        # not by a cluster fault, so it must not page anyone (the issue
        # 3606 rationale on _reject_token).
        self.assertEqual(logging.INFO, record.levelno)

    def test_undecodable_jwt_401_is_attributable(self):
        resp = self._raise_through_handler(
            DecodeError('Not enough segments'), 'application/json')
        self.assertEqual(401, resp.status_code)

        record = self._sole_record('API request rejected, undecodable JWT')
        self._assert_attribution(record, 'DecodeError')
        self.assertEqual('Not enough segments',
                         record.extra_fields['error'])

    def test_undecodable_jwt_browser_redirect_is_logged(self):
        # A browser being bounced to / with its cookies cleared was
        # previously invisible in the logs.
        resp = self._raise_through_handler(
            DecodeError('Not enough segments'), 'text/html')
        self.assertEqual(302, resp.status_code)

        record = self._sole_record(
            'Undecodable JWT, redirecting browser to root')
        self._assert_attribution(record, 'DecodeError')

    def test_expired_jwt_401_is_attributable(self):
        resp = self._raise_through_handler(
            ExpiredSignatureError('Signature has expired'),
            'application/json')
        self.assertEqual(401, resp.status_code)

        record = self._sole_record('API request rejected, expired JWT')
        self._assert_attribution(record, 'ExpiredSignatureError')

    def test_expired_jwt_browser_redirect_is_logged(self):
        resp = self._raise_through_handler(
            ExpiredSignatureError('Signature has expired'), 'text/html')
        self.assertEqual(302, resp.status_code)

        record = self._sole_record(
            'Expired JWT, redirecting browser to root')
        self._assert_attribution(record, 'ExpiredSignatureError')

    def test_missing_authorization_401_is_attributable(self):
        resp = self._raise_through_handler(
            NoAuthorizationError('Missing Authorization Header'),
            'application/json')
        self.assertEqual(401, resp.status_code)

        record = self._sole_record(
            'API request rejected, JWT authorization failed')
        self._assert_attribution(record, 'NoAuthorizationError')

    def test_type_error_400_is_attributable(self):
        resp = self._raise_through_handler(
            TypeError("'<=' not supported between instances of "
                      "'str' and 'int'"),
            'application/json')
        self.assertEqual(400, resp.status_code)
        body = json.loads(resp.get_data(as_text=True))
        self.assertIn('not supported', body['error'])

        record = self._sole_record('API request rejected as malformed')
        self._assert_attribution(record, 'TypeError')

    def test_success_path_logs_nothing(self):
        @api_base.handle_authorization_exceptions
        def _fine():
            return 'ok'

        with self.app.test_request_context('/auth/namespaces', method='GET'):
            self.assertEqual('ok', _fine())
        self.assertEqual([], self.capture.records)
