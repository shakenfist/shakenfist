# Pydantic schema for namespace attributes storage in MariaDB.
#
# This schema defines the structure for storing namespace mutable
# attributes. Attributes are values that can change during the
# namespace's lifetime, unlike static values which are immutable
# after creation.
#
# This is separate from NamespaceData (static values) per the
# architecture decision to keep mutable and immutable data in
# separate tables.
# See docs/operator_guide/database.md for the rationale.
#
# This model serves as both:
# 1. The source of truth for the namespace_attributes table schema
# 2. A typed data transfer object for namespace attributes

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class NamespaceAttributesData(BaseModel):
    """Schema for namespace attributes in MariaDB.

    This model represents the mutable attributes for a namespace
    object. Unlike NamespaceData (static values), these can be
    updated after creation.

    Table: namespace_attributes
    Primary key: name (references namespaces.name)

    A namespace's authentication keys used to be a JSON dict on this
    row. They are NamespaceKey objects in their own tables now, and the
    column was dropped by this table's version 3 migration, so the
    model no longer carries the field either (issue 5016).

    Attributes:
        name: The namespace name (references namespaces.name).
        trust: List of namespace names granted full trust.
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # Primary key — references namespaces.name
    name: str = Field(max_length=255)

    # Trust relationships — list of namespace names granted
    # full trust. Always includes 'system' by default.
    trust: list[str] = Field(
        default_factory=lambda: ['system']
    )
