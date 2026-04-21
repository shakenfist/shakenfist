# Tests for the mariadb object reference functions (gRPC client layer).
#
# This module tests:
# - record_relationship() gRPC client function
# - remove_relationship() gRPC client function
# - get_references_to() gRPC client function
# - get_references_from() gRPC client function
# - count_references_to() gRPC client function
# - remove_all_references_from() gRPC client function
# - update_last_active() gRPC client function
# - get_stale_references() gRPC client function

from unittest import mock
from uuid import uuid4

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.protos import database_pb2
from shakenfist.protos import shakenfist_enums_pb2
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.tests import base


# Generate valid UUID4 values for tests
SOURCE_UUID = uuid4()
TARGET_UUID = uuid4()


class FakeConfig(BaseSettings):
    DATABASE_NODE_IP: str = '192.168.1.1'
    DATABASE_API_PORT: int = 13005
    MARIADB_HOST: str = ''
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()


class RecordRelationshipTestCase(base.ShakenFistTestCase):
    """Tests for record_relationship() gRPC client function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_record_relationship_success(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.RecordRelationship.return_value = database_pb2.StatusReply(
            success=True, error='')
        mock_get_stub.return_value = mock_stub

        result = mariadb.record_relationship(
            ObjectType.INSTANCE, SOURCE_UUID,
            RelationshipType.DISK, '0',
            ObjectType.BLOB, TARGET_UUID)

        self.assertTrue(result)
        mock_stub.RecordRelationship.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_record_relationship_failure(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.RecordRelationship.return_value = database_pb2.StatusReply(
            success=False, error='Database error')
        mock_get_stub.return_value = mock_stub

        result = mariadb.record_relationship(
            ObjectType.INSTANCE, SOURCE_UUID,
            RelationshipType.DISK, '0',
            ObjectType.BLOB, TARGET_UUID)

        self.assertFalse(result)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_record_relationship_with_none_value(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.RecordRelationship.return_value = database_pb2.StatusReply(
            success=True, error='')
        mock_get_stub.return_value = mock_stub

        result = mariadb.record_relationship(
            ObjectType.BLOB, SOURCE_UUID,
            RelationshipType.DEPENDS_ON, None,
            ObjectType.BLOB, TARGET_UUID)

        self.assertTrue(result)
        mock_stub.RecordRelationship.assert_called_once()
        # Verify relationship_value was passed as empty string (None -> '')
        call_args = mock_stub.RecordRelationship.call_args[0][0]
        self.assertEqual(call_args.relationship_value, '')


class RemoveRelationshipTestCase(base.ShakenFistTestCase):
    """Tests for remove_relationship() gRPC client function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_remove_relationship_success(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.RemoveRelationship.return_value = database_pb2.StatusReply(
            success=True, error='')
        mock_get_stub.return_value = mock_stub

        result = mariadb.remove_relationship(
            ObjectType.INSTANCE, SOURCE_UUID,
            RelationshipType.DISK, '0',
            ObjectType.BLOB, TARGET_UUID)

        self.assertTrue(result)
        mock_stub.RemoveRelationship.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_remove_relationship_not_found(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.RemoveRelationship.return_value = database_pb2.StatusReply(
            success=False, error='Relationship not found')
        mock_get_stub.return_value = mock_stub

        result = mariadb.remove_relationship(
            ObjectType.INSTANCE, SOURCE_UUID,
            RelationshipType.DISK, '0',
            ObjectType.BLOB, TARGET_UUID)

        self.assertFalse(result)


class GetReferencesToTestCase(base.ShakenFistTestCase):
    """Tests for get_references_to() gRPC client function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_get_references_to_found(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        ref_data = database_pb2.ObjectReferenceData(
            source_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_INSTANCE,
            source_uuid=str(SOURCE_UUID),
            relationship=shakenfist_enums_pb2.RelationshipType.RELATIONSHIP_TYPE_DISK,
            relationship_value='0',
            target_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_BLOB,
            target_uuid=str(TARGET_UUID),
            created=1234567890.0,
            last_active=1234567890.0
        )
        mock_stub.GetReferencesTo.return_value = database_pb2.GetReferencesReply(
            references=[ref_data])
        mock_get_stub.return_value = mock_stub

        result = mariadb.get_references_to(ObjectType.BLOB, TARGET_UUID)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source_object_type, ObjectType.INSTANCE)
        self.assertEqual(result[0].relationship, RelationshipType.DISK)
        self.assertEqual(result[0].relationship_value, '0')

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_get_references_to_empty(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.GetReferencesTo.return_value = database_pb2.GetReferencesReply(
            references=[])
        mock_get_stub.return_value = mock_stub

        result = mariadb.get_references_to(ObjectType.BLOB, TARGET_UUID)

        self.assertEqual(len(result), 0)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_get_references_to_with_relationship_filter(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        ref_data = database_pb2.ObjectReferenceData(
            source_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_INSTANCE,
            source_uuid=str(SOURCE_UUID),
            relationship=shakenfist_enums_pb2.RelationshipType.RELATIONSHIP_TYPE_DISK,
            relationship_value='0',
            target_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_BLOB,
            target_uuid=str(TARGET_UUID),
            created=1234567890.0,
            last_active=1234567890.0
        )
        mock_stub.GetReferencesTo.return_value = database_pb2.GetReferencesReply(
            references=[ref_data])
        mock_get_stub.return_value = mock_stub

        result = mariadb.get_references_to(
            ObjectType.BLOB, TARGET_UUID, RelationshipType.DISK)

        self.assertEqual(len(result), 1)
        # Verify the relationship filter was passed to gRPC
        call_args = mock_stub.GetReferencesTo.call_args
        request = call_args[0][0]
        self.assertTrue(request.HasField('relationship'))
        self.assertEqual(
            request.relationship,
            shakenfist_enums_pb2.RelationshipType.RELATIONSHIP_TYPE_DISK)


class GetReferencesFromTestCase(base.ShakenFistTestCase):
    """Tests for get_references_from() gRPC client function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_get_references_from_found(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        ref_data = database_pb2.ObjectReferenceData(
            source_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_INSTANCE,
            source_uuid=str(SOURCE_UUID),
            relationship=shakenfist_enums_pb2.RelationshipType.RELATIONSHIP_TYPE_DISK,
            relationship_value='0',
            target_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_BLOB,
            target_uuid=str(TARGET_UUID),
            created=1234567890.0,
            last_active=1234567890.0
        )
        mock_stub.GetReferencesFrom.return_value = database_pb2.GetReferencesReply(
            references=[ref_data])
        mock_get_stub.return_value = mock_stub

        result = mariadb.get_references_from(ObjectType.INSTANCE, SOURCE_UUID)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].target_object_type, ObjectType.BLOB)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_get_references_from_multiple(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        ref_data1 = database_pb2.ObjectReferenceData(
            source_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_INSTANCE,
            source_uuid=str(SOURCE_UUID),
            relationship=shakenfist_enums_pb2.RelationshipType.RELATIONSHIP_TYPE_DISK,
            relationship_value='0',
            target_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_BLOB,
            target_uuid=str(TARGET_UUID),
            created=1234567890.0,
            last_active=1234567890.0
        )
        ref_data2 = database_pb2.ObjectReferenceData(
            source_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_INSTANCE,
            source_uuid=str(SOURCE_UUID),
            relationship=shakenfist_enums_pb2.RelationshipType.RELATIONSHIP_TYPE_DISK,
            relationship_value='1',
            target_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_BLOB,
            target_uuid=str(uuid4()),
            created=1234567890.0,
            last_active=1234567890.0
        )
        mock_stub.GetReferencesFrom.return_value = database_pb2.GetReferencesReply(
            references=[ref_data1, ref_data2])
        mock_get_stub.return_value = mock_stub

        result = mariadb.get_references_from(ObjectType.INSTANCE, SOURCE_UUID)

        self.assertEqual(len(result), 2)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_get_references_from_with_relationship_filter(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        ref_data = database_pb2.ObjectReferenceData(
            source_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_BLOB,
            source_uuid=str(SOURCE_UUID),
            relationship=shakenfist_enums_pb2.RelationshipType.RELATIONSHIP_TYPE_TRANSCODE,
            relationship_value='qcow2',
            target_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_BLOB,
            target_uuid=str(TARGET_UUID),
            created=1234567890.0,
            last_active=1234567890.0
        )
        mock_stub.GetReferencesFrom.return_value = database_pb2.GetReferencesReply(
            references=[ref_data])
        mock_get_stub.return_value = mock_stub

        result = mariadb.get_references_from(
            ObjectType.BLOB, SOURCE_UUID, RelationshipType.TRANSCODE)

        self.assertEqual(len(result), 1)
        # Verify the relationship filter was passed to gRPC
        call_args = mock_stub.GetReferencesFrom.call_args
        request = call_args[0][0]
        self.assertTrue(request.HasField('relationship'))
        self.assertEqual(
            request.relationship,
            shakenfist_enums_pb2.RelationshipType.RELATIONSHIP_TYPE_TRANSCODE)


class CountReferencesToTestCase(base.ShakenFistTestCase):
    """Tests for count_references_to() gRPC client function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_count_references_to_found(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.CountReferencesTo.return_value = database_pb2.CountReply(
            count=5)
        mock_get_stub.return_value = mock_stub

        result = mariadb.count_references_to(ObjectType.BLOB, TARGET_UUID)

        self.assertEqual(result, 5)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_count_references_to_zero(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.CountReferencesTo.return_value = database_pb2.CountReply(
            count=0)
        mock_get_stub.return_value = mock_stub

        result = mariadb.count_references_to(ObjectType.BLOB, TARGET_UUID)

        self.assertEqual(result, 0)


class RemoveAllReferencesFromTestCase(base.ShakenFistTestCase):
    """Tests for remove_all_references_from() gRPC client function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_remove_all_references_from_success(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.RemoveAllReferencesFrom.return_value = database_pb2.CountReply(
            count=3)
        mock_get_stub.return_value = mock_stub

        result = mariadb.remove_all_references_from(
            ObjectType.INSTANCE, SOURCE_UUID)

        self.assertEqual(result, 3)
        mock_stub.RemoveAllReferencesFrom.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_remove_all_references_from_none(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.RemoveAllReferencesFrom.return_value = database_pb2.CountReply(
            count=0)
        mock_get_stub.return_value = mock_stub

        result = mariadb.remove_all_references_from(
            ObjectType.INSTANCE, SOURCE_UUID)

        self.assertEqual(result, 0)


class UpdateLastActiveTestCase(base.ShakenFistTestCase):
    """Tests for update_last_active() gRPC client function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_update_last_active_success(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.UpdateLastActive.return_value = database_pb2.StatusReply(
            success=True, error='')
        mock_get_stub.return_value = mock_stub

        result = mariadb.update_last_active(
            ObjectType.INSTANCE, SOURCE_UUID,
            RelationshipType.DISK, '0',
            ObjectType.BLOB, TARGET_UUID)

        self.assertTrue(result)
        mock_stub.UpdateLastActive.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_update_last_active_not_found(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.UpdateLastActive.return_value = database_pb2.StatusReply(
            success=False, error='Relationship not found')
        mock_get_stub.return_value = mock_stub

        result = mariadb.update_last_active(
            ObjectType.INSTANCE, SOURCE_UUID,
            RelationshipType.DISK, '0',
            ObjectType.BLOB, TARGET_UUID)

        self.assertFalse(result)


class GetStaleReferencesTestCase(base.ShakenFistTestCase):
    """Tests for get_stale_references() gRPC client function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_get_stale_references_found(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        old_time = 1234567890.0
        ref_data = database_pb2.ObjectReferenceData(
            source_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_INSTANCE,
            source_uuid=str(SOURCE_UUID),
            relationship=shakenfist_enums_pb2.RelationshipType.RELATIONSHIP_TYPE_DISK,
            relationship_value='0',
            target_type=shakenfist_enums_pb2.ObjectType.OBJECT_TYPE_BLOB,
            target_uuid=str(TARGET_UUID),
            created=old_time,
            last_active=old_time
        )
        mock_stub.GetStaleReferences.return_value = database_pb2.GetReferencesReply(
            references=[ref_data])
        mock_get_stub.return_value = mock_stub

        result = mariadb.get_stale_references(old_time + 3600)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].last_active, old_time)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_get_stale_references_empty(self, mock_get_stub):
        mock_stub = mock.MagicMock()
        mock_stub.GetStaleReferences.return_value = database_pb2.GetReferencesReply(
            references=[])
        mock_get_stub.return_value = mock_stub

        result = mariadb.get_stale_references(1234567890.0)

        self.assertEqual(len(result), 0)
