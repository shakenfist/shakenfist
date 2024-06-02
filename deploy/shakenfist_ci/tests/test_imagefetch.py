import subprocess
import time

from shakenfist_ci import base


class TestHTTPFetch(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'httpfetch'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_disappearing_source_cache(self):
        p = subprocess.run(
            ['sudo sf-client artifact download debian-11 '
             '/var/www/html/debian-11-disappearing-cache'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            'Command failed:\n\tstdout = {}\n\tstderr = {}\n'.format(p.stdout, p.stderr))

        url = 'http://10.0.0.10/debian-11-disappearing-cache'
        img = self.system_client.cache_artifact(url)

        # Get all artifacts once to make sure we get added to the list
        image_urls = []
        for image in self.system_client.get_artifacts():
            image_urls.append(image['source_url'])
        self.assertIn(url, image_urls)

        # And then just lookup the single artifact
        start_time = time.time()
        while time.time() - start_time < 2 * 60:
            img = self.system_client.get_artifact(img['uuid'])
            if img['state'] in ['created', 'error']:
                break
            time.sleep(5)

        self.assertEqual('created', img['state'])

        # Remove the source image
        p = subprocess.run(
            ['sudo rm /var/www/html/debian-11-disappearing-cache'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            'Command failed:\n\tstdout = {}\n\tstderr = {}\n'.format(p.stdout, p.stderr))
        self.system_client.cache_artifact(url)
        time.sleep(10)

        # Ensure the image isn't in an error state
        img = self.system_client.get_artifact(img['uuid'])
        self.assertEqual('created', img['state'])

    def test_disappearing_source_instance(self):
        p = subprocess.run(
            ['sudo sf-client artifact download debian-11 '
             '/var/www/html/debian-11-disappearing-instance'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            'Command failed:\n\tstdout = {}\n\tstderr = {}\n'.format(p.stdout, p.stderr))

        url = 'http://10.0.0.10/debian-11-disappearing-instance'
        inst = self.test_client.create_instance(
            'inst1', 1, 1024, None,
            [
                {
                    'size': 20,
                    'base': url,
                    'type': 'disk'
                }
            ], None, None)
        self._await_instance_ready(inst['uuid'])

        # Remove the source image
        p = subprocess.run(
            ['sudo rm /var/www/html/debian-11-disappearing-instance'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            'Command failed:\n\tstdout = {}\n\tstderr = {}\n'.format(p.stdout, p.stderr))

        # Ensure we can still start an instance
        inst = self.test_client.create_instance(
            'inst2', 1, 1024, None,
            [
                {
                    'size': 20,
                    'base': url,
                    'type': 'disk'
                }
            ], None, None)
        self._await_instance_ready(inst['uuid'])
