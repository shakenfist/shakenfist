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
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.util.access_tokens import request_namespace


# The maximum length of a relationship_value. This is the width of the
# generated object_references.relationship_value column, and is used both
# as the model's validation limit and as the write-time check in
# mariadb.record_relationship(), so the two cannot drift apart.
RELATIONSHIP_VALUE_MAX_LENGTH = 255


def validate_relationship_value(relationship_value: Optional[str]) -> None:
    """Refuse a relationship_value which could not be read back.

    A value longer than the column is a programming error, not a
    recoverable condition. The raw-SQL insert does not validate, and every
    read builds an ObjectReference from the row, so a value the column
    could not hold would be written once and then raise on every
    subsequent reference read for that object (#4559). Both
    record_relationship() and the direct writer call this, so a value
    arriving over gRPC is refused at the write as well as before dispatch.

    Args:
        relationship_value: The value to check. None is always valid.

    Raises:
        ValueError: If the value is longer than the column can store.
    """
    if relationship_value is not None and (
            len(relationship_value) > RELATIONSHIP_VALUE_MAX_LENGTH):
        raise ValueError(
            f'relationship_value is {len(relationship_value)} characters, '
            f'the maximum is {RELATIONSHIP_VALUE_MAX_LENGTH}')


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
        source_uuid: The identifier of the source object (UUID string,
            FQDN for node BLOB_LOCATION refs, or name for namespaces).
        relationship: The type of relationship (disk, artifact_index, etc.).
        relationship_value: Optional value for the relationship (e.g., disk
            index "0", transcode style "qcow2"), at most
            RELATIONSHIP_VALUE_MAX_LENGTH characters.
        target_object_type: The type of the referenced object.
        target_uuid: The identifier of the referenced object (UUID string,
            FQDN for node BLOB_LOCATION refs, or name for namespaces).
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
    # Note: source_uuid is a string (max 255 chars) rather than UUID because
    # some objects use non-UUID identifiers: Namespace uses its name, and
    # Node uses FQDN for BLOB_LOCATION references (node.py:487).
    source_object_type: ObjectType
    source_uuid: Annotated[str, Field(max_length=255)]

    # Relationship type and optional value. The cap is the width of the
    # generated column, not a semantic limit: pydantic_to_sqlalchemy_table()
    # maps every str field to VARCHAR(255) and never reads max_length, so a
    # model which declared less than the column accepts would store a value
    # on write and then refuse to read it back (#4559).
    relationship: RelationshipType
    relationship_value: Annotated[
        Optional[str], Field(max_length=RELATIONSHIP_VALUE_MAX_LENGTH)
    ] = None

    # Target object (the one being referenced)
    # Note: target_uuid is a string (max 255 chars) rather than UUID because
    # some objects use non-UUID identifiers: Namespace uses its name, and
    # Node uses FQDN for BLOB_LOCATION references (node.py:487).
    target_object_type: ObjectType
    target_uuid: Annotated[str, Field(max_length=255)]

    # Timestamps
    created: float  # Unix timestamp when reference was created
    last_active: float  # Unix timestamp, updated by cleaner daemon

    def external_view(self) -> dict[str, Any]:
        """Serialize ObjectReference for JSON API responses.

        Enum values are serialized as their string values (not proto_id) for
        consistency with other API responses like state fields.

        Returns:
            A dictionary suitable for JSON serialization containing all fields
            of the ObjectReference.
        """
        return {
            'source_object_type': str(self.source_object_type),
            'source_uuid': self.source_uuid,
            'relationship': str(self.relationship),
            'relationship_value': self.relationship_value,
            'target_object_type': str(self.target_object_type),
            'target_uuid': self.target_uuid,
            'created': self.created,
            'last_active': self.last_active
        }


def references_to_grouped_dict(
    references: list[ObjectReference]
) -> dict[str, list[dict[str, Any]]]:
    """Group a list of ObjectReferences by relationship type for API responses.

    This function takes a list of ObjectReference objects and groups them by
    their relationship type, returning a dictionary where keys are relationship
    type strings and values are lists of serialized reference dictionaries.

    References with a Node endpoint (blob_location, instance_location)
    describe the placement of objects on physical infrastructure and are
    only included for the system namespace, matching the visibility of the
    blob 'locations' field. All other references link objects reachable via
    the object whose external_view() is being rendered, which the caller
    has already been authorised to see.

    Args:
        references: A list of ObjectReference objects to group.

    Returns:
        A dictionary mapping relationship type strings to lists of serialized
        ObjectReference dictionaries.

    Example:
        >>> refs = [ref1, ref2, ref3]  # ref1, ref2 are 'disk', ref3 is 'nvram'
        >>> references_to_grouped_dict(refs)
        {
            'disk': [ref1.external_view(), ref2.external_view()],
            'nvram_template': [ref3.external_view()]
        }
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    requesting_namespace = request_namespace()

    for ref in references:
        rel_key = str(ref.relationship)

        # Reference rows do not record the namespaces of their endpoint
        # objects, so visibility is decided by object type: Node objects are
        # only visible to the system namespace.
        if requesting_namespace != 'system' and ObjectType.NODE in (
                ref.source_object_type, ref.target_object_type):
            continue

        if rel_key not in grouped:
            grouped[rel_key] = []
        grouped[rel_key].append(ref.external_view())
    return grouped
