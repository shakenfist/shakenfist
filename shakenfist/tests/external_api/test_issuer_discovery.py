"""Discovery, where the issuer endpoints meet an identity provider.

The asymmetry these tests exist to pin is the one an implementation can
get wrong without any test failing: an operator who supplied no JWKS URI
is relying on the discovery document for it, so a failed fetch has to
refuse the request, while an operator who supplied one is relying on
discovery only for endpoints a human login flow uses -- so a failed
fetch there is a warning on a request which still succeeds.

Getting the second half wrong breaks every deployment in existence,
because every issuer configured before discovery existed supplies a
JWKS URI and most of them are workload issuers whose provider either
publishes no document or publishes one nobody has ever fetched.
test_a_workload_create_survives_a_provider_with_no_document is that
case, named so it is obvious what a failure means.

Nothing here touches the network: oidc_discovery.fetch() is mocked in
every test, which is also the only honest way to test a provider that
answers badly.
"""

import json
import logging
import sys
import time
from unittest import mock

from shakenfist import exceptions
from shakenfist import federation
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.trusted_issuer import TrustedIssuer


GITHUB = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS = GITHUB + '/.well-known/jwks'

AUTHENTIK = 'https://auth.example.com/application/o/sf/'
AUTHENTIK_JWKS = AUTHENTIK + 'jwks/'


def document(issuer=AUTHENTIK, **overrides):
    """What oidc_discovery.fetch() hands back for a good document.

    Only the members fetch() returns, because that is all a caller can
    see: the module validates the document and then narrows it to the
    things an issuer stores.
    """
    out = {
        'issuer': issuer,
        'jwks_uri': issuer + 'jwks/',
        'authorization_endpoint': issuer + 'authorize/',
        'token_endpoint': issuer + 'token/',
        'device_authorization_endpoint': issuer + 'device/',
    }
    out.update(overrides)
    return {k: v for k, v in out.items() if v is not None}


class IssuerDiscoveryTestCase(base.ShakenFistTestCase):
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

        # The default is a provider which does not answer. Each test
        # which wants a document says so, which keeps "what happens
        # when discovery fails" from being something a test gets by
        # forgetting to set it up.
        self.fetch = mock.patch('shakenfist.oidc_discovery.fetch').start()
        self.fetch.side_effect = exceptions.OIDCDiscoveryFailed(
            'https://provider.example.com/.well-known/openid-configuration '
            'answered with status 404')
        self.addCleanup(mock.patch.stopall)

        self.client = external_api.app.test_client()
        self.admin = self._token('system', 'bar')
        self.user = self._token('banana', 'bacon')

    def _token(self, namespace, key):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': namespace, 'key': key}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _create(self, token=None, **overrides):
        """Configure an issuer.

        A None value omits the key rather than sending a JSON null, so
        that `jwks_uri=None` reads as "the operator did not supply one"
        -- which is the case these tests are about.
        """
        body = {
            'name': 'authentik',
            'issuer_url': AUTHENTIK,
            'jwks_uri': AUTHENTIK_JWKS,
            'audience': 'https://sf.example.com'
        }
        body.update(overrides)
        return self.client.post(
            '/auth/issuers',
            headers={'Authorization': token or self.admin},
            data=json.dumps({k: v for k, v in body.items() if v is not None}))

    def _update(self, name='authentik', token=None, **overrides):
        body = {
            'issuer_url': AUTHENTIK,
            'jwks_uri': AUTHENTIK_JWKS,
            'audience': 'https://sf.example.com'
        }
        body.update(overrides)
        return self.client.put(
            '/auth/issuers/%s' % name,
            headers={'Authorization': token or self.admin},
            data=json.dumps({k: v for k, v in body.items() if v is not None}))

    def _discover(self, name='authentik', token=None):
        return self.client.post(
            '/auth/issuers/%s/discover' % name,
            headers={'Authorization': token or self.admin})

    def _issuer(self, name='authentik'):
        resp = self.client.get(
            '/auth/issuers/%s' % name, headers={'Authorization': self.admin})
        self.assertEqual(200, resp.status_code)
        return resp.get_json()

    def _audit_messages(self, add_event):
        return [c.args[1] for c in add_event.call_args_list
                if len(c.args) > 1]

    # ------------------------------------------------------------------
    # No jwks_uri supplied: discovery is mandatory.
    # ------------------------------------------------------------------
    def test_discovery_fills_in_an_omitted_jwks_uri(self):
        self.fetch.side_effect = None
        self.fetch.return_value = document()

        before = time.time()
        resp = self._create(jwks_uri=None)
        self.assertEqual(200, resp.status_code)

        body = resp.get_json()
        self.assertEqual(AUTHENTIK + 'jwks/', body['jwks_uri'])
        self.assertEqual(AUTHENTIK + 'authorize/',
                         body['authorization_endpoint'])
        self.assertEqual(AUTHENTIK + 'token/', body['token_endpoint'])
        self.assertEqual(AUTHENTIK + 'device/',
                         body['device_authorization_endpoint'])
        self.assertTrue(body['discovered_at'] >= before)

        # The fetch is asked about the configured issuer URL, trailing
        # slash and all: oidc_discovery owns what gets appended to it.
        self.fetch.assert_called_once_with(AUTHENTIK)

    def test_a_create_without_a_jwks_uri_fails_when_discovery_fails(self):
        # An issuer with no JWKS URI cannot verify a signature at all,
        # so a half-created one is worse than none.
        resp = self._create(jwks_uri=None)
        self.assertEqual(502, resp.status_code)
        self.assertIn('404', resp.get_json()['error'])

        self.assertEqual(404, self.client.get(
            '/auth/issuers/authentik',
            headers={'Authorization': self.admin}).status_code)

    def test_an_explicit_null_jwks_uri_asks_for_discovery(self):
        # Null equals absent, which is what the API promises
        # everywhere else and what a client assembling a body from
        # optional values will send. The observable is that it takes
        # the mandatory-discovery path rather than being refused as a
        # malformed value.
        resp = self.client.post(
            '/auth/issuers',
            headers={'Authorization': self.admin},
            data=json.dumps({
                'name': 'authentik', 'issuer_url': AUTHENTIK,
                'jwks_uri': None, 'audience': 'https://sf.example.com'}))
        self.assertEqual(502, resp.status_code)
        self.fetch.assert_called_once_with(AUTHENTIK)

    def test_a_create_without_a_jwks_uri_fails_on_an_unusable_ca_bundle(self):
        # Ours rather than theirs, so 503 rather than 502: nothing was
        # asked of the provider.
        self.fetch.side_effect = exceptions.JWKSTrustAnchorUnusable(
            'FEDERATION_JWKS_CA_BUNDLE /nope.pem could not be loaded')
        resp = self._create(jwks_uri=None)
        self.assertEqual(503, resp.status_code)

    def test_an_update_without_a_jwks_uri_fails_when_discovery_fails(self):
        self.assertEqual(200, self._create().status_code)
        resp = self._update(jwks_uri=None)
        self.assertEqual(502, resp.status_code)

        # And the issuer it refused to update is untouched.
        self.assertEqual(AUTHENTIK_JWKS, self._issuer()['jwks_uri'])

    # ------------------------------------------------------------------
    # jwks_uri supplied: discovery is best effort.
    # ------------------------------------------------------------------
    def test_a_workload_create_survives_a_provider_with_no_document(self):
        # The regression guard. This is every issuer configured today:
        # a jwks_uri an operator typed, and a provider which publishes
        # no discovery document we can fetch. It must still work, and
        # the failure must be recorded rather than silent.
        with mock.patch.object(TrustedIssuer, 'add_event') as add_event:
            resp = self._create(name='github', issuer_url=GITHUB,
                                jwks_uri=GITHUB_JWKS)

        self.assertEqual(200, resp.status_code)
        self.assertEqual(GITHUB_JWKS, resp.get_json()['jwks_uri'])
        self.assertIsNone(resp.get_json()['discovered_at'])
        self.assertIn(
            'OIDC discovery failed, keeping the configured jwks_uri',
            self._audit_messages(add_event))

    def test_a_workload_create_survives_an_unusable_ca_bundle(self):
        # Same reasoning: a CA bundle this cluster cannot load is our
        # misconfiguration, and taking out every issuer create until
        # somebody fixes it would be a worse answer than an audit event.
        self.fetch.side_effect = exceptions.JWKSTrustAnchorUnusable(
            'FEDERATION_JWKS_CA_BUNDLE /nope.pem could not be loaded')

        with mock.patch.object(TrustedIssuer, 'add_event') as add_event:
            resp = self._create(name='github', issuer_url=GITHUB,
                                jwks_uri=GITHUB_JWKS)

        self.assertEqual(200, resp.status_code)
        self.assertEqual(GITHUB_JWKS, resp.get_json()['jwks_uri'])
        self.assertIn(
            'OIDC discovery skipped, this cluster cannot build a TLS '
            'trust store', self._audit_messages(add_event))

    def test_endpoints_are_discovered_even_when_jwks_uri_is_supplied(self):
        # Best effort is about what a failure does, not about whether
        # the fetch happens.
        self.fetch.side_effect = None
        self.fetch.return_value = document()

        resp = self._create()
        self.assertEqual(200, resp.status_code)
        self.assertEqual(AUTHENTIK + 'device/',
                         resp.get_json()['device_authorization_endpoint'])

    # ------------------------------------------------------------------
    # Both, and they disagree.
    # ------------------------------------------------------------------
    def test_a_supplied_jwks_uri_wins_a_disagreement(self):
        # Not a refusal. The operator guide describes a provider whose
        # published JWKS path answers with a redirect to a CDN, which
        # the fetch will not follow -- so the correct value there is
        # deliberately not the published one, and refusing would break
        # a deployment the documentation tells operators to build.
        self.fetch.side_effect = None
        self.fetch.return_value = document(
            jwks_uri='https://cdn.example.com/elsewhere/jwks')

        with mock.patch.object(TrustedIssuer, 'add_event') as add_event:
            resp = self._create(jwks_uri=AUTHENTIK_JWKS)

        self.assertEqual(200, resp.status_code)
        self.assertEqual(AUTHENTIK_JWKS, resp.get_json()['jwks_uri'])

        disagreements = [
            c for c in add_event.call_args_list
            if len(c.args) > 1 and 'differs from' in c.args[1]]
        self.assertEqual(1, len(disagreements))
        extra = disagreements[0].kwargs['extra']
        self.assertEqual(AUTHENTIK_JWKS, extra['configured_jwks_uri'])
        self.assertEqual('https://cdn.example.com/elsewhere/jwks',
                         extra['published_jwks_uri'])

    def test_agreement_is_not_audited(self):
        # The anti-vacuity half of the test above: if every create
        # recorded a disagreement, that assertion would pass without
        # the comparison working.
        self.fetch.side_effect = None
        self.fetch.return_value = document()

        with mock.patch.object(TrustedIssuer, 'add_event') as add_event:
            self.assertEqual(200, self._create().status_code)

        self.assertEqual([], [m for m in self._audit_messages(add_event)
                              if 'differs from' in m])

    # ------------------------------------------------------------------
    # discover: false
    # ------------------------------------------------------------------
    def test_discover_false_skips_the_fetch(self):
        resp = self._create(discover=False)
        self.assertEqual(200, resp.status_code)
        self.fetch.assert_not_called()
        self.assertIsNone(resp.get_json()['authorization_endpoint'])

    def test_discover_false_requires_a_jwks_uri(self):
        # Asking for no discovery and supplying no JWKS URI asks for an
        # issuer which can never verify a token.
        resp = self._create(jwks_uri=None, discover=False)
        self.assertEqual(400, resp.status_code)
        self.assertIn('jwks_uri', resp.get_json()['error'])
        self.fetch.assert_not_called()

    def test_discover_false_leaves_discovered_endpoints_alone(self):
        # An operator setting a client registration must not have to
        # reach the provider to do it, and must not lose the endpoints
        # a previous run found by not reaching it.
        self.fetch.side_effect = None
        self.fetch.return_value = document()
        self.assertEqual(200, self._create().status_code)

        resp = self._update(discover=False, client_id='sf-cli',
                            oidc_scopes='openid profile')
        self.assertEqual(200, resp.status_code)

        body = resp.get_json()
        self.assertEqual('sf-cli', body['client_id'])
        self.assertEqual('openid profile', body['oidc_scopes'])
        self.assertEqual(AUTHENTIK + 'device/',
                         body['device_authorization_endpoint'])
        self.assertEqual(1, self.fetch.call_count)

    # ------------------------------------------------------------------
    # The client registration is not part of the identity triple.
    # ------------------------------------------------------------------
    def test_an_update_of_the_identity_fields_keeps_the_client_id(self):
        # The regression this pair of tests exists for. The documented
        # way to repoint an issuer is a PUT carrying issuer_url,
        # jwks_uri and audience, which is also the only body a client
        # or script written before this phase knows how to send. If
        # that cleared the client registration, correcting an audience
        # would turn human login off as a side effect, silently.
        self.assertEqual(200, self._create(
            client_id='sf-cli', oidc_scopes='openid profile').status_code)

        resp = self._update(audience='https://sf2.example.com')
        self.assertEqual(200, resp.status_code)
        self.assertEqual('https://sf2.example.com',
                         resp.get_json()['audience'])
        self.assertEqual('sf-cli', resp.get_json()['client_id'])
        self.assertEqual('openid profile', resp.get_json()['oidc_scopes'])

    def test_an_empty_client_id_clears_the_client_registration(self):
        # The other half: an absent key cannot mean "clear", so
        # something has to, and an empty string is not a valid client
        # identifier. Stored as null rather than as an empty string,
        # because whether this issuer can be logged into is read as a
        # presence test.
        self.assertEqual(200, self._create(
            client_id='sf-cli', oidc_scopes='openid').status_code)

        resp = self._update(client_id='', oidc_scopes='')
        self.assertEqual(200, resp.status_code)
        self.assertIsNone(resp.get_json()['client_id'])
        self.assertIsNone(resp.get_json()['oidc_scopes'])

    def test_a_discovery_run_never_touches_the_client_registration(self):
        # A document says nothing about a registration an operator
        # performed at the provider's console, so a refresh must not be
        # able to disturb one.
        self.assertEqual(200, self._create(
            client_id='sf-cli', oidc_scopes='openid').status_code)

        self.fetch.side_effect = None
        self.fetch.return_value = document()
        resp = self._discover()
        self.assertEqual(200, resp.status_code)
        self.assertEqual('sf-cli', resp.get_json()['client_id'])
        self.assertEqual('openid', resp.get_json()['oidc_scopes'])

    # ------------------------------------------------------------------
    # A login capable issuer always ends a write with a scope list.
    # ------------------------------------------------------------------
    def test_a_client_id_with_no_scopes_gets_the_mandatory_one(self):
        # openid is required in every OpenID Connect flow, so an issuer
        # with a registration and no scopes is one no client can use.
        # Defaulting it here means a client does not hard code it.
        self.assertEqual(200, self._create(client_id='sf-cli').status_code)

        self.assertEqual('openid', self._issuer()['oidc_scopes'])

    def test_scopes_the_operator_chose_are_kept(self):
        self.assertEqual(200, self._create(
            client_id='sf-cli',
            oidc_scopes='openid profile groups').status_code)

        self.assertEqual('openid profile groups',
                         self._issuer()['oidc_scopes'])

    def test_an_update_which_adds_a_client_id_gets_the_scope(self):
        # The case a default written per write path would miss: the
        # registration arrives in a later request than the one which
        # created the issuer, and this request mentions no scopes at
        # all.
        self.assertEqual(200, self._create().status_code)
        self.assertIsNone(self._issuer()['oidc_scopes'])

        resp = self._update(client_id='sf-cli')
        self.assertEqual(200, resp.status_code)
        self.assertEqual('sf-cli', resp.get_json()['client_id'])
        self.assertEqual('openid', resp.get_json()['oidc_scopes'])

    def test_clearing_the_scopes_is_not_undone_by_the_default(self):
        # An operator who clears the column is saying they will manage
        # the scope list themselves, and re-applying the default in the
        # same request would make that impossible to say.
        self.assertEqual(200, self._create(
            client_id='sf-cli', oidc_scopes='openid profile').status_code)

        resp = self._update(oidc_scopes='')
        self.assertEqual(200, resp.status_code)
        self.assertEqual('sf-cli', resp.get_json()['client_id'])
        self.assertIsNone(resp.get_json()['oidc_scopes'])

    def test_an_issuer_with_no_client_id_gets_no_scopes(self):
        # Which is every issuer configured today. A workload issuer has
        # no registration, so there is no flow to request a scope for
        # and nothing to default.
        self.assertEqual(200, self._create().status_code)

        self.assertIsNone(self._issuer()['oidc_scopes'])

    def test_the_default_is_not_rewritten_by_a_later_update(self):
        # Idempotence, asserted on the write rather than on the value:
        # once the invariant holds, a PUT which does not mention scopes
        # must leave the column out of its field mask entirely, or
        # every unrelated edit would rewrite it and event it.
        self.assertEqual(200, self._create(client_id='sf-cli').status_code)
        self.assertEqual('openid', self._issuer()['oidc_scopes'])

        with mock.patch(
                'shakenfist.mariadb.update_trusted_issuer_attributes',
                side_effect=(
                    self.mock_mariadb._mariadb_update_trusted_issuer_attributes
                )) as update:
            self.assertEqual(
                200, self._update(audience='https://sf2.example.com')
                .status_code)

        [call] = update.call_args_list
        self.assertNotIn('oidc_scopes', call.kwargs['fields'])

    # ------------------------------------------------------------------
    # A successful run is authoritative about what the provider offers.
    # ------------------------------------------------------------------
    def test_a_withdrawn_endpoint_stops_being_advertised(self):
        self.fetch.side_effect = None
        self.fetch.return_value = document()
        self.assertEqual(200, self._create().status_code)

        # The provider has turned the device code grant off, so it no
        # longer publishes the endpoint. A client reads the endpoint's
        # presence as the signal that the flow is available, so a stale
        # one would offer a flow that cannot work.
        self.fetch.return_value = document(
            device_authorization_endpoint=None)
        resp = self._update()
        self.assertEqual(200, resp.status_code)
        self.assertIsNone(resp.get_json()['device_authorization_endpoint'])
        self.assertEqual(AUTHENTIK + 'token/',
                         resp.get_json()['token_endpoint'])

    # ------------------------------------------------------------------
    # The refresh endpoint.
    # ------------------------------------------------------------------
    def test_the_refresh_endpoint_writes_what_it_finds(self):
        self.assertEqual(200, self._create().status_code)
        self.assertIsNone(self._issuer()['token_endpoint'])

        self.fetch.side_effect = None
        self.fetch.return_value = document()

        before = time.time()
        resp = self._discover()
        self.assertEqual(200, resp.status_code)

        body = resp.get_json()
        self.assertEqual(AUTHENTIK + 'token/', body['token_endpoint'])
        self.assertTrue(body['discovered_at'] >= before)
        # The operator's JWKS URI is not replaced by a discovered one,
        # because nothing stored says which of the two somebody typed.
        self.assertEqual(AUTHENTIK_JWKS, body['jwks_uri'])
        # And the rest of the configuration is untouched.
        self.assertEqual('https://sf.example.com', body['audience'])

    def test_the_refresh_endpoint_fills_in_a_missing_jwks_uri(self):
        # Built through the object rather than the endpoint, because
        # the endpoint cannot produce an issuer with no JWKS URI: a
        # create which omits one requires discovery to succeed, and a
        # successful discovery supplies it. The case is covered anyway
        # because repairing a null column is the one part of this
        # write which is not writing a value back over itself, and
        # because the object API does permit the row.
        url = AUTHENTIK + 'nokeys/'
        self.assertIsNotNone(TrustedIssuer.new(
            'nokeys', url, None, 'https://sf.example.com'))
        self.assertIsNone(self._issuer('nokeys')['jwks_uri'])

        self.fetch.side_effect = None
        self.fetch.return_value = document(issuer=url)

        resp = self._discover(name='nokeys')
        self.assertEqual(200, resp.status_code)
        self.assertEqual(url + 'jwks/', resp.get_json()['jwks_uri'])

    def test_the_refresh_endpoint_refuses_a_failed_fetch(self):
        # Unlike a create or an update, the fetch is the only thing
        # this request asked for, so answering 200 would be a lie.
        self.assertEqual(200, self._create().status_code)
        resp = self._discover()
        self.assertEqual(502, resp.status_code)
        self.assertIn('404', resp.get_json()['error'])

    def test_the_refresh_endpoint_is_admin_only(self):
        # An issuer is who this cluster believes, so reading or
        # re-reading its configuration is an administrative act.
        self.assertEqual(200, self._create().status_code)
        self.assertEqual(401, self._discover(token=self.user).status_code)

    def test_the_refresh_endpoint_404s_for_an_unknown_issuer(self):
        self.assertEqual(404, self._discover(name='nope').status_code)
        self.fetch.assert_not_called()

    def test_the_refresh_endpoint_404s_for_a_deleted_issuer(self):
        self.assertEqual(200, self._create().status_code)
        self.assertEqual(200, self.client.delete(
            '/auth/issuers/authentik',
            headers={'Authorization': self.admin}).status_code)
        self.assertEqual(404, self._discover().status_code)

    # ------------------------------------------------------------------
    # Odds and ends which are easy to get wrong.
    # ------------------------------------------------------------------
    def test_the_documents_issuer_member_is_not_stored(self):
        # fetch() returns it, and it is deliberately ignored: there is
        # no column for it, and it equals the configured issuer_url by
        # construction because the fetch refuses any document where it
        # does not.
        self.fetch.side_effect = None
        self.fetch.return_value = document()

        resp = self._create()
        self.assertEqual(200, resp.status_code)
        self.assertNotIn('issuer', resp.get_json())
        self.assertEqual(AUTHENTIK, resp.get_json()['issuer_url'])

    def test_a_client_id_longer_than_its_column_is_refused(self):
        resp = self._create(client_id='x' * 256)
        self.assertEqual(422, resp.status_code)

    def test_the_jwks_cache_is_dropped_when_an_issuer_is_written(self):
        # Belt and braces -- the cache replaces a client whose uri has
        # changed anyway -- but a cache serving a disowned provider's
        # keys is a failure nobody would find quickly.
        self.assertEqual(200, self._create().status_code)
        uuid = self._issuer()['uuid']

        with mock.patch.object(federation.JWKS_CACHE, 'forget') as forget:
            self.assertEqual(200, self._update().status_code)
        forget.assert_called_once_with(uuid)

        self.fetch.side_effect = None
        self.fetch.return_value = document()
        with mock.patch.object(federation.JWKS_CACHE, 'forget') as forget:
            self.assertEqual(200, self._discover().status_code)
        forget.assert_called_once_with(uuid)
