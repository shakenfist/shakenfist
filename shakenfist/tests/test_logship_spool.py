# Copyright 2026 Michael Still and contributors
"""Unit tests for the local logship spool.

Crash recovery, orphan rescue, and high-water-mark behaviour are
the things that have to work right -- everything else in
``shakenfist.logship_spool`` is straightforward sqlite I/O. This
suite mirrors ``test_eventlog_spool.py``.
"""
import fcntl
import os
from unittest import mock

from shakenfist import logship_spool
from shakenfist.tests import base


class _SpoolRootMixin(base.SpoolRootMixin):
    """Redirect ``SPOOL_ROOT`` to a tempdir for every test."""

    spool_module = logship_spool
    spool_prefix = 'sf-logship-spool-test-'


class SpoolBasicsTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """End-to-end: enqueue -> dequeue -> delete."""

    def test_initialise_creates_spool_named_by_pid(self):
        s = logship_spool.initialise('test-daemon')
        expected = os.path.join(
            self.tmp, f'test-daemon-{os.getpid()}.db')
        self.assertEqual(expected, s.path)
        self.assertTrue(os.path.exists(expected))

    def test_initialise_is_idempotent(self):
        first = logship_spool.initialise('test-daemon')
        second = logship_spool.initialise('test-daemon')
        self.assertIs(first, second)

    def test_round_trip(self):
        s = logship_spool.initialise('test-daemon')
        s.enqueue(100, 'hello')
        s.enqueue(200, 'world')

        batch = s.dequeue_batch(10)
        self.assertEqual(2, len(batch))
        # (id, ts_ns, line)
        self.assertEqual(100, batch[0][1])
        self.assertEqual('hello', batch[0][2])
        self.assertEqual(200, batch[1][1])
        self.assertEqual('world', batch[1][2])

        deleted = s.delete_ids([row_id for row_id, _, _ in batch])
        self.assertEqual(2, deleted)
        self.assertEqual(0, s.count())

    def test_dequeue_returns_oldest_first(self):
        s = logship_spool.initialise('test-daemon')
        for i in range(5):
            s.enqueue(i, f'line-{i}')

        batch = s.dequeue_batch(3)
        self.assertEqual(
            ['line-0', 'line-1', 'line-2'],
            [line for _, _, line in batch])

    def test_dequeue_batch_empty_when_no_rows(self):
        s = logship_spool.initialise('test-daemon')
        self.assertEqual([], s.dequeue_batch(10))

    def test_delete_ids_empty_is_zero(self):
        s = logship_spool.initialise('test-daemon')
        self.assertEqual(0, s.delete_ids([]))


class SpoolHighWaterMarkTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """Drop posture above the high-water mark."""

    def test_enqueue_returns_false_when_full(self):
        s = logship_spool.initialise('test-daemon')
        with mock.patch.object(
                logship_spool, 'SPOOL_HIGH_WATER_MARK', 3):
            self.assertTrue(s.enqueue(1, 'a'))
            self.assertTrue(s.enqueue(2, 'b'))
            self.assertTrue(s.enqueue(3, 'c'))
            self.assertFalse(s.enqueue(4, 'd'))
            self.assertEqual(3, s.count())

    def test_module_enqueue_falls_back_on_uninitialised(self):
        # No initialise() call.
        self.assertFalse(logship_spool.enqueue(1, 'line'))

    def test_module_enqueue_drops_above_high_water_and_counts(self):
        logship_spool.initialise('test-daemon')
        before = logship_spool.LOGSHIP_SPOOL_DROPPED._value.get()
        with mock.patch.object(
                logship_spool, 'SPOOL_HIGH_WATER_MARK', 2):
            self.assertTrue(logship_spool.enqueue(1, 'a'))
            self.assertTrue(logship_spool.enqueue(2, 'b'))
            self.assertFalse(logship_spool.enqueue(3, 'c'))
        after = logship_spool.LOGSHIP_SPOOL_DROPPED._value.get()
        self.assertEqual(1, after - before)


class SpoolOrphanRecoveryTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """Rows from dead-pid spool files are migrated in on startup."""

    def _make_orphan(self, daemon_name, pid, lines):
        path = os.path.join(self.tmp, f'{daemon_name}-{pid}.db')
        s = logship_spool.Spool(path)
        for ts_ns, line in lines:
            s.enqueue(ts_ns, line)
        s.close()
        return path

    def test_orphan_with_dead_pid_is_drained_in(self):
        dead_pid = 99999999
        self.assertFalse(os.path.isdir(f'/proc/{dead_pid}'))
        orphan_path = self._make_orphan(
            'previous-daemon', dead_pid,
            [(1, 'one'), (2, 'two')])

        s = logship_spool.initialise('current-daemon')
        self.assertEqual(2, s.count())
        self.assertFalse(os.path.exists(orphan_path))
        # The migrated lines keep their timestamps.
        batch = s.dequeue_batch(10)
        self.assertEqual(
            [(1, 'one'), (2, 'two')],
            [(ts_ns, line) for _, ts_ns, line in batch])

    def test_orphan_with_live_pid_is_left_alone(self):
        live_pid = os.getpid() + 1
        with mock.patch.object(
                logship_spool, '_pid_is_alive', return_value=True):
            orphan_path = self._make_orphan(
                'sibling-daemon', live_pid, [(9, 'mine')])

            s = logship_spool.initialise('current-daemon')
            self.assertEqual(0, s.count())
            self.assertTrue(os.path.exists(orphan_path))

    def test_orphan_flock_held_elsewhere_is_skipped(self):
        dead_pid = 99999999
        self.assertFalse(os.path.isdir(f'/proc/{dead_pid}'))
        orphan_path = self._make_orphan(
            'previous-daemon', dead_pid, [(1, 'one'), (2, 'two')])

        holder_fd = os.open(orphan_path, os.O_RDONLY)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            s = logship_spool.initialise('current-daemon')
            self.assertEqual(0, s.count())
            self.assertTrue(os.path.exists(orphan_path))
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_orphan_recovery_handles_unparseable_filename(self):
        weird = os.path.join(self.tmp, 'no-hyphen-no-pid.db')
        with open(weird, 'w') as f:
            f.write('not a sqlite database')
        s = logship_spool.initialise('current-daemon')
        self.assertEqual(0, s.count())
        self.assertTrue(os.path.exists(weird))


class SpoolDurabilityTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """A committed enqueue survives reopening the file."""

    def test_enqueue_persists_across_close_reopen(self):
        path = os.path.join(self.tmp, 'durable-test.db')
        s1 = logship_spool.Spool(path)
        s1.enqueue(42, 'should survive')
        s1.close()

        s2 = logship_spool.Spool(path)
        batch = s2.dequeue_batch(10)
        self.assertEqual(1, len(batch))
        self.assertEqual(42, batch[0][1])
        self.assertEqual('should survive', batch[0][2])
        s2.close()


class SpoolCountTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """``Spool.count()`` returns the number of pending rows."""

    def test_empty_spool_count_is_zero(self):
        s = logship_spool.initialise('test-daemon')
        self.assertEqual(0, s.count())

    def test_count_increases_with_each_enqueue(self):
        s = logship_spool.initialise('test-daemon')
        for i in range(5):
            s.enqueue(i, f'line-{i}')
        self.assertEqual(5, s.count())

    def test_count_decreases_after_delete_ids(self):
        s = logship_spool.initialise('test-daemon')
        for i in range(4):
            s.enqueue(i, f'line-{i}')
        batch = s.dequeue_batch(2)
        s.delete_ids([row_id for row_id, _, _ in batch])
        self.assertEqual(2, s.count())


class PidParsingTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """Edge cases on ``_pid_from_spool_path``."""

    def test_simple_daemon_name(self):
        self.assertEqual(
            12345,
            logship_spool._pid_from_spool_path('/x/y/cleaner-12345.db'))

    def test_daemon_name_with_hyphens(self):
        self.assertEqual(
            789,
            logship_spool._pid_from_spool_path('/x/y/sf-api-789.db'))

    def test_unparseable_returns_none(self):
        self.assertIsNone(
            logship_spool._pid_from_spool_path('/x/y/no-pid-here.db'))
        self.assertIsNone(
            logship_spool._pid_from_spool_path('/x/y/nohyphen.db'))
