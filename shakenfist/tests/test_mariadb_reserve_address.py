# Copyright 2026 Michael Still and contributors
#
# Direct SQL tests for _direct_reserve_address() (issue #4987).
#
# _grpc_call() re-sends a call which failed with DEADLINE_EXCEEDED or
# CANCELLED, so a reserve whose server-side commit landed but whose reply
# was lost arrives a second time and fails on the (ipam_uuid, address)
# primary key. These tests run the production INSERT against a real
# (sqlite) database, because what is under test is which refusals the
# primary key raises and what the re-read makes of the committed row --
# neither of which a mocked engine can show.

import time
import uuid
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.ipam_reservation import IPAMReservation
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.tests import base
from shakenfist.tests import dbfixture


HALO_DURATION = 300

IPAM_UUID = uuid.UUID('11111111-1111-4111-8111-111111111111')
INSTANCE_UUID = '22222222-2222-4222-8222-222222222222'
OTHER_INSTANCE_UUID = '33333333-3333-4333-8333-333333333333'


def _reservation(address='10.0.0.5', user_uuid=INSTANCE_UUID,
                 reservation_type=ReservationType.INSTANCE,
                 reserved_at=None, comment='nic 0'):
    return IPAMReservation(
        ipam_uuid=IPAM_UUID,
        address=address,
        reservation_type=reservation_type,
        user_type=ObjectType.INSTANCE,
        user_uuid=user_uuid,
        reserved_at=reserved_at if reserved_at is not None else time.time(),
        comment=comment)


class ReserveAddressTestCase(dbfixture.MariaDBTableFixture,
                             base.ShakenFistTestCase):
    """The reserve INSERT and its refusals, against a real database."""

    def setUp(self):
        super().setUp()
        self.engine = self.build_engine(
            [mariadb._get_ipam_reservations_table])
        self.addCleanup(self.engine.dispose)
        self.table = mariadb._get_ipam_reservations_table()
        for target, value in (
                ('shakenfist.mariadb._get_engine', self.engine),
                ('shakenfist.mariadb._use_database_service', False)):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

        # dbfixture replaces mariadb.config with a bare MagicMock, and the
        # halo expiry cutoff is arithmetic on this value.
        mariadb.config.IP_DELETION_HALO_DURATION = HALO_DURATION

    def _rows(self):
        with self.engine.connect() as conn:
            return conn.execute(sa.select(self.table)).fetchall()

    def test_a_first_reserve_is_granted(self):
        # A floor: the ordinary path is unchanged.
        self.assertTrue(mariadb._direct_reserve_address(_reservation()))
        self.assertEqual(1, len(self._rows()))

    def test_a_redelivered_reserve_is_not_in_use(self):
        # The defect: read as "in use", a random allocation moves on to
        # another address and leaves this one reserved with nothing
        # holding it, and an explicit reserve reports a conflict for a
        # reservation the caller owns.
        reservation = _reservation()
        self.assertTrue(mariadb._direct_reserve_address(reservation))
        self.assertTrue(mariadb._direct_reserve_address(reservation))
        self.assertEqual(1, len(self._rows()))

    def test_another_users_reservation_still_refuses(self):
        # A floor: the whole point of the INSERT is that a second user
        # cannot take an address a first one holds.
        self.assertTrue(mariadb._direct_reserve_address(_reservation()))
        self.assertFalse(mariadb._direct_reserve_address(
            _reservation(user_uuid=OTHER_INSTANCE_UUID)))
        self.assertEqual(INSTANCE_UUID, str(self._rows()[0].user_uuid))

    def test_the_same_user_at_a_different_time_still_refuses(self):
        # Two reserves of one address for one user are different calls,
        # not two deliveries of one call, and reserved_at is what tells
        # them apart. Granting the second would let a re-plug believe it
        # holds an address the first plug's row describes.
        first = _reservation(reserved_at=1000.0)
        self.assertTrue(mariadb._direct_reserve_address(first))
        self.assertFalse(mariadb._direct_reserve_address(
            _reservation(reserved_at=2000.0)))
        self.assertEqual(1000.0, self._rows()[0].reserved_at)

    def test_a_differing_comment_still_refuses(self):
        # Every stored column is compared, not just the identity ones.
        first = _reservation(reserved_at=1000.0, comment='nic 0')
        self.assertTrue(mariadb._direct_reserve_address(first))
        self.assertFalse(mariadb._direct_reserve_address(
            _reservation(reserved_at=1000.0, comment='nic 1')))
        self.assertEqual('nic 0', self._rows()[0].comment)

    def test_a_redelivered_halo_evicting_reserve_is_not_in_use(self):
        # The evict_halo path has the same shape: the guarded UPDATE
        # requires the row still be a deletion-halo, so once an earlier
        # delivery has taken the halo over the retry matches zero rows.
        reservation = _reservation()
        self.assertTrue(mariadb._direct_reserve_address(
            reservation, evict_halo=True))
        self.assertTrue(mariadb._direct_reserve_address(
            reservation, evict_halo=True))
        self.assertEqual(1, len(self._rows()))

    def test_a_halo_is_still_evicted(self):
        # A floor: a halo belonging to nobody is still taken over, which
        # is a different row from the one asked for.
        halo = _reservation(reservation_type=ReservationType.DELETION_HALO,
                            comment=None)
        self.assertTrue(mariadb._direct_reserve_address(halo))
        self.assertTrue(mariadb._direct_reserve_address(
            _reservation(), evict_halo=True))
        self.assertEqual(ReservationType.INSTANCE,
                         self._rows()[0].reservation_type)

    def test_a_live_halo_is_not_evicted_without_permission(self):
        # A floor: the idempotency re-read must not let a live halo
        # through the plain path, which answers a refusal. This is what
        # the halo is *for*, so it is the one case the expiry takeover
        # below must leave alone.
        halo = _reservation(reservation_type=ReservationType.DELETION_HALO,
                            comment=None)
        self.assertTrue(mariadb._direct_reserve_address(halo))
        self.assertFalse(mariadb._direct_reserve_address(_reservation()))
        self.assertEqual(ReservationType.DELETION_HALO,
                         self._rows()[0].reservation_type)

    def test_an_expired_halo_is_taken_over_without_permission(self):
        # Issue #4175: an expired halo is a free address which still has
        # a row, so the reserve statement takes it over. That is what
        # replaces deleting every expired halo on the IPAM before each
        # reservation attempt.
        halo = _reservation(
            reservation_type=ReservationType.DELETION_HALO, comment=None,
            reserved_at=time.time() - HALO_DURATION - 1)
        self.assertTrue(mariadb._direct_reserve_address(halo))
        self.assertTrue(mariadb._direct_reserve_address(_reservation()))
        row = self._rows()[0]
        self.assertEqual(1, len(self._rows()))
        self.assertEqual(ReservationType.INSTANCE, row.reservation_type)
        self.assertEqual(INSTANCE_UUID, str(row.user_uuid))

    def test_a_halo_one_second_from_expiry_is_still_refused(self):
        # The boundary, so that "expired" cannot drift into "nearly".
        halo = _reservation(
            reservation_type=ReservationType.DELETION_HALO, comment=None,
            reserved_at=time.time() - HALO_DURATION + 1)
        self.assertTrue(mariadb._direct_reserve_address(halo))
        self.assertFalse(mariadb._direct_reserve_address(_reservation()))
        self.assertEqual(ReservationType.DELETION_HALO,
                         self._rows()[0].reservation_type)

    def test_an_expired_halo_does_not_let_a_real_reservation_go(self):
        # A floor: expiry applies to halos only. A reservation held by
        # someone else is refused however old it is.
        self.assertTrue(mariadb._direct_reserve_address(
            _reservation(reserved_at=time.time() - HALO_DURATION * 10)))
        self.assertFalse(mariadb._direct_reserve_address(
            _reservation(user_uuid=OTHER_INSTANCE_UUID)))
        self.assertEqual(INSTANCE_UUID, str(self._rows()[0].user_uuid))

    def test_a_halo_re_haloed_after_the_read_is_not_stolen(self):
        # The pre-check reads the row, and a release landing between that
        # read and the UPDATE re-halos it with a fresh reserved_at. The
        # expiry therefore has to be in the UPDATE's WHERE clause as well,
        # or the takeover would steal a halo which is live again.
        halo = _reservation(reservation_type=ReservationType.DELETION_HALO,
                            comment=None)
        self.assertTrue(mariadb._direct_reserve_address(halo))

        stale_read = mock.MagicMock()
        stale_read.reservation_type = ReservationType.DELETION_HALO
        stale_read.reserved_at = time.time() - HALO_DURATION - 1
        with mock.patch.object(mariadb, '_read_reservation_row',
                               return_value=(True, stale_read)):
            self.assertFalse(mariadb._direct_reserve_address(_reservation()))
        self.assertEqual(ReservationType.DELETION_HALO,
                         self._rows()[0].reservation_type)

    def test_an_address_in_use_issues_no_update(self):
        # The pre-check is there so that the common refusal -- a random
        # allocation walking addresses which are genuinely reserved --
        # costs the INSERT and the re-read, and not a third statement
        # which could not have matched (issue #4175).
        #
        # The row is older than the halo period, which is what a standing
        # instance's address looks like. Only its *type* stops the
        # takeover, so this is the case where the type pre-check is the
        # one thing keeping the UPDATE off the wire.
        self.assertTrue(mariadb._direct_reserve_address(
            _reservation(reserved_at=time.time() - HALO_DURATION * 10)))

        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sa.event.listen(self.engine, 'before_cursor_execute', record)
        self.addCleanup(sa.event.remove, self.engine,
                        'before_cursor_execute', record)
        self.assertFalse(mariadb._direct_reserve_address(
            _reservation(user_uuid=OTHER_INSTANCE_UUID)))
        self.assertEqual(
            [], [s for s in statements if s.lstrip().upper().startswith('UPDATE')])

    def test_an_unreadable_row_refuses_rather_than_taking_a_halo(self):
        # The read which decides whether a halo may be taken can fail. It
        # cannot then tell a live halo from an expired one, so a caller
        # without evict_halo is refused rather than guessing.
        halo = _reservation(
            reservation_type=ReservationType.DELETION_HALO, comment=None,
            reserved_at=time.time() - HALO_DURATION - 1)
        self.assertTrue(mariadb._direct_reserve_address(halo))
        with mock.patch.object(mariadb, '_read_reservation_row',
                               return_value=(False, None)):
            self.assertFalse(mariadb._direct_reserve_address(_reservation()))
        self.assertEqual(ReservationType.DELETION_HALO,
                         self._rows()[0].reservation_type)

    def test_an_unreadable_row_still_lets_evict_halo_try(self):
        # ... while the caller which asked to evict any halo still issues
        # the guarded UPDATE, which is safe on its own WHERE clause. This
        # is the behaviour the pre-check must not quietly remove.
        halo = _reservation(reservation_type=ReservationType.DELETION_HALO,
                            comment=None)
        self.assertTrue(mariadb._direct_reserve_address(halo))
        with mock.patch.object(mariadb, '_read_reservation_row',
                               return_value=(False, None)):
            self.assertTrue(mariadb._direct_reserve_address(
                _reservation(), evict_halo=True))
        self.assertEqual(ReservationType.INSTANCE,
                         self._rows()[0].reservation_type)


class ReservationAlreadyInsertedTestCase(dbfixture.MariaDBTableFixture,
                                         base.ShakenFistTestCase):
    """The re-read helper on its own, including its failure answer."""

    def setUp(self):
        super().setUp()
        self.engine = self.build_engine(
            [mariadb._get_ipam_reservations_table])
        self.addCleanup(self.engine.dispose)
        self.table = mariadb._get_ipam_reservations_table()

    def test_a_missing_row_is_not_ours(self):
        self.assertFalse(mariadb._reservation_already_inserted(
            self.engine, self.table, _reservation()))

    def test_an_operational_error_is_not_ours(self):
        engine = mock.MagicMock()
        engine.connect.side_effect = mariadb.OperationalError(
            'select', {}, Exception('gone'))
        self.assertFalse(mariadb._reservation_already_inserted(
            engine, self.table, _reservation()))
