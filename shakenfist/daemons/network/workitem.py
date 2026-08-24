import queue as queue_module
import threading
import time
import zlib

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist.daemons.daemon import IdlePollBackoff
from shakenfist import mariadb
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.exceptions import InvalidStateException
from shakenfist.config import config
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_all_network_queues
from shakenfist.operations.baseoperation import get_node_network_queues
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions


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
        # WARNING: PARTITIONED-WORKER SAFETY INVARIANT
        # ---------------------------------------------------------------------
        # Every operation is routed to a worker thread by a stable hash of
        # its target (the network for network ops -- see _routing_key()), so
        # any given op, and every op sharing its target, is only ever
        # processed by ONE worker thread. Three things depend on this:
        #
        #   1. Per-target ordering: ops for the same network execute in the
        #      order they were claimed, because they all pass through the
        #      same worker's FIFO queue.
        #   2. The exponential back-off schedule for deferred ops (each
        #      worker's map is single-writer for any op_uuid, because the
        #      routing key is stable across defers and re-dequeues).
        #   3. The cross-op coalescing fold in BaseClusterOperation.execute()
        #      only claims sibling ops with the same target, which by (1)
        #      route to the same worker *within this process* -- so a fold
        #      can never mark an op complete while another thread here is
        #      executing it.
        #
        #      Note what (1) does NOT give you: routing is per-process, so
        #      two ops with the same target on two different queues are
        #      drained by two different daemons and (1) says nothing about
        #      them. The fold's SQL cannot filter on queue either --
        #      cluster_operations has no queue column. What actually makes
        #      the fold safe is that every coalescible task is confined to
        #      the cluster-wide networknode queue, enforced at enqueue time
        #      by the InvalidCoalescibleEnqueue guard in
        #      schema/operations/net_op.py. Do not weaken that guard on the
        #      strength of this invariant; it is the guard that holds, not
        #      this one.
        #
        # Additionally each queue is drained by exactly ONE dispatcher
        # process: per-node {node_uuid}-network-* queues only by that node's
        # net-worker, and the cluster-wide networknode-* queues only by the
        # elected network node (the `if config.NODE_IS_NETWORK_NODE` guard
        # in execute()).
        #
        # DO NOT change the routing so ops for one target can reach two
        # workers, and DO NOT let two nodes drain the same queue, without
        # revisiting all three properties above (cross-node voting would
        # need DB-backed back-off state and a DB-side fold guard).
        # =====================================================================

    def _routing_key(self, op):
        """The partition key for an operation.

        Ops sharing a target must always land on the same worker (see the
        safety invariant above). The target column used by the coalescing
        fold is authoritative when set; network_uuid covers the op types
        that don't declare one; and the op's own uuid is a safe fallback
        (an unshared key trivially preserves the invariant).
        """
        target_column = type(op).coalescible_target_column or 'network_uuid'
        target = getattr(op, target_column, None)
        if target is None:
            return str(op.uuid)
        return str(target)

    def _apply_defer(self, op, waiting_on, defer_delays):
        op_uuid = str(op.uuid)
        current_delay = defer_delays.get(op_uuid, INITIAL_DEFER_DELAY)
        op.defer(waiting_on=waiting_on, delay=current_delay)
        defer_delays[op_uuid] = min(
            current_delay * DEFER_DELAY_MULTIPLIER, MAX_DEFER_DELAY)

        if len(defer_delays) > BACKOFF_MAP_CAP:
            # Python dicts preserve insertion order, so the first key is
            # the oldest entry — FIFO eviction.
            oldest_key = next(iter(defer_delays))
            del defer_delays[oldest_key]

    def _drop_defer_entry(self, op_uuid, defer_delays):
        defer_delays.pop(op_uuid, None)

    def _worker_loop(self, index, worker_queue):
        """One partition of the network worker pool.

        Drains its own FIFO queue of pre-routed (queue_name, jobname, op,
        batch_size) tuples. Exits on the None sentinel or when the abort
        path fires; any items still claimed at that point are recovered
        by the stuck-row reaper, exactly as for a worker crash.
        """
        defer_delays: dict[str, float] = {}
        while daemon.check_abort_path(self.abort_path):
            try:
                item = worker_queue.get(timeout=0.5)
            except queue_module.Empty:
                continue
            if item is None:
                return

            queue_name, jobname, op, batch_size = item
            util_concurrency.set_thread_name(jobname)
            LOG.debug(
                f'Network worker {index} is now processing job {jobname}')
            resolve = True
            try:
                self._cluster_operation_execute(
                    queue_name, op, batch_size, defer_delays)
            except DatabaseUnavailable:
                # The database went away mid-operation. Leave the work
                # item claimed rather than resolving it: the stuck-row
                # reaper re-queues it once the database returns, exactly
                # as for a worker crash, so the op is retried rather
                # than silently dropped.
                LOG.warning(
                    f'Database service unavailable, abandoning work item '
                    f'{jobname} for the stuck-row reaper')
                resolve = False
            except Exception as e:
                util_exceptions.ignore_exception(
                    f'network worker {index}', e)
            finally:
                if resolve:
                    try:
                        mariadb.resolve_work_item(queue_name, jobname)
                    except DatabaseUnavailable:
                        # Same recovery path as above: the row stays
                        # claimed and the stuck-row reaper re-queues it.
                        LOG.warning(
                            f'Database service unavailable resolving work '
                            f'item {jobname}, leaving it for the stuck-row '
                            f'reaper')
                util_concurrency.set_thread_name(f'net-worker-{index}')

    def execute(self):
        LOG.info('Starting network worker')
        was_previously_idle = False

        # Safety property: each queue must be drained by exactly one
        # dispatcher. Per-node queues ({node_uuid}-network-*) are only
        # drained by this node's net-worker, so they are always included
        # here. The cluster-wide networknode-* queues are only drained on
        # the elected network node, so they are added conditionally —
        # adding them on every node would cause multiple dispatchers to
        # race over the same queue and break the ordering and coalescing
        # guarantees described in the safety invariant above.
        queue_names = list(get_node_network_queues(config.NODE_UUID))
        if config.NODE_IS_NETWORK_NODE:
            queue_names += get_all_network_queues()

        # A partitioned worker pool: this thread claims and routes work,
        # the workers execute it. Ops are partitioned by target so ops
        # for one network execute serially in claim order while different
        # networks proceed in parallel -- under parallel teardown load a
        # single serial worker was observed 90+ seconds behind, blowing
        # the API_ASYNC_WAIT deadline of callers waiting on their op.
        worker_count = max(1, config.NETWORK_OPERATION_WORKERS)
        # Bounded so a wedged worker applies backpressure to the
        # dispatcher (degrading towards the old serial behaviour) rather
        # than accumulating claimed-but-unprocessed items in memory.
        worker_queues: list[queue_module.Queue] = [
            queue_module.Queue(maxsize=BATCH_SIZE * 2)
            for _ in range(worker_count)]
        worker_threads = []
        for i in range(worker_count):
            t = threading.Thread(
                target=self._worker_loop, args=(i, worker_queues[i]),
                name=f'net-worker-{i}', daemon=True)
            t.start()
            worker_threads.append(t)

        try:
            # The queue names list is constant for the dispatcher's
            # lifetime (NODE_UUID and NODE_IS_NETWORK_NODE don't change at
            # runtime). The first entry is highest priority -- the MariaDB
            # query honours that order via FIELD().
            util_concurrency.set_thread_name('net-dispatcher')
            # Adaptive backoff so an idle dispatcher stops issuing ~5 empty
            # Dequeue/s per node; snaps back to fast polling on any work. See
            # issue #3499.
            poll_backoff = IdlePollBackoff()
            while daemon.check_abort_path(self.abort_path):
                try:
                    # One round trip claims up to BATCH_SIZE items in
                    # priority order; the win is the dispatcher gRPC count
                    # (1 instead of len(queue_names)) and that
                    # lower-priority queues spill in once the higher ones
                    # are exhausted within a single batch.
                    items = mariadb.dequeue_work_items(
                        queue_names, limit=BATCH_SIZE)

                    if not items:
                        if not was_previously_idle:
                            util_concurrency.set_thread_name('idle')
                            LOG.debug('This network thread is now idle')
                            was_previously_idle = True
                        # Sleep the backoff interval in short chunks so
                        # shutdown stays responsive even at the cap.
                        remaining = poll_backoff.next_empty_interval()
                        while (remaining > 0
                               and daemon.check_abort_path(self.abort_path)):
                            chunk = min(0.2, remaining)
                            time.sleep(chunk)
                            remaining -= chunk
                        continue

                    poll_backoff.reset()
                    if was_previously_idle:
                        util_concurrency.set_thread_name('net-dispatcher')
                        was_previously_idle = False
                    batch_size = len(items)
                    for queue_name, jobname, workitem in items:
                        op_type = workitem.get('operation_type')
                        op_uuid = workitem.get('operation_uuid')
                        op = get_object_class(op_type).from_db(op_uuid)

                        if not op:
                            LOG.with_fields({
                                'operation_type': op_type,
                                'operation_uuid': op_uuid,
                            }).error('Operation not found')
                            mariadb.resolve_work_item(queue_name, jobname)
                            continue

                        op.current_defer_count = workitem.get('defer_count', 0)
                        index = (zlib.crc32(self._routing_key(op).encode())
                                 % worker_count)
                        item = (queue_name, jobname, op, batch_size)
                        # A blocking put with a timeout so shutdown stays
                        # responsive while a full worker queue applies
                        # backpressure.
                        while daemon.check_abort_path(self.abort_path):
                            try:
                                worker_queues[index].put(item, timeout=1)
                                break
                            except queue_module.Full:
                                continue

                        # Honour abort mid-batch so shutdown is responsive
                        # even when a batch is large.
                        if not daemon.check_abort_path(self.abort_path):
                            break
                except DatabaseUnavailable:
                    # The database will come back; pause dispatch rather
                    # than letting this thread die and churn through
                    # supervisor restarts. Items claimed but not yet
                    # routed when a mid-batch failure struck are
                    # recovered by the stuck-row reaper, exactly as for
                    # a worker crash.
                    LOG.warning(
                        'Database service unavailable, pausing network '
                        'work dispatch')
                    time.sleep(5)
        finally:
            for wq in worker_queues:
                try:
                    wq.put_nowait(None)
                except queue_module.Full:
                    # The worker will notice the abort path instead.
                    pass
            for t in worker_threads:
                t.join(timeout=5)

    def _cluster_operation_execute(self, queue_name, op, batch_size,
                                   defer_delays):
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
            self._drop_defer_entry(str(op.uuid), defer_delays)
            return

        op.queue_name = queue_name
        # Hint for the cross-op coalescing fold in
        # ``BaseClusterOperation.execute()``: when the dispatcher's
        # batch was size 1 (this op was the only ready item) the fold
        # skips its SQL round-trip.
        op.dispatcher_batch_size = batch_size

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
                self._apply_defer(op, waiting_on=[dep_op], defer_delays=defer_delays)
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
                self._apply_defer(op, waiting_on=[dep_op], defer_delays=defer_delays)
                return

        # We're good to go! All dependencies are met, so we no longer need
        # the back-off entry for this op — drop it so any later defer (e.g.
        # this op chained onto a different dep) starts back at the initial
        # delay rather than carrying over the previous chain's depth.
        self._drop_defer_entry(str(op.uuid), defer_delays)
        start_time = time.time()
        op.execute()
        # The op may have transitioned to a terminal state during execute();
        # drop the entry again in case it was somehow re-populated.
        self._drop_defer_entry(str(op.uuid), defer_delays)
        # One end-of-op event carries both the queue-wait time and the
        # execution duration, since both are knowable here and emitting
        # two separate events doubles the eventlog gRPC cost on the
        # critical path. ``wait_seconds`` is only populated when the
        # op carries a ``created_at`` (i.e. it was loaded from the
        # cluster_operations table -- in-memory ops loaded outside the
        # dispatch path don't have one).
        extra = {'seconds': time.time() - start_time}
        if op.created_at is not None:
            extra['wait_seconds'] = start_time - op.created_at
            extra['defer_count'] = op.current_defer_count
            extra['queue_name'] = queue_name
        op.add_event(EVENT_TYPE_USAGE, 'execution duration', extra=extra)
