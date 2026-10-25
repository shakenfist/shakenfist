# Copyright 2026 Michael Still and contributors

"""sentinel-first's stop path, which must exit cleanly on a lone restart.

A deploy restarts each unit on its own, so sentinel-first stops while
sentinel-last is still running and the node is not stopping. It used to mark
the node stopped regardless, which the state machine refused, and the unit
failed on every hypervisor on every deploy (#4507).
"""

from unittest import mock

from shakenfist.daemons.sentinel_first import main as sentinel_first
from shakenfist.node import Node
from shakenfist.tests import base


def _node(state):
    n = mock.MagicMock()
    n.state.value = state
    n.set_lifecycle_state.return_value = True
    return n


class RecordStopTestCase(base.ShakenFistTestCase):
    def test_a_whole_node_stop_completes_to_stopped(self):
        # sentinel-last stopped first and marked the node stopping.
        n = _node(Node.STATE_STOPPING)
        self.assertTrue(sentinel_first.record_stop(n))
        n.set_daemon_state.assert_called_once_with(
            'sentinel-first', Node.DAEMON_STATE_STOPPED)
        n.set_lifecycle_state.assert_called_once_with(Node.STATE_STOPPED)

    def test_a_lone_restart_leaves_the_lifecycle_alone(self):
        # Recording this daemon stopped reconciles the node to degraded, and
        # degraded -> stopped is refused; created -> stopped is too.
        for state in (Node.STATE_DEGRADED, Node.STATE_CREATED):
            n = _node(state)
            self.assertFalse(sentinel_first.record_stop(n))
            n.set_daemon_state.assert_called_once_with(
                'sentinel-first', Node.DAEMON_STATE_STOPPED)
            n.set_lifecycle_state.assert_not_called()

    def test_the_stopping_to_stopped_transition_is_valid(self):
        # Guards the premise: if the state machine stopped allowing this,
        # a whole-node stop would raise here instead.
        self.assertIn(Node.STATE_STOPPED, Node.state_targets[Node.STATE_STOPPING])
        self.assertNotIn(Node.STATE_STOPPED, Node.state_targets[Node.STATE_DEGRADED])
