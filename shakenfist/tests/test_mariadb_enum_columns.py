# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Tests for native ENUM column reconciliation.

MariaDB ENUM columns freeze their permitted values at CREATE TABLE time,
so adding a member to a Python enum silently breaks existing databases:
inserts of the new value fail with "Data truncated for column ..." (error
1265). This is exactly what happened when ObjectType.NAMESPACE_KEY shipped
without a widening migration and every API request touching namespace keys
started returning 500s.

_ensure_native_enum_columns() reconciles the database ENUMs against the
Python enums on every ensure_schema() run. These tests exercise it against
a mocked engine; tools/ci-enum-widening-test.sh proves the live behaviour
against a real MariaDB in CI.
"""

from unittest import mock

from pydantic_settings import BaseSettings

from shakenfist import mariadb
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    MARIADB_HOST: str = 'localhost'


fake_config = FakeConfig()


# The (table, column) pairs we know are rendered as native MariaDB ENUMs.
# Discovery is automatic, so a new sa.Enum(...) column does not need to be
# added here to be reconciled -- this list only anchors the assertion that
# discovery keeps finding the columns that exist today.
KNOWN_ENUM_COLUMNS = {
    ('cluster_operation_targets', 'target_object_type'),
    ('ipam_reservations', 'reservation_type'),
    ('ipam_reservations', 'user_type'),
    ('object_metadata', 'object_type'),
    ('object_states', 'object_type'),
}


def _current_column_types() -> dict:
    """Build information_schema COLUMN_TYPE strings matching the Python enums.

    This is what a fully up-to-date database would report: the exact enum
    member list, rendered the way MariaDB does (lowercase keyword, no space
    after commas).
    """
    column_types = {}
    for table_name, column in mariadb._native_enum_columns():
        rendered = ','.join(f"'{v}'" for v in column.type.enums)
        column_types[(table_name, column.name)] = f'enum({rendered})'
    return column_types


class _FakeConnection:
    """A connection double that answers information_schema queries.

    Serves COLUMN_TYPE values from a dict keyed by (table, column) and
    records every executed statement so tests can assert on emitted DDL.
    """

    def __init__(self, column_types: dict):
        self.column_types = column_types
        self.executed = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, params))
        result = mock.MagicMock()
        if 'information_schema' in sql:
            column_type = self.column_types.get(
                (params['table_name'], params['column_name']))
            result.first.return_value = (
                None if column_type is None else (column_type,))
        return result

    def commit(self):
        pass

    def alters(self):
        return [sql for sql, _ in self.executed if sql.startswith('ALTER')]


def _engine_with(column_types: dict):
    conn = _FakeConnection(column_types)
    engine = mock.MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    return engine, conn


class NativeEnumDiscoveryTestCase(base.ShakenFistTestCase):
    """Tests for _native_enum_columns() discovery."""

    def test_discovers_known_enum_columns(self):
        """Discovery finds every ENUM column that exists today.

        This is a superset assertion: a future sa.Enum(...) column is
        covered automatically and must not break this test, but losing
        discovery of an existing column must.
        """
        discovered = {
            (table_name, column.name)
            for table_name, column in mariadb._native_enum_columns()}
        self.assertTrue(
            KNOWN_ENUM_COLUMNS.issubset(discovered),
            f'ENUM column discovery lost columns: '
            f'{KNOWN_ENUM_COLUMNS - discovered}')

    def test_discovered_columns_use_member_names(self):
        """The canonical value list is enum member names, not values.

        The removed _build_object_type_enum_values() helper quoted enum
        *values* ('namespace_key'), but SQLAlchemy renders member *names*
        ('NAMESPACE_KEY') into the DDL. Guard against that bug returning.
        """
        for table_name, column in mariadb._native_enum_columns():
            if column.type.enum_class is mariadb.ObjectType:
                self.assertIn('NAMESPACE_KEY', column.type.enums)
                self.assertNotIn('namespace_key', column.type.enums)


class ParseEnumColumnTypeTestCase(base.ShakenFistTestCase):
    """Tests for _parse_enum_column_type()."""

    def test_non_enum_returns_none(self):
        self.assertIsNone(mariadb._parse_enum_column_type('varchar(36)'))
        self.assertIsNone(mariadb._parse_enum_column_type('bigint(20)'))

    def test_simple_enum(self):
        self.assertEqual(
            ['A', 'B', 'C'],
            mariadb._parse_enum_column_type("enum('A','B','C')"))

    def test_uppercase_keyword_and_spaces(self):
        self.assertEqual(
            ['A', 'B'],
            mariadb._parse_enum_column_type("ENUM('A', 'B')"))

    def test_embedded_quote(self):
        self.assertEqual(
            ["it's", 'B'],
            mariadb._parse_enum_column_type("enum('it''s','B')"))

    def test_round_trips_render(self):
        values = ['A', "it's", 'B-2']
        rendered = mariadb._render_enum_ddl(values)
        self.assertEqual(
            values,
            mariadb._parse_enum_column_type(rendered))


class EnsureNativeEnumColumnsTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_native_enum_columns() against a mocked engine."""

    def test_up_to_date_database_is_noop(self):
        engine, conn = _engine_with(_current_column_types())

        result = mariadb._ensure_native_enum_columns(engine)

        self.assertEqual([], conn.alters())
        self.assertFalse(result['migrated'])
        self.assertEqual([], result['altered_columns'])

    def test_namespace_key_regression(self):
        """An object_states ENUM predating NAMESPACE_KEY is widened.

        Regression test for the 2026-07-28 outage: clusters deployed
        before ObjectType.NAMESPACE_KEY existed rejected namespace key
        state writes with "Data truncated for column 'object_type'",
        which turned every request touching namespace keys into a 500.
        """
        column_types = _current_column_types()
        for key in [('object_states', 'object_type'),
                    ('object_metadata', 'object_type'),
                    ('cluster_operation_targets', 'target_object_type'),
                    ('ipam_reservations', 'user_type')]:
            column_types[key] = column_types[key].replace(
                ",'NAMESPACE_KEY'", '')
            self.assertNotIn('NAMESPACE_KEY', column_types[key])
        engine, conn = _engine_with(column_types)

        result = mariadb._ensure_native_enum_columns(engine)

        self.assertTrue(result['migrated'])
        self.assertEqual(
            ['cluster_operation_targets.target_object_type',
             'ipam_reservations.user_type',
             'object_metadata.object_type',
             'object_states.object_type'],
            result['altered_columns'])

        alters = conn.alters()
        self.assertEqual(4, len(alters))
        for alter in alters:
            self.assertIn("'NAMESPACE_KEY'", alter)
            self.assertIn('MODIFY COLUMN', alter)

    def test_nullability_is_preserved(self):
        """user_type is nullable and must stay so; object_type must not."""
        column_types = _current_column_types()
        for key in [('object_states', 'object_type'),
                    ('ipam_reservations', 'user_type')]:
            column_types[key] = column_types[key].replace(
                ",'NAMESPACE_KEY'", '')
        engine, conn = _engine_with(column_types)

        mariadb._ensure_native_enum_columns(engine)

        by_table = {}
        for alter in conn.alters():
            by_table[alter.split(' ')[2]] = alter
        self.assertTrue(by_table['object_states'].endswith(' NOT NULL'))
        self.assertTrue(by_table['ipam_reservations'].endswith(' NULL'))
        self.assertFalse(
            by_table['ipam_reservations'].endswith(' NOT NULL'))

    def test_stale_values_are_retained(self):
        """Values removed from the Python enum stay in the column.

        Rows may still hold the old value; dropping it from the ENUM would
        corrupt them. The stale value is appended after the canonical list
        so canonical ordinals stay stable.
        """
        column_types = _current_column_types()
        key = ('object_states', 'object_type')
        column_types[key] = column_types[key].replace(
            ",'NAMESPACE_KEY'", '').replace(
            "enum(", "enum('LEGACY_TYPE',")
        engine, conn = _engine_with(column_types)

        mariadb._ensure_native_enum_columns(engine)

        alters = conn.alters()
        self.assertEqual(1, len(alters))
        self.assertIn("'NAMESPACE_KEY'", alters[0])
        self.assertIn("'LEGACY_TYPE'", alters[0])
        # The stale value comes after every canonical value.
        self.assertGreater(
            alters[0].index("'LEGACY_TYPE'"),
            alters[0].index("'NAMESPACE_KEY'"))

    def test_missing_column_is_skipped(self):
        """A table not present in the database is skipped without DDL."""
        column_types = _current_column_types()
        del column_types[('object_states', 'object_type')]
        engine, conn = _engine_with(column_types)

        result = mariadb._ensure_native_enum_columns(engine)

        self.assertEqual([], conn.alters())
        self.assertFalse(result['migrated'])

    def test_non_enum_database_column_is_skipped(self):
        """A column that is not an ENUM in the database is left alone."""
        column_types = _current_column_types()
        column_types[('object_states', 'object_type')] = 'varchar(64)'
        engine, conn = _engine_with(column_types)

        result = mariadb._ensure_native_enum_columns(engine)

        self.assertEqual([], conn.alters())
        self.assertFalse(result['migrated'])


class EnsureSchemaWiringTestCase(base.ShakenFistTestCase):
    """ensure_schema() must run the ENUM reconciliation pass."""

    def test_ensure_schema_calls_enum_reconciliation(self):
        ensure_fns = [
            name for name in dir(mariadb)
            if name.startswith('_ensure_') and name.endswith('_schema')]
        patchers = []
        for name in ensure_fns:
            p = mock.patch.object(
                mariadb, name,
                return_value={'table': name, 'migrated': False})
            p.start()
            patchers.append(p)
        self.addCleanup(lambda: [p.stop() for p in patchers])

        with mock.patch.object(mariadb, '_ensure_schema_versions_table'), \
                mock.patch.object(mariadb, '_get_engine'), \
                mock.patch.object(
                    mariadb, '_ensure_native_enum_columns',
                    return_value={'table': 'native-enum-columns',
                                  'altered_columns': [],
                                  'migrated': False}) as mock_reconcile, \
                mock.patch('shakenfist.mariadb.config', fake_config):
            results = mariadb.ensure_schema()

        mock_reconcile.assert_called_once()
        self.assertEqual(
            'native-enum-columns', results[-1]['table'])
