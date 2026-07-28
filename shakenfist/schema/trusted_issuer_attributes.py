# Pydantic schema for TrustedIssuer mutable attributes in MariaDB.
#
# Separated from the static values because an operator may need to
# correct where an issuer's signing keys are fetched from, or which
# audience to expect, without the issuer's identity changing underneath
# every mapping rule which references it by name.

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class TrustedIssuerAttributesData(BaseModel):
    """Schema for TrustedIssuer mutable values in MariaDB.

    Table: trusted_issuer_attributes
    Primary key: uuid (references trusted_issuers.uuid)

    Attributes:
        uuid: The TrustedIssuer's unique identifier (FK to
            trusted_issuers).
        issuer_url: The value an identity token's `iss` claim must
            equal. Compared exactly -- no normalisation, no trailing
            slash tolerance -- because a loose comparison here is a
            way to accept tokens from somewhere else entirely.
        jwks_uri: Where the issuer publishes its signing keys. Fetched
            and cached; never followed from a claim inside a token,
            only from this configured value.
        audience: The value an identity token's `aud` claim must
            contain. Prevents a token minted for some other relying
            party being replayed at us.
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # The TrustedIssuer's UUID - primary key
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Exact match against the token's iss claim
    issuer_url: Annotated[str, Field(max_length=1024)]

    # Where signing keys come from. Configured, never discovered from
    # the token being validated.
    jwks_uri: Annotated[str, Field(max_length=1024)]

    # Expected aud claim
    audience: Annotated[str, Field(max_length=1024)]
