# Pydantic schema for NamespaceKey mutable attributes in MariaDB.
#
# This schema defines the structure for storing NamespaceKey mutable
# attributes. These are the values that change during the lifetime of
# a key: the hash and nonce change when the key is rotated, and expiry
# / scopes / provenance are all mutable policy.
#
# This model serves as both:
# 1. The source of truth for the namespace_key_attributes table schema
# 2. A typed data transfer object for NamespaceKey attributes

from typing import Annotated
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class NamespaceKeyAttributesData(BaseModel):
    """Schema for NamespaceKey mutable attributes in MariaDB.

    Table: namespace_key_attributes
    Primary key: uuid (references namespace_keys.uuid)

    Attributes:
        uuid: The NamespaceKey's unique identifier (FK to
            namespace_keys).
        key: The base64-encoded bcrypt hash of the key's secret. Never
            the plaintext, and never exposed by external_view().
        nonce: The nonce embedded in tokens minted from this key.
            Rotating the key changes the nonce, which invalidates
            every outstanding token that referenced the old one.
        expiry: Nullable epoch seconds after which the key may no
            longer mint or validate tokens. NULL means the key never
            expires.
        scopes: Nullable JSON list of scope strings. NULL means
            unscoped (wildcard) -- phase 3 of the auth federation plan
            formalises the vocabulary and adds enforcement.
        provenance: Nullable JSON dict recording where the key came
            from. Written by phase 3's federated exchange (the
            mapping-rule reference and the satisfied claims); NULL for
            keys an operator created directly.
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # The NamespaceKey's UUID - primary key
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Base64-encoded bcrypt hash of the key secret
    key: Annotated[str, Field(max_length=255)]

    # Nonce, compared on every token validation
    nonce: Annotated[str, Field(max_length=255)]

    # Nullable epoch seconds; NULL means "never expires"
    expiry: Optional[float] = None

    # Nullable JSON list of scopes; NULL means unscoped (wildcard)
    scopes: Optional[list[str]] = None

    # Nullable JSON dict of provenance metadata
    provenance: Optional[dict[str, Any]] = None
