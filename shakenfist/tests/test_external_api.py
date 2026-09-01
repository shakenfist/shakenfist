import base64
import json
import logging
import sys
from unittest import mock
from uuid import uuid4

import bcrypt
from shakenfist_utilities import api as sf_api

from shakenfist.artifact import Artifact
from shakenfist.artifact import BLOB_URL
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import NoopLock
from shakenfist.baseobject import State
from shakenfist import exceptions
from shakenfist.config import BaseSettings
from shakenfist.config import SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

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
        )
        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_mariadb.create_namespace('two', 'key1', 'space')
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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

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
        )
        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_mariadb.create_namespace('two', 'key1', 'space')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'two', 'key': 'space'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token_two = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_mariadb.create_namespace('three', 'key1', 'pass')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'three', 'key': 'pass'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token_three = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

    def test_get_root(self):
        resp = self.client.get('/')
        self.assertTrue('Shaken Fist REST API service' in
                        resp.get_data().decode('utf-8'))
        self.assertEqual(200, resp.status_code)
        self.assertEqual('text/html; charset=utf-8', resp.content_type)

    def test_get_instance(self):
        self.mock_mariadb.create_instance('barry')
        self.mock_mariadb.create_instance('alice')
        self.mock_mariadb.create_instance('bob')

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
        self.mock_mariadb.create_instance('barry')
        self.mock_mariadb.create_instance('barry', namespace='two')
        self.mock_mariadb.create_instance('bob', namespace='two')

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
        self.mock_mariadb.create_instance('banana', metadata={'a': 'a', 'b': 'b'})
        resp = self.client.get(
            '/instances/12345678-1234-4321-8234-000000000001/metadata',
            headers={'Authorization': self.auth_token})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual('application/json', resp.content_type)
        self.assertEqual(200, resp.status_code)

    def test_put_instance_metadata(self):
        self.mock_mariadb.create_instance('banana')
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
            self.mock_mariadb.object_metadata[
                'instance/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_post_instance_metadata(self):
        self.mock_mariadb.create_instance('banana')
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
            self.mock_mariadb.object_metadata[
                'instance/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_get_network(self):
        self.mock_mariadb.create_network('barry')
        self.mock_mariadb.create_network('alice')
        self.mock_mariadb.create_network('bob')

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
        self.mock_mariadb.create_network('banana', namespace='foo',
                                         metadata={'a': 'a', 'b': 'b'})
        resp = self.client.get(
            '/networks/12345678-1234-4321-8234-000000000001/metadata',
            headers={'Authorization': self.auth_token})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    def test_put_network_metadata(self):
        self.mock_mariadb.create_network('banana', namespace='foo')
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
            self.mock_mariadb.object_metadata[
                'network/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_post_network_metadata(self):
        self.mock_mariadb.create_network('banana', namespace='foo')
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
            self.mock_mariadb.object_metadata[
                'network/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_delete_instance_metadata(self):
        self.mock_mariadb.create_instance('banana',
                                          metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/instances/12345678-1234-4321-8234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(None, resp.get_json())
        self.assertEqual(
            {'real': 'smart'},
            self.mock_mariadb.object_metadata[
                'instance/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_delete_instance_metadata_bad_key(self):
        # We now just silently ignore deletes of things which don't exist
        self.mock_mariadb.create_instance(
            'banana', metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/instances/12345678-1234-4321-8234-000000000001/metadata/wrong',
            headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)

    def test_delete_network_metadata(self):
        self.mock_mariadb.create_network('banana', namespace='foo',
                                         metadata={'foo': 'bar', 'real': 'smart'})
        resp = self.client.delete(
            '/networks/12345678-1234-4321-8234-000000000001/metadata/foo',
            headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'real': 'smart'},
            self.mock_mariadb.object_metadata[
                'network/12345678-1234-4321-8234-000000000001']['metadata'])

    def test_delete_network_metadata_bad_key(self):
        # We now just silently ignore deletes of things which don't exist
        self.mock_mariadb.create_network('banana', namespace='system',
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

        net = self.mock_mariadb.create_network('barrynet')
        nd = self.mock_mariadb.generate_netdesc(net.uuid)
        self.mock_mariadb.create_network_interface(
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
    @mock.patch('shakenfist.locks.ClusterLock')
    def test_delete_all_instances(
            self, mock_db_get_lock,
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
        self.mock_mariadb.create_network('betsy', netblock='10.1.2.0/24',
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
        self.mock_mariadb.create_network('betsy', netblock='10.1.3.0/24',
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


class ExternalApiCreateAdmissionWalkTestCase(ExternalApiTestCase):
    """The create path claims capacity by walking the candidate list (D7).

    ``find_candidates()`` filters against a metrics snapshot up to a
    minute stale, so its ordered list is a preference. The decision is
    the guarded capacity claim inside ``place_instance()``, and a
    refusal there means another create took the slot in between -- so
    the create walks on rather than failing.
    """

    NODE_A = 'aaaaaaaa-1111-4111-8111-111111111111'
    NODE_B = 'bbbbbbbb-2222-4222-8222-222222222222'
    NODE_C = 'cccccccc-3333-4333-8333-333333333333'

    def _candidates(self, *nodes):
        fake = mock.MagicMock()
        fake.find_candidates.return_value = list(nodes)
        return mock.patch(
            'shakenfist.external_api.instance.SCHEDULER', fake)

    def _full(self, node):
        # Every limit at zero, so any request at all is refused.
        self.mock_mariadb.set_node_capacity(node)

    def _roomy(self, node):
        self.mock_mariadb.set_node_capacity(
            node, limit_cpus=16, limit_memory_mb=65536, limit_disk_gb=500)

    def _post(self, name, cpus=1):
        # A sizeless-base disk, so no artifact fetch is enqueued and the
        # create is entirely about placement.
        return self.client.post(
            '/instances',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'name': name,
                'cpus': cpus,
                'memory': 1024,
                'network': [],
                'disk': [{'size': 8}],
                'namespace': 'system',
            }))

    def test_a_denied_candidate_is_skipped_for_the_next(self):
        self._full(self.NODE_A)
        self._roomy(self.NODE_B)

        with self._candidates(self.NODE_A, self.NODE_B):
            resp = self._post('walks-on')

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(self.NODE_B, resp.get_json()['node'])
        # ... and only the admitting node was charged.
        self.assertEqual(
            0, self.mock_mariadb.node_capacity[self.NODE_A]['used_cpus'])
        self.assertEqual(
            1, self.mock_mariadb.node_capacity[self.NODE_B]['used_cpus'])

    def test_a_node_with_no_capacity_row_still_admits(self):
        # P7: mid-upgrade a node the reconciler has not sized admits
        # unguarded rather than the cluster refusing every create.
        self._full(self.NODE_A)

        with self._candidates(self.NODE_A, self.NODE_C):
            resp = self._post('unguarded')

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(self.NODE_C, resp.get_json()['node'])

    def test_every_candidate_denied_is_a_507(self):
        self._full(self.NODE_A)
        self._full(self.NODE_B)

        with self._candidates(self.NODE_A, self.NODE_B):
            resp = self._post('nowhere-to-go')

        self.assertEqual(507, resp.status_code, resp.get_json())
        self.assertIn('2 candidates refused it', resp.get_json()['error'])

    def test_demand_only_refusals_are_waived(self):
        # The D13 demand feedforward spreads bursts across nodes; when
        # no candidate admits and the refusals were on demand alone, the
        # walk retries with the clause waived rather than 507ing a
        # cluster with free real capacity (the smoke CI single-node
        # lockout of 2026-08-14).
        self.mock_mariadb.set_node_capacity(
            self.NODE_A, limit_cpus=16, limit_memory_mb=65536,
            limit_disk_gb=500, expected_demand=9.2, demand_limit=6.0)

        with self._candidates(self.NODE_A):
            resp = self._post('demand-waived')

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(self.NODE_A, resp.get_json()['node'])
        # The waived admission still drew down real capacity and still
        # accumulated its demand contribution.
        row = self.mock_mariadb.node_capacity[self.NODE_A]
        self.assertEqual(1, row['used_cpus'])
        self.assertLess(9.2, row['expected_demand'])

    def test_an_idle_node_admits_a_large_instance_on_the_first_walk(self):
        # Issue #3813 as the walkers see it, rather than as the SQL
        # clause sees it. A CI-sized hypervisor -- two schedulable
        # threads, so a demand budget of 0.75 x 2 = 1.5 -- sitting
        # completely idle must admit an 8-vCPU create on the *first*
        # walk, with no waiver. The clause compares the node's existing
        # demand against that budget and ignores the size of the
        # placement asking; before phase 4a the placement's charge
        # (8 x 0.6 = 4.8) went on the left-hand side, so this refused
        # and only the P9 waiver rescued it -- a second walk, and a
        # spreader that never spread, for a node that was never busy.
        self.mock_mariadb.set_node_capacity(
            self.NODE_A, limit_cpus=16, limit_memory_mb=65536,
            limit_disk_gb=500, expected_demand=0.0, demand_limit=1.5)

        with mock.patch('shakenfist.instance.Instance.add_event') as events:
            with self._candidates(self.NODE_A):
                resp = self._post('idle-node-large-instance', cpus=8)

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(self.NODE_A, resp.get_json()['node'])
        self.assertNotIn(
            'waiving demand guard',
            ' '.join(str(c) for c in events.call_args_list))
        # The charge still landed, so the next create sees a node over
        # budget and spreads.
        row = self.mock_mariadb.node_capacity[self.NODE_A]
        self.assertEqual(8, row['used_cpus'])
        self.assertAlmostEqual(4.8, row['expected_demand'])
        self.assertLess(row['demand_limit'], row['expected_demand'])

    def test_the_waiver_reaches_past_a_genuinely_full_node(self):
        # A mixed exhaustion -- one node full on real capacity, another
        # refused on demand alone -- must also re-walk: the demand-hot
        # node has free real capacity, and pre-D13 it would have
        # admitted this create.
        self._full(self.NODE_A)
        self.mock_mariadb.set_node_capacity(
            self.NODE_B, limit_cpus=16, limit_memory_mb=65536,
            limit_disk_gb=500, expected_demand=9.2, demand_limit=6.0)

        with self._candidates(self.NODE_A, self.NODE_B):
            resp = self._post('waived-mixed')

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(self.NODE_B, resp.get_json()['node'])
        self.assertEqual(
            0, self.mock_mariadb.node_capacity[self.NODE_A]['used_cpus'])

    def test_real_capacity_exhaustion_is_still_a_507(self):
        # The waiver frees only the demand clause. Nodes full on a real
        # dimension stay full through the second pass.
        self._full(self.NODE_A)
        self._full(self.NODE_B)
        self.mock_mariadb.node_capacity[self.NODE_A]['demand_limit'] = 6.0
        self.mock_mariadb.node_capacity[self.NODE_A]['expected_demand'] = 9.2

        with self._candidates(self.NODE_A, self.NODE_B):
            resp = self._post('still-full')

        self.assertEqual(507, resp.status_code, resp.get_json())

    def test_a_database_failure_is_not_a_full_cluster(self):
        # A WriteException means the database could not be reached, not
        # that the cluster is full: asking the next node would only get
        # the same answer, so it must not be caught by the walk.
        with self._candidates(self.NODE_A, self.NODE_B):
            with mock.patch(
                    'shakenfist.instance.Instance.place_instance',
                    side_effect=exceptions.WriteException('database gone')):
                resp = self._post('database-down')

        self.assertEqual(500, resp.status_code)


class ExternalApiAffinityRefusalTestCase(
        ExternalApiCreateAdmissionWalkTestCase):
    """The create path answers 409 for an unsatisfiable hard affinity.

    ``AffinityConstraintUnsatisfiable`` is a *subclass* of
    ``LowResourceException``, chosen so that preflight's redirect keeps
    working unchanged. Python matches ``except`` clauses in source
    order, so the only thing standing between a 409 and a 507 is that
    the subclass clause sits above the parent one in
    ``external_api/instance.py``. That is invisible to a scheduler-level
    test: one asserting the exception type passes identically whichever
    clause caught it. These tests are here because this is the only
    level at which the ordering is observable.
    """

    def _raises(self, exc):
        fake = mock.MagicMock()
        fake.find_candidates.side_effect = exc
        return mock.patch(
            'shakenfist.external_api.instance.SCHEDULER', fake)

    def test_unsatisfiable_affinity_is_a_409(self):
        constraint = ('no node satisfies require_with_tag=[\'database\'] '
                      'at stage affinity_constraints')

        with self._raises(
                exceptions.AffinityConstraintUnsatisfiable(constraint)):
            resp = self._post('affinity-nowhere')

        self.assertEqual(409, resp.status_code, resp.get_json())
        # The body has to name the constraint, or a 409 is no more
        # actionable than the 507 it replaced.
        self.assertIn('require_with_tag', resp.get_json()['error'])
        self.assertIn('affinity_constraints', resp.get_json()['error'])

    def test_a_real_capacity_refusal_is_still_a_507(self):
        # The other half of the ordering. Reversing the two clauses
        # turns every 409 into a 507 and this test keeps passing, so it
        # is the pair that pins the behaviour, not either one alone.
        with self._raises(exceptions.LowResourceException('cluster is full')):
            resp = self._post('really-full')

        self.assertEqual(507, resp.status_code, resp.get_json())
        self.assertIn('cluster is full', resp.get_json()['error'])

    def test_the_refused_instance_is_deleted(self):
        # The instance exists only because it is created before
        # scheduling runs. A 409 that left it behind would leak one per
        # refused create, and the 507 path has always deleted.
        with mock.patch('shakenfist.instance.Instance.'
                        'enqueue_delete_due_error') as delete:
            with self._raises(
                    exceptions.AffinityConstraintUnsatisfiable('nope')):
                resp = self._post('affinity-cleanup')

        self.assertEqual(409, resp.status_code, resp.get_json())
        delete.assert_called_once_with('scheduling failed')


class ExternalApiExceptionRecordingTestCase(ExternalApiTestCase):
    """Test that exceptions during JSON serialization are recorded."""

    def test_json_serialization_error_recorded(self):
        """Test that UUID serialization errors are caught by the signal handler.

        Flask-RESTful has its own error handling that bypasses Flask's
        @app.errorhandler decorator. We use the got_request_exception signal
        to record exceptions that occur during JSON response serialization.
        """
        from uuid import UUID

        self.mock_mariadb.create_instance('barry')

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

    Phase 3b refactored the disk loop to resolve the artifact eagerly and
    pass artifact_uuid into afo_create_and_enqueue, and to build
    instance_start_dependencies passed as depends_on to
    nino_create_and_enqueue.  These tests verify that contract.

    Resolution is by ownership rather than visibility (#3640), so the
    mocks below stand in for owned_from_url_or_new rather than from_url.
    Which resolver runs is the substance of that change rather than an
    incidental detail: from_url could land the fetch -- and so add_index,
    and so delete_old_versions -- on an artifact belonging to another
    namespace. What a caller may boot from is a wider question than what
    it may write to, and it gets answered separately, in the disk_base
    loop rather than here.
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
        'shakenfist.instance.afo_create_and_enqueue')
    @mock.patch(
        'shakenfist.external_api.instance.Artifact.owned_from_url_or_new')
    def test_post_instance_disk_loop_enqueues_artifact_fetch(
            self, mock_resolve, mock_afo, mock_nino):
        """A disk with a plain URL triggers afo_create_and_enqueue with
        the artifact UUID and nino_create_and_enqueue with a depends_on list."""
        fake_artifact = self._fake_artifact()
        mock_resolve.return_value = fake_artifact

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

        # The fetch loop must have resolved the URL by ownership.
        url_arg = 'https://example.com/img.qcow2'
        resolved_urls = [
            c.args[1] if len(c.args) > 1 else c.kwargs.get('url', '')
            for c in mock_resolve.call_args_list
        ]
        self.assertIn(
            url_arg, resolved_urls,
            f'Expected {url_arg!r} in owned_from_url_or_new calls, '
            f'got {resolved_urls!r}')

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
        'shakenfist.instance.afo_create_and_enqueue')
    @mock.patch(
        'shakenfist.external_api.instance.Artifact.owned_from_url_or_new')
    def test_post_instance_disk_loop_blob_uuid_branch(
            self, mock_resolve, mock_afo, mock_nino):
        """A disk with blob_uuid resolves the BLOB_URL prefixed URL in the
        artifact-fetch loop."""
        fake_artifact = self._fake_artifact()
        mock_resolve.return_value = fake_artifact

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

        # The resolution must be against a URL that includes the BLOB_URL
        # prefix followed by the blob UUID.
        expected_url = f'{BLOB_URL}{self.BLOB_UUID}'
        resolved_urls = [
            c.args[1] if len(c.args) > 1 else c.kwargs.get('url', '')
            for c in mock_resolve.call_args_list
        ]
        self.assertIn(
            expected_url, resolved_urls,
            f'Expected {expected_url!r} in owned_from_url_or_new calls, '
            f'got {resolved_urls!r}')

    @mock.patch(
        'shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch(
        'shakenfist.instance.afo_create_and_enqueue')
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


class ExternalApiInstanceDiskBaseTargetTestCase(ExternalApiInstanceTestCase):
    """Which artifact `disk.base` as a plain URL is allowed to land on.

    Booting from a URL somebody else has already fetched used to resolve
    to *their* artifact, and then enqueue a fetch against it. That fetch
    ends in add_index, which ends in delete_old_versions, so any tenant
    who knew the URL of a shared image could roll the system namespace's
    artifact forward and drop the versions underneath it at a moment of
    their choosing. The operator guide says the opposite: a shared
    artifact is one "non-system namespaces should not be able to
    update".

    The obvious narrowing -- resolve by ownership, full stop -- would
    have broken the feature instead of fixing it. Reuse is the entire
    point of sharing an official image, and an artifact with no versions
    yet is treated by transfer_image as "cluster does not have a copy",
    so every namespace would have downloaded and stored its own copy of
    every shared image.

    So the split is per verb rather than per artifact: a visible foreign
    artifact is something to boot from, by resolving it to a blob the
    way the label and snapshot branches already do, and never something
    to fetch into. Both halves are asserted, because a change which only
    stopped the write would look identical here to one which also
    stopped the reuse.
    """

    FOREIGN_BLOB = 'bbbbbbbb-cccc-4ddd-8eee-ffffffffffff'
    URL = 'https://example.com/shared-image.qcow2'

    def setUp(self):
        super().setUp()

        self.foreign = Artifact.new(
            Artifact.TYPE_IMAGE, self.URL, name='shared-image',
            namespace='system')
        self.foreign.state = Artifact.STATE_CREATED

        # The blob machinery is well beyond what this harness has, and
        # irrelevant to the question: all these tests need is for the
        # foreign artifact to have something bootable in it.
        patcher = mock.patch.object(
            Artifact, 'resolve_to_blob', return_value=self.FOREIGN_BLOB)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create(self, token, namespace, mock_afo, mock_nino):
        mock_afo.return_value = (ObjectType.ARTIFACT_FETCH_OP, str(uuid4()))
        mock_nino.return_value = (
            ObjectType.NODE_INST_NETDESC_OP, str(uuid4()))

        resp = self.client.post(
            '/instances',
            headers={'Authorization': token},
            data=json.dumps({
                'name': 'test-disk-base',
                'cpus': 1,
                'memory': 1024,
                'network': [],
                'disk': [{'size': 8, 'base': self.URL}],
                'namespace': namespace,
            }))
        self.assertEqual(200, resp.status_code, resp.get_json())

        # afo_create_and_enqueue(namespace, url, instance_uuid, ...) -- the
        # url it is handed, and the artifact_uuid alongside it, are the
        # observable answer to "what did this resolve to".
        mock_afo.assert_called()
        return (mock_afo.call_args.args[1],
                str(mock_afo.call_args.kwargs.get('artifact_uuid')))

    def _owned_by(self, namespace):
        return Artifact.owned_from_url(
            Artifact.TYPE_IMAGE, self.URL, namespace=namespace)

    @mock.patch('shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    def test_a_shared_artifact_is_booted_from_and_not_fetched_into(
            self, mock_afo, mock_nino):
        # The fix. The fetch is enqueued against the blob rather than
        # against the URL, so it can neither re-download nor re-index.
        self.foreign.shared = True

        url, _ = self._create(
            self.auth_token_two, 'two', mock_afo, mock_nino)
        self.assertEqual(f'{BLOB_URL}{self.FOREIGN_BLOB}', url)

    @mock.patch('shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    def test_the_fetch_does_not_name_the_foreign_artifact(
            self, mock_afo, mock_nino):
        # The same fix stated on the other argument. artifact_uuid is
        # what the fetch operation records as its target, and it used to
        # be the shared artifact's -- which is how the write reached it.
        self.foreign.shared = True

        _, artifact_uuid = self._create(
            self.auth_token_two, 'two', mock_afo, mock_nino)
        self.assertNotEqual(str(self.foreign.uuid), artifact_uuid)

    @mock.patch('shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    def test_booting_from_a_shared_artifact_creates_nothing(
            self, mock_afo, mock_nino):
        # A property rather than a regression -- it held before the
        # change too, for a different reason. A caller who boots from
        # somebody else's blob should not also acquire an artifact of
        # its own for that URL, or the next boot would fetch it after
        # all and the reuse would last exactly one instance.
        self.foreign.shared = True
        self._create(self.auth_token_two, 'two', mock_afo, mock_nino)

        self.assertIsNone(self._owned_by('two'))

    @mock.patch('shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    def test_an_invisible_artifact_is_not_reused(self, mock_afo, mock_nino):
        # The control which proves the tests above are the sharing
        # rather than the URL. Unshared, so `two` cannot see it, so
        # `two` gets an artifact of its own and a fetch against the URL.
        url, _ = self._create(
            self.auth_token_two, 'two', mock_afo, mock_nino)
        self.assertEqual(self.URL, url)

        mine = self._owned_by('two')
        self.assertIsNotNone(mine)
        self.assertNotEqual(str(self.foreign.uuid), str(mine.uuid))

    @mock.patch('shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    def test_your_own_artifact_is_still_fetched_into(
            self, mock_afo, mock_nino):
        # The control for the whole class. An owner booting from their
        # own URL must still get a real fetch, which is what keeps a
        # cached image up to date -- narrowing this to the blob would
        # freeze every artifact at its first version.
        url, artifact_uuid = self._create(
            self.auth_token, 'system', mock_afo, mock_nino)

        self.assertEqual(self.URL, url)
        self.assertEqual(str(self.foreign.uuid), artifact_uuid)
        self.assertEqual(str(self.foreign.uuid),
                         str(self._owned_by('system').uuid))

    @mock.patch('shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    def test_a_shared_artifact_with_no_blob_falls_through_to_our_own_fetch(
            self, mock_afo, mock_nino):
        # The branch the comment claims and nothing exercised. A visible
        # artifact which passes the safety checks and still resolves to
        # no blob is a half-built one, and that is a reason to fetch our
        # own copy rather than a reason the instance cannot boot -- so
        # this must be a 200 with a URL fetch, not a refusal.
        self.foreign.shared = True
        with mock.patch.object(Artifact, 'resolve_to_blob', return_value=None):
            url, artifact_uuid = self._create(
                self.auth_token_two, 'two', mock_afo, mock_nino)

        self.assertEqual(self.URL, url)
        self.assertNotEqual(str(self.foreign.uuid), artifact_uuid)
        self.assertIsNotNone(self._owned_by('two'))

    @mock.patch('shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    def test_a_not_yet_created_artifact_is_not_booted_from(
            self, mock_afo, mock_nino):
        # The usability half of the same fall-through, one step earlier:
        # an artifact still in STATE_INITIAL fails the safety check, and
        # that too has to fetch rather than refuse. A second URL rather
        # than the fixture's, because an artifact cannot go backwards
        # from created and Artifact.new leaves this one exactly where it
        # needs to be.
        self.URL = 'https://example.com/half-built.qcow2'
        half_built = Artifact.new(
            Artifact.TYPE_IMAGE, self.URL, name='half-built',
            namespace='system')
        half_built.shared = True

        url, _ = self._create(
            self.auth_token_two, 'two', mock_afo, mock_nino)

        # resolve_to_blob is stubbed to answer for the whole class, so a
        # URL fetch here can only mean the state check refused to use it.
        self.assertEqual(self.URL, url)
        self.assertIsNotNone(self._owned_by('two'))

    @mock.patch('shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    def test_resolution_follows_the_target_namespace_not_the_requestor(
            self, mock_afo, mock_nino):
        # system creating an instance in `two`. Resolution uses the
        # target namespace and the safety check uses request_namespace,
        # which is an asymmetry worth pinning: the artifact this lands
        # on must be `two`'s, because `two` is who will own the instance
        # and the fetch, not system's just because system asked.
        self._create(self.auth_token, 'two', mock_afo, mock_nino)

        mine = self._owned_by('two')
        self.assertIsNotNone(mine)
        self.assertNotEqual(str(self.foreign.uuid), str(mine.uuid))

    @mock.patch('shakenfist.external_api.instance.nino_create_and_enqueue')
    @mock.patch('shakenfist.instance.afo_create_and_enqueue')
    def test_a_successful_create_builds_no_refusal(self, mock_afo, mock_nino):
        # The disk.base fall-through asks whether a foreign artifact is
        # usable and carries on either way, so it must ask with the
        # predicate rather than with _artifact_safety_checks. That
        # helper does not return a bare boolean: it builds a Flask 404
        # and sf_api.error logs 'Returning API error: 404' with a
        # traceback as it does. Called for its truthiness, it wrote a
        # refusal into the log of a request which returns 200 and
        # creates an instance -- and 'not visible to us' is the ordinary
        # case here, not an unusual one.
        #
        # Shared and still in STATE_INITIAL, which is the case where
        # the two diverge: `two` can see it, so `theirs` is truthy and
        # the check actually runs, and the state refuses it. An unshared
        # artifact would prove nothing, because from_url returns None
        # and the check is short-circuited before it is reached.
        self.URL = 'https://example.com/half-built-quietly.qcow2'
        half_built = Artifact.new(
            Artifact.TYPE_IMAGE, self.URL, name='half-built-quietly',
            namespace='system')
        half_built.shared = True

        with mock.patch(
                'shakenfist.external_api.instance.sf_api.error',
                side_effect=sf_api.error) as error:
            url, _ = self._create(
                self.auth_token_two, 'two', mock_afo, mock_nino)

        self.assertEqual(self.URL, url)
        error.assert_not_called()
