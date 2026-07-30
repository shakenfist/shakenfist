# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.operations.baseoperation import get_all_background_node_queues
from shakenfist.operations.baseoperation import get_all_user_facing_node_queues
from shakenfist.tests import base


NODE_UUID = 'aabbccdd-1234-5678-abcd-000000000002'


class DequeueJobDiskPressureTestCase(base.ShakenFistTestCase):
    """WorkerPoolDaemon.dequeue_job() gates high_io background queues on the
    disk_busy_time_delta_per_second metric published by the resources daemon.

    The metric is a float (counter delta divided by sample spacing) and may
    round-trip as a string like '16.6', so the daemon must parse it with
    float() -- int() would raise ValueError.
    """

    def _dequeue_queues(self, metrics):
        """Run one dequeue_job() pass with the given node metrics and return
        the queue names it asked mariadb.dequeue_work_items() for."""
        pool = daemon.WorkerPoolDaemon.__new__(daemon.WorkerPoolDaemon)
        pool.workers = {}
        pool.present_cpus = 8
        pool.metrics = {}
        pool.metrics_acquired_at = 0

        with mock.patch('shakenfist.daemons.daemon.config') as mock_config, \
                mock.patch.object(daemon.mariadb, 'get_node_metrics',
                                  return_value={'metrics': metrics}), \
                mock.patch.object(daemon.mariadb, 'dequeue_work_items',
                                  return_value=[]) as mock_dequeue:
            mock_config.NODE_UUID = NODE_UUID
            self.assertFalse(pool.dequeue_job(mock.MagicMock()))
            self.assertEqual(1, mock_dequeue.call_count)
            return mock_dequeue.call_args.args[0]

    def test_idle_disk_includes_high_io_queues(self):
        queue_names = self._dequeue_queues(
            {'disk_busy_time_delta_per_second': '16.6'})
        self.assertEqual(
            get_all_user_facing_node_queues(NODE_UUID) +
            get_all_background_node_queues(NODE_UUID),
            queue_names)

    def test_busy_disk_gates_high_io_queues(self):
        queue_names = self._dequeue_queues(
            {'disk_busy_time_delta_per_second': 950.5})
        self.assertEqual(
            get_all_user_facing_node_queues(NODE_UUID) +
            [q for q in get_all_background_node_queues(NODE_UUID)
             if 'high_io' not in q],
            queue_names)

    def test_missing_metric_defaults_to_idle(self):
        queue_names = self._dequeue_queues({})
        self.assertEqual(
            get_all_user_facing_node_queues(NODE_UUID) +
            get_all_background_node_queues(NODE_UUID),
            queue_names)
