# Copyright 2019 Michael Still and contributors
"""Background drainer that ships spooled events to sf-database.

The local eventlog spool (``shakenfist.eventlog_spool``) holds
events on disk so the caller's ``add_event_multi()`` returns in
microseconds. This module runs the daemon thread that picks
batches off the spool and writes them via
``mariadb.record_event_batch`` -- which transparently routes
through the direct MariaDB path on sf-database itself and via
the database gRPC channel on every other daemon.

One drainer thread per process. Lifecycle:

* ``start(daemon_name)`` -- initialises the spool, launches the
  drainer thread, registers an ``atexit`` handler. Idempotent.
* On clean shutdown the atexit handler tells the drainer to
  flush everything synchronously, then joins it. Events that
  arrived right before exit are not lost.
* On unclean shutdown (kill -9, crash) the spool stays on disk
  and the next process startup (or a sibling daemon's startup)
  rescues it via ``eventlog_spool.initialise`` orphan recovery.

On RPC failure the batch is left in the spool and retried on the
next drain tick; the spool itself is the durability boundary.
"""
import atexit
import json
import threading
import time
from typing import Optional

from shakenfist_utilities import logs
from shakenfist_utilities import random as sf_random

from shakenfist import eventlog_spool
from shakenfist import mariadb
from shakenfist.schema.event import EventRecord


LOG, _ = logs.setup(__name__)


# Drainer cadence and batch sizing.
#
# When the spool has work the drainer sends back-to-back batches.
# When the spool is empty it sleeps in 100 ms increments. The
# cadence is short enough that a single enqueued event is on the
# wire within ~100 ms of enqueue under normal conditions.
DRAIN_POLL_INTERVAL = 0.1
DRAIN_BATCH_SIZE = 100

# Backoff schedule when sf-database refuses or times out. Resets
# to the start on any successful batch.
BACKOFF_INITIAL = 0.5
BACKOFF_MAX = 30.0
BACKOFF_MULTIPLIER = 2.0

# Wall time the atexit hook waits for in-flight events to drain
# before giving up. Configured to be longer than systemd's
# default ``TimeoutStopSec`` so a clean shutdown almost always
# delivers everything, while still bounded so a wedged
# sf-database daemon can't keep this process alive forever.
SHUTDOWN_DRAIN_TIMEOUT = 20.0


_drainer_thread: Optional['_DrainerThread'] = None
_drainer_lock = threading.Lock()


def _build_single_record(payload: dict) -> EventRecord:
    """Translate one spool payload dict into an ``EventRecord``.

    May raise on a malformed payload; the caller in ``_build_records``
    isolates the failure to a single spool row.
    """
    extra_raw = payload.get('extra')
    extra_dict: Optional[dict] = None
    if extra_raw:
        try:
            extra_dict = json.loads(extra_raw)
        except (TypeError, ValueError):
            extra_dict = None

    request_id = payload.get('request_id')
    if not request_id and extra_dict is not None:
        request_id = extra_dict.get('request-id') or None

    objects: list[tuple[str, str]] = []
    for obj in payload.get('objects', []) or []:
        if isinstance(obj, dict):
            objects.append(
                (str(obj.get('object_type', '')),
                 str(obj.get('object_uuid', ''))))
        else:
            objects.append((str(obj[0]), str(obj[1])))

    return EventRecord(
        event_uuid=(payload.get('event_uuid')
                    or sf_random.random_id()),
        event_type=payload.get('event_type', ''),
        timestamp=payload.get('timestamp', 0.0),
        fqdn=payload.get('fqdn', ''),
        duration=payload.get('duration') or None,
        message=payload.get('message', ''),
        extra=extra_dict,
        request_id=request_id,
        objects=objects,
    )


def _build_records(
        batch: list[tuple[int, dict]]
) -> tuple[list[EventRecord], list[int], list[int]]:
    """Translate spool rows into ``EventRecord`` instances.

    Returns a triple ``(records, good_ids, poison_ids)``:

    * ``records`` is the list of successfully translated EventRecords,
      aligned with ``good_ids`` -- ``records[i]`` came from spool row
      ``good_ids[i]``.
    * ``poison_ids`` is the list of spool ids whose payload failed to
      translate (KeyError, ValueError parsing extra, pydantic
      validation failure, etc.). The caller drops these from the spool
      immediately; holding them forever would block every later event
      behind the same poison row, but dropping the whole batch on the
      first bad row would punish the healthy events. Per-row
      granularity preserves the rest of the batch.

    Handles the upgrade case where in-flight rows may have been
    written before the phase 2a payload-shape change:

    * ``event_uuid`` may be missing -- fall back to a fresh UUID
      (acceptable for the single drain cycle's worth of legacy
      rows; the events table PK still protects against duplicate
      inserts if a retry races).
    * ``request_id`` may live as ``extra['request-id']`` rather
      than as a top-level key -- read both, prefer the top-level.
    * ``objects`` may be a list of ``{'object_type': ...,
      'object_uuid': ...}`` dicts (current shape) or a list of
      ``(object_type, object_uuid)`` tuples (older shape).
    """
    records: list[EventRecord] = []
    good_ids: list[int] = []
    poison_ids: list[int] = []
    for row_id, payload in batch:
        try:
            record = _build_single_record(payload)
        except Exception as e:
            LOG.with_fields({
                'error': str(e),
                'error_type': type(e).__name__,
                'spool_row_id': row_id,
            }).error('Eventlog drainer dropping poison spool row')
            poison_ids.append(row_id)
            continue
        records.append(record)
        good_ids.append(row_id)
    return records, good_ids, poison_ids


class _DrainerThread(threading.Thread):
    """The per-process daemon thread that drains the spool.

    Reads in batches of ``DRAIN_BATCH_SIZE``, writes each batch
    via ``mariadb.record_event_batch``, deletes spool rows on
    success. On failure, leaves the rows in the spool and applies
    exponential backoff; the next drain tick re-reads them.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name='eventlog-drainer')
        self._stop_event = threading.Event()
        self._backoff = BACKOFF_INITIAL

    def stop(self) -> None:
        """Ask the drainer to exit at its next wake-up."""
        self._stop_event.set()

    def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                drained = self._drain_one_batch()
                if drained:
                    # More work might be ready; loop hot.
                    continue
                self._stop_event.wait(DRAIN_POLL_INTERVAL)
        except Exception as e:
            # Catch-all so an unexpected exception doesn't
            # silently kill the drainer thread and leave the
            # spool growing forever. Log and exit; the atexit
            # hook will then drain synchronously on process
            # exit, and orphan recovery on the next startup
            # picks up whatever's left.
            LOG.with_fields({
                'error': str(e),
                'error_type': type(e).__name__,
            }).error('Eventlog drainer thread exited unexpectedly')

    def drain_until_empty(
            self, deadline_seconds: float = SHUTDOWN_DRAIN_TIMEOUT
    ) -> int:
        """Block until the spool is empty or the deadline passes.

        Returns the number of events drained in this call. Called
        from the atexit hook so a clean shutdown leaves nothing
        behind.
        """
        drained = 0
        deadline = time.time() + deadline_seconds
        while time.time() < deadline:
            n = self._drain_one_batch()
            if n == 0:
                spool = eventlog_spool.get_spool()
                if spool is None or spool.count() == 0:
                    break
                # Spool still has rows but our batch sent zero
                # (RPC failure). Wait a beat to let the
                # backoff clear, then try again.
                time.sleep(DRAIN_POLL_INTERVAL)
                continue
            drained += n
        return drained

    def _drain_one_batch(self) -> int:
        """Write up to DRAIN_BATCH_SIZE events.

        Returns the number of events successfully written (and
        deleted from the spool). Returns 0 if the spool is
        empty or the write failed -- the caller distinguishes
        the two via ``spool.count()``.
        """
        spool = eventlog_spool.get_spool()
        if spool is None:
            return 0

        batch = spool.dequeue_batch(DRAIN_BATCH_SIZE)
        if not batch:
            return 0

        records, good_ids, poison_ids = _build_records(batch)
        if poison_ids:
            # Drop the poison rows from the spool now so a single bad
            # payload can't wedge the drainer forever. Healthy
            # siblings in the same batch continue through the normal
            # RPC path below.
            spool.delete_ids(poison_ids)

        if not records:
            return 0

        if mariadb.record_event_batch(records):
            # Success. Clear the good rows from the spool and reset
            # the backoff so the next failure starts at INITIAL.
            spool.delete_ids(good_ids)
            self._backoff = BACKOFF_INITIAL
            return len(records)

        # Failure. Leave the batch in the spool for retry on the
        # next tick and back off.
        self._on_rpc_failure('record_event_batch returned False')
        return 0

    def _on_rpc_failure(self, reason: str) -> None:
        """Apply exponential backoff and log."""
        sleep_for = self._backoff
        self._backoff = min(
            self._backoff * BACKOFF_MULTIPLIER, BACKOFF_MAX)
        LOG.with_fields({
            'reason': reason,
            'backoff_seconds': sleep_for,
        }).debug('Eventlog drainer backoff')
        # Sleep here rather than at the loop top so the next
        # iteration won't immediately re-attempt the same
        # batch. ``_stop_event.wait`` honours stop requests
        # during the sleep so shutdown stays responsive.
        self._stop_event.wait(sleep_for)


def start(daemon_name: str) -> None:
    """Initialise the spool and launch the drainer thread.

    Idempotent. Called once per process from the daemon's
    startup path (typically right after ``daemon.write_pid_file``).
    """
    global _drainer_thread
    with _drainer_lock:
        if _drainer_thread is not None:
            return
        eventlog_spool.initialise(daemon_name)
        _drainer_thread = _DrainerThread()
        _drainer_thread.start()
        atexit.register(_atexit_drain)


def _atexit_drain() -> None:
    """Drain everything synchronously before process exit.

    Registered by ``start()`` so a clean shutdown leaves the
    spool empty. The drainer thread is signalled to stop, then
    we ourselves call ``drain_until_empty`` so the spool is
    cleared on the exiting thread before the process closes
    sockets.
    """
    with _drainer_lock:
        if _drainer_thread is None:
            return
        _drainer_thread.stop()
        _drainer_thread.drain_until_empty()


def stop_for_tests() -> None:
    """Tear down the singleton drainer for unit-test isolation."""
    global _drainer_thread
    with _drainer_lock:
        if _drainer_thread is not None:
            _drainer_thread.stop()
            _drainer_thread.join(timeout=5)
            _drainer_thread = None
