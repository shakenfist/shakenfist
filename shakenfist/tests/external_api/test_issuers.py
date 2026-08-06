"""The trusted issuer CRUD API.

Configuring who may vouch for identities on a cluster is the most
privileged thing in the federation design: an attacker who can add an
issuer can mint themselves keys in any namespace a rule targets. So the
tests here care as much about who is refused as about what works.
"""

import json
import logging
import sys
from unittest import mock

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.trusted_issuer import TrustedIssuer


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

    def test_a_second_issuer_for_one_url_is_refused(self):
        # Token validation resolves an issuer by its URL, so two live
        # records claiming the same URL would make which provider's
        # keys we trust depend on listing order.
        self.assertEqual(200, self._create().status_code)
        resp = self._create(
            name='github-again', jwks_uri='https://evil.example.com/jwks')

        self.assertEqual(409, resp.status_code)
        self.assertIn('github', resp.get_json()['error'])

    def test_the_url_check_and_the_write_happen_under_one_lock(self):
        # issuer_url has no unique index behind it -- it lives in the
        # attributes row, and a soft-deleted issuer keeps its URL so
        # that the URL can be reused. Uniqueness is therefore a read
        # followed by a write, and only the lock makes that a decision
        # rather than a race: without it, two administrators
        # configuring the same provider at once both read "free" and
        # both write.
        events = []

        real_acquire = mariadb.acquire_cluster_lock
        real_release = mariadb.release_cluster_lock

        def acquire(objecttype, subtype, name, lock_data):
            events.append(('acquire', objecttype))
            return real_acquire(objecttype, subtype, name, lock_data)

        def release(objecttype, subtype, name, lock_data):
            events.append(('release', objecttype))
            return real_release(objecttype, subtype, name, lock_data)

        original_new = TrustedIssuer.new

        def new(*args, **kwargs):
            events.append(('create', None))
            return original_new(*args, **kwargs)

        with mock.patch('shakenfist.mariadb.acquire_cluster_lock', acquire), \
                mock.patch(
                    'shakenfist.mariadb.release_cluster_lock', release), \
                mock.patch.object(TrustedIssuer, 'new', new):
            self.assertEqual(200, self._create().status_code)

        # The create has to sit strictly between an acquire and the
        # matching release of the issuer URL lock.
        relevant = [e for e in events
                    if e[1] == 'trusted_issuer_urls' or e[0] == 'create']
        self.assertEqual(
            [('acquire', 'trusted_issuer_urls'),
             ('create', None),
             ('release', 'trusted_issuer_urls')], relevant)

    def test_a_deleted_issuers_url_can_be_reused(self):
        # The conflict is with live issuers only, or disowning a
        # compromised provider would make its URL unusable forever.
        self._create()
        self.assertEqual(200, self.client.delete(
            '/auth/issuers/github',
            headers={'Authorization': self.admin}).status_code)

        self.assertEqual(200, self._create(name='github2').status_code)

    def test_update_cannot_steal_another_issuers_url(self):
        self._create()
        self._create(name='authentik', issuer_url='https://auth.example.com',
                     jwks_uri='https://auth.example.com/jwks')

        resp = self.client.put(
            '/auth/issuers/authentik',
            headers={'Authorization': self.admin},
            data=json.dumps({
                'issuer_url': GITHUB,
                'jwks_uri': 'https://evil.example.com/jwks',
                'audience': 'https://sf.example.com'}))
        self.assertEqual(409, resp.status_code)

    def test_update_can_leave_the_url_alone(self):
        # The conflict check must not trip over the issuer being
        # updated, or no issuer could ever change its audience.
        self._create()
        resp = self.client.put(
            '/auth/issuers/github',
            headers={'Authorization': self.admin},
            data=json.dumps({
                'issuer_url': GITHUB, 'jwks_uri': GITHUB_JWKS,
                'audience': 'https://sf3.example.com'}))

        self.assertEqual(200, resp.status_code)
        self.assertEqual('https://sf3.example.com',
                         resp.get_json()['audience'])

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

    def test_a_deleted_issuer_is_gone_immediately(self):
        # Not "gone once the reaper runs". Until the row is collected
        # the object still exists, so every read path has to agree that
        # this cluster no longer trusts it.
        self._create()
        self.assertEqual(200, self.client.delete(
            '/auth/issuers/github',
            headers={'Authorization': self.admin}).status_code)

        self.assertEqual(404, self.client.get(
            '/auth/issuers/github',
            headers={'Authorization': self.admin}).status_code)

        resp = self.client.get(
            '/auth/issuers', headers={'Authorization': self.admin})
        self.assertEqual([], resp.get_json())

    def test_a_deleted_issuers_name_can_be_reused(self):
        self._create()
        self.client.delete(
            '/auth/issuers/github', headers={'Authorization': self.admin})

        resp = self._create(audience='https://sf2.example.com')
        self.assertEqual(200, resp.status_code)
        self.assertEqual('https://sf2.example.com',
                         resp.get_json()['audience'])

    def test_database_outage_is_503_not_404(self):
        # A read failure must not be reported as "no such issuer". The
        # exchange resolves issuers by name and treats a miss as an
        # authoritative refusal, so an outage answering "not found"
        # would look like a deliberate configuration change.
        self._create()
        with mock.patch(
                'shakenfist.mariadb.get_trusted_issuer_by_name',
                side_effect=exceptions.DatabaseUnavailable('unreadable')):
            resp = self.client.get(
                '/auth/issuers/github',
                headers={'Authorization': self.admin})
        self.assertEqual(503, resp.status_code)

    def test_database_outage_does_not_report_an_empty_issuer_list(self):
        # "This cluster trusts nobody" is a valid-looking answer, which
        # is exactly why it must not be what an outage produces.
        self._create()
        with mock.patch(
                'shakenfist.mariadb.get_all_trusted_issuers',
                side_effect=exceptions.DatabaseUnavailable('unreadable')):
            resp = self.client.get(
                '/auth/issuers', headers={'Authorization': self.admin})
        self.assertEqual(503, resp.status_code)

    def test_update_of_a_deleted_issuer_is_404(self):
        self._create()
        self.client.delete(
            '/auth/issuers/github', headers={'Authorization': self.admin})

        resp = self.client.put(
            '/auth/issuers/github',
            headers={'Authorization': self.admin},
            data=json.dumps({
                'issuer_url': GITHUB, 'jwks_uri': GITHUB_JWKS,
                'audience': 'x'}))
        self.assertEqual(404, resp.status_code)

    def test_listing(self):
        self._create()
        self._create(name='authentik', issuer_url='https://auth.example.com',
                     jwks_uri='https://auth.example.com/jwks')
        resp = self.client.get(
            '/auth/issuers', headers={'Authorization': self.admin})
        self.assertEqual(200, resp.status_code)
        self.assertEqual({'authentik', 'github'},
                         {i['name'] for i in resp.get_json()})
