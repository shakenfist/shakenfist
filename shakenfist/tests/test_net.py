import uuid
from unittest import mock

import testtools
from shakenfist import exceptions
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.network import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.operations.net_op import NetOp
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class NetworkTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.etcd_lock = mock.patch('shakenfist.locks.ClusterLock')
        self.mock_etcd_lock = self.etcd_lock.start()
        self.addCleanup(self.etcd_lock.stop)


class NetworkGeneralTestCase(NetworkTestCase):
    def test_str(self):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid)
        n = network.Network.from_db(network_uuid)
        self.assertEqual('network(%s)' % network_uuid, str(n))


class NetworkNormalNodeTestCase(NetworkTestCase):
    def setUp(self):
        super().setUp()
        fake_config = SFConfig(NODE_EGRESS_IP='1.1.1.2',
                               NODE_MESH_IP='1.1.1.2',
                               NETWORK_NODE_IP='1.1.1.2',
                               NODE_IS_NETWORK_NODE=False)
        self.config = mock.patch(
            'shakenfist.network.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    #
    #  is_okay()
    #
    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.network.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_yes(self, mock_is_dnsmasq, mock_is_created, mock_pending):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())

    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_created', return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_not_created(self, mock_is_dnsmasq, mock_is_created, mock_pending):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.network.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_dns(self, mock_is_dnsmasq, mock_is_created, mock_pending):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())


class NetworkNetNodeTestCase(NetworkTestCase):
    def setUp(self):
        super().setUp()

        fake_config = SFConfig(NODE_EGRESS_IP='1.1.1.2',
                               NODE_MESH_IP='1.1.1.2',
                               NETWORK_NODE_IP='1.1.1.2',
                               NODE_IS_NETWORK_NODE=True)
        self.config = mock.patch(
            'shakenfist.network.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    #
    #  is_okay()
    #
    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.network.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_yes(self, mock_is_dnsmasq, mock_is_created, mock_pending):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())

    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_created', return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_not_created(self, mock_is_dnsmasq, mock_is_created, mock_pending):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.network.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_masq(self, mock_is_dnsmasq, mock_is_created, mock_pending):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=False)
        n = network.Network.from_db(network_uuid)
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.network.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_masq_no_dhcp(self, mock_is_dnsmasq, mock_is_created, mock_pending):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=False,
                                      provide_nat=False)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())

    #
    #  is_okay() — new test cases for history-aware gating (phase 2)
    #
    @mock.patch('shakenfist.network.network.Network.is_created')
    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=True)
    def test_is_okay_true_when_pending_operation(self, mock_pending, mock_is_created):
        """is_okay() returns True immediately when a pending op is in flight,
        without calling is_created or is_dnsmasq_running."""
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())
        mock_is_created.assert_not_called()

    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.network.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_falls_through_when_no_pending_operation(
            self, mock_is_dnsmasq, mock_is_created, mock_pending):
        """is_okay() falls through to bridge/dnsmasq checks when no op is in flight."""
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())
        mock_is_created.assert_called_once()

    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_created', return_value=False)
    @mock.patch('shakenfist.network.network.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_false_when_not_created_and_no_pending(
            self, mock_is_dnsmasq, mock_is_created, mock_pending):
        """is_okay() returns False when no pending op and network is not created."""
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.network.network.Network.last_cluster_operation',
                new_callable=mock.PropertyMock, return_value=None)
    @mock.patch('shakenfist.network.network.Network.has_pending_cluster_operation',
                return_value=True)
    def test_is_okay_history_aware_race_fix(self, mock_pending, mock_lco):
        """Regression test: gating rests on has_pending_cluster_operation alone.

        Even when last_cluster_operation returns None (simulating the race
        where a later terminal op overwrote the pointer), is_okay() must
        return True because has_pending_cluster_operation reports an in-flight
        op. A regression to single-pointer gating would break this test.
        """
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())

    #
    # is_created()
    #
    @mock.patch('shakenfist.util.concurrency.execute',
                return_value=(
                    """[ {},{
        "ifindex": 1,
        "ifname": "br-vxlan-5",
        "flags": [ "BROADCAST","MULTICAST","UP","LOWER_UP" ],
        "mtu": 1500,
        "qdisc": "noqueue",
        "operstate": "UP",
        "group": "default",
        "txqlen": 1000,
        "link_type": "ether",
        "address": "1a:46:97:a1:c2:3a",
        "broadcast": "ff:ff:ff:ff:ff:ff"
    },{},{},{} ]""", ''))
    def test_is_created_yes(self, mock_execute):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=False)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_created())

    @mock.patch('shakenfist.util.concurrency.execute',
                return_value=("""[ {},{
        "ifindex": 1,
        "ifname": "br-vxlan-5",
        "flags": [ "BROADCAST","MULTICAST","DOWN","LOWER_UP" ],
        "mtu": 1500,
        "qdisc": "noqueue",
        "operstate": "UP",
        "group": "default",
        "txqlen": 1000,
        "link_type": "ether",
        "address": "1a:46:97:a1:c2:3a",
        "broadcast": "ff:ff:ff:ff:ff:ff"
    },{},{},{} ]""", ''))
    def test_is_created_no(self, mock_execute):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=False)
        n = network.Network.from_db(network_uuid)
        self.assertFalse(n.is_created())

    @mock.patch('shakenfist.util.concurrency.execute',
                return_value=('', "Device 'br-vxlan-45' does not exist."))
    def test_is_created_no_bridge(self, mock_execute):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=False)
        n = network.Network.from_db(network_uuid)
        self.assertFalse(n.is_created())

    def test_set_state_valid(self):

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=False)
        n = network.Network.from_db(network_uuid)

        with testtools.ExpectedException(exceptions.InvalidStateException):
            n.state = network.Network.STATE_INITIAL
        n.state = dbo.STATE_ERROR
        n.state = dbo.STATE_DELETED
        with testtools.ExpectedException(exceptions.InvalidStateException):
            n.state = dbo.STATE_CREATED


class NetworkEnsureMeshEnqueueTestCase(NetworkTestCase):
    """Tests for the phase 2f flip of ``Network.ensure_mesh()``.

    After phase 2f, ``Network.ensure_mesh()`` no longer mutates host
    state inline. Instead it enqueues a ``NetOp`` with task
    ``network_ensure_mesh`` on the calling node's per-node ``network``
    queue and returns the enqueued op so callers can call
    ``op.raise_for_error()``. These tests pin that contract.
    """

    NODE_UUID = '11111111-1111-4111-8111-aaaaaaaaaaaa'

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(NODE_UUID=self.NODE_UUID,
                               NODE_EGRESS_IP='1.1.1.2',
                               NODE_MESH_IP='1.1.1.2',
                               NETWORK_NODE_IP='1.1.1.2',
                               NODE_IS_NETWORK_NODE=False)
        self.config = mock.patch(
            'shakenfist.network.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # The util.enqueue_cluster_operation helper also references
        # the cluster_operation_targets writer; the MockEtcd fixture
        # already mocks the underlying mariadb calls, but it does not
        # mock the target writer. Patch it inert so calls succeed.
        self.mock_create_target = mock.patch(
            'shakenfist.mariadb.create_cluster_operation_target').start()
        self.addCleanup(mock.patch.stopall)

        # Guard the test against any accidental host mutation: the old
        # implementation called util_concurrency.ensure_vxlan_mesh
        # synchronously. The new implementation must not.
        self.mock_ensure_vxlan_mesh = mock.patch(
            'shakenfist.util.concurrency.ensure_vxlan_mesh').start()

    def test_ensure_mesh_enqueues_netop_on_local_node_queue(self):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, provide_dhcp=True, provide_nat=False)
        n = network.Network.from_db(network_uuid)

        # Spy on the enqueue path. MockEtcd already wraps the underlying
        # mariadb function via side_effect; we put another patch in front
        # of it that delegates to the MockEtcd implementation and records
        # the call. This lets us inspect arguments without losing the
        # mock_etcd state-machine side effects (which NetOp.from_db
        # depends on for state lookup).
        original = (
            self.mock_etcd._mariadb_create_and_enqueue_cluster_operation)
        spy = mock.MagicMock(side_effect=original)
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.ensure_mesh()

        spy.assert_called_once()

        # Inspect the call that ensure_mesh issued.
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            f'{self.NODE_UUID}-network-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_ensure_mesh', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual(PRIORITY.user_facing.name, metadata['priority'])

        # The new contract: ensure_mesh returns a NetOp instance
        # constructed from the persisted operation record. The caller
        # uses op.raise_for_error() to block on completion.
        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        # The old, host-mutating call site must not fire from inside
        # Network.ensure_mesh() any more.
        self.mock_ensure_vxlan_mesh.assert_not_called()

    def test_ensure_mesh_skips_for_floating_network(self):
        # The floating network short-circuits via the
        # @_not_on_floating_network decorator and must not enqueue.
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        self.mock_etcd.create_network(
            'floatnet', str(FLOATING_NETWORK_UUID),
            provide_dhcp=False, provide_nat=False)
        n = network.Network.from_db(str(FLOATING_NETWORK_UUID))

        original = (
            self.mock_etcd._mariadb_create_and_enqueue_cluster_operation)
        spy = mock.MagicMock(side_effect=original)
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            result = n.ensure_mesh()

        # No enqueue happened: the decorator short-circuits.
        spy.assert_not_called()
        # The decorator returns None implicitly.
        self.assertIsNone(result)
        self.mock_ensure_vxlan_mesh.assert_not_called()
