import base64
import bcrypt
import json
import mock


from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    State)
from shakenfist.config import config, BaseSettings, SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.ipmanager import IPManager
from shakenfist.tests import test_shakenfist


class FakeResponse(object):
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


class FakeScheduler(object):
    def place_instance(self, *args, **kwargs):
        return config.NODE_NAME


class BaseFakeObject(object):
    def __init__(self, state=None):
        self._state = state

    @property
    def state(self):
        if isinstance(self._state, list):
            s = self._state[0]
            self._state = self._state[1:]
            return State(s, 1)
        else:
            return State(self._state, 1)

    @state.setter
    def state(self, state):
        self._state = state

    def unique_label(self):
        return ('instance', self.uuid)

    def add_event(self, operation, phase, duration=None, msg=None):
        pass

    def delete(self):
        pass


class FakeInstance(BaseFakeObject):
    object_type = 'instance'

    def __init__(self, uuid=None, namespace=None,
                 state=dbo.STATE_CREATED, power_state='on',
                 placement='node1'):
        super(FakeInstance, self).__init__(state)

        self.uuid = uuid
        self.namespace = namespace
        self.power_state = {'power_state': power_state}
        self.placement = {'node': placement}
        self.version = 2


class FakeNetwork(BaseFakeObject):
    object_type = 'network'

    def __init__(self, uuid=None, vxid=None, namespace=None,
                 name=None, netblock=None, state=dbo.STATE_CREATED):
        super(FakeNetwork, self).__init__(state)
        self.uuid = uuid
        self.vxid = vxid
        self.namespace = namespace
        self.name = name
        self.netblock = netblock
        self.version = 2

    def is_dead(self):
        return False

    def remove_dhcp(self):
        pass


def _encode_key(key):
    return bcrypt.hashpw(key.encode('utf-8'), bcrypt.gensalt())


def _clean_traceback(resp):
    if 'traceback' in resp:
        del resp['traceback']
    return resp


class AuthTestCase(test_shakenfist.ShakenFistTestCase):
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

    def test_post_auth_bad_parameter(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'keyyy': 'pwd'}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': "post() got an unexpected keyword argument 'keyyy'",
                'status': 400
            },
            _clean_traceback(resp.get_json()))

    def test_post_auth_key_non_string(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 1234}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'key is not a string',
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

    def test_no_auth_header(self):
        resp = self.client.post('/auth/namespaces',
                                data=json.dumps({
                                    'namespace': 'foo'
                                }))
        self.assertEqual(401, resp.status_code)
        self.assertEqual(
            {
                'error': 'Missing Authorization Header',
                'status': 401
            },
            _clean_traceback(resp.get_json()))

    def test_auth_header_wrong(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': 'l33thacker'},
                                data=json.dumps({
                                    'namespace': 'foo'
                                }))
        self.assertEqual(
            {
                'error': "Bad Authorization header. Expected value 'Bearer <JWT>'",
                'status': 401
            },
            _clean_traceback(resp.get_json()))
        self.assertEqual(401, resp.status_code)

    def test_auth_header_bad_jwt(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': 'Bearer l33thacker'},
                                data=json.dumps({
                                    'namespace': 'foo'
                                }))
        self.assertEqual(
            {
                'error': 'invalid JWT in Authorization header',
                'status': 401
            },
            _clean_traceback(resp.get_json()))
        self.assertEqual(401, resp.status_code)


class ExternalApiTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super(ExternalApiTestCase, self).setUp()

        self.add_event = mock.patch(
            'shakenfist.db.add_event')
        self.mock_add_event = self.add_event.start()
        self.addCleanup(self.add_event.stop)

        self.scheduler = mock.patch(
            'shakenfist.scheduler.Scheduler', FakeScheduler)
        self.mock_scheduler = self.scheduler.start()
        self.addCleanup(self.scheduler.stop)

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
        self.addCleanup(self.get_keys.stop)

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'foo'}))
        print(resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.auth_header = 'Bearer %s' % resp.get_json()['access_token']

        fake_config = SFConfig(
            NODE_NAME='node1',
        )

        self.config = mock.patch('shakenfist.instance.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)


class ExternalApiGeneralTestCase(ExternalApiTestCase):
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
                    ('/sf/namespace/aaa', {'name': 'aaa'}),
                    ('/sf/namespace/bbb', {'name': 'bbb'}),
                    ('/sf/namespace/ccc', {'name': 'ccc'})
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

    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                return_value={'value': dbo.STATE_CREATED, 'update_time': 2})
    @mock.patch('shakenfist.instance.Instances',
                return_value=[FakeInstance(uuid='123')])
    def test_delete_namespace_with_instances(self, mock_get_instances,
                                             mock_get_instance_attribute):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete a namespace with instances',
                'status': 400
            },
            resp.get_json())

    @mock.patch('shakenfist.instance.Instances', return_value=[])
    @mock.patch('shakenfist.net.Networks',
                return_value=[FakeNetwork(uuid='123', state=dbo.STATE_CREATED)])
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

    @mock.patch('shakenfist.instance.Instance._db_get',
                return_value={
                    'cpus': 1,
                    'disk_spec': [{}],
                    'memory': 2048,
                    'name': 'barry',
                    'namespace': 'namespace',
                    'requested_placement': None,
                    'ssh_key': 'sshkey',
                    'user_data': 'userdata',
                    'uuid': 'uuid42',
                    'version': 3,
                    'video': {'memory': 16384, 'model': 'cirrus'},
                    'uefi': False
                })
    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                return_value={})
    def test_get_instance(self, mock_get_instance_attribute, mock_get_instance):
        resp = self.client.get(
            '/instances/foo', headers={'Authorization': self.auth_header})
        self.assertEqual({
            'console_port': None,
            'cpus': 1,
            'disk_spec': [{}],
            'error_message': None,
            'memory': 2048,
            'name': 'barry',
            'namespace': 'namespace',
            'node': None,
            'power_state': None,
            'ssh_key': 'sshkey',
            'state': None,
            'uefi': False,
            'user_data': 'userdata',
            'uuid': 'uuid42',
            'vdi_port': None,
            'version': 3,
            'video': {'memory': 16384, 'model': 'cirrus'}
        }, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.instance.Instance._db_get', return_value=None)
    def test_get_instance_not_found(self, mock_get_instance):
        resp = self.client.get(
            '/instances/foo', headers={'Authorization': self.auth_header})
        self.assertEqual({'error': 'instance not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.instance.Instance._db_get',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo',
                              'disk_spec': [{'size': 4, 'base': 'foo'}]})
    @mock.patch('shakenfist.db.get_metadata', return_value={'a': 'a', 'b': 'b'})
    def test_get_instance_metadata(self, mock_get_instance, mock_md_get):
        resp = self.client.get(
            '/instances/foo/metadata', headers={'Authorization': self.auth_header})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.instance.Instance._db_get',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo',
                              'disk_spec': [{'size': 4, 'base': 'foo'}]})
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

    @mock.patch('shakenfist.instance.Instance._db_get',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo',
                              'disk_spec': [{'size': 4, 'base': 'foo'}]})
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

    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork(
                    uuid='foo',
                    name='banana',
                    namespace='foo'))
    @mock.patch('shakenfist.db.get_metadata', return_value={'a': 'a', 'b': 'b'})
    def test_get_network_metadata(self, mock_md_get, mock_get_network):
        resp = self.client.get(
            '/networks/foo/metadata', headers={'Authorization': self.auth_header})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork(
                    uuid='foo',
                    name='banana',
                    namespace='foo'))
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

    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork(
                    uuid='foo',
                    name='banana',
                    namespace='foo'))
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

    @mock.patch('shakenfist.instance.Instance._db_get',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo',
                              'disk_spec': [{'size': 4, 'base': 'foo'}]})
    @mock.patch('shakenfist.db.get_metadata', return_value={'foo': 'bar', 'real': 'smart'})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_instance_metadata(self, mock_get_lock, mock_md_put,
                                      mock_md_get, mock_get_instance):
        resp = self.client.delete('/instances/foo/metadata/foo',
                                  headers={'Authorization': self.auth_header})
        self.assertEqual(None, resp.get_json())
        mock_md_put.assert_called_with('instance', 'foo', {'real': 'smart'})
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.instance.Instance._db_get',
                return_value={'uuid': 'foo',
                              'name': 'banana',
                              'namespace': 'foo',
                              'disk_spec': [{'size': 4, 'base': 'foo'}]})
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

    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork(
                    uuid='foo',
                    name='banana',
                    namespace='foo'))
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

    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork(
                    uuid='foo',
                    name='banana',
                    namespace='foo'))
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


class ExternalApiInstanceTestCase(ExternalApiTestCase):
    def setUp(self):
        super(ExternalApiInstanceTestCase, self).setUp()

        def fake_virt_from_db(uuid):
            return {'uuid': uuid}

        self.virt_from_db = mock.patch('shakenfist.instance.Instance.from_db',
                                       fake_virt_from_db)
        self.mock_virt_from_db = self.virt_from_db.start()
        self.addCleanup(self.virt_from_db.stop)

        class FakeConfig(BaseSettings):
            API_ASYNC_WAIT: int = 1
            LOG_METHOD_TRACE: int = 1

        fake_config = FakeConfig()

        self.config = mock.patch('shakenfist.config.config', fake_config)
        self.mock_config = self.config.start()

        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.db.enqueue')
    @mock.patch('shakenfist.instance.Instances',
                return_value=[
                    FakeInstance(
                        namespace='system',
                        uuid='6a973b82-31b3-4780-93e4-04d99ae49f3f',
                        state=[dbo.STATE_CREATED]),
                    FakeInstance(
                        namespace='system',
                        uuid='847b0327-9b17-4148-b4ed-be72b6722c17',
                        state=[dbo.STATE_CREATED])])
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_all_instances(
            self, mock_db_get_lock, mock_etcd_put,
            mock_get_instances, mock_enqueue):

        resp = self.client.delete('/instances',
                                  headers={'Authorization': self.auth_header},
                                  data=json.dumps({
                                      'confirm': True,
                                      'namespace': 'foo'
                                  }))
        self.assertEqual(['6a973b82-31b3-4780-93e4-04d99ae49f3f',
                          '847b0327-9b17-4148-b4ed-be72b6722c17'],
                         resp.get_json())
        self.assertEqual(200, resp.status_code)

    def test_post_instance_no_disk(self):
        resp = self.client.post('/instances',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'name': 'test-instance',
                                    'cpus': 1,
                                    'memory': 1024,
                                    'network': [],
                                    'disk': None,
                                    'ssh_key': None,
                                    'user_data': None,
                                    'placed_on': None,
                                    'namespace': None,
                                }))
        self.assertEqual(
            {'error': 'instance must specify at least one disk', 'status': 400},
            resp.get_json())
        self.assertEqual(400, resp.status_code)

    def test_post_instance_invalid_disk(self):
        resp = self.client.post('/instances',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'name': 'test-instance',
                                    'cpus': 1,
                                    'memory': 1024,
                                    'network': [],
                                    'disk': ['8@cirros'],
                                    'ssh_key': None,
                                    'user_data': None,
                                    'placed_on': None,
                                    'namespace': None,
                                }))
        self.assertEqual(
            {'error': 'disk specification should contain JSON objects', 'status': 400},
            resp.get_json())
        self.assertEqual(400, resp.status_code)

    def test_post_instance_invalid_network(self):
        resp = self.client.post('/instances',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'name': 'test-instance',
                                    'cpus': 1,
                                    'memory': 1024,
                                    'network': ['87c15186-5f73-4947-a9fb-2183c4951efc'],
                                    'disk': [{'size': 8,
                                              'base': 'cirros'}],
                                    'ssh_key': None,
                                    'user_data': None,
                                    'placed_on': None,
                                    'namespace': None,
                                }))
        self.assertEqual(
            {'error': 'network specification should contain JSON objects', 'status': 400},
            resp.get_json())
        self.assertEqual(400, resp.status_code)

    def test_post_instance_invalid_network_uuid(self):
        resp = self.client.post('/instances',
                                headers={'Authorization': self.auth_header},
                                data=json.dumps({
                                    'name': 'test-instance',
                                    'cpus': 1,
                                    'memory': 1024,
                                    'network': [
                                        {'uuid': '87c15186-5f73-4947-a9fb-2183c4951efc'}],
                                    'disk': [{'size': 8,
                                              'base': 'cirros'}],
                                    'ssh_key': None,
                                    'user_data': None,
                                    'placed_on': None,
                                    'namespace': None,
                                }))
        self.assertEqual(
            {'error': 'network specification is missing network_uuid', 'status': 400},
            resp.get_json())
        self.assertEqual(400, resp.status_code)

    @mock.patch('shakenfist.net.Network._db_get_attribute',
                return_value={'value': dbo.STATE_CREATED, 'update_time': 2})
    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork(
                    uuid='87c15186-5f73-4947-a9fb-2183c4951efc',
                    vxid=1,
                    namespace='nonespace',
                    name='bob',
                    netblock='10.10.0.0/24'
                ))
    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.ipmanager.IPManager.from_db')
    def test_post_instance_only_system_specifies_namespaces(
            self, mock_ipmanager, mock_lock, mock_net, mock_net_attribute):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'foo'}))
        self.assertEqual(200, resp.status_code)
        non_system_auth_header = 'Bearer %s' % resp.get_json()['access_token']

        resp = self.client.post('/instances',
                                headers={
                                    'Authorization': non_system_auth_header},
                                data=json.dumps({
                                    'name': 'test-instance',
                                    'cpus': 1,
                                    'memory': 1024,
                                    'network': [
                                        {'network_uuid': '87c15186-5f73-4947-a9fb-2183c4951efc'}],
                                    'disk': [{'size': 8,
                                              'base': 'cirros'}],
                                    'ssh_key': None,
                                    'user_data': None,
                                    'placed_on': None,
                                    'namespace': 'gerkin',
                                }))
        self.assertEqual(
            {'error': 'only admins can create resources in a different namespace',
             'status': 401},
            resp.get_json())
        self.assertEqual(401, resp.status_code)


class ExternalApiNetworkTestCase(ExternalApiTestCase):
    def setUp(self):
        super(ExternalApiNetworkTestCase, self).setUp()

        class FakeConfig(BaseSettings):
            NODE_NAME: str = 'seriously'
            NODE_EGRESS_IP: str = '127.0.0.1'
            NETWORK_NODE_IP = '127.0.0.1'
            LOG_METHOD_TRACE: int = 1
            NODE_EGRESS_NIC: str = 'eth0'
            NODE_MESH_NIC: str = 'eth1'

        fake_config_network = FakeConfig()
        self.config = mock.patch('shakenfist.config.config',
                                 fake_config_network)
        self.mock_config = self.config.start()
        # Without this cleanup, other test classes will have
        # 'shakenfist.config.config.get' mocked during parallel testing
        # by stestr.
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.net.Network._db_get_attribute',
                return_value={'value': dbo.STATE_CREATED, 'update_time': 2})
    @mock.patch('shakenfist.ipmanager.IPManager.from_db')
    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork(
                    uuid='30f6da44-look-i-am-uuid',
                    vxid=1,
                    namespace='nonespace',
                    name='bob',
                    netblock='10.10.0.0/24'
                ))
    @mock.patch('shakenfist.net.Networks',
                return_value=[FakeNetwork(
                    uuid='30f6da44-look-i-am-uuid',
                    vxid=1,
                    namespace='nonespace',
                    name='bob',
                    netblock='10.10.0.0/24'
                )])
    @mock.patch('shakenfist.networkinterface.interfaces_for_network', return_value=[])
    @mock.patch('shakenfist.ipmanager.IPManager.from_db',
                return_value=IPManager('uuid', '10.0.0.0/24'))
    @mock.patch('shakenfist.net.Network.remove_dhcp')
    @mock.patch('shakenfist.net.Network.delete_on_network_node')
    @mock.patch('shakenfist.net.Network.delete_on_hypervisor')
    @mock.patch('shakenfist.net.Network.state')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.etcd.enqueue')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_all_networks(self,
                                 mock_db_get_lock,
                                 mock_etcd_enqueue,
                                 mock_etcd_put,
                                 mock_network_state,
                                 mock_delete_on_hypervisor,
                                 mock_delete_on_network_node,
                                 mock_remove_dhcp,
                                 mock_get_ipmanager,
                                 mock_network_interfaces,
                                 mock_db_get_networks,
                                 mock_db_get_network,
                                 mock_ipmanager_from_db,
                                 mock_net_attribute):

        resp = self.client.delete('/networks',
                                  headers={'Authorization': self.auth_header},
                                  data=json.dumps({
                                      'confirm': True,
                                      'namespace': 'foo'
                                  }))
        self.assertEqual(['30f6da44-look-i-am-uuid'],
                         resp.get_json())
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.net.Network.from_db',
                return_value=FakeNetwork(
                    uuid='30f6da44-look-i-am-uuid',
                    vxid=1,
                    namespace='foo',
                    name='bob',
                    netblock='10.10.0.0/24',
                    state=dbo.STATE_DELETED
                ))
    @mock.patch('shakenfist.etcd.get_all',
                return_value=[(None, {'uuid': '30f6da44-look-i-am-uuid'})])
    @mock.patch('shakenfist.networkinterface.interfaces_for_network', return_value=[])
    @mock.patch('shakenfist.ipmanager.IPManager.from_db',
                return_value=IPManager('uuid', '10.0.0.0/24'))
    @mock.patch('shakenfist.net.Network.remove_dhcp')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_all_networks_none_to_delete(self,
                                                mock_db_get_lock,
                                                mock_etcd_put,
                                                mock_remove_dhcp,
                                                mock_get_ipmanager,
                                                mock_network_interfaces,
                                                mock_db_get_networks,
                                                mock_db_get_network):
        resp = self.client.delete('/networks',
                                  headers={'Authorization': self.auth_header},
                                  data=json.dumps({
                                      'confirm': True,
                                      'namespace': 'foo'
                                  }))
        self.assertEqual([], resp.get_json())
