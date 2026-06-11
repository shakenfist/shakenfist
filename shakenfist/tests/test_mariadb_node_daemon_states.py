# Copyright 2019 Michael Still and contributors
#
# Tests for the mariadb node daemon state functions.

import uuid
from unittest import mock

from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.config import BaseSettings
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
