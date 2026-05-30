# Copyright 2026 Michael Still and contributors
"""Unit tests for the local eventlog spool.

Crash recovery, orphan rescue, and high-water-mark behaviour
are the things that have to work right -- everything else in
``shakenfist.eventlog_spool`` is straightforward sqlite I/O.
"""
import fcntl
import os
import tempfile
import threading
import time
from unittest import mock

from shakenfist import eventlog_spool
from shakenfist.tests import base


class _SpoolRootMixin:
    """Redirect ``SPOOL_ROOT`` to a tempdir for every test."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix='sf-spool-test-')
        self._original_root = eventlog_spool.SPOOL_ROOT
        eventlog_spool.SPOOL_ROOT = self.tmp
        # Reset module-level singletons between tests so each test
        # starts from a clean slate.
        eventlog_spool.reset_for_tests()
        self.addCleanup(eventlog_spool.reset_for_tests)
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        eventlog_spool.SPOOL_ROOT = self._original_root


class SpoolBasicsTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """End-to-end: enqueue -> dequeue -> delete."""

    def test_initialise_creates_spool_named_by_pid(self):
        s = eventlog_spool.initialise('test-daemon')
        expected = os.path.join(
            self.tmp, f'test-daemon-{os.getpid()}.db')
        self.assertEqual(expected, s.path)
        self.assertTrue(os.path.exists(expected))

    def test_initialise_is_idempotent(self):
        first = eventlog_spool.initialise('test-daemon')
        second = eventlog_spool.initialise('test-daemon')
        # Same instance, no second open.
        self.assertIs(first, second)

    def test_round_trip(self):
        s = eventlog_spool.initialise('test-daemon')
        s.enqueue({'a': 1, 'msg': 'hello'})
        s.enqueue({'a': 2, 'msg': 'world'})

        batch = s.dequeue_batch(10)
        self.assertEqual(2, len(batch))
        self.assertEqual({'a': 1, 'msg': 'hello'}, batch[0][1])
        self.assertEqual({'a': 2, 'msg': 'world'}, batch[1][1])

        deleted = s.delete_ids([row_id for row_id, _ in batch])
        self.assertEqual(2, deleted)
        self.assertEqual(0, s.count())

    def test_dequeue_returns_oldest_first(self):
        s = eventlog_spool.initialise('test-daemon')
        for i in range(5):
            s.enqueue({'i': i})

        # limit smaller than population
        batch = s.dequeue_batch(3)
        self.assertEqual([0, 1, 2], [p['i'] for _, p in batch])

    def test_dequeue_batch_empty_when_no_events(self):
        s = eventlog_spool.initialise('test-daemon')
        self.assertEqual([], s.dequeue_batch(10))

    def test_delete_ids_empty_is_zero(self):
        s = eventlog_spool.initialise('test-daemon')
        self.assertEqual(0, s.delete_ids([]))


class SpoolHighWaterMarkTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """Drop posture above the high-water mark."""

    def test_enqueue_returns_false_when_full(self):
        s = eventlog_spool.initialise('test-daemon')
        # Use a tiny cap so the test runs fast.
        with mock.patch.object(
                eventlog_spool, 'SPOOL_HIGH_WATER_MARK', 3):
            self.assertTrue(s.enqueue({'i': 1}))
            self.assertTrue(s.enqueue({'i': 2}))
            self.assertTrue(s.enqueue({'i': 3}))
            self.assertFalse(s.enqueue({'i': 4}))
            self.assertEqual(3, s.count())

    def test_module_enqueue_falls_back_on_uninitialised(self):
        # No initialise() call.
        self.assertFalse(
            eventlog_spool.enqueue({'event_type': 'audit'}))

    def test_module_enqueue_drops_above_high_water(self):
        eventlog_spool.initialise('test-daemon')
        with mock.patch.object(
                eventlog_spool, 'SPOOL_HIGH_WATER_MARK', 2):
            self.assertTrue(
                eventlog_spool.enqueue({'i': 1}))
            self.assertTrue(
                eventlog_spool.enqueue({'i': 2}))
            self.assertFalse(
                eventlog_spool.enqueue({'i': 3}))

    def test_concurrent_enqueue_and_dequeue(self):
        # Regression for sf-database 'NoneType is not subscriptable'
        # and 'bad parameter or other API misuse' errors observed
        # when multiple gRPC worker threads enqueue while the
        # drainer thread reads from the same sqlite connection.
        # Without the per-connection lock, cursor state races and
        # this test surfaces either error within a few seconds.
        s = eventlog_spool.initialise('test-daemon')
        stop = threading.Event()
        errors = []

        def writer(start_n):
            n = start_n
            while not stop.is_set():
                try:
                    s.enqueue({'i': n})
                    n += 1
                except Exception as e:
                    errors.append(('writer', e))
                    return

        def reader():
            while not stop.is_set():
                try:
                    batch = s.dequeue_batch(limit=50)
                    if batch:
                        s.delete_ids(row_id for row_id, _ in batch)
                except Exception as e:
                    errors.append(('reader', e))
                    return

        writers = [
            threading.Thread(target=writer, args=(w * 10_000,))
            for w in range(4)
        ]
        readers = [threading.Thread(target=reader) for _ in range(2)]
        for t in writers + readers:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in writers + readers:
            t.join(timeout=5)

        self.assertEqual([], errors)


class SpoolOrphanRecoveryTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """Rows from dead-pid spool files are migrated in on startup."""

    def _make_orphan(self, daemon_name, pid, events):
        """Drop a spool file claimed by ``pid`` and fill it."""
        path = os.path.join(
            self.tmp, f'{daemon_name}-{pid}.db')
        s = eventlog_spool.Spool(path)
        for e in events:
            s.enqueue(e)
        s.close()
        return path

    def test_orphan_with_dead_pid_is_drained_in(self):
        # PID 1 is init -- always alive on Linux. Use a clearly
        # bogus pid that won't exist.
        dead_pid = 99999999
        self.assertFalse(os.path.isdir(f'/proc/{dead_pid}'))
        orphan_path = self._make_orphan(
            'previous-daemon', dead_pid,
            [{'i': 1}, {'i': 2}])

        s = eventlog_spool.initialise('current-daemon')
        # Both events live in the current spool.
        self.assertEqual(2, s.count())
        # Orphan file deleted.
        self.assertFalse(os.path.exists(orphan_path))

    def test_orphan_with_live_pid_is_left_alone(self):
        # Use OUR pid as the "live" pid so the check actually
        # sees a real /proc entry. A sibling daemon's spool with
        # this pid should not be touched by orphan recovery.
        live_pid = os.getpid() + 1  # Some other "live" process
        with mock.patch.object(
                eventlog_spool, '_pid_is_alive',
                return_value=True):
            orphan_path = self._make_orphan(
                'sibling-daemon', live_pid, [{'i': 99}])

            # Initialise a DIFFERENT daemon name + pid; orphan
            # check sees sibling spool but skips it.
            s = eventlog_spool.initialise('current-daemon')
            self.assertEqual(0, s.count())
            self.assertTrue(os.path.exists(orphan_path))

    def test_orphan_flock_held_elsewhere_is_skipped(self):
        # Regression for the 5x event duplication seen in the
        # Debian 12 cluster smoke (test_network_events delete
        # events arriving in chunks 5 times each with distinct
        # correlation_ids). Multiple gunicorn workers racing on
        # the same orphan spool produced one downstream event
        # per recoverer; the flock guard means only one wins.
        dead_pid = 99999999
        self.assertFalse(os.path.isdir(f'/proc/{dead_pid}'))
        orphan_path = self._make_orphan(
            'previous-daemon', dead_pid, [{'i': 1}, {'i': 2}])

        # Simulate a concurrent recoverer by holding an
        # exclusive flock on the orphan from a separate FD.
        # flock(2) treats multiple opens of the same file as
        # independent within one process, so this exercises the
        # exact contention path the cross-process race produces.
        holder_fd = os.open(orphan_path, os.O_RDONLY)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            s = eventlog_spool.initialise('current-daemon')
            # Orphan was skipped; rows did NOT migrate into us
            # and the file is still on disk for the lock holder
            # to finish recovering.
            self.assertEqual(0, s.count())
            self.assertTrue(os.path.exists(orphan_path))
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_orphan_recovery_handles_unparseable_filename(self):
        # A stray file in SPOOL_ROOT that doesn't follow the
        # daemon-pid.db pattern must not crash initialise.
        weird = os.path.join(self.tmp, 'no-hyphen-no-pid.db')
        with open(weird, 'w') as f:
            f.write('not a sqlite database')
        # Should complete without raising even though the file is
        # garbage; the parse helper returns None for "can't
        # extract a pid" and the orphan loop skips it.
        s = eventlog_spool.initialise('current-daemon')
        self.assertEqual(0, s.count())
        # The garbage file is left in place (we didn't know what
        # to do with it).
        self.assertTrue(os.path.exists(weird))


class SpoolDurabilityTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """A committed enqueue survives reopening the file."""

    def test_enqueue_persists_across_close_reopen(self):
        path = os.path.join(self.tmp, 'durable-test.db')
        s1 = eventlog_spool.Spool(path)
        s1.enqueue({'msg': 'should survive'})
        s1.close()

        s2 = eventlog_spool.Spool(path)
        batch = s2.dequeue_batch(10)
        self.assertEqual(1, len(batch))
        self.assertEqual('should survive', batch[0][1]['msg'])
        s2.close()


class PidParsingTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """Edge cases on ``_pid_from_spool_path``."""

    def test_simple_daemon_name(self):
        self.assertEqual(
            12345,
            eventlog_spool._pid_from_spool_path(
                '/x/y/cleaner-12345.db'))

    def test_daemon_name_with_hyphens(self):
        # The split is on the *last* hyphen so multi-word daemon
        # names work.
        self.assertEqual(
            789,
            eventlog_spool._pid_from_spool_path(
                '/x/y/eventlog-drainer-789.db'))

    def test_unparseable_returns_none(self):
        self.assertIsNone(
            eventlog_spool._pid_from_spool_path(
                '/x/y/no-pid-here.db'))
        self.assertIsNone(
            eventlog_spool._pid_from_spool_path(
                '/x/y/nohyphen.db'))
