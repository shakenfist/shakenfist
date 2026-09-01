# Copyright 2025 Michael Still and contributors

"""Database microservice daemon.

This daemon provides a gRPC interface to etcd, allowing all cluster nodes to
access the database through the sf-database service on the database tier.
This is the first step in decoupling etcd from the rest of the codebase to
enable future migration to other database backends.
"""

from concurrent import futures
from ipaddress import IPv4Address
import json
import time
from typing import Any
from typing import cast
from uuid import UUID

import grpc
from grpc_health.v1 import health
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc
from prometheus_client import Counter
from prometheus_client import Gauge
from prometheus_client import start_http_server
from sqlalchemy.exc import OperationalError
from shakenfist_utilities import logs  # noreorder

from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.daemons import daemon
from shakenfist.daemons.daemon import send_systemd_ready
from shakenfist.daemons.daemon import send_systemd_status
from shakenfist.exceptions import InvalidStateException
from shakenfist.node import Node
from shakenfist.operations.error_report import ErrorReport
from shakenfist.protos import database_pb2
from shakenfist.protos import database_pb2_grpc
from shakenfist.protos import shakenfist_enums_pb2
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.dnsmasq import DnsMasqData
from shakenfist.schema.event import EventRecord
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.schema.agentoperation_attributes import AgentOperationAttributesData
from shakenfist.schema.agentoperation_data import AgentOperationData
from shakenfist.schema.instance_attributes import InstanceAttributesData
from shakenfist.schema.instance_data import InstanceData
from shakenfist.schema.artifact_attributes import ArtifactAttributesData
from shakenfist.schema.artifact_data import ArtifactData
from shakenfist.schema.blob_attributes import BlobAttributesData
from shakenfist.schema.blob_data import BlobData
from shakenfist.schema.namespace_attributes import NamespaceAttributesData
from shakenfist.schema.network_interface_attributes import NetworkInterfaceAttributesData
from shakenfist.schema.ipam_data import IPAMData
from shakenfist.schema.network_attributes import NetworkAttributesData
from shakenfist.schema.network_data import NetworkData
from shakenfist.schema.network_interface_data import NetworkInterfaceData
from shakenfist.schema.node_attributes import NodeAttributesData
from shakenfist.schema.node_data import NodeData
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.schema.upload import UploadData
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import json as util_json
from shakenfist.util.caller_identity import KNOWN_CALLERS
from shakenfist.util.caller_identity import set_caller_identity


LOG, _ = logs.setup(__name__)


class DatabaseService(database_pb2_grpc.DatabaseServiceServicer):
    """gRPC service implementation for database operations."""

    def __init__(self, monitor: 'Monitor') -> None:
        super().__init__()
        self.monitor: Monitor = monitor

    # Queue Operations

    def Enqueue(
        self,
        request: database_pb2.EnqueueRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Add a work item to a queue."""
        try:
            self.monitor.counters['enqueue'].inc()
            workitem = json.loads(request.work_item)
            mariadb._direct_work_queue_enqueue(
                request.queue_name, workitem, delay=request.delay)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database Enqueue failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def Dequeue(
        self,
        request: database_pb2.DequeueRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DequeueReply:
        """Claim up to ``request.limit`` jobs across ``request.queue_names``.

        Queue order in the request is the caller's priority order
        (index 0 = top priority); the MariaDB query honours it via
        ``FIELD()``. Items are returned in the same order.
        """
        try:
            self.monitor.counters['dequeue'].inc()
            results = mariadb._direct_work_queue_dequeue_batch(
                list(request.queue_names),
                config.NODE_NAME,
                request.limit)
            return database_pb2.DequeueReply(
                items=[
                    database_pb2.DequeuedItem(
                        queue_name=queue_name,
                        job_name=job_name,
                        work_item=util_json.json_dump(workitem),
                    )
                    for queue_name, job_name, workitem in results
                ]
            )
        except Exception as e:
            util_exceptions.ignore_exception('database Dequeue failed', e)
            return database_pb2.DequeueReply(items=[])

    def Resolve(
        self,
        request: database_pb2.ResolveRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Mark a job as complete."""
        try:
            self.monitor.counters['resolve'].inc()
            mariadb._direct_work_queue_resolve(
                request.queue_name, request.job_name)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database Resolve failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetQueueLength(
        self,
        request: database_pb2.QueueLengthRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.QueueLengthReply:
        """Get queue statistics."""
        try:
            self.monitor.counters['get_queue_length'].inc()
            processing, queued, deferred = (
                mariadb._direct_work_queue_length(
                    request.queue_name))
            return database_pb2.QueueLengthReply(
                processing=processing,
                queued=queued,
                deferred=deferred
            )
        except Exception as e:
            util_exceptions.ignore_exception('database GetQueueLength failed', e)
            return database_pb2.QueueLengthReply(
                processing=0, queued=0, deferred=0)

    def RestartQueue(
        self,
        request: database_pb2.RestartQueueRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Clear claims on a queue so workers re-pick up the jobs."""
        try:
            self.monitor.counters['restart_queue'].inc()
            mariadb._direct_work_queue_restart(request.queue_name)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database RestartQueue failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def ListStuckWorkQueueRows(
        self,
        request: database_pb2.ListStuckWorkQueueRowsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ListStuckWorkQueueRowsReply:
        """List work_queue rows whose claim exceeds the threshold."""
        try:
            self.monitor.counters['list_stuck_work_queue_rows'].inc()
            rows = mariadb._direct_work_queue_list_stuck(
                request.threshold_seconds)
            return database_pb2.ListStuckWorkQueueRowsReply(
                rows=[
                    database_pb2.StuckWorkQueueRow(
                        id=row['id'],
                        queue_name=row['queue_name'],
                        claimed_at=row['claimed_at'],
                        claimed_by=row['claimed_by'] or '',
                        attempts=row['attempts'],
                        payload_json=util_json.json_dump(
                            row['payload']),
                    )
                    for row in rows
                ]
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ListStuckWorkQueueRows failed', e)
            return database_pb2.ListStuckWorkQueueRowsReply(rows=[])

    def ClearWorkQueueClaim(
        self,
        request: database_pb2.ClearWorkQueueClaimRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Clear the claim on a stuck work_queue row."""
        try:
            self.monitor.counters['clear_work_queue_claim'].inc()
            cleared = mariadb._direct_work_queue_clear_claim(
                request.row_id)
            return database_pb2.StatusReply(
                success=cleared, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ClearWorkQueueClaim failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteWorkQueueRow(
        self,
        request: database_pb2.DeleteWorkQueueRowRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a single work_queue row by id."""
        try:
            self.monitor.counters['delete_work_queue_row'].inc()
            deleted = mariadb._direct_work_queue_delete_row(
                request.row_id)
            return database_pb2.StatusReply(
                success=deleted, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteWorkQueueRow failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def FindExistingCoalescibleOp(
        self,
        request: database_pb2.FindExistingCoalescibleOpRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.FindExistingCoalescibleOpReply:
        """Read-only enqueue-side dedup lookup.

        Returns the uuid of an existing pending coalescible op on
        the same target, or an empty string when there's no match.
        See ``mariadb._direct_find_existing_coalescible_op``.

        Retained for one release so a rolling upgrade can still serve
        clients which predate ``FindExistingCoalescibleOpV2``. Its
        single (column, uuid) pair is simply a one-element key.
        """
        try:
            self.monitor.counters['find_existing_coalescible_op'].inc()
            uuid = mariadb._direct_find_existing_coalescible_op(
                request.operation_type,
                [(request.target_column, request.target_uuid)],
                request.task_name)
            return database_pb2.FindExistingCoalescibleOpReply(
                op_uuid=uuid or '')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database FindExistingCoalescibleOp failed', e)
            return database_pb2.FindExistingCoalescibleOpReply(op_uuid='')

    def ClaimCoalescibleSiblings(
        self,
        request: database_pb2.ClaimCoalescibleSiblingsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ClaimCoalescibleSiblingsReply:
        """Fold sibling pending coalescible ops.

        Transitions every matching sibling's ``object_states`` row
        to ``complete`` in a single statement and returns the
        affected uuids. See
        ``mariadb._direct_claim_coalescible_siblings`` for the
        safety guards.

        Retained for one release so a rolling upgrade can still serve
        clients which predate ``ClaimCoalescibleSiblingsV2``. Its
        single (column, uuid) pair is simply a one-element key.
        """
        try:
            self.monitor.counters['claim_coalescible_siblings'].inc()
            folded = mariadb._direct_claim_coalescible_siblings(
                request.operation_type,
                [(request.target_column, request.target_uuid)],
                list(request.task_names),
                request.exclude_op_uuid)
            return database_pb2.ClaimCoalescibleSiblingsReply(
                folded_op_uuids=folded)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ClaimCoalescibleSiblings failed', e)
            return database_pb2.ClaimCoalescibleSiblingsReply(
                folded_op_uuids=[])

    def FindExistingCoalescibleOpV2(
        self,
        request: database_pb2.FindExistingCoalescibleOpV2Request,
        context: grpc.ServicerContext
    ) -> database_pb2.FindExistingCoalescibleOpReply:
        """Read-only enqueue-side dedup lookup, multi-column key.

        As ``FindExistingCoalescibleOp``, but the caller supplies a
        list of ``(column, uuid)`` pairs which must all match rather
        than a single pair. The V1 method is retained for one release
        so a rolling upgrade can still serve old clients; a new client
        never falls back to it, because folding on the first column
        alone is the cross-node corruption this method exists to
        prevent. See ``mariadb._direct_find_existing_coalescible_op``.
        """
        try:
            self.monitor.counters['find_existing_coalescible_op_v2'].inc()
            uuid = mariadb._direct_find_existing_coalescible_op(
                request.operation_type,
                [(k.column, k.uuid) for k in request.keys],
                request.task_name)
            return database_pb2.FindExistingCoalescibleOpReply(
                op_uuid=uuid or '')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database FindExistingCoalescibleOpV2 failed', e)
            return database_pb2.FindExistingCoalescibleOpReply(op_uuid='')

    def ClaimCoalescibleSiblingsV2(
        self,
        request: database_pb2.ClaimCoalescibleSiblingsV2Request,
        context: grpc.ServicerContext
    ) -> database_pb2.ClaimCoalescibleSiblingsReply:
        """Fold sibling pending coalescible ops, multi-column key.

        As ``ClaimCoalescibleSiblings``, but the caller supplies a
        list of ``(column, uuid)`` pairs which must all match rather
        than a single pair. See
        ``mariadb._direct_claim_coalescible_siblings`` for the safety
        guards.
        """
        try:
            self.monitor.counters['claim_coalescible_siblings_v2'].inc()
            folded = mariadb._direct_claim_coalescible_siblings(
                request.operation_type,
                [(k.column, k.uuid) for k in request.keys],
                list(request.task_names),
                request.exclude_op_uuid)
            return database_pb2.ClaimCoalescibleSiblingsReply(
                folded_op_uuids=folded)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ClaimCoalescibleSiblingsV2 failed', e)
            return database_pb2.ClaimCoalescibleSiblingsReply(
                folded_op_uuids=[])

    # Lock Operations

    def AcquireLock(
        self,
        request: database_pb2.ClusterLockRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ClusterLockReply:
        """Attempt to acquire a distributed lock."""
        try:
            self.monitor.counters['acquire_lock'].inc()
            lock_data = json.loads(request.lock_data)
            lock_key = mariadb._cluster_lock_key(
                request.object_type, request.subtype,
                request.name)
            acquired = mariadb._direct_acquire_cluster_lock(
                lock_key=lock_key,
                holder_json=lock_data,
                node_uuid=lock_data.get('node', ''),
                pid=int(lock_data.get('pid', 0)),
                lock_id=lock_data.get('id', ''),
                now=time.time(),
            )
            return database_pb2.ClusterLockReply(acquired=acquired)
        except Exception as e:
            util_exceptions.ignore_exception('database AcquireLock failed', e)
            return database_pb2.ClusterLockReply(acquired=False)

    def ReleaseLock(
        self,
        request: database_pb2.ClusterReleaseLockRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Release a distributed lock.

        InnoDB deadlocks (errno 1213) on the cluster_locks row are
        routine when acquire / steal / release races overlap. Signal
        UNAVAILABLE so the gRPC client retries the delete rather than
        treating the transient as success=False -- which would
        otherwise trip the caller's ``LockNotHeld`` path and produce a
        noisy ``ERROR gunicorn`` log plus a real lease loss for any
        other waiter, since the abandoning holder has already stopped
        refreshing.
        """
        try:
            self.monitor.counters['release_lock'].inc()
            lock_data = json.loads(request.lock_data)
            lock_key = mariadb._cluster_lock_key(
                request.object_type, request.subtype,
                request.name)
            released = mariadb._direct_release_cluster_lock(
                lock_key=lock_key,
                lock_id=lock_data.get('id', ''),
            )
            return database_pb2.StatusReply(success=released, error='')
        except OperationalError as e:
            LOG.warning(
                f'ReleaseLock transient MariaDB error '
                f'({request.object_type}/{request.subtype}/{request.name}): '
                f'{e}')
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False, error=str(e))
        except Exception as e:
            util_exceptions.ignore_exception('database ReleaseLock failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def RefreshLock(
        self,
        request: database_pb2.ClusterRefreshLockRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Extend the lease on a distributed lock for the current holder.

        A transient MariaDB error must not look like "lock stolen" to
        the client (success=False), or the holder will spuriously
        re-elect. Signal UNAVAILABLE on transient failure so the
        client's retry / refresher loop can treat it as such.

        InnoDB deadlocks (errno 1213) are routine on this row when
        acquire/steal/refresh races overlap; gRPC retry handles them.
        Log them at warning without a traceback or on-disk exception
        record so the CI forbidden-string checks (Traceback / ERROR sf)
        do not trip on benign transients.
        """
        try:
            self.monitor.counters['refresh_lock'].inc()
            lock_key = mariadb._cluster_lock_key(
                request.object_type, request.subtype,
                request.name)
            refreshed = mariadb._direct_refresh_cluster_lock(
                lock_key=lock_key,
                lock_id=request.lock_id,
            )
            return database_pb2.StatusReply(success=refreshed, error='')
        except OperationalError as e:
            LOG.warning(
                f'RefreshLock transient MariaDB error '
                f'({request.object_type}/{request.subtype}/{request.name}): '
                f'{e}')
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False, error=str(e))
        except Exception as e:
            util_exceptions.ignore_exception('database RefreshLock failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetLockHolder(
        self,
        request: database_pb2.ClusterGetLockHolderRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ClusterLockHolderReply:
        """Get the current holder of a lock."""
        try:
            self.monitor.counters['get_lock_holder'].inc()
            lock_key = mariadb._cluster_lock_key(
                request.object_type, request.subtype,
                request.name)
            holder = mariadb._direct_get_cluster_lock(lock_key)
            if holder is None:
                return database_pb2.ClusterLockHolderReply(held=False, holder='')
            return database_pb2.ClusterLockHolderReply(
                held=True,
                holder=util_json.json_dump(holder)
            )
        except Exception as e:
            util_exceptions.ignore_exception('database GetLockHolder failed', e)
            return database_pb2.ClusterLockHolderReply(held=False, holder='')

    def ClearStaleLocks(
        self,
        request: database_pb2.ClusterClearStaleLocksRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Clear locks held by dead processes on a node."""
        try:
            self.monitor.counters['clear_stale_locks'].inc()
            mariadb._direct_clear_stale_cluster_locks(
                node_uuid=request.node_name,
                live_pids=list(request.live_pids),
            )
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ClearStaleLocks failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetExistingLocks(
        self,
        request: database_pb2.ClusterGetExistingLocksRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ClusterGetExistingLocksReply:
        """Get all existing locks in the cluster."""
        try:
            self.monitor.counters['get_existing_locks'].inc()
            locks = mariadb._direct_get_all_cluster_locks()
            lock_entries = []
            for key, holder in locks.items():
                lock_entries.append(database_pb2.ClusterLockEntry(
                    key=key,
                    holder=util_json.json_dump(holder)
                ))
            return database_pb2.ClusterGetExistingLocksReply(locks=lock_entries)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetExistingLocks failed', e)
            return database_pb2.ClusterGetExistingLocksReply(locks=[])

    # Cluster Config Operations

    def GetClusterConfig(
        self,
        request: database_pb2.ClusterConfigRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ClusterConfigReply:
        """Get all cluster config entries."""
        try:
            self.monitor.counters['get_cluster_config'].inc()
            config_data = mariadb._direct_get_all_cluster_config()
            entries = []
            for key_name, value in config_data.items():
                entries.append(
                    database_pb2.ClusterConfigEntry(
                        key_name=key_name,
                        value_json=json.dumps(value),
                    ))
            return database_pb2.ClusterConfigReply(
                entries=entries)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetClusterConfig failed', e)
            return database_pb2.ClusterConfigReply(entries=[])

    def SetClusterConfig(
        self,
        request: database_pb2.SetClusterConfigRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Set a single cluster config key."""
        try:
            self.monitor.counters['set_cluster_config'].inc()
            value = json.loads(request.value_json)
            mariadb._direct_set_cluster_config(
                request.key_name, value)
            return database_pb2.StatusReply(
                success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database SetClusterConfig failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteClusterConfig(
        self,
        request: database_pb2.DeleteClusterConfigRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a single cluster config key."""
        try:
            self.monitor.counters['delete_cluster_config'].inc()
            mariadb._direct_delete_cluster_config(
                request.key_name)
            return database_pb2.StatusReply(
                success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteClusterConfig failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    # Object State Operations (MariaDB)
    # These operations provide access to MariaDB state storage for all daemons.
    # The database service uses direct MariaDB access; all other daemons call
    # these gRPC methods.

    def GetObjectState(
        self,
        request: database_pb2.GetObjectStateRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetObjectStateReply:
        """Get state for an object from MariaDB."""
        try:
            self.monitor.counters['get_object_state'].inc()
            object_type = ObjectType.from_proto_id(request.object_type)
            if object_type is None:
                return database_pb2.GetObjectStateReply(found=False)
            state = mariadb.get_state(object_type, request.object_uuid)
            if state is None:
                return database_pb2.GetObjectStateReply(found=False)
            return database_pb2.GetObjectStateReply(
                found=True,
                state_value=state.value or '',
                update_time=state.update_time,
                message=state.message or ''
            )
        except Exception as e:
            util_exceptions.ignore_exception('database GetObjectState failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetObjectStateReply(found=False)

    def SetObjectState(
        self,
        request: database_pb2.SetObjectStateRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Set state for an object in MariaDB."""
        try:
            self.monitor.counters['set_object_state'].inc()
            from shakenfist.schema.object_state import State
            object_type = ObjectType.from_proto_id(request.object_type)
            if object_type is None:
                return database_pb2.StatusReply(
                    success=False, error='Invalid object_type')
            state = State(
                value=request.state_value,
                update_time=request.update_time,
                message=request.message if request.message else None
            )
            success = mariadb.set_state(object_type, request.object_uuid, state)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database SetObjectState failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteObjectState(
        self,
        request: database_pb2.DeleteObjectStateRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete state for an object from MariaDB."""
        try:
            self.monitor.counters['delete_object_state'].inc()
            object_type = ObjectType.from_proto_id(request.object_type)
            if object_type is None:
                return database_pb2.StatusReply(
                    success=False, error='Invalid object_type')
            success = mariadb.delete_state(object_type, request.object_uuid)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteObjectState failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetObjectsByState(
        self,
        request: database_pb2.GetObjectsByStateRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetObjectsByStateReply:
        """Get all object UUIDs of a given type in specified states.

        An empty reply must mean "no objects are in those states", never
        "the read failed". The client cannot tell the difference -- the
        reply carries no error field -- and one caller,
        ``get_active_blob_uuids()``, hands its result to the cleaner as a
        *complement* set: every blob file on disk not named in it is
        unlinked. So a failed read answered as an empty list is an
        instruction to delete the node's entire blob store (#3638).

        Raising the client's receive cap fixed the oversized-reply route
        into that hazard, but not this one:
        ``_direct_get_objects_by_state()`` returns None on
        ``OperationalError`` -- MariaDB down, connection dropped, lock
        wait timeout, deadlock -- which is the failure an operator is
        most likely to actually hit, because it happens while
        sf-database itself is perfectly healthy and answering. Signal it
        on the status instead, following ReleaseLock and RefreshLock
        above: UNAVAILABLE is retried by ``_grpc_call`` and becomes
        ``DatabaseUnavailable`` once the budget is spent, which is
        exactly the shape the callers were built to handle.
        """
        try:
            self.monitor.counters['get_objects_by_state'].inc()
            object_type = ObjectType.from_proto_id(request.object_type)
            if object_type is None:
                # An unknown or unset object type is a bad request, not a
                # type with no objects in it. Non-retryable, so the client
                # wrapper maps it to None rather than to an empty list.
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(
                    f'unknown object type id {request.object_type}')
                return database_pb2.GetObjectsByStateReply(object_uuids=[])
            uuids = mariadb.get_objects_by_state(
                object_type, list(request.state_values),
                updated_before=(request.updated_before or None))
            if uuids is None:
                LOG.warning(
                    f'GetObjectsByState transient MariaDB error for '
                    f'{object_type} in {list(request.state_values)}')
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                context.set_details('object state read failed')
                return database_pb2.GetObjectsByStateReply(object_uuids=[])
            return database_pb2.GetObjectsByStateReply(object_uuids=uuids)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetObjectsByState failed', e)
            # INTERNAL rather than UNAVAILABLE: an unexpected exception in
            # this handler is a bug, not a transient outage, so retrying
            # it wastes the caller's budget. It is non-retryable, so the
            # client wrapper turns it into None -- a failed read -- which
            # is the whole point.
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetObjectsByStateReply(object_uuids=[])

    def DeleteOrphanedObjectStates(
        self,
        request: database_pb2.DeleteOrphanedObjectStatesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteOrphanedObjectStatesReply:
        """Delete phantom object_states rows for one object type."""
        try:
            self.monitor.counters['delete_orphaned_object_states'].inc()
            object_type = ObjectType.from_proto_id(request.object_type)
            if object_type is None:
                return database_pb2.DeleteOrphanedObjectStatesReply(
                    success=False, deleted=0)
            deleted = mariadb.delete_orphaned_object_states(
                object_type, request.updated_before)
            if deleted is None:
                return database_pb2.DeleteOrphanedObjectStatesReply(
                    success=False, deleted=0)
            return database_pb2.DeleteOrphanedObjectStatesReply(
                success=True, deleted=deleted)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteOrphanedObjectStates failed', e)
            return database_pb2.DeleteOrphanedObjectStatesReply(
                success=False, deleted=0)

    def GetStatelessObjectUuids(
        self,
        request: database_pb2.GetStatelessObjectUuidsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetStatelessObjectUuidsReply:
        """List zombie static rows (no object_states row) for a type.

        Same contract as GetObjectsByState above, and for the same
        reason: an empty reply means "this type has no zombies", never
        "the read failed". The consequence here is milder -- orphan
        reconciliation silently repairs nothing rather than deleting
        anything -- but the caller cannot distinguish the two, and a
        reconcile sweep that quietly stops running is how the orphans it
        exists to fix stay invisible to every state-driven iterator.
        """
        try:
            self.monitor.counters['get_stateless_object_uuids'].inc()
            object_type = ObjectType.from_proto_id(request.object_type)
            if object_type is None:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(
                    f'unknown object type id {request.object_type}')
                return database_pb2.GetStatelessObjectUuidsReply(
                    object_uuids=[])
            uuids = mariadb.get_stateless_object_uuids(object_type)
            if uuids is None:
                LOG.warning(
                    'GetStatelessObjectUuids transient MariaDB error for '
                    f'{object_type}')
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                context.set_details('stateless object read failed')
                return database_pb2.GetStatelessObjectUuidsReply(
                    object_uuids=[])
            return database_pb2.GetStatelessObjectUuidsReply(
                object_uuids=uuids)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetStatelessObjectUuids failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetStatelessObjectUuidsReply(object_uuids=[])

    def DeleteOrphanedArtifactAttributes(
        self,
        request: database_pb2.DeleteOrphanedArtifactAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteOrphanedArtifactAttributesReply:
        """Delete artifact_attributes rows whose artifact row is gone."""
        try:
            self.monitor.counters['delete_orphaned_artifact_attributes'].inc()
            deleted = mariadb.delete_orphaned_artifact_attributes()
            if deleted is None:
                return database_pb2.DeleteOrphanedArtifactAttributesReply(
                    success=False, deleted=0)
            return database_pb2.DeleteOrphanedArtifactAttributesReply(
                success=True, deleted=deleted)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteOrphanedArtifactAttributes failed', e)
            return database_pb2.DeleteOrphanedArtifactAttributesReply(
                success=False, deleted=0)

    # IPAM Reservation Operations (MariaDB)
    # These operations provide atomic IP address reservation and management.

    def ReserveAddress(
        self,
        request: database_pb2.ReserveAddressRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Atomically reserve an IP address."""
        try:
            self.monitor.counters['reserve_address'].inc()
            from shakenfist.schema.ipam_reservation import IPAMReservation
            # Convert protobuf enums to Python enums
            res_type = ReservationType.from_proto_id(
                request.reservation.reservation_type)
            if res_type is None:
                return database_pb2.StatusReply(
                    success=False, error='Invalid reservation_type')
            user_type = ObjectType.from_proto_id(
                request.reservation.user_type)  # None is valid here
            user_uuid = (UUID(request.reservation.user_uuid)
                         if request.reservation.user_uuid else None)
            reservation = IPAMReservation(
                ipam_uuid=UUID(request.reservation.ipam_uuid),
                address=IPv4Address(request.reservation.address),
                reservation_type=res_type,
                user_type=user_type,
                user_uuid=user_uuid,
                reserved_at=request.reservation.reserved_at,
                comment=request.reservation.comment or None
            )
            success = mariadb._direct_reserve_address(reservation)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database ReserveAddress failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def ReleaseAddress(
        self,
        request: database_pb2.ReleaseAddressRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Release an IP address (convert to deletion-halo)."""
        try:
            self.monitor.counters['release_address'].inc()
            from shakenfist.schema.ipam_reservation import IPAMReservation
            # Convert protobuf enums to Python enums
            res_type = ReservationType.from_proto_id(
                request.halo_reservation.reservation_type)
            if res_type is None:
                return database_pb2.StatusReply(
                    success=False, error='Invalid reservation_type')
            user_type = ObjectType.from_proto_id(
                request.halo_reservation.user_type)  # None is valid here
            user_uuid = (UUID(request.halo_reservation.user_uuid)
                         if request.halo_reservation.user_uuid else None)
            halo_reservation = IPAMReservation(
                ipam_uuid=UUID(request.halo_reservation.ipam_uuid),
                address=IPv4Address(request.halo_reservation.address),
                reservation_type=res_type,
                user_type=user_type,
                user_uuid=user_uuid,
                reserved_at=request.halo_reservation.reserved_at,
                comment=request.halo_reservation.comment or None
            )
            success = mariadb._direct_release_address(
                UUID(request.ipam_uuid), request.address, halo_reservation)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database ReleaseAddress failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetReservation(
        self,
        request: database_pb2.GetReservationRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetReservationReply:
        """Get a single reservation."""
        try:
            self.monitor.counters['get_reservation'].inc()
            reservation = mariadb._direct_get_reservation(
                UUID(request.ipam_uuid), request.address)
            if reservation is None:
                return database_pb2.GetReservationReply(found=False)
            return database_pb2.GetReservationReply(
                found=True,
                reservation=database_pb2.IPAMReservationData(
                    ipam_uuid=str(reservation.ipam_uuid),
                    address=str(reservation.address),
                    reservation_type=cast(
                        shakenfist_enums_pb2.ReservationType.ValueType,
                        reservation.reservation_type.proto_id),
                    user_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        reservation.user_type.proto_id
                        if reservation.user_type else 0
                    ),
                    user_uuid=(str(reservation.user_uuid)
                               if reservation.user_uuid else ''),
                    reserved_at=reservation.reserved_at,
                    comment=reservation.comment or ''
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception('database GetReservation failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetReservationReply(found=False)

    def GetReservationsForIPAM(
        self,
        request: database_pb2.GetReservationsForIPAMRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetReservationsForIPAMReply:
        """Get all reservations for an IPAM."""
        try:
            self.monitor.counters['get_reservations_for_ipam'].inc()
            reservations = mariadb._direct_get_reservations_for_ipam(
                UUID(request.ipam_uuid))
            result = []
            for res in reservations:
                result.append(database_pb2.IPAMReservationData(
                    ipam_uuid=str(res.ipam_uuid),
                    address=str(res.address),
                    reservation_type=cast(
                        shakenfist_enums_pb2.ReservationType.ValueType,
                        res.reservation_type.proto_id),
                    user_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        res.user_type.proto_id if res.user_type else 0
                    ),
                    user_uuid=str(res.user_uuid) if res.user_uuid else '',
                    reserved_at=res.reserved_at,
                    comment=res.comment or ''
                ))
            return database_pb2.GetReservationsForIPAMReply(reservations=result)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetReservationsForIPAM failed', e)
            return database_pb2.GetReservationsForIPAMReply(reservations=[])

    def DeleteReservation(
        self,
        request: database_pb2.DeleteReservationRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a single reservation."""
        try:
            self.monitor.counters['delete_reservation'].inc()
            success = mariadb._direct_delete_reservation(
                UUID(request.ipam_uuid), request.address)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteReservation failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteReservationsForIPAM(
        self,
        request: database_pb2.DeleteReservationsForIPAMRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteCountReply:
        """Delete all reservations for an IPAM."""
        try:
            self.monitor.counters['delete_reservations_for_ipam'].inc()
            count = mariadb._direct_delete_reservations_for_ipam(
                UUID(request.ipam_uuid))
            return database_pb2.DeleteCountReply(count=count)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteReservationsForIPAM failed', e)
            return database_pb2.DeleteCountReply(count=0)

    def ReleaseHaloedAddresses(
        self,
        request: database_pb2.ReleaseHaloedAddressesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteCountReply:
        """Release expired deletion-halo addresses."""
        try:
            self.monitor.counters['release_haloed_addresses'].inc()
            count = mariadb._direct_release_haloed_addresses(
                UUID(request.ipam_uuid), request.older_than)
            return database_pb2.DeleteCountReply(count=count)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ReleaseHaloedAddresses failed', e)
            return database_pb2.DeleteCountReply(count=0)

    def GetAddressesInUse(
        self,
        request: database_pb2.GetAddressesInUseRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAddressesInUseReply:
        """Get all addresses in use for an IPAM."""
        try:
            self.monitor.counters['get_addresses_in_use'].inc()
            addresses = mariadb._direct_get_addresses_in_use(
                UUID(request.ipam_uuid))
            return database_pb2.GetAddressesInUseReply(
                addresses=list(addresses))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAddressesInUse failed', e)
            return database_pb2.GetAddressesInUseReply(addresses=[])

    # Upload Operations (MariaDB)
    # These operations manage upload objects in MariaDB. Uploads are temporary
    # objects that receive streamed data before being converted to artifacts.

    def CreateUpload(
        self,
        request: database_pb2.CreateUploadRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create an upload record in MariaDB."""
        try:
            self.monitor.counters['create_upload'].inc()
            success = mariadb._direct_create_upload(
                UUID(request.upload.uuid),
                request.upload.node,
                request.upload.created_at,
                request.upload.version
            )
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database CreateUpload failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetUpload(
        self,
        request: database_pb2.GetUploadRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetUploadReply:
        """Get upload static values from MariaDB."""
        try:
            self.monitor.counters['get_upload'].inc()
            data = mariadb._direct_get_upload(UUID(request.uuid))
            if data is None:
                return database_pb2.GetUploadReply(found=False)
            return database_pb2.GetUploadReply(
                found=True,
                upload=database_pb2.UploadData(
                    uuid=str(data.uuid),
                    node=data.node,
                    created_at=data.created_at,
                    version=data.version
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception('database GetUpload failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetUploadReply(found=False)

    def GetUploads(
        self,
        request: database_pb2.GetUploadsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetUploadsReply:
        """Get uploads from MariaDB with optional filters."""
        try:
            self.monitor.counters['get_uploads'].inc()
            # Convert empty string/zero to None for optional filters
            node = request.node if request.node else None
            created_before = request.created_before if request.created_before else None
            uploads_data = mariadb._direct_get_uploads(node, created_before)
            uploads = [
                database_pb2.UploadData(
                    uuid=str(u.uuid),
                    node=u.node,
                    created_at=u.created_at,
                    version=u.version
                )
                for u in uploads_data
            ]
            return database_pb2.GetUploadsReply(uploads=uploads)
        except Exception as e:
            util_exceptions.ignore_exception('database GetUploads failed', e)
            return database_pb2.GetUploadsReply(uploads=[])

    def DeleteUpload(
        self,
        request: database_pb2.DeleteUploadRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete an upload record from MariaDB."""
        try:
            self.monitor.counters['delete_upload'].inc()
            success = mariadb._direct_delete_upload(UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database DeleteUpload failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def UpdateUpload(
        self,
        request: database_pb2.UpdateUploadRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update an upload record in MariaDB."""
        try:
            self.monitor.counters['update_upload'].inc()
            data = UploadData(
                uuid=UUID(request.upload.uuid),
                node=request.upload.node,
                created_at=request.upload.created_at,
                version=request.upload.version
            )
            success = mariadb._direct_update_upload(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database UpdateUpload failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # =========================================================================
    # Blob Operations (MariaDB)
    # These operations manage blob static values in MariaDB. Blobs are
    # immutable binary data objects (images, snapshots, etc.) that store
    # actual content on disk and track metadata in the database.
    # =========================================================================

    def CreateBlob(
        self,
        request: database_pb2.CreateBlobRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create a blob record in MariaDB."""
        try:
            self.monitor.counters['create_blob'].inc()
            success = mariadb._direct_create_blob(
                UUID(request.blob.uuid),
                request.blob.modified,
                request.blob.fetched_at,
                request.blob.version
            )
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database CreateBlob failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetBlob(
        self,
        request: database_pb2.GetBlobRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetBlobReply:
        """Get blob static values from MariaDB."""
        try:
            self.monitor.counters['get_blob'].inc()
            data = mariadb._direct_get_blob(UUID(request.uuid))
            if data is None:
                return database_pb2.GetBlobReply(found=False)
            return database_pb2.GetBlobReply(
                found=True,
                blob=database_pb2.BlobData(
                    uuid=str(data.uuid),
                    modified=data.modified,
                    fetched_at=data.fetched_at,
                    version=data.version
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception('database GetBlob failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetBlobReply(found=False)

    def GetAllBlobUuids(
        self,
        request: database_pb2.GetAllBlobUuidsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllBlobUuidsReply:
        """Get all blob UUIDs from MariaDB."""
        try:
            self.monitor.counters['get_all_blob_uuids'].inc()
            uuids = mariadb._direct_get_all_blob_uuids()
            return database_pb2.GetAllBlobUuidsReply(uuids=uuids)
        except Exception as e:
            util_exceptions.ignore_exception('database GetAllBlobUuids failed', e)
            return database_pb2.GetAllBlobUuidsReply(uuids=[])

    def DeleteBlob(
        self,
        request: database_pb2.DeleteBlobRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a blob record from MariaDB."""
        try:
            self.monitor.counters['delete_blob'].inc()
            success = mariadb._direct_delete_blob(UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database DeleteBlob failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def UpdateBlob(
        self,
        request: database_pb2.UpdateBlobRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update a blob record in MariaDB."""
        try:
            self.monitor.counters['update_blob'].inc()
            data = BlobData(
                uuid=UUID(request.blob.uuid),
                modified=request.blob.modified,
                fetched_at=request.blob.fetched_at,
                version=request.blob.version
            )
            success = mariadb._direct_update_blob(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database UpdateBlob failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # =========================================================================
    # DnsMasq Operations (MariaDB)
    # =========================================================================

    def CreateDnsMasq(
        self,
        request: database_pb2.CreateDnsMasqRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create a DnsMasq record in MariaDB."""
        try:
            self.monitor.counters['create_dnsmasq'].inc()
            owner_type = ObjectType.from_proto_id(request.dnsmasq.owner_type)
            if owner_type is None:
                owner_type = ObjectType.UNKNOWN
            data = DnsMasqData(
                uuid=UUID(request.dnsmasq.uuid),
                namespace=request.dnsmasq.namespace,
                owner_type=owner_type,
                owner_uuid=UUID(request.dnsmasq.owner_uuid),
                version=request.dnsmasq.version,
                provide_dhcp=request.dnsmasq.provide_dhcp,
                provide_dns=request.dnsmasq.provide_dns
            )
            success = mariadb._direct_create_dnsmasq(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database CreateDnsMasq failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetDnsMasq(
        self,
        request: database_pb2.GetDnsMasqRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetDnsMasqReply:
        """Get DnsMasq static values from MariaDB."""
        try:
            self.monitor.counters['get_dnsmasq'].inc()
            data = mariadb._direct_get_dnsmasq(UUID(request.uuid))
            if data is None:
                return database_pb2.GetDnsMasqReply(found=False)
            return database_pb2.GetDnsMasqReply(
                found=True,
                dnsmasq=database_pb2.DnsMasqData(
                    uuid=str(data.uuid),
                    namespace=data.namespace,
                    owner_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        data.owner_type.proto_id),
                    owner_uuid=str(data.owner_uuid),
                    version=data.version,
                    provide_dhcp=data.provide_dhcp,
                    provide_dns=data.provide_dns
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception('database GetDnsMasq failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetDnsMasqReply(found=False)

    def GetDnsMasqs(
        self,
        request: database_pb2.GetDnsMasqsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetDnsMasqsReply:
        """Get DnsMasq objects from MariaDB with optional filters."""
        try:
            self.monitor.counters['get_dnsmasqs'].inc()
            # Convert empty string to None for optional filters
            namespace = request.namespace if request.namespace else None
            owner_uuid = UUID(request.owner_uuid) if request.owner_uuid else None
            dnsmasqs_data = mariadb._direct_get_dnsmasqs(namespace, owner_uuid)
            dnsmasqs = [
                database_pb2.DnsMasqData(
                    uuid=str(d.uuid),
                    namespace=d.namespace,
                    owner_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        d.owner_type.proto_id),
                    owner_uuid=str(d.owner_uuid),
                    version=d.version,
                    provide_dhcp=d.provide_dhcp,
                    provide_dns=d.provide_dns
                )
                for d in dnsmasqs_data
            ]
            return database_pb2.GetDnsMasqsReply(dnsmasqs=dnsmasqs)
        except Exception as e:
            util_exceptions.ignore_exception('database GetDnsMasqs failed', e)
            return database_pb2.GetDnsMasqsReply(dnsmasqs=[])

    def DeleteDnsMasq(
        self,
        request: database_pb2.DeleteDnsMasqRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a DnsMasq record from MariaDB."""
        try:
            self.monitor.counters['delete_dnsmasq'].inc()
            success = mariadb._direct_delete_dnsmasq(UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database DeleteDnsMasq failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def UpdateDnsMasq(
        self,
        request: database_pb2.UpdateDnsMasqRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update a DnsMasq record in MariaDB."""
        try:
            self.monitor.counters['update_dnsmasq'].inc()
            owner_type = ObjectType.from_proto_id(request.dnsmasq.owner_type)
            if owner_type is None:
                owner_type = ObjectType.UNKNOWN
            data = DnsMasqData(
                uuid=UUID(request.dnsmasq.uuid),
                namespace=request.dnsmasq.namespace,
                owner_type=owner_type,
                owner_uuid=UUID(request.dnsmasq.owner_uuid),
                version=request.dnsmasq.version,
                provide_dhcp=request.dnsmasq.provide_dhcp,
                provide_dns=request.dnsmasq.provide_dns
            )
            success = mariadb._direct_update_dnsmasq(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database UpdateDnsMasq failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # =========================================================================
    # Object Reference Operations (MariaDB)
    # =========================================================================

    def RecordRelationship(
        self,
        request: database_pb2.RecordRelationshipRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Record a relationship between two objects."""
        try:
            self.monitor.counters['record_relationship'].inc()
            source_type = ObjectType.from_proto_id(request.source_type)
            target_type = ObjectType.from_proto_id(request.target_type)
            relationship = RelationshipType.from_proto_id(request.relationship)
            if source_type is None or target_type is None or relationship is None:
                return database_pb2.StatusReply(
                    success=False, error='Invalid type or relationship')
            rel_value = request.relationship_value if request.relationship_value else None
            success = mariadb._direct_record_relationship(
                source_type, request.source_uuid, relationship, rel_value,
                target_type, request.target_uuid)
            if success:
                # Emit structured event for audit trail on both ends of the
                # relationship. add_event_multi automatically adds a
                # correlation_id when there are multiple objects.
                eventlog.add_event_multi(
                    EVENT_TYPE_MUTATE,
                    [(source_type.value, request.source_uuid),
                     (target_type.value, request.target_uuid)],
                    'added reference',
                    extra={
                        'relationship': relationship.value,
                        'relationship_value': rel_value
                    })
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database RecordRelationship failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def RemoveRelationship(
        self,
        request: database_pb2.RemoveRelationshipRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Remove a relationship between two objects."""
        try:
            self.monitor.counters['remove_relationship'].inc()
            source_type = ObjectType.from_proto_id(request.source_type)
            target_type = ObjectType.from_proto_id(request.target_type)
            relationship = RelationshipType.from_proto_id(request.relationship)
            if source_type is None or target_type is None or relationship is None:
                return database_pb2.StatusReply(
                    success=False, error='Invalid type or relationship')
            rel_value = request.relationship_value if request.relationship_value else None
            success = mariadb._direct_remove_relationship(
                source_type, request.source_uuid, relationship, rel_value,
                target_type, request.target_uuid)
            if success:
                # Emit structured event for audit trail on both ends of the
                # relationship. add_event_multi automatically adds a
                # correlation_id when there are multiple objects.
                eventlog.add_event_multi(
                    EVENT_TYPE_MUTATE,
                    [(source_type.value, request.source_uuid),
                     (target_type.value, request.target_uuid)],
                    'removed reference',
                    extra={
                        'relationship': relationship.value,
                        'relationship_value': rel_value
                    })
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database RemoveRelationship failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetReferencesTo(
        self,
        request: database_pb2.GetReferencesToRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetReferencesReply:
        """Get all references to a target object."""
        try:
            self.monitor.counters['get_references_to'].inc()
            target_type = ObjectType.from_proto_id(request.target_type)
            if target_type is None:
                return database_pb2.GetReferencesReply(references=[])
            relationship = None
            if request.HasField('relationship'):
                relationship = RelationshipType.from_proto_id(
                    request.relationship)
            refs = mariadb._direct_get_references_to(
                target_type, request.target_uuid, relationship)
            result = []
            for ref in refs:
                result.append(database_pb2.ObjectReferenceData(
                    source_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        ref.source_object_type.proto_id),
                    source_uuid=str(ref.source_uuid),
                    relationship=cast(
                        shakenfist_enums_pb2.RelationshipType.ValueType,
                        ref.relationship.proto_id),
                    relationship_value=ref.relationship_value or '',
                    target_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        ref.target_object_type.proto_id),
                    target_uuid=str(ref.target_uuid),
                    created=ref.created,
                    last_active=ref.last_active
                ))
            return database_pb2.GetReferencesReply(references=result)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetReferencesTo failed', e)
            return database_pb2.GetReferencesReply(references=[])

    def GetReferencesFrom(
        self,
        request: database_pb2.GetReferencesFromRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetReferencesReply:
        """Get all references from a source object."""
        try:
            self.monitor.counters['get_references_from'].inc()
            source_type = ObjectType.from_proto_id(request.source_type)
            if source_type is None:
                # An unknown or unset object type is a bad request, not a
                # source with no references. Non-retryable, so the client
                # wrapper maps it to None rather than to an empty list.
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(
                    f'unknown object type id {request.source_type}')
                return database_pb2.GetReferencesReply(references=[])
            relationship = None
            if request.HasField('relationship'):
                relationship = RelationshipType.from_proto_id(
                    request.relationship)
            refs = mariadb._direct_get_references_from(
                source_type, request.source_uuid, relationship)
            if refs is None:
                LOG.warning(
                    f'GetReferencesFrom transient MariaDB error for '
                    f'{source_type} {request.source_uuid}')
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                context.set_details('reference read failed')
                return database_pb2.GetReferencesReply(references=[])
            result = []
            for ref in refs:
                result.append(database_pb2.ObjectReferenceData(
                    source_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        ref.source_object_type.proto_id),
                    source_uuid=str(ref.source_uuid),
                    relationship=cast(
                        shakenfist_enums_pb2.RelationshipType.ValueType,
                        ref.relationship.proto_id),
                    relationship_value=ref.relationship_value or '',
                    target_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        ref.target_object_type.proto_id),
                    target_uuid=str(ref.target_uuid),
                    created=ref.created,
                    last_active=ref.last_active
                ))
            return database_pb2.GetReferencesReply(references=result)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetReferencesFrom failed', e)
            # INTERNAL rather than UNAVAILABLE: an unexpected exception in
            # this handler is a bug, not a transient outage, and retrying
            # it will not help.
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetReferencesReply(references=[])

    def CountReferencesTo(
        self,
        request: database_pb2.CountReferencesToRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.CountReply:
        """Count references to a target object."""
        try:
            self.monitor.counters['count_references_to'].inc()
            target_type = ObjectType.from_proto_id(request.target_type)
            if target_type is None:
                return database_pb2.CountReply(count=0)
            exclude_relationships: list[RelationshipType] | None = None
            if request.exclude_relationships:
                converted = []
                for r in request.exclude_relationships:
                    rel = RelationshipType.from_proto_id(r)
                    if rel is not None:
                        converted.append(rel)
                exclude_relationships = converted if converted else None
            count = mariadb._direct_count_references_to(
                target_type, request.target_uuid, exclude_relationships)
            return database_pb2.CountReply(count=count)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CountReferencesTo failed', e)
            return database_pb2.CountReply(count=0)

    def RemoveAllReferencesFrom(
        self,
        request: database_pb2.RemoveAllReferencesFromRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.CountReply:
        """Remove all references from a source object."""
        try:
            self.monitor.counters['remove_all_references_from'].inc()
            source_type = ObjectType.from_proto_id(request.source_type)
            if source_type is None:
                return database_pb2.CountReply(count=0)
            # Optional relationship filter (0 = UNSPECIFIED means all)
            relationship = None
            if request.relationship:
                relationship = RelationshipType.from_proto_id(
                    request.relationship)
            count = mariadb._direct_remove_all_references_from(
                source_type, request.source_uuid, relationship)
            if count > 0:
                # Emit structured event for audit trail
                eventlog.add_event(
                    EVENT_TYPE_MUTATE,
                    source_type.value, request.source_uuid,
                    'removed all references',
                    extra={
                        'relationship': relationship.value if relationship else 'all',
                        'count': count
                    })
            return database_pb2.CountReply(count=count)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database RemoveAllReferencesFrom failed', e)
            return database_pb2.CountReply(count=0)

    def UpdateLastActive(
        self,
        request: database_pb2.UpdateLastActiveRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update the last_active timestamp of a relationship."""
        try:
            self.monitor.counters['update_last_active'].inc()
            source_type = ObjectType.from_proto_id(request.source_type)
            target_type = ObjectType.from_proto_id(request.target_type)
            relationship = RelationshipType.from_proto_id(request.relationship)
            if source_type is None or target_type is None or relationship is None:
                return database_pb2.StatusReply(
                    success=False, error='Invalid type or relationship')
            rel_value = request.relationship_value if request.relationship_value else None
            success = mariadb._direct_update_last_active(
                source_type, request.source_uuid, relationship, rel_value,
                target_type, request.target_uuid)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateLastActive failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetStaleReferences(
        self,
        request: database_pb2.GetStaleReferencesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetReferencesReply:
        """Get references with last_active older than specified time."""
        try:
            self.monitor.counters['get_stale_references'].inc()
            refs = mariadb._direct_get_stale_references(request.older_than)
            result = []
            for ref in refs:
                result.append(database_pb2.ObjectReferenceData(
                    source_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        ref.source_object_type.proto_id),
                    source_uuid=str(ref.source_uuid),
                    relationship=cast(
                        shakenfist_enums_pb2.RelationshipType.ValueType,
                        ref.relationship.proto_id),
                    relationship_value=ref.relationship_value or '',
                    target_type=cast(
                        shakenfist_enums_pb2.ObjectType.ValueType,
                        ref.target_object_type.proto_id),
                    target_uuid=str(ref.target_uuid),
                    created=ref.created,
                    last_active=ref.last_active
                ))
            return database_pb2.GetReferencesReply(references=result)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetStaleReferences failed', e)
            return database_pb2.GetReferencesReply(references=[])

    # =========================================================================
    # Blob Hash Operations
    # =========================================================================

    def UpsertBlobHash(
        self,
        request: database_pb2.UpsertBlobHashRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Upsert a blob hash record."""
        try:
            self.monitor.counters['upsert_blob_hash'].inc()
            from shakenfist.schema.blob_hash import BlobHash
            blob_hash = BlobHash(
                blob_uuid=request.blob_hash.blob_uuid,
                node=request.blob_hash.node,
                algorithm=request.blob_hash.algorithm,
                hash_value=request.blob_hash.hash_value,
                file_size=request.blob_hash.file_size,
                computed_at=request.blob_hash.computed_at,
                last_verified_at=request.blob_hash.last_verified_at,
                verification_status=request.blob_hash.verification_status,
                error_message=request.blob_hash.error_message or None
            )
            success = mariadb._direct_upsert_blob_hash(blob_hash)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpsertBlobHash failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetBlobHashes(
        self,
        request: database_pb2.GetBlobHashesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetBlobHashesReply:
        """Get all hash records for a blob."""
        try:
            self.monitor.counters['get_blob_hashes'].inc()
            node = request.node if request.HasField('node') else None
            hashes = mariadb._direct_get_blob_hashes(request.blob_uuid, node)
            result = []
            for h in hashes:
                result.append(database_pb2.BlobHashData(
                    blob_uuid=h.blob_uuid,
                    node=h.node,
                    algorithm=h.algorithm,
                    hash_value=h.hash_value,
                    file_size=h.file_size,
                    computed_at=h.computed_at,
                    last_verified_at=h.last_verified_at,
                    verification_status=h.verification_status,
                    error_message=h.error_message or ''
                ))
            return database_pb2.GetBlobHashesReply(hashes=result)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetBlobHashes failed', e)
            return database_pb2.GetBlobHashesReply(hashes=[])

    def FindBlobByHash(
        self,
        request: database_pb2.FindBlobByHashRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.FindBlobByHashReply:
        """Find a blob UUID by hash value."""
        try:
            self.monitor.counters['find_blob_by_hash'].inc()
            blob_uuid = mariadb._direct_find_blob_by_hash(
                request.algorithm, request.hash_value)
            if blob_uuid:
                return database_pb2.FindBlobByHashReply(
                    found=True, blob_uuid=blob_uuid)
            return database_pb2.FindBlobByHashReply(found=False, blob_uuid='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database FindBlobByHash failed', e)
            return database_pb2.FindBlobByHashReply(found=False, blob_uuid='')

    def GetStaleBlobHashes(
        self,
        request: database_pb2.GetStaleBlobHashesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetBlobHashesReply:
        """Get blob hashes with last_verified_at older than specified time."""
        try:
            self.monitor.counters['get_stale_blob_hashes'].inc()
            hashes = mariadb._direct_get_stale_blob_hashes(request.older_than)
            result = []
            for h in hashes:
                result.append(database_pb2.BlobHashData(
                    blob_uuid=h.blob_uuid,
                    node=h.node,
                    algorithm=h.algorithm,
                    hash_value=h.hash_value,
                    file_size=h.file_size,
                    computed_at=h.computed_at,
                    last_verified_at=h.last_verified_at,
                    verification_status=h.verification_status,
                    error_message=h.error_message or ''
                ))
            return database_pb2.GetBlobHashesReply(hashes=result)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetStaleBlobHashes failed', e)
            return database_pb2.GetBlobHashesReply(hashes=[])

    def DeleteBlobHashes(
        self,
        request: database_pb2.DeleteBlobHashesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete all hash records for a blob."""
        try:
            self.monitor.counters['delete_blob_hashes'].inc()
            success = mariadb._direct_delete_blob_hashes(request.blob_uuid)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteBlobHashes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # =========================================================================
    # Blob Transfer Operations
    # =========================================================================

    def CreateBlobTransfer(
        self,
        request: database_pb2.CreateBlobTransferRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create a blob transfer record."""
        try:
            self.monitor.counters['create_blob_transfer'].inc()
            from shakenfist.schema.blob_transfer import BlobTransfer
            transfer = BlobTransfer(
                source_node=request.transfer.source_node,
                transfer_name=request.transfer.transfer_name,
                requesting_node=request.transfer.requesting_node,
                blob_uuid=request.transfer.blob_uuid,
                token=request.transfer.token,
                server_state=request.transfer.server_state,
                port=request.transfer.port if request.transfer.port else None,
                percentage=request.transfer.percentage,
                created_at=request.transfer.created_at,
                updated_at=request.transfer.updated_at
            )
            success = mariadb._direct_create_blob_transfer(transfer)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateBlobTransfer failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetBlobTransfer(
        self,
        request: database_pb2.GetBlobTransferRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetBlobTransferReply:
        """Get a specific blob transfer record."""
        try:
            self.monitor.counters['get_blob_transfer'].inc()
            transfer = mariadb._direct_get_blob_transfer(
                request.source_node, request.transfer_name)
            if transfer is None:
                return database_pb2.GetBlobTransferReply(found=False)
            return database_pb2.GetBlobTransferReply(
                found=True,
                transfer=database_pb2.BlobTransferData(
                    source_node=transfer.source_node,
                    transfer_name=transfer.transfer_name,
                    requesting_node=transfer.requesting_node,
                    blob_uuid=transfer.blob_uuid,
                    token=transfer.token,
                    server_state=transfer.server_state,
                    port=transfer.port if transfer.port else 0,
                    percentage=transfer.percentage,
                    created_at=transfer.created_at,
                    updated_at=transfer.updated_at
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetBlobTransfer failed', e)
            return database_pb2.GetBlobTransferReply(found=False)

    def GetBlobTransfersForNode(
        self,
        request: database_pb2.GetBlobTransfersForNodeRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetBlobTransfersReply:
        """Get all blob transfers for a source node."""
        try:
            self.monitor.counters['get_blob_transfers_for_node'].inc()
            transfers = mariadb._direct_get_blob_transfers_for_node(
                request.source_node)
            result = []
            for t in transfers:
                result.append(database_pb2.BlobTransferData(
                    source_node=t.source_node,
                    transfer_name=t.transfer_name,
                    requesting_node=t.requesting_node,
                    blob_uuid=t.blob_uuid,
                    token=t.token,
                    server_state=t.server_state,
                    port=t.port if t.port else 0,
                    percentage=t.percentage,
                    created_at=t.created_at,
                    updated_at=t.updated_at
                ))
            return database_pb2.GetBlobTransfersReply(transfers=result)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetBlobTransfersForNode failed', e)
            return database_pb2.GetBlobTransfersReply(transfers=[])

    def GetBlobTransfersForBlob(
        self,
        request: database_pb2.GetBlobTransfersForBlobRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetBlobTransfersReply:
        """Get all blob transfers for a blob."""
        try:
            self.monitor.counters['get_blob_transfers_for_blob'].inc()
            transfers = mariadb._direct_get_blob_transfers_for_blob(
                request.blob_uuid)
            result = []
            for t in transfers:
                result.append(database_pb2.BlobTransferData(
                    source_node=t.source_node,
                    transfer_name=t.transfer_name,
                    requesting_node=t.requesting_node,
                    blob_uuid=t.blob_uuid,
                    token=t.token,
                    server_state=t.server_state,
                    port=t.port if t.port else 0,
                    percentage=t.percentage,
                    created_at=t.created_at,
                    updated_at=t.updated_at
                ))
            return database_pb2.GetBlobTransfersReply(transfers=result)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetBlobTransfersForBlob failed', e)
            return database_pb2.GetBlobTransfersReply(transfers=[])

    def UpdateBlobTransfer(
        self,
        request: database_pb2.UpdateBlobTransferRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update a blob transfer record."""
        try:
            self.monitor.counters['update_blob_transfer'].inc()
            # Extract optional fields only if they are set
            server_state = (request.server_state if
                            request.HasField('server_state') else None)
            port = request.port if request.HasField('port') else None
            percentage = (request.percentage if
                          request.HasField('percentage') else None)

            success = mariadb._direct_update_blob_transfer(
                request.source_node,
                request.transfer_name,
                server_state=server_state,
                port=port,
                percentage=percentage
            )
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateBlobTransfer failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteBlobTransfer(
        self,
        request: database_pb2.DeleteBlobTransferRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a blob transfer record."""
        try:
            self.monitor.counters['delete_blob_transfer'].inc()
            success = mariadb._direct_delete_blob_transfer(
                request.source_node, request.transfer_name)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteBlobTransfer failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteStaleTransfers(
        self,
        request: database_pb2.DeleteStaleTransfersRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteCountReply:
        """Delete stale transfers."""
        try:
            self.monitor.counters['delete_stale_transfers'].inc()
            count = mariadb._direct_delete_stale_transfers(request.older_than)
            return database_pb2.DeleteCountReply(count=count)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteStaleTransfers failed', e)
            return database_pb2.DeleteCountReply(count=0)

    def DeleteBlobTransfersForBlob(
        self,
        request: database_pb2.DeleteBlobTransfersForBlobRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteCountReply:
        """Delete all transfers for a blob."""
        try:
            self.monitor.counters['delete_blob_transfers_for_blob'].inc()
            count = mariadb._direct_delete_blob_transfers_for_blob(
                request.blob_uuid)
            return database_pb2.DeleteCountReply(count=count)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteBlobTransfersForBlob failed', e)
            return database_pb2.DeleteCountReply(count=-1)

    # Blob Attributes Operations (MariaDB)

    def CreateBlobAttributes(
        self,
        request: database_pb2.CreateBlobAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create blob attributes in MariaDB."""
        try:
            self.monitor.counters['create_blob_attributes'].inc()
            data = BlobAttributesData(
                uuid=UUID(request.data.uuid),
                size=request.data.size,
                info=(json.loads(request.data.info_json)
                      if request.data.info_json else {}),
                last_used=(request.data.last_used
                           if request.data.has_last_used else None),
                expires_at=request.data.expires_at
            )
            success = mariadb._direct_create_blob_attributes(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateBlobAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetBlobAttributes(
        self,
        request: database_pb2.GetBlobAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetBlobAttributesReply:
        """Get blob attributes from MariaDB."""
        try:
            self.monitor.counters['get_blob_attributes'].inc()
            data = mariadb._direct_get_blob_attributes(UUID(request.uuid))
            if data is None:
                return database_pb2.GetBlobAttributesReply(found=False)
            return database_pb2.GetBlobAttributesReply(
                found=True,
                data=database_pb2.BlobAttributesData(
                    uuid=str(data.uuid),
                    size=data.size,
                    info_json=json.dumps(data.info) if data.info else '{}',
                    last_used=data.last_used if data.last_used else 0,
                    has_last_used=data.last_used is not None,
                    expires_at=data.expires_at
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetBlobAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetBlobAttributesReply(found=False)

    def UpdateBlobAttributes(
        self,
        request: database_pb2.UpdateBlobAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update blob attributes in MariaDB."""
        try:
            self.monitor.counters['update_blob_attributes'].inc()
            data = BlobAttributesData(
                uuid=UUID(request.data.uuid),
                size=request.data.size,
                info=(json.loads(request.data.info_json)
                      if request.data.info_json else {}),
                last_used=(request.data.last_used
                           if request.data.has_last_used else None),
                expires_at=request.data.expires_at
            )
            success = mariadb._direct_update_blob_attributes(
                data, fields=list(request.fields))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateBlobAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def UpdateBlobLastUsed(
        self,
        request: database_pb2.UpdateBlobLastUsedRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update blob last_used in MariaDB (optimized single-column update)."""
        try:
            self.monitor.counters['update_blob_last_used'].inc()
            success = mariadb._direct_update_blob_last_used(
                UUID(request.uuid), request.last_used)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateBlobLastUsed failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteBlobAttributes(
        self,
        request: database_pb2.DeleteBlobAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete blob attributes from MariaDB."""
        try:
            self.monitor.counters['delete_blob_attributes'].inc()
            success = mariadb._direct_delete_blob_attributes(
                UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteBlobAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetExpiredBlobUuids(
        self,
        request: database_pb2.GetExpiredBlobUuidsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetExpiredBlobUuidsReply:
        """Get UUIDs of expired blobs."""
        try:
            self.monitor.counters['get_expired_blob_uuids'].inc()
            current_time = (request.current_time
                            if request.current_time > 0 else None)
            uuids = mariadb._direct_get_expired_blob_uuids(current_time)
            return database_pb2.GetExpiredBlobUuidsReply(uuids=uuids)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetExpiredBlobUuids failed', e)
            return database_pb2.GetExpiredBlobUuidsReply(uuids=[])

    def GetStaleTranscodedBlobUuids(
        self,
        request: database_pb2.GetStaleTranscodedBlobUuidsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetStaleTranscodedBlobUuidsReply:
        """Get UUIDs of stale transcoded blobs."""
        try:
            self.monitor.counters['get_stale_transcoded_blob_uuids'].inc()
            uuids = mariadb._direct_get_stale_transcoded_blob_uuids(
                request.idle_seconds)
            return database_pb2.GetStaleTranscodedBlobUuidsReply(uuids=uuids)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetStaleTranscodedBlobUuids failed', e)
            return database_pb2.GetStaleTranscodedBlobUuidsReply(uuids=[])

    # =====================================================================
    # Node Operations (MariaDB)
    # =====================================================================

    def CreateNode(
        self,
        request: database_pb2.CreateNodeRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create a node record in MariaDB."""
        try:
            self.monitor.counters['create_node'].inc()
            success = mariadb._direct_create_node(
                UUID(request.node.uuid),
                request.node.fqdn,
                request.node.ip,
                request.node.version
            )
            return database_pb2.StatusReply(
                success=success, error=''
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateNode failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e)
            )

    def GetNode(
        self,
        request: database_pb2.GetNodeRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNodeReply:
        """Get node static values from MariaDB."""
        try:
            self.monitor.counters['get_node'].inc()
            data = mariadb._direct_get_node(
                UUID(request.uuid)
            )
            if data is None:
                return database_pb2.GetNodeReply(found=False)
            return database_pb2.GetNodeReply(
                found=True,
                node=database_pb2.NodeStaticData(
                    uuid=str(data.uuid),
                    fqdn=data.fqdn,
                    ip=data.ip,
                    version=data.version
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNode failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNodeReply(found=False)

    def GetNodeByFqdn(
        self,
        request: database_pb2.GetNodeByFqdnRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNodeReply:
        """Get node static values by FQDN from MariaDB."""
        try:
            self.monitor.counters['get_node_by_fqdn'].inc()
            data = mariadb._direct_get_node_by_fqdn(
                request.fqdn
            )
            if data is None:
                return database_pb2.GetNodeReply(found=False)
            return database_pb2.GetNodeReply(
                found=True,
                node=database_pb2.NodeStaticData(
                    uuid=str(data.uuid),
                    fqdn=data.fqdn,
                    ip=data.ip,
                    version=data.version
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNodeByFqdn failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNodeReply(found=False)

    def GetAllNodeUuids(
        self,
        request: database_pb2.GetAllNodeUuidsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllNodeUuidsReply:
        """Get all node UUIDs from MariaDB."""
        try:
            self.monitor.counters['get_all_node_uuids'].inc()
            uuids = mariadb._direct_get_all_node_uuids()
            return database_pb2.GetAllNodeUuidsReply(
                uuids=uuids
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllNodeUuids failed', e)
            return database_pb2.GetAllNodeUuidsReply(uuids=[])

    def DeleteNode(
        self,
        request: database_pb2.DeleteNodeRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a node record from MariaDB."""
        try:
            self.monitor.counters['delete_node'].inc()
            success = mariadb._direct_delete_node(
                UUID(request.uuid)
            )
            return database_pb2.StatusReply(
                success=success, error=''
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNode failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e)
            )

    def UpdateNode(
        self,
        request: database_pb2.UpdateNodeRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update a node record in MariaDB."""
        try:
            self.monitor.counters['update_node'].inc()
            data = NodeData(
                uuid=UUID(request.node.uuid),
                fqdn=request.node.fqdn,
                ip=request.node.ip,
                version=request.node.version
            )
            success = mariadb._direct_update_node(data)
            return database_pb2.StatusReply(
                success=success, error=''
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateNode failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e)
            )

    # =====================================================================
    # Node Attributes Operations (MariaDB)
    # =====================================================================

    def _node_attrs_from_proto(
        self,
        d: database_pb2.NodeAttributesProto
    ) -> NodeAttributesData:
        """Convert a proto NodeAttributesProto to a Pydantic model."""
        return NodeAttributesData(
            uuid=UUID(d.uuid),
            last_seen=d.last_seen,
            installed_version=(
                d.installed_version
                if d.has_installed_version else None
            ),
            spice_server_cert_subject=(
                d.spice_server_cert_subject
                if d.has_spice_server_cert_subject else None
            ),
            is_etcd_master=d.is_etcd_master,
            is_hypervisor=d.is_hypervisor,
            is_network_node=d.is_network_node,
            is_eventlog_node=d.is_eventlog_node,
            is_database_node=d.is_database_node,
            daemons=(
                json.loads(d.daemons_json)
                if d.daemons_json else []
            ),
            daemon_states=(
                json.loads(d.daemon_states_json)
                if d.daemon_states_json else {}
            ),
            qemu_version=(
                json.loads(d.qemu_version_json)
                if d.has_qemu_version else None
            ),
            libvirt_version=(
                json.loads(d.libvirt_version_json)
                if d.has_libvirt_version else None
            ),
            python_version=(
                json.loads(d.python_version_json)
                if d.has_python_version else None
            ),
            python_implementation=(
                d.python_implementation
                if d.has_python_implementation else None
            ),
            dependency_versions=(
                json.loads(d.dependency_versions_json)
                if d.dependency_versions_json else {}
            ),
            process_metrics=(
                json.loads(d.process_metrics_json)
                if d.process_metrics_json else {}
            ),
        )

    def _node_attrs_to_proto(
        self,
        data: NodeAttributesData
    ) -> database_pb2.NodeAttributesProto:
        """Convert a Pydantic NodeAttributesData to proto."""
        return database_pb2.NodeAttributesProto(
            uuid=str(data.uuid),
            last_seen=data.last_seen,
            installed_version=(
                data.installed_version or ''
            ),
            has_installed_version=(
                data.installed_version is not None
            ),
            spice_server_cert_subject=(
                data.spice_server_cert_subject or ''
            ),
            has_spice_server_cert_subject=(
                data.spice_server_cert_subject is not None
            ),
            is_etcd_master=data.is_etcd_master,
            is_hypervisor=data.is_hypervisor,
            is_network_node=data.is_network_node,
            is_eventlog_node=data.is_eventlog_node,
            is_database_node=data.is_database_node,
            daemons_json=json.dumps(data.daemons),
            daemon_states_json=json.dumps(data.daemon_states),
            qemu_version_json=(
                json.dumps(data.qemu_version)
                if data.qemu_version is not None else ''
            ),
            has_qemu_version=(
                data.qemu_version is not None
            ),
            libvirt_version_json=(
                json.dumps(data.libvirt_version)
                if data.libvirt_version is not None else ''
            ),
            has_libvirt_version=(
                data.libvirt_version is not None
            ),
            python_version_json=(
                json.dumps(data.python_version)
                if data.python_version is not None else ''
            ),
            has_python_version=(
                data.python_version is not None
            ),
            python_implementation=(
                data.python_implementation or ''
            ),
            has_python_implementation=(
                data.python_implementation is not None
            ),
            dependency_versions_json=(
                json.dumps(data.dependency_versions)
            ),
            process_metrics_json=(
                json.dumps(data.process_metrics)
            ),
        )

    def CreateNodeAttributes(
        self,
        request: database_pb2.CreateNodeAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create node attributes in MariaDB."""
        try:
            self.monitor.counters['create_node_attributes'].inc()
            data = self._node_attrs_from_proto(request.data)
            success = mariadb._direct_create_node_attributes(
                data
            )
            return database_pb2.StatusReply(
                success=success, error=''
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateNodeAttributes failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e)
            )

    def GetNodeAttributes(
        self,
        request: database_pb2.GetNodeAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNodeAttributesReply:
        """Get node attributes from MariaDB."""
        try:
            self.monitor.counters['get_node_attributes'].inc()
            data = mariadb._direct_get_node_attributes(
                UUID(request.uuid)
            )
            if data is None:
                return database_pb2.GetNodeAttributesReply(
                    found=False
                )
            return database_pb2.GetNodeAttributesReply(
                found=True,
                data=self._node_attrs_to_proto(data)
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNodeAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNodeAttributesReply(
                found=False
            )

    def UpdateNodeAttributes(
        self,
        request: database_pb2.UpdateNodeAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update node attributes in MariaDB."""
        try:
            self.monitor.counters['update_node_attributes'].inc()
            data = self._node_attrs_from_proto(request.data)
            success = mariadb._direct_update_node_attributes(
                data, fields=list(request.fields)
            )
            return database_pb2.StatusReply(
                success=success, error=''
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateNodeAttributes failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e)
            )

    def DeleteNodeAttributes(
        self,
        request: database_pb2.DeleteNodeAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete node attributes from MariaDB."""
        try:
            self.monitor.counters['delete_node_attributes'].inc()
            success = mariadb._direct_delete_node_attributes(
                UUID(request.uuid)
            )
            return database_pb2.StatusReply(
                success=success, error=''
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNodeAttributes failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e)
            )

    # =========================================================
    # Node Daemon State Operations (MariaDB)
    # =========================================================

    def SetNodeDaemonState(
        self,
        request: database_pb2.SetNodeDaemonStateRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Atomically upsert one (node, daemon) state row."""
        try:
            self.monitor.counters['set_node_daemon_state'].inc()
            d = request.data
            success = mariadb._direct_set_node_daemon_state(
                UUID(d.node_uuid),
                d.daemon,
                d.value if d.value else None,
                d.update_time,
                d.message if d.message else None,
            )
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database SetNodeDaemonState failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetNodeDaemonState(
        self,
        request: database_pb2.GetNodeDaemonStateRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNodeDaemonStateReply:
        """Get one (node, daemon) state row."""
        try:
            self.monitor.counters['get_node_daemon_state'].inc()
            # Do not start a read nobody is waiting for. This is by a wide
            # margin our highest frequency RPC: every daemon on every node
            # polls its own row every DAEMON_STATE_POLL_INTERVAL seconds
            # with a BOUNDED_QUERY_TIMEOUT deadline, so it is the only call
            # whose caller re-issues several times over within its own
            # deadline. When MariaDB slows, those re-issues arrive faster
            # than the worker pool retires them and the queue fills with
            # already-expired polls, each of which used to run a full
            # database read on arrival -- so the pool never catches up and
            # every subsequent poll times out too, sustaining the burst
            # long after the original stall (issue 3607). Dropping an
            # expired call costs microseconds instead, which lets the
            # backlog drain. A caller that is somehow still waiting sees
            # DEADLINE_EXCEEDED, which _grpc_call already treats as
            # retryable, rather than a found=False that reads as "no row".
            remaining = context.time_remaining()
            if isinstance(remaining, (int, float)) and remaining <= 0:
                context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
                context.set_details(
                    'caller deadline expired before the read started')
                return database_pb2.GetNodeDaemonStateReply(found=False)
            row = mariadb._direct_get_node_daemon_state(
                UUID(request.node_uuid), request.daemon)
            if row is None:
                return database_pb2.GetNodeDaemonStateReply(found=False)
            return database_pb2.GetNodeDaemonStateReply(
                found=True,
                data=database_pb2.NodeDaemonStateData(
                    node_uuid=str(row.node_uuid),
                    daemon=row.daemon,
                    value=row.value or '',
                    update_time=row.update_time,
                    message=row.message or '',
                ),
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNodeDaemonState failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNodeDaemonStateReply(found=False)

    def GetAllNodeDaemonStates(
        self,
        request: database_pb2.GetAllNodeDaemonStatesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllNodeDaemonStatesReply:
        """Get every daemon state row for one node."""
        try:
            self.monitor.counters['get_all_node_daemon_states'].inc()
            rows = mariadb._direct_get_all_node_daemon_states(
                UUID(request.node_uuid))
            if rows is None:
                rows = []
            return database_pb2.GetAllNodeDaemonStatesReply(
                data=[
                    database_pb2.NodeDaemonStateData(
                        node_uuid=str(r.node_uuid),
                        daemon=r.daemon,
                        value=r.value or '',
                        update_time=r.update_time,
                        message=r.message or '',
                    )
                    for r in rows
                ],
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllNodeDaemonStates failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetAllNodeDaemonStatesReply()

    def DeleteNodeDaemonState(
        self,
        request: database_pb2.DeleteNodeDaemonStateRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete one (node, daemon) state row."""
        try:
            self.monitor.counters['delete_node_daemon_state'].inc()
            success = mariadb._direct_delete_node_daemon_state(
                UUID(request.node_uuid), request.daemon)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNodeDaemonState failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # =========================================================
    # Scheduler Capacity Operations (MariaDB)
    # =========================================================

    def ReconcileSchedulerCapacity(
        self,
        request: database_pb2.ReconcileSchedulerCapacityRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ReconcileSchedulerCapacityReply:
        """Run one scheduler capacity reconcile pass (phase 2, D5).

        The client calls with a bounded deadline, so check the call is
        still alive before starting what is deliberately one long
        analytical query: a caller that has already given up should not
        have a pass run on its behalf. Overlapping passes are benign in
        this release regardless -- the reconciler is the sole writer and
        every statement is an idempotent recompute from ground truth --
        but that stops being true in phase 3, when guarded UPDATEs give
        these counters a second writer, so do not lean on it.
        """
        try:
            self.monitor.counters['reconcile_scheduler_capacity'].inc()
            if not context.is_active():
                LOG.info('Skipping scheduler capacity reconcile pass: the '
                         'caller has already abandoned the call')
                return database_pb2.ReconcileSchedulerCapacityReply(
                    success=False)
            result = mariadb._direct_reconcile_scheduler_capacity(
                request.demand_per_vcpu, request.demand_decay_seconds,
                request.disk_overcommit)
            if result is None:
                return database_pb2.ReconcileSchedulerCapacityReply(
                    success=False)
            reply = database_pb2.ReconcileSchedulerCapacityReply(
                success=True,
                nodes_added=result['nodes_added'],
                nodes_removed=result['nodes_removed'],
                claims_expired=result['claims_expired'])
            for node in result['nodes']:
                reply.nodes.add(**node)
            reply.cluster.CopyFrom(
                database_pb2.ClusterCapacity(**result['cluster']))
            return reply
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ReconcileSchedulerCapacity failed', e)
            return database_pb2.ReconcileSchedulerCapacityReply(
                success=False)

    def AdmitInstancePlacement(
        self,
        request: database_pb2.AdmitInstancePlacementRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.AdmitInstancePlacementReply:
        """Draw down capacity and write a placement, atomically (phase 3).

        The whole point of this RPC is that the guarded capacity
        drawdown, the placement attribute write and the placement
        reference rewrite are one MariaDB transaction, so there is no
        Python-side composition to fall back to and nothing here to do
        but hand the request to the direct implementation.

        An unexpected exception is a denial-shaped reply with
        ``success=False``: a caller that read only ``admitted`` would
        walk to the next candidate, which is the safe direction, while
        one that checks ``success`` can tell a database problem from a
        full cluster.
        """
        try:
            self.monitor.counters['admit_instance_placement'].inc()
            result = mariadb._direct_admit_instance_placement(
                request.instance_uuid, request.namespace, request.node_uuid,
                request.old_node_uuid, request.cpus, request.memory_mb,
                request.disk_gb, request.demand_add, request.target_load,
                request.enforce, request.placement_json)
            if result['unguarded']:
                # P7 fail-open. Counted separately so the soak can tell a
                # guard which is passing everything from a guard which is
                # not evaluating at all.
                self.monitor.counters[
                    'admit_instance_placement_unguarded'].inc()
            reply = database_pb2.AdmitInstancePlacementReply(
                success=result['success'],
                error=result['error'],
                admitted=result['admitted'],
                unguarded=result['unguarded'],
                clamped=result['clamped'],
                failing_stage=result['failing_stage'],
                node_used_cpus=result['node_used_cpus'],
                node_used_memory_mb=result['node_used_memory_mb'],
                node_used_disk_gb=result['node_used_disk_gb'],
                node_expected_demand=result['node_expected_demand'],
                claim_over_limit=result['claim_over_limit'])
            for dimension in result['dimensions']:
                reply.dimensions.add(**dimension)
            # The advisory over-limit detail travels in its own repeated
            # field: dimensions means "why this was refused" and an
            # over-claim placement was admitted (D5/D16).
            for dimension in result['claim_dimensions']:
                reply.claim_dimensions.add(**dimension)
            return reply
        except Exception as e:
            util_exceptions.ignore_exception(
                'database AdmitInstancePlacement failed', e)
            return database_pb2.AdmitInstancePlacementReply(
                success=False, error=str(e))

    def ReleaseInstancePlacement(
        self,
        request: database_pb2.ReleaseInstancePlacementRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ReleaseInstancePlacementReply:
        """Give an instance's capacity back and drop its placement rows."""
        try:
            self.monitor.counters['release_instance_placement'].inc()
            result = mariadb._direct_release_instance_placement(
                request.instance_uuid, request.namespace, request.node_uuid,
                request.cpus, request.memory_mb, request.disk_gb)
            return database_pb2.ReleaseInstancePlacementReply(
                success=result['success'],
                error=result['error'],
                released=result['released'],
                clamped=result['clamped'])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ReleaseInstancePlacement failed', e)
            return database_pb2.ReleaseInstancePlacementReply(
                success=False, error=str(e))

    def GetSchedulerNodeCapacity(
        self,
        request: database_pb2.GetSchedulerNodeCapacityRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetSchedulerNodeCapacityReply:
        """Read the materialised per-node capacity counters (phase 3).

        A read of a small table with no filtering, so an error is an
        empty reply: the caller is an admin summary which reports a
        node with no row as uncounted rather than as full.
        """
        try:
            self.monitor.counters['get_scheduler_node_capacity'].inc()
            reply = database_pb2.GetSchedulerNodeCapacityReply()
            for row in mariadb._direct_get_scheduler_node_capacity():
                reply.rows.add(**row)
            return reply
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetSchedulerNodeCapacity failed', e)
            return database_pb2.GetSchedulerNodeCapacityReply()

    # =========================================================
    # Namespace Claim CRUD (MariaDB)
    #
    # Scheduler-reservations phase 4. Creating and growing a claim are
    # guarded admission decisions against the cluster_capacity
    # singleton, so like the placement primitive above there is no
    # Python-side composition to fall back to: each of these is one
    # MariaDB transaction and nothing here does more than hand the
    # request to the direct implementation and shape the reply.
    # =========================================================

    def _claim_message(
        self, claim: mariadb.NamespaceClaimRow
    ) -> database_pb2.NamespaceClaim:
        """Build the wire form of one claim row."""
        return database_pb2.NamespaceClaim(**claim)

    def CreateNamespaceClaim(
        self,
        request: database_pb2.CreateNamespaceClaimRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.CreateNamespaceClaimReply:
        """Create a namespace claim, guarded against the cluster (D3/D14).

        An unexpected exception is a refusal-shaped reply with
        ``success=False``: a caller that read only ``created`` would
        report the claim as not made, which is the safe direction, while
        one that checks ``success`` can tell a database problem from a
        full cluster.
        """
        try:
            self.monitor.counters['create_namespace_claim'].inc()
            result = mariadb._direct_create_namespace_claim(
                request.uuid, request.namespace, request.limit_cpus,
                request.limit_memory_mb, request.limit_disk_gb,
                request.expires_in_seconds)
            reply = database_pb2.CreateNamespaceClaimReply(
                success=result['success'],
                error=result['error'],
                created=result['created'],
                refused_reason=result['refused_reason'])
            for dimension in result['dimensions']:
                reply.dimensions.add(**dimension)
            if result['claim'] is not None:
                reply.claim.CopyFrom(self._claim_message(result['claim']))
            return reply
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateNamespaceClaim failed', e)
            return database_pb2.CreateNamespaceClaimReply(
                success=False, error=str(e))

    def GetNamespaceClaim(
        self,
        request: database_pb2.GetNamespaceClaimRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNamespaceClaimReply:
        """Read one claim row by uuid."""
        try:
            self.monitor.counters['get_namespace_claim'].inc()
            claim = mariadb._direct_get_namespace_claim(request.uuid)
            if claim is None:
                return database_pb2.GetNamespaceClaimReply(found=False)
            return database_pb2.GetNamespaceClaimReply(
                found=True, claim=self._claim_message(claim))
        except Exception as e:
            # found=False on its own tells the client "no such claim",
            # which is a lie a claim caller acts on destructively -- see
            # _direct_get_namespace_claims(). The status code is what
            # makes _grpc_get_namespace_claim() raise instead of
            # returning a well formed absence.
            util_exceptions.ignore_exception(
                'database GetNamespaceClaim failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNamespaceClaimReply(found=False)

    def GetNamespaceClaims(
        self,
        request: database_pb2.GetNamespaceClaimsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNamespaceClaimsReply:
        """List claim rows, optionally restricted to one namespace."""
        try:
            self.monitor.counters['get_namespace_claims'].inc()
            reply = database_pb2.GetNamespaceClaimsReply()
            for claim in mariadb._direct_get_namespace_claims(
                    request.namespace):
                reply.claims.append(self._claim_message(claim))
            return reply
        except Exception as e:
            # An empty reply is indistinguishable from "this namespace
            # holds no claims", and Namespace.hard_delete() acts on that
            # by removing the namespace anyway. Setting the code is what
            # makes _grpc_get_namespace_claims() raise.
            util_exceptions.ignore_exception(
                'database GetNamespaceClaims failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNamespaceClaimsReply()

    def UpdateNamespaceClaim(
        self,
        request: database_pb2.UpdateNamespaceClaimRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.UpdateNamespaceClaimReply:
        """Grow, shrink or re-date a claim in one transaction (D8)."""
        try:
            self.monitor.counters['update_namespace_claim'].inc()
            result = mariadb._direct_update_namespace_claim(
                request.uuid, list(request.fields), request.limit_cpus,
                request.limit_memory_mb, request.limit_disk_gb,
                request.expires_in_seconds)
            reply = database_pb2.UpdateNamespaceClaimReply(
                success=result['success'],
                error=result['error'],
                updated=result['updated'],
                refused_reason=result['refused_reason'])
            for dimension in result['dimensions']:
                reply.dimensions.add(**dimension)
            if result['claim'] is not None:
                reply.claim.CopyFrom(self._claim_message(result['claim']))
            return reply
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateNamespaceClaim failed', e)
            return database_pb2.UpdateNamespaceClaimReply(
                success=False, error=str(e))

    def DeleteNamespaceClaim(
        self,
        request: database_pb2.DeleteNamespaceClaimRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteNamespaceClaimReply:
        """Delete a claim and return its capacity to the cluster."""
        try:
            self.monitor.counters['delete_namespace_claim'].inc()
            result = mariadb._direct_delete_namespace_claim(request.uuid)
            return database_pb2.DeleteNamespaceClaimReply(
                success=result['success'],
                error=result['error'],
                deleted=result['deleted'],
                returned_cpus=result['returned_cpus'],
                returned_memory_mb=result['returned_memory_mb'],
                returned_disk_gb=result['returned_disk_gb'],
                clamped=result['clamped'])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNamespaceClaim failed', e)
            return database_pb2.DeleteNamespaceClaimReply(
                success=False, error=str(e))

    # =========================================================
    # Namespace Operations (MariaDB)
    # =========================================================

    def CreateNamespace(self, request: database_pb2.CreateNamespaceRequest,
                        context: grpc.ServicerContext) -> database_pb2.StatusReply:
        """Create a namespace record in MariaDB."""
        try:
            self.monitor.counters['create_namespace'].inc()
            success = mariadb._direct_create_namespace(request.namespace.name,
                                                       request.namespace.version)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database CreateNamespace failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetNamespace(self, request: database_pb2.GetNamespaceRequest,
                     context: grpc.ServicerContext) -> database_pb2.GetNamespaceReply:
        """Get namespace static values from MariaDB."""
        try:
            self.monitor.counters['get_namespace'].inc()
            data = mariadb._direct_get_namespace(request.name)
            if data is None:
                return database_pb2.GetNamespaceReply(found=False)
            return database_pb2.GetNamespaceReply(
                found=True,
                namespace=database_pb2.NamespaceStaticData(name=data.name, version=data.version))
        except Exception as e:
            util_exceptions.ignore_exception('database GetNamespace failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNamespaceReply(found=False)

    def GetAllNamespaceNames(self, request: database_pb2.GetAllNamespaceNamesRequest,
                             context: grpc.ServicerContext
                             ) -> database_pb2.GetAllNamespaceNamesReply:
        """Get all namespace names from MariaDB."""
        try:
            self.monitor.counters['get_all_namespace_names'].inc()
            names = mariadb._direct_get_all_namespace_names()
            return database_pb2.GetAllNamespaceNamesReply(names=names)
        except Exception as e:
            util_exceptions.ignore_exception('database GetAllNamespaceNames failed', e)
            return database_pb2.GetAllNamespaceNamesReply(names=[])

    def DeleteNamespace(self, request: database_pb2.DeleteNamespaceRequest,
                        context: grpc.ServicerContext) -> database_pb2.StatusReply:
        """Delete a namespace record from MariaDB."""
        try:
            self.monitor.counters['delete_namespace'].inc()
            success = mariadb._direct_delete_namespace(request.name)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database DeleteNamespace failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # =========================================================
    # Namespace Attributes Operations (MariaDB)
    # =========================================================

    def CreateNamespaceAttributes(self, request: database_pb2.CreateNamespaceAttributesRequest,
                                  context: grpc.ServicerContext) -> database_pb2.StatusReply:
        """Create namespace attributes in MariaDB."""
        try:
            self.monitor.counters['create_namespace_attributes'].inc()
            data = self._ns_attrs_from_proto(request.data)
            success = mariadb._direct_create_namespace_attributes(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database CreateNamespaceAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetNamespaceAttributes(self, request: database_pb2.GetNamespaceAttributesRequest,
                               context: grpc.ServicerContext
                               ) -> database_pb2.GetNamespaceAttributesReply:
        """Get namespace attributes from MariaDB."""
        try:
            self.monitor.counters['get_namespace_attributes'].inc()
            data = mariadb._direct_get_namespace_attributes(request.name)
            if data is None:
                return database_pb2.GetNamespaceAttributesReply(found=False)
            return database_pb2.GetNamespaceAttributesReply(
                found=True, data=self._ns_attrs_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception('database GetNamespaceAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNamespaceAttributesReply(found=False)

    def UpdateNamespaceAttributes(self, request: database_pb2.UpdateNamespaceAttributesRequest,
                                  context: grpc.ServicerContext) -> database_pb2.StatusReply:
        """Update namespace attributes in MariaDB."""
        try:
            self.monitor.counters['update_namespace_attributes'].inc()
            data = self._ns_attrs_from_proto(request.data)
            success = mariadb._direct_update_namespace_attributes(
                data, fields=list(request.fields))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database UpdateNamespaceAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteNamespaceAttributes(self, request: database_pb2.DeleteNamespaceAttributesRequest,
                                  context: grpc.ServicerContext) -> database_pb2.StatusReply:
        """Delete namespace attributes from MariaDB."""
        try:
            self.monitor.counters['delete_namespace_attributes'].inc()
            success = mariadb._direct_delete_namespace_attributes(request.name)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database DeleteNamespaceAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def _ns_attrs_from_proto(self,
                             d: database_pb2.NamespaceAttributesProto) -> 'NamespaceAttributesData':
        """Convert a proto NamespaceAttributesProto to model."""
        return NamespaceAttributesData(
            name=d.name,
            keys=json.loads(d.keys_json) if d.keys_json else {'nonced_keys': {}},
            trust=json.loads(d.trust_json) if d.trust_json else ['system'],
        )

    def _ns_attrs_to_proto(self,
                           data: 'NamespaceAttributesData') -> database_pb2.NamespaceAttributesProto:
        """Convert a Pydantic NamespaceAttributesData to proto."""
        return database_pb2.NamespaceAttributesProto(
            name=data.name, keys_json=json.dumps(data.keys), trust_json=json.dumps(data.trust))

    # =========================================================================
    # NetworkInterface Operations (MariaDB)
    # =========================================================================

    def CreateNetworkInterface(
        self,
        request: database_pb2.CreateNetworkInterfaceRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create a NetworkInterface record in MariaDB."""
        try:
            self.monitor.counters['create_network_interface'].inc()
            data = self._ni_from_proto(request.network_interface)
            success = mariadb._direct_create_network_interface(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database CreateNetworkInterface failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetNetworkInterface(
        self,
        request: database_pb2.GetNetworkInterfaceRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNetworkInterfaceReply:
        """Get NetworkInterface static values from MariaDB."""
        try:
            self.monitor.counters['get_network_interface'].inc()
            data = mariadb._direct_get_network_interface(UUID(request.uuid))
            if data is None:
                return database_pb2.GetNetworkInterfaceReply(found=False)
            return database_pb2.GetNetworkInterfaceReply(
                found=True,
                network_interface=self._ni_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception('database GetNetworkInterface failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNetworkInterfaceReply(found=False)

    def GetNetworkInterfacesByInstance(
        self,
        request: database_pb2.GetNetworkInterfacesByInstanceRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNetworkInterfacesReply:
        """Get NetworkInterfaces for an instance from MariaDB."""
        try:
            self.monitor.counters['get_network_interfaces_by_instance'].inc()
            nis = mariadb._direct_get_network_interfaces_by_instance(
                UUID(request.instance_uuid))
            return database_pb2.GetNetworkInterfacesReply(
                network_interfaces=[self._ni_to_proto(d) for d in nis])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNetworkInterfacesByInstance failed', e)
            return database_pb2.GetNetworkInterfacesReply(network_interfaces=[])

    def GetNetworkInterfacesByNetwork(
        self,
        request: database_pb2.GetNetworkInterfacesByNetworkRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNetworkInterfacesReply:
        """Get NetworkInterfaces for a network from MariaDB."""
        try:
            self.monitor.counters['get_network_interfaces_by_network'].inc()
            nis = mariadb._direct_get_network_interfaces_by_network(
                UUID(request.network_uuid))
            return database_pb2.GetNetworkInterfacesReply(
                network_interfaces=[self._ni_to_proto(d) for d in nis])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNetworkInterfacesByNetwork failed', e)
            return database_pb2.GetNetworkInterfacesReply(network_interfaces=[])

    def GetAllNetworkInterfaces(
        self,
        request: database_pb2.GetAllNetworkInterfacesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNetworkInterfacesReply:
        """Get all NetworkInterface records from MariaDB."""
        try:
            self.monitor.counters['get_all_network_interfaces'].inc()
            nis = mariadb._direct_get_all_network_interfaces()
            return database_pb2.GetNetworkInterfacesReply(
                network_interfaces=[self._ni_to_proto(d) for d in nis])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllNetworkInterfaces failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNetworkInterfacesReply(
                network_interfaces=[])

    def FindNetworkInterfaces(
        self,
        request: database_pb2.FindNetworkInterfacesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.FindNetworkInterfacesReply:
        """Find NetworkInterface records matching filter criteria from MariaDB."""
        try:
            self.monitor.counters['find_network_interfaces'].inc()
            criteria = ObjectFilterCriteria(
                states=list(request.criteria.states) or None,
                namespace=(
                    request.criteria.namespace
                    if request.criteria.HasField('namespace') else None),
                name=(
                    request.criteria.name
                    if request.criteria.HasField('name') else None),
                network_uuid=(
                    request.criteria.network_uuid
                    if request.criteria.HasField('network_uuid') else None),
                instance_uuid=(
                    request.criteria.instance_uuid
                    if request.criteria.HasField('instance_uuid') else None),
            )
            nis = mariadb._direct_find_network_interfaces(criteria)
            return database_pb2.FindNetworkInterfacesReply(
                network_interfaces=[
                    self._ni_to_proto(d)
                    for d in nis
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database FindNetworkInterfaces failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.FindNetworkInterfacesReply(
                network_interfaces=[])

    def DeleteNetworkInterface(
        self,
        request: database_pb2.DeleteNetworkInterfaceRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a NetworkInterface record from MariaDB."""
        try:
            self.monitor.counters['delete_network_interface'].inc()
            success = mariadb._direct_delete_network_interface(UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database DeleteNetworkInterface failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def UpdateNetworkInterface(
        self,
        request: database_pb2.UpdateNetworkInterfaceRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update a NetworkInterface record in MariaDB."""
        try:
            self.monitor.counters['update_network_interface'].inc()
            data = self._ni_from_proto(request.network_interface)
            success = mariadb._direct_update_network_interface(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database UpdateNetworkInterface failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # =========================================================================
    # NetworkInterface Attributes Operations (MariaDB)
    # =========================================================================

    def CreateNetworkInterfaceAttributes(
        self,
        request: database_pb2.CreateNetworkInterfaceAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create NetworkInterface attributes in MariaDB."""
        try:
            self.monitor.counters['create_network_interface_attributes'].inc()
            data = self._ni_attrs_from_proto(request.data)
            success = mariadb._direct_create_network_interface_attributes(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateNetworkInterfaceAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetNetworkInterfaceAttributes(
        self,
        request: database_pb2.GetNetworkInterfaceAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNetworkInterfaceAttributesReply:
        """Get NetworkInterface attributes from MariaDB."""
        try:
            self.monitor.counters['get_network_interface_attributes'].inc()
            data = mariadb._direct_get_network_interface_attributes(UUID(request.uuid))
            if data is None:
                return database_pb2.GetNetworkInterfaceAttributesReply(found=False)
            return database_pb2.GetNetworkInterfaceAttributesReply(
                found=True, data=self._ni_attrs_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNetworkInterfaceAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNetworkInterfaceAttributesReply(found=False)

    def UpdateNetworkInterfaceAttributes(
        self,
        request: database_pb2.UpdateNetworkInterfaceAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update NetworkInterface attributes in MariaDB."""
        try:
            self.monitor.counters['update_network_interface_attributes'].inc()
            data = self._ni_attrs_from_proto(request.data)
            success = mariadb._direct_update_network_interface_attributes(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateNetworkInterfaceAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteNetworkInterfaceAttributes(
        self,
        request: database_pb2.DeleteNetworkInterfaceAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete NetworkInterface attributes from MariaDB."""
        try:
            self.monitor.counters['delete_network_interface_attributes'].inc()
            success = mariadb._direct_delete_network_interface_attributes(UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNetworkInterfaceAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def _ni_from_proto(self,
                       d: database_pb2.NetworkInterfaceStaticData) -> NetworkInterfaceData:
        """Convert a proto NetworkInterfaceStaticData to model."""
        return NetworkInterfaceData(
            uuid=UUID(d.uuid),
            network_uuid=UUID(d.network_uuid),
            instance_uuid=UUID(d.instance_uuid),
            macaddr=d.macaddr,
            ipv4=d.ipv4 or None,
            order=d.order,
            model=d.model or None,
            version=d.version
        )

    def _ni_to_proto(self,
                     data: NetworkInterfaceData) -> database_pb2.NetworkInterfaceStaticData:
        """Convert a Pydantic NetworkInterfaceData to proto."""
        return database_pb2.NetworkInterfaceStaticData(
            uuid=str(data.uuid),
            network_uuid=str(data.network_uuid),
            instance_uuid=str(data.instance_uuid),
            macaddr=data.macaddr,
            ipv4=data.ipv4 or '',
            order=data.order,
            model=data.model or '',
            version=data.version
        )

    def _ni_attrs_from_proto(
            self,
            d: database_pb2.NetworkInterfaceAttributesProto
    ) -> NetworkInterfaceAttributesData:
        """Convert a proto NetworkInterfaceAttributesProto to model."""
        return NetworkInterfaceAttributesData(
            uuid=UUID(d.uuid),
            floating_address=d.floating_address if d.floating_address else None,
        )

    def _ni_attrs_to_proto(
            self,
            data: NetworkInterfaceAttributesData
    ) -> database_pb2.NetworkInterfaceAttributesProto:
        """Convert a Pydantic NetworkInterfaceAttributesData to proto."""
        return database_pb2.NetworkInterfaceAttributesProto(
            uuid=str(data.uuid),
            floating_address=data.floating_address or '')

    # =========================================================================
    # IPAM Operations (MariaDB)
    # =========================================================================

    def CreateIPAM(
        self,
        request: database_pb2.CreateIPAMRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create an IPAM record in MariaDB."""
        try:
            self.monitor.counters['create_ipam'].inc()
            data = self._ipam_from_proto(request.ipam)
            success = mariadb._direct_create_ipam(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database CreateIPAM failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetIPAM(
        self,
        request: database_pb2.GetIPAMRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetIPAMReply:
        """Get IPAM static values from MariaDB."""
        try:
            self.monitor.counters['get_ipam'].inc()
            data = mariadb._direct_get_ipam(UUID(request.uuid))
            if data is None:
                return database_pb2.GetIPAMReply(found=False)
            return database_pb2.GetIPAMReply(
                found=True,
                ipam=self._ipam_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception('database GetIPAM failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetIPAMReply(found=False)

    def DeleteIPAM(
        self,
        request: database_pb2.DeleteIPAMRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete an IPAM record from MariaDB."""
        try:
            self.monitor.counters['delete_ipam'].inc()
            success = mariadb._direct_delete_ipam(UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database DeleteIPAM failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def UpdateIPAM(
        self,
        request: database_pb2.UpdateIPAMRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update an IPAM record in MariaDB."""
        try:
            self.monitor.counters['update_ipam'].inc()
            data = self._ipam_from_proto(request.ipam)
            success = mariadb._direct_update_ipam(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database UpdateIPAM failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def _ipam_from_proto(self, d: database_pb2.IPAMStaticData) -> IPAMData:
        """Convert a proto IPAMStaticData to model."""
        return IPAMData(
            uuid=UUID(d.uuid),
            namespace=d.namespace or None,
            network_uuid=UUID(d.network_uuid),
            ipblock=d.ipblock,
            version=d.version
        )

    def _ipam_to_proto(self, data: IPAMData) -> database_pb2.IPAMStaticData:
        """Convert a Pydantic IPAMData to proto."""
        return database_pb2.IPAMStaticData(
            uuid=str(data.uuid),
            namespace=data.namespace or '',
            network_uuid=str(data.network_uuid),
            ipblock=data.ipblock,
            version=data.version
        )

    # Network Operations (MariaDB)

    def CreateNetwork(
        self,
        request: database_pb2.CreateNetworkRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create a Network record in MariaDB."""
        try:
            self.monitor.counters['create_network'].inc()
            data = self._network_from_proto(request.network)
            success = mariadb._direct_create_network(data)
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateNetwork failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetNetwork(
        self,
        request: database_pb2.GetNetworkRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNetworkReply:
        """Get Network static values from MariaDB."""
        try:
            self.monitor.counters['get_network'].inc()
            data = mariadb._direct_get_network(
                UUID(request.uuid))
            if data is None:
                return database_pb2.GetNetworkReply(found=False)
            return database_pb2.GetNetworkReply(
                found=True,
                network=self._network_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNetwork failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNetworkReply(found=False)

    def GetAllNetworks(
        self,
        request: database_pb2.GetAllNetworksRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllNetworksReply:
        """Get all Network records from MariaDB."""
        try:
            self.monitor.counters['get_all_networks'].inc()
            networks = mariadb._direct_get_all_networks()
            return database_pb2.GetAllNetworksReply(
                networks=[
                    self._network_to_proto(d)
                    for d in networks
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllNetworks failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetAllNetworksReply(networks=[])

    def FindNetworks(
        self,
        request: database_pb2.FindNetworksRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.FindNetworksReply:
        """Find Network records matching filter criteria from MariaDB."""
        try:
            self.monitor.counters['find_networks'].inc()
            criteria = ObjectFilterCriteria(
                states=list(request.criteria.states) or None,
                namespace=(
                    request.criteria.namespace
                    if request.criteria.HasField('namespace') else None),
                name=(
                    request.criteria.name
                    if request.criteria.HasField('name') else None),
            )
            networks = mariadb._direct_find_networks(criteria)
            return database_pb2.FindNetworksReply(
                networks=[
                    self._network_to_proto(d)
                    for d in networks
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database FindNetworks failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.FindNetworksReply(networks=[])

    def FindNetworkVxids(
        self,
        request: database_pb2.FindNetworkVxidsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.FindNetworkVxidsReply:
        """Return the requested vxids a network claims, and its uuid.

        The reply is empty both when nothing claims the vxids and, were
        we to swallow the error, when the query failed -- and the caller
        deletes host network devices when a vxid is unclaimed. The error
        path therefore sets an INTERNAL status so the client raises
        rather than reading the empty reply as an answer.
        """
        try:
            self.monitor.counters['find_network_vxids'].inc()
            claims = mariadb._direct_find_network_vxids(list(request.vxids))
            return database_pb2.FindNetworkVxidsReply(
                claims=[
                    database_pb2.NetworkVxidClaim(vxid=vxid, uuid=uuid)
                    for vxid, uuid in sorted(claims.items())
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database FindNetworkVxids failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.FindNetworkVxidsReply(claims=[])

    def GetNodeInstanceVxids(
        self,
        request: database_pb2.GetNodeInstanceVxidsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNodeInstanceVxidsReply:
        """Return the vxids instances on a node are attached to.

        As with FindNetworkVxids, an empty reply is a meaningful answer
        -- it means no instance on that node uses the network, and the
        caller tears down host network devices on the strength of it.
        The error path therefore sets an INTERNAL status so the client
        raises rather than reading the empty reply as an answer.
        """
        try:
            self.monitor.counters['get_node_instance_vxids'].inc()
            vxids = mariadb._direct_get_node_instance_vxids(
                request.node_uuid, list(request.states))
            return database_pb2.GetNodeInstanceVxidsReply(
                vxids=sorted(vxids))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNodeInstanceVxids failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNodeInstanceVxidsReply(vxids=[])

    def DeleteNetwork(
        self,
        request: database_pb2.DeleteNetworkRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a Network record from MariaDB."""
        try:
            self.monitor.counters['delete_network'].inc()
            success = mariadb._direct_delete_network(
                UUID(request.uuid))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNetwork failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def _network_from_proto(
            self,
            d: database_pb2.NetworkStaticData) -> NetworkData:
        """Convert a proto NetworkStaticData to model."""
        return NetworkData(
            uuid=UUID(d.uuid),
            name=d.name,
            namespace=d.namespace or None,
            netblock=d.netblock,
            provide_dhcp=d.provide_dhcp,
            provide_nat=d.provide_nat,
            provide_dns=d.provide_dns,
            vxid=d.vxid,
            egress_nic=d.egress_nic or None,
            mesh_nic=d.mesh_nic or None,
            version=d.version
        )

    def _network_to_proto(
            self,
            data: NetworkData) -> database_pb2.NetworkStaticData:
        """Convert a Pydantic NetworkData to proto."""
        return database_pb2.NetworkStaticData(
            uuid=str(data.uuid),
            name=data.name,
            namespace=data.namespace or '',
            netblock=data.netblock,
            provide_dhcp=data.provide_dhcp,
            provide_nat=data.provide_nat,
            provide_dns=data.provide_dns,
            vxid=data.vxid,
            egress_nic=data.egress_nic or '',
            mesh_nic=data.mesh_nic or '',
            version=data.version
        )

    # Network Attributes Operations (MariaDB)

    def CreateNetworkAttributes(
        self,
        request: database_pb2.CreateNetworkAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create Network attributes in MariaDB."""
        try:
            self.monitor.counters[
                'create_network_attributes'].inc()
            data = self._network_attrs_from_proto(request.data)
            success = mariadb._direct_create_network_attributes(
                data)
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateNetworkAttributes failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetNetworkAttributes(
        self,
        request: database_pb2.GetNetworkAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNetworkAttributesReply:
        """Get Network attributes from MariaDB."""
        try:
            self.monitor.counters[
                'get_network_attributes'].inc()
            data = mariadb._direct_get_network_attributes(
                UUID(request.uuid))
            if data is None:
                return database_pb2.GetNetworkAttributesReply(
                    found=False)
            return database_pb2.GetNetworkAttributesReply(
                found=True,
                data=self._network_attrs_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNetworkAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNetworkAttributesReply(
                found=False)

    def GetNetworkFloatingGateways(
        self,
        request: database_pb2.GetNetworkFloatingGatewaysRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNetworkFloatingGatewaysReply:
        """Get every assigned floating gateway from MariaDB."""
        try:
            self.monitor.counters[
                'get_network_floating_gateways'].inc()
            gateways = mariadb._direct_get_network_floating_gateways()
            return database_pb2.GetNetworkFloatingGatewaysReply(
                gateways=[
                    database_pb2.NetworkFloatingGateway(
                        uuid=net_uuid, floating_gateway=addr)
                    for net_uuid, addr in sorted(gateways.items())
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNetworkFloatingGateways failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNetworkFloatingGatewaysReply()

    def UpdateNetworkAttributes(
        self,
        request: database_pb2.UpdateNetworkAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update Network attributes in MariaDB."""
        try:
            self.monitor.counters[
                'update_network_attributes'].inc()
            data = self._network_attrs_from_proto(request.data)
            success = mariadb._direct_update_network_attributes(
                data, fields=list(request.fields))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateNetworkAttributes failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteNetworkAttributes(
        self,
        request: database_pb2.DeleteNetworkAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete Network attributes from MariaDB."""
        try:
            self.monitor.counters[
                'delete_network_attributes'].inc()
            success = mariadb._direct_delete_network_attributes(
                UUID(request.uuid))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNetworkAttributes failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def _network_attrs_from_proto(
            self,
            d: database_pb2.NetworkAttributesProto
    ) -> NetworkAttributesData:
        """Convert a proto NetworkAttributesProto to model."""
        dns = (json.loads(d.hosteddns_json)
               if d.hosteddns_json else {})
        return NetworkAttributesData(
            uuid=UUID(d.uuid),
            floating_gateway=(
                d.floating_gateway
                if d.floating_gateway else None),
            hosteddns=dns,
        )

    def _network_attrs_to_proto(
            self,
            data: NetworkAttributesData
    ) -> database_pb2.NetworkAttributesProto:
        """Convert a Pydantic NetworkAttributesData to proto."""
        return database_pb2.NetworkAttributesProto(
            uuid=str(data.uuid),
            floating_gateway=data.floating_gateway or '',
            hosteddns_json=json.dumps(data.hosteddns))

    # NamespaceKey Operations (MariaDB)

    def CreateNamespaceKey(
        self,
        request: database_pb2.CreateNamespaceKeyRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create a NamespaceKey record in MariaDB."""
        try:
            self.monitor.counters['create_namespace_key'].inc()
            data = mariadb._namespace_key_from_proto(request.data)
            success = mariadb._direct_create_namespace_key(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateNamespaceKey failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetNamespaceKey(
        self,
        request: database_pb2.GetNamespaceKeyRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNamespaceKeyReply:
        """Get NamespaceKey static values from MariaDB."""
        try:
            self.monitor.counters['get_namespace_key'].inc()
            data = mariadb._direct_get_namespace_key(UUID(request.uuid))
            if data is None:
                return database_pb2.GetNamespaceKeyReply(found=False)
            return database_pb2.GetNamespaceKeyReply(
                found=True,
                data=mariadb._namespace_key_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNamespaceKey failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNamespaceKeyReply(found=False)

    def GetNamespaceKeyByName(
        self,
        request: database_pb2.GetNamespaceKeyByNameRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNamespaceKeyByNameReply:
        """Point read one key by its (namespace, name) pair."""
        try:
            self.monitor.counters['get_namespace_key_by_name'].inc()
            row = mariadb._direct_get_namespace_key_by_name(
                request.namespace, request.name)
            if row is None:
                return database_pb2.GetNamespaceKeyByNameReply(found=False)
            static_data, attributes = row
            return database_pb2.GetNamespaceKeyByNameReply(
                found=True,
                key=database_pb2.NamespaceKeyJoinedProto(
                    static_data=mariadb._namespace_key_to_proto(static_data),
                    attributes=mariadb._namespace_key_attrs_to_proto(
                        attributes)))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNamespaceKeyByName failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNamespaceKeyByNameReply(found=False)

    def FindNamespaceKeys(
        self,
        request: database_pb2.FindNamespaceKeysRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.FindNamespaceKeysReply:
        """List a namespace's keys, joined with their attributes."""
        try:
            self.monitor.counters['find_namespace_keys'].inc()
            rows = mariadb._direct_find_namespace_keys(
                request.namespace, request.include_expired, request.now)
            return database_pb2.FindNamespaceKeysReply(
                keys=[
                    database_pb2.NamespaceKeyJoinedProto(
                        static_data=mariadb._namespace_key_to_proto(
                            static_data),
                        attributes=mariadb._namespace_key_attrs_to_proto(
                            attributes))
                    for static_data, attributes in rows
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database FindNamespaceKeys failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.FindNamespaceKeysReply(keys=[])

    def DeleteNamespaceKey(
        self,
        request: database_pb2.DeleteNamespaceKeyRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a NamespaceKey record from MariaDB."""
        try:
            self.monitor.counters['delete_namespace_key'].inc()
            success = mariadb._direct_delete_namespace_key(
                UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNamespaceKey failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteExpiredNamespaceKeys(
        self,
        request: database_pb2.DeleteExpiredNamespaceKeysRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteCountReply:
        """Hard delete keys which expired before older_than."""
        try:
            self.monitor.counters['delete_expired_namespace_keys'].inc()
            count = mariadb._direct_delete_expired_namespace_keys(
                request.older_than)
            return database_pb2.DeleteCountReply(count=count)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteExpiredNamespaceKeys failed', e)
            return database_pb2.DeleteCountReply(count=0)

    # NamespaceKey Attributes Operations (MariaDB)

    def CreateNamespaceKeyAttributes(
        self,
        request: database_pb2.CreateNamespaceKeyAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create NamespaceKey attributes in MariaDB."""
        try:
            self.monitor.counters[
                'create_namespace_key_attributes'].inc()
            data = mariadb._namespace_key_attrs_from_proto(request.data)
            success = mariadb._direct_create_namespace_key_attributes(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateNamespaceKeyAttributes failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetNamespaceKeyAttributes(
        self,
        request: database_pb2.GetNamespaceKeyAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNamespaceKeyAttributesReply:
        """Get NamespaceKey attributes from MariaDB."""
        try:
            self.monitor.counters['get_namespace_key_attributes'].inc()
            data = mariadb._direct_get_namespace_key_attributes(
                UUID(request.uuid))
            if data is None:
                return database_pb2.GetNamespaceKeyAttributesReply(
                    found=False)
            return database_pb2.GetNamespaceKeyAttributesReply(
                found=True,
                data=mariadb._namespace_key_attrs_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNamespaceKeyAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNamespaceKeyAttributesReply(found=False)

    def UpdateNamespaceKeyAttributes(
        self,
        request: database_pb2.UpdateNamespaceKeyAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update NamespaceKey attributes in MariaDB."""
        try:
            self.monitor.counters[
                'update_namespace_key_attributes'].inc()
            data = mariadb._namespace_key_attrs_from_proto(request.data)
            success = mariadb._direct_update_namespace_key_attributes(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateNamespaceKeyAttributes failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteNamespaceKeyAttributes(
        self,
        request: database_pb2.DeleteNamespaceKeyAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete NamespaceKey attributes from MariaDB."""
        try:
            self.monitor.counters[
                'delete_namespace_key_attributes'].inc()
            success = mariadb._direct_delete_namespace_key_attributes(
                UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNamespaceKeyAttributes failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    # AgentOperation Operations (MariaDB)

    # ------------------------------------------------------------------
    # TrustedIssuer operations
    # ------------------------------------------------------------------

    def CreateTrustedIssuer(
        self,
        request: database_pb2.CreateTrustedIssuerRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['create_trusted_issuer'].inc()
            ok = mariadb._direct_create_trusted_issuer(
                mariadb._trusted_issuer_from_proto(request.data))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateTrustedIssuer failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    def GetTrustedIssuer(
        self,
        request: database_pb2.GetTrustedIssuerRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetTrustedIssuerReply:
        try:
            self.monitor.counters['get_trusted_issuer'].inc()
            data = mariadb._direct_get_trusted_issuer(UUID(request.uuid))
            if data is None:
                return database_pb2.GetTrustedIssuerReply(found=False)
            return database_pb2.GetTrustedIssuerReply(
                found=True, data=mariadb._trusted_issuer_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetTrustedIssuer failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetTrustedIssuerReply(found=False)

    def GetTrustedIssuerByName(
        self,
        request: database_pb2.GetTrustedIssuerByNameRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetTrustedIssuerReply:
        try:
            self.monitor.counters['get_trusted_issuer_by_name'].inc()
            data = mariadb._direct_get_trusted_issuer_by_name(request.name)
            if data is None:
                return database_pb2.GetTrustedIssuerReply(found=False)
            return database_pb2.GetTrustedIssuerReply(
                found=True, data=mariadb._trusted_issuer_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetTrustedIssuerByName failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetTrustedIssuerReply(found=False)

    def GetAllTrustedIssuers(
        self,
        request: database_pb2.GetAllTrustedIssuersRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllTrustedIssuersReply:
        try:
            self.monitor.counters['get_all_trusted_issuers'].inc()
            issuers = mariadb._direct_get_all_trusted_issuers()
            return database_pb2.GetAllTrustedIssuersReply(
                issuers=[mariadb._trusted_issuer_to_proto(i)
                         for i in issuers])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllTrustedIssuers failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetAllTrustedIssuersReply(issuers=[])

    def DeleteTrustedIssuer(
        self,
        request: database_pb2.DeleteTrustedIssuerRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['delete_trusted_issuer'].inc()
            ok = mariadb._direct_delete_trusted_issuer(UUID(request.uuid))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteTrustedIssuer failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    def CreateTrustedIssuerAttributes(
        self,
        request: database_pb2.CreateTrustedIssuerAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['create_trusted_issuer_attributes'].inc()
            ok = mariadb._direct_create_trusted_issuer_attributes(
                mariadb._trusted_issuer_attrs_from_proto(request.data))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateTrustedIssuerAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    def GetTrustedIssuerAttributes(
        self,
        request: database_pb2.GetTrustedIssuerAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetTrustedIssuerAttributesReply:
        try:
            self.monitor.counters['get_trusted_issuer_attributes'].inc()
            data = mariadb._direct_get_trusted_issuer_attributes(
                UUID(request.uuid))
            if data is None:
                return database_pb2.GetTrustedIssuerAttributesReply(
                    found=False)
            return database_pb2.GetTrustedIssuerAttributesReply(
                found=True,
                data=mariadb._trusted_issuer_attrs_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetTrustedIssuerAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetTrustedIssuerAttributesReply(found=False)

    def UpdateTrustedIssuerAttributes(
        self,
        request: database_pb2.UpdateTrustedIssuerAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['update_trusted_issuer_attributes'].inc()
            ok = mariadb._direct_update_trusted_issuer_attributes(
                mariadb._trusted_issuer_attrs_from_proto(request.data),
                fields=list(request.fields))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateTrustedIssuerAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    def DeleteTrustedIssuerAttributes(
        self,
        request: database_pb2.DeleteTrustedIssuerAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['delete_trusted_issuer_attributes'].inc()
            ok = mariadb._direct_delete_trusted_issuer_attributes(
                UUID(request.uuid))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteTrustedIssuerAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    # ------------------------------------------------------------------
    # MappingRule operations
    # ------------------------------------------------------------------

    def CreateMappingRule(
        self,
        request: database_pb2.CreateMappingRuleRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['create_mapping_rule'].inc()
            ok = mariadb._direct_create_mapping_rule(
                mariadb._mapping_rule_from_proto(request.data))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateMappingRule failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    def GetMappingRule(
        self,
        request: database_pb2.GetMappingRuleRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetMappingRuleReply:
        try:
            self.monitor.counters['get_mapping_rule'].inc()
            data = mariadb._direct_get_mapping_rule(UUID(request.uuid))
            if data is None:
                return database_pb2.GetMappingRuleReply(found=False)
            return database_pb2.GetMappingRuleReply(
                found=True, data=mariadb._mapping_rule_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetMappingRule failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetMappingRuleReply(found=False)

    def GetMappingRuleByName(
        self,
        request: database_pb2.GetMappingRuleByNameRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetMappingRuleReply:
        try:
            self.monitor.counters['get_mapping_rule_by_name'].inc()
            data = mariadb._direct_get_mapping_rule_by_name(
                request.namespace, request.name)
            if data is None:
                return database_pb2.GetMappingRuleReply(found=False)
            return database_pb2.GetMappingRuleReply(
                found=True, data=mariadb._mapping_rule_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetMappingRuleByName failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetMappingRuleReply(found=False)

    def GetMappingRulesInNamespace(
        self,
        request: database_pb2.GetMappingRulesInNamespaceRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetMappingRulesReply:
        try:
            self.monitor.counters['get_mapping_rules_in_namespace'].inc()
            rules = mariadb._direct_get_mapping_rules_in_namespace(
                request.namespace)
            return database_pb2.GetMappingRulesReply(
                rules=[mariadb._mapping_rule_to_proto(r) for r in rules])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetMappingRulesInNamespace failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetMappingRulesReply(rules=[])

    def GetAllMappingRules(
        self,
        request: database_pb2.GetAllMappingRulesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetMappingRulesReply:
        try:
            self.monitor.counters['get_all_mapping_rules'].inc()
            rules = mariadb._direct_get_all_mapping_rules()
            return database_pb2.GetMappingRulesReply(
                rules=[mariadb._mapping_rule_to_proto(r) for r in rules])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllMappingRules failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetMappingRulesReply(rules=[])

    def DeleteMappingRule(
        self,
        request: database_pb2.DeleteMappingRuleRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['delete_mapping_rule'].inc()
            ok = mariadb._direct_delete_mapping_rule(UUID(request.uuid))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteMappingRule failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    def CreateMappingRuleAttributes(
        self,
        request: database_pb2.CreateMappingRuleAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['create_mapping_rule_attributes'].inc()
            ok = mariadb._direct_create_mapping_rule_attributes(
                mariadb._mapping_rule_attrs_from_proto(request.data))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateMappingRuleAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    def GetMappingRuleAttributes(
        self,
        request: database_pb2.GetMappingRuleAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetMappingRuleAttributesReply:
        try:
            self.monitor.counters['get_mapping_rule_attributes'].inc()
            data = mariadb._direct_get_mapping_rule_attributes(
                UUID(request.uuid))
            if data is None:
                return database_pb2.GetMappingRuleAttributesReply(found=False)
            return database_pb2.GetMappingRuleAttributesReply(
                found=True, data=mariadb._mapping_rule_attrs_to_proto(data))
        except exceptions.CorruptMappingRule as e:
            # A rule whose bound_claims or scopes will not decode is a
            # data fault, and the catch-all below would flatten it into
            # INTERNAL, which the client can only read as a database
            # outage. Carry it in a field instead, so the API can still
            # refuse the exchange and still mark the rule unusable. See
            # the comment on GetMappingRuleAttributesReply.corrupt.
            #
            # str(e) is logged rather than passed to set_details(),
            # because the message names the rule uuid, and on the
            # exchange path this reply is produced for a caller who has
            # not authenticated.
            LOG.with_fields({'rule': request.uuid}).error(
                'database GetMappingRuleAttributes found a damaged '
                f'rule: {e}')
            return database_pb2.GetMappingRuleAttributesReply(
                found=True, corrupt=True)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetMappingRuleAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetMappingRuleAttributesReply(found=False)

    def UpdateMappingRuleAttributes(
        self,
        request: database_pb2.UpdateMappingRuleAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['update_mapping_rule_attributes'].inc()
            ok = mariadb._direct_update_mapping_rule_attributes(
                mariadb._mapping_rule_attrs_from_proto(request.data),
                fields=list(request.fields))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateMappingRuleAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    def DeleteMappingRuleAttributes(
        self,
        request: database_pb2.DeleteMappingRuleAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        try:
            self.monitor.counters['delete_mapping_rule_attributes'].inc()
            ok = mariadb._direct_delete_mapping_rule_attributes(
                UUID(request.uuid))
            return database_pb2.StatusReply(success=ok)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteMappingRuleAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.StatusReply(success=False)

    def CreateAgentOperation(
        self,
        request: database_pb2.CreateAgentOperationRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create an AgentOperation record in MariaDB."""
        try:
            self.monitor.counters[
                'create_agent_operation'].inc()
            data = self._agentop_from_proto(request.data)
            success = mariadb._direct_create_agent_operation(
                data)
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateAgentOperation failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetAgentOperation(
        self,
        request: database_pb2.GetAgentOperationRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAgentOperationReply:
        """Get AgentOperation static values from MariaDB."""
        try:
            self.monitor.counters[
                'get_agent_operation'].inc()
            data = mariadb._direct_get_agent_operation(
                UUID(request.uuid))
            if data is None:
                return database_pb2.GetAgentOperationReply(
                    found=False)
            return database_pb2.GetAgentOperationReply(
                found=True,
                data=self._agentop_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAgentOperation failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetAgentOperationReply(
                found=False)

    def DeleteAgentOperation(
        self,
        request: database_pb2.DeleteAgentOperationRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete an AgentOperation record from MariaDB."""
        try:
            self.monitor.counters[
                'delete_agent_operation'].inc()
            success = mariadb._direct_delete_agent_operation(
                UUID(request.uuid))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteAgentOperation failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def _agentop_from_proto(
            self,
            d: database_pb2.AgentOperationStaticData
    ) -> AgentOperationData:
        """Convert a proto AgentOperationStaticData to model."""
        commands = (json.loads(d.commands_json)
                    if d.commands_json else [])
        return AgentOperationData(
            uuid=UUID(d.uuid),
            namespace=d.namespace or '',
            instance_uuid=UUID(d.instance_uuid),
            commands=commands,
            deadline=d.deadline if d.HasField('deadline') else None,
            progress_timeout=(d.progress_timeout
                              if d.HasField('progress_timeout') else None),
            version=d.version
        )

    def _agentop_to_proto(
            self,
            data: AgentOperationData
    ) -> database_pb2.AgentOperationStaticData:
        """Convert a Pydantic AgentOperationData to proto."""
        return database_pb2.AgentOperationStaticData(
            uuid=str(data.uuid),
            namespace=data.namespace or '',
            instance_uuid=str(data.instance_uuid),
            commands_json=json.dumps(data.commands),
            deadline=data.deadline,
            progress_timeout=data.progress_timeout,
            version=data.version
        )

    # AgentOperation Attributes Operations (MariaDB)

    def CreateAgentOperationAttributes(
        self,
        request: database_pb2.CreateAgentOperationAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create AgentOperation attributes in MariaDB."""
        try:
            self.monitor.counters[
                'create_agent_operation_attributes'].inc()
            data = self._agentop_attrs_from_proto(request.data)
            success = (
                mariadb._direct_create_agent_operation_attributes(
                    data))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateAgentOperationAttributes failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetAgentOperationAttributes(
        self,
        request: database_pb2.GetAgentOperationAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAgentOperationAttributesReply:
        """Get AgentOperation attributes from MariaDB."""
        try:
            self.monitor.counters[
                'get_agent_operation_attributes'].inc()
            data = (
                mariadb._direct_get_agent_operation_attributes(
                    UUID(request.uuid)))
            if data is None:
                return (
                    database_pb2
                    .GetAgentOperationAttributesReply(
                        found=False))
            return (
                database_pb2
                .GetAgentOperationAttributesReply(
                    found=True,
                    data=self._agentop_attrs_to_proto(data)))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAgentOperationAttributes failed',
                e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return (
                database_pb2
                .GetAgentOperationAttributesReply(found=False))

    def UpdateAgentOperationAttributes(
        self,
        request: database_pb2.UpdateAgentOperationAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update AgentOperation attributes in MariaDB."""
        try:
            self.monitor.counters[
                'update_agent_operation_attributes'].inc()
            data = self._agentop_attrs_from_proto(request.data)
            success = (
                mariadb._direct_update_agent_operation_attributes(
                    data, fields=list(request.fields)))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateAgentOperationAttributes failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteAgentOperationAttributes(
        self,
        request: database_pb2.DeleteAgentOperationAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete AgentOperation attributes from MariaDB."""
        try:
            self.monitor.counters[
                'delete_agent_operation_attributes'].inc()
            success = (
                mariadb
                ._direct_delete_agent_operation_attributes(
                    UUID(request.uuid)))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteAgentOperationAttributes failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def _agentop_attrs_from_proto(
            self,
            d: database_pb2.AgentOperationAttributesProto
    ) -> AgentOperationAttributesData:
        """Convert a proto AgentOperationAttributesProto to model."""
        results = (json.loads(d.results_json)
                   if d.results_json else {})
        return AgentOperationAttributesData(
            uuid=UUID(d.uuid),
            results=results,
            last_progress=(d.last_progress
                           if d.HasField('last_progress') else None),
            attempts=d.attempts,
        )

    def _agentop_attrs_to_proto(
            self,
            data: AgentOperationAttributesData
    ) -> database_pb2.AgentOperationAttributesProto:
        """Convert AgentOperationAttributesData to proto."""
        return database_pb2.AgentOperationAttributesProto(
            uuid=str(data.uuid),
            results_json=json.dumps(data.results),
            last_progress=data.last_progress,
            attempts=data.attempts)

    # Artifact Operations (MariaDB)
    def CreateArtifact(
        self,
        request: database_pb2.CreateArtifactRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create an artifact record in MariaDB."""
        try:
            self.monitor.counters['create_artifact'].inc()
            success = mariadb._direct_create_artifact(
                UUID(request.artifact.uuid),
                request.artifact.artifact_type,
                request.artifact.source_url,
                request.artifact.name,
                request.artifact.namespace,
                request.artifact.version
            )
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateArtifact failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetArtifact(
        self,
        request: database_pb2.GetArtifactRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetArtifactReply:
        """Get artifact static values from MariaDB."""
        try:
            self.monitor.counters['get_artifact'].inc()
            data = mariadb._direct_get_artifact(UUID(request.uuid))
            if data is None:
                return database_pb2.GetArtifactReply(found=False)
            return database_pb2.GetArtifactReply(
                found=True,
                artifact=database_pb2.ArtifactStaticData(
                    uuid=str(data.uuid),
                    artifact_type=data.artifact_type,
                    source_url=data.source_url,
                    name=data.name,
                    namespace=data.namespace,
                    version=data.version
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetArtifact failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetArtifactReply(found=False)

    def GetAllArtifacts(
        self,
        request: database_pb2.GetAllArtifactsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllArtifactsReply:
        """Get all artifacts from MariaDB."""
        try:
            self.monitor.counters['get_all_artifacts'].inc()
            all_artifacts = mariadb._direct_get_all_artifacts()
            return database_pb2.GetAllArtifactsReply(
                artifacts=[
                    database_pb2.ArtifactStaticData(
                        uuid=str(a.uuid),
                        artifact_type=a.artifact_type,
                        source_url=a.source_url,
                        name=a.name,
                        namespace=a.namespace,
                        version=a.version
                    )
                    for a in all_artifacts
                ]
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllArtifacts failed', e)
            return database_pb2.GetAllArtifactsReply(artifacts=[])

    def FindArtifacts(
        self,
        request: database_pb2.FindArtifactsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.FindArtifactsReply:
        """Find artifact records matching filter criteria from MariaDB."""
        try:
            self.monitor.counters['find_artifacts'].inc()
            criteria = ObjectFilterCriteria(
                states=list(request.criteria.states) or None,
                namespace=(
                    request.criteria.namespace
                    if request.criteria.HasField('namespace') else None),
                name=(
                    request.criteria.name
                    if request.criteria.HasField('name') else None),
            )
            all_artifacts = mariadb._direct_find_artifacts(criteria)
            return database_pb2.FindArtifactsReply(
                artifacts=[
                    database_pb2.ArtifactStaticData(
                        uuid=str(a.uuid),
                        artifact_type=a.artifact_type,
                        source_url=a.source_url,
                        name=a.name,
                        namespace=a.namespace,
                        version=a.version
                    )
                    for a in all_artifacts
                ]
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database FindArtifacts failed', e)
            return database_pb2.FindArtifactsReply(artifacts=[])

    def UpdateArtifact(
        self,
        request: database_pb2.UpdateArtifactRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update an artifact record in MariaDB."""
        try:
            self.monitor.counters['update_artifact'].inc()
            data = ArtifactData(
                uuid=UUID(request.artifact.uuid),
                artifact_type=request.artifact.artifact_type,
                source_url=request.artifact.source_url,
                name=request.artifact.name,
                namespace=request.artifact.namespace,
                version=request.artifact.version
            )
            success = mariadb._direct_update_artifact(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateArtifact failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteArtifact(
        self,
        request: database_pb2.DeleteArtifactRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete an artifact record from MariaDB."""
        try:
            self.monitor.counters['delete_artifact'].inc()
            success = mariadb._direct_delete_artifact(
                UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteArtifact failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # Artifact Attributes Operations (MariaDB)

    def CreateArtifactAttributes(
        self,
        request: database_pb2.CreateArtifactAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create artifact attributes in MariaDB."""
        try:
            self.monitor.counters['create_artifact_attributes'].inc()
            data = ArtifactAttributesData(
                uuid=UUID(request.data.uuid),
                max_versions=request.data.max_versions,
                shared=request.data.shared,
                highest_index=request.data.highest_index
            )
            success = mariadb._direct_create_artifact_attributes(data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateArtifactAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetArtifactAttributes(
        self,
        request: database_pb2.GetArtifactAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetArtifactAttributesReply:
        """Get artifact attributes from MariaDB."""
        try:
            self.monitor.counters['get_artifact_attributes'].inc()
            data = mariadb._direct_get_artifact_attributes(
                UUID(request.uuid))
            if data is None:
                return database_pb2.GetArtifactAttributesReply(
                    found=False)
            return database_pb2.GetArtifactAttributesReply(
                found=True,
                data=database_pb2.ArtifactAttributesProto(
                    uuid=str(data.uuid),
                    max_versions=data.max_versions,
                    shared=data.shared,
                    highest_index=data.highest_index
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetArtifactAttributes failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetArtifactAttributesReply(found=False)

    def UpdateArtifactAttributes(
        self,
        request: database_pb2.UpdateArtifactAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update artifact attributes in MariaDB."""
        try:
            self.monitor.counters['update_artifact_attributes'].inc()
            data = ArtifactAttributesData(
                uuid=UUID(request.data.uuid),
                max_versions=request.data.max_versions,
                shared=request.data.shared,
                highest_index=request.data.highest_index
            )
            success = mariadb._direct_update_artifact_attributes(
                data, fields=list(request.fields))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateArtifactAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteArtifactAttributes(
        self,
        request: database_pb2.DeleteArtifactAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete artifact attributes from MariaDB."""
        try:
            self.monitor.counters['delete_artifact_attributes'].inc()
            success = mariadb._direct_delete_artifact_attributes(
                UUID(request.uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteArtifactAttributes failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    # Artifact Index Operations (MariaDB)

    def CreateArtifactIndex(
        self,
        request: database_pb2.CreateArtifactIndexRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create an artifact index in MariaDB."""
        try:
            self.monitor.counters['create_artifact_index'].inc()
            success = mariadb._direct_create_artifact_index(
                UUID(request.data.artifact_uuid),
                request.data.index_number,
                UUID(request.data.blob_uuid)
            )
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateArtifactIndex failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def GetArtifactIndex(
        self,
        request: database_pb2.GetArtifactIndexRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetArtifactIndexReply:
        """Get a specific artifact index from MariaDB."""
        try:
            self.monitor.counters['get_artifact_index'].inc()
            data = mariadb._direct_get_artifact_index(
                UUID(request.artifact_uuid),
                request.index_number
            )
            if data is None:
                return database_pb2.GetArtifactIndexReply(found=False)
            return database_pb2.GetArtifactIndexReply(
                found=True,
                data=database_pb2.ArtifactIndexProto(
                    artifact_uuid=str(data.artifact_uuid),
                    index_number=data.index_number,
                    blob_uuid=str(data.blob_uuid)
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetArtifactIndex failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetArtifactIndexReply(found=False)

    def GetAllArtifactIndexes(
        self,
        request: database_pb2.GetAllArtifactIndexesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllArtifactIndexesReply:
        """Get all indexes for an artifact from MariaDB."""
        try:
            self.monitor.counters['get_all_artifact_indexes'].inc()
            indexes = mariadb._direct_get_all_artifact_indexes(
                UUID(request.artifact_uuid))
            return database_pb2.GetAllArtifactIndexesReply(
                indexes=[
                    database_pb2.ArtifactIndexProto(
                        artifact_uuid=str(idx.artifact_uuid),
                        index_number=idx.index_number,
                        blob_uuid=str(idx.blob_uuid)
                    )
                    for idx in indexes
                ]
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllArtifactIndexes failed', e)
            return database_pb2.GetAllArtifactIndexesReply(indexes=[])

    def DeleteArtifactIndex(
        self,
        request: database_pb2.DeleteArtifactIndexRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a specific artifact index from MariaDB."""
        try:
            self.monitor.counters['delete_artifact_index'].inc()
            success = mariadb._direct_delete_artifact_index(
                UUID(request.artifact_uuid),
                request.index_number
            )
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteArtifactIndex failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeleteAllArtifactIndexes(
        self,
        request: database_pb2.DeleteAllArtifactIndexesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteCountReply:
        """Delete all indexes for an artifact from MariaDB."""
        try:
            self.monitor.counters['delete_all_artifact_indexes'].inc()
            count = mariadb._direct_delete_all_artifact_indexes(
                UUID(request.artifact_uuid))
            return database_pb2.DeleteCountReply(count=count)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteAllArtifactIndexes failed', e)
            return database_pb2.DeleteCountReply(count=0)

    # Instance Operations (MariaDB)
    def CreateInstance(
        self,
        request: database_pb2.CreateInstanceRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create an Instance record in MariaDB."""
        try:
            self.monitor.counters['create_instance'].inc()
            data = self._instance_from_proto(request.data)
            success = mariadb._direct_create_instance(data)
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateInstance failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetInstance(
        self,
        request: database_pb2.GetInstanceRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetInstanceReply:
        """Get Instance static values from MariaDB."""
        try:
            self.monitor.counters['get_instance'].inc()
            data = mariadb._direct_get_instance(
                UUID(request.uuid))
            if data is None:
                return database_pb2.GetInstanceReply(
                    found=False)
            return database_pb2.GetInstanceReply(
                found=True,
                data=self._instance_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetInstance failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetInstanceReply(
                found=False)

    def GetAllInstances(
        self,
        request: database_pb2.GetAllInstancesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllInstancesReply:
        """Get all Instance static values from MariaDB."""
        try:
            self.monitor.counters['get_all_instances'].inc()
            all_data = mariadb._direct_get_all_instances()
            return database_pb2.GetAllInstancesReply(
                instances=[
                    self._instance_to_proto(d)
                    for d in all_data
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllInstances failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetAllInstancesReply(
                instances=[])

    def FindInstances(
        self,
        request: database_pb2.FindInstancesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.FindInstancesReply:
        """Find Instance records matching filter criteria from MariaDB."""
        try:
            self.monitor.counters['find_instances'].inc()
            criteria = ObjectFilterCriteria(
                states=list(request.criteria.states) or None,
                namespace=(
                    request.criteria.namespace
                    if request.criteria.HasField('namespace') else None),
                name=(
                    request.criteria.name
                    if request.criteria.HasField('name') else None),
            )
            all_data = mariadb._direct_find_instances(criteria)
            return database_pb2.FindInstancesReply(
                instances=[
                    self._instance_to_proto(d)
                    for d in all_data
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database FindInstances failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.FindInstancesReply(instances=[])

    def GetAllInstanceUuids(
        self,
        request: database_pb2.GetAllInstanceUuidsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllInstanceUuidsReply:
        """Get all instance UUIDs from MariaDB."""
        try:
            self.monitor.counters['get_all_instance_uuids'].inc()
            uuids = mariadb._direct_get_all_instance_uuids()
            return database_pb2.GetAllInstanceUuidsReply(
                uuids=uuids)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllInstanceUuids failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetAllInstanceUuidsReply(
                uuids=[])

    def DeleteInstance(
        self,
        request: database_pb2.DeleteInstanceRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete an Instance record from MariaDB."""
        try:
            self.monitor.counters['delete_instance'].inc()
            success = mariadb._direct_delete_instance(
                UUID(request.uuid))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteInstance failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def _instance_from_proto(
            self,
            d: database_pb2.InstanceStaticData
    ) -> InstanceData:
        """Convert a proto InstanceStaticData to model."""
        disk_spec = (json.loads(d.disk_spec_json)
                     if d.disk_spec_json else [])
        requested_placement = (
            json.loads(d.requested_placement_json)
            if d.requested_placement_json else None)
        if not requested_placement:
            requested_placement = None
        video = (json.loads(d.video_json)
                 if d.video_json else {})
        side_channels = (json.loads(d.side_channels_json)
                         if d.side_channels_json else [])
        return InstanceData(
            uuid=UUID(d.uuid),
            cpus=d.cpus,
            disk_spec=disk_spec,
            memory=d.memory,
            name=d.name,
            namespace=d.namespace,
            requested_placement=requested_placement,
            ssh_key=d.ssh_key or None,
            user_data=d.user_data or None,
            video=video,
            uefi=d.uefi,
            configdrive=d.configdrive,
            nvram_template=d.nvram_template or None,
            secure_boot=d.secure_boot,
            machine_type=d.machine_type,
            side_channels=side_channels,
            version=d.version
        )

    def _instance_to_proto(
            self,
            data: InstanceData
    ) -> database_pb2.InstanceStaticData:
        """Convert InstanceData to proto."""
        return database_pb2.InstanceStaticData(
            uuid=str(data.uuid),
            cpus=data.cpus,
            disk_spec_json=json.dumps(data.disk_spec),
            memory=data.memory,
            name=data.name,
            namespace=data.namespace,
            requested_placement_json=json.dumps(
                data.requested_placement),
            ssh_key=data.ssh_key or '',
            user_data=data.user_data or '',
            video_json=json.dumps(data.video),
            uefi=data.uefi,
            configdrive=data.configdrive,
            nvram_template=data.nvram_template or '',
            secure_boot=data.secure_boot,
            machine_type=data.machine_type,
            side_channels_json=json.dumps(
                data.side_channels),
            version=data.version
        )

    # Instance Attributes Operations (MariaDB)
    def CreateInstanceAttributes(
        self,
        request: database_pb2.CreateInstanceAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create Instance attributes in MariaDB."""
        try:
            self.monitor.counters[
                'create_instance_attributes'].inc()
            data = self._instance_attrs_from_proto(
                request.data)
            success = (
                mariadb._direct_create_instance_attributes(
                    data))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateInstanceAttributes failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetInstanceAttributes(
        self,
        request: database_pb2.GetInstanceAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetInstanceAttributesReply:
        """Get Instance attributes from MariaDB."""
        try:
            self.monitor.counters[
                'get_instance_attributes'].inc()
            data = (
                mariadb._direct_get_instance_attributes(
                    UUID(request.uuid)))
            if data is None:
                return (
                    database_pb2
                    .GetInstanceAttributesReply(
                        found=False))
            return (
                database_pb2
                .GetInstanceAttributesReply(
                    found=True,
                    data=self._instance_attrs_to_proto(
                        data)))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetInstanceAttributes failed',
                e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return (
                database_pb2
                .GetInstanceAttributesReply(
                    found=False))

    def UpdateInstanceAttributes(
        self,
        request: database_pb2.UpdateInstanceAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Update Instance attributes in MariaDB."""
        try:
            self.monitor.counters[
                'update_instance_attributes'].inc()
            data = self._instance_attrs_from_proto(
                request.data)
            success = (
                mariadb
                ._direct_update_instance_attributes(
                    data, fields=list(request.fields)))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpdateInstanceAttributes failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteInstanceAttributes(
        self,
        request: database_pb2.DeleteInstanceAttributesRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete Instance attributes from MariaDB."""
        try:
            self.monitor.counters[
                'delete_instance_attributes'].inc()
            success = (
                mariadb
                ._direct_delete_instance_attributes(
                    UUID(request.uuid)))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteInstanceAttributes failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetConsumedPortsForNode(
        self,
        request: database_pb2.GetConsumedPortsForNodeRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetConsumedPortsForNodeReply:
        """Get consumed console/VDI ports for instances on a node."""
        try:
            self.monitor.counters[
                'get_consumed_ports_for_node'].inc()
            ports = (
                mariadb._direct_get_consumed_ports_for_node(
                    request.node_uuid))
            return database_pb2.GetConsumedPortsForNodeReply(
                ports=ports)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetConsumedPortsForNode failed',
                e)
            return database_pb2.GetConsumedPortsForNodeReply(
                ports=[])

    def IsVsockCidInUse(
        self,
        request: database_pb2.IsVsockCidInUseRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.IsVsockCidInUseReply:
        """Check if a vsock CID is in use by any instance.

        Returns in_use=True on error as a fail-safe so the caller
        picks another CID from the 4-billion-wide range rather than
        risking a duplicate allocation on a transient query failure.
        """
        try:
            self.monitor.counters[
                'is_vsock_cid_in_use'].inc()
            in_use = (
                mariadb._direct_is_vsock_cid_in_use(
                    request.cid))
            return database_pb2.IsVsockCidInUseReply(
                in_use=in_use)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database IsVsockCidInUse failed', e)
            return database_pb2.IsVsockCidInUseReply(
                in_use=True)

    # Object Metadata Operations

    def GetObjectMetadata(
        self,
        request: database_pb2.GetObjectMetadataRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetObjectMetadataReply:
        """Get metadata for an object from MariaDB."""
        try:
            self.monitor.counters[
                'get_object_metadata'].inc()
            object_type = ObjectType.from_proto_id(
                request.object_type)
            if object_type is None:
                return database_pb2.GetObjectMetadataReply(
                    found=False)
            data = mariadb._direct_get_object_metadata(
                object_type, request.object_uuid)
            if data is None:
                return database_pb2.GetObjectMetadataReply(
                    found=False)
            return database_pb2.GetObjectMetadataReply(
                found=True,
                metadata_json=(
                    json.dumps(data.metadata)
                    if data.metadata is not None else ''),
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetObjectMetadata failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetObjectMetadataReply(
                found=False)

    def SetMetadata(
        self,
        request: database_pb2.SetMetadataRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Set metadata for an object in MariaDB."""
        try:
            self.monitor.counters['set_metadata'].inc()
            object_type = ObjectType.from_proto_id(
                request.object_type)
            if object_type is None:
                return database_pb2.StatusReply(
                    success=False,
                    error='Invalid object_type')
            metadata_dict = (
                json.loads(request.metadata_json)
                if request.metadata_json else None)
            success = mariadb._direct_set_metadata(
                object_type,
                request.object_uuid,
                metadata_dict)
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database SetMetadata failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteObjectMetadata(
        self,
        request: database_pb2.DeleteObjectMetadataRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete metadata for an object from MariaDB."""
        try:
            self.monitor.counters[
                'delete_object_metadata'].inc()
            object_type = ObjectType.from_proto_id(
                request.object_type)
            if object_type is None:
                return database_pb2.StatusReply(
                    success=False,
                    error='Invalid object_type')
            success = (
                mariadb._direct_delete_object_metadata(
                    object_type, request.object_uuid))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteObjectMetadata failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    # Cluster Operation Target Operations

    def _target_to_proto(
        self,
        data: ClusterOperationTargetData
    ) -> database_pb2.ClusterOperationTargetProto:
        """Convert ClusterOperationTargetData to proto."""
        # Look up the ObjectType by string value to get proto_id
        proto_id = 0
        for ot in ObjectType:
            if ot.value == data.target_object_type:
                proto_id = ot.proto_id
                break
        return database_pb2.ClusterOperationTargetProto(
            operation_uuid=data.operation_uuid,
            operation_type=data.operation_type,
            target_object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                proto_id),
            target_uuid=data.target_uuid,
            sequence_number=data.sequence_number or 0,
            created_at=data.created_at
        )

    def CreateClusterOperationTarget(
        self,
        request: database_pb2.CreateClusterOperationTargetRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Record a cluster operation targeting an object."""
        try:
            self.monitor.counters[
                'create_cluster_operation_target'].inc()
            target_object_type = ObjectType.from_proto_id(
                request.target_object_type)
            if target_object_type is None:
                return database_pb2.StatusReply(
                    success=False,
                    error='Invalid target_object_type')
            success = (
                mariadb._direct_create_cluster_operation_target(
                    request.operation_uuid,
                    request.operation_type,
                    target_object_type,
                    request.target_uuid,
                    request.created_at))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateClusterOperationTarget failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def RecordEventBatch(
        self,
        request: database_pb2.RecordEventBatchRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Record a batch of event log entries in MariaDB."""
        try:
            self.monitor.counters['record_event_batch'].inc()
            records: list[EventRecord] = []
            for entry in request.events:
                objects: list[tuple[str, str]] = []
                for obj in entry.objects:
                    ot = ObjectType.from_proto_id(obj.object_type)
                    if ot is None:
                        return database_pb2.StatusReply(
                            success=False,
                            error='Invalid object_type in event '
                                  f'{entry.event_uuid}')
                    objects.append((ot.value, obj.object_uuid))

                extra = (
                    json.loads(entry.extra_json)
                    if entry.extra_json else None)
                records.append(EventRecord(
                    event_uuid=entry.event_uuid,
                    event_type=entry.event_type,
                    timestamp=entry.timestamp,
                    fqdn=entry.fqdn,
                    duration=(
                        entry.duration if entry.duration != 0.0 else None),
                    message=entry.message,
                    extra=extra,
                    request_id=entry.request_id if entry.request_id else None,
                    objects=objects,
                ))
            success = mariadb._direct_record_event_batch(records)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database RecordEventBatch failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def PruneEvents(
        self,
        request: database_pb2.PruneEventsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.PruneEventsReply:
        """Run the daily events prune sweep."""
        try:
            self.monitor.counters['prune_events'].inc()
            rows = mariadb._direct_prune_events()
            return database_pb2.PruneEventsReply(
                success=True, error='', rows_pruned=rows)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database PruneEvents failed', e)
            return database_pb2.PruneEventsReply(
                success=False, error=str(e), rows_pruned=0)

    def GetObjectEvents(
        self,
        request: database_pb2.GetObjectEventsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetObjectEventsReply:
        """Read events for one (object_type, object_uuid) directly."""
        try:
            self.monitor.counters['get_object_events'].inc()
            ot = ObjectType.from_proto_id(request.object_type)
            if ot is None:
                # Invalid object_type proto id -- return an empty list.
                return database_pb2.GetObjectEventsReply(events=[])
            rows = mariadb._direct_get_object_events(
                object_type=ot.value,
                object_uuid=request.object_uuid,
                limit=request.limit if request.limit else 100,
                event_type=(request.event_type_filter
                            if request.event_type_filter else None))
            proto_rows = []
            for row in rows:
                proto_rows.append(database_pb2.EventReadRowProto(
                    event_uuid=row.event_uuid,
                    event_type=row.event_type,
                    timestamp=row.timestamp,
                    fqdn=row.fqdn,
                    duration=row.duration if row.duration is not None else 0.0,
                    message=row.message,
                    extra_json=(
                        json.dumps(row.extra) if row.extra is not None else ''),
                    request_id=row.request_id if row.request_id is not None else '',
                ))
            return database_pb2.GetObjectEventsReply(events=proto_rows)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetObjectEvents failed', e)
            return database_pb2.GetObjectEventsReply(events=[])

    def DeleteObjectEvents(
        self,
        request: database_pb2.DeleteObjectEventsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete event_objects rows for one (object_type, object_uuid)."""
        try:
            self.monitor.counters['delete_object_events'].inc()
            ot = ObjectType.from_proto_id(request.object_type)
            if ot is None:
                return database_pb2.StatusReply(
                    success=False, error='Invalid object_type')
            mariadb._direct_delete_object_events(
                object_type=ot.value, object_uuid=request.object_uuid)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteObjectEvents failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetClusterOperationTarget(
        self,
        request: database_pb2.GetClusterOperationTargetRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetClusterOperationTargetReply:
        """Get a single cluster operation target by operation UUID."""
        try:
            self.monitor.counters[
                'get_cluster_operation_target'].inc()
            data = (
                mariadb._direct_get_cluster_operation_target(
                    request.operation_uuid))
            if data is None:
                return database_pb2.GetClusterOperationTargetReply(
                    found=False)
            return database_pb2.GetClusterOperationTargetReply(
                found=True,
                target=self._target_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetClusterOperationTarget failed',
                e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetClusterOperationTargetReply(
                found=False)

    def GetClusterOperationTargetsForObject(
        self,
        request: database_pb2.GetClusterOperationTargetsForObjectRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetClusterOperationTargetsForObjectReply:
        """Get all cluster operation targets for an object."""
        try:
            self.monitor.counters[
                'get_cluster_operation_targets_for_object'].inc()
            target_object_type = ObjectType.from_proto_id(
                request.target_object_type)
            if target_object_type is None:
                return database_pb2.GetClusterOperationTargetsForObjectReply(
                    targets=[])
            data_list = (
                mariadb
                ._direct_get_cluster_operation_targets_for_object(
                    target_object_type,
                    request.target_uuid))
            return database_pb2.GetClusterOperationTargetsForObjectReply(
                targets=[
                    self._target_to_proto(d)
                    for d in data_list
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetClusterOperationTargetsForObject'
                ' failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetClusterOperationTargetsForObjectReply(
                targets=[])

    def GetLatestClusterOperationTarget(
        self,
        request: database_pb2.GetLatestClusterOperationTargetRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetClusterOperationTargetReply:
        """Get the most recent cluster operation target for an object."""
        try:
            self.monitor.counters[
                'get_latest_cluster_operation_target'].inc()
            target_object_type = ObjectType.from_proto_id(
                request.target_object_type)
            if target_object_type is None:
                return database_pb2.GetClusterOperationTargetReply(
                    found=False)
            data = (
                mariadb
                ._direct_get_latest_cluster_operation_target(
                    target_object_type,
                    request.target_uuid))
            if data is None:
                return database_pb2.GetClusterOperationTargetReply(
                    found=False)
            return database_pb2.GetClusterOperationTargetReply(
                found=True,
                target=self._target_to_proto(data))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetLatestClusterOperationTarget'
                ' failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetClusterOperationTargetReply(
                found=False)

    def HasPendingClusterOperationTarget(
        self,
        request: database_pb2.HasPendingClusterOperationTargetRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.HasPendingClusterOperationTargetReply:
        """True if any in-flight cluster operation targets this object."""
        try:
            self.monitor.counters[
                'has_pending_cluster_operation_target'].inc()
            target_object_type = ObjectType.from_proto_id(
                request.target_object_type)
            if target_object_type is None:
                # Unknown object type — fail closed.
                return database_pb2.HasPendingClusterOperationTargetReply(
                    pending=True)
            pending = (
                mariadb
                ._direct_has_pending_cluster_operation_target(
                    target_object_type,
                    request.target_uuid))
            return database_pb2.HasPendingClusterOperationTargetReply(
                pending=pending)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database HasPendingClusterOperationTarget'
                ' failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            # Fail closed: treat unknown as in-flight.
            return database_pb2.HasPendingClusterOperationTargetReply(
                pending=True)

    def GetRecentTerminalOpStatesForTarget(
        self,
        request: database_pb2.GetRecentTerminalOpStatesForTargetRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetRecentTerminalOpStatesForTargetReply:
        """Return recent terminal op states for an object."""
        try:
            self.monitor.counters[
                'get_recent_terminal_op_states_for_target'].inc()
            target_object_type = ObjectType.from_proto_id(
                request.target_object_type)
            if target_object_type is None:
                return database_pb2.GetRecentTerminalOpStatesForTargetReply(
                    entries=[])
            op_type = request.op_type if request.op_type else None
            rows = (
                mariadb
                ._direct_get_recent_terminal_op_states_for_target(
                    target_object_type,
                    request.target_uuid,
                    request.limit,
                    op_type))
            return database_pb2.GetRecentTerminalOpStatesForTargetReply(
                entries=[
                    database_pb2.TerminalOpState(
                        op_uuid=op_uuid,
                        state_value=state_value,
                        update_time=update_time)
                    for op_uuid, state_value, update_time in rows
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetRecentTerminalOpStatesForTarget'
                ' failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetRecentTerminalOpStatesForTargetReply(
                entries=[])

    def DeleteClusterOperationTarget(
        self,
        request: database_pb2.DeleteClusterOperationTargetRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a single cluster operation target."""
        try:
            self.monitor.counters[
                'delete_cluster_operation_target'].inc()
            success = (
                mariadb._direct_delete_cluster_operation_target(
                    request.operation_uuid))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteClusterOperationTarget failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteClusterOperationTargetsForObject(
        self,
        request: database_pb2.DeleteClusterOperationTargetsForObjectRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete all cluster operation targets for an object."""
        try:
            self.monitor.counters[
                'delete_cluster_operation_targets_for_object'
            ].inc()
            target_object_type = ObjectType.from_proto_id(
                request.target_object_type)
            if target_object_type is None:
                return database_pb2.StatusReply(
                    success=False,
                    error='Invalid target_object_type')
            success = (
                mariadb
                ._direct_delete_cluster_operation_targets_for_object(
                    target_object_type,
                    request.target_uuid))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteClusterOperationTargetsForObject'
                ' failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def DeleteStaleClusterOperationTargets(
        self,
        request: database_pb2.DeleteStaleClusterOperationTargetsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.DeleteCountReply:
        """Prune cluster_operation_targets rows for completed operations."""
        try:
            self.monitor.counters[
                'delete_stale_cluster_operation_targets'].inc()
            count = (
                mariadb
                ._direct_delete_stale_cluster_operation_targets(
                    request.older_than))
            return database_pb2.DeleteCountReply(count=count)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteStaleClusterOperationTargets failed',
                e)
            return database_pb2.DeleteCountReply(count=0)

    # Node Metrics Operations (MariaDB)

    def UpsertNodeMetrics(
        self,
        request: database_pb2.UpsertNodeMetricsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Upsert a node metrics record in MariaDB."""
        try:
            self.monitor.counters['upsert_node_metrics'].inc()
            success = mariadb._direct_upsert_node_metrics(
                UUID(request.data.node_uuid),
                request.data.fqdn,
                request.data.timestamp,
                json.loads(request.data.metrics_json)
                if request.data.metrics_json else {}
            )
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database UpsertNodeMetrics failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetNodeMetrics(
        self,
        request: database_pb2.GetNodeMetricsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetNodeMetricsReply:
        """Get node metrics from MariaDB."""
        try:
            self.monitor.counters['get_node_metrics'].inc()
            data = mariadb._direct_get_node_metrics(
                UUID(request.node_uuid))
            if data is None:
                return database_pb2.GetNodeMetricsReply(
                    found=False)
            return database_pb2.GetNodeMetricsReply(
                found=True,
                data=database_pb2.NodeMetricsData(
                    node_uuid=data['node_uuid'],
                    fqdn=data['fqdn'],
                    timestamp=data['timestamp'],
                    metrics_json=json.dumps(data['metrics'])
                )
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetNodeMetrics failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetNodeMetricsReply(
                found=False)

    def GetAllNodeMetrics(
        self,
        request: database_pb2.GetAllNodeMetricsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetAllNodeMetricsReply:
        """Get all node metrics from MariaDB."""
        try:
            self.monitor.counters['get_all_node_metrics'].inc()
            items = mariadb._direct_get_all_node_metrics()
            return database_pb2.GetAllNodeMetricsReply(
                items=[
                    database_pb2.NodeMetricsData(
                        node_uuid=d['node_uuid'],
                        fqdn=d['fqdn'],
                        timestamp=d['timestamp'],
                        metrics_json=json.dumps(
                            d['metrics'])
                    )
                    for d in items
                ]
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetAllNodeMetrics failed', e)
            return database_pb2.GetAllNodeMetricsReply(
                items=[])

    def DeleteNodeMetrics(
        self,
        request: database_pb2.DeleteNodeMetricsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a node metrics record from MariaDB."""
        try:
            self.monitor.counters['delete_node_metrics'].inc()
            success = mariadb._direct_delete_node_metrics(
                UUID(request.node_uuid))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteNodeMetrics failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    # Cluster Operations (MariaDB)

    def CreateClusterOperation(
        self,
        request: database_pb2.CreateClusterOperationRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Insert a cluster operation header in MariaDB."""
        try:
            self.monitor.counters['create_cluster_operation'].inc()
            metadata = (
                json.loads(request.data.metadata_json)
                if request.data.metadata_json else {}
            )
            success = mariadb._direct_create_cluster_operation(
                UUID(request.data.uuid),
                request.data.operation_type,
                metadata,
                request.data.created_at,
            )
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateClusterOperation failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetClusterOperation(
        self,
        request: database_pb2.GetClusterOperationRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetClusterOperationReply:
        """Get a cluster operation header from MariaDB."""
        try:
            self.monitor.counters['get_cluster_operation'].inc()
            data = mariadb._direct_get_cluster_operation(
                UUID(request.uuid))
            if data is None:
                return database_pb2.GetClusterOperationReply(
                    found=False)
            return database_pb2.GetClusterOperationReply(
                found=True,
                data=self._cluster_operation_to_proto(data),
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetClusterOperation failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetClusterOperationReply(
                found=False)

    def GetClusterOperationsByNode(
        self,
        request: database_pb2.GetClusterOperationsByNodeRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetClusterOperationsByNodeReply:
        """Get all cluster operation headers targeting a node."""
        try:
            self.monitor.counters[
                'get_cluster_operations_by_node'].inc()
            items = mariadb._direct_get_cluster_operations_by_node(
                UUID(request.node_uuid))
            return database_pb2.GetClusterOperationsByNodeReply(
                items=[
                    self._cluster_operation_to_proto(d)
                    for d in items
                ]
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetClusterOperationsByNode failed', e)
            return database_pb2.GetClusterOperationsByNodeReply(
                items=[])

    def ListClusterOperationsForTarget(
        self,
        request: database_pb2.ListClusterOperationsForTargetRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ListClusterOperationsForTargetReply:
        """List cluster operation headers targeting an object, newest first."""
        try:
            self.monitor.counters[
                'list_cluster_operations_for_target'].inc()
            target_object_type = ObjectType.from_proto_id(
                request.target_object_type)
            if target_object_type is None:
                return database_pb2.ListClusterOperationsForTargetReply(
                    items=[])
            items = (
                mariadb
                ._direct_list_cluster_operations_for_target(
                    target_object_type,
                    request.target_uuid))
            return database_pb2.ListClusterOperationsForTargetReply(
                items=[
                    self._cluster_operation_to_proto(d)
                    for d in items
                ])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ListClusterOperationsForTarget failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.ListClusterOperationsForTargetReply(
                items=[])

    def DeleteClusterOperation(
        self,
        request: database_pb2.DeleteClusterOperationRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a cluster operation header from MariaDB."""
        try:
            self.monitor.counters['delete_cluster_operation'].inc()
            success = mariadb._direct_delete_cluster_operation(
                UUID(request.uuid))
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteClusterOperation failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def CreateAndEnqueueClusterOperation(
        self,
        request: database_pb2.CreateAndEnqueueClusterOperationRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Atomically create a cluster operation and enqueue its work item.

        Writes cluster_operations, object_states and work_queue in a
        single MariaDB transaction. Audit events are not written by
        this RPC -- callers emit them via the eventlog service after
        the RPC returns successfully.
        """
        try:
            self.monitor.counters[
                'create_and_enqueue_cluster_operation'].inc()
            metadata = (
                json.loads(request.metadata_json)
                if request.metadata_json else {}
            )
            targets: list[tuple[ObjectType, str]] = []
            for t in request.targets:
                ot = ObjectType.from_proto_id(t.target_object_type)
                if ot is None:
                    return database_pb2.StatusReply(
                        success=False,
                        error=(
                            f'Invalid target_object_type '
                            f'{t.target_object_type}'))
                targets.append((ot, t.target_uuid))
            success, error = (
                mariadb
                ._direct_create_and_enqueue_cluster_operation(
                    UUID(request.uuid),
                    request.operation_type,
                    metadata,
                    request.created_at,
                    request.queue_name,
                    request.delay,
                    targets,
                )
            )
            return database_pb2.StatusReply(
                success=success, error=error)
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateAndEnqueueClusterOperation failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def SetClusterOperationError(
        self,
        request: database_pb2.SetClusterOperationErrorRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Persist an ErrorReport for a failed cluster operation."""
        try:
            self.monitor.counters['set_cluster_operation_error'].inc()
            report = ErrorReport.model_validate(
                json.loads(request.error_report_json))
            success = mariadb._direct_set_cluster_operation_error(
                UUID(request.op_uuid),
                report,
                request.created_at,
            )
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database SetClusterOperationError failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def GetClusterOperationError(
        self,
        request: database_pb2.GetClusterOperationErrorRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetClusterOperationErrorReply:
        """Read the ErrorReport for a cluster operation."""
        try:
            self.monitor.counters['get_cluster_operation_error'].inc()
            report = mariadb._direct_get_cluster_operation_error(
                UUID(request.op_uuid))
            if report is None:
                return database_pb2.GetClusterOperationErrorReply(
                    found=False)
            return database_pb2.GetClusterOperationErrorReply(
                found=True,
                error_report_json=json.dumps(
                    report.model_dump(mode='json')),
            )
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetClusterOperationError failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetClusterOperationErrorReply(
                found=False)

    def DeleteClusterOperationError(
        self,
        request: database_pb2.DeleteClusterOperationErrorRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete the ErrorReport row for a cluster operation."""
        try:
            self.monitor.counters['delete_cluster_operation_error'].inc()
            success = mariadb._direct_delete_cluster_operation_error(
                UUID(request.op_uuid))
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database DeleteClusterOperationError failed', e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

    def RecordFederatedExchange(
        self,
        request: database_pb2.RecordFederatedExchangeRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.RecordFederatedExchangeReply:
        """Claim a (token, rule) pair for a federated exchange."""
        try:
            self.monitor.counters['record_federated_exchange'].inc()
            recorded = mariadb._direct_record_federated_exchange(
                request.token_id,
                UUID(request.rule_uuid),
                request.expires_at)
            return database_pb2.RecordFederatedExchangeReply(
                recorded=recorded, error='', ok=True)
        except Exception as e:
            # Reported in the reply body rather than as an RPC error so
            # the client can tell "we could not find out" apart from
            # "already claimed". Both refuse the exchange, but only one
            # of them means the database is in trouble.
            #
            # ok is set on the success path only, so it stays false here
            # whatever str(e) turns out to be.
            util_exceptions.ignore_exception(
                'database RecordFederatedExchange failed', e)
            return database_pb2.RecordFederatedExchangeReply(
                recorded=False, error=str(e), ok=False)

    def CountFederatedAttempt(
        self,
        request: database_pb2.CountFederatedAttemptRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.CountFederatedAttemptReply:
        """Count one federated exchange attempt from a source address."""
        try:
            self.monitor.counters['count_federated_attempt'].inc()
            attempts = mariadb._direct_count_federated_attempt(
                request.source, request.window_start)
            return database_pb2.CountFederatedAttemptReply(
                attempts=attempts, error='', ok=True)
        except Exception as e:
            # attempts=0 is not a safe default here -- read as a real
            # count it means "under the limit, allow" -- so the client
            # decides on ok rather than on this reply having managed to
            # produce a non-empty error string.
            util_exceptions.ignore_exception(
                'database CountFederatedAttempt failed', e)
            return database_pb2.CountFederatedAttemptReply(
                attempts=0, error=str(e), ok=False)

    def ReapFederationReplay(
        self,
        request: database_pb2.ReapFederationReplayRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ReapFederationReply:
        """Delete replay rows for tokens which have expired."""
        try:
            self.monitor.counters['reap_federation_replay'].inc()
            return database_pb2.ReapFederationReply(
                removed=mariadb._direct_reap_federation_replay(
                    request.cutoff))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ReapFederationReplay failed', e)
            return database_pb2.ReapFederationReply(removed=0)

    def ReapFederationRateLimits(
        self,
        request: database_pb2.ReapFederationRateLimitsRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ReapFederationReply:
        """Delete rate limit rows for windows which have closed."""
        try:
            self.monitor.counters['reap_federation_rate_limits'].inc()
            return database_pb2.ReapFederationReply(
                removed=mariadb._direct_reap_federation_rate_limits(
                    request.cutoff))
        except Exception as e:
            util_exceptions.ignore_exception(
                'database ReapFederationRateLimits failed', e)
            return database_pb2.ReapFederationReply(removed=0)

    def _cluster_operation_to_proto(
            self,
            data: dict[str, Any]
    ) -> 'database_pb2.ClusterOperationData':
        """Build a ClusterOperationData proto from a cluster_operations
        row dict. The full dict is JSON-serialized into metadata_json so
        the client sees exactly the same shape _direct_get_cluster_operation
        returned."""
        return database_pb2.ClusterOperationData(
            uuid=data['uuid'],
            operation_type=data['operation_type'],
            created_at=data['created_at'],
            metadata_json=json.dumps(data),
        )

    def _instance_attrs_from_proto(
            self,
            d: database_pb2.InstanceAttributesProto
    ) -> InstanceAttributesData:
        """Convert a proto InstanceAttributesProto to model."""
        def _parse(val: str) -> Any:
            return json.loads(val) if val else None

        return InstanceAttributesData(
            uuid=UUID(d.uuid),
            placement=_parse(d.placement_json),
            power_state=_parse(d.power_state_json),
            ports=_parse(d.ports_json),
            enforced_deletes=_parse(
                d.enforced_deletes_json),
            block_devices=_parse(
                d.block_devices_json),
            agent_state=_parse(d.agent_state_json),
            agent_attributes=_parse(
                d.agent_attributes_json),
            agent_operations=_parse(
                d.agent_operations_json),
            kvm_pid=d.kvm_pid or None,
            error_message=d.error_message or None,
            vsock_cids=_parse(d.vsock_cids_json),
        )

    def _instance_attrs_to_proto(
            self,
            data: InstanceAttributesData
    ) -> database_pb2.InstanceAttributesProto:
        """Convert InstanceAttributesData to proto."""
        return database_pb2.InstanceAttributesProto(
            uuid=str(data.uuid),
            placement_json=json.dumps(data.placement),
            power_state_json=json.dumps(
                data.power_state),
            ports_json=json.dumps(data.ports),
            enforced_deletes_json=json.dumps(
                data.enforced_deletes),
            block_devices_json=json.dumps(
                data.block_devices),
            agent_state_json=json.dumps(
                data.agent_state),
            agent_attributes_json=json.dumps(
                data.agent_attributes),
            agent_operations_json=json.dumps(
                data.agent_operations),
            kvm_pid=data.kvm_pid or 0,
            error_message=data.error_message or '',
            vsock_cids_json=json.dumps(data.vsock_cids))


# Per-caller request attribution. This is additive: the existing unlabelled
# database_<op>_total counters (and every dashboard/alert on them) are left
# untouched. A single server interceptor increments this one labelled counter
# so we can answer "which daemon drives operation X". See
# PLAN-database-load-reduction-phase-04-attribution.md.
DATABASE_REQUESTS = Counter(
    'database_requests_total',
    'sf-database gRPC requests, by operation and calling daemon.',
    ['operation', 'caller_daemon']
)


def _method_to_operation(method: str) -> str:
    """Return the RPC name from a gRPC method path.

    ``/shakenfist.protos.DatabaseService/GetNode`` -> ``GetNode``. The raw
    PascalCase name is used as-is rather than converted to snake_case: several
    RPCs carry acronym runs (GetIPAM, GetDnsMasq) that no algorithmic
    conversion maps back to the hand-written counter names cleanly, so the
    unambiguous wire name is the label.
    """
    return method.rsplit('/', 1)[-1] or 'unknown'


def _caller_from_metadata(metadata: Any) -> str:
    """Read caller-daemon from invocation metadata, defaulting to unknown.

    The value is client-asserted and this port is unauthenticated, so
    anything not in KNOWN_CALLERS is reported as 'unknown' rather than
    becoming a label of its own. An unfiltered label here is an unbounded
    prometheus cardinality leak -- every distinct value creates a child
    which is retained for the life of the process -- and the exposition
    it corrupts is what sf-ctl database-load, the drop-in alerting rules
    and the functional CI budget check all parse.
    """
    for key, value in (metadata or ()):
        if key == 'caller-daemon':
            caller = str(value)
            return caller if caller in KNOWN_CALLERS else 'unknown'
    return 'unknown'


class _CallerMetricsInterceptor(grpc.ServerInterceptor):  # type: ignore[type-arg]  # noqa: E501
    """Count every RPC by operation and calling daemon.

    Additive and best-effort: it touches no handler and, on any failure,
    proceeds with the call rather than disrupting it. Health-check RPCs are
    skipped to keep the operation cardinality bounded to DatabaseService.
    """

    def intercept_service(self, continuation: Any,
                          handler_call_details: Any) -> Any:
        try:
            method = handler_call_details.method or ''
            if not method.startswith('/grpc.health.v1.Health/'):
                DATABASE_REQUESTS.labels(
                    operation=_method_to_operation(method),
                    caller_daemon=_caller_from_metadata(
                        handler_call_details.invocation_metadata)
                ).inc()
        except Exception:
            pass
        return continuation(handler_call_details)


class Monitor(daemon.WorkerPoolDaemon):
    """Background monitor for the database daemon.

    The database daemon is special because it provides database access to other
    daemons. This means we must use direct etcd access for our own startup and
    shutdown recording, otherwise we'd have a chicken-and-egg problem.
    """

    def __init__(self, id: str,
                 health_servicer: health.HealthServicer | None = None) -> None:
        super().__init__(id)
        self.counters: dict[str, Counter] = {}

        # The gRPC health servicer whose '' (overall server health) status we
        # drive from live MariaDB reachability in _run_inner. Optional so other
        # construction sites and tests don't need to supply one.
        self.health_servicer = health_servicer
        self._last_health_status: int | None = None

        # Create counters for all operations
        operations = [
            'enqueue', 'dequeue', 'resolve', 'get_queue_length',
            'restart_queue', 'list_stuck_work_queue_rows',
            'clear_work_queue_claim', 'delete_work_queue_row',
            'claim_coalescible_siblings',
            'find_existing_coalescible_op',
            'claim_coalescible_siblings_v2',
            'find_existing_coalescible_op_v2',
            'acquire_lock', 'release_lock', 'refresh_lock', 'get_lock_holder',
            'clear_stale_locks', 'get_existing_locks',
            'get_cluster_config', 'set_cluster_config',
            'delete_cluster_config',
            'record_event_batch', 'prune_events',
            'get_object_events', 'delete_object_events',
            # MariaDB state operations
            'get_object_state', 'set_object_state', 'delete_object_state',
            'get_objects_by_state',
            # Orphan reconciliation
            'delete_orphaned_object_states', 'get_stateless_object_uuids',
            'delete_orphaned_artifact_attributes',
            # MariaDB IPAM operations
            'reserve_address', 'release_address', 'get_reservation',
            'get_reservations_for_ipam', 'delete_reservation',
            'delete_reservations_for_ipam', 'release_haloed_addresses',
            'get_addresses_in_use',
            # MariaDB upload operations
            'create_upload', 'get_upload', 'get_uploads', 'delete_upload',
            'update_upload',
            # MariaDB blob operations
            'create_blob', 'get_blob', 'get_all_blob_uuids', 'delete_blob',
            'update_blob',
            # MariaDB DnsMasq operations
            'create_dnsmasq', 'get_dnsmasq', 'get_dnsmasqs', 'delete_dnsmasq',
            'update_dnsmasq',
            # MariaDB object reference operations
            'record_relationship', 'remove_relationship', 'get_references_to',
            'get_references_from', 'count_references_to',
            'remove_all_references_from', 'update_last_active',
            'get_stale_references',
            # MariaDB blob hash operations
            'upsert_blob_hash', 'get_blob_hashes', 'find_blob_by_hash',
            'get_stale_blob_hashes', 'delete_blob_hashes',
            # MariaDB blob transfer operations
            'create_blob_transfer', 'get_blob_transfer',
            'get_blob_transfers_for_node', 'get_blob_transfers_for_blob',
            'update_blob_transfer', 'delete_blob_transfer',
            'delete_stale_transfers', 'delete_blob_transfers_for_blob',
            # MariaDB blob attributes operations
            'create_blob_attributes', 'get_blob_attributes',
            'update_blob_attributes', 'update_blob_last_used',
            'delete_blob_attributes', 'get_expired_blob_uuids',
            'get_stale_transcoded_blob_uuids',
            # MariaDB node operations
            'create_node', 'get_node', 'get_node_by_fqdn',
            'get_all_node_uuids', 'delete_node', 'update_node',
            # MariaDB node attributes operations
            'create_node_attributes', 'get_node_attributes',
            'update_node_attributes', 'delete_node_attributes',
            # MariaDB namespace operations
            'create_namespace', 'get_namespace',
            'get_all_namespace_names', 'delete_namespace',
            # MariaDB namespace attributes operations
            'create_namespace_attributes',
            'get_namespace_attributes',
            'update_namespace_attributes',
            'delete_namespace_attributes',
            # MariaDB namespace key operations
            'create_namespace_key', 'get_namespace_key',
            'get_namespace_key_by_name',
            # MariaDB trusted issuer operations
            'create_trusted_issuer', 'get_trusted_issuer',
            'get_trusted_issuer_by_name', 'get_all_trusted_issuers',
            'delete_trusted_issuer',
            'create_trusted_issuer_attributes',
            'get_trusted_issuer_attributes',
            'update_trusted_issuer_attributes',
            'delete_trusted_issuer_attributes',
            'create_mapping_rule', 'get_mapping_rule',
            'get_mapping_rule_by_name', 'get_mapping_rules_in_namespace',
            'get_all_mapping_rules', 'delete_mapping_rule',
            'create_mapping_rule_attributes',
            'get_mapping_rule_attributes',
            'update_mapping_rule_attributes',
            'delete_mapping_rule_attributes',
            'find_namespace_keys', 'delete_namespace_key',
            'delete_expired_namespace_keys',
            # MariaDB namespace key attributes operations
            'create_namespace_key_attributes',
            'get_namespace_key_attributes',
            'update_namespace_key_attributes',
            'delete_namespace_key_attributes',
            # MariaDB network interface operations
            'create_network_interface', 'get_network_interface',
            'get_network_interfaces_by_instance',
            'get_network_interfaces_by_network',
            'get_all_network_interfaces',
            'delete_network_interface', 'update_network_interface',
            # MariaDB network interface attributes operations
            'create_network_interface_attributes',
            'get_network_interface_attributes',
            'update_network_interface_attributes',
            'delete_network_interface_attributes',
            # MariaDB IPAM operations
            'create_ipam', 'get_ipam',
            'delete_ipam', 'update_ipam',
            # MariaDB network operations
            'create_network', 'get_network',
            'get_all_networks', 'delete_network',
            # MariaDB network attributes operations
            'create_network_attributes',
            'get_network_attributes',
            'get_network_floating_gateways',
            'update_network_attributes',
            'delete_network_attributes',
            # MariaDB artifact operations
            'create_artifact', 'get_artifact', 'get_all_artifacts',
            'update_artifact', 'delete_artifact',
            # MariaDB artifact attributes operations
            'create_artifact_attributes', 'get_artifact_attributes',
            'update_artifact_attributes', 'delete_artifact_attributes',
            # MariaDB artifact index operations
            'create_artifact_index', 'get_artifact_index',
            'get_all_artifact_indexes', 'delete_artifact_index',
            'delete_all_artifact_indexes',
            # MariaDB agent operation operations
            'create_agent_operation', 'get_agent_operation',
            'delete_agent_operation',
            # MariaDB agent operation attributes operations
            'create_agent_operation_attributes',
            'get_agent_operation_attributes',
            'update_agent_operation_attributes',
            'delete_agent_operation_attributes',
            # MariaDB instance operations
            'create_instance', 'get_instance',
            'get_all_instances', 'get_all_instance_uuids',
            'delete_instance',
            # MariaDB instance attributes operations
            'create_instance_attributes',
            'get_instance_attributes',
            'update_instance_attributes',
            'delete_instance_attributes',
            'get_consumed_ports_for_node',
            'is_vsock_cid_in_use',
            # MariaDB object metadata operations
            'get_object_metadata', 'set_metadata',
            'delete_object_metadata',
            # MariaDB cluster operation target operations
            'create_cluster_operation_target',
            'get_cluster_operation_target',
            'get_cluster_operation_targets_for_object',
            'get_latest_cluster_operation_target',
            'has_pending_cluster_operation_target',
            'get_recent_terminal_op_states_for_target',
            'delete_cluster_operation_target',
            'delete_cluster_operation_targets_for_object',
            'delete_stale_cluster_operation_targets',
            # MariaDB cluster operation operations
            'create_cluster_operation', 'get_cluster_operation',
            'get_cluster_operations_by_node',
            'list_cluster_operations_for_target',
            'delete_cluster_operation',
            'create_and_enqueue_cluster_operation',
            # MariaDB cluster operation error operations
            'set_cluster_operation_error',
            'get_cluster_operation_error',
            'delete_cluster_operation_error',
            # MariaDB federation abuse resistance operations
            'record_federated_exchange',
            'count_federated_attempt',
            'reap_federation_replay',
            'reap_federation_rate_limits',
            # MariaDB find (filter-pushdown) operations
            'find_artifacts', 'find_instances', 'find_networks',
            'find_network_interfaces', 'find_network_vxids',
            'get_node_instance_vxids',
            # MariaDB node metrics operations
            'upsert_node_metrics', 'get_node_metrics',
            'get_all_node_metrics', 'delete_node_metrics',
            # MariaDB node daemon state operations
            'set_node_daemon_state', 'get_node_daemon_state',
            'get_all_node_daemon_states', 'delete_node_daemon_state',
            # MariaDB scheduler capacity operations
            'reconcile_scheduler_capacity',
            'admit_instance_placement', 'release_instance_placement',
            'get_scheduler_node_capacity',
            # Placement admissions which ran without a capacity guard
            # (P7): a node or cluster with no capacity row fails open, so
            # this counter is how "the guard is working" is told apart
            # from "the guard is not running at all". A node the
            # reconciler never sizes admits unguarded indefinitely, not
            # just for one mid-upgrade reconcile period, so this rate
            # staying above zero is a standing alert rather than a
            # transient.
            'admit_instance_placement_unguarded',
            # MariaDB namespace claim operations
            'create_namespace_claim', 'get_namespace_claim',
            'get_namespace_claims', 'update_namespace_claim',
            'delete_namespace_claim',
        ]
        for op in operations:
            self.counters[op] = Counter(
                f'database_{op}_total',
                f'Number of {op} operations'
            )

        self.events_rows_gauge = Gauge(
            'database_events_rows',
            'Current row count in the events table.'
        )

        # Bind the Prometheus metrics server on this node's mesh IP rather
        # than listing it in MARIADB_GATEWAY_HOSTS. The two are the same
        # address today but will diverge once sf-database can run as a
        # tier of several instances; clients scrape via their own discovery.
        start_http_server(config.MARIADB_GATEWAY_METRICS_PORT, addr=config.NODE_MESH_IP)

    def record_start(self) -> None:
        # The database daemon records its own startup. Events flow into the
        # local spool and are picked up by the drainer when it starts.
        n = Node.from_db(config.NODE_NAME)
        if n:
            n.set_daemon_state(
                self.daemon_name,
                Node.DAEMON_STATE_RUNNING)
            n.add_event(
                EVENT_TYPE_AUDIT,
                f'{self.daemon_name} daemon starting')
        send_systemd_ready()

    def record_exit(self) -> None:
        # The database daemon records its own shutdown.
        n = Node.from_db(config.NODE_NAME)
        if n:
            try:
                n.set_daemon_state(
                    self.daemon_name,
                    Node.DAEMON_STATE_STOPPED)
            except InvalidStateException as e:
                if not str(e).startswith(
                        'Invalid state change from '
                        'stopping to degraded'):
                    raise e
            n.add_event(
                EVENT_TYPE_AUDIT,
                f'{self.daemon_name} daemon stopped')
        send_systemd_status('Terminated')

    def _update_health(self) -> None:
        # Drive the gRPC health protocol's overall ('') status from live
        # MariaDB reachability. check_reachable() is bounded and never raises;
        # the in-memory .set() is safe but wrapped defensively so a health
        # update can never break the daemon's main loop. We only log on a
        # SERVING<->NOT_SERVING transition to avoid spamming every ~10s tick.
        if self.health_servicer is None:
            return

        try:
            reachable = mariadb.check_reachable()
            status = (health_pb2.HealthCheckResponse.SERVING if reachable
                      else health_pb2.HealthCheckResponse.NOT_SERVING)

            if status != self._last_health_status:
                if not reachable:
                    LOG.warning('sf-database health: MariaDB became '
                                'unreachable, reporting NOT_SERVING')
                elif self._last_health_status is None:
                    LOG.info('sf-database health: MariaDB reachable, '
                             'reporting SERVING')
                else:
                    LOG.info('sf-database health: MariaDB reachable again, '
                             'reporting SERVING')
                self._last_health_status = status

            self.health_servicer.set('', status)
        except Exception as e:
            LOG.warning(f'sf-database health update failed: {e}')

    def _run_inner(self) -> None:
        refresh_tick = 0
        while daemon.check_abort_path(self.abort_path):
            try:
                # The database daemon doesn't have background work to do,
                # it just serves gRPC requests. We check health periodically
                # and refresh the events row-count gauge every ~60s (every
                # 6 ticks of the 10s idle). Everything in this loop must be
                # bounded well inside the unit's WatchdogSec: the watchdog is
                # only petted from idle(), so an unbounded database call here
                # gets the daemon SIGABRT-killed when MariaDB stalls -- on
                # both gateways at once, since they run the same loop against
                # the same server (issue 3586).
                self._update_health()

                refresh_tick += 1
                if refresh_tick % 6 == 0:
                    try:
                        self.events_rows_gauge.set(
                            mariadb._direct_get_events_count())
                    except Exception as e:
                        LOG.warning(
                            f'events row-count gauge refresh failed: {e}')
                self.idle(10)
            except Exception as e:
                util_exceptions.ignore_exception('database daemon', e)

            self.check_daemon_state()


def drain_and_stop(server: Any, health_servicer: Any) -> None:
    """Drain in-flight RPCs and stop the gRPC server on shutdown.

    The ordering here is the load-bearing invariant: health flips to
    NOT_SERVING *before* the server stops accepting calls, so external
    Check-based monitoring (including the deploy's gateway-health roll
    gate) sees the drain rather than an abrupt disappearance. SF's own
    clients fail over via connectivity state and keepalives, not the
    health protocol.
    """
    health_servicer.set('', health_pb2.HealthCheckResponse.NOT_SERVING)

    # Graceful drain: stop accepting new RPCs but let the in-flight ones
    # finish (up to DATABASE_DRAIN_GRACE seconds) rather than cutting them
    # at one second. SF clients fail over via round_robin, but an in-flight
    # RPC on this gateway that gets cut mid-call still surfaces on the
    # client as a spurious UNAVAILABLE / CANCELLED -- exactly the per-deploy
    # noise #3430 is about. The grace is a cap, not a fixed delay: stop()
    # returns as soon as the last in-flight call ends, and it stays below
    # the unit's TimeoutStopSec (30s) so systemd never SIGKILLs mid-drain.
    server.stop(config.DATABASE_DRAIN_GRACE).wait()


def main() -> None:
    util_exceptions.install_exception_tracking()

    # Claim this process' caller identity before anything which might touch
    # MariaDB. _use_database_service() consults it, and an unset identity
    # reads as 'unknown', which is deliberately not in
    # DIRECT_MARIADB_CALLERS -- so until this runs sf-database would route
    # its own database access through the gRPC tier, which on a single node
    # deployment is this very process, before it is listening. The window
    # is real rather than theoretical: write_pid_file() below starts the
    # eventlog drainer thread, whose record_event_batch() flush is a
    # dispatching call. Daemon.__init__ sets the same identity again later;
    # this is the same value, just claimed early enough to matter.
    set_caller_identity('database')

    daemon.write_pid_file('database')

    # NOTE: do not add faulthandler.dump_traceback_later() here. Its
    # watchdog thread walks every thread's frame stack without holding
    # the GIL, and under sf-database's 64-thread load it dereferences
    # frames mid-mutation -- we measured five SIGSEGVs across three CI
    # jobs in a single merge run before removing it. For on-demand
    # thread dumps use py-spy from outside the process instead.

    # MariaDB is required for the database service. Abort early with a clear
    # error message if it's not configured.
    if not config.MARIADB_HOST:
        LOG.error('MariaDB is not configured. The database service requires '
                  'MARIADB_HOST to be set. Aborting.')
        raise SystemExit(1)

    # Verify the MariaDB server is compatible before serving any request.
    # Schema creation and migrations are operator-driven via
    # `sf-ctl ensure-mariadb-schema`; sf-database refuses to start if the
    # schema is not at the version this build expects.
    engine = mariadb._get_engine()
    try:
        mariadb.verify_mariadb_compat(engine)
    except exceptions.MariaDBIncompatibleError as e:
        LOG.error(str(e))
        raise SystemExit(1)

    try:
        mariadb.verify_schema_versions(engine)
    except exceptions.SchemaVersionMismatchError as e:
        LOG.error(str(e))
        raise SystemExit(1)

    # Pre-populate every SQLAlchemy ``Table`` object before the gRPC
    # server starts accepting requests. The lazy ``_get_*_table()``
    # helpers serialise first-time inits behind a single RLock, and
    # with 64 worker threads servicing the cluster-startup burst that
    # one lock becomes a thundering-herd hot spot exactly when
    # sf-database needs to be answering keepalives.
    mariadb.register_all_tables()

    # Create the gRPC server.  Allow clients to send keepalive pings as
    # often as every 5 seconds — mariadb.py uses a 10-second interval.
    # Without this the default minimum (5 minutes) triggers GOAWAY with
    # ENHANCE_YOUR_CALM when the client pings more frequently.
    #
    # keepalive_permit_without_calls=1 is also required: when there are no
    # active RPCs the server otherwise treats the transport as idle and
    # forces a 2-hour minimum ping interval regardless of
    # min_recv_ping_interval_without_data_ms.
    #
    # max_ping_strikes=0 disables the strike counter entirely. Even with the
    # two options above, multi-node CI clusters still occasionally tripped
    # the default 2-strike limit (e.g., during reconnect bursts), producing
    # GOAWAY too_many_pings. Our clients are trusted internal daemons, so
    # disabling the kill switch is the recommended pattern.
    # 64 worker threads. Profiling a 70-minute functional-test bundle
    # showed the previous 20-worker pool getting starved by slow MariaDB
    # queries: 456 GOAWAY ping_timeout frames in 70 minutes (~1 every
    # 9 seconds) because every worker was blocked on a SQL round-trip
    # and there was nobody left to service the keepalive-ping frame in
    # time. With the buffer-pool tuning landed alongside this commit
    # most queries fall to <10 ms, but bursty workloads (multiple
    # parallel instance deletes draining DHCP leases, the maintainer
    # passing every 30 s) still spike concurrent request counts above
    # 20. Going to 64 leaves daylight above the worst case we saw
    # without requiring tuning per-deployment, and each idle thread
    # costs only ~8 KiB of Python stack.
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=64),
        interceptors=[_CallerMetricsInterceptor()],
        options=[
            ('grpc.http2.min_recv_ping_interval_without_data_ms', 5000),
            ('grpc.keepalive_permit_without_calls', 1),
            ('grpc.http2.max_ping_strikes', 0),
        ]
    )

    # Register the gRPC standard health-checking protocol against the
    # empty-string service name (the convention for "overall server
    # health") for external monitoring via unary Check calls. SF's own
    # client channels deliberately do NOT consume this via
    # healthCheckConfig: Watch-based client-side health checking
    # deadlocks this synchronous servicer against the server's single
    # event-dispatch thread (the initial Watch response is sent while
    # holding the servicer lock; Watch close callbacks acquire that
    # lock inline on the event thread). Nothing should open Watch
    # streams against this server. See shakenfist/util/grpc_channel.py.
    # The status still flips to NOT_SERVING during the shutdown path
    # so external Check-based monitoring sees the drain.
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)

    # Bind the gRPC server on this node's mesh IP. Clients discover where
    # to connect via MARIADB_GATEWAY_HOSTS; the bind address is intentionally
    # separate so a tier of sf-database instances can each bind locally.
    server.add_insecure_port(
        f'{config.NODE_MESH_IP}:{config.MARIADB_GATEWAY_PORT}')

    # Create the monitor and register the service BEFORE starting the server.
    # This is critical - if we start the server before registering the service,
    # clients that connect during that window will fail because no service
    # handlers are registered.
    m = Monitor('database', health_servicer)
    database_pb2_grpc.add_DatabaseServiceServicer_to_server(
        DatabaseService(m), server)

    # Now start the server - it's ready to accept requests
    server.start()
    LOG.info('gRPC server started and listening on '
             f'{config.NODE_MESH_IP}:{config.MARIADB_GATEWAY_PORT}')

    m.run()

    drain_and_stop(server, health_servicer)

    daemon.force_clean_exit()
