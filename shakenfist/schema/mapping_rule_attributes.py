# Pydantic schema for MappingRule mutable attributes in MariaDB.
#
# Separated from the static values because tightening a rule -- adding a
# bound claim, narrowing its scopes, shortening the keys it mints -- is
# an ordinary operation that should not cost the rule its identity or
# its event history.
#
# Mutating a rule deliberately does not touch keys already minted from
# it. Keys stand alone once minted, and a key's provenance records the
# claims that were actually satisfied, so the audit trail describes the
# grant as it was rather than as the rule reads today. Narrowing a
# rule's scopes does not retroactively narrow a live key; deleting the
# key is how you mean that.

from typing import Annotated
from typing import Any
from typing import Union

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class MappingRuleAttributesData(BaseModel):
    """Schema for MappingRule mutable attributes in MariaDB.

    Table: mapping_rule_attributes
    Primary key: uuid (references mapping_rules.uuid)

    Attributes:
        uuid: The MappingRule's unique identifier (FK to mapping_rules).
        issuer: The name of the TrustedIssuer whose tokens this rule
            accepts. Stored by name because that is how an operator
            refers to an issuer, and validated to exist at rule
            creation. A rule naming an issuer that has since been
            deleted resolves to nothing and the exchange refuses,
            which is the safe direction.
        bound_claims: Claim name to matcher. A matcher is either an
            exact string or a list of acceptable strings; both compare
            exactly, with no globbing, no regular expressions and no
            prefix matching. Every bound claim must be present in the
            token and must match. Typed narrowly rather than as a bare
            dict so that a row which is not a valid matcher set fails
            loudly on load: the failure mode worth engineering against
            is a malformed matcher that silently matches everything.
        scopes: The scopes granted to keys minted through this rule.
            Required and non-empty, unlike NamespaceKey.scopes where
            None means "unscoped, therefore wildcard" for upgrade
            compatibility with keys that predate scopes. No federated
            key may inherit that wildcard by omission, so there is
            deliberately no way to express it here.
        key_ttl: Seconds; becomes the minted key's expiry. Positive,
            enforced at rule creation -- a rule minting keys that have
            already expired is a configuration error, not a policy.
        key_name_prefix: Human-readable prefix for minted key names.
            The exchange appends a random discriminator, so this is a
            prefix and not a template and cannot collide.
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # The MappingRule's UUID - primary key
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Name of the TrustedIssuer this rule accepts tokens from
    issuer: Annotated[str, Field(max_length=255)]

    # Claim name -> exact string, or list of acceptable strings
    bound_claims: dict[str, Union[str, list[str]]]

    # Scopes granted to minted keys. Never None: see the class
    # docstring, an omitted scope list must not mean wildcard here.
    scopes: list[str]

    # Seconds of life for keys minted through this rule
    key_ttl: int

    # Prefix for minted key names; the exchange appends a discriminator
    key_name_prefix: Annotated[str, Field(max_length=128)]


# Matchers are one of these two shapes. Kept next to the model so the
# validator in shakenfist/mapping_rule.py and the type above cannot
# drift apart.
MATCHER_TYPES: tuple[type[Any], ...] = (str, list)
