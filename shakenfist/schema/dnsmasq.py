# Pydantic schema for DnsMasq object storage in MariaDB.
#
# This schema defines the structure for storing DnsMasq static values.
# DnsMasq objects represent running dnsmasq processes that provide DHCP
# and/or DNS services for virtual networks.
#
# This model serves as both:
# 1. The source of truth for the dnsmasq table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for DnsMasq static values

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class DnsMasqData(BaseModel):
    """Schema for DnsMasq static values in MariaDB.

    This model represents the static (immutable) values for a DnsMasq object.
    It replaces the dict-based static_values pattern with a type-safe Pydantic
    model.

    The model can be constructed from:
    - Keyword arguments: DnsMasqData(uuid='...', namespace='...', ...)
    - A dict: DnsMasqData(**row_dict)

    Table: dnsmasq
    Primary key: uuid

    Attributes:
        uuid: The DnsMasq's unique identifier (same as owning network UUID).
        namespace: The namespace the DnsMasq belongs to.
        owner_type: The type of the owning object (typically 'network').
        owner_uuid: The UUID of the owning object.
        version: Object version number for schema migrations.
        provide_dhcp: Whether this DnsMasq provides DHCP services.
        provide_dns: Whether this DnsMasq provides DNS services.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # Fields from ManagedExecutable base class
    # The DnsMasq's UUID - primary key, stored as native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The namespace the DnsMasq belongs to
    namespace: Annotated[str, SQLIndex()]

    # The type of the owning object (e.g., ObjectType.NETWORK)
    owner_type: Annotated[ObjectType, SQLIndex()]

    # The UUID of the owning object
    owner_uuid: Annotated[UUID4, SQLIndex()]

    # Object version number for schema migrations
    version: int

    # DnsMasq-specific fields
    # Whether this DnsMasq provides DHCP services
    provide_dhcp: bool

    # Whether this DnsMasq provides DNS services
    provide_dns: bool
