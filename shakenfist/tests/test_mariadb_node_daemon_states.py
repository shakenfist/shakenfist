# Copyright 2019 Michael Still and contributors
#
# Tests for the mariadb node daemon state functions.

import uuid
from unittest import mock

import grpc
from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.daemons.database import main as daemons_database_main
from shakenfist.protos import database_pb2
from shakenfist.schema.node_daemon_state import NodeDaemonStateData
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    MARIADB_GATEWAY_HOSTS: list[str] = ['192.168.1.1']
    MARIADB_GATEWAY_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()
NODE_UUID = uuid.uuid4()


class SetNodeDaemonStateTestCase(base.ShakenFistTestCase):
    """``_direct_set_node_daemon_state`` issues an atomic upsert."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_upsert_succeeds(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_set_node_daemon_state(
            NODE_UUID, 'net', 'daemon-running', 1234567890.0, None)

        self.assertTrue(result)
        # Single execute (the upsert) and a commit.
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_false_on_operational_error(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'statement', {}, Exception('connection refused'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_set_node_daemon_state(
            NODE_UUID, 'net', 'daemon-running', 1234567890.0, None)

        self.assertFalse(result)


class GetNodeDaemonStateTestCase(base.ShakenFistTestCase):
    """``_direct_get_node_daemon_state`` returns one row or None."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_data_when_row_present(self, mock_get_engine):
        mock_row = mock.MagicMock()
        mock_row.node_uuid = NODE_UUID
        mock_row.daemon = 'net'
        mock_row.value = 'daemon-running'
        mock_row.update_time = 1234567890.0
        mock_row.message = None

        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_node_daemon_state(NODE_UUID, 'net')

        self.assertIsInstance(result, NodeDaemonStateData)
        self.assertEqual(result.daemon, 'net')
        self.assertEqual(result.value, 'daemon-running')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_none_when_row_absent(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_node_daemon_state(NODE_UUID, 'net')

        self.assertIsNone(result)

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._get_bounded_engine')
    def test_bounded_read_uses_bounded_engine(
            self, mock_get_bounded_engine, mock_get_engine):
        """bounded=True must route the read via the timeout-bearing engine.

        The bounded path exists so Daemon.check_daemon_state()'s poll --
        which runs upstream of the systemd watchdog pet -- cannot block
        past WatchdogSec on a stalled server (issue 3586).
        """
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_bounded_engine.return_value = mock_engine

        result = mariadb._direct_get_node_daemon_state(
            NODE_UUID, 'net', bounded=True)

        self.assertIsNone(result)
        mock_get_bounded_engine.assert_called_once()
        mock_get_engine.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_get_node_daemon_state',
                return_value=None)
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_public_accessor_plumbs_bounded_direct(
            self, mock_use_service, mock_direct):
        mariadb.get_node_daemon_state(NODE_UUID, 'net', bounded=True)
        mock_direct.assert_called_once_with(NODE_UUID, 'net', bounded=True)

    @mock.patch('shakenfist.mariadb._grpc_get_node_daemon_state',
                return_value=None)
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_public_accessor_plumbs_bounded_grpc(
            self, mock_use_service, mock_grpc):
        mariadb.get_node_daemon_state(NODE_UUID, 'net', bounded=True)
        mock_grpc.assert_called_once_with(NODE_UUID, 'net', bounded=True)

    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_grpc_bounded_read_uses_short_deadline_single_slow_attempt(
            self, mock_get_stub, mock_grpc_call):
        """The bounded gRPC read caps its worst-case wall time.

        The default retry budget (GRPC_RETRIES full deadlines plus
        sleeps) exceeds WatchdogSec, which is how a stalled database
        tier SIGABRTed non-database daemons via their
        check_daemon_state() poll (issue 3586).
        """
        mock_grpc_call.return_value = mock.Mock(found=False)

        result = mariadb._grpc_get_node_daemon_state(
            NODE_UUID, 'net', bounded=True)

        self.assertIsNone(result)
        _, kwargs = mock_grpc_call.call_args
        self.assertEqual(kwargs['timeout'], mariadb.BOUNDED_QUERY_TIMEOUT)
        self.assertEqual(kwargs['max_slow_failures'], 1)


class GetNodeDaemonStateServicerTestCase(base.ShakenFistTestCase):
    """The servicer must not run reads for callers which have given up.

    Every daemon polls its own row every couple of seconds with a
    BOUNDED_QUERY_TIMEOUT deadline, so a slow MariaDB queues up several
    expired copies of the same poll per caller. Running each of those to
    completion is what kept sf-database's worker pool full and made the
    failures self-sustaining (issue 3607).
    """

    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.request = database_pb2.GetNodeDaemonStateRequest(
            node_uuid=str(NODE_UUID), daemon='net')

    def _context(self, time_remaining):
        context = mock.MagicMock()
        context.time_remaining.return_value = time_remaining
        return context

    @mock.patch('shakenfist.mariadb._direct_get_node_daemon_state')
    def test_expired_call_is_dropped_without_a_read(self, mock_direct):
        context = self._context(0.0)

        reply = self.servicer.GetNodeDaemonState(self.request, context)

        mock_direct.assert_not_called()
        self.assertFalse(reply.found)
        # A found=False with no status would read as "no such row", which
        # is a different (and wrong) answer to "I did not look".
        context.set_code.assert_called_once_with(
            grpc.StatusCode.DEADLINE_EXCEEDED)

    @mock.patch('shakenfist.mariadb._direct_get_node_daemon_state',
                return_value=NodeDaemonStateData(
                    node_uuid=NODE_UUID, daemon='net',
                    value='daemon-running', update_time=1.0, message=None))
    def test_live_call_is_served(self, mock_direct):
        context = self._context(9.5)

        reply = self.servicer.GetNodeDaemonState(self.request, context)

        mock_direct.assert_called_once()
        self.assertTrue(reply.found)
        self.assertEqual('daemon-running', reply.data.value)
        context.set_code.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_get_node_daemon_state',
                return_value=None)
    def test_call_without_a_deadline_is_served(self, mock_direct):
        # time_remaining() is None when the caller set no deadline at all.
        context = self._context(None)

        reply = self.servicer.GetNodeDaemonState(self.request, context)

        mock_direct.assert_called_once()
        self.assertFalse(reply.found)
        context.set_code.assert_not_called()


class GetAllNodeDaemonStatesTestCase(base.ShakenFistTestCase):
    """``_direct_get_all_node_daemon_states`` returns rows for one node."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_list(self, mock_get_engine):
        rows = []
        for d, v in (('net', 'daemon-running'), ('queues', 'daemon-stopped')):
            r = mock.MagicMock()
            r.node_uuid = NODE_UUID
            r.daemon = d
            r.value = v
            r.update_time = 1.0
            r.message = None
            rows.append(r)

        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = rows
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_get_all_node_daemon_states(NODE_UUID)

        self.assertEqual(len(result), 2)
        self.assertEqual({r.daemon for r in result}, {'net', 'queues'})


class DeleteNodeDaemonStateTestCase(base.ShakenFistTestCase):
    """``_direct_delete_node_daemon_state`` removes one row."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_true(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_node_daemon_state(NODE_UUID, 'net')

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()
