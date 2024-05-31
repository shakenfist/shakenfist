import os
import time

from shakenfist_ci import base


class TestAgentFileOperations(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'agentfileops'
        super().__init__(*args, **kwargs)

    def test_put_and_exec_large_stdout(self):
        # Create an instance to run our script on
        inst = self.test_client.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
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

        start_time = time.time()
        while time.time() - start_time < 120:
            if op['state'] == 'complete':
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        if op['state'] != 'complete':
            self.fail('Agent put operation %s did not complete in 120 seconds (%s)'
                      % (op['uuid'], op['state']))

        # Request that the agent execute the file
        _, data = self._await_agent_command(inst['uuid'], '/tmp/fibonacci.py')
        self.assertTrue(data.startswith(
            '[0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987'))

    def test_exec_small_stdout(self):
        # Create an instance to run our command on
        inst = self.test_client.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Run a simple command
        op = self.test_client.instance_execute(inst['uuid'], 'cat /etc/os-release')

        # Wait for the operation to be complete
        start_time = time.time()
        while time.time() - start_time < 120:
            if op['state'] == 'complete':
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        if op['state'] != 'complete':
            self.fail('Agent execute operation %s did not complete in 120 seconds (%s)'
                      % (op['uuid'], op['state']))

        # Wait for the operation to have results
        start_time = time.time()
        while time.time() - start_time < 60:
            if op['results'] != {}:
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        self.assertNotEqual({}, op['results'])
        self.assertEqual(0, op['results']['0']['return-code'])
        self.assertFalse('stdout_blob' in op['results']['0'])
        self.assertTrue('stdout' in op['results']['0'])

        self.assertTrue(op['results']['0']['stdout'].startswith('PRETTY_NAME='))
        self.assertEqual(0, len(op['results']['0']['stderr']))

    def test_get(self):
        # Create an instance to fetch files from
        inst = self.test_client.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Run a simple command
        op = self.test_client.instance_get(inst['uuid'], '/etc/os-release')

        # Wait for the operation to be complete
        start_time = time.time()
        while time.time() - start_time < 120:
            if op['state'] == 'complete':
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        if op['state'] != 'complete':
            self.fail('Agent execute operation %s did not complete in 120 seconds (%s)'
                      % (op['uuid'], op['state']))

        # Wait for the operation to have results
        start_time = time.time()
        while time.time() - start_time < 60:
            if op['results'] != {}:
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        self.assertNotEqual({}, op['results'])
        self.assertTrue('content_blob' in op['results']['0'])

        # Wait for the blob containing the file to be ready
        start_time = time.time()
        b = self.test_client.get_blob(op['results']['0']['content_blob'])
        while time.time() - start_time < 60:
            if b['state'] == 'created':
                break
            time.sleep(5)
            b = self.test_client.get_blob(op['results']['0']['content_blob'])

        # Fetch the blob containing the file
        data = ''
        for chunk in self.test_client.get_blob_data(op['results']['0']['content_blob']):
            data += chunk.decode('utf-8')

        self.assertTrue(data.startswith('PRETTY_NAME='))
