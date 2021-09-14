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
        self._await_network_ready(self.net['uuid'])

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
                    'base': 'ubuntu:20.04',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])

        while inst['state'] in ['initial', 'preflight', 'creating']:
            time.sleep(1)
            inst = self.test_client.get_instance(inst['uuid'])

        self.assertTrue(inst['state'] in ['creating-error', 'error'])
