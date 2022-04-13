import socket

from shakenfist_client import apiclient

from shakenfist_ci import base


class TestPlacement(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'placement'
        super(TestPlacement, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestPlacement, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_no_such_node(self):
        # Make sure we get an except for a missing node
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.test_client.create_instance,
            'cirros', 1, 1024,
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
            ], None, None, force_placement='sf-nosuchnode',
            side_channels=['sf-agent'])

    def test_local_placement_works(self):
        # Create an instance, force it to be on the name node as us.
        try:
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
                        'base': 'sf://upload/system/ubuntu-1804',
                        'type': 'disk'
                    }
                ], None, None, force_placement=socket.getfqdn(),
                side_channels=['sf-agent'])
        except apiclient.ResourceNotFoundException as e:
            self.skip('Target node does not exist. %s' % e)
            return

        self._await_instance_ready(inst['uuid'])

        # NOTE(mikal): Ubuntu 18.04 has a bug where DHCP doesn't always work in the
        # cloud image. This is ok though, because we should be using the config drive
        # style interface information anyway.
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self._test_ping(inst['uuid'], self.net['uuid'], ip, 0)

        # Ensure that deleting a local instance works
        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)

    def test_remote_placement_works(self):
        # Create another instance, force it to be on a remote node.
        try:
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
                        'base': 'sf://upload/system/ubuntu-1804',
                        'type': 'disk'
                    }
                ], None, None, force_placement='sf-2', side_channels=['sf-agent'])
        except apiclient.ResourceNotFoundException as e:
            self.skip('Target node does not exist. %s' % e)
            return

        self._await_instance_ready(inst['uuid'])

        # NOTE(mikal): Ubuntu 18.04 has a bug where DHCP doesn't always work in the
        # cloud image. This is ok though, because we should be using the config drive
        # style interface information anyway.
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self._test_ping(inst['uuid'], self.net['uuid'], ip, 0)

        # Ensure that deleting a remote instance works
        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)
