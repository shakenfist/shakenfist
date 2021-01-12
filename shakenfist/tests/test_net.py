import mock
import testtools

from shakenfist import exceptions
from shakenfist import net
from shakenfist.baseobject import State
from shakenfist.config import SFConfig
from shakenfist.tests import test_shakenfist


class NetworkTestCase(test_shakenfist.ShakenFistTestCase):
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

        self.etcd_client = mock.patch('etcd3.client')
        self.mock_etcd_client = self.etcd_client.start()
        self.addCleanup(self.etcd_client.stop)

        self.etcd_lock = mock.patch('shakenfist.etcd.ActualLock')
        self.mock_etcd_lock = self.etcd_lock.start()
        self.addCleanup(self.etcd_lock.stop)


class NetworkGeneralTestCase(NetworkTestCase):
    def test_init(self):
        net.Network({
            'uuid': 'notauuid',
            'vxid': 2,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })

    def test_str(self):
        n = net.Network({
            'uuid': 'notauuid',
            'vxid': 42,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertEqual('network(notauuid)', str(n))


class NetworkNormalNodeTestCase(NetworkTestCase):
    def setUp(self):
        super(NetworkNormalNodeTestCase, self).setUp()
        fake_config = SFConfig(NODE_IP="1.1.1.2", NETWORK_NODE_IP="1.1.1.2")
        self.config = mock.patch('shakenfist.config.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    #
    #  is_okay()
    #
    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_yes(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network({
            'uuid': 'actualuuid',
            'vxid': 42,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertTrue(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=False)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_not_created(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network({
            'uuid': 'actualuuid',
            'vxid': 42,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=False)
    @mock.patch('shakenfist.util.config', SFConfig(NODE_IP="1.1.1.1", NETWORK_NODE_IP="1.1.1.2"))
    def test_is_okay_no_dns(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network({
            'uuid': 'actualuuid',
            'vxid': 42,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertTrue(n.is_okay())


class NetworkNetNodeTestCase(NetworkTestCase):
    def setUp(self):
        super(NetworkNetNodeTestCase, self).setUp()

        fake_config = SFConfig(NODE_IP="1.1.1.2", NETWORK_NODE_IP="1.1.1.2")
        self.config = mock.patch('shakenfist.config.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    #
    #  is_okay()
    #
    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_yes(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network({
            'uuid': 'actualuuid',
            'vxid': 42,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertTrue(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=False)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_not_created(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network({
            'uuid': 'actualuuid',
            'vxid': 42,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_masq(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network({
            'uuid': 'actualuuid',
            'vxid': 42,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': False,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_masq_no_dhcp(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network({
            'uuid': 'actualuuid',
            'vxid': 42,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': False,
            'provide_nat': False,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertTrue(n.is_okay())

    #
    # is_created()
    #
    pgrep = ('1: br-vxlan-5: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500'
             + ''' qdisc noqueue state UP mode DEFAULT group default qlen 1000
link/ether 1a:46:97:a1:c2:3a brd ff:ff:ff:ff:ff:ff
''')

    @mock.patch('shakenfist.util.execute',
                return_value=(pgrep, ''))
    def test_is_created_yes(self, mock_execute):
        n = net.Network({
            'uuid': '8abbc9a6-d923-4441-b498-4f8e3c166804',
            'vxid': 5,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertTrue(n.is_created())

    pgrep = ('1: br-vxlan-5: <BROADCAST,MULTICAST,DOWN,LOWER_UP> mtu 1500'
             + ''' qdisc noqueue state UP mode DEFAULT group default qlen 1000
link/ether 1a:46:97:a1:c2:3a brd ff:ff:ff:ff:ff:ff
''')

    @mock.patch('shakenfist.util.execute',
                return_value=(pgrep, ''))
    def test_is_created_no(self, mock_execute):
        n = net.Network({
            'uuid': '8abbc9a6-d923-4441-b498-4f8e3c166804',
            'vxid': 1,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertFalse(n.is_created())

    pgrep = 'Device "br-vxlan-45" does not exist.'

    @mock.patch('shakenfist.util.execute',
                return_value=(pgrep, ''))
    def test_is_created_no_bridge(self, mock_execute):
        n = net.Network({
            'uuid': '8abbc9a6-d923-4441-b498-4f8e3c166804',
            'vxid': 5,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        self.assertFalse(n.is_created())

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.net.Network._db_get_attribute',
                side_effect=[
                    {'value': 'created', 'update_time': 0},
                    {'value': 'created', 'update_time': 0},
                    {'value': 'created', 'update_time': 0},
                    {'value': 'deleting', 'update_time': 0},
                    {'value': 'error', 'update_time': 0},
                    {'value': 'deleted', 'update_time': 0},
                    {'value': 'deleted', 'update_time': 0},
                    ])
    @mock.patch('shakenfist.net.Network._db_set_attribute')
    @mock.patch('shakenfist.etcd.put')
    def test_set_state_valid(
            self, mock_put, mock_attribute_set, mock_state_get, mock_lock):

        n = net.Network({
            'uuid': '8abbc9a6-d923-4441-b498-4f8e3c166804',
            'vxid': 5,
            'name': 'bobnet',
            'namespace': 'finitespace',
            'provide_dhcp': True,
            'provide_nat': True,
            'physical_nic': 'eth0',
            'netblock': '192.168.1.0/24'
        })
        with testtools.ExpectedException(exceptions.InvalidStateException):
            n.state = 'initial'
        n.state = 'deleting'
        n.state = 'error'
        n.state = 'deleted'
        with testtools.ExpectedException(exceptions.InvalidStateException):
            n.state = 'created'
