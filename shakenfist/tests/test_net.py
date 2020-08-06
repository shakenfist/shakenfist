import mock
import testtools

from oslo_concurrency import processutils

from shakenfist import net
from shakenfist import config


class NetworkTestCase(testtools.TestCase):
    def setUp(self):
        super(NetworkTestCase, self).setUp()

        self.ipmanager_get = mock.patch(
            'shakenfist.db.get_ipmanager')
        self.mock_ipmanager_get = self.ipmanager_get.start()
        self.addCleanup(self.ipmanager_get.stop)

        self.ipmanager_persist = mock.patch(
            'shakenfist.db.persist_ipmanager')
        self.mock_ipmanager_persist = self.ipmanager_persist.start()
        self.addCleanup(self.ipmanager_persist.stop)

        self.etcd_client = mock.patch('etcd3.client')
        self.mock_etcd_client = self.etcd_client.start()
        self.addCleanup(self.etcd_client.stop)

        self.etcd_lock = mock.patch('etcd3.Lock')
        self.mock_etcd_lock = self.etcd_lock.start()
        self.addCleanup(self.etcd_lock.stop)


class NetworkGeneralTestCase(NetworkTestCase):
    def test_init(self):
        net.Network(uuid='notauuid', vxlan_id=42, provide_dhcp=True,
                    provide_nat=True, physical_nic='eth0',
                    ipblock='192.168.1.0/24')

    def test_str(self):
        n = net.Network(uuid='notauuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertEqual('network(notauuid, vxid 42)', str(n))

        config.CONFIG_DEFAULTS['NODE_IP'] = '1.1.1.1'
        config.CONFIG_DEFAULTS['NETWORK_NODE_IP'] = '2.2.2.2'
        config.parsed = config.Config()

        self.assertFalse(net.is_network_node())


class NetworkNormalNodeTestCase(NetworkTestCase):
    def setUp(self):
        super(NetworkNormalNodeTestCase, self).setUp()
        config.CONFIG_DEFAULTS['NODE_IP'] = '1.1.1.2'
        config.CONFIG_DEFAULTS['NETWORK_NODE_IP'] = '1.1.1.1'
        config.parsed = config.Config()

    #
    #  is_okay()
    #
    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_yes(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network(uuid='actualuuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertTrue(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=False)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_not_created(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network(uuid='actualuuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_dns(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network(uuid='actualuuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertTrue(n.is_okay())


class NetworkNetNodeTestCase(NetworkTestCase):
    def setUp(self):
        super(NetworkNetNodeTestCase, self).setUp()
        config.CONFIG_DEFAULTS['NODE_IP'] = '1.1.1.1'
        config.CONFIG_DEFAULTS['NETWORK_NODE_IP'] = '1.1.1.1'
        config.parsed = config.Config()

    #
    #  is_okay()
    #
    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_yes(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network(uuid='actualuuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertTrue(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=False)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=True)
    def test_is_okay_not_created(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network(uuid='actualuuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_masq(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network(uuid='actualuuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertFalse(n.is_okay())

    @mock.patch('shakenfist.net.Network.is_created', return_value=True)
    @mock.patch('shakenfist.net.Network.is_dnsmasq_running', return_value=False)
    def test_is_okay_no_masq_no_dhcp(self, mock_is_dnsmasq, mock_is_created):
        n = net.Network(uuid='actualuuid', vxlan_id=42, provide_dhcp=False,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertFalse(n.is_okay())

    #
    # is_created()
    #
    pgrep = ('1: br-vxlan-5: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500'
             + ''' qdisc noqueue state UP mode DEFAULT group default qlen 1000
link/ether 1a:46:97:a1:c2:3a brd ff:ff:ff:ff:ff:ff
''')

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(pgrep, ''))
    def test_is_created_yes(self, mock_execute):
        n = net.Network(uuid='8abbc9a6-d923-4441-b498-4f8e3c166804',
                        vxlan_id=5, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertTrue(n.is_created())

    pgrep = ('1: br-vxlan-5: <BROADCAST,MULTICAST,DOWN,LOWER_UP> mtu 1500'
             + ''' qdisc noqueue state UP mode DEFAULT group default qlen 1000
link/ether 1a:46:97:a1:c2:3a brd ff:ff:ff:ff:ff:ff
''')

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(pgrep, ''))
    def test_is_created_no(self, mock_execute):
        n = net.Network(uuid='1111111-d923-4441-b498-4f8e3c166804',
                        vxlan_id=111, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertFalse(n.is_created())

    pgrep = 'Device "br-vxlan-45" does not exist.'

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(pgrep, ''))
    def test_is_created_no_bridge(self, mock_execute):
        n = net.Network(uuid='1111111-d923-4441-b498-4f8e3c166804',
                        vxlan_id=111, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertFalse(n.is_created())
    #
    # is_dnsmasq_running()
    #
    pgrep = '''
5438 dnsmasq --conf-file=/srv/shakenfist/dhcp/29e83e99-ce0c-4340-9eab-4fc07217d002/config
5812 dnsmasq --conf-file=/srv/shakenfist/dhcp/8abbc9a6-d923-4441-b498-4f8e3c166804/config
6386 dnsmasq --conf-file=/srv/shakenfist/dhcp/0f5c73eb-708a-4f7c-b4d5-3b63146cac18/config
'''

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(pgrep, ''))
    def test_is_dnsmasq_running_yes(self, mock_execute):
        n = net.Network(uuid='8abbc9a6-d923-4441-b498-4f8e3c166804',
                        vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertTrue(n.is_dnsmasq_running())

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(pgrep, ''))
    def test_is_dnsmasq_running_no(self, mock_execute):
        n = net.Network(uuid='11111111-d923-4441-b498-4f8e3c166804',
                        vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertFalse(n.is_dnsmasq_running())

    @mock.patch('oslo_concurrency.processutils.execute',
                side_effect=processutils.ProcessExecutionError)
    def test_is_dnsmasq_running_no_processes(self, mock_execute):
        n = net.Network(uuid='11111111-d923-4441-b498-4f8e3c166804',
                        vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertFalse(n.is_dnsmasq_running())
