# Copyright 2019 Michael Still and contributors

"""The root page's capability list is a contract, so pin it.

A client feature-detects a server-side capability by asking whether its
token appears in the root page -- `shakenfist_client`'s
`check_capability()` is a substring test against the whole document.
That makes this list the only thing standing between a new endpoint
family and a client that cannot tell it exists.

Nothing used to check it. `namespace-claims` was written in
scheduler-reservations phase 4, shipped without its token, and was
found only at the phase's close-out; the phase's own Definition of done
would not have caught it and no test pinned the list. These tests make
the omission mechanical to catch rather than something a reviewer has
to notice.

The snapshot below is deliberately exhaustive. Adding a capability
means editing it, which is the point: a capability worth advertising is
worth one line in a test, and a capability that silently disappears
breaks released clients that are still asking for it.

Some tokens are conditional: they are advertised only when the cluster
configuration their endpoint depends on is present, because advertising
an endpoint that 404s sends probing clients down a path with no
fallback (issue 4003). Which tokens are conditional is part of the
contract too, so it is pinned separately below.
"""

from unittest import mock

from shakenfist.config import SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.tests import base


# Every token the API can advertise, by family. Keep in step with
# API_CAPABILITIES in shakenfist/external_api/app.py.
EXPECTED_CAPABILITIES = {
    'admin': ['cluster-cacert', 'cluster-resources', 'vdi-token-pubkey'],
    'agent-operations': [
        'agentoperations-crud', 'instance-agentoperations',
        'instance-agentoperations-all', 'agentoperations-put-with-mode'],
    'artifacts': [
        'artifact-metadata', 'artifact-upload-types',
        'artifact-clusteroperations'],
    'auth': [
        'trusted-issuers', 'generated-key-secrets', 'scope-enforcement',
        'mapping-rules', 'federated-exchange', 'namespace-claims'],
    'blobs': [
        'blob-metadata', 'blob-search-by-hash', 'blob-data-limit',
        'blob-hash-sha1', 'blob-hash-sha256', 'blob-hash-xxh128',
        'blob-events', 'blob-checksums', 'blob-single-checksum'],
    'cluster-operations': [
        'get-cluster-operations', 'cluster-operation-chain',
        'cluster-operations-by-target'],
    'events': ['events-by-type'],
    'instances': [
        'pure-affinity', 'spice-vdi-console', 'vdi-console-helper',
        'vdi-console-proxy', 'instance-put-blob', 'instance-execute',
        'instance-get', 'instance-screenshot', 'get-instance-namespace',
        'hot-plug-interface', 'include-queued-agent-operations',
        'instance-clusteroperations', 'agentoperation-deadlines'],
    'networks': [
        'list-addresses', 'route-addresses', 'get-network-namespace',
        'provide-dns', 'extra-dns-entries', 'network-clusteroperations',
        'network-delete-async'],
    'networkinterfaces': ['interface-metadata'],
    'nodes': ['node-get', 'node-metadata', 'node-process-metrics'],
}

# The tokens above that are advertised only under some configuration.
# Making a token conditional (or unconditional) is a contract change for
# released clients, so it takes an edit here as well.
EXPECTED_CONDITIONAL_CAPABILITIES = {
    'instances': {'vdi-console-proxy'},
}

KERBSIDE_ON = SFConfig(KERBSIDE_URL='https://kerbside.example.com')
KERBSIDE_OFF = SFConfig(KERBSIDE_URL='')


def declared_tokens(capabilities):
    """Flatten a capability dict's conditional entries to token strings."""
    return {
        family: [
            token.token
            if isinstance(token, external_api.ConditionalCapability)
            else token
            for token in tokens]
        for family, tokens in capabilities.items()}


class RootCapabilitiesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        external_api.TESTING = True
        external_api.app.testing = True
        self.client = external_api.app.test_client()

    def test_declared_capabilities_match_the_snapshot(self):
        self.assertEqual(EXPECTED_CAPABILITIES,
                         declared_tokens(external_api.API_CAPABILITIES))

    def test_conditional_capabilities_match_the_snapshot(self):
        conditional = {}
        for family, tokens in external_api.API_CAPABILITIES.items():
            names = {
                token.token for token in tokens
                if isinstance(token, external_api.ConditionalCapability)}
            if names:
                conditional[family] = names
        self.assertEqual(EXPECTED_CONDITIONAL_CAPABILITIES, conditional)

    def test_root_is_unauthenticated_and_lists_every_capability(self):
        # No Authorization header: the landing page is public, and a
        # client reads it before it has a token to offer. Configuration
        # with every conditional feature on, so the page must carry the
        # whole snapshot.
        with mock.patch('shakenfist.external_api.app.config', KERBSIDE_ON):
            resp = self.client.get('/')
        self.assertEqual(200, resp.status_code)
        body = resp.data.decode('utf-8')

        for family, tokens in EXPECTED_CAPABILITIES.items():
            self.assertIn('<li>%s: ' % family, body)
            for token in tokens:
                # This is the check the client actually performs.
                self.assertIn(token, body)

    def test_namespace_claims_is_advertised(self):
        # The specific regression this file exists for. The claim CRUD
        # surface has no other feature-detection route.
        self.assertIn('namespace-claims',
                      external_api.API_CAPABILITIES['auth'])
        self.assertIn('namespace-claims', self.client.get('/').data.decode())

    def test_vdi_console_proxy_follows_kerbside_config(self):
        # Issue 4003: the /vdiconsoleproxy endpoint 404s when
        # KERBSIDE_URL is unset, and a client that sees the token takes
        # the proxy path with no fallback. The advertisement must track
        # the same config value the endpoint's guard reads.
        with mock.patch('shakenfist.external_api.app.config', KERBSIDE_OFF):
            body = self.client.get('/').data.decode()
        self.assertNotIn('vdi-console-proxy', body)
        # The direct-to-hypervisor path is unconditional; it is what the
        # client degrades to when the proxy token is absent.
        self.assertIn('vdi-console-helper', body)

        with mock.patch('shakenfist.external_api.app.config', KERBSIDE_ON):
            body = self.client.get('/').data.decode()
        self.assertIn('vdi-console-proxy', body)

    def test_tokens_survive_the_rendering(self):
        # The page renders as "family: a, b, c", so a token containing a
        # comma or whitespace would be unparseable to anything splitting
        # the list, and an uppercase one would not match a client's
        # lowercase probe.
        for family, tokens in declared_tokens(
                external_api.API_CAPABILITIES).items():
            self.assertEqual(sorted(set(tokens)), sorted(tokens),
                             'duplicate token in %s' % family)
            for token in tokens:
                self.assertEqual(token, token.strip().lower())
                self.assertNotIn(',', token)
                self.assertNotIn(' ', token)
