# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the namespace claim CRUD primitive (phase 4).

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module behind one MariaDB install;
developers can point at a local instance). The unit tests in
test_mariadb_capacity_claims.py all run against a mocked connection and
assert on compiled statement text, which cannot exercise the parts most
likely to break:

* D14's mirror guard actually binding -- that ``claimed + limit <= total
  - unclaimed_used`` in an UPDATE WHERE really does refuse the claim
  that would overflow and admit the one that exactly fits,
* D8's shrink floor actually binding against a real ``used_*``,
* **the drawdown migration agreeing with the reconciler**. That the
  create-time seed and the per-claim recompute are the same query is a
  structural assertion in the unit suite; that they produce the same
  *numbers*, over real placement rows including a stale duplicate and a
  deleted instance, can only be shown by creating a claim and then
  running a pass. If they could disagree, every freshly created claim's
  counters would flap on every reconcile pass, forever,
* the driver's rowcount semantics. SQLAlchemy's mysqldb dialect sets
  CLIENT_FOUND_ROWS, so rowcount is rows *matched*. A zero-sized claim's
  guarded UPDATE has a no-op SET and must still read as "the guard
  passed",
* concurrency. "Two operators racing for the last of the cluster's
  capacity create exactly one claim" is entirely a property of InnoDB
  re-evaluating a guarded UPDATE's WHERE against the row as committed by
  whoever held the lock first, and no mock can test it,
* the ER_CHECKREAD invariant. Every transaction here opens with a
  guarded UPDATE, which is only observably necessary against a server
  with ``innodb_snapshot_isolation`` ON (the default from MariaDB
  11.6.2). The concurrency case reports the regime it ran under.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database (and this one flips its
collation to utf8mb4_bin for the duration of each test), so they must be
run serially -- ``stestr run --serial``, as CI does.
"""

import functools
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock
from uuid import uuid4

import sqlalchemy as sa
from testtools import content

from shakenfist.constants import GiB
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
    'instances',
    'nodes',
    'schema_versions',
]

# How many operators race for the last slot in the concurrency case.
RACE_THREADS = 8
RACE_ROUNDS = 10


class _LiveClaimFixture(base.ShakenFistTestCase):
    """Shared live-MariaDB scaffolding for both suites in this module."""

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
                       mariadb._ensure_cluster_capacity_schema):
            ensure(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (('CPU_OVERCOMMIT_RATIO', 3.0),
                            ('RAM_OVERCOMMIT_RATIO', 3.0)):
            ratio = mock.patch.object(mariadb.config, name, value)
            ratio.start()
            self.addCleanup(ratio.stop)

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

    def _set_cluster(self, total_cpus=100, total_memory_mb=102400,
                     total_disk_gb=1000, claimed_cpus=0,
                     claimed_memory_mb=0, claimed_disk_gb=0,
                     unclaimed_used_cpus=0, unclaimed_used_memory_mb=0,
                     unclaimed_used_disk_gb=0):
        table = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.delete(table))
            self._insert(conn, table, id=1, total_cpus=total_cpus,
                         total_memory_mb=total_memory_mb,
                         total_disk_gb=total_disk_gb,
                         claimed_cpus=claimed_cpus,
                         claimed_memory_mb=claimed_memory_mb,
                         claimed_disk_gb=claimed_disk_gb,
                         unclaimed_used_cpus=unclaimed_used_cpus,
                         unclaimed_used_memory_mb=unclaimed_used_memory_mb,
                         unclaimed_used_disk_gb=unclaimed_used_disk_gb,
                         updated_at=sa.func.now())
            conn.commit()

    def _cluster(self):
        table = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table)).first()

    def _claim_rows(self):
        table = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(table)).fetchall()

    def _add_node(self, cpu_schedulable=16, reconcilable=True):
        """Seed a hypervisor the reconciler will keep a capacity row for."""
        node = uuid4()
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        metrics_t = mariadb._get_node_metrics_table()
        nodes_t = mariadb._get_nodes_table()
        states_t = mariadb._get_object_states_table()

        with self.engine.connect() as conn:
            self._insert(conn, capacity_t, node_uuid=node,
                         limit_cpus=48, limit_memory_mb=190464,
                         limit_disk_gb=480, used_cpus=0, used_memory_mb=0,
                         used_disk_gb=0, expected_demand=0.0,
                         updated_at=sa.func.now())
            self._insert(conn, metrics_t, node_uuid=node,
                         cpu_schedulable=cpu_schedulable, cpu_load_1=0.5,
                         memory_max=65536, memory_reserved_mb=2048,
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

    def _add_instance(self, node, cpus=4, memory_mb=4096, disk_gb=40,
                      namespace='ci-1', state='created',
                      extra_nodes=None):
        """Seed a placed instance, optionally with duplicate placements.

        ``extra_nodes`` plants the stale INSTANCE_LOCATION rows a lost
        node leaves behind. A namespace quota must count such an
        instance once however many rows point at it, which is what the
        aggregation's SELECT DISTINCT is for -- and what the create-time
        seed inherits by sharing that aggregation.
        """
        instance = uuid4()
        instances_t = mariadb._get_instances_table()
        states_t = mariadb._get_object_states_table()

        with self.engine.connect() as conn:
            self._insert(conn, instances_t, uuid=instance, cpus=cpus,
                         memory=memory_mb,
                         disk_spec=json.dumps([{'size': disk_gb}]),
                         namespace=namespace, name=str(instance)[:8])
            if state:
                self._insert(conn, states_t,
                             object_type=ObjectType.INSTANCE,
                             object_uuid=str(instance), state_value=state,
                             update_time=self.now)
            for source in [node] + list(extra_nodes or []):
                self._insert(
                    conn, mariadb._get_object_references_table(),
                    source_object_type=str(ObjectType.NODE),
                    source_uuid=str(source),
                    relationship=str(RelationshipType.INSTANCE_LOCATION),
                    target_object_type=str(ObjectType.INSTANCE),
                    target_uuid=str(instance),
                    created=self.now - 60, last_active=self.now)
            conn.commit()
        return instance

    def _report(self, label, text):
        self.addDetail(label, content.text_content(text))
        sys.stderr.write(f'\n{label}: {text}\n')


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class NamespaceClaimCrudLiveTestCase(_LiveClaimFixture):
    """Run real claim creates, updates and deletes against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self._prepare_database()
        self.now = time.time()
        self._set_cluster()

    def _create(self, namespace='ci-1', limit_cpus=16,
                limit_memory_mb=16384, limit_disk_gb=160,
                expires_in_seconds=3600, claim_uuid=None):
        return mariadb._direct_create_namespace_claim(
            str(claim_uuid or uuid4()), namespace, limit_cpus,
            limit_memory_mb, limit_disk_gb, expires_in_seconds)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def test_a_claim_lands_and_reserves_cluster_capacity(self):
        result = self._create()

        self.assertTrue(result['created'], result['error'])
        claim = result['claim']
        self.assertEqual('ci-1', claim['namespace'])
        self.assertEqual(16, claim['limit_cpus'])
        self.assertEqual('active', claim['state'])
        # The expiry was computed from the server's clock, not ours.
        self.assertGreater(claim['expires_at'], time.time() + 3000)
        self.assertLess(claim['expires_at'], time.time() + 4200)

        cluster = self._cluster()
        self.assertEqual(16, cluster.claimed_cpus)
        self.assertEqual(16384, cluster.claimed_memory_mb)
        self.assertEqual(160, cluster.claimed_disk_gb)

    def test_creation_migrates_the_namespaces_existing_drawdown(self):
        # The D3 property, and the whole reason creation is not a plain
        # INSERT: without the migration this namespace could place its
        # entire claim a second time before the next reconcile pass.
        node = self._add_node()
        self._add_instance(node, cpus=4, memory_mb=4096, disk_gb=40)
        self._add_instance(node, cpus=2, memory_mb=2048, disk_gb=20)
        self._set_cluster(unclaimed_used_cpus=10,
                          unclaimed_used_memory_mb=10240,
                          unclaimed_used_disk_gb=100)

        result = self._create()

        self.assertTrue(result['created'], result['error'])
        claim = result['claim']
        self.assertEqual(6, claim['used_cpus'])
        self.assertEqual(6144, claim['used_memory_mb'])
        self.assertEqual(60, claim['used_disk_gb'])

        # ...and the same amounts came off the unclaimed side, in the
        # same transaction that incremented claimed_*.
        cluster = self._cluster()
        self.assertEqual(4, cluster.unclaimed_used_cpus)
        self.assertEqual(4096, cluster.unclaimed_used_memory_mb)
        self.assertEqual(40, cluster.unclaimed_used_disk_gb)
        self.assertEqual(16, cluster.claimed_cpus)

    def test_a_deleted_instance_is_not_migrated(self):
        node = self._add_node()
        self._add_instance(node, cpus=4)
        self._add_instance(node, cpus=64, state='deleted')

        result = self._create()
        self.assertEqual(4, result['claim']['used_cpus'])

    def test_a_duplicated_placement_is_migrated_once(self):
        # A lost node's INSTANCE_LOCATION row survives place_instance()'s
        # best-effort removal, so one instance appears on two nodes. The
        # namespace holds one instance's worth of resources however many
        # rows point at it.
        node = self._add_node()
        other = self._add_node()
        self._add_instance(node, cpus=4, memory_mb=4096, disk_gb=40,
                           extra_nodes=[other])

        result = self._create()
        self.assertEqual(4, result['claim']['used_cpus'])
        self.assertEqual(40, result['claim']['used_disk_gb'])

    def test_the_migration_floors_at_zero(self):
        # The drawdown is a probe, so it can exceed what the singleton
        # holds -- an instance stranded on a node with no capacity row
        # is namespace-wide usage the reconciler's unclaimed fold never
        # counted. Floored, and corrected by the next pass.
        node = self._add_node()
        self._add_instance(node, cpus=8, memory_mb=8192, disk_gb=80)
        self._set_cluster(unclaimed_used_cpus=2,
                          unclaimed_used_memory_mb=2048,
                          unclaimed_used_disk_gb=20)

        self.assertTrue(self._create()['created'])
        cluster = self._cluster()
        self.assertEqual(0, cluster.unclaimed_used_cpus)
        self.assertEqual(0, cluster.unclaimed_used_memory_mb)
        self.assertEqual(0, cluster.unclaimed_used_disk_gb)

    def test_a_claim_larger_than_the_cluster_is_refused(self):
        self._set_cluster(total_cpus=100, claimed_cpus=20,
                          unclaimed_used_cpus=30)

        result = self._create(limit_cpus=51)

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['created'])
        self.assertEqual('capacity', result['refused_reason'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertEqual(80.0, detail['cpus']['limit'])
        self.assertEqual(30.0, detail['cpus']['used'])
        self.assertFalse(detail['memory_mb']['exceeded'])

        # And nothing moved.
        cluster = self._cluster()
        self.assertEqual(20, cluster.claimed_cpus)
        self.assertEqual(0, cluster.claimed_memory_mb)
        self.assertEqual([], self._claim_rows())

    def test_a_namespace_can_claim_capacity_it_is_already_holding(self):
        # The case D14's original formula got wrong, and the reason the
        # guard carries a migrated-drawdown term at all. 100 cpus total,
        # 80 used by unclaimed namespaces of which 40 belong to ci-1.
        # Claiming exactly the 40 ci-1 already holds lands at claimed 40
        # plus unclaimed_used 40 -- a consistent 80 of 100, nothing
        # over-promised -- but a guard testing the *pre*-migration
        # unclaimed sums computed 0 + 40 <= 100 - 80 and refused an
        # operator capacity their own namespace was already using. That
        # is the conductor's primary use case: sizing a runner namespace
        # which already has runners in it.
        node = self._add_node()
        self._add_instance(node, cpus=40, memory_mb=1024, disk_gb=10)
        self._set_cluster(total_cpus=100, unclaimed_used_cpus=80,
                          unclaimed_used_memory_mb=2048,
                          unclaimed_used_disk_gb=20)

        result = self._create(limit_cpus=40, limit_memory_mb=2048,
                              limit_disk_gb=20)

        self.assertTrue(
            result['created'],
            f'{result["refused_reason"]}: {result["dimensions"]}')
        self.assertEqual(40, result['claim']['used_cpus'])

        # The singleton is internally consistent afterwards: claimed
        # plus unclaimed_used is the 80 of 100 that was always in use.
        cluster = self._cluster()
        self.assertEqual(40, cluster.claimed_cpus)
        self.assertEqual(40, cluster.unclaimed_used_cpus)
        self.assertLessEqual(
            cluster.claimed_cpus + cluster.unclaimed_used_cpus,
            cluster.total_cpus)

    def test_a_namespace_cannot_claim_more_than_its_share_plus_free(self):
        # The other half of the migration-aware guard, so the fix above
        # cannot degenerate into "grant everything". Same cluster, but
        # ci-1 asks for 70: post-migration that is claimed 70 plus
        # unclaimed_used 40, or 110 of 100, which does not exist.
        node = self._add_node()
        self._add_instance(node, cpus=40, memory_mb=1024, disk_gb=10)
        self._set_cluster(total_cpus=100, unclaimed_used_cpus=80,
                          unclaimed_used_memory_mb=2048,
                          unclaimed_used_disk_gb=20)

        result = self._create(limit_cpus=70, limit_memory_mb=2048,
                              limit_disk_gb=20)

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['created'])
        self.assertEqual('capacity', result['refused_reason'])

        # The detail describes the state the guard actually tested: the
        # unclaimed sums *after* the migration this create would have
        # performed, not the ones it started from.
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertEqual(100.0, detail['cpus']['limit'])
        self.assertEqual(40.0, detail['cpus']['used'])
        self.assertEqual(70.0, detail['cpus']['requested'])
        self.assertFalse(detail['memory_mb']['exceeded'])

        cluster = self._cluster()
        self.assertEqual(0, cluster.claimed_cpus)
        self.assertEqual(80, cluster.unclaimed_used_cpus)
        self.assertEqual([], self._claim_rows())

    def test_a_grow_migrates_no_drawdown(self):
        # A grow's migrated term is zero because the namespace's usage
        # is already on the claim side of the ledger. Were a grow handed
        # the namespace's drawdown it would credit the same 40 cpus
        # twice and admit a claim the cluster cannot keep.
        node = self._add_node()
        self._add_instance(node, cpus=40, memory_mb=1024, disk_gb=10)
        self._set_cluster(total_cpus=100, unclaimed_used_cpus=80,
                          unclaimed_used_memory_mb=2048,
                          unclaimed_used_disk_gb=20)
        claim = self._create(limit_cpus=40, limit_memory_mb=2048,
                             limit_disk_gb=20)['claim']
        # claimed 40, unclaimed_used 40, so 20 cpus are genuinely free.
        self.assertEqual(40, self._cluster().unclaimed_used_cpus)

        fits = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['limit_cpus'], 60, 0, 0, 0)
        self.assertTrue(fits['updated'], fits['error'])
        self.assertEqual(60, self._cluster().claimed_cpus)
        # ...and the grow did not touch the unclaimed side at all.
        self.assertEqual(40, self._cluster().unclaimed_used_cpus)

        over = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['limit_cpus'], 61, 0, 0, 0)
        self.assertFalse(over['updated'])
        self.assertEqual('capacity', over['refused_reason'])
        self.assertEqual(60, self._cluster().claimed_cpus)

    def test_a_claim_that_exactly_fits_is_created(self):
        # The boundary the guard is written against, with no drawdown to
        # migrate: claimed + limit + unclaimed_used <= total.
        self._set_cluster(total_cpus=100, claimed_cpus=20,
                          unclaimed_used_cpus=30)
        result = self._create(limit_cpus=50)

        self.assertTrue(result['created'], result['error'])
        self.assertEqual(70, self._cluster().claimed_cpus)

    def test_memory_and_disk_bind_as_well_as_cpu(self):
        for dimension, total, over in (
                ('memory_mb', 'total_memory_mb', 102401),
                ('disk_gb', 'total_disk_gb', 1001)):
            self._set_cluster()
            request = {'limit_cpus': 0, 'limit_memory_mb': 0,
                       'limit_disk_gb': 0}
            request[f'limit_{dimension}'] = over
            result = self._create(**request)
            self.assertFalse(result['created'],
                             f'{dimension} did not bind ({total})')
            detail = {d['dimension']: d for d in result['dimensions']}
            self.assertTrue(detail[dimension]['exceeded'])

    def test_a_zero_sized_claim_is_not_read_as_a_denial(self):
        # CLIENT_FOUND_ROWS: rowcount is rows matched, so a guarded
        # UPDATE whose SET happens to be a no-op must read as "the guard
        # passed".
        result = self._create(limit_cpus=0, limit_memory_mb=0,
                              limit_disk_gb=0)
        self.assertTrue(result['created'], result['error'])

    def test_a_cluster_with_no_singleton_refuses(self):
        # Deliberately the opposite of the placement path's P7 fail-open.
        table = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.delete(table))
            conn.commit()

        result = self._create()
        self.assertTrue(result['success'])
        self.assertFalse(result['created'])
        self.assertEqual('no_cluster_capacity', result['refused_reason'])
        self.assertEqual([], self._claim_rows())

    def test_a_second_claim_for_one_namespace_is_refused(self):
        self.assertTrue(self._create()['created'])
        second = self._create()

        self.assertEqual('exists', second['refused_reason'])
        self.assertEqual(1, len(self._claim_rows()))
        self.assertEqual(16, self._cluster().claimed_cpus)

    def test_an_expired_claim_does_not_block_a_new_one(self):
        first = self._create(expires_in_seconds=1)
        self.assertTrue(first['created'])
        table = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(table).values(
                expires_at=sa.text('NOW() - INTERVAL 1 HOUR')))
            conn.commit()

        self.assertTrue(self._create()['created'])

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def test_claims_read_back_by_uuid_and_by_namespace(self):
        first = self._create(namespace='ci-1')['claim']
        second = self._create(namespace='ci-2', limit_cpus=8)['claim']

        self.assertEqual(first, mariadb._direct_get_namespace_claim(
            first['uuid']))
        self.assertIsNone(
            mariadb._direct_get_namespace_claim(str(uuid4())))

        self.assertEqual(
            [second['uuid']],
            [c['uuid'] for c in
             mariadb._direct_get_namespace_claims('ci-2')])
        self.assertEqual(
            {first['uuid'], second['uuid']},
            {c['uuid'] for c in mariadb._direct_get_namespace_claims('')})
        self.assertEqual(
            [], mariadb._direct_get_namespace_claims('no-such-namespace'))

    # ------------------------------------------------------------------
    # Update (D8)
    # ------------------------------------------------------------------

    def test_a_grow_within_capacity_increments_claimed(self):
        claim = self._create(limit_cpus=16)['claim']

        result = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['limit_cpus'], 32, 0, 0, 0)

        self.assertTrue(result['updated'], result['error'])
        self.assertEqual(32, result['claim']['limit_cpus'])
        self.assertEqual(32, self._cluster().claimed_cpus)
        # The dimensions nobody named are untouched.
        self.assertEqual(16384, result['claim']['limit_memory_mb'])

    def test_a_grow_beyond_the_cluster_is_refused(self):
        self._set_cluster(total_cpus=100, unclaimed_used_cpus=20)
        claim = self._create(limit_cpus=16)['claim']

        result = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['limit_cpus'], 81, 0, 0, 0)

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['updated'])
        self.assertEqual('capacity', result['refused_reason'])
        # Nothing moved, on either row.
        self.assertEqual(16, self._cluster().claimed_cpus)
        self.assertEqual(
            16, mariadb._direct_get_namespace_claim(
                claim['uuid'])['limit_cpus'])

    def test_a_shrink_below_usage_is_refused(self):
        node = self._add_node()
        self._add_instance(node, cpus=8, memory_mb=8192, disk_gb=80)
        claim = self._create(limit_cpus=16)['claim']
        self.assertEqual(8, claim['used_cpus'])

        result = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['limit_cpus'], 4, 0, 0, 0)

        self.assertTrue(result['success'], result['error'])
        self.assertFalse(result['updated'])
        self.assertEqual('below_usage', result['refused_reason'])
        detail = {d['dimension']: d for d in result['dimensions']}
        self.assertTrue(detail['cpus']['exceeded'])
        self.assertEqual(4.0, detail['cpus']['limit'])
        self.assertEqual(8.0, detail['cpus']['used'])

        self.assertEqual(16, self._cluster().claimed_cpus)
        self.assertEqual(
            16, mariadb._direct_get_namespace_claim(
                claim['uuid'])['limit_cpus'])

    def test_a_shrink_to_exactly_usage_succeeds(self):
        node = self._add_node()
        self._add_instance(node, cpus=8, memory_mb=8192, disk_gb=80)
        claim = self._create(limit_cpus=16)['claim']

        result = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['limit_cpus'], 8, 0, 0, 0)

        self.assertTrue(result['updated'], result['error'])
        self.assertEqual(8, result['claim']['limit_cpus'])
        self.assertEqual(8, self._cluster().claimed_cpus)

    def test_one_update_grows_one_dimension_and_shrinks_another(self):
        node = self._add_node()
        self._add_instance(node, cpus=4, memory_mb=4096, disk_gb=40)
        claim = self._create(limit_cpus=16, limit_disk_gb=160)['claim']

        result = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['limit_cpus', 'limit_disk_gb'], 32, 0, 40, 0)

        self.assertTrue(result['updated'], result['error'])
        self.assertEqual(32, result['claim']['limit_cpus'])
        self.assertEqual(40, result['claim']['limit_disk_gb'])
        cluster = self._cluster()
        self.assertEqual(32, cluster.claimed_cpus)
        self.assertEqual(40, cluster.claimed_disk_gb)
        # Memory was not named, so neither row moved on it.
        self.assertEqual(16384, cluster.claimed_memory_mb)

    def test_a_mixed_update_that_cannot_grow_shrinks_nothing_either(self):
        # One transaction: a refusal on the grow rolls back the shrink.
        self._set_cluster(total_cpus=100, unclaimed_used_cpus=20)
        claim = self._create(limit_cpus=16, limit_disk_gb=160)['claim']

        result = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['limit_cpus', 'limit_disk_gb'], 81, 0, 40, 0)

        self.assertFalse(result['updated'])
        self.assertEqual('capacity', result['refused_reason'])
        current = mariadb._direct_get_namespace_claim(claim['uuid'])
        self.assertEqual(16, current['limit_cpus'])
        self.assertEqual(160, current['limit_disk_gb'])
        self.assertEqual(160, self._cluster().claimed_disk_gb)

    def test_expiry_may_be_extended_and_shortened(self):
        claim = self._create(expires_in_seconds=3600)['claim']

        longer = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['expires_in_seconds'], 0, 0, 0, 7200)
        self.assertTrue(longer['updated'], longer['error'])
        self.assertGreater(longer['claim']['expires_at'],
                           claim['expires_at'] + 3000)

        shorter = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['expires_in_seconds'], 0, 0, 0, 60)
        self.assertTrue(shorter['updated'], shorter['error'])
        self.assertLess(shorter['claim']['expires_at'],
                        claim['expires_at'])

        # An expiry change moved no capacity.
        self.assertEqual(16, self._cluster().claimed_cpus)

    def test_an_absent_claim_is_not_found(self):
        result = mariadb._direct_update_namespace_claim(
            str(uuid4()), ['limit_cpus'], 4, 0, 0, 0)
        self.assertEqual('not_found', result['refused_reason'])

    def test_an_expired_claim_cannot_be_updated(self):
        claim = self._create()['claim']
        table = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(table).values(state='expired'))
            conn.commit()

        result = mariadb._direct_update_namespace_claim(
            claim['uuid'], ['limit_cpus'], 32, 0, 0, 0)
        self.assertEqual('not_active', result['refused_reason'])
        self.assertEqual(16, self._cluster().claimed_cpus)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def test_delete_returns_capacity_to_the_unclaimed_side(self):
        node = self._add_node()
        self._add_instance(node, cpus=4, memory_mb=4096, disk_gb=40)
        self._set_cluster(unclaimed_used_cpus=4,
                          unclaimed_used_memory_mb=4096,
                          unclaimed_used_disk_gb=40)
        claim = self._create()['claim']
        self.assertEqual(4, claim['used_cpus'])
        self.assertEqual(0, self._cluster().unclaimed_used_cpus)

        result = mariadb._direct_delete_namespace_claim(claim['uuid'])

        self.assertTrue(result['deleted'], result['error'])
        self.assertEqual(4, result['returned_cpus'])
        self.assertEqual(4096, result['returned_memory_mb'])
        self.assertEqual(40, result['returned_disk_gb'])
        self.assertFalse(result['clamped'])

        cluster = self._cluster()
        self.assertEqual(0, cluster.claimed_cpus)
        self.assertEqual(0, cluster.claimed_memory_mb)
        self.assertEqual(4, cluster.unclaimed_used_cpus)
        self.assertEqual(4096, cluster.unclaimed_used_memory_mb)
        self.assertEqual(40, cluster.unclaimed_used_disk_gb)
        self.assertEqual([], self._claim_rows())

    def test_double_delete_is_harmless(self):
        claim = self._create()['claim']
        self.assertTrue(
            mariadb._direct_delete_namespace_claim(claim['uuid'])['deleted'])
        before = self._cluster()

        second = mariadb._direct_delete_namespace_claim(claim['uuid'])

        self.assertTrue(second['success'], second['error'])
        self.assertFalse(second['deleted'])
        self.assertEqual(0, second['returned_cpus'])
        after = self._cluster()
        self.assertEqual(before.claimed_cpus, after.claimed_cpus)
        self.assertEqual(before.unclaimed_used_cpus,
                         after.unclaimed_used_cpus)

    def test_delete_clamps_rather_than_driving_claimed_negative(self):
        claim = self._create(limit_cpus=16)['claim']
        # A reconcile pass has recomputed claimed_* lower in between.
        table = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            conn.execute(sa.update(table).values(claimed_cpus=4))
            conn.commit()

        result = mariadb._direct_delete_namespace_claim(claim['uuid'])

        self.assertTrue(result['deleted'])
        self.assertTrue(result['clamped'])
        self.assertEqual(0, self._cluster().claimed_cpus)

    def test_a_create_delete_cycle_returns_to_the_seeded_counters(self):
        node = self._add_node()
        self._add_instance(node, cpus=4, memory_mb=4096, disk_gb=40)
        self._set_cluster(unclaimed_used_cpus=4,
                          unclaimed_used_memory_mb=4096,
                          unclaimed_used_disk_gb=40)
        before = dict(self._cluster()._mapping)

        for _ in range(3):
            claim = self._create()['claim']
            self.assertTrue(mariadb._direct_delete_namespace_claim(
                claim['uuid'])['deleted'])

        after = dict(self._cluster()._mapping)
        for column in ('claimed_cpus', 'claimed_memory_mb',
                       'claimed_disk_gb', 'unclaimed_used_cpus',
                       'unclaimed_used_memory_mb', 'unclaimed_used_disk_gb'):
            self.assertEqual(before[column], after[column], column)

    # ------------------------------------------------------------------
    # The shared aggregation, end to end
    # ------------------------------------------------------------------

    def _reconcile(self):
        result = mariadb._direct_reconcile_scheduler_capacity(2.5, 600, 1.0)
        self.assertIsNotNone(result, 'reconcile pass failed')
        return result

    def test_create_then_reconcile_moves_nothing(self):
        """The most valuable assertion in this module.

        The create-time seed and the reconciler's per-claim recompute
        share one SQL fragment, so a claim created over real placement
        rows must already hold the figure the next pass will compute.
        The data is deliberately awkward -- a stale duplicate placement,
        a deleted instance, a stateless zombie and a second namespace --
        because those are the cases where two nearly-identical
        aggregations would differ.
        """
        node = self._add_node()
        other = self._add_node()
        self._add_instance(node, cpus=4, memory_mb=4096, disk_gb=40,
                           extra_nodes=[other])
        self._add_instance(other, cpus=2, memory_mb=2048, disk_gb=20)
        self._add_instance(node, cpus=1, memory_mb=1024, disk_gb=10,
                           state=None)
        self._add_instance(node, cpus=64, memory_mb=65536, disk_gb=640,
                           state='deleted')
        self._add_instance(other, cpus=8, memory_mb=8192, disk_gb=80,
                           namespace='ci-2')

        created = self._create(namespace='ci-1', limit_cpus=32,
                               limit_memory_mb=32768, limit_disk_gb=320)
        self.assertTrue(created['created'], created['error'])
        seeded = created['claim']
        self.assertEqual(7, seeded['used_cpus'])
        self.assertEqual(7168, seeded['used_memory_mb'])
        self.assertEqual(70, seeded['used_disk_gb'])

        self._reconcile()

        recomputed = mariadb._direct_get_namespace_claim(seeded['uuid'])
        self.assertEqual(seeded['used_cpus'], recomputed['used_cpus'])
        self.assertEqual(seeded['used_memory_mb'],
                         recomputed['used_memory_mb'])
        self.assertEqual(seeded['used_disk_gb'], recomputed['used_disk_gb'])

    def test_a_second_reconcile_pass_moves_nothing_either(self):
        # Belt and braces on the same property: the first pass could
        # coincidentally agree while the counters were still settling.
        node = self._add_node()
        self._add_instance(node, cpus=4, memory_mb=4096, disk_gb=40)
        claim = self._create()['claim']

        self._reconcile()
        first = mariadb._direct_get_namespace_claim(claim['uuid'])
        self._reconcile()
        second = mariadb._direct_get_namespace_claim(claim['uuid'])

        self.assertEqual(claim['used_cpus'], first['used_cpus'])
        self.assertEqual(first['used_cpus'], second['used_cpus'])


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class NamespaceClaimConcurrencyLiveTestCase(_LiveClaimFixture):
    """Adversarially concurrent claim operations against a real MariaDB.

    The suite above proves each guard binds. This one proves the mirror
    guard binds *under contention*, which no mock can test: the
    guarantee is entirely a property of InnoDB re-evaluating a guarded
    UPDATE's WHERE against the row as committed by whoever held the lock
    first.

    It is also the only place an ER_CHECKREAD regression in the new
    transactions is visible, so it reports the server regime it ran
    under (see issue #3759 for the standing gap that CI's MariaDB does
    not have innodb_snapshot_isolation).
    """

    ENGINE_KWARGS = {'pool_size': RACE_THREADS + 8, 'max_overflow': 8,
                     'pool_timeout': 120}

    def setUp(self):
        super().setUp()
        self._prepare_database()
        self.now = time.time()
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
                snapshot = 'absent (pre 11.6.2)'
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

    def _create(self, namespace, limit_cpus):
        return mariadb._direct_create_namespace_claim(
            str(uuid4()), namespace, limit_cpus, 1024, 10, 3600)

    def test_a_race_for_the_last_capacity_creates_exactly_one_claim(self):
        # Eight operators, each claiming the cluster's whole remaining
        # cpu headroom, in different namespaces so the 'exists' probe is
        # not what refuses them.
        for round_number in range(RACE_ROUNDS):
            self._set_cluster(total_cpus=40, total_memory_mb=102400,
                              total_disk_gb=1000, unclaimed_used_cpus=0)
            claims_t = mariadb._get_namespace_claims_table()
            with self.engine.connect() as conn:
                conn.execute(sa.delete(claims_t))
                conn.commit()

            replies = self._run_concurrently([
                functools.partial(self._create, f'ns-{index}', 40)
                for index in range(RACE_THREADS)])

            for reply in replies:
                self.assertTrue(reply['success'],
                                f'round {round_number}: {reply["error"]}')
            created = [r for r in replies if r['created']]
            self.assertEqual(
                1, len(created),
                f'round {round_number}: {len(created)} of {RACE_THREADS} '
                f'claims took one cluster\'s worth of capacity')
            for reply in replies:
                if reply['created']:
                    continue
                self.assertEqual('capacity', reply['refused_reason'])

            self.assertEqual(1, len(self._claim_rows()),
                             f'round {round_number}')
            self.assertEqual(40, self._cluster().claimed_cpus,
                             f'round {round_number}')

        self._report('claim-race-shape', (
            f'{RACE_ROUNDS} rounds x {RACE_THREADS} threads, 1 created '
            f'and {RACE_THREADS - 1} refused per round'))

    def test_concurrent_creates_for_one_namespace_do_not_double_charge(self):
        # The 'exists' probe is time-of-check-to-time-of-use racy by
        # construction, so two creates for one namespace can both
        # commit. What must not happen is the cluster singleton
        # disagreeing with the claims it is summing: whatever lands,
        # claimed_* is the sum of the rows that landed.
        self._set_cluster(total_cpus=400, total_memory_mb=102400,
                          total_disk_gb=1000)
        replies = self._run_concurrently([
            functools.partial(self._create, 'ci-1', 8)
            for _ in range(RACE_THREADS)])

        created = [r for r in replies if r['created']]
        self.assertGreaterEqual(len(created), 1)
        rows = self._claim_rows()
        self.assertEqual(len(created), len(rows))
        self.assertEqual(sum(r.limit_cpus for r in rows),
                         self._cluster().claimed_cpus)
        self._report('claim-exists-race', (
            f'{RACE_THREADS} concurrent creates for one namespace: '
            f'{len(created)} created, singleton agrees with the rows'))

    def test_concurrent_grow_and_delete_leave_the_singleton_consistent(self):
        self._set_cluster(total_cpus=400, total_memory_mb=102400,
                          total_disk_gb=1000)
        claims = []
        for index in range(RACE_THREADS):
            reply = self._create(f'ns-{index}', 8)
            self.assertTrue(reply['created'], reply['error'])
            claims.append(reply['claim'])

        def _grow(claim):
            return mariadb._direct_update_namespace_claim(
                claim['uuid'], ['limit_cpus'], 16, 0, 0, 0)

        def _delete(claim):
            return mariadb._direct_delete_namespace_claim(claim['uuid'])

        calls = []
        for index, claim in enumerate(claims):
            calls.append(functools.partial(
                _grow if index % 2 else _delete, claim))
        replies = self._run_concurrently(calls)
        for reply in replies:
            self.assertTrue(reply['success'], reply['error'])

        rows = self._claim_rows()
        self.assertEqual(sum(r.limit_cpus for r in rows),
                         self._cluster().claimed_cpus)
        self._report('claim-mixed-race', (
            f'{RACE_THREADS} concurrent grows and deletes: '
            f'{len(rows)} claims left, singleton agrees with the rows'))
