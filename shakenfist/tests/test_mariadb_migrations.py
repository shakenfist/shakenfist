# Tests for the mariadb data migration framework.
#
# This module tests:
# - ensure_data_migrations() with empty registry
# - ensure_data_migrations() without MARIADB_HOST configured
# - ensure_data_migrations() skipping non-existent tables
# - ensure_data_migrations() skipping already-completed migrations
# - ensure_data_migrations() running pending migrations
# - ensure_data_migrations() handling migration failures
# - ensure_data_migrations() running migrations in version order

from unittest import mock

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    DATABASE_NODE_IP: str = '192.168.1.1'
    DATABASE_API_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    ETCD_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


class FakeConfigNoMariaDB(BaseSettings):
    DATABASE_NODE_IP: str = '192.168.1.1'
    DATABASE_API_PORT: int = 13005
    MARIADB_HOST: str = ''
    ETCD_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


class FakeConfigNoEtcd(BaseSettings):
    DATABASE_NODE_IP: str = '192.168.1.1'
    DATABASE_API_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    ETCD_HOST: str = ''
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()
fake_config_no_mariadb = FakeConfigNoMariaDB()
fake_config_no_etcd = FakeConfigNoEtcd()


class EnsureDataMigrationsEmptyRegistryTestCase(base.ShakenFistTestCase):
    """Tests for ensure_data_migrations() with empty registry."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    def test_empty_registry_returns_empty_list(self):
        # Ensure DATA_MIGRATIONS is empty for this test
        with mock.patch.dict(mariadb.DATA_MIGRATIONS, {}, clear=True):
            result = mariadb.ensure_data_migrations()
            self.assertEqual(result, [])


class EnsureDataMigrationsNoMariaDBTestCase(base.ShakenFistTestCase):
    """Tests for ensure_data_migrations() without MariaDB configured."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch(
            'shakenfist.mariadb.config', fake_config_no_mariadb)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    def test_no_mariadb_raises_runtime_error(self):
        # Register a migration so the empty check doesn't short-circuit
        def dummy_migration(engine):
            return {'migrated_count': 0, 'error_count': 0}

        with mock.patch.dict(
                mariadb.DATA_MIGRATIONS,
                {'test_table': {2: dummy_migration}},
                clear=True):
            self.assertRaises(RuntimeError, mariadb.ensure_data_migrations)


class EnsureDataMigrationsNoEtcdTestCase(base.ShakenFistTestCase):
    """ensure_data_migrations() short-circuits when ETCD_HOST is unset."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch(
            'shakenfist.mariadb.config', fake_config_no_etcd)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_etcd_marks_pending_migrations_complete(
            self, mock_get_engine, mock_get_version, mock_set_version):
        mock_get_engine.return_value = mock.MagicMock()
        mock_get_version.return_value = 1

        migration_called = []

        def dummy_migration(engine):
            migration_called.append(True)
            return {'migrated_count': 0, 'error_count': 0}

        with mock.patch.dict(
                mariadb.DATA_MIGRATIONS,
                {'test_table': {2: dummy_migration}},
                clear=True):
            result = mariadb.ensure_data_migrations()

        # Migration function must not run when there is no etcd to drain.
        self.assertEqual(migration_called, [])
        # Version is bumped so we do not retry on the next restart.
        mock_set_version.assert_called_once_with(mock.ANY, 'test_table', 2)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['migrated'])
        self.assertEqual(result[0]['from_version'], 1)
        self.assertEqual(result[0]['to_version'], 2)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_etcd_skips_nonexistent_tables(
            self, mock_get_engine, mock_get_version, mock_set_version):
        mock_get_engine.return_value = mock.MagicMock()
        mock_get_version.return_value = 0

        def dummy_migration(engine):
            return {'migrated_count': 0, 'error_count': 0}

        with mock.patch.dict(
                mariadb.DATA_MIGRATIONS,
                {'test_table': {2: dummy_migration}},
                clear=True):
            result = mariadb.ensure_data_migrations()

        mock_set_version.assert_not_called()
        self.assertEqual(result, [])


class EnsureDataMigrationsSkipTestCase(base.ShakenFistTestCase):
    """Tests for ensure_data_migrations() skipping migrations."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_skips_nonexistent_table(self, mock_get_version, mock_get_engine):
        # Table doesn't exist (version 0)
        mock_get_version.return_value = 0
        mock_engine = mock.MagicMock()
        mock_get_engine.return_value = mock_engine

        migration_called = []

        def dummy_migration(engine):
            migration_called.append(True)
            return {'migrated_count': 0, 'error_count': 0}

        with mock.patch.dict(
                mariadb.DATA_MIGRATIONS,
                {'test_table': {2: dummy_migration}},
                clear=True):
            result = mariadb.ensure_data_migrations()

        # Migration should not have been called
        self.assertEqual(migration_called, [])
        # No results since table was skipped
        self.assertEqual(result, [])

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_skips_completed_migration(self, mock_get_version, mock_get_engine):
        # Table already at version 2
        mock_get_version.return_value = 2
        mock_engine = mock.MagicMock()
        mock_get_engine.return_value = mock_engine

        migration_called = []

        def dummy_migration(engine):
            migration_called.append(True)
            return {'migrated_count': 0, 'error_count': 0}

        with mock.patch.dict(
                mariadb.DATA_MIGRATIONS,
                {'test_table': {2: dummy_migration}},
                clear=True):
            result = mariadb.ensure_data_migrations()

        # Migration should not have been called (already at target version)
        self.assertEqual(migration_called, [])
        self.assertEqual(result, [])


class EnsureDataMigrationsRunTestCase(base.ShakenFistTestCase):
    """Tests for ensure_data_migrations() running migrations."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_runs_pending_migration(
            self, mock_get_version, mock_set_version, mock_get_engine):
        # Table at version 1, migration targets version 2
        mock_get_version.return_value = 1
        mock_engine = mock.MagicMock()
        mock_get_engine.return_value = mock_engine

        migration_called = []

        def dummy_migration(engine):
            migration_called.append(engine)
            return {'migrated_count': 5, 'error_count': 0}

        with mock.patch.dict(
                mariadb.DATA_MIGRATIONS,
                {'test_table': {2: dummy_migration}},
                clear=True):
            result = mariadb.ensure_data_migrations()

        # Migration should have been called with the engine
        self.assertEqual(len(migration_called), 1)
        self.assertEqual(migration_called[0], mock_engine)

        # Version should have been updated
        mock_set_version.assert_called_once_with(mock_engine, 'test_table', 2)

        # Result should show successful migration
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['table'], 'test_table')
        self.assertEqual(result[0]['from_version'], 1)
        self.assertEqual(result[0]['to_version'], 2)
        self.assertTrue(result[0]['migrated'])
        self.assertEqual(result[0]['stats']['migrated_count'], 5)

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_runs_migrations_in_version_order(
            self, mock_get_version, mock_set_version, mock_get_engine):
        # Table at version 1, migrations for v2 and v3
        mock_get_version.return_value = 1
        mock_engine = mock.MagicMock()
        mock_get_engine.return_value = mock_engine

        migration_order = []

        def migration_v2(engine):
            migration_order.append(2)
            return {'migrated_count': 1, 'error_count': 0}

        def migration_v3(engine):
            migration_order.append(3)
            return {'migrated_count': 2, 'error_count': 0}

        # Register in reverse order to test sorting
        with mock.patch.dict(
                mariadb.DATA_MIGRATIONS,
                {'test_table': {3: migration_v3, 2: migration_v2}},
                clear=True):
            result = mariadb.ensure_data_migrations()

        # Migrations should run in version order (2 then 3)
        self.assertEqual(migration_order, [2, 3])
        self.assertEqual(len(result), 2)


class EnsureDataMigrationsFailureTestCase(base.ShakenFistTestCase):
    """Tests for ensure_data_migrations() handling failures."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_failed_migration_does_not_update_version(
            self, mock_get_version, mock_set_version, mock_get_engine):
        # Table at version 1
        mock_get_version.return_value = 1
        mock_engine = mock.MagicMock()
        mock_get_engine.return_value = mock_engine

        def failing_migration(engine):
            raise ValueError('Migration failed!')

        with mock.patch.dict(
                mariadb.DATA_MIGRATIONS,
                {'test_table': {2: failing_migration}},
                clear=True):
            result = mariadb.ensure_data_migrations()

        # Version should NOT have been updated
        mock_set_version.assert_not_called()

        # Result should show failed migration
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['table'], 'test_table')
        self.assertFalse(result[0]['migrated'])
        self.assertIn('Migration failed!', result[0]['error'])

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_failure_stops_subsequent_migrations_for_table(
            self, mock_get_version, mock_set_version, mock_get_engine):
        # Table at version 1, migrations for v2 and v3
        mock_get_version.return_value = 1
        mock_engine = mock.MagicMock()
        mock_get_engine.return_value = mock_engine

        migration_order = []

        def migration_v2(engine):
            migration_order.append(2)
            raise ValueError('v2 failed!')

        def migration_v3(engine):
            migration_order.append(3)
            return {'migrated_count': 0, 'error_count': 0}

        with mock.patch.dict(
                mariadb.DATA_MIGRATIONS,
                {'test_table': {2: migration_v2, 3: migration_v3}},
                clear=True):
            result = mariadb.ensure_data_migrations()

        # Only v2 should have been attempted (v3 skipped due to failure)
        self.assertEqual(migration_order, [2])
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]['migrated'])


class EnsureObjectMetadataSchemaTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_object_metadata_schema() v2->v3 migration."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    def _make_mock_engine(self):
        """Build a mock engine whose connect() is a usable context manager."""
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        return mock_engine, mock_conn

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_v2_to_v3_runs_drop_column(
            self, mock_get_version, mock_set_version):
        """v2->v3 ALTER TABLE DROP COLUMN DDL must be issued and committed."""
        mock_get_version.return_value = 2
        mock_engine, mock_conn = self._make_mock_engine()

        mariadb._ensure_object_metadata_schema(mock_engine)

        # Verify the right DDL was executed.
        self.assertEqual(mock_conn.execute.call_count, 1)
        call_args = mock_conn.execute.call_args
        # sa.text() objects compare by their string, accessible via .text
        executed_sql = str(call_args[0][0])
        self.assertIn('ALTER TABLE', executed_sql)
        self.assertIn('DROP COLUMN IF EXISTS', executed_sql)
        self.assertIn('last_cluster_operation_json', executed_sql)

        # commit must be issued after the DDL
        mock_conn.commit.assert_called_once()

        # version must be bumped to 3
        mock_set_version.assert_called_once_with(
            mock_engine, 'object_metadata', 3)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_already_v3_is_noop(
            self, mock_get_version, mock_set_version):
        """Already at v3: no DDL and no version bump."""
        mock_get_version.return_value = 3
        mock_engine, mock_conn = self._make_mock_engine()

        mariadb._ensure_object_metadata_schema(mock_engine)

        mock_conn.execute.assert_not_called()
        mock_set_version.assert_not_called()

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version')
    def test_v0_creates_then_migrates(
            self, mock_get_version, mock_set_version):
        """Table at v0: create_all runs first, then v2->v3 migration."""
        mock_get_version.return_value = 0
        mock_engine, mock_conn = self._make_mock_engine()

        # Suppress the create_all call on the SQLAlchemy metadata object so
        # we don't need a real database dialect.
        with mock.patch('sqlalchemy.MetaData.create_all'):
            mariadb._ensure_object_metadata_schema(mock_engine)

        # _set_table_version must be called twice: once for v1 and once for v3
        self.assertEqual(mock_set_version.call_count, 2)
        calls = mock_set_version.call_args_list
        # First call: set to 1 after create_all
        self.assertEqual(calls[0], mock.call(mock_engine, 'object_metadata', 1))
        # Second call: set to 3 after DROP COLUMN
        self.assertEqual(calls[1], mock.call(mock_engine, 'object_metadata', 3))
