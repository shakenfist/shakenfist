# Copyright 2019 Michael Still and contributors
import time
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.daemons.cluster import scheduled_tasks as st
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


BLOB_UUID_1 = '11111111-1111-4111-8111-111111111111'
BLOB_UUID_2 = '22222222-2222-4222-8222-222222222222'
NODE_UUID_1 = 'aaaa1111-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
NODE_UUID_2 = 'bbbb2222-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
NODE_FQDN_1 = 'sf-1.example.com'
NODE_FQDN_2 = 'sf-2.example.com'
OP_UUID_1 = 'cccc3333-cccc-4ccc-8ccc-cccccccccccc'


class FakeBlob:
    """Minimal fake Blob for queue tests."""
    object_type = ObjectType.BLOB
    STATE_CREATED = dbo.STATE_CREATED
    STATE_DELETED = dbo.STATE_DELETED

    def __init__(self, uuid, state='created', ref_count=1, locations=None,
                 last_used=None, fetched_at=None):
        self.uuid = uuid
        self._state_value = state
        self._ref_count = ref_count
        self._locations = locations or []
        # Default to "old enough" so existing tests bypass the grace
        # period; new tests pass explicit timestamps.
        self.last_used = last_used if last_used is not None else 0.0
        self.fetched_at = fetched_at if fetched_at is not None else 0.0

    @property
    def state(self):
        return mock.MagicMock(value=self._state_value)

    @state.setter
    def state(self, value):
        self._state_value = value

    @property
    def ref_count(self):
        return self._ref_count

    @property
    def locations(self):
        return list(self._locations)

    def add_event(self, *args, **kwargs):
        pass


class FakeNode:
    def __init__(self, uuid, fqdn):
        self.uuid = uuid
        self.fqdn = fqdn


class FakeOp:
    def __init__(self, node_uuid, outstanding=True):
        self.node_uuid = node_uuid
        self._outstanding = outstanding

    def is_outstanding(self):
        return self._outstanding


class FillPerBlobQueueTestCase(base.ShakenFistTestCase):
    """Test _fill_per_blob_queue uses MariaDB for blob enumeration."""

    def setUp(self):
        super().setUp()
        # Clear the module-level queue between tests
        while not st.BLOB_CHECKS_QUEUE.empty():
            st.BLOB_CHECKS_QUEUE.get(block=False)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Blob.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_objects_by_state')
    def test_fill_uses_mariadb_not_etcd(self, mock_get_by_state, mock_from_db):
        blob1 = FakeBlob(BLOB_UUID_1)
        blob2 = FakeBlob(BLOB_UUID_2)
        mock_get_by_state.return_value = [BLOB_UUID_1, BLOB_UUID_2]
        mock_from_db.side_effect = [blob1, blob2]

        st._fill_per_blob_queue()

        mock_get_by_state.assert_called_once_with(
            ObjectType.BLOB, [Blob.STATE_CREATED])
        self.assertEqual(2, st.BLOB_CHECKS_QUEUE.qsize())

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Blob.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_objects_by_state')
    def test_fill_skips_missing_blobs(self, mock_get_by_state, mock_from_db):
        mock_get_by_state.return_value = [BLOB_UUID_1, BLOB_UUID_2]
        mock_from_db.side_effect = [None, FakeBlob(BLOB_UUID_2)]

        st._fill_per_blob_queue()

        self.assertEqual(1, st.BLOB_CHECKS_QUEUE.qsize())

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Blob.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_objects_by_state')
    def test_fill_empty_when_no_created_blobs(self, mock_get_by_state, mock_from_db):
        mock_get_by_state.return_value = []

        st._fill_per_blob_queue()

        mock_from_db.assert_not_called()
        self.assertEqual(0, st.BLOB_CHECKS_QUEUE.qsize())


class ProcessPerBlobQueueTestCase(base.ShakenFistTestCase):
    """Test _process_per_blob_queue uses cluster_operation_targets for
    duplicate prevention and correctly resolves node UUIDs to FQDNs."""

    def setUp(self):
        super().setUp()
        while not st.BLOB_CHECKS_QUEUE.empty():
            st.BLOB_CHECKS_QUEUE.get(block=False)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.nbo_schema.create_and_enqueue')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Node.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_cluster_operation_targets_for_object')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_blob_hashes')
    def test_schedules_checksum_when_no_pending_ops(
            self, mock_get_hashes, mock_get_targets,
            mock_node_from_db, mock_create_and_enqueue):
        blob = FakeBlob(BLOB_UUID_1, locations=[NODE_FQDN_1])
        st.BLOB_CHECKS_QUEUE.put(blob)

        mock_get_hashes.return_value = []
        mock_get_targets.return_value = []

        node = FakeNode(NODE_UUID_1, NODE_FQDN_1)
        mock_node_from_db.return_value = node

        st._process_per_blob_queue(execution_limit=5)

        mock_get_targets.assert_called_once_with(
            ObjectType.BLOB, BLOB_UUID_1)
        mock_create_and_enqueue.assert_called_once()

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.nbo_schema.create_and_enqueue')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Node.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_cluster_operation_targets_for_object')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_blob_hashes')
    def test_skips_node_with_pending_op(
            self, mock_get_hashes, mock_get_targets,
            mock_get_class, mock_node_from_db,
            mock_create_and_enqueue):
        blob = FakeBlob(BLOB_UUID_1, locations=[NODE_FQDN_1])
        st.BLOB_CHECKS_QUEUE.put(blob)

        mock_get_hashes.return_value = []

        # There is already a pending operation targeting this blob
        pending_target = ClusterOperationTargetData(
            operation_uuid=OP_UUID_1,
            operation_type='node_blob_op',
            target_object_type='blob',
            target_uuid=BLOB_UUID_1,
            sequence_number=1,
            created_at=time.time()
        )
        mock_get_targets.return_value = [pending_target]

        # The pending op is outstanding and assigned to NODE_UUID_1
        fake_op = FakeOp(NODE_UUID_1, outstanding=True)
        mock_get_class.return_value = mock.MagicMock(
            from_db=mock.MagicMock(return_value=fake_op))

        # Resolve NODE_UUID_1 -> NODE_FQDN_1
        node = FakeNode(NODE_UUID_1, NODE_FQDN_1)
        mock_node_from_db.return_value = node

        st._process_per_blob_queue(execution_limit=5)

        # Should NOT schedule a new operation because node already has one
        mock_create_and_enqueue.assert_not_called()

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.nbo_schema.create_and_enqueue')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Node.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_cluster_operation_targets_for_object')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_blob_hashes')
    def test_schedules_on_node_without_pending_op(
            self, mock_get_hashes, mock_get_targets,
            mock_get_class, mock_node_from_db,
            mock_create_and_enqueue):
        """When blob is on two nodes but only one has a pending op,
        schedule a checksum on the other node."""
        blob = FakeBlob(
            BLOB_UUID_1, locations=[NODE_FQDN_1, NODE_FQDN_2])
        st.BLOB_CHECKS_QUEUE.put(blob)

        mock_get_hashes.return_value = []

        # Pending op only on NODE_UUID_1
        pending_target = ClusterOperationTargetData(
            operation_uuid=OP_UUID_1,
            operation_type='node_blob_op',
            target_object_type='blob',
            target_uuid=BLOB_UUID_1,
            sequence_number=1,
            created_at=time.time()
        )
        mock_get_targets.return_value = [pending_target]

        fake_op = FakeOp(NODE_UUID_1, outstanding=True)
        mock_get_class.return_value = mock.MagicMock(
            from_db=mock.MagicMock(return_value=fake_op))

        node1 = FakeNode(NODE_UUID_1, NODE_FQDN_1)
        node2 = FakeNode(NODE_UUID_2, NODE_FQDN_2)

        def node_from_db(identifier):
            if identifier == NODE_UUID_1:
                return node1
            if identifier == NODE_FQDN_1:
                return node1
            if identifier == NODE_FQDN_2:
                return node2
            return None

        mock_node_from_db.side_effect = node_from_db

        st._process_per_blob_queue(execution_limit=5)

        # Should schedule exactly one operation, on NODE_FQDN_2
        mock_create_and_enqueue.assert_called_once()
        call_args = mock_create_and_enqueue.call_args
        self.assertEqual(NODE_UUID_2, call_args[0][0])

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.nbo_schema.create_and_enqueue')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Node.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_cluster_operation_targets_for_object')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_blob_hashes')
    def test_completed_ops_do_not_block_scheduling(
            self, mock_get_hashes, mock_get_targets,
            mock_get_class, mock_node_from_db,
            mock_create_and_enqueue):
        blob = FakeBlob(BLOB_UUID_1, locations=[NODE_FQDN_1])
        st.BLOB_CHECKS_QUEUE.put(blob)

        mock_get_hashes.return_value = []

        # There is a target record but the operation is already complete
        old_target = ClusterOperationTargetData(
            operation_uuid=OP_UUID_1,
            operation_type='node_blob_op',
            target_object_type='blob',
            target_uuid=BLOB_UUID_1,
            sequence_number=1,
            created_at=time.time() - 3600
        )
        mock_get_targets.return_value = [old_target]

        fake_op = FakeOp(NODE_UUID_1, outstanding=False)
        mock_get_class.return_value = mock.MagicMock(
            from_db=mock.MagicMock(return_value=fake_op))

        node = FakeNode(NODE_UUID_1, NODE_FQDN_1)
        mock_node_from_db.return_value = node

        st._process_per_blob_queue(execution_limit=5)

        # Completed ops should not block scheduling
        mock_create_and_enqueue.assert_called_once()

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.nbo_schema.create_and_enqueue')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_cluster_operation_targets_for_object')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_blob_hashes')
    def test_zero_ref_count_deletes_blob(
            self, mock_get_hashes, mock_get_targets,
            mock_create_and_enqueue):
        blob = FakeBlob(BLOB_UUID_1, ref_count=0)
        st.BLOB_CHECKS_QUEUE.put(blob)

        st._process_per_blob_queue(execution_limit=5)

        # Should delete, not schedule checksums
        mock_get_targets.assert_not_called()
        mock_create_and_enqueue.assert_not_called()
        self.assertEqual(dbo.STATE_DELETED, blob._state_value)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.nbo_schema.create_and_enqueue')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_cluster_operation_targets_for_object')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_blob_hashes')
    def test_zero_ref_count_within_grace_period_is_skipped(
            self, mock_get_hashes, mock_get_targets,
            mock_create_and_enqueue):
        # Freshly registered blob: ref_count is briefly zero between
        # snapshot_disk()'s b.register() and the snapshot operation's
        # a.add_index() call. The per-blob queue must leave it alone
        # during the 300s grace period.
        blob = FakeBlob(
            BLOB_UUID_1, ref_count=0,
            last_used=time.time(), fetched_at=time.time())
        st.BLOB_CHECKS_QUEUE.put(blob)

        st._process_per_blob_queue(execution_limit=5)

        self.assertEqual(dbo.STATE_CREATED, blob._state_value)
        mock_get_targets.assert_not_called()
        mock_create_and_enqueue.assert_not_called()
