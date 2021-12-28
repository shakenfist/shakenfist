import json
import sys
import time

from oslo_concurrency import processutils

from shakenfist_ci import base


def _exec(cmd):
    sys.stderr.write('\n----- Exec: %s -----\n' % cmd)
    out, err = processutils.execute(cmd, shell=True)
    for line in out.split('\n'):
        sys.stderr.write('out: %s\n' % line)
    sys.stderr.write('\n')
    for line in err.split('\n'):
        sys.stderr.write('err: %s\n' % line)
    sys.stderr.write('\n----- End: %s -----\n' % cmd)
    return out


class TestArtifactCommandLine(base.BaseNamespacedTestCase):
    """Make sure the command line client works."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'artifact-commandline'
        super(TestArtifactCommandLine, self).__init__(*args, **kwargs)

    def _exec_client(self, cmd):
        return _exec('sf-client --apiurl %s --namespace %s --key %s %s'
                     % (self.test_client.base_url, self.namespace,
                        self.namespace_key, cmd))

    def test_artifact_commands(self):
        # Ensure we have a version of cirros in the cache
        self._exec_client('artifact cache cirros')

        # Ensure that cirros appears in the list of artifacts
        cirros_uuid = None
        self.assertRegexpMatches(
            self._exec_client('artifact list'), '.*cirros.*')
        for a in json.loads(self._exec_client('--json artifact list')):
            if a['source_url'] == 'cirros':
                cirros_uuid = a['uuid']
                self.assertIn(a['state'], ['initial', 'created'])
        self.assertIsNotNone(cirros_uuid)

        # Show the artifact
        self.assertRegexpMatches(
            self._exec_client('artifact show %s' % cirros_uuid), '.*image.*')

        # Wait until we have a blob_uuid
        start_time = time.time()
        a = json.loads(self._exec_client(
            '--json artifact show %s' % cirros_uuid))
        while time.time() - start_time < 5 * 60 * base.NETWORK_PATIENCE_FACTOR:
            if a.get('blob_uuid'):
                break
            time.sleep(5)
            a = json.loads(self._exec_client('--json artifact show %s'
                                             % cirros_uuid))

        self.assertIn('blob_uuid', a)

        # Show artifact versions
        versions = json.loads(self._exec_client(
            'artifact versions %s' % cirros_uuid))
        self.assertEqual(1, len(versions))

    def test_artifact_commands_multiple_versions(self):
        url = 'http://uuid.com/'
        self._exec_client('artifact cache "%s"' % url)
        time.sleep(5)
        self._exec_client('artifact cache "%s"' % url)
        time.sleep(5)
        self._exec_client('artifact cache "%s"' % url)
        time.sleep(5)
        self._exec_client('artifact cache "%s"' % url)
        time.sleep(5)
        self._exec_client('artifact cache "%s"' % url)

        # Ensure that our download appears in the list of artifacts
        artifact_urls = []
        artifact_uuid = None
        for a in json.loads(self._exec_client('--json artifact list')):
            artifact_urls.append(a['source_url'])
            if a['source_url'] == url:
                artifact_uuid = a['uuid']
                self.assertIn(a['state'], ['initial', 'created'])
        self.assertIsNotNone(artifact_uuid)

        # Wait for downloads
        time.sleep(30)

        # Ensure we have the right number of versions
        start_time = time.time()
        while time.time() - start_time < 5 * 60 * base.NETWORK_PATIENCE_FACTOR:
            versions = json.loads(self._exec_client(
                'artifact versions %s' % artifact_uuid))
            # Number of versions will be limited to the max_versions setting
            if len(versions) == 3:
                return
            time.sleep(30)

        self.fail('Never received the correct number of versions. I have %d'
                  % len(versions))

    def test_artifact_show(self):
        # Create an instance
        inst1 = self.test_client.create_instance(
            'test-cirros-boot-no-network', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ], None, None)
        self._await_instances_ready([inst1['uuid']])

        # Take a snapshot
        snap1 = json.loads(self._exec_client(
            '--json instance snapshot %s' % inst1['uuid']))
        self.assertIn('vda', snap1)
        self.assertIn('artifact_uuid', snap1['vda'])
        snap_uuid = snap1['vda']['artifact_uuid']

        # Check the blobs information for the first version
        show_info = json.loads(self._exec_client(
            '--json artifact show %s' % snap_uuid))
        self.assertIn('blobs', show_info)
        self.assertEqual(1, len(show_info['blobs']))
        self.assertIn('1', show_info['blobs'])

        self.assertIn('size', show_info['blobs']['1'])
        self.assertIsInstance(show_info['blobs']['1']['size'], int)
        self.assertGreater(show_info['blobs']['1']['size'], 100000)

        self.assertIn('instances', show_info['blobs']['1'])
        self.assertEqual(0, len(show_info['blobs']['1']['instances']))

        # Start an instance on the snapshot
        inst2 = self.test_client.create_instance(
            'test-cirros-boot-no-network', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://snapshot/%s' % snap_uuid,
                    'type': 'disk'
                }
            ], None, None)
        self._await_instances_ready([inst2['uuid']])

        # Test instance is listed against blob in snapshot listing
        show_info = json.loads(self._exec_client(
            '--json artifact show %s' % snap_uuid))
        self.assertIn('blobs', show_info)
        self.assertEqual(1, len(show_info['blobs']))

        self.assertIn('instances', show_info['blobs']['1'])
        self.assertEqual(1, len(show_info['blobs']['1']['instances']))
        self.assertEqual(
            inst2['uuid'], show_info['blobs']['1']['instances'][0])

        # Take a second snapshot of the original instance
        self._exec_client('--json instance snapshot %s' % inst1['uuid'])

        # Check the second snapshot is listed
        show_info = json.loads(self._exec_client(
            '--json artifact show %s' % snap_uuid))
        self.assertIn('blobs', show_info)
        self.assertEqual(2, len(show_info['blobs']))

        self.assertIn('1', show_info['blobs'])
        self.assertIn('instances', show_info['blobs']['1'])
        self.assertEqual(1, len(show_info['blobs']['1']['instances']))
        self.assertEqual(
            inst2['uuid'], show_info['blobs']['1']['instances'][0])

        self.assertIn('2', show_info['blobs'])
        self.assertIn('size', show_info['blobs']['2'])
        self.assertIsInstance(show_info['blobs']['2']['size'], int)
        self.assertGreater(show_info['blobs']['2']['size'], 100000)

        self.assertIn('instances', show_info['blobs']['2'])
        self.assertEqual(0, len(show_info['blobs']['2']['instances']))
