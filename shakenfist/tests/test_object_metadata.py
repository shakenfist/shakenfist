# Copyright 2019 Michael Still and contributors
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.schema.object_metadata import ObjectMetadataData
from shakenfist.tests import base

TEST_UUID = '12345678-1234-4321-8234-123456789012'


class MetadataPropertyTestCase(base.ShakenFistTestCase):
    """Test metadata property reads from MariaDB only."""

    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={'key1': 'val1', 'key2': 'val2'}
                ))
    def test_metadata_reads_from_mariadb(self, mock_get):
        d = DatabaseBackedObject(TEST_UUID)
        result = d.metadata
        self.assertEqual(result, {'key1': 'val1', 'key2': 'val2'})
        mock_get.assert_called_once_with(d.object_type, TEST_UUID)

    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=None)
    def test_metadata_returns_empty_when_no_record(self, mock_get):
        d = DatabaseBackedObject(TEST_UUID)
        result = d.metadata
        self.assertEqual(result, {})

    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata=None
                ))
    def test_metadata_returns_empty_when_column_is_none(
            self, mock_get):
        """MariaDB row exists but metadata column is NULL."""
        d = DatabaseBackedObject(TEST_UUID)
        result = d.metadata
        self.assertEqual(result, {})

    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={}
                ))
    def test_metadata_returns_empty_dict_from_mariadb(
            self, mock_get):
        """Empty dict is a valid value."""
        d = DatabaseBackedObject(TEST_UUID)
        result = d.metadata
        self.assertEqual(result, {})

    def test_metadata_in_memory_only_uses_in_memory_store(self):
        d = DatabaseBackedObject(TEST_UUID, in_memory_only=True)
        # In-memory values are stored as JSON strings
        d._DatabaseBackedObject__in_memory_values = {
            'metadata': '{"mem": "val"}'
        }
        result = d.metadata
        self.assertEqual(result, {'mem': 'val'})


class AddMetadataKeyTestCase(base.ShakenFistTestCase):
    """Test add_metadata_key writes to MariaDB only."""

    @mock.patch('shakenfist.mariadb.set_metadata', return_value=True)
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={'existing': 'val'}
                ))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_add_metadata_key_writes_to_mariadb(
            self, mock_lock, mock_get, mock_set):
        mock_lock.return_value.__enter__ = mock.Mock()
        mock_lock.return_value.__exit__ = mock.Mock(return_value=False)

        d = DatabaseBackedObject(TEST_UUID)
        d.add_metadata_key('new_key', 'new_val')

        expected = {'existing': 'val', 'new_key': 'new_val'}
        mock_set.assert_called_once_with(d.object_type, TEST_UUID, expected)

    @mock.patch('shakenfist.mariadb.set_metadata', return_value=True)
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={'key': 'old'}
                ))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_add_metadata_key_overwrites_existing(
            self, mock_lock, mock_get, mock_set):
        mock_lock.return_value.__enter__ = mock.Mock()
        mock_lock.return_value.__exit__ = mock.Mock(return_value=False)

        d = DatabaseBackedObject(TEST_UUID)
        d.add_metadata_key('key', 'new')

        expected = {'key': 'new'}
        mock_set.assert_called_once_with(d.object_type, TEST_UUID, expected)


class RemoveMetadataKeyTestCase(base.ShakenFistTestCase):
    """Test remove_metadata_key writes to MariaDB only."""

    @mock.patch('shakenfist.mariadb.set_metadata', return_value=True)
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={'keep': 'yes', 'remove': 'me'}
                ))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_remove_metadata_key_writes_to_mariadb(
            self, mock_lock, mock_get, mock_set):
        mock_lock.return_value.__enter__ = mock.Mock()
        mock_lock.return_value.__exit__ = mock.Mock(return_value=False)

        d = DatabaseBackedObject(TEST_UUID)
        d.remove_metadata_key('remove')

        expected = {'keep': 'yes'}
        mock_set.assert_called_once_with(d.object_type, TEST_UUID, expected)

    @mock.patch('shakenfist.mariadb.set_metadata')
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={'keep': 'yes'}
                ))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_remove_nonexistent_key_is_noop(
            self, mock_lock, mock_get, mock_set):
        mock_lock.return_value.__enter__ = mock.Mock()
        mock_lock.return_value.__exit__ = mock.Mock(return_value=False)

        d = DatabaseBackedObject(TEST_UUID)
        d.remove_metadata_key('nonexistent')

        mock_set.assert_not_called()


class HardDeleteMetadataTestCase(base.ShakenFistTestCase):
    """Test hard_delete cleans up object_metadata."""

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    @mock.patch('shakenfist.mariadb.delete_object_metadata', return_value=True)
    @mock.patch('shakenfist.mariadb.delete_state', return_value=True)
    def test_hard_delete_cleans_up_object_metadata(
            self, mock_del_state, mock_del_meta, mock_del_events, mock_event):
        d = DatabaseBackedObject(TEST_UUID)
        d.hard_delete()

        mock_del_meta.assert_called_once_with(d.object_type, TEST_UUID)
        mock_del_state.assert_called_once_with(d.object_type, TEST_UUID)


class ObjectMetadataDataTestCase(base.ShakenFistTestCase):
    """Test the Pydantic schema itself."""

    def test_create_with_all_fields(self):
        data = ObjectMetadataData(
            object_type='instance',
            object_uuid=TEST_UUID,
            metadata={'key': 'value'},
        )
        self.assertEqual(data.object_type, 'instance')
        self.assertEqual(data.object_uuid, TEST_UUID)
        self.assertEqual(data.metadata, {'key': 'value'})

    def test_create_with_defaults(self):
        data = ObjectMetadataData(
            object_type='network',
            object_uuid=TEST_UUID
        )
        self.assertIsNone(data.metadata)

    def test_is_mutable(self):
        data = ObjectMetadataData(
            object_type='instance',
            object_uuid=TEST_UUID,
            metadata={'key': 'old'}
        )
        data.metadata = {'key': 'new'}
        self.assertEqual(data.metadata, {'key': 'new'})
