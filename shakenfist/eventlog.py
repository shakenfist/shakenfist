import copy
import time
import uuid
from typing import Any
from typing import Optional
from typing import Union

import flask
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as sf_random  # noreorder

from shakenfist import eventlog_spool
from shakenfist.config import config
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)


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


def add_event_multi(
        event_type: str,
        objects: list[Any],
        message: str,
        duration: Optional[float] = None,
        extra: Optional[dict[str, Any]] = None,
        suppress_event_logging: bool = False,
        log_as_error: bool = False
) -> None:
    if not objects:
        return

    # Flatten objects down to a single data type, whilst also not recording
    # events for in memory only artifacts. The object uuid is normalised to a
    # str here: callers may pass a uuid.UUID (add_event is typed to accept
    # one, and obj.uuid is a UUID), and it flows both into the 'Added event'
    # log fields below and into the spool payload. A raw UUID is not JSON
    # serializable, so leaving it unconverted makes the log shipper's JSON
    # formatter raise mid-emit and drop the record. Stringify at the source so
    # every consumer gets a serializable value.
    simpler_objects = []
    for obj in objects:
        if isinstance(obj, tuple):
            object_type, object_uuid = obj
            simpler_objects.append((object_type, str(object_uuid)))
            continue

        if obj.in_memory_only:
            continue
        simpler_objects.append((obj.object_type, str(obj.uuid)))

    # If we alter extra, we don't want that to leak back to the caller.
    if not extra:
        extra = {}
    else:
        extra = copy.deepcopy(extra)

    # Per-event identity for idempotent RecordEventBatch retries against the
    # events table's primary key.
    event_uuid = sf_random.random_id()

    # If this event was created in the context of a request from our API, then
    # we should record the request id that caused this event.
    try:
        request_id = flask.request.environ.get('FLASK_REQUEST_ID')
    except RuntimeError:
        request_id = None

    # The 'Added event' diagnostic line is what flows to the log stream
    # (Loki). It is gated by LOG_EVENTS_TO_LOKI so an operator can mute
    # the event echo, and by suppress_event_logging so high-volume
    # callers (billing statistics, object creation) can mute their own
    # echo per-event. Neither gate affects the authoritative MariaDB
    # write below. The 'fqdn' field is intentionally omitted here -- it
    # would duplicate the host label on the log stream -- but is kept in
    # the MariaDB payload below, which is the authoritative record.
    if config.LOG_EVENTS_TO_LOKI and not suppress_event_logging:
        log = LOG.with_fields({
            'event_type': event_type,
            'duration': duration,
            'message': message,
            'extra': extra
        })
        for object_type, object_uuid in simpler_objects:
            log = log.with_fields({object_type: object_uuid})

        if log_as_error:
            log.error('Added event')
        else:
            log.info('Added event')

    # Enqueue into the local per-daemon spool. The background drainer thread
    # (``shakenfist.eventlog_drainer``) picks the event up, batches it with
    # peers, and ships the batch via ``RecordEventBatch``. The spool is the
    # durability boundary: on spool-full or spool-uninitialised, the
    # ``EVENTLOG_SPOOL_DROPPED`` counter inside ``eventlog_spool``
    # increments and the event is dropped. No DLQ fallback exists any more.
    timestamp = time.time()
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
    eventlog_spool.enqueue(payload)
