# Copyright 2026 Michael Still and contributors
#
# Tests for the floating IP reaper's reconciliation sweep,
# specifically the "rescue" branch which re-reserves active floating
# addresses whose IPAM reservation has gone missing. This branch
# previously reserved the wrong address (a leftover variable from the
# gateway loop), leaving the actual floating address unreserved and
# eligible for double allocation.
#
# The leak branch is covered here too: the sweep is built from several
# separate database reads and is therefore not a consistent snapshot,
# so an address must look leaked for LEAK_CONFIRMATION_SECONDS of
# continuous observation before it is released (issue 3645).

import time
from unittest import mock

import grpc

from shakenfist.daemons.network import floating_ip_reaper
from shakenfist import exceptions
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class FakeIPAM:
    """A fake which costs what the real IPAM costs.

    The real ``in_use`` is a property issuing a whole-table read on every
    access, and ``is_free()`` is defined in terms of it. This fake used to
    hold ``in_use`` as a plain set and answer ``is_free()`` from a separate
    ``free_addresses``, which made the two disagree in ways the real object
    cannot, and made free precisely the access that costs a round trip.
    The phase 8 push audit found a per-address sweep surviving behind that
    gap. ``in_use`` is now a counting property and ``is_free()`` derives
    from it, so a caller which walks addresses one at a time shows up in
    ``table_reads``.
    """

    def __init__(self, in_use=None, reservations=None, reserve_result=True):
        self.reserve_calls = []
        self.released = []
        self._in_use = in_use if in_use is not None else set()
        self.reservations = reservations or {}
        self.broadcast_address = '192.168.10.255'
        self.network_address = '192.168.10.0'
        self.table_reads = 0
        self.reserve_result = reserve_result

    @property
    def in_use(self):
        self.table_reads += 1
        return set(self._in_use)

    def is_free(self, address):
        return address not in self.in_use

    def reserve(self, address, user, reservation_type, comment):
        self.reserve_calls.append(
            (address, user, reservation_type, comment))
        return self.reserve_result

    def get_address_at_index(self, idx):
        return '192.168.10.%d' % idx

    def get_haloed_addresses(self):
        return iter([])

    def get_reservation(self, address):
        return self.reservations.get(address)

    def get_all_reservations(self):
        self.table_reads += 1
        return dict(self.reservations)

    def get_allocation_age(self, address):
        return 0

    def release(self, address):
        self.released.append(address)


def _reservation(user_type=None, user_uuid=None, reserved_at=0):
    res = mock.Mock()
    res.reservation_type = ReservationType.GATEWAY
    res.user_type = user_type
    res.user_uuid = user_uuid
    # The reaper's age check reads this directly now rather than going
    # back to the database via get_allocation_age().
    res.reserved_at = reserved_at
    return res


class FloatingIPReaperTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # The candidate leak history is module level state which
        # persists between sweeps, so each test starts from empty. The
        # bulk read failure flag persists the same way.
        floating_ip_reaper._leak_candidates.clear()
        self.addCleanup(floating_ip_reaper._leak_candidates.clear)
        floating_ip_reaper._bulk_gateway_read_failing = False

    def _sweep(self, ipam, networks=None, interfaces=None, gateways=None):
        fake_floating_network = mock.MagicMock()
        fake_floating_network.ipam = ipam

        # The sweep reads gateways from the bulk accessor rather than
        # from each network's floating_gateway property, so derive the
        # map the accessor would return from the fakes unless the test
        # supplies its own.
        if gateways is None:
            gateways = {}
            for n in networks or []:
                if n.floating_gateway:
                    gateways[str(n.uuid)] = n.floating_gateway

        with mock.patch.object(
                floating_ip_reaper.network, 'floating_network',
                return_value=fake_floating_network), \
            mock.patch.object(
                floating_ip_reaper.mariadb, 'get_network_floating_gateways',
                return_value=gateways), \
            mock.patch.object(
                floating_ip_reaper.network, 'Networks',
                return_value=networks or []), \
            mock.patch.object(
                floating_ip_reaper.interface, 'NetworkInterfaces',
                return_value=interfaces or []):
            return floating_ip_reaper.reap_floating_ips()

    def test_unreserved_floating_address_rescue_reserves_that_address(
            self):
        # A gateway with a valid reservation, so the gateway loop
        # leaves stale locals behind (this is how the original bug
        # reserved the wrong address).
        fake_gw_network = mock.MagicMock()
        fake_gw_network.floating_gateway = '192.168.10.11'
        fake_gw_network.unique_label.return_value = (
            ObjectType.NETWORK, 'gw-network-uuid')

        # An active interface holding a floating address that IPAM
        # has (incorrectly) forgotten about.
        fake_ni = mock.MagicMock()
        fake_ni.uuid = 'iface-uuid'
        fake_ni.floating = {'floating_address': '192.168.10.42'}
        fake_ni.unique_label.return_value = (
            ObjectType.INTERFACE, 'iface-uuid')

        # The gateway is reserved; the interface's floating address is
        # not, which is the state the rescue path exists to repair.
        ipam = FakeIPAM(in_use={'192.168.10.11'})
        self.assertTrue(self._sweep(
            ipam, networks=[fake_gw_network], interfaces=[fake_ni]))

        self.assertEqual(1, len(ipam.reserve_calls))
        address, user, reservation_type, _ = ipam.reserve_calls[0]
        self.assertEqual('192.168.10.42', address)
        self.assertEqual((ObjectType.INTERFACE, 'iface-uuid'), user)
        self.assertEqual(ReservationType.FLOATING, reservation_type)

    def test_address_reserved_mid_sweep_does_not_log_an_error(self):
        # The interface gained its floating address (and reservation)
        # after the sweep snapshotted in_use, so the rescue's reserve()
        # is refused. That is the sweep racing normal operation, not a
        # fault, and must not log at error (issue 3984).
        fake_ni = mock.MagicMock()
        fake_ni.uuid = 'iface-uuid'
        fake_ni.floating = {'floating_address': '192.168.10.42'}
        fake_ni.unique_label.return_value = (
            ObjectType.INTERFACE, 'iface-uuid')

        actual = _reservation(
            user_type=ObjectType.INTERFACE, user_uuid='iface-uuid')
        actual.to_legacy_dict.return_value = {}
        ipam = FakeIPAM(
            in_use=set(),
            reservations={'192.168.10.42': actual},
            reserve_result=False)

        with mock.patch.object(floating_ip_reaper, 'LOG') as log:
            self.assertTrue(self._sweep(ipam, interfaces=[fake_ni]))

        error_calls = [c for c in log.mock_calls if c[0].endswith('error')]
        self.assertEqual(
            [], error_calls,
            'a reservation written mid-sweep was logged as an error')

    def test_no_floating_network_returns_false(self):
        with mock.patch.object(
                floating_ip_reaper.network, 'floating_network',
                return_value=None):
            self.assertFalse(floating_ip_reaper.reap_floating_ips())

    def test_unowned_address_is_not_reaped_on_first_sighting(self):
        # The first time an address looks leaked we merely remember it.
        # A network delete in flight briefly presents exactly this way.
        ipam = FakeIPAM(in_use={'192.168.10.154'})

        self.assertTrue(self._sweep(ipam))

        self.assertEqual([], ipam.released)
        self.assertIn('192.168.10.154', floating_ip_reaper._leak_candidates)

    def test_address_is_reaped_once_confirmed(self):
        # An address which has looked leaked for longer than the
        # confirmation period really has leaked, so release it.
        ipam = FakeIPAM(in_use={'192.168.10.154'})
        floating_ip_reaper._leak_candidates['192.168.10.154'] = (
            time.time() - floating_ip_reaper.LEAK_CONFIRMATION_SECONDS - 1)

        self.assertTrue(self._sweep(ipam))

        self.assertEqual(['192.168.10.154'], ipam.released)

    def test_candidate_is_forgotten_when_it_stops_looking_leaked(self):
        # The address is now claimed by an active network, so its leak
        # history must be discarded rather than accumulating towards a
        # future reap.
        floating_ip_reaper._leak_candidates['192.168.10.154'] = (
            time.time() - 1000)

        fake_network = mock.MagicMock()
        fake_network.floating_gateway = '192.168.10.154'
        fake_network.unique_label.return_value = (
            ObjectType.NETWORK, 'network-uuid')

        ipam = FakeIPAM(
            in_use={'192.168.10.154'})
        self.assertTrue(self._sweep(ipam, networks=[fake_network]))

        self.assertEqual([], ipam.released)
        self.assertEqual({}, floating_ip_reaper._leak_candidates)

    def test_recently_deleted_owner_is_not_a_leak_candidate(self):
        # A gateway reservation owned by a network which was deleted
        # moments ago is mid-teardown, not leaked. It must not even
        # start accruing confirmation time.
        ipam = FakeIPAM(
            in_use={'192.168.10.154'},
            reservations={
                '192.168.10.154': _reservation(
                    user_type=ObjectType.NETWORK, user_uuid='network-uuid')
            })

        owner = mock.Mock()
        owner.state = State(value='deleted', update_time=time.time())
        owner_class = mock.Mock()
        owner_class.from_db.return_value = owner

        with mock.patch.object(
                floating_ip_reaper, 'get_object_class',
                return_value=owner_class):
            self.assertTrue(self._sweep(ipam))

        self.assertEqual([], ipam.released)
        self.assertEqual({}, floating_ip_reaper._leak_candidates)

    def test_long_deleted_owner_is_reaped_once_confirmed(self):
        # A genuine leak: the owning object was deleted long ago and the
        # reservation has looked leaked for longer than the confirmation
        # period.
        ipam = FakeIPAM(
            in_use={'192.168.10.154'},
            reservations={
                '192.168.10.154': _reservation(
                    user_type=ObjectType.NETWORK, user_uuid='network-uuid')
            })
        floating_ip_reaper._leak_candidates['192.168.10.154'] = (
            time.time() - floating_ip_reaper.LEAK_CONFIRMATION_SECONDS - 1)

        owner = mock.Mock()
        owner.state = State(
            value='deleted', update_time=time.time() - 100000)
        owner_class = mock.Mock()
        owner_class.from_db.return_value = owner

        with mock.patch.object(
                floating_ip_reaper, 'get_object_class',
                return_value=owner_class):
            self.assertTrue(self._sweep(ipam))

        self.assertEqual(['192.168.10.154'], ipam.released)

    def test_bulk_gateway_read_failure_falls_back_to_per_network(self):
        # An sf-database from before GetNetworkFloatingGateways answers
        # UNIMPLEMENTED for the length of a mixed version window. The
        # sweep must fall back to per-network attribute reads rather
        # than dying (which would crash-restart the job every few
        # seconds) or treating every gateway as a leak candidate.
        fake_network = mock.MagicMock()
        fake_network.floating_gateway = '192.168.10.11'
        fake_network.unique_label.return_value = (
            ObjectType.NETWORK, 'network-uuid')

        ipam = FakeIPAM(in_use={'192.168.10.11'})
        fake_floating_network = mock.MagicMock()
        fake_floating_network.ipam = ipam

        with mock.patch.object(
                floating_ip_reaper.network, 'floating_network',
                return_value=fake_floating_network), \
            mock.patch.object(
                floating_ip_reaper.mariadb, 'get_network_floating_gateways',
                side_effect=grpc.RpcError()), \
            mock.patch.object(
                floating_ip_reaper.network, 'Networks',
                return_value=[fake_network]), \
            mock.patch.object(
                floating_ip_reaper.interface, 'NetworkInterfaces',
                return_value=[]):
            self.assertTrue(floating_ip_reaper.reap_floating_ips())

        # The gateway was found via the fallback property read, so it
        # is not on the leak path.
        self.assertEqual([], ipam.released)
        self.assertEqual({}, floating_ip_reaper._leak_candidates)
        self.assertTrue(floating_ip_reaper._bulk_gateway_read_failing)

    def test_unreachable_database_aborts_the_sweep(self):
        # DatabaseUnavailable means retries are already exhausted, and
        # every read after this one would fail the same way. It must
        # propagate rather than being mistaken for the mixed version
        # window: the fallback would issue one more doomed RPC per
        # network, and a sweep built on failed reads feeds the leak
        # release path.
        fake_floating_network = mock.MagicMock()
        fake_floating_network.ipam = FakeIPAM()

        with mock.patch.object(
                floating_ip_reaper.network, 'floating_network',
                return_value=fake_floating_network), \
            mock.patch.object(
                floating_ip_reaper.mariadb, 'get_network_floating_gateways',
                side_effect=exceptions.DatabaseUnavailable('gone')):
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                floating_ip_reaper.reap_floating_ips)


def _actual_reservation(user_type, user_uuid):
    res = mock.Mock()
    res.user_type = user_type
    res.user_uuid = user_uuid
    res.to_legacy_dict.return_value = {
        'address': '192.168.10.42',
        'user': (str(user_type), user_uuid) if user_type else None,
        'when': 0,
        'type': 'floating',
        'comment': ''
    }
    return res


class RescueReservationTestCase(base.ShakenFistTestCase):
    """The rescue path must say what was expected, what was found, and
    whether it actually repaired anything (issue 3984). A reservation
    written between the sweep's in_use snapshot and the object walk is
    benign skew, not an error.
    """

    OWNER = (ObjectType.INTERFACE, 'iface-uuid')

    def _rescue(self, ipam):
        log = mock.MagicMock()
        floating_ip_reaper._rescue_reservation(
            ipam, '192.168.10.42', self.OWNER, ReservationType.FLOATING,
            log, 'Floating address not reserved correctly')
        return log

    def test_missing_reservation_is_rescued_and_logged_with_detail(self):
        ipam = mock.MagicMock()
        ipam.reserve.return_value = True

        log = self._rescue(ipam)

        ipam.reserve.assert_called_once_with(
            '192.168.10.42', self.OWNER, ReservationType.FLOATING,
            'Rescued from incorrect registration')
        fields = log.with_fields.call_args[0][0]
        self.assertEqual(self.OWNER, fields['expected_user'])
        self.assertEqual('floating', fields['expected_type'])
        self.assertIsNone(fields['actual_reservation'])
        log.with_fields.return_value.error.assert_called_once_with(
            'Floating address not reserved correctly')

    def test_reservation_written_mid_sweep_is_not_an_error(self):
        ipam = mock.MagicMock()
        ipam.reserve.return_value = False
        ipam.get_reservation.return_value = _actual_reservation(
            ObjectType.INTERFACE, 'iface-uuid')

        log = self._rescue(ipam)

        log.with_fields.return_value.error.assert_not_called()
        log.with_fields.return_value.info.assert_called_once_with(
            'Floating reservation appeared mid-sweep, no rescue required')

    def test_conflicting_reservation_logs_expected_versus_actual(self):
        ipam = mock.MagicMock()
        ipam.reserve.return_value = False
        ipam.get_reservation.return_value = _actual_reservation(
            ObjectType.INTERFACE, 'someone-else')

        log = self._rescue(ipam)

        log.with_fields.return_value.info.assert_not_called()
        fields = log.with_fields.call_args[0][0]
        self.assertEqual(self.OWNER, fields['expected_user'])
        self.assertEqual(
            ('interface', 'someone-else'),
            tuple(fields['actual_reservation']['user']))
        log.with_fields.return_value.error.assert_called_once_with(
            'Floating address not reserved correctly')

    def test_reservation_vanished_before_reread_is_still_an_error(self):
        # reserve() refused but the row was gone by the time we read it
        # back: something is churning this address and it deserves eyes.
        ipam = mock.MagicMock()
        ipam.reserve.return_value = False
        ipam.get_reservation.return_value = None

        log = self._rescue(ipam)

        fields = log.with_fields.call_args[0][0]
        self.assertIsNone(fields['actual_reservation'])
        log.with_fields.return_value.error.assert_called_once_with(
            'Floating address not reserved correctly')


class CountingNetwork:
    """A fake network which counts floating_gateway property reads.

    On the real object every read is one uncacheable
    GetNetworkAttributes RPC, which is the per-network cost issue 3976
    exists to remove from this sweep.
    """

    def __init__(self, net_uuid, gateway):
        self.uuid = net_uuid
        self._gateway = gateway
        self.attribute_reads = 0

    @property
    def floating_gateway(self):
        self.attribute_reads += 1
        return self._gateway

    def unique_label(self):
        return (ObjectType.NETWORK, self.uuid)


class FloatingIPReaperReadShapeTestCase(base.ShakenFistTestCase):
    """The sweep's cost must not grow with the number of addresses.

    Issue 3655 is about access shape, not outcome, so these assert on
    the read count. Phase 6 removed the per-address get_reservation()
    and left is_free() reaching the same table once per address through
    the in_use property; the phase 8 push audit found it because the
    budget recorded a per-instance slope on GetAddressesInUse that its
    replacement was explicitly documented not to have.
    """

    def _sweep_with(self, address_count):
        interfaces = []
        for i in range(address_count):
            ni = mock.MagicMock()
            ni.uuid = 'iface-%d' % i
            # Addresses the IPAM already knows about, so the rescue path
            # does not fire and we measure the walk rather than repairs.
            ni.floating = {'floating_address': '192.168.10.%d' % (20 + i)}
            ni.unique_label.return_value = (ObjectType.INTERFACE, ni.uuid)
            interfaces.append(ni)

        in_use = {'192.168.10.%d' % (20 + i) for i in range(address_count)}
        ipam = FakeIPAM(in_use=in_use)

        fake_floating_network = mock.MagicMock()
        fake_floating_network.ipam = ipam
        with mock.patch.object(
                floating_ip_reaper.network, 'floating_network',
                return_value=fake_floating_network), \
            mock.patch.object(
                floating_ip_reaper.mariadb, 'get_network_floating_gateways',
                return_value={}), \
            mock.patch.object(
                floating_ip_reaper.network, 'Networks', return_value=[]), \
            mock.patch.object(
                floating_ip_reaper.interface, 'NetworkInterfaces',
                return_value=interfaces):
            self.assertTrue(floating_ip_reaper.reap_floating_ips())
        return ipam.table_reads

    def test_the_sweep_reads_the_table_a_fixed_number_of_times(self):
        few = self._sweep_with(2)
        many = self._sweep_with(40)

        # Guard against measuring nothing at all.
        self.assertGreater(few, 0)
        self.assertEqual(
            few, many,
            'the sweep issued %d whole-table reads for 40 addresses against '
            '%d for 2, so its database cost grows per address' % (many, few))

    def test_the_sweep_does_not_read_attributes_per_network(self):
        # The per-network floating_gateway property read is one
        # uncacheable GetNetworkAttributes RPC, issued by every sf-net
        # node every 30 seconds, so the sweep's database rate scaled
        # with node count times network count (issue 3976). The
        # gateways must come from the one bulk read instead.
        networks = [
            CountingNetwork('network-%d' % i, '192.168.10.%d' % (30 + i))
            for i in range(5)]
        gateways = {n.uuid: '192.168.10.%d' % (30 + i)
                    for i, n in enumerate(networks)}
        ipam = FakeIPAM(in_use=set(gateways.values()))

        fake_floating_network = mock.MagicMock()
        fake_floating_network.ipam = ipam
        with mock.patch.object(
                floating_ip_reaper.network, 'floating_network',
                return_value=fake_floating_network), \
            mock.patch.object(
                floating_ip_reaper.mariadb, 'get_network_floating_gateways',
                return_value=gateways), \
            mock.patch.object(
                floating_ip_reaper.network, 'Networks',
                return_value=networks), \
            mock.patch.object(
                floating_ip_reaper.interface, 'NetworkInterfaces',
                return_value=[]):
            self.assertTrue(floating_ip_reaper.reap_floating_ips())

        self.assertEqual(
            [0, 0, 0, 0, 0],
            [n.attribute_reads for n in networks],
            'the sweep read floating_gateway per network rather than '
            'using the bulk read')
        # And the bulk answer was actually used: every gateway is
        # accounted for, so none was recorded as a leak candidate.
        self.assertEqual([], ipam.released)
        self.assertEqual({}, floating_ip_reaper._leak_candidates)
