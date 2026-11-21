# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the shape hold booking primitive.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module behind one MariaDB install;
developers can point at a local instance). The unit tests in
test_mariadb_capacity_holds.py all run against a mocked connection and
assert on compiled statement text, which cannot exercise the parts most
likely to break:

* the claim-side sum invariant actually binding -- that
  ``limit_* >= COALESCE(SUM(holds), 0) + requested`` inside an UPDATE
  WHERE really does refuse the booking that would overflow the claim
  and admit the one that exactly fits,
* the per-node guard actually binding against real ``used_*`` and
  ``held_*`` columns, including a booking refused on the second of two
  nodes leaving the first node's counters where they were,
* **concurrency, which is the whole reason the invariant has the shape
  it has.** "Two bookings of one claim cannot both pass the sum" is
  entirely a property of InnoDB re-evaluating a guarded UPDATE's WHERE
  -- subquery included -- against the rows as committed by whoever held
  the lock first, and no mock can say anything about it. A separate
  SELECT SUM(...) ahead of the UPDATE would pass every unit test in the
  sibling module and still let two bookings overfill a claim,
* the ER_CHECKREAD invariant. The booking transaction opens with a
  guarded UPDATE whose WHERE carries the sum, which is only observably
  necessary against a server with ``innodb_snapshot_isolation`` ON (the
  default from MariaDB 11.6.2). The concurrency case reports the regime
  it ran under,
* the real CREATE TABLE for namespace_claim_holds, which the unit suite
  can only assert as rendered DDL.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database (and this one flips its
collation to utf8mb4_bin for the duration of each test), so they must be
run serially -- ``stestr run --serial``, as CI does.
"""

import functools
import os
import sys
import threading
import time
import unittest
from unittest import mock
from uuid import UUID
from uuid import uuid4

import sqlalchemy as sa
from testtools import content

from shakenfist.constants import GiB
from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

TEST_TABLES = [
    'scheduler_node_capacity',
    'namespace_claims',
    'namespace_claim_holds',
    'cluster_capacity',
    'node_metrics',
    'object_references',
    'object_states',
    'instances',
    'nodes',
    'schema_versions',
]

# How many holders race for the last room in the claim.
RACE_THREADS = 8
RACE_ROUNDS = 5


class _LiveHoldFixture(base.ShakenFistTestCase):
    """Shared live-MariaDB scaffolding for the suites in this module."""

    ENGINE_KWARGS: dict = {}

    def _prepare_database(self):
        self.engine = sa.create_engine(os.environ[DSN_ENV],
                                       **self.ENGINE_KWARGS)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        # utf8mb4_bin, for the same reason the other capacity live
        # suites use it: the shared usage aggregation spans two enum
        # storage conventions whose spellings differ only in case, so a
        # binding naming the wrong one is papered over by the default
        # collations and silently matches nothing under a _bin one.
        with self.engine.connect() as conn:
            database = conn.execute(sa.text('SELECT DATABASE()')).scalar()
            previous_collation = conn.execute(sa.text(
                'SELECT @@collation_database')).scalar()
            conn.execute(sa.text(
                f'ALTER DATABASE `{database}` CHARACTER SET utf8mb4 '
                f'COLLATE utf8mb4_bin'))
            conn.commit()
        self.addCleanup(self._restore_collation, database,
                        previous_collation)

        mariadb._ensure_schema_versions_table(self.engine)
        for ensure in (mariadb._ensure_object_states_schema,
                       mariadb._ensure_object_references_schema,
                       mariadb._ensure_nodes_schema,
                       mariadb._ensure_instances_schema,
                       mariadb._ensure_node_metrics_schema,
                       mariadb._ensure_scheduler_node_capacity_schema,
                       mariadb._ensure_namespace_claims_schema,
                       mariadb._ensure_namespace_claim_holds_schema,
                       mariadb._ensure_cluster_capacity_schema):
            ensure(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _restore_collation(self, database, collation):
        with self.engine.connect() as conn:
            conn.execute(sa.text(
                f'ALTER DATABASE `{database}` COLLATE {collation}'))
            conn.commit()

    def _fill_required(self, table, known):
        """Fill NOT NULL columns these suites do not care about."""
        row = dict(known)
        for col in table.columns:
            if (col.name in row or col.nullable
                    or col.server_default is not None
                    or col.default is not None):
                continue
            if isinstance(col.type, (sa.Integer, sa.BigInteger)):
                row[col.name] = 0
            elif isinstance(col.type, (sa.Float, sa.Double)):
                row[col.name] = 0.0
            elif isinstance(col.type, sa.JSON):
                row[col.name] = {}
            elif isinstance(col.type, sa.Boolean):
                row[col.name] = False
            elif isinstance(col.type, sa.Uuid):
                row[col.name] = uuid4()
            else:
                row[col.name] = ''
        return row

    def _insert(self, conn, table, **known):
        conn.execute(sa.insert(table).values(
            self._fill_required(table, known)))

    def _set_cluster(self, total_cpus=1000000, total_memory_mb=100000000,
                     total_disk_gb=10000000):
        table = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.delete(table))
            self._insert(conn, table, id=1, total_cpus=total_cpus,
                         total_memory_mb=total_memory_mb,
                         total_disk_gb=total_disk_gb,
                         updated_at=sa.func.now())
            conn.commit()

    def _add_node(self, limit_cpus=16, limit_memory_mb=16384,
                  limit_disk_gb=200, used_cpus=0, used_memory_mb=0,
                  used_disk_gb=0, held_cpus=0, held_memory_mb=0,
                  held_disk_gb=0):
        """Seed a hypervisor with a capacity row a booking can charge."""
        node = uuid4()
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        metrics_t = mariadb._get_node_metrics_table()

        with self.engine.connect() as conn:
            self._insert(conn, capacity_t, node_uuid=node,
                         limit_cpus=limit_cpus,
                         limit_memory_mb=limit_memory_mb,
                         limit_disk_gb=limit_disk_gb, used_cpus=used_cpus,
                         used_memory_mb=used_memory_mb,
                         used_disk_gb=used_disk_gb, held_cpus=held_cpus,
                         held_memory_mb=held_memory_mb,
                         held_disk_gb=held_disk_gb, expected_demand=0.0,
                         updated_at=sa.func.now())
            self._insert(conn, metrics_t, node_uuid=node,
                         cpu_schedulable=limit_cpus, cpu_load_1=0.5,
                         memory_max=65536, memory_reserved_mb=2048,
                         disk_free_instances=500 * GiB,
                         disk_reservation_gb=20, metrics_json={},
                         is_hypervisor=True, timestamp=time.time(),
                         fqdn=f'n-{str(node)[:8]}')
            conn.commit()
        return node

    def _create_claim(self, namespace='ci-1', limit_cpus=16,
                      limit_memory_mb=16384, limit_disk_gb=200,
                      expires_in_seconds=3600, claim_uuid=None):
        result = mariadb._direct_create_namespace_claim(
            str(claim_uuid or uuid4()), namespace, limit_cpus,
            limit_memory_mb, limit_disk_gb, expires_in_seconds)
        self.assertTrue(result['created'],
                        f'{result["error"]} {result["refused_reason"]}')
        return result['claim']['uuid']

    def _book(self, claim_uuid, shapes):
        return mariadb._direct_book_claim_holds(claim_uuid, shapes)

    def _shape(self, node, cpus=4, memory_mb=4096, disk_gb=40):
        return {'cpus': cpus, 'memory_mb': memory_mb, 'disk_gb': disk_gb,
                'node_uuid': str(node)}

    def _node_row(self, node):
        table = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table).where(
                table.c.node_uuid == node)).first()

    def _hold_rows(self, claim_uuid=None):
        table = mariadb._get_namespace_claim_holds_table()
        statement = sa.select(table)
        if claim_uuid is not None:
            statement = statement.where(
                table.c.claim_uuid == UUID(claim_uuid))
        with self.engine.connect() as conn:
            return conn.execute(statement).fetchall()

    def _claim_row(self, claim_uuid):
        table = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table).where(
                table.c.uuid == UUID(claim_uuid))).first()

    def _set_claim_used(self, claim_uuid, cpus=0, memory_mb=0, disk_gb=0):
        """Charge the claim as though it were hosting instances.

        The shrink floor is about the claim's own usage, and seeding the
        counter is how a test gets there without having to place real
        instances in a suite that is about bookings.
        """
        table = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(table).where(
                table.c.uuid == UUID(claim_uuid)).values(
                    used_cpus=cpus, used_memory_mb=memory_mb,
                    used_disk_gb=disk_gb))
            conn.commit()

    def _lapse_claim(self, claim_uuid):
        """Put the claim's expiry in the past, leaving its state active.

        Which is the state the reconciler's expiry sweep exists to find:
        coverage that has run out but which nothing has lapsed yet.
        """
        table = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(table).where(
                table.c.uuid == UUID(claim_uuid)).values(
                    expires_at=sa.text('NOW() - INTERVAL 1 HOUR')))
            conn.commit()

    def _make_schedulable(self, node):
        """Give a node the rows the reconciler needs to keep its row.

        A pass deletes the capacity row of anything that is not an
        active hypervisor it can find in the nodes table, so a test
        which runs a pass and then reads held_* has to seed both.
        """
        with self.engine.connect() as conn:
            self._insert(conn, mariadb._get_nodes_table(), uuid=node,
                         fqdn=f'n-{str(node)[:8]}')
            self._insert(conn, mariadb._get_object_states_table(),
                         object_type=ObjectType.NODE, object_uuid=str(node),
                         state_value='created', update_time=time.time())
            conn.commit()

    def _reconcile(self):
        return mariadb._direct_reconcile_scheduler_capacity(2.5, 600, 1.0)

    def _release(self, claim_uuid, hold_uuids=None, all_holds=False):
        return mariadb._direct_release_claim_holds(
            claim_uuid, hold_uuids or [], all_holds)

    def _shrink(self, claim_uuid, **limits):
        fields = [f'limit_{name}' for name in limits]
        return mariadb._direct_update_namespace_claim(
            claim_uuid, fields, limits.get('cpus', 0),
            limits.get('memory_mb', 0), limits.get('disk_gb', 0), 0)

    def _report(self, label, text):
        self.addDetail(label, content.text_content(text))
        sys.stderr.write(f'\n{label}: {text}\n')


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class HoldBookingLiveTestCase(_LiveHoldFixture):
    """Run real bookings against a real MariaDB, where the guards bind."""

    def setUp(self):
        super().setUp()
        self._prepare_database()
        self._set_cluster()

    # ------------------------------------------------------------------
    # Booking
    # ------------------------------------------------------------------

    def test_a_booking_charges_exactly_the_nodes_it_names(self):
        node_a = self._add_node()
        node_b = self._add_node()
        spare = self._add_node()
        claim = self._create_claim()

        result = self._book(claim, [
            self._shape(node_a, cpus=4, memory_mb=4096, disk_gb=40),
            self._shape(node_b, cpus=2, memory_mb=2048, disk_gb=20),
        ])

        self.assertTrue(result['booked'], result['error'])
        self.assertEqual(2, len(result['holds']))
        self.assertEqual(str(node_a), result['holds'][0]['node_uuid'])
        self.assertEqual(str(node_b), result['holds'][1]['node_uuid'])
        self.assertEqual('ci-1', result['holds'][0]['namespace'])
        self.assertEqual('', result['holds'][0]['instance_uuid'])

        self.assertEqual(4, self._node_row(node_a).held_cpus)
        self.assertEqual(4096, self._node_row(node_a).held_memory_mb)
        self.assertEqual(40, self._node_row(node_a).held_disk_gb)
        self.assertEqual(2, self._node_row(node_b).held_cpus)
        self.assertEqual(0, self._node_row(spare).held_cpus)

        # One row per shape, and nothing on the claim row moved: a
        # hold's room is not counted in the claim's used_*.
        self.assertEqual(2, len(self._hold_rows(claim)))
        self.assertEqual(
            0, mariadb._direct_get_namespace_claim(claim)['used_cpus'])

    def test_a_booking_does_not_charge_the_cluster_singleton(self):
        # A hold sits inside its claim's limits, and those limits are
        # already inside claimed_*, so a cluster-side counter would be a
        # second ledger that agreed with the first until the next write.
        node = self._add_node()
        claim = self._create_claim(limit_cpus=16)
        table = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            before = conn.execute(sa.select(table)).first()

        self.assertTrue(self._book(claim, [self._shape(node)])['booked'])

        with self.engine.connect() as conn:
            after = conn.execute(sa.select(table)).first()
        self.assertEqual(before.claimed_cpus, after.claimed_cpus)
        self.assertEqual(before.unclaimed_used_cpus, after.unclaimed_used_cpus)

    def test_a_booking_that_exactly_fills_the_claim_is_admitted(self):
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=1000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=8192,
                                   limit_disk_gb=80)

        result = self._book(claim, [
            self._shape(node, cpus=8, memory_mb=8192, disk_gb=80)])

        self.assertTrue(result['booked'], result['refused_reason'])
        self.assertEqual(8, self._node_row(node).held_cpus)

    def test_a_booking_over_the_claims_limit_is_refused_whole(self):
        node_a = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                                limit_disk_gb=1000)
        node_b = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                                limit_disk_gb=1000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=100000,
                                   limit_disk_gb=1000)

        result = self._book(claim, [
            self._shape(node_a, cpus=5, memory_mb=1024, disk_gb=10),
            self._shape(node_b, cpus=4, memory_mb=1024, disk_gb=10),
        ])

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['booked'])
        self.assertEqual('claim_capacity', result['refused_reason'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(8.0, detail['cpus']['limit'])
        self.assertEqual(0.0, detail['cpus']['used'])
        self.assertEqual(9.0, detail['cpus']['requested'])
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertFalse(detail['memory_mb']['exceeded'])

        # All or nothing: neither node moved and no row was written.
        self.assertEqual(0, self._node_row(node_a).held_cpus)
        self.assertEqual(0, self._node_row(node_b).held_cpus)
        self.assertEqual([], self._hold_rows(claim))

    def test_holds_already_booked_count_against_the_claim(self):
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=1000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=100000,
                                   limit_disk_gb=1000)
        self.assertTrue(self._book(claim, [
            self._shape(node, cpus=6, memory_mb=1024, disk_gb=10)])['booked'])

        result = self._book(claim, [
            self._shape(node, cpus=3, memory_mb=1024, disk_gb=10)])

        self.assertFalse(result['booked'])
        self.assertEqual('claim_capacity', result['refused_reason'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(6.0, detail['cpus']['used'])
        self.assertEqual(3.0, detail['cpus']['requested'])
        # And the room the first booking took is still booked.
        self.assertEqual(6, self._node_row(node).held_cpus)
        self.assertEqual(1, len(self._hold_rows(claim)))

    def test_a_second_booking_inside_the_limit_is_admitted(self):
        # The other half of the test above, so the guard cannot
        # degenerate into "one booking per claim".
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=1000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=100000,
                                   limit_disk_gb=1000)
        self.assertTrue(self._book(claim, [
            self._shape(node, cpus=6, memory_mb=1024, disk_gb=10)])['booked'])

        self.assertTrue(self._book(claim, [
            self._shape(node, cpus=2, memory_mb=1024, disk_gb=10)])['booked'])
        self.assertEqual(8, self._node_row(node).held_cpus)
        self.assertEqual(2, len(self._hold_rows(claim)))

    def test_a_booking_over_a_nodes_room_is_refused_whole(self):
        node_a = self._add_node(limit_cpus=16)
        node_b = self._add_node(limit_cpus=4)
        claim = self._create_claim(limit_cpus=100, limit_memory_mb=1000000,
                                   limit_disk_gb=10000)

        result = self._book(claim, [
            self._shape(node_a, cpus=4, memory_mb=1024, disk_gb=10),
            self._shape(node_b, cpus=8, memory_mb=1024, disk_gb=10),
        ])

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['booked'])
        self.assertEqual('node_capacity', result['refused_reason'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(4.0, detail['cpus']['limit'])
        self.assertEqual(8.0, detail['cpus']['requested'])
        self.assertTrue(detail['cpus']['exceeded'])

        # The node that did fit was charged inside the same transaction
        # and rolled back with it.
        self.assertEqual(0, self._node_row(node_a).held_cpus)
        self.assertEqual(0, self._node_row(node_b).held_cpus)
        self.assertEqual([], self._hold_rows(claim))

    def test_the_node_guard_counts_placements_as_well_as_holds(self):
        # used + held + request <= limit. A node with eight of its
        # sixteen cpus placed and four booked has four left, so a
        # five-cpu booking does not fit although neither term alone
        # would refuse it.
        node = self._add_node(limit_cpus=16, used_cpus=8, held_cpus=4)
        claim = self._create_claim(limit_cpus=100, limit_memory_mb=1000000,
                                   limit_disk_gb=10000)

        refused = self._book(claim, [
            self._shape(node, cpus=5, memory_mb=1024, disk_gb=10)])
        self.assertFalse(refused['booked'])
        self.assertEqual('node_capacity', refused['refused_reason'])
        detail = {d['dimension']: d for d in refused['dimensions']}
        self.assertEqual(12.0, detail['cpus']['used'])

        # Four fits exactly.
        self.assertTrue(self._book(claim, [
            self._shape(node, cpus=4, memory_mb=1024, disk_gb=10)])['booked'])
        self.assertEqual(8, self._node_row(node).held_cpus)

    def test_two_shapes_on_one_node_cannot_both_take_the_last_of_it(self):
        # Each shape fits on its own; together they do not, and the
        # guard has to see the pair rather than each in turn.
        node = self._add_node(limit_cpus=10)
        claim = self._create_claim(limit_cpus=100, limit_memory_mb=1000000,
                                   limit_disk_gb=10000)

        result = self._book(claim, [
            self._shape(node, cpus=6, memory_mb=1024, disk_gb=10),
            self._shape(node, cpus=6, memory_mb=1024, disk_gb=10),
        ])

        self.assertFalse(result['booked'])
        self.assertEqual('node_capacity', result['refused_reason'])
        self.assertEqual(0, self._node_row(node).held_cpus)

    def test_a_node_with_no_capacity_row_is_refused(self):
        # Deliberately the opposite of what an instance admission does
        # with the same missing row: admitting an instance unguarded
        # records where a machine already is, while granting a booking
        # against a row nothing has sized promises room the cluster may
        # have no way to keep.
        claim = self._create_claim()

        result = self._book(claim, [self._shape(uuid4())])

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['booked'])
        self.assertEqual('node_capacity', result['refused_reason'])
        self.assertEqual([], result['dimensions'])
        self.assertEqual([], self._hold_rows(claim))

    def test_a_zero_sized_booking_reads_as_a_pass_not_a_denial(self):
        # The driver's rowcount semantics, which only a real server can
        # show: mysqldb sets CLIENT_FOUND_ROWS, so rowcount is rows
        # matched. A booking whose SET changes nothing -- a zero shape,
        # or a re-booking inside the same second, which leaves
        # updated_at where it was -- must read as "the guard passed".
        node = self._add_node()
        claim = self._create_claim()

        first = self._book(claim, [
            self._shape(node, cpus=0, memory_mb=0, disk_gb=0)])
        self.assertTrue(first['booked'], first['refused_reason'])
        second = self._book(claim, [
            self._shape(node, cpus=0, memory_mb=0, disk_gb=0)])
        self.assertTrue(second['booked'], second['refused_reason'])
        self.assertEqual(2, len(self._hold_rows(claim)))
        self.assertEqual(0, self._node_row(node).held_cpus)

    def test_an_expired_claim_cannot_book(self):
        # An expired claim's limits are not in claimed_*, so there is no
        # cluster-side aggregate for its bookings to sit inside.
        node = self._add_node()
        claim = self._create_claim()
        claims_t = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(claims_t).where(
                claims_t.c.uuid == UUID(claim)).values(
                    state=mariadb.CLAIM_STATE_EXPIRED))
            conn.commit()

        result = self._book(claim, [self._shape(node)])

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['booked'])
        self.assertEqual('not_active', result['refused_reason'])
        self.assertEqual([], self._hold_rows(claim))

    def test_a_claim_that_is_not_there_cannot_book(self):
        node = self._add_node()
        result = self._book(str(uuid4()), [self._shape(node)])

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['booked'])
        self.assertEqual('not_found', result['refused_reason'])
        self.assertEqual(0, self._node_row(node).held_cpus)

    # ------------------------------------------------------------------
    # Reading back
    # ------------------------------------------------------------------

    def test_the_listing_returns_what_was_booked_oldest_first(self):
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=1000)
        claim = self._create_claim(limit_cpus=100, limit_memory_mb=100000,
                                   limit_disk_gb=1000)
        # No wait between them: created_at carries microseconds, so two
        # bookings back to back are genuinely ordered rather than tied.
        first = self._book(claim, [
            self._shape(node, cpus=4, memory_mb=1024, disk_gb=10)])
        second = self._book(claim, [
            self._shape(node, cpus=2, memory_mb=1024, disk_gb=10)])

        listed = mariadb._direct_get_namespace_claim_holds(claim)

        self.assertEqual(
            [first['holds'][0]['uuid'], second['holds'][0]['uuid']],
            [hold['uuid'] for hold in listed])
        self.assertLess(listed[0]['created_at'], listed[1]['created_at'])
        self.assertEqual(str(node), listed[0]['node_uuid'])
        self.assertEqual('', listed[0]['instance_uuid'])

    def test_another_claims_holds_are_not_listed(self):
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=1000)
        mine = self._create_claim(namespace='ci-1')
        theirs = self._create_claim(namespace='ci-2')
        self.assertTrue(self._book(mine, [
            self._shape(node, cpus=1, memory_mb=1, disk_gb=1)])['booked'])

        self.assertEqual(
            [], mariadb._direct_get_namespace_claim_holds(theirs))
        self.assertEqual(
            1, len(mariadb._direct_get_namespace_claim_holds(mine)))

    def test_a_claim_with_no_holds_lists_nothing(self):
        claim = self._create_claim()
        self.assertEqual(
            [], mariadb._direct_get_namespace_claim_holds(claim))

    # ------------------------------------------------------------------
    # The claim create's copy of the invariant
    # ------------------------------------------------------------------

    def test_a_claim_create_asserts_the_invariant_it_cannot_fail(self):
        # The guard is on the create path so that both statements which
        # set a claim's limits carry it. A normally created claim has a
        # fresh uuid and so no holds, and COALESCE is what makes an
        # empty sum zero rather than NULL -- without it this create
        # would fail its own guard.
        self.assertIsNotNone(self._create_claim())

    def test_a_claim_created_over_existing_holds_fails_its_invariant(self):
        # Unreachable in production, and asserted here because that is
        # the only way to know the guard is live rather than inert.
        holds_t = mariadb._get_namespace_claim_holds_table()
        claim_uuid = uuid4()
        with self.engine.connect() as conn:
            self._insert(conn, holds_t, uuid=uuid4(), claim_uuid=claim_uuid,
                         namespace='ci-1', cpus=20, memory_mb=1024,
                         disk_gb=10, node_uuid=uuid4(), instance_uuid=None,
                         created_at=sa.func.now(), updated_at=sa.func.now())
            conn.commit()

        result = mariadb._direct_create_namespace_claim(
            str(claim_uuid), 'ci-1', 16, 16384, 200, 3600)

        self.assertFalse(result['success'])
        self.assertFalse(result['created'])
        self.assertEqual('', result['refused_reason'])
        self.assertIn('already carries holds', result['error'])
        # And the whole transaction rolled back, cluster row included.
        claims_t = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            self.assertEqual(
                0, conn.execute(sa.select(sa.func.count()).select_from(
                    claims_t)).scalar())
            cluster = conn.execute(sa.select(
                mariadb._get_cluster_capacity_table())).first()
        self.assertEqual(0, cluster.claimed_cpus)

    def test_a_claim_created_over_holds_it_covers_still_lands(self):
        # The boundary on the other side: limits that do cover the hold
        # rows are admitted, so the guard is an inequality rather than
        # "any hold at all refuses".
        holds_t = mariadb._get_namespace_claim_holds_table()
        claim_uuid = uuid4()
        with self.engine.connect() as conn:
            self._insert(conn, holds_t, uuid=uuid4(), claim_uuid=claim_uuid,
                         namespace='ci-1', cpus=16, memory_mb=1024,
                         disk_gb=10, node_uuid=uuid4(), instance_uuid=None,
                         created_at=sa.func.now(), updated_at=sa.func.now())
            conn.commit()

        result = mariadb._direct_create_namespace_claim(
            str(claim_uuid), 'ci-1', 16, 16384, 200, 3600)

        self.assertTrue(result['created'], result['error'])


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class HoldConcurrencyLiveTestCase(_LiveHoldFixture):
    """Adversarially concurrent bookings against a real MariaDB.

    The suite above proves each guard binds. This one proves the
    claim-side sum invariant binds *under contention*, which is the only
    property that distinguishes the subquery-inside-the-UPDATE form from
    a SELECT SUM(...) in front of it -- and which no mock can test,
    because the guarantee is entirely a property of InnoDB
    re-evaluating a guarded UPDATE's WHERE against the rows as committed
    by whoever held the lock first.

    It is also the only place an ER_CHECKREAD regression in the booking
    transaction is visible, so it reports the server regime it ran
    under. CI's live lane runs it against Debian 13's MariaDB, where
    innodb_snapshot_isolation is ON.
    """

    ENGINE_KWARGS = {'pool_size': RACE_THREADS + 8, 'max_overflow': 8,
                     'pool_timeout': 120}

    def setUp(self):
        super().setUp()
        self._prepare_database()
        self._set_cluster()
        self._report_server_regime()

    def _report_server_regime(self):
        with self.engine.connect() as conn:
            version = conn.execute(sa.text('SELECT VERSION()')).scalar()
            collation = conn.execute(sa.text(
                'SELECT @@collation_database')).scalar()
            try:
                snapshot = conn.execute(sa.text(
                    'SELECT @@innodb_snapshot_isolation')).scalar()
            except sa.exc.OperationalError:
                snapshot = 'absent (server predates it)'
        self._report('server-regime', (
            f'MariaDB {version}, collation {collation}, '
            f'innodb_snapshot_isolation {snapshot}'))

    def _run_concurrently(self, calls):
        """Fire every call at once; return the replies.

        The barrier is load bearing: without it thread startup staggers
        the calls enough that the first transaction commits before the
        last begins, and the race never happens.
        """
        barrier = threading.Barrier(len(calls))
        outcomes = [None] * len(calls)
        failures = []

        def _worker(index, call):
            try:
                barrier.wait(timeout=120)
                outcomes[index] = call()
            except BaseException as e:
                failures.append(f'{type(e).__name__}: {e}')

        threads = [threading.Thread(target=_worker, args=(index, call))
                   for index, call in enumerate(calls)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)

        self.assertEqual([], failures, 'a call raised instead of replying')
        self.assertNotIn(None, outcomes, 'a worker did not complete')
        return outcomes

    def test_concurrent_bookings_cannot_both_pass_the_sum_invariant(self):
        # Eight holders, each booking the claim's whole cpu allowance
        # onto a node with room for all eight of them. The node guard
        # therefore refuses nobody, so the claim-side sum is the only
        # thing that can, and exactly one booking may land.
        for round_number in range(RACE_ROUNDS):
            node = self._add_node(limit_cpus=1000, limit_memory_mb=1024000,
                                  limit_disk_gb=10000)
            claim = self._create_claim(
                namespace=f'ns-{round_number}', limit_cpus=8,
                limit_memory_mb=1024000, limit_disk_gb=10000)

            replies = self._run_concurrently([
                functools.partial(
                    self._book, claim,
                    [self._shape(node, cpus=8, memory_mb=1, disk_gb=1)])
                for _ in range(RACE_THREADS)])

            for reply in replies:
                self.assertTrue(reply['success'],
                                f'round {round_number}: {reply["error"]}')
            booked = [r for r in replies if r['booked']]
            self.assertEqual(
                1, len(booked),
                f'round {round_number}: {len(booked)} of {RACE_THREADS} '
                f'bookings took one claim\'s worth of room')
            for reply in replies:
                if reply['booked']:
                    continue
                self.assertEqual('claim_capacity', reply['refused_reason'],
                                 f'round {round_number}')

            rows = self._hold_rows(claim)
            self.assertEqual(1, len(rows), f'round {round_number}')
            self.assertEqual(8, sum(row.cpus for row in rows),
                             f'round {round_number}')
            self.assertEqual(8, self._node_row(node).held_cpus,
                             f'round {round_number}')

        self._report('hold-race-shape', (
            f'{RACE_ROUNDS} rounds x {RACE_THREADS} threads, 1 booked '
            f'and {RACE_THREADS - 1} refused per round'))

    def test_a_burst_of_bookings_fills_a_claim_exactly(self):
        # The same invariant at the boundary rather than all or nothing:
        # eight holders each asking for one cpu of a five cpu claim fill
        # it exactly and no further, whichever order they commit in.
        node = self._add_node(limit_cpus=1000, limit_memory_mb=1024000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=5, limit_memory_mb=1024000,
                                   limit_disk_gb=10000)

        replies = self._run_concurrently([
            functools.partial(
                self._book, claim,
                [self._shape(node, cpus=1, memory_mb=1, disk_gb=1)])
            for _ in range(RACE_THREADS)])

        for reply in replies:
            self.assertTrue(reply['success'], reply['error'])
        booked = [r for r in replies if r['booked']]
        self.assertEqual(5, len(booked))
        rows = self._hold_rows(claim)
        self.assertEqual(5, len(rows))
        self.assertEqual(5, sum(row.cpus for row in rows))
        self.assertEqual(5, self._node_row(node).held_cpus)
        self._report('hold-burst-shape', (
            f'{RACE_THREADS} concurrent one-cpu bookings of a five cpu '
            f'claim: {len(booked)} booked, sum exactly at the limit'))

    def test_concurrent_bookings_of_one_node_cannot_overfill_it(self):
        # The node side of the same question, with the claim deliberately
        # too large to be what refuses: eight holders in eight namespaces
        # each booking four of a node's sixteen cpus, so exactly four of
        # them fit.
        node = self._add_node(limit_cpus=16, limit_memory_mb=1024000,
                              limit_disk_gb=10000)
        claims = [self._create_claim(
            namespace=f'ns-{index}', limit_cpus=1000,
            limit_memory_mb=1024000, limit_disk_gb=10000)
            for index in range(RACE_THREADS)]

        replies = self._run_concurrently([
            functools.partial(
                self._book, claim,
                [self._shape(node, cpus=4, memory_mb=1, disk_gb=1)])
            for claim in claims])

        for reply in replies:
            self.assertTrue(reply['success'], reply['error'])
        booked = [r for r in replies if r['booked']]
        self.assertEqual(4, len(booked))
        for reply in replies:
            if not reply['booked']:
                self.assertEqual('node_capacity', reply['refused_reason'])
        row = self._node_row(node)
        self.assertEqual(16, row.held_cpus)
        self.assertLessEqual(row.used_cpus + row.held_cpus, row.limit_cpus)
        self.assertEqual(4, len(self._hold_rows()))
        self._report('hold-node-race-shape', (
            f'{RACE_THREADS} concurrent four-cpu bookings of a sixteen cpu '
            f'node: {len(booked)} booked, held exactly at the limit'))

    def test_concurrent_bookings_of_separate_claims_all_land(self):
        # The hold rows are written last and are the only place two
        # bookings of different claims meet, through the index on
        # claim_uuid. Whatever contention that creates has to resolve by
        # queueing or by whole-transaction retry rather than by failing,
        # so every one of these must land.
        node = self._add_node(limit_cpus=1000, limit_memory_mb=1024000,
                              limit_disk_gb=10000)
        claims = [self._create_claim(
            namespace=f'ns-{index}', limit_cpus=100,
            limit_memory_mb=1024000, limit_disk_gb=10000)
            for index in range(RACE_THREADS)]

        replies = self._run_concurrently([
            functools.partial(
                self._book, claim,
                [self._shape(node, cpus=2, memory_mb=1, disk_gb=1)])
            for claim in claims])

        for reply in replies:
            self.assertTrue(reply['success'], reply['error'])
            self.assertTrue(reply['booked'], reply['refused_reason'])
        self.assertEqual(RACE_THREADS, len(self._hold_rows()))
        self.assertEqual(2 * RACE_THREADS, self._node_row(node).held_cpus)
        self._report('hold-cross-claim-race', (
            f'{RACE_THREADS} concurrent bookings of separate claims: all '
            f'landed, node held equals the sum of the rows'))


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class HoldReleaseLiveTestCase(_LiveHoldFixture):
    """The three things that end a hold, against a real MariaDB.

    A hold ends when its claim is deleted, when the expiry sweep lapses
    the claim, or when a shrink leaves the claim's limits under the sum
    of its bookings. It deliberately does *not* end when an instance is
    deleted -- that is a later phase, and callers which delete and
    recreate under one claim depend on the booking surviving.

    What the mocked suite cannot say is whether the guards bind and
    whether the counters actually come back: a floored decrement, a
    DELETE scoped to one claim, and an expiry sweep that has to find the
    holds of a claim it lapsed in the same transaction are all
    statements whose behaviour is the database's rather than ours.
    """

    def setUp(self):
        super().setUp()
        self._prepare_database()
        self._set_cluster()

    # ------------------------------------------------------------------
    # Releasing on request
    # ------------------------------------------------------------------

    def test_a_named_release_credits_the_node_and_deletes_the_row(self):
        node = self._add_node()
        claim = self._create_claim()
        booked = self._book(claim, [
            self._shape(node, cpus=4, memory_mb=4096, disk_gb=40)])
        self.assertTrue(booked['booked'], booked['error'])

        result = self._release(claim, [booked['holds'][0]['uuid']])

        self.assertTrue(result['success'], result['error'])
        self.assertEqual([booked['holds'][0]['uuid']],
                         [hold['uuid'] for hold in result['released']])
        self.assertFalse(result['clamped'])
        self.assertEqual(0, self._node_row(node).held_cpus)
        self.assertEqual(0, self._node_row(node).held_memory_mb)
        self.assertEqual(0, self._node_row(node).held_disk_gb)
        self.assertEqual([], self._hold_rows(claim))

    def test_one_of_two_holds_leaves_the_other_booked(self):
        node = self._add_node()
        claim = self._create_claim()
        booked = self._book(claim, [
            self._shape(node, cpus=4, memory_mb=4096, disk_gb=40),
            self._shape(node, cpus=2, memory_mb=2048, disk_gb=20)])

        self._release(claim, [booked['holds'][1]['uuid']])

        self.assertEqual(4, self._node_row(node).held_cpus)
        self.assertEqual(4096, self._node_row(node).held_memory_mb)
        rows = self._hold_rows(claim)
        self.assertEqual(1, len(rows))
        self.assertEqual(booked['holds'][0]['uuid'], str(rows[0].uuid))

    def test_releasing_everything_takes_every_hold_on_two_nodes(self):
        node_a = self._add_node()
        node_b = self._add_node()
        claim = self._create_claim()
        self._book(claim, [
            self._shape(node_a, cpus=4, memory_mb=4096, disk_gb=40),
            self._shape(node_b, cpus=2, memory_mb=2048, disk_gb=20)])

        result = self._release(claim, all_holds=True)

        self.assertEqual(2, len(result['released']))
        self.assertEqual(0, self._node_row(node_a).held_cpus)
        self.assertEqual(0, self._node_row(node_b).held_cpus)
        self.assertEqual([], self._hold_rows(claim))

    def test_a_repeat_release_is_a_no_op_rather_than_a_second_credit(self):
        node = self._add_node(held_cpus=0)
        claim = self._create_claim()
        booked = self._book(claim, [self._shape(node, cpus=4)])
        hold = booked['holds'][0]['uuid']

        self.assertEqual(1, len(self._release(claim, [hold])['released']))
        again = self._release(claim, [hold])

        self.assertTrue(again['success'], again['error'])
        self.assertEqual([], again['released'])
        self.assertEqual(0, self._node_row(node).held_cpus)

    def test_another_claims_hold_cannot_be_released_through_this_one(self):
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=1000)
        mine = self._create_claim(namespace='ci-1')
        theirs = self._create_claim(namespace='ci-2')
        booked = self._book(theirs, [self._shape(node, cpus=4)])

        result = self._release(mine, [booked['holds'][0]['uuid']])

        self.assertTrue(result['success'], result['error'])
        self.assertEqual([], result['released'])
        self.assertEqual(1, len(self._hold_rows(theirs)))
        self.assertEqual(4, self._node_row(node).held_cpus)

    def test_an_empty_request_without_the_flag_is_refused(self):
        claim = self._create_claim()
        self.assertRaises(ValueError, self._release, claim)

    def test_a_release_cannot_drive_a_counter_negative(self):
        # The reconciler recomputes held_* from the hold rows, so a
        # release can find the counter already lower than the room it is
        # giving back. A negative counter would hand out capacity that
        # does not exist.
        node = self._add_node()
        claim = self._create_claim()
        booked = self._book(claim, [self._shape(node, cpus=4,
                                                memory_mb=4096, disk_gb=40)])
        capacity = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(capacity).where(
                capacity.c.node_uuid == node).values(
                    held_cpus=1, held_memory_mb=1024, held_disk_gb=10))
            conn.commit()

        result = self._release(claim, [booked['holds'][0]['uuid']])

        self.assertTrue(result['success'], result['error'])
        self.assertTrue(result['clamped'])
        self.assertEqual(str(node), result['clamps'][0]['counter'])
        self.assertEqual(3, result['clamps'][0]['shortfall_cpus'])
        row = self._node_row(node)
        self.assertEqual(0, row.held_cpus)
        self.assertEqual(0, row.held_memory_mb)
        self.assertEqual(0, row.held_disk_gb)

    def test_released_room_can_be_booked_again(self):
        # The point of the whole exercise: the claim and the node both
        # have the room back, not just the row gone.
        node = self._add_node(limit_cpus=8, limit_memory_mb=100000,
                              limit_disk_gb=1000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=100000,
                                   limit_disk_gb=1000)
        booked = self._book(claim, [self._shape(node, cpus=8, memory_mb=1,
                                                disk_gb=1)])
        refused = self._book(claim, [self._shape(node, cpus=8, memory_mb=1,
                                                 disk_gb=1)])
        self.assertFalse(refused['booked'])

        self._release(claim, [booked['holds'][0]['uuid']])

        again = self._book(claim, [self._shape(node, cpus=8, memory_mb=1,
                                               disk_gb=1)])
        self.assertTrue(again['booked'], again['refused_reason'])

    def test_a_displaced_hold_is_released_without_a_node_to_credit(self):
        node = self._add_node()
        claim = self._create_claim()
        booked = self._book(claim, [self._shape(node, cpus=4)])
        holds = mariadb._get_namespace_claim_holds_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(holds).where(
                holds.c.uuid == UUID(booked['holds'][0]['uuid'])).values(
                    node_uuid=None))
            conn.commit()

        result = self._release(claim, all_holds=True)

        self.assertEqual(1, len(result['released']))
        self.assertEqual('', result['released'][0]['node_uuid'])
        self.assertEqual([], self._hold_rows(claim))
        # The counter was not credited, because this hold was not
        # charged to that node any more. The reconciler owns the
        # discrepancy, and correcting it here would credit a node twice
        # for one booking.
        self.assertEqual(4, self._node_row(node).held_cpus)

    # ------------------------------------------------------------------
    # Releasing because the claim went away
    # ------------------------------------------------------------------

    def test_deleting_a_claim_releases_its_holds(self):
        node_a = self._add_node()
        node_b = self._add_node()
        claim = self._create_claim()
        self._book(claim, [self._shape(node_a, cpus=4, memory_mb=4096,
                                       disk_gb=40),
                           self._shape(node_b, cpus=2, memory_mb=2048,
                                       disk_gb=20)])

        result = mariadb._direct_delete_namespace_claim(claim)

        self.assertTrue(result['deleted'], result['error'])
        self.assertEqual(2, len(result['released_holds']))
        self.assertEqual(0, self._node_row(node_a).held_cpus)
        self.assertEqual(0, self._node_row(node_b).held_cpus)
        self.assertEqual([], self._hold_rows(claim))

    def test_deleting_a_claim_with_no_holds_still_works(self):
        claim = self._create_claim()
        result = mariadb._direct_delete_namespace_claim(claim)
        self.assertTrue(result['deleted'], result['error'])
        self.assertEqual([], result['released_holds'])

    def test_an_expired_claims_holds_go_when_the_claim_does(self):
        node = self._add_node()
        claim = self._create_claim()
        self._book(claim, [self._shape(node, cpus=4)])
        claims = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(claims).where(
                claims.c.uuid == UUID(claim)).values(state='expired'))
            conn.commit()

        result = mariadb._direct_delete_namespace_claim(claim)

        self.assertTrue(result['deleted'], result['error'])
        self.assertEqual(1, len(result['released_holds']))
        self.assertEqual(0, self._node_row(node).held_cpus)

    # ------------------------------------------------------------------
    # Releasing because the claim lapsed
    # ------------------------------------------------------------------

    def test_the_expiry_sweep_releases_a_lapsed_claims_holds(self):
        # An expired claim's limits are not in claimed_* -- the rebuild
        # in this same pass sums active claims only -- so there is no
        # aggregate left for a booking to sit inside and release is the
        # only honest outcome.
        node = self._add_node()
        self._make_schedulable(node)
        claim = self._create_claim()
        self._book(claim, [self._shape(node, cpus=4, memory_mb=4096,
                                       disk_gb=40)])
        self._lapse_claim(claim)

        result = self._reconcile()

        self.assertIsNotNone(result)
        self.assertEqual(1, result['claims_expired'])
        self.assertEqual([], self._hold_rows(claim))
        row = self._node_row(node)
        self.assertEqual(0, row.held_cpus)
        self.assertEqual(0, row.held_memory_mb)
        self.assertEqual(0, row.held_disk_gb)
        self.assertEqual('expired', self._claim_row(claim).state)

    def test_an_active_claims_holds_survive_a_pass(self):
        node = self._add_node()
        self._make_schedulable(node)
        claim = self._create_claim()
        self._book(claim, [self._shape(node, cpus=4)])

        self.assertIsNotNone(self._reconcile())

        self.assertEqual(1, len(self._hold_rows(claim)))
        self.assertEqual(4, self._node_row(node).held_cpus)

    def test_a_second_pass_finds_nothing_left_to_release(self):
        node = self._add_node()
        self._make_schedulable(node)
        claim = self._create_claim()
        self._book(claim, [self._shape(node, cpus=4)])
        self._lapse_claim(claim)

        self._reconcile()
        self.assertIsNotNone(self._reconcile())

        self.assertEqual([], self._hold_rows(claim))
        self.assertEqual(0, self._node_row(node).held_cpus)

    # ------------------------------------------------------------------
    # Releasing because the claim shrank
    # ------------------------------------------------------------------

    def test_a_shrink_releases_the_most_recently_booked_hold_first(self):
        # Two bookings back to back, microseconds apart, which the
        # DATETIME(6) created_at can tell apart. This is the case the
        # precision exists for: at whole seconds these two would tie and
        # the release order would be the uuid tie-break instead.
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=100000,
                                   limit_disk_gb=10000)
        first = self._book(claim, [self._shape(node, cpus=4, memory_mb=1,
                                               disk_gb=1)])
        second = self._book(claim, [self._shape(node, cpus=4, memory_mb=1,
                                                disk_gb=1)])
        self.assertTrue(second['booked'], second['refused_reason'])
        # Not a tie, and not ordered by uuid either: whichever way the
        # uuids fall, the later booking has the later timestamp.
        self.assertLess(first['holds'][0]['created_at'],
                        second['holds'][0]['created_at'])

        result = self._shrink(claim, cpus=4)

        self.assertTrue(result['updated'], result['refused_reason'])
        self.assertEqual([second['holds'][0]['uuid']],
                         [hold['uuid'] for hold in result['released_holds']])
        self.assertEqual([first['holds'][0]['uuid']],
                         [str(row.uuid) for row in self._hold_rows(claim)])
        self.assertEqual(4, self._node_row(node).held_cpus)
        self.assertEqual(4, self._claim_row(claim).limit_cpus)

    def test_the_hold_timestamps_really_carry_microseconds(self):
        # Both halves of the precision, against the server rather than
        # against rendered SQL: the column keeps a fraction, and the
        # statement writes one. A plain NOW() into a DATETIME(6) column
        # stores .000000, so a column type changed on its own would
        # leave this reading zero and the ordering rule quietly back at
        # second resolution.
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=100,
                                   limit_memory_mb=100000,
                                   limit_disk_gb=10000)
        self._book(claim, [self._shape(node, cpus=1, memory_mb=1,
                                       disk_gb=1)])

        [row] = self._hold_rows(claim)
        self.assertNotEqual(0, row.created_at.microsecond)
        self.assertNotEqual(0, row.updated_at.microsecond)

        table = mariadb._get_namespace_claim_holds_table()
        with self.engine.connect() as conn:
            for column in ('created_at', 'updated_at'):
                declared = conn.execute(sa.text(
                    'SELECT column_type FROM information_schema.columns '
                    'WHERE table_schema = DATABASE() '
                    'AND table_name = :t AND column_name = :c'),
                    {'t': table.name, 'c': column}).scalar()
                self.assertEqual('datetime(6)', declared, column)

    def test_a_shrink_stops_as_soon_as_the_claim_fits(self):
        # Four holds, half the limit to give back, and so exactly two
        # released. They are booked in one call and so share a
        # timestamp by construction, which makes which two go
        # unspecified -- correctly so: a shrink gives back the least it
        # can, and when bookings are simultaneous there is no "most
        # recent" among them to prefer.
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=100000,
                                   limit_disk_gb=10000)
        for _ in range(4):
            booked = self._book(claim, [self._shape(node, cpus=2,
                                                    memory_mb=1, disk_gb=1)])
            self.assertTrue(booked['booked'], booked['refused_reason'])

        result = self._shrink(claim, cpus=4)

        self.assertTrue(result['updated'], result['refused_reason'])
        self.assertEqual(2, len(result['released_holds']))
        rows = self._hold_rows(claim)
        self.assertEqual(2, len(rows))
        self.assertEqual(4, sum(row.cpus for row in rows))
        self.assertEqual(4, self._node_row(node).held_cpus)
        self.assertEqual(4, self._claim_row(claim).limit_cpus)

    def test_a_shrink_the_holds_fit_inside_changes_no_hold(self):
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=16, limit_memory_mb=100000,
                                   limit_disk_gb=10000)
        self._book(claim, [self._shape(node, cpus=4, memory_mb=1,
                                       disk_gb=1)])

        result = self._shrink(claim, cpus=8)

        self.assertTrue(result['updated'], result['refused_reason'])
        self.assertEqual([], result['released_holds'])
        self.assertEqual(1, len(self._hold_rows(claim)))
        self.assertEqual(4, self._node_row(node).held_cpus)

    def test_a_shrink_to_nothing_releases_every_hold(self):
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=100000,
                                   limit_disk_gb=10000)
        self._book(claim, [self._shape(node, cpus=4, memory_mb=1, disk_gb=1),
                           self._shape(node, cpus=4, memory_mb=1,
                                       disk_gb=1)])

        result = self._shrink(claim, cpus=0)

        self.assertTrue(result['updated'], result['refused_reason'])
        self.assertEqual(2, len(result['released_holds']))
        self.assertEqual([], self._hold_rows(claim))
        self.assertEqual(0, self._node_row(node).held_cpus)

    def test_the_shrink_floor_is_still_used_and_not_used_plus_held(self):
        # Raising the floor to used + held was rejected twice in the
        # design: it would make giving capacity back harder than taking
        # it, because an operator would have to find and release every
        # booking first. So a shrink below used_* is still refused, and
        # a shrink that only crosses the holds is not.
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=16, limit_memory_mb=100000,
                                   limit_disk_gb=10000)
        self._book(claim, [self._shape(node, cpus=8, memory_mb=1,
                                       disk_gb=1)])
        self._set_claim_used(claim, cpus=6)

        under_usage = self._shrink(claim, cpus=4)
        self.assertFalse(under_usage['updated'])
        self.assertEqual('below_usage', under_usage['refused_reason'])
        self.assertEqual([], under_usage['released_holds'])
        self.assertEqual(1, len(self._hold_rows(claim)))
        self.assertEqual(16, self._claim_row(claim).limit_cpus)

        over_usage = self._shrink(claim, cpus=6)
        self.assertTrue(over_usage['updated'], over_usage['refused_reason'])
        self.assertEqual(1, len(over_usage['released_holds']))
        self.assertEqual([], self._hold_rows(claim))
        self.assertEqual(0, self._node_row(node).held_cpus)

    def test_a_shrink_of_one_dimension_leaves_the_others_alone(self):
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=8192,
                                   limit_disk_gb=80)
        self._book(claim, [self._shape(node, cpus=2, memory_mb=8192,
                                       disk_gb=80)])

        result = self._shrink(claim, cpus=1)

        self.assertTrue(result['updated'], result['refused_reason'])
        self.assertEqual(1, len(result['released_holds']))
        row = self._claim_row(claim)
        self.assertEqual(1, row.limit_cpus)
        self.assertEqual(8192, row.limit_memory_mb)
        self.assertEqual(80, row.limit_disk_gb)

    def test_a_grow_releases_nothing(self):
        node = self._add_node(limit_cpus=100, limit_memory_mb=100000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=8, limit_memory_mb=100000,
                                   limit_disk_gb=10000)
        self._book(claim, [self._shape(node, cpus=8, memory_mb=1,
                                       disk_gb=1)])

        result = self._shrink(claim, cpus=16)

        self.assertTrue(result['updated'], result['refused_reason'])
        self.assertEqual([], result['released_holds'])
        self.assertEqual(1, len(self._hold_rows(claim)))


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class HoldReleaseConcurrencyLiveTestCase(_LiveHoldFixture):
    """A release and a shrink under contention, against a real MariaDB.

    The shrink is the case worth the machinery. Which holds it has to
    give back is decided from a read taken *before* its transaction
    opens -- the hold rows cannot be read inside it before its first
    guarded UPDATE -- so a booking that commits in between would leave
    the claim over its new limit with nothing to show for it. What stops
    that is a subquery in the claim UPDATE's own WHERE, asserting
    against the committed state that the holds which will remain fit
    inside the new limit. That is a property of InnoDB re-evaluating a
    guarded UPDATE's WHERE against the rows as committed by whoever held
    the lock first, and no mock can say anything about it.

    Like the booking suite, this reports the server regime it ran under:
    the ER_CHECKREAD invariant is only observably necessary against a
    server with innodb_snapshot_isolation ON, the default from MariaDB
    11.6.2.
    """

    ENGINE_KWARGS = {'pool_size': RACE_THREADS + 8, 'max_overflow': 8,
                     'pool_timeout': 120}

    def setUp(self):
        super().setUp()
        self._prepare_database()
        self._set_cluster()
        self._report_regime()

    def _report_regime(self):
        with self.engine.connect() as conn:
            version = conn.execute(sa.text('SELECT VERSION()')).scalar()
            try:
                snapshot = conn.execute(sa.text(
                    'SELECT @@innodb_snapshot_isolation')).scalar()
            except sa.exc.OperationalError:
                snapshot = 'absent (server predates it)'
        self._report('server-regime', (
            f'MariaDB {version}, innodb_snapshot_isolation {snapshot}'))

    def _run_concurrently(self, calls):
        """Fire every call at once; return the replies.

        The barrier is load bearing: without it thread startup staggers
        the calls enough that the first transaction commits before the
        last begins, and the race never happens.
        """
        barrier = threading.Barrier(len(calls))
        outcomes = [None] * len(calls)
        failures = []

        def _worker(index, call):
            try:
                barrier.wait(timeout=120)
                outcomes[index] = call()
            except BaseException as e:
                failures.append(f'{type(e).__name__}: {e}')

        threads = [threading.Thread(target=_worker, args=(index, call))
                   for index, call in enumerate(calls)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)

        self.assertEqual([], failures, 'a call raised instead of replying')
        self.assertNotIn(None, outcomes, 'a worker did not complete')
        return outcomes

    def test_a_booking_during_a_shrink_cannot_leave_the_claim_over_it(self):
        shapes = []
        for round_number in range(RACE_ROUNDS):
            node = self._add_node(limit_cpus=1000,
                                  limit_memory_mb=1024000,
                                  limit_disk_gb=10000)
            claim = self._create_claim(
                namespace=f'ns-{round_number}', limit_cpus=8,
                limit_memory_mb=1024000, limit_disk_gb=10000)
            booked = self._book(claim, [self._shape(node, cpus=4,
                                                    memory_mb=1, disk_gb=1)])
            self.assertTrue(booked['booked'], booked['refused_reason'])

            shrink, booking = self._run_concurrently([
                functools.partial(self._shrink, claim, cpus=4),
                functools.partial(
                    self._book, claim,
                    [self._shape(node, cpus=4, memory_mb=1, disk_gb=1)])])

            self.assertTrue(shrink['success'], shrink['error'])
            self.assertTrue(booking['success'], booking['error'])
            self.assertTrue(shrink['updated'],
                            f'round {round_number}: '
                            f'{shrink["refused_reason"]}')

            # Whichever order they committed in, the claim is at four
            # cpus and the holds inside it sum to no more than four.
            rows = self._hold_rows(claim)
            self.assertEqual(4, self._claim_row(claim).limit_cpus)
            self.assertLessEqual(
                sum(row.cpus for row in rows), 4,
                f'round {round_number}: the claim is over its new limit')
            # And the node's counter agrees with the rows, which is what
            # says the releases and the limit change were one
            # transaction rather than two.
            self.assertEqual(sum(row.cpus for row in rows),
                             self._node_row(node).held_cpus,
                             f'round {round_number}')
            shapes.append(
                f'{len(rows)} hold(s) left, booking '
                f'{"landed" if booking["booked"] else "refused"}')

        self._report('shrink-race-shape', '; '.join(shapes))

    def test_concurrent_releases_of_one_hold_credit_its_node_once(self):
        # Both callers read the row, so both believe they have four cpus
        # to give back. Only one may actually credit the node: the
        # other's DELETE matches nothing and its whole transaction is
        # rolled back and re-probed.
        for round_number in range(RACE_ROUNDS):
            node = self._add_node(limit_cpus=1000,
                                  limit_memory_mb=1024000,
                                  limit_disk_gb=10000)
            claim = self._create_claim(
                namespace=f'ns-{round_number}', limit_cpus=8,
                limit_memory_mb=1024000, limit_disk_gb=10000)
            booked = self._book(claim, [self._shape(node, cpus=4,
                                                    memory_mb=1, disk_gb=1)])
            hold = booked['holds'][0]['uuid']

            replies = self._run_concurrently([
                functools.partial(self._release, claim, [hold])
                for _ in range(RACE_THREADS)])

            for reply in replies:
                self.assertTrue(reply['success'],
                                f'round {round_number}: {reply["error"]}')
            released = [r for r in replies if r['released']]
            self.assertEqual(1, len(released), f'round {round_number}')
            self.assertEqual([], self._hold_rows(claim))
            self.assertEqual(0, self._node_row(node).held_cpus,
                             f'round {round_number}')

        self._report('release-race-shape', (
            f'{RACE_ROUNDS} rounds x {RACE_THREADS} threads releasing one '
            f'hold: exactly one credited the node per round'))

    def test_a_release_during_a_booking_leaves_the_counters_consistent(self):
        # The two halves of the same ledger running at once: a release
        # of one hold and a booking of another, on one node, repeatedly.
        # held_* must always equal the sum of the rows afterwards.
        node = self._add_node(limit_cpus=1000, limit_memory_mb=1024000,
                              limit_disk_gb=10000)
        claim = self._create_claim(limit_cpus=1000,
                                   limit_memory_mb=1024000,
                                   limit_disk_gb=10000)
        for _ in range(RACE_ROUNDS):
            seeded = self._book(claim, [
                self._shape(node, cpus=4, memory_mb=1, disk_gb=1)
                for _ in range(RACE_THREADS)])
            self.assertTrue(seeded['booked'], seeded['refused_reason'])

            calls = []
            for hold in seeded['holds']:
                calls.append(functools.partial(
                    self._release, claim, [hold['uuid']]))
                calls.append(functools.partial(
                    self._book, claim,
                    [self._shape(node, cpus=1, memory_mb=1, disk_gb=1)]))
            self._run_concurrently(calls)

            rows = self._hold_rows(claim)
            self.assertEqual(sum(row.cpus for row in rows),
                             self._node_row(node).held_cpus)
