# Copyright 2019 Michael Still and contributors
from unittest import mock

from shakenfist.daemons.network import floating_ip_reaper
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.tests import base


class CountingIPAM:
    """An IPAM which records how it was asked about reservations.

    Issue 3655: the floating IP sweeps walked every in-use address and
    read that address's reservation, one round trip each. The whole
    point of the fix is the shape of the access, so the double counts
    both forms and the tests assert on the counts rather than on the
    outcome alone.
    """

    def __init__(self, reservations):
        self.reservations = reservations
        self.in_use = set(reservations)
        self.per_address_reads = 0
        self.bulk_reads = 0
        self.released = []
        self.broadcast_address = '192.168.10.255'
        self.network_address = '192.168.10.0'

    def get_reservation(self, address):
        self.per_address_reads += 1
        return self.reservations.get(address)

    def get_all_reservations(self):
        self.bulk_reads += 1
        return dict(self.reservations)

    def get_allocation_age(self, address):
        # The old second read per address. Nothing should call this in a
        # sweep any more.
        self.per_address_reads += 1
        return 0

    def is_free(self, address):
        return address not in self.reservations

    def get_address_at_index(self, idx):
        return '192.168.10.%d' % idx

    def get_haloed_addresses(self):
        return iter([])

    def release(self, address):
        self.released.append(address)


def _reservation(address, reservation_type=ReservationType.FLOATING,
                 user_type=None, user_uuid=None, reserved_at=0):
    res = mock.Mock()
    res.address = address
    res.reservation_type = reservation_type
    res.user_type = user_type
    res.user_uuid = user_uuid
    res.reserved_at = reserved_at
    return res


def _ipam_with(count):
    return CountingIPAM({
        '192.168.10.%d' % (100 + i): _reservation('192.168.10.%d' % (100 + i))
        for i in range(count)
    })


class ReservationSweepShapeTestCase(base.ShakenFistTestCase):
    """The sweeps must cost one read per pass, not one per address."""

    def _run_reaper(self, ipam):
        fn = mock.MagicMock()
        fn.ipam = ipam
        fn.uuid = 'floating-network'

        with mock.patch.object(
                floating_ip_reaper, 'network') as mock_network, \
                mock.patch.object(
                    floating_ip_reaper, 'interface') as mock_interface:
            mock_network.floating_network.return_value = fn
            mock_network.Networks.return_value = []
            mock_network.Network.from_db.return_value = None
            mock_interface.NetworkInterfaces.return_value = []
            try:
                floating_ip_reaper.reap_floating_ips()
            except Exception:
                # The remainder of the reaper is not fully stubbed. This
                # test is only about how the reservation table is read,
                # and that read happens before anything else.
                pass

    def test_reaper_reads_the_reservation_table_once(self):
        ipam = _ipam_with(25)
        self._run_reaper(ipam)

        self.assertEqual(
            0, ipam.per_address_reads,
            'The reaper read reservations one address at a time; that is '
            'one database round trip each and it grows with the number of '
            'floating addresses (issue 3655)')
        self.assertGreaterEqual(
            ipam.bulk_reads, 1,
            'The reaper did not read the reservation table at all, so this '
            'test proves nothing')

    def test_reaper_read_count_does_not_grow_with_address_count(self):
        # The assertion that actually holds the fix in place: the same
        # code over four times the addresses must cost the same.
        small = _ipam_with(5)
        large = _ipam_with(20)
        self._run_reaper(small)
        self._run_reaper(large)

        self.assertEqual(small.bulk_reads, large.bulk_reads)
        self.assertEqual(0, small.per_address_reads)
        self.assertEqual(0, large.per_address_reads)
