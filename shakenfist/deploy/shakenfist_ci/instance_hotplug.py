# Copyright 2019 Michael Still and contributors
"""Interface hot plug tests shared by the smoke and guest suites.

Until issue 4318 each suite's ``TestAgentOperations`` carried its own
near-identical copy of these scenarios, and the copies drifted: the
smoke copy gained the predictable-interface-naming diagnostics,
per-method hot plug MACs and the name-stable-across-reboot assertion
while the guest copy still hardcoded one MAC in five places, so every
fix had to be made twice and a fix made once looked complete in review.
The test bodies live in this module rather than in either suite
directory because stestr discovers tests per directory and the suites
are disjoint -- the same reasoning as ``database_tier.py``, the worked
example in ``docs/developer_guide/coding_rules.md``.
"""

import json
import time


class InstanceHotplugTestsMixin:
    """Hot plug an interface, then prove the guest can use it.

    Mix into a ``base.BaseNamespacedTestCase`` subclass in a suite
    directory; this class defines no ``__init__`` so the concrete class
    sets its own namespace prefix. The tests use ``self.test_client``,
    ``self.namespace`` and the instance and agent await helpers.
    """

    def test_interface_plug_and_exec_dhcp(self):
        # Create a network to hot plug to
        hotnet = self.test_client.allocate_network(
            '10.0.0.0/24', True, True, '%s-hotplug' % self.namespace)

        # Create an instance to run our command on
        inst = self.create_instance(
            'test-hotplug', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Hot plug an interface in. The MAC is unique to this test method
        # so parallel or sequential runs of sibling tests on the same
        # cluster do not collide on the UNIQUE constraint on
        # network_interfaces.macaddr. The smoke and guest suites never
        # share a cluster, so the same MAC in both is safe.
        hotplug_mac = '02:00:00:ea:3a:28'
        netdesc = {
            'network_uuid': hotnet['uuid'],
            'address': '10.0.0.5',
            'macaddress': hotplug_mac
        }
        self.test_client.add_instance_interface(inst['uuid'], netdesc)
        self._await_instance_operations_complete(inst['uuid'])

        # Wait a bit longer for the kernel to do its thing
        time.sleep(10)

        # Check lshw
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'sudo lshw -class network')
        self.assertNotEqual(
            -1, data.find(hotplug_mac),
            'Interface not found in `sudo lshw -class network` output:\n%s' % data)

        # List interfaces
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'ip -json link')
        self.assertNotEqual(
            -1, data.find(hotplug_mac),
            'Interface not found in `ip -json link` output:\n%s' % data)

        # Determine which interface the new one was added as
        d = json.loads(data)
        new_interface = None
        for i in d:
            if i['address'] == hotplug_mac:
                new_interface = i['ifname']
        self.assertNotEqual(None, new_interface)

        # DHCP on the new interface
        _, data = self.test_client.await_agent_command(
            inst['uuid'], f'dhclient {new_interface}')

        # Ensure interface picked up the right address
        _, data = self.test_client.await_agent_command(
            inst['uuid'], f'ip -4 -json -o addr show dev {new_interface}')
        d = json.loads(data)
        self.assertEqual('10.0.0.5', d[0]['addr_info'][0]['local'],
                         f'Wrong address in {data}')

    def test_interface_plug_and_exec_reboot(self):
        # Create a network to hot plug to
        hotnet = self.test_client.allocate_network(
            '10.0.0.0/24', True, True, '%s-hotplug' % self.namespace)

        # Create an instance to run our command on
        inst = self.create_instance(
            'test-hotplug', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Debug: check that predictable interface naming is
        # disabled inside the instance
        _, cmdline = self.test_client.await_agent_command(
            inst['uuid'], 'cat /proc/cmdline')
        self.assertIn(
            'net.ifnames=0', cmdline,
            'net.ifnames=0 not in kernel cmdline: '
            '%s' % cmdline)

        _, udev_rule = self.test_client.await_agent_command(
            inst['uuid'],
            'ls -la /etc/udev/rules.d/'
            '80-net-setup-link.rules',
            exit_codes=[0, 2],
            ignore_stderr=True)
        _, systemd_link = self.test_client.await_agent_command(
            inst['uuid'],
            'ls -la /etc/systemd/network/'
            '99-default.link',
            exit_codes=[0, 2],
            ignore_stderr=True)
        _, ifaces = self.test_client.await_agent_command(
            inst['uuid'], 'ip -json link')
        iface_names = [
            i['ifname'] for i in json.loads(ifaces)
            if i['ifname'] != 'lo'
        ]
        for name in iface_names:
            self.assertTrue(
                name.startswith('eth'),
                'Interface %s does not use eth naming. '
                'All interfaces: %s. '
                'Kernel cmdline: %s. '
                'udev rule: %s. '
                'systemd link: %s.'
                % (name, iface_names,
                   cmdline.strip(),
                   udev_rule.strip(),
                   systemd_link.strip()))

        # Hot plug an interface in. The MAC is unique to this test method
        # so parallel or sequential runs of sibling tests on the same
        # cluster do not collide on the UNIQUE constraint on
        # network_interfaces.macaddr.
        hotplug_mac = '02:00:00:ea:3a:29'
        netdesc = {
            'network_uuid': hotnet['uuid'],
            'address': '10.0.0.5',
            'macaddress': hotplug_mac
        }
        self.test_client.add_instance_interface(inst['uuid'], netdesc)
        self._await_instance_operations_complete(inst['uuid'])

        # Wait a bit longer for the kernel to do its thing
        time.sleep(10)

        # Check lshw
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'sudo lshw -class network')
        self.assertNotEqual(
            -1, data.find(hotplug_mac),
            'Interface not found in `sudo lshw -class network` output:\n%s' % data)

        # List interfaces
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'ip -json link')
        self.assertNotEqual(
            -1, data.find(hotplug_mac),
            'Interface not found in `ip -json link` output:\n%s' % data)

        # Determine which interface the new one was added as
        d = json.loads(data)
        new_interface = None
        for i in d:
            if i['address'] == hotplug_mac:
                new_interface = i['ifname']
        self.assertNotEqual(None, new_interface)

        # Power instance off and then on again to force re-creation of the
        # config drive.
        self.test_client.power_off_instance(inst['uuid'])
        self._await_instance_not_ready(inst['uuid'])
        self.test_client.power_on_instance(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        # List interfaces to ensure the device persisted
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'ip -json link')
        self.assertNotEqual(
            -1, data.find(hotplug_mac),
            'Interface not found in `ip -json link` output:\n%s' % data)

        # Determine which interface the new one is post reboot
        d = json.loads(data)
        new_interface_after_reboot = None
        for i in d:
            if i['address'] == hotplug_mac:
                new_interface_after_reboot = i['ifname']
        self.assertNotEqual(None, new_interface_after_reboot)
        self.assertEqual(
            new_interface,
            new_interface_after_reboot,
            (
                f'The interface name changed from {new_interface} to '
                f'{new_interface_after_reboot} across the reboot!'
            )
        )

        # Collect the config drive network configuration to ensure that the new
        # device is listed
        self.test_client.await_agent_command(
            inst['uuid'], 'mount /dev/vdb /mnt', ignore_stderr=True)
        data = self.test_client.await_agent_fetch(
            inst['uuid'], '/mnt/openstack/latest/network_data.json')
        self.assertTrue(hotplug_mac in data,
                        f'Expected mac address not present in {data}')

        # DHCP the new interface to ensure that works too
        self.test_client.await_agent_command(
            inst['uuid'], f'dhclient {new_interface}')

        # Ensure interface picked up the right address
        _, data = self.test_client.await_agent_command(
            inst['uuid'], f'ip -4 -json -o addr show dev {new_interface}')
        d = json.loads(data)
        self.assertNotEqual(0, len(d),
                            f'Wrong address information in {data}')
        self.assertTrue('addr_info' in d[0],
                        f'Wrong address information in {data}')
        self.assertNotEqual(0, len(d[0]['addr_info']),
                            f'Wrong address information in {data}')
        self.assertEqual('10.0.0.5', d[0]['addr_info'][0]['local'],
                         f'Wrong address information in {data}')
