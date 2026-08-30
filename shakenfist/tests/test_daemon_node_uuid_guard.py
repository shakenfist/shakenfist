# Copyright 2026 Michael Still and contributors

"""The malformed-NODE_UUID guard in Daemon.check_daemon_state().

config.NODE_UUID has no validator and _resolve_node_uuid() returns early
when it is already set, so a typo in SHAKENFIST_NODE_UUID reaches
check_daemon_state() unparsed and used to raise out of every daemon's
idle loop every couple of seconds. The guard swallows that, which costs
this daemon its shutdown observation for the life of the process -- so
it has to be loud about it exactly once.
"""

from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.tests import base


class NodeUUIDGuardTestCase(base.ShakenFistTestCase):
    def _make_daemon(self):
        d = daemon.Daemon.__new__(daemon.Daemon)
        d._last_daemon_state_check = 0.0
        d._daemon_state_poll_interval = 0
        d.node_uuid_invalid_logged = False
        d.daemon_name = 'testdaemon'
        return d

    @mock.patch('shakenfist.mariadb.get_node_daemon_state')
    @mock.patch('shakenfist.config.config.NODE_UUID', 'not-a-uuid')
    def test_a_malformed_node_uuid_does_not_raise(self, mock_get):
        d = self._make_daemon()
        # Before the guard this raised ValueError into the idle loop.
        d.check_daemon_state()
        mock_get.assert_not_called()

    @mock.patch('shakenfist.daemons.daemon.LOG')
    @mock.patch('shakenfist.mariadb.get_node_daemon_state')
    @mock.patch('shakenfist.config.config.NODE_UUID', 'not-a-uuid')
    def test_it_is_logged_at_error_once_with_the_value(self, _mock_get,
                                                       mock_log):
        d = self._make_daemon()
        for _ in range(5):
            d._last_daemon_state_check = 0.0
            d.check_daemon_state()

        # Once, not five times: a permanent misconfiguration must not become
        # a per-tick log flood.
        self.assertEqual(1, mock_log.error.call_count)
        mock_log.debug.assert_not_called()

        # The operator cannot fix what the log will not name.
        args = mock_log.error.call_args[0]
        self.assertIn('not-a-uuid', args)

    @mock.patch('shakenfist.mariadb.get_node_daemon_state')
    @mock.patch('shakenfist.config.config.NODE_UUID',
                '11111111-2222-3333-4444-555555555555')
    def test_a_valid_node_uuid_still_reads_the_state_row(self, mock_get):
        mock_get.return_value = None
        d = self._make_daemon()
        d.check_daemon_state()
        # Vacuity guard: if the guard ever swallowed the good path too, the
        # tests above would pass for the wrong reason.
        mock_get.assert_called_once()
