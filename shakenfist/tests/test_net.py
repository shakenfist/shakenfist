import mock
import testtools


from shakenfist import net


class NetTestCase(testtools.TestCase):
    def setUp(self):
        super(NetTestCase, self).setUp()

        self.ipmanager_get = mock.patch(
            'shakenfist.db.get_ipmanager')
        self.mock_ipmanager_get = self.ipmanager_get.start()

        self.ipmanager_persist = mock.patch(
            'shakenfist.db.persist_ipmanager')
        self.mock_ipmanager_persist = self.ipmanager_persist.start()

        self.etcd_client = mock.patch('etcd3.client')
        self.mock_etcd_client = self.etcd_client.start()

        self.etcd_lock = mock.patch('etcd3.Lock')
        self.mock_etcd_lock = self.etcd_lock.start()

    def test_init(self):
        net.Network(uuid='notauuid', vxlan_id=42, provide_dhcp=True,
                    provide_nat=True, physical_nic='eth0',
                    ipblock='192.168.1.0/24')

    def test_str(self):
        n = net.Network(uuid='notauuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertEqual('network(notauuid, vxid 42)', str(n))
