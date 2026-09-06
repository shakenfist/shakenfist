# Copyright 2019 Michael Still and contributors
"""Key management for the Kerbside VDI console token signing key.

This module owns the one place in Shaken Fist that understands the
asymmetric key material Kerbside VDI console tokens are signed with.
Phase 2's minting endpoint and the ``sf-ctl`` ensure/rotate commands
all go through these helpers; nothing else parses the stored row.

The material lives in a single ``cluster_config`` row named
``KERBSIDE_JWT_SIGNING_KEY`` holding this JSON value::

    {
      "active_kid": "3f2a9c1e",
      "keys": [
        {"kid": "3f2a9c1e",
         "private_pem": "-----BEGIN PRIVATE KEY-----...",
         "public_pem": "-----BEGIN PUBLIC KEY-----...",
         "created": 1789000000}
      ]
    }

Schema notes:

* ``keys`` is newest-first and capped at two entries (the current key
  plus the immediately previous one). Rotation prepends a fresh key,
  marks it active and trims the tail. The two-key window exists so a
  rotation never invalidates tokens that are still in flight: verifiers
  (Kerbside) accept any published key, and only the active one is ever
  used to sign new tokens. The third-oldest key is dropped, so tokens
  signed by it become unverifiable once it ages out.
* ``kid`` is ``uuid.uuid4().hex[:8]`` and doubles as the JWT ``kid``
  header. ``created`` is an epoch-seconds integer.
* Keys are Ed25519. The corresponding JWT ``alg`` value is ``EdDSA``.
  Private keys are serialised as unencrypted PKCS8 PEM and public keys
  as SubjectPublicKeyInfo PEM.

Private key material must never be logged, evented, or served. The
public view helper deliberately strips every private member; only kid
values are safe to log.
"""

import json
import time
import uuid
from typing import Any
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
import jwt
from shakenfist_utilities import logs

LOG, _ = logs.setup(__name__)

# The cluster_config row name. Deliberately ends in _KEY so sf-ctl's
# SECRET_CONFIG_KEY_RE masks it in show-config output.
SIGNING_KEY_CONFIG_NAME = 'KERBSIDE_JWT_SIGNING_KEY'

# JWT algorithm identifier for Ed25519.
SIGNING_ALG = 'EdDSA'

# The rolling window of published keys: the active key plus one
# previous key so in-flight tokens survive a rotation.
MAX_PUBLISHED_KEYS = 2


class SigningKeyError(Exception):
    """The stored signing material is missing or internally inconsistent."""


def _material_members(material: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Return material's ``(active_kid, keys)`` members.

    A stored row that is valid JSON but lacks either member is corrupt.
    Raise the module's SigningKeyError, which callers already handle,
    rather than letting a bare KeyError escape to them.
    """
    try:
        return material['active_kid'], material['keys']
    except KeyError as e:
        raise SigningKeyError(
            f'stored signing material is missing the {e.args[0]!r} member') from e


def generate_keypair() -> dict[str, Any]:
    """Generate a fresh Ed25519 keypair as a serialised key entry.

    Returns a dict with ``kid``, ``private_pem`` (unencrypted PKCS8
    PEM), ``public_pem`` (SubjectPublicKeyInfo PEM) and ``created``
    (epoch seconds).
    """
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode('utf-8')
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode('utf-8')

    return {
        'kid': uuid.uuid4().hex[:8],
        'private_pem': private_pem,
        'public_pem': public_pem,
        'created': int(time.time()),
    }


def get_signing_material() -> Optional[dict[str, Any]]:
    """Return the stored signing material, or None if the row is absent.

    Reads the ``KERBSIDE_JWT_SIGNING_KEY`` row via
    ``mariadb.get_cluster_config()``. Both the direct and gRPC read
    paths JSON-decode the value before returning it, so in normal
    operation we receive an already-parsed dict. We stay defensive
    against a raw JSON string arriving (for example if the value is
    ever surveyed through a path that does not decode) and parse it.
    """
    # NOTE: imported inline because mariadb imports config and several
    # util submodules at module load; importing it at the top of a util
    # module risks a circular import (see config.load_cluster_config).
    from shakenfist import mariadb

    value = mariadb.get_cluster_config().get(SIGNING_KEY_CONFIG_NAME)
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise SigningKeyError(
            'stored signing material is not a JSON object: '
            f'{type(value).__name__}')
    return value


def ensure_signing_key() -> dict[str, Any]:
    """Return existing signing material, creating it if absent.

    Idempotent: if the row already exists it is returned unchanged. If
    it is absent, a fresh keypair is generated and written. The write
    is a last-writer-wins upsert: cluster_config deliberately has no
    create-if-absent primitive (phase 1 decision 3 of
    PLAN-kerbside-vdi-tokens), so two concurrent creators each write a
    whole new ``{'active_kid': ..., 'keys': [...]}`` value and the
    loser's key is discarded, not merged. Tokens minted with a
    discarded key can never be verified. The row is re-read after the
    write so the caller sees what is stored at that moment, but that
    is a diagnostic courtesy, not a race guarantee. This is only safe
    because the sole caller is ``sf-ctl ensure-kerbside-signing-key``,
    a run-once deploy-time operator action; do not add an API-path
    caller without first building a real create-if-absent primitive.
    """
    material = get_signing_material()
    if material is not None:
        return material

    keypair = generate_keypair()
    value = {'active_kid': keypair['kid'], 'keys': [keypair]}

    # NOTE: inline import, see get_signing_material for rationale.
    from shakenfist import mariadb
    mariadb.set_cluster_config(SIGNING_KEY_CONFIG_NAME, value)

    stored = get_signing_material()
    if stored is None:
        raise SigningKeyError(
            'signing key not present immediately after write')
    LOG.with_fields({'active_kid': stored['active_kid']}).info(
        'Ensured Kerbside VDI token signing key')
    return stored


def rotate_signing_key() -> dict[str, Any]:
    """Rotate the signing key, keeping a two-key publication window.

    Generates a new keypair, prepends it to ``keys``, marks it active,
    and trims ``keys`` to at most two entries (newest first). The row is
    written and then re-read so the caller sees what is actually stored.
    Tokens signed by the dropped (third-oldest) key become unverifiable.
    """
    material = get_signing_material()
    keypair = generate_keypair()

    if material is None:
        existing_keys: list[dict[str, Any]] = []
    else:
        _, existing_keys = _material_members(material)
    keys = ([keypair] + list(existing_keys))[:MAX_PUBLISHED_KEYS]
    value = {'active_kid': keypair['kid'], 'keys': keys}

    # NOTE: inline import, see get_signing_material for rationale.
    from shakenfist import mariadb
    mariadb.set_cluster_config(SIGNING_KEY_CONFIG_NAME, value)

    stored = get_signing_material()
    if stored is None:
        raise SigningKeyError(
            'signing key not present immediately after rotation')
    LOG.with_fields({'active_kid': stored['active_kid']}).info(
        'Rotated Kerbside VDI token signing key')
    return stored


def active_signing_key(material: dict[str, Any]) -> dict[str, Any]:
    """Return the key entry named by ``material['active_kid']``.

    Raises SigningKeyError if either member is absent or the active kid
    is not present in the published keys, both of which mean the stored
    material is corrupt.
    """
    active_kid, keys = _material_members(material)
    for key in keys:
        if key.get('kid') == active_kid:
            return key
    raise SigningKeyError(
        f'active_kid {active_kid} is not present in the published keys')


def mint_console_token(
    instance_uuid: str, namespace: str, *,
    audience: str, issuer: str, duration: int,
) -> dict[str, Any]:
    """Mint a short-lived Ed25519-signed JWT for a VDI console session.

    Signs the token with the active key from ``get_signing_material()``
    and returns ``{'token', 'jti', 'kid', 'expires_at'}``. Raises
    ``SigningKeyError`` if no signing material is configured, so the
    caller can translate that to a clear HTTP error.

    ``audience``, ``issuer`` and ``duration`` are explicit keyword-only
    arguments rather than read from ``config`` here, so this helper stays
    unit-testable without mocking the config singleton. The token and any
    private key material must never be logged.
    """
    material = get_signing_material()
    if material is None:
        raise SigningKeyError('no signing key is configured')
    key = active_signing_key(material)

    iat = int(time.time())
    exp = iat + duration
    jti = uuid.uuid4().hex
    claims = {
        'iss': issuer,
        'aud': audience,
        'sub': instance_uuid,
        'sf:namespace': namespace,
        'iat': iat,
        'exp': exp,
        'jti': jti,
    }

    # On PyJWT 2.x jwt.encode returns a str, so no decode is needed.
    token = jwt.encode(
        claims, key['private_pem'], algorithm=SIGNING_ALG,
        headers={'kid': key['kid']})

    LOG.with_fields(
        {'kid': key['kid'], 'jti': jti, 'sub': instance_uuid}).info(
        'Minted Kerbside VDI console token')

    return {
        'token': token,
        'jti': jti,
        'kid': key['kid'],
        'expires_at': exp,
    }


def public_view(material: dict[str, Any]) -> dict[str, Any]:
    """Return the public-only view of the signing material.

    Produces ``{'active_kid': ..., 'keys': [{'kid', 'alg', 'public_pem',
    'created'}]}`` with no private members. Callers must handle absent
    material (None) before calling this; corrupt material raises
    SigningKeyError.
    """
    active_kid, keys = _material_members(material)
    return {
        'active_kid': active_kid,
        'keys': [
            {
                'kid': key['kid'],
                'alg': SIGNING_ALG,
                'public_pem': key['public_pem'],
                'created': key['created'],
            }
            for key in keys
        ],
    }
