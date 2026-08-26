# Copyright 2026 Michael Still and contributors
#
# Tests for anchoring long-period scheduled maintenance tasks to a
# persisted cluster-wide last-run stamp (issue 3869). The schedule
# library computes a job's next run from the moment it is registered,
# which is process start, so on a cluster which redeploys more often
# than daily the daily prune_events never came due and the events
# tables grew without bound. Each anchored task now records a last-run
# stamp in cluster_config, and a newly elected maintainer re-anchors
# its timers to those stamps.

import datetime
import time
from unittest import mock

import schedule

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.tests import base


PRUNE_KEY = 'SCHEDULED_TASK_LAST_RUN_PRUNE_EVENTS'
RECONCILE_KEY = 'SCHEDULED_TASK_LAST_RUN_RECONCILE_ORPHANED_OBJECTS'


def _make_monitor():
    m = cluster_main.Monitor.__new__(cluster_main.Monitor)
    m._anchored_jobs = []
    return m


def _register_daily_prune(m, func):
    # A private Scheduler keeps these registrations off the module
    # global job list other tests share.
    sched = schedule.Scheduler()
    m._register_anchored_job(sched.every(1).days, 'prune_events', func)
    return m._anchored_jobs[0][0]


class AnchoredJobRegistrationTestCase(base.ShakenFistTestCase):
    def test_registration_uses_the_task_name_as_the_stamp_key(self):
        m = _make_monitor()
        _register_daily_prune(m, mock.MagicMock())

        self.assertEqual(PRUNE_KEY, m._anchored_jobs[0][1])

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.set_cluster_config')
    def test_a_run_records_a_cluster_wide_stamp(self, mock_set):
        m = _make_monitor()
        task = mock.MagicMock()
        job = _register_daily_prune(m, task)

        job.run()

        task.assert_called_once()
        mock_set.assert_called_once()
        key, stamp = mock_set.call_args.args
        self.assertEqual(PRUNE_KEY, key)
        self.assertAlmostEqual(time.time(), stamp, delta=30)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.set_cluster_config')
    def test_a_raising_task_is_not_stamped(self, mock_set):
        # A raising job never reached _schedule_next_run() so it stays
        # due and retries on the next 60 second cycle; the stamp must
        # not say it ran.
        m = _make_monitor()
        task = mock.MagicMock(side_effect=RuntimeError('task exploded'))
        job = _register_daily_prune(m, task)

        self.assertRaises(RuntimeError, job.run)
        mock_set.assert_not_called()

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.set_cluster_config',
        side_effect=DatabaseUnavailable('tier is down'))
    def test_a_failed_stamp_write_does_not_fail_the_job(self, mock_set):
        # The task has already run; failing to record that must not
        # propagate, or the whole task would re-run on the next cycle.
        m = _make_monitor()
        task = mock.MagicMock()
        job = _register_daily_prune(m, task)

        job.run()

        task.assert_called_once()
        # The job rescheduled normally: next run is about a day out.
        self.assertFalse(job.should_run)


class AnchorScheduledJobsTestCase(base.ShakenFistTestCase):
    def _monitor_with_daily_prune(self):
        m = _make_monitor()
        job = _register_daily_prune(m, mock.MagicMock())
        return m, job

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config')
    def test_an_overdue_stamp_makes_the_job_due_now(self, mock_get):
        # The heart of issue 3869: at registration the daily job is a
        # day away, so a process which never lives a day never prunes.
        # A stamp 40 days old must pull the job due immediately.
        mock_get.return_value = {PRUNE_KEY: time.time() - (40 * 86400)}
        m, job = self._monitor_with_daily_prune()
        self.assertFalse(job.should_run)

        m._anchor_scheduled_jobs()

        self.assertTrue(job.should_run)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config')
    def test_a_recent_stamp_defers_the_job(self, mock_get):
        # A lock handover must not re-fire a task another node ran an
        # hour ago: the next run is one period after that run, not one
        # period after this node's registration.
        stamp = time.time() - 3600
        mock_get.return_value = {PRUNE_KEY: stamp}
        m, job = self._monitor_with_daily_prune()

        m._anchor_scheduled_jobs()

        self.assertFalse(job.should_run)
        self.assertEqual(
            datetime.datetime.fromtimestamp(stamp) +
            datetime.timedelta(days=1),
            job.next_run)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config',
        return_value={})
    def test_a_missing_stamp_makes_the_job_due_now(self, mock_get):
        # No stamp means the task has never verifiably run (the first
        # election after this code lands): run it on the first pass.
        m, job = self._monitor_with_daily_prune()

        m._anchor_scheduled_jobs()

        self.assertTrue(job.should_run)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config',
        side_effect=DatabaseUnavailable('tier is down'))
    def test_an_unreadable_database_keeps_process_local_timers(
            self, mock_get):
        # Anchoring is an improvement, not a precondition: a failed
        # stamp read falls back to the status quo ante rather than
        # killing the freshly elected maintainer.
        m, job = self._monitor_with_daily_prune()
        before = job.next_run

        m._anchor_scheduled_jobs()

        self.assertEqual(before, job.next_run)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config')
    def test_a_corrupt_stamp_is_ignored(self, mock_get):
        mock_get.return_value = {PRUNE_KEY: 'not a timestamp'}
        m, job = self._monitor_with_daily_prune()
        before = job.next_run

        m._anchor_scheduled_jobs()

        self.assertEqual(before, job.next_run)


class RunInnerAnchorWiringTestCase(base.ShakenFistTestCase):
    @mock.patch(
        'shakenfist.daemons.cluster.main.daemon.check_abort_path',
        return_value=False)
    def test_long_period_tasks_are_registered_anchored(self, mock_abort):
        # _run_inner registers on the module-global scheduler, so clear
        # it afterwards for whatever test shares this process next.
        self.addCleanup(schedule.clear)
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = None
        m.is_elected = False
        m.abort_path = '/nonexistent/sf-test-cluster-abort-path'

        m._run_inner()

        anchored = {key: job for job, key in m._anchored_jobs}
        self.assertEqual({PRUNE_KEY, RECONCILE_KEY}, set(anchored))
        # schedule 1.2.2 Jobs carry unit and interval, not a period.
        self.assertEqual(
            ('days', 1),
            (anchored[PRUNE_KEY].unit, anchored[PRUNE_KEY].interval))
        self.assertEqual(
            ('minutes', 60),
            (anchored[RECONCILE_KEY].unit, anchored[RECONCILE_KEY].interval))
        for job, _ in m._anchored_jobs:
            self.assertIn(job, schedule.jobs)
