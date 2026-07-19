# Copyright 2026 Michael Still and contributors

"""Tests for the read-through static-object cache core in mariadb.py.

Only the cache primitives are exercised here; the per-type wiring
(which get_<type>() reads the cache and which update_/delete_<type>()
evict) is covered in the object-type test modules.
"""

import time
import uuid

from shakenfist import mariadb
from shakenfist.tests import base


class ObjectCacheCoreTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # The cache is process-global; isolate each test.
        mariadb._OBJECT_CACHE.clear()
        self.addCleanup(mariadb._OBJECT_CACHE.clear)
        self.key = uuid.uuid4()

    def test_put_then_get_is_a_hit(self):
        model = object()
        mariadb._object_cache_put('instance', self.key, model, ttl=300)
        self.assertIs(model, mariadb._object_cache_get('instance', self.key))

    def test_get_absent_is_a_miss(self):
        self.assertIsNone(mariadb._object_cache_get('instance', self.key))

    def test_wrong_type_does_not_collide(self):
        model = object()
        mariadb._object_cache_put('instance', self.key, model, ttl=300)
        # Same key, different object type: distinct cache entry.
        self.assertIsNone(mariadb._object_cache_get('blob', self.key))

    def test_expired_entry_is_dropped_on_access(self):
        model = object()
        mariadb._object_cache_put('node', self.key, model, ttl=300)
        # Force the entry to be in the past without sleeping.
        ck = mariadb._object_cache_key('node', self.key)
        _, m = mariadb._OBJECT_CACHE[ck]
        mariadb._OBJECT_CACHE[ck] = (time.monotonic() - 1, m)
        self.assertIsNone(mariadb._object_cache_get('node', self.key))
        # A miss on an expired entry must also remove it.
        self.assertNotIn(ck, mariadb._OBJECT_CACHE)

    def test_ttl_zero_disables_caching(self):
        mariadb._object_cache_put('node', self.key, object(), ttl=0)
        self.assertNotIn(
            mariadb._object_cache_key('node', self.key), mariadb._OBJECT_CACHE)
        self.assertIsNone(mariadb._object_cache_get('node', self.key))

    def test_none_is_never_cached(self):
        # Guards against negative caching masking a later create/delete.
        mariadb._object_cache_put('node', self.key, None, ttl=300)
        self.assertNotIn(
            mariadb._object_cache_key('node', self.key), mariadb._OBJECT_CACHE)

    def test_uuid_and_string_key_forms_collide(self):
        # A reader passes a UUID; a writer may evict with the str form. They
        # must address the same entry, or eviction would miss.
        model = object()
        mariadb._object_cache_put('instance', self.key, model, ttl=300)
        mariadb._object_cache_evict('instance', str(self.key))
        self.assertIsNone(mariadb._object_cache_get('instance', self.key))

    def test_evict_removes_entry(self):
        mariadb._object_cache_put('blob', self.key, object(), ttl=300)
        mariadb._object_cache_evict('blob', self.key)
        self.assertIsNone(mariadb._object_cache_get('blob', self.key))

    def test_evict_absent_is_safe(self):
        # Must not raise when there is nothing to evict.
        mariadb._object_cache_evict('blob', self.key)

    def test_string_key_supported(self):
        # Namespace is keyed by name, not a UUID.
        model = object()
        mariadb._object_cache_put('namespace', 'sys', model, ttl=300)
        self.assertIs(model, mariadb._object_cache_get('namespace', 'sys'))
