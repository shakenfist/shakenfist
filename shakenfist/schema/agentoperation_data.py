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

    # Object version number for schema migrations
    version: int
