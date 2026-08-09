# Copyright 2026 Michael Still and contributors
#
# Tests for _direct_create_and_enqueue_cluster_operation() from
# phase 3 of the etcd-removal ops-queues plan. This is the only
# multi-table atomic write in mariadb.py, so the tests focus on
# proving the three inserts run inside a single connection
# context and commit exactly once -- and that any failure rolls
# the whole transaction back.

from unittest import mock
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    MARIADB_GATEWAY_HOSTS: list[str] = ['192.168.1.1']
    MARIADB_GATEWAY_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()


OP_UUID_STR = '11111111-1111-4111-8111-111111111111'
NODE_UUID_STR = 'aaaa1111-1111-4111-8111-111111111111'
INSTANCE_UUID_STR = 'bbbb1111-1111-4111-8111-111111111111'
NETWORK_UUID_STR = 'cccc1111-1111-4111-8111-111111111111'


def _make_mock_engine():
    """Build a mock engine whose connect() works as a context manager."""
    mock_engine = mock.MagicMock()
    mock_conn = mock.MagicMock()
    mock_engine.connect.return_value.__enter__ = mock.Mock(
        return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = mock.Mock(
        return_value=False)
    return mock_engine, mock_conn


def _make_deadlock_error():
    """Build a SQLAlchemy OperationalError whose orig is shaped
    like the mysqldb driver's deadlock exception (errno 1213)."""
    orig = Exception(
        1213, 'Deadlock found when trying to get lock; '
              'try restarting transaction')
    return OperationalError('stmt', {}, orig)


def _make_metadata(**overrides):
    metadata = {
        'uuid': OP_UUID_STR,
        'node_uuid': NODE_UUID_STR,
        'instance_uuid': INSTANCE_UUID_STR,
        'network_uuid': NETWORK_UUID_STR,
        'priority': 'user_waiting',
        'tasks': ['fetch_image', 'provision_interfaces'],
    }
    metadata.update(overrides)
    return metadata


class CreateAndEnqueueClusterOperationTestCase(base.ShakenFistTestCase):
    """Tests for _direct_create_and_enqueue_cluster_operation()."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch(
            'shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_happy_path_writes_three_rows_and_commits_once(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        from uuid import UUID
        success, error = (
            mariadb._direct_create_and_enqueue_cluster_operation(
                UUID(OP_UUID_STR),
                'node_net_op',
                _make_metadata(),
                1000.0,
                'node-clusteroperation-user_waiting',
            )
        )

        self.assertTrue(success)
        self.assertEqual('', error)
        # Three inserts (cluster_operations, object_states,
        # work_queue) then one commit.
        self.assertEqual(mock_conn.execute.call_count, 3)
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_targets_written_in_same_transaction(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        from uuid import UUID
        from shakenfist.schema.object_types import ObjectType
        success, error = (
            mariadb._direct_create_and_enqueue_cluster_operation(
                UUID(OP_UUID_STR),
                'node_inst_net_iface_op',
                _make_metadata(),
                1000.0,
                'node-clusteroperation-user_waiting',
                targets=[
                    (ObjectType.INSTANCE, INSTANCE_UUID_STR),
                    (ObjectType.NETWORK, NETWORK_UUID_STR),
                ],
            )
        )

        self.assertTrue(success)
        self.assertEqual('', error)
        # Three base inserts (cluster_operations, object_states,
        # work_queue) plus one insert per target, all before a
        # single commit -- the atomicity that makes an enqueued op
        # and its target rows visible together.
        self.assertEqual(mock_conn.execute.call_count, 5)
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_duplicate_cluster_operation_rolls_back(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        # First execute (cluster_operations insert) raises duplicate.
        mock_conn.execute.side_effect = IntegrityError(
            'insert', {}, Exception('duplicate'))
        mock_get_engine.return_value = mock_engine

        from uuid import UUID
        success, error = (
            mariadb._direct_create_and_enqueue_cluster_operation(
                UUID(OP_UUID_STR),
                'node_net_op',
                _make_metadata(),
                1000.0,
                'node-clusteroperation-user_waiting',
            )
        )

        self.assertFalse(success)
        self.assertIn('duplicate', error)
        # Only the first execute was attempted; no commit.
        self.assertEqual(mock_conn.execute.call_count, 1)
        mock_conn.commit.assert_not_called()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_on_state_rolls_back(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        # First insert succeeds, second (state upsert) raises.
        mock_conn.execute.side_effect = [
            mock.Mock(),
            OperationalError('upsert', {}, Exception('DB down')),
        ]
        mock_get_engine.return_value = mock_engine

        from uuid import UUID
        success, error = (
            mariadb._direct_create_and_enqueue_cluster_operation(
                UUID(OP_UUID_STR),
                'node_net_op',
                _make_metadata(),
                1000.0,
                'node-clusteroperation-user_waiting',
            )
        )

        self.assertFalse(success)
        self.assertIn('DB down', error)
        # Only two executes attempted (third would be work_queue).
        self.assertEqual(mock_conn.execute.call_count, 2)
        mock_conn.commit.assert_not_called()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_on_work_queue_rolls_back(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = [
            mock.Mock(),
            mock.Mock(),
            OperationalError('insert', {}, Exception('DB down')),
        ]
        mock_get_engine.return_value = mock_engine

        from uuid import UUID
        success, error = (
            mariadb._direct_create_and_enqueue_cluster_operation(
                UUID(OP_UUID_STR),
                'node_net_op',
                _make_metadata(),
                1000.0,
                'node-clusteroperation-user_waiting',
            )
        )

        self.assertFalse(success)
        self.assertIn('DB down', error)
        self.assertEqual(mock_conn.execute.call_count, 3)
        mock_conn.commit.assert_not_called()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delay_sets_scheduled_at(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        from uuid import UUID
        mariadb._direct_create_and_enqueue_cluster_operation(
            UUID(OP_UUID_STR),
            'node_net_op',
            _make_metadata(),
            1000.0,
            'node-clusteroperation-user_waiting',
            delay=60.0,
        )

        # Third insert is the work_queue row.
        queue_stmt = mock_conn.execute.call_args_list[2][0][0]
        params = queue_stmt.compile().params
        self.assertEqual(params['scheduled_at'], 1060.0)
        self.assertEqual(params['created_at'], 1000.0)
        self.assertEqual(params['attempts'], 0)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_metadata_columns_are_extracted(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        from uuid import UUID
        mariadb._direct_create_and_enqueue_cluster_operation(
            UUID(OP_UUID_STR),
            'node_net_op',
            _make_metadata(),
            1000.0,
            'node-clusteroperation-user_waiting',
        )

        # First insert is the cluster_operations row.
        cluster_stmt = mock_conn.execute.call_args_list[0][0][0]
        params = cluster_stmt.compile().params
        self.assertEqual(
            str(params['uuid']), OP_UUID_STR)
        self.assertEqual(params['operation_type'], 'node_net_op')
        self.assertEqual(params['created_at'], 1000.0)
        self.assertEqual(str(params['node_uuid']), NODE_UUID_STR)
        self.assertEqual(
            str(params['instance_uuid']), INSTANCE_UUID_STR)
        self.assertEqual(
            str(params['network_uuid']), NETWORK_UUID_STR)
        self.assertEqual(params['priority'], 'user_waiting')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_missing_optional_uuids_become_null(
            self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        metadata = {
            'uuid': OP_UUID_STR,
            'node_uuid': NODE_UUID_STR,
            'priority': 'background',
            'tasks': ['x'],
        }

        from uuid import UUID
        mariadb._direct_create_and_enqueue_cluster_operation(
            UUID(OP_UUID_STR),
            'net_op',
            metadata,
            1000.0,
            'node-clusteroperation-background',
        )

        cluster_stmt = mock_conn.execute.call_args_list[0][0][0]
        params = cluster_stmt.compile().params
        self.assertIsNone(params['instance_uuid'])
        self.assertIsNone(params['network_uuid'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_work_queue_payload_shape(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        from uuid import UUID
        mariadb._direct_create_and_enqueue_cluster_operation(
            UUID(OP_UUID_STR),
            'node_net_op',
            _make_metadata(),
            1000.0,
            'node-clusteroperation-user_waiting',
        )

        queue_stmt = mock_conn.execute.call_args_list[2][0][0]
        params = queue_stmt.compile().params
        self.assertEqual(
            params['payload'],
            {
                'operation_type': 'node_net_op',
                'operation_uuid': OP_UUID_STR,
            })
        self.assertEqual(
            params['queue_name'],
            'node-clusteroperation-user_waiting')
        self.assertIsNone(params['claimed_at'])
        self.assertIsNone(params['claimed_by'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_state_row_shape(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_get_engine.return_value = mock_engine

        from uuid import UUID
        mariadb._direct_create_and_enqueue_cluster_operation(
            UUID(OP_UUID_STR),
            'node_net_op',
            _make_metadata(),
            1000.0,
            'node-clusteroperation-user_waiting',
        )

        # Second insert is the object_states upsert.
        state_stmt = mock_conn.execute.call_args_list[1][0][0]
        params = state_stmt.compile().params
        self.assertEqual(params['object_uuid'], OP_UUID_STR)
        self.assertEqual(params['object_type'], 'node_net_op')
        self.assertEqual(params['state_value'], 'queued')
        self.assertEqual(params['update_time'], 1000.0)
        self.assertIsNone(params['message'])


class CreateAndEnqueueDeadlockRetryTestCase(base.ShakenFistTestCase):
    """Issue 3631: deadlock retries on the enqueue transaction.

    This is the widest transaction in mariadb.py and so the most
    likely deadlock victim. Before this it had no retry at all, so an
    InnoDB 1213 permanently dropped a cluster operation -- observed on
    sfcbr as a network delete whose net_op never ran.
    """

    def setUp(self):
        super().setUp()
        self.config = mock.patch(
            'shakenfist.mariadb.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

        # Don't actually sleep through the backoff.
        mock.patch('shakenfist.mariadb.time.sleep').start()
        self.addCleanup(mock.patch.stopall)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_deadlock_is_retried_and_succeeds(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        # First attempt deadlocks on the object_states upsert -- the
        # exact statement seen failing in the issue -- then the whole
        # transaction is replayed and succeeds.
        mock_conn.execute.side_effect = [
            mock.Mock(),
            _make_deadlock_error(),
            mock.Mock(),
            mock.Mock(),
            mock.Mock(),
        ]
        mock_get_engine.return_value = mock_engine

        success, error = (
            mariadb._direct_create_and_enqueue_cluster_operation(
                UUID(OP_UUID_STR),
                'net_op',
                _make_metadata(),
                1000.0,
                'networknode-clusteroperation-user_waiting',
            )
        )

        self.assertTrue(success)
        self.assertEqual('', error)
        # Two executes for the failed attempt, three for the retry.
        self.assertEqual(5, mock_conn.execute.call_count)
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_sustained_deadlock_reports_failure(self, mock_get_engine):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = _make_deadlock_error()
        mock_get_engine.return_value = mock_engine

        success, error = (
            mariadb._direct_create_and_enqueue_cluster_operation(
                UUID(OP_UUID_STR),
                'net_op',
                _make_metadata(),
                1000.0,
                'networknode-clusteroperation-user_waiting',
            )
        )

        self.assertFalse(success)
        self.assertIn('Deadlock found', error)
        self.assertEqual(
            mariadb._DEADLOCK_MAX_ATTEMPTS,
            mock_conn.execute.call_count)
        mock_conn.commit.assert_not_called()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_duplicate_uuid_is_not_retried(self, mock_get_engine):
        # An IntegrityError is a permanent failure, not a transient
        # one -- replaying it just inserts the same duplicate again.
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = IntegrityError(
            'insert', {}, Exception('duplicate'))
        mock_get_engine.return_value = mock_engine

        success, error = (
            mariadb._direct_create_and_enqueue_cluster_operation(
                UUID(OP_UUID_STR),
                'net_op',
                _make_metadata(),
                1000.0,
                'networknode-clusteroperation-user_waiting',
            )
        )

        self.assertFalse(success)
        self.assertIn('duplicate', error)
        self.assertEqual(1, mock_conn.execute.call_count)


class GrpcCreateAndEnqueueClusterOperationTestCase(base.ShakenFistTestCase):
    """Tests for _grpc_create_and_enqueue_cluster_operation().

    Issue 3524: the client used to return only bool(reply.success),
    discarding reply.error entirely, so a failed enqueue reported by
    the database service surfaced as an undiagnosable bare 'Failed to
    enqueue cluster operation' at the schema layer. These tests pin
    the (success, error) propagation contract.
    """

    def _call(self):
        from uuid import UUID
        return mariadb._grpc_create_and_enqueue_cluster_operation(
            UUID(OP_UUID_STR),
            'node_net_op',
            _make_metadata(),
            1000.0,
            'node-clusteroperation-user_waiting',
        )

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_success_returns_empty_error(
            self, mock_stub, mock_call):
        mock_call.return_value = mock.Mock(success=True, error='')

        success, error = self._call()

        self.assertTrue(success)
        self.assertEqual('', error)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_failure_reply_propagates_service_error(
            self, mock_stub, mock_call):
        mock_call.return_value = mock.Mock(
            success=False,
            error='duplicate cluster_operation uuid: boom')

        success, error = self._call()

        self.assertFalse(success)
        self.assertEqual(
            'duplicate cluster_operation uuid: boom', error)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_rpc_error_propagates_exception_detail(
            self, mock_stub, mock_call):
        import grpc
        mock_call.side_effect = grpc.RpcError('service unavailable')

        success, error = self._call()

        self.assertFalse(success)
        self.assertIn('service unavailable', error)
