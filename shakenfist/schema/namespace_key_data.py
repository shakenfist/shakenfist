# Pydantic schema for NamespaceKey object storage in MariaDB.
#
# This schema defines the structure for storing NamespaceKey static
# values. A NamespaceKey is an authentication key owned by a
# namespace; it is the thing an operator names when creating an API
# key, and the thing a JWT's "<namespace>:<keyname>" identity string
# refers to.
#
# This model serves as both:
# 1. The source of truth for the namespace_keys table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for NamespaceKey static values
#
# Note that the (namespace, name) pair must be unique. That index is
# not expressible with the field markers in
# shakenfist/schema/sqlalchemy.py -- both the SQLUniqueIndex marker and
# the compound-index configuration paths are single-column-unique or
# multi-column-non-unique respectively -- so it is created by hand in
# _ensure_namespace_keys_schema() in shakenfist/mariadb.py.

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class NamespaceKeyData(BaseModel):
    """Schema for NamespaceKey static values in MariaDB.

    This model represents the static (immutable) values for a
    NamespaceKey object. The secret material (hash and nonce) lives in
    NamespaceKeyAttributesData because key rotation mutates it.

    Table: namespace_keys
    Primary key: uuid
    Unique: (namespace, name), created in the ensure function.

    Attributes:
        uuid: The NamespaceKey's unique identifier.
        namespace: The name of the owning namespace, matching
            namespaces.name. Listing a namespace's keys is the hot
            path for /auth token minting; that lookup is served by
            the leading column of the (namespace, name) unique
            index, so no separate single-column index is carried.
        name: The key name, as chosen by the operator. Unique within
            the owning namespace.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The NamespaceKey's UUID - primary key, stored as native MariaDB
    # UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The owning namespace's name. Per-namespace listing is served by
    # the (namespace, name) unique index created in the ensure
    # function, so this field carries no index of its own.
    namespace: Annotated[str, Field(max_length=255)]

    # The key name, unique within the owning namespace
    name: Annotated[str, Field(max_length=255)]

    # Object version number for schema migrations
    version: int
