import json
import logging
import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config, SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.network import Network
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class FakeScheduler(object):
    def place_instance(self, *args, **kwargs):
        return config.NODE_NAME


class NetworksDeleteNoneTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(NetworksDeleteNoneTestCase, self).setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        # We need to pretend to be the network node
        fake_config = SFConfig(
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.1',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=True,
            ETCD_HOST='127.0.0.1'
        )
        self.config = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        self.mock_etcd.create_namespace('foo', 'key1', 'bar')
        self.mock_etcd.create_network('banana', uuid='123', namespace='foo')
        n = Network.from_db('123')
        n.state = dbo.STATE_DELETED

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def test_delete(self):
        resp = self.client.delete('/networks',
                                  headers={'Authorization': self.auth_token},
                                  data=json.dumps({
                                      'confirm': True,
                                      'namespace': 'foo'
                                  }))
        self.assertEqual([], resp.get_json())


class NetworksDeleteAllTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(NetworksDeleteAllTestCase, self).setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler())
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        # We need to pretend to be the network node
        fake_config = SFConfig(
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.1',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=True
        )
        self.config = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        self.mock_etcd.create_namespace('foo', 'key1', 'bar')
        self.mock_etcd.create_network(
            name='foonet', uuid='123', namespace='foo', set_state=dbo.STATE_CREATED)

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    @mock.patch('shakenfist.network.Network.remove_dhcp')
    @mock.patch('shakenfist.network.Network.delete_on_network_node')
    @mock.patch('shakenfist.network.Network.delete_on_hypervisor')
    def test_delete_all_networks(self, mock_delete_on_hypervisor,
                                 mock_delete_on_network_node, mock_remove_dhcp):
        self.client = external_api.app.test_client()
        resp = self.client.delete('/networks',
                                  headers={'Authorization': self.auth_token},
                                  data=json.dumps({
                                      'confirm': True,
                                      'namespace': 'foo'
                                  }))
        self.assertEqual(['123'], resp.get_json())
        self.assertEqual(200, resp.status_code)
