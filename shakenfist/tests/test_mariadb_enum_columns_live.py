# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB test for native ENUM column widening.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh;
developers can point at a local instance). They simulate the failure
mode that broke the sfcbr cluster on 2026-07-28: a database created
before ObjectType.NAMESPACE_KEY existed rejected namespace key state
writes with "Data truncated for column 'object_type'" (error 1265),
because a MariaDB ENUM column freezes its value list at CREATE TABLE
time and no migration widened it.

The test creates the real schema, shrinks every native ENUM column back
one member (what a pre-upgrade database looks like), then runs the
reconciliation and proves both that the column definitions are restored
and that the incident's exact write -- a namespace_key state row -- now
succeeds.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment.
"""

import os
import time
import unittest
from uuid import uuid4

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

# Every table an enum-bearing ensure function touches, for cleanup.
TEST_TABLES = [
    'object_states',
    'object_metadata',
    'cluster_operation_targets',
    'ipam_reservations',
    'schema_versions',
]


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class NativeEnumWideningLiveTestCase(base.ShakenFistTestCase):
    """Prove ENUM widening works against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self._drop_tables)
        self.addCleanup(self.engine.dispose)

        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_object_states_schema(self.engine)
        mariadb._ensure_object_metadata_schema(self.engine)
        mariadb._ensure_cluster_operation_targets_schema(self.engine)
        mariadb._ensure_ipam_reservations_schema(self.engine)

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _column_enum_values(self, table_name, column_name):
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    'SELECT COLUMN_TYPE FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA = DATABASE() '
                    'AND TABLE_NAME = :table_name '
                    'AND COLUMN_NAME = :column_name'),
                {'table_name': table_name, 'column_name': column_name}
            ).first()
        self.assertIsNotNone(row)
        values = mariadb._parse_enum_column_type(row[0])
        self.assertIsNotNone(values)
        return values

    def _shrink_enum_columns(self, omit=None):
        """Rewrite every native ENUM column without one of its members.

        This is exactly what a database created from an older release
        looks like after a code upgrade: the Python enum has a member
        the column definition has never heard of.

        The member dropped defaults to the last one declared, which
        approximates "the newest" and is all a test needs when it does
        not care which member is missing. A test that does care must
        pass omit={(table, column): member_name} rather than relying on
        its member being last, because "last" moves whenever someone
        appends to the Python enum.
        """
        omit = omit or {}
        shrunk = []
        with self.engine.connect() as conn:
            for table_name, column in mariadb._native_enum_columns():
                values = list(column.type.enums)
                if len(values) < 2:
                    continue
                dropped = omit.get((table_name, column.name), values[-1])
                self.assertIn(dropped, values)
                remaining = [v for v in values if v != dropped]
                nullability = 'NULL' if column.nullable else 'NOT NULL'
                conn.execute(sa.text(
                    f'ALTER TABLE {table_name} '
                    f'MODIFY COLUMN {column.name} '
                    f'{mariadb._render_enum_ddl(remaining)} '
                    f'{nullability}'))
                shrunk.append((table_name, column.name, dropped))
            conn.commit()
        return shrunk

    def test_widening_restores_all_enum_columns(self):
        shrunk = self._shrink_enum_columns()
        self.assertNotEqual([], shrunk)
        for table_name, column_name, newest in shrunk:
            self.assertNotIn(
                newest, self._column_enum_values(table_name, column_name))

        result = mariadb._ensure_native_enum_columns(self.engine)

        self.assertTrue(result['migrated'])
        self.assertEqual(len(shrunk), len(result['altered_columns']))
        for table_name, column_name, newest in shrunk:
            self.assertIn(
                newest, self._column_enum_values(table_name, column_name))

        # Reconciliation is idempotent: a second run changes nothing.
        result = mariadb._ensure_native_enum_columns(self.engine)
        self.assertFalse(result['migrated'])

    def test_namespace_key_state_write_succeeds_after_widening(self):
        """The incident's exact failing write works after reconciliation.

        On the broken cluster this INSERT raised DataError 1265 ("Data
        truncated for column 'object_type'"), which surfaced as 500s on
        every API request that touched namespace keys. First reproduce
        that failure against the shrunken schema, then prove the
        reconciliation fixes it.
        """
        # Name the member to drop rather than taking the default of
        # "whichever is last": this test inserts a NAMESPACE_KEY row and
        # needs that specific member missing. It used to be last, so the
        # default happened to work, until trusted_issuer and mapping_rule
        # were appended after it and the INSERT stopped failing.
        self._shrink_enum_columns(
            omit={('object_states', 'object_type'):
                  ObjectType.NAMESPACE_KEY.name})

        table = mariadb._get_object_states_table()
        key_uuid = str(uuid4())
        with self.engine.connect() as conn:
            self.assertRaises(
                sa.exc.DBAPIError,
                conn.execute,
                table.insert().values(
                    object_uuid=key_uuid,
                    object_type=ObjectType.NAMESPACE_KEY,
                    state_value='initial',
                    update_time=time.time(),
                    message=None))

        mariadb._ensure_native_enum_columns(self.engine)
        with self.engine.connect() as conn:
            conn.execute(table.insert().values(
                object_uuid=key_uuid,
                object_type=ObjectType.NAMESPACE_KEY,
                state_value='initial',
                update_time=time.time(),
                message=None))
            conn.commit()

            row = conn.execute(
                sa.select(table).where(
                    table.c.object_uuid == key_uuid)).first()
        self.assertIsNotNone(row)
        self.assertEqual('initial', row.state_value)
