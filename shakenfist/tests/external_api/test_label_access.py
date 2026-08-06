"""Which namespace's label a request to /label is allowed to land on.

A label reference may be written bare, meaning one of your own, or as
`<namespace>/<label>`, meaning somebody else's. `_label_url` honours the
second form and hands back the namespace it was given, so the namespace
this endpoint acts on is chosen by the requestor.

Nothing checked it. The `requires_admin=True` in the swagger helper is
prose appended to the generated description and enforces nothing, and
the route carries no ownership decorator, so any authenticated caller
holding `label.write` -- which every legacy unscoped key does, via the
wildcard -- could POST to `/label/<somebody else>/<label>` and have its
blob become the newest version of their label. `add_index` ends in
`delete_old_versions`, so the versions underneath went with it. The
operator guide says the opposite in as many words: a shared artifact
"should not be able to [be updated by] non-system namespaces".

The read and delete halves of the same endpoint were unreachable rather
than unguarded. `_label_url` returns a pair, and both handed the whole
pair to `url_filter`, which compares it against a string -- so nothing
ever matched, and the resulting 404 was computed but not returned,
leaving `get` to raise IndexError and `delete` to raise NameError. Both
answered 500 to every request. Their guards are asserted here too,
because a route being fixed is exactly when it acquires the ability to
be wrong in the more interesting way.

Refusals are 404 and not 403 throughout, matching every other artifact
route: a caller who may not touch an object should not learn it exists.
"""

import json
from unittest import mock

from shakenfist import mariadb
from shakenfist.artifact import LABEL_URL
from shakenfist.artifact import Artifact
from shakenfist.external_api import app as external_api
from shakenfist.namespace import Namespace
from shakenfist.namespace_key import NamespaceKey
from shakenfist.schema.namespace_key_attributes import (
    NamespaceKeyAttributesData)
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class LabelAccessFixture(base.ShakenFistTestCase):
    """A label owned by `owner`, and three other points of view.

    `trusted` is trusted by owner, `stranger` is not, and system is
    trusted by everybody whether they like it or not.
    """

    BLOB_UUID = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        for ns in ['system', 'owner', 'trusted', 'stranger']:
            self.mock_mariadb.create_namespace(ns, 'key1', '%skey' % ns)

        Namespace.from_db('owner').add_trust('trusted')

        self.client = external_api.app.test_client()

        self.label = Artifact.new(
            Artifact.TYPE_LABEL, '%sowner/thing' % LABEL_URL, name='thing',
            namespace='owner')
        self.label.state = Artifact.STATE_CREATED

    def _token(self, namespace, scopes_granted=None):
        """A token for namespace, optionally scoped.

        Unscoped keys get the legacy wildcard, which is what almost
        every caller in the wild still holds -- and therefore the
        interesting case, since a wildcard satisfies `label.write`.
        """
        tag = 'wildcard' if scopes_granted is None else (
            '-'.join(sorted(scopes_granted)) or 'noscopes')
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

    def _post(self, requestor, label_name='owner/thing'):
        """Update a label, with add_index stubbed out.

        The write itself needs a real blob and a real hash, neither of
        which this harness has. Patching `add_index` is not a way around
        that so much as the assertion itself: whether the request
        reached the write, and which artifact it would have written to,
        is the entire question this file asks. autospec keeps `self`
        in the recorded call so the second half can be answered.
        """
        with mock.patch.object(Artifact, 'add_index', autospec=True) as ai:
            resp = self.client.post(
                '/label/%s' % label_name,
                headers={'Authorization': self._token(requestor)},
                data=json.dumps({'blob_uuid': self.BLOB_UUID}))
        return resp, ai

    def _get(self, requestor, label_name='owner/thing'):
        return self.client.get(
            '/label/%s' % label_name,
            headers={'Authorization': self._token(requestor)})

    def _delete(self, requestor, label_name='owner/thing'):
        return self.client.delete(
            '/label/%s' % label_name,
            headers={'Authorization': self._token(requestor)})

    def _labels_owned_by(self, namespace, name='thing'):
        return Artifact.owned_from_url(
            Artifact.TYPE_LABEL, '%s%s/%s' % (LABEL_URL, namespace, name),
            namespace=namespace)


class LabelWriteTargetTestCase(LabelAccessFixture):
    """POST /label/<namespace>/<label>."""

    def test_the_owner_reaches_the_write(self):
        # The control. Without it every refusal below could be a broken
        # route rather than a working guard.
        resp, add_index = self._post('owner')
        self.assertEqual(200, resp.status_code, resp.get_json())
        add_index.assert_called_once()
        self.assertEqual(str(self.label.uuid),
                         str(add_index.call_args[0][0].uuid))

    def test_system_reaches_the_write(self):
        # A cluster admin who cannot fix a tenant's label is not a
        # cluster admin, so system keeps the door it has everywhere
        # else.
        resp, add_index = self._post('system')
        self.assertEqual(200, resp.status_code, resp.get_json())
        add_index.assert_called_once()

    def test_a_stranger_cannot_write_to_the_owners_label(self):
        # The bug, in its plainest form. `stranger` has no relationship
        # with `owner` at all and could still name the label.
        resp, add_index = self._post('stranger')
        self.assertEqual(404, resp.status_code)
        self.assertEqual('namespace not found', resp.get_json()['error'])
        add_index.assert_not_called()

    def test_a_trusted_namespace_cannot_write_to_the_owners_label(self):
        # Trust is what makes this more than a theoretical hole: it is
        # a relationship an operator sets up on purpose, expecting it
        # to grant sight. Replacing what a label points at is not a
        # smaller version of being able to see it.
        resp, add_index = self._post('trusted')
        self.assertEqual(404, resp.status_code)
        self.assertEqual('namespace not found', resp.get_json()['error'])
        add_index.assert_not_called()

    def test_sharing_does_not_make_a_label_writable(self):
        # Sharing is the other half of the visibility predicate that
        # used to be consulted here, so it gets a case of its own even
        # though the trust check refused the one above already.
        self.label.shared = True
        resp, add_index = self._post('stranger')
        self.assertEqual(404, resp.status_code)
        add_index.assert_not_called()

    def test_a_bare_name_still_lands_in_the_callers_namespace(self):
        # The ordinary case, which is the one that must not have been
        # narrowed: `stranger` updating `thing` gets an artifact of its
        # own, even though `owner` has a label by that name.
        resp, add_index = self._post('stranger', label_name='thing')
        self.assertEqual(200, resp.status_code, resp.get_json())

        mine = self._labels_owned_by('stranger')
        self.assertIsNotNone(mine)
        self.assertNotEqual(str(self.label.uuid), str(mine.uuid))
        self.assertEqual(str(mine.uuid), str(add_index.call_args[0][0].uuid))

    def test_a_trusted_namespace_may_create_a_label_that_does_not_exist(self):
        # Creating is additive, and a trust is enough for it -- the
        # same split the artifact upload route makes, and what the
        # operator guide promises a trust is for. Only replacing what
        # an existing label resolves to takes ownership.
        resp, _ = self._post('trusted', label_name='owner/brandnew')
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertIsNotNone(self._labels_owned_by('owner', 'brandnew'))

    def test_a_stranger_may_not_create_in_another_namespace(self):
        # The control for the test above: it is the trust doing that,
        # not the absence of an existing label.
        resp, _ = self._post('stranger', label_name='owner/brandnew')
        self.assertEqual(404, resp.status_code)
        self.assertIsNone(self._labels_owned_by('owner', 'brandnew'))

    def test_the_owners_label_gains_no_event_from_a_refusal(self):
        # Asserted on the object rather than the status code, because a
        # route which wrote and then refused would satisfy every status
        # assertion above.
        with mock.patch.object(Artifact, 'add_event') as add_event:
            self._post('trusted')
        add_event.assert_not_called()

    def test_the_event_is_still_written_when_the_write_is_allowed(self):
        with mock.patch.object(Artifact, 'add_event') as add_event:
            self._post('owner')
        add_event.assert_called()


class LabelReadTestCase(LabelAccessFixture):
    """GET /label/<namespace>/<label>.

    Reading resolves by visibility, so this is the one verb where a
    share or a trust is meant to open the door.
    """

    def test_the_owner_can_read_their_own_label(self):
        resp = self._get('owner')
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(str(self.label.uuid), resp.get_json()['uuid'])

    def test_a_trusted_namespace_can_read_it(self):
        resp = self._get('trusted')
        self.assertEqual(200, resp.status_code)
        self.assertEqual(str(self.label.uuid), resp.get_json()['uuid'])

    def test_a_stranger_cannot_read_an_unshared_label(self):
        self.assertEqual(404, self._get('stranger').status_code)

    def test_a_stranger_can_read_a_shared_label(self):
        self.label.shared = True
        self.assertEqual(200, self._get('stranger').status_code)

    def test_system_can_read_it(self):
        self.assertEqual(200, self._get('system').status_code)

    def test_an_unknown_label_is_404_and_not_500(self):
        # The direct regression for the unreturned error: the 404 was
        # built and dropped, and the endpoint fell through to an
        # IndexError.
        resp = self._get('owner', label_name='owner/nosuchthing')
        self.assertEqual(404, resp.status_code)
        self.assertIn('not found', resp.get_json()['error'])


class LabelDeleteTestCase(LabelAccessFixture):
    """DELETE /label/<namespace>/<label>.

    Deleting resolves by ownership, like every other route that changes
    an artifact.
    """

    def _state(self):
        return Artifact.from_db(self.label.uuid).state.value

    def test_the_owner_can_delete_their_own_label(self):
        resp = self._delete('owner')
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(Artifact.STATE_DELETED, self._state())

    def test_system_can_delete_it(self):
        self.assertEqual(200, self._delete('system').status_code)
        self.assertEqual(Artifact.STATE_DELETED, self._state())

    def test_a_trusted_namespace_cannot_delete_it(self):
        self.assertEqual(404, self._delete('trusted').status_code)
        self.assertNotEqual(Artifact.STATE_DELETED, self._state())

    def test_a_stranger_cannot_delete_it(self):
        self.assertEqual(404, self._delete('stranger').status_code)
        self.assertNotEqual(Artifact.STATE_DELETED, self._state())

    def test_sharing_does_not_grant_delete(self):
        self.label.shared = True
        self.assertEqual(404, self._delete('stranger').status_code)
        self.assertNotEqual(Artifact.STATE_DELETED, self._state())

    def test_an_unknown_label_is_404_and_not_500(self):
        # The delete half of the unreturned error: this used to fall
        # through to a NameError on the loop variable.
        resp = self._delete('owner', label_name='owner/nosuchthing')
        self.assertEqual(404, resp.status_code)
        self.assertIn('not found', resp.get_json()['error'])
