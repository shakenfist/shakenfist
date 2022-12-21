import base64
import bcrypt
import json
import logging
import mock

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    State)
from shakenfist.config import config, BaseSettings, SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.ipmanager import IPManager
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


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
        self.interfaces = []


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
        self.provide_nat = True

    def is_dead(self):
        return False

    def remove_dhcp(self):
        pass

    def networkinterfaces(self):
        return []


def _encode_key(key):
    return bcrypt.hashpw(key.encode('utf-8'), bcrypt.gensalt())


class AuthNoNamespaceMockTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(AuthNoNamespaceMockTestCase, self).setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

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


class ExternalApiGeneralTestCase(ExternalApiTestCase):
    def test_get_root(self):
        resp = self.client.get('/')
        self.assertEqual('Shaken Fist REST API service',
                         resp.get_data().decode('utf-8'))
        self.assertEqual(200, resp.status_code)
        self.assertEqual('text/plain; charset=utf-8', resp.content_type)

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

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value=None)
    @mock.patch('shakenfist.etcd.put')
    def test_auth_add_key_missing_keyname(self, mock_put, mock_get, mock_lock):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
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

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value=None)
    def test_auth_add_key_illegal_keyname(self, mock_get, mock_lock):
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

    @mock.patch('shakenfist.etcd.get_all',
                return_value=[
                    ('/sf/namespace/aaa', {'name': 'aaa'}),
                    ('/sf/namespace/bbb', {'name': 'bbb'}),
                    ('/sf/namespace/ccc', {'name': 'ccc'})
                ])
    def test_get_namespaces(self, mock_get_all):
        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(['aaa', 'bbb', 'ccc'], resp.get_json())

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

    @mock.patch('shakenfist.instance.Instance._db_get_attribute',
                return_value={'value': dbo.STATE_CREATED, 'update_time': 2})
    @mock.patch('shakenfist.instance.Instances',
                return_value=[FakeInstance(uuid='123')])
    def test_delete_namespace_with_instances(self, mock_get_instances,
                                             mock_get_instance_attribute):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete a namespace with instances',
                'status': 400
            },
            resp.get_json())

    @mock.patch('shakenfist.instance.Instances', return_value=[])
    @mock.patch('shakenfist.network.Networks',
                return_value=[FakeNetwork(uuid='123', state=dbo.STATE_CREATED)])
    def test_delete_namespace_with_networks(self, mock_get_networks, mock_get_instances):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete a namespace with networks',
                'status': 400
            },
            resp.get_json())

    def test_delete_namespace_key_missing_args(self):
        resp = self.client.delete('/auth/namespaces/system/',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)
        self.assertEqual(None, resp.get_json())

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value={'keys': {}})
    def test_delete_namespace_key_missing_key(self, mock_get, mock_lock):
        resp = self.client.delete('/auth/namespaces/system/keys/mykey',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)
        self.assertEqual(
            {
                'error': 'key name not found in namespace',
                'status': 404
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

    def test_get_instance(self):
        self.mock_etcd.create_instance('barry')
        self.mock_etcd.create_instance('alice')
        self.mock_etcd.create_instance('bob')

        # Instance by name
        resp = self.client.get('/instances/barry',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-1234-000000000001',
                         resp.get_json().get('uuid'))

        resp = self.client.get('/instances/bob',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-1234-000000000003',
                         resp.get_json().get('uuid'))

        # Instance by name - WRONG
        resp = self.client.get('/instances/bazza',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)

        # Instance by UUID
        resp = self.client.get('/instances/12345678-1234-4321-1234-000000000001',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-1234-000000000001',
                         resp.get_json().get('uuid'))

        # Instance by UUID - WRONG
        resp = self.client.get('/instances/12345678-1234-4321-1234-111111111111',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)

    def test_get_instance_by_namespace(self):
        self.mock_etcd.create_instance('barry')
        self.mock_etcd.create_instance('barry', namespace='two')
        self.mock_etcd.create_instance('bob', namespace='two')

        # Instance by name
        resp = self.client.get('/instances/barry',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(400, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual(
            {'error': 'multiple instances have the name "barry" in namespace "system"',
             'status': 400},
            resp.get_json())

        resp = self.client.get('/instances/barry',
                               headers={'Authorization': self.auth_token_two})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-1234-000000000002',
                         resp.get_json().get('uuid'))

        resp = self.client.get('/instances/bob',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-1234-000000000003',
                         resp.get_json().get('uuid'))

        # Instance by name - WRONG name
        resp = self.client.get('/instances/bazza',
                               headers={'Authorization': self.auth_token_two})
        self.assertEqual(404, resp.status_code)

        # Instance by name - WRONG namespace
        resp = self.client.get('/instances/barry',
                               headers={'Authorization': self.auth_token_three})
        self.assertEqual(404, resp.status_code)

    def test_get_instance_metadata(self):
        self.mock_etcd.create_instance('banana', metadata={'a': 'a', 'b': 'b'})
        resp = self.client.get(
            '/instances/12345678-1234-4321-1234-000000000001/metadata',
            headers={'Authorization': self.auth_token})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual(200, resp.status_code)

    def test_put_instance_metadata(self):
        self.mock_etcd.create_instance('banana')
        resp = self.client.put(
            '/instances/12345678-1234-4321-1234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'key': 'foo',
                'value': 'bar'
            }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            json.loads(self.mock_etcd.db[
                '/sf/metadata/instance/12345678-1234-4321-1234-000000000001']))

    def test_post_instance_metadata(self):
        self.mock_etcd.create_instance('banana')
        resp = self.client.post(
            '/instances/12345678-1234-4321-1234-000000000001/metadata',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'key': 'foo',
                'value': 'bar'
            }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            json.loads(self.mock_etcd.db[
                '/sf/metadata/instance/12345678-1234-4321-1234-000000000001']))

    def test_get_network(self):
        self.mock_etcd.create_network('barry')
        self.mock_etcd.create_network('alice')
        self.mock_etcd.create_network('bob')

        # Instance by name
        resp = self.client.get('/networks/barry',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-1234-000000000001',
                         resp.get_json().get('uuid'))

        resp = self.client.get('/networks/bob',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-1234-000000000003',
                         resp.get_json().get('uuid'))

        # Instance by name - WRONG
        resp = self.client.get('/networks/bazza',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)

        # Instance by UUID
        resp = self.client.get('/networks/12345678-1234-4321-1234-000000000001',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-1234-000000000001',
                         resp.get_json().get('uuid'))

        # Instance by UUID - WRONG
        resp = self.client.get('/networks/12345678-1234-4321-1234-111111111111',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)

    def test_get_network_metadata(self):
        self.mock_etcd.create_network('banana', namespace='foo',
                                      metadata={'a': 'a', 'b': 'b'})
        resp = self.client.get(
            '/networks/12345678-1234-4321-1234-000000000001/metadata',
            headers={'Authorization': self.auth_token})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    def test_put_network_metadata(self):
        self.mock_etcd.create_network('banana', namespace='foo')
        resp = self.client.put(
            '/networks/12345678-1234-4321-1234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'key': 'foo',
                'value': 'bar'
            }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            json.loads(self.mock_etcd.db[
                '/sf/metadata/network/12345678-1234-4321-1234-000000000001']))

    def test_post_network_metadata(self):
        self.mock_etcd.create_network('banana', namespace='foo')
        resp = self.client.post(
            '/networks/12345678-1234-4321-1234-000000000001/metadata',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'key': 'foo',
                'value': 'bar'
            }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            json.loads(self.mock_etcd.db[
                '/sf/metadata/network/12345678-1234-4321-1234-000000000001']))

    def test_delete_instance_metadata(self):
        self.mock_etcd.create_instance('banana',
                                       metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/instances/12345678-1234-4321-1234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(None, resp.get_json())
        self.assertEqual(
            {'real': 'smart'},
            json.loads(self.mock_etcd.db[
                '/sf/metadata/instance/12345678-1234-4321-1234-000000000001']))

    def test_delete_instance_metadata_bad_key(self):
        self.mock_etcd.create_instance('banana',
                                       metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/instances/12345678-1234-4321-1234-000000000001/metadata/wrong',
            headers={'Authorization': self.auth_token})
        self.assertEqual({'error': 'key not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)

    def test_delete_network_metadata(self):
        self.mock_etcd.create_network('banana', namespace='foo',
                                      metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/networks/12345678-1234-4321-1234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'real': 'smart'},
            json.loads(self.mock_etcd.db[
                '/sf/metadata/network/12345678-1234-4321-1234-000000000001']))

    def test_delete_network_metadata_bad_key(self):
        self.mock_etcd.create_network('banana', namespace='system',
                                      metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/networks/12345678-1234-4321-1234-000000000001/metadata/wrong',
            headers={'Authorization': self.auth_token})
        self.assertEqual({'error': 'key not found', 'status': 404},
                         resp.get_json())
        self.assertEqual(404, resp.status_code)


class ExternalApiNetworkInterfaceTestCase(ExternalApiTestCase):
    def test_get_network_interface(self):
        net = self.mock_etcd.create_network('barrynet')
        nd = self.mock_etcd.generate_netdesc(net.uuid)
        self.mock_etcd.create_network_interface(
            uuid='88888888-1234-4321-1234-000000000001',
            netdesc=nd,
            instance_uuid='9999999-1234-4321-1234-000000000001')

        # Get NetworkInterface
        resp = self.client.get('/networks/barrynet/interfaces',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('88888888-1234-4321-1234-000000000001',
                         resp.get_json()[0].get('uuid'))


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
            ETCD_HOST: str = '127.0.0.1'

        fake_config = FakeConfig()

        self.config = mock.patch('shakenfist.config.config', fake_config)
        self.mock_config = self.config.start()

        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.etcd.enqueue')
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
                                  headers={'Authorization': self.auth_token},
                                  data=json.dumps({
                                      'confirm': True,
                                      'namespace': 'system'
                                  }))
        self.assertEqual(['6a973b82-31b3-4780-93e4-04d99ae49f3f',
                          '847b0327-9b17-4148-b4ed-be72b6722c17'],
                         resp.get_json())
        self.assertEqual(200, resp.status_code)

    def test_post_instance_no_disk(self):
        resp = self.client.post('/instances',
                                headers={'Authorization': self.auth_token},
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
                                headers={'Authorization': self.auth_token},
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

    @mock.patch('shakenfist.artifact.Artifact.from_url')
    def test_post_instance_invalid_network(self, mock_get_artifact):
        resp = self.client.post('/instances',
                                headers={'Authorization': self.auth_token},
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

    @mock.patch('shakenfist.artifact.Artifact.from_url')
    def test_post_instance_invalid_network_uuid(self, mock_get_artifact):
        resp = self.client.post('/instances',
                                headers={'Authorization': self.auth_token},
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

    @mock.patch('shakenfist.artifact.Artifact.from_url')
    @mock.patch('shakenfist.network.Network._db_get_attribute',
                return_value={'value': dbo.STATE_CREATED, 'update_time': 2})
    @mock.patch('shakenfist.network.Network.from_db',
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
            self, mock_ipmanager, mock_lock, mock_net, mock_net_attribute,
            mock_get_artifact):
        with mock.patch('shakenfist.db.get_namespace',
                        return_value={
                            'service_key': 'foo',
                            'keys': {
                                'key1': str(base64.b64encode(_encode_key('bar')))
                            }
                        }):
            resp = self.client.post(
                '/auth', data=json.dumps({'namespace': 'banana', 'key': 'foo'}))
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

    def test_post_instance_specific_ip(self):
        self.mock_etcd.create_network('betsy', netblock='10.1.2.0/24',
                                      namespace='two')

        # Request in range IP address
        resp = self.client.post(
            '/instances',
            headers={'Authorization': self.auth_token_two},
            data=json.dumps({
                'name': 'test-instance',
                'cpus': 1,
                'memory': 1024,
                'network': [{'network_uuid': 'betsy',
                             'address': '10.1.2.11'}],
                'disk': [{'size': 8,
                          'base': 'cirros'}],
                'namespace': 'two',
            }))
        self.assertEqual(200, resp.status_code)

        # Request out of range IP address
        resp = self.client.post(
            '/instances',
            headers={'Authorization': self.auth_token_two},
            data=json.dumps({
                'name': 'test-instance',
                'cpus': 1,
                'memory': 1024,
                'network': [{'network_uuid': 'betsy',
                             'address': '10.1.200.11'}],
                'disk': [{'size': 8,
                          'base': 'cirros'}],
                'namespace': 'two',
            }))
        self.assertEqual(400, resp.status_code)

        # Check that instance create API catches duplicate network names
        self.mock_etcd.create_network('betsy', netblock='10.1.3.0/24',
                                      namespace='two')
        resp = self.client.post(
            '/instances',
            headers={'Authorization': self.auth_token_two},
            data=json.dumps({
                'name': 'test-instance',
                'cpus': 1,
                'memory': 1024,
                'network': [{'network_uuid': 'betsy',
                             'address': '10.1.2.11'}],
                'disk': [{'size': 8,
                          'base': 'cirros'}],
                'namespace': 'two',
            }))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            'multiple networks have the name "betsy" in namespace "None"',
            resp.get_json().get('error'))


class ExternalApiNetworkTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(ExternalApiNetworkTestCase, self).setUp()

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
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.1',
            LOG_METHOD_TRACE=1,
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=True
        )
        self.config = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.get_namespace = mock.patch('shakenfist.db.get_namespace')
        self.mock_get_namespace = self.get_namespace.start()
        self.addCleanup(self.get_namespace.stop)

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        # Make a fake auth token
        with mock.patch('shakenfist.db.get_namespace',
                        return_value={
                            'service_key': 'foo',
                            'keys': {
                                'key1': str(base64.b64encode(_encode_key('bar')))
                            }
                        }):
            resp = self.client.post(
                '/auth', data=json.dumps({'namespace': 'system', 'key': 'foo'}))
            self.assertEqual(200, resp.status_code)
            self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    @mock.patch('shakenfist.ipmanager.IPManager.from_db')
    @mock.patch('shakenfist.network.Network.from_db',
                return_value=FakeNetwork(
                    uuid='30f6da44-look-i-am-uuid',
                    vxid=1,
                    namespace='nonespace',
                    name='bob',
                    netblock='10.10.0.0/24'
                ))
    @mock.patch('shakenfist.network.Networks',
                return_value=[FakeNetwork(
                    uuid='30f6da44-look-i-am-uuid',
                    vxid=1,
                    namespace='nonespace',
                    name='bob',
                    netblock='10.10.0.0/24'
                )])
    @mock.patch('shakenfist.ipmanager.IPManager.from_db',
                return_value=IPManager('uuid', '10.0.0.0/24'))
    @mock.patch('shakenfist.network.Network.remove_dhcp')
    @mock.patch('shakenfist.network.Network.delete_on_network_node')
    @mock.patch('shakenfist.network.Network.delete_on_hypervisor')
    @mock.patch('shakenfist.network.Network.state')
    @mock.patch('shakenfist.network.Network.networkinterfaces',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.etcd.enqueue')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_all_networks(self,
                                 mock_db_get_lock,
                                 mock_etcd_enqueue,
                                 mock_etcd_put,
                                 mock_network_interfaces,
                                 mock_network_state,
                                 mock_delete_on_hypervisor,
                                 mock_delete_on_network_node,
                                 mock_remove_dhcp,
                                 mock_get_ipmanager,
                                 mock_db_get_networks,
                                 mock_db_get_network,
                                 mock_ipmanager_from_db):
        mock_network_interfaces.return_value = []

        self.client = external_api.app.test_client()
        resp = self.client.delete('/networks',
                                  headers={'Authorization': self.auth_token},
                                  data=json.dumps({
                                      'confirm': True,
                                      'namespace': 'foo'
                                  }))
        self.assertEqual(['30f6da44-look-i-am-uuid'], resp.get_json())
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.network.Network.from_db',
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
    @mock.patch('shakenfist.ipmanager.IPManager.from_db',
                return_value=IPManager('uuid', '10.0.0.0/24'))
    @mock.patch('shakenfist.network.Network.remove_dhcp')
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.db.get_lock')
    def test_delete_all_networks_none_to_delete(self,
                                                mock_db_get_lock,
                                                mock_etcd_put,
                                                mock_remove_dhcp,
                                                mock_get_ipmanager,
                                                mock_db_get_networks,
                                                mock_db_get_network):
        resp = self.client.delete('/networks',
                                  headers={'Authorization': self.auth_token},
                                  data=json.dumps({
                                      'confirm': True,
                                      'namespace': 'foo'
                                  }))
        self.assertEqual([], resp.get_json())


class ExternalApiNoNamespaceMockTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(ExternalApiNoNamespaceMockTestCase, self).setUp()

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

        self.config = mock.patch('shakenfist.instance.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        # Make a fake auth token
        with mock.patch('shakenfist.db.get_namespace',
                        return_value={
                            'service_key': 'foo',
                            'keys': {
                                'key1': str(base64.b64encode(_encode_key('bar')))
                            }
                        }):
            resp = self.client.post(
                '/auth', data=json.dumps({'namespace': 'system', 'key': 'foo'}))
            self.assertEqual(200, resp.status_code)
            self.auth_token = 'Bearer %s' % resp.get_json()[
                'access_token']

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.get_namespace',
                return_value={
                    'service_key': 'foo',
                    'keys': {
                        'mykey': str(base64.b64encode(_encode_key('bar')))
                    }
                })
    @mock.patch('shakenfist.etcd.put')
    def test_delete_namespace_key(self, mock_put, mock_get, mock_lock):
        resp = self.client.delete('/auth/namespaces/system/keys/mykey',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        mock_put.assert_called_with('namespace', None, 'system',
                                    {'service_key': 'foo', 'keys': {}})

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.etcd.get', return_value=None)
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('bcrypt.hashpw', return_value='terminator'.encode('utf-8'))
    def test_auth_add_key_new_namespace(self, mock_hashpw, mock_put, mock_get, mock_lock):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
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
