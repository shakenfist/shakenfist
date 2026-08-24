import os
import shutil
import tempfile
import time
from unittest import mock

import psutil

from shakenfist.daemons.resources import main as resources_main
from shakenfist.node_health import NodeHealthResult
from shakenfist.tests import base


class ParseCpuListTestCase(base.ShakenFistTestCase):
    def test_simple_range(self):
        self.assertEqual(list(range(0, 16)),
                         resources_main._parse_cpu_list('0-15'))

    def test_range_with_trailing_newline(self):
        self.assertEqual([16, 17, 18, 19, 20, 21, 22, 23],
                         resources_main._parse_cpu_list('16-23\n'))

    def test_comma_separated_ranges(self):
        self.assertEqual([0, 1, 2, 3, 8, 9, 10, 11],
                         resources_main._parse_cpu_list('0-3,8-11'))

    def test_single_cpu(self):
        self.assertEqual([5], resources_main._parse_cpu_list('5'))


class HybridCoreCountsTestCase(base.ShakenFistTestCase):
    def _make_sysfs(self, core_cpus=None, atom_cpus=None, core_ids=None):
        # Build a fake sysfs tree. core_ids maps thread number to the
        # topology core_id reported for that thread.
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        if core_cpus is not None:
            os.makedirs(os.path.join(root, 'devices/cpu_core'))
            with open(os.path.join(root, 'devices/cpu_core/cpus'), 'w') as f:
                f.write(core_cpus)
        if atom_cpus is not None:
            os.makedirs(os.path.join(root, 'devices/cpu_atom'))
            with open(os.path.join(root, 'devices/cpu_atom/cpus'), 'w') as f:
                f.write(atom_cpus)

        for thread, core_id in (core_ids or {}).items():
            d = os.path.join(root, f'devices/system/cpu/cpu{thread}/topology')
            os.makedirs(d)
            with open(os.path.join(d, 'core_id'), 'w') as f:
                f.write(f'{core_id}\n')

        return root

    def test_i9_12900_shape(self):
        # 8 hyperthreaded performance cores (threads 0-15) plus 8
        # single-thread efficiency cores (threads 16-23), as observed on
        # real hardware.
        core_ids = {}
        for t in range(16):
            core_ids[t] = t // 2
        for t in range(16, 24):
            core_ids[t] = t - 8

        root = self._make_sysfs(core_cpus='0-15\n', atom_cpus='16-23\n',
                                core_ids=core_ids)
        self.assertEqual(
            {'cpu_cores_performance': 8, 'cpu_cores_efficiency': 8},
            resources_main._get_hybrid_core_counts(sysfs_root=root))

    def test_absent_on_non_hybrid_hardware(self):
        root = self._make_sysfs()
        self.assertEqual(
            {}, resources_main._get_hybrid_core_counts(sysfs_root=root))

    def test_only_one_path_present(self):
        root = self._make_sysfs(core_cpus='0-15\n')
        self.assertEqual(
            {}, resources_main._get_hybrid_core_counts(sysfs_root=root))

    def test_parse_failure_is_omitted(self):
        root = self._make_sysfs(core_cpus='banana\n', atom_cpus='16-23\n')
        self.assertEqual(
            {}, resources_main._get_hybrid_core_counts(sysfs_root=root))


class ComputeReservationsTestCase(base.ShakenFistTestCase):
    def test_hyperthreaded_plain_hypervisor(self):
        # Ryzen 5 3600 shape: 6 cores, 12 threads. The default 2-thread
        # reservation is subtracted straight off the thread count; the
        # informational cpu_cores_reserved derives back to ceil(2 / 2) = 1
        # physical core.
        self.assertEqual(
            {
                'cpu_cores_reserved': 1,
                'cpu_schedulable': 10,
                'cpu_cores_schedulable': 5,
                'memory_reserved_mb': 2048,
            },
            resources_main._compute_reservations(6, 12, 2, 2.0, 65536))

    def test_hyperthreaded_larger_reservation(self):
        # The same hardware with a larger per-node reservation (for
        # example the value Ansible templates onto an infra-role host):
        # 4 threads reserved, 6 GB RAM.
        self.assertEqual(
            {
                'cpu_cores_reserved': 2,
                'cpu_schedulable': 8,
                'cpu_cores_schedulable': 4,
                'memory_reserved_mb': 6144,
            },
            resources_main._compute_reservations(6, 12, 4, 6.0, 65536))

    def test_non_hyperthreaded(self):
        # 8 cores, 8 threads: threads_per_core is 1, so reserving 2 threads
        # derives to 2 reserved physical cores.
        self.assertEqual(
            {
                'cpu_cores_reserved': 2,
                'cpu_schedulable': 6,
                'cpu_cores_schedulable': 6,
                'memory_reserved_mb': 6144,
            },
            resources_main._compute_reservations(8, 8, 2, 6.0, 65536))

    def test_hybrid_topology(self):
        # i9-12900 shape: 16 cores, 24 threads. ceil(24 / 16) = 2 threads
        # per core, so reserving 4 threads derives to 2 physical cores,
        # which errs conservative on hybrid parts.
        self.assertEqual(
            {
                'cpu_cores_reserved': 2,
                'cpu_schedulable': 20,
                'cpu_cores_schedulable': 14,
                'memory_reserved_mb': 6144,
            },
            resources_main._compute_reservations(16, 24, 4, 6.0, 65536))

    def test_schedulable_floors_at_one(self):
        # A tiny 2-core non-hyperthreaded node reserving more threads than
        # it has cannot reserve itself to zero.
        self.assertEqual(
            {
                'cpu_cores_reserved': 4,
                'cpu_schedulable': 1,
                'cpu_cores_schedulable': 1,
                'memory_reserved_mb': 6144,
            },
            resources_main._compute_reservations(2, 2, 4, 6.0, 65536))

    def test_memory_reservation_capped_on_small_nodes(self):
        # A small node carrying every role (the single-node deployment
        # case), or one given an oversized override, must not reserve
        # itself out of scheduling: the memory reservation is capped at
        # half the machine. Here the uncapped reservation would be 6144 MB
        # of a 6144 MB node.
        self.assertEqual(
            {
                'cpu_cores_reserved': 2,
                'cpu_schedulable': 1,
                'cpu_cores_schedulable': 1,
                'memory_reserved_mb': 3072,
            },
            resources_main._compute_reservations(2, 4, 4, 6.0, 6144))

    def test_configurable_reservations(self):
        # The knobs are honoured, not hardcoded.
        self.assertEqual(
            {
                'cpu_cores_reserved': 4,
                'cpu_schedulable': 16,
                'cpu_cores_schedulable': 12,
                'memory_reserved_mb': 9216,
            },
            resources_main._compute_reservations(16, 24, 8, 9.0, 65536))


class GetStatsRacesNodeDeletionTestCase(base.ShakenFistTestCase):
    """The periodic stats sweep can race deletion of this node from the
    cluster (issue 3591). That is expected: the lookup must not emit the
    "non-existent object" audit event, and the sweep must be skipped rather
    than recreating rows for the deleted node."""

    def test_sweep_skipped_and_audit_suppressed(self):
        m = resources_main.Monitor.__new__(resources_main.Monitor)
        m.last_logged_resources = 0
        with mock.patch.object(
                resources_main.Node, 'from_db',
                return_value=None) as mock_from_db, \
                mock.patch.object(
                    resources_main.mariadb,
                    'get_node_metrics') as mock_get_metrics:
            self.assertIsNone(m._get_stats())
        mock_from_db.assert_called_once_with(
            resources_main.config.NODE_NAME, suppress_failure_audit=True)
        mock_get_metrics.assert_not_called()


class HealthGaugeTestCase(base.ShakenFistTestCase):
    """The node-health thread exposes node_resource_health, tracking the
    evaluate() result each cycle."""

    def _run_one_cycle(self, result):
        m = resources_main.Monitor.__new__(resources_main.Monitor)
        m.abort_path = '/nonexistent-abort-path'
        gauge = mock.MagicMock()
        with mock.patch.object(
                resources_main, 'Gauge', return_value=gauge), \
                mock.patch.object(
                    resources_main.node_health, 'evaluate',
                    return_value=result), \
                mock.patch.object(
                    resources_main.Node, 'from_db', return_value=None), \
                mock.patch.object(
                    resources_main.daemon, 'check_abort_path',
                    side_effect=[True, False, False]):
            # One loop iteration, then the inner and outer abort checks return
            # False so the thread body exits.
            m._run_health_checks(checks=[], types_by_identity={})
        return gauge

    def test_gauge_one_when_healthy(self):
        gauge = self._run_one_cycle(
            NodeHealthResult(healthy=True, failed=[], affected_types=set(),
                             reason='all resource health checks passed'))
        gauge.set.assert_called_with(1.0)

    def test_gauge_zero_when_unhealthy(self):
        gauge = self._run_one_cycle(
            NodeHealthResult(healthy=False, failed=[], affected_types=set(),
                             reason='resource health check failed'))
        gauge.set.assert_called_with(0.0)

    def test_apply_result_called_when_node_present(self):
        # The wiring that actually errors a node in production: Node.from_db
        # returns this node, so apply_result must be invoked with it.
        m = resources_main.Monitor.__new__(resources_main.Monitor)
        m.abort_path = '/nonexistent-abort-path'
        result = NodeHealthResult(
            healthy=False, failed=[], affected_types=set(),
            reason='resource health check failed')
        fake_node = mock.MagicMock()
        with mock.patch.object(resources_main, 'Gauge'), \
                mock.patch.object(resources_main.node_health, 'evaluate',
                                  return_value=result), \
                mock.patch.object(resources_main.Node, 'from_db',
                                  return_value=fake_node), \
                mock.patch.object(resources_main.node_health,
                                  'apply_result') as mock_apply, \
                mock.patch.object(resources_main.daemon, 'check_abort_path',
                                  side_effect=[True, False, False]):
            m._run_health_checks(checks=[], types_by_identity={})
        mock_apply.assert_called_once_with(fake_node, result)

    def test_node_lookup_suppresses_failure_audit(self):
        # The health thread's node lookup races node deletion just like
        # _get_stats does (issue 3591), so it must not emit the
        # "non-existent object" audit event either.
        m = resources_main.Monitor.__new__(resources_main.Monitor)
        m.abort_path = '/nonexistent-abort-path'
        result = NodeHealthResult(
            healthy=True, failed=[], affected_types=set(),
            reason='all resource health checks passed')
        with mock.patch.object(resources_main, 'Gauge'), \
                mock.patch.object(resources_main.node_health, 'evaluate',
                                  return_value=result), \
                mock.patch.object(resources_main.Node, 'from_db',
                                  return_value=None) as mock_from_db, \
                mock.patch.object(resources_main.daemon, 'check_abort_path',
                                  side_effect=[True, False, False]):
            m._run_health_checks(checks=[], types_by_identity={})
        mock_from_db.assert_called_once_with(
            resources_main.config.NODE_NAME, suppress_failure_audit=True)

    def test_evaluate_exception_is_swallowed(self):
        # A raise inside the cycle (for example a probe failure) must be
        # swallowed via ignore_exception, not propagated out of the thread.
        m = resources_main.Monitor.__new__(resources_main.Monitor)
        m.abort_path = '/nonexistent-abort-path'
        with mock.patch.object(resources_main, 'Gauge'), \
                mock.patch.object(resources_main.node_health, 'evaluate',
                                  side_effect=RuntimeError('boom')), \
                mock.patch.object(resources_main.Node, 'from_db',
                                  return_value=None), \
                mock.patch.object(resources_main.util_exceptions,
                                  'ignore_exception') as mock_ignore, \
                mock.patch.object(resources_main.daemon, 'check_abort_path',
                                  side_effect=[True, False, False]):
            m._run_health_checks(checks=[], types_by_identity={})
        self.assertEqual(1, mock_ignore.call_count)


class SfDaemonPidsTestCase(base.ShakenFistTestCase):
    """Process metrics must be scoped to Shaken Fist's own systemd units.

    Since each daemon became its own systemd unit, walking our parent's
    children walked every service on the node -- including the guest VMs
    under libvirtd (issue 3860). The pid enumeration instead reads the
    sf-*.service cgroups."""

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir)

    def _write_cgroup_procs(self, hierarchy, unit, pids):
        unit_dir = os.path.join(self.tempdir, hierarchy, unit)
        os.makedirs(unit_dir)
        with open(os.path.join(unit_dir, 'cgroup.procs'), 'w') as f:
            for pid in pids:
                f.write('%s\n' % pid)

    def _patched_globs(self):
        return [
            os.path.join(self.tempdir, 'v2/sf-*.service/cgroup.procs'),
            os.path.join(self.tempdir, 'v1/sf-*.service/cgroup.procs'),
        ]

    def test_pids_collected_from_sf_units_only(self):
        self._write_cgroup_procs('v2', 'sf-database.service', [101, 102])
        self._write_cgroup_procs('v2', 'sf-resources.service', [204])
        self._write_cgroup_procs('v2', 'libvirtd.service', [666])
        with mock.patch.object(resources_main, 'SF_UNIT_CGROUP_GLOBS',
                               self._patched_globs()):
            self.assertEqual([101, 102, 204], resources_main._sf_daemon_pids())

    def test_pids_deduplicated_across_hierarchies(self):
        self._write_cgroup_procs('v2', 'sf-database.service', [101])
        self._write_cgroup_procs('v1', 'sf-database.service', [101])
        with mock.patch.object(resources_main, 'SF_UNIT_CGROUP_GLOBS',
                               self._patched_globs()):
            self.assertEqual([101], resources_main._sf_daemon_pids())

    def test_blank_lines_and_missing_hierarchy_ignored(self):
        unit_dir = os.path.join(self.tempdir, 'v2/sf-api.service')
        os.makedirs(unit_dir)
        with open(os.path.join(unit_dir, 'cgroup.procs'), 'w') as f:
            f.write('42\n\n')
        with mock.patch.object(resources_main, 'SF_UNIT_CGROUP_GLOBS',
                               self._patched_globs()):
            self.assertEqual([42], resources_main._sf_daemon_pids())

    def test_no_units_present(self):
        with mock.patch.object(resources_main, 'SF_UNIT_CGROUP_GLOBS',
                               self._patched_globs()):
            self.assertEqual([], resources_main._sf_daemon_pids())


class CollectProcessMetricsTestCase(base.ShakenFistTestCase):
    def _fake_process(self, name, age, cpu_seconds):
        p = mock.MagicMock()
        p.name.return_value = name
        p.create_time.return_value = time.time() - age
        p.cpu_times.return_value = mock.Mock(user=cpu_seconds, system=0.0)
        return p

    def test_metrics_scoped_to_enumerated_pids(self):
        procs = {
            100: self._fake_process('sf-database', 1000, 10.0),
            200: self._fake_process('sf-cleaner', 30, 1.0),
            300: self._fake_process('sf-queues', 1000, 10.0),
        }

        def _process(pid):
            if pid == 400:
                raise psutil.NoSuchProcess(pid)
            return procs[pid]

        n = mock.MagicMock()
        with mock.patch.object(resources_main, '_sf_daemon_pids',
                               return_value=[100, 200, 300, 400]), \
                mock.patch.object(resources_main.psutil, 'Process',
                                  side_effect=_process):
            metrics = resources_main._collect_process_metrics(n)

        # The long-running daemon is measured, the young process and the
        # queue workers are not, and a pid which exited between enumeration
        # and measurement is skipped.
        self.assertIn('process_cpu_time_sf_database', metrics)
        self.assertIn('process_age_sf_database', metrics)
        self.assertIn('process_cpu_fraction_sf_database', metrics)
        self.assertEqual(3, len(metrics))
        self.assertAlmostEqual(
            0.01, metrics['process_cpu_fraction_sf_database'], places=3)
        n.add_event.assert_not_called()

    def test_cpu_hog_emits_event(self):
        hog = self._fake_process('sf-net', 100, 50.0)
        n = mock.MagicMock()
        with mock.patch.object(resources_main, '_sf_daemon_pids',
                               return_value=[100]), \
                mock.patch.object(resources_main.psutil, 'Process',
                                  return_value=hog):
            metrics = resources_main._collect_process_metrics(n)

        self.assertGreater(metrics['process_cpu_fraction_sf_net'], 0.25)
        self.assertEqual(1, n.add_event.call_count)
        self.assertIn('sf_net is a CPU hog', n.add_event.call_args[0][1])
