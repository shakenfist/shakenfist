# Pydantic schema for Network object storage in MariaDB.
#
# This schema defines the structure for storing Network static values.
# Networks represent virtual L2 networks with optional DHCP, NAT, and
# DNS services, connected via VXLAN overlays.
#
# This model serves as both:
# 1. The source of truth for the networks table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for Network static values

from typing import Annotated
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class NetworkData(BaseModel):
    """Schema for Network static values in MariaDB.

    This model represents the static (immutable) values for a Network
    object. It replaces the dict-based static_values pattern with a
    type-safe Pydantic model.

    The model can be constructed from:
    - Keyword arguments: NetworkData(uuid='...', ...)
    - A dict: NetworkData(**row_dict)

    Table: networks
    Primary key: uuid

    Attributes:
        uuid: The Network's unique identifier.
        name: Human-readable network name.
        namespace: The namespace this network belongs to (None for
            floating network).
        netblock: The CIDR notation network block
            (e.g., '10.0.0.0/24').
        provide_dhcp: Whether DHCP services are enabled.
        provide_nat: Whether NAT services are enabled.
        provide_dns: Whether DNS services are enabled.
        vxid: The VXLAN ID (1-16777215), globally unique. The
            networks table has a UNIQUE constraint on this column
            to enforce atomicity of allocation.
        egress_nic: Egress network interface name override, or None
            to use the node's configured default.
        mesh_nic: Mesh network interface name override, or None to
            use the node's configured default.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The Network's UUID - primary key, stored as native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Human-readable network name
    name: Annotated[str, SQLIndex()]

    # The namespace this network belongs to (nullable, indexed for
    # filtered listings)
    namespace: Annotated[Optional[str], SQLIndex()] = None

    # The CIDR notation network block
    netblock: str

    # Service flags
    provide_dhcp: bool
    provide_nat: bool
    provide_dns: bool = False

    # VXLAN ID — unique constraint enforced at table level
    vxid: int

    # NIC overrides (None = use node config defaults)
    egress_nic: Optional[str] = None
    mesh_nic: Optional[str] = None

    # Object version number for schema migrations
    version: int
