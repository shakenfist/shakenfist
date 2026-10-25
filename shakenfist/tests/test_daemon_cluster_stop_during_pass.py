# Copyright 2026 Michael Still and contributors

"""A SIGTERM during a maintenance pass ends the pass at the next item.

The elected loop used to look at its abort path only between passes, so a
stop requested mid-pass waited for the rest of it, and a pass could start
the cluster-wide cleanup after the abort file was already set. A slow pass
outlived systemd's 30 second TimeoutStopSec and sf-cluster was SIGKILLed
(#4576).
"""

import os
import tempfile
import threading
from unittest import mock

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base
from shakenfist.tests.test_daemon_cluster_watchdog import FakeIPAM


class StopDuringPassTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.abort_path = os.path.join(self.tempdir.name, 'cluster.abort')

    def _stop(self, *args, **kwargs):
        with open(self.abort_path, 'w') as f:
            f.write('stop')

    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.abort_path = self.abort_path
        m.lock = mock.MagicMock()
        m.lock.lost_event = threading.Event()
        m.is_elected = True
        m.pet_watchdog = mock.MagicMock()
        m._force_capacity_reconcile_if_unguarded = mock.MagicMock()
        m._run_due_scheduled_jobs = mock.MagicMock()
        m._cluster_wide_cleanup = mock.MagicMock()
        return m

    def test_a_pass_requested_after_the_stop_does_nothing(self):
        m = self._make_monitor()
        self._stop()
        self.assertRaises(cluster_main.MaintenancePassAbandoned, m._maintenance_pass, 0)
        m._force_capacity_reconcile_if_unguarded.assert_not_called()
        m._run_due_scheduled_jobs.assert_not_called()
        m._cluster_wide_cleanup.assert_not_called()

    def test_a_stop_during_the_scheduled_jobs_does_not_start_the_cleanup(self):
        # The sfcbr case: SIGTERM arrived during the scheduled jobs and the
        # cleanup then started 9.5 seconds after it.
        m = self._make_monitor()
        m._run_due_scheduled_jobs.side_effect = self._stop
        self.assertRaises(cluster_main.MaintenancePassAbandoned, m._maintenance_pass, 0)
        m._cluster_wide_cleanup.assert_not_called()

    def test_an_abandoned_pass_is_not_recorded_as_an_exception(self):
        # Each phase swallows its own errors through ignore_exception; the
        # abandonment must get past that rather than being logged as a fault
        # and the next phase started.
        m = self._make_monitor()
        m._force_capacity_reconcile_if_unguarded.side_effect = \
            lambda: (self._stop(), m._maintenance_checkpoint())
        with mock.patch('shakenfist.daemons.cluster.main.util_exceptions') as mock_exc:
            self.assertRaises(cluster_main.MaintenancePassAbandoned, m._maintenance_pass, 0)
        mock_exc.ignore_exception.assert_not_called()
        m._run_due_scheduled_jobs.assert_not_called()

    def test_a_pass_without_a_stop_runs_every_phase(self):
        m = self._make_monitor()
        m._maintenance_pass(0)
        m._force_capacity_reconcile_if_unguarded.assert_called_once()
        m._run_due_scheduled_jobs.assert_called_once()
        m._cluster_wide_cleanup.assert_called_once_with(0)

    def test_no_further_scheduled_job_starts_after_a_stop(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.abort_path = self.abort_path
        m.pet_watchdog = mock.MagicMock()
        ran = []
        jobs = []
        for i in range(3):
            job = mock.MagicMock()
            job.should_run = True
            job.__lt__ = lambda self, other: id(self) < id(other)
            jobs.append(job)
        jobs.sort()
        jobs[0].run.side_effect = lambda: (ran.append(0), self._stop())
        jobs[1].run.side_effect = lambda: ran.append(1)
        jobs[2].run.side_effect = lambda: ran.append(2)
        with mock.patch.object(cluster_main.schedule, 'jobs', jobs):
            self.assertRaises(cluster_main.MaintenancePassAbandoned, m._run_due_scheduled_jobs)
        self.assertEqual([0], ran)

    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_the_cleanup_stops_at_the_next_item(self, mock_mariadb, mock_ipam, mock_network):
        mock_mariadb.delete_stale_transfers.return_value = 0
        mock_mariadb.delete_stale_cluster_operation_targets.return_value = 0
        ipams = [FakeIPAM(), FakeIPAM(), FakeIPAM()]
        mock_ipam.IPAMs.return_value = ipams
        # The first IPAM's network lookup is where the stop arrives.
        mock_network.Network.from_db.side_effect = lambda *a, **k: self._stop()

        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.abort_path = self.abort_path
        m.lock = mock.MagicMock()
        m.lock.lost_event = threading.Event()
        m.pet_watchdog = mock.MagicMock()

        self.assertRaises(cluster_main.MaintenancePassAbandoned, m._cluster_wide_cleanup, 0)
        self.assertEqual(1, mock_network.Network.from_db.call_count)
        mock_network.floating_network.assert_not_called()
