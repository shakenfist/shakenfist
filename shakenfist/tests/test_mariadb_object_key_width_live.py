# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB test for widening the object key columns.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module; developers can point at a
local instance).

They reproduce issue 4856 against a real server. A namespace's object
key is its name, names are published as legal up to 63 characters, and
the key columns were VARCHAR(36) -- so creating a namespace with a 43
character name (which is what a CI test did) failed the state write with
error 1406, answered a 500, and left a namespace row with no state. The
tests narrow the columns back to a uuid's width, prove the write fails
there, run the migrations, and prove it succeeds.

One of them also asserts the property the migration's design rests on:
that this widening is metadata-only on the oldest MariaDB SF supports.
VARCHAR(36) to VARCHAR(63) in utf8mb4 is 144 to 252 bytes, both under
the 256 byte boundary where the stored length prefix grows from one byte
to two, so ALGORITHM=INSTANT is accepted even on a column inside a
primary key and a secondary index. Had the ceiling been 255 (1020
bytes), MariaDB would refuse INSTANT and INPLACE both and rebuild every
row of these tables under a metadata lock during a deploy.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment.
"""

import os
import time
import unittest

import sqlalchemy as sa

from shakenfist import constants
from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

# The ensure function for each table carrying an object key column, the
# column itself, and the schema version the widening step upgrades from.
KEY_COLUMNS = [
    ('object_states', 'object_uuid',
     mariadb._ensure_object_states_schema, 3),
    ('object_metadata', 'object_uuid',
     mariadb._ensure_object_metadata_schema, 3),
    ('event_objects', 'object_uuid',
     mariadb._ensure_event_objects_schema, 2),
    ('cluster_operation_targets', 'target_uuid',
     mariadb._ensure_cluster_operation_targets_schema, 3),
]

TEST_TABLES = [
    'object_states',
    'object_metadata',
    'event_objects',
    'events',
    'cluster_operation_targets',
    'schema_versions',
]

# 43 characters: the length the CI namespace test built, and so the
# length which first showed this.
LONG_NAMESPACE_NAME = 'ci-namespace-test-errored-abcdef12345678901'


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class ObjectKeyWidthLiveTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(43, len(LONG_NAMESPACE_NAME))
        self.assertLessEqual(
            len(LONG_NAMESPACE_NAME), constants.NAMESPACE_NAME_MAX_LENGTH)

        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self._drop_tables)
        self.addCleanup(self.engine.dispose)

        self._drop_tables()
        mariadb._ensure_schema_versions_table(self.engine)
        for _table, _column, ensure, _from_version in KEY_COLUMNS:
            ensure(self.engine)

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _column_width(self, table_name, column_name):
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    'SELECT CHARACTER_MAXIMUM_LENGTH FROM '
                    'information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA = DATABASE() '
                    'AND TABLE_NAME = :table_name '
                    'AND COLUMN_NAME = :column_name'),
                {'table_name': table_name, 'column_name': column_name}
            ).first()
        self.assertIsNotNone(
            row, f'{table_name}.{column_name} is not in the database')
        return int(row[0])

    def _narrow_to_a_uuid(self):
        """Make the database look like one from before this migration."""
        with self.engine.connect() as conn:
            for table_name, column_name, _ensure, from_version in KEY_COLUMNS:
                conn.execute(sa.text(
                    f'ALTER TABLE {table_name} MODIFY COLUMN {column_name} '
                    f'VARCHAR(36) NOT NULL'))
                mariadb._set_table_version(
                    self.engine, table_name, from_version)
            conn.commit()

    def test_the_migration_widens_every_key_column(self):
        self._narrow_to_a_uuid()
        for table_name, column_name, _ensure, _from in KEY_COLUMNS:
            self.assertEqual(36, self._column_width(table_name, column_name))

        for table_name, column_name, ensure, _from in KEY_COLUMNS:
            result = ensure(self.engine)
            self.assertTrue(
                result['migrated'],
                f'{table_name} reported no migration')
            self.assertEqual(
                constants.OBJECT_KEY_MAX_LENGTH,
                self._column_width(table_name, column_name))

        # And it is idempotent, at both levels: the ensure function
        # reports no migration, and the widening itself declines to
        # re-ALTER a column which is already wide enough.
        for table_name, column_name, ensure, _from in KEY_COLUMNS:
            self.assertFalse(
                ensure(self.engine)['migrated'],
                f'{table_name} migrated twice')
            self.assertFalse(
                mariadb._widen_object_key_column(
                    self.engine, table_name, column_name),
                f'{table_name}.{column_name} was widened again')

    def test_a_long_namespace_state_write_fails_then_succeeds(self):
        """The incident's exact write, before and after the migration."""
        self._narrow_to_a_uuid()
        table = mariadb._get_object_states_table()

        with self.engine.connect() as conn:
            # Error 1406, "Data too long for column 'object_uuid'" --
            # which Namespace.new() hit after writing the namespaces
            # row, so the create answered 500 and left that row stateless.
            self.assertRaises(
                sa.exc.DBAPIError,
                conn.execute,
                table.insert().values(
                    object_uuid=LONG_NAMESPACE_NAME,
                    object_type=ObjectType.NAMESPACE,
                    state_value='created',
                    update_time=time.time(),
                    message=None))

        mariadb._ensure_object_states_schema(self.engine)

        with self.engine.connect() as conn:
            conn.execute(table.insert().values(
                object_uuid=LONG_NAMESPACE_NAME,
                object_type=ObjectType.NAMESPACE,
                state_value='created',
                update_time=time.time(),
                message=None))
            conn.commit()

            stored = conn.execute(
                sa.select(table.c.state_value).where(
                    table.c.object_uuid == LONG_NAMESPACE_NAME)).scalar()
        self.assertEqual('created', stored)

    def test_the_widening_is_metadata_only(self):
        """ALGORITHM=INSTANT is accepted, which is why the ceiling is 63.

        The migration asks for INSTANT and falls back to the server's
        choice, so a regression here would not fail a deploy -- it would
        quietly rebuild these tables under a lock. This is the test that
        notices.
        """
        self._narrow_to_a_uuid()
        with self.engine.connect() as conn:
            for table_name, column_name, _ensure, _from in KEY_COLUMNS:
                conn.execute(sa.text(
                    f'ALTER TABLE {table_name} MODIFY COLUMN {column_name} '
                    f'VARCHAR({constants.OBJECT_KEY_MAX_LENGTH}) NOT NULL, '
                    f'ALGORITHM=INSTANT'))
            conn.commit()

        for table_name, column_name, _ensure, _from in KEY_COLUMNS:
            self.assertEqual(
                constants.OBJECT_KEY_MAX_LENGTH,
                self._column_width(table_name, column_name))
