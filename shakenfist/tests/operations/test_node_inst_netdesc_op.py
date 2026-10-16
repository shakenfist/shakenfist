# Copyright 2026 Michael Still and contributors
from unittest import mock

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.instance import Instance
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import DEPENDENCY_DEFER_BUDGET
from shakenfist.operations.node_inst_netdesc_op import NodeInstNetdescOp
from shakenfist.schema.object_state import State
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

    @mock.patch('shakenfist.mariadb.get_state',
                return_value=State(value=BaseClusterOperation.STATE_QUEUED,
                                   update_time=0))
    @mock.patch('shakenfist.operations.baseoperation.eventlog.add_event_multi')
    @mock.patch.object(BaseClusterOperation, '_state_update')
    def test_abandoned_fetch_wait_errors_the_instance(
            self, mock_state_update, mock_add_event_multi, mock_get_state):
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


class DependencyHelpersTestCase(base.ShakenFistTestCase):
    """The other two dependency paths, driven into the real override.

    Each helper must error a queued op's instance, and none may touch an
    instance whose op already ran: a COMPLETE instance_create the
    stuck-row reaper re-dispatches, with a dependency deleted since,
    would otherwise error the running instance it started.
    """

    def setUp(self):
        super().setUp()

        for name, patcher in [
                ('from_db', mock.patch.object(Instance, 'from_db')),
                ('state_update', mock.patch.object(
                    BaseClusterOperation, '_state_update')),
                ('add_event', mock.patch.object(
                    BaseClusterOperation, 'add_event')),
                ('add_event_multi', mock.patch(
                    'shakenfist.operations.baseoperation.eventlog.'
                    'add_event_multi')),
                ('get_state', mock.patch('shakenfist.mariadb.get_state'))]:
            setattr(self, f'mock_{name}', patcher.start())
            self.addCleanup(patcher.stop)

    def _dep_op(self, state):
        dep_op = mock.MagicMock()
        dep_op.object_type = 'artifact_fetch_op'
        dep_op.uuid = DEP_UUID
        dep_op.state.value = state
        return dep_op

    def _paths(self):
        return {
            'unsuitable': (
                lambda op: op.abort_for_unsuitable_dependency(
                    self._dep_op(BaseClusterOperation.STATE_ERROR)),
                f'dependency artifact_fetch_op {DEP_UUID} is '
                f'{BaseClusterOperation.STATE_ERROR}'),
            'missing': (
                lambda op: op.cancel_for_missing_dependency(
                    {'op_type': 'artifact_fetch_op', 'op_uuid': DEP_UUID}),
                f'dependency artifact_fetch_op {DEP_UUID} does not exist'),
            'abandoned': (
                lambda op: op.abandon_dependency_wait(
                    self._dep_op(BaseClusterOperation.STATE_EXECUTING)),
                f'gave up waiting for dependency artifact_fetch_op '
                f'{DEP_UUID}, which was still '
                f'{BaseClusterOperation.STATE_EXECUTING}'),
        }

    def test_queued_op_errors_its_instance(self):
        self.mock_get_state.return_value = State(
            value=BaseClusterOperation.STATE_QUEUED, update_time=0)
        for name, (call, reason) in self._paths().items():
            with self.subTest(path=name):
                inst = _make_instance(dbo.STATE_INITIAL)
                self.mock_from_db.return_value = inst

                call(NodeInstNetdescOp(_make_static_values()))

                inst.enqueue_delete_due_error.assert_called_once_with(
                    f'instance start abandoned: {reason}')

    def test_finished_op_never_errors_its_instance(self):
        self.mock_state_update.side_effect = exceptions.InvalidStateException(
            'no such transition')
        for op_state in [BaseClusterOperation.STATE_COMPLETE,
                         BaseClusterOperation.STATE_EXECUTING]:
            self.mock_get_state.return_value = State(
                value=op_state, update_time=0)
            for name, (call, _) in self._paths().items():
                with self.subTest(op_state=op_state, path=name):
                    inst = _make_instance(dbo.STATE_CREATED)
                    self.mock_from_db.return_value = inst

                    call(NodeInstNetdescOp(_make_static_values()))

                    inst.enqueue_delete_due_error.assert_not_called()
