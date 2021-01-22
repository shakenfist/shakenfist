import base64

from shakenfist_ci import base


class TestCloudInit(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'cloudinit'
        super(TestCloudInit, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestCloudInit, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)

    def test_simple(self):
        ud = """#!/bin/sh
sudo echo 'banana' >  /tmp/output"""

        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
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

        # We need to refresh our view of the instance, as it might have
        # changed as it started up
        inst = self.test_client.get_instance(inst['uuid'])

        console = base.LoggingSocket(inst['node'], inst['console_port'])
        out = console.execute('cat /tmp/output')
        if not out.find('banana'):
            self.fail('User data script did not run!\n\n%s' % out)

        out = console.execute('cat /home/cirros/.ssh/authorized_keys')
        if not out.find('elLwq/bpzBWsg0JjjGvtuuKMM'):
            self.fail('ssh key was not placed in authorized keys!\n\n%s' % out)
