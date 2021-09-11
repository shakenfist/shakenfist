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

        self.system_client.cache_image(url)
        image_urls = []
        for image in self.system_client.get_images():
            image_urls.append(image['source_url'])

        self.assertIn(url, image_urls)

        # It would be better if this used a get_image() call, but that doesn't
        # exist at the moment.
        cache = {}
        start_time = time.time()
        while time.time() - start_time < 7 * 60:
            cache = {}
            for img in self.system_client.get_images():
                cache.setdefault(img['source_url'], [])
                cache[img['source_url']].append(img)

            self.assertIn(url, cache)
            if cache[url][0]['state'] == 'created':
                return

            time.sleep(5)

        self.fail('Image was not downloaded after seven minutes: %s'
                  % cache.get(url))

    def test_cache_image_specific(self):
        self.skip('Requires API reworking')

        # It is currently not possible to check if an image is
        # in the cache via API, so for now we just cache this and
        # see if any errors come back.
        url = ('https://cloud.centos.org/centos/6/images/'
               'CentOS-6-x86_64-GenericCloud.qcow2.xz')
        img = self.system_client.cache_image(url)
        self._await_image_download_success(img['uuid'], after=time.time())

    def test_cache_invalid_image(self):
        url = ('http://nosuch.shakenfist.com/centos/6/images/'
               'CentOS-6-x86_64-GenericCloud-1604.qcow2.xz')
        self.system_client.cache_image(url)
        self._await_image_download_error(url, after=time.time())

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

        self.assertEqual('creating-error', inst['state'])
