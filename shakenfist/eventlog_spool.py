# Copyright 2019 Michael Still and contributors
"""Per-daemon disk-backed spool for the local eventlog client.

Each Shaken Fist daemon process holds its own sqlite spool file
under ``/srv/shakenfist/spool/eventlog/<daemon>-<pid>.db``. The
caller-facing ``eventlog.add_event_multi()`` API enqueues events
into the spool synchronously (one cheap sqlite insert per call,
sub-millisecond) and returns immediately. A background drainer
thread (``shakenfist.eventlog_drainer``) reads batches off the
spool and ships them to ``sf-eventlog`` via the existing gRPC
channel.

The spool is the **durability boundary**. An event that returns
from ``enqueue()`` is on disk and will be delivered eventually,
even if the daemon process crashes immediately afterwards. On
startup the spool module scans for orphan spool files left
behind by previously-dead PIDs and migrates their rows into the
fresh-pid spool so the drainer drains them too.

This module lands in the network-facade branch ahead of the
broader ``PLAN-eventlog-direct-mariadb`` plan; once that plan
executes the drainer will swap its gRPC target from sf-eventlog
to sf-database, but the spool shape itself doesn't change.
"""
import contextlib
import json
import os
import pathlib
import sqlite3
import threading
import time
from typing import Any
from typing import Iterable
from typing import Optional

from shakenfist_utilities import logs


LOG, _ = logs.setup(__name__)


# All spool files live under this root so an operator (or a
# future loki-style log-spool peer) can grow more spool kinds
# under siblings.
SPOOL_ROOT = '/srv/shakenfist/spool/eventlog'

# Maximum spool size in number of pending rows. At ~512 bytes
# per serialised event this is ~50 MiB on disk, which is small
# enough to live on any deployment's root filesystem and large
# enough that a brief sf-eventlog outage does not cause loss.
# When the cap is exceeded ``enqueue()`` drops the incoming
# event (with a counter increment + WARN log) rather than
# blocking the caller -- matching today's "drop if sf-eventlog
# unreachable for >cooldown" posture.
SPOOL_HIGH_WATER_MARK = 100_000

# Module-level singleton spool. The drainer reaches into this
# for batches. Initialised lazily on first ``enqueue()`` or
# ``initialise()`` call -- both are safe to call multiple times
# (idempotent).
_spool: Optional['Spool'] = None
_spool_lock = threading.Lock()

# Counters surfaced via logs for the drop path -- the spool
# does not own a metrics endpoint, the host daemon does.
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
    """A single sqlite-backed event spool.

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
            'CREATE TABLE IF NOT EXISTS events ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'payload BLOB NOT NULL, '
            'created_at REAL NOT NULL)')
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

    def enqueue(self, payload: dict[str, Any]) -> bool:
        """Append an event to the spool.

        Returns True on success. Returns False if the spool is
        over its high-water mark (caller should treat as
        "dropped"). Failures to write -- disk full, permission
        denied -- raise, since they indicate broken local
        state that has to surface.
        """
        blob = json.dumps(payload, default=str).encode('utf-8')
        with self._lock:
            if self._count_locked() >= SPOOL_HIGH_WATER_MARK:
                return False
            self._conn.execute(
                'INSERT INTO events (payload, created_at) VALUES (?, ?)',
                (blob, time.time()))
        return True

    def dequeue_batch(
            self, limit: int) -> list[tuple[int, dict[str, Any]]]:
        """Read up to ``limit`` oldest events without removing them.

        The drainer calls this, hands the payloads to gRPC, and
        on ack calls ``delete_ids()`` with the returned ids. On
        no-ack the rows stay; the next dequeue picks them up
        again.
        """
        with self._lock:
            rows = self._conn.execute(
                'SELECT id, payload FROM events ORDER BY id ASC LIMIT ?',
                (limit,)).fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    def delete_ids(self, ids: Iterable[int]) -> int:
        """Remove the given rows. Returns the affected count."""
        ids = list(ids)
        if not ids:
            return 0
        placeholders = ','.join('?' for _ in ids)
        with self._lock:
            cur = self._conn.execute(
                f'DELETE FROM events WHERE id IN ({placeholders})', ids)
            return cur.rowcount

    def _count_locked(self) -> int:
        """Row count assuming the caller already holds ``self._lock``."""
        cur = self._conn.execute('SELECT COUNT(*) FROM events')
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
        (PIDs that no longer exist). Returns the number of
        rows migrated. The source spool is left intact for the
        caller to delete after a successful migrate.
        """
        moved = 0
        while True:
            batch = other.dequeue_batch(limit=500)
            if not batch:
                break
            with self._lock:
                for _id, payload in batch:
                    # Mint a fresh row -- the source's auto-id is
                    # not stable across spools.
                    blob = json.dumps(payload, default=str).encode(
                        'utf-8')
                    self._conn.execute(
                        'INSERT INTO events (payload, created_at) '
                        'VALUES (?, ?)',
                        (blob, time.time()))
            other.delete_ids(row_id for row_id, _ in batch)
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
    Pids that *are* alive (typically a sibling daemon) are
    left untouched -- that daemon owns those rows.
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
                        'rescued_events': moved,
                    }).info(
                        'Rescued events from orphan eventlog spool')
            except Exception as e:
                LOG.with_fields({
                    'orphan_path': orphan_path,
                    'error': str(e),
                }).warning(
                    'Failed to rescue orphan eventlog spool; '
                    'leaving it in place for the next attempt')

        _spool = spool
        return _spool


def get_spool() -> Optional[Spool]:
    """Return the initialised spool, or ``None`` if uninitialised.

    ``add_event_multi`` calls this; if the spool is not
    initialised yet (a daemon called add_event before
    ``initialise()``) the caller falls back to the direct gRPC
    path so the event still lands.
    """
    return _spool


def enqueue(payload: dict[str, Any]) -> bool:
    """Convenience: enqueue into the singleton spool.

    Returns True on success, False if the spool is over its
    high-water mark or has not been initialised. Callers that
    get False fall back to the direct gRPC path so the event is
    not silently dropped.
    """
    global _dropped_total
    spool = get_spool()
    if spool is None:
        return False
    if spool.enqueue(payload):
        return True
    _dropped_total += 1
    # Log every 100th drop at WARN to avoid flooding under
    # sustained drop conditions, but make sure operators see
    # the first one (and an order-of-magnitude marker
    # thereafter).
    if _dropped_total == 1 or _dropped_total % 100 == 0:
        LOG.with_fields({
            'dropped_total': _dropped_total,
            'spool_path': spool.path,
            'high_water_mark': SPOOL_HIGH_WATER_MARK,
        }).warning(
            'Eventlog spool over high-water mark; dropping event')
    return False


def reset_for_tests() -> None:
    """Tear down the module-level singleton for unit-test isolation."""
    global _spool, _dropped_total
    with _spool_lock:
        if _spool is not None:
            _spool.close()
        _spool = None
        _dropped_total = 0
