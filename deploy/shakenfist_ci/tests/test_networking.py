from shakenfist_ci import base
from shakenfist_client import apiclient


class TestNetworking(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'net'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net_one = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace,
            provide_dns=True)
        self.net_two = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net-two' % self.namespace)
        self.net_three = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-three' % self.namespace)
        self.net_four = self.test_client.allocate_network(
            '192.168.10.0/24', True, True, '%s-net-four' % self.namespace)
        self._await_networks_ready([self.net_one['uuid'],
                                    self.net_two['uuid'],
                                    self.net_three['uuid'],
                                    self.net_four['uuid']])

    def test_network_validity(self):
        self.assertRaises(apiclient.APIException, self.test_client.allocate_network,
                          '192.168.242.2', True, True, '%s-validity1' % self.namespace)
        self.assertRaises(apiclient.APIException, self.test_client.allocate_network,
                          '192.168.242.2/32', True, True, '%s-validity2' % self.namespace)
        self.assertRaises(apiclient.APIException, self.test_client.allocate_network,
                          '192.168.242.0/30', True, True, '%s-validity3' % self.namespace)
        n = self.test_client.allocate_network(
            '192.168.10.0/29', True, True, '%s-validity2' % self.namespace)
        self.test_client.delete_network(n['uuid'])

    def test_virtual_networks_are_separate(self):
        inst1 = self.test_client.create_instance(
            'test-networks-separate-1', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        inst2 = self.test_client.create_instance(
            'test-networks-separate-1', 1, 1024,
            [
                {
                    'network_uuid': self.net_two['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst2['uuid'])

        self._await_instance_ready(inst1['uuid'])
        self._await_instance_ready(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        results = self._await_command(inst1['uuid'], 'ping -c 3 %s' % nics[0]['ipv4'])
        self.assertEqual(1, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue(' 100% packet' in results['stdout'])

    def test_overlapping_virtual_networks_are_separate(self):
        inst1 = self.test_client.create_instance(
            'test-overlap-cidr-1', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        inst2 = self.test_client.create_instance(
            'test-overlap-cidr-1', 1, 1024,
            [
                {
                    'network_uuid': self.net_three['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst2['uuid'])

        self._await_instance_ready(inst1['uuid'])
        self._await_instance_ready(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        results = self._await_command(inst1['uuid'], 'ping -c 3 %s' % nics[0]['ipv4'])
        self.assertEqual(1, results['return-code'],
                         'Incorrect return code: %s' % results)
        self.assertEqual('', results['stderr'])
        self.assertTrue(' 100% packet' in results['stdout'])

    def test_single_virtual_networks_work(self):
        inst1 = self.test_client.create_instance(
            'test-networks-1', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])

        inst2 = self.test_client.create_instance(
            'test-networks-2', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst2['uuid'])

        self._await_instance_ready(inst1['uuid'])
        self._await_instance_ready(inst2['uuid'])

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        # Ping the other instance on this network
        results = self._await_command(inst1['uuid'], 'ping -c 3 %s' % nics[0]['ipv4'])
        self.assertEqual(0, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue(' 0% packet' in results['stdout'], results['stdout'])

        # Ping google (prove NAT works)
        results = self._await_command(inst1['uuid'], 'ping -c 3 8.8.8.8')
        self.assertEqual(0, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue(' 0% packet' in results['stdout'], results['stdout'])

    def test_specific_ip_request(self):
        inst = self.test_client.create_instance(
            'test-specific-ip', 1, 1024,
            [
                {
                    'network_uuid': self.net_four['uuid'],
                    'address': '192.168.10.56'
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        self._await_instances_ready([inst['uuid']])

        nics = self.test_client.get_instance_interfaces(inst['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        ips = []
        for nic in nics:
            ips.append(nic['ipv4'])

        self.assertEqual(['192.168.10.56'], ips)

    def test_specific_ip_request_invalid(self):
        self.assertRaises(
            apiclient.RequestMalformedException,
            self.test_client.create_instance,
            'test-invalid-ip', 1, 1024,
            [
                {
                    'network_uuid': self.net_four['uuid'],
                    'address': '192.168.100.56'
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

    def test_specific_macaddress_request(self):
        inst = self.test_client.create_instance(
            'test-macaddress', 1, 1024,
            [
                {
                    'network_uuid': self.net_four['uuid'],
                    'macaddress': '04:ed:33:c0:2e:6c'
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])

        self._await_instance_ready(inst['uuid'])

        results = self._await_command(inst['uuid'], 'ip link')
        self.assertEqual(0, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue('04:ed:33:c0:2e:6c' in results['stdout'])

    def test_interface_delete(self):
        inst1 = self.test_client.create_instance(
            'test-iface-delete', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self._await_instance_ready(inst1['uuid'])

        # We need to refresh our view of the instances, as it might have
        # changed as they started up
        inst1 = self.test_client.get_instance(inst1['uuid'])

        nics = self.test_client.get_instance_interfaces(inst1['uuid'])
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        # Delete the instance
        self.test_client.delete_instance(inst1['uuid'])
        self._await_instance_deleted(inst1['uuid'])

        # Ensure that interfaces are now marked as deleted
        for iface in nics:
            self.assertEqual(
                'deleted', self.test_client.get_interface(iface['uuid'])['state'])

    def test_extraneous_network_duplicates(self):
        dupnet = self.test_client.allocate_network(
            '10.0.0.0/24', True, True, '%s-dups' % self.namespace)
        self._await_networks_ready([dupnet['uuid']])

        try:
            inst_hyp1_vm1 = self.test_client.create_instance(
                'dup1', 1, 1024,
                [
                    {
                        'network_uuid': dupnet['uuid']
                    }
                ],
                [
                    {
                        'size': 8,
                        'base': 'sf://upload/system/debian-11',
                        'type': 'disk'
                    }
                ], None, None, force_placement='sf-2')

            inst_hyp1_vm2 = self.test_client.create_instance(
                'dup2', 1, 1024,
                [
                    {
                        'network_uuid': dupnet['uuid']
                    }
                ],
                [
                    {
                        'size': 8,
                        'base': 'sf://upload/system/ubuntu-2004',
                        'type': 'disk'
                    }
                ], None, None, force_placement='sf-2')

            inst_hyp2_vm1 = self.test_client.create_instance(
                'dup3', 1, 1024,
                [
                    {
                        'network_uuid': dupnet['uuid']
                    }
                ],
                [
                    {
                        'size': 8,
                        'base': 'sf://upload/system/ubuntu-2004',
                        'type': 'disk'
                    }
                ], None, None, force_placement='sf-3')

        except apiclient.ResourceNotFoundException as e:
            self.skip('Target node does not exist. %s' % e)
            return

        self.assertIsNotNone(inst_hyp1_vm1['uuid'])
        self._await_instance_ready(inst_hyp1_vm1['uuid'])
        self.assertIsNotNone(inst_hyp1_vm2['uuid'])
        self._await_instance_ready(inst_hyp1_vm2['uuid'])
        self.assertIsNotNone(inst_hyp2_vm1['uuid'])
        self._await_instance_ready(inst_hyp2_vm1['uuid'])

        nics = self.test_client.get_instance_interfaces(inst_hyp1_vm2['uuid'])
        results = self._await_command(inst_hyp1_vm1['uuid'], 'ping -c 3 %s' % nics[0]['ipv4'])
        self.assertEqual(0, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertFalse('DUP' in results['stdout'])

    def test_provided_dns(self):
        inst1 = self.test_client.create_instance(
            'test-provided-dns', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)
        inst2 = self.test_client.create_instance(
            'test-provided-dns-2', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst1['uuid'])

        # Ensure cloud-init didn't report any warnings. This is annoying because
        # cloud-init treats not having user data as a warning even though it
        # isn't a schema error. https://github.com/canonical/cloud-init/issues/5803
        # asserts that v23.4 fixes this so maybe one day I can remove this hack.
        _, data = self.test_client.await_agent_command(
            inst1['uuid'], 'grep WARNING /var/log/cloud-init.log 2>&1 || true')
        if data.find('WARNING') != -1:
            _, schema_warnings = self.test_client.await_agent_command(
                inst1['uuid'], 'cloud-init schema --system 2>&1 || true')
            for line in schema_warnings.split('\n'):
                if line.find('File None needs to begin with "#cloud-config"') != -1:
                    pass
                elif line.find('schema error') != -1:
                    self.fail(
                        f'cloud-init.log contained warnings:\n\n{data}\n\n'
                        f'"cloud-init schema --system" says:\n\n{schema_warnings}')

        # Ensure the gateway is set as the DNS server in /etc/resolv.conf
        data = self.test_client.await_agent_fetch(
            inst1['uuid'], '/etc/resolv.conf')
        if data.find('192.168.242.1') == -1:
            self.fail(
                '/etc/resolv.conf did not have the gateway set as the '
                f'DNS address:\n\n{data}')
        if data.find(f'{self.namespace}.bonkerslab') == -1:
            self.fail(
                '/etc/resolv.conf did not have the namespace set as the '
                f'DNS search domain:\n\n{data}')

        # Lookup our addresses
        nics = self.test_client.get_instance_interfaces(inst1['uuid'])
        self.assertEqual(1, len(nics))
        address1 = nics[0]['ipv4']

        nics = self.test_client.get_instance_interfaces(inst2['uuid'])
        self.assertEqual(1, len(nics))
        address2 = nics[0]['ipv4']

        # Do a DNS lookup for a public address. getent is included in the base
        # distro, whereas host and nslookup are not.
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], 'getent hosts 8.8.8.8')
        self.assertEqual(0, ec)
        self.assertTrue(data.find('dns.google') != -1)

        # Do a DNS lookup for google
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], 'getent ahostsv4 www.google.com || true')
        self.assertEqual(0, ec)
        if data.find('www.google.com') == -1:
            self.fail(
                f'Did not find "www.google.com" in getent output:\n\n{data}')

        # Do a DNS lookup for an internal address.
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], f'getent hosts {address1} || true')
        self.assertEqual(0, ec)
        if data.find('test-provided-dns') == -1:
            self.fail(
                f'Did not find address "test-provided-dns" for instance 1 at '
                f'{address1} via getent ahosts output:\n\n{data}')

        # Do a DNS lookup for our local network
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'],
            f'getent ahostsv4 test-provided-dns.{self.namespace}.bonkerslab || true')
        self.assertEqual(0, ec)
        if data.find(address1) == -1:
            self.fail(
                f'Did not find address "{address1}" for instance 1 at '
                f'test-provided-dns.{self.namespace}.bonkerslab via getent ahostsv4 '
                f'output:\n\n{data}')

        # Do another DNS lookup for our local network for someone other than us
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'],
            f'getent ahostsv4 test-provided-dns-2.{self.namespace}.bonkerslab || true')
        self.assertEqual(0, ec)
        if data.find(address2) == -1:
            self.fail(
                f'Did not find address "{address2}" for instance 1 at '
                f'test-provided-dns-2.{self.namespace}.bonkerslab via getent ahostsv4 '
                f'output:\n\n{data}')

    def test_no_provided_dns(self):
        inst1 = self.test_client.create_instance(
            'test-no-provided-dns', 1, 1024,
            [
                {
                    'network_uuid': self.net_two['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst1['uuid'])

        # Ensure cloud-init didn't report any warnings. This is annoying because
        # cloud-init treats not having user data as a warning even though it
        # isn't a schema error. https://github.com/canonical/cloud-init/issues/5803
        # asserts that v23.4 fixes this so maybe one day I can remove this hack.
        _, data = self.test_client.await_agent_command(
            inst1['uuid'], 'grep WARNING /var/log/cloud-init.log 2>&1 || true')
        if data.find('WARNING') != -1:
            _, schema_warnings = self.test_client.await_agent_command(
                inst1['uuid'], 'cloud-init schema --system 2>&1 || true')
            for line in schema_warnings.split('\n'):
                if line.find('File None needs to begin with "#cloud-config"') != -1:
                    pass
                elif line.find('schema error') != -1:
                    self.fail(
                        f'cloud-init.log contained warnings:\n\n{data}\n\n'
                        f'"cloud-init schema --system" says:\n\n{schema_warnings}')

        # Ensure the gateway is not set as the DNS server in /etc/resolv.conf
        data = self.test_client.await_agent_fetch(
            inst1['uuid'], '/etc/resolv.conf')
        if data.find('192.168.242.1') != -1:
            self.fail(
                '/etc/resolv.conf should not have the gateway set as the '
                f'DNS address:\n\n{data}')
        if data.find(f'{self.namespace}.bonkerslab') != -1:
            self.fail(
                '/etc/resolv.conf should not have the namespace set as the '
                f'DNS search domain:\n\n{data}')

        # Do a DNS lookup for google
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], 'getent ahostsv4 www.google.com || true')
        self.assertEqual(0, ec)
        if data.find('www.google.com') == -1:
            self.fail(
                f'Did not find "www.google.com" in getent output:\n\n{data}')

    # TODO(mikal): we should do this for Rocky 9 too.
    def test_provided_dns_debian_12(self):
        inst1 = self.test_client.create_instance(
            'test-provided-dns', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst1['uuid'])

        # Ensure cloud-init didn't report any warnings. This is annoying because
        # cloud-init treats not having user data as a warning even though it
        # isn't a schema error. https://github.com/canonical/cloud-init/issues/5803
        # asserts that v23.4 fixes this so maybe one day I can remove this hack.
        _, data = self.test_client.await_agent_command(
            inst1['uuid'], 'grep WARNING /var/log/cloud-init.log 2>&1 || true')
        if data.find('WARNING') != -1:
            _, schema_warnings = self.test_client.await_agent_command(
                inst1['uuid'], 'cloud-init schema --system 2>&1 || true')
            for line in schema_warnings.split('\n'):
                if line.find('File None needs to begin with "#cloud-config"') != -1:
                    pass
                elif line.find('schema error') != -1:
                    self.fail(
                        f'cloud-init.log contained warnings:\n\n{data}\n\n'
                        f'"cloud-init schema --system" says:\n\n{schema_warnings}')

        # Ensure the gateway is set as the DNS server in /etc/resolv.conf.
        # Debian 12 uses resolvectl not /etc/resolv.conf
        _, data = self.test_client.await_agent_command(
            inst1['uuid'], 'resolvectl status')
        if data.find('192.168.242.1') == -1:
            self.fail(
                '"resolvectl status" should have the gateway set as the '
                f'DNS address:\n\n{data}')
        if data.find('8.8.8.8') != -1:
            self.fail(
                '"resolvectl status" should not have 8.8.8.8 set as the '
                f'DNS address:\n\n{data}')

        data = self.test_client.await_agent_fetch(
            inst1['uuid'], '/etc/resolv.conf')
        if data.find(f'{self.namespace}.bonkerslab') == -1:
            self.fail(
                '/etc/resolv.conf should have the namespace set as the '
                f'DNS search domain:\n\n{data}')

        # Lookup our addresses
        nics = self.test_client.get_instance_interfaces(inst1['uuid'])
        self.assertEqual(1, len(nics))
        address1 = nics[0]['ipv4']

        # Do a DNS lookup for a public address. getent is included in the base
        # distro, whereas host and nslookup are not.
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], 'getent hosts 8.8.8.8')
        self.assertEqual(0, ec)
        self.assertTrue(data.find('dns.google') != -1)

        # Do a DNS lookup for google
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], 'getent ahostsv4 www.google.com || true')
        self.assertEqual(0, ec)
        if data.find('www.google.com') == -1:
            self.fail(
                f'Did not find "www.google.com" in getent output:\n\n{data}')

        # Do a DNS lookup for an internal address.
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], f'getent hosts {address1} || true')
        self.assertEqual(0, ec)
        if data.find('test-provided-dns') == -1:
            self.fail(
                f'Did not find address "test-provided-dns" for instance 1 at '
                f'{address1} via getent ahosts output:\n\n{data}')

        # Do a DNS lookup for our local network
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'],
            f'getent ahostsv4 test-provided-dns.{self.namespace}.bonkerslab || true')
        self.assertEqual(0, ec)
        if data.find(address1) == -1:
            self.fail(
                f'Did not find address "{address1}" for instance 1 at '
                f'test-provided-dns.{self.namespace}.bonkerslab via getent ahostsv4 '
                f'output:\n\n{data}')

    def test_no_provided_dns_debian12(self):
        inst1 = self.test_client.create_instance(
            'test-no-provided-dns', 1, 1024,
            [
                {
                    'network_uuid': self.net_two['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Wait for the instance agent to report in
        self._await_instance_ready(inst1['uuid'])

        # Ensure cloud-init didn't report any warnings. This is annoying because
        # cloud-init treats not having user data as a warning even though it
        # isn't a schema error. https://github.com/canonical/cloud-init/issues/5803
        # asserts that v23.4 fixes this so maybe one day I can remove this hack.
        _, data = self.test_client.await_agent_command(
            inst1['uuid'], 'grep WARNING /var/log/cloud-init.log 2>&1 || true')
        if data.find('WARNING') != -1:
            _, schema_warnings = self.test_client.await_agent_command(
                inst1['uuid'], 'cloud-init schema --system 2>&1 || true')
            for line in schema_warnings.split('\n'):
                if line.find('File None needs to begin with "#cloud-config"') != -1:
                    pass
                elif line.find('schema error') != -1:
                    self.fail(
                        f'cloud-init.log contained warnings:\n\n{data}\n\n'
                        f'"cloud-init schema --system" says:\n\n{schema_warnings}')

        # Ensure the gateway is not set as the DNS server in /etc/resolv.conf.
        # Debian 12 uses resolvectl not /etc/resolv.conf
        _, data = self.test_client.await_agent_command(
            inst1['uuid'], 'resolvectl status')
        if data.find('192.168.242.1') != -1:
            self.fail(
                '"resolvectl status" should not have the gateway set as the '
                f'DNS address:\n\n{data}')
        if data.find('8.8.8.8') == -1:
            self.fail(
                '"resolvectl status" should have 8.8.8.8 set as the '
                f'DNS address:\n\n{data}')

        data = self.test_client.await_agent_fetch(
            inst1['uuid'], '/etc/resolv.conf')
        if data.find(f'{self.namespace}.bonkerslab') != -1:
            self.fail(
                '/etc/resolv.conf should not have the namespace set as the '
                f'DNS search domain:\n\n{data}')

        # Do a DNS lookup for google
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], 'getent ahostsv4 www.google.com || true')
        self.assertEqual(0, ec)
        if data.find('www.google.com') == -1:
            self.fail(
                f'Did not find "www.google.com" in getent output:\n\n{data}')
