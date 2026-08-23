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
