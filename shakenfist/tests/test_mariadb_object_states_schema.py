# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Version-gating tests for _ensure_object_states_schema().

The migration DDL runs against a mocked engine, so no real MariaDB is needed;
these tests only exercise the version branching, not the SQL execution itself
(functional CI covers the live migration).
"""

from unittest import mock

from shakenfist import mariadb
from shakenfist.tests import base


class EnsureObjectStatesSchemaTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_object_states_schema() version gating."""

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_v2_to_v3_adds_uuid_index(
            self, mock_get_version, mock_set_version):
        """From v2: the object_uuid index is created and the version advances."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_object_states_schema(mock_engine)

        self.assertEqual(result['table'], 'object_states')
        self.assertEqual(result['start_version'], 2)
        self.assertEqual(result['end_version'], 3)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'object_states', 3)

        conn = mock_engine.connect.return_value.__enter__.return_value
        executed = ' '.join(
            str(call.args[0]) for call in conn.execute.call_args_list)
        self.assertIn('CREATE INDEX', executed)
        self.assertIn('idx_object_states_uuid', executed)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=3)
    def test_already_at_v3_is_noop(
            self, mock_get_version, mock_set_version):
        """Already at v3: no DDL, no version write, migrated=False."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_object_states_schema(mock_engine)

        self.assertEqual(result['end_version'], 3)
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.assert_not_called()

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_fresh_creates_at_target_version(
            self, mock_get_version, mock_set_version):
        """From 0: the table is created straight at the target version.

        A fresh install gets idx_object_states_uuid from the table definition
        (created by create_all), so it never runs the v2->v3 migration branch.
        """
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all'):
            result = mariadb._ensure_object_states_schema(mock_engine)

        self.assertEqual(
            result['end_version'], mariadb.OBJECT_STATES_VERSION)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_any_call(
            mock_engine, 'object_states', mariadb.OBJECT_STATES_VERSION)
