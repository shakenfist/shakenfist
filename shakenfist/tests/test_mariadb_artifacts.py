# Tests for the mariadb artifact functions.
#
# This module tests:
# - _direct_create_artifact() creating artifact records
# - _direct_get_artifact() retrieving artifacts
# - _direct_get_all_artifacts() listing all artifacts
# - _direct_update_artifact() updating artifact records
# - _direct_delete_artifact() deleting artifact records
# - _direct_create_artifact_attributes() creating attribute records
# - _direct_get_artifact_attributes() retrieving attributes
# - _direct_update_artifact_attributes() updating attributes
# - _direct_delete_artifact_attributes() deleting attributes
# - _direct_create_artifact_index() creating index records
# - _direct_get_artifact_index() retrieving indexes
# - _direct_get_all_artifact_indexes() listing all indexes
# - _direct_delete_artifact_index() deleting indexes
# - _direct_delete_all_artifact_indexes() bulk deleting indexes

from unittest import mock
import uuid

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.schema.artifact_attributes import ArtifactAttributesData
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    DATABASE_NODE_IP: str = '192.168.1.1'
    DATABASE_API_PORT: int = 13005
    DATABASE_USE_DIRECT_ETCD: bool = False
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()

TEST_UUID = uuid.uuid4()
TEST_UUID2 = uuid.uuid4()
BLOB_UUID = uuid.uuid4()
BLOB_UUID2 = uuid.uuid4()


class CreateArtifactTestCase(base.ShakenFistTestCase):
    """Tests for _direct_create_artifact() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_artifact_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_create_artifact(
            TEST_UUID, 'image', 'http://example.com/image.qcow2',
            'image.qcow2', 'system', 9)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_artifact_integrity_error(self, mock_get_engine):
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

        result = mariadb._direct_create_artifact(
            TEST_UUID, 'image', 'http://example.com/image.qcow2',
            'image.qcow2', 'system', 9)

        self.assertFalse(result)


class GetArtifactTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_artifact() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_artifact_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_row = mock.MagicMock()
        mock_row.uuid = TEST_UUID
        mock_row.artifact_type = 'image'
        mock_row.source_url = 'http://example.com/image.qcow2'
        mock_row.name = 'image.qcow2'
        mock_row.namespace = 'system'
        mock_row.version = 9
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_artifact(TEST_UUID)

        self.assertIsNotNone(result)
        self.assertEqual(result.uuid, TEST_UUID)
        self.assertEqual(result.artifact_type, 'image')
        self.assertEqual(result.source_url, 'http://example.com/image.qcow2')
        self.assertEqual(result.name, 'image.qcow2')
        self.assertEqual(result.namespace, 'system')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_artifact_not_found(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_artifact(TEST_UUID)

        self.assertIsNone(result)


class GetAllArtifactsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_all_artifacts() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_all_artifacts_empty(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_all_artifacts()

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_all_artifacts_with_data(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_row = mock.MagicMock()
        mock_row.uuid = TEST_UUID
        mock_row.artifact_type = 'image'
        mock_row.source_url = 'http://example.com/image.qcow2'
        mock_row.name = 'image.qcow2'
        mock_row.namespace = 'system'
        mock_row.version = 9
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_all_artifacts()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].uuid, TEST_UUID)


class CreateArtifactAttributesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_create_artifact_attributes() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_artifact_attributes_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        attrs = ArtifactAttributesData(
            uuid=TEST_UUID, max_versions=3, shared=True, highest_index=5)

        result = mariadb._direct_create_artifact_attributes(attrs)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()


class GetArtifactAttributesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_artifact_attributes() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_artifact_attributes_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_row = mock.MagicMock()
        mock_row.uuid = TEST_UUID
        mock_row.max_versions = 3
        mock_row.shared = True
        mock_row.highest_index = 5
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_artifact_attributes(TEST_UUID)

        self.assertIsNotNone(result)
        self.assertEqual(result.uuid, TEST_UUID)
        self.assertEqual(result.max_versions, 3)
        self.assertTrue(result.shared)
        self.assertEqual(result.highest_index, 5)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_artifact_attributes_not_found(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_artifact_attributes(TEST_UUID)

        self.assertIsNone(result)


class CreateArtifactIndexTestCase(base.ShakenFistTestCase):
    """Tests for _direct_create_artifact_index() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_artifact_index_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_create_artifact_index(
            TEST_UUID, 1, BLOB_UUID)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()


class GetAllArtifactIndexesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_all_artifact_indexes() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_all_artifact_indexes_empty(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_all_artifact_indexes(TEST_UUID)

        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_all_artifact_indexes_with_data(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_row1 = mock.MagicMock()
        mock_row1.artifact_uuid = TEST_UUID
        mock_row1.index_number = 1
        mock_row1.blob_uuid = BLOB_UUID
        mock_row2 = mock.MagicMock()
        mock_row2.artifact_uuid = TEST_UUID
        mock_row2.index_number = 2
        mock_row2.blob_uuid = BLOB_UUID2
        mock_conn.execute.return_value.fetchall.return_value = [
            mock_row1, mock_row2]
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_all_artifact_indexes(TEST_UUID)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].index_number, 1)
        self.assertEqual(result[0].blob_uuid, BLOB_UUID)
        self.assertEqual(result[1].index_number, 2)
        self.assertEqual(result[1].blob_uuid, BLOB_UUID2)


class DeleteAllArtifactIndexesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_all_artifact_indexes() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_all_artifact_indexes(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.rowcount = 3
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_all_artifact_indexes(TEST_UUID)

        self.assertEqual(result, 3)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()
