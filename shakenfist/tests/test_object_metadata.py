# Copyright 2019 Michael Still and contributors
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.baseobject import DatabaseBackedObjectWithOperations
from shakenfist.schema.object_metadata import ObjectMetadataData
from shakenfist.tests import base

TEST_UUID = '12345678-1234-4321-8234-123456789012'


class MetadataPropertyTestCase(base.ShakenFistTestCase):
    """Test metadata property routing: MariaDB first, etcd fallback."""

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

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                return_value={'etcd_key': 'etcd_val'})
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=None)
    def test_metadata_falls_back_to_etcd(self, mock_get, mock_etcd_get):
        d = DatabaseBackedObject(TEST_UUID)
        result = d.metadata
        self.assertEqual(result, {'etcd_key': 'etcd_val'})
        mock_etcd_get.assert_called_once_with('metadata', {})

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                return_value={'etcd_key': 'etcd_val'})
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata=None
                ))
    def test_metadata_falls_back_when_mariadb_metadata_is_none(
            self, mock_get, mock_etcd_get):
        """MariaDB row exists but metadata column is NULL."""
        d = DatabaseBackedObject(TEST_UUID)
        result = d.metadata
        self.assertEqual(result, {'etcd_key': 'etcd_val'})
        mock_etcd_get.assert_called_once_with('metadata', {})

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                return_value={})
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={}
                ))
    def test_metadata_returns_empty_dict_from_mariadb(
            self, mock_get, mock_etcd_get):
        """Empty dict is a valid value, should not fall back to etcd."""
        d = DatabaseBackedObject(TEST_UUID)
        result = d.metadata
        self.assertEqual(result, {})
        mock_etcd_get.assert_not_called()

    def test_metadata_in_memory_only_uses_etcd(self):
        d = DatabaseBackedObject(TEST_UUID, in_memory_only=True)
        # In-memory values are stored as JSON strings
        d._DatabaseBackedObject__in_memory_values = {
            'metadata': '{"mem": "val"}'
        }
        result = d.metadata
        self.assertEqual(result, {'mem': 'val'})


class AddMetadataKeyTestCase(base.ShakenFistTestCase):
    """Test add_metadata_key dual-write behavior."""

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.mariadb.set_metadata', return_value=True)
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={'existing': 'val'}
                ))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_add_metadata_key_dual_writes(
            self, mock_lock, mock_get, mock_set, mock_etcd_set):
        mock_lock.return_value.__enter__ = mock.Mock()
        mock_lock.return_value.__exit__ = mock.Mock(return_value=False)

        d = DatabaseBackedObject(TEST_UUID)
        d.add_metadata_key('new_key', 'new_val')

        expected = {'existing': 'val', 'new_key': 'new_val'}
        mock_set.assert_called_once_with(d.object_type, TEST_UUID, expected)
        mock_etcd_set.assert_called_once_with('metadata', expected)

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.mariadb.set_metadata', return_value=True)
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={'key': 'old'}
                ))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_add_metadata_key_overwrites_existing(
            self, mock_lock, mock_get, mock_set, mock_etcd_set):
        mock_lock.return_value.__enter__ = mock.Mock()
        mock_lock.return_value.__exit__ = mock.Mock(return_value=False)

        d = DatabaseBackedObject(TEST_UUID)
        d.add_metadata_key('key', 'new')

        expected = {'key': 'new'}
        mock_set.assert_called_once_with(d.object_type, TEST_UUID, expected)
        mock_etcd_set.assert_called_once_with('metadata', expected)


class RemoveMetadataKeyTestCase(base.ShakenFistTestCase):
    """Test remove_metadata_key dual-write behavior."""

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.mariadb.set_metadata', return_value=True)
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={'keep': 'yes', 'remove': 'me'}
                ))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_remove_metadata_key_dual_writes(
            self, mock_lock, mock_get, mock_set, mock_etcd_set):
        mock_lock.return_value.__enter__ = mock.Mock()
        mock_lock.return_value.__exit__ = mock.Mock(return_value=False)

        d = DatabaseBackedObject(TEST_UUID)
        d.remove_metadata_key('remove')

        expected = {'keep': 'yes'}
        mock_set.assert_called_once_with(d.object_type, TEST_UUID, expected)
        mock_etcd_set.assert_called_once_with('metadata', expected)

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.mariadb.set_metadata')
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    metadata={'keep': 'yes'}
                ))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject.get_lock_attr')
    def test_remove_nonexistent_key_is_noop(
            self, mock_lock, mock_get, mock_set, mock_etcd_set):
        mock_lock.return_value.__enter__ = mock.Mock()
        mock_lock.return_value.__exit__ = mock.Mock(return_value=False)

        d = DatabaseBackedObject(TEST_UUID)
        d.remove_metadata_key('nonexistent')

        mock_set.assert_not_called()
        mock_etcd_set.assert_not_called()


class HardDeleteMetadataTestCase(base.ShakenFistTestCase):
    """Test hard_delete cleans up object_metadata."""

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.delete_object_metadata', return_value=True)
    @mock.patch('shakenfist.mariadb.delete_state', return_value=True)
    @mock.patch('shakenfist.etcd.delete_all')
    @mock.patch('shakenfist.etcd.delete')
    def test_hard_delete_cleans_up_object_metadata(
            self, mock_etcd_del, mock_etcd_del_all,
            mock_del_state, mock_del_meta, mock_event):
        d = DatabaseBackedObject(TEST_UUID)
        d.hard_delete()

        mock_del_meta.assert_called_once_with(d.object_type, TEST_UUID)
        mock_del_state.assert_called_once_with(d.object_type, TEST_UUID)
        mock_etcd_del.assert_called_once()
        mock_etcd_del_all.assert_called_once()


class LastClusterOperationTestCase(base.ShakenFistTestCase):
    """Test last_cluster_operation property routing."""

    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    last_cluster_operation={
                        'op_type': 'instance_preflight',
                        'op_uuid': 'abcd1234-0000-0000-0000-000000000000'
                    }
                ))
    def test_lco_reads_from_mariadb(self, mock_get):
        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        result = d.last_cluster_operation
        self.assertEqual(result, {
            'op_type': 'instance_preflight',
            'op_uuid': 'abcd1234-0000-0000-0000-000000000000'
        })

    @mock.patch(
        'shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
        return_value={'op_type': 'etcd_op', 'op_uuid': 'from-etcd'})
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=None)
    def test_lco_falls_back_to_etcd(self, mock_get, mock_etcd_get):
        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        result = d.last_cluster_operation
        self.assertEqual(result, {
            'op_type': 'etcd_op', 'op_uuid': 'from-etcd'
        })
        mock_etcd_get.assert_called_once_with('last_cluster_operation')

    @mock.patch(
        'shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
        return_value={'op_type': 'etcd_op', 'op_uuid': 'from-etcd'})
    @mock.patch('shakenfist.mariadb.get_object_metadata',
                return_value=ObjectMetadataData(
                    object_type='unknown',
                    object_uuid=TEST_UUID,
                    last_cluster_operation=None
                ))
    def test_lco_falls_back_when_mariadb_lco_is_none(
            self, mock_get, mock_etcd_get):
        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        result = d.last_cluster_operation
        self.assertEqual(result, {
            'op_type': 'etcd_op', 'op_uuid': 'from-etcd'
        })


class SetLastClusterOperationTestCase(base.ShakenFistTestCase):
    """Test set_last_cluster_operation dual-write behavior."""

    @mock.patch(
        'shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.mariadb.set_last_cluster_operation',
                return_value=True)
    def test_set_lco_dual_writes(self, mock_set_lco, mock_etcd_set):
        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        d.set_last_cluster_operation('instance_preflight', 'op-uuid-1')

        expected = {
            'op_type': 'instance_preflight',
            'op_uuid': 'op-uuid-1'
        }
        mock_set_lco.assert_called_once_with(
            d.object_type, TEST_UUID, expected)
        mock_etcd_set.assert_called_once_with(
            'last_cluster_operation', expected)


class ObjectMetadataDataTestCase(base.ShakenFistTestCase):
    """Test the Pydantic schema itself."""

    def test_create_with_all_fields(self):
        data = ObjectMetadataData(
            object_type='instance',
            object_uuid=TEST_UUID,
            metadata={'key': 'value'},
            last_cluster_operation={'op_type': 'test', 'op_uuid': 'uuid'}
        )
        self.assertEqual(data.object_type, 'instance')
        self.assertEqual(data.object_uuid, TEST_UUID)
        self.assertEqual(data.metadata, {'key': 'value'})
        self.assertEqual(data.last_cluster_operation,
                         {'op_type': 'test', 'op_uuid': 'uuid'})

    def test_create_with_defaults(self):
        data = ObjectMetadataData(
            object_type='network',
            object_uuid=TEST_UUID
        )
        self.assertIsNone(data.metadata)
        self.assertIsNone(data.last_cluster_operation)

    def test_is_mutable(self):
        data = ObjectMetadataData(
            object_type='instance',
            object_uuid=TEST_UUID,
            metadata={'key': 'old'}
        )
        data.metadata = {'key': 'new'}
        self.assertEqual(data.metadata, {'key': 'new'})
