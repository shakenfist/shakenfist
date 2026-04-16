# Copyright 2026 Michael Still and contributors
#
# Tests for the data migration that drains residual /sflocks/*
# etcd keys into the MariaDB cluster_locks table.
#
# These tests seed MockEtcd's fake etcd dict and patch
# _direct_acquire_cluster_lock so the migration writes into an
# in-memory store instead of a real MariaDB.

import time
from unittest import mock

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd
from shakenfist.util import json as util_json


HOLDER_A = {
    'node': 'node1',
    'pid': 100,
    'thread': 11111,
    'line': 'instance.py:42',
    'operation': 'create',
    'id': 'lock-aaa',
}

HOLDER_B = {
    'node': 'node2',
    'pid': 200,
    'thread': 22222,
    'line': 'cluster.py:57',
    'operation': 'maintenance',
    'id': 'lock-bbb',
}


class ClusterLocksDrainTestCase(base.ShakenFistTestCase):
    """Exercises _migrate_etcd_cluster_locks."""

    def setUp(self):
        super().setUp()

        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

        self.acquired_locks = {}

        def _fake_acquire(lock_key, holder_json, node_uuid,
                          pid, lock_id, now):
            if lock_key in self.acquired_locks:
                return False
            self.acquired_locks[lock_key] = {
                'holder_json': holder_json,
                'node_uuid': node_uuid,
                'pid': pid,
                'lock_id': lock_id,
                'now': now,
            }
            return True

        self.acquire_patch = mock.patch(
            'shakenfist.mariadb._direct_acquire_cluster_lock',
            side_effect=_fake_acquire)
        self.acquire_patch.start()
        self.addCleanup(self.acquire_patch.stop)

    def _seed_etcd(self, path, data):
        """Write a json-encoded entry to MockEtcd's fake store."""
        self.mock_etcd.db[path] = util_json.json_dump(data).encode()

    def test_happy_path_two_locks(self):
        """Two well-formed locks are migrated and etcd keys deleted."""
        self._seed_etcd(
            '/sflocks/instance/parent/uuid1', HOLDER_A)
        self._seed_etcd('/sflocks/cluster/', HOLDER_B)

        result = mariadb._migrate_etcd_cluster_locks(None)

        self.assertEqual(result['migrated_count'], 2)
        self.assertEqual(result['error_count'], 0)

        # Verify keys were stripped of /sflocks/ prefix
        self.assertIn('instance/parent/uuid1', self.acquired_locks)
        self.assertIn('cluster/', self.acquired_locks)

        # Verify holder data was passed through
        acq = self.acquired_locks['instance/parent/uuid1']
        self.assertEqual(acq['holder_json'], HOLDER_A)
        self.assertEqual(acq['node_uuid'], 'node1')
        self.assertEqual(acq['pid'], 100)
        self.assertEqual(acq['lock_id'], 'lock-aaa')

        # Verify etcd keys were deleted
        self.assertNotIn('/sflocks/instance/parent/uuid1',
                         self.mock_etcd.db)
        self.assertNotIn('/sflocks/cluster/', self.mock_etcd.db)

    def test_idempotency(self):
        """Running the migration twice does not duplicate rows."""
        self._seed_etcd(
            '/sflocks/instance/parent/uuid1', HOLDER_A)

        result1 = mariadb._migrate_etcd_cluster_locks(None)
        self.assertEqual(result1['migrated_count'], 1)

        # Second run: etcd keys were deleted, so nothing to migrate
        result2 = mariadb._migrate_etcd_cluster_locks(None)
        self.assertEqual(result2['migrated_count'], 0)
        self.assertEqual(result2['error_count'], 0)

    def test_malformed_payload_skipped(self):
        """A key with a non-dict value is skipped and left in etcd."""
        self.mock_etcd.db['/sflocks/bad/key/'] = b'"just a string"'

        result = mariadb._migrate_etcd_cluster_locks(None)

        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 1)
        # Key should still be in etcd
        self.assertIn('/sflocks/bad/key/', self.mock_etcd.db)

    def test_collision_does_not_error(self):
        """If the row already exists in MariaDB, it is skipped cleanly."""
        self._seed_etcd(
            '/sflocks/instance/parent/uuid1', HOLDER_A)

        # Pre-populate the in-memory store so the acquire returns False
        self.acquired_locks['instance/parent/uuid1'] = {
            'holder_json': HOLDER_A,
            'node_uuid': 'node1',
            'pid': 100,
            'lock_id': 'lock-aaa',
            'now': time.time(),
        }

        result = mariadb._migrate_etcd_cluster_locks(None)

        # Not counted as migrated (row existed) but also not an error
        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 0)

    def test_empty_etcd_is_noop(self):
        """If there are no /sflocks/ keys, the migration is a no-op."""
        result = mariadb._migrate_etcd_cluster_locks(None)
        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 0)

    def test_missing_holder_fields_use_defaults(self):
        """A holder with missing fields uses safe defaults."""
        minimal_holder = {'id': 'lock-min'}
        self._seed_etcd('/sflocks/test/key/', minimal_holder)

        result = mariadb._migrate_etcd_cluster_locks(None)

        self.assertEqual(result['migrated_count'], 1)
        acq = self.acquired_locks['test/key/']
        self.assertEqual(acq['node_uuid'], '')
        self.assertEqual(acq['pid'], 0)
        self.assertEqual(acq['lock_id'], 'lock-min')
