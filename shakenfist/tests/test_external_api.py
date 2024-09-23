import base64
import bcrypt
import json
import logging
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo, State
from shakenfist.config import config, BaseSettings, SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


class FakeScheduler:
    def find_candidates(self, *args, **kwargs):
        return config.NODE_NAME


class BaseFakeObject:
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
        super().__init__(state)

        self.uuid = uuid
        self.namespace = namespace
        self.power_state = {'power_state': power_state}
        self.placement = {'node': placement}
        self.version = 2
        self.interfaces = []

    def add_event(self, eventtype, message, duration=None, extra=None,
                  suppress_event_logging=False, log_as_error=False):
        ...


class FakeNetwork(BaseFakeObject):
    object_type = 'network'

    def __init__(self, uuid=None, vxid=None, namespace=None,
                 name=None, netblock=None, state=dbo.STATE_CREATED):
        super().__init__(state)
        self.uuid = uuid
        self.vxid = vxid
        self.namespace = namespace
        self.name = name
        self.netblock = netblock
        self.version = 2
        self.provide_nat = True

    def is_dead(self):
        return False

    def remove_dnsmasq(self):
        pass

    def networkinterfaces(self):
        return []


def _encode_key(key):
    return str(base64.b64encode(bcrypt.hashpw(
               key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')


class ExternalApiTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.recorded_op = mock.patch('shakenfist.util.general.RecordedOperation')
        self.recorded_op.start()
        self.addCleanup(self.recorded_op.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.scheduler = mock.patch('shakenfist.scheduler.Scheduler', FakeScheduler)
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


class ExternalApiGeneralTestCase(ExternalApiTestCase):
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

    def test_get_root(self):
        resp = self.client.get('/')
        self.assertTrue('Shaken Fist REST API service' in
                        resp.get_data().decode('utf-8'))
        self.assertEqual(200, resp.status_code)
        self.assertEqual('text/html; charset=utf-8', resp.content_type)

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
                '/sf/attribute/instance/12345678-1234-4321-1234-000000000001/metadata']))

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
                '/sf/attribute/instance/12345678-1234-4321-1234-000000000001/metadata']))

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
                '/sf/attribute/network/12345678-1234-4321-1234-000000000001/metadata']))

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
                '/sf/attribute/network/12345678-1234-4321-1234-000000000001/metadata']))

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
                '/sf/attribute/instance/12345678-1234-4321-1234-000000000001/metadata']))

    def test_delete_instance_metadata_bad_key(self):
        # We now just silently ignore deletes of things which don't exist
        self.mock_etcd.create_instance(
            'banana', metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/instances/12345678-1234-4321-1234-000000000001/metadata/wrong',
            headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)

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
                '/sf/attribute/network/12345678-1234-4321-1234-000000000001/metadata']))

    def test_delete_network_metadata_bad_key(self):
        # We now just silently ignore deletes of things which don't exist
        self.mock_etcd.create_network('banana', namespace='system',
                                      metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/networks/12345678-1234-4321-1234-000000000001/metadata/wrong',
            headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)


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
        super().setUp()

        def fake_virt_from_db(uuid):
            return {'uuid': uuid}

        self.virt_from_db = mock.patch('shakenfist.instance.Instance.from_db',
                                       fake_virt_from_db)
        self.mock_virt_from_db = self.virt_from_db.start()
        self.addCleanup(self.virt_from_db.stop)

        class FakeConfig(BaseSettings):
            API_ASYNC_WAIT: int = 1
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
    @mock.patch('shakenfist.etcd.get_lock')
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
            'multiple networks have the name "betsy" in namespace "two"',
            resp.get_json().get('error'))
