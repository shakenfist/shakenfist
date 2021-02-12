import mock

from shakenfist import exceptions
from shakenfist.ipmanager import IPManager
from shakenfist.tests import test_shakenfist


class IPManagerTestCase(test_shakenfist.ShakenFistTestCase):
    def test_init(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertEqual(
            {
                '192.168.1.0': True,
                '192.168.1.255': True
            }, ipm.in_use)

    def test_init_with_inuse(self):
        ipm = IPManager('uuid', '192.168.1.0/24',
                        ['192.168.1.10', '192.168.1.11'])
        self.assertEqual(
            {
                '192.168.1.0': True,
                '192.168.1.255': True,
                '192.168.1.10': True,
                '192.168.1.11': True
            }, ipm.in_use)

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.ipmanager.IPManager.persist')
    def test_new(self, mock_persist, mock_lock):
        ipm = IPManager.new('uuid', '192.168.1.0/24')
        self.assertEqual(
            {
                '192.168.1.0': True,
                '192.168.1.255': True,
                '192.168.1.1': True
            }, ipm.in_use)

    @mock.patch('shakenfist.db.get_ipmanager',
                return_value={
                    'ipmanager.v1': {
                        'in_use': [
                            '192.168.20.0',
                            '192.168.20.255',
                            '192.168.20.1',
                            '192.168.20.75',
                            '192.168.20.101'
                        ],
                        'ipblock': '192.168.20.0/24'
                    }}
                )
    def test_from_db(self, mock_get):
        ipm = IPManager.from_db('floating')
        self.assertEqual(
            {
                '192.168.20.0': True,
                '192.168.20.255': True,
                '192.168.20.1': True,
                '192.168.20.75': True,
                '192.168.20.101': True
            }, ipm.in_use)

    def test_get_address_at_index(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertEqual('192.168.1.1', str(ipm.get_address_at_index(1)))
        self.assertEqual('192.168.1.254', str(ipm.get_address_at_index(-2)))

    def test_is_in_range(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertTrue(ipm.is_in_range('192.168.1.21'))
        self.assertFalse(ipm.is_in_range('10.1.1.1'))

    def test_reserve(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        ipm.reserve('192.168.1.10')
        self.assertEqual(
            {
                '192.168.1.0': True,
                '192.168.1.255': True,
                '192.168.1.10': True
            }, ipm.in_use)

    def test_release(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        ipm.reserve('192.168.1.10')
        ipm.release('10.0.0.1')
        ipm.release('192.168.1.10')
        self.assertEqual(
            {
                '192.168.1.0': True,
                '192.168.1.255': True
            }, ipm.in_use)

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

    def test_get_free_random_ip_congested_fails(self):
        ipm = IPManager('uuid', '192.168.24.0/24')

        try:
            for _ in range(65025):
                ipm.get_random_free_address()

        except exceptions.CongestedNetwork:
            pass
