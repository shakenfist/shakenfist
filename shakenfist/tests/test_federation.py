"""Identity token validation.

This is the module that decides whether to believe a token, so the
tests are mostly about refusals. Everything runs against locally
generated RSA keys and a mocked JWKS fetch -- no network, and no
dependency on a real identity provider being reachable or unchanged.
"""

import base64
import hashlib
import hmac
import io
import json
import string
import threading
import time
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from shakenfist import exceptions
from shakenfist import federation
from shakenfist.mapping_rule import MappingRule
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.trusted_issuer import TrustedIssuer


GITHUB = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS = GITHUB + '/.well-known/jwks'
AUDIENCE = 'https://sf.example.com'


def _keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks_for(keys):
    """A JWKS document for {kid: private_key}."""
    return {
        'keys': [
            json.loads(
                jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
            ) | {'kid': kid, 'use': 'sig', 'alg': 'RS256'}
            for kid, key in keys.items()
        ]
    }


class FederationTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        self.key = _keypair()
        self.keys = {'key-1': self.key}
        self.fetches = []
        self.fetch_delay = 0

        # Every test starts with an empty client cache, or a client
        # built by an earlier test would answer with its stale keys.
        federation.JWKS_CACHE = federation.JWKSCache()

        self.issuer = TrustedIssuer.new(
            'github', GITHUB, GITHUB_JWKS, AUDIENCE)

        # Patched at the socket rather than at fetch_data, because
        # fetch_data is what populates PyJWKClient's key set cache.
        # Mocking it out would silently disable the caching these tests
        # exist to check, and they would pass for the wrong reason.
        patcher = mock.patch(
            'jwt.jwks_client.urllib.request.urlopen',
            side_effect=self._urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _urlopen(self, request, **kwargs):
        self.fetches.append(request.full_url)
        # A real JWKS fetch is a network round trip. Without some
        # duration here the threads in the stampede test finish one at
        # a time before the next is scheduled, and the test passes
        # whether or not the single-flight lock exists -- which makes
        # it worse than no test.
        time.sleep(self.fetch_delay)
        body = json.dumps(_jwks_for(self.keys)).encode('utf-8')
        response = mock.MagicMock()
        response.read.return_value = body
        response.__enter__.return_value = io.BytesIO(body)
        response.__exit__.return_value = False
        return response

    def _token(self, kid='key-1', key=None, claims=None, audience=AUDIENCE,
               issuer=GITHUB, exp_delta=300, nbf_delta=None, iat_delta=0):
        now = int(time.time())
        body = {
            'iss': issuer,
            'aud': audience,
            # iat_delta models an identity provider whose clock is
            # ahead of ours, which is the common direction for skew.
            'iat': now + iat_delta,
            'exp': now + exp_delta,
            'sub': 'repo:shakenfist/ryll:ref:refs/heads/develop',
            'repository': 'shakenfist/ryll',
            'ref': 'refs/heads/develop'
        }
        if nbf_delta is not None:
            body['nbf'] = now + nbf_delta
        if claims:
            body.update(claims)
        return jwt.encode(
            body, key or self.key, algorithm='RS256',
            headers={'kid': kid})


class IssuerResolutionTestCase(FederationTestCase):
    def test_the_issuer_is_read_from_an_unverified_token(self):
        self.assertEqual(GITHUB, federation.unverified_issuer(self._token()))

    def test_garbage_has_no_readable_issuer(self):
        self.assertIsNone(federation.unverified_issuer('not-a-token'))
        self.assertIsNone(federation.unverified_issuer(''))

    def test_resolution_finds_the_configured_issuer(self):
        found = federation.issuer_for_token(self._token())
        self.assertEqual(self.issuer.uuid, found.uuid)

    def test_an_unknown_issuer_is_refused_without_a_fetch(self):
        # The cheap rejection. If this ever reaches the network, an
        # unauthenticated caller with a made-up iss can tie up a worker
        # on an outbound request.
        self.assertRaises(
            exceptions.UntrustedIssuer, federation.issuer_for_token,
            self._token(issuer='https://evil.example.com'))
        self.assertEqual([], self.fetches)

    def test_issuer_matching_is_exact(self):
        # No trailing slash tolerance: a loose comparison here is a way
        # to accept tokens from somewhere else entirely.
        self.assertRaises(
            exceptions.UntrustedIssuer, federation.issuer_for_token,
            self._token(issuer=GITHUB + '/'))

    def test_a_deleted_issuer_is_no_longer_trusted(self):
        self.issuer.delete()
        self.assertRaises(
            exceptions.UntrustedIssuer, federation.issuer_for_token,
            self._token())


class TokenValidationTestCase(FederationTestCase):
    def test_a_good_token_validates(self):
        claims = federation.validate_token(self._token(), self.issuer)

        self.assertEqual(GITHUB, claims['iss'])
        self.assertEqual('shakenfist/ryll', claims['repository'])

    def test_a_token_signed_by_the_wrong_key_is_refused(self):
        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            self._token(key=_keypair()), self.issuer)

    def test_a_token_for_another_audience_is_refused(self):
        # Otherwise a token minted for some other relying party can be
        # replayed at us.
        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            self._token(audience='https://someone-else.example.com'),
            self.issuer)

    def test_an_expired_token_is_refused(self):
        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            self._token(exp_delta=-1), self.issuer)

    def test_a_token_not_yet_valid_is_refused(self):
        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            self._token(nbf_delta=300), self.issuer)

    def test_an_issuer_whose_clock_runs_fast_still_works(self):
        # The skew that matters is between the identity provider, which
        # stamps iat, and the API node checking it. PyJWT treats iat as
        # "not valid before", so with zero leeway a provider running a
        # few seconds fast would have every token it issues refused the
        # moment it is issued -- which is why iat is not verified.
        claims = federation.validate_token(
            self._token(iat_delta=30), self.issuer)
        self.assertEqual(GITHUB, claims['iss'])

    def test_expiry_is_still_strict_for_a_fast_issuer(self):
        # Not verifying iat must not soften exp, which is the claim
        # that actually bounds the token.
        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            self._token(iat_delta=30, exp_delta=-1), self.issuer)

    def test_a_deliberately_postdated_token_is_still_refused(self):
        # nbf is how an issuer says "not before", and it stays enforced
        # even though iat no longer is.
        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            self._token(iat_delta=30, nbf_delta=300), self.issuer)

    def test_a_token_with_no_expiry_is_refused(self):
        # A token that never expires is a permanent grant wearing a
        # credential's clothes.
        now = int(time.time())
        token = jwt.encode(
            {'iss': GITHUB, 'aud': AUDIENCE, 'iat': now,
             'repository': 'shakenfist/ryll'},
            self.key, algorithm='RS256', headers={'kid': 'key-1'})
        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            token, self.issuer)

    def test_an_unsigned_token_is_refused(self):
        now = int(time.time())
        token = jwt.encode(
            {'iss': GITHUB, 'aud': AUDIENCE, 'exp': now + 300},
            key='', algorithm='none')
        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            token, self.issuer)

    def test_an_hmac_token_signed_with_the_public_key_is_refused(self):
        # The classic JWT attack. The issuer's public key is, by
        # definition, public; if HS256 were accepted then anybody could
        # take that key, use it as an HMAC secret, and mint a token we
        # would verify happily. ALLOWED_ALGORITHMS being asymmetric only
        # is what stops it.
        #
        # Hand-rolled because PyJWT refuses to *encode* this -- which is
        # a good protection, but it protects the signer, and the signer
        # here is the attacker. Only the verify side matters.
        pub_pem = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)

        def _segment(obj):
            return base64.urlsafe_b64encode(
                json.dumps(obj).encode('utf-8')).rstrip(b'=')

        now = int(time.time())
        signing_input = b'.'.join([
            _segment({'alg': 'HS256', 'typ': 'JWT', 'kid': 'key-1'}),
            _segment({'iss': GITHUB, 'aud': AUDIENCE, 'exp': now + 300,
                      'repository': 'shakenfist/evil'})
        ])
        signature = base64.urlsafe_b64encode(
            hmac.new(pub_pem, signing_input, hashlib.sha256).digest()
        ).rstrip(b'=')
        token = (signing_input + b'.' + signature).decode('utf-8')

        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            token, self.issuer)

    def test_hmac_is_not_an_allowed_algorithm(self):
        for algorithm in federation.ALLOWED_ALGORITHMS:
            self.assertFalse(
                algorithm.startswith('HS'),
                'a symmetric algorithm would let the public key sign')
        self.assertNotIn('none', federation.ALLOWED_ALGORITHMS)


class JWKSCachingTestCase(FederationTestCase):
    def test_the_fetch_timeout_is_short_enough_to_free_a_worker(self):
        # The fetch happens while holding the issuer's refetch lock, so
        # PyJWT's thirty second default would let an unreachable
        # provider pin an API worker for thirty seconds per queued
        # request. That is a denial of service arranged by anyone able
        # to interfere with the provider's network path.
        federation.validate_token(self._token(), self.issuer)
        client, _ = federation.JWKS_CACHE._client_and_lock(
            str(self.issuer.uuid), self.issuer.jwks_uri)

        self.assertEqual(
            federation.config.FEDERATION_JWKS_FETCH_TIMEOUT_SECONDS,
            client.timeout)
        self.assertLessEqual(client.timeout, 10)

    def test_the_key_set_is_fetched_once_and_then_cached(self):
        for _ in range(5):
            federation.validate_token(self._token(), self.issuer)
        self.assertEqual(1, len(self.fetches))

    def test_an_unknown_kid_triggers_exactly_one_refetch(self):
        federation.validate_token(self._token(), self.issuer)
        self.assertEqual(1, len(self.fetches))

        # The issuer rotates: a new key, and a token signed with it.
        rotated = _keypair()
        self.keys['key-2'] = rotated

        claims = federation.validate_token(
            self._token(kid='key-2', key=rotated), self.issuer)
        self.assertEqual(GITHUB, claims['iss'])
        self.assertEqual(2, len(self.fetches))

    def test_a_kid_that_never_appears_does_not_fetch_forever(self):
        federation.validate_token(self._token(), self.issuer)
        before = len(self.fetches)

        self.assertRaises(
            exceptions.TokenValidationFailed, federation.validate_token,
            self._token(kid='nope', key=_keypair()), self.issuer)

        # One refetch to check whether the key is newly published, and
        # then it gives up rather than retrying per request.
        self.assertEqual(before + 1, len(self.fetches))

    def test_concurrent_unknown_kids_collapse_into_one_fetch(self):
        # The stampede this cache exists to prevent: fifty CI jobs
        # presenting tokens signed with a freshly rotated key must not
        # become fifty requests to the identity provider.
        federation.validate_token(self._token(), self.issuer)
        self.fetches.clear()

        rotated = _keypair()
        self.keys['key-2'] = rotated
        tokens = [self._token(kid='key-2', key=rotated) for _ in range(20)]
        # Wide enough that every thread is inside the refetch window at
        # once, so an unlocked implementation genuinely stampedes.
        self.fetch_delay = 0.1

        barrier = threading.Barrier(len(tokens))
        errors = []

        def _validate(token):
            try:
                barrier.wait(timeout=10)
                federation.validate_token(token, self.issuer)
            except Exception as e:      # noqa: BLE001 - reported below
                errors.append(e)

        threads = [threading.Thread(target=_validate, args=(t,))
                   for t in tokens]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual([], errors)
        self.assertEqual(
            1, len(self.fetches),
            'concurrent unknown-kid lookups stampeded the issuer')

    def test_repointing_an_issuers_jwks_uri_replaces_the_client(self):
        federation.validate_token(self._token(), self.issuer)
        self.assertEqual(1, len(self.fetches))

        # The operator repoints the issuer. The old client's cached
        # keys are no longer the right answer.
        self.issuer.update(GITHUB, GITHUB_JWKS + '2', AUDIENCE)
        federation.validate_token(self._token(), self.issuer)
        self.assertEqual(2, len(self.fetches))


class ClaimMatchingTestCase(base.ShakenFistTestCase):
    def test_an_exact_matcher_compares_exactly(self):
        self.assertTrue(federation.claim_matches('a', 'a'))
        self.assertFalse(federation.claim_matches('a', 'b'))
        self.assertFalse(federation.claim_matches('ab', 'a'))
        self.assertFalse(federation.claim_matches('a', 'ab'))

    def test_an_enumerated_matcher_accepts_any_alternative(self):
        self.assertTrue(federation.claim_matches('b', ['a', 'b']))
        self.assertFalse(federation.claim_matches('c', ['a', 'b']))

    def test_there_is_no_globbing(self):
        # The property the whole design rests on. shakenfist/* must not
        # match, or registering shakenfist-evil becomes an attack.
        self.assertFalse(
            federation.claim_matches('shakenfist/evil', 'shakenfist/*'))
        self.assertFalse(
            federation.claim_matches('shakenfist/ryll', 'shakenfist/'))

    def test_non_string_claim_values_never_match(self):
        # A claim of 1 must not satisfy a matcher of "1": a federation
        # that coerces is one that eventually accepts the wrong thing.
        self.assertFalse(federation.claim_matches(1, '1'))
        self.assertFalse(federation.claim_matches(True, 'true'))
        self.assertFalse(federation.claim_matches(None, 'None'))
        self.assertFalse(federation.claim_matches(['a'], 'a'))

    def test_every_bound_claim_must_be_present(self):
        self.assertRaises(
            exceptions.ClaimMismatch, federation.match_claims,
            {'repository': 'shakenfist/ryll'},
            {'repository': 'shakenfist/ryll', 'ref': 'refs/heads/develop'})

    def test_matching_returns_the_satisfied_claims(self):
        satisfied = federation.match_claims(
            {'repository': 'shakenfist/ryll', 'ref': 'refs/heads/develop',
             'sub': 'ignored'},
            {'repository': 'shakenfist/ryll',
             'ref': ['refs/heads/develop', 'refs/heads/main']})

        self.assertEqual(
            {'repository': 'shakenfist/ryll', 'ref': 'refs/heads/develop'},
            satisfied)

    def test_an_empty_matcher_set_refuses_rather_than_matching(self):
        # Rule creation refuses to write one, so reaching here means a
        # damaged row -- and the safe reading of a damaged rule is that
        # it grants nothing.
        self.assertRaises(
            exceptions.ClaimMismatch, federation.match_claims,
            {'repository': 'shakenfist/ryll'}, {})


class TokenIdentityTestCase(FederationTestCase):
    """The value replay refusal is keyed on."""

    def test_the_jti_is_used_when_the_issuer_provides_one(self):
        token = self._token(claims={'jti': 'abc-123'})
        self.assertEqual(
            'abc-123',
            federation.token_identity(token, {'jti': 'abc-123'}))

    def test_a_token_with_no_jti_falls_back_to_a_hash(self):
        token = self._token()
        identity = federation.token_identity(token, {})

        self.assertTrue(identity.startswith('sha256:'))
        self.assertEqual(71, len(identity))

    def test_re_encoding_the_signature_does_not_change_the_identity(self):
        # base64url leaves four don't-care bits in the final character of
        # a 256 byte signature, and the padding is optional, so one
        # signature has many spellings which all verify. If the identity
        # were derived from the signature text an attacker could replay a
        # token as many times as it has spellings, so it is derived from
        # the signed material instead.
        token = self._token()
        head, signature = token.rsplit('.', 1)
        identities = set()
        variants = 0

        for char in string.ascii_letters + string.digits + '-_':
            for padding in ('', '=', '=='):
                candidate = head + '.' + signature[:-1] + char + padding
                try:
                    jwt.decode(
                        candidate, self.key.public_key(),
                        algorithms=['RS256'], audience=AUDIENCE,
                        issuer=GITHUB)
                except Exception:
                    continue

                variants += 1
                identities.add(federation.token_identity(candidate, {}))

        # If only the original spelling verified the test would prove
        # nothing at all.
        self.assertGreater(variants, 1)
        self.assertEqual(1, len(identities))

    def test_the_fallback_is_stable_for_one_token(self):
        token = self._token()
        self.assertEqual(federation.token_identity(token, {}),
                         federation.token_identity(token, {}))

    def test_the_fallback_differs_between_tokens(self):
        first = self._token(claims={'ref': 'refs/heads/develop'})
        second = self._token(claims={'ref': 'refs/heads/main'})

        self.assertNotEqual(federation.token_identity(first, {}),
                            federation.token_identity(second, {}))

    def test_the_fallback_does_not_contain_the_signature(self):
        # The signature is the secret half of the credential and has no
        # business sitting in a table, which is why it is hashed.
        token = self._token()
        signature = token.rsplit('.', 1)[-1]

        self.assertNotIn(signature, federation.token_identity(token, {}))

    def test_an_absurdly_long_jti_falls_back_rather_than_truncating(self):
        # A truncated identity would make two different tokens look
        # like the same one, which is a false replay refusal.
        token = self._token()
        identity = federation.token_identity(token, {'jti': 'x' * 4096})

        self.assertTrue(identity.startswith('sha256:'))

    def test_a_non_string_jti_falls_back(self):
        token = self._token()
        for junk in (12345, ['a'], {'a': 1}, None, ''):
            self.assertTrue(
                federation.token_identity(token, {'jti': junk}).startswith(
                    'sha256:'),
                f'{junk!r} was not rejected as a jti')


class RateLimitTestCase(FederationTestCase):
    def test_under_the_limit_is_allowed(self):
        with mock.patch.object(
                federation.config, 'FEDERATION_RATE_LIMIT_PER_MINUTE', 5):
            for _ in range(5):
                federation.enforce_rate_limit('10.0.0.1')

    def test_over_the_limit_raises(self):
        with mock.patch.object(
                federation.config, 'FEDERATION_RATE_LIMIT_PER_MINUTE', 2):
            federation.enforce_rate_limit('10.0.0.1')
            federation.enforce_rate_limit('10.0.0.1')
            self.assertRaises(
                exceptions.RateLimited,
                federation.enforce_rate_limit, '10.0.0.1')

    def test_sources_are_counted_separately(self):
        with mock.patch.object(
                federation.config, 'FEDERATION_RATE_LIMIT_PER_MINUTE', 1):
            federation.enforce_rate_limit('10.0.0.1')
            federation.enforce_rate_limit('10.0.0.2')

            self.assertRaises(
                exceptions.RateLimited,
                federation.enforce_rate_limit, '10.0.0.1')

    def test_zero_writes_no_rows_at_all(self):
        with mock.patch.object(
                federation.config, 'FEDERATION_RATE_LIMIT_PER_MINUTE', 0):
            for _ in range(100):
                federation.enforce_rate_limit('10.0.0.1')

        self.assertEqual({}, self.mock_mariadb.federation_rate_limits)

    def test_a_negative_limit_disables_rather_than_refusing_everything(self):
        # An operator who types -1 meaning "off" gets off, not a
        # cluster where federation refuses every request.
        with mock.patch.object(
                federation.config, 'FEDERATION_RATE_LIMIT_PER_MINUTE', -1):
            federation.enforce_rate_limit('10.0.0.1')

    def test_the_window_is_a_whole_number_of_windows_from_the_epoch(self):
        # Every node must agree on where the window starts, or the
        # limit is per node rather than cluster wide.
        with mock.patch.object(
                federation.config, 'FEDERATION_RATE_LIMIT_PER_MINUTE', 10):
            federation.enforce_rate_limit('10.0.0.1')

        [(_, window)] = self.mock_mariadb.federation_rate_limits
        self.assertEqual(0, window % federation.RATE_LIMIT_WINDOW_SECONDS)
        self.assertLessEqual(window, time.time())
        self.assertGreater(
            window + federation.RATE_LIMIT_WINDOW_SECONDS, time.time())

    def test_a_database_failure_propagates_rather_than_allowing(self):
        with mock.patch.object(
                federation.config,
                'FEDERATION_RATE_LIMIT_PER_MINUTE', 10), \
                mock.patch('shakenfist.mariadb.count_federated_attempt',
                           side_effect=exceptions.DatabaseUnavailable('x')):
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                federation.enforce_rate_limit, '10.0.0.1')


class ReplayRefusalTestCase(FederationTestCase):
    def setUp(self):
        super().setUp()
        self.rule = MappingRule.new(
            'ci', 'ryll', 'github', {'repository': 'shakenfist/ryll'},
            ['blob.read'], 3600, 'ryll-ci')

    def test_the_first_presentation_is_allowed(self):
        federation.refuse_replay(
            self._token(), {'jti': 'a', 'exp': time.time() + 300},
            self.rule)

    def test_the_second_presentation_raises(self):
        claims = {'jti': 'a', 'exp': time.time() + 300}
        federation.refuse_replay(self._token(), claims, self.rule)

        self.assertRaises(
            exceptions.TokenReplayed, federation.refuse_replay,
            self._token(), claims, self.rule)

    def test_the_same_token_against_a_second_rule_is_allowed(self):
        other = MappingRule.new(
            'ci', 'ryll-two', 'github', {'repository': 'shakenfist/ryll'},
            ['blob.read'], 3600, 'ryll-ci')
        claims = {'jti': 'a', 'exp': time.time() + 300}

        federation.refuse_replay(self._token(), claims, self.rule)
        federation.refuse_replay(self._token(), claims, other)

    def test_the_record_carries_the_tokens_own_expiry(self):
        expiry = time.time() + 1234
        federation.refuse_replay(
            self._token(), {'jti': 'a', 'exp': expiry}, self.rule)

        self.assertEqual(
            [expiry], list(self.mock_mariadb.federation_replay.values()))

    def test_a_token_with_no_exp_is_refused_rather_than_given_one(self):
        # validate_token requires exp, so reaching here without one
        # means validation was skipped. Inventing a lifetime for the
        # replay record would be inventing how long the protection
        # lasts.
        for junk in (None, 'soon', True):
            self.assertRaises(
                exceptions.TokenValidationFailed, federation.refuse_replay,
                self._token(), {'jti': 'a', 'exp': junk}, self.rule)

    def test_a_database_failure_propagates_rather_than_allowing(self):
        with mock.patch('shakenfist.mariadb.record_federated_exchange',
                        side_effect=exceptions.DatabaseUnavailable('x')):
            self.assertRaises(
                exceptions.DatabaseUnavailable, federation.refuse_replay,
                self._token(), {'jti': 'a', 'exp': time.time() + 300},
                self.rule)
