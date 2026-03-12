# Copyright 2019 Michael Still and contributors
#
# Pydantic schema for IPAM object storage in MariaDB.
#
# This schema defines the structure for storing IPAM static values.
# IPAMs represent IP Address Managers that track address allocation
# within a network's IP block.
#
# This model serves as both:
# 1. The source of truth for the ipams table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for IPAM static values

from typing import Annotated
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class IPAMData(BaseModel):
    """Schema for IPAM static values in MariaDB.

    This model represents the static (immutable) values for an IPAM
    object. It replaces the dict-based static_values pattern with a
    type-safe Pydantic model.

    The model can be constructed from:
    - Keyword arguments: IPAMData(uuid='...', ...)
    - A dict: IPAMData(**row_dict)

    Table: ipams
    Primary key: uuid

    Attributes:
        uuid: The IPAM's unique identifier (same as the parent
            network's UUID).
        namespace: The namespace this IPAM belongs to (None for
            some IPAMs).
        network_uuid: The UUID of the network this IPAM manages
            addresses for.
        ipblock: The CIDR notation IP block (e.g., '192.168.1.0/24').
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The IPAM's UUID - primary key, stored as native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The namespace this IPAM belongs to (nullable)
    namespace: Optional[str] = None

    # The network this IPAM manages addresses for
    network_uuid: Annotated[UUID4, SQLNativeUUID(), SQLIndex()]

    # The CIDR notation IP block
    ipblock: str

    # Object version number for schema migrations
    version: int
