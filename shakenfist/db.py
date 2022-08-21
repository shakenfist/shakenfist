# Copyright 2020 Michael Still

from shakenfist_utilities import logs

from shakenfist import constants
from shakenfist import etcd


LOG, _ = logs.setup(__name__)

#####################################################################
# Locks
#####################################################################


def get_lock(objecttype, subtype, name, ttl=60, timeout=constants.ETCD_ATTEMPT_TIMEOUT,
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


def get_existing_locks():
    return etcd.get_existing_locks()


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
