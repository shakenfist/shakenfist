# Copyright 2026 Michael Still and contributors

"""The sentinels, and every unit's ExecStop, survive an unreachable database.

At a whole-cluster reboot the database hosts went down first. On every other
node sentinel-first's check-in raised DatabaseUnavailable out of its loop, or
its ExecStop (sf-ctl stop) did, and the unit failed with nothing recorded; in
normal running a database blip of more than about 30 seconds crash-looped the
same way (#4628).
"""

import sys
from unittest import mock

from click.testing import CliRunner

from shakenfist.daemons import daemon
from shakenfist.daemons.sentinel_first import main as sentinel_first
from shakenfist.daemons.sentinel_last import main as sentinel_last
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.node import Node
from shakenfist.tests import base


class CheckInTestCase(base.ShakenFistTestCase):
    @mock.patch.object(daemon.Node, 'observe_this_node')
    def test_records_a_check_in(self, observe):
        self.assertTrue(daemon.check_in_this_node())
        observe.assert_called_once_with()

    @mock.patch.object(daemon.Node, 'observe_this_node',
                       side_effect=DatabaseUnavailable('connection refused'))
    def test_skips_a_check_in_the_database_cannot_take(self, observe):
        with mock.patch.object(daemon, 'LOG') as log:
            self.assertFalse(daemon.check_in_this_node())
        log.warning.assert_called_once()

    @mock.patch.object(daemon.Node, 'observe_this_node', side_effect=ValueError('a bug'))
    def test_other_errors_still_propagate(self, observe):
        self.assertRaises(ValueError, daemon.check_in_this_node)

    @mock.patch.object(daemon.Node, 'observe_this_node')
    def test_a_check_in_which_works_is_not_logged(self, observe):
        # Each sentinel checks in every 15 seconds, so a line per check-in
        # was almost all of a cluster's debug log (#4541).
        with mock.patch.object(daemon, 'LOG') as log:
            self.assertTrue(daemon.check_in_this_node())
        self.assertEqual([], log.mock_calls)

    @mock.patch.object(daemon.Node, 'observe_this_node')
    def test_a_slow_check_in_is_a_warning(self, observe):
        with mock.patch.object(daemon, 'LOG') as log, \
                mock.patch.object(daemon.time, 'monotonic',
                                  side_effect=[100, 100 + daemon.SLOW_CHECK_IN_SECONDS + 1]):
            self.assertTrue(daemon.check_in_this_node())
        log.with_fields.assert_called_once_with(
            {'duration': daemon.SLOW_CHECK_IN_SECONDS + 1})
        log.with_fields.return_value.warning.assert_called_once_with('Slow check-in')

    def test_the_sentinel_loops_do_not_log_each_check_in(self):
        for module in (sentinel_first, sentinel_last):
            with self.subTest(module.__name__):
                with open(module.__file__) as f:
                    self.assertNotIn("'Checking in'", f.read())


class SentinelFirstStopTestCase(base.ShakenFistTestCase):
    def test_database_gone_before_the_daemon_state_write(self):
        n = mock.MagicMock()
        n.set_daemon_state.side_effect = DatabaseUnavailable('connection refused')
        self.assertFalse(sentinel_first.record_stop(n))
        n.set_lifecycle_state.assert_not_called()

    def test_database_gone_before_the_lifecycle_write(self):
        n = mock.MagicMock()
        n.state.value = Node.STATE_STOPPING
        n.set_lifecycle_state.side_effect = DatabaseUnavailable('connection refused')
        self.assertFalse(sentinel_first.record_stop(n))


class SentinelLastStopTestCase(base.ShakenFistTestCase):
    def test_records_this_daemon_stopped_and_the_node_stopping(self):
        n = mock.MagicMock()
        self.assertTrue(sentinel_last.record_stop(n))
        n.set_daemon_state.assert_called_once_with('sentinel-last', Node.DAEMON_STATE_STOPPED)
        n.set_lifecycle_state.assert_called_once_with(Node.STATE_STOPPING)

    def test_database_gone(self):
        n = mock.MagicMock()
        n.set_lifecycle_state.side_effect = DatabaseUnavailable('connection refused')
        self.assertFalse(sentinel_last.record_stop(n))


class CtlStopTestCase(base.ShakenFistTestCase):
    @classmethod
    def setUpClass(cls):
        cls.verify_config_patcher = mock.patch('shakenfist.config.verify_config', mock.MagicMock())
        cls.verify_config_patcher.start()
        if 'shakenfist.client.ctl' in sys.modules:
            del sys.modules['shakenfist.client.ctl']

    @classmethod
    def tearDownClass(cls):
        cls.verify_config_patcher.stop()

    def setUp(self):
        super().setUp()
        from shakenfist.client import ctl
        self.ctl = ctl

    def invoke(self, from_db):
        with mock.patch.object(self.ctl, 'Node') as node:
            node.STATE_MISSING = Node.STATE_MISSING
            node.DAEMON_STATE_STOPPING = Node.DAEMON_STATE_STOPPING
            node.from_db.side_effect = from_db
            result = CliRunner().invoke(self.ctl.stop, ['queues'])
        return result

    def test_records_the_daemon_stopping(self):
        n = mock.MagicMock()
        n.state.value = Node.STATE_CREATED
        result = self.invoke(lambda name: n)
        self.assertEqual(0, result.exit_code, result.output)
        n.set_daemon_state.assert_called_once_with('queues', Node.DAEMON_STATE_STOPPING)

    def test_succeeds_when_the_database_is_unreachable(self):
        def unreachable(name):
            raise DatabaseUnavailable('connection refused')

        result = self.invoke(unreachable)
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('Database unavailable', result.output)

    def test_an_unknown_node_still_fails(self):
        result = self.invoke(lambda name: None)
        self.assertNotEqual(0, result.exit_code)


class SentinelMainLoopTestCase(base.ShakenFistTestCase):
    """Each sentinel runs one check-in and stops, with the database gone."""

    def run_main(self, module):
        unreachable = DatabaseUnavailable('connection refused')
        with mock.patch.object(module, 'Node') as node, \
                mock.patch.object(daemon.Node, 'observe_this_node', side_effect=unreachable), \
                mock.patch.object(module.daemon, 'check_abort_path', side_effect=[True, False]), \
                mock.patch.object(module.daemon, 'clear_abort_path'), \
                mock.patch.object(module.daemon, 'force_clean_exit') as exit_, \
                mock.patch.object(module, 'send_systemd_ready'), \
                mock.patch.object(module, 'send_systemd_stopping'), \
                mock.patch.object(module, 'setproctitle'), \
                mock.patch.object(module, 'util_exceptions'), \
                mock.patch.object(module.time, 'sleep'):
            node.observe_this_node.side_effect = unreachable
            n = node.from_db.return_value
            n.set_daemon_state.side_effect = [None, unreachable]
            n.state.value = Node.STATE_STOPPING
            n.set_lifecycle_state.side_effect = [None, unreachable]
            module.main()
        exit_.assert_called_once_with()

    def test_sentinel_first(self):
        self.run_main(sentinel_first)

    def test_sentinel_last(self):
        self.run_main(sentinel_last)
