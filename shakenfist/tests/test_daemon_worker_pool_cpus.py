# Copyright 2026 Michael Still and contributors

"""A worker pool daemon sizes itself without asking libvirt.

The pool size came from util_libvirt.get_cpu_count(), called in
WorkerPoolDaemon.__init__ with no retry and nothing to catch it. When
libvirtd deactivated in the instant sf-net opened its first connection,
the daemon exited 1 before run(), the deploy's "Start or restart the
other SF daemons" task failed, and the per-node checks failed a second
time on the journal line (#4785). sf-database, which has no other use
for libvirt at all, was exposed to the same failure.
"""

import os
from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.tests import base


class WorkerPoolCpuCountTestCase(base.ShakenFistTestCase):
    def _construct(self):
        # Daemon.__init__ sets a process title, installs signal handlers
        # and writes to the filesystem; none of that is what this test is
        # about.
        with mock.patch.object(daemon.Daemon, '__init__', return_value=None), \
                mock.patch('shakenfist.util.libvirt.get_libvirt') as get_libvirt, \
                mock.patch('shakenfist.util.libvirt.LibvirtConnection') as connection:
            d = daemon.WorkerPoolDaemon('net')
        return d, get_libvirt, connection

    def test_construction_does_not_open_a_libvirt_connection(self):
        d, get_libvirt, connection = self._construct()
        get_libvirt.assert_not_called()
        connection.assert_not_called()
        self.assertGreaterEqual(d.usable_cpus, 1)

    def test_the_pool_is_sized_by_the_cpus_this_process_may_use(self):
        # Affinity, not the host's CPU count: a daemon pinned to two CPUs
        # cannot usefully run a pool sized for ninety-six.
        with mock.patch.object(daemon.Daemon, '__init__', return_value=None), \
                mock.patch.object(os, 'sched_getaffinity',
                                  return_value={0, 3, 7}) as affinity:
            d = daemon.WorkerPoolDaemon('net')
        affinity.assert_called_once_with(0)
        self.assertEqual(3, d.usable_cpus)
