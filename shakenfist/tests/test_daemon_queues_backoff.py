# Copyright 2026 Michael Still and contributors

"""The queues daemon's dequeue loop backs off while idle and wakes on work.

The loop under test is Monitor._run_inner() in daemons/queues/main.py, which
gained an IdlePollBackoff in issue #3499 but had no coverage of either the
sleep or the wake path. See issue #3943.
"""

from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.daemons.queues import main as queues_main
from shakenfist.tests import base


class QueuesBackoffTestCase(base.ShakenFistTestCase):
    def _make_monitor(self):
        # Construct a Monitor without running __init__ (which touches
        # setproctitle, logging and the filesystem). _run_inner only reads the
        # attributes and instance methods stubbed below.
        m = queues_main.Monitor.__new__(queues_main.Monitor)
        m.abort_path = '/run/sf/_test.abort'
        m.reap_workers = mock.MagicMock()
        m.check_daemon_state = mock.MagicMock()
        return m

    def _run_schedule(self, m, dequeue_results):
        # Run one loop iteration per entry, where each entry is what
        # dequeue_job returns that iteration. Returns the idle() sleeps
        # requested; an iteration which found work requests none.
        idle_calls = []
        m.idle = mock.MagicMock(side_effect=lambda s: idle_calls.append(s))
        m.dequeue_job = mock.MagicMock(side_effect=list(dequeue_results))

        polls = {'n': 0}

        def fake_abort(path):
            polls['n'] += 1
            return polls['n'] <= len(dequeue_results)

        with mock.patch.object(
                daemon, 'check_abort_path', side_effect=fake_abort), \
                mock.patch.object(queues_main, '_block_until_healthy'), \
                mock.patch.object(
                    queues_main.sf_locks, 'get_existing_locks',
                    return_value={}):
            m._run_inner()

        return idle_calls

    def test_backs_off_when_idle(self):
        m = self._make_monitor()
        # An empty dequeue on every poll: the sleep grows geometrically to
        # the cap.
        idle_calls = self._run_schedule(m, [False] * 5)
        self.assertEqual([0.2, 0.4, 0.8, 1.6, 2.0], idle_calls)

    def test_stays_fast_while_work_present(self):
        m = self._make_monitor()
        # While every dequeue starts a job the loop never sleeps at all: it
        # polls again immediately.
        idle_calls = self._run_schedule(m, [True] * 4)
        self.assertEqual([], idle_calls)

    def test_wakes_from_backoff_when_work_arrives(self):
        m = self._make_monitor()
        # Backoff has grown to the cap, then a job is dequeued: the backoff
        # resets, so the next empty poll sleeps the fast interval again.
        # See issue #3943.
        idle_calls = self._run_schedule(
            m, [False] * 5 + [True, False])
        self.assertEqual([0.2, 0.4, 0.8, 1.6, 2.0, 0.2], idle_calls)
