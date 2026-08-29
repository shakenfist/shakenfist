# Copyright 2026 Michael Still and contributors

"""Tests for the read-through static-object cache in mariadb.py.

ObjectCacheCoreTestCase exercises the cache primitives, and
ObjectCacheResidencyTestCase the bound on how much is retained.

ObjectCacheWiringTestCase covers the per-type wiring -- which
get_<type>() reads the cache and which writers evict -- as end-to-end
read-cache-write-evict cycles for representative types (blob, ipam,
instance, namespace). It is not exhaustive over the eleven cached
types; see issue #3943.

ObjectCacheEveryTypeEvictsTestCase is the exhaustive half of that: it
derives the type list from the source rather than restating it, so a
twelfth cached type cannot be added with a reader and no eviction hook.
It is type-level, which is why it does not close #3943. This module
used to claim the wiring was "covered in the object-type test modules";
it was not covered anywhere, and the phase 8 push audit found that the
claim was what stopped anyone looking.
"""

import pathlib
import re
import time
import uuid
from unittest import mock

import fixtures

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


class ObjectCacheResidencyTestCase(base.ShakenFistTestCase):
    """The bound on how much the cache retains, as opposed to for how long.

    TTL expiry is lazy -- an entry is reclaimed only when its own key is
    read again -- so these assert the property TTL does not supply.
    """

    def setUp(self):
        super().setUp()
        mariadb._OBJECT_CACHE.clear()
        self.addCleanup(mariadb._OBJECT_CACHE.clear)

    def _fill(self, count, ttl=300):
        for i in range(count):
            mariadb._object_cache_put('instance', f'obj-{i}', object(), ttl=ttl)

    @mock.patch('shakenfist.config.config.OBJECT_CACHE_MAX_ENTRIES', 100)
    def test_the_cache_stops_growing_at_the_cap(self):
        # Without the bound this reaches 500, which is the defect: entries
        # that are never read again are never reclaimed.
        self._fill(500)
        self.assertLessEqual(len(mariadb._OBJECT_CACHE), 100)

    @mock.patch('shakenfist.config.config.OBJECT_CACHE_MAX_ENTRIES', 100)
    def test_expired_entries_are_dropped_before_live_ones(self):
        # One clock for both fixtures, so "expired" means expired relative to
        # the same now the trim uses. Mocking time.monotonic for only half the
        # entries puts them on a different timeline and the test measures
        # nothing.
        clock = [1000.0]
        with mock.patch('time.monotonic', side_effect=lambda: clock[0]):
            for i in range(90):
                mariadb._object_cache_put(
                    'blob', f'stale-{i}', object(), ttl=1)

            # Every blob entry is now long expired, and none has been read
            # again -- which is exactly the state lazy expiry never reclaims.
            clock[0] = 2000.0
            for i in range(30):
                mariadb._object_cache_put(
                    'instance', f'obj-{i}', object(), ttl=300)

        stale = [k for k in mariadb._OBJECT_CACHE if k[0] == 'blob']
        live = [k for k in mariadb._OBJECT_CACHE if k[0] == 'instance']
        self.assertEqual([], stale)
        self.assertEqual(30, len(live))

    @mock.patch('shakenfist.config.config.OBJECT_CACHE_MAX_ENTRIES', 100)
    def test_the_soonest_to_expire_are_evicted_first(self):
        # All live, so the trim has to choose between them. Inserted in
        # increasing order of lifetime, so the survivors should be a suffix:
        # the entries a reader is least likely to want again go first.
        clock = [1000.0]
        with mock.patch('time.monotonic', side_effect=lambda: clock[0]):
            for i in range(120):
                mariadb._object_cache_put(
                    'instance', f'obj-{i}', object(), ttl=100 + i)

        survivors = sorted(
            int(k[1].split('-')[1]) for k in mariadb._OBJECT_CACHE)
        self.assertLessEqual(len(survivors), 100)
        self.assertGreater(len(survivors), 0)
        # A contiguous suffix of the insertion order, i.e. the longest-lived.
        self.assertEqual(
            list(range(120 - len(survivors), 120)), survivors,
            'the trim kept entries other than the longest-lived ones')

    @mock.patch('shakenfist.config.config.OBJECT_CACHE_MAX_ENTRIES', 0)
    def test_a_zero_cap_disables_the_bound(self):
        self._fill(300)
        self.assertEqual(300, len(mariadb._OBJECT_CACHE))

    @mock.patch('shakenfist.config.config.OBJECT_CACHE_MAX_ENTRIES', 100)
    def test_the_occupancy_gauge_tracks_the_lazy_expiry_read_path(self):
        # The operator guide sends operators to this gauge for occupancy, and
        # a read is where most entries actually leave the cache -- expiry is
        # lazy, so an entry nothing reads again is reclaimed by the next read
        # of its own key. A gauge moved only by put and evict therefore drifts
        # high forever in exactly the workload the cache exists for.
        clock = [1000.0]
        with mock.patch('time.monotonic', side_effect=lambda: clock[0]):
            for i in range(10):
                mariadb._object_cache_put(
                    'instance', f'obj-{i}', object(), ttl=1)
            self.assertEqual(10, mariadb.OBJECT_CACHE_SIZE._value.get())

            clock[0] = 2000.0
            for i in range(10):
                mariadb._object_cache_get('instance', f'obj-{i}')

        self.assertEqual(0, len(mariadb._OBJECT_CACHE))
        self.assertEqual(
            0, mariadb.OBJECT_CACHE_SIZE._value.get(),
            'the gauge kept counting entries the read path had dropped')

    @mock.patch('shakenfist.config.config.OBJECT_CACHE_MAX_ENTRIES', 100)
    def test_a_full_scan_is_amortised_when_one_entry_expires_per_insert(self):
        # The steady state the TRIM_TARGET amortisation exists for. Both O(n)
        # passes in the trim walk the dict via items(), so counting items()
        # calls counts full scans. If the expired sweep trims back to the cap
        # rather than to the target, freeing a single entry leaves the cache
        # at exactly the cap, the very next insert is over again, and every
        # single put performs a full scan while holding the cache lock --
        # which is the amortisation defeated rather than applied.
        class CountingDict(dict):
            scans = 0

            def items(self):
                CountingDict.scans += 1
                return super().items()

        cache = CountingDict()
        self.useFixture(
            fixtures.MockPatchObject(mariadb, '_OBJECT_CACHE', cache))

        clock = [1000.0]
        with mock.patch('time.monotonic', side_effect=lambda: clock[0]):
            # Staggered expiries, so that advancing the clock by one second
            # per insert expires exactly one entry per insert.
            for i in range(100):
                mariadb._object_cache_put(
                    'instance', f'old-{i}', object(), ttl=50 + i)

            CountingDict.scans = 0
            for i in range(200):
                clock[0] = 1050.0 + i
                mariadb._object_cache_put(
                    'instance', f'new-{i}', object(), ttl=10000)

        # Trimming only to the cap scans on all 200 inserts. Trimming to
        # TRIM_TARGET frees cap-target entries per pass, so a pass runs at
        # most once every cap-target inserts.
        self.assertLess(
            CountingDict.scans, 60,
            'the cache performed a full scan on nearly every insert')
        self.assertGreater(
            CountingDict.scans, 0,
            'the trim never ran, so this test proves nothing')


class ObjectCacheEveryTypeEvictsTestCase(base.ShakenFistTestCase):
    """Every cached object type must have an eviction hook.

    Derived from the source rather than from a hand-written list: a new
    cached type with a reader and no writer-side evict fails this without
    anyone remembering to extend the test.

    Deliberately type-level, not per-writer: a type with two writers and
    one eviction hook still passes. Closing that would mean matching
    writers to evictions, which needs more than a regex. Said out loud
    because the docstring this module used to carry claimed coverage it
    did not have, and that claim is what stopped anyone looking.
    """

    def _cached_types(self, helper):
        pattern = re.compile(
            r"_object_cache_%s\(\s*'([a-z_]+)'" % helper)
        source = pathlib.Path(mariadb.__file__).read_text()
        return set(pattern.findall(source))

    def test_every_type_that_is_cached_is_also_evicted(self):
        cached = self._cached_types('put')
        evicted = self._cached_types('evict')

        # Guard against the regex silently matching nothing, which would
        # make this pass vacuously.
        self.assertIn('instance', cached)
        self.assertIn('ipam', cached)
        self.assertGreaterEqual(len(cached), 11)

        self.assertEqual(
            set(), cached - evicted,
            'object types cached with no eviction hook: a writer for one of '
            'these would serve a stale row until its TTL expired')
