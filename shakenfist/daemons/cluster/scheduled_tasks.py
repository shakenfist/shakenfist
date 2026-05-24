import queue
import time

from prometheus_client import Counter
from shakenfist_utilities import logs                 # noreorder

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FINAL_OBJECT_STATES
from shakenfist.constants import get_object_class
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.exceptions import InvalidStateException
from shakenfist.schema.object_types import ObjectType
from shakenfist.blob import Blob
from shakenfist import mariadb
from shakenfist.schema.operations import baseclusteroperation as bco_schema
from shakenfist.schema.operations import node_blob_op as nbo_schema
from shakenfist.schema.operations import node_inst_op as nio_schema
from shakenfist.instance import Instance
from shakenfist.node import Node
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_general_background_node_queues
from shakenfist.operations.baseoperation import get_general_user_facing_node_queues
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)
BLOB_CHECKS_QUEUE = queue.Queue()
INSTANCE_CHECKS_QUEUE = queue.Queue()
DELETED_OBJECTS_QUEUE = queue.Queue()

REAPER_REQUEUED = Counter(
    'cluster_op_reaper_requeued_total',
    'Stuck cluster operation work items that were re-queued.')
REAPER_REJECTED = Counter(
    'cluster_op_reaper_rejected_total',
    'Stuck cluster operation work items that exceeded '
    'max_attempts and were rejected.')


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
    blob_uuids = mariadb.get_objects_by_state(
        ObjectType.BLOB, [Blob.STATE_CREATED])
    for blob_uuid in (blob_uuids or []):
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
    instance_uuids = mariadb.get_objects_by_state(
        ObjectType.INSTANCE, [Instance.STATE_CREATED])
    for instance_uuid in (instance_uuids or []):
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

    Races with another freshly-elected cluster daemon are
    harmless: the row-level delete / update wins exactly once,
    and the loser's helper returns False so the loser skips the
    state transition.
    """
    threshold = config.CLUSTER_OP_STUCK_THRESHOLD
    max_attempts = config.CLUSTER_OP_MAX_ATTEMPTS

    stuck = mariadb.list_stuck_work_queue_rows(threshold)
    if not stuck:
        return

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


@util_general.recorded_method
def per_deleted_object_checks():
    start_time = time.time()
    if DELETED_OBJECTS_QUEUE.empty():
        _fill_per_deleted_object_queue()
        LOG.info(
            'Refreshed per-deleted-boject queue with '
            f'{DELETED_OBJECTS_QUEUE.qsize()} items')

    queue_fill_cost = time.time() - start_time
    if queue_fill_cost > 10:
        return

    processed = _process_per_deleted_object_queue(
        execution_limit=(10 - queue_fill_cost))
    LOG.info(f'Processed {processed} items from per-deleted-object queue')


def _fill_per_deleted_object_queue():
    for objtype in OBJECT_NAMES_TO_CLASSES:
        obj_uuids = mariadb.get_objects_by_state(
            ObjectType(objtype), FINAL_OBJECT_STATES)
        for obj_uuid in (obj_uuids or []):
            # The object row may have been hard-deleted by a concurrent
            # cleaner between the state query and this lookup; suppress
            # the failure audit so that race does not show up as an
            # ERROR event in the logs.
            obj = get_object_class(objtype).from_db(
                obj_uuid, suppress_failure_audit=True)
            if not obj:
                continue
            DELETED_OBJECTS_QUEUE.put(obj)


def _process_per_deleted_object_queue(execution_limit=10):
    processed = 0
    start_time = time.time()
    while True:
        # Limit how long we spend in this loop
        if time.time() - start_time > execution_limit:
            return processed

        try:
            obj = DELETED_OBJECTS_QUEUE.get(block=False)
        except queue.Empty:
            return processed

        processed += 1
        delay = config.CLEANER_DELAY
        if obj.object_type.endswith('_op'):
            delay = 30

        if time.time() - obj.state.update_time > delay:
            obj.hard_delete()
