# Copyright 2026 Michael Still and contributors

"""The queues daemon's dequeue loop backs off while idle and wakes on work.

The loop under test is Monitor._run_inner() in daemons/queues/main.py, which
gained an IdlePollBackoff in issue #3499 but had no coverage of either the
sleep or the wake path. See issue #3943.
"""

from unittest import mock

from shakenfist import exceptions
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
                mock.patch.object(
                    queues_main, '_block_until_healthy') as block, \
                mock.patch.object(
                    queues_main.sf_locks, 'get_existing_locks',
                    return_value={}):
            m._run_inner()

        self.block_until_healthy = block
        return idle_calls

    def test_backs_off_when_idle(self):
        m = self._make_monitor()
        # An empty dequeue on every poll: the sleep grows geometrically to
        # the cap.
        idle_calls = self._run_schedule(
            m, [daemon.DequeueOutcome.NOTHING_ELIGIBLE] * 5)
        self.assertEqual([0.2, 0.4, 0.8, 1.6, 2.0], idle_calls)

    def test_stays_fast_while_work_present(self):
        m = self._make_monitor()
        # While every dequeue starts a job the loop never sleeps at all: it
        # polls again immediately.
        idle_calls = self._run_schedule(
            m, [daemon.DequeueOutcome.STARTED] * 4)
        self.assertEqual([], idle_calls)

    def test_wakes_from_backoff_when_work_arrives(self):
        m = self._make_monitor()
        # Backoff has grown to the cap, then a job is dequeued: the backoff
        # resets, so the next empty poll sleeps the fast interval again.
        # See issue #3943.
        idle_calls = self._run_schedule(
            m, [daemon.DequeueOutcome.NOTHING_ELIGIBLE] * 5 +
            [daemon.DequeueOutcome.STARTED,
             daemon.DequeueOutcome.NOTHING_ELIGIBLE])
        self.assertEqual([0.2, 0.4, 0.8, 1.6, 2.0, 0.2], idle_calls)

    def test_pool_full_does_not_back_off(self):
        m = self._make_monitor()
        # A full worker pool reads no queue, so there is no database load for
        # the backoff to remove: every pass retries at the fast interval
        # instead of climbing to the cap, which is what delayed dispatch by
        # up to IDLE_POLL_MAX_SECONDS after a worker freed up. See issue
        # #3942.
        idle_calls = self._run_schedule(
            m, [daemon.DequeueOutcome.POOL_FULL] * 5)
        self.assertEqual([0.2] * 5, idle_calls)

    def test_pool_full_leaves_the_backoff_state_alone(self):
        m = self._make_monitor()
        # A pool-full pass neither grows nor resets the empty-poll backoff,
        # so an idle period either side of a burst is unaffected: the empty
        # poll after the burst resumes from where the first one left off.
        idle_calls = self._run_schedule(
            m, [daemon.DequeueOutcome.NOTHING_ELIGIBLE,
                daemon.DequeueOutcome.POOL_FULL,
                daemon.DequeueOutcome.POOL_FULL,
                daemon.DequeueOutcome.NOTHING_ELIGIBLE])
        self.assertEqual([0.2, 0.2, 0.2, 0.4], idle_calls)

    def test_a_refused_call_backs_off_rather_than_spinning(self):
        m = self._make_monitor()
        # The tier answered and refused: it is healthy, so waiting for it
        # to be healthy returns at once, and the loop used to reissue the
        # same Dequeue with no sleep. A refusal backs off like an empty
        # queue instead, and the backoff keeps growing while it persists.
        refused = exceptions.DatabaseCallFailed('refused', code='RESOURCE_EXHAUSTED')
        idle_calls = self._run_schedule(m, [refused] * 3)
        self.assertEqual([0.2, 0.4, 0.8], idle_calls)
        # Only the startup and periodic third party checks wait for
        # health; no refusal does.
        self.assertEqual(2, self.block_until_healthy.call_count)

    def test_an_unreachable_tier_still_waits_for_health(self):
        m = self._make_monitor()
        # The negative control: an outage is not a refusal, and pauses in
        # _block_until_healthy() rather than sleeping a backoff interval.
        idle_calls = self._run_schedule(
            m, [exceptions.DatabaseUnavailable('gone')] * 3)
        self.assertEqual([], idle_calls)
        self.assertEqual(2 + 3, self.block_until_healthy.call_count)
