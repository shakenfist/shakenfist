# Copyright 2026 Michael Still and contributors
#
# Unit tests for the namespace_attributes schema, and in particular for
# the version 3 migration which drops the legacy keys JSON column
# (issue 5016). Namespace keys became objects of their own in the
# namespace_keys / namespace_key_attributes tables, and nothing has read
# that column since -- so what it held was a second copy of live bcrypt
# hashes and nonces, at rest, with no reader.

import inspect
import json
from unittest import mock

from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.schema.namespace_attributes import NamespaceAttributesData
from shakenfist.tests import base


def _inspector(has_table=False, columns=('name', 'keys', 'trust')):
    """A sqlalchemy.inspect() stub for the namespace_attributes table."""
    inspector = mock.MagicMock()
    inspector.has_table.return_value = has_table
    inspector.get_columns.return_value = [{'name': c} for c in columns]
    return mock.patch('shakenfist.mariadb.sa.inspect',
                      return_value=inspector)


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

        with mock.patch('sqlalchemy.MetaData.create_all'), _inspector():
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

    @mock.patch('shakenfist.mariadb._unmigrated_legacy_keys', return_value=[])
    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes',
                return_value=(0, 0))
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_version_2_drops_the_legacy_keys_column(
            self, mock_get_version, mock_set_version, mock_migrate,
            mock_unmigrated):
        """An upgrading cluster loses the column and lands at v3."""
        mock_engine = mock.MagicMock()

        with _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        # The copy is retried, and confirmed, before the drop.
        mock_migrate.assert_called_once_with(mock_engine)
        mock_unmigrated.assert_called_once_with(mock_engine)

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

    @mock.patch('shakenfist.mariadb._unmigrated_legacy_keys', return_value=[])
    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes',
                return_value=(0, 0))
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_a_failed_drop_does_not_claim_the_version(
            self, mock_get_version, mock_set_version, mock_migrate,
            mock_unmigrated):
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

        with _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual(2, result['end_version'])
        self.assertEqual(3, result['target_version'])
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()


class DropRefusesWhileKeysAreUncopiedTestCase(base.ShakenFistTestCase):
    """The drop proves the copy happened before it destroys the original.

    namespace_keys stamps its version 2 even when the fan out counted
    failures, and a cluster which reached that version releases ago may
    hold a key whose copy failed then and which lives today only in the
    legacy column. Dropping the column would delete that key for good,
    and nothing would ever retry it.
    """

    def _drops(self, mock_engine):
        return [stmt for stmt in _executed(mock_engine)
                if 'DROP COLUMN' in stmt]

    @mock.patch('shakenfist.mariadb._unmigrated_legacy_keys',
                return_value=['banana:first'])
    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes',
                return_value=(0, 1))
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_an_uncopied_key_keeps_the_column(
            self, mock_get_version, mock_set_version, mock_migrate,
            mock_unmigrated):
        mock_engine = mock.MagicMock()

        with _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual([], self._drops(mock_engine))
        self.assertEqual(2, result['end_version'])
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()

    @mock.patch('shakenfist.mariadb._unmigrated_legacy_keys',
                side_effect=OperationalError(
                    'SELECT', {}, Exception('lock wait timeout exceeded')))
    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes',
                return_value=(0, 0))
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_a_check_which_cannot_be_made_keeps_the_column(
            self, mock_get_version, mock_set_version, mock_migrate,
            mock_unmigrated):
        """A failed read is not an empty answer."""
        mock_engine = mock.MagicMock()

        with _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual([], self._drops(mock_engine))
        self.assertEqual(2, result['end_version'])
        mock_set_version.assert_not_called()

    @mock.patch('shakenfist.mariadb._unmigrated_legacy_keys')
    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_a_column_already_gone_is_stamped(
            self, mock_get_version, mock_set_version, mock_migrate,
            mock_unmigrated):
        """A drop whose version stamp failed is finished, not stuck."""
        mock_engine = mock.MagicMock()

        with _inspector(has_table=True, columns=('name', 'trust')):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        # Nothing to read the keys out of, so nothing is asked of it.
        mock_migrate.assert_not_called()
        mock_unmigrated.assert_not_called()
        self.assertEqual(3, result['end_version'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'namespace_attributes', 3)

    @mock.patch('shakenfist.mariadb._unmigrated_legacy_keys', return_value=[])
    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes',
                return_value=(0, 0))
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_an_unversioned_existing_table_is_checked_not_stamped(
            self, mock_get_version, mock_set_version, mock_migrate,
            mock_unmigrated):
        """A table that exists with no version may still have the column."""
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all') as create_all, \
                _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        create_all.assert_not_called()
        mock_migrate.assert_called_once_with(mock_engine)
        self.assertEqual(1, len(self._drops(mock_engine)))
        self.assertEqual(3, result['end_version'])


class UnmigratedLegacyKeysTestCase(base.ShakenFistTestCase):
    """Tests for _unmigrated_legacy_keys()."""

    def _ask(self, namespace_rows, existing_rows):
        conn = mock.MagicMock()
        conn.execute.side_effect = [
            mock.Mock(fetchall=mock.Mock(return_value=namespace_rows)),
            mock.Mock(fetchall=mock.Mock(return_value=existing_rows)),
        ]
        engine = mock.MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        return mariadb._unmigrated_legacy_keys(engine)

    def test_a_copied_key_is_not_reported(self):
        self.assertEqual([], self._ask(
            [_row('banana', {'nonced_keys': {
                'first': {'key': 'aGFzaA==', 'nonce': 'n'}}})],
            [_existing('banana', 'first')]))

    def test_an_uncopied_key_is_reported(self):
        self.assertEqual(['banana:second'], self._ask(
            [_row('banana', {'nonced_keys': {
                'first': {'key': 'aGFzaA==', 'nonce': 'n'},
                'second': {'key': 'aGFzaA==', 'nonce': 'n'}}})],
            [_existing('banana', 'first')]))

    def test_the_json_text_the_driver_returns_is_decoded(self):
        self.assertEqual(['banana:first'], self._ask(
            [_row('banana', json.dumps({'nonced_keys': {
                'first': {'key': 'aGFzaA==', 'nonce': 'n'}}}))],
            []))

    def test_a_key_of_the_same_name_in_another_namespace_is_not_a_copy(self):
        self.assertEqual(['banana:first'], self._ask(
            [_row('banana', {'nonced_keys': {
                'first': {'key': 'aGFzaA==', 'nonce': 'n'}}})],
            [_existing('apple', 'first')]))

    def test_entries_which_could_never_authenticate_do_not_block(self):
        """Without both a hash and a nonce there is nothing to lose."""
        self.assertEqual([], self._ask(
            [_row('banana', {'nonced_keys': {
                'nohash': {'nonce': 'n'},
                'nononce': {'key': 'aGFzaA=='},
                'notadict': 'nope'}}),
             _row('apple', 'not json'),
             _row('cherry', ['a', 'list']),
             _row('damson', {'nonced_keys': 'nope'}),
             _row('elder', None)],
            []))


def _row(name, keys_json):
    row = mock.Mock(keys_json=keys_json)
    row.name = name
    return row


def _existing(namespace, name):
    row = mock.Mock(namespace=namespace)
    row.name = name
    return row
