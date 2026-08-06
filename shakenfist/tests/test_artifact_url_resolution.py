"""The three ways a URL resolves to an artifact, and why there are three.

`from_url` resolves by *visibility*: shared artifacts and those owned by
a namespace which trusts you are legitimate answers, because a caller
who wants to read one or boot from one should be able to.

`owned_from_url` resolves by *ownership*, and returns None rather than
creating. Adding a version is not a read: `add_index` ends in
`delete_old_versions`, so landing a write on somebody else's artifact
destroys the versions underneath it. Not creating is deliberate --
routes which take a caller-nominated namespace have two cases to
authorise apart, because a trust is enough to gift a namespace an
artifact it lacked and not enough to replace one it has.

`owned_from_url_or_new` is that pair collapsed, for the callers which
genuinely have one case: the target namespace is fixed, so whoever may
write may equally create.

The distinction is only worth anything if the resolvers actually differ
on the same inputs, so each case here is asserted against more than one
of them.
"""

from unittest import mock

from shakenfist.artifact import Artifact
from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


URL = 'https://example.com/an-image.qcow2'


class ArtifactUrlResolutionTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        for ns in ['system', 'owner', 'trusted', 'stranger']:
            self.mock_mariadb.create_namespace(ns, 'key1', '%skey' % ns)

        Namespace.from_db('owner').add_trust('trusted')

        self.theirs = Artifact.new(
            Artifact.TYPE_IMAGE, URL, name='an-image', namespace='owner')
        self.theirs.state = Artifact.STATE_CREATED

    # owned_from_url: never somebody else's, ever.

    def test_a_shared_artifact_is_visible_but_not_ours(self):
        # The pair that matters. from_url finds it, because a shared
        # artifact is a perfectly good thing to boot from; owned_from_url
        # does not, because it is not ours to write to.
        self.theirs.shared = True

        self.assertIsNotNone(
            Artifact.from_url(Artifact.TYPE_IMAGE, URL, namespace='stranger'))
        self.assertIsNone(
            Artifact.owned_from_url(
                Artifact.TYPE_IMAGE, URL, namespace='stranger'))

    def test_a_trust_grants_visibility_and_not_ownership(self):
        # The other half of the same point. `trusted` is trusted by
        # `owner`, which is why from_url resolves for it.
        self.assertIsNotNone(
            Artifact.from_url(Artifact.TYPE_IMAGE, URL, namespace='trusted'))
        self.assertIsNone(
            Artifact.owned_from_url(
                Artifact.TYPE_IMAGE, URL, namespace='trusted'))

    def test_naming_system_does_not_reach_a_foreign_artifact(self):
        # There is no system escape in the ownership predicate, because
        # its argument is the target namespace rather than the
        # requestor. Asking for the system namespace's artifact for this
        # URL when system has none is None, not owner's.
        #
        # This is also the regression for pushing the namespace down to
        # SQL: the iterator drops a namespace criterion of 'system' so
        # that listing as system sees the whole cluster, so a pushdown
        # without the Python predicate still standing behind it would
        # answer owner's artifact here.
        self.assertIsNone(
            Artifact.owned_from_url(
                Artifact.TYPE_IMAGE, URL, namespace='system'))

    def test_our_own_artifact_resolves(self):
        # The control. Ownership resolution has to still find the
        # ordinary case, or every test above passes for the wrong reason.
        a = Artifact.owned_from_url(
            Artifact.TYPE_IMAGE, URL, namespace='owner')
        self.assertIsNotNone(a)
        self.assertEqual(str(self.theirs.uuid), str(a.uuid))

    def test_ownership_resolution_does_not_create(self):
        # Why the _or_new variant has to exist separately.
        self.assertIsNone(
            Artifact.owned_from_url(
                Artifact.TYPE_IMAGE, URL, namespace='stranger'))
        self.assertIsNone(
            Artifact.owned_from_url(
                Artifact.TYPE_IMAGE, URL, namespace='stranger'))

    def test_the_namespace_is_pushed_down_to_sql(self):
        # Ownership is a plain equality on an indexed column and this
        # runs on the instance create path, so it belongs in the query
        # rather than in a Python predicate over every artifact row.
        with mock.patch('shakenfist.artifact.mariadb.find_artifacts',
                        return_value=[]) as find:
            Artifact.owned_from_url(
                Artifact.TYPE_IMAGE, URL, namespace='owner')

        find.assert_called()
        self.assertEqual('owner', find.call_args.args[0].namespace)

    def test_visibility_resolution_does_not_push_the_namespace_down(self):
        # The counterpart, and the reason the pushdown is on one
        # resolver and not in _resolve_url. Visibility is a trust graph
        # walk, so narrowing the query to the caller's own namespace
        # would drop exactly the shared and trusted rows it exists to
        # find.
        with mock.patch('shakenfist.artifact.mariadb.find_artifacts',
                        return_value=[]) as find:
            Artifact.from_url(Artifact.TYPE_IMAGE, URL, namespace='owner')

        find.assert_called()
        self.assertIsNone(find.call_args.args[0].namespace)

    # owned_from_url_or_new: creates, but never onto somebody else's.

    def test_or_new_returns_the_artifact_we_already_own(self):
        a = Artifact.owned_from_url_or_new(
            Artifact.TYPE_IMAGE, URL, namespace='owner')
        self.assertEqual(str(self.theirs.uuid), str(a.uuid))

    def test_or_new_creates_rather_than_taking_a_shared_one(self):
        # The case which would be a security bug if it resolved: a
        # shared artifact is visible to everybody, and this is a write
        # path.
        self.theirs.shared = True

        a = Artifact.owned_from_url_or_new(
            Artifact.TYPE_IMAGE, URL, namespace='stranger')
        self.assertNotEqual(str(self.theirs.uuid), str(a.uuid))
        self.assertEqual('stranger', a.namespace)

    def test_or_new_creates_rather_than_taking_a_trusted_one(self):
        a = Artifact.owned_from_url_or_new(
            Artifact.TYPE_IMAGE, URL, namespace='trusted')
        self.assertNotEqual(str(self.theirs.uuid), str(a.uuid))
        self.assertEqual('trusted', a.namespace)

    def test_or_new_passes_the_name_and_version_count_through(self):
        # Instance.snapshot() names its artifact `<uuid>/<device>` and
        # sets max_versions from the request, which the resolver has to
        # carry or the snapshot route silently loses both on the create.
        a = Artifact.owned_from_url_or_new(
            Artifact.TYPE_SNAPSHOT, 'sf://instance/an-instance/vda',
            name='an-instance/vda', max_versions=3, namespace='owner')

        self.assertEqual('an-instance/vda', a.name)
        self.assertEqual(3, a.max_versions)
