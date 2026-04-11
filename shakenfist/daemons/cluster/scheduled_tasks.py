import queue
import time

from shakenfist_utilities import logs                 # noreorder

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FINAL_OBJECT_STATES
from shakenfist.constants import get_object_class
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.schema.object_types import ObjectType
from shakenfist.blob import Blob
from shakenfist import etcd
from shakenfist import mariadb
from shakenfist.schema.operations import baseclusteroperation as bco_schema
from shakenfist.schema.operations import node_blob_op as nbo_schema
from shakenfist.schema.operations import node_inst_op as nio_schema
from shakenfist.instance import Instance
from shakenfist.node import Node
from shakenfist.operations.baseoperation import get_general_background_node_queues
from shakenfist.operations.baseoperation import get_general_user_facing_node_queues
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)
BLOB_CHECKS_QUEUE = queue.Queue()
INSTANCE_CHECKS_QUEUE = queue.Queue()
DELETED_OBJECTS_QUEUE = queue.Queue()


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
            b.add_event(
                EVENT_TYPE_AUDIT, 'deleting blob with reference count of 0')
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
        # blob, so we don't schedule duplicate checksum requests.
        nodes_with_pending_ops = set()
        pending_ops = mariadb.get_cluster_operation_targets_for_object(
            ObjectType.BLOB, str(b.uuid))
        for target in pending_ops:
            op = get_object_class(target.operation_type).from_db(
                target.operation_uuid)
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
    for objkey, _ in etcd.get_all(Instance.object_type, None):
        instance_uuid = objkey.split('/')[-1]
        state_data = etcd.get(
            f'attribute/{Instance.object_type}', instance_uuid, 'state')
        if not state_data or state_data.get('value') != Instance.STATE_CREATED:
            continue
        inst = Instance.from_db(instance_uuid)
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
    processing, queued, deferred = etcd.get_queue_length(queue)
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
        for objkey, _ in etcd.get_all(objtype, None):
            obj_uuid = objkey.split('/')[-1]
            state = mariadb.get_state(ObjectType(objtype), obj_uuid)
            state_value = state.value if state else None
            if state_value not in FINAL_OBJECT_STATES:
                continue
            obj = get_object_class(objtype).from_db(obj_uuid)
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
