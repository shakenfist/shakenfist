from shakenfist_ci import base


class TestObjectNames(base.BaseNamespacedTestCase):
    """Make sure instances boot under various configurations."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'namespace_test'
        super().__init__(*args, **kwargs)

    def test_object_names(self):
        """Check instances and networks names

        Testing API create_instance() using network name and instance/network
        retrieval by name.
        """

        nets = {}
        for i in ['barry', 'dave', 'alice']:
            n = self.test_client.allocate_network(
                '192.168.242.0/24', True, True, i+'_net')
            nets[i+'_net'] = n['uuid']

        for name, uuid in nets.items():
            n = self.system_client.get_network(name)
            self.assertEqual(uuid, n['uuid'])

        self._await_networks_ready(['barry_net'])

        inst_uuids = {}
        for name in ['barry', 'dave', 'trouble-writing-tests']:
            new_inst = self.test_client.create_instance(
                name, 1, 1024,
                [
                    {
                        'network_uuid': 'barry_net'
                    }
                ],
                [
                    {
                        'size': 8,
                        'base': 'sf://upload/system/debian-11',
                        'type': 'disk'
                    }
                ], None, None, namespace=self.namespace, side_channels=['sf-agent'])
            inst_uuids[name] = new_inst['uuid']

        # Get instance by name
        for name, uuid in inst_uuids.items():
            inst = self.system_client.get_instance(name)
            self.assertEqual(uuid, inst['uuid'])
