# Pydantic schema for namespace object storage in MariaDB.
#
# This schema defines the structure for storing namespace static
# values. Namespaces provide multi-tenancy in Shaken Fist, isolating
# instances, networks, and artifacts.
#
# Unlike other object types, namespaces use their name (a string) as
# their primary key rather than a UUID4. This is intentional: the
# namespace name is the natural key that users interact with in the
# REST API, JWT tokens, and trust relationships. Converting to real
# UUIDs would have enormous blast radius for no meaningful benefit.
#
# This model serves as both:
# 1. The source of truth for the namespaces table schema
# 2. A typed data transfer object for namespace static values

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from shakenfist.schema.sqlalchemy import SQLIndex


class NamespaceData(BaseModel):
    """Schema for namespace static values in MariaDB.

    This model represents the static (immutable) values for a
    namespace object. It replaces the dict-based static_values
    pattern with a type-safe Pydantic model.

    The model can be constructed from:
    - Keyword arguments: NamespaceData(name='system', ...)
    - A dict: NamespaceData(**row_dict)

    Table: namespaces
    Primary key: name

    Attributes:
        name: The namespace name (primary key, string).
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The namespace name — primary key, stored as VARCHAR(255)
    name: Annotated[str, SQLIndex(), Field(max_length=255)]

    # Object version number for schema migrations
    version: int
