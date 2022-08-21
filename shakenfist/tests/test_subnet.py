import mock

from shakenfist.config import SFConfig
from shakenfist import exceptions
from shakenfist import subnet
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


fake_config = SFConfig(
    NODE_NAME='node01',
    SCHEDULER_CACHE_TIMEOUT=30,
    CPU_OVERCOMMIT_RATIO=16.0,
    RAM_OVERCOMMIT_RATIO=1.5,
    RAM_SYSTEM_RESERVATION=5.0,
    NETWORK_NODE_IP='10.0.0.1',
)


class SubnetTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super(SubnetTestCase, self).setUp()

        self.recorded_op = mock.patch(
            'shakenfist.util.general.RecordedOperation')
        self.recorded_op.start()
        self.addCleanup(self.recorded_op.stop)

        self.mock_config = mock.patch(
            'shakenfist.scheduler.config', fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

    def test_get_address_at_index(self):
        ipm = subnet.Subnet({
            'uuid': 'uuid',
            'network_uuid': 'net_uuid',
            'iprange': '192.168.1.0/24'
        })
        self.assertEqual('192.168.1.1', str(ipm.get_address_at_index(1)))
        self.assertEqual('192.168.1.254', str(ipm.get_address_at_index(-2)))

    def test_is_in_range(self):
        ipm = subnet.Subnet({
            'uuid': 'uuid',
            'network_uuid': 'net_uuid',
            'iprange': '192.168.1.0/24'
        })
        self.assertTrue(ipm.is_in_range('192.168.1.21'))
        self.assertFalse(ipm.is_in_range('10.1.1.1'))

    @mock.patch('time.time', return_value=1632261535.027476)
    def test_reserve(self, mock_time):
        ipm = subnet.Subnet({
            'uuid': 'uuid',
            'network_uuid': 'net_uuid',
            'iprange': '192.168.1.0/24'
        })
        ipm.reserve('192.168.1.10', ['test', '123'])
        self.assertEqual(
            {
                '192.168.1.10': {
                    'user': ['test', '123'],
                    'when': 1632261535.027476
                }
            }, ipm.allocations['addresses'])

    @mock.patch('time.time', return_value=1632261535.027476)
    def test_release(self, mock_time):
        ipm = subnet.Subnet({
            'uuid': 'uuid',
            'network_uuid': 'net_uuid',
            'iprange': '192.168.1.0/24'
        })
        ipm.reserve('192.168.1.10', ['test', '123'])
        ipm.release('10.0.0.1')
        ipm.release('192.168.1.10')
        self.assertEqual({}, ipm.allocations['addresses'])

    def test_is_free_and_reserve(self):
        ipm = subnet.Subnet({
            'uuid': 'uuid',
            'network_uuid': 'net_uuid',
            'iprange': '192.168.1.0/24'
        })
        self.assertEqual(True, ipm.is_free('192.168.1.24'))
        ipm.reserve('192.168.1.24', ['test', '123'])
        self.assertEqual(False, ipm.is_free('192.168.1.24'))
        self.assertEqual(False, ipm.reserve('192.168.1.24', ['test', '123']))

        self.assertEqual(True, ipm.is_free('192.168.1.42'))
        self.assertEqual(True, ipm.reserve('192.168.1.42', ['test', '123']))
        self.assertEqual(False, ipm.is_free('192.168.1.42'))
        ipm.release('192.168.1.42')
        self.assertEqual(True, ipm.is_free('192.168.1.42'))

    def test_get_free_random_ip(self):
        ipm = subnet.Subnet({
            'uuid': 'uuid',
            'network_uuid': 'net_uuid',
            'iprange': '192.168.1.0/24'
        })

        for _ in range(5):
            ipm.get_random_free_address(['test', '123'])

        # The network and broadcast address are not included here because this
        # test skips new()
        self.assertEqual(5, ipm.allocations['counter'])

    def test_get_free_random_ip_congested_fails(self):
        self.skip('Does not play well with MockEtcd')
        ipm = subnet.Subnet({
            'uuid': 'uuid',
            'network_uuid': 'net_uuid',
            'iprange': '192.168.1.0/24'
        })

        try:
            for _ in range(65025):
                ipm.get_random_free_address(['test', '123'])

        except exceptions.CongestedNetwork:
            pass
