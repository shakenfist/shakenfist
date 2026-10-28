# Copyright 2019 Michael Still and contributors
"""Regression tests for ``NodeInstOp.dispatch_task``'s error handler.

A failed task used to blindly assign ``Instance.STATE_ERROR`` to the
instance. From ``delete-wait`` the only legal transitions are
``deleted`` and ``delete-wait-error``, so the assignment raised
InvalidStateException out of the exception handler, killed the queue
worker thread, and abandoned the instance in ``delete-wait`` (observed
in CI as test_interface_delete timing out after a slow DHCP-lease-
removal op). The handler must use the same suffix-then-fallback
pattern as ``Instance.enqueue_delete_due_error`` and must not touch
instances that are already deleted.

Observational operations (every task in ``model.observational_tasks``)
must fail without changing the instance's state at all (issue 4585). The
rule is asked of the whole operation, as ``is_observational()`` asks it: an
operation which puts a measurement ahead of a mutating task is mutating,
and a failed measurement ends it before the mutating task runs, so the
instance must be errored (issue 4670).
"""

from unittest import mock
from uuid import uuid4

from shakenfist import exceptions
from shakenfist.operations.node_inst_op import NodeInstOp
from shakenfist.schema.object_state import State
from shakenfist.schema.operations import node_inst_op as nio_schema
from shakenfist.schema.operations.node_inst_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.util import is_observational
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class FakeInstance:
    """An instance double with a state machine we control.

    ``reject`` lists state values whose assignment raises
    InvalidStateException, mimicking baseobject._state_update.
    """

    def __init__(self, state_value, reject=()):
        self._state_value = state_value
        self._reject = reject
        self.state_sets = []

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


class DispatchTaskErrorStateTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _dispatch_failing_task(self, inst, task):
        _, op_uuid = create_and_enqueue(
            node_uuid=str(uuid4()),
            instance_uuid=str(uuid4()),
            tasks=[task],
            priority=PRIORITY.user_facing,
        )
        op = NodeInstOp.from_db(op_uuid)
        self.assertIsNotNone(op)
        op.state = NodeInstOp.STATE_EXECUTING

        with mock.patch(
                'shakenfist.operations.node_inst_op.util_exceptions.'
                'ignore_exception'):
            with mock.patch(
                    'shakenfist.operations.node_inst_op.Instance.from_db',
                    return_value=inst):
                with mock.patch(
                        'shakenfist.operations.node_inst_op.NodeInstOp.'
                        f'_{task.name}',
                        side_effect=RuntimeError('boom')):
                    op.dispatch_task(task)
        return op

    def _dispatch_failing_delete(self, inst):
        return self._dispatch_failing_task(inst, model_tasks.instance_delete)

    def test_delete_wait_failure_sets_delete_wait_error(self):
        inst = FakeInstance('delete-wait', reject=('error',))
        op = self._dispatch_failing_delete(inst)

        self.assertEqual(NodeInstOp.STATE_ERROR, op.state.value)
        self.assertEqual(['delete-wait-error'], inst.state_sets)

    def test_suffix_rejected_falls_back_to_error(self):
        # An instance already in an error state has no valid '-error'
        # suffix target; the handler must fall back to plain 'error'.
        inst = FakeInstance('created-error', reject=('created-error-error',))
        op = self._dispatch_failing_delete(inst)

        self.assertEqual(NodeInstOp.STATE_ERROR, op.state.value)
        self.assertEqual(['error'], inst.state_sets)

    def test_deleted_instance_state_untouched(self):
        inst = FakeInstance('deleted',
                            reject=('deleted-error', 'error'))
        op = self._dispatch_failing_delete(inst)

        self.assertEqual(NodeInstOp.STATE_ERROR, op.state.value)
        self.assertEqual([], inst.state_sets)

    def test_failed_observational_task_leaves_instance_alone(self):
        # The cluster daemon measures every created instance on every
        # sweep. A failure to measure one, here anything the task does not
        # catch itself, must fail the operation without erroring a healthy
        # guest (issue 4585).
        for task in (model_tasks.collect_billing_statistics,
                     model_tasks.health_check_kvm_process):
            with self.subTest(task=task.name):
                inst = FakeInstance('created')
                op = self._dispatch_failing_task(inst, task)

                self.assertEqual(NodeInstOp.STATE_ERROR, op.state.value)
                self.assertEqual([], inst.state_sets)

    def test_failed_mutating_task_from_created_errors_instance(self):
        # The counterpart of the test above: a mutating task which fails
        # from created still errors its instance.
        inst = FakeInstance('created')
        op = self._dispatch_failing_delete(inst)

        self.assertEqual(NodeInstOp.STATE_ERROR, op.state.value)
        self.assertEqual(['created-error'], inst.state_sets)

    def test_failed_probe_in_mutating_op_errors_instance(self):
        # [health_check_kvm_process, instance_delete] is a mutating
        # operation: is_observational() says so, and its target row is
        # stamped mutating. When the probe raises, execute() stops at the
        # error state and the delete never runs, so the instance has to
        # show that. Sparing it, because the task which happened to fail
        # was a probe, left a mutating operation that failed without
        # erroring its object (issue 4670).
        tasks = [model_tasks.health_check_kvm_process,
                 model_tasks.instance_delete]
        self.assertFalse(is_observational(nio_schema.model, tasks))

        _, op_uuid = create_and_enqueue(
            node_uuid=str(uuid4()), instance_uuid=str(uuid4()),
            tasks=tasks, priority=PRIORITY.user_facing)
        op = NodeInstOp.from_db(op_uuid)
        self.assertIsNotNone(op)

        inst = FakeInstance('created')
        instance_delete = mock.MagicMock()
        with mock.patch(
                'shakenfist.operations.node_inst_op.util_exceptions.'
                'ignore_exception'), \
                mock.patch(
                    'shakenfist.operations.node_inst_op.Instance.from_db',
                    return_value=inst), \
                mock.patch.object(
                    NodeInstOp, '_health_check_kvm_process',
                    side_effect=RuntimeError('boom')), \
                mock.patch.object(
                    NodeInstOp, '_instance_delete', instance_delete):
            op.execute()

        self.assertEqual(NodeInstOp.STATE_ERROR, op.state.value)
        instance_delete.assert_not_called()
        self.assertEqual(['created-error'], inst.state_sets)
