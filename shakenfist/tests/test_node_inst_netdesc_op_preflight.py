# Copyright 2019 Michael Still and contributors
"""Regression tests for ``NodeInstNetdescOp`` preflight redirects.

The reschedule branch of ``_instance_preflight`` used to call
``NodeInstNetdescOp.new()``, which does not exist -- cluster operations
are created in database transactions and only the schema-layer
``create_and_enqueue`` helper can mint one. The resulting
AttributeError was swallowed by ``dispatch_task``'s generic exception
handler, which then blindly assigned ``STATE_ERROR`` to an operation
already in ``abort`` and raised InvalidStateException out of the
handler, killing the queue worker thread (observed during the first
in-flight sfcbr upgrade on 2026-07-12). These tests pin both fixes:
the redirect must go via ``schema.create_and_enqueue``, and the error
handler must tolerate operations already in a terminal state.
"""

from unittest import mock
from uuid import uuid4

from shakenfist import exceptions
from shakenfist.operations.node_inst_netdesc_op import AbortInstanceStart
from shakenfist.operations.node_inst_netdesc_op import NodeInstNetdescOp
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.node_inst_netdesc_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_netdesc_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class FakeInstance:
    """An instance double with a state machine we control."""

    def __init__(self, state_value, reject=(), deny=(), deny_demand=(),
                 fetch_dependencies=None):
        self._state_value = state_value
        self._reject = reject
        # Nodes whose capacity guard refuses this instance, as the
        # admission RPC would (P2/D7). ``deny`` refuses on a real
        # dimension in every mode; ``deny_demand`` refuses on the D13
        # demand term alone, so it admits when the walker's second pass
        # waives that clause.
        self._deny = set(deny)
        self._deny_demand = set(deny_demand)
        self.state_sets = []
        self.delete_errors = []
        self.placement = {'placement_attempts': 0}
        self.requested_placement = None
        self.placed_on = []
        self.fetch_dependencies = fetch_dependencies or []
        self.disk_fetch_calls = []
        self.placement_attempts = []

    @property
    def state(self):
        return State(value=self._state_value, update_time=1.0)

    @state.setter
    def state(self, new_value):
        if new_value in self._reject:
            raise exceptions.InvalidStateException(
                'Invalid state change from %s to %s'
                % (self._state_value, new_value))
        self.state_sets.append(new_value)
        self._state_value = new_value

    def add_event(self, *args, **kwargs):
        pass

    def enqueue_delete_due_error(self, message):
        self.delete_errors.append(message)

    def place_instance(self, node, enforce_demand=True):
        self.placement_attempts.append(node)
        if node in self._deny:
            raise exceptions.CapacityAdmissionDenied(
                'node', [{'dimension': 'cpus', 'limit': 16.0, 'used': 16.0,
                          'requested': 1.0, 'exceeded': True}])
        if enforce_demand and node in self._deny_demand:
            raise exceptions.CapacityAdmissionDenied(
                'node', [{'dimension': 'cpus', 'limit': 16.0, 'used': 0.0,
                          'requested': 1.0, 'exceeded': False},
                         {'dimension': 'demand', 'limit': 6.0, 'used': 9.2,
                          'requested': 2.5, 'exceeded': True}])
        self.placed_on.append(node)

    def enqueue_disk_fetches(self, target_node, priority, request_id=None,
                             artifact_event=None):
        self.disk_fetch_calls.append(
            (target_node, priority, request_id, artifact_event))
        return self.fetch_dependencies


class PreflightRedirectTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _make_op(self):
        _, op_uuid = create_and_enqueue(
            node_uuid=str(uuid4()),
            instance_uuid=str(uuid4()),
            net_desc=[],
            tasks=[model_tasks.instance_preflight,
                   model_tasks.instance_start],
            priority=PRIORITY.user_waiting,
        )
        op = NodeInstNetdescOp.from_db(op_uuid)
        self.assertIsNotNone(op)
        op.state = NodeInstNetdescOp.STATE_EXECUTING
        return op

    def _redirect(self, inst, candidates):
        """Run a preflight whose local placement fails, over `candidates`."""
        op = self._make_op()
        fake_scheduler = mock.MagicMock()
        fake_scheduler.find_candidates.side_effect = [
            exceptions.LowResourceException('full here'),
            list(candidates),
        ]
        fake_scheduler.metrics = {c: {} for c in candidates}

        with mock.patch(
                'shakenfist.operations.node_inst_netdesc_op.scheduler.'
                'Scheduler', return_value=fake_scheduler):
            with mock.patch(
                    'shakenfist.operations.node_inst_netdesc_op.schema.'
                    'create_and_enqueue') as mock_enqueue:
                with mock.patch(
                        'shakenfist.operations.node_inst_netdesc_op.'
                        'add_event_multi'):
                    try:
                        op._instance_preflight(inst)
                        raised = None
                    except AbortInstanceStart as e:
                        raised = e
        return op, mock_enqueue, raised

    def test_redirect_uses_schema_create_and_enqueue(self):
        inst = FakeInstance('created')
        target_node = str(uuid4())

        op, mock_enqueue, raised = self._redirect(inst, [target_node])

        self.assertIsNone(raised)
        mock_enqueue.assert_called_once_with(
            target_node, op.instance_uuid, op.net_desc, op.tasks,
            op.priority, op.request_id, depends_on=None)
        self.assertEqual([target_node], inst.placed_on)
        self.assertEqual(NodeInstNetdescOp.STATE_ABORT, op.state.value)

    def test_redirect_enqueues_fetches_for_new_node(self):
        # The artifact fetches minted at create time targeted the original
        # placement, so a redirect must mint fresh fetches against the new
        # node and gate the redirected start on them -- otherwise the new
        # node's image cache is never populated and instance create raises
        # ImageMissingFromCache (issue 3720). Since the pick-then-claim
        # walk, the fetches must target the node the capacity guard
        # actually admitted, not merely the scheduler's first preference.
        fetch_deps = [dependency(op_type=ObjectType.ARTIFACT_FETCH_OP,
                                 op_uuid=str(uuid4()))]
        denied = str(uuid4())
        admitted = str(uuid4())
        inst = FakeInstance('created', deny=[denied],
                            fetch_dependencies=fetch_deps)

        op, mock_enqueue, raised = self._redirect(inst, [denied, admitted])

        self.assertIsNone(raised)
        self.assertEqual(
            [(admitted, op.priority, op.request_id,
              'fetch requested by instance start redirect')],
            inst.disk_fetch_calls)
        mock_enqueue.assert_called_once_with(
            admitted, op.instance_uuid, op.net_desc, op.tasks,
            op.priority, op.request_id, depends_on=fetch_deps)

    def test_redirect_walks_past_a_denied_candidate(self):
        # The scheduler's candidate list is ordered by preference, but
        # the capacity guard is what admits: a refusal from the head of
        # the list is a genuine reschedule onto the next candidate, not
        # a failure (D7).
        first = str(uuid4())
        second = str(uuid4())
        inst = FakeInstance('created', deny=[first])

        op, mock_enqueue, raised = self._redirect(inst, [first, second])

        self.assertIsNone(raised)
        self.assertEqual([first, second], inst.placement_attempts)
        self.assertEqual([second], inst.placed_on)
        mock_enqueue.assert_called_once_with(
            second, op.instance_uuid, op.net_desc, op.tasks,
            op.priority, op.request_id, depends_on=None)
        self.assertEqual(NodeInstNetdescOp.STATE_ABORT, op.state.value)

    def test_demand_only_refusals_are_waived_on_a_second_pass(self):
        # The D13 demand feedforward spreads bursts across nodes; when
        # the redirect walk admits nowhere and the refusals were on
        # demand alone, it re-walks with the clause waived rather than
        # aborting a start the cluster has real capacity for.
        target = str(uuid4())
        inst = FakeInstance('created', deny_demand=[target])

        op, mock_enqueue, raised = self._redirect(inst, [target])

        self.assertIsNone(raised)
        # Two attempts: the enforced walk, then the waived one.
        self.assertEqual([target, target], inst.placement_attempts)
        self.assertEqual([target], inst.placed_on)
        mock_enqueue.assert_called_once_with(
            target, op.instance_uuid, op.net_desc, op.tasks,
            op.priority, op.request_id, depends_on=None)
        self.assertEqual(NodeInstNetdescOp.STATE_ABORT, op.state.value)

    def test_the_waiver_reaches_past_a_genuinely_full_node(self):
        # Mixed exhaustion: one node full on real capacity, another
        # refused on demand alone. The second pass must run, and only
        # the demand refusal is waivable.
        full = str(uuid4())
        demand_hot = str(uuid4())
        inst = FakeInstance('created', deny=[full],
                            deny_demand=[demand_hot])

        op, mock_enqueue, raised = self._redirect(inst, [full, demand_hot])

        self.assertIsNone(raised)
        self.assertEqual(
            [full, demand_hot, full, demand_hot],
            inst.placement_attempts)
        self.assertEqual([demand_hot], inst.placed_on)
        mock_enqueue.assert_called_once_with(
            demand_hot, op.instance_uuid, op.net_desc, op.tasks,
            op.priority, op.request_id, depends_on=None)

    def test_requested_placement_aborts_rather_than_redirects(self):
        # A targeted create is honoured-or-error: when the requested
        # node cannot take the instance at preflight, the create must
        # abort rather than fall through to open scheduling on another
        # node (issue 3496). The production guard reads the node uuid
        # string _db_create stored; it fires on any truthy value.
        inst = FakeInstance('created')
        inst.requested_placement = str(uuid4())
        other_node = str(uuid4())

        _, mock_enqueue, raised = self._redirect(inst, [other_node])

        self.assertIsNotNone(raised)
        self.assertIn('Requested node lacks resources', str(raised))
        self.assertEqual([], inst.placement_attempts)
        self.assertEqual([], inst.placed_on)
        mock_enqueue.assert_not_called()

    def test_every_candidate_denied_aborts_the_start(self):
        # An exhausted candidate list means the cluster really is full,
        # which is the same outcome as the scheduler finding no
        # candidates at all -- and it must not enqueue work anywhere.
        first = str(uuid4())
        second = str(uuid4())
        inst = FakeInstance('created', deny=[first, second])

        _, mock_enqueue, raised = self._redirect(inst, [first, second])

        self.assertIsNotNone(raised)
        self.assertIn('Unable to find suitable node', str(raised))
        self.assertEqual([first, second], inst.placement_attempts)
        self.assertEqual([], inst.placed_on)
        mock_enqueue.assert_not_called()
        self.assertEqual([], inst.disk_fetch_calls)

    def _redirect_raising(self, inst, exc, candidates=()):
        """As _redirect(), but choosing what the local placement raised.

        The three aborts below are reached after the ``except
        LowResourceException as e:`` suite has exited, so they cannot
        read ``e`` -- Python deletes the ``as`` target when the suite
        ends (PEP 3110), and reading it there is a NameError. They test
        an ``affinity_failure`` flag captured inside the suite instead.
        That flag is only exercised in the queue daemon, which runs
        under cluster CI and not on a pull request, so these tests are
        the only thing between a mistake in it and the merge queue.
        """
        op = self._make_op()
        fake_scheduler = mock.MagicMock()
        fake_scheduler.find_candidates.side_effect = [
            exc, list(candidates)]
        fake_scheduler.metrics = {c: {} for c in candidates}

        with mock.patch(
                'shakenfist.operations.node_inst_netdesc_op.scheduler.'
                'Scheduler', return_value=fake_scheduler):
            with mock.patch(
                    'shakenfist.operations.node_inst_netdesc_op.schema.'
                    'create_and_enqueue'):
                with mock.patch(
                        'shakenfist.operations.node_inst_netdesc_op.'
                        'add_event_multi'):
                    try:
                        op._instance_preflight(inst)
                        return None
                    except AbortInstanceStart as e:
                        return e

    def test_attempt_limit_abort_names_affinity(self):
        # The ":166" guard. Without the flag this abort says "Too many
        # start attempts", which reads as a busy cluster to an operator
        # whose constraint simply cannot be met anywhere.
        inst = FakeInstance('created')
        inst.placement = {'placement_attempts': 4}

        raised = self._redirect_raising(
            inst,
            exceptions.AffinityConstraintUnsatisfiable(
                'no node carries require_with_tag=[\'database\']'))

        self.assertIsNotNone(raised)
        self.assertIn('affinity constraints', str(raised))
        self.assertIn('require_with_tag', str(raised))

    def test_attempt_limit_abort_still_says_attempts_for_capacity(self):
        inst = FakeInstance('created')
        inst.placement = {'placement_attempts': 4}

        raised = self._redirect_raising(
            inst, exceptions.LowResourceException('full here'))

        self.assertIsNotNone(raised)
        self.assertIn('Too many start attempts', str(raised))
        self.assertNotIn('affinity', str(raised))

    def test_requested_placement_abort_names_affinity(self):
        # The ":170" guard. An operator who pinned a node is otherwise
        # told it lacks resources when what it lacks is a matching tag.
        inst = FakeInstance('created')
        inst.requested_placement = str(uuid4())

        raised = self._redirect_raising(
            inst,
            exceptions.AffinityConstraintUnsatisfiable(
                'node carries require_without_tag=[\'batch\']'))

        self.assertIsNotNone(raised)
        self.assertIn('affinity constraints', str(raised))
        self.assertNotIn('lacks resources', str(raised))

    def test_requested_placement_abort_still_says_resources_for_capacity(self):
        inst = FakeInstance('created')
        inst.requested_placement = str(uuid4())

        raised = self._redirect_raising(
            inst, exceptions.LowResourceException('full here'))

        self.assertIsNotNone(raised)
        self.assertIn('Requested node lacks resources', str(raised))

    def test_the_redirect_itself_is_unchanged_by_the_subclass(self):
        # AffinityConstraintUnsatisfiable subclasses
        # LowResourceException precisely so this keeps working: another
        # node may satisfy a constraint this one does not, so preflight
        # must still redirect rather than escaping as a traceback.
        inst = FakeInstance('created')
        target_node = str(uuid4())

        raised = self._redirect_raising(
            inst, exceptions.AffinityConstraintUnsatisfiable('not here'),
            candidates=[target_node])

        self.assertIsNone(raised)
        self.assertEqual([target_node], inst.placed_on)

    def _dispatch_failing_preflight(self, op):
        inst = FakeInstance('created')
        with mock.patch(
                'shakenfist.operations.node_inst_netdesc_op.util_exceptions.'
                'ignore_exception'):
            with mock.patch(
                    'shakenfist.operations.node_inst_netdesc_op.Instance.'
                    'from_db', return_value=inst):
                with mock.patch(
                        'shakenfist.operations.node_inst_netdesc_op.'
                        'NodeInstNetdescOp._instance_preflight',
                        side_effect=RuntimeError('boom')):
                    op.dispatch_task(model_tasks.instance_preflight)
        return inst

    def test_failed_task_sets_operation_error(self):
        op = self._make_op()
        inst = self._dispatch_failing_preflight(op)

        self.assertEqual(NodeInstNetdescOp.STATE_ERROR, op.state.value)
        self.assertEqual(['Unhandled error: boom'], inst.delete_errors)

    def test_failed_task_tolerates_aborted_operation(self):
        # An operation already in abort (for example from a previous
        # execution of the same work item) cannot legally transition to
        # error. The handler must not raise out of the worker thread.
        op = self._make_op()
        op.state = NodeInstNetdescOp.STATE_ABORT

        inst = self._dispatch_failing_preflight(op)

        self.assertEqual(NodeInstNetdescOp.STATE_ABORT, op.state.value)
        self.assertEqual(['Unhandled error: boom'], inst.delete_errors)
