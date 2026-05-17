import uuid
from unittest import mock

import testtools
from shakenfist import exceptions
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.network import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.operations.net_ip_op import NetIPOp
from shakenfist.operations.net_macaddr_ip_op import NetMacaddrIPOp
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


class NetworkFloatingIPEnqueueTestCase(NetworkTestCase):
    """Tests for the phase 3e flip of the five floating-IP / route
    methods on ``Network``.

    After phase 3e, each of ``add_floating_ip``, ``remove_floating_ip``,
    ``route_address``, ``unroute_address``, and ``remove_nat`` no longer
    mutates host state inline. Instead each enqueues a cluster operation
    on ``networknode-clusteroperation-user_facing`` (the existing
    network-node queue family) and returns the loaded op so callers can
    call ``op.raise_for_error()``. These tests pin that contract.
    """

    NODE_UUID = '22222222-2222-4222-8222-bbbbbbbbbbbb'

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(NODE_UUID=self.NODE_UUID,
                               NODE_EGRESS_IP='1.1.1.2',
                               NODE_MESH_IP='1.1.1.2',
                               NETWORK_NODE_IP='1.1.1.2',
                               NODE_IS_NETWORK_NODE=True)
        self.config = mock.patch(
            'shakenfist.network.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # Inert mock for the cluster_operation_targets writer; the
        # enqueue path calls it during ``enqueue_cluster_operation``.
        self.mock_create_target = mock.patch(
            'shakenfist.mariadb.create_cluster_operation_target').start()
        self.addCleanup(mock.patch.stopall)

        # Guards against any accidental host mutation from inside
        # ``Network.<method>``: the old implementations called these
        # synchronously, the new implementations must not.
        self.mock_add_floating_ip = mock.patch(
            'shakenfist.util.concurrency.add_floating_ip').start()
        self.mock_remove_floating_ip = mock.patch(
            'shakenfist.util.concurrency.remove_floating_ip').start()
        self.mock_execute = mock.patch(
            'shakenfist.util.concurrency.execute').start()

    def _make_network(self):
        """Create a network and return the loaded ``Network``."""
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()
        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, provide_dhcp=True, provide_nat=True)
        return network.Network.from_db(network_uuid), network_uuid

    def _enqueue_spy(self):
        """Return a MagicMock wrapping the MockEtcd enqueue side-effect."""
        original = (
            self.mock_etcd._mariadb_create_and_enqueue_cluster_operation)
        return mock.MagicMock(side_effect=original)

    def test_add_floating_ip_enqueues_netop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.add_floating_ip('10.0.0.5', '192.168.1.10', [])

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_add_floating_ip', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual('10.0.0.5', metadata['floating_address'])
        self.assertEqual('192.168.1.10', metadata['inner_address'])
        self.assertEqual(PRIORITY.user_facing.name, metadata['priority'])

        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        # Host mutation must not fire from inside Network.add_floating_ip
        # any more — the work has moved to BridgedVXLanNetwork.
        self.mock_add_floating_ip.assert_not_called()

    def test_remove_floating_ip_enqueues_netop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.remove_floating_ip('10.0.0.5', '192.168.1.10', [])

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_remove_floating_ip', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual('10.0.0.5', metadata['floating_address'])
        self.assertEqual('192.168.1.10', metadata['inner_address'])

        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        self.mock_remove_floating_ip.assert_not_called()

    def test_route_address_enqueues_netipop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.route_address('10.0.0.5')

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_ip_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('route_address', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual('10.0.0.5', metadata['ip'])

        self.assertIsInstance(op, NetIPOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        # No ``ip route add`` execute calls should have fired from
        # inside Network.route_address.
        self.mock_execute.assert_not_called()

    def test_unroute_address_enqueues_netipop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.unroute_address('10.0.0.5')

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_ip_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('unroute_address', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual('10.0.0.5', metadata['ip'])

        self.assertIsInstance(op, NetIPOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        self.mock_execute.assert_not_called()

    def test_remove_nat_enqueues_netop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.remove_nat()

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_remove_nat', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))

        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        # No host-mutation utilities should fire inline.
        self.mock_add_floating_ip.assert_not_called()
        self.mock_remove_floating_ip.assert_not_called()
        self.mock_execute.assert_not_called()


class NetworkDnsmasqEnqueueTestCase(NetworkTestCase):
    """Tests for the phase 4d flip of the five dnsmasq-related methods
    on ``Network``.

    After phase 4d, each of ``update_dnsmasq``, ``remove_dnsmasq``,
    ``remove_dhcp_lease``, ``update_dns_entry``, and
    ``remove_dns_entry`` no longer mutates dnsmasq state inline. Each
    enqueues a cluster operation on the network-node queue family and
    returns the loaded op so callers can call ``op.raise_for_error()``.
    ``update_dns_entry`` and ``remove_dns_entry`` still mutate the
    network's ``hosteddns`` attribute synchronously — that's DB-only
    state.
    """

    NODE_UUID = '33333333-3333-4333-8333-cccccccccccc'

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(NODE_UUID=self.NODE_UUID,
                               NODE_EGRESS_IP='1.1.1.2',
                               NODE_MESH_IP='1.1.1.2',
                               NETWORK_NODE_IP='1.1.1.2',
                               NODE_IS_NETWORK_NODE=True)
        self.config = mock.patch(
            'shakenfist.network.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # Inert mock for the cluster_operation_targets writer; the
        # enqueue path calls it during ``enqueue_cluster_operation``.
        self.mock_create_target = mock.patch(
            'shakenfist.mariadb.create_cluster_operation_target').start()
        self.addCleanup(mock.patch.stopall)

        # Guard against any accidental dnsmasq host mutation. The
        # ``_get_dnsmasq_object`` factory must not be called from the
        # flipped methods — it stays in ``Network`` purely for
        # ``BridgedVXLanNetwork`` to consume via ``self.network``.
        self.mock_get_dnsmasq = mock.patch(
            'shakenfist.network.network.Network._get_dnsmasq_object'
        ).start()

    def _make_network(self, provide_dhcp=True, provide_dns=True,
                      provide_nat=True):
        """Create a network and return the loaded ``Network``."""
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()
        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid,
            provide_dhcp=provide_dhcp, provide_dns=provide_dns,
            provide_nat=provide_nat)
        return network.Network.from_db(network_uuid), network_uuid

    def _enqueue_spy(self):
        """Return a MagicMock wrapping the MockEtcd enqueue side-effect."""
        original = (
            self.mock_etcd._mariadb_create_and_enqueue_cluster_operation)
        return mock.MagicMock(side_effect=original)

    def test_update_dnsmasq_enqueues_netop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.update_dnsmasq()

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing_high_io',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_apply_update_dnsmasq', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual(
            PRIORITY.user_facing_high_io.name, metadata['priority'])

        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        # The dnsmasq lifecycle factory must not fire from inside
        # Network.update_dnsmasq any more.
        self.mock_get_dnsmasq.assert_not_called()

    def test_update_dnsmasq_skips_without_dhcp_or_dns(self):
        n, _ = self._make_network(provide_dhcp=False, provide_dns=False,
                                  provide_nat=False)
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            result = n.update_dnsmasq()

        spy.assert_not_called()
        self.assertIsNone(result)
        self.mock_get_dnsmasq.assert_not_called()

    def test_remove_dnsmasq_enqueues_netop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.remove_dnsmasq()

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_apply_remove_dnsmasq', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual(PRIORITY.user_facing.name, metadata['priority'])

        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        self.mock_get_dnsmasq.assert_not_called()

    def test_remove_dnsmasq_skips_without_dhcp_or_dns(self):
        n, _ = self._make_network(provide_dhcp=False, provide_dns=False,
                                  provide_nat=False)
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            result = n.remove_dnsmasq()

        spy.assert_not_called()
        self.assertIsNone(result)
        self.mock_get_dnsmasq.assert_not_called()

    def test_remove_dhcp_lease_enqueues_netmacaddripop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.remove_dhcp_lease('192.168.1.10', 'aa:bb:cc:dd:ee:ff')

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_macaddr_ip_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('remove_dhcp_lease', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual('192.168.1.10', metadata['ip'])
        self.assertEqual('aa:bb:cc:dd:ee:ff', metadata['mac_address'])
        self.assertEqual(PRIORITY.user_facing.name, metadata['priority'])

        self.assertIsInstance(op, NetMacaddrIPOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        self.mock_get_dnsmasq.assert_not_called()

    def test_remove_dhcp_lease_skips_without_dhcp_or_dns(self):
        n, _ = self._make_network(provide_dhcp=False, provide_dns=False,
                                  provide_nat=False)
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            result = n.remove_dhcp_lease('192.168.1.10', 'aa:bb:cc:dd:ee:ff')

        spy.assert_not_called()
        self.assertIsNone(result)
        self.mock_get_dnsmasq.assert_not_called()

    def test_update_dns_entry_mutates_and_enqueues(self):
        n, network_uuid = self._make_network()

        # Spy on add_event so we can confirm the audit event fires.
        with mock.patch.object(n, 'add_event') as mock_add_event:
            spy = self._enqueue_spy()
            with mock.patch(
                    'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                    spy):
                op = n.update_dns_entry('foo.example.com', '10.0.0.5')

        # Synchronous attribute update happened.
        attrs = n._ensure_attributes()
        self.assertEqual('10.0.0.5', attrs.hosteddns.get('foo.example.com'))

        # Audit event was emitted.
        mock_add_event.assert_called_once()
        event_args = mock_add_event.call_args
        self.assertEqual('update dns entry', event_args.args[1])
        self.assertEqual({'name': 'foo.example.com', 'value': '10.0.0.5'},
                         event_args.kwargs['extra'])

        # The dnsmasq restart was enqueued as
        # network_apply_update_dnsmasq on the high-io queue.
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing_high_io',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_apply_update_dnsmasq', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))

        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        self.mock_get_dnsmasq.assert_not_called()

    def test_update_dns_entry_skips_without_dns(self):
        n, _ = self._make_network(provide_dhcp=True, provide_dns=False,
                                  provide_nat=False)
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            result = n.update_dns_entry('foo.example.com', '10.0.0.5')

        spy.assert_not_called()
        self.assertIsNone(result)
        self.mock_get_dnsmasq.assert_not_called()

    def test_remove_dns_entry_mutates_and_enqueues(self):
        n, network_uuid = self._make_network()

        # Seed an entry to remove.
        attrs = n._ensure_attributes()
        attrs.hosteddns['foo.example.com'] = '10.0.0.5'
        n._save_attributes()

        with mock.patch.object(n, 'add_event') as mock_add_event:
            spy = self._enqueue_spy()
            with mock.patch(
                    'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                    spy):
                op = n.remove_dns_entry('foo.example.com')

        # Synchronous attribute mutation: name removed.
        attrs = n._ensure_attributes()
        self.assertNotIn('foo.example.com', attrs.hosteddns)

        # Audit event was emitted.
        mock_add_event.assert_called_once()
        event_args = mock_add_event.call_args
        self.assertEqual('remove dns entry', event_args.args[1])
        self.assertEqual({'name': 'foo.example.com'},
                         event_args.kwargs['extra'])

        # The dnsmasq restart was enqueued as
        # network_apply_update_dnsmasq on the high-io queue.
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing_high_io',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_apply_update_dnsmasq', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))

        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

        self.mock_get_dnsmasq.assert_not_called()

    def test_remove_dns_entry_enqueues_even_when_name_absent(self):
        # The pre-flip behaviour always enqueues the dnsmasq restart,
        # whether or not the named entry was actually present. The
        # flipped method preserves that contract.
        n, network_uuid = self._make_network()

        with mock.patch.object(n, 'add_event') as mock_add_event:
            spy = self._enqueue_spy()
            with mock.patch(
                    'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                    spy):
                op = n.remove_dns_entry('does-not-exist.example.com')

        # No audit event fired for the absent name.
        mock_add_event.assert_not_called()

        # But the dnsmasq restart still enqueued.
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        metadata = kwargs['metadata']
        self.assertIn('network_apply_update_dnsmasq', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))

        self.assertIsInstance(op, NetOp)
        self.mock_get_dnsmasq.assert_not_called()

    def test_remove_dns_entry_skips_without_dns(self):
        n, _ = self._make_network(provide_dhcp=True, provide_dns=False,
                                  provide_nat=False)
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            result = n.remove_dns_entry('foo.example.com')

        spy.assert_not_called()
        self.assertIsNone(result)
        self.mock_get_dnsmasq.assert_not_called()


class NetworkInternalSiblingCallsTestCase(NetworkTestCase):
    """Tests for the phase 4e migration of internal sibling calls.

    After phase 4e, the in-worker host-mutating methods
    ``create_on_network_node``, ``delete_on_network_node`` and
    ``remove_networkinterface_lease`` no longer call the enqueueing
    public ``Network`` methods for dnsmasq/nat work. They call the
    ``BridgedVXLanNetwork._apply_*`` methods directly to avoid the
    same-queue self-enqueue that would deadlock (or, for the
    Phase 3 latent bug, time out after ASYNC_OP_TIMEOUT).
    """

    NODE_UUID = '44444444-4444-4444-8444-dddddddddddd'

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(NODE_UUID=self.NODE_UUID,
                               NODE_EGRESS_IP='1.1.1.2',
                               NODE_MESH_IP='1.1.1.2',
                               NETWORK_NODE_IP='1.1.1.2',
                               NODE_IS_NETWORK_NODE=True)
        self.config = mock.patch(
            'shakenfist.network.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_create_target = mock.patch(
            'shakenfist.mariadb.create_cluster_operation_target').start()
        self.addCleanup(mock.patch.stopall)

        # ``get_lock(global_scope=False)`` returns a NodeLock which
        # requires a real socket. Replace it with a no-op context
        # manager for the duration of these tests.
        self.mock_node_lock = mock.patch(
            'shakenfist.util.concurrency.NodeLock',
            return_value=mock.MagicMock()).start()
        # The MagicMock returned by NodeLock() will support the
        # context-manager protocol automatically.

        # Stub the heavy host-mutation helpers used by
        # create_on_network_node and delete_on_network_node so the
        # tests can exercise the dnsmasq/nat call paths without
        # running real host commands.
        self.mock_create_vxlan = mock.patch(
            'shakenfist.util.concurrency.create_vxlan_interface').start()
        self.mock_create_ns = mock.patch(
            'shakenfist.util.concurrency.create_network_namespace').start()
        self.mock_execute = mock.patch(
            'shakenfist.util.concurrency.execute').start()
        self.mock_enable_nat = mock.patch(
            'shakenfist.util.concurrency.enable_nat').start()
        self.mock_check_for_interface = mock.patch(
            'shakenfist.util.network.check_for_interface',
            return_value=True).start()
        self.mock_create_interface = mock.patch(
            'shakenfist.util.network.create_interface').start()
        self.mock_get_interface_mtu = mock.patch(
            'shakenfist.util.network.get_interface_mtu',
            return_value=1500).start()
        self.mock_add_address = mock.patch(
            'shakenfist.util.network.add_address_to_interface').start()
        self.mock_get_default_routes = mock.patch(
            'shakenfist.util.network.get_default_routes',
            return_value=[]).start()
        self.mock_add_default_route = mock.patch(
            'shakenfist.util.network.add_default_route').start()
        self.mock_delete_default_route = mock.patch(
            'shakenfist.util.network.delete_default_route').start()
        self.mock_get_iface_addresses = mock.patch(
            'shakenfist.util.network.get_interface_addresses',
            return_value=[]).start()
        self.mock_os_path_exists = mock.patch(
            'os.path.exists', return_value=False).start()

        # Network.is_dead must report False so create_on_network_node
        # does not raise DeadNetwork mid-way.
        self.mock_is_dead = mock.patch(
            'shakenfist.network.network.Network.is_dead',
            return_value=False).start()
        # Suppress the per-active-node enqueue inside
        # delete_on_network_node so it does not assert on Nodes
        # iteration during the test.
        self.mock_nn_enqueue = mock.patch(
            'shakenfist.network.network.nn_create_and_enqueue').start()
        self.mock_nodes = mock.patch(
            'shakenfist.network.network.Nodes', return_value=[]).start()

    def _make_network(self, provide_dhcp=True, provide_dns=True,
                      provide_nat=False):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()
        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid,
            provide_dhcp=provide_dhcp, provide_dns=provide_dns,
            provide_nat=provide_nat)
        return network.Network.from_db(network_uuid), network_uuid

    def test_create_on_network_node_calls_apply_update_dnsmasq(self):
        """create_on_network_node must route dnsmasq startup through
        BridgedVXLanNetwork directly, not via the enqueueing
        Network.update_dnsmasq().
        """
        n, _ = self._make_network(provide_dhcp=True, provide_dns=True,
                                  provide_nat=False)

        with mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'BridgedVXLanNetwork._apply_update_dnsmasq') as mock_apply, \
                mock.patch.object(network.Network, 'update_dnsmasq') as \
                mock_public:
            n.create_on_network_node()

        mock_apply.assert_called_once_with()
        mock_public.assert_not_called()

    def test_create_on_network_node_skips_dnsmasq_when_not_required(self):
        """When neither DHCP nor DNS is provided, no dnsmasq work is
        kicked off at all.
        """
        n, _ = self._make_network(provide_dhcp=False, provide_dns=False,
                                  provide_nat=False)

        with mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'BridgedVXLanNetwork._apply_update_dnsmasq') as mock_apply, \
                mock.patch.object(network.Network, 'update_dnsmasq') as \
                mock_public:
            n.create_on_network_node()

        mock_apply.assert_not_called()
        mock_public.assert_not_called()

    def test_delete_on_network_node_calls_apply_remove_dnsmasq(self):
        """delete_on_network_node must route dnsmasq shutdown through
        BridgedVXLanNetwork directly, not via the enqueueing
        Network.remove_dnsmasq().
        """
        n, _ = self._make_network(provide_dhcp=True, provide_dns=True,
                                  provide_nat=False)

        with mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'BridgedVXLanNetwork._apply_remove_dnsmasq') as mock_apply, \
                mock.patch(
                    'shakenfist.network.bridged_vxlan_network.'
                    'BridgedVXLanNetwork._apply_remove_nat'), \
                mock.patch.object(network.Network, 'remove_dnsmasq') as \
                mock_public:
            n.delete_on_network_node()

        mock_apply.assert_called_once_with()
        mock_public.assert_not_called()

    def test_delete_on_network_node_calls_apply_remove_nat(self):
        """Phase 3 latent-bug fix regression test.

        ``delete_on_network_node`` previously did
        ``remove_nat_op = self.remove_nat(); remove_nat_op.raise_for_error()``
        from inside the ``_network_destroy`` dispatcher. Both ops live on
        the same networknode queue, so the new ``remove_nat`` op could
        never dequeue while the current handler was blocked --
        ``raise_for_error`` timed out after ASYNC_OP_TIMEOUT and the
        destroy op ended in ERROR. Phase 4e calls
        ``BridgedVXLanNetwork(self)._apply_remove_nat()`` directly.
        """
        n, _ = self._make_network(provide_dhcp=True, provide_dns=True,
                                  provide_nat=True)

        with mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'BridgedVXLanNetwork._apply_remove_nat') as mock_apply, \
                mock.patch(
                    'shakenfist.network.bridged_vxlan_network.'
                    'BridgedVXLanNetwork._apply_remove_dnsmasq'), \
                mock.patch.object(network.Network, 'remove_nat') as \
                mock_public:
            n.delete_on_network_node()

        mock_apply.assert_called_once_with()
        mock_public.assert_not_called()

    def test_delete_on_network_node_skips_dnsmasq_when_not_required(self):
        n, _ = self._make_network(provide_dhcp=False, provide_dns=False,
                                  provide_nat=False)

        with mock.patch(
                'shakenfist.network.bridged_vxlan_network.'
                'BridgedVXLanNetwork._apply_remove_dnsmasq') as mock_apply, \
                mock.patch(
                    'shakenfist.network.bridged_vxlan_network.'
                    'BridgedVXLanNetwork._apply_remove_nat'):
            n.delete_on_network_node()

        mock_apply.assert_not_called()

    def test_remove_networkinterface_lease_blocks_on_op(self):
        """remove_networkinterface_lease enqueues a NetMacaddrIPOp via
        the public Network.remove_dhcp_lease method and blocks on its
        completion via raise_for_error. Callers (NetworkInterface.delete
        invoked from node_inst_op._instance_delete, stray_nics and the
        cluster maintainer) all run outside the networknode queue, so
        the wait does not self-deadlock.
        """
        n, _ = self._make_network(provide_dhcp=True, provide_dns=True,
                                  provide_nat=False)

        fake_op = mock.MagicMock()
        with mock.patch.object(
                network.Network, 'remove_dhcp_lease',
                return_value=fake_op) as mock_remove:
            ni = mock.MagicMock()
            ni.ipv4 = '192.168.1.10'
            ni.macaddr = 'aa:bb:cc:dd:ee:ff'
            n.remove_networkinterface_lease(ni)

        mock_remove.assert_called_once_with(
            '192.168.1.10', 'aa:bb:cc:dd:ee:ff')
        fake_op.raise_for_error.assert_called_once_with()

    def test_remove_networkinterface_lease_handles_none_op(self):
        """When the network has neither DHCP nor DNS, ``remove_dhcp_lease``
        returns ``None``. The caller must not blow up on the absent op
        handle.
        """
        n, _ = self._make_network(provide_dhcp=False, provide_dns=False,
                                  provide_nat=False)

        with mock.patch.object(
                network.Network, 'remove_dhcp_lease',
                return_value=None) as mock_remove:
            ni = mock.MagicMock()
            ni.ipv4 = '192.168.1.10'
            ni.macaddr = 'aa:bb:cc:dd:ee:ff'
            # Should not raise.
            n.remove_networkinterface_lease(ni)

        mock_remove.assert_called_once_with(
            '192.168.1.10', 'aa:bb:cc:dd:ee:ff')

    def test_remove_networkinterface_lease_skips_when_no_ipv4(self):
        """When the NetworkInterface has no IPv4 assigned, there is no
        DHCP lease to release.
        """
        n, _ = self._make_network(provide_dhcp=True, provide_dns=True,
                                  provide_nat=False)

        with mock.patch.object(
                network.Network, 'remove_dhcp_lease') as mock_remove:
            ni = mock.MagicMock()
            ni.ipv4 = None
            ni.macaddr = 'aa:bb:cc:dd:ee:ff'
            n.remove_networkinterface_lease(ni)

        mock_remove.assert_not_called()
