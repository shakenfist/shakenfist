# Copyright 2019 Michael Still and contributors

import uuid
from unittest import mock

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import mariadb
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.operations.baseoperation import BaseOperation
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class AgentOperationDependencyCascadeTestCase(base.ShakenFistTestCase):
    """The two writes an operation makes about a dependency it is waiting on.

    cascade_abort_for_dependency() is the fate-sharing outcome of a
    depends_on edge, and note_dependency_wait_started() is the anchor
    the dependency wait budget is measured from. Both are here rather
    than in the dispatcher because they move the state machine, and the
    class which owns the state machine is where its guard lives.
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

    def _edge(self, op_uuid=None, settle_seconds=None):
        # A stored edge, which is what the evaluator hands these methods:
        # op_type is present because a persisted agent edge is the same
        # shape as a persisted cluster one.
        return {'op_type': ObjectType.AGENTOPERATION.value,
                'op_uuid': op_uuid or str(uuid.uuid4()),
                'settle_seconds': settle_seconds}

    def _make_agentop(self, state=None, depends_on=None):
        op = AgentOperation.new(
            str(uuid.uuid4()), 'unittest', self.instance_uuid,
            [{'command': 'execute', 'commandline': 'true'}],
            depends_on=depends_on)
        # The state machine is enforced, so reaching a state means walking
        # to it rather than assigning it.
        for step in (state or []):
            op.state = step
        return op

    def _view(self, op):
        # The reference grouping in external_view() reads the caller's
        # namespace out of a JWT, so it needs a request context these
        # tests have no interest in standing up; the same shape as
        # test_agent_operation_expiry.py's helper.
        with mock.patch(
                'shakenfist.operations.agentoperation.'
                'references_to_grouped_dict', return_value={}):
            return op.external_view()

    # cascade_abort_for_dependency()
    def test_a_cascade_records_the_dependency_and_aborts(self):
        dep = self._edge()
        op = self._make_agentop(
            state=[AgentOperation.STATE_QUEUED], depends_on=[dep])

        # True distinguishes a cascade which landed from one which was
        # too late, which is what the dispatcher branches on.
        self.assertTrue(op.cascade_abort_for_dependency(dep, dbo.STATE_ERROR))

        self.assertEqual(BaseOperation.STATE_ABORT, op.state.value)
        self.assertEqual(
            {'op_type': ObjectType.AGENTOPERATION.value,
             'op_uuid': dep['op_uuid'],
             'state': dbo.STATE_ERROR},
            op.dependency_failure)
        self.assertEqual(op.dependency_failure,
                         self._view(op)['dependency_failure'])

    def test_a_cascade_records_a_missing_dependency(self):
        # 'missing' is not an AgentOperation state and is meant not to be:
        # a client has to be able to tell a dependency which failed from
        # one which no longer exists, because the two are remedied
        # differently even though they cost the dependent the same fate.
        dep = self._edge()
        op = self._make_agentop(
            state=[AgentOperation.STATE_QUEUED], depends_on=[dep])

        op.cascade_abort_for_dependency(dep, 'missing')

        self.assertEqual(BaseOperation.STATE_ABORT, op.state.value)
        self.assertEqual('missing', op.dependency_failure['state'])
        self.assertNotIn('missing', AgentOperation.state_targets)

    def test_a_cascade_writes_the_reason_before_the_state(self):
        # delete() removes an operation's attribute rows and only then
        # assigns its state, so there is no later moment at which the
        # reason could be filled in -- a reader who sees abort has to be
        # able to see why already. The order is asserted rather than
        # inferred, because both writes happen whichever way round they
        # are made.
        dep = self._edge()
        op = self._make_agentop(
            state=[AgentOperation.STATE_QUEUED], depends_on=[dep])

        order = []
        stored_attributes = mariadb.update_agent_operation_attributes
        stored_state = mariadb.set_state

        def record_attributes(*args, **kwargs):
            order.append('attributes')
            return stored_attributes(*args, **kwargs)

        def record_state(*args, **kwargs):
            order.append('state')
            return stored_state(*args, **kwargs)

        with mock.patch(
                'shakenfist.mariadb.update_agent_operation_attributes',
                side_effect=record_attributes):
            with mock.patch('shakenfist.mariadb.set_state',
                            side_effect=record_state):
                op.cascade_abort_for_dependency(dep, dbo.STATE_ERROR)

        self.assertEqual(['attributes', 'state'], order)

    def test_a_cascade_writes_a_masked_dependency_failure(self):
        # An unmasked write would push a stale snapshot of the results
        # column over a concurrent add_result(), exactly as
        # record_progress()'s docstring warns.
        dep = self._edge()
        op = self._make_agentop(
            state=[AgentOperation.STATE_QUEUED], depends_on=[dep])

        with mock.patch(
                'shakenfist.operations.agentoperation.mariadb'
                '.update_agent_operation_attributes') as mock_update:
            with mock.patch(
                    'shakenfist.operations.agentoperation.add_event_multi'):
                op.cascade_abort_for_dependency(dep, dbo.STATE_ERROR)

        mock_update.assert_called_once()
        self.assertEqual(
            ['dependency_failure'], mock_update.call_args.kwargs['fields'])

    def test_a_cascade_events_the_operation_and_its_instance(self):
        # abort is in FINAL_OBJECT_STATES, so the operation is swept for
        # hard deletion and an event held only against it would go with
        # it; the instance's copy is the one which survives. The same
        # argument expire() makes.
        dep = self._edge()
        op = self._make_agentop(
            state=[AgentOperation.STATE_QUEUED], depends_on=[dep])

        with mock.patch(
                'shakenfist.operations.agentoperation.add_event_multi'
        ) as mock_event:
            op.cascade_abort_for_dependency(dep, dbo.STATE_ERROR)

        mock_event.assert_called_once()
        args = mock_event.call_args.args
        self.assertEqual(EVENT_TYPE_AUDIT, args[0])
        self.assertEqual(
            [(ObjectType.AGENTOPERATION, op.uuid),
             (ObjectType.INSTANCE, self.instance_uuid)], args[1])
        self.assertEqual(
            {'op_type': ObjectType.AGENTOPERATION.value,
             'op_uuid': dep['op_uuid'],
             'state': dbo.STATE_ERROR},
            mock_event.call_args.kwargs['extra'])

    def test_a_cascade_of_a_finished_operation_is_a_noisy_noop(self):
        # A finished operation is left exactly as it was found, and the
        # cascade says so rather than claiming it landed. The event is
        # the point: a cascade which did not apply is worth an operator
        # seeing, which a silent return would not give them.
        dep = self._edge()
        op = self._make_agentop(
            state=[AgentOperation.STATE_QUEUED, AgentOperation.STATE_EXECUTING,
                   BaseOperation.STATE_COMPLETE],
            depends_on=[dep])

        with mock.patch(
                'shakenfist.operations.agentoperation.add_event_multi'
        ) as mock_event:
            self.assertFalse(
                op.cascade_abort_for_dependency(dep, dbo.STATE_ERROR))

        self.assertEqual(BaseOperation.STATE_COMPLETE, op.state.value)
        self.assertIsNone(op.dependency_failure)
        mock_event.assert_called_once()
        self.assertEqual(
            [(ObjectType.AGENTOPERATION, op.uuid),
             (ObjectType.INSTANCE, self.instance_uuid)],
            mock_event.call_args.args[1])
        self.assertEqual(
            BaseOperation.STATE_COMPLETE,
            mock_event.call_args.kwargs['extra']['state'])
        self.assertEqual(
            dbo.STATE_ERROR,
            mock_event.call_args.kwargs['extra']['dependency_state'])

    def test_a_cascade_of_an_expired_operation_writes_nothing(self):
        # expired is the reachable race rather than a theoretical one:
        # deadline enforcement expires an operation out of band, so a
        # cascade really can arrive after one has. Writing the reason
        # anyway would publish a dependency_failure on an operation
        # which reads expired, telling a client branching on that field
        # -- which is what the field is for -- something untrue.
        dep = self._edge()
        op = self._make_agentop(
            state=[AgentOperation.STATE_QUEUED], depends_on=[dep])
        op.expire('out of time', AgentOperation.EXPIRY_REASON_DEADLINE)

        self.assertFalse(op.cascade_abort_for_dependency(dep, 'missing'))

        self.assertEqual(AgentOperation.STATE_EXPIRED, op.state.value)
        self.assertIsNone(op.dependency_failure)
        self.assertIsNone(self._view(op)['dependency_failure'])
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_DEADLINE, op.expiry_reason)

    def test_the_state_machine_still_refuses_aborting_a_finished_operation(
            self):
        # The backstop behind the guard above, pinned on its own because
        # the guard cannot close the window between its own read and the
        # write which follows it. Reaching this is a last resort -- the
        # reason has been persisted by the time it fires -- so it has to
        # stay a refusal rather than become a silent no-op.
        op = self._make_agentop(
            state=[AgentOperation.STATE_QUEUED, AgentOperation.STATE_EXECUTING,
                   BaseOperation.STATE_COMPLETE])

        self.assertRaises(
            exceptions.InvalidStateException,
            setattr, op, 'state', BaseOperation.STATE_ABORT)
        self.assertEqual(BaseOperation.STATE_COMPLETE, op.state.value)

    def test_an_aborted_operation_may_be_deleted_but_not_errored(self):
        dep = self._edge()
        op = self._make_agentop(
            state=[AgentOperation.STATE_QUEUED], depends_on=[dep])
        op.cascade_abort_for_dependency(dep, dbo.STATE_ERROR)

        self.assertRaises(
            exceptions.InvalidStateException,
            setattr, op, 'state', dbo.STATE_ERROR)
        op.state = dbo.STATE_DELETED
        self.assertEqual(dbo.STATE_DELETED, op.state.value)

    def test_dependency_failure_is_none_until_a_cascade(self):
        op = self._make_agentop(state=[AgentOperation.STATE_QUEUED])
        self.assertIsNone(op.dependency_failure)
        self.assertIsNone(self._view(op)['dependency_failure'])

    # note_dependency_wait_started()
    def test_the_wait_anchor_is_kept_across_later_calls(self):
        # A blocked operation is re-evaluated on every dispatch pass. An
        # anchor which moved on each of those passes would leave a budget
        # which could never be exhausted, so the second call has to
        # return the first call's answer and write nothing.
        op = self._make_agentop(state=[AgentOperation.STATE_QUEUED])

        with mock.patch('time.time', return_value=1000.0):
            first = op.note_dependency_wait_started()
        with mock.patch('time.time', return_value=2000.0):
            second = op.note_dependency_wait_started()

        self.assertEqual(1000.0, first)
        self.assertEqual(1000.0, second)

    def test_the_wait_anchor_is_written_once_and_masked(self):
        op = self._make_agentop(state=[AgentOperation.STATE_QUEUED])

        with mock.patch(
                'shakenfist.operations.agentoperation.mariadb'
                '.update_agent_operation_attributes',
                side_effect=mariadb.update_agent_operation_attributes
        ) as mock_update:
            op.note_dependency_wait_started()
            self.assertEqual(
                ['dependency_wait_started'],
                mock_update.call_args.kwargs['fields'])
            op.note_dependency_wait_started()

        mock_update.assert_called_once()

    def test_the_wait_anchor_is_not_on_the_external_view(self):
        # A caller needs the outcome of a wait, not the instant the
        # server started counting: publishing the anchor would invite
        # clients to re-derive a budget which is the operator's to set.
        op = self._make_agentop(state=[AgentOperation.STATE_QUEUED])
        op.note_dependency_wait_started()
        self.assertNotIn('dependency_wait_started', self._view(op))
