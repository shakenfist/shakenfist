import mock
import testtools

from shakenfist import etcd
from shakenfist import tasks


class TaskEncodingETCDtestCase(testtools.TestCase):
    @mock.patch('etcd3gw.Etcd3Client.put')
    def test_put_PreflightInstanceTask(self, mock_put):
        etcd.put('objecttype', 'subtype', 'name',
                 tasks.PreflightInstanceTask('fake_uuid'))

        path = '/sf/objecttype/subtype/name'
        encoded = '''{
    "instance_uuid": "fake_uuid",
    "network": null,
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
                 tasks.DeleteInstanceTask('fake_uuid', 'next_state', 'dunno'))

        path = '/sf/objecttype/subtype/name'
        encoded = '''{
    "instance_uuid": "fake_uuid",
    "network": null,
    "next_state": "next_state",
    "next_state_message": "dunno",
    "task": "instance_delete",
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
class TaskDecodingETCDtestCase(testtools.TestCase):
    '''Test that decodeTasks does decode subclasses of QueueTasks.

    Only need to check that JSON will convert to QueueTask objects. Testing of
    the actual JSON conversion is in TaskDequeueTestCase.
    '''
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
class TaskDequeueTestCase(testtools.TestCase):
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
        self.assertSequenceEqual(set(expected), set(workitem['tasks']))

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
        self.assertSequenceEqual(set(expected), set(workitem['tasks']))

    @mock.patch('etcd3gw.Etcd3Client.get_prefix', return_value=[(
        '''{
            "tasks": [
                        {
                            "instance_uuid": "fake_uuid",
                            "task": "instance_delete",
                            "next_state": "where_to",
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
            tasks.DeleteInstanceTask('fake_uuid', 'where_to'),
        ]
        self.assertCountEqual(expected, workitem['tasks'])
        self.assertSequenceEqual(set(expected), set(workitem['tasks']))

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
                            "instance_uuid": "fake_uuid",
                            "task": "instance_delete",
                            "next_state": "where_to",
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
            tasks.DeleteInstanceTask('fake_uuid', 'where_to'),
        ]
        self.assertCountEqual(expected, workitem['tasks'])
        self.assertSequenceEqual(set(expected), set(workitem['tasks']))

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
        self.assertSequenceEqual(set(expected), set(workitem['tasks']))
