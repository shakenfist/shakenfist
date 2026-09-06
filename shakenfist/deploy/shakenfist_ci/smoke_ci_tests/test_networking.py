import json

from testtools import content

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
        self.addDetail(
            'net_one',
            content.text_content(json.dumps(self.net_one, indent=4,
                                            sort_keys=True)))
        self.net_two = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net-two' % self.namespace)
        self.addDetail(
            'net_two',
            content.text_content(json.dumps(self.net_two, indent=4,
                                            sort_keys=True)))
        self.net_three = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-three' % self.namespace)
        self.addDetail(
            'net_three',
            content.text_content(json.dumps(self.net_three, indent=4,
                                            sort_keys=True)))
        self.net_four = self.test_client.allocate_network(
            '192.168.10.0/24', True, True, '%s-net-four' % self.namespace)
        self.addDetail(
            'net_four',
            content.text_content(json.dumps(self.net_four, indent=4,
                                            sort_keys=True)))
        self._await_networks_ready([self.net_one['uuid'],
                                    self.net_two['uuid'],
                                    self.net_three['uuid'],
                                    self.net_four['uuid']])

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
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        self._await_instance_ready(inst['uuid'])

        nics = self.test_client.get_instance_interfaces(inst['uuid'])
        self.addDetail(
            'nics',
            content.text_content(json.dumps(nics, indent=4, sort_keys=True)))
        self.assertEqual(1, len(nics))
        for iface in nics:
            self.assertEqual('created', iface['state'],
                             'Interface %s is not in correct state' % iface['uuid'])

        ips = []
        for nic in nics:
            ips.append(nic['ipv4'])

        self.assertEqual(['192.168.10.56'], ips)

    def test_specific_ip_reuse_after_delete(self):
        # Regression coverage for issue 4059: deleting an instance leaves
        # its addresses in deletion-halo reservations for
        # IP_DELETION_HALO_DURATION. An immediate recreate at the same
        # explicit address must take over that halo rather than returning
        # a 409, otherwise replacing an instance pinned to a static
        # address always fails.
        netdesc = [
            {
                'network_uuid': self.net_four['uuid'],
                'address': '192.168.10.57'
            }
        ]
        diskdesc = [
            {
                'size': 8,
                'base': 'sf://upload/system/debian-12',
                'type': 'disk'
            }
        ]

        inst = self.test_client.create_instance(
            'test-specific-ip-reuse', 1, 1024, netdesc, diskdesc, None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self._await_instance_ready(inst['uuid'])

        self.test_client.delete_instance(inst['uuid'])
        self._await_instance_deleted(inst['uuid'])

        inst = self.test_client.create_instance(
            'test-specific-ip-reuse', 1, 1024, netdesc, diskdesc, None, None)
        self.addDetail(
            'inst_recreated',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self._await_instance_ready(inst['uuid'])

        nics = self.test_client.get_instance_interfaces(inst['uuid'])
        self.addDetail(
            'nics',
            content.text_content(json.dumps(nics, indent=4, sort_keys=True)))
        self.assertEqual(['192.168.10.57'], [nic['ipv4'] for nic in nics])

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
                    'base': 'sf://upload/system/debian-12',
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
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent2'])

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
                    'base': 'sf://upload/system/debian-12',
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
                'deleted', self.test_client.get_interface(iface['uuid'])['state'],
                f'Interface {iface["uuid"]} did not delete')

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
                        'base': 'sf://upload/system/debian-12',
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
            self.skipTest('Target node does not exist. %s' % e)
            return

        self.assertIsNotNone(inst_hyp1_vm1['uuid'])
        self._await_instance_ready(inst_hyp1_vm1['uuid'])
        self.assertIsNotNone(inst_hyp1_vm2['uuid'])
        self._await_instance_ready(inst_hyp1_vm2['uuid'])
        self.assertIsNotNone(inst_hyp2_vm1['uuid'])
        self._await_instance_ready(inst_hyp2_vm1['uuid'])

        nics = self.test_client.get_instance_interfaces(inst_hyp1_vm2['uuid'])
        results = self._await_command(
            inst_hyp1_vm1['uuid'], 'ping -c 3 %s' % nics[0]['ipv4'])
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
                    'base': 'sf://upload/system/debian-12',
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

        # Ensure the gateway is set as the DNS server. Debian 12 uses
        # systemd-resolved, so /etc/resolv.conf may point to the local
        # stub resolver (127.0.0.53). In that case, check the real
        # upstream config at /run/systemd/resolve/resolv.conf.
        data = self.test_client.await_agent_fetch(
            inst1['uuid'], '/etc/resolv.conf')
        if data.find('192.168.242.1') == -1:
            if data.find('127.0.0.53') != -1:
                data = self.test_client.await_agent_fetch(
                    inst1['uuid'],
                    '/run/systemd/resolve/resolv.conf')
            if data.find('192.168.242.1') == -1:
                self.fail(
                    '/etc/resolv.conf (or systemd-resolved '
                    'upstream) did not have the gateway set '
                    f'as the DNS address:\n\n{data}')
        if data.find(f'{self.namespace}.bonkerslab') == -1:
            self.fail(
                'resolv.conf did not have the namespace set '
                f'as the DNS search domain:\n\n{data}')

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

    def test_provided_extra_dns(self):
        extra_dns_net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-extra-dns' % self.namespace,
            provide_dns=True)
        inst1 = self.test_client.create_instance(
            'test-provided-dns', 1, 1024,
            [
                {
                    'network_uuid': extra_dns_net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)

        # Create two extra DNS entries, delete one. Each call writes the
        # network attributes synchronously but enqueues the dnsmasq
        # reload as a cluster op; we must wait for that to drain before
        # querying DNS or we race the SIGHUP.
        self.test_client.update_network_dns_entry(
            extra_dns_net['uuid'], 'banana', '11.22.33.44')
        self.test_client.update_network_dns_entry(
            extra_dns_net['uuid'], 'mango', '55.66.77.88')
        self.test_client.delete_network_dns_entry(
            extra_dns_net['uuid'], 'banana')
        self._await_network_operations_complete(extra_dns_net['uuid'])

        # Wait for the instance agent to report in
        self._await_instance_ready(inst1['uuid'])

        # Do a DNS lookup for banana
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], 'getent ahostsv4 banana || true')
        self.assertEqual(0, ec)
        if data.find('11.22.33.44') != -1:
            self.fail(
                f'Found "banana" in getent output:\n\n{data}')

        # Do a DNS lookup for mango
        ec, data = self.test_client.await_agent_command(
            inst1['uuid'], 'getent ahostsv4 mango || true')
        self.assertEqual(0, ec)
        if data.find('55.66.77.88') == -1:
            self.fail(
                f'Did not find "mango" in getent output:\n\n{data}')
