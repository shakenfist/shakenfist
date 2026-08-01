import datetime
from typing import Optional
from typing import TYPE_CHECKING

from flask_jwt_extended import create_access_token
from flask_jwt_extended import get_jwt_identity

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.exceptions import CannotParseJWTIdentity
from shakenfist.external_api import scopes as api_scopes

if TYPE_CHECKING:
    from shakenfist.namespace import Namespace


def create_token(
    ns: 'Namespace', keyname: str, nonce: str,
    duration: int = config.API_TOKEN_DURATION,
    scopes: Optional[list[str]] = None
) -> dict[str, str | int]:
    # NOTE(mikal): the "identity" here must be a string, which was not always
    # true for tokens we issued.
    #
    # Scopes are copied from the minting key into the token, so that
    # enforcement is a set membership test on the token rather than a
    # key lookup per request. A key with no scopes recorded -- which is
    # every key predating the federation work -- mints a wildcard
    # token, so nothing an operator already has changes behaviour.
    #
    # Note that tokens issued before this claim existed carry no
    # 'scopes' at all. api_scopes.satisfies() treats a missing claim as
    # wildcard for exactly that reason: refusing them would invalidate
    # every token in flight across an upgrade.
    #
    # None and an empty list mean different things here and must not be
    # conflated. None is "no scopes recorded", which is every legacy
    # key, and mints a wildcard. [] is "recorded as granting nothing",
    # and must mint a token which satisfies nothing. Testing scopes for
    # truthiness makes an empty grant mean total access, which is the
    # worst direction for an authorisation bug to fail in.
    claims = {
        'iss': config.ZONE,
        'nonce': nonce,
        'scopes': (list(scopes) if scopes is not None
                   else [api_scopes.WILDCARD])
    }
    token = create_access_token(
        identity=f'{ns.uuid}:{keyname}',
        additional_claims=claims,
        expires_delta=datetime.timedelta(minutes=duration))
    # NOTE(mikal): neither the token nor the nonce may appear in the
    # event. An event is readable by anyone who can read the namespace.
    # A JWT there is a credential anyone reading it can replay until it
    # expires, and the nonce is the revocation handle -- publishing it
    # tells a reader which of their captured tokens are still live, and
    # confirms a rotation has not happened yet.
    ns.add_event(
        EVENT_TYPE_AUDIT, 'token created from key',
        extra={'keyname': keyname})
    return {
        'access_token': token,
        'token_type': 'Bearer',
        'expires_in': duration * 60
    }


def parse_jwt_identity() -> list[str]:
    ident_string = get_jwt_identity()
    ident = ident_string.split(':')
    if len(ident) != 2:
        raise CannotParseJWTIdentity(f'Cannot parse identity "{ident_string}"')
    return ident


def request_namespace() -> str:
    return parse_jwt_identity()[0]
