import json
import os
import time

from testtools import content

from shakenfist_ci import base


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

    def _await_agentop_complete(self, instance_uuid, aop, timeout):
        # Poll a single agent operation to completion with its own independent
        # timeout window. Previously test_instance_put_and_get_blob shared one
        # start_time across three sequential operations, so the last and
        # heaviest of them (get-file, which reads, hashes and uploads a blob
        # back to the cluster) was left only whatever budget the earlier
        # operations had not already consumed. Under under-cloud contention
        # that remainder collapsed and get-file "timed out" while still
        # legitimately executing -- the intermittent merge-queue flake.
        start_time = time.time()
        while aop['state'] != 'complete':
            if time.time() - start_time > timeout:
                console_data = self.test_client.get_console_data(instance_uuid)
                self.fail(
                    f'Timeout for agentop: {aop}\n\nConsole: {console_data}')
            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])
        return aop

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

        # Wait for the instance agent to report in
        self._await_instance_ready(inst['uuid'])

        # Execute a command
        start_time = time.time()
        aop = self.test_client.instance_execute(inst['uuid'], 'whoami')
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                console_data = self.test_client.get_console_data(inst['uuid'])
                self.fail(
                    f'Timeout for agentop: {aop}\n\nConsole: {console_data}')
            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

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
        start_time = time.time()
        aop = self.test_client.instance_execute(
            inst['uuid'], 'cat /var/log/syslog')

        # Wait for the operation to complete
        while aop['state'] != 'complete':
            if time.time() - start_time > 30:
                console_data = self.test_client.get_console_data(inst['uuid'])
                self.fail(
                    f'Timeout for agentop: {aop}\n\nConsole: {console_data}')
            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

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

        # Pick a blob and send it to the instance
        blobs = self.system_client.get_blobs()
        self.assertNotEqual(0, len(blobs))

        blob_uuid = None
        for blob in blobs:
            if blob['checksums'].get('sha512'):
                blob_uuid = blob['uuid']
                cluster_hash = blob['checksums']['sha512']
                break

        self.assertNotEqual(
            None, blob_uuid, 'Failed to find a blob with a hash')

        aop = self.test_client.instance_put_blob(
            inst['uuid'], blob_uuid, '/tmp/foo', 'ugo+r')
        aop = self._await_agentop_complete(inst['uuid'], aop, 60)

        # Now ensure the data arrived correctly
        aop = self.test_client.instance_execute(
            inst['uuid'], 'sha512sum /tmp/foo')
        aop = self._await_agentop_complete(inst['uuid'], aop, 60)

        remote_hash = aop['results']['0']['stdout'].split(' ')[0]
        self.assertEqual(
            cluster_hash, remote_hash,
            f'Cluster hash {cluster_hash} does not match remote hash'
            f'{remote_hash}')

        # Now fetch the data back. get-file is the heaviest operation (the
        # agent reads the file, hashes it and uploads it back as a new blob),
        # so it gets a more generous independent budget.
        aop = self.test_client.instance_get(inst['uuid'], '/tmp/foo')
        aop = self._await_agentop_complete(inst['uuid'], aop, 120)

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
            base.AGENT_OPERATION_FAILURES, self.test_client.await_agent_fetch,
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

        # Hot plug an interface in
        netdesc = {
            'network_uuid': hotnet['uuid'],
            'address': '10.0.0.5',
            'macaddress': '02:00:00:ea:3a:28'
        }
        self.test_client.add_instance_interface(inst['uuid'], netdesc)
        self._await_instance_operations_complete(inst['uuid'])

        # Wait a bit longer for the kernel to do its thing
        time.sleep(10)

        # Check lshw
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'sudo lshw -class network')
        self.assertNotEqual(
            -1, data.find('02:00:00:ea:3a:28'),
            'Interface not found in `sudo lshw -class network` output:\n%s' % data)

        # List interfaces
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'ip -json link')
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

        # Hot plug an interface in
        netdesc = {
            'network_uuid': hotnet['uuid'],
            'address': '10.0.0.5',
            'macaddress': '02:00:00:ea:3a:28'
        }
        self.test_client.add_instance_interface(inst['uuid'], netdesc)
        self._await_instance_operations_complete(inst['uuid'])

        # Wait a bit longer for the kernel to do its thing
        time.sleep(10)

        # Check lshw
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'sudo lshw -class network')
        self.assertNotEqual(
            -1, data.find('02:00:00:ea:3a:28'),
            'Interface not found in `sudo lshw -class network` output:\n%s' % data)

        # List interfaces
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'ip -json link')
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
        _, data = self.test_client.await_agent_command(
            inst['uuid'], 'ip -json link')
        self.assertNotEqual(
            -1, data.find('02:00:00:ea:3a:28'),
            'Interface not found in `ip -json link` output:\n%s' % data)

        # Collect the config drive network configuration to ensure that the new
        # device is listed
        self.test_client.await_agent_command(
            inst['uuid'], 'mount /dev/vdb /mnt', ignore_stderr=True)
        data = self.test_client.await_agent_fetch(
            inst['uuid'], '/mnt/openstack/latest/network_data.json')
        self.assertTrue('02:00:00:ea:3a:28' in data,
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
