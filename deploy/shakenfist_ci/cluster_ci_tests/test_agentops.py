import time

from shakenfist_ci import base


class TestAgentOperations(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'agentops'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net_one = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace,
            provide_dns=True)
        self._await_networks_ready([self.net_one['uuid']])

    def test_instance_execute_small(self):
        inst = self.test_client.create_instance(
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
        start_time = time.time()
        aop = self.test_client.instance_execute(inst['uuid'], 'whoami')
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                console_data = self.test_client.get_console_data(inst['uuid'])
                self.fail(
                    f'Timeout for agentop: {aop}\n\nConsole: {console_data}')
            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

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
        inst = self.test_client.create_instance(
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
        start_time = time.time()
        aop = self.test_client.instance_execute(
            inst['uuid'], 'cat /var/log/syslog')

        # Wait for the operation to complete
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                console_data = self.test_client.get_console_data(inst['uuid'])
                self.fail(
                    f'Timeout for agentop: {aop}\n\nConsole: {console_data}')
            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

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

    def test_instance_put_and_get_blob(self):
        inst = self.test_client.create_instance(
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

        start_time = time.time()
        aop = self.test_client.instance_put_blob(
            inst['uuid'], blob_uuid, '/tmp/foo', 'ugo+r')

        # Wait for the operation to complete
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                console_data = self.test_client.get_console_data(inst['uuid'])
                self.fail(
                    f'Timeout for agentop: {aop}\n\nConsole: {console_data}')
            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

        # Now ensure the data arrived correctly
        aop = self.test_client.instance_execute(
            inst['uuid'], 'sha512sum /tmp/foo')
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                console_data = self.test_client.get_console_data(inst['uuid'])
                self.fail(
                    f'Timeout for agentop: {aop}\n\nConsole: {console_data}')
            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

        remote_hash = aop['results']['0']['stdout'].split(' ')[0]
        self.assertEqual(
            cluster_hash, remote_hash,
            f'Cluster hash {cluster_hash} does not match remote hash'
            f'{remote_hash}')

        # Now fetch the data back
        aop = self.test_client.instance_get(inst['uuid'], '/tmp/foo')

        # Wait for the operation to complete
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                console_data = self.test_client.get_console_data(inst['uuid'])
                self.fail(
                    f'Timeout for agentop: {aop}\n\nConsole: {console_data}')
            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

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
        while not b['checksums'].get('sha512'):
            if time.time() - start_time > 60:
                self.fail(
                    f'Checksum for blob {b["uuid"]} not available after 60 '
                    'seconds')

            time.sleep(5)
            b = self.test_client.get_blob(aop['results']['0']['content_blob'])

        self.assertEqual(cluster_hash, b['checksums'].get('sha512'))
