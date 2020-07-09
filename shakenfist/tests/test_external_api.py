import base64
import bcrypt
import json
import mock
import testtools


from shakenfist import config
from shakenfist.external_api import app as external_api
from shakenfist import util


class FakeResponse(object):
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


class FakeScheduler(object):
    def place_instance(self, *args, **kwargs):
        return config.parsed.get('NODE_NAME')


def _encode_key(key):
    return bcrypt.hashpw(key.encode('utf-8'), bcrypt.gensalt())


class AuthTestCase(testtools.TestCase):
    def setUp(self):
        super(AuthTestCase, self).setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False
        self.client = external_api.app.test_client()

    def test_post_auth_no_args(self):
        resp = self.client.post('/auth', data=json.dumps({}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'missing namespace in request',
                'status': 400
            },
            resp.get_json())

    def test_post_auth_no_key(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana'}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'missing key in request',
                'status': 400
            },
            resp.get_json())

    @mock.patch('shakenfist.external_api.app.Auth._get_keys',
                return_value=(None, [_encode_key('cheese')]))
    def test_post_auth(self, mock_get_keys):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'cheese'}))
        self.assertEqual(200, resp.status_code)
        self.assertIn('access_token', resp.get_json())

    @mock.patch('shakenfist.external_api.app.Auth._get_keys',
                return_value=('cheese', [_encode_key('bacon')]))
    def test_post_auth_not_authorized(self, mock_get_keys):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'hamster'}))
        self.assertEqual(401, resp.status_code)
        self.assertEqual(
            {
                'error': 'unauthorized',
                'status': 401
            },
            resp.get_json())

    @mock.patch('shakenfist.etcd.get',
                return_value={
                    'service_key': 'cheese',
                    'keys': {
                        'key1': str(base64.b64encode(_encode_key('bacon')), 'utf-8'),
                        'key2': str(base64.b64encode(_encode_key('sausage')), 'utf-8')
                    }
                })
    def test_post_auth_service_key(self, mock_get):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'cheese'}))
        self.assertEqual(200, resp.status_code)
        self.assertIn('access_token', resp.get_json())


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

        # Make a fake auth token
        self.get_keys = mock.patch(
            'shakenfist.external_api.app.Auth._get_keys',
            return_value=('foo', ['bar'])
        )
        self.mock_get_keys = self.get_keys.start()

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'foo'}))
        self.assertEqual(200, resp.status_code)
        self.auth_header = 'Bearer %s' % resp.get_json()['access_token']

    def test_get_root(self):
        resp = self.client.get('/')
        self.assertEqual('Shaken Fist REST API service',
                         resp.get_data().decode('utf-8'))
        self.assertEqual(200, resp.status_code)
        self.assertEqual('text/plain; charset=utf-8', resp.content_type)

    def test_auth_add_key_missing_args(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'no namespace specified',
                'status': 400
            },
            resp.get_json())

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value=None)
    @mock.patch('shakenfist.etcd.put')
    def test_auth_add_key_missing_keyname(self, mock_put, mock_get, mock_lock):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'namespace': 'foo'
                                }))
        self.assertEqual(200, resp.status_code)
        self.assertEqual('foo', resp.get_json())

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value=None)
    @mock.patch('shakenfist.etcd.put')
    def test_auth_add_key_missing_key(self, mock_put, mock_get, mock_lock):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'namespace': 'foo',
                                    'key_name': 'bernard'
                                }))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'no key specified',
                'status': 400
            },
            resp.get_json())

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value=None)
    def test_auth_add_key_illegal_keyname(self, mock_get, mock_lock):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'namespace': 'foo',
                                    'key_name': 'service_key',
                                    'key': 'cheese'
                                }))
        self.assertEqual(
            {
                'error': 'illegal key name',
                'status': 403
            },
            resp.get_json())
        self.assertEqual(403, resp.status_code)

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value=None)
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('bcrypt.hashpw', return_value='terminator'.encode('utf-8'))
    def test_auth_add_key_new_namespace(self, mock_hashpw, mock_put, mock_get, mock_lock):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'namespace': 'foo',
                                    'key_name': 'bernard',
                                    'key': 'cheese'
                                }))
        self.assertEqual(200, resp.status_code)
        self.assertEqual('foo', resp.get_json())
        mock_put.assert_called_with(
            'namespace', None, 'foo',
            {'name': 'foo', 'keys': {'bernard': 'dGVybWluYXRvcg=='}})

    @mock.patch('shakenfist.etcd.get_all',
                return_value=[
                    {'name': 'aaa'}, {'name': 'bbb'}, {'name': 'ccc'}
                ])
    def test_get_namespaces(self, mock_get_all):
        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': self.auth_header})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(['aaa', 'bbb', 'ccc'], resp.get_json())

    def test_delete_namespace_missing_args(self):
        resp = self.client.delete('/auth/namespaces',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(405, resp.status_code)
        self.assertEqual(
            {
                'message': 'The method is not allowed for the requested URL.'
            },
            resp.get_json())

    def test_delete_namespace_system(self):
        resp = self.client.delete('/auth/namespaces/system',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(403, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete the system namespace',
                'status': 403
            },
            resp.get_json())

    @mock.patch('shakenfist.db.get_instances',
                return_value=[{'uuid': '123', 'state': 'created'}])
    def test_delete_namespace_with_instances(self, mock_get_instances):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete a namespace with instances',
                'status': 400
            },
            resp.get_json())

    @mock.patch('shakenfist.db.get_instances', return_value=[])
    @mock.patch('shakenfist.db.get_networks',
                return_value=[{'uuid': '123', 'state': 'created'}])
    def test_delete_namespace_with_networks(self, mock_get_networks, mock_get_instances):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete a namespace with networks',
                'status': 400
            },
            resp.get_json())

    @mock.patch('shakenfist.db.get_instances',
                return_value=[{'uuid': '123', 'state': 'deleted'}])
    @mock.patch('shakenfist.db.get_networks',
                return_value=[{'uuid': '123', 'state': 'deleted'}])
    @mock.patch('shakenfist.db.hard_delete_instance')
    @mock.patch('shakenfist.db.hard_delete_network')
    @mock.patch('shakenfist.etcd.delete')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_namespace_with_deleted(self, mock_lock, mock_etcd_delete,
                                           mock_hd_network, mock_hd_instance,
                                           mock_get_networks, mock_get_instances):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_hd_instance.assert_called()
        mock_hd_network.assert_called()
        mock_etcd_delete.assert_called()

    def test_delete_namespace_key_missing_args(self):
        resp = self.client.delete('/auth/namespaces/system/',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(404, resp.status_code)
        self.assertEqual(None, resp.get_json())

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value={'keys': {}})
    def test_delete_namespace_key_missing_key(self, mock_get, mock_lock):
        resp = self.client.delete('/auth/namespaces/system/keys/mykey',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(404, resp.status_code)
        self.assertEqual(
            {
                'error': 'key name not found in namespace',
                'status': 404
            },
            resp.get_json())

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value={'keys': {'mykey': 'foo'}})
    @mock.patch('shakenfist.etcd.put')
    def test_delete_namespace_key(self, mock_put, mock_get, mock_lock):
        resp = self.client.delete('/auth/namespaces/system/keys/mykey',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(200, resp.status_code)
        mock_put.assert_called_with('namespace', None, 'system', {'keys': {}})

    @mock.patch('shakenfist.db.get_metadata', return_value={'a': 'a', 'b': 'b'})
    def test_get_namespace_metadata(self, mock_md_get):
        resp = self.client.get(
            '/auth/namespaces/foo/metadata', headers={'Authorization': self.auth_header})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_put_namespace_metadata(self, mock_get_lock, mock_md_put,
                                    mock_md_get):
        resp = self.client.put('/auth/namespaces/foo/metadata/foo',
                               headers={'Authorization': self.auth_header},
                               data=json.dumps({
                                   'key': 'foo',
                                   'value': 'bar'
                               }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('namespace', 'foo', {'foo': 'bar'})

    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_post_namespace_metadata(self, mock_get_lock, mock_md_put,
                                     mock_md_get):
        resp = self.client.post('/auth/namespaces/foo/metadata',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'key': 'foo',
                                    'value': 'bar'
                                }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('namespace', 'foo', {'foo': 'bar'})

    @mock.patch('shakenfist.db.get_metadata', return_value={'foo': 'bar', 'real': 'smart'})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_namespace_metadata(self, mock_get_lock, mock_md_put,
                                       mock_md_get):
        resp = self.client.delete('/auth/namespaces/foo/metadata/foo',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('namespace', 'foo', {'real': 'smart'})

    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_namespace_metadata_bad_key(self, mock_get_lock,
                                               mock_md_put, mock_md_get):
        resp = self.client.delete('/auth/namespaces/foo/metadata/wrong',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual({'error': 'key not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)

    @mock.patch('shakenfist.db.get_metadata', return_value={'foo': 'bar', 'real': 'smart'})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_namespace_metadata_no_keys(self, mock_get_lock,
                                               mock_md_put, mock_md_get):
        resp = self.client.delete('/auth/namespaces/foo/metadata/wrong',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual({'error': 'key not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)

    @mock.patch('shakenfist.db.get_instance',
                return_value={'uuid': '123',
                              'name': 'banana',
                              'namespace': 'foo'})
    def test_get_instance(self, mock_get_instance):
        resp = self.client.get(
            '/instances/foo', headers={'Authorization': self.auth_header})
        self.assertEqual({'uuid': '123', 'name': 'banana', 'namespace': 'foo'},
                         resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.db.get_instance', return_value=None)
    def test_get_instance_not_found(self, mock_get_instance):
        resp = self.client.get(
            '/instances/foo', headers={'Authorization': self.auth_header})
        self.assertEqual({'error': 'instance not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.db.get_instance',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={'a': 'a', 'b': 'b'})
    def test_get_instance_metadata(self, mock_get_instance, mock_md_get):
        resp = self.client.get(
            '/instances/foo/metadata', headers={'Authorization': self.auth_header})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.db.get_instance',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_put_instance_metadata(self, mock_get_lock, mock_md_put,
                                   mock_md_get, mock_get_instance):
        resp = self.client.put('/instances/foo/metadata/foo',
                               headers={'Authorization': self.auth_header},
                               data=json.dumps({
                                   'key': 'foo',
                                   'value': 'bar'
                               }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('instance', 'foo', {'foo': 'bar'})

    @mock.patch('shakenfist.db.get_instance',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_post_instance_metadata(self, mock_get_lock, mock_md_put,
                                    mock_md_get, mock_get_instance):
        resp = self.client.post('/instances/foo/metadata',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'key': 'foo',
                                    'value': 'bar'
                                }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('instance', 'foo', {'foo': 'bar'})

    @mock.patch('shakenfist.db.get_network',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={'a': 'a', 'b': 'b'})
    def test_get_network_metadata(self, mock_get_network, mock_md_get):
        resp = self.client.get(
            '/networks/foo/metadata', headers={'Authorization': self.auth_header})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.db.get_network',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_put_network_metadata(self, mock_get_lock, mock_md_put,
                                  mock_md_get, mock_get_network):
        resp = self.client.put('/networks/foo/metadata/foo',
                               headers={'Authorization': self.auth_header},
                               data=json.dumps({
                                   'key': 'foo',
                                   'value': 'bar'
                               }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('network', 'foo', {'foo': 'bar'})

    @mock.patch('shakenfist.db.get_network',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_post_network_metadata(self, mock_get_lock, mock_md_put,
                                   mock_md_get, mock_get_network):
        resp = self.client.post('/networks/foo/metadata',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'key': 'foo',
                                    'value': 'bar'
                                }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('network', 'foo', {'foo': 'bar'})

    @mock.patch('shakenfist.db.get_instance',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={'foo': 'bar', 'real': 'smart'})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_instance_metadata(self, mock_get_lock, mock_md_put,
                                      mock_md_get, mock_get_instance):
        resp = self.client.delete('/instances/foo/metadata/foo',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('instance', 'foo', {'real': 'smart'})

    @mock.patch('shakenfist.db.get_instance',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={'foo': 'bar', 'real': 'smart'})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_instance_metadata_bad_key(self, mock_get_lock,
                                              mock_md_put, mock_md_get,
                                              mock_get_instance):
        resp = self.client.delete('/instances/foo/metadata/wrong',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual({'error': 'key not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)

    @mock.patch('shakenfist.db.get_network',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={'foo': 'bar', 'real': 'smart'})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_network_metadata(self, mock_get_lock, mock_md_put,
                                     mock_md_get, mock_get_network):
        resp = self.client.delete('/networks/foo/metadata/foo',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('network', 'foo', {'real': 'smart'})

    @mock.patch('shakenfist.db.get_network',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo'})
    @mock.patch('shakenfist.db.get_metadata', return_value={'foo': 'bar', 'real': 'smart'})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_network_metadata_bad_key(self, mock_get_lock,
                                             mock_md_put, mock_md_get,
                                             mock_get_network):
        resp = self.client.delete('/networks/foo/metadata/wrong',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual({'error': 'key not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)
