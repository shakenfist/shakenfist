from shakenfist_ci import base


class TestPlacement(base.BaseTestCase):
    def setUp(self):
        super(TestPlacement, self).setUp()

        self.namespace = 'ci-state-%s' % self._uniquifier()
        self.namespace_key = self._uniquifier()
        self.test_client = self._make_namespace(
            self.namespace, self.namespace_key)
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)

    def tearDown(self):
        super(TestPlacement, self).tearDown()
        for inst in self.test_client.get_instances():
            self.test_client.delete_instance(inst['uuid'])
        self.test_client.delete_network(self.net['uuid'])
        self._remove_namespace(self.namespace)

    def test_placement_works(self):
        # Create an instance, force it to be on the name node as us.
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'ubuntu:18.04',
                    'type': 'disk'
                }
            ], None, None, force_placement='sf-1')

        self._await_login_prompt(inst['uuid'])

        # NOTE(mikal): Ubuntu 18.04 has a bug where DHCP doesn't always work in the
        # cloud image. This is ok though, because we should be using the config drive
        # style interface information anyway.
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self._test_ping(self.net['uuid'], ip, True)

        # Ensure that deleting a local instance works
        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)

        # Create another instance, force it to be on a remote node.
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'ubuntu:18.04',
                    'type': 'disk'
                }
            ], None, None, force_placement='sf-2')

        self._await_login_prompt(inst['uuid'])

        # NOTE(mikal): Ubuntu 18.04 has a bug where DHCP doesn't always work in the
        # cloud image. This is ok though, because we should be using the config drive
        # style interface information anyway.
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self._test_ping(self.net['uuid'], ip, True)

        # Ensure that deleting a remote instance works
        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)
