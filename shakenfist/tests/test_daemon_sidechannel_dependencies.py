# Copyright 2019 Michael Still and contributors

import time
import uuid
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.daemons.sidechannel import main as sidechannel
from shakenfist import instance
from shakenfist import mariadb
from shakenfist.operations import agentoperation
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.operations.baseoperation import BaseOperation
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class SidechannelDependencyEvaluationTestCase(base.ShakenFistTestCase):
    """The dispatcher's dependency walk, driven directly.

    _dependencies_met() is called with a bare Monitor rather than a
    running dispatcher: the thread, its generation and its supervisor
    are tested elsewhere, and what matters here is what the walk reads,
    what it writes and what it answers.

    Dependencies are real agent operations in the mocked database rather
    than mocked state reads, because the walk's whole point is that it
    reads a dependency's state row and never the dependency itself --
    the read is the thing under test, so it is the real one. Counting it
    is done through the mocked mariadb.get_state(), filtered by the
    dependency's uuid so the operation's own state reads do not inflate
    the count.
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
            AGENT_OPERATION_DEPENDENCY_WAIT=300,
        )

        for target in ('shakenfist.operations.agentoperation.config',
                       'shakenfist.daemons.sidechannel.main.config'):
            p = mock.patch(target, fake_config)
            p.start()
            self.addCleanup(p.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', self.instance_uuid)
        self.inst = instance.Instance.from_db(self.instance_uuid)

        self.daemon = sidechannel.Monitor.__new__(sidechannel.Monitor)
        self.daemon.terminal_dependency_cache = {}

    # Helpers.
    def _agentop(self, states=None, depends_on=None, runs_after=None):
        op = AgentOperation.new(
            str(uuid.uuid4()), 'unittest', self.instance_uuid,
            [{'command': 'execute', 'commandline': 'true'}],
            depends_on=depends_on, runs_after=runs_after)
        # The state machine is enforced, so reaching a state means
        # walking to it rather than assigning it.
        for step in (states or [AgentOperation.STATE_QUEUED]):
            op.state = step
        return op

    def _dependency(self, states=None, settle_seconds=None):
        """A real dependency operation, and the stored edge naming it."""
        dep_op = self._agentop(states=states)
        return dep_op, self._edge(dep_op.uuid, settle_seconds=settle_seconds)

    def _edge(self, op_uuid, settle_seconds=None):
        return {'op_type': ObjectType.AGENTOPERATION.value,
                'op_uuid': str(op_uuid),
                'settle_seconds': settle_seconds}

    def _missing_edge(self, settle_seconds=None):
        # Submission validated that this operation existed, so an edge
        # which names nothing is one whose operation has since been
        # deleted or hard delete swept.
        return self._edge(str(uuid.uuid4()), settle_seconds=settle_seconds)

    def _anchor(self, op, seconds_ago):
        """Backdate the dependency wait anchor, as a long wait would.

        Written rather than simulated with a frozen clock, because the
        evaluator reads it back through note_dependency_wait_started()
        and so a wait of any length is just a value in that column.
        """
        attrs = op._attributes()
        attrs.dependency_wait_started = time.time() - seconds_ago
        mariadb.update_agent_operation_attributes(
            attrs, fields=['dependency_wait_started'])
        return attrs.dependency_wait_started

    def _stored_anchor(self, op):
        return op._attributes().dependency_wait_started

    def _observed(self, op_uuid, seconds_ago,
                  state=BaseOperation.STATE_COMPLETE):
        """Backdate when the dispatcher first saw a dependency finish.

        A settle delay runs from this rather than from the wait anchor,
        so this is what a test moves to make a settle elapse. Seeding it
        also stands in for the read, which is what the cache is for.
        """
        self.daemon.terminal_dependency_cache[str(op_uuid)] = (
            state, time.time() - seconds_ago)

    def _reads_of(self, op_uuid):
        return [c for c in mariadb.get_state.call_args_list
                if str(c.args[1]) == str(op_uuid)]

    # An operation which declared nothing.
    def test_an_operation_with_no_edges_reads_nothing(self):
        # The cost control the whole design rests on: this walk runs
        # once per agent-ready instance every DISPATCH_CHECK_INTERVAL
        # whether anything in the cluster uses dependencies or not, so
        # the no-edges case must not cost a database read. Calls are
        # counted rather than inspected, because an early return is one
        # if away from being lost.
        agentop = self._agentop()

        mariadb.get_state.reset_mock()
        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertTrue(dispatchable)
        self.assertIsNone(reason)
        self.assertEqual([], mariadb.get_state.call_args_list)

    # depends_on, which is fate sharing.
    def test_a_complete_dependency_dispatches(self):
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE])
        agentop = self._agentop(depends_on=[edge])

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertTrue(dispatchable)
        self.assertIsNone(reason)
        self.assertIsNone(agentop.dependency_failure)
        self.assertIsNone(self._stored_anchor(agentop))

    def test_an_errored_dependency_cascades_and_records_the_state(self):
        dep_op, edge = self._dependency(states=[dbo.STATE_ERROR])
        agentop = self._agentop(depends_on=[edge])

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('error', reason)
        self.assertEqual(BaseOperation.STATE_ABORT, agentop.state.value)
        self.assertEqual(
            {'op_type': ObjectType.AGENTOPERATION.value,
             'op_uuid': str(dep_op.uuid),
             'state': dbo.STATE_ERROR},
            agentop.dependency_failure)

    def test_an_expired_dependency_cascades(self):
        # expired is a failure for this purpose even though the
        # dependency's own commands never failed: whatever this
        # operation was waiting for did not happen.
        _, edge = self._dependency(states=[AgentOperation.STATE_EXPIRED])
        agentop = self._agentop(depends_on=[edge])

        dispatchable, _ = self.daemon._dependencies_met(self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertEqual(BaseOperation.STATE_ABORT, agentop.state.value)
        self.assertEqual(AgentOperation.STATE_EXPIRED,
                         agentop.dependency_failure['state'])

    def test_a_deleted_dependency_cascades_as_deleted_not_missing(self):
        # delete() removes an operation's static and attribute rows and
        # only then assigns deleted, so the state row outlives the
        # object. Reading state rather than hydrating is what lets this
        # be reported as deleted: an evaluator which called from_db()
        # would find nothing and say missing.
        _, edge = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                           dbo.STATE_DELETED])
        agentop = self._agentop(depends_on=[edge])

        dispatchable, _ = self.daemon._dependencies_met(self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertEqual(dbo.STATE_DELETED,
                         agentop.dependency_failure['state'])

    def test_a_missing_dependency_cascades_as_missing(self):
        edge = self._missing_edge()
        agentop = self._agentop(depends_on=[edge])

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('missing', reason)
        self.assertEqual(BaseOperation.STATE_ABORT, agentop.state.value)
        self.assertEqual('missing', agentop.dependency_failure['state'])
        # Not a state any operation holds, which is the point of it.
        self.assertNotIn('missing', AgentOperation.state_targets)

    def test_an_executing_dependency_blocks_without_moving_the_anchor(self):
        # The anchor is written on the first blocked pass and never
        # again. A pass which rewrote it would push the budget forward
        # a few seconds at a time and leave a budget which can never be
        # exhausted, so the second pass is made against a backdated
        # anchor: a second write would show up as the anchor jumping
        # forward to now.
        _, edge = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                           AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(depends_on=[edge])

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)
        self.assertFalse(dispatchable)
        self.assertIn('executing', reason)
        self.assertIsNotNone(self._stored_anchor(agentop))
        self.assertIsNone(agentop.dependency_failure)

        backdated = self._anchor(agentop, 5)
        dispatchable, _ = self.daemon._dependencies_met(self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertEqual(backdated, self._stored_anchor(agentop))
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)

    def test_a_wait_past_the_budget_cascades(self):
        # The analogue of the cluster side's abandon_dependency_wait():
        # the dependency may yet finish, but not inside a budget the
        # instance's other callers can afford to wait out.
        _, edge = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                           AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(depends_on=[edge])
        self._anchor(agentop, 301)

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('gave up', reason)
        self.assertEqual(BaseOperation.STATE_ABORT, agentop.state.value)
        # The live state, not a sentinel: the dependency had not failed,
        # the wait for it had.
        self.assertEqual(AgentOperation.STATE_EXECUTING,
                         agentop.dependency_failure['state'])

    def test_a_give_up_is_audited_as_a_give_up_not_a_failure(self):
        # A budget which ran out is not a dependency which failed, and
        # an operator keys alerts off the event text. The stored state
        # already distinguishes them; the event has to as well.
        _, edge = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                           AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(depends_on=[edge])
        self._anchor(agentop, 301)

        with mock.patch(
                'shakenfist.operations.agentoperation.add_event_multi'
        ) as mock_event:
            self.daemon._dependencies_met(self.inst, agentop)

        texts = [call.args[2] for call in mock_event.call_args_list]
        self.assertIn(agentoperation.CASCADE_EVENT_GAVE_UP, texts)
        self.assertNotIn(agentoperation.CASCADE_EVENT_FAILED, texts)

    def test_a_failed_dependency_is_audited_as_a_failure(self):
        # The other side of the pair, so neither text can drift into
        # covering both cases.
        _, edge = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                           dbo.STATE_ERROR])
        agentop = self._agentop(depends_on=[edge])

        with mock.patch(
                'shakenfist.operations.agentoperation.add_event_multi'
        ) as mock_event:
            self.daemon._dependencies_met(self.inst, agentop)

        texts = [call.args[2] for call in mock_event.call_args_list]
        self.assertIn(agentoperation.CASCADE_EVENT_FAILED, texts)
        self.assertNotIn(agentoperation.CASCADE_EVENT_GAVE_UP, texts)

    def test_a_wait_inside_the_budget_does_not_cascade(self):
        _, edge = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                           AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(depends_on=[edge])
        self._anchor(agentop, 299)

        dispatchable, _ = self.daemon._dependencies_met(self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        self.assertIsNone(agentop.dependency_failure)

    def test_a_settle_delay_blocks_until_it_has_elapsed(self):
        # Measured from when this dispatcher first observed the
        # dependency finish, which is the closest it can get to the
        # dependency's own completion without hydrating it.
        dep_op, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE],
            settle_seconds=30)
        agentop = self._agentop(depends_on=[edge])
        self._observed(dep_op.uuid, 10)

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)
        self.assertFalse(dispatchable)
        self.assertEqual('settling', reason)
        self.assertIsNone(agentop.dependency_failure)

        self._observed(dep_op.uuid, 31)
        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertTrue(dispatchable)
        self.assertIsNone(reason)
        self.assertIsNone(agentop.dependency_failure)

    def test_a_settle_does_not_run_down_while_the_dependency_does(self):
        # The defect this pins. The settle clock was the wait anchor,
        # set on the first pass which found the operation blocked on
        # anything at all, so a dependency still executing at that pass
        # had already spent the whole delay by the time it finished --
        # and a caller who asked the guest to be left alone for thirty
        # seconds after a reboot got no delay whatsoever.
        dep_op, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING],
            settle_seconds=30)
        agentop = self._agentop(depends_on=[edge])

        self.assertFalse(
            self.daemon._dependencies_met(self.inst, agentop)[0])

        # Parked for a long time while the dependency ran, which is the
        # ordinary case rather than a corner: a dependency worth waiting
        # on usually takes longer than the settle after it.
        self._anchor(agentop, 100)
        dep_op.state = BaseOperation.STATE_COMPLETE

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)
        self.assertFalse(dispatchable)
        self.assertEqual('settling', reason)

        # The full delay still has to elapse from the observation.
        self._observed(dep_op.uuid, 31)
        self.assertTrue(
            self.daemon._dependencies_met(self.inst, agentop)[0])

    def test_a_settle_which_outlives_the_budget_cascades(self):
        # The API refuses a settle larger than the budget, so reaching
        # this means an edge stored before that bound existed. Giving up
        # is better than blocking on something which can never elapse.
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE],
            settle_seconds=600)
        agentop = self._agentop(depends_on=[edge])
        self._anchor(agentop, 301)

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('gave up', reason)
        self.assertEqual(BaseOperation.STATE_ABORT, agentop.state.value)

    def test_a_later_depends_on_edge_is_still_evaluated(self):
        # A satisfied edge continues the walk rather than ending it.
        _, met = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE])
        agentop = self._agentop(depends_on=[met, self._missing_edge()])

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('missing', reason)
        self.assertEqual(BaseOperation.STATE_ABORT, agentop.state.value)

    # runs_after, which is ordering only. These are the four places the
    # two lists disagree.
    def test_a_missing_runs_after_dependency_dispatches_with_a_warning(self):
        # Not fatal, for the reason the cluster side gives: an ordering
        # hint whose target has gone must not stop an object being
        # managed.
        edge = self._missing_edge()
        agentop = self._agentop(runs_after=[edge])

        with mock.patch.object(sidechannel, 'add_event_multi') as mock_event:
            dispatchable, reason = self.daemon._dependencies_met(
                self.inst, agentop)

        self.assertTrue(dispatchable)
        self.assertIsNone(reason)
        self.assertIsNone(agentop.dependency_failure)
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        mock_event.assert_called_once()
        self.assertIn('runs_after dependency is missing',
                      mock_event.call_args.args[2])
        self.assertEqual(
            [(ObjectType.AGENTOPERATION, agentop.uuid),
             (ObjectType.INSTANCE, self.inst.uuid)],
            mock_event.call_args.args[1])

    def test_a_missing_runs_after_edge_continues_to_the_next_edge(self):
        # Warn and continue, not warn and return: an unsatisfiable
        # ordering edge must not hide the edge behind it.
        _, pending = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                              AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(runs_after=[self._missing_edge(), pending])

        with mock.patch.object(sidechannel, 'add_event_multi'):
            dispatchable, reason = self.daemon._dependencies_met(
                self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('runs_after', reason)

    def test_a_runs_after_wait_past_the_budget_dispatches(self):
        # The sharpest difference between the two lists: the same
        # condition which cascades a depends_on edge warns and lets the
        # operation run here, and writes no dependency_failure, which
        # belongs to the cascade alone.
        _, edge = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                           AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(runs_after=[edge])
        self._anchor(agentop, 301)

        with mock.patch.object(sidechannel, 'add_event_multi') as mock_event:
            dispatchable, reason = self.daemon._dependencies_met(
                self.inst, agentop)

        self.assertTrue(dispatchable)
        self.assertIsNone(reason)
        self.assertIsNone(agentop.dependency_failure)
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        mock_event.assert_called_once()
        self.assertIn('gave up waiting for runs_after',
                      mock_event.call_args.args[2])

    def test_a_runs_after_wait_inside_the_budget_blocks(self):
        _, edge = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                           AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(runs_after=[edge])
        self._anchor(agentop, 299)

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('runs_after', reason)
        self.assertIsNone(agentop.dependency_failure)

    def test_a_failed_runs_after_dependency_dispatches(self):
        # An ordering edge says "not before" and nothing about the
        # outcome, so every terminal state satisfies it.
        for states in ([dbo.STATE_ERROR],
                       [AgentOperation.STATE_EXPIRED],
                       [AgentOperation.STATE_QUEUED, dbo.STATE_DELETED]):
            _, edge = self._dependency(states=states)
            agentop = self._agentop(runs_after=[edge])

            dispatchable, reason = self.daemon._dependencies_met(
                self.inst, agentop)

            self.assertTrue(dispatchable, states)
            self.assertIsNone(reason)
            self.assertIsNone(agentop.dependency_failure)

    def test_a_runs_after_settle_blocks_until_it_has_elapsed(self):
        # An ordering edge's settle delay is honoured: the field is
        # published and validated on both lists, and "not before" plus
        # a settle is still an ordering statement.
        dep_op, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE],
            settle_seconds=30)
        agentop = self._agentop(runs_after=[edge])
        self._observed(dep_op.uuid, 10)

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)
        self.assertFalse(dispatchable)
        self.assertIn('settling', reason)
        self.assertIsNone(agentop.dependency_failure)

        self._observed(dep_op.uuid, 31)
        self.assertTrue(
            self.daemon._dependencies_met(self.inst, agentop)[0])

    def test_a_runs_after_settle_which_outlives_the_budget_dispatches(self):
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE],
            settle_seconds=600)
        agentop = self._agentop(runs_after=[edge])
        self._anchor(agentop, 301)

        with mock.patch.object(sidechannel, 'add_event_multi') as mock_event:
            dispatchable, _ = self.daemon._dependencies_met(
                self.inst, agentop)

        self.assertTrue(dispatchable)
        self.assertIsNone(agentop.dependency_failure)
        mock_event.assert_called_once()
        self.assertIn('settle', mock_event.call_args.args[2])

    # The terminal dependency cache.
    def test_a_terminal_dependency_is_read_once(self):
        dep_op, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE])
        agentop = self._agentop(depends_on=[edge])

        mariadb.get_state.reset_mock()
        self.assertTrue(
            self.daemon._dependencies_met(self.inst, agentop)[0])
        self.assertEqual(1, len(self._reads_of(dep_op.uuid)))

        self.assertTrue(
            self.daemon._dependencies_met(self.inst, agentop)[0])
        self.assertEqual(1, len(self._reads_of(dep_op.uuid)))
        self.assertEqual(
            [str(dep_op.uuid)],
            list(self.daemon.terminal_dependency_cache.keys()))
        state, observed = self.daemon.terminal_dependency_cache[
            str(dep_op.uuid)]
        self.assertEqual(BaseOperation.STATE_COMPLETE, state)
        self.assertAlmostEqual(time.time(), observed, delta=30)

    def test_a_pending_dependency_is_not_cached(self):
        # Caching a non-terminal observation would mean a blocked
        # operation never unblocks, which is why only terminal states
        # are ever stored: terminal is absorbing and nothing else is.
        dep_op, edge = self._dependency(states=[AgentOperation.STATE_QUEUED,
                                                AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(depends_on=[edge])

        mariadb.get_state.reset_mock()
        self.assertFalse(
            self.daemon._dependencies_met(self.inst, agentop)[0])
        self.assertFalse(
            self.daemon._dependencies_met(self.inst, agentop)[0])

        self.assertEqual({}, self.daemon.terminal_dependency_cache)
        self.assertEqual(2, len(self._reads_of(dep_op.uuid)))

    def test_a_missing_dependency_is_not_cached(self):
        edge = self._missing_edge()
        agentop = self._agentop(depends_on=[edge])

        self.daemon._dependencies_met(self.inst, agentop)

        self.assertEqual({}, self.daemon.terminal_dependency_cache)

    def test_the_cache_evicts_the_oldest_entry(self):
        with mock.patch.object(
                sidechannel, 'TERMINAL_DEPENDENCY_CACHE_SIZE', 2):
            for name in ('first', 'second', 'third'):
                self.daemon._cache_terminal_dependency(
                    name, BaseOperation.STATE_COMPLETE)

        self.assertEqual(['second', 'third'],
                         list(self.daemon.terminal_dependency_cache.keys()))

    def test_the_cache_does_not_grow_when_rewriting_an_entry(self):
        with mock.patch.object(
                sidechannel, 'TERMINAL_DEPENDENCY_CACHE_SIZE', 2):
            for name in ('first', 'second', 'first'):
                self.daemon._cache_terminal_dependency(
                    name, BaseOperation.STATE_COMPLETE)

        self.assertEqual(['first', 'second'],
                         list(self.daemon.terminal_dependency_cache.keys()))

    def test_a_new_dispatcher_generation_starts_with_a_cold_cache(self):
        # The cache's lifetime is one dispatcher generation, because
        # that is the lifetime the generation counter already makes
        # reasonable to reason about.
        self.daemon.dispatcher_generation = 7
        self.daemon.dispatcher_thread = None
        self.daemon.terminal_dependency_cache = {
            'stale': BaseOperation.STATE_COMPLETE}

        with mock.patch.object(sidechannel.threading, 'Thread'):
            self.daemon.start_dispatcher()

        self.assertEqual(8, self.daemon.dispatcher_generation)
        self.assertEqual({}, self.daemon.terminal_dependency_cache)
