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

        # The fan-out always tries to include the network node. Default
        # to "no network node found" so each test controls whether one
        # participates; this also isolates the tests from the
        # module-level cache in scheduler.get_network_node().
        self.mock_get_network_node = mock.patch(
            'shakenfist.scheduler.get_network_node',
            side_effect=exceptions.NoNetworkNode('test')).start()

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

    def test_ensure_mesh_fans_out_to_every_participating_node(self):
        # Set up a network with three network-interfaces backed by
        # three instances placed across three distinct hypervisors
        # (one of which is the local node). The fan-out must enqueue
        # one ensure_mesh op per node, each on that node's per-node
        # ``network`` queue. This is the bug fix for the asymmetric
        # mesh that broke ``test_single_virtual_networks_work``:
        # ensure_mesh used to only enqueue on the caller's node, so
        # other hypervisors never re-meshed.
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'meshnet', network_uuid,
            provide_dhcp=True, provide_nat=False)
        n = network.Network.from_db(network_uuid)

        # Build three fake interfaces, each pointing at a different
        # instance, and three fake instances each placed on a
        # different node (the local node plus two remotes). We mock
        # the property and the lookups rather than driving the
        # MockEtcd state machine through full instance / placement
        # flows -- the fan-out logic itself is what's under test.
        remote_a_uuid = '99999999-9999-4999-8999-aaaaaaaaaaaa'
        remote_b_uuid = '88888888-8888-4888-8888-bbbbbbbbbbbb'

        def _fake_ni(instance_uuid):
            ni = mock.MagicMock()
            ni.instance_uuid = instance_uuid
            return ni

        ifaces = [
            _fake_ni('inst-local'),
            _fake_ni('inst-remote-a'),
            _fake_ni('inst-remote-b'),
            # Duplicate instance_uuid in the iface list must not
            # produce a duplicate enqueue.
            _fake_ni('inst-remote-a'),
        ]

        def _fake_instance(inst_uuid):
            placement_by_uuid = {
                'inst-local': {'node': 'node-local.fqdn'},
                'inst-remote-a': {'node': 'node-a.fqdn'},
                'inst-remote-b': {'node': 'node-b.fqdn'},
            }
            inst = mock.MagicMock()
            inst.placement = placement_by_uuid[inst_uuid]
            return inst

        def _fake_node(fqdn):
            uuids_by_fqdn = {
                'node-local.fqdn': self.NODE_UUID,
                'node-a.fqdn': remote_a_uuid,
                'node-b.fqdn': remote_b_uuid,
            }
            node = mock.MagicMock()
            node.uuid = uuids_by_fqdn[fqdn]
            return node

        original = (
            self.mock_etcd._mariadb_create_and_enqueue_cluster_operation)
        spy = mock.MagicMock(side_effect=original)

        with mock.patch.object(
                network.Network, 'networkinterfaces',
                new_callable=mock.PropertyMock,
                return_value=ifaces), \
             mock.patch(
                'shakenfist.instance.Instance.from_db',
                side_effect=_fake_instance), \
             mock.patch(
                'shakenfist.network.network.Node.from_db',
                side_effect=_fake_node), \
             mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.ensure_mesh()

        # One enqueue per distinct participating hypervisor.
        queue_names = [c.kwargs['queue_name'] for c in spy.call_args_list]
        self.assertEqual(3, len(queue_names))
        self.assertIn(
            f'{self.NODE_UUID}-network-user_facing', queue_names)
        self.assertIn(
            f'{remote_a_uuid}-network-user_facing', queue_names)
        self.assertIn(
            f'{remote_b_uuid}-network-user_facing', queue_names)

        # The returned op corresponds to the local-node enqueue so
        # the caller's raise_for_error() still polls a meaningful
        # state -- the local worker is the most likely participant
        # that the caller has direct knowledge of.
        self.assertIsInstance(op, NetOp)
        local_call = next(
            c for c in spy.call_args_list
            if c.kwargs['queue_name'].startswith(self.NODE_UUID))
        self.assertEqual(
            local_call.kwargs['metadata']['uuid'], str(op.uuid))

        # No host mutation from this code path.
        self.mock_ensure_vxlan_mesh.assert_not_called()

    def test_ensure_mesh_falls_back_to_local_when_no_participants(self):
        # An empty network with no interfaces has no participants to
        # fan out to. The fallback rule is: still enqueue on the
        # local node so the caller has something to block on with
        # raise_for_error(). This keeps the existing
        # "ensure_mesh during network bootstrap" callers working.
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'emptynet', network_uuid,
            provide_dhcp=True, provide_nat=False)
        n = network.Network.from_db(network_uuid)

        original = (
            self.mock_etcd._mariadb_create_and_enqueue_cluster_operation)
        spy = mock.MagicMock(side_effect=original)
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.ensure_mesh()

        spy.assert_called_once()
        self.assertEqual(
            f'{self.NODE_UUID}-network-user_facing',
            spy.call_args.kwargs['queue_name'])
        self.assertIsInstance(op, NetOp)

    def test_ensure_mesh_always_includes_network_node(self):
        # The network node hosts the netns side of every network, so it
        # participates in every mesh even when it hosts no instance on
        # the network. Without this the network node's FDB never gains
        # the flood entry for an instance's hypervisor, and inbound
        # floating traffic dies as soon as the learned FDB entry for an
        # idle guest ages out.
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_node_uuid = '77777777-7777-4777-8777-cccccccccccc'
        remote_uuid = '99999999-9999-4999-8999-aaaaaaaaaaaa'

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'meshnet', network_uuid,
            provide_dhcp=True, provide_nat=False)
        n = network.Network.from_db(network_uuid)

        ni = mock.MagicMock()
        ni.instance_uuid = 'inst-remote'

        inst = mock.MagicMock()
        inst.placement = {'node': 'node-remote.fqdn'}

        remote_node = mock.MagicMock()
        remote_node.uuid = remote_uuid

        network_node = mock.MagicMock()
        network_node.uuid = network_node_uuid
        self.mock_get_network_node.side_effect = None
        self.mock_get_network_node.return_value = network_node

        original = (
            self.mock_etcd._mariadb_create_and_enqueue_cluster_operation)
        spy = mock.MagicMock(side_effect=original)

        with mock.patch.object(
                network.Network, 'networkinterfaces',
                new_callable=mock.PropertyMock,
                return_value=[ni]), \
             mock.patch(
                'shakenfist.instance.Instance.from_db',
                return_value=inst), \
             mock.patch(
                'shakenfist.network.network.Node.from_db',
                return_value=remote_node), \
             mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.ensure_mesh()

        # One enqueue for the instance's hypervisor, one for the
        # network node.
        queue_names = [c.kwargs['queue_name'] for c in spy.call_args_list]
        self.assertEqual(2, len(queue_names))
        self.assertIn(
            f'{remote_uuid}-network-user_facing', queue_names)
        self.assertIn(
            f'{network_node_uuid}-network-user_facing', queue_names)
        self.assertIsInstance(op, NetOp)

    def test_ensure_mesh_network_node_not_duplicated(self):
        # When the network node also hosts an instance on the network it
        # is already in the fan-out set; it must not be enqueued twice.
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_node_uuid = '77777777-7777-4777-8777-cccccccccccc'

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'meshnet', network_uuid,
            provide_dhcp=True, provide_nat=False)
        n = network.Network.from_db(network_uuid)

        ni = mock.MagicMock()
        ni.instance_uuid = 'inst-on-network-node'

        inst = mock.MagicMock()
        inst.placement = {'node': 'network-node.fqdn'}

        placed_node = mock.MagicMock()
        placed_node.uuid = network_node_uuid

        network_node = mock.MagicMock()
        network_node.uuid = network_node_uuid
        self.mock_get_network_node.side_effect = None
        self.mock_get_network_node.return_value = network_node

        original = (
            self.mock_etcd._mariadb_create_and_enqueue_cluster_operation)
        spy = mock.MagicMock(side_effect=original)

        with mock.patch.object(
                network.Network, 'networkinterfaces',
                new_callable=mock.PropertyMock,
                return_value=[ni]), \
             mock.patch(
                'shakenfist.instance.Instance.from_db',
                return_value=inst), \
             mock.patch(
                'shakenfist.network.network.Node.from_db',
                return_value=placed_node), \
             mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            n.ensure_mesh()

        spy.assert_called_once()
        self.assertEqual(
            f'{network_node_uuid}-network-user_facing',
            spy.call_args.kwargs['queue_name'])


class NetworkMeshDesiredNodeIPsTestCase(NetworkTestCase):
    """Tests for ``Network.mesh_desired_node_ips``, the shared source of
    truth for what a node's VXLAN flood mesh should contain. The
    enumeration was lifted from ``BridgedVXLanNetwork._apply_ensure_mesh``
    so the writer and the ``is_mesh_okay`` auditor can never disagree.
    """

    def setUp(self):
        super().setUp()
        # NODE_MESH_IP is offset from NETWORK_NODE_IP so the network
        # node IP is included in the computed mesh -- the enumeration
        # excludes the running host from its own mesh, so distinct
        # values are required to exercise the "network node IP makes it
        # into the mesh" branch.
        fake_config = SFConfig(NODE_EGRESS_IP='10.0.0.2',
                               NODE_MESH_IP='10.0.0.2',
                               NETWORK_NODE_IP='10.0.0.1',
                               NODE_IS_NETWORK_NODE=False)
        self.config = mock.patch(
            'shakenfist.network.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'meshnet', network_uuid, provide_dhcp=True, provide_nat=False)
        self.network = network.Network.from_db(network_uuid)

    def _fake_ni(self, instance_uuid):
        ni = mock.MagicMock()
        ni.instance_uuid = instance_uuid
        return ni

    def test_collects_node_ips(self):
        ifaces = [
            self._fake_ni('inst-a'),
            self._fake_ni('inst-b'),
            # Duplicate iface for inst-a to confirm dedupe.
            self._fake_ni('inst-a'),
        ]

        def instance_from_db(instance_uuid):
            placements = {
                'inst-a': {'node': 'sf1.example.com'},
                'inst-b': {'node': 'sf2.example.com'},
            }
            inst = mock.Mock()
            inst.placement = placements[instance_uuid]
            return inst

        def node_from_db(fqdn):
            ips = {
                'sf1.example.com': '10.0.0.3',
                'sf2.example.com': '10.0.0.4',
            }
            n = mock.Mock()
            n.ip = ips[fqdn]
            return n

        with mock.patch.object(
                network.Network, 'networkinterfaces',
                new_callable=mock.PropertyMock,
                return_value=ifaces), \
             mock.patch(
                'shakenfist.instance.Instance.from_db',
                side_effect=instance_from_db), \
             mock.patch(
                'shakenfist.network.network.Node.from_db',
                side_effect=node_from_db):
            self.assertEqual(
                {'10.0.0.1', '10.0.0.3', '10.0.0.4'},
                self.network.mesh_desired_node_ips())

    def test_skips_missing_instance(self):
        with mock.patch.object(
                network.Network, 'networkinterfaces',
                new_callable=mock.PropertyMock,
                return_value=[self._fake_ni('inst-missing')]), \
             mock.patch(
                'shakenfist.instance.Instance.from_db',
                return_value=None):
            # Only NETWORK_NODE_IP makes it onto the mesh.
            self.assertEqual(
                {'10.0.0.1'}, self.network.mesh_desired_node_ips())

    def test_skips_unplaced_instance(self):
        unplaced = mock.Mock()
        unplaced.placement = None

        with mock.patch.object(
                network.Network, 'networkinterfaces',
                new_callable=mock.PropertyMock,
                return_value=[self._fake_ni('inst-unplaced')]), \
             mock.patch(
                'shakenfist.instance.Instance.from_db',
                return_value=unplaced):
            self.assertEqual(
                {'10.0.0.1'}, self.network.mesh_desired_node_ips())

    def test_omits_network_node_when_self(self):
        # When this node *is* the network node, NETWORK_NODE_IP must not
        # be added (it would be us, and we never include ourselves).
        self.mock_config.NODE_MESH_IP = '10.0.0.1'

        placed = mock.Mock()
        placed.placement = {'node': 'sf2.example.com'}
        placed_node = mock.Mock()
        placed_node.ip = '10.0.0.4'

        with mock.patch.object(
                network.Network, 'networkinterfaces',
                new_callable=mock.PropertyMock,
                return_value=[self._fake_ni('inst-a')]), \
             mock.patch(
                'shakenfist.instance.Instance.from_db',
                return_value=placed), \
             mock.patch(
                'shakenfist.network.network.Node.from_db',
                return_value=placed_node):
            self.assertEqual(
                {'10.0.0.4'}, self.network.mesh_desired_node_ips())

    def test_omits_node_when_self(self):
        # Nodes whose IP equals NODE_MESH_IP are intentionally not added
        # to the mesh (no self-loop in the FDB).
        placed = mock.Mock()
        placed.placement = {'node': 'self.example.com'}
        placed_node = mock.Mock()
        placed_node.ip = '10.0.0.2'  # equals NODE_MESH_IP

        with mock.patch.object(
                network.Network, 'networkinterfaces',
                new_callable=mock.PropertyMock,
                return_value=[self._fake_ni('inst-a')]), \
             mock.patch(
                'shakenfist.instance.Instance.from_db',
                return_value=placed), \
             mock.patch(
                'shakenfist.network.network.Node.from_db',
                return_value=placed_node):
            self.assertEqual(
                {'10.0.0.1'}, self.network.mesh_desired_node_ips())


class NetworkIsMeshOkayTestCase(NetworkTestCase):
    """Tests for ``Network.is_mesh_okay``, the auditor half of the mesh.

    The maintain loop calls this on every pass; a False return triggers
    a targeted ensure_mesh repair on this node rather than a full
    network recreate.
    """

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(NODE_EGRESS_IP='10.0.0.2',
                               NODE_MESH_IP='10.0.0.2',
                               NETWORK_NODE_IP='10.0.0.1',
                               NODE_IS_NETWORK_NODE=False)
        self.config = mock.patch(
            'shakenfist.network.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'meshnet', network_uuid, provide_dhcp=True, provide_nat=False)
        self.network = network.Network.from_db(network_uuid)

        self.mock_discover = mock.patch(
            'shakenfist.util.network.discover_mesh_flood_ips').start()
        self.mock_desired = mock.patch(
            'shakenfist.network.network.Network.mesh_desired_node_ips').start()
        self.addCleanup(mock.patch.stopall)

    def test_mesh_matches(self):
        self.mock_discover.return_value = {'10.0.0.1', '10.0.0.3'}
        self.mock_desired.return_value = {'10.0.0.1', '10.0.0.3'}
        self.assertTrue(self.network.is_mesh_okay())

    def test_mesh_missing_entry_is_drift(self):
        # The raptor failure mode: the network node's FDB has no flood
        # entry for the hypervisor hosting the network's only instance.
        self.mock_discover.return_value = set()
        self.mock_desired.return_value = {'10.0.0.3'}
        self.assertFalse(self.network.is_mesh_okay())

    def test_mesh_stale_entry_is_drift(self):
        self.mock_discover.return_value = {'10.0.0.1', '10.0.0.9'}
        self.mock_desired.return_value = {'10.0.0.1'}
        self.assertFalse(self.network.is_mesh_okay())

    def test_missing_vxlan_interface_is_not_mesh_drift(self):
        # A missing vxlan interface is is_created()'s drift to detect;
        # the mesh audit must not double-report it.
        self.mock_discover.return_value = None
        self.assertTrue(self.network.is_mesh_okay())
        self.mock_desired.assert_not_called()

    def test_floating_network_short_circuits(self):
        self.mock_etcd.create_network(
            'floatnet', str(FLOATING_NETWORK_UUID),
            provide_dhcp=False, provide_nat=False)
        fn = network.Network.from_db(str(FLOATING_NETWORK_UUID))
        self.assertTrue(fn.is_mesh_okay())
        self.mock_discover.assert_not_called()


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


class NetworkLifecycleEnqueueTestCase(NetworkTestCase):
    """Tests for the phase 5d flip of the four ``Network`` lifecycle
    methods.

    After phase 5d, each of ``create_on_hypervisor``,
    ``create_on_network_node``, ``delete_on_hypervisor`` and
    ``delete_on_network_node`` no longer mutates host state inline.
    Each enqueues a cluster operation (a ``node_net_op`` for the
    per-hypervisor pair, a ``net_op`` for the network-node pair) and
    returns the loaded op so callers can call ``op.raise_for_error()``.
    ``Network.enable_nat`` is gone from the public surface entirely.
    """

    NODE_UUID = '55555555-5555-4555-8555-eeeeeeeeeeee'

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

    def _make_network(self, provide_dhcp=True, provide_dns=True,
                      provide_nat=True):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()
        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid,
            provide_dhcp=provide_dhcp, provide_dns=provide_dns,
            provide_nat=provide_nat)
        return network.Network.from_db(network_uuid), network_uuid

    def _enqueue_spy(self):
        original = (
            self.mock_etcd._mariadb_create_and_enqueue_cluster_operation)
        return mock.MagicMock(side_effect=original)

    def test_create_on_hypervisor_enqueues_node_net_op(self):
        from shakenfist.operations.node_net_op import NodeNetOp
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.create_on_hypervisor()

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            f'{self.NODE_UUID}-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('node_net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_apply_create_hypervisor', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual(self.NODE_UUID, str(metadata['node_uuid']))
        self.assertEqual(PRIORITY.user_facing.name, metadata['priority'])

        self.assertIsInstance(op, NodeNetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

    def test_create_on_network_node_enqueues_netop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.create_on_network_node()

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn(
            'network_apply_create_network_node', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual(PRIORITY.user_facing.name, metadata['priority'])

        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

    def test_create_on_network_node_skips_when_deleted(self):
        """A deleted network must not be re-created on the network node.

        ``create_on_network_node`` short-circuits when the network's
        state is ``deleted`` and returns ``None`` without enqueueing
        anything.
        """
        n, _ = self._make_network()
        n.state = dbo.STATE_DELETED

        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            result = n.create_on_network_node()

        spy.assert_not_called()
        self.assertIsNone(result)

    def test_delete_on_hypervisor_enqueues_node_net_op(self):
        from shakenfist.operations.node_net_op import NodeNetOp
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.delete_on_hypervisor()

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            f'{self.NODE_UUID}-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('node_net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn('network_destroy', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual(self.NODE_UUID, str(metadata['node_uuid']))
        self.assertEqual(PRIORITY.user_facing.name, metadata['priority'])

        self.assertIsInstance(op, NodeNetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

    def test_delete_on_network_node_enqueues_netop(self):
        n, network_uuid = self._make_network()
        spy = self._enqueue_spy()
        with mock.patch(
                'shakenfist.mariadb.create_and_enqueue_cluster_operation',
                spy):
            op = n.delete_on_network_node()

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(
            'networknode-clusteroperation-user_facing',
            kwargs['queue_name'])
        self.assertEqual('net_op', kwargs['operation_type'])
        metadata = kwargs['metadata']
        self.assertIn(
            'network_apply_delete_network_node', metadata['tasks'])
        self.assertEqual(str(network_uuid), str(metadata['network_uuid']))
        self.assertEqual(PRIORITY.user_facing.name, metadata['priority'])

        self.assertIsInstance(op, NetOp)
        self.assertEqual(metadata['uuid'], str(op.uuid))

    def test_enable_nat_method_is_removed(self):
        """``Network.enable_nat`` is no longer part of the public API.

        Phase 5b lifted the only call site into
        ``BridgedVXLanNetwork._apply_enable_nat``; phase 5d deletes
        the method from ``Network`` entirely.
        """
        n, _ = self._make_network()
        self.assertIsNone(getattr(n, 'enable_nat', None))


class NetworkInternalSiblingCallsTestCase(NetworkTestCase):
    """Tests for ``remove_networkinterface_lease``.

    The phase 4e sibling-call tests for ``create_on_network_node`` and
    ``delete_on_network_node`` were removed by phase 5d when those
    method bodies moved to ``BridgedVXLanNetwork``. The
    ``remove_networkinterface_lease`` tests remain — that method still
    enqueues via ``Network.remove_dhcp_lease`` and blocks on the op.
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

    def test_remove_networkinterface_lease_tolerates_op_timeout(self):
        """An OperationTimeout from raise_for_error means the op is
        still queued, not that it failed -- under load the networknode
        queue can back up past API_ASYNC_WAIT and the op executes when
        dequeued. The timeout must not propagate (it used to fail the
        calling instance delete, wedging the instance in delete-wait),
        but it should leave an audit event behind.
        """
        n, _ = self._make_network(provide_dhcp=True, provide_dns=True,
                                  provide_nat=False)

        fake_op = mock.MagicMock()
        fake_op.uuid = str(uuid.uuid4())
        fake_op.raise_for_error.side_effect = exceptions.OperationTimeout(
            'operation did not reach terminal state within 60s')
        with mock.patch.object(
                network.Network, 'remove_dhcp_lease',
                return_value=fake_op):
            with mock.patch.object(
                    network.Network, 'add_event') as mock_add_event:
                ni = mock.MagicMock()
                ni.ipv4 = '192.168.1.10'
                ni.macaddr = 'aa:bb:cc:dd:ee:ff'
                ni.uuid = str(uuid.uuid4())
                # Should not raise.
                n.remove_networkinterface_lease(ni)

        fake_op.raise_for_error.assert_called_once_with()
        mock_add_event.assert_called_once()

    def test_remove_networkinterface_lease_propagates_op_failure(self):
        """A genuine op failure (NetworkOperationFailed) must still
        propagate to the caller -- only queue latency is tolerated.
        """
        n, _ = self._make_network(provide_dhcp=True, provide_dns=True,
                                  provide_nat=False)

        fake_op = mock.MagicMock()
        fake_op.raise_for_error.side_effect = \
            exceptions.NetworkOperationFailed(mock.MagicMock())
        with mock.patch.object(
                network.Network, 'remove_dhcp_lease',
                return_value=fake_op):
            ni = mock.MagicMock()
            ni.ipv4 = '192.168.1.10'
            ni.macaddr = 'aa:bb:cc:dd:ee:ff'
            self.assertRaises(
                exceptions.NetworkOperationFailed,
                n.remove_networkinterface_lease, ni)

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
