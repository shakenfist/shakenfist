import json
import os
import time

from shakenfist_ci import base


class TestAgentFileOperations(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'agentfileops'
        super().__init__(*args, **kwargs)

    def test_put_and_exec_large_stdout(self):
        # Create an instance to run our script on
        inst = self.test_client.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
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

        start_time = time.time()
        while time.time() - start_time < 120:
            if op['state'] == 'complete':
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        if op['state'] != 'complete':
            self.fail('Agent put operation %s did not complete in 120 seconds (%s)'
                      % (op['uuid'], op['state']))

        # Request that the agent execute the file
        _, data = self._await_agent_command(inst['uuid'], '/tmp/fibonacci.py')
        self.assertTrue(data.startswith(
            '[0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987'))

    def test_exec_small_stdout(self):
        # Create an instance to run our command on
        inst = self.test_client.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Run a simple command
        op = self.test_client.instance_execute(inst['uuid'], 'cat /etc/os-release')

        # Wait for the operation to be complete
        start_time = time.time()
        while time.time() - start_time < 120:
            if op['state'] == 'complete':
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        if op['state'] != 'complete':
            self.fail('Agent execute operation %s did not complete in 120 seconds (%s)'
                      % (op['uuid'], op['state']))

        # Wait for the operation to have results
        start_time = time.time()
        while time.time() - start_time < 60:
            if op['results'] != {}:
                break
            time.sleep(5)
            op = self.test_client.get_agent_operation(op['uuid'])

        self.assertNotEqual({}, op['results'])
        self.assertEqual(0, op['results']['0']['return-code'])
        self.assertFalse('stdout_blob' in op['results']['0'])
        self.assertTrue('stdout' in op['results']['0'])

        self.assertTrue(op['results']['0']['stdout'].startswith('PRETTY_NAME='))
        self.assertEqual(0, len(op['results']['0']['stderr']))

    def test_get(self):
        # Create an instance to fetch files from
        inst = self.test_client.create_instance(
            'test-put-and-get-file', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Run a simple fetch command
        data = self._await_agent_fetch(inst['uuid'], '/etc/os-release')
        self.assertTrue(data.startswith('PRETTY_NAME='))

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
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Hot plug an interface in
        netdesc = {
            'network_uuid': hotnet['uuid'],
            'address': '10.0.0.5',
            'macaddress': '02:00:00:ea:3a:28'
        }
        self.test_client.add_instance_interface(inst['uuid'], netdesc)
        time.sleep(10)

        # List interfaces
        _, data = self._await_agent_command(inst['uuid'], 'ip -json link')
        self.assertNotEqual(
            -1, data.find('02:00:00:ea:3a:28'),
            'Interface not found in `ip -json link` output:\n%s' % data)

        # Determine which interface the new one was added as
        d = json.loads(data)
        new_interface = None
        for i in d:
            if i['address'] == '02:00:00:ea:3a:28':
                new_interface = i['ifname']
        self.assertNotEqual(None, new_interface)

        # DHCP on the new interface
        _, data = self._await_agent_command(
            inst['uuid'], f'dhclient {new_interface}')

        # Ensure interface picked up the right address
        _, data = self._await_agent_command(
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
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Hot plug an interface in
        netdesc = {
            'network_uuid': hotnet['uuid'],
            'address': '10.0.0.5',
            'macaddress': '02:00:00:ea:3a:28'
        }
        self.test_client.add_instance_interface(inst['uuid'], netdesc)
        time.sleep(10)

        # List interfaces
        _, data = self._await_agent_command(inst['uuid'], 'ip -json link')
        self.assertNotEqual(
            -1, data.find('02:00:00:ea:3a:28'),
            'Interface not found in `ip -json link` output:\n%s' % data)

        # Determine which interface the new one was added as
        d = json.loads(data)
        new_interface = None
        for i in d:
            if i['address'] == '02:00:00:ea:3a:28':
                new_interface = i['ifname']
        self.assertNotEqual(None, new_interface)

        # Power instance off and then on again to force re-creation of the
        # config drive.
        self.test_client.power_off_instance(inst['uuid'])
        self._await_instance_not_ready(inst['uuid'])
        self.test_client.power_on_instance(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        # List interfaces to ensure the device persisted
        _, data = self._await_agent_command(inst['uuid'], 'ip -json link')
        self.assertNotEqual(
            -1, data.find('02:00:00:ea:3a:28'),
            'Interface not found in `ip -json link` output:\n%s' % data)

        # Collect the config drive network configuration to ensure that the new
        # device is listed
        self._await_agent_command(inst['uuid'], 'mount /dev/vdb /mnt',
                                  ignore_stderr=True)
        data = self._await_agent_fetch(
            inst['uuid'], '/mnt/openstack/latest/network_data.json')
        self.assertTrue('02:00:00:ea:3a:28' in data,
                        f'Expected mac address not present in {data}')

        # DHCP the new interface to ensure that works too
        self._await_agent_command(inst['uuid'], f'dhclient {new_interface}')

        # Ensure interface picked up the right address
        _, data = self._await_agent_command(
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
