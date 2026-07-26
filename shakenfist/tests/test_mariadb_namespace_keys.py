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

from shakenfist import mariadb
from shakenfist.schema.namespace_key_attributes import NamespaceKeyAttributesData
from shakenfist.schema.namespace_key_data import NamespaceKeyData
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

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_fresh_engine_creates_table_and_returns_version_1(
            self, mock_get_version, mock_set_version):
        """From version 0 the table is created and lands at v1."""
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all'):
            result = mariadb._ensure_namespace_keys_schema(mock_engine)

        self.assertEqual(result['table'], 'namespace_keys')
        self.assertEqual(result['target_version'], 1)
        self.assertEqual(result['start_version'], 0)
        self.assertEqual(result['end_version'], 1)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(
            mock_engine, 'namespace_keys', 1)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_creation_emits_composite_unique_index_ddl(
            self, mock_get_version, mock_set_version):
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

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_already_at_version_1_is_noop(
            self, mock_get_version, mock_set_version):
        """Table already at v1: no DDL, no version bump, migrated=False."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_namespace_keys_schema(mock_engine)

        self.assertEqual(result['table'], 'namespace_keys')
        self.assertEqual(result['target_version'], 1)
        self.assertEqual(result['end_version'], 1)
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()
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

    def test_expected_schema_versions_lists_both_tables_at_1(self):
        """Both tables are registered in EXPECTED_SCHEMA_VERSIONS at v1."""
        self.assertEqual(
            1, mariadb.EXPECTED_SCHEMA_VERSIONS['namespace_keys'])
        self.assertEqual(
            1, mariadb.EXPECTED_SCHEMA_VERSIONS['namespace_key_attributes'])


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
        self.assertEqual('aGFzaC1vbmU=', first_attrs.key)
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
        self.assertEqual('nonce-one', attrs.nonce)
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
