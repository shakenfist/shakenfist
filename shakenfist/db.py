# Copyright 2020 Michael Still

import time
import uuid

from shakenfist.config import config
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import logutil


ETCD_ATTEMPT_TIMEOUT = 60


LOG, _ = logutil.setup(__name__)

#####################################################################
# Locks
#####################################################################


def get_lock(objecttype, subtype, name, ttl=60, timeout=ETCD_ATTEMPT_TIMEOUT,
             relatedobjects=None, log_ctx=LOG, op=None):
    return etcd.get_lock(objecttype, subtype, name, ttl=ttl, timeout=timeout,
                         log_ctx=log_ctx, op=op)


def refresh_lock(lock, relatedobjects=None, log_ctx=LOG):
    if lock:
        etcd.refresh_lock(lock, log_ctx=log_ctx)


def refresh_locks(locks, relatedobjects=None, log_ctx=LOG):
    if locks:
        for lock in locks:
            refresh_lock(lock, log_ctx=log_ctx)


def clear_stale_locks():
    etcd.clear_stale_locks()


def get_existing_locks():
    return etcd.get_existing_locks()


#####################################################################
# IPManagers
#####################################################################


def get_ipmanager(network_uuid):
    ipm = etcd.get('ipmanager', None, network_uuid)
    if not ipm:
        raise Exception('IP Manager not found for network %s' % network_uuid)
    return ipm


def persist_ipmanager(network_uuid, data):
    etcd.put('ipmanager', None, network_uuid, data)


def delete_ipmanager(network_uuid):
    etcd.delete('ipmanager', None, uuid)


#####################################################################
# Snapshots
#####################################################################


def create_snapshot(snapshot_uuid, device, instance_uuid, created):
    etcd.put(
        'snapshot', instance_uuid, created,
        {
            'uuid': snapshot_uuid,
            'device': device,
            'instance_uuid': instance_uuid,
            'created': created
        })


def get_instance_snapshots(instance_uuid):
    for _, m in etcd.get_all('snapshot', instance_uuid,
                             sort_order='ascend'):
        yield m

#####################################################################
# Events
#####################################################################


def add_event(object_type, object_uuid, operation, phase, duration, message):
    t = time.time()
    LOG.with_fields(
        {
            object_type: object_uuid,
            'fqdn': config.NODE_NAME,
            'operation': operation,
            'phase': phase,
            'duration': duration,
            'message': message
        }).info('Added event')
    etcd.put(
        'event/%s' % object_type, object_uuid, t,
        {
            'timestamp': t,
            'object_type': object_type,
            'object_uuid': object_uuid,
            'fqdn': config.NODE_NAME,
            'operation': operation,
            'phase': phase,
            'duration': duration,
            'message': message
        })


def get_events(object_type, object_uuid):
    for _, m in etcd.get_all('event/%s' % object_type, object_uuid,
                             sort_order='ascend'):
        yield m

#####################################################################
# Metrics
#####################################################################


def update_metrics_bulk(metrics):
    etcd.put(
        'metrics', config.NODE_NAME, None,
        {
            'fqdn': config.NODE_NAME,
            'timestamp': time.time(),
            'metrics': metrics
        },
        ttl=120)


def get_metrics(fqdn):
    d = etcd.get('metrics', fqdn, None)
    if not d:
        return {}
    return d.get('metrics', {})


#####################################################################
# Namespaces
#####################################################################


def list_namespaces():
    for _, value in etcd.get_all('namespace', None):
        yield value


def get_namespace(namespace):
    return etcd.get('namespace', None, namespace)


def persist_namespace(namespace, data):
    etcd.put('namespace', None, namespace, data)


def delete_namespace(namespace):
    etcd.delete('namespace', None, namespace)

#####################################################################
# Metadata
#####################################################################


def get_metadata(object_type, name):
    return etcd.get('metadata', object_type, name)


def persist_metadata(object_type, name, metadata):
    etcd.put('metadata', object_type, name, metadata)


def delete_metadata(object_type, name):
    etcd.delete('metadata', object_type, name)

#####################################################################
# vxid mappings
#####################################################################


def persist_node_vxid_mapping(node, vxid_to_mac):
    etcd.put('vxid_mapping', None, node, vxid_to_mac)


def get_node_vxid_mapping(node):
    etcd.get('vxid_mapping', None, node)

#####################################################################
# Queues
#####################################################################


def enqueue(queuename, workitem):
    etcd.enqueue(queuename, workitem)


def dequeue(queuename):
    try:
        return etcd.dequeue(queuename)
    except exceptions.LockException:
        # We didn't acquire the lock, we should just try again later. This probably
        # indicates congestion.
        return None, None


def resolve(queuename, jobname):
    etcd.resolve(queuename, jobname)


def get_queue_length(queuename):
    return etcd.get_queue_length(queuename)


def restart_queues():
    etcd.restart_queues()
