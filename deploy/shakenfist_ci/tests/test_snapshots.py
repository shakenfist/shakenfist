import time

from shakenfist_ci import base


class TestSnapshots(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'snapshots'
        super(TestSnapshots, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestSnapshots, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_single_disk_snapshots(self):
        inst1 = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst1['node'])

        self._await_login_prompt(inst1['uuid'])

        # Take a snapshot
        snap1 = self.test_client.snapshot_instance(inst1['uuid'])
        self.assertIsNotNone(snap1)
        self.assertEqual(1, snap1['vda']['artifact_index'])

        # Wait until the blob uuid specified above is the one used for the
        # current snapshot
        start_time = time.time()
        while time.time() - start_time < 300:
            snapshots = self.test_client.get_instance_snapshots(inst1['uuid'])
            if snapshots and snapshots[-1].get('blob_uuid') == snap1['vda']['blob_uuid']:
                break
            time.sleep(5)

        self.assertEqual(1, len(snapshots))
        self.assertEqual('created', snapshots[0]['state'])

        # Check blob exists and has correct reference count
        snapshot_uuid = snapshots[-1]['uuid']
        snap1_info = self.test_client.get_artifact(snapshot_uuid)
        self.assertEqual(1, len(snap1_info.get('blobs', [])))
        self.assertEqual(1, snap1_info['blobs']['1']['reference_count'])

        # Take another snapshot, we only get the new snapshot returned
        snap2 = self.test_client.snapshot_instance(inst1['uuid'])
        self.assertEqual(2, snap2['vda']['artifact_index'])

        # Wait until the blob uuid specified above is the one used for the
        # current snapshot
        start_time = time.time()
        while time.time() - start_time < 300:
            snapshots = self.test_client.get_instance_snapshots(inst1['uuid'])
            if snapshots and snapshots[-1].get('blob_uuid') == snap2['vda']['blob_uuid']:
                break
            time.sleep(5)

        self.assertEqual(2, len(snapshots))
        self.assertEqual('sf://instance/%s/vda'
                         % inst1['uuid'], snapshots[0]['source_url'])

        # Now attempt to boot the snapshot via blob uuid
        inst2 = self.test_client.create_instance(
            'cirros-from-blob', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://blob/%s' % snap2['vda']['blob_uuid'],
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst2['uuid'])
        self.assertIsNotNone(inst2['node'])
        self._await_login_prompt(inst2['uuid'])

        # Now attempt to boot the snapshot via snapshot uuid
        inst3 = self.test_client.create_instance(
            'cirros-from-snapshot', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://snapshot/%s' % snap2['vda']['artifact_uuid'],
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst3['uuid'])
        self.assertIsNotNone(inst3['node'])
        self._await_login_prompt(inst3['uuid'])

        # Test deleting snapshot versions
        versions = self.test_client.get_artifact_versions(snapshot_uuid)
        self.assertEqual(2, len(versions))

        self.test_client.delete_artifact_version(snapshot_uuid, 2)
        versions = self.test_client.get_artifact_versions(snapshot_uuid)
        self.assertEqual(1, len(versions))

        # Take snapshot and check the index increases (does not reuse index 2)
        snap3 = self.test_client.snapshot_instance(inst1['uuid'])
        self.assertEqual(3, snap3['vda']['artifact_index'])

        # Quickly delete snapshot whilst still in progress
        self.test_client.delete_artifact_version(snapshot_uuid, 3)

        # Delete only version in artifact
        self.test_client.delete_artifact_version(snapshot_uuid, 1)
        versions = self.test_client.get_artifact_versions(snapshot_uuid)
        self.assertEqual(0, len(versions))

        snap_info = self.test_client.get_artifact(snapshot_uuid)
        self.assertEqual('deleted', snap_info['state'])

        self.test_client.delete_instance(inst1['uuid'])
        self.test_client.delete_instance(inst2['uuid'])
        self.test_client.delete_instance(inst3['uuid'])

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
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                },
                {
                    'size': 8,
                    'type': 'disk'
                },
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'cdrom'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self.assertIsNotNone(inst['node'])
        self._await_login_prompt(inst['uuid'])

        snap1 = self.test_client.snapshot_instance(inst['uuid'], all=True)
        self.assertIsNotNone(snap1)

        # Wait until the blob uuid specified above is the one used for the
        # current snapshot
        start_time = time.time()
        while time.time() - start_time < 300:
            snapshots = self.test_client.get_instance_snapshots(inst['uuid'])
            if snapshots and snapshots[-1].get('blob_uuid') == snap1['vdc']['blob_uuid']:
                break
            time.sleep(5)

        self.assertEqual(2, len(snapshots))

        snap2 = self.test_client.snapshot_instance(inst['uuid'], all=True)
        self.assertIsNotNone(snap2)

        # Wait until the blob uuid specified above is the one used for the
        # current snapshot
        start_time = time.time()
        while time.time() - start_time < 300:
            snapshots = self.test_client.get_instance_snapshots(inst['uuid'])
            if snapshots and snapshots[-1].get('blob_uuid') == snap2['vdc']['blob_uuid']:
                break
            time.sleep(5)

        self.assertEqual(4, len(snapshots))

        for snap in snapshots:
            self.assertIn(snap['source_url'].split('/')[-1], ['vda', 'vdc'])
            self.assertTrue(snap['source_url'].startswith(
                'sf://instance/%s' % inst['uuid']))

        self.test_client.delete_instance(inst['uuid'])

    def test_labels(self):
        inst1 = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst1['node'])

        self._await_login_prompt(inst1['uuid'])

        # Take a snapshot
        snap = self.test_client.snapshot_instance(
            inst1['uuid'], label_name='testlabel')
        self.assertIsNotNone(snap)

        # Now attempt to boot the snapshot via snapshot uuid
        inst2 = self.test_client.create_instance(
            'cirros-from-snapshot', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'label:testlabel',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst2['uuid'])
        self.assertIsNotNone(inst2['node'])
        self._await_login_prompt(inst2['uuid'])

        self.test_client.delete_instance(inst1['uuid'])
        self.test_client.delete_instance(inst2['uuid'])

    def test_labels_with_deleted_snapshot(self):
        inst1 = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst1['uuid'])
        self.assertIsNotNone(inst1['node'])

        self._await_login_prompt(inst1['uuid'])

        # Take a snapshot
        snap = self.test_client.snapshot_instance(
            inst1['uuid'], label_name='anotherlabel', delete_snapshot_after_label=True)
        self.assertIsNotNone(snap)

        # Wait for the label, we cannot use _await_artifacts_ready here because
        # we don't know the UUID for the label artifact
        start_time = time.time()
        artifact_uuid = None
        while not artifact_uuid:
            for a in self.test_client.get_artifacts():
                if a['source_url'] == 'sf://label/%s/anotherlabel' % self.namespace:
                    artifact_uuid = a['uuid']
                    break

            if not artifact_uuid:
                time.sleep(20)
                if time.time() - start_time > 300:
                    raise base.TimeoutException('Label never appeared')

        self._await_artifacts_ready([artifact_uuid])

        # Ensure the snapshot is gone
        for a in self.test_client.get_artifacts():
            if a['uuid'] == snap['vda']['artifact_uuid']:
                self.fail('Snapshot remains')

    def test_specific_device_snapshots(self):
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
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                },
                {
                    'size': 8,
                    'type': 'disk'
                },
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self.assertIsNotNone(inst['node'])
        self._await_login_prompt(inst['uuid'])

        snap1 = self.test_client.snapshot_instance(inst['uuid'], device='vdc')
        self.assertIsNotNone(snap1)

        # Wait until the blob uuid specified above is the one used for the
        # current snapshot
        start_time = time.time()
        while time.time() - start_time < 300:
            snapshots = self.test_client.get_instance_snapshots(inst['uuid'])
            if snapshots and snapshots[-1].get('blob_uuid') == snap1['vdc']['blob_uuid']:
                break
            time.sleep(5)

        self.assertEqual(1, len(snapshots))

        snap2 = self.test_client.snapshot_instance(
            inst['uuid'], device='vdc', label_name='foo')
        self.assertIsNotNone(snap2)

        # Wait until the blob uuid specified above is the one used for the
        # current snapshot
        start_time = time.time()
        while time.time() - start_time < 300:
            snapshots = self.test_client.get_instance_snapshots(inst['uuid'])
            if snapshots and snapshots[-1].get('blob_uuid') == snap2['vdc']['blob_uuid']:
                break
            time.sleep(5)

        self.assertEqual(2, len(snapshots))

        for snap in snapshots:
            self.assertEqual('vdc', snap['source_url'].split('/')[-1])
            self.assertTrue(snap['source_url'].startswith(
                'sf://instance/%s' % inst['uuid']))

        self.test_client.delete_instance(inst['uuid'])
