import mock
import testtools
import uuid

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import exceptions
from shakenfist import network
from shakenfist.config import SFConfig
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class NetworkTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(NetworkTestCase, self).setUp()

        self.ipmanager_get = mock.patch(
            'shakenfist.ipmanager.IPManager.from_db')
        self.mock_ipmanager_get = self.ipmanager_get.start()
        self.addCleanup(self.ipmanager_get.stop)

        self.ipmanager_persist = mock.patch(
            'shakenfist.db.persist_ipmanager')
        self.mock_ipmanager_persist = self.ipmanager_persist.start()
        self.addCleanup(self.ipmanager_persist.stop)

        self.etcd_lock = mock.patch('shakenfist.etcd.ActualLock')
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
        super(NetworkNormalNodeTestCase, self).setUp()
        fake_config = SFConfig(NODE_EGRESS_IP='1.1.1.2',
                               NODE_MESH_IP='1.1.1.2',
                               NETWORK_NODE_IP='1.1.1.2',
                               NODE_IS_NETWORK_NODE=False)
        self.config = mock.patch('shakenfist.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    #
    #  is_okay()
    #
    @mock.patch('shakenfist.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_yes(self, mock_is_dnsmasq, mock_is_created):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())

    @mock.patch('shakenfist.network.Network.is_created', return_value=False)
    @mock.patch('shakenfist.network.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_not_created(self, mock_is_dnsmasq, mock_is_created):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_dns(self, mock_is_dnsmasq, mock_is_created):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())


class NetworkNetNodeTestCase(NetworkTestCase):
    def setUp(self):
        super(NetworkNetNodeTestCase, self).setUp()

        fake_config = SFConfig(NODE_EGRESS_IP='1.1.1.2',
                               NODE_MESH_IP='1.1.1.2',
                               NETWORK_NODE_IP='1.1.1.2',
                               NODE_IS_NETWORK_NODE=True)
        self.config = mock.patch('shakenfist.network.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    #
    #  is_okay()
    #
    @mock.patch('shakenfist.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_yes(self, mock_is_dnsmasq, mock_is_created):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())

    @mock.patch('shakenfist.network.Network.is_created', return_value=False)
    @mock.patch('shakenfist.network.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_not_created(self, mock_is_dnsmasq, mock_is_created):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=True)
        n = network.Network.from_db(network_uuid)
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_masq(self, mock_is_dnsmasq, mock_is_created):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=True,
                                      provide_nat=False)
        n = network.Network.from_db(network_uuid)
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.network.Network.is_created', return_value=True)
    @mock.patch('shakenfist.network.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_masq_no_dhcp(self, mock_is_dnsmasq, mock_is_created):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, provide_dhcp=False,
                                      provide_nat=False)
        n = network.Network.from_db(network_uuid)
        self.assertTrue(n.is_okay())

    #
    # is_created()
    #
    @mock.patch('shakenfist.util.process.execute',
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

    @mock.patch('shakenfist.util.process.execute',
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

    @mock.patch('shakenfist.util.process.execute',
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
