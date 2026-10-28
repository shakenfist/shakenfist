import collections
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
from shakenfist.exceptions import StateWriteFailed
from shakenfist.config import config
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import DEPENDENCY_DEFER_BUDGET
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
# case orphan window on a crash (BATCH_SIZE items sitting with
# claimed_at set until sf-net starts again and clears them, or the
# stuck-row reaper finds them) stays small,
# while the per-iteration dequeue cost is amortised across enough
# work to matter under load.
BATCH_SIZE = 10

# How often a dispatcher which is waiting for its workers to finish before it
# exits says what it is waiting for.
WORKER_STOP_LOG_INTERVAL = 30


def dispatcher_queue_names(node_uuid):
    """The queues this node's network dispatcher drains, highest priority first.

    Each queue is drained by exactly one dispatcher (see the safety invariant
    in Job). Per-node queues ({node_uuid}-network-*) are only drained by this
    node's net-worker, so they are always included. The cluster-wide
    networknode-* queues are only drained on the elected network node, so they
    are added conditionally -- adding them on every node would cause multiple
    dispatchers to race over the same queue and break the ordering and
    coalescing guarantees the invariant describes.
    """
    queue_names = list(get_node_network_queues(node_uuid))
    if config.NODE_IS_NETWORK_NODE:
        queue_names += get_all_network_queues()
    return queue_names


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
        #      never claims an op another thread here is running, and the
        #      survivor never completes one either.
        #
        #      The fold moves each sibling from queued to executing, and
        #      the survivor moves them on to complete only after its own
        #      tasks have succeeded. On any other outcome -- an error, a
        #      defer back to queued, an exception, a crash -- the siblings
        #      are left executing, each with the work_queue row the fold
        #      never touched. When that row is dequeued here the sibling
        #      runs itself, because _cluster_operation_execute() skips
        #      only terminal states and a transition to executing from
        #      executing is a no-op. So a sibling's final state is always
        #      a real outcome, and nothing waiting on it is released before
        #      the work is done (#4638). That makes "sf-net re-runs an op it
        #      dequeues in executing" part of this invariant: treat
        #      executing as terminal there and every folded sibling of a
        #      survivor which failed is stranded until the orphan reaper
        #      errors it.
        #
        #      Note what (1) does NOT give you: routing is per-process, so
        #      two ops with the same target on two different queues are
        #      drained by two different daemons and (1) says nothing about
        #      them. The fold's SQL cannot filter on queue either --
        #      cluster_operations has no queue column. So the *key* is what
        #      has to confine a fold to one process, and there are two ways
        #      it does:
        #
        #        a. A cluster-wide networknode-* queue is drained by the
        #           single elected network node, so every sibling the fold
        #           can find is one this same process would have run.
        #        b. A key which includes node_uuid, on an op which carries
        #           one. That case rests on four links, all of which have
        #           to hold together:
        #             i.   a (network_uuid, node_uuid) key can only match
        #                  ops carrying that node's uuid;
        #             ii.  every such op was enqueued to that node's own
        #                  {node_uuid}-network-* queue. net_op's
        #                  create_and_enqueue derives node_uuid from the
        #                  enqueue target, and the queue name is
        #                  {target}-{family}-{priority}, so this holds
        #                  exactly when the enqueue also chose
        #                  family='network' -- which is why the
        #                  enqueue-time guard tests the family and not
        #                  only the key;
        #             iii. that queue is drained by exactly one dispatcher
        #                  process, that node's own net-worker; and
        #             iv.  within that process, every op for the same
        #                  network hashes to the same worker thread via
        #                  _routing_key() below.
        #           Together those give property (3) for per-node queues as
        #           well: the folded sibling is not being executed
        #           anywhere, because the only thread which could be
        #           executing it is the one doing the folding.
        #
        #           The links do not all cost the same to break. Without
        #           (i), (ii) or (iii) a fold claims another node's
        #           operation, and a survivor which succeeds marks it
        #           complete with that node's work undone -- the
        #           cross-node fold. Without (iv) two threads here run the
        #           same idempotent work at once, which wastes effort but
        #           loses nothing: the sibling is never complete unless a
        #           run of its work succeeded. A dispatcher restart breaks
        #           (iv) for a moment today, because the old workers can
        #           outlive it, and per-target ordering with it (#4645).
        #
        #      Both guards enforcing this are key-aware and family-aware:
        #      InvalidCoalescibleEnqueue in schema/operations/net_op.py
        #      refuses the enqueue unless the key names node_uuid and the
        #      family is 'network', and BaseClusterOperation.execute()
        #      records key_cannot_distinguish_queue and skips the fold
        #      unless the queue is {node_uuid}-network-*. A key naming
        #      node_uuid is necessary but not sufficient: the queue family
        #      decides which dispatcher drains the work. The same two
        #      guards are also what keep every foldable operation off
        #      sf-queues, below. Weaken neither by arguing from this
        #      invariant alone -- link (iv) is this invariant, and it is
        #      one link of four.
        #
        #      The argument does NOT extend to sf-queues, and that is a
        #      deliberate limit rather than an oversight. Every operation a
        #      fold can claim has to be drained by sf-net, never by
        #      sf-queues, because sf-queues drops any operation it dequeues
        #      which is not queued. A folded sibling reaching it would be
        #      dropped while still executing, and if the survivor then
        #      failed nothing would ever run it: it would sit there until
        #      the orphan reaper errored it. sf-queues is a
        #      WorkerPoolDaemon as well: it fills its spare slots from a
        #      single dequeue_work_items() call
        #      (shakenfist/daemons/daemon.py) and starts one worker per
        #      claimed item, with no routing key and no per-target
        #      affinity, so link (iv) simply does not exist there. Two of
        #      its workers can hold two ops for the same (network, node)
        #      at once, and one's fold can claim the other's op in the
        #      window between that worker's queued-state check and its own
        #      transition to executing. That is why
        #      NodeNetOp.network_apply_create_hypervisor is not
        #      coalescible even though its model already carries both key
        #      columns. Revisiting that is tracked in issue #4017, and
        #      would mean teaching sf-queues to run an op it dequeues in
        #      executing.
        #
        # Additionally each queue is drained by exactly ONE dispatcher
        # process: per-node {node_uuid}-network-* queues only by that node's
        # net-worker, and the cluster-wide networknode-* queues only by the
        # elected network node (the `if config.NODE_IS_NETWORK_NODE` guard
        # in dispatcher_queue_names()). And one dispatcher's workers at a
        # time: a dispatcher which exits first waits for its workers to stop
        # (_stop_workers()), so the one the monitor restarts never runs
        # alongside them.
        #
        # DO NOT change the routing so ops for one target can reach two
        # workers, and DO NOT let two nodes drain the same queue, without
        # revisiting all three properties above (cross-node voting would
        # need DB-backed back-off state and a DB-side fold guard).
        # =====================================================================

    def _routing_key(self, op):
        """The partition key for an operation.

        Ops sharing a target must always land on the same worker (see the
        safety invariant above). The *first* column of the coalescing key
        is authoritative when the op type declares one; network_uuid
        covers the op types that don't; and the op's own uuid is a safe
        fallback (an unshared key trivially preserves the invariant).

        Only the first column is used: routing has to be at least as
        coarse as the fold, so that any two ops a fold could match are
        drained by the same worker. A finer routing key than the fold's
        would split siblings across workers and break property (3) of
        the invariant above.
        """
        key_columns = type(op).coalescible_key_columns
        target_column = key_columns[0] if key_columns else 'network_uuid'
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

    def _worker_loop(self, index, worker_queue, stop):
        """One partition of the network worker pool.

        Drains its own FIFO queue of pre-routed (queue_name, jobname, op,
        batch_size) tuples. Exits once stop is set or the abort path
        fires, finishing the operation it is running first; the dispatcher
        gives back the claims on whatever is still in its queue.
        """
        defer_delays: dict[str, float] = {}
        while not stop.is_set() and daemon.check_abort_path(self.abort_path):
            try:
                item = worker_queue.get(timeout=0.5)
            except queue_module.Empty:
                continue

            queue_name, jobname, op, batch_size = item
            util_concurrency.set_thread_name(jobname)
            LOG.debug(
                f'Network worker {index} is now processing job {jobname}')
            resolve = True
            try:
                self._cluster_operation_execute(
                    queue_name, op, batch_size, defer_delays)
            except (DatabaseUnavailable, StateWriteFailed):
                # The database went away mid-operation, or answered but
                # reported a state write as failed (issue 4273: an
                # InnoDB deadlock whose retries were exhausted). Leave
                # the work item claimed rather than resolving it: the
                # stuck-row reaper re-queues it once the database
                # recovers, exactly as for a worker crash, so the op is
                # retried rather than orphaned in 'queued' where the
                # enqueue-side dedup adopts it forever.
                LOG.warning(
                    f'Database write failed or service unavailable, '
                    f'abandoning work item {jobname} for the stuck-row '
                    f'reaper')
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

    def _stop_workers(self, stop, worker_threads):
        """Stop the workers, and wait for every one of them to exit.

        The dispatcher must not exit while a worker it started still runs.
        The monitor restarts a dispatcher which has exited, and the new one
        starts its own workers, so an old worker still running would execute
        a network's operations alongside the new pool's worker for that
        network. That breaks link (iv) of the safety invariant above, and
        per-target ordering with it. The old workers used to be asked to stop
        with a sentinel skipped when their queue was full, and joined for
        five seconds (#4645). So wait without a limit: a worker finishes the
        operation it is running and exits. At process exit the daemon
        abandons this thread after its own stop grace, and the workers go
        with the process.

        The cost is liveness: a worker wedged in an operation keeps this
        dispatcher from exiting, so no network on the node is dispatched
        until sf-net restarts, which recovers through reset_network_queues.
        The warning below repeats every WORKER_STOP_LOG_INTERVAL until then.
        """
        stop.set()
        for t in worker_threads:
            t.join(WORKER_STOP_LOG_INTERVAL)
            while t.is_alive():
                LOG.with_fields({'thread': t.name}).warning(
                    'Waiting for a network worker to finish its operation '
                    'before the dispatcher exits')
                t.join(WORKER_STOP_LOG_INTERVAL)

    def _release_claims(self, unrouted, worker_queues):
        """Give back the claims on work items this dispatcher will not run.

        Those are the items it claimed but had not yet routed, and those
        still waiting in a worker queue when the workers stopped. Left
        claimed, nothing ran them until the cluster's stuck-row reaper found
        them CLUSTER_OP_STUCK_THRESHOLD (30 minutes) later. Every request for
        the same work in the meantime, sf-net's own startup restore included,
        was deduplicated onto the stranded operation and waited with it
        (#4660). Must only be called once the workers have exited.
        """
        jobs = [(queue_name, jobname) for queue_name, jobname, _ in unrouted]
        for worker_queue in worker_queues:
            while True:
                try:
                    queue_name, jobname, _, _ = worker_queue.get_nowait()
                except queue_module.Empty:
                    break
                jobs.append((queue_name, jobname))

        # Never raises: this runs on the dispatcher's way out, where an
        # exception would replace the one it is failing with, and abandon the
        # claims not yet given back. sf-net's next start, or the stuck-row
        # reaper, frees any claim left here.
        released = []
        not_released = []
        remaining = collections.deque(jobname for _, jobname in jobs)
        while remaining:
            jobname = remaining.popleft()
            try:
                # False means the claim was not cleared: the database
                # reported an error, or the row was already resolved or
                # reaped.
                if mariadb.clear_work_queue_claim(int(jobname)):
                    released.append(jobname)
                else:
                    not_released.append(jobname)
            except DatabaseUnavailable:
                # Each call has already waited out its retries, so do not
                # make the rest wait in turn and delay the restart.
                not_released.append(jobname)
                not_released.extend(remaining)
                remaining.clear()
            except Exception as e:
                util_exceptions.ignore_exception(
                    f'releasing the claim on network work item {jobname}', e)
                not_released.append(jobname)
        if jobs:
            LOG.with_fields({
                'released': released,
                'not_released': not_released
            }).info('Released claims on network work items not run')

    def _route_item(self, queue_name, jobname, workitem, batch_size,
                    worker_count):
        """Load a claimed work item's operation and pick its worker.

        Returns the worker index and the item to queue for it, or None for
        an operation which no longer exists, whose work item is resolved.
        """
        op_type = workitem.get('operation_type')
        op_uuid = workitem.get('operation_uuid')
        op = get_object_class(op_type).from_db(op_uuid)
        if not op:
            LOG.with_fields({
                'operation_type': op_type,
                'operation_uuid': op_uuid,
            }).error('Operation not found')
            mariadb.resolve_work_item(queue_name, jobname)
            return None

        op.current_defer_count = workitem.get('defer_count', 0)
        index = zlib.crc32(self._routing_key(op).encode()) % worker_count
        return index, (queue_name, jobname, op, batch_size)

    def execute(self):
        LOG.info('Starting network worker')
        was_previously_idle = False
        queue_names = dispatcher_queue_names(config.NODE_UUID)

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
        stop_workers = threading.Event()
        worker_threads = []
        for i in range(worker_count):
            t = threading.Thread(
                target=self._worker_loop,
                args=(i, worker_queues[i], stop_workers),
                name=f'net-worker-{i}', daemon=True)
            t.start()
            worker_threads.append(t)

        # Items claimed from the database and not yet handed to a worker.
        # Kept across iterations, so that items a database outage
        # interrupted are routed once it is back rather than left claimed.
        unrouted: collections.deque = collections.deque()
        batch_size = 0

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
                    if not unrouted:
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
                                   and daemon.check_abort_path(
                                       self.abort_path)):
                                chunk = min(0.2, remaining)
                                time.sleep(chunk)
                                remaining -= chunk
                            continue

                        poll_backoff.reset()
                        if was_previously_idle:
                            util_concurrency.set_thread_name('net-dispatcher')
                            was_previously_idle = False
                        batch_size = len(items)
                        unrouted.extend(items)

                    while unrouted:
                        queue_name, jobname, workitem = unrouted[0]
                        try:
                            routed = self._route_item(
                                queue_name, jobname, workitem, batch_size,
                                worker_count)
                        except DatabaseUnavailable:
                            raise
                        except Exception as e:
                            # Skip an item which cannot be loaded or routed,
                            # and leave it claimed: the stuck-row reaper
                            # retries it, and errors it after
                            # CLUSTER_OP_MAX_ATTEMPTS. Letting this end the
                            # dispatcher would hand the claim back on the way
                            # out, so the next dispatcher would claim it first
                            # and end too.
                            util_exceptions.ignore_exception(
                                f'routing network work item {jobname}', e)
                            unrouted.popleft()
                            continue

                        if not routed:
                            unrouted.popleft()
                            continue

                        index, item = routed
                        # A blocking put with a timeout so shutdown stays
                        # responsive while a full worker queue applies
                        # backpressure.
                        while daemon.check_abort_path(self.abort_path):
                            try:
                                worker_queues[index].put(item, timeout=1)
                                unrouted.popleft()
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
                    # routed when a mid-batch failure struck stay in
                    # unrouted, and are routed once it is back.
                    LOG.warning(
                        'Database service unavailable, pausing network '
                        'work dispatch')
                    time.sleep(5)
        finally:
            self._stop_workers(stop_workers, worker_threads)
            self._release_claims(unrouted, worker_queues)

    def _cluster_operation_execute(self, queue_name, op, batch_size,
                                   defer_delays):
        # Executing is deliberately not here. An op dequeued in executing
        # is a sibling a coalescing survivor folded and did not complete,
        # because the survivor failed, deferred or died; running it now is
        # the only way it reaches a real outcome. See property (3) of the
        # safety invariant above.
        terminal_states = {
            BaseClusterOperation.STATE_ABORT,
            BaseClusterOperation.STATE_COMPLETE,
            dbo.STATE_DELETED,
            dbo.STATE_ERROR,
        }
        state = op.state
        if state.value in terminal_states:
            op.add_event(
                EVENT_TYPE_AUDIT,
                f'skipping op already in terminal state {state.value}')
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
                op.cancel_for_missing_dependency(dep)
                self._drop_defer_entry(str(op.uuid), defer_delays)
                return

            dep_op_state = dep_op.state.value
            if dep_op_state in [BaseClusterOperation.STATE_ERROR,
                                BaseClusterOperation.STATE_DELETED,
                                BaseClusterOperation.STATE_ABORT]:
                op.abort_for_unsuitable_dependency(dep_op)
                self._drop_defer_entry(str(op.uuid), defer_delays)
                return

            if dep_op_state in [BaseClusterOperation.STATE_INITIAL,
                                BaseClusterOperation.STATE_QUEUED,
                                BaseClusterOperation.STATE_PREFLIGHT,
                                BaseClusterOperation.STATE_EXECUTING]:
                # A dependency which never goes terminal must not park us
                # forever (issue 3992) -- give up loudly once the defer
                # budget is exhausted. The budget is checked against the
                # defer_count the work item persists, not this worker's
                # in-memory delay map, so it survives daemon restarts.
                if op.current_defer_count >= DEPENDENCY_DEFER_BUDGET:
                    op.abandon_dependency_wait(dep_op)
                    self._drop_defer_entry(str(op.uuid), defer_delays)
                    return

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
                # runs_after is an ordering hint, and a missing dependency
                # already warns and continues rather than making the object
                # unmanageable -- treat an exhausted defer budget the same
                # way (issue 3992).
                if op.current_defer_count >= DEPENDENCY_DEFER_BUDGET:
                    op.add_event(
                        EVENT_TYPE_AUDIT,
                        'warning, gave up waiting for runs_after dependency',
                        extra={
                            'dep_object_type': dep_op.object_type,
                            'dep_object_uuid': str(dep_op.uuid),
                            'dep_object_state': dep_op_state,
                            'defer_count': op.current_defer_count
                        })
                    continue

                # Dependency not yet ready, we should defer
                self._apply_defer(op, waiting_on=[dep_op], defer_delays=defer_delays)
                return

        # We're good to go! All dependencies are met, so we no longer need
        # the back-off entry for this op — drop it so any later defer (e.g.
        # this op chained onto a different dep) starts back at the initial
        # delay rather than carrying over the previous chain's depth.
        self._drop_defer_entry(str(op.uuid), defer_delays)
        if state.value == BaseClusterOperation.STATE_EXECUTING:
            # Its state row still says "coalesced into <survivor>", and
            # execute() cannot refresh that because an unchanged state is
            # never rewritten. Say here that this run is the op's own.
            op.add_event(
                EVENT_TYPE_AUDIT,
                'running an op which was already executing, because the '
                'survivor which folded it, or an earlier run, did not '
                'complete it',
                extra={'previous_state_message': state.message})
        start_time = time.time()
        op.execute()
        # The op may have transitioned to a terminal state during execute();
        # drop the entry again in case it was somehow re-populated.
        self._drop_defer_entry(str(op.uuid), defer_delays)
        # One end-of-op event carries the queue-wait time, the execution
        # duration and the coalescing instrumentation. The payload is
        # built on the operation so that this dispatcher and the one in
        # ``shakenfist/daemons/queues/workitem.py`` cannot drift apart
        # on field names -- tools/queue-wait-report.py reads one stream
        # carrying events from both.
        op.add_event(
            EVENT_TYPE_USAGE, 'execution duration',
            extra=op.execution_duration_extra(start_time, queue_name))
