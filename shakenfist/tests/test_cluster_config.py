# Copyright 2026 Michael Still and contributors
#
# Tests for the cluster_config _direct_* helpers in mariadb.py.

import json
from unittest import mock

from shakenfist import mariadb
from shakenfist.daemons.database import main as daemons_database_main
from shakenfist.protos import database_pb2
from shakenfist.tests import base


class _MockResult:
    """Minimal mock for a SQLAlchemy CursorResult."""

    def __init__(self, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _MockConnection:
    """Minimal mock for a SQLAlchemy Connection."""

    def __init__(self, result=None):
        self.result = result or _MockResult()
        self.executed = []
        self.committed = False

    def execute(self, stmt):
        self.executed.append(stmt)
        return self.result

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _MockEngine:
    """Minimal mock for a SQLAlchemy Engine."""

    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        return self._connection


class DirectGetAllClusterConfigTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_all_cluster_config."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_all_config(self, mock_engine):
        conn = _MockConnection(
            result=_MockResult(rows=[
                ('AUTH_SECRET_SEED', 'secret123'),
                ('DNS_SERVER', '8.8.8.8'),
                ('RAM_OVERCOMMIT_RATIO', 5),
            ]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_config()
        self.assertEqual(result, {
            'AUTH_SECRET_SEED': 'secret123',
            'DNS_SERVER': '8.8.8.8',
            'RAM_OVERCOMMIT_RATIO': 5,
        })

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_empty_when_no_config(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_config()
        self.assertEqual(result, {})

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_empty_on_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_all_cluster_config()
        self.assertEqual(result, {})


class DirectSetClusterConfigTestCase(base.ShakenFistTestCase):
    """Tests for _direct_set_cluster_config."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_upserts_key(self, mock_engine):
        conn = _MockConnection()
        mock_engine.return_value = _MockEngine(conn)

        mariadb._direct_set_cluster_config('DNS_SERVER', '8.8.8.8')
        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.executed), 1)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_handles_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        # Should not raise
        mariadb._direct_set_cluster_config('key', 'val')


class PublicClusterConfigRoutingTestCase(base.ShakenFistTestCase):
    """Tests for the public get_cluster_config / set_cluster_config wrappers.

    These exercise only the routing decision between direct MariaDB access
    and the database microservice gRPC path.
    """

    @mock.patch('shakenfist.mariadb._use_database_service')
    @mock.patch('shakenfist.mariadb._direct_get_all_cluster_config')
    @mock.patch('shakenfist.mariadb._grpc_get_all_cluster_config')
    def test_get_routes_to_direct_when_mariadb_host_set(
            self, mock_grpc, mock_direct, mock_use_service):
        mock_use_service.return_value = False
        mock_direct.return_value = {'DNS_SERVER': '8.8.8.8'}

        result = mariadb.get_cluster_config()

        self.assertEqual(result, {'DNS_SERVER': '8.8.8.8'})
        mock_direct.assert_called_once_with()
        mock_grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._use_database_service')
    @mock.patch('shakenfist.mariadb._direct_get_all_cluster_config')
    @mock.patch('shakenfist.mariadb._grpc_get_all_cluster_config')
    def test_get_routes_to_grpc_otherwise(
            self, mock_grpc, mock_direct, mock_use_service):
        mock_use_service.return_value = True
        mock_grpc.return_value = {'DNS_SERVER': '1.1.1.1'}

        result = mariadb.get_cluster_config()

        self.assertEqual(result, {'DNS_SERVER': '1.1.1.1'})
        mock_grpc.assert_called_once_with()
        mock_direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._use_database_service')
    @mock.patch('shakenfist.mariadb._direct_set_cluster_config')
    @mock.patch('shakenfist.mariadb._grpc_set_cluster_config')
    def test_set_routes_to_direct_when_mariadb_host_set(
            self, mock_grpc, mock_direct, mock_use_service):
        mock_use_service.return_value = False

        mariadb.set_cluster_config('DNS_SERVER', '8.8.8.8')

        mock_direct.assert_called_once_with('DNS_SERVER', '8.8.8.8')
        mock_grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._use_database_service')
    @mock.patch('shakenfist.mariadb._direct_set_cluster_config')
    @mock.patch('shakenfist.mariadb._grpc_set_cluster_config')
    def test_set_routes_to_grpc_otherwise(
            self, mock_grpc, mock_direct, mock_use_service):
        mock_use_service.return_value = True

        mariadb.set_cluster_config('DNS_SERVER', '8.8.8.8')

        mock_grpc.assert_called_once_with('DNS_SERVER', '8.8.8.8')
        mock_direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._use_database_service')
    @mock.patch('shakenfist.mariadb._direct_delete_cluster_config')
    @mock.patch('shakenfist.mariadb._grpc_delete_cluster_config')
    def test_delete_routes_to_direct_when_mariadb_host_set(
            self, mock_grpc, mock_direct, mock_use_service):
        mock_use_service.return_value = False

        mariadb.delete_cluster_config('DNS_SERVER')

        mock_direct.assert_called_once_with('DNS_SERVER')
        mock_grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._use_database_service')
    @mock.patch('shakenfist.mariadb._direct_delete_cluster_config')
    @mock.patch('shakenfist.mariadb._grpc_delete_cluster_config')
    def test_delete_routes_to_grpc_otherwise(
            self, mock_grpc, mock_direct, mock_use_service):
        mock_use_service.return_value = True

        mariadb.delete_cluster_config('DNS_SERVER')

        mock_grpc.assert_called_once_with('DNS_SERVER')
        mock_direct.assert_not_called()


class DirectDeleteClusterConfigTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_cluster_config."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_returns_true_on_success(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rowcount=1))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_delete_cluster_config('DNS_SERVER')
        self.assertTrue(result)
        self.assertTrue(conn.committed)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_returns_false_on_missing(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rowcount=0))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_delete_cluster_config('MISSING')
        self.assertFalse(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_returns_false_on_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_delete_cluster_config('key')
        self.assertFalse(result)


class DirectGetClusterConfigValueTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_cluster_config_value (issue 4096)."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_value_when_present(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rows=[('8.8.8.8',)]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_cluster_config_value('DNS_SERVER')
        self.assertEqual('8.8.8.8', result)
        # The filter is pushed down to SQL rather than applied to a
        # full-table read in Python.
        self.assertIn('WHERE', str(conn.executed[0]))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_none_when_absent(self, mock_engine):
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_cluster_config_value('MISSING')
        self.assertIsNone(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_none_on_error(self, mock_engine):
        from sqlalchemy.exc import OperationalError
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception()))
        mock_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_get_cluster_config_value('key')
        self.assertIsNone(result)


class GrpcGetClusterConfigValueTestCase(base.ShakenFistTestCase):
    """Tests for _grpc_get_cluster_config_value (issue 4096)."""

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_sends_keyed_request_and_decodes_value(
            self, mock_stub, mock_call):
        mock_call.return_value = mock.Mock(entries=[
            mock.Mock(key_name='DNS_SERVER',
                      value_json=json.dumps('8.8.8.8'))])

        result = mariadb._grpc_get_cluster_config_value('DNS_SERVER')

        self.assertEqual('8.8.8.8', result)
        request = mock_call.call_args.args[1]
        self.assertEqual('DNS_SERVER', request.key_name)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_filters_a_full_table_reply_from_an_old_server(
            self, mock_stub, mock_call):
        # During a rolling upgrade an older sf-database ignores
        # key_name and replies with every row; take only ours.
        mock_call.return_value = mock.Mock(entries=[
            mock.Mock(key_name='AUTH_SECRET_SEED',
                      value_json=json.dumps('secret123')),
            mock.Mock(key_name='DNS_SERVER',
                      value_json=json.dumps('8.8.8.8'))])

        result = mariadb._grpc_get_cluster_config_value('DNS_SERVER')
        self.assertEqual('8.8.8.8', result)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_returns_none_when_absent(self, mock_stub, mock_call):
        mock_call.return_value = mock.Mock(entries=[])

        result = mariadb._grpc_get_cluster_config_value('MISSING')
        self.assertIsNone(result)


class PublicGetClusterConfigValueRoutingTestCase(base.ShakenFistTestCase):
    """Routing tests for the public get_cluster_config_value wrapper."""

    @mock.patch('shakenfist.mariadb._use_database_service')
    @mock.patch('shakenfist.mariadb._direct_get_cluster_config_value')
    @mock.patch('shakenfist.mariadb._grpc_get_cluster_config_value')
    def test_get_value_routes_to_direct_when_mariadb_host_set(
            self, mock_grpc, mock_direct, mock_use_service):
        mock_use_service.return_value = False
        mock_direct.return_value = '8.8.8.8'

        result = mariadb.get_cluster_config_value('DNS_SERVER')

        self.assertEqual('8.8.8.8', result)
        mock_direct.assert_called_once_with('DNS_SERVER')
        mock_grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._use_database_service')
    @mock.patch('shakenfist.mariadb._direct_get_cluster_config_value')
    @mock.patch('shakenfist.mariadb._grpc_get_cluster_config_value')
    def test_get_value_routes_to_grpc_otherwise(
            self, mock_grpc, mock_direct, mock_use_service):
        mock_use_service.return_value = True
        mock_grpc.return_value = '1.1.1.1'

        result = mariadb.get_cluster_config_value('DNS_SERVER')

        self.assertEqual('1.1.1.1', result)
        mock_grpc.assert_called_once_with('DNS_SERVER')
        mock_direct.assert_not_called()


class ServicerGetClusterConfigKeyedTestCase(base.ShakenFistTestCase):
    """The GetClusterConfig servicer's keyed branch (issue 4096)."""

    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())

    @mock.patch('shakenfist.mariadb._direct_get_all_cluster_config')
    @mock.patch('shakenfist.mariadb._direct_get_cluster_config_value')
    def test_keyed_request_reads_one_row(self, mock_value, mock_all):
        mock_value.return_value = '8.8.8.8'
        request = database_pb2.ClusterConfigRequest(
            key_name='DNS_SERVER')

        reply = self.servicer.GetClusterConfig(request, mock.MagicMock())

        self.assertEqual(1, len(reply.entries))
        self.assertEqual('DNS_SERVER', reply.entries[0].key_name)
        self.assertEqual('8.8.8.8', json.loads(reply.entries[0].value_json))
        mock_value.assert_called_once_with('DNS_SERVER')
        # The whole point: no full-table read for a keyed request.
        mock_all.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_get_cluster_config_value',
                return_value=None)
    def test_keyed_request_for_absent_key_is_empty(self, mock_value):
        request = database_pb2.ClusterConfigRequest(
            key_name='MISSING')

        reply = self.servicer.GetClusterConfig(request, mock.MagicMock())

        self.assertEqual(0, len(reply.entries))

    @mock.patch('shakenfist.mariadb._direct_get_all_cluster_config')
    def test_unkeyed_request_still_returns_everything(self, mock_all):
        mock_all.return_value = {'DNS_SERVER': '8.8.8.8', 'OTHER': 1}
        request = database_pb2.ClusterConfigRequest()

        reply = self.servicer.GetClusterConfig(request, mock.MagicMock())

        self.assertEqual(
            {'DNS_SERVER', 'OTHER'},
            {entry.key_name for entry in reply.entries})
