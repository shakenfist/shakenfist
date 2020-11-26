import ipaddress

from shakenfist.ipmanager import IPManager
from shakenfist.tests import test_shakenfist


class IPManagerTestCase(test_shakenfist.ShakenFistTestCase):
    def test_init(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertEqual({
            ipaddress.IPv4Address('192.168.1.0'): True,
            ipaddress.IPv4Address('192.168.1.255'): True
        }, ipm.in_use)

    def test_get_address_at_index(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertEqual('192.168.1.1', str(ipm.get_address_at_index(1)))
        self.assertEqual('192.168.1.254', str(ipm.get_address_at_index(-2)))

    def test_is_free_and_reserve(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertEqual(True, ipm.is_free('192.168.1.24'))
        ipm.reserve('192.168.1.24')
        self.assertEqual(False, ipm.is_free('192.168.1.24'))
        self.assertEqual(False, ipm.reserve('192.168.1.24'))

        self.assertEqual(True, ipm.is_free('192.168.1.42'))
        self.assertEqual(True, ipm.reserve('192.168.1.42'))
        self.assertEqual(False, ipm.is_free('192.168.1.42'))
        ipm.release('192.168.1.42')
        self.assertEqual(True, ipm.is_free('192.168.1.42'))

    def test_get_free_random_ip(self):
        ipm = IPManager('uuid', '10.0.0.0/8')

        for _ in range(65025):
            ipm.get_random_free_address()

        # The extra two are the reserved network and broadcast
        # addresses
        self.assertEqual(65025 + 2, len(ipm.in_use))
