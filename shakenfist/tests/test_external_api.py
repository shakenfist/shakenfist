import base64
import json
import logging
import sys
from unittest import mock
from uuid import uuid4

import bcrypt

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import NoopLock
from shakenfist.baseobject import State
from shakenfist.config import BaseSettings
from shakenfist.config import SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.schema.object_types import ObjectType
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
        return ['a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d']


class BaseFakeObject:
    def __init__(self, state=None):
        self._state = state

    @property
    def state(self):
        if isinstance(self._state, list):
            s = self._state[0]
            self._state = self._state[1:]
            return State(value=s, update_time=1)
        else:
            return State(value=self._state, update_time=1)

    @state.setter
    def state(self, state):
        self._state = state

    def unique_label(self):
        return ('instance', self.uuid)

    def delete(self):
        pass


class FakeInstance(BaseFakeObject):
    object_type = ObjectType.INSTANCE

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
        self.last_cluster_operation = None

    def add_event(self, eventtype, message, duration=None, extra=None,
                  suppress_event_logging=False, log_as_error=False):
        ...

    def _set_last_cluster_operation(self, op_type, op_uuid):
        self.last_cluster_operation = (op_type, op_uuid)

    def get_lock_attr(self, name, op, global_scope=True, timeout=10):
        return NoopLock()

    def enqueue_delete(self):
        ...


class FakeNetwork(BaseFakeObject):
    object_type = ObjectType.NETWORK

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

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='node1',
            NODE_UUID='a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
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

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='node1',
            NODE_UUID='a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
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
        self.assertEqual('12345678-1234-4321-8234-000000000001',
                         resp.get_json().get('uuid'))

        resp = self.client.get('/instances/bob',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-8234-000000000003',
                         resp.get_json().get('uuid'))

        # Instance by name - WRONG
        resp = self.client.get('/instances/bazza',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)

        # Instance by UUID
        resp = self.client.get('/instances/12345678-1234-4321-8234-000000000003',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-8234-000000000003',
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
        self.assertEqual('12345678-1234-4321-8234-000000000002',
                         resp.get_json().get('uuid'))

        resp = self.client.get('/instances/bob',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-8234-000000000003',
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
            '/instances/12345678-1234-4321-8234-000000000001/metadata',
            headers={'Authorization': self.auth_token})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual(200, resp.status_code)

    def test_put_instance_metadata(self):
        self.mock_etcd.create_instance('banana')
        resp = self.client.put(
            '/instances/12345678-1234-4321-8234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'key': 'foo',
                'value': 'bar'
            }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            self.mock_etcd.object_metadata[
                'instance/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_post_instance_metadata(self):
        self.mock_etcd.create_instance('banana')
        resp = self.client.post(
            '/instances/12345678-1234-4321-8234-000000000001/metadata',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'key': 'foo',
                'value': 'bar'
            }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            self.mock_etcd.object_metadata[
                'instance/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_get_network(self):
        self.mock_etcd.create_network('barry')
        self.mock_etcd.create_network('alice')
        self.mock_etcd.create_network('bob')

        # Instance by name
        resp = self.client.get('/networks/barry',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-8234-000000000001',
                         resp.get_json().get('uuid'))

        resp = self.client.get('/networks/bob',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-8234-000000000003',
                         resp.get_json().get('uuid'))

        # Instance by name - WRONG
        resp = self.client.get('/networks/bazza',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)

        # Instance by UUID
        resp = self.client.get('/networks/12345678-1234-4321-8234-000000000001',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual('12345678-1234-4321-8234-000000000001',
                         resp.get_json().get('uuid'))

        # Instance by UUID - WRONG
        resp = self.client.get('/networks/12345678-1234-4321-8234-111111111111',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)

    def test_get_network_metadata(self):
        self.mock_etcd.create_network('banana', namespace='foo',
                                      metadata={'a': 'a', 'b': 'b'})
        resp = self.client.get(
            '/networks/12345678-1234-4321-8234-000000000001/metadata',
            headers={'Authorization': self.auth_token})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    def test_put_network_metadata(self):
        self.mock_etcd.create_network('banana', namespace='foo')
        resp = self.client.put(
            '/networks/12345678-1234-4321-8234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'key': 'foo',
                'value': 'bar'
            }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            self.mock_etcd.object_metadata[
                'network/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_post_network_metadata(self):
        self.mock_etcd.create_network('banana', namespace='foo')
        resp = self.client.post(
            '/networks/12345678-1234-4321-8234-000000000001/metadata',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'key': 'foo',
                'value': 'bar'
            }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            self.mock_etcd.object_metadata[
                'network/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_delete_instance_metadata(self):
        self.mock_etcd.create_instance('banana',
                                       metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/instances/12345678-1234-4321-8234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(None, resp.get_json())
        self.assertEqual(
            {'real': 'smart'},
            self.mock_etcd.object_metadata[
                'instance/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_delete_instance_metadata_bad_key(self):
        # We now just silently ignore deletes of things which don't exist
        self.mock_etcd.create_instance(
            'banana', metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/instances/12345678-1234-4321-8234-000000000001/metadata/wrong',
            headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)

    def test_delete_network_metadata(self):
        self.mock_etcd.create_network('banana', namespace='foo',
                                      metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/networks/12345678-1234-4321-8234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'real': 'smart'},
            self.mock_etcd.object_metadata[
                'network/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_delete_network_metadata_bad_key(self):
        # We now just silently ignore deletes of things which don't exist
        self.mock_etcd.create_network('banana', namespace='system',
                                      metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/networks/12345678-1234-4321-8234-000000000001/metadata/wrong',
            headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)


class ExternalApiNetworkInterfaceTestCase(ExternalApiTestCase):
    def test_get_network_interface(self):
        id1 = str(uuid4())
        id2 = str(uuid4())

        net = self.mock_etcd.create_network('barrynet')
        nd = self.mock_etcd.generate_netdesc(net.uuid)
        self.mock_etcd.create_network_interface(
            uuid=id1,
            netdesc=nd,
            instance_uuid=id2)

        # Get NetworkInterface
        resp = self.client.get('/networks/barrynet/interfaces',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual(id1, resp.get_json()[0].get('uuid'))


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

    @mock.patch('shakenfist.mariadb.enqueue_work_item')
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
    @mock.patch('shakenfist.locks.ClusterLock')
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


class ExternalApiExceptionRecordingTestCase(ExternalApiTestCase):
    """Test that exceptions during JSON serialization are recorded."""

    def test_json_serialization_error_recorded(self):
        """Test that UUID serialization errors are caught by the signal handler.

        Flask-RESTful has its own error handling that bypasses Flask's
        @app.errorhandler decorator. We use the got_request_exception signal
        to record exceptions that occur during JSON response serialization.
        """
        from uuid import UUID

        self.mock_etcd.create_instance('barry')

        # Mock external_view to return an unserializable UUID
        def bad_external_view(namespace=None, **kwargs):
            return {'uuid': UUID('12345678-1234-5678-1234-567812345678')}

        # Disable testing/debug mode temporarily so exceptions don't propagate
        # and we can verify the signal handler is called
        external_api.app.testing = False
        external_api.app.debug = False
        external_api.app.config['PROPAGATE_EXCEPTIONS'] = False

        try:
            with mock.patch('shakenfist.instance.Instance.external_view',
                            bad_external_view):
                # Make request that will fail during JSON serialization
                resp = self.client.get(
                    '/instances/barry',
                    headers={'Authorization': self.auth_token})

                # The response should be a 500 error
                self.assertEqual(500, resp.status_code)

                # Verify that record_exception was called via the signal
                # The mock is from base.ShakenFistTestCase
                self.mock_record_exception.assert_called()
        finally:
            # Restore testing mode
            external_api.app.testing = True


class ExternalApiInstanceDiskLoopTestCase(ExternalApiInstanceTestCase):
    """Tests for the disk-loop artifact-fetch logic in InstancesEndpoint.post.

    Phase 3b refactored the disk loop to call Artifact.from_url eagerly
    (with create_if_new=True) and pass artifact_uuid into afo_create_and_enqueue,
    and to build instance_start_dependencies passed as depends_on to
    nino_create_and_enqueue.  These tests verify that contract.
    """

    # Valid UUID4 values (version nibble = 4).
    ARTIFACT_UUID = '11111111-2222-4333-8444-555555555555'
    BLOB_UUID = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    FETCH_OP_UUID_1 = 'f1111111-2222-4333-8444-000000000001'
    FETCH_OP_UUID_2 = 'f2222222-3333-4444-8555-000000000002'

    def _fake_artifact(self):
        """Return a minimal mock artifact with a uuid and add_event."""
        a = mock.MagicMock()
        a.uuid = self.ARTIFACT_UUID
        return a

    @mock.patch(
        'shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch(
        'shakenfist.external_api.instance.afo_create_and_enqueue')
    @mock.patch(
        'shakenfist.external_api.instance.Artifact.from_url')
    def test_post_instance_disk_loop_enqueues_artifact_fetch(
            self, mock_from_url, mock_afo, mock_nino):
        """A disk with a plain URL triggers afo_create_and_enqueue with
        the artifact UUID and nino_create_and_enqueue with a depends_on list."""
        fake_artifact = self._fake_artifact()
        mock_from_url.return_value = fake_artifact

        fetch_op_uuid = self.FETCH_OP_UUID_1
        from shakenfist.schema.object_types import ObjectType as _OT
        mock_afo.return_value = (_OT.ARTIFACT_FETCH_OP, fetch_op_uuid)
        mock_nino.return_value = (_OT.NODE_INST_NETDESC_OP, str(uuid4()))

        resp = self.client.post(
            '/instances',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'name': 'test-disk-loop',
                'cpus': 1,
                'memory': 1024,
                'network': [],
                'disk': [{'size': 8, 'base': 'https://example.com/img.qcow2'}],
                'namespace': 'system',
            }))
        self.assertEqual(200, resp.status_code, resp.get_json())

        # Artifact.from_url must have been called at least once with
        # create_if_new=True for the given URL.
        url_arg = 'https://example.com/img.qcow2'
        from_url_calls = mock_from_url.call_args_list
        create_if_new_calls = [
            c for c in from_url_calls
            if c.kwargs.get('create_if_new') is True
            or (len(c.args) > 1 and url_arg in c.args)
        ]
        self.assertTrue(
            len(create_if_new_calls) > 0,
            'Artifact.from_url was not called with create_if_new=True')

        # afo_create_and_enqueue must have been called with artifact_uuid
        # matching the resolved artifact's UUID.
        mock_afo.assert_called()
        afo_kwargs = mock_afo.call_args.kwargs
        self.assertEqual(str(afo_kwargs.get('artifact_uuid')),
                         str(self.ARTIFACT_UUID))

        # nino_create_and_enqueue must have been called with a non-None
        # depends_on list that references the fetch operation.
        mock_nino.assert_called()
        nino_kwargs = mock_nino.call_args.kwargs
        depends_on = nino_kwargs.get('depends_on')
        self.assertIsNotNone(depends_on,
                             'depends_on must not be None when there is a fetch op')
        self.assertTrue(
            len(depends_on) > 0,
            'depends_on must contain at least one dependency')
        dep = depends_on[0]
        self.assertEqual(str(dep.op_uuid), fetch_op_uuid)

    @mock.patch(
        'shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch(
        'shakenfist.external_api.instance.afo_create_and_enqueue')
    @mock.patch(
        'shakenfist.external_api.instance.Artifact.from_url')
    def test_post_instance_disk_loop_blob_uuid_branch(
            self, mock_from_url, mock_afo, mock_nino):
        """A disk with blob_uuid triggers Artifact.from_url with the BLOB_URL
        prefix in the artifact-fetch loop."""
        fake_artifact = self._fake_artifact()
        mock_from_url.return_value = fake_artifact

        from shakenfist.schema.object_types import ObjectType as _OT
        mock_afo.return_value = (
            _OT.ARTIFACT_FETCH_OP, self.FETCH_OP_UUID_2)
        mock_nino.return_value = (_OT.NODE_INST_NETDESC_OP, str(uuid4()))

        resp = self.client.post(
            '/instances',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'name': 'test-blob-uuid',
                'cpus': 1,
                'memory': 1024,
                'network': [],
                'disk': [{'size': 8, 'blob_uuid': self.BLOB_UUID}],
                'namespace': 'system',
            }))
        self.assertEqual(200, resp.status_code, resp.get_json())

        # Artifact.from_url must be called with a URL that includes BLOB_URL
        # prefix followed by the blob UUID.
        from shakenfist.artifact import BLOB_URL
        expected_url = f'{BLOB_URL}{self.BLOB_UUID}'
        from_url_urls = [
            c.args[1] if len(c.args) > 1 else c.kwargs.get('url', '')
            for c in mock_from_url.call_args_list
        ]
        self.assertIn(
            expected_url, from_url_urls,
            f'Expected {expected_url!r} in from_url calls, got {from_url_urls!r}')

    @mock.patch(
        'shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch(
        'shakenfist.external_api.instance.afo_create_and_enqueue')
    def test_post_instance_no_disks_skips_artifact_fetch(
            self, mock_afo, mock_nino):
        """A disk with no base and no blob_uuid skips afo_create_and_enqueue
        and passes depends_on=None to nino_create_and_enqueue."""
        from shakenfist.schema.object_types import ObjectType as _OT
        mock_nino.return_value = (_OT.NODE_INST_NETDESC_OP, str(uuid4()))

        resp = self.client.post(
            '/instances',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'name': 'test-no-fetch',
                'cpus': 1,
                'memory': 1024,
                'network': [],
                # Disk with a size but no base or blob_uuid — empty disk.
                'disk': [{'size': 8}],
                'namespace': 'system',
            }))
        self.assertEqual(200, resp.status_code, resp.get_json())

        # No artifact fetch operation should have been enqueued.
        mock_afo.assert_not_called()

        # nino_create_and_enqueue must have been called with depends_on=None.
        mock_nino.assert_called()
        nino_kwargs = mock_nino.call_args.kwargs
        self.assertIsNone(
            nino_kwargs.get('depends_on'),
            'depends_on must be None when there are no fetch dependencies')
