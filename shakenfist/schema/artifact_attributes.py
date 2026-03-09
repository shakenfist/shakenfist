# Pydantic schema for artifact attributes storage in MariaDB.
#
# This schema defines the structure for storing artifact mutable attributes.
# Attributes are values that can change during the artifact's lifetime,
# unlike static values which are immutable after creation.
#
# This is separate from ArtifactData (static values) per the architecture
# decision to keep mutable and immutable data in separate tables.
# See docs/operator_guide/database.md for the rationale.
#
# This model serves as both:
# 1. The source of truth for the artifact_attributes table schema
# 2. A typed data transfer object for artifact attributes

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class ArtifactAttributesData(BaseModel):
    """Schema for artifact attributes in MariaDB.

    This model represents the mutable attributes for an artifact object.
    Unlike ArtifactData (static values), these can be updated after
    creation.

    Table: artifact_attributes
    Primary key: uuid (references artifacts.uuid)

    Attributes:
        uuid: The artifact's unique identifier (references artifacts.uuid).
        max_versions: Maximum number of versions to keep.
        shared: Whether the artifact is shared across namespaces.
        highest_index: The current highest version index number.
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # Primary key - references artifacts.uuid
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Maximum number of versions to retain (0 = use default)
    max_versions: int = 0

    # Whether this artifact is shared with other namespaces
    shared: Annotated[bool, SQLIndex()] = False

    # The current highest version index number
    highest_index: int = 0
