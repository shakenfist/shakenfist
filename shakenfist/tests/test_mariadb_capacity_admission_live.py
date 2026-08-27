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

import functools
import json
import math
import os
import random
import statistics
import sys
import threading
import time
import unittest
from unittest import mock
from uuid import uuid4

import sqlalchemy as sa
from testtools import content

from shakenfist.constants import GiB
from shakenfist import exceptions
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

# The concurrency suite's shape. These are deliberately module level so
# a soak run can be widened from one place, and so the numbers reported
# in the plan's Validation section have a single source of truth.
RACE_THREADS = 8
RACE_ROUNDS = 20
BURST_THREADS = 50
SOAK_THREADS = 6
SOAK_OPERATIONS_PER_THREAD = 60
SOAK_NODES = 4
SOAK_INSTANCES = 12

# SCHEDULER_DEMAND_PER_VCPU's default, so the accumulated feedforward
# term can be asserted as admitted vCPUs x this constant.
DEMAND_PER_VCPU = 0.6


class _LiveCapacityFixture(base.ShakenFistTestCase):
    """Shared live-MariaDB scaffolding for both suites in this module.

    Both want the same disposable database, the same utf8mb4_bin
    collation and the same set of ensured tables; only the seed shape
    and the connection pool differ, so those are what a subclass
    supplies.
    """

    # SQLAlchemy pools five connections by default, which is right for
    # the single threaded suite and useless for the concurrency one: a
    # pool smaller than the thread count serialises the admissions at
    # the pool rather than at the row locks, so the race under test
    # never actually races.
    ENGINE_KWARGS: dict = {}

    def _prepare_database(self):
        self.engine = sa.create_engine(os.environ[DSN_ENV],
                                       **self.ENGINE_KWARGS)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        # Run these suites against a case-sensitive collation, for the
        # same reason the reconciler's live suite does: object_references
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

    def _capacity(self, node):
        table = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table).where(
                table.c.node_uuid == node)).first()

    def _cluster(self):
        table = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table)).first()

    def _references_for(self, instance):
        table = mariadb._get_object_references_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table.c.source_uuid).where(sa.and_(
                table.c.relationship == str(
                    RelationshipType.INSTANCE_LOCATION),
                table.c.target_object_type == str(ObjectType.INSTANCE),
                table.c.target_uuid == str(instance)))).fetchall()

    def _placement_of(self, instance):
        table = mariadb._get_instance_attributes_table()
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(table.c.placement).where(
                table.c.uuid == instance)).first()
        value = row.placement
        return json.loads(value) if isinstance(value, str) else value

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

    def _place_reference(self, node, instance, created=None):
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


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class PlacementAdmissionLiveTestCase(_LiveCapacityFixture):
    """Run real admissions and releases against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self._prepare_database()
        self.now = time.time()
        self._seed()

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

    def _references(self, instance=None):
        return self._references_for(instance or self.instance)

    def _placement(self, instance=None):
        return self._placement_of(instance or self.instance)

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
        # already past that budget: since phase 4a the clause tests the
        # node's existing state, so what refuses is the node being over
        # target, not the size of the placement asking.
        metrics_t = mariadb._get_node_metrics_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(metrics_t).where(
                metrics_t.c.node_uuid == self.node_b).values(cpu_load_1=50.0))
            conn.commit()

        result = self._admit(self.node_b, demand_add=10.0)
        self.assertFalse(result['admitted'])
        self.assertEqual('node', result['failing_stage'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(48.0, detail['demand']['limit'])
        self.assertEqual(50.0, detail['demand']['used'])
        self.assertTrue(detail['demand']['exceeded'])
        # used is two terms meaning different things, reported
        # separately so a correct refusal (real load) can be told from
        # an estimator defect (undecayed expected_demand) -- issue 3913.
        # Here the refusal is all measured load.
        self.assertEqual(50.0, detail['demand']['cpu_load_1'])
        self.assertEqual(0.0, detail['demand']['expected_demand'])
        # No allocation dimension is at fault, which is exactly the case
        # a bare "denied" reply could not explain -- and it is what
        # makes the denial waivable by the P9 re-walk.
        self.assertFalse(detail['cpus']['exceeded'])
        self.assertTrue(exceptions.CapacityAdmissionDenied(
            result['failing_stage'], result['dimensions']).demand_only)

    def test_the_demand_clause_admits_the_smallest_node(self):
        # Issue #3813, at the smallest node this project supports. One
        # schedulable thread gives a budget of 0.75, and the old clause
        # charged the placement against it, so an idle single-thread
        # node refused an 8-vCPU instance -- and every other instance
        # size too. Now only the node's own state is compared, so an
        # idle node admits whatever it has allocation room for.
        metrics_t = mariadb._get_node_metrics_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(metrics_t).where(
                metrics_t.c.node_uuid == self.node_b).values(
                    cpu_schedulable=1, cpu_load_1=0.0))
            conn.commit()

        result = self._admit(self.node_b, cpus=8, memory_mb=8192,
                             disk_gb=80, demand_add=8 * DEMAND_PER_VCPU)
        self.assertTrue(result['admitted'], result['error'])
        # And the charge still landed, so the next create spreads.
        self.assertEqual(8 * DEMAND_PER_VCPU,
                         self._capacity(self.node_b).expected_demand)

    def test_the_demand_clause_is_satisfiable_at_every_node_size(self):
        # Half of the property #3813 violated: an idle node admits, at
        # every node size this project supports. Evaluated as SQL
        # against the real server, because the clause is SQL and the
        # arithmetic that broke was the server's.
        #
        # There is no instance-size axis here, and that is the point:
        # since the fix, `_demand_guard_clause()` does not take the
        # placement's charge at all, so instance size cannot enter this
        # comparison. Re-adding it would have to change the helper's
        # signature, which the unit suite pins directly
        # (test_the_demand_clause_does_not_charge_the_placement). The
        # instance-size half of the property is behavioural and lives in
        # test_an_idle_small_node_admits_every_instance_size below,
        # which goes through a real admission rather than the clause
        # alone.
        metrics_t = mariadb._get_node_metrics_table()
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        refused = []

        for schedulable in range(1, 17):
            with self.engine.connect() as conn:
                conn.execute(sa.update(metrics_t).where(
                    metrics_t.c.node_uuid == self.node_b).values(
                        cpu_schedulable=schedulable, cpu_load_1=0.0))
                conn.execute(sa.update(capacity_t).where(
                    capacity_t.c.node_uuid == self.node_b).values(
                        expected_demand=0.0))
                conn.commit()

            clause = mariadb._demand_guard_clause(self.node_b, TARGET_LOAD)
            with self.engine.connect() as conn:
                admits = conn.execute(
                    sa.select(clause).select_from(capacity_t).where(
                        capacity_t.c.node_uuid == self.node_b)).scalar()
            if not admits:
                refused.append(schedulable)

        self.assertEqual(
            [], refused,
            f'idle nodes refused at cpu_schedulable values: {refused}')

    def test_an_idle_small_node_admits_every_instance_size(self):
        # The other half, and the one the clause-level sweep cannot
        # prove: a real admission, at the node size #3813 was found on
        # (two schedulable threads, a demand budget of 0.75 x 2 = 1.5),
        # for every instance size. Before the fix the charge was
        # compared against that budget, so everything from 1 vCPU up
        # was refused here; now nothing is, and each admission's charge
        # is released again so the sizes stay independent.
        metrics_t = mariadb._get_node_metrics_table()
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        refused = []

        for cpus in (1, 2, 4, 8, 16):
            with self.engine.connect() as conn:
                conn.execute(sa.update(metrics_t).where(
                    metrics_t.c.node_uuid == self.node_b).values(
                        cpu_schedulable=2, cpu_load_1=0.0))
                conn.execute(sa.update(capacity_t).where(
                    capacity_t.c.node_uuid == self.node_b).values(
                        expected_demand=0.0))
                conn.commit()

            result = self._admit(
                self.node_b, cpus=cpus, memory_mb=1024, disk_gb=10,
                demand_add=cpus * DEMAND_PER_VCPU)
            if not result['admitted']:
                refused.append((cpus, result['dimensions']))
            else:
                # The charge landed even though it was not compared.
                self.assertAlmostEqual(
                    cpus * DEMAND_PER_VCPU,
                    self._capacity(self.node_b).expected_demand)
                self._release(cpus=cpus, memory_mb=1024, disk_gb=10)

        self.assertEqual(
            [], refused,
            f'an idle two-thread node refused these instance sizes: '
            f'{refused}')

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
        # Exactly at the claim's limits, which is not over them.
        self.assertFalse(result['claim_over_limit'])
        self.assertEqual([], result['claim_dimensions'])

    def test_a_claim_that_would_overflow_is_admitted_and_reported(self):
        # D16: claim ceilings are advisory for one release. The
        # over-limit admission has to *land* -- counters above the limit
        # and all -- and say so, because phase 5 flips
        # CLAIM_ENFORCEMENT_HARD and turns this into the refusal.
        claim_uuid = self._add_claim(
            limit_cpus=8, used_cpus=6, limit_memory_mb=65536,
            used_memory_mb=0, limit_disk_gb=1000, used_disk_gb=0)
        result = self._admit(self.node_b, cpus=4)

        self.assertTrue(result['admitted'], result['error'])
        # Advisory, so nothing about this reads as a refusal.
        self.assertEqual('', result['failing_stage'])
        self.assertEqual([], result['dimensions'])

        self.assertTrue(result['claim_over_limit'])
        detail = {d['dimension']: d for d in result['claim_dimensions']}
        # Only the dimension actually over is named: memory and disk had
        # room and must not be reported as if they did not.
        self.assertEqual(['cpus'], list(detail))
        self.assertEqual(8.0, detail['cpus']['limit'])
        # used is what the claim held before this admission.
        self.assertEqual(6.0, detail['cpus']['used'])
        self.assertEqual(4.0, detail['cpus']['requested'])
        self.assertTrue(detail['cpus']['exceeded'])

        # And the row really is over its limit afterwards. This is the
        # assertion that separates "advisory mode works" from "advisory
        # mode does nothing", which look identical from the create.
        claim = self._claim(claim_uuid)
        self.assertEqual(10, claim.used_cpus)
        self.assertGreater(claim.used_cpus, claim.limit_cpus)
        # The node counters were still drawn down normally.
        self.assertEqual(4, self._capacity(self.node_b).used_cpus)

    def test_every_over_claim_dimension_is_reported(self):
        self._add_claim(limit_cpus=8, used_cpus=6, limit_memory_mb=4096,
                        used_memory_mb=4096, limit_disk_gb=1000,
                        used_disk_gb=0)
        result = self._admit(self.node_b, cpus=4, memory_mb=4096, disk_gb=40)

        self.assertTrue(result['admitted'], result['error'])
        self.assertTrue(result['claim_over_limit'])
        self.assertEqual(
            ['cpus', 'memory_mb'],
            [d['dimension'] for d in result['claim_dimensions']])

    def test_a_claim_with_room_reports_nothing(self):
        self._add_claim(limit_cpus=64, used_cpus=0, limit_memory_mb=65536,
                        used_memory_mb=0, limit_disk_gb=1000, used_disk_gb=0)
        result = self._admit(self.node_b)

        self.assertTrue(result['admitted'], result['error'])
        self.assertFalse(result['claim_over_limit'])
        self.assertEqual([], result['claim_dimensions'])

    def test_a_move_in_a_claimed_namespace_reports_nothing(self):
        # A move consumes nothing new on the namespace side (it never
        # changes namespace), so it must neither touch the claim
        # counters nor re-report a claim that some earlier admission
        # left over its limits.
        claim_uuid = self._add_claim(limit_cpus=8, used_cpus=6,
                                     limit_memory_mb=65536, used_memory_mb=0,
                                     limit_disk_gb=1000, used_disk_gb=0)
        self.assertTrue(self._admit(self.node_a)['claim_over_limit'])
        before = self._claim(claim_uuid)

        result = self._admit(self.node_b, old_node=str(self.node_a))
        self.assertTrue(result['admitted'], result['error'])
        self.assertFalse(result['claim_over_limit'])
        self.assertEqual([], result['claim_dimensions'])
        after = self._claim(claim_uuid)
        self.assertEqual(before.used_cpus, after.used_cpus)
        self.assertEqual(before.used_memory_mb, after.used_memory_mb)
        self.assertEqual(before.used_disk_gb, after.used_disk_gb)

    def test_an_unclaimed_namespace_reports_nothing(self):
        result = self._admit(self.node_b)
        self.assertTrue(result['admitted'], result['error'])
        self.assertFalse(result['claim_over_limit'])
        self.assertEqual([], result['claim_dimensions'])

    def test_the_claim_guard_binds_when_enforcement_is_turned_on(self):
        # Phase 5's flip, exercised against a real server so its guard is
        # known to bind before the constant moves. Also the live half of
        # D6: the node here has a capacity row, so this says nothing
        # about the fail-open -- the unit suite covers that half.
        self._add_claim(limit_cpus=8, used_cpus=6)
        with mock.patch.object(mariadb, 'CLAIM_ENFORCEMENT_HARD', True):
            result = self._admit(self.node_b, cpus=4)

        self.assertFalse(result['admitted'])
        self.assertEqual('claim', result['failing_stage'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertEqual(8.0, detail['cpus']['limit'])
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertFalse(result['claim_over_limit'])
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
        self._place_reference(self.node_c, self.instance)
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
        self._place_reference(self.node_b, self.instance)
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

    def test_release_of_a_named_node_is_reference_gated(self):
        # A named node filters the located references rather than
        # overriding them. With no reference rows the instance holds
        # nothing, so naming its old node must release nothing.
        self._admit(self.node_a)
        table = mariadb._get_object_references_table()
        with self.engine.connect() as conn:
            conn.execute(sa.delete(table))
            conn.commit()

        result = self._release(node=str(self.node_a))
        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['released'])
        self.assertFalse(result['clamped'])
        self.assertEqual(12, self._capacity(self.node_a).used_cpus)
        self.assertEqual(12, self._cluster().unclaimed_used_cpus)

    def test_release_of_a_node_not_holding_the_instance_is_a_no_op(self):
        # The instance is on node_a; releasing it "from" node_b must not
        # touch either node's counters.
        self._admit(self.node_a)
        result = self._release(node=str(self.node_b))

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['released'])
        self.assertEqual(12, self._capacity(self.node_a).used_cpus)
        self.assertEqual(0, self._capacity(self.node_b).used_cpus)
        self.assertEqual([str(self.node_a)],
                         [r.source_uuid for r in self._references()])

    def test_repeated_named_release_does_not_double_decrement(self):
        # The shape a repeated delete of an errored instance takes:
        # _delete_globally() names the node from the never-cleared
        # placement attribute every time it runs.
        self._admit(self.node_a)
        node = str(self.node_a)

        first = self._release(node=node)
        self.assertTrue(first['released'])
        self.assertEqual(8, self._capacity(self.node_a).used_cpus)
        self.assertEqual(8, self._cluster().unclaimed_used_cpus)

        for _ in range(3):
            repeat = self._release(node=node)
            self.assertTrue(repeat['success'], repeat['error'])
            self.assertFalse(repeat['released'])
            self.assertFalse(repeat['clamped'])
            self.assertEqual(8, self._capacity(self.node_a).used_cpus)
            self.assertEqual(8, self._cluster().unclaimed_used_cpus)

    def test_release_follows_duplicate_references_to_every_node(self):
        self._place_reference(self.node_a, self.instance)
        self._place_reference(self.node_b, self.instance)
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
        # node_a's budget is 0.75 x 64 schedulable threads = 48, and it
        # carries a measured load of 0.5, so five rounds of demand_add
        # 10.0 accumulate 50.0 and take it past target.
        for round_number in range(5):
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
        # back. A sixth round is denied by the demand clause even
        # though every allocation dimension has room, which is the
        # feedforward term doing exactly its job: the node is now over
        # its target load, so the next create is spread elsewhere.
        self.assertEqual(50.0, node.expected_demand)
        sixth = self._admit(self.node_a)
        self.assertFalse(sixth['admitted'])
        self.assertEqual('node', sixth['failing_stage'])


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class PlacementAdmissionConcurrencyLiveTestCase(_LiveCapacityFixture):
    """Adversarially concurrent admissions against a real MariaDB.

    The suite above proves each guard binds. This one proves they bind
    *under contention*, which is the only property the phase exists for:
    "two concurrent creates against one remaining slot cannot both be
    admitted". No mock can test that -- the guarantee is entirely a
    property of InnoDB re-evaluating a guarded UPDATE's WHERE against
    the row as committed by whoever held the lock first -- so this is
    the step 6 harness from
    docs/plans/PLAN-scheduler-reservations-phase-03-primitive.md, kept
    repeatable rather than run once and thrown away.

    Four scenarios, each asserting counters exactly rather than
    approximately, because an off-by-one in a ledger is the whole bug
    class:

    * a race for one slot, repeated for enough rounds that a
      once-in-twenty interleaving cannot pass unnoticed,
    * a burst against known capacity, with two nodes bound by different
      dimensions, so "denied" is checked to name a dimension that
      genuinely was exceeded,
    * a move with a stale duplicate placement row planted first,
    * a randomised admit/release/move soak from several threads,
      followed by a reconcile pass which must report zero drift. That
      last one is the "guard and reconciler agree by construction"
      claim of decision P2, tested rather than asserted in prose.
    """

    # One pooled connection per concurrent admission plus headroom for
    # the assertions' own reads.
    ENGINE_KWARGS = {'pool_size': BURST_THREADS + 8, 'max_overflow': 8,
                     'pool_timeout': 120}

    def setUp(self):
        super().setUp()
        self._prepare_database()
        self.now = time.time()
        self.sizes = {}
        self._report_server_regime()

    def _report_server_regime(self):
        """Record the server settings the run's result is only true for.

        ``innodb_snapshot_isolation`` is ON by default from MariaDB
        11.6.2 and absent before it, and it changes what a guarded
        UPDATE does when it collides: block and re-evaluate, or abort
        with ER_CHECKREAD (1020) for the caller to retry. A concurrency
        result that does not say which regime produced it is not a
        result -- see the phase 0 findings' MariaDB caveats and the
        step 6 notes in
        docs/plans/PLAN-scheduler-reservations-phase-03-primitive.md.
        """
        with self.engine.connect() as conn:
            version = conn.execute(sa.text('SELECT VERSION()')).scalar()
            collation = conn.execute(sa.text(
                'SELECT @@collation_database')).scalar()
            try:
                snapshot = conn.execute(sa.text(
                    'SELECT @@innodb_snapshot_isolation')).scalar()
            except sa.exc.OperationalError:
                snapshot = 'absent (pre 11.6.2)'
        self._report('server-regime', (
            f'MariaDB {version}, collation {collation}, '
            f'innodb_snapshot_isolation {snapshot}'))

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def _add_node(self, limit_cpus=1000, limit_memory_mb=1000000,
                  limit_disk_gb=100000, used_cpus=0, used_memory_mb=0,
                  used_disk_gb=0, cpu_schedulable=64, cpu_load_1=0.5,
                  reconcilable=False):
        """Seed one hypervisor: a capacity row and a metrics row.

        ``reconcilable`` adds the nodes and object_states rows the
        reconciler needs before it will keep a capacity row for this
        node; the tests which never reconcile do not need them.
        """
        node = uuid4()
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        metrics_t = mariadb._get_node_metrics_table()
        nodes_t = mariadb._get_nodes_table()
        states_t = mariadb._get_object_states_table()

        with self.engine.connect() as conn:
            self._insert(conn, capacity_t, node_uuid=node,
                         limit_cpus=limit_cpus,
                         limit_memory_mb=limit_memory_mb,
                         limit_disk_gb=limit_disk_gb, used_cpus=used_cpus,
                         used_memory_mb=used_memory_mb,
                         used_disk_gb=used_disk_gb, expected_demand=0.0,
                         updated_at=sa.func.now())
            self._insert(conn, metrics_t, node_uuid=node,
                         cpu_schedulable=cpu_schedulable,
                         cpu_load_1=cpu_load_1, memory_max=65536,
                         memory_reserved_mb=2048,
                         disk_free_instances=500 * GiB,
                         disk_reservation_gb=20, metrics_json={},
                         is_hypervisor=True, timestamp=self.now,
                         fqdn=f'n-{str(node)[:8]}')
            if reconcilable:
                self._insert(conn, nodes_t, uuid=node,
                             fqdn=f'n-{str(node)[:8]}')
                self._insert(conn, states_t, object_type=ObjectType.NODE,
                             object_uuid=str(node), state_value='created',
                             update_time=self.now)
            conn.commit()
        return node

    def _add_instance(self, cpus=4, memory_mb=4096, disk_gb=40,
                      namespace='ci-1', stated=False):
        instance = uuid4()
        instances_t = mariadb._get_instances_table()
        attributes_t = mariadb._get_instance_attributes_table()
        states_t = mariadb._get_object_states_table()

        with self.engine.connect() as conn:
            self._insert(conn, instances_t, uuid=instance, cpus=cpus,
                         memory=memory_mb,
                         disk_spec=json.dumps([{'size': disk_gb}]),
                         namespace=namespace, name=str(instance)[:8])
            self._insert(conn, attributes_t, uuid=instance,
                         placement=json.dumps(None))
            if stated:
                self._insert(conn, states_t, object_type=ObjectType.INSTANCE,
                             object_uuid=str(instance),
                             state_value='created', update_time=self.now)
            conn.commit()
        self.sizes[instance] = {'cpus': cpus, 'memory_mb': memory_mb,
                                'disk_gb': disk_gb, 'namespace': namespace}
        return instance

    def _set_cluster(self, total_cpus, total_memory_mb, total_disk_gb,
                     unclaimed_used_cpus=0, unclaimed_used_memory_mb=0,
                     unclaimed_used_disk_gb=0):
        table = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            self._insert(conn, table, id=1, total_cpus=total_cpus,
                         total_memory_mb=total_memory_mb,
                         total_disk_gb=total_disk_gb, claimed_cpus=0,
                         claimed_memory_mb=0, claimed_disk_gb=0,
                         unclaimed_used_cpus=unclaimed_used_cpus,
                         unclaimed_used_memory_mb=unclaimed_used_memory_mb,
                         unclaimed_used_disk_gb=unclaimed_used_disk_gb,
                         updated_at=sa.func.now())
            conn.commit()

    # ------------------------------------------------------------------
    # Calling the primitive
    # ------------------------------------------------------------------

    def _admit(self, node, instance, old_node='', enforce=True,
               demand_add=None, target_load=TARGET_LOAD):
        size = self.sizes[instance]
        if demand_add is None:
            demand_add = size['cpus'] * DEMAND_PER_VCPU
        return mariadb._direct_admit_instance_placement(
            str(instance), size['namespace'], str(node), old_node,
            size['cpus'], size['memory_mb'], size['disk_gb'], demand_add,
            target_load, enforce,
            json.dumps({'node': str(node), 'placement_attempts': 1}))

    def _release(self, instance, node=''):
        size = self.sizes[instance]
        return mariadb._direct_release_instance_placement(
            str(instance), size['namespace'], node, size['cpus'],
            size['memory_mb'], size['disk_gb'])

    def _reconcile(self):
        result = mariadb._direct_reconcile_scheduler_capacity(
            DEMAND_PER_VCPU, 600, 1.0)
        self.assertIsNotNone(result, 'reconcile pass failed')
        return result

    # ------------------------------------------------------------------
    # Concurrency and reporting plumbing
    # ------------------------------------------------------------------

    def _run_concurrently(self, calls):
        """Fire every call at once; return [(reply, seconds), ...].

        The barrier is load bearing. Without it thread startup staggers
        the calls enough that the first transaction has committed before
        the last has begun, and the race this suite exists to run never
        happens -- the test would pass for the wrong reason.

        Anything raised out of a worker is collected and asserted on in
        the main thread: the primitive must express "no capacity" as a
        denial reply, never as an exception, so a raise here is a
        finding rather than a test error.
        """
        barrier = threading.Barrier(len(calls))
        outcomes = [None] * len(calls)
        failures = []

        def _worker(index, call):
            try:
                barrier.wait(timeout=120)
                started = time.perf_counter()
                reply = call()
                outcomes[index] = (reply, time.perf_counter() - started)
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

    def _report(self, label, text):
        """Record a measurement both durably and visibly.

        addDetail keeps it with the (possibly failing) test result;
        stderr is what a developer watching a soak run actually sees,
        and what the plan's Validation numbers were read from.
        """
        self.addDetail(label, content.text_content(text))
        sys.stderr.write(f'\n{label}: {text}\n')

    def _timing_summary(self, label, seconds):
        ordered = sorted(seconds)
        index = max(0, math.ceil(0.99 * len(ordered)) - 1)
        median = statistics.median(ordered)
        p99 = ordered[index]
        self._report(label, (
            f'{len(ordered)} calls, median {median * 1000:.1f} ms, '
            f'p99 {p99 * 1000:.1f} ms, max {ordered[-1] * 1000:.1f} ms'))
        return median, p99

    # ------------------------------------------------------------------
    # Scenario (a): a race for exactly one slot
    # ------------------------------------------------------------------

    def _reset_race_round(self, node):
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        cluster_t = mariadb._get_cluster_capacity_table()
        refs_t = mariadb._get_object_references_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(capacity_t).where(
                capacity_t.c.node_uuid == node).values(
                    used_cpus=8, used_memory_mb=8192, used_disk_gb=80,
                    expected_demand=0.0))
            conn.execute(sa.update(cluster_t).where(
                cluster_t.c.id == 1).values(
                    unclaimed_used_cpus=8, unclaimed_used_memory_mb=8192,
                    unclaimed_used_disk_gb=80))
            conn.execute(sa.delete(refs_t))
            conn.commit()

    def test_a_race_for_one_slot_admits_exactly_once(self):
        # The success criterion the phase was written for, run wide (8
        # threads rather than the plan's two) and repeatedly, because a
        # guard that leaks one admission in twenty interleavings is a
        # guard that would leak in production and pass a single-shot
        # test.
        node = self._add_node(limit_cpus=12, limit_memory_mb=12288,
                              limit_disk_gb=120, used_cpus=8,
                              used_memory_mb=8192, used_disk_gb=80)
        # Deliberately roomy, so the node row is the only thing that can
        # refuse and a miscount cannot hide behind the cluster guard.
        self._set_cluster(total_cpus=1000, total_memory_mb=1024000,
                          total_disk_gb=100000, unclaimed_used_cpus=8,
                          unclaimed_used_memory_mb=8192,
                          unclaimed_used_disk_gb=80)
        instances = [self._add_instance() for _ in range(RACE_THREADS)]

        seconds = []
        for round_number in range(RACE_ROUNDS):
            self._reset_race_round(node)
            outcomes = self._run_concurrently([
                functools.partial(self._admit, node, instance)
                for instance in instances])
            seconds.extend(elapsed for _, elapsed in outcomes)
            replies = [reply for reply, _ in outcomes]

            for reply in replies:
                self.assertTrue(reply['success'],
                                f'round {round_number}: {reply["error"]}')
            admitted = [r for r in replies if r['admitted']]
            self.assertEqual(
                1, len(admitted),
                f'round {round_number}: {len(admitted)} of {RACE_THREADS} '
                f'admitted into one slot')
            for reply in replies:
                if reply['admitted']:
                    continue
                # A clean denial, at the stage that ran out of room.
                self.assertEqual('node', reply['failing_stage'])
                self.assertTrue(
                    any(d['exceeded'] for d in reply['dimensions']),
                    f'round {round_number}: denial named no dimension')

            row = self._capacity(node)
            self.assertEqual(12, row.used_cpus, f'round {round_number}')
            self.assertEqual(12288, row.used_memory_mb)
            self.assertEqual(120, row.used_disk_gb)
            # Exactly one admission's worth of feedforward, so no denied
            # transaction left its increment behind.
            self.assertEqual(4 * DEMAND_PER_VCPU, row.expected_demand)

            cluster = self._cluster()
            self.assertEqual(12, cluster.unclaimed_used_cpus,
                             f'round {round_number}: cluster row leaked')
            self.assertEqual(12288, cluster.unclaimed_used_memory_mb)
            self.assertEqual(120, cluster.unclaimed_used_disk_gb)

            placed = [(instance, reference.source_uuid)
                      for instance in instances
                      for reference in self._references_for(instance)]
            self.assertEqual(1, len(placed),
                             f'round {round_number}: {placed}')

        self._report('race-shape', (
            f'{RACE_ROUNDS} rounds x {RACE_THREADS} threads, '
            f'1 admitted and {RACE_THREADS - 1} denied per round'))
        self._timing_summary('race-admission-timings', seconds)

    # ------------------------------------------------------------------
    # Scenario (b): a burst against known capacity
    # ------------------------------------------------------------------

    def test_a_burst_admits_exactly_the_fitting_number(self):
        # Two nodes bound by different dimensions, because this phase
        # gives RAM and disk allocation-denominated protection for the
        # first time and a burst that only ever binds on cpus would not
        # show it.
        cpu_node = self._add_node(limit_cpus=12, limit_memory_mb=1000000,
                                  limit_disk_gb=100000)
        memory_node = self._add_node(limit_cpus=1000, limit_memory_mb=8192,
                                     limit_disk_gb=100000)
        self._set_cluster(total_cpus=1012, total_memory_mb=1008192,
                          total_disk_gb=200000)

        instances = [self._add_instance() for _ in range(BURST_THREADS)]
        # Alternating, so the two nodes are contended simultaneously and
        # the cluster singleton -- which every one of the 50 must take a
        # lock on first -- is the shared choke point it is in production.
        targets = [cpu_node if index % 2 == 0 else memory_node
                   for index in range(BURST_THREADS)]

        outcomes = self._run_concurrently([
            functools.partial(self._admit, target, instance)
            for target, instance in zip(targets, instances)])

        admitted_by_node = {cpu_node: 0, memory_node: 0}
        denied_by_node = {cpu_node: [], memory_node: []}
        for (reply, _), target in zip(outcomes, targets):
            self.assertTrue(reply['success'], reply['error'])
            if reply['admitted']:
                admitted_by_node[target] += 1
            else:
                denied_by_node[target].append(reply)

        # 12 cpus / 4 = 3, and 8192 MB / 4096 = 2.
        self.assertEqual(3, admitted_by_node[cpu_node])
        self.assertEqual(2, admitted_by_node[memory_node])

        cpu_row = self._capacity(cpu_node)
        self.assertEqual(12, cpu_row.used_cpus)
        self.assertEqual(3 * 4096, cpu_row.used_memory_mb)
        self.assertEqual(3 * 40, cpu_row.used_disk_gb)
        memory_row = self._capacity(memory_node)
        self.assertEqual(8192, memory_row.used_memory_mb)
        self.assertEqual(2 * 4, memory_row.used_cpus)
        self.assertEqual(2 * 40, memory_row.used_disk_gb)

        cluster = self._cluster()
        self.assertEqual(5 * 4, cluster.unclaimed_used_cpus)
        self.assertEqual(5 * 4096, cluster.unclaimed_used_memory_mb)
        self.assertEqual(5 * 40, cluster.unclaimed_used_disk_gb)

        # Every denial names a dimension that really is exceeded, and
        # the one this node is bound by. The detail is recomputed from a
        # read after the rollback, so it could in principle disagree
        # with the guard that refused; it cannot understate here because
        # a burst with no releases only ever grows the used counters.
        for node, dimension in ((cpu_node, 'cpus'),
                                (memory_node, 'memory_mb')):
            self.assertEqual(25 - admitted_by_node[node],
                             len(denied_by_node[node]))
            for reply in denied_by_node[node]:
                self.assertEqual('node', reply['failing_stage'])
                detail = {d['dimension']: d for d in reply['dimensions']}
                self.assertTrue(detail[dimension]['exceeded'],
                                f'{dimension} denial did not name it')
                self.assertGreater(
                    detail[dimension]['used'] + detail[dimension]['requested'],
                    detail[dimension]['limit'])

        # D13's feedforward term accumulated once per admission and not
        # once per attempt: 20 admitted vCPUs x the demand constant.
        self.assertEqual(3 * 4 * DEMAND_PER_VCPU, cpu_row.expected_demand)
        self.assertEqual(2 * 4 * DEMAND_PER_VCPU, memory_row.expected_demand)
        self.assertEqual(
            5 * 4 * DEMAND_PER_VCPU,
            cpu_row.expected_demand + memory_row.expected_demand)

        self._report('burst-shape', (
            f'{BURST_THREADS} concurrent admissions, '
            f'{sum(admitted_by_node.values())} admitted '
            f'({admitted_by_node[cpu_node]} cpu-bound node, '
            f'{admitted_by_node[memory_node]} memory-bound node), '
            f'{BURST_THREADS - sum(admitted_by_node.values())} denied'))
        self._timing_summary('burst-admission-timings',
                             [elapsed for _, elapsed in outcomes])

    # ------------------------------------------------------------------
    # Scenario (c): a move over a stale duplicate placement row
    # ------------------------------------------------------------------

    def test_a_move_eliminates_a_planted_duplicate_placement(self):
        # Survey item 1: the counter ledger fail-closes on duplicate
        # placement rows, so the primitive has to stop them being
        # producible rather than filter them out. The duplicate planted
        # here is exactly what the old non-atomic triple could leave
        # behind when its best-effort removal lost.
        old_node = self._add_node(limit_cpus=48, limit_memory_mb=49152,
                                  limit_disk_gb=480)
        new_node = self._add_node(limit_cpus=48, limit_memory_mb=49152,
                                  limit_disk_gb=480)
        stale_node = self._add_node()
        self._set_cluster(total_cpus=1000, total_memory_mb=1024000,
                          total_disk_gb=100000)
        # A claim makes the namespace side visible: with one active, the
        # claim row rather than the cluster singleton is what a move
        # must leave alone.
        claim_uuid = self._add_claim(namespace='ci-1', limit_cpus=64,
                                     limit_memory_mb=65536,
                                     limit_disk_gb=640, used_cpus=0,
                                     used_memory_mb=0, used_disk_gb=0)
        instance = self._add_instance()

        self.assertTrue(self._admit(old_node, instance)['admitted'])
        before = self._claim(claim_uuid)
        self.assertEqual(4, before.used_cpus)

        self._place_reference(stale_node, instance)
        self.assertEqual(2, len(self._references_for(instance)))

        reply = self._admit(new_node, instance, old_node=str(old_node))
        self.assertTrue(reply['admitted'], reply['error'])
        self.assertFalse(reply['clamped'])

        self.assertEqual(0, self._capacity(old_node).used_cpus)
        self.assertEqual(0, self._capacity(old_node).used_memory_mb)
        self.assertEqual(0, self._capacity(old_node).used_disk_gb)
        self.assertEqual(4, self._capacity(new_node).used_cpus)
        self.assertEqual(4096, self._capacity(new_node).used_memory_mb)
        self.assertEqual(40, self._capacity(new_node).used_disk_gb)

        # Exactly one row survives, on the new node -- the planted
        # duplicate went with it even though no caller named stale_node.
        self.assertEqual([str(new_node)],
                         [r.source_uuid for r in
                          self._references_for(instance)])
        self.assertEqual({'node': str(new_node), 'placement_attempts': 1},
                         self._placement_of(instance))

        # The namespace side is untouched by a move: a move never
        # changes namespace, and the instance has been counted there
        # since its first placement.
        after = self._claim(claim_uuid)
        self.assertEqual(before.used_cpus, after.used_cpus)
        self.assertEqual(before.used_memory_mb, after.used_memory_mb)
        self.assertEqual(before.used_disk_gb, after.used_disk_gb)
        cluster = self._cluster()
        self.assertEqual(0, cluster.unclaimed_used_cpus)

    def test_concurrent_moves_between_two_nodes_do_not_deadlock(self):
        # The reason both scheduler_node_capacity rows in a move are
        # touched in uuid order: two moves crossing in opposite
        # directions between the same pair of nodes would otherwise take
        # the rows in opposite orders. A deadlock is retried rather than
        # fatal, so what this asserts is that every reply is a clean
        # success and the counters end where ground truth says.
        node_one = self._add_node(limit_cpus=48, limit_memory_mb=49152,
                                  limit_disk_gb=480)
        node_two = self._add_node(limit_cpus=48, limit_memory_mb=49152,
                                  limit_disk_gb=480)
        self._set_cluster(total_cpus=1000, total_memory_mb=1024000,
                          total_disk_gb=100000)

        movers = [self._add_instance() for _ in range(4)]
        stayers = [self._add_instance() for _ in range(4)]
        # demand_add is zero throughout: a move adds the new node's
        # feedforward term without crediting the old node's back (the
        # term decays with instance age and is recomputed by the
        # reconciler), so eight placements plus eight moves would deny
        # on D13 for reasons that have nothing to do with lock order.
        for instance in movers:
            self.assertTrue(self._admit(node_one, instance,
                                        demand_add=0.0)['admitted'])
        for instance in stayers:
            self.assertTrue(self._admit(node_two, instance,
                                        demand_add=0.0)['admitted'])

        calls = [functools.partial(self._admit, node_two, instance,
                                   old_node=str(node_one), demand_add=0.0)
                 for instance in movers]
        calls += [functools.partial(self._admit, node_one, instance,
                                    old_node=str(node_two), demand_add=0.0)
                  for instance in stayers]
        for reply, _ in self._run_concurrently(calls):
            self.assertTrue(reply['success'], reply['error'])
            self.assertTrue(reply['admitted'], reply['failing_stage'])
            self.assertFalse(reply['clamped'])

        # Every instance swapped nodes, so the two nodes swapped totals
        # -- which here are the same, four instances each.
        for node in (node_one, node_two):
            row = self._capacity(node)
            self.assertEqual(16, row.used_cpus)
            self.assertEqual(4 * 4096, row.used_memory_mb)
            self.assertEqual(4 * 40, row.used_disk_gb)
        self.assertEqual(8 * 4, self._cluster().unclaimed_used_cpus)

    # ------------------------------------------------------------------
    # Scenario (d): randomised cycling, then the reconciler
    # ------------------------------------------------------------------

    def test_randomised_cycling_leaves_the_reconciler_nothing_to_fix(self):
        """The P2 claim -- guard and reconciler agree by construction.

        The guard denominates in the allocation ledger precisely because
        that is what the reconciler recomputes from ground truth, so
        after any sequence of admissions, moves and releases a reconcile
        pass must find nothing to change. ``delta_used_*`` per node is
        that drift signal; a non-zero one means the primitive and the
        recompute disagree about what a placement costs.
        """
        for name, value in (('CPU_OVERCOMMIT_RATIO', 3.0),
                            ('RAM_OVERCOMMIT_RATIO', 3.0)):
            ratio = mock.patch.object(mariadb.config, name, value)
            ratio.start()
            self.addCleanup(ratio.stop)

        # Deliberately uneven, so the small nodes deny sometimes and the
        # soak exercises the rollback path as well as the happy one.
        nodes = [self._add_node(cpu_schedulable=threads, reconcilable=True)
                 for threads in (16, 8, 4, 2)[:SOAK_NODES]]
        shapes = [(1, 1024, 10), (2, 2048, 20), (4, 4096, 40)]
        instances = [
            self._add_instance(*shapes[index % len(shapes)], stated=True)
            for index in range(SOAK_INSTANCES)]

        # Establish ground truth before the soak: the reconciler writes
        # the limits its own arithmetic derives, so the counters the
        # soak starts from are the ones it would have recomputed.
        self._reconcile()

        placement = {instance: None for instance in instances}
        instance_locks = {instance: threading.Lock()
                          for instance in instances}
        tally = {'admit': 0, 'move': 0, 'release': 0, 'denied': 0}
        tally_lock = threading.Lock()
        failures = []
        seconds = []

        def _record(kind, elapsed):
            with tally_lock:
                tally[kind] += 1
                seconds.append(elapsed)

        def _worker(seed):
            rng = random.Random(seed)
            try:
                for _ in range(SOAK_OPERATIONS_PER_THREAD):
                    instance = rng.choice(instances)
                    # Per instance serialisation models the attribute
                    # lock place_instance() holds. Without it two
                    # threads could place the same instance twice with
                    # neither naming the other's node as old_node, which
                    # no production path can do and which would leave a
                    # charge behind by design rather than by bug.
                    with instance_locks[instance]:
                        current = placement[instance]
                        started = time.perf_counter()
                        if current is None:
                            node = rng.choice(nodes)
                            # demand_add is zero for the soak: the
                            # feedforward term only accumulates between
                            # reconcile passes, so a few hundred
                            # operations of it would end with every node
                            # denying on demand and stop exercising the
                            # ledger this test is about.
                            reply = self._admit(node, instance,
                                                demand_add=0.0)
                            elapsed = time.perf_counter() - started
                            if reply['admitted']:
                                placement[instance] = node
                                _record('admit', elapsed)
                            else:
                                _record('denied', elapsed)
                        elif rng.random() < 0.5:
                            node = rng.choice(
                                [n for n in nodes if n != current])
                            reply = self._admit(node, instance,
                                                old_node=str(current),
                                                demand_add=0.0)
                            elapsed = time.perf_counter() - started
                            if reply['admitted']:
                                placement[instance] = node
                                _record('move', elapsed)
                            else:
                                _record('denied', elapsed)
                        else:
                            reply = self._release(instance)
                            elapsed = time.perf_counter() - started
                            placement[instance] = None
                            _record('release', elapsed)

                        if not reply['success']:
                            failures.append(f'rpc failed: {reply["error"]}')
                        if reply['clamped']:
                            # A counter would have gone negative, which
                            # means the ledger had already diverged from
                            # ground truth. Nothing in this soak should
                            # produce that.
                            failures.append(f'clamped on {instance}')
            except BaseException as e:
                failures.append(f'{type(e).__name__}: {e}')

        threads = [threading.Thread(target=_worker, args=(seed,))
                   for seed in range(SOAK_THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=600)
        self.assertEqual([], failures)
        self.assertEqual(SOAK_THREADS * SOAK_OPERATIONS_PER_THREAD,
                         sum(tally.values()))

        # First check the counters against an independent model built in
        # Python from the replies, which is a different oracle from the
        # reconciler's SQL and catches an error the two could share.
        expected_nodes = {node: [0, 0, 0] for node in nodes}
        expected_cluster = [0, 0, 0]
        for instance, node in placement.items():
            if node is None:
                continue
            size = self.sizes[instance]
            for index, key in enumerate(('cpus', 'memory_mb', 'disk_gb')):
                expected_nodes[node][index] += size[key]
                expected_cluster[index] += size[key]
        for node, expected in expected_nodes.items():
            row = self._capacity(node)
            self.assertEqual(expected,
                             [row.used_cpus, row.used_memory_mb,
                              row.used_disk_gb],
                             f'node {node} ledger disagrees with the replies')
        cluster_before = self._cluster()
        self.assertEqual(expected_cluster,
                         [cluster_before.unclaimed_used_cpus,
                          cluster_before.unclaimed_used_memory_mb,
                          cluster_before.unclaimed_used_disk_gb])

        # Then the reconciler, recomputing from placement ground truth.
        result = self._reconcile()
        self.assertEqual(0, result['nodes_added'])
        self.assertEqual(0, result['nodes_removed'])
        for node in result['nodes']:
            self.assertEqual(
                [0, 0, 0],
                [node['delta_used_cpus'], node['delta_used_memory_mb'],
                 node['delta_used_disk_gb']],
                f'reconciler found drift on {node["node_uuid"]}')
        # The cluster singleton is rebuilt rather than deltaed, so its
        # drift is the before/after comparison. total_* is deliberately
        # not compared: limit_disk_gb is "virtual drawdown plus measured
        # free space", so it legitimately moves with used_disk_gb.
        self.assertEqual(cluster_before.unclaimed_used_cpus,
                         result['cluster']['unclaimed_used_cpus'])
        self.assertEqual(cluster_before.unclaimed_used_memory_mb,
                         result['cluster']['unclaimed_used_memory_mb'])
        self.assertEqual(cluster_before.unclaimed_used_disk_gb,
                         result['cluster']['unclaimed_used_disk_gb'])

        self._report('soak-shape', (
            f'{SOAK_THREADS} threads x {SOAK_OPERATIONS_PER_THREAD} '
            f'operations over {SOAK_INSTANCES} instances and '
            f'{len(nodes)} nodes: {tally["admit"]} admits, '
            f'{tally["move"]} moves, {tally["release"]} releases, '
            f'{tally["denied"]} denials; reconciler drift zero on every '
            f'counter'))
        self._timing_summary('soak-operation-timings', seconds)
