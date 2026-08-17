# Copyright 2019 Michael Still and contributors
import time
from unittest import mock

from prometheus_client import REGISTRY

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.daemons.cluster import scheduled_tasks as st
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.exceptions import InvalidStateException
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.namespace_key_attributes import NamespaceKeyAttributesData
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist import mariadb


BLOB_UUID_1 = '11111111-1111-4111-8111-111111111111'
BLOB_UUID_2 = '22222222-2222-4222-8222-222222222222'
NODE_UUID_1 = 'aaaa1111-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
NODE_UUID_2 = 'bbbb2222-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
NODE_FQDN_1 = 'sf-1.example.com'
NODE_FQDN_2 = 'sf-2.example.com'
OP_UUID_1 = 'cccc3333-cccc-4ccc-8ccc-cccccccccccc'
KEY_UUID_1 = 'dddd4444-dddd-4ddd-8ddd-dddddddddddd'


def _reset_sweep_failure_state():
    """Clear both halves of the sweep failure streak state.

    The dict and the gauge are separate process-global state and the
    reset branch in _sweep_work_list() is guarded on the dict, so
    clearing only the dict leaves the gauge carrying a value from an
    earlier test that nothing will ever zero.

    The resume offsets are process-global for the same reason and would
    otherwise silently rotate a later test's expected object ordering.
    """
    st._SWEEP_FAILURE_STREAK.clear()
    st.SWEEP_WORK_LIST_FAILURE_STREAK.clear()
    st._SWEEP_RESUME_AFTER.clear()


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
        _reset_sweep_failure_state()

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Blob.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_objects_by_state')
    def test_fill_uses_mariadb_not_etcd(self, mock_get_by_state, mock_from_db):
        blob1 = FakeBlob(BLOB_UUID_1)
        blob2 = FakeBlob(BLOB_UUID_2)
        mock_get_by_state.return_value = [BLOB_UUID_1, BLOB_UUID_2]
        mock_from_db.side_effect = [blob1, blob2]

        st._fill_per_blob_queue()

        mock_get_by_state.assert_called_once_with(
            ObjectType.BLOB, [Blob.STATE_CREATED], updated_before=None)
        self.assertEqual(2, st.BLOB_CHECKS_QUEUE.qsize())

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Blob.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_objects_by_state')
    def test_fill_failure_is_visible_not_empty(
            self, mock_get_by_state, mock_from_db):
        """A failed read is not an empty work list.

        This sweep shares the deleted-object sweep's failure mode: with
        `or []` a failed read produced a silent no-op pass which looked
        exactly like a healthy cluster with nothing to check (#3638).
        """
        mock_get_by_state.return_value = None

        st._fill_per_blob_queue()

        mock_from_db.assert_not_called()
        self.assertEqual(0, st.BLOB_CHECKS_QUEUE.qsize())
        self.assertEqual(
            {('per_blob', 'blob'): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_blob', 'object_type': 'blob'}))

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


class FillPerInstanceQueueTestCase(base.ShakenFistTestCase):
    """The per-instance sweep must also distinguish None from []."""

    def setUp(self):
        super().setUp()
        while not st.INSTANCE_CHECKS_QUEUE.empty():
            st.INSTANCE_CHECKS_QUEUE.get(block=False)
        _reset_sweep_failure_state()

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Instance.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_objects_by_state')
    def test_fill_failure_is_visible_not_empty(
            self, mock_get_by_state, mock_from_db):
        mock_get_by_state.return_value = None

        st._fill_per_instance_queue()

        mock_from_db.assert_not_called()
        self.assertEqual(0, st.INSTANCE_CHECKS_QUEUE.qsize())
        self.assertEqual(
            {('per_instance', 'instance'): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_instance', 'object_type': 'instance'}))

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.Instance.from_db')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.get_objects_by_state')
    def test_unavailable_database_counts_as_a_failed_read(
            self, mock_get_by_state, mock_from_db):
        """A tier outage is a failed read, not an exception to escape on.

        The oversized-reply case returns None, but an unreachable
        database tier exhausts the retry budget and raises
        DatabaseUnavailable instead -- which is not an RpcError, so it
        propagates through the mariadb wrapper. It must reach the same
        streak, or the gauge reads zero during precisely the outage it
        was added to make visible.
        """
        mock_get_by_state.side_effect = DatabaseUnavailable('tier is down')

        st._fill_per_instance_queue()

        mock_from_db.assert_not_called()
        self.assertEqual(0, st.INSTANCE_CHECKS_QUEUE.qsize())
        self.assertEqual(
            {('per_instance', 'instance'): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_instance', 'object_type': 'instance'}))


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

        # Must not raise -- the key is going away either way. And no
        # audit event: the event records a delete that happened, and
        # this one did not. Eventing before the attempt is how 4,151
        # undeletable keys generated ~380k junk events/day (issue 3588).
        self._sweep([(key, _attrs(1000.0))])

        self.assertEqual([], key.events)

    def test_skips_a_zombie_key_with_no_state_row(self):
        # A static row with no object_states row (a mixed-version deploy
        # artifact) has no legal transition to deleted, so delete()
        # raises on every sweep forever. The sweep must leave these to
        # reconcile_orphaned_objects: no delete attempt, and above all
        # no per-key audit event per pass (issue 3588).
        key = FakeNamespaceKey('zombie', state=None)
        key.delete = mock.Mock(
            side_effect=AssertionError('zombie must not be deleted here'))

        self._sweep([(key, _attrs(1000.0))])

        self.assertEqual([], key.events)
        key.delete.assert_not_called()

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
        _reset_sweep_failure_state()

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

    @mock.patch('shakenfist.mariadb.get_objects_by_state')
    def test_fill_failure_is_visible_not_empty(self, mock_get_by_state):
        # get_objects_by_state returns None when the read failed
        # (e.g. a RESOURCE_EXHAUSTED oversized gRPC reply), distinct
        # from [] for no matches. Collapsing the two silently turned
        # the GC off for an object type forever (#3638): the failed
        # type must be skipped visibly while other types still enqueue.
        def by_state(objtype, states, updated_before=None):
            if objtype == ObjectType.NETWORK:
                return None
            if objtype == ObjectType.BLOB:
                return [BLOB_UUID_1]
            return []
        mock_get_by_state.side_effect = by_state

        st._fill_per_deleted_object_queue()

        self.assertEqual([('blob', BLOB_UUID_1)],
                         list(st.DELETED_OBJECTS_QUEUE.queue))
        # Every object type was still read: a per-reply failure costs
        # one type, not the pass.
        self.assertEqual(
            len(OBJECT_NAMES_TO_CLASSES), mock_get_by_state.call_count)
        self.assertEqual(
            {('per_deleted_object', 'network'): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))

    @mock.patch('shakenfist.mariadb.get_objects_by_state')
    def test_unavailable_database_stops_the_sweep_after_one_budget(
            self, mock_get_by_state):
        """A tier outage must cost one retry budget, not 28 of them.

        DatabaseUnavailable is only raised once _grpc_call has spent its
        whole budget -- up to GRPC_RETRIES full deadlines plus the
        inter-attempt sleeps. There are 28 object types in this loop and
        _run_due_scheduled_jobs() pets the watchdog only *between* jobs,
        so continuing past a tier-wide failure multiplies this job's
        worst case by 28 and takes it past sf-cluster's WatchdogSec.
        That SIGABRTs the elected maintainer and costs a lock failover,
        where previously the first DatabaseUnavailable merely ended the
        pass. This function has blown the watchdog window once already
        (issue 3533).

        A dead tier does not become reachable on the 28th ask, so the
        bound is the property worth pinning: one read attempt, then stop.
        """
        mock_get_by_state.side_effect = DatabaseUnavailable('tier is down')

        st._fill_per_deleted_object_queue()

        self.assertEqual(1, mock_get_by_state.call_count)
        self.assertEqual(0, st.DELETED_OBJECTS_QUEUE.qsize())
        # The one type we did attempt is still counted, so the gauge is
        # not silent during exactly the outage it exists to expose.
        first_type = next(iter(OBJECT_NAMES_TO_CLASSES))
        self.assertEqual(
            {('per_deleted_object', first_type): 1},
            st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': first_type}))

    @mock.patch('shakenfist.mariadb.get_objects_by_state')
    def test_unavailable_database_keeps_work_read_before_it(
            self, mock_get_by_state):
        # Stopping the loop must not discard the types already read.
        # 'blob' comes before 'network' in OBJECT_NAMES_TO_CLASSES, so
        # its work is still enqueued for this pass even though the tier
        # goes away part way through.
        def by_state(objtype, states, updated_before=None):
            if objtype == ObjectType.NETWORK:
                raise DatabaseUnavailable('tier is down')
            if objtype == ObjectType.BLOB:
                return [BLOB_UUID_1]
            return []
        mock_get_by_state.side_effect = by_state

        st._fill_per_deleted_object_queue()

        self.assertEqual([('blob', BLOB_UUID_1)],
                         list(st.DELETED_OBJECTS_QUEUE.queue))
        types = list(OBJECT_NAMES_TO_CLASSES)
        self.assertEqual(
            types.index('network') + 1, mock_get_by_state.call_count)
        self.assertEqual(
            {('per_deleted_object', 'network'): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))

    @mock.patch('shakenfist.mariadb.get_objects_by_state')
    def test_a_slow_type_cannot_starve_the_types_behind_it(
            self, mock_get_by_state):
        """Stopping the pass must not stop it in the same place forever.

        DatabaseUnavailable is not only raised for a dead tier: an
        exhausted DEADLINE_EXCEEDED budget raises it too, which one
        large, slow or lock-contended query can produce for a single
        object type while the tier is otherwise healthy. With a fixed
        starting point, `break` would mean every type after that one is
        never swept again -- and the backlog that then accumulates makes
        the slow type slower still. That is the #3638 ratchet rebuilt
        one level up, so each pass resumes after wherever the last one
        stopped.
        """
        types = list(OBJECT_NAMES_TO_CLASSES)
        slow = types[0]

        def by_state(objtype, states, updated_before=None):
            if str(objtype) == slow:
                raise DatabaseUnavailable('this one query is too slow')
            return []
        mock_get_by_state.side_effect = by_state

        # Pass one stops on the very first type, having read only it.
        st._fill_per_deleted_object_queue()
        self.assertEqual(1, mock_get_by_state.call_count)

        # Pass two starts after it and reaches every other type, rather
        # than dying on the same query again.
        mock_get_by_state.reset_mock()
        st._fill_per_deleted_object_queue()

        # Every other type is reached, and the slow one is retried
        # last: one wasted budget per pass, and nothing behind it
        # starves. The resume point then holds steady, so this is the
        # steady state rather than a one-off recovery.
        read = [str(c.args[0]) for c in mock_get_by_state.call_args_list]
        self.assertEqual(types[1:] + [slow], read)

    @mock.patch('shakenfist.mariadb.get_objects_by_state', return_value=[])
    def test_a_completed_pass_starts_from_the_top_again(
            self, mock_get_by_state):
        # Only a pass which stopped early leaves a resume offset behind.
        types = list(OBJECT_NAMES_TO_CLASSES)
        st._SWEEP_RESUME_AFTER['per_deleted_object'] = types[5]

        st._fill_per_deleted_object_queue()
        self.assertNotIn('per_deleted_object', st._SWEEP_RESUME_AFTER)

        mock_get_by_state.reset_mock()
        st._fill_per_deleted_object_queue()
        self.assertEqual(
            types, [str(c.args[0]) for c in mock_get_by_state.call_args_list])

    @mock.patch('shakenfist.mariadb.get_objects_by_state')
    def test_demotion_stops_publishing_the_streak(self, mock_get_by_state):
        # Only the elected node runs these sweeps, so a demoted node
        # holding a non-zero streak keeps a "streak > 0" alert firing
        # against work it is no longer doing. Nothing on that node will
        # run the reset branch again, so the clear has to be explicit.
        mock_get_by_state.side_effect = (
            lambda objtype, states, updated_before=None:
            None if objtype == ObjectType.NETWORK else [])
        st._fill_per_deleted_object_queue()
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))

        st.clear_sweep_failure_metrics()

        self.assertEqual({}, st._SWEEP_FAILURE_STREAK)
        # Removed entirely rather than zeroed: a node which is not the
        # leader should not answer for these sweeps at all.
        self.assertIsNone(
            REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))

    @mock.patch('shakenfist.mariadb.get_objects_by_state')
    def test_fill_failure_streak_counts_and_resets(self, mock_get_by_state):
        # The streak counts consecutive failed passes per object type,
        # and a successful read clears it -- the alertable signal is
        # "still failing", not "failed once ever" (#3638).
        def failing(objtype, states, updated_before=None):
            return None if objtype == ObjectType.NETWORK else []
        mock_get_by_state.side_effect = failing
        st._fill_per_deleted_object_queue()
        st._fill_per_deleted_object_queue()
        self.assertEqual(
            {('per_deleted_object', 'network'): 2}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            2, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))

        mock_get_by_state.side_effect = (
            lambda objtype, states, updated_before=None: [])
        st._fill_per_deleted_object_queue()
        self.assertEqual({}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            0, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))

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
        _reset_sweep_failure_state()

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

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_failed_zombie_read_is_visible_not_empty(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        """A zombie read that failed is not "there are no zombies".

        `if uuids is None: continue` is the same silent skipped pass the
        sweep streak gauge exists to expose (#3638): repair quietly does
        nothing while the zombies it exists to fix stay invisible to
        every state-driven iterator. A per-reply failure still costs
        only that object type.
        """
        def stateless(objtype):
            if objtype == ObjectType.NETWORK:
                return None
            return []
        mock_stateless.side_effect = stateless

        st.reconcile_orphaned_objects()

        self.assertEqual(
            {('reconcile_orphans', 'network'): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'reconcile_orphans', 'object_type': 'network'}))
        # Every other reconcilable type was still read.
        self.assertEqual(
            len([t for t in mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES
                 if t not in st.ZOMBIE_REPAIR_EXCLUDED_TYPES]),
            mock_stateless.call_count)

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids',
                side_effect=DatabaseUnavailable('tier is down'))
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_unavailable_database_stops_zombie_repair_after_one_budget(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        # Same bound as the deleted-object sweep, and for the same
        # reason: this loop also runs one read per object type inside a
        # single scheduled job whose watchdog is only petted between
        # jobs, and each DatabaseUnavailable costs a full _grpc_call
        # retry budget.
        st.reconcile_orphaned_objects()

        self.assertEqual(1, mock_stateless.call_count)
        first_type = next(
            t for t in mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES
            if t not in st.ZOMBIE_REPAIR_EXCLUDED_TYPES)
        self.assertEqual(
            {('reconcile_orphans', first_type): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'reconcile_orphans', 'object_type': first_type}))
        mock_set_state.assert_not_called()

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_a_slow_type_cannot_starve_the_types_behind_it(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        # Same starvation hazard as the deleted-object sweep: one slow
        # object type must not permanently hide every type after it.
        zombie_types = [t for t in mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES
                        if t not in st.ZOMBIE_REPAIR_EXCLUDED_TYPES]
        slow = zombie_types[0]

        def stateless(objtype):
            if str(objtype) == slow:
                raise DatabaseUnavailable('this one query is too slow')
            return []
        mock_stateless.side_effect = stateless

        st.reconcile_orphaned_objects()
        self.assertEqual(1, mock_stateless.call_count)

        mock_stateless.reset_mock()
        st.reconcile_orphaned_objects()

        read = [str(c.args[0]) for c in mock_stateless.call_args_list]
        self.assertEqual(zombie_types[1:] + [slow], read)


class ReapFederationRecordsTestCase(base.ShakenFistTestCase):
    """Housekeeping for the two federated exchange abuse tables.

    Neither row is read once it has gone stale, so without this sweep
    both tables grow for the life of the cluster.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def test_an_expired_replay_record_is_removed(self):
        st.mariadb.record_federated_exchange(
            'jti-old', KEY_UUID_1, time.time() - st.REPLAY_REAP_GRACE - 60)

        st.reap_federation_records()
        self.assertEqual({}, self.mock_mariadb.federation_replay)

    def test_a_live_replay_record_is_kept(self):
        st.mariadb.record_federated_exchange(
            'jti-live', KEY_UUID_1, time.time() + 300)

        st.reap_federation_records()
        self.assertEqual(1, len(self.mock_mariadb.federation_replay))

    def test_a_just_expired_record_survives_the_grace_period(self):
        # The grace covers clock skew between the node running this
        # sweep and whichever node verifies a token. A node running
        # behind would otherwise still accept a token whose replay
        # record a node running ahead had already deleted -- which is
        # precisely the replay the table exists to refuse.
        st.mariadb.record_federated_exchange(
            'jti-recent', KEY_UUID_1, time.time() - 60)

        st.reap_federation_records()
        self.assertEqual(1, len(self.mock_mariadb.federation_replay))

    def test_a_closed_rate_limit_window_is_removed(self):
        st.mariadb.count_federated_attempt(
            '10.0.0.1', int(time.time()) - st.RATE_LIMIT_REAP_GRACE - 60)

        st.reap_federation_records()
        self.assertEqual({}, self.mock_mariadb.federation_rate_limits)

    def test_the_current_rate_limit_window_is_kept(self):
        # Reaping the window a request is currently being counted
        # against would reset that caller's allowance mid-minute.
        st.mariadb.count_federated_attempt('10.0.0.1', int(time.time()))

        st.reap_federation_records()
        self.assertEqual(1, len(self.mock_mariadb.federation_rate_limits))

    def test_the_sweep_reports_what_it_removed(self):
        st.mariadb.record_federated_exchange(
            'jti-old', KEY_UUID_1, time.time() - st.REPLAY_REAP_GRACE - 60)
        st.mariadb.count_federated_attempt(
            '10.0.0.1', int(time.time()) - st.RATE_LIMIT_REAP_GRACE - 60)

        with mock.patch.object(st.LOG, 'info') as log_info:
            st.reap_federation_records()

        messages = ' '.join(str(c[0][0]) for c in log_info.call_args_list)
        self.assertIn('replay', messages)
        self.assertIn('rate limit', messages)

    def test_an_empty_sweep_says_nothing(self):
        # Quiet when there is nothing to do, so the log stays readable
        # at four sweeps an hour forever.
        with mock.patch.object(st.LOG, 'info') as log_info:
            st.reap_federation_records()

        self.assertEqual([], log_info.call_args_list)


def _capacity_node(node_uuid, limit_cpus=16, limit_memory_mb=32768,
                   limit_disk_gb=500, used_cpus=4, used_memory_mb=8192,
                   used_disk_gb=100, expected_demand=2.5):
    return {
        'node_uuid': node_uuid,
        'limit_cpus': limit_cpus,
        'limit_memory_mb': limit_memory_mb,
        'limit_disk_gb': limit_disk_gb,
        'used_cpus': used_cpus,
        'used_memory_mb': used_memory_mb,
        'used_disk_gb': used_disk_gb,
        'expected_demand': expected_demand,
        'delta_used_cpus': 0,
        'delta_used_memory_mb': 0,
        'delta_used_disk_gb': 0
    }


def _capacity_reply(nodes, nodes_added=0, nodes_removed=0, claims_expired=0):
    return {
        'success': True,
        'nodes_added': nodes_added,
        'nodes_removed': nodes_removed,
        'claims_expired': claims_expired,
        'nodes': nodes,
        'cluster': {
            'total_cpus': sum(n['limit_cpus'] for n in nodes),
            'total_memory_mb': sum(n['limit_memory_mb'] for n in nodes),
            'total_disk_gb': sum(n['limit_disk_gb'] for n in nodes),
            'claimed_cpus': 0,
            'claimed_memory_mb': 0,
            'claimed_disk_gb': 0,
            'unclaimed_used_cpus': sum(n['used_cpus'] for n in nodes),
            'unclaimed_used_memory_mb': sum(
                n['used_memory_mb'] for n in nodes),
            'unclaimed_used_disk_gb': sum(n['used_disk_gb'] for n in nodes)
        }
    }


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels)


class ReconcileSchedulerCapacityTaskTestCase(base.ShakenFistTestCase):
    """The scheduled_tasks.reconcile_scheduler_capacity() wrapper: one
    mariadb RPC per pass, gauges updated from the reply, stale node
    label sets removed, and failures counted without raising."""

    def setUp(self):
        super().setUp()
        # Metrics and the exported-nodes record are module level; reset
        # them so tests do not observe each other's label sets.
        st._CAPACITY_EXPORTED_NODES.clear()
        st.SCHEDULER_CAPACITY_NODE_LIMIT.clear()
        st.SCHEDULER_CAPACITY_NODE_USED.clear()
        st.SCHEDULER_CAPACITY_NODE_EXPECTED_DEMAND.clear()
        st.SCHEDULER_CAPACITY_CLUSTER_TOTAL.clear()
        st.SCHEDULER_CAPACITY_CLUSTER_CLAIMED.clear()
        st.SCHEDULER_CAPACITY_CLUSTER_UNCLAIMED_USED.clear()

    @mock.patch(
        'shakenfist.daemons.cluster.scheduled_tasks.'
        'mariadb.reconcile_scheduler_capacity')
    def test_success_sets_gauges_from_reply(self, mock_reconcile):
        mock_reconcile.return_value = _capacity_reply(
            [_capacity_node(NODE_UUID_1),
             _capacity_node(NODE_UUID_2, limit_cpus=32, used_cpus=10,
                            expected_demand=7.5)])

        st.reconcile_scheduler_capacity()

        mock_reconcile.assert_called_once_with()
        self.assertEqual(
            16.0, _sample('scheduler_capacity_node_limit',
                          {'node': NODE_UUID_1, 'resource': 'cpus'}))
        self.assertEqual(
            8192.0, _sample('scheduler_capacity_node_used',
                            {'node': NODE_UUID_1, 'resource': 'memory_mb'}))
        self.assertEqual(
            7.5, _sample('scheduler_capacity_node_expected_demand',
                         {'node': NODE_UUID_2}))
        self.assertEqual(
            48.0, _sample('scheduler_capacity_cluster_total',
                          {'resource': 'cpus'}))
        self.assertEqual(
            0.0, _sample('scheduler_capacity_cluster_claimed',
                         {'resource': 'memory_mb'}))
        self.assertEqual(
            200.0, _sample('scheduler_capacity_cluster_unclaimed_used',
                           {'resource': 'disk_gb'}))
        self.assertIsNotNone(
            _sample('scheduler_capacity_reconcile_last_success_timestamp'))
        self.assertIsNotNone(
            _sample('scheduler_capacity_reconcile_last_duration_seconds'))

    @mock.patch(
        'shakenfist.daemons.cluster.scheduled_tasks.'
        'mariadb.reconcile_scheduler_capacity')
    def test_departed_node_label_sets_are_removed(self, mock_reconcile):
        mock_reconcile.side_effect = [
            _capacity_reply(
                [_capacity_node(NODE_UUID_1), _capacity_node(NODE_UUID_2)]),
            _capacity_reply([_capacity_node(NODE_UUID_1)], nodes_removed=1)
        ]

        st.reconcile_scheduler_capacity()
        self.assertIsNotNone(
            _sample('scheduler_capacity_node_limit',
                    {'node': NODE_UUID_2, 'resource': 'cpus'}))

        st.reconcile_scheduler_capacity()
        for resource in st.CAPACITY_RESOURCES:
            self.assertIsNone(
                _sample('scheduler_capacity_node_limit',
                        {'node': NODE_UUID_2, 'resource': resource}))
            self.assertIsNone(
                _sample('scheduler_capacity_node_used',
                        {'node': NODE_UUID_2, 'resource': resource}))
        self.assertIsNone(
            _sample('scheduler_capacity_node_expected_demand',
                    {'node': NODE_UUID_2}))

        # The surviving node's label sets are untouched.
        self.assertIsNotNone(
            _sample('scheduler_capacity_node_limit',
                    {'node': NODE_UUID_1, 'resource': 'cpus'}))
        self.assertEqual({NODE_UUID_1}, st._CAPACITY_EXPORTED_NODES)

    @mock.patch(
        'shakenfist.daemons.cluster.scheduled_tasks.'
        'mariadb.reconcile_scheduler_capacity', return_value=None)
    def test_failed_pass_increments_counter_without_raising(
            self, mock_reconcile):
        passes_before = _sample(
            'scheduler_capacity_reconcile_passes_total') or 0.0
        failures_before = _sample(
            'scheduler_capacity_reconcile_failures_total') or 0.0

        with mock.patch.object(st.LOG, 'with_fields') as mock_with_fields:
            # Must not raise.
            st.reconcile_scheduler_capacity()

        self.assertEqual(
            passes_before + 1,
            _sample('scheduler_capacity_reconcile_passes_total'))
        self.assertEqual(
            failures_before + 1,
            _sample('scheduler_capacity_reconcile_failures_total'))
        mock_with_fields.return_value.warning.assert_called_once()
        mock_with_fields.return_value.info.assert_not_called()

    @mock.patch(
        'shakenfist.daemons.cluster.scheduled_tasks.'
        'mariadb.reconcile_scheduler_capacity')
    def test_success_logs_exactly_one_info_summary(self, mock_reconcile):
        mock_reconcile.return_value = _capacity_reply(
            [_capacity_node(NODE_UUID_1)], nodes_added=1, claims_expired=2)

        with mock.patch.object(st.LOG, 'with_fields') as mock_with_fields:
            st.reconcile_scheduler_capacity()

        mock_with_fields.assert_called_once()
        fields = mock_with_fields.call_args[0][0]
        self.assertEqual(1, fields['nodes'])
        self.assertEqual(1, fields['nodes_added'])
        self.assertEqual(0, fields['nodes_removed'])
        self.assertEqual(2, fields['claims_expired'])
        self.assertIn('duration', fields)
        mock_with_fields.return_value.info.assert_called_once()
        mock_with_fields.return_value.warning.assert_not_called()

    @mock.patch(
        'shakenfist.daemons.cluster.scheduled_tasks.'
        'mariadb.reconcile_scheduler_capacity')
    def test_failed_pass_still_records_its_duration(self, mock_reconcile):
        # A slow-then-failing pass is exactly when an operator wants the
        # duration, so it must not be left reporting the last successful
        # pass's timing.
        mock_reconcile.return_value = _capacity_reply(
            [_capacity_node(NODE_UUID_1)])
        st.reconcile_scheduler_capacity()
        success_timestamp = _sample(
            'scheduler_capacity_reconcile_last_success_timestamp')

        mock_reconcile.return_value = None
        st.reconcile_scheduler_capacity()

        self.assertIsNotNone(
            _sample('scheduler_capacity_reconcile_last_duration_seconds'))
        # The success timestamp does not move on a failed pass, so
        # freshness alerting still fires.
        self.assertEqual(
            success_timestamp,
            _sample('scheduler_capacity_reconcile_last_success_timestamp'))

    @mock.patch(
        'shakenfist.daemons.cluster.scheduled_tasks.'
        'mariadb.reconcile_scheduler_capacity')
    def test_demotion_clears_the_capacity_gauges(self, mock_reconcile):
        # The cluster gauges describe singleton cluster state, so a
        # demoted node must stop publishing rather than contradict
        # whichever node takes the lock next.
        mock_reconcile.return_value = _capacity_reply(
            [_capacity_node(NODE_UUID_1), _capacity_node(NODE_UUID_2)])
        st.reconcile_scheduler_capacity()
        self.assertIsNotNone(
            _sample('scheduler_capacity_cluster_total', {'resource': 'cpus'}))

        st.clear_scheduler_capacity_metrics()

        for resource in st.CAPACITY_RESOURCES:
            self.assertIsNone(
                _sample('scheduler_capacity_cluster_total',
                        {'resource': resource}))
            self.assertIsNone(
                _sample('scheduler_capacity_cluster_claimed',
                        {'resource': resource}))
            self.assertIsNone(
                _sample('scheduler_capacity_cluster_unclaimed_used',
                        {'resource': resource}))
            for node_uuid in (NODE_UUID_1, NODE_UUID_2):
                self.assertIsNone(
                    _sample('scheduler_capacity_node_limit',
                            {'node': node_uuid, 'resource': resource}))
                self.assertIsNone(
                    _sample('scheduler_capacity_node_used',
                            {'node': node_uuid, 'resource': resource}))
        self.assertEqual(set(), st._CAPACITY_EXPORTED_NODES)

        # The counters are monotonic and aggregate correctly across
        # nodes, so they are deliberately left alone.
        self.assertIsNotNone(
            _sample('scheduler_capacity_reconcile_passes_total'))

    def test_clearing_when_nothing_was_exported_is_safe(self):
        # Called on every loop exit, including from a node which was
        # never elected or has just restarted.
        st.clear_scheduler_capacity_metrics()
        st.clear_scheduler_capacity_metrics()
