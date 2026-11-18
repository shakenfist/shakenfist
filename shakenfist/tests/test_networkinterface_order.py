# Copyright 2026 Michael Still and contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""The per-instance interface device index is unique (issue 4946).

``network_interfaces.order`` is an identity, not a label:
``interfaces_for_instance()`` keys a dict on it and so silently drops
one of a colliding pair, and the libvirt device naming turns it into
``ethN``. The hot plug endpoint used to read the highest index in use
and then create the interface, two steps with nothing between them, so
two overlapping hot plugs both took the same next index.

The arbiter is now the UNIQUE constraint on ``(instance_uuid, order,
active)``, with ``NetworkInterface.new()`` re-reading the index and
retrying when it loses -- the same shape as the MAC collision retry one
field over. These tests cover the constraint, the index read, the
retry, and the migration's repair of rows which already collide. The
endpoint itself is covered end to end by
``cluster_ci_tests/test_networking.py``'s concurrent hot plug test,
which is the only place real overlapping requests exist.
"""

import uuid
from unittest import mock

import grpc
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError

from shakenfist.daemons.database import main as daemons_database_main
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.network.interface import NetworkInterface
from shakenfist.protos import database_pb2
from shakenfist.schema.network_interface_data import NetworkInterfaceData
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
from shakenfist.tests import base
from shakenfist.tests import dbfixture


def _ni(instance_uuid, order, macaddr, network_uuid, ni_uuid=None):
    return NetworkInterfaceData(
        uuid=ni_uuid or uuid.uuid4(),
        network_uuid=network_uuid,
        instance_uuid=instance_uuid,
        macaddr=macaddr,
        ipv4=None,
        order=order,
        model='virtio',
        version=NetworkInterface.current_version)


class InterfaceOrderConstraintTestCase(dbfixture.MariaDBTableFixture,
                                       base.ShakenFistTestCase):
    """The constraint, exercised through the real INSERT statement."""

    def setUp(self):
        super().setUp()
        self.engine = self.build_engine(
            [mariadb._get_network_interfaces_table])
        self.addCleanup(self.engine.dispose)
        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.table = mariadb._get_network_interfaces_table()
        self.instance = uuid.uuid4()
        self.network = uuid.uuid4()

    def _soft_delete(self, ni_uuid):
        """What _direct_set_state() does when an interface is deleted."""
        with self.engine.connect() as conn:
            conn.execute(sa.update(self.table)
                         .where(self.table.c.uuid == ni_uuid)
                         .values(active=None))
            conn.commit()

    def test_the_constraint_is_declared(self):
        # Named, because the migration adds it by name and a rename on
        # one side only would leave upgraded clusters unprotected.
        self.assertIn(
            'uq_network_interfaces_instance_order_active',
            {c.name for c in self.table.constraints})

    def test_second_claim_on_one_index_is_refused(self):
        first = _ni(self.instance, 1, '02:00:00:00:00:01', self.network)
        second = _ni(self.instance, 1, '02:00:00:00:00:02', self.network)

        self.assertTrue(mariadb._direct_create_network_interface(first))
        self.assertFalse(mariadb._direct_create_network_interface(second))

    def test_distinct_indexes_are_accepted(self):
        for order in range(4):
            self.assertTrue(mariadb._direct_create_network_interface(
                _ni(self.instance, order, '02:00:00:00:00:%02d' % order,
                    self.network)))

    def test_another_instance_may_use_the_same_index(self):
        # The index is per instance, so eth0 exists on every instance.
        self.assertTrue(mariadb._direct_create_network_interface(
            _ni(self.instance, 0, '02:00:00:00:00:01', self.network)))
        self.assertTrue(mariadb._direct_create_network_interface(
            _ni(uuid.uuid4(), 0, '02:00:00:00:00:02', self.network)))

    def test_a_deleted_interface_does_not_hold_its_index(self):
        # active is NULLed on delete, and NULLs do not collide, so the
        # slot is free again immediately rather than after CLEANER_DELAY.
        first = _ni(self.instance, 1, '02:00:00:00:00:01', self.network)
        self.assertTrue(mariadb._direct_create_network_interface(first))
        self._soft_delete(first.uuid)

        self.assertTrue(mariadb._direct_create_network_interface(
            _ni(self.instance, 1, '02:00:00:00:00:02', self.network)))

    def test_two_deleted_interfaces_may_share_an_index(self):
        for macaddr in ('02:00:00:00:00:01', '02:00:00:00:00:02'):
            ni = _ni(self.instance, 1, macaddr, self.network)
            self.assertTrue(mariadb._direct_create_network_interface(ni))
            self._soft_delete(ni.uuid)

    def test_the_macaddr_constraint_still_refuses_a_duplicate(self):
        # A floor: the new constraint must not have displaced the old
        # one, which shares the ``active`` column with it.
        self.assertTrue(mariadb._direct_create_network_interface(
            _ni(self.instance, 0, '02:00:00:00:00:01', self.network)))
        self.assertFalse(mariadb._direct_create_network_interface(
            _ni(self.instance, 1, '02:00:00:00:00:01', self.network)))


class NextOrderTestCase(dbfixture.MariaDBTableFixture,
                        base.ShakenFistTestCase):
    """What NetworkInterface.next_order() can see, against real tables.

    The index read only converges on a free slot if it sees every index
    the UNIQUE constraint is holding. The first version of this fix
    filtered the read on state, and a row takes its slot in the
    constraint at INSERT while new() gives it a state only afterwards,
    so a loser kept re-guessing the index it had just lost until its
    retry budget ran out. Merge CI's concurrent hot plug test caught it;
    these tests catch it without a cluster. The object_states table is
    built so that a state-filtered read has the states it needs to
    return the stated interfaces, and fails only on the unstated one.
    """

    def setUp(self):
        super().setUp()
        self.engine = self.build_engine(
            [mariadb._get_network_interfaces_table,
             mariadb._get_object_states_table])
        self.addCleanup(self.engine.dispose)
        for target, value in (('shakenfist.mariadb._get_engine', self.engine),
                              ('shakenfist.mariadb._use_database_service', False)):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.table = mariadb._get_network_interfaces_table()
        self.instance = uuid.uuid4()
        self.network = uuid.uuid4()
        self.macs = ('02:00:00:00:00:%02x' % i for i in range(1, 255))

    def _plug(self, order, state='created', instance=None):
        ni = _ni(instance or self.instance, order, next(self.macs), self.network)
        self.assertTrue(mariadb._direct_create_network_interface(ni))
        if state:
            with self.engine.connect() as conn:
                conn.execute(sa.insert(mariadb._get_object_states_table()).values(
                    object_uuid=str(ni.uuid), object_type=ObjectType.INTERFACE,
                    state_value=state, update_time=0.0))
                conn.commit()
        return ni

    def test_an_instance_with_no_interfaces_starts_at_zero(self):
        self.assertEqual(0, NetworkInterface.next_order(self.instance))

    def test_the_next_index_is_above_the_highest_in_use(self):
        self._plug(0)
        self._plug(3)
        self.assertEqual(4, NetworkInterface.next_order(self.instance))

    def test_an_index_won_before_its_state_is_written_is_seen(self):
        # The merge CI failure: a sibling hot plug has inserted its row,
        # and so holds index 1 in the constraint, but has not yet been
        # given a state. The next guess must step past it.
        self._plug(0)
        self._plug(1, state=None)
        self.assertEqual(2, NetworkInterface.next_order(self.instance))

    def test_the_guess_is_never_one_the_constraint_refuses(self):
        # The retry loop's contract, end to end against the constraint:
        # whatever a sibling has claimed, the next guess inserts.
        self._plug(0)
        for sibling in (None, 'initial', None):
            self._plug(NetworkInterface.next_order(self.instance), state=sibling)
        guess = NetworkInterface.next_order(self.instance)
        self.assertTrue(mariadb._direct_create_network_interface(
            _ni(self.instance, guess, next(self.macs), self.network)))

    def test_a_soft_deleted_interface_is_stepped_over(self):
        # Not reused until the row is reaped. The index has to be
        # unique, not dense, and the read must not depend on state.
        self._plug(0)
        deleted = self._plug(1, state='deleted')
        with self.engine.connect() as conn:
            conn.execute(sa.update(self.table)
                         .where(self.table.c.uuid == deleted.uuid)
                         .values(active=None))
            conn.commit()
        self.assertEqual(2, NetworkInterface.next_order(self.instance))

    def test_another_instance_is_none_of_our_business(self):
        self._plug(0)
        self._plug(7, instance=uuid.uuid4())
        self.assertEqual(1, NetworkInterface.next_order(self.instance))

    def test_a_string_instance_uuid_is_accepted(self):
        # The hot plug endpoint holds the uuid as a string.
        self._plug(0)
        self.assertEqual(1, NetworkInterface.next_order(str(self.instance)))


class NewRetriesOnOrderCollisionTestCase(base.ShakenFistTestCase):
    """The create retries the index it lost, not just the MAC."""

    def setUp(self):
        super().setUp()
        self.netdesc = {
            'network_uuid': str(uuid.uuid4()),
            'address': None,
            'model': 'virtio'
        }
        self.attempts = []

    def _db_create_failing_once(self, fail_attempts):
        def _fake(interface_uuid, metadata):
            self.attempts.append(
                (metadata['order'], metadata['macaddr']))
            if len(self.attempts) <= fail_attempts:
                raise exceptions.NetworkInterfaceCollision(
                    'refused by a UNIQUE constraint')
        return _fake

    def _run_new(self, fail_attempts=1, order=None, next_orders=(1, 2, 3)):
        orders = iter(next_orders)
        with mock.patch.object(NetworkInterface, '_db_create',
                               side_effect=self._db_create_failing_once(
                                   fail_attempts)), \
             mock.patch.object(NetworkInterface, 'next_order',
                               side_effect=lambda _: next(orders)), \
             mock.patch.object(NetworkInterface, 'from_db',
                               return_value=mock.MagicMock()), \
             mock.patch('shakenfist.mariadb.'
                        'create_network_interface_attributes'), \
             mock.patch('shakenfist.network.network.Network.from_db',
                        return_value=mock.MagicMock()):
            return NetworkInterface.new(
                str(uuid.uuid4()), self.netdesc, str(uuid.uuid4()), order)

    def test_a_lost_index_is_re_read_and_retried(self):
        self._run_new()

        # Two attempts, and the second asked the database for the index
        # again rather than reusing the one which just lost. Retrying
        # with the same index would fail forever.
        self.assertEqual(2, len(self.attempts))
        self.assertEqual([1, 2], [order for order, _ in self.attempts])

    def test_a_caller_supplied_mac_survives_the_retry(self):
        # The MAC is only re-rolled when we generated it. A caller who
        # asked for a specific MAC and lost the index race gets their
        # interface, with their MAC.
        self.netdesc['macaddress'] = '02:00:00:19:e4:b4'
        self._run_new()

        self.assertEqual(2, len(self.attempts))
        self.assertEqual({'02:00:00:19:e4:b4'},
                         {macaddr for _, macaddr in self.attempts})

    def test_a_generated_mac_is_still_re_rolled(self):
        # The pre-existing MAC collision retry, unchanged.
        self._run_new()

        self.assertEqual(2, len(self.attempts))
        macaddrs = [macaddr for _, macaddr in self.attempts]
        self.assertNotEqual(macaddrs[0], macaddrs[1])

    def test_an_explicit_index_survives_a_mac_retry(self):
        # Instance create's shape: the MAC is ours to re-roll, the index
        # is not, so the retry must not quietly renumber the interface
        # the caller listed second as the third.
        self._run_new(fail_attempts=1, order=2)

        self.assertEqual([2, 2], [order for order, _ in self.attempts])

    def test_an_explicit_index_is_never_moved(self):
        # Instance create numbers its interfaces by the order the caller
        # listed the networks in, so that index is not ours to change.
        # With a caller MAC as well there is nothing left to retry, and
        # the failure propagates on the first attempt as it always has.
        self.netdesc['macaddress'] = '02:00:00:19:e4:b4'
        self.assertRaises(exceptions.NetworkInterfaceCollision, self._run_new,
                          fail_attempts=1, order=2)
        self.assertEqual([(2, '02:00:00:19:e4:b4')], self.attempts)

    def test_an_exhausted_retry_budget_still_raises(self):
        self.assertRaises(exceptions.NetworkInterfaceCollision, self._run_new,
                          fail_attempts=10, next_orders=range(1, 12))
        self.assertEqual(10, len(self.attempts))

    def test_an_outage_is_not_retried_as_a_collision(self):
        # A database which cannot be reached is not a lost race. Retrying
        # it would spend the budget and then report a 409 naming the
        # wrong cause, so it leaves on the first attempt.
        def _fake(interface_uuid, metadata):
            self.attempts.append((metadata['order'], metadata['macaddr']))
            raise exceptions.DatabaseUnavailable('database is away')

        with mock.patch.object(NetworkInterface, '_db_create',
                               side_effect=_fake), \
             mock.patch.object(NetworkInterface, 'next_order',
                               return_value=1):
            self.assertRaises(
                exceptions.DatabaseUnavailable, NetworkInterface.new,
                str(uuid.uuid4()), self.netdesc, str(uuid.uuid4()))
        self.assertEqual(1, len(self.attempts))


class CreateFailureIsNotACollisionTestCase(base.ShakenFistTestCase):
    """Only a refused insert may read as a collision, on either path.

    new() retries a collision with a fresh index, and the API answers an
    exhausted budget with a 409 and releases the reserved address. Both
    are wrong for a database which could not be asked: the row may even
    exist. So the direct insert, the gRPC client, the servicer between
    them and NetworkInterface._db_create each keep the two apart.
    """

    def _data(self):
        return _ni(uuid.uuid4(), 0, '02:00:00:00:00:01', uuid.uuid4())

    def test_direct_operational_error_raises(self):
        engine = mock.MagicMock()
        conn = engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = OperationalError(
            'INSERT', {}, Exception('lost connection'))
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            self.assertRaises(exceptions.DatabaseUnavailable,
                              mariadb._direct_create_network_interface,
                              self._data())

    def test_direct_integrity_error_is_a_refusal(self):
        # The negative control for the test above.
        engine = mock.MagicMock()
        conn = engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = IntegrityError(
            'INSERT', {}, Exception('Duplicate entry'))
        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            self.assertFalse(
                mariadb._direct_create_network_interface(self._data()))

    def test_grpc_failure_raises(self):
        with mock.patch('shakenfist.mariadb._get_database_stub'), \
             mock.patch('shakenfist.mariadb._grpc_call',
                        side_effect=grpc.RpcError()):
            self.assertRaises(exceptions.DatabaseUnavailable,
                              mariadb._grpc_create_network_interface,
                              self._data())

    def _servicer_create(self, **direct):
        servicer = daemons_database_main.DatabaseService(mock.MagicMock())
        request = database_pb2.CreateNetworkInterfaceRequest(
            network_interface=database_pb2.NetworkInterfaceStaticData(
                uuid=str(uuid.uuid4()), network_uuid=str(uuid.uuid4()),
                instance_uuid=str(uuid.uuid4()),
                macaddr='02:00:00:00:00:01', order=0, model='virtio',
                version=NetworkInterface.current_version))
        context = mock.MagicMock()
        with mock.patch(
                'shakenfist.mariadb._direct_create_network_interface',
                **direct):
            reply = servicer.CreateNetworkInterface(request, context)
        return reply, context

    def test_servicer_answers_an_outage_with_unavailable(self):
        # UNAVAILABLE so _grpc_call retries it, then raises
        # DatabaseUnavailable rather than returning success=False.
        reply, context = self._servicer_create(
            side_effect=exceptions.DatabaseUnavailable('away'))
        context.set_code.assert_called_once_with(
            grpc.StatusCode.UNAVAILABLE)
        self.assertFalse(reply.success)

    def test_servicer_answers_a_bug_with_internal(self):
        reply, context = self._servicer_create(
            side_effect=ValueError('boom'))
        context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
        self.assertFalse(reply.success)

    def test_servicer_answers_a_refusal_with_ok(self):
        # success=False on an OK status is the one shape which means a
        # collision.
        reply, context = self._servicer_create(return_value=False)
        context.set_code.assert_not_called()
        self.assertFalse(reply.success)

    def _db_create(self, created, add_event):
        with mock.patch('shakenfist.mariadb.create_network_interface',
                        return_value=created), \
             mock.patch('shakenfist.baseobject.eventlog.add_event',
                        add_event):
            NetworkInterface._db_create(str(uuid.uuid4()), {
                'network_uuid': str(uuid.uuid4()),
                'instance_uuid': str(uuid.uuid4()),
                'macaddr': '02:00:00:00:00:01', 'ipv4': None,
                'order': 0, 'model': 'virtio',
                'version': NetworkInterface.current_version})

    def test_a_refused_insert_raises_a_collision_and_audits_nothing(self):
        # new() retries refusals, and each one used to leave a 'db record
        # created' audit event for an interface which never existed.
        add_event = mock.MagicMock()
        self.assertRaises(exceptions.NetworkInterfaceCollision,
                          self._db_create, False, add_event)
        add_event.assert_not_called()

    def test_a_created_row_is_audited(self):
        # The negative control for the test above.
        add_event = mock.MagicMock()
        self._db_create(True, add_event)
        self.assertEqual(['db record created'],
                         [c.args[3] for c in add_event.call_args_list])


class RepairDuplicateInterfaceOrdersTestCase(dbfixture.MariaDBTableFixture,
                                             base.ShakenFistTestCase):
    """The v3 to v4 migration's repair of rows which already collide.

    The duplicates cannot be inserted through the shipped table
    definition any more, so this builds the pre-version-4 schema -- the
    generated table plus the ``active`` column, and no constraint on the
    index -- which is exactly what an upgrading cluster hands the
    migration.
    """

    def setUp(self):
        super().setUp()
        # The fixture restores both of these, so clearing them here is
        # safe; it is what makes the legacy definition possible.
        mariadb._network_interfaces_table = None
        mariadb._metadata = None
        metadata = mariadb._get_metadata()
        self.table = pydantic_to_sqlalchemy_table(
            NetworkInterfaceData, 'network_interfaces', metadata,
            primary_key_fields=['uuid'], include_id_column=False)
        self.table.append_column(
            sa.Column('active', sa.Boolean(), nullable=True))
        self.engine = sa.create_engine('sqlite:///:memory:')
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine, tables=[self.table])

        self.instance = uuid.uuid4()
        self.network = uuid.uuid4()

    def _insert(self, order, macaddr, active=True, instance=None):
        ni_uuid = uuid.uuid4()
        with self.engine.connect() as conn:
            conn.execute(sa.insert(self.table).values(
                uuid=ni_uuid,
                network_uuid=self.network,
                instance_uuid=instance or self.instance,
                macaddr=macaddr,
                ipv4=None,
                order=order,
                model='virtio',
                version=5,
                active=active))
            conn.commit()
        return ni_uuid

    def _orders(self):
        with self.engine.connect() as conn:
            return {
                row[0]: row[1] for row in conn.execute(
                    sa.select(self.table.c.uuid, self.table.c.order)).fetchall()
            }

    def _repair(self):
        with self.engine.connect() as conn:
            return mariadb._migrate_duplicate_interface_orders(conn, self.table)

    def test_a_clean_table_is_left_alone(self):
        before = {self._insert(order, '02:00:00:00:00:%02d' % order): order
                  for order in range(3)}

        self.assertEqual(0, self._repair())
        self.assertEqual(before, self._orders())

    def test_a_collision_moves_the_loser_above_the_highest_index(self):
        first = self._insert(1, '02:00:00:00:00:01')
        second = self._insert(1, '02:00:00:00:00:02')
        third = self._insert(2, '02:00:00:00:00:03')

        self.assertEqual(1, self._repair())

        orders = self._orders()
        # One of the pair keeps the slot, and the other is given a free
        # one. Which keeps it does not matter -- they were already
        # indistinguishable to every reader -- but neither may be moved
        # onto an index already in use.
        self.assertEqual(2, orders[third])
        self.assertEqual({1, 3}, {orders[first], orders[second]})

    def test_three_claimants_are_all_separated(self):
        nis = [self._insert(0, '02:00:00:00:00:%02d' % i) for i in range(3)]

        self.assertEqual(2, self._repair())

        orders = self._orders()
        self.assertEqual(3, len({orders[ni] for ni in nis}))

    def test_two_colliding_groups_do_not_reuse_one_index(self):
        # The second group's new index is computed after the first
        # group's writes, or both groups are handed the same slot.
        nis = [self._insert(0, '02:00:00:00:00:01'),
               self._insert(0, '02:00:00:00:00:02'),
               self._insert(1, '02:00:00:00:00:03'),
               self._insert(1, '02:00:00:00:00:04')]

        self.assertEqual(2, self._repair())

        orders = self._orders()
        self.assertEqual(4, len({orders[ni] for ni in nis}))

    def test_a_deleted_duplicate_is_not_a_collision(self):
        # Soft-deleted rows carry active=NULL, do not participate in the
        # constraint, and so must not be renumbered -- their index is
        # part of the audit record of what the instance used to have.
        live = self._insert(1, '02:00:00:00:00:01')
        gone = self._insert(1, '02:00:00:00:00:02', active=None)
        before = self._orders()

        self.assertEqual(0, self._repair())
        self.assertEqual(before, self._orders())
        self.assertEqual(1, self._orders()[live])
        self.assertEqual(1, self._orders()[gone])

    def test_every_write_is_bounded_to_one_row(self):
        """The lock footprint of the repair is a row, not the table.

        ``ensure-mariadb-schema`` runs while the cluster is still
        serving, so a migration which rewrote the table in one statement
        would hold locks across it (issue 4797). This repair cannot go
        through ``_migration_backfill_batched()`` -- it has no cursor
        range to walk, only the handful of rows a race left behind --
        and the expression-language UPDATE it uses instead is invisible
        to the source guard in test_mariadb_migration_backfills.py, so
        the bound is asserted here.
        """
        self._insert(0, '02:00:00:00:00:01')
        self._insert(0, '02:00:00:00:00:02')
        self._insert(0, '02:00:00:00:00:03')

        class _Recorder:
            def __init__(self, conn):
                self._conn = conn
                self.updates = []

            def execute(self, stmt, *args, **kwargs):
                if str(stmt).lstrip().upper().startswith('UPDATE'):
                    self.updates.append(str(stmt))
                return self._conn.execute(stmt, *args, **kwargs)

            def commit(self):
                return self._conn.commit()

        with self.engine.connect() as conn:
            recorder = _Recorder(conn)
            moved = mariadb._migrate_duplicate_interface_orders(
                recorder, self.table)

        self.assertEqual(2, moved)
        self.assertEqual(2, len(recorder.updates))
        for statement in recorder.updates:
            self.assertIn('WHERE network_interfaces.uuid =', statement)

    def test_one_index_on_two_instances_is_not_a_collision(self):
        self._insert(0, '02:00:00:00:00:01')
        self._insert(0, '02:00:00:00:00:02', instance=uuid.uuid4())
        before = self._orders()

        self.assertEqual(0, self._repair())
        self.assertEqual(before, self._orders())
