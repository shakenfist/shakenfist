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
    MARIADB_GATEWAY_HOSTS: list[str] = ['192.168.1.1']
    MARIADB_GATEWAY_PORT: int = 13005
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
