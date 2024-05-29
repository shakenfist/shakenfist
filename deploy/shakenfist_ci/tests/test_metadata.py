import random

from shakenfist_ci import base


class TestArtifactMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'artifact-metadata'
        super().__init__(*args, **kwargs)

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


class TestBlobMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'blob-metadata'
        super().__init__(*args, **kwargs)

    def test_simple(self):
        img = self.test_client.cache_artifact(
                'https://sfcbr.shakenfist.com/cgi-bin/uuid.cgi?uniq=%06d'
                % random.randint(-999999, 999999))
        results = self._await_artifacts_ready([img['uuid']])
        img = results[0]

        self.assertIn('blobs', img)
        self.assertEqual(1, len(img['blobs']))
        self.assertIn(1, img['blobs'])
        b = img['blobs'][1]

        self.assertEqual({}, self.test_client.get_blob_metadata(b['uuid']))
        self.test_client.set_blob_metadata_item(b['uuid'], 'foo', 'bar')
        self.assertEqual(
            {'foo': 'bar'}, self.test_client.get_blob_metadata(b['uuid']))
        self.test_client.delete_blob_metadata_item(b['uuid'], 'foo')
        self.assertEqual({}, self.test_client.get_blob_metadata(b['uuid']))


class TestInstanceMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'instance-metadata'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
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


class TestInterfaceMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'interface-metadata'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
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

        iface = self.test_client.get_instance_interfaces(inst['uuid'])[0]

        self.assertEqual({}, self.test_client.get_interface_metadata(iface['uuid']))
        self.test_client.set_interface_metadata_item(iface['uuid'], 'foo', 'bar')
        self.assertEqual({
            'foo': 'bar'}, self.test_client.get_interface_metadata(iface['uuid']))
        self.test_client.delete_interface_metadata_item(iface['uuid'], 'foo')
        self.assertEqual({}, self.test_client.get_interface_metadata(iface['uuid']))


class TestNamespaceMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'namespace-metadata'
        super().__init__(*args, **kwargs)

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


class TestNetworkMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'network-metadata'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_simple(self):
        self.assertEqual({}, self.test_client.get_network_metadata(self.net['uuid']))
        self.test_client.set_network_metadata_item(self.net['uuid'], 'foo', 'bar')
        self.assertEqual({
            'foo': 'bar'}, self.test_client.get_network_metadata(self.net['uuid']))
        self.test_client.delete_network_metadata_item(self.net['uuid'], 'foo')
        self.assertEqual({}, self.test_client.get_network_metadata(self.net['uuid']))


class TestNodeMetadata(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'node-metadata'
        super().__init__(*args, **kwargs)

    def test_simple(self):
        n = self.system_client.get_nodes()[0]

        self.assertEqual({}, self.system_client.get_node_metadata(n['name']))
        self.system_client.set_node_metadata_item(n['name'], 'foo', 'bar')
        self.assertEqual({
            'foo': 'bar'}, self.system_client.get_node_metadata(n['name']))
        self.system_client.delete_node_metadata_item(n['name'], 'foo')
        self.assertEqual({}, self.system_client.get_node_metadata(n['name']))
