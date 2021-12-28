import time

from shakenfist_ci import base


class TestImages(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'images'
        super(TestImages, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestImages, self).setUp()
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
        self._await_image_download_error(img['uuid'], after=time.time())

    def test_instance_invalid_image(self):
        # Start our test instance
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
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
                          self._await_login_prompt, inst['uuid'])
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

        img = self.system_client.cache_artifact(url)

        # Get all artifacts once to make sure we get added to the list
        image_urls = []
        for image in self.system_client.get_artifacts():
            image_urls.append(image['source_url'])
        self.assertIn(url, image_urls)

        # Ensure the artifact is ready
        results = self._await_artifacts_ready([img['uuid']])
        img = results[0]

        self.assertIn('blobs', img)
        self.assertEqual(1, len(img['blobs']))
        self.assertIn('1', img['blobs'])
        self.assertIn('reference_count', img['blobs']['1'])
        self.assertEqual(1, img['blobs']['1']['reference_count'])

        self.assertIn('blob_uuid', img)
        blob_uuid = img['blob_uuid']

        # Create a label artifact pointing at the blob
        label_name1 = 'test_label_01'
        lbl = self.test_client.update_label(label_name1, blob_uuid)
        self.assertIn('blobs', lbl)
        self.assertEqual(1, len(lbl['blobs']))
        self.assertIn('1', lbl['blobs'])
        self.assertIn('reference_count', lbl['blobs']['1'])
        self.assertEqual(2, lbl['blobs']['1']['reference_count'])

        # Create second label also pointing at the blob
        label_name2 = 'test_label_02'
        lbl2 = self.test_client.update_label(label_name2, blob_uuid)
        self.assertIn('blobs', lbl2)
        self.assertEqual(3, lbl2['blobs']['1']['reference_count'])

        # Delete the first label
        self.assertIn('uuid', lbl)
        self.test_client.delete_artifact(lbl['uuid'])
        lbl_del = self.test_client.get_artifact(img['uuid'])
        self.assertEqual(2, lbl_del['blobs']['1']['reference_count'])

        # Delete the second label
        self.assertIn('uuid', lbl2)
        self.test_client.delete_artifact(lbl2['uuid'])
        lbl_del = self.test_client.get_artifact(img['uuid'])
        self.assertEqual(1, lbl_del['blobs']['1']['reference_count'])

        # Delete image artifact
        self.test_client.delete_artifact(img['uuid'])

        # Check reference count is now zero
        img_del = self.test_client.get_artifact(img['uuid'])
        self.assertEqual(0, img_del['blobs']['1']['reference_count'])
        self.assertEqual('deleted', img_del['state'])

        # Delete image artifact again (this is idempotent)
        self.test_client.delete_artifact(img['uuid'])
        img_del = self.test_client.get_artifact(img['uuid'])
        self.assertEqual(0, img_del['blobs']['1']['reference_count'])

    def test_artifact_max_versions(self):
        url = ('https://sfcbr.shakenfist.com/gw-basic/gwbasic.qcow2')

        img = self.system_client.cache_artifact(url)
        results = self._await_artifacts_ready([img['uuid']])
        self.assertEqual('created', results[0].get('state'))

        self.assertIn('blob_uuid', results[0])
        blob_uuid = results[0]['blob_uuid']

        # Create a label artifact pointing at the blob
        label_name = 'test_label_max_versions'
        lbl = self.test_client.update_label(label_name, blob_uuid)
        self.assertIsNot(0, lbl.get('max_versions'))

        expected_versions = lbl.get('max_versions')
        for i in range(expected_versions-1):
            lbl = self.test_client.update_label(label_name, blob_uuid)
            self.assertEqual(i + 2, len(lbl.get('blobs')))

        self.assertEqual(expected_versions, len(lbl.get('blobs')))
        for i in range(expected_versions):
            self.assertIn(str(i + 1), lbl['blobs'])

        # Check that the blob count remains static
        lbl = self.test_client.update_label(label_name, blob_uuid)
        self.assertEqual(expected_versions, len(lbl.get('blobs')))
        for i in range(expected_versions):
            self.assertIn(str(i + 2), lbl['blobs'])
        self.assertNotIn('1', lbl['blobs'])

        # Again, check that the blob count remains static
        lbl = self.test_client.update_label(label_name, blob_uuid)
        self.assertEqual(expected_versions, len(lbl.get('blobs')))
        for i in range(expected_versions):
            self.assertIn(str(i + 3), lbl['blobs'])
        self.assertNotIn('1', lbl['blobs'])
        self.assertNotIn('2', lbl['blobs'])

        # Delete a version in middle of the list
        if expected_versions > 2:
            self.test_client.delete_artifact_version(lbl['uuid'], '4')

            img = self.system_client.get_artifact(lbl['uuid'])
            self.assertEqual(expected_versions-1, len(img['blobs']))

            # Add extra version
            lbl = self.test_client.update_label(label_name, blob_uuid)
            self.assertEqual(expected_versions, len(lbl.get('blobs')))
            self.assertIn('3', lbl['blobs'])
