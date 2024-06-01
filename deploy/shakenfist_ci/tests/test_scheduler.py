from shakenfist_ci import base


class TestAffinity(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'affinity'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_affinity(self):
        nodes = self.system_client.get_nodes()
        if len(nodes) < 3:
            self.skip('Insufficient nodes for test')

        # Create an instance with a tag
        inst1 = self.test_client.create_instance(
            'inst1', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/ubuntu-1804',
                    'type': 'disk'
                }
            ], None, None, metadata={
                'tags': ['first-node']
                }
            )
        self._await_instance_create(inst1['uuid'])

        # Now create two more instances, one with affinity one without
        inst2 = self.test_client.create_instance(
            'inst2', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/ubuntu-1804',
                    'type': 'disk'
                }
            ], None, None, metadata={
                'affinity': {
                    'first-node': 100
                    }
                }
            )
        inst3 = self.test_client.create_instance(
            'inst3', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/ubuntu-1804',
                    'type': 'disk'
                }
            ], None, None, metadata={
                'affinity': {
                    'first-node': -100
                    }
                }
            )

        self._await_instance_create(inst2['uuid'])
        self._await_instance_create(inst3['uuid'])

        # Refresh out view of the instances
        inst1 = self.test_client.get_instance(inst1['uuid'])
        inst2 = self.test_client.get_instance(inst2['uuid'])
        inst3 = self.test_client.get_instance(inst3['uuid'])

        # inst1 and inst2 should share a node, inst3 should not
        self.assertEqual(
            inst1['node'], inst2['node'],
            'Instances %s and %s should be on the same node but are not'
            % (inst1['uuid'], inst2['uuid']))
        self.assertNotEqual(
            inst1['node'], inst3['node'],
            'Instances %s and %s should not be on the same node but are'
            % (inst1['uuid'], inst3['uuid']))
