# Copyright 2025 Michael Still and contributors

"""Database client library for accessing the database microservice.

This module provides a client interface to the database microservice, which
wraps etcd access. All database operations should go through this module
when the database service is enabled.
"""

import json
import threading
import time

import grpc
from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.protos import database_pb2
from shakenfist.protos import database_pb2_grpc
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)


# This module stores some state in thread local storage.
local = threading.local()
local.sf_database_client = None


def get_database_client():
    """Get or create a thread-local gRPC channel to the database service."""
    c = getattr(local, 'sf_database_client', None)
    if c:
        # Ensure the channel is ready. Use a longer timeout (2 seconds) to
        # handle service startup delays, especially during bootstrap when
        # multiple services are starting simultaneously.
        try:
            grpc.channel_ready_future(c).result(timeout=2.0)
        except grpc.FutureTimeoutError:
            # We do not close the channel here because this causes grpc to
            # sometimes throw a traceback from another thread trying to
            # monitor a now closed channel.
            c = None

    if not c:
        if not config.DATABASE_NODE_IP:
            LOG.error('Cannot communicate with database service, no '
                      'configured server!')
            return None

        local.sf_database_client = grpc.insecure_channel(
            f'{config.DATABASE_NODE_IP}:{config.DATABASE_API_PORT}',
            options=[
                ('keepalive_timeout_ms', 200),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.keepalive_permit_without_calls', 1),
                ('grpc.max_send_message_length', 100000000),
                ('grpc.max_receive_message_length', 100000000),
            ]
        )
        c = local.sf_database_client
    return c


def reset_client():
    """Reset the thread-local database client."""
    # We do not close the channel here because this causes grpc to sometimes
    # throw a traceback from another thread trying to monitor a now closed
    # channel.
    local.sf_database_client = None


def _retry_database(func):
    """Decorator to retry database operations on failure.

    Uses exponential backoff with 5 attempts to handle transient failures
    during service startup or network issues.
    """
    def wrapper(*args, **kwargs):
        attempt = 0
        last_exception = None
        max_attempts = 5

        while attempt < max_attempts:
            try:
                return func(*args, **kwargs)
            except grpc.RpcError as e:
                last_exception = e
                if attempt > 0:
                    LOG.with_fields({
                        'function': func.__name__,
                        'attempt': attempt,
                        'max_attempts': max_attempts
                    }).info('Failed database request via gRPC, retrying')
                reset_client()
                # Exponential backoff: 0.5, 1, 2, 4 seconds
                time.sleep(0.5 * (2 ** attempt))
                attempt += 1

        if last_exception:
            raise last_exception

    return wrapper


def is_available():
    """Check if the database service is configured and available."""
    if not config.DATABASE_NODE_IP:
        return False
    if config.DATABASE_USE_DIRECT_ETCD:
        return False
    return True


# Key-Value Operations

@_retry_database
def get(object_type, subtype, name):
    """Get a single value by key."""
    channel = get_database_client()
    if not channel:
        return None

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.GetRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name or ''
    )
    response = stub.Get(request)
    if not response.found:
        return None
    return json.loads(response.value)


@_retry_database
def get_all(object_type, subtype, prefix=None, limit=0):
    """Get all values matching a prefix. Returns a generator."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.GetPrefixRequest(
        object_type=object_type,
        subtype=subtype or '',
        prefix=prefix or '',
        limit=limit
    )
    response = stub.GetPrefix(request)
    for kv in response.results:
        yield kv.key, json.loads(kv.value)


@_retry_database
def put(object_type, subtype, name, data):
    """Store a value."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.PutRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name or '',
        data=util_json.json_dump(data)
    )
    response = stub.Put(request)
    if not response.success:
        LOG.error(f'Database put failed: {response.error}')


@_retry_database
def create(object_type, subtype, name, data):
    """Create a new value (fails if exists)."""
    channel = get_database_client()
    if not channel:
        return False

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.CreateRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name or '',
        data=util_json.json_dump(data)
    )
    response = stub.Create(request)
    return response.success


@_retry_database
def delete(object_type, subtype, name):
    """Delete a value."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.DeleteRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name or ''
    )
    response = stub.Delete(request)
    if not response.success:
        LOG.error(f'Database delete failed: {response.error}')


@_retry_database
def delete_prefix(path):
    """Delete all keys with a given prefix."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.DeletePrefixRequest(path=path)
    response = stub.DeletePrefix(request)
    if not response.success:
        LOG.error(f'Database delete_prefix failed: {response.error}')


@_retry_database
def replace_many_raw(mutations, suppress_failure_audit=False):
    """Atomic multi-key compare-and-swap operation."""
    channel = get_database_client()
    if not channel:
        return False, []

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.ReplaceManyRequest(
        suppress_failure_audit=suppress_failure_audit
    )

    for m in mutations:
        mutation = request.mutations.add()
        mutation.path = m['path']
        if m['original_data'] is None:
            mutation.original_is_none = True
            mutation.original_data = ''
        else:
            mutation.original_is_none = False
            mutation.original_data = util_json.json_dump(m['original_data'])
        if m['new_data'] is None:
            mutation.new_is_none = True
            mutation.new_data = ''
        else:
            mutation.new_is_none = False
            mutation.new_data = util_json.json_dump(m['new_data'])

    response = stub.ReplaceMany(request)

    failures = []
    for f in response.failures:
        failures.append({
            'path': f.path,
            'desired': f.desired,
            'actual': f.actual,
            'replacement': f.replacement
        })

    return response.success, failures


# Queue Operations

@_retry_database
def enqueue(queue_name, workitem, delay=0):
    """Add a work item to a queue."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.EnqueueRequest(
        queue_name=queue_name,
        work_item=util_json.json_dump(workitem),
        delay=delay
    )
    response = stub.Enqueue(request)
    if not response.success:
        LOG.error(f'Database enqueue failed: {response.error}')


@_retry_database
def dequeue(queue_name):
    """Claim the next available job from a queue."""
    channel = get_database_client()
    if not channel:
        return None

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.DequeueRequest(queue_name=queue_name)
    response = stub.Dequeue(request)
    if not response.found:
        return None
    return response.job_name, json.loads(response.work_item)


@_retry_database
def resolve(queue_name, job_name):
    """Mark a job as complete."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.ResolveRequest(
        queue_name=queue_name,
        job_name=job_name
    )
    response = stub.Resolve(request)
    if not response.success:
        LOG.error(f'Database resolve failed: {response.error}')


@_retry_database
def get_queue_length(queue_name):
    """Get queue statistics."""
    channel = get_database_client()
    if not channel:
        return 0, 0, 0

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.QueueLengthRequest(queue_name=queue_name)
    response = stub.GetQueueLength(request)
    return response.processing, response.queued, response.deferred


@_retry_database
def restart_queue(queue_name):
    """Move jobs from processing back to queue."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.RestartQueueRequest(queue_name=queue_name)
    response = stub.RestartQueue(request)
    if not response.success:
        LOG.error(f'Database restart_queue failed: {response.error}')


# Lock Operations

@_retry_database
def acquire_lock(object_type, subtype, name, lock_data):
    """Attempt to acquire a distributed lock."""
    channel = get_database_client()
    if not channel:
        return False

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.ClusterLockRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name,
        lock_data=util_json.json_dump(lock_data)
    )
    response = stub.AcquireLock(request)
    return response.acquired


@_retry_database
def release_lock(object_type, subtype, name, lock_data):
    """Release a distributed lock."""
    channel = get_database_client()
    if not channel:
        return False

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.ClusterReleaseLockRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name,
        lock_data=util_json.json_dump(lock_data)
    )
    response = stub.ReleaseLock(request)
    return response.success


@_retry_database
def get_lock_holder(object_type, subtype, name):
    """Get the current holder of a lock."""
    channel = get_database_client()
    if not channel:
        return {'holder': None}

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.ClusterGetLockHolderRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name
    )
    response = stub.GetLockHolder(request)
    if not response.held:
        return {'holder': None}
    return json.loads(response.holder)


@_retry_database
def clear_stale_locks():
    """Clear locks held by dead processes on this node."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.ClusterClearStaleLocksRequest(
        node_name=config.NODE_NAME
    )
    response = stub.ClearStaleLocks(request)
    if not response.success:
        LOG.error(f'Database clear_stale_locks failed: {response.error}')


@_retry_database
def get_existing_locks():
    """Get all existing locks in the cluster."""
    channel = get_database_client()
    if not channel:
        return {}

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.ClusterGetExistingLocksRequest()
    response = stub.GetExistingLocks(request)

    locks = {}
    for lock in response.locks:
        locks[lock.key] = json.loads(lock.holder)
    return locks


# Maintenance Operations

@_retry_database
def compact(revision):
    """Compact the etcd database."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.CompactRequest(revision=revision)
    response = stub.Compact(request)
    if not response.success:
        LOG.error(f'Database compact failed: {response.error}')
