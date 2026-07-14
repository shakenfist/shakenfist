# Copyright 2019 Michael Still and contributors
#
# Behaviour-preservation tests for namespace keys, written before the
# phase 2 migration of keys from the namespace_attributes JSON column to
# first-class NamespaceKey objects (see
# docs/plans/PLAN-auth-federation-phase-02-key-objects.md). Every test here
# pins the CURRENT semantics of the anonymous-dict-entry implementation so
# that the later cutover can be proven bit-compatible.
import json
import time
from unittest import mock

from shakenfist.external_api import app as external_api
from shakenfist.namespace import get_api_token
from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NamespaceKeyExpiryTestCase(base.ShakenFistTestCase):
    """Pin Namespace.add_key(expiry=...) storage and the read-time filter.

    Covers brief item (i): add_key stores an expiry and the `keys` accessor
    hides an entry once time.time() has stepped past that expiry
    (namespace.py:165-194).
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

    def test_add_key_stores_expiry(self):
        ns = Namespace.from_db('banana')
        ns.add_key('expkey', 'secret', expiry=2000)

        # The expiry is persisted verbatim in the nonced_keys entry.
        stored = self.mock_mariadb.namespace_attributes['banana'].keys
        self.assertEqual(2000, stored['nonced_keys']['expkey']['expiry'])

    def test_add_key_without_expiry_has_no_expiry(self):
        ns = Namespace.from_db('banana')
        ns.add_key('noexp', 'secret')
        stored = self.mock_mariadb.namespace_attributes['banana'].keys
        self.assertNotIn('expiry', stored['nonced_keys']['noexp'])

    def test_keys_accessor_filters_expired_entry(self):
        ns = Namespace.from_db('banana')
        ns.add_key('expkey', 'secret', expiry=2000)

        # Before the expiry the key is visible through the accessor.
        with mock.patch('shakenfist.namespace.time.time', return_value=1000):
            visible = ns.keys.get('nonced_keys', {})
            self.assertIn('expkey', visible)
            self.assertIn('key1', visible)

        # Once time has stepped past the expiry, the accessor hides it -- but
        # only the expired key; the never-expiring key1 survives.
        with mock.patch('shakenfist.namespace.time.time', return_value=3000):
            visible = ns.keys.get('nonced_keys', {})
            self.assertNotIn('expkey', visible)
            self.assertIn('key1', visible)

        # The filter is read-time only: the entry is still in storage.
        stored = self.mock_mariadb.namespace_attributes['banana'].keys
        self.assertIn('expkey', stored['nonced_keys'])


class GetApiTokenTestCase(base.ShakenFistTestCase):
    """Pin get_api_token's service-key minting and caching.

    Covers brief item (vi): the first call creates a single _service_key_<rand>
    key with an expiry ~300s in the future and mints a Bearer token; a second
    call inside the cache window returns the same token without creating a
    second key (namespace.py:289-314).
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')

        # get_api_token caches in a module-level global; isolate the test.
        import shakenfist.namespace as namespace_mod
        namespace_mod.CACHED_TOKENS.clear()
        self.addCleanup(namespace_mod.CACHED_TOKENS.clear)

        # bcrypt hashing is slow and its value is irrelevant here.
        self.hashpw = mock.patch('bcrypt.hashpw', return_value=b'terminator')
        self.hashpw.start()
        self.addCleanup(self.hashpw.stop)

    def _service_keys(self):
        keys = self.mock_mariadb.namespace_attributes['system'].keys['nonced_keys']
        return [k for k in keys if k.startswith('_service_key_')]

    def test_creates_service_key_and_caches_token(self):
        before = time.time()
        with external_api.app.app_context():
            token1 = get_api_token('http://localhost:13000', namespace='system')
        after = time.time()

        self.assertTrue(token1.startswith('Bearer '))

        # Exactly one _service_key_<rand> key was created.
        service_keys = self._service_keys()
        self.assertEqual(1, len(service_keys))

        # Its expiry is ~300s in the future.
        keys = self.mock_mariadb.namespace_attributes['system'].keys['nonced_keys']
        expiry = keys[service_keys[0]]['expiry']
        self.assertGreaterEqual(expiry, before + 300)
        self.assertLessEqual(expiry, after + 300)

        # A second call within the cache window returns the cached token and
        # does not create a second key.
        with external_api.app.app_context():
            token2 = get_api_token('http://localhost:13000', namespace='system')
        self.assertEqual(token1, token2)
        self.assertEqual(1, len(self._service_keys()))


class NamespaceExternalViewTestCase(base.ShakenFistTestCase):
    """Pin Namespace.external_view() key redaction.

    Covers brief item (vii): external_view() returns `keys` as a list of key
    names, with no hash or nonce material anywhere in the response
    (namespace.py:239-256).
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

        # The hash value is irrelevant; make it deterministic and fast.
        self.hashpw = mock.patch('bcrypt.hashpw', return_value=b'terminator')
        self.hashpw.start()
        self.addCleanup(self.hashpw.stop)

    def test_external_view_lists_names_without_secrets(self):
        ns = Namespace.from_db('banana')
        ns.add_key('deploy', 'anothersecret')

        view = ns.external_view()

        # keys is a plain list of names.
        self.assertIsInstance(view['keys'], list)
        self.assertEqual(sorted(['key1', 'deploy']), sorted(view['keys']))

        # No hash or nonce material appears anywhere in the serialised view.
        stored = self.mock_mariadb.namespace_attributes['banana'].keys['nonced_keys']
        serialised = json.dumps(view)
        for keyname, entry in stored.items():
            self.assertNotIn(entry['key'], serialised)
            self.assertNotIn(entry['nonce'], serialised)
