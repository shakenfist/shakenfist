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
        inst1 = self.test_client.create_instance(
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
        self._await_instance_ready(inst1['uuid'])

        # Execute a command
        start_time = time.time()
        aop = self.test_client.instance_execute(inst1['uuid'], 'whoami')
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                self.fail(f'Timeout for agentop: {aop}')
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
        inst1 = self.test_client.create_instance(
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
        self._await_instance_ready(inst1['uuid'])

        # Execute a command
        start_time = time.time()
        aop = self.test_client.instance_execute(
            inst1['uuid'], 'cat /var/log/syslog')

        # Wait for the operation to complete
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                self.fail(f'Timeout for agentop: {aop}')
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

    def test_instance_put_blob(self):
        inst1 = self.test_client.create_instance(
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
        self._await_instance_ready(inst1['uuid'])

        # Pick a blob and send it to the instance
        blobs = self.system_client.get_blobs()
        self.assertNotEqual(0, len(blobs))
        blob_uuid = blobs[0]['uuid']

        start_time = time.time()
        aop = self.test_client.instance_put_blob(
            inst1['uuid'], blob_uuid, '/tmp/foo', 'ugo+r')

        # Wait for the operation to complete
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                self.fail(f'Timeout for agentop: {aop}')
            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

        self.assertTrue(
            '0' in aop['results'],
            f'Agent operation results lack expected result key "0": {aop}')
