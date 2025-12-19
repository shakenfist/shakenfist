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
    DATABASE_USE_DIRECT_ETCD: bool = False
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
    def test_get_found(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.Get.return_value = database_pb2.GetReply(
            found=True,
            value='{"key": "value"}'
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.get('instance', 'subtype', 'name')

        self.assertEqual({'key': 'value'}, result)
        mock_stub.Get.assert_called_once()

    @mock.patch('shakenfist.database.get_database_client')
    def test_get_not_found(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.Get.return_value = database_pb2.GetReply(
            found=False,
            value=''
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.get('instance', 'subtype', 'name')

        self.assertIsNone(result)

    @mock.patch('shakenfist.database.get_database_client')
    def test_put(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.Put.return_value = database_pb2.StatusReply(
            success=True,
            error=''
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            database.put('instance', 'subtype', 'name', {'key': 'value'})

        mock_stub.Put.assert_called_once()

    @mock.patch('shakenfist.database.get_database_client')
    def test_create_success(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.Create.return_value = database_pb2.StatusReply(
            success=True,
            error=''
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.create('instance', 'subtype', 'name',
                                     {'key': 'value'})

        self.assertTrue(result)

    @mock.patch('shakenfist.database.get_database_client')
    def test_create_failure(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.Create.return_value = database_pb2.StatusReply(
            success=False,
            error='Key already exists'
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.create('instance', 'subtype', 'name',
                                     {'key': 'value'})

        self.assertFalse(result)

    @mock.patch('shakenfist.database.get_database_client')
    def test_delete(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.Delete.return_value = database_pb2.StatusReply(
            success=True,
            error=''
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            database.delete('instance', 'subtype', 'name')

        mock_stub.Delete.assert_called_once()

    @mock.patch('shakenfist.database.get_database_client')
    def test_enqueue(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.Enqueue.return_value = database_pb2.StatusReply(
            success=True,
            error=''
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            database.enqueue('my-queue', {'work': 'item'}, delay=5)

        mock_stub.Enqueue.assert_called_once()

    @mock.patch('shakenfist.database.get_database_client')
    def test_dequeue_found(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.Dequeue.return_value = database_pb2.DequeueReply(
            found=True,
            job_name='12345-abc',
            work_item='{"work": "item"}'
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.dequeue('my-queue')

        self.assertEqual(('12345-abc', {'work': 'item'}), result)

    @mock.patch('shakenfist.database.get_database_client')
    def test_dequeue_not_found(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.Dequeue.return_value = database_pb2.DequeueReply(
            found=False,
            job_name='',
            work_item=''
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.dequeue('my-queue')

        self.assertIsNone(result)

    @mock.patch('shakenfist.database.get_database_client')
    def test_get_queue_length(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.GetQueueLength.return_value = database_pb2.QueueLengthReply(
            processing=5,
            queued=10,
            deferred=3
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            result = database.get_queue_length('my-queue')

        self.assertEqual((5, 10, 3), result)

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

    @mock.patch('shakenfist.database.get_database_client')
    def test_replace_many_raw_success(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        mock_stub = mock.MagicMock()
        mock_stub.ReplaceMany.return_value = database_pb2.ReplaceManyReply(
            success=True,
            failures=[]
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            mutations = [
                {
                    'path': '/sf/instance/uuid1',
                    'original_data': {'state': 'created'},
                    'new_data': {'state': 'running'}
                }
            ]
            success, failures = database.replace_many_raw(mutations)

        self.assertTrue(success)
        self.assertEqual([], failures)

    @mock.patch('shakenfist.database.get_database_client')
    def test_replace_many_raw_failure(self, mock_get_client):
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel

        failure = database_pb2.MutationFailure(
            path='/sf/instance/uuid1',
            desired='{"state": "created"}',
            actual='{"state": "error"}',
            replacement='{"state": "running"}'
        )
        mock_stub = mock.MagicMock()
        mock_stub.ReplaceMany.return_value = database_pb2.ReplaceManyReply(
            success=False,
            failures=[failure]
        )

        with mock.patch(
            'shakenfist.database.database_pb2_grpc.DatabaseServiceStub',
            return_value=mock_stub
        ):
            mutations = [
                {
                    'path': '/sf/instance/uuid1',
                    'original_data': {'state': 'created'},
                    'new_data': {'state': 'running'}
                }
            ]
            success, failures = database.replace_many_raw(mutations)

        self.assertFalse(success)
        self.assertEqual(1, len(failures))
        self.assertEqual('/sf/instance/uuid1', failures[0]['path'])


class IsAvailableTestCase(base.ShakenFistTestCase):
    def test_is_available_when_configured(self):
        with mock.patch('shakenfist.database.config', fake_config):
            result = database.is_available()
        self.assertTrue(result)

    def test_is_available_when_direct_etcd(self):
        config_direct = FakeConfig()
        config_direct.DATABASE_USE_DIRECT_ETCD = True
        with mock.patch('shakenfist.database.config', config_direct):
            result = database.is_available()
        self.assertFalse(result)

    def test_is_available_when_no_ip(self):
        config_no_ip = FakeConfig()
        config_no_ip.DATABASE_NODE_IP = ''
        with mock.patch('shakenfist.database.config', config_no_ip):
            result = database.is_available()
        self.assertFalse(result)
