import json
import logging
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.config import SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


def _clean_traceback(resp):
    if 'traceback' in resp:
        del resp['traceback']
    return resp


class FakeScheduler:
    def find_candidates(self, *args, **kwargs):
        return config.NODE_NAME


class AuthTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.mock_etcd.create_namespace('banana', 'key1', 'bacon')

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

    def test_post_auth(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))
        self.assertEqual(200, resp.status_code)
        self.assertIn('access_token', resp.get_json())

    def test_post_auth_not_authorized(self):
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
                                headers={
                                    'Accept': 'application/json',
                                    'Authorization': 'l33thacker'
                                    },
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
                                headers={
                                    'Accept': 'application/json',
                                    'Authorization': 'Bearer l33thacker'
                                    },
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
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        ns = Namespace.new('banana')
        ns.add_key('_service_key', 'cheese')
        ns.add_key('key1', 'bacon')
        ns.add_key('key2', 'sausage')

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
        super().setUp()

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
        super().setUp()

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
        super().setUp()

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
        self.assertEqual({
            'keys': [],
            'metadata': {},
            'name': 'foo',
            'state': 'created',
            'trust': {'full': ['system']},
            'version': 5
        }, resp.get_json())

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
        super().setUp()

        self.recorded_op = mock.patch(
            'shakenfist.util.general.RecordedOperation')
        self.recorded_op.start()
        self.addCleanup(self.recorded_op.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.mock_etcd.create_namespace('banana', 'key1', 'cheese')

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
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'banana',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 5
            },
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'foo',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 5
            },
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'system',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 5
            },
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'three',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 5
            },
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'two',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 5
            }
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

    def test_get_namespace_metadata(self):
        self.mock_etcd.db['/sf/attribute/namespace/system/metadata'] = \
            json.dumps({'a': 'a', 'b': 'b'}, indent=4, sort_keys=True)
        resp = self.client.get(
            '/auth/namespaces/system/metadata', headers={'Authorization': self.auth_token})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.etcd.get_lock')
    def test_put_namespace_metadata(self, mock_get_lock):
        resp = self.client.put('/auth/namespaces/system/metadata/foo',
                               headers={'Authorization': self.auth_token},
                               data=json.dumps({
                                   'key': 'foo',
                                   'value': 'bar'
                               }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            json.dumps({'foo': 'bar'}, indent=4, sort_keys=True),
            self.mock_etcd.db['/sf/attribute/namespace/system/metadata'])

    @mock.patch('shakenfist.etcd.get_lock')
    def test_post_namespace_metadata(self, mock_get_lock):
        resp = self.client.post('/auth/namespaces/system/metadata',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'key': 'foo',
                                    'value': 'bar'
                                }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            json.dumps({'foo': 'bar'}, indent=4, sort_keys=True),
            self.mock_etcd.db['/sf/attribute/namespace/system/metadata'])

    @mock.patch('shakenfist.etcd.get_lock')
    def test_delete_namespace_metadata(self, mock_get_lock):
        self.mock_etcd.db['/sf/attribute/namespace/system/metadata'] = \
            json.dumps({'foo': 'bar', 'real': 'smart'}, indent=4,
                       sort_keys=True)
        resp = self.client.delete('/auth/namespaces/system/metadata/foo',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            json.dumps({'real': 'smart'}, indent=4, sort_keys=True),
            self.mock_etcd.db['/sf/attribute/namespace/system/metadata'])

    @mock.patch('shakenfist.etcd.get_lock')
    def test_delete_namespace_metadata_bad_key(self, mock_get_lock):
        # We now just silently ignore deletes of things which don't exist
        resp = self.client.delete('/auth/namespaces/system/metadata/wrong',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.etcd.get_lock')
    def test_delete_namespace_metadata_no_keys(self, mock_get_lock):
        # We now just silently ignore deletes of things which don't exist
        resp = self.client.delete('/auth/namespaces/system/metadata/wrong',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.artifact.Artifact.from_url')
    @mock.patch('shakenfist.network.Network._db_get_attribute',
                return_value={'value': dbo.STATE_CREATED, 'update_time': 2})
    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('shakenfist.ipmanager.IPManager.from_db')
    def test_post_instance_only_system_specifies_namespaces(
            self, mock_ipmanager, mock_lock, mock_net_attribute,
            mock_get_artifact):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'cheese'}))
        self.assertEqual(200, resp.status_code)
        non_system_auth_header = 'Bearer %s' % resp.get_json()[
            'access_token']

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
            {'error': 'namespace not found',
             'status': 404},
            resp.get_json())
        self.assertEqual(404, resp.status_code)

    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('shakenfist.etcd.put')
    def test_delete_namespace_key(self, mock_put, mock_lock):
        resp = self.client.delete('/auth/namespaces/system/keys/key1',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.etcd.get_lock')
    @mock.patch('bcrypt.hashpw', return_value=b'terminator')
    def test_auth_add_key_new_namespace(self, mock_hashpw, mock_lock):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'namespace': 'foo-unique',
                                    'key_name': 'bernard',
                                    'key': 'cheese'
                                }))
        self.assertEqual(200, resp.status_code)
        self.assertEqual({
            'keys': ['bernard'],
            'metadata': {},
            'name': 'foo-unique',
            'state': 'created',
            'trust': {'full': ['system']},
            'version': 5
        }, resp.get_json())
