# Copyright 2026 Michael Still and contributors

"""Tests for the read-through static-object cache in mariadb.py.

ObjectCacheCoreTestCase exercises the cache primitives.
ObjectCacheWiringTestCase covers the per-type wiring -- which
get_<type>() reads the cache and which writers evict -- as end-to-end
read-cache-write-evict cycles for representative types (blob, ipam,
instance, namespace). It is not exhaustive over the eleven cached
types; see issue #3943.
"""

import time
import uuid
from unittest import mock

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


class ObjectCacheWiringTestCase(base.ShakenFistTestCase):
    """End-to-end: the public get_/update_/delete_ functions use the cache."""

    def setUp(self):
        super().setUp()
        mariadb._OBJECT_CACHE.clear()
        self.addCleanup(mariadb._OBJECT_CACHE.clear)
        self.uuid = uuid.uuid4()

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_blob')
    def test_reader_caches_then_writers_evict(self, mock_get, _mock_use):
        model = mock.Mock(uuid=self.uuid)
        mock_get.return_value = model

        # First read hits the database; the second is served from cache.
        self.assertIs(model, mariadb.get_blob(self.uuid))
        self.assertIs(model, mariadb.get_blob(self.uuid))
        self.assertEqual(1, mock_get.call_count)

        # An update evicts (this is the online-upgrade self-heal path), so the
        # next read hits the database again.
        with mock.patch('shakenfist.mariadb._direct_update_blob',
                        return_value=True):
            mariadb.update_blob(model)
        mariadb.get_blob(self.uuid)
        self.assertEqual(2, mock_get.call_count)

        # A delete also evicts, so a dead object is never resurrected.
        with mock.patch('shakenfist.mariadb._direct_delete_blob',
                        return_value=True):
            mariadb.delete_blob(self.uuid)
        mariadb.get_blob(self.uuid)
        self.assertEqual(3, mock_get.call_count)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_ipam')
    def test_ipam_reader_caches_then_writers_evict(self, mock_get, _mock_use):
        # The static IPAM record is immutable (block definition), so get_ipam
        # is cached; update_ipam (version-upgrade persist) and delete_ipam must
        # evict. See issue #3501.
        model = mock.Mock(uuid=self.uuid)
        mock_get.return_value = model

        self.assertIs(model, mariadb.get_ipam(self.uuid))
        self.assertIs(model, mariadb.get_ipam(self.uuid))
        self.assertEqual(1, mock_get.call_count)

        with mock.patch('shakenfist.mariadb._direct_update_ipam',
                        return_value=True):
            mariadb.update_ipam(model)
        mariadb.get_ipam(self.uuid)
        self.assertEqual(2, mock_get.call_count)

        with mock.patch('shakenfist.mariadb._direct_delete_ipam',
                        return_value=True):
            mariadb.delete_ipam(self.uuid)
        mariadb.get_ipam(self.uuid)
        self.assertEqual(3, mock_get.call_count)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_instance')
    def test_instance_reader_caches_then_delete_evicts(
            self, mock_get, _mock_use):
        # Instance static values are immutable after creation, so there is no
        # update_instance: delete_instance is the sole evicting writer, and
        # create_instance relies on misses never being cached (covered by
        # test_missing_row_is_not_cached). The instances table is the hottest
        # write path, so its wiring is pinned per-writer here rather than
        # left to the type-level sweep. See issue #3943.
        model = mock.Mock(uuid=self.uuid)
        mock_get.return_value = model

        self.assertIs(model, mariadb.get_instance(self.uuid))
        self.assertIs(model, mariadb.get_instance(self.uuid))
        self.assertEqual(1, mock_get.call_count)

        with mock.patch('shakenfist.mariadb._direct_delete_instance',
                        return_value=True):
            mariadb.delete_instance(self.uuid)
        mariadb.get_instance(self.uuid)
        self.assertEqual(2, mock_get.call_count)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_namespace')
    def test_namespace_reader_caches_then_writers_evict(
            self, mock_get, _mock_use):
        # Namespace holds trust-relevant static columns and is keyed by name
        # rather than UUID; both of its writers (create and delete) must
        # evict. See issue #3943.
        model = mock.Mock()
        mock_get.return_value = model

        self.assertIs(model, mariadb.get_namespace('testns'))
        self.assertIs(model, mariadb.get_namespace('testns'))
        self.assertEqual(1, mock_get.call_count)

        with mock.patch('shakenfist.mariadb._direct_create_namespace',
                        return_value=True):
            mariadb.create_namespace('testns', 1)
        mariadb.get_namespace('testns')
        self.assertEqual(2, mock_get.call_count)

        with mock.patch('shakenfist.mariadb._direct_delete_namespace',
                        return_value=True):
            mariadb.delete_namespace('testns')
        mariadb.get_namespace('testns')
        self.assertEqual(3, mock_get.call_count)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_blob', return_value=None)
    def test_missing_row_is_not_cached(self, mock_get, _mock_use):
        # A None result must not be cached, or a later create would be masked.
        self.assertIsNone(mariadb.get_blob(self.uuid))
        self.assertIsNone(mariadb.get_blob(self.uuid))
        self.assertEqual(2, mock_get.call_count)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_blob')
    def test_ttl_zero_disables_reader_caching(self, mock_get, _mock_use):
        mock_get.return_value = mock.Mock(uuid=self.uuid)
        with mock.patch.object(mariadb.config, 'OBJECT_CACHE_TTL_MUTABLE', 0):
            mariadb.get_blob(self.uuid)
            mariadb.get_blob(self.uuid)
        self.assertEqual(2, mock_get.call_count)
