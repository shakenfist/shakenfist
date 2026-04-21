# Copyright 2025 Michael Still and contributors

"""Tests for the database client library."""

from unittest import mock

from shakenfist_utilities import logs  # noreorder

from shakenfist import database
from shakenfist.config import BaseSettings
from shakenfist.protos import database_pb2
from shakenfist.tests import base


LOG, _ = logs.setup(__name__)


class FakeConfig(BaseSettings):
    DATABASE_NODE_IP: str = '192.168.1.1'
    DATABASE_API_PORT: int = 13005
    DATABASE_METRICS_PORT: int = 13006
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()


class DatabaseClientTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.config = mock.patch('shakenfist.database.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # Reset thread-local client before each test
        database.reset_client()

    @mock.patch('shakenfist.database.get_database_client')
    def test_acquire_lock_success(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.AcquireLock.return_value = database_pb2.ClusterLockReply(
            acquired=True
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.acquire_lock(
                'instance', 'subtype', 'name',
                {'node': 'testnode', 'pid': 123}
            )

        self.assertTrue(result)

    @mock.patch('shakenfist.database.get_database_client')
    def test_acquire_lock_failure(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.AcquireLock.return_value = database_pb2.ClusterLockReply(
            acquired=False
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.acquire_lock(
                'instance', 'subtype', 'name',
                {'node': 'testnode', 'pid': 123}
            )

        self.assertFalse(result)

    @mock.patch('shakenfist.database.get_database_client')
    def test_release_lock(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.ReleaseLock.return_value = database_pb2.StatusReply(
            success=True,
            error=''
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.release_lock(
                'instance', 'subtype', 'name',
                {'node': 'testnode', 'pid': 123}
            )

        self.assertTrue(result)

    @mock.patch('shakenfist.database.get_database_client')
    def test_get_existing_locks(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        lock1 = database_pb2.ClusterLockEntry(
            key='/sflocks/instance/uuid1',
            holder='{"node": "node1", "pid": 123}'
        )
        lock2 = database_pb2.ClusterLockEntry(
            key='/sflocks/instance/uuid2',
            holder='{"node": "node2", "pid": 456}'
        )
        mock_stub.GetExistingLocks.return_value = \
            database_pb2.ClusterGetExistingLocksReply(locks=[lock1, lock2])

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.get_existing_locks()

        self.assertEqual(2, len(result))
        self.assertIn('/sflocks/instance/uuid1', result)
        self.assertIn('/sflocks/instance/uuid2', result)


class IsAvailableTestCase(base.ShakenFistTestCase):
    def test_is_available_when_configured(self):
        with mock.patch('shakenfist.database.config', fake_config):
            result = database.is_available()
        self.assertTrue(result)

    def test_is_available_when_no_ip(self):
        config_no_ip = FakeConfig()
        config_no_ip.DATABASE_NODE_IP = ''
        with mock.patch('shakenfist.database.config', config_no_ip):
            result = database.is_available()
        self.assertFalse(result)
