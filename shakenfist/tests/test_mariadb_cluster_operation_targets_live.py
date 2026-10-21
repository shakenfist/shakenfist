# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the cluster_operation_targets v3 migration.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which matches every test_mariadb_*_live.py module by regex; developers
can point at a local instance).

v3 adds the observational flag (see
docs/plans/PLAN-cluster-op-visibility.md) and widens idx_cot_target to
end in it. Both halves need a real server to check. The rest of the
unit suite runs against mocks or sqlite, which cannot say whether the
ALTER is valid MariaDB, what default an existing row is given, or
which index the optimizer picks. The ALTER also leans on MariaDB's IF
[NOT] EXISTS extensions, whose semantics within one statement are not
what they look like (see the comment on the v3 block).

Each migration test rewinds a current database to what a v2 deployment
looks like (drop the column, restore the two-column index, reset the
version to 2), puts rows in it, and then verifies that reconciliation
brings it forward without losing them.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment.
"""

import os
import time
import unittest
from unittest import mock
from uuid import uuid4

import sqlalchemy as sa
from testtools.content import text_content

from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

TABLE = 'cluster_operation_targets'

TEST_TABLES = [
    TABLE,
    'object_states',
    'schema_versions',
]

V3_INDEX_COLUMNS = ['target_object_type', 'target_uuid', 'observational']


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class ClusterOperationTargetsMigrationLiveTestCase(base.ShakenFistTestCase):
    """Prove the v3 migration migrates, against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)

        # Start from a clean slate: a previous run's tables would
        # otherwise be at the current version with rows in them.
        self._drop_tables()

        # Like the other live modules, this drives the per-table ensure
        # functions rather than ensure_schema(), which would create (and
        # leave for cleanup) every table in the schema. The cached Table
        # objects are safe to reuse across tests: they describe the
        # current schema, and nothing here needs an older shape of them.
        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_object_states_schema(self.engine)
        mariadb._ensure_cluster_operation_targets_schema(self.engine)

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _migrate(self):
        return mariadb._ensure_cluster_operation_targets_schema(self.engine)

    def _observational_column(self):
        with self.engine.connect() as conn:
            return conn.execute(
                sa.text(
                    'SELECT IS_NULLABLE, COLUMN_DEFAULT, DATA_TYPE '
                    'FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA = DATABASE() '
                    'AND TABLE_NAME = :table_name '
                    "AND COLUMN_NAME = 'observational'"),
                {'table_name': TABLE}).first()

    def _index_columns(self, index_name):
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    'SELECT COLUMN_NAME FROM information_schema.STATISTICS '
                    'WHERE TABLE_SCHEMA = DATABASE() '
                    'AND TABLE_NAME = :table_name '
                    'AND INDEX_NAME = :index_name '
                    'ORDER BY SEQ_IN_INDEX'),
                {'table_name': TABLE, 'index_name': index_name}).fetchall()
        return [r[0] for r in rows]

    def _rewind(self):
        """Make the database look like a v2 deployment."""
        # IF EXISTS so the rewind works from whatever state setUp's
        # fresh install left, including a broken migration which drops
        # the index; the assertions after the migration then report it.
        with self.engine.connect() as conn:
            conn.execute(sa.text(
                f'ALTER TABLE {TABLE} '
                f'DROP INDEX IF EXISTS idx_cot_target, '
                f'DROP COLUMN observational, '
                f'ADD INDEX idx_cot_target (target_object_type, target_uuid)'))
            conn.commit()
        mariadb._set_table_version(self.engine, TABLE, 2)

        self.assertIsNone(self._observational_column())
        self.assertEqual(['target_object_type', 'target_uuid'],
                         self._index_columns('idx_cot_target'))

    def _insert_v2_row(self, op_uuid, target_uuid):
        """Insert a row using only the columns a v2 build knew.

        Named explicitly rather than via the cached Table object, which
        already carries observational. This is both the shape of a row
        written before the upgrade and of one an older daemon writes
        during a rolling upgrade.
        """
        with self.engine.connect() as conn:
            conn.execute(
                sa.text(
                    f'INSERT INTO {TABLE} '
                    '(operation_uuid, operation_type, target_object_type, '
                    'target_uuid, created_at) '
                    'VALUES (:op, :op_type, :type, :target, :created)'),
                {'op': op_uuid, 'op_type': 'node_inst_op',
                 'type': ObjectType.INSTANCE.name, 'target': target_uuid,
                 'created': time.time()})
            conn.commit()

    def _observational_value(self, op_uuid):
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text(f'SELECT observational FROM {TABLE} '
                        'WHERE operation_uuid = :op'),
                {'op': op_uuid}).first()
        self.assertIsNotNone(row)
        return row[0]

    def _assert_v3_shape(self):
        column = self._observational_column()
        self.assertIsNotNone(column)
        self.assertEqual('NO', column[0])
        # information_schema reports the default as text. A column added
        # without DEFAULT 0 reports NULL here even though MariaDB still
        # backfills existing rows with the type's implicit zero, which
        # is why this is asserted separately from the backfilled value.
        self.assertEqual('0', column[1])
        self.assertEqual('tinyint', column[2])
        self.assertEqual(V3_INDEX_COLUMNS, self._index_columns('idx_cot_target'))

    def _assert_at_current_version(self, result):
        # The constant has to have moved as well as the version row: a
        # gateway compares the two in verify_schema_versions().
        self.assertGreaterEqual(mariadb.CLUSTER_OPERATION_TARGETS_VERSION, 3)
        self.assertEqual(
            mariadb.CLUSTER_OPERATION_TARGETS_VERSION,
            mariadb.EXPECTED_SCHEMA_VERSIONS[TABLE])
        self.assertEqual(mariadb.CLUSTER_OPERATION_TARGETS_VERSION,
                         result['end_version'])
        self.assertEqual(mariadb.CLUSTER_OPERATION_TARGETS_VERSION,
                         mariadb._get_table_version(self.engine, TABLE))

    def test_migration_from_v2(self):
        legacy_op = str(uuid4())
        self._rewind()
        self._insert_v2_row(legacy_op, str(uuid4()))

        result = self._migrate()

        self.assertTrue(result['migrated'])
        self.assertEqual(2, result['start_version'])
        self._assert_at_current_version(result)
        self._assert_v3_shape()

        # The pre-upgrade row is mutating, which is how it was treated
        # before the column existed.
        self.assertEqual(0, self._observational_value(legacy_op))

        # So is a row from an older daemon which writes after the
        # upgrade without naming the column. Without the DEFAULT this
        # INSERT is rejected outright in strict mode.
        late_op = str(uuid4())
        self._insert_v2_row(late_op, str(uuid4()))
        self.assertEqual(0, self._observational_value(late_op))

    def test_migration_is_idempotent(self):
        self._rewind()
        self._migrate()

        result = self._migrate()
        self.assertFalse(result['migrated'])
        self._assert_at_current_version(result)
        self._assert_v3_shape()

    def test_v3_block_is_restartable(self):
        # A crash after the ALTER but before _set_table_version leaves
        # the v3 shape with a version row that still says 2. Re-running
        # must leave the three-column index in place. (With IF NOT
        # EXISTS on the ADD, every run against a table which has the
        # index drops it and skips the add; the other tests catch that,
        # this one passes against it by alternation.)
        self._rewind()
        self._migrate()
        mariadb._set_table_version(self.engine, TABLE, 2)

        result = self._migrate()
        self.assertTrue(result['migrated'])
        self._assert_at_current_version(result)
        self._assert_v3_shape()

    def test_fresh_install(self):
        # A greenfield deployment creates the table from the current
        # definition at v1 and then passes through every upgrade block.
        # The v2 block's ADD CONSTRAINT meets an existing constraint
        # and the v3 block meets an existing column and index; neither
        # may fail or leave the schema in a different shape.
        with self.engine.connect() as conn:
            conn.execute(sa.text(f'DROP TABLE {TABLE}'))
            conn.execute(sa.text(
                'DELETE FROM schema_versions WHERE table_name = :t'),
                {'t': TABLE})
            conn.commit()

        result = self._migrate()
        self.assertTrue(result['migrated'])
        self.assertEqual(0, result['start_version'])
        self._assert_at_current_version(result)
        # create_all() renders the column from the Table definition, not
        # from the hand written ALTER, so the default and the index are
        # asserted on this path too.
        self._assert_v3_shape()
        self.assertEqual(
            ['operation_uuid', 'target_object_type', 'target_uuid'],
            self._index_columns('uq_cot_op_target'))
        self.assertEqual(['operation_uuid'],
                         self._index_columns('idx_cot_operation'))

        op = str(uuid4())
        self._insert_v2_row(op, str(uuid4()))
        self.assertEqual(0, self._observational_value(op))

    def test_migration_time_on_a_seeded_table(self):
        # Informational: the reviewer wants to know how long the ALTER
        # holds the table on a deployment. Production holds in-flight
        # ops plus about thirty seconds of terminal ones, so 50,000 rows
        # is well beyond a real table. The bound is a sanity check, not
        # a performance target.
        rows = 50_000
        self._rewind()
        now = time.time()
        batch = []
        with self.engine.connect() as conn:
            for i in range(rows):
                batch.append({
                    'op': str(uuid4()), 'op_type': 'node_inst_op',
                    'type': ObjectType.INSTANCE.name,
                    'target': f'00000000-0000-4000-8000-{i % 5000:012d}',
                    'created': now})
                if len(batch) == 5000:
                    conn.execute(
                        sa.text(
                            f'INSERT INTO {TABLE} '
                            '(operation_uuid, operation_type, '
                            'target_object_type, target_uuid, created_at) '
                            'VALUES (:op, :op_type, :type, :target, '
                            ':created)'),
                        batch)
                    batch = []
            conn.commit()

        start = time.monotonic()
        result = self._migrate()
        elapsed = time.monotonic() - start

        message = f'v2 -> v3 migration of {rows} rows took {elapsed:.3f}s'
        self.addDetail('migration-time', text_content(message))
        print(message)

        self.assertTrue(result['migrated'])
        self._assert_v3_shape()
        self.assertLess(elapsed, 60)


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class ClusterOperationTargetsIndexLiveTestCase(base.ShakenFistTestCase):
    """The filtered "current state" queries are served by idx_cot_target.

    EXPLAINs the statements the production readers actually send, so a
    change to either query that stops it using the index fails here.
    """

    TARGETS = 100
    OPS_PER_TARGET = 8

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_object_states_schema(self.engine)
        mariadb._ensure_cluster_operation_targets_schema(self.engine)

        self.table = mariadb._get_cluster_operation_targets_table()
        self.states_table = mariadb._get_object_states_table()
        self.target_uuids = [str(uuid4()) for _ in range(self.TARGETS)]
        self._seed()

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _seed(self):
        # A few hundred rows over many targets, a quarter of them
        # observational, each op with a state row in a mix of in-flight
        # and terminal states. That makes a single-target lookup
        # selective, which is what production looks like, so the
        # optimizer's choice is about the index rather than about a
        # table small enough to scan.
        states = ['queued', 'preflight', 'executing', 'complete', 'error']
        now = time.time()
        target_rows = []
        state_rows = []
        n = 0
        for target_uuid in self.target_uuids:
            for _ in range(self.OPS_PER_TARGET):
                op_uuid = str(uuid4())
                target_rows.append({
                    'operation_uuid': op_uuid,
                    'operation_type': 'node_inst_op',
                    'target_object_type': ObjectType.INSTANCE,
                    'target_uuid': target_uuid,
                    'created_at': now,
                    'observational': n % 4 == 0,
                })
                state_rows.append({
                    'object_uuid': op_uuid,
                    'object_type': ObjectType.NODE_INST_OP,
                    'state_value': states[n % len(states)],
                    'update_time': now,
                    'message': None,
                })
                n += 1
        with self.engine.connect() as conn:
            conn.execute(self.table.insert(), target_rows)
            conn.execute(self.states_table.insert(), state_rows)
            conn.commit()
            conn.exec_driver_sql(f'ANALYZE TABLE {TABLE}, object_states').fetchall()

    def _production_statement(self, fn, *args):
        """Run a production reader and capture the SQL it sends.

        The statement is taken from the cursor, after SQLAlchemy has
        compiled it for this dialect, so what is EXPLAINed below is
        exactly what the function issues -- not a copy of its query
        that could drift from it.
        """
        captured = []

        def _capture(conn, cursor, statement, parameters, context,
                     executemany):
            if TABLE in statement:
                captured.append((statement, parameters))

        sa.event.listen(self.engine, 'before_cursor_execute', _capture)
        try:
            with mock.patch('shakenfist.mariadb._get_engine',
                            return_value=self.engine):
                result = fn(*args)
        finally:
            sa.event.remove(self.engine, 'before_cursor_execute', _capture)
        self.assertEqual(1, len(captured), captured)
        statement, parameters = captured[0]
        return result, statement, parameters

    def _explain(self, statement, parameters):
        with self.engine.connect() as conn:
            rows = conn.exec_driver_sql(
                f'EXPLAIN {statement}', parameters).mappings().fetchall()
        plan = [dict(r) for r in rows]
        self.addDetail('explain', text_content(
            f'{statement}\n{parameters}\n\n{plan}'))
        return plan

    def _plan_for_targets_table(self, plan):
        found = [r for r in plan if r['table'] == TABLE]
        self.assertEqual(1, len(found), plan)
        return found[0]

    def _rows_for(self, target_uuid):
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(self.table).where(
                    self.table.c.target_uuid == target_uuid)
            ).fetchall()

    def test_get_latest_uses_idx_cot_target(self):
        target_uuid = self.target_uuids[7]
        latest, statement, parameters = self._production_statement(
            mariadb._direct_get_latest_cluster_operation_target,
            ObjectType.INSTANCE, target_uuid)

        # The statement really carries the observational predicate, and
        # gives the newest mutating row for the target.
        self.assertIn('observational', statement)
        mutating = [r for r in self._rows_for(target_uuid)
                    if not r.observational]
        self.assertIsNotNone(latest)
        self.assertFalse(latest.observational)
        self.assertEqual(
            max(r.sequence_number for r in mutating), latest.sequence_number)

        row = self._plan_for_targets_table(self._explain(statement, parameters))
        self.assertEqual('idx_cot_target', row['key'], row)
        # The ordering comes from the primary key carried in the index,
        # not from a sort.
        self.assertNotIn('filesort', row['Extra'] or '', row)

    def test_has_pending_uses_idx_cot_target(self):
        # A target whose only in-flight op is observational, beside a
        # mutating op which has completed, so the answer depends on the
        # predicate. has_pending fails closed (True) on a database error,
        # so the False also proves the statement ran.
        target_uuid = str(uuid4())
        now = time.time()
        with self.engine.connect() as conn:
            for observational, state in ((True, 'queued'),
                                         (False, 'complete')):
                op_uuid = str(uuid4())
                conn.execute(self.table.insert().values(
                    operation_uuid=op_uuid,
                    operation_type='node_inst_op',
                    target_object_type=ObjectType.INSTANCE,
                    target_uuid=target_uuid,
                    created_at=now,
                    observational=observational))
                conn.execute(self.states_table.insert().values(
                    object_uuid=op_uuid,
                    object_type=ObjectType.NODE_INST_OP,
                    state_value=state,
                    update_time=now,
                    message=None))
            conn.commit()

        pending, statement, parameters = self._production_statement(
            mariadb._direct_has_pending_cluster_operation_target,
            ObjectType.INSTANCE, target_uuid)
        self.assertIn('observational', statement)
        self.assertFalse(pending)

        row = self._plan_for_targets_table(self._explain(statement, parameters))
        self.assertEqual('idx_cot_target', row['key'], row)

    def test_pending_only_listing_uses_idx_cot_target(self):
        # One in-flight and one finished op, so the state join decides
        # what comes back.
        target_uuid = str(uuid4())
        now = time.time()
        wanted = None
        with self.engine.connect() as conn:
            for state in ('queued', 'complete'):
                op_uuid = str(uuid4())
                if state == 'queued':
                    wanted = op_uuid
                conn.execute(self.table.insert().values(
                    operation_uuid=op_uuid,
                    operation_type='node_inst_op',
                    target_object_type=ObjectType.INSTANCE,
                    target_uuid=target_uuid,
                    created_at=now,
                    observational=False))
                conn.execute(self.states_table.insert().values(
                    object_uuid=op_uuid,
                    object_type=ObjectType.NODE_INST_OP,
                    state_value=state,
                    update_time=now,
                    message=None))
            conn.commit()

        rows, statement, parameters = self._production_statement(
            mariadb._direct_get_cluster_operation_targets_for_object,
            ObjectType.INSTANCE, target_uuid, True)
        self.assertIn('object_states', statement)
        self.assertEqual([wanted], [r.operation_uuid for r in rows])

        row = self._plan_for_targets_table(self._explain(statement, parameters))
        self.assertEqual('idx_cot_target', row['key'], row)
