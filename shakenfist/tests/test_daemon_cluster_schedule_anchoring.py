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
import uuid
from unittest import mock

import schedule

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.tests import base


PRUNE_KEY = 'SCHEDULED_TASK_LAST_RUN_PRUNE_EVENTS'
RECONCILE_KEY = 'SCHEDULED_TASK_LAST_RUN_RECONCILE_ORPHANED_OBJECTS'
CAPACITY_KEY = 'SCHEDULED_TASK_LAST_RUN_RECONCILE_SCHEDULER_CAPACITY'


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
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config_value')
    def test_an_overdue_stamp_makes_the_job_due_now(self, mock_get):
        # The heart of issue 3869: at registration the daily job is a
        # day away, so a process which never lives a day never prunes.
        # A stamp 40 days old must pull the job due immediately.
        mock_get.side_effect = {PRUNE_KEY: time.time() - (40 * 86400)}.get
        m, job = self._monitor_with_daily_prune()
        self.assertFalse(job.should_run)

        m._anchor_scheduled_jobs()

        self.assertTrue(job.should_run)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config_value')
    def test_a_recent_stamp_defers_the_job(self, mock_get):
        # A lock handover must not re-fire a task another node ran an
        # hour ago: the next run is one period after that run, not one
        # period after this node's registration.
        stamp = time.time() - 3600
        mock_get.side_effect = {PRUNE_KEY: stamp}.get
        m, job = self._monitor_with_daily_prune()

        m._anchor_scheduled_jobs()

        self.assertFalse(job.should_run)
        self.assertEqual(
            datetime.datetime.fromtimestamp(stamp) +
            datetime.timedelta(days=1),
            job.next_run)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config_value',
        return_value=None)
    def test_a_missing_stamp_makes_the_job_due_now(self, mock_get):
        # No stamp means the task has never verifiably run (the first
        # election after this code lands): run it on the first pass.
        m, job = self._monitor_with_daily_prune()

        m._anchor_scheduled_jobs()

        self.assertTrue(job.should_run)
        # Issue 4096: each stamp is fetched by key, not by pulling the
        # whole cluster_config table (which holds cluster secrets).
        mock_get.assert_called_once_with(PRUNE_KEY)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config_value',
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
        'shakenfist.daemons.cluster.main.mariadb.get_cluster_config_value')
    def test_a_corrupt_stamp_is_ignored(self, mock_get):
        mock_get.side_effect = {PRUNE_KEY: 'not a timestamp'}.get
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
        self.assertEqual(
            {PRUNE_KEY, RECONCILE_KEY, CAPACITY_KEY}, set(anchored))
        # schedule 1.2.2 Jobs carry unit and interval, not a period.
        self.assertEqual(
            ('days', 1),
            (anchored[PRUNE_KEY].unit, anchored[PRUNE_KEY].interval))
        self.assertEqual(
            ('minutes', 60),
            (anchored[RECONCILE_KEY].unit, anchored[RECONCILE_KEY].interval))
        self.assertEqual(
            ('minutes', 5),
            (anchored[CAPACITY_KEY].unit, anchored[CAPACITY_KEY].interval))
        for job, _ in m._anchored_jobs:
            self.assertIn(job, schedule.jobs)

    @mock.patch(
        'shakenfist.daemons.cluster.main.daemon.check_abort_path',
        return_value=False)
    def test_the_capacity_reconcile_is_not_also_registered_unanchored(
            self, mock_abort):
        # It used to be a plain schedule.every(5).minutes registration,
        # and leaving that behind as well would run the pass twice per
        # cadence while only one of the two recorded a stamp.
        self.addCleanup(schedule.clear)
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = None
        m.is_elected = False
        m.abort_path = '/nonexistent/sf-test-cluster-abort-path'

        m._run_inner()

        anchored = {job for job, _ in m._anchored_jobs}
        unanchored_five_minute = [
            j for j in schedule.jobs
            if j not in anchored and (j.unit, j.interval) == ('minutes', 5)]
        self.assertEqual(
            2, len(unanchored_five_minute),
            'expected only per_blob_checks and '
            'per_instance_checks_and_usage to remain unanchored at five '
            'minutes')

    @mock.patch(
        'shakenfist.daemons.cluster.main.daemon.check_abort_path',
        return_value=False)
    def test_run_inner_keeps_a_handle_on_the_capacity_job(self, mock_abort):
        self.addCleanup(schedule.clear)
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = None
        m.is_elected = False
        m.abort_path = '/nonexistent/sf-test-cluster-abort-path'

        m._run_inner()

        anchored = {key: job for job, key in m._anchored_jobs}
        self.assertIs(anchored[CAPACITY_KEY], m._capacity_reconcile_job)


class ForcedCapacityReconcileTestCase(base.ShakenFistTestCase):
    """The maintenance-pass check that placement is being guarded at all.

    A node with no scheduler_node_capacity row has its placements
    admitted against nothing at all (P7), which is issue 4087.
    Anchoring handles the fresh-cluster case by way of a missing stamp;
    this handles every other way a node can end up unguarded while a
    stamp says a pass ran recently -- an empty table, and a populated
    table which has no row for a hypervisor that has only just started
    publishing metrics.

    The predicate matters more than the plumbing: it has to agree with
    the one _direct_reconcile_scheduler_capacity() applies, because a
    check which qualifies a node the reconciler will never size forces
    the five minute job every sixty seconds for the life of the
    cluster.
    """

    def _monitor_with_capacity_job(self):
        m = _make_monitor()
        sched = schedule.Scheduler()
        m._capacity_reconcile_job = m._register_anchored_job(
            sched.every(5).minutes, 'reconcile_scheduler_capacity',
            mock.MagicMock())
        # Park it well out of reach so "became due" is unambiguous.
        m._capacity_reconcile_job.next_run = (
            datetime.datetime.now() + datetime.timedelta(days=1))
        return m

    def _park(self, m):
        """Push the job back out of reach and report where it was put."""
        m._capacity_reconcile_job.next_run = (
            datetime.datetime.now() + datetime.timedelta(days=1))
        return m._capacity_reconcile_job.next_run

    def _became_due(self, m):
        return m._capacity_reconcile_job.next_run <= datetime.datetime.now()

    def _metrics(self, node_uuid, metrics, age=0):
        return {
            'node_uuid': node_uuid,
            'fqdn': f'{node_uuid}.example.com',
            'timestamp': time.time() - age,
            'metrics': metrics
        }

    def _row(self, node_uuid):
        return {'node_uuid': node_uuid}

    def _patch(self, rows, metrics, active):
        """Patch the three reads the check makes, for the whole test."""
        capacity = mock.patch(
            'shakenfist.daemons.cluster.main.mariadb.'
            'get_scheduler_node_capacity',
            return_value=(rows, False))
        node_metrics = mock.patch(
            'shakenfist.daemons.cluster.main.mariadb.get_all_node_metrics',
            return_value=metrics)
        nodes = mock.patch(
            'shakenfist.daemons.cluster.main.Nodes',
            return_value=[mock.MagicMock(uuid=u) for u in active])
        for patcher in (capacity, node_metrics, nodes):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_an_empty_table_forces_the_pass_due(self):
        # All three reads are mocked, including the two an empty table
        # does not obviously need. Without them the metrics read reaches
        # mariadb.get_all_node_metrics(), whose behaviour in a unit test
        # depends on the host: it raises immediately where neither
        # MARIADB_HOST nor a gateway is configured, but builds a channel
        # and retries through GRPC_UNAVAILABLE_RETRIES where one is. The
        # empty-table branch would then be reached by way of the
        # unknown-answer branch rather than on its own terms.
        self._patch(rows=[], metrics=[self._metrics('a-node', {})],
                    active=['a-node'])
        m = self._monitor_with_capacity_job()

        m._force_capacity_reconcile_if_unguarded()

        self.assertLessEqual(
            m._capacity_reconcile_job.next_run, datetime.datetime.now())

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.'
        'get_scheduler_node_capacity')
    def test_a_populated_table_leaves_the_cadence_alone(self, mock_get):
        mock_get.return_value = ([{'node_uuid': 'a-node'}], False)
        # The other two reads are mocked so this exercises the path it
        # names. Without them the metrics read raises immediately (no
        # MARIADB_HOST in a unit test), the check returns on the
        # unknown-answer branch, and the assertion below passes without
        # ever reaching the question of whether a populated table
        # leaves the cadence alone.
        metrics = mock.patch(
            'shakenfist.daemons.cluster.main.mariadb.get_all_node_metrics',
            return_value=[{
                'node_uuid': 'a-node',
                'fqdn': 'a-node.example.com',
                'timestamp': time.time(),
                'metrics': {'is_hypervisor': True}
            }])
        nodes = mock.patch(
            'shakenfist.daemons.cluster.main.Nodes',
            return_value=[mock.MagicMock(uuid='a-node')])
        for patcher in (metrics, nodes):
            patcher.start()
            self.addCleanup(patcher.stop)

        m = self._monitor_with_capacity_job()
        before = m._capacity_reconcile_job.next_run

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.'
        'get_scheduler_node_capacity')
    def test_a_degraded_read_is_not_an_empty_table(self, mock_get):
        # rows is empty here too, so this fails if the check reads the
        # rows without also reading what degraded says about them.
        mock_get.return_value = ([], True)
        m = self._monitor_with_capacity_job()
        before = m._capacity_reconcile_job.next_run

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.'
        'get_scheduler_node_capacity',
        side_effect=DatabaseUnavailable('no database'))
    def test_a_failed_read_does_not_escape_the_election(self, mock_get):
        m = self._monitor_with_capacity_job()
        before = m._capacity_reconcile_job.next_run

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.'
        'get_scheduler_node_capacity')
    def test_no_registered_job_reads_nothing(self, mock_get):
        m = _make_monitor()

        m._force_capacity_reconcile_if_unguarded()

        mock_get.assert_not_called()

    def test_a_fresh_hypervisor_with_no_row_forces_the_pass(self):
        # Realistic dashed uuids rather than the short opaque names the
        # rest of this class uses, and one source handing back a UUID
        # object rather than a string, because the set arithmetic joins
        # three independently sourced spellings of a node uuid and
        # CLAUDE.md pitfall 6 records that a mismatch between them fails
        # silently. If the str() normalisation were dropped, the guarded
        # node would never subtract from the capacity rows, and the
        # unguarded one would never intersect with the active set: the
        # first spelling mismatch forces the five minute pass every
        # sixty seconds forever, the second silently forces nothing.
        guarded = '2f9a1c74-0b3d-4a1e-9c8f-7d6e5b4a3c21'
        unguarded = 'a1b2c3d4-e5f6-4708-9a0b-1c2d3e4f5061'
        self._patch(
            rows=[{'node_uuid': uuid.UUID(guarded)}],
            metrics=[self._metrics(guarded, {'is_hypervisor': True}),
                     self._metrics(unguarded, {'is_hypervisor': True})],
            active=[uuid.UUID(guarded), uuid.UUID(unguarded)])
        m = self._monitor_with_capacity_job()

        m._force_capacity_reconcile_if_unguarded()

        self.assertTrue(self._became_due(m))
        self.assertEqual(
            frozenset([unguarded]), m._forced_capacity_reconcile_for)

    def test_the_same_unguarded_set_does_not_force_twice(self):
        self._patch(
            rows=[self._row('guarded')],
            metrics=[self._metrics('unguarded', {'is_hypervisor': True})],
            active=['guarded', 'unguarded'])
        m = self._monitor_with_capacity_job()

        m._force_capacity_reconcile_if_unguarded()
        self.assertTrue(self._became_due(m))

        # Nothing about the cluster has changed, so a second pass must
        # leave the cadence alone rather than re-forcing every minute.
        before = self._park(m)
        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)

    def test_a_changed_unguarded_set_forces_again(self):
        capacity = mock.patch(
            'shakenfist.daemons.cluster.main.mariadb.'
            'get_scheduler_node_capacity',
            return_value=([self._row('guarded')], False))
        capacity.start()
        self.addCleanup(capacity.stop)
        node_metrics = mock.patch(
            'shakenfist.daemons.cluster.main.mariadb.get_all_node_metrics')
        mock_metrics = node_metrics.start()
        self.addCleanup(node_metrics.stop)
        nodes = mock.patch(
            'shakenfist.daemons.cluster.main.Nodes',
            return_value=[mock.MagicMock(uuid=u)
                          for u in ('guarded', 'one', 'two')])
        nodes.start()
        self.addCleanup(nodes.stop)

        m = self._monitor_with_capacity_job()

        mock_metrics.return_value = [
            self._metrics('one', {'is_hypervisor': True})]
        m._force_capacity_reconcile_if_unguarded()
        self.assertTrue(self._became_due(m))

        self._park(m)
        mock_metrics.return_value = [
            self._metrics('one', {'is_hypervisor': True}),
            self._metrics('two', {'is_hypervisor': True})]
        m._force_capacity_reconcile_if_unguarded()

        self.assertTrue(self._became_due(m))

    def test_stale_metrics_do_not_force(self):
        self._patch(
            rows=[self._row('guarded')],
            metrics=[self._metrics(
                'ancient', {'is_hypervisor': True},
                age=cluster_main.mariadb.RECONCILE_METRICS_MAX_AGE_SECONDS
                + 60)],
            active=['guarded', 'ancient'])
        m = self._monitor_with_capacity_job()
        before = self._park(m)

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)

    def test_a_non_hypervisor_does_not_force(self):
        self._patch(
            rows=[self._row('guarded')],
            metrics=[self._metrics('storage', {'is_hypervisor': False})],
            active=['guarded', 'storage'])
        m = self._monitor_with_capacity_job()
        before = self._park(m)

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)

    def test_an_unknown_is_hypervisor_does_not_force(self):
        # Mid-upgrade, before the resources daemon repopulates the
        # column, is_hypervisor is absent or NULL. The reconciler counts
        # such a node as neither a hypervisor nor a non-hypervisor, so
        # neither does this.
        self._patch(
            rows=[self._row('guarded')],
            metrics=[self._metrics('absent', {}),
                     self._metrics('null', {'is_hypervisor': None})],
            active=['guarded', 'absent', 'null'])
        m = self._monitor_with_capacity_job()
        before = self._park(m)

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)

    def test_a_phantom_metrics_row_does_not_force(self):
        # The load regression this check exists to avoid. A node_metrics
        # row which outlived its node reads as a fresh hypervisor
        # forever, so a check without the active-node intersection would
        # force the five minute reconcile every sixty seconds for the
        # life of the cluster -- and the pass would never create a row
        # for it, so the condition would never clear.
        self._patch(
            rows=[self._row('guarded')],
            metrics=[self._metrics('phantom', {'is_hypervisor': True})],
            active=['guarded'])
        m = self._monitor_with_capacity_job()
        before = self._park(m)

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)

    @mock.patch('shakenfist.daemons.cluster.main.Nodes')
    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_all_node_metrics')
    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.'
        'get_scheduler_node_capacity')
    def test_a_degraded_read_forces_nothing_even_with_a_candidate(
            self, mock_get, mock_metrics, mock_nodes):
        # rows is empty and a hypervisor would qualify, but a degraded
        # read means this process knows nothing about the counters. It
        # must not read metrics on the strength of a guess either.
        mock_get.return_value = ([], True)
        mock_metrics.return_value = [
            self._metrics('unguarded', {'is_hypervisor': True})]
        mock_nodes.return_value = [mock.MagicMock(uuid='unguarded')]
        m = self._monitor_with_capacity_job()
        before = self._park(m)

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)
        mock_metrics.assert_not_called()

    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.get_all_node_metrics',
        side_effect=DatabaseUnavailable('no database'))
    @mock.patch(
        'shakenfist.daemons.cluster.main.mariadb.'
        'get_scheduler_node_capacity')
    def test_a_failed_metrics_read_leaves_the_cadence_alone(
            self, mock_get, mock_metrics):
        mock_get.return_value = ([self._row('guarded')], False)
        m = self._monitor_with_capacity_job()
        before = self._park(m)

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)

    def test_a_swallowed_metrics_read_leaves_the_cadence_alone(self):
        # Neither transport raises on a failed metrics read: the gRPC
        # path logs the RpcError and returns [], and the direct path
        # does the same for an OperationalError. Read as an empty set
        # that would clear the memory below and re-force the five minute
        # pass every time the read flapped, so it has to read as unknown
        # instead. A running cluster does not produce an empty list --
        # sf-resources publishes from every node whatever its roles.
        self._patch(rows=[self._row('guarded')], metrics=[], active=['hyp'])
        m = self._monitor_with_capacity_job()
        m._forced_capacity_reconcile_for = frozenset(['unguarded'])
        before = self._park(m)

        m._force_capacity_reconcile_if_unguarded()

        self.assertEqual(before, m._capacity_reconcile_job.next_run)
        self.assertEqual(
            frozenset(['unguarded']), m._forced_capacity_reconcile_for)

    def test_a_cold_cluster_forces_again_once_metrics_appear(self):
        # The sequence issue 4087 is actually about. The first pass on a
        # cold cluster finds an empty table and no hypervisor metrics at
        # all, so the pass it forces has nothing to size and the table
        # stays empty. When the first hypervisor finally publishes, that
        # is a different condition and must force another pass -- if the
        # empty table alone were the condition, the cluster would wait
        # out the five minute cadence, which is the defect.
        capacity = mock.patch(
            'shakenfist.daemons.cluster.main.mariadb.'
            'get_scheduler_node_capacity',
            return_value=([], False))
        capacity.start()
        self.addCleanup(capacity.stop)
        node_metrics = mock.patch(
            'shakenfist.daemons.cluster.main.mariadb.get_all_node_metrics',
            return_value=[])
        mock_metrics = node_metrics.start()
        self.addCleanup(node_metrics.stop)
        nodes = mock.patch(
            'shakenfist.daemons.cluster.main.Nodes',
            return_value=[mock.MagicMock(uuid='hyp')])
        nodes.start()
        self.addCleanup(nodes.stop)

        m = self._monitor_with_capacity_job()

        m._force_capacity_reconcile_if_unguarded()
        self.assertTrue(self._became_due(m))

        # Still nothing to size, so no second pass.
        before = self._park(m)
        m._force_capacity_reconcile_if_unguarded()
        self.assertEqual(before, m._capacity_reconcile_job.next_run)

        # And now there is.
        self._park(m)
        mock_metrics.return_value = [
            self._metrics('hyp', {'is_hypervisor': True})]
        m._force_capacity_reconcile_if_unguarded()

        self.assertTrue(self._became_due(m))

    def test_a_fully_guarded_cluster_forgets_what_it_forced(self):
        # Once the condition clears the memory must clear too, or the
        # next occurrence of the same set would never be forced.
        self._patch(
            rows=[self._row('guarded')],
            metrics=[self._metrics('guarded', {'is_hypervisor': True})],
            active=['guarded'])
        m = self._monitor_with_capacity_job()
        m._forced_capacity_reconcile_for = frozenset(['guarded'])

        m._force_capacity_reconcile_if_unguarded()

        self.assertIsNone(m._forced_capacity_reconcile_for)
