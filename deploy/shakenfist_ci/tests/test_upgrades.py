from shakenfist_ci import base


class TestUpgrades(base.BaseTestCase):
    def test_upgraded_data_exists(self):
        # There is an upgraded namespace called 'nstest'
        self.assertIn('nstest', self.system_client.get_namespaces())

        # Collect networks
        networks = {}
        for net in self.system_client.get_networks():
            networks[net['uuid']] = net

        self.assertIn('30acadd4-66ee-4390-9477-e31d11fe44cb', networks)
        self.assertIn('afce2cce-dec2-4cf7-a61b-2c4614b320c7', networks)

        # This network is in the dump, but removed at upgrade for having
        # and invalid netblock.
        self.assertNotIn('3edb03de-4f3d-4859-b822-94924073a1ea', networks)

        # First network
        self.assertEqual(
            'testnet', networks['30acadd4-66ee-4390-9477-e31d11fe44cb']['name'])
        self.assertEqual(
            'system', networks['30acadd4-66ee-4390-9477-e31d11fe44cb']['namespace'])
        self.assertEqual(
            '192.16.10.0/24', networks['30acadd4-66ee-4390-9477-e31d11fe44cb']['netblock'])

        # Second network
        self.assertEqual(
            'nsnet', networks['afce2cce-dec2-4cf7-a61b-2c4614b320c7']['name'])
        self.assertEqual(
            'nstest', networks['afce2cce-dec2-4cf7-a61b-2c4614b320c7']['namespace'])
        self.assertEqual(
            '192.168.20.0/24', networks['afce2cce-dec2-4cf7-a61b-2c4614b320c7']['netblock'])

        # Collect instances
        instances = {}
        for inst in self.system_client.get_instances():
            instances[inst['uuid']] = inst

        self.assertIn('99ef006a-a0f0-4d2f-a784-16bce921431c', instances)
        self.assertIn('a13ce821-adcb-45a4-9260-1b18b5250ad3', instances)
        self.assertIn('a9121ca7-4f72-4b94-87f4-2a1e0d54f4f4', instances)
        self.assertIn('ce2c93af-a9f8-4efe-94df-3786c3f92a7a', instances)

        # First instance
        self.assertEqual(
            {
                'console_port': 31614,
                'cpus': 1,
                'disk_spec': [
                    {
                        'base': 'cirros',
                        'bus': None,
                        'size': 8,
                        'type': 'disk'
                    }
                ],
                'error_message': None,
                'memory': 1024,
                'name': 'inst1',
                'namespace': 'system',
                'node': 'sf-2',
                'power_state': 'on',
                'ssh_key': None,
                'state': 'created',
                'user_data': None,
                'uuid': 'ce2c93af-a9f8-4efe-94df-3786c3f92a7a',
                'vdi_port': 40495,
                'version': 2,
                'video': {
                    'memory': 16384,
                    'model': 'cirrus'
                }
            }, self.system_client.get_instance('ce2c93af-a9f8-4efe-94df-3786c3f92a7a')
        )

        # Second instance
        self.assertEqual(
            {
                'console_port': 47305,
                'cpus': 1,
                'disk_spec': [
                    {
                        'base': 'cirros',
                        'bus': None,
                        'size': 8,
                        'type': 'disk'
                    }
                ],
                'error_message': None,
                'memory': 1024,
                'name': 'inst2',
                'namespace': 'system',
                'node': 'sf-2',
                'power_state': 'on',
                'ssh_key': None,
                'state': 'created',
                'user_data': None,
                'uuid': 'a9121ca7-4f72-4b94-87f4-2a1e0d54f4f4',
                'vdi_port': 46898,
                'version': 2,
                'video': {
                    'memory': 16384,
                    'model': 'cirrus'
                }
            }, self.system_client.get_instance('a9121ca7-4f72-4b94-87f4-2a1e0d54f4f4')
        )

        # Third instance
        self.assertEqual(
            {
                'console_port': None,
                'cpus': 2,
                'disk_spec': [
                    {
                        'base': 'ubuntu',
                        'bus': None,
                        'size': 20,
                        'type': 'disk'
                    }
                ],
                'error_message': None,
                'memory': 2048,
                'name': 'ubuntu',
                'namespace': 'nstest',
                'node': 'sf-2',
                'power_state': 'initial',
                'ssh_key': None,
                'state': 'preflight',
                'user_data': None,
                'uuid': 'a13ce821-adcb-45a4-9260-1b18b5250ad3',
                'vdi_port': None,
                'version': 2,
                'video': {
                    'memory': 16384,
                    'model': 'cirrus'
                }
            }, self.system_client.get_instance('a13ce821-adcb-45a4-9260-1b18b5250ad3')
        )

        # Fourth instance
        self.assertEqual(
            {
                'console_port': 30364,
                'cpus': 1,
                'disk_spec': [
                    {
                        'base': 'cirros',
                        'bus': None,
                        'size': 8,
                        'type': 'disk'
                    }
                ],
                'error_message': None,
                'memory': 1024,
                'name': 'inst4',
                'namespace': 'banana',
                'node': 'sf-3',
                'power_state': 'on',
                'ssh_key': None,
                'state': 'created',
                'user_data': None,
                'uuid': '99ef006a-a0f0-4d2f-a784-16bce921431c',
                'vdi_port': 48695,
                'version': 2,
                'video': {
                    'memory': 16384,
                    'model': 'cirrus'
                }
            }, self.system_client.get_instance('99ef006a-a0f0-4d2f-a784-16bce921431c')
        )
