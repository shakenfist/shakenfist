# Copyright 2019 Michael Still and contributors
import time
from unittest import mock

from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObjectWithOperations
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
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
    """Test _set_last_cluster_operation writes to cluster_operation_targets."""

    @mock.patch('shakenfist.mariadb.create_cluster_operation_target',
                return_value=True)
    @mock.patch('shakenfist.baseobject.time')
    def test_set_lco_writes_to_targets_only(
            self, mock_time, mock_create_target):
        mock_time.time.return_value = 1234.5

        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        d._set_last_cluster_operation('instance_preflight', OP_UUID)

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
        d._set_last_cluster_operation('net_op', OP_UUID)

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


class HasPendingClusterOperationTestCase(base.ShakenFistTestCase):
    """Test has_pending_cluster_operation() on DatabaseBackedObjectWithOperations."""

    @mock.patch('shakenfist.mariadb.has_pending_cluster_operation_target',
                return_value=False)
    def test_no_targets_returns_false(self, mock_has_pending):
        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        result = d.has_pending_cluster_operation()
        self.assertFalse(result)
        mock_has_pending.assert_called_once_with(d.object_type, TEST_UUID)

    @mock.patch('shakenfist.mariadb.has_pending_cluster_operation_target',
                return_value=True)
    def test_in_flight_target_returns_true(self, mock_has_pending):
        d = DatabaseBackedObjectWithOperations(TEST_UUID)
        result = d.has_pending_cluster_operation()
        self.assertTrue(result)
        mock_has_pending.assert_called_once_with(d.object_type, TEST_UUID)

    @mock.patch('shakenfist.mariadb.has_pending_cluster_operation_target')
    def test_in_memory_object_short_circuits(self, mock_has_pending):
        d = DatabaseBackedObjectWithOperations(TEST_UUID, in_memory_only=True)
        result = d.has_pending_cluster_operation()
        self.assertFalse(result)
        mock_has_pending.assert_not_called()


class HasPendingClusterOperationQueryTestCase(base.ShakenFistTestCase):
    """Test has_pending_cluster_operation_target dispatcher routing."""

    @mock.patch('shakenfist.mariadb._direct_has_pending_cluster_operation_target',
                return_value=False)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct_when_no_service(
            self, mock_use_svc, mock_direct):
        result = mariadb.has_pending_cluster_operation_target(
            'instance', TEST_UUID)
        self.assertFalse(result)
        mock_direct.assert_called_once_with('instance', TEST_UUID)

    @mock.patch('shakenfist.mariadb._grpc_has_pending_cluster_operation_target',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc_when_service_enabled(
            self, mock_use_svc, mock_grpc):
        result = mariadb.has_pending_cluster_operation_target(
            'instance', TEST_UUID)
        self.assertTrue(result)
        mock_grpc.assert_called_once_with('instance', TEST_UUID)


class DirectHasPendingClusterOperationTargetTestCase(
        base.ShakenFistTestCase):
    """Test the SQL-level _direct_has_pending_cluster_operation_target.

    Tests 4-8 from phase 1 plan: no targets, one in-flight, one terminal,
    terminal-then-in-flight (the latest-only race fix), multiple terminal.
    """

    def setUp(self):
        super().setUp()
        from shakenfist.config import BaseSettings

        class _FakeConfig(BaseSettings):
            DATABASE_NODE_IP: str = '192.168.1.1'
            DATABASE_API_PORT: int = 13005
            MARIADB_HOST: str = 'localhost'
            NODE_NAME: str = 'testnode'

        self.config_patch = mock.patch(
            'shakenfist.mariadb.config', _FakeConfig())
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def _make_tables(self):
        import sqlalchemy as sa
        metadata = sa.MetaData()
        targets_table = sa.Table(
            'cluster_operation_targets',
            metadata,
            sa.Column('operation_uuid', sa.String(36), primary_key=True),
            sa.Column('target_object_type', sa.String(32)),
            sa.Column('target_uuid', sa.String(36)),
        )
        states_table = sa.Table(
            'object_states',
            metadata,
            sa.Column('object_uuid', sa.String(36)),
            sa.Column('state_value', sa.String(32)),
        )
        return targets_table, states_table

    def _make_engine(self, scalar_result):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.scalar.return_value = scalar_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        return mock_engine, mock_conn

    @mock.patch('shakenfist.mariadb._get_object_states_table')
    @mock.patch('shakenfist.mariadb._get_cluster_operation_targets_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_targets_returns_false(
            self, mock_get_engine, mock_get_table, mock_get_states):
        targets_table, states_table = self._make_tables()
        mock_get_table.return_value = targets_table
        mock_get_states.return_value = states_table
        mock_engine, mock_conn = self._make_engine(scalar_result=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_has_pending_cluster_operation_target(
            'instance', TEST_UUID)

        self.assertFalse(result)
        mock_conn.execute.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_object_states_table')
    @mock.patch('shakenfist.mariadb._get_cluster_operation_targets_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_one_in_flight_target_returns_true(
            self, mock_get_engine, mock_get_table, mock_get_states):
        targets_table, states_table = self._make_tables()
        mock_get_table.return_value = targets_table
        mock_get_states.return_value = states_table
        mock_engine, mock_conn = self._make_engine(scalar_result=True)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_has_pending_cluster_operation_target(
            'instance', TEST_UUID)

        self.assertTrue(result)

    @mock.patch('shakenfist.mariadb._get_object_states_table')
    @mock.patch('shakenfist.mariadb._get_cluster_operation_targets_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_one_terminal_target_returns_false(
            self, mock_get_engine, mock_get_table, mock_get_states):
        targets_table, states_table = self._make_tables()
        mock_get_table.return_value = targets_table
        mock_get_states.return_value = states_table
        # Terminal operation — JOIN to object_states finds no active rows.
        mock_engine, mock_conn = self._make_engine(scalar_result=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_has_pending_cluster_operation_target(
            'instance', TEST_UUID)

        self.assertFalse(result)

    @mock.patch('shakenfist.mariadb._get_object_states_table')
    @mock.patch('shakenfist.mariadb._get_cluster_operation_targets_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_terminal_then_in_flight_returns_true(
            self, mock_get_engine, mock_get_table, mock_get_states):
        """The latest-only race: a later terminal op must not hide an earlier
        in-flight op. The query must check ALL rows, not just the latest."""
        targets_table, states_table = self._make_tables()
        mock_get_table.return_value = targets_table
        mock_get_states.return_value = states_table
        # Scalar would return True because the JOIN finds the in-flight row.
        mock_engine, mock_conn = self._make_engine(scalar_result=True)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_has_pending_cluster_operation_target(
            'instance', TEST_UUID)

        self.assertTrue(result)

        # Verify the SQL shape: must contain state_value IN (...) with all
        # three active states. This ensures the query actually checks state
        # rather than just counting rows.
        executed_stmt = mock_conn.execute.call_args[0][0]
        compiled = str(executed_stmt.compile(
            compile_kwargs={'literal_binds': True}))
        self.assertIn('state_value IN', compiled)
        self.assertIn("'queued'", compiled)
        self.assertIn("'preflight'", compiled)
        self.assertIn("'executing'", compiled)

    @mock.patch('shakenfist.mariadb._get_object_states_table')
    @mock.patch('shakenfist.mariadb._get_cluster_operation_targets_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_multiple_terminal_targets_returns_false(
            self, mock_get_engine, mock_get_table, mock_get_states):
        targets_table, states_table = self._make_tables()
        mock_get_table.return_value = targets_table
        mock_get_states.return_value = states_table
        # All operations terminal — no JOIN match.
        mock_engine, mock_conn = self._make_engine(scalar_result=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_has_pending_cluster_operation_target(
            'instance', TEST_UUID)

        self.assertFalse(result)


HOTPLUG_INSTANCE_UUID = 'eeee1111-1111-4111-8111-111111111111'
HOTPLUG_NETWORK_UUID = 'ffff1111-1111-4111-8111-111111111111'
HOTPLUG_INTERFACE_UUID = 'aaaa2222-2222-4222-8222-222222222222'
HOTPLUG_NODE_UUID = 'bbbb3333-3333-4333-8333-333333333333'


class HotPlugTripleTargetRegressionTestCase(base.ShakenFistTestCase):
    """Unit-level regression for the hot-plug triple-target bug.

    This test mocks mariadb.create_cluster_operation_target so it
    only proves the schema's target_fields declaration and the
    enqueue_cluster_operation() call-site fan-out are correct. It
    does NOT exercise the actual database UNIQUE constraint --
    see HotPlugTargetWriteIntegrationTestCase below for that.

    Originally added for commit 8923391c. Kept for fast feedback;
    the integration test catches the deeper bug.
    """

    def setUp(self):
        super().setUp()
        self.mock_create_and_enqueue = mock.patch(
            'shakenfist.mariadb.create_and_enqueue_cluster_operation',
            return_value=True,
        ).start()
        self.mock_create_target = mock.patch(
            'shakenfist.mariadb.create_cluster_operation_target',
        ).start()
        self.mock_add_event_multi = mock.patch(
            'shakenfist.schema.operations.util.eventlog.add_event_multi',
        ).start()
        self.mock_time = mock.patch(
            'shakenfist.schema.operations.util.time.time',
            return_value=5000.0,
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_hot_plug_enqueue_writes_three_target_rows(self):
        """Three cluster_operation_targets rows must be written: instance,
        network, and interface. This locks in that the original CI failure
        from commit 8923391c cannot regress.
        """
        from shakenfist.schema.operations.node_inst_net_iface_op import (
            create_and_enqueue, model_tasks)
        from shakenfist.schema.operations.baseclusteroperation import PRIORITY

        create_and_enqueue(
            HOTPLUG_NODE_UUID,
            HOTPLUG_INSTANCE_UUID,
            HOTPLUG_NETWORK_UUID,
            HOTPLUG_INTERFACE_UUID,
            [model_tasks.hot_plug_instance_interface],
            PRIORITY.user_waiting,
        )

        self.assertEqual(3, self.mock_create_target.call_count)
        target_types = {
            call.kwargs['target_object_type']
            for call in self.mock_create_target.call_args_list
        }
        self.assertEqual(
            {ObjectType.INSTANCE, ObjectType.NETWORK, ObjectType.INTERFACE},
            target_types,
        )
        target_uuids = {
            call.kwargs['target_uuid']
            for call in self.mock_create_target.call_args_list
        }
        self.assertEqual(
            {HOTPLUG_INSTANCE_UUID, HOTPLUG_NETWORK_UUID,
             HOTPLUG_INTERFACE_UUID},
            target_uuids,
        )


class HotPlugTargetWriteIntegrationTestCase(base.ShakenFistTestCase):
    """Integration regression: hot-plug must persist three target rows.

    Drives _direct_create_cluster_operation_target() against a real
    in-memory SQLite engine so the actual UNIQUE constraint is
    exercised. The pre-fix schema (v1) declared operation_uuid UNIQUE,
    which silently dropped every target row after the first --
    masked by the IntegrityError-as-True handler in the writer. The
    fixed schema (v2) replaces that with a composite UNIQUE on
    (operation_uuid, target_object_type, target_uuid).

    Also asserts has_pending_cluster_operation_target() sees the
    network target while the op is queued -- the exact gate that
    was being bypassed in the recurring "Recreating not okay
    network on hypervisor" CI failure.
    """

    def setUp(self):
        super().setUp()

        # Late imports: sqlalchemy is heavy at import time and only
        # this test class needs it; pulling it in lazily keeps the
        # rest of the test suite's import phase fast. The shakenfist
        # mariadb module is rebound locally as ``mariadb_mod`` so
        # test methods can call ``self._mariadb.<fn>()`` without
        # shadowing the module-level ``mariadb`` import the file
        # uses elsewhere.
        import sqlalchemy as sa
        from shakenfist import mariadb as mariadb_mod

        # Build an isolated MetaData for this test so the global
        # module-level metadata (which other tests share) isn't
        # affected. We rebuild the two tables under test against it.
        self._sa = sa
        self._mariadb = mariadb_mod
        self._metadata = sa.MetaData()

        # Use Integer (not BigInteger) so SQLite's rowid alias kicks
        # in for AUTOINCREMENT. Production runs on MariaDB which uses
        # BigInteger; the column type isn't what we're testing here.
        self._targets_table = sa.Table(
            'cluster_operation_targets',
            self._metadata,
            sa.Column('sequence_number', sa.Integer(),
                      primary_key=True, autoincrement=True),
            sa.Column('operation_uuid', sa.String(36), nullable=False),
            sa.Column('operation_type', sa.String(64), nullable=False),
            sa.Column('target_object_type', sa.String(64),
                      nullable=False),
            sa.Column('target_uuid', sa.String(36), nullable=False),
            sa.Column('created_at', sa.Double(), nullable=False),
            sa.UniqueConstraint(
                'operation_uuid', 'target_object_type', 'target_uuid',
                name='uq_cot_op_target'),
        )
        self._states_table = sa.Table(
            'object_states',
            self._metadata,
            sa.Column('object_uuid', sa.String(36), nullable=False),
            sa.Column('object_type', sa.String(64), nullable=False),
            sa.Column('state_value', sa.String(64), nullable=False),
            sa.Column('update_time', sa.Double(), nullable=False),
            sa.Column('message', sa.String(1024), nullable=True),
            sa.PrimaryKeyConstraint('object_type', 'object_uuid'),
        )

        # SQLite :memory: databases are per-connection by default;
        # pool with StaticPool so every checkout shares one DB.
        # Late import for the same reason sa is late above -- only
        # this fixture needs the pool class.
        from sqlalchemy.pool import StaticPool
        self._engine = sa.create_engine(
            'sqlite:///:memory:',
            connect_args={'check_same_thread': False},
            poolclass=StaticPool)
        self._metadata.create_all(self._engine)

        # Route the mariadb helpers at the real in-memory engine and
        # tables. The helpers under test (_direct_create_cluster_
        # operation_target, _direct_has_pending_cluster_operation_
        # target) read these accessors and the table objects each
        # invocation.
        self._patches = [
            mock.patch(
                'shakenfist.mariadb._get_engine',
                return_value=self._engine),
            mock.patch(
                'shakenfist.mariadb._get_cluster_operation_targets_table',
                return_value=self._targets_table),
            mock.patch(
                'shakenfist.mariadb._get_object_states_table',
                return_value=self._states_table),
            mock.patch(
                'shakenfist.mariadb._use_database_service',
                return_value=False),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(mock.patch.stopall)

    def _insert_op_state(self, op_uuid, state_value):
        """Drop a row in the fake object_states table for an op."""
        with self._engine.begin() as conn:
            conn.execute(self._states_table.insert().values(
                object_uuid=op_uuid,
                object_type='node_inst_net_iface_op',
                state_value=state_value,
                update_time=1000.0,
                message=None,
            ))

    def _count_target_rows(self):
        with self._engine.connect() as conn:
            return conn.execute(
                self._sa.select(self._sa.func.count()).select_from(
                    self._targets_table)).scalar()

    def _target_rows(self):
        with self._engine.connect() as conn:
            return list(conn.execute(
                self._sa.select(self._targets_table)).mappings())

    def test_three_target_rows_persisted(self):
        """All three (instance, network, interface) targets land in
        the table -- the v1 schema dropped the latter two."""
        op_uuid = 'cccc1111-1111-4111-8111-111111111111'
        op_type = 'node_inst_net_iface_op'

        for field, target_uuid, target_type in (
                ('instance', HOTPLUG_INSTANCE_UUID, 'instance'),
                ('network', HOTPLUG_NETWORK_UUID, 'network'),
                ('interface', HOTPLUG_INTERFACE_UUID, 'interface')):
            ok = self._mariadb._direct_create_cluster_operation_target(
                operation_uuid=op_uuid,
                operation_type=op_type,
                target_object_type=target_type,
                target_uuid=target_uuid,
                created_at=1000.0)
            self.assertTrue(ok, f'write for {field} target should succeed')

        self.assertEqual(3, self._count_target_rows())

        rows = self._target_rows()
        target_uuids = {r['target_uuid'] for r in rows}
        self.assertEqual(
            {HOTPLUG_INSTANCE_UUID, HOTPLUG_NETWORK_UUID,
             HOTPLUG_INTERFACE_UUID},
            target_uuids)
        target_types = {r['target_object_type'] for r in rows}
        self.assertEqual({'instance', 'network', 'interface'},
                         target_types)

    def test_duplicate_op_target_pair_is_idempotent(self):
        """Same (op_uuid, target_type, target_uuid) triple written
        twice must not raise -- callers rely on idempotency."""
        op_uuid = 'cccc1111-1111-4111-8111-111111111111'

        ok = self._mariadb._direct_create_cluster_operation_target(
            operation_uuid=op_uuid,
            operation_type='node_inst_net_iface_op',
            target_object_type='network',
            target_uuid=HOTPLUG_NETWORK_UUID,
            created_at=1000.0)
        self.assertTrue(ok)

        ok = self._mariadb._direct_create_cluster_operation_target(
            operation_uuid=op_uuid,
            operation_type='node_inst_net_iface_op',
            target_object_type='network',
            target_uuid=HOTPLUG_NETWORK_UUID,
            created_at=2000.0)
        self.assertTrue(ok)

        self.assertEqual(1, self._count_target_rows())

    def test_pending_network_target_visible_while_op_queued(self):
        """has_pending_cluster_operation_target(NETWORK, uuid) must
        return True while the op is in 'queued' state. This is the
        gate Network.is_okay() uses to defer the maintainer. The
        v1 schema bug dropped this target row, making the gate
        useless for hot-plug.
        """
        op_uuid = 'cccc1111-1111-4111-8111-111111111111'

        self._insert_op_state(op_uuid, 'queued')
        ok = self._mariadb._direct_create_cluster_operation_target(
            operation_uuid=op_uuid,
            operation_type='node_inst_net_iface_op',
            target_object_type='network',
            target_uuid=HOTPLUG_NETWORK_UUID,
            created_at=1000.0)
        self.assertTrue(ok)

        result = (
            self._mariadb._direct_has_pending_cluster_operation_target(
                'network', HOTPLUG_NETWORK_UUID))
        self.assertTrue(result)

    def test_pending_returns_false_after_op_completes(self):
        """Once the op state moves to 'complete', the gate releases."""
        op_uuid = 'cccc1111-1111-4111-8111-111111111111'

        self._insert_op_state(op_uuid, 'complete')
        self._mariadb._direct_create_cluster_operation_target(
            operation_uuid=op_uuid,
            operation_type='node_inst_net_iface_op',
            target_object_type='network',
            target_uuid=HOTPLUG_NETWORK_UUID,
            created_at=1000.0)

        result = (
            self._mariadb._direct_has_pending_cluster_operation_target(
                'network', HOTPLUG_NETWORK_UUID))
        self.assertFalse(result)

    def test_non_uniqueness_integrity_error_returns_false(self):
        """A NOT NULL violation (or any non-uniqueness IntegrityError)
        must surface as False, not be swallowed as idempotency.

        Before the keyword-match was tightened, the writer's
        IntegrityError handler returned True for any IntegrityError --
        which hid bugs like passing None for a required column. The
        narrowed match only forgives the composite UNIQUE we
        actually want to be idempotent on.
        """
        ok = self._mariadb._direct_create_cluster_operation_target(
            operation_uuid=None,
            operation_type='node_inst_net_iface_op',
            target_object_type='network',
            target_uuid=HOTPLUG_NETWORK_UUID,
            created_at=1000.0)
        self.assertFalse(ok)
        self.assertEqual(0, self._count_target_rows())

    def test_full_enqueue_persists_three_rows(self):
        """End-to-end: calling node_inst_net_iface_op.create_and_enqueue
        must result in three persisted cluster_operation_targets rows
        (instance, network, interface) in the real database, not just
        three call-sites to create_cluster_operation_target.

        This is the test the v1 schema bug would have failed: the
        UNIQUE(operation_uuid) constraint silently dropped the
        network and interface rows after the instance row landed.
        """
        # Late import to keep the schema module (and its transitive
        # cluster-operation-target machinery) out of this test file's
        # import path -- only this one method needs it, and pulling
        # it at module scope would couple every test in the file to
        # the operation-schema import chain.
        from shakenfist.schema.operations.node_inst_net_iface_op import (
            create_and_enqueue, model_tasks)
        from shakenfist.schema.operations.baseclusteroperation import PRIORITY

        # The op + state + work-queue write is its own MariaDB
        # transaction; here we only care about the target writes,
        # so stub it out as success.
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                return_value=True):
            _, op_uuid = create_and_enqueue(
                HOTPLUG_NODE_UUID,
                HOTPLUG_INSTANCE_UUID,
                HOTPLUG_NETWORK_UUID,
                HOTPLUG_INTERFACE_UUID,
                [model_tasks.hot_plug_instance_interface],
                PRIORITY.user_waiting,
            )

        self.assertEqual(3, self._count_target_rows())

        rows = self._target_rows()
        for r in rows:
            self.assertEqual(op_uuid, r['operation_uuid'])
            self.assertEqual('node_inst_net_iface_op', r['operation_type'])

        target_uuids = {r['target_uuid'] for r in rows}
        self.assertEqual(
            {HOTPLUG_INSTANCE_UUID, HOTPLUG_NETWORK_UUID,
             HOTPLUG_INTERFACE_UUID},
            target_uuids)
        target_types = {r['target_object_type'] for r in rows}
        self.assertEqual({'instance', 'network', 'interface'},
                         target_types)


NETWORK_UUID = 'aaaa9999-9999-4999-8999-999999999999'
OP_UUID_T1 = 'bbbb9999-9999-4999-8999-000000000001'
OP_UUID_T2 = 'bbbb9999-9999-4999-8999-000000000002'
OP_UUID_T3 = 'bbbb9999-9999-4999-8999-000000000003'
OP_UUID_OTHER_TYPE = 'bbbb9999-9999-4999-8999-000000000004'
OP_UUID_QUEUED = 'bbbb9999-9999-4999-8999-000000000005'


class GetRecentTerminalOpStatesForTargetTestCase(base.ShakenFistTestCase):
    """Direct SQL test for _direct_get_recent_terminal_op_states_for_target.

    Drives the helper against a real in-memory SQLite engine so we exercise
    the actual JOIN, ORDER BY, LIMIT, and the op_type filter.
    """

    def setUp(self):
        super().setUp()

        import sqlalchemy as sa
        from shakenfist import mariadb as mariadb_mod

        self._sa = sa
        self._mariadb = mariadb_mod
        self._metadata = sa.MetaData()

        self._targets_table = sa.Table(
            'cluster_operation_targets',
            self._metadata,
            sa.Column('sequence_number', sa.Integer(),
                      primary_key=True, autoincrement=True),
            sa.Column('operation_uuid', sa.String(36), nullable=False),
            sa.Column('operation_type', sa.String(64), nullable=False),
            sa.Column('target_object_type', sa.String(64),
                      nullable=False),
            sa.Column('target_uuid', sa.String(36), nullable=False),
            sa.Column('created_at', sa.Double(), nullable=False),
        )
        self._states_table = sa.Table(
            'object_states',
            self._metadata,
            sa.Column('object_uuid', sa.String(36), nullable=False),
            sa.Column('object_type', sa.String(64), nullable=False),
            sa.Column('state_value', sa.String(64), nullable=False),
            sa.Column('update_time', sa.Double(), nullable=False),
            sa.Column('message', sa.String(1024), nullable=True),
            sa.PrimaryKeyConstraint('object_type', 'object_uuid'),
        )

        from sqlalchemy.pool import StaticPool
        self._engine = sa.create_engine(
            'sqlite:///:memory:',
            connect_args={'check_same_thread': False},
            poolclass=StaticPool)
        self._metadata.create_all(self._engine)

        self._patches = [
            mock.patch(
                'shakenfist.mariadb._get_engine',
                return_value=self._engine),
            mock.patch(
                'shakenfist.mariadb._get_cluster_operation_targets_table',
                return_value=self._targets_table),
            mock.patch(
                'shakenfist.mariadb._get_object_states_table',
                return_value=self._states_table),
            mock.patch(
                'shakenfist.mariadb._use_database_service',
                return_value=False),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(mock.patch.stopall)

    def _insert_target(self, op_uuid, op_type, target_type, target_uuid,
                       created_at):
        with self._engine.begin() as conn:
            conn.execute(self._targets_table.insert().values(
                operation_uuid=op_uuid,
                operation_type=op_type,
                target_object_type=target_type,
                target_uuid=target_uuid,
                created_at=created_at,
            ))

    def _insert_state(self, op_uuid, op_type, state_value, update_time):
        with self._engine.begin() as conn:
            conn.execute(self._states_table.insert().values(
                object_uuid=op_uuid,
                object_type=op_type,
                state_value=state_value,
                update_time=update_time,
                message=None,
            ))

    def test_empty_when_no_targets(self):
        """No cluster_operation_targets rows -> empty list."""
        results = (
            self._mariadb._direct_get_recent_terminal_op_states_for_target(
                'network', NETWORK_UUID, limit=10))
        self.assertEqual([], results)

    def test_limit_is_honoured(self):
        """3 matching terminal rows with limit=2 returns 2 rows."""
        self._insert_target(OP_UUID_T1, 'net_op', 'network',
                            NETWORK_UUID, 1000.0)
        self._insert_state(OP_UUID_T1, 'net_op', 'complete', 1001.0)
        self._insert_target(OP_UUID_T2, 'net_op', 'network',
                            NETWORK_UUID, 2000.0)
        self._insert_state(OP_UUID_T2, 'net_op', 'error', 2001.0)
        self._insert_target(OP_UUID_T3, 'net_op', 'network',
                            NETWORK_UUID, 3000.0)
        self._insert_state(OP_UUID_T3, 'net_op', 'error', 3001.0)

        results = (
            self._mariadb._direct_get_recent_terminal_op_states_for_target(
                'network', NETWORK_UUID, limit=2))
        self.assertEqual(2, len(results))

    def test_results_ordered_newest_first(self):
        """Rows are returned in descending update_time order."""
        self._insert_target(OP_UUID_T1, 'net_op', 'network',
                            NETWORK_UUID, 1000.0)
        self._insert_state(OP_UUID_T1, 'net_op', 'complete', 1001.0)
        self._insert_target(OP_UUID_T2, 'net_op', 'network',
                            NETWORK_UUID, 2000.0)
        self._insert_state(OP_UUID_T2, 'net_op', 'error', 2001.0)
        self._insert_target(OP_UUID_T3, 'net_op', 'network',
                            NETWORK_UUID, 3000.0)
        self._insert_state(OP_UUID_T3, 'net_op', 'abort', 3001.0)

        results = (
            self._mariadb._direct_get_recent_terminal_op_states_for_target(
                'network', NETWORK_UUID, limit=10))
        self.assertEqual(3, len(results))
        # Newest first by update_time: T3 (3001) -> T2 (2001) -> T1 (1001)
        self.assertEqual(OP_UUID_T3, results[0][0])
        self.assertEqual('abort', results[0][1])
        self.assertEqual(3001.0, results[0][2])
        self.assertEqual(OP_UUID_T2, results[1][0])
        self.assertEqual(OP_UUID_T1, results[2][0])

    def test_op_type_filter_narrows_results(self):
        """op_type filter excludes rows with non-matching operation_type."""
        self._insert_target(OP_UUID_T1, 'net_op', 'network',
                            NETWORK_UUID, 1000.0)
        self._insert_state(OP_UUID_T1, 'net_op', 'complete', 1001.0)
        self._insert_target(OP_UUID_OTHER_TYPE, 'instance_preflight',
                            'network', NETWORK_UUID, 2000.0)
        self._insert_state(OP_UUID_OTHER_TYPE, 'instance_preflight',
                           'complete', 2001.0)

        # Without filter: both rows visible.
        unfiltered = (
            self._mariadb._direct_get_recent_terminal_op_states_for_target(
                'network', NETWORK_UUID, limit=10))
        self.assertEqual(2, len(unfiltered))

        # With op_type='net_op': only the net_op row.
        filtered = (
            self._mariadb._direct_get_recent_terminal_op_states_for_target(
                'network', NETWORK_UUID, limit=10, op_type='net_op'))
        self.assertEqual(1, len(filtered))
        self.assertEqual(OP_UUID_T1, filtered[0][0])

    def test_terminal_state_filter_excludes_active_ops(self):
        """Ops in non-terminal states (e.g. 'queued') are excluded."""
        self._insert_target(OP_UUID_T1, 'net_op', 'network',
                            NETWORK_UUID, 1000.0)
        self._insert_state(OP_UUID_T1, 'net_op', 'complete', 1001.0)
        self._insert_target(OP_UUID_QUEUED, 'net_op', 'network',
                            NETWORK_UUID, 2000.0)
        self._insert_state(OP_UUID_QUEUED, 'net_op', 'queued', 2001.0)

        results = (
            self._mariadb._direct_get_recent_terminal_op_states_for_target(
                'network', NETWORK_UUID, limit=10))
        self.assertEqual(1, len(results))
        self.assertEqual(OP_UUID_T1, results[0][0])

    def test_terminal_state_filter_includes_all_four_states(self):
        """All four terminal states (complete, abort, deleted, error) are
        included by the filter."""
        for op_uuid, state in [
            (OP_UUID_T1, 'complete'),
            (OP_UUID_T2, 'abort'),
            (OP_UUID_T3, 'error'),
            (OP_UUID_OTHER_TYPE, 'deleted'),
        ]:
            self._insert_target(op_uuid, 'net_op', 'network',
                                NETWORK_UUID, 1000.0)
            self._insert_state(op_uuid, 'net_op', state, 1000.0)

        results = (
            self._mariadb._direct_get_recent_terminal_op_states_for_target(
                'network', NETWORK_UUID, limit=10))
        self.assertEqual(4, len(results))

    def test_target_uuid_filter_isolates_objects(self):
        """Empty list when no targets exist for the queried target_uuid."""
        # Insert a row for a DIFFERENT network uuid; the query should
        # not see it.
        other_uuid = 'cccc9999-9999-4999-8999-000000000099'
        self._insert_target(OP_UUID_T1, 'net_op', 'network',
                            other_uuid, 1000.0)
        self._insert_state(OP_UUID_T1, 'net_op', 'complete', 1001.0)

        results = (
            self._mariadb._direct_get_recent_terminal_op_states_for_target(
                'network', NETWORK_UUID, limit=10))
        self.assertEqual([], results)


class GetRecentTerminalOpStatesRoutingTestCase(base.ShakenFistTestCase):
    """Public wrapper routes to _direct or _grpc based on _use_database_service."""

    @mock.patch(
        'shakenfist.mariadb._direct_get_recent_terminal_op_states_for_target',
        return_value=[('op-uuid', 'complete', 1000.0)])
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_routes_to_direct_when_no_service(
            self, mock_use_svc, mock_direct):
        result = mariadb.get_recent_terminal_op_states_for_target(
            'network', NETWORK_UUID, limit=5, op_type='net_op')
        self.assertEqual([('op-uuid', 'complete', 1000.0)], result)
        mock_direct.assert_called_once_with(
            'network', NETWORK_UUID, 5, 'net_op')

    @mock.patch(
        'shakenfist.mariadb._grpc_get_recent_terminal_op_states_for_target',
        return_value=[])
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_routes_to_grpc_when_service_enabled(
            self, mock_use_svc, mock_grpc):
        result = mariadb.get_recent_terminal_op_states_for_target(
            'network', NETWORK_UUID, limit=1)
        self.assertEqual([], result)
        mock_grpc.assert_called_once_with(
            'network', NETWORK_UUID, 1, None)
