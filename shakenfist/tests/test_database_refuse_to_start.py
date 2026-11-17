# Copyright 2019 Michael Still and contributors

"""sf-database says why it refused to start, where systemd shows it.

Refusing to start on a failed startup precondition is deliberate, but
the unit is Restart=on-failure, so the only thing an operator saw was a
restart loop with the reason buried in the journal (issue #4963). Each
refusal now sends an sd_notify STATUS= line, which outlives the process
and appears as `systemctl status sf-database`'s Status: field.
"""

from unittest import mock

from shakenfist import exceptions
from shakenfist.daemons import daemon
from shakenfist.daemons.database import main as database_main
from shakenfist.tests import base


class RefuseToStartTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.daemons.database.main.send_systemd_status')
    def test_refuse_to_start_reports_and_exits(self, mock_status):
        with mock.patch.object(database_main, 'LOG') as mock_log:
            self.assertRaises(
                SystemExit, database_main._refuse_to_start,
                'the long explanation', 'the short status')

        mock_log.error.assert_called_once_with('the long explanation')
        mock_status.assert_called_once_with('the short status')

    @mock.patch('shakenfist.daemons.database.main.send_systemd_status')
    def test_refuse_to_start_exits_non_zero(self, _mock_status):
        # A zero exit would leave Restart=on-failure thinking the daemon
        # had finished its work successfully.
        e = self.assertRaises(
            SystemExit, database_main._refuse_to_start, 'message', 'status')
        self.assertEqual(e.code, 1)


class RefuseToStartPathsTestCase(base.ShakenFistTestCase):
    """The three startup preconditions main() refuses to start on."""

    def setUp(self):
        super().setUp()

        for target in ('shakenfist.util.exceptions.install_exception_tracking',
                       'shakenfist.daemons.database.main.set_caller_identity',
                       'shakenfist.daemons.daemon.write_pid_file'):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.status_patcher = mock.patch(
            'shakenfist.daemons.database.main.send_systemd_status')
        self.mock_status = self.status_patcher.start()
        self.addCleanup(self.status_patcher.stop)

        self.config_patcher = mock.patch(
            'shakenfist.daemons.database.main.config')
        self.mock_config = self.config_patcher.start()
        self.mock_config.MARIADB_HOST = 'db.example.com'
        self.addCleanup(self.config_patcher.stop)

        engine_patcher = mock.patch('shakenfist.mariadb._get_engine')
        engine_patcher.start()
        self.addCleanup(engine_patcher.stop)

    def test_schema_mismatch_status(self):
        with mock.patch('shakenfist.mariadb.verify_mariadb_compat'), \
                mock.patch(
                    'shakenfist.mariadb.verify_schema_versions',
                    side_effect=exceptions.SchemaVersionMismatchError(
                        'Schema version mismatches detected. Run '
                        '`sf-ctl ensure-mariadb-schema` to apply pending '
                        'migrations.\n  - instances: expected v3, found v2')):
            self.assertRaises(SystemExit, database_main.main)

        self.mock_status.assert_called_once_with(
            'schema out of date; run sf-ctl ensure-mariadb-schema')

    def test_incompatible_server_status(self):
        with mock.patch(
                'shakenfist.mariadb.verify_mariadb_compat',
                side_effect=exceptions.MariaDBIncompatibleError(
                    'MariaDB server is incompatible:\n  - too old')):
            self.assertRaises(SystemExit, database_main.main)

        self.mock_status.assert_called_once_with(
            'MariaDB server is incompatible with this release')

    def test_unconfigured_mariadb_status(self):
        self.mock_config.MARIADB_HOST = ''

        self.assertRaises(SystemExit, database_main.main)

        self.mock_status.assert_called_once_with(
            'MariaDB is not configured; set MARIADB_HOST')


class SystemdStatusFlatteningTestCase(base.ShakenFistTestCase):
    """A status line is one line, whatever the caller passed."""

    @mock.patch('shakenfist.daemons.daemon._send_systemd_notification')
    def test_multiline_status_is_flattened(self, mock_notify):
        # sd_notify parses its datagram as newline separated KEY=value
        # assignments, so an unflattened exception message would have
        # its second line read as an assignment rather than as status.
        daemon.send_systemd_status(
            'Schema version mismatches detected.\n'
            '  - instances: expected v3, found v2')

        mock_notify.assert_called_once_with(
            b'STATUS=Schema version mismatches detected. - instances: '
            b'expected v3, found v2')

    @mock.patch('shakenfist.daemons.daemon._send_systemd_notification')
    def test_single_line_status_is_unchanged(self, mock_notify):
        daemon.send_systemd_status('3 active workers')

        mock_notify.assert_called_once_with(b'STATUS=3 active workers')
