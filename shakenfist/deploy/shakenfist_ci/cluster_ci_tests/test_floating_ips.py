import base64
import ipaddress
import json
import os
import time

import requests
from oslo_concurrency import processutils
from testtools import content

from shakenfist_ci import base


class TestFloatingIPs(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'floating'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def test_simple(self):
        self.skipTest('Disabled because unreliable')

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
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ],
            None,
            str(base64.b64encode(ud.encode('utf-8')), 'utf-8'))
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        # Wait for boot and cloud-init
        time.sleep(120)

        out = self.test_client.await_agent_fetch(
            inst['uuid'], '/var/www/html/index.html')
        self.assertEqual('Floating IPs work!', out.rstrip())

        ifaces = self.test_client.get_instance_interfaces(inst['uuid'])
        self.addDetail(
            'ifaces',
            content.text_content(json.dumps(ifaces, indent=4, sort_keys=True)))
        self.test_client.float_interface(ifaces[0]['uuid'])

        ifaces = self.test_client.get_instance_interfaces(inst['uuid'])
        self.addDetail(
            'ifaces after float',
            content.text_content(json.dumps(ifaces, indent=4, sort_keys=True)))
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


class TestFloatingIPLifecycle(base.BaseNamespacedTestCase):
    """Floating IPs must be plumbed on float and fully cleaned on defloat.

    The host state for a floating IP on the network node is a veth pair
    (the outer end named flt-<hex> in the root namespace and the inner
    end named flt-<hex>-i inside the virtual network's namespace, where
    <hex> is the floating IPv4 address as eight lower case hex digits),
    the floating address as a /32 on the inner end, and a DNAT PREROUTING
    rule in the network namespace directing the floating address to the
    instance's inner address. See the interface naming conventions
    section of the networking operator guide.

    Anything left behind on defloat slowly poisons the floating pool: a
    later user of the same floating address either fails to configure it
    (the stale inner end is stranded in another network's namespace), or
    has its traffic misdirected by the stale DNAT rule matching before
    the new one. That is the failure mode from github issues #3378
    through #3383, where removal used the wrong interface name and
    therefore leaked on every release.

    The host level assertions require this test to run on the network
    node, which is true for the CI topologies the shakenfist.shakenfist
    ansible collection deploys (the primary node is the network node and
    runs this suite). Elsewhere the host assertions are skipped and only
    the API state and reachability assertions run.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'floatlifecycle'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def _on_network_node(self):
        return os.path.exists('/var/run/netns/%s' % self.net['uuid'])

    def _floating_interface_name(self, floating):
        return 'flt-%08x' % int(ipaddress.IPv4Address(floating))

    def _host_link_names(self):
        out, _ = processutils.execute('ip -json link show', shell=True)
        return [i['ifname'] for i in json.loads(out) if i]

    def _floating_dnat_rules(self, floating):
        out, _ = processutils.execute(
            'sudo ip netns exec %s iptables -w 10 -t nat -S PREROUTING'
            % self.net['uuid'], shell=True)
        return [r for r in out.split('\n') if '-d %s/32 ' % floating in r]

    def _await(self, callback, description, timeout=120):
        start_time = time.time()
        while time.time() - start_time < timeout:
            if callback():
                return
            time.sleep(5)
        self.fail('Timed out waiting for %s' % description)

    def _await_floating_address(self, interface_uuid, present=True):
        self._await(
            lambda: bool(self.test_client.get_interface(
                interface_uuid).get('floating')) == present,
            'floating address to be %s on interface %s'
            % (['removed', 'assigned'][present], interface_uuid))
        return self.test_client.get_interface(interface_uuid).get('floating')

    def _assert_floating_plumbed(self, floating, inner):
        if not self._on_network_node():
            return

        outer = self._floating_interface_name(floating)
        self._await(
            lambda: outer in self._host_link_names(),
            'floating interface %s to appear' % outer)

        self._await(
            lambda: len(self._floating_dnat_rules(floating)) > 0,
            'DNAT rule for %s to appear' % floating)
        rules = self._floating_dnat_rules(floating)
        self.assertEqual(
            1, len(rules),
            'Expected exactly one DNAT rule for %s, found: %s'
            % (floating, rules))
        self.assertIn('--to-destination %s' % inner, rules[0])

    def _assert_floating_cleaned(self, floating):
        if not self._on_network_node():
            return

        outer = self._floating_interface_name(floating)
        self._await(
            lambda: outer not in self._host_link_names(),
            'floating interface %s to be removed' % outer)
        self._await(
            lambda: not self._floating_dnat_rules(floating),
            'DNAT rules for %s to be removed' % floating)

    def _await_floating_ping(self, floating):
        start_time = time.time()
        while time.time() - start_time < 300:
            out, _ = processutils.execute(
                'ping -c 3 -W 2 %s' % floating, shell=True,
                check_exit_code=[0, 1, 2])
            if out.find('bytes from') != -1:
                return
            time.sleep(15)
        self.fail('Could not ping floating address %s' % floating)

    def test_float_defloat_lifecycle(self):
        inst = self.test_client.create_instance(
            'floatlifecycle', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ],
            None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        iface = self.test_client.get_instance_interfaces(inst['uuid'])[0]
        inner = iface['ipv4']

        # Two full float / defloat cycles. The second cycle is the
        # regression test for state leaked by the first: repeated use of
        # floating addresses must start from a clean slate every time.
        for cycle in range(2):
            self.test_client.float_interface(iface['uuid'])
            floating = self._await_floating_address(iface['uuid'])
            self.addDetail(
                'cycle %d floating address' % cycle,
                content.text_content(floating))

            self._assert_floating_plumbed(floating, inner)
            self._await_floating_ping(floating)

            self.test_client.defloat_interface(iface['uuid'])
            self._await_floating_address(iface['uuid'], present=False)
            self._assert_floating_cleaned(floating)

        # Deleting an instance with a floating IP still attached must
        # also clean up the floating IP's host state. This is the common
        # path for ephemeral CI instances.
        self.test_client.float_interface(iface['uuid'])
        floating = self._await_floating_address(iface['uuid'])
        self._assert_floating_plumbed(floating, inner)

        self.test_client.delete_instance(inst['uuid'])
        self._await_instance_deleted(inst['uuid'])
        self._assert_floating_cleaned(floating)
