"""Federated identity exchange, against a real cluster.

The unit tests cover the logic; this covers the wiring. A trusted
issuer and a mapping rule are real database-backed objects with CRUD
endpoints and ownership gates, the exchange is an unauthenticated
route that mints a real credential, and none of that is exercised by
a test which mocks the database away.

Most of this needs no external identity provider. The successful
exchange does -- the cluster has to fetch a JWKS -- so this stands up
a throwaway JWKS server in the test process and hands the issuer its
address. That server speaks TLS, because the API refuses a plaintext
jwks_uri and should keep doing so.

Whether the cluster will accept its certificate depends on
tools/ci-jwks-ca.sh having run first (issue #3639). That script mints
a throwaway CA, points FEDERATION_JWKS_CA_BUNDLE at it on every node,
and leaves the signing key here, so the server can present a leaf the
cluster trusts and the exchange is tested for real. Without it the
server falls back to signing its own, the cluster refuses it, and the
five tests which need a callback skip rather than fail -- in the same
spirit as the Kerbside VDI token test. The parts that need no callback
always run.

The requests library is used directly for anything where the exact
status code is the assertion, because the client library maps
responses onto typed exceptions and flattens the distinctions this
file cares about (401 refused, 403 out of scope, 413 too large, 429
rate limited).
"""

import datetime
import http.server
import ipaddress
import json
import os
import shutil
import socket
import ssl
import tempfile
import threading
import time

import jwt
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.x509.oid import NameOID
from testtools import content

from shakenfist_ci import base
from shakenfist_ci.safe_headers import SafeHeaderMixin


class _JWKSHandler(SafeHeaderMixin, http.server.BaseHTTPRequestHandler):
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

        # The address has to be settled before the server starts, since
        # it is what the certificate has to be issued for.
        local_ip = self._local_ip()

        server = http.server.ThreadingHTTPServer(('0.0.0.0', 0), _JWKSHandler)
        server.jwks = jwks
        server.fetches = []
        server.socket = self._tls_context(local_ip).wrap_socket(
            server.socket, server_side=True)
        self.jwks_server = server

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)

        return 'https://%s:%d/jwks' % (local_ip, server.server_port)

    def _local_ip(self):
        """The address the cluster would have to come back to.

        Asking a socket which local interface routes towards the API is
        more reliable than guessing at hostnames.
        """
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            host = self.base_url.split('://', 1)[-1].split('/')[0]
            probe.connect((host.split(':')[0], 80))
            return probe.getsockname()[0]
        finally:
            probe.close()

    def _ci_ca(self):
        """The CA tools/ci-jwks-ca.sh left for us, if it ran.

        Returns (certificate, key) or None. The directory is the one
        that script writes; change the two together.
        """
        directory = os.environ.get(
            'SF_CI_JWKS_CA_DIR',
            os.path.join(os.path.expanduser('~'), '.sf-ci-jwks-ca'))
        cert_path = os.path.join(directory, 'ca-cert.pem')
        key_path = os.path.join(directory, 'ca-key.pem')
        if not (os.path.exists(cert_path) and os.path.exists(key_path)):
            return None

        with open(cert_path, 'rb') as f:
            certificate = x509.load_pem_x509_certificate(f.read())
        with open(key_path, 'rb') as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        return certificate, key

    @staticmethod
    def _authority_key_identifier(ca_certificate):
        """Point the leaf at the exact CA key that signed it.

        Derived from the CA's own Subject Key Identifier when it has
        one, because that is the identifier a verifier matches against.
        Falling back to hashing the public key produces the same bytes
        for a conventionally generated CA, but not necessarily for one
        somebody made another way.
        """
        try:
            ski = ca_certificate.extensions.get_extension_for_class(
                x509.SubjectKeyIdentifier)
        except x509.ExtensionNotFound:
            return x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_certificate.public_key())
        return x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
            ski.value)

    def _tls_context(self, local_ip):
        """Serve the JWKS over TLS, behind a certificate for local_ip.

        The API refuses a jwks_uri that is not https, and it is right
        to: a JWKS fetched over plaintext can be substituted by anyone
        on the path, which turns signature verification into theatre.
        So the test server has to speak TLS.

        Which certificate depends on whether tools/ci-jwks-ca.sh ran.
        If it did, it minted a throwaway CA, told the cluster to trust
        it for JWKS fetches via FEDERATION_JWKS_CA_BUNDLE, and left the
        signing key here -- so we issue a leaf the cluster will accept
        and the exchange is tested for real.

        If it did not, we fall back to signing our own, which the
        cluster refuses. That is a working configuration rather than a
        broken one: _require_reachable_jwks sees the JWKS was never
        served and skips, so the exchange stays honestly uncovered
        rather than the validation being weakened to suit the test.
        That fallback is what running this file against a hand-built
        cluster gets you.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, local_ip)])

        ca = self._ci_ca()
        if ca:
            ca_certificate, signing_key = ca
            issuer = ca_certificate.subject
            authority_key = self._authority_key_identifier(ca_certificate)
        else:
            # The JWKS signing key doubles as the transport key. A
            # second 2048 bit key per test buys nothing when the
            # certificate is untrusted by design.
            signing_key = self.key
            issuer = subject
            authority_key = x509.AuthorityKeyIdentifier.from_issuer_public_key(
                self.key.public_key())

        # The key identifier extensions are not decoration. Python 3.13
        # turned on ssl.VERIFY_X509_STRICT by default, and a leaf with no
        # Authority Key Identifier fails that check with "Missing
        # Authority Key Identifier" -- which looks exactly like a cluster
        # that does not trust the CA, and would have left these tests
        # skipping for a reason nothing in the skip message mentions.
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address(local_ip))]),
                critical=False)
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(
                    self.key.public_key()),
                critical=False)
            .add_extension(authority_key, critical=False)
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False)
            .sign(signing_key, hashes.SHA256()))

        # load_cert_chain has no in-memory equivalent, so these have to
        # touch disk.
        tmpdir = tempfile.mkdtemp(prefix='sf-ci-jwks-')
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        certfile = os.path.join(tmpdir, 'cert.pem')
        keyfile = os.path.join(tmpdir, 'key.pem')
        with open(certfile, 'wb') as f:
            f.write(certificate.public_bytes(serialization.Encoding.PEM))
        with open(keyfile, 'wb') as f:
            f.write(self.key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()))

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        return context

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
        # Distinguish "not supplied" from "supplied but empty": the rule
        # validation test passes empty containers on purpose, and an `or`
        # here would silently replace them with the valid defaults.
        if bound_claims is None:
            bound_claims = {'repository': 'sf/ci'}
        if scopes is None:
            scopes = ['artifact.read']
        return requests.post(
            self._rules_url(),
            headers={'Authorization': 'Bearer %s' % (token or self.token)},
            json={'name': name, 'issuer': self.issuer_name,
                  'bound_claims': bound_claims, 'scopes': scopes,
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
                'The cluster never fetched %s, so the signature could not '
                'be verified. The likely cause is that tools/ci-jwks-ca.sh '
                'did not run before this suite, so the JWKS server is '
                'behind a certificate it signed itself and the cluster '
                'refuses it -- the state issue #3639 described, and the '
                'expected one outside the CI pipeline. The alternatives '
                'are that the script ran but FEDERATION_JWKS_CA_BUNDLE did '
                'not reach this sf-api, or that the cluster cannot route '
                'back to the test runner at all. Either way the exchange '
                'logic is covered by the unit tests, and the parts of this '
                'file which need no callback still ran.'
                % self.jwks_uri)

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
