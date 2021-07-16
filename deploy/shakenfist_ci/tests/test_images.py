import time

from shakenfist_ci import base


class TestImages(base.BaseTestCase):
    def test_cache_image(self):
        url = ('http://cdimage.debian.org/cdimage/openstack/archive/'
               '10.7.2-20201210/debian-10.7.2-20201210-openstack-amd64.qcow2')

        self.system_client.cache_image(url)
        image_urls = []
        for image in self.system_client.get_images():
            image_urls.append(image['source_url'])

        self.assertIn(url, image_urls)

        # It would be better if this used a get_image() call, but that doesn't
        # exist at the moment.
        cache = {}
        start_time = time.time()
        while time.time() - start_time < 300:
            cache = {}
            for img in self.system_client.get_images():
                cache.setdefault(img['source_url'], [])
                cache[img['source_url']].append(img)

            self.assertIn(url, cache)
            if cache[url][0]['state'] == 'created':
                return

            time.sleep(5)

        self.fail('Image was not downloaded after five minutes: %s'
                  % cache.get(url))
