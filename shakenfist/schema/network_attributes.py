# Pydantic schema for Network attributes storage in MariaDB.
#
# This schema defines the structure for storing Network mutable
# attributes. Attributes are values that can change during the
# Network's lifetime, unlike static values which are immutable
# after creation.
#
# This is separate from NetworkData (static values) per the
# architecture decision to keep mutable and immutable data in
# separate tables.
# See docs/operator_guide/database.md for the rationale.
#
# This model serves as both:
# 1. The source of truth for the network_attributes table schema
# 2. A typed data transfer object for Network attributes
#
# Consolidates separate etcd attributes into a single row:
# - attribute/network/{uuid}/routing -> floating_gateway
# - attribute/network/{uuid}/networkinterfaces ->
#       networkinterfaces, networkinterfaces_initialized
# - attribute/network/{uuid}/hosteddns -> hosteddns

from typing import Annotated
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class NetworkAttributesData(BaseModel):
    """Schema for Network attributes in MariaDB.

    This model represents the mutable attributes for a Network
    object. Unlike NetworkData (static values), these can be
    updated after creation.

    Table: network_attributes
    Primary key: uuid (references networks.uuid)

    Attributes:
        uuid: The Network UUID (references networks.uuid).
        floating_gateway: The floating IP gateway address assigned
            to this network, or None if no floating gateway.
            Flattened from the etcd 'routing' attribute dict.
        networkinterfaces: List of NetworkInterface UUIDs attached
            to this network.
        networkinterfaces_initialized: Whether the NI list has been
            populated (legacy migration flag).
        hosteddns: Dictionary mapping DNS names to addresses for
            networks with provide_dns=True.
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # Primary key — references networks.uuid
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Floating IP gateway address (or None)
    floating_gateway: Optional[str] = None

    # List of NetworkInterface UUIDs attached to this network
    networkinterfaces: list[str] = Field(default_factory=list)

    # Whether the NI list has been populated
    networkinterfaces_initialized: bool = False

    # DNS entries: {name: address} for hosted DNS
    hosteddns: dict[str, Any] = Field(default_factory=dict)
