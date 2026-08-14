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

    def __init__(self, state_value, reject=(), fetch_dependencies=None):
        self._state_value = state_value
        self._reject = reject
        self.state_sets = []
        self.delete_errors = []
        self.placement = {'placement_attempts': 0}
        self.requested_placement = None
        self.placed_on = []
        self.fetch_dependencies = fetch_dependencies or []
        self.disk_fetch_calls = []

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

    def place_instance(self, node):
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

    def _redirect(self, inst, target_node):
        fake_scheduler = mock.MagicMock()
        fake_scheduler.find_candidates.side_effect = [
            exceptions.LowResourceException('full here'),
            [target_node],
        ]
        fake_scheduler.metrics = {target_node: {}}

        op = self._make_op()
        with mock.patch(
                'shakenfist.operations.node_inst_netdesc_op.scheduler.'
                'Scheduler', return_value=fake_scheduler):
            with mock.patch(
                    'shakenfist.operations.node_inst_netdesc_op.schema.'
                    'create_and_enqueue') as mock_enqueue:
                with mock.patch(
                        'shakenfist.operations.node_inst_netdesc_op.'
                        'add_event_multi'):
                    op._instance_preflight(inst)
        return op, mock_enqueue

    def test_redirect_uses_schema_create_and_enqueue(self):
        inst = FakeInstance('created')
        target_node = str(uuid4())

        op, mock_enqueue = self._redirect(inst, target_node)

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
        # ImageMissingFromCache (issue 3720).
        fetch_deps = [dependency(op_type=ObjectType.ARTIFACT_FETCH_OP,
                                 op_uuid=str(uuid4()))]
        inst = FakeInstance('created', fetch_dependencies=fetch_deps)
        target_node = str(uuid4())

        op, mock_enqueue = self._redirect(inst, target_node)

        self.assertEqual(
            [(target_node, op.priority, op.request_id,
              'fetch requested by instance start redirect')],
            inst.disk_fetch_calls)
        mock_enqueue.assert_called_once_with(
            target_node, op.instance_uuid, op.net_desc, op.tasks,
            op.priority, op.request_id, depends_on=fetch_deps)

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
