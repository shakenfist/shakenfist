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


def _versions(**versions):
    """A _get_table_version() stub which answers per table.

    The v2->v3 step asks about two tables: its own, and namespace_keys,
    whose version 2 is the proof that the legacy keys were copied.
    """
    def _get(engine, table_name):
        return versions.get(table_name, 0)
    return _get


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
        # The drop step is gated on this constant, so it has to be the
        # version the table is expected to reach.
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

    @mock.patch('shakenfist.mariadb._unmigrated_legacy_keys')
    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version',
                side_effect=_versions(namespace_attributes=2,
                                      namespace_keys=2))
    def test_version_2_drops_the_legacy_keys_column(
            self, mock_get_version, mock_set_version, mock_migrate,
            mock_unmigrated):
        """An upgrading cluster loses the column and lands at v3."""
        mock_engine = mock.MagicMock()

        with _inspector(has_table=True):
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
    @mock.patch('shakenfist.mariadb._get_table_version',
                side_effect=_versions(namespace_attributes=2,
                                      namespace_keys=2))
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

        with _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual(2, result['end_version'])
        self.assertEqual(3, result['target_version'])
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()


class DropTrustsTheProvenCopyTestCase(base.ShakenFistTestCase):
    """The drop goes ahead on namespace_keys' proof, and only on it.

    namespace_keys stamps version 2 only once its fan out has proven
    every well-formed legacy key has a row. Below that the copy is not
    proven and the drop refuses. From it on, a legacy key with no row
    has been revoked since -- remove_key() is a hard delete -- or has
    not authenticated since, and copying it back could only make a
    revoked credential valid again.
    """

    def _drops(self, mock_engine):
        return [stmt for stmt in _executed(mock_engine)
                if 'DROP COLUMN' in stmt]

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version',
                side_effect=_versions(namespace_attributes=2,
                                      namespace_keys=1))
    def test_an_unproven_copy_keeps_the_column(
            self, mock_get_version, mock_set_version):
        mock_engine = mock.MagicMock()

        with _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual([], self._drops(mock_engine))
        self.assertEqual(2, result['end_version'])
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()

    @mock.patch('shakenfist.mariadb._legacy_keys_column_present',
                side_effect=OperationalError(
                    'SHOW COLUMNS', {}, Exception('lock wait timeout')))
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version',
                side_effect=_versions(namespace_attributes=2,
                                      namespace_keys=2))
    def test_a_check_which_cannot_be_made_keeps_the_column(
            self, mock_get_version, mock_set_version, mock_present):
        """A failed inspection is not an absent column."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual([], self._drops(mock_engine))
        self.assertEqual(2, result['end_version'])
        mock_set_version.assert_not_called()

    @mock.patch('shakenfist.mariadb._unmigrated_legacy_keys',
                return_value=['banana:revoked'])
    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version',
                side_effect=_versions(namespace_attributes=2,
                                      namespace_keys=2))
    def test_a_key_removed_since_the_copy_is_not_copied_back(
            self, mock_get_version, mock_set_version, mock_migrate,
            mock_unmigrated):
        """The column is dropped as it stands, not fanned out again.

        Fanning out here re-created a key removed after the first fan
        out, with its old hash and nonce, in state created.
        """
        mock_engine = mock.MagicMock()

        with _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        mock_migrate.assert_not_called()
        self.assertEqual(1, len(self._drops(mock_engine)))
        self.assertEqual(3, result['end_version'])

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version',
                side_effect=_versions(namespace_attributes=2,
                                      namespace_keys=1))
    def test_a_column_already_gone_is_stamped(
            self, mock_get_version, mock_set_version):
        """A drop whose version stamp failed is finished, not stuck.

        Nothing is left to lose, so not even an unproven copy holds it
        back.
        """
        mock_engine = mock.MagicMock()

        with _inspector(has_table=True, columns=('name', 'trust')):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual(3, result['end_version'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'namespace_attributes', 3)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version',
                side_effect=_versions(namespace_attributes=0,
                                      namespace_keys=2))
    def test_an_unversioned_existing_table_is_checked_not_stamped(
            self, mock_get_version, mock_set_version):
        """A table that exists with no version may still have the column."""
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all') as create_all, \
                _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        create_all.assert_not_called()
        self.assertEqual(1, len(self._drops(mock_engine)))
        self.assertEqual(3, result['end_version'])

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version',
                side_effect=_versions(namespace_attributes=0,
                                      namespace_keys=1))
    def test_a_refused_drop_reports_the_version_recorded(
            self, mock_get_version, mock_set_version):
        """An unversioned table which is refused reports 0, not 1.

        The version is what an operator will go and look for in
        schema_versions, so it has to be the one which is there.
        """
        mock_engine = mock.MagicMock()

        with _inspector(has_table=True):
            result = mariadb._ensure_namespace_attributes_schema(mock_engine)

        self.assertEqual([], self._drops(mock_engine))
        self.assertEqual(0, result['end_version'])
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()


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
