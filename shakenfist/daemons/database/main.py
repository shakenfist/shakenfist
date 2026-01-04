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
from shakenfist.schema.dnsmasq import DnsMasqData
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
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
            etcd.enqueue(request.queue_name, workitem, delay=request.delay)
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
            etcd.resolve(request.queue_name, request.job_name)
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
            processing, queued, deferred = etcd.get_queue_length(
                request.queue_name)
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
        """Move jobs from processing back to queue."""
        try:
            self.monitor.counters['restart_queue'].inc()
            etcd.restart_queue(request.queue_name)
            return database_pb2.StatusReply(success=True, error='')
        except Exception as e:
            util_exceptions.ignore_exception('database RestartQueue failed', e)
            return database_pb2.StatusReply(success=False, error=str(e))

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
            path = etcd._construct_key(
                request.object_type, request.subtype, request.name,
                prefix='sflocks'
            )
            acquired = etcd.create_raw(path, lock_data)
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
            path = etcd._construct_key(
                request.object_type, request.subtype, request.name,
                prefix='sflocks'
            )
            released = etcd.transactional_delete_raw(path, lock_data)
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
            etcd.clear_stale_locks()
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
            locks = etcd.get_existing_locks()
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
            return database_pb2.GetObjectsByStateReply(object_uuids=uuids)
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
                source_type, UUID(request.source_uuid), relationship, rel_value,
                target_type, UUID(request.target_uuid))
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
                source_type, UUID(request.source_uuid), relationship, rel_value,
                target_type, UUID(request.target_uuid))
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
                target_type, UUID(request.target_uuid), relationship)
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
                source_type, UUID(request.source_uuid), relationship)
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
            count = mariadb._direct_count_references_to(
                target_type, UUID(request.target_uuid))
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
                source_type, UUID(request.source_uuid), relationship)
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
                source_type, UUID(request.source_uuid), relationship, rel_value,
                target_type, UUID(request.target_uuid))
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
            'restart_queue', 'acquire_lock', 'release_lock', 'get_lock_holder',
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
            # MariaDB DnsMasq operations
            'create_dnsmasq', 'get_dnsmasq', 'get_dnsmasqs', 'delete_dnsmasq',
            'update_dnsmasq',
            # MariaDB object reference operations
            'record_relationship', 'remove_relationship', 'get_references_to',
            'get_references_from', 'count_references_to',
            'remove_all_references_from', 'update_last_active',
            'get_stale_references'
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
            n.set_daemon_state(self.daemon_name, Node.DAEMON_STATE_RUNNING)
            n.add_event(EVENT_TYPE_AUDIT, f'{self.daemon_name} daemon starting')
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
            try:
                n.set_daemon_state(self.daemon_name, Node.DAEMON_STATE_STOPPED)
            except InvalidStateException as e:
                if not str(e).startswith(
                        'Invalid state change from stopping to degraded'):
                    raise e
            n.add_event(EVENT_TYPE_AUDIT, f'{self.daemon_name} daemon stopped')
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

    # Create the gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
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
