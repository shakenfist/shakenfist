import sys

from shakenfist_ci import base


class TestUpgrades(base.BaseTestCase):
    def test_upgraded_data_exists(self):
        # There is an upgraded namespace called 'upgrade'
        if 'upgrade' not in self.system_client.get_namespaces():
            self.skip('There is no upgrade namespace')

        # Collect networks and check
        networks_by_name = {}
        networks_by_uuid = {}
        for net in self.system_client.get_networks():
            networks_by_name['%s/%s' % (net['namespace'], net['name'])] = net
            networks_by_uuid[net['uuid']] = net

        self.assertIn('upgrade/upgrade-fe', networks_by_name)
        self.assertIn('upgrade/upgrade-be', networks_by_name)

        sys.stderr.write(
            'Discovered networks post upgrade: %s\n' % networks_by_name)

        # Collect instances and check
        instances = {}
        for inst in self.system_client.get_instances():
            instances['%s/%s' % (inst['namespace'], inst['name'])] = inst

        sys.stderr.write(
            'Discovered instances post upgrade: %s\n' % instances)

        # Determine interface information
        addresses = {}
        for name in ['upgrade/fe', 'upgrade/be-1', 'upgrade/be-2']:
            sys.stderr.write('Looking up interfaces for %s\n' % name)
            self.assertIn(name, instances)
            for iface in self.system_client.get_instance_interfaces(instances[name]['uuid']):
                sys.stderr.write('%s has interface %s\n' % (name, iface))
                net_name = networks_by_uuid.get(
                    iface['network_uuid'], {'name': 'unknown'})['name']
                addresses['%s/%s' % (name, net_name)] = iface['ipv4']

        sys.stderr.write(
            'Discovered addresses post upgrade: %s\n' % addresses)

        # Ensure we can ping all instances
        self._test_ping(
            instances['upgrade/fe']['uuid'],
            networks_by_name['upgrade/upgrade-fe']['uuid'],
            addresses['upgrade/fe/upgrade-fe'],
            True, 10)
        self._test_ping(
            instances['upgrade/fe']['uuid'],
            networks_by_name['upgrade/upgrade-be']['uuid'],
            addresses['upgrade/fe/upgrade-be'],
            True, 10)

        self._test_ping(
            instances['upgrade/be-1']['uuid'],
            networks_by_name['upgrade/upgrade-be']['uuid'],
            addresses['upgrade/be-1/upgrade-be'],
            True, 10)
        self._test_ping(
            instances['upgrade/be-2']['uuid'],
            networks_by_name['upgrade/upgrade-be']['uuid'],
            addresses['upgrade/be-2/upgrade-be'],
            True, 10)
