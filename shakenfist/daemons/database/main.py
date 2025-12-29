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
from typing import Any
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
from shakenfist.daemons import daemon
from shakenfist.daemons.daemon import send_systemd_ready
from shakenfist.daemons.daemon import send_systemd_status
from shakenfist.etcd import set_force_direct_etcd
from shakenfist.exceptions import InvalidStateException
from shakenfist.node import Node
from shakenfist.protos import database_pb2
from shakenfist.protos import database_pb2_grpc
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
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
            object_type = ObjectType(request.object_type)
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
            object_type = ObjectType(request.object_type)
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
            object_type = ObjectType(request.object_type)
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
            object_type = ObjectType(request.object_type)
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
            # Convert string user_uuid to UUID if present
            user_uuid = (UUID(request.reservation.user_uuid)
                         if request.reservation.user_uuid else None)
            reservation = IPAMReservation(
                ipam_uuid=UUID(request.reservation.ipam_uuid),
                address=IPv4Address(request.reservation.address),
                reservation_type=ReservationType(
                    request.reservation.reservation_type),
                user_type=mariadb._string_to_object_type(
                    request.reservation.user_type),
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
            # Convert string user_uuid to UUID if present
            user_uuid = (UUID(request.halo_reservation.user_uuid)
                         if request.halo_reservation.user_uuid else None)
            halo_reservation = IPAMReservation(
                ipam_uuid=UUID(request.halo_reservation.ipam_uuid),
                address=IPv4Address(request.halo_reservation.address),
                reservation_type=ReservationType(
                    request.halo_reservation.reservation_type),
                user_type=mariadb._string_to_object_type(
                    request.halo_reservation.user_type),
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
                    reservation_type=str(reservation.reservation_type),
                    user_type=mariadb._object_type_to_string(
                        reservation.user_type),
                    user_uuid=str(reservation.user_uuid) if reservation.user_uuid else '',
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
                    reservation_type=str(res.reservation_type),
                    user_type=mariadb._object_type_to_string(res.user_type),
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
            'get_addresses_in_use'
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
