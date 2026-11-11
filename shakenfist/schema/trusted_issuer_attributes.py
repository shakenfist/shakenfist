# Pydantic schema for TrustedIssuer mutable attributes in MariaDB.
#
# Separated from the static values because an operator may need to
# correct where an issuer's signing keys are fetched from, or which
# audience to expect, without the issuer's identity changing underneath
# every mapping rule which references it by name.

from typing import Annotated
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLLongText
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
            only from this configured value. Optional because it may be
            filled in from the issuer's discovery document; a value the
            operator supplied is never overwritten by discovery.
        audience: The value an identity token's `aud` claim must
            contain. Prevents a token minted for some other relying
            party being replayed at us.
        authorization_endpoint: Where a browser is sent to begin an
            authorisation code login. Learned from discovery. Null when
            the issuer has not been discovered or does not publish one.
        token_endpoint: Where an authorisation code or device code is
            exchanged for tokens. Learned from discovery. Null when the
            issuer has not been discovered or does not publish one.
        device_authorization_endpoint: Where a device code login is
            started. Learned from discovery. Null when the issuer does
            not support the device code grant.
        client_id: The OAuth client identifier registered with the
            issuer for human login. Operator supplied; it cannot be
            discovered. Null for issuers used only to verify workload
            tokens.
        oidc_scopes: The OpenID Connect scopes to request at login, as a
            single space separated string. Operator supplied.
        discovered_at: Epoch seconds at which discovery last succeeded
            for this issuer. Null if discovery has never run.
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # The TrustedIssuer's UUID - primary key
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Exact match against the token's iss claim
    issuer_url: Annotated[str, Field(max_length=1024)]

    # Where signing keys come from. Configured, never discovered from
    # the token being validated.
    # It may now be filled in from the issuer's discovery document when
    # the operator does not supply one.
    jwks_uri: Annotated[Optional[str], Field(max_length=1024)] = None

    # Expected aud claim
    audience: Annotated[str, Field(max_length=1024)]

    # Endpoints learned from the issuer's discovery document. These URLs
    # come from a third party rather than from us, so their length cannot
    # be bounded by inspection, and a silent truncation would land on an
    # endpoint a user's browser is then sent to. They are therefore stored
    # as LONGTEXT rather than the VARCHAR(255) a plain str gets.
    authorization_endpoint: Annotated[
        Optional[str], SQLLongText(), Field(max_length=1024)] = None
    token_endpoint: Annotated[
        Optional[str], SQLLongText(), Field(max_length=1024)] = None
    device_authorization_endpoint: Annotated[
        Optional[str], SQLLongText(), Field(max_length=1024)] = None

    # Operator supplied, because a client registration cannot be discovered.
    # A short value, so the 255 limit matches the VARCHAR(255) column.
    client_id: Annotated[Optional[str], Field(max_length=255)] = None

    # A space separated OIDC scope string, which is the wire form. It is
    # deliberately not a list, unlike the sibling NamespaceKeyAttributesData
    # scopes: the client passes this value through to the provider verbatim,
    # and an operator pastes what their provider's documentation says to
    # request, so a join on one side and a split on the other would buy
    # nothing. It is not called scopes because that word already means a
    # Shaken Fist authorisation scope on keys, mapping rules and access
    # tokens, and an OIDC scope is a different thing.
    oidc_scopes: Annotated[Optional[str], Field(max_length=255)] = None

    # Epoch seconds of the last successful discovery, a float like the
    # sibling NamespaceKeyAttributesData expiry.
    discovered_at: Optional[float] = None
