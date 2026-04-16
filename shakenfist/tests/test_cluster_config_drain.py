# Copyright 2026 Michael Still and contributors
#
# Tests for the data migration that drains /sf/config from etcd
# into the MariaDB cluster_config table.

from unittest import mock

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd
from shakenfist.util import json as util_json


class ClusterConfigDrainTestCase(base.ShakenFistTestCase):
    """Exercises _migrate_etcd_cluster_config."""

    def setUp(self):
        super().setUp()

        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

        self.config_store = {}

        def _fake_set(key_name, value):
            self.config_store[key_name] = value

        self.set_patch = mock.patch(
            'shakenfist.mariadb._direct_set_cluster_config',
            side_effect=_fake_set)
        self.set_patch.start()
        self.addCleanup(self.set_patch.stop)

    def _seed_config(self, config_dict):
        """Write a JSON blob to /sf/config in MockEtcd."""
        self.mock_etcd.db['/sf/config'] = (
            util_json.json_dump(config_dict).encode())

    def test_happy_path(self):
        """A multi-key config blob is split into individual rows."""
        self._seed_config({
            'AUTH_SECRET_SEED': 'secret123',
            'DNS_SERVER': '8.8.8.8',
            'RAM_SYSTEM_RESERVATION': 5,
            'HTTP_PROXY': '',
        })

        result = mariadb._migrate_etcd_cluster_config(None)

        self.assertEqual(result['migrated_count'], 4)
        self.assertEqual(result['error_count'], 0)

        self.assertEqual(
            self.config_store['AUTH_SECRET_SEED'], 'secret123')
        self.assertEqual(
            self.config_store['DNS_SERVER'], '8.8.8.8')
        self.assertEqual(
            self.config_store['RAM_SYSTEM_RESERVATION'], 5)
        self.assertEqual(
            self.config_store['HTTP_PROXY'], '')

        # etcd key should be deleted
        self.assertNotIn('/sf/config', self.mock_etcd.db)

    def test_empty_config_is_noop(self):
        """No /sf/config key means nothing to migrate."""
        result = mariadb._migrate_etcd_cluster_config(None)
        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 0)

    def test_empty_dict_is_noop(self):
        """An empty dict /sf/config is a no-op (treated as not found)."""
        self._seed_config({})

        result = mariadb._migrate_etcd_cluster_config(None)
        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 0)

    def test_idempotency(self):
        """Running the migration twice: second is a no-op."""
        self._seed_config({'DNS_SERVER': '1.1.1.1'})

        result1 = mariadb._migrate_etcd_cluster_config(None)
        self.assertEqual(result1['migrated_count'], 1)

        # etcd key was deleted, so second run finds nothing
        result2 = mariadb._migrate_etcd_cluster_config(None)
        self.assertEqual(result2['migrated_count'], 0)

    def test_non_dict_is_skipped(self):
        """A non-dict /sf/config is skipped with an error."""
        self.mock_etcd.db['/sf/config'] = b'"just a string"'

        result = mariadb._migrate_etcd_cluster_config(None)
        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 1)

        # Key should still be in etcd
        self.assertIn('/sf/config', self.mock_etcd.db)

    def test_preserves_value_types(self):
        """Bool, int, float, and string values are preserved."""
        self._seed_config({
            'BOOL_VAL': True,
            'INT_VAL': 42,
            'FLOAT_VAL': 3.14,
            'STR_VAL': 'hello',
        })

        result = mariadb._migrate_etcd_cluster_config(None)
        self.assertEqual(result['migrated_count'], 4)

        self.assertIs(self.config_store['BOOL_VAL'], True)
        self.assertEqual(self.config_store['INT_VAL'], 42)
        self.assertAlmostEqual(
            self.config_store['FLOAT_VAL'], 3.14)
        self.assertEqual(self.config_store['STR_VAL'], 'hello')
