# Copyright 2019 Michael Still and contributors
import flask
from flask_jwt_extended import JWTManager
from jwt.exceptions import DecodeError
from jwt.exceptions import ExpiredSignatureError

from shakenfist.external_api import base as api_base
from shakenfist.tests import base


class AuthorizationRedirectTestCase(base.ShakenFistTestCase):
    """A browser presenting a bad JWT is redirected with cookies cleared.

    Issue 3616: base.py was not type checked, so nothing verified that
    the response handed to unset_jwt_cookies() is the flask Response
    subclass that function requires. flask.redirect() is declared as
    returning werkzeug's Response, so the redirect must be coerced to
    the application's response class before the cookies are cleared.
    """

    def setUp(self):
        super().setUp()

        self.app = flask.Flask(__name__)
        self.app.config['JWT_SECRET_KEY'] = 'test-key'
        JWTManager(self.app)

    def _raise_through_handler(self, exc, accept):
        @api_base.handle_authorization_exceptions
        def _boom():
            raise exc

        with self.app.test_request_context('/thing', headers={'Accept': accept}):
            return _boom()

    def _assert_browser_redirect(self, exc):
        resp = self._raise_through_handler(exc, 'text/html')

        # unset_jwt_cookies() is annotated as taking flask's Response,
        # not werkzeug's. The runtime object must actually be one.
        self.assertIsInstance(resp, flask.Response)
        self.assertEqual(302, resp.status_code)
        self.assertEqual('/', resp.headers['Location'])

        cookies = resp.headers.getlist('Set-Cookie')
        self.assertIn('access_token_cookie=;', ''.join(cookies))
        self.assertIn('refresh_token_cookie=;', ''.join(cookies))

    def test_undecodable_jwt_redirects_browser(self):
        self._assert_browser_redirect(DecodeError('Not enough segments'))

    def test_expired_jwt_redirects_browser(self):
        self._assert_browser_redirect(ExpiredSignatureError('Signature has expired'))

    def test_undecodable_jwt_returns_401_to_api_client(self):
        resp = self._raise_through_handler(
            DecodeError('Not enough segments'), 'application/json')
        self.assertEqual(401, resp.status_code)
        self.assertNotIn('Set-Cookie', resp.headers)

    def test_expired_jwt_returns_401_to_api_client(self):
        resp = self._raise_through_handler(
            ExpiredSignatureError('Signature has expired'), 'application/json')
        self.assertEqual(401, resp.status_code)
        self.assertNotIn('Set-Cookie', resp.headers)
