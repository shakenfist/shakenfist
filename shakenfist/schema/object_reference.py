# Pydantic schema for object reference storage in MariaDB.
#
# This schema defines the structure for storing references between objects.
# The table tracks relationships like Instance->Blob (disk), Artifact->Blob
# (index), Blob->Blob (depends_on, transcode), etc.
#
# The combination of (source_object_type, source_uuid, relationship,
# target_object_type, target_uuid) forms the primary key. This ensures each
# relationship is unique and can be queried from either direction.

from typing import Annotated
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class ObjectReference(BaseModel):
    """Schema for object reference storage in MariaDB.

    This model represents a single reference between two objects. The table
    enables queries like "what references this blob?" (ref_count replacement)
    and "what does this instance reference?" (for cleanup).

    The primary key is a compound of (source_object_type, source_uuid,
    relationship, target_object_type, target_uuid). This ensures:
    - Each relationship is unique
    - Queries can efficiently find all references to/from an object
    - No separate id column is needed

    Attributes:
        source_object_type: The type of the object that holds the reference.
        source_uuid: The UUID of the source object.
        relationship: The type of relationship (disk, artifact_index, etc.).
        relationship_value: Optional value for the relationship (e.g., disk
            index "0", transcode style "qcow2").
        target_object_type: The type of the referenced object.
        target_uuid: The UUID of the referenced object.
        created: Unix timestamp when reference was created.
        last_active: Unix timestamp, updated by cleaner daemon when it
            observes the reference is still valid/in-use.
    """

    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                # Source lookup: "What does this instance reference?"
                ['source_object_type', 'source_uuid'],
                # Target lookup: "What references this blob?"
                ['target_object_type', 'target_uuid'],
                # Relationship queries: "Show all transcodes"
                ['relationship', 'relationship_value'],
                # Time-based queries
                ['created'],
                ['last_active'],
            ]
        }
    )

    # Source object (the one holding the reference)
    source_object_type: ObjectType
    source_uuid: Annotated[UUID4, SQLNativeUUID()]

    # Relationship type and optional value
    relationship: RelationshipType
    relationship_value: Annotated[
        Optional[str], Field(max_length=64)
    ] = None

    # Target object (the one being referenced)
    target_object_type: ObjectType
    target_uuid: Annotated[UUID4, SQLNativeUUID()]

    # Timestamps
    created: float  # Unix timestamp when reference was created
    last_active: float  # Unix timestamp, updated by cleaner daemon
