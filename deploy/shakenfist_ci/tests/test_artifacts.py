import random
import time

from shakenfist_client import apiclient

from shakenfist_ci import base


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
        self.assertIn(url, image_urls)

        # And then just lookup the single artifact
        start_time = time.time()
        while time.time() - start_time < 7 * 60:
            img = self.system_client.get_artifact(img['uuid'])
            if img['state'] == 'created':
                return
            time.sleep(5)

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
                return
            time.sleep(5)

        self.fail('Image was not placed into an error state after seven minutes: %s'
                  % img['uuid'])

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

        self.assertRaises(base.StartException,
                          self._await_instance_ready, inst['uuid'])
        i = self.test_client.get_instance(inst['uuid'])
        self.assertEqual('error', i['state'])

    def test_resize_image_to_small(self):
        inst = self.test_client.create_instance(
            'resizetoosmall', 2, 2048,
            [],
            [
                {
                    'size': 1,
                    'base': 'sf://upload/system/ubuntu-2004',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])

        while inst['state'] in ['initial', 'preflight', 'creating']:
            time.sleep(1)
            inst = self.test_client.get_instance(inst['uuid'])

        self.assertTrue(inst['state'] in ['creating-error', 'error'])

    def test_artifact_ref_count_label(self):
        # Use a URL not used by other tests in order to control the ref count
        url = ('https://sfcbr.shakenfist.com/gw-basic-again.qcow2')

        img = self.test_client.cache_artifact(url)

        # Get all artifacts once to make sure we get added to the list
        image_urls = []
        for image in self.test_client.get_artifacts():
            image_urls.append(image['source_url'])
        self.assertIn(url, image_urls)

        # Ensure the artifact is ready
        results = self._await_artifacts_ready([img['uuid']])
        img = results[0]

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
        self.assertIn('blobs', lbl)
        self.assertEqual(1, len(lbl['blobs']))
        self.assertIn(1, lbl['blobs'])
        self.assertIn('reference_count', lbl['blobs'][1])
        self.assertEqual(2, lbl['blobs'][1]['reference_count'])

        # Create second label also pointing at the blob
        label_name2 = 'test_label_02'
        lbl2 = self.test_client.update_label(label_name2, blob_uuid)
        self.assertIn('blobs', lbl2)
        self.assertEqual(3, lbl2['blobs'][1]['reference_count'])

        # Delete the first label
        self.assertIn('uuid', lbl)
        self.test_client.delete_artifact(lbl['uuid'])
        lbl_del = self.test_client.get_artifact(img['uuid'])
        self.assertEqual(2, lbl_del['blobs'][1]['reference_count'])

        # Delete the second label
        self.assertIn('uuid', lbl2)
        self.test_client.delete_artifact(lbl2['uuid'])
        lbl_del = self.test_client.get_artifact(img['uuid'])
        self.assertEqual(1, lbl_del['blobs'][1]['reference_count'])

        # Delete image artifact
        self.test_client.delete_artifact(img['uuid'])

        # Check reference count is now zero
        img_del = self.test_client.get_artifact(img['uuid'])
        self.assertEqual(0, img_del['blobs'][1]['reference_count'])
        self.assertEqual('deleted', img_del['state'])

        # Delete image artifact again (this is idempotent)
        self.test_client.delete_artifact(img['uuid'])
        img_del = self.test_client.get_artifact(img['uuid'])
        self.assertEqual(0, img_del['blobs'][1]['reference_count'])

    def test_artifact_ignores_duplicate_blobs(self):
        url = ('https://sfcbr.shakenfist.com/gw-basic/gwbasic.qcow2')

        img = self.system_client.cache_artifact(url)
        results = self._await_artifacts_ready([img['uuid']])
        self.assertEqual('created', results[0].get('state'))

        self.assertIn('blob_uuid', results[0])
        blob_uuid = results[0]['blob_uuid']

        # Create a label artifact pointing at the blob and try to use the
        # same blob twice.
        label_name = 'test_duplicate_blobs'
        lbl = self.test_client.update_label(label_name, blob_uuid)
        lbl = self.test_client.update_label(label_name, blob_uuid)
        self.assertEqual(1, len(lbl.get('blobs')))

    def test_artifact_max_versions(self):
        def _fetch_to_blob():
            img = self.system_client.cache_artifact(
                'https://sfcbr.shakenfist.com/cgi-bin/uuid.cgi?uniq=%06d'
                % random.randint(-999999, 999999))
            results = self._await_artifacts_ready([img['uuid']])
            self.assertEqual('created', results[0].get('state'))
            self.assertIn('blob_uuid', results[0])
            return results[0]['blob_uuid']

        # Create a label artifact pointing at the blob
        label_name = 'test_label_max_versions'
        lbl = self.test_client.update_label(label_name, _fetch_to_blob())
        self.assertIsNot(
            0, lbl.get('max_versions'),
            'Artifact uuid %s should have a version' % lbl['uuid'])

        expected_versions = lbl.get('max_versions')
        for i in range(expected_versions - 1):
            lbl = self.test_client.update_label(label_name, _fetch_to_blob())
            self.assertEqual(
                i + 2, len(lbl.get('blobs')),
                'Artifact uuid %s should have %d versions' % (lbl['uuid'], i + 2))

        self.assertEqual(expected_versions, len(lbl.get('blobs')))
        for i in range(expected_versions):
            self.assertIn(
                i + 1, lbl['blobs'],
                'Artifact uuid %s is missing blob %d' % (lbl['uuid'], i + 1))

        # Check that the blob count remains static
        lbl = self.test_client.update_label(label_name, _fetch_to_blob())
        self.assertEqual(expected_versions, len(lbl.get('blobs')))
        for i in range(expected_versions):
            self.assertIn(i + 2, lbl['blobs'])
        self.assertNotIn(1, lbl['blobs'])

        # Again, check that the blob count remains static
        lbl = self.test_client.update_label(label_name, _fetch_to_blob())
        self.assertEqual(expected_versions, len(lbl.get('blobs')))
        for i in range(expected_versions):
            self.assertIn(i + 3, lbl['blobs'])
        self.assertNotIn(1, lbl['blobs'])
        self.assertNotIn(2, lbl['blobs'])

        # Delete a version in middle of the list
        if expected_versions > 2:
            self.test_client.delete_artifact_version(lbl['uuid'], '4')

            img = self.system_client.get_artifact(lbl['uuid'])
            self.assertEqual(expected_versions-1, len(img['blobs']))

            # Add extra version
            lbl = self.test_client.update_label(label_name, _fetch_to_blob())
            self.assertEqual(expected_versions, len(lbl.get('blobs')))
            self.assertIn(3, lbl['blobs'])


class TestSharedImages(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'sharedimages'
        super().__init__(*args, **kwargs)

    def test_sharing(self):
        url = ('https://sfcbr.shakenfist.com/gw-basic-shared.qcow2')

        # Cache a non-shared version of the image
        self.system_client.cache_artifact(url)

        image_urls = []
        for image in self.test_client.get_artifacts():
            image_urls.append(image['source_url'])
        self.assertNotIn(url, image_urls)

        # Cache a shared version of the image
        self.system_client.cache_artifact(url, shared=True)

        image_urls = []
        for image in self.test_client.get_artifacts():
            image_urls.append(image['source_url'])
        self.assertIn(url, image_urls)

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
        self.test_client_one.cache_artifact(url)

        image_urls = []
        for image in self.test_client_two.get_artifacts():
            image_urls.append(image['source_url'])
        self.assertNotIn(url, image_urls)

        # Add a trust
        self.test_client_one.add_namespace_trust(
            self.namespace + '-1', self.namespace + '-2')

        image_urls = []
        for image in self.test_client_two.get_artifacts():
            image_urls.append(image['source_url'])
        self.assertIn(url, image_urls)

        # Remove trust
        self.test_client_one.remove_namespace_trust(
            self.namespace + '-1', self.namespace + '-2')

        image_urls = []
        for image in self.test_client_two.get_artifacts():
            image_urls.append(image['source_url'])
        self.assertNotIn(url, image_urls)

        self.system_client.delete_namespace(self.namespace + '-1')
        self.system_client.delete_namespace(self.namespace + '-2')


class TestTypoedLabel(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'typolabel'
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
                          None, None, side_channels=['sf-agent'])


class TestArtifactUndelete(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'undeleteartifact'
        super().__init__(*args, **kwargs)

    def test_artifacts_can_undelete(self):
        # We should be able to transition from created to soft deleted
        # and back to created. If the artifact has been hard deleted, then
        # re-creation would just treat it as new.
        url = ('https://sfcbr.shakenfist.com/gw-basic-shared.qcow2')
        img = self.test_client.cache_artifact(url)
        orig_img_uuid = img['uuid']
        self._await_artifacts_ready([orig_img_uuid])

        # Delete the artifact. The API should return for this immediately.
        img = self.test_client.delete_artifact(orig_img_uuid)
        self.assertEqual('deleted', img['state'], img)

        img = self.test_client.cache_artifact(url)
        self.assertEqual(orig_img_uuid, img['uuid'])

        start_time = time.time()
        while time.time() - start_time < 300:
            img = self.test_client.get_artifact(img['uuid'])
            if img['state'] == 'created':
                break
            time.sleep(5)
        self.assertEqual('created', img['state'], img)

        self.fail(img['uuid'])
