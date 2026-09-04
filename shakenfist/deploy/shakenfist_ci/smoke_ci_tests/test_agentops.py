import json
import os
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestAgentOperations(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'agentops'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net_one = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace,
            provide_dns=True)
        self.addDetail(
            'net_one',
            content.text_content(json.dumps(self.net_one, indent=4,
                                            sort_keys=True)))
        self._await_networks_ready([self.net_one['uuid']])

    def test_instance_execute_small(self):
        inst = self.test_client.create_instance(
            'test-instance-execute-small', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
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

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Execute a command
        aop = self.test_client.instance_execute(inst['uuid'], 'whoami')
        aop = self._await_agentop_complete(inst['uuid'], aop, 30, 'whoami')

        self.assertTrue(
            '0' in aop['results'],
            f'Agent operation results lack expected result key "0": {aop}')
        self.assertTrue(
            'stdout' in aop['results']['0'],
            f'Agent operation result 0 lacks expected result key "stdout": {aop}')
        self.assertEqual(
            'root\n', aop['results']['0']['stdout'],
            f'Agent operation result "0" stdout value lacks expected value '
            f'"root\\n": {aop}')

    def test_instance_execute_large(self):
        inst = self.test_client.create_instance(
            'test-instance-execute-large', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Execute a command
        aop = self.test_client.instance_execute(
            inst['uuid'], 'cat /var/log/syslog')

        # Wait for the operation to complete
        aop = self._await_agentop_complete(
            inst['uuid'], aop, 30, 'cat /var/log/syslog')

        self.assertTrue(
            '0' in aop['results'],
            f'Agent operation results lack expected result key "0": {aop}')
        self.assertTrue(
            'stdout' not in aop['results']['0'],
            f'Agent operation result "0" has unexpected result key "stdout": {aop}')
        self.assertTrue(
            'stdout_blob' in aop['results']['0'],
            'Agent operation result "0" lacks expected result key '
            f'"stdout_blob": {aop}')

        b = self.test_client.get_blob(aop['results']['0']['stdout_blob'])
        self.assertNotEqual(None, b)

    def test_put_and_exec_large_stdout(self):
        # Create an instance to run our script on
        inst = self.test_client.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Upload our script
        upl = self.test_client.create_upload()
        test_dir = os.path.dirname(os.path.abspath(__file__))
        with open('%s/files/fibonacci.py' % test_dir, 'rb') as f:
            self.test_client.send_upload_file(upl['uuid'], f)
        input = self.test_client.upload_artifact(
            'fibonacci', upl['uuid'], artifact_type='other')
        input_blob = input['blob_uuid']

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Request that the agent copy the file to the instance
        op = self.test_client.instance_put_blob(
            inst['uuid'], input_blob, '/tmp/fibonacci.py', 'ugo+rx')
        op = self._await_agentop_complete(
            inst['uuid'], op, 120, 'put fibonacci.py')

        # Request that the agent execute the file
        _, data = self.test_client.await_agent_command(
            inst['uuid'], '/tmp/fibonacci.py')
        self.assertTrue(data.startswith(
            '[0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987'))

    def test_instance_put_and_get_blob(self):
        inst = self.test_client.create_instance(
            'test-instance-put-blob', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 20,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        # Create a blob to use for the test by uploading a file
        upl = self.test_client.create_upload()
        test_dir = os.path.dirname(os.path.abspath(__file__))
        with open('%s/files/fibonacci.py' % test_dir, 'rb') as f:
            self.test_client.send_upload_file(upl['uuid'], f)
        artifact = self.test_client.upload_artifact(
            'test-blob', upl['uuid'], artifact_type='other')
        blob_uuid = artifact['blob_uuid']

        # Wait for the blob's sha512 checksum to be calculated
        start_time = time.time()
        cluster_hash = self.test_client.get_blob_hash(blob_uuid, 'sha512')
        while not cluster_hash:
            if time.time() - start_time > 60:
                self.fail(
                    f'Checksum for blob {blob_uuid} not available after 60 '
                    'seconds')
            time.sleep(5)
            cluster_hash = self.test_client.get_blob_hash(blob_uuid, 'sha512')

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        aop = self.test_client.instance_put_blob(
            inst['uuid'], blob_uuid, '/tmp/foo', 'ugo+r')
        aop = self._await_agentop_complete(inst['uuid'], aop, 30, 'put /tmp/foo')

        # Now ensure the data arrived correctly
        aop = self.test_client.instance_execute(
            inst['uuid'], 'sha512sum /tmp/foo')
        aop = self._await_agentop_complete(
            inst['uuid'], aop, 60, 'sha512sum /tmp/foo')

        remote_hash = aop['results']['0']['stdout'].split(' ')[0]
        self.assertEqual(
            cluster_hash, remote_hash,
            f'Cluster hash {cluster_hash} does not match remote hash'
            f'{remote_hash}')

        # Now fetch the data back. get-file is the heaviest operation (the
        # agent reads the file, hashes it and uploads it back as a new blob),
        # so it gets a more generous independent budget.
        aop = self.test_client.instance_get(inst['uuid'], '/tmp/foo')
        aop = self._await_agentop_complete(inst['uuid'], aop, 120, 'get /tmp/foo')

        self.assertTrue(
            '0' in aop['results'],
            f'Agent operation results lack expected result key "0": {aop}')
        self.assertTrue(
            'stat_result' in aop['results']['0'],
            f'Agent operation results lacks stat results: {aop}')
        self.assertTrue(
            'content_blob' in aop['results']['0'],
            f'Agent operation results lacks stat results: {aop}')

        b = self.test_client.get_blob(aop['results']['0']['content_blob'])
        self.assertNotEqual(None, b)

        start_time = time.time()
        fetched_hash = self.test_client.get_blob_hash(b['uuid'], 'sha512')
        while not fetched_hash:
            if time.time() - start_time > 60:
                self.fail(
                    f'Checksum for blob {b["uuid"]} not available after 60 '
                    'seconds')

            time.sleep(5)
            fetched_hash = self.test_client.get_blob_hash(b['uuid'], 'sha512')

        self.assertEqual(cluster_hash, fetched_hash)

    def test_get(self):
        # Create an instance to fetch files from
        inst = self.test_client.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Run a simple fetch command
        data = self.test_client.await_agent_fetch(
            inst['uuid'], '/etc/os-release')
        self.assertTrue(data.startswith('PRETTY_NAME='))

    def test_get_missing_file(self):
        # Create an instance to fetch files from
        inst = self.test_client.create_instance(
            'test-put-and-get-file-missing', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Run a fetch command which should fail
        self.assertRaises(
            apiclient.AgentOperationFailed, self.test_client.await_agent_fetch,
            inst['uuid'], '/tmp/nosuch')

    def test_interface_plug_and_exec_dhcp(self):
        # Create a network to hot plug to
        hotnet = self.test_client.allocate_network(
            '10.0.0.0/24', True, True, '%s-hotplug' % self.namespace)

        # Create an instance to run our command on
        inst = self.test_client.create_instance(
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
        # network_interfaces.macaddr.
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
                         'Wrong address in {data}')

    def test_interface_plug_and_exec_reboot(self):
        # Create a network to hot plug to
        hotnet = self.test_client.allocate_network(
            '10.0.0.0/24', True, True, '%s-hotplug' % self.namespace)

        # Create an instance to run our command on
        inst = self.test_client.create_instance(
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
