# Tests for Upload class and related functionality.
#
# This module tests:
# - UploadData Pydantic model
# - Upload class initialization and methods
# - upgrade_pydantic_data() in DatabaseBackedObject
# - _persist_pydantic_upgrade() override in Upload
# - mariadb upload functions

from typing import Any
from unittest import mock
from uuid import UUID

from pydantic import ValidationError
import testtools

from shakenfist.schema.object_state import State
from shakenfist.schema.upload import UploadData
from shakenfist.tests import base
from shakenfist import upload


class UploadDataTestCase(base.ShakenFistTestCase):
    """Tests for the UploadData Pydantic model."""

    def test_create_from_kwargs(self):
        """Test creating UploadData from keyword arguments."""
        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=5
        )
        self.assertEqual(str(data.uuid), '12345678-1234-4321-8234-123456789012')
        self.assertEqual(data.node, 'node1')
        self.assertEqual(data.created_at, 1234567890.0)
        self.assertEqual(data.version, 5)

    def test_create_from_uuid_object(self):
        """Test creating UploadData with a UUID object."""
        uuid_obj = UUID('12345678-1234-4321-8234-123456789012')
        data = UploadData(
            uuid=uuid_obj,
            node='node1',
            created_at=1234567890.0,
            version=5
        )
        self.assertEqual(data.uuid, uuid_obj)

    def test_immutable(self):
        """Test that UploadData is immutable (frozen)."""
        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=5
        )
        with testtools.ExpectedException(ValidationError):
            data.node = 'node2'

    def test_invalid_uuid(self):
        """Test that invalid UUID raises ValidationError."""
        with testtools.ExpectedException(ValidationError):
            UploadData(
                uuid='not-a-uuid',
                node='node1',
                created_at=1234567890.0,
                version=5
            )

    def test_model_dump(self):
        """Test that model_dump() produces expected output."""
        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=5
        )
        dumped = data.model_dump()
        self.assertEqual(dumped['node'], 'node1')
        self.assertEqual(dumped['created_at'], 1234567890.0)
        self.assertEqual(dumped['version'], 5)
        # UUID is a UUID object in the dump
        self.assertEqual(
            str(dumped['uuid']), '12345678-1234-4321-8234-123456789012')


class UpgradePydanticDataTestCase(base.ShakenFistTestCase):
    """Tests for the upgrade_pydantic_data() method in DatabaseBackedObject."""

    def test_no_upgrade_needed(self):
        """Test that data at current version is returned unchanged."""
        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=upload.Upload.current_version
        )

        result = upload.Upload.upgrade_pydantic_data(data, UploadData)

        # Should be the same object (no upgrade needed)
        self.assertIs(result, data)

    @mock.patch('shakenfist.baseobject.get_minimum_object_version')
    def test_upgrade_applies_steps(self, mock_get_min_version):
        """Test that upgrade steps are applied sequentially."""
        # Set cluster not ready so we don't try to persist
        mock_get_min_version.return_value = 1

        # Create a test subclass with upgrade steps
        class TestUploadWithUpgrade(upload.Upload):
            initial_version = 1
            current_version = 3

            @classmethod
            def _upgrade_step_1_to_2(cls, values: dict[str, Any]) -> None:
                values['node'] = values['node'] + '_upgraded_v2'

            @classmethod
            def _upgrade_step_2_to_3(cls, values: dict[str, Any]) -> None:
                values['node'] = values['node'] + '_upgraded_v3'

        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=1
        )

        result = TestUploadWithUpgrade.upgrade_pydantic_data(data, UploadData)

        # Should be a new object
        self.assertIsNot(result, data)
        # Version should be updated
        self.assertEqual(result.version, 3)
        # Upgrade steps should have been applied
        self.assertEqual(result.node, 'node1_upgraded_v2_upgraded_v3')

    @mock.patch('shakenfist.baseobject.get_minimum_object_version')
    @mock.patch('shakenfist.mariadb.update_upload')
    def test_upgrade_persists_when_cluster_ready(self, mock_update, mock_get_min):
        """Test that upgrade is persisted when cluster min == current version."""
        mock_get_min.return_value = upload.Upload.current_version
        mock_update.return_value = True

        # Create data at an older version
        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=upload.Upload.current_version - 1
        )

        # Create a subclass with an upgrade step
        class TestUpload(upload.Upload):
            current_version = upload.Upload.current_version

            @classmethod
            def _upgrade_step_4_to_5(cls, values: dict[str, Any]) -> None:
                # No-op upgrade step
                pass

        TestUpload.initial_version = upload.Upload.current_version - 1

        TestUpload.upgrade_pydantic_data(data, UploadData)

        # Should have called update_upload with the upgraded data
        mock_update.assert_called_once()
        call_args = mock_update.call_args[0][0]
        self.assertEqual(call_args.version, upload.Upload.current_version)

    @mock.patch('shakenfist.baseobject.get_minimum_object_version')
    @mock.patch('shakenfist.mariadb.update_upload')
    def test_upgrade_not_persisted_when_cluster_not_ready(
            self, mock_update, mock_get_min):
        """Test that upgrade is NOT persisted when cluster min < current."""
        # Cluster is not ready (min version < current)
        mock_get_min.return_value = upload.Upload.current_version - 1

        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=upload.Upload.current_version - 1
        )

        # Create a subclass with an upgrade step
        class TestUpload(upload.Upload):
            current_version = upload.Upload.current_version

            @classmethod
            def _upgrade_step_4_to_5(cls, values: dict[str, Any]) -> None:
                pass

        TestUpload.initial_version = upload.Upload.current_version - 1

        TestUpload.upgrade_pydantic_data(data, UploadData)

        # Should NOT have called update_upload
        mock_update.assert_not_called()


class UploadClassTestCase(base.ShakenFistTestCase):
    """Tests for the Upload class."""

    @mock.patch('shakenfist.baseobject.get_minimum_object_version',
                return_value=upload.Upload.current_version)
    @mock.patch('shakenfist.mariadb.get_state',
                return_value=State(value='created', update_time=1234567890.0))
    def test_init_from_upload_data(self, mock_get_state, mock_get_min):
        """Test creating Upload from UploadData."""
        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=upload.Upload.current_version
        )

        u = upload.Upload(data)

        self.assertEqual(str(u.uuid), '12345678-1234-4321-8234-123456789012')
        self.assertEqual(u.node, 'node1')
        self.assertEqual(u.created_at, 1234567890.0)
        self.assertEqual(u.version, upload.Upload.current_version)

    @mock.patch('shakenfist.baseobject.get_minimum_object_version',
                return_value=upload.Upload.current_version)
    @mock.patch('shakenfist.mariadb.update_upload', return_value=True)
    @mock.patch('shakenfist.mariadb.get_state',
                return_value=State(value='created', update_time=1234567890.0))
    def test_persist_pydantic_upgrade_called(
            self, mock_get_state, mock_update, mock_get_min):
        """Test that _persist_pydantic_upgrade is called during upgrade."""
        # Create data at an older version
        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=upload.Upload.current_version - 1
        )

        # Temporarily modify Upload to have a simple upgrade step
        original_version = upload.Upload.initial_version
        upload.Upload.initial_version = upload.Upload.current_version - 1

        try:
            # Define the missing upgrade step
            def upgrade_step(cls, values):
                pass
            step_name = '_upgrade_step_%d_to_%d' % (
                upload.Upload.current_version - 1,
                upload.Upload.current_version
            )
            setattr(upload.Upload, step_name, classmethod(upgrade_step))

            # Create the upload - this should trigger upgrade and persist
            upload.Upload(data)

            # Verify update_upload was called
            mock_update.assert_called_once()

        finally:
            upload.Upload.initial_version = original_version
            if hasattr(upload.Upload, step_name):
                delattr(upload.Upload, step_name)


class MariaDBUploadFunctionsTestCase(base.ShakenFistTestCase):
    """Tests for mariadb upload functions."""

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_update_upload', return_value=True)
    def test_update_upload_direct(self, mock_direct, mock_use_db):
        """Test update_upload routes to direct function."""
        from shakenfist import mariadb

        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=5
        )

        result = mariadb.update_upload(data)

        self.assertTrue(result)
        mock_direct.assert_called_once_with(data)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    @mock.patch('shakenfist.mariadb._grpc_update_upload', return_value=True)
    def test_update_upload_grpc(self, mock_grpc, mock_use_db):
        """Test update_upload routes to gRPC function."""
        from shakenfist import mariadb

        data = UploadData(
            uuid='12345678-1234-4321-8234-123456789012',
            node='node1',
            created_at=1234567890.0,
            version=5
        )

        result = mariadb.update_upload(data)

        self.assertTrue(result)
        mock_grpc.assert_called_once_with(data)
