# Copyright 2026 Michael Still and contributors
#
# Tests that the cluster maintainer pets the systemd watchdog during
# its long work phases. The elected loop sleeps via lock.lost_event.wait()
# rather than idle(), and _cluster_wide_cleanup iterates over potentially
# large blob/artifact/node collections, so both must pet explicitly to
# survive WatchdogSec once it is armed.

import threading
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base


class FakeIPAM:
    def __init__(self):
        # update_time well in the past so the per-IPAM body is reached
        # (the pet fires before the recency check regardless). Mark the
        # IPAM already deleted so the body takes no destructive action
        # and the loop proceeds cleanly to the next item.
        self.state = mock.MagicMock()
        self.state.update_time = 0
        self.network_uuid = 'no-such-network'
        self.state.value = dbo.STATE_DELETED


class ClusterWatchdogTestCase(base.ShakenFistTestCase):
    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = mock.MagicMock()
        # A held lease so the early lost_event gate does not short-circuit.
        m.lock.lost_event = threading.Event()
        m.is_elected = True
        m.pet_watchdog = mock.MagicMock()
        return m

    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_cleanup_pets_per_ipam(self, mock_mariadb, mock_ipam, mock_network):
        # Drive far enough to reach the per-IPAM loop and confirm a pet
        # fires for each item. Make the floating_network() absent so the
        # cleanup short-circuits the floating-IP loop and the rest of the
        # heavy body, keeping the test focused.
        mock_mariadb.delete_stale_transfers.return_value = 0
        mock_mariadb.delete_stale_cluster_operation_targets.return_value = 0
        mock_ipam.IPAMs.return_value = [FakeIPAM(), FakeIPAM()]
        # No associated network -> the IPAM body runs (but state is not
        # STATE_DELETED so no destructive action). floating_network None
        # so the remainder of the function raises/returns early when it
        # hits the next collection; wrap in try to keep the test focused
        # on the pet behaviour.
        mock_network.Network.from_db.return_value = None
        mock_network.floating_network.return_value = None

        m = self._make_monitor()
        try:
            m._cluster_wide_cleanup(last_loop_run=0)
        except Exception:
            # The rest of the body is not fully stubbed; we only care
            # that the IPAM loop pet fired.
            pass

        # One pet per IPAM iterated.
        self.assertGreaterEqual(m.pet_watchdog.call_count, 2)


class ScheduledJobWatchdogTestCase(base.ShakenFistTestCase):
    """The maintenance batch pets between jobs, not just around it.

    Job timers run continuously while a node is idle but only fire while
    it is elected, so a node up for hours and then elected finds every
    job overdue at once -- including the heaviest three. Petting only
    around the batch, with WatchdogSec at 60s, makes that first elected
    pass the likeliest place to be killed for unresponsiveness.
    """

    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.pet_watchdog = mock.MagicMock()
        return m

    def _fake_job(self, should_run, ran):
        job = mock.MagicMock()
        job.should_run = should_run
        job.run.side_effect = lambda: ran.append(job)
        # sorted() over the job list needs an ordering.
        job.__lt__ = lambda self, other: False
        return job

    def test_pets_between_each_due_job(self):
        ran = []
        jobs = [self._fake_job(True, ran) for _ in range(9)]

        m = self._make_monitor()
        with mock.patch.object(cluster_main.schedule, 'jobs', jobs):
            m._run_due_scheduled_jobs()

        self.assertEqual(9, len(ran))
        # One pet before each job, plus one after the batch.
        self.assertEqual(10, m.pet_watchdog.call_count)

    def test_skips_jobs_which_are_not_due(self):
        ran = []
        due = self._fake_job(True, ran)
        not_due = self._fake_job(False, ran)

        m = self._make_monitor()
        with mock.patch.object(cluster_main.schedule, 'jobs',
                               [due, not_due]):
            m._run_due_scheduled_jobs()

        self.assertEqual([due], ran)
        not_due.run.assert_not_called()

    def test_pets_even_when_nothing_is_due(self):
        m = self._make_monitor()
        with mock.patch.object(cluster_main.schedule, 'jobs', []):
            m._run_due_scheduled_jobs()
        self.assertEqual(1, m.pet_watchdog.call_count)

    def test_cancel_job_is_honoured(self):
        # The loop mirrors Scheduler._run_job(), which cancels a job
        # whose run() returns schedule.CancelJob. No registered task
        # returns it today, but a future self-cancelling task must not
        # silently keep running forever.
        ran = []
        cancels = self._fake_job(True, ran)
        cancels.run.side_effect = None
        cancels.run.return_value = cluster_main.schedule.CancelJob
        keeps = self._fake_job(True, ran)

        m = self._make_monitor()
        with mock.patch.object(cluster_main.schedule, 'jobs',
                               [cancels, keeps]), \
                mock.patch.object(cluster_main.schedule,
                                  'cancel_job') as mock_cancel:
            m._run_due_scheduled_jobs()

        mock_cancel.assert_called_once_with(cancels)
        keeps.run.assert_called_once()

    def test_raising_job_propagates_and_skips_the_rest(self):
        # A job that raises propagates to the elected loop's
        # ignore_exception, skipping the remainder of the batch; the
        # raising job never reached _schedule_next_run(), so it stays
        # due and retries on the next 60 second cycle -- the same
        # behaviour run_pending() would give. Pinned so a refactor that
        # swallows the exception per-job is a deliberate change rather
        # than an accident.
        ran = []
        raises = self._fake_job(True, ran)
        raises.run.side_effect = RuntimeError('task exploded')
        after = self._fake_job(True, ran)

        m = self._make_monitor()
        with mock.patch.object(cluster_main.schedule, 'jobs',
                               [raises, after]):
            self.assertRaises(RuntimeError, m._run_due_scheduled_jobs)
        after.run.assert_not_called()
