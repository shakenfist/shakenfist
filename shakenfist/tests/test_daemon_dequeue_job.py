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
        pool.usable_cpus = 8
        pool.metrics = {}
        pool.metrics_acquired_at = 0

        with mock.patch('shakenfist.daemons.daemon.config') as mock_config, \
                mock.patch.object(daemon.mariadb, 'get_node_metrics',
                                  return_value={'metrics': metrics}), \
                mock.patch.object(daemon.mariadb, 'dequeue_work_items',
                                  return_value=[]) as mock_dequeue:
            mock_config.NODE_UUID = NODE_UUID
            self.assertEqual(daemon.DequeueOutcome.NOTHING_ELIGIBLE,
                             pool.dequeue_job(mock.MagicMock()))
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


class DequeueJobPoolFullTestCase(base.ShakenFistTestCase):
    """A full worker pool is reported as such, and reads no queue.

    dequeue_job() used to answer False both for a full pool and for an
    empty queue read, so its caller slept the idle backoff on a saturated
    dispatcher -- a sleep which removes no database load, because this
    path makes no database call. See issue #3942.
    """

    def _saturated_daemon(self):
        pool = daemon.WorkerPoolDaemon.__new__(daemon.WorkerPoolDaemon)
        pool.usable_cpus = 8
        # max_workers is max(3, usable_cpus / 2), so five workers is over
        # the limit for an eight cpu node.
        pool.workers = {f'worker-{i}': mock.MagicMock() for i in range(5)}
        pool.metrics = {}
        pool.metrics_acquired_at = 0
        return pool

    def test_pool_full_is_distinguishable_from_an_empty_queue(self):
        pool = self._saturated_daemon()
        with mock.patch('shakenfist.daemons.daemon.config') as mock_config, \
                mock.patch.object(daemon.mariadb, 'dequeue_work_items',
                                  return_value=[]) as mock_dequeue:
            mock_config.NODE_UUID = NODE_UUID
            self.assertEqual(daemon.DequeueOutcome.POOL_FULL,
                             pool.dequeue_job(mock.MagicMock()))

        # And it got there without asking the database anything.
        self.assertEqual(0, mock_dequeue.call_count)


class DequeueJobConstructsRealJobTestCase(base.ShakenFistTestCase):
    """The dispatcher's arguments reach the real sf-queues Job.

    dequeue_job() hands each worker its arguments positionally, and the
    other tests here pass a mock as the processing class, so an arity or
    ordering mismatch against workitem.Job's constructor would go
    unnoticed. This drives the real class and checks the claim time and
    delivery count arrive where the execution duration event reads them
    (issue 3974).
    """

    def test_claim_time_and_deliveries_reach_the_job(self):
        from shakenfist.daemons.queues import workitem

        pool = daemon.WorkerPoolDaemon.__new__(daemon.WorkerPoolDaemon)
        pool.workers = {}
        pool.usable_cpus = 8
        pool.metrics = {}
        pool.metrics_acquired_at = 0

        items = [('q-one', 'job-1', {'op': 'a'}, 1),
                 ('q-two', 'job-2', {'op': 'b'}, 3)]
        with mock.patch('shakenfist.daemons.daemon.config') as mock_config, \
                mock.patch('shakenfist.daemons.daemon.time') as mock_time, \
                mock.patch('shakenfist.daemons.daemon.threading'), \
                mock.patch.object(daemon, 'clear_abort_path'), \
                mock.patch.object(daemon.mariadb, 'get_node_metrics',
                                  return_value={}), \
                mock.patch.object(daemon.mariadb, 'dequeue_work_items',
                                  return_value=items):
            mock_config.NODE_UUID = NODE_UUID
            mock_time.time.return_value = 12_345.0
            self.assertEqual(daemon.DequeueOutcome.STARTED,
                             pool.dequeue_job(workitem.Job))

        first = pool.workers['job-1']['object']
        second = pool.workers['job-2']['object']
        self.assertIsInstance(first, workitem.Job)
        self.assertEqual(
            ('q-one', 'job-1', {'op': 'a'}, 2, 12_345.0, 1),
            (first.queue_name, first.jobname, first.workitem,
             first.batch_size, first.dequeued_at, first.deliveries))
        # One claim time for the whole batch, but each item's own count.
        self.assertEqual(
            ('q-two', 'job-2', {'op': 'b'}, 2, 12_345.0, 3),
            (second.queue_name, second.jobname, second.workitem,
             second.batch_size, second.dequeued_at, second.deliveries))
