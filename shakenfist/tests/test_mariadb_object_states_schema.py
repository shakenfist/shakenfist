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
    def test_v2_runs_both_steps_in_order(
            self, mock_get_version, mock_set_version):
        """From v2: the object_uuid index, then the key width, to v4."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_object_states_schema(mock_engine)

        self.assertEqual(result['table'], 'object_states')
        self.assertEqual(result['start_version'], 2)
        self.assertEqual(result['end_version'], 4)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_any_call(mock_engine, 'object_states', 3)
        mock_set_version.assert_any_call(mock_engine, 'object_states', 4)

        conn = mock_engine.connect.return_value.__enter__.return_value
        executed = ' '.join(
            str(call.args[0]) for call in conn.execute.call_args_list)
        self.assertIn('CREATE INDEX', executed)
        self.assertIn('idx_object_states_uuid', executed)
        self.assertIn('CHARACTER_MAXIMUM_LENGTH', executed)

    @mock.patch('shakenfist.mariadb._widen_object_key_column')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=3)
    def test_v3_to_v4_widens_the_object_key(
            self, mock_get_version, mock_set_version, mock_widen):
        """From v3: object_uuid is widened to hold a namespace name.

        v3's column was VARCHAR(36), a uuid's width, so a namespace
        whose name was longer than that -- which the create handler
        published as legal up to 63 characters -- could not have a
        state row written for it at all (issue 4856).
        """
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_object_states_schema(mock_engine)

        self.assertEqual(result['end_version'], 4)
        self.assertTrue(result['migrated'])
        mock_widen.assert_called_once_with(
            mock_engine, 'object_states', 'object_uuid')
        mock_set_version.assert_called_once_with(
            mock_engine, 'object_states', 4)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=4)
    def test_already_at_v4_is_noop(
            self, mock_get_version, mock_set_version):
        """Already at v4: no DDL, no version write, migrated=False."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_object_states_schema(mock_engine)

        self.assertEqual(result['end_version'], 4)
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
