# Copyright 2019 Michael Still and contributors
#
# Distributed locking backed by MariaDB via the database
# microservice.

import os
import threading
import time

from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as util_random  # noreorder

from shakenfist import database
from shakenfist import exceptions
from shakenfist.config import config
from shakenfist.util import callstack as util_callstack


LOG, _ = logs.setup(__name__)


class ClusterLock:
    def __init__(self, objecttype, subtype, name,
                 timeout=120, log_ctx=LOG, op=None):
        self.objecttype = objecttype
        self.subtype = subtype
        self.objectname = name
        self.name = name

        self.timeout = timeout
        self.operation = op
        self.lockid = util_random.random_id()

        self.node = config.NODE_NAME
        self.pid = os.getpid()
        caller = util_callstack.get_caller(offset=-3)

        self.lock_data = {
            'node': self.node,
            'pid': self.pid,
            'thread': threading.get_ident(),
            'line': caller,
            'operation': self.operation,
            'id': self.lockid
        }
        self.log_ctx = log_ctx.with_fields(self.lock_data)

    def get_holder(self, key_prefix=''):
        value = database.get_lock_holder(
            self.objecttype, self.subtype, self.name)

        if value is None or value == {}:
            return {'holder': None}

        if key_prefix:
            new_holder = {}
            for key in value:
                new_holder[f'{key_prefix}-{key}'] = value[key]
            return new_holder

        return value

    def acquire(self):
        return database.acquire_lock(
            self.objecttype, self.subtype, self.name, self.lock_data)

    def is_acquired(self):
        holder = self.get_holder()
        for field in self.lock_data.keys():
            if holder.get(field) != self.lock_data[field]:
                return False
        return True

    def __enter__(self):
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            res = self.acquire()
            if res:
                return self
            time.sleep(0.5)

        current = self.get_holder(key_prefix='current')
        self.log_ctx.with_fields(current).with_fields({
            'duration': round(time.time() - start_time, 2)
            }).info('Failed to acquire lock')

        raise exceptions.LockException(
            'Cannot acquire lock %s, timed out after %.02f seconds'
            % (self.name, self.timeout))

    def release(self):
        return database.release_lock(
            self.objecttype, self.subtype, self.name, self.lock_data)

    def __exit__(self, _exception_type, _exception_value, _traceback):
        if self.release():
            return

        current = self.get_holder(key_prefix='current')
        self.log_ctx.with_fields(current).error(
            'Attempt to release a lock we were not holding')

    def __str__(self):
        return (f'ClusterLock({self.objecttype} {self.objectname}, '
                f'lock name "{self.name}", operation {self.operation}, '
                f'with timeout {self.timeout})')


def clear_stale_locks():
    """Clear locks held by dead processes on this node."""
    database.clear_stale_locks()


def get_existing_locks():
    """Get all existing locks in the cluster."""
    return database.get_existing_locks()
