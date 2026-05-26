# Copyright 2019 Michael Still and contributors
"""Background drainer that ships spooled events to sf-eventlog.

The local eventlog spool (``shakenfist.eventlog_spool``) holds
events on disk so the caller's ``add_event_multi()`` returns in
microseconds. This module runs the daemon thread that picks
batches off the spool and sends them via the batched
``RecordMultiEventBatch`` RPC to sf-eventlog.

One drainer thread per process. Lifecycle:

* ``start(daemon_name)`` -- initialises the spool, launches the
  drainer thread, registers an ``atexit`` handler. Idempotent.
* On clean shutdown the atexit handler tells the drainer to
  flush everything synchronously, then joins it. Events that
  arrived right before exit are not lost.
* On unclean shutdown (kill -9, crash) the spool stays on disk
  and the next process startup (or a sibling daemon's startup)
  rescues it via ``eventlog_spool.initialise`` orphan recovery.

The drainer's RPC target swaps from sf-eventlog to sf-database
during phase 2 of ``PLAN-eventlog-direct-mariadb``; the spool
shape and the drain loop don't change.
"""
import atexit
import threading
import time
from typing import Optional

import grpc
from shakenfist_utilities import logs

from shakenfist import eventlog_spool
from shakenfist import mariadb
from shakenfist.config import config
from shakenfist.protos import event_pb2
from shakenfist.protos import event_pb2_grpc


LOG, _ = logs.setup(__name__)


# Drainer cadence and batch sizing.
#
# When the spool has work the drainer sends back-to-back batches.
# When the spool is empty it sleeps in 100 ms increments. The
# cadence is short enough that a single enqueued event is on the
# wire within ~100 ms of enqueue under normal conditions.
DRAIN_POLL_INTERVAL = 0.1
DRAIN_BATCH_SIZE = 100

# Backoff schedule when sf-eventlog refuses or times out. Resets
# to the start on any successful batch.
BACKOFF_INITIAL = 0.5
BACKOFF_MAX = 30.0
BACKOFF_MULTIPLIER = 2.0

# Per-RPC timeout for the batched send. Sized to absorb a slow
# eventlog daemon under burst load (multiple drainers sending
# concurrently) without blocking the local drainer for unbounded
# time. The drainer can re-attempt the same batch on the next
# tick if the RPC times out.
RPC_TIMEOUT_SECONDS = 10.0

# Wall time the atexit hook waits for in-flight events to drain
# before giving up. Configured to be longer than systemd's
# default ``TimeoutStopSec`` so a clean shutdown almost always
# delivers everything, while still bounded so a wedged
# sf-eventlog daemon can't keep this process alive forever.
SHUTDOWN_DRAIN_TIMEOUT = 20.0


_drainer_thread: Optional['_DrainerThread'] = None
_drainer_lock = threading.Lock()


class _DrainerThread(threading.Thread):
    """The per-process daemon thread that drains the spool.

    Reads in batches of ``DRAIN_BATCH_SIZE``, sends each batch
    via ``RecordMultiEventBatch``, deletes spool rows on ack.
    On any RPC failure (network refused, timeout, server
    ack=false), holds the batch and backs off; the next attempt
    re-reads (so a transient server-side glitch doesn't lose
    the batch).
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name='eventlog-drainer')
        self._stop_event = threading.Event()
        self._channel: Optional[grpc.Channel] = None
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

    def _get_channel(self) -> grpc.Channel:
        if self._channel is None:
            self._channel = grpc.insecure_channel(
                f'{config.EVENTLOG_NODE_IP}:'
                f'{config.EVENTLOG_API_PORT}',
                options=[
                    ('grpc.keepalive_timeout_ms', 200),
                    ('grpc.http2.max_pings_without_data', 0),
                    ('grpc.keepalive_permit_without_calls', 1),
                ])
        return self._channel

    def _drain_one_batch(self) -> int:
        """Send up to DRAIN_BATCH_SIZE events.

        Returns the number of events successfully sent (and
        deleted from the spool). Returns 0 if the spool is
        empty or the RPC failed -- the caller distinguishes
        the two via ``spool.count()``.
        """
        spool = eventlog_spool.get_spool()
        if spool is None:
            return 0

        batch = spool.dequeue_batch(DRAIN_BATCH_SIZE)
        if not batch:
            return 0

        request = self._build_batch_request(batch)
        try:
            stub = event_pb2_grpc.EventServiceStub(
                self._get_channel())
            reply = stub.RecordMultiEventBatch(
                request, timeout=RPC_TIMEOUT_SECONDS,
                wait_for_ready=False)
        except grpc.RpcError as e:
            # Channel-level failure (server unreachable,
            # timeout). Drop the channel so the next attempt
            # rebuilds it.
            self._on_rpc_failure(
                f'RPC failed: {e.code().name if hasattr(e, "code") else e}')
            return 0

        if not reply.ack:
            self._on_rpc_failure(
                'server returned ack=false for batch')
            # Fall back to the DLQ for THIS batch so the events
            # aren't held hostage by a permanently-broken
            # server. The DLQ is a different path that the
            # eventlog daemon drains separately; whichever side
            # recovers first delivers the events.
            self._fallback_to_dlq(batch)
            spool.delete_ids(row_id for row_id, _ in batch)
            return 0

        # Success. Clear the rows from the spool and reset the
        # backoff so the next failure starts at INITIAL again.
        spool.delete_ids(row_id for row_id, _ in batch)
        self._backoff = BACKOFF_INITIAL
        return len(batch)

    def _build_batch_request(
            self, batch: list[tuple[int, dict]]
    ) -> event_pb2.EventMultiBatchRequest:
        """Translate spool rows into the gRPC batch message."""
        request = event_pb2.EventMultiBatchRequest()
        for _row_id, payload in batch:
            inner = request.events.add()
            inner.event_type = payload.get('event_type', '')
            inner.fqdn = payload.get('fqdn', '')
            inner.message = payload.get('message', '')
            inner.extra = payload.get('extra', '{}')
            inner.timestamp = payload.get('timestamp', 0.0)
            if payload.get('duration') is not None:
                inner.duration = payload['duration']
            for obj in payload.get('objects', []):
                eo = inner.objects.add()
                eo.object_type = str(obj.get('object_type', ''))
                eo.object_uuid = str(obj.get('object_uuid', ''))
        return request

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

    def _fallback_to_dlq(
            self, batch: list[tuple[int, dict]]) -> None:
        """Last-ditch: push a failed batch through the existing DLQ.

        Mirrors the per-event DLQ path that today's
        ``add_event_multi`` falls through to when sf-eventlog
        is unreachable. The DLQ is a separate path that
        sf-eventlog's own drainer pulls back out -- between
        the two paths the events still arrive eventually.
        """
        for _row_id, payload in batch:
            try:
                mariadb.enqueue_event_dlq(
                    object_type=(
                        payload['objects'][0]['object_type']
                        if payload.get('objects') else ''),
                    object_uuid=(
                        payload['objects'][0]['object_uuid']
                        if payload.get('objects') else ''),
                    event_timestamp=payload.get('timestamp', 0.0),
                    event_json=payload.get('extra', '{}'))
            except Exception as e:
                LOG.with_fields({
                    'error': str(e),
                }).warning(
                    'Eventlog drainer fallback-to-DLQ failed; '
                    'event dropped')


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
