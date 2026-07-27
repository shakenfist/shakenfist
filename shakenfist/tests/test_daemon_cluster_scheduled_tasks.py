# Copyright 2019 Michael Still and contributors
import time
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.daemons.cluster import scheduled_tasks as st
from shakenfist.exceptions import InvalidStateException
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.namespace_key_attributes import NamespaceKeyAttributesData
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


BLOB_UUID_1 = '11111111-1111-4111-8111-111111111111'
BLOB_UUID_2 = '22222222-2222-4222-8222-222222222222'
NODE_UUID_1 = 'aaaa1111-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
NODE_UUID_2 = 'bbbb2222-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
NODE_FQDN_1 = 'sf-1.example.com'
NODE_FQDN_2 = 'sf-2.example.com'
OP_UUID_1 = 'cccc3333-cccc-4ccc-8ccc-cccccccccccc'
KEY_UUID_1 = 'dddd4444-dddd-4ddd-8ddd-dddddddddddd'


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


# ---------------------------------------------------------------------------
# prune_events scheduled-task wiring tests
# ---------------------------------------------------------------------------

class ClusterPruneEventsScheduledTaskTestCase(base.ShakenFistTestCase):
    """Tests for the scheduled_tasks.prune_events() wrapper.

    The wrapper is a thin shim: call mariadb.prune_events(), log the
    row count at info level, and swallow any exception with a warning log.
    These tests verify the shim behaves correctly without touching
    real MariaDB or gRPC.
    """

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.prune_events',
                return_value=42)
    def test_prune_events_calls_mariadb_and_logs_row_count(
            self, mock_prune):
        """prune_events() calls mariadb.prune_events and logs the returned count."""
        with mock.patch.object(st.LOG, 'info') as mock_log_info:
            st.prune_events()

        mock_prune.assert_called_once()
        # At least one log call should mention the row count.
        log_messages = ' '.join(
            str(call[0][0]) for call in mock_log_info.call_args_list
        )
        self.assertIn('42', log_messages)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.prune_events',
                side_effect=Exception('db offline'))
    def test_prune_events_handles_exception(self, mock_prune):
        """prune_events() catches exceptions and logs a warning without re-raising."""
        with mock.patch.object(st.LOG, 'warning') as mock_log_warn:
            # Must not raise.
            st.prune_events()

        mock_log_warn.assert_called_once()
        warn_msg = str(mock_log_warn.call_args[0][0])
        self.assertIn('failed', warn_msg.lower())


class FakeNamespaceKey:
    """Minimal fake NamespaceKey for the expiry sweep tests."""
    object_type = ObjectType.NAMESPACE_KEY

    def __init__(self, name, namespace='banana', state=dbo.STATE_CREATED,
                 update_time=0.0):
        self.uuid = f'{namespace}-{name}'
        self.name = name
        self.namespace = namespace
        self._state_value = state
        self._update_time = update_time
        self.events = []
        self.hard_deleted = False

    @property
    def state(self):
        return mock.MagicMock(value=self._state_value,
                              update_time=self._update_time)

    def add_event(self, event_type, message, extra=None):
        self.events.append((event_type, message, extra))

    def delete(self):
        self._state_value = dbo.STATE_DELETED

    def hard_delete(self):
        self.hard_deleted = True


class FakeFinalStateObject:
    """Minimal fake for per-deleted-object processing tests."""

    def __init__(self, state_value='deleted', age=1000000.0):
        self._state = mock.MagicMock(
            value=state_value, update_time=time.time() - age)
        self.hard_deleted = False

    @property
    def state(self):
        return self._state

    def hard_delete(self):
        self.hard_deleted = True


def _attrs(expiry):
    return NamespaceKeyAttributesData(
        uuid=KEY_UUID_1, key='aGFzaA==', nonce='noncenonce', expiry=expiry)


class ReapExpiredNamespaceKeysTestCase(base.ShakenFistTestCase):
    """The cluster sweep which soft deletes long-expired keys.

    Expiry is enforced check-at-use elsewhere; these tests are only
    about the tidy up, so they care about which keys get their state
    moved and which are left alone.
    """

    def _sweep(self, pairs, grace=3600, now=10000.0):
        """Run the sweep over one namespace holding ``pairs``."""
        namespaces = [mock.MagicMock(uuid='banana')]
        with mock.patch.object(config, 'NAMESPACE_KEY_REAP_GRACE', grace), \
                mock.patch(
                    'shakenfist.daemons.cluster.scheduled_tasks.Namespaces',
                    return_value=namespaces), \
                mock.patch(
                    'shakenfist.daemons.cluster.scheduled_tasks.'
                    'keys_with_attributes',
                    return_value=pairs) as mock_keys, \
                mock.patch(
                    'shakenfist.daemons.cluster.scheduled_tasks.time.time',
                    return_value=now):
            st.reap_expired_namespace_keys()
        return mock_keys

    def test_soft_deletes_a_key_past_the_grace_period(self):
        # now 10000, grace 3600, so the cutoff is 6400.
        key = FakeNamespaceKey('stale')
        self._sweep([(key, _attrs(1000.0))])

        self.assertEqual(dbo.STATE_DELETED, key.state.value)

    def test_leaves_a_key_still_inside_the_grace_period(self):
        # Expired at 9000, which is after the 6400 cutoff. The key no
        # longer authenticates, but an operator can still see it.
        key = FakeNamespaceKey('recent')
        self._sweep([(key, _attrs(9000.0))])

        self.assertEqual(dbo.STATE_CREATED, key.state.value)
        self.assertEqual([], key.events)

    def test_ignores_a_key_which_never_expires(self):
        key = FakeNamespaceKey('forever')
        self._sweep([(key, _attrs(None))])

        self.assertEqual(dbo.STATE_CREATED, key.state.value)

    def test_skips_a_key_an_earlier_sweep_already_deleted(self):
        # Otherwise every sweep would re-delete it for the whole
        # CLEANER_DELAY window before the hard delete lands, spamming
        # the event log and tripping the state machine.
        key = FakeNamespaceKey('gone', state=dbo.STATE_DELETED)
        self._sweep([(key, _attrs(1000.0))])

        self.assertEqual([], key.events)

    def test_records_an_audit_event_carrying_no_secrets(self):
        key = FakeNamespaceKey('stale')
        self._sweep([(key, _attrs(1000.0))])

        self.assertEqual(1, len(key.events))
        event_type, message, extra = key.events[0]
        self.assertEqual(EVENT_TYPE_AUDIT, event_type)
        self.assertIn('expired', message)
        self.assertEqual({'expiry': 1000.0}, extra)

    def test_survives_a_key_deleted_underneath_it(self):
        key = FakeNamespaceKey('racing')
        key.delete = mock.Mock(side_effect=InvalidStateException('raced'))

        # Must not raise -- the key is going away either way.
        self._sweep([(key, _attrs(1000.0))])

    def test_does_nothing_when_reaping_is_disabled(self):
        key = FakeNamespaceKey('stale')
        mock_keys = self._sweep([(key, _attrs(1000.0))], grace=0)

        mock_keys.assert_not_called()
        self.assertEqual(dbo.STATE_CREATED, key.state.value)


class HardDeleteExpiredNamespaceKeyTestCase(base.ShakenFistTestCase):
    """The sweep only soft deletes; the standard reaper finishes the job."""

    def setUp(self):
        super().setUp()
        while not st.DELETED_OBJECTS_QUEUE.empty():
            st.DELETED_OBJECTS_QUEUE.get(block=False)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_hard_deletes_a_soft_deleted_key_after_cleaner_delay(
            self, mock_get_class):
        # State last changed at the epoch, so it is comfortably older
        # than CLEANER_DELAY.
        key = FakeNamespaceKey('stale', state=dbo.STATE_DELETED,
                               update_time=0.0)
        mock_get_class.return_value.from_db.return_value = key
        st.DELETED_OBJECTS_QUEUE.put(('namespace_key', KEY_UUID_1))

        st._process_per_deleted_object_queue(execution_limit=5)

        self.assertTrue(key.hard_deleted)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_leaves_a_recently_deleted_key_alone(self, mock_get_class):
        key = FakeNamespaceKey('fresh', state=dbo.STATE_DELETED,
                               update_time=time.time())
        mock_get_class.return_value.from_db.return_value = key
        st.DELETED_OBJECTS_QUEUE.put(('namespace_key', KEY_UUID_1))

        st._process_per_deleted_object_queue(execution_limit=5)

        self.assertFalse(key.hard_deleted)

    def test_the_reaper_enumerates_namespace_keys_at_all(self):
        # The sweep only ever soft deletes. If NamespaceKey were not
        # registered in OBJECT_NAMES_TO_CLASSES then nothing would ever
        # hard delete the rows, and the sweep would quietly leak them.
        self.assertIn('namespace_key', OBJECT_NAMES_TO_CLASSES)

        with mock.patch(
                'shakenfist.daemons.cluster.scheduled_tasks.'
                'mariadb.get_objects_by_state',
                side_effect=lambda objtype, states, updated_before=None: (
                    [KEY_UUID_1]
                    if objtype == ObjectType.NAMESPACE_KEY else [])):
            st._fill_per_deleted_object_queue()

        self.assertEqual([('namespace_key', KEY_UUID_1)],
                         list(st.DELETED_OBJECTS_QUEUE.queue))


class PerDeletedObjectQueueTestCase(base.ShakenFistTestCase):
    """The deleted-object sweep fills with (type, uuid) tuples and only
    hydrates at processing time, one object per try/except (issue 3533)."""

    def setUp(self):
        super().setUp()
        while not st.DELETED_OBJECTS_QUEUE.empty():
            st.DELETED_OBJECTS_QUEUE.get(block=False)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.mariadb.get_objects_by_state')
    def test_fill_enqueues_tuples_without_hydration(
            self, mock_get_by_state, mock_get_class):
        def by_state(objtype, states, updated_before=None):
            self.assertIsNotNone(updated_before)
            self.assertTrue(updated_before < time.time())
            if objtype == ObjectType.NETWORK:
                return [BLOB_UUID_1, BLOB_UUID_2]
            return []
        mock_get_by_state.side_effect = by_state

        st._fill_per_deleted_object_queue()

        # Hydration must not happen at fill time -- with a large backlog
        # it blew the fill budget and the watchdog window.
        mock_get_class.assert_not_called()

        items = []
        while not st.DELETED_OBJECTS_QUEUE.empty():
            items.append(st.DELETED_OBJECTS_QUEUE.get(block=False))
        self.assertEqual(
            [('network', BLOB_UUID_1), ('network', BLOB_UUID_2)], items)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_process_hard_deletes_old_final_objects(self, mock_get_class):
        obj = FakeFinalStateObject()
        mock_get_class.return_value.from_db.return_value = obj
        st.DELETED_OBJECTS_QUEUE.put(('network', BLOB_UUID_1))

        processed = st._process_per_deleted_object_queue()

        self.assertEqual(1, processed)
        self.assertTrue(obj.hard_deleted)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_process_poison_object_does_not_abort_pass(self, mock_get_class):
        # The first object explodes on hydration; the second must still
        # be processed. Previously one exception aborted the entire
        # scheduled pass.
        good = FakeFinalStateObject()

        def from_db(obj_uuid, suppress_failure_audit=False):
            if obj_uuid == BLOB_UUID_1:
                raise RuntimeError('database exploded')
            return good
        mock_get_class.return_value.from_db.side_effect = from_db

        st.DELETED_OBJECTS_QUEUE.put(('network', BLOB_UUID_1))
        st.DELETED_OBJECTS_QUEUE.put(('network', BLOB_UUID_2))

        processed = st._process_per_deleted_object_queue()

        self.assertEqual(2, processed)
        self.assertTrue(good.hard_deleted)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_process_skips_vanished_and_non_final_objects(
            self, mock_get_class):
        # A vanished object (concurrent hard delete) and an object whose
        # state has left the final set since fill time are both skipped.
        resurrected = FakeFinalStateObject(state_value='created')

        def from_db(obj_uuid, suppress_failure_audit=False):
            if obj_uuid == BLOB_UUID_1:
                return None
            return resurrected
        mock_get_class.return_value.from_db.side_effect = from_db

        st.DELETED_OBJECTS_QUEUE.put(('network', BLOB_UUID_1))
        st.DELETED_OBJECTS_QUEUE.put(('network', BLOB_UUID_2))

        processed = st._process_per_deleted_object_queue()

        self.assertEqual(2, processed)
        self.assertFalse(resurrected.hard_deleted)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_process_respects_delay(self, mock_get_class):
        # An object still inside its post-deletion grace period is left
        # alone (fill filters age in SQL, but queue entries can be old
        # and objects can be re-deleted in the interim).
        young = FakeFinalStateObject(age=1.0)
        mock_get_class.return_value.from_db.return_value = young
        st.DELETED_OBJECTS_QUEUE.put(('network', BLOB_UUID_1))

        processed = st._process_per_deleted_object_queue()

        self.assertEqual(1, processed)
        self.assertFalse(young.hard_deleted)


class ReconcileOrphanedObjectsTestCase(base.ShakenFistTestCase):
    """Orphan reconciliation: phantoms removed server-side, zombies
    repaired by writing a deleted state row after two consecutive
    observations (issue 3534)."""

    def setUp(self):
        super().setUp()
        st._ZOMBIE_CANDIDATES.clear()

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids',
                return_value=[])
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=3)
    def test_phantoms_removed_for_all_types(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        st.reconcile_orphaned_objects()

        from shakenfist import mariadb
        deleted_types = [
            c.args[0] for c in mock_delete_orphans.call_args_list]
        self.assertEqual(
            sorted(mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES),
            sorted(deleted_types))
        for c in mock_delete_orphans.call_args_list:
            self.assertTrue(c.args[1] < time.time())
        mock_delete_attrs.assert_called_once()
        mock_set_state.assert_not_called()

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_zombies_repaired_after_two_observations(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        def stateless(objtype):
            if objtype == ObjectType.NETWORK:
                return [BLOB_UUID_1]
            return []
        mock_stateless.side_effect = stateless

        # First observation: candidate recorded, nothing repaired.
        st.reconcile_orphaned_objects()
        mock_set_state.assert_not_called()

        # Second observation: repaired with a deleted state row.
        st.reconcile_orphaned_objects()
        mock_set_state.assert_called_once()
        objtype, obj_uuid, state = mock_set_state.call_args.args
        self.assertEqual(ObjectType.NETWORK, objtype)
        self.assertEqual(BLOB_UUID_1, obj_uuid)
        self.assertEqual('deleted', state.value)
        mock_add_event.assert_called_once()

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids',
                return_value=[])
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_zombie_repair_excludes_nodes_and_namespaces(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        st.reconcile_orphaned_objects()

        queried = {c.args[0] for c in mock_stateless.call_args_list}
        self.assertNotIn(ObjectType.NODE, queried)
        self.assertNotIn(ObjectType.NAMESPACE, queried)
        self.assertIn(ObjectType.NETWORK, queried)

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_zombie_gone_between_sweeps_not_repaired(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        # A zombie observed once which then disappears (its state row
        # was written by its creator after all) must not be repaired.
        responses = [[BLOB_UUID_1], []]

        def stateless(objtype):
            if objtype == ObjectType.NETWORK:
                return responses.pop(0) if responses else []
            return []
        mock_stateless.side_effect = stateless

        st.reconcile_orphaned_objects()
        st.reconcile_orphaned_objects()
        mock_set_state.assert_not_called()
