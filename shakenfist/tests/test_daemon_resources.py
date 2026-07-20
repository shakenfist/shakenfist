import os
import shutil
import tempfile
from unittest import mock

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
        # Ryzen 5 3600 shape: 6 cores, 12 threads, no infra role. One core
        # reserved for the OS costs two threads.
        self.assertEqual(
            {
                'cpu_cores_reserved': 1,
                'cpu_schedulable': 10,
                'cpu_cores_schedulable': 5,
                'memory_reserved_mb': 2048,
            },
            resources_main._compute_reservations(6, 12, False, 1, 1, 2.0, 4.0, 65536))

    def test_hyperthreaded_infra_role(self):
        # The same hardware carrying network or database duties reserves a
        # second core and extra RAM.
        self.assertEqual(
            {
                'cpu_cores_reserved': 2,
                'cpu_schedulable': 8,
                'cpu_cores_schedulable': 4,
                'memory_reserved_mb': 6144,
            },
            resources_main._compute_reservations(6, 12, True, 1, 1, 2.0, 4.0, 65536))

    def test_non_hyperthreaded_infra_role(self):
        # 8 cores, 8 threads: reserved cores convert 1:1 to threads.
        self.assertEqual(
            {
                'cpu_cores_reserved': 2,
                'cpu_schedulable': 6,
                'cpu_cores_schedulable': 6,
                'memory_reserved_mb': 6144,
            },
            resources_main._compute_reservations(8, 8, True, 1, 1, 2.0, 4.0, 65536))

    def test_hybrid_infra_role(self):
        # i9-12900 shape: 16 cores, 24 threads. ceil(24 / 16) = 2 threads
        # per reserved core, which errs conservative on hybrid parts.
        self.assertEqual(
            {
                'cpu_cores_reserved': 2,
                'cpu_schedulable': 20,
                'cpu_cores_schedulable': 14,
                'memory_reserved_mb': 6144,
            },
            resources_main._compute_reservations(16, 24, True, 1, 1, 2.0, 4.0, 65536))

    def test_schedulable_floors_at_one(self):
        # A tiny 2-core non-hyperthreaded infra node cannot reserve itself
        # to zero.
        self.assertEqual(
            {
                'cpu_cores_reserved': 2,
                'cpu_schedulable': 1,
                'cpu_cores_schedulable': 1,
                'memory_reserved_mb': 6144,
            },
            resources_main._compute_reservations(2, 2, True, 1, 1, 2.0, 4.0, 65536))

    def test_memory_reservation_capped_on_small_nodes(self):
        # A small node carrying every role (the single-node deployment
        # case) must not reserve itself out of scheduling: the memory
        # reservation is capped at half the machine. Here the uncapped
        # reservation would be 6144 MB of a 6144 MB node.
        self.assertEqual(
            {
                'cpu_cores_reserved': 2,
                'cpu_schedulable': 1,
                'cpu_cores_schedulable': 1,
                'memory_reserved_mb': 3072,
            },
            resources_main._compute_reservations(2, 4, True, 1, 1, 2.0, 4.0, 6144))

    def test_configurable_reservations(self):
        # The knobs are honoured, not hardcoded.
        self.assertEqual(
            {
                'cpu_cores_reserved': 4,
                'cpu_schedulable': 16,
                'cpu_cores_schedulable': 12,
                'memory_reserved_mb': 9216,
            },
            resources_main._compute_reservations(16, 24, True, 2, 2, 1.0, 8.0, 65536))


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
