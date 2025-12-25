import time
import uuid
from ipaddress import IPv4Address
from unittest import mock

from shakenfist import exceptions
from shakenfist import ipam
from shakenfist.schema.ipam_reservation import IPAMReservation
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class IPAMTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

    @mock.patch('time.time', return_value=1632261535.027476)
    def test_new(self, mock_time):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        self.assertEqual({'192.168.1.0', '192.168.1.1', '192.168.1.255'}, ipm.in_use)
        # Verify reservations using IPAMReservation objects
        self.assertEqual(
            IPAMReservation(
                ipam_uuid=ipam_uuid,
                address=IPv4Address('192.168.1.0'),
                reservation_type=ReservationType.NETWORK,
                user_type=ObjectType.NETWORK,
                user_uuid=ipam_uuid,
                reserved_at=1632261535.027476,
                comment=None
            ),
            ipm.get_reservation('192.168.1.0'))
        self.assertEqual(
            IPAMReservation(
                ipam_uuid=ipam_uuid,
                address=IPv4Address('192.168.1.1'),
                reservation_type=ReservationType.GATEWAY,
                user_type=ObjectType.NETWORK,
                user_uuid=ipam_uuid,
                reserved_at=1632261535.027476,
                comment=None
            ),
            ipm.get_reservation('192.168.1.1'))
        self.assertEqual(
            IPAMReservation(
                ipam_uuid=ipam_uuid,
                address=IPv4Address('192.168.1.255'),
                reservation_type=ReservationType.BROADCAST,
                user_type=ObjectType.NETWORK,
                user_uuid=ipam_uuid,
                reserved_at=1632261535.027476,
                comment=None
            ),
            ipm.get_reservation('192.168.1.255'))
        self.assertIsNone(ipm.get_reservation('192.168.1.2'))

    def test_get_address_at_index(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')
        self.assertEqual('192.168.1.1', ipm.get_address_at_index(1))
        self.assertEqual('192.168.1.254', ipm.get_address_at_index(-2))

    def test_is_in_range(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')
        self.assertTrue(ipm.is_in_range('192.168.1.21'))
        self.assertFalse(ipm.is_in_range('10.1.1.1'))

    def test_reservation_lifecycle(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        self.assertNotIn('192.168.1.10', ipm.in_use)
        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, '123'),
                        ReservationType.FLOATING, ''))
        self.assertIn('192.168.1.10', ipm.in_use)

        # Check for halo
        self.assertTrue(ipm.release('192.168.1.10'))
        self.assertIn('192.168.1.10', ipm.in_use)

        # Check that halo goes away
        time.sleep(1)
        self.assertTrue(ipm.release_haloed(0) > 0)
        self.assertNotIn('192.168.1.10', ipm.in_use)

    def test_is_free_and_reserve(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        self.assertEqual(True, ipm.is_free('192.168.1.24'))
        ipm.reserve('192.168.1.24', (ObjectType.INSTANCE, '123'),
                    ReservationType.FLOATING, '')
        self.assertEqual(False, ipm.is_free('192.168.1.24'))
        self.assertEqual(
            False, ipm.reserve('192.168.1.24', (ObjectType.INSTANCE, '123'),
                               ReservationType.FLOATING, ''))

        self.assertEqual(True, ipm.is_free('192.168.1.42'))
        self.assertEqual(
            True, ipm.reserve('192.168.1.42', (ObjectType.INSTANCE, '123'),
                              ReservationType.FLOATING, ''))
        self.assertEqual(False, ipm.is_free('192.168.1.42'))

    def test_get_free_random_ip(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '10.0.0.0/22')

        for _ in range(800):
            ipm.reserve_random_free_address(
                (ObjectType.INSTANCE, '123'), ReservationType.FLOATING, '')

        # The extra three are the reserved network, broadcast, and gateway
        # addresses
        self.assertEqual(800 + 3, len(ipm.in_use))

    def test_get_free_random_ip_congested_fails(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        try:
            for _ in range(65025):
                ipm.reserve_random_free_address(
                    (ObjectType.INSTANCE, '123'), ReservationType.FLOATING, '')

        except exceptions.CongestedNetwork:
            pass
