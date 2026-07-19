# Copyright 2026 Michael Still and contributors
#
# Tests for shakenfist.util.vdi_tokens, the Kerbside VDI console token
# signing key manager.

import json
import time
from unittest import mock

import jwt

from shakenfist.tests import base
from shakenfist.util import vdi_tokens


class _FakeClusterConfigStore:
    """An in-memory stand-in for the mariadb cluster_config row.

    Backs mariadb.get_cluster_config/set_cluster_config so
    ensure_signing_key() and rotate_signing_key() round-trip through the
    same fake store, mirroring test_cluster_config.py's mocking style.
    """

    def __init__(self):
        self.values = {}
        self.set_calls = 0

    def get_cluster_config(self):
        return dict(self.values)

    def set_cluster_config(self, name, value):
        self.set_calls += 1
        self.values[name] = value


def _patch_mariadb(store):
    return mock.patch.multiple(
        'shakenfist.mariadb',
        get_cluster_config=mock.Mock(side_effect=store.get_cluster_config),
        set_cluster_config=mock.Mock(side_effect=store.set_cluster_config),
    )


class EnsureSigningKeyTestCase(base.ShakenFistTestCase):
    """Tests for ensure_signing_key()."""

    def setUp(self):
        super().setUp()
        self.store = _FakeClusterConfigStore()
        self.mariadb_patch = _patch_mariadb(self.store)
        self.mariadb_patch.start()
        self.addCleanup(self.mariadb_patch.stop)

    def test_ensure_creates_then_is_idempotent(self):
        first = vdi_tokens.ensure_signing_key()
        self.assertEqual(1, len(first['keys']))
        self.assertEqual(1, self.store.set_calls)

        second = vdi_tokens.ensure_signing_key()
        self.assertEqual(first['active_kid'], second['active_kid'])
        self.assertEqual(1, self.store.set_calls)


class RotateSigningKeyTestCase(base.ShakenFistTestCase):
    """Tests for rotate_signing_key()."""

    def setUp(self):
        super().setUp()
        self.store = _FakeClusterConfigStore()
        self.mariadb_patch = _patch_mariadb(self.store)
        self.mariadb_patch.start()
        self.addCleanup(self.mariadb_patch.stop)

    def test_rotate_keeps_previous_and_caps_at_two(self):
        ensured = vdi_tokens.ensure_signing_key()
        first_kid = ensured['active_kid']

        rotated_once = vdi_tokens.rotate_signing_key()
        self.assertNotEqual(first_kid, rotated_once['active_kid'])
        kids_once = [key['kid'] for key in rotated_once['keys']]
        self.assertIn(first_kid, kids_once)
        self.assertEqual(2, len(rotated_once['keys']))

        rotated_twice = vdi_tokens.rotate_signing_key()
        self.assertEqual(2, len(rotated_twice['keys']))
        kids_twice = [key['kid'] for key in rotated_twice['keys']]
        self.assertNotIn(first_kid, kids_twice)


class SigningKeyCryptoTestCase(base.ShakenFistTestCase):
    """Tests for the actual Ed25519 / JWT signing contract."""

    def setUp(self):
        super().setUp()
        self.store = _FakeClusterConfigStore()
        self.mariadb_patch = _patch_mariadb(self.store)
        self.mariadb_patch.start()
        self.addCleanup(self.mariadb_patch.stop)

    def test_token_signed_by_active_key_verifies(self):
        vdi_tokens.ensure_signing_key()
        material = vdi_tokens.get_signing_material()
        active = vdi_tokens.active_signing_key(material)

        # A fixed far-future expiry avoids any dependency on wall-clock
        # timing during the test run.
        payload = {'sub': 'test-subject', 'exp': 4102444800}
        token = jwt.encode(
            payload, active['private_pem'], algorithm=vdi_tokens.SIGNING_ALG,
            headers={'kid': active['kid']})

        decoded = jwt.decode(
            token, active['public_pem'], algorithms=[vdi_tokens.SIGNING_ALG],
            options={'verify_aud': False})
        self.assertEqual('test-subject', decoded['sub'])

    def test_rotated_out_key_fails_to_verify(self):
        vdi_tokens.ensure_signing_key()
        first_material = vdi_tokens.get_signing_material()
        first_active = vdi_tokens.active_signing_key(first_material)
        aged_out_public_pem = first_active['public_pem']

        # Two rotations push the original key out of the two-key window.
        vdi_tokens.rotate_signing_key()
        vdi_tokens.rotate_signing_key()

        current_material = vdi_tokens.get_signing_material()
        current_active = vdi_tokens.active_signing_key(current_material)

        payload = {'sub': 'test-subject', 'exp': 4102444800}
        token = jwt.encode(
            payload, current_active['private_pem'],
            algorithm=vdi_tokens.SIGNING_ALG,
            headers={'kid': current_active['kid']})

        self.assertRaises(
            jwt.exceptions.InvalidSignatureError,
            jwt.decode, token, aged_out_public_pem,
            algorithms=[vdi_tokens.SIGNING_ALG],
            options={'verify_aud': False})


class PublicViewTestCase(base.ShakenFistTestCase):
    """Tests for public_view()."""

    def test_no_private_material_leaks(self):
        material = {
            'active_kid': 'abcd1234',
            'keys': [
                {
                    'kid': 'abcd1234',
                    'private_pem': '-----BEGIN PRIVATE KEY-----\nfoo\n'
                                   '-----END PRIVATE KEY-----\n',
                    'public_pem': '-----BEGIN PUBLIC KEY-----\nbar\n'
                                  '-----END PUBLIC KEY-----\n',
                    'created': int(time.time()),
                },
            ],
        }

        view = vdi_tokens.public_view(material)
        serialised = json.dumps(view)

        self.assertNotIn('private', serialised.lower())
        self.assertNotIn('PRIVATE KEY', serialised)

        for key in view['keys']:
            self.assertEqual(
                {'kid', 'alg', 'public_pem', 'created'}, set(key.keys()))
            self.assertEqual(vdi_tokens.SIGNING_ALG, key['alg'])


class ActiveSigningKeyTestCase(base.ShakenFistTestCase):
    """Tests for active_signing_key()."""

    def test_raises_on_corrupt_material(self):
        material = {
            'active_kid': 'missing',
            'keys': [
                {'kid': 'other', 'private_pem': 'x', 'public_pem': 'y',
                 'created': 1},
            ],
        }

        self.assertRaises(
            vdi_tokens.SigningKeyError,
            vdi_tokens.active_signing_key, material)
