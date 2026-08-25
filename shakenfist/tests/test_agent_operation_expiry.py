# Copyright 2019 Michael Still and contributors

import time
import uuid
from unittest import mock

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class AgentOperationExpiryTestCase(base.ShakenFistTestCase):
    """The expired state, and the three timing resolution helpers.

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
        op = self._make_agentop(
            state=AgentOperation.STATE_QUEUED, deadline=0.0)
        self.assertIsNone(op.effective_deadline())

        # However old the operation gets, an explicit zero never expires.
        with mock.patch('time.time', return_value=time.time() + 1000000):
            self.assertFalse(op.deadline_passed())

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
            op.expire('budget exhausted in %s' % state)
            self.assertEqual(AgentOperation.STATE_EXPIRED, op.state.value)
            self.assertEqual(
                'budget exhausted in %s' % state, op.state.message)

    def test_expire_does_not_write_the_error_attribute(self):
        # The error setter refuses any state not ending in "error", so
        # an expire() which tried to record its reason there would
        # raise. The reason lives on the state instead.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.expire('no progress')
        self.assertIsNone(op.error)

    def test_expire_from_a_terminal_state_is_a_noop(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.state = AgentOperation.STATE_COMPLETE
        op.expire('too late')
        self.assertEqual(AgentOperation.STATE_COMPLETE, op.state.value)

    def test_expired_may_be_deleted_but_not_errored(self):
        op = self._make_agentop(state=AgentOperation.STATE_QUEUED)
        op.expire('gone')
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
            op.expire('no progress')

        mock_event.assert_called_once()
        args = mock_event.call_args.args
        self.assertEqual(EVENT_TYPE_AUDIT, args[0])
        self.assertEqual(
            [(ObjectType.AGENTOPERATION, op.uuid),
             (ObjectType.INSTANCE, self.instance_uuid)], args[1])
        self.assertEqual('operation expired', args[2])
        self.assertEqual(
            {'reason': 'no progress'}, mock_event.call_args.kwargs['extra'])

    # fail()
    def test_fail_records_the_message_on_the_state(self):
        # The state message is where the reason survives, and the only
        # place it is written. AgentOperation does not override
        # _db_set_attribute(), so an assignment to self.error would
        # persist nothing and cost a warning per failure; see issue
        # #3899.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.fail('it broke')
        self.assertEqual(dbo.STATE_ERROR, op.state.value)
        self.assertEqual('it broke', op.state.message)
        self.assertIsNone(op.error)

    def test_fail_from_expired_is_a_noop(self):
        # Without the terminal state guard this raises
        # InvalidStateException, because expired has no edge to error.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.expire('deadline passed')
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
        op.expire('deadline passed')

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
