# Copyright 2026 Michael Still and contributors
from unittest import mock

from shakenfist.exceptions import NodeLockUnavailable
from shakenfist.operations import node_net_op
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.schema.operations import node_net_op as schema
from shakenfist.tests import base


class _FakeNetwork:
    """Enough of a Network for dispatch_task() to work on."""

    def __init__(self):
        self.uuid = 'fake-network'
        self.events = []

    def add_event(self, event_type, message, extra=None):
        self.events.append((event_type, message, extra))


class _FakeNodeNetOp:
    """Stands in for self inside NodeNetOp.dispatch_task().

    The real defer_with_backoff() and _error_for_task() are borrowed
    rather than stubbed, so the retry budget and the error message the
    fix branches between are the production ones. Only the collaborators
    which reach MariaDB -- the event write, the re-enqueue and the state
    transition -- are recorded here.
    """

    object_type = schema.object_type

    defer_with_backoff = BaseClusterOperation.defer_with_backoff
    _error_for_task = node_net_op.NodeNetOp._error_for_task

    def __init__(self, raises, defer_count=0):
        self.uuid = 'fake-op'
        self.node_uuid = 'fake-node'
        self.network_uuid = 'fake-network'
        self.current_defer_count = defer_count
        self.queue_name = 'fake-node-workers'
        self.log = mock.MagicMock()

        self._raises = raises
        self.events = []
        self.deferred = []
        self.errored = []

    def add_event(self, event_type, message, extra=None):
        self.events.append((event_type, message, extra))

    def defer(self, waiting_on=None, delay=15.0):
        self.deferred.append(delay)

    def enter_error_state(self, new_value, message):
        self.errored.append((new_value, message))

    def _network_destroy(self, n):
        raise self._raises


class NodeNetOpNodelockRetryTestCase(base.ShakenFistTestCase):
    """A nodelock blip defers the operation instead of erroring it.

    This node's nodelock daemon is unreachable for a few seconds on
    every rolling restart, which is why every long lived daemon blocks
    in Daemon.wait_for_nodelock() before doing any work. The work item
    path has no such wait, so it used to error the cluster operation
    outright the first time the lock could not be reached (issue 5058).
    """

    def setUp(self):
        super().setUp()

        self.network = _FakeNetwork()

        self.from_db_patcher = mock.patch(
            'shakenfist.operations.node_net_op.Network.from_db',
            return_value=self.network)
        self.from_db_patcher.start()
        self.addCleanup(self.from_db_patcher.stop)

        self.set_error_patcher = mock.patch(
            'shakenfist.operations.node_net_op.mariadb.'
            'set_cluster_operation_error')
        self.mock_set_error = self.set_error_patcher.start()
        self.addCleanup(self.set_error_patcher.stop)

        # Recording an exception writes a json file under
        # /srv/shakenfist, which a unit test has no business doing.
        self.ignore_patcher = mock.patch(
            'shakenfist.operations.node_net_op.util_exceptions.'
            'ignore_exception')
        self.ignore_patcher.start()
        self.addCleanup(self.ignore_patcher.stop)

    def _dispatch(self, op):
        node_net_op.NodeNetOp.dispatch_task(
            op, schema.model_tasks.network_destroy)

    def test_nodelock_unavailable_is_deferred(self):
        op = _FakeNodeNetOp(NodeLockUnavailable(
            'the nodelock daemon on this node did not answer after 3 '
            'attempts'))

        self._dispatch(op)

        # Deferred with the first delay of the default backoff ladder,
        # which comfortably covers the six to fourteen seconds the
        # daemon has been observed to take to come back.
        self.assertEqual([15], op.deferred)
        self.assertEqual([], op.errored)
        self.mock_set_error.assert_not_called()

        # And the network carries a record of why its task was late,
        # which outlives the operation.
        self.assertEqual(1, len(self.network.events))
        self.assertEqual(
            'nodelock daemon unreachable, deferring network task',
            self.network.events[0][1])
        self.assertEqual(
            'network_destroy', self.network.events[0][2]['task'])
        self.assertEqual(1, self.network.events[0][2]['defer_count'])

    def test_nodelock_unavailable_backs_off(self):
        # The second and third retries use the rest of the ladder.
        for already_deferred, expected_delay in ((1, 30), (2, 60)):
            op = _FakeNodeNetOp(
                NodeLockUnavailable('did not answer'),
                defer_count=already_deferred)

            self._dispatch(op)

            self.assertEqual([expected_delay], op.deferred)
            self.assertEqual([], op.errored)

    def test_nodelock_unavailable_errors_once_budget_is_spent(self):
        # A daemon which never comes back must still end the operation,
        # rather than deferring it forever.
        op = _FakeNodeNetOp(
            NodeLockUnavailable('did not answer'), defer_count=3)

        self._dispatch(op)

        self.assertEqual([], op.deferred)
        self.assertEqual([], self.network.events)
        self.assertEqual(
            [(node_net_op.NodeNetOp.STATE_ERROR,
              'network_destroy task raised NodeLockUnavailable: '
              'did not answer')],
            op.errored)
        self.mock_set_error.assert_called_once()

    def test_other_exceptions_still_error_immediately(self):
        # The floor: only an unreachable nodelock daemon is retried. A
        # genuine failure of the work is still terminal on its first
        # occurrence, with no defer.
        op = _FakeNodeNetOp(ValueError('no such interface'))

        self._dispatch(op)

        self.assertEqual([], op.deferred)
        self.assertEqual([], self.network.events)
        self.assertEqual(
            [(node_net_op.NodeNetOp.STATE_ERROR,
              'network_destroy task raised ValueError: no such interface')],
            op.errored)
        self.mock_set_error.assert_called_once()
