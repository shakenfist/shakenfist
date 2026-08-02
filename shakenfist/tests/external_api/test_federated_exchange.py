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
               exp_delta=300, key=None, kid='key-1'):
        now = int(time.time())
        body = {
            'iss': issuer, 'aud': audience, 'iat': now,
            'exp': now + exp_delta, 'jti': 'jti-%d' % now,
            'sub': 'repo:shakenfist/ryll:ref:refs/heads/develop',
            'repository': 'shakenfist/ryll',
            'ref': 'refs/heads/develop'
        }
        if claims:
            body.update(claims)
        return jwt.encode(body, key or self.key, algorithm='RS256',
                          headers={'kid': kid})

    def _exchange(self, token=None, namespace='ci', rule='ryll'):
        body = {'namespace': namespace, 'rule': rule}
        if token is not False:
            body['token'] = token if token is not None else self._token()
        return self.client.post('/auth/federated', data=json.dumps(body))


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
        # rotates the secret out from under a still-running job.
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
