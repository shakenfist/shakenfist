"""Who may read an artifact they did not create.

`arg_is_artifact_ref` short-circuits a uuid straight to
`Artifact.from_db`, which applies no namespace filter -- by design,
because the same decorator serves system callers who legitimately reach
across namespaces. That makes `requires_artifact_access` the only thing
between a guessed or leaked uuid and another tenant's artifact, and it
used to get the test backwards in both directions: shared artifacts
were hidden from the namespaces they were shared with, and unshared
artifacts were readable by anybody who knew the uuid.

So the interesting cases here are the negative ones, and each is paired
with a control showing the same request succeeding when the single
thing under test changes. A 404 arriving because the fixture is
misbuilt looks exactly like a 404 arriving because the guard works.

The matrix is (owner, trusted, stranger, system) x (shared, unshared),
run against `GET /artifacts/<uuid>` and then repeated across the other
routes carrying the same decorator, because a guard fixed in one place
and not the others is the failure mode that put this file here.
"""

import json
import logging
import sys
from unittest import mock

from shakenfist import mariadb
from shakenfist.artifact import Artifact
from shakenfist.external_api import app as external_api
from shakenfist.namespace import Namespace
from shakenfist.namespace_key import NamespaceKey
from shakenfist.schema.namespace_key_attributes import (
    NamespaceKeyAttributesData)
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class ArtifactAccessFixture(base.ShakenFistTestCase):
    """An artifact owned by `owner`, and three other points of view.

    `trusted` is trusted by owner, `stranger` is not, and system is
    trusted by everybody whether they like it or not.
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
        self.mock_mariadb.create_namespace('owner', 'key1', 'ownerkey')
        self.mock_mariadb.create_namespace('trusted', 'key1', 'trustedkey')
        self.mock_mariadb.create_namespace('stranger', 'key1', 'strangerkey')

        Namespace.from_db('owner').add_trust('trusted')

        self.client = external_api.app.test_client()

        self.artifact = Artifact.new(
            Artifact.TYPE_OTHER, 'http://example.com/thing.tgz',
            name='thing', namespace='owner')
        self.artifact.state = Artifact.STATE_CREATED

    def _share(self):
        self.artifact.shared = True

    def _token(self, namespace, scopes_granted=None):
        """A token for namespace, optionally scoped.

        Unscoped keys get the legacy wildcard, which is what almost
        every caller in the wild still holds.
        """
        # Derived from the namespace and the scopes rather than being a
        # counter, so that repeated calls within one test reuse the one
        # key and calls asking for different scopes never collide on it.
        # No scopes at all and an empty scope list are different things
        # and get different keys.
        if scopes_granted is None:
            tag = 'wildcard'
        else:
            tag = '-'.join(sorted(scopes_granted)) or 'noscopes'
        secret = 'secret-%s-%s' % (namespace, tag)
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

    def _get(self, namespace, path='', scopes_granted=None):
        return self.client.get(
            '/artifacts/%s%s' % (self.artifact.uuid, path),
            headers={'Authorization': self._token(namespace, scopes_granted)})

    def _delete(self, namespace):
        return self.client.delete(
            '/artifacts/%s' % self.artifact.uuid,
            headers={'Authorization': self._token(namespace)})


class UnsharedArtifactAccessTestCase(ArtifactAccessFixture):
    """An artifact nobody has published."""

    def test_the_owner_can_read_it(self):
        # The control for everything below. If this fails, the fixture
        # is broken and none of the 404s mean anything.
        resp = self._get('owner')
        self.assertEqual(200, resp.status_code)
        self.assertEqual(str(self.artifact.uuid), resp.get_json()['uuid'])

    def test_a_stranger_who_knows_the_uuid_cannot_read_it(self):
        # The bug. Knowing a uuid is not authorisation, and a uuid is
        # not a secret -- they turn up in logs, in error messages, and
        # in the output of any API call that mentions the object.
        self.assertEqual(404, self._get('stranger').status_code)

    def test_a_trusted_namespace_can_read_it(self):
        # Trust is the mechanism that is *supposed* to open this door,
        # and it still does.
        self.assertEqual(200, self._get('trusted').status_code)

    def test_system_can_read_it(self):
        self.assertEqual(200, self._get('system').status_code)

    def test_trust_is_one_directional(self):
        # owner trusting trusted does not let owner read trusted's
        # artifacts. A symmetric implementation passes every other
        # test in this file, so it gets asserted explicitly.
        other = Artifact.new(
            Artifact.TYPE_OTHER, 'http://example.com/other.tgz',
            name='other', namespace='trusted')
        other.state = Artifact.STATE_CREATED

        resp = self.client.get(
            '/artifacts/%s' % other.uuid,
            headers={'Authorization': self._token('owner')})
        self.assertEqual(404, resp.status_code)

    def test_the_refusal_is_404_and_not_403(self):
        # Deliberate: a caller who may not see an object should not
        # learn that it exists. Asserted because a well meaning change
        # to "403 forbidden" would be a small information leak, and
        # every other artifact decorator already refuses this way.
        resp = self._get('stranger')
        self.assertEqual(404, resp.status_code)
        self.assertNotIn('owner', resp.get_data(as_text=True))


class SharedArtifactAccessTestCase(ArtifactAccessFixture):
    """An artifact the system namespace has published to everybody."""

    def setUp(self):
        super().setUp()
        self._share()

    def test_a_stranger_can_read_a_shared_artifact(self):
        # The other half of the inverted test: sharing an artifact used
        # to make it *less* readable, which meant the shared images the
        # deployment ships could be listed but not fetched by uuid.
        self.assertEqual(200, self._get('stranger').status_code)

    def test_the_owner_can_still_read_it(self):
        self.assertEqual(200, self._get('owner').status_code)

    def test_system_can_still_read_it(self):
        self.assertEqual(200, self._get('system').status_code)

    def test_sharing_does_not_grant_a_stranger_delete(self):
        # Sharing publishes an artifact for reading. It is not a
        # transfer of ownership, and `requires_artifact_ownership`
        # never consults the flag.
        self.assertEqual(404, self._delete('stranger').status_code)
        self.assertNotEqual(
            Artifact.STATE_DELETED,
            Artifact.from_db(self.artifact.uuid).state.value)

    def test_the_delete_refusal_is_the_sharing_and_not_the_uuid(self):
        # The control for the test above: the same request, from a
        # namespace that is trusted, succeeds.
        self.assertEqual(200, self._delete('trusted').status_code)


class ArtifactSubresourceAccessTestCase(ArtifactAccessFixture):
    """Every other route carrying `requires_artifact_access`.

    A read guard fixed on the object itself and left wrong on the
    routes hanging off it leaks the same information more slowly.
    """

    SUBRESOURCES = ['', '/events', '/versions', '/clusteroperations']

    def setUp(self):
        super().setUp()

        # MockMariaDB deliberately does not store events, so the events
        # route would 500 on the way to the database and the positive
        # controls below could not tell a working guard from a broken
        # route. The content of the list is irrelevant here -- the
        # question is only whether the request got past the decorator.
        patcher = mock.patch(
            'shakenfist.mariadb.get_object_events', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_stranger_is_refused_everywhere(self):
        for path in self.SUBRESOURCES:
            with self.subTest(path=path or '/'):
                self.assertEqual(404, self._get('stranger', path).status_code)

    def test_the_owner_is_allowed_everywhere(self):
        # The control. Without it, a typo in a route would look like
        # the guard working.
        for path in self.SUBRESOURCES:
            with self.subTest(path=path or '/'):
                self.assertEqual(200, self._get('owner', path).status_code)

    def test_a_trusted_namespace_is_allowed_everywhere(self):
        for path in self.SUBRESOURCES:
            with self.subTest(path=path or '/'):
                self.assertEqual(200, self._get('trusted', path).status_code)

    def test_a_stranger_is_allowed_everywhere_once_shared(self):
        self._share()
        for path in self.SUBRESOURCES:
            with self.subTest(path=path or '/'):
                self.assertEqual(200, self._get('stranger', path).status_code)


class ArtifactAccessComposesWithScopesTestCase(ArtifactAccessFixture):
    """Visibility and scope are separate gates, and both apply.

    The fix widens who may read a shared artifact, so it is worth
    stating that it did not widen anything else along the way.
    """

    def test_sharing_does_not_excuse_a_missing_scope(self):
        self._share()
        resp = self._get('stranger', scopes_granted=['instance.read'])
        self.assertEqual(403, resp.status_code)

    def test_the_scope_refusal_is_the_scope_and_not_the_sharing(self):
        self._share()
        resp = self._get('stranger', scopes_granted=['artifact.read'])
        self.assertEqual(200, resp.status_code)

    def test_the_right_scope_does_not_substitute_for_visibility(self):
        # Unshared, untrusted, correctly scoped: still nothing. Scope
        # answers "may this caller do this kind of thing", not "may
        # this caller see that object".
        resp = self._get('stranger', scopes_granted=['artifact.read'])
        self.assertEqual(404, resp.status_code)
