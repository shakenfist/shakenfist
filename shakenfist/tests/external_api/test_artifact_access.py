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

    def _delete(self, namespace, ref=None):
        return self.client.delete(
            '/artifacts/%s' % (ref or self.artifact.uuid),
            headers={'Authorization': self._token(namespace)})

    def _by_name(self, namespace, name='thing', body=None):
        kwargs = {'headers': {'Authorization': self._token(namespace)}}
        if body is not None:
            kwargs['data'] = json.dumps(body)
        return self.client.get('/artifacts/%s' % name, **kwargs)


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
        # The control for the test above: the same request, from the
        # owning namespace, succeeds. Not from `trusted` -- trust
        # grants visibility and does not authorise writes, which is
        # asserted in ArtifactMutationTestCase below.
        self.assertEqual(200, self._delete('owner').status_code)


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


class ArtifactNameResolutionTestCase(ArtifactAccessFixture):
    """Looking an artifact up by the name the listing showed you.

    Visibility and resolution are different steps, and they used to
    disagree in a way that had no explanation from outside: a tenant
    could see a shared image in `GET /artifacts`, read its name, and
    then get a 404 asking for it by that name, because the name search
    never left their own namespace. Only a UUID worked.

    Widening name resolution is not free, though. Names are unique per
    namespace and nothing stops two namespaces choosing the same one,
    so the ordering matters more than the widening does: your own
    namespace must always win, or sharing an artifact called `debian`
    would silently redirect every tenant who already had one.

    And it only applies to reading. On a route that changes an
    artifact, a name means one of yours -- see the write path tests at
    the bottom of this class.
    """

    def _other(self, namespace, name='thing', shared=False):
        """Another artifact of the same name, somewhere else."""
        a = Artifact.new(
            Artifact.TYPE_OTHER, 'http://example.com/%s-%s.tgz' % (
                namespace, name),
            name=name, namespace=namespace)
        a.state = Artifact.STATE_CREATED
        if shared:
            a.shared = True
        return a

    # -- The gap being closed ------------------------------------------

    def test_a_stranger_resolves_a_shared_artifact_by_name(self):
        # The fix. Before this the listing would show `thing` and this
        # request would 404.
        self._share()
        resp = self._by_name('stranger')

        self.assertEqual(200, resp.status_code)
        self.assertEqual(str(self.artifact.uuid), resp.get_json()['uuid'])

    def test_a_trusted_namespace_resolves_by_name(self):
        resp = self._by_name('trusted')

        self.assertEqual(200, resp.status_code)
        self.assertEqual(str(self.artifact.uuid), resp.get_json()['uuid'])

    def test_the_owner_resolves_their_own_by_name(self):
        # The control. Unchanged behaviour, and if it breaks none of
        # the rest of this class means anything.
        resp = self._by_name('owner')

        self.assertEqual(200, resp.status_code)
        self.assertEqual(str(self.artifact.uuid), resp.get_json()['uuid'])

    def test_a_stranger_cannot_resolve_an_unshared_name(self):
        # Widening resolution to what you can see must not widen what
        # you can see.
        self.assertEqual(404, self._by_name('stranger').status_code)

    def test_an_unknown_name_is_still_not_found(self):
        self.assertEqual(404, self._by_name('owner', 'nosuchthing').status_code)

    # -- Ordering, which is the risky part -----------------------------

    def test_your_own_artifact_wins_over_a_shared_one(self):
        # The property that makes the widening safe to ship. A tenant
        # with their own `thing` keeps resolving to their own `thing`
        # after somebody else shares one by that name -- otherwise
        # sharing an image would silently retarget every tenant who
        # had already picked the same name for their own.
        self._share()
        mine = self._other('stranger')

        resp = self._by_name('stranger')
        self.assertEqual(200, resp.status_code)
        self.assertEqual(str(mine.uuid), resp.get_json()['uuid'])

    def test_your_own_artifact_wins_over_a_trusted_one(self):
        # The same property on the trust path. 'trusted' can see
        # owner's `thing`, but has one of its own.
        mine = self._other('trusted')

        resp = self._by_name('trusted')
        self.assertEqual(200, resp.status_code)
        self.assertEqual(str(mine.uuid), resp.get_json()['uuid'])

    def test_an_ambiguous_name_in_your_own_namespace_still_refuses(self):
        # Pre-existing behaviour, pinned because the new code path runs
        # underneath it: two of your own by one name is a 400, and the
        # widening must not "resolve" it by picking one.
        self._other('owner')

        self.assertEqual(400, self._by_name('owner').status_code)

    def test_two_visible_foreign_artifacts_are_ambiguous(self):
        # 'trusted' can see owner's `thing` through trust and
        # stranger's `thing` through sharing, and owns neither, so
        # there is no answer to give. A 400 naming the problem beats
        # picking one at random.
        self._share()
        self._other('stranger', shared=True)

        resp = self._by_name('trusted')
        self.assertEqual(400, resp.status_code)
        self.assertIn('UUID', resp.get_data(as_text=True))

    def test_the_ambiguity_resolves_by_uuid(self):
        # The control for the test above, and the escape hatch its
        # error message points at. A tenant cannot disambiguate with
        # the namespace field, because they may only name their own.
        self._share()
        self._other('stranger', shared=True)

        resp = self.client.get(
            '/artifacts/%s' % self.artifact.uuid,
            headers={'Authorization': self._token('trusted')})
        self.assertEqual(200, resp.status_code)

    # -- Naming a namespace explicitly ---------------------------------

    def test_naming_a_namespace_scopes_the_lookup(self):
        # Widening only applies when the caller left the question open.
        # A system caller who names a namespace is asking about that
        # namespace, and must not be answered from another one.
        self._share()

        self.assertEqual(
            200, self._by_name('system', body={'namespace': 'owner'}
                               ).status_code)
        self.assertEqual(
            404, self._by_name('system', body={'namespace': 'stranger'}
                               ).status_code)

    def test_a_tenant_may_not_name_a_namespace_they_do_not_own(self):
        # Pinned because this branch is the one the change reorganised.
        self._share()

        self.assertEqual(
            404, self._by_name('stranger', body={'namespace': 'owner'}
                               ).status_code)

    def test_system_resolves_by_name_unchanged(self):
        resp = self._by_name('system')

        self.assertEqual(200, resp.status_code)
        self.assertEqual(str(self.artifact.uuid), resp.get_json()['uuid'])

    # -- The write paths -----------------------------------------------

    def test_a_shared_artifact_cannot_be_deleted_by_name(self):
        # Resolution getting wider must not make authorisation wider.
        self._share()

        self.assertEqual(404, self._delete('stranger', 'thing').status_code)
        self.assertNotEqual(
            Artifact.STATE_DELETED,
            Artifact.from_db(self.artifact.uuid).state.value)

    def test_the_owner_can_still_delete_by_name(self):
        # Narrowing name resolution on the write routes did not narrow
        # it to nothing.
        self.assertEqual(200, self._delete('owner', 'thing').status_code)

    def test_a_write_route_does_not_leak_a_foreign_ambiguity(self):
        # The observable difference between the two ref decorators
        # once ownership stops honouring trust. `stranger` owns no
        # `thing` and can see two: the read route has to say "which
        # one" and answers 400, but the write route was never going to
        # accept any of them, so it answers a flat 404 rather than
        # confirming that two exist.
        self._share()
        self._other('trusted', shared=True)

        self.assertEqual(400, self._by_name('stranger').status_code)
        self.assertEqual(404, self._delete('stranger', 'thing').status_code)

    def test_a_write_route_ignores_a_shared_artifact_of_the_same_name(self):
        # `stranger` owns `thing` and can also see owner's shared
        # `thing`. On a read route that ambiguity never arises, because
        # your own namespace wins. On a write route the foreign one is
        # not a candidate at all, so this deletes stranger's and leaves
        # owner's alone.
        self._share()
        mine = self._other('stranger')

        self.assertEqual(200, self._delete('stranger', 'thing').status_code)
        self.assertEqual(
            Artifact.STATE_DELETED, Artifact.from_db(mine.uuid).state.value)
        self.assertNotEqual(
            Artifact.STATE_DELETED,
            Artifact.from_db(self.artifact.uuid).state.value)


class ArtifactMutationTestCase(ArtifactAccessFixture):
    """Trust lets you look. It does not let you touch.

    The operator guide introduces a trust as a way to get the system
    namespace's cross-namespace *sight* on a smaller scale, and being
    able to delete somebody's artifacts is not a smaller scale version
    of being able to see them. Instances and networks have always read
    this way -- `requires_instance_ownership` and
    `requires_network_ownership` both test `request_namespace() not in
    [obj.namespace, 'system']` -- and artifacts were the one object
    type where trust reached past reading.

    Every refusal below is a UUID, because a name would also have been
    refused by resolution and a refusal with two causes demonstrates
    neither.
    """

    def _mutations(self, namespace):
        """Every ownership guarded route, and what an owner should get.

        Ordered so that delete runs last, since the routes after it
        would otherwise be acting on a deleted artifact.

        `share` and `unshare` carry preconditions of their own -- only
        an artifact in the system namespace can be shared, and only a
        shared one can be unshared -- so an authorised caller reaches
        those and is refused with 403. That asymmetry is useful rather
        than awkward: 403 means the request got past the ownership
        guard, 404 means it did not, so these two routes distinguish
        the two refusals more sharply than the rest.
        """
        auth = {'Authorization': self._token(namespace)}
        u = self.artifact.uuid
        return [
            ('metadata_read', 200, lambda: self.client.get(
                '/artifacts/%s/metadata' % u, headers=auth)),
            ('metadata_write', 200, lambda: self.client.post(
                '/artifacts/%s/metadata' % u, headers=auth,
                data=json.dumps({'key': 'k', 'value': 'v'}))),
            ('max_versions', 200, lambda: self.client.post(
                '/artifacts/%s/versions' % u, headers=auth,
                data=json.dumps({'max_versions': 2}))),
            ('share', 403, lambda: self.client.post(
                '/artifacts/%s/share' % u, headers=auth)),
            ('unshare', 403, lambda: self.client.post(
                '/artifacts/%s/unshare' % u, headers=auth)),
            ('delete', 200, lambda: self.client.delete(
                '/artifacts/%s' % u, headers=auth)),
        ]

    def test_a_trusted_namespace_cannot_mutate(self):
        for name, _, call in self._mutations('trusted'):
            with self.subTest(route=name):
                self.assertEqual(404, call().status_code)

    def test_a_stranger_cannot_mutate(self):
        for name, _, call in self._mutations('stranger'):
            with self.subTest(route=name):
                self.assertEqual(404, call().status_code)

    def test_the_owner_can_mutate(self):
        # The control. Without it every 404 above could be a broken
        # route rather than a working guard.
        for name, expected, call in self._mutations('owner'):
            with self.subTest(route=name):
                self.assertEqual(expected, call().status_code)

    def test_system_can_mutate(self):
        # System has to keep working; a cluster admin who cannot clean
        # up a tenant's artifacts is not a cluster admin. This is why
        # the test is `not in [namespace, 'system']` rather than a
        # plain equality.
        for name, expected, call in self._mutations('system'):
            with self.subTest(route=name):
                self.assertEqual(expected, call().status_code)

    def test_a_negative_max_versions_is_refused(self):
        """A negative maximum is silently destructive rather than
        merely meaningless: delete_old_versions() tests len(indexes) >
        max, always true for a negative, and then slices [:-max], so
        every index add deletes the oldest surviving version. The
        declaration publishes minimum 0 and this is the server backing
        it."""
        auth = {'Authorization': self._token('owner')}
        resp = self.client.post(
            '/artifacts/%s/versions' % self.artifact.uuid, headers=auth,
            data=json.dumps({'max_versions': -1}))
        self.assertEqual(400, resp.status_code)

    def test_an_unparsable_max_versions_is_a_400_not_a_500(self):
        # int() raises TypeError, not ValueError, for a list or dict,
        # so this path used to serve a 500.
        auth = {'Authorization': self._token('owner')}
        for value in (['two'], {'two': 2}, 'two'):
            with self.subTest(value=value):
                resp = self.client.post(
                    '/artifacts/%s/versions' % self.artifact.uuid,
                    headers=auth, data=json.dumps({'max_versions': value}))
                self.assertEqual(400, resp.status_code)

    def test_a_trusted_namespace_can_still_read(self):
        # The whole point: nothing about visibility changed. Trust
        # still opens the read paths it always did.
        self.assertEqual(200, self._get('trusted').status_code)
        self.assertIn(
            str(self.artifact.uuid),
            [a['uuid'] for a in self.client.get(
                '/artifacts',
                headers={'Authorization': self._token('trusted')}
            ).get_json()])

    def test_the_artifact_survives_a_refused_delete(self):
        # Asserted on the object rather than the status code, because
        # a guard that returns 404 after doing the work would pass
        # every other test in this class.
        self.client.delete(
            '/artifacts/%s' % self.artifact.uuid,
            headers={'Authorization': self._token('trusted')})

        self.assertNotEqual(
            Artifact.STATE_DELETED,
            Artifact.from_db(self.artifact.uuid).state.value)


class ArtifactWriteTargetTestCase(ArtifactAccessFixture):
    """Which artifact a write route is allowed to land on.

    The routes above take a uuid, so the ownership check has something
    to check. The upload and cache routes instead resolve a
    caller-supplied url, and they used to resolve it with
    `Artifact.from_url`, whose predicate is *visibility*. A trusted
    namespace could therefore name the owner's source_url, land on the
    owner's artifact, and have its own blob added as the newest version
    -- and `add_index` ends in `delete_old_versions`, so the owner's
    older versions went with it.

    Resolution is by ownership now, so there are two distinct outcomes
    and both matter. A caller who names somebody else's namespace is
    refused outright. A caller who names nobody's lands on an artifact in
    its *own* namespace even when the url collides with the owner's --
    source_url is not a cluster wide key, and owning an artifact that
    claims a url somebody else also claims has never been forbidden.

    Refusals are distinguished by *reason* rather than status, because
    both outcomes are a 404 here: a caller turned away at the gate is
    told the namespace does not exist, while one who gets past it goes on
    to fail on the deliberately absent blob. Comparing the two proves the
    gate moved rather than merely that something returned 404.
    """

    MISSING_BLOB = '9f0f3e64-4b64-4b9f-9f2c-0f5e0a2c9a01'

    def _upload(self, requestor, target=None, source_url=None, name='thing'):
        # The blob is made absent on purpose. It is the marker for
        # "authorisation passed": the lookup sits inside the artifact
        # lock, well past the gate, so only a caller entitled to write
        # ever sees 'blob not found'. Everybody else is turned away
        # earlier with 'namespace not found' and never reaches it.
        body = {
            'blob_uuid': self.MISSING_BLOB,
            'source_url': source_url or self.artifact.source_url,
            'artifact_type': 'other'}
        if target:
            body['namespace'] = target

        with mock.patch(
                'shakenfist.external_api.artifact.Blob.from_db',
                return_value=None):
            return self.client.post(
                '/artifacts/upload/%s' % name,
                headers={'Authorization': self._token(requestor)},
                data=json.dumps(body))

    def _indexes(self):
        return len(list(
            Artifact.from_db(self.artifact.uuid).get_all_indexes()))

    def test_the_owner_reaches_the_write(self):
        # The control. 'blob not found' means authorisation passed and
        # the route got as far as looking for the blob, which is as far
        # as this test wants it to get.
        resp = self._upload('owner')
        self.assertEqual(404, resp.status_code)
        self.assertEqual('blob not found', resp.get_json()['error'])

    def test_system_reaches_the_write(self):
        resp = self._upload('system')
        self.assertEqual(404, resp.status_code)
        self.assertEqual('blob not found', resp.get_json()['error'])

    def test_a_trusted_namespace_cannot_write_to_the_owners_namespace(self):
        # The bug. Naming the owner's namespace used to resolve the
        # owner's artifact and then pass the trust check, so a trusted
        # caller's blob became the newest version of it.
        resp = self._upload('trusted', target='owner')
        self.assertEqual(404, resp.status_code)
        self.assertEqual('namespace not found', resp.get_json()['error'])

    def test_a_stranger_cannot_write_to_the_owners_namespace(self):
        resp = self._upload('stranger', target='owner')
        self.assertEqual(404, resp.status_code)
        self.assertEqual('namespace not found', resp.get_json()['error'])

    def test_sharing_does_not_make_an_artifact_writable(self):
        # Sharing is the other half of the visibility predicate that used
        # to be consulted here, so it gets its own case even though the
        # trust check refused this one already.
        self._share()
        resp = self._upload('trusted', target='owner')
        self.assertEqual(404, resp.status_code)
        self.assertEqual('namespace not found', resp.get_json()['error'])

    def test_a_colliding_url_lands_in_the_callers_own_namespace(self):
        # Not a refusal, and should not be one. The caller named no
        # namespace, so the write goes to an artifact of its own -- even
        # though the url collides with the owner's, because source_url is
        # not a cluster wide key.
        resp = self._upload('trusted')
        self.assertEqual('blob not found', resp.get_json()['error'])

        mine = Artifact.owned_from_url(
            Artifact.TYPE_OTHER, self.artifact.source_url,
            namespace='trusted')
        self.assertIsNotNone(mine)
        self.assertNotEqual(str(self.artifact.uuid), str(mine.uuid))

    def test_the_owners_artifact_gains_no_version_from_a_collision(self):
        # The property the reasons above stand in for. Asserted on the
        # object, because a route which wrote and then refused would
        # satisfy every status assertion in this class.
        before = self._indexes()
        self._upload('trusted')
        self._upload('trusted', target='owner')
        self.assertEqual(before, self._indexes())

    def test_the_owners_artifact_gains_no_event_from_a_refusal(self):
        # The audit event used to be written before the authorisation
        # check, which let a refused caller append to the event log of a
        # namespace it was about to be told does not exist.
        with mock.patch.object(Artifact, 'add_event') as add_event:
            self._upload('trusted', target='owner')
        add_event.assert_not_called()

    def test_the_event_is_still_written_when_the_write_is_allowed(self):
        # The control for the ordering change. Moving the event after the
        # gate must not lose it for callers who pass.
        with mock.patch.object(Artifact, 'add_event') as add_event:
            self._upload('owner')
        add_event.assert_called()
