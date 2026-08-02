# Pydantic schema for MappingRule object storage in MariaDB.
#
# A MappingRule says which external identities a namespace is willing to
# mint keys for, and what those keys may do. It is owned by the
# namespace it targets, because deciding who may act as your namespace
# is the namespace owner's decision, not a cluster-wide one -- unlike
# the TrustedIssuer it references, which is administrative.
#
# The chain issuer <- rule <- key <- token is object-modelled end to
# end, so an audit can answer "why does this credential exist" without
# leaving the database. See
# docs/plans/PLAN-auth-federation-phase-03-exchange.md.
#
# This model serves as both:
# 1. The source of truth for the mapping_rules table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for MappingRule static values
#
# Note that the (namespace, name) pair must be unique. That index is
# not expressible with the field markers in
# shakenfist/schema/sqlalchemy.py -- both the SQLUniqueIndex marker and
# the compound-index configuration paths are single-column-unique or
# multi-column-non-unique respectively -- so it is created by hand in
# _ensure_mapping_rules_schema() in shakenfist/mariadb.py, exactly as
# _ensure_namespace_keys_schema() does for namespace keys.

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class MappingRuleData(BaseModel):
    """Schema for MappingRule static values in MariaDB.

    The policy the rule expresses -- which issuer, which claims, what
    the resulting key may do -- lives in MappingRuleAttributesData,
    because an operator tightening a rule's claims should not have to
    delete and recreate the rule and lose its event history.

    Table: mapping_rules
    Primary key: uuid
    Unique: (namespace, name), created in the ensure function.

    Attributes:
        uuid: The MappingRule's unique identifier.
        namespace: The name of the owning namespace, matching
            namespaces.name. The exchange names a namespace and a rule
            together, and that lookup is served by the leading column
            of the (namespace, name) unique index, so no separate
            single-column index is carried.
        name: The rule name, as chosen by the namespace owner. Unique
            within the owning namespace.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The MappingRule's UUID - primary key, stored as native MariaDB
    # UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The owning namespace's name. Per-namespace listing is served by
    # the (namespace, name) unique index created in the ensure
    # function, so this field carries no index of its own.
    namespace: Annotated[str, Field(max_length=255)]

    # The rule name, unique within the owning namespace
    name: Annotated[str, Field(max_length=255)]

    # Object version number for schema migrations
    version: int
