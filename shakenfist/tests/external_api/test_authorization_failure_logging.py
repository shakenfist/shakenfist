# Copyright 2019 Michael Still and contributors
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

    def test_a_type_error_is_not_an_authorization_failure(self):
        """Phase 5 of PLAN-api-input-validation, decision D23.

        A TypeError used to be caught here and answered as a 400
        carrying str(e) -- the second half of the mechanism issue 3612
        describes, and a workaround for the absence of the validation
        layer phases 1 to 4 built. It is not an authorization
        condition: nothing in flask_jwt_extended or PyJWT signals one
        with it, and there is no attribute on a TypeError that would
        let this wrapper tell where it came from. So it must now travel
        straight through, to be recorded and answered as the server
        fault it is by the decorators outside this one.

        Asserted in this frame as well as at request level (see
        test_request_validation's
        test_a_handler_internal_type_error_is_a_recorded_500), because
        this is the frame the arm lived in: restoring it fails this
        test on the exception which no longer escapes, however the
        response is shaped further out.
        """
        with self.assertRaises(TypeError):
            self._raise_through_handler(
                TypeError("'<=' not supported between instances of "
                          "'str' and 'int'"),
                'application/json')

        # And this wrapper said nothing about it. An INFO line here
        # claiming the *request* was malformed would be a second and
        # wrong attribution of a server side fault, on top of the one
        # suppress_exceptions_to_client correctly emits.
        self.assertEqual([], self.capture.records)

    def test_success_path_logs_nothing(self):
        @api_base.handle_authorization_exceptions
        def _fine():
            return 'ok'

        with self.app.test_request_context('/auth/namespaces', method='GET'):
            self.assertEqual('ok', _fine())
        self.assertEqual([], self.capture.records)
