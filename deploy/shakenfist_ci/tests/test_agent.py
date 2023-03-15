import os

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
        self.test_client.upload_artifact(
            'fibonacci', upl['uuid'], artifact_type='other')

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])
