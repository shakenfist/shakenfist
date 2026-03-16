# Copyright 2019 Michael Still and contributors
# Pydantic schema for object metadata storage in MariaDB.
#
# This schema defines the structure for storing object metadata and
# last_cluster_operation in a single shared table. All object types use
# this same table structure, with (object_type, object_uuid) as the
# composite primary key — following the same pattern as object_states.
#
# This model serves as both:
# 1. The source of truth for the object_metadata table schema
# 2. A typed data transfer object for object metadata
#
# Consolidates these etcd attributes into a single row per object:
# - attribute/{type}/{uuid}/metadata -> metadata
# - attribute/{type}/{uuid}/last_cluster_operation -> last_cluster_operation
#
# metadata: User-defined key-value pairs, available on all object types.
# last_cluster_operation: Tracks the last queued operation, only used by
#     DatabaseBackedObjectWithOperations subclasses (Instance, Artifact,
#     Network).

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
        last_cluster_operation: Dict with op_type and op_uuid of the
            last queued cluster operation, or None if not set. Only
            meaningful for Instance, Artifact, and Network objects.
    """

    # NOTE: Not frozen - metadata is mutable
    model_config = ConfigDict(frozen=False)

    # Part of composite primary key (object_type, object_uuid)
    object_type: str

    # Part of composite primary key (object_type, object_uuid)
    object_uuid: str

    # User-defined key-value metadata: {'key1': 'val1', 'key2': 'val2'}
    metadata: Optional[dict[str, Any]] = Field(default=None)

    # Last cluster operation: {'op_type': '...', 'op_uuid': '...'}
    last_cluster_operation: Optional[dict[str, Any]] = Field(default=None)
