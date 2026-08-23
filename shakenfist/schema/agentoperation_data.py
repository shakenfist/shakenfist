# Pydantic schema for AgentOperation object storage in MariaDB.
#
# This schema defines the structure for storing AgentOperation static
# values. AgentOperations represent in-guest agent tasks (execute,
# get-file, put-blob, chmod) queued against an Instance.
#
# This model serves as both:
# 1. The source of truth for the agent_operations table schema (used
#    by pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for AgentOperation static values

from typing import Annotated
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class AgentOperationData(BaseModel):
    """Schema for AgentOperation static values in MariaDB.

    This model represents the static (immutable) values for an
    AgentOperation object. It replaces the dict-based static_values
    pattern with a type-safe Pydantic model.

    Table: agent_operations
    Primary key: uuid

    Attributes:
        uuid: The AgentOperation's unique identifier.
        namespace: The namespace this operation belongs to.
        instance_uuid: The UUID of the Instance this operation
            targets.
        commands: List of command dicts (stored as JSON).
        deadline: Absolute unix timestamp after which the operation
            must not be dispatched and must not continue executing.
            NULL means no client intent was recorded, so the server
            default applies; an explicit 0.0 means the client asked
            for no wall-clock deadline at all.
        progress_timeout: Seconds without forward progress which are
            fatal to the operation. NULL means no client intent was
            recorded, so the server default for progress-capable
            commands applies; an explicit 0.0 disables the progress
            timeout.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The AgentOperation's UUID - primary key, stored as native
    # MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The namespace this operation belongs to
    namespace: str

    # The Instance UUID this operation targets (indexed for lookups)
    instance_uuid: Annotated[UUID4, SQLNativeUUID(), SQLIndex()]

    # List of command dicts (variable-length, stored as JSON)
    commands: list[dict]

    # The absolute unix timestamp after which this operation must not
    # be dispatched and must not continue executing. Computed by the
    # API server at request receipt as time.time() + deadline_seconds.
    #
    # NULL means no client intent was recorded -- either the row
    # predates deadlines entirely, or it was written by an API node
    # which had not yet been upgraded -- and the server default
    # applies. An explicit 0.0 means the client asked for no
    # wall-clock deadline at all. 0.0 is an unambiguous sentinel
    # because a real deadline is an absolute timestamp of order 1.7e9.
    deadline: Optional[float] = None

    # Seconds without forward progress which are fatal to this
    # operation. NULL means the same thing it means for deadline: no
    # client intent was recorded, so the server default for
    # progress-capable commands applies. An explicit 0.0 disables the
    # progress timeout.
    progress_timeout: Optional[float] = None

    # Object version number for schema migrations
    version: int
