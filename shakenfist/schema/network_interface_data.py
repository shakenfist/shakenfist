# Pydantic schema for NetworkInterface object storage in MariaDB.
#
# This schema defines the structure for storing NetworkInterface static
# values. NetworkInterfaces represent virtual NICs attached to instances,
# connecting them to virtual networks.
#
# This model serves as both:
# 1. The source of truth for the network_interfaces table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for NetworkInterface static values

from typing import Annotated
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class NetworkInterfaceData(BaseModel):
    """Schema for NetworkInterface static values in MariaDB.

    This model represents the static (immutable) values for a
    NetworkInterface object. It replaces the dict-based static_values
    pattern with a type-safe Pydantic model.

    The model can be constructed from:
    - Keyword arguments: NetworkInterfaceData(uuid='...', ...)
    - A dict: NetworkInterfaceData(**row_dict)

    Table: network_interfaces
    Primary key: uuid

    Attributes:
        uuid: The NetworkInterface's unique identifier.
        network_uuid: The UUID of the network this interface belongs to.
        instance_uuid: The UUID of the instance this interface is
            attached to.
        macaddr: The MAC address assigned to this interface.
        ipv4: The IPv4 address assigned to this interface.
        order: The interface ordering index within the instance.
        model: The virtio device model (e.g., 'virtio').
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The NetworkInterface's UUID - primary key, stored as native
    # MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The network this interface belongs to
    network_uuid: Annotated[UUID4, SQLNativeUUID(), SQLIndex()]

    # The instance this interface is attached to
    instance_uuid: Annotated[UUID4, SQLNativeUUID(), SQLIndex()]

    # The MAC address assigned to this interface
    macaddr: Annotated[str, SQLIndex()]

    # The IPv4 address assigned to this interface
    ipv4: str

    # The interface ordering index within the instance
    order: int

    # The virtio device model (e.g., 'virtio'), None for some test cases
    model: Optional[str] = None

    # Object version number for schema migrations
    version: int
