# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the agent operation table migrations.

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

Each test rewinds a current database to what some earlier deployment
looks like -- drop the columns a step added and reset the table
versions to what they were before it -- puts rows in it, and then
verifies that reconciliation brings it forward without losing them.
Two rewind points are covered: the one before the deadline columns,
and the one before the dependency edge columns.

The dependency tests also compare a migrated column against the same
column on a table created straight from create_all(), because a
migration which types a column differently from the table generator
leaves a migrated cluster and a fresh cluster with different schemas
and no version difference to notice it by.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment.
"""

import json
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
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

# Copies of the two tables, built from the same pydantic models and
# created by create_all(), so that a migrated column can be compared
# with the one a greenfield deployment gets. They sit in the same
# schema under a different name rather than in a second schema: the
# DSN names one database and the test user need not be able to create
# another, and every information_schema query here can keep its
# TABLE_SCHEMA = DATABASE() predicate instead of growing a parameter.
FRESH_TABLES = {
    'agent_operations': 'agent_operations_fresh',
    'agent_operation_attributes': 'agent_operation_attributes_fresh',
}

TEST_TABLES = [
    'agent_operations',
    'agent_operation_attributes',
    'schema_versions',
] + list(FRESH_TABLES.values())

# The columns this migration adds, by the table they belong to. The
# attributes table gained last_progress and attempts at v3 and
# expiry_reason at v4; one ALTER block serves both steps, so a full
# rewind to v2 covers all three.
NEW_COLUMNS = {
    'agent_operations': ['deadline', 'progress_timeout'],
    'agent_operation_attributes': ['last_progress', 'attempts',
                                   'expiry_reason'],
}

# The columns the dependency edge work adds, by the table they belong
# to. These are kept apart from NEW_COLUMNS because they have their own
# rewind point: a deployment from immediately before them has the
# deadline columns and sits at agent_operations v3 and
# agent_operation_attributes v4.
DEPENDENCY_COLUMNS = {
    'agent_operations': ['depends_on', 'runs_after'],
    'agent_operation_attributes': ['dependency_failure'],
}

PRE_DEPENDENCY_VERSIONS = {
    'agent_operations': 3,
    'agent_operation_attributes': 4,
}

# Written as literals rather than as mariadb's version constants. The
# version a migration records is the constant, so an assertion made
# against the constant holds for whatever value the constant has --
# including one which never moved past the pre-dependency version at
# all, which is exactly the regression worth catching here.
POST_DEPENDENCY_VERSIONS = {
    'agent_operations': 4,
    'agent_operation_attributes': 5,
}


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class AgentOperationMigrationLiveTestCase(base.ShakenFistTestCase):
    """Prove the two agent operation migrations migrate, against a real MariaDB."""

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

    def _column_facts(self, table_name, column_name):
        """Return the facts about a column which a migration can get wrong.

        COLLATION_NAME is in the list for a reason worth knowing before
        trusting this comparison: MariaDB's JSON type is an alias for
        LONGTEXT with a json_valid() check constraint, so a column
        added as JSON reports a DATA_TYPE and a COLUMN_TYPE of
        'longtext' exactly as a LONGTEXT column does. The one thing
        information_schema shows differently is the collation, which is
        utf8mb4_bin for JSON and the table default otherwise. A
        comparison on the types alone would not notice the divergence
        these tests exist to catch.

        COLUMN_DEFAULT is deliberately left out. The migration adds the
        edge lists with DEFAULT '[]' so that the value backfilled into
        existing rows does not depend on sql_mode, while
        pydantic_to_sqlalchemy_table has no server-default support at
        all, so the migrated and the fresh column legitimately differ
        there. What that default does is asserted by reading a
        pre-existing row back instead.
        """
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    'SELECT DATA_TYPE, COLUMN_TYPE, IS_NULLABLE, '
                    'CHARACTER_SET_NAME, COLLATION_NAME, '
                    'CHARACTER_MAXIMUM_LENGTH '
                    'FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA = DATABASE() '
                    'AND TABLE_NAME = :table_name '
                    'AND COLUMN_NAME = :column_name'),
                {'table_name': table_name,
                 'column_name': column_name}).first()
        self.assertIsNotNone(
            row, f'{table_name}.{column_name} does not exist')
        return dict(row._mapping)

    def _create_fresh_comparison_tables(self):
        """Create copies of both tables straight from create_all().

        This is the only way to tell what a greenfield deployment's
        columns look like without being a greenfield deployment, and
        the comparison is against it rather than against expected DDL
        written out here, which would just be a second place to make
        the same mistake.

        A separate MetaData, because the module level ones are cached
        against the real table names.
        """
        metadata = sa.MetaData()
        tables = [
            pydantic_to_sqlalchemy_table(
                AgentOperationData,
                FRESH_TABLES['agent_operations'],
                metadata,
                primary_key_fields=['uuid'],
                include_id_column=False),
            pydantic_to_sqlalchemy_table(
                AgentOperationAttributesData,
                FRESH_TABLES['agent_operation_attributes'],
                metadata,
                primary_key_fields=['uuid'],
                include_id_column=False),
        ]
        metadata.create_all(self.engine, tables=tables, checkfirst=True)

    def _rewind(self):
        """Make the database look like a deployment from before the deadline columns.

        Every later column goes too, not just the deadline ones: a v2
        deployment had none of them, and leaving one behind would also
        leave _insert_legacy_rows() naming a column set which no longer
        covers every NOT NULL column in the table, which a strict
        sql_mode refuses outright.
        """
        with self.engine.connect() as conn:
            for table_name, columns in NEW_COLUMNS.items():
                for column in columns + DEPENDENCY_COLUMNS[table_name]:
                    conn.execute(sa.text(
                        f'ALTER TABLE {table_name} DROP COLUMN {column}'))
            conn.commit()
        mariadb._set_table_version(self.engine, 'agent_operations', 2)
        mariadb._set_table_version(
            self.engine, 'agent_operation_attributes', 2)

        for table_name, columns in NEW_COLUMNS.items():
            present = self._columns(table_name)
            for column in columns + DEPENDENCY_COLUMNS[table_name]:
                self.assertNotIn(column, present)

    def _rewind_dependency_columns(self):
        """Make the database look like a deployment from before the edge columns.

        A shallower rewind than _rewind(): the deadline columns stay,
        because a deployment which is about to gain edges already has
        them, and the versions go back to 3 and 4 rather than to 2.
        """
        with self.engine.connect() as conn:
            for table_name, columns in DEPENDENCY_COLUMNS.items():
                for column in columns:
                    conn.execute(sa.text(
                        f'ALTER TABLE {table_name} DROP COLUMN {column}'))
            conn.commit()
        for table_name, version in PRE_DEPENDENCY_VERSIONS.items():
            mariadb._set_table_version(self.engine, table_name, version)

        for table_name, columns in DEPENDENCY_COLUMNS.items():
            present = self._columns(table_name)
            for column in columns:
                self.assertNotIn(column, present)

    def _insert_legacy_rows(self, aop_uuid):
        """Insert rows using only the columns a pre-deadline build knew.

        Named explicitly rather than via the cached Table objects,
        which already carry the new columns.

        The uuid columns are the undashed 32 character form, while
        object_states holds the dashed one, so raw SQL has to pass
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

    def _insert_pre_dependency_rows(self, aop_uuid):
        """Insert rows using only the columns a pre-dependency build knew.

        Not _insert_legacy_rows(), which names the columns a
        pre-deadline build knew. The deadline columns survive the
        shallower rewind, and attempts among them is NOT NULL with no
        server default on a freshly created table -- its DEFAULT 0
        lives in the ALTER, which is a deliberate divergence -- so an
        insert which omits it is refused outright under a strict
        sql_mode rather than silently defaulted.

        The uuid columns take the undashed 32 character form, as
        _insert_legacy_rows() explains.
        """
        with self.engine.connect() as conn:
            conn.execute(
                sa.text(
                    'INSERT INTO agent_operations '
                    '(uuid, namespace, instance_uuid, commands, deadline, '
                    'progress_timeout, version) '
                    'VALUES (:uuid, :namespace, :instance_uuid, :commands, '
                    ':deadline, :progress_timeout, :version)'),
                {'uuid': aop_uuid.hex, 'namespace': 'unittest',
                 'instance_uuid': uuid4().hex, 'commands': '[]',
                 'deadline': None, 'progress_timeout': None, 'version': 3})
            conn.execute(
                sa.text(
                    'INSERT INTO agent_operation_attributes '
                    '(uuid, results, last_progress, attempts, expiry_reason) '
                    'VALUES (:uuid, :results, :last_progress, :attempts, '
                    ':expiry_reason)'),
                {'uuid': aop_uuid.hex, 'results': '{"0": {"status": 0}}',
                 'last_progress': None, 'attempts': 0,
                 'expiry_reason': None})
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

    def test_dependency_migration_adds_every_column(self):
        self._rewind_dependency_columns()
        ops, attrs = self._migrate()

        self.assertTrue(ops['migrated'])
        self.assertTrue(attrs['migrated'])
        for table_name, version in POST_DEPENDENCY_VERSIONS.items():
            self.assertEqual(
                version,
                mariadb._get_table_version(self.engine, table_name),
                f'{table_name} did not reach version {version}')

        for table_name, columns in DEPENDENCY_COLUMNS.items():
            present = self._columns(table_name)
            for column in columns:
                self.assertIn(
                    column, present,
                    f'the migration did not add {table_name}.{column}')

    def test_dependency_columns_match_a_fresh_create(self):
        # The assertion the live test exists for. The unit suite mocks
        # MariaDB completely, so a migration which adds JSON where
        # create_all() adds LONGTEXT, or a nullable column where
        # create_all() makes it NOT NULL, is invisible everywhere else:
        # both databases report the same table version while holding
        # different schemas, and the difference only surfaces as an
        # insert which works on one cluster and fails on the other.
        self._rewind_dependency_columns()
        self._migrate()
        self._create_fresh_comparison_tables()

        for table_name, columns in DEPENDENCY_COLUMNS.items():
            for column in columns:
                self.assertEqual(
                    self._column_facts(FRESH_TABLES[table_name], column),
                    self._column_facts(table_name, column),
                    f'{table_name}.{column} on a migrated database is not '
                    f'what create_all() produces on a fresh one')

        # And in absolute terms as well, so that the equality above
        # cannot be satisfied by both paths being wrong together -- a
        # model field which stopped being Optional, say, would move the
        # fresh column with the migrated one.
        self.assertFalse(
            self._column_is_nullable('agent_operations', 'depends_on'))
        self.assertFalse(
            self._column_is_nullable('agent_operations', 'runs_after'))
        self.assertTrue(
            self._column_is_nullable(
                'agent_operation_attributes', 'dependency_failure'))

    def test_dependency_migration_backfills_existing_rows(self):
        aop_uuid = uuid4()
        self._rewind_dependency_columns()
        self._insert_pre_dependency_rows(aop_uuid)
        self._migrate()

        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text('SELECT namespace, depends_on, runs_after '
                        'FROM agent_operations WHERE uuid = :uuid'),
                {'uuid': aop_uuid.hex}).first()
        self.assertIsNotNone(row)
        self.assertEqual('unittest', row[0])
        for value in (row[1], row[2]):
            # The edge columns are NOT NULL, so what an existing row
            # gets is whatever the ALTER's DEFAULT says -- and '[]' is
            # the only answer the reader can parse. Asserted on the raw
            # string before it is parsed, so that a migration which
            # dropped the DEFAULT fails saying '' is not '[]' rather
            # than raising a JSON decode error from a helper.
            self.assertEqual('[]', value)
            self.assertEqual([], json.loads(value))

        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text('SELECT results, dependency_failure '
                        'FROM agent_operation_attributes '
                        'WHERE uuid = :uuid'),
                {'uuid': aop_uuid.hex}).first()
        self.assertIsNotNone(row)
        # NULL here, not '{}' and not 'null': no cascade reached this
        # operation, which is a different fact from a cascade which
        # recorded no detail.
        self.assertIsNone(row[1])

    def test_dependency_migration_is_a_clean_no_op_when_re_run(self):
        self._rewind_dependency_columns()
        self._migrate()

        ops, attrs = self._migrate()
        self.assertFalse(ops['migrated'])
        self.assertFalse(attrs['migrated'])
        for table_name, version in POST_DEPENDENCY_VERSIONS.items():
            self.assertEqual(
                version,
                mariadb._get_table_version(self.engine, table_name))

        for table_name, columns in DEPENDENCY_COLUMNS.items():
            present = self._columns(table_name)
            for column in columns:
                self.assertIn(column, present)


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

    def test_dependency_edges_round_trip_through_the_public_path(self):
        """Write edges with the public create and read them with the public get.

        Everything else in this class calls the _direct_ functions, but
        the edge lists are worth a trip through create_agent_operation()
        and get_agent_operation() because those are what the object
        layer calls, and the read consults the object cache before the
        database. A freshly generated uuid is a cache miss, and the
        entry is evicted anyway below, so what comes back is what
        MariaDB stored rather than the model that went in.
        """
        aop_uuid = uuid4()
        edges = [{'op_type': 'agentoperation',
                  'op_uuid': str(uuid4()),
                  'settle_seconds': 30.0}]
        self.assertTrue(mariadb.create_agent_operation(
            AgentOperationData(
                uuid=aop_uuid,
                namespace='unittest',
                instance_uuid=uuid4(),
                commands=[{'command': 'execute'}],
                depends_on=edges,
                runs_after=[],
                version=3)))
        mariadb._object_cache_evict('agentoperation', aop_uuid)

        out = mariadb.get_agent_operation(aop_uuid)
        self.assertIsNotNone(out)
        self.assertEqual(edges, out.depends_on)
        # An operation which named nothing comes back as an empty list
        # rather than as None, which is what lets a reader iterate it
        # without a guard.
        self.assertEqual([], out.runs_after)

    def test_unknown_uuid_returns_none(self):
        self.assertIsNone(mariadb._direct_get_agent_operation(
            UUID('aaaabbbb-0000-4000-8000-00000000dead')))
