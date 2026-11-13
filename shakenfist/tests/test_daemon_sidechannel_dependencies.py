# Copyright 2019 Michael Still and contributors

import time
import uuid
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import constants
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
    is done through the mocked mariadb.get_state_checked(), filtered by the
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
        self.daemon.runs_after_warnings = set()

    # Helpers.
    def _agentop(self, states=None, depends_on=None, runs_after=None,
                 commands=None):
        # execute by default, because that is what the dependency walk
        # is usually asked about. A test about retries passes a
        # retryable command instead: execute is not one, deliberately,
        # since a second attempt would repeat a side effect the agent
        # cannot take back.
        op = AgentOperation.new(
            str(uuid.uuid4()), 'unittest', self.instance_uuid,
            commands or [{'command': 'execute', 'commandline': 'true'}],
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
        return [c for c in mariadb.get_state_checked.call_args_list
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

        mariadb.get_state_checked.reset_mock()
        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertTrue(dispatchable)
        self.assertIsNone(reason)
        self.assertEqual(
            [], mariadb.get_state_checked.call_args_list)

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
        # The bound on a settling operation is the budget plus the
        # declared settle, so reaching this means a hold which outlived
        # even that: a dependency observed finished after the budget had
        # already run down, or a settle restarted often enough by a lost
        # observation to outlast it. Giving up beats blocking forever.
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE],
            settle_seconds=600)
        agentop = self._agentop(depends_on=[edge])
        self._anchor(agentop, 901)

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
        # Budget plus settle, as the depends_on case above; this walk
        # warns and runs where that one aborts.
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE],
            settle_seconds=600)
        agentop = self._agentop(runs_after=[edge])
        self._anchor(agentop, 901)

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

        mariadb.get_state_checked.reset_mock()
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

        mariadb.get_state_checked.reset_mock()
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

    def test_a_new_dispatcher_generation_forgets_its_warnings(self):
        # The same lifetime as the cache, and for the same reason: a
        # replacement thread has not warned about anything yet.
        self.daemon.dispatcher_generation = 7
        self.daemon.dispatcher_thread = None
        self.daemon.runs_after_warnings = {('op', 'dep', 'warning')}

        with mock.patch.object(sidechannel.threading, 'Thread'):
            self.daemon.start_dispatcher()

        self.assertEqual(set(), self.daemon.runs_after_warnings)

    # A read which failed says nothing, and must not be mistaken for an
    # answer. Both of mariadb.get_state()'s backends return None when
    # their transport fails, which is the same thing they return for a
    # dependency which is genuinely gone -- so the walk reads through
    # get_state_checked() instead, and these pin what it does with a
    # failure.
    def _unreadable(self):
        return mock.patch.object(
            mariadb, 'get_state_checked', return_value=(False, None))

    def test_an_unreadable_dependency_does_not_cascade(self):
        # The defect this guards against: an sf-database restart during
        # one dispatch pass aborting every queued dependent on the node,
        # permanently, with a dependency_failure naming a dependency
        # which was alive the whole time.
        dep_op, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE])
        agentop = self._agentop(depends_on=[edge])

        with self._unreadable():
            dispatchable, reason = self.daemon._dependencies_met(
                self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('could not be read', reason)
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        self.assertIsNone(agentop.dependency_failure)
        # And the dependency is readable again next pass, as a blip is.
        self.assertTrue(
            self.daemon._dependencies_met(self.inst, agentop)[0])
        self.assertIsNotNone(dep_op)

    def test_an_unreadable_dependency_past_the_budget_says_unreadable(self):
        # Bounded like every other hold, and honest about why: a client
        # which reads unreadable should look at the node's database
        # connectivity, not at the chain it submitted.
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE])
        agentop = self._agentop(depends_on=[edge])
        self._anchor(agentop, 301)

        with self._unreadable():
            dispatchable, reason = self.daemon._dependencies_met(
                self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('gave up', reason)
        self.assertEqual(BaseOperation.STATE_ABORT, agentop.state.value)
        self.assertEqual(sidechannel.DEPENDENCY_UNREADABLE,
                         agentop.dependency_failure['state'])

    def test_an_unreadable_dependency_is_not_recorded_as_missing(self):
        # The two sentinels say opposite things, so they must not be
        # reachable from the same observation.
        self.assertNotEqual(sidechannel.DEPENDENCY_MISSING,
                            sidechannel.DEPENDENCY_UNREADABLE)

    def test_an_unreadable_runs_after_dependency_blocks(self):
        # An ordering edge may be warned past when its dependency is
        # known to be gone, but not when nothing is known at all:
        # dispatching then breaks exactly the ordering it asked for.
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(runs_after=[edge])

        with self._unreadable():
            with mock.patch.object(
                    sidechannel, 'add_event_multi') as mock_event:
                dispatchable, reason = self.daemon._dependencies_met(
                    self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('could not be read', reason)
        mock_event.assert_not_called()

    def test_an_unreadable_runs_after_past_the_budget_dispatches(self):
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(runs_after=[edge])
        self._anchor(agentop, 301)

        with self._unreadable():
            with mock.patch.object(
                    sidechannel, 'add_event_multi') as mock_event:
                dispatchable, _ = self.daemon._dependencies_met(
                    self.inst, agentop)

        self.assertTrue(dispatchable)
        self.assertIsNone(agentop.dependency_failure)
        mock_event.assert_called_once()
        self.assertIn('read', mock_event.call_args.args[2])

    # The two clocks. The budget runs from the wait anchor and the
    # settle from the dependency's observed finish, so the bound on a
    # settling operation has to be the budget plus the settle or the
    # API's ceiling buys nothing.
    def test_a_settle_at_the_ceiling_survives_a_slow_dependency(self):
        # Reachable, and the ordinary case rather than a corner: a
        # dependency which ran for most of the budget and then finished.
        # Measured against the budget alone this aborts on the first
        # pass which could have started the settle.
        dep_op, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE],
            settle_seconds=300)
        agentop = self._agentop(depends_on=[edge])
        self._anchor(agentop, 301)
        self._observed(dep_op.uuid, 1)

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertEqual('settling', reason)
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)

        # And it does elapse, rather than merely being deferred.
        self._observed(dep_op.uuid, 301)
        self.assertTrue(
            self.daemon._dependencies_met(self.inst, agentop)[0])

    def test_an_elapsed_settle_does_not_write_the_anchor(self):
        # The column means "was blocked at least once", and an operation
        # whose settle is already spent was not blocked by anything. It
        # is also two database round trips on a path about to succeed.
        dep_op, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE],
            settle_seconds=10)
        agentop = self._agentop(depends_on=[edge])
        self._observed(dep_op.uuid, 60)

        self.assertTrue(
            self.daemon._dependencies_met(self.inst, agentop)[0])
        self.assertIsNone(self._stored_anchor(agentop))

    # A retry is a new tenant of the queue head, so it is owed the whole
    # budget rather than what the abandoned attempt left of it.
    def test_a_requeue_clears_the_wait_anchor(self):
        agentop = self._agentop(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING],
            commands=[{'command': 'get-file', 'path': '/etc/hostname'}])
        self._anchor(agentop, 301)

        requeued = sidechannel.resolve_abandoned_operation(
            agentop, 'unittest abandoned it', mock.Mock())

        self.assertTrue(requeued)
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        self.assertIsNone(self._stored_anchor(agentop))

    def test_a_requeue_of_an_unblocked_operation_writes_nothing(self):
        # Most retries are of operations with no edges at all, so the
        # clear must not cost them a write.
        agentop = self._agentop(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING],
            commands=[{'command': 'get-file', 'path': '/etc/hostname'}])

        with mock.patch.object(
                mariadb, 'update_agent_operation_attributes') as mock_update:
            agentop.clear_dependency_wait()

        mock_update.assert_not_called()

    def test_a_requeued_operation_is_not_aborted_for_the_old_wait(self):
        # The defect: the stale anchor is already past the budget, so the
        # first edge needing a settle whose observation this dispatcher
        # no longer holds is abandoned on sight.
        dep_op, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE],
            settle_seconds=30)
        agentop = self._agentop(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING],
            depends_on=[edge],
            commands=[{'command': 'get-file', 'path': '/etc/hostname'}])
        self._anchor(agentop, 301)

        self.assertTrue(sidechannel.resolve_abandoned_operation(
            agentop, 'unittest abandoned it', mock.Mock()))
        # A new generation, so nothing remembers the dependency finishing.
        self.daemon.terminal_dependency_cache = {}

        dispatchable, reason = self.daemon._dependencies_met(
            self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertEqual('settling', reason)
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        self.assertIsNone(agentop.dependency_failure)
        self.assertIsNotNone(dep_op)

    # A state the evaluator does not recognise must not satisfy an edge.
    # The three sets below enumerate AgentOperation's whole vocabulary
    # today, and the test after them holds them to it, but a fall
    # through would make a state added later fail open silently.
    def test_every_agent_operation_state_is_classified(self):
        classified = (set(sidechannel.DEPENDENCY_FAILED_STATES)
                      | set(sidechannel.DEPENDENCY_PENDING_STATES)
                      | {BaseOperation.STATE_COMPLETE})
        vocabulary = {state for state in AgentOperation.state_targets
                      if state is not None}

        self.assertEqual(
            set(), vocabulary - classified,
            'a state no arm of the dependency walk classifies')
        self.assertEqual(
            set(), classified - vocabulary,
            'the walk classifies a state no operation can hold')
        self.assertEqual(
            len(sidechannel.DEPENDENCY_FAILED_STATES)
            + len(sidechannel.DEPENDENCY_PENDING_STATES) + 1,
            len(classified), 'a state is classified twice')

    def test_an_unknown_dependency_state_blocks(self):
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE])
        agentop = self._agentop(depends_on=[edge])

        with mock.patch.object(self.daemon, '_dependency_state',
                               return_value=(True, 'invented-later')):
            dispatchable, reason = self.daemon._dependencies_met(
                self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('unknown', reason)
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)

    def test_an_unknown_dependency_state_past_the_budget_cascades(self):
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE])
        agentop = self._agentop(depends_on=[edge])
        self._anchor(agentop, 301)

        with mock.patch.object(self.daemon, '_dependency_state',
                               return_value=(True, 'invented-later')):
            dispatchable, reason = self.daemon._dependencies_met(
                self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('gave up', reason)
        self.assertEqual(BaseOperation.STATE_ABORT, agentop.state.value)
        self.assertEqual('invented-later',
                         agentop.dependency_failure['state'])

    def test_an_unknown_runs_after_state_blocks(self):
        _, edge = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING,
                    BaseOperation.STATE_COMPLETE])
        agentop = self._agentop(runs_after=[edge])

        with mock.patch.object(self.daemon, '_dependency_state',
                               return_value=(True, 'invented-later')):
            dispatchable, reason = self.daemon._dependencies_met(
                self.inst, agentop)

        self.assertFalse(dispatchable)
        self.assertIn('unknown', reason)
        self.assertIsNone(agentop.dependency_failure)

    # The only per-pass database write either walk makes.
    def test_a_repeated_runs_after_warning_is_audited_once(self):
        # The shape which does this: a missing edge in front of a
        # blocking one, re-walked from the start every pass. Sixty
        # durable event rows over one budget, left alone.
        _, blocking = self._dependency(
            states=[AgentOperation.STATE_QUEUED,
                    AgentOperation.STATE_EXECUTING])
        agentop = self._agentop(
            runs_after=[self._missing_edge(), blocking])

        with mock.patch.object(sidechannel, 'add_event_multi') as mock_event:
            for _ in range(5):
                self.assertFalse(
                    self.daemon._dependencies_met(self.inst, agentop)[0])

        mock_event.assert_called_once()

    def test_a_second_runs_after_edge_warns_on_its_own_account(self):
        # Deduplicated per edge, not per operation: two gone edges are
        # two findings an operator should see.
        agentop = self._agentop(
            runs_after=[self._missing_edge(), self._missing_edge()])

        with mock.patch.object(sidechannel, 'add_event_multi') as mock_event:
            self.assertTrue(
                self.daemon._dependencies_met(self.inst, agentop)[0])

        self.assertEqual(2, mock_event.call_count)


class SidechannelDispatchSeamTestCase(base.ShakenFistTestCase):
    """How _dispatch_loop() uses the verdict _dependencies_met() returns.

    The walk itself is covered above. What is covered here is the seam:
    a refused verdict must stop the dispatch and the audit event which
    claims one, and a superseded generation must not reach the walk at
    all, because the walk writes. Both are one line from being wrong in
    a way no test of the walk could see.

    One pass is driven by letting check_abort_path() answer True once.
    """

    def setUp(self):
        super().setUp()
        self.daemon = sidechannel.Monitor.__new__(sidechannel.Monitor)
        self.daemon.abort_path = '/a/b/abort'
        self.daemon.dispatcher_generation = 3
        self.daemon.dispatcher_last_pass = 0
        self.daemon.executors = {}
        self.daemon.executor_attempts = {}

        self.instance_uuid = str(uuid.uuid4())
        monitor = mock.Mock()
        monitor.instance_ready = constants.AGENT_READY
        self.daemon.monitors = {self.instance_uuid: {'object': monitor}}

        self.agentop = mock.Mock()
        self.agentop.uuid = str(uuid.uuid4())
        self.inst = mock.Mock()
        self.inst.uuid = self.instance_uuid
        self.inst.agent_operation_next.return_value = self.agentop

        for target, kwargs in (
                ('shakenfist.daemons.sidechannel.main.daemon.'
                 'check_abort_path', {'side_effect': [True, False]}),
                ('shakenfist.daemons.sidechannel.main.util_concurrency.'
                 'set_thread_name', {}),
                ('shakenfist.daemons.sidechannel.main.instance.Instance.'
                 'from_db', {'return_value': self.inst}),
                ('shakenfist.daemons.sidechannel.main.time.sleep', {})):
            p = mock.patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

        for name in ('reap_instance_executors', 'start_instance_executor'):
            p = mock.patch.object(self.daemon, name)
            setattr(self, f'mock_{name}', p.start())
            self.addCleanup(p.stop)

    def test_a_refused_verdict_dispatches_nothing(self):
        with mock.patch.object(self.daemon, '_dependencies_met',
                               return_value=(False, 'settling')) as mock_met:
            self.daemon._dispatch_loop(3)

        mock_met.assert_called_once_with(self.inst, self.agentop)
        self.mock_start_instance_executor.assert_not_called()
        # The event claims a dispatch, so a blocked operation must not
        # emit it: an operator counting these would otherwise read one
        # dispatch every DISPATCH_CHECK_INTERVAL for a parked operation.
        self.inst.add_event.assert_not_called()

    def test_a_met_verdict_dispatches(self):
        with mock.patch.object(self.daemon, '_dependencies_met',
                               return_value=(True, None)):
            self.daemon._dispatch_loop(3)

        self.mock_start_instance_executor.assert_called_once_with(
            self.instance_uuid, self.agentop)
        self.inst.add_event.assert_called_once()
        self.assertIn('dispatching', self.inst.add_event.call_args.args[1])

    def test_a_superseded_generation_never_evaluates(self):
        # The plan's own stated risk. Evaluation writes -- a cascade is
        # a terminal transition and a first block records the budget
        # anchor -- so a thread which has been replaced must not reach
        # it, not merely refrain from dispatching afterwards.
        self.daemon.dispatcher_generation = 4

        with mock.patch.object(self.daemon, '_dependencies_met') as mock_met:
            self.daemon._dispatch_loop(3)

        mock_met.assert_not_called()
        self.mock_start_instance_executor.assert_not_called()

    def test_a_generation_replaced_mid_pass_never_evaluates(self):
        # The same check, reached the other way: the generation moved
        # after this pass began, which is the race the second check in
        # the loop exists for.
        def supersede(_uuid):
            self.daemon.dispatcher_generation = 4
            return self.inst

        with mock.patch(
                'shakenfist.daemons.sidechannel.main.instance.Instance.'
                'from_db', side_effect=supersede):
            with mock.patch.object(
                    self.daemon, '_dependencies_met') as mock_met:
                self.daemon._dispatch_loop(3)

        mock_met.assert_not_called()
        self.mock_start_instance_executor.assert_not_called()
