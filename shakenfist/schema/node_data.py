# Pydantic schema for node object storage in MariaDB.
#
# This schema defines the structure for storing node static values.
# Nodes represent physical or virtual machines in the Shaken Fist
# cluster that run daemons and host instances.
#
# Historically, nodes used their FQDN as a fake UUID identifier.
# This migration introduces proper UUID4 identifiers with the FQDN
# stored as a separate indexed column for efficient lookups.
#
# This model serves as both:
# 1. The source of truth for the nodes table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for node static values

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID
from shakenfist.schema.sqlalchemy import SQLUniqueIndex


class NodeData(BaseModel):
    """Schema for node static values in MariaDB.

    This model represents the static (immutable) values for a node
    object. It replaces the dict-based static_values pattern with a
    type-safe Pydantic model.

    The model can be constructed from:
    - Keyword arguments: NodeData(uuid='...', fqdn='...', ...)
    - A dict: NodeData(**row_dict)

    Table: nodes
    Primary key: uuid

    Attributes:
        uuid: The node's unique identifier (real UUID4).
        fqdn: The node's fully qualified domain name (unique).
        ip: The node's mesh network IP address.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The node's UUID - primary key, stored as native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The node's FQDN - unique index for efficient lookups
    fqdn: Annotated[str, SQLUniqueIndex()]

    # The node's mesh network IP address
    ip: str

    # Object version number for schema migrations
    version: int
