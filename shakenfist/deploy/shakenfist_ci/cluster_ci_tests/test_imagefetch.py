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
        p = subprocess.run(
            ['sudo /srv/shakenfist/venv/bin/sf-client '
             'artifact download debian-12 '
             '/var/www/html/debian-12-disappearing-instance'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            f'Command failed:\n\tstdout = {p.stdout}\n\tstderr = {p.stderr}\n')

        url = 'http://10.0.0.10/debian-12-disappearing-instance'
        inst1 = self.create_instance(
            'inst1', 1, 1024, None,
            [
                {
                    'size': 20,
                    'base': url,
                    'type': 'disk'
                }
            ], None, None)
        self.addDetail('inst1', content.text_content(json.dumps(
            inst1, indent=4, sort_keys=True)))
        self._await_instance_ready(inst1['uuid'])

        # Remove the source image
        p = subprocess.run(
            ['sudo rm /var/www/html/debian-12-disappearing-instance'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            f'Command failed:\n\tstdout = {p.stdout}\n\tstderr = {p.stderr}\n')

        # Ensure we can still start an instance
        inst = self.create_instance(
            'inst2', 1, 1024, None,
            [
                {
                    'size': 20,
                    'base': url,
                    'type': 'disk'
                }
            ], None, None, force_placement=inst1['node'])
        self.addDetail('inst2', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self._await_instance_ready(inst['uuid'])

    # A port for this test alone, so stopping the server cannot break the
    # shared port 80 source the other tests in this file use.
    VANISHED_SERVER_PORT = 8517

    def _vanished_server_listening(self):
        try:
            with socket.create_connection(
                    ('127.0.0.1', self.VANISHED_SERVER_PORT), timeout=2):
                return True
        except OSError:
            return False

    def test_vanished_source_server_instance(self):
        # Issue 3603: an instance built from a cached image must still
        # start when the image's source cannot be reached at all -- even
        # on a node which has never held the image locally, so the cached
        # version has to actually be fetched, not just declared usable.
        # test_disappearing_source_instance above deletes the file, which
        # exercises the 404 handling inside the fetch's version check;
        # stopping the server entirely makes the fetch fail with a
        # connection error, which reaches the fetch operation's fallback
        # path instead.
        nodes = self.system_client.get_nodes()
        hypervisors = [n['name'] for n in nodes if n['is_hypervisor']]
        self.addDetail('hypervisors', content.text_content(json.dumps(
            hypervisors, indent=4, sort_keys=True)))
        first = hypervisors[0]
        second = hypervisors[1] if len(hypervisors) > 1 else first

        p = subprocess.run(
            ['sudo /srv/shakenfist/venv/bin/sf-client '
             'artifact download debian-12 '
             '/var/www/html/debian-12-vanished-server'],
            shell=True, capture_output=True, timeout=300)
        self.assertEqual(
            0, p.returncode,
            f'Command failed:\n\tstdout = {p.stdout}\n\tstderr = {p.stderr}\n')

        server_args = f'http.server {self.VANISHED_SERVER_PORT}'
        subprocess.Popen(
            ['sudo', 'python3', '-m', 'http.server',
             str(self.VANISHED_SERVER_PORT), '--bind', '0.0.0.0',
             '--directory', IMAGE_SOURCE_ROOT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(
            subprocess.run, ['sudo', 'pkill', '-f', server_args])
        for _ in range(30):
            if self._vanished_server_listening():
                break
            time.sleep(1)
        self.assertTrue(
            self._vanished_server_listening(),
            'Dedicated HTTP image source failed to start on port %d'
            % self.VANISHED_SERVER_PORT)

        url = ('http://10.0.0.10:%d/debian-12-vanished-server'
               % self.VANISHED_SERVER_PORT)
        inst = self.create_instance(
            'inst1', 1, 1024, None,
            [
                {
                    'size': 20,
                    'base': url,
                    'type': 'disk'
                }
            ], None, None, force_placement=first)
        self.addDetail('inst1', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self._await_instance_ready(inst['uuid'])

        # Stop the source server entirely
        subprocess.run(['sudo', 'pkill', '-f', server_args])
        for _ in range(30):
            if not self._vanished_server_listening():
                break
            time.sleep(1)
        self.assertFalse(
            self._vanished_server_listening(),
            'Dedicated HTTP image source failed to stop')

        # Ensure we can still start an instance, on a node which has not
        # seen this image before if the cluster has one.
        inst = self.create_instance(
            'inst2', 1, 1024, None,
            [
                {
                    'size': 20,
                    'base': url,
                    'type': 'disk'
                }
            ], None, None, force_placement=second)
        self.addDetail('inst2', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self._await_instance_ready(inst['uuid'])
