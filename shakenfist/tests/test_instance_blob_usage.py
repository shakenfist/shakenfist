# Copyright 2026 Michael Still and contributors
#
# Tests for the single-pass blob usage map (issue 3502). The cluster wide
# cleanup loop must compute which instances use which blobs once per pass,
# not once per blob -- the per-blob form repeated the full instance walk
# (a block_devices attribute read per instance, plus reference reads for
# every disk's dependency chain) for every single blob.

from unittest import mock

from shakenfist import instance
from shakenfist.tests import base


INSTANCE_UUID_1 = '11111111-1111-4111-8111-111111111111'
INSTANCE_UUID_2 = '22222222-2222-4222-8222-222222222222'
BLOB_CHILD = 'aaaa1111-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
BLOB_BASE = 'bbbb2222-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
BLOB_OTHER = 'cccc3333-cccc-4ccc-8ccc-cccccccccccc'
INSTANCE_UUID_3 = '33333333-3333-4333-8333-333333333333'


class FakeInstance:
    def __init__(self, uuid, block_devices):
        self.uuid = uuid
        self.block_devices = block_devices


class FakeBlob:
    def __init__(self, uuid, depends_on=None):
        self.uuid = uuid
        self.depends_on = depends_on


class InstanceBlobUsageTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        # BLOB_CHILD depends on BLOB_BASE; BLOB_OTHER stands alone.
        blobs = {
            BLOB_CHILD: FakeBlob(BLOB_CHILD, depends_on=BLOB_BASE),
            BLOB_BASE: FakeBlob(BLOB_BASE),
            BLOB_OTHER: FakeBlob(BLOB_OTHER),
        }

        def from_db(blob_uuid, suppress_failure_audit=False):
            return blobs.get(blob_uuid)

        self.blob_from_db_patcher = mock.patch(
            'shakenfist.blob.Blob.from_db', side_effect=from_db)
        self.mock_blob_from_db = self.blob_from_db_patcher.start()
        self.addCleanup(self.blob_from_db_patcher.stop)

    @mock.patch('shakenfist.instance.Instances')
    def test_single_pass_map_includes_dependency_chain(self, mock_instances):
        mock_instances.return_value = [
            FakeInstance(INSTANCE_UUID_1, {'devices': [
                {'blob_uuid': BLOB_CHILD},
                {'device': 'vdb'},
            ]}),
            FakeInstance(INSTANCE_UUID_2, {'devices': [
                {'blob_uuid': BLOB_OTHER},
                {'blob_uuid': BLOB_OTHER},
            ]}),
        ]

        usage = instance.instance_blob_usage()

        self.assertEqual({
            BLOB_CHILD: [INSTANCE_UUID_1],
            BLOB_BASE: [INSTANCE_UUID_1],
            BLOB_OTHER: [INSTANCE_UUID_2],
        }, usage)

        # The instance walk happens exactly once, with the healthy
        # prefilter.
        mock_instances.assert_called_once()
        self.assertEqual(
            'healthy', mock_instances.call_args.kwargs.get('prefilter'))

    @mock.patch('shakenfist.instance.Instances')
    def test_vanished_blob_mid_chain_does_not_crash(self, mock_instances):
        mock_instances.return_value = [
            FakeInstance(INSTANCE_UUID_1, {'devices': [
                {'blob_uuid': 'dddd4444-dddd-4ddd-8ddd-dddddddddddd'},
            ]}),
        ]

        usage = instance.instance_blob_usage()

        # The disk's blob is recorded as in use even though it cannot be
        # hydrated (it may be mid-replication or freshly deleted).
        self.assertEqual(
            {'dddd4444-dddd-4ddd-8ddd-dddddddddddd': [INSTANCE_UUID_1]},
            usage)

    @mock.patch('shakenfist.instance.Instances')
    def test_per_blob_wrapper_matches_map(self, mock_instances):
        mock_instances.return_value = [
            FakeInstance(INSTANCE_UUID_1, {'devices': [
                {'blob_uuid': BLOB_CHILD},
            ]}),
        ]

        self.assertEqual(
            [INSTANCE_UUID_1],
            instance.instance_usage_for_blob_uuid(BLOB_BASE))
        self.assertEqual(
            [], instance.instance_usage_for_blob_uuid(
                'eeee5555-eeee-4eee-8eee-eeeeeeeeeeee'))

    @mock.patch('shakenfist.instance.Instances')
    def test_dependency_chain_is_read_once_per_blob(self, mock_instances):
        # Instances overwhelmingly share their base images, so the same
        # dependency chain used to be re-read from object_references once
        # per instance using it (issue 3876). Three instances on the same
        # disk blob must hydrate that chain once, not three times.
        mock_instances.return_value = [
            FakeInstance(uuid, {'devices': [{'blob_uuid': BLOB_CHILD}]})
            for uuid in (INSTANCE_UUID_1, INSTANCE_UUID_2, INSTANCE_UUID_3)
        ]

        usage = instance.instance_blob_usage()

        self.assertEqual({
            BLOB_CHILD: [INSTANCE_UUID_1, INSTANCE_UUID_2, INSTANCE_UUID_3],
            BLOB_BASE: [INSTANCE_UUID_1, INSTANCE_UUID_2, INSTANCE_UUID_3],
        }, usage)

        # BLOB_CHILD is hydrated to read its depends_on, and BLOB_BASE is
        # hydrated to discover the chain ends there. Two hydrations for
        # three instances; before the memo it was two per instance.
        hydrated = [c.args[0] for c in self.mock_blob_from_db.call_args_list]
        self.assertEqual([BLOB_CHILD, BLOB_BASE], hydrated)

    @mock.patch('shakenfist.instance.Instances')
    def test_dependency_cycle_terminates(self, mock_instances):
        # A cycle should not be reachable, but walking one would hang the
        # API worker rather than return a wrong answer.
        cyclic = {
            BLOB_CHILD: FakeBlob(BLOB_CHILD, depends_on=BLOB_BASE),
            BLOB_BASE: FakeBlob(BLOB_BASE, depends_on=BLOB_CHILD),
        }
        self.blob_from_db_patcher.stop()
        self.addCleanup(self.blob_from_db_patcher.start)
        with mock.patch('shakenfist.blob.Blob.from_db',
                        side_effect=lambda u, suppress_failure_audit=False:
                        cyclic.get(u)):
            mock_instances.return_value = [
                FakeInstance(INSTANCE_UUID_1, {'devices': [
                    {'blob_uuid': BLOB_CHILD},
                ]}),
            ]

            usage = instance.instance_blob_usage()

        self.assertEqual({
            BLOB_CHILD: [INSTANCE_UUID_1],
            BLOB_BASE: [INSTANCE_UUID_1],
        }, usage)
