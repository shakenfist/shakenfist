# Copyright 2026 Michael Still and contributors
"""Tests for sf-queues re-running the work its previous process abandoned.

When sf-queues stopped with a worker still running -- SIGKILLed by systemd
during a deploy, for one -- the worker's operation stayed executing and its
work item claimed. The restarted daemon never cleared that claim (it reset
queues named after the node's name, but they are named after its uuid), and
had it done so the next worker would have skipped the executing operation and
resolved its item, stranding it. The reaper errored it 30 minutes later, long
after the instance waiting on it had given up (#4543).

The same recovery now reaches the shared any-* queues, which every node's
sf-queues drains. It could not before, because claimed_by named the database
daemon's node rather than the worker's, so a stopped node's claim there was
indistinguishable from a live peer's (#4652).

Re-running is only correct for an operation whose tasks converge on a desired
state, because this interruption, unlike a defer, lands wherever the SIGKILL
did. A type which creates or appends declares RESTARTABLE = False and is
errored instead; RestartabilityAuditTestCase holds that audit (#4656).
"""

import types
from unittest import mock

from shakenfist import exceptions
from shakenfist.constants import get_object_class
from shakenfist.constants import NoSuchObject
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.daemons.queues import startup_tasks
from shakenfist.exceptions import InvalidStateException
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_general_background_node_queues
from shakenfist.operations.baseoperation import get_general_user_facing_node_queues
from shakenfist.operations.baseoperation import get_node_background_node_queues
from shakenfist.operations.baseoperation import get_node_user_facing_node_queues
from shakenfist.tests import base


NODE_UUID = '6a3c5a8e-0c8a-4a8f-9d77-6f1f4f0f8d11'
OWN_QUEUE = f'{NODE_UUID}-clusteroperation-user_facing'
OTHER_NODE_QUEUE = 'f00dfeed-0000-4000-8000-000000000000-clusteroperation-user_facing'
SHARED_QUEUE = 'any-clusteroperation-user_facing'


class FakeOp:
    def __init__(self, state, refuse=False, restartable=True):
        self._state = state
        self.refuse = refuse
        self.RESTARTABLE = restartable
        self.events = []

    @property
    def state(self):
        return types.SimpleNamespace(value=self._state)

    @state.setter
    def state(self, new_state):
        if self.refuse:
            raise InvalidStateException('already terminal')
        self._state = new_state

    def add_event(self, event_type, message, extra=None):
        self.events.append(message)


def row(queue_name, op_uuid, attempts=1, row_id=1, claimed_by=NODE_UUID):
    return {
        'id': row_id,
        'queue_name': queue_name,
        'claimed_at': 1000.0,
        'claimed_by': claimed_by,
        'attempts': attempts,
        'payload': {'operation_type': 'artifact_fetch_op', 'operation_uuid': op_uuid},
    }


class RecoverInterruptedOperationsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.ops = {}
        self.rows = []

        self.config = fake_config = mock.MagicMock()
        fake_config.CLUSTER_OP_MAX_ATTEMPTS = 5
        fake_config.NODE_NAME = 'sf-6'
        fake_config.NODE_UUID = NODE_UUID
        self.mariadb = mock.MagicMock()
        self.mariadb.list_stuck_work_queue_rows.side_effect = lambda threshold, strict=False: self.rows
        self.mariadb.delete_work_queue_row.return_value = True
        op_class = mock.MagicMock()
        op_class.from_db.side_effect = lambda op_uuid: self.ops.get(op_uuid)

        def get_object_class(op_type):
            if op_type == 'retired_op':
                raise NoSuchObject(op_type)
            return op_class

        for p in [
                mock.patch.object(startup_tasks, 'config', fake_config),
                mock.patch.object(startup_tasks, 'mariadb', self.mariadb),
                mock.patch.object(startup_tasks, 'get_object_class', side_effect=get_object_class)]:
            p.start()
            self.addCleanup(p.stop)

    def test_lists_every_claim(self):
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        # Strictly: a listing which failed must not read as "no claims", or
        # startup clears claims nobody checked.
        self.mariadb.list_stuck_work_queue_rows.assert_called_once_with(0, strict=True)

    def test_executing_op_on_own_queue_is_requeued(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OWN_QUEUE, 'op'))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, op.state.value)
        self.assertEqual(1, len(op.events))
        self.mariadb.delete_work_queue_row.assert_not_called()

    def test_background_queue_is_also_ours(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(f'{NODE_UUID}-clusteroperation-background_high_io', 'op'))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, op.state.value)

    def test_our_own_claim_on_a_shared_queue_is_requeued(self):
        # #4652: claimed_by now names the node whose worker claimed the row,
        # so a shared queue claim this node's previous sf-queues abandoned is
        # recoverable rather than the reaper's 30 minutes later.
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(SHARED_QUEUE, 'op', claimed_by=NODE_UUID))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, op.state.value)

    def test_another_nodes_claim_on_a_shared_queue_is_left_alone(self):
        # Its worker may be running that operation right now.
        shared = self.ops['shared'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        # And a claim written by an sf-queues from before worker_id existed
        # names the database node, which is nobody's to recover.
        legacy = self.ops['legacy'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.extend([
            row(SHARED_QUEUE, 'shared',
                claimed_by='f00dfeed-0000-4000-8000-000000000000'),
            row('any-clusteroperation-background', 'legacy', row_id=2,
                claimed_by='sf-database-1')])
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_EXECUTING, shared.state.value)
        self.assertEqual(BaseClusterOperation.STATE_EXECUTING, legacy.state.value)

    def test_another_nodes_own_queue_is_left_alone(self):
        other = self.ops['other'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OTHER_NODE_QUEUE, 'other'))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_EXECUTING, other.state.value)

    def test_the_database_nodes_name_is_not_ours_even_on_the_database_node(self):
        # Before #4652 an sf-database recorded its own name for every claim
        # made over gRPC, and still does for an older client. On the database
        # node itself that name is this node's, but the claim may be any
        # node's -- a live peer's included. Only the uuid makes it ours.
        self.config.NODE_NAME = 'sf-database-1'
        legacy = self.ops['legacy'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(SHARED_QUEUE, 'legacy', claimed_by='sf-database-1'))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_EXECUTING, legacy.state.value)

    def test_our_own_queue_is_ours_whatever_claimed_by_says(self):
        # Nothing else drains it while this node is live. Claims on it may
        # still name another node: one an older sf-queues made over gRPC
        # names the database node, as this row does.
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OWN_QUEUE, 'op', claimed_by='sf-database-1'))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, op.state.value)

    def test_ops_not_executing_are_left_alone(self):
        # A queued operation re-runs once its claim is cleared anyway.
        queued = self.ops['queued'] = FakeOp(BaseClusterOperation.STATE_QUEUED)
        done = self.ops['done'] = FakeOp(BaseClusterOperation.STATE_COMPLETE)
        self.rows.extend([row(OWN_QUEUE, 'queued'), row(OWN_QUEUE, 'done', row_id=2)])
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual([], queued.events)
        self.assertEqual([], done.events)
        self.assertEqual(BaseClusterOperation.STATE_COMPLETE, done.state.value)

    def test_missing_op_and_bad_payload_are_skipped(self):
        self.rows.append(row(OWN_QUEUE, 'missing'))
        self.rows.append(dict(row(OWN_QUEUE, 'x', row_id=2), payload={}))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.mariadb.delete_work_queue_row.assert_not_called()

    def test_op_which_keeps_killing_sf_queues_is_errored(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OWN_QUEUE, 'op', attempts=5, row_id=42))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.mariadb.delete_work_queue_row.assert_called_once_with(42)
        self.assertEqual(BaseClusterOperation.STATE_ERROR, op.state.value)

    def test_non_restartable_op_is_errored_rather_than_requeued(self):
        # #4656: a defer happens where the task chose to, but this
        # interruption can land part way through a libvirt attach or a
        # blob write. A type whose tasks create or append is errored, as
        # the reaper's orphan pass did before this recovery existed.
        op = self.ops['op'] = FakeOp(
            BaseClusterOperation.STATE_EXECUTING, restartable=False)
        self.rows.append(row(OWN_QUEUE, 'op', attempts=1, row_id=7))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_ERROR, op.state.value)
        # The item goes too, or a worker would dequeue it, find the
        # operation not executing, and resolve it anyway.
        self.mariadb.delete_work_queue_row.assert_called_once_with(7)
        self.assertEqual(1, len(op.events))
        self.assertIn('cannot be safely re-run', op.events[0])

    def test_non_restartable_op_is_left_alone_if_its_row_is_gone(self):
        # Erroring an operation whose item another process has already
        # taken would error work which is about to run.
        op = self.ops['op'] = FakeOp(
            BaseClusterOperation.STATE_EXECUTING, restartable=False)
        self.rows.append(row(OWN_QUEUE, 'op'))
        self.mariadb.delete_work_queue_row.return_value = False
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_EXECUTING, op.state.value)
        self.assertEqual([], op.events)

    def test_a_non_restartable_op_does_not_stop_the_pass(self):
        unsafe = self.ops['unsafe'] = FakeOp(
            BaseClusterOperation.STATE_EXECUTING, restartable=False)
        later = self.ops['later'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.extend([row(OWN_QUEUE, 'unsafe'),
                          row(OWN_QUEUE, 'later', row_id=2)])
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_ERROR, unsafe.state.value)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, later.state.value)

    def test_last_attempt_below_the_ceiling_is_still_requeued(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OWN_QUEUE, 'op', attempts=4))
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, op.state.value)

    def test_row_already_gone_leaves_op_alone(self):
        op = self.ops['op'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.append(row(OWN_QUEUE, 'op', attempts=5))
        self.mariadb.delete_work_queue_row.return_value = False
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_EXECUTING, op.state.value)

    def test_an_item_which_cannot_be_recovered_does_not_stop_the_pass(self):
        # An operation type which no longer exists, or the database failing
        # part way through, is left for the reaper rather than escaping and
        # stopping sf-queues' startup before it restores this node's instances.
        retired = row(OWN_QUEUE, 'retired')
        retired['payload']['operation_type'] = 'retired_op'
        broken = self.ops['broken'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        broken.add_event = mock.MagicMock(side_effect=exceptions.DatabaseUnavailable('gone'))
        later = self.ops['later'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.extend([retired, row(OWN_QUEUE, 'broken', row_id=2), row(OWN_QUEUE, 'later', row_id=3)])
        with mock.patch.object(startup_tasks.util_exceptions, 'ignore_exception') as ignored:
            startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, later.state.value)
        self.assertEqual([NoSuchObject, exceptions.DatabaseUnavailable],
                         [type(c.args[1]) for c in ignored.call_args_list])

    def test_failing_to_list_the_claims_is_raised(self):
        # The caller must then not clear claims nobody has checked.
        self.mariadb.list_stuck_work_queue_rows.side_effect = exceptions.DatabaseUnavailable('gone')
        self.assertRaises(exceptions.DatabaseUnavailable,
                          startup_tasks.recover_interrupted_operations, NODE_UUID)

    def test_op_gone_terminal_meanwhile_does_not_stop_the_pass(self):
        self.ops['refuses'] = FakeOp(BaseClusterOperation.STATE_EXECUTING, refuse=True)
        later = self.ops['later'] = FakeOp(BaseClusterOperation.STATE_EXECUTING)
        self.rows.extend([row(OWN_QUEUE, 'refuses'), row(OWN_QUEUE, 'later', row_id=2)])
        startup_tasks.recover_interrupted_operations(NODE_UUID)
        self.assertEqual(BaseClusterOperation.STATE_QUEUED, later.state.value)


class RestartabilityAuditTestCase(base.ShakenFistTestCase):
    """Every cluster operation type declares whether it may be re-run.

    This is the audit of #4656, kept as a test so that a new operation
    type -- or a new task on an existing one -- has to make the same
    decision rather than inheriting a default nobody looked at.

    Restartable, because every task converges on a desired state:

    * artifact_fetch_op, node_blob_op -- both already re-queue
      themselves with defer_with_backoff(), and a fetch which finds the
      blob local returns.
    * imgcache_op -- each run mints a fresh transcode blob uuid, and
      add_transcode() refuses a description the blob already has, so a
      re-run deletes its own attempt. The interrupted run's blob is
      unreferenced and the cluster daemon reaps it.
    * node_aop_op -- returns unless the agent operation is still in
      preflight, and ensure_local() is convergent.
    * node_net_op, net_op, net_ip_op, net_iface_op, net_iface_ip_op,
      net_macaddr_ip_op -- apply the network's current database state to
      this host. (The net_* types are on sf-net's queues, which this
      recovery does not read, but the declaration has to be right if
      that changes.)
    * node_inst_op -- instance_delete is the path the cleaner already
      re-enqueues, and both sweep tasks return unless the instance is
      created. collect_billing_statistics appends one duplicate usage
      event if it is interrupted after recording one, which is a
      repeated sample of a sweep that samples repeatedly, not work
      applied twice.

    Not restartable: see the RESTARTABLE comment on each class.
    """

    EXPECTED_NOT_RESTARTABLE = {
        'node_inst_net_iface_op',
        'node_inst_netdesc_op',
        'node_inst_snap_op',
    }

    def test_the_audit_is_complete(self):
        not_restartable = {
            name for name in OPERATION_NAMES_TO_CLASSES
            if not get_object_class(name).RESTARTABLE}
        self.assertEqual(self.EXPECTED_NOT_RESTARTABLE, not_restartable)

    def test_restartable_is_a_bool_everywhere(self):
        # A truthy non-bool would work here and read as a mistake in the
        # declaration, which is a decision, not a flag.
        for name in OPERATION_NAMES_TO_CLASSES:
            self.assertIsInstance(
                get_object_class(name).RESTARTABLE, bool,
                f'{name} must declare RESTARTABLE as a bool')


class StartupResetTestCase(base.ShakenFistTestCase):
    """startup_tasks() recovers first, then resets the node's own queues."""

    def setUp(self):
        super().setUp()
        self.calls = mock.MagicMock()
        self.config = fake_config = mock.MagicMock()
        fake_config.NODE_UUID = NODE_UUID
        fake_config.NODE_NAME = 'sf-6'
        self.mariadb = self.calls.mariadb
        self.mariadb.get_work_queue_length.return_value = (0, 0, 0)

        for p in [
                mock.patch.object(startup_tasks, 'config', fake_config),
                mock.patch.object(startup_tasks, 'mariadb', self.mariadb),
                mock.patch.object(startup_tasks, 'recover_interrupted_operations',
                                  self.calls.recover),
                mock.patch.object(startup_tasks, 'redacted_config_items', return_value=[]),
                mock.patch.object(startup_tasks, '_resolve_node_uuid'),
                mock.patch.object(startup_tasks, 'Node'),
                mock.patch.object(startup_tasks, 'daemon'),
                mock.patch.object(startup_tasks, 'locks'),
                mock.patch.object(startup_tasks, 'util_general'),
                mock.patch.object(startup_tasks, 'util_concurrency'),
                mock.patch.object(startup_tasks, 'upgrade_blob_datastore'),
                mock.patch.object(startup_tasks, 'restore_instances')]:
            p.start()
            self.addCleanup(p.stop)

    def _resets(self):
        """Map each queue reset to the worker it was narrowed to, or None."""
        resets = {c.args[0]: None
                  for c in self.mariadb.restart_work_queue.call_args_list}
        for c in self.mariadb.restart_work_queue_claims_by.call_args_list:
            resets[c.args[0]] = c.args[1]
        return resets

    def test_resets_queues_named_by_uuid_after_recovering(self):
        startup_tasks.startup_tasks().join(10)

        resets = self._resets()
        self.assertIn(OWN_QUEUE, resets)
        self.assertNotIn('sf-6-clusteroperation-user_facing', resets)

        names = [c[0] for c in self.calls.mock_calls]
        self.assertLess(names.index('recover'), names.index('mariadb.restart_work_queue'))
        self.calls.recover.assert_called_once_with(NODE_UUID)

    def test_own_queues_are_reset_unfiltered(self):
        # Nothing else drains them, so every claim on one is this node's
        # whatever claimed_by says -- including one an older sf-queues wrote
        # over gRPC, which carries the database node's name.
        startup_tasks.startup_tasks().join(10)

        resets = self._resets()
        for queue in (get_node_user_facing_node_queues(NODE_UUID) +
                      get_node_background_node_queues(NODE_UUID)):
            self.assertIsNone(resets[queue])

    def test_shared_queues_are_reset_for_this_node_only(self):
        # #4652: every node's sf-queues drains the any-* queues, so an
        # unfiltered reset there hands a peer's running item to a second
        # worker. Now that claimed_by names the claiming node, this node can
        # clear its own abandoned claims instead of waiting for the reaper.
        startup_tasks.startup_tasks().join(10)

        resets = self._resets()
        for queue in (get_general_user_facing_node_queues() +
                      get_general_background_node_queues()):
            self.assertEqual(NODE_UUID, resets[queue])
        self.assertEqual(10, len(resets))
        self.mariadb.restart_work_queue.assert_has_calls(
            [mock.call(q) for q in get_node_user_facing_node_queues(NODE_UUID)])
        for c in self.mariadb.restart_work_queue.call_args_list:
            self.assertNotIn(c.args[0], get_general_user_facing_node_queues() +
                             get_general_background_node_queues())

    def test_resets_nothing_without_a_node_uuid(self):
        # Not "None-clusteroperation-*", and not the shared queues either.
        self.config.NODE_UUID = None
        startup_tasks.startup_tasks().join(10)

        self.mariadb.restart_work_queue.assert_not_called()
        self.mariadb.restart_work_queue_claims_by.assert_not_called()
        self.calls.recover.assert_not_called()

    def test_claims_which_could_not_be_checked_are_left_to_the_reaper(self):
        # Clearing them unchecked strands any executing operation behind
        # them, as #4543 did. The rest of startup still runs.
        self.calls.recover.side_effect = exceptions.DatabaseUnavailable('gone')
        with mock.patch.object(startup_tasks.util_exceptions, 'ignore_exception'):
            restore = startup_tasks.startup_tasks()
        restore.join(10)

        self.mariadb.restart_work_queue.assert_not_called()
        self.mariadb.restart_work_queue_claims_by.assert_not_called()
        startup_tasks.upgrade_blob_datastore.assert_called_once()
        startup_tasks.restore_instances.assert_called_once()

    def _unreset_warnings(self):
        return [c for c in startup_tasks.LOG.with_fields.call_args_list
                if 'queues' in c.args[0]]

    def test_a_reset_which_fails_does_not_stop_startup(self):
        # Every reset is attempted, the failures are named in one warning,
        # and the blob datastore upgrade and instance restore still run.
        self.mariadb.restart_work_queue.side_effect = \
            exceptions.DatabaseUnavailable('gone')
        self.mariadb.restart_work_queue_claims_by.side_effect = \
            exceptions.DatabaseUnavailable('gone')
        with mock.patch.object(startup_tasks.util_exceptions, 'ignore_exception'), \
                mock.patch.object(startup_tasks, 'LOG'):
            restore = startup_tasks.startup_tasks()
            restore.join(10)
            warnings = self._unreset_warnings()

        self.assertEqual(10, len(self._resets()))
        startup_tasks.upgrade_blob_datastore.assert_called_once()
        startup_tasks.restore_instances.assert_called_once()
        self.assertEqual(1, len(warnings))
        self.assertEqual(10, len(warnings[0].args[0]['queues']))

    def test_a_shared_reset_the_database_cannot_narrow_is_logged(self):
        # An older sf-database leaves the shared claims alone; say so in
        # sf-queues' own log, not just the database layer's.
        self.mariadb.restart_work_queue_claims_by.return_value = False
        with mock.patch.object(startup_tasks, 'LOG'):
            startup_tasks.startup_tasks().join(10)
            warnings = self._unreset_warnings()

        self.assertEqual(1, len(warnings))
        self.assertEqual(
            sorted(get_general_user_facing_node_queues() +
                   get_general_background_node_queues()),
            sorted(warnings[0].args[0]['queues']))

    def test_resets_which_succeed_are_not_logged_as_failures(self):
        self.mariadb.restart_work_queue_claims_by.return_value = True
        with mock.patch.object(startup_tasks, 'LOG'):
            startup_tasks.startup_tasks().join(10)
            self.assertEqual([], self._unreset_warnings())

    def test_a_queue_length_which_cannot_be_read_does_not_stop_startup(self):
        self.mariadb.get_work_queue_length.side_effect = \
            exceptions.DatabaseUnavailable('gone')
        self.mariadb.restart_work_queue_claims_by.return_value = True
        with mock.patch.object(startup_tasks.util_exceptions, 'ignore_exception'), \
                mock.patch.object(startup_tasks, 'LOG'):
            startup_tasks.startup_tasks().join(10)
            self.assertEqual([], self._unreset_warnings())

        startup_tasks.restore_instances.assert_called_once()
