from unittest import mock

from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist import exceptions
from shakenfist.config import BaseSettings
from shakenfist.tests import base


LOG, _ = logs.setup(__name__)


class FakeConfig(BaseSettings):
    NODE_NAME: str = 'thisnode'
    SLOW_LOCK_THRESHOLD: int = 2
    DATABASE_USE_DIRECT_ETCD: bool = True


fake_config = FakeConfig()


class ClusterLockTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.config = mock.patch('shakenfist.etcd.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist_utilities.random.random_id', return_value='fakeid')
    @mock.patch('shakenfist.etcd.ClusterLock.get_holder',
                return_value={
                    'node': 'foo',
                    'pid': 43,
                    'line': 'banana.py:43',
                    'operation': 'bar',
                    'id': 'fakeid'
                })
    @mock.patch('shakenfist.util.callstack.get_caller',
                return_value='banana.py:43')
    @mock.patch('shakenfist.etcd.transactional_delete_raw')
    @mock.patch('shakenfist.etcd.create_raw', return_value=True)
    @mock.patch('os.getpid', return_value=42)
    @mock.patch('threading.get_ident', return_value=1234567890)
    def test_context_manager(
            self, mock_thread_ident, mock_pid, mock_create_raw,
            mock_delete_raw, mock_get_caller, mock_get_holder, mock_fake_id):
        al = etcd.ClusterLock('instance', None, 'auuid', op='Test case')

        self.assertEqual('/sflocks/instance/auuid', al.path)
        self.assertEqual('instance', al.objecttype)
        self.assertEqual('auuid', al.objectname)
        self.assertEqual(120, al.timeout)
        self.assertEqual('Test case', al.operation)
        self.assertIsNotNone(al.lockid)

        attempt = mock.call(
            '/sflocks/instance/auuid',
            {
                'node': 'thisnode',
                'pid': 42,
                'thread': 1234567890,
                'line': mock.ANY,
                'operation': 'Test case',
                'id': 'fakeid'
            }
        )
        with al:
            mock_create_raw.assert_has_calls([attempt])

        mock_delete_raw.assert_has_calls([attempt])

    @mock.patch('shakenfist_utilities.random.random_id', return_value='fakeid')
    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('time.time',
                side_effect=[100.0, 101.0, 102.0, 103.0, 104.0, 105.0,
                             106.0, 107.0, 108.0, 109.0, 110.0, 111.0,
                             112.0, 113.0, 114.0, 115.0, 116.0, 117.0])
    @mock.patch('time.sleep')
    @mock.patch('shakenfist.etcd.transactional_delete_raw')
    @mock.patch('shakenfist.etcd.create_raw',
                side_effect=[False, False, False, False, False, True])
    @mock.patch('os.getpid', return_value=42)
    @mock.patch('threading.get_ident', return_value=1234567890)
    def test_context_manager_slow(
            self, mock_thread_ident, mock_pid, mock_create_raw,
            mock_delete_raw, mock_sleep, mock_time, mock_add_event,
            mock_fake_id):
        al = etcd.ClusterLock('instance', None, 'auuid',
                              op='Test case', timeout=12)
        al.log_ctx = mock.MagicMock()

        attempt = mock.call(
            '/sflocks/instance/auuid',
            {
                'node': 'thisnode',
                'pid': 42,
                'thread': 1234567890,
                'line': mock.ANY,
                'operation': 'Test case',
                'id': 'fakeid'
            }
        )

        with al:
            mock_create_raw.assert_has_calls(
                [attempt, attempt, attempt, attempt, attempt, attempt])
            mock_sleep.assert_has_calls(
                [
                    mock.call(0.5), mock.call(0.5), mock.call(0.5),
                    mock.call(0.5), mock.call(0.5)
                ]
            )

        mock_delete_raw.assert_has_calls([attempt])

    @mock.patch('shakenfist_utilities.random.random_id', return_value='fakeid')
    @mock.patch('shakenfist.etcd.ClusterLock.get_holder',
                return_value={
                    'node': 'foo',
                    'pid': 43,
                    'thread': 1234567890,
                    'line': 'banana.py:43',
                    'operation': 'bar',
                    'id': 'fakeid'
                })
    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('time.time',
                side_effect=[100.0, 101.0, 102.0, 103.0, 104.0, 105.0,
                             106.0, 107.0, 108.0, 109.0, 110.0, 111.0,
                             112.0, 113.0, 114.0, 115.0, 116.0, 117.0])
    @mock.patch('time.sleep')
    @mock.patch('shakenfist.etcd.transactional_delete_raw')
    @mock.patch('shakenfist.etcd.create_raw',
                side_effect=[False, False, False, False, False, True])
    @mock.patch('os.getpid', return_value=42)
    @mock.patch('threading.get_ident', return_value=1234567890)
    def test_context_manager_timeout(
            self, mock_thread_ident, mock_pid, mock_create_raw,
            mock_delete_raw, mock_sleep, mock_time, mock_add_event,
            mock_get_holder, mock_fake_id):
        al = etcd.ClusterLock('instance', None, 'auuid', op='Test case',
                              timeout=2)
        al.log_ctx = mock.MagicMock()

        attempt = mock.call(
            '/sflocks/instance/auuid',
            {
                'node': 'thisnode',
                'pid': 42,
                'thread': 1234567890,
                'line': mock.ANY,
                'operation': 'Test case',
                'id': 'fakeid'
            }
        )

        self.assertRaises(exceptions.LockException, al.__enter__)

        mock_create_raw.assert_has_calls([attempt])
        mock_sleep.assert_has_calls([mock.call(0.5)])

        mock_get_holder.assert_called_with(key_prefix='current')
        mock_delete_raw.assert_not_called()
