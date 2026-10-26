# Copyright 2026 Michael Still and contributors
"""Tests for ``NodeInstOp._collect_billing_statistics``.

The per-instance sweep queues this task for created instances, but the
instance may have moved on: enqueue_delete() aborts the queued operations it
finds, but a sweep enqueued after that walk, or one already executing, still
runs. These tests pin down that the task leaves an instance which is no longer
created alone, and that a disk or console log removed while it runs is
measured as absent rather than raising, since a task that raises errors its
operation.
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
from shakenfist.util import libvirt as util_libvirt


# Kept before setUp() patches LibvirtConnection out of util_libvirt.
RealLibvirtConnection = util_libvirt.LibvirtConnection


class FakeLibvirtError(Exception):
    def __init__(self, message='', code=1):
        super().__init__(message)
        self.code = code

    def get_error_code(self):
        return self.code


class FakeLibvirtModule:
    """Just enough of libvirt for the power state and error code checks."""
    libvirtError = FakeLibvirtError

    VIR_ERR_INTERNAL_ERROR = 1
    VIR_ERR_NO_DOMAIN = 42
    VIR_ERR_OPERATION_INVALID = 55

    VIR_DOMAIN_NOSTATE = 0
    VIR_DOMAIN_RUNNING = 1
    VIR_DOMAIN_BLOCKED = 2
    VIR_DOMAIN_PAUSED = 3
    VIR_DOMAIN_SHUTDOWN = 4
    VIR_DOMAIN_SHUTOFF = 5
    VIR_DOMAIN_CRASHED = 6
    VIR_DOMAIN_PMSUSPENDED = 7


class FakeDomain:
    def __init__(self, state, state_error=None):
        self._state = state
        self._state_error = state_error

    def isActive(self):
        return 1 if self._state != FakeLibvirtModule.VIR_DOMAIN_SHUTOFF else 0

    def state(self):
        if self._state_error:
            raise self._state_error
        return self._state, 0


class FakeInstance:
    """An instance double for driving _collect_billing_statistics directly."""

    def __init__(self, instance_path, disk_path, state=Instance.STATE_CREATED):
        self.uuid = str(uuid4())
        self.state = mock.Mock(value=state)
        self.instance_path = instance_path
        self.block_devices = {'devices': [{'path': disk_path, 'device': 'vda'}]}
        self.kvm_pid = None
        self.add_event = mock.Mock()


class _BillingStatisticsTestBase(base.ShakenFistTestCase):
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


class CollectBillingStatisticsTestCase(_BillingStatisticsTestBase):
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


class CollectBillingStatisticsLibvirtTestCase(_BillingStatisticsTestBase):
    """Libvirt's answers for a domain which is off, stopping or gone.

    These use the real LibvirtConnection against a fake libvirt module, so
    the error code checks are the ones production runs.
    """

    def _connect(self, domain):
        lc = RealLibvirtConnection()
        lc.libvirt = FakeLibvirtModule()
        lc.get_domain_from_sf_uuid = mock.Mock(return_value=domain)
        self.mock_connection.return_value.__enter__.return_value = lc

    def _collect(self, inst, extract_statistics=None):
        op = self._make_op()
        op.log = mock.MagicMock()
        with mock.patch(
                'shakenfist.operations.node_inst_op.util_libvirt.extract_statistics',
                side_effect=extract_statistics,
                return_value={'cpu usage': {'cpu time ns': 1}}) as mock_extract:
            op._collect_billing_statistics(inst)
        return op.log, mock_extract

    def test_powered_off_domain_records_its_static_statistics(self):
        self._connect(FakeDomain(FakeLibvirtModule.VIR_DOMAIN_SHUTOFF))
        inst = FakeInstance(self.instance_path, self.disk_path)
        log, mock_extract = self._collect(inst)

        mock_extract.assert_not_called()
        statistics = self._statistics(inst)
        self.assertEqual('off', statistics['power_state'])
        self.assertEqual('shutoff', statistics['libvirt_raw_power_state'])
        self.assertEqual(
            1024, statistics['disk usage']['vda']['actual bytes on disk'])
        self.assertEqual(len('console'), statistics['console_log_size'])
        log.warning.assert_not_called()

    def test_running_domain_records_its_runtime_statistics(self):
        self._connect(FakeDomain(FakeLibvirtModule.VIR_DOMAIN_RUNNING))
        inst = FakeInstance(self.instance_path, self.disk_path)
        _, mock_extract = self._collect(inst)

        mock_extract.assert_called_once()
        statistics = self._statistics(inst)
        self.assertEqual({'cpu time ns': 1}, statistics['cpu usage'])
        self.assertEqual('on', statistics['power_state'])

    def test_domain_which_stops_mid_read_keeps_the_rest_of_the_record(self):
        self._connect(FakeDomain(FakeLibvirtModule.VIR_DOMAIN_RUNNING))
        inst = FakeInstance(self.instance_path, self.disk_path)
        log, _ = self._collect(inst, extract_statistics=FakeLibvirtError(
            'Requested operation is not valid: domain is not running',
            code=FakeLibvirtModule.VIR_ERR_OPERATION_INVALID))

        statistics = self._statistics(inst)
        self.assertEqual(
            1024, statistics['disk usage']['vda']['actual bytes on disk'])
        self.assertEqual('on', statistics['power_state'])
        log.warning.assert_not_called()

    def test_domain_deleted_mid_read_is_not_a_warning(self):
        self._connect(FakeDomain(
            FakeLibvirtModule.VIR_DOMAIN_SHUTOFF,
            state_error=FakeLibvirtError(
                'Domain not found', code=FakeLibvirtModule.VIR_ERR_NO_DOMAIN)))
        inst = FakeInstance(self.instance_path, self.disk_path)
        log, _ = self._collect(inst)

        inst.add_event.assert_not_called()
        log.warning.assert_not_called()
        log.debug.assert_called_once()

    def test_other_libvirt_errors_are_still_warnings(self):
        self._connect(FakeDomain(FakeLibvirtModule.VIR_DOMAIN_RUNNING))
        inst = FakeInstance(self.instance_path, self.disk_path)
        log, _ = self._collect(inst, extract_statistics=FakeLibvirtError(
            'internal error', code=FakeLibvirtModule.VIR_ERR_INTERNAL_ERROR))

        inst.add_event.assert_not_called()
        log.warning.assert_called_once()
