from shakenfist_ci import base


class TestSnapshots(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'snapshots'
        super(TestSnapshots, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestSnapshots, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)

    def test_single_disk_snapshots(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self.assertIsNotNone(inst['node'])

        self._await_login_prompt(inst['uuid'])

        snap1 = self.test_client.snapshot_instance(inst['uuid'])
        self.assertIsNotNone(snap1)
        snapshots = self.test_client.get_instance_snapshots(inst['uuid'])
        self.assertEqual(1, len(snapshots))

        snap2 = self.test_client.snapshot_instance(inst['uuid'], all=True)
        self.assertIsNotNone(snap2)
        snapshots = self.test_client.get_instance_snapshots(inst['uuid'])
        self.assertEqual(2, len(snapshots))

        for snap in snapshots:
            self.assertEqual('vda', snap['device'])
            self.assertEqual(inst['uuid'], snap['instance_uuid'])

        self.test_client.delete_instance(inst['uuid'])

    def test_multiple_disk_snapshots(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                },
                {
                    'size': 8,
                    'type': 'disk'
                },
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'cdrom'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self.assertIsNotNone(inst['node'])
        self._await_login_prompt(inst['uuid'])

        snap1 = self.test_client.snapshot_instance(inst['uuid'])
        self.assertIsNotNone(snap1)
        snapshots = self.test_client.get_instance_snapshots(inst['uuid'])
        self.assertEqual(1, len(snapshots))

        snap2 = self.test_client.snapshot_instance(inst['uuid'], all=True)
        self.assertIsNotNone(snap2)
        snapshots = self.test_client.get_instance_snapshots(inst['uuid'])
        self.assertEqual(3, len(snapshots))

        for snap in snapshots:
            self.assertIn(snap['device'], ['vda', 'vdc'])
            self.assertEqual(inst['uuid'], snap['instance_uuid'])

        self.test_client.delete_instance(inst['uuid'])
