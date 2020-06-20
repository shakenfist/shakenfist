import logging
import mock
import testtools


from shakenfist import config
from shakenfist.external_api import app as external_api


logging.basicConfig(level=logging.DEBUG)


class FakeResponse(object):
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class FakeScheduler(object):
    def place_instance(self, *args, **kwargs):
        return config.parsed.get('NODE_NAME')


class ExternalApiTestCase(testtools.TestCase):
    def setUp(self):
        super(ExternalApiTestCase, self).setUp()

        self.add_event = mock.patch(
            'shakenfist.db.add_event')
        self.mock_add_event = self.add_event.start()

        self.scheduler = mock.patch(
            'shakenfist.scheduler.Scheduler', FakeScheduler)
        self.mock_scheduler = self.scheduler.start()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False
        self.client = external_api.app.test_client()

    def test_get_root(self):
        resp = self.client.get('/')
        self.assertEqual('Shaken Fist REST API service',
                         resp.get_data().decode('utf-8'))
        self.assertEqual(200, resp.status_code)
        self.assertEqual('text/plain; charset=utf-8', resp.content_type)

    @mock.patch('shakenfist.db.get_instance', return_value=None)
    def test_get_instance_not_found(self, mock_get_instance):
        resp = self.client.get('/instances/foo')
        self.assertEqual({'error': 'instance not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.db.get_instance',
                return_value={'uuid': '123',
                              'name': 'banana'})
    def test_get_instance(self, mock_get_instance):
        resp = self.client.get('/instances/foo')
        self.assertEqual({'uuid': '123', 'name': 'banana'},
                         resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.db.get_instance',
                return_value={'uuid': '123',
                              'name': 'banana',
                              'node': 'notthisone',
                              'disk_spec': [{
                                  'base': 'cirros',
                                  'size': 8
                              }],
                              'block_devices': None})
    @mock.patch('shakenfist.config.parsed',
                return_value={'INCLUDE_TRACEBACKS': '1',
                              'NODE_NAME': 'thisone',
                              'STORAGE_PATH': '/a/b/c'})
    @mock.patch('requests.request',
                return_value=FakeResponse(200, '{"fakestuff": "here"}'))
    def test_delete_instance(self, mock_request, mock_get_config,
                             mock_get_instance):
        resp = self.client.delete('/instances/foo')
        mock_request.assert_called_with(
            'DELETE', 'http://notthisone:1/instances/foo', data='{}')
        self.assertEqual({'fakestuff': 'here'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
