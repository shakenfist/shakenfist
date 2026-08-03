"""Federated identity exchange, against a real cluster.

The unit tests cover the logic; this covers the wiring. A trusted
issuer and a mapping rule are real database-backed objects with CRUD
endpoints and ownership gates, the exchange is an unauthenticated
route that mints a real credential, and none of that is exercised by
a test which mocks the database away.

Most of this needs no external identity provider. The successful
exchange does -- the cluster has to fetch a JWKS over HTTP -- so this
stands up a throwaway JWKS server in the test process and hands the
issuer its address. Whether the cluster can route back to the test
runner depends on the deployment, so that half detects reachability
and skips rather than failing, in the same spirit as the Kerbside VDI
token test. The parts that need no network always run.

The requests library is used directly for anything where the exact
status code is the assertion, because the client library maps
responses onto typed exceptions and flattens the distinctions this
file cares about (401 refused, 403 out of scope, 413 too large, 429
rate limited).
"""

import http.server
import json
import socket
import threading
import time

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from testtools import content

from shakenfist_ci import base


class _JWKSHandler(http.server.BaseHTTPRequestHandler):
    """Serves one JWKS document and records that it was asked for."""

    def do_GET(self):
        self.server.fetches.append(self.path)
        body = json.dumps(self.server.jwks).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Silence the default stderr access log.
        ...


class TestFederation(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'federation'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()

        self.base_url = self.system_client.base_url
        self.issuer_name = 'ci-issuer-%s' % self._uniquifier()
        self.issuer_url = 'https://idp.example.com/%s' % self._uniquifier()
        self.audience = self.base_url

        self.key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048)
        self.jwks_uri = self._start_jwks_server()

        self.system_client._request_url(
            'POST', '/auth/issuers',
            data={'name': self.issuer_name, 'issuer_url': self.issuer_url,
                  'jwks_uri': self.jwks_uri, 'audience': self.audience})
        self.addCleanup(self._delete_issuer)

        self.token = self._bearer(self.namespace, self.namespace_key)

    # -- fixtures ------------------------------------------------------

    def _start_jwks_server(self):
        jwks = {
            'keys': [
                json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(
                    self.key.public_key())) | {
                        'kid': 'ci-key-1', 'use': 'sig', 'alg': 'RS256'}
            ]
        }

        server = http.server.ThreadingHTTPServer(('0.0.0.0', 0), _JWKSHandler)
        server.jwks = jwks
        server.fetches = []
        self.jwks_server = server

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)

        # The address the cluster would have to come back to. Asking a
        # socket which local interface routes towards the API is more
        # reliable than guessing at hostnames.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            host = self.base_url.split('://', 1)[-1].split('/')[0]
            probe.connect((host.split(':')[0], 80))
            local_ip = probe.getsockname()[0]
        finally:
            probe.close()

        return 'http://%s:%d/jwks' % (local_ip, server.server_port)

    def _delete_issuer(self):
        try:
            self.system_client._request_url(
                'DELETE', '/auth/issuers/' + self.issuer_name)
        except Exception:
            # Some tests delete the issuer themselves; cleanup is best
            # effort and must not mask the real assertion.
            ...

    def _bearer(self, namespace, key):
        resp = requests.post(
            self.base_url + '/auth',
            json={'namespace': namespace, 'key': key}, timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)
        return resp.json()['access_token']

    def _rules_url(self, namespace=None):
        return '%s/auth/namespaces/%s/rules' % (
            self.base_url, namespace or self.namespace)

    def _create_rule(self, name, scopes=None, bound_claims=None,
                     key_ttl=3600, token=None):
        return requests.post(
            self._rules_url(),
            headers={'Authorization': 'Bearer %s' % (token or self.token)},
            json={'name': name, 'issuer': self.issuer_name,
                  'bound_claims': bound_claims or {'repository': 'sf/ci'},
                  'scopes': scopes or ['artifact.read'],
                  'key_ttl': key_ttl, 'key_name_prefix': 'ci'},
            timeout=30)

    def _identity_token(self, claims=None, jti=None, exp_delta=300,
                        issuer=None, audience=None):
        now = int(time.time())
        body = {
            'iss': issuer or self.issuer_url,
            'aud': audience or self.audience,
            'iat': now, 'exp': now + exp_delta,
            'jti': jti or 'ci-jti-%s' % self._uniquifier(),
            'sub': 'ci', 'repository': 'sf/ci'
        }
        if claims:
            body.update(claims)
        return jwt.encode(body, self.key, algorithm='RS256',
                          headers={'kid': 'ci-key-1'})

    def _exchange(self, token, rule, namespace=None):
        return requests.post(
            self.base_url + '/auth/federated',
            json={'token': token, 'rule': rule,
                  'namespace': namespace or self.namespace},
            timeout=60)

    def _require_reachable_jwks(self, rule):
        """Skip unless the cluster can actually fetch our JWKS."""
        resp = self._exchange(self._identity_token(), rule)
        if resp.status_code == 200:
            return resp

        if not self.jwks_server.fetches:
            self.skipTest(
                'This cluster cannot reach the test runner at %s, so the '
                'JWKS could not be served and the signature could not be '
                'verified. The exchange logic is covered by the unit '
                'tests; the parts of this file which need no callback '
                'still ran.' % self.jwks_uri)

        self.addDetail('exchange_response',
                       content.text_content(resp.text))
        self.fail('The cluster fetched the JWKS but the exchange still '
                  'failed with %d' % resp.status_code)

    # -- issuers -------------------------------------------------------

    def test_an_issuer_round_trips_through_the_api(self):
        issuers = self.system_client._request_url(
            'GET', '/auth/issuers').json()
        names = [i['name'] for i in issuers]
        self.assertIn(self.issuer_name, names)

        one = self.system_client._request_url(
            'GET', '/auth/issuers/' + self.issuer_name).json()
        self.assertEqual(self.issuer_url, one['issuer_url'])
        self.assertEqual(self.jwks_uri, one['jwks_uri'])
        self.assertEqual(self.audience, one['audience'])

    def test_issuers_are_administrative(self):
        # Trusting an identity provider is a cluster-wide decision, so
        # an ordinary namespace must not be able to make or see one.
        for method, path in (('GET', '/auth/issuers'),
                             ('GET', '/auth/issuers/' + self.issuer_name),
                             ('DELETE', '/auth/issuers/' + self.issuer_name)):
            resp = requests.request(
                method, self.base_url + path,
                headers={'Authorization': 'Bearer %s' % self.token},
                timeout=30)
            self.assertIn(
                resp.status_code, (401, 403, 404),
                '%s %s was answered %d for a non-system caller'
                % (method, path, resp.status_code))

    # -- rules ---------------------------------------------------------

    def test_a_rule_round_trips_through_the_api(self):
        name = 'r-%s' % self._uniquifier()
        self.assertEqual(200, self._create_rule(name).status_code)

        resp = requests.get(
            self._rules_url() + '/' + name,
            headers={'Authorization': 'Bearer %s' % self.token}, timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)

        rule = resp.json()
        self.assertEqual(self.issuer_name, rule['issuer'])
        self.assertEqual(['artifact.read'], rule['scopes'])
        self.assertEqual(3600, rule['key_ttl'])

        resp = requests.delete(
            self._rules_url() + '/' + name,
            headers={'Authorization': 'Bearer %s' % self.token}, timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)

    def test_rule_validation_is_enforced_by_the_api(self):
        # Each of these would produce a rule that grants more than its
        # author meant, so they are refused at creation rather than
        # surprising somebody at exchange time.
        cases = {
            'no bound claims': {'bound_claims': {}},
            'no scopes': {'scopes': []},
            'a zero ttl': {'key_ttl': 0},
            'a negative ttl': {'key_ttl': -1},
        }
        for description, kwargs in cases.items():
            resp = self._create_rule('bad-%s' % self._uniquifier(), **kwargs)
            self.assertEqual(
                400, resp.status_code,
                'a rule with %s was accepted: %s'
                % (description, resp.text))

    def test_a_rule_naming_an_unknown_issuer_is_refused(self):
        resp = requests.post(
            self._rules_url(),
            headers={'Authorization': 'Bearer %s' % self.token},
            json={'name': 'r-%s' % self._uniquifier(),
                  'issuer': 'no-such-issuer-%s' % self._uniquifier(),
                  'bound_claims': {'repository': 'sf/ci'},
                  'scopes': ['artifact.read'], 'key_ttl': 3600,
                  'key_name_prefix': 'ci'},
            timeout=30)
        self.assertEqual(400, resp.status_code, resp.text)

    def test_another_namespace_cannot_see_or_write_our_rules(self):
        name = 'r-%s' % self._uniquifier()
        self.assertEqual(200, self._create_rule(name).status_code)

        other = 'ci-fedother-%s' % self._uniquifier()
        other_key = self._uniquifier()
        self._make_namespace(other, other_key)
        self.addCleanup(self._remove_namespace, other)
        other_token = self._bearer(other, other_key)

        for method in ('GET', 'DELETE'):
            resp = requests.request(
                method, self._rules_url() + '/' + name,
                headers={'Authorization': 'Bearer %s' % other_token},
                timeout=30)
            self.assertEqual(
                404, resp.status_code,
                '%s of another namespace rule was answered %d'
                % (method, resp.status_code))

        self.assertEqual(404, self._create_rule(
            'r-%s' % self._uniquifier(), token=other_token).status_code)

    # -- the exchange, without needing a callback ----------------------

    def test_an_untrusted_issuer_is_refused(self):
        resp = self._exchange(
            self._identity_token(issuer='https://not-configured.example.com'),
            'anything')
        self.assertEqual(401, resp.status_code, resp.text)

    def test_missing_fields_are_refused(self):
        for body in ({'namespace': self.namespace, 'rule': 'r'},
                     {'token': 'x', 'rule': 'r'},
                     {'token': 'x', 'namespace': self.namespace}):
            resp = requests.post(
                self.base_url + '/auth/federated', json=body, timeout=30)
            self.assertEqual(400, resp.status_code, resp.text)

    def test_garbage_is_refused_rather_than_erroring(self):
        for junk in ('', 'not-a-token', 'a.b.c'):
            resp = self._exchange(junk, 'anything')
            self.assertIn(
                resp.status_code, (400, 401),
                '%r was answered %d' % (junk, resp.status_code))

    def test_an_oversized_body_is_refused(self):
        # The default limit is 16kb; well past it, and refused on size
        # before anything tries to parse it.
        resp = requests.post(
            self.base_url + '/auth/federated',
            json={'token': 'a' * 200000, 'namespace': self.namespace,
                  'rule': 'anything'},
            timeout=30)
        self.assertEqual(413, resp.status_code, resp.text)

    def test_the_exchange_needs_no_credential(self):
        # Every request in this section carries no Authorization
        # header, which is the whole point of the endpoint. A 400 for
        # a malformed body proves the request reached the handler
        # rather than being turned away by authentication -- a route
        # which had lost its @public marker would answer 401 here
        # without ever looking at the body.
        resp = requests.post(
            self.base_url + '/auth/federated',
            json={'namespace': self.namespace}, timeout=30)
        self.assertEqual(400, resp.status_code, resp.text)

        # And the same route with a credential attached behaves no
        # differently, so nothing depends on being anonymous either.
        resp = requests.post(
            self.base_url + '/auth/federated',
            headers={'Authorization': 'Bearer %s' % self.token},
            json={'namespace': self.namespace}, timeout=30)
        self.assertEqual(400, resp.status_code, resp.text)

    # -- the exchange, end to end --------------------------------------

    def test_a_token_is_exchanged_for_a_working_scoped_key(self):
        name = 'r-%s' % self._uniquifier()
        self.assertEqual(200, self._create_rule(
            name, scopes=['artifact.read']).status_code)

        resp = self._require_reachable_jwks(name)
        minted = resp.json()
        self.addDetail('minted_key_name',
                       content.text_content(minted['key_name']))

        self.assertEqual(self.namespace, minted['namespace'])
        self.assertTrue(minted['key_name'].startswith('ci-'))
        self.assertTrue(minted['key'].startswith('sfk_'),
                        'a cluster minted secret must carry the prefix')

        # The credential actually works, which is the whole point.
        token = self._bearer(self.namespace, minted['key'])
        resp = requests.get(
            self.base_url + '/artifacts',
            headers={'Authorization': 'Bearer %s' % token}, timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)

        # And carries only what the rule granted.
        resp = requests.get(
            self.base_url + '/instances',
            headers={'Authorization': 'Bearer %s' % token}, timeout=30)
        self.assertEqual(
            403, resp.status_code,
            'a key scoped to artifact.read reached the instance list')

    def test_replaying_one_token_through_one_rule_is_refused(self):
        name = 'r-%s' % self._uniquifier()
        self.assertEqual(200, self._create_rule(name).status_code)
        self._require_reachable_jwks(name)

        identity = self._identity_token(jti='ci-replay-%s'
                                        % self._uniquifier())
        self.assertEqual(200, self._exchange(identity, name).status_code)

        resp = self._exchange(identity, name)
        self.assertEqual(
            401, resp.status_code,
            'the same token was exchanged twice through one rule')

    def test_one_token_reaches_two_namespaces_through_two_rules(self):
        # The pattern the CI conductor design depends on, and the
        # reason replay is refused per rule rather than per token.
        first = 'r-%s' % self._uniquifier()
        self.assertEqual(200, self._create_rule(first).status_code)
        self._require_reachable_jwks(first)

        other = 'ci-fedtwo-%s' % self._uniquifier()
        other_key = self._uniquifier()
        self._make_namespace(other, other_key)
        self.addCleanup(self._remove_namespace, other)

        second = 'r-%s' % self._uniquifier()
        resp = requests.post(
            self._rules_url(namespace=other),
            headers={'Authorization': 'Bearer %s'
                     % self._bearer(other, other_key)},
            json={'name': second, 'issuer': self.issuer_name,
                  'bound_claims': {'repository': 'sf/ci'},
                  'scopes': ['artifact.read'], 'key_ttl': 3600,
                  'key_name_prefix': 'ci'},
            timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)

        identity = self._identity_token()
        self.assertEqual(200, self._exchange(identity, first).status_code)
        self.assertEqual(
            200,
            self._exchange(identity, second, namespace=other).status_code)

    def test_a_token_whose_claims_do_not_match_is_refused(self):
        name = 'r-%s' % self._uniquifier()
        self.assertEqual(200, self._create_rule(
            name, bound_claims={'repository': 'sf/ci'}).status_code)
        self._require_reachable_jwks(name)

        resp = self._exchange(
            self._identity_token(claims={'repository': 'sf/somewhere-else'}),
            name)
        self.assertEqual(401, resp.status_code, resp.text)

    def test_deleting_the_rule_stops_the_exchange(self):
        name = 'r-%s' % self._uniquifier()
        self.assertEqual(200, self._create_rule(name).status_code)
        self._require_reachable_jwks(name)

        resp = requests.delete(
            self._rules_url() + '/' + name,
            headers={'Authorization': 'Bearer %s' % self.token}, timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)

        self.assertEqual(
            401, self._exchange(self._identity_token(), name).status_code)
