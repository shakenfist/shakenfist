import mock

from shakenfist import exceptions
from shakenfist.ipmanager import IPManager
from shakenfist.tests import base


class IPManagerTestCase(base.ShakenFistTestCase):
    @mock.patch('time.time', return_value=1632261535.027476)
    def test_init(self, mock_time):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertEqual(
            {
                '192.168.1.0': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                },
                '192.168.1.255': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                }
            }, ipm.in_use)

    @mock.patch('time.time', return_value=1632261535.027476)
    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.ipmanager.IPManager.persist')
    def test_new(self, mock_persist, mock_lock, mock_time):
        ipm = IPManager.new('uuid', '192.168.1.0/24')
        self.assertEqual(
            {
                '192.168.1.0': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                },
                '192.168.1.255': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                },
                '192.168.1.1': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                }
            }, ipm.in_use)

    @mock.patch('time.time', return_value=1632261535.027476)
    @mock.patch('shakenfist.db.get_ipmanager',
                return_value={
                    'ipmanager.v2': {
                        'in_use': {
                            '192.168.20.0': ('ipmanager', 'uuid'),
                            '192.168.20.255': ('ipmanager', 'uuid'),
                            '192.168.20.1': ('ipmanager', 'uuid'),
                            '192.168.20.75': ('ipmanager', 'uuid'),
                            '192.168.20.101': ('ipmanager', 'uuid')
                        },
                        'ipblock': '192.168.20.0/24'
                    }}
                )
    def test_from_db(self, mock_get, mock_time):
        ipm = IPManager.from_db('floating')
        self.assertEqual(
            {
                '192.168.20.0': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                },
                '192.168.20.1': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                },
                '192.168.20.101': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                },
                '192.168.20.255': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                },
                '192.168.20.75': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                }
            }, ipm.in_use)

    def test_get_address_at_index(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertEqual('192.168.1.1', str(ipm.get_address_at_index(1)))
        self.assertEqual('192.168.1.254', str(ipm.get_address_at_index(-2)))

    def test_is_in_range(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertTrue(ipm.is_in_range('192.168.1.21'))
        self.assertFalse(ipm.is_in_range('10.1.1.1'))

    @mock.patch('time.time', return_value=1632261535.027476)
    def test_reserve(self, mock_time):
        ipm = IPManager('uuid', '192.168.1.0/24')
        ipm.reserve('192.168.1.10', ('test', '123'))
        self.assertEqual(
            {
                '192.168.1.0': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                },
                '192.168.1.10': {
                    'user': ('test', '123'),
                    'when': 1632261535.027476
                },
                '192.168.1.255': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                }
            }, ipm.in_use)

    @mock.patch('time.time', return_value=1632261535.027476)
    def test_release(self, mock_time):
        ipm = IPManager('uuid', '192.168.1.0/24')
        ipm.reserve('192.168.1.10', ('test', '123'))
        ipm.release('10.0.0.1')
        ipm.release('192.168.1.10')
        self.assertEqual(
            {
                '192.168.1.0': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                },
                '192.168.1.255': {
                    'user': ('ipmanager', 'uuid'),
                    'when': 1632261535.027476
                }
            }, ipm.in_use)

    def test_is_free_and_reserve(self):
        ipm = IPManager('uuid', '192.168.1.0/24')
        self.assertEqual(True, ipm.is_free('192.168.1.24'))
        ipm.reserve('192.168.1.24', ('test', '123'))
        self.assertEqual(False, ipm.is_free('192.168.1.24'))
        self.assertEqual(False, ipm.reserve('192.168.1.24', ('test', '123')))

        self.assertEqual(True, ipm.is_free('192.168.1.42'))
        self.assertEqual(True, ipm.reserve('192.168.1.42', ('test', '123')))
        self.assertEqual(False, ipm.is_free('192.168.1.42'))
        ipm.release('192.168.1.42')
        self.assertEqual(True, ipm.is_free('192.168.1.42'))

    def test_get_free_random_ip(self):
        ipm = IPManager('uuid', '10.0.0.0/8')

        for _ in range(65025):
            ipm.get_random_free_address(('test', '123'))

        # The extra two are the reserved network and broadcast
        # addresses
        self.assertEqual(65025 + 2, len(ipm.in_use))

    def test_get_free_random_ip_congested_fails(self):
        ipm = IPManager('uuid', '192.168.24.0/24')

        try:
            for _ in range(65025):
                ipm.get_random_free_address(('test', '123'))

        except exceptions.CongestedNetwork:
            pass
