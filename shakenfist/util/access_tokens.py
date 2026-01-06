import datetime

from flask_jwt_extended import create_access_token
from flask_jwt_extended import get_jwt_identity

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.exceptions import CannotParseJWTIdentity


def create_token(ns, keyname, nonce, duration=config.API_TOKEN_DURATION):
    # NOTE(mikal): the "identity" here must be a string, which was not always
    # true for tokens we issued.
    token = create_access_token(
        identity=f'{ns.uuid}:{keyname}',
        additional_claims={
            'iss': config.ZONE,
            'nonce': nonce
        },
        expires_delta=datetime.timedelta(minutes=duration))
    ns.add_event(
        EVENT_TYPE_AUDIT, 'token created from key',
        extra={
            'keyname': keyname,
            'nonce': nonce,
            'token': token
        })
    return {
        'access_token': token,
        'token_type': 'Bearer',
        'expires_in': duration * 60
    }


def parse_jwt_identity():
    ident_string = get_jwt_identity()
    ident = ident_string.split(':')
    if len(ident) != 2:
        raise CannotParseJWTIdentity(f'Cannot parse identity "{ident_string}"')
    return ident


def request_namespace():
    return parse_jwt_identity()[0]
