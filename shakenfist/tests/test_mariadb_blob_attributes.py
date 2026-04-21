# Tests for the mariadb blob attributes functions.
#
# This module tests:
# - _direct_create_blob_attributes() creating attribute records
# - _direct_get_blob_attributes() retrieving attributes
# - _direct_update_blob_attributes() updating attributes
# - _direct_update_blob_last_used() optimized last_used updates
# - _direct_delete_blob_attributes() deleting attribute records
# - _direct_get_expired_blob_uuids() querying expired blobs
# - _migrate_etcd_blob_attributes() migration function

from unittest import mock
import uuid

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.schema.blob_attributes import BlobAttributesData
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    DATABASE_NODE_IP: str = '192.168.1.1'
    DATABASE_API_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()

TEST_UUID = uuid.uuid4()
TEST_UUID_STR = str(TEST_UUID)
TEST_UUID2 = uuid.uuid4()
TEST_UUID2_STR = str(TEST_UUID2)


class CreateBlobAttributesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_create_blob_attributes() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_blob_attributes_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        attrs = BlobAttributesData(
            uuid=TEST_UUID,
            size=1024,
            info={'format': 'qcow2', 'virtual_size': 10737418240},
            last_used=1234567890.0,
            expires_at=0.0
        )

        result = mariadb._direct_create_blob_attributes(attrs)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_blob_attributes_integrity_error(self, mock_get_engine):
        from sqlalchemy.exc import IntegrityError
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = IntegrityError(
            'statement', {}, Exception('Duplicate entry'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        attrs = BlobAttributesData(
            uuid=TEST_UUID,
            size=1024,
            info={},
            last_used=None,
            expires_at=0.0
        )

        result = mariadb._direct_create_blob_attributes(attrs)

        self.assertFalse(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_blob_attributes_operational_error(self, mock_get_engine):
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

        attrs = BlobAttributesData(
            uuid=TEST_UUID,
            size=1024,
            info={},
            last_used=None,
            expires_at=0.0
        )

        result = mariadb._direct_create_blob_attributes(attrs)

        self.assertFalse(result)


class UpdateBlobAttributesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_update_blob_attributes() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_blob_attributes_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        attrs = BlobAttributesData(
            uuid=TEST_UUID,
            size=2048,
            info={'format': 'raw'},
            last_used=1234567900.0,
            expires_at=1234567999.0
        )

        result = mariadb._direct_update_blob_attributes(attrs)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_blob_attributes_not_found(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 0
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        attrs = BlobAttributesData(
            uuid=TEST_UUID,
            size=2048,
            info={},
            last_used=None,
            expires_at=0.0
        )

        result = mariadb._direct_update_blob_attributes(attrs)

        self.assertFalse(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_blob_attributes_error(self, mock_get_engine):
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

        attrs = BlobAttributesData(
            uuid=TEST_UUID,
            size=2048,
            info={},
            last_used=None,
            expires_at=0.0
        )

        result = mariadb._direct_update_blob_attributes(attrs)

        self.assertFalse(result)


class UpdateBlobLastUsedTestCase(base.ShakenFistTestCase):
    """Tests for _direct_update_blob_last_used() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_last_used_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_update_blob_last_used(TEST_UUID, 1234567890.0)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_last_used_not_found(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 0
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_update_blob_last_used(TEST_UUID, 1234567890.0)

        self.assertFalse(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_last_used_error(self, mock_get_engine):
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

        result = mariadb._direct_update_blob_last_used(TEST_UUID, 1234567890.0)

        self.assertFalse(result)


class DeleteBlobAttributesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_blob_attributes() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_blob_attributes_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_blob_attributes(TEST_UUID)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_blob_attributes_not_found(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 0
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_blob_attributes(TEST_UUID)

        self.assertFalse(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_blob_attributes_error(self, mock_get_engine):
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

        result = mariadb._direct_delete_blob_attributes(TEST_UUID)

        self.assertFalse(result)


class GetExpiredBlobUuidsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_expired_blob_uuids() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_expired_blobs_returns_uuids(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_row1 = mock.MagicMock()
        mock_row1.uuid = TEST_UUID
        mock_row2 = mock.MagicMock()
        mock_row2.uuid = TEST_UUID2
        mock_conn.execute.return_value = [mock_row1, mock_row2]
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_expired_blob_uuids(current_time=2000000000.0)

        self.assertEqual(len(result), 2)
        self.assertIn(TEST_UUID_STR, result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_expired_blobs_returns_empty_list(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value = []
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_expired_blob_uuids(current_time=1234567890.0)

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_expired_blobs_error_returns_empty(self, mock_get_engine):
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

        result = mariadb._direct_get_expired_blob_uuids(current_time=1234567890.0)

        self.assertEqual(result, [])


# Note: _migrate_etcd_blob_attributes() is difficult to unit test due to
# SQLAlchemy table object requirements. The function queries the blobs table
# directly using sa.select(), which cannot be easily mocked.
# This is tested through integration tests instead.


class BlobAttributesDataSchemaTestCase(base.ShakenFistTestCase):
    """Tests for the BlobAttributesData pydantic schema."""

    def test_create_with_defaults(self):
        attrs = BlobAttributesData(uuid=TEST_UUID)

        self.assertEqual(attrs.uuid, TEST_UUID)
        self.assertEqual(attrs.size, 0)
        self.assertEqual(attrs.info, {})
        self.assertIsNone(attrs.last_used)
        self.assertEqual(attrs.expires_at, 0.0)

    def test_create_with_all_fields(self):
        attrs = BlobAttributesData(
            uuid=TEST_UUID,
            size=10240,
            info={'format': 'qcow2', 'virtual_size': 10737418240},
            last_used=1234567890.0,
            expires_at=1234599999.0
        )

        self.assertEqual(attrs.uuid, TEST_UUID)
        self.assertEqual(attrs.size, 10240)
        self.assertEqual(attrs.info['format'], 'qcow2')
        self.assertEqual(attrs.last_used, 1234567890.0)
        self.assertEqual(attrs.expires_at, 1234599999.0)

    def test_schema_is_mutable(self):
        attrs = BlobAttributesData(uuid=TEST_UUID)
        attrs.size = 2048
        attrs.last_used = 1234567890.0

        self.assertEqual(attrs.size, 2048)
        self.assertEqual(attrs.last_used, 1234567890.0)

    def test_uuid_validation(self):
        from pydantic import ValidationError

        self.assertRaises(
            ValidationError, BlobAttributesData,
            uuid='not-a-valid-uuid')

    def test_info_defaults_to_empty_dict(self):
        attrs1 = BlobAttributesData(uuid=TEST_UUID)
        attrs2 = BlobAttributesData(uuid=TEST_UUID)

        # Ensure each instance gets its own dict (not shared)
        attrs1.info['key'] = 'value'
        self.assertNotIn('key', attrs2.info)
