# Tests for the mariadb blob transfer functions.
#
# This module tests:
# - _direct_create_blob_transfer() creating transfer records
# - _direct_get_blob_transfer() retrieving a specific transfer
# - _direct_update_blob_transfer() updating transfer state/port/percentage
# - _direct_delete_blob_transfer() deleting transfer records
# - _cleanup_etcd_blob_transfers() cleanup function

from unittest import mock

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.schema.blob_transfer import BlobTransfer
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    MARIADB_GATEWAY_HOSTS: list[str] = ['192.168.1.1']
    MARIADB_GATEWAY_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()


class CreateBlobTransferTestCase(base.ShakenFistTestCase):
    """Tests for _direct_create_blob_transfer() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_blob_transfer_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        transfer = BlobTransfer(
            source_node='sf-1',
            transfer_name='test-transfer-123',
            requesting_node='192.168.1.100',
            blob_uuid='blob-uuid-456',
            token='auth-token-789',
            server_state='initial',
            port=None,
            percentage=0.0,
            created_at=1234567890.0,
            updated_at=1234567890.0
        )

        result = mariadb._direct_create_blob_transfer(transfer)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_blob_transfer_error(self, mock_get_engine):
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

        transfer = BlobTransfer(
            source_node='sf-1',
            transfer_name='test-transfer-123',
            requesting_node='192.168.1.100',
            blob_uuid='blob-uuid-456',
            token='auth-token-789',
            server_state='initial',
            port=None,
            percentage=0.0,
            created_at=1234567890.0,
            updated_at=1234567890.0
        )

        result = mariadb._direct_create_blob_transfer(transfer)

        self.assertFalse(result)


class UpdateBlobTransferTestCase(base.ShakenFistTestCase):
    """Tests for _direct_update_blob_transfer() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('time.time')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_server_state_and_port(self, mock_get_engine, mock_time):
        mock_time.return_value = 1234567890.0
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

        result = mariadb._direct_update_blob_transfer(
            'sf-1', 'test-transfer-123',
            server_state='created', port=12345)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('time.time')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_percentage(self, mock_get_engine, mock_time):
        mock_time.return_value = 1234567890.0
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

        result = mariadb._direct_update_blob_transfer(
            'sf-1', 'test-transfer-123', percentage=50.5)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()

    def test_update_no_fields(self):
        result = mariadb._direct_update_blob_transfer(
            'sf-1', 'test-transfer-123')

        self.assertFalse(result)


class DeleteBlobTransferTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_blob_transfer() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_blob_transfer_success(self, mock_get_engine):
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

        result = mariadb._direct_delete_blob_transfer('sf-1', 'test-transfer')

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_nonexistent_transfer(self, mock_get_engine):
        # delete returns True even if record doesn't exist (idempotent delete)
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

        result = mariadb._direct_delete_blob_transfer('sf-1', 'missing')

        self.assertTrue(result)


class DeleteStaleTransfersTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_stale_transfers() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_stale_transfers(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 3
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        # older_than is now a timestamp, not max_age
        older_than = 1234567290.0
        result = mariadb._direct_delete_stale_transfers(older_than)

        self.assertEqual(result, 3)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()


class DeleteBlobTransfersForBlobTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_blob_transfers_for_blob() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_transfers_for_blob_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 2
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_blob_transfers_for_blob('blob-uuid-123')

        self.assertEqual(result, 2)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_transfers_for_blob_none_exist(self, mock_get_engine):
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

        result = mariadb._direct_delete_blob_transfers_for_blob('blob-uuid-123')

        # 0 is a valid success case (no transfers existed)
        self.assertEqual(result, 0)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_transfers_for_blob_error(self, mock_get_engine):
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

        result = mariadb._direct_delete_blob_transfers_for_blob('blob-uuid-123')

        # -1 indicates error
        self.assertEqual(result, -1)
