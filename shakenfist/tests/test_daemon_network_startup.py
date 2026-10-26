# Copyright 2026 Michael Still and contributors

"""sf-net clears the claims its previous process left on its queues.

A claim an sf-net could not give back -- it crashed, or was killed while an
operation ran -- waited for the stuck-row reaper, 30 minutes, and every
request for the same work in the meantime was deduplicated onto the
stranded operation (#4660). Only this node's sf-net drains its queues, so it
clears them at startup, before its dispatcher runs.
"""

from unittest import mock

from shakenfist import exceptions
from shakenfist.daemons.network import main as network_main
from shakenfist.tests import base


NODE_UUID = 'aabbccdd-1234-5678-abcd-000000000002'


class ResetNetworkQueuesTestCase(base.ShakenFistTestCase):
    def _queues_reset(self, is_network_node, side_effect=None):
        with mock.patch.object(network_main.config, 'NODE_IS_NETWORK_NODE', is_network_node), \
                mock.patch.object(network_main, 'mariadb') as mock_mariadb:
            mock_mariadb.restart_work_queue.side_effect = side_effect
            network_main.reset_network_queues(NODE_UUID)
        return [c.args[0] for c in mock_mariadb.restart_work_queue.call_args_list]

    def test_the_dispatchers_queues_are_reset(self):
        for is_network_node in (True, False):
            with self.subTest(is_network_node=is_network_node):
                with mock.patch.object(network_main.config, 'NODE_IS_NETWORK_NODE', is_network_node):
                    expected = network_main.workitem.dispatcher_queue_names(NODE_UUID)
                self.assertEqual(expected, self._queues_reset(is_network_node))

    def test_only_the_network_node_resets_the_shared_queues(self):
        # Every other node's sf-net leaves them alone: only the network node
        # drains them, so only its claims can be there.
        shared = [q for q in self._queues_reset(True) if q.startswith('networknode-')]
        self.assertEqual(5, len(shared))
        self.assertEqual(
            [], [q for q in self._queues_reset(False) if not q.startswith(f'{NODE_UUID}-network-')])

    def test_a_queue_which_cannot_be_reset_does_not_stop_the_rest(self):
        reset = self._queues_reset(
            False, side_effect=[exceptions.DatabaseUnavailable('down'), None, None, None, None])
        self.assertEqual(5, len(reset))


class MainResetsBeforeDispatchTestCase(base.ShakenFistTestCase):
    def test_queues_are_reset_before_the_monitor_runs(self):
        order = mock.Mock()
        with mock.patch.object(network_main, 'reset_network_queues', order.reset), \
                mock.patch.object(network_main, 'Monitor', order.Monitor), \
                mock.patch.object(network_main, 'daemon'), \
                mock.patch.object(network_main, 'Node'), \
                mock.patch.object(network_main, 'setproctitle'), \
                mock.patch.object(network_main, 'util_exceptions'), \
                mock.patch.object(network_main.config, 'NODE_IS_NETWORK_NODE', False), \
                mock.patch.object(network_main.config, 'NODE_UUID', NODE_UUID):
            network_main.main()

        calls = [c[0] for c in order.mock_calls]
        self.assertEqual(['reset', 'Monitor', 'Monitor().run'], calls)
        order.reset.assert_called_once_with(NODE_UUID)
