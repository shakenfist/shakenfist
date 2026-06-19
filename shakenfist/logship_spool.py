# Copyright 2019 Michael Still and contributors
"""Per-daemon disk-backed spool for the local Loki log shipper.

Each Shaken Fist daemon process holds its own sqlite spool file
under ``/srv/shakenfist/spool/logship/<daemon>-<pid>.db``. The
``logging.Handler`` in ``shakenfist.logship`` enqueues each
formatted log line into the spool synchronously (one cheap sqlite
insert per call, sub-millisecond) and returns immediately. A
background drainer thread (``shakenfist.logship_drainer``) reads
batches off the spool and ships them to Loki via HTTP.

The spool is the **durability boundary**. A log line that returns
from ``enqueue()`` is on disk and will be delivered eventually,
even if the daemon process crashes immediately afterwards. On
startup the spool module scans for orphan spool files left behind
by previously-dead PIDs and migrates their rows into the
fresh-pid spool so the drainer drains them too.

This is a near line-for-line fork of ``eventlog_spool.py``. The
only structural difference is the row schema: the Loki labels
(``{job, daemon, host}``) are constant for a process, so they are
held by the drainer rather than stored per row. Each row is just
``(id, ts_ns, line)``.
"""
import contextlib
import fcntl
import os
import pathlib
import sqlite3
import threading
from typing import Iterable
from typing import Optional

from prometheus_client import Counter
from prometheus_client import Gauge
from shakenfist_utilities import logs


LOG, _ = logs.setup(__name__)


# Module-scope Prometheus metrics. ``prometheus_client`` uses a
# single process-wide default registry, so every daemon that
# imports ``logship.py`` (which transitively imports this module)
# exposes these on its existing metrics endpoint with no
# per-daemon bootstrap.
LOGSHIP_SPOOL_DROPPED = Counter(
    'logship_spool_dropped_total',
    'Log lines dropped at the spool high-water mark.')

LOGSHIP_SPOOL_DEPTH = Gauge(
    'logship_spool_depth',
    'Rows currently pending in the local logship spool.')


# All spool files live under this root so an operator can grow
# more spool kinds under siblings (the eventlog spool lives in a
# sibling ``eventlog`` directory).
SPOOL_ROOT = '/srv/shakenfist/spool/logship'

# Maximum spool size in number of pending rows. When the cap is
# exceeded ``enqueue()`` drops the incoming line (with a counter
# increment + WARN log) rather than blocking the caller -- a log
# handler must never block its call site.
SPOOL_HIGH_WATER_MARK = 100_000

# Module-level singleton spool. The drainer reaches into this for
# batches. Initialised lazily on first ``enqueue()`` or
# ``initialise()`` call -- both are safe to call multiple times
# (idempotent).
_spool: Optional['Spool'] = None
_spool_lock = threading.Lock()

# Local mirror of the drop counter so the "log every 100 drops"
# diagnostic remains independent of the prometheus_client
# Counter's internal representation. The authoritative count for
# operators is ``LOGSHIP_SPOOL_DROPPED``.
_dropped_total = 0


def _spool_path_for(daemon_name: str, pid: int) -> str:
    """Return the sqlite path for a given daemon process."""
    return os.path.join(SPOOL_ROOT, f'{daemon_name}-{pid}.db')


def _all_spool_files() -> list[str]:
    """Every sqlite file currently in the spool directory."""
    root = pathlib.Path(SPOOL_ROOT)
    if not root.exists():
        return []
    return sorted(str(p) for p in root.iterdir() if p.suffix == '.db')


def _pid_from_spool_path(path: str) -> Optional[int]:
    """Extract the pid embedded in a spool filename, or None."""
    name = os.path.basename(path)
    # ``<daemon>-<pid>.db`` -- the daemon name may itself
    # contain hyphens, so split on the last hyphen.
    stem, _ = os.path.splitext(name)
    if '-' not in stem:
        return None
    try:
        return int(stem.rsplit('-', 1)[1])
    except ValueError:
        return None


def _pid_is_alive(pid: int) -> bool:
    """True if a process with this pid is still around.

    Used at startup to decide whether a stale spool file belongs
    to a dead process whose contents we should rescue. Reads
    ``/proc/<pid>`` directly rather than ``psutil`` to avoid
    pulling a heavy dependency into the early-startup path.
    """
    return os.path.isdir(f'/proc/{pid}')


class Spool:
    """A single sqlite-backed log-line spool.

    Thread-safe across concurrent ``enqueue()`` callers and the
    single drainer reader. Python's ``sqlite3.Connection`` is
    not internally thread-safe even with
    ``check_same_thread=False`` -- two threads issuing
    ``execute()`` against the same connection race on the
    underlying cursor state and surface as
    ``sqlite3.ProgrammingError: bad parameter or other API
    misuse`` or as ``fetchone()`` returning ``None`` mid-result
    (``'NoneType' object is not subscriptable``). We serialise
    every connection use behind ``self._lock``. WAL mode and
    NORMAL sync still apply -- they govern on-disk durability
    and reader/writer concurrency at the *database* level; the
    lock only governs in-process *connection* access.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # ``check_same_thread=False`` because the drainer
        # thread reads while caller threads write. We add a
        # Python-side ``self._lock`` ourselves around every
        # connection use -- see the class docstring for why
        # WAL mode alone is not sufficient.
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None,
            timeout=30.0)
        self._conn.execute('PRAGMA journal_mode = WAL')
        # ``synchronous = NORMAL`` is the WAL recommendation for
        # workloads where a small loss-window on hard power-loss
        # is acceptable in exchange for ~10x write throughput
        # vs. FULL. Our durability guarantee is "an enqueue that
        # returned successfully survives a process crash" --
        # power loss is a stronger event the cluster as a whole
        # is not designed to survive without forensic loss.
        self._conn.execute('PRAGMA synchronous = NORMAL')
        self._conn.execute(
            'CREATE TABLE IF NOT EXISTS lines ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'ts_ns INTEGER NOT NULL, '
            'line TEXT NOT NULL)')
        self._conn.execute(
            'CREATE TABLE IF NOT EXISTS schema_meta ('
            'key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self._conn.execute(
            'INSERT OR IGNORE INTO schema_meta (key, value) '
            'VALUES (?, ?)',
            ('version', str(self.SCHEMA_VERSION)))

    def close(self) -> None:
        with contextlib.suppress(Exception):
            with self._lock:
                self._conn.close()

    def enqueue(self, ts_ns: int, line: str) -> bool:
        """Append a log line to the spool.

        Returns True on success. Returns False if the spool is
        over its high-water mark (caller should treat as
        "dropped"). Failures to write -- disk full, permission
        denied -- raise, since they indicate broken local state
        that has to surface.
        """
        with self._lock:
            if self._count_locked() >= SPOOL_HIGH_WATER_MARK:
                return False
            self._conn.execute(
                'INSERT INTO lines (ts_ns, line) VALUES (?, ?)',
                (ts_ns, line))
        return True

    def dequeue_batch(self, limit: int) -> list[tuple[int, int, str]]:
        """Read up to ``limit`` oldest rows without removing them.

        Returns a list of ``(id, ts_ns, line)`` tuples in
        insertion (time-ascending) order. The drainer calls this,
        ships the lines to Loki, and on success calls
        ``delete_ids()`` with the returned ids. On failure the
        rows stay; the next dequeue picks them up again.
        """
        with self._lock:
            rows = self._conn.execute(
                'SELECT id, ts_ns, line FROM lines ORDER BY id ASC LIMIT ?',
                (limit,)).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def delete_ids(self, ids: Iterable[int]) -> int:
        """Remove the given rows. Returns the affected count."""
        ids = list(ids)
        if not ids:
            return 0
        placeholders = ','.join('?' for _ in ids)
        with self._lock:
            cur = self._conn.execute(
                f'DELETE FROM lines WHERE id IN ({placeholders})', ids)
            return cur.rowcount

    def _count_locked(self) -> int:
        """Row count assuming the caller already holds ``self._lock``."""
        cur = self._conn.execute('SELECT COUNT(*) FROM lines')
        return int(cur.fetchone()[0])

    def _count(self) -> int:
        with self._lock:
            return self._count_locked()

    def count(self) -> int:
        """Public count for tests and metrics."""
        return self._count()

    def migrate_in(self, other: 'Spool') -> int:
        """Copy every row from ``other`` into this spool.

        Used at startup to rescue rows from orphan spool files
        (PIDs that no longer exist). Returns the number of rows
        migrated. The source spool is left intact for the caller
        to delete after a successful migrate.
        """
        moved = 0
        while True:
            batch = other.dequeue_batch(limit=500)
            if not batch:
                break
            with self._lock:
                for _id, ts_ns, line in batch:
                    # Mint a fresh row -- the source's auto-id is
                    # not stable across spools.
                    self._conn.execute(
                        'INSERT INTO lines (ts_ns, line) VALUES (?, ?)',
                        (ts_ns, line))
            other.delete_ids(row_id for row_id, _, _ in batch)
            moved += len(batch)
        return moved


def initialise(daemon_name: str) -> Spool:
    """Open this process's spool and absorb any orphan spools.

    Safe to call multiple times; subsequent calls return the
    same Spool instance. ``daemon_name`` should match the value
    passed to ``daemon.write_pid_file()`` so spool filenames
    correlate cleanly with the rest of the systemd / pid file
    layout.

    Orphan recovery: any sqlite file under ``SPOOL_ROOT`` whose
    embedded pid is no longer alive is opened, its rows are
    migrated into our spool, and the orphan file is deleted.
    Pids that *are* alive (typically a sibling daemon) are left
    untouched -- that daemon owns those rows.
    """
    global _spool
    with _spool_lock:
        if _spool is not None:
            return _spool

        my_pid = os.getpid()
        my_path = _spool_path_for(daemon_name, my_pid)
        spool = Spool(my_path)

        for orphan_path in _all_spool_files():
            if orphan_path == my_path:
                continue
            orphan_pid = _pid_from_spool_path(orphan_path)
            if orphan_pid is None:
                continue
            if _pid_is_alive(orphan_pid):
                continue

            # Take an exclusive flock on the orphan file before
            # we touch it. Multiple sibling workers racing to
            # recover the same orphan (e.g., five gunicorn
            # workers starting together) would otherwise each
            # read the same rows via ``dequeue_batch`` before
            # any of them got to ``delete_ids`` and produce
            # N-way duplicate lines downstream. Whoever wins the
            # lock owns the migration; the rest skip.
            #
            # ``fcntl.flock`` is BSD-style and does not collide
            # with sqlite's POSIX byte-range locks on the same
            # file, so the ``Spool(orphan_path)`` open below
            # still works inside the held lock. The kernel
            # releases the lock when the process exits, so a
            # crashed recoverer does not strand the orphan.
            try:
                lock_fd = os.open(orphan_path, os.O_RDONLY)
            except FileNotFoundError:
                # Another worker recovered and unlinked it
                # between our directory scan and now.
                continue
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(lock_fd)
                continue

            try:
                orphan = Spool(orphan_path)
                moved = spool.migrate_in(orphan)
                orphan.close()
                os.unlink(orphan_path)
                # The -wal/-shm sidecars are recreated on next
                # connect; cleaning them up is a courtesy to
                # operators inspecting the spool dir.
                for suffix in ('-wal', '-shm'):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(orphan_path + suffix)
                if moved:
                    LOG.with_fields({
                        'orphan_pid': orphan_pid,
                        'orphan_path': orphan_path,
                        'rescued_lines': moved,
                    }).info(
                        'Rescued log lines from orphan logship spool')
            except Exception as e:
                LOG.with_fields({
                    'orphan_path': orphan_path,
                    'error': str(e),
                }).warning(
                    'Failed to rescue orphan logship spool; '
                    'leaving it in place for the next attempt')
            finally:
                with contextlib.suppress(OSError):
                    os.close(lock_fd)

        _spool = spool
        return _spool


def get_spool() -> Optional[Spool]:
    """Return the initialised spool, or ``None`` if uninitialised."""
    return _spool


def enqueue(ts_ns: int, line: str) -> bool:
    """Convenience: enqueue into the singleton spool.

    Returns True on success, False if the spool is over its
    high-water mark or has not been initialised. The Loki
    handler ignores the return value (a dropped line is gone),
    but the high-water-mark drop is still counted and logged.
    """
    global _dropped_total
    spool = get_spool()
    if spool is None:
        return False
    if spool.enqueue(ts_ns, line):
        return True
    LOGSHIP_SPOOL_DROPPED.inc()
    _dropped_total += 1
    # Log every 100th drop at WARN to avoid flooding under
    # sustained drop conditions, but make sure operators see the
    # first one (and an order-of-magnitude marker thereafter).
    if _dropped_total == 1 or _dropped_total % 100 == 0:
        LOG.with_fields({
            'dropped_total': _dropped_total,
            'spool_path': spool.path,
            'high_water_mark': SPOOL_HIGH_WATER_MARK,
        }).warning(
            'Logship spool over high-water mark; dropping log line')
    return False


def _sample_depth() -> int:
    """Prometheus scrape callback for ``LOGSHIP_SPOOL_DEPTH``.

    Sampled on each ``/metrics`` scrape rather than tracked
    incrementally so we avoid races between enqueue and dequeue
    paths and pay the ``SELECT COUNT(*)`` cost at most once per
    scrape interval. Returns 0 if the spool is uninitialised or
    if the underlying sqlite query raises transiently -- a
    metrics endpoint that breaks on a sampler error is worse than
    one that briefly reports zero.
    """
    spool = get_spool()
    if spool is None:
        return 0
    try:
        return spool.count()
    except Exception:
        return 0


LOGSHIP_SPOOL_DEPTH.set_function(_sample_depth)


def reset_for_tests() -> None:
    """Tear down the module-level singleton for unit-test isolation."""
    global _spool, _dropped_total
    with _spool_lock:
        if _spool is not None:
            _spool.close()
        _spool = None
        _dropped_total = 0
