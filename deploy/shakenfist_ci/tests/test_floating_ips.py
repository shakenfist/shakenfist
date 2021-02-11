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
        self._await_network_ready(self.net['uuid'])

    def test_simple(self):
        self.skip('Disabled because unreliable')

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
        self._await_cloud_init_complete(inst['uuid'])

        # We need to refresh our view of the instance, as it might have
        # changed as it started up
        inst = self.test_client.get_instance(inst['uuid'])

        # Wait for boot and cloud-init
        time.sleep(120)

        console = base.LoggingSocket(inst['node'], inst['console_port'])
        out = console.execute('cat /var/www/html/index.html')
        if not out.find('Floating IPs work!'):
            self.fail('User data script did not run!\n\n%s' % out)

        ifaces = self.test_client.get_instance_interfaces(inst['uuid'])
        self.test_client.float_interface(ifaces[0]['uuid'])

        ifaces = self.test_client.get_instance_interfaces(inst['uuid'])
        self.assertNotEqual(None, ifaces[0]['floating'])

        # Because the user data in this test does a dist-upgrade and installs
        # a package, it can take a long time to run. This happens after the
        # instance presents its first login prompt (checked above), so we
        # need to sleep for a disturbingly long time just in case.
        time.sleep(300)

        attempts = 0
        for _ in range(10):
            attempts += 1
            try:
                r = requests.request(
                    'GET', 'http://%s/' % ifaces[0]['floating'])

                if r.status_code == 200:
                    if r.text.find('Floating IPs work!') != -1:
                        return
                    print('Floating IPs test attempt failed, incorrect HTTP '
                          'result')
                else:
                    print('Floating IPs test attempt received HTTP status %s'
                          % r.status_code)

            except Exception as e:
                print('Floating IPs test attempt failed with exception: %s' % e)

            time.sleep(30)

        self.fail('Incorrect result after %d attempts, instance was %s'
                  % (attempts, inst['uuid']))
