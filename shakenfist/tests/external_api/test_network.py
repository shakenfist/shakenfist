import json
import logging
import sys
from unittest import mock
from uuid import uuid4

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.config import SFConfig
from shakenfist.exceptions import NetworkOperationFailed
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class FakeScheduler:
    def find_candidates(self, *args, **kwargs):
        return config.NODE_NAME


class NetworksDeleteNoneTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
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

        self.network_id = str(uuid4())
        self.mock_etcd.create_network(
            'banana',
            uuid=self.network_id,
            namespace='foo',
            set_state=dbo.STATE_DELETED)

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
        # Phase 7 contract: bulk delete returns 202 with a list of
        # {network_uuid, op_type, op_uuid} entries. When the namespace
        # has no active networks the list is empty.
        self.assertEqual(202, resp.status_code)
        self.assertEqual([], resp.get_json())


class NetworksDeleteAllTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
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

        self.network_id = str(uuid4())
        self.mock_etcd.create_network(
            name='foonet',
            uuid=self.network_id,
            namespace='foo',
            set_state=dbo.STATE_CREATED)

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    @mock.patch('shakenfist.external_api.network.net_create_and_enqueue')
    @mock.patch('shakenfist.network.network.Network.remove_dnsmasq')
    @mock.patch('shakenfist.network.network.Network.delete_on_network_node')
    @mock.patch('shakenfist.network.network.Network.delete_on_hypervisor')
    def test_delete_all_networks(self, mock_delete_on_hypervisor,
                                 mock_delete_on_network_node,
                                 mock_remove_dnsmasq,
                                 mock_enqueue):
        fake_op_uuid = str(uuid4())
        mock_enqueue.return_value = ('net_op', fake_op_uuid)

        self.client = external_api.app.test_client()
        resp = self.client.delete('/networks',
                                  headers={'Authorization': self.auth_token},
                                  data=json.dumps({
                                      'confirm': True,
                                      'namespace': 'foo'
                                  }))
        # Phase 7 contract: bulk delete returns HTTP 202 with a list of
        # {network_uuid, op_type, op_uuid} entries.
        self.assertEqual(202, resp.status_code)
        self.assertEqual(
            [{
                'network_uuid': self.network_id,
                'op_type': 'net_op',
                'op_uuid': fake_op_uuid,
            }],
            resp.get_json())


class NetworkDeleteEnqueueTaskTestCase(base.ShakenFistTestCase):
    """Phase 6 step 6c: DELETE /networks/<uuid> enqueues
    network_apply_delete_network_node (task 12) directly, not the retired
    network_destroy composite task (task 2).
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.1',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=True,
        )
        self.config = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        self.mock_etcd.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        self.mock_etcd.create_network(
            name='foonet',
            uuid=self.network_id,
            namespace='foo',
            set_state=dbo.STATE_CREATED)

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    @mock.patch('shakenfist.external_api.network.net_create_and_enqueue')
    def test_delete_network_enqueues_apply_delete_network_node(
            self, mock_enqueue):
        # The mocked enqueue returns the (op_type, op_uuid) tuple the real
        # function returns; the Phase 7 DELETE handler surfaces these as
        # `op_type` / `op_uuid` in the 202 response body.
        fake_op_uuid = str(uuid4())
        mock_enqueue.return_value = ('net_op', fake_op_uuid)

        resp = self.client.delete(
            '/networks/%s' % self.network_id,
            headers={'Authorization': self.auth_token})
        # Phase 7 contract: single-network delete returns HTTP 202 with
        # `{op_type, op_uuid}` identifying the queued cluster operation.
        self.assertEqual(202, resp.status_code)
        self.assertEqual(
            {'op_type': 'net_op', 'op_uuid': fake_op_uuid},
            resp.get_json())

        mock_enqueue.assert_called_once()
        args, kwargs = mock_enqueue.call_args
        # Positional args: (network_uuid, tasks, priority)
        tasks = args[1]
        # Late import to avoid pulling the schema into the module-level
        # imports when the rest of this file does not need it.
        from shakenfist.schema.operations.net_op import model_tasks
        self.assertEqual(
            [model_tasks.network_apply_delete_network_node], tasks)

    @mock.patch('shakenfist.external_api.network.net_create_and_enqueue')
    @mock.patch(
        'shakenfist.network.network.Network.networkinterfaces',
        new_callable=mock.PropertyMock)
    def test_delete_network_with_interfaces_still_enqueues_op(
            self, mock_interfaces, mock_enqueue):
        """When a network still has interfaces, the DELETE handler must
        return a real op handle (not None/None) so the client can poll
        the 202+op-handle contract. The op itself will defer in the
        worker until the interfaces drain.
        """
        mock_interfaces.return_value = ['some-interface-uuid']
        fake_op_uuid = str(uuid4())
        mock_enqueue.return_value = ('net_op', fake_op_uuid)

        resp = self.client.delete(
            '/networks/%s' % self.network_id,
            headers={'Authorization': self.auth_token})
        self.assertEqual(202, resp.status_code)
        self.assertEqual(
            {'op_type': 'net_op', 'op_uuid': fake_op_uuid},
            resp.get_json())
        mock_enqueue.assert_called_once()


class NetworkDeleteAlreadyDeletedTestCase(base.ShakenFistTestCase):
    """DELETE on a network whose state is already 'deleted' must not
    return a 200 ``null`` body -- that crashed the client on
    ``handle['op_type']``. The endpoint should now surface
    ``_delete_network``'s 404 response instead.
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.1',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=True,
        )
        self.config = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        self.mock_etcd.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        # Note the state -- the whole point of this test class.
        self.mock_etcd.create_network(
            name='foonet',
            uuid=self.network_id,
            namespace='foo',
            set_state=dbo.STATE_DELETED)

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def test_delete_already_deleted_returns_404_not_null(self):
        resp = self.client.delete(
            '/networks/%s' % self.network_id,
            headers={'Authorization': self.auth_token})
        # Specifically NOT 200 + null body -- that's the bug.
        self.assertEqual(404, resp.status_code)
        body = resp.get_json()
        self.assertIsNotNone(body)
        self.assertIn('error', body)


class NetworkDNSAddressEndpointTestCase(base.ShakenFistTestCase):
    """Regression tests for step 4f: REST handlers call raise_for_error()
    after update_dns_entry / remove_dns_entry."""

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.1',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=True,
        )
        self.config = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')
        self.mock_etcd.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        self.mock_etcd.create_network(
            'dnsnet',
            uuid=self.network_id,
            namespace='foo',
            provide_dns=True,
            set_state=dbo.STATE_CREATED)

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    @mock.patch('shakenfist.network.network.Network.update_dns_entry')
    def test_post_dns_entry_success(self, mock_update):
        """update_dns_entry returns an op; raise_for_error() succeeds."""
        fake_op = mock.MagicMock()
        fake_op.raise_for_error.return_value = None
        mock_update.return_value = fake_op

        resp = self.client.post(
            '/networks/%s/dns' % self.network_id,
            headers={'Authorization': self.auth_token},
            data=json.dumps({'name': 'test.example', 'value': '10.0.0.1'}))
        self.assertEqual(200, resp.status_code)
        mock_update.assert_called_once_with('test.example', '10.0.0.1')
        fake_op.raise_for_error.assert_called_once()

    @mock.patch('shakenfist.network.network.Network.update_dns_entry')
    def test_post_dns_entry_operation_failed(self, mock_update):
        """When raise_for_error() raises NetworkOperationFailed, the REST
        endpoint returns HTTP 500."""
        fake_op = mock.MagicMock()
        fake_report = mock.MagicMock()
        fake_report.code = 'network.dnsmasq.restart_failed'
        fake_report.message = 'dnsmasq restart failed'
        fake_op.raise_for_error.side_effect = NetworkOperationFailed(fake_report)
        mock_update.return_value = fake_op

        resp = self.client.post(
            '/networks/%s/dns' % self.network_id,
            headers={'Authorization': self.auth_token},
            data=json.dumps({'name': 'fail.example', 'value': '10.0.0.2'}))
        self.assertEqual(500, resp.status_code)

    @mock.patch('shakenfist.network.network.Network.remove_dns_entry')
    def test_delete_dns_entry_success(self, mock_remove):
        """remove_dns_entry returns an op; raise_for_error() succeeds."""
        fake_op = mock.MagicMock()
        fake_op.raise_for_error.return_value = None
        mock_remove.return_value = fake_op

        resp = self.client.delete(
            '/networks/%s/dns' % self.network_id,
            headers={'Authorization': self.auth_token},
            data=json.dumps({'name': 'test.example'}))
        self.assertEqual(200, resp.status_code)
        mock_remove.assert_called_once_with('test.example')
        fake_op.raise_for_error.assert_called_once()

    @mock.patch('shakenfist.network.network.Network.remove_dns_entry')
    def test_delete_dns_entry_operation_failed(self, mock_remove):
        """When raise_for_error() raises NetworkOperationFailed, the REST
        endpoint returns HTTP 500."""
        fake_op = mock.MagicMock()
        fake_report = mock.MagicMock()
        fake_report.code = 'network.dnsmasq.restart_failed'
        fake_report.message = 'dnsmasq restart failed'
        fake_op.raise_for_error.side_effect = NetworkOperationFailed(fake_report)
        mock_remove.return_value = fake_op

        resp = self.client.delete(
            '/networks/%s/dns' % self.network_id,
            headers={'Authorization': self.auth_token},
            data=json.dumps({'name': 'fail.example'}))
        self.assertEqual(500, resp.status_code)
