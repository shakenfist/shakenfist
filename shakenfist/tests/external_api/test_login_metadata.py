"""What an unauthenticated caller may learn about human login.

GET /auth/login is reachable with no credential at all, which makes two
things worth pinning rather than reviewing by eye.

The first is which issuers it lists. An issuer with no client
registration, or with no endpoint a flow can begin at, cannot be logged
in through -- and every issuer configured today is one of those,
because they exist to verify workload tokens. Listing one would
disclose a provider for no purpose and offer a person a login that
cannot work.

The second is the field set, which is asserted exactly rather than by
checking that a particular leak is absent. Absence tests only catch the
leak somebody already thought of; the endpoint builds its response
field by field so that a column added to an issuer later is not
published by accident, and an exact key set is the assertion that
notices when that stops being true.
"""

import json
import logging
import sys
from unittest import mock

from shakenfist import exceptions
from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


IDP = 'https://idp.example.com/application/o/sf/'
IDP_JWKS = IDP + 'jwks/'

# What a login capable issuer serves, and nothing else. jwks_uri is
# ours to verify signatures with, audience is how this cluster names
# itself to the provider, and uuid, state and version are an
# administrator's business.
FULL_KEYS = {
    'name', 'issuer_url', 'client_id', 'oidc_scopes',
    'authorization_endpoint', 'token_endpoint',
    'device_authorization_endpoint'
}


def document(issuer=IDP, **overrides):
    """What oidc_discovery.fetch() hands back for a good document.

    Only the members fetch() returns, because the module validates the
    document and then narrows it to what an issuer stores.
    """
    out = {
        'issuer': issuer,
        'jwks_uri': issuer + 'jwks/',
        'authorization_endpoint': issuer + 'authorize/',
        'token_endpoint': issuer + 'token/',
        'device_authorization_endpoint': issuer + 'device/'
    }
    out.update(overrides)
    return {k: v for k, v in out.items() if v is not None}


class LoginMetadataTestCase(base.ShakenFistTestCase):
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

        # No network, ever. Each test says what the provider published
        # by setting a return value before it configures an issuer.
        self.fetch = mock.patch('shakenfist.oidc_discovery.fetch').start()
        self.fetch.return_value = document()
        self.addCleanup(mock.patch.stopall)

        self.client = external_api.app.test_client()
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.admin = 'Bearer %s' % resp.get_json()['access_token']

    def _create(self, name='corporate', issuer_url=IDP, jwks_uri=IDP_JWKS,
                **overrides):
        body = {
            'name': name,
            'issuer_url': issuer_url,
            'jwks_uri': jwks_uri,
            'audience': 'https://sf.example.com'
        }
        body.update(overrides)
        resp = self.client.post(
            '/auth/issuers', headers={'Authorization': self.admin},
            data=json.dumps({k: v for k, v in body.items() if v is not None}))
        self.assertEqual(200, resp.status_code, resp.get_data(as_text=True))
        return resp.get_json()

    def _login(self):
        # Deliberately no Authorization header: a person about to log
        # in has nothing to put in one.
        return self.client.get('/auth/login')

    def _rate_limit(self, limit):
        patcher = mock.patch.object(
            config, 'FEDERATION_RATE_LIMIT_PER_MINUTE', limit)
        patcher.start()
        self.addCleanup(patcher.stop)


class LoginCapabilityTestCase(LoginMetadataTestCase):
    def test_a_login_capable_issuer_is_offered(self):
        self._create(client_id='shakenfist', oidc_scopes='openid profile')

        resp = self._login()

        self.assertEqual(200, resp.status_code)
        [entry] = resp.get_json()
        self.assertEqual(FULL_KEYS, set(entry.keys()))
        self.assertEqual(
            {
                'name': 'corporate',
                'issuer_url': IDP,
                'client_id': 'shakenfist',
                'oidc_scopes': 'openid profile',
                'authorization_endpoint': IDP + 'authorize/',
                'token_endpoint': IDP + 'token/',
                'device_authorization_endpoint': IDP + 'device/'
            }, entry)

    def test_an_issuer_with_no_client_id_is_omitted(self):
        # Which is every issuer configured today: a client
        # registration is something an operator performs at the
        # provider for human login, and a workload issuer has none.
        self._create()

        resp = self._login()

        self.assertEqual(200, resp.status_code)
        self.assertEqual([], resp.get_json())

    def test_an_issuer_with_no_flow_endpoint_is_omitted(self):
        # A client registration with nowhere to send it. The
        # provider publishes a document, so the create succeeds, but it
        # names no endpoint a flow could begin at.
        self.fetch.return_value = document(
            authorization_endpoint=None, token_endpoint=None,
            device_authorization_endpoint=None)
        issuer = self._create(client_id='shakenfist')
        self.assertIsNone(issuer['authorization_endpoint'])
        self.assertIsNone(issuer['device_authorization_endpoint'])

        resp = self._login()

        self.assertEqual(200, resp.status_code)
        self.assertEqual([], resp.get_json())

    def test_a_device_only_issuer_is_offered_without_the_absent_keys(self):
        # Half of the point of the endpoint: a client reads the lack of
        # an authorization endpoint as "fall back to the device flow",
        # so an endpoint the provider does not publish is absent rather
        # than null.
        self.fetch.return_value = document(
            authorization_endpoint=None, token_endpoint=None)
        self._create(client_id='shakenfist')

        resp = self._login()

        self.assertEqual(200, resp.status_code)
        [entry] = resp.get_json()
        self.assertEqual(
            {'name', 'issuer_url', 'client_id', 'oidc_scopes',
             'device_authorization_endpoint'}, set(entry.keys()))
        self.assertEqual(IDP + 'device/',
                         entry['device_authorization_endpoint'])

    def test_a_soft_deleted_issuer_is_omitted(self):
        self._create(client_id='shakenfist')
        self.assertEqual(1, len(self._login().get_json()))

        resp = self.client.delete(
            '/auth/issuers/corporate',
            headers={'Authorization': self.admin})
        self.assertEqual(200, resp.status_code)

        resp = self._login()

        self.assertEqual(200, resp.status_code)
        self.assertEqual([], resp.get_json())

    def test_a_cluster_with_no_issuers_offers_no_login(self):
        # An empty list rather than a 404, because "this cluster does
        # not do human login" is an answer a client acts on, and a 404
        # would be indistinguishable from a server which predates the
        # endpoint.
        resp = self._login()

        self.assertEqual(200, resp.status_code)
        self.assertEqual([], resp.get_json())

    def test_only_the_login_capable_issuers_of_several_are_offered(self):
        self.fetch.return_value = document()
        self._create(name='corporate', client_id='shakenfist')

        workload = 'https://token.actions.githubusercontent.com'
        self.fetch.return_value = document(issuer=workload)
        self._create(name='github', issuer_url=workload,
                     jwks_uri=workload + '/.well-known/jwks')

        resp = self._login()

        self.assertEqual(200, resp.status_code)
        self.assertEqual(['corporate'], [e['name'] for e in resp.get_json()])


class LoginMetadataRateLimitTestCase(LoginMetadataTestCase):
    def test_the_rate_limiter_is_consulted_before_any_issuer_read(self):
        # The ordering is the whole of this endpoint's abuse
        # resistance. The listing reads the attributes row of every
        # configured issuer, so a caller who could reach it unmetered
        # would multiply one anonymous request into a database read
        # per issuer.
        self._rate_limit(1)
        self._create(client_id='shakenfist')
        self.assertEqual(200, self._login().status_code)

        issuers = mock.patch(
            'shakenfist.mariadb.get_all_trusted_issuers',
            side_effect=AssertionError(
                'the issuer listing ran above the rate limit')).start()

        resp = self._login()

        self.assertEqual(429, resp.status_code)
        issuers.assert_not_called()

    def test_a_rate_limited_caller_is_told_nothing_useful(self):
        self._rate_limit(1)
        self._create(client_id='shakenfist')
        self.assertEqual(200, self._login().status_code)

        resp = self._login()

        self.assertEqual(429, resp.status_code)
        body = resp.get_data(as_text=True)
        self.assertNotIn('corporate', body)
        self.assertNotIn('idp.example.com', body)

    def test_zero_disables_rate_limiting(self):
        self._rate_limit(0)
        self._create(client_id='shakenfist')

        for _ in range(5):
            self.assertEqual(200, self._login().status_code)
        self.assertEqual({}, self.mock_mariadb.federation_rate_limits)

    def test_the_counter_is_keyed_on_the_source_under_its_own_prefix(self):
        # Per source, as the exchange is, but under a key of this
        # endpoint's own so the two have a budget each.
        self._rate_limit(10)
        self._login()

        [(source, _)] = self.mock_mariadb.federation_rate_limits
        self.assertEqual('login:127.0.0.1', source)

    def test_exhausting_the_login_budget_does_not_refuse_an_exchange(self):
        # The point of the separate key, and the reason it is not an
        # optimisation. A CI cluster behind one NAT spends a whole
        # address's allowance on exchanges, and a shared counter would
        # then refuse every person logging in from that office -- and
        # in this direction, a burst of logins would stop CI minting
        # keys. The two cost very different amounts, so sharing a
        # budget also lets the expensive endpoint starve the cheap one.
        self._rate_limit(1)
        self.assertEqual(200, self._login().status_code)
        self.assertEqual(429, self._login().status_code)

        # Refused on its merits -- an unparseable token from nobody we
        # trust -- rather than on a counter somebody else filled.
        resp = self.client.post('/auth/federated', data=json.dumps(
            {'token': 'not-a-jwt', 'namespace': 'system', 'rule': 'nope'}))
        self.assertEqual(401, resp.status_code)

    def test_an_unreadable_counter_refuses_rather_than_serves(self):
        # Treating an unreadable counter as "under the limit" would
        # turn a database wobble into an open door. The 503 comes from
        # handle_database_unavailable, which every endpoint has, which
        # is why this method catches nothing itself.
        self._rate_limit(10)
        self._create(client_id='shakenfist')

        with mock.patch(
                'shakenfist.mariadb.count_federated_attempt',
                side_effect=exceptions.DatabaseUnavailable('down')):
            resp = self._login()

        self.assertEqual(503, resp.status_code)
