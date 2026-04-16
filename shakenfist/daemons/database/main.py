# Copyright 2025 Michael Still and contributors

"""Database microservice daemon.

This daemon provides a gRPC interface to etcd, allowing all cluster nodes to
access the database through a single service running on the etcd_master node.
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
from prometheus_client import Counter
from prometheus_client import start_http_server
from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import mariadb
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.daemons import daemon
from shakenfist.daemons.daemon import send_systemd_ready
from shakenfist.daemons.daemon import send_systemd_status
from shakenfist.etcd import set_force_direct_etcd
from shakenfist.exceptions import InvalidStateException
from shakenfist.node import Node
from shakenfist.protos import database_pb2
from shakenfist.protos import database_pb2_grpc
from shakenfist.protos import shakenfist_enums_pb2
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.dnsmasq import DnsMasqData
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
from shakenfist.schema.upload import UploadData
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)


class DatabaseService(database_pb2_grpc.DatabaseServiceServicer):
    """gRPC service implementation for database operations."""

    def __init__(self, monitor: 'Monitor') -> None:
        super().__init__()
        self.monitor: Monitor = monitor

    # Key-Value Operations

    def Get(
        self,
        request: database_pb2.GetRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetReply:
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
            util_exceptions.ignore_exception('database Get failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetReply(found=False, value='')

    def GetPrefix(
        self,
        request: database_pb2.GetPrefixRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.GetPrefixReply:
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
            util_exceptions.ignore_exception('database GetPrefix failed', e)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return database_pb2.GetPrefixReply(results=[])

    def Put(
        self,
        request: database_pb2.PutRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Store a value."""
        try:
            self.monitor.counters['put'].inc()
            data = json.loads(request.data)
            etcd.put(request.object_type, request.subtype, request.name, data)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database Put failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def Create(
        self,
        request: database_pb2.CreateRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Create a new value (fails if exists)."""
        try:
            self.monitor.counters['create'].inc()
            data = json.loads(request.data)
            success = etcd.create(
                request.object_type, request.subtype, request.name, data)
            return database_pb2.StatusReply(success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database Create failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def Delete(
        self,
        request: database_pb2.DeleteRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete a value."""
        try:
            self.monitor.counters['delete'].inc()
            etcd.delete(request.object_type, request.subtype, request.name)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database Delete failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def DeletePrefix(
        self,
        request: database_pb2.DeletePrefixRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Delete all keys with a given prefix."""
        try:
            self.monitor.counters['delete_prefix'].inc()
            etcd.delete_prefix(request.path)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database DeletePrefix failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

    def ReplaceMany(
        self,
        request: database_pb2.ReplaceManyRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.ReplaceManyReply:
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
            util_exceptions.ignore_exception('database ReplaceMany failed', e)
            return database_pb2.ReplaceManyReply(
                success=False,
                failures=[database_pb2.MutationFailure(
                    path='', desired='', actual='', replacement=str(e)
                )]
            )

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
        """Claim the next available job from a queue."""
        try:
            self.monitor.counters['dequeue'].inc()
            result = mariadb._direct_work_queue_dequeue(
                request.queue_name, config.NODE_NAME)
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
            util_exceptions.ignore_exception('database Dequeue failed', e)
            return database_pb2.DequeueReply(
                found=False, job_name='', work_item='')

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
        """Release a distributed lock."""
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
        except Exception as e:
            util_exceptions.ignore_exception('database ReleaseLock failed', e)
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

    # Maintenance Operations

    def Compact(
        self,
        request: database_pb2.CompactRequest,
        context: grpc.ServicerContext
    ) -> database_pb2.StatusReply:
        """Compact the etcd database."""
        try:
            self.monitor.counters['compact'].inc()
            etcd.compact(request.revision)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database Compact failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

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
        """Get all object UUIDs of a given type in specified states."""
        try:
            self.monitor.counters['get_objects_by_state'].inc()
            object_type = ObjectType.from_proto_id(request.object_type)
            if object_type is None:
                return database_pb2.GetObjectsByStateReply(object_uuids=[])
            uuids = mariadb.get_objects_by_state(
                object_type, list(request.state_values))
            return database_pb2.GetObjectsByStateReply(
                object_uuids=uuids or [])
        except Exception as e:
            util_exceptions.ignore_exception(
                'database GetObjectsByState failed', e)
            return database_pb2.GetObjectsByStateReply(object_uuids=[])

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
                return database_pb2.GetReferencesReply(references=[])
            relationship = None
            if request.HasField('relationship'):
                relationship = RelationshipType.from_proto_id(
                    request.relationship)
            refs = mariadb._direct_get_references_from(
                source_type, request.source_uuid, relationship)
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
            success = mariadb._direct_update_blob_attributes(data)
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
            is_etcd_master=d.is_etcd_master,
            is_hypervisor=d.is_hypervisor,
            is_network_node=d.is_network_node,
            is_eventlog_node=d.is_eventlog_node,
            instances=(
                json.loads(d.instances_json)
                if d.instances_json else []
            ),
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
            is_etcd_master=data.is_etcd_master,
            is_hypervisor=data.is_hypervisor,
            is_network_node=data.is_network_node,
            is_eventlog_node=data.is_eventlog_node,
            instances_json=json.dumps(data.instances),
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
                data
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
            success = mariadb._direct_update_namespace_attributes(data)
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
            ipv4=d.ipv4,
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
            ipv4=data.ipv4,
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
                data)
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
            networkinterfaces=list(d.networkinterfaces),
            networkinterfaces_initialized=(
                d.networkinterfaces_initialized),
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
            networkinterfaces=data.networkinterfaces,
            networkinterfaces_initialized=(
                data.networkinterfaces_initialized),
            hosteddns_json=json.dumps(data.hosteddns))

    # AgentOperation Operations (MariaDB)

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
                    data))
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
        )

    def _agentop_attrs_to_proto(
            self,
            data: AgentOperationAttributesData
    ) -> database_pb2.AgentOperationAttributesProto:
        """Convert AgentOperationAttributesData to proto."""
        return database_pb2.AgentOperationAttributesProto(
            uuid=str(data.uuid),
            results_json=json.dumps(data.results))

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
            success = mariadb._direct_update_artifact_attributes(data)
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
                ._direct_update_instance_attributes(data))
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
                last_cluster_operation_json=(
                    json.dumps(data.last_cluster_operation)
                    if data.last_cluster_operation is not None
                    else '')
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
            success = (
                mariadb
                ._direct_create_and_enqueue_cluster_operation(
                    UUID(request.uuid),
                    request.operation_type,
                    metadata,
                    request.created_at,
                    request.queue_name,
                    request.delay,
                )
            )
            return database_pb2.StatusReply(
                success=success, error='')
        except Exception as e:
            util_exceptions.ignore_exception(
                'database CreateAndEnqueueClusterOperation failed',
                e)
            return database_pb2.StatusReply(
                success=False, error=str(e))

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

        interfaces = _parse(d.interfaces_json)
        return InstanceAttributesData(
            uuid=UUID(d.uuid),
            placement=_parse(d.placement_json),
            power_state=_parse(d.power_state_json),
            ports=_parse(d.ports_json),
            enforced_deletes=_parse(
                d.enforced_deletes_json),
            block_devices=_parse(
                d.block_devices_json),
            interfaces=(
                interfaces if interfaces else []),
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
            interfaces_json=json.dumps(data.interfaces),
            agent_state_json=json.dumps(
                data.agent_state),
            agent_attributes_json=json.dumps(
                data.agent_attributes),
            agent_operations_json=json.dumps(
                data.agent_operations),
            kvm_pid=data.kvm_pid or 0,
            error_message=data.error_message or '',
            vsock_cids_json=json.dumps(data.vsock_cids))


class Monitor(daemon.WorkerPoolDaemon):
    """Background monitor for the database daemon.

    The database daemon is special because it provides database access to other
    daemons. This means we must use direct etcd access for our own startup and
    shutdown recording, otherwise we'd have a chicken-and-egg problem.
    """

    def __init__(self, id: str) -> None:
        super().__init__(id)
        self.counters: dict[str, Counter] = {}

        # Create counters for all operations
        operations = [
            'get', 'get_prefix', 'put', 'create', 'delete', 'delete_prefix',
            'replace_many', 'enqueue', 'dequeue', 'resolve', 'get_queue_length',
            'restart_queue', 'list_stuck_work_queue_rows',
            'clear_work_queue_claim', 'delete_work_queue_row',
            'acquire_lock', 'release_lock', 'get_lock_holder',
            'clear_stale_locks', 'get_existing_locks', 'compact',
            # MariaDB state operations
            'get_object_state', 'set_object_state', 'delete_object_state',
            'get_objects_by_state',
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
            'delete_cluster_operation_target',
            'delete_cluster_operation_targets_for_object',
            'delete_stale_cluster_operation_targets',
            # MariaDB node metrics operations
            'upsert_node_metrics', 'get_node_metrics',
            'get_all_node_metrics', 'delete_node_metrics',
        ]
        for op in operations:
            self.counters[op] = Counter(
                f'database_{op}_total',
                f'Number of {op} operations'
            )

        start_http_server(config.DATABASE_METRICS_PORT)

    def record_start(self) -> None:
        # Override to use direct etcd access. The database daemon can't use
        # the database service for its own startup recording because WE ARE
        # the database service. We also force events to the dead letter queue
        # because the eventlog daemon may not be running yet (avoiding circular
        # dependencies).
        set_force_direct_etcd(True)
        eventlog.set_force_event_dlq(True)
        try:
            n = Node.from_db(config.NODE_NAME)
            if n:
                n.set_daemon_state(
                    self.daemon_name,
                    Node.DAEMON_STATE_RUNNING)
                n.add_event(
                    EVENT_TYPE_AUDIT,
                    f'{self.daemon_name} daemon starting')
        finally:
            set_force_direct_etcd(False)
            eventlog.set_force_event_dlq(False)
        send_systemd_ready()

    def record_exit(self) -> None:
        # Override to use direct etcd access and force events to the dead
        # letter queue (eventlog daemon may have already stopped).
        set_force_direct_etcd(True)
        eventlog.set_force_event_dlq(True)
        try:
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
        finally:
            set_force_direct_etcd(False)
            eventlog.set_force_event_dlq(False)
        send_systemd_status('Terminated')

    def _run_inner(self) -> None:
        while daemon.check_abort_path(self.abort_path):
            try:
                # The database daemon doesn't have background work to do,
                # it just serves gRPC requests. We check health periodically.
                self.idle(10)
            except Exception as e:
                util_exceptions.ignore_exception('database daemon', e)

            self.check_daemon_state()


def main() -> None:
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('database')

    # MariaDB is required for the database service. Abort early with a clear
    # error message if it's not configured.
    if not config.MARIADB_HOST:
        LOG.error('MariaDB is not configured. The database service requires '
                  'MARIADB_HOST to be set. Aborting.')
        raise SystemExit(1)

    # Ensure the MariaDB schema exists before accepting requests
    mariadb.ensure_schema()

    # Run any pending data migrations (e.g., etcd -> MariaDB)
    mariadb.ensure_data_migrations()

    # Create the gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=20))
    server.add_insecure_port(
        f'{config.DATABASE_NODE_IP}:{config.DATABASE_API_PORT}')

    # Create the monitor and register the service BEFORE starting the server.
    # This is critical - if we start the server before registering the service,
    # clients that connect during that window will fail because no service
    # handlers are registered.
    m = Monitor('database')
    database_pb2_grpc.add_DatabaseServiceServicer_to_server(
        DatabaseService(m), server)

    # Now start the server - it's ready to accept requests
    server.start()
    LOG.info('gRPC server started and listening on '
             f'{config.DATABASE_NODE_IP}:{config.DATABASE_API_PORT}')

    m.run()
    server.stop(1).wait()

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    raise SystemExit(0)
