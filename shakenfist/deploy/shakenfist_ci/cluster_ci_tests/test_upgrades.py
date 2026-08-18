import json
import sys

from testtools import content

from shakenfist_ci import base
from shakenfist_ci.base import namespace_names


class TestUpgrades(base.BaseTestCase):
    def test_upgraded_data_exists(self):
        # There is an upgraded namespace called 'upgrade'. This test still
        # skips on every cluster we currently build, because nothing
        # creates that namespace -- but until this comparison was fixed it
        # would have skipped even on a cluster which had one, so the guard
        # could not detect the condition it names.
        if 'upgrade' not in namespace_names(
                self.system_client.get_namespaces()):
            self.skipTest('There is no upgrade namespace')

        # Collect networks and check
        networks_by_name = {}
        networks_by_uuid = {}
        for net in self.system_client.get_networks():
            networks_by_name[f'{net["namespace"]}/{net["name"]}'] = net
            networks_by_uuid[net['uuid']] = net

        self.addDetail(
            'networks_by_name',
            content.text_content(json.dumps(
                {k: v for k, v in networks_by_name.items()},
                indent=4, sort_keys=True, default=str)))
        self.assertIn('upgrade/upgrade-fe', networks_by_name)
        self.assertIn('upgrade/upgrade-be', networks_by_name)

        sys.stderr.write(
            'Discovered networks post upgrade: %s\n' % networks_by_name)

        # Collect instances and check
        instances = {}
        for inst in self.system_client.get_instances():
            instances[f'{inst["namespace"]}/{inst["name"]}'] = inst
        self.addDetail(
            'instances',
            content.text_content(json.dumps(
                {k: v for k, v in instances.items()},
                indent=4, sort_keys=True, default=str)))

        sys.stderr.write(
            'Discovered instances post upgrade: %s\n' % instances)

        # Determine interface information
        addresses = {}
        for name in ['upgrade/fe', 'upgrade/be-1', 'upgrade/be-2']:
            sys.stderr.write('Looking up interfaces for %s\n' % name)
            self.assertIn(name, instances)
            for iface in self.system_client.get_instance_interfaces(instances[name]['uuid']):
                sys.stderr.write(f'{name} has interface {iface}\n')
                net_name = networks_by_uuid.get(
                    iface['network_uuid'], {'name': 'unknown'})['name']
                addresses[f'{name}/{net_name}'] = iface['ipv4']

        self.addDetail(
            'addresses',
            content.text_content(json.dumps(addresses, indent=4,
                                            sort_keys=True)))
        sys.stderr.write(
            'Discovered addresses post upgrade: %s\n' % addresses)

        # Ensure we can ping all instances
        self._test_ping(
            instances['upgrade/fe']['uuid'],
            networks_by_name['upgrade/upgrade-fe']['uuid'],
            addresses['upgrade/fe/upgrade-fe'],
            True)
        self._test_ping(
            instances['upgrade/fe']['uuid'],
            networks_by_name['upgrade/upgrade-be']['uuid'],
            addresses['upgrade/fe/upgrade-be'],
            True)

        self._test_ping(
            instances['upgrade/be-1']['uuid'],
            networks_by_name['upgrade/upgrade-be']['uuid'],
            addresses['upgrade/be-1/upgrade-be'],
            True)
        self._test_ping(
            instances['upgrade/be-2']['uuid'],
            networks_by_name['upgrade/upgrade-be']['uuid'],
            addresses['upgrade/be-2/upgrade-be'],
            True)
