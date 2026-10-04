# Copyright 2019 Michael Still and contributors
"""Regression tests for ``NodeInstOp._health_check_kvm_process``.

This method was deleted in commit 216fa31dd on the mistaken belief that
nothing called it -- see test_operation_task_dispatch.py for the dispatch
mechanism that calls it by name. It is restored here without its dead
delete branch: ``inst.power_state`` is a dict, so the old
``inst.power_state == 'on'`` comparison was always false, and wiring it
up correctly would error-delete every guest that powered itself off (F3
in docs/plans/PLAN-power-state-correctness.md). Only the stale-kvm_pid
clearing is live, and these tests pin that down: a missing qemu process
clears ``kvm_pid`` and never calls ``enqueue_delete_due_error``, even
when the power state dict says the instance is on.
"""
from unittest import mock
from uuid import uuid4

import psutil

from shakenfist.operations.node_inst_op import NodeInstOp
from shakenfist.schema.operations.node_inst_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class FakeInstance:
    """An instance double for driving _health_check_kvm_process directly."""

    def __init__(self, pid):
        self.uuid = str(uuid4())
        self._kvm_pid = pid
        self.power_state = {'power_state': 'on'}
        self.enqueue_delete_due_error = mock.Mock()

    @property
    def kvm_pid(self):
        return self._kvm_pid

    @kvm_pid.setter
    def kvm_pid(self, pid):
        self._kvm_pid = pid


class HealthCheckKvmProcessTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _make_op(self):
        _, op_uuid = create_and_enqueue(
            node_uuid=str(uuid4()),
            instance_uuid=str(uuid4()),
            tasks=[model_tasks.health_check_kvm_process],
            priority=PRIORITY.user_facing,
        )
        op = NodeInstOp.from_db(op_uuid)
        self.assertIsNotNone(op)
        return op

    def test_live_pid_leaves_kvm_pid_and_does_not_delete(self):
        inst = FakeInstance(pid=1234)
        op = self._make_op()

        with mock.patch(
                'shakenfist.operations.node_inst_op.psutil.Process') as mock_process:
            op._health_check_kvm_process(inst)

        mock_process.assert_called_once_with(1234)
        self.assertEqual(1234, inst.kvm_pid)
        inst.enqueue_delete_due_error.assert_not_called()

    def test_dead_pid_clears_kvm_pid_and_does_not_delete(self):
        inst = FakeInstance(pid=1234)
        op = self._make_op()

        with mock.patch(
                'shakenfist.operations.node_inst_op.psutil.Process',
                side_effect=psutil.NoSuchProcess(1234)):
            op._health_check_kvm_process(inst)

        self.assertIsNone(inst.kvm_pid)
        # The power state dict says "on", but a missing qemu process is a
        # guest that powered itself off, not an error -- see F3. This must
        # never enqueue a delete.
        inst.enqueue_delete_due_error.assert_not_called()

    def test_missing_pid_file_error_clears_kvm_pid_and_does_not_delete(self):
        inst = FakeInstance(pid=1234)
        op = self._make_op()

        with mock.patch(
                'shakenfist.operations.node_inst_op.psutil.Process',
                side_effect=FileNotFoundError()):
            op._health_check_kvm_process(inst)

        self.assertIsNone(inst.kvm_pid)
        inst.enqueue_delete_due_error.assert_not_called()

    def test_no_pid_does_nothing(self):
        inst = FakeInstance(pid=None)
        op = self._make_op()

        with mock.patch(
                'shakenfist.operations.node_inst_op.psutil.Process') as mock_process:
            op._health_check_kvm_process(inst)

        mock_process.assert_not_called()
        self.assertIsNone(inst.kvm_pid)
        inst.enqueue_delete_due_error.assert_not_called()
