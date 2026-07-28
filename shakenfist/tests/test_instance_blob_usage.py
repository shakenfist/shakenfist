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

        self.mock_blob_from_db = mock.patch(
            'shakenfist.blob.Blob.from_db', side_effect=from_db)
        self.mock_blob_from_db.start()
        self.addCleanup(self.mock_blob_from_db.stop)

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
