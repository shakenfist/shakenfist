import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
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

# Maximum jobs claimed per dequeue round trip. Chosen so the worst-
# case orphan window on worker crash (BATCH_SIZE items sitting with
# claimed_at set until the stuck-row reaper finds them) stays small,
# while the per-iteration dequeue cost is amortised across enough
# work to matter under load.
BATCH_SIZE = 10


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
        # ONLY because each queue this worker drains is serviced by exactly
        # ONE worker process. Today that holds for two reasons:
        #
        #   1. Per-node {node_uuid}-network-* queues are drained only by the
        #      net-worker on that specific node.
        #   2. The cluster-wide networknode-* queues are drained only by the
        #      net-worker on the elected network node (enforced by the
        #      `if config.NODE_IS_NETWORK_NODE` guard in execute()).
        #
        # DO NOT move to multi-worker dequeue (worker pool inside one process,
        # or multiple nodes voting on the same queue) without fixing this
        # map. Two workers servicing the same queue can independently defer
        # the same op and end up with inconsistent delays, double-enqueueing
        # the op and breaking the back-off schedule.
        #
        # Valid mitigations if the topology ever changes:
        #   * In-process worker pool -> share one map behind a lock.
        #   * Cross-node voting       -> return to DB-backed back-off state.
        # =====================================================================
        self._defer_delays: dict[str, float] = {}

    def _apply_defer(self, op, waiting_on):
        op_uuid = str(op.uuid)
        current_delay = self._defer_delays.get(op_uuid, INITIAL_DEFER_DELAY)
        op.defer(waiting_on=waiting_on, delay=current_delay)
        self._defer_delays[op_uuid] = min(
            current_delay * DEFER_DELAY_MULTIPLIER, MAX_DEFER_DELAY)

        if len(self._defer_delays) > BACKOFF_MAP_CAP:
            # Python dicts preserve insertion order, so the first key is
            # the oldest entry — FIFO eviction.
            oldest_key = next(iter(self._defer_delays))
            del self._defer_delays[oldest_key]

    def _drop_defer_entry(self, op_uuid):
        self._defer_delays.pop(op_uuid, None)

    def execute(self):
        LOG.info('Starting network worker')
        was_previously_idle = False

        # NOTE(mikal): there's really nothing stopping us from processing a bunch
        # of these jobs in parallel with a pool of workers, but I am not sure its
        # worth the complexity right now. Are we really going to be changing
        # networks that much?

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

        # Pulled out of the per-iteration body: this list is constant for
        # the worker's lifetime (NODE_UUID and NODE_IS_NETWORK_NODE don't
        # change at runtime), so there's no reason to materialise it on
        # every poll. The first entry is highest priority -- the MariaDB
        # query honours that order via FIELD().
        while daemon.check_abort_path(self.abort_path):
            # One round trip claims up to BATCH_SIZE items in priority
            # order. Since this worker is single-threaded the batch is
            # then drained sequentially; the win is the dispatcher
            # gRPC count (1 instead of len(queue_names)) and that
            # lower-priority queues spill in once the higher ones are
            # exhausted within a single batch.
            items = mariadb.dequeue_work_items(
                queue_names, limit=BATCH_SIZE)

            if not items:
                if not was_previously_idle:
                    util_concurrency.set_thread_name('idle')
                    LOG.debug('This network thread is now idle')
                    was_previously_idle = True
                time.sleep(0.2)
                continue

            was_previously_idle = False
            for queue_name, jobname, workitem in items:
                util_concurrency.set_thread_name(jobname)
                LOG.debug(
                    f'This network thread is now processing job {jobname}')

                try:
                    self._cluster_operation_execute(queue_name, workitem)
                finally:
                    mariadb.resolve_work_item(queue_name, jobname)

                # Honour abort mid-batch so shutdown is responsive even
                # when a batch is large.
                if not daemon.check_abort_path(self.abort_path):
                    break

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
        # Emit one event at the dispatcher-pickup boundary carrying the
        # queue-wait time (start - created_at). This is the only place
        # in the pipeline that can observe that delta -- the caller side
        # has created_at but not start_time, the apply side has neither.
        # ``current_defer_count`` is included so a deferred-and-retried op
        # is distinguishable in eventlog from a first-time pickup with the
        # same wait_seconds value.
        if op.created_at is not None:
            op.add_event(
                EVENT_TYPE_STATUS, 'started executing',
                extra={
                    'wait_seconds': start_time - op.created_at,
                    'defer_count': op.current_defer_count,
                    'queue_name': queue_name,
                })
        op.execute()
        # The op may have transitioned to a terminal state during execute();
        # drop the entry again in case it was somehow re-populated.
        self._drop_defer_entry(str(op.uuid))
        op.add_event(
            EVENT_TYPE_USAGE, 'execution duration',
            extra={
                'seconds': time.time() - start_time
            })
