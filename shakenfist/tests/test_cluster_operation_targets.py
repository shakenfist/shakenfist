# Copyright 2019 Michael Still and contributors
import time
from unittest import mock

from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObjectWithOperations
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.object_state import State
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd

TEST_UUID = '12345678-1234-4321-8234-123456789012'
OP_UUID = 'abcd1234-0000-4000-8000-000000000001'
OP_UUID_OLD = 'abcd1234-0000-4000-8000-000000000002'
OP_UUID_NEW = 'abcd1234-0000-4000-8000-000000000003'
OP_UUID_ACTIVE = 'abcd1234-0000-4000-8000-000000000004'


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


class DirectDeleteStaleClusterOperationTargetsTestCase(
        base.ShakenFistTestCase):
    """Test the SQL-level _direct_delete_stale_cluster_operation_targets."""

    def setUp(self):
        super().setUp()
        from shakenfist.config import BaseSettings

        class _FakeConfig(BaseSettings):
            DATABASE_NODE_IP: str = '192.168.1.1'
            DATABASE_API_PORT: int = 13005
            DATABASE_USE_DIRECT_ETCD: bool = False
            MARIADB_HOST: str = 'localhost'
            NODE_NAME: str = 'testnode'

        self.config_patch = mock.patch(
            'shakenfist.mariadb.config', _FakeConfig())
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    @mock.patch('shakenfist.mariadb._get_object_states_table')
    @mock.patch('shakenfist.mariadb._get_cluster_operation_targets_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_stale_returns_rowcount(
            self, mock_get_engine, mock_get_table, mock_get_states):
        # Build a real table object so SQLAlchemy can build the WHERE
        # clause without complaining about missing columns. We do not
        # care what SQL is generated -- just that the function returns
        # the rowcount the engine reports.
        import sqlalchemy as sa
        metadata = sa.MetaData()
        targets_table = sa.Table(
            'cluster_operation_targets',
            metadata,
            sa.Column('operation_uuid', sa.String(36), primary_key=True),
            sa.Column('created_at', sa.Double()),
        )
        states_table = sa.Table(
            'object_states',
            metadata,
            sa.Column('object_uuid', sa.String(36)),
            sa.Column('state_value', sa.String(32)),
        )
        mock_get_table.return_value = targets_table
        mock_get_states.return_value = states_table

        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 4
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_stale_cluster_operation_targets(
            older_than=1234567890.0)

        self.assertEqual(result, 4)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

        # Verify the SQL is a DELETE with both an age predicate and a
        # NOT IN subquery referencing object_states. We compile to a
        # string and assert on its shape rather than its exact text.
        executed_stmt = mock_conn.execute.call_args[0][0]
        compiled = str(executed_stmt.compile(
            compile_kwargs={'literal_binds': True}))
        self.assertIn('DELETE FROM cluster_operation_targets', compiled)
        self.assertIn('created_at < 1234567890', compiled)
        self.assertIn('NOT IN', compiled)
        self.assertIn('object_states', compiled)
        self.assertIn("'queued'", compiled)
        self.assertIn("'preflight'", compiled)
        self.assertIn("'executing'", compiled)

    @mock.patch('shakenfist.mariadb._get_object_states_table')
    @mock.patch('shakenfist.mariadb._get_cluster_operation_targets_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_stale_returns_zero_on_operational_error(
            self, mock_get_engine, mock_get_table, mock_get_states):
        from sqlalchemy.exc import OperationalError
        import sqlalchemy as sa

        metadata = sa.MetaData()
        targets_table = sa.Table(
            'cluster_operation_targets',
            metadata,
            sa.Column('operation_uuid', sa.String(36), primary_key=True),
            sa.Column('created_at', sa.Double()),
        )
        states_table = sa.Table(
            'object_states',
            metadata,
            sa.Column('object_uuid', sa.String(36)),
            sa.Column('state_value', sa.String(32)),
        )
        mock_get_table.return_value = targets_table
        mock_get_states.return_value = states_table

        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'stmt', {}, Exception('boom'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_stale_cluster_operation_targets(
            older_than=1234567890.0)

        self.assertEqual(result, 0)


class MockDeleteStaleClusterOperationTargetsTestCase(
        base.ShakenFistTestCase):
    """Verify the in-memory mock implements the prune semantics.

    The mock is what unit tests rely on; any deviation between it and
    the real SQL behaviour would mask bugs.
    """

    def setUp(self):
        super().setUp()
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    def test_mock_prunes_old_terminal_keeps_active_and_recent(self):
        now = time.time()

        # Three target rows: old+terminal, old+active, recent.
        mariadb.create_cluster_operation_target(
            operation_uuid=OP_UUID_OLD,
            operation_type='instance_preflight',
            target_object_type='instance',
            target_uuid=TEST_UUID,
            created_at=now - 7_200)
        mariadb.create_cluster_operation_target(
            operation_uuid=OP_UUID_ACTIVE,
            operation_type='instance_preflight',
            target_object_type='instance',
            target_uuid=TEST_UUID,
            created_at=now - 7_200)
        mariadb.create_cluster_operation_target(
            operation_uuid=OP_UUID_NEW,
            operation_type='instance_preflight',
            target_object_type='instance',
            target_uuid=TEST_UUID,
            created_at=now - 60)

        # Mark the "active" operation as in-flight. The other two have
        # no object_states row, which the prune treats as eligible.
        mariadb.set_state(
            'instance_preflight', OP_UUID_ACTIVE,
            State(value='executing', update_time=now - 7_200))

        deleted = mariadb.delete_stale_cluster_operation_targets(3_600)

        self.assertEqual(deleted, 1)
        self.assertIsNone(
            mariadb.get_cluster_operation_target(OP_UUID_OLD))
        self.assertIsNotNone(
            mariadb.get_cluster_operation_target(OP_UUID_ACTIVE))
        self.assertIsNotNone(
            mariadb.get_cluster_operation_target(OP_UUID_NEW))
