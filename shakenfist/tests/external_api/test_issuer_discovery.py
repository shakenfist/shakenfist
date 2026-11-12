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
from shakenfist.external_api import auth as api_auth
from shakenfist.tests import base
from shakenfist.schema.trusted_issuer_attributes import (
    TrustedIssuerAttributesData)
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

    def test_a_refresh_is_refused_if_the_issuer_moved_while_fetching(self):
        # The sibling of the jwks_uri case below, and the worse half.
        # Every endpoint in the document was accepted only because the
        # document advertised the pre-fetch issuer_url as its own, so
        # the endpoints describe that provider. Writing them after a PUT
        # has repointed the issuer puts the previous provider's
        # authorisation endpoint against the new issuer_url -- and
        # GET /auth/login hands that to anyone who asks, so a person
        # following it would be sent to the old provider to log in.
        # There is nothing to merge, so the refresh refuses.
        self.assertEqual(200, self._create().status_code)
        self.fetch.side_effect = None
        self.fetch.return_value = document()

        before = self._issuer()
        moved = TrustedIssuerAttributesData(
            uuid=before['uuid'],
            issuer_url='https://elsewhere.example.com/application/o/sf/',
            jwks_uri=AUTHENTIK_JWKS, audience='https://sf.example.com')
        original = TrustedIssuerAttributesData(
            uuid=before['uuid'], issuer_url=AUTHENTIK,
            jwks_uri=AUTHENTIK_JWKS, audience='https://sf.example.com')

        with mock.patch.object(
                TrustedIssuer, 'configuration',
                side_effect=[original, moved, moved, moved]):
            with mock.patch(
                    'shakenfist.mariadb.update_trusted_issuer_attributes'
            ) as update:
                resp = self._discover()

        self.assertEqual(409, resp.status_code)
        self.assertIn('repointed', resp.get_json()['error'])
        # And nothing was written at all, rather than partly written.
        update.assert_not_called()

    def test_a_refresh_writes_the_jwks_uri_it_read_under_the_lock(self):
        # The interleaving the lock was added for, simulated by making
        # the two configuration() reads disagree: the first, before the
        # fetch, sees the old key set, and the second, taken under the
        # lock, sees what a concurrent PUT committed while the provider
        # was being asked. The refresh must write what it read under the
        # lock. Writing what it read first would restore the previous
        # provider's key set on top of the new issuer_url, which is a
        # cluster verifying signatures against the wrong place.
        self.assertEqual(200, self._create().status_code)
        self.fetch.side_effect = None
        self.fetch.return_value = document()

        before = self._issuer()
        old = TrustedIssuerAttributesData(
            uuid=before['uuid'], issuer_url=AUTHENTIK,
            jwks_uri=AUTHENTIK + 'old-keys/',
            audience='https://sf.example.com')
        committed = TrustedIssuerAttributesData(
            uuid=before['uuid'], issuer_url=AUTHENTIK,
            jwks_uri=AUTHENTIK + 'new-keys/',
            audience='https://sf.example.com')

        with mock.patch.object(
                TrustedIssuer, 'configuration',
                side_effect=[old, committed, committed, committed]):
            with mock.patch(
                    'shakenfist.mariadb.update_trusted_issuer_attributes'
            ) as update:
                self.assertEqual(200, self._discover().status_code)

        [call] = update.call_args_list
        self.assertEqual(AUTHENTIK + 'new-keys/', call.args[0].jwks_uri)

    def test_the_refresh_fetches_outside_the_lock_and_writes_inside_it(self):
        # Both halves of the placement matter and they pull opposite
        # ways. The fetch must stay outside, because holding a cluster
        # wide lock across a third party's network timeout blocks every
        # other issuer write for as long as somebody else's provider
        # takes to answer. The write must go inside, because the
        # jwks_uri it writes was resolved from a read taken before the
        # fetch, and a PUT committing a new issuer_url in between would
        # otherwise be overwritten with the previous provider's key set
        # -- the broken pairing the lock exists to prevent.
        #
        # Asserted as an ordering rather than by trying to interleave
        # two requests, because the ordering is the property; a race
        # test would prove it only for the interleaving it happened to
        # produce.
        self.assertEqual(200, self._create().status_code)

        order = []

        class RecordingLock:
            def __enter__(inner):
                order.append('lock')
                return inner

            def __exit__(inner, *args):
                order.append('unlock')
                return False

        self.fetch.side_effect = lambda *a, **kw: (
            order.append('fetch') or document())

        with mock.patch.object(
                api_auth, '_issuer_url_lock', return_value=RecordingLock()):
            with mock.patch(
                    'shakenfist.mariadb.update_trusted_issuer_attributes',
                    side_effect=lambda *a, **kw: order.append('write')):
                self.assertEqual(200, self._discover().status_code)

        self.assertEqual(['fetch', 'lock', 'write', 'unlock'], order)

    def test_the_refresh_mask_never_names_the_identity_fields(self):
        # The assertion above sees the result; this one sees the write,
        # which is what would change if somebody widened the mask. A
        # refresh builds a whole TrustedIssuerAttributesData because the
        # model requires issuer_url and audience, and only the mask
        # keeps those two out of the UPDATE. Widen it by one name and
        # every refresh on the cluster would rewrite a field the
        # administrator did not ask it to touch.
        self.assertEqual(200, self._create().status_code)
        self.fetch.side_effect = None
        self.fetch.return_value = document()

        with mock.patch(
                'shakenfist.mariadb.update_trusted_issuer_attributes',
                side_effect=(
                    self.mock_mariadb._mariadb_update_trusted_issuer_attributes
                )) as update:
            self.assertEqual(200, self._discover().status_code)

        [call] = update.call_args_list
        self.assertNotIn('issuer_url', call.kwargs['fields'])
        self.assertNotIn('audience', call.kwargs['fields'])
        self.assertIn('jwks_uri', call.kwargs['fields'])
        # And what it did construct for the two it must not write is
        # the issuer's real configuration, so a widened mask writes the
        # truth rather than a blank.
        self.assertEqual(AUTHENTIK, call.args[0].issuer_url)
        self.assertEqual('https://sf.example.com', call.args[0].audience)

    def test_clearing_a_client_id_alone_leaves_the_scopes(self):
        # The scope default has no converse, which the operator guide
        # now says explicitly. Clearing client_id without also clearing
        # oidc_scopes leaves a scope list behind for a registration
        # that no longer exists. Nothing is disclosed and no flow
        # breaks, because client_id is the half that decides whether an
        # issuer is login capable -- this test is here so that the state
        # is a decision on the record rather than a surprise, and so
        # that a later change to it has to change a test.
        self.assertEqual(200, self._create(
            client_id='sf-cli', oidc_scopes='openid profile').status_code)

        resp = self._update(client_id='')
        self.assertEqual(200, resp.status_code)
        self.assertIsNone(resp.get_json()['client_id'])
        self.assertEqual('openid profile', resp.get_json()['oidc_scopes'])

        # The consequence that actually matters: no longer offered for
        # human login, whatever the scope list still says.
        self.assertEqual([], self.client.get('/auth/login').get_json())

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
        # And the rest of the configuration is untouched. issuer_url
        # matters most of the three: it is the value compared byte for
        # byte against a token's iss claim, so an issuer which lost it
        # would stop validating anything, and record_discovery() has to
        # construct it in order to write the fields next to it.
        self.assertEqual(AUTHENTIK, body['issuer_url'])
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

    def test_every_issuer_field_is_refused_at_its_real_column_width(self):
        # These three declare max_length=1024 on the model and are
        # stored in VARCHAR(255) columns, because a declared limit never
        # reaches the generated DDL. The validator used to take the
        # declared number, so a 300 character value was accepted here
        # and then refused by MariaDB -- a 500 -- or silently truncated
        # where strict mode is off. Nothing which ever worked is refused
        # by checking the real width: a longer value could not have been
        # stored in the first place.
        for name in ('issuer_url', 'jwks_uri', 'audience'):
            long_value = 'https://example.com/' + (
                'x' * (256 - len('https://example.com/')))
            self.assertEqual(256, len(long_value))
            resp = self._create(**{name: long_value})
            self.assertEqual(
                422, resp.status_code, f'{name} was not refused at 256')
            self.assertIn('255 characters', resp.get_json()['error'])

    def test_an_issuer_field_at_its_column_width_is_accepted(self):
        # The boundary from below, so the refusal above is not passing
        # by refusing everything.
        at_limit = 'https://example.com/' + (
            'x' * (255 - len('https://example.com/')))
        self.assertEqual(255, len(at_limit))
        self.assertEqual(200, self._create(audience=at_limit).status_code)

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
