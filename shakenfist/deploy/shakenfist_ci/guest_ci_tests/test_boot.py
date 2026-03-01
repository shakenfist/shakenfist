import json

from testtools import content

from shakenfist_ci import base

import testscenarios


class TestBoot(testscenarios.WithScenarios, base.BaseNamespacedTestCase):
    """Make sure instances boot under various configurations."""

    scenarios = [
        (
            'debian-12',
            {
                'base': 'debian-12'
            }
        ),
        (
            'debian-12',
            {
                'base': 'debian-12'
            }
        ),
    ]

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'boot'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def _boot_no_network(self):
        """Check that instances without a network still boot.

        Once we had a bug that only stopped instance creation when no network
        was specified.
        """
        inst = self.test_client.create_instance(
            f'test-boot-no-network-{self.base}', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': f'sf://upload/system/{self.base}',
                    'type': 'disk'
                }
            ], None, None)

        self._await_instance_ready(inst['uuid'])

    def _boot_network(self):
        inst = self.test_client.create_instance(
            f'test-boot-network-{self.base}', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': f'sf://upload/system/{self.base}',
                    'type': 'disk'
                }
            ], None, None)

        self._await_instance_ready(inst['uuid'])

    def _boot_large_disk(self):
        inst = self.test_client.create_instance(
            f'test-boot-large-disk-{self.base}', 1, 1024, None,
            [
                {
                    'size': 30,
                    'base': f'sf://upload/system/{self.base}',
                    'type': 'disk'
                }
            ], None, None)

        self._await_instance_ready(inst['uuid'])
