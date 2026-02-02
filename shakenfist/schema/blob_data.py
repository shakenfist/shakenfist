# Pydantic schema for blob object storage in MariaDB.
#
# This schema defines the structure for storing blob static values.
# Blobs are immutable binary data objects (images, snapshots, etc.) that
# store the actual content on disk and track metadata in the database.
#
# This model serves as both:
# 1. The source of truth for the blobs table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for blob static values

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class BlobData(BaseModel):
    """Schema for blob static values in MariaDB.

    This model represents the static (immutable) values for a blob object.
    It replaces the dict-based static_values pattern with a type-safe Pydantic
    model.

    Note: depends_on is NOT stored here - it is already migrated to the
    object_references table as a DEPENDS_ON relationship.

    The model can be constructed from:
    - Keyword arguments: BlobData(uuid='...', modified=..., ...)
    - A dict: BlobData(**row_dict)

    Table: blobs
    Primary key: uuid

    Attributes:
        uuid: The blob's unique identifier.
        modified: Unix timestamp when the blob source was last modified.
        fetched_at: Unix timestamp when the blob was fetched/created.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The blob's UUID - primary key, stored as native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # When the blob source was last modified (Unix epoch seconds)
    modified: Annotated[float, SQLIndex()]

    # When the blob was fetched/created (Unix epoch seconds)
    fetched_at: Annotated[float, SQLIndex()]

    # Object version number for schema migrations
    version: int
