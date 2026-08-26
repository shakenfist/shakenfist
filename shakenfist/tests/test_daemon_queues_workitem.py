# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.daemons.queues import workitem
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.tests import base


OP_UUID = 'aabbccdd-1234-5678-abcd-000000000001'
DEP_UUID = 'aabbccdd-1234-5678-abcd-000000000002'
QUEUE_NAME = f'{OP_UUID}-clusteroperation-user_waiting'


class DependencyDeferDelayTest(base.ShakenFistTestCase):
    """Unit tests for the stateless dependency-wait back-off schedule.

    sf-net keeps its back-off depth in a per-worker map, but sf-queues jobs
    are one-shot threads, so the delay is derived from the defer_count the
    work item persists (issue 3863).
    """

    def test_first_defer_uses_initial_delay(self):
        self.assertAlmostEqual(
            workitem.INITIAL_DEFER_DELAY, workitem.dependency_defer_delay(0))
        self.assertAlmostEqual(0.1, workitem.INITIAL_DEFER_DELAY)

    def test_defer_schedule_progression(self):
        """The full schedule: 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 15.0,
        15.0 -- the last two clamp at MAX_DEFER_DELAY. This matches sf-net's
        schedule in shakenfist/daemons/network/workitem.py."""
        expected_delays = [0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 15.0, 15.0]
        for defer_count, expected in enumerate(expected_delays):
            self.assertAlmostEqual(
                expected, workitem.dependency_defer_delay(defer_count),
                places=6)

    def test_cap_holds_at_fourteen_defers(self):
        """The 215.69 second maximum in the issue 3863 sample was 14
        consecutive flat 15 second defers; the same depth now sits at the
        15 second cap rather than 15 seconds per defer from the start."""
        self.assertAlmostEqual(
            workitem.MAX_DEFER_DELAY, workitem.dependency_defer_delay(14))

    def test_huge_defer_count_does_not_overflow(self):
        self.assertAlmostEqual(
            workitem.MAX_DEFER_DELAY, workitem.dependency_defer_delay(100000))


class DependencyWaitDeferTest(base.ShakenFistTestCase):
    """Verify _cluster_operation_execute passes the backed-off delay to
    op.defer() for both depends_on and runs_after waits, rather than taking
    defer()'s flat 15 second default."""

    def _run_defer(self, defer_count, dep_kind):
        mock_op = mock.MagicMock()
        mock_op.state.value = BaseClusterOperation.STATE_QUEUED
        mock_op.depends_on = []
        mock_op.runs_after = []

        dep = {'op_type': 'net_op', 'op_uuid': DEP_UUID}
        setattr(mock_op, dep_kind, [dep])

        mock_dep_op = mock.MagicMock()
        mock_dep_op.state.value = BaseClusterOperation.STATE_QUEUED

        op_class = mock.MagicMock()
        op_class.from_db.return_value = mock_op
        dep_class = mock.MagicMock()
        dep_class.from_db.return_value = mock_dep_op
        classes = {'node_inst_netdesc_op': op_class, 'net_op': dep_class}

        job = workitem.Job.__new__(workitem.Job)
        job.queue_name = QUEUE_NAME
        job.batch_size = None
        job.log = mock.MagicMock()
        job.workitem = {
            'operation_type': 'node_inst_netdesc_op',
            'operation_uuid': OP_UUID,
            'defer_count': defer_count
        }

        with mock.patch(
                'shakenfist.daemons.queues.workitem.get_object_class',
                side_effect=lambda op_type: classes[op_type]):
            job._cluster_operation_execute()

        return mock_op, mock_dep_op

    def test_depends_on_first_defer_uses_initial_delay(self):
        mock_op, mock_dep_op = self._run_defer(0, 'depends_on')
        mock_op.defer.assert_called_once_with(
            waiting_on=[mock_dep_op], delay=workitem.INITIAL_DEFER_DELAY)

    def test_depends_on_delay_grows_with_defer_count(self):
        mock_op, mock_dep_op = self._run_defer(3, 'depends_on')
        mock_op.defer.assert_called_once_with(
            waiting_on=[mock_dep_op],
            delay=workitem.dependency_defer_delay(3))
        self.assertAlmostEqual(
            0.8, mock_op.defer.call_args.kwargs['delay'], places=6)

    def test_depends_on_delay_caps_at_max(self):
        mock_op, mock_dep_op = self._run_defer(14, 'depends_on')
        mock_op.defer.assert_called_once_with(
            waiting_on=[mock_dep_op], delay=workitem.MAX_DEFER_DELAY)

    def test_runs_after_first_defer_uses_initial_delay(self):
        mock_op, mock_dep_op = self._run_defer(0, 'runs_after')
        mock_op.defer.assert_called_once_with(
            waiting_on=[mock_dep_op], delay=workitem.INITIAL_DEFER_DELAY)

    def test_runs_after_delay_caps_at_max(self):
        mock_op, mock_dep_op = self._run_defer(14, 'runs_after')
        mock_op.defer.assert_called_once_with(
            waiting_on=[mock_dep_op], delay=workitem.MAX_DEFER_DELAY)

    def test_defer_not_called_when_dependency_complete(self):
        mock_op = mock.MagicMock()
        mock_op.state.value = BaseClusterOperation.STATE_QUEUED
        mock_op.depends_on = [{'op_type': 'net_op', 'op_uuid': DEP_UUID}]
        mock_op.runs_after = []

        mock_dep_op = mock.MagicMock()
        mock_dep_op.state.value = BaseClusterOperation.STATE_COMPLETE

        op_class = mock.MagicMock()
        op_class.from_db.return_value = mock_op
        dep_class = mock.MagicMock()
        dep_class.from_db.return_value = mock_dep_op
        classes = {'node_inst_netdesc_op': op_class, 'net_op': dep_class}

        job = workitem.Job.__new__(workitem.Job)
        job.queue_name = QUEUE_NAME
        job.batch_size = None
        job.log = mock.MagicMock()
        job.workitem = {
            'operation_type': 'node_inst_netdesc_op',
            'operation_uuid': OP_UUID,
            'defer_count': 2
        }

        with mock.patch(
                'shakenfist.daemons.queues.workitem.get_object_class',
                side_effect=lambda op_type: classes[op_type]):
            job._cluster_operation_execute()

        mock_op.defer.assert_not_called()
        mock_op.execute.assert_called_once()
