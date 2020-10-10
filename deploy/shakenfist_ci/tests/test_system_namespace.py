import time

from shakenfist_client import apiclient

from shakenfist_ci import base


class TestSystemNamespace(base.BaseTestCase):
    def test_system_namespace(self):
        self.assertEqual('system', self.system_client.namespace)

        net = self.system_client.allocate_network(
            '192.168.242.0/24', True, True,
            'ci-system-net')
        nets = []
        for n in self.system_client.get_networks():
            nets.append(n['uuid'])
        self.assertIn(net['uuid'], nets)

        inst = self.system_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self.assertIsNotNone(inst['node'])

        insts = []
        for i in self.system_client.get_instances():
            insts.append(i['uuid'])
        self.assertIn(inst['uuid'], insts)

        self.system_client.delete_instance(inst['uuid'])
        start_time = time.time()
        while time.time() - start_time < 300:
            if not list(self.system_client.get_instances()):
                break
            time.sleep(5)

        self.system_client.delete_network(net['uuid'])

        self.assertRaises(
            apiclient.ResourceCannotBeDeletedException,
            self.system_client.delete_namespace, None)
