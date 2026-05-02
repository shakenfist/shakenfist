# Copyright 2019 Michael Still and contributors
#
# Distributed locking backed by MariaDB via the database
# microservice. Locks are leased: every row in cluster_locks has an
# expires_at column managed server-side, holders extend their lease
# from a background refresher thread, and a candidate that finds an
# expired row may steal it. This means a node that dies hard never
# permanently strands a lock -- the row times out, another candidate
# acquires, and the cluster recovers without the dead holder ever
# rebooting.

import os
import threading
import time

from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as util_random  # noreorder

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.config import config
from shakenfist.util import callstack as util_callstack


LOG, _ = logs.setup(__name__)


# Lease length on the database side. Refreshes target one third of
# this so a holder can lose two consecutive refreshes (e.g. through a
# transient sf-database outage) and still keep the lock.
#
# Must stay aligned with ``mariadb.CLUSTER_LOCK_LEASE_SECONDS`` -- they
# are duplicated rather than shared because importing across the two
# modules would be circular. If you change one, change the other.
LEASE_SECONDS = 60
REFRESH_INTERVAL = LEASE_SECONDS // 3
# How long the refresher waits between attempts when it hits a
# transient failure. Tighter than REFRESH_INTERVAL so we recover
# quickly from short outages without blowing the lease.
REFRESH_RETRY = 2


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

        # Set when a refresh confirms the row no longer carries our
        # lock_id; long-held holders should poll this and abort the
        # critical section they were in.
        self.lost_event = threading.Event()
        # Drives the refresher. Set on release() to wake it up so it
        # can exit promptly rather than waiting out a sleep.
        self._stop_event = threading.Event()
        self._refresher = None

    def get_holder(self, key_prefix=''):
        value = mariadb.get_cluster_lock_holder(
            self.objecttype, self.subtype, self.name)

        if value == {'holder': None}:
            return {'holder': None}

        if key_prefix:
            new_holder = {}
            for key in value:
                new_holder[f'{key_prefix}-{key}'] = value[key]
            return new_holder

        return value

    def acquire(self):
        if mariadb.acquire_cluster_lock(
                self.objecttype, self.subtype, self.name, self.lock_data):
            self._start_refresher()
            return True
        return False

    def is_acquired(self):
        holder = self.get_holder()
        for field in self.lock_data.keys():
            if holder.get(field) != self.lock_data[field]:
                return False
        return True

    def __enter__(self):
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            if self.acquire():
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
        """Release the lock and stop refreshing.

        Raises ``LockNotHeld`` if the database has no record of us
        holding this lock -- that means the lease expired and somebody
        else stole it (or we never held it). Silent loss is worse than
        a noisy failure; callers can catch ``LockNotHeld`` if a release
        race is genuinely benign in their context.
        """
        self._stop_refresher()
        released = mariadb.release_cluster_lock(
            self.objecttype, self.subtype, self.name, self.lock_data)
        if not released:
            current = self.get_holder(key_prefix='current')
            self.log_ctx.with_fields(current).error(
                'Attempt to release a lock we were not holding')
            raise exceptions.LockNotHeld(
                f'Cannot release lock {self.name}: '
                f'no record of us holding it')
        return True

    def __exit__(self, _exception_type, _exception_value, _traceback):
        # release() raises LockNotHeld for the explicit-release path so a
        # silent lease loss is loud in CI. In the context-manager path the
        # body's exception is more interesting than ours, so catch and rely
        # on the log that release() already emitted.
        try:
            self.release()
        except exceptions.LockNotHeld:
            ...

    def __str__(self):
        return (f'ClusterLock({self.objecttype} {self.objectname}, '
                f'lock name "{self.name}", operation {self.operation}, '
                f'with timeout {self.timeout})')

    def _start_refresher(self):
        if self._refresher is not None:
            return
        self._stop_event.clear()
        self.lost_event.clear()
        self._refresher = threading.Thread(
            target=self._refresh_loop,
            name=f'lock-refresh-{self.name}',
            daemon=True)
        self._refresher.start()

    def _stop_refresher(self):
        if self._refresher is None:
            return
        self._stop_event.set()
        # Best-effort join. The refresher is a daemon thread so a
        # hung join during interpreter shutdown will not prevent
        # exit; but during normal release we want it stopped cleanly.
        self._refresher.join(timeout=REFRESH_INTERVAL + 1)
        if self._refresher.is_alive():
            # The refresher is still inside a slow gRPC call. Leaving
            # the handle attached would let `_start_refresher` spawn a
            # second thread on a re-acquire of this same lock object
            # while the orphan finishes. Logging is the most we can
            # do; the orphan is a daemon and will exit when its call
            # returns or the process exits.
            self.log_ctx.warning(
                'Lock refresher did not join cleanly; orphan thread')
        self._refresher = None

    def _refresh_loop(self):
        while not self._stop_event.is_set():
            # Wake up early if release() signals stop.
            if self._stop_event.wait(REFRESH_INTERVAL):
                return
            try:
                ok = mariadb.refresh_cluster_lock(
                    self.objecttype, self.subtype, self.name, self.lockid)
            except Exception as e:
                # Transient: log and try again sooner than a full
                # interval so we have a fighting chance of recovery
                # before the lease expires.
                self.log_ctx.with_fields({'error': str(e)}).warning(
                    'Lock refresh hit transient error, will retry')
                if self._stop_event.wait(REFRESH_RETRY):
                    return
                continue

            if not ok:
                # The database confirmed the row no longer carries our
                # lock_id. The lease was stolen.
                self.log_ctx.error(
                    'Lock lease lost; another holder has stolen it')
                self.lost_event.set()
                return


def clear_stale_locks():
    """Clear locks held by dead processes on this node."""
    import psutil
    mariadb.clear_stale_cluster_locks(
        config.NODE_NAME, list(psutil.pids()))


def get_existing_locks():
    """Get all existing locks in the cluster."""
    return mariadb.get_all_cluster_locks()
