import collections
import queue
import time
from typing import NamedTuple
from typing import Optional

from prometheus_client import Counter
from prometheus_client import Gauge
from prometheus_client import Histogram
from shakenfist_utilities import logs                 # noreorder

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FINAL_OBJECT_STATES
from shakenfist.constants import get_object_class
from shakenfist.constants import NoSuchObject
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.exceptions import HardDeleteBatchUnavailable
from shakenfist.exceptions import InvalidStateException
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist import eventlog
from shakenfist import mariadb
from shakenfist.schema.operations import baseclusteroperation as bco_schema
from shakenfist.schema.operations import node_blob_op as nbo_schema
from shakenfist.schema.operations import node_inst_op as nio_schema
from shakenfist.instance import Instance
from shakenfist.namespace import Namespaces
from shakenfist.namespace_key import keys_with_attributes
from shakenfist.namespace_key import NamespaceKey
from shakenfist.node import Node
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_general_background_node_queues
from shakenfist.operations.baseoperation import get_general_user_facing_node_queues
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)
BLOB_CHECKS_QUEUE = queue.Queue()
INSTANCE_CHECKS_QUEUE = queue.Queue()

# The deleted-object sweep's work, held per object type. A single queue
# filled type by type let one large backlog take every pass's whole budget,
# so the types behind it were never reached at all. Instead the drain takes
# a turn of up to DELETED_OBJECT_BATCH_SIZE entries from each type that has
# work in turn, in the order of OBJECT_NAMES_TO_CLASSES, so a type with
# thirty objects gets the same share of the pass as one with hundreds of
# thousands. The share is counted in entries rather than time: a type whose
# turn is one cheap batch delete must not crowd out one whose objects are
# hard deleted one at a time.
#
# A pass stops at DELETED_OBJECT_SWEEP_MAX_OBJECTS entries or at its time
# budget, whichever comes first. With batches the time budget alone would
# let a pass hard delete thousands of objects, which is a write load on the
# database tier that should be chosen rather than fallen into, so the entry
# cap is what sets the drain rate and the time budget is a ceiling.
#
# Each type holds at most one page of uuids, read in primary key order: an
# ORDER BY on anything else would sort the type's whole final-state range
# for every page, which is the unbounded read paging exists to avoid. The
# keyset cursor is where the next page of that type's walk starts, and ''
# starts a new walk, which is how objects that arrived behind the cursor are
# reached. A type is read again only when its own work has run out, and a
# type whose walk has completed -- including one whose read came back empty
# -- is not walked again until its recheck time. That keeps an idle cluster
# to a handful of small reads every few minutes, and means an object whose
# hard delete fails every time is retried once per recheck rather than
# once per pass.
#
# _DELETED_OBJECT_NEXT_TYPE is the drain's position in the rotation, an
# index into list(OBJECT_NAMES_TO_CLASSES). It persists across passes: if
# every pass started from the top, a budget that ran out part way through a
# round would favour the types at the head of the order every time, which
# is the same starvation in a milder form.
#
# None of this is cleared on demotion, for the same reason as
# _SWEEP_RESUME_AFTER below: it is work and a position, not a published
# value. A demoted node keeps its entries and would carry on with them if it
# were re-elected, and each one is re-read and re-checked before it is acted
# on, so a stale entry costs a read rather than a wrong delete.
DELETED_OBJECT_PAGE_SIZE = 500
DELETED_OBJECT_WALK_RECHECK = 300
_DELETED_OBJECT_WORK: dict[str, collections.deque[str]] = {}
_DELETED_OBJECT_CURSOR: dict[str, str] = {}
_DELETED_OBJECT_RECHECK_AT: dict[str, float] = {}
_DELETED_OBJECT_NEXT_TYPE: int = 0
# 'skipped' is an entry of a batch which the batch's own eligibility check
# left alone: it was gone, no longer in a final state, or too young, and a
# batch cannot say which. 'gone', 'not_final' and 'too_young' are counted
# only for an object hard deleted on its own; 'hard_deleted' and 'failed'
# either way.
DELETED_OBJECT_OUTCOMES = (
    'gone', 'not_final', 'too_young', 'skipped', 'hard_deleted', 'failed')

# What the deleted-object sweep costs and how far behind it is (#4473).
# The entry histogram is per dequeued entry, and an entry of a batch is
# observed with an equal share of the batch's wall time, so its counts and
# sums mean the same whichever way a type is hard deleted. The batch
# histogram observes each batch call, which with the entry counts gives a
# batch's latency against its size. The RPCs are timed separately by
# mariadb.rpc_timing_scope(), so the difference between those and these is
# time spent outside the database. All are leader-only, like the streak
# gauge below.
CLUSTER_DELETED_OBJECT_SECONDS = Histogram(
    'cluster_deleted_object_seconds',
    'Wall time spent on one entry of the per-deleted-object queue, by '
    'object type and how the entry ended. An entry hard deleted in a batch '
    'is observed with an equal share of the batch\'s wall time.',
    ['object_type', 'outcome'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
CLUSTER_DELETED_OBJECT_BATCH_SECONDS = Histogram(
    'cluster_deleted_object_batch_seconds',
    'Wall time of one batch hard delete call by the deleted-object sweep, '
    'by object type.', ['object_type'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
CLUSTER_DELETED_OBJECT_BACKLOG = Gauge(
    'cluster_deleted_object_backlog',
    'Objects in a final state, by object type. This is a count with no age '
    'filter, so it includes objects still inside their grace period.',
    ['object_type'])

REAPER_REQUEUED = Counter(
    'cluster_op_reaper_requeued_total',
    'Stuck cluster operation work items that were re-queued.')
REAPER_REJECTED = Counter(
    'cluster_op_reaper_rejected_total',
    'Stuck cluster operation work items that exceeded '
    'max_attempts and were rejected.')
REAPER_ORPHANED = Counter(
    'cluster_op_reaper_orphaned_total',
    'Cluster operations errored because no work_queue row '
    'referenced them.')

# Every sweep in this module shares a failure mode: silence. A pass
# that cannot read its work list does nothing, and nothing else ever
# retries on its behalf (#3638). Track consecutive failed passes per
# sweep and object type so operators can alert on the streak.
SWEEP_WORK_LIST_FAILURE_STREAK = Gauge(
    'cluster_sweep_work_list_failure_streak',
    'Consecutive scheduled-task passes unable to read their work list, '
    'by sweep and object type.', ['sweep', 'object_type'])
_SWEEP_FAILURE_STREAK: dict[tuple[str, str], int] = {}

# Where each per-object-type sweep should begin its next pass. The
# sweeps that read one work list per object type stop the pass on a
# tier-wide failure, which bounds the pass to a single _grpc_call retry
# budget (see _fill_deleted_object_work). Stopping at the same
# place every pass would starve every type after it, though:
# DatabaseUnavailable is raised for an exhausted DEADLINE_EXCEEDED
# budget too, which one large, slow or lock-contended query can produce
# for a single object type on an otherwise healthy tier -- and the
# backlog that then accumulates makes that type's query slower still.
# That is the self-sustaining ratchet which made #3638 unrecoverable,
# one level up. Resuming after the type that stopped the last pass
# bounds the pass and still reaches every type within a bounded number
# of passes, whichever shape the failure takes.
#
# This is deliberately not cleared on demotion the way the streak
# metrics are: it is a starting offset, not a published value, and a
# re-elected node is better off carrying on than starting from the top
# again.
_SWEEP_RESUME_AFTER: dict[str, str] = {}


def _sweep_object_types(sweep: str, object_types: list[str]) -> list[str]:
    """Order a pass's object types, resuming after the last early stop."""
    resume = _SWEEP_RESUME_AFTER.get(sweep)
    if resume not in object_types:
        return list(object_types)
    start = object_types.index(resume)
    return object_types[start:] + object_types[:start]


def _record_sweep_stopped(sweep: str, object_types: list[str],
                          stopped_on: str) -> None:
    """Remember to begin the next pass after the type that stopped this one."""
    nxt = (object_types.index(stopped_on) + 1) % len(object_types)
    _SWEEP_RESUME_AFTER[sweep] = object_types[nxt]


def _record_sweep_completed(sweep: str) -> None:
    """A pass that reached every type has no backlog to resume from."""
    _SWEEP_RESUME_AFTER.pop(sweep, None)


class WorkList(NamedTuple):
    """The outcome of a sweep's work-list read.

    ``uuids`` is None when the read failed, never [] -- a sweep must be
    able to tell "the read failed" from "there was nothing to do".

    ``tier_unavailable`` says which of the two shapes of failed read
    happened, because a caller reading one work list per object type
    must treat them differently. A None return from the gRPC wrapper is
    per-reply (the RESOURCE_EXHAUSTED oversized-reply case of #3638
    applies to one type's reply, and the other types are still
    readable), so that caller should move on to the next type. A
    ``DatabaseUnavailable`` is tier-wide, and asking the same dead tier
    28 more times buys nothing while costing a full ``_grpc_call``
    retry budget each time -- see ``_fill_deleted_object_work``.
    """
    uuids: Optional[list[str]]
    tier_unavailable: bool


def _record_sweep_read(sweep: str, object_type: ObjectType,
                       uuids: Optional[list[str]],
                       error: Optional[str]) -> None:
    """Account one work-list read against the failure streak gauge.

    Every sweep in this module shares a failure mode: silence. A pass
    that cannot read its work list does nothing, and nothing else ever
    retries on its behalf (#3638), so the streak is the only signal an
    operator gets. A successful read clears the streak for that label
    set; a failed one increments it and says so in the log.
    """
    key = (sweep, str(object_type))

    if uuids is None:
        streak = _SWEEP_FAILURE_STREAK.get(key, 0) + 1
        _SWEEP_FAILURE_STREAK[key] = streak
        SWEEP_WORK_LIST_FAILURE_STREAK.labels(
            sweep=sweep, object_type=str(object_type)).set(streak)
        LOG.with_fields({
            'sweep': sweep,
            'object_type': str(object_type),
            'consecutive_failures': streak,
            'error': error}).warning(
            'Scheduled sweep could not read its work list; this pass is '
            'skipped and its backlog is not being drained')
        return

    if _SWEEP_FAILURE_STREAK.pop(key, None):
        SWEEP_WORK_LIST_FAILURE_STREAK.labels(
            sweep=sweep, object_type=str(object_type)).set(0)


def _sweep_work_list(sweep: str, object_type: ObjectType,
                     state_values: list[str],
                     updated_before: Optional[float] = None
                     ) -> WorkList:
    """Read a sweep's work list, treating a failed read as visible.

    ``mariadb.get_objects_by_state`` returns None when the read failed,
    which is distinct from [] for "no matches". Collapsing the two with
    ``or []`` turns the sweep off silently: it reports a successful pass
    over an empty queue while the backlog it exists to drain keeps
    growing, and grows the very reply that could not be read (#3638).

    A failed read arrives in two shapes. The gRPC wrapper returns None
    when the call itself failed -- the RESOURCE_EXHAUSTED
    oversized-reply case of #3638 -- but a database tier that is down or
    mid rolling-restart exhausts the retry budget in ``_grpc_call``
    instead, which raises ``DatabaseUnavailable``. That exception is
    deliberately not a ``grpc.RpcError`` (issue 3373), so it propagates
    straight through the wrapper; catching it here is what keeps the
    streak gauge truthful during precisely the outage it exists to make
    visible, and what keeps one object type's failure from unwinding the
    deleted-object sweep for every type after it. The two shapes are
    reported separately in the return value -- see ``WorkList``.
    """
    try:
        obj_uuids = mariadb.get_objects_by_state(
            object_type, state_values, updated_before=updated_before)
        error = None
        tier_unavailable = False
    except DatabaseUnavailable as e:
        obj_uuids = None
        error = str(e)
        tier_unavailable = True

    _record_sweep_read(sweep, object_type, obj_uuids, error)
    return WorkList(uuids=obj_uuids, tier_unavailable=tier_unavailable)


class WorkPage(NamedTuple):
    """The outcome of reading one page of a sweep's work list.

    ``uuids`` and ``tier_unavailable`` mean exactly what they do in
    ``WorkList``. ``more_available`` is True only when the walk stopped
    early, so the next read should start after the last uuid; it is False
    for a failed read.
    """
    uuids: Optional[list[str]]
    more_available: bool
    tier_unavailable: bool


def _sweep_work_page(sweep: str, object_type: ObjectType,
                     state_values: list[str], page_size: int,
                     after_uuid: str,
                     updated_before: Optional[float] = None
                     ) -> WorkPage:
    """Read one page of a sweep's work list, treating a failed read as visible.

    This is ``_sweep_work_list()`` for a keyset walk, and handles the two
    shapes of failed read in the same way for the same reasons: a None
    from the gRPC wrapper is per-reply, a raised ``DatabaseUnavailable``
    is tier-wide, and both are accounted against the failure streak.
    """
    try:
        page = mariadb.get_objects_by_state_page(
            object_type, state_values, page_size, after_uuid=after_uuid,
            updated_before=updated_before)
        error = None
        tier_unavailable = False
    except DatabaseUnavailable as e:
        page = None
        error = str(e)
        tier_unavailable = True

    obj_uuids = page.uuids if page is not None else None
    _record_sweep_read(sweep, object_type, obj_uuids, error)
    return WorkPage(
        uuids=obj_uuids,
        more_available=page.more_available if page is not None else False,
        tier_unavailable=tier_unavailable)


def _stateless_work_list(sweep: str, object_type: ObjectType) -> WorkList:
    """Read the zombie-repair work list, with the same visibility.

    ``mariadb.get_stateless_object_uuids`` has exactly the shape of
    ``get_objects_by_state``: None for a failed read, [] for no
    orphans, and a raised ``DatabaseUnavailable`` when the tier is gone.
    Routing it through the same accounting means a reconcile pass that
    silently repairs nothing is as visible as a sweep that silently
    collects nothing.
    """
    try:
        obj_uuids = mariadb.get_stateless_object_uuids(object_type)
        error = None
        tier_unavailable = False
    except DatabaseUnavailable as e:
        obj_uuids = None
        error = str(e)
        tier_unavailable = True

    _record_sweep_read(sweep, object_type, obj_uuids, error)
    return WorkList(uuids=obj_uuids, tier_unavailable=tier_unavailable)


def clear_sweep_failure_metrics() -> None:
    """Drop the sweep failure streaks when this node stops being the leader.

    Only the elected cluster maintainer runs these sweeps, so a node
    that failed a read and then lost the lock would otherwise keep
    exporting a non-zero streak forever -- nothing on that node will
    ever run the reset branch again, and any "streak > 0 for N minutes"
    alert would fire indefinitely against a node that is not sweeping.
    The newly elected node repopulates on its first failing pass. This
    is the same reasoning as ``clear_scheduler_capacity_metrics()``.

    Every label set on this gauge describes leader-only work, so the
    whole metric goes rather than just the label sets currently at a
    non-zero streak -- a demoted node should stop answering for these
    sweeps entirely, not answer zero for them.
    """
    SWEEP_WORK_LIST_FAILURE_STREAK.clear()
    _SWEEP_FAILURE_STREAK.clear()


def clear_deleted_object_metrics() -> None:
    """Drop the deleted-object backlog gauge when this node is demoted.

    Only the elected cluster maintainer publishes the backlog, so a node
    that held the lock and then lost it would otherwise keep exporting the
    counts it last saw forever -- nothing on that node will ever run the
    publishing code again. The newly elected node repopulates on its first
    pass. This is the same reasoning as ``clear_sweep_failure_metrics()``.

    The sweep's work is not touched: it is not a published value, and a
    re-elected node carries on with it.
    """
    CLUSTER_DELETED_OBJECT_BACKLOG.clear()


# Scheduler capacity reconciler metrics. Per-node gauges are labelled by node
# uuid and resource dimension; the resource label values are 'cpus',
# 'memory_mb' and 'disk_gb'.
CAPACITY_RESOURCES = ('cpus', 'memory_mb', 'disk_gb')
SCHEDULER_CAPACITY_NODE_LIMIT = Gauge(
    'scheduler_capacity_node_limit',
    'Per-node scheduler capacity limit, by resource dimension.',
    ['node', 'resource'])
SCHEDULER_CAPACITY_NODE_USED = Gauge(
    'scheduler_capacity_node_used',
    'Per-node scheduler capacity usage, by resource dimension.',
    ['node', 'resource'])
SCHEDULER_CAPACITY_NODE_EXPECTED_DEMAND = Gauge(
    'scheduler_capacity_node_expected_demand',
    'Per-node decayed expected demand from recent placements.',
    ['node'])
SCHEDULER_CAPACITY_CLUSTER_TOTAL = Gauge(
    'scheduler_capacity_cluster_total',
    'Cluster-wide scheduler capacity (sum of node limits), by resource '
    'dimension.',
    ['resource'])
SCHEDULER_CAPACITY_CLUSTER_CLAIMED = Gauge(
    'scheduler_capacity_cluster_claimed',
    'Cluster-wide capacity reserved by active namespace claims, by '
    'resource dimension.',
    ['resource'])
SCHEDULER_CAPACITY_CLUSTER_UNCLAIMED_USED = Gauge(
    'scheduler_capacity_cluster_unclaimed_used',
    'Cluster-wide usage by namespaces without an active claim, by '
    'resource dimension.',
    ['resource'])
SCHEDULER_CAPACITY_PASSES = Counter(
    'scheduler_capacity_reconcile_passes_total',
    'Scheduler capacity reconcile passes attempted.')
SCHEDULER_CAPACITY_FAILURES = Counter(
    'scheduler_capacity_reconcile_failures_total',
    'Scheduler capacity reconcile passes that failed.')
# Incremented by the elected cluster daemon, not by the pass itself: it
# counts how often the maintenance loop found placement unguarded and
# pulled the reconcile forward off its cadence. A cluster which keeps
# forcing is one where this check and the reconciler disagree about
# which nodes should have a row, which is otherwise only visible by
# grepping logs.
SCHEDULER_CAPACITY_FORCED = Counter(
    'scheduler_capacity_reconcile_forced_total',
    'Scheduler capacity reconcile passes forced because one or more nodes '
    'were admitting placements unguarded.')
SCHEDULER_CAPACITY_LAST_SUCCESS = Gauge(
    'scheduler_capacity_reconcile_last_success_timestamp',
    'Unix timestamp of the last successful scheduler capacity reconcile '
    'pass.')
SCHEDULER_CAPACITY_LAST_DURATION = Gauge(
    'scheduler_capacity_reconcile_last_duration_seconds',
    'Duration of the last scheduler capacity reconcile pass in seconds.')

# The node label sets we most recently exported, so a node deleted from
# the cluster has its per-node gauges removed rather than lingering at
# their final values forever.
_CAPACITY_EXPORTED_NODES: set[str] = set()


@util_general.recorded_method
def per_blob_checks():
    start_time = time.time()
    if BLOB_CHECKS_QUEUE.empty():
        _fill_per_blob_queue()
        LOG.info(
            f'Refreshed per-blob queue with {BLOB_CHECKS_QUEUE.qsize()} items')

    queue_fill_cost = time.time() - start_time
    if queue_fill_cost > 10:
        return

    processed = _process_per_blob_queue(execution_limit=(10 - queue_fill_cost))
    LOG.info(f'Processed {processed} items from per-blob queue')


def _fill_per_blob_queue():
    work = _sweep_work_list(
        'per_blob', ObjectType.BLOB, [Blob.STATE_CREATED])
    if work.uuids is None:
        return
    for blob_uuid in work.uuids:
        b = Blob.from_db(blob_uuid)
        if not b:
            continue
        BLOB_CHECKS_QUEUE.put(b)


def _process_per_blob_queue(execution_limit=10):
    processed = 0
    start_time = time.time()
    while True:
        # Limit how long we spend in this loop
        if time.time() - start_time > execution_limit:
            return processed

        try:
            b = BLOB_CHECKS_QUEUE.get(block=False)
        except queue.Empty:
            return processed

        processed += 1

        if b.ref_count == 0:
            # Grace period prevents racing freshly-registered blobs whose
            # owning reference (e.g. an artifact_index) has not yet been
            # written. snapshot_disk() registers the blob before the
            # snapshot operation calls Artifact.add_index, so the per-blob
            # queue can pick the blob up in that window. Mirror the
            # cluster_wide_cleanup grace period in main.py.
            last_used = b.last_used or b.fetched_at
            age = time.time() - last_used
            if age <= 300:
                continue
            b.add_event(
                EVENT_TYPE_AUDIT,
                'deleting blob with reference count of 0',
                extra={'last_used': last_used, 'age': age})
            b.state = Blob.STATE_DELETED
            continue

        if b.state.value != Blob.STATE_CREATED:
            continue

        # Every blob location should have a checksum performed at least every
        # config.CHECKSUM_VERIFICATION_FREQUENCY seconds.
        node_fqdns = b.locations

        # Get existing hash verification timestamps from MariaDB
        # We check sha512 since it's always computed
        blob_hashes = mariadb.get_blob_hashes(str(b.uuid))
        last_verified_by_node: dict[str, float] = {}
        for h in blob_hashes:
            if h.algorithm == 'sha512' and h.verification_status == 'valid':
                if (h.node not in last_verified_by_node or
                        h.last_verified_at > last_verified_by_node[h.node]):
                    last_verified_by_node[h.node] = h.last_verified_at

        # Find nodes that already have pending blob operations for this
        # blob, so we don't schedule duplicate checksum requests. The
        # lookup is best-effort: an op can be hard-deleted between when
        # we read the target row and when we read the op row, so the
        # caller suppresses the "non-existent object" audit event —
        # treating the absent op as a non-pending one is the correct
        # behaviour.
        nodes_with_pending_ops = set()
        pending_ops = mariadb.get_cluster_operation_targets_for_object(
            ObjectType.BLOB, str(b.uuid))
        for target in pending_ops:
            op = get_object_class(target.operation_type).from_db(
                target.operation_uuid, suppress_failure_audit=True)
            if op and op.is_outstanding():
                # Resolve node UUID to FQDN for comparison with locations
                n = Node.from_db(op.node_uuid)
                if n:
                    nodes_with_pending_ops.add(n.fqdn)

        for node_fqdn in node_fqdns:
            # Check when this specific node last verified the blob
            last_checksum = last_verified_by_node.get(node_fqdn, 0)
            age = time.time() - last_checksum

            if age < config.CHECKSUM_VERIFICATION_FREQUENCY:
                continue

            if node_fqdn not in nodes_with_pending_ops:
                # Blob locations are FQDNs (from BLOB_LOCATION refs),
                # but create_and_enqueue requires a node UUID.
                node_obj = Node.from_db(node_fqdn)
                if not node_obj:
                    continue
                nbo_schema.create_and_enqueue(
                    str(node_obj.uuid), b.uuid,
                    [nbo_schema.model_tasks.verify_size_and_checksum],
                    bco_schema.PRIORITY.background_high_io)


@util_general.recorded_method
def per_instance_checks_and_usage():
    start_time = time.time()
    if INSTANCE_CHECKS_QUEUE.empty():
        _fill_per_instance_queue()
        LOG.info(
            f'Refreshed per-blob queue with {INSTANCE_CHECKS_QUEUE.qsize()} '
            'items')

    queue_fill_cost = time.time() - start_time
    if queue_fill_cost > 10:
        return

    processed = _process_per_instance_queue(
        execution_limit=(10 - queue_fill_cost))
    LOG.info(f'Processed {processed} items from per-instance queue')


def _fill_per_instance_queue():
    work = _sweep_work_list(
        'per_instance', ObjectType.INSTANCE, [Instance.STATE_CREATED])
    if work.uuids is None:
        return
    for instance_uuid in work.uuids:
        inst = Instance.from_db(instance_uuid, suppress_failure_audit=True)
        if not inst:
            continue
        INSTANCE_CHECKS_QUEUE.put(inst)


def _process_per_instance_queue(execution_limit=10):
    processed = 0
    start_time = time.time()
    while True:
        # Limit how long we spend in this loop
        if time.time() - start_time > execution_limit:
            return processed

        try:
            inst = INSTANCE_CHECKS_QUEUE.get(block=False)
        except queue.Empty:
            return processed

        processed += 1

        if inst.state.value != Instance.STATE_CREATED:
            continue

        placement = inst.placement
        node = None
        if placement and placement.get('node'):
            node = Node.from_db(placement['node'])

        if not node:
            inst.add_event(
                EVENT_TYPE_AUDIT,
                ('instance in created state with no node placement, '
                 'transitioning to error state'))
            inst.state = Instance.STATE_ERROR
            continue

        nio_schema.create_and_enqueue(
            str(node.uuid),
            inst.uuid,
            [
                nio_schema.model_tasks.collect_billing_statistics,
                nio_schema.model_tasks.health_check_kvm_process
            ],
            bco_schema.PRIORITY.user_facing
        )


def _log_and_update_metrics_for_queue(queue, log_prefix):
    processing, queued, deferred = mariadb.get_work_queue_length(queue)
    LOG.with_fields({
        'processing': processing,
        'queued': queued,
        'deferred': deferred,
        'queue': queue
    }).debug(f'{log_prefix} queue length')


def log_cluster_queue_lengths():
    for queuename in get_general_user_facing_node_queues():
        _log_and_update_metrics_for_queue(queuename, 'Cluster user facing')

    for queuename in get_general_background_node_queues():
        _log_and_update_metrics_for_queue(queuename, 'Cluster background')


@util_general.recorded_method
def reap_stuck_cluster_operation_jobs():
    """Re-queue or reject work_queue rows whose claim has gone stale.

    Runs only on the elected cluster node via the scheduled task
    loop in daemons/cluster/main.py. For every row whose
    claimed_at is older than CLUSTER_OP_STUCK_THRESHOLD:

    - If attempts < CLUSTER_OP_MAX_ATTEMPTS, clear the claim so a
      fresh worker re-picks up the job. attempts is left in place
      so the next failure still counts toward the ceiling.
    - Otherwise delete the row, flip the cluster operation to
      STATE_ERROR, and log an audit event. This is the 'job of
      death' guard.

    A second pass errors out cluster operations sitting in a
    non-terminal state for longer than the same threshold with no
    work_queue row referencing them at all (issue 4303). Nothing
    can ever run such an operation, and before the enqueue-side
    dedup learned to skip them it adopted them forever.

    Races with another freshly-elected cluster daemon are
    harmless: the row-level delete / update wins exactly once,
    and the loser's helper returns False so the loser skips the
    state transition.
    """
    threshold = config.CLUSTER_OP_STUCK_THRESHOLD
    max_attempts = config.CLUSTER_OP_MAX_ATTEMPTS

    stuck = mariadb.list_stuck_work_queue_rows(threshold)
    if stuck:
        LOG.info(
            f'Reaper found {len(stuck)} stuck work queue rows '
            f'(threshold={threshold}s, max_attempts={max_attempts})')

    for row in stuck:
        row_id = row['id']
        queue_name = row['queue_name']
        payload = row.get('payload') or {}
        op_type = payload.get('operation_type')
        op_uuid = payload.get('operation_uuid')
        attempts = row['attempts']

        if attempts >= max_attempts:
            if not mariadb.delete_work_queue_row(row_id):
                continue
            REAPER_REJECTED.inc()
            LOG.with_fields({
                'row_id': row_id,
                'queue_name': queue_name,
                'attempts': attempts,
                'claimed_by': row.get('claimed_by'),
                'claimed_at': row.get('claimed_at'),
                'operation_type': op_type,
                'operation_uuid': op_uuid,
            }).warning(
                'Reaper rejected stuck work item: '
                'exceeded max_attempts')

            if not op_type or not op_uuid:
                continue
            try:
                cls = get_object_class(op_type)
            except KeyError:
                continue
            op = cls.from_db(op_uuid)
            if op is None:
                continue
            try:
                op.state = BaseClusterOperation.STATE_ERROR
                op.add_event(
                    EVENT_TYPE_AUDIT,
                    'rejected by reaper: exceeded '
                    f'{max_attempts} claim attempts',
                    extra={
                        'queue_name': queue_name,
                        'attempts': attempts,
                        'claimed_by': row.get('claimed_by'),
                        'claimed_at': row.get('claimed_at'),
                    })
            except InvalidStateException as e:
                LOG.with_fields({
                    'operation_type': op_type,
                    'operation_uuid': op_uuid,
                }).warning(
                    f'Reaper cannot transition op to error: {e}')
            continue

        if mariadb.clear_work_queue_claim(row_id):
            REAPER_REQUEUED.inc()
            LOG.with_fields({
                'row_id': row_id,
                'queue_name': queue_name,
                'attempts': attempts,
                'claimed_by': row.get('claimed_by'),
                'claimed_at': row.get('claimed_at'),
            }).info('Reaper re-queued stuck work item')

    # Orphan pass: operations in queued or executing with no work_queue
    # row at all. The stuck-row walk above cannot see these -- there is
    # no row to walk. They cannot make progress, so error them out.
    orphans = mariadb.list_orphaned_cluster_operations(threshold)
    if orphans:
        LOG.info(
            f'Reaper found {len(orphans)} cluster operations with no '
            f'work queue row (threshold={threshold}s)')

    for orphan in orphans:
        op_type = orphan['operation_type']
        op_uuid = orphan['uuid']
        try:
            cls = get_object_class(op_type)
        except NoSuchObject:
            continue
        op = cls.from_db(op_uuid)
        if op is None:
            continue
        try:
            op.state = BaseClusterOperation.STATE_ERROR
            op.add_event(
                EVENT_TYPE_AUDIT,
                'rejected by reaper: no work queue row references '
                'this operation',
                extra={
                    'orphaned_state': orphan['state_value'],
                    'state_update_time': orphan['update_time'],
                })
            REAPER_ORPHANED.inc()
            LOG.with_fields({
                'operation_type': op_type,
                'operation_uuid': op_uuid,
                'orphaned_state': orphan['state_value'],
            }).warning('Reaper errored orphaned cluster operation')
        except InvalidStateException as e:
            # Already terminal -- for example a dispatcher's fold
            # marked it complete between our read and this write.
            LOG.with_fields({
                'operation_type': op_type,
                'operation_uuid': op_uuid,
            }).warning(
                f'Reaper cannot transition orphaned op to error: {e}')


def reap_expired_namespace_keys() -> None:
    """Soft delete namespace keys which expired a while ago.

    Expiry enforcement is check-at-use -- an expired key stops
    authenticating the moment it lapses, without this sweep having run.
    All this does is tidy up, after a grace period during which the
    lapsed key is still listable so an operator can see why their
    automation stopped working.

    The soft delete is all we do here. Hard deletion is
    per_deleted_object_checks' job, which hard deletes any object which
    has sat in a final state for config.CLEANER_DELAY, keys included.
    """
    grace = config.NAMESPACE_KEY_REAP_GRACE
    if grace <= 0:
        return

    cutoff = time.time() - grace
    reaped = 0

    # Which keys can this sweep actually act on? Ask once, in SQL,
    # rather than reading .state per expired key.
    #
    # keys_with_attributes(include_expired=True) deliberately does not
    # filter on object state (see its docstring), so it returns every
    # key this sweep has ever soft deleted -- they stay expired, and
    # stay listed, until the hard delete reaper gets to them -- plus
    # every stateless zombie, which can never be soft deleted at all.
    # Reading .state per key to discard those was ~15,200 uncached
    # GetObjectState round trips per pass on sfcbr, issued in one
    # fifteen minute burst, and it grew with the backlog rather than
    # with the work: 0 to 19 QPS over eleven days (issue 3814).
    #
    # A key not in this set is either already soft deleted or has no
    # state row. Neither is actionable here, and neither needs to be
    # told apart one round trip at a time: stateless keys are
    # reconcile_orphaned_objects' job, and it counts them hourly.
    actionable = mariadb.get_objects_by_state(
        ObjectType.NAMESPACE_KEY, sorted(NamespaceKey.ACTIVE_STATES))
    if actionable is None:
        # Distinct from an empty list, which means "no active keys".
        LOG.warning(
            'Could not list active namespace keys, skipping expiry sweep')
        return
    actionable = set(actionable)

    for ns in Namespaces(filters=[], prefilter='active'):
        # include_expired is the whole point of the sweep, and the
        # accessor joins the attributes row, so expiry costs no extra
        # read here.
        for key, attrs in keys_with_attributes(ns.uuid, include_expired=True):
            if attrs.expiry is None or attrs.expiry >= cutoff:
                continue

            if str(key.uuid) not in actionable:
                continue

            try:
                key.delete()
            except InvalidStateException as e:
                # Raced with something else deleting the key. Not an
                # error, the key is going away either way.
                LOG.with_fields({
                    'namespace': key.namespace,
                    'key': key.name
                }).info(f'Expired namespace key already transitioning: {e}')
                continue

            # The audit event records only a successful delete. Eventing
            # before the attempt meant a key whose delete always failed
            # was re-evented every fifteen minutes forever -- 4,151
            # zombie keys generated ~380k junk audit events/day, two
            # thirds of the cluster's entire event volume (issue 3588).
            key.add_event(
                EVENT_TYPE_AUDIT,
                'the cluster wide cleanup daemon is deleting this '
                'namespace key because it expired',
                extra={'expiry': attrs.expiry})
            reaped += 1

    if reaped:
        LOG.info(f'Soft deleted {reaped} expired namespace keys')


# How long after a token's own expiry its replay record is kept.
#
# A replay record only has to outlive the token it describes: once the
# token is expired the exchange refuses it during validation and never
# reaches the replay check. The hour of margin covers clock skew
# between the node running this sweep and whichever node verifies a
# token. A node running behind would otherwise still accept a token
# whose replay record a node running ahead had already deleted, which
# is exactly the replay this table exists to refuse. At the volume this
# endpoint sees, an hour of stale rows costs nothing.
REPLAY_REAP_GRACE = 3600

# How long a closed rate limiting window is kept. Only the current
# window is ever counted against, so anything older is dead weight.
# Two windows of margin keeps the sweep from racing a request which is
# mid-flight across a boundary.
RATE_LIMIT_REAP_GRACE = 120


def reap_federation_records() -> None:
    """Delete spent federated exchange replay and rate limit rows.

    Both tables are write-mostly and neither row is read once it has
    gone stale, so without this they would grow for the life of the
    cluster. Nothing depends on the sweep having run: it removes rows
    that no longer affect any decision.
    """
    replayed = mariadb.reap_federation_replay(
        time.time() - REPLAY_REAP_GRACE)
    if replayed:
        LOG.info(f'Removed {replayed} expired federation replay records')

    windows = mariadb.reap_federation_rate_limits(
        int(time.time()) - RATE_LIMIT_REAP_GRACE)
    if windows:
        LOG.info(f'Removed {windows} closed federation rate limit windows')


def prune_events() -> None:
    """Daily prune sweep of the events / event_objects tables.

    Runs on the elected cluster maintainer; the cluster lock makes
    sure only one node calls this per day. The actual prune work
    runs inside the sf-database direct path; this function just
    triggers it and logs the result.

    mariadb.prune_events() raises on failure (issue 3849), so the
    success line below always means the sweep actually ran. A failure
    is swallowed with a warning so a transient database problem does
    not kill the maintainer; there is no sooner retry than the next
    daily fire (retry cadence and pause-after-repeated-failure
    semantics belong to PLAN-recurring-operations).
    """
    try:
        rows = mariadb.prune_events()
        LOG.info(f'Events prune sweep removed {rows} rows.')
    except Exception as e:
        LOG.warning(
            'Events prune sweep failed and will not retry until the '
            f'next daily sweep: {e}')


def _deleted_object_delay(objtype):
    if objtype.endswith('_op'):
        return 30
    return config.CLEANER_DELAY


def _deleted_object_work_held() -> int:
    """How many entries the deleted-object sweep holds, across every type."""
    return sum(len(work) for work in _DELETED_OBJECT_WORK.values())


@util_general.recorded_method
def per_deleted_object_checks():
    start_time = time.time()
    budget = config.DELETED_OBJECT_SWEEP_SECONDS

    held_before = _deleted_object_work_held()
    if not _fill_deleted_object_work(start_time, start_time + budget):
        # The first read of every entry would spend a whole _grpc_call
        # retry budget against a tier which has just failed one, and fail
        # anyway, so a pass whose fill could not reach the tier does not
        # drain. Whatever it did read waits for the next pass.
        LOG.warning(
            'Skipping per-deleted-object drain, database unavailable '
            'during the fill')
        return
    filled = _deleted_object_work_held() - held_before

    fill_cost = time.time() - start_time
    outcomes: collections.Counter[str] = collections.Counter()
    process_start = time.time()
    processed = _drain_deleted_object_work(budget - fill_cost, outcomes)
    process_seconds = time.time() - process_start

    fields: dict[str, object] = {
        'total': processed,
        'remaining': _deleted_object_work_held(),
        'filled': filled,
        'elapsed_seconds': round(process_seconds, 3)
    }
    for outcome in DELETED_OBJECT_OUTCOMES:
        fields[outcome] = outcomes[outcome]
    LOG.with_fields(fields).info(
        f'Processed {processed} items from per-deleted-object queue')


@util_general.recorded_method
def publish_deleted_object_backlog():
    """Set the backlog gauge from a count of final-state objects per type.

    Every type is published, zeros included, so an alert can rely on the
    series existing. A failed count removes the type's label set rather than
    leaving a value the failed read can no longer vouch for. A tier-wide
    failure stops the pass for the reason given in
    ``_fill_deleted_object_work()``.
    """
    object_types = list(OBJECT_NAMES_TO_CLASSES)
    for objtype in _sweep_object_types('deleted_object_backlog', object_types):
        try:
            with mariadb.rpc_timing_scope('deleted_object_backlog', objtype):
                count = mariadb.count_objects_by_state(
                    ObjectType(objtype), FINAL_OBJECT_STATES)
        except DatabaseUnavailable as e:
            LOG.with_fields({'object_type': objtype}).warning(
                f'Stopping deleted object backlog pass, database '
                f'unavailable: {e}')
            _record_sweep_stopped(
                'deleted_object_backlog', object_types, objtype)
            break

        if count is None:
            LOG.with_fields({'object_type': objtype}).warning(
                'Could not count deleted objects, dropping backlog gauge')
            try:
                CLUSTER_DELETED_OBJECT_BACKLOG.remove(objtype)
            except KeyError:
                # Never published, or cleared by a demotion.
                pass
            continue

        CLUSTER_DELETED_OBJECT_BACKLOG.labels(objtype).set(count)
    else:
        _record_sweep_completed('deleted_object_backlog')


def _fill_deleted_object_work(now: float, deadline: float) -> bool:
    """Read a page of work for each type whose own work has run out.

    No read is started after ``deadline``, a time.time() value; the types
    not reached are read first by the next pass. Returns False when the
    database tier was unavailable, in which case the caller must not drain.
    """
    # The work holds uuids, not hydrated objects. Hydrating here serialised
    # a gRPC round trip per object inside a single scheduled call, which
    # with a large backlog blew both the fill budget and the watchdog window
    # before any processing happened (issue 3533). The age filter is pushed
    # down to SQL so objects too young to hard delete are never fetched at
    # all.
    object_types = list(OBJECT_NAMES_TO_CLASSES)
    for objtype in _sweep_object_types('per_deleted_object', object_types):
        work = _DELETED_OBJECT_WORK.setdefault(objtype, collections.deque())
        if work:
            continue
        if _DELETED_OBJECT_RECHECK_AT.get(objtype, 0.0) > now:
            continue

        # Each read is cheap while MariaDB walks the primary key, but 29 of
        # them back to back are not if it ever chooses to sort instead, and
        # the pass is meant to fit its budget whichever it does. Resuming
        # at this type rather than starting from the top again means a run
        # of slow reads moves along the list rather than starving its tail.
        if time.time() >= deadline:
            previous = object_types[object_types.index(objtype) - 1]
            _record_sweep_stopped('per_deleted_object', object_types, previous)
            return True

        # A per-reply failure for one object type must not stop the sweep
        # for the rest: the other types are still collectable. This is the
        # site where collapsing None into [] silently turned garbage
        # collection off for node_inst_op while its uncollected backlog
        # made each subsequent reply larger still (#3638).
        #
        # A tier-wide failure is the opposite case and must stop the loop.
        # Each DatabaseUnavailable costs a whole _grpc_call retry budget
        # before it is raised, and that budget is split by cost, so what
        # one costs depends on which shape of failure it is:
        #
        #   UNAVAILABLE -- a dead tier, and the status the servicer now
        #   sets for its own transient MariaDB errors -- fails fast and
        #   does not rebuild the channel, so it spends
        #   GRPC_UNAVAILABLE_RETRIES (6) attempts and their escalating
        #   GRPC_RETRY_DELAY sleeps: on the order of ten seconds. This is
        #   the common shape.
        #
        #   DEADLINE_EXCEEDED -- a tier that is up but not answering --
        #   spends GRPC_RETRIES (3) deadlines of GRPC_TIMEOUT (30s) each
        #   plus sleeps, so about 93s. This is the shape the early return is
        #   sized for.
        #
        # There are 29 object types here and _run_due_scheduled_jobs()
        # pets the watchdog only between jobs, while sf-cluster runs at
        # WatchdogSec=300s. In the slow shape continuing past the fourth
        # failure already exceeds the window and a full pass would take
        # some 45 minutes; even the fast shape burns most of the window
        # learning nothing. Either way it risks SIGABRTing the elected
        # maintainer and costing a lock failover, where before it merely
        # skipped a pass. Nothing is gained by asking a tier that is not
        # there 28 more times. This function has blown the watchdog
        # window once already (issue 3533).
        with mariadb.rpc_timing_scope('per_deleted_object_fill', objtype):
            page = _sweep_work_page(
                'per_deleted_object', ObjectType(objtype), FINAL_OBJECT_STATES,
                DELETED_OBJECT_PAGE_SIZE, _DELETED_OBJECT_CURSOR.get(objtype, ''),
                updated_before=(now - _deleted_object_delay(objtype)))
        if page.uuids is None:
            if page.tier_unavailable:
                _record_sweep_stopped(
                    'per_deleted_object', object_types, objtype)
                return False
            continue

        work.extend(page.uuids)
        if page.more_available and page.uuids:
            _DELETED_OBJECT_CURSOR[objtype] = page.uuids[-1]
        else:
            # The walk is complete, so the next one starts from the top and
            # reaches whatever arrived behind the cursor -- but not until
            # the recheck, or every entry which failed in this walk would
            # be read and fail again in the very next pass.
            _DELETED_OBJECT_CURSOR[objtype] = ''
            _DELETED_OBJECT_RECHECK_AT[objtype] = (
                now + DELETED_OBJECT_WALK_RECHECK)

    _record_sweep_completed('per_deleted_object')
    return True


def _hard_delete_deleted_object(
        objtype: str, obj_uuid: str,
        outcomes: Optional[collections.Counter[str]] = None) -> None:
    """Hard delete one object, if it is still in a final state and old enough.

    If ``outcomes`` is given, the outcome of this entry -- one of
    DELETED_OBJECT_OUTCOMES -- is counted in it.
    """
    # Which way this entry ended, for the histogram and the pass
    # summary. 'too_young' is the implicit else of the age check.
    outcome = 'too_young'
    entry_start = time.monotonic()

    # A failure to hard delete one object must only cost that one
    # object, not the rest of the pass (and the other scheduled
    # tasks sharing the run_pending() invocation).
    try:
        with mariadb.rpc_timing_scope('per_deleted_object', objtype):
            # The object row may have been hard-deleted by a
            # concurrent cleaner between the state query and this
            # lookup; suppress the failure audit so that race does
            # not show up as an ERROR event in the logs.
            obj = get_object_class(objtype).from_db(
                obj_uuid, suppress_failure_audit=True)
            if not obj:
                outcome = 'gone'
                return

            # Work entries can be old by the time we get to them, so
            # re-check the state before acting on it.
            if obj.state.value not in FINAL_OBJECT_STATES:
                outcome = 'not_final'
                return

            if (time.time() - obj.state.update_time >
                    _deleted_object_delay(objtype)):
                obj.hard_delete()
                outcome = 'hard_deleted'
    except Exception as e:
        outcome = 'failed'
        util_exceptions.ignore_exception(
            f'hard delete of {objtype} {obj_uuid}', e)
    finally:
        CLUSTER_DELETED_OBJECT_SECONDS.labels(objtype, outcome).observe(
            time.monotonic() - entry_start)
        if outcomes is not None:
            outcomes[outcome] += 1


def _hard_delete_deleted_object_batch(
        objtype: str, obj_uuids: list[str],
        outcomes: Optional[collections.Counter[str]] = None) -> None:
    """Hard delete a batch of one type's objects still final and old enough.

    The eligibility check is made by the database in the same transaction as
    the delete, so nothing is read first. If ``outcomes`` is given, the
    outcome of each entry -- 'hard_deleted', 'skipped' or 'failed' -- is
    counted in it.

    Raises DatabaseUnavailable, HardDeleteBatchUnavailable and ValueError
    (a request refused whole, such as one naming a malformed uuid) having
    deleted nothing and counted nothing, for the caller to put the batch
    back or hard delete it another way.
    """
    batch_start = time.monotonic()
    try:
        with mariadb.rpc_timing_scope('per_deleted_object', objtype):
            deleted = mariadb.hard_delete_objects(
                ObjectType(objtype), obj_uuids,
                state_values=FINAL_OBJECT_STATES,
                updated_before=time.time() - _deleted_object_delay(objtype))
        CLUSTER_DELETED_OBJECT_BATCH_SECONDS.labels(objtype).observe(
            time.monotonic() - batch_start)
    except (DatabaseUnavailable, HardDeleteBatchUnavailable, ValueError):
        raise
    except Exception as e:
        deleted = None
        util_exceptions.ignore_exception(
            f'hard delete of {len(obj_uuids)} {objtype} objects', e)

    deleted_uuids = set(deleted or [])
    share = (time.monotonic() - batch_start) / len(obj_uuids)
    for obj_uuid in obj_uuids:
        if deleted is None:
            # The batch rolled back, so this entry is left for the next walk
            # of its type, after the recheck, to find again.
            outcome = 'failed'
        elif obj_uuid in deleted_uuids:
            outcome = 'hard_deleted'
            eventlog.add_event(
                EVENT_TYPE_AUDIT, objtype, obj_uuid, 'hard deleted object')
        else:
            # No longer final, or not yet old enough. An entry the database
            # deleted on an earlier attempt whose reply was lost, such as one
            # _grpc_call() retried after a deadline, also lands here, and
            # its audit event is never written.
            outcome = 'skipped'
        CLUSTER_DELETED_OBJECT_SECONDS.labels(objtype, outcome).observe(share)
        if outcomes is not None:
            outcomes[outcome] += 1


def _hard_delete_deleted_objects_singly(
        objtype: str, work: collections.deque[str], count: int,
        deadline: float,
        outcomes: Optional[collections.Counter[str]] = None) -> int:
    """Take up to ``count`` entries from ``work`` one at a time.

    No entry is started after ``deadline``, a time.time() value. Returns
    how many entries were taken.
    """
    taken = 0
    while taken < count and work and time.time() <= deadline:
        _hard_delete_deleted_object(objtype, work.popleft(), outcomes)
        taken += 1
    return taken


def _drain_deleted_object_work(
        execution_limit: float,
        outcomes: Optional[collections.Counter[str]] = None) -> int:
    """Take a turn from each type with work in turn, within the pass's limits.

    A turn is up to DELETED_OBJECT_BATCH_SIZE entries of one type: one batch
    hard delete for a type which can be deleted in batches, and one object
    at a time otherwise. The pass stops at ``execution_limit`` seconds or
    DELETED_OBJECT_SWEEP_MAX_OBJECTS entries, whichever comes first, and a
    turn never takes more entries than the cap has left.

    Returns how many entries were taken. If ``outcomes`` is given it is
    filled with how many entries ended in each of DELETED_OBJECT_OUTCOMES.
    """
    global _DELETED_OBJECT_NEXT_TYPE

    object_types = list(OBJECT_NAMES_TO_CLASSES)
    processed = 0
    start_time = time.time()
    deadline = start_time + execution_limit
    batch_unavailable = False
    while True:
        # Limit how long we spend in this loop, and how much we take
        if time.time() > deadline:
            return processed
        remaining = config.DELETED_OBJECT_SWEEP_MAX_OBJECTS - processed
        if remaining <= 0:
            return processed

        # The first type at or after the rotation's position which has
        # work, wrapping around the end of the list.
        objtype = None
        for offset in range(len(object_types)):
            index = (_DELETED_OBJECT_NEXT_TYPE + offset) % len(object_types)
            if _DELETED_OBJECT_WORK.get(object_types[index]):
                objtype = object_types[index]
                _DELETED_OBJECT_NEXT_TYPE = (index + 1) % len(object_types)
                break
        if objtype is None:
            return processed

        work = _DELETED_OBJECT_WORK[objtype]
        count = min(config.DELETED_OBJECT_BATCH_SIZE, remaining)

        # A type whose hard delete does more than delete rows -- an instance
        # releases its placement, a namespace cascades to what it owns -- is
        # hard deleted one object at a time by its own code.
        if (batch_unavailable or
                ObjectType(objtype) not in mariadb.HARD_DELETE_BATCH_FUNCTIONS):
            processed += _hard_delete_deleted_objects_singly(
                objtype, work, count, deadline, outcomes)
            continue

        batch = [work.popleft() for _ in range(min(count, len(work)))]
        try:
            _hard_delete_deleted_object_batch(objtype, batch, outcomes)
        except DatabaseUnavailable as e:
            # As with the fill, every further call would spend a whole
            # _grpc_call retry budget against a tier which has just failed
            # one, so the pass ends. The batch goes back where it was rather
            # than waiting for the next walk; anything in it the database did
            # delete before the reply was lost is skipped next time.
            work.extendleft(reversed(batch))
            LOG.with_fields({'object_type': objtype}).warning(
                f'Stopping per-deleted-object drain, database unavailable: '
                f'{e}')
            return processed
        except HardDeleteBatchUnavailable:
            # An sf-database which predates the batch. Each object's own
            # hard delete falls back to one delete at a time against it, and
            # the client remembers the answer for a while, so this and every
            # later turn of the pass go that way rather than asking again.
            work.extendleft(reversed(batch))
            LOG.warning(
                'Database service cannot hard delete in batches, hard '
                'deleting deleted objects one at a time for this pass')
            batch_unavailable = True
            processed += _hard_delete_deleted_objects_singly(
                objtype, work, count, deadline, outcomes)
            continue
        except ValueError as e:
            # The request was refused whole, most likely for one malformed
            # uuid. Left as a batch it would fail every entry in it on every
            # walk, so this turn goes one object at a time and only the bad
            # entry fails.
            work.extendleft(reversed(batch))
            LOG.with_fields({'object_type': objtype}).warning(
                f'Batch hard delete refused, hard deleting this turn one '
                f'object at a time: {e}')
            processed += _hard_delete_deleted_objects_singly(
                objtype, work, len(batch), deadline, outcomes)
            continue
        processed += len(batch)


# Orphan reconciliation (issue 3534). Zombie repair is excluded for nodes
# (marking a node deleted cascades deletion of its instances) and
# namespaces (which include 'system'); phantom state rows for those types
# are still removed, as a state row with no static row is garbage
# regardless of type.
ZOMBIE_REPAIR_EXCLUDED_TYPES = {'node', 'namespace'}

# Phantom state rows must be at least this old before removal, so object
# creation -- where the static and state rows land moments apart -- is
# never raced. Zombies are instead confirmed by being observed on two
# consecutive sweeps.
ORPHAN_MINIMUM_AGE = 3600

_ZOMBIE_CANDIDATES: dict[str, set[str]] = {}


def _repair_zombies(objtype: str, cls: type[dbo], confirmed: set[str]) -> bool:
    """Repair one object type's confirmed zombies.

    Returns False if a liveness read found the database tier
    unavailable, in which case the caller must end the pass. Nothing is
    written for that zombie or any after it; they are all still in
    _ZOMBIE_CANDIDATES, so the next pass confirms and retries them.
    Guessing instead would risk destroying a live object (issue 3836),
    and a tier-wide failure must not spend another _grpc_call retry
    budget per remaining object inside a job whose watchdog is only
    petted between jobs.

    Any other failure costs only that one object.
    """
    for obj_uuid in sorted(confirmed):
        try:
            repaired_state = cls.zombie_repair_state(obj_uuid)
        except DatabaseUnavailable as e:
            LOG.with_fields({
                'object_type': objtype,
                'object_uuid': obj_uuid,
                'error': str(e)
            }).warning('Orphan reconciliation could not tell whether a '
                       'zombie is live, stopping this pass')
            return False
        except Exception as e:
            util_exceptions.ignore_exception(
                f'zombie repair of {objtype} {obj_uuid}', e)
            continue

        try:
            if not mariadb.set_state(
                    ObjectType(objtype), obj_uuid,
                    State(value=repaired_state,
                          update_time=time.time(),
                          message=('reconciled object with no state '
                                   f'row to {repaired_state}'))):
                continue

            if repaired_state in cls.ACTIVE_STATES:
                audit = ('the orphan reconciliation sweep found this '
                         'object live with no state row and marked it '
                         f'{repaired_state}')
                log_message = ('Orphan reconciliation repaired live '
                               f'zombie object to {repaired_state}')
            else:
                audit = ('the orphan reconciliation sweep marked this '
                         f'object {repaired_state} because it had no '
                         'state row')
                log_message = ('Orphan reconciliation marked zombie '
                               f'object {repaired_state}')
            eventlog.add_event(EVENT_TYPE_AUDIT, objtype, obj_uuid, audit)
            LOG.with_fields({
                'object_type': objtype,
                'object_uuid': obj_uuid,
                'repaired_state': repaired_state
            }).info(log_message)
        except Exception as e:
            util_exceptions.ignore_exception(
                f'zombie repair of {objtype} {obj_uuid}', e)
    return True


@util_general.recorded_method
def reconcile_orphaned_objects():
    """Remove phantom state rows and repair zombie static rows.

    Phantoms (an object_states row whose static-values row is gone) are
    deleted server-side. Zombies (a static-values row with no
    object_states row) are invisible to every state-driven iterator,
    forever, so once one has been seen on two consecutive passes it is
    given the state its class's zombie_repair_state() returns.

    For most types that is deleted, which makes the zombie visible to
    the regular deleted-object sweep; that sweep then hard deletes it
    through the normal path. A type whose read path honours a stateless
    object (a namespace key, trusted issuer or mapping rule with its
    attributes row present) is instead repaired forward to created, so
    that reconciliation never destroys something a reader would still
    honour (issue 3836). If that liveness read finds the database tier
    unavailable, nothing is written, the pass stops and the zombie is
    retried on the next pass.

    Runs only on the elected cluster node.
    """
    for objtype in mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES:
        deleted = mariadb.delete_orphaned_object_states(
            ObjectType(objtype), time.time() - ORPHAN_MINIMUM_AGE)
        if deleted:
            LOG.with_fields({
                'object_type': objtype,
                'deleted': deleted
            }).info('Orphan reconciliation removed phantom state rows')

    deleted = mariadb.delete_orphaned_artifact_attributes()
    if deleted:
        LOG.with_fields({'deleted': deleted}).info(
            'Orphan reconciliation removed orphaned artifact attributes')

    zombie_types = [t for t in mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES
                    if t not in ZOMBIE_REPAIR_EXCLUDED_TYPES]
    for objtype in _sweep_object_types('reconcile_orphans', zombie_types):
        # Same two shapes, same treatment as the deleted-object sweep:
        # a per-reply failure only costs this object type's zombies, but
        # a tier-wide one ends the pass rather than spending another
        # retry budget per remaining type inside a job whose watchdog is
        # only petted between jobs. The next pass resumes after whichever
        # type stopped this one, so a persistently slow type cannot
        # permanently hide the ones behind it.
        work = _stateless_work_list('reconcile_orphans', ObjectType(objtype))
        if work.uuids is None:
            if work.tier_unavailable:
                _record_sweep_stopped(
                    'reconcile_orphans', zombie_types, objtype)
                break
            continue

        current = set(work.uuids)
        confirmed = current & _ZOMBIE_CANDIDATES.get(objtype, set())
        _ZOMBIE_CANDIDATES[objtype] = current
        if not confirmed:
            continue

        # Which state a zombie is repaired to is the class's decision,
        # not ours (issue 3836). Without the class there is no way to
        # ask, and falling back to deleted is the one answer that can
        # destroy a live object, so this type's zombies are left alone.
        # They stay candidates, so they are confirmed again next pass.
        try:
            cls = get_object_class(objtype)
        except Exception as e:
            util_exceptions.ignore_exception(
                f'zombie repair class lookup for {objtype}', e)
            continue

        if not _repair_zombies(objtype, cls, confirmed):
            # The tier went away mid-pass. Same treatment as a
            # tier-wide work-list failure above, for the same reason.
            _record_sweep_stopped('reconcile_orphans', zombie_types, objtype)
            break
    else:
        _record_sweep_completed('reconcile_orphans')


def clear_scheduler_capacity_metrics() -> None:
    """Drop the capacity gauges when this node stops being the leader.

    The cluster-wide gauges describe singleton cluster state rather than
    this node's own work, so a demoted node that keeps exporting its
    final values leaves two nodes publishing contradictory cluster
    numbers until the demoted one restarts. Counters do not have this
    problem -- they are monotonic and aggregate correctly across nodes --
    but these are gauges, so the demoted node has to stop answering. The
    newly elected node repopulates all of them on its first pass.

    The last-success and last-duration gauges are deliberately left
    alone. They describe this node's own last pass, not cluster state,
    and are worth keeping for debugging ("when did this node last
    reconcile successfully?"). They are also unlabelled, so they cannot
    be removed the way a label set can -- only overwritten. That means a
    freshness alert on them must aggregate across instances or it will
    fire forever on every node that has ever held the lock; see
    docs/operator_guide/database.md for the query.
    """
    for node_uuid in _CAPACITY_EXPORTED_NODES:
        for gauge in (SCHEDULER_CAPACITY_NODE_LIMIT,
                      SCHEDULER_CAPACITY_NODE_USED):
            for resource in CAPACITY_RESOURCES:
                try:
                    gauge.remove(node_uuid, resource)
                except KeyError:
                    pass
        try:
            SCHEDULER_CAPACITY_NODE_EXPECTED_DEMAND.remove(node_uuid)
        except KeyError:
            pass
    _CAPACITY_EXPORTED_NODES.clear()

    for gauge in (SCHEDULER_CAPACITY_CLUSTER_TOTAL,
                  SCHEDULER_CAPACITY_CLUSTER_CLAIMED,
                  SCHEDULER_CAPACITY_CLUSTER_UNCLAIMED_USED):
        for resource in CAPACITY_RESOURCES:
            try:
                gauge.remove(resource)
            except KeyError:
                pass


@util_general.recorded_method
def reconcile_scheduler_capacity() -> None:
    """Run the scheduler capacity reconciler cadence.

    Runs only on the elected cluster node. One pass is a single
    mariadb.reconcile_scheduler_capacity() RPC which expires stale
    claims, refreshes the node capacity rows from node_metrics,
    recomputes usage and expected demand, and recomputes the cluster
    singleton row. Placement admits against the counters in those
    tables, and a counter is only as good as its last recompute from
    ground truth, so this task's job is to keep the tables fresh and
    export what it saw as prometheus metrics and one structured log
    line per pass. It is a reconciler rather than a reaper: there are
    no abandoned rows to steal, only counters to correct, and any
    drift it corrects indicates a bug. See
    docs/plans/PLAN-scheduler-reservations-phase-02-capacity-tables.md.
    """
    SCHEDULER_CAPACITY_PASSES.inc()

    start_time = time.time()
    try:
        reply = mariadb.reconcile_scheduler_capacity()
    except Exception as e:
        util_exceptions.ignore_exception('scheduler capacity reconcile', e)
        reply = None
    duration = time.time() - start_time
    # Set before the failure return: a slow-then-failing pass is exactly
    # when an operator wants to know how long it ran, and leaving the
    # gauge at the last successful pass's duration hides that.
    SCHEDULER_CAPACITY_LAST_DURATION.set(duration)

    if not reply:
        SCHEDULER_CAPACITY_FAILURES.inc()
        LOG.with_fields({'duration': duration}).warning(
            'Scheduler capacity reconcile pass failed')
        return

    # Drift is the whole point of the reconcile pass: it recomputes the
    # used_* counters from ground truth and silently overwrites whatever
    # the incremental admit/release path had left behind. The reply
    # carries the per-node before/after difference, so summarise it here
    # -- without this the pass logs membership and timing only, and a
    # correction of a used_* counter leaves no trace at all. That made
    # "the reconciler reports zero drift" unverifiable from logs, which
    # is exactly what a soak of a change to the admit/release path needs
    # to check.
    drifted_nodes = 0
    drift_magnitude = {resource: 0 for resource in CAPACITY_RESOURCES}

    seen_nodes = set()
    for node in reply['nodes']:
        node_uuid = node['node_uuid']
        seen_nodes.add(node_uuid)
        deltas = {resource: node.get('delta_used_%s' % resource, 0)
                  for resource in CAPACITY_RESOURCES}
        if any(deltas.values()):
            drifted_nodes += 1
            for resource, delta in deltas.items():
                drift_magnitude[resource] += abs(delta)
            LOG.with_fields({
                'node': node_uuid,
                **{'delta_used_%s' % r: d for r, d in deltas.items()}
            }).warning('Scheduler capacity counters drifted, corrected')
        for resource in CAPACITY_RESOURCES:
            SCHEDULER_CAPACITY_NODE_LIMIT.labels(
                node=node_uuid, resource=resource).set(node[f'limit_{resource}'])
            SCHEDULER_CAPACITY_NODE_USED.labels(
                node=node_uuid, resource=resource).set(node[f'used_{resource}'])
        SCHEDULER_CAPACITY_NODE_EXPECTED_DEMAND.labels(
            node=node_uuid).set(node['expected_demand'])

    # Remove label sets for nodes which have left the cluster since the
    # last pass. Removing a label set which was never exported (for
    # example after a daemon restart) raises KeyError, so guard each.
    for node_uuid in _CAPACITY_EXPORTED_NODES - seen_nodes:
        for gauge in (SCHEDULER_CAPACITY_NODE_LIMIT,
                      SCHEDULER_CAPACITY_NODE_USED):
            for resource in CAPACITY_RESOURCES:
                try:
                    gauge.remove(node_uuid, resource)
                except KeyError:
                    pass
        try:
            SCHEDULER_CAPACITY_NODE_EXPECTED_DEMAND.remove(node_uuid)
        except KeyError:
            pass
    _CAPACITY_EXPORTED_NODES.clear()
    _CAPACITY_EXPORTED_NODES.update(seen_nodes)

    cluster = reply['cluster']
    for resource in CAPACITY_RESOURCES:
        SCHEDULER_CAPACITY_CLUSTER_TOTAL.labels(
            resource=resource).set(cluster[f'total_{resource}'])
        SCHEDULER_CAPACITY_CLUSTER_CLAIMED.labels(
            resource=resource).set(cluster[f'claimed_{resource}'])
        SCHEDULER_CAPACITY_CLUSTER_UNCLAIMED_USED.labels(
            resource=resource).set(cluster[f'unclaimed_used_{resource}'])

    SCHEDULER_CAPACITY_LAST_SUCCESS.set(time.time())

    LOG.with_fields({
        'nodes': len(reply['nodes']),
        'nodes_added': reply['nodes_added'],
        'nodes_removed': reply['nodes_removed'],
        'claims_expired': reply['claims_expired'],
        'drifted_nodes': drifted_nodes,
        **{'drift_%s' % r: m for r, m in drift_magnitude.items()},
        'duration': duration
    }).info('Scheduler capacity reconcile pass complete')
