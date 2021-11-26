import base64
import time

from shakenfist_ci import base


class TestCloudInit(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'cloudinit'
        super(TestCloudInit, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestCloudInit, self).setUp()
        self.net_one = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-1' % self.namespace)
        self.net_two = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net-2' % self.namespace)
        self._await_networks_ready([self.net_one['uuid'],
                                    self.net_two['uuid']])

    def test_simple(self):
        ud = """#!/bin/sh
sudo echo 'banana' >  /tmp/output"""

        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                },
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ],
            'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCuGJ47be0/3EH/q1b/2AYdh'
            'vTG/5L84QnKm3MhTO+cQGYfxw2AhPk6AOdHYPIp+t2wV/noc1eKCHN8n//T42'
            '4usEIQ/ODg9o2BeAhUU8S4qd6XSW5ihOknBZRnoQrYmAM6gUUvF4hLJ62Tzf/'
            'h2Hi9Wl774DRDs/Il5pBJnt+AdAgLcnVgJJG8KtX3JnynwnBOTlbKnyIWmEnH'
            'ZL+RH2+lIftsVXelLwq/bpzBWsg0JjjGvtuuKMMge0y3ZfsBA8/vLytaEV/vQ'
            'k/osilJeAbYa7Ul1K65S5eC2G2Yx4rNKdx0nn4lK2o/2keN52pDhrJbmK4907'
            'B50mWqtCFjsNULnfT5paInHRPgasKl007E0ZNNxhfXWieiVCUu/5zFiMPcWyB'
            '9YN60gp4lZSKB19GaURxtbKWlajfEakn3mTm9JQH5eU48XIaCh+LcptKYd6lD'
            'BWeoicQzECQLMfnKuGpfoZsKbOTTeCzS0/q6guKLNgfXijpRf5uaZaTqQa18t'
            '8s= mikal@marvin"',
            str(base64.b64encode(ud.encode('utf-8')), 'utf-8'))

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)
        out = console.execute('cat /tmp/output')
        if not out.find('banana'):
            self.fail('User data script did not run!\n\n%s' % out)

        out = console.execute('cat /home/cirros/.ssh/authorized_keys')
        if not out.find('elLwq/bpzBWsg0JjjGvtuuKMM'):
            self.fail('ssh key was not placed in authorized keys!\n\n%s' % out)

    def test_cloudinit_no_tracebacks(self):
        inst = self.test_client.create_instance(
            'notracebacks', 2, 2048,
            [
                {
                    'network_uuid': self.net_one['uuid']
                },
                {
                    'network_uuid': self.net_two['uuid'],
                    'address': None
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/ubuntu-2004',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])

        while inst['state'] not in ['created', 'error']:
            time.sleep(1)
            inst = self.test_client.get_instance(inst['uuid'])

        self._await_login_prompt(inst['uuid'])

        ifaces = self.test_client.get_instance_interfaces(inst['uuid'])
        self.assertEqual(2, len(ifaces))
        for iface in ifaces:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        c = self.test_client.get_console_data(inst['uuid'], 200000)
        self.assertFalse('Traceback' in c)
