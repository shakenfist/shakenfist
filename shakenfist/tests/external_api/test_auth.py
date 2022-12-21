import base64
import bcrypt
import json
import logging
import mock

from shakenfist.config import config, SFConfig
from shakenfist import db
from shakenfist.external_api import app as external_api
from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


def _encode_key(key):
    return str(base64.b64encode(bcrypt.hashpw(
               key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')


def _clean_traceback(resp):
    if 'traceback' in resp:
        del resp['traceback']
    return resp


class FakeNamespaceNoServiceKey(object):
    def __init__(self):
        self.__keys = {
            'key1': _encode_key('bacon')
        }

    @property
    def service_key(self):
        return {}

    @property
    def keys(self):
        return self.__keys

    def remove_key(self, key_name):
        del self.__keys[key_name]


class FakeNamespaceServiceKey(FakeNamespaceNoServiceKey):
    @property
    def service_key(self):
        return {'service_key': 'cheese'}


class FakeScheduler(object):
    def place_instance(self, *args, **kwargs):
        return config.NODE_NAME


class AuthTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(AuthTestCase, self).setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.get_namespace = mock.patch(
            'shakenfist.namespace.Namespace.from_db')
        self.mock_get_namespace = self.get_namespace.start()
        self.addCleanup(self.get_namespace.stop)

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
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

    @mock.patch('shakenfist.namespace.Namespace.from_db',
                return_value=FakeNamespaceNoServiceKey())
    def test_post_auth(self, mock_get_keys):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))
        self.assertEqual(200, resp.status_code)
        self.assertIn('access_token', resp.get_json())

    @mock.patch('shakenfist.namespace.Namespace.from_db',
                return_value=FakeNamespaceServiceKey())
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
                'error': ("Missing 'Bearer' type in 'Authorization' header. Expected "
                          "'Authorization: Bearer <JWT>'"),
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


class AuthWithServiceKeyTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(AuthWithServiceKeyTestCase, self).setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        ns = Namespace.new('banana')
        ns.service_key = 'cheese'
        ns.add_key('key1', _encode_key('bacon'))
        ns.add_key('key2', _encode_key('sausage'))
        db.persist_metadata('namespace', 'banana', {})

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

    def test_post_auth_service_key(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'cheese'}))
        self.assertEqual(200, resp.status_code)
        self.assertIn('access_token', resp.get_json())


class AuthWithLingeringInstance(base.ShakenFistTestCase):
    def setUp(self):
        super(AuthWithLingeringInstance, self).setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.mock_etcd.create_namespace('foo', 'key1', 'banana')
        self.mock_etcd.create_instance(
            name='fooinst', uuid='123', namespace='foo')

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def test_delete_namespace_with_instances(self):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete a namespace with instances',
                'status': 400
            },
            resp.get_json())


class AuthWithLingeringNetwork(base.ShakenFistTestCase):
    def setUp(self):
        super(AuthWithLingeringNetwork, self).setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.mock_etcd.create_namespace('foo', 'key1', 'banana')
        self.mock_etcd.create_network(
            name='foonet', uuid='123', namespace='foo')

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def test_delete_namespace_with_networks(self):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete a namespace with networks',
                'status': 400
            },
            resp.get_json())


class AuthKeysTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(AuthKeysTestCase, self).setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.add_event = mock.patch('shakenfist.eventlog.add_event')
        self.add_event.start()
        self.addCleanup(self.add_event.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def test_auth_add_key_missing_args(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'no namespace specified',
                'status': 400
            },
            resp.get_json())

    def test_auth_add_key_missing_keyname(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'namespace': 'foo'
                                }))
        self.assertEqual(200, resp.status_code)
        self.assertEqual('foo', resp.get_json())

    def test_auth_add_key_missing_key(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
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

    def test_auth_add_key_illegal_keyname(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
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

    def test_delete_namespace_key_missing_args(self):
        resp = self.client.delete('/auth/namespaces/system/',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)
        self.assertEqual(None, resp.get_json())

    def test_delete_namespace_key_missing_key(self):
        resp = self.client.delete('/auth/namespaces/system/keys/mykey',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)
        self.assertEqual(
            {
                'error': 'key name not found in namespace',
                'status': 404
            },
            resp.get_json())


class ExternalApiTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(ExternalApiTestCase, self).setUp()

        self.recorded_op = mock.patch(
            'shakenfist.util.general.RecordedOperation')
        self.recorded_op.start()
        self.addCleanup(self.recorded_op.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.scheduler = mock.patch(
            'shakenfist.scheduler.Scheduler', FakeScheduler)
        self.mock_scheduler = self.scheduler.start()
        self.addCleanup(self.scheduler.stop)

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='node1',
            ETCD_HOST='127.0.0.1'
        )
        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_etcd.create_namespace('two', 'key1', 'space')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'two', 'key': 'space'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token_two = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_etcd.create_namespace('three', 'key1', 'pass')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'three', 'key': 'pass'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token_three = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_etcd.create_namespace('foo', 'key1', 'bar')

    def test_get_namespaces(self):
        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual([
            {'name': 'foo', 'state': 'created'},
            {'name': 'system', 'state': 'created'},
            {'name': 'three', 'state': 'created'},
            {'name': 'two', 'state': 'created'}
        ], resp.get_json())

    def test_delete_namespace_missing_args(self):
        resp = self.client.delete('/auth/namespaces',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(405, resp.status_code)
        self.assertEqual(
            {
                'message': 'The method is not allowed for the requested URL.'
            },
            resp.get_json())

    def test_delete_namespace_system(self):
        resp = self.client.delete('/auth/namespaces/system',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(403, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete the system namespace',
                'status': 403
            },
            resp.get_json())

    @mock.patch('shakenfist.db.get_metadata', return_value={'a': 'a', 'b': 'b'})
    def test_get_namespace_metadata(self, mock_md_get):
        resp = self.client.get(
            '/auth/namespaces/system/metadata', headers={'Authorization': self.auth_token})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_put_namespace_metadata(self, mock_get_lock, mock_md_put,
                                    mock_md_get):
        resp = self.client.put('/auth/namespaces/system/metadata/foo',
                               headers={'Authorization': self.auth_token},
                               data=json.dumps({
                                   'key': 'foo',
                                   'value': 'bar'
                               }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('namespace', 'system', {'foo': 'bar'})

    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_post_namespace_metadata(self, mock_get_lock, mock_md_put,
                                     mock_md_get):
        resp = self.client.post('/auth/namespaces/system/metadata',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'key': 'foo',
                                    'value': 'bar'
                                }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with('namespace', 'system', {'foo': 'bar'})

    @mock.patch('shakenfist.db.get_metadata', return_value={'foo': 'bar', 'real': 'smart'})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_namespace_metadata(self, mock_get_lock, mock_md_put,
                                       mock_md_get):
        resp = self.client.delete('/auth/namespaces/system/metadata/foo',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        mock_md_put.assert_called_with(
            'namespace', 'system', {'real': 'smart'})

    @mock.patch('shakenfist.db.get_metadata', return_value={})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_namespace_metadata_bad_key(self, mock_get_lock,
                                               mock_md_put, mock_md_get):
        resp = self.client.delete('/auth/namespaces/system/metadata/wrong',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual({'error': 'key not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)

    @mock.patch('shakenfist.db.get_metadata', return_value={'foo': 'bar', 'real': 'smart'})
    @mock.patch('shakenfist.db.persist_metadata')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_namespace_metadata_no_keys(self, mock_get_lock,
                                               mock_md_put, mock_md_get):
        resp = self.client.delete('/auth/namespaces/system/metadata/wrong',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual({'error': 'key not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)
