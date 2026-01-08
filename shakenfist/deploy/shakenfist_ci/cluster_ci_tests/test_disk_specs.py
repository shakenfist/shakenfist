import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestDiskSpecifications(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'diskspecs'
        super().__init__(*args, **kwargs)

    def test_default(self):
        inst = self.test_client.create_instance(
            'test-default-disk', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        results = self._await_command(inst['uuid'], 'df -h')
        self.addDetail(
            'results',
            content.text_content(json.dumps(results, indent=4, sort_keys=True)))
        self.assertEqual(0, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue('vda' in results['stdout'])

    def test_bad_bus(self):
        self.assertRaises(
            apiclient.RequestMalformedException,
            self.test_client.create_instance,
            'test-bad-bus-disk', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk',
                    'bus': 'banana'
                }
            ], None, None)
