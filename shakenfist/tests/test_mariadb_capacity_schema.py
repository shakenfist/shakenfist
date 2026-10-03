# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Tests for the scheduler-reservations capacity tables.

Covers the three capacity tables added by scheduler-reservations phase 2
(see docs/plans/PLAN-scheduler-reservations-phase-02-capacity-tables.md):
column drift guards for each table definition, the version constants and
their EXPECTED_SCHEMA_VERSIONS wiring, and the fresh-create / no-op
branches of the _ensure_*_schema() functions. The ensure functions run
against mocked engines, so no real MariaDB is needed; these tests only
exercise the version branching, not DDL execution (functional CI covers
the live creation).
"""

import inspect
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base


# The exact expected columns for each capacity table, in definition
# order. This is a drift guard: any column added to or removed from the
# table definitions in shakenfist/mariadb.py must be reflected here.
EXPECTED_COLUMNS = {
    'scheduler_node_capacity': [
        'node_uuid',
        'limit_cpus',
        'limit_memory_mb',
        'limit_disk_gb',
        'used_cpus',
        'used_memory_mb',
        'used_disk_gb',
        'expected_demand',
        'updated_at',
    ],
    'namespace_claims': [
        'uuid',
        'namespace',
        'limit_cpus',
        'limit_memory_mb',
        'limit_disk_gb',
        'used_cpus',
        'used_memory_mb',
        'used_disk_gb',
        'peak_used_cpus',
        'peak_used_memory_mb',
        'peak_used_disk_gb',
        'state',
        'expires_at',
        'updated_at',
    ],
    'cluster_capacity': [
        'id',
        'total_cpus',
        'total_memory_mb',
        'total_disk_gb',
        'claimed_cpus',
        'claimed_memory_mb',
        'claimed_disk_gb',
        'unclaimed_used_cpus',
        'unclaimed_used_memory_mb',
        'unclaimed_used_disk_gb',
        'updated_at',
    ],
}


# (table_name, table getter, ensure function, version constant) for each
# capacity table, so the branch tests below can cover all three without
# triplicating the assertions.
CAPACITY_TABLES = [
    ('scheduler_node_capacity',
     mariadb._get_scheduler_node_capacity_table,
     mariadb._ensure_scheduler_node_capacity_schema,
     mariadb.SCHEDULER_NODE_CAPACITY_VERSION),
    ('namespace_claims',
     mariadb._get_namespace_claims_table,
     mariadb._ensure_namespace_claims_schema,
     mariadb.NAMESPACE_CLAIMS_VERSION),
    ('cluster_capacity',
     mariadb._get_cluster_capacity_table,
     mariadb._ensure_cluster_capacity_schema,
     mariadb.CLUSTER_CAPACITY_VERSION),
]


class CapacityTableDefinitionTestCase(base.ShakenFistTestCase):
    """Drift guards for the capacity table definitions."""

    def test_scheduler_node_capacity_columns(self):
        table = mariadb._get_scheduler_node_capacity_table()
        self.assertEqual(EXPECTED_COLUMNS['scheduler_node_capacity'],
                         [c.name for c in table.c])

    def test_namespace_claims_columns(self):
        table = mariadb._get_namespace_claims_table()
        self.assertEqual(EXPECTED_COLUMNS['namespace_claims'],
                         [c.name for c in table.c])

    def test_cluster_capacity_columns(self):
        table = mariadb._get_cluster_capacity_table()
        self.assertEqual(EXPECTED_COLUMNS['cluster_capacity'],
                         [c.name for c in table.c])

    def test_scheduler_node_capacity_types(self):
        table = mariadb._get_scheduler_node_capacity_table()

        self.assertIsInstance(table.c['node_uuid'].type, sa.Uuid)
        self.assertTrue(table.c['node_uuid'].primary_key)

        # expected_demand is a DOUBLE with a server-side default of zero;
        # the counters are BIGINTs defaulting to zero. BigInteger is asserted
        # rather than Integer (which BigInteger would also satisfy) so a
        # future narrowing of the deliberate overflow-avoiding widening
        # fails a test rather than requiring a migration.
        self.assertIsInstance(table.c['expected_demand'].type, sa.Double)
        for name in ('used_cpus', 'used_memory_mb', 'used_disk_gb'):
            self.assertIsInstance(table.c[name].type, sa.BigInteger)
            self.assertIsNotNone(table.c[name].server_default,
                                 f'{name} should default to zero')

        # updated_at follows the cluster_locks server-side timestamp
        # idiom.
        self.assertIsInstance(table.c['updated_at'].type, sa.DateTime)

    def test_namespace_claims_types(self):
        table = mariadb._get_namespace_claims_table()

        self.assertIsInstance(table.c['uuid'].type, sa.Uuid)
        self.assertTrue(table.c['uuid'].primary_key)

        # The namespace column matches the namespaces table's name
        # primary key exactly, and is indexed for the phase 3 admission
        # lookup path.
        namespaces = mariadb._get_namespaces_table()
        self.assertIsInstance(table.c['namespace'].type, sa.String)
        self.assertEqual(namespaces.c['name'].type.length,
                         table.c['namespace'].type.length)
        index_columns = [
            [c.name for c in idx.columns] for idx in table.indexes]
        self.assertIn(['namespace'], index_columns)

        # The high-water mark columns match the counters they mark, in
        # width and in default, so an upgraded row reads as zero rather
        # than as NULL and a 64 bit counter cannot outgrow its mark.
        self.assertEqual(('peak_used_cpus', 'peak_used_memory_mb',
                          'peak_used_disk_gb'),
                         mariadb.NAMESPACE_CLAIMS_PEAK_COLUMNS)
        for name in mariadb.NAMESPACE_CLAIMS_PEAK_COLUMNS:
            self.assertIsInstance(table.c[name].type, sa.BigInteger)
            self.assertFalse(table.c[name].nullable, name)
            self.assertIsNotNone(table.c[name].server_default,
                                 f'{name} should default to zero')
        # And the converse: a peak_* column added to the table but not
        # to the tuple would never be added to an upgraded database,
        # because the migration only walks the tuple.
        self.assertEqual(
            set(mariadb.NAMESPACE_CLAIMS_PEAK_COLUMNS),
            {c.name for c in table.c if c.name.startswith('peak_')})

        self.assertIsInstance(table.c['state'].type, sa.String)
        self.assertEqual(32, table.c['state'].type.length)

        # expires_at and updated_at follow the cluster_locks server-side
        # timestamp idiom.
        self.assertIsInstance(table.c['expires_at'].type, sa.DateTime)
        self.assertIsInstance(table.c['updated_at'].type, sa.DateTime)

    def test_cluster_capacity_types(self):
        table = mariadb._get_cluster_capacity_table()

        # The id primary key holds a singleton row (id always 1) written
        # by the reconciler, so it must not be AUTO_INCREMENT.
        self.assertIsInstance(table.c['id'].type, sa.Integer)
        self.assertTrue(table.c['id'].primary_key)
        self.assertFalse(table.c['id'].autoincrement)

        # BigInteger asserted rather than Integer for the same reason as the
        # node capacity counters above: pin the deliberate widening.
        for name in EXPECTED_COLUMNS['cluster_capacity']:
            if name in ('id', 'updated_at'):
                continue
            self.assertIsInstance(table.c[name].type, sa.BigInteger)

        self.assertIsInstance(table.c['updated_at'].type, sa.DateTime)


class CapacitySchemaVersionsTestCase(base.ShakenFistTestCase):
    """The version constants and their EXPECTED_SCHEMA_VERSIONS wiring."""

    def test_capacity_table_versions(self):
        # Pinned rather than derived, so a version bump has to be a
        # deliberate edit here as well as in mariadb.py -- which is the
        # only thing that makes the migration branch tests below mean
        # anything. namespace_claims is at 2 because claim coverage and
        # sizing phase 2b added the used_* high-water mark.
        self.assertEqual(1, mariadb.SCHEDULER_NODE_CAPACITY_VERSION)
        self.assertEqual(2, mariadb.NAMESPACE_CLAIMS_VERSION)
        self.assertEqual(1, mariadb.CLUSTER_CAPACITY_VERSION)

    def test_versions_wired_into_expected_schema_versions(self):
        for table_name, _, _, version in CAPACITY_TABLES:
            self.assertIn(table_name, mariadb.EXPECTED_SCHEMA_VERSIONS)
            self.assertEqual(
                version, mariadb.EXPECTED_SCHEMA_VERSIONS[table_name])


class EnsureCapacitySchemaTestCase(base.ShakenFistTestCase):
    """Tests for the _ensure_*_schema() fresh-create and no-op branches."""

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_fresh_creates_at_the_target_version(
            self, mock_get_version, mock_set_version):
        """From 0: each table is created at its target version.

        A fresh create lands the current table definition in one step,
        so no migration branch may fire behind it -- a table created
        with the high-water mark columns already in it must not then be
        asked to add them.
        """
        for table_name, get_table, ensure, version in CAPACITY_TABLES:
            mock_get_version.reset_mock()
            mock_set_version.reset_mock()
            mock_engine = mock.MagicMock()

            with mock.patch('sqlalchemy.MetaData.create_all') as mock_create:
                result = ensure(mock_engine)

            self.assertEqual(result['table'], table_name)
            self.assertEqual(result['start_version'], 0)
            self.assertEqual(result['end_version'], version)
            self.assertEqual(result['target_version'], version)
            self.assertTrue(result['migrated'])
            mock_create.assert_called_once_with(
                mock_engine, tables=[get_table()], checkfirst=True)
            mock_set_version.assert_called_once_with(
                mock_engine, table_name, version)
            mock_engine.begin.assert_not_called()

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_up_to_date_schema_is_noop(
            self, mock_get_version, mock_set_version):
        """Already at the target: no DDL, no version write, no migrate.

        Each table is asked about at *its own* target version rather
        than at 1, so this keeps testing the no-op branch as the
        versions diverge.
        """
        for table_name, _, ensure, version in CAPACITY_TABLES:
            mock_get_version.reset_mock()
            mock_get_version.return_value = version
            mock_set_version.reset_mock()
            mock_engine = mock.MagicMock()

            with mock.patch('sqlalchemy.MetaData.create_all') as mock_create:
                result = ensure(mock_engine)

            self.assertEqual(result['table'], table_name)
            self.assertEqual(result['start_version'], version)
            self.assertEqual(result['end_version'], version)
            self.assertFalse(result['migrated'])
            mock_create.assert_not_called()
            mock_set_version.assert_not_called()
            mock_engine.begin.assert_not_called()
            conn = mock_engine.connect.return_value.__enter__.return_value
            conn.execute.assert_not_called()


class NamespaceClaimsPeakMigrationTestCase(base.ShakenFistTestCase):
    """The v1-to-2 high-water mark migration (claim sizing phase 2b, D2).

    The DDL itself runs against a real server in
    test_mariadb_capacity_claims_live.py, which is also where the
    idempotence of a re-run is proven. What is checked here is the
    branching and the existence guard: which statements a v1 database
    gets, and that a database which already has the columns gets none.
    """

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_version_one_gains_the_peak_columns(
            self, mock_get_version, mock_set_version):
        mock_engine = mock.MagicMock()
        conn = mock_engine.begin.return_value.__enter__.return_value

        with mock.patch('sqlalchemy.MetaData.create_all') as mock_create, \
                mock.patch('shakenfist.mariadb.get_table_columns',
                           return_value={'uuid': {}, 'used_cpus': {}}):
            result = mariadb._ensure_namespace_claims_schema(mock_engine)

        self.assertEqual(1, result['start_version'])
        self.assertEqual(2, result['end_version'])
        self.assertTrue(result['migrated'])
        mock_create.assert_not_called()
        mock_set_version.assert_called_once_with(
            mock_engine, 'namespace_claims', 2)

        issued = [str(call.args[0]) for call in conn.execute.call_args_list]
        self.assertEqual(3, len(issued), issued)
        for name in mariadb.NAMESPACE_CLAIMS_PEAK_COLUMNS:
            # The type comes from the table definition rather than from
            # a string in the migration, so the DDL cannot drift from
            # the column it adds -- and the mark must not be nullable,
            # because every reader of it does arithmetic.
            self.assertIn(
                f'ALTER TABLE namespace_claims ADD COLUMN {name} BIGINT '
                f'NOT NULL DEFAULT 0', issued)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_a_rerun_over_existing_columns_issues_no_ddl(
            self, mock_get_version, mock_set_version):
        # A database at v1 whose columns are already there -- the shape
        # an interrupted migration, or a hand-patched schema, leaves
        # behind. The version is still advanced; no ALTER is issued.
        mock_engine = mock.MagicMock()
        conn = mock_engine.begin.return_value.__enter__.return_value
        present = {name: {} for name in mariadb.NAMESPACE_CLAIMS_PEAK_COLUMNS}

        with mock.patch('shakenfist.mariadb.get_table_columns',
                        return_value=present):
            result = mariadb._ensure_namespace_claims_schema(mock_engine)

        self.assertTrue(result['migrated'])
        self.assertEqual(2, result['end_version'])
        conn.execute.assert_not_called()
        mock_set_version.assert_called_once_with(
            mock_engine, 'namespace_claims', 2)

    def test_the_migration_does_not_backfill_from_used(self):
        # A high-water mark is a measurement. Seeding one from the
        # counter as it happens to read at upgrade time would publish a
        # peak nothing observed, so the columns default to zero and are
        # raised from below by the next admission or reconcile pass.
        source = inspect.getsource(
            mariadb._add_missing_namespace_claims_columns)
        self.assertNotIn('UPDATE', source)
        self.assertNotIn('used_cpus', source)
