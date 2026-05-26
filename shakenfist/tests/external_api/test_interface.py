import json
import logging
import sys
from unittest import mock
from uuid import uuid4

from shakenfist.config import config
from shakenfist.config import SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class InterfaceEndpointGetTestCase(base.ShakenFistTestCase):
    """Phase 7 step 7b: InterfaceEndpoint.get is now a synchronous read.

    The endpoint previously carried `@redirect_to_network_node`, which
    forced the API server to proxy the request to the elected network
    node. The handler body is a pure database read, so the redirect is
    no longer necessary and was removed.
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        # We do NOT pretend to be the network node here; the whole point
        # of removing the redirect is that this endpoint works from any
        # API server.
        fake_config = SFConfig(
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.2',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=False,
            ETCD_HOST='127.0.0.1'
        )
        self.config_patch = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.client = external_api.app.test_client()

        self.mock_etcd.create_namespace('system', 'key1', 'bar')

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    @mock.patch('shakenfist.external_api.interface.api_util.safe_get_network_interface')
    def test_get_interface_returns_external_view(self, mock_safe_get):
        # Use a real-looking UUID so the route matches.
        interface_uuid = str(uuid4())
        fake_ni = mock.MagicMock()
        fake_view = {
            'uuid': interface_uuid,
            'ipv4': '10.0.0.6',
            'macaddr': '02:00:00:73:18:66',
            'state': 'created',
        }
        fake_ni.external_view.return_value = fake_view
        # safe_get_network_interface returns (ni, network, err)
        mock_safe_get.return_value = (fake_ni, mock.MagicMock(), None)

        # Sanity check that we are running this independently of being
        # the network node -- the decorator removal is what makes this
        # possible.
        self.assertFalse(config.NODE_IS_NETWORK_NODE)

        resp = self.client.get(
            '/interfaces/%s' % interface_uuid,
            headers={'Authorization': self.auth_token})

        self.assertEqual(200, resp.status_code)
        self.assertEqual(fake_view, resp.get_json())
        fake_ni.external_view.assert_called_once()
