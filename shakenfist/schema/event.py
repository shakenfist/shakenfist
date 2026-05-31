# Copyright 2019 Michael Still and contributors
# Pydantic schema for event records destined for the events and
# event_objects tables in MariaDB.
#
# An EventRecord is the in-process Python form of one event log entry
# carried through the three-layer accessor stack
# (_direct_record_event_batch / _grpc_record_event_batch /
# record_event_batch) in shakenfist/mariadb.py. The drainer assembles
# a list[EventRecord] per batch; the direct path writes one row per
# record into the events table and one row per (object_type,
# object_uuid) tuple into the event_objects table inside a single
# transaction so the two tables stay consistent.
#
# Naming note: the generated protobuf class for the wire-level entry
# is called EventBatchEntry (see protos/database.proto). This Python
# model is intentionally named EventRecord to avoid clashing with the
# proto class when both are imported in the same module.

from typing import Optional

from pydantic import BaseModel


class EventRecord(BaseModel):
    """A single event log entry plus the objects it targets.

    Attributes:
        event_uuid: UUID of the event (primary key in the events table).
        event_type: The event type/category string.
        timestamp: Unix timestamp when the event occurred.
        fqdn: Fully-qualified domain name of the node that produced
            the event.
        duration: Optional duration in seconds; None means unset.
        message: Human-readable event message.
        extra: Optional structured payload (free-form per-event
            metadata). None means unset.
        request_id: Optional request identifier for request-scoped
            audit queries. None means unset.
        objects: List of (object_type, object_uuid) tuples naming the
            objects this event targets. May be empty.
    """

    event_uuid: str
    event_type: str
    timestamp: float
    fqdn: str
    duration: Optional[float] = None
    message: str
    extra: Optional[dict] = None
    request_id: Optional[str] = None
    # List of (object_type, object_uuid) tuples
    objects: list[tuple[str, str]]
