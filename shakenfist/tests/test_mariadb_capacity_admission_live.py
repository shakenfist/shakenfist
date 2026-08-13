# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the placement admission primitive (phase 3).

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module behind one MariaDB install;
developers can point at a local instance). The unit tests all run
against a mocked connection and assert on compiled statement text, which
cannot exercise the parts most likely to break:

* the guards actually binding -- that ``used + x <= limit`` in an UPDATE
  WHERE really does refuse the request that would overflow and admit the
  one that exactly fits,
* the driver's rowcount semantics. SQLAlchemy's mysqldb dialect sets
  CLIENT_FOUND_ROWS, so rowcount is rows *matched*. If that ever changed
  a guarded UPDATE whose SET happened to be a no-op would read as a
  denial, and admission would fail intermittently for reasons no unit
  test could see,
* the uuid forms landing on the right keys: scheduler_node_capacity,
  node_metrics and instance_attributes key on sa.Uuid (undashed CHAR(32)
  on MariaDB) while object_references stores the dashed 36 character
  form (CLAUDE.md pitfall 6, where the failure mode is silently matching
  nothing rather than an error),
* the enum storage conventions in object_references, which this suite
  runs under utf8mb4_bin to make case-sensitive,
* GREATEST() clamping and the demand subselect against real NULLs.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database (and this one flips its
collation to utf8mb4_bin for the duration of each test), so they must be
run serially -- ``stestr run --serial``, as CI does.
"""

import json
import os
import time
import unittest
from unittest import mock
from uuid import uuid4

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

TEST_TABLES = [
    'scheduler_node_capacity',
    'namespace_claims',
    'cluster_capacity',
    'node_metrics',
    'object_references',
    'object_states',
    'instance_attributes',
    'instances',
    'nodes',
    'schema_versions',
]

TARGET_LOAD = 0.75


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class PlacementAdmissionLiveTestCase(base.ShakenFistTestCase):
    """Run real admissions and releases against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        # Run this suite against a case-sensitive collation, for the same
        # reason the reconciler's live suite does: object_references
        # stores enum member *values* while object_states stores member
        # *names*, and the two spellings differ only in case, so a
        # binding naming the wrong convention is papered over by the
        # default collations and returns zero rows under a _bin one.
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
                       mariadb._ensure_instance_attributes_schema,
                       mariadb._ensure_node_metrics_schema,
                       mariadb._ensure_scheduler_node_capacity_schema,
                       mariadb._ensure_namespace_claims_schema,
                       mariadb._ensure_cluster_capacity_schema):
            ensure(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.now = time.time()
        self._seed()

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
        """Fill NOT NULL columns this suite does not care about."""
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

    def _seed(self):
        # node_a  a capacity row with room for exactly one more of the
        #         instances this suite places
        # node_b  a second capacity row, the move target
        # node_c  an active hypervisor with metrics but no capacity row
        #         (the P7 mid-upgrade case)
        self.node_a, self.node_b, self.node_c = (uuid4() for _ in range(3))
        self.instance = uuid4()
        self.other_instance = uuid4()

        capacity_t = mariadb._get_scheduler_node_capacity_table()
        cluster_t = mariadb._get_cluster_capacity_table()
        metrics_t = mariadb._get_node_metrics_table()
        instances_t = mariadb._get_instances_table()
        attributes_t = mariadb._get_instance_attributes_table()

        with self.engine.connect() as conn:
            self._insert(conn, capacity_t, node_uuid=self.node_a,
                         limit_cpus=12, limit_memory_mb=12288,
                         limit_disk_gb=120, used_cpus=8,
                         used_memory_mb=8192, used_disk_gb=80,
                         expected_demand=0.0, updated_at=sa.func.now())
            self._insert(conn, capacity_t, node_uuid=self.node_b,
                         limit_cpus=64, limit_memory_mb=65536,
                         limit_disk_gb=1000, used_cpus=0,
                         used_memory_mb=0, used_disk_gb=0,
                         expected_demand=0.0, updated_at=sa.func.now())
            self._insert(conn, cluster_t, id=1, total_cpus=76,
                         total_memory_mb=77824, total_disk_gb=1120,
                         claimed_cpus=0, claimed_memory_mb=0,
                         claimed_disk_gb=0, unclaimed_used_cpus=8,
                         unclaimed_used_memory_mb=8192,
                         unclaimed_used_disk_gb=80,
                         updated_at=sa.func.now())

            # Generous measured metrics, so the D13 demand clause is not
            # what refuses anything unless a test makes it so.
            for node in (self.node_a, self.node_b, self.node_c):
                self._insert(conn, metrics_t, node_uuid=node,
                             cpu_schedulable=64, cpu_load_1=0.5,
                             metrics_json={}, is_hypervisor=True,
                             timestamp=self.now, fqdn=f'n-{str(node)[:8]}')

            for instance in (self.instance, self.other_instance):
                self._insert(conn, instances_t, uuid=instance, cpus=4,
                             memory=4096, disk_spec=json.dumps(
                                 [{'size': 40}]),
                             namespace='ci-1', name=str(instance)[:8])
                self._insert(conn, attributes_t, uuid=instance,
                             placement=json.dumps(None))
            conn.commit()

    def _admit(self, node, instance=None, old_node='', enforce=True,
               cpus=4, memory_mb=4096, disk_gb=40, demand_add=10.0,
               namespace='ci-1', target_load=TARGET_LOAD):
        instance = instance or self.instance
        return mariadb._direct_admit_instance_placement(
            str(instance), namespace, str(node), old_node, cpus, memory_mb,
            disk_gb, demand_add, target_load, enforce,
            json.dumps({'node': str(node), 'placement_attempts': 1}))

    def _release(self, instance=None, node='', cpus=4, memory_mb=4096,
                 disk_gb=40, namespace='ci-1'):
        instance = instance or self.instance
        return mariadb._direct_release_instance_placement(
            str(instance), namespace, node, cpus, memory_mb, disk_gb)

    def _capacity(self, node):
        table = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table).where(
                table.c.node_uuid == node)).first()

    def _cluster(self):
        table = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table)).first()

    def _references(self, instance=None):
        instance = instance or self.instance
        table = mariadb._get_object_references_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table.c.source_uuid).where(sa.and_(
                table.c.relationship == str(
                    RelationshipType.INSTANCE_LOCATION),
                table.c.target_object_type == str(ObjectType.INSTANCE),
                table.c.target_uuid == str(instance)))).fetchall()

    def _placement(self, instance=None):
        instance = instance or self.instance
        table = mariadb._get_instance_attributes_table()
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(table.c.placement).where(
                table.c.uuid == instance)).first()
        value = row.placement
        return json.loads(value) if isinstance(value, str) else value

    def _place_reference(self, node, instance=None, created=None):
        instance = instance or self.instance
        table = mariadb._get_object_references_table()
        with self.engine.connect() as conn:
            self._insert(
                conn, table,
                source_object_type=str(ObjectType.NODE),
                source_uuid=str(node),
                relationship=str(RelationshipType.INSTANCE_LOCATION),
                target_object_type=str(ObjectType.INSTANCE),
                target_uuid=str(instance),
                created=created or self.now, last_active=self.now)
            conn.commit()

    def _add_claim(self, namespace='ci-1', limit_cpus=8,
                   limit_memory_mb=8192, limit_disk_gb=80, used_cpus=4,
                   used_memory_mb=4096, used_disk_gb=40, hours=4):
        claim_uuid = uuid4()
        table = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            self._insert(
                conn, table, uuid=claim_uuid, namespace=namespace,
                limit_cpus=limit_cpus, limit_memory_mb=limit_memory_mb,
                limit_disk_gb=limit_disk_gb, used_cpus=used_cpus,
                used_memory_mb=used_memory_mb, used_disk_gb=used_disk_gb,
                state='active',
                expires_at=sa.text(f'NOW() + INTERVAL {hours} HOUR'),
                updated_at=sa.func.now())
            conn.commit()
        return claim_uuid

    def _claim(self, claim_uuid):
        table = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table).where(
                table.c.uuid == claim_uuid)).first()

    # ------------------------------------------------------------------
    # Admission
    # ------------------------------------------------------------------

    def test_a_real_admission_lands_every_write(self):
        result = self._admit(self.node_a)

        self.assertTrue(result['success'], result['error'])
        self.assertTrue(result['admitted'])
        self.assertFalse(result['unguarded'])

        node = self._capacity(self.node_a)
        self.assertEqual(12, node.used_cpus)
        self.assertEqual(12288, node.used_memory_mb)
        self.assertEqual(120, node.used_disk_gb)
        self.assertEqual(10.0, node.expected_demand)

        cluster = self._cluster()
        self.assertEqual(12, cluster.unclaimed_used_cpus)
        self.assertEqual(12288, cluster.unclaimed_used_memory_mb)
        self.assertEqual(120, cluster.unclaimed_used_disk_gb)

        # The post-admit counters come from a PK SELECT inside the same
        # transaction, so they must agree with what landed.
        self.assertEqual(12, result['node_used_cpus'])
        self.assertEqual(10.0, result['node_expected_demand'])

        # The dashed reference row landed and the undashed attributes
        # key found its row.
        self.assertEqual([str(self.node_a)],
                         [r.source_uuid for r in self._references()])
        self.assertEqual({'node': str(self.node_a),
                          'placement_attempts': 1}, self._placement())

    def test_a_dimension_that_would_overflow_is_denied(self):
        # node_a has exactly 4 cpus of headroom, so 8 does not fit.
        result = self._admit(self.node_a, cpus=8, memory_mb=4096,
                             disk_gb=40)

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['admitted'])
        self.assertEqual('node', result['failing_stage'])

        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertEqual(12.0, detail['cpus']['limit'])
        self.assertEqual(8.0, detail['cpus']['used'])
        self.assertEqual(8.0, detail['cpus']['requested'])
        # Memory and disk fit; only the binding dimension is flagged.
        self.assertFalse(detail['memory_mb']['exceeded'])
        self.assertFalse(detail['disk_gb']['exceeded'])

        # And the whole transaction rolled back.
        self.assertEqual(8, self._capacity(self.node_a).used_cpus)
        self.assertEqual(8, self._cluster().unclaimed_used_cpus)
        self.assertEqual([], self._references())
        self.assertIsNone(self._placement())

    def test_a_request_that_exactly_fits_is_admitted(self):
        # The boundary the guard is written against: used + x <= limit.
        result = self._admit(self.node_a, cpus=4, memory_mb=4096,
                             disk_gb=40)
        self.assertTrue(result['admitted'])
        self.assertEqual(12, self._capacity(self.node_a).used_cpus)

    def test_memory_and_disk_bind_as_well_as_cpu(self):
        # RAM and disk gain allocation-denominated protection for the
        # first time in this phase, so prove each one refuses alone.
        for dimension, over in (('memory_mb', 8192), ('disk_gb', 80)):
            request = {'cpus': 0, 'memory_mb': 0, 'disk_gb': 0}
            request[dimension] = over
            result = self._admit(self.node_a, **request)
            self.assertFalse(result['admitted'], f'{dimension} did not bind')
            detail = {d['dimension']: d for d in result['dimensions']}
            self.assertTrue(detail[dimension]['exceeded'])

    def test_the_demand_clause_refuses_on_measured_load(self):
        # cpu_schedulable 64 x target load 0.75 = 48. Publish a load
        # that leaves less headroom than the placement's demand.
        metrics_t = mariadb._get_node_metrics_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(metrics_t).where(
                metrics_t.c.node_uuid == self.node_b).values(cpu_load_1=45.0))
            conn.commit()

        result = self._admit(self.node_b, demand_add=10.0)
        self.assertFalse(result['admitted'])
        self.assertEqual('node', result['failing_stage'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(48.0, detail['demand']['limit'])
        self.assertEqual(45.0, detail['demand']['used'])
        self.assertTrue(detail['demand']['exceeded'])
        # No allocation dimension is at fault, which is exactly the case
        # a bare "denied" reply could not explain.
        self.assertFalse(detail['cpus']['exceeded'])

    def test_the_demand_clause_passes_on_null_metrics(self):
        # A node whose resources daemon has not published typed columns
        # has no bound to compute; fail closed would deny every create
        # mid-upgrade.
        metrics_t = mariadb._get_node_metrics_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(metrics_t).where(
                metrics_t.c.node_uuid == self.node_b).values(
                    cpu_schedulable=None, cpu_load_1=None))
            conn.commit()

        result = self._admit(self.node_b, demand_add=1000.0)
        self.assertTrue(result['admitted'], result['error'])

    def test_the_demand_clause_passes_with_no_metrics_row_at_all(self):
        metrics_t = mariadb._get_node_metrics_table()
        with self.engine.connect() as conn:
            conn.execute(sa.delete(metrics_t).where(
                metrics_t.c.node_uuid == self.node_b))
            conn.commit()

        self.assertTrue(self._admit(self.node_b, demand_add=1000.0)[
            'admitted'])

    def test_enforce_false_admits_over_the_limit_and_still_counts(self):
        # P5: the cleaner and the startup-task reconciliation record
        # where a libvirt domain already is, which a guard cannot refuse
        # -- but refusing to record it would leave the ledger wrong.
        result = self._admit(self.node_a, cpus=100, memory_mb=100000,
                             disk_gb=1000, enforce=False)

        self.assertTrue(result['admitted'], result['error'])
        node = self._capacity(self.node_a)
        self.assertEqual(108, node.used_cpus)
        self.assertGreater(node.used_cpus, node.limit_cpus)
        self.assertEqual(108, self._cluster().unclaimed_used_cpus)
        self.assertEqual([str(self.node_a)],
                         [r.source_uuid for r in self._references()])

    def test_a_node_with_no_capacity_row_admits_unguarded(self):
        # P7: mid-upgrade, fail open loudly rather than refusing every
        # create until the reconciler's next pass.
        result = self._admit(self.node_c, cpus=1000)

        self.assertTrue(result['admitted'], result['error'])
        self.assertTrue(result['unguarded'])
        self.assertEqual(0, result['node_used_cpus'])
        self.assertIsNone(self._capacity(self.node_c))
        # The placement still happened.
        self.assertEqual([str(self.node_c)],
                         [r.source_uuid for r in self._references()])
        # And the cluster row's guards came off with the node's, because
        # the totals do not include a node with no capacity row.
        self.assertEqual(1008, self._cluster().unclaimed_used_cpus)

    def test_a_cluster_with_no_singleton_admits_unguarded(self):
        cluster_t = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.delete(cluster_t))
            conn.commit()

        result = self._admit(self.node_a)
        self.assertTrue(result['admitted'], result['error'])
        self.assertTrue(result['unguarded'])
        # The node guard still applied, because that row exists.
        self.assertEqual(12, self._capacity(self.node_a).used_cpus)

    def test_the_cluster_guard_refuses_when_the_node_would_not(self):
        # node_b has plenty of room, but the cluster does not.
        cluster_t = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(cluster_t).where(
                cluster_t.c.id == 1).values(unclaimed_used_cpus=74))
            conn.commit()

        result = self._admit(self.node_b, cpus=4)
        self.assertFalse(result['admitted'])
        self.assertEqual('cluster', result['failing_stage'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(76.0, detail['cpus']['limit'])
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertEqual(0, self._capacity(self.node_b).used_cpus)

    def test_the_cluster_limit_is_total_less_claimed(self):
        # D14: what active claims have not spoken for is what an
        # unclaimed namespace may use.
        cluster_t = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(cluster_t).where(
                cluster_t.c.id == 1).values(claimed_cpus=68))
            conn.commit()

        result = self._admit(self.node_b, cpus=4)
        self.assertFalse(result['admitted'])
        self.assertEqual('cluster', result['failing_stage'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(8.0, detail['cpus']['limit'])

    def test_zero_sized_admission_is_not_read_as_a_denial(self):
        # SQLAlchemy's mysqldb dialect sets CLIENT_FOUND_ROWS, so
        # rowcount is rows matched rather than rows changed. Without it a
        # guarded UPDATE whose SET is a no-op reads as rowcount 0 --
        # a denial for a request that fits trivially.
        result = self._admit(self.node_a, cpus=0, memory_mb=0, disk_gb=0,
                             demand_add=0.0)
        self.assertTrue(result['admitted'], result['error'])
        self.assertEqual(8, self._capacity(self.node_a).used_cpus)

    # ------------------------------------------------------------------
    # Claims (dormant in production until phase 4)
    # ------------------------------------------------------------------

    def test_an_active_claim_is_drawn_down_instead_of_the_cluster(self):
        claim_uuid = self._add_claim()
        result = self._admit(self.node_b)

        self.assertTrue(result['admitted'], result['error'])
        claim = self._claim(claim_uuid)
        self.assertEqual(8, claim.used_cpus)
        self.assertEqual(8192, claim.used_memory_mb)
        self.assertEqual(80, claim.used_disk_gb)
        # The claim was accounted against the cluster row when it was
        # created, so drawing it down again would double count.
        self.assertEqual(8, self._cluster().unclaimed_used_cpus)

    def test_a_claim_that_would_overflow_denies(self):
        self._add_claim(limit_cpus=8, used_cpus=6)
        result = self._admit(self.node_b, cpus=4)

        self.assertFalse(result['admitted'])
        self.assertEqual('claim', result['failing_stage'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(8.0, detail['cpus']['limit'])
        self.assertEqual(6.0, detail['cpus']['used'])
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertEqual(0, self._capacity(self.node_b).used_cpus)

    def test_an_expired_claim_falls_back_to_the_cluster_branch(self):
        # expires_at is compared server side, so a claim that lapsed
        # before the reconciler swept it must not still gate admission.
        self._add_claim(hours=-4)
        result = self._admit(self.node_b)

        self.assertTrue(result['admitted'], result['error'])
        self.assertEqual(12, self._cluster().unclaimed_used_cpus)

    # ------------------------------------------------------------------
    # Moves
    # ------------------------------------------------------------------

    def test_a_move_decrements_the_old_node_and_deletes_duplicates(self):
        self._admit(self.node_a)
        # Simulate the duplicate placement row that the old non-atomic
        # triple could leave behind: a survivor of a best-effort removal.
        self._place_reference(self.node_c)
        self.assertEqual(2, len(self._references()))

        result = self._admit(self.node_b, old_node=str(self.node_a))
        self.assertTrue(result['admitted'], result['error'])
        self.assertFalse(result['clamped'])

        self.assertEqual(8, self._capacity(self.node_a).used_cpus)
        self.assertEqual(4, self._capacity(self.node_b).used_cpus)
        # Exactly one reference row survives, on the new node: this is
        # what stops the reconciler's ledger double charging.
        self.assertEqual([str(self.node_b)],
                         [r.source_uuid for r in self._references()])
        # A move never changes namespace, so the namespace side of the
        # ledger is untouched: the instance's allocation has been
        # counted there since its first placement, and incrementing
        # again would inflate the namespace by one instance per move.
        self.assertEqual(12, self._cluster().unclaimed_used_cpus)

    def test_a_move_does_not_credit_expected_demand_back(self):
        self._admit(self.node_a)
        self.assertEqual(10.0, self._capacity(self.node_a).expected_demand)
        self._admit(self.node_b, old_node=str(self.node_a))
        # The D13 term decays with instance age and is recomputed by the
        # reconciler, so the old node keeps its (decaying) contribution.
        self.assertEqual(10.0, self._capacity(self.node_a).expected_demand)
        self.assertEqual(10.0, self._capacity(self.node_b).expected_demand)

    def test_a_move_decrement_clamps_rather_than_going_negative(self):
        # A release racing a reconcile pass: the counters are already
        # lower than this move believes.
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(capacity_t).where(
                capacity_t.c.node_uuid == self.node_a).values(
                    used_cpus=1, used_memory_mb=1, used_disk_gb=1))
            conn.commit()

        result = self._admit(self.node_b, old_node=str(self.node_a))
        self.assertTrue(result['admitted'], result['error'])
        self.assertTrue(result['clamped'])
        node_a = self._capacity(self.node_a)
        self.assertEqual(0, node_a.used_cpus)
        self.assertEqual(0, node_a.used_memory_mb)
        self.assertEqual(0, node_a.used_disk_gb)

    def test_a_denied_move_leaves_the_old_node_alone(self):
        self._admit(self.node_a)
        result = self._admit(self.node_b, old_node=str(self.node_a),
                             cpus=1000)

        self.assertFalse(result['admitted'])
        # The old node's decrement rolled back with everything else,
        # whichever order the two rows were touched in.
        self.assertEqual(12, self._capacity(self.node_a).used_cpus)
        self.assertEqual([str(self.node_a)],
                         [r.source_uuid for r in self._references()])

    def test_re_placing_onto_the_same_node_is_not_a_move(self):
        self._admit(self.node_b)
        result = self._admit(self.node_b, old_node=str(self.node_b))
        self.assertTrue(result['admitted'], result['error'])
        # Charged twice (this is a second placement of the same
        # instance, which a caller should not do), but never decremented
        # and re-incremented with a clamp risk in the middle.
        self.assertFalse(result['clamped'])
        self.assertEqual(8, self._capacity(self.node_b).used_cpus)

    def test_a_missing_attributes_row_aborts_the_transaction(self):
        attributes_t = mariadb._get_instance_attributes_table()
        with self.engine.connect() as conn:
            conn.execute(sa.delete(attributes_t).where(
                attributes_t.c.uuid == self.instance))
            conn.commit()

        result = self._admit(self.node_a)
        self.assertFalse(result['success'])
        self.assertFalse(result['admitted'])
        # Nothing was drawn down.
        self.assertEqual(8, self._capacity(self.node_a).used_cpus)
        self.assertEqual(8, self._cluster().unclaimed_used_cpus)
        self.assertEqual([], self._references())

    # ------------------------------------------------------------------
    # Release
    # ------------------------------------------------------------------

    def test_release_gives_the_capacity_back(self):
        self._admit(self.node_a)
        result = self._release()

        self.assertTrue(result['success'], result['error'])
        self.assertTrue(result['released'])
        self.assertFalse(result['clamped'])
        self.assertEqual(8, self._capacity(self.node_a).used_cpus)
        self.assertEqual(8, self._cluster().unclaimed_used_cpus)
        self.assertEqual([], self._references())
        # P8: the placement attribute is deliberately not cleared.
        self.assertEqual({'node': str(self.node_a),
                          'placement_attempts': 1}, self._placement())

    def test_double_release_is_harmless(self):
        # hard_delete()'s sweep runs behind _delete_globally()'s release.
        self._admit(self.node_a)
        self.assertTrue(self._release()['released'])

        second = self._release()
        self.assertTrue(second['success'], second['error'])
        self.assertFalse(second['released'])
        # And nothing moved: a second decrement would take capacity from
        # an instance that no longer holds any.
        self.assertEqual(8, self._capacity(self.node_a).used_cpus)
        self.assertEqual(8, self._cluster().unclaimed_used_cpus)

    def test_release_clamps_rather_than_going_negative(self):
        self._place_reference(self.node_b)
        result = self._release()
        self.assertTrue(result['released'])
        self.assertTrue(result['clamped'])
        node_b = self._capacity(self.node_b)
        self.assertEqual(0, node_b.used_cpus)
        self.assertEqual(0, node_b.used_memory_mb)

    def test_release_of_a_claimed_namespace_credits_the_claim(self):
        claim_uuid = self._add_claim()
        self._admit(self.node_b)
        self.assertTrue(self._release()['released'])

        claim = self._claim(claim_uuid)
        self.assertEqual(4, claim.used_cpus)
        self.assertEqual(4096, claim.used_memory_mb)
        # The cluster row was never charged for a claimed namespace.
        self.assertEqual(8, self._cluster().unclaimed_used_cpus)

    def test_release_of_a_named_node_needs_no_references(self):
        self._admit(self.node_a)
        table = mariadb._get_object_references_table()
        with self.engine.connect() as conn:
            conn.execute(sa.delete(table))
            conn.commit()

        result = self._release(node=str(self.node_a))
        self.assertTrue(result['released'])
        self.assertEqual(8, self._capacity(self.node_a).used_cpus)

    def test_release_follows_duplicate_references_to_every_node(self):
        self._place_reference(self.node_a)
        self._place_reference(self.node_b)
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(capacity_t).where(
                capacity_t.c.node_uuid == self.node_b).values(
                    used_cpus=4, used_memory_mb=4096, used_disk_gb=40))
            conn.commit()

        result = self._release()
        self.assertTrue(result['released'])
        self.assertEqual(4, self._capacity(self.node_a).used_cpus)
        self.assertEqual(0, self._capacity(self.node_b).used_cpus)
        # The namespace side is charged once per instance however many
        # nodes hold a reference for it.
        self.assertEqual(4, self._cluster().unclaimed_used_cpus)
        self.assertEqual([], self._references())

    def test_release_only_touches_this_instance(self):
        self._admit(self.node_a)
        self._admit(self.node_b, instance=self.other_instance)
        self._release()

        self.assertEqual([str(self.node_b)],
                         [r.source_uuid for r in
                          self._references(self.other_instance)])
        self.assertEqual(4, self._capacity(self.node_b).used_cpus)

    def test_admit_release_cycling_returns_to_the_seeded_counters(self):
        # The reconciler recomputes from ground truth, so admission and
        # release have to agree with each other in between passes.
        for round_number in range(4):
            admitted = self._admit(self.node_a)
            self.assertTrue(admitted['admitted'],
                            f'round {round_number}: {admitted["error"]}')
            self.assertTrue(self._release()['released'])

        node = self._capacity(self.node_a)
        self.assertEqual(8, node.used_cpus)
        self.assertEqual(8192, node.used_memory_mb)
        self.assertEqual(80, node.used_disk_gb)
        cluster = self._cluster()
        self.assertEqual(8, cluster.unclaimed_used_cpus)
        self.assertEqual(80, cluster.unclaimed_used_disk_gb)
        # expected_demand only ever accumulates here; the reconciler
        # decays it (D13), and release deliberately does not credit it
        # back. A fifth round would be denied by the demand clause even
        # though every allocation dimension has room, which is the
        # feedforward term doing exactly its job.
        self.assertEqual(40.0, node.expected_demand)
        fifth = self._admit(self.node_a)
        self.assertFalse(fifth['admitted'])
        self.assertEqual('node', fifth['failing_stage'])
