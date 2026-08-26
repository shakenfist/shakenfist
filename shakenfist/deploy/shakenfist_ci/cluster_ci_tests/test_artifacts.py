import json
import random
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestImages(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'images'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_cache_image(self):
        url = ('https://sfcbr.shakenfist.com/gw-basic/gwbasic.qcow2')

        img = self.system_client.cache_artifact(url)

        # Get all artifacts once to make sure we get added to the list
        image_urls = []
        for image in self.system_client.get_artifacts():
            image_urls.append(image['source_url'])
        self.addDetail('image_urls', content.text_content(json.dumps(
            image_urls, indent=4, sort_keys=True)))
        self.assertIn(url, image_urls)

        # And then just lookup the single artifact
        start_time = time.time()
        while time.time() - start_time < 7 * 60:
            img = self.system_client.get_artifact(img['uuid'])
            if img['state'] == 'created':
                return
            time.sleep(5)

        self.addDetail('img', content.text_content(json.dumps(
            img, indent=4, sort_keys=True)))
        self.fail('Image was not downloaded after seven minutes: %s'
                  % img['uuid'])

    def test_cache_invalid_image(self):
        url = ('http://nosuch.shakenfist.com/centos/6/images/'
               'CentOS-6-x86_64-GenericCloud-1604.qcow2.xz')
        img = self.system_client.cache_artifact(url)

        start_time = time.time()
        while time.time() - start_time < 7 * 60:
            img = self.system_client.get_artifact(img['uuid'])
            if img['state'] == 'error':
                # The reason for the failure must be recoverable from the
                # artifact itself, not just from its event log (issue 3899).
                # The message is written immediately after the state, so
                # keep polling briefly if it has not appeared yet.
                if img.get('error_message'):
                    return
            time.sleep(5)

        self.addDetail('img', content.text_content(json.dumps(
            img, indent=4, sort_keys=True)))
        self.fail('Image was not placed into an error state with an error '
                  'message after seven minutes: %s' % img['uuid'])

    def test_instance_invalid_image(self):
        # Start our test instance
        inst = self.test_client.create_instance(
            'nosuch', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': 'https://nosuch.shakenfist.com/foo',
                    'type': 'disk'
                }
            ], None, None)

        self.addDetail('inst', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self.assertRaises(base.StartException,
                          self._await_instance_ready, inst['uuid'])
        i = self.test_client.get_instance(inst['uuid'])
        self.addDetail('i', content.text_content(json.dumps(
            i, indent=4, sort_keys=True)))
        self.assertEqual('error', i['state'])

    def test_resize_image_too_small(self):
        inst = self.test_client.create_instance(
            'resizetoosmall', 2, 2048,
            [],
            [
                {
                    'size': 1,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        self.addDetail('inst', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self.assertIsNotNone(inst['uuid'])

        while inst['state'] in ['initial', 'preflight', 'creating']:
            time.sleep(1)
            inst = self.test_client.get_instance(inst['uuid'])

        self.addDetail('inst_final', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self.assertTrue(inst['state'] in ['creating-error', 'error'])

    def test_artifact_ref_count_label(self):
        # Use a URL not used by other tests in order to control the ref count
        url = ('https://sfcbr.shakenfist.com/gw-basic-again.qcow2')

        img = self.test_client.cache_artifact(url)

        # Get all artifacts once to make sure we get added to the list
        image_urls = []
        for image in self.test_client.get_artifacts():
            image_urls.append(image['source_url'])
        self.addDetail('image_urls', content.text_content(json.dumps(
            image_urls, indent=4, sort_keys=True)))
        self.assertIn(url, image_urls)

        # Ensure the artifact is ready
        results = self._await_artifacts_ready([img['uuid']])
        img = results[0]

        self.addDetail('img', content.text_content(json.dumps(
            img, indent=4, sort_keys=True)))
        self.assertIn('blobs', img)
        self.assertEqual(1, len(img['blobs']))
        self.assertIn(1, img['blobs'])
        self.assertIn('reference_count', img['blobs'][1])
        self.assertEqual(1, img['blobs'][1]['reference_count'])

        self.assertIn('blob_uuid', img)
        blob_uuid = img['blob_uuid']

        # Create a label artifact pointing at the blob
        label_name1 = 'test_label_01'
        lbl = self.test_client.update_label(label_name1, blob_uuid)
        self.addDetail('lbl', content.text_content(json.dumps(
            lbl, indent=4, sort_keys=True)))
        self.assertIn('blobs', lbl)
        self.assertEqual(1, len(lbl['blobs']))
        self.assertIn(1, lbl['blobs'])
        self.assertIn('reference_count', lbl['blobs'][1])
        self.assertEqual(2, lbl['blobs'][1]['reference_count'])

        # Create second label also pointing at the blob
        label_name2 = 'test_label_02'
        lbl2 = self.test_client.update_label(label_name2, blob_uuid)
        self.addDetail('lbl2', content.text_content(json.dumps(
            lbl2, indent=4, sort_keys=True)))
        self.assertIn('blobs', lbl2)
        self.assertEqual(3, lbl2['blobs'][1]['reference_count'])

        # Delete the first label
        self.assertIn('uuid', lbl)
        self.test_client.delete_artifact(lbl['uuid'])
        lbl_del = self.test_client.get_artifact(img['uuid'])
        self.addDetail('lbl_del_first', content.text_content(json.dumps(
            lbl_del, indent=4, sort_keys=True)))
        self.assertEqual(2, lbl_del['blobs'][1]['reference_count'])

        # Delete the second label
        self.assertIn('uuid', lbl2)
        self.test_client.delete_artifact(lbl2['uuid'])
        lbl_del = self.test_client.get_artifact(img['uuid'])
        self.addDetail('lbl_del_second', content.text_content(json.dumps(
            lbl_del, indent=4, sort_keys=True)))
        self.assertEqual(1, lbl_del['blobs'][1]['reference_count'])

        # Delete image artifact
        self.test_client.delete_artifact(img['uuid'])

        # Check reference count is now zero
        img_del = self.test_client.get_artifact(img['uuid'])
        self.addDetail('img_del', content.text_content(json.dumps(
            img_del, indent=4, sort_keys=True)))
        self.assertEqual(0, img_del['blobs'][1]['reference_count'])
        self.assertEqual('deleted', img_del['state'])

        # Delete image artifact again (this is idempotent)
        self.test_client.delete_artifact(img['uuid'])
        img_del = self.test_client.get_artifact(img['uuid'])
        self.addDetail('img_del_idempotent', content.text_content(json.dumps(
            img_del, indent=4, sort_keys=True)))
        self.assertEqual(0, img_del['blobs'][1]['reference_count'])

    def test_artifact_ignores_duplicate_blobs(self):
        url = ('https://sfcbr.shakenfist.com/gw-basic/gwbasic.qcow2')

        img = self.system_client.cache_artifact(url)
        results = self._await_artifacts_ready([img['uuid']])
        self.addDetail('results', content.text_content(json.dumps(
            results, indent=4, sort_keys=True)))
        self.assertEqual('created', results[0].get('state'))

        self.assertIn('blob_uuid', results[0])
        blob_uuid = results[0]['blob_uuid']

        # Create a label artifact pointing at the blob and try to use the
        # same blob twice.
        label_name = 'test_duplicate_blobs'
        lbl = self.test_client.update_label(label_name, blob_uuid)
        lbl = self.test_client.update_label(label_name, blob_uuid)
        self.addDetail('lbl', content.text_content(json.dumps(
            lbl, indent=4, sort_keys=True)))
        self.assertEqual(1, len(lbl.get('blobs')))

    def test_artifact_max_versions(self):
        def _fetch_to_blob():
            img = self.system_client.cache_artifact(
                'https://sfcbr.shakenfist.com/cgi-bin/uuid.cgi?uniq=%06d'
                % random.randint(-999999, 999999))
            results = self._await_artifacts_ready([img['uuid']])
            self.addDetail('fetch_results', content.text_content(json.dumps(
                results, indent=4, sort_keys=True)))
            self.assertEqual('created', results[0].get('state'))
            self.assertIn('blob_uuid', results[0])
            return results[0]['blob_uuid']

        # Create a label artifact pointing at the blob
        label_name = 'test_label_max_versions'
        lbl = self.test_client.update_label(label_name, _fetch_to_blob())
        self.addDetail('lbl_initial', content.text_content(json.dumps(
            lbl, indent=4, sort_keys=True)))
        self.assertIsNot(
            0, lbl.get('max_versions'),
            'Artifact uuid %s should have a version' % lbl['uuid'])

        expected_versions = lbl.get('max_versions')
        for i in range(expected_versions - 1):
            lbl = self.test_client.update_label(label_name, _fetch_to_blob())
            self.addDetail('lbl_iter_%d' % i, content.text_content(json.dumps(
                lbl, indent=4, sort_keys=True)))
            self.assertEqual(
                i + 2, len(lbl.get('blobs')),
                'Artifact uuid %s should have %d versions' % (lbl['uuid'], i + 2))

        self.addDetail('lbl_after_loop', content.text_content(json.dumps(
            lbl, indent=4, sort_keys=True)))
        self.assertEqual(expected_versions, len(lbl.get('blobs')))
        for i in range(expected_versions):
            self.assertIn(
                i + 1, lbl['blobs'],
                'Artifact uuid %s is missing blob %d' % (lbl['uuid'], i + 1))

        # Check that the blob count remains static
        lbl = self.test_client.update_label(label_name, _fetch_to_blob())
        self.addDetail('lbl_static_check1', content.text_content(json.dumps(
            lbl, indent=4, sort_keys=True)))
        self.assertEqual(expected_versions, len(lbl.get('blobs')))
        for i in range(expected_versions):
            self.assertIn(i + 2, lbl['blobs'])
        self.assertNotIn(1, lbl['blobs'])

        # Again, check that the blob count remains static
        lbl = self.test_client.update_label(label_name, _fetch_to_blob())
        self.addDetail('lbl_static_check2', content.text_content(json.dumps(
            lbl, indent=4, sort_keys=True)))
        self.assertEqual(expected_versions, len(lbl.get('blobs')))
        for i in range(expected_versions):
            self.assertIn(i + 3, lbl['blobs'])
        self.assertNotIn(1, lbl['blobs'])
        self.assertNotIn(2, lbl['blobs'])

        # Delete a version in middle of the list
        if expected_versions > 2:
            self.test_client.delete_artifact_version(lbl['uuid'], '4')

            img = self.system_client.get_artifact(lbl['uuid'])
            self.addDetail('img_after_delete', content.text_content(json.dumps(
                img, indent=4, sort_keys=True)))
            self.assertEqual(expected_versions-1, len(img['blobs']))

            # Add extra version
            lbl = self.test_client.update_label(label_name, _fetch_to_blob())
            self.addDetail('lbl_final', content.text_content(json.dumps(
                lbl, indent=4, sort_keys=True)))
            self.assertEqual(expected_versions, len(lbl.get('blobs')))
            self.assertIn(3, lbl['blobs'])


class TestSharedImages(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'sharedimages'
        super().__init__(*args, **kwargs)

    def test_sharing(self):
        url = ('https://sfcbr.shakenfist.com/gw-basic-shared.qcow2')

        # Cache a non-shared version of the image
        art = self.system_client.cache_artifact(url)

        image_urls = []
        for image in self.test_client.get_artifacts():
            image_urls.append(image['source_url'])
        self.addDetail('image_urls_non_shared', content.text_content(json.dumps(
            image_urls, indent=4, sort_keys=True)))
        self.assertNotIn(url, image_urls)

        # The listing filter and the by-uuid fetch are separate guards,
        # so the second is checked as well as the first. A uuid is not
        # a secret -- it turns up in logs, in error messages, and in
        # any API response that mentions the object -- and knowing one
        # must not be a way around the listing.
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.test_client.get_artifact, art['uuid'])

        # Cache a shared version of the image
        shared = self.system_client.cache_artifact(url, shared=True)

        image_urls = []
        for image in self.test_client.get_artifacts():
            image_urls.append(image['source_url'])
        self.addDetail('image_urls_shared', content.text_content(json.dumps(
            image_urls, indent=4, sort_keys=True)))
        self.assertIn(url, image_urls)

        # ... and now the fetch works, which is the point of sharing:
        # a tenant that can see an image in the list has to be able to
        # go and read it.
        fetched = self.test_client.get_artifact(shared['uuid'])
        self.addDetail('shared_fetch', content.text_content(json.dumps(
            fetched, indent=4, sort_keys=True)))
        self.assertEqual(url, fetched['source_url'])

        # And by name, which is what a caller who has just read the
        # listing actually has to hand. An artifact cached from a URL
        # takes its name from the last path element.
        by_name = self.test_client.get_artifact(url.split('/')[-1])
        self.assertEqual(shared['uuid'], by_name['uuid'])

        # Sharing publishes an artifact for reading. It is not a
        # transfer of ownership, so the write paths stay closed.
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.test_client.delete_artifact, shared['uuid'])

        # Try to cache a shared version when not admin
        self.assertRaises(
            apiclient.UnauthorizedException,
            self.test_client.cache_artifact, url, shared=True)


class TestTrusts(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'trusts'
        super().__init__(*args, **kwargs)

    def test_trusts(self):
        url = ('https://sfcbr.shakenfist.com/gw-basic-trust.qcow2')

        self.test_client_one = self._make_namespace(
            self.namespace + '-1', self.namespace_key)
        self.test_client_two = self._make_namespace(
            self.namespace + '-2', self.namespace_key)

        # Cache a non-shared version of the image in the first namespace
        art = self.test_client_one.cache_artifact(url)

        image_urls = []
        for image in self.test_client_two.get_artifacts():
            image_urls.append(image['source_url'])
        self.addDetail('image_urls_before_trust', content.text_content(
            json.dumps(image_urls, indent=4, sort_keys=True)))
        self.assertNotIn(url, image_urls)

        # The by-uuid fetch is checked alongside the listing at every
        # step, because it is guarded separately and the two have
        # disagreed before.
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.test_client_two.get_artifact, art['uuid'])

        # Add a trust
        self.test_client_one.add_namespace_trust(
            self.namespace + '-1', self.namespace + '-2')

        image_urls = []
        for image in self.test_client_two.get_artifacts():
            image_urls.append(image['source_url'])
        self.addDetail('image_urls_after_trust', content.text_content(
            json.dumps(image_urls, indent=4, sort_keys=True)))
        self.assertIn(url, image_urls)

        fetched = self.test_client_two.get_artifact(art['uuid'])
        self.assertEqual(url, fetched['source_url'])

        by_name = self.test_client_two.get_artifact(url.split('/')[-1])
        self.assertEqual(art['uuid'], by_name['uuid'])

        # A trust grants visibility and nothing else. Namespace two can
        # see the artifact and cannot destroy it, by uuid or by name.
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.test_client_two.delete_artifact, art['uuid'])
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.test_client_two.delete_artifact, url.split('/')[-1])
        self.assertEqual(
            url, self.test_client_two.get_artifact(art['uuid'])['source_url'])

        # Remove trust
        self.test_client_one.remove_namespace_trust(
            self.namespace + '-1', self.namespace + '-2')

        image_urls = []
        for image in self.test_client_two.get_artifacts():
            image_urls.append(image['source_url'])
        self.addDetail('image_urls_after_remove', content.text_content(
            json.dumps(image_urls, indent=4, sort_keys=True)))
        self.assertNotIn(url, image_urls)

        # Revoking a trust closes the fetch path too, and not just the
        # listing. This is the assertion that a cached authorisation
        # decision, or a guard reading a stale trust list, would fail.
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.test_client_two.get_artifact, art['uuid'])
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.test_client_two.get_artifact, url.split('/')[-1])

        self.system_client.delete_namespace(self.namespace + '-1')
        self.system_client.delete_namespace(self.namespace + '-2')


class TestLabelWriteTargets(base.BaseNamespacedTestCase):
    """Which namespace's label a POST /label is allowed to land on.

    A label may be named as `<namespace>/<label>`, and until v0.8
    nothing checked the namespace in it: any authenticated caller could
    make its blob the newest version of anybody's label, and since
    add_index ends in delete_old_versions the versions underneath went
    too. Updating one now takes the owning namespace or system.

    Creating is still additive and a trust is still enough for it. That
    split is the whole design and neither half proves the other, so
    both are asserted here, against a real cluster and real keys.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'labelwrites'
        super().__init__(*args, **kwargs)

    def _blob(self):
        """A blob to hang a label off, via a URL nothing else uses."""
        img = self.system_client.cache_artifact(
            'https://sfcbr.shakenfist.com/cgi-bin/uuid.cgi?uniq=%06d'
            % random.randint(-999999, 999999))
        results = self._await_artifacts_ready([img['uuid']])
        self.assertEqual('created', results[0].get('state'))
        return results[0]['blob_uuid']

    def test_label_write_targets(self):
        one = self.namespace + '-1'
        two = self.namespace + '-2'
        client_one = self._make_namespace(one, self.namespace_key)
        client_two = self._make_namespace(two, self.namespace_key)

        blob = self._blob()

        # One owns a label. The control: the owner reaches the write.
        lbl = client_one.update_label('%s/thing' % one, blob)
        self.addDetail('label', content.text_content(json.dumps(
            lbl, indent=4, sort_keys=True)))
        self.assertEqual(one, lbl['namespace'])

        # A stranger cannot replace it. 404 rather than 403, matching
        # every other artifact route: a caller who may not touch an
        # object should not learn that it exists.
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            client_two.update_label, '%s/thing' % one, blob)

        # Nor can somebody one trusts. This is the case which makes the
        # change more than theoretical -- a trust is set up on purpose,
        # expecting it to grant sight, and replacing what a label points
        # at is not a smaller version of being able to see it.
        client_one.add_namespace_trust(one, two)
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            client_two.update_label, '%s/thing' % one, blob)

        # But the trust does let two seed a label one does not have,
        # which is the creating-is-a-gift half of the same rule.
        created = client_two.update_label('%s/brandnew' % one, blob)
        self.assertEqual(one, created['namespace'])

        # And system can still fix a tenant's label, or it is not an
        # administrative namespace.
        fixed = self.system_client.update_label('%s/thing' % one, blob)
        self.assertEqual(one, fixed['namespace'])

        # A bare name still means one of your own, unchanged.
        mine = client_two.update_label('ownlabel', blob)
        self.assertEqual(two, mine['namespace'])

        self.system_client.delete_namespace(one)
        self.system_client.delete_namespace(two)


class TestTypoedLabel(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'sharedimages'
        super().__init__(*args, **kwargs)

    def test_typo_is_error(self):
        self.assertRaises(apiclient.ResourceNotFoundException,
                          self.test_client.create_instance,
                          'typoedlabel', 1, 1024, None,
                          [
                              {
                                  'size': 20,
                                  'base': 'label:doesnotexist',
                                  'type': 'disk'
                              }
                          ],
                          None, None)


class TestArtifactLookupByName(base.BaseNamespacedTestCase):
    """Functional tests for Artifact.from_db_by_ref name-based lookup.

    These tests verify the behaviour added in phase 2 of the SQL pushdown
    filtering plan (PLAN-sql-pushdown-filtering-phase-02-artifact.md):

    A) Same-name-different-namespace: each namespace resolves its own artifact.
    B) System-namespace cross-visibility: the system client sees at least one
       of the two artifacts when querying by the shared name.
    C) Same-name-same-namespace: skipped because the REST creation path
       de-duplicates on source_url (which embeds the namespace and name), so
       two live artifacts with the identical (namespace, name) pair cannot be
       created via the REST API. See open question #1 in the plan.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'artlookup'
        super().__init__(*args, **kwargs)

    def _upload_tiny_artifact(self, client, artifact_name):
        """Upload a 16-byte placeholder blob and convert it to a named artifact.

        Returns the artifact dict returned by the server.
        """
        # Create the upload slot
        up = client.create_upload()
        upload_uuid = up['uuid']
        self.addDetail(
            'upload_uuid_%s' % artifact_name,
            content.text_content(upload_uuid))

        # Send a tiny placeholder payload (16 null bytes)
        client.send_upload(upload_uuid, b'\x00' * 16)

        # Convert the upload to a named artifact
        art = client.upload_artifact(artifact_name, upload_uuid)
        self.addDetail(
            'artifact_%s' % artifact_name,
            content.text_content(json.dumps(art, indent=4, sort_keys=True)))
        return art

    def test_same_name_different_namespace(self):
        """Part A: two namespaces each own an artifact called 'shared-name'.

        get_artifact('shared-name') from each namespace's client must return
        the artifact that belongs to *that* namespace (verified by UUID).
        """
        artifact_name = 'shared-name'

        # Create a second namespace alongside the one provided by the base class
        ns_b_name = self.namespace + '-b'
        ns_b_key = self._uniquifier()
        client_b = self._make_namespace(ns_b_name, ns_b_key)

        try:
            # Upload the same-named artifact in namespace A (self.namespace)
            art_a = self._upload_tiny_artifact(self.test_client, artifact_name)
            self.addDetail(
                'art_a_uuid', content.text_content(art_a['uuid']))

            # Upload the same-named artifact in namespace B
            art_b = self._upload_tiny_artifact(client_b, artifact_name)
            self.addDetail(
                'art_b_uuid', content.text_content(art_b['uuid']))

            # The two artifacts must be distinct objects
            self.assertNotEqual(
                art_a['uuid'], art_b['uuid'],
                'Expected different UUIDs for same name in different namespaces')

            # Part A: each namespace client resolves its own artifact by name
            resolved_a = self.test_client.get_artifact(artifact_name)
            self.addDetail(
                'resolved_a', content.text_content(
                    json.dumps(resolved_a, indent=4, sort_keys=True)))
            self.assertEqual(
                art_a['uuid'], resolved_a['uuid'],
                'Namespace A client should resolve to its own artifact')

            resolved_b = client_b.get_artifact(artifact_name)
            self.addDetail(
                'resolved_b', content.text_content(
                    json.dumps(resolved_b, indent=4, sort_keys=True)))
            self.assertEqual(
                art_b['uuid'], resolved_b['uuid'],
                'Namespace B client should resolve to its own artifact')

            # Part B: system-namespace cross-visibility.
            #
            # When the system client queries by name both artifacts match
            # (namespace='system' means no namespace filter). The current
            # from_db_by_ref raises MultipleObjects in that situation;
            # external_api/artifact.py:arg_is_artifact_ref wraps that as a
            # 400 RequestMalformedException. We assert the no-404 outcome:
            # either a 400 (ambiguous) or a 200 (first match silently
            # returned) are acceptable here. A 404 would be a regression.
            try:
                resolved_sys = self.system_client.get_artifact(artifact_name)
                # If we reach here the API returned one result silently.
                # Assert it is one of the two known UUIDs.
                self.addDetail(
                    'resolved_sys', content.text_content(
                        json.dumps(resolved_sys, indent=4, sort_keys=True)))
                self.assertIn(
                    resolved_sys['uuid'],
                    {art_a['uuid'], art_b['uuid']},
                    'System client resolved an unexpected artifact UUID')
            except apiclient.RequestMalformedException:
                # This is the expected path after phase 2: the API surfaces
                # MultipleObjects as a 400 when the system client sees both.
                pass

        finally:
            # Clean up namespace B (namespace A is cleaned up by tearDown)
            try:
                self.system_client.delete_namespace(ns_b_name)
            except apiclient.ResourceNotFoundException:
                pass

    def test_artifact_system_creds_namespace_scoped(self):
        """System creds + explicit `namespace=` scope strictly (artifacts).

        Companion to the instance/network versions in test_object_names.
        Regression test for the bug where a system caller passing
        ``namespace=A`` could receive a same-named artifact from
        namespace B. The fix lives in `arg_is_artifact_ref`.

        The Python client `get_artifact` helper does not accept
        ``namespace=`` (the artifact endpoint historically had no
        scoped form), so this test drives the body parameter via
        ``_request_url`` directly.
        """
        artifact_name = 'shared-name'

        ns_b_name = self.namespace + '-b'
        ns_b_key = self._uniquifier()
        client_b = self._make_namespace(ns_b_name, ns_b_key)

        try:
            art_a = self._upload_tiny_artifact(
                self.test_client, artifact_name)
            art_b = self._upload_tiny_artifact(client_b, artifact_name)
            self.assertNotEqual(art_a['uuid'], art_b['uuid'])

            # System creds, explicit namespace=A → must be A's UUID.
            scoped_a = self.system_client._request_url(
                'GET', '/artifacts/' + artifact_name,
                data={'namespace': self.namespace}).json()
            self.addDetail(
                'scoped_a', content.text_content(
                    json.dumps(scoped_a, indent=4, sort_keys=True)))
            self.assertEqual(
                art_a['uuid'], scoped_a['uuid'],
                'System client with namespace=A should resolve A, not B')

            # System creds, explicit namespace=B → must be B's UUID.
            scoped_b = self.system_client._request_url(
                'GET', '/artifacts/' + artifact_name,
                data={'namespace': ns_b_name}).json()
            self.addDetail(
                'scoped_b', content.text_content(
                    json.dumps(scoped_b, indent=4, sort_keys=True)))
            self.assertEqual(
                art_b['uuid'], scoped_b['uuid'],
                'System client with namespace=B should resolve B, not A')

            # Tenant A attempting to query namespace B by name must fail.
            self.assertRaises(
                apiclient.ResourceNotFoundException,
                self.test_client._request_url,
                'GET', '/artifacts/' + artifact_name,
                data={'namespace': ns_b_name})
        finally:
            try:
                self.system_client.delete_namespace(ns_b_name)
            except apiclient.ResourceNotFoundException:
                pass


class TestBulkArtifactDelete(base.BaseNamespacedTestCase):
    """Functional test for the bulk DELETE /artifacts endpoint.

    The delete-all-artifacts-in-a-namespace form used to perform the
    deletions and then 500 while serializing its own response, because
    the uuid list it returned contained raw uuid.UUID objects (issue
    3657). This exercises the documented 200 response: a list of the
    artifact uuids that were deleted.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'bulkartdel'
        super().__init__(*args, **kwargs)

    def _upload_tiny_artifact(self, artifact_name):
        up = self.test_client.create_upload()
        self.test_client.send_upload(up['uuid'], b'\x00' * 16)
        art = self.test_client.upload_artifact(artifact_name, up['uuid'])
        self.addDetail(
            'artifact_%s' % artifact_name,
            content.text_content(json.dumps(art, indent=4, sort_keys=True)))
        return art

    def test_bulk_delete_returns_deleted_uuids(self):
        created = {
            self._upload_tiny_artifact('bulk-delete-1')['uuid'],
            self._upload_tiny_artifact('bulk-delete-2')['uuid']
        }

        deleted = self.test_client.delete_all_artifacts(self.namespace)
        self.addDetail(
            'deleted', content.text_content(json.dumps(
                deleted, indent=4, sort_keys=True)))

        self.assertEqual(created, set(deleted))
        for artifact_uuid in created:
            self.assertEqual(
                'deleted',
                self.system_client.get_artifact(artifact_uuid)['state'])
