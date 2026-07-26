import datetime
from typing import TYPE_CHECKING

from flask_jwt_extended import create_access_token
from flask_jwt_extended import get_jwt_identity

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.exceptions import CannotParseJWTIdentity

if TYPE_CHECKING:
    from shakenfist.namespace import Namespace


def create_token(
    ns: 'Namespace', keyname: str, nonce: str,
    duration: int = config.API_TOKEN_DURATION
) -> dict[str, str | int]:
    # NOTE(mikal): the "identity" here must be a string, which was not always
    # true for tokens we issued.
    token = create_access_token(
        identity=f'{ns.uuid}:{keyname}',
        additional_claims={
            'iss': config.ZONE,
            'nonce': nonce
        },
        expires_delta=datetime.timedelta(minutes=duration))
    # NOTE(mikal): the token itself must never appear in the event. An
    # event is readable by anyone who can read the namespace, and a JWT
    # in an event log is a credential anyone reading it can replay until
    # it expires.
    ns.add_event(
        EVENT_TYPE_AUDIT, 'token created from key',
        extra={
            'keyname': keyname,
            'nonce': nonce
        })
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
