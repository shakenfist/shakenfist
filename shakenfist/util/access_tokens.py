import datetime

from flask_jwt_extended import create_access_token

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT


def create_token(ns, keyname, nonce, duration=config.API_TOKEN_DURATION):
    token = create_access_token(
        identity=[ns.uuid, keyname],
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
