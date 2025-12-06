# Copyright 2025 Michael Still and contributors

"""Database microservice daemon.

This daemon provides a gRPC interface to etcd, allowing all cluster nodes to
access the database through a single service running on the etcd_master node.
This is the first step in decoupling etcd from the rest of the codebase to
enable future migration to other database backends.
"""

from concurrent import futures
import json
import time

import grpc
from prometheus_client import Counter
from prometheus_client import start_http_server
from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.protos import database_pb2
from shakenfist.protos import database_pb2_grpc
from shakenfist.util import general as util_general
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)


class DatabaseService(database_pb2_grpc.DatabaseServiceServicer):
    """gRPC service implementation for database operations."""

    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor

    # Key-Value Operations

    def Get(self, request, context):
        """Get a single value by key."""
        try:
            self.monitor.counters['get'].inc()
            value = etcd.get(request.object_type, request.subtype, request.name)
            if value is None:
                return database_pb2.GetReply(found=False, value='')
            return database_pb2.GetReply(
                found=True,
                value=util_json.json_dump(value)
            )
        except Exception as e:
            util_general.ignore_exception('database Get failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetReply(found=False, value='')

    def GetPrefix(self, request, context):
        """Get all values matching a prefix."""
        try:
            self.monitor.counters['get_prefix'].inc()
            results = []
            for key, value in etcd.get_all(
                    request.object_type, request.subtype,
                    prefix=request.prefix if request.prefix else None,
                    limit=request.limit):
                results.append(database_pb2.KeyValue(
                    key=key,
                    value=util_json.json_dump(value)
                ))
            return database_pb2.GetPrefixReply(results=results)
        except Exception as e:
            util_general.ignore_exception('database GetPrefix failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetPrefixReply(results=[])

    def Put(self, request, context):
        """Store a value."""
        try:
            self.monitor.counters['put'].inc()
            data = json.loads(request.data)
            etcd.put(request.object_type, request.subtype, request.name, data)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_general.ignore_exception('database Put failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def Create(self, request, context):
        """Create a new value (fails if exists)."""
        try:
            self.monitor.counters['create'].inc()
            data = json.loads(request.data)
            success = etcd.create(
                request.object_type, request.subtype, request.name, data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_general.ignore_exception('database Create failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def Delete(self, request, context):
        """Delete a value."""
        try:
            self.monitor.counters['delete'].inc()
            etcd.delete(request.object_type, request.subtype, request.name)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_general.ignore_exception('database Delete failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeletePrefix(self, request, context):
        """Delete all keys with a given prefix."""
        try:
            self.monitor.counters['delete_prefix'].inc()
            etcd.delete_prefix(request.path)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_general.ignore_exception('database DeletePrefix failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def ReplaceMany(self, request, context):
        """Atomic multi-key compare-and-swap operation."""
        try:
            self.monitor.counters['replace_many'].inc()
            mutations = []
            for m in request.mutations:
                mutation = {
                    'path': m.path,
                    'original_data': (
                        None if m.original_is_none
                        else json.loads(m.original_data)
                    ),
                    'new_data': (
                        None if m.new_is_none
                        else json.loads(m.new_data)
                    )
                }
                mutations.append(mutation)

            success, failures = etcd.replace_many_raw(
                mutations,
                suppress_failure_audit=request.suppress_failure_audit
            )

            failure_msgs = []
            for f in failures:
                path = f['path']
                if isinstance(path, bytes):
                    path = path.decode()
                failure_msgs.append(database_pb2.MutationFailure(
                    path=path,
                    desired=str(f.get('desired', '')),
                    actual=str(f.get('actual', '')),
                    replacement=str(f.get('replacement', ''))
                ))

            return database_pb2.ReplaceManyReply(
                success=success,
                failures=failure_msgs
            )
        except Exception as e:
            util_general.ignore_exception('database ReplaceMany failed', e)
            return database_pb2.ReplaceManyReply(
                success=False,
                failures=[database_pb2.MutationFailure(
                    path='', desired='', actual='', replacement=str(e)
                )]
            )

    # Queue Operations

    def Enqueue(self, request, context):
        """Add a work item to a queue."""
        try:
            self.monitor.counters['enqueue'].inc()
            workitem = json.loads(request.work_item)
            etcd.enqueue(request.queue_name, workitem, delay=request.delay)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_general.ignore_exception('database Enqueue failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def Dequeue(self, request, context):
        """Claim the next available job from a queue."""
        try:
            self.monitor.counters['dequeue'].inc()
            result = etcd.dequeue(request.queue_name)
            if result is None:
                return database_pb2.DequeueReply(
                    found=False, job_name='', work_item='')
            job_name, workitem = result
            return database_pb2.DequeueReply(
                found=True,
                job_name=job_name,
                work_item=util_json.json_dump(workitem)
            )
        except Exception as e:
            util_general.ignore_exception('database Dequeue failed', e)
            return database_pb2.DequeueReply(
                found=False, job_name='', work_item='')

    def Resolve(self, request, context):
        """Mark a job as complete."""
        try:
            self.monitor.counters['resolve'].inc()
            etcd.resolve(request.queue_name, request.job_name)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_general.ignore_exception('database Resolve failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetQueueLength(self, request, context):
        """Get queue statistics."""
        try:
            self.monitor.counters['get_queue_length'].inc()
            processing, queued, deferred = etcd.get_queue_length(
                request.queue_name)
            return database_pb2.QueueLengthReply(
                processing=processing,
                queued=queued,
                deferred=deferred
            )
        except Exception as e:
            util_general.ignore_exception('database GetQueueLength failed', e)
            return database_pb2.QueueLengthReply(
                processing=0, queued=0, deferred=0)

    def RestartQueue(self, request, context):
        """Move jobs from processing back to queue."""
        try:
            self.monitor.counters['restart_queue'].inc()
            etcd.restart_queue(request.queue_name)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_general.ignore_exception('database RestartQueue failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # Lock Operations

    def AcquireLock(self, request, context):
        """Attempt to acquire a distributed lock."""
        try:
            self.monitor.counters['acquire_lock'].inc()
            lock_data = json.loads(request.lock_data)
            path = etcd._construct_key(
                request.object_type, request.subtype, request.name,
                prefix='sflocks'
            )
            acquired = etcd.create_raw(path, lock_data)
            return database_pb2.ClusterLockReply(acquired=acquired)
        except Exception as e:
            util_general.ignore_exception('database AcquireLock failed', e)
            return database_pb2.ClusterLockReply(acquired=False)

    def ReleaseLock(self, request, context):
        """Release a distributed lock."""
        try:
            self.monitor.counters['release_lock'].inc()
            lock_data = json.loads(request.lock_data)
            path = etcd._construct_key(
                request.object_type, request.subtype, request.name,
                prefix='sflocks'
            )
            released = etcd.transactional_delete_raw(path, lock_data)
            return database_pb2.StatusReply(success=released, error='')
        except Exception as e:
            util_general.ignore_exception('database ReleaseLock failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetLockHolder(self, request, context):
        """Get the current holder of a lock."""
        try:
            self.monitor.counters['get_lock_holder'].inc()
            path = etcd._construct_key(
                request.object_type, request.subtype, request.name,
                prefix='sflocks'
            )
            holder = etcd.get_raw(path)
            if holder is None or holder == {}:
                return database_pb2.ClusterLockHolderReply(held=False, holder='')
            return database_pb2.ClusterLockHolderReply(
                held=True,
                holder=util_json.json_dump(holder)
            )
        except Exception as e:
            util_general.ignore_exception('database GetLockHolder failed', e)
            return database_pb2.ClusterLockHolderReply(held=False, holder='')

    def ClearStaleLocks(self, request, context):
        """Clear locks held by dead processes on a node."""
        try:
            self.monitor.counters['clear_stale_locks'].inc()
            etcd.clear_stale_locks()
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_general.ignore_exception(
                'database ClearStaleLocks failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetExistingLocks(self, request, context):
        """Get all existing locks in the cluster."""
        try:
            self.monitor.counters['get_existing_locks'].inc()
            locks = etcd.get_existing_locks()
            lock_entries = []
            for key, holder in locks.items():
                lock_entries.append(database_pb2.ClusterLockEntry(
                    key=key,
                    holder=util_json.json_dump(holder)
                ))
            return database_pb2.ClusterGetExistingLocksReply(locks=lock_entries)
        except Exception as e:
            util_general.ignore_exception(
                'database GetExistingLocks failed', e)
            return database_pb2.ClusterGetExistingLocksReply(locks=[])

    # Maintenance Operations

    def Compact(self, request, context):
        """Compact the etcd database."""
        try:
            self.monitor.counters['compact'].inc()
            etcd.compact(request.revision)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_general.ignore_exception('database Compact failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))


class Monitor(daemon.WorkerPoolDaemon):
    """Background monitor for the database daemon."""

    def __init__(self, id):
        super().__init__(id)
        self.counters = {}

        # Create counters for all operations
        operations = [
            'get', 'get_prefix', 'put', 'create', 'delete', 'delete_prefix',
            'replace_many', 'enqueue', 'dequeue', 'resolve', 'get_queue_length',
            'restart_queue', 'acquire_lock', 'release_lock', 'get_lock_holder',
            'clear_stale_locks', 'get_existing_locks', 'compact'
        ]
        for op in operations:
            self.counters[op] = Counter(
                f'database_{op}_total',
                f'Number of {op} operations'
            )

        start_http_server(config.DATABASE_METRICS_PORT)

    def _run_inner(self):
        while daemon.check_abort_path(self.abort_path):
            try:
                # The database daemon doesn't have background work to do,
                # it just serves gRPC requests. We check health periodically.
                self.idle(10)
            except Exception as e:
                util_general.ignore_exception('database daemon', e)

            self.check_daemon_state()


def main():
    daemon.write_pid_file('database')
    m = Monitor('database')

    # Wait for nodelock daemon to be healthy before starting
    while not daemon.health_check_nodelock():
        LOG.info('Waiting for nodelock daemon to be healthy')
        time.sleep(1)
    LOG.info('nodelock daemon reports healthy')

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    database_pb2_grpc.add_DatabaseServiceServicer_to_server(
        DatabaseService(m), server)
    server.add_insecure_port(
        f'{config.DATABASE_NODE_IP}:{config.DATABASE_API_PORT}')

    LOG.info(
        f'Starting database gRPC server on '
        f'{config.DATABASE_NODE_IP}:{config.DATABASE_API_PORT}'
    )
    server.start()
    m.run()
    server.stop(1).wait()

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    raise SystemExit(0)
