import json
import os
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_ci import instance_hotplug
from shakenfist_client import apiclient


class TestAgentOperations(instance_hotplug.InstanceHotplugTestsMixin,
                          base.BaseNamespacedTestCase):
    # The interface hot plug tests live on InstanceHotplugTestsMixin so the
    # smoke and guest suites run one implementation (issue 4318). Do not add
    # a local copy here.
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'agentops'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net_one = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace,
            provide_dns=True)
        self.addDetail(
            'net_one',
            content.text_content(json.dumps(self.net_one, indent=4,
                                            sort_keys=True)))
        self._await_networks_ready([self.net_one['uuid']])

    def test_instance_execute_small(self):
        inst = self.create_instance(
            'test-instance-execute-small', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Execute a command
        aop = self.test_client.instance_execute(inst['uuid'], 'whoami')
        aop = self._await_agentop_complete(inst['uuid'], aop, 30, 'whoami')

        self.assertTrue(
            '0' in aop['results'],
            f'Agent operation results lack expected result key "0": {aop}')
        self.assertTrue(
            'stdout' in aop['results']['0'],
            f'Agent operation result 0 lacks expected result key "stdout": {aop}')
        self.assertEqual(
            'root\n', aop['results']['0']['stdout'],
            f'Agent operation result "0" stdout value lacks expected value '
            f'"root\\n": {aop}')

    def test_instance_execute_large(self):
        inst = self.create_instance(
            'test-instance-execute-large', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Execute a command
        aop = self.test_client.instance_execute(
            inst['uuid'], 'cat /var/log/syslog')

        # Wait for the operation to complete
        aop = self._await_agentop_complete(
            inst['uuid'], aop, 30, 'cat /var/log/syslog')

        self.assertTrue(
            '0' in aop['results'],
            f'Agent operation results lack expected result key "0": {aop}')
        self.assertTrue(
            'stdout' not in aop['results']['0'],
            f'Agent operation result "0" has unexpected result key "stdout": {aop}')
        self.assertTrue(
            'stdout_blob' in aop['results']['0'],
            'Agent operation result "0" lacks expected result key '
            f'"stdout_blob": {aop}')

        b = self.test_client.get_blob(aop['results']['0']['stdout_blob'])
        self.assertNotEqual(None, b)

    def test_put_and_exec_large_stdout(self):
        # Create an instance to run our script on
        inst = self.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Upload our script
        upl = self.test_client.create_upload()
        test_dir = os.path.dirname(os.path.abspath(__file__))
        with open('%s/files/fibonacci.py' % test_dir, 'rb') as f:
            self.test_client.send_upload_file(upl['uuid'], f)
        input = self.test_client.upload_artifact(
            'fibonacci', upl['uuid'], artifact_type='other')
        input_blob = input['blob_uuid']

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Request that the agent copy the file to the instance
        op = self.test_client.instance_put_blob(
            inst['uuid'], input_blob, '/tmp/fibonacci.py', 'ugo+rx')
        op = self._await_agentop_complete(
            inst['uuid'], op, 120, 'put fibonacci.py')

        # Request that the agent execute the file
        _, data = self.test_client.await_agent_command(
            inst['uuid'], '/tmp/fibonacci.py')
        self.assertTrue(data.startswith(
            '[0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987'))

    def test_instance_put_and_get_blob(self):
        inst = self.create_instance(
            'test-instance-put-blob', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 20,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        # Create a blob to use for the test by uploading a file
        upl = self.test_client.create_upload()
        test_dir = os.path.dirname(os.path.abspath(__file__))
        with open('%s/files/fibonacci.py' % test_dir, 'rb') as f:
            self.test_client.send_upload_file(upl['uuid'], f)
        artifact = self.test_client.upload_artifact(
            'test-blob', upl['uuid'], artifact_type='other')
        blob_uuid = artifact['blob_uuid']

        # Wait for the blob's sha512 checksum to be calculated
        start_time = time.time()
        cluster_hash = self.test_client.get_blob_hash(blob_uuid, 'sha512')
        while not cluster_hash:
            if time.time() - start_time > 60:
                self.fail(
                    f'Checksum for blob {blob_uuid} not available after 60 '
                    'seconds')
            time.sleep(5)
            cluster_hash = self.test_client.get_blob_hash(blob_uuid, 'sha512')

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Pick a blob and send it to the instance
        blobs = self.system_client.get_blobs()
        self.assertNotEqual(0, len(blobs))

        blob_uuid = None
        for blob in blobs:
            if blob['checksums'].get('sha512'):
                blob_uuid = blob['uuid']
                cluster_hash = blob['checksums']['sha512']
                break

        self.assertNotEqual(
            None, blob_uuid, 'Failed to find a blob with a hash')

        aop = self.test_client.instance_put_blob(
            inst['uuid'], blob_uuid, '/tmp/foo', 'ugo+r')
        aop = self._await_agentop_complete(inst['uuid'], aop, 60, 'put /tmp/foo')

        # Now ensure the data arrived correctly
        aop = self.test_client.instance_execute(
            inst['uuid'], 'sha512sum /tmp/foo')
        aop = self._await_agentop_complete(
            inst['uuid'], aop, 60, 'sha512sum /tmp/foo')

        remote_hash = aop['results']['0']['stdout'].split(' ')[0]
        self.assertEqual(
            cluster_hash, remote_hash,
            f'Cluster hash {cluster_hash} does not match remote hash'
            f'{remote_hash}')

        # Now fetch the data back. get-file is the heaviest operation (the
        # agent reads the file, hashes it and uploads it back as a new blob),
        # so it gets a more generous independent budget.
        aop = self.test_client.instance_get(inst['uuid'], '/tmp/foo')
        aop = self._await_agentop_complete(inst['uuid'], aop, 120, 'get /tmp/foo')

        self.assertTrue(
            '0' in aop['results'],
            f'Agent operation results lack expected result key "0": {aop}')
        self.assertTrue(
            'stat_result' in aop['results']['0'],
            f'Agent operation results lacks stat results: {aop}')
        self.assertTrue(
            'content_blob' in aop['results']['0'],
            f'Agent operation results lacks stat results: {aop}')

        b = self.test_client.get_blob(aop['results']['0']['content_blob'])
        self.assertNotEqual(None, b)

        start_time = time.time()
        fetched_hash = self.test_client.get_blob_hash(b['uuid'], 'sha512')
        while not fetched_hash:
            if time.time() - start_time > 60:
                self.fail(
                    f'Checksum for blob {b["uuid"]} not available after 60 '
                    'seconds')

            time.sleep(5)
            fetched_hash = self.test_client.get_blob_hash(b['uuid'], 'sha512')

        self.assertEqual(cluster_hash, fetched_hash)

    def test_get(self):
        # Create an instance to fetch files from
        inst = self.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Run a simple fetch command
        data = self.test_client.await_agent_fetch(
            inst['uuid'], '/etc/os-release')
        self.assertTrue(data.startswith('PRETTY_NAME='))

    def test_get_missing_file(self):
        # Create an instance to fetch files from
        inst = self.create_instance(
            'test-put-and-get-file-missing', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Run a fetch command which should fail
        self.assertRaises(
            apiclient.AgentOperationFailed, self.test_client.await_agent_fetch,
            inst['uuid'], '/tmp/nosuch')
