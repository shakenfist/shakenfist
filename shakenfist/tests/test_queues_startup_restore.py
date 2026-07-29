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

            startup_tasks.restore_instances()

        # The failing instance was enqueued for deletion due to error...
        self.assertEqual(
            ['exception while restoring instance on daemon restart'],
            failing.delete_errors)
        mock_ignore.assert_called_once()

        # ...and the failure did not stop later instances being restored.
        self.assertTrue(healthy.restored)
        self.assertEqual([], healthy.delete_errors)


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
