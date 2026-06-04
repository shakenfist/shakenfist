# Copyright 2025 Michael Still and contributors

"""Database client library for accessing the database microservice.

This module provides a client interface to the database microservice. All
database operations should go through this module. The underlying storage
is MariaDB.
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
from shakenfist.util.grpc_channel import make_database_channel


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
        if not config.MARIADB_GATEWAY_HOSTS:
            LOG.error('Cannot communicate with database service, no '
                      'configured server!')
            return None

        local.sf_database_client = make_database_channel(
            config.MARIADB_GATEWAY_HOSTS,
            config.MARIADB_GATEWAY_PORT,
            extra_options=[
                ('grpc.keepalive_timeout_ms', 200),
                ('grpc.max_send_message_length', 100000000),
                ('grpc.max_receive_message_length', 100000000),
            ],
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
    if not config.MARIADB_GATEWAY_HOSTS:
        return False
    return True


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
    response = stub.AcquireLock(request, timeout=30, wait_for_ready=True)
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
    response = stub.ReleaseLock(request, timeout=30, wait_for_ready=True)
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
    response = stub.GetLockHolder(request, timeout=30, wait_for_ready=True)
    if not response.held:
        return {'holder': None}
    return json.loads(response.holder)


@_retry_database
def clear_stale_locks():
    """Clear locks held by dead processes on this node."""
    import psutil

    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.ClusterClearStaleLocksRequest(
        node_name=config.NODE_NAME,
        live_pids=list(psutil.pids()),
    )
    response = stub.ClearStaleLocks(request, timeout=30, wait_for_ready=True)
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
    response = stub.GetExistingLocks(request, timeout=30, wait_for_ready=True)

    locks = {}
    for lock in response.locks:
        locks[lock.key] = json.loads(lock.holder)
    return locks


# Cluster Config Operations

@_retry_database
def get_cluster_config():
    """Get all cluster config as a dict."""
    channel = get_database_client()
    if not channel:
        return {}

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.ClusterConfigRequest()
    response = stub.GetClusterConfig(
        request, timeout=30, wait_for_ready=True)

    config_data = {}
    for entry in response.entries:
        config_data[entry.key_name] = json.loads(
            entry.value_json)
    return config_data


@_retry_database
def set_cluster_config(key_name, value):
    """Set a single cluster config key."""
    channel = get_database_client()
    if not channel:
        return

    stub = database_pb2_grpc.DatabaseServiceStub(channel)
    request = database_pb2.SetClusterConfigRequest(
        key_name=key_name,
        value_json=json.dumps(value),
    )
    response = stub.SetClusterConfig(
        request, timeout=30, wait_for_ready=True)
    if not response.success:
        LOG.error(
            f'Database set_cluster_config failed: '
            f'{response.error}')
