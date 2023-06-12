import os
import time

from shakenfist_ci import base


class TestAgentFileOperations(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'agentfileops'
        super(TestAgentFileOperations, self).__init__(*args, **kwargs)

    def test_put_and_get_file(self):
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
        op = self.test_client.put_instance_blob(
            inst['uuid'], input_blob, '/tmp/fibonacci.py', 'ugo+rx')

        start_time = time.time()
        while time.time() - start_time < 60:
            if op['state'] == 'complete':
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        if op['state'] != 'complete':
            self.fail('Agent put operation %s did not complete in 60 seconds (%s)'
                      % (op['uuid'], op['state']))

        # Request that the agent execute the file
        op = self.test_client.instance_execute(
            inst['uuid'], '/tmp/fibonacci.py')

        start_time = time.time()
        while time.time() - start_time < 60:
            if op['state'] == 'complete':
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        if op['state'] != 'complete':
            self.fail('Agent execute operation %s did not complete in 60 seconds (%s)'
                      % (op['uuid'], op['state']))

        self.assertNotEqual({}, op['results'])
        self.assertEqual(0, op['results']['1']['return-code'])
        self.assertTrue(op['results']['stdout'].startswith(
            '[0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987'))
        self.assertEqual(0, len(op['results']['1']['stderr']))
