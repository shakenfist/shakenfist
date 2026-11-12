# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""The batch hard delete, in one transaction per batch.

This is a seam of mariadb.py rather than a layer of its own: every
statement here is built from that module's table definitions and every
call it falls back to is one of its public delete functions, so it
imports mariadb and nothing in mariadb imports it. It moved out because
mariadb.py is over thirty thousand lines and this is a contiguous,
narrow piece of it -- four public entry points, declared in one place,
with one caller outside the tests (#4882).

The public surface is hard_delete_objects(), its per-call fallback
hard_delete_objects_per_call(), the request validation both they and the
servicer share, and the declarations HARD_DELETE_BATCH_FUNCTIONS and
HARD_DELETE_STATEMENTS which say what a type's hard delete removes.
"""

import time
from typing import Any
from typing import Callable
from typing import cast
from typing import NamedTuple
from typing import Optional
from uuid import UUID

import grpc
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from shakenfist_utilities import logs

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.protos import database_pb2
from shakenfist.protos import shakenfist_enums_pb2
from shakenfist.schema.object_types import ObjectType


LOG, _ = logs.setup(__name__)


# =============================================================================
# Batch hard delete
#
# Most object types' hard_delete() is nothing but row deletes keyed on the
# object: rows in the type's own tables, then the object_states,
# object_metadata and event_objects rows every type has. Run one call at a
# time, each of those deletes is its own commit, and a commit which writes
# costs a redo log flush however little it deletes. The batch below runs
# every delete for up to HARD_DELETE_OBJECTS_MAX objects of one type in one
# transaction, and so pays for one.
#
# HARD_DELETE_BATCH_FUNCTIONS is the one place a batchable type's deletes are
# declared: DatabaseBackedObject.hard_delete() sends a type listed here to the
# batch, and the classes add no deletes of their own. It maps each type to
# the ordered names of the public delete functions run for it, before the
# three every type runs (mariadb.delete_state, mariadb.delete_object_metadata and
# mariadb.delete_object_events), and
# HARD_DELETE_STATEMENTS maps each name to the same delete as one statement
# for many objects, and to a call of the public function for one object.
# The calls are for an sf-database which predates the batch RPC.
#
# A cluster operation's deletes are ordered targets first, so that a reader
# of cluster_operation_targets which runs between the calls finds no target
# rather than a target whose operation has gone. In one transaction the
# order is invisible to readers, and is kept so the two paths match. On the
# per-call path a reader must still tolerate a torn read, which is why
# scheduled_tasks._process_per_blob_queue() passes suppress_failure_audit;
# the order only removes the easy half of that race. The order is also what
# lets the error delete be scoped to the operation type at all: the type
# lives on the cluster_operations row, so the error delete reads it while
# it is still there -- see
# _delete_cluster_operation_errors_for_operations().
#
# Instance, namespace and namespace claim are absent on purpose: each
# hard_delete() does more than delete rows keyed on the object, so they keep
# their own code. Node is absent for the opposite reason -- its hard_delete()
# deletes nothing at all, and the deleted-object sweep skips the type for
# that reason (scheduled_tasks.DELETED_OBJECT_SWEEP_EXCLUDED_TYPES).
# =============================================================================

# The most objects one HardDeleteObjects call may name, so that a caller
# mistake cannot build an unbounded transaction.
HARD_DELETE_OBJECTS_MAX = 1000


class HardDeleteStatement(NamedTuple):
    """One public delete function, as a batch statement and as a call.

    ``build`` returns one DELETE of the rows the function deletes for
    every object in ``uuids`` of ``object_type``. Its WHERE clause is
    that of the function's ``mariadb._direct_`` implementation, with ``IN`` in
    place of ``=``. ``call`` runs the public function for one object, as
    hard_delete_objects_per_call() calls it. ``evicts`` is the object cache
    type the public function evicts, if it evicts one.
    """
    build: Callable[[ObjectType, list[str]], sa.Delete]
    call: Callable[[ObjectType, str], Any]
    evicts: Optional[str] = None


def _uuids_in(column: sa.ColumnElement[Any],
              uuids: list[str]) -> sa.ColumnElement[bool]:
    """``column IN uuids``, with each uuid in the form the column binds.

    A static table's ``sa.Uuid`` column binds ``uuid.UUID`` objects,
    which is why the ``mariadb._direct_`` functions for those tables take a
    UUID; the other uuid columns are strings.
    """
    if isinstance(column.type, sa.Uuid):
        return column.in_([UUID(uuid) for uuid in uuids])
    return column.in_(uuids)


def _delete_where_uuid_in(
        get_table: Callable[[], sa.Table],
        column: str) -> Callable[[ObjectType, list[str]], sa.Delete]:
    """A builder for a delete keyed on one uuid column alone."""
    def build(object_type: ObjectType, uuids: list[str]) -> sa.Delete:
        table = get_table()
        return sa.delete(table).where(_uuids_in(table.c[column], uuids))
    return build


def _delete_cluster_operation_targets_for_objects(
        object_type: ObjectType, uuids: list[str]) -> sa.Delete:
    table = mariadb._get_cluster_operation_targets_table()
    return sa.delete(table).where(sa.and_(
        table.c.target_object_type == object_type,
        _uuids_in(table.c.target_uuid, uuids)))


# The three builders below are the batch's only ones whose table is
# shared by every cluster operation type, so keying on the operation's
# uuid alone does not scope them to the type the batch declares. Every
# other builder keys on a table one type owns, where the table is the
# scope.
#
# A uuid is globally unique, so an unconditional batch which declared one
# cluster operation type and named another's uuid deleted that
# operation's own rows while the type-scoped object_states delete which
# opens the transaction matched nothing: an object_states row with no
# object left behind it, which is the shape #3534 had to reconcile. No
# in-tree caller can do that, since baseobject.hard_delete() and the
# deleted-object sweep both pass matching pairs, so these predicates
# restore an invariant rather than fix a live defect (#4877).


def _delete_cluster_operation_targets_for_operations(
        object_type: ObjectType, uuids: list[str]) -> sa.Delete:
    # As mariadb._direct_delete_cluster_operation_target(), for many operations
    # of one type.
    table = mariadb._get_cluster_operation_targets_table()
    return sa.delete(table).where(sa.and_(
        table.c.operation_type == str(object_type),
        _uuids_in(table.c.operation_uuid, uuids)))


def _delete_cluster_operation_errors_for_operations(
        object_type: ObjectType, uuids: list[str]) -> sa.Delete:
    """As mariadb._direct_delete_cluster_operation_error(), scoped to the type.

    cluster_operation_errors carries no operation type of its own, so the
    type can only come from the operation's own row. That makes the
    order of the two deletes load-bearing: this one is a scope only
    while the cluster_operations row is still there, which is why
    _CLUSTER_OPERATION_HARD_DELETES deletes the error before the
    operation. test_the_error_delete_is_ordered_before_the_operation
    holds them in that order.
    """
    table = mariadb._get_cluster_operation_errors_table()
    operations = mariadb._get_cluster_operations_table()
    return sa.delete(table).where(table.c.op_uuid.in_(
        sa.select(operations.c.uuid).where(sa.and_(
            operations.c.operation_type == str(object_type),
            _uuids_in(operations.c.uuid, uuids)))))


def _delete_cluster_operations_for_objects(
        object_type: ObjectType, uuids: list[str]) -> sa.Delete:
    # As mariadb._direct_delete_cluster_operation(), for many operations of one
    # type.
    table = mariadb._get_cluster_operations_table()
    return sa.delete(table).where(sa.and_(
        table.c.operation_type == str(object_type),
        _uuids_in(table.c.uuid, uuids)))


def _remove_all_references_from_objects(
        object_type: ObjectType, uuids: list[str]) -> sa.Delete:
    table = mariadb._get_object_references_table()
    return sa.delete(table).where(sa.and_(
        table.c.source_object_type == str(object_type),
        _uuids_in(table.c.source_uuid, uuids)))


def _delete_object_metadata_for_objects(
        object_type: ObjectType, uuids: list[str]) -> sa.Delete:
    # As mariadb._direct_delete_object_metadata().
    table = mariadb._get_object_metadata_table()
    return sa.delete(table).where(sa.and_(
        table.c.object_type == object_type,
        _uuids_in(table.c.object_uuid, uuids)))


def _delete_object_events_for_objects(
        object_type: ObjectType, uuids: list[str]) -> sa.Delete:
    # As mariadb._direct_delete_object_events(). event_objects keys on
    # (object_type, object_uuid, event_uuid), so this is an index range
    # per uuid.
    table = mariadb._get_event_objects_table()
    return sa.delete(table).where(sa.and_(
        table.c.object_type == str(object_type),
        _uuids_in(table.c.object_uuid, uuids)))


# Each entry's builder mirrors the mariadb._direct_ function named in its comment.
HARD_DELETE_STATEMENTS: dict[str, HardDeleteStatement] = {
    # mariadb._direct_delete_cluster_operation_target()
    'delete_cluster_operation_target': HardDeleteStatement(
        _delete_cluster_operation_targets_for_operations,
        lambda t, u: mariadb.delete_cluster_operation_target(u)),
    # mariadb._direct_delete_cluster_operation_error()
    'delete_cluster_operation_error': HardDeleteStatement(
        _delete_cluster_operation_errors_for_operations,
        lambda t, u: mariadb.delete_cluster_operation_error(u)),
    # mariadb._direct_delete_cluster_operation()
    'delete_cluster_operation': HardDeleteStatement(
        _delete_cluster_operations_for_objects,
        lambda t, u: mariadb.delete_cluster_operation(u)),
    # mariadb._direct_delete_cluster_operation_targets_for_object()
    'delete_cluster_operation_targets_for_object': HardDeleteStatement(
        _delete_cluster_operation_targets_for_objects,
        lambda t, u: mariadb.delete_cluster_operation_targets_for_object(t, u)),
    # mariadb._direct_delete_agent_operation_attributes()
    'delete_agent_operation_attributes': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_agent_operation_attributes_table, 'uuid'),
        lambda t, u: mariadb.delete_agent_operation_attributes(UUID(u))),
    # mariadb._direct_delete_agent_operation()
    'delete_agent_operation': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_agent_operations_table, 'uuid'),
        lambda t, u: mariadb.delete_agent_operation(UUID(u)),
        evicts='agentoperation'),
    # mariadb._direct_remove_all_references_from(), with no relationship filter
    'remove_all_references_from': HardDeleteStatement(
        _remove_all_references_from_objects,
        lambda t, u: mariadb.remove_all_references_from(t, u)),
    # mariadb._direct_delete_all_artifact_indexes()
    'delete_all_artifact_indexes': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_artifact_indexes_table, 'artifact_uuid'),
        lambda t, u: mariadb.delete_all_artifact_indexes(UUID(u))),
    # mariadb._direct_delete_artifact_attributes()
    'delete_artifact_attributes': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_artifact_attributes_table, 'uuid'),
        lambda t, u: mariadb.delete_artifact_attributes(UUID(u))),
    # mariadb._direct_delete_artifact()
    'delete_artifact': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_artifacts_table, 'uuid'),
        lambda t, u: mariadb.delete_artifact(UUID(u)),
        evicts='artifact'),
    # mariadb._direct_delete_network_attributes()
    'delete_network_attributes': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_network_attributes_table, 'uuid'),
        lambda t, u: mariadb.delete_network_attributes(UUID(u))),
    # mariadb._direct_delete_network()
    'delete_network': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_networks_table, 'uuid'),
        lambda t, u: mariadb.delete_network(UUID(u)),
        evicts='network'),
    # mariadb._direct_delete_blob_hashes()
    'delete_blob_hashes': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_blob_hashes_table, 'blob_uuid'),
        lambda t, u: mariadb.delete_blob_hashes(u)),
    # mariadb._direct_delete_blob_transfers_for_blob()
    'delete_blob_transfers_for_blob': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_blob_transfers_table, 'blob_uuid'),
        lambda t, u: mariadb.delete_blob_transfers_for_blob(u)),
    # mariadb._direct_delete_blob_attributes()
    'delete_blob_attributes': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_blob_attributes_table, 'uuid'),
        lambda t, u: mariadb.delete_blob_attributes(UUID(u))),
    # mariadb._direct_delete_blob()
    'delete_blob': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_blobs_table, 'uuid'),
        lambda t, u: mariadb.delete_blob(UUID(u)),
        evicts='blob'),
    # mariadb._direct_delete_dnsmasq()
    'delete_dnsmasq': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_dnsmasq_table, 'uuid'),
        lambda t, u: mariadb.delete_dnsmasq(UUID(u)),
        evicts='dnsmasq'),
    # mariadb._direct_delete_network_interface_attributes()
    'delete_network_interface_attributes': HardDeleteStatement(
        _delete_where_uuid_in(
            mariadb._get_network_interface_attributes_table, 'uuid'),
        lambda t, u: mariadb.delete_network_interface_attributes(UUID(u))),
    # mariadb._direct_delete_network_interface()
    'delete_network_interface': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_network_interfaces_table, 'uuid'),
        lambda t, u: mariadb.delete_network_interface(UUID(u)),
        evicts='networkinterface'),
    # mariadb._direct_delete_reservations_for_ipam()
    'delete_reservations_for_ipam': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_ipam_reservations_table, 'ipam_uuid'),
        lambda t, u: mariadb.delete_reservations_for_ipam(UUID(u))),
    # mariadb._direct_delete_ipam()
    'delete_ipam': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_ipams_table, 'uuid'),
        lambda t, u: mariadb.delete_ipam(UUID(u)),
        evicts='ipam'),
    # mariadb._direct_delete_namespace_key_attributes()
    'delete_namespace_key_attributes': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_namespace_key_attributes_table, 'uuid'),
        lambda t, u: mariadb.delete_namespace_key_attributes(UUID(u))),
    # mariadb._direct_delete_namespace_key()
    'delete_namespace_key': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_namespace_keys_table, 'uuid'),
        lambda t, u: mariadb.delete_namespace_key(UUID(u))),
    # mariadb._direct_delete_trusted_issuer_attributes()
    'delete_trusted_issuer_attributes': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_trusted_issuer_attributes_table, 'uuid'),
        lambda t, u: mariadb.delete_trusted_issuer_attributes(UUID(u))),
    # mariadb._direct_delete_trusted_issuer()
    'delete_trusted_issuer': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_trusted_issuers_table, 'uuid'),
        lambda t, u: mariadb.delete_trusted_issuer(UUID(u))),
    # mariadb._direct_delete_mapping_rule_attributes()
    'delete_mapping_rule_attributes': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_mapping_rule_attributes_table, 'uuid'),
        lambda t, u: mariadb.delete_mapping_rule_attributes(UUID(u))),
    # mariadb._direct_delete_mapping_rule()
    'delete_mapping_rule': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_mapping_rules_table, 'uuid'),
        lambda t, u: mariadb.delete_mapping_rule(UUID(u))),
    # mariadb._direct_delete_upload()
    'delete_upload': HardDeleteStatement(
        _delete_where_uuid_in(mariadb._get_uploads_table, 'uuid'),
        lambda t, u: mariadb.delete_upload(UUID(u)),
        evicts='upload'),
}

_CLUSTER_OPERATION_HARD_DELETES = (
    'delete_cluster_operation_target',
    'delete_cluster_operation_error',
    'delete_cluster_operation',
)

HARD_DELETE_BATCH_FUNCTIONS: dict[ObjectType, tuple[str, ...]] = {
    # Every cluster operation
    ObjectType.ARTIFACT_FETCH_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.IMGCACHE_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NET_IFACE_IP_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NET_IFACE_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NET_IP_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NET_MACADDR_IP_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NET_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NODE_AOP_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NODE_BLOB_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NODE_INST_NET_IFACE_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NODE_INST_NETDESC_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NODE_INST_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NODE_INST_SNAP_OP: _CLUSTER_OPERATION_HARD_DELETES,
    ObjectType.NODE_NET_OP: _CLUSTER_OPERATION_HARD_DELETES,
    # An agent operation is not a cluster operation and has no cluster
    # operation rows. Its references go as delete() removes them: an
    # expired agent operation is swept here too, and a reference row left
    # behind would leak forever.
    ObjectType.AGENTOPERATION: (
        'delete_agent_operation_attributes',
        'delete_agent_operation',
        'remove_all_references_from',
    ),
    # Artifact's rows, then the targets every
    # DatabaseBackedObjectWithOperations has
    ObjectType.ARTIFACT: (
        'delete_all_artifact_indexes',
        'delete_artifact_attributes',
        'delete_artifact',
        'delete_cluster_operation_targets_for_object',
    ),
    # Network's rows, then the targets every
    # DatabaseBackedObjectWithOperations has
    ObjectType.NETWORK: (
        'delete_network_attributes',
        'delete_network',
        'delete_cluster_operation_targets_for_object',
    ),
    # The references a blob holds first: a blob removes them on its
    # transition to deleted, and this is the backstop for one deleted
    # before it did, or whose removal failed then (issue 4558).
    ObjectType.BLOB: (
        'remove_all_references_from',
        'delete_blob_hashes',
        'delete_blob_transfers_for_blob',
        'delete_blob_attributes',
        'delete_blob',
    ),
    ObjectType.DHCP: ('delete_dnsmasq',),
    ObjectType.INTERFACE: (
        'delete_network_interface_attributes',
        'delete_network_interface',
    ),
    # An IPAM which is in memory only has no rows, and deletes nothing
    ObjectType.IPAM: (
        'delete_reservations_for_ipam',
        'delete_ipam',
    ),
    ObjectType.NAMESPACE_KEY: (
        'delete_namespace_key_attributes',
        'delete_namespace_key',
    ),
    ObjectType.TRUSTED_ISSUER: (
        'delete_trusted_issuer_attributes',
        'delete_trusted_issuer',
    ),
    ObjectType.MAPPING_RULE: (
        'delete_mapping_rule_attributes',
        'delete_mapping_rule',
    ),
    ObjectType.UPLOAD: ('delete_upload',),
}


def hard_delete_objects_request_error(
        object_type: ObjectType, object_uuids: list[str],
        state_values: list[str], updated_before: Optional[float],
        unconditional: bool) -> Optional[str]:
    """Why a batch hard delete request can never succeed, or None if it can.

    Shared by hard_delete_objects() and the HardDeleteObjects servicer,
    so that a request the client would refuse is refused by the server
    too. A guarded request must name states and an age, and an
    unconditional one neither, so that no caller mistake can turn the
    guard off by leaving them out.
    """
    if object_type not in HARD_DELETE_BATCH_FUNCTIONS:
        return f'{object_type} objects are not hard deleted in batches'
    if len(object_uuids) > HARD_DELETE_OBJECTS_MAX:
        return (f'{len(object_uuids)} uuids is more than the '
                f'{HARD_DELETE_OBJECTS_MAX} one batch may delete')
    if unconditional:
        if state_values or updated_before:
            return 'an unconditional hard delete takes no states and no age'
    else:
        if not state_values:
            return 'a guarded hard delete must name the states to delete'
        if not updated_before:
            return 'a guarded hard delete must name an age'
    for object_uuid in object_uuids:
        try:
            UUID(object_uuid)
        except ValueError:
            return f'{object_uuid!r} is not a uuid'
    return None


def _direct_hard_delete_objects(object_type: ObjectType,
                                object_uuids: list[str],
                                state_values: list[str],
                                updated_before: Optional[float],
                                unconditional: bool
                                ) -> Optional[list[str]]:
    """Hard delete a batch of one type's objects in one transaction.

    The transaction opens with the delete of the objects' object_states
    rows. A guarded call deletes only the rows in one of
    ``state_values`` and older than ``updated_before``, and the rest of
    the transaction deletes only the objects whose row it deleted: an
    object which left its final state, or was touched again, after the
    caller chose it is not returned and nothing else of it goes. That is
    the per-object path's from_db() and state check, made atomic with
    the delete. An unconditional call deletes every object it is given,
    including one with no object_states row.

    This is the direct access version used by the database daemon.
    Returns the uuids deleted, or None on error (distinct from [] for a
    guarded call which found nothing eligible); on error the whole
    transaction rolled back and nothing was deleted.
    """
    engine = mariadb._get_engine()
    states = mariadb._get_object_states_table()
    functions = HARD_DELETE_BATCH_FUNCTIONS[object_type]

    def _delete() -> list[str]:
        with engine.begin() as conn:
            # INVARIANT: the first statement on this connection is this
            # DELETE, so that it and not a plain read establishes the read
            # view. Do not read here -- see the block comment above
            # mariadb._probe_admission_rows() for the ER_CHECKREAD invariant.
            where = [states.c.object_type == object_type,
                     states.c.object_uuid.in_(object_uuids)]
            if not unconditional:
                where.append(states.c.state_value.in_(state_values))
                where.append(states.c.update_time < updated_before)
            stmt = (sa.delete(states).where(sa.and_(*where))
                    .returning(states.c.object_uuid))
            returned = [row.object_uuid for row in conn.execute(stmt).fetchall()]

            # An unconditional delete has already been decided by its
            # caller, and an object it names may have no state row, so
            # what the state delete returned does not limit it.
            deleted = list(object_uuids) if unconditional else returned
            if not deleted:
                return []

            for name in functions:
                conn.execute(HARD_DELETE_STATEMENTS[name].build(
                    object_type, deleted))
            conn.execute(_delete_object_metadata_for_objects(
                object_type, deleted))
            conn.execute(_delete_object_events_for_objects(
                object_type, deleted))
            return deleted

    try:
        return mariadb._retry_transaction(
            _delete, f'hard_delete_objects({object_type})')
    except OperationalError as e:
        LOG.warning(
            f'MariaDB hard delete of {len(object_uuids)} {object_type} '
            f'objects failed: {e}')
        return None


# The batch's client wrapper raises HardDeleteBatchUnavailable on
# UNIMPLEMENTED, for the reason the coalescing wrappers raise
# CoalescingUnavailable: an sf-database from before the RPC existed calls
# for a different response from any other failure. A failed batch deleted
# nothing and is retried later, while an unavailable one can be done now,
# one object at a time, with hard_delete_objects_per_call() against the
# same server. Returning None for both would leave every hard delete of a
# batchable type failing for the length of a rolling upgrade.
#
# The answer is remembered for a while, as a time.monotonic() reading until
# which the RPC is not asked again. Without that every hard delete of a
# batchable type would pay a refused round trip, and log a warning, before
# its per-call fallback for as long as the upgrade window lasts.
HARD_DELETE_BATCH_RECHECK_SECONDS = 300
_HARD_DELETE_BATCH_UNAVAILABLE_UNTIL = 0.0


def _grpc_hard_delete_objects(object_type: ObjectType,
                              object_uuids: list[str],
                              state_values: list[str],
                              updated_before: Optional[float],
                              unconditional: bool
                              ) -> Optional[list[str]]:
    """Hard delete a batch of one type's objects via the database microservice.

    Returns the uuids deleted, or None on error. Raises
    HardDeleteBatchUnavailable if the database service predates the RPC,
    or answered so within the last HARD_DELETE_BATCH_RECHECK_SECONDS.
    """
    global _HARD_DELETE_BATCH_UNAVAILABLE_UNTIL

    if time.monotonic() < _HARD_DELETE_BATCH_UNAVAILABLE_UNTIL:
        raise exceptions.HardDeleteBatchUnavailable(
            'HardDeleteObjects was recently not implemented by this '
            'database service')
    try:
        stub = mariadb._get_database_stub()
        request = database_pb2.HardDeleteObjectsRequest(
            object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType, object_type.proto_id),
            object_uuids=object_uuids,
            state_values=state_values,
            updated_before=(updated_before or 0),
            unconditional=unconditional
        )
        reply = mariadb._grpc_call(stub.HardDeleteObjects, request)
        return list(reply.deleted_uuids)
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.UNIMPLEMENTED:
            _HARD_DELETE_BATCH_UNAVAILABLE_UNTIL = (
                time.monotonic() + HARD_DELETE_BATCH_RECHECK_SECONDS)
            LOG.warning(
                f'gRPC HardDeleteObjects is not implemented by this database '
                f'service, objects are hard deleted one at a time for the '
                f'next {HARD_DELETE_BATCH_RECHECK_SECONDS} seconds')
            raise exceptions.HardDeleteBatchUnavailable(
                'HardDeleteObjects is not implemented by this database '
                'service')
        LOG.error(
            f'gRPC HardDeleteObjects failed for {len(object_uuids)} '
            f'{object_type} objects: {e}')
        return None


def hard_delete_objects(object_type: ObjectType,
                        object_uuids: list[str],
                        state_values: Optional[list[str]] = None,
                        updated_before: Optional[float] = None,
                        unconditional: bool = False
                        ) -> Optional[list[str]]:
    """Hard delete a batch of one type's objects in one transaction.

    Deletes every row the type's hard_delete() deletes -- the rows named
    by HARD_DELETE_BATCH_FUNCTIONS, then the object_states,
    object_metadata and event_objects rows -- and nothing else. It writes
    no audit event; that is the caller's to write for each uuid returned.

    A call is guarded or unconditional. A guarded call, for a caller
    which listed the objects earlier, passes ``state_values`` and
    ``updated_before``, and deletes only the objects whose state is
    still one of ``state_values`` and was last set before
    ``updated_before``; the check is made in the same transaction as the
    delete. An unconditional call, for a caller which has already
    decided, deletes every object named, whatever its state, including
    one with no state row.

    Args:
        object_type: The type of every object named. Must be a key of
            HARD_DELETE_BATCH_FUNCTIONS.
        object_uuids: At most HARD_DELETE_OBJECTS_MAX uuids.
        state_values: Guarded only: the states an object must be in.
        updated_before: Guarded only: a unix timestamp an object's state
            must be older than.
        unconditional: True for an unconditional call.

    Returns:
        The uuids deleted: for a guarded call those which were eligible,
        and for an unconditional call every uuid named. None if the call
        failed, in which case nothing was deleted.

    Raises:
        ValueError: the request can never succeed (see
            hard_delete_objects_request_error()).
        exceptions.HardDeleteBatchUnavailable: the database service
            predates the batch; hard_delete_objects_per_call() does the
            same work one object at a time.
    """
    state_values = state_values or []
    error = hard_delete_objects_request_error(
        object_type, object_uuids, state_values, updated_before,
        unconditional)
    if error:
        raise ValueError(error)
    if not object_uuids:
        return []

    if mariadb._use_database_service():
        deleted = _grpc_hard_delete_objects(
            object_type, object_uuids, state_values, updated_before,
            unconditional)
    else:
        deleted = _direct_hard_delete_objects(
            object_type, object_uuids, state_values, updated_before,
            unconditional)

    # Evict what the public delete functions would have. A failed call
    # deleted nothing as far as we know, but a reply lost after the
    # commit looks the same, and an eviction is never wrong.
    caches = [HARD_DELETE_STATEMENTS[name].evicts
              for name in HARD_DELETE_BATCH_FUNCTIONS[object_type]]
    for cache in caches:
        if cache:
            for object_uuid in (object_uuids if deleted is None else deleted):
                mariadb._object_cache_evict(cache, object_uuid)
    return deleted


def hard_delete_objects_per_call(object_type: ObjectType,
                                 object_uuid: str) -> None:
    """Hard delete one object of a batchable type, one delete at a time.

    Calls each public function HARD_DELETE_BATCH_FUNCTIONS names for the
    type, in that order, then mariadb.delete_state(), mariadb.delete_object_metadata() and
    mariadb.delete_object_events(). Each is its own commit, so unlike the batch the
    object's state row goes last. This is for an sf-database which predates
    the batch, where hard_delete_objects() raises HardDeleteBatchUnavailable.
    """
    if object_type not in HARD_DELETE_BATCH_FUNCTIONS:
        raise ValueError(
            f'{object_type} objects are not hard deleted in batches')
    for name in HARD_DELETE_BATCH_FUNCTIONS[object_type]:
        HARD_DELETE_STATEMENTS[name].call(object_type, object_uuid)
    mariadb.delete_state(object_type, object_uuid)
    mariadb.delete_object_metadata(object_type, object_uuid)
    mariadb.delete_object_events(object_type, object_uuid)
