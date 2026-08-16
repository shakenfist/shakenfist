# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB test for the scheduler capacity reconciler.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module behind one MariaDB install;
developers can point at a local instance). They exist because the
reconciler's unit tests all run against a mocked connection and assert
on compiled statement text, which cannot exercise the parts most likely
to break:

* the JSON_TABLE derived table, including its DEFAULT ... ON EMPTY /
  ON ERROR clauses against genuinely malformed disk_spec payloads and
  its half-away-from-zero rounding of fractional sizes,
* the REPLACE(dashed, '-', '') joins landing on the instances CHAR(32)
  primary key (CLAUDE.md pitfall 6: comparing the two uuid forms
  directly silently never matches, so a broken join returns zero rows
  rather than an error),
* the enum storage conventions -- object_states persists member names
  while object_references stores member values, so this suite runs
  under utf8mb4_bin, where a binding naming the wrong convention
  returns zero rows instead of being papered over by a
  case-insensitive collation,
* is_hypervisor and the node-state filter against real NULL-able
  columns and a real IN-the-active-set membership test,
* both ON DUPLICATE KEY UPDATE upserts,
* the per-claim usage recompute, whose whole job -- counting an
  instance with a duplicated placement row once, and zeroing a claim
  whose namespace has emptied -- lives in one UPDATE ... JOIN that a
  mocked connection can only be asked about as a string.

The fixture mirrors the one used for the plan's step 4 validation, so
the assertions here are the same hand-computed numbers recorded in
docs/plans/PLAN-scheduler-reservations-phase-02-capacity-tables.md.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database (and this one flips its
collation to utf8mb4_bin for the duration of each test), so they must
be run serially -- ``stestr run --serial``, as CI does.
"""

import json
import os
import time
import unittest
from unittest import mock
from uuid import uuid4

import sqlalchemy as sa

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

# Deliberately messy disk specs. The first is the ordinary case with a
# string size (the resources daemon and the API have both produced
# those); the second has a null size, a sizeless CD-ROM style disk, and
# two fractional sizes (no current write path produces those, but the
# JSON-number-to-BIGINT cast rounds half away from zero -- 2.5 -> 3,
# '1.5' -> 2 -- and disk_spec_virtual_gb must agree with the SQL on
# them or the oracle assertion below is not an oracle); the third is
# not a list at all.
DISK_SPEC_NORMAL = [{'size': 20}, {'size': '8'}]
DISK_SPEC_MESSY = [{'size': None}, {'base': 'x'}, {'size': 2.5},
                   {'size': '1.5'}]
DISK_SPEC_NOT_A_LIST = {'oops': 'not-a-list'}


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class CapacityReconcileLiveTestCase(base.ShakenFistTestCase):
    """Run real reconcile passes against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        # Run this suite against a case-sensitive collation.
        # verify_mariadb_compat() accepts any utf8mb4_* collation, and
        # the reconcile SQL spans two enum storage conventions --
        # object_states persists member names ('INSTANCE') while
        # object_references stores member values ('instance') -- so a
        # binding that names the wrong convention still matches under
        # the default case-insensitive collations and silently returns
        # zero rows under a _bin or _cs one. Creating this suite's
        # tables under utf8mb4_bin makes that class of mistake fail
        # loudly here instead of on the one deployment that pinned a
        # case-sensitive server. The database default is restored in
        # cleanup because the CI database is shared with the other live
        # test modules in the same stestr run.
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

        # The reconciler resolves its engine through _get_engine(), and
        # reads the overcommit ratios from this process's config.
        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (('CPU_OVERCOMMIT_RATIO', 3.0),
                            ('RAM_OVERCOMMIT_RATIO', 3.0)):
            ratio = mock.patch.object(mariadb.config, name, value)
            ratio.start()
            self.addCleanup(ratio.stop)

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
        """Fill NOT NULL columns the reconciler does not care about."""
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

    def _place(self, conn, node_uuid, instance_uuid, placed_at):
        """Write an instance_location reference row.

        Note the dashed uuid strings: object_references stores the dashed
        form while instances.uuid is an undashed CHAR(32), which is the
        join this test exists to exercise.
        """
        self._insert(
            conn, mariadb._get_object_references_table(),
            source_object_type=ObjectType.NODE,
            source_uuid=str(node_uuid),
            relationship=RelationshipType.INSTANCE_LOCATION,
            target_object_type=ObjectType.INSTANCE,
            target_uuid=str(instance_uuid),
            created=placed_at, last_active=placed_at)

    def _seed(self):
        # node_a  active hypervisor, two instances
        # node_b  active hypervisor, a malformed disk_spec and a deleted
        #         instance which must not count
        # node_c  active hypervisor whose metrics lack cpu_schedulable
        # node_d  deleted node, full metrics
        # node_e  active node that is not a hypervisor
        # node_f  errored hypervisor with good, fresh metrics
        # node_g  active hypervisor whose metrics have gone stale
        (self.node_a, self.node_b, self.node_c, self.node_d, self.node_e,
         self.node_f, self.node_g) = (uuid4() for _ in range(7))
        self.instances = {k: uuid4() for k in ('i1', 'i2', 'i3', 'i4', 'i5')}

        nodes_t = mariadb._get_nodes_table()
        metrics_t = mariadb._get_node_metrics_table()
        states_t = mariadb._get_object_states_table()
        instances_t = mariadb._get_instances_table()
        claims_t = mariadb._get_namespace_claims_table()

        common = dict(memory_max=65536, memory_reserved_mb=2048,
                      disk_free_instances=500 * GiB, disk_reservation_gb=20)

        with self.engine.connect() as conn:
            for node, state in ((self.node_a, 'created'),
                                (self.node_b, 'created'),
                                (self.node_c, 'created'),
                                (self.node_d, 'deleted'),
                                (self.node_e, 'created'),
                                (self.node_f, 'error'),
                                (self.node_g, 'degraded')):
                self._insert(conn, nodes_t, uuid=node,
                             fqdn=f'node-{str(node)[:8]}')
                self._insert(conn, states_t, object_type=ObjectType.NODE,
                             object_uuid=str(node), state_value=state,
                             update_time=self.now)

            self._insert(conn, metrics_t, node_uuid=self.node_a,
                         cpu_schedulable=16, metrics_json={},
                         is_hypervisor=True, timestamp=self.now, **common)
            self._insert(conn, metrics_t, node_uuid=self.node_b,
                         cpu_schedulable=32, metrics_json={},
                         is_hypervisor=True, timestamp=self.now, **common)
            self._insert(conn, metrics_t, node_uuid=self.node_c,
                         cpu_schedulable=None, metrics_json={},
                         is_hypervisor=True, timestamp=self.now, **common)
            self._insert(conn, metrics_t, node_uuid=self.node_d,
                         cpu_schedulable=8, metrics_json={},
                         is_hypervisor=True, timestamp=self.now, **common)
            self._insert(conn, metrics_t, node_uuid=self.node_e,
                         cpu_schedulable=99, metrics_json={},
                         is_hypervisor=False, timestamp=self.now, **common)
            self._insert(conn, metrics_t, node_uuid=self.node_f,
                         cpu_schedulable=64, metrics_json={},
                         is_hypervisor=True, timestamp=self.now, **common)
            self._insert(
                conn, metrics_t, node_uuid=self.node_g, cpu_schedulable=48,
                metrics_json={}, is_hypervisor=True,
                timestamp=self.now - mariadb.RECONCILE_METRICS_MAX_AGE_SECONDS
                - 60, **common)

            spec = {
                'i1': dict(node=self.node_a, cpus=4, memory=4096,
                           namespace='ci-1', disk_spec=DISK_SPEC_NORMAL,
                           state='created', placed=self.now - 100),
                # Errored instances keep their placement and their
                # resources until hard delete, so this one counts.
                'i2': dict(node=self.node_a, cpus=2, memory=2048,
                           namespace='ci-1', disk_spec=DISK_SPEC_MESSY,
                           state='error', placed=self.now - 10000),
                # A stateless (zombie) instance counts until the orphan
                # reconciler removes it.
                'i3': dict(node=self.node_b, cpus=8, memory=8192,
                           namespace='manual',
                           disk_spec=DISK_SPEC_NOT_A_LIST,
                           state=None, placed=self.now - 10000),
                'i4': dict(node=self.node_b, cpus=64, memory=65536,
                           namespace='manual', disk_spec=[{'size': 100}],
                           state='deleted', placed=self.now - 10000),
                'i5': dict(node=self.node_d, cpus=2, memory=1024,
                           namespace='manual', disk_spec=[{'size': 10}],
                           state='created', placed=self.now - 10000),
            }
            for key, s in spec.items():
                self._insert(conn, instances_t, uuid=self.instances[key],
                             cpus=s['cpus'], memory=s['memory'],
                             disk_spec=json.dumps(s['disk_spec']),
                             namespace=s['namespace'], name=key)
                if s['state']:
                    self._insert(conn, states_t,
                                 object_type=ObjectType.INSTANCE,
                                 object_uuid=str(self.instances[key]),
                                 state_value=s['state'],
                                 update_time=self.now)
                self._place(conn, s['node'], self.instances[key], s['placed'])

            # Expiry must be written server-relative: a client-computed
            # datetime silently does nothing if the two clocks or
            # timezones differ (found during step 4 validation).
            self._insert(conn, claims_t, uuid=uuid4(), namespace='ci-1',
                         limit_cpus=16, limit_memory_mb=16384,
                         limit_disk_gb=100, state='active',
                         expires_at=sa.text('NOW() + INTERVAL 4 HOUR'),
                         updated_at=sa.func.now())
            self._insert(conn, claims_t, uuid=uuid4(), namespace='stale-ns',
                         limit_cpus=4, limit_memory_mb=4096,
                         limit_disk_gb=40, state='active',
                         expires_at=sa.text('NOW() - INTERVAL 1 HOUR'),
                         updated_at=sa.func.now())
            conn.commit()

    def _reconcile(self):
        result = mariadb._direct_reconcile_scheduler_capacity(2.5, 600, 1.0)
        self.assertIsNotNone(result, 'reconcile pass failed')
        return result

    def _by_node(self, result):
        return {n['node_uuid']: n for n in result['nodes']}

    def _claim_rows(self):
        claims_t = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            return conn.execute(sa.select(claims_t)).fetchall()

    def _claims_by_namespace(self):
        # Only safe where the test's claims are one per namespace; a
        # test with two claims on one namespace reads _claim_rows().
        return {r.namespace: r for r in self._claim_rows()}

    def _add_claim(self, namespace, state='active', used=(0, 0, 0),
                   expires='NOW() + INTERVAL 4 HOUR'):
        """Add a claim beyond the two the fixture seeds.

        The expiry is written server-relative for the reason recorded in
        _seed(): a client-computed datetime silently does nothing if the
        two clocks or timezones differ.
        """
        claims_t = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            self._insert(conn, claims_t, uuid=uuid4(), namespace=namespace,
                         limit_cpus=32, limit_memory_mb=32768,
                         limit_disk_gb=200, used_cpus=used[0],
                         used_memory_mb=used[1], used_disk_gb=used[2],
                         state=state, expires_at=sa.text(expires),
                         updated_at=sa.func.now())
            conn.commit()

    def test_only_active_hypervisors_get_rows(self):
        result = self._reconcile()
        by_node = self._by_node(result)

        self.assertEqual({str(self.node_a), str(self.node_b)},
                         set(by_node.keys()))
        # node_c has no cpu_schedulable and no previous row, node_d is
        # deleted, node_e is not a hypervisor, node_f is errored, and
        # node_g's metrics are stale.
        for node in (self.node_c, self.node_d, self.node_e, self.node_f,
                     self.node_g):
            self.assertNotIn(str(node), by_node)

    def test_limits_match_the_scheduler_arithmetic(self):
        by_node = self._by_node(self._reconcile())
        node_a = by_node[str(self.node_a)]

        self.assertEqual(48, node_a['limit_cpus'])
        self.assertEqual(65536 * 3 - 2048, node_a['limit_memory_mb'])
        # 33 GB currently drawn down, plus 500 GiB free less the 20 GB
        # reservation.
        self.assertEqual(33 + 480, node_a['limit_disk_gb'])

    def test_usage_joins_across_both_uuid_forms(self):
        # If the REPLACE() transform were dropped, the dashed
        # object_references uuid would never match the undashed
        # instances primary key and every one of these would be zero.
        by_node = self._by_node(self._reconcile())
        node_a = by_node[str(self.node_a)]

        self.assertEqual(6, node_a['used_cpus'])
        self.assertEqual(6144, node_a['used_memory_mb'])

    def test_json_table_handles_malformed_disk_specs(self):
        by_node = self._by_node(self._reconcile())

        # 20 + '8' from i1; from i2, nothing for the null and sizeless
        # entries (without the ON EMPTY / ON ERROR defaults the sum
        # would be NULL) plus the rounded fractional sizes: 2.5 -> 3
        # and '1.5' -> 2, half away from zero.
        self.assertEqual(33, by_node[str(self.node_a)]['used_disk_gb'])
        # i3's disk_spec is not an array at all, so the JSON_TYPE guard
        # drops it; i4 is deleted and does not count despite its 100 GB.
        self.assertEqual(0, by_node[str(self.node_b)]['used_disk_gb'])

    def test_disk_spec_reference_agrees_with_the_sql(self):
        # disk_spec_virtual_gb documents the SQL's intended semantics.
        # Assert it really is an oracle rather than stale prose.
        by_node = self._by_node(self._reconcile())
        self.assertEqual(
            mariadb.disk_spec_virtual_gb(DISK_SPEC_NORMAL)
            + mariadb.disk_spec_virtual_gb(DISK_SPEC_MESSY),
            by_node[str(self.node_a)]['used_disk_gb'])
        self.assertEqual(
            mariadb.disk_spec_virtual_gb(DISK_SPEC_NOT_A_LIST),
            by_node[str(self.node_b)]['used_disk_gb'])

    def test_deleted_instance_does_not_count(self):
        by_node = self._by_node(self._reconcile())
        # i4 is deleted (64 cpus), i3 is stateless and does count.
        self.assertEqual(8, by_node[str(self.node_b)]['used_cpus'])

    def test_expected_demand_decays(self):
        by_node = self._by_node(self._reconcile())
        # i1: 4 vCPUs placed 100s ago -> 4 * 2.5 * (1 - 100/600).
        self.assertAlmostEqual(
            8.3333, by_node[str(self.node_a)]['expected_demand'], places=2)
        # i3 was placed well outside the decay window.
        self.assertEqual(0.0, by_node[str(self.node_b)]['expected_demand'])

    def test_claims_expire_and_recompute(self):
        result = self._reconcile()
        self.assertEqual(1, result['claims_expired'])

        claims_t = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            rows = {r.namespace: r for r in
                    conn.execute(sa.select(claims_t)).fetchall()}
        self.assertEqual('expired', rows['stale-ns'].state)
        # The live claim's usage is recomputed from its namespace.
        self.assertEqual(6, rows['ci-1'].used_cpus)
        self.assertEqual(33, rows['ci-1'].used_disk_gb)

    def test_cluster_singleton_sums(self):
        result = self._reconcile()
        cluster = result['cluster']

        self.assertEqual(48 + 96, cluster['total_cpus'])
        # Only the live claim's limits.
        self.assertEqual(16, cluster['claimed_cpus'])
        # The manual namespace has no claim. i3 (8 cpus) counts; i5
        # (2 cpus) does not, because its placement row points at the
        # deleted node_d, which holds no capacity row. That exclusion is
        # a decision, not an accident: the singleton is a closed
        # accounting over the schedulable cluster, so an instance
        # stranded on a node whose limits are not in total_* may not
        # inflate unclaimed_used_* either. (The per-claim counters are
        # the deliberate exception -- see
        # test_claim_usage_is_namespace_wide.)
        self.assertEqual(8, cluster['unclaimed_used_cpus'])

        cluster_t = mariadb._get_cluster_capacity_table()
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(cluster_t)).fetchone()
        self.assertEqual(1, row.id)
        self.assertEqual(cluster['total_cpus'], row.total_cpus)

    def test_claim_usage_is_namespace_wide(self):
        # The mirror image of the closed-accounting rule asserted in
        # test_cluster_singleton_sums: a claim's used_* counters are
        # namespace-wide, so i5 -- stranded on the deleted node_d and
        # invisible to the cluster singleton -- still counts against a
        # claim on its namespace. A quota covers the namespace's
        # instances wherever they are stranded.
        claims_t = mariadb._get_namespace_claims_table()
        with self.engine.connect() as conn:
            self._insert(conn, claims_t, uuid=uuid4(), namespace='manual',
                         limit_cpus=32, limit_memory_mb=32768,
                         limit_disk_gb=200, state='active',
                         expires_at=sa.text('NOW() + INTERVAL 4 HOUR'),
                         updated_at=sa.func.now())
            conn.commit()

        result = self._reconcile()
        with self.engine.connect() as conn:
            rows = {r.namespace: r for r in
                    conn.execute(sa.select(claims_t)).fetchall()}
        # i3 (8, on schedulable node_b) plus i5 (2, stranded on the
        # deleted node_d).
        self.assertEqual(10, rows['manual'].used_cpus)
        # With every namespace claimed, nothing is left unclaimed.
        self.assertEqual(0, result['cluster']['unclaimed_used_cpus'])

    def test_duplicated_placement_counts_once_against_a_claim(self):
        # place_instance()'s removal of an old INSTANCE_LOCATION row is
        # best-effort, so a lost node can leave a stale row behind and
        # one instance then appears on two nodes. A namespace quota must
        # charge that instance once -- the namespace is holding one
        # instance's worth of resources however many placement rows
        # point at it -- so the per-claim aggregation de-duplicates by
        # instance uuid before summing. Summing the node-grouped rows
        # instead (what the reconciler did before this was fixed) counts
        # it twice.
        with self.engine.connect() as conn:
            self._place(conn, self.node_b, self.instances['i1'],
                        self.now - 100)
            conn.commit()

        self._reconcile()
        claim = self._claims_by_namespace()['ci-1']
        # i1 (4, 4096, 28) counted once, plus i2 (2, 2048, 5) -- exactly
        # what the claim reads with no duplicate row present.
        self.assertEqual(6, claim.used_cpus)
        self.assertEqual(6144, claim.used_memory_mb)
        self.assertEqual(33, claim.used_disk_gb)

    def test_duplicated_placement_still_counts_on_both_nodes(self):
        # The other half of the asymmetry asserted above: the per-node
        # ledger is not de-duplicated, because an instance with a
        # placement row on a node really is drawing that node's capacity
        # down as far as anything scheduling onto it knows. Only the
        # namespace-wide claim recompute needs the instance counted
        # once, so if somebody ever "fixes" the node query to match, the
        # duplicate stops being visible where an operator can see it.
        with self.engine.connect() as conn:
            self._place(conn, self.node_b, self.instances['i1'],
                        self.now - 100)
            conn.commit()

        by_node = self._by_node(self._reconcile())
        self.assertEqual(6, by_node[str(self.node_a)]['used_cpus'])
        # node_b's own i3 (8 cpus) plus the duplicated i1 (4).
        self.assertEqual(12, by_node[str(self.node_b)]['used_cpus'])

    def test_claim_for_a_namespace_without_instances_is_zeroed(self):
        # The recompute joins the claims table to the usage aggregation
        # with a LEFT JOIN and COALESCEs the misses to zero. An inner
        # join would leave a claim whose namespace holds no instances at
        # whatever its counters last were -- which is the state every
        # namespace reaches by deleting its last instance -- so seed
        # stale non-zero counters and require them to be cleared.
        self._add_claim('empty-ns', used=(7, 7168, 70))

        self._reconcile()
        claim = self._claims_by_namespace()['empty-ns']
        self.assertEqual(0, claim.used_cpus)
        self.assertEqual(0, claim.used_memory_mb)
        self.assertEqual(0, claim.used_disk_gb)

    def test_every_active_claim_gets_its_own_namespace_figure(self):
        # One statement now updates every active claim, so the join
        # condition is what keeps a namespace's usage on its own claim.
        # A pass with three claims and two populated namespaces catches
        # a join that has become a cross product or that writes the
        # first group's figures to every row.
        self._add_claim('manual')
        self._add_claim('empty-ns')

        self._reconcile()
        rows = self._claims_by_namespace()

        # ci-1: i1 (4, 4096, 28) and i2 (2, 2048, 5).
        self.assertEqual((6, 6144, 33),
                         (rows['ci-1'].used_cpus, rows['ci-1'].used_memory_mb,
                          rows['ci-1'].used_disk_gb))
        # manual: i3 (8, 8192, 0 -- its disk_spec is not an array) and
        # i5 (2, 1024, 10), stranded on the deleted node_d but still the
        # namespace's. i4 is deleted and counts for nothing.
        self.assertEqual((10, 9216, 10),
                         (rows['manual'].used_cpus,
                          rows['manual'].used_memory_mb,
                          rows['manual'].used_disk_gb))
        self.assertEqual((0, 0, 0),
                         (rows['empty-ns'].used_cpus,
                          rows['empty-ns'].used_memory_mb,
                          rows['empty-ns'].used_disk_gb))

    def test_expired_claim_is_not_recomputed(self):
        # An expired claim covers nothing, so the recompute must leave
        # it alone -- both because rewriting it is work for no reader
        # and because its last counters are the record of what it held
        # when it lapsed. This claim names a namespace that does hold
        # instances, so a recompute that ignored the state predicate
        # would visibly overwrite the sentinel.
        self._add_claim('ci-1', state='expired', used=(99, 9999, 999),
                        expires='NOW() - INTERVAL 1 HOUR')

        self._reconcile()
        expired = [r for r in self._claim_rows()
                   if r.state == 'expired' and r.namespace == 'ci-1']
        self.assertEqual(1, len(expired))
        self.assertEqual(99, expired[0].used_cpus)
        self.assertEqual(9999, expired[0].used_memory_mb)
        self.assertEqual(999, expired[0].used_disk_gb)

    def test_rows_are_written_and_upserted(self):
        self._reconcile()
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            rows = {r.node_uuid: r for r in
                    conn.execute(sa.select(capacity_t)).fetchall()}
        self.assertEqual(2, len(rows))
        self.assertEqual(6, rows[self.node_a].used_cpus)

        # A second pass exercises the ON DUPLICATE KEY UPDATE half of
        # both upserts rather than the INSERT half.
        second = self._reconcile()
        self.assertEqual(0, second['nodes_added'])
        self.assertEqual(0, second['claims_expired'])
        for node in second['nodes']:
            self.assertEqual(0, node['delta_used_cpus'])
            self.assertEqual(0, node['delta_used_memory_mb'])
            self.assertEqual(0, node['delta_used_disk_gb'])

        with self.engine.connect() as conn:
            rows_again = {r.node_uuid: r for r in
                          conn.execute(sa.select(capacity_t)).fetchall()}
            cluster_rows = conn.execute(
                sa.select(mariadb._get_cluster_capacity_table())).fetchall()
        self.assertEqual(2, len(rows_again))
        self.assertEqual(1, len(cluster_rows), 'singleton was duplicated')

    def test_orphaned_metrics_row_never_gets_a_row(self):
        # A node_metrics row that has outlived its node's static and
        # state rows looks like a fresh hypervisor with good capacity
        # columns: there is nothing left to mark it inactive. If
        # existence in the nodes table gated only removal and not
        # creation, this would get a row on the first pass and then be
        # in both previous and metrics_rows forever, so no removal
        # condition would ever fire and its limits would sit in the
        # cluster totals permanently.
        orphan = uuid4()
        metrics_t = mariadb._get_node_metrics_table()
        with self.engine.connect() as conn:
            self._insert(conn, metrics_t, node_uuid=orphan,
                         cpu_schedulable=128, memory_max=65536,
                         memory_reserved_mb=2048,
                         disk_free_instances=500 * GiB,
                         disk_reservation_gb=20, metrics_json={},
                         is_hypervisor=True, timestamp=self.now)
            conn.commit()

        first = self._reconcile()
        self.assertNotIn(str(orphan),
                         [n['node_uuid'] for n in first['nodes']])
        self.assertEqual(48 + 96, first['cluster']['total_cpus'])

        # And it does not creep in on a later pass either.
        second = self._reconcile()
        self.assertNotIn(str(orphan),
                         [n['node_uuid'] for n in second['nodes']])

        capacity_t = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(capacity_t)).fetchall()
        self.assertNotIn(orphan, [r.node_uuid for r in rows])

    def test_stateless_node_never_gets_a_row(self):
        # The orphaned-state sibling of the orphaned-metrics case above:
        # a node with a nodes row and fresh hypervisor metrics but no
        # object_states row at all. The scheduler cannot see it --
        # Nodes([], prefilter='active') resolves through
        # get_objects_by_state, which only returns objects that have a
        # state row -- and stateless zombies are a real condition (the
        # orphan reconciler exists for them). A subtractive
        # NOT-IN-the-active-set filter put this node in neither the
        # active nor the inactive set, so it slipped through and its
        # limits were summed into the cluster totals.
        stateless = uuid4()
        nodes_t = mariadb._get_nodes_table()
        metrics_t = mariadb._get_node_metrics_table()
        with self.engine.connect() as conn:
            self._insert(conn, nodes_t, uuid=stateless,
                         fqdn=f'node-{str(stateless)[:8]}')
            self._insert(conn, metrics_t, node_uuid=stateless,
                         cpu_schedulable=128, memory_max=65536,
                         memory_reserved_mb=2048,
                         disk_free_instances=500 * GiB,
                         disk_reservation_gb=20, metrics_json={},
                         is_hypervisor=True, timestamp=self.now)
            conn.commit()

        first = self._reconcile()
        self.assertNotIn(str(stateless),
                         [n['node_uuid'] for n in first['nodes']])
        self.assertEqual(48 + 96, first['cluster']['total_cpus'])

        # And it does not creep in on a later pass either.
        second = self._reconcile()
        self.assertNotIn(str(stateless),
                         [n['node_uuid'] for n in second['nodes']])

        capacity_t = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(capacity_t)).fetchall()
        self.assertNotIn(stateless, [r.node_uuid for r in rows])

    def test_missing_metrics_row_loses_the_capacity_row(self):
        # sf-resources deletes its own node's node_metrics rows at
        # daemon startup, before the first upsert, so a resources
        # daemon that dies in that window leaves a live, active,
        # sentinel-reporting node with no metrics row at all. The
        # freshness filter must treat "no row" like "stale row": the
        # capacity row is removed rather than being rewritten with its
        # last-known limits forever. (An earlier draft subtracted a
        # stale set instead of intersecting with a fresh set, and a
        # node with no row was in neither.)
        first = self._reconcile()
        self.assertIn(str(self.node_a),
                      [n['node_uuid'] for n in first['nodes']])

        metrics_t = mariadb._get_node_metrics_table()
        with self.engine.connect() as conn:
            conn.execute(sa.delete(metrics_t).where(
                metrics_t.c.node_uuid == self.node_a))
            conn.commit()

        second = self._reconcile()
        self.assertNotIn(str(self.node_a),
                         [n['node_uuid'] for n in second['nodes']])
        self.assertEqual(1, second['nodes_removed'])
        self.assertEqual(96, second['cluster']['total_cpus'])

        capacity_t = mariadb._get_scheduler_node_capacity_table()
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(capacity_t)).fetchall()
        self.assertNotIn(self.node_a, [r.node_uuid for r in rows])

    def test_capacity_rows_are_removed_when_a_node_stops_qualifying(self):
        self._reconcile()
        capacity_t = mariadb._get_scheduler_node_capacity_table()
        states_t = mariadb._get_object_states_table()

        # node_a goes to error, exactly as the node-health cascade would
        # leave it.
        with self.engine.connect() as conn:
            conn.execute(sa.update(states_t).where(sa.and_(
                states_t.c.object_type == ObjectType.NODE,
                states_t.c.object_uuid == str(self.node_a)
            )).values(state_value='error', update_time=time.time()))
            conn.commit()

        result = self._reconcile()
        self.assertEqual([str(self.node_b)],
                         [n['node_uuid'] for n in result['nodes']])
        self.assertEqual(1, result['nodes_removed'])
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(capacity_t)).fetchall()
        self.assertEqual([self.node_b], [r.node_uuid for r in rows])
        # And its capacity is out of the cluster totals.
        self.assertEqual(96, result['cluster']['total_cpus'])
