# Copyright 2026 Michael Still and contributors
from unittest import mock

from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import CannotDeferUnqueued
from shakenfist.operations.net_op import NetOp
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations import artifact_fetch_op as fetch_schema
from shakenfist.schema.operations import net_op as net_op_schema
from shakenfist.tests import base


OP_UUID = '11111111-1111-4111-8111-111111111111'
NETWORK_UUID = '22222222-2222-4222-8222-222222222222'


class _StubOp(BaseClusterOperation):
    object_type = ObjectType.ARTIFACT_FETCH_OP
    initial_version = 1
    current_version = 1

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, fetch_schema)


def _make_static_values():
    return {
        'uuid': OP_UUID,
        'priority': 'background',
        'request_id': None,
        'depends_on': None,
        'runs_after': None,
        'tasks': ['image_fetch'],
        'version': 1,
    }


def _make_net_op_static_values(tasks):
    return {
        'uuid': OP_UUID,
        'network_uuid': NETWORK_UUID,
        'priority': 'user_facing_high_io',
        'request_id': None,
        'depends_on': None,
        'runs_after': None,
        'tasks': tasks,
        'version': net_op_schema.current_version,
    }


class DeferWithBackoffTestCase(base.ShakenFistTestCase):
    """Tests for the retry-with-backoff helper on cluster operations.

    defer() and defer_with_backoff() touch MariaDB (state, work queue)
    and the eventlog. We patch all three so we can assert purely on the
    payloads handed to mariadb.enqueue_work_item.
    """

    def setUp(self):
        super().setUp()

        self.add_event_patcher = mock.patch.object(
            BaseClusterOperation, 'add_event')
        self.add_event_patcher.start()
        self.addCleanup(self.add_event_patcher.stop)

        self.state_patcher = mock.patch.object(
            BaseClusterOperation, '_state_update')
        self.state_patcher.start()
        self.addCleanup(self.state_patcher.stop)

        self.enqueue_patcher = mock.patch(
            'shakenfist.operations.baseoperation.mariadb.enqueue_work_item')
        self.mock_enqueue = self.enqueue_patcher.start()
        self.addCleanup(self.enqueue_patcher.stop)

    def _make_op(self):
        op = _StubOp(_make_static_values())
        op.queue_name = 'test-queue'
        return op

    def test_defer_without_queue_name_raises(self):
        op = _StubOp(_make_static_values())
        self.assertRaises(CannotDeferUnqueued, op.defer)

    def test_defer_writes_initial_defer_count(self):
        op = self._make_op()
        op.defer(delay=15)

        self.mock_enqueue.assert_called_once()
        args, kwargs = self.mock_enqueue.call_args
        self.assertEqual('test-queue', args[0])
        work_item = args[1]
        self.assertEqual(OP_UUID, work_item['operation_uuid'])
        self.assertEqual(
            ObjectType.ARTIFACT_FETCH_OP, work_item['operation_type'])
        self.assertEqual(1, work_item['defer_count'])
        self.assertEqual(15, kwargs['delay'])

    def test_defer_increments_existing_defer_count(self):
        op = self._make_op()
        op.current_defer_count = 2
        op.defer(delay=42)

        work_item = self.mock_enqueue.call_args.args[1]
        self.assertEqual(3, work_item['defer_count'])

    def test_defer_with_backoff_schedules_first_retry(self):
        op = self._make_op()
        self.assertTrue(op.defer_with_backoff())

        args, kwargs = self.mock_enqueue.call_args
        self.assertEqual(15, kwargs['delay'])
        self.assertEqual(1, args[1]['defer_count'])

    def test_defer_with_backoff_uses_default_schedule_in_order(self):
        delays_seen = []
        for n in range(3):
            op = self._make_op()
            op.current_defer_count = n
            self.mock_enqueue.reset_mock()
            self.assertTrue(op.defer_with_backoff())
            delays_seen.append(self.mock_enqueue.call_args.kwargs['delay'])
        self.assertEqual([15, 30, 60], delays_seen)

    def test_defer_with_backoff_exhausts_budget(self):
        op = self._make_op()
        op.current_defer_count = 3

        self.assertFalse(op.defer_with_backoff(reason='upstream 503'))
        self.mock_enqueue.assert_not_called()

    def test_defer_with_backoff_honours_custom_delays(self):
        op = self._make_op()
        op.current_defer_count = 1

        self.assertTrue(op.defer_with_backoff(delays=(5, 10, 20, 40)))
        self.assertEqual(10, self.mock_enqueue.call_args.kwargs['delay'])

    def test_current_defer_count_starts_at_zero(self):
        # The dispatcher relies on this being a sensible default for
        # ops loaded outside the queue dispatch path.
        op = self._make_op()
        self.assertEqual(0, op.current_defer_count)


class CoalescingExecuteTestCase(base.ShakenFistTestCase):
    """Tests for the within-job and cross-op coalescing branches in
    ``BaseClusterOperation.execute``.

    The two passes are wired in ``execute()`` itself rather than the
    dispatcher, so a unit test against a real NetOp class is enough --
    no need to stand up either of the daemon workers.
    """

    def setUp(self):
        super().setUp()

        # State writes hit MariaDB; the test only cares about the
        # behavioural surface (tasks dispatched, events emitted,
        # claim_coalescible_siblings called with the right shape).
        self.state_patcher = mock.patch.object(
            BaseClusterOperation, '_state_update')
        self.state_patcher.start()
        self.addCleanup(self.state_patcher.stop)
        self.state_value_patcher = mock.patch.object(
            BaseClusterOperation, 'state',
            new_callable=mock.PropertyMock)
        mock_state = self.state_value_patcher.start()
        # The post-dispatch loop in execute() short-circuits if
        # state.value is in {ABORT, DELETED, ERROR, QUEUED}. We want
        # the loop to run every task, so return EXECUTING -- the
        # value execute() itself set before iterating.
        mock_state.return_value = mock.MagicMock(
            value=BaseClusterOperation.STATE_EXECUTING)
        self.addCleanup(self.state_value_patcher.stop)

        self.add_event_patcher = mock.patch.object(
            BaseClusterOperation, 'add_event')
        self.mock_add_event = self.add_event_patcher.start()
        self.addCleanup(self.add_event_patcher.stop)

        # The cross-op fold emits through eventlog.add_event_multi
        # rather than the object's own add_event, so that the event
        # lands on the coalescing target as well as the survivor.
        self.add_event_multi_patcher = mock.patch(
            'shakenfist.operations.baseoperation.eventlog.add_event_multi')
        self.mock_add_event_multi = self.add_event_multi_patcher.start()
        self.addCleanup(self.add_event_multi_patcher.stop)

        self.claim_patcher = mock.patch(
            'shakenfist.operations.baseoperation.mariadb.'
            'claim_coalescible_siblings')
        self.mock_claim = self.claim_patcher.start()
        self.mock_claim.return_value = []
        self.addCleanup(self.claim_patcher.stop)

    def _make_net_op(self, task_names, queue_name=None):
        op = NetOp(_make_net_op_static_values(task_names))
        # ``dispatch_task`` is wired by the concrete op; patch it so
        # we observe which tasks would actually run.
        op.dispatch_task = mock.MagicMock()
        # The cross-op fold only runs when the op was dequeued from a
        # cluster-wide ``networknode-*`` queue. Default the tests to
        # one of those so the legacy assertions still exercise the
        # fold; tests that want to exercise the skip path pass an
        # explicit per-node ``queue_name``.
        if queue_name is None:
            queue_name = 'networknode-clusteroperation-user_facing'
        op.queue_name = queue_name
        return op

    def test_within_job_drops_duplicate_coalescible_tasks(self):
        # update_dnsmasq is coalescible, so [update, update, update]
        # collapses to one dispatch.
        op = self._make_net_op([
            'network_apply_update_dnsmasq',
            'network_apply_update_dnsmasq',
            'network_apply_update_dnsmasq',
        ])
        op.execute()

        self.assertEqual(1, op.dispatch_task.call_count)
        # Two "dropped duplicate" events for the two skipped tasks.
        drop_events = [
            c for c in self.mock_add_event.call_args_list
            if c.args[1].startswith('within-job: dropped duplicate')]
        self.assertEqual(2, len(drop_events))

    def test_within_job_does_not_drop_non_coalescible_duplicates(self):
        # network_remove_dnsmasq is *not* coalescible -- a repeat would
        # be intentional, so the loop runs it twice.
        op = self._make_net_op([
            'network_remove_dnsmasq',
            'network_remove_dnsmasq',
        ])
        op.execute()

        self.assertEqual(2, op.dispatch_task.call_count)

    def test_cross_op_coalescing_calls_mariadb_with_target(self):
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.execute()

        self.mock_claim.assert_called_once()
        kwargs = self.mock_claim.call_args.kwargs
        self.assertEqual(ObjectType.NET_OP, kwargs['operation_type'])
        self.assertEqual('network_uuid', kwargs['target_column'])
        self.assertEqual(NETWORK_UUID, kwargs['target_uuid'])
        self.assertEqual(
            ['network_apply_update_dnsmasq'], kwargs['task_names'])
        self.assertEqual(OP_UUID, kwargs['exclude_op_uuid'])

    def _coalesced_events(self):
        return [
            c for c in self.mock_add_event_multi.call_args_list
            if c.args[2] == 'coalesced sibling ops']

    def test_cross_op_coalescing_records_sibling_uuids(self):
        sibling = '33333333-3333-4333-8333-333333333333'
        self.mock_claim.return_value = [sibling]

        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.execute()

        coalesced_events = self._coalesced_events()
        self.assertEqual(1, len(coalesced_events))
        extra = coalesced_events[0].kwargs['extra']
        self.assertEqual(1, extra['sibling_count'])
        self.assertEqual([sibling], extra['sibling_uuids'])

    def test_fold_event_lands_on_the_target_as_well(self):
        # The survivor operation is hard deleted thirty seconds after
        # it completes and takes its event_objects rows with it
        # (#3864), so an event recorded only against the operation is
        # unreadable almost immediately -- which is how #3878 hid. The
        # network outlives the operation, so the event has to land
        # there too for anything to be able to assert on it.
        self.mock_claim.return_value = [
            '33333333-3333-4333-8333-333333333333']

        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.execute()

        coalesced_events = self._coalesced_events()
        self.assertEqual(1, len(coalesced_events))
        references = coalesced_events[0].args[1]
        self.assertIn((str(ObjectType.NET_OP), OP_UUID), references)
        self.assertIn(('network', NETWORK_UUID), references)

    def test_fold_target_object_type_comes_from_the_schema(self):
        # Resolved through the schema model's target_fields map rather
        # than hard-coded, so the multi-column key in #3884 extends the
        # map instead of adding a special case here.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        self.assertEqual(
            ('network', NETWORK_UUID),
            op._coalescible_target_reference())

    def test_target_reference_is_none_without_a_target_column(self):
        # An operation type which declares no coalescing target at all.
        # The fold cannot run for it, but the helper is public enough
        # that it must degrade rather than raise.
        op = _StubOp(_make_static_values())
        self.assertIsNone(op._coalescible_target_reference())

    def test_target_reference_is_none_when_the_target_is_unset(self):
        # network_uuid is a read only property, so this stands in for an
        # operation loaded with no target recorded against it.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        with mock.patch.object(
                NetOp, 'network_uuid',
                new_callable=mock.PropertyMock, return_value=None):
            self.assertIsNone(op._coalescible_target_reference())

    def test_target_reference_is_none_when_the_schema_omits_the_column(self):
        # A column the schema's target_fields map does not name. The
        # event then lands on the operation alone: it loses its
        # durability past the operation's thirty second hard delete
        # (#3864), but nothing raises and the fold still happens.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        with mock.patch.object(
                net_op_schema.model, 'target_fields', {}):
            self.assertIsNone(op._coalescible_target_reference())

    def test_no_fold_event_when_nothing_was_folded(self):
        self.mock_claim.return_value = []

        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.execute()

        self.assertEqual([], self._coalesced_events())

    def test_no_coalescing_call_when_task_not_coalescible(self):
        # network_remove_dnsmasq isn't in COALESCIBLE_TASKS, so the
        # mariadb call must not happen at all.
        op = self._make_net_op(['network_remove_dnsmasq'])
        op.execute()

        self.mock_claim.assert_not_called()

    def test_no_coalescing_call_when_dispatcher_batch_was_one(self):
        # Dispatcher hint says we were the only item in the batch, so
        # no sibling can possibly be ready. Skip the SQL round-trip.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.dispatcher_batch_size = 1
        op.execute()

        self.mock_claim.assert_not_called()

    def test_coalescing_runs_when_dispatcher_batch_was_multiple(self):
        # Dispatcher hint says >1 ready items, so contention is
        # possible -- the fold runs.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.dispatcher_batch_size = 5
        op.execute()

        self.mock_claim.assert_called_once()

    def test_coalescing_runs_when_dispatcher_batch_unknown(self):
        # ``None`` means the op was loaded outside the queue path
        # (e.g. a unit test or REST endpoint); be conservative and
        # run the fold.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.dispatcher_batch_size = None
        op.execute()

        self.mock_claim.assert_called_once()

    def test_no_coalescing_call_when_queue_is_per_node(self):
        # Per-node queues (``<node_uuid>-network-*`` and
        # ``<node_uuid>-clusteroperation-*``) MUST NOT fold across
        # sibling ops, because the fold query keys on
        # (op_type, target_uuid, task) and a sibling on a different
        # node's queue is doing different work (e.g. mesh apply on
        # hypervisor B vs hypervisor A). Folding would mark B's op
        # complete without ever running it. See the comment in
        # ``BaseClusterOperation.execute`` for the full story.
        op = self._make_net_op(
            ['network_apply_update_dnsmasq'],
            queue_name=(
                '11111111-1111-4111-8111-111111111111'
                '-network-user_facing'))
        op.execute()

        self.mock_claim.assert_not_called()

    def test_no_coalescing_call_when_queue_name_unset(self):
        # An op loaded outside the dispatch path (e.g. by a unit test
        # that doesn't set ``queue_name``) has ``None`` for the queue
        # and we can't tell whether the fold would be safe. The
        # conservative choice is to skip it -- the fold is a cost
        # optimisation, not a correctness requirement.
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.queue_name = None
        op.execute()

        self.mock_claim.assert_not_called()

    def test_outcome_records_that_the_fold_ran(self):
        # "The fold ran and found nothing" has to be distinguishable
        # from "the fold never ran". While the join was broken (#3878)
        # those two looked identical from outside, which is why the
        # defect survived three months and a green test suite.
        self.mock_claim.return_value = []

        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.execute()

        self.assertEqual('ran', op.coalesce_outcome)
        self.assertEqual(0, op.coalesce_folded)
        self.assertIsNotNone(op.coalesce_seconds)

    def test_outcome_records_how_many_siblings_were_folded(self):
        self.mock_claim.return_value = [
            '33333333-3333-4333-8333-333333333333',
            '44444444-4444-4444-8444-444444444444',
        ]

        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.execute()

        self.assertEqual('ran', op.coalesce_outcome)
        self.assertEqual(2, op.coalesce_folded)

    def test_outcome_records_the_batch_size_guard(self):
        op = self._make_net_op(['network_apply_update_dnsmasq'])
        op.dispatcher_batch_size = 1
        op.execute()

        self.assertEqual('batch_size_one', op.coalesce_outcome)
        self.assertIsNone(op.coalesce_seconds)

    def test_outcome_records_the_per_node_queue_guard(self):
        op = self._make_net_op(
            ['network_apply_update_dnsmasq'],
            queue_name=(
                '11111111-1111-4111-8111-111111111111'
                '-network-user_facing'))
        op.execute()

        self.assertEqual('not_cluster_wide', op.coalesce_outcome)
        self.assertIsNone(op.coalesce_seconds)

    def test_outcome_records_a_job_with_nothing_coalescible(self):
        op = self._make_net_op(['network_remove_dnsmasq'])
        op.execute()

        self.assertEqual('no_coalescible_tasks', op.coalesce_outcome)
        self.assertIsNone(op.coalesce_seconds)

    def test_a_type_which_cannot_coalesce_is_a_different_outcome(self):
        # 'this operation type declares no coalescing at all' is the
        # overwhelming majority of cluster operations, and reporting it
        # as 'no_coalescible_tasks' would bury the interesting case --
        # a type which could have coalesced and did not -- underneath
        # it in the by-queue-class table.
        op = _StubOp(_make_static_values())
        op.dispatch_task = mock.MagicMock()
        op.queue_name = 'networknode-clusteroperation-user_facing'
        op.dispatcher_batch_size = 4
        op.execute()

        self.mock_claim.assert_not_called()
        self.assertEqual('type_not_coalescible', op.coalesce_outcome)
        self.assertIsNone(op.coalesce_seconds)

    def test_outcome_survives_a_fold_which_raised(self):
        # Recorded before the call rather than after it. A caller which
        # catches and continues would otherwise emit an event with no
        # outcome, which the report classifies as an uninstrumented
        # build rather than as a fold which was attempted and failed.
        self.mock_claim.side_effect = Exception('database unavailable')

        op = self._make_net_op(['network_apply_update_dnsmasq'])
        self.assertRaises(Exception, op.execute)

        self.assertEqual('ran', op.coalesce_outcome)
        self.assertIsNone(op.coalesce_seconds)
        self.assertIsNone(op.coalesce_folded)

    def test_emptying_the_coalescible_set_silences_both_signals(self):
        # The mutation the functional test in
        # cluster_ci_tests/test_coalescing.py is calibrated against: with
        # nothing declared coalescible, the fold never runs and the
        # 'coalesced sibling ops' event never fires, so a cluster run
        # asserting on that event must fail. Proving it here means the
        # calibration does not depend on someone remembering to try it by
        # hand against a real cluster.
        self.mock_claim.return_value = [
            '33333333-3333-4333-8333-333333333333']

        with mock.patch.object(NetOp, 'coalescible_tasks', frozenset()):
            op = self._make_net_op(['network_apply_update_dnsmasq'])
            op.execute()

        self.mock_claim.assert_not_called()
        self.assertEqual([], self._coalesced_events())
        self.assertEqual('type_not_coalescible', op.coalesce_outcome)


class ExecutionDurationExtraTestCase(base.ShakenFistTestCase):
    """The end-of-op event payload both dispatchers emit.

    It is built on the operation rather than in each dispatcher so the
    two cannot drift apart on field names: tools/queue-wait-report.py
    reads a single stream carrying events from both, and a field only
    one of them spells correctly silently halves whatever it measures.
    """

    def _make_op(self, created_at=None):
        static_values = _make_net_op_static_values(
            ['network_apply_update_dnsmasq'])
        op = NetOp(static_values)
        op._BaseClusterOperation__created_at = created_at
        return op

    def test_queue_fields_omitted_without_created_at(self):
        # An op constructed outside the dispatch path has no insert
        # time, so there is no queue wait to report.
        extra = self._make_op().execution_duration_extra(0.0, 'a-queue')

        self.assertIn('seconds', extra)
        self.assertNotIn('wait_seconds', extra)
        self.assertNotIn('queue_name', extra)

    def test_queue_fields_present_with_created_at(self):
        op = self._make_op(created_at=100.0)
        op.current_defer_count = 3

        extra = op.execution_duration_extra(160.0, 'a-queue')

        self.assertEqual(60.0, extra['wait_seconds'])
        self.assertEqual(3, extra['defer_count'])
        self.assertEqual('a-queue', extra['queue_name'])

    def test_coalescing_fields_omitted_when_unset(self):
        # Events from a build predating the instrumentation carry none
        # of these, and the report tool has to be able to tell that
        # apart from a zero.
        extra = self._make_op().execution_duration_extra(0.0, None)

        self.assertNotIn('coalesce_outcome', extra)
        self.assertNotIn('coalesce_seconds', extra)
        self.assertNotIn('coalesce_folded', extra)

    def test_coalescing_fields_reported_when_the_fold_ran(self):
        op = self._make_op()
        op.coalesce_outcome = 'ran'
        op.coalesce_seconds = 0.25
        op.coalesce_folded = 0

        extra = op.execution_duration_extra(0.0, None)

        self.assertEqual('ran', extra['coalesce_outcome'])
        self.assertEqual(0.25, extra['coalesce_seconds'])
        # Zero is a real measurement -- "ran and folded nothing" -- and
        # must survive into the payload rather than being dropped as
        # falsey.
        self.assertEqual(0, extra['coalesce_folded'])

    def test_skip_reason_reported_without_a_duration(self):
        op = self._make_op()
        op.coalesce_outcome = 'batch_size_one'

        extra = op.execution_duration_extra(0.0, None)

        self.assertEqual('batch_size_one', extra['coalesce_outcome'])
        self.assertNotIn('coalesce_seconds', extra)
