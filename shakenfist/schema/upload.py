# Pydantic schema for upload object storage in MariaDB.
#
# This schema defines the structure for storing upload static values.
# Uploads are temporary objects that receive streamed data during artifact
# creation. Once the upload is complete, the data is converted to a blob
# and the upload is deleted.
#
# This model serves as both:
# 1. The source of truth for the uploads table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for upload static values

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class UploadData(BaseModel):
    """Schema for upload static values in MariaDB.

    This model represents the static (immutable) values for an upload object.
    It replaces the dict-based static_values pattern with a type-safe Pydantic
    model.

    The model can be constructed from:
    - Keyword arguments: UploadData(uuid='...', node='...', ...)
    - A dict: UploadData(**row_dict)

    Table: uploads
    Primary key: uuid

    Attributes:
        uuid: The upload's unique identifier.
        node: The node where the upload data is stored.
        created_at: Unix timestamp when the upload was created.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The upload's UUID - primary key, stored as native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The node where the upload data is stored
    node: Annotated[str, SQLIndex()]

    # When the upload was created (Unix epoch seconds)
    created_at: Annotated[float, SQLIndex()]

    # Object version number for schema migrations
    version: int
