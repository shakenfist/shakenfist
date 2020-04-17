import ipaddress
import mock
import testtools


from shakenfist.net import impl as net


class NetTestCase(testtools.TestCase):
    def test_init(self):
        net.Network(uuid='notauuid', vxlan_id=42, provide_dhcp=True,
                    provide_nat=True, physical_nic='eth0',
                    ipblock='192.168.1.0/24')

    def test_str(self):
        n = net.Network(uuid='notauuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        self.assertEqual('network(notauuid, vxid 42)', str(n))

    @mock.patch('shakenfist.util.get_random_ip',
                side_effect=['192.168.1.0', '192.168.1.1', '192.168.1.2',
                             '192.168.1.30', '192.168.1.42'])
    @mock.patch('shakenfist.db.impl.is_address_free',
                side_effect=[False, True])
    def test_allocate_ip(self, mock_is_free, mock_get_random_ip):
        n = net.Network(uuid='notauuid', vxlan_id=42, provide_dhcp=True,
                        provide_nat=True, physical_nic='eth0',
                        ipblock='192.168.1.0/24')
        addr = n.allocate_ip()
        self.assertEqual('192.168.1.1', str(n.router))
        self.assertEqual('192.168.1.2', str(n.dhcp_server))
        self.assertEqual('192.168.1.42', addr)
