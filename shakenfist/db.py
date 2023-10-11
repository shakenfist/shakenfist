# Copyright 2020 Michael Still

from shakenfist_utilities import logs
import uuid

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