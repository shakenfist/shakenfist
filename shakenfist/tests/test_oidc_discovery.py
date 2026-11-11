"""Fetching an issuer's OpenID Connect discovery document.

The document decides where a person's browser and authorisation code
will later be sent, so these tests are mostly about refusals, and the
one that matters most is that a document naming a different issuer is
refused.

Everything runs against a fake requests session: no network, and no
dependency on a real identity provider being reachable or unchanged.
The fake is hand written rather than a MagicMock because the code reads
response.status_code and iterates the body, and a MagicMock answers both
of those with something truthy -- a mock response would pass the status
check and never finish the body.
"""

import json
import ssl
from unittest import mock

import requests

from shakenfist import exceptions
from shakenfist import oidc_discovery
from shakenfist.tests import base


# Keycloak's issuer carries a path and no trailing slash.
KEYCLOAK = 'https://keycloak.example.com/realms/shakenfist'
KEYCLOAK_DISCOVERY = (
    'https://keycloak.example.com/realms/shakenfist'
    '/.well-known/openid-configuration')

# Authentik's carries a path and a trailing slash, and its iss claim
# carries the slash too.
AUTHENTIK = 'https://auth.example.com/application/o/shakenfist/'
AUTHENTIK_DISCOVERY = (
    'https://auth.example.com/application/o/shakenfist'
    '/.well-known/openid-configuration')


def _document(issuer, **overrides):
    """A conforming document for an issuer, with members overridden."""
    document = {
        'issuer': issuer,
        'jwks_uri': issuer + '/jwks',
        'authorization_endpoint': issuer + '/authorize',
        'token_endpoint': issuer + '/token',
        'device_authorization_endpoint': issuer + '/device',
    }
    document.update(overrides)
    return document


class FakeResponse:
    """Just enough of a requests response for the fetch path."""

    def __init__(self, status_code=200, body=b'', headers=None,
                 body_error=None):
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}
        self.body_error = body_error
        self.closed = False
        self.bytes_served = 0

    def iter_content(self, chunk_size=1):
        for offset in range(0, len(self.body), chunk_size):
            chunk = self.body[offset:offset + chunk_size]
            self.bytes_served += len(chunk)
            yield chunk
        if self.body_error:
            raise self.body_error

    def close(self):
        self.closed = True


class FakeSession:
    """A requests session which answers one request from memory."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.mounted = {}
        self.requests = []
        self.closed = False

    def mount(self, prefix, adapter):
        self.mounted[prefix] = adapter

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


class OIDCDiscoveryTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.session = None

    def _serve(self, body, status_code=200, headers=None, body_error=None):
        """Answer the next fetch with this body, and record the request."""
        if not isinstance(body, bytes):
            body = json.dumps(body).encode('utf-8')
        self.session = FakeSession(
            FakeResponse(status_code, body, headers, body_error))
        self._patch_session()
        return self.session

    def _fail_with(self, error):
        """Answer the next fetch by raising this requests exception."""
        self.session = FakeSession(error=error)
        self._patch_session()
        return self.session

    def _patch_session(self):
        patch = mock.patch.object(
            oidc_discovery.requests, 'Session', return_value=self.session)
        # Kept so that a test can assert no session was built at all,
        # which is a stronger statement than no request being sent.
        self.session_factory = patch.start()
        self.addCleanup(patch.stop)

    def _requested_url(self):
        self.assertEqual(1, len(self.session.requests))
        return self.session.requests[0][0]


class DiscoveryURLTestCase(OIDCDiscoveryTestCase):
    def test_an_authentik_issuer_gets_exactly_one_slash(self):
        # The whole URL, not a substring: two slashes in the middle of a
        # path are the failure being tested for, and a substring
        # assertion would not see them.
        self.assertEqual(AUTHENTIK_DISCOVERY, oidc_discovery.discovery_url(
            AUTHENTIK))

        self._serve(_document(AUTHENTIK))
        oidc_discovery.fetch(AUTHENTIK)
        self.assertEqual(AUTHENTIK_DISCOVERY, self._requested_url())

    def test_a_keycloak_issuer_keeps_its_realm_path(self):
        # Appended after the path, not inserted between the host and the
        # path, which is the other arrangement a specification defines
        # and is not what either provider serves.
        self.assertEqual(KEYCLOAK_DISCOVERY, oidc_discovery.discovery_url(
            KEYCLOAK))

        self._serve(_document(KEYCLOAK))
        oidc_discovery.fetch(KEYCLOAK)
        self.assertEqual(KEYCLOAK_DISCOVERY, self._requested_url())

    def test_only_one_trailing_slash_is_stripped(self):
        # Stripping every trailing slash would be a normalisation, and
        # the point of doing this in the URL rather than in the
        # comparison is that no normalisation happens anywhere.
        self.assertEqual(
            'https://h/a//.well-known/openid-configuration',
            oidc_discovery.discovery_url('https://h/a//'))


class DiscoveryFetchRefusalTestCase(OIDCDiscoveryTestCase):
    def test_an_http_issuer_url_is_refused_without_a_fetch(self):
        # A document which would otherwise be accepted is waiting to be
        # served, so this fails if the scheme check is weakened rather
        # than merely reordered.
        plaintext = 'http://keycloak.example.com/realms/shakenfist'
        self._serve(_document(plaintext))

        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, plaintext)
        self.assertIn('is not https', str(e))

        # Refused before anything is built, not merely before anything
        # is believed.
        self.session_factory.assert_not_called()
        self.assertEqual([], self.session.requests)

    def test_a_non_2xx_status_is_refused(self):
        self._serve(b'not found', status_code=404)
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('status 404', str(e))

    def test_a_redirect_is_refused_rather_than_followed(self):
        self._serve(b'', status_code=302,
                    headers={'Location': 'https://elsewhere.example.com/'})
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('redirect', str(e))
        self.assertIn('https://elsewhere.example.com/', str(e))

        # And the refusal is ours rather than something the HTTP library
        # was asked to do on our behalf.
        self.assertEqual(
            False, self.session.requests[0][1]['allow_redirects'])

    def test_an_oversized_body_is_refused(self):
        cap = mock.patch.object(
            oidc_discovery.config, 'FEDERATION_DISCOVERY_MAX_BYTES', 64)
        cap.start()
        self.addCleanup(cap.stop)

        self._serve(_document(KEYCLOAK))
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('FEDERATION_DISCOVERY_MAX_BYTES', str(e))

    def test_a_body_which_is_not_json_is_refused(self):
        self._serve(b'<html><body>Sign in</body></html>')
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('did not return JSON', str(e))

    def test_a_connection_failure_is_refused_with_its_cause(self):
        self._fail_with(requests.exceptions.ConnectTimeout('took too long'))
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('ConnectTimeout', str(e))

    def test_a_connection_lost_mid_body_is_refused(self):
        # The body read raises the same family of exceptions as the
        # request, and an administrative endpoint should see a discovery
        # failure rather than an unhandled HTTP library exception.
        self._serve(
            b'{"issuer": "https://keycloak',
            body_error=requests.exceptions.ChunkedEncodingError(
                'connection reset'))
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('ChunkedEncodingError', str(e))

    def test_an_unloadable_ca_bundle_is_not_reported_as_the_providers_fault(
            self):
        # Our misconfiguration rather than anything about the provider,
        # so it propagates for the endpoint to answer differently.
        context = mock.patch.object(
            oidc_discovery.federation, 'jwks_ssl_context',
            side_effect=exceptions.JWKSTrustAnchorUnusable('bad bundle'))
        context.start()
        self.addCleanup(context.stop)

        self.assertRaises(
            exceptions.JWKSTrustAnchorUnusable, oidc_discovery.fetch,
            KEYCLOAK)


class DiscoveryIssuerComparisonTestCase(OIDCDiscoveryTestCase):
    def test_a_document_for_another_issuer_is_refused(self):
        self._serve(_document('https://evil.example.com/realms/shakenfist'))
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('https://evil.example.com/realms/shakenfist', str(e))
        self.assertIn('match exactly', str(e))

    def test_a_trailing_slash_difference_is_refused_with_both_urls(self):
        # The configured value has no trailing slash and the document's
        # does. The comparison is exact, so this is a refusal -- and the
        # message has to carry both strings, because a difference of one
        # character at the end of a long URL is otherwise invisible to
        # the operator reading it.
        configured = AUTHENTIK[:-1]
        self._serve(_document(AUTHENTIK))
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, configured)
        self.assertIn(f'"{AUTHENTIK}"', str(e))
        self.assertIn(f'"{configured}"', str(e))

    def test_an_enormous_issuer_member_does_not_bloat_the_refusal(self):
        # The refusal names what the provider said, and what it said is
        # bounded only by the body cap -- so a provider which is hostile
        # or merely broken could otherwise put sixty kilobytes into the
        # response to the administrator and into the log line. The value
        # is truncated and its real length reported instead, because
        # "it is enormous" is the useful diagnosis in that case.
        #
        # The document is built by hand rather than with _document(),
        # which repeats the issuer into each endpoint member: that
        # inflates the body past FEDERATION_DISCOVERY_MAX_BYTES and the
        # size refusal fires first, which tests the cap and not this.
        # The case that reaches the comparison is one enormous member in
        # a document which still fits, so that is what is served.
        enormous = 'https://evil.example.com/' + 'A' * 60000
        self._serve({'issuer': enormous,
                     'jwks_uri': 'https://evil.example.com/jwks'})
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertLess(len(str(e)), 1000)
        self.assertIn(f'({len(enormous)} characters in total)', str(e))
        # The start of it still appears, so an operator can tell which
        # provider answered.
        self.assertIn('https://evil.example.com/AAA', str(e))

    def test_a_document_with_no_issuer_member_is_refused(self):
        document = _document(KEYCLOAK)
        del document['issuer']
        self._serve(document)
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('no issuer member', str(e))


class DiscoveryDocumentTestCase(OIDCDiscoveryTestCase):
    def test_a_document_with_no_jwks_uri_is_refused(self):
        document = _document(KEYCLOAK)
        del document['jwks_uri']
        self._serve(document)
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('no jwks_uri', str(e))

    def test_a_plaintext_jwks_uri_is_refused(self):
        self._serve(_document(
            KEYCLOAK, jwks_uri='http://keycloak.example.com/jwks'))
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('jwks_uri', str(e))
        self.assertIn('is not https', str(e))

    def test_a_plaintext_authorization_endpoint_is_refused(self):
        self._serve(_document(
            KEYCLOAK,
            authorization_endpoint='http://keycloak.example.com/authorize'))
        e = self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)
        self.assertIn('authorization_endpoint', str(e))
        self.assertIn('is not https', str(e))

    def test_a_well_formed_document_returns_only_what_we_store(self):
        # The served document carries members a real provider publishes
        # and we do not keep, so this asserts the whole returned dict
        # rather than the presence of the endpoints: a member nobody has
        # considered must not reach an issuer's configuration.
        self._serve(_document(
            KEYCLOAK,
            grant_types_supported=['authorization_code', 'device_code'],
            response_types_supported=['code'],
            userinfo_endpoint=KEYCLOAK + '/userinfo',
            end_session_endpoint=KEYCLOAK + '/logout'))

        self.assertEqual({
            'issuer': KEYCLOAK,
            'jwks_uri': KEYCLOAK + '/jwks',
            'authorization_endpoint': KEYCLOAK + '/authorize',
            'token_endpoint': KEYCLOAK + '/token',
            'device_authorization_endpoint': KEYCLOAK + '/device',
        }, oidc_discovery.fetch(KEYCLOAK))

    def test_an_endpoint_a_provider_does_not_publish_is_simply_absent(self):
        # A provider which has not enabled the device code grant does not
        # publish the endpoint, and that is an answer rather than an
        # error: a client reads the absence and offers another flow.
        document = _document(KEYCLOAK)
        del document['device_authorization_endpoint']
        self._serve(document)

        self.assertEqual({
            'issuer': KEYCLOAK,
            'jwks_uri': KEYCLOAK + '/jwks',
            'authorization_endpoint': KEYCLOAK + '/authorize',
            'token_endpoint': KEYCLOAK + '/token',
        }, oidc_discovery.fetch(KEYCLOAK))


class DiscoveryRequestTestCase(OIDCDiscoveryTestCase):
    def test_the_request_is_built_the_way_the_jwks_fetch_is(self):
        self._serve(_document(KEYCLOAK))
        oidc_discovery.fetch(KEYCLOAK)

        _, kwargs = self.session.requests[0]
        self.assertEqual(False, kwargs['allow_redirects'])
        self.assertEqual(True, kwargs['stream'])
        self.assertEqual(
            oidc_discovery.config.FEDERATION_DISCOVERY_FETCH_TIMEOUT_SECONDS,
            kwargs['timeout'])

        # verify= would replace the system trust store rather than adding
        # to it, which is the whole reason for the adapter below.
        self.assertNotIn('verify', kwargs)

    def test_the_session_verifies_with_the_jwks_trust_anchors(self):
        context = ssl.create_default_context()
        patch = mock.patch.object(
            oidc_discovery.federation, 'jwks_ssl_context',
            return_value=context)
        patch.start()
        self.addCleanup(patch.stop)

        self._serve(_document(KEYCLOAK))
        oidc_discovery.fetch(KEYCLOAK)

        adapter = self.session.mounted['https://']
        # Mounted, and actually handed to the connection pool rather than
        # merely held by the adapter.
        self.assertIs(context, adapter._ssl_context)
        self.assertIs(
            context, adapter.poolmanager.connection_pool_kw['ssl_context'])

    def test_the_session_and_the_response_are_closed(self):
        # A leaked streamed response holds a connection, and a leaked
        # session holds a pool of them, in a long lived API worker.
        self._serve(_document(KEYCLOAK))
        oidc_discovery.fetch(KEYCLOAK)

        self.assertTrue(self.session.closed)
        self.assertTrue(self.session.response.closed)

    def test_a_refused_response_is_closed_too(self):
        self._serve(b'not found', status_code=404)
        self.assertRaises(
            exceptions.OIDCDiscoveryFailed, oidc_discovery.fetch, KEYCLOAK)

        self.assertTrue(self.session.closed)
        self.assertTrue(self.session.response.closed)
