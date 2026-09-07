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

from shakenfist.instance import Instance
from shakenfist.schema.object_state import STATE_MESSAGE_MAX_LENGTH
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


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
