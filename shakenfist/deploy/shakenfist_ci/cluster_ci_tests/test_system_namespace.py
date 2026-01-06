import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestSystemNamespace(base.BaseTestCase):
    def test_system_namespace(self):
        self.assertEqual('system', self.system_client.namespace)

        net = self.system_client.allocate_network(
            '192.168.242.0/24', True, True,
            'ci-system-net')
        self.addDetail(
            'net',
            content.text_content(json.dumps(net, indent=4, sort_keys=True)))
        nets = []
        for n in self.system_client.get_networks():
            nets.append(n['uuid'])
        self.addDetail(
            'nets',
            content.text_content(json.dumps(nets, indent=4, sort_keys=True)))
        self.assertIn(net['uuid'], nets)

        inst = self.system_client.create_instance(
            'test-system-ns', 1, 1024,
            [
                {
                    'network_uuid': net['uuid']
                }
            ],
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
        self.assertIsNotNone(inst['node'])

        insts = []
        for i in self.system_client.get_instances():
            insts.append(i['uuid'])
        self.addDetail(
            'insts',
            content.text_content(json.dumps(insts, indent=4, sort_keys=True)))
        self.assertIn(inst['uuid'], insts)

        self.system_client.delete_instance(inst['uuid'])
        self._await_instance_deleted(inst['uuid'])

        self.system_client.delete_network(net['uuid'])

        self.assertRaises(
            apiclient.UnauthorizedException,
            self.system_client.delete_namespace, None)
