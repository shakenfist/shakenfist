from shakenfist_ci import base


class TestCacheImage(base.BaseTestCase):
    def setUp(self):
        super(TestCacheImage, self).setUp()

    def test_cache_image(self):
        # It is currently not possible to check if an image is
        # in the cache via API, so for now we just cache this and
        # see if any errors come back.
        self.system_client.cache_image(
            'https://stable.release.core-os.net/amd64-usr/'
            'current/coreos_production_openstack_image.img.bz2')
