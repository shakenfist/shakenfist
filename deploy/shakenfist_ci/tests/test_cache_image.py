import time

from shakenfist_ci import base


class TestCacheImage(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'cacheimage'
        super(TestCacheImage, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestCacheImage, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)

    def test_cache_image(self):
        # It is currently not possible to check if an image is
        # in the cache via API, so for now we just cache this and
        # see if any errors come back.
        url = ('http://cloud.centos.org/centos/6/images/'
               'CentOS-6-x86_64-GenericCloud-1604.qcow2.xz')
        self.system_client.cache_image(url)
        self._await_image_download_success(url, after=time.time())

    def test_cache_invalid_image(self):
        url = ('https://nosuch.shakenfist.com/centos/6/images/'
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
