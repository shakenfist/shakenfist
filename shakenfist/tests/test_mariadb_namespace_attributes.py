# Copyright 2026 Michael Still and contributors
#
# Unit tests for the namespace_attributes schema, and in particular for
# the version 3 migration which drops the legacy keys JSON column
# (issue 5016). Namespace keys became objects of their own in the
# namespace_keys / namespace_key_attributes tables, and nothing has read
# that column since -- so what it held was a second copy of live bcrypt
# hashes and nonces, at rest, with no reader.

import inspect
from unittest import mock

from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.schema.namespace_attributes import NamespaceAttributesData
from shakenfist.tests import base


def _executed(mock_engine):
    """Every statement the ensure function issued, as text."""
    conn = mock_engine.connect.return_value.__enter__.return_value
    return [str(call.args[0]) for call in conn.execute.call_args_list]


class NamespaceAttributesShapeTestCase(base.ShakenFistTestCase):
    """The column and the model field are both gone."""

    def test_table_has_no_keys_column(self):
        table = mariadb._get_namespace_attributes_table()
        self.assertEqual(['name', 'trust'], list(table.c.keys()))

    def test_model_has_no_keys_field(self):
        self.assertEqual(
            ['name', 'trust'],
            list(NamespaceAttributesData.model_fields.keys()))

    def test_expected_schema_version_is_3(self):
        self.assertEqual(
            3, mariadb.EXPECTED_SCHEMA_VERSIONS['namespace_attributes'])
        # The namespace_keys migration reads this constant to decide
        # whether the column it fans out is still there, so the two
        # must agree about which version dropped it.
        self.assertEqual(
            3, mariadb.NAMESPACE_ATTRIBUTES_KEYS_DROPPED_VERSION)

    def test_keys_are_fanned_out_before_the_column_is_dropped(self):
        """ensure_schema() runs namespace_keys before this table.

        The namespace_keys v1->v2 data migration is the only reader the
        legacy column has. The other way round, an upgrading cluster
        would have the column dropped before its keys had been copied
        out of it, and every key which predated the upgrade would be
        gone.
        """
        source = inspect.getsource(mariadb.ensure_schema)
        keys = source.index('_ensure_namespace_keys_schema(engine)')
        attributes = source.index(
            '_ensure_namespace_attributes_schema(engine)')
        self.assertLess(
            keys, attributes,
            'ensure_schema() must ensure namespace_keys before '
            'namespace_attributes, whose v2->v3 step drops the column '
            'the namespace_keys migration reads')


class EnsureNamespaceAttributesSchemaTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_namespace_attributes_schema()."""

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_fresh_engine_creates_the_table_at_version_3(
            self, mock_get_version, mock_set_version):
        """A fresh table is created without the column in the first place."""
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all'):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual('namespace_attributes', result['table'])
        self.assertEqual(0, result['start_version'])
        self.assertEqual(3, result['end_version'])
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'namespace_attributes', 3)
        self.assertEqual(
            [], [stmt for stmt in _executed(mock_engine)
                 if 'DROP COLUMN' in stmt])

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_version_2_drops_the_legacy_keys_column(
            self, mock_get_version, mock_set_version):
        """An upgrading cluster loses the column and lands at v3."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual(2, result['start_version'])
        self.assertEqual(3, result['end_version'])
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'namespace_attributes', 3)

        drops = [stmt for stmt in _executed(mock_engine)
                 if 'DROP COLUMN' in stmt]
        self.assertEqual(1, len(drops), _executed(mock_engine))
        # IF EXISTS keeps a re-run, or a deployment where create_all
        # never made the column, a no-op. The backquotes matter too:
        # keys is a MariaDB reserved word.
        self.assertIn('DROP COLUMN IF EXISTS `keys`', drops[0])

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=3)
    def test_already_at_version_3_is_a_noop(
            self, mock_get_version, mock_set_version):
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual(3, result['end_version'])
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()
        mock_engine.connect.assert_not_called()

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_a_failed_drop_does_not_claim_the_version(
            self, mock_get_version, mock_set_version):
        """A drop which fails leaves the table short of version 3.

        The point of the step is that the key material stops being at
        rest, so a failure must not be recorded as success: sf-ctl
        ensure-mariadb-schema names a table short of its version and
        exits non-zero, and sf-database refuses to start, which is what
        gets an operator to re-run it.
        """
        mock_engine = mock.MagicMock()
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = OperationalError(
            'ALTER TABLE', {}, Exception('lock wait timeout exceeded'))

        result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual(2, result['end_version'])
        self.assertEqual(3, result['target_version'])
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()
