# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the agent operation deadline migration.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which matches every test_mariadb_*_live.py module by regex; developers
can point at a local instance).

The rest of the unit suite mocks MariaDB completely, so nothing else in
the repository can tell the difference between a migration which works
and one which raises. That matters more than usual
here because the agent_operations and agent_operation_attributes tables
had never been migrated before: both ensure functions consisted of a
create-if-absent block and nothing else, so there was no established
ladder to add a rung to.

Each test rewinds a current database to what a pre-upgrade deployment
looks like (drop the four columns, reset both table versions to 2),
puts rows in it, and then verifies that reconciliation brings it
forward without losing them.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment.
"""

import os
import unittest
from unittest import mock
from uuid import UUID
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.exc import DatabaseError

from shakenfist import mariadb
from shakenfist.schema.agentoperation_attributes import (
    AgentOperationAttributesData)
from shakenfist.schema.agentoperation_data import AgentOperationData
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

TEST_TABLES = [
    'agent_operations',
    'agent_operation_attributes',
    'schema_versions',
]

# The columns this migration adds, by the table they belong to. The
# attributes table gained last_progress and attempts at v3 and
# expiry_reason at v4; one ALTER block serves both steps, so a full
# rewind to v2 covers all three.
NEW_COLUMNS = {
    'agent_operations': ['deadline', 'progress_timeout'],
    'agent_operation_attributes': ['last_progress', 'attempts',
                                   'expiry_reason'],
}


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class AgentOperationMigrationLiveTestCase(base.ShakenFistTestCase):
    """Prove the phase 2 migration migrates, against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)

        # Start from a clean slate: a previous run's tables would
        # otherwise be at the current version with rows in them.
        self._drop_tables()

        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_agent_operations_schema(self.engine)
        mariadb._ensure_agent_operation_attributes_schema(self.engine)

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _columns(self, table_name):
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    'SELECT COLUMN_NAME FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA = DATABASE() '
                    'AND TABLE_NAME = :table_name'),
                {'table_name': table_name}).fetchall()
        return {r[0] for r in rows}

    def _column_is_nullable(self, table_name, column_name):
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    'SELECT IS_NULLABLE FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA = DATABASE() '
                    'AND TABLE_NAME = :table_name '
                    'AND COLUMN_NAME = :column_name'),
                {'table_name': table_name,
                 'column_name': column_name}).first()
        self.assertIsNotNone(row)
        return row[0] == 'YES'

    def _rewind(self):
        """Make the database look like a pre-phase-2 deployment."""
        with self.engine.connect() as conn:
            for table_name, columns in NEW_COLUMNS.items():
                for column in columns:
                    conn.execute(sa.text(
                        f'ALTER TABLE {table_name} DROP COLUMN {column}'))
            conn.commit()
        mariadb._set_table_version(self.engine, 'agent_operations', 2)
        mariadb._set_table_version(
            self.engine, 'agent_operation_attributes', 2)

        for table_name, columns in NEW_COLUMNS.items():
            present = self._columns(table_name)
            for column in columns:
                self.assertNotIn(column, present)

    def _insert_legacy_rows(self, aop_uuid):
        """Insert rows using only the columns a pre-phase-2 build knew.

        Named explicitly rather than via the cached Table objects,
        which already carry the new columns.

        The uuid columns are the undashed 32 character form -- see the
        two-uuid-formats note in CLAUDE.md -- so raw SQL has to pass
        .hex rather than str(). Comparing the dashed form here does not
        error, it silently matches nothing.
        """
        with self.engine.connect() as conn:
            conn.execute(
                sa.text(
                    'INSERT INTO agent_operations '
                    '(uuid, namespace, instance_uuid, commands, version) '
                    'VALUES (:uuid, :namespace, :instance_uuid, '
                    ':commands, :version)'),
                {'uuid': aop_uuid.hex, 'namespace': 'unittest',
                 'instance_uuid': uuid4().hex, 'commands': '[]',
                 'version': 3})
            conn.execute(
                sa.text(
                    'INSERT INTO agent_operation_attributes '
                    '(uuid, results) VALUES (:uuid, :results)'),
                {'uuid': aop_uuid.hex, 'results': '{"0": {"status": 0}}'})
            conn.commit()

    def _migrate(self):
        return (
            mariadb._ensure_agent_operations_schema(self.engine),
            mariadb._ensure_agent_operation_attributes_schema(self.engine))

    def test_migration_adds_every_column(self):
        self._rewind()
        ops, attrs = self._migrate()

        self.assertTrue(ops['migrated'])
        self.assertTrue(attrs['migrated'])
        self.assertEqual(mariadb.AGENT_OPERATIONS_VERSION,
                         ops['end_version'])
        self.assertEqual(mariadb.AGENT_OPERATION_ATTRIBUTES_VERSION,
                         attrs['end_version'])

        for table_name, columns in NEW_COLUMNS.items():
            present = self._columns(table_name)
            for column in columns:
                self.assertIn(column, present)

    def test_migration_preserves_existing_rows(self):
        aop_uuid = uuid4()
        self._rewind()
        self._insert_legacy_rows(aop_uuid)
        self._migrate()

        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text('SELECT namespace, deadline, progress_timeout '
                        'FROM agent_operations WHERE uuid = :uuid'),
                {'uuid': aop_uuid.hex}).first()
        self.assertIsNotNone(row)
        self.assertEqual('unittest', row[0])
        # NULL is the right answer, and it means "no client intent was
        # recorded, so the server default applies" -- not "no deadline".
        self.assertIsNone(row[1])
        self.assertIsNone(row[2])

        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text('SELECT results, last_progress, attempts '
                        'FROM agent_operation_attributes '
                        'WHERE uuid = :uuid'),
                {'uuid': aop_uuid.hex}).first()
        self.assertIsNotNone(row)
        self.assertIsNone(row[1])
        # attempts backfills to zero rather than to NULL or to
        # something arbitrary. Note what this does *not* prove:
        # deleting the ALTER's explicit DEFAULT 0 leaves this passing,
        # because MariaDB fills a new NOT NULL numeric column with its
        # implicit type default. The DEFAULT is there to make that
        # deterministic across sql_mode settings, not to rescue an
        # ALTER which would otherwise fail -- verified by mutation
        # rather than assumed.
        self.assertEqual(0, row[2])

    def test_migration_preserves_nullability(self):
        self._rewind()
        self._migrate()

        self.assertTrue(
            self._column_is_nullable('agent_operations', 'deadline'))
        self.assertTrue(
            self._column_is_nullable('agent_operations', 'progress_timeout'))
        self.assertTrue(
            self._column_is_nullable(
                'agent_operation_attributes', 'last_progress'))
        self.assertTrue(
            self._column_is_nullable(
                'agent_operation_attributes', 'expiry_reason'))
        # attempts is the one column with no "unknown" state, so a
        # reader never has to write "attempts or 0".
        self.assertFalse(
            self._column_is_nullable(
                'agent_operation_attributes', 'attempts'))

    def test_migration_from_v3_adds_only_expiry_reason(self):
        # The upgrade path a real deployment already at v3 takes: the
        # two v3 ALTERs re-run as IF NOT EXISTS no-ops and only
        # expiry_reason is new.
        aop_uuid = uuid4()
        self._rewind()
        self._migrate()
        self._insert_legacy_rows(aop_uuid)

        with self.engine.connect() as conn:
            conn.execute(sa.text(
                'ALTER TABLE agent_operation_attributes '
                'DROP COLUMN expiry_reason'))
            conn.commit()
        mariadb._set_table_version(
            self.engine, 'agent_operation_attributes', 3)

        _, attrs = self._migrate()
        self.assertTrue(attrs['migrated'])
        self.assertEqual(mariadb.AGENT_OPERATION_ATTRIBUTES_VERSION,
                         attrs['end_version'])
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text('SELECT results, expiry_reason '
                        'FROM agent_operation_attributes '
                        'WHERE uuid = :uuid'),
                {'uuid': aop_uuid.hex}).first()
        self.assertIsNotNone(row)
        self.assertIsNone(row[1])

    def test_migration_is_idempotent(self):
        self._rewind()
        self._migrate()

        ops, attrs = self._migrate()
        self.assertFalse(ops['migrated'])
        self.assertFalse(attrs['migrated'])
        self.assertEqual(mariadb.AGENT_OPERATIONS_VERSION,
                         ops['end_version'])
        self.assertEqual(mariadb.AGENT_OPERATION_ATTRIBUTES_VERSION,
                         attrs['end_version'])

        for table_name, columns in NEW_COLUMNS.items():
            present = self._columns(table_name)
            for column in columns:
                self.assertIn(column, present)

    def test_migration_runs_on_a_freshly_created_schema(self):
        # A greenfield deployment gets the columns from create_all()
        # and must not then try to add them again. This is the case
        # the IF NOT EXISTS guard covers.
        ops, attrs = self._migrate()
        self.assertFalse(ops['migrated'])
        self.assertFalse(attrs['migrated'])
        for table_name, columns in NEW_COLUMNS.items():
            present = self._columns(table_name)
            for column in columns:
                self.assertIn(column, present)

        # Nullability has to be asserted on this path as well as the
        # migrated one. The two are produced by different code --
        # create_all() from the pydantic model here, the hand written
        # ALTER there -- so test_migration_preserves_nullability says
        # nothing about a fresh install, and a later change making
        # attempts Optional would silently diverge the two.
        self.assertTrue(
            self._column_is_nullable('agent_operations', 'deadline'))
        self.assertTrue(
            self._column_is_nullable('agent_operations', 'progress_timeout'))
        self.assertTrue(
            self._column_is_nullable(
                'agent_operation_attributes', 'last_progress'))
        self.assertTrue(
            self._column_is_nullable(
                'agent_operation_attributes', 'expiry_reason'))
        self.assertFalse(
            self._column_is_nullable(
                'agent_operation_attributes', 'attempts'))

    def test_failed_migration_does_not_advance_the_version(self):
        # A migration which cannot add its columns must not record
        # itself as done. verify_schema_versions() compares versions
        # rather than columns, so a version written past a column that
        # was never added yields a schema which reports itself healthy
        # while every insert fails on an unknown column -- and which
        # re-running ensure-mariadb-schema can never repair, because
        # the `current_ver < VERSION` guard is false from then on.
        #
        # The failure is induced by removing the table out from under a
        # version row that still claims 2. Any real ALTER failure --
        # metadata lock timeout on a busy table, missing privilege,
        # disk full -- arrives at the same place. Asserted against
        # DatabaseError rather than a leaf class because the induced
        # failure surfaces as ProgrammingError (unknown table) while a
        # lock timeout would be OperationalError; what the test is
        # about is the version, not which leaf was raised.
        self._rewind()
        with self.engine.connect() as conn:
            for table_name in NEW_COLUMNS:
                conn.execute(sa.text(f'DROP TABLE {table_name}'))
            conn.commit()

        self.assertRaises(
            DatabaseError,
            mariadb._ensure_agent_operations_schema, self.engine)
        self.assertRaises(
            DatabaseError,
            mariadb._ensure_agent_operation_attributes_schema, self.engine)

        for table_name in NEW_COLUMNS:
            self.assertEqual(
                2, mariadb._get_table_version(self.engine, table_name))


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class AgentOperationDirectAccessLiveTestCase(base.ShakenFistTestCase):
    """The direct MariaDB path, which only runs on database-tier nodes.

    Every other daemon reaches these rows over gRPC, so the direct
    functions are the half of the three-layer stack that a mocked unit
    test is least likely to catch a dropped column in.
    """

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_agent_operations_schema(self.engine)
        mariadb._ensure_agent_operation_attributes_schema(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _round_trip(self, deadline, progress_timeout):
        aop_uuid = uuid4()
        self.assertTrue(mariadb._direct_create_agent_operation(
            AgentOperationData(
                uuid=aop_uuid,
                namespace='unittest',
                instance_uuid=uuid4(),
                commands=[{'command': 'execute'}],
                deadline=deadline,
                progress_timeout=progress_timeout,
                version=3)))
        return mariadb._direct_get_agent_operation(aop_uuid)

    def test_static_values_round_trip(self):
        out = self._round_trip(1787427490.5, 30.0)
        self.assertEqual(1787427490.5, out.deadline)
        self.assertEqual(30.0, out.progress_timeout)

    def test_static_none_is_stored_as_null(self):
        out = self._round_trip(None, None)
        self.assertIsNone(out.deadline)
        self.assertIsNone(out.progress_timeout)

    def test_static_zero_is_distinct_from_null(self):
        # The whole point of the nullable columns: a client which
        # explicitly asked for no deadline must not be stored the same
        # way as a client which said nothing.
        out = self._round_trip(0.0, 0.0)
        self.assertIsNotNone(out.deadline)
        self.assertEqual(0.0, out.deadline)
        self.assertEqual(0.0, out.progress_timeout)

    def test_attributes_round_trip_and_mask(self):
        aop_uuid = uuid4()
        self.assertTrue(mariadb._direct_create_agent_operation_attributes(
            AgentOperationAttributesData(
                uuid=aop_uuid, results={'0': {'status': 0}})))

        out = mariadb._direct_get_agent_operation_attributes(aop_uuid)
        self.assertIsNone(out.last_progress)
        self.assertEqual(0, out.attempts)
        self.assertIsNone(out.expiry_reason)

        # Write only last_progress. A masked update must leave the
        # other columns exactly as they were, which is what stops a
        # progress writer clobbering a concurrent results writer.
        out.last_progress = 1787427490.5
        out.attempts = 7
        out.results = {}
        out.expiry_reason = 'deadline'
        self.assertTrue(mariadb._direct_update_agent_operation_attributes(
            out, fields=['last_progress']))

        reread = mariadb._direct_get_agent_operation_attributes(aop_uuid)
        self.assertEqual(1787427490.5, reread.last_progress)
        self.assertEqual(0, reread.attempts)
        self.assertIsNone(reread.expiry_reason)
        self.assertEqual({'0': {'status': 0}}, reread.results)

        # And the expiry_reason mask expire() uses writes only that.
        self.assertTrue(mariadb._direct_update_agent_operation_attributes(
            out, fields=['expiry_reason']))
        reread = mariadb._direct_get_agent_operation_attributes(aop_uuid)
        self.assertEqual('deadline', reread.expiry_reason)
        self.assertEqual(0, reread.attempts)

    def test_unknown_uuid_returns_none(self):
        self.assertIsNone(mariadb._direct_get_agent_operation(
            UUID('aaaabbbb-0000-4000-8000-00000000dead')))
