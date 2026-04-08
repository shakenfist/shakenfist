# Copyright 2019 Michael Still and contributors
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObjectWithOperations
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.tests import base

TEST_UUID = '12345678-1234-4321-8234-123456789012'
OP_UUID = 'abcd1234-0000-4000-8000-000000000001'


class LastClusterOperationWithTargetsTestCase(base.ShakenFistTestCase):
    """Test last_cluster_operation reads from cluster_operation_targets first."""

    @mock.patch('shakenfist.mariadb.get_latest_cluster_operation_target',
                return_value=ClusterOperationTargetData(
                    operation_uuid=OP_UUID,
                    operation_type='instance_preflight',
                    target_object_type='unknown',
                    target_uuid=TEST_UUID,
                    sequence_number=1,
                    created_at=1000.0
                ))
    def test_lco_reads_from_targets_table(self, mock_get_latest):
        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        result = d.last_cluster_operation
        self.assertEqual(result, {
            'op_type': 'instance_preflight',
            'op_uuid': OP_UUID
        })
        mock_get_latest.assert_called_once_with(
            d.object_type, TEST_UUID)

    @mock.patch('shakenfist.mariadb.get_latest_cluster_operation_target',
                return_value=None)
    def test_lco_returns_none_when_no_targets(self, mock_get_latest):
        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        result = d.last_cluster_operation
        self.assertIsNone(result)
        mock_get_latest.assert_called_once_with(
            d.object_type, TEST_UUID)


class SetLastClusterOperationWithTargetsTestCase(base.ShakenFistTestCase):
    """Test set_last_cluster_operation writes to cluster_operation_targets."""

    @mock.patch('shakenfist.mariadb.create_cluster_operation_target',
                return_value=True)
    @mock.patch('shakenfist.baseobject.time')
    def test_set_lco_writes_to_targets_only(
            self, mock_time, mock_create_target):
        mock_time.time.return_value = 1234.5

        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        d.set_last_cluster_operation('instance_preflight', OP_UUID)

        # Verify cluster_operation_targets write
        mock_create_target.assert_called_once_with(
            operation_uuid=OP_UUID,
            operation_type='instance_preflight',
            target_object_type=d.object_type,
            target_uuid=TEST_UUID,
            created_at=1234.5
        )

    @mock.patch('shakenfist.mariadb.create_cluster_operation_target')
    def test_set_lco_in_memory_skips_mariadb(
            self, mock_create_target):
        d = DatabaseBackedObjectWithOperations(
            TEST_UUID, in_memory_only=True)
        d.set_last_cluster_operation('net_op', OP_UUID)

        mock_create_target.assert_not_called()


class HardDeleteWithTargetsTestCase(base.ShakenFistTestCase):
    """Test hard_delete cleans up cluster_operation_targets."""

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.delete_object_metadata',
                return_value=True)
    @mock.patch('shakenfist.mariadb.delete_state', return_value=True)
    @mock.patch(
        'shakenfist.mariadb.delete_cluster_operation_targets_for_object',
        return_value=True)
    @mock.patch('shakenfist.etcd.delete_all')
    @mock.patch('shakenfist.etcd.delete')
    def test_hard_delete_cleans_up_targets(
            self, mock_etcd_del, mock_etcd_del_all,
            mock_del_targets, mock_del_state,
            mock_del_meta, mock_event):
        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        d.hard_delete()

        mock_del_targets.assert_called_once_with(
            d.object_type, TEST_UUID)
        mock_del_meta.assert_called_once_with(
            d.object_type, TEST_UUID)
        mock_del_state.assert_called_once_with(
            d.object_type, TEST_UUID)


class ClusterOperationTargetDataTestCase(base.ShakenFistTestCase):
    """Test the Pydantic schema."""

    def test_create_with_all_fields(self):
        data = ClusterOperationTargetData(
            operation_uuid=OP_UUID,
            operation_type='instance_preflight',
            target_object_type='instance',
            target_uuid=TEST_UUID,
            sequence_number=42,
            created_at=1000.0
        )
        self.assertEqual(data.operation_uuid, OP_UUID)
        self.assertEqual(data.operation_type, 'instance_preflight')
        self.assertEqual(data.target_object_type, 'instance')
        self.assertEqual(data.target_uuid, TEST_UUID)
        self.assertEqual(data.sequence_number, 42)
        self.assertEqual(data.created_at, 1000.0)

    def test_sequence_number_defaults_to_none(self):
        data = ClusterOperationTargetData(
            operation_uuid=OP_UUID,
            operation_type='net_op',
            target_object_type='network',
            target_uuid=TEST_UUID,
            created_at=2000.0
        )
        self.assertIsNone(data.sequence_number)

    def test_is_frozen(self):
        data = ClusterOperationTargetData(
            operation_uuid=OP_UUID,
            operation_type='net_op',
            target_object_type='network',
            target_uuid=TEST_UUID,
            created_at=2000.0
        )
        with self.assertRaises(Exception):
            data.operation_type = 'changed'

    def test_external_view(self):
        data = ClusterOperationTargetData(
            operation_uuid=OP_UUID,
            operation_type='instance_preflight',
            target_object_type='instance',
            target_uuid=TEST_UUID,
            sequence_number=1,
            created_at=1000.0
        )
        view = data.external_view()
        self.assertEqual(view['operation_uuid'], OP_UUID)
        self.assertEqual(view['operation_type'], 'instance_preflight')
        self.assertEqual(view['target_object_type'], 'instance')
        self.assertEqual(view['target_uuid'], TEST_UUID)
        self.assertEqual(view['sequence_number'], 1)
        self.assertEqual(view['created_at'], 1000.0)
