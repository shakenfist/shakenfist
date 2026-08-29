# Copyright 2026 Michael Still and contributors

"""The network dispatcher's dequeue loop backs off while idle and wakes on
work.

The loop under test is Job.execute() in daemons/network/workitem.py, which
gained an IdlePollBackoff in issue #3499 but had no coverage of either the
sleep or the wake path. Unlike the queues and transfers daemons the
dispatcher sleeps in 0.2s chunks of its own (it runs in a worker thread, so
it cannot use Daemon.idle()), so these tests measure the summed sleep
between consecutive polls rather than mocking a sleep helper. See issue
#3943.
"""

import os
import shutil
import tempfile
from unittest import mock

from shakenfist.daemons.network import workitem
from shakenfist.tests import base


class NetworkDispatcherBackoffTestCase(base.ShakenFistTestCase):
    def _run_dispatcher(self, schedule):
        # Run execute() with one dequeue poll per schedule entry, where each
        # entry is the item list that poll returns. Returns the seconds slept
        # before each poll (index 0 is the sleep before the first poll,
        # always zero). The abort path is set as the last scheduled poll
        # returns, which ends the dispatch loop.
        tmp = tempfile.mkdtemp(prefix='sf-net-backoff-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        abort_path = os.path.join(tmp, 'abort')

        # Construct a Job without running __init__ (which touches the
        # filesystem under /run/sf). execute() only needs abort_path here:
        # _routing_key is never reached because from_db returns None below.
        j = workitem.Job.__new__(workitem.Job)
        j.abort_path = abort_path

        remaining = list(schedule)
        slept = [0.0]
        sleeps_before_poll = []

        def fake_sleep(seconds):
            slept[0] += seconds

        def fake_dequeue(queue_names, limit=None):
            # Rounded because the chunked sleep accumulates float error.
            sleeps_before_poll.append(round(slept[0], 10))
            slept[0] = 0.0
            items = remaining.pop(0)
            if not remaining:
                # The last scheduled poll asks the dispatcher to shut down.
                with open(abort_path, 'w') as f:
                    f.write('1')
            return items

        # Work items resolve to a missing operation, which the dispatcher
        # resolves and skips -- the shortest path through "found work".
        op_class = mock.MagicMock()
        op_class.from_db.return_value = None

        with mock.patch.object(workitem, 'config') as mock_config, \
                mock.patch.object(workitem.Job, '_worker_loop'), \
                mock.patch.object(workitem, 'mariadb') as mock_mdb, \
                mock.patch.object(
                    workitem, 'get_object_class', return_value=op_class), \
                mock.patch.object(
                    workitem.time, 'sleep', side_effect=fake_sleep):
            mock_config.NODE_UUID = 'test-node-uuid'
            mock_config.NODE_IS_NETWORK_NODE = False
            mock_config.NETWORK_OPERATION_WORKERS = 1
            mock_mdb.dequeue_work_items.side_effect = fake_dequeue
            j.execute()

        return sleeps_before_poll

    def _work_item(self):
        return [('queue', 'job', {'operation_type': 'network_create',
                                  'operation_uuid': 'op-uuid'})]

    def test_backs_off_when_idle(self):
        # An empty batch on every poll: the sleep grows geometrically to the
        # cap. The final poll triggers shutdown, so nothing is slept after it.
        sleeps = self._run_dispatcher([[]] * 6)
        self.assertEqual([0.0, 0.2, 0.4, 0.8, 1.6, 2.0], sleeps)

    def test_stays_fast_while_work_present(self):
        # While every poll returns work the dispatcher never sleeps at all.
        sleeps = self._run_dispatcher(
            [self._work_item(), self._work_item(), []])
        self.assertEqual([0.0, 0.0, 0.0], sleeps)

    def test_wakes_from_backoff_when_work_arrives(self):
        # Backoff has grown to the cap, then a batch arrives: it is
        # dispatched without sleeping first, the backoff resets, and the
        # next empty poll sleeps the fast interval again. See issue #3943.
        sleeps = self._run_dispatcher(
            [[]] * 5 + [self._work_item()] + [[], []])
        self.assertEqual(
            [0.0, 0.2, 0.4, 0.8, 1.6, 2.0, 0.0, 0.2], sleeps)
