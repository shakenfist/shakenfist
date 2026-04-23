# Pydantic schema for artifact object storage in MariaDB.
#
# This schema defines the structure for storing artifact static values.
# Artifacts represent versioned disk images (snapshots, labels, images)
# with a source URL and namespace ownership.
#
# This model serves as both:
# 1. The source of truth for the artifacts table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for artifact static values

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class ArtifactData(BaseModel):
    """Schema for artifact static values in MariaDB.

    This model represents the static (immutable) values for an artifact
    object. It replaces the dict-based static_values pattern with a
    type-safe Pydantic model.

    The model can be constructed from:
    - Keyword arguments: ArtifactData(uuid='...', artifact_type='...', ...)
    - A dict: ArtifactData(**row_dict)

    Table: artifacts
    Primary key: uuid

    Attributes:
        uuid: The artifact's unique identifier.
        artifact_type: One of 'snapshot', 'label', 'image', 'other'.
        source_url: Origin URL (sf://blob/, sf://snapshot/, etc.).
        name: Human-readable name (derived from source_url if not set).
        namespace: Owning namespace (required).
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The artifact's UUID - primary key, stored as native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Artifact type: snapshot, label, image, or other
    artifact_type: Annotated[str, SQLIndex()]

    # Origin URL for the artifact content
    source_url: Annotated[str, SQLIndex()]

    # Human-readable artifact name
    name: Annotated[str, SQLIndex()]

    # Owning namespace (required for all persisted artifacts)
    namespace: Annotated[str, SQLIndex()]

    # Object version number for schema migrations
    version: int
