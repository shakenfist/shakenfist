# Copyright 2026 Michael Still and contributors
#
# Tests for the floating IP reaper's reconciliation sweep,
# specifically the "rescue" branch which re-reserves active floating
# addresses whose IPAM reservation has gone missing. This branch
# previously reserved the wrong address (a leftover variable from the
# gateway loop), leaving the actual floating address unreserved and
# eligible for double allocation.

from unittest import mock

from shakenfist.daemons.network import floating_ip_reaper
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class FakeIPAM:
    def __init__(self, free_addresses):
        self.free_addresses = free_addresses
        self.reserve_calls = []
        self.in_use = set()
        self.broadcast_address = '192.168.10.255'
        self.network_address = '192.168.10.0'

    def is_free(self, address):
        return address in self.free_addresses

    def reserve(self, address, user, reservation_type, comment):
        self.reserve_calls.append(
            (address, user, reservation_type, comment))
        return True

    def get_address_at_index(self, idx):
        return '192.168.10.%d' % idx

    def get_haloed_addresses(self):
        return iter([])

    def get_reservation(self, address):
        return None

    def get_allocation_age(self, address):
        return 0

    def release(self, address):
        pass


class FloatingIPReaperTestCase(base.ShakenFistTestCase):
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

        ipam = FakeIPAM(free_addresses={'192.168.10.42'})
        fake_floating_network = mock.MagicMock()
        fake_floating_network.ipam = ipam

        with mock.patch.object(
                floating_ip_reaper.network, 'floating_network',
                return_value=fake_floating_network), \
            mock.patch.object(
                floating_ip_reaper.network, 'Networks',
                return_value=[fake_gw_network]), \
            mock.patch.object(
                floating_ip_reaper.interface, 'NetworkInterfaces',
                return_value=[fake_ni]):
            self.assertTrue(floating_ip_reaper.reap_floating_ips())

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
