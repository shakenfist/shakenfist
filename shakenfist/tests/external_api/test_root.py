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
"""

from shakenfist.external_api import app as external_api
from shakenfist.tests import base


# Every token the API advertises, by family. Keep in step with
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
        'instance-clusteroperations'],
    'networks': [
        'list-addresses', 'route-addresses', 'get-network-namespace',
        'provide-dns', 'extra-dns-entries', 'network-clusteroperations',
        'network-delete-async'],
    'networkinterfaces': ['interface-metadata'],
    'nodes': ['node-get', 'node-metadata', 'node-process-metrics'],
}


class RootCapabilitiesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        external_api.TESTING = True
        external_api.app.testing = True
        self.client = external_api.app.test_client()

    def test_declared_capabilities_match_the_snapshot(self):
        self.assertEqual(EXPECTED_CAPABILITIES,
                         external_api.API_CAPABILITIES)

    def test_root_is_unauthenticated_and_lists_every_capability(self):
        # No Authorization header: the landing page is public, and a
        # client reads it before it has a token to offer.
        resp = self.client.get('/')
        self.assertEqual(200, resp.status_code)
        body = resp.data.decode('utf-8')

        for family, tokens in external_api.API_CAPABILITIES.items():
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

    def test_tokens_survive_the_rendering(self):
        # The page renders as "family: a, b, c", so a token containing a
        # comma or whitespace would be unparseable to anything splitting
        # the list, and an uppercase one would not match a client's
        # lowercase probe.
        for family, tokens in external_api.API_CAPABILITIES.items():
            self.assertEqual(sorted(set(tokens)), sorted(tokens),
                             'duplicate token in %s' % family)
            for token in tokens:
                self.assertEqual(token, token.strip().lower())
                self.assertNotIn(',', token)
                self.assertNotIn(' ', token)
