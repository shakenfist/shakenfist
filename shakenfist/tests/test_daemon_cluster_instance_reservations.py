# Copyright 2026 Michael Still and contributors
#
# Tests for the sweep which releases instance IPAM reservations no
# interface holds any more (issue 4986). An interface create reserves
# its address before it writes the interface row and deliberately keeps
# the reservation when that write fails with the database unavailable,
# because whether the row committed is unknown. Nothing released those
# reservations afterwards, so each such 503 permanently removed one
# address from the network's pool.

import time
from unittest import mock

import fixtures

from shakenfist.daemons.cluster import scheduled_tasks as st
from shakenfist import ipam
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


IPAM_UUID = '11111111-1111-4111-8111-111111111111'
NETWORK_UUID = '22222222-2222-4222-8222-222222222222'
INSTANCE_UUID = '33333333-3333-4333-8333-333333333333'

LEAKED = '10.0.0.5'
HELD = '10.0.0.6'


class FakeInterface:
    def __init__(self, ipv4):
        self.ipv4 = ipv4


class FakeNetwork:
    def __init__(self, addresses):
        self.networkinterfaces = [FakeInterface(a) for a in addresses]


class ReconcileInstanceAddressReservationsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        # An in-memory IPAM so the reservation table is real: the sweep
        # reads it, filters it and releases through the same code a
        # deployed cluster uses, with only the network's interface list
        # and the IPAM listing faked.
        self.ipm = ipam.IPAM.new(
            IPAM_UUID, None, NETWORK_UUID, '10.0.0.0/24',
            in_memory_only=True)

        self.ipams = self.useFixture(
            fixtures.MockPatchObject(
                st, 'IPAMs', return_value=[self.ipm])).mock
        self.add_event = self.useFixture(
            fixtures.MockPatchObject(
                st.eventlog, 'add_event_multi')).mock

    def _reserve(self, address, reservation_type, age):
        """Reserve an address as if it had been reserved `age` ago."""
        with mock.patch('time.time', return_value=time.time() - age):
            self.assertTrue(self.ipm.reserve(
                address, (ObjectType.INSTANCE, INSTANCE_UUID),
                reservation_type, ''))

    def _sweep(self, network_addresses=None, network=True):
        net = FakeNetwork(network_addresses or []) if network else None
        with mock.patch.object(st.Network, 'from_db', return_value=net):
            st.reconcile_instance_address_reservations()

    def test_releases_a_reservation_no_interface_holds(self):
        self._reserve(LEAKED, ReservationType.INSTANCE,
                      st.INSTANCE_RESERVATION_MINIMUM_AGE + 60)

        self._sweep()

        self.assertNotIn(LEAKED, self.ipm.in_use)

    def test_the_audit_event_names_the_instance(self):
        self._reserve(LEAKED, ReservationType.INSTANCE,
                      st.INSTANCE_RESERVATION_MINIMUM_AGE + 60)

        self._sweep()

        self.add_event.assert_called_once()
        objects = self.add_event.call_args.args[1]
        self.assertEqual(self.ipm, objects[0])
        # add_event_multi() stringifies the uuid itself; the schema types
        # user_uuid as a UUID4, so it arrives here as a UUID object.
        self.assertEqual(
            (ObjectType.INSTANCE, INSTANCE_UUID),
            (objects[1][0], str(objects[1][1])))
        self.assertEqual(
            {'address': LEAKED}, self.add_event.call_args.kwargs['extra'])

    def test_keeps_a_reservation_an_attached_interface_holds(self):
        # The whole reason the 503 path keeps the reservation: releasing
        # an address a committed interface row holds hands it to a
        # second guest.
        self._reserve(HELD, ReservationType.INSTANCE,
                      st.INSTANCE_RESERVATION_MINIMUM_AGE + 60)

        self._sweep(network_addresses=[HELD])

        self.assertIn(HELD, self.ipm.in_use)
        self.add_event.assert_not_called()

    def test_keeps_a_reservation_inside_the_window(self):
        # A create reserves the address before it writes the interface
        # row, so a young reservation may be an in-flight create.
        self._reserve(LEAKED, ReservationType.INSTANCE,
                      st.INSTANCE_RESERVATION_MINIMUM_AGE - 60)

        self._sweep()

        self.assertIn(LEAKED, self.ipm.in_use)
        self.add_event.assert_not_called()

    def test_leaves_other_reservation_types_alone(self):
        # The gateway, network and broadcast reservations IPAM.new()
        # makes are all older than nothing and held by no interface, and
        # a manual reservation is held for something outside the cluster
        # entirely.
        self._reserve('10.0.0.9', ReservationType.MANUAL,
                      st.INSTANCE_RESERVATION_MINIMUM_AGE + 60)
        self._reserve('10.0.0.10', ReservationType.FLOATING,
                      st.INSTANCE_RESERVATION_MINIMUM_AGE + 60)
        before = set(self.ipm.in_use)

        self._sweep()

        self.assertEqual(before, set(self.ipm.in_use))
        self.add_event.assert_not_called()

    def test_releases_only_as_an_instance_reservation(self):
        # The type check has to be atomic with the release (issue 4261),
        # which is what expected_type buys: a create which took the
        # address over between the read and the release keeps it.
        self._reserve(LEAKED, ReservationType.INSTANCE,
                      st.INSTANCE_RESERVATION_MINIMUM_AGE + 60)

        with mock.patch.object(
                self.ipm, 'release', return_value=True) as mock_release:
            self._sweep()

        mock_release.assert_called_once_with(
            LEAKED, expected_type=ReservationType.INSTANCE)

    def test_a_refused_release_records_nothing(self):
        self._reserve(LEAKED, ReservationType.INSTANCE,
                      st.INSTANCE_RESERVATION_MINIMUM_AGE + 60)

        with mock.patch.object(self.ipm, 'release', return_value=False):
            self._sweep()

        self.add_event.assert_not_called()

    def test_a_missing_network_releases_nothing(self):
        # Without the network there is no list of attached interfaces to
        # compare against, and the cluster wide cleanup deletes an IPAM
        # whose network has gone anyway.
        self._reserve(LEAKED, ReservationType.INSTANCE,
                      st.INSTANCE_RESERVATION_MINIMUM_AGE + 60)

        self._sweep(network=False)

        self.assertIn(LEAKED, self.ipm.in_use)
        self.add_event.assert_not_called()

    def test_an_ipam_with_no_candidates_is_not_read_further(self):
        # Every network pays this sweep's cost hourly, so an IPAM with
        # nothing to do must not also cost a network read.
        with mock.patch.object(st.Network, 'from_db') as mock_network:
            st.reconcile_instance_address_reservations()

        mock_network.assert_not_called()
        self.ipams.assert_called_once()
