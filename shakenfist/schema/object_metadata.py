# Copyright 2019 Michael Still and contributors
# Pydantic schema for object metadata storage in MariaDB.
#
# This schema defines the structure for storing object metadata in a single
# shared table. All object types use this same table structure, with
# (object_type, object_uuid) as the composite primary key — following the same
# pattern as object_states.
#
# This model serves as both:
# 1. The source of truth for the object_metadata table schema
# 2. A typed data transfer object for object metadata
#
# Consolidates this etcd attribute into a single row per object:
# - attribute/{type}/{uuid}/metadata -> metadata
#
# metadata: User-defined key-value pairs, available on all object types.

from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ObjectMetadataData(BaseModel):
    """Schema for object metadata in MariaDB.

    This model represents a single row in the object_metadata table.
    The combination of (object_type, object_uuid) forms the primary key
    because different object types can share the same UUID (e.g., a
    Network and its associated IPAM both use the network's UUID).

    Table: object_metadata
    Primary key: (object_type, object_uuid)

    Attributes:
        object_type: The type of object (ObjectType enum value as string).
        object_uuid: The UUID of the object this metadata belongs to.
        metadata: User-defined key-value pairs, or None if not set.
    """

    # NOTE: Not frozen - metadata is mutable
    model_config = ConfigDict(frozen=False)

    # Part of composite primary key (object_type, object_uuid)
    object_type: str

    # Part of composite primary key (object_type, object_uuid)
    object_uuid: str

    # User-defined key-value metadata: {'key1': 'val1', 'key2': 'val2'}
    metadata: Optional[dict[str, Any]] = Field(default=None)
