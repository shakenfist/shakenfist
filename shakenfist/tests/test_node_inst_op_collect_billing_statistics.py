# Copyright 2026 Michael Still and contributors
"""Tests for ``NodeInstOp._collect_billing_statistics``.

The per-instance sweep queues this task for created instances, but a queued
sweep is not cancelled when the instance is later deleted, and a task that
raises errors its instance. These tests pin down that the task leaves an
instance which is no longer created alone, and that a disk or console log
removed while it runs is measured as absent rather than raising.
"""
import os
import tempfile
from unittest import mock
from uuid import uuid4

from shakenfist.instance import Instance
from shakenfist.operations.node_inst_op import NodeInstOp
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.node_inst_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_op import model_tasks
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class FakeLibvirtError(Exception):
    pass


class FakeInstance:
    """An instance double for driving _collect_billing_statistics directly."""

    def __init__(self, instance_path, disk_path, state=Instance.STATE_CREATED):
        self.uuid = str(uuid4())
        self.state = mock.Mock(value=state)
        self.instance_path = instance_path
        self.block_devices = {'devices': [{'path': disk_path, 'device': 'vda'}]}
        self.kvm_pid = None
        self.add_event = mock.Mock()


class CollectBillingStatisticsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.instance_path = tmpdir.name
        self.disk_path = os.path.join(self.instance_path, 'vda')
        with open(self.disk_path, 'wb') as f:
            f.write(b'x' * 1024)
        with open(os.path.join(self.instance_path, 'console.log'), 'w') as f:
            f.write('console')

        lc = mock.MagicMock()
        lc.get_domain_from_sf_uuid.return_value = None
        lc.libvirt.libvirtError = FakeLibvirtError
        patcher = mock.patch(
            'shakenfist.operations.node_inst_op.util_libvirt.LibvirtConnection')
        self.mock_connection = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_connection.return_value.__enter__.return_value = lc

    def _make_op(self):
        _, op_uuid = create_and_enqueue(
            node_uuid=str(uuid4()),
            instance_uuid=str(uuid4()),
            tasks=[model_tasks.collect_billing_statistics],
            priority=PRIORITY.user_facing,
        )
        op = NodeInstOp.from_db(op_uuid)
        self.assertIsNotNone(op)
        return op

    def _statistics(self, inst):
        inst.add_event.assert_called_once()
        return inst.add_event.call_args.kwargs['extra']

    def test_created_instance_is_measured(self):
        inst = FakeInstance(self.instance_path, self.disk_path)
        self._make_op()._collect_billing_statistics(inst)

        statistics = self._statistics(inst)
        self.assertEqual(
            1024, statistics['disk usage']['vda']['actual bytes on disk'])
        self.assertEqual(len('console'), statistics['console_log_size'])

    def test_instance_no_longer_created_is_skipped(self):
        for state in [Instance.STATE_DELETE_WAIT, Instance.STATE_DELETED,
                      Instance.STATE_ERROR]:
            with self.subTest(state=state):
                self.mock_connection.reset_mock()
                inst = FakeInstance(self.instance_path, self.disk_path,
                                    state=state)
                self._make_op()._collect_billing_statistics(inst)

                self.mock_connection.assert_not_called()
                inst.add_event.assert_not_called()

    def test_files_removed_during_measurement_are_absent(self):
        # The delete removes the files after any existence check would have
        # passed, so os.stat() is the call that sees them gone.
        inst = FakeInstance(self.instance_path, self.disk_path)
        with mock.patch('shakenfist.operations.node_inst_op.os.path.exists',
                        return_value=True), \
                mock.patch('shakenfist.operations.node_inst_op.os.stat',
                           side_effect=FileNotFoundError()):
            self._make_op()._collect_billing_statistics(inst)

        statistics = self._statistics(inst)
        self.assertNotIn('vda', statistics['disk usage'])
        self.assertEqual(0, statistics['console_log_size'])

    def test_deleting_instance_is_not_errored_by_dispatch(self):
        # Any failure measuring a guest mid-delete, here libvirt failing in
        # a way it does not report as a libvirt error, must not reach
        # dispatch_task, which would error the instance.
        lc = self.mock_connection.return_value.__enter__.return_value
        lc.get_domain_from_sf_uuid.side_effect = RuntimeError('domain gone')
        inst = FakeInstance(self.instance_path, self.disk_path,
                            state=Instance.STATE_DELETE_WAIT)
        original_state = inst.state
        op = self._make_op()
        with mock.patch('shakenfist.operations.node_inst_op.Instance.from_db',
                        return_value=inst):
            op.dispatch_task(model_tasks.collect_billing_statistics)

        self.assertIs(original_state, inst.state)
