import random

from shakenfist_ci import base


class TestArtifactMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'artifact-metadata'
        super(TestArtifactMetadata, self).__init__(*args, **kwargs)

    def test_simple(self):
        img = self.test_client.cache_artifact(
                'https://sfcbr.shakenfist.com/cgi-bin/uuid.cgi?uniq=%06d'
                % random.randint(-999999, 999999))

        self.assertEqual({}, self.test_client.get_artifact_metadata(img['uuid']))
        self.test_client.set_artifact_metadata_item(img['uuid'], 'foo', 'bar')
        self.assertEqual(
            {'foo': 'bar'}, self.test_client.get_artifact_metadata(img['uuid']))
        self.test_client.delete_artifact_metadata_item(img['uuid'], 'foo')
        self.assertEqual({}, self.test_client.get_artifact_metadata(img['uuid']))


class TestInstanceMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'instance-metadata'
        super(TestInstanceMetadata, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestInstanceMetadata, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_simple(self):
        inst = self.test_client.create_instance(
            'test-simple-metadata', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])

        self.assertEqual({}, self.test_client.get_instance_metadata(inst['uuid']))
        self.test_client.set_instance_metadata_item(inst['uuid'], 'foo', 'bar')
        self.assertEqual({
            'foo': 'bar'}, self.test_client.get_instance_metadata(inst['uuid']))
        self.test_client.delete_instance_metadata_item(inst['uuid'], 'foo')
        self.assertEqual({}, self.test_client.get_instance_metadata(inst['uuid']))


class TestNamespaceMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'namespace-metadata'
        super(TestNamespaceMetadata, self).__init__(*args, **kwargs)

    def test_simple(self):
        # The name of the namespace is uniqified by the test runner, so we need
        # to lookup what was actually created.
        nsname = self.test_client.namespace

        self.assertEqual({}, self.test_client.get_namespace_metadata(nsname))
        self.test_client.set_namespace_metadata_item(nsname, 'foo', 'bar')
        self.assertEqual(
            {'foo': 'bar'}, self.test_client.get_namespace_metadata(nsname))
        self.test_client.delete_namespace_metadata_item(nsname, 'foo')
        self.assertEqual({}, self.test_client.get_namespace_metadata(nsname))
