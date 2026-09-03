# Copyright 2019 Michael Still and contributors
"""Regression tests for sf-queues startup restore threading.

startup_tasks() used to call restore_instances() inline. The network
restore enqueues cluster operations on this node's own
clusteroperation queues and waits up to 600 seconds per operation for
them to complete -- but the only consumer of those queues is this
same daemon's worker pool, which starts after startup_tasks()
returns. Under Type=notify with TimeoutStartSec=90 systemd killed the
daemon before READY whenever the node hosted instances, making
sf-queues unstartable (observed during the first in-flight sfcbr
upgrade on 2026-07-12). The restore must run on a background thread
so its waits complete once the main loop is consuming.
"""

import contextlib
import json
import threading
from unittest import mock

from shakenfist.daemons.queues import startup_tasks
from shakenfist.tests import base


class FakeInstance:
    """A deliberately minimal instance stand-in.

    This is a plain class rather than a MagicMock so that accessing an
    attribute which does not exist on real Instance objects (such as the
    etcd-era ``inst.etcd``) raises AttributeError, just like production.
    """

    def __init__(self, uuid, fail_restore=False):
        self.uuid = uuid
        self.power_state = 'on'
        self.fail_restore = fail_restore
        self.restored = False
        self.delete_errors = []

        # The placement reconciliation at the end of restore_instances()
        # records where each instance is through the admission RPC, which
        # needs the instance's namespace and its resource sizes.
        self.namespace = 'unittest'
        self.cpus = 1
        self.memory = 1024
        self.disk_spec = [{'base': 'cirros', 'size': 8}]
        self.placement = {'node': 'fake-node-uuid', 'placement_attempts': 1}

    def get_lock(self, timeout=None, op=None, global_scope=False):
        return contextlib.nullcontext()

    def create_on_hypervisor(self):
        if self.fail_restore:
            raise RuntimeError('hypervisor exploded')
        self.restored = True

    def enqueue_delete_due_error(self, error_msg):
        self.delete_errors.append(error_msg)

    def __str__(self):
        return 'instance(%s)' % self.uuid


class RestoreInstancesErrorPathTestCase(base.ShakenFistTestCase):
    """Regression test for issue #3552.

    The instance-restore error path used to call
    ``inst.etcd.enqueue_delete_due_error(...)``, an etcd-era leftover.
    Instance objects no longer have an ``etcd`` attribute, so a failed
    restore raised AttributeError instead of enqueuing the delete, and
    the exception aborted the loop so later instances were never
    restored.
    """

    def test_failed_restore_enqueues_delete_and_continues(self):
        failing = FakeInstance('uuid-failing', fail_restore=True)
        healthy = FakeInstance('uuid-healthy')

        fake_config = mock.MagicMock()
        fake_config.NODE_NAME = 'fake-node'

        fake_instance_module = mock.MagicMock()
        fake_instance_module.Instances.return_value = [failing, healthy]
        fake_instance_module.Instance.STATE_INITIAL = 'initial'

        fake_node = mock.MagicMock()
        fake_node.instances = []
        fake_node_class = mock.MagicMock()
        fake_node_class.from_db.return_value = fake_node

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                startup_tasks, 'config', fake_config))
            stack.enter_context(mock.patch.object(
                startup_tasks, 'instance', fake_instance_module))
            stack.enter_context(mock.patch.object(
                startup_tasks, 'interfaces_for_instance', return_value=[]))
            stack.enter_context(mock.patch.object(
                startup_tasks, 'Node', fake_node_class))
            mock_ignore = stack.enter_context(mock.patch.object(
                startup_tasks.util_exceptions, 'ignore_exception'))
            admit = stack.enter_context(mock.patch.object(
                startup_tasks.mariadb, 'admit_instance_placement',
                return_value={'success': True, 'error': '',
                              'admitted': True, 'unguarded': False,
                              'clamped': False, 'failing_stage': '',
                              'dimensions': [], 'node_used_cpus': 0,
                              'node_used_memory_mb': 0,
                              'node_used_disk_gb': 0,
                              'node_expected_demand': 0.0}))

            startup_tasks.restore_instances()

        # The failing instance was enqueued for deletion due to error...
        self.assertEqual(
            ['exception while restoring instance on daemon restart'],
            failing.delete_errors)
        mock_ignore.assert_called_once()

        # ...and the failure did not stop later instances being restored.
        self.assertTrue(healthy.restored)
        self.assertEqual([], healthy.delete_errors)

        # Both instances had their placement reference rows repaired
        # through the admission RPC, without enforcing the capacity
        # guard (P5).
        self.assertEqual(2, admit.call_count)
        for call in admit.call_args_list:
            self.assertFalse(call.kwargs['enforce'])


class StartupRestoreThreadTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        fake_config = mock.MagicMock()
        fake_config.NODE_UUID = 'fake-node-uuid'
        fake_config.NODE_NAME = 'fake-node'
        fake_config.NODE_MESH_IP = '10.0.0.1'
        fake_config.model_dump.return_value = {}

        fake_mariadb = mock.MagicMock()
        fake_mariadb.get_work_queue_length.return_value = (0, 0, 0)

        self.patchers = [
            mock.patch.object(startup_tasks, 'config', fake_config),
            mock.patch.object(startup_tasks, 'mariadb', fake_mariadb),
            mock.patch.object(startup_tasks, '_resolve_node_uuid'),
            mock.patch.object(startup_tasks, 'Node'),
            mock.patch.object(startup_tasks, 'daemon'),
            mock.patch.object(startup_tasks, 'locks'),
            mock.patch.object(startup_tasks, 'util_general'),
            mock.patch.object(startup_tasks, 'util_concurrency'),
            mock.patch.object(
                startup_tasks, 'get_all_node_queues', return_value=[]),
            mock.patch.object(startup_tasks, 'upgrade_blob_datastore'),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_startup_does_not_block_on_restore(self):
        release = threading.Event()
        restore_threads = []

        def slow_restore():
            restore_threads.append(threading.current_thread())
            release.wait(30)

        with mock.patch.object(
                startup_tasks, 'restore_instances',
                side_effect=slow_restore):
            restore_thread = startup_tasks.startup_tasks()

            # startup_tasks() must return while the restore is still
            # blocked; the daemon's worker pool (the only consumer of
            # the operations the restore waits on) starts after this.
            self.assertIsNotNone(restore_thread)
            self.assertTrue(restore_thread.daemon)
            self.assertTrue(restore_thread.is_alive())

            release.set()
            restore_thread.join(10)
            self.assertFalse(restore_thread.is_alive())

        # And the restore ran off the main thread.
        self.assertEqual(1, len(restore_threads))
        self.assertNotEqual(
            threading.current_thread(), restore_threads[0])

    def test_restore_exception_is_captured(self):
        with mock.patch.object(
                startup_tasks, 'restore_instances',
                side_effect=RuntimeError('boom')):
            with mock.patch.object(
                    startup_tasks.util_exceptions,
                    'ignore_exception') as mock_ignore:
                restore_thread = startup_tasks.startup_tasks()
                restore_thread.join(10)
                self.assertFalse(restore_thread.is_alive())

        mock_ignore.assert_called_once()
        self.assertEqual(
            'startup instance restore', mock_ignore.call_args.args[0])


class RestorePlacementReconciliationTestCase(base.ShakenFistTestCase):
    """The startup placement reconciliation goes through the RPCs.

    It used to write INSTANCE_LOCATION reference rows directly, which
    could leave an instance recorded on two nodes at once. Every write
    now goes through the admission and release RPCs, whose
    delete-all-then-insert makes duplicate placement rows unproducible,
    and none of them enforce the capacity guard (P5): this path records
    where libvirt domains already are.
    """

    NODE_UUID = 'fake-node-uuid'

    def _run(self, restored=None, current=None, known=None):
        fake_config = mock.MagicMock()
        fake_config.NODE_NAME = 'fake-node'

        fake_instance_module = mock.MagicMock()
        fake_instance_module.Instances.return_value = restored or []
        fake_instance_module.Instance.STATE_INITIAL = 'initial'
        fake_instance_module.Instance.TERMINAL_STATES = {'deleted'}
        fake_instance_module.Instance.from_db.side_effect = (
            lambda u: (known or {}).get(u))

        fake_node = mock.MagicMock()
        fake_node.instances = current or []
        fake_node.uuid = self.NODE_UUID
        fake_node_class = mock.MagicMock()
        fake_node_class.from_db.return_value = fake_node

        admit_reply = {
            'success': True, 'error': '', 'admitted': True,
            'unguarded': False, 'clamped': False, 'failing_stage': '',
            'dimensions': [], 'node_used_cpus': 0, 'node_used_memory_mb': 0,
            'node_used_disk_gb': 0, 'node_expected_demand': 0.0}
        release_reply = {
            'success': True, 'error': '', 'released': True, 'clamped': False,
            'counters_node_uuid': '', 'node_used_cpus': 0,
            'node_used_memory_mb': 0, 'node_used_disk_gb': 0,
            'node_expected_demand': 0.0}

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                startup_tasks, 'config', fake_config))
            stack.enter_context(mock.patch.object(
                startup_tasks, 'instance', fake_instance_module))
            stack.enter_context(mock.patch.object(
                startup_tasks, 'interfaces_for_instance', return_value=[]))
            stack.enter_context(mock.patch.object(
                startup_tasks, 'Node', fake_node_class))
            admit = stack.enter_context(mock.patch.object(
                startup_tasks.mariadb, 'admit_instance_placement',
                return_value=admit_reply))
            release = stack.enter_context(mock.patch.object(
                startup_tasks.mariadb, 'release_instance_placement',
                return_value=release_reply))

            startup_tasks.restore_instances()

        return admit, release

    def test_a_missing_reference_is_recorded_here(self):
        inst = FakeInstance('uuid-here')
        inst.placement = {'node': self.NODE_UUID, 'placement_attempts': 3}
        admit, release = self._run(restored=[inst])

        admit.assert_called_once()
        args, kwargs = admit.call_args
        self.assertEqual(
            ('uuid-here', 'unittest', self.NODE_UUID, 1, 1024, 8), args[:6])
        self.assertEqual('', kwargs['old_node_uuid'])
        self.assertFalse(kwargs['enforce'])
        self.assertFalse(release.called)

        # The placement attribute is the authority here and is rewritten
        # unchanged -- this is a repair, not a new placement attempt.
        self.assertEqual(
            {'node': self.NODE_UUID, 'placement_attempts': 3},
            json.loads(args[6]))

    def test_a_stale_reference_moves_to_the_real_node(self):
        # The instance is alive but placed elsewhere, so our reference
        # row is stale. Recording it where it really is removes our row
        # as a side effect of the admission's delete-all-then-insert.
        elsewhere = FakeInstance('uuid-moved')
        elsewhere.placement = {'node': 'other-node-uuid',
                               'placement_attempts': 2}
        elsewhere.state = mock.MagicMock()
        elsewhere.state.value = 'created'

        admit, release = self._run(
            current=['uuid-moved'], known={'uuid-moved': elsewhere})

        args, kwargs = admit.call_args
        self.assertEqual('other-node-uuid', args[2])
        self.assertEqual(self.NODE_UUID, kwargs['old_node_uuid'])
        self.assertFalse(kwargs['enforce'])
        self.assertFalse(release.called)

    def test_a_deleted_instance_has_its_capacity_released(self):
        gone = FakeInstance('uuid-gone')
        gone.placement = {'node': self.NODE_UUID, 'placement_attempts': 1}
        gone.state = mock.MagicMock()
        gone.state.value = 'deleted'

        admit, release = self._run(
            current=['uuid-gone'], known={'uuid-gone': gone})

        self.assertFalse(admit.called)
        release.assert_called_once_with(
            'uuid-gone', 'unittest', 1, 1024, 8, node_uuid=self.NODE_UUID)

    def test_a_vanished_instance_has_its_reference_dropped(self):
        # No instance row at all, so there is nothing to read resource
        # sizes from: drop the row and let the capacity reconciler
        # recompute the counters rather than guessing at amounts.
        admit, release = self._run(current=['uuid-vanished'])

        self.assertFalse(admit.called)
        release.assert_called_once_with(
            'uuid-vanished', '', 0, 0, 0, node_uuid=self.NODE_UUID)
