import time
import uuid
from ipaddress import IPv4Address
from unittest import mock

from shakenfist import exceptions
from shakenfist import ipam
from shakenfist.config import config
from shakenfist.schema.ipam_reservation import IPAMReservation
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


# Fixed UUID4 for use in tests where a consistent user_uuid is needed
TEST_USER_UUID = '8a8496df-9b86-4e94-8c26-c179632e084e'


class IPAMTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

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
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
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
        ipm.reserve('192.168.1.24', (ObjectType.INSTANCE, TEST_USER_UUID),
                    ReservationType.FLOATING, '')
        self.assertEqual(False, ipm.is_free('192.168.1.24'))
        self.assertEqual(
            False, ipm.reserve('192.168.1.24', (ObjectType.INSTANCE, TEST_USER_UUID),
                               ReservationType.FLOATING, ''))

        self.assertEqual(True, ipm.is_free('192.168.1.42'))
        self.assertEqual(
            True, ipm.reserve('192.168.1.42', (ObjectType.INSTANCE, TEST_USER_UUID),
                              ReservationType.FLOATING, ''))
        self.assertEqual(False, ipm.is_free('192.168.1.42'))

    def test_reserve_evict_halo(self):
        # Regression coverage for issue 4059: an explicit address request
        # must be able to take over a deletion-halo reservation, so that
        # deleting and immediately recreating an instance at a static
        # address works. A random allocation must not.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, ''))
        self.assertTrue(ipm.release('192.168.1.10'))
        self.assertEqual(
            ReservationType.DELETION_HALO,
            ipm.get_reservation('192.168.1.10').reservation_type)

        # Without evict_halo the halo still blocks the address
        self.assertFalse(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, ''))

        # With evict_halo the reservation takes the halo over
        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, '', evict_halo=True))
        reservation = ipm.get_reservation('192.168.1.10')
        self.assertEqual(ReservationType.INSTANCE,
                         reservation.reservation_type)
        self.assertEqual(TEST_USER_UUID, str(reservation.user_uuid))

    def test_reserve_evict_halo_never_takes_real_reservation(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, ''))
        self.assertFalse(
            ipm.reserve('192.168.1.10',
                        (ObjectType.INSTANCE, str(uuid.uuid4())),
                        ReservationType.INSTANCE, '', evict_halo=True))
        self.assertEqual(
            TEST_USER_UUID,
            str(ipm.get_reservation('192.168.1.10').user_uuid))

    @mock.patch('time.time', return_value=1632261535.027476)
    def test_reserve_redelivered_is_not_in_use(self, mock_time):
        # _grpc_call() re-sends a reserve which hit DEADLINE_EXCEEDED or
        # CANCELLED, so the same reserve can arrive twice. Read as "in
        # use", reserve_random_free_address() moves on to another address
        # and leaves this one reserved with nothing holding it (issue
        # #4987). Only MockMariaDB is exercised here, which is the point:
        # it has to answer that the way _direct_reserve_address() does, or
        # every test which reaches IPAM through it proves the wrong thing.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        for _ in range(2):
            self.assertTrue(
                ipm.reserve('192.168.1.10',
                            (ObjectType.INSTANCE, TEST_USER_UUID),
                            ReservationType.INSTANCE, 'nic 0'))
        self.assertEqual(
            TEST_USER_UUID,
            str(ipm.get_reservation('192.168.1.10').user_uuid))

    def test_reserve_evict_halo_in_memory(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24',
                            in_memory_only=True)

        # An in-memory release deletes the reservation rather than haloing
        # it, so build the halo directly to exercise the takeover path.
        self.assertTrue(
            ipm.reserve('192.168.1.10', None,
                        ReservationType.DELETION_HALO, ''))
        self.assertFalse(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, ''))
        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, '', evict_halo=True))
        self.assertEqual(
            ReservationType.INSTANCE,
            ipm.get_reservation('192.168.1.10').reservation_type)

    def _counting_sweep(self):
        """Count release_haloed_addresses calls, keeping its behaviour.

        MockMariaDB has already patched it; this patches over the top with
        the same implementation so the sweep still happens.
        """
        return mock.patch(
            'shakenfist.mariadb.release_haloed_addresses',
            side_effect=self.mock_mariadb._mariadb_release_haloed_addresses)

    def test_reserve_does_not_sweep_the_haloed_addresses(self):
        # Issue #4175: reserve() used to delete every expired halo on the
        # IPAM before each attempt, which is a write RPC on the hot path
        # of an operation that does not need one.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        with self._counting_sweep() as swept:
            self.assertTrue(
                ipm.reserve('192.168.1.10',
                            (ObjectType.INSTANCE, TEST_USER_UUID),
                            ReservationType.INSTANCE, ''))
            # And a refused one does not sweep either, which is the case
            # the allocation loop below makes hundreds of.
            self.assertFalse(
                ipm.reserve('192.168.1.10',
                            (ObjectType.INSTANCE, str(uuid.uuid4())),
                            ReservationType.INSTANCE, ''))
        self.assertEqual(0, swept.call_count)

    def test_a_random_allocation_sweeps_once_however_many_attempts(self):
        # The multiplier the issue measured: reserve_random_free_address()
        # tries five random addresses and then walks the netblock, and
        # every attempt used to pay a halo DELETE -- on the order of 250
        # of them for one allocation on a congested /24. The sweep is
        # worth doing once, because an expired halo still occupies a row
        # that in_use counts.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        # Fill the network, leaving one address free, so the allocation
        # has to walk the whole block to find it.
        free = '192.168.1.254'
        for idx in range(2, 255):
            addr = f'192.168.1.{idx}'
            if addr == free:
                continue
            self.assertTrue(
                ipm.reserve(addr, (ObjectType.INSTANCE, TEST_USER_UUID),
                            ReservationType.INSTANCE, ''))

        with self._counting_sweep() as swept:
            self.assertEqual(
                free,
                ipm.reserve_random_free_address(
                    (ObjectType.INSTANCE, TEST_USER_UUID),
                    ReservationType.FLOATING, ''))
        self.assertEqual(1, swept.call_count)

    def test_reserve_takes_over_an_expired_halo(self):
        # An expired halo is a free address which still has a row, and the
        # reserve statement takes it over rather than a sweep deleting it
        # first.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, ''))
        with mock.patch('time.time',
                        return_value=time.time() - config.IP_DELETION_HALO_DURATION - 1):
            self.assertTrue(ipm.release('192.168.1.10'))

        # A random allocation may have it, with no evict_halo anywhere.
        self.assertTrue(
            ipm.reserve('192.168.1.10',
                        (ObjectType.INSTANCE, str(uuid.uuid4())),
                        ReservationType.INSTANCE, ''))
        self.assertEqual(
            ReservationType.INSTANCE,
            ipm.get_reservation('192.168.1.10').reservation_type)

    def test_reserve_takes_over_an_expired_halo_in_memory(self):
        # The in-memory store answers a reserve the way the stored one
        # does, or every test which reaches IPAM through it proves the
        # wrong thing.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24',
                            in_memory_only=True)

        with mock.patch('time.time',
                        return_value=time.time() - config.IP_DELETION_HALO_DURATION - 1):
            self.assertTrue(
                ipm.reserve('192.168.1.10', None,
                            ReservationType.DELETION_HALO, ''))
        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, ''))
        self.assertEqual(
            ReservationType.INSTANCE,
            ipm.get_reservation('192.168.1.10').reservation_type)

    def test_release_expected_type_guards_the_release(self):
        # Issue #4261: releasing a manual reservation must be atomic with
        # its type check, so a concurrent takeover cannot be clobbered by a
        # racing release. reserve a manual address, then act out the race:
        # something else takes the address over (an instance), and the
        # second DELETE's release -- which still expects MANUAL -- must
        # refuse rather than return the instance's address to the halo.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.MANUAL, ''))

        # The first DELETE releases the manual reservation to the halo, and a
        # racing instance create naming that address evicts the halo and
        # takes it -- so the address is now held by an instance.
        self.assertTrue(ipm.release('192.168.1.10'))
        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, '', evict_halo=True))

        # The second DELETE's release still expects MANUAL. It matches no row
        # and refuses, leaving the instance's reservation untouched -- the
        # collision manual reservations exist to prevent.
        self.assertFalse(
            ipm.release('192.168.1.10', expected_type=ReservationType.MANUAL))
        self.assertEqual(
            ReservationType.INSTANCE,
            ipm.get_reservation('192.168.1.10').reservation_type)

    def test_release_expected_type_allows_the_matching_type(self):
        # The same guard releases normally when the type still holds.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.MANUAL, ''))
        self.assertTrue(
            ipm.release('192.168.1.10', expected_type=ReservationType.MANUAL))
        self.assertEqual(
            ReservationType.DELETION_HALO,
            ipm.get_reservation('192.168.1.10').reservation_type)

    def test_release_without_expected_type_is_unguarded(self):
        # No expected_type keeps the pre-#4261 behaviour for every other
        # caller: the release happens whatever the current type is.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, ''))
        self.assertTrue(ipm.release('192.168.1.10'))
        self.assertEqual(
            ReservationType.DELETION_HALO,
            ipm.get_reservation('192.168.1.10').reservation_type)

    def test_release_expected_type_guards_in_memory(self):
        # The in-memory IPAM honours the guard too, so a test fixture and a
        # real cluster agree.
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24',
                            in_memory_only=True)

        self.assertTrue(
            ipm.reserve('192.168.1.10', (ObjectType.INSTANCE, TEST_USER_UUID),
                        ReservationType.INSTANCE, ''))
        self.assertFalse(
            ipm.release('192.168.1.10', expected_type=ReservationType.MANUAL))
        self.assertTrue(
            ipm.release('192.168.1.10', expected_type=ReservationType.INSTANCE))

    def test_get_free_random_ip(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '10.0.0.0/22')

        for _ in range(800):
            ipm.reserve_random_free_address(
                (ObjectType.INSTANCE, TEST_USER_UUID), ReservationType.FLOATING, '')

        # The extra three are the reserved network, broadcast, and gateway
        # addresses
        self.assertEqual(800 + 3, len(ipm.in_use))

    def test_get_free_random_ip_congested_fails(self):
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24')

        try:
            for _ in range(65025):
                ipm.reserve_random_free_address(
                    (ObjectType.INSTANCE, TEST_USER_UUID), ReservationType.FLOATING, '')

        except exceptions.CongestedNetwork:
            pass

    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch('shakenfist.mariadb.get_state')
    def test_in_memory_ipam_writes_no_state_row(
            self, mock_get_state, mock_set_state):
        # The in-memory IPAM that Network.__init__ builds for deleted
        # networks must not write an object_states row -- nothing can ever
        # clean such a row up (issue 3532).
        ipam_uuid = str(uuid.uuid4())
        ipm = ipam.IPAM.new(ipam_uuid, None, ipam_uuid, '192.168.1.0/24',
                            in_memory_only=True)

        self.assertEqual(ipam.IPAM.STATE_CREATED, ipm.state.value)
        self.assertEqual({'192.168.1.0', '192.168.1.1', '192.168.1.255'},
                         ipm.in_use)
        mock_get_state.assert_not_called()
        mock_set_state.assert_not_called()
