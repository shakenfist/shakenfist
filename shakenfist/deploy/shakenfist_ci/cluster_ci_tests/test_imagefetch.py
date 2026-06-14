import json
import socket
import subprocess
import time

from testtools import content

from shakenfist_ci import base


# These tests need a throwaway HTTP image source on the primary that they can
# create and then delete mid-test. The deployer used to install Apache, which
# provided /var/www/html served on port 80; it no longer does, so we serve the
# same docroot ourselves with a stdlib http.server.
IMAGE_SOURCE_ROOT = '/var/www/html'
IMAGE_SOURCE_PORT = 80


class TestHTTPFetch(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'httpfetch'
        super().__init__(*args, **kwargs)

    def _ensure_image_source_server(self):
        # Idempotent and tolerant of parallel test workers racing to bind the
        # port: whichever worker wins serves the shared docroot for all of
        # them, and the server is left running for the (ephemeral) test node's
        # lifetime rather than torn down per test.
        subprocess.run(['sudo', 'mkdir', '-p', IMAGE_SOURCE_ROOT], check=True)

        def _listening():
            try:
                with socket.create_connection(
                        ('127.0.0.1', IMAGE_SOURCE_PORT), timeout=2):
                    return True
            except OSError:
                return False

        if _listening():
            return

        subprocess.Popen(
            ['sudo', 'python3', '-m', 'http.server', str(IMAGE_SOURCE_PORT),
             '--bind', '0.0.0.0', '--directory', IMAGE_SOURCE_ROOT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for _ in range(30):
            if _listening():
                return
            time.sleep(1)
        raise Exception(
            'Local HTTP image source failed to start on port %d'
            % IMAGE_SOURCE_PORT)

    def setUp(self):
        super().setUp()
        self._ensure_image_source_server()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_disappearing_source_cache(self):
        p = subprocess.run(
            ['sudo /srv/shakenfist/venv/bin/sf-client '
             'artifact download debian-12 '
             '/var/www/html/debian-12-disappearing-cache'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            f'Command failed:\n\tstdout = {p.stdout}\n\tstderr = {p.stderr}\n')

        url = 'http://10.0.0.10/debian-12-disappearing-cache'
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
        while time.time() - start_time < 2 * 60:
            img = self.system_client.get_artifact(img['uuid'])
            if img['state'] in ['created', 'error']:
                break
            time.sleep(5)

        self.addDetail('img', content.text_content(json.dumps(
            img, indent=4, sort_keys=True)))
        self.assertEqual('created', img['state'])

        # Remove the source image
        p = subprocess.run(
            ['sudo rm /var/www/html/debian-12-disappearing-cache'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            f'Command failed:\n\tstdout = {p.stdout}\n\tstderr = {p.stderr}\n')
        self.system_client.cache_artifact(url)
        time.sleep(10)

        # Ensure the image isn't in an error state
        img = self.system_client.get_artifact(img['uuid'])
        self.addDetail('img_after_delete', content.text_content(json.dumps(
            img, indent=4, sort_keys=True)))
        self.assertEqual('created', img['state'])

    def test_disappearing_source_instance(self):
        nodes = self.system_client.get_nodes()
        self.addDetail('nodes', content.text_content(json.dumps(
            nodes, indent=4, sort_keys=True)))
        for n in nodes:
            if n['is_hypervisor']:
                break
        n = n['name']

        p = subprocess.run(
            ['sudo /srv/shakenfist/venv/bin/sf-client '
             'artifact download debian-12 '
             '/var/www/html/debian-12-disappearing-instance'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            f'Command failed:\n\tstdout = {p.stdout}\n\tstderr = {p.stderr}\n')

        url = 'http://10.0.0.10/debian-12-disappearing-instance'
        inst = self.test_client.create_instance(
            'inst1', 1, 1024, None,
            [
                {
                    'size': 20,
                    'base': url,
                    'type': 'disk'
                }
            ], None, None, force_placement=n)
        self.addDetail('inst1', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self._await_instance_ready(inst['uuid'])

        # Remove the source image
        p = subprocess.run(
            ['sudo rm /var/www/html/debian-12-disappearing-instance'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            f'Command failed:\n\tstdout = {p.stdout}\n\tstderr = {p.stderr}\n')

        # Ensure we can still start an instance
        inst = self.test_client.create_instance(
            'inst2', 1, 1024, None,
            [
                {
                    'size': 20,
                    'base': url,
                    'type': 'disk'
                }
            ], None, None, force_placement=n)
        self.addDetail('inst2', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self._await_instance_ready(inst['uuid'])
