# Pydantic schema for relationship type validation.
#
# This module defines an enum of all valid relationship types for the
# object_references table. This provides type safety and validation for
# tracking references between objects.
#
# This is the single source of truth for relationship type names AND their
# protobuf integer identifiers. The protobuf enum generator reads these values.
#
# IMPORTANT: The object_references table is designed for PERSISTENT
# relationships with INFREQUENT updates. Before adding a new relationship
# type here, consider whether your use case fits this pattern.
#
# Use object_references when:
# - The relationship is long-lived (exists for the lifetime of the objects)
# - Updates are infrequent (created once, rarely modified)
# - You need to query "what references X?" or "what does X reference?"
#
# Use a SEPARATE specialized table when:
# - High-frequency inserts (e.g., operation ordering)
# - Relationships are transient or have different lifecycle than the objects
# - You need native database features like AUTO_INCREMENT sequences
# - Additional metadata beyond (source, target, value) is required
# - The relationship involves more than two objects (ternary+)

from enum import Enum
from typing import NamedTuple
from typing import Optional


class RelationshipTypeValue(NamedTuple):
    """Value type for RelationshipType enum members.

    Each RelationshipType has both a string value (for database/API use) and a
    protobuf ID (for gRPC communication). This NamedTuple makes both values
    explicit and self-documenting.

    Attributes:
        string: The string value used in databases and APIs.
        proto_id: The integer ID used in protobuf messages. These values are
            stable and must never be changed or reused once assigned.
    """
    string: str
    proto_id: int


# Lookup table for proto_id -> RelationshipType (module-level to avoid
# Enum issues)
_RELATIONSHIP_TYPE_PROTO_MAP: dict[int, 'RelationshipType'] = {}


class RelationshipType(str, Enum):
    """Enum of all valid relationship types for object_references.

    This enum inherits from str so that the value can be used directly as a
    string in SQL queries and JSON serialization. For example:
        RelationshipType.DISK.value == 'disk'
        RelationshipType.DISK == 'disk'  # Also works due to str inheritance
        str(RelationshipType.DISK) == 'disk'  # Works due to __str__ override

    Each member also has a proto_id attribute containing the protobuf integer
    value. This ensures protobuf enum values remain stable even if the Python
    enum is reordered. Use RelationshipType.from_proto_id() to convert from
    protobuf.

    Relationship types and their meanings:
    - DISK: Instance -> Blob, value is disk index ("0", "1", ...)
    - NVRAM_TEMPLATE: Instance -> Blob, value is NULL
    - ARTIFACT_INDEX: Artifact -> Blob, value is index ("000000000001")
    - DEPENDS_ON: Blob -> Blob, value is NULL
    - TRANSCODE: Blob -> Blob, value is style ("qcow2", "raw")
    - AGENT_OUTPUT: AgentOperation -> Blob, value is output type
      ("stdout", "stderr")
    - BLOB_LOCATION: Node -> Blob, value is NULL (blob fully present on node)
    """

    _proto_id: int

    def __new__(cls, string: str, proto_id: int) -> 'RelationshipType':
        """Create a RelationshipType with both string and protobuf values.

        Args:
            string: The string value for database/API use.
            proto_id: The stable integer ID for protobuf messages.

        Note: When defining enum members with RelationshipTypeValue(...),
        Python's enum machinery unpacks the NamedTuple as positional arguments.
        """
        obj = str.__new__(cls, string)
        obj._value_ = string
        obj._proto_id = proto_id
        return obj

    def __str__(self) -> str:
        """Return the enum value as a string.

        This override is needed because the default str(Enum) returns
        'EnumName.MEMBER_NAME' rather than the value. Since we want to use
        these values in error messages and other string contexts,
        we override __str__ to return the value directly.
        """
        return str(self.value)

    @property
    def proto_id(self) -> int:
        """Return the protobuf integer ID for this type."""
        return self._proto_id

    @classmethod
    def from_proto_id(cls, proto_id: int) -> Optional['RelationshipType']:
        """Get a RelationshipType from its protobuf ID.

        Args:
            proto_id: The protobuf enum integer value.

        Returns:
            The corresponding RelationshipType, or None if proto_id is 0
            (UNSPECIFIED) or unknown.
        """
        global _RELATIONSHIP_TYPE_PROTO_MAP
        if proto_id == 0:
            return None
        if not _RELATIONSHIP_TYPE_PROTO_MAP:
            _RELATIONSHIP_TYPE_PROTO_MAP = {m.proto_id: m for m in cls}
        return _RELATIONSHIP_TYPE_PROTO_MAP.get(proto_id)

    # Relationship types (proto_id values are stable - never reorder or change!)
    # Instance -> Blob relationships
    DISK = RelationshipTypeValue(string='disk', proto_id=1)
    NVRAM_TEMPLATE = RelationshipTypeValue(string='nvram_template', proto_id=2)

    # Artifact -> Blob relationships
    ARTIFACT_INDEX = RelationshipTypeValue(string='artifact_index', proto_id=3)

    # Blob -> Blob relationships
    DEPENDS_ON = RelationshipTypeValue(string='depends_on', proto_id=4)
    TRANSCODE = RelationshipTypeValue(string='transcode', proto_id=5)

    # AgentOperation -> Blob relationships
    AGENT_OUTPUT = RelationshipTypeValue(string='agent_output', proto_id=6)

    # Node -> Blob relationships
    BLOB_LOCATION = RelationshipTypeValue(string='blob_location', proto_id=7)

    # Node -> Instance relationships. The source is keyed by node UUID
    # (as a string), not fqdn: the fqdn keying of BLOB_LOCATION rows is
    # a legacy of the pre-UUID node identity and should not be copied.
    INSTANCE_LOCATION = RelationshipTypeValue(
        string='instance_location', proto_id=8)
