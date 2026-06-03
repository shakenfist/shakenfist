# Tests for the client/ctl.py CLI module.
#
# This module tests:
# - MigrationStats dataclass methods
# - parse_uuid helper function
# - migration_precheck and migration_postcheck functions
# - Click command argument validation
#
# Note: The ctl module runs verify_config() on import, so we need to mock
# the config module before importing any ctl components.

import sys
from unittest import mock

from click.testing import CliRunner

from shakenfist.tests import base


# Create a mock config that satisfies verify_config requirements
class MockConfig:
    STORAGE_PATH = '/tmp/sf-test'
    NODE_NAME = 'test-node'
    NODE_MESH_IP = '10.0.0.1'
    MARIADB_HOST = ''
    MARIADB_GATEWAY_HOSTS = ['192.168.1.1']
    MARIADB_GATEWAY_PORT = 13005


def _import_ctl_module():
    """Import ctl module with config verification bypassed.

    The ctl module runs verify_config() on import, which fails in test
    environments. We need to mock the config module before importing.
    """
    # Remove ctl from sys.modules if it was previously imported
    if 'shakenfist.client.ctl' in sys.modules:
        del sys.modules['shakenfist.client.ctl']

    # Mock sf_config.verify_config to prevent sys.exit()
    with mock.patch.dict('sys.modules', {
        'shakenfist.config': mock.MagicMock(
            config=MockConfig(),
            verify_config=mock.MagicMock()
        )
    }):
        # Now we can safely import the ctl module components we need
        # But this approach won't work because the module is already loaded
        pass

    # Alternative: just use a fresh import with mocked verify_config
    return None


class MigrationStatsTestCase(base.ShakenFistTestCase):
    """Tests for MigrationStats dataclass."""

    @classmethod
    def setUpClass(cls):
        # Patch verify_config before any imports from ctl
        cls.verify_config_patcher = mock.patch(
            'shakenfist.config.verify_config', mock.MagicMock())
        cls.verify_config_patcher.start()

        # Force reimport of ctl module
        if 'shakenfist.client.ctl' in sys.modules:
            del sys.modules['shakenfist.client.ctl']

    @classmethod
    def tearDownClass(cls):
        cls.verify_config_patcher.stop()

    def setUp(self):
        super().setUp()
        # Import after patching
        from shakenfist.client.ctl import MigrationStats
        self.MigrationStats = MigrationStats

    def test_initial_values(self):
        stats = self.MigrationStats()
        self.assertEqual(stats.migrated, 0)
        self.assertEqual(stats.skipped, 0)
        self.assertEqual(stats.errors, 0)
        self.assertEqual(stats.categories, {})
        self.assertEqual(stats.progress_interval, 100)

    def test_add_category(self):
        stats = self.MigrationStats()
        stats.add_category('DISK')
        stats.add_category('BLOB')
        self.assertEqual(stats.categories, {'DISK': 0, 'BLOB': 0})

    def test_record_migrated_without_category(self):
        stats = self.MigrationStats()
        stats.record_migrated()
        stats.record_migrated()
        self.assertEqual(stats.migrated, 2)

    def test_record_migrated_with_category(self):
        stats = self.MigrationStats()
        stats.add_category('DISK')
        stats.record_migrated('DISK')
        stats.record_migrated('DISK')
        self.assertEqual(stats.migrated, 2)
        self.assertEqual(stats.categories['DISK'], 2)

    def test_record_migrated_with_unknown_category(self):
        stats = self.MigrationStats()
        stats.add_category('DISK')
        stats.record_migrated('UNKNOWN')
        self.assertEqual(stats.migrated, 1)
        self.assertEqual(stats.categories['DISK'], 0)

    def test_record_skipped(self):
        stats = self.MigrationStats()
        stats.record_skipped()
        stats.record_skipped()
        self.assertEqual(stats.skipped, 2)

    def test_record_error(self):
        stats = self.MigrationStats()
        with mock.patch('click.echo') as mock_echo:
            stats.record_error('Test error')
            stats.record_error('Another error')
        self.assertEqual(stats.errors, 2)
        self.assertEqual(mock_echo.call_count, 2)

    def test_total_processed(self):
        stats = self.MigrationStats()
        stats.record_migrated()
        stats.record_migrated()
        stats.record_skipped()
        with mock.patch('click.echo'):
            stats.record_error('error')
        self.assertEqual(stats.total_processed, 4)

    def test_should_show_progress_at_interval(self):
        stats = self.MigrationStats(progress_interval=10)
        # Process 10 items
        for _ in range(10):
            stats.record_migrated()
        self.assertTrue(stats.should_show_progress())

    def test_should_show_progress_not_at_interval(self):
        stats = self.MigrationStats(progress_interval=10)
        # Process 5 items
        for _ in range(5):
            stats.record_migrated()
        self.assertFalse(stats.should_show_progress())

    def test_should_show_progress_zero(self):
        stats = self.MigrationStats()
        self.assertFalse(stats.should_show_progress())

    def test_show_progress_at_interval(self):
        stats = self.MigrationStats(progress_interval=10)
        for _ in range(10):
            stats.record_migrated()
        with mock.patch('click.echo') as mock_echo:
            stats.show_progress('objects')
        mock_echo.assert_called_once_with('  ... 10 objects processed')

    def test_show_progress_not_at_interval(self):
        stats = self.MigrationStats(progress_interval=10)
        for _ in range(5):
            stats.record_migrated()
        with mock.patch('click.echo') as mock_echo:
            stats.show_progress('objects')
        mock_echo.assert_not_called()

    def test_print_summary_with_categories(self):
        stats = self.MigrationStats()
        stats.add_category('DISK')
        stats.add_category('BLOB')
        stats.record_migrated('DISK')
        stats.record_migrated('DISK')
        stats.record_migrated('BLOB')
        stats.record_skipped()
        with mock.patch('click.echo'):
            stats.record_error('error')
        with mock.patch('click.echo') as mock_echo:
            stats.print_summary()
        # Should have multiple echo calls for summary
        self.assertGreater(mock_echo.call_count, 0)

    def test_print_summary_without_categories(self):
        stats = self.MigrationStats()
        stats.record_migrated()
        stats.record_skipped()
        with mock.patch('click.echo') as mock_echo:
            stats.print_summary()
        self.assertGreater(mock_echo.call_count, 0)


class ParseUuidTestCase(base.ShakenFistTestCase):
    """Tests for parse_uuid helper function."""

    @classmethod
    def setUpClass(cls):
        cls.verify_config_patcher = mock.patch(
            'shakenfist.config.verify_config', mock.MagicMock())
        cls.verify_config_patcher.start()

        if 'shakenfist.client.ctl' in sys.modules:
            del sys.modules['shakenfist.client.ctl']

    @classmethod
    def tearDownClass(cls):
        cls.verify_config_patcher.stop()

    def setUp(self):
        super().setUp()
        from shakenfist.client.ctl import parse_uuid
        self.parse_uuid = parse_uuid

    def test_valid_uuid(self):
        valid = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
        result = self.parse_uuid(valid)
        self.assertIsNotNone(result)
        self.assertEqual(str(result), valid)

    def test_valid_uuid_uppercase(self):
        valid = 'A1B2C3D4-E5F6-7890-ABCD-EF1234567890'
        result = self.parse_uuid(valid)
        self.assertIsNotNone(result)

    def test_invalid_uuid(self):
        invalid = 'not-a-uuid'
        result = self.parse_uuid(invalid)
        self.assertIsNone(result)

    def test_empty_string(self):
        result = self.parse_uuid('')
        self.assertIsNone(result)

    def test_partial_uuid(self):
        partial = 'a1b2c3d4-e5f6'
        result = self.parse_uuid(partial)
        self.assertIsNone(result)


class MigrationPrecheckTestCase(base.ShakenFistTestCase):
    """Tests for migration_precheck function."""

    @classmethod
    def setUpClass(cls):
        cls.verify_config_patcher = mock.patch(
            'shakenfist.config.verify_config', mock.MagicMock())
        cls.verify_config_patcher.start()

        if 'shakenfist.client.ctl' in sys.modules:
            del sys.modules['shakenfist.client.ctl']

    @classmethod
    def tearDownClass(cls):
        cls.verify_config_patcher.stop()

    def setUp(self):
        super().setUp()
        from shakenfist.client.ctl import migration_precheck
        self.migration_precheck = migration_precheck

    @mock.patch('shakenfist.client.ctl.mariadb')
    @mock.patch('click.echo')
    def test_precheck_dry_run(self, mock_echo, mock_mariadb):
        result = self.migration_precheck(dry_run=True)
        self.assertTrue(result)
        mock_mariadb.ensure_schema.assert_not_called()

    @mock.patch('shakenfist.client.ctl.mariadb')
    @mock.patch('click.echo')
    def test_precheck_not_dry_run(self, mock_echo, mock_mariadb):
        result = self.migration_precheck(dry_run=False)
        self.assertTrue(result)
        mock_mariadb.ensure_schema.assert_called_once()
        mock_echo.assert_called_once()


class MigrationPostcheckTestCase(base.ShakenFistTestCase):
    """Tests for migration_postcheck function."""

    @classmethod
    def setUpClass(cls):
        cls.verify_config_patcher = mock.patch(
            'shakenfist.config.verify_config', mock.MagicMock())
        cls.verify_config_patcher.start()

        if 'shakenfist.client.ctl' in sys.modules:
            del sys.modules['shakenfist.client.ctl']

    @classmethod
    def tearDownClass(cls):
        cls.verify_config_patcher.stop()

    def setUp(self):
        super().setUp()
        from shakenfist.client.ctl import migration_postcheck
        self.migration_postcheck = migration_postcheck

    @mock.patch('click.echo')
    def test_postcheck_dry_run(self, mock_echo):
        self.migration_postcheck(dry_run=True)
        mock_echo.assert_called_once()
        self.assertIn('dry run', mock_echo.call_args[0][0].lower())

    @mock.patch('click.echo')
    def test_postcheck_not_dry_run(self, mock_echo):
        self.migration_postcheck(dry_run=False)
        mock_echo.assert_called_once()
        self.assertIn('complete', mock_echo.call_args[0][0].lower())


class CliCommandsTestCase(base.ShakenFistTestCase):
    """Tests for Click CLI commands."""

    @classmethod
    def setUpClass(cls):
        cls.verify_config_patcher = mock.patch(
            'shakenfist.config.verify_config', mock.MagicMock())
        cls.verify_config_patcher.start()

        if 'shakenfist.client.ctl' in sys.modules:
            del sys.modules['shakenfist.client.ctl']

    @classmethod
    def tearDownClass(cls):
        cls.verify_config_patcher.stop()

    def setUp(self):
        super().setUp()
        self.runner = CliRunner()

    @mock.patch('shakenfist.client.ctl.Namespace')
    def test_bootstrap_system_key(self, mock_namespace):
        from shakenfist.client.ctl import bootstrap_system_key

        mock_ns = mock.MagicMock()
        mock_namespace.new.return_value = mock_ns

        result = self.runner.invoke(bootstrap_system_key, ['mykey', 'myvalue'])

        self.assertEqual(result.exit_code, 0)
        mock_namespace.new.assert_called_once_with('system')
        mock_ns.add_key.assert_called_once_with('mykey', 'myvalue')
        self.assertIn('Done', result.output)

    @mock.patch('shakenfist.client.ctl.mariadb')
    def test_show_config_empty(self, mock_mariadb):
        from shakenfist.client.ctl import show_config

        mock_mariadb.get_cluster_config.return_value = {}

        result = self.runner.invoke(show_config)

        self.assertEqual(result.exit_code, 0)
        self.assertIn('{}', result.output)

    @mock.patch('shakenfist.client.ctl.mariadb')
    def test_show_config_with_data(self, mock_mariadb):
        from shakenfist.client.ctl import show_config

        mock_mariadb.get_cluster_config.return_value = {'key': 'value'}

        result = self.runner.invoke(show_config)

        self.assertEqual(result.exit_code, 0)
        self.assertIn('key', result.output)
        self.assertIn('value', result.output)

    @mock.patch('shakenfist.client.ctl.mariadb')
    def test_set_config_string(self, mock_mariadb):
        from shakenfist.client.ctl import set_config

        result = self.runner.invoke(set_config, ['myflag', 'myvalue'])

        self.assertEqual(result.exit_code, 0)
        mock_mariadb.set_cluster_config.assert_called_once_with(
            'myflag', 'myvalue')
        self.assertIn('Setting myflag', result.output)

    @mock.patch('shakenfist.client.ctl.mariadb')
    def test_set_config_bool_true(self, mock_mariadb):
        from shakenfist.client.ctl import set_config

        result = self.runner.invoke(set_config, ['myflag', 'true'])

        self.assertEqual(result.exit_code, 0)
        mock_mariadb.set_cluster_config.assert_called_once_with(
            'myflag', True)
        # Verify bool conversion happened
        self.assertIn("<class 'bool'>", result.output)

    @mock.patch('shakenfist.client.ctl.mariadb')
    def test_set_config_int(self, mock_mariadb):
        from shakenfist.client.ctl import set_config

        result = self.runner.invoke(set_config, ['myflag', '42'])

        self.assertEqual(result.exit_code, 0)
        mock_mariadb.set_cluster_config.assert_called_once_with(
            'myflag', 42)
        self.assertIn("<class 'int'>", result.output)

    @mock.patch('shakenfist.client.ctl.mariadb')
    def test_set_config_float(self, mock_mariadb):
        from shakenfist.client.ctl import set_config

        result = self.runner.invoke(set_config, ['myflag', '3.14'])

        self.assertEqual(result.exit_code, 0)
        mock_mariadb.set_cluster_config.assert_called_once_with(
            'myflag', 3.14)
        self.assertIn("<class 'float'>", result.output)

    @mock.patch('shakenfist.client.ctl.sf_config')
    def test_verify_config(self, mock_sf_config):
        from shakenfist.client.ctl import verify_config

        result = self.runner.invoke(verify_config)

        self.assertEqual(result.exit_code, 0)
        mock_sf_config.verify_config.assert_called_once()
        self.assertIn('ok', result.output.lower())

    @mock.patch('shakenfist.client.ctl.config')
    @mock.patch('shakenfist.client.ctl.mariadb')
    def test_ensure_mariadb_schema_no_host(self, mock_mariadb, mock_config):
        from shakenfist.client.ctl import ensure_mariadb_schema

        mock_config.MARIADB_HOST = ''

        result = self.runner.invoke(ensure_mariadb_schema)

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('MARIADB_HOST', result.output)

    @mock.patch('shakenfist.client.ctl.config')
    @mock.patch('shakenfist.client.ctl.mariadb')
    def test_ensure_mariadb_schema_success(self, mock_mariadb, mock_config):
        from shakenfist.client.ctl import ensure_mariadb_schema

        mock_config.MARIADB_HOST = 'localhost'
        mock_mariadb.ensure_schema.return_value = [
            {'table': 'test', 'migrated': True, 'start_version': 0,
             'end_version': 1}
        ]

        result = self.runner.invoke(ensure_mariadb_schema)

        self.assertEqual(result.exit_code, 0)
        mock_mariadb.ensure_schema.assert_called_once()
        self.assertIn('verified', result.output.lower())


class NodeCommandsTestCase(base.ShakenFistTestCase):
    """Tests for node-related CLI commands."""

    @classmethod
    def setUpClass(cls):
        cls.verify_config_patcher = mock.patch(
            'shakenfist.config.verify_config', mock.MagicMock())
        cls.verify_config_patcher.start()

        if 'shakenfist.client.ctl' in sys.modules:
            del sys.modules['shakenfist.client.ctl']

    @classmethod
    def tearDownClass(cls):
        cls.verify_config_patcher.stop()

    def setUp(self):
        super().setUp()
        self.runner = CliRunner()

    @mock.patch('shakenfist.client.ctl.config')
    @mock.patch('shakenfist.client.ctl.Node')
    def test_initialise_node_with_args(self, mock_node_cls, mock_config):
        from shakenfist.client.ctl import initialise_node

        mock_node = mock.MagicMock()
        mock_node.state.value = 'created'
        mock_node_cls.new.return_value = mock_node

        result = self.runner.invoke(
            initialise_node,
            ['--node-name', 'test-node', '--node-mesh-ip', '192.168.1.1'])

        self.assertEqual(result.exit_code, 0)
        mock_node_cls.new.assert_called_once_with('test-node', '192.168.1.1')

    @mock.patch('shakenfist.client.ctl.config')
    @mock.patch('shakenfist.client.ctl.Node')
    def test_initialise_node_uses_config_defaults(self, mock_node_cls, mock_config):
        from shakenfist.client.ctl import initialise_node

        mock_config.NODE_NAME = 'config-node'
        mock_config.NODE_MESH_IP = '10.0.0.1'
        mock_node = mock.MagicMock()
        mock_node.state.value = 'created'
        mock_node_cls.new.return_value = mock_node

        result = self.runner.invoke(initialise_node)

        self.assertEqual(result.exit_code, 0)
        mock_node_cls.new.assert_called_once_with('config-node', '10.0.0.1')

    @mock.patch('shakenfist.client.ctl.config')
    @mock.patch('shakenfist.client.ctl.Node')
    def test_register_daemon(self, mock_node_cls, mock_config):
        from shakenfist.client.ctl import register_daemon

        mock_config.NODE_NAME = 'test-node'
        mock_node = mock.MagicMock()
        mock_node.state.value = 'created'
        mock_node.get_daemon_state.return_value = mock.MagicMock(value='running')
        mock_node_cls.from_db.return_value = mock_node

        result = self.runner.invoke(register_daemon, ['api', 'cluster'])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(mock_node.register_daemon.call_count, 2)

    @mock.patch('shakenfist.client.ctl.config')
    @mock.patch('shakenfist.client.ctl.Node')
    def test_register_daemon_node_not_found(self, mock_node_cls, mock_config):
        from shakenfist.client.ctl import register_daemon

        mock_config.NODE_NAME = 'test-node'
        mock_node_cls.from_db.return_value = None

        result = self.runner.invoke(register_daemon, ['api'])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('not found', result.output)

    @mock.patch('shakenfist.client.ctl.config')
    @mock.patch('shakenfist.client.ctl.Node')
    def test_deregister_daemon(self, mock_node_cls, mock_config):
        from shakenfist.client.ctl import deregister_daemon

        mock_config.NODE_NAME = 'test-node'
        mock_node = mock.MagicMock()
        mock_node.state.value = 'created'
        mock_node_cls.from_db.return_value = mock_node

        result = self.runner.invoke(deregister_daemon, ['api'])

        self.assertEqual(result.exit_code, 0)
        mock_node.deregister_daemon.assert_called_once_with('api')

    @mock.patch('shakenfist.client.ctl.config')
    @mock.patch('shakenfist.client.ctl.Node')
    def test_stop_daemon(self, mock_node_cls, mock_config):
        from shakenfist.client.ctl import stop

        mock_config.NODE_NAME = 'test-node'
        mock_node = mock.MagicMock()
        mock_node.state.value = 'created'
        mock_node_cls.from_db.return_value = mock_node

        result = self.runner.invoke(stop, ['api'])

        self.assertEqual(result.exit_code, 0)
        mock_node.set_daemon_state.assert_called_once()
