# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.tests import base


NODE_UUID = 'aabbccdd-1234-5678-abcd-000000000001'

EXPECTED_PER_NODE_QUEUES = [
    f'{NODE_UUID}-network-user_waiting',
    f'{NODE_UUID}-network-user_facing',
    f'{NODE_UUID}-network-user_facing_high_io',
    f'{NODE_UUID}-network-background',
    f'{NODE_UUID}-network-background_high_io',
]

EXPECTED_CLUSTER_QUEUES = [
    'networknode-clusteroperation-user_waiting',
    'networknode-clusteroperation-user_facing',
    'networknode-clusteroperation-user_facing_high_io',
    'networknode-clusteroperation-background',
    'networknode-clusteroperation-background_high_io',
]

EXPECTED_QUEUE_ORDER_NETWORK_NODE = EXPECTED_PER_NODE_QUEUES + EXPECTED_CLUSTER_QUEUES


class NetWorkerDequeueOrderTest(base.ShakenFistTestCase):
    """Assert that the net-worker dequeues per-node queues before cluster-wide
    network-node queues, and that the correct queues are polled depending on
    whether this node is the elected network node.

    Safety property: each queue is drained by exactly one worker.
      - Per-node queues ({node_uuid}-network-*): drained by this node's
        net-worker only.
      - Cluster-wide queues (networknode-*): drained by the elected network
        node's net-worker only.
    """

    def _run_one_iteration(self, node_uuid, is_network_node):
        """Run one iteration of Job.execute() then break the loop.

        Patches:
        - mariadb.dequeue_work_item  -> always returns None (no work)
        - daemon.check_abort_path    -> True on the first call, False on the
                                        second so the while-loop exits cleanly
        - daemon.clear_abort_path    -> no-op
        - time.sleep                 -> no-op
        - config.NODE_UUID           -> stable test UUID
        - config.NODE_IS_NETWORK_NODE -> controlled per test
        - util_concurrency.set_thread_name -> no-op
        """
        with mock.patch(
            'shakenfist.daemons.network.workitem.config'
        ) as mock_config, mock.patch(
            'shakenfist.daemons.network.workitem.mariadb'
        ) as mock_mariadb, mock.patch(
            'shakenfist.daemons.network.workitem.daemon'
        ) as mock_daemon, mock.patch(
            'shakenfist.daemons.network.workitem.time'
        ), mock.patch(
            'shakenfist.daemons.network.workitem.util_concurrency'
        ):
            mock_config.NODE_UUID = node_uuid
            mock_config.NODE_IS_NETWORK_NODE = is_network_node
            # Allow exactly one pass through the while loop.
            mock_daemon.check_abort_path.side_effect = [True, False]
            mock_daemon.clear_abort_path.return_value = None
            mock_mariadb.dequeue_work_item.return_value = None

            # Import here so the module-level config mock takes effect.
            from shakenfist.daemons.network.workitem import Job
            job = Job.__new__(Job)
            job.name = 'test-worker'
            job.abort_path = '/run/sf/net-test-worker.abort'

            job.execute()

            return mock_mariadb.dequeue_work_item

    # -------------------------------------------------------------------------
    # Non-network-node: only per-node queues should be visited
    # -------------------------------------------------------------------------

    def test_non_network_node_visits_only_per_node_queues(self):
        """A hypervisor that is not the network node must only poll its own
        per-node queues.  Polling the cluster-wide networknode-* queues on
        every node would cause multiple workers to race over the same queue.
        """
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=False)

        actual_queues = [call.args[0] for call in mock_dequeue.call_args_list]
        self.assertEqual(EXPECTED_PER_NODE_QUEUES, actual_queues)

    def test_non_network_node_exactly_five_dequeue_calls(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=False)
        self.assertEqual(5, mock_dequeue.call_count)

    def test_non_network_node_no_cluster_queues(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=False)

        actual_queues = [call.args[0] for call in mock_dequeue.call_args_list]
        for queue_name in actual_queues:
            self.assertNotIn('networknode', queue_name)

    # -------------------------------------------------------------------------
    # Network-node: per-node queues first, then cluster-wide queues
    # -------------------------------------------------------------------------

    def test_network_node_dequeue_call_order_per_node_first(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)

        actual_queues = [call.args[0] for call in mock_dequeue.call_args_list]
        self.assertEqual(EXPECTED_QUEUE_ORDER_NETWORK_NODE, actual_queues)

    def test_network_node_per_node_queues_come_before_cluster_queues(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)

        actual_queues = [call.args[0] for call in mock_dequeue.call_args_list]
        self.assertEqual(EXPECTED_PER_NODE_QUEUES, actual_queues[:5])
        self.assertEqual(EXPECTED_CLUSTER_QUEUES, actual_queues[5:])

    def test_network_node_exactly_ten_dequeue_calls_per_iteration(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)
        self.assertEqual(10, mock_dequeue.call_count)

    def test_network_node_uuid_embedded_in_per_node_queue_names(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)

        per_node_calls = [
            call.args[0]
            for call in mock_dequeue.call_args_list[:5]
        ]
        for queue_name in per_node_calls:
            self.assertIn(NODE_UUID, queue_name)

    def test_network_node_cluster_queues_do_not_contain_node_uuid(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)

        cluster_calls = [
            call.args[0]
            for call in mock_dequeue.call_args_list[5:]
        ]
        for queue_name in cluster_calls:
            self.assertNotIn(NODE_UUID, queue_name)


OP_UUID = 'deadbeef-dead-beef-dead-beefdeadbeef'
QUEUE_NAME = f'{NODE_UUID}-network-user_waiting'

# The four terminal states that should cause the dispatcher to skip an op.
TERMINAL_STATES = [
    BaseClusterOperation.STATE_ABORT,
    BaseClusterOperation.STATE_COMPLETE,
    dbo.STATE_DELETED,
    dbo.STATE_ERROR,
]


class TerminalStateCancellationTest(base.ShakenFistTestCase):
    """Verify that _cluster_operation_execute skips ops already in a terminal
    state without raising InvalidStateException, and logs an audit event.

    The outer execute() try/finally in workitem.py always calls
    mariadb.resolve_work_item regardless of whether this method returns early,
    so we do not assert on resolve_work_item here — that is covered by the
    execute() caller, not by this method.
    """

    def _run_terminal_state_check(self, terminal_state_value):
        """Exercise _cluster_operation_execute with an op already in the given
        terminal state and return the mock op so callers can assert on it."""
        mock_state = mock.MagicMock()
        mock_state.value = terminal_state_value

        mock_op = mock.MagicMock()
        mock_op.state = mock_state

        mock_op_class = mock.MagicMock()
        mock_op_class.from_db.return_value = mock_op

        workitem = {
            'operation_type': 'net_op',
            'operation_uuid': OP_UUID,
        }

        with mock.patch(
            'shakenfist.daemons.network.workitem.get_object_class',
            return_value=mock_op_class,
        ):
            from shakenfist.daemons.network.workitem import Job
            job = Job.__new__(Job)
            # _cluster_operation_execute uses self.log (inherited from Job),
            # but we only need it for the "op not found" path.  Provide a
            # no-op logger to avoid AttributeError on the happy path.
            job.log = mock.MagicMock()

            # Must not raise InvalidStateException (or any other exception).
            job._cluster_operation_execute(QUEUE_NAME, workitem)

        return mock_op

    def test_state_abort_skips_without_exception(self):
        """STATE_ABORT is the primary target of this fix; confirm no exception."""
        mock_op = self._run_terminal_state_check(BaseClusterOperation.STATE_ABORT)
        self.assertIsNotNone(mock_op)

    def test_state_abort_logs_audit_event(self):
        mock_op = self._run_terminal_state_check(BaseClusterOperation.STATE_ABORT)
        mock_op.add_event.assert_called_once_with(
            EVENT_TYPE_AUDIT,
            f'skipping op already in terminal state {BaseClusterOperation.STATE_ABORT}')

    def test_state_complete_skips_without_exception(self):
        mock_op = self._run_terminal_state_check(BaseClusterOperation.STATE_COMPLETE)
        self.assertIsNotNone(mock_op)

    def test_state_complete_logs_audit_event(self):
        mock_op = self._run_terminal_state_check(BaseClusterOperation.STATE_COMPLETE)
        mock_op.add_event.assert_called_once_with(
            EVENT_TYPE_AUDIT,
            f'skipping op already in terminal state {BaseClusterOperation.STATE_COMPLETE}')

    def test_state_deleted_skips_without_exception(self):
        mock_op = self._run_terminal_state_check(dbo.STATE_DELETED)
        self.assertIsNotNone(mock_op)

    def test_state_deleted_logs_audit_event(self):
        mock_op = self._run_terminal_state_check(dbo.STATE_DELETED)
        mock_op.add_event.assert_called_once_with(
            EVENT_TYPE_AUDIT,
            f'skipping op already in terminal state {dbo.STATE_DELETED}')

    def test_state_error_skips_without_exception(self):
        mock_op = self._run_terminal_state_check(dbo.STATE_ERROR)
        self.assertIsNotNone(mock_op)

    def test_state_error_logs_audit_event(self):
        mock_op = self._run_terminal_state_check(dbo.STATE_ERROR)
        mock_op.add_event.assert_called_once_with(
            EVENT_TYPE_AUDIT,
            f'skipping op already in terminal state {dbo.STATE_ERROR}')

    def test_non_terminal_state_does_not_short_circuit(self):
        """An op in STATE_QUEUED must NOT be dropped; verify the code reaches
        the dependency-checking path (op.depends_on is accessed)."""
        mock_op = self._run_terminal_state_check(BaseClusterOperation.STATE_QUEUED)
        # add_event is called only for the terminal-state early return;
        # for STATE_QUEUED the code moves on to set op.queue_name and access
        # op.depends_on — so add_event should NOT have been called with the
        # 'skipping' message.
        for c in mock_op.add_event.call_args_list:
            self.assertNotIn('skipping op already in terminal state', str(c))
