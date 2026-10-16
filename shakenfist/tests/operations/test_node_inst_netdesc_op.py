# Copyright 2026 Michael Still and contributors
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.instance import Instance
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import DEPENDENCY_DEFER_BUDGET
from shakenfist.operations.node_inst_netdesc_op import NodeInstNetdescOp
from shakenfist.schema.operations import node_inst_netdesc_op as schema
from shakenfist.tests import base


OP_UUID = '11111111-1111-4111-8111-111111111111'
INSTANCE_UUID = '33333333-3333-4333-8333-333333333333'
NODE_UUID = '55555555-5555-4555-8555-555555555555'
DEP_UUID = '77777777-7777-4777-8777-777777777777'


def _make_static_values():
    return {
        'uuid': OP_UUID,
        'node_uuid': NODE_UUID,
        'instance_uuid': INSTANCE_UUID,
        'net_desc': [],
        'priority': 'user_waiting',
        'request_id': None,
        'depends_on': None,
        'runs_after': None,
        'tasks': ['instance_preflight', 'instance_start'],
        'version': schema.current_version,
    }


def _make_instance(state):
    inst = mock.MagicMock()
    inst.state.value = state
    return inst


class DependencyFailedTestCase(base.ShakenFistTestCase):
    """An instance start which will never run must error its instance.

    Issue 4542: a start op whose image fetch never went terminal was
    aborted once the dependency defer budget was spent, but nothing
    moved the instance, which sat in initial while its client waited
    for over thirty minutes.
    """

    def setUp(self):
        super().setUp()

        self.from_db_patcher = mock.patch.object(Instance, 'from_db')
        self.mock_from_db = self.from_db_patcher.start()
        self.addCleanup(self.from_db_patcher.stop)

    def _make_op(self):
        return NodeInstNetdescOp(_make_static_values())

    def test_instances_still_being_built_are_errored(self):
        for state in [dbo.STATE_INITIAL, Instance.STATE_PREFLIGHT,
                      dbo.STATE_CREATING]:
            with self.subTest(state=state):
                inst = _make_instance(state)
                self.mock_from_db.return_value = inst

                self._make_op()._dependency_failed('the reason')

                inst.enqueue_delete_due_error.assert_called_once_with(
                    'instance start abandoned: the reason')

    def test_terminal_instances_are_left_alone(self):
        """A failed artifact fetch errors the instance itself, and the
        start op waiting on it then fails too. Erroring the instance a
        second time would push initial-error on to plain error."""
        for state in [Instance.STATE_INITIAL_ERROR, dbo.STATE_ERROR,
                      dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED]:
            with self.subTest(state=state):
                inst = _make_instance(state)
                self.mock_from_db.return_value = inst

                self._make_op()._dependency_failed('the reason')

                inst.enqueue_delete_due_error.assert_not_called()

    def test_missing_instance_is_not_an_error(self):
        self.mock_from_db.return_value = None
        self._make_op()._dependency_failed('the reason')
        self.mock_from_db.assert_called_once_with(
            INSTANCE_UUID, suppress_failure_audit=True)

    @mock.patch('shakenfist.operations.baseoperation.eventlog.add_event_multi')
    @mock.patch.object(BaseClusterOperation, '_state_update')
    def test_abandoned_fetch_wait_errors_the_instance(
            self, mock_state_update, mock_add_event_multi):
        """The issue's own sequence: the start op gives up on a fetch op
        still executing, and the instance it was starting reaches an
        error state naming that fetch rather than staying in initial."""
        inst = _make_instance(dbo.STATE_INITIAL)
        self.mock_from_db.return_value = inst

        dep_op = mock.MagicMock()
        dep_op.object_type = 'artifact_fetch_op'
        dep_op.uuid = DEP_UUID
        dep_op.state.value = BaseClusterOperation.STATE_EXECUTING

        op = self._make_op()
        op.current_defer_count = DEPENDENCY_DEFER_BUDGET
        op.abandon_dependency_wait(dep_op)

        mock_state_update.assert_called_once_with(
            BaseClusterOperation.STATE_ABORT)
        inst.enqueue_delete_due_error.assert_called_once_with(
            'instance start abandoned: gave up waiting for dependency '
            f'artifact_fetch_op {DEP_UUID}, which was still '
            f'{BaseClusterOperation.STATE_EXECUTING}')
