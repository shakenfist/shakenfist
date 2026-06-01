import copy
import threading
import time
import uuid
from typing import Any
from typing import Optional
from typing import Union

import flask
import grpc
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as sf_random  # noreorder

from shakenfist import eventlog_spool
from shakenfist import mariadb
from shakenfist.config import config
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)


# This module stores some state in thread local storage.
local = threading.local()
local.sf_eventlog_client = None

# Thread-local flag to force events directly to the dead letter queue (etcd).
# This is used during daemon startup to avoid circular dependencies - for example
# the database daemon needs to record events during startup but the eventlog
# daemon isn't running yet.
local.sf_force_event_dlq = False

# Module-level state for tracking eventlog service availability. When the service
# is unavailable, we skip gRPC attempts and go directly to the dead letter queue
# for a cooldown period to avoid slow retries on every event.
_eventlog_unavailable_until: float = 0
_eventlog_unavailable_lock = threading.Lock()
EVENTLOG_UNAVAILABLE_COOLDOWN = 60  # seconds


def set_force_event_dlq(value: bool) -> None:
    """Force events to go directly to the dead letter queue (MariaDB).

    This is used during daemon startup when the eventlog daemon may not be
    available yet. Events will be queued in the ``event_dlq`` table and
    processed later.
    """
    local.sf_force_event_dlq = value


def get_force_event_dlq() -> bool:
    """Check if events should be forced to the dead letter queue."""
    return getattr(local, 'sf_force_event_dlq', False)


def _mark_eventlog_unavailable() -> None:
    """Mark the eventlog service as unavailable for a cooldown period."""
    global _eventlog_unavailable_until
    with _eventlog_unavailable_lock:
        _eventlog_unavailable_until = time.time() + EVENTLOG_UNAVAILABLE_COOLDOWN


def _is_eventlog_available() -> bool:
    """Check if the eventlog service should be considered available.

    Returns True if we should try gRPC, False if we should skip to DLQ.
    """
    with _eventlog_unavailable_lock:
        if time.time() < _eventlog_unavailable_until:
            return False
        return True


def get_eventlog_client() -> grpc.Channel:
    c = getattr(local, 'sf_eventlog_client', None)
    if c:
        # Ensure the channel is ready
        try:
            grpc.channel_ready_future(c).result(timeout=0.5)
        except grpc.FutureTimeoutError:
            # We do not close the channel here because this cause grpc to sometimes
            # throw a traceback from another thread trying to monitor a now closed
            # channel.
            c = None

    if not c:
        local.sf_eventlog_client = grpc.insecure_channel(
            f'{config.EVENTLOG_NODE_IP}:{config.EVENTLOG_API_PORT}',
            options=[
                ('grpc.keepalive_timeout_ms', 200),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.keepalive_permit_without_calls', 1),
            ]
        )
        c = local.sf_eventlog_client
    return c


def add_event(
        event_type: str,
        object_type: str,
        object_uuid: Union[str, uuid.UUID],
        message: str,
        duration: Optional[float] = None,
        extra: Optional[dict[str, Any]] = None,
        suppress_event_logging: bool = False,
        log_as_error: bool = False
) -> None:
    add_event_multi(
        event_type, [(object_type, object_uuid)], message, duration=duration,
        extra=extra, suppress_event_logging=suppress_event_logging,
        log_as_error=log_as_error)


def _add_event_dlq_inner(
        event_type: str,
        log: Any,
        timestamp: float,
        simpler_objects: list[tuple[str, Union[str, uuid.UUID]]],
        message: str,
        duration: Optional[float] = None,
        extra: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None
) -> None:
    for object_type, object_uuid in simpler_objects:
        mariadb.enqueue_event_dlq(
            object_type=str(object_type),
            object_uuid=str(object_uuid),
            event_timestamp=timestamp,
            event_json={
                'timestamp': timestamp,
                'event_type': event_type,
                'object_type': str(object_type),
                'object_uuid': str(object_uuid),
                'fqdn': config.NODE_NAME,
                'duration': duration,
                'message': message,
                'extra': extra,
                'correlation_id': correlation_id,
            },
        )


def add_event_multi(
        event_type: str,
        objects: list[Any],
        message: str,
        duration: Optional[float] = None,
        extra: Optional[dict[str, Any]] = None,
        suppress_event_logging: bool = False,
        log_as_error: bool = False
) -> None:
    # Queue an event in etcd to get shuffled over to the long term data store
    timestamp = time.time()

    if not objects:
        return

    # Flatten objects down to a single data type, whilst also not recording
    # events for in memory only artifacts.
    simpler_objects = []
    for obj in objects:
        if isinstance(obj, tuple):
            simpler_objects.append(obj)
            continue

        if obj.in_memory_only:
            continue
        simpler_objects.append((obj.object_type, obj.uuid))

    # If we alter extra, we don't want that to leak back to the caller.
    if not extra:
        extra = {}
    else:
        extra = copy.deepcopy(extra)

    # Always generate an event_uuid: this is the per-event identity that
    # makes RecordEventBatch retries idempotent against the events table's
    # primary key. The legacy gRPC and DLQ paths still want a
    # correlation_id alias, so we keep that name pointing at the same
    # value for compatibility until phase 5 deletes those paths.
    event_uuid = sf_random.random_id()
    correlation_id = event_uuid

    # If this event was created in the context of a request from our API, then
    # we should record the request id that caused this event. The request id
    # used to live inside ``extra`` under the ``request-id`` key; it is now a
    # first-class top-level field on the spool payload.
    try:
        request_id = flask.request.environ.get('FLASK_REQUEST_ID')
    except RuntimeError:
        request_id = None

    log = LOG.with_fields({
        'event_type': event_type,
        'fqdn': config.NODE_NAME,
        'duration': duration,
        'message': message,
        'extra': extra
    })
    for object_type, object_uuid in simpler_objects:
        log = log.with_fields({object_type: object_uuid})

    if not suppress_event_logging:
        if log_as_error:
            log.error('Added event')
        else:
            log.info('Added event')

    # Fast path: enqueue into the local per-daemon spool. The
    # background drainer thread (``shakenfist.eventlog_drainer``)
    # picks the event up, batches it with peers, and ships the
    # batch via ``RecordMultiEventBatch``. The spool's
    # ``enqueue()`` returns in microseconds (single sqlite
    # insert), so the caller doesn't pay the per-event RPC cost
    # anymore -- which was the largest remaining contributor to
    # cluster-operation wrapper time in CI profiling.
    #
    # The spool returns False in two cases: (a) uninitialised
    # (this process never called ``eventlog_drainer.start()`` --
    # typical for sf-ctl, unit tests, or anything that imports
    # this module without the daemon scaffolding) or (b) over
    # its high-water mark. Either way we fall through to the
    # legacy direct-gRPC + DLQ path so the event still lands.
    if (not get_force_event_dlq()
            and not config.EVENTLOG_SUPPRESS_GRPC):
        payload = {
            'event_uuid': event_uuid,
            'event_type': event_type,
            'fqdn': config.NODE_NAME,
            'duration': duration,
            'message': message,
            'extra': util_json.json_dump(extra),
            'request_id': request_id,
            'timestamp': timestamp,
            'objects': [
                {'object_type': str(ot), 'object_uuid': str(ou)}
                for ot, ou in simpler_objects
            ],
        }
        if eventlog_spool.enqueue(payload):
            return

    # Spool init failed or hit its high-water mark; fall through to the
    # MariaDB dead-letter queue. The legacy direct-gRPC path to sf-eventlog
    # is gone in phase 5 -- the DLQ itself, the cooldown cache, and this
    # whole DLQ fallback are scheduled for deletion in step 5c.
    _add_event_dlq_inner(
        event_type, log, timestamp, simpler_objects, message,
        duration=duration, extra=extra, correlation_id=correlation_id)
