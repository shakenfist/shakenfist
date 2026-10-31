# Copyright 2019 Michael Still and contributors
import collections
import time
from unittest import mock

from prometheus_client import REGISTRY
from sqlalchemy.exc import OperationalError

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FINAL_OBJECT_STATES
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.daemons.cluster import scheduled_tasks as st
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.exceptions import HardDeleteBatchUnavailable
from shakenfist.exceptions import InvalidStateException
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.namespace_key_attributes import NamespaceKeyAttributesData
from shakenfist.namespace_key import NamespaceKey
from shakenfist.protos import database_pb2
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


def _reset_deleted_object_sweep_state():
    """Empty the deleted-object sweep's process-global work.

    The work, the cursors, the empty-type backoff and the rotation's
    position all outlive a pass by design, so a test which leaves any of
    them behind changes what a later test's pass reads and drains.
    """
    st._DELETED_OBJECT_WORK.clear()
    st._DELETED_OBJECT_CURSOR.clear()
    st._DELETED_OBJECT_RECHECK_AT.clear()
    st._DELETED_OBJECT_NEXT_TYPE = 0


def _hold_deleted_object_work(objtype, *uuids):
    """Give the deleted-object sweep work for a type, as a fill would."""
    st._DELETED_OBJECT_WORK.setdefault(
        objtype, collections.deque()).extend(uuids)


def _held_deleted_object_work():
    """The deleted-object sweep's work, leaving out types with none."""
    return {objtype: list(work)
            for objtype, work in st._DELETED_OBJECT_WORK.items() if work}


# The fill's deadline, for tests about something other than the budget.
NO_DEADLINE = float('inf')


def _page(uuids, more_available=False):
    return mariadb.ObjectsByStatePage(
        uuids=list(uuids), more_available=more_available)


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

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.mariadb.prune_events',
                side_effect=DatabaseUnavailable(
                    'gRPC PruneEvents failed after 300.1s of its 300s '
                    'deadline'))
    def test_prune_events_timeout_is_a_failure_not_a_noop(self, mock_prune):
        """A timed-out prune must not produce the success line (issue 3849).

        mariadb.prune_events() raises DatabaseUnavailable when the RPC
        deadline expires; the shim must log the failure branch, with
        the cause, and never the "removed N rows" success line.
        """
        with mock.patch.object(st.LOG, 'info') as mock_log_info, \
                mock.patch.object(st.LOG, 'warning') as mock_log_warn:
            st.prune_events()

        mock_log_info.assert_not_called()
        mock_log_warn.assert_called_once()
        warn_msg = str(mock_log_warn.call_args[0][0])
        self.assertIn('300s deadline', warn_msg)
        self.assertIn('next daily sweep', warn_msg)


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
        # On a real key .state is an uncached round trip to the
        # database every single time it is read (baseobject.py's
        # _state_read), which is what made the expiry sweep expensive
        # (issue 3814). Count reads so a test can assert none happen.
        self.state_reads = 0

    @property
    def state(self):
        self.state_reads += 1
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


class FakeDeletedObjectStore:
    """Final-state uuids per object type, served the way sf-database does.

    Both the unpaged read and the keyset page are answered from the same
    store, in ascending uuid order, and a hard delete removes the uuid so
    a later read does not return it again.
    """

    def __init__(self, counts):
        self.uuids = {
            objtype: [f'{objtype}-{i:08d}' for i in range(count)]
            for objtype, count in counts.items()}
        self.initial = {objtype: list(uuids)
                        for objtype, uuids in self.uuids.items()}

    def by_state(self, objtype, states, updated_before=None):
        return list(self.uuids.get(str(objtype), []))

    def page(self, objtype, states, page_size, after_uuid='',
             updated_before=None):
        after = [u for u in self.uuids.get(str(objtype), []) if u > after_uuid]
        return mariadb.ObjectsByStatePage(
            uuids=after[:page_size], more_available=len(after) > page_size)


class FakeClock:
    def __init__(self, now=1000000000.0):
        self.now = now

    def __call__(self):
        return self.now


def _attrs(expiry):
    return NamespaceKeyAttributesData(
        uuid=KEY_UUID_1, key='aGFzaA==', nonce='noncenonce', expiry=expiry)


class ReapExpiredNamespaceKeysTestCase(base.ShakenFistTestCase):
    """The cluster sweep which soft deletes long-expired keys.

    Expiry is enforced check-at-use elsewhere; these tests are only
    about the tidy up, so they care about which keys get their state
    moved and which are left alone.
    """

    # Distinct from None, which is what a failed state query returns.
    DERIVE_ACTIONABLE = object()

    def _sweep(self, pairs, grace=3600, now=10000.0,
               actionable=DERIVE_ACTIONABLE):
        """Run the sweep over one namespace holding ``pairs``.

        The sweep asks the database once for the keys in an actionable
        state rather than reading .state per key (issue 3814), so the
        fake stands in for that query. By default it answers what the
        real query would for these keys: the ones whose state is one
        the sweep can move. Pass ``actionable`` to answer something
        else, including None for a failed read.
        """
        if actionable is self.DERIVE_ACTIONABLE:
            actionable = [
                str(key.uuid) for key, _ in pairs
                if key.state.value in NamespaceKey.ACTIVE_STATES]

        # Deriving the answer above read .state itself. Zero the
        # counters so what a test sees is what the sweep did.
        for key, _ in pairs:
            key.state_reads = 0

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
                    'shakenfist.daemons.cluster.scheduled_tasks.mariadb.'
                    'get_objects_by_state',
                    return_value=actionable) as mock_states, \
                mock.patch(
                    'shakenfist.daemons.cluster.scheduled_tasks.time.time',
                    return_value=now):
            st.reap_expired_namespace_keys()
        self._last_state_query = mock_states
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

    def test_asks_for_key_states_once_however_many_keys(self):
        # The point of issue 3814. This sweep used to read .state per
        # expired key, which is one uncached round trip each: on sfcbr
        # that was ~15,200 GetObjectState calls in a single fifteen
        # minute burst, growing with the backlog rather than with the
        # work. The cost must now be one query whatever the key count.
        #
        # The assertion is on the count, not on the absence of a
        # particular call, so re-introducing a per-key read fails here
        # rather than passing quietly.
        pairs = [(FakeNamespaceKey('stale-%d' % i), _attrs(1000.0))
                 for i in range(50)]
        self._sweep(pairs)

        self.assertEqual(1, self._last_state_query.call_count)
        self.assertEqual(
            (ObjectType.NAMESPACE_KEY, sorted(NamespaceKey.ACTIVE_STATES)),
            self._last_state_query.call_args[0])

        # And -- the assertion that actually holds the fix in place --
        # it read no key's state individually. One bulk query plus 50
        # point reads would satisfy the count above but is the defect.
        self.assertEqual(
            0, sum(key.state_reads for key, _ in pairs),
            'the sweep read .state per key; each read is a database '
            'round trip (issue 3814)')

        # And it still did the work. Read the state values last, since
        # reading them is itself counted above.
        for key, _ in pairs:
            self.assertEqual(dbo.STATE_DELETED, key.state.value)

    def test_skips_a_zombie_key_with_no_state_row_via_the_bulk_query(self):
        # A key with no state row cannot be soft deleted -- the state
        # machine has no None to deleted transition -- so delete()
        # would raise on it every sweep forever. It is absent from the
        # actionable set by construction, because that set comes from
        # object_states. reconcile_orphaned_objects repairs these.
        key = FakeNamespaceKey('zombie', state=None)
        self._sweep([(key, _attrs(1000.0))])

        self.assertEqual([], key.events)
        self.assertIsNone(key.state.value)

    def test_skips_the_pass_when_the_state_query_fails(self):
        # None means the query failed, which is deliberately distinct
        # from [] meaning "no active keys". Treating a failed read as an
        # empty set would be harmless here (nothing is actionable, so
        # nothing is deleted) but the sweep must not log or behave as
        # though it completed a clean pass over the keys.
        key = FakeNamespaceKey('stale')
        mock_keys = self._sweep([(key, _attrs(1000.0))], actionable=None)

        # Nothing was even listed: the sweep gave up before walking
        # namespaces.
        self.assertEqual(0, mock_keys.call_count)
        self.assertEqual(dbo.STATE_CREATED, key.state.value)

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
        _reset_sweep_failure_state()
        _reset_deleted_object_sweep_state()
        self.addCleanup(_reset_deleted_object_sweep_state)

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.hard_delete_objects')
    def test_hard_deletes_a_soft_deleted_key_after_cleaner_delay(
            self, mock_batch, mock_event):
        # Keys are hard deleted in batches, and the database deletes only
        # those whose state is still final and older than updated_before.
        mock_batch.return_value = [KEY_UUID_1]
        _hold_deleted_object_work('namespace_key', KEY_UUID_1)

        before = time.time()
        st._drain_deleted_object_work(execution_limit=5)
        after = time.time()

        mock_batch.assert_called_once()
        args, kwargs = mock_batch.call_args
        self.assertEqual((ObjectType.NAMESPACE_KEY, [KEY_UUID_1]), args)
        self.assertEqual(FINAL_OBJECT_STATES, kwargs['state_values'])
        self.assertLessEqual(before - config.CLEANER_DELAY,
                             kwargs['updated_before'])
        self.assertLessEqual(kwargs['updated_before'],
                             after - config.CLEANER_DELAY)
        mock_event.assert_called_once_with(
            EVENT_TYPE_AUDIT, 'namespace_key', KEY_UUID_1,
            'hard deleted object', suppress_event_logging=True)

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.hard_delete_objects', return_value=[])
    def test_leaves_a_recently_deleted_key_alone(self, mock_batch, mock_event):
        # A key deleted as the pass runs is younger than the batch's
        # updated_before, so the database leaves it and says so.
        deleted_at = time.time()
        _hold_deleted_object_work('namespace_key', KEY_UUID_1)

        outcomes = collections.Counter()
        st._drain_deleted_object_work(execution_limit=5, outcomes=outcomes)

        self.assertLess(mock_batch.call_args.kwargs['updated_before'],
                        deleted_at)
        self.assertEqual({'skipped': 1}, dict(outcomes))
        mock_event.assert_not_called()

    def test_the_reaper_enumerates_namespace_keys_at_all(self):
        # The sweep only ever soft deletes. If NamespaceKey were not
        # registered in OBJECT_NAMES_TO_CLASSES then nothing would ever
        # hard delete the rows, and the sweep would quietly leak them.
        self.assertIn('namespace_key', OBJECT_NAMES_TO_CLASSES)

        with mock.patch(
                'shakenfist.daemons.cluster.scheduled_tasks.'
                'mariadb.get_objects_by_state_page',
                side_effect=lambda objtype, states, page_size, after_uuid='',
                updated_before=None: _page(
                    [KEY_UUID_1]
                    if objtype == ObjectType.NAMESPACE_KEY else [])):
            st._fill_deleted_object_work(time.time(), NO_DEADLINE)

        self.assertEqual({'namespace_key': [KEY_UUID_1]},
                         _held_deleted_object_work())


class PerDeletedObjectQueueTestCase(base.ShakenFistTestCase):
    """The deleted-object sweep fills with uuids per type and only
    hydrates at processing time, one object per try/except (issue 3533).

    The processing tests use instances, which are still hard deleted one
    object at a time.
    """

    def setUp(self):
        super().setUp()
        _reset_sweep_failure_state()
        _reset_deleted_object_sweep_state()
        self.addCleanup(_reset_deleted_object_sweep_state)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_fill_holds_uuids_without_hydration(
            self, mock_page, mock_get_class):
        def by_page(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            self.assertIsNotNone(updated_before)
            self.assertTrue(updated_before < time.time())
            self.assertEqual(st.DELETED_OBJECT_PAGE_SIZE, page_size)
            self.assertEqual('', after_uuid)
            if objtype == ObjectType.NETWORK:
                return _page([BLOB_UUID_1, BLOB_UUID_2])
            return _page([])
        mock_page.side_effect = by_page

        self.assertTrue(st._fill_deleted_object_work(time.time(), NO_DEADLINE))

        # Hydration must not happen at fill time -- with a large backlog
        # it blew the fill budget and the watchdog window.
        mock_get_class.assert_not_called()

        self.assertEqual({'network': [BLOB_UUID_1, BLOB_UUID_2]},
                         _held_deleted_object_work())

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_fill_failure_is_visible_not_empty(self, mock_page):
        # get_objects_by_state_page returns None when the read failed
        # (e.g. a RESOURCE_EXHAUSTED gRPC reply), distinct from an empty
        # page for no matches. Collapsing the two silently turned the GC
        # off for an object type forever (#3638): the failed type must be
        # skipped visibly while other types still fill.
        def by_page(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            if objtype == ObjectType.NETWORK:
                return None
            if objtype == ObjectType.BLOB:
                return _page([BLOB_UUID_1])
            return _page([])
        mock_page.side_effect = by_page

        self.assertTrue(st._fill_deleted_object_work(time.time(), NO_DEADLINE))

        self.assertEqual({'blob': [BLOB_UUID_1]}, _held_deleted_object_work())
        # Every object type was still read: a per-reply failure costs
        # one type, not the pass.
        self.assertEqual(len(OBJECT_NAMES_TO_CLASSES), mock_page.call_count)
        self.assertEqual(
            {('per_deleted_object', 'network'): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))
        # A failed read is not an empty one, so it is not backed off.
        self.assertNotIn('network', st._DELETED_OBJECT_RECHECK_AT)

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_unavailable_database_stops_the_sweep_after_one_budget(
            self, mock_page):
        """A tier outage must cost one retry budget, not 29 of them.

        DatabaseUnavailable is only raised once _grpc_call has spent its
        whole budget, which is split by cost: GRPC_UNAVAILABLE_RETRIES
        fast-failing attempts for the common shape (a dead tier, or the
        servicer's own UNAVAILABLE for a transient MariaDB error), and up
        to GRPC_RETRIES full GRPC_TIMEOUT deadlines for the slow one,
        plus escalating sleeps in both. There are 29 object types in
        this loop and
        _run_due_scheduled_jobs() pets the watchdog only *between* jobs,
        so continuing past a tier-wide failure multiplies this job's
        worst case by 29 and takes it past sf-cluster's WatchdogSec.
        That SIGABRTs the elected maintainer and costs a lock failover,
        where previously the first DatabaseUnavailable merely ended the
        pass. This function has blown the watchdog window once already
        (issue 3533).

        A dead tier does not become reachable on the 29th ask, so the
        bound is the property worth pinning: one read attempt, then stop.
        """
        mock_page.side_effect = DatabaseUnavailable('tier is down')

        self.assertFalse(st._fill_deleted_object_work(time.time(), NO_DEADLINE))

        self.assertEqual(1, mock_page.call_count)
        self.assertEqual({}, _held_deleted_object_work())
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

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_unavailable_database_keeps_work_read_before_it(
            self, mock_page):
        # Stopping the loop must not discard the types already read.
        # 'blob' comes before 'network' in OBJECT_NAMES_TO_CLASSES, so
        # its work is still held even though the tier goes away part way
        # through.
        def by_page(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            if objtype == ObjectType.NETWORK:
                raise DatabaseUnavailable('tier is down')
            if objtype == ObjectType.BLOB:
                return _page([BLOB_UUID_1])
            return _page([])
        mock_page.side_effect = by_page

        self.assertFalse(st._fill_deleted_object_work(time.time(), NO_DEADLINE))

        self.assertEqual({'blob': [BLOB_UUID_1]}, _held_deleted_object_work())
        types = list(OBJECT_NAMES_TO_CLASSES)
        self.assertEqual(types.index('network') + 1, mock_page.call_count)
        self.assertEqual(
            {('per_deleted_object', 'network'): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            1, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_a_slow_type_cannot_starve_the_types_behind_it(
            self, mock_page):
        """Stopping the pass must not stop it in the same place forever.

        DatabaseUnavailable is not only raised for a dead tier: an
        exhausted DEADLINE_EXCEEDED budget raises it too, which one
        large, slow or lock-contended query can produce for a single
        object type while the tier is otherwise healthy. With a fixed
        starting point, stopping would mean every type after that one is
        never swept again -- and the backlog that then accumulates makes
        the slow type slower still. That is the #3638 ratchet rebuilt
        one level up, so each pass resumes after wherever the last one
        stopped.
        """
        types = list(OBJECT_NAMES_TO_CLASSES)
        slow = types[0]

        def by_page(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            if str(objtype) == slow:
                raise DatabaseUnavailable('this one query is too slow')
            return _page([])
        mock_page.side_effect = by_page

        # Pass one stops on the very first type, having read only it.
        now = time.time()
        st._fill_deleted_object_work(now, NO_DEADLINE)
        self.assertEqual(1, mock_page.call_count)

        # Pass two starts after it and reaches every other type, rather
        # than dying on the same query again.
        mock_page.reset_mock()
        st._fill_deleted_object_work(now, NO_DEADLINE)

        # Every other type is reached, and the slow one is retried
        # last: one wasted budget per pass, and nothing behind it
        # starves. The resume point then holds steady, so this is the
        # steady state rather than a one-off recovery.
        read = [str(c.args[0]) for c in mock_page.call_args_list]
        self.assertEqual(types[1:] + [slow], read)

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page',
                return_value=mariadb.ObjectsByStatePage(
                    uuids=[], more_available=False))
    def test_a_completed_pass_starts_from_the_top_again(self, mock_page):
        # Only a pass which stopped early leaves a resume offset behind.
        types = list(OBJECT_NAMES_TO_CLASSES)
        st._SWEEP_RESUME_AFTER['per_deleted_object'] = types[5]

        now = time.time()
        st._fill_deleted_object_work(now, NO_DEADLINE)
        self.assertNotIn('per_deleted_object', st._SWEEP_RESUME_AFTER)

        # Every type came back empty, so wait out their backoff.
        mock_page.reset_mock()
        st._fill_deleted_object_work(now + st.DELETED_OBJECT_WALK_RECHECK + 1, NO_DEADLINE)
        self.assertEqual(
            types, [str(c.args[0]) for c in mock_page.call_args_list])

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_demotion_stops_publishing_the_streak(self, mock_page):
        # Only the elected node runs these sweeps, so a demoted node
        # holding a non-zero streak keeps a "streak > 0" alert firing
        # against work it is no longer doing. Nothing on that node will
        # run the reset branch again, so the clear has to be explicit.
        mock_page.side_effect = (
            lambda objtype, states, page_size, after_uuid='',
            updated_before=None:
            None if objtype == ObjectType.NETWORK else _page([]))
        st._fill_deleted_object_work(time.time(), NO_DEADLINE)
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

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_fill_failure_streak_counts_and_resets(self, mock_page):
        # The streak counts consecutive failed passes per object type,
        # and a successful read clears it -- the alertable signal is
        # "still failing", not "failed once ever" (#3638).
        def failing(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            return None if objtype == ObjectType.NETWORK else _page([])
        mock_page.side_effect = failing
        now = time.time()
        st._fill_deleted_object_work(now, NO_DEADLINE)
        st._fill_deleted_object_work(now, NO_DEADLINE)
        self.assertEqual(
            {('per_deleted_object', 'network'): 2}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            2, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))

        mock_page.side_effect = (
            lambda objtype, states, page_size, after_uuid='',
            updated_before=None: _page([]))
        st._fill_deleted_object_work(now, NO_DEADLINE)
        self.assertEqual({}, st._SWEEP_FAILURE_STREAK)
        self.assertEqual(
            0, REGISTRY.get_sample_value(
                'cluster_sweep_work_list_failure_streak',
                {'sweep': 'per_deleted_object', 'object_type': 'network'}))

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_process_hard_deletes_old_final_objects(self, mock_get_class):
        obj = FakeFinalStateObject()
        mock_get_class.return_value.from_db.return_value = obj
        _hold_deleted_object_work('instance', BLOB_UUID_1)

        processed = st._drain_deleted_object_work(execution_limit=10)

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

        _hold_deleted_object_work('instance', BLOB_UUID_1, BLOB_UUID_2)

        processed = st._drain_deleted_object_work(execution_limit=10)

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

        _hold_deleted_object_work('instance', BLOB_UUID_1, BLOB_UUID_2)

        processed = st._drain_deleted_object_work(execution_limit=10)

        self.assertEqual(2, processed)
        self.assertFalse(resurrected.hard_deleted)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_process_respects_delay(self, mock_get_class):
        # An object still inside its post-deletion grace period is left
        # alone (fill filters age in SQL, but held entries can be old
        # and objects can be re-deleted in the interim).
        young = FakeFinalStateObject(age=1.0)
        mock_get_class.return_value.from_db.return_value = young
        _hold_deleted_object_work('instance', BLOB_UUID_1)

        processed = st._drain_deleted_object_work(execution_limit=10)

        self.assertEqual(1, processed)
        self.assertFalse(young.hard_deleted)


UUID_GONE = 'a0000000-0000-4000-8000-000000000001'
UUID_NOT_FINAL = 'a0000000-0000-4000-8000-000000000002'
UUID_TOO_YOUNG = 'a0000000-0000-4000-8000-000000000003'
UUID_HARD_DELETED = 'a0000000-0000-4000-8000-000000000004'
UUID_FAILED = 'a0000000-0000-4000-8000-000000000005'


class DeletedObjectSweepMixedQueueTestCase(base.ShakenFistTestCase):
    """One work entry for each way an object hard deleted on its own can end.

    Instances are still hard deleted one object at a time, so they take
    every outcome but 'skipped', which only a batch can give.
    """

    def setUp(self):
        super().setUp()
        _reset_sweep_failure_state()
        _reset_deleted_object_sweep_state()
        self.addCleanup(_reset_deleted_object_sweep_state)

    def _fixtures(self, calls):
        """Return a from_db side effect which records its calls in order."""
        def make(label, **kwargs):
            obj = FakeFinalStateObject(**kwargs)
            real = obj.hard_delete

            def hard_delete():
                calls.append(('hard_delete', label))
                real()
            obj.hard_delete = hard_delete
            return obj

        objs = {
            UUID_NOT_FINAL: make(UUID_NOT_FINAL, state_value='created'),
            UUID_TOO_YOUNG: make(UUID_TOO_YOUNG, age=1.0),
            UUID_HARD_DELETED: make(UUID_HARD_DELETED),
        }

        def from_db(obj_uuid, suppress_failure_audit=False):
            calls.append(('from_db', obj_uuid, suppress_failure_audit))
            if obj_uuid == UUID_GONE:
                return None
            if obj_uuid == UUID_FAILED:
                raise RuntimeError('database exploded')
            return objs[obj_uuid]
        return from_db, objs

    def _queue_mixed(self):
        _hold_deleted_object_work(
            'instance', UUID_GONE, UUID_NOT_FINAL, UUID_TOO_YOUNG,
            UUID_HARD_DELETED, UUID_FAILED)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_call_order_is_unchanged(self, mock_get_class):
        # Instrumenting the sweep must not change what it does: the same
        # calls, in the same order, with the same arguments (D6).
        calls = []
        from_db, objs = self._fixtures(calls)
        mock_get_class.return_value.from_db.side_effect = from_db
        self._queue_mixed()

        processed = st._drain_deleted_object_work(execution_limit=5)

        self.assertEqual(5, processed)
        self.assertEqual(
            [('from_db', UUID_GONE, True),
             ('from_db', UUID_NOT_FINAL, True),
             ('from_db', UUID_TOO_YOUNG, True),
             ('from_db', UUID_HARD_DELETED, True),
             ('hard_delete', UUID_HARD_DELETED),
             ('from_db', UUID_FAILED, True)],
            calls)
        self.assertEqual(
            [('instance',)] * 5,
            [(c.args[0],) for c in mock_get_class.call_args_list])

    def _sample_counts(self):
        return {
            outcome: REGISTRY.get_sample_value(
                'cluster_deleted_object_seconds_count',
                {'object_type': 'instance', 'outcome': outcome}) or 0
            for outcome in st.DELETED_OBJECT_OUTCOMES}

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_one_histogram_sample_per_outcome(self, mock_get_class):
        calls = []
        from_db, objs = self._fixtures(calls)
        mock_get_class.return_value.from_db.side_effect = from_db
        self._queue_mixed()
        before = self._sample_counts()

        outcomes = collections.Counter()
        processed = st._drain_deleted_object_work(
            execution_limit=5, outcomes=outcomes)

        after = self._sample_counts()
        self.assertEqual(5, processed)
        expected = {outcome: 0 if outcome == 'skipped' else 1
                    for outcome in st.DELETED_OBJECT_OUTCOMES}
        self.assertEqual(expected, {o: after[o] - before[o] for o in after})
        self.assertEqual({o: n for o, n in expected.items() if n},
                         dict(outcomes))
        self.assertEqual({}, _held_deleted_object_work())

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_outcomes_parameter_is_optional(self, mock_get_class):
        calls = []
        from_db, objs = self._fixtures(calls)
        mock_get_class.return_value.from_db.side_effect = from_db
        self._queue_mixed()

        self.assertEqual(
            5, st._drain_deleted_object_work(execution_limit=5))

    def _pass_fields(self, mock_log):
        """The structured fields of the pass's summary line."""
        lines = [c.args[0] for c in mock_log.with_fields.call_args_list
                 if 'total' in c.args[0]]
        self.assertEqual(1, len(lines))
        return lines[0]

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.LOG')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_pass_summary_tracks_fill_and_drain(
            self, mock_page, mock_get_class, mock_log):
        store = FakeDeletedObjectStore({'namespace': 2, 'instance': 1})
        mock_page.side_effect = store.page
        clock = FakeClock()
        spend = [10.0]

        def from_db(obj_uuid, suppress_failure_audit=False):
            # A concurrent cleaner got there first.
            for uuids in store.uuids.values():
                if obj_uuid in uuids:
                    uuids.remove(obj_uuid)
            clock.now += spend[0]
            return None
        mock_get_class.return_value.from_db.side_effect = from_db

        # The first entry spends more than the whole budget, so the pass
        # takes exactly one.
        with mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                        'time') as fake_time, \
                mock.patch.object(config, 'DELETED_OBJECT_SWEEP_SECONDS', 5):
            fake_time.time.side_effect = clock
            fake_time.monotonic.side_effect = clock
            st.per_deleted_object_checks()

            fields = self._pass_fields(mock_log)
            self.assertEqual(1, fields['total'])
            self.assertEqual(1, fields['gone'])
            self.assertEqual(3, fields['filled'])
            self.assertEqual(2, fields['remaining'])

            # The next pass reads nothing for the type which still has
            # work, and drains the rest.
            mock_log.reset_mock()
            spend[0] = 0.0
            st.per_deleted_object_checks()

        fields = self._pass_fields(mock_log)
        self.assertEqual(2, fields['total'])
        self.assertEqual(0, fields['filled'])
        self.assertEqual(0, fields['remaining'])
        self.assertEqual({}, _held_deleted_object_work())

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.LOG')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_fill_which_spends_the_budget_leaves_the_work(
            self, mock_page, mock_get_class, mock_log):
        clock = FakeClock()

        def by_page(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            # Reading the work takes a minute.
            if objtype == ObjectType.NETWORK:
                clock.now += 60.0
                return _page([BLOB_UUID_1, BLOB_UUID_2])
            return _page([])
        mock_page.side_effect = by_page

        with mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                        'time') as fake_time:
            fake_time.time.side_effect = clock
            fake_time.monotonic.side_effect = clock
            st.per_deleted_object_checks()

        mock_get_class.assert_not_called()
        self.assertEqual({'network': [BLOB_UUID_1, BLOB_UUID_2]},
                         _held_deleted_object_work())
        fields = self._pass_fields(mock_log)
        self.assertEqual(0, fields['total'])
        self.assertEqual(2, fields['filled'])
        self.assertEqual(2, fields['remaining'])
        # The fill stopped reading once it had spent the pass's budget, and
        # the next pass starts with the types it did not reach.
        types = list(OBJECT_NAMES_TO_CLASSES)
        self.assertEqual(
            types[(types.index('network') + 1) % len(types)],
            st._SWEEP_RESUME_AFTER['per_deleted_object'])

    def test_demotion_keeps_the_work(self):
        _hold_deleted_object_work('network', BLOB_UUID_1)
        st._DELETED_OBJECT_CURSOR['network'] = BLOB_UUID_1
        st._DELETED_OBJECT_NEXT_TYPE = 5

        st.clear_deleted_object_metrics()

        self.assertEqual({'network': [BLOB_UUID_1]},
                         _held_deleted_object_work())
        self.assertEqual(BLOB_UUID_1, st._DELETED_OBJECT_CURSOR['network'])
        self.assertEqual(5, st._DELETED_OBJECT_NEXT_TYPE)

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.mariadb.get_objects_by_state_page',
                return_value=mariadb.ObjectsByStatePage(
                    uuids=[], more_available=False))
    def test_next_pass_after_demotion_carries_on(
            self, mock_page, mock_get_class):
        _hold_deleted_object_work('instance', BLOB_UUID_1)
        st.clear_deleted_object_metrics()
        mock_get_class.return_value.from_db.return_value = None

        st.per_deleted_object_checks()

        mock_get_class.return_value.from_db.assert_called_once_with(
            BLOB_UUID_1, suppress_failure_audit=True)
        self.assertEqual({}, _held_deleted_object_work())


class DeletedObjectSweepFairnessTestCase(base.ShakenFistTestCase):
    """A large backlog in one type must not starve the others.

    This drives the scheduled entry point rather than any one helper, so
    it describes the sweep's behaviour whatever its internal structure.
    """

    def setUp(self):
        super().setUp()
        _reset_sweep_failure_state()
        # Only reset the per-type work where it exists, so that this test
        # still runs against the single-queue implementation it replaced,
        # which is how it was shown to fail there.
        if hasattr(st, '_DELETED_OBJECT_WORK'):
            _reset_deleted_object_sweep_state()
            self.addCleanup(_reset_deleted_object_sweep_state)

    def test_a_small_type_is_served_behind_a_large_one(self):
        # Both types are hard deleted one object at a time, so every entry
        # costs the same and the budget, not the entry cap, ends the pass.
        first, last = 'instance', 'node'
        store = FakeDeletedObjectStore({first: 5000, last: 3})
        clock = FakeClock()
        deleted = []

        def object_class(objtype):
            class FakeClass:
                @staticmethod
                def from_db(obj_uuid, suppress_failure_audit=False):
                    obj = mock.MagicMock()
                    obj.state.value = dbo.STATE_DELETED
                    obj.state.update_time = 0.0

                    def hard_delete():
                        deleted.append((objtype, obj_uuid))
                        store.uuids[objtype].remove(obj_uuid)
                        clock.now += 0.1
                    obj.hard_delete = hard_delete
                    return obj
            return FakeClass

        # A copy of the configuration, rather than a patched attribute, so
        # this test also runs against an implementation which predates the
        # budget option.
        budgeted = config.model_copy(
            update={'DELETED_OBJECT_SWEEP_SECONDS': 5})

        with mock.patch('shakenfist.mariadb.get_objects_by_state',
                        side_effect=store.by_state), \
                mock.patch('shakenfist.mariadb.get_objects_by_state_page',
                           side_effect=store.page, create=True), \
                mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                           'get_object_class', side_effect=object_class), \
                mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                           'time') as fake_time, \
                mock.patch.object(st, 'config', budgeted):
            fake_time.time.side_effect = clock
            fake_time.monotonic.side_effect = clock

            st.per_deleted_object_checks()

        # One pass reaches every object of the small type, even though the
        # large one could have used the whole budget by itself.
        self.assertEqual(
            store.initial[last],
            sorted(u for t, u in deleted if t == last))
        # And the pass was cut short by its budget, so the large type was
        # not simply drained first.
        self.assertAlmostEqual(50, len(deleted), delta=1)
        self.assertNotEqual([], store.uuids[first])


class DeletedObjectSweepPagedWorkTestCase(base.ShakenFistTestCase):
    """How the sweep chooses its next entry and when it reads more."""

    def setUp(self):
        super().setUp()
        _reset_sweep_failure_state()
        _reset_deleted_object_sweep_state()
        self.addCleanup(_reset_deleted_object_sweep_state)

    def test_rotation_survives_a_budget_cut(self):
        # Each entry takes a second against a budget of one and a half, so
        # with a turn of one entry every drain takes two entries. If each
        # pass started from the top of the order, the first two types would
        # get every entry until they ran out.
        served = ['instance', 'namespace', 'namespace_claim', 'node']
        for objtype in served:
            _hold_deleted_object_work(objtype, f'{objtype}-1', f'{objtype}-2')
        clock = FakeClock()
        taken = []

        def object_class(objtype):
            cls = mock.MagicMock()

            def from_db(obj_uuid, suppress_failure_audit=False):
                taken.append(obj_uuid)
                clock.now += 1.0
                return None
            cls.from_db.side_effect = from_db
            return cls

        with mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                        'get_object_class', side_effect=object_class), \
                mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                           'time') as fake_time, \
                mock.patch.object(config, 'DELETED_OBJECT_BATCH_SIZE', 1):
            fake_time.time.side_effect = clock
            fake_time.monotonic.side_effect = clock

            passes = []
            for _ in range(4):
                taken.clear()
                st._drain_deleted_object_work(1.5)
                passes.append(list(taken))

        self.assertEqual(
            [[f'{served[0]}-1', f'{served[1]}-1'],
             [f'{served[2]}-1', f'{served[3]}-1'],
             [f'{served[0]}-2', f'{served[1]}-2'],
             [f'{served[2]}-2', f'{served[3]}-2']],
            passes)

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_one_type_refills_while_others_have_work(self, mock_page):
        _hold_deleted_object_work('network', BLOB_UUID_1)
        mock_page.side_effect = (
            lambda objtype, states, page_size, after_uuid='',
            updated_before=None: _page(
                [BLOB_UUID_2] if objtype == ObjectType.BLOB else []))

        self.assertTrue(st._fill_deleted_object_work(time.time(), NO_DEADLINE))

        read = [str(c.args[0]) for c in mock_page.call_args_list]
        self.assertNotIn('network', read)
        self.assertIn('blob', read)
        self.assertEqual(len(OBJECT_NAMES_TO_CLASSES) - 1, len(read))
        self.assertEqual(
            {'network': [BLOB_UUID_1], 'blob': [BLOB_UUID_2]},
            _held_deleted_object_work())

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_cursor_advances_then_resets(self, mock_page):
        pages = {
            '': _page(['uuid-1', 'uuid-2'], more_available=True),
            'uuid-2': _page(['uuid-3']),
        }

        def by_page(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            if objtype == ObjectType.NETWORK:
                return pages[after_uuid]
            return _page([])
        mock_page.side_effect = by_page
        now = time.time()

        # A page which stopped early moves the cursor to its last uuid.
        st._fill_deleted_object_work(now, NO_DEADLINE)
        self.assertEqual('uuid-2', st._DELETED_OBJECT_CURSOR['network'])
        self.assertEqual({'network': ['uuid-1', 'uuid-2']},
                         _held_deleted_object_work())

        # The next page continues the walk, and finishing it resets the
        # cursor so the next walk reaches anything which arrived behind it.
        st._DELETED_OBJECT_WORK['network'].clear()
        st._fill_deleted_object_work(now, NO_DEADLINE)
        self.assertEqual('', st._DELETED_OBJECT_CURSOR['network'])
        self.assertEqual({'network': ['uuid-3']}, _held_deleted_object_work())

        st._DELETED_OBJECT_WORK['network'].clear()
        st._fill_deleted_object_work(
            now + st.DELETED_OBJECT_WALK_RECHECK + 1, NO_DEADLINE)
        self.assertEqual(
            ['', 'uuid-2', ''],
            [c.kwargs['after_uuid'] for c in mock_page.call_args_list
             if c.args[0] == ObjectType.NETWORK])

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_completed_walk_backs_off(self, mock_page):
        mock_page.side_effect = (
            lambda objtype, states, page_size, after_uuid='',
            updated_before=None: _page(
                [BLOB_UUID_1] if objtype == ObjectType.BLOB else []))
        types = list(OBJECT_NAMES_TO_CLASSES)
        now = 1000000.0

        st._fill_deleted_object_work(now, NO_DEADLINE)
        self.assertEqual(len(types), mock_page.call_count)
        # An empty page and a page which finished its walk are both a
        # completed walk.
        self.assertEqual(now + st.DELETED_OBJECT_WALK_RECHECK,
                         st._DELETED_OBJECT_RECHECK_AT['network'])
        self.assertEqual(now + st.DELETED_OBJECT_WALK_RECHECK,
                         st._DELETED_OBJECT_RECHECK_AT['blob'])

        # Until the recheck is due nothing is read again, even once the
        # type which found work has run out.
        st._DELETED_OBJECT_WORK['blob'].clear()
        mock_page.reset_mock()
        st._fill_deleted_object_work(
            now + st.DELETED_OBJECT_WALK_RECHECK - 1, NO_DEADLINE)
        mock_page.assert_not_called()

        st._DELETED_OBJECT_WORK['blob'].clear()
        mock_page.reset_mock()
        st._fill_deleted_object_work(now + st.DELETED_OBJECT_WALK_RECHECK + 1, NO_DEADLINE)
        self.assertEqual(len(types), mock_page.call_count)

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_page_which_stopped_early_is_not_backed_off(self, mock_page):
        mock_page.side_effect = (
            lambda objtype, states, page_size, after_uuid='',
            updated_before=None: _page(
                ['uuid-1'], more_available=objtype == ObjectType.NETWORK))
        now = 1000000.0

        st._fill_deleted_object_work(now, NO_DEADLINE)
        self.assertNotIn('network', st._DELETED_OBJECT_RECHECK_AT)

        st._DELETED_OBJECT_WORK['network'].clear()
        mock_page.reset_mock()
        st._fill_deleted_object_work(now, NO_DEADLINE)
        self.assertEqual(
            ['network'], [str(c.args[0]) for c in mock_page.call_args_list])

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_failing_object_waits_for_the_recheck(
            self, mock_page, mock_get_class):
        mock_page.side_effect = (
            lambda objtype, states, page_size, after_uuid='',
            updated_before=None: _page(
                [BLOB_UUID_1] if objtype == ObjectType.INSTANCE else []))
        mock_get_class.return_value.from_db.side_effect = Exception('broken')
        clock = FakeClock()

        with mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                        'time') as fake_time, \
                mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                           'util_exceptions.ignore_exception'):
            fake_time.time.side_effect = clock
            fake_time.monotonic.side_effect = clock
            for _ in range(st.DELETED_OBJECT_WALK_RECHECK // 60):
                st.per_deleted_object_checks()
                clock.now += 60.0
            attempts_in_recheck = (
                mock_get_class.return_value.from_db.call_count)

            st.per_deleted_object_checks()

        # One attempt per recheck, rather than one per pass, and the next
        # once the recheck is due.
        self.assertEqual(1, attempts_in_recheck)
        self.assertEqual(
            2, mock_get_class.return_value.from_db.call_count)

    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_fill_stops_reading_at_its_deadline(self, mock_page):
        types = list(OBJECT_NAMES_TO_CLASSES)
        clock = FakeClock()

        def by_page(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            clock.now += 2.0
            return _page([f'{str(objtype)}-1'], more_available=True)
        mock_page.side_effect = by_page

        with mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                        'time') as fake_time:
            fake_time.time.side_effect = clock
            self.assertTrue(
                st._fill_deleted_object_work(clock.now, clock.now + 5.0))

        # Reads start at 0, 2 and 4 seconds; the fourth would start at 6.
        self.assertEqual(
            types[:3], [str(c.args[0]) for c in mock_page.call_args_list])
        self.assertEqual(
            types[3], st._SWEEP_RESUME_AFTER['per_deleted_object'])

        # The next pass starts where this one stopped.
        st._DELETED_OBJECT_WORK.clear()
        mock_page.reset_mock()
        st._fill_deleted_object_work(time.time(), NO_DEADLINE)
        self.assertEqual(
            types[3:] + types[:3],
            [str(c.args[0]) for c in mock_page.call_args_list])
        self.assertNotIn('per_deleted_object', st._SWEEP_RESUME_AFTER)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    @mock.patch('shakenfist.mariadb._get_database_stub')
    @mock.patch('shakenfist.mariadb._grpc_call')
    def test_old_server_reply_is_held_whole(
            self, mock_call, mock_stub, mock_use):
        # An sf-database from before paging ignores page_size and returns
        # every match, unordered. The fill holds all of it and treats it as
        # a completed walk.
        uuids = [f'uuid-{i:04d}'
                 for i in reversed(range(st.DELETED_OBJECT_PAGE_SIZE + 3))]

        def reply(method_name, request):
            if request.object_type == ObjectType.NETWORK.proto_id:
                return database_pb2.GetObjectsByStateReply(object_uuids=uuids)
            return database_pb2.GetObjectsByStateReply()
        mock_call.side_effect = reply
        now = time.time()
        st._DELETED_OBJECT_CURSOR['network'] = 'uuid-0100'

        self.assertTrue(st._fill_deleted_object_work(now, NO_DEADLINE))

        self.assertEqual({'network': uuids}, _held_deleted_object_work())
        self.assertEqual('', st._DELETED_OBJECT_CURSOR['network'])
        self.assertEqual(now + st.DELETED_OBJECT_WALK_RECHECK,
                         st._DELETED_OBJECT_RECHECK_AT['network'])

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_unavailable_fill_does_not_drain(self, mock_page, mock_get_class):
        # Each entry's first read would spend a whole _grpc_call retry
        # budget against the tier which has just failed one, and fail.
        types = list(OBJECT_NAMES_TO_CLASSES)

        def by_page(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            if objtype == ObjectType(types[3]):
                raise DatabaseUnavailable('tier is down')
            return _page([f'{str(objtype)}-1'])
        mock_page.side_effect = by_page

        st.per_deleted_object_checks()

        mock_get_class.assert_not_called()
        self.assertEqual({t: [f'{t}-1'] for t in types[:3]},
                         _held_deleted_object_work())
        self.assertEqual(
            types[4], st._SWEEP_RESUME_AFTER['per_deleted_object'])

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_failed_page_does_not_stop_the_other_types(
            self, mock_page, mock_get_class):
        def by_page(objtype, states, page_size, after_uuid='',
                    updated_before=None):
            if objtype == ObjectType.NETWORK:
                return None
            if objtype in (ObjectType.INSTANCE, ObjectType.NODE):
                return _page([f'{str(objtype)}-1'])
            return _page([])
        mock_page.side_effect = by_page
        mock_get_class.return_value.from_db.return_value = None

        st.per_deleted_object_checks()

        # Types on both sides of the failed one are filled and drained.
        self.assertEqual(
            ['instance-1', 'node-1'],
            [c.args[0] for c in
             mock_get_class.return_value.from_db.call_args_list])
        self.assertEqual(
            {('per_deleted_object', 'network'): 1}, st._SWEEP_FAILURE_STREAK)
        self.assertNotIn('per_deleted_object', st._SWEEP_RESUME_AFTER)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_page_reads_are_timed_per_type(self, mock_stub, mock_use):
        method = mock.MagicMock(
            return_value=database_pb2.GetObjectsByStateReply())
        method._method = '/shakenfist.protos.DatabaseService/GetObjectsByState'
        mock_stub.return_value.GetObjectsByState = method
        types = list(OBJECT_NAMES_TO_CLASSES)

        def count(objtype):
            return REGISTRY.get_sample_value(
                'database_client_rpc_seconds_count',
                {'sweep': 'per_deleted_object_fill', 'object_type': objtype,
                 'operation': 'GetObjectsByState'}) or 0
        before = {objtype: count(objtype) for objtype in types}

        self.assertTrue(st._fill_deleted_object_work(time.time(), NO_DEADLINE))

        self.assertEqual(len(types), method.call_count)
        self.assertEqual(
            {objtype: before[objtype] + 1 for objtype in types},
            {objtype: count(objtype) for objtype in types})


def _uuids(objtype, count):
    return [f'{objtype}-{i:04d}' for i in range(count)]


class DeletedObjectSweepBatchTestCase(base.ShakenFistTestCase):
    """Most types are hard deleted a batch at a time, within the pass's cap.

    hard_delete_objects() is mocked here: its eligibility check and what
    it deletes are tested against MariaDB itself.
    """

    def setUp(self):
        super().setUp()
        _reset_sweep_failure_state()
        _reset_deleted_object_sweep_state()
        self.addCleanup(_reset_deleted_object_sweep_state)

        patcher = mock.patch('shakenfist.mariadb.hard_delete_objects')
        self.mock_batch = patcher.start()
        self.addCleanup(patcher.stop)
        # Every uuid offered is eligible unless a test says otherwise.
        self.mock_batch.side_effect = (
            lambda object_type, object_uuids, **kwargs: list(object_uuids))

        patcher = mock.patch('shakenfist.eventlog.add_event')
        self.mock_event = patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch(
            'shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
        self.mock_get_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.from_db = self.mock_get_class.return_value.from_db
        self.from_db.side_effect = (
            lambda obj_uuid, suppress_failure_audit=False:
            FakeFinalStateObject())

    def _batches(self):
        """Each batch call's type and uuids, in order."""
        return [(str(c.args[0]), list(c.args[1]))
                for c in self.mock_batch.call_args_list]

    def _audited(self):
        # Every sweep audit event is muted from the log stream: one INFO
        # line per hard delete is what issue 4723 measured as a 241% log
        # volume increase while a backlog drained.
        for c in self.mock_event.call_args_list:
            self.assertTrue(c.kwargs.get('suppress_event_logging'), c)
        return [c.args for c in self.mock_event.call_args_list]

    def _entry_count(self, objtype, outcome):
        return REGISTRY.get_sample_value(
            'cluster_deleted_object_seconds_count',
            {'object_type': objtype, 'outcome': outcome}) or 0

    def _batch_count(self, objtype):
        return REGISTRY.get_sample_value(
            'cluster_deleted_object_batch_seconds_count',
            {'object_type': objtype}) or 0

    def test_a_turn_is_one_call_of_up_to_the_batch_size(self):
        uuids = _uuids('network', 30)
        _hold_deleted_object_work('network', *uuids)

        before = time.time()
        processed = st._drain_deleted_object_work(execution_limit=10)
        after = time.time()

        self.assertEqual(30, processed)
        self.assertEqual([('network', uuids[:25]), ('network', uuids[25:])],
                         self._batches())
        for c in self.mock_batch.call_args_list:
            self.assertEqual(FINAL_OBJECT_STATES, c.kwargs['state_values'])
            self.assertLessEqual(before - config.CLEANER_DELAY,
                                 c.kwargs['updated_before'])
            self.assertLessEqual(c.kwargs['updated_before'],
                                 after - config.CLEANER_DELAY)
            self.assertNotIn('unconditional', c.kwargs)
        # The eligibility check is the database's, in the delete itself.
        self.mock_get_class.assert_not_called()
        self.assertEqual(
            [(EVENT_TYPE_AUDIT, 'network', u, 'hard deleted object')
             for u in uuids],
            self._audited())
        self.assertEqual({}, _held_deleted_object_work())

    def test_an_operation_batch_uses_the_operation_delay(self):
        _hold_deleted_object_work('net_op', OP_UUID_1)

        before = time.time()
        st._drain_deleted_object_work(execution_limit=10)

        updated_before = self.mock_batch.call_args.kwargs['updated_before']
        self.assertLessEqual(before - 30, updated_before)
        self.assertLessEqual(updated_before, time.time() - 30)

    def test_a_per_object_type_is_hard_deleted_one_at_a_time(self):
        objs = {u: FakeFinalStateObject() for u in _uuids('instance', 3)}
        self.from_db.side_effect = (
            lambda obj_uuid, suppress_failure_audit=False: objs[obj_uuid])
        _hold_deleted_object_work('instance', *objs)

        outcomes = collections.Counter()
        processed = st._drain_deleted_object_work(
            execution_limit=10, outcomes=outcomes)

        self.assertEqual(3, processed)
        self.mock_batch.assert_not_called()
        self.assertEqual(
            [mock.call(u, suppress_failure_audit=True) for u in objs],
            self.from_db.call_args_list)
        self.assertTrue(all(obj.hard_deleted for obj in objs.values()))
        self.assertEqual({'hard_deleted': 3}, dict(outcomes))

    def test_skipped_and_hard_deleted_are_counted_from_the_reply(self):
        uuids = _uuids('upload', 4)
        eligible = [uuids[1], uuids[3]]
        self.mock_batch.side_effect = None
        self.mock_batch.return_value = eligible
        _hold_deleted_object_work('upload', *uuids)
        before = {o: self._entry_count('upload', o)
                  for o in ('hard_deleted', 'skipped', 'failed')}
        batches_before = self._batch_count('upload')

        outcomes = collections.Counter()
        processed = st._drain_deleted_object_work(
            execution_limit=10, outcomes=outcomes)

        self.assertEqual(4, processed)
        self.assertEqual({'hard_deleted': 2, 'skipped': 2}, dict(outcomes))
        self.assertEqual(
            {'hard_deleted': 2, 'skipped': 2, 'failed': 0},
            {o: self._entry_count('upload', o) - before[o] for o in before})
        self.assertEqual(batches_before + 1, self._batch_count('upload'))
        # Only what the database deleted is audited.
        self.assertEqual(
            [(EVENT_TYPE_AUDIT, 'upload', u, 'hard deleted object')
             for u in eligible],
            self._audited())

    def test_each_entry_gets_an_equal_share_of_the_batch(self):
        clock = FakeClock()

        def batch(object_type, object_uuids, **kwargs):
            clock.now += 2.0
            return list(object_uuids)
        self.mock_batch.side_effect = batch
        _hold_deleted_object_work('dhcp', *_uuids('dhcp', 4))

        entry = {'object_type': 'dhcp', 'outcome': 'hard_deleted'}

        def samples():
            def value(name, labels):
                return REGISTRY.get_sample_value(name, labels) or 0
            return {
                'entry_sum': value('cluster_deleted_object_seconds_sum', entry),
                # Entries of more than a quarter and at most half a second.
                'entry_half': (
                    value('cluster_deleted_object_seconds_bucket',
                          dict(entry, le='0.5'))
                    - value('cluster_deleted_object_seconds_bucket',
                            dict(entry, le='0.25'))),
                'batch_sum': value('cluster_deleted_object_batch_seconds_sum',
                                   {'object_type': 'dhcp'})}
        before = samples()

        with mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                        'time') as fake_time:
            fake_time.time.side_effect = clock
            fake_time.monotonic.side_effect = clock
            st._drain_deleted_object_work(execution_limit=10)

        after = samples()
        # One two second batch of four is four entries of half a second.
        self.assertAlmostEqual(2.0, after['batch_sum'] - before['batch_sum'])
        self.assertAlmostEqual(2.0, after['entry_sum'] - before['entry_sum'])
        self.assertEqual(4, after['entry_half'] - before['entry_half'])

    def test_the_cap_ends_the_pass(self):
        _hold_deleted_object_work('network', *_uuids('network', 40))
        _hold_deleted_object_work('upload', *_uuids('upload', 40))

        with mock.patch.object(config, 'DELETED_OBJECT_SWEEP_MAX_OBJECTS', 30):
            processed = st._drain_deleted_object_work(execution_limit=10)

        # The second turn takes only the five the cap has left.
        self.assertEqual(30, processed)
        self.assertEqual(
            [('network', _uuids('network', 25)),
             ('upload', _uuids('upload', 5))],
            self._batches())
        self.assertEqual(15, len(_held_deleted_object_work()['network']))
        self.assertEqual(35, len(_held_deleted_object_work()['upload']))

    def test_a_per_object_turn_takes_only_what_the_cap_has_left(self):
        # agentoperation is batched and comes before instance.
        _hold_deleted_object_work('agentoperation', *_uuids('agentoperation', 5))
        _hold_deleted_object_work('instance', *_uuids('instance', 10))

        with mock.patch.object(config, 'DELETED_OBJECT_SWEEP_MAX_OBJECTS', 8):
            processed = st._drain_deleted_object_work(execution_limit=10)

        self.assertEqual(8, processed)
        self.assertEqual(
            _uuids('instance', 3),
            [c.args[0] for c in self.from_db.call_args_list])
        self.assertEqual(7, len(_held_deleted_object_work()['instance']))

    def test_a_per_object_turn_stops_at_the_deadline(self):
        clock = FakeClock()

        def from_db(obj_uuid, suppress_failure_audit=False):
            clock.now += 1.0
            return None
        self.from_db.side_effect = from_db
        _hold_deleted_object_work('instance', *_uuids('instance', 5))

        with mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                        'time') as fake_time:
            fake_time.time.side_effect = clock
            fake_time.monotonic.side_effect = clock
            processed = st._drain_deleted_object_work(execution_limit=1.5)

        # Entries start at 0 and 1 seconds; the third would start at 2.
        self.assertEqual(2, processed)
        self.assertEqual(_uuids('instance', 5)[2:],
                         _held_deleted_object_work()['instance'])

    def test_a_per_object_type_is_served_between_batches(self):
        # agentoperation and upload are batched, instance is not and sits
        # between them. Fairness is by entries, so the instance turn takes
        # as many as a batch even though each costs a call of its own.
        _hold_deleted_object_work('agentoperation', *_uuids('agentoperation', 200))
        _hold_deleted_object_work('instance', *_uuids('instance', 30))
        _hold_deleted_object_work('upload', *_uuids('upload', 200))
        turns = []
        self.mock_batch.side_effect = (
            lambda object_type, object_uuids, **kwargs:
            turns.append((str(object_type), len(object_uuids))) or
            list(object_uuids))

        def from_db(obj_uuid, suppress_failure_audit=False):
            if turns and turns[-1][0] == 'instance':
                turns[-1] = ('instance', turns[-1][1] + 1)
            else:
                turns.append(('instance', 1))
            return FakeFinalStateObject()
        self.from_db.side_effect = from_db

        processed = st._drain_deleted_object_work(execution_limit=10)

        self.assertEqual(100, processed)
        self.assertEqual(
            [('agentoperation', 25), ('instance', 25), ('upload', 25),
             ('agentoperation', 25)],
            turns)

        # The next pass carries on from the rotation, so instance's last
        # five are taken in its next turn.
        turns.clear()
        st._drain_deleted_object_work(execution_limit=10)
        self.assertEqual(
            [('instance', 5), ('upload', 25), ('agentoperation', 25),
             ('upload', 25), ('agentoperation', 20)],
            turns)

    def test_a_failed_batch_is_dropped(self):
        self.mock_batch.side_effect = None
        self.mock_batch.return_value = None
        _hold_deleted_object_work('network', *_uuids('network', 3))
        _hold_deleted_object_work('upload', *_uuids('upload', 2))
        failed_before = self._entry_count('network', 'failed')

        outcomes = collections.Counter()
        processed = st._drain_deleted_object_work(
            execution_limit=10, outcomes=outcomes)

        # Both turns are tried, and the next walk of each type finds them
        # again after its recheck.
        self.assertEqual(5, processed)
        self.assertEqual({'failed': 5}, dict(outcomes))
        self.assertEqual(3, self._entry_count('network', 'failed')
                         - failed_before)
        self.assertEqual({}, _held_deleted_object_work())
        self.mock_event.assert_not_called()

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                'util_exceptions.ignore_exception')
    def test_a_batch_which_raises_is_failed(self, mock_ignore):
        self.mock_batch.side_effect = RuntimeError('lost the channel')
        _hold_deleted_object_work('network', *_uuids('network', 3))

        outcomes = collections.Counter()
        processed = st._drain_deleted_object_work(
            execution_limit=10, outcomes=outcomes)

        self.assertEqual(3, processed)
        self.assertEqual({'failed': 3}, dict(outcomes))
        mock_ignore.assert_called_once()
        self.mock_event.assert_not_called()

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                'util_exceptions.ignore_exception')
    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.LOG')
    def test_a_refused_batch_is_taken_one_object_at_a_time(
            self, mock_log, mock_ignore):
        network = _uuids('network', 4)
        bad = network[1]
        upload = _uuids('upload', 2)
        _hold_deleted_object_work('network', *network)
        _hold_deleted_object_work('upload', *upload)

        def batch(object_type, object_uuids, **kwargs):
            if bad in object_uuids:
                raise ValueError(f'{bad!r} is not a uuid')
            return list(object_uuids)
        self.mock_batch.side_effect = batch

        objs = {u: FakeFinalStateObject() for u in network}

        def from_db(obj_uuid, suppress_failure_audit=False):
            if obj_uuid == bad:
                raise ValueError(f'{bad!r} is not a uuid')
            return objs[obj_uuid]
        self.from_db.side_effect = from_db

        outcomes = collections.Counter()
        processed = st._drain_deleted_object_work(
            execution_limit=10, outcomes=outcomes)

        # Only the malformed entry fails; the rest of its turn is hard
        # deleted one at a time, and the next type is still batched.
        self.assertEqual(6, processed)
        self.assertEqual({'hard_deleted': 5, 'failed': 1}, dict(outcomes))
        self.assertEqual(network, [c.args[0] for c in self.from_db.call_args_list])
        self.assertEqual(
            [u != bad for u in network], [objs[u].hard_deleted for u in network])
        self.assertEqual([('network', network), ('upload', upload)], self._batches())
        self.assertEqual({}, _held_deleted_object_work())
        mock_log.with_fields.return_value.warning.assert_called_once()

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.LOG')
    def test_an_unavailable_tier_puts_the_batch_back(self, mock_log):
        network = _uuids('network', 30)
        _hold_deleted_object_work('network', *network)
        _hold_deleted_object_work('upload', *_uuids('upload', 3))
        _hold_deleted_object_work('node', *_uuids('node', 3))
        self.mock_batch.side_effect = DatabaseUnavailable('tier is down')

        outcomes = collections.Counter()
        processed = st._drain_deleted_object_work(
            execution_limit=10, outcomes=outcomes)

        # One call, then the pass ends, with the batch back where it was.
        self.assertEqual(0, processed)
        self.assertEqual({}, dict(outcomes))
        self.assertEqual(1, self.mock_batch.call_count)
        self.from_db.assert_not_called()
        self.assertEqual(
            {'network': network, 'upload': _uuids('upload', 3),
             'node': _uuids('node', 3)},
            _held_deleted_object_work())
        mock_log.with_fields.return_value.warning.assert_called_once()

        # And the next pass resumes after the type that stopped it.
        self.mock_batch.side_effect = (
            lambda object_type, object_uuids, **kwargs: list(object_uuids))
        st._drain_deleted_object_work(execution_limit=10)
        self.assertEqual('upload', self._batches()[1][0])
        self.assertEqual(('network', network[:25]), self._batches()[2])

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.LOG')
    def test_an_old_database_is_hard_deleted_one_at_a_time(self, mock_log):
        network = _uuids('network', 3)
        upload = _uuids('upload', 2)
        _hold_deleted_object_work('network', *network)
        _hold_deleted_object_work('upload', *upload)
        self.mock_batch.side_effect = HardDeleteBatchUnavailable('too old')
        objs = {u: FakeFinalStateObject() for u in network + upload}
        self.from_db.side_effect = (
            lambda obj_uuid, suppress_failure_audit=False: objs[obj_uuid])

        outcomes = collections.Counter()
        processed = st._drain_deleted_object_work(
            execution_limit=10, outcomes=outcomes)

        # Asked once, then every turn of the pass goes one object at a time,
        # through each object's own hard delete, in the order held.
        self.assertEqual(5, processed)
        self.assertEqual(1, self.mock_batch.call_count)
        self.assertEqual(network + upload,
                         [c.args[0] for c in self.from_db.call_args_list])
        self.assertTrue(all(obj.hard_deleted for obj in objs.values()))
        self.assertEqual({'hard_deleted': 5}, dict(outcomes))
        self.assertEqual(1, mock_log.warning.call_count)

        # A later pass asks again, in case the database has been upgraded.
        _hold_deleted_object_work('network', *_uuids('network', 2))
        self.mock_batch.side_effect = (
            lambda object_type, object_uuids, **kwargs: list(object_uuids))
        st._drain_deleted_object_work(execution_limit=10)
        self.assertEqual(2, self.mock_batch.call_count)

    def test_an_old_database_turn_keeps_what_it_did_not_reach(self):
        clock = FakeClock()

        def from_db(obj_uuid, suppress_failure_audit=False):
            clock.now += 1.0
            return None
        self.from_db.side_effect = from_db
        self.mock_batch.side_effect = HardDeleteBatchUnavailable('too old')
        network = _uuids('network', 5)
        _hold_deleted_object_work('network', *network)

        with mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                        'time') as fake_time:
            fake_time.time.side_effect = clock
            fake_time.monotonic.side_effect = clock
            processed = st._drain_deleted_object_work(execution_limit=1.5)

        self.assertEqual(2, processed)
        self.assertEqual({'network': network[2:]},
                         _held_deleted_object_work())

    @mock.patch('shakenfist.daemons.cluster.scheduled_tasks.LOG')
    @mock.patch('shakenfist.mariadb.get_objects_by_state_page')
    def test_the_pass_summary_counts_skipped(self, mock_page, mock_log):
        uuids = _uuids('network', 3)
        mock_page.side_effect = (
            lambda objtype, states, page_size, after_uuid='',
            updated_before=None: _page(
                uuids if objtype == ObjectType.NETWORK else []))
        self.mock_batch.side_effect = None
        self.mock_batch.return_value = uuids[:1]

        st.per_deleted_object_checks()

        fields = [c.args[0] for c in mock_log.with_fields.call_args_list
                  if 'total' in c.args[0]][0]
        self.assertEqual(3, fields['total'])
        self.assertEqual(1, fields['hard_deleted'])
        self.assertEqual(2, fields['skipped'])


class DeletedObjectBacklogTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        _reset_sweep_failure_state()
        st.CLUSTER_DELETED_OBJECT_BACKLOG.clear()
        self.addCleanup(st.CLUSTER_DELETED_OBJECT_BACKLOG.clear)

    def _backlog(self, objtype):
        return REGISTRY.get_sample_value(
            'cluster_deleted_object_backlog', {'object_type': objtype})

    @mock.patch('shakenfist.mariadb.count_objects_by_state')
    def test_every_type_published_zero_included(self, mock_count):
        counts = {ObjectType.NETWORK: 7}
        mock_count.side_effect = lambda objtype, states: counts.get(objtype, 0)

        st.publish_deleted_object_backlog()

        for objtype in OBJECT_NAMES_TO_CLASSES:
            self.assertEqual(7 if objtype == 'network' else 0,
                             self._backlog(objtype), objtype)
        self.assertEqual(len(OBJECT_NAMES_TO_CLASSES), mock_count.call_count)
        for call in mock_count.call_args_list:
            self.assertIsInstance(call.args[0], ObjectType)
            self.assertEqual(st.FINAL_OBJECT_STATES, call.args[1])
        self.assertNotIn('deleted_object_backlog', st._SWEEP_RESUME_AFTER)

    @mock.patch('shakenfist.mariadb.count_objects_by_state')
    def test_failed_count_removes_label_set(self, mock_count):
        mock_count.return_value = 4
        st.publish_deleted_object_backlog()
        self.assertEqual(4, self._backlog('network'))

        mock_count.side_effect = (
            lambda objtype, states: None if objtype == ObjectType.NETWORK else 5)
        st.publish_deleted_object_backlog()

        self.assertIsNone(self._backlog('network'))
        self.assertEqual(5, self._backlog('blob'))

    @mock.patch('shakenfist.mariadb.count_objects_by_state')
    def test_failed_count_never_published_is_safe(self, mock_count):
        mock_count.return_value = None
        st.publish_deleted_object_backlog()
        self.assertIsNone(self._backlog('network'))

    @mock.patch('shakenfist.mariadb.count_objects_by_state')
    def test_database_unavailable_stops_and_records_resume(self, mock_count):
        types = list(OBJECT_NAMES_TO_CLASSES)

        def count(objtype, states):
            if objtype == ObjectType(types[3]):
                raise DatabaseUnavailable('gone')
            return 1
        mock_count.side_effect = count

        st.publish_deleted_object_backlog()

        self.assertEqual(4, mock_count.call_count)
        self.assertEqual(1, self._backlog(types[2]))
        self.assertIsNone(self._backlog(types[3]))
        self.assertIsNone(self._backlog(types[4]))
        self.assertEqual(
            types[4], st._SWEEP_RESUME_AFTER['deleted_object_backlog'])

        # The next pass resumes after the type that stopped this one.
        mock_count.reset_mock()
        mock_count.side_effect = None
        mock_count.return_value = 2
        st.publish_deleted_object_backlog()
        self.assertEqual(ObjectType(types[4]), mock_count.call_args_list[0].args[0])
        self.assertEqual(len(types), mock_count.call_count)
        self.assertNotIn('deleted_object_backlog', st._SWEEP_RESUME_AFTER)

    @mock.patch('shakenfist.mariadb.count_objects_by_state', return_value=0)
    def test_each_count_is_timed_in_a_scope(self, mock_count):
        with mock.patch('shakenfist.mariadb.rpc_timing_scope') as mock_scope:
            st.publish_deleted_object_backlog()
        self.assertEqual(
            [mock.call('deleted_object_backlog', t) for t in
             OBJECT_NAMES_TO_CLASSES], mock_scope.call_args_list)

    @mock.patch('shakenfist.mariadb.count_objects_by_state', return_value=3)
    def test_demotion_clears_gauge(self, mock_count):
        st.publish_deleted_object_backlog()
        self.assertEqual(3, self._backlog('network'))

        st.clear_deleted_object_metrics()

        self.assertIsNone(self._backlog('network'))


class ReconcileOrphanedObjectsTestCase(base.ShakenFistTestCase):
    """Orphan reconciliation: phantoms removed server-side, zombies
    repaired after two consecutive observations (issue 3534) -- to
    deleted, or to created for a credential its read path would still
    honour (issue 3836)."""

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
        self.assertIn('marked this object deleted',
                      mock_add_event.call_args.args[3])

    # The object types whose read paths honour an object with no state
    # row, and the attributes reader each one's zombie_repair_state()
    # consults (issue 3836).
    LIVE_READ_TYPES = (
        (ObjectType.NAMESPACE_KEY,
         'shakenfist.mariadb.get_namespace_key_attributes'),
        (ObjectType.TRUSTED_ISSUER,
         'shakenfist.mariadb.get_trusted_issuer_attributes'),
        (ObjectType.MAPPING_RULE,
         'shakenfist.mariadb.get_mapping_rule_attributes'),
    )

    def _one_zombie_of(self, zombie_type):
        def stateless(objtype):
            if objtype == zombie_type:
                return [BLOB_UUID_1]
            return []
        return stateless

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_live_zombie_credentials_are_repaired_forward(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        # A key, issuer or rule with its attributes row is honoured by
        # its read path with no state row at all. Marking it deleted
        # would destroy a working credential (issue 3836), so it is
        # repaired to created instead.
        for zombie_type, reader in self.LIVE_READ_TYPES:
            with self.subTest(object_type=zombie_type):
                st._ZOMBIE_CANDIDATES.clear()
                mock_set_state.reset_mock()
                mock_add_event.reset_mock()
                mock_stateless.side_effect = self._one_zombie_of(zombie_type)

                with mock.patch(reader,
                                return_value=mock.sentinel.attributes) as rd, \
                        mock.patch.object(st.LOG, 'with_fields') as log:
                    st.reconcile_orphaned_objects()
                    mock_set_state.assert_not_called()

                    st.reconcile_orphaned_objects()

                rd.assert_called_once()
                mock_set_state.assert_called_once()
                objtype, obj_uuid, state = mock_set_state.call_args.args
                self.assertEqual(zombie_type, objtype)
                self.assertEqual(BLOB_UUID_1, obj_uuid)
                self.assertEqual('created', state.value)

                mock_add_event.assert_called_once()
                self.assertIn('found this object live',
                              mock_add_event.call_args.args[3])
                self.assertIn('marked it created',
                              mock_add_event.call_args.args[3])

                fields = [c.args[0] for c in log.call_args_list
                          if 'repaired_state' in c.args[0]]
                self.assertEqual(1, len(fields))
                self.assertEqual('created', fields[0]['repaired_state'])

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_zombie_credentials_without_attributes_are_deleted(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        # With no attributes row there is nothing to authenticate
        # against or evaluate, so the zombie is garbage, exactly as for
        # any other type.
        for zombie_type, reader in self.LIVE_READ_TYPES:
            with self.subTest(object_type=zombie_type):
                st._ZOMBIE_CANDIDATES.clear()
                mock_set_state.reset_mock()
                mock_add_event.reset_mock()
                mock_stateless.side_effect = self._one_zombie_of(zombie_type)

                with mock.patch(reader, return_value=None) as rd:
                    st.reconcile_orphaned_objects()
                    st.reconcile_orphaned_objects()

                rd.assert_called_once()
                mock_set_state.assert_called_once()
                objtype, obj_uuid, state = mock_set_state.call_args.args
                self.assertEqual(zombie_type, objtype)
                self.assertEqual('deleted', state.value)
                mock_add_event.assert_called_once()
                self.assertIn('marked this object deleted',
                              mock_add_event.call_args.args[3])

    def _two_zombies_of(self, zombie_type):
        def stateless(objtype):
            if objtype == zombie_type:
                return [BLOB_UUID_1, BLOB_UUID_2]
            return []
        return stateless

    def _assert_pass_stopped_on(self, zombie_type, mock_stateless):
        """The pass read types up to zombie_type and then stopped.

        The stop is recorded the same way as a tier-wide work-list
        failure: the next pass resumes after the type that stopped this
        one, and no type after it was read on this pass.
        """
        zombie_types = [t for t in mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES
                        if t not in st.ZOMBIE_REPAIR_EXCLUDED_TYPES]
        idx = zombie_types.index(str(zombie_type))
        read = [str(c.args[0]) for c in mock_stateless.call_args_list]
        self.assertEqual(zombie_types[:idx + 1], read)
        self.assertEqual(
            zombie_types[(idx + 1) % len(zombie_types)],
            st._SWEEP_RESUME_AFTER['reconcile_orphans'])

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_unreadable_liveness_writes_nothing_and_retries(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        # If the reconciler cannot tell whether a zombie is live, it
        # must not guess: deleted would destroy a working credential,
        # created would resurrect garbage. It writes nothing, and since
        # the tier is gone it ends the pass rather than spending another
        # retry budget per remaining object and type. The zombies stay
        # candidates, and the next pass repairs them.
        for zombie_type, reader in self.LIVE_READ_TYPES:
            with self.subTest(object_type=zombie_type):
                st._ZOMBIE_CANDIDATES.clear()
                _reset_sweep_failure_state()
                mock_set_state.reset_mock()
                mock_add_event.reset_mock()
                mock_stateless.side_effect = self._two_zombies_of(
                    zombie_type)

                with mock.patch(
                        reader,
                        side_effect=DatabaseUnavailable('tier blip')) as rd:
                    st.reconcile_orphaned_objects()
                    self.assertNotIn('reconcile_orphans',
                                     st._SWEEP_RESUME_AFTER)

                    mock_stateless.reset_mock()
                    st.reconcile_orphaned_objects()

                # Only the first zombie was asked about; the second was
                # not attempted, and neither were the later types.
                rd.assert_called_once()
                mock_set_state.assert_not_called()
                mock_add_event.assert_not_called()
                self._assert_pass_stopped_on(zombie_type, mock_stateless)
                self.assertEqual(
                    {BLOB_UUID_1, BLOB_UUID_2},
                    st._ZOMBIE_CANDIDATES[str(zombie_type)])

                # The read recovers: the next pass repairs both and
                # completes.
                with mock.patch(reader,
                                return_value=mock.sentinel.attributes):
                    st.reconcile_orphaned_objects()

                self.assertEqual(2, mock_set_state.call_count)
                self.assertEqual(
                    {BLOB_UUID_1, BLOB_UUID_2},
                    {c.args[1] for c in mock_set_state.call_args_list})
                for c in mock_set_state.call_args_list:
                    self.assertEqual('created', c.args[2].value)
                self.assertEqual(2, mock_add_event.call_count)
                self.assertNotIn('reconcile_orphans', st._SWEEP_RESUME_AFTER)

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_mariadb_error_on_key_liveness_read_writes_nothing(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event, mock_use, mock_engine):
        # The same fail-closed property, but driven through the real
        # attributes reader rather than a mock of it. The reader used to
        # return None on an OperationalError, which reads as "no
        # attributes" and would have marked a live key deleted on any
        # MariaDB blip (issue 3836).
        mock_stateless.side_effect = self._two_zombies_of(
            ObjectType.NAMESPACE_KEY)
        mock_engine.return_value.connect.side_effect = OperationalError(
            'SELECT', {}, Exception('server has gone away'))

        st.reconcile_orphaned_objects()
        mock_stateless.reset_mock()
        st.reconcile_orphaned_objects()

        mock_engine.return_value.connect.assert_called_once()
        mock_set_state.assert_not_called()
        mock_add_event.assert_not_called()
        self._assert_pass_stopped_on(ObjectType.NAMESPACE_KEY, mock_stateless)
        self.assertEqual({BLOB_UUID_1, BLOB_UUID_2},
                         st._ZOMBIE_CANDIDATES['namespace_key'])

        with mock.patch('shakenfist.mariadb.get_namespace_key_attributes',
                        return_value=mock.sentinel.attributes):
            st.reconcile_orphaned_objects()

        self.assertEqual(2, mock_set_state.call_count)
        for c in mock_set_state.call_args_list:
            self.assertEqual('created', c.args[2].value)

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_other_liveness_failures_cost_only_that_object(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        # Only a tier-wide failure ends the pass. Anything else is about
        # one object, so that object is skipped (and stays a candidate)
        # while the rest of its type and the pass carry on.
        mock_stateless.side_effect = self._two_zombies_of(
            ObjectType.NAMESPACE_KEY)

        with mock.patch('shakenfist.mariadb.get_namespace_key_attributes',
                        side_effect=[RuntimeError('odd row'),
                                     mock.sentinel.attributes]):
            st.reconcile_orphaned_objects()
            st.reconcile_orphaned_objects()

        mock_set_state.assert_called_once()
        self.assertEqual(BLOB_UUID_2, mock_set_state.call_args.args[1])
        self.assertEqual('created', mock_set_state.call_args.args[2].value)
        self.assertIn(BLOB_UUID_1, st._ZOMBIE_CANDIDATES['namespace_key'])
        self.assertNotIn('reconcile_orphans', st._SWEEP_RESUME_AFTER)

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.set_state', return_value=True)
    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids')
    @mock.patch('shakenfist.mariadb.delete_orphaned_artifact_attributes',
                return_value=0)
    @mock.patch('shakenfist.mariadb.delete_orphaned_object_states',
                return_value=0)
    def test_unresolvable_class_skips_only_that_type(
            self, mock_delete_orphans, mock_delete_attrs, mock_stateless,
            mock_set_state, mock_add_event):
        # Without the class there is no way to ask whether a zombie is
        # live, and defaulting to deleted is the answer that can destroy
        # one. That type's zombies are left as candidates, and the sweep
        # still finishes every other type.
        def stateless(objtype):
            if objtype in (ObjectType.NETWORK, ObjectType.NAMESPACE_KEY):
                return [BLOB_UUID_1]
            return []
        mock_stateless.side_effect = stateless

        with mock.patch.object(st, 'get_object_class') as get_class:
            def resolve(objtype):
                if objtype == 'namespace_key':
                    raise ImportError('no such module')
                return dbo
            get_class.side_effect = resolve

            st.reconcile_orphaned_objects()
            st.reconcile_orphaned_objects()

        mock_set_state.assert_called_once()
        objtype, _, state = mock_set_state.call_args.args
        self.assertEqual(ObjectType.NETWORK, objtype)
        self.assertEqual('deleted', state.value)
        self.assertIn(BLOB_UUID_1, st._ZOMBIE_CANDIDATES['namespace_key'])

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
                   used_disk_gb=100, expected_demand=2.5,
                   delta_used_cpus=0, delta_used_memory_mb=0,
                   delta_used_disk_gb=0):
    return {
        'node_uuid': node_uuid,
        'limit_cpus': limit_cpus,
        'limit_memory_mb': limit_memory_mb,
        'limit_disk_gb': limit_disk_gb,
        'used_cpus': used_cpus,
        'used_memory_mb': used_memory_mb,
        'used_disk_gb': used_disk_gb,
        'expected_demand': expected_demand,
        'delta_used_cpus': delta_used_cpus,
        'delta_used_memory_mb': delta_used_memory_mb,
        'delta_used_disk_gb': delta_used_disk_gb
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
    def test_zero_drift_is_reported_as_zero(self, mock_reconcile):
        # The ordinary case, and the one that has to be distinguishable
        # from "we did not look". A quiet pass must say drifted_nodes=0
        # rather than say nothing, otherwise zero drift and absent
        # instrumentation are the same log line -- which is exactly the
        # ambiguity that once meant a soak could not confirm the
        # reconciler found zero drift, only that it ran.
        mock_reconcile.return_value = _capacity_reply(
            [_capacity_node(NODE_UUID_1), _capacity_node(NODE_UUID_2)])

        with mock.patch.object(st, 'LOG') as mock_log:
            st.reconcile_scheduler_capacity()

        fields = mock_log.with_fields.call_args[0][0]
        self.assertEqual(0, fields['drifted_nodes'])
        self.assertEqual(0, fields['drift_cpus'])
        self.assertEqual(0, fields['drift_memory_mb'])
        self.assertEqual(0, fields['drift_disk_gb'])

    @mock.patch(
        'shakenfist.daemons.cluster.scheduled_tasks.'
        'mariadb.reconcile_scheduler_capacity')
    def test_drift_is_counted_and_summed_by_magnitude(self, mock_reconcile):
        # Drift in opposite directions on two nodes. The magnitudes must
        # add rather than cancel: a cluster where one node over-counted
        # by 3 cpus and another under-counted by 2 has drifted by 5, not
        # by 1, and a signed sum would report the latter and read as
        # nearly healthy.
        mock_reconcile.return_value = _capacity_reply(
            [_capacity_node(NODE_UUID_1, delta_used_cpus=3,
                            delta_used_memory_mb=1024),
             _capacity_node(NODE_UUID_2, delta_used_cpus=-2,
                            delta_used_disk_gb=-40)])

        with mock.patch.object(st, 'LOG') as mock_log:
            st.reconcile_scheduler_capacity()

        fields = mock_log.with_fields.call_args[0][0]
        self.assertEqual(2, fields['drifted_nodes'])
        self.assertEqual(5, fields['drift_cpus'])
        self.assertEqual(1024, fields['drift_memory_mb'])
        self.assertEqual(40, fields['drift_disk_gb'])

        # Each drifting node is also named individually, so an operator
        # can tell which counter went wrong rather than only that one
        # did.
        warned = [c for c in mock_log.with_fields.call_args_list
                  if 'node' in c[0][0]]
        self.assertEqual(2, len(warned))

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
