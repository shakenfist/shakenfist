# Copyright 2019 Michael Still and contributors

import queue as queue_module
import threading
from unittest import mock

from shakenfist import exceptions
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
    """Assert that the net-worker requests per-node queues before cluster-wide
    network-node queues in a single ``dequeue_work_items`` call, and that the
    correct queues are included depending on whether this node is the
    elected network node.

    Safety property: each queue is drained by exactly one worker.
      - Per-node queues ({node_uuid}-network-*): drained by this node's
        net-worker only.
      - Cluster-wide queues (networknode-*): drained by the elected network
        node's net-worker only.
    """

    def _run_one_iteration(self, node_uuid, is_network_node):
        """Run one iteration of Job.execute() then break the loop.

        Patches:
        - mariadb.dequeue_work_items -> always returns [] (no work)
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
            mock_config.NETWORK_OPERATION_WORKERS = 1

            # Allow exactly one pass through the dispatcher while loop. The
            # worker pool threads also poll check_abort_path, so a plain
            # two-element side_effect list would be racily consumed; key the
            # response on which thread is asking instead.
            dispatcher_polls = {'count': 0}

            def fake_check_abort(path):
                if threading.current_thread().name.startswith('net-worker-'):
                    return True
                dispatcher_polls['count'] += 1
                return dispatcher_polls['count'] <= 1

            mock_daemon.check_abort_path.side_effect = fake_check_abort
            mock_daemon.clear_abort_path.return_value = None
            mock_mariadb.dequeue_work_items.return_value = []

            # Import here so the module-level config mock takes effect.
            from shakenfist.daemons.network.workitem import Job
            job = Job.__new__(Job)
            job.name = 'test-worker'
            job.abort_path = '/run/sf/net-test-worker.abort'

            job.execute()

            return mock_mariadb.dequeue_work_items

    # -------------------------------------------------------------------------
    # Non-network-node: only per-node queues should be requested
    # -------------------------------------------------------------------------

    def test_non_network_node_requests_only_per_node_queues(self):
        """A hypervisor that is not the network node must only ask for its own
        per-node queues. Including the cluster-wide networknode-* queues on
        every node would cause multiple workers to race over the same queue.
        """
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=False)

        self.assertEqual(1, mock_dequeue.call_count)
        queue_names = mock_dequeue.call_args.args[0]
        self.assertEqual(EXPECTED_PER_NODE_QUEUES, queue_names)

    def test_non_network_node_single_batched_call(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=False)
        self.assertEqual(1, mock_dequeue.call_count)

    def test_non_network_node_no_cluster_queues(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=False)

        queue_names = mock_dequeue.call_args.args[0]
        for queue_name in queue_names:
            self.assertNotIn('networknode', queue_name)

    # -------------------------------------------------------------------------
    # Network-node: per-node queues first, then cluster-wide queues
    # -------------------------------------------------------------------------

    def test_network_node_queue_order_per_node_first(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)

        self.assertEqual(1, mock_dequeue.call_count)
        queue_names = mock_dequeue.call_args.args[0]
        self.assertEqual(EXPECTED_QUEUE_ORDER_NETWORK_NODE, queue_names)

    def test_network_node_per_node_queues_come_before_cluster_queues(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)

        queue_names = mock_dequeue.call_args.args[0]
        self.assertEqual(EXPECTED_PER_NODE_QUEUES, queue_names[:5])
        self.assertEqual(EXPECTED_CLUSTER_QUEUES, queue_names[5:])

    def test_network_node_single_batched_call_per_iteration(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)
        self.assertEqual(1, mock_dequeue.call_count)

    def test_network_node_uuid_embedded_in_per_node_queue_names(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)

        per_node_queues = mock_dequeue.call_args.args[0][:5]
        for queue_name in per_node_queues:
            self.assertIn(NODE_UUID, queue_name)

    def test_network_node_cluster_queues_do_not_contain_node_uuid(self):
        mock_dequeue = self._run_one_iteration(NODE_UUID, is_network_node=True)

        cluster_queues = mock_dequeue.call_args.args[0][5:]
        for queue_name in cluster_queues:
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
        mock_op.uuid = OP_UUID

        from shakenfist.daemons.network.workitem import Job
        job = Job.__new__(Job)
        # _cluster_operation_execute uses self.log (inherited from Job),
        # but we only need it for the "op not found" path.  Provide a
        # no-op logger to avoid AttributeError on the happy path.
        job.log = mock.MagicMock()

        # Must not raise InvalidStateException (or any other exception).
        # The dispatcher loads the op and routes it here with the owning
        # worker's back-off map.
        job._cluster_operation_execute(QUEUE_NAME, mock_op, 1, {})

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


class ExponentialBackoffMapTest(base.ShakenFistTestCase):
    """Unit tests for the per-worker exponential back-off map.

    Tests exercise the _apply_defer and _drop_defer_entry helpers directly
    so we are not coupled to dispatcher-internal control flow.
    """

    def _make_job(self):
        """Construct a Job without running __init__. The back-off map is
        owned by each worker loop and passed into the helpers explicitly,
        so tests carry their own map alongside the job."""
        from shakenfist.daemons.network.workitem import Job
        job = Job.__new__(Job)
        return job, {}

    def _make_op(self, op_uuid):
        mock_op = mock.MagicMock()
        mock_op.uuid = op_uuid
        return mock_op

    def test_first_defer_uses_initial_delay(self):
        """An op never seen before is deferred at INITIAL_DEFER_DELAY (0.1 s)."""
        from shakenfist.daemons.network import workitem
        job, defer_delays = self._make_job()
        op = self._make_op('op-uuid-1')
        dep = mock.MagicMock()

        job._apply_defer(op, waiting_on=[dep], defer_delays=defer_delays)

        op.defer.assert_called_once_with(
            waiting_on=[dep], delay=workitem.INITIAL_DEFER_DELAY)
        self.assertAlmostEqual(0.1, workitem.INITIAL_DEFER_DELAY)

    def test_second_defer_doubles_delay(self):
        """The second defer for the same op uses 2 x INITIAL_DEFER_DELAY."""
        from shakenfist.daemons.network import workitem
        job, defer_delays = self._make_job()
        op = self._make_op('op-uuid-1')
        dep = mock.MagicMock()

        job._apply_defer(op, waiting_on=[dep], defer_delays=defer_delays)
        op.defer.reset_mock()
        job._apply_defer(op, waiting_on=[dep], defer_delays=defer_delays)

        op.defer.assert_called_once_with(
            waiting_on=[dep], delay=workitem.INITIAL_DEFER_DELAY * 2)

    def test_defer_schedule_progression(self):
        """The full schedule: 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 15.0,
        15.0 — the last two clamp at MAX_DEFER_DELAY."""
        job, defer_delays = self._make_job()
        op = self._make_op('op-uuid-1')
        dep = mock.MagicMock()

        expected_delays = [0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 15.0, 15.0]
        observed_delays = []
        for _ in expected_delays:
            op.defer.reset_mock()
            job._apply_defer(op, waiting_on=[dep], defer_delays=defer_delays)
            observed_delays.append(op.defer.call_args.kwargs['delay'])

        for expected, observed in zip(expected_delays, observed_delays):
            self.assertAlmostEqual(expected, observed, places=6)

    def test_advancing_to_executing_drops_entry(self):
        """After _drop_defer_entry (which the dispatcher calls just before
        op.execute()), the next defer for that op starts back at the initial
        delay rather than carrying over."""
        from shakenfist.daemons.network import workitem
        job, defer_delays = self._make_job()
        op = self._make_op('op-uuid-1')
        dep = mock.MagicMock()

        # Build up the back-off depth: 0.1 -> 0.2 -> 0.4 stored.
        job._apply_defer(op, waiting_on=[dep], defer_delays=defer_delays)
        job._apply_defer(op, waiting_on=[dep], defer_delays=defer_delays)
        # Dispatcher would now run op.execute(), so it drops the entry.
        job._drop_defer_entry(str(op.uuid), defer_delays)
        self.assertNotIn(str(op.uuid), defer_delays)

        # Next defer should be back at INITIAL_DEFER_DELAY.
        op.defer.reset_mock()
        job._apply_defer(op, waiting_on=[dep], defer_delays=defer_delays)
        op.defer.assert_called_once_with(
            waiting_on=[dep], delay=workitem.INITIAL_DEFER_DELAY)

    def test_terminal_state_drops_entry(self):
        """An op already in the back-off map that arrives at the dispatcher
        in a terminal state has its entry dropped before the dispatcher
        returns."""
        terminal_op_uuid = 'terminal-op-uuid'

        mock_state = mock.MagicMock()
        mock_state.value = BaseClusterOperation.STATE_ABORT

        mock_op = mock.MagicMock()
        mock_op.uuid = terminal_op_uuid
        mock_op.state = mock_state

        from shakenfist.daemons.network.workitem import Job
        job = Job.__new__(Job)
        job.log = mock.MagicMock()
        defer_delays = {terminal_op_uuid: 6.4}

        job._cluster_operation_execute(QUEUE_NAME, mock_op, 1, defer_delays)

        self.assertNotIn(terminal_op_uuid, defer_delays)

    def test_map_cap_evicts_oldest(self):
        """Inserting BACKOFF_MAP_CAP + 1 distinct op uuids via _apply_defer
        evicts the first-inserted entry; the others remain."""
        from shakenfist.daemons.network import workitem
        job, defer_delays = self._make_job()
        dep = mock.MagicMock()

        first_uuid = 'op-uuid-0000'
        # Insert BACKOFF_MAP_CAP + 1 distinct ops.
        for i in range(workitem.BACKOFF_MAP_CAP + 1):
            op = self._make_op(f'op-uuid-{i:04d}')
            job._apply_defer(op, waiting_on=[dep], defer_delays=defer_delays)

        # Oldest must be gone; everyone else must remain.
        self.assertNotIn(first_uuid, defer_delays)
        self.assertEqual(workitem.BACKOFF_MAP_CAP, len(defer_delays))
        for i in range(1, workitem.BACKOFF_MAP_CAP + 1):
            self.assertIn(f'op-uuid-{i:04d}', defer_delays)


class FakeNetOp:
    coalescible_key_columns = ('network_uuid',)

    def __init__(self, uuid, network_uuid):
        self.uuid = uuid
        self.network_uuid = network_uuid


class FakeUntargetedOp:
    coalescible_key_columns = ()

    def __init__(self, uuid):
        self.uuid = uuid


class RoutingKeyTest(base.ShakenFistTestCase):
    """The partition key must be stable and shared by all ops targeting the
    same network -- the safety invariant in workitem.py depends on it."""

    def _make_job(self):
        from shakenfist.daemons.network.workitem import Job
        return Job.__new__(Job)

    def test_ops_for_same_network_share_a_key(self):
        job = self._make_job()
        net = '40ab5222-a825-401f-ac76-7afd65b143ac'
        self.assertEqual(
            job._routing_key(FakeNetOp('op-1', net)),
            job._routing_key(FakeNetOp('op-2', net)))

    def test_ops_for_different_networks_have_different_keys(self):
        job = self._make_job()
        self.assertNotEqual(
            job._routing_key(
                FakeNetOp('op-1', '40ab5222-a825-401f-ac76-7afd65b143ac')),
            job._routing_key(
                FakeNetOp('op-2', '960bbd84-7da9-4213-ba99-4eee1f1ce084')))

    def test_untargeted_op_keys_on_own_uuid(self):
        job = self._make_job()
        op = FakeUntargetedOp('deadbeef-dead-beef-dead-beefdeadbeef')
        self.assertEqual('deadbeef-dead-beef-dead-beefdeadbeef',
                         job._routing_key(op))

    def test_missing_target_attribute_keys_on_own_uuid(self):
        job = self._make_job()
        op = FakeNetOp('op-1', None)
        self.assertEqual('op-1', job._routing_key(op))


class PartitionedDispatchTest(base.ShakenFistTestCase):
    """End-to-end over Job.execute(): the dispatcher routes claimed items
    into the worker pool, ops for the same network are processed by a
    single worker in claim order, and every claimed item is resolved."""

    def test_dispatch_preserves_per_network_order(self):
        net_a = 'aaaa5222-a825-401f-ac76-7afd65b143ac'
        net_b = 'bbbbbd84-7da9-4213-ba99-4eee1f1ce084'
        ops = {
            'op-a1': FakeNetOp('op-a1', net_a),
            'op-a2': FakeNetOp('op-a2', net_a),
            'op-b1': FakeNetOp('op-b1', net_b),
            'op-b2': FakeNetOp('op-b2', net_b),
        }
        items = [
            (QUEUE_NAME, f'job-{u}',
             {'operation_type': 'net_op', 'operation_uuid': u})
            for u in ['op-a1', 'op-b1', 'op-a2', 'op-b2']]

        executions = []
        exec_lock = threading.Lock()

        def fake_execute(queue_name, op, batch_size, defer_delays):
            with exec_lock:
                executions.append(
                    (threading.current_thread().name, op.uuid))

        mock_op_class = mock.MagicMock()
        mock_op_class.from_db.side_effect = lambda u: ops[u]

        dequeues = {'count': 0}

        def fake_dequeue(queue_names, limit):
            dequeues['count'] += 1
            if dequeues['count'] == 1:
                return items
            return []

        def fake_check_abort(path):
            # Workers exit via the dispatcher's shutdown sentinel so they
            # always drain their queues; the dispatcher exits once it has
            # seen an empty dequeue after the batch.
            if threading.current_thread().name.startswith('net-worker-'):
                return True
            return dequeues['count'] < 2

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
        ), mock.patch(
            'shakenfist.daemons.network.workitem.get_object_class',
            return_value=mock_op_class,
        ):
            mock_config.NODE_UUID = NODE_UUID
            mock_config.NODE_IS_NETWORK_NODE = False
            mock_config.NETWORK_OPERATION_WORKERS = 4
            mock_daemon.check_abort_path.side_effect = fake_check_abort
            mock_daemon.clear_abort_path.return_value = None
            mock_mariadb.dequeue_work_items.side_effect = fake_dequeue

            from shakenfist.daemons.network.workitem import Job
            job = Job.__new__(Job)
            job.name = 'test-worker'
            job.abort_path = '/run/sf/net-test-worker.abort'
            # Instance attribute shadows the method: capture executions
            # without running real operation logic.
            job._cluster_operation_execute = fake_execute

            job.execute()

            # Every claimed item was resolved exactly once.
            self.assertEqual(4, mock_mariadb.resolve_work_item.call_count)

        self.assertEqual(4, len(executions))
        for net_op_order in (['op-a1', 'op-a2'], ['op-b1', 'op-b2']):
            per_net = [(t, u) for t, u in executions if u in net_op_order]
            # Claim order preserved within a network...
            self.assertEqual(net_op_order, [u for _, u in per_net])
            # ... because both ops ran on the same worker thread.
            self.assertEqual(1, len({t for t, _ in per_net}))


class DatabaseUnavailableTest(base.ShakenFistTestCase):
    """A database outage must pause the network daemon, not kill its
    dispatcher or worker threads (issue 3373: DatabaseUnavailable is
    deliberately not an RpcError, so it propagates through the mariadb
    client wrappers to here)."""

    def test_dispatcher_survives_database_unavailable(self):
        """A dequeue that raises DatabaseUnavailable must not escape
        execute() -- the dispatcher pauses and retries instead of dying
        and churning through supervisor restarts."""
        dequeues = {'count': 0}

        def fake_dequeue(queue_names, limit):
            dequeues['count'] += 1
            if dequeues['count'] == 1:
                raise exceptions.DatabaseUnavailable('down')
            return []

        def fake_check_abort(path):
            if threading.current_thread().name.startswith('net-worker-'):
                return True
            return dequeues['count'] < 2

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
            mock_config.NODE_UUID = NODE_UUID
            mock_config.NODE_IS_NETWORK_NODE = False
            mock_config.NETWORK_OPERATION_WORKERS = 1
            mock_daemon.check_abort_path.side_effect = fake_check_abort
            mock_daemon.clear_abort_path.return_value = None
            mock_mariadb.dequeue_work_items.side_effect = fake_dequeue

            from shakenfist.daemons.network.workitem import Job
            job = Job.__new__(Job)
            job.name = 'test-worker'
            job.abort_path = '/run/sf/net-test-worker.abort'

            # Must not raise; the outage iteration is followed by a
            # second, successful dequeue before the loop exits.
            job.execute()

        self.assertEqual(2, dequeues['count'])

    def _run_worker_loop(self, items, fake_execute):
        """Drive _worker_loop in this thread over a pre-loaded queue.

        The queue ends with the None shutdown sentinel so the loop
        terminates deterministically.
        """
        worker_queue = queue_module.Queue()
        for item in items:
            worker_queue.put(item)
        worker_queue.put(None)

        with mock.patch(
            'shakenfist.daemons.network.workitem.daemon'
        ) as mock_daemon, mock.patch(
            'shakenfist.daemons.network.workitem.util_concurrency'
        ):
            mock_daemon.check_abort_path.return_value = True

            from shakenfist.daemons.network.workitem import Job
            job = Job.__new__(Job)
            job.name = 'test-worker'
            job.abort_path = '/run/sf/net-test-worker.abort'
            # Instance attribute shadows the method, as in
            # PartitionedDispatchTest.
            job._cluster_operation_execute = fake_execute

            job._worker_loop(0, worker_queue)

    def test_worker_abandons_item_on_database_unavailable(self):
        """DatabaseUnavailable from an op leaves its work item claimed
        (for the stuck-row reaper to re-queue) instead of resolving it,
        and the worker moves on to the next item rather than dying."""
        executed = []

        def fake_execute(queue_name, op, batch_size, defer_delays):
            executed.append(op.uuid)
            if op.uuid == 'op-1':
                raise exceptions.DatabaseUnavailable('down')

        items = [
            (QUEUE_NAME, 'job-op-1', FakeNetOp('op-1', 'net-a'), 1),
            (QUEUE_NAME, 'job-op-2', FakeNetOp('op-2', 'net-a'), 1),
        ]

        with mock.patch(
                'shakenfist.daemons.network.workitem.mariadb') as mock_mariadb:
            self._run_worker_loop(items, fake_execute)

            # op-1's work item stays claimed; only op-2 resolves.
            mock_mariadb.resolve_work_item.assert_called_once_with(
                QUEUE_NAME, 'job-op-2')

        self.assertEqual(['op-1', 'op-2'], executed)

    def test_worker_survives_database_unavailable_during_resolve(self):
        """DatabaseUnavailable from resolve_work_item itself must not
        kill the worker thread; the next item is still processed."""
        executed = []

        def fake_execute(queue_name, op, batch_size, defer_delays):
            executed.append(op.uuid)

        items = [
            (QUEUE_NAME, 'job-op-1', FakeNetOp('op-1', 'net-a'), 1),
            (QUEUE_NAME, 'job-op-2', FakeNetOp('op-2', 'net-a'), 1),
        ]

        with mock.patch(
                'shakenfist.daemons.network.workitem.mariadb') as mock_mariadb:
            mock_mariadb.resolve_work_item.side_effect = [
                exceptions.DatabaseUnavailable('down'), None]
            self._run_worker_loop(items, fake_execute)

            self.assertEqual(2, mock_mariadb.resolve_work_item.call_count)

        self.assertEqual(['op-1', 'op-2'], executed)
