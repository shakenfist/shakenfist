# Copyright 2019 Michael Still and contributors

import time
from unittest import mock

from shakenfist.constants import DISK_BUSY_PER_SECOND_METRIC
from shakenfist.daemons import daemon
from shakenfist.tests import base


class WorkerPoolHighIOGateTestCase(base.ShakenFistTestCase):
    """The dispatcher must skip high_io background queues on a busy disk.

    Issue 3568: the gate read ``disk_busy_time_delta_per_seconds`` (with a
    trailing s) which the resources daemon never publishes, so the ``.get()``
    default of zero meant the protection never engaged. It also parsed with
    ``int()``, which raises ValueError on the float string the resources
    daemon actually publishes.
    """

    def _make_daemon(self, metrics_values):
        # Construct a WorkerPoolDaemon without running __init__ (which touches
        # setproctitle, logging, libvirt and the filesystem). We only need the
        # attributes dequeue_job() actually reads. metrics_acquired_at is set
        # to now so dequeue_job() does not try to refresh from the database.
        d = daemon.WorkerPoolDaemon.__new__(daemon.WorkerPoolDaemon)
        d.workers = {}
        d.present_cpus = 8
        d.metrics = {'metrics': metrics_values}
        d.metrics_acquired_at = time.time()
        return d

    def _eligible_queues(self, metrics_values):
        d = self._make_daemon(metrics_values)

        with mock.patch(
                'shakenfist.daemons.daemon.get_all_user_facing_node_queues',
                return_value=['node1-clusteroperation-user_facing']), \
            mock.patch(
                'shakenfist.daemons.daemon.get_all_background_node_queues',
                return_value=['node1-clusteroperation-background',
                              'node1-clusteroperation-background_high_io']), \
            mock.patch(
                'shakenfist.daemons.daemon.mariadb.dequeue_work_items',
                return_value=[]) as mock_dequeue:
            self.assertFalse(d.dequeue_job(None))

        self.assertEqual(1, mock_dequeue.call_count)
        return mock_dequeue.call_args[0][0]

    def test_high_io_skipped_when_disk_busy(self):
        # The published value is a float string; 966.6 ms/s is above the 800
        # gating threshold.
        queues = self._eligible_queues(
            {DISK_BUSY_PER_SECOND_METRIC: '966.6'})
        self.assertNotIn('node1-clusteroperation-background_high_io', queues)
        self.assertIn('node1-clusteroperation-background', queues)
        self.assertIn('node1-clusteroperation-user_facing', queues)

    def test_high_io_eligible_when_disk_idle(self):
        queues = self._eligible_queues(
            {DISK_BUSY_PER_SECOND_METRIC: '16.6'})
        self.assertIn('node1-clusteroperation-background_high_io', queues)
        self.assertIn('node1-clusteroperation-background', queues)

    def test_high_io_eligible_when_metric_absent(self):
        # A node with no previous counter reading publishes no delta metrics
        # at all; the gate must treat that as an idle disk.
        queues = self._eligible_queues({})
        self.assertIn('node1-clusteroperation-background_high_io', queues)

    def test_metric_key_matches_published_spelling(self):
        # There have historically been three spellings of this key in the
        # tree (_per_sec, _per_second, _per_seconds). The shared constant is
        # the wire format the resources daemon publishes; changing it breaks
        # rolling upgrades against nodes publishing the old key.
        self.assertEqual(
            'disk_busy_time_delta_per_second', DISK_BUSY_PER_SECOND_METRIC)
