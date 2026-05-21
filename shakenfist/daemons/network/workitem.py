import threading
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist import mariadb
from shakenfist.exceptions import InvalidStateException
from shakenfist.config import config
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_all_network_queues
from shakenfist.operations.baseoperation import get_node_network_queues
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


INITIAL_DEFER_DELAY = 0.1
MAX_DEFER_DELAY = 15.0
DEFER_DELAY_MULTIPLIER = 2.0
BACKOFF_MAP_CAP = 1000


class Job(util_concurrency.Job):
    def __init__(self, name):
        super().__init__()
        self.name = name

        self.abort_path = f'/run/sf/net-{name}.abort'
        daemon.clear_abort_path(self.abort_path)

        # =====================================================================
        # WARNING: SINGLE-WORKER SAFETY INVARIANT
        # ---------------------------------------------------------------------
        # The exponential back-off schedule for deferred ops below is correct
        # ONLY because each queue is serviced by exactly ONE drainer thread.
        # Today that holds for two reasons:
        #
        #   1. Per-node {node_uuid}-network-* queues are drained only by the
        #      net-worker on that specific node, and within that process by
        #      exactly one drainer thread (see ``execute()`` below).
        #   2. The cluster-wide networknode-* queues are drained only by the
        #      net-worker on the elected network node, again with exactly one
        #      drainer thread per queue.
        #
        # The drainer fan-out in ``execute()`` deliberately spawns one thread
        # per queue, not multiple threads per queue, so the per-op back-off
        # state below stays internally consistent. The lock makes the map
        # safe to read/write from sibling drainer threads but does not relax
        # the one-thread-per-queue rule.
        #
        # Valid mitigations if the topology ever changes:
        #   * Multiple drainer threads per queue -> move the back-off state
        #     into the work_item itself (it already carries defer_count) and
        #     drop this map entirely.
        #   * Cross-node voting -> return to DB-backed back-off state.
        # =====================================================================
        self._defer_delays: dict[str, float] = {}
        self._defer_delays_lock = threading.Lock()

    def _apply_defer(self, op, waiting_on):
        op_uuid = str(op.uuid)
        with self._defer_delays_lock:
            current_delay = self._defer_delays.get(op_uuid, INITIAL_DEFER_DELAY)
            self._defer_delays[op_uuid] = min(
                current_delay * DEFER_DELAY_MULTIPLIER, MAX_DEFER_DELAY)

            if len(self._defer_delays) > BACKOFF_MAP_CAP:
                # Python dicts preserve insertion order, so the first key is
                # the oldest entry — FIFO eviction.
                oldest_key = next(iter(self._defer_delays))
                del self._defer_delays[oldest_key]
        op.defer(waiting_on=waiting_on, delay=current_delay)

    def _drop_defer_entry(self, op_uuid):
        with self._defer_delays_lock:
            self._defer_delays.pop(op_uuid, None)

    def execute(self):
        LOG.info('Starting network worker')

        # Safety property: each queue must be drained by exactly one worker.
        # Per-node queues ({node_uuid}-network-*) are only drained by this
        # node's net-worker, so they are always included here.  The cluster-wide
        # networknode-* queues are only drained on the elected network node, so
        # they are added conditionally — adding them on every node would cause
        # multiple workers to race over the same queue and break the ordering
        # guarantees that the back-off map (step 1e) depends on.
        queue_names = list(get_node_network_queues(config.NODE_UUID))
        if config.NODE_IS_NETWORK_NODE:
            queue_names += get_all_network_queues()

        # Fan out one drainer thread per queue. A long-running op on one
        # priority lane no longer blocks the others: while the network
        # node was previously bottlenecked on a single thread dispatching
        # all five cluster-wide lanes serially, each lane now drains
        # independently. The one-thread-per-queue invariant the back-off
        # map depends on is preserved -- a queue still has exactly one
        # drainer -- and the lock makes the shared map safe.
        threads = []
        for queue_name in queue_names:
            thread = threading.Thread(
                target=self._drain_queue, args=(queue_name,),
                daemon=True, name=f'net-{queue_name[-32:]}')
            thread.start()
            threads.append(thread)

        # Block here until every drainer exits. Each drainer polls
        # ``daemon.check_abort_path`` on the shared abort file, so a
        # single ``set_abort_path`` from the daemon controller stops
        # them all.
        for thread in threads:
            thread.join()

    def _drain_queue(self, queue_name):
        """Inner loop for one queue. Runs in its own thread."""
        was_previously_idle = False

        while daemon.check_abort_path(self.abort_path):
            jobname_workitem = mariadb.dequeue_work_item(queue_name)

            if not jobname_workitem:
                if not was_previously_idle:
                    util_concurrency.set_thread_name(
                        f'idle:{queue_name[-32:]}')
                    LOG.debug(
                        f'Drainer for {queue_name} is now idle')
                    was_previously_idle = True
                time.sleep(0.2)
                continue

            was_previously_idle = False
            jobname, workitem = jobname_workitem
            util_concurrency.set_thread_name(jobname)
            LOG.debug(
                f'Drainer for {queue_name} is now processing job {jobname}')

            try:
                self._cluster_operation_execute(queue_name, workitem)
            finally:
                mariadb.resolve_work_item(queue_name, jobname)

    def _cluster_operation_execute(self, queue_name, workitem):
        op_type = workitem.get('operation_type')
        op_uuid = workitem.get('operation_uuid')
        op = get_object_class(op_type).from_db(op_uuid)

        if not op:
            self.log.error('Operation not found')
            return

        terminal_states = {
            BaseClusterOperation.STATE_ABORT,
            BaseClusterOperation.STATE_COMPLETE,
            dbo.STATE_DELETED,
            dbo.STATE_ERROR,
        }
        if op.state.value in terminal_states:
            op.add_event(
                EVENT_TYPE_AUDIT,
                f'skipping op already in terminal state {op.state.value}')
            self._drop_defer_entry(str(op.uuid))
            return

        op.queue_name = queue_name
        op.current_defer_count = workitem.get('defer_count', 0)

        # Ensure our dependencies are met.
        for dep in op.depends_on:
            dep_op = get_object_class(dep['op_type']).from_db(dep['op_uuid'])
            if not dep_op:
                op.add_event(
                    EVENT_TYPE_AUDIT,
                    'cancelling operation, as dependency does not exist',
                    extra={
                        'dep_object_type': dep['op_type'],
                        'dep_object_uuid': dep['op_uuid']
                    })
                op.state = BaseClusterOperation.STATE_ERROR
                return

            dep_op_state = dep_op.state.value
            if dep_op_state in [BaseClusterOperation.STATE_ERROR,
                                BaseClusterOperation.STATE_DELETED,
                                BaseClusterOperation.STATE_ABORT]:
                op.add_event(
                    EVENT_TYPE_AUDIT,
                    'aborting operation, as dependency is unsuitable',
                    extra={
                        'dep_object_type': dep_op.object_type,
                        'dep_object_uuid': dep_op.uuid,
                        'dep_object_state': dep_op_state
                    })

                try:
                    op.state = BaseClusterOperation.STATE_ABORT
                except InvalidStateException:
                    op.add_event(EVENT_TYPE_AUDIT, 'failed to abort operation')
                return

            if dep_op_state in [BaseClusterOperation.STATE_INITIAL,
                                BaseClusterOperation.STATE_QUEUED,
                                BaseClusterOperation.STATE_PREFLIGHT,
                                BaseClusterOperation.STATE_EXECUTING]:
                # Dependency not yet ready, we should defer
                self._apply_defer(op, waiting_on=[dep_op])
                return

        # Ensure that we are running after any runs_after requirements.
        for dep in op.runs_after:
            dep_op = get_object_class(dep['op_type']).from_db(dep['op_uuid'])
            if not dep_op:
                # Not fatal because otherwise a missing cluster operation
                # could cause the entire cluster to stop being able to manage
                # a given object.
                op.add_event(
                    EVENT_TYPE_AUDIT,
                    'warning, runs_after dependency is missing',
                    extra={
                        'dep_object_type': dep['op_type'],
                        'dep_object_uuid': dep['op_uuid']
                    })
                continue

            dep_op_state = dep_op.state.value
            if dep_op_state in [BaseClusterOperation.STATE_INITIAL,
                                BaseClusterOperation.STATE_QUEUED,
                                BaseClusterOperation.STATE_PREFLIGHT,
                                BaseClusterOperation.STATE_EXECUTING]:
                # Dependency not yet ready, we should defer
                self._apply_defer(op, waiting_on=[dep_op])
                return

        # We're good to go! All dependencies are met, so we no longer need
        # the back-off entry for this op — drop it so any later defer (e.g.
        # this op chained onto a different dep) starts back at the initial
        # delay rather than carrying over the previous chain's depth.
        self._drop_defer_entry(str(op.uuid))
        start_time = time.time()
        op.execute()
        # The op may have transitioned to a terminal state during execute();
        # drop the entry again in case it was somehow re-populated.
        self._drop_defer_entry(str(op.uuid))
        op.add_event(
            EVENT_TYPE_USAGE, 'execution duration',
            extra={
                'seconds': time.time() - start_time
            })
