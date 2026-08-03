"""Scopes must compose with namespace trust.

Open question 11 of docs/plans/PLAN-auth-federation.md. Namespace trust
grants cross-namespace visibility, and the deferred CI conductor design
leans on it: a per-pull-request scratch namespace with read access to a
per-repository cache namespace.

The two mechanisms answer different questions. Trust answers "may this
caller see that object at all", and scope answers "may this caller do
this kind of thing". They have to compose, and they have to compose in
one direction only -- a scoped credential must carry its scopes across
the trust boundary intact, and must never widen just because the
object it is reaching for lives in a namespace that trusts it. If it
did, trust would be a scope-escape hatch: mint a narrowly scoped key,
point it at a trusting namespace, and get the wildcard back.

The tests run against the artifact paths trust governs: listing and
reading by uuid, which both filter through namespace_or_shared_filter.
Delete appears too, but as the boundary rather than as a path across
it -- trust is a visibility mechanism, so requires_artifact_ownership
tests the caller's own namespace and nothing else, and a scoped key
therefore has its own artifacts to delete and no others. Each refusal
is paired with a control showing the same request succeeding when the
one thing under test is changed -- otherwise a 403 for some unrelated
reason would read as the property holding.

Whether a caller may see an artifact at all is a separate question,
tested separately in test_artifact_access.py. Here it is only the
backdrop: trust is the thing that opens the door, and the point is
that a scoped key walking through it stays scoped.
"""

import io
import json
import logging
import sys
import time
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from flask_jwt_extended import decode_token

from shakenfist import federation
from shakenfist import mariadb
from shakenfist.artifact import Artifact
from shakenfist.external_api import app as external_api
from shakenfist.mapping_rule import MappingRule
from shakenfist.namespace import Namespace
from shakenfist.namespace_key import NamespaceKey
from shakenfist.schema.namespace_key_attributes import (
    NamespaceKeyAttributesData)
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.trusted_issuer import TrustedIssuer


GITHUB = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS = GITHUB + '/.well-known/jwks'
AUDIENCE = 'https://sf.example.com'


class TrustCompositionFixture(base.ShakenFistTestCase):
    """Two namespaces, one trusting the other, and an artifact.

    Carries no tests of its own so the two suites below do not
    re-run each other's.
    """

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
        # The shape the CI conductor design assumes: a long lived cache
        # namespace, and a short lived scratch namespace which is
        # allowed to read from it.
        self.mock_mariadb.create_namespace('cache', 'key1', 'cachekey')
        self.mock_mariadb.create_namespace('scratch', 'key1', 'scratchkey')

        self.client = external_api.app.test_client()

        # An artifact owned by cache, which scratch has no business
        # seeing until trust says otherwise.
        self.artifact = Artifact.new(
            Artifact.TYPE_OTHER, 'http://example.com/thing.tgz',
            name='thing', namespace='cache')
        self.artifact.state = Artifact.STATE_CREATED

    def _trust(self):
        """cache trusts scratch, so scratch may see cache's objects."""
        Namespace.from_db('cache').add_trust('scratch')

    def _token(self, namespace, secret, scopes_granted=None):
        """A token from a key in namespace, optionally scoped."""
        name = 'key-%s' % secret
        ns = Namespace.from_db(namespace)
        ns.add_key(name, secret)

        if scopes_granted is not None:
            key = ns.lookup_key(name)
            obj = NamespaceKey.from_db_by_name(namespace, name)
            mariadb.update_namespace_key_attributes(
                NamespaceKeyAttributesData(
                    uuid=obj.uuid, key=key.key, nonce=key.nonce,
                    expiry=key.expiry, scopes=scopes_granted,
                    provenance=None))

        resp = self.client.post('/auth', data=json.dumps(
            {'namespace': namespace, 'key': secret}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _list(self, token):
        return self.client.get('/artifacts',
                               headers={'Authorization': token})

    def _visible(self, token):
        resp = self._list(token)
        self.assertEqual(200, resp.status_code)
        return [a['uuid'] for a in resp.get_json()]

    def _read(self, token):
        return self.client.get(
            '/artifacts/%s' % self.artifact.uuid,
            headers={'Authorization': token})

    def _delete(self, token):
        return self.client.delete(
            '/artifacts/%s' % self.artifact.uuid,
            headers={'Authorization': token})


class ScopeTrustCompositionTestCase(TrustCompositionFixture):
    """Hand-scoped keys, which is the mechanism under test."""

    # -- Reading across the boundary -----------------------------------

    def test_a_scoped_key_reads_across_the_trust_boundary(self):
        self._trust()
        token = self._token('scratch', 'sekrit1', ['artifact.read'])

        self.assertIn(str(self.artifact.uuid), self._visible(token))

    def test_without_trust_there_is_nothing_to_read(self):
        # The control for the test above: the scope alone buys nothing.
        token = self._token('scratch', 'sekrit2', ['artifact.read'])

        self.assertNotIn(str(self.artifact.uuid), self._visible(token))

    def test_trust_does_not_turn_a_scoped_token_into_a_wildcard(self):
        # The escape hatch this exists to close. A token scoped to a
        # different family entirely must not reach artifacts merely
        # because they live somewhere that trusts it.
        #
        # Refused outright rather than filtered to nothing: handing a
        # scoped credential an empty list would read as "there is
        # nothing here", which is a different and misleading answer.
        self._trust()
        token = self._token('scratch', 'sekrit3', ['instance.read'])

        self.assertEqual(403, self._list(token).status_code)

    def test_a_key_granted_nothing_gains_nothing_from_trust(self):
        self._trust()
        token = self._token('scratch', 'sekrit4', [])

        self.assertEqual(403, self._list(token).status_code)
        self.assertEqual(403, self._delete(token).status_code)

    def test_a_scoped_key_reads_by_uuid_across_the_trust_boundary(self):
        # The listing is the discovery path; this is the fetch path,
        # and it has its own guard (requires_artifact_access) rather
        # than sharing the filter the listing applies. Composition has
        # to hold on both or the second is a way around the first.
        self._trust()
        token = self._token('scratch', 'sekritc', ['artifact.read'])

        self.assertEqual(200, self._read(token).status_code)

    def test_reading_by_uuid_still_needs_the_trust(self):
        token = self._token('scratch', 'sekritd', ['artifact.read'])

        self.assertEqual(404, self._read(token).status_code)

    def test_reading_by_uuid_still_needs_the_scope(self):
        self._trust()
        token = self._token('scratch', 'sekrite', ['instance.read'])

        self.assertEqual(403, self._read(token).status_code)

    def test_the_scopes_claim_survives_the_boundary_unchanged(self):
        # Asserted on the claim as well as the behaviour, so a failure
        # here means the scopes changed rather than some incidental
        # difference in the response.
        self._trust()
        token = self._token('scratch', 'sekrit5', ['artifact.read'])

        with external_api.app.app_context():
            claims = decode_token(token.split(' ', 1)[1])
        self.assertEqual(['artifact.read'], claims['scopes'])

    # -- Writing across the boundary -----------------------------------

    def test_a_read_scoped_key_cannot_delete_across_the_boundary(self):
        # The same credential, the same object, the same trust -- and
        # refused, because artifact.read is not artifact.delete. This
        # is the property open question 11 asks for.
        #
        # Note this is a 403 rather than a 404: the scope gate runs
        # before the object is ever resolved, so the caller is turned
        # away for the scope, not for the ownership test which would
        # also have refused them. The pair of tests below separate the
        # two, since a refusal that happens for two reasons at once
        # proves neither of them.
        self._trust()
        token = self._token('scratch', 'sekrit6', ['artifact.read'])

        self.assertEqual(403, self._delete(token).status_code)

    def test_the_delete_refusal_is_the_scope_and_not_the_ownership(self):
        # The control. A wildcard key deleting an artifact of its own
        # succeeds, so the 403 above was the scope talking. Deleting
        # *its own* rather than the cache's, because trust no longer
        # authorises writing to somebody else's namespace -- see
        # test_trust_does_not_authorise_deletion below.
        own = Artifact.new(
            Artifact.TYPE_OTHER, 'http://example.com/mine.tgz',
            name='mine', namespace='scratch')
        own.state = Artifact.STATE_CREATED
        token = self._token('scratch', 'sekrit7')

        self.assertEqual(200, self.client.delete(
            '/artifacts/%s' % own.uuid,
            headers={'Authorization': token}).status_code)

    def test_the_delete_scope_granted_permits_the_delete(self):
        # The other control: the right scope does work, so a scoped
        # key is not simply read only.
        own = Artifact.new(
            Artifact.TYPE_OTHER, 'http://example.com/mine.tgz',
            name='mine', namespace='scratch')
        own.state = Artifact.STATE_CREATED
        token = self._token(
            'scratch', 'sekrit8', ['artifact.read', 'artifact.delete'])

        self.assertEqual(200, self.client.delete(
            '/artifacts/%s' % own.uuid,
            headers={'Authorization': token}).status_code)

    def test_trust_does_not_authorise_deletion(self):
        # Trust composes with scopes on the read paths and does not
        # compose at all on the write ones, because it is a visibility
        # mechanism. A correctly scoped key, across a live trust, with
        # the right UUID, still cannot delete the cache's artifact.
        self._trust()
        token = self._token(
            'scratch', 'sekritf', ['artifact.read', 'artifact.delete'])

        self.assertEqual(200, self._read(token).status_code)
        self.assertEqual(404, self._delete(token).status_code)

    def test_the_delete_scope_is_not_enough_without_trust(self):
        # Scope does not substitute for visibility. Without the trust
        # grant this is a 404 rather than a 403 -- a caller who may not
        # see an object should not learn that it exists.
        token = self._token(
            'scratch', 'sekrit9', ['artifact.read', 'artifact.delete'])

        self.assertEqual(404, self._delete(token).status_code)

    def test_trust_is_one_directional(self):
        # cache trusting scratch does not let cache reach into
        # scratch. Asserted because a symmetric implementation would
        # pass every other test in this file.
        self._trust()
        other = Artifact.new(
            Artifact.TYPE_OTHER, 'http://example.com/other.tgz',
            name='other', namespace='scratch')
        other.state = Artifact.STATE_CREATED

        token = self._token('cache', 'sekrita', ['artifact.read'])
        self.assertNotIn(str(other.uuid), self._visible(token))

    def test_a_wildcard_key_still_needs_trust(self):
        # The compatibility half: trust is not weakened for the legacy
        # unscoped keys every existing operator holds.
        token = self._token('scratch', 'sekritb')

        self.assertNotIn(str(self.artifact.uuid), self._visible(token))


class FederatedKeyTrustCompositionTestCase(TrustCompositionFixture):
    """The same property, for a key the exchange actually minted.

    Everything above scopes a key by hand. This drives the whole chain
    the plan builds -- trusted issuer, mapping rule, identity token,
    exchange, minted key, token -- and then asserts the composition
    behaves identically. "A federated key is just a namespace key" is
    the claim the design rests on, so it is tested rather than assumed.
    """

    def setUp(self):
        super().setUp()

        self.key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048)
        federation.JWKS_CACHE = federation.JWKSCache()
        TrustedIssuer.new('github', GITHUB, GITHUB_JWKS, AUDIENCE)
        MappingRule.new(
            'scratch', 'pr-build', 'github',
            {'repository': 'shakenfist/ryll'},
            ['artifact.read'], 3600, 'pr')

        patcher = mock.patch(
            'jwt.jwks_client.urllib.request.urlopen',
            side_effect=self._urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _urlopen(self, request, **kwargs):
        body = json.dumps({
            'keys': [
                json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(
                    self.key.public_key())) | {
                        'kid': 'key-1', 'use': 'sig', 'alg': 'RS256'}
            ]
        }).encode('utf-8')
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(body)
        response.__exit__.return_value = False
        return response

    def _federated_token(self):
        """Exchange an identity token and authenticate with the result."""
        now = int(time.time())
        identity = jwt.encode(
            {'iss': GITHUB, 'aud': AUDIENCE, 'iat': now, 'exp': now + 300,
             'jti': 'jti-%d' % now, 'sub': 'repo:shakenfist/ryll',
             'repository': 'shakenfist/ryll'},
            self.key, algorithm='RS256', headers={'kid': 'key-1'})

        resp = self.client.post('/auth/federated', data=json.dumps(
            {'token': identity, 'namespace': 'scratch',
             'rule': 'pr-build'}))
        self.assertEqual(200, resp.status_code)
        minted = resp.get_json()

        resp = self.client.post('/auth', data=json.dumps(
            {'namespace': 'scratch', 'key': minted['key']}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def test_a_federated_key_reads_across_the_trust_boundary(self):
        self._trust()
        token = self._federated_token()

        self.assertIn(str(self.artifact.uuid), self._visible(token))

    def test_a_federated_key_reads_by_uuid_across_the_boundary(self):
        self._trust()
        token = self._federated_token()

        self.assertEqual(200, self._read(token).status_code)

    def test_a_federated_key_cannot_delete_across_it(self):
        self._trust()
        token = self._federated_token()

        self.assertEqual(403, self._delete(token).status_code)

    def test_a_federated_key_carries_exactly_the_rules_scopes(self):
        self._trust()
        token = self._federated_token()

        with external_api.app.app_context():
            claims = decode_token(token.split(' ', 1)[1])
        self.assertEqual(['artifact.read'], claims['scopes'])

    def test_a_federated_key_sees_nothing_without_trust(self):
        token = self._federated_token()

        self.assertNotIn(str(self.artifact.uuid), self._visible(token))
