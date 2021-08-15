import json
import mock

from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import tasks
from shakenfist.config import BaseSettings
from shakenfist.tests import test_shakenfist

LOG, _ = logutil.setup(__name__)


class FakeConfig(BaseSettings):
    NODE_NAME: str = 'thisnode'
    SLOW_LOCK_THRESHOLD: int = 2


fake_config = FakeConfig()


class ActualLockTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super(ActualLockTestCase, self).setUp()

        self.config = mock.patch('shakenfist.etcd.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('etcd3gw.lock.Lock.release')
    @mock.patch('etcd3gw.lock.Lock.acquire', return_value=True)
    @mock.patch('os.getpid', return_value=42)
    def test_context_manager(self, mock_pid, mock_acquire, mock_release):
        al = etcd.ActualLock('instance', None, 'auuid', op='Test case')

        self.assertEqual('/sflocks/sf/instance/auuid', al.key)
        self.assertEqual('instance', al.objecttype)
        self.assertEqual('auuid', al.objectname)
        self.assertEqual(1000000000, al.timeout)
        self.assertEqual('Test case', al.operation)
        self.assertEqual(
            json.dumps({
                'node': 'thisnode',
                'pid': 42,
                'operation': 'Test case'
            }, indent=4, sort_keys=True), al._uuid)

        with al:
            mock_acquire.assert_called_with()

        mock_release.assert_called_with()

    @mock.patch('shakenfist.etcd.ActualLock.get_holder',
                return_value=('foo', '43'))
    @mock.patch('shakenfist.db.add_event')
    @mock.patch('time.time',
                side_effect=[100.0, 101.0, 102.0, 103.0, 104.0, 105.0,
                             106.0, 107.0, 108.0, 109.0, 110.0, 111.0,
                             112.0, 113.0, 114.0, 115.0, 116.0, 117.0])
    @mock.patch('time.sleep')
    @mock.patch('etcd3gw.lock.Lock.release')
    @mock.patch('etcd3gw.lock.Lock.acquire',
                side_effect=[False, False, False, False, False, True])
    @mock.patch('os.getpid', return_value=42)
    def test_context_manager_slow(
            self, mock_pid, mock_acquire, mock_release, mock_sleep,
            mock_time, mock_add_event, mock_get_holder):
        al = etcd.ActualLock('instance', None, 'auuid', op='Test case')
        al.log_ctx = mock.MagicMock()

        with al:
            mock_acquire.assert_has_calls(
                [mock.call(), mock.call(), mock.call(),
                 mock.call(), mock.call(), mock.call()])
            mock_sleep.assert_has_calls(
                [mock.call(1), mock.call(1), mock.call(1), mock.call(1)])

            mock_add_event.assert_has_calls(
                [mock.call('instance', 'auuid', 'lock', 'acquire',
                           None, 'Waiting for lock more than threshold'),
                 mock.call('instance', 'auuid', 'lock', 'acquired',
                           None, 'Waited 12 seconds for lock')])
            mock_get_holder.assert_called_with()

        mock_release.assert_called_with()

    @mock.patch('shakenfist.etcd.ActualLock.get_holder',
                return_value=('foo', '43'))
    @mock.patch('shakenfist.db.add_event')
    @mock.patch('time.time',
                side_effect=[100.0, 101.0, 102.0, 103.0, 104.0, 105.0,
                             106.0, 107.0, 108.0, 109.0, 110.0, 111.0,
                             112.0, 113.0, 114.0, 115.0, 116.0, 117.0])
    @mock.patch('time.sleep')
    @mock.patch('etcd3gw.lock.Lock.release')
    @mock.patch('etcd3gw.lock.Lock.acquire',
                side_effect=[False, False, False, False, False, True])
    @mock.patch('os.getpid', return_value=42)
    def test_context_manager_timeout(
            self, mock_pid, mock_acquire, mock_release, mock_sleep,
            mock_time, mock_add_event, mock_get_holder):
        al = etcd.ActualLock('instance', None, 'auuid', op='Test case',
                             timeout=4)
        al.log_ctx = mock.MagicMock()

        self.assertRaises(exceptions.LockException, al.__enter__)

        mock_acquire.assert_has_calls([mock.call(), mock.call()])
        mock_sleep.assert_has_calls([mock.call(1), mock.call(1)])

        mock_add_event.assert_has_calls(
            [mock.call('instance', 'auuid', 'lock', 'acquire', None,
                       'Waiting for lock more than threshold'),
             mock.call('instance', 'auuid', 'lock', 'failed', None,
                       'Failed to acquire lock after 6.00 seconds')])
        mock_get_holder.assert_called_with()

        mock_release.assert_not_called()


class TaskEncodingETCDtestCase(test_shakenfist.ShakenFistTestCase):
    @mock.patch('etcd3gw.Etcd3Client.put')
    def test_put_PreflightInstanceTask(self, mock_put):
        etcd.put('objecttype', 'subtype', 'name',
                 tasks.PreflightInstanceTask('fake_uuid'))

        path = '/sf/objecttype/subtype/name'
        encoded = '''{
    "instance_uuid": "fake_uuid",
    "network": [],
    "task": "instance_preflight",
    "version": 1
}'''
        mock_put.assert_called_with(path, encoded, lease=None)

    @mock.patch('etcd3gw.Etcd3Client.put')
    def test_put_StartInstanceTask(self, mock_put):
        etcd.put('objecttype', 'subtype', 'name',
                 tasks.StartInstanceTask('fake_uuid', ['net_uuid']))

        path = '/sf/objecttype/subtype/name'
        encoded = '''{
    "instance_uuid": "fake_uuid",
    "network": [
        "net_uuid"
    ],
    "task": "instance_start",
    "version": 1
}'''
        mock_put.assert_called_with(path, encoded, lease=None)

    @mock.patch('etcd3gw.Etcd3Client.put')
    def test_put_DeleteInstanceTask(self, mock_put):
        etcd.put('objecttype', 'subtype', 'name',
                 tasks.DeleteInstanceTask('fake_uuid'))

        path = '/sf/objecttype/subtype/name'
        encoded = '''{
    "instance_uuid": "fake_uuid",
    "network": [],
    "task": "instance_delete",
    "version": 1
}'''
        mock_put.assert_called_with(path, encoded, lease=None)

    @mock.patch('etcd3gw.Etcd3Client.put')
    def test_put_DeployNetworkTask(self, mock_put):
        etcd.put('objecttype', 'subtype', 'name',
                 tasks.DeployNetworkTask('fake_uuid'))

        path = '/sf/objecttype/subtype/name'
        encoded = '''{
    "network_uuid": "fake_uuid",
    "task": "network_deploy",
    "version": 1
}'''
        mock_put.assert_called_with(path, encoded, lease=None)

    @mock.patch('etcd3gw.Etcd3Client.put')
    def test_put_FetchImageTask(self, mock_put):
        etcd.put('objecttype', 'subtype', 'name',
                 tasks.FetchImageTask('http://server/image'))

        path = '/sf/objecttype/subtype/name'
        encoded = '''{
    "instance_uuid": null,
    "task": "image_fetch",
    "url": "http://server/image",
    "version": 1
}'''
        mock_put.assert_called_with(path, encoded, lease=None)

        etcd.put('objecttype', 'subtype', 'name',
                 tasks.FetchImageTask('http://server/image',
                                      instance_uuid='fake_uuid'))

        path = '/sf/objecttype/subtype/name'
        encoded = '''{
    "instance_uuid": "fake_uuid",
    "task": "image_fetch",
    "url": "http://server/image",
    "version": 1
}'''
        mock_put.assert_called_with(path, encoded, lease=None)


#
# Decode tasks from JSON
#
class TaskDecodingETCDtestCase(test_shakenfist.ShakenFistTestCase):
    """Test that decodeTasks does decode subclasses of QueueTasks.

    Only need to check that JSON will convert to QueueTask objects. Testing of
    the actual JSON conversion is in TaskDequeueTestCase.
    """

    def test_decode_PreflightInstanceTask(self):
        obs = etcd.decodeTasks({'tasks': [
            {
                'instance_uuid': 'fake_uuid',
                'network': None,
                'task': 'instance_preflight',
                'version': 1,
            }
        ]})

        self.assertItemsEqual(
            {'tasks': [tasks.PreflightInstanceTask('fake_uuid')]},
            obs)

    def test_decode_multi(self):
        obs = etcd.decodeTasks({'tasks': [
            {
                'instance_uuid': 'fake_uuid',
                'network': None,
                'task': 'instance_preflight',
                'version': 1,
            },
            {
                'instance_uuid': 'fake_uuid',
                'task': 'image_fetch',
                'url': 'http://whoknows',
                'version': 1,
            }]})

        self.assertItemsEqual(
            {'tasks': [
                tasks.PreflightInstanceTask('fake_uuid'),
                tasks.FetchImageTask('http://whoknows', 'fake_uuid')
            ]},
            obs)


#
# Dequeue tasks from ETCD
#
class TaskDequeueTestCase(test_shakenfist.ShakenFistTestCase):
    @mock.patch('etcd3gw.Etcd3Client.get_prefix', return_value=[(
        '''{
            "tasks": [
                        {
                            "instance_uuid": "diff_uuid",
                            "task": "instance_preflight",
                            "version": 1
                        }
                     ]
            }
        ''',
        {
            'key': '/somejob'
        },
    )])
    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('etcd3gw.Etcd3Client.delete')
    def test_dequeue_preflight(self, m_delete, m_put, m_get_lock, m_get_prefix):
        jobname, workitem = etcd.dequeue('node01')
        self.assertEqual('somejob', jobname)
        expected = [
            tasks.PreflightInstanceTask('diff_uuid'),
        ]
        self.assertCountEqual(expected, workitem['tasks'])
        self.assertSequenceEqual(expected, workitem['tasks'])

    @mock.patch('etcd3gw.Etcd3Client.get_prefix', return_value=[(
        '''{
            "tasks": [
                        {
                            "instance_uuid": "fake_uuid",
                            "task": "instance_start",
                            "version": 1
                        }
                     ]
            }
        ''',
        {
            'key': '/somejob'
        },
    )])
    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('etcd3gw.Etcd3Client.delete')
    def test_dequeue_start(self, m_delete, m_put, m_get_lock, m_get_prefix):
        jobname, workitem = etcd.dequeue('node01')
        self.assertEqual('somejob', jobname)
        expected = [
            tasks.StartInstanceTask('fake_uuid'),
        ]
        self.assertCountEqual(expected, workitem['tasks'])
        self.assertSequenceEqual(expected, workitem['tasks'])

    @mock.patch('etcd3gw.Etcd3Client.get_prefix', return_value=[(
        '''{
            "tasks": [
                        {
                            "network": [],
                            "instance_uuid": "fake_uuid",
                            "task": "instance_delete",
                            "version": 1
                        }
                    ]
            }
        ''',
        {
            'key': '/somejob'
        },
    )])
    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('etcd3gw.Etcd3Client.delete')
    def test_dequeue_error(self, m_delete, m_put, m_get_lock, m_get_prefix):
        jobname, workitem = etcd.dequeue('node01')
        self.assertEqual('somejob', jobname)
        expected = [
            tasks.DeleteInstanceTask('fake_uuid'),
        ]
        self.assertCountEqual(expected, workitem['tasks'])
        self.assertSequenceEqual(expected, workitem['tasks'])

    @mock.patch('etcd3gw.Etcd3Client.get_prefix', return_value=[(
        '''{
            "tasks": [
                        {
                            "instance_uuid": "diff_uuid",
                            "task": "instance_preflight",
                            "version": 1
                        },
                        {
                            "instance_uuid": "fake_uuid",
                            "task": "instance_start",
                            "version": 1
                        },
                        {
                            "network": [],
                            "instance_uuid": "fake_uuid",
                            "task": "instance_delete",
                            "version": 1
                        }
                    ]
            }
        ''',
        {
            'key': '/somejob'
        },
    )])
    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('etcd3gw.Etcd3Client.delete')
    def test_dequeue_multi(self, m_delete, m_put, m_get_lock, m_get_prefix):
        jobname, workitem = etcd.dequeue('node01')
        self.assertEqual('somejob', jobname)
        expected = [
            tasks.PreflightInstanceTask('diff_uuid'),
            tasks.StartInstanceTask('fake_uuid'),
            tasks.DeleteInstanceTask('fake_uuid'),
        ]
        self.assertCountEqual(expected, workitem['tasks'])
        self.assertSequenceEqual(expected, workitem['tasks'])

    @mock.patch('etcd3gw.Etcd3Client.get_prefix', return_value=[(
        '''{
            "tasks": [
                        {
                            "network": [],
                            "instance_uuid": "fake_uuid",
                            "task": "instance_delete",
                            "version": 1
                        }
                    ]
            }
        ''',
        {
            'key': '/somejob'
        },
    )])
    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('etcd3gw.Etcd3Client.delete')
    def test_dequeue_delete(self, m_delete, m_put, m_get_lock, m_get_prefix):
        jobname, workitem = etcd.dequeue('node01')
        self.assertEqual('somejob', jobname)
        expected = [
            tasks.DeleteInstanceTask('fake_uuid'),
        ]
        self.assertCountEqual(expected, workitem['tasks'])
        self.assertSequenceEqual(expected, workitem['tasks'])

    @mock.patch('etcd3gw.Etcd3Client.get_prefix', return_value=[(
        '''{
            "tasks": [
                        {
                            "instance_uuid": "fake_uuid",
                            "task": "image_fetch",
                            "url": "http://whoknows",
                            "version": 1
                        }
                    ]
            }
        ''',
        {
            'key': '/somejob'
        },
    )])
    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('etcd3gw.Etcd3Client.delete')
    def test_dequeue_image_fetch(self, m_delete, m_put, m_get_lock, m_get_prefix):
        jobname, workitem = etcd.dequeue('node01')
        self.assertEqual('somejob', jobname)
        expected = [
            tasks.FetchImageTask('http://whoknows', 'fake_uuid'),
        ]
        self.assertCountEqual(expected, workitem['tasks'])
        self.assertSequenceEqual(expected, workitem['tasks'])


#
# General ETCD operations
#
class GeneralETCDtestCase(test_shakenfist.ShakenFistTestCase):
    maxDiff = None

    @mock.patch('etcd3gw.Etcd3Client.get_prefix',
                return_value=[
                    ('''{"checksum": "ed44b9745b8d62bcbbc180b5f36c24bb",
                        "file_version": 1,
                        "size": "359464960",
                        "version": 1
                        }''',
                     {'key': b'/sf/image/095fdd2b66625412aa/sf-2',
                      'create_revision': '198335947',
                      'mod_revision': '198335947',
                      'version': '1'}),
                    ('''{"checksum": null,
                        "file_version": 1,
                        "size": "16338944",
                        "version": 1
                        }''',
                     {'key': b'/sf/image/aca41cefa18b052074e092/sf-2',
                      'create_revision': '200780292',
                      'mod_revision': '200780292',
                      'version': '1'
                      })])
    def test_get_all_dict(self, mock_get_prefix):
        data = etcd.get_all_dict('objecttype', 'subtype')
        self.assertDictEqual({
            '/sf/image/095fdd2b66625412aa/sf-2': {
                "checksum": "ed44b9745b8d62bcbbc180b5f36c24bb",
                "file_version": 1,
                "size": '359464960',
                "version": 1
            },
            '/sf/image/aca41cefa18b052074e092/sf-2': {
                "checksum": None,
                "file_version": 1,
                "size": '16338944',
                "version": 1
            }
        },
            data)
