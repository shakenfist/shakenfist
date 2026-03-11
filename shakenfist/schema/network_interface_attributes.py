# Pydantic schema for NetworkInterface attributes storage in MariaDB.
#
# This schema defines the structure for storing NetworkInterface mutable
# attributes. Attributes are values that can change during the
# NetworkInterface's lifetime, unlike static values which are immutable
# after creation.
#
# This is separate from NetworkInterfaceData (static values) per the
# architecture decision to keep mutable and immutable data in separate
# tables.
# See docs/operator_guide/database.md for the rationale.
#
# This model serves as both:
# 1. The source of truth for the network_interface_attributes table
#    schema
# 2. A typed data transfer object for NetworkInterface attributes

from typing import Annotated
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class NetworkInterfaceAttributesData(BaseModel):
    """Schema for NetworkInterface attributes in MariaDB.

    This model represents the mutable attributes for a NetworkInterface
    object. Unlike NetworkInterfaceData (static values), these can be
    updated after creation.

    Consolidates separate etcd attributes into a single row:
    - attribute/networkinterface/{uuid}/floating ->
          floating_address

    Table: network_interface_attributes
    Primary key: uuid (references network_interfaces.uuid)

    Attributes:
        uuid: The NetworkInterface UUID (references
            network_interfaces.uuid).
        floating_address: The floating IP address assigned to this
            interface, or None if no floating IP is assigned.
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # Primary key — references network_interfaces.uuid
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Floating IP address assigned to this interface (or None)
    floating_address: Optional[str] = None
