from collections import defaultdict
import queue
import time

from shakenfist_utilities import logs                 # noreorder

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FINAL_OBJECT_STATES
from shakenfist.constants import get_object_class
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.blob import Blob
from shakenfist import etcd
from shakenfist import mariadb
from shakenfist.schema.operations import baseclusteroperation as bco_schema
from shakenfist.schema.operations import node_blob_op as nbo_schema
from shakenfist.schema.operations import node_inst_op as nio_schema
from shakenfist.instance import Instance
from shakenfist.network.network import Network
from shakenfist.node import Node
from shakenfist.operations.baseoperation import get_general_background_node_queues
from shakenfist.operations.baseoperation import get_general_user_facing_node_queues
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)
BLOB_CHECKS_QUEUE = queue.Queue()
INSTANCE_CHECKS_QUEUE = queue.Queue()
DELETED_OBJECTS_QUEUE = queue.Queue()
IPAM_RESERVATIONS = queue.Queue()


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
    for objkey, _ in etcd.get_all(Blob.object_type, None):
        blob_uuid = objkey.split('/')[-1]
        state_data = etcd.get(
            f'attribute/{Blob.object_type}', blob_uuid, 'state')
        if not state_data or state_data.get('value') != Blob.STATE_CREATED:
            continue
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
        checksums = b.checksums
        node_uuids = b.locations

        requests_by_node = defaultdict(list)
        for _, value in etcd.get_prefix_raw('/sf/clusteroperations-by-blob/'):
            op_type = value.get('operation_type')
            op_uuid = value.get('operation_uuid')
            op = get_object_class(op_type).from_db(op_uuid)
            requests_by_node[op.node_uuid].append((op_type, op_uuid))

        for node_uuid in node_uuids:
            last_checksum = checksums.get(config.NODE_NAME, 0)
            age = time.time() - last_checksum

            if age < config.CHECKSUM_VERIFICATION_FREQUENCY:
                continue

            if not requests_by_node[node_uuid]:
                nbo_schema.create_and_enqueue(
                    node_uuid,
                    b.uuid,
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
            node.uuid,
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
            state = mariadb.get_state(objtype, obj_uuid)
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


@util_general.recorded_method
def per_ipam_reservations_checks():
    start_time = time.time()
    if IPAM_RESERVATIONS.empty():
        _fill_ipam_reservations_queue()
        LOG.info(
            'Refreshed ipam reservations queue with '
            f'{IPAM_RESERVATIONS.qsize()} items')

    queue_fill_cost = time.time() - start_time
    if queue_fill_cost > 10:
        return

    processed = _process_ipam_reservations_queue(
        execution_limit=(10 - queue_fill_cost))
    LOG.info(f'Processed {processed} items from ipam reservations queue')


def _fill_ipam_reservations_queue():
    seen = {}

    for key, _ in etcd.get_prefix_raw('/sf/ipam_reservations'):
        network_uuid = key.split('/')[3]
        if network_uuid not in seen:
            IPAM_RESERVATIONS.put(network_uuid)
            seen[network_uuid] = True


def _process_ipam_reservations_queue(execution_limit=10):
    processed = 0
    start_time = time.time()
    while True:
        # Limit how long we spend in this loop
        if time.time() - start_time > execution_limit:
            return processed

        try:
            network_uuid = IPAM_RESERVATIONS.get(block=False)
        except queue.Empty:
            return processed

        processed += 1
        n = Network.from_db(network_uuid, suppress_failure_audit=True)
        if not n:
            LOG.error(
                f'Deleting leaked IPAM reservations for {network_uuid}')
            etcd.delete_prefix(f'/sf/ipam_reservations/{network_uuid}')
