# Tests for the mariadb blob hash functions.
#
# This module tests:
# - _direct_upsert_blob_hash() creating and updating hash records
# - _migrate_etcd_blob_hashes() migration function
#
# Note: _direct_delete_blob_hashes() and _direct_get_blob_hashes() are
# difficult to unit test due to SQLAlchemy table object requirements.
# These are tested through integration tests instead.

from unittest import mock

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.schema.blob_hash import BlobHash
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    DATABASE_NODE_IP: str = '192.168.1.1'
    DATABASE_API_PORT: int = 13005
    DATABASE_USE_DIRECT_ETCD: bool = False
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()


class UpsertBlobHashTestCase(base.ShakenFistTestCase):
    """Tests for _direct_upsert_blob_hash() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_upsert_creates_new_hash(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        blob_hash = BlobHash(
            blob_uuid='test-uuid-1234',
            node='sf-1',
            algorithm='sha512',
            hash_value='abc123',
            file_size=1024,
            computed_at=1234567890.0,
            last_verified_at=1234567890.0,
            verification_status='valid',
            error_message=None
        )

        result = mariadb._direct_upsert_blob_hash(blob_hash)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_upsert_returns_false_on_error(self, mock_get_engine):
        from sqlalchemy.exc import OperationalError
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'statement', {}, Exception('DB error'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        blob_hash = BlobHash(
            blob_uuid='test-uuid-1234',
            node='sf-1',
            algorithm='sha512',
            hash_value='abc123',
            file_size=1024,
            computed_at=1234567890.0,
            last_verified_at=1234567890.0,
            verification_status='valid',
            error_message=None
        )

        result = mariadb._direct_upsert_blob_hash(blob_hash)

        self.assertFalse(result)


class MigrateEtcdBlobHashesTestCase(base.ShakenFistTestCase):
    """Tests for _migrate_etcd_blob_hashes() migration function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb.upsert_blob_hash')
    @mock.patch('shakenfist.etcd.delete')
    @mock.patch('shakenfist.etcd.get')
    @mock.patch('shakenfist.etcd.get_all')
    def test_migrate_no_blobs(
            self, mock_get_all, mock_get, mock_delete, mock_upsert):
        mock_get_all.return_value = []
        mock_engine = mock.MagicMock()

        result = mariadb._migrate_etcd_blob_hashes(mock_engine)

        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 0)
        mock_upsert.assert_not_called()
        mock_delete.assert_not_called()

    @mock.patch('shakenfist.mariadb.upsert_blob_hash')
    @mock.patch('shakenfist.etcd.delete')
    @mock.patch('shakenfist.etcd.get')
    @mock.patch('shakenfist.etcd.get_all')
    def test_migrate_blob_without_checksums(
            self, mock_get_all, mock_get, mock_delete, mock_upsert):
        mock_get_all.return_value = [
            ('/sf/object/blob/test-uuid', {'size': 1024})
        ]
        mock_get.return_value = None  # No checksums attribute
        mock_engine = mock.MagicMock()

        result = mariadb._migrate_etcd_blob_hashes(mock_engine)

        self.assertEqual(result['migrated_count'], 0)
        mock_upsert.assert_not_called()
        mock_delete.assert_not_called()

    @mock.patch('shakenfist.mariadb.upsert_blob_hash')
    @mock.patch('shakenfist.etcd.delete')
    @mock.patch('shakenfist.etcd.get')
    @mock.patch('shakenfist.etcd.get_all')
    def test_migrate_blob_with_checksums(
            self, mock_get_all, mock_get, mock_delete, mock_upsert):
        mock_get_all.return_value = [
            ('/sf/object/blob/test-uuid', {'size': 1024})
        ]
        mock_get.return_value = {
            'sha512': 'abc123',
            'nodes': {'sf-1': 1234567890.0}
        }
        mock_upsert.return_value = True
        mock_engine = mock.MagicMock()

        result = mariadb._migrate_etcd_blob_hashes(mock_engine)

        self.assertEqual(result['migrated_count'], 1)
        self.assertEqual(result['error_count'], 0)
        mock_upsert.assert_called_once()
        mock_delete.assert_called_once_with(
            'attribute/blob', 'test-uuid', 'checksums')

    @mock.patch('shakenfist.mariadb.upsert_blob_hash')
    @mock.patch('shakenfist.etcd.delete')
    @mock.patch('shakenfist.etcd.get')
    @mock.patch('shakenfist.etcd.get_all')
    def test_migrate_partial_failure_retains_etcd(
            self, mock_get_all, mock_get, mock_delete, mock_upsert):
        mock_get_all.return_value = [
            ('/sf/object/blob/test-uuid', {'size': 1024})
        ]
        mock_get.return_value = {
            'sha512': 'abc123',
            'sha256': 'def456',
            'nodes': {'sf-1': 1234567890.0}
        }
        # First upsert succeeds, second fails with exception
        mock_upsert.side_effect = [True, Exception('DB error')]
        mock_engine = mock.MagicMock()

        result = mariadb._migrate_etcd_blob_hashes(mock_engine)

        # Should not delete etcd attribute on partial failure
        mock_delete.assert_not_called()
        self.assertEqual(result['error_count'], 1)

    @mock.patch('shakenfist.mariadb.upsert_blob_hash')
    @mock.patch('shakenfist.etcd.delete')
    @mock.patch('shakenfist.etcd.get')
    @mock.patch('shakenfist.etcd.get_all')
    def test_migrate_multiple_algorithms_and_nodes(
            self, mock_get_all, mock_get, mock_delete, mock_upsert):
        mock_get_all.return_value = [
            ('/sf/object/blob/test-uuid', {'size': 1024})
        ]
        mock_get.return_value = {
            'sha512': 'abc123',
            'sha256': 'def456',
            'nodes': {'sf-1': 1234567890.0, 'sf-2': 1234567891.0}
        }
        mock_upsert.return_value = True
        mock_engine = mock.MagicMock()

        result = mariadb._migrate_etcd_blob_hashes(mock_engine)

        # 2 algorithms x 2 nodes = 4 hash records
        self.assertEqual(result['migrated_count'], 4)
        self.assertEqual(mock_upsert.call_count, 4)
        mock_delete.assert_called_once()
