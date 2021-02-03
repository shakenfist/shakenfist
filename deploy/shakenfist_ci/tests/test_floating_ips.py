import base64
import requests
import time

from shakenfist_ci import base


class TestFloatingIPs(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'floating'
        super(TestFloatingIPs, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestFloatingIPs, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)

    def test_simple(self):
        ud = """#!/bin/sh
sudo apt-get update
sudo apt-get dist-upgrade -y
sudo apt-get install apache2 -y
sudo chmod ugo+rw /var/www/html/index.html
echo 'Floating IPs work!' > /var/www/html/index.html
"""

        inst = self.test_client.create_instance(
            'floating', 1, 1024,
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
                    'size': 20,
                    'base': 'ubuntu',
                    'type': 'disk'
                }
            ],
            None,
            str(base64.b64encode(ud.encode('utf-8')), 'utf-8'))

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        # We need to refresh our view of the instance, as it might have
        # changed as it started up
        inst = self.test_client.get_instance(inst['uuid'])

        console = base.LoggingSocket(inst['node'], inst['console_port'])
        out = console.execute('cat /var/www/html/index.html')
        if not out.find('Floating IPs work!'):
            self.fail('User data script did not run!\n\n%s' % out)

        ifaces = self.test_client.get_instance_interfaces(inst['uuid'])
        self.test_client.float_interface(ifaces[0]['uuid'])

        ifaces = self.test_client.get_instance_interfaces(inst['uuid'])
        self.assertNotEqual(None, ifaces[0]['floating'])

        time.sleep(120)

        attempts = 0
        for _ in range(6):
            attempts += 1
            try:
                r = requests.request(
                    'GET', 'http://%s/' % ifaces[0]['floating'])
                self.assertEqual(200, r.status_code)
                self.assertEqual('Floating IPs work!\n', r.text)
                return

            except Exception as e:
                if attempts < 5:
                    pass
                else:
                    raise e
