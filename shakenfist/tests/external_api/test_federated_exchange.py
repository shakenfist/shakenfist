"""The federated identity exchange.

This is the security boundary of the whole auth federation design: an
unauthenticated endpoint that hands out namespace keys. So the tests
are overwhelmingly about refusals, about the order those refusals
happen in, and about the exchange never granting more than the rule it
went through says.
"""

import io
import json
import logging
import sys
import time
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from shakenfist.external_api import app as external_api
from shakenfist.mapping_rule import MappingRule
from shakenfist.namespace_key import NamespaceKey
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.trusted_issuer import TrustedIssuer
from shakenfist.util import credentials
from shakenfist import exceptions
from shakenfist import federation
from shakenfist.config import config


GITHUB = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS = GITHUB + '/.well-known/jwks'
AUDIENCE = 'https://sf.example.com'


class FederatedExchangeTestCase(base.ShakenFistTestCase):
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
        self.mock_mariadb.create_namespace('ci', 'key1', 'bacon')

        self.key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048)
        self.keys = {'key-1': self.key}
        self.fetches = []
        self.jti_counter = 0

        federation.JWKS_CACHE = federation.JWKSCache()
        self.issuer = TrustedIssuer.new(
            'github', GITHUB, GITHUB_JWKS, AUDIENCE)
        self.rule = MappingRule.new(
            'ci', 'ryll', 'github',
            {'repository': 'shakenfist/ryll',
             'ref': ['refs/heads/develop', 'refs/heads/main']},
            ['blob.read', 'artifact.*'], 3600, 'ryll-ci')

        patcher = mock.patch(
            'jwt.jwks_client.urllib.request.urlopen',
            side_effect=self._urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.client = external_api.app.test_client()

    def _urlopen(self, request, **kwargs):
        self.fetches.append(request.full_url)
        body = json.dumps({
            'keys': [
                json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(
                    key.public_key())) | {
                        'kid': kid, 'use': 'sig', 'alg': 'RS256'}
                for kid, key in self.keys.items()
            ]
        }).encode('utf-8')
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(body)
        response.__exit__.return_value = False
        return response

    def _token(self, claims=None, audience=AUDIENCE, issuer=GITHUB,
               exp_delta=300, key=None, kid='key-1', jti=None):
        # A distinct jti per token by default, which is what a real
        # issuer does -- two runs of the same workflow are two separate
        # identities, not one presented twice. Tests which want to
        # exercise replay pass an explicit jti to say so, and jti=False
        # leaves the claim out altogether the way some issuers do.
        self.jti_counter += 1
        now = int(time.time())
        body = {
            'iss': issuer, 'aud': audience, 'iat': now,
            'exp': now + exp_delta,
            'sub': 'repo:shakenfist/ryll:ref:refs/heads/develop',
            'repository': 'shakenfist/ryll',
            'ref': 'refs/heads/develop'
        }
        if jti is not False:
            body['jti'] = jti or 'jti-%d-%d' % (now, self.jti_counter)
        if claims:
            body.update(claims)
        return jwt.encode(body, key or self.key, algorithm='RS256',
                          headers={'kid': kid})

    def _exchange(self, token=None, namespace='ci', rule='ryll'):
        body = {'namespace': namespace, 'rule': rule}
        if token is not False:
            body['token'] = token if token is not None else self._token()
        return self.client.post('/auth/federated', data=json.dumps(body))

    def _rate_limit(self, limit):
        patcher = mock.patch.object(
            config, 'FEDERATION_RATE_LIMIT_PER_MINUTE', limit)
        patcher.start()
        self.addCleanup(patcher.stop)


class SuccessfulExchangeTestCase(FederatedExchangeTestCase):
    def test_a_good_token_mints_a_key(self):
        resp = self._exchange()

        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual('ci', body['namespace'])
        self.assertTrue(body['key_name'].startswith('ryll-ci-'))
        self.assertTrue(credentials.looks_valid(body['key']))

    def test_the_endpoint_needs_no_credential(self):
        # The entire point: the caller has no Shaken Fist credential,
        # only an identity from somewhere we were told to believe.
        resp = self._exchange()
        self.assertEqual(200, resp.status_code)

    def test_the_minted_key_works_and_carries_the_rules_scopes(self):
        minted = self._exchange().get_json()

        auth = self.client.post('/auth', data=json.dumps(
            {'namespace': 'ci', 'key': minted['key']}))
        self.assertEqual(200, auth.status_code)

        key = NamespaceKey.from_db_by_name('ci', minted['key_name'])
        self.assertEqual(['blob.read', 'artifact.*'], key.scopes)

    def test_the_key_expires_with_the_rules_ttl(self):
        minted = self._exchange().get_json()
        key = NamespaceKey.from_db_by_name('ci', minted['key_name'])

        # 3600 from the rule, give or take the time the test took.
        self.assertGreater(key.expiry, time.time() + 3500)
        self.assertLess(key.expiry, time.time() + 3700)

    def test_provenance_records_the_grant_as_it_was_made(self):
        minted = self._exchange().get_json()
        key = NamespaceKey.from_db_by_name('ci', minted['key_name'])

        provenance = key.provenance
        self.assertEqual('federated', provenance['source'])
        self.assertEqual(str(self.rule.uuid), provenance['rule'])
        self.assertEqual('github', provenance['issuer'])
        # The claims satisfied, not the rule's matchers: an audit must
        # describe the grant as made, not as the rule reads later.
        self.assertEqual(
            {'repository': 'shakenfist/ryll', 'ref': 'refs/heads/develop'},
            provenance['claims'])

    def test_a_rerun_gets_its_own_key_rather_than_rotating(self):
        # A discriminator on the name, so a workflow re-run never
        # rotates the secret out from under a still-running job. A
        # re-run presents a fresh token, which is why this is not
        # refused as a replay.
        first = self._exchange().get_json()
        second = self._exchange().get_json()

        self.assertNotEqual(first['key_name'], second['key_name'])
        self.assertNotEqual(first['key'], second['key'])

        for minted in (first, second):
            auth = self.client.post('/auth', data=json.dumps(
                {'namespace': 'ci', 'key': minted['key']}))
            self.assertEqual(200, auth.status_code)

    def test_an_enumerated_claim_alternative_is_accepted(self):
        resp = self._exchange(
            token=self._token(claims={'ref': 'refs/heads/main'}))
        self.assertEqual(200, resp.status_code)


class RefusedExchangeTestCase(FederatedExchangeTestCase):
    def test_an_untrusted_issuer_is_refused_without_a_fetch(self):
        # The ordering property: a made-up issuer must not be able to
        # make us dial out, or an anonymous caller can tie up workers.
        resp = self._exchange(
            token=self._token(issuer='https://evil.example.com'))

        self.assertEqual(401, resp.status_code)
        self.assertEqual([], self.fetches)

    def test_a_token_signed_by_the_wrong_key_is_refused(self):
        other = rsa.generate_private_key(
            public_exponent=65537, key_size=2048)
        resp = self._exchange(token=self._token(key=other))
        self.assertEqual(401, resp.status_code)

    def test_an_expired_token_is_refused(self):
        self.assertEqual(
            401, self._exchange(token=self._token(exp_delta=-1)).status_code)

    def test_a_token_for_another_audience_is_refused(self):
        self.assertEqual(401, self._exchange(
            token=self._token(audience='https://elsewhere')).status_code)

    def test_the_wrong_repository_is_refused(self):
        resp = self._exchange(
            token=self._token(claims={'repository': 'shakenfist/evil'}))
        self.assertEqual(401, resp.status_code)

    def test_the_wrong_branch_is_refused(self):
        resp = self._exchange(
            token=self._token(claims={'ref': 'refs/heads/wip'}))
        self.assertEqual(401, resp.status_code)

    def test_no_globbing_in_claim_matching(self):
        # shakenfist/* must not match, or registering shakenfist-evil
        # becomes an attack on this endpoint.
        resp = self._exchange(
            token=self._token(claims={'repository': 'shakenfist/ryll-evil'}))
        self.assertEqual(401, resp.status_code)

    def test_an_unknown_rule_is_refused(self):
        self.assertEqual(401, self._exchange(rule='nope').status_code)

    def test_an_unknown_namespace_is_refused(self):
        self.assertEqual(401, self._exchange(namespace='nope').status_code)

    def test_a_deleted_rule_stops_minting_immediately(self):
        self.assertEqual(200, self._exchange().status_code)
        self.rule.delete()
        self.assertEqual(401, self._exchange().status_code)

    def test_a_deleted_issuer_stops_the_exchange_immediately(self):
        self.assertEqual(200, self._exchange().status_code)
        self.issuer.delete()
        self.assertEqual(401, self._exchange().status_code)

    def test_a_rule_bound_to_another_issuer_refuses_the_token(self):
        TrustedIssuer.new('authentik', 'https://auth.example.com',
                          'https://auth.example.com/jwks', AUDIENCE)
        MappingRule.new(
            'ci', 'via-authentik', 'authentik',
            {'repository': 'shakenfist/ryll'}, ['blob.read'], 3600, 'x')

        self.assertEqual(
            401, self._exchange(rule='via-authentik').status_code)

    def test_missing_fields_are_refused(self):
        self.assertEqual(400, self._exchange(token=False).status_code)
        self.assertEqual(
            400, self.client.post('/auth/federated', data=json.dumps(
                {'token': self._token(), 'rule': 'ryll'})).status_code)
        self.assertEqual(
            400, self.client.post('/auth/federated', data=json.dumps(
                {'token': self._token(), 'namespace': 'ci'})).status_code)

    def test_garbage_is_refused_rather_than_raising(self):
        for junk in ['', 'not-a-token', 'a.b.c', '.' * 100]:
            resp = self._exchange(token=junk)
            self.assertIn(resp.status_code, (400, 401),
                          f'{junk!r} produced {resp.status_code}')

    def test_a_refusal_does_not_say_which_claim_missed(self):
        # Otherwise the endpoint is an oracle for guessing a rule's
        # contents, one anonymous request at a time.
        resp = self._exchange(
            token=self._token(claims={'ref': 'refs/heads/wip'}))
        body = resp.get_data(as_text=True)

        self.assertNotIn('refs/heads/develop', body)
        self.assertNotIn('refs/heads/wip', body)
        self.assertNotIn('repository', body)

    def test_a_refused_exchange_mints_nothing(self):
        before = len(self.mock_mariadb.namespace_key_objects)
        self._exchange(token=self._token(claims={'ref': 'refs/heads/wip'}))
        self.assertEqual(before, len(self.mock_mariadb.namespace_key_objects))


class ExchangeAuditTestCase(FederatedExchangeTestCase):
    def test_a_claim_failure_is_audited_against_the_rule(self):
        # A stream of near-miss claim failures is what probing looks
        # like, and the rule's owner is who needs to see it.
        with mock.patch.object(MappingRule, 'add_event') as add_event:
            self._exchange(
                token=self._token(claims={'ref': 'refs/heads/wip'}))

        messages = [c.args[1] for c in add_event.call_args_list
                    if len(c.args) > 1]
        self.assertIn('federated exchange refused', messages)

    def test_an_unidentifiable_failure_is_not_evented(self):
        # No owner can be charged for an unknown rule, and eventing it
        # would let an anonymous caller write unbounded audit rows.
        with mock.patch.object(MappingRule, 'add_event') as add_event:
            self._exchange(rule='nope')
        self.assertEqual([], add_event.call_args_list)

    def test_an_untrusted_issuer_is_not_evented(self):
        with mock.patch.object(MappingRule, 'add_event') as add_event:
            self._exchange(token=self._token(issuer='https://evil.example'))
        self.assertEqual([], add_event.call_args_list)

    def test_a_successful_exchange_is_audited(self):
        with mock.patch.object(NamespaceKey, 'add_event') as add_event:
            self._exchange()

        messages = [c.args[1] for c in add_event.call_args_list
                    if len(c.args) > 1]
        self.assertIn('key minted by federated exchange', messages)

    def test_the_route_is_redacted_from_request_and_response_logging(self):
        # The response body carries the minted secret, and app.py logs
        # response bodies to the audit event stream. Redaction is by
        # path prefix, so this pins that /auth/federated is inside it.
        with external_api.app.test_request_context('/auth/federated'):
            self.assertTrue(external_api._handles_credentials())

    def test_an_oversized_body_is_refused(self):
        # Refused on size before anything parses it, because parsing an
        # attacker-sized JWT is work done on their behalf.
        resp = self.client.post('/auth/federated', data=json.dumps({
            'token': 'a' * (config.FEDERATION_MAX_TOKEN_BYTES + 1),
            'namespace': 'ci', 'rule': 'ryll'}))
        self.assertEqual(413, resp.status_code)

    def test_a_real_sized_token_is_comfortably_under_the_limit(self):
        # The limit must not be tight enough to refuse genuine tokens.
        self.assertLess(len(self._token()) * 2,
                        config.FEDERATION_MAX_TOKEN_BYTES)

    def test_the_secret_never_appears_in_an_event(self):
        recorded = []

        def _capture(self, eventtype, message, duration=None, extra=None,
                     **kwargs):
            recorded.append((message, extra))

        with mock.patch.object(NamespaceKey, 'add_event', _capture):
            minted = self._exchange().get_json()

        for message, extra in recorded:
            self.assertNotIn(minted['key'], json.dumps(extra or {}))
            self.assertNotIn(minted['key'], message)


class ReplayRefusalTestCase(FederatedExchangeTestCase):
    """One token, one rule, one key.

    Refusal is per (token, rule) rather than per token, because
    exchanging one identity against two rules to reach two namespaces
    is a pattern the CI conductor design depends on, while
    re-exchanging it against the same rule is not.
    """

    def test_the_same_token_twice_through_one_rule_is_refused(self):
        token = self._token()

        self.assertEqual(200, self._exchange(token=token).status_code)
        self.assertEqual(401, self._exchange(token=token).status_code)

    def test_a_replay_mints_nothing(self):
        token = self._token()
        self._exchange(token=token)

        before = len(self.mock_mariadb.namespace_key_objects)
        self._exchange(token=token)
        self.assertEqual(
            before, len(self.mock_mariadb.namespace_key_objects))

    def test_the_same_token_through_a_second_rule_is_allowed(self):
        MappingRule.new(
            'system', 'ryll-admin', 'github',
            {'repository': 'shakenfist/ryll'}, ['node.read'], 3600, 'ryll')
        token = self._token()

        self.assertEqual(200, self._exchange(token=token).status_code)
        self.assertEqual(200, self._exchange(
            token=token, namespace='system',
            rule='ryll-admin').status_code)

    def test_a_fresh_token_after_a_replay_still_works(self):
        token = self._token()
        self._exchange(token=token)
        self.assertEqual(401, self._exchange(token=token).status_code)

        self.assertEqual(200, self._exchange().status_code)

    def test_a_token_with_no_jti_is_still_protected(self):
        # Not every issuer stamps a jti, and letting those through
        # unprotected would leave open exactly the hole this closes.
        # The signature identifies the token just as well.
        token = self._token(jti=False)

        self.assertEqual(200, self._exchange(token=token).status_code)
        self.assertEqual(401, self._exchange(token=token).status_code)

    def test_two_no_jti_tokens_are_told_apart(self):
        # The fallback must identify the token, not the shape of it.
        first = self._token(jti=False)
        second = self._token(jti=False, claims={'ref': 'refs/heads/main'})

        self.assertEqual(200, self._exchange(token=first).status_code)
        self.assertEqual(200, self._exchange(token=second).status_code)

    def test_the_record_expires_with_the_token(self):
        token = self._token(exp_delta=300)
        self._exchange(token=token)

        [expires_at] = self.mock_mariadb.federation_replay.values()
        self.assertGreater(expires_at, time.time() + 200)
        self.assertLess(expires_at, time.time() + 400)

    def test_a_claim_failure_does_not_burn_the_tokens_one_use(self):
        # The replay claim is the last gate before minting precisely so
        # that a refusal for some other reason leaves the token usable.
        # An operator who fixes their rule and retries with a token
        # that is still valid should succeed.
        token = self._token(claims={'ref': 'refs/heads/wip'})
        self.assertEqual(401, self._exchange(token=token).status_code)

        self.rule.update(
            'github',
            {'repository': 'shakenfist/ryll', 'ref': 'refs/heads/wip'},
            ['blob.read'], 3600, 'ryll-ci')
        self.assertEqual(200, self._exchange(token=token).status_code)

    def test_a_replay_is_audited_against_the_rule(self):
        token = self._token()
        self._exchange(token=token)

        with mock.patch.object(MappingRule, 'add_event') as add_event:
            self._exchange(token=token)

        messages = [c.args[1] for c in add_event.call_args_list
                    if len(c.args) > 1]
        self.assertIn('federated exchange refused', messages)

    def test_an_unreadable_replay_table_refuses_rather_than_mints(self):
        # Both answers this check can give authorise something, so an
        # outage must not be allowed to look like either one.
        before = len(self.mock_mariadb.namespace_key_objects)
        with mock.patch('shakenfist.mariadb.record_federated_exchange',
                        side_effect=exceptions.DatabaseUnavailable('down')):
            resp = self._exchange()

        self.assertEqual(503, resp.status_code)
        self.assertEqual(
            before, len(self.mock_mariadb.namespace_key_objects))


class RateLimitTestCase(FederatedExchangeTestCase):
    def test_the_limit_trips_and_then_recovers(self):
        self._rate_limit(3)

        for _ in range(3):
            self.assertEqual(200, self._exchange().status_code)
        self.assertEqual(429, self._exchange().status_code)

        # A new window is a clean slate. Rather than sleep a minute,
        # move the recorded window into the past the way time passing
        # would.
        self.mock_mariadb.federation_rate_limits.clear()
        self.assertEqual(200, self._exchange().status_code)

    def test_the_limit_counts_unverifiable_tokens_too(self):
        # Verification is the expensive part, so a limit that only
        # applied to well formed requests would not limit anything.
        self._rate_limit(2)

        other = rsa.generate_private_key(
            public_exponent=65537, key_size=2048)
        for _ in range(2):
            self.assertEqual(
                401, self._exchange(token=self._token(key=other)).status_code)

        self.assertEqual(429, self._exchange().status_code)

    def test_an_untrusted_issuer_costs_no_counter_row(self):
        # Refused before the counter, so a flood of garbage from one
        # source cannot fill the table.
        self._rate_limit(10)
        self._exchange(token=self._token(issuer='https://evil.example.com'))

        self.assertEqual({}, self.mock_mariadb.federation_rate_limits)

    def test_zero_disables_rate_limiting(self):
        self._rate_limit(0)

        for _ in range(5):
            self.assertEqual(200, self._exchange().status_code)
        self.assertEqual({}, self.mock_mariadb.federation_rate_limits)

    def test_the_counter_is_keyed_on_the_source_address(self):
        self._rate_limit(10)
        self._exchange()

        [(source, window)] = self.mock_mariadb.federation_rate_limits
        self.assertEqual('127.0.0.1', source)
        self.assertEqual(0, window % federation.RATE_LIMIT_WINDOW_SECONDS)

    def test_a_rate_limited_caller_is_told_nothing_useful(self):
        self._rate_limit(1)
        self._exchange()
        resp = self._exchange()

        body = resp.get_data(as_text=True)
        self.assertEqual(429, resp.status_code)
        self.assertNotIn('ryll', body)
        self.assertNotIn('github', body)

    def test_an_unwritable_counter_refuses_rather_than_mints(self):
        self._rate_limit(10)

        before = len(self.mock_mariadb.namespace_key_objects)
        with mock.patch('shakenfist.mariadb.count_federated_attempt',
                        side_effect=exceptions.DatabaseUnavailable('down')):
            resp = self._exchange()

        self.assertEqual(503, resp.status_code)
        self.assertEqual(
            before, len(self.mock_mariadb.namespace_key_objects))

    def test_rate_limiting_precedes_the_jwks_fetch(self):
        # The point of the ordering: a limited caller must not still be
        # able to make us dial out on every attempt.
        #
        # The second token carries an unknown key id, which is what
        # makes this test say anything. A token reusing key-1 would hit
        # PyJWKClient's cache and fetch nothing even with no rate limit
        # at all, so the assertion would hold for the wrong reason.
        self._rate_limit(1)
        self._exchange()

        before = len(self.fetches)
        self.assertEqual(429, self._exchange(
            token=self._token(kid='key-2')).status_code)
        self.assertEqual(before, len(self.fetches))


class SecondIssuerTestCase(FederatedExchangeTestCase):
    """The same machinery, a completely different identity provider.

    The plan's proof obligation: an Authentik-style token, whose claims
    look nothing like GitHub's, must be exchangeable with configuration
    alone. If any of this needed a code path of its own then the
    design is not federation, it is a GitHub integration with extra
    steps.
    """

    AUTHENTIK = 'https://auth.example.com'

    def setUp(self):
        super().setUp()
        self.authentik = TrustedIssuer.new(
            'authentik', self.AUTHENTIK, self.AUTHENTIK + '/jwks', AUDIENCE)
        self.authentik_rule = MappingRule.new(
            'ci', 'via-groups', 'authentik', {'groups': ['sf-ci', 'sf-ops']},
            ['blob.read'], 900, 'ak')

    def _authentik_token(self, claims=None):
        # No repository, no ref, a different subject shape, and a
        # group membership instead -- nothing GitHub about it.
        return self._token(
            issuer=self.AUTHENTIK,
            claims={'groups': 'sf-ci', 'sub': 'service-account-ci',
                    'preferred_username': 'ci',
                    **(claims or {})})

    def test_an_authentik_token_mints_a_key(self):
        resp = self.client.post('/auth/federated', data=json.dumps({
            'token': self._authentik_token(), 'namespace': 'ci',
            'rule': 'via-groups'}))

        self.assertEqual(200, resp.status_code)
        self.assertTrue(resp.get_json()['key_name'].startswith('ak-'))

    def test_it_carries_the_rules_scopes_and_ttl(self):
        minted = self.client.post('/auth/federated', data=json.dumps({
            'token': self._authentik_token(), 'namespace': 'ci',
            'rule': 'via-groups'})).get_json()

        key = NamespaceKey.from_db_by_name('ci', minted['key_name'])
        self.assertEqual(['blob.read'], key.scopes)
        self.assertGreater(key.expiry, time.time() + 800)
        self.assertLess(key.expiry, time.time() + 1000)

    def test_the_provenance_names_the_second_issuer(self):
        minted = self.client.post('/auth/federated', data=json.dumps({
            'token': self._authentik_token(), 'namespace': 'ci',
            'rule': 'via-groups'})).get_json()

        key = NamespaceKey.from_db_by_name('ci', minted['key_name'])
        self.assertEqual('authentik', key.provenance['issuer'])
        self.assertEqual({'groups': 'sf-ci'}, key.provenance['claims'])

    def test_the_other_enumerated_group_also_matches(self):
        resp = self.client.post('/auth/federated', data=json.dumps({
            'token': self._authentik_token(claims={'groups': 'sf-ops'}),
            'namespace': 'ci', 'rule': 'via-groups'}))
        self.assertEqual(200, resp.status_code)

    def test_a_group_the_rule_does_not_bind_is_refused(self):
        resp = self.client.post('/auth/federated', data=json.dumps({
            'token': self._authentik_token(claims={'groups': 'everyone'}),
            'namespace': 'ci', 'rule': 'via-groups'}))
        self.assertEqual(401, resp.status_code)

    def test_the_two_issuers_do_not_bleed_into_each_other(self):
        # A GitHub token must not pass through the Authentik rule even
        # though its claims would satisfy nothing there, and an
        # Authentik token must not pass through the GitHub rule. The
        # issuer is checked against the rule's, not just against the
        # configured set.
        resp = self.client.post('/auth/federated', data=json.dumps({
            'token': self._token(), 'namespace': 'ci',
            'rule': 'via-groups'}))
        self.assertEqual(401, resp.status_code)

        resp = self.client.post('/auth/federated', data=json.dumps({
            'token': self._authentik_token(), 'namespace': 'ci',
            'rule': 'ryll'}))
        self.assertEqual(401, resp.status_code)

    def test_each_issuer_is_fetched_from_its_own_jwks_uri(self):
        # Both issuers are served by the same mock here, so what this
        # pins is that the uri came from the TrustedIssuer rather than
        # from the token -- a token naming its own key source would be
        # a token vouching for itself.
        self.client.post('/auth/federated', data=json.dumps({
            'token': self._authentik_token(), 'namespace': 'ci',
            'rule': 'via-groups'}))

        self.assertIn(self.AUTHENTIK + '/jwks', self.fetches)
        self.assertNotIn(GITHUB_JWKS, self.fetches)
