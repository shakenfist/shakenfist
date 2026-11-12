# Pydantic schema for AgentOperation mutable attributes in MariaDB.
#
# This schema defines the structure for storing AgentOperation mutable
# attributes. These are values that change during the lifetime of an
# AgentOperation (results from executed commands).
#
# This model serves as both:
# 1. The source of truth for the agent_operation_attributes table
#    schema
# 2. A typed data transfer object for AgentOperation attributes

from typing import Annotated
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class AgentOperationAttributesData(BaseModel):
    """Schema for AgentOperation mutable attributes in MariaDB.

    Table: agent_operation_attributes
    Primary key: uuid

    Attributes:
        uuid: The AgentOperation's unique identifier (FK to
            agent_operations).
        results: Dict mapping command index (as string) to result
            dict. Stored as JSON.
        last_progress: Unix timestamp of the most recent observed
            forward progress. NULL means no progress has been
            observed yet.
        attempts: How many times this operation has been dispatched,
            for the retry bound. Never NULL.
        expiry_reason: Which timing budget expired the operation --
            'deadline' or 'progress'. NULL unless the operation is in
            the expired state.
        dependency_failure: The op_type, op_uuid and state of the
            failed dependency which aborted this operation. NULL
            unless that happened.
        dependency_wait_started: Unix timestamp of the first
            dispatcher pass which found this operation blocked on a
            dependency. NULL for an operation which was never
            blocked.
    """

    model_config = ConfigDict(frozen=False)  # Mutable

    # The AgentOperation's UUID - primary key
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Results from executed commands (variable-length, stored as JSON)
    # Keys are string indices ("0", "1", ...), values are result dicts
    results: dict = {}

    # Unix timestamp of the most recent observed forward progress.
    # NULL means none has been observed yet. Written by the executor's
    # progress hooks, and read by the node-local reaper so it can tell
    # a stalled transfer from a slow but healthy one.
    last_progress: Optional[float] = None

    # How many times this operation has been dispatched. Not nullable
    # -- an attempt count has no "unknown" state worth representing,
    # so a reader never has to write "attempts or 0".
    attempts: int = 0

    # Which timing budget expired the operation: one of
    # AgentOperation.EXPIRY_REASONS ('deadline' or 'progress'). NULL
    # unless the operation is in the expired state. Written once by
    # AgentOperation.expire() and surfaced on the external view so a
    # client can branch on it without parsing the state message's
    # prose (issue #4075).
    expiry_reason: Optional[str] = None

    # The op_type, op_uuid and state of the depends_on edge whose failure
    # cascaded this operation to abort, so a client can tell which of
    # several dependencies went wrong without walking them all. NULL means
    # no cascade reached this operation, which is the ordinary case; an
    # empty dict would instead mean a cascade which recorded no detail, so
    # the two are not interchangeable.
    #
    # The column lands with the rest of the dependency schema, but nothing
    # writes it yet -- the cascade which does is the next piece of work --
    # and it is deliberately absent from the external view until then,
    # because a field which can only ever be null is a field a client
    # learns to ignore.
    dependency_failure: Optional[dict] = None

    # Wall-clock time of the first dispatcher pass which found this
    # operation blocked on a dependency, and so the anchor the
    # dependency wait budget is measured from. Written once and never
    # updated: a second write would move the anchor forward every time
    # the operation was looked at, and a budget which restarts whenever
    # it is checked is not a budget at all. NULL for every operation
    # which was never blocked, which is the ordinary case.
    #
    # Deliberately absent from the external view. A caller needs the
    # outcome of a wait -- whether the operation ran or was aborted,
    # and why -- not the instant the server started counting, which it
    # cannot act on and which would invite clients to re-derive the
    # budget for themselves.
    dependency_wait_started: Optional[float] = None
