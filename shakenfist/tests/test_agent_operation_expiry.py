# Copyright 2019 Michael Still and contributors

import time
import uuid
from unittest import mock

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class AgentOperationExpiryTestCase(base.ShakenFistTestCase):
    """The expired state, and the timing resolution helpers.

    Both stored timing columns are three valued (NULL, an explicit
    0.0, or a real value) and the two absences mean different things,
    so most of what follows is about not collapsing 0.0 into "unset".
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch(
            'shakenfist.operations.agentoperation.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', self.instance_uuid)

    # The state machine is enforced, so reaching a state means walking
    # to it rather than assigning it.
    _PATHS = {
        dbo.STATE_INITIAL: [],
        AgentOperation.STATE_PREFLIGHT: [AgentOperation.STATE_PREFLIGHT],
        AgentOperation.STATE_QUEUED: [AgentOperation.STATE_QUEUED],
        AgentOperation.STATE_EXECUTING: [AgentOperation.STATE_QUEUED,
                                         AgentOperation.STATE_EXECUTING],
    }

    def _make_agentop(self, state=None, deadline=None, progress_timeout=None):
        op = AgentOperation.new(
            str(uuid.uuid4()), 'unittest', self.instance_uuid,
            [{'command': 'execute', 'commandline': 'true'}],
            deadline=deadline, progress_timeout=progress_timeout)
        for step in self._PATHS[state or dbo.STATE_INITIAL]:
            op.state = step
        return op

    # effective_deadline() and deadline_passed()
    def test_explicit_zero_deadline_means_none(self):
        # The licensed use of the sentinel: no wall-clock deadline,
        # with a live progress timeout still bounding a stall. A NULL
        # progress timeout resolves to the live server default, so it
        # licenses the sentinel too (second assertion) -- though no
        # API server since phase 3 writes that combination.
        op = self._make_agentop(
            state=AgentOperation.STATE_QUEUED, deadline=0.0,
            progress_timeout=30.0)
        self.assertIsNone(op.effective_deadline())

        # However old the operation gets, it never expires.
        with mock.patch('time.time', return_value=time.time() + 1000000):
            self.assertFalse(op.deadline_passed())

        op = self._make_agentop(
            state=AgentOperation.STATE_QUEUED, deadline=0.0)
        self.assertIsNone(op.effective_deadline())

    def test_zero_deadline_with_no_progress_timeout_gets_the_backstop(self):
        # Issue #4074: deadline_seconds=0 on agent/execute disables
        # both budgets at once, because that endpoint always stores a
        # progress timeout of 0.0. Nothing would ever end such an
        # operation, and it parks the instance's only executor slot
        # for as long as the guest command runs, so the operator
        # ceiling bounds it instead -- anchored on the current state
        # transition, like a legacy NULL row.
        op = self._make_agentop(
            state=AgentOperation.STATE_EXECUTING, deadline=0.0,
            progress_timeout=0.0)
        anchor = op.state.update_time
        backstop = self.mock_config.AGENT_OPERATION_MAX_DEADLINE
        self.assertEqual(anchor + backstop, op.effective_deadline())

        with mock.patch('time.time', return_value=anchor + backstop - 1):
            self.assertFalse(op.deadline_passed())
        with mock.patch('time.time', return_value=anchor + backstop + 1):
            self.assertTrue(op.deadline_passed())

    def test_the_backstop_honours_a_supplied_state_anchor(self):
        # The reaper and the executor pass the State they already read,
        # for the same GetState-per-check reason as a NULL deadline.
        op = self._make_agentop(
            state=AgentOperation.STATE_EXECUTING, deadline=0.0,
            progress_timeout=0.0)
        backstop = self.mock_config.AGENT_OPERATION_MAX_DEADLINE
        supplied = State(value=AgentOperation.STATE_EXECUTING,
                         update_time=op.state.update_time - backstop - 5)
        self.assertTrue(op.deadline_passed(state=supplied))
        self.assertFalse(op.deadline_passed())

    # deadline_needs_state_anchor()
    def test_which_deadlines_need_a_state_anchor(self):
        cases = [
            # (deadline, progress_timeout, needs_anchor)
            (None, None, True),      # legacy NULL row, server default
            (None, 30.0, True),
            (0.0, 0.0, True),        # both disabled, the #4074 backstop
            (0.0, 30.0, False),      # licensed sentinel, no deadline
            (0.0, None, False),      # NULL resolves to the live default
            (1700000000.0, 0.0, False),  # stored absolute timestamp
        ]
        for deadline, progress_timeout, needs_anchor in cases:
            op = self._make_agentop(
                state=AgentOperation.STATE_QUEUED, deadline=deadline,
                progress_timeout=progress_timeout)
            self.assertEqual(
                needs_anchor, op.deadline_needs_state_anchor(),
                f'deadline={deadline} progress_timeout={progress_timeout}')

    def test_explicit_deadline_is_used_verbatim(self):
        op = self._make_agentop(
            state=AgentOperation.STATE_QUEUED, deadline=1500.0)
        self.assertEqual(1500.0, op.effective_deadline())

        with mock.patch('time.time', return_value=1499.0):
            self.assertFalse(op.deadline_passed())
        with mock.patch('time.time', return_value=1501.0):
            self.assertTrue(op.deadline_passed())

    def test_null_deadline_anchors_on_the_state_transition(self):
        # A row written by an API server which predates deadlines has
        # no receipt time, so the default is anchored on when the
        # operation entered its current state -- not on now, which
        # would recede on every check and never fire.
        op = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        anchor = op.state.update_time
        self.assertEqual(anchor + 600, op.effective_deadline())

        with mock.patch('time.time', return_value=anchor + 599):
            self.assertFalse(op.deadline_passed())
        with mock.patch('time.time', return_value=anchor + 601):
            self.assertTrue(op.deadline_passed())

    def test_a_supplied_state_is_used_as_the_anchor(self):
        # The dispatcher has already read the State it is deciding on,
        # and resolving a NULL deadline's anchor here would otherwise
        # be a second uncached GetState on a path polled per ready
        # instance every five seconds.
        op = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        anchor = op.state.update_time
        supplied = State(value=AgentOperation.STATE_QUEUED,
                         update_time=anchor - 5000)

        self.assertEqual(anchor - 5000 + 600,
                         op.effective_deadline(state=supplied))
        self.assertTrue(op.deadline_passed(state=supplied))
        self.assertFalse(op.deadline_passed())

    def test_a_supplied_state_is_ignored_when_a_deadline_is_stored(self):
        # The anchor only exists to resolve a NULL column. A stored
        # deadline is an absolute timestamp and nothing rebases it.
        op = self._make_agentop(state=AgentOperation.STATE_QUEUED,
                                deadline=1700000000.0)
        supplied = State(value=AgentOperation.STATE_QUEUED, update_time=0.0)
        self.assertEqual(1700000000.0,
                         op.effective_deadline(state=supplied))

    def test_null_deadline_rebases_on_each_transition(self):
        # Documented consequence of the anchor choice: a legacy row's
        # budget restarts when it changes state. Asserted so the
        # behaviour cannot change silently.
        op = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        first = op.effective_deadline()
        op.state = AgentOperation.STATE_EXECUTING
        self.assertGreater(op.effective_deadline(), first)

    # effective_progress_timeout()
    def test_explicit_zero_progress_timeout_disables_it(self):
        op = self._make_agentop(progress_timeout=0.0)
        self.assertIsNone(op.effective_progress_timeout())

    def test_explicit_progress_timeout_is_used_verbatim(self):
        op = self._make_agentop(progress_timeout=5.0)
        self.assertEqual(5.0, op.effective_progress_timeout())

    def test_null_progress_timeout_uses_the_default(self):
        op = self._make_agentop()
        self.assertEqual(30.0, op.effective_progress_timeout())

    # expire()
    def test_expire_from_every_non_terminal_state(self):
        for state in (dbo.STATE_INITIAL, AgentOperation.STATE_PREFLIGHT,
                      AgentOperation.STATE_QUEUED,
                      AgentOperation.STATE_EXECUTING):
            op = self._make_agentop(state=state)
            op.expire('budget exhausted in %s' % state,
                      AgentOperation.EXPIRY_REASON_DEADLINE)
            self.assertEqual(AgentOperation.STATE_EXPIRED, op.state.value)
            self.assertEqual(
                'budget exhausted in %s' % state, op.state.message)

    def test_expire_does_not_write_the_error_attribute(self):
        # The error setter refuses any state not ending in "error", so
        # an expire() which tried to record its reason there would
        # raise. The reason lives on the state instead.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.expire('no progress', AgentOperation.EXPIRY_REASON_PROGRESS)
        self.assertIsNone(op.error)

    def test_expire_from_a_terminal_state_is_a_noop(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.state = AgentOperation.STATE_COMPLETE
        op.expire('too late', AgentOperation.EXPIRY_REASON_DEADLINE)
        self.assertEqual(AgentOperation.STATE_COMPLETE, op.state.value)

        # The no-op must be complete: an operation which did not expire
        # must not read back as though it had.
        self.assertIsNone(op.expiry_reason)

    def test_expired_may_be_deleted_but_not_errored(self):
        op = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op.expire('gone', AgentOperation.EXPIRY_REASON_DEADLINE)
        self.assertRaises(
            exceptions.InvalidStateException,
            setattr, op, 'state', dbo.STATE_ERROR)
        op.state = dbo.STATE_DELETED
        self.assertEqual(dbo.STATE_DELETED, op.state.value)

    # expire() emits the audit event the reason really lives in
    def test_expire_events_the_operation_and_its_instance(self):
        # expired is in FINAL_OBJECT_STATES, so the operation is swept
        # for hard deletion; an event held only against it would go
        # with it. The instance's copy is the one which survives.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        with mock.patch(
                'shakenfist.operations.agentoperation.add_event_multi'
        ) as mock_event:
            op.expire('no progress', AgentOperation.EXPIRY_REASON_PROGRESS)

        mock_event.assert_called_once()
        args = mock_event.call_args.args
        self.assertEqual(EVENT_TYPE_AUDIT, args[0])
        self.assertEqual(
            [(ObjectType.AGENTOPERATION, op.uuid),
             (ObjectType.INSTANCE, self.instance_uuid)], args[1])
        self.assertEqual('operation expired', args[2])
        self.assertEqual(
            {'reason': 'no progress',
             'budget': AgentOperation.EXPIRY_REASON_PROGRESS},
            mock_event.call_args.kwargs['extra'])

    # expiry_reason (issue #4075): the enumerated fact a client can
    # branch on, where the state message is prose.
    def _view(self, op):
        # The reference grouping in external_view() reads the caller's
        # namespace out of a JWT, so it needs a request context these
        # tests have no interest in standing up; the same shape as
        # test_instance.py's helper.
        with mock.patch(
                'shakenfist.operations.agentoperation.'
                'references_to_grouped_dict', return_value={}):
            return op.external_view()

    def test_expire_records_which_budget_on_the_external_view(self):
        for budget in AgentOperation.EXPIRY_REASONS:
            op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
            op.expire(f'the {budget} budget ran out', budget)
            self.assertEqual(budget, op.expiry_reason)
            self.assertEqual(budget, self._view(op)['expiry_reason'])

    def test_expiry_reason_is_none_until_expiry(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        self.assertIsNone(op.expiry_reason)
        self.assertIsNone(self._view(op)['expiry_reason'])

    def test_fail_does_not_record_an_expiry_reason(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.fail('it broke')
        self.assertIsNone(op.expiry_reason)
        self.assertIsNone(self._view(op)['expiry_reason'])

    def test_expire_refuses_prose_as_a_budget(self):
        # The field is enumerated so a client can branch on it; free
        # text sneaking in through a future call site would quietly
        # break every such client.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        self.assertRaises(
            ValueError, op.expire, 'reason', 'the deadline passed')
        self.assertEqual(AgentOperation.STATE_EXECUTING, op.state.value)

    def test_expire_writes_a_masked_expiry_reason(self):
        # An unmasked write would push a stale snapshot of the results
        # column over a concurrent add_result(), exactly as
        # record_progress()'s docstring warns.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        with mock.patch(
                'shakenfist.operations.agentoperation.mariadb'
                '.update_agent_operation_attributes') as mock_update:
            with mock.patch(
                    'shakenfist.operations.agentoperation.add_event_multi'):
                op.expire('deadline passed',
                          AgentOperation.EXPIRY_REASON_DEADLINE)

        mock_update.assert_called_once()
        self.assertEqual(
            ['expiry_reason'], mock_update.call_args.kwargs['fields'])
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_DEADLINE,
            mock_update.call_args.args[0].expiry_reason)

    # fail()
    def test_fail_records_the_message_on_the_state(self):
        # fail() makes one write, and both readers see it: the state
        # message is where the reason is stored, and since the fix for
        # issue #3899 the error property reads that same message back.
        # Asserting both is what pins the single write -- .error alone
        # would also pass if the message were stored somewhere else.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.fail('it broke')
        self.assertEqual(dbo.STATE_ERROR, op.state.value)
        self.assertEqual('it broke', op.state.message)
        self.assertEqual('it broke', op.error)

    def test_fail_from_expired_is_a_noop(self):
        # Without the terminal state guard this raises
        # InvalidStateException, because expired has no edge to error.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.expire('deadline passed', AgentOperation.EXPIRY_REASON_DEADLINE)
        op.fail('and then something else went wrong')
        self.assertEqual(AgentOperation.STATE_EXPIRED, op.state.value)

    # record_progress()
    def test_record_progress_writes_a_masked_field(self):
        # An unmasked write would push a stale snapshot of the results
        # column over a concurrent add_result() -- the cross-attribute
        # lost update which caused the vanished-agent-operation flake.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        with mock.patch(
                'shakenfist.operations.agentoperation.mariadb'
                '.update_agent_operation_attributes') as mock_update:
            op.record_progress(1700000000.0)

        mock_update.assert_called_once()
        self.assertEqual(
            ['last_progress'], mock_update.call_args.kwargs['fields'])
        self.assertEqual(
            1700000000.0, mock_update.call_args.args[0].last_progress)

    # hard_delete()
    def test_hard_delete_clears_object_references(self):
        # Symmetric with delete(). Adding expired to
        # FINAL_OBJECT_STATES routes a new class of operation into the
        # cluster's hard delete sweep, so an asymmetry here becomes a
        # leak the moment anything records a reference from an agent
        # operation.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.expire('deadline passed', AgentOperation.EXPIRY_REASON_DEADLINE)

        with mock.patch(
                'shakenfist.operations.agentoperation.mariadb'
                '.remove_all_references_from') as mock_remove:
            op.hard_delete()

        mock_remove.assert_called_once_with(
            ObjectType.AGENTOPERATION, op.uuid)

    def test_record_progress_round_trips(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.record_progress(1700000000.0)
        self.assertEqual(1700000000.0, op.last_progress)


class AgentOperationStateMachineTestCase(base.ShakenFistTestCase):
    """Shape assertions on state_targets itself, which needs no database."""

    def test_every_target_list_is_a_tuple(self):
        # A bare string value would do substring membership in
        # baseobject._state_update(), admitting 'delete' as well as
        # 'deleted'.
        for source, targets in AgentOperation.state_targets.items():
            if targets is None:
                continue
            self.assertIsInstance(targets, tuple, source)

    def test_expired_is_reachable_from_every_non_terminal_state(self):
        for source in (dbo.STATE_INITIAL, AgentOperation.STATE_PREFLIGHT,
                       AgentOperation.STATE_QUEUED,
                       AgentOperation.STATE_EXECUTING):
            self.assertIn(
                AgentOperation.STATE_EXPIRED,
                AgentOperation.state_targets[source], source)

    def test_expired_is_terminal(self):
        self.assertEqual(
            (dbo.STATE_DELETED,),
            AgentOperation.state_targets[AgentOperation.STATE_EXPIRED])
        for source in (AgentOperation.STATE_COMPLETE, dbo.STATE_ERROR):
            self.assertNotIn(
                AgentOperation.STATE_EXPIRED,
                AgentOperation.state_targets[source], source)
