# Pydantic schema for artifact index storage in MariaDB.
#
# This schema defines the structure for storing artifact version indexes.
# Each artifact can have multiple version indexes, each pointing to a
# specific blob. This replaces the etcd attribute pattern of storing
# individual index_NNNNNNNNNNNN attributes.
#
# Note: The composite primary key (artifact_uuid, index_number) is
# defined in the SQLAlchemy table definition in mariadb.py since Pydantic
# doesn't have a concept of composite keys.

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class ArtifactIndexData(BaseModel):
    """Schema for artifact version indexes in MariaDB.

    Each row represents one version of an artifact, mapping an index
    number to a blob UUID. This is a one-to-many relationship: one
    artifact has many version indexes.

    Table: artifact_indexes
    Primary key: (artifact_uuid, index_number) - composite
    Index: blob_uuid (for reverse lookups)

    Attributes:
        artifact_uuid: The artifact's UUID (part of composite PK).
        index_number: The version index number (part of composite PK).
        blob_uuid: The UUID of the blob for this version.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # Part of composite primary key - the artifact this index belongs to
    artifact_uuid: Annotated[UUID4, SQLNativeUUID()]

    # Part of composite primary key - the version index number
    index_number: int

    # The blob UUID for this version
    blob_uuid: Annotated[UUID4, SQLIndex()]
