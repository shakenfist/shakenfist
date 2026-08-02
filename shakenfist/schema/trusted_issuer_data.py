# Pydantic schema for TrustedIssuer object storage in MariaDB.
#
# A TrustedIssuer is an external identity provider this cluster is
# willing to believe. Deciding who may vouch for identities here is a
# cluster-level administrative decision, so issuers are owned by the
# system namespace rather than by the namespaces that reference them.
#
# Mapping rules reference an issuer by name, and a minted key's
# provenance records the rule, so the whole chain issuer <- rule <- key
# <- token is object-modelled and auditable. See
# docs/plans/PLAN-auth-federation-phase-03-exchange.md.
#
# This model serves as both:
# 1. The source of truth for the trusted_issuers table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for TrustedIssuer static values

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID
from shakenfist.schema.sqlalchemy import SQLUniqueIndex


class TrustedIssuerData(BaseModel):
    """Schema for TrustedIssuer static values in MariaDB.

    The mutable configuration -- where to fetch signing keys, which
    audience to expect -- lives in TrustedIssuerAttributesData, because
    an operator may need to correct it without the issuer's identity
    changing underneath every rule that references it.

    Table: trusted_issuers
    Primary key: uuid
    Unique: name

    Attributes:
        uuid: The TrustedIssuer's unique identifier.
        name: The operator-chosen name. Unique cluster wide, and how
            mapping rules refer to this issuer.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The TrustedIssuer's UUID - primary key, stored as native MariaDB
    # UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Unique cluster wide: rules reference an issuer by this name, so
    # two issuers sharing one would make a rule ambiguous.
    name: Annotated[str, Field(max_length=255), SQLUniqueIndex()]

    # Object version number for schema migrations
    version: int
