import json

from testtools import content

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
            self.addDetail(
                'net_%s' % i,
                content.text_content(json.dumps(n, indent=4, sort_keys=True)))
            nets[i+'_net'] = n['uuid']
        self.addDetail(
            'nets',
            content.text_content(json.dumps(nets, indent=4, sort_keys=True)))

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
                        'base': base.CLUSTER_CI_IMAGE,
                        'type': 'disk'
                    }
                ], None, None, namespace=self.namespace)
            self.addDetail(
                'new_inst_%s' % name,
                content.text_content(json.dumps(new_inst, indent=4,
                                                sort_keys=True)))
            inst_uuids[name] = new_inst['uuid']
        self.addDetail(
            'inst_uuids',
            content.text_content(json.dumps(inst_uuids, indent=4,
                                            sort_keys=True)))

        # Get instance by name
        for name, uuid in inst_uuids.items():
            inst = self.system_client.get_instance(name)
            self.addDetail(
                'inst_%s' % name,
                content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
            self.assertEqual(uuid, inst['uuid'])
