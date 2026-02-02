# Pydantic schema for blob attributes storage in MariaDB.
#
# This schema defines the structure for storing blob mutable attributes.
# Attributes are values that can change during the blob's lifetime, unlike
# static values which are immutable after creation.
#
# This is separate from BlobData (static values) per the architecture
# decision to keep mutable and immutable data in separate tables.
# See docs/operator_guide/database.md for the rationale.
#
# This model serves as both:
# 1. The source of truth for the blob_attributes table schema
# 2. A typed data transfer object for blob attributes

from typing import Annotated, Any, Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class BlobAttributesData(BaseModel):
    """Schema for blob attributes in MariaDB.

    This model represents the mutable attributes for a blob object.
    Unlike BlobData (static values), these can be updated after creation.

    Table: blob_attributes
    Primary key: uuid (references blobs.uuid)

    Attributes:
        uuid: The blob's unique identifier (references blobs.uuid).
        size: The blob's file size in bytes.
        info: QEMU image metadata (format, virtual_size, etc.) as JSON.
        last_used: Unix timestamp when the blob was last accessed.
        expires_at: Unix timestamp when the blob expires (0 = never).
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # Primary key - references blobs.uuid
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Blob file size in bytes (immutable after first set, but stored here
    # because it's set after blob creation during observe())
    size: int = 0

    # QEMU image metadata (virtual_size, format, backing_file, etc.)
    # Stored as JSON in MariaDB
    info: dict[str, Any] = Field(default_factory=dict)

    # When the blob was last accessed (updated by record_usage())
    last_used: Annotated[Optional[float], SQLIndex()] = None

    # When the blob expires (0 = never expires)
    # Updated by set_lifetime()
    expires_at: Annotated[float, SQLIndex()] = 0.0
