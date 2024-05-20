from unittest import mock

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
