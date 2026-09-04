# Copyright 2019 Michael Still and contributors
"""Background drainer that ships spooled log lines to Loki.

The local logship spool (``shakenfist.logship_spool``) holds
formatted JSON log lines on disk so the ``logging.Handler`` in
``shakenfist.logship`` returns in microseconds. This module runs
the daemon thread that picks batches off the spool and POSTs them
to Loki's ``/loki/api/v1/push`` endpoint.

One drainer thread per process. Lifecycle:

* ``start(daemon_name)`` -- initialises the spool, launches the
  drainer thread, registers an ``atexit`` handler. Idempotent.
* On clean shutdown the atexit handler tells the drainer to flush
  everything synchronously, then joins it. Lines that arrived
  right before exit are not lost.
* On unclean shutdown (kill -9, crash) the spool stays on disk and
  the next process startup (or a sibling daemon's startup) rescues
  it via ``logship_spool.initialise`` orphan recovery.

On a transient push failure (timeout, connection error, 429, 5xx)
the batch is left in the spool and retried on the next drain tick;
the spool itself is the durability boundary. A permanent rejection
(any other 4xx, most commonly Loki's ``reject_old_samples``) drops
the batch instead -- retrying it can never succeed, and retaining
it would poison the head of the FIFO spool forever (issue 4054).
Rows older than ``LOKI_MAX_LINE_AGE`` are discarded before the
push for the same reason.

This is a fork of ``eventlog_drainer.py`` with the sink swapped
from ``mariadb.record_event_batch`` to a Loki HTTP POST. The
constants, the ``_DrainerThread`` loop, the backoff and the
``atexit`` drain are all identical; the retry contract differs as
above because HTTP, unlike the eventlog's database sink, can
reject a batch permanently.
"""
import atexit
import threading
import time
from typing import Any
from typing import Optional

import requests
from prometheus_client import Counter
from prometheus_client import Histogram
from shakenfist_utilities import logs

from shakenfist import logship_spool
from shakenfist.config import config


LOG, _ = logs.setup(__name__)


# Drainer cadence and batch sizing.
#
# When the spool has work the drainer sends back-to-back batches.
# When the spool is empty it sleeps in 100 ms increments. The
# cadence is short enough that a single enqueued line is on the
# wire within ~100 ms of enqueue under normal conditions.
DRAIN_POLL_INTERVAL = 0.1
DRAIN_BATCH_SIZE = 100

# Backoff schedule when Loki refuses or times out. Resets to the
# start on any successful batch.
BACKOFF_INITIAL = 0.5
BACKOFF_MAX = 30.0
BACKOFF_MULTIPLIER = 2.0

# Wall time the atexit hook waits for in-flight lines to drain
# before giving up. Configured to be longer than systemd's default
# ``TimeoutStopSec`` so a clean shutdown almost always delivers
# everything, while still bounded so a wedged Loki can't keep this
# process alive forever.
SHUTDOWN_DRAIN_TIMEOUT = 20.0

# How long a single push may block. Short enough that a wedged
# Loki cannot stall the drainer for more than a tick.
PUSH_TIMEOUT = 5.0


# Module-scope Prometheus metrics on the process-wide default
# registry, so they appear on each daemon's existing /metrics with
# no per-daemon bootstrap.
LOGSHIP_PUSH_TOTAL = Counter(
    'logship_push_total',
    'Loki push attempts by result.',
    ['result'])

LOGSHIP_PUSH_SECONDS = Histogram(
    'logship_push_seconds',
    'Wall time spent in a Loki push request.')

LOGSHIP_EXPIRED_LINES = Counter(
    'logship_expired_lines_total',
    'Spooled log lines discarded at drain time because their age '
    'exceeded LOKI_MAX_LINE_AGE.')

LOGSHIP_REJECTED_LINES = Counter(
    'logship_rejected_lines_total',
    'Spooled log lines dropped because Loki permanently rejected '
    'their batch with a non-retryable 4xx status.')


# _push_to_loki result values. SUCCESS deletes the batch; FAILURE
# (transient: exception, timeout, 429, 5xx) retains it for retry;
# REJECTED (any other 4xx) deletes it too, because a permanent
# rejection retried forever wedges the FIFO spool.
PUSH_SUCCESS = 'success'
PUSH_FAILURE = 'failure'
PUSH_REJECTED = 'rejected'


_drainer_thread: Optional['_DrainerThread'] = None
_drainer_lock = threading.Lock()


def _push_to_loki(daemon_name: str, body: dict[str, Any]) -> tuple[str, str]:
    """POST a push body to Loki.

    Returns a ``(result, detail)`` tuple. ``result`` is
    ``PUSH_SUCCESS`` on a 2xx; ``PUSH_FAILURE`` on anything
    retryable (a ``requests`` exception, 429, or 5xx);
    ``PUSH_REJECTED`` on any other 4xx, which is a permanent
    rejection retrying can never fix. ``detail`` is a short
    human-readable cause for the caller's warning log. Never
    raises out of the drainer loop. The ``LOKI_AUTH_HEADER``
    value is opaque and is never logged.
    """
    url = f'{config.LOKI_BASE_URL.rstrip("/")}/loki/api/v1/push'
    headers = {'Content-Type': 'application/json'}
    if config.LOKI_TENANT:
        headers['X-Scope-OrgID'] = config.LOKI_TENANT
    # SecretStr implements __len__, so the emptiness test below still
    # means "not configured"; only the value itself needs unwrapping, at
    # the point it goes on the wire.
    if config.LOKI_AUTH_HEADER:
        headers['Authorization'] = config.LOKI_AUTH_HEADER.get_secret_value()

    try:
        with LOGSHIP_PUSH_SECONDS.time():
            resp = requests.post(
                url, json=body, headers=headers, timeout=PUSH_TIMEOUT)
    except requests.RequestException as e:
        LOGSHIP_PUSH_TOTAL.labels(result='failure').inc()
        # Logged by the caller's backoff path; keep this at debug
        # so a sustained outage does not flood the local log.
        LOG.with_fields({
            'daemon': daemon_name,
            'error': str(e),
            'error_type': type(e).__name__,
        }).debug('Loki push raised a request exception')
        return PUSH_FAILURE, f'{type(e).__name__}: {e}'

    if 200 <= resp.status_code < 300:
        LOGSHIP_PUSH_TOTAL.labels(result='success').inc()
        return PUSH_SUCCESS, ''

    # The response body's first line carries the actual cause (for
    # example Loki's "entry ... has timestamp too old"); it flows
    # into the caller's WARNING via detail so the reason a batch was
    # refused is visible without running the daemon at DEBUG.
    try:
        body_first_line = (resp.text or '').splitlines()[0][:200]
    except Exception:
        body_first_line = ''
    detail = f'HTTP {resp.status_code}: {body_first_line}'

    if resp.status_code == 429 or resp.status_code >= 500:
        LOGSHIP_PUSH_TOTAL.labels(result='failure').inc()
        return PUSH_FAILURE, detail

    LOGSHIP_PUSH_TOTAL.labels(result='rejected').inc()
    return PUSH_REJECTED, detail


class _DrainerThread(threading.Thread):
    """The per-process daemon thread that drains the spool.

    Reads in batches of ``DRAIN_BATCH_SIZE``, ships each batch to
    Loki via ``_push_to_loki``, deletes spool rows on success. On
    a transient failure, leaves the rows in the spool and applies
    exponential backoff; the next drain tick re-reads them. On a
    permanent rejection, deletes the rows anyway so one bad batch
    cannot block the FIFO spool behind it.
    """

    def __init__(self, daemon_name: str) -> None:
        super().__init__(daemon=True, name='logship-drainer')
        self._daemon_name = daemon_name
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
            # Catch-all so an unexpected exception doesn't silently
            # kill the drainer thread and leave the spool growing
            # forever. Log and exit; the atexit hook will then drain
            # synchronously on process exit, and orphan recovery on
            # the next startup picks up whatever's left.
            LOG.with_fields({
                'error': str(e),
                'error_type': type(e).__name__,
            }).error('Logship drainer thread exited unexpectedly')

    def drain_until_empty(
            self, deadline_seconds: float = SHUTDOWN_DRAIN_TIMEOUT
    ) -> int:
        """Block until the spool is empty or the deadline passes.

        Returns the number of lines drained in this call. Called
        from the atexit hook so a clean shutdown leaves nothing
        behind.
        """
        drained = 0
        deadline = time.time() + deadline_seconds
        while time.time() < deadline:
            n = self._drain_one_batch()
            if n == 0:
                spool = logship_spool.get_spool()
                if spool is None or spool.count() == 0:
                    break
                # Spool still has rows but our batch sent zero
                # (push failure). Wait a beat to let the backoff
                # clear, then try again.
                time.sleep(DRAIN_POLL_INTERVAL)
                continue
            drained += n
        return drained

    def _build_push_body(
            self, batch: list[tuple[int, int, str]]) -> dict[str, Any]:
        """Build the Loki push envelope for a batch of spool rows.

        A single stream -- the labels (``{job, daemon, host}``)
        are constant per process, so grouping is a no-op. Loki
        rejects a numeric timestamp (HTTP 400); each timestamp
        must be a nanosecond **string**. Rows come off the spool
        in insertion order, so values are already time-ascending.
        """
        values = [[str(ts_ns), line] for _id, ts_ns, line in batch]
        return {
            'streams': [{
                'stream': {
                    'job': 'shakenfist',
                    'daemon': self._daemon_name,
                    'host': config.NODE_NAME,
                },
                'values': values,
            }],
        }

    def _drain_one_batch(self) -> int:
        """Ship up to DRAIN_BATCH_SIZE lines to Loki.

        Returns the number of lines removed from the spool --
        shipped, expired, or dropped after a permanent rejection.
        Returns 0 if the spool is empty or a transient push
        failure retained the batch -- the caller distinguishes the
        two via ``spool.count()``.
        """
        spool = logship_spool.get_spool()
        if spool is None:
            return 0

        batch = spool.dequeue_batch(DRAIN_BATCH_SIZE)
        if not batch:
            return 0

        # Discard rows Loki would reject as too old before they are
        # pushed. This also heals a spool wedged by an earlier build
        # (or a long outage) on the first drain after startup.
        cutoff_ns = int(
            (time.time() - config.LOKI_MAX_LINE_AGE) * 1_000_000_000)
        expired = [row for row in batch if row[1] < cutoff_ns]
        if expired:
            spool.delete_ids(row_id for row_id, _, _ in expired)
            LOGSHIP_EXPIRED_LINES.inc(len(expired))
            LOG.with_fields({
                'daemon': self._daemon_name,
                'expired_lines': len(expired),
                'max_line_age': config.LOKI_MAX_LINE_AGE,
            }).warning('Discarded spooled log lines older than '
                       'LOKI_MAX_LINE_AGE')
            batch = [row for row in batch if row[1] >= cutoff_ns]
            if not batch:
                return len(expired)

        body = self._build_push_body(batch)
        result, detail = _push_to_loki(self._daemon_name, body)
        if result == PUSH_SUCCESS:
            # Success. Clear the rows from the spool and reset the
            # backoff so the next failure starts at INITIAL.
            spool.delete_ids(row_id for row_id, _, _ in batch)
            self._backoff = BACKOFF_INITIAL
            return len(expired) + len(batch)

        if result == PUSH_REJECTED:
            # Permanent rejection: retrying can never succeed, and
            # retaining the batch would block the FIFO spool behind
            # it forever. Drop it and move on -- on a mixed batch
            # Loki has already ingested the acceptable entries, so
            # a retry would also duplicate those.
            spool.delete_ids(row_id for row_id, _, _ in batch)
            LOGSHIP_REJECTED_LINES.inc(len(batch))
            self._backoff = BACKOFF_INITIAL
            LOG.with_fields({
                'daemon': self._daemon_name,
                'rejected_lines': len(batch),
                'detail': detail,
            }).warning('Loki permanently rejected a log batch; '
                       'dropping it')
            return len(expired) + len(batch)

        # Transient failure. Leave the batch in the spool for retry
        # on the next tick and back off.
        self._on_push_failure(f'Loki push failed: {detail}')
        return len(expired)

    def _on_push_failure(self, reason: str) -> None:
        """Apply exponential backoff and log."""
        sleep_for = self._backoff
        self._backoff = min(
            self._backoff * BACKOFF_MULTIPLIER, BACKOFF_MAX)
        # Logged at warning (not per-retry debug) with backoff
        # context so an operator sees a sustained outage without
        # the local log being flooded by every tick.
        LOG.with_fields({
            'reason': reason,
            'backoff_seconds': sleep_for,
        }).warning('Logship drainer backing off after Loki push failure')
        # Sleep here rather than at the loop top so the next
        # iteration won't immediately re-attempt the same batch.
        # ``_stop_event.wait`` honours stop requests during the
        # sleep so shutdown stays responsive.
        self._stop_event.wait(sleep_for)


def start(daemon_name: str) -> None:
    """Initialise the spool and launch the drainer thread.

    Idempotent. Called once per process from the daemon's startup
    path (typically right after ``daemon.write_pid_file``).
    """
    global _drainer_thread
    with _drainer_lock:
        if _drainer_thread is not None:
            return
        logship_spool.initialise(daemon_name)
        _drainer_thread = _DrainerThread(daemon_name)
        _drainer_thread.start()
        atexit.register(_atexit_drain)


def _atexit_drain() -> None:
    """Drain everything synchronously before process exit.

    Registered by ``start()`` so a clean shutdown leaves the spool
    empty. The drainer thread is signalled to stop, then we
    ourselves call ``drain_until_empty`` so the spool is cleared on
    the exiting thread before the process closes sockets.
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
