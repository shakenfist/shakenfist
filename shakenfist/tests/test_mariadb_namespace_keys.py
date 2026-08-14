# Copyright 2019 Michael Still and contributors
#
# Unit tests for the namespace_keys / namespace_key_attributes schema
# helpers and the three-layer accessor stack in shakenfist/mariadb.py.
#
# Patterns mirrored:
#   - test_events_storage.py  (schema-up via mock engine + patched helpers,
#                              _MockEngine/_MockConnection direct-path tests,
#                              public-router tests)

from unittest import mock
from uuid import UUID

from pydantic import SecretStr

from shakenfist import mariadb
from shakenfist.schema.namespace_key_attributes import NamespaceKeyAttributesData
from shakenfist.schema.namespace_key_data import NamespaceKeyData
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


KEY_UUID_1 = UUID('aaaabbbb-0000-4000-8000-000000000001')
KEY_UUID_2 = UUID('aaaabbbb-0000-4000-8000-000000000002')

_SAMPLE_KEY = NamespaceKeyData(
    uuid=KEY_UUID_1,
    namespace='banana',
    name='keyname',
    version=1,
)

_SAMPLE_ATTRS = NamespaceKeyAttributesData(
    uuid=KEY_UUID_1,
    key='JDJiJDEyJGZha2VoYXNo',
    nonce='deadbeef',
    expiry=None,
    scopes=None,
    provenance=None,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class _MockRow:
    """Minimal row stub with attribute access for column values."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _MockResult:
    """Minimal result stub supporting fetchall(), fetchone() and rowcount."""

    def __init__(self, rows=None, rowcount=0):
        self._rows = rows if rows is not None else []
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _MockConnection:
    """Connection stub that records every execute() call."""

    def __init__(self, result=None):
        self.result = result or _MockResult()
        self.executed = []

    def execute(self, stmt, *args, **kwargs):
        self.executed.append(stmt)
        return self.result

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _MockEngine:
    """Engine stub returning a fixed connection for connect() and begin()."""

    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        return self._connection

    def begin(self):
        return self._connection


# ---------------------------------------------------------------------------
# Schema-up tests
# ---------------------------------------------------------------------------

class EnsureNamespaceKeysSchemaTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_namespace_keys_schema() creation and idempotency."""

    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes',
                return_value=(0, 0))
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_fresh_engine_creates_table_and_migrates_to_version_2(
            self, mock_get_version, mock_set_version, mock_migrate):
        """From version 0 the table is created, then migrated to v2."""
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all'):
            result = mariadb._ensure_namespace_keys_schema(mock_engine)

        self.assertEqual(result['table'], 'namespace_keys')
        self.assertEqual(result['target_version'], 2)
        self.assertEqual(result['start_version'], 0)
        self.assertEqual(result['end_version'], 2)
        self.assertTrue(result['migrated'])
        self.assertEqual(
            [mock.call(mock_engine, 'namespace_keys', 1),
             mock.call(mock_engine, 'namespace_keys', 2)],
            mock_set_version.call_args_list)
        mock_migrate.assert_called_once_with(mock_engine)

    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes',
                return_value=(0, 0))
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_creation_emits_composite_unique_index_ddl(
            self, mock_get_version, mock_set_version, mock_migrate):
        """The (namespace, name) UNIQUE index is created by hand.

        The Pydantic marker system cannot express a composite unique
        index, so the ensure function issues the DDL itself. If that
        ever moves back onto the model, this test should move with it.
        """
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all'):
            mariadb._ensure_namespace_keys_schema(mock_engine)

        conn = mock_engine.connect.return_value.__enter__.return_value
        executed = [str(call.args[0]) for call in conn.execute.call_args_list]
        self.assertTrue(
            any('uidx_namespace_keys_namespace_name' in stmt
                for stmt in executed),
            f'No composite unique index DDL in {executed}')
        self.assertTrue(
            any('(namespace, name)' in stmt for stmt in executed),
            f'Unique index is not on (namespace, name) in {executed}')

    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes',
                return_value=(2, 0))
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_existing_v1_table_runs_only_the_migration(
            self, mock_get_version, mock_set_version, mock_migrate):
        """An upgrading cluster gets the data migration, and no DDL."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_namespace_keys_schema(mock_engine)

        self.assertEqual(result['start_version'], 1)
        self.assertEqual(result['end_version'], 2)
        self.assertTrue(result['migrated'])
        mock_migrate.assert_called_once_with(mock_engine)
        mock_set_version.assert_called_once_with(
            mock_engine, 'namespace_keys', 2)
        mock_engine.connect.assert_not_called()

    @mock.patch('shakenfist.mariadb._migrate_keys_from_namespace_attributes')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=2)
    def test_already_at_version_2_is_noop(
            self, mock_get_version, mock_set_version, mock_migrate):
        """Table already at v2: no DDL, no migration, migrated=False."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_namespace_keys_schema(mock_engine)

        self.assertEqual(result['table'], 'namespace_keys')
        self.assertEqual(result['target_version'], 2)
        self.assertEqual(result['end_version'], 2)
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()
        mock_migrate.assert_not_called()
        mock_engine.connect.assert_not_called()


class EnsureNamespaceKeyAttributesSchemaTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_namespace_key_attributes_schema()."""

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_fresh_engine_creates_table_and_returns_version_1(
            self, mock_get_version, mock_set_version):
        """From version 0 the table is created and lands at v1."""
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all'):
            result = mariadb._ensure_namespace_key_attributes_schema(
                mock_engine)

        self.assertEqual(result['table'], 'namespace_key_attributes')
        self.assertEqual(result['target_version'], 1)
        self.assertEqual(result['start_version'], 0)
        self.assertEqual(result['end_version'], 1)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'namespace_key_attributes', 1)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_already_at_version_1_is_noop(
            self, mock_get_version, mock_set_version):
        """Table already at v1: no DDL, no version bump, migrated=False."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_namespace_key_attributes_schema(mock_engine)

        self.assertEqual(result['table'], 'namespace_key_attributes')
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()

    def test_expected_schema_versions_lists_both_tables(self):
        """Both tables are registered in EXPECTED_SCHEMA_VERSIONS.

        namespace_keys is at v2 because of the data migration out of the
        namespace_attributes JSON column; namespace_key_attributes stays
        at v1 because that migration is gated on the namespace_keys
        version, not on this table's.
        """
        self.assertEqual(
            2, mariadb.EXPECTED_SCHEMA_VERSIONS['namespace_keys'])
        self.assertEqual(
            1, mariadb.EXPECTED_SCHEMA_VERSIONS['namespace_key_attributes'])


# ---------------------------------------------------------------------------
# The one shot migration out of namespace_attributes.keys
# ---------------------------------------------------------------------------

def _migration_connection(namespace_rows, existing_rows=None):
    """A connection stub for _migrate_keys_from_namespace_attributes().

    The migration issues two selects (the namespace attribute rows, then
    the (namespace, name) pairs which already have key rows) and then up
    to three inserts per key.
    """
    conn = _MockConnection()
    results = [
        _MockResult(rows=namespace_rows),
        _MockResult(rows=existing_rows or []),
    ]
    results += [_MockResult() for _ in range(64)]
    conn.execute = mock.Mock(side_effect=results)
    return conn


def _inserts_for(conn, table_name):
    """The compiled parameters of every insert into one table."""
    retval = []
    for call in conn.execute.call_args_list:
        stmt = call.args[0]
        if str(stmt).startswith(f'INSERT INTO {table_name} '):
            retval.append(stmt.compile().params)
    return retval


class MigrateKeysFromNamespaceAttributesTestCase(base.ShakenFistTestCase):
    """Tests for _migrate_keys_from_namespace_attributes()."""

    def test_two_keys_become_static_attribute_and_state_rows(self):
        """Each JSON entry produces three rows, values preserved verbatim."""
        conn = _migration_connection([
            _MockRow(name='banana', keys={'nonced_keys': {
                'first': {'key': 'aGFzaC1vbmU=', 'nonce': 'nonce-one'},
                'second': {'key': 'aGFzaC10d28=', 'nonce': 'nonce-two',
                           'expiry': 4242.0},
            }}),
        ])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(2, migrated)
        self.assertEqual(0, errors)

        # Two selects, then three inserts per key.
        self.assertEqual(8, conn.execute.call_count)

        statics = _inserts_for(conn, 'namespace_keys')
        self.assertEqual(2, len(statics))
        self.assertEqual(['banana', 'banana'],
                         [s['namespace'] for s in statics])
        self.assertEqual({'first', 'second'}, {s['name'] for s in statics})
        self.assertEqual([1, 1], [s['version'] for s in statics])
        self.assertEqual(2, len({s['uuid'] for s in statics}))

        attrs = _inserts_for(conn, 'namespace_key_attributes')
        self.assertEqual(2, len(attrs))
        by_uuid = {a['uuid']: a for a in attrs}
        for static in statics:
            attr = by_uuid[static['uuid']]
            if static['name'] == 'first':
                self.assertEqual('aGFzaC1vbmU=', attr['key'])
                self.assertEqual('nonce-one', attr['nonce'])
                self.assertIsNone(attr['expiry'])
            else:
                self.assertEqual('aGFzaC10d28=', attr['key'])
                self.assertEqual('nonce-two', attr['nonce'])
                self.assertEqual(4242.0, attr['expiry'])
            # Phase 3 populates these; the migration must not guess.
            self.assertIsNone(attr['scopes'])
            self.assertIsNone(attr['provenance'])

        states = _inserts_for(conn, 'object_states')
        self.assertEqual(2, len(states))
        self.assertEqual({str(s['uuid']) for s in statics},
                         {st['object_uuid'] for st in states})
        for state in states:
            self.assertEqual('created', state['state_value'])
            self.assertEqual(
                ObjectType.NAMESPACE_KEY, state['object_type'])

    def test_expired_keys_are_migrated_too(self):
        """Expiry is not a migration policy -- the reaper deals with it."""
        conn = _migration_connection([
            _MockRow(name='banana', keys={'nonced_keys': {
                'ancient': {'key': 'aGFzaA==', 'nonce': 'n', 'expiry': 1.0},
            }}),
        ])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(1, migrated)
        self.assertEqual(0, errors)
        self.assertEqual(
            [1.0], [a['expiry'] for a in
                    _inserts_for(conn, 'namespace_key_attributes')])

    def test_rerun_migrates_nothing(self):
        """Keys which already have a row are skipped, so a re-run is a no-op."""
        conn = _migration_connection(
            [
                _MockRow(name='banana', keys={'nonced_keys': {
                    'first': {'key': 'aGFzaC1vbmU=', 'nonce': 'nonce-one'},
                    'second': {'key': 'aGFzaC10d28=', 'nonce': 'nonce-two',
                               'expiry': 4242.0},
                }}),
            ],
            existing_rows=[
                _MockRow(namespace='banana', name='first'),
                _MockRow(namespace='banana', name='second'),
            ])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(0, migrated)
        self.assertEqual(0, errors)
        # The two selects and nothing else.
        self.assertEqual(2, conn.execute.call_count)

    def test_rotated_key_is_not_clobbered(self):
        """A key rotated after migration keeps its post-migration secret.

        The JSON column is still written by the pre-cutover code, so it
        can hold a stale hash for a key which the tables have since
        rotated. The migration must not write that stale hash back.
        """
        conn = _migration_connection(
            [
                _MockRow(name='banana', keys={'nonced_keys': {
                    'first': {'key': 'c3RhbGU=', 'nonce': 'stale'},
                }}),
            ],
            existing_rows=[_MockRow(namespace='banana', name='first')])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(0, migrated)
        self.assertEqual(0, errors)
        self.assertEqual([], _inserts_for(conn, 'namespace_key_attributes'))

    def test_namespace_with_no_keys_is_a_noop(self):
        """An empty or absent keys column migrates nothing and errors none."""
        conn = _migration_connection([
            _MockRow(name='banana', keys={'nonced_keys': {}}),
            _MockRow(name='sausage', keys={}),
            _MockRow(name='potato', keys=None),
        ])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(0, migrated)
        self.assertEqual(0, errors)
        self.assertEqual(2, conn.execute.call_count)

    def test_malformed_keys_column_is_counted_not_raised(self):
        """A keys column which is not a dict is an error, not an exception."""
        conn = _migration_connection([
            _MockRow(name='banana', keys=['not', 'a', 'dict']),
        ])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(0, migrated)
        self.assertEqual(1, errors)

    def test_missing_nonced_keys_is_counted_not_raised(self):
        """A populated keys dict without nonced_keys is malformed."""
        conn = _migration_connection([
            _MockRow(name='banana', keys={'legacy_keys': {'a': 'b'}}),
        ])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(0, migrated)
        self.assertEqual(1, errors)

    def test_malformed_nonced_keys_is_counted_not_raised(self):
        """nonced_keys which is not a dict is malformed."""
        conn = _migration_connection([
            _MockRow(name='banana', keys={'nonced_keys': 'oops'}),
        ])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(0, migrated)
        self.assertEqual(1, errors)

    def test_malformed_entry_is_skipped_and_the_rest_migrate(self):
        """One broken entry does not stop the namespace's other keys."""
        conn = _migration_connection([
            _MockRow(name='banana', keys={'nonced_keys': {
                'broken': {'nonce': 'no key here'},
                'alsobroken': 'not even a dict',
                'good': {'key': 'aGFzaA==', 'nonce': 'n'},
            }}),
        ])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(1, migrated)
        self.assertEqual(2, errors)
        self.assertEqual(
            ['good'],
            [s['name'] for s in _inserts_for(conn, 'namespace_keys')])

    def test_insert_failure_is_counted_not_raised(self):
        """A failing insert is logged and counted, and the pass continues."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _MockResult(rows=[
                _MockRow(name='banana', keys={'nonced_keys': {
                    'first': {'key': 'aGFzaA==', 'nonce': 'n'},
                    'second': {'key': 'aGFzaA==', 'nonce': 'n'},
                }})]),
            _MockResult(rows=[]),
            Exception('the database fell over'),
            _MockResult(), _MockResult(), _MockResult(),
        ])

        migrated, errors = mariadb._migrate_keys_from_namespace_attributes(
            _MockEngine(conn))

        self.assertEqual(1, migrated)
        self.assertEqual(1, errors)


# ---------------------------------------------------------------------------
# Find accessor (SQL pushdown of the expiry filter)
# ---------------------------------------------------------------------------

class DirectFindNamespaceKeysTestCase(base.ShakenFistTestCase):
    """Tests for _direct_find_namespace_keys()."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_expiry_filter_is_pushed_into_sql(self, mock_get_engine):
        """include_expired=False adds the expiry predicate to the WHERE."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_find_namespace_keys('banana', False, 1000.0)

        self.assertEqual(1, len(conn.executed))
        sql = str(conn.executed[0])
        self.assertIn('namespace_keys.namespace = ', sql)
        self.assertIn('namespace_key_attributes.expiry IS NULL', sql)
        self.assertIn('namespace_key_attributes.expiry > ', sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_include_expired_omits_the_expiry_filter(self, mock_get_engine):
        """include_expired=True leaves the expiry predicate out entirely."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_find_namespace_keys('banana', True, 1000.0)

        sql = str(conn.executed[0])
        self.assertIn('namespace_keys.namespace = ', sql)
        self.assertNotIn('expiry IS NULL', sql)
        self.assertNotIn('expiry > ', sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_now_is_bound_as_the_expiry_comparison_value(
            self, mock_get_engine):
        """The ``now`` argument is what expiry is compared against."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_find_namespace_keys('banana', False, 1234.5)

        params = conn.executed[0].compile().params
        self.assertIn(1234.5, params.values())

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_query_joins_static_to_attributes_on_uuid(self, mock_get_engine):
        """The listing is a single joined query, not an N+1 read."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_find_namespace_keys('banana', False, 1000.0)

        sql = str(conn.executed[0])
        self.assertIn('JOIN namespace_key_attributes', sql)
        self.assertIn(
            'namespace_key_attributes.uuid = namespace_keys.uuid', sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_rows_are_decoded_into_model_pairs(self, mock_get_engine):
        """Each row becomes a (static, attributes) model pair."""
        rows = [
            _MockRow(
                uuid=KEY_UUID_1, namespace='banana', name='first', version=1,
                key='aGFzaC1vbmU=', nonce='nonce-one', expiry=None,
                scopes=None, provenance=None),
            _MockRow(
                uuid=KEY_UUID_2, namespace='banana', name='second', version=1,
                key='aGFzaC10d28=', nonce='nonce-two', expiry=2000.0,
                scopes='["read"]', provenance='{"source": "oidc"}'),
        ]
        conn = _MockConnection(result=_MockResult(rows=rows))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_find_namespace_keys('banana', True, 0.0)

        self.assertEqual(2, len(results))
        first_static, first_attrs = results[0]
        self.assertEqual('first', first_static.name)
        self.assertEqual('banana', first_static.namespace)
        self.assertEqual(
            'aGFzaC1vbmU=', first_attrs.key.get_secret_value())
        self.assertIsNone(first_attrs.expiry)
        self.assertIsNone(first_attrs.scopes)
        self.assertIsNone(first_attrs.provenance)

        second_static, second_attrs = results[1]
        self.assertEqual('second', second_static.name)
        self.assertEqual(2000.0, second_attrs.expiry)
        self.assertEqual(['read'], second_attrs.scopes)
        self.assertEqual({'source': 'oidc'}, second_attrs.provenance)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_result_returns_empty_list(self, mock_get_engine):
        """No matching rows means an empty list, not None."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        self.assertEqual(
            [], mariadb._direct_find_namespace_keys('banana', False, 1.0))


# ---------------------------------------------------------------------------
# Point read by (namespace, name) -- the token validation hot path
# ---------------------------------------------------------------------------

class DirectGetNamespaceKeyByNameTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_namespace_key_by_name()."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_both_index_columns_are_in_the_where_clause(
            self, mock_get_engine):
        """The unique (namespace, name) index serves the lookup."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_get_namespace_key_by_name('banana', 'keyname')

        self.assertEqual(1, len(conn.executed))
        sql = str(conn.executed[0])
        self.assertIn('namespace_keys.namespace = ', sql)
        self.assertIn('namespace_keys.name = ', sql)
        self.assertIn('JOIN namespace_key_attributes', sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_expiry_is_not_filtered(self, mock_get_engine):
        """Expiry is check-at-use, so an expired key is still returned."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_get_namespace_key_by_name('banana', 'keyname')

        sql = str(conn.executed[0])
        self.assertNotIn('expiry', sql.split('WHERE')[-1])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_row_is_decoded_into_a_model_pair(self, mock_get_engine):
        conn = _MockConnection(result=_MockResult(rows=[
            _MockRow(
                uuid=KEY_UUID_1, namespace='banana', name='keyname',
                version=1, key='aGFzaC1vbmU=', nonce='nonce-one',
                expiry=2000.0, scopes='["read"]',
                provenance='{"source": "oidc"}')
        ]))
        mock_get_engine.return_value = _MockEngine(conn)

        row = mariadb._direct_get_namespace_key_by_name('banana', 'keyname')

        self.assertIsNotNone(row)
        static_data, attrs = row
        self.assertEqual('keyname', static_data.name)
        self.assertEqual('banana', static_data.namespace)
        self.assertEqual('nonce-one', attrs.nonce.get_secret_value())
        self.assertEqual(2000.0, attrs.expiry)
        self.assertEqual(['read'], attrs.scopes)
        self.assertEqual({'source': 'oidc'}, attrs.provenance)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_missing_row_returns_none(self, mock_get_engine):
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        self.assertIsNone(
            mariadb._direct_get_namespace_key_by_name('banana', 'nosuch'))


# ---------------------------------------------------------------------------
# Expiry reaping
# ---------------------------------------------------------------------------

class DirectDeleteExpiredNamespaceKeysTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_expired_namespace_keys()."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_deletes_from_both_tables_and_returns_count(
            self, mock_get_engine):
        """Expired uuids are removed from attributes and static tables."""
        conn = _MockConnection()
        mock_get_engine.return_value = _MockEngine(conn)
        conn.execute = mock.Mock(side_effect=[
            _MockResult(rows=[_MockRow(uuid=KEY_UUID_1),
                              _MockRow(uuid=KEY_UUID_2)]),
            _MockResult(rowcount=2),
            _MockResult(rowcount=2),
        ])

        count = mariadb._direct_delete_expired_namespace_keys(5000.0)

        self.assertEqual(2, count)
        self.assertEqual(3, conn.execute.call_count)

        select_sql = str(conn.execute.call_args_list[0].args[0])
        self.assertIn(
            'namespace_key_attributes.expiry IS NOT NULL', select_sql)
        self.assertIn('namespace_key_attributes.expiry < ', select_sql)

        attrs_delete = str(conn.execute.call_args_list[1].args[0])
        self.assertIn('DELETE FROM namespace_key_attributes', attrs_delete)

        static_delete = str(conn.execute.call_args_list[2].args[0])
        self.assertIn('DELETE FROM namespace_keys', static_delete)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_expired_keys_issues_no_deletes(self, mock_get_engine):
        """Nothing expired means the select runs and nothing else does."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        count = mariadb._direct_delete_expired_namespace_keys(5000.0)

        self.assertEqual(0, count)
        self.assertEqual(1, len(conn.executed))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_older_than_is_the_comparison_value(self, mock_get_engine):
        """The older_than argument is bound into the select predicate."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_delete_expired_namespace_keys(4242.0)

        params = conn.executed[0].compile().params
        self.assertIn(4242.0, params.values())


# ---------------------------------------------------------------------------
# Public accessor routing
# ---------------------------------------------------------------------------

class NamespaceKeyRoutingTestCase(base.ShakenFistTestCase):
    """The public accessors route on _use_database_service()."""

    @mock.patch('shakenfist.mariadb._grpc_create_namespace_key',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_create_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertTrue(mariadb.create_namespace_key(_SAMPLE_KEY))
        mock_grpc.assert_called_once_with(_SAMPLE_KEY)

    @mock.patch('shakenfist.mariadb._direct_create_namespace_key',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_create_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertTrue(mariadb.create_namespace_key(_SAMPLE_KEY))
        mock_direct.assert_called_once_with(_SAMPLE_KEY)

    @mock.patch('shakenfist.mariadb._grpc_get_namespace_key',
                return_value=_SAMPLE_KEY)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_get_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertEqual(_SAMPLE_KEY, mariadb.get_namespace_key(KEY_UUID_1))
        mock_grpc.assert_called_once_with(KEY_UUID_1)

    @mock.patch('shakenfist.mariadb._direct_get_namespace_key',
                return_value=_SAMPLE_KEY)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_get_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertEqual(_SAMPLE_KEY, mariadb.get_namespace_key(KEY_UUID_1))
        mock_direct.assert_called_once_with(KEY_UUID_1)

    @mock.patch('shakenfist.mariadb._grpc_get_namespace_key_by_name',
                return_value=(_SAMPLE_KEY, _SAMPLE_ATTRS))
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_get_by_name_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertEqual(
            (_SAMPLE_KEY, _SAMPLE_ATTRS),
            mariadb.get_namespace_key_by_name('banana', 'keyname'))
        mock_grpc.assert_called_once_with('banana', 'keyname')

    @mock.patch('shakenfist.mariadb._direct_get_namespace_key_by_name',
                return_value=(_SAMPLE_KEY, _SAMPLE_ATTRS))
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_get_by_name_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertEqual(
            (_SAMPLE_KEY, _SAMPLE_ATTRS),
            mariadb.get_namespace_key_by_name('banana', 'keyname'))
        mock_direct.assert_called_once_with('banana', 'keyname')

    @mock.patch('shakenfist.mariadb._grpc_delete_namespace_key',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_delete_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertTrue(mariadb.delete_namespace_key(KEY_UUID_1))
        mock_grpc.assert_called_once_with(KEY_UUID_1)

    @mock.patch('shakenfist.mariadb._direct_delete_namespace_key',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_delete_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertTrue(mariadb.delete_namespace_key(KEY_UUID_1))
        mock_direct.assert_called_once_with(KEY_UUID_1)

    @mock.patch('shakenfist.mariadb._grpc_find_namespace_keys',
                return_value=[])
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_find_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertEqual(
            [], mariadb.find_namespace_keys('banana', now=99.0))
        mock_grpc.assert_called_once_with('banana', False, 99.0)

    @mock.patch('shakenfist.mariadb._direct_find_namespace_keys',
                return_value=[])
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_find_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertEqual(
            [], mariadb.find_namespace_keys(
                'banana', include_expired=True, now=99.0))
        mock_direct.assert_called_once_with('banana', True, 99.0)

    @mock.patch('shakenfist.mariadb.time.time', return_value=555.0)
    @mock.patch('shakenfist.mariadb._direct_find_namespace_keys',
                return_value=[])
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_find_defaults_now_to_the_current_time(
            self, mock_use_svc, mock_direct, mock_time):
        mariadb.find_namespace_keys('banana')
        mock_direct.assert_called_once_with('banana', False, 555.0)

    @mock.patch('shakenfist.mariadb._grpc_delete_expired_namespace_keys',
                return_value=3)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_delete_expired_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertEqual(3, mariadb.delete_expired_namespace_keys(10.0))
        mock_grpc.assert_called_once_with(10.0)

    @mock.patch('shakenfist.mariadb._direct_delete_expired_namespace_keys',
                return_value=3)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_delete_expired_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertEqual(3, mariadb.delete_expired_namespace_keys(10.0))
        mock_direct.assert_called_once_with(10.0)


class NamespaceKeyAttributesRoutingTestCase(base.ShakenFistTestCase):
    """The public attribute accessors route on _use_database_service()."""

    @mock.patch('shakenfist.mariadb._grpc_create_namespace_key_attributes',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_create_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertTrue(
            mariadb.create_namespace_key_attributes(_SAMPLE_ATTRS))
        mock_grpc.assert_called_once_with(_SAMPLE_ATTRS)

    @mock.patch('shakenfist.mariadb._direct_create_namespace_key_attributes',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_create_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertTrue(
            mariadb.create_namespace_key_attributes(_SAMPLE_ATTRS))
        mock_direct.assert_called_once_with(_SAMPLE_ATTRS)

    @mock.patch('shakenfist.mariadb._grpc_get_namespace_key_attributes',
                return_value=_SAMPLE_ATTRS)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_get_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertEqual(
            _SAMPLE_ATTRS,
            mariadb.get_namespace_key_attributes(KEY_UUID_1))
        mock_grpc.assert_called_once_with(KEY_UUID_1)

    @mock.patch('shakenfist.mariadb._direct_get_namespace_key_attributes',
                return_value=_SAMPLE_ATTRS)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_get_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertEqual(
            _SAMPLE_ATTRS,
            mariadb.get_namespace_key_attributes(KEY_UUID_1))
        mock_direct.assert_called_once_with(KEY_UUID_1)

    @mock.patch('shakenfist.mariadb._grpc_update_namespace_key_attributes',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_update_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertTrue(
            mariadb.update_namespace_key_attributes(_SAMPLE_ATTRS))
        mock_grpc.assert_called_once_with(_SAMPLE_ATTRS)

    @mock.patch('shakenfist.mariadb._direct_update_namespace_key_attributes',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_update_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertTrue(
            mariadb.update_namespace_key_attributes(_SAMPLE_ATTRS))
        mock_direct.assert_called_once_with(_SAMPLE_ATTRS)

    @mock.patch('shakenfist.mariadb._grpc_delete_namespace_key_attributes',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_delete_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertTrue(
            mariadb.delete_namespace_key_attributes(KEY_UUID_1))
        mock_grpc.assert_called_once_with(KEY_UUID_1)

    @mock.patch('shakenfist.mariadb._direct_delete_namespace_key_attributes',
                return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_delete_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertTrue(
            mariadb.delete_namespace_key_attributes(KEY_UUID_1))
        mock_direct.assert_called_once_with(KEY_UUID_1)


# ---------------------------------------------------------------------------
# Attribute writes (nullable JSON columns)
# ---------------------------------------------------------------------------

class DirectNamespaceKeyAttributesTestCase(base.ShakenFistTestCase):
    """Nullable scopes / provenance survive the write path as NULL."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_writes_null_for_absent_json_fields(self, mock_get_engine):
        conn = _MockConnection()
        mock_get_engine.return_value = _MockEngine(conn)

        self.assertTrue(
            mariadb._direct_create_namespace_key_attributes(_SAMPLE_ATTRS))

        params = conn.executed[0].compile().params
        self.assertIsNone(params['scopes'])
        self.assertIsNone(params['provenance'])
        self.assertIsNone(params['expiry'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_json_encodes_populated_fields(self, mock_get_engine):
        conn = _MockConnection()
        mock_get_engine.return_value = _MockEngine(conn)
        attrs = NamespaceKeyAttributesData(
            uuid=KEY_UUID_1, key='aGFzaA==', nonce='n', expiry=17.0,
            scopes=['read', 'write'], provenance={'source': 'oidc'})

        self.assertTrue(
            mariadb._direct_create_namespace_key_attributes(attrs))

        params = conn.executed[0].compile().params
        self.assertEqual('["read", "write"]', params['scopes'])
        self.assertEqual('{"source": "oidc"}', params['provenance'])
        self.assertEqual(17.0, params['expiry'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_decodes_json_columns(self, mock_get_engine):
        row = _MockRow(
            uuid=KEY_UUID_1, key='aGFzaA==', nonce='n', expiry=17.0,
            scopes='["read"]', provenance='{"source": "oidc"}')
        conn = _MockConnection(result=_MockResult(rows=[row]))
        mock_get_engine.return_value = _MockEngine(conn)

        attrs = mariadb._direct_get_namespace_key_attributes(KEY_UUID_1)

        self.assertIsNotNone(attrs)
        self.assertEqual(['read'], attrs.scopes)
        self.assertEqual({'source': 'oidc'}, attrs.provenance)
        self.assertEqual(17.0, attrs.expiry)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_get_missing_row_returns_none(self, mock_get_engine):
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        self.assertIsNone(
            mariadb._direct_get_namespace_key_attributes(KEY_UUID_1))


# ---------------------------------------------------------------------------
# Proto round trip (nullable fields use proto3 field presence)
# ---------------------------------------------------------------------------

class NamespaceKeyProtoRoundTripTestCase(base.ShakenFistTestCase):
    """NULL attribute fields must survive the gRPC hop as NULL."""

    def test_static_round_trip(self):
        proto = mariadb._namespace_key_to_proto(_SAMPLE_KEY)
        self.assertEqual(
            _SAMPLE_KEY, mariadb._namespace_key_from_proto(proto))

    def test_attributes_round_trip_with_nulls(self):
        proto = mariadb._namespace_key_attrs_to_proto(_SAMPLE_ATTRS)
        self.assertFalse(proto.HasField('expiry'))
        self.assertFalse(proto.HasField('scopes_json'))
        self.assertFalse(proto.HasField('provenance_json'))

        self.assertEqual(
            _SAMPLE_ATTRS, mariadb._namespace_key_attrs_from_proto(proto))

    def test_attributes_round_trip_with_values(self):
        attrs = NamespaceKeyAttributesData(
            uuid=KEY_UUID_1, key='aGFzaA==', nonce='n', expiry=17.0,
            scopes=[], provenance={'source': 'oidc'})

        proto = mariadb._namespace_key_attrs_to_proto(attrs)
        self.assertTrue(proto.HasField('expiry'))
        # An empty scopes list is not the same thing as NULL: NULL means
        # unscoped/wildcard, [] means no scopes at all.
        self.assertTrue(proto.HasField('scopes_json'))

        self.assertEqual(
            attrs, mariadb._namespace_key_attrs_from_proto(proto))


# ---------------------------------------------------------------------------
# Secret material must cross each storage boundary as plaintext
# ---------------------------------------------------------------------------

class SecretMaterialBoundaryTestCase(base.ShakenFistTestCase):
    """The hash and the nonce must be unwrapped on the way out.

    These fields are SecretStr on the model, which is what stops them
    being stringified into a log line by accident. The flip side is that
    a missed unwrap on a write path would persist the literal
    '**********' -- the key would silently stop matching any secret, and
    every token minted from it would stop validating. That failure is
    quiet at the point it happens and only shows up later as an
    authentication outage, so each boundary is asserted directly rather
    than through a round trip which could mask a symmetric mistake.
    """

    MASK = '**********'

    def _executed_params(self, method, data):
        conn = _MockConnection(_MockResult(rowcount=1))
        with mock.patch.object(mariadb, '_get_engine',
                               return_value=_MockEngine(conn)):
            method(data)
        self.assertEqual(1, len(conn.executed))
        return conn.executed[0].compile().params

    def test_insert_binds_plaintext_not_the_mask(self):
        params = self._executed_params(
            mariadb._direct_create_namespace_key_attributes, _SAMPLE_ATTRS)

        self.assertEqual('JDJiJDEyJGZha2VoYXNo', params['key'])
        self.assertEqual('deadbeef', params['nonce'])
        self.assertIsInstance(params['key'], str)
        self.assertIsInstance(params['nonce'], str)
        self.assertNotEqual(self.MASK, params['key'])
        self.assertNotEqual(self.MASK, params['nonce'])

    def test_update_binds_plaintext_not_the_mask(self):
        params = self._executed_params(
            mariadb._direct_update_namespace_key_attributes, _SAMPLE_ATTRS)

        self.assertEqual('JDJiJDEyJGZha2VoYXNo', params['key'])
        self.assertEqual('deadbeef', params['nonce'])
        self.assertNotEqual(self.MASK, params['key'])
        self.assertNotEqual(self.MASK, params['nonce'])

    def test_proto_carries_plaintext_on_the_wire(self):
        # The existing round-trip tests above would catch a symmetric
        # mistake, but not one where both sides agreed on the mask. This
        # looks at the proto field itself.
        proto = mariadb._namespace_key_attrs_to_proto(_SAMPLE_ATTRS)

        self.assertEqual('JDJiJDEyJGZha2VoYXNo', proto.key)
        self.assertEqual('deadbeef', proto.nonce)
        self.assertNotEqual(self.MASK, proto.key)
        self.assertNotEqual(self.MASK, proto.nonce)

    def test_decoded_model_rewraps_the_secret(self):
        # And the other direction: values arriving from the wire come back
        # wrapped, so nothing downstream of the transport holds a bare
        # string it could log.
        attrs = mariadb._namespace_key_attrs_from_proto(
            mariadb._namespace_key_attrs_to_proto(_SAMPLE_ATTRS))

        self.assertIsInstance(attrs.key, SecretStr)
        self.assertIsInstance(attrs.nonce, SecretStr)
        self.assertEqual('JDJiJDEyJGZha2VoYXNo', attrs.key.get_secret_value())
        self.assertEqual('deadbeef', attrs.nonce.get_secret_value())
        # ...and rendering it gives the mask, which is the whole point.
        self.assertEqual(self.MASK, str(attrs.key))
