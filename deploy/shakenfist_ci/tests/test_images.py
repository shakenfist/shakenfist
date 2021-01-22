from shakenfist_ci import base


class TestImages(base.BaseTestCase):
    def test_cache_image(self):
        url = ('http://cdimage.debian.org/cdimage/openstack/archive/'
               '10.7.2-20201210/debian-10.7.2-20201210-openstack-amd64.qcow2')

        self.system_client.cache_image(url)
        image_urls = []
        for image in self.system_client.get_image_meta():
            image_urls.append(image['url'])

        self.assertIn(url, image_urls)
