# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Tests for the node_metrics typed capacity columns.

Covers the extraction spec (coercions, missing keys, garbage values),
drift between the spec and the table definition, population of the typed
columns in the upsert statement, and the version gating of
_ensure_node_metrics_schema(). The migration DDL runs against a mocked
engine, so no real MariaDB is needed; these tests only exercise the
version branching and generated SQL, not its execution (functional CI
covers the live migration).
"""

from unittest import mock
from uuid import uuid4

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base


# A realistic metrics dict, matching what the resources daemon publishes.
# Note that some values arrive as strings (the delta fields are floats
# rendered as strings, e.g. '16.6').
REALISTIC_METRICS = {
    'cpu_max': 16,
    'cpu_schedulable': 14,
    'cpu_max_per_instance': 16,
    'cpu_total_instance_vcpus': 12,
    'cpu_load_1': 0.42,
    'cpu_load_5': '0.61',
    'cpu_load_15': 0.55,
    'memory_max': 64243,
    'memory_available': 41892,
    'memory_reserved_mb': 4096,
    'memory_total_instance_actual': 18022,
    'disk_free_instances': 803469852672,
    'disk_reservation_gb': 20,
    'disk_busy_time_delta_per_second': '16.6',
    'node_queue_waiting': 3,
    'is_hypervisor': True,

    # Fields which are not projected to typed columns.
    'instances_total': 7,
    'network_in_bytes_delta_per_second': '1024.5',
}

BASE_COLUMNS = ['node_uuid', 'fqdn', 'timestamp', 'metrics_json']


class NodeMetricsExtractionTestCase(base.ShakenFistTestCase):
    """Tests for the extraction spec and _extract_node_metrics_columns()."""

    def test_realistic_metrics_extract_correctly(self):
        extracted = mariadb._extract_node_metrics_columns(
            uuid4(), REALISTIC_METRICS)

        self.assertEqual(
            {
                'cpu_max': 16,
                'cpu_schedulable': 14,
                'cpu_max_per_instance': 16,
                'cpu_total_instance_vcpus': 12,
                'cpu_load_1': 0.42,
                'cpu_load_5': 0.61,
                'cpu_load_15': 0.55,
                'memory_max': 64243,
                'memory_available': 41892,
                'memory_reserved_mb': 4096,
                'memory_total_instance_actual': 18022,
                'disk_free_instances': 803469852672,
                'disk_reservation_gb': 20,
                'disk_busy_time_delta_per_second': 16.6,
                'node_queue_waiting': 3,
                'is_hypervisor': True,
            }, extracted)

        # Integral columns really are ints (parsed via float() first, so a
        # float-string for an integral column would truncate, not raise).
        self.assertIsInstance(extracted['cpu_max'], int)
        self.assertIsInstance(extracted['disk_free_instances'], int)
        self.assertIsInstance(
            extracted['disk_busy_time_delta_per_second'], float)

    def test_float_string_truncates_for_integral_column(self):
        extracted = mariadb._extract_node_metrics_columns(
            uuid4(), {'cpu_max': '16.6'})
        self.assertEqual(16, extracted['cpu_max'])
        self.assertIsInstance(extracted['cpu_max'], int)

    def test_missing_keys_extract_as_none(self):
        with mock.patch.object(mariadb, 'LOG') as mock_log:
            extracted = mariadb._extract_node_metrics_columns(uuid4(), {})

        self.assertEqual(len(mariadb.NODE_METRICS_EXTRACTION_SPEC),
                         len(extracted))
        for column_name, value in extracted.items():
            self.assertIsNone(value, f'{column_name} should be None')
        mock_log.warning.assert_not_called()

    def test_explicit_none_extracts_as_none_without_warning(self):
        with mock.patch.object(mariadb, 'LOG') as mock_log:
            extracted = mariadb._extract_node_metrics_columns(
                uuid4(), {'cpu_max': None})
        self.assertIsNone(extracted['cpu_max'])
        mock_log.warning.assert_not_called()

    def test_garbage_extracts_as_none_and_warns(self):
        node_uuid = uuid4()
        metrics = dict(REALISTIC_METRICS)
        metrics['cpu_max'] = 'banana'
        metrics['cpu_load_1'] = ['not', 'a', 'number']

        with mock.patch.object(mariadb, 'LOG') as mock_log:
            extracted = mariadb._extract_node_metrics_columns(
                node_uuid, metrics)

        self.assertIsNone(extracted['cpu_max'])
        self.assertIsNone(extracted['cpu_load_1'])

        # Other fields still extract despite the garbage.
        self.assertEqual(14, extracted['cpu_schedulable'])
        self.assertEqual(16.6, extracted['disk_busy_time_delta_per_second'])

        # A warning was logged for each failed coercion, naming the node
        # and the key.
        self.assertEqual(2, mock_log.warning.call_count)
        warnings = ' '.join(
            str(call.args[0]) for call in mock_log.warning.call_args_list)
        self.assertIn(str(node_uuid), warnings)
        self.assertIn('cpu_max', warnings)
        self.assertIn('cpu_load_1', warnings)

    def test_spec_matches_table_definition(self):
        """Every spec column exists in the table, is nullable, and the
        typed columns are exactly the non-base columns (guards drift in
        both directions between the spec and the schema)."""
        table = mariadb._get_node_metrics_table()

        spec_columns = [column_name for _, column_name, _
                        in mariadb.NODE_METRICS_EXTRACTION_SPEC]
        self.assertEqual(16, len(spec_columns))
        self.assertEqual(len(spec_columns), len(set(spec_columns)))

        for column_name in spec_columns:
            self.assertIn(column_name, table.c)
            self.assertTrue(table.c[column_name].nullable,
                            f'{column_name} should be nullable')

        self.assertEqual(
            set(spec_columns),
            {c.name for c in table.c} - set(BASE_COLUMNS))

        # disk_free_instances is in bytes and needs a BIGINT.
        self.assertIsInstance(
            table.c['disk_free_instances'].type, sa.BigInteger)

    def test_spec_coercions_match_column_types(self):
        """Integral columns coerce to int, floating columns to float."""
        table = mariadb._get_node_metrics_table()
        for _, column_name, coercion in mariadb.NODE_METRICS_EXTRACTION_SPEC:
            column_type = table.c[column_name].type
            if isinstance(column_type, sa.Double):
                self.assertEqual(
                    mariadb._node_metric_to_float, coercion,
                    f'{column_name} is DOUBLE but does not coerce to float')
            elif isinstance(column_type, sa.Boolean):
                self.assertEqual(
                    mariadb._node_metric_to_bool, coercion,
                    f'{column_name} is BOOLEAN but does not coerce to bool')
            else:
                self.assertIsInstance(column_type, sa.Integer)
                self.assertEqual(
                    mariadb._node_metric_to_int, coercion,
                    f'{column_name} is integral but does not coerce to int')


class NodeMetricsUpsertTestCase(base.ShakenFistTestCase):
    """Tests that _direct_upsert_node_metrics() populates the typed
    columns in both halves of the INSERT ... ON DUPLICATE KEY UPDATE."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_upsert_populates_typed_columns(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_get_engine.return_value = mock_engine
        conn = mock_engine.connect.return_value.__enter__.return_value

        node_uuid = uuid4()
        result = mariadb._direct_upsert_node_metrics(
            node_uuid, 'sf-1', 1769800000.0, REALISTIC_METRICS)
        self.assertTrue(result)

        stmt = conn.execute.call_args.args[0]
        compiled = stmt.compile(dialect=sa.dialects.mysql.dialect())
        sql = str(compiled)

        self.assertIn('ON DUPLICATE KEY UPDATE', sql)
        insert_half, update_half = sql.split('ON DUPLICATE KEY UPDATE')
        for _, column_name, _ in mariadb.NODE_METRICS_EXTRACTION_SPEC:
            self.assertIn(column_name, insert_half,
                          f'{column_name} missing from INSERT half')
            self.assertIn(column_name, update_half,
                          f'{column_name} missing from UPDATE half')

        # The bound values are the coerced ones.
        self.assertEqual(16, compiled.params['cpu_max'])
        self.assertEqual(16.6,
                         compiled.params['disk_busy_time_delta_per_second'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_upsert_with_garbage_metrics_does_not_raise(
            self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_upsert_node_metrics(
            uuid4(), 'sf-1', 1769800000.0,
            {'cpu_max': 'banana', 'disk_busy_time_delta_per_second': {}})
        self.assertTrue(result)


class EnsureNodeMetricsSchemaTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_node_metrics_schema() version gating."""

    def test_target_version_is_wired_into_expected_versions(self):
        self.assertEqual(4, mariadb.NODE_METRICS_VERSION)
        self.assertEqual(mariadb.NODE_METRICS_VERSION,
                         mariadb.EXPECTED_SCHEMA_VERSIONS['node_metrics'])

    @mock.patch('shakenfist.mariadb.get_table_columns')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_v2_to_current_adds_columns(
            self, mock_get_version, mock_set_version, mock_get_columns):
        """From v2: every typed column is added and the version advances.

        Both the v3 and v4 steps run, and both just converge the table
        onto the spec, so an old database ends up with exactly the same
        columns as a fresh one regardless of which step introduced them.
        """
        mock_get_columns.return_value = {name: {} for name in BASE_COLUMNS}
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_node_metrics_schema(mock_engine)

        self.assertEqual(result['table'], 'node_metrics')
        self.assertEqual(result['start_version'], 2)
        self.assertEqual(result['end_version'], 4)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_has_calls([
            mock.call(mock_engine, 'node_metrics', 3),
            mock.call(mock_engine, 'node_metrics', 4)])

        conn = mock_engine.begin.return_value.__enter__.return_value
        executed = [str(call.args[0]) for call in conn.execute.call_args_list]
        joined = ' '.join(executed)
        for _, column_name, _ in mariadb.NODE_METRICS_EXTRACTION_SPEC:
            self.assertIn(
                f'ALTER TABLE node_metrics ADD COLUMN {column_name} ',
                joined)
        self.assertIn('disk_free_instances BIGINT NULL', joined)
        self.assertIn('cpu_load_1 DOUBLE NULL', joined)
        self.assertIn('cpu_max INTEGER NULL', joined)
        self.assertIn('is_hypervisor BOOL NULL', joined)

    @mock.patch('shakenfist.mariadb.get_table_columns')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_v2_to_current_is_idempotent(
            self, mock_get_version, mock_set_version, mock_get_columns):
        """A re-run against a table that already has the columns adds
        nothing but still advances the version."""
        table = mariadb._get_node_metrics_table()
        mock_get_columns.return_value = {c.name: {} for c in table.c}
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_node_metrics_schema(mock_engine)

        self.assertEqual(result['end_version'], 4)
        self.assertTrue(result['migrated'])
        conn = mock_engine.begin.return_value.__enter__.return_value
        conn.execute.assert_not_called()
        mock_set_version.assert_has_calls([
            mock.call(mock_engine, 'node_metrics', 3),
            mock.call(mock_engine, 'node_metrics', 4)])

    @mock.patch('shakenfist.mariadb.get_table_columns')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=3)
    def test_v3_to_v4_adds_only_is_hypervisor(
            self, mock_get_version, mock_set_version, mock_get_columns):
        """From v3: the capacity columns are already there, so only the
        new is_hypervisor column is added."""
        table = mariadb._get_node_metrics_table()
        mock_get_columns.return_value = {
            c.name: {} for c in table.c if c.name != 'is_hypervisor'}
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_node_metrics_schema(mock_engine)

        self.assertEqual(result['start_version'], 3)
        self.assertEqual(result['end_version'], 4)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'node_metrics', 4)

        conn = mock_engine.begin.return_value.__enter__.return_value
        executed = [str(call.args[0]) for call in conn.execute.call_args_list]
        self.assertEqual(1, len(executed))
        self.assertIn(
            'ALTER TABLE node_metrics ADD COLUMN is_hypervisor BOOL NULL',
            executed[0])

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=4)
    def test_already_at_v4_is_noop(
            self, mock_get_version, mock_set_version):
        """Already at v4: no DDL, no version write, migrated=False."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_node_metrics_schema(mock_engine)

        self.assertEqual(result['end_version'], 4)
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()
        mock_engine.begin.assert_not_called()
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.assert_not_called()

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_fresh_creates_at_target_version(
            self, mock_get_version, mock_set_version):
        """From 0: the table is created straight at the target version.

        A fresh install gets the typed columns from the table definition
        (created by create_all), so it never runs the v2->v3 migration
        branch.
        """
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all'):
            result = mariadb._ensure_node_metrics_schema(mock_engine)

        self.assertEqual(
            result['end_version'], mariadb.NODE_METRICS_VERSION)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'node_metrics', mariadb.NODE_METRICS_VERSION)
        mock_engine.begin.assert_not_called()
