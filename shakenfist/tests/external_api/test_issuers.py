"""The trusted issuer CRUD API.

Configuring who may vouch for identities on a cluster is the most
privileged thing in the federation design: an attacker who can add an
issuer can mint themselves keys in any namespace a rule targets. So the
tests here care as much about who is refused as about what works.
"""

import json
import logging
import sys

from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


GITHUB = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS = GITHUB + '/.well-known/jwks'


class IssuerEndpointTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False
        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

        self.client = external_api.app.test_client()
        self.admin = self._token('system', 'bar')
        self.user = self._token('banana', 'bacon')

    def _token(self, namespace, key):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': namespace, 'key': key}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _create(self, token=None, **overrides):
        body = {
            'name': 'github',
            'issuer_url': GITHUB,
            'jwks_uri': GITHUB_JWKS,
            'audience': 'https://sf.example.com'
        }
        body.update(overrides)
        return self.client.post(
            '/auth/issuers',
            headers={'Authorization': token or self.admin},
            data=json.dumps(body))

    def test_admin_can_create_and_read_an_issuer(self):
        resp = self._create()
        self.assertEqual(200, resp.status_code)
        self.assertEqual('github', resp.get_json()['name'])

        resp = self.client.get(
            '/auth/issuers/github', headers={'Authorization': self.admin})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(GITHUB, resp.get_json()['issuer_url'])

    def test_non_admin_cannot_create_an_issuer(self):
        # The escalation that matters: anyone who can add an issuer can
        # mint themselves keys wherever a rule points.
        resp = self._create(token=self.user)
        self.assertEqual(401, resp.status_code)

    def test_non_admin_cannot_list_issuers(self):
        resp = self.client.get(
            '/auth/issuers', headers={'Authorization': self.user})
        self.assertEqual(401, resp.status_code)

    def test_unauthenticated_access_is_refused(self):
        self.assertEqual(401, self.client.get('/auth/issuers').status_code)

    def test_duplicate_names_are_refused(self):
        self.assertEqual(200, self._create().status_code)
        resp = self._create()
        self.assertEqual(409, resp.status_code)

    def test_required_fields_are_required(self):
        for field in ('name', 'issuer_url', 'jwks_uri', 'audience'):
            resp = self._create(**{field: None})
            self.assertEqual(
                400, resp.status_code,
                f'omitting {field} should be refused')

    def test_plaintext_jwks_is_refused(self):
        # A JWKS fetched over HTTP can be substituted by anyone on the
        # path, which makes signature verification meaningless.
        resp = self._create(jwks_uri='http://insecure.example.com/jwks')
        self.assertEqual(400, resp.status_code)
        self.assertIn('https', resp.get_json()['error'])

    def test_update_replaces_the_configuration(self):
        self._create()
        resp = self.client.put(
            '/auth/issuers/github',
            headers={'Authorization': self.admin},
            data=json.dumps({
                'issuer_url': 'https://other.example.com',
                'jwks_uri': 'https://other.example.com/jwks',
                'audience': 'https://sf2.example.com'}))
        self.assertEqual(200, resp.status_code)
        self.assertEqual('https://other.example.com',
                         resp.get_json()['issuer_url'])

    def test_update_of_an_unknown_issuer_is_404(self):
        resp = self.client.put(
            '/auth/issuers/nope',
            headers={'Authorization': self.admin},
            data=json.dumps({
                'issuer_url': GITHUB, 'jwks_uri': GITHUB_JWKS,
                'audience': 'x'}))
        self.assertEqual(404, resp.status_code)

    def test_delete(self):
        self._create()
        resp = self.client.delete(
            '/auth/issuers/github', headers={'Authorization': self.admin})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('deleted', resp.get_json()['state'])

    def test_delete_of_an_unknown_issuer_is_404(self):
        resp = self.client.delete(
            '/auth/issuers/nope', headers={'Authorization': self.admin})
        self.assertEqual(404, resp.status_code)

    def test_listing(self):
        self._create()
        self._create(name='authentik')
        resp = self.client.get(
            '/auth/issuers', headers={'Authorization': self.admin})
        self.assertEqual(200, resp.status_code)
        self.assertEqual({'authentik', 'github'},
                         {i['name'] for i in resp.get_json()})
