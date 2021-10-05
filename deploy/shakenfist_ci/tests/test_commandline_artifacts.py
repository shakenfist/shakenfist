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
            while not a.get('blob_uuid', None):
                time.sleep(5)
                a = json.loads(self._exec_client('--json artifact show %s'
                                                 % cirros_uuid))

        self.assertIn('blob_uuid', a)

        # Show artifact versions
        versions = json.loads(self._exec_client(
            'artifact versions %s' % cirros_uuid))
        self.assertEqual(1, len(versions))

    def test_artifact_commands_multiple_versions(self):
        url = ('http://www.randomnumberapi.com/api/v1.0/'
               'random?min=100&max=1000&count=1')
        self._exec_client('artifact cache "%s"' % url)
        self._exec_client('artifact cache "%s"' % url)
        self._exec_client('artifact cache "%s"' % url)
        self._exec_client('artifact cache "%s"' % url)
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
        time.sleep(10)

        # Ensure we have the right number of versions
        versions = json.loads(self._exec_client(
            'artifact versions %s' % artifact_uuid))
        self.assertEqual(5, len(versions))
