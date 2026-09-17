import ipaddress
import json
import logging
import sys
import time
from unittest import mock
from uuid import uuid4

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.config import SFConfig
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.external_api import network as api_network
from shakenfist.network import network as net
from shakenfist.exceptions import NetworkOperationFailed
from shakenfist.external_api import app as external_api
from shakenfist.schema.ipam_reservation import IPAMReservation
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        self.mock_mariadb.create_network(
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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        self.mock_mariadb.create_network(
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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        self.mock_mariadb.create_network(
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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        # Note the state -- the whole point of this test class.
        self.mock_mariadb.create_network(
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


class NetworkCreateBooleanDefaultsTestCase(base.ShakenFistTestCase):
    """Regression tests for the POST /networks boolean flag defaults.

    The create handler must distinguish "the caller omitted the field"
    (None -> apply the default) from "the caller explicitly asked for
    False". A previous `if not provide_nat: provide_nat = True` idiom
    collapsed both cases, so --no-nat / --no-dhcp silently created a
    network with NAT / DHCP still enabled.
    """

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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def _create_network(self, body):
        body.setdefault('name', 'boolnet')
        body.setdefault('netblock', '10.0.2.0/24')
        body.setdefault('namespace', 'system')
        return self.client.post(
            '/networks',
            headers={'Authorization': self.auth_token},
            data=json.dumps(body))

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_explicit_false_is_honoured(self, _mock_enqueue):
        """--no-dhcp / --no-nat / --no-dns send explicit False and must be
        stored as False, not coerced back to the default."""
        resp = self._create_network({
            'provide_dhcp': False,
            'provide_nat': False,
            'provide_dns': False,
        })
        self.assertEqual(200, resp.status_code)
        n = resp.get_json()
        self.assertFalse(n['provide_dhcp'])
        self.assertFalse(n['provide_nat'])
        self.assertFalse(n['provide_dns'])

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_omitted_fields_use_defaults(self, _mock_enqueue):
        """When the flags are omitted, DHCP and NAT default to enabled and
        DNS defaults to disabled."""
        resp = self._create_network({})
        self.assertEqual(200, resp.status_code)
        n = resp.get_json()
        self.assertTrue(n['provide_dhcp'])
        self.assertTrue(n['provide_nat'])
        self.assertFalse(n['provide_dns'])

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_explicit_true_is_honoured(self, _mock_enqueue):
        """Explicitly requesting the non-default (DNS on) must also work."""
        resp = self._create_network({
            'provide_dhcp': True,
            'provide_nat': True,
            'provide_dns': True,
        })
        self.assertEqual(200, resp.status_code)
        n = resp.get_json()
        self.assertTrue(n['provide_dhcp'])
        self.assertTrue(n['provide_nat'])
        self.assertTrue(n['provide_dns'])


class NetworkCreateFloatingOverlapTestCase(base.ShakenFistTestCase):
    """Regression tests for issue 323: POST /networks must refuse a
    netblock which overlaps the deployed floating network, and must not
    refuse anything when no floating network is configured yet."""

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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def _create_floating_network(self, netblock='192.168.20.0/24'):
        self.mock_mariadb.create_network(
            'floatnet', str(FLOATING_NETWORK_UUID), netblock=netblock,
            provide_dhcp=False, provide_nat=False)

    def _create_network(self, netblock):
        return self.client.post(
            '/networks',
            headers={'Authorization': self.auth_token},
            data=json.dumps({
                'name': 'overlapnet',
                'netblock': netblock,
                'namespace': 'system',
            }))

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_no_row_falls_back_to_the_configuration(self, _mock_enqueue):
        """A cluster which has not needed a floating IP yet has no
        floating network row, because network.floating_network() creates
        it on first use. The netblock is still configured, and a network
        overlapping it still conflicts -- it would collide the moment the
        network node bootstrapped."""
        with mock.patch.object(
                api_network.config, 'FLOATING_NETWORK', '192.168.20.0/24'):
            resp = self._create_network('192.168.20.0/24')
        self.assertEqual(400, resp.status_code)
        self.assertIn('overlaps the floating network', resp.get_json()['error'])

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_no_floating_network_at_all_does_not_refuse(self, _mock_enqueue):
        """With no row and nothing configured there is nothing to
        overlap with, and the guard must not refuse."""
        with mock.patch.object(api_network.config, 'FLOATING_NETWORK', ''):
            resp = self._create_network('192.168.20.0/24')
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_an_unparseable_configuration_does_not_refuse(self, _mock_enqueue):
        """A floating netblock we cannot parse is a deployment problem.
        Refusing every network create until it is fixed would be a worse
        one, so the guard fails open."""
        with mock.patch.object(
                api_network.config, 'FLOATING_NETWORK', 'not-a-netblock'):
            resp = self._create_network('192.168.20.0/24')
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_the_row_wins_over_the_configuration(self, _mock_enqueue):
        """FLOATING_NETWORK can be edited after the floating network is
        created, and floating IPs keep coming from the row. So the row is
        what a new network must not overlap: a netblock which clashes with
        a stale configuration but not with the live row is allowed."""
        self._create_floating_network('10.10.0.0/24')
        with mock.patch.object(
                api_network.config, 'FLOATING_NETWORK', '192.168.20.0/24'):
            resp = self._create_network('192.168.20.0/24')
        self.assertEqual(200, resp.status_code)

    def test_a_partial_overlap_cannot_be_expressed(self):
        """There is no fourth overlap case to test for.

        CIDR prefixes form a tree, so two valid blocks are either
        disjoint or one contains the other -- a block which shares only
        some addresses with another has host bits set and is not a valid
        strict network at all. identical, contains and contained-by are
        therefore the complete set of ways to overlap, and the format
        validator step 4 added refuses the rest before the guard runs.
        """
        self.assertRaises(
            ValueError, ipaddress.ip_network, '192.168.20.128/23')

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_identical_netblock_is_refused(self, _mock_enqueue):
        self._create_floating_network('192.168.20.0/24')
        resp = self._create_network('192.168.20.0/24')
        self.assertEqual(400, resp.status_code)
        self.assertIn('overlaps the floating network', resp.get_json()['error'])

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_requested_netblock_contains_floating_network(self, _mock_enqueue):
        self._create_floating_network('192.168.20.0/24')
        resp = self._create_network('192.168.0.0/16')
        self.assertEqual(400, resp.status_code)
        self.assertIn('overlaps the floating network', resp.get_json()['error'])

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_requested_netblock_contained_by_floating_network(self, _mock_enqueue):
        self._create_floating_network('192.168.20.0/24')
        resp = self._create_network('192.168.20.128/25')
        self.assertEqual(400, resp.status_code)
        self.assertIn('overlaps the floating network', resp.get_json()['error'])

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_disjoint_netblock_is_not_refused(self, _mock_enqueue):
        self._create_floating_network('192.168.20.0/24')
        resp = self._create_network('10.0.2.0/24')
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_a_row_with_no_netblock_falls_back_to_the_configuration(
            self, _mock_enqueue):
        """A floating network row carrying no netblock is not an answer.

        "There is a row" and "the row says what the floating block is"
        are different facts, and only the second one can turn the guard
        off. Reading the row's netblock without asking whether it has
        one would skip the whole guard on an empty value -- the same
        failure the fallback to the configuration exists to prevent,
        reached by a different route.

        The row is a stub rather than a real one because Network.new()
        cannot make this row: it builds an IPAM from the netblock and
        ipaddress.ip_network('') raises. An empty netblock here is a
        damaged or half-written row, which is the case a guard is
        supposed to survive rather than one a caller can ask for.
        """
        stub = mock.MagicMock()
        stub.netblock = ''

        with mock.patch.object(
                api_network.config, 'FLOATING_NETWORK', '192.168.20.0/24'), \
                mock.patch.object(api_network.network.Network, 'from_db',
                                  return_value=stub):
            resp = self._create_network('192.168.20.0/24')
        self.assertEqual(400, resp.status_code)
        self.assertIn('overlaps the floating network', resp.get_json()['error'])

    @mock.patch('shakenfist.external_api.network.LOG')
    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_an_unparseable_configuration_warns(self, _mock_enqueue, mock_log):
        """Failing open is right, failing open silently is not.

        The guard cannot refuse every network create because a
        deployment's FLOATING_NETWORK is malformed. But an operator
        whose safety check has turned itself off has to be able to find
        out, and the request which triggered it is the only moment
        anything knows.
        """
        with mock.patch.object(
                api_network.config, 'FLOATING_NETWORK', 'not-a-netblock'):
            resp = self._create_network('192.168.20.0/24')
        self.assertEqual(200, resp.status_code)

        mock_log.with_fields.assert_called_with(
            {'floating_netblock': 'not-a-netblock'})
        mock_log.with_fields.return_value.warning.assert_called_once()
        self.assertIn(
            'the overlap guard is disabled',
            mock_log.with_fields.return_value.warning.call_args.args[0])

    @mock.patch('shakenfist.network.network.net_create_and_enqueue')
    def test_an_unauthorised_caller_is_not_told_the_floating_netblock(
            self, _mock_enqueue):
        """401 beats 400, and the guard must not leak on the way past.

        The floating network has namespace=None, so an ordinary caller
        cannot read it. A refusal naming its netblock therefore tells
        them something the API otherwise does not -- and tells it to a
        caller whose request was going to be refused as unauthorised
        anyway. The guard sits below the authorisation check for that
        reason.
        """
        self._create_floating_network('192.168.20.0/24')
        self.mock_mariadb.create_namespace('tenant', 'key1', 'tenantkey')

        resp = self.client.post(
            '/auth',
            data=json.dumps({'namespace': 'tenant', 'key': 'tenantkey'}))
        self.assertEqual(200, resp.status_code)
        tenant_token = 'Bearer %s' % resp.get_json()['access_token']

        resp = self.client.post(
            '/networks',
            headers={'Authorization': tenant_token},
            data=json.dumps({
                'name': 'overlapnet',
                'netblock': '192.168.20.0/24',
                'namespace': 'system',
            }))
        self.assertEqual(401, resp.status_code)
        self.assertNotIn(
            'overlaps the floating network', resp.get_json()['error'])
        self.assertNotIn('192.168.20.0/24', resp.get_json()['error'])


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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        self.mock_mariadb.create_network(
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


class NetworkUnrouteAddressEndpointTestCase(base.ShakenFistTestCase):
    """Unrouting an address must release its IPAM reservation.

    The unroute_address job only tears down the host side route; the
    handler is responsible for returning the address to the floating
    pool, mirroring the defloat path. Before issue 4114 the handler
    enqueued the job and stopped, so the reservation stayed in the
    floating pool until the owning network was deleted.
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
            NETWORK_NODE_IP='127.0.0.2',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=False,
        )
        self.config = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        self.mock_mariadb.create_network(
            'routenet',
            uuid=self.network_id,
            namespace='foo',
            set_state=dbo.STATE_CREATED)

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def _routed_reservation(self, user_uuid):
        return IPAMReservation(
            ipam_uuid=str(uuid4()),
            address='192.168.20.75',
            reservation_type=ReservationType.ROUTED,
            user_type=ObjectType.NETWORK,
            user_uuid=user_uuid,
            reserved_at=time.time())

    @mock.patch('shakenfist.external_api.network.nip_create_and_enqueue')
    @mock.patch('shakenfist.network.network.floating_network')
    def test_unroute_releases_reservation(self, mock_fn, mock_enqueue):
        fake_fn = mock.MagicMock()
        fake_fn.ipam.get_reservation.return_value = \
            self._routed_reservation(self.network_id)
        mock_fn.return_value = fake_fn

        resp = self.client.delete(
            '/networks/%s/route/192.168.20.75' % self.network_id,
            headers={'Authorization': self.auth_token})

        self.assertEqual(200, resp.status_code)
        mock_enqueue.assert_called_once()
        # The host side teardown is asynchronous, but the reservation must
        # be released synchronously or the address leaks from the floating
        # pool until the network is deleted.
        fake_fn.ipam.release.assert_called_once_with('192.168.20.75')

    @mock.patch('shakenfist.external_api.network.nip_create_and_enqueue')
    @mock.patch('shakenfist.network.network.floating_network')
    def test_unroute_unreserved_address_404_no_release(
            self, mock_fn, mock_enqueue):
        fake_fn = mock.MagicMock()
        fake_fn.ipam.get_reservation.return_value = None
        mock_fn.return_value = fake_fn

        resp = self.client.delete(
            '/networks/%s/route/192.168.20.75' % self.network_id,
            headers={'Authorization': self.auth_token})

        self.assertEqual(404, resp.status_code)
        mock_enqueue.assert_not_called()
        fake_fn.ipam.release.assert_not_called()

    @mock.patch('shakenfist.external_api.network.nip_create_and_enqueue')
    @mock.patch('shakenfist.network.network.floating_network')
    def test_unroute_other_networks_address_403_no_release(
            self, mock_fn, mock_enqueue):
        fake_fn = mock.MagicMock()
        fake_fn.ipam.get_reservation.return_value = \
            self._routed_reservation(str(uuid4()))
        mock_fn.return_value = fake_fn

        resp = self.client.delete(
            '/networks/%s/route/192.168.20.75' % self.network_id,
            headers={'Authorization': self.auth_token})

        self.assertEqual(403, resp.status_code)
        mock_enqueue.assert_not_called()
        fake_fn.ipam.release.assert_not_called()


class NetworkAddressEndpointTestCase(base.ShakenFistTestCase):
    """Manual address reservations.

    A caller can hold an address in a network against something Shaken
    Fist does not manage -- a keepalived VIP inside a guest, say -- so
    that IPAM never hands it to an interface. Without this the address
    is free, and `reserve_random_free_address` will eventually pick it
    (kerbside-patches CI run 35202102331, where the VIP landed on a test
    instance's second interface and kolla-ansible's prechecks refused to
    deploy).
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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        self.network_id = str(uuid4())
        self.mock_mariadb.create_network(
            'reservenet',
            uuid=self.network_id,
            namespace='foo',
            netblock='10.9.8.0/24',
            set_state=dbo.STATE_CREATED)

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def _reserve(self, address, comment=None):
        body = {}
        if comment:
            body['comment'] = comment
        return self.client.post(
            '/networks/%s/addresses/%s' % (self.network_id, address),
            headers={'Authorization': self.auth_token},
            data=json.dumps(body))

    def _release(self, address):
        return self.client.delete(
            '/networks/%s/addresses/%s' % (self.network_id, address),
            headers={'Authorization': self.auth_token})

    def test_reserve_a_free_address(self):
        resp = self._reserve('10.9.8.3', comment='kolla VIP')
        self.assertEqual(200, resp.status_code)

        reservation = resp.get_json()
        self.assertEqual('10.9.8.3', reservation['address'])
        self.assertEqual(ReservationType.MANUAL.value,
                         reservation['reservation_type'])
        self.assertEqual('kolla VIP', reservation['comment'])
        self.assertEqual(ObjectType.NETWORK.value, reservation['user_type'])
        self.assertEqual(self.network_id, reservation['user_uuid'])

    def test_a_reserved_address_is_not_allocated(self):
        """The whole point: IPAM must not hand the address out again."""
        self.assertEqual(200, self._reserve('10.9.8.3').status_code)

        n = net.Network.from_db(self.network_id)
        for _ in range(250):
            address = n.ipam.reserve_random_free_address(
                (ObjectType.NETWORK, self.network_id),
                ReservationType.INSTANCE, '')
            self.assertNotEqual('10.9.8.3', address)

    def test_reserve_an_address_already_in_use(self):
        # 10.9.8.1 is the gateway, reserved when the IPAM was created.
        resp = self._reserve('10.9.8.1')
        self.assertEqual(409, resp.status_code)

    def test_reserve_the_same_address_twice(self):
        self.assertEqual(200, self._reserve('10.9.8.3').status_code)
        self.assertEqual(409, self._reserve('10.9.8.3').status_code)

    def test_reserve_an_address_outside_the_netblock(self):
        resp = self._reserve('10.9.9.3')
        self.assertEqual(400, resp.status_code)

    def test_reserve_an_address_which_is_not_an_address(self):
        resp = self._reserve('banana')
        self.assertEqual(400, resp.status_code)

    def test_release_a_reservation(self):
        self.assertEqual(200, self._reserve('10.9.8.3').status_code)
        self.assertEqual(200, self._release('10.9.8.3').status_code)

        # Released addresses sit in the deletion halo rather than becoming
        # immediately free, which is IPAM's normal behaviour and not
        # special to manual reservations.
        n = net.Network.from_db(self.network_id)
        reservation = n.ipam.get_reservation('10.9.8.3')
        self.assertEqual(ReservationType.DELETION_HALO,
                         reservation.reservation_type)

    def test_release_an_address_we_do_not_hold(self):
        resp = self._release('10.9.8.3')
        self.assertEqual(404, resp.status_code)

    def test_release_an_address_something_else_holds(self):
        """A gateway or an instance's address is not the caller's to free."""
        resp = self._release('10.9.8.1')
        self.assertEqual(403, resp.status_code)

        n = net.Network.from_db(self.network_id)
        reservation = n.ipam.get_reservation('10.9.8.1')
        self.assertEqual(ReservationType.GATEWAY,
                         reservation.reservation_type)

    def test_reserve_takes_over_a_deletion_halo_address(self):
        """A caller naming an address gets it even if it is cooling down."""
        n = net.Network.from_db(self.network_id)
        n.ipam.reserve('10.9.8.3', (ObjectType.NETWORK, self.network_id),
                       ReservationType.INSTANCE, '')
        n.ipam.release('10.9.8.3')

        resp = self._reserve('10.9.8.3')
        self.assertEqual(200, resp.status_code)
        self.assertEqual(ReservationType.MANUAL.value,
                         resp.get_json()['reservation_type'])
