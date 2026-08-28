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

from shakenfist.daemons.network import floating_ip_reaper
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

    def __init__(self, in_use=None, reservations=None):
        self.reserve_calls = []
        self.released = []
        self._in_use = in_use if in_use is not None else set()
        self.reservations = reservations or {}
        self.broadcast_address = '192.168.10.255'
        self.network_address = '192.168.10.0'
        self.table_reads = 0

    @property
    def in_use(self):
        self.table_reads += 1
        return set(self._in_use)

    def is_free(self, address):
        return address not in self.in_use

    def reserve(self, address, user, reservation_type, comment):
        self.reserve_calls.append(
            (address, user, reservation_type, comment))
        return True

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
        # persists between sweeps, so each test starts from empty.
        floating_ip_reaper._leak_candidates.clear()
        self.addCleanup(floating_ip_reaper._leak_candidates.clear)

    def _sweep(self, ipam, networks=None, interfaces=None):
        fake_floating_network = mock.MagicMock()
        fake_floating_network.ipam = ipam

        with mock.patch.object(
                floating_ip_reaper.network, 'floating_network',
                return_value=fake_floating_network), \
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
