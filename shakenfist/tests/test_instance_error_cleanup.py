# Copyright 2019 Michael Still and contributors
"""Regression tests for ``Instance.enqueue_delete_due_error`` (issue 4112).

A qemu-img failure produced a 549 character error message, which the
255 character ``object_states.message`` column rejected with DataError
1406. The resulting RuntimeError escaped from inside dispatch_task's
exception handler, so the instance was neither annotated nor enqueued
for deletion. Two behaviours pin the fix: the message is clipped before
it reaches the database, and a failure to record the message no longer
prevents ``enqueue_delete()`` from running.
"""

from unittest import mock
from uuid import uuid4

from shakenfist.constants import CAPACITY_GUARD_STAGE
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import exceptions
from shakenfist.instance import Instance
from shakenfist.operations.node_inst_netdesc_op import AbortInstanceStart
from shakenfist.operations.node_inst_netdesc_op import NodeInstNetdescOp
from shakenfist.schema.object_state import STATE_MESSAGE_MAX_LENGTH
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.node_inst_netdesc_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_netdesc_op import model_tasks
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


UNREADABLE_EVENT = (
    'delete not enqueued after error, outstanding operations unreadable; '
    'delete the instance to clean it up')


def _unreadable_listing(*args, raise_on_error=False, **kwargs):
    """A cluster operation listing which honours the raise_on_error
    contract and cannot be read."""
    if raise_on_error:
        raise exceptions.DatabaseUnavailable('boom')
    return []


class EnqueueDeleteDueErrorTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    @mock.patch('shakenfist.instance.Instance.enqueue_delete')
    def test_long_error_message_clipped_and_cleanup_enqueued(
            self, mock_enqueue_delete):
        inst = self.mock_mariadb.create_instance(
            'longerror', set_state=Instance.STATE_CREATING)

        inst.enqueue_delete_due_error('Unhandled error: ' + 'x' * 549)

        self.assertEqual(Instance.STATE_CREATING_ERROR, inst.state.value)
        self.assertIsNotNone(inst.error)
        self.assertEqual(STATE_MESSAGE_MAX_LENGTH, len(inst.error))
        self.assertTrue(inst.error.endswith('...'))
        self.assertTrue(inst.error.startswith('Unhandled error: '))
        mock_enqueue_delete.assert_called_once_with()

    @mock.patch('shakenfist.instance.Instance.enqueue_delete')
    def test_error_write_failure_does_not_block_cleanup(
            self, mock_enqueue_delete):
        inst = self.mock_mariadb.create_instance(
            'writefail', set_state=Instance.STATE_CREATING)

        with mock.patch.object(
                Instance, 'error', new_callable=mock.PropertyMock,
                side_effect=RuntimeError('Failed to write error message')):
            inst.enqueue_delete_due_error('Unhandled error: boom')

        self.assertEqual(Instance.STATE_CREATING_ERROR, inst.state.value)
        mock_enqueue_delete.assert_called_once_with()


class PreflightAbortMessageTestCase(base.ShakenFistTestCase):
    """A preflight capacity abort must still carry its message once the
    delete it queues has moved the instance to state error (issue 4776).

    sf_instance's capacity wait identifies a preflight abort by the
    instance's error_message, and usually reads the instance back after
    the delete has finished. Clearing the message on the preflight-error
    to error transition left it nothing to match, so it failed the task
    instead of replaying the create.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    @mock.patch('shakenfist.instance.Instance.enqueue_delete')
    def test_message_survives_the_delete(self, mock_enqueue_delete):
        inst = self.mock_mariadb.create_instance(
            'preflightabort', set_state=Instance.STATE_PREFLIGHT)

        inst.enqueue_delete_due_error('Unable to find suitable node')
        self.assertEqual(Instance.STATE_PREFLIGHT_ERROR, inst.state.value)
        mock_enqueue_delete.assert_called_once_with()

        inst._delete_globally()

        refetched = Instance.from_db(inst.uuid)
        self.assertEqual(Instance.STATE_ERROR, refetched.state.value)
        self.assertEqual('Unable to find suitable node', refetched.error)


class PreflightAbortStageTestCase(base.ShakenFistTestCase):
    """A capacity abort reaches the instance as a stage, not just prose.

    Issue 4568: preflight knew which scheduling stage refused the
    instance and whether a wait could clear it, and then threw both away
    -- the abort carried a message and nothing else, so a client had to
    match two exact strings to recognise a transient capacity refusal.
    The instance now publishes the stage and the server's own transient
    verdict beside the message.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _abort(self, name, message, stage):
        inst = self.mock_mariadb.create_instance(
            name, set_state=Instance.STATE_PREFLIGHT)
        with mock.patch('shakenfist.instance.Instance.enqueue_delete'):
            inst.enqueue_delete_due_error(message, stage=stage)
        return inst

    def _view(self, inst):
        # The reference grouping in external_view() reads the caller's
        # namespace out of a JWT, so it needs a request context these
        # tests have no interest in standing up.
        with mock.patch(
                'shakenfist.instance.references_to_grouped_dict',
                return_value={}):
            return inst.external_view()

    def test_transient_stage_is_published_as_worth_waiting_out(self):
        inst = self._abort(
            'transientabort', 'Unable to find suitable node',
            CAPACITY_GUARD_STAGE)

        view = self._view(inst)
        self.assertEqual('Unable to find suitable node',
                         view['error_message'])
        self.assertEqual(CAPACITY_GUARD_STAGE, view['error_stage'])
        self.assertTrue(view['error_transient'])

    def test_permanent_stage_is_published_as_not_worth_waiting_out(self):
        # The whole point of publishing the stage rather than the class:
        # a request for more vCPUs than any node can host is refused
        # identically after every instance on the cluster is deleted.
        inst = self._abort(
            'permanentabort', 'Too many start attempts',
            'cpu_max_per_instance')

        view = self._view(inst)
        self.assertEqual('cpu_max_per_instance', view['error_stage'])
        self.assertFalse(view['error_transient'])

    def test_an_error_which_is_not_a_refusal_carries_no_stage(self):
        inst = self._abort('plainabort', 'Missing network', None)

        view = self._view(inst)
        self.assertEqual('Missing network', view['error_message'])
        self.assertIsNone(view['error_stage'])
        self.assertFalse(view['error_transient'])

    def test_a_later_error_replaces_the_stage_of_an_earlier_refusal(self):
        inst = self._abort(
            'restaged', 'Unable to find suitable node', CAPACITY_GUARD_STAGE)

        with mock.patch('shakenfist.instance.Instance.enqueue_delete'):
            inst.enqueue_delete_due_error('instance failed to power on')

        view = self._view(inst)
        self.assertIsNone(view['error_stage'])
        self.assertFalse(view['error_transient'])

    def test_a_healthy_instance_publishes_no_refusal(self):
        inst = self.mock_mariadb.create_instance(
            'healthy', set_state=Instance.STATE_CREATED)

        view = self._view(inst)
        self.assertIsNone(view['error_message'])
        self.assertIsNone(view['error_stage'])
        self.assertFalse(view['error_transient'])

    def test_the_stage_survives_the_delete_the_abort_queues(self):
        # The same reasoning as issue 4776's message test above: the
        # client usually reads the instance back after the delete has
        # moved it from preflight-error to error, so an annotation which
        # does not survive that transition is not there when it looks.
        inst = self._abort(
            'stagesurvives', 'Unable to find suitable node',
            CAPACITY_GUARD_STAGE)

        inst._delete_globally()

        refetched = Instance.from_db(inst.uuid)
        self.assertEqual(Instance.STATE_ERROR, refetched.state.value)
        view = self._view(refetched)
        self.assertEqual(CAPACITY_GUARD_STAGE, view['error_stage'])
        self.assertTrue(view['error_transient'])


class UnreadableOperationsTestCase(base.ShakenFistTestCase):
    """Instance.enqueue_delete() refuses to enqueue a delete when the
    operations targeting the instance cannot be read (issue 4671). The
    error path calls it from inside exception handlers, which must finish:
    the instance stays errored, nothing is enqueued, and nothing escapes.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _error_with_unreadable_operations(self, inst, error_msg):
        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_targets_for_object',
                side_effect=_unreadable_listing), \
                mock.patch.object(
                    Instance, 'placement', new_callable=mock.PropertyMock,
                    return_value={'node': str(uuid4())}), \
                mock.patch('shakenfist.instance.nio_create_and_enqueue') as enqueue, \
                mock.patch.object(
                    Instance, 'add_event', autospec=True) as add_event:
            inst.enqueue_delete_due_error(error_msg)
        return enqueue, add_event

    def test_instance_stays_errored_and_nothing_is_enqueued(self):
        inst = self.mock_mariadb.create_instance(
            'unreadable', set_state=Instance.STATE_CREATING)

        enqueue, add_event = self._error_with_unreadable_operations(
            inst, 'instance failed to power on')

        self.assertEqual(Instance.STATE_CREATING_ERROR, inst.state.value)
        self.assertEqual('instance failed to power on', inst.error)
        enqueue.assert_not_called()
        self.assertEqual(
            [mock.call(inst, EVENT_TYPE_AUDIT, UNREADABLE_EVENT,
                       extra={'error': mock.ANY}, log_as_error=True)],
            [c for c in add_event.call_args_list
             if c.args[1] == EVENT_TYPE_AUDIT])

    def test_operation_error_handler_finishes(self):
        # The start operation's handler for an abandoned start errors the
        # instance and then marks the operation aborted. A raise from the
        # first would leave the operation executing forever.
        inst = self.mock_mariadb.create_instance(
            'unreadable', set_state=Instance.STATE_CREATING)
        _, op_uuid = create_and_enqueue(
            node_uuid=str(uuid4()), instance_uuid=str(inst.uuid),
            net_desc=[], tasks=[model_tasks.instance_preflight],
            priority=PRIORITY.user_waiting)
        op = NodeInstNetdescOp.from_db(op_uuid)
        op.state = NodeInstNetdescOp.STATE_EXECUTING

        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_targets_for_object',
                side_effect=_unreadable_listing), \
                mock.patch.object(
                    Instance, 'placement', new_callable=mock.PropertyMock,
                    return_value={'node': op.node_uuid}), \
                mock.patch('shakenfist.instance.nio_create_and_enqueue') as enqueue, \
                mock.patch(
                    'shakenfist.operations.node_inst_netdesc_op.Instance.from_db',
                    return_value=inst), \
                mock.patch.object(
                    NodeInstNetdescOp, '_instance_preflight',
                    side_effect=AbortInstanceStart(op, 'no room')):
            op.dispatch_task(model_tasks.instance_preflight)

        self.assertEqual(NodeInstNetdescOp.STATE_ABORT, op.state.value)
        self.assertEqual(Instance.STATE_CREATING_ERROR, inst.state.value)
        self.assertEqual('no room', inst.error)
        enqueue.assert_not_called()
