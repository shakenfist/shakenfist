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
    it is absent, a fresh keypair is generated, written, and then the
    row is re-read so that a concurrent writer who won the race is the
    one whose material we return (there is no create-if-absent
    primitive for cluster_config).
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

    existing_keys = list(material['keys']) if material is not None else []
    keys = ([keypair] + existing_keys)[:MAX_PUBLISHED_KEYS]
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

    Raises SigningKeyError if the active kid is not present in the
    published keys, which would mean the stored material is corrupt.
    """
    active_kid = material['active_kid']
    keys: list[dict[str, Any]] = material['keys']
    for key in keys:
        if key.get('kid') == active_kid:
            return key
    raise SigningKeyError(
        f'active_kid {active_kid} is not present in the published keys')


def public_view(material: dict[str, Any]) -> dict[str, Any]:
    """Return the public-only view of the signing material.

    Produces ``{'active_kid': ..., 'keys': [{'kid', 'alg', 'public_pem',
    'created'}]}`` with no private members. Callers must handle absent
    material (None) before calling this.
    """
    return {
        'active_kid': material['active_kid'],
        'keys': [
            {
                'kid': key['kid'],
                'alg': SIGNING_ALG,
                'public_pem': key['public_pem'],
                'created': key['created'],
            }
            for key in material['keys']
        ],
    }
