# MariaDB connection and state storage module.
#
# This module provides connection management for MariaDB and implements
# the state storage layer for Shaken Fist objects. State data is migrated
# from etcd to MariaDB for better query performance, especially for scans
# by object type and state value.
#
# The object_states table stores state for all object types in a single
# table, with object_type discriminating between them. State validation
# is handled per-type in Python code using each object's state_targets dict.
#
# Access is routed through the database microservice (gRPC) for most daemons.
# Only the database daemon uses direct MariaDB access, which it does by
# calling the _direct_* functions.
#
# Schema Versioning:
# Each table has a version number tracked in the schema_versions table.
# When ensure_schema() is called, it checks the current version and applies
# any necessary migrations. This follows the same pattern as eventlog.py.

from ipaddress import IPv4Address
import json
import random
import time
import threading
from typing import Any, Callable, cast, Optional, TypeVar
from uuid import UUID
from uuid import uuid4

import grpc
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import INET4
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError
from shakenfist_utilities import logs

from shakenfist.config import config
from shakenfist.constants import CLUSTER_LOCK_LEASE_SECONDS
from shakenfist.operations.error_report import ErrorReport
from shakenfist.protos import database_pb2
from shakenfist.protos import database_pb2_grpc
from shakenfist.protos import shakenfist_enums_pb2
from shakenfist.schema.agentoperation_attributes import AgentOperationAttributesData
from shakenfist.schema.agentoperation_data import AgentOperationData
from shakenfist.schema.instance_attributes import InstanceAttributesData
from shakenfist.schema.instance_data import InstanceData
from shakenfist.schema.artifact_attributes import ArtifactAttributesData
from shakenfist.schema.artifact_data import ArtifactData
from shakenfist.schema.artifact_index import ArtifactIndexData
from shakenfist.schema.blob_attributes import BlobAttributesData
from shakenfist.schema.blob_data import BlobData
from shakenfist.schema.namespace_attributes import NamespaceAttributesData
from shakenfist.schema.namespace_data import NamespaceData
from shakenfist.schema.node_attributes import NodeAttributesData
from shakenfist.schema.node_daemon_state import NodeDaemonStateData
from shakenfist.schema.node_data import NodeData
from shakenfist.schema.blob_hash import BlobHash
from shakenfist.schema.blob_transfer import BlobTransfer
from shakenfist.schema.dnsmasq import DnsMasqData
from shakenfist.schema.ipam_data import IPAMData
from shakenfist.schema.network_attributes import NetworkAttributesData
from shakenfist.schema.network_data import NetworkData
from shakenfist.schema.network_interface_attributes import NetworkInterfaceAttributesData
from shakenfist.schema.network_interface_data import NetworkInterfaceData
from shakenfist.schema.ipam_reservation import IPAMReservation
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.schema.object_metadata import ObjectMetadataData
from shakenfist.schema.object_reference import ObjectReference
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.schema.sqlalchemy import get_table_columns
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
from shakenfist.schema.sqlalchemy import TABLE_CREATION_LOCK
from shakenfist.schema.upload import UploadData
from shakenfist.util import callstack as util_callstack


LOG, _ = logs.setup(__name__)

# Sentinel node name used during migration when the original node is unknown.
# This uses a name that cannot conflict with real hostnames.
MIGRATION_UNKNOWN_NODE = '__migrated_unknown_node__'


class _UUIDEncoder(json.JSONEncoder):
    """JSON encoder that converts UUID objects to strings."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


def _json_dumps(data: Any) -> str:
    """JSON-serialize data, converting UUID objects to strings."""
    return json.dumps(data, cls=_UUIDEncoder)


# Thread-local storage for database connections and gRPC channels
_local = threading.local()

# Module-level metadata for table definitions
_metadata: Optional[sa.MetaData] = None
_schema_versions_table: Optional[sa.Table] = None
_object_states_table: Optional[sa.Table] = None
_ipam_reservations_table: Optional[sa.Table] = None
_uploads_table: Optional[sa.Table] = None
_dnsmasq_table: Optional[sa.Table] = None
_object_references_table: Optional[sa.Table] = None
_blobs_table: Optional[sa.Table] = None
_blob_hashes_table: Optional[sa.Table] = None
_blob_transfers_table: Optional[sa.Table] = None
_blob_attributes_table: Optional[sa.Table] = None
_nodes_table: Optional[sa.Table] = None
_node_attributes_table: Optional[sa.Table] = None
_artifacts_table: Optional[sa.Table] = None
_artifact_attributes_table: Optional[sa.Table] = None
_artifact_indexes_table: Optional[sa.Table] = None
_network_interfaces_table: Optional[sa.Table] = None
_network_interface_attributes_table: Optional[sa.Table] = None
_networks_table: Optional[sa.Table] = None
_network_attributes_table: Optional[sa.Table] = None
_ipams_table: Optional[sa.Table] = None
_agent_operations_table: Optional[sa.Table] = None
_agent_operation_attributes_table: Optional[sa.Table] = None
_instances_table: Optional[sa.Table] = None
_instance_attributes_table: Optional[sa.Table] = None
_object_metadata_table: Optional[sa.Table] = None
_cluster_operation_targets_table: Optional[sa.Table] = None
_node_metrics_table: Optional[sa.Table] = None
_node_daemon_states_table: Optional[sa.Table] = None
_cluster_operations_table: Optional[sa.Table] = None
_cluster_operation_errors_table: Optional[sa.Table] = None
_work_queue_table: Optional[sa.Table] = None

# Current schema versions for each table. Increment when making schema changes.
# Version history:
#   All tables v1: Initial schema creation
#   All tables v2: Data migration from etcd to MariaDB
#   blob_hashes: Same pattern - v1 schema, v2 data migration from etcd
OBJECT_STATES_VERSION = 2
IPAM_RESERVATIONS_VERSION = 2
UPLOADS_VERSION = 2
DNSMASQ_VERSION = 2
OBJECT_REFERENCES_VERSION = 2
BLOBS_VERSION = 2
BLOB_HASHES_VERSION = 2
BLOB_TRANSFERS_VERSION = 2
BLOB_ATTRIBUTES_VERSION = 1
NODES_VERSION = 2
NODE_ATTRIBUTES_VERSION = 2
NAMESPACES_VERSION = 2
NAMESPACE_ATTRIBUTES_VERSION = 2
ARTIFACTS_VERSION = 3
ARTIFACT_ATTRIBUTES_VERSION = 2
ARTIFACT_INDEXES_VERSION = 2
NETWORK_INTERFACES_VERSION = 3
NETWORK_INTERFACE_ATTRIBUTES_VERSION = 2
NETWORKS_VERSION = 3
NETWORK_ATTRIBUTES_VERSION = 3
IPAMS_VERSION = 2
AGENT_OPERATIONS_VERSION = 2
AGENT_OPERATION_ATTRIBUTES_VERSION = 2
INSTANCES_VERSION = 3
INSTANCE_ATTRIBUTES_VERSION = 3
OBJECT_METADATA_VERSION = 3
# v1: schema creation.
# v2: replace column-level UNIQUE on operation_uuid with a composite
# UNIQUE on (operation_uuid, target_object_type, target_uuid). The v1
# constraint made it impossible to record more than one target per
# operation; multi-target ops (e.g. node_inst_net_iface_op) silently
# dropped all but the first declared target via IntegrityError, which
# the writer treated as idempotency. The replacement preserves real
# idempotency (same target written twice) without truncating multi-
# target ops. A non-unique idx_cot_operation index keeps lookups by
# operation_uuid fast.
CLUSTER_OPERATION_TARGETS_VERSION = 2
NODE_METRICS_VERSION = 2
# v1: schema creation. v2: data migration from node_attributes.daemon_states
# JSON column.
NODE_DAEMON_STATES_VERSION = 2
CLUSTER_OPERATIONS_VERSION = 2
CLUSTER_OPERATION_ERRORS_VERSION = 1
WORK_QUEUE_VERSION = 2
# v3: leased locks. Adds expires_at, makes acquire steal-if-expired,
# and introduces a refresh path so live holders can extend their lease.
# v4: drops idx_cluster_locks_expires and idx_cluster_locks_acquired.
# The expires index was a deadlock vector via REPEATABLE READ gap
# locks; the acquired index never had a reader.
CLUSTER_LOCKS_VERSION = 4
CLUSTER_CONFIG_VERSION = 2
EVENT_DLQ_VERSION = 2


def _use_database_service() -> bool:
    """Check if we should use the database microservice instead of direct access.

    Returns True if the database service is configured and we should use it.
    Returns False if we should use direct MariaDB access (database daemon mode).

    Only the database daemon has MARIADB_HOST configured directly. All other
    daemons access MariaDB via the database service gRPC interface.
    """
    if config.MARIADB_HOST:
        return False
    if not config.DATABASE_NODE_IP:
        return False
    return True


GRPC_TIMEOUT = 30
GRPC_RETRIES = 3
GRPC_RETRY_DELAY = 0.5


def _get_database_stub() -> Any:
    """Get or create a gRPC stub for the database service.

    Returns Any because the generated protobuf stubs are untyped.
    """
    if not hasattr(_local, 'database_channel') or _local.database_channel is None:
        _local.database_channel = grpc.insecure_channel(
            f'{config.DATABASE_NODE_IP}:{config.DATABASE_API_PORT}',
            options=[
                ('grpc.keepalive_time_ms', 10000),
                ('grpc.keepalive_timeout_ms', 5000),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.keepalive_permit_without_calls', 1),
            ]
        )
        _local.database_stub = database_pb2_grpc.DatabaseServiceStub(
            _local.database_channel)
    return _local.database_stub


def _reset_database_stub() -> None:
    """Close and reset the gRPC channel so the next call creates a fresh one."""
    if hasattr(_local, 'database_channel') and _local.database_channel is not None:
        try:
            _local.database_channel.close()
        except Exception:
            pass
    _local.database_channel = None
    _local.database_stub = None


def _grpc_call(method: Any, request: Any) -> Any:
    """Call a gRPC method with timeout, wait_for_ready, and retry.

    Retries on UNAVAILABLE and DEADLINE_EXCEEDED with a short delay
    between attempts. Resets the gRPC channel after persistent failures
    so the next attempt gets a fresh connection.

    The method parameter is a bound method on the stub (e.g.
    stub.GetNode). On retry we must re-resolve the method from a
    fresh stub, because _reset_database_stub() closes the old
    channel and any methods bound to it become invalid.

    Concurrent gRPC calls can race: thread A's retry path closes the
    channel while thread B is mid-invoke, and thread B then sees
    ``ValueError("Cannot invoke RPC on closed channel!")`` rather
    than an ``RpcError``. Treat that ValueError as retryable in the
    same way -- the channel was just closed under us, so the next
    attempt picks up a fresh stub from ``_get_database_stub()``.
    """
    retryable_codes = {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
    }
    method_name = getattr(method, '__name__', None)

    last_error: BaseException = grpc.RpcError()
    for attempt in range(GRPC_RETRIES):
        try:
            if attempt > 0 and method_name:
                stub = _get_database_stub()
                method = getattr(stub, method_name)
            return method(request, timeout=GRPC_TIMEOUT, wait_for_ready=True)
        except grpc.RpcError as e:
            last_error = e
            if e.code() not in retryable_codes:
                raise
            if attempt < GRPC_RETRIES - 1:
                time.sleep(GRPC_RETRY_DELAY * (attempt + 1))
                _reset_database_stub()
        except ValueError as e:
            if 'closed channel' not in str(e):
                raise
            last_error = e
            if attempt < GRPC_RETRIES - 1:
                time.sleep(GRPC_RETRY_DELAY * (attempt + 1))
                _reset_database_stub()

    raise last_error


# =============================================================================
# Direct MariaDB Access Functions
# These are used by the database daemon which needs direct access.
# =============================================================================

def _get_connection_url() -> str:
    """Build the MariaDB connection URL from config."""
    if not config.MARIADB_HOST:
        raise RuntimeError('MARIADB_HOST not configured')

    # Use mariadb dialect with mysqldb driver. The mariadb dialect is required
    # for MariaDB-specific types like INET4. The mysqldb driver is available
    # via python3-mysqldb.
    return (
        f'mariadb+mysqldb://{config.MARIADB_USER}:{config.MARIADB_PASSWORD}'
        f'@{config.MARIADB_HOST}:{config.MARIADB_PORT}/{config.MARIADB_DATABASE}'
    )


def _get_engine() -> sa.Engine:
    """Get or create a thread-local SQLAlchemy engine.

    We use thread-local engines to avoid connection sharing issues across
    threads. Each thread gets its own engine with its own connection pool.
    """
    if not hasattr(_local, 'engine') or _local.engine is None:
        url = _get_connection_url()
        _local.engine = sa.create_engine(
            url,
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=3600,   # Recycle connections after 1 hour
            echo=False           # Set True for SQL debugging
        )
        LOG.debug('Created new MariaDB engine for thread')
    engine: sa.Engine = _local.engine
    return engine


def _get_metadata() -> sa.MetaData:
    """Get or create the module-level MetaData object."""
    global _metadata
    if _metadata is None:
        _metadata = sa.MetaData()
    return _metadata


def _get_schema_versions_table() -> sa.Table:
    """Get or create the schema_versions table definition.

    This table tracks the schema version of each table in the database,
    allowing for incremental migrations when the schema changes.
    """
    global _schema_versions_table
    if _schema_versions_table is None:
        metadata = _get_metadata()
        _schema_versions_table = sa.Table(
            'schema_versions',
            metadata,
            sa.Column('table_name', sa.String(64), primary_key=True),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('updated_at', sa.Double(), nullable=False),
        )
    return _schema_versions_table


def _get_table_version(engine: sa.Engine, table_name: str) -> int:
    """Get the current schema version for a table.

    Returns 0 if the table has no version record (new installation).
    Returns -1 if the schema_versions table doesn't exist yet.
    """
    versions_table = _get_schema_versions_table()

    # Check if schema_versions table exists
    if not sa.inspect(engine).has_table('schema_versions'):
        return -1

    try:
        with engine.connect() as conn:
            stmt = sa.select(versions_table.c.version).where(
                versions_table.c.table_name == table_name
            )
            result = conn.execute(stmt).fetchone()
            if result is None:
                return 0
            return int(result.version)
    except OperationalError as e:
        LOG.warning(f'Failed to get schema version for {table_name}: {e}')
        return -1


def _set_table_version(engine: sa.Engine, table_name: str, version: int) -> None:
    """Set the schema version for a table."""
    versions_table = _get_schema_versions_table()

    try:
        with engine.connect() as conn:
            # Use MySQL's INSERT ... ON DUPLICATE KEY UPDATE for upsert
            stmt = sa.dialects.mysql.insert(versions_table).values(
                table_name=table_name,
                version=version,
                updated_at=time.time()
            )
            stmt = stmt.on_duplicate_key_update(
                version=version,
                updated_at=time.time()
            )
            conn.execute(stmt)
            conn.commit()
            LOG.info(f'Set schema version for {table_name} to {version}')
    except OperationalError as e:
        LOG.error(f'Failed to set schema version for {table_name}: {e}')
        raise


def _ensure_schema_versions_table(engine: sa.Engine) -> None:
    """Ensure the schema_versions table exists.

    This is the bootstrap table that must exist before we can track
    versions of other tables.
    """
    versions_table = _get_schema_versions_table()
    versions_table.metadata.create_all(engine, tables=[versions_table],
                                       checkfirst=True)


def _get_object_states_table() -> sa.Table:
    """Get or create the object_states table definition.

    The table uses a composite primary key of (object_type, object_uuid) because
    different object types can share the same UUID. For example, a Network and
    its associated IPAM both use the network's UUID.
    """
    global _object_states_table
    if _object_states_table is None:
        with TABLE_CREATION_LOCK:
            if _object_states_table is not None:
                return _object_states_table
            metadata = _get_metadata()
            if 'object_states' in metadata.tables:
                _object_states_table = metadata.tables['object_states']
                return _object_states_table
            # Build the table manually to support composite primary key
            _object_states_table = sa.Table(
                'object_states',
                metadata,
                sa.Column('object_uuid', sa.String(36), nullable=False),
                sa.Column(
                    'object_type', sa.Enum(ObjectType), nullable=False),
                sa.Column('state_value', sa.String(32), nullable=True),
                sa.Column('update_time', sa.Double(), nullable=False),
                sa.Column('message', sa.String(255), nullable=True),
                # Composite primary key
                sa.PrimaryKeyConstraint('object_type', 'object_uuid'),
                # Index for efficient queries by type and state
                sa.Index(
                    'idx_object_states_type_state',
                    'object_type', 'state_value'),
            )
    return _object_states_table


def _build_object_type_enum_values() -> str:
    """Build the ENUM values string for ObjectType.

    Returns a comma-separated list of quoted enum values for use in
    ALTER TABLE statements.
    """
    return ', '.join(f"'{ot.value}'" for ot in ObjectType)


def _build_object_filter_query(
        table: sa.Table,
        object_type: ObjectType,
        criteria: ObjectFilterCriteria) -> sa.Select[Any]:
    """Build a Select that joins a per-type table to object_states.

    Produces:

        SELECT <table>.* FROM <table>
        JOIN object_states s
          ON s.object_uuid = <table>.uuid
         AND s.object_type = <object_type>
        WHERE (optional) s.state_value IN criteria.states
          AND (optional) <table>.namespace    = criteria.namespace
          AND (optional) <table>.name         = criteria.name
          AND (optional) <table>.network_uuid = criteria.network_uuid
          AND (optional) <table>.instance_uuid = criteria.instance_uuid

    ``criteria.states`` of ``None`` or ``[]`` skips the state filter.
    Any scalar filter of ``None`` skips that filter. Callers are
    responsible for stripping fields that do not exist on the target
    table (e.g. ``name``/``namespace`` on ``network_interfaces``).
    """
    states_table = _get_object_states_table()
    # ``object_states.object_uuid`` is VARCHAR(36) (with dashes) while the
    # per-type ``uuid`` columns use SQLAlchemy ``Uuid()`` which renders as
    # CHAR(32) (no dashes) on MariaDB, so the JOIN cannot compare the two
    # columns directly. Strip the dashes off the state row's UUID before
    # comparing — the composite ``(object_type, state_value)`` index still
    # narrows the join, and the per-type primary key matches a 32-char hex
    # value.
    stmt = sa.select(table).join(
        states_table,
        sa.and_(
            sa.func.replace(states_table.c.object_uuid, '-', '')
            == table.c.uuid,
            states_table.c.object_type == object_type))
    if criteria.states:
        stmt = stmt.where(
            states_table.c.state_value.in_(criteria.states))
    if criteria.namespace is not None:
        stmt = stmt.where(table.c.namespace == criteria.namespace)
    if criteria.name is not None:
        stmt = stmt.where(table.c.name == criteria.name)
    if criteria.network_uuid is not None:
        # Convert string UUIDs to uuid.UUID objects when the column uses
        # native MariaDB UUID type (sa.Uuid), which expects objects with
        # a .hex attribute rather than plain strings.
        net_uuid: UUID | str = criteria.network_uuid
        if isinstance(net_uuid, str):
            net_uuid = UUID(net_uuid)
        stmt = stmt.where(table.c.network_uuid == net_uuid)
    if criteria.instance_uuid is not None:
        inst_uuid: UUID | str = criteria.instance_uuid
        if isinstance(inst_uuid, str):
            inst_uuid = UUID(inst_uuid)
        stmt = stmt.where(table.c.instance_uuid == inst_uuid)
    return stmt


def _ensure_object_states_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the object_states table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'object_states'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_object_states_table()

    # Version 0 or -1 means table doesn't exist yet - create it at version 1.
    # Data migration from etcd will upgrade to version 2.
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': OBJECT_STATES_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_object_metadata_table() -> sa.Table:
    """Get or create the object_metadata table definition.

    This table stores user-defined metadata for all object types. It uses a
    composite primary key of (object_type, object_uuid) following the same
    pattern as the object_states table.
    """
    global _object_metadata_table
    if _object_metadata_table is None:
        metadata = _get_metadata()
        _object_metadata_table = sa.Table(
            'object_metadata',
            metadata,
            sa.Column('object_uuid', sa.String(36), nullable=False),
            sa.Column('object_type', sa.Enum(ObjectType), nullable=False),
            sa.Column('metadata_json', sa.Text(), nullable=True),
            # Composite primary key
            sa.PrimaryKeyConstraint('object_type', 'object_uuid'),
        )
    return _object_metadata_table


def _ensure_object_metadata_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the object_metadata table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'object_metadata'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_object_metadata_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver < 3:
        LOG.info(
            f'Upgrading {table_name} from v{current_ver} to v3: '
            'dropping dead last_cluster_operation_json column.')
        with engine.connect() as conn:
            conn.execute(sa.text(
                'ALTER TABLE object_metadata '
                'DROP COLUMN IF EXISTS last_cluster_operation_json'
            ))
            conn.commit()
        current_ver = 3
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': OBJECT_METADATA_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_cluster_operation_targets_table() -> sa.Table:
    """Get or create the cluster_operation_targets table definition.

    This table records every cluster operation that targets an object
    (Instance, Artifact, Network). Each row represents one operation-to-target
    relationship with an auto-incrementing sequence number for ordering.

    The sequence_number column uses AUTO_INCREMENT so MariaDB assigns ordering
    automatically on insert. This requires a manual table definition rather
    than Pydantic-generated schema.
    """
    global _cluster_operation_targets_table
    if _cluster_operation_targets_table is None:
        metadata = _get_metadata()
        # sequence_number is the primary key so MariaDB applies
        # AUTO_INCREMENT (SQLAlchemy only emits AUTO_INCREMENT DDL for
        # the first column of the primary key on MySQL backends).
        #
        # The unique constraint is on the triple (operation_uuid,
        # target_object_type, target_uuid): one op can target many
        # objects (e.g. node_inst_net_iface_op targets instance,
        # network, and interface), but the same op-target pair must
        # not appear twice. A column-level UNIQUE on operation_uuid
        # alone (the v1 schema) silently truncated multi-target ops.
        # idx_cot_operation keeps single-column operation_uuid
        # lookups fast.
        _cluster_operation_targets_table = sa.Table(
            'cluster_operation_targets',
            metadata,
            sa.Column('sequence_number', sa.BigInteger(),
                      primary_key=True, autoincrement=True),
            sa.Column('operation_uuid', sa.String(36), nullable=False),
            sa.Column('operation_type', sa.String(64), nullable=False),
            sa.Column('target_object_type', sa.Enum(ObjectType),
                      nullable=False),
            sa.Column('target_uuid', sa.String(36), nullable=False),
            sa.Column('created_at', sa.Double(), nullable=False),
            sa.UniqueConstraint(
                'operation_uuid', 'target_object_type', 'target_uuid',
                name='uq_cot_op_target'),
            # Indexes for common query patterns
            sa.Index('idx_cot_target', 'target_object_type', 'target_uuid'),
            sa.Index('idx_cot_operation', 'operation_uuid'),
            sa.Index('idx_cot_created', 'created_at'),
        )
    return _cluster_operation_targets_table


def _ensure_cluster_operation_targets_schema(
    engine: sa.Engine
) -> dict[str, Any]:
    """Ensure the cluster_operation_targets table schema is up to date."""
    table_name = 'cluster_operation_targets'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_cluster_operation_targets_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver < 2:
        # The v1 schema declared operation_uuid UNIQUE, which made
        # multi-target operations impossible to represent: every
        # target row after the first hit the UNIQUE constraint and
        # was silently dropped by the writer's IntegrityError
        # handler. The hot-plug interface op
        # (node_inst_net_iface_op) is the path this bit hardest --
        # the network target row was lost, so
        # has_pending_cluster_operation(network) returned False
        # while the op was queued, the network maintainer raced
        # the queue worker, and the CI forbidden-string guard for
        # "Recreating not okay network on hypervisor" tripped.
        #
        # The replacement is a composite UNIQUE on the triple
        # (operation_uuid, target_object_type, target_uuid), which
        # still gives idempotency (same op-target written twice is
        # a no-op) without truncating multi-target ops. We also
        # add idx_cot_operation to preserve the fast operation_uuid
        # lookup the column-level UNIQUE was implicitly providing.
        #
        # SQLAlchemy's column-level unique=True on MariaDB creates
        # an index named after the column. The drop is wrapped in
        # IF EXISTS because some older deployments might already
        # have it under a different auto-generated name -- the
        # add-uniqueconstraint below is unconditional and will
        # surface any leftover constraint as an error there.
        LOG.info(
            f'Upgrading {table_name} from v{current_ver} to v2: '
            'replacing UNIQUE(operation_uuid) with composite UNIQUE '
            '(operation_uuid, target_object_type, target_uuid).')
        with engine.connect() as conn:
            conn.execute(sa.text(
                f'ALTER TABLE {table_name} '
                f'DROP INDEX IF EXISTS operation_uuid'))
            # MariaDB has no ADD CONSTRAINT ... IF NOT EXISTS, so we
            # tolerate an existing constraint of the same name. This
            # mirrors the network_interfaces.macaddr migration and
            # keeps the step restartable if a previous attempt
            # crashed after the ALTER but before _set_table_version.
            try:
                conn.execute(sa.text(
                    f'ALTER TABLE {table_name} '
                    f'ADD CONSTRAINT uq_cot_op_target UNIQUE '
                    f'(operation_uuid, target_object_type, target_uuid)'))
            except (IntegrityError, OperationalError) as e:
                LOG.debug(
                    f'UNIQUE constraint uq_cot_op_target already '
                    f'exists or could not be added: {e}')
            conn.execute(sa.text(
                f'CREATE INDEX IF NOT EXISTS idx_cot_operation '
                f'ON {table_name} (operation_uuid)'))
            conn.commit()
        current_ver = 2
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': CLUSTER_OPERATION_TARGETS_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_node_metrics_table() -> sa.Table:
    """Get or create the node_metrics table definition.

    This table stores ephemeral per-node resource metrics (CPU, memory, disk,
    network, queue depths, etc.) updated every 60 seconds by the resources
    daemon. The metrics payload is stored as a JSON column because it is
    inherently schemaless (~50+ fields, new ones added as needed). Individual
    metrics are already exposed as Prometheus gauges for monitoring, so SQL
    queryability of individual fields is not needed.

    One row per node, upserted each update cycle.
    """
    global _node_metrics_table
    if _node_metrics_table is None:
        metadata = _get_metadata()
        _node_metrics_table = sa.Table(
            'node_metrics',
            metadata,
            sa.Column('node_uuid', sa.Uuid(), primary_key=True),
            sa.Column('fqdn', sa.String(255), nullable=False),
            sa.Column('timestamp', sa.Double(), nullable=False),
            sa.Column('metrics_json', sa.JSON(), nullable=True),
        )
    return _node_metrics_table


def _ensure_node_metrics_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the node_metrics table schema is up to date."""
    table_name = 'node_metrics'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_node_metrics_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NODE_METRICS_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_cluster_operations_table() -> sa.Table:
    """Get or create the cluster_operations table definition.

    This table stores cluster operation headers (one row per operation).
    The full metadata dict is persisted in metadata_json; commonly
    filtered fields are extracted into their own indexed columns at
    insert time by _direct_create_cluster_operation(). State lives in
    the separate object_states table keyed on (object_type, uuid), and
    per-target tracking lives in cluster_operation_targets.

    Rows are insert-only: cluster operations are not mutated after
    creation, only deleted when the operation finishes and is cleaned
    up.
    """
    global _cluster_operations_table
    if _cluster_operations_table is None:
        metadata = _get_metadata()
        _cluster_operations_table = sa.Table(
            'cluster_operations',
            metadata,
            sa.Column('uuid', sa.Uuid(), primary_key=True),
            sa.Column('operation_type', sa.String(64), nullable=False),
            sa.Column('created_at', sa.Double(), nullable=False),
            sa.Column('node_uuid', sa.Uuid(), nullable=True),
            sa.Column('instance_uuid', sa.Uuid(), nullable=True),
            sa.Column('network_uuid', sa.Uuid(), nullable=True),
            sa.Column('priority', sa.String(32), nullable=True),
            sa.Column('metadata_json', sa.JSON(), nullable=False),
            sa.Index('ix_cluster_ops_node', 'node_uuid'),
            sa.Index('ix_cluster_ops_instance', 'instance_uuid'),
            sa.Index('ix_cluster_ops_network', 'network_uuid'),
            sa.Index('ix_cluster_ops_type_created',
                     'operation_type', 'created_at'),
        )
    return _cluster_operations_table


def _ensure_cluster_operations_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the cluster_operations table schema is up to date."""
    table_name = 'cluster_operations'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_cluster_operations_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': CLUSTER_OPERATIONS_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_cluster_operation_errors_table() -> sa.Table:
    """Get or create the cluster_operation_errors table definition.

    This table stores the structured failure record (``ErrorReport``)
    for a cluster operation. There is at most one row per operation;
    the row is written when the dispatcher catches an exception
    escaping an ``_apply_*`` method and converts it via
    ``ErrorReport.from_exception``.

    ``cluster_operations`` rows are insert-only (the table doc on
    ``_get_cluster_operations_table`` makes this contract explicit),
    so the error report cannot live as a column there. Persisting it
    separately also keeps the contract narrow: this table only exists
    to surface failure data to the REST layer and to operators.

    The ``op_uuid`` column is a natural foreign key to
    ``cluster_operations.uuid`` but no SA-level FK constraint is
    declared, matching the existing pattern across the other
    cluster-operation tables (e.g. ``cluster_operation_targets``
    does not declare an FK either).
    """
    global _cluster_operation_errors_table
    if _cluster_operation_errors_table is None:
        metadata = _get_metadata()
        _cluster_operation_errors_table = sa.Table(
            'cluster_operation_errors',
            metadata,
            sa.Column('op_uuid', sa.Uuid(), primary_key=True),
            sa.Column('error_report', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.Double(), nullable=False),
        )
    return _cluster_operation_errors_table


def _ensure_cluster_operation_errors_schema(
        engine: sa.Engine) -> dict[str, Any]:
    """Ensure the cluster_operation_errors table schema is up to date."""
    table_name = 'cluster_operation_errors'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_cluster_operation_errors_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': CLUSTER_OPERATION_ERRORS_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_work_queue_table() -> sa.Table:
    """Get or create the work_queue table definition.

    This table stores queued work items and their claim state in a
    single row per job. The claim fields (claimed_at, claimed_by)
    replace the old etcd two-prefix design where a claim was
    represented by moving a key from /sf/queue/... to /sf/processing/...
    MariaDB row locking lets us use a single table instead.

    scheduled_at supports deferred jobs: dequeue uses
    UNIX_TIMESTAMP(NOW(6)) >= scheduled_at so a future timestamp
    defers the job past its eligibility point.

    attempts is incremented on each successful claim. Phase 7's
    reaper will use it to enforce max_attempts (the "job of death"
    guard) when clearing stale claims.
    """
    global _work_queue_table
    if _work_queue_table is None:
        metadata = _get_metadata()
        _work_queue_table = sa.Table(
            'work_queue',
            metadata,
            sa.Column('id', sa.BigInteger(),
                      primary_key=True, autoincrement=True),
            sa.Column('queue_name', sa.String(255), nullable=False),
            sa.Column('scheduled_at', sa.Double(), nullable=False),
            sa.Column('claimed_at', sa.Double(), nullable=True),
            sa.Column('claimed_by', sa.String(255), nullable=True),
            sa.Column('attempts', sa.Integer(),
                      nullable=False, server_default='0'),
            sa.Column('payload', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.Double(), nullable=False),
            sa.Index(
                'ix_work_queue_ready',
                'queue_name', 'claimed_at', 'scheduled_at'),
        )
    return _work_queue_table


def _ensure_work_queue_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the work_queue table schema is up to date."""
    table_name = 'work_queue'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_work_queue_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': WORK_QUEUE_VERSION,
        'migrated': start_ver != current_ver
    }


_cluster_locks_table: Optional[sa.Table] = None


# Lease length is shared with ``shakenfist.locks.LEASE_SECONDS`` via the
# common constant in ``shakenfist.constants``.


def _get_cluster_locks_table() -> sa.Table:
    """Get or create the cluster_locks table definition.

    This table stores distributed locks previously held in etcd at
    /sflocks/{type}/{subtype}/{name}. The lock_key column stores the
    path without the /sflocks/ prefix. Acquire is either a fresh
    INSERT IGNORE or a steal-the-expired-row UPDATE; release is a
    DELETE with a lock_id CAS check; refresh extends ``expires_at``
    for the current holder. ``expires_at`` is a server-side
    ``TIMESTAMP`` so the database is the single source of truth for
    "is this lease still alive" -- per-node clock skew can never let
    a candidate steal a lock the holder still believes it owns.
    """
    global _cluster_locks_table
    if _cluster_locks_table is None:
        with TABLE_CREATION_LOCK:
            if _cluster_locks_table is not None:
                return _cluster_locks_table
            metadata = _get_metadata()
            # If another thread registered this table while we were
            # waiting on the lock, hand back the existing object
            # instead of re-registering and tripping
            # ``InvalidRequestError: Table already defined``.
            if 'cluster_locks' in metadata.tables:
                _cluster_locks_table = metadata.tables['cluster_locks']
                return _cluster_locks_table
            _cluster_locks_table = _build_cluster_locks_table(metadata)
    return _cluster_locks_table


def _build_cluster_locks_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        'cluster_locks',
        metadata,
        sa.Column('lock_key', sa.String(255), primary_key=True),
        sa.Column('holder_json', sa.JSON(), nullable=False),
        sa.Column('node_uuid', sa.String(255), nullable=False),
        sa.Column('pid', sa.Integer(), nullable=False),
        sa.Column('lock_id', sa.String(64), nullable=False),
        sa.Column('acquired_at', sa.Double(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        # idx_cluster_locks_node serves _direct_clear_stale_cluster_locks
        # (WHERE node_uuid = ?). expires_at and acquired_at have no
        # readers worth indexing for: the steal path uses a primary-key
        # lookup on lock_key inside an ON DUPLICATE KEY UPDATE, and
        # acquired_at is written but never read. A secondary index here
        # only adds a gap-lock surface under REPEATABLE READ.
        sa.Index('idx_cluster_locks_node', 'node_uuid'),
    )


def _cluster_lock_key(
        object_type: str, subtype: str, name: str) -> str:
    """Build a lock key from the gRPC request fields.

    Mirrors etcd._construct_key(prefix='sflocks') but without
    the /sflocks/ prefix so we don't leak the old etcd naming
    into MariaDB.
    """
    if subtype and name:
        return f'{object_type}/{subtype}/{name}'
    if name:
        return f'{object_type}/{name}'
    if subtype:
        return f'{object_type}/{subtype}/'
    return f'{object_type}/'


def _ensure_cluster_locks_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the cluster_locks table schema is up to date."""
    table_name = 'cluster_locks'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_cluster_locks_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver < 3:
        # v3 adds expires_at for leased locks. Pre-existing rows get a
        # fresh full lease so an in-flight holder is not immediately
        # stealable just because the cluster restarted into the new
        # schema.
        LOG.info(f'Adding expires_at to {table_name} table (version 3)')
        with engine.begin() as conn:
            cols = get_table_columns(engine, table_name)
            if 'expires_at' not in cols:
                conn.execute(sa.text(
                    f'ALTER TABLE {table_name} ADD COLUMN expires_at '
                    f'TIMESTAMP NOT NULL DEFAULT '
                    f'(NOW() + INTERVAL {CLUSTER_LOCK_LEASE_SECONDS} SECOND)'
                ))
                conn.execute(sa.text(
                    f'CREATE INDEX IF NOT EXISTS idx_cluster_locks_expires '
                    f'ON {table_name} (expires_at)'
                ))
        current_ver = 3
        _set_table_version(engine, table_name, current_ver)

    if current_ver < 4:
        # v4 drops idx_cluster_locks_expires and idx_cluster_locks_acquired.
        # Both were dead weight: the steal path resolves rows by
        # primary-key lock_key inside a single INSERT ... ON DUPLICATE
        # KEY UPDATE, and acquired_at has never had a query reader.
        # Their only side-effect was widening InnoDB's gap-lock
        # footprint under REPEATABLE READ, which produced 1213
        # deadlocks on cluster_locks under acquire/release contention.
        LOG.info(
            f'Dropping idx_cluster_locks_expires and '
            f'idx_cluster_locks_acquired from {table_name} (version 4)')
        with engine.begin() as conn:
            conn.execute(sa.text(
                f'ALTER TABLE {table_name} '
                f'DROP INDEX IF EXISTS idx_cluster_locks_expires'))
            conn.execute(sa.text(
                f'ALTER TABLE {table_name} '
                f'DROP INDEX IF EXISTS idx_cluster_locks_acquired'))
        current_ver = 4
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': CLUSTER_LOCKS_VERSION,
        'migrated': start_ver != current_ver
    }


_cluster_config_table: Optional[sa.Table] = None


def _get_cluster_config_table() -> sa.Table:
    """Get or create the cluster_config table definition.

    This table stores cluster-wide configuration previously held in etcd
    at /sf/config as a single JSON blob. Each top-level key in that blob
    becomes a row here, so concurrent writes to different keys don't
    conflict.
    """
    global _cluster_config_table
    if _cluster_config_table is None:
        metadata = _get_metadata()
        _cluster_config_table = sa.Table(
            'cluster_config',
            metadata,
            sa.Column('key_name', sa.String(128),
                      primary_key=True),
            sa.Column('value_json', sa.JSON(),
                      nullable=False),
            sa.Column('updated_at', sa.Double(),
                      nullable=False),
        )
    return _cluster_config_table


def _ensure_cluster_config_schema(
        engine: sa.Engine) -> dict[str, Any]:
    """Ensure the cluster_config table schema is up to date."""
    table_name = 'cluster_config'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_cluster_config_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': CLUSTER_CONFIG_VERSION,
        'migrated': start_ver != current_ver
    }


_event_dlq_table: Optional[sa.Table] = None


def _get_event_dlq_table() -> sa.Table:
    """Get or create the event_dlq table definition.

    This table stores event log dead-letter queue entries previously held
    in etcd at /sf/event/{object_type}/{object_uuid}/{timestamp}. The
    eventlog daemon drains this table periodically and writes events to
    per-object SQLite files.
    """
    global _event_dlq_table
    if _event_dlq_table is None:
        metadata = _get_metadata()
        _event_dlq_table = sa.Table(
            'event_dlq',
            metadata,
            sa.Column('id', sa.BigInteger(),
                      primary_key=True, autoincrement=True),
            sa.Column('object_type', sa.String(32),
                      nullable=False),
            sa.Column('object_uuid', sa.String(36),
                      nullable=False),
            sa.Column('event_timestamp', sa.Double(),
                      nullable=False),
            sa.Column('event_json', sa.JSON(),
                      nullable=False),
            sa.Column('enqueued_at', sa.Double(),
                      nullable=False),
            sa.Index('idx_event_dlq_object',
                     'object_type', 'object_uuid'),
            sa.Index('idx_event_dlq_enqueued',
                     'enqueued_at'),
        )
    return _event_dlq_table


def _ensure_event_dlq_schema(
        engine: sa.Engine) -> dict[str, Any]:
    """Ensure the event_dlq table schema is up to date."""
    table_name = 'event_dlq'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_event_dlq_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': EVENT_DLQ_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_ipam_reservations_table() -> sa.Table:
    """Get or create the ipam_reservations table definition.

    This table stores IP address reservations for all IPAMs. The combination
    of (ipam_uuid, address) is unique - each address can only be reserved
    once within a given IPAM.

    The address column uses MariaDB's INET4 type for efficient IPv4 storage
    and indexing (4 bytes vs up to 15 bytes for string representation).

    The user_type column uses an ENUM type for efficient storage (1-2 bytes
    vs 32 bytes for VARCHAR) and type safety.
    """
    global _ipam_reservations_table
    if _ipam_reservations_table is None:
        metadata = _get_metadata()
        _ipam_reservations_table = sa.Table(
            'ipam_reservations',
            metadata,
            sa.Column('ipam_uuid', sa.Uuid(), nullable=False),
            sa.Column('address', INET4(), nullable=False),
            sa.Column('reservation_type', sa.Enum(ReservationType),
                      nullable=False),
            sa.Column('user_type', sa.Enum(ObjectType), nullable=True),
            sa.Column('user_uuid', sa.Uuid(), nullable=True),
            sa.Column('reserved_at', sa.Double(), nullable=False),
            sa.Column('comment', sa.Text(), nullable=True),
            # Composite primary key ensures uniqueness
            sa.PrimaryKeyConstraint('ipam_uuid', 'address'),
            # Index for efficient queries by IPAM
            sa.Index('idx_ipam_reservations_ipam', 'ipam_uuid'),
            # Index for finding reservations by user
            sa.Index('idx_ipam_reservations_user', 'user_type', 'user_uuid'),
            # Index for finding reservations by type (e.g., deletion-halo)
            sa.Index('idx_ipam_reservations_type', 'reservation_type'),
        )
    return _ipam_reservations_table


def _build_reservation_type_enum_values() -> str:
    """Build the ENUM values string for ReservationType.

    Returns a comma-separated list of quoted enum values for use in
    ALTER TABLE statements.
    """
    return ', '.join(f"'{rt.value}'" for rt in ReservationType)


def _ensure_ipam_reservations_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the ipam_reservations table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'ipam_reservations'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_ipam_reservations_table()

    # Version 0 or -1 means table doesn't exist yet - create it at version 1.
    # Data migration from etcd will upgrade to version 2.
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': IPAM_RESERVATIONS_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_uploads_table() -> sa.Table:
    """Get or create the uploads table definition.

    This table stores static values for upload objects. Uploads are temporary
    objects used during artifact creation - they receive streamed data before
    being converted to artifacts.

    The table schema is generated from the UploadData Pydantic model in
    schema/upload.py. The uuid is the primary key, with indexes on node
    (for routing) and created_at (for cleanup).
    """
    global _uploads_table
    if _uploads_table is None:
        metadata = _get_metadata()
        _uploads_table = pydantic_to_sqlalchemy_table(
            UploadData,
            'uploads',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _uploads_table


def _ensure_uploads_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the uploads table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'uploads'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_uploads_table()

    # Version 0 or -1 means table doesn't exist yet - create it at version 1.
    # Data migration from etcd will upgrade to version 2.
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': UPLOADS_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_dnsmasq_table() -> sa.Table:
    """Get or create the dnsmasq table definition.

    This table stores static values for DnsMasq objects. DnsMasq objects
    represent running dnsmasq processes that provide DHCP and/or DNS
    services for virtual networks.

    The table schema is generated from the DnsMasqData Pydantic model in
    schema/dnsmasq.py. The uuid is the primary key (same as the owning
    network's UUID), with indexes on namespace and owner_uuid.
    """
    global _dnsmasq_table
    if _dnsmasq_table is None:
        metadata = _get_metadata()
        _dnsmasq_table = pydantic_to_sqlalchemy_table(
            DnsMasqData,
            'dnsmasq',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _dnsmasq_table


def _ensure_dnsmasq_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the dnsmasq table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'dnsmasq'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_dnsmasq_table()

    # Version 0 or -1 means table doesn't exist yet - create it at version 1.
    # Data migration from etcd will upgrade to version 2.
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': DNSMASQ_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_blobs_table() -> sa.Table:
    """Get or create the blobs table definition.

    This table stores static values for blob objects. Blobs are immutable
    binary data objects (images, snapshots, etc.) that store actual content
    on disk and track metadata in the database.

    The table schema is generated from the BlobData Pydantic model in
    schema/blob_data.py. The uuid is the primary key, with indexes on
    modified and fetched_at for cleanup and reporting queries.
    """
    global _blobs_table
    if _blobs_table is None:
        metadata = _get_metadata()
        _blobs_table = pydantic_to_sqlalchemy_table(
            BlobData,
            'blobs',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _blobs_table


def _ensure_blobs_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the blobs table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'blobs'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_blobs_table()

    # Version 0 or -1 means table doesn't exist yet - create it at version 1.
    # Data migration from etcd will upgrade to version 2.
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': BLOBS_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_object_references_table() -> sa.Table:
    """Get or create the object_references table definition.

    This table tracks references between objects, replacing the manual ref_count
    attribute on blobs. It enables queries like "which instances use this blob?"
    and "what does this instance reference?"

    The table has a compound primary key of (source_object_type, source_uuid,
    relationship, target_object_type, target_uuid) to ensure uniqueness and
    enable efficient lookups from either direction.
    """
    global _object_references_table
    if _object_references_table is None:
        metadata = _get_metadata()
        _object_references_table = pydantic_to_sqlalchemy_table(
            ObjectReference,
            'object_references',
            metadata,
            primary_key_fields=[
                'source_object_type', 'source_uuid', 'relationship',
                'target_object_type', 'target_uuid'
            ],
            include_id_column=False
        )
    return _object_references_table


def _ensure_object_references_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the object_references table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'object_references'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_object_references_table()

    # Version 0 or -1 means table doesn't exist yet - create it at version 1.
    # Data migration from etcd will upgrade to version 2.
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': OBJECT_REFERENCES_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_blob_hashes_table() -> sa.Table:
    """Get or create the blob_hashes table definition.

    This table stores blob checksums/hashes, replacing the etcd-based
    checksums attribute. It enables O(1) hash lookups via the idx_hash_lookup
    index and tracks per-node verification status.

    The table has a compound primary key of (blob_uuid, node, algorithm) to
    allow each node to independently verify blob integrity with multiple
    hash algorithms.
    """
    global _blob_hashes_table
    if _blob_hashes_table is None:
        metadata = _get_metadata()
        _blob_hashes_table = pydantic_to_sqlalchemy_table(
            BlobHash,
            'blob_hashes',
            metadata,
            primary_key_fields=['blob_uuid', 'node', 'algorithm'],
            include_id_column=False
        )
    return _blob_hashes_table


def _ensure_blob_hashes_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the blob_hashes table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'blob_hashes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_blob_hashes_table()

    # Version 0 or -1 means table doesn't exist yet - create it at version 1.
    # Data migration from etcd will upgrade to version 2.
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': BLOB_HASHES_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_blob_transfers_table() -> sa.Table:
    """Get or create the blob_transfers table definition.

    This table stores blob transfer coordination data, replacing the etcd-based
    /sf/transfer/{node}/{name} keys and incomplete_locations attributes. It
    tracks in-progress blob transfers between nodes.

    The table has a compound primary key of (source_node, transfer_name) to
    uniquely identify each transfer operation.
    """
    global _blob_transfers_table
    if _blob_transfers_table is None:
        metadata = _get_metadata()
        _blob_transfers_table = pydantic_to_sqlalchemy_table(
            BlobTransfer,
            'blob_transfers',
            metadata,
            primary_key_fields=['source_node', 'transfer_name'],
            include_id_column=False
        )
    return _blob_transfers_table


def _ensure_blob_transfers_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the blob_transfers table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'blob_transfers'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_blob_transfers_table()

    # Version 0 or -1 means table doesn't exist yet - create it at version 1.
    # Cleanup migration will upgrade to version 2.
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': BLOB_TRANSFERS_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_blob_attributes_table() -> sa.Table:
    """Get or create the blob_attributes table definition.

    This table stores mutable blob attributes, separate from the blobs
    table which stores immutable static values. This follows the
    architectural pattern of separating static values from attributes.

    See docs/operator_guide/database.md for the rationale.
    """
    global _blob_attributes_table
    if _blob_attributes_table is None:
        metadata = _get_metadata()
        _blob_attributes_table = sa.Table(
            'blob_attributes',
            metadata,
            sa.Column('uuid', sa.Uuid(), primary_key=True),
            sa.Column('size', sa.BigInteger(), nullable=False, default=0),
            sa.Column('info', sa.JSON(), nullable=True),
            sa.Column('last_used', sa.Double(), nullable=True),
            sa.Column('expires_at', sa.Double(), nullable=False, default=0.0),
            # Indexes for query optimization
            sa.Index('idx_blob_attrs_last_used', 'last_used'),
            sa.Index('idx_blob_attrs_expires_at', 'expires_at'),
            # Note: Foreign key to blobs table not enforced to allow
            # flexible migration ordering
        )
    return _blob_attributes_table


def _ensure_blob_attributes_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the blob_attributes table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'blob_attributes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_blob_attributes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': BLOB_ATTRIBUTES_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_artifacts_table() -> sa.Table:
    """Get or create the artifacts table definition.

    This table stores static values for artifact objects. Artifacts are
    versioned disk images (snapshots, labels, images) with namespace
    ownership.

    The table schema is generated from the ArtifactData Pydantic model in
    schema/artifact_data.py. The uuid is the primary key, with indexes on
    artifact_type, source_url, and namespace for query efficiency.
    """
    global _artifacts_table
    if _artifacts_table is None:
        metadata = _get_metadata()
        _artifacts_table = pydantic_to_sqlalchemy_table(
            ArtifactData,
            'artifacts',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _artifacts_table


def _get_artifact_attributes_table() -> sa.Table:
    """Get or create the artifact_attributes table definition.

    This table stores mutable artifact attributes, separate from the
    artifacts table which stores immutable static values.
    """
    global _artifact_attributes_table
    if _artifact_attributes_table is None:
        metadata = _get_metadata()
        _artifact_attributes_table = sa.Table(
            'artifact_attributes',
            metadata,
            sa.Column('uuid', sa.Uuid(), primary_key=True),
            sa.Column('max_versions', sa.Integer(), nullable=False, default=0),
            sa.Column('shared', sa.Boolean(), nullable=False, default=False),
            sa.Column('highest_index', sa.Integer(), nullable=False, default=0),
            sa.Index('idx_artifact_attrs_shared', 'shared'),
        )
    return _artifact_attributes_table


def _get_artifact_indexes_table() -> sa.Table:
    """Get or create the artifact_indexes table definition.

    This table stores artifact version indexes, mapping index numbers to
    blob UUIDs. Uses a composite primary key (artifact_uuid, index_number).
    """
    global _artifact_indexes_table
    if _artifact_indexes_table is None:
        metadata = _get_metadata()
        _artifact_indexes_table = sa.Table(
            'artifact_indexes',
            metadata,
            sa.Column('artifact_uuid', sa.Uuid(), nullable=False),
            sa.Column('index_number', sa.Integer(), nullable=False),
            sa.Column('blob_uuid', sa.Uuid(), nullable=False),
            sa.PrimaryKeyConstraint('artifact_uuid', 'index_number'),
            sa.Index('idx_artifact_idx_blob_uuid', 'blob_uuid'),
        )
    return _artifact_indexes_table


def _ensure_artifacts_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the artifacts table schema is up to date."""
    table_name = 'artifacts'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_artifacts_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver <= 2:
        LOG.info(f'Upgrading {table_name} table to version 3 '
                 '(add index on name column)')
        with engine.connect() as conn:
            try:
                conn.execute(sa.text(
                    'CREATE INDEX idx_artifacts_name ON artifacts(name)'))
                conn.commit()
            except (IntegrityError, OperationalError) as e:
                LOG.debug(
                    f'Index idx_artifacts_name already exists '
                    f'or could not be added: {e}')

        current_ver = 3
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': ARTIFACTS_VERSION,
        'migrated': start_ver != current_ver
    }


def _ensure_artifact_attributes_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the artifact_attributes table schema is up to date."""
    table_name = 'artifact_attributes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_artifact_attributes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': ARTIFACT_ATTRIBUTES_VERSION,
        'migrated': start_ver != current_ver
    }


def _ensure_artifact_indexes_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the artifact_indexes table schema is up to date."""
    table_name = 'artifact_indexes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_artifact_indexes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': ARTIFACT_INDEXES_VERSION,
        'migrated': start_ver != current_ver
    }


def ensure_schema() -> list[dict[str, Any]]:
    """Ensure all MariaDB tables exist with current schema versions.

    This is the main entry point for schema management. It creates any
    missing tables and applies migrations to bring existing tables up
    to the current version.

    Safe to call multiple times - it's idempotent. Only nodes with direct
    MariaDB access (MARIADB_HOST configured) should call this function.

    Returns:
        List of dicts describing the migration status for each table.

    Raises:
        RuntimeError: If MARIADB_HOST is not configured.
    """
    if not config.MARIADB_HOST:
        raise RuntimeError('MariaDB is not configured (MARIADB_HOST not set)')

    engine = _get_engine()
    results = []

    # First, ensure the schema_versions table exists (bootstrap)
    _ensure_schema_versions_table(engine)

    # Then ensure each application table is up to date
    results.append(_ensure_object_states_schema(engine))
    results.append(_ensure_ipam_reservations_schema(engine))
    results.append(_ensure_uploads_schema(engine))
    results.append(_ensure_dnsmasq_schema(engine))
    results.append(_ensure_blobs_schema(engine))
    results.append(_ensure_object_references_schema(engine))
    results.append(_ensure_blob_hashes_schema(engine))
    results.append(_ensure_blob_transfers_schema(engine))
    results.append(_ensure_blob_attributes_schema(engine))
    results.append(_ensure_nodes_schema(engine))
    results.append(_ensure_node_attributes_schema(engine))
    results.append(_ensure_namespaces_schema(engine))
    results.append(_ensure_namespace_attributes_schema(engine))
    results.append(_ensure_artifacts_schema(engine))
    results.append(_ensure_artifact_attributes_schema(engine))
    results.append(_ensure_artifact_indexes_schema(engine))
    results.append(_ensure_network_interfaces_schema(engine))
    results.append(_ensure_network_interface_attributes_schema(engine))
    results.append(_ensure_networks_schema(engine))
    results.append(_ensure_network_attributes_schema(engine))
    results.append(_ensure_ipams_schema(engine))
    results.append(_ensure_agent_operations_schema(engine))
    results.append(_ensure_agent_operation_attributes_schema(engine))
    results.append(_ensure_instances_schema(engine))
    results.append(_ensure_instance_attributes_schema(engine))
    results.append(_ensure_object_metadata_schema(engine))
    results.append(_ensure_cluster_operation_targets_schema(engine))
    results.append(_ensure_node_metrics_schema(engine))
    results.append(_ensure_node_daemon_states_schema(engine))
    results.append(_ensure_cluster_operations_schema(engine))
    results.append(_ensure_cluster_operation_errors_schema(engine))
    results.append(_ensure_work_queue_schema(engine))
    results.append(_ensure_cluster_locks_schema(engine))
    results.append(_ensure_cluster_config_schema(engine))
    results.append(_ensure_event_dlq_schema(engine))

    # Log summary
    migrated = [r for r in results if r['migrated']]
    if migrated:
        LOG.info(f'MariaDB schema updated: {len(migrated)} table(s) migrated')
    else:
        LOG.info('MariaDB schema verified (no migrations needed)')

    return results


# Data Migration Framework
#
# Data migrations transfer data from other storage systems (e.g., etcd) into
# MariaDB tables. They use the same version numbers as schema migrations,
# allowing a single version to track both "schema created" and "data migrated"
# states.
#
# To add a new data migration:
# 1. Register it in DATA_MIGRATIONS with table name and target version
# 2. Implement the migration function that returns a dict with migration stats
# 3. The migration runs automatically on daemon startup when version is lower
#
# Example:
#   DATA_MIGRATIONS = {
#       'blob_hashes': {
#           2: _migrate_blob_checksums_from_etcd,
#       },
#   }
#
# The migration function signature should be:
#   def _migrate_foo(engine: sa.Engine) -> dict[str, Any]
#
# It should return a dict with at least:
#   {'migrated_count': int, 'error_count': int}

# Registry of data migrations: table_name -> {version: migration_function}
# Migrations are run in version order when current version is below target.
# NOTE: This is populated at the end of this section, after migration functions
# are defined.
DATA_MIGRATIONS: dict[str, dict[int, Callable[[sa.Engine], dict[str, Any]]]] = {}


def ensure_data_migrations() -> list[dict[str, Any]]:
    """Run pending data migrations after schema setup.

    Data migrations transfer data from other storage systems (e.g., etcd)
    into MariaDB tables. They're tracked using the same version numbers
    as schema migrations, allowing a single version to represent both
    "schema created" and "data migrated" states.

    This function should be called after ensure_schema() to ensure all
    tables exist before attempting data migrations.

    Safe to call multiple times - it's idempotent. Migrations that have
    already run (version >= target) are skipped.

    Important notes for migration authors:
    - Migration functions MUST be idempotent (use upserts, not inserts)
    - If a migration fails partway through, it will retry from the start
      on the next daemon restart
    - No concurrency protection: assumes only one database daemon runs
      migrations at a time (typical deployment)

    Returns:
        List of dicts describing the migration status for each table.

    Raises:
        RuntimeError: If MARIADB_HOST is not configured.
    """
    if not config.MARIADB_HOST:
        raise RuntimeError('MariaDB is not configured (MARIADB_HOST not set)')

    if not DATA_MIGRATIONS:
        LOG.debug('No data migrations registered')
        return []

    engine = _get_engine()
    results = []

    # All registered data migrations drain residual state from etcd into
    # MariaDB. Fresh clusters (and clusters already fully migrated) have
    # ETCD_HOST unset, in which case there is nothing to drain: mark the
    # pending versions as complete without touching etcd so we neither
    # spam the log with "Cannot communicate with etcd" errors nor retry
    # the same no-op on every database daemon restart.
    if not config.ETCD_HOST:
        LOG.info(
            'No etcd server configured; marking pending data migrations '
            'as complete without running them'
        )
        for table_name, migrations in DATA_MIGRATIONS.items():
            current_ver = _get_table_version(engine, table_name)
            if current_ver <= 0:
                continue
            target_ver = max(migrations.keys())
            if current_ver >= target_ver:
                continue
            _set_table_version(engine, table_name, target_ver)
            results.append({
                'table': table_name,
                'from_version': current_ver,
                'to_version': target_ver,
                'migrated': True,
                'stats': {'migrated_count': 0, 'error_count': 0,
                          'skipped_reason': 'no etcd configured'},
            })
        return results

    for table_name, migrations in DATA_MIGRATIONS.items():
        current_ver = _get_table_version(engine, table_name)

        # Skip if table doesn't exist yet (schema migration hasn't run)
        if current_ver <= 0:
            LOG.warning(
                f'Skipping data migrations for {table_name}: '
                f'table does not exist (run ensure_schema first)'
            )
            continue

        # Run migrations in version order
        for target_ver in sorted(migrations.keys()):
            if current_ver >= target_ver:
                continue  # Already at or past this version

            migrate_func = migrations[target_ver]
            LOG.info(
                f'Running data migration for {table_name}: '
                f'v{current_ver} -> v{target_ver}'
            )

            try:
                from_ver = current_ver
                stats = migrate_func(engine)

                error_count = stats.get('error_count', 0)
                if error_count > 0:
                    LOG.error(
                        f'Data migration for {table_name} had '
                        f'{error_count} errors, not bumping version '
                        f'(will retry on next restart)')
                    results.append({
                        'table': table_name,
                        'from_version': from_ver,
                        'to_version': target_ver,
                        'migrated': False,
                        'stats': stats,
                        'error': f'{error_count} objects failed'
                    })
                    break

                _set_table_version(engine, table_name, target_ver)
                current_ver = target_ver

                results.append({
                    'table': table_name,
                    'from_version': from_ver,
                    'to_version': target_ver,
                    'migrated': True,
                    'stats': stats
                })

                LOG.info(
                    f'Data migration complete for {table_name}: '
                    f'migrated {stats.get("migrated_count", "?")} items'
                )

            except Exception as e:
                LOG.error(
                    f'Data migration failed for {table_name} '
                    f'v{current_ver} -> v{target_ver}: {e}'
                )
                results.append({
                    'table': table_name,
                    'from_version': current_ver,
                    'to_version': target_ver,
                    'migrated': False,
                    'error': str(e)
                })
                # Stop processing this table on error
                break

    # Log summary
    migrated = [r for r in results if r.get('migrated')]
    failed = [r for r in results if not r.get('migrated')]
    if migrated:
        LOG.info(f'Data migrations complete: {len(migrated)} succeeded')
    if failed:
        LOG.error(f'Data migrations failed: {len(failed)} errors')

    return results


# =============================================================================
# Data Migration Functions
#
# These functions migrate data from etcd to MariaDB. They are called
# automatically by ensure_data_migrations() when the table version is below
# the target version.
#
# All migration functions must:
# - Be idempotent (use upserts, handle already-migrated data)
# - Return a dict with 'migrated_count' and 'error_count' keys
# - Delete source data from etcd after successful migration
# - Use LOG for progress reporting (not click.echo)
# =============================================================================

# Object types that have state stored in etcd
_OBJECT_TYPES_WITH_STATE = [
    'agentoperation', 'artifact', 'blob', 'dhcp', 'instance', 'interface',
    'ipam', 'namespace', 'network', 'node', 'upload',
    'artifact_fetch_op', 'imgcache_op', 'node_blob_op', 'node_op'
]


def _migrate_etcd_object_states(engine: sa.Engine) -> dict[str, Any]:
    """Migrate object state from etcd attributes to MariaDB.

    Scans all object types that store state in etcd and migrates them
    to the object_states table.
    """
    from shakenfist import etcd

    migrated_count = 0
    error_count = 0

    for object_type in _OBJECT_TYPES_WITH_STATE:
        LOG.info(f'Migrating {object_type} state from etcd...')
        type_count = 0

        for objkey, _ in etcd.get_all(object_type, None):
            objuuid = objkey.split('/')[-1]
            state_data = etcd.get(f'attribute/{object_type}', objuuid, 'state')
            if not state_data:
                continue

            try:
                state = State(**state_data)
                success = _direct_set_state(
                    ObjectType(object_type),  # type: ignore[call-arg]
                    objuuid, state)
                if success:
                    etcd.delete(f'attribute/{object_type}', objuuid, 'state')
                    migrated_count += 1
                    type_count += 1
            except Exception as e:
                LOG.warning(f'Error migrating state for {object_type}/{objuuid}: {e}')
                error_count += 1

            if type_count > 0 and type_count % 100 == 0:
                LOG.info(f'  ... {type_count} {object_type} objects processed')

        if type_count > 0:
            LOG.info(f'  Migrated {type_count} {object_type} objects')

    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_ipam_reservations(engine: sa.Engine) -> dict[str, Any]:
    """Migrate IPAM reservations from etcd to MariaDB."""
    from shakenfist import etcd

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating IPAM reservations from etcd...')

    for key, data in etcd.get_prefix_raw('/sf/ipam_reservations/'):
        parts = key.split('/')
        if len(parts) < 5:
            LOG.warning(f'Skipping invalid IPAM key: {key}')
            error_count += 1
            continue

        ipam_uuid = parts[3]
        # address = parts[4]  # Not needed, in data

        try:
            reservation = IPAMReservation.from_legacy_dict(ipam_uuid, data)
            success = _direct_reserve_address(reservation)
            if success:
                etcd.delete_raw(key)
                migrated_count += 1
            else:
                # Already exists, just delete from etcd
                etcd.delete_raw(key)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating IPAM reservation {key}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(f'  ... {migrated_count + skipped_count} reservations processed')

    LOG.info(f'IPAM migration: {migrated_count} migrated, {skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_uploads(engine: sa.Engine) -> dict[str, Any]:
    """Migrate upload objects from etcd to MariaDB."""
    from shakenfist import etcd
    from shakenfist.upload import Upload
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating uploads from etcd...')

    for objkey, data in etcd.get_all('upload', None):
        upload_uuid = objkey.split('/')[-1]

        try:
            upload_uuid_obj = UUIDType(upload_uuid)
            success = create_upload(
                upload_uuid_obj,
                data['node'],
                data['created_at'],
                data.get('version', Upload.current_version)
            )
            if success:
                etcd.delete('upload', None, upload_uuid)
                migrated_count += 1
            else:
                # Already exists
                etcd.delete('upload', None, upload_uuid)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating upload {upload_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(f'  ... {migrated_count + skipped_count} uploads processed')

    LOG.info(f'Upload migration: {migrated_count} migrated, {skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_dnsmasq(engine: sa.Engine) -> dict[str, Any]:
    """Migrate DnsMasq objects from etcd to MariaDB."""
    from shakenfist import etcd
    from shakenfist.managed_executables.dnsmasq import DnsMasq

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating DnsMasq objects from etcd...')

    # DnsMasq uses ObjectType.DHCP for historical reasons
    for objkey, data in etcd.get_all('dhcp', None):
        dnsmasq_uuid = objkey.split('/')[-1]

        try:
            # Apply upgrades to legacy data
            version = data.get('version', DnsMasq.initial_version)
            while version < DnsMasq.current_version:
                step_name = f'_upgrade_step_{version}_to_{version + 1}'
                step_func = getattr(DnsMasq, step_name, None)
                if step_func:
                    step_func(data)
                version += 1
                data['version'] = version

            # Convert owner_type to ObjectType if it's a string
            owner_type_value = data.get('owner_type')
            if isinstance(owner_type_value, str):
                owner_type = ObjectType(owner_type_value)  # type: ignore[call-arg]
            else:
                owner_type = ObjectType.UNKNOWN

            from uuid import UUID as UUIDType
            dnsmasq_data = DnsMasqData(
                uuid=UUIDType(dnsmasq_uuid),
                namespace=data.get('namespace', 'unknown'),
                owner_type=owner_type,
                owner_uuid=UUIDType(data.get('owner_uuid', dnsmasq_uuid)),
                version=DnsMasq.current_version,
                provide_dhcp=data.get('provide_dhcp', True),
                provide_dns=data.get('provide_dns', False)
            )
            success = create_dnsmasq(dnsmasq_data)
            if success:
                etcd.delete('dhcp', None, dnsmasq_uuid)
                migrated_count += 1
            else:
                # Already exists
                etcd.delete('dhcp', None, dnsmasq_uuid)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating DnsMasq {dnsmasq_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(f'  ... {migrated_count + skipped_count} DnsMasq objects processed')

    LOG.info(f'DnsMasq migration: {migrated_count} migrated, {skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_blobs(engine: sa.Engine) -> dict[str, Any]:
    """Migrate blob static values from etcd to MariaDB.

    This migration scans all blobs in etcd and copies their static values
    (modified, fetched_at, version) to the MariaDB blobs table. After
    successful migration, the etcd entry is deleted.

    Note: depends_on is NOT migrated here - it's already in the
    object_references table as a DEPENDS_ON relationship.
    """
    from shakenfist import etcd
    from shakenfist.blob import Blob
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating blobs from etcd...')

    for objkey, data in etcd.get_all('blob', None):
        blob_uuid = objkey.split('/')[-1]

        try:
            blob_uuid_obj = UUIDType(blob_uuid)
            success = create_blob(
                blob_uuid_obj,
                data.get('modified', 0.0),
                data.get('fetched_at', 0.0),
                data.get('version', Blob.current_version)
            )
            if success:
                etcd.delete('blob', None, blob_uuid)
                migrated_count += 1
            else:
                # Already exists
                etcd.delete('blob', None, blob_uuid)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating blob {blob_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(f'  ... {migrated_count + skipped_count} blobs processed')

    LOG.info(f'Blob migration: {migrated_count} migrated, {skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_object_references(engine: sa.Engine) -> dict[str, Any]:
    """Migrate blob references from various objects to MariaDB.

    This is a complex migration that scans multiple object types to build
    the object_references table. It migrates:
    - Instance disk and nvram_template references
    - Artifact index references
    - Blob depends_on and transcoded references
    - Agent operation blob references
    - Blob location references
    """
    from shakenfist import etcd
    from shakenfist.schema.relationship_types import RelationshipType
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    def parse_uuid(uuid_str: str) -> Optional[UUIDType]:
        try:
            return UUIDType(uuid_str)
        except (ValueError, AttributeError):
            return None

    # --- Instances: disk references and nvram_template ---
    LOG.info('Migrating instance blob references...')
    for objkey, data in etcd.get_all('instance', None):
        instance_uuid = objkey.split('/')[-1]
        instance_uuid_obj = parse_uuid(instance_uuid)
        if not instance_uuid_obj:
            error_count += 1
            continue

        disk_spec = data.get('disk_spec', [])

        # Create DISK references for each disk with a blob_uuid
        for disk_idx, disk in enumerate(disk_spec):
            blob_uuid = disk.get('blob_uuid')
            if not blob_uuid:
                continue

            blob_uuid_obj = parse_uuid(blob_uuid)
            if not blob_uuid_obj:
                error_count += 1
                continue

            success = record_relationship(
                ObjectType.INSTANCE, instance_uuid_obj,
                RelationshipType.DISK, str(disk_idx),
                ObjectType.BLOB, blob_uuid_obj)
            if success:
                migrated_count += 1
            else:
                skipped_count += 1

        # Handle nvram_template
        nvram_template = data.get('nvram_template')
        if nvram_template:
            nvram_uuid_obj = parse_uuid(nvram_template)
            if nvram_uuid_obj:
                success = record_relationship(
                    ObjectType.INSTANCE, instance_uuid_obj,
                    RelationshipType.NVRAM_TEMPLATE, None,
                    ObjectType.BLOB, nvram_uuid_obj)
                if success:
                    migrated_count += 1
                else:
                    skipped_count += 1

        # Remove old blob_references attribute
        etcd.delete('attribute/instance', instance_uuid, 'blob_references')

    # --- Artifacts: index_* references ---
    LOG.info('Migrating artifact index references...')
    for objkey, _ in etcd.get_all('artifact', None):
        artifact_uuid = objkey.split('/')[-1]
        artifact_uuid_obj = parse_uuid(artifact_uuid)
        if not artifact_uuid_obj:
            error_count += 1
            continue

        # Get all index_* attributes
        for attrkey, index_data in etcd.get_all(
                'attribute/artifact', artifact_uuid, prefix='index_'):
            if not index_data:
                continue

            index_str = attrkey.split('/')[-1].replace('index_', '')
            blob_uuid = index_data.get('blob_uuid')
            if not blob_uuid:
                continue

            blob_uuid_obj = parse_uuid(blob_uuid)
            if not blob_uuid_obj:
                error_count += 1
                continue

            success = record_relationship(
                ObjectType.ARTIFACT, artifact_uuid_obj,
                RelationshipType.ARTIFACT_INDEX, index_str,
                ObjectType.BLOB, blob_uuid_obj)
            if success:
                migrated_count += 1
            else:
                skipped_count += 1

    # --- Blobs: depends_on and transcoded references ---
    LOG.info('Migrating blob depends_on and transcoded references...')
    for objkey, data in etcd.get_all('blob', None):
        blob_uuid = objkey.split('/')[-1]
        blob_uuid_obj = parse_uuid(blob_uuid)
        if not blob_uuid_obj:
            error_count += 1
            continue

        # Handle depends_on
        depends_on = data.get('depends_on')
        if depends_on:
            dep_uuid_obj = parse_uuid(depends_on)
            if dep_uuid_obj:
                success = record_relationship(
                    ObjectType.BLOB, blob_uuid_obj,
                    RelationshipType.DEPENDS_ON, None,
                    ObjectType.BLOB, dep_uuid_obj)
                if success:
                    migrated_count += 1
                else:
                    skipped_count += 1

        # Handle transcoded
        transcoded = etcd.get('attribute/blob', blob_uuid, 'transcoded')
        if transcoded:
            for style, transcoded_blob_uuid in transcoded.items():
                trans_uuid_obj = parse_uuid(transcoded_blob_uuid)
                if not trans_uuid_obj:
                    error_count += 1
                    continue

                success = record_relationship(
                    ObjectType.BLOB, blob_uuid_obj,
                    RelationshipType.TRANSCODE, style,
                    ObjectType.BLOB, trans_uuid_obj)
                if success:
                    migrated_count += 1
                else:
                    skipped_count += 1

        # Remove old attributes
        etcd.delete('attribute/blob', blob_uuid, 'ref_count')
        etcd.delete('attribute/blob', blob_uuid, 'transcoded')

    # --- AgentOperations: *_blob references ---
    LOG.info('Migrating agent operation blob references...')
    for objkey, _ in etcd.get_all('agentoperation', None):
        aop_uuid = objkey.split('/')[-1]
        aop_uuid_obj = parse_uuid(aop_uuid)
        if not aop_uuid_obj:
            error_count += 1
            continue

        results_data = etcd.get('attribute/agentoperation', aop_uuid, 'results')
        if not results_data:
            continue

        results = results_data.get('results', {})
        for result_idx, result in results.items():
            if not isinstance(result, dict):
                continue
            for key, value in result.items():
                if not key.endswith('_blob'):
                    continue
                output_type = key.replace('_blob', '')

                result_blob_uuid_obj = parse_uuid(value)
                if not result_blob_uuid_obj:
                    error_count += 1
                    continue

                success = record_relationship(
                    ObjectType.AGENTOPERATION, aop_uuid_obj,
                    RelationshipType.AGENT_OUTPUT, output_type,
                    ObjectType.BLOB, result_blob_uuid_obj)
                if success:
                    migrated_count += 1
                else:
                    skipped_count += 1

    # --- Blobs: locations -> BLOB_LOCATION ---
    LOG.info('Migrating blob location references...')
    for objkey, _ in etcd.get_all('blob', None):
        blob_uuid = objkey.split('/')[-1]
        blob_uuid_obj = parse_uuid(blob_uuid)
        if not blob_uuid_obj:
            error_count += 1
            continue

        locations_data = etcd.get('attribute/blob', blob_uuid, 'locations')
        if not locations_data:
            continue

        locations = locations_data.get('locations', [])
        for node_name in locations:
            # Legacy blob locations use node FQDNs as identifiers
            success = record_relationship(
                ObjectType.NODE, node_name,
                RelationshipType.BLOB_LOCATION, None,
                ObjectType.BLOB, blob_uuid_obj)
            if success:
                migrated_count += 1
            else:
                skipped_count += 1

        # Remove old locations attribute
        etcd.delete('attribute/blob', blob_uuid, 'locations')

    LOG.info(
        f'References migration: {migrated_count} created, '
        f'{skipped_count} skipped, {error_count} errors')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_blob_hashes(engine: sa.Engine) -> dict[str, Any]:
    """Migrate blob checksums from etcd to MariaDB blob_hashes table.

    This migration scans all blobs in etcd and migrates their checksums
    attribute to the MariaDB blob_hashes table. This enables O(1) hash
    lookups and proper per-node verification tracking.

    After migration, the checksums attributes are removed from etcd, but
    only if ALL hash records for that blob were successfully created.
    """
    from shakenfist import etcd

    migrated_count = 0
    skipped_count = 0
    error_count = 0

    LOG.info('Migrating blob checksums from etcd to MariaDB...')

    for objkey, data in etcd.get_all('blob', None):
        blob_uuid = objkey.split('/')[-1]

        # Get checksums attribute from etcd
        checksums = etcd.get('attribute/blob', blob_uuid, 'checksums')
        if not checksums:
            continue

        # Get the nodes dict (node_name -> last_verified_timestamp)
        nodes = checksums.get('nodes', {})
        if not nodes:
            # If no nodes recorded, use sentinel name as fallback
            nodes = {MIGRATION_UNKNOWN_NODE: time.time()}

        # Get file_size from blob object data
        file_size = data.get('size', 0)

        # Track success for this blob to handle partial failures
        blob_success = True
        blob_migrated = 0

        # For each algorithm that has a hash value
        for algorithm in ['sha512', 'sha256', 'sha1', 'xxh128']:
            hash_value = checksums.get(algorithm)
            if not hash_value:
                continue

            # Create a hash record for each node that has verified this blob
            for node_name, last_verified in nodes.items():
                blob_hash = BlobHash(
                    blob_uuid=blob_uuid,
                    node=node_name,
                    algorithm=algorithm,
                    hash_value=hash_value,
                    file_size=file_size,
                    computed_at=last_verified,
                    last_verified_at=last_verified,
                    verification_status='valid',
                    error_message=None
                )
                try:
                    success = upsert_blob_hash(blob_hash)
                    if success:
                        blob_migrated += 1
                    else:
                        # upsert_blob_hash returned False - skip, already exists
                        skipped_count += 1
                except Exception as e:
                    LOG.warning(
                        f'Error migrating blob hash for {blob_uuid}: {e}')
                    blob_success = False
                    error_count += 1

        # Only delete the etcd attribute if ALL upserts succeeded for this blob
        if blob_success and blob_migrated > 0:
            etcd.delete('attribute/blob', blob_uuid, 'checksums')
            migrated_count += blob_migrated
        elif not blob_success:
            LOG.warning(
                f'Partial failure migrating blob {blob_uuid}, '
                f'etcd attribute retained')

    LOG.info(
        f'Blob hashes migration: {migrated_count} created, '
        f'{skipped_count} skipped, {error_count} errors')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _cleanup_etcd_blob_transfers(engine: sa.Engine) -> dict[str, Any]:
    """Clean up old etcd transfer keys during migration.

    Unlike other migrations, we don't migrate transfer data - transfers are
    transient and any in-flight transfer during an upgrade will fail anyway.
    The requesting node will simply retry, creating a new record in MariaDB.

    This function just deletes the old etcd keys:
    - /sf/transfer/{node}/{name} - transfer handshake records
    - /sf/attribute/blob/{uuid}/incomplete_locations - progress tracking
    """
    from shakenfist import etcd

    deleted_transfers = 0
    deleted_incomplete = 0

    # Delete all transfer records
    LOG.info('Cleaning up etcd transfer records...')
    try:
        for objkey, _data in etcd.get_all('transfer', None):
            # objkey format: /sf/transfer/{node}/{name}
            parts = objkey.split('/')
            if len(parts) >= 4:
                node = parts[-2]
                name = parts[-1]
                etcd.delete('transfer', node, name)
                deleted_transfers += 1
    except Exception as e:
        LOG.warning(f'Error cleaning up transfer records: {e}')

    # Delete all incomplete_locations attributes
    LOG.info('Cleaning up etcd incomplete_locations attributes...')
    try:
        for objkey, _data in etcd.get_all('attribute/blob', None):
            # Check if this is an incomplete_locations attribute
            if objkey.endswith('/incomplete_locations'):
                parts = objkey.split('/')
                if len(parts) >= 3:
                    blob_uuid = parts[-2]
                    etcd.delete('attribute/blob', blob_uuid, 'incomplete_locations')
                    deleted_incomplete += 1
    except Exception as e:
        LOG.warning(f'Error cleaning up incomplete_locations: {e}')

    LOG.info(
        f'Blob transfers cleanup: {deleted_transfers} transfer records deleted, '
        f'{deleted_incomplete} incomplete_locations deleted')
    return {
        'deleted_transfers': deleted_transfers,
        'deleted_incomplete': deleted_incomplete
    }


def _cleanup_legacy_port_reservation_keys() -> tuple[int, int]:
    """Clean up legacy etcd port and vsock CID reservation keys.

    Earlier versions of shakenfist tracked allocated console/VDI
    ports under /sf/console/{node}/{port} via etcd.create('console',
    ...) and allocated vsock CIDs under /sf/cid/{cid} via
    etcd.create('cid', ...). Both allocators have moved to MariaDB
    (the instance_attributes.ports and .vsock_cids columns
    respectively), but those etcd keys are not touched by any of
    the per-object attribute migrations and so leak indefinitely on
    upgraded clusters.

    Called from _migrate_etcd_instance_attributes (which is the
    natural home for instance-port-related cleanup) so that it
    runs once on the same upgrade where the underlying allocator
    moved to MariaDB. The reservations themselves are no longer
    authoritative for any allocation decision, so dropping them is
    safe.

    Returns (deleted_console, deleted_cid).
    """
    from shakenfist import etcd

    deleted_console = 0
    deleted_cid = 0

    # /sf/console/{node}/{port} -> port reservation per node
    LOG.info(
        'Cleaning up legacy etcd console port reservations...')
    try:
        for objkey, _data in etcd.get_all('console', None):
            # objkey looks like /sf/console/{node}/{port}
            parts = objkey.split('/')
            if len(parts) >= 4:
                node = parts[-2]
                port = parts[-1]
                etcd.delete('console', node, port)
                deleted_console += 1
    except Exception as e:
        LOG.warning(
            f'Error cleaning up console reservations: {e}')

    # /sf/cid/{cid} -> per-CID vsock reservation
    LOG.info(
        'Cleaning up legacy etcd vsock CID reservations...')
    try:
        for objkey, _data in etcd.get_all('cid', None):
            # objkey looks like /sf/cid/{cid}
            parts = objkey.split('/')
            if len(parts) >= 3:
                cid = parts[-1]
                etcd.delete('cid', None, cid)
                deleted_cid += 1
    except Exception as e:
        LOG.warning(
            f'Error cleaning up vsock CID reservations: {e}')

    LOG.info(
        f'Port reservation cleanup: {deleted_console} console '
        f'records deleted, {deleted_cid} vsock CID records '
        f'deleted')
    return deleted_console, deleted_cid


def _migrate_etcd_blob_attributes(engine: sa.Engine) -> dict[str, Any]:
    """Migrate blob attributes from etcd to MariaDB blob_attributes table.

    This migration scans all blob UUIDs from the blobs table and migrates
    their attributes (size, info, last_used, expires_at) from etcd to MariaDB.
    After successful migration, the etcd entries are deleted.

    The etcd attribute keys are:
    - attribute/blob/{uuid}/size -> {'size': int}
    - attribute/blob/{uuid}/info -> {...qemu-img metadata...}
    - attribute/blob/{uuid}/last_used -> {'last_used': float|null}
    - attribute/blob/{uuid}/retention -> {'expires_at': float}
    """
    from shakenfist import etcd
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    # Get all blob UUIDs from the blobs table
    blobs_table = _get_blobs_table()
    with engine.connect() as conn:
        stmt = sa.select(blobs_table.c.uuid)
        result = conn.execute(stmt)
        blob_uuids = [str(row.uuid) for row in result]

    LOG.info(f'Migrating attributes for {len(blob_uuids)} blobs...')

    for blob_uuid in blob_uuids:
        try:
            # Check if attributes already exist in MariaDB
            existing = _direct_get_blob_attributes(UUIDType(blob_uuid))
            if existing:
                skipped_count += 1
                continue

            # Read attributes from etcd
            size_data = etcd.get('attribute/blob', blob_uuid, 'size')
            info_data = etcd.get('attribute/blob', blob_uuid, 'info')
            last_used_data = etcd.get('attribute/blob', blob_uuid, 'last_used')
            retention_data = etcd.get('attribute/blob', blob_uuid, 'retention')

            # Extract values with defaults
            size = size_data.get('size', 0) if size_data else 0
            info = info_data if info_data else {}
            last_used = (last_used_data.get('last_used')
                         if last_used_data else None)
            expires_at = (retention_data.get('expires_at', 0.0)
                          if retention_data else 0.0)

            # Create blob_attributes record
            attrs = BlobAttributesData(
                uuid=UUIDType(blob_uuid),
                size=size,
                info=info,
                last_used=last_used,
                expires_at=expires_at
            )
            success = _direct_create_blob_attributes(attrs)

            if success:
                # Delete etcd attributes after successful migration
                if size_data:
                    etcd.delete('attribute/blob', blob_uuid, 'size')
                if info_data:
                    etcd.delete('attribute/blob', blob_uuid, 'info')
                if last_used_data:
                    etcd.delete('attribute/blob', blob_uuid, 'last_used')
                if retention_data:
                    etcd.delete('attribute/blob', blob_uuid, 'retention')
                migrated_count += 1
            else:
                error_count += 1

        except Exception as e:
            LOG.warning(f'Failed to migrate attributes for blob {blob_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count + error_count) % 100 == 0:
            LOG.info(f'  ... {migrated_count + skipped_count + error_count} '
                     f'blobs processed')

    LOG.info(f'Blob attribute migration: {migrated_count} migrated, '
             f'{skipped_count} skipped, {error_count} errors')

    return {
        'migrated_count': migrated_count,
        'skipped_count': skipped_count,
        'error_count': error_count
    }


def _migrate_node_state_key(fqdn: str, new_uuid: str) -> None:
    """Re-key a node's object_states entry from FQDN to UUID4.

    The object_states migration runs before the node migration, so node
    state entries are stored with the FQDN as the object_uuid.  After
    the node migration assigns a real UUID4, we must update the state
    entry to use the new key so that subsequent lookups succeed.
    """
    old_state = get_state(ObjectType.NODE, fqdn)
    if old_state and old_state.value is not None:
        set_state(ObjectType.NODE, new_uuid, old_state)
        delete_state(ObjectType.NODE, fqdn)


def _migrate_etcd_nodes(engine: sa.Engine) -> dict[str, Any]:
    """Migrate node static values from etcd to MariaDB.

    Old etcd format: key='node/None/{fqdn}', value={'uuid': fqdn, 'fqdn': fqdn,
    'ip': ip, 'version': int}

    New MariaDB format: real UUID4 with FQDN as a separate indexed column.
    """
    from shakenfist import etcd as etcd_mod

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating nodes from etcd...')

    for objkey, data in etcd_mod.get_all('node', None):
        fqdn = objkey.split('/')[-1]

        try:
            node_uuid = uuid4()
            node_ip = data.get('ip', '')
            node_version = 11  # Current version

            success = create_node(node_uuid, fqdn, node_ip, node_version)
            if success:
                # Update the object_states entry from the old FQDN key
                # to the new UUID4 key. The earlier object_states
                # migration stored node state keyed by FQDN.
                _migrate_node_state_key(fqdn, str(node_uuid))
                etcd_mod.delete('node', None, fqdn)
                migrated_count += 1
            else:
                # Already exists (by FQDN unique index); look up the
                # existing UUID and re-key the state entry.
                existing = get_node_by_fqdn(fqdn)
                if existing:
                    _migrate_node_state_key(fqdn, str(existing.uuid))
                etcd_mod.delete('node', None, fqdn)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating node {fqdn}: {e}')
            error_count += 1

        total = migrated_count + skipped_count + error_count
        if total % 100 == 0:
            LOG.info(f'  ... {total} nodes processed')

    LOG.info(f'Node migration: {migrated_count} migrated, '
             f'{skipped_count} skipped, {error_count} errors')

    return {
        'migrated_count': migrated_count,
        'skipped_count': skipped_count,
        'error_count': error_count
    }


def _migrate_etcd_node_attributes(engine: sa.Engine) -> dict[str, Any]:
    """Migrate node attributes from etcd to MariaDB.

    Consolidates multiple etcd attribute keys per node into a single
    node_attributes row. Must run after _migrate_etcd_nodes so that node UUIDs
    exist in MariaDB.

    Old etcd attributes (at 'attribute/node/{fqdn}/'):
    - observed: {at: float, release: str}
    - roles: {is_etcd_master: bool, ...}
    - daemons: {daemons: [str, ...]}
    - daemon:{name}: {value: str, update_time: float, ...}
    - instances: {instances: [str, ...]}
    - dependency_versions, qemu_version, libvirt_version, python_version,
      python_implementation, process_metrics
    """
    from shakenfist import etcd as etcd_mod
    from shakenfist.schema.node_attributes import NodeAttributesData

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating node attributes from etcd...')

    # Get all nodes from MariaDB (must have been migrated by _migrate_etcd_nodes)
    all_uuids = get_all_node_uuids()

    for node_uuid_str in all_uuids:
        parsed_uuid = UUID(node_uuid_str)
        node_data = get_node(parsed_uuid)
        if not node_data:
            continue

        fqdn = node_data.fqdn

        try:
            # Read old etcd attributes
            observed = etcd_mod.get('attribute/node', fqdn, 'observed')
            roles = etcd_mod.get('attribute/node', fqdn, 'roles')
            daemons_data = etcd_mod.get('attribute/node', fqdn, 'daemons')
            instances_data = etcd_mod.get('attribute/node', fqdn, 'instances')
            dep_versions = etcd_mod.get(
                'attribute/node', fqdn, 'dependency_versions')
            qemu_ver = etcd_mod.get('attribute/node', fqdn, 'qemu_version')
            libvirt_ver = etcd_mod.get('attribute/node', fqdn, 'libvirt_version')
            python_ver = etcd_mod.get('attribute/node', fqdn, 'python_version')
            python_impl = etcd_mod.get(
                'attribute/node', fqdn, 'python_implementation')
            proc_metrics = etcd_mod.get('attribute/node', fqdn, 'process_metrics')

            # Collect daemon states from individual etcd keys
            daemon_states: dict[str, Any] = {}
            daemons_list = []
            if daemons_data:
                daemons_list = daemons_data.get('daemons', [])
            for daemon_name in daemons_list:
                ds = etcd_mod.get(
                    'attribute/node', fqdn, f'daemon:{daemon_name}')
                if ds:
                    daemon_states[daemon_name] = ds

            # Build the consolidated attributes model
            attrs = NodeAttributesData(
                uuid=parsed_uuid,
                last_seen=observed.get('at', 0) if observed else 0,
                installed_version=observed.get('release') if observed else None,
                is_etcd_master=roles.get('is_etcd_master', False) if roles else False,
                is_hypervisor=roles.get('is_hypervisor', False) if roles else False,
                is_network_node=roles.get('is_network_node', False) if roles else False,
                is_eventlog_node=(
                    roles.get('is_eventlog_node', False) if roles else False),
                instances=(
                    instances_data.get('instances', []) if instances_data else []),
                daemons=daemons_list,
                daemon_states=daemon_states,
                qemu_version=qemu_ver if isinstance(qemu_ver, list) else None,
                libvirt_version=libvirt_ver if isinstance(libvirt_ver, list) else None,
                python_version=python_ver if isinstance(python_ver, list) else None,
                python_implementation=(
                    python_impl if isinstance(python_impl, str) else None),
                dependency_versions=(
                    dep_versions if isinstance(dep_versions, dict) else {}),
                process_metrics=(
                    proc_metrics if isinstance(proc_metrics, dict) else {}),
            )

            success = create_node_attributes(attrs)
            if success:
                migrated_count += 1
            else:
                skipped_count += 1

            # Clean up old etcd attributes regardless of whether we created or
            # skipped (already existed).
            _etcd_attrs = [
                'observed', 'roles', 'daemons', 'instances',
                'dependency_versions', 'qemu_version', 'libvirt_version',
                'python_version', 'python_implementation', 'process_metrics',
            ]
            for attr in _etcd_attrs:
                etcd_mod.delete('attribute/node', fqdn, attr)
            for daemon_name in daemons_list:
                etcd_mod.delete('attribute/node', fqdn, f'daemon:{daemon_name}')

        except Exception as e:
            LOG.warning(f'Failed to migrate attributes for node {fqdn}: {e}')
            error_count += 1

        total = migrated_count + skipped_count + error_count
        if total % 100 == 0:
            LOG.info(f'  ... {total} node attributes processed')

    LOG.info(
        f'Node attribute migration: '
        f'{migrated_count} migrated, '
        f'{skipped_count} skipped, '
        f'{error_count} errors')

    return {
        'migrated_count': migrated_count,
        'skipped_count': skipped_count,
        'error_count': error_count
    }


def _migrate_etcd_namespaces(engine: sa.Engine) -> dict[str, Any]:
    """Migrate namespace static values from etcd to MariaDB.

    Old etcd format: key='namespace/None/{name}', value={'uuid': name, 'version': int}
    New MariaDB format: name (VARCHAR PK), version (INT).
    """
    from shakenfist import etcd as etcd_mod

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating namespaces from etcd...')

    for objkey, data in etcd_mod.get_all('namespace', None):
        name = objkey.split('/')[-1]

        try:
            version = data.get('version', 7)
            success = create_namespace(name, version)
            if success:
                etcd_mod.delete('namespace', None, name)
                migrated_count += 1
            else:
                etcd_mod.delete('namespace', None, name)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating namespace {name}: {e}')
            error_count += 1

        total = migrated_count + skipped_count + error_count
        if total % 100 == 0:
            LOG.info(f'  ... {total} namespaces processed')

    LOG.info(f'Namespace migration: {migrated_count} migrated, '
             f'{skipped_count} skipped, {error_count} errors')

    return {
        'migrated_count': migrated_count,
        'skipped_count': skipped_count,
        'error_count': error_count
    }


def _migrate_etcd_namespace_attributes(engine: sa.Engine) -> dict[str, Any]:
    """Migrate namespace attributes from etcd to MariaDB.

    Consolidates separate etcd attribute keys per namespace into a single
    namespace_attributes row. Must run after _migrate_etcd_namespaces.

    Old etcd attributes (at 'attribute/namespace/{name}/'):
    - keys: {'nonced_keys': {...}}
    - trust: {'full_trust': [...]}
    """
    from shakenfist import etcd as etcd_mod
    from shakenfist.schema.namespace_attributes import NamespaceAttributesData

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating namespace attributes from etcd...')

    all_names = get_all_namespace_names()

    for name in all_names:
        try:
            keys_data = etcd_mod.get('attribute/namespace', name, 'keys')
            trust_data = etcd_mod.get('attribute/namespace', name, 'trust')

            keys: dict[str, Any] = {'nonced_keys': {}}
            if keys_data:
                keys = keys_data

            trust = ['system']
            if trust_data and 'full_trust' in trust_data:
                trust = trust_data['full_trust']

            attrs = NamespaceAttributesData(name=name, keys=keys, trust=trust)
            success = create_namespace_attributes(attrs)

            if success:
                # Clean up etcd entries
                if keys_data:
                    etcd_mod.delete('attribute/namespace', name, 'keys')
                if trust_data:
                    etcd_mod.delete('attribute/namespace', name, 'trust')
                migrated_count += 1
            else:
                skipped_count += 1

        except Exception as e:
            LOG.warning(f'Error migrating namespace attributes for {name}: {e}')
            error_count += 1

        total = migrated_count + skipped_count + error_count
        if total % 100 == 0:
            LOG.info(f'  ... {total} namespace attributes processed')

    LOG.info(f'Namespace attribute migration: {migrated_count} migrated, '
             f'{skipped_count} skipped, {error_count} errors')

    return {
        'migrated_count': migrated_count,
        'skipped_count': skipped_count,
        'error_count': error_count
    }


def _migrate_etcd_artifacts(engine: sa.Engine) -> dict[str, Any]:
    """Migrate artifact static values from etcd to MariaDB.

    This migration scans all artifacts in etcd and copies their static values
    to the MariaDB artifacts table. After successful migration, the etcd
    entry is deleted.
    """
    from shakenfist import etcd
    from shakenfist.artifact import Artifact
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating artifacts from etcd...')

    for objkey, data in etcd.get_all('artifact', None):
        artifact_uuid = objkey.split('/')[-1]

        try:
            artifact_uuid_obj = UUIDType(artifact_uuid)
            success = create_artifact(
                artifact_uuid_obj,
                data.get('artifact_type', ''),
                data.get('source_url', ''),
                data.get('name', ''),
                data.get('namespace', ''),
                data.get('version', Artifact.current_version)
            )
            if success:
                etcd.delete('artifact', None, artifact_uuid)
                migrated_count += 1
            else:
                # Already exists
                etcd.delete('artifact', None, artifact_uuid)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating artifact {artifact_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} artifacts processed')

    LOG.info(f'Artifact migration: {migrated_count} migrated, '
             f'{skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_artifact_attributes(engine: sa.Engine) -> dict[str, Any]:
    """Migrate artifact attributes from etcd to MariaDB.

    Reads max_versions, shared, and highest_index from etcd attributes
    and creates artifact_attributes records.

    The etcd attribute keys are:
    - attribute/artifact/{uuid}/max_versions -> {'max_versions': int}
    - attribute/artifact/{uuid}/shared -> {'shared': bool}
    - attribute/artifact/{uuid}/highest_index -> {'index': int}
    """
    from shakenfist import etcd
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    # Get all artifact UUIDs from the artifacts table
    artifacts_table = _get_artifacts_table()
    with engine.connect() as conn:
        stmt = sa.select(artifacts_table.c.uuid)
        result = conn.execute(stmt)
        artifact_uuids = [str(row.uuid) for row in result]

    LOG.info(f'Migrating attributes for {len(artifact_uuids)} artifacts...')

    for artifact_uuid in artifact_uuids:
        try:
            existing = _direct_get_artifact_attributes(
                UUIDType(artifact_uuid))
            if existing:
                skipped_count += 1
                continue

            max_versions_data = etcd.get(
                'attribute/artifact', artifact_uuid, 'max_versions')
            shared_data = etcd.get(
                'attribute/artifact', artifact_uuid, 'shared')
            highest_index_data = etcd.get(
                'attribute/artifact', artifact_uuid, 'highest_index')

            max_versions = (max_versions_data.get('max_versions', 0)
                            if max_versions_data else 0)
            shared = (shared_data.get('shared', False)
                      if shared_data else False)
            highest_index = (highest_index_data.get('index', 0)
                             if highest_index_data else 0)

            attrs = ArtifactAttributesData(
                uuid=UUIDType(artifact_uuid),
                max_versions=max_versions,
                shared=shared,
                highest_index=highest_index
            )
            success = _direct_create_artifact_attributes(attrs)

            if success:
                if max_versions_data:
                    etcd.delete(
                        'attribute/artifact', artifact_uuid, 'max_versions')
                if shared_data:
                    etcd.delete(
                        'attribute/artifact', artifact_uuid, 'shared')
                if highest_index_data:
                    etcd.delete(
                        'attribute/artifact', artifact_uuid, 'highest_index')
                migrated_count += 1
            else:
                error_count += 1

        except Exception as e:
            LOG.warning(
                f'Failed to migrate attributes for artifact '
                f'{artifact_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count + error_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count + error_count} '
                f'artifacts processed')

    LOG.info(f'Artifact attribute migration: {migrated_count} migrated, '
             f'{skipped_count} skipped, {error_count} errors')

    return {
        'migrated_count': migrated_count,
        'skipped_count': skipped_count,
        'error_count': error_count
    }


def _migrate_etcd_artifact_indexes(engine: sa.Engine) -> dict[str, Any]:
    """Migrate artifact indexes from etcd to MariaDB.

    Reads all index_* attributes from etcd for each artifact and creates
    artifact_indexes records.

    The etcd attribute keys are:
    - attribute/artifact/{uuid}/index_NNNNNNNNNNNN -> {
          'index': int, 'blob_uuid': str}
    """
    from shakenfist import etcd
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    # Get all artifact UUIDs from the artifacts table
    artifacts_table = _get_artifacts_table()
    with engine.connect() as conn:
        stmt = sa.select(artifacts_table.c.uuid)
        result = conn.execute(stmt)
        artifact_uuids = [str(row.uuid) for row in result]

    LOG.info(f'Migrating indexes for {len(artifact_uuids)} artifacts...')

    for artifact_uuid in artifact_uuids:
        try:
            for key, data in etcd.get_all(
                    'attribute/artifact', artifact_uuid, prefix='index_'):
                if not data or 'index' not in data or 'blob_uuid' not in data:
                    continue

                index_number = data['index']
                blob_uuid = data['blob_uuid']

                try:
                    success = _direct_create_artifact_index(
                        UUIDType(artifact_uuid),
                        index_number,
                        UUIDType(blob_uuid)
                    )
                    if success:
                        etcd.delete(
                            'attribute/artifact', artifact_uuid, key)
                        migrated_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    LOG.warning(
                        f'Failed to migrate index {key} for artifact '
                        f'{artifact_uuid}: {e}')
                    error_count += 1

        except Exception as e:
            LOG.warning(
                f'Failed to scan indexes for artifact '
                f'{artifact_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count + error_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count + error_count} '
                f'indexes processed')

    LOG.info(f'Artifact index migration: {migrated_count} migrated, '
             f'{skipped_count} skipped, {error_count} errors')

    return {
        'migrated_count': migrated_count,
        'skipped_count': skipped_count,
        'error_count': error_count
    }


def _migrate_etcd_network_interfaces(engine: sa.Engine) -> dict[str, Any]:
    """Migrate NetworkInterface static values from etcd to MariaDB."""
    from shakenfist import etcd
    from shakenfist.network.interface import NetworkInterface
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating NetworkInterface objects from etcd...')

    for objkey, data in etcd.get_all('networkinterface', None):
        ni_uuid = objkey.split('/')[-1]

        try:
            # Apply upgrades to legacy data
            version = data.get('version', NetworkInterface.initial_version)
            while version < NetworkInterface.current_version:
                step_name = f'_upgrade_step_{version}_to_{version + 1}'
                step_func = getattr(NetworkInterface, step_name, None)
                if step_func:
                    step_func(data)
                version += 1
                data['version'] = version

            ni_data = NetworkInterfaceData(
                uuid=UUIDType(ni_uuid),
                network_uuid=UUIDType(data['network_uuid']),
                instance_uuid=UUIDType(data['instance_uuid']),
                macaddr=data.get('macaddr', ''),
                ipv4=data.get('ipv4') or None,
                order=data.get('order', 0),
                model=data.get('model', 'virtio'),
                version=NetworkInterface.current_version,
            )
            success = create_network_interface(ni_data)
            if success:
                etcd.delete('networkinterface', None, ni_uuid)
                migrated_count += 1
            else:
                etcd.delete('networkinterface', None, ni_uuid)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating NetworkInterface {ni_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} '
                f'NetworkInterface objects processed')

    # Clean up macaddress allocation keys — uniqueness is now enforced by
    # the UNIQUE constraint on network_interfaces.macaddr.
    mac_cleaned = 0
    for objkey, _data in etcd.get_all('macaddress', None):
        mac = objkey.split('/')[-1]
        etcd.delete('macaddress', None, mac)
        mac_cleaned += 1
    if mac_cleaned:
        LOG.info(f'Cleaned up {mac_cleaned} etcd macaddress keys')

    LOG.info(
        f'NetworkInterface migration: {migrated_count} migrated, '
        f'{skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_network_interface_attributes(
        engine: sa.Engine) -> dict[str, Any]:
    """Migrate NetworkInterface attributes from etcd to MariaDB.

    Reads the 'floating' attribute from etcd for each NetworkInterface and
    creates network_interface_attributes records.
    """
    from shakenfist import etcd
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    # Get all network interface UUIDs from the network_interfaces table
    ni_table = _get_network_interfaces_table()
    with engine.connect() as conn:
        stmt = sa.select(ni_table.c.uuid)
        result = conn.execute(stmt)
        ni_uuids = [str(row.uuid) for row in result]

    LOG.info(
        f'Migrating attributes for {len(ni_uuids)} '
        f'NetworkInterface objects...')

    for ni_uuid in ni_uuids:
        try:
            # Check if already migrated
            existing = get_network_interface_attributes(UUIDType(ni_uuid))
            if existing:
                skipped_count += 1
                continue

            # Read floating attribute from etcd
            floating_data = etcd.get(
                'attribute/networkinterface', ni_uuid, 'floating')
            floating_address = None
            if floating_data:
                floating_address = floating_data.get('floating_address')

            attrs = NetworkInterfaceAttributesData(
                uuid=UUIDType(ni_uuid),
                floating_address=floating_address,
            )
            success = create_network_interface_attributes(attrs)
            if success:
                # Delete the etcd attribute after migration
                if floating_data:
                    etcd.delete(
                        'attribute/networkinterface', ni_uuid, 'floating')
                migrated_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            LOG.warning(
                f'Error migrating NetworkInterface attributes '
                f'{ni_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} '
                f'NetworkInterface attributes processed')

    LOG.info(
        f'NetworkInterface attributes migration: {migrated_count} '
        f'migrated, {skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_ipams(engine: sa.Engine) -> dict[str, Any]:
    """Migrate IPAM static values from etcd to MariaDB."""
    from shakenfist import etcd
    from shakenfist.ipam import IPAM
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating IPAM objects from etcd...')

    for objkey, data in etcd.get_all('ipam', None):
        ipam_uuid = objkey.split('/')[-1]

        try:
            # Apply upgrades to legacy data
            version = data.get('version', IPAM.initial_version)
            while version < IPAM.current_version:
                step_name = f'_upgrade_step_{version}_to_{version + 1}'
                step_func = getattr(IPAM, step_name, None)
                if step_func:
                    step_func(data)
                version += 1
                data['version'] = version

            ipam_data = IPAMData(
                uuid=UUIDType(ipam_uuid),
                namespace=data.get('namespace'),
                network_uuid=UUIDType(data['network_uuid']),
                ipblock=data['ipblock'],
                version=IPAM.current_version,
            )
            success = create_ipam(ipam_data)
            if success:
                etcd.delete('ipam', None, ipam_uuid)
                migrated_count += 1
            else:
                etcd.delete('ipam', None, ipam_uuid)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating IPAM {ipam_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} '
                f'IPAM objects processed')

    LOG.info(
        f'IPAM migration: {migrated_count} migrated, '
        f'{skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_networks(engine: sa.Engine) -> dict[str, Any]:
    """Migrate Network static values from etcd to MariaDB."""
    from shakenfist import etcd
    from shakenfist.network.network import Network
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating Network objects from etcd...')

    for objkey, data in etcd.get_all('network', None):
        net_uuid = objkey.split('/')[-1]

        try:
            # Apply upgrades to legacy data
            version = data.get('version', Network.initial_version)
            while version < Network.current_version:
                step_name = f'_upgrade_step_{version}_to_{version + 1}'
                step_func = getattr(Network, step_name, None)
                if step_func:
                    step_func(data)
                version += 1
                data['version'] = version

            net_data = NetworkData(
                uuid=UUIDType(net_uuid),
                name=data.get('name', ''),
                namespace=data.get('namespace'),
                netblock=data.get('netblock', ''),
                provide_dhcp=data.get('provide_dhcp', False),
                provide_nat=data.get('provide_nat', False),
                provide_dns=data.get('provide_dns', False),
                vxid=data.get('vxid', 0),
                egress_nic=data.get('egress_nic'),
                mesh_nic=data.get('mesh_nic'),
                version=Network.current_version,
            )
            success = create_network(net_data)
            if success:
                etcd.delete('network', None, net_uuid)
                migrated_count += 1
            else:
                etcd.delete('network', None, net_uuid)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating Network {net_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} '
                f'Network objects processed')

    # Clean up vxlan allocation keys — uniqueness is now enforced by
    # the UNIQUE constraint on networks.vxid.
    vxlan_cleaned = 0
    for objkey, _data in etcd.get_all('vxlan', None):
        vxid = objkey.split('/')[-1]
        etcd.delete('vxlan', None, vxid)
        vxlan_cleaned += 1

    LOG.info(
        f'Network migration: {migrated_count} migrated, '
        f'{skipped_count} skipped, '
        f'{vxlan_cleaned} vxlan keys cleaned')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_network_attributes(
        engine: sa.Engine) -> dict[str, Any]:
    """Migrate Network attributes from etcd to MariaDB."""
    from shakenfist import etcd
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating Network attributes from etcd...')

    # Get all network UUIDs from the static values table
    static_table = _get_networks_table()
    with engine.connect() as conn:
        stmt = sa.select(static_table.c.uuid)
        result = conn.execute(stmt)
        net_uuids = [str(row.uuid) for row in result]

    for net_uuid in net_uuids:
        try:
            existing = _direct_get_network_attributes(
                UUIDType(net_uuid))
            if existing:
                skipped_count += 1
                continue

            # Read attributes from etcd
            routing_data = etcd.get(
                'attribute/network', net_uuid, 'routing')
            dns_data = etcd.get(
                'attribute/network', net_uuid, 'hosteddns')

            # Extract values with defaults
            floating_gateway = None
            if routing_data:
                floating_gateway = routing_data.get(
                    'floating_gateway')

            hosteddns: dict[str, Any] = {}
            if dns_data:
                hosteddns = dns_data

            attrs = NetworkAttributesData(
                uuid=UUIDType(net_uuid),
                floating_gateway=floating_gateway,
                hosteddns=hosteddns,
            )
            success = _direct_create_network_attributes(attrs)

            if success:
                # Delete etcd attributes after successful migration. The
                # ``networkinterfaces`` etcd key is also dropped here even
                # though we no longer read it -- phase 7 made the cached
                # list redundant, but stale etcd data should still be
                # cleaned up so re-migrations are idempotent.
                etcd.delete(
                    'attribute/network', net_uuid, 'routing')
                etcd.delete(
                    'attribute/network', net_uuid,
                    'networkinterfaces')
                etcd.delete(
                    'attribute/network', net_uuid, 'hosteddns')
                migrated_count += 1
            else:
                error_count += 1

        except Exception as e:
            LOG.warning(
                f'Failed to migrate attributes for '
                f'Network {net_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} '
                f'Network attributes processed')

    LOG.info(
        f'Network attributes migration: {migrated_count} '
        f'migrated, {skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_agent_operations(engine: sa.Engine) -> dict[str, Any]:
    """Migrate AgentOperation static values from etcd to MariaDB."""
    from shakenfist import etcd
    from shakenfist.operations.agentoperation import AgentOperation
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating AgentOperation objects from etcd...')

    for objkey, data in etcd.get_all('agentoperation', None):
        aop_uuid = objkey.split('/')[-1]

        try:
            # Apply upgrades to legacy data
            version = data.get('version', AgentOperation.initial_version)
            while version < AgentOperation.current_version:
                step_name = f'_upgrade_step_{version}_to_{version + 1}'
                step_func = getattr(AgentOperation, step_name, None)
                if step_func:
                    step_func(data)
                version += 1
                data['version'] = version

            aop_data = AgentOperationData(
                uuid=UUIDType(aop_uuid),
                namespace=data.get('namespace', ''),
                instance_uuid=UUIDType(data['instance_uuid']),
                commands=data.get('commands', []),
                version=AgentOperation.current_version,
            )
            success = create_agent_operation(aop_data)
            if success:
                etcd.delete('agentoperation', None, aop_uuid)
                migrated_count += 1
            else:
                etcd.delete('agentoperation', None, aop_uuid)
                skipped_count += 1
        except Exception as e:
            LOG.warning(f'Error migrating AgentOperation {aop_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} '
                f'AgentOperation objects processed')

    LOG.info(
        f'AgentOperation migration: {migrated_count} migrated, '
        f'{skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_agent_operation_attributes(
        engine: sa.Engine) -> dict[str, Any]:
    """Migrate AgentOperation attributes from etcd to MariaDB."""
    from shakenfist import etcd
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating AgentOperation attributes from etcd...')

    # Get all agent operation UUIDs from the static values table
    static_table = _get_agent_operations_table()
    with engine.connect() as conn:
        stmt = sa.select(static_table.c.uuid)
        result = conn.execute(stmt)
        aop_uuids = [str(row.uuid) for row in result]

    for aop_uuid in aop_uuids:
        try:
            existing = _direct_get_agent_operation_attributes(
                UUIDType(aop_uuid))
            if existing:
                skipped_count += 1
                continue

            # Read results attribute from etcd
            results_data = etcd.get(
                'attribute/agentoperation', aop_uuid, 'results')

            results: dict[str, Any] = {}
            if results_data:
                results = results_data.get('results', {})

            attrs = AgentOperationAttributesData(
                uuid=UUIDType(aop_uuid),
                results=results,
            )
            success = _direct_create_agent_operation_attributes(attrs)

            if success:
                # Delete etcd attributes after successful migration
                etcd.delete(
                    'attribute/agentoperation', aop_uuid, 'results')
                migrated_count += 1
            else:
                error_count += 1

        except Exception as e:
            LOG.warning(
                f'Failed to migrate attributes for '
                f'AgentOperation {aop_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} '
                f'AgentOperation attributes processed')

    LOG.info(
        f'AgentOperation attributes migration: {migrated_count} '
        f'migrated, {skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_instances(engine: sa.Engine) -> dict[str, Any]:
    """Migrate Instance static values from etcd to MariaDB."""
    from shakenfist import etcd
    from shakenfist.instance import Instance
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating Instance objects from etcd...')

    for objkey, data in etcd.get_all('instance', None):
        inst_uuid = objkey.split('/')[-1]

        try:
            # Apply upgrades to legacy data
            version = data.get('version', Instance.initial_version)
            while version < Instance.current_version:
                step_name = f'_upgrade_step_{version}_to_{version + 1}'
                step_func = getattr(Instance, step_name, None)
                if step_func:
                    step_func(data)
                version += 1
                data['version'] = version

            # Normalize values for Pydantic validation
            requested_placement = data.get('requested_placement')
            if not isinstance(requested_placement, dict):
                requested_placement = None

            video = data.get('video', {})
            if not isinstance(video, dict):
                video = {'model': str(video)} if video else {}

            side_channels = data.get('side_channels')
            if not isinstance(side_channels, list):
                side_channels = []

            inst_data = InstanceData(
                uuid=UUIDType(inst_uuid),
                cpus=data.get('cpus', 0),
                disk_spec=data.get('disk_spec', []),
                memory=data.get('memory', 0),
                name=data.get('name', ''),
                namespace=data.get('namespace', ''),
                requested_placement=requested_placement,
                ssh_key=data.get('ssh_key'),
                user_data=data.get('user_data'),
                video=video,
                uefi=data.get('uefi', False),
                configdrive=data.get(
                    'configdrive', 'openstack-disk'),
                nvram_template=data.get('nvram_template'),
                secure_boot=data.get('secure_boot', False),
                machine_type=data.get('machine_type', 'pc'),
                side_channels=side_channels,
                version=Instance.current_version,
            )
            success = create_instance(inst_data)
            if success:
                etcd.delete('instance', None, inst_uuid)
                migrated_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            LOG.warning(
                f'Error migrating Instance {inst_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} '
                f'Instance objects processed')

    LOG.info(
        f'Instance migration: {migrated_count} migrated, '
        f'{skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_instance_attributes(
        engine: sa.Engine) -> dict[str, Any]:
    """Migrate Instance attributes from etcd to MariaDB."""
    from shakenfist import etcd
    from uuid import UUID as UUIDType

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating Instance attributes from etcd...')

    # Get all instance UUIDs from the static values table
    static_table = _get_instances_table()
    with engine.connect() as conn:
        stmt = sa.select(static_table.c.uuid)
        result = conn.execute(stmt)
        inst_uuids = [str(row.uuid) for row in result]

    for inst_uuid in inst_uuids:
        try:
            existing = _direct_get_instance_attributes(
                UUIDType(inst_uuid))
            if existing:
                skipped_count += 1
                continue

            # Read each attribute from etcd
            placement = etcd.get(
                'attribute/instance', inst_uuid, 'placement')
            power_state = etcd.get(
                'attribute/instance', inst_uuid, 'power_state')
            ports = etcd.get(
                'attribute/instance', inst_uuid, 'ports')
            enforced_deletes = etcd.get(
                'attribute/instance', inst_uuid,
                'enforced_deletes')
            block_devices = etcd.get(
                'attribute/instance', inst_uuid, 'block_devices')
            agent_state = etcd.get(
                'attribute/instance', inst_uuid, 'agent_state')
            agent_attributes = etcd.get(
                'attribute/instance', inst_uuid,
                'agent_attributes')
            agent_operations = etcd.get(
                'attribute/instance', inst_uuid,
                'agent_operations')
            kvm_pid_data = etcd.get(
                'attribute/instance', inst_uuid, 'kvm_pid')
            error_data = etcd.get(
                'attribute/instance', inst_uuid, 'error')

            # Extract values from etcd format
            kvm_pid = None
            if kvm_pid_data and isinstance(
                    kvm_pid_data, dict):
                kvm_pid = kvm_pid_data.get('pid')

            error_message = None
            if error_data and isinstance(
                    error_data, dict):
                error_message = error_data.get('message')

            # Migrate vsock_cid:* dynamic keys into a single
            # vsock_cids dict
            vsock_cids: dict[str, int] = {}
            vsock_keys_to_delete: list[str] = []
            for attr_key, attr_val in etcd.get_all(
                    'attribute/instance', inst_uuid):
                if attr_key.startswith('vsock_cid:'):
                    channel = attr_key[len('vsock_cid:'):]
                    if isinstance(attr_val, (int, float)):
                        vsock_cids[channel] = int(attr_val)
                    vsock_keys_to_delete.append(attr_key)

            attrs = InstanceAttributesData(
                uuid=UUIDType(inst_uuid),
                placement=placement if isinstance(
                    placement, dict) else None,
                power_state=power_state if isinstance(
                    power_state, dict) else None,
                ports=ports if isinstance(
                    ports, dict) else None,
                enforced_deletes=enforced_deletes if isinstance(
                    enforced_deletes, dict) else None,
                block_devices=block_devices if isinstance(
                    block_devices, dict) else None,
                agent_state=agent_state if isinstance(
                    agent_state, dict) else None,
                agent_attributes=agent_attributes if isinstance(
                    agent_attributes, dict) else None,
                agent_operations=agent_operations if isinstance(
                    agent_operations, dict) else None,
                kvm_pid=kvm_pid,
                error_message=error_message,
                vsock_cids=vsock_cids if vsock_cids else None,
            )
            success = _direct_create_instance_attributes(attrs)

            if success:
                # Delete etcd attributes after successful migration
                for attr_name in [
                    'placement', 'power_state', 'ports',
                    'enforced_deletes', 'block_devices',
                    'interfaces', 'agent_state',
                    'agent_attributes', 'agent_operations',
                    'kvm_pid', 'error',
                ] + vsock_keys_to_delete:
                    etcd.delete(
                        'attribute/instance', inst_uuid,
                        attr_name)
                migrated_count += 1
            else:
                error_count += 1

        except Exception as e:
            LOG.warning(
                f'Failed to migrate attributes for '
                f'Instance {inst_uuid}: {e}')
            error_count += 1

        if (migrated_count + skipped_count) % 100 == 0:
            LOG.info(
                f'  ... {migrated_count + skipped_count} '
                f'Instance attributes processed')

    LOG.info(
        f'Instance attributes migration: {migrated_count} '
        f'migrated, {skipped_count} skipped')

    # Once instance attributes are in MariaDB, the legacy
    # /sf/console/* and /sf/cid/* reservation keys (used by the
    # old etcd-backed port and vsock CID allocators) are no
    # longer referenced by anything. Clean them up here so the
    # cleanup is gated on the same version bump as the migration
    # that obsoletes them.
    deleted_console, deleted_cid = (
        _cleanup_legacy_port_reservation_keys())

    return {
        'migrated_count': migrated_count,
        'error_count': error_count,
        'deleted_console_reservations': deleted_console,
        'deleted_vsock_cid_reservations': deleted_cid,
    }


def _migrate_etcd_object_metadata(
    engine: sa.Engine
) -> dict[str, Any]:
    """Migrate metadata and last_cluster_operation from etcd to MariaDB.

    Iterates all object types and migrates metadata and
    last_cluster_operation attributes from etcd into the shared
    object_metadata table.

    This migration must run after all object type migrations (phases 1-13)
    because those migrations may move or delete etcd keys.
    """
    from shakenfist import etcd

    migrated_count = 0
    skipped_count = 0
    error_count = 0

    for ot in ObjectType:
        # Collect UUIDs for this object type by scanning etcd attribute keys.
        # We can't rely on MariaDB static value tables because not all object
        # types have been migrated to MariaDB (e.g., operations).
        try:
            uuids_seen: set[str] = set()
            for key, _data in etcd.get_all(
                    'attribute/%s' % ot.value, None):
                # Keys look like: /sf/attribute/{type}/{uuid}/{attr}
                # After get_all, key is just the UUID/attr portion.
                parts = key.split('/')
                if parts:
                    uuids_seen.add(parts[0])
        except Exception as e:
            LOG.warning(
                f'Object metadata migration: failed to scan '
                f'etcd for {ot.value}: {e}')
            continue

        for obj_uuid in uuids_seen:
            try:
                # Check if already migrated
                existing = _direct_get_object_metadata(ot, obj_uuid)
                if existing is not None:
                    skipped_count += 1
                    continue

                # Read metadata and last_cluster_operation from etcd
                md = etcd.get(
                    f'attribute/{ot.value}', obj_uuid, 'metadata')
                lco = etcd.get(
                    f'attribute/{ot.value}', obj_uuid,
                    'last_cluster_operation')

                if md is None:
                    # Only metadata is migrated to object_metadata
                    # table. last_cluster_operation is now in
                    # cluster_operation_targets (etcd key is still
                    # cleaned up below).
                    if lco is None:
                        skipped_count += 1
                        continue

                # Write to MariaDB using direct upsert functions
                if md is not None:
                    _direct_set_metadata(ot, obj_uuid, md)

                # Delete from etcd after successful migration
                if md is not None:
                    etcd.delete(
                        f'attribute/{ot.value}', obj_uuid,
                        'metadata')
                # Clean up legacy etcd last_cluster_operation
                # (now stored in cluster_operation_targets table)
                if lco is not None:
                    etcd.delete(
                        f'attribute/{ot.value}', obj_uuid,
                        'last_cluster_operation')

                migrated_count += 1
            except Exception as e:
                LOG.warning(
                    f'Object metadata migration: failed for '
                    f'{ot.value}/{obj_uuid}: {e}')
                error_count += 1

            if (migrated_count + skipped_count) % 100 == 0:
                LOG.info(
                    f'Object metadata migration progress: '
                    f'{migrated_count} migrated, '
                    f'{skipped_count} skipped')

    LOG.info(
        f'Object metadata migration: {migrated_count} '
        f'migrated, {skipped_count} skipped')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_node_metrics(engine: sa.Engine) -> dict[str, Any]:
    """Migrate node metrics from etcd to MariaDB.

    Reads all /sf/metrics/ entries from etcd, upserts them into the
    node_metrics table, then deletes the etcd entries. This is a one-time
    migration that runs automatically on database daemon startup.
    """
    migrated_count = 0
    error_count = 0

    try:
        from shakenfist import etcd as etcd_module
        for k, d in etcd_module.get_all('metrics', None):
            try:
                node_uuid_str = d.get('node_uuid')
                if not node_uuid_str:
                    # Legacy FQDN-keyed entry without node_uuid, skip
                    etcd_module.delete_raw(k)
                    continue

                _direct_upsert_node_metrics(
                    UUID(node_uuid_str),
                    d.get('fqdn', ''),
                    d.get('timestamp', 0.0),
                    d.get('metrics', {})
                )
                etcd_module.delete_raw(k)
                migrated_count += 1
            except Exception as e:
                LOG.warning(
                    f'Failed to migrate node_metrics entry {k}: {e}')
                error_count += 1
    except Exception as e:
        LOG.warning(f'Node metrics migration failed: {e}')
        error_count += 1

    LOG.info(
        f'Node metrics migration: {migrated_count} '
        f'migrated, {error_count} errors')
    return {'migrated_count': migrated_count, 'error_count': error_count}


def _migrate_etcd_cluster_operations(
        engine: sa.Engine) -> dict[str, Any]:
    """Drain residual /sf/{op_type}/{uuid} etcd keys into the
    cluster_operations table.

    One-shot migration used when a pre-phase-6 cluster is upgraded
    and may have leftover cluster-operation header keys. Iterates
    the authoritative OPERATION_NAMES_TO_CLASSES list from
    constants.py and walks each type's etcd prefix. Idempotent on
    re-run: a duplicate uuid returns False from the insert and the
    etcd key is deleted anyway.
    """
    from shakenfist import etcd
    from shakenfist.constants import OPERATION_NAMES_TO_CLASSES

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating cluster operation headers from etcd...')

    for op_type in OPERATION_NAMES_TO_CLASSES:
        for key, data in etcd.get_all(op_type, None):
            op_uuid_str = key.split('/')[-1]
            try:
                op_uuid = UUID(op_uuid_str)
            except ValueError:
                LOG.warning(
                    f'Cluster operation migration: invalid uuid '
                    f'in key {key}; leaving in place')
                error_count += 1
                continue

            if not isinstance(data, dict):
                LOG.warning(
                    f'Cluster operation migration: malformed '
                    f'payload at {key}; leaving in place')
                error_count += 1
                continue

            created_at = data.get('created_at') or time.time()
            try:
                inserted = _direct_create_cluster_operation(
                    op_uuid, op_type, data, created_at)
            except Exception as e:
                LOG.warning(
                    f'Cluster operation migration: insert raised '
                    f'for {key}: {e}')
                error_count += 1
                continue

            etcd.delete_raw(key)
            if inserted:
                migrated_count += 1
            else:
                skipped_count += 1

    LOG.info(
        f'Cluster operation migration: {migrated_count} migrated, '
        f'{skipped_count} skipped, {error_count} errors')
    return {
        'migrated_count': migrated_count,
        'error_count': error_count,
        'skipped_count': skipped_count,
    }


def _migrate_etcd_work_queue(engine: sa.Engine) -> dict[str, Any]:
    """Drain residual /sf/queue/* and /sf/processing/* etcd keys
    into the work_queue table.

    Queue rows are inserted via _direct_work_queue_enqueue with a
    delay computed from the legacy {timestamp}-{random} job name.
    Processing rows (rows that were in flight when the old cluster
    stopped) are re-queued with claimed_at=None so a worker picks
    them up again -- the old worker is gone and we cannot preserve
    attempt count across the etcd->MariaDB boundary.

    If the legacy job name's timestamp prefix cannot be parsed the
    migration falls back to scheduled_at=now (the row becomes
    immediately eligible). Malformed JSON payloads are skipped and
    the etcd key is left in place for the operator to investigate.
    """
    from shakenfist import etcd
    from shakenfist import exceptions

    migrated_count = 0
    error_count = 0
    skipped_count = 0

    LOG.info('Migrating work queue rows from etcd...')

    for source_prefix in ('/sf/queue/', '/sf/processing/'):
        for key, workitem in etcd.get_prefix_raw(source_prefix):
            parts = key.split('/')
            # /sf/{queue|processing}/{queue_name}/{jobname}
            if len(parts) < 5:
                LOG.warning(
                    f'Work queue migration: malformed key {key}; '
                    f'leaving in place')
                error_count += 1
                continue

            queue_name = parts[3]
            jobname = '/'.join(parts[4:])

            if not isinstance(workitem, dict):
                LOG.warning(
                    f'Work queue migration: malformed payload at '
                    f'{key}; leaving in place')
                error_count += 1
                continue

            try:
                legacy_ts = float(jobname.split('-')[0])
            except (ValueError, IndexError):
                LOG.warning(
                    f'Work queue migration: cannot parse timestamp '
                    f'from {jobname}; scheduling immediately')
                legacy_ts = time.time()

            delay = max(0.0, legacy_ts - time.time())
            try:
                _direct_work_queue_enqueue(queue_name, workitem, delay)
            except exceptions.CannotEnqueueWork as e:
                LOG.warning(
                    f'Work queue migration: enqueue failed for '
                    f'{key}: {e}')
                error_count += 1
                continue

            etcd.delete_raw(key)
            migrated_count += 1

    LOG.info(
        f'Work queue migration: {migrated_count} migrated, '
        f'{skipped_count} skipped, {error_count} errors')
    return {
        'migrated_count': migrated_count,
        'error_count': error_count,
        'skipped_count': skipped_count,
    }


def _migrate_etcd_cluster_locks(engine: sa.Engine) -> dict[str, Any]:
    """Drain residual /sflocks/* etcd keys into the cluster_locks table.

    Each etcd key at /sflocks/{type}/{subtype}/{name} holds a JSON dict
    with node, pid, thread, line, operation, id. We insert each into
    cluster_locks with INSERT IGNORE (so re-runs skip already-migrated
    rows) and delete the etcd key on success.

    Stale locks from crashed daemons are acceptable -- the queues daemon
    reaper will clear them on its next startup.
    """
    from shakenfist import etcd

    migrated = 0
    errors = 0

    LOG.info('Migrating cluster locks from etcd...')

    for key, holder in etcd.get_prefix_raw('/sflocks/'):
        lock_key = key.replace('/sflocks/', '', 1)

        if not isinstance(holder, dict):
            LOG.warning(
                f'Lock migration: malformed payload at '
                f'{key}; leaving in place')
            errors += 1
            continue

        acquired = _direct_acquire_cluster_lock(
            lock_key=lock_key,
            holder_json=holder,
            node_uuid=holder.get('node', ''),
            pid=int(holder.get('pid', 0)),
            lock_id=holder.get('id', ''),
            now=time.time(),
        )

        if acquired:
            etcd.delete_raw(key)
            migrated += 1
        else:
            # Row already exists -- somebody migrated it or a live lock
            # is held. Skip without error.
            LOG.debug(
                f'Lock migration: key {lock_key} already '
                f'exists in MariaDB; skipping')

    LOG.info(
        f'Lock migration: {migrated} migrated, {errors} errors')
    return {
        'migrated_count': migrated,
        'error_count': errors,
    }


def _migrate_etcd_cluster_config(engine: sa.Engine) -> dict[str, Any]:
    """Drain /sf/config from etcd into the cluster_config table.

    The etcd key is a single JSON blob. We split it into one row
    per top-level key and delete the etcd key.
    """
    from shakenfist import etcd

    migrated = 0
    errors = 0

    LOG.info('Migrating cluster config from etcd...')

    raw = etcd.get_raw('/sf/config')
    if raw is None or raw == {}:
        LOG.info('No /sf/config in etcd; nothing to migrate')
        return {'migrated_count': 0, 'error_count': 0}

    if not isinstance(raw, dict):
        LOG.warning(
            'Cluster config migration: /sf/config is not a '
            'dict; leaving in place')
        return {'migrated_count': 0, 'error_count': 1}

    for key_name, value in raw.items():
        _direct_set_cluster_config(key_name, value)
        migrated += 1

    etcd.delete_raw('/sf/config')

    LOG.info(
        f'Cluster config migration: {migrated} keys migrated, '
        f'{errors} errors')
    return {
        'migrated_count': migrated,
        'error_count': errors,
    }


def _migrate_etcd_event_dlq(engine: sa.Engine) -> dict[str, Any]:
    """Drain residual /sf/event/* etcd keys into the event_dlq table.

    Each etcd key at /sf/event/{objtype}/{objuuid}/{timestamp} holds
    a JSON dict with the event payload. We insert each into event_dlq
    and delete the etcd key.
    """
    from shakenfist import etcd

    migrated = 0
    errors = 0

    LOG.info('Migrating event DLQ from etcd...')

    for key, event in etcd.get_prefix_raw('/sf/event/'):
        if not isinstance(event, dict):
            LOG.warning(
                f'Event DLQ migration: malformed payload '
                f'at {key}; leaving in place')
            errors += 1
            continue

        try:
            parts = key.split('/')
            # /sf/event/{objtype}/{objuuid}/{ts}
            objtype = parts[3]
            objuuid = parts[4]
            ts = float(parts[5])
        except (IndexError, ValueError) as e:
            LOG.warning(
                f'Event DLQ migration: bad key {key}: {e}')
            errors += 1
            continue

        _direct_enqueue_event_dlq(
            object_type=objtype,
            object_uuid=objuuid,
            event_timestamp=ts,
            event_json=event,
        )
        etcd.delete_raw(key)
        migrated += 1

    LOG.info(
        f'Event DLQ migration: {migrated} migrated, '
        f'{errors} errors')
    return {
        'migrated_count': migrated,
        'error_count': errors,
    }


# Populate DATA_MIGRATIONS now that all migration functions are defined.
# All data migrations happen at version 2 (after table creation at version 1).
DATA_MIGRATIONS.update({
    'object_states': {
        2: _migrate_etcd_object_states,
    },
    'ipam_reservations': {
        2: _migrate_etcd_ipam_reservations,
    },
    'uploads': {
        2: _migrate_etcd_uploads,
    },
    'dnsmasq': {
        2: _migrate_etcd_dnsmasq,
    },
    'blobs': {
        2: _migrate_etcd_blobs,
    },
    'object_references': {
        2: _migrate_etcd_object_references,
    },
    'blob_hashes': {
        2: _migrate_etcd_blob_hashes,
    },
    'blob_transfers': {
        2: _cleanup_etcd_blob_transfers,
    },
    'blob_attributes': {
        2: _migrate_etcd_blob_attributes,
    },
    'nodes': {
        2: _migrate_etcd_nodes,
    },
    'node_attributes': {
        2: _migrate_etcd_node_attributes,
    },
    'namespaces': {
        2: _migrate_etcd_namespaces,
    },
    'namespace_attributes': {
        2: _migrate_etcd_namespace_attributes,
    },
    'artifacts': {
        2: _migrate_etcd_artifacts,
    },
    'artifact_attributes': {
        2: _migrate_etcd_artifact_attributes,
    },
    'artifact_indexes': {
        2: _migrate_etcd_artifact_indexes,
    },
    'network_interfaces': {
        2: _migrate_etcd_network_interfaces,
    },
    'network_interface_attributes': {
        2: _migrate_etcd_network_interface_attributes,
    },
    'ipams': {
        2: _migrate_etcd_ipams,
    },
    'networks': {
        2: _migrate_etcd_networks,
    },
    'network_attributes': {
        2: _migrate_etcd_network_attributes,
    },
    'agent_operations': {
        2: _migrate_etcd_agent_operations,
    },
    'agent_operation_attributes': {
        2: _migrate_etcd_agent_operation_attributes,
    },
    'instances': {
        2: _migrate_etcd_instances,
    },
    'instance_attributes': {
        2: _migrate_etcd_instance_attributes,
    },
    'object_metadata': {
        2: _migrate_etcd_object_metadata,
    },
    'node_metrics': {
        2: _migrate_etcd_node_metrics,
    },
    'cluster_operations': {
        2: _migrate_etcd_cluster_operations,
    },
    'work_queue': {
        2: _migrate_etcd_work_queue,
    },
    'cluster_locks': {
        2: _migrate_etcd_cluster_locks,
    },
    'cluster_config': {
        2: _migrate_etcd_cluster_config,
    },
    'event_dlq': {
        2: _migrate_etcd_event_dlq,
    },
})


def _direct_get_state(object_type: ObjectType, object_uuid: str) -> Optional[State]:
    """Read state for an object directly from MariaDB.

    This is the direct access version used by the database daemon.
    """
    engine = _get_engine()
    table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                sa.and_(
                    table.c.object_type == object_type,
                    table.c.object_uuid == object_uuid
                )
            )
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return State(
                value=result.state_value,
                update_time=result.update_time,
                message=result.message
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB read failed for {object_type}/{object_uuid}: {e}')
        return None


def _direct_set_state(object_type: ObjectType, object_uuid: str, state: State) -> bool:
    """Write state for an object directly to MariaDB.

    This is the direct access version used by the database daemon.
    Uses INSERT ... ON DUPLICATE KEY UPDATE for atomic upsert.

    For NetworkInterface objects, also nulls out the
    ``network_interfaces.active`` flag when the new state is
    ``deleted``. The flag is part of the composite UNIQUE constraint
    on macaddr — clearing it lets the MAC be reused immediately
    while the soft-deleted row remains in place for audit.
    """
    engine = _get_engine()
    table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            # Use MySQL's INSERT ... ON DUPLICATE KEY UPDATE for upsert
            stmt = sa.dialects.mysql.insert(table).values(
                object_uuid=object_uuid,
                object_type=object_type,
                state_value=state.value,
                update_time=state.update_time,
                message=state.message
            )
            stmt = stmt.on_duplicate_key_update(
                state_value=state.value,
                update_time=state.update_time,
                message=state.message
            )
            conn.execute(stmt)

            if (object_type == ObjectType.INTERFACE
                    and state.value == 'deleted'):
                ni_table = _get_network_interfaces_table()
                conn.execute(sa.update(ni_table).where(
                    ni_table.c.uuid == UUID(object_uuid)
                ).values(active=None))

            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB write failed for {object_type}/{object_uuid}: {e}')
        return False


def _direct_delete_state(object_type: ObjectType, object_uuid: str) -> bool:
    """Delete state for an object directly from MariaDB.

    This is the direct access version used by the database daemon.
    """
    engine = _get_engine()
    table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.object_type == object_type,
                    table.c.object_uuid == object_uuid
                )
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for {object_type}/{object_uuid}: {e}')
        return False


def _direct_get_objects_by_state(object_type: ObjectType,
                                 state_values: list[str]
                                 ) -> Optional[list[str]]:
    """Get all object UUIDs of a given type in specified states.

    An empty ``state_values`` list means "no state filter" — return every
    object of ``object_type`` regardless of state. This matches the
    pre-phase-5 ``Nodes([])`` semantics where no prefilter returned every
    node, including DELETED.

    This is the direct access version used by the database daemon.
    Returns None on error (distinct from [] for no matches).
    """
    engine = _get_engine()
    table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            where = [table.c.object_type == object_type]
            if state_values:
                where.append(table.c.state_value.in_(state_values))
            stmt = sa.select(table.c.object_uuid).where(sa.and_(*where))
            result = conn.execute(stmt).fetchall()
            return [row.object_uuid for row in result]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for {object_type} in {state_values}: {e}')
        return None


# =============================================================================
# gRPC Client Functions
# These call the database microservice for state operations.
# =============================================================================

def _grpc_get_state(object_type: ObjectType, object_uuid: str) -> Optional[State]:
    """Read state for an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetObjectStateRequest(
            object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType, object_type.proto_id),
            object_uuid=object_uuid
        )
        reply = _grpc_call(stub.GetObjectState, request)
        if not reply.found:
            return None
        return State(
            value=reply.state_value if reply.state_value else None,
            update_time=reply.update_time,
            message=reply.message if reply.message else None
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetObjectState failed for {object_type}/{object_uuid}: {e}')
        return None


def _grpc_set_state(object_type: ObjectType, object_uuid: str, state: State) -> bool:
    """Write state for an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.SetObjectStateRequest(
            object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType, object_type.proto_id),
            object_uuid=object_uuid,
            state_value=state.value or '',
            update_time=state.update_time,
            message=state.message or ''
        )
        reply = _grpc_call(stub.SetObjectState, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC SetObjectState failed for {object_type}/{object_uuid}: {e}')
        return False


def _grpc_delete_state(object_type: ObjectType, object_uuid: str) -> bool:
    """Delete state for an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteObjectStateRequest(
            object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType, object_type.proto_id),
            object_uuid=object_uuid
        )
        reply = _grpc_call(stub.DeleteObjectState, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteObjectState failed for {object_type}/{object_uuid}: {e}')
        return False


def _grpc_get_objects_by_state(object_type: ObjectType,
                               state_values: list[str]
                               ) -> Optional[list[str]]:
    """Get all object UUIDs of a given type in specified states via gRPC.

    Returns None on error (distinct from [] for no matches).
    """
    try:
        stub = _get_database_stub()
        request = database_pb2.GetObjectsByStateRequest(
            object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType, object_type.proto_id),
            state_values=state_values
        )
        reply = _grpc_call(stub.GetObjectsByState, request)
        return list(reply.object_uuids)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetObjectsByState failed for {object_type}: {e}')
        return None


# Note: ObjectType and ReservationType now have proto_id attributes and
# from_proto_id() methods for efficient gRPC enum conversion.


# =============================================================================
# IPAM gRPC Client Functions
# These call the database microservice for IPAM operations.
# =============================================================================

def _grpc_reserve_address(reservation: IPAMReservation) -> bool:
    """Atomically reserve an IP address via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.ReserveAddressRequest(
            reservation=database_pb2.IPAMReservationData(
                ipam_uuid=str(reservation.ipam_uuid),
                address=str(reservation.address),
                reservation_type=cast(
                    shakenfist_enums_pb2.ReservationType.ValueType,
                    reservation.reservation_type.proto_id),
                user_type=cast(
                    shakenfist_enums_pb2.ObjectType.ValueType,
                    reservation.user_type.proto_id if reservation.user_type else 0
                ),
                user_uuid=(str(reservation.user_uuid)
                           if reservation.user_uuid else ''),
                reserved_at=reservation.reserved_at,
                comment=reservation.comment or ''
            )
        )
        reply = _grpc_call(stub.ReserveAddress, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC ReserveAddress failed for {reservation.ipam_uuid}/'
            f'{reservation.address}: {e}')
        return False


def _grpc_release_address(ipam_uuid: str, address: str,
                          halo_reservation: IPAMReservation) -> bool:
    """Release an IP address via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.ReleaseAddressRequest(
            ipam_uuid=ipam_uuid,
            address=address,
            halo_reservation=database_pb2.IPAMReservationData(
                ipam_uuid=str(halo_reservation.ipam_uuid),
                address=str(halo_reservation.address),
                reservation_type=cast(
                    shakenfist_enums_pb2.ReservationType.ValueType,
                    halo_reservation.reservation_type.proto_id),
                user_type=cast(
                    shakenfist_enums_pb2.ObjectType.ValueType,
                    halo_reservation.user_type.proto_id
                    if halo_reservation.user_type else 0
                ),
                user_uuid=(str(halo_reservation.user_uuid)
                           if halo_reservation.user_uuid else ''),
                reserved_at=halo_reservation.reserved_at,
                comment=halo_reservation.comment or ''
            )
        )
        reply = _grpc_call(stub.ReleaseAddress, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC ReleaseAddress failed for {ipam_uuid}/{address}: {e}')
        return False


def _grpc_get_reservation(ipam_uuid: str,
                          address: str) -> Optional[IPAMReservation]:
    """Get a single reservation via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetReservationRequest(
            ipam_uuid=ipam_uuid,
            address=address
        )
        reply = _grpc_call(stub.GetReservation, request)
        if not reply.found:
            return None
        res_type = ReservationType.from_proto_id(
            reply.reservation.reservation_type)
        if res_type is None:
            res_type = ReservationType.UNKNOWN
        user_type = ObjectType.from_proto_id(reply.reservation.user_type)
        if user_type is None:
            user_type = ObjectType.UNKNOWN
        return IPAMReservation(
            ipam_uuid=reply.reservation.ipam_uuid,
            address=IPv4Address(reply.reservation.address),
            reservation_type=res_type,
            user_type=user_type,
            user_uuid=reply.reservation.user_uuid or None,
            reserved_at=reply.reservation.reserved_at,
            comment=reply.reservation.comment or None
        )
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetReservation failed for {ipam_uuid}/{address}: {e}')
        return None


def _grpc_get_reservations_for_ipam(ipam_uuid: str) -> list[IPAMReservation]:
    """Get all reservations for an IPAM via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetReservationsForIPAMRequest(
            ipam_uuid=ipam_uuid)
        reply = _grpc_call(stub.GetReservationsForIPAM, request)
        result = []
        for res in reply.reservations:
            res_type = ReservationType.from_proto_id(res.reservation_type)
            if res_type is None:
                res_type = ReservationType.UNKNOWN
            user_type = ObjectType.from_proto_id(res.user_type)
            if user_type is None:
                user_type = ObjectType.UNKNOWN
            result.append(IPAMReservation(
                ipam_uuid=res.ipam_uuid,
                address=IPv4Address(res.address),
                reservation_type=res_type,
                user_type=user_type,
                user_uuid=res.user_uuid or None,
                reserved_at=res.reserved_at,
                comment=res.comment or None
            ))
        return result
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetReservationsForIPAM failed for {ipam_uuid}: {e}')
        return []


def _grpc_delete_reservation(ipam_uuid: str, address: str) -> bool:
    """Delete a single reservation via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteReservationRequest(
            ipam_uuid=ipam_uuid,
            address=address
        )
        reply = _grpc_call(stub.DeleteReservation, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteReservation failed for {ipam_uuid}/{address}: {e}')
        return False


def _grpc_delete_reservations_for_ipam(ipam_uuid: str) -> int:
    """Delete all reservations for an IPAM via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteReservationsForIPAMRequest(
            ipam_uuid=ipam_uuid)
        reply = _grpc_call(stub.DeleteReservationsForIPAM, request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteReservationsForIPAM failed for {ipam_uuid}: {e}')
        return 0


def _grpc_release_haloed_addresses(ipam_uuid: str, older_than: float) -> int:
    """Release expired deletion-halo addresses via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.ReleaseHaloedAddressesRequest(
            ipam_uuid=ipam_uuid,
            older_than=older_than
        )
        reply = _grpc_call(stub.ReleaseHaloedAddresses, request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC ReleaseHaloedAddresses failed for {ipam_uuid}: {e}')
        return 0


def _grpc_get_addresses_in_use(ipam_uuid: str) -> set[str]:
    """Get all addresses in use for an IPAM via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAddressesInUseRequest(
            ipam_uuid=ipam_uuid)
        reply = _grpc_call(stub.GetAddressesInUse, request)
        return set(reply.addresses)
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetAddressesInUse failed for {ipam_uuid}: {e}')
        return set()


# =============================================================================
# Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def get_state(object_type: ObjectType, object_uuid: str) -> Optional[State]:
    """Read state for an object.

    Args:
        object_type: The type of object.
        object_uuid: The UUID of the object.

    Returns:
        A State object, or None if no state exists for this object.
    """
    if _use_database_service():
        return _grpc_get_state(object_type, object_uuid)
    return _direct_get_state(object_type, object_uuid)


def set_state(object_type: ObjectType, object_uuid: str, state: State) -> bool:
    """Write state for an object.

    Args:
        object_type: The type of object.
        object_uuid: The UUID of the object.
        state: The State object to store.

    Returns:
        True if the write succeeded, False otherwise.
    """
    if _use_database_service():
        return _grpc_set_state(object_type, object_uuid, state)
    return _direct_set_state(object_type, object_uuid, state)


def delete_state(object_type: ObjectType, object_uuid: str) -> bool:
    """Delete state for an object.

    Args:
        object_type: The type of object.
        object_uuid: The UUID of the object.

    Returns:
        True if the delete succeeded (or row didn't exist), False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_state(object_type, object_uuid)
    return _direct_delete_state(object_type, object_uuid)


def get_objects_by_state(object_type: ObjectType,
                         state_values: list[str]
                         ) -> Optional[list[str]]:
    """Get all object UUIDs of a given type in specified states.

    This is the primary use case for MariaDB state storage - efficient
    queries across object states without scanning all objects in etcd.

    Args:
        object_type: The type of object.
        state_values: List of state values to match.

    Returns:
        List of object UUIDs matching the criteria, or None if the
        query failed (distinct from [] which means no matches).
    """
    if _use_database_service():
        return _grpc_get_objects_by_state(object_type, state_values)
    return _direct_get_objects_by_state(object_type, state_values)


def get_all_states_for_type(object_type: ObjectType) -> list[tuple[str, State]]:
    """Get all states for a given object type.

    Useful for migrations and debugging. This function always uses direct
    access as it's only called by admin tools and the database daemon.

    Args:
        object_type: The type of object.

    Returns:
        List of tuples (object_uuid, State).
    """
    engine = _get_engine()
    table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.object_type == object_type)
            result = conn.execute(stmt).fetchall()
            return [
                (
                    row.object_uuid,
                    State(
                        value=row.state_value,
                        update_time=row.update_time,
                        message=row.message
                    )
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for type {object_type}: {e}')
        return []


# =============================================================================
# Object Metadata Direct Access Functions
# These store user-defined metadata for all objects.
# =============================================================================

def _direct_get_object_metadata(
    object_type: ObjectType,
    object_uuid: str
) -> Optional[ObjectMetadataData]:
    """Read object metadata directly from MariaDB."""
    engine = _get_engine()
    table = _get_object_metadata_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                sa.and_(
                    table.c.object_type == object_type,
                    table.c.object_uuid == object_uuid
                )
            )
            result = conn.execute(stmt).fetchone()
            if result is None:
                return None

            metadata = json.loads(result.metadata_json) if result.metadata_json else None

            return ObjectMetadataData(
                object_type=object_type.value,
                object_uuid=object_uuid,
                metadata=metadata,
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB read failed for object_metadata {object_type}/{object_uuid}: {e}')
        return None


def _direct_set_metadata(
    object_type: ObjectType,
    object_uuid: str,
    metadata_dict: Optional[dict[str, Any]]
) -> bool:
    """Write metadata for an object directly to MariaDB.

    Uses INSERT ... ON DUPLICATE KEY UPDATE for atomic upsert.
    Only updates the metadata_json column.
    """
    engine = _get_engine()
    table = _get_object_metadata_table()

    try:
        metadata_json = json.dumps(metadata_dict) if metadata_dict is not None else None
        with engine.connect() as conn:
            stmt = sa.dialects.mysql.insert(table).values(
                object_uuid=object_uuid,
                object_type=object_type,
                metadata_json=metadata_json,
            )
            stmt = stmt.on_duplicate_key_update(
                metadata_json=metadata_json
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB write failed for object_metadata '
            f'{object_type}/{object_uuid}: {e}')
        return False


def _direct_delete_object_metadata(
    object_type: ObjectType,
    object_uuid: str
) -> bool:
    """Delete object metadata directly from MariaDB."""
    engine = _get_engine()
    table = _get_object_metadata_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.object_type == object_type,
                    table.c.object_uuid == object_uuid
                )
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for object_metadata '
            f'{object_type}/{object_uuid}: {e}')
        return False


# =============================================================================
# Object Metadata gRPC Client Functions
# These call the database microservice for object metadata operations.
# =============================================================================

def _grpc_get_object_metadata(
    object_type: ObjectType,
    object_uuid: str
) -> Optional[ObjectMetadataData]:
    """Read object metadata via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetObjectMetadataRequest(
            object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType, object_type.proto_id),
            object_uuid=object_uuid
        )
        reply = _grpc_call(stub.GetObjectMetadata, request)
        if not reply.found:
            return None

        metadata = json.loads(reply.metadata_json) if reply.metadata_json else None

        return ObjectMetadataData(
            object_type=object_type.value,
            object_uuid=object_uuid,
            metadata=metadata,
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetObjectMetadata failed for '
            f'{object_type}/{object_uuid}: {e}')
        return None


def _grpc_set_metadata(
    object_type: ObjectType,
    object_uuid: str,
    metadata_dict: Optional[dict[str, Any]]
) -> bool:
    """Write metadata for an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.SetMetadataRequest(
            object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType, object_type.proto_id),
            object_uuid=object_uuid,
            metadata_json=json.dumps(metadata_dict) if metadata_dict is not None else ''
        )
        reply = _grpc_call(stub.SetMetadata, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC SetMetadata failed for '
            f'{object_type}/{object_uuid}: {e}')
        return False


def _grpc_delete_object_metadata(
    object_type: ObjectType,
    object_uuid: str
) -> bool:
    """Delete object metadata via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteObjectMetadataRequest(
            object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType, object_type.proto_id),
            object_uuid=object_uuid
        )
        reply = _grpc_call(stub.DeleteObjectMetadata, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteObjectMetadata failed for '
            f'{object_type}/{object_uuid}: {e}')
        return False


# =============================================================================
# Object Metadata Public API
# These route to either direct access or gRPC based on configuration.
# =============================================================================

def get_object_metadata(
    object_type: ObjectType,
    object_uuid: str
) -> Optional[ObjectMetadataData]:
    """Read metadata for an object.

    Args:
        object_type: The type of object.
        object_uuid: The UUID of the object.

    Returns:
        An ObjectMetadataData object, or None if no metadata exists.
    """
    if _use_database_service():
        return _grpc_get_object_metadata(object_type, object_uuid)
    return _direct_get_object_metadata(object_type, object_uuid)


def set_metadata(
    object_type: ObjectType,
    object_uuid: str,
    metadata_dict: Optional[dict[str, Any]]
) -> bool:
    """Write metadata for an object.

    Args:
        object_type: The type of object.
        object_uuid: The UUID of the object.
        metadata_dict: The metadata dict to store, or None.

    Returns:
        True if the write succeeded, False otherwise.
    """
    if _use_database_service():
        return _grpc_set_metadata(object_type, object_uuid, metadata_dict)
    return _direct_set_metadata(object_type, object_uuid, metadata_dict)


def delete_object_metadata(
    object_type: ObjectType,
    object_uuid: str
) -> bool:
    """Delete metadata for an object.

    Args:
        object_type: The type of object.
        object_uuid: The UUID of the object.

    Returns:
        True if the delete succeeded (or row didn't exist), False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_object_metadata(object_type, object_uuid)
    return _direct_delete_object_metadata(object_type, object_uuid)


# =============================================================================
# Cluster Operation Target Functions
# Track which cluster operations target which objects, with sequence ordering.
# =============================================================================

def _direct_create_cluster_operation_target(
    operation_uuid: str,
    operation_type: str,
    target_object_type: ObjectType,
    target_uuid: str,
    created_at: float
) -> bool:
    """Insert a cluster operation target row directly into MariaDB.

    The sequence_number is assigned automatically by AUTO_INCREMENT.
    """
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                operation_uuid=operation_uuid,
                operation_type=operation_type,
                target_object_type=target_object_type,
                target_uuid=target_uuid,
                created_at=created_at
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError as e:
        # The only IntegrityError we want to treat as success is the
        # composite UNIQUE on (operation_uuid, target_object_type,
        # target_uuid) tripping -- the same op-target pair already
        # exists, which is the idempotency case. Any other integrity
        # violation (NOT NULL, type-check, foreign-key) is a real
        # bug and must surface.
        #
        # MariaDB names the constraint in the error text. SQLite does
        # not, so we also accept its fixed "UNIQUE constraint failed"
        # phrase; the table only carries this one composite UNIQUE so
        # the fallback cannot misclassify a different uniqueness rule.
        msg = str(e).lower()
        if 'uq_cot_op_target' in msg or 'unique constraint failed' in msg:
            return True
        LOG.warning(
            f'Non-uniqueness IntegrityError writing '
            f'cluster_operation_targets row for op {operation_uuid}: {e}')
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB write failed for cluster_operation_targets '
            f'{operation_uuid}: {e}')
        return False


def _direct_get_cluster_operation_target(
    operation_uuid: str
) -> Optional[ClusterOperationTargetData]:
    """Read a single cluster operation target by operation_uuid."""
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.operation_uuid == operation_uuid
            )
            result = conn.execute(stmt).fetchone()
            if result is None:
                return None

            return ClusterOperationTargetData(
                operation_uuid=result.operation_uuid,
                operation_type=result.operation_type,
                target_object_type=result.target_object_type.value,
                target_uuid=result.target_uuid,
                sequence_number=result.sequence_number,
                created_at=result.created_at
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB read failed for cluster_operation_targets '
            f'{operation_uuid}: {e}')
        return None


def _direct_get_cluster_operation_targets_for_object(
    target_object_type: ObjectType,
    target_uuid: str
) -> list[ClusterOperationTargetData]:
    """Get all cluster operation targets for an object, ordered by sequence."""
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                sa.and_(
                    table.c.target_object_type == target_object_type,
                    table.c.target_uuid == target_uuid
                )
            ).order_by(table.c.sequence_number)
            results = conn.execute(stmt).fetchall()
            return [
                ClusterOperationTargetData(
                    operation_uuid=r.operation_uuid,
                    operation_type=r.operation_type,
                    target_object_type=r.target_object_type.value,
                    target_uuid=r.target_uuid,
                    sequence_number=r.sequence_number,
                    created_at=r.created_at
                )
                for r in results
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB read failed for cluster_operation_targets '
            f'{target_object_type}/{target_uuid}: {e}')
        return []


def _direct_get_latest_cluster_operation_target(
    target_object_type: ObjectType,
    target_uuid: str
) -> Optional[ClusterOperationTargetData]:
    """Get the most recent cluster operation target for an object."""
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                sa.and_(
                    table.c.target_object_type == target_object_type,
                    table.c.target_uuid == target_uuid
                )
            ).order_by(table.c.sequence_number.desc()).limit(1)
            result = conn.execute(stmt).fetchone()
            if result is None:
                return None

            return ClusterOperationTargetData(
                operation_uuid=result.operation_uuid,
                operation_type=result.operation_type,
                target_object_type=result.target_object_type.value,
                target_uuid=result.target_uuid,
                sequence_number=result.sequence_number,
                created_at=result.created_at
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB read failed for cluster_operation_targets '
            f'{target_object_type}/{target_uuid}: {e}')
        return None


# An operation is "in flight" if and only if its row in object_states has
# one of these state values. Anything else (complete, abort, error,
# deleted, ...) is terminal. Matches
# _direct_delete_stale_cluster_operation_targets.
_ACTIVE_OPERATION_STATES = ('queued', 'preflight', 'executing')


def _direct_has_pending_cluster_operation_target(
    target_object_type: ObjectType,
    target_uuid: str
) -> bool:
    """True if any in-flight cluster operation targets this object."""
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()
    states_table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            inner = sa.select(sa.literal(1)).select_from(
                table.join(
                    states_table,
                    table.c.operation_uuid == states_table.c.object_uuid
                )
            ).where(
                sa.and_(
                    table.c.target_object_type == target_object_type,
                    table.c.target_uuid == target_uuid,
                    states_table.c.state_value.in_(_ACTIVE_OPERATION_STATES)
                )
            )
            stmt = sa.select(inner.exists())
            result = conn.execute(stmt).scalar()
            return bool(result)
    except OperationalError as e:
        LOG.warning(
            f'MariaDB read failed for has_pending_cluster_operation '
            f'{target_object_type}/{target_uuid}: {e}')
        # Fail closed: if we cannot prove no op is in flight, treat that
        # as "in flight" so callers defer rather than racing.
        return True


# Terminal states for cluster operations -- mirror the set used by the
# operation state machine (shakenfist/operations/baseoperation.py). An op
# in any of these states is no longer executing and is safe to consider
# for cooldown / circuit-breaker queries.
_TERMINAL_OPERATION_STATES = ('complete', 'abort', 'deleted', 'error')


def _direct_get_recent_terminal_op_states_for_target(
    target_object_type: ObjectType,
    target_uuid: str,
    limit: int,
    op_type: Optional[str] = None
) -> list[tuple[str, str, float]]:
    """Return up to ``limit`` most recent terminal op states for an object.

    Joins ``cluster_operation_targets`` against ``object_states`` and
    filters to terminal cluster operation states (complete, abort,
    deleted, error). Results are ordered newest first by
    ``object_states.update_time``. If ``op_type`` is provided, results
    are additionally narrowed to that operation type (e.g. ``'net_op'``).

    Returns a list of ``(op_uuid, state_value, update_time)`` tuples.
    """
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()
    states_table = _get_object_states_table()

    conditions = [
        table.c.target_object_type == target_object_type,
        table.c.target_uuid == target_uuid,
        states_table.c.state_value.in_(_TERMINAL_OPERATION_STATES),
    ]
    if op_type is not None:
        conditions.append(table.c.operation_type == op_type)

    try:
        with engine.connect() as conn:
            stmt = sa.select(
                table.c.operation_uuid,
                states_table.c.state_value,
                states_table.c.update_time,
            ).select_from(
                table.join(
                    states_table,
                    table.c.operation_uuid == states_table.c.object_uuid
                )
            ).where(
                sa.and_(*conditions)
            ).order_by(
                states_table.c.update_time.desc()
            ).limit(limit)
            results = conn.execute(stmt).fetchall()
            return [
                (r.operation_uuid, r.state_value, float(r.update_time))
                for r in results
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB read failed for get_recent_terminal_op_states '
            f'{target_object_type}/{target_uuid}: {e}')
        return []


def _direct_delete_cluster_operation_target(
    operation_uuid: str
) -> bool:
    """Delete a single cluster operation target row."""
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.operation_uuid == operation_uuid
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for cluster_operation_targets '
            f'{operation_uuid}: {e}')
        return False


def _direct_delete_cluster_operation_targets_for_object(
    target_object_type: ObjectType,
    target_uuid: str
) -> bool:
    """Delete all cluster operation target rows for an object."""
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.target_object_type == target_object_type,
                    table.c.target_uuid == target_uuid
                )
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for cluster_operation_targets '
            f'{target_object_type}/{target_uuid}: {e}')
        return False


def _direct_delete_stale_cluster_operation_targets(
    older_than: float
) -> int:
    """Prune cluster_operation_targets rows for completed operations.

    Deletes rows where:
      * created_at is older than the supplied unix timestamp, AND
      * the operation referenced by operation_uuid is not currently in
        an active state (queued/preflight/executing) in object_states.

    Operations that have been hard-deleted leave no row in object_states
    and so are also pruned by this query.

    Args:
        older_than: Unix timestamp. Targets created before this are
            eligible for pruning.

    Returns:
        Number of rows deleted.
    """
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()
    states_table = _get_object_states_table()

    # States that mean an operation is still in flight. Anything else
    # (complete, abort, error, deleted, ...) is considered terminal.
    active_states = ['queued', 'preflight', 'executing']

    try:
        with engine.connect() as conn:
            active_subq = sa.select(states_table.c.object_uuid).where(
                states_table.c.state_value.in_(active_states)
            )
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.created_at < older_than,
                    ~table.c.operation_uuid.in_(active_subq)
                )
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete_stale_cluster_operation_targets '
            f'failed: {e}')
        return 0


# gRPC client functions for cluster operation targets

def _grpc_create_cluster_operation_target(
    operation_uuid: str,
    operation_type: str,
    target_object_type: ObjectType,
    target_uuid: str,
    created_at: float
) -> bool:
    """Insert a cluster operation target via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateClusterOperationTargetRequest(
            operation_uuid=operation_uuid,
            operation_type=operation_type,
            target_object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_object_type.proto_id),
            target_uuid=target_uuid,
            created_at=created_at
        )
        reply = _grpc_call(stub.CreateClusterOperationTarget, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateClusterOperationTarget failed for '
            f'{operation_uuid}: {e}')
        return False


def _target_from_proto(
    t: 'database_pb2.ClusterOperationTargetProto'
) -> ClusterOperationTargetData:
    """Convert a proto ClusterOperationTargetProto to model."""
    ot = ObjectType.from_proto_id(t.target_object_type)
    return ClusterOperationTargetData(
        operation_uuid=t.operation_uuid,
        operation_type=t.operation_type,
        target_object_type=ot.value if ot else 'unknown',
        target_uuid=t.target_uuid,
        sequence_number=t.sequence_number if t.sequence_number else None,
        created_at=t.created_at
    )


def _grpc_get_cluster_operation_target(
    operation_uuid: str
) -> Optional[ClusterOperationTargetData]:
    """Read a single cluster operation target via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetClusterOperationTargetRequest(
            operation_uuid=operation_uuid
        )
        reply = _grpc_call(stub.GetClusterOperationTarget, request)
        if not reply.found:
            return None
        return _target_from_proto(reply.target)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetClusterOperationTarget failed for '
            f'{operation_uuid}: {e}')
        return None


def _grpc_get_cluster_operation_targets_for_object(
    target_object_type: ObjectType,
    target_uuid: str
) -> list[ClusterOperationTargetData]:
    """Get all cluster operation targets for an object via gRPC."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetClusterOperationTargetsForObjectRequest(
            target_object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_object_type.proto_id),
            target_uuid=target_uuid
        )
        reply = _grpc_call(stub.GetClusterOperationTargetsForObject, request)
        return [_target_from_proto(t) for t in reply.targets]
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetClusterOperationTargetsForObject failed for '
            f'{target_object_type}/{target_uuid}: {e}')
        return []


def _grpc_get_latest_cluster_operation_target(
    target_object_type: ObjectType,
    target_uuid: str
) -> Optional[ClusterOperationTargetData]:
    """Get the most recent cluster operation target via gRPC."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetLatestClusterOperationTargetRequest(
            target_object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_object_type.proto_id),
            target_uuid=target_uuid
        )
        reply = _grpc_call(stub.GetLatestClusterOperationTarget, request)
        if not reply.found:
            return None
        return _target_from_proto(reply.target)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetLatestClusterOperationTarget failed for '
            f'{target_object_type}/{target_uuid}: {e}')
        return None


def _grpc_has_pending_cluster_operation_target(
    target_object_type: ObjectType,
    target_uuid: str
) -> bool:
    """True if any in-flight cluster operation targets this object (gRPC)."""
    try:
        stub = _get_database_stub()
        request = database_pb2.HasPendingClusterOperationTargetRequest(
            target_object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_object_type.proto_id),
            target_uuid=target_uuid
        )
        reply = _grpc_call(
            stub.HasPendingClusterOperationTarget, request)
        return bool(reply.pending)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC HasPendingClusterOperationTarget failed for '
            f'{target_object_type}/{target_uuid}: {e}')
        return True


def _grpc_get_recent_terminal_op_states_for_target(
    target_object_type: ObjectType,
    target_uuid: str,
    limit: int,
    op_type: Optional[str] = None
) -> list[tuple[str, str, float]]:
    """gRPC variant of get_recent_terminal_op_states_for_target."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetRecentTerminalOpStatesForTargetRequest(
            target_object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_object_type.proto_id),
            target_uuid=target_uuid,
            limit=limit,
            op_type=op_type if op_type is not None else '',
        )
        reply = _grpc_call(
            stub.GetRecentTerminalOpStatesForTarget, request)
        return [
            (entry.op_uuid, entry.state_value, float(entry.update_time))
            for entry in reply.entries
        ]
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetRecentTerminalOpStatesForTarget failed for '
            f'{target_object_type}/{target_uuid}: {e}')
        return []


def _grpc_delete_cluster_operation_target(
    operation_uuid: str
) -> bool:
    """Delete a single cluster operation target via gRPC."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteClusterOperationTargetRequest(
            operation_uuid=operation_uuid
        )
        reply = _grpc_call(stub.DeleteClusterOperationTarget, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteClusterOperationTarget failed for '
            f'{operation_uuid}: {e}')
        return False


def _grpc_delete_cluster_operation_targets_for_object(
    target_object_type: ObjectType,
    target_uuid: str
) -> bool:
    """Delete all cluster operation targets for an object via gRPC."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteClusterOperationTargetsForObjectRequest(
            target_object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_object_type.proto_id),
            target_uuid=target_uuid
        )
        reply = _grpc_call(stub.DeleteClusterOperationTargetsForObject, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteClusterOperationTargetsForObject failed for '
            f'{target_object_type}/{target_uuid}: {e}')
        return False


def _grpc_delete_stale_cluster_operation_targets(max_age: float) -> int:
    """Prune stale cluster_operation_targets via the database microservice."""
    try:
        stub = _get_database_stub()
        older_than = time.time() - max_age
        request = database_pb2.DeleteStaleClusterOperationTargetsRequest(
            older_than=older_than
        )
        reply = _grpc_call(
            stub.DeleteStaleClusterOperationTargets, request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteStaleClusterOperationTargets failed: {e}')
        return 0


# Public API functions for cluster operation targets

def create_cluster_operation_target(
    operation_uuid: str,
    operation_type: str,
    target_object_type: ObjectType,
    target_uuid: str,
    created_at: float
) -> bool:
    """Record that a cluster operation targets an object.

    Args:
        operation_uuid: UUID of the cluster operation.
        operation_type: The operation type string.
        target_object_type: The ObjectType of the target object.
        target_uuid: UUID of the target object.
        created_at: Unix timestamp when the operation was enqueued.

    Returns:
        True if the row was created, False on duplicate or error.
    """
    if _use_database_service():
        return _grpc_create_cluster_operation_target(
            operation_uuid, operation_type, target_object_type,
            target_uuid, created_at)
    return _direct_create_cluster_operation_target(
        operation_uuid, operation_type, target_object_type,
        target_uuid, created_at)


def get_cluster_operation_target(
    operation_uuid: str
) -> Optional[ClusterOperationTargetData]:
    """Get a cluster operation target by operation UUID.

    Returns:
        The target data, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_cluster_operation_target(operation_uuid)
    return _direct_get_cluster_operation_target(operation_uuid)


def get_cluster_operation_targets_for_object(
    target_object_type: ObjectType,
    target_uuid: str
) -> list[ClusterOperationTargetData]:
    """Get all cluster operation targets for an object, ordered by sequence.

    Returns:
        List of target data ordered by sequence_number ascending.
    """
    if _use_database_service():
        return _grpc_get_cluster_operation_targets_for_object(
            target_object_type, target_uuid)
    return _direct_get_cluster_operation_targets_for_object(
        target_object_type, target_uuid)


def get_latest_cluster_operation_target(
    target_object_type: ObjectType,
    target_uuid: str
) -> Optional[ClusterOperationTargetData]:
    """Get the most recent cluster operation target for an object.

    Returns:
        The most recent target data, or None if no operations exist.
    """
    if _use_database_service():
        return _grpc_get_latest_cluster_operation_target(
            target_object_type, target_uuid)
    return _direct_get_latest_cluster_operation_target(
        target_object_type, target_uuid)


def has_pending_cluster_operation_target(
    target_object_type: ObjectType,
    target_uuid: str
) -> bool:
    """True if any in-flight cluster operation targets this object.

    "In flight" means the operation's row in object_states is in
    {queued, preflight, executing}. Any later operation against the same
    object that has reached a terminal state does NOT mask an earlier
    in-flight operation, fixing the latest-only race in the legacy
    single-pointer last_cluster_operation gating.
    """
    if _use_database_service():
        return _grpc_has_pending_cluster_operation_target(
            target_object_type, target_uuid)
    return _direct_has_pending_cluster_operation_target(
        target_object_type, target_uuid)


def get_recent_terminal_op_states_for_target(
    target_object_type: ObjectType,
    target_uuid: str,
    limit: int,
    op_type: Optional[str] = None
) -> list[tuple[str, str, float]]:
    """Return up to ``limit`` most recent terminal op states for an object.

    Joins ``cluster_operation_targets`` against ``object_states`` and
    filters to terminal cluster operation states (complete, abort,
    deleted, error). Results are ordered newest first by
    ``object_states.update_time``. If ``op_type`` is provided, results
    are additionally narrowed to that operation type (e.g. ``'net_op'``).

    This is a generic helper -- it targets any object type. The
    maintain pass uses ``target_object_type='network'`` and
    ``op_type='net_op'`` to power its cooldown and circuit-breaker
    queries.

    Returns a list of ``(op_uuid, state_value, update_time)`` tuples,
    newest first. Returns an empty list on database error.
    """
    if _use_database_service():
        return _grpc_get_recent_terminal_op_states_for_target(
            target_object_type, target_uuid, limit, op_type)
    return _direct_get_recent_terminal_op_states_for_target(
        target_object_type, target_uuid, limit, op_type)


def delete_cluster_operation_target(
    operation_uuid: str
) -> bool:
    """Delete a single cluster operation target.

    Returns:
        True if the delete succeeded, False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_cluster_operation_target(operation_uuid)
    return _direct_delete_cluster_operation_target(operation_uuid)


def delete_cluster_operation_targets_for_object(
    target_object_type: ObjectType,
    target_uuid: str
) -> bool:
    """Delete all cluster operation targets for an object.

    Used by hard_delete() to clean up when an object is destroyed.

    Returns:
        True if the delete succeeded, False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_cluster_operation_targets_for_object(
            target_object_type, target_uuid)
    return _direct_delete_cluster_operation_targets_for_object(
        target_object_type, target_uuid)


def delete_stale_cluster_operation_targets(max_age: float) -> int:
    """Prune cluster_operation_targets rows for completed operations.

    Used by the cluster daemon to bound the size of the
    cluster_operation_targets history table on long-lived deployments.
    Only rows whose operation has reached a terminal state (or been
    hard-deleted entirely) are removed -- in-flight operations are
    always preserved regardless of age.

    Args:
        max_age: Maximum age in seconds. Targets older than this whose
            operation is no longer active are deleted.

    Returns:
        Number of rows deleted.
    """
    if _use_database_service():
        return _grpc_delete_stale_cluster_operation_targets(max_age)
    older_than = time.time() - max_age
    return _direct_delete_stale_cluster_operation_targets(older_than)


# =============================================================================
# IPAM Reservation Direct Access Functions
# These are used by the database daemon for atomic IP address reservation.
# =============================================================================

def _direct_reserve_address(reservation: IPAMReservation) -> bool:
    """Atomically reserve an IP address in MariaDB.

    Uses INSERT with the unique constraint on (ipam_uuid, address) to ensure
    atomicity. If the address is already reserved, IntegrityError is raised
    and we return False.

    Args:
        reservation: The IPAMReservation to store.

    Returns:
        True if the reservation was created, False if the address was already
        reserved.
    """
    engine = _get_engine()
    table = _get_ipam_reservations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                ipam_uuid=reservation.ipam_uuid,
                address=str(reservation.address),
                reservation_type=reservation.reservation_type,
                user_type=reservation.user_type,
                user_uuid=reservation.user_uuid,
                reserved_at=reservation.reserved_at,
                comment=reservation.comment
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        # Address already reserved - this is expected and not an error
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB reserve failed for {reservation.ipam_uuid}/'
                    f'{reservation.address}: {e}')
        return False


def _direct_release_address(ipam_uuid: UUID, address: str,
                            halo_reservation: IPAMReservation) -> bool:
    """Release an IP address by updating it to deletion-halo state.

    Uses a transactional update to atomically change the reservation to
    deletion-halo state. This preserves the row for the halo period.

    Args:
        ipam_uuid: The IPAM UUID.
        address: The IP address to release (as string).
        halo_reservation: The new reservation data with deletion-halo type.

    Returns:
        True if the update succeeded, False if the reservation didn't exist
        or the update failed.
    """
    engine = _get_engine()
    table = _get_ipam_reservations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                sa.and_(
                    table.c.ipam_uuid == ipam_uuid,
                    table.c.address == address
                )
            ).values(
                reservation_type=halo_reservation.reservation_type,
                user_type=halo_reservation.user_type,
                user_uuid=halo_reservation.user_uuid,
                reserved_at=halo_reservation.reserved_at,
                comment=halo_reservation.comment
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB release failed for {ipam_uuid}/{address}: {e}')
        return False


def _direct_get_reservation(ipam_uuid: UUID,
                            address: str) -> Optional[IPAMReservation]:
    """Get a single reservation by IPAM UUID and address.

    Args:
        ipam_uuid: The IPAM UUID.
        address: The IP address (as string).

    Returns:
        The IPAMReservation if found, None otherwise.
    """
    engine = _get_engine()
    table = _get_ipam_reservations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                sa.and_(
                    table.c.ipam_uuid == ipam_uuid,
                    table.c.address == address
                )
            )
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            # MariaDB INET4 returns the address as a string
            return IPAMReservation(
                ipam_uuid=result.ipam_uuid,
                address=IPv4Address(result.address),
                reservation_type=result.reservation_type,
                user_type=result.user_type,
                user_uuid=result.user_uuid,
                reserved_at=result.reserved_at,
                comment=result.comment
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for {ipam_uuid}/{address}: {e}')
        return None


def _direct_get_reservations_for_ipam(
        ipam_uuid: UUID) -> list[IPAMReservation]:
    """Get all reservations for an IPAM.

    Args:
        ipam_uuid: The IPAM UUID.

    Returns:
        List of IPAMReservation objects.
    """
    engine = _get_engine()
    table = _get_ipam_reservations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.ipam_uuid == ipam_uuid)
            result = conn.execute(stmt).fetchall()

            return [
                IPAMReservation(
                    ipam_uuid=row.ipam_uuid,
                    address=IPv4Address(row.address),
                    reservation_type=row.reservation_type,
                    user_type=row.user_type,
                    user_uuid=row.user_uuid,
                    reserved_at=row.reserved_at,
                    comment=row.comment
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for IPAM {ipam_uuid}: {e}')
        return []


def _direct_delete_reservation(ipam_uuid: UUID, address: str) -> bool:
    """Delete a single reservation (hard delete).

    Args:
        ipam_uuid: The IPAM UUID.
        address: The IP address (as string).

    Returns:
        True if deleted, False if not found or error.
    """
    engine = _get_engine()
    table = _get_ipam_reservations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.ipam_uuid == ipam_uuid,
                    table.c.address == address
                )
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for {ipam_uuid}/{address}: {e}')
        return False


def _direct_delete_reservations_for_ipam(ipam_uuid: UUID) -> int:
    """Delete all reservations for an IPAM (hard delete).

    Args:
        ipam_uuid: The IPAM UUID.

    Returns:
        Number of reservations deleted.
    """
    engine = _get_engine()
    table = _get_ipam_reservations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.ipam_uuid == ipam_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for IPAM {ipam_uuid}: {e}')
        return 0


def _direct_release_haloed_addresses(ipam_uuid: UUID, older_than: float) -> int:
    """Delete deletion-halo reservations older than the specified time.

    Args:
        ipam_uuid: The IPAM UUID.
        older_than: Unix timestamp - delete halos reserved before this time.

    Returns:
        Number of reservations deleted.
    """
    engine = _get_engine()
    table = _get_ipam_reservations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.ipam_uuid == ipam_uuid,
                    table.c.reservation_type == ReservationType.DELETION_HALO,
                    table.c.reserved_at < older_than
                )
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount
    except OperationalError as e:
        LOG.warning(f'MariaDB halo release failed for IPAM {ipam_uuid}: {e}')
        return 0


def _direct_get_addresses_in_use(ipam_uuid: UUID) -> set[str]:
    """Get all addresses currently in use for an IPAM.

    Args:
        ipam_uuid: The IPAM UUID.

    Returns:
        Set of IP addresses (as strings) that are reserved.
    """
    engine = _get_engine()
    table = _get_ipam_reservations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table.c.address).where(
                table.c.ipam_uuid == ipam_uuid
            )
            result = conn.execute(stmt).fetchall()
            # INET4 returns addresses as strings
            return {str(row.address) for row in result}
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for IPAM {ipam_uuid}: {e}')
        return set()


# =============================================================================
# IPAM Reservation Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def reserve_address(reservation: IPAMReservation) -> bool:
    """Atomically reserve an IP address.

    Args:
        reservation: The IPAMReservation to store.

    Returns:
        True if the reservation was created, False if already reserved.
    """
    if _use_database_service():
        return _grpc_reserve_address(reservation)
    return _direct_reserve_address(reservation)


def release_address(ipam_uuid: UUID, address: str,
                    halo_reservation: IPAMReservation) -> bool:
    """Release an IP address by updating it to deletion-halo state.

    Args:
        ipam_uuid: The IPAM UUID.
        address: The IP address to release (as string).
        halo_reservation: The new reservation data with deletion-halo type.

    Returns:
        True if successful, False otherwise.
    """
    if _use_database_service():
        return _grpc_release_address(str(ipam_uuid), address, halo_reservation)
    return _direct_release_address(ipam_uuid, address, halo_reservation)


def get_reservation(ipam_uuid: UUID, address: str) -> Optional[IPAMReservation]:
    """Get a single reservation by IPAM UUID and address.

    Args:
        ipam_uuid: The IPAM UUID.
        address: The IP address (as string).

    Returns:
        The IPAMReservation if found, None otherwise.
    """
    if _use_database_service():
        return _grpc_get_reservation(str(ipam_uuid), address)
    return _direct_get_reservation(ipam_uuid, address)


def get_reservations_for_ipam(ipam_uuid: UUID) -> list[IPAMReservation]:
    """Get all reservations for an IPAM.

    Args:
        ipam_uuid: The IPAM UUID.

    Returns:
        List of IPAMReservation objects.
    """
    if _use_database_service():
        return _grpc_get_reservations_for_ipam(str(ipam_uuid))
    return _direct_get_reservations_for_ipam(ipam_uuid)


def delete_reservation(ipam_uuid: UUID, address: str) -> bool:
    """Delete a single reservation (hard delete).

    Args:
        ipam_uuid: The IPAM UUID.
        address: The IP address (as string).

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_reservation(str(ipam_uuid), address)
    return _direct_delete_reservation(ipam_uuid, address)


def delete_reservations_for_ipam(ipam_uuid: UUID) -> int:
    """Delete all reservations for an IPAM (hard delete).

    Args:
        ipam_uuid: The IPAM UUID.

    Returns:
        Number of reservations deleted.
    """
    if _use_database_service():
        return _grpc_delete_reservations_for_ipam(str(ipam_uuid))
    return _direct_delete_reservations_for_ipam(ipam_uuid)


def release_haloed_addresses(ipam_uuid: UUID, older_than: float) -> int:
    """Delete deletion-halo reservations older than the specified time.

    Args:
        ipam_uuid: The IPAM UUID.
        older_than: Unix timestamp - delete halos reserved before this time.

    Returns:
        Number of reservations deleted.
    """
    if _use_database_service():
        return _grpc_release_haloed_addresses(str(ipam_uuid), older_than)
    return _direct_release_haloed_addresses(ipam_uuid, older_than)


def get_addresses_in_use(ipam_uuid: UUID) -> set[str]:
    """Get all addresses currently in use for an IPAM.

    Args:
        ipam_uuid: The IPAM UUID.

    Returns:
        Set of IP addresses (as strings) that are reserved.
    """
    if _use_database_service():
        return _grpc_get_addresses_in_use(str(ipam_uuid))
    return _direct_get_addresses_in_use(ipam_uuid)


# =============================================================================
# Upload Direct Access Functions
# These are used by the database daemon for upload object storage.
# =============================================================================

def _direct_create_upload(upload_uuid: UUID, node: str, created_at: float,
                          version: int) -> bool:
    """Create an upload record in MariaDB.

    Args:
        upload_uuid: The UUID of the upload.
        node: The node where the upload data is stored.
        created_at: Unix timestamp when the upload was created.
        version: The object version number.

    Returns:
        True if the record was created, False if it already exists or error.
    """
    engine = _get_engine()
    table = _get_uploads_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=upload_uuid,
                node=node,
                created_at=created_at,
                version=version
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        # Upload already exists
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB create failed for upload {upload_uuid}: {e}')
        return False


def _direct_get_upload(upload_uuid: UUID) -> Optional[UploadData]:
    """Get upload static values from MariaDB.

    Args:
        upload_uuid: The UUID of the upload.

    Returns:
        An UploadData object, or None if not found.
    """
    engine = _get_engine()
    table = _get_uploads_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == upload_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return UploadData(
                uuid=result.uuid,
                node=result.node,
                created_at=result.created_at,
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for upload {upload_uuid}: {e}')
        return None


def _direct_get_uploads(
    node: Optional[str] = None,
    created_before: Optional[float] = None
) -> list[UploadData]:
    """Get uploads from MariaDB with optional filters.

    Args:
        node: If provided, only return uploads on this node.
        created_before: If provided, only return uploads created before this
            Unix timestamp.

    Returns:
        List of UploadData objects.
    """
    engine = _get_engine()
    table = _get_uploads_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table)

            # Apply optional filters
            if node:
                stmt = stmt.where(table.c.node == node)
            if created_before:
                stmt = stmt.where(table.c.created_at < created_before)

            result = conn.execute(stmt).fetchall()

            return [
                UploadData(
                    uuid=row.uuid,
                    node=row.node,
                    created_at=row.created_at,
                    version=row.version
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for uploads: {e}')
        return []


def _direct_delete_upload(upload_uuid: UUID) -> bool:
    """Delete an upload record from MariaDB.

    Args:
        upload_uuid: The UUID of the upload.

    Returns:
        True if deleted, False if not found or error.
    """
    engine = _get_engine()
    table = _get_uploads_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == upload_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for upload {upload_uuid}: {e}')
        return False


def _direct_update_upload(data: UploadData) -> bool:
    """Update an upload record in MariaDB.

    This is used to persist version upgrades.

    Args:
        data: The UploadData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    engine = _get_engine()
    table = _get_uploads_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                node=data.node,
                created_at=data.created_at,
                version=data.version
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update failed for upload {data.uuid}: {e}')
        return False


# =============================================================================
# Blob Direct Access Functions
# These are used by the database daemon for blob object storage.
# =============================================================================

def _direct_create_blob(blob_uuid: UUID, modified: float, fetched_at: float,
                        version: int) -> bool:
    """Create a blob record in MariaDB.

    Args:
        blob_uuid: The UUID of the blob.
        modified: Unix timestamp when the blob source was last modified.
        fetched_at: Unix timestamp when the blob was fetched.
        version: The object version number.

    Returns:
        True if the record was created, False if it already exists or error.
    """
    engine = _get_engine()
    table = _get_blobs_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=blob_uuid,
                modified=modified,
                fetched_at=fetched_at,
                version=version
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        # Blob already exists
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB create failed for blob {blob_uuid}: {e}')
        return False


def _direct_get_blob(blob_uuid: UUID) -> Optional[BlobData]:
    """Get blob static values from MariaDB.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        A BlobData object, or None if not found.
    """
    engine = _get_engine()
    table = _get_blobs_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == blob_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return BlobData(
                uuid=result.uuid,
                modified=result.modified,
                fetched_at=result.fetched_at,
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for blob {blob_uuid}: {e}')
        return None


def _direct_get_all_blob_uuids() -> list[str]:
    """Get all blob UUIDs from MariaDB.

    Returns:
        List of blob UUID strings.
    """
    engine = _get_engine()
    table = _get_blobs_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table.c.uuid)
            result = conn.execute(stmt).fetchall()
            return [str(row.uuid) for row in result]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for blob UUIDs: {e}')
        return []


def _direct_delete_blob(blob_uuid: UUID) -> bool:
    """Delete a blob record from MariaDB.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        True if deleted, False if not found or error.
    """
    engine = _get_engine()
    table = _get_blobs_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == blob_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for blob {blob_uuid}: {e}')
        return False


def _direct_update_blob(data: BlobData) -> bool:
    """Update a blob record in MariaDB.

    This is used to persist version upgrades.

    Args:
        data: The BlobData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    engine = _get_engine()
    table = _get_blobs_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                modified=data.modified,
                fetched_at=data.fetched_at,
                version=data.version
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update failed for blob {data.uuid}: {e}')
        return False


# =============================================================================
# Upload gRPC Client Functions
# These call the database microservice for upload operations.
# =============================================================================

def _grpc_create_upload(upload_uuid: UUID, node: str, created_at: float,
                        version: int) -> bool:
    """Create an upload record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateUploadRequest(
            upload=database_pb2.UploadData(
                uuid=str(upload_uuid),
                node=node,
                created_at=created_at,
                version=version
            )
        )
        reply = _grpc_call(stub.CreateUpload, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC CreateUpload failed for {upload_uuid}: {e}')
        return False


def _grpc_get_upload(upload_uuid: UUID) -> Optional[UploadData]:
    """Get upload static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetUploadRequest(uuid=str(upload_uuid))
        reply = _grpc_call(stub.GetUpload, request)
        if not reply.found:
            return None
        return UploadData(
            uuid=reply.upload.uuid,
            node=reply.upload.node,
            created_at=reply.upload.created_at,
            version=reply.upload.version
        )
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetUpload failed for {upload_uuid}: {e}')
        return None


def _grpc_get_uploads(
    node: Optional[str] = None,
    created_before: Optional[float] = None
) -> list[UploadData]:
    """Get uploads via the database microservice with optional filters."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetUploadsRequest(
            node=node or '',
            created_before=created_before or 0.0
        )
        reply = _grpc_call(stub.GetUploads, request)
        return [
            UploadData(
                uuid=u.uuid,
                node=u.node,
                created_at=u.created_at,
                version=u.version
            )
            for u in reply.uploads
        ]
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetUploads failed: {e}')
        return []


def _grpc_delete_upload(upload_uuid: UUID) -> bool:
    """Delete an upload record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteUploadRequest(uuid=str(upload_uuid))
        reply = _grpc_call(stub.DeleteUpload, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteUpload failed for {upload_uuid}: {e}')
        return False


def _grpc_update_upload(data: UploadData) -> bool:
    """Update an upload record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateUploadRequest(
            upload=database_pb2.UploadData(
                uuid=str(data.uuid),
                node=data.node,
                created_at=data.created_at,
                version=data.version
            )
        )
        reply = _grpc_call(stub.UpdateUpload, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpdateUpload failed for {data.uuid}: {e}')
        return False


# =============================================================================
# Blob gRPC Client Functions
# These call the database microservice for blob operations.
# =============================================================================

def _grpc_create_blob(blob_uuid: UUID, modified: float, fetched_at: float,
                      version: int) -> bool:
    """Create a blob record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateBlobRequest(
            blob=database_pb2.BlobData(
                uuid=str(blob_uuid),
                modified=modified,
                fetched_at=fetched_at,
                version=version
            )
        )
        reply = _grpc_call(stub.CreateBlob, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC CreateBlob failed for {blob_uuid}: {e}')
        return False


def _grpc_get_blob(blob_uuid: UUID) -> Optional[BlobData]:
    """Get blob static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetBlobRequest(uuid=str(blob_uuid))
        reply = _grpc_call(stub.GetBlob, request)
        if not reply.found:
            return None
        return BlobData(
            uuid=reply.blob.uuid,
            modified=reply.blob.modified,
            fetched_at=reply.blob.fetched_at,
            version=reply.blob.version
        )
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetBlob failed for {blob_uuid}: {e}')
        return None


def _grpc_get_all_blob_uuids() -> list[str]:
    """Get all blob UUIDs via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllBlobUuidsRequest()
        reply = _grpc_call(stub.GetAllBlobUuids, request)
        return list(reply.uuids)
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetAllBlobUuids failed: {e}')
        return []


def _grpc_delete_blob(blob_uuid: UUID) -> bool:
    """Delete a blob record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteBlobRequest(uuid=str(blob_uuid))
        reply = _grpc_call(stub.DeleteBlob, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteBlob failed for {blob_uuid}: {e}')
        return False


def _grpc_update_blob(data: BlobData) -> bool:
    """Update a blob record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateBlobRequest(
            blob=database_pb2.BlobData(
                uuid=str(data.uuid),
                modified=data.modified,
                fetched_at=data.fetched_at,
                version=data.version
            )
        )
        reply = _grpc_call(stub.UpdateBlob, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpdateBlob failed for {data.uuid}: {e}')
        return False


# =============================================================================
# Upload Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def create_upload(upload_uuid: UUID, node: str, created_at: float,
                  version: int) -> bool:
    """Create an upload record.

    Args:
        upload_uuid: The UUID of the upload.
        node: The node where the upload data is stored.
        created_at: Unix timestamp when the upload was created.
        version: The object version number.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_upload(upload_uuid, node, created_at, version)
    return _direct_create_upload(upload_uuid, node, created_at, version)


def get_upload(upload_uuid: UUID) -> Optional[UploadData]:
    """Get upload static values.

    Args:
        upload_uuid: The UUID of the upload.

    Returns:
        An UploadData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_upload(upload_uuid)
    return _direct_get_upload(upload_uuid)


def get_uploads(
    node: Optional[str] = None,
    created_before: Optional[float] = None
) -> list[UploadData]:
    """Get uploads with optional filters.

    Args:
        node: If provided, only return uploads on this node.
        created_before: If provided, only return uploads created before this
            Unix timestamp.

    Returns:
        List of UploadData objects.
    """
    if _use_database_service():
        return _grpc_get_uploads(node, created_before)
    return _direct_get_uploads(node, created_before)


def delete_upload(upload_uuid: UUID) -> bool:
    """Delete an upload record.

    Args:
        upload_uuid: The UUID of the upload.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_upload(upload_uuid)
    return _direct_delete_upload(upload_uuid)


def update_upload(data: UploadData) -> bool:
    """Update an upload record.

    This is used to persist version upgrades.

    Args:
        data: The UploadData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_upload(data)
    return _direct_update_upload(data)


# =============================================================================
# Blob Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def create_blob(blob_uuid: UUID, modified: float, fetched_at: float,
                version: int) -> bool:
    """Create a blob record.

    Args:
        blob_uuid: The UUID of the blob.
        modified: Unix timestamp when the blob source was last modified.
        fetched_at: Unix timestamp when the blob was fetched.
        version: The object version number.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_blob(blob_uuid, modified, fetched_at, version)
    return _direct_create_blob(blob_uuid, modified, fetched_at, version)


def get_blob(blob_uuid: UUID) -> Optional[BlobData]:
    """Get blob static values.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        A BlobData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_blob(blob_uuid)
    return _direct_get_blob(blob_uuid)


def get_all_blob_uuids() -> list[str]:
    """Get all blob UUIDs.

    Returns:
        List of blob UUID strings.
    """
    if _use_database_service():
        return _grpc_get_all_blob_uuids()
    return _direct_get_all_blob_uuids()


def delete_blob(blob_uuid: UUID) -> bool:
    """Delete a blob record.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_blob(blob_uuid)
    return _direct_delete_blob(blob_uuid)


def update_blob(data: BlobData) -> bool:
    """Update a blob record.

    This is used to persist version upgrades.

    Args:
        data: The BlobData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_blob(data)
    return _direct_update_blob(data)


def get_active_blob_uuids() -> list[str]:
    """Get UUIDs of all blobs in active states.

    Active states are 'initial' and 'created' (not 'deleted' or 'error').

    Returns:
        List of blob UUID strings in active states (empty on error).
    """
    active_states = ['initial', 'created']
    return get_objects_by_state(ObjectType.BLOB, active_states) or []


# =============================================================================
# DnsMasq Direct Access Functions
# These are used by the database daemon for DnsMasq object storage.
# =============================================================================

def _direct_create_dnsmasq(data: DnsMasqData) -> bool:
    """Create a DnsMasq record in MariaDB.

    Args:
        data: The DnsMasqData to insert.

    Returns:
        True if the record was created, False if it already exists or error.
    """
    engine = _get_engine()
    table = _get_dnsmasq_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                namespace=data.namespace,
                owner_type=str(data.owner_type),
                owner_uuid=data.owner_uuid,
                version=data.version,
                provide_dhcp=data.provide_dhcp,
                provide_dns=data.provide_dns
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        # DnsMasq already exists
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB create failed for dnsmasq {data.uuid}: {e}')
        return False


def _direct_get_dnsmasq(dnsmasq_uuid: UUID) -> Optional[DnsMasqData]:
    """Get DnsMasq static values from MariaDB.

    Args:
        dnsmasq_uuid: The UUID of the DnsMasq.

    Returns:
        A DnsMasqData object, or None if not found.
    """
    engine = _get_engine()
    table = _get_dnsmasq_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.uuid == _ensure_uuid(dnsmasq_uuid))
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            # ObjectType is a str enum so we can construct from the string value
            # stored in the database. Mypy doesn't understand this pattern.
            return DnsMasqData(
                uuid=result.uuid,
                namespace=result.namespace,
                owner_type=ObjectType(result.owner_type),  # type: ignore[call-arg]
                owner_uuid=result.owner_uuid,
                version=result.version,
                provide_dhcp=result.provide_dhcp,
                provide_dns=result.provide_dns
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for dnsmasq {dnsmasq_uuid}: {e}')
        return None


def _direct_get_dnsmasqs(
    namespace: Optional[str] = None,
    owner_uuid: Optional[UUID] = None
) -> list[DnsMasqData]:
    """Get DnsMasq objects from MariaDB with optional filters.

    Args:
        namespace: If provided, only return DnsMasq objects in this namespace.
        owner_uuid: If provided, only return DnsMasq objects owned by this UUID.

    Returns:
        List of DnsMasqData objects.
    """
    engine = _get_engine()
    table = _get_dnsmasq_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table)

            # Apply optional filters
            if namespace:
                stmt = stmt.where(table.c.namespace == namespace)
            if owner_uuid:
                stmt = stmt.where(
                    table.c.owner_uuid == _ensure_uuid(owner_uuid))

            result = conn.execute(stmt).fetchall()

            # ObjectType is a str enum so we can construct from the string value
            return [
                DnsMasqData(
                    uuid=row.uuid,
                    namespace=row.namespace,
                    owner_type=ObjectType(row.owner_type),  # type: ignore[call-arg]
                    owner_uuid=row.owner_uuid,
                    version=row.version,
                    provide_dhcp=row.provide_dhcp,
                    provide_dns=row.provide_dns
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for dnsmasqs: {e}')
        return []


def _direct_delete_dnsmasq(dnsmasq_uuid: UUID) -> bool:
    """Delete a DnsMasq record from MariaDB.

    Args:
        dnsmasq_uuid: The UUID of the DnsMasq.

    Returns:
        True if deleted, False if not found or error.
    """
    engine = _get_engine()
    table = _get_dnsmasq_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.uuid == _ensure_uuid(dnsmasq_uuid))
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for dnsmasq {dnsmasq_uuid}: {e}')
        return False


def _direct_update_dnsmasq(data: DnsMasqData) -> bool:
    """Update a DnsMasq record in MariaDB.

    This is used to persist version upgrades.

    Args:
        data: The DnsMasqData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    engine = _get_engine()
    table = _get_dnsmasq_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == _ensure_uuid(data.uuid)
            ).values(
                namespace=data.namespace,
                owner_type=str(data.owner_type),
                owner_uuid=data.owner_uuid,
                version=data.version,
                provide_dhcp=data.provide_dhcp,
                provide_dns=data.provide_dns
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update failed for dnsmasq {data.uuid}: {e}')
        return False


# =============================================================================
# Object References Direct Access Functions
# These access the database directly and are used by the database daemon.
# =============================================================================

@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_record_relationship(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: RelationshipType,
    relationship_value: Optional[str],
    target_type: ObjectType,
    target_uuid: str | UUID
) -> bool:
    """Record a relationship between objects directly in MariaDB.

    Creates a reference from source to target. If the reference already exists,
    updates last_active to the current time. This makes repeated calls (like
    observe() on blobs) idempotent while also refreshing the activity timestamp.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: The type of relationship.
        relationship_value: Optional value for the relationship.
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.

    Returns:
        True if created or updated, False on error.
    """
    engine = _get_engine()
    now = time.time()

    try:
        with engine.connect() as conn:
            # Use INSERT ... ON DUPLICATE KEY UPDATE for idempotent upsert.
            # If the row already exists (based on primary key), update
            # last_active to the current time. This allows observe() to
            # refresh the activity timestamp without needing separate logic.
            stmt = sa.text('''
                INSERT INTO object_references (
                    source_object_type, source_uuid, relationship,
                    relationship_value, target_object_type, target_uuid,
                    created, last_active
                ) VALUES (
                    :source_type, :source_uuid, :relationship,
                    :relationship_value, :target_type, :target_uuid,
                    :created, :last_active
                ) ON DUPLICATE KEY UPDATE last_active = VALUES(last_active)
            ''')
            conn.execute(stmt, {
                'source_type': str(source_type),
                'source_uuid': str(source_uuid),
                'relationship': str(relationship),
                'relationship_value': relationship_value,
                'target_type': str(target_type),
                'target_uuid': str(target_uuid),
                'created': now,
                'last_active': now
            })
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(f'MariaDB record_relationship failed: {e}')
        return False


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_remove_relationship(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: RelationshipType,
    relationship_value: Optional[str],
    target_type: ObjectType,
    target_uuid: str | UUID
) -> bool:
    """Remove a relationship between objects directly from MariaDB.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: The type of relationship.
        relationship_value: Optional value for the relationship.
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.

    Returns:
        True if removed (or didn't exist), False on error.
    """
    engine = _get_engine()
    table = _get_object_references_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.source_object_type == str(source_type),
                    table.c.source_uuid == str(source_uuid),
                    table.c.relationship == str(relationship),
                    table.c.relationship_value == relationship_value,
                    table.c.target_object_type == str(target_type),
                    table.c.target_uuid == str(target_uuid)
                )
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(f'MariaDB remove_relationship failed: {e}')
        return False


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_get_references_to(
    target_type: ObjectType,
    target_uuid: str | UUID,
    relationship: Optional[RelationshipType] = None
) -> list[ObjectReference]:
    """Get all references to an object directly from MariaDB.

    Args:
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.
        relationship: Optional filter by relationship type.

    Returns:
        List of ObjectReference objects referencing the target.
    """
    engine = _get_engine()
    table = _get_object_references_table()

    try:
        with engine.connect() as conn:
            conditions = [
                table.c.target_object_type == str(target_type),
                table.c.target_uuid == str(target_uuid)
            ]
            if relationship is not None:
                conditions.append(table.c.relationship == str(relationship))
            stmt = sa.select(table).where(sa.and_(*conditions))
            result = conn.execute(stmt)
            refs = []
            for row in result:
                # Enum __new__ expects (str, int) but lookup uses single arg
                src_type = ObjectType(row.source_object_type)  # type: ignore
                rel = RelationshipType(row.relationship)  # type: ignore
                tgt_type = ObjectType(row.target_object_type)  # type: ignore
                refs.append(ObjectReference(
                    source_object_type=src_type,
                    source_uuid=row.source_uuid,
                    relationship=rel,
                    relationship_value=row.relationship_value,
                    target_object_type=tgt_type,
                    target_uuid=row.target_uuid,
                    created=row.created,
                    last_active=row.last_active
                ))
            return refs
    except OperationalError as e:
        LOG.warning(f'MariaDB get_references_to failed: {e}')
        return []


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_get_references_from(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: Optional[RelationshipType] = None
) -> list[ObjectReference]:
    """Get all references from an object directly from MariaDB.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: Optional filter by relationship type.

    Returns:
        List of ObjectReference objects the source references.
    """
    engine = _get_engine()
    table = _get_object_references_table()

    try:
        with engine.connect() as conn:
            conditions = [
                table.c.source_object_type == str(source_type),
                table.c.source_uuid == str(source_uuid)
            ]
            if relationship is not None:
                conditions.append(table.c.relationship == str(relationship))
            stmt = sa.select(table).where(sa.and_(*conditions))
            result = conn.execute(stmt)
            refs = []
            for row in result:
                # Enum __new__ expects (str, int) but lookup uses single arg
                src_type = ObjectType(row.source_object_type)  # type: ignore
                rel = RelationshipType(row.relationship)  # type: ignore
                tgt_type = ObjectType(row.target_object_type)  # type: ignore
                refs.append(ObjectReference(
                    source_object_type=src_type,
                    source_uuid=row.source_uuid,
                    relationship=rel,
                    relationship_value=row.relationship_value,
                    target_object_type=tgt_type,
                    target_uuid=row.target_uuid,
                    created=row.created,
                    last_active=row.last_active
                ))
            return refs
    except OperationalError as e:
        LOG.warning(f'MariaDB get_references_from failed: {e}')
        return []


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_count_references_to(
    target_type: ObjectType,
    target_uuid: str | UUID,
    exclude_relationships: Optional[list[RelationshipType]] = None
) -> int:
    """Count references to an object directly from MariaDB.

    This is the replacement for blob.ref_count - it returns the number of
    objects that reference the target.

    Args:
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.
        exclude_relationships: Optional list of relationship types to exclude
            from the count.

    Returns:
        The count of references, or 0 on error.
    """
    engine = _get_engine()
    table = _get_object_references_table()

    try:
        with engine.connect() as conn:
            conditions = [
                table.c.target_object_type == str(target_type),
                table.c.target_uuid == str(target_uuid)
            ]
            if exclude_relationships:
                excluded = [str(r) for r in exclude_relationships]
                conditions.append(
                    table.c.relationship.notin_(excluded)
                )
            stmt = sa.select(sa.func.count()).where(sa.and_(*conditions))
            result = conn.execute(stmt)
            row = result.fetchone()
            return row[0] if row else 0
    except OperationalError as e:
        LOG.warning(f'MariaDB count_references_to failed: {e}')
        return 0


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_remove_all_references_from(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: Optional[RelationshipType] = None
) -> int:
    """Remove all references from an object directly from MariaDB.

    This is used during object deletion to clean up all references the
    object holds. Optionally filter by relationship type.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: Optional relationship type to filter by. If None,
            removes all references from the source.

    Returns:
        The number of references deleted, or 0 on error.
    """
    engine = _get_engine()
    table = _get_object_references_table()

    try:
        with engine.connect() as conn:
            conditions = [
                table.c.source_object_type == str(source_type),
                table.c.source_uuid == str(source_uuid)
            ]
            if relationship is not None:
                conditions.append(table.c.relationship == str(relationship))

            stmt = sa.delete(table).where(sa.and_(*conditions))
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount
    except OperationalError as e:
        LOG.warning(f'MariaDB remove_all_references_from failed: {e}')
        return 0


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_update_last_active(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: RelationshipType,
    relationship_value: Optional[str],
    target_type: ObjectType,
    target_uuid: str | UUID
) -> bool:
    """Update the last_active timestamp for a reference.

    Called by the cleaner daemon during maintenance scans to indicate the
    reference is still valid/in-use.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: The type of relationship.
        relationship_value: Optional value for the relationship.
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.

    Returns:
        True if updated, False if not found or error.
    """
    engine = _get_engine()
    table = _get_object_references_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                sa.and_(
                    table.c.source_object_type == str(source_type),
                    table.c.source_uuid == str(source_uuid),
                    table.c.relationship == str(relationship),
                    table.c.relationship_value == relationship_value,
                    table.c.target_object_type == str(target_type),
                    table.c.target_uuid == str(target_uuid)
                )
            ).values(last_active=time.time())
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update_last_active failed: {e}')
        return False


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_get_stale_references(older_than: float) -> list[ObjectReference]:
    """Get references with last_active older than the specified timestamp.

    Used by the cleaner daemon to find stale references that may indicate
    over-replication or orphaned data.

    Args:
        older_than: Unix timestamp. References with last_active older than
            this are considered stale.

    Returns:
        List of stale ObjectReference objects.
    """
    engine = _get_engine()
    table = _get_object_references_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.last_active < older_than)
            result = conn.execute(stmt)
            refs = []
            for row in result:
                # Enum __new__ expects (str, int) but lookup uses single arg
                src_type = ObjectType(row.source_object_type)  # type: ignore
                rel = RelationshipType(row.relationship)  # type: ignore
                tgt_type = ObjectType(row.target_object_type)  # type: ignore
                refs.append(ObjectReference(
                    source_object_type=src_type,
                    source_uuid=row.source_uuid,
                    relationship=rel,
                    relationship_value=row.relationship_value,
                    target_object_type=tgt_type,
                    target_uuid=row.target_uuid,
                    created=row.created,
                    last_active=row.last_active
                ))
            return refs
    except OperationalError as e:
        LOG.warning(f'MariaDB get_stale_references failed: {e}')
        return []


# =============================================================================
# Blob Hashes Direct Access Functions
# These access the database directly and are used by the database daemon.
# =============================================================================

@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_upsert_blob_hash(blob_hash: BlobHash) -> bool:
    """Upsert a blob hash record directly in MariaDB.

    Creates or updates a hash record for a blob. If the record already exists
    (based on blob_uuid, node, algorithm), updates all mutable fields.

    Args:
        blob_hash: The BlobHash record to upsert.

    Returns:
        True if created or updated, False on error.
    """
    engine = _get_engine()

    try:
        with engine.connect() as conn:
            # Use INSERT ... ON DUPLICATE KEY UPDATE for idempotent upsert
            stmt = sa.text('''
                INSERT INTO blob_hashes (
                    blob_uuid, node, algorithm, hash_value, file_size,
                    computed_at, last_verified_at, verification_status,
                    error_message
                ) VALUES (
                    :blob_uuid, :node, :algorithm, :hash_value, :file_size,
                    :computed_at, :last_verified_at, :verification_status,
                    :error_message
                ) ON DUPLICATE KEY UPDATE
                    hash_value = VALUES(hash_value),
                    file_size = VALUES(file_size),
                    last_verified_at = VALUES(last_verified_at),
                    verification_status = VALUES(verification_status),
                    error_message = VALUES(error_message)
            ''')
            conn.execute(stmt, {
                'blob_uuid': blob_hash.blob_uuid,
                'node': blob_hash.node,
                'algorithm': blob_hash.algorithm,
                'hash_value': blob_hash.hash_value,
                'file_size': blob_hash.file_size,
                'computed_at': blob_hash.computed_at,
                'last_verified_at': blob_hash.last_verified_at,
                'verification_status': blob_hash.verification_status,
                'error_message': blob_hash.error_message
            })
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(f'MariaDB upsert_blob_hash failed: {e}')
        return False


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_get_blob_hashes(
    blob_uuid: str,
    node: Optional[str] = None
) -> list[BlobHash]:
    """Get all hash records for a blob directly from MariaDB.

    Args:
        blob_uuid: The UUID of the blob.
        node: Optional filter by node name.

    Returns:
        List of BlobHash records for the blob.
    """
    engine = _get_engine()
    table = _get_blob_hashes_table()

    try:
        with engine.connect() as conn:
            conditions = [table.c.blob_uuid == blob_uuid]
            if node is not None:
                conditions.append(table.c.node == node)
            stmt = sa.select(table).where(sa.and_(*conditions))
            result = conn.execute(stmt)
            hashes = []
            for row in result:
                hashes.append(BlobHash(
                    blob_uuid=row.blob_uuid,
                    node=row.node,
                    algorithm=row.algorithm,
                    hash_value=row.hash_value,
                    file_size=row.file_size,
                    computed_at=row.computed_at,
                    last_verified_at=row.last_verified_at,
                    verification_status=row.verification_status,
                    error_message=row.error_message
                ))
            return hashes
    except OperationalError as e:
        LOG.warning(f'MariaDB get_blob_hashes failed: {e}')
        return []


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_find_blob_by_hash(
    algorithm: str,
    hash_value: str
) -> Optional[str]:
    """Find a blob UUID by hash value directly from MariaDB.

    Uses the idx_hash_lookup index for O(1) lookup performance.

    Args:
        algorithm: The hash algorithm (sha512, sha256, etc.).
        hash_value: The hash value to search for.

    Returns:
        The blob UUID if found, None otherwise.
    """
    engine = _get_engine()
    table = _get_blob_hashes_table()

    try:
        with engine.connect() as conn:
            # Only return blobs with valid hashes to avoid returning blobs
            # with corrupted or unverified data
            stmt = sa.select(table.c.blob_uuid).where(
                sa.and_(
                    table.c.algorithm == algorithm,
                    table.c.hash_value == hash_value,
                    table.c.verification_status == 'valid'
                )
            ).limit(1)
            result = conn.execute(stmt)
            row = result.fetchone()
            if row:
                return str(row.blob_uuid)
            return None
    except OperationalError as e:
        LOG.warning(f'MariaDB find_blob_by_hash failed: {e}')
        return None


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_get_stale_blob_hashes(older_than: float) -> list[BlobHash]:
    """Get blob hashes with last_verified_at older than the specified timestamp.

    Used by the scheduled verification task to find blobs needing re-verification.

    Args:
        older_than: Unix timestamp. Hashes with last_verified_at older than
            this are considered stale.

    Returns:
        List of stale BlobHash records.
    """
    engine = _get_engine()
    table = _get_blob_hashes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.last_verified_at < older_than)
            result = conn.execute(stmt)
            hashes = []
            for row in result:
                hashes.append(BlobHash(
                    blob_uuid=row.blob_uuid,
                    node=row.node,
                    algorithm=row.algorithm,
                    hash_value=row.hash_value,
                    file_size=row.file_size,
                    computed_at=row.computed_at,
                    last_verified_at=row.last_verified_at,
                    verification_status=row.verification_status,
                    error_message=row.error_message
                ))
            return hashes
    except OperationalError as e:
        LOG.warning(f'MariaDB get_stale_blob_hashes failed: {e}')
        return []


@util_callstack.restrict_caller(
        'shakenfist.daemons.database',
        'shakenfist.mariadb'
    )
def _direct_delete_blob_hashes(blob_uuid: str) -> bool:
    """Delete all hash records for a blob directly from MariaDB.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        True if deleted (or didn't exist), False on error.
    """
    engine = _get_engine()
    table = _get_blob_hashes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.blob_uuid == blob_uuid)
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(f'MariaDB delete_blob_hashes failed: {e}')
        return False


# =============================================================================
# DnsMasq gRPC Client Functions
# These call the database microservice for DnsMasq operations.
# =============================================================================

def _grpc_create_dnsmasq(data: DnsMasqData) -> bool:
    """Create a DnsMasq record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateDnsMasqRequest(
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
        reply = _grpc_call(stub.CreateDnsMasq, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC CreateDnsMasq failed for {data.uuid}: {e}')
        return False


def _grpc_get_dnsmasq(dnsmasq_uuid: UUID) -> Optional[DnsMasqData]:
    """Get DnsMasq static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetDnsMasqRequest(uuid=str(dnsmasq_uuid))
        reply = _grpc_call(stub.GetDnsMasq, request)
        if not reply.found:
            return None
        owner_type = ObjectType.from_proto_id(reply.dnsmasq.owner_type)
        if owner_type is None:
            owner_type = ObjectType.UNKNOWN
        return DnsMasqData(
            uuid=reply.dnsmasq.uuid,
            namespace=reply.dnsmasq.namespace,
            owner_type=owner_type,
            owner_uuid=reply.dnsmasq.owner_uuid,
            version=reply.dnsmasq.version,
            provide_dhcp=reply.dnsmasq.provide_dhcp,
            provide_dns=reply.dnsmasq.provide_dns
        )
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetDnsMasq failed for {dnsmasq_uuid}: {e}')
        return None


def _grpc_get_dnsmasqs(
    namespace: Optional[str] = None,
    owner_uuid: Optional[UUID] = None
) -> list[DnsMasqData]:
    """Get DnsMasq objects via the database microservice with optional filters.
    """
    try:
        stub = _get_database_stub()
        request = database_pb2.GetDnsMasqsRequest(
            namespace=namespace or '',
            owner_uuid=str(owner_uuid) if owner_uuid else ''
        )
        reply = _grpc_call(stub.GetDnsMasqs, request)
        results = []
        for d in reply.dnsmasqs:
            owner_type = ObjectType.from_proto_id(d.owner_type)
            if owner_type is None:
                owner_type = ObjectType.UNKNOWN
            results.append(DnsMasqData(
                uuid=d.uuid,
                namespace=d.namespace,
                owner_type=owner_type,
                owner_uuid=d.owner_uuid,
                version=d.version,
                provide_dhcp=d.provide_dhcp,
                provide_dns=d.provide_dns
            ))
        return results
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetDnsMasqs failed: {e}')
        return []


def _grpc_delete_dnsmasq(dnsmasq_uuid: UUID) -> bool:
    """Delete a DnsMasq record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteDnsMasqRequest(uuid=str(dnsmasq_uuid))
        reply = _grpc_call(stub.DeleteDnsMasq, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteDnsMasq failed for {dnsmasq_uuid}: {e}')
        return False


def _grpc_update_dnsmasq(data: DnsMasqData) -> bool:
    """Update a DnsMasq record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateDnsMasqRequest(
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
        reply = _grpc_call(stub.UpdateDnsMasq, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpdateDnsMasq failed for {data.uuid}: {e}')
        return False


# =============================================================================
# Object References gRPC Client Functions
# These call the database microservice for object reference operations.
# =============================================================================

def _grpc_record_relationship(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: RelationshipType,
    relationship_value: Optional[str],
    target_type: ObjectType,
    target_uuid: str | UUID
) -> bool:
    """Record a relationship via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.RecordRelationshipRequest(
            source_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                source_type.proto_id),
            source_uuid=str(source_uuid),
            relationship=cast(
                shakenfist_enums_pb2.RelationshipType.ValueType,
                relationship.proto_id),
            relationship_value=relationship_value or '',
            target_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_type.proto_id),
            target_uuid=str(target_uuid)
        )
        reply = _grpc_call(stub.RecordRelationship, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC RecordRelationship failed: {e}')
        return False


def _grpc_remove_relationship(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: RelationshipType,
    relationship_value: Optional[str],
    target_type: ObjectType,
    target_uuid: str | UUID
) -> bool:
    """Remove a relationship via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.RemoveRelationshipRequest(
            source_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                source_type.proto_id),
            source_uuid=str(source_uuid),
            relationship=cast(
                shakenfist_enums_pb2.RelationshipType.ValueType,
                relationship.proto_id),
            relationship_value=relationship_value or '',
            target_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_type.proto_id),
            target_uuid=str(target_uuid)
        )
        reply = _grpc_call(stub.RemoveRelationship, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC RemoveRelationship failed: {e}')
        return False


def _grpc_get_references_to(
    target_type: ObjectType,
    target_uuid: str | UUID,
    relationship: Optional[RelationshipType] = None
) -> list[ObjectReference]:
    """Get references to an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetReferencesToRequest(
            target_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_type.proto_id),
            target_uuid=str(target_uuid)
        )
        if relationship is not None:
            request.relationship = cast(
                shakenfist_enums_pb2.RelationshipType.ValueType,
                relationship.proto_id)
        reply = _grpc_call(stub.GetReferencesTo, request)
        refs = []
        for ref in reply.references:
            src_type = ObjectType.from_proto_id(ref.source_type)
            rel_type = RelationshipType.from_proto_id(ref.relationship)
            tgt_type = ObjectType.from_proto_id(ref.target_type)
            if src_type is None or rel_type is None or tgt_type is None:
                continue
            refs.append(ObjectReference(
                source_object_type=src_type,
                source_uuid=ref.source_uuid,
                relationship=rel_type,
                relationship_value=ref.relationship_value or None,
                target_object_type=tgt_type,
                target_uuid=ref.target_uuid,
                created=ref.created,
                last_active=ref.last_active
            ))
        return refs
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetReferencesTo failed: {e}')
        return []


def _grpc_get_references_from(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: Optional[RelationshipType] = None
) -> list[ObjectReference]:
    """Get references from an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetReferencesFromRequest(
            source_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                source_type.proto_id),
            source_uuid=str(source_uuid)
        )
        if relationship is not None:
            request.relationship = cast(
                shakenfist_enums_pb2.RelationshipType.ValueType,
                relationship.proto_id)
        reply = _grpc_call(stub.GetReferencesFrom, request)
        refs = []
        for ref in reply.references:
            src_type = ObjectType.from_proto_id(ref.source_type)
            rel_type = RelationshipType.from_proto_id(ref.relationship)
            tgt_type = ObjectType.from_proto_id(ref.target_type)
            if src_type is None or rel_type is None or tgt_type is None:
                continue
            refs.append(ObjectReference(
                source_object_type=src_type,
                source_uuid=ref.source_uuid,
                relationship=rel_type,
                relationship_value=ref.relationship_value or None,
                target_object_type=tgt_type,
                target_uuid=ref.target_uuid,
                created=ref.created,
                last_active=ref.last_active
            ))
        return refs
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetReferencesFrom failed: {e}')
        return []


def _grpc_count_references_to(
    target_type: ObjectType,
    target_uuid: str | UUID,
    exclude_relationships: Optional[list[RelationshipType]] = None
) -> int:
    """Count references to an object via the database microservice."""
    try:
        stub = _get_database_stub()
        excluded_proto = []
        if exclude_relationships:
            excluded_proto = [
                cast(
                    shakenfist_enums_pb2.RelationshipType.ValueType,
                    r.proto_id)
                for r in exclude_relationships
            ]
        request = database_pb2.CountReferencesToRequest(
            target_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_type.proto_id),
            target_uuid=str(target_uuid),
            exclude_relationships=excluded_proto
        )
        reply = _grpc_call(stub.CountReferencesTo, request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.error(f'gRPC CountReferencesTo failed: {e}')
        return 0


def _grpc_remove_all_references_from(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: Optional[RelationshipType] = None
) -> int:
    """Remove all references from an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.RemoveAllReferencesFromRequest(
            source_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                source_type.proto_id),
            source_uuid=str(source_uuid)
        )
        if relationship is not None:
            request.relationship = cast(
                shakenfist_enums_pb2.RelationshipType.ValueType,
                relationship.proto_id)
        reply = _grpc_call(stub.RemoveAllReferencesFrom, request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.error(f'gRPC RemoveAllReferencesFrom failed: {e}')
        return 0


def _grpc_update_last_active(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: RelationshipType,
    relationship_value: Optional[str],
    target_type: ObjectType,
    target_uuid: str | UUID
) -> bool:
    """Update last_active timestamp via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateLastActiveRequest(
            source_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                source_type.proto_id),
            source_uuid=str(source_uuid),
            relationship=cast(
                shakenfist_enums_pb2.RelationshipType.ValueType,
                relationship.proto_id),
            relationship_value=relationship_value or '',
            target_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_type.proto_id),
            target_uuid=str(target_uuid)
        )
        reply = _grpc_call(stub.UpdateLastActive, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpdateLastActive failed: {e}')
        return False


def _grpc_get_stale_references(older_than: float) -> list[ObjectReference]:
    """Get stale references via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetStaleReferencesRequest(older_than=older_than)
        reply = _grpc_call(stub.GetStaleReferences, request)
        refs = []
        for ref in reply.references:
            src_type = ObjectType.from_proto_id(ref.source_type)
            rel_type = RelationshipType.from_proto_id(ref.relationship)
            tgt_type = ObjectType.from_proto_id(ref.target_type)
            if src_type is None or rel_type is None or tgt_type is None:
                continue
            refs.append(ObjectReference(
                source_object_type=src_type,
                source_uuid=ref.source_uuid,
                relationship=rel_type,
                relationship_value=ref.relationship_value or None,
                target_object_type=tgt_type,
                target_uuid=ref.target_uuid,
                created=ref.created,
                last_active=ref.last_active
            ))
        return refs
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetStaleReferences failed: {e}')
        return []


# =============================================================================
# Blob Hashes gRPC Client Functions
# These call the database microservice for blob hash operations.
# =============================================================================

def _grpc_upsert_blob_hash(blob_hash: BlobHash) -> bool:
    """Upsert a blob hash record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpsertBlobHashRequest(
            blob_hash=database_pb2.BlobHashData(
                blob_uuid=blob_hash.blob_uuid,
                node=blob_hash.node,
                algorithm=blob_hash.algorithm,
                hash_value=blob_hash.hash_value,
                file_size=blob_hash.file_size,
                computed_at=blob_hash.computed_at,
                last_verified_at=blob_hash.last_verified_at,
                verification_status=blob_hash.verification_status,
                error_message=blob_hash.error_message or ''
            )
        )
        reply = _grpc_call(stub.UpsertBlobHash, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpsertBlobHash failed: {e}')
        return False


def _grpc_get_blob_hashes(
    blob_uuid: str,
    node: Optional[str] = None
) -> list[BlobHash]:
    """Get blob hash records via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetBlobHashesRequest(blob_uuid=blob_uuid)
        if node is not None:
            request.node = node
        reply = _grpc_call(stub.GetBlobHashes, request)
        hashes = []
        for h in reply.hashes:
            hashes.append(BlobHash(
                blob_uuid=h.blob_uuid,
                node=h.node,
                algorithm=h.algorithm,
                hash_value=h.hash_value,
                file_size=h.file_size,
                computed_at=h.computed_at,
                last_verified_at=h.last_verified_at,
                verification_status=h.verification_status,
                error_message=h.error_message or None
            ))
        return hashes
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetBlobHashes failed: {e}')
        return []


def _grpc_find_blob_by_hash(algorithm: str, hash_value: str) -> Optional[str]:
    """Find a blob UUID by hash via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.FindBlobByHashRequest(
            algorithm=algorithm,
            hash_value=hash_value
        )
        reply = _grpc_call(stub.FindBlobByHash, request)
        if reply.found:
            return str(reply.blob_uuid)
        return None
    except grpc.RpcError as e:
        LOG.error(f'gRPC FindBlobByHash failed: {e}')
        return None


def _grpc_get_stale_blob_hashes(older_than: float) -> list[BlobHash]:
    """Get stale blob hashes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetStaleBlobHashesRequest(older_than=older_than)
        reply = _grpc_call(stub.GetStaleBlobHashes, request)
        hashes = []
        for h in reply.hashes:
            hashes.append(BlobHash(
                blob_uuid=h.blob_uuid,
                node=h.node,
                algorithm=h.algorithm,
                hash_value=h.hash_value,
                file_size=h.file_size,
                computed_at=h.computed_at,
                last_verified_at=h.last_verified_at,
                verification_status=h.verification_status,
                error_message=h.error_message or None
            ))
        return hashes
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetStaleBlobHashes failed: {e}')
        return []


def _grpc_delete_blob_hashes(blob_uuid: str) -> bool:
    """Delete blob hash records via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteBlobHashesRequest(blob_uuid=blob_uuid)
        reply = _grpc_call(stub.DeleteBlobHashes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteBlobHashes failed: {e}')
        return False


# =============================================================================
# DnsMasq Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def create_dnsmasq(data: DnsMasqData) -> bool:
    """Create a DnsMasq record.

    Args:
        data: The DnsMasqData to insert.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_dnsmasq(data)
    return _direct_create_dnsmasq(data)


def get_dnsmasq(dnsmasq_uuid: UUID) -> Optional[DnsMasqData]:
    """Get DnsMasq static values.

    Args:
        dnsmasq_uuid: The UUID of the DnsMasq.

    Returns:
        A DnsMasqData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_dnsmasq(dnsmasq_uuid)
    return _direct_get_dnsmasq(dnsmasq_uuid)


def get_dnsmasqs(
    namespace: Optional[str] = None,
    owner_uuid: Optional[UUID] = None
) -> list[DnsMasqData]:
    """Get DnsMasq objects with optional filters.

    Args:
        namespace: If provided, only return DnsMasq objects in this namespace.
        owner_uuid: If provided, only return DnsMasq objects owned by this UUID.

    Returns:
        List of DnsMasqData objects.
    """
    if _use_database_service():
        return _grpc_get_dnsmasqs(namespace, owner_uuid)
    return _direct_get_dnsmasqs(namespace, owner_uuid)


def delete_dnsmasq(dnsmasq_uuid: UUID) -> bool:
    """Delete a DnsMasq record.

    Args:
        dnsmasq_uuid: The UUID of the DnsMasq.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_dnsmasq(dnsmasq_uuid)
    return _direct_delete_dnsmasq(dnsmasq_uuid)


def update_dnsmasq(data: DnsMasqData) -> bool:
    """Update a DnsMasq record.

    This is used to persist version upgrades.

    Args:
        data: The DnsMasqData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_dnsmasq(data)
    return _direct_update_dnsmasq(data)


# =============================================================================
# Object References Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def record_relationship(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: RelationshipType,
    relationship_value: Optional[str],
    target_type: ObjectType,
    target_uuid: str | UUID
) -> bool:
    """Record a relationship between objects.

    Creates a reference from source to target. Idempotent - if the reference
    already exists, it's a no-op.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: The type of relationship.
        relationship_value: Optional value for the relationship (e.g., disk
            index, transcode style).
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.

    Returns:
        True if created (or already existed), False on error.
    """
    if _use_database_service():
        return _grpc_record_relationship(
            source_type, source_uuid, relationship, relationship_value,
            target_type, target_uuid)
    return _direct_record_relationship(
        source_type, source_uuid, relationship, relationship_value,
        target_type, target_uuid)


def remove_relationship(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: RelationshipType,
    relationship_value: Optional[str],
    target_type: ObjectType,
    target_uuid: str | UUID
) -> bool:
    """Remove a relationship between objects.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: The type of relationship.
        relationship_value: Optional value for the relationship.
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.

    Returns:
        True if removed (or didn't exist), False on error.
    """
    if _use_database_service():
        return _grpc_remove_relationship(
            source_type, source_uuid, relationship, relationship_value,
            target_type, target_uuid)
    return _direct_remove_relationship(
        source_type, source_uuid, relationship, relationship_value,
        target_type, target_uuid)


def get_references_to(
    target_type: ObjectType,
    target_uuid: str | UUID,
    relationship: Optional[RelationshipType] = None
) -> list[ObjectReference]:
    """Get all references to an object.

    Args:
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.
        relationship: Optional filter by relationship type.

    Returns:
        List of ObjectReference objects referencing the target.
    """
    if _use_database_service():
        return _grpc_get_references_to(target_type, target_uuid, relationship)
    return _direct_get_references_to(target_type, target_uuid, relationship)


def get_references_from(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: Optional[RelationshipType] = None
) -> list[ObjectReference]:
    """Get all references from an object.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: Optional filter by relationship type.

    Returns:
        List of ObjectReference objects the source references.
    """
    if _use_database_service():
        return _grpc_get_references_from(source_type, source_uuid, relationship)
    return _direct_get_references_from(source_type, source_uuid, relationship)


def count_references_to(
    target_type: ObjectType,
    target_uuid: str | UUID,
    exclude_relationships: Optional[list[RelationshipType]] = None
) -> int:
    """Count references to an object.

    This is the replacement for blob.ref_count - it returns the number of
    objects that reference the target.

    Args:
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.
        exclude_relationships: Optional list of relationship types to exclude
            from the count.

    Returns:
        The count of references, or 0 on error.
    """
    if _use_database_service():
        return _grpc_count_references_to(
            target_type, target_uuid, exclude_relationships)
    return _direct_count_references_to(
        target_type, target_uuid, exclude_relationships)


def remove_all_references_from(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: Optional[RelationshipType] = None
) -> int:
    """Remove all references from an object.

    This is used during object deletion to clean up all references the
    object holds. Optionally filter by relationship type.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: Optional relationship type to filter by. If None,
            removes all references from the source.

    Returns:
        The number of references deleted, or 0 on error.
    """
    if _use_database_service():
        return _grpc_remove_all_references_from(
            source_type, source_uuid, relationship)
    return _direct_remove_all_references_from(
        source_type, source_uuid, relationship)


def update_last_active(
    source_type: ObjectType,
    source_uuid: str | UUID,
    relationship: RelationshipType,
    relationship_value: Optional[str],
    target_type: ObjectType,
    target_uuid: str | UUID
) -> bool:
    """Update the last_active timestamp for a reference.

    Called by the cleaner daemon during maintenance scans to indicate the
    reference is still valid/in-use.

    Args:
        source_type: The type of the source object.
        source_uuid: The UUID of the source object.
        relationship: The type of relationship.
        relationship_value: Optional value for the relationship.
        target_type: The type of the target object.
        target_uuid: The UUID of the target object.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_last_active(
            source_type, source_uuid, relationship, relationship_value,
            target_type, target_uuid)
    return _direct_update_last_active(
        source_type, source_uuid, relationship, relationship_value,
        target_type, target_uuid)


def get_stale_references(older_than: float) -> list[ObjectReference]:
    """Get references with last_active older than the specified timestamp.

    Used by the cleaner daemon to find stale references that may indicate
    over-replication or orphaned data.

    Args:
        older_than: Unix timestamp. References with last_active older than
            this are considered stale.

    Returns:
        List of stale ObjectReference objects.
    """
    if _use_database_service():
        return _grpc_get_stale_references(older_than)
    return _direct_get_stale_references(older_than)


# =============================================================================
# Blob Hashes Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def upsert_blob_hash(blob_hash: BlobHash) -> bool:
    """Upsert a blob hash record.

    Creates or updates a hash record for a blob. If the record already exists
    (based on blob_uuid, node, algorithm), updates all mutable fields.

    Args:
        blob_hash: The BlobHash record to upsert.

    Returns:
        True if created or updated, False on error.
    """
    if _use_database_service():
        return _grpc_upsert_blob_hash(blob_hash)
    return _direct_upsert_blob_hash(blob_hash)


def get_blob_hashes(
    blob_uuid: str,
    node: Optional[str] = None
) -> list[BlobHash]:
    """Get all hash records for a blob.

    Args:
        blob_uuid: The UUID of the blob.
        node: Optional filter by node name.

    Returns:
        List of BlobHash records for the blob.
    """
    if _use_database_service():
        return _grpc_get_blob_hashes(blob_uuid, node)
    return _direct_get_blob_hashes(blob_uuid, node)


def get_valid_hash(blob_uuid: str, algorithm: str) -> Optional[str]:
    """Get the valid hash value for a blob and algorithm.

    Convenience function that finds the first valid hash for the given
    algorithm across all nodes.

    Args:
        blob_uuid: The UUID of the blob.
        algorithm: The hash algorithm (sha512, sha256, etc.).

    Returns:
        The hash value string if found and valid, None otherwise.
    """
    for h in get_blob_hashes(blob_uuid):
        if h.algorithm == algorithm and h.verification_status == 'valid':
            return h.hash_value
    return None


def get_valid_checksums(blob_uuid: str) -> dict[str, str]:
    """Get all valid checksums for a blob as a dict.

    Convenience function that collects all valid hashes into a dict
    keyed by algorithm. If multiple nodes have the same algorithm,
    only the first valid one is included.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        Dict mapping algorithm name to hash value for all valid hashes.
    """
    checksums: dict[str, str] = {}
    for h in get_blob_hashes(blob_uuid):
        if h.verification_status == 'valid' and h.algorithm not in checksums:
            checksums[h.algorithm] = h.hash_value
    return checksums


def find_blob_by_hash(algorithm: str, hash_value: str) -> Optional[str]:
    """Find a blob UUID by hash value.

    Uses the idx_hash_lookup index for O(1) lookup performance.

    Args:
        algorithm: The hash algorithm (sha512, sha256, etc.).
        hash_value: The hash value to search for.

    Returns:
        The blob UUID if found, None otherwise.
    """
    if _use_database_service():
        return _grpc_find_blob_by_hash(algorithm, hash_value)
    return _direct_find_blob_by_hash(algorithm, hash_value)


def get_stale_blob_hashes(older_than: float) -> list[BlobHash]:
    """Get blob hashes with last_verified_at older than the specified timestamp.

    Used by the scheduled verification task to find blobs needing re-verification.

    Args:
        older_than: Unix timestamp. Hashes with last_verified_at older than
            this are considered stale.

    Returns:
        List of stale BlobHash records.
    """
    if _use_database_service():
        return _grpc_get_stale_blob_hashes(older_than)
    return _direct_get_stale_blob_hashes(older_than)


def delete_blob_hashes(blob_uuid: str) -> bool:
    """Delete all hash records for a blob.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        True if deleted (or didn't exist), False on error.
    """
    if _use_database_service():
        return _grpc_delete_blob_hashes(blob_uuid)
    return _direct_delete_blob_hashes(blob_uuid)


# =============================================================================
# Blob Transfer Direct Access Functions
# These provide direct MariaDB access for the database daemon.
# =============================================================================

def _direct_create_blob_transfer(
    transfer: 'BlobTransfer'
) -> bool:
    """Create a blob transfer record directly in MariaDB.

    Args:
        transfer: The BlobTransfer record to create.

    Returns:
        True if created, False on error or if already exists.
    """
    engine = _get_engine()
    table = _get_blob_transfers_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                source_node=transfer.source_node,
                transfer_name=transfer.transfer_name,
                requesting_node=transfer.requesting_node,
                blob_uuid=transfer.blob_uuid,
                token=transfer.token,
                server_state=transfer.server_state,
                port=transfer.port,
                percentage=transfer.percentage,
                created_at=transfer.created_at,
                updated_at=transfer.updated_at
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        # Already exists
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB create_blob_transfer failed: {e}')
        return False


def _direct_get_blob_transfer(
    source_node: str,
    transfer_name: str
) -> Optional['BlobTransfer']:
    """Get a specific blob transfer record directly from MariaDB.

    Args:
        source_node: The source node name.
        transfer_name: The transfer name.

    Returns:
        The BlobTransfer record if found, None otherwise.
    """
    engine = _get_engine()
    table = _get_blob_transfers_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                sa.and_(
                    table.c.source_node == source_node,
                    table.c.transfer_name == transfer_name
                )
            )
            result = conn.execute(stmt)
            row = result.fetchone()
            if row is None:
                return None
            return BlobTransfer(
                source_node=row.source_node,
                transfer_name=row.transfer_name,
                requesting_node=row.requesting_node,
                blob_uuid=row.blob_uuid,
                token=row.token,
                server_state=row.server_state,
                port=row.port,
                percentage=row.percentage,
                created_at=row.created_at,
                updated_at=row.updated_at
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB get_blob_transfer failed: {e}')
        return None


def _direct_get_blob_transfers_for_node(
    source_node: str
) -> list['BlobTransfer']:
    """Get all blob transfers for a source node directly from MariaDB.

    Used by the transfers daemon to poll for pending work.

    Args:
        source_node: The source node name.

    Returns:
        List of BlobTransfer records for the node.
    """
    engine = _get_engine()
    table = _get_blob_transfers_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.source_node == source_node)
            result = conn.execute(stmt)
            transfers = []
            for row in result:
                transfers.append(BlobTransfer(
                    source_node=row.source_node,
                    transfer_name=row.transfer_name,
                    requesting_node=row.requesting_node,
                    blob_uuid=row.blob_uuid,
                    token=row.token,
                    server_state=row.server_state,
                    port=row.port,
                    percentage=row.percentage,
                    created_at=row.created_at,
                    updated_at=row.updated_at
                ))
            return transfers
    except OperationalError as e:
        LOG.warning(f'MariaDB get_blob_transfers_for_node failed: {e}')
        return []


def _direct_get_blob_transfers_for_blob(
    blob_uuid: str
) -> list['BlobTransfer']:
    """Get all blob transfers for a blob directly from MariaDB.

    Used by replication logic to avoid over-replicating during active transfers.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        List of BlobTransfer records for the blob.
    """
    engine = _get_engine()
    table = _get_blob_transfers_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.blob_uuid == blob_uuid)
            result = conn.execute(stmt)
            transfers = []
            for row in result:
                transfers.append(BlobTransfer(
                    source_node=row.source_node,
                    transfer_name=row.transfer_name,
                    requesting_node=row.requesting_node,
                    blob_uuid=row.blob_uuid,
                    token=row.token,
                    server_state=row.server_state,
                    port=row.port,
                    percentage=row.percentage,
                    created_at=row.created_at,
                    updated_at=row.updated_at
                ))
            return transfers
    except OperationalError as e:
        LOG.warning(f'MariaDB get_blob_transfers_for_blob failed: {e}')
        return []


def _direct_update_blob_transfer(
    source_node: str,
    transfer_name: str,
    server_state: Optional[str] = None,
    port: Optional[int] = None,
    percentage: Optional[float] = None
) -> bool:
    """Update a blob transfer record directly in MariaDB.

    Args:
        source_node: The source node name.
        transfer_name: The transfer name.
        server_state: Optional new server state.
        port: Optional new port number.
        percentage: Optional new percentage.

    Returns:
        True if updated, False on error or no fields to update.
    """
    # Build the update values dict with only provided fields
    values: dict[str, Any] = {'updated_at': time.time()}
    if server_state is not None:
        values['server_state'] = server_state
    if port is not None:
        values['port'] = port
    if percentage is not None:
        values['percentage'] = percentage

    if len(values) == 1:  # Only updated_at
        return False

    engine = _get_engine()
    table = _get_blob_transfers_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                sa.and_(
                    table.c.source_node == source_node,
                    table.c.transfer_name == transfer_name
                )
            ).values(**values)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update_blob_transfer failed: {e}')
        return False


def _direct_delete_blob_transfer(
    source_node: str,
    transfer_name: str
) -> bool:
    """Delete a blob transfer record directly from MariaDB.

    Args:
        source_node: The source node name.
        transfer_name: The transfer name.

    Returns:
        True if deleted (or didn't exist), False on error.
    """
    engine = _get_engine()
    table = _get_blob_transfers_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.source_node == source_node,
                    table.c.transfer_name == transfer_name
                )
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(f'MariaDB delete_blob_transfer failed: {e}')
        return False


def _direct_delete_stale_transfers(older_than: float) -> int:
    """Delete stale transfers directly from MariaDB.

    Args:
        older_than: Unix timestamp. Transfers with updated_at < this are deleted.

    Returns:
        Number of deleted records.
    """
    engine = _get_engine()
    table = _get_blob_transfers_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.updated_at < older_than)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount
    except OperationalError as e:
        LOG.warning(f'MariaDB delete_stale_transfers failed: {e}')
        return 0


def _direct_delete_blob_transfers_for_blob(blob_uuid: str) -> int:
    """Delete all transfers for a blob directly from MariaDB.

    This is more efficient than fetching and deleting one by one.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        Number of deleted records, or -1 on error.
    """
    engine = _get_engine()
    table = _get_blob_transfers_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.blob_uuid == blob_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount
    except OperationalError as e:
        LOG.warning(f'MariaDB delete_blob_transfers_for_blob failed: {e}')
        return -1


# =============================================================================
# Blob Transfer gRPC Client Functions
# These call the database microservice for blob transfer operations.
# =============================================================================

def _grpc_create_blob_transfer(transfer: 'BlobTransfer') -> bool:
    """Create a blob transfer record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateBlobTransferRequest(
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
        reply = _grpc_call(stub.CreateBlobTransfer, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateBlobTransfer failed for '
            f'{transfer.source_node}/{transfer.transfer_name}: {e}')
        return False


def _grpc_get_blob_transfer(
    source_node: str,
    transfer_name: str
) -> Optional['BlobTransfer']:
    """Get a blob transfer record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetBlobTransferRequest(
            source_node=source_node,
            transfer_name=transfer_name
        )
        reply = _grpc_call(stub.GetBlobTransfer, request)
        if not reply.found:
            return None
        t = reply.transfer
        return BlobTransfer(
            source_node=t.source_node,
            transfer_name=t.transfer_name,
            requesting_node=t.requesting_node,
            blob_uuid=t.blob_uuid,
            token=t.token,
            server_state=t.server_state,
            port=t.port if t.port else None,
            percentage=t.percentage,
            created_at=t.created_at,
            updated_at=t.updated_at
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetBlobTransfer failed for '
            f'{source_node}/{transfer_name}: {e}')
        return None


def _grpc_get_blob_transfers_for_node(
    source_node: str
) -> list['BlobTransfer']:
    """Get all blob transfers for a node via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetBlobTransfersForNodeRequest(
            source_node=source_node
        )
        reply = _grpc_call(stub.GetBlobTransfersForNode, request)
        transfers = []
        for t in reply.transfers:
            transfers.append(BlobTransfer(
                source_node=t.source_node,
                transfer_name=t.transfer_name,
                requesting_node=t.requesting_node,
                blob_uuid=t.blob_uuid,
                token=t.token,
                server_state=t.server_state,
                port=t.port if t.port else None,
                percentage=t.percentage,
                created_at=t.created_at,
                updated_at=t.updated_at
            ))
        return transfers
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetBlobTransfersForNode failed for {source_node}: {e}')
        return []


def _grpc_get_blob_transfers_for_blob(
    blob_uuid: str
) -> list['BlobTransfer']:
    """Get all blob transfers for a blob via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetBlobTransfersForBlobRequest(
            blob_uuid=blob_uuid
        )
        reply = _grpc_call(stub.GetBlobTransfersForBlob, request)
        transfers = []
        for t in reply.transfers:
            transfers.append(BlobTransfer(
                source_node=t.source_node,
                transfer_name=t.transfer_name,
                requesting_node=t.requesting_node,
                blob_uuid=t.blob_uuid,
                token=t.token,
                server_state=t.server_state,
                port=t.port if t.port else None,
                percentage=t.percentage,
                created_at=t.created_at,
                updated_at=t.updated_at
            ))
        return transfers
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetBlobTransfersForBlob failed for {blob_uuid}: {e}')
        return []


def _grpc_update_blob_transfer(
    source_node: str,
    transfer_name: str,
    server_state: Optional[str] = None,
    port: Optional[int] = None,
    percentage: Optional[float] = None
) -> bool:
    """Update a blob transfer record via the database microservice."""
    try:
        stub = _get_database_stub()
        # Build request with only provided optional fields
        kwargs: dict[str, Any] = {
            'source_node': source_node,
            'transfer_name': transfer_name
        }
        if server_state is not None:
            kwargs['server_state'] = server_state
        if port is not None:
            kwargs['port'] = port
        if percentage is not None:
            kwargs['percentage'] = percentage

        request = database_pb2.UpdateBlobTransferRequest(**kwargs)
        reply = _grpc_call(stub.UpdateBlobTransfer, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC UpdateBlobTransfer failed for '
            f'{source_node}/{transfer_name}: {e}')
        return False


def _grpc_delete_blob_transfer(
    source_node: str,
    transfer_name: str
) -> bool:
    """Delete a blob transfer record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteBlobTransferRequest(
            source_node=source_node,
            transfer_name=transfer_name
        )
        reply = _grpc_call(stub.DeleteBlobTransfer, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteBlobTransfer failed for '
            f'{source_node}/{transfer_name}: {e}')
        return False


def _grpc_delete_stale_transfers(max_age: float) -> int:
    """Delete stale transfers via the database microservice."""
    try:
        stub = _get_database_stub()
        older_than = time.time() - max_age
        request = database_pb2.DeleteStaleTransfersRequest(
            older_than=older_than
        )
        reply = _grpc_call(stub.DeleteStaleTransfers, request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteStaleTransfers failed: {e}')
        return 0


def _grpc_delete_blob_transfers_for_blob(blob_uuid: str) -> int:
    """Delete all transfers for a blob via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteBlobTransfersForBlobRequest(
            blob_uuid=blob_uuid
        )
        reply = _grpc_call(stub.DeleteBlobTransfersForBlob, request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteBlobTransfersForBlob failed: {e}')
        return -1


# =============================================================================
# Blob Transfer Public API Functions
# These route to direct or gRPC based on configuration.
# =============================================================================

def create_blob_transfer(transfer: 'BlobTransfer') -> bool:
    """Create a blob transfer record.

    Args:
        transfer: The BlobTransfer record to create.

    Returns:
        True if created, False on error or if already exists.
    """
    if _use_database_service():
        return _grpc_create_blob_transfer(transfer)
    return _direct_create_blob_transfer(transfer)


def get_blob_transfer(
    source_node: str,
    transfer_name: str
) -> Optional['BlobTransfer']:
    """Get a specific blob transfer record.

    Args:
        source_node: The source node name.
        transfer_name: The transfer name.

    Returns:
        The BlobTransfer record if found, None otherwise.
    """
    if _use_database_service():
        return _grpc_get_blob_transfer(source_node, transfer_name)
    return _direct_get_blob_transfer(source_node, transfer_name)


def get_blob_transfers_for_node(source_node: str) -> list['BlobTransfer']:
    """Get all blob transfers for a source node.

    Used by the transfers daemon to poll for pending work.

    Args:
        source_node: The source node name.

    Returns:
        List of BlobTransfer records for the node.
    """
    if _use_database_service():
        return _grpc_get_blob_transfers_for_node(source_node)
    return _direct_get_blob_transfers_for_node(source_node)


def get_blob_transfers_for_blob(blob_uuid: str) -> list['BlobTransfer']:
    """Get all blob transfers for a blob.

    Used by replication logic to avoid over-replicating during active transfers.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        List of BlobTransfer records for the blob.
    """
    if _use_database_service():
        return _grpc_get_blob_transfers_for_blob(blob_uuid)
    return _direct_get_blob_transfers_for_blob(blob_uuid)


def update_blob_transfer(
    source_node: str,
    transfer_name: str,
    server_state: Optional[str] = None,
    port: Optional[int] = None,
    percentage: Optional[float] = None
) -> bool:
    """Update a blob transfer record.

    Args:
        source_node: The source node name.
        transfer_name: The transfer name.
        server_state: Optional new server state.
        port: Optional new port number.
        percentage: Optional new percentage.

    Returns:
        True if updated, False on error.
    """
    if _use_database_service():
        return _grpc_update_blob_transfer(
            source_node, transfer_name, server_state, port, percentage)
    return _direct_update_blob_transfer(
        source_node, transfer_name, server_state, port, percentage)


def delete_blob_transfer(source_node: str, transfer_name: str) -> bool:
    """Delete a blob transfer record.

    Args:
        source_node: The source node name.
        transfer_name: The transfer name.

    Returns:
        True if deleted (or didn't exist), False on error.
    """
    if _use_database_service():
        return _grpc_delete_blob_transfer(source_node, transfer_name)
    return _direct_delete_blob_transfer(source_node, transfer_name)


def delete_stale_transfers(max_age: float) -> int:
    """Delete stale transfers.

    Used by scheduled cleanup tasks to remove abandoned transfers.

    Args:
        max_age: Maximum age in seconds. Transfers not updated in this
            many seconds are deleted.

    Returns:
        Number of deleted records.
    """
    if _use_database_service():
        return _grpc_delete_stale_transfers(max_age)
    older_than = time.time() - max_age
    return _direct_delete_stale_transfers(older_than)


def delete_blob_transfers_for_blob(blob_uuid: str) -> bool:
    """Delete all transfer records for a blob.

    Used by Blob.hard_delete() to clean up any pending transfers.
    Uses bulk SQL DELETE for efficiency.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        True if deleted successfully (or no records exist), False on error.
    """
    if _use_database_service():
        result = _grpc_delete_blob_transfers_for_blob(blob_uuid)
    else:
        result = _direct_delete_blob_transfers_for_blob(blob_uuid)
    # result is -1 on error, >= 0 (count of deleted records) on success
    return result >= 0


# =============================================================================
# Blob Attributes Direct Access Functions
# These store mutable blob attributes, separate from static values.
# =============================================================================

def _direct_create_blob_attributes(data: BlobAttributesData) -> bool:
    """Create a blob_attributes record in MariaDB.

    This is typically called when a blob is first observed on a node,
    after the blob static values are created in the blobs table.

    Args:
        data: The BlobAttributesData to insert.

    Returns:
        True if the record was created, False if it already exists or error.
    """
    engine = _get_engine()
    table = _get_blob_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                size=data.size,
                info=data.info,
                last_used=data.last_used,
                expires_at=data.expires_at
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB create failed for blob_attributes {data.uuid}: {e}')
        return False


def _direct_get_blob_attributes(
    blob_uuid: UUID
) -> Optional[BlobAttributesData]:
    """Get blob attributes from MariaDB.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        BlobAttributesData if found, None otherwise.
    """
    engine = _get_engine()
    table = _get_blob_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == blob_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return BlobAttributesData(
                uuid=result.uuid,
                size=result.size,
                info=result.info if result.info else {},
                last_used=result.last_used,
                expires_at=result.expires_at
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for blob_attributes {blob_uuid}: {e}')
        return None


def _direct_update_blob_attributes(data: BlobAttributesData) -> bool:
    """Update blob attributes in MariaDB.

    Args:
        data: The BlobAttributesData with updated values.

    Returns:
        True if a row was updated, False otherwise.
    """
    engine = _get_engine()
    table = _get_blob_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                size=data.size,
                info=data.info,
                last_used=data.last_used,
                expires_at=data.expires_at
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update failed for blob_attributes {data.uuid}: {e}')
        return False


def _direct_update_blob_last_used(blob_uuid: UUID, last_used: float) -> bool:
    """Update only the last_used attribute (optimized for frequent updates).

    This avoids full row reads/writes for the common case of recording usage.

    Args:
        blob_uuid: The UUID of the blob.
        last_used: The new last_used timestamp.

    Returns:
        True if a row was updated, False otherwise.
    """
    engine = _get_engine()
    table = _get_blob_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == blob_uuid
            ).values(last_used=last_used)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update last_used failed for {blob_uuid}: {e}')
        return False


def _direct_delete_blob_attributes(blob_uuid: UUID) -> bool:
    """Delete blob attributes from MariaDB.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        True if a row was deleted, False otherwise.
    """
    engine = _get_engine()
    table = _get_blob_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == blob_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for blob_attributes {blob_uuid}: {e}')
        return False


def _direct_get_expired_blob_uuids(
        current_time: Optional[float] = None) -> list[str]:
    """Get UUIDs of blobs that have expired.

    Returns blobs where expires_at > 0 (has expiration) AND
    expires_at < current_time (past expiration).

    This pushes filtering to the database, avoiding loading each blob.

    Args:
        current_time: The current time to compare against. Defaults to now.

    Returns:
        List of blob UUID strings that have expired.
    """
    if current_time is None:
        current_time = time.time()

    active_states = ['initial', 'created']

    engine = _get_engine()
    attrs_table = _get_blob_attributes_table()
    states_table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(attrs_table.c.uuid).select_from(
                attrs_table.join(
                    states_table,
                    sa.and_(
                        sa.func.replace(
                            states_table.c.object_uuid, '-', '')
                        == attrs_table.c.uuid,
                        states_table.c.object_type == ObjectType.BLOB
                    )
                )
            ).where(
                sa.and_(
                    attrs_table.c.expires_at > 0,
                    attrs_table.c.expires_at < current_time,
                    states_table.c.state_value.in_(active_states)
                )
            )
            result = conn.execute(stmt)
            return [str(row.uuid) for row in result]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query for expired blobs failed: {e}')
        return []


def _direct_get_stale_transcoded_blob_uuids(idle_seconds: float) -> list[str]:
    """Get UUIDs of transcoded blobs not used recently.

    Returns blobs that:
    1. Have transcodes (exist in object_references as TRANSCODE source)
    2. Have last_used older than (now - idle_seconds)

    This pushes filtering to the database, avoiding loading each blob.

    Args:
        idle_seconds: Maximum seconds since last use.

    Returns:
        List of blob UUID strings that are stale transcodes.
    """
    cutoff_time = time.time() - idle_seconds
    active_states = ['initial', 'created']

    engine = _get_engine()
    attrs_table = _get_blob_attributes_table()
    refs_table = _get_object_references_table()
    states_table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            # Subquery: blob UUIDs that have transcodes
            transcoded_blobs = sa.select(
                refs_table.c.source_uuid
            ).where(
                sa.and_(
                    refs_table.c.source_object_type
                    == str(ObjectType.BLOB),
                    refs_table.c.relationship
                    == str(RelationshipType.TRANSCODE)
                )
            ).distinct()

            # Main query: transcoded blobs with stale last_used,
            # filtered to active states only
            stmt = sa.select(
                attrs_table.c.uuid
            ).select_from(
                attrs_table.join(
                    states_table,
                    sa.and_(
                        sa.func.replace(
                            states_table.c.object_uuid, '-', '')
                        == attrs_table.c.uuid,
                        states_table.c.object_type
                        == ObjectType.BLOB
                    )
                )
            ).where(
                sa.and_(
                    attrs_table.c.uuid.in_(transcoded_blobs),
                    sa.or_(
                        attrs_table.c.last_used.is_(None),
                        attrs_table.c.last_used < cutoff_time
                    ),
                    states_table.c.state_value.in_(
                        active_states)
                )
            )
            result = conn.execute(stmt)
            return [str(row.uuid) for row in result]
    except OperationalError as e:
        LOG.warning(
            'MariaDB query for stale transcoded blobs '
            f'failed: {e}')
        return []


# =============================================================================
# Blob Attributes gRPC Client Functions
# These call the database microservice for blob attribute operations.
# =============================================================================

def _grpc_create_blob_attributes(data: BlobAttributesData) -> bool:
    """Create blob attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateBlobAttributesRequest(
            data=database_pb2.BlobAttributesData(
                uuid=str(data.uuid),
                size=data.size,
                info_json=json.dumps(data.info) if data.info else '{}',
                last_used=data.last_used if data.last_used is not None else 0,
                has_last_used=data.last_used is not None,
                expires_at=data.expires_at
            )
        )
        reply = _grpc_call(stub.CreateBlobAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC CreateBlobAttributes failed for {data.uuid}: {e}')
        return False


def _grpc_get_blob_attributes(blob_uuid: UUID) -> Optional[BlobAttributesData]:
    """Get blob attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetBlobAttributesRequest(uuid=str(blob_uuid))
        reply = _grpc_call(stub.GetBlobAttributes, request)
        if not reply.found:
            return None
        d = reply.data
        return BlobAttributesData(
            uuid=UUID(d.uuid),
            size=d.size,
            info=json.loads(d.info_json) if d.info_json else {},
            last_used=d.last_used if d.has_last_used else None,
            expires_at=d.expires_at
        )
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetBlobAttributes failed for {blob_uuid}: {e}')
        return None


def _grpc_update_blob_attributes(data: BlobAttributesData) -> bool:
    """Update blob attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateBlobAttributesRequest(
            data=database_pb2.BlobAttributesData(
                uuid=str(data.uuid),
                size=data.size,
                info_json=json.dumps(data.info) if data.info else '{}',
                last_used=data.last_used if data.last_used is not None else 0,
                has_last_used=data.last_used is not None,
                expires_at=data.expires_at
            )
        )
        reply = _grpc_call(stub.UpdateBlobAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpdateBlobAttributes failed for {data.uuid}: {e}')
        return False


def _grpc_update_blob_last_used(blob_uuid: UUID, last_used: float) -> bool:
    """Update blob last_used via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateBlobLastUsedRequest(
            uuid=str(blob_uuid),
            last_used=last_used
        )
        reply = _grpc_call(stub.UpdateBlobLastUsed, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpdateBlobLastUsed failed for {blob_uuid}: {e}')
        return False


def _grpc_delete_blob_attributes(blob_uuid: UUID) -> bool:
    """Delete blob attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteBlobAttributesRequest(uuid=str(blob_uuid))
        reply = _grpc_call(stub.DeleteBlobAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteBlobAttributes failed for {blob_uuid}: {e}')
        return False


def _grpc_get_expired_blob_uuids(
        current_time: Optional[float] = None) -> list[str]:
    """Get expired blob UUIDs via the database microservice."""
    if current_time is None:
        current_time = time.time()
    try:
        stub = _get_database_stub()
        request = database_pb2.GetExpiredBlobUuidsRequest(
            current_time=current_time
        )
        reply = _grpc_call(stub.GetExpiredBlobUuids, request)
        return list(reply.uuids)
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetExpiredBlobUuids failed: {e}')
        return []


def _grpc_get_stale_transcoded_blob_uuids(idle_seconds: float) -> list[str]:
    """Get stale transcoded blob UUIDs via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetStaleTranscodedBlobUuidsRequest(
            idle_seconds=idle_seconds
        )
        reply = _grpc_call(stub.GetStaleTranscodedBlobUuids, request)
        return list(reply.uuids)
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetStaleTranscodedBlobUuids failed: {e}')
        return []


# =============================================================================
# Blob Attributes Public API Functions
# =============================================================================

def create_blob_attributes(data: BlobAttributesData) -> bool:
    """Create blob attributes record.

    Args:
        data: The BlobAttributesData to create.

    Returns:
        True if created successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_create_blob_attributes(data)
    return _direct_create_blob_attributes(data)


def get_blob_attributes(blob_uuid: UUID) -> Optional[BlobAttributesData]:
    """Get blob attributes.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        BlobAttributesData if found, None otherwise.
    """
    if _use_database_service():
        return _grpc_get_blob_attributes(blob_uuid)
    return _direct_get_blob_attributes(blob_uuid)


def update_blob_attributes(data: BlobAttributesData) -> bool:
    """Update blob attributes.

    Args:
        data: The BlobAttributesData with updated values.

    Returns:
        True if updated successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_update_blob_attributes(data)
    return _direct_update_blob_attributes(data)


def update_blob_last_used(blob_uuid: UUID, last_used: float) -> bool:
    """Update only the last_used attribute (optimized for frequent updates).

    Args:
        blob_uuid: The UUID of the blob.
        last_used: The new last_used timestamp.

    Returns:
        True if updated successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_update_blob_last_used(blob_uuid, last_used)
    return _direct_update_blob_last_used(blob_uuid, last_used)


def delete_blob_attributes(blob_uuid: UUID) -> bool:
    """Delete blob attributes.

    Args:
        blob_uuid: The UUID of the blob.

    Returns:
        True if deleted successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_blob_attributes(blob_uuid)
    return _direct_delete_blob_attributes(blob_uuid)


def get_expired_blob_uuids(current_time: Optional[float] = None) -> list[str]:
    """Get UUIDs of blobs that have expired.

    Returns blobs where expires_at > 0 (has expiration) AND
    expires_at < current_time (past expiration).

    Args:
        current_time: The current time to compare against. Defaults to now.

    Returns:
        List of blob UUID strings that have expired.
    """
    if _use_database_service():
        return _grpc_get_expired_blob_uuids(current_time)
    return _direct_get_expired_blob_uuids(current_time)


def get_stale_transcoded_blob_uuids(idle_seconds: float) -> list[str]:
    """Get UUIDs of transcoded blobs not used recently.

    Returns blobs that:
    1. Have transcodes (exist in object_references as TRANSCODE source)
    2. Have last_used older than (now - idle_seconds)

    Args:
        idle_seconds: Maximum seconds since last use.

    Returns:
        List of blob UUID strings that are stale transcodes.
    """
    if _use_database_service():
        return _grpc_get_stale_transcoded_blob_uuids(idle_seconds)
    return _direct_get_stale_transcoded_blob_uuids(idle_seconds)


# =============================================================================
# Node Operations (MariaDB)
# =============================================================================

def _get_nodes_table() -> sa.Table:
    """Get or create the nodes table definition."""
    global _nodes_table
    if _nodes_table is None:
        metadata = _get_metadata()
        _nodes_table = pydantic_to_sqlalchemy_table(
            NodeData,
            'nodes',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _nodes_table


def _get_node_attributes_table() -> sa.Table:
    """Get or create the node_attributes table definition."""
    global _node_attributes_table
    if _node_attributes_table is None:
        metadata = _get_metadata()
        _node_attributes_table = sa.Table(
            'node_attributes',
            metadata,
            sa.Column('uuid', sa.Uuid(), primary_key=True),
            sa.Column(
                'last_seen', sa.Double(),
                nullable=False, default=0.0
            ),
            sa.Column(
                'installed_version', sa.String(255),
                nullable=True
            ),
            sa.Column(
                'is_etcd_master', sa.Boolean(),
                nullable=False, default=False
            ),
            sa.Column(
                'is_hypervisor', sa.Boolean(),
                nullable=False, default=False
            ),
            sa.Column(
                'is_network_node', sa.Boolean(),
                nullable=False, default=False
            ),
            sa.Column(
                'is_eventlog_node', sa.Boolean(),
                nullable=False, default=False
            ),
            sa.Column('instances', sa.JSON(), nullable=True),
            sa.Column('daemons', sa.JSON(), nullable=True),
            sa.Column('daemon_states', sa.JSON(), nullable=True),
            sa.Column('qemu_version', sa.JSON(), nullable=True),
            sa.Column(
                'libvirt_version', sa.JSON(), nullable=True
            ),
            sa.Column(
                'python_version', sa.JSON(), nullable=True
            ),
            sa.Column(
                'python_implementation', sa.String(255),
                nullable=True
            ),
            sa.Column(
                'dependency_versions', sa.JSON(), nullable=True
            ),
            sa.Column(
                'process_metrics', sa.JSON(), nullable=True
            ),
            sa.Index(
                'idx_node_attrs_last_seen', 'last_seen'
            ),
        )
    return _node_attributes_table


def _ensure_nodes_schema(
    engine: sa.Engine,
) -> dict[str, Any]:
    """Ensure the nodes table schema is up to date."""
    table_name = 'nodes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_nodes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True
        )

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(
                        f'Index {idx.name} creation '
                        f'skipped: {e}'
                    )

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NODES_VERSION,
        'migrated': start_ver != current_ver
    }


def _ensure_node_attributes_schema(
    engine: sa.Engine,
) -> dict[str, Any]:
    """Ensure the node_attributes table schema is up to date."""
    table_name = 'node_attributes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_node_attributes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True
        )

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(
                        f'Index {idx.name} creation '
                        f'skipped: {e}'
                    )

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NODE_ATTRIBUTES_VERSION,
        'migrated': start_ver != current_ver
    }


def _get_node_daemon_states_table() -> sa.Table:
    """Get or create the node_daemon_states table definition.

    One row per (node_uuid, daemon). Replaces the daemon_states JSON
    dict that used to live inside node_attributes; that dict required a
    coarse per-node lock for every transition, which serialised every
    daemon's startup and shutdown through a single 10s-timeout lock and
    caused tail acquisitions to fail under load.
    """
    global _node_daemon_states_table
    if _node_daemon_states_table is None:
        with TABLE_CREATION_LOCK:
            if _node_daemon_states_table is not None:
                return _node_daemon_states_table
            metadata = _get_metadata()
            if 'node_daemon_states' in metadata.tables:
                _node_daemon_states_table = (
                    metadata.tables['node_daemon_states'])
                return _node_daemon_states_table
            _node_daemon_states_table = sa.Table(
                'node_daemon_states',
                metadata,
                sa.Column('node_uuid', sa.Uuid(), nullable=False),
                sa.Column('daemon', sa.String(32), nullable=False),
                sa.Column('value', sa.String(32), nullable=True),
                sa.Column(
                    'update_time', sa.Double(), nullable=False, default=0.0),
                sa.Column('message', sa.String(255), nullable=True),
                sa.PrimaryKeyConstraint('node_uuid', 'daemon'),
                sa.Index(
                    'idx_node_daemon_states_daemon_value', 'daemon', 'value'),
            )
    return _node_daemon_states_table


def _migrate_daemon_states_from_node_attributes(
    engine: sa.Engine,
) -> tuple[int, int]:
    """Copy daemon_states JSON entries from node_attributes into node_daemon_states.

    Idempotent: uses INSERT ... ON DUPLICATE KEY UPDATE so re-running on
    an already-migrated cluster is a no-op. The JSON column is left in
    place; it is dropped in a later schema bump once nothing reads it.

    Returns ``(migrated, errors)``.
    """
    src = _get_node_attributes_table()
    dst = _get_node_daemon_states_table()
    migrated = 0
    errors = 0

    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(src.c.uuid, src.c.daemon_states)
        ).fetchall()

        for row in rows:
            ds = row.daemon_states or {}
            if not isinstance(ds, dict):
                continue
            for daemon_name, payload in ds.items():
                if not isinstance(payload, dict):
                    errors += 1
                    continue
                try:
                    stmt = sa.dialects.mysql.insert(dst).values(
                        node_uuid=row.uuid,
                        daemon=daemon_name,
                        value=payload.get('value'),
                        update_time=payload.get('update_time') or 0.0,
                        message=payload.get('message'),
                    )
                    stmt = stmt.on_duplicate_key_update(
                        value=payload.get('value'),
                        update_time=payload.get('update_time') or 0.0,
                        message=payload.get('message'),
                    )
                    conn.execute(stmt)
                    migrated += 1
                except Exception as e:
                    LOG.warning(
                        f'Failed to migrate daemon_state '
                        f'{row.uuid}/{daemon_name}: {e}')
                    errors += 1
        conn.commit()

    return migrated, errors


def _ensure_node_daemon_states_schema(
    engine: sa.Engine,
) -> dict[str, Any]:
    """Ensure the node_daemon_states table schema is up to date."""
    table_name = 'node_daemon_states'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_node_daemon_states_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver < 2:
        # Migrate any pre-existing daemon_states JSON content from
        # node_attributes. Skipped automatically on a fresh cluster
        # because there is nothing to read.
        LOG.info(
            f'Migrating daemon_states JSON into {table_name} (version 2)')
        migrated, errors = _migrate_daemon_states_from_node_attributes(engine)
        LOG.info(
            f'{table_name} migration: {migrated} row(s) copied, '
            f'{errors} error(s)')
        current_ver = 2
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NODE_DAEMON_STATES_VERSION,
        'migrated': start_ver != current_ver
    }


# --- Direct node access functions ---

def _direct_create_node(
    node_uuid: UUID, fqdn: str, ip: str, version: int
) -> bool:
    """Create a node record in MariaDB."""
    engine = _get_engine()
    table = _get_nodes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=node_uuid,
                fqdn=fqdn,
                ip=ip,
                version=version
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            'MariaDB create failed for node '
            f'{node_uuid}: {e}'
        )
        return False


def _direct_get_node(
    node_uuid: UUID,
) -> Optional[NodeData]:
    """Get node static values from MariaDB."""
    engine = _get_engine()
    table = _get_nodes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.uuid == node_uuid
            )
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return NodeData(
                uuid=result.uuid,
                fqdn=result.fqdn,
                ip=result.ip,
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(
            'MariaDB get failed for node '
            f'{node_uuid}: {e}'
        )
        return None


def _direct_get_node_by_fqdn(
    fqdn: str,
) -> Optional[NodeData]:
    """Get node static values by FQDN from MariaDB."""
    engine = _get_engine()
    table = _get_nodes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.fqdn == fqdn
            )
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return NodeData(
                uuid=result.uuid,
                fqdn=result.fqdn,
                ip=result.ip,
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(
            'MariaDB get by fqdn failed for node '
            f'{fqdn}: {e}'
        )
        return None


def _direct_get_all_node_uuids() -> list[str]:
    """Get all node UUIDs from MariaDB."""
    engine = _get_engine()
    table = _get_nodes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table.c.uuid)
            result = conn.execute(stmt).fetchall()
            return [str(row.uuid) for row in result]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for node UUIDs: {e}'
        )
        return []


def _direct_delete_node(node_uuid: UUID) -> bool:
    """Delete a node record from MariaDB."""
    engine = _get_engine()
    table = _get_nodes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.uuid == node_uuid
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            'MariaDB delete failed for node '
            f'{node_uuid}: {e}'
        )
        return False


def _direct_update_node(data: NodeData) -> bool:
    """Update a node record in MariaDB."""
    engine = _get_engine()
    table = _get_nodes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                fqdn=data.fqdn,
                ip=data.ip,
                version=data.version
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            'MariaDB update failed for node '
            f'{data.uuid}: {e}'
        )
        return False


# --- Direct node attributes access functions ---

def _direct_create_node_attributes(
    data: NodeAttributesData,
) -> bool:
    """Create a node_attributes record in MariaDB."""
    engine = _get_engine()
    table = _get_node_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                last_seen=data.last_seen,
                installed_version=data.installed_version,
                is_etcd_master=data.is_etcd_master,
                is_hypervisor=data.is_hypervisor,
                is_network_node=data.is_network_node,
                is_eventlog_node=data.is_eventlog_node,
                instances=data.instances,
                daemons=data.daemons,
                daemon_states=data.daemon_states,
                qemu_version=data.qemu_version,
                libvirt_version=data.libvirt_version,
                python_version=data.python_version,
                python_implementation=(
                    data.python_implementation
                ),
                dependency_versions=(
                    data.dependency_versions
                ),
                process_metrics=data.process_metrics,
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            'MariaDB create failed for '
            f'node_attributes {data.uuid}: {e}'
        )
        return False


def _direct_get_node_attributes(
    node_uuid: UUID,
) -> Optional[NodeAttributesData]:
    """Get node attributes from MariaDB."""
    engine = _get_engine()
    table = _get_node_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.uuid == node_uuid
            )
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return NodeAttributesData(
                uuid=result.uuid,
                last_seen=result.last_seen,
                installed_version=result.installed_version,
                is_etcd_master=result.is_etcd_master,
                is_hypervisor=result.is_hypervisor,
                is_network_node=result.is_network_node,
                is_eventlog_node=result.is_eventlog_node,
                instances=(
                    result.instances
                    if result.instances else []
                ),
                daemons=(
                    result.daemons
                    if result.daemons else []
                ),
                daemon_states=(
                    result.daemon_states
                    if result.daemon_states else {}
                ),
                qemu_version=result.qemu_version,
                libvirt_version=result.libvirt_version,
                python_version=result.python_version,
                python_implementation=(
                    result.python_implementation
                ),
                dependency_versions=(
                    result.dependency_versions
                    if result.dependency_versions else {}
                ),
                process_metrics=(
                    result.process_metrics
                    if result.process_metrics else {}
                ),
            )
    except OperationalError as e:
        LOG.warning(
            'MariaDB get failed for '
            f'node_attributes {node_uuid}: {e}'
        )
        return None


def _direct_update_node_attributes(
    data: NodeAttributesData,
) -> bool:
    """Update node attributes in MariaDB."""
    engine = _get_engine()
    table = _get_node_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                last_seen=data.last_seen,
                installed_version=data.installed_version,
                is_etcd_master=data.is_etcd_master,
                is_hypervisor=data.is_hypervisor,
                is_network_node=data.is_network_node,
                is_eventlog_node=data.is_eventlog_node,
                instances=data.instances,
                daemons=data.daemons,
                daemon_states=data.daemon_states,
                qemu_version=data.qemu_version,
                libvirt_version=data.libvirt_version,
                python_version=data.python_version,
                python_implementation=(
                    data.python_implementation
                ),
                dependency_versions=(
                    data.dependency_versions
                ),
                process_metrics=data.process_metrics,
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            'MariaDB update failed for '
            f'node_attributes {data.uuid}: {e}'
        )
        return False


def _direct_delete_node_attributes(
    node_uuid: UUID,
) -> bool:
    """Delete node attributes from MariaDB."""
    engine = _get_engine()
    table = _get_node_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.uuid == node_uuid
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            'MariaDB delete failed for '
            f'node_attributes {node_uuid}: {e}'
        )
        return False


# --- Direct node daemon state access functions ---

def _direct_set_node_daemon_state(
    node_uuid: UUID, daemon: str, value: Optional[str],
    update_time: float, message: Optional[str],
) -> bool:
    """Atomically upsert one (node, daemon) state row.

    No Python-side locking: the composite primary key
    ``(node_uuid, daemon)`` and ``INSERT ... ON DUPLICATE KEY UPDATE``
    give us per-daemon isolation directly at the SQL layer.

    A delayed call (e.g. a state transition queued behind a slow
    refresh) must not overwrite a fresher state with an older
    timestamp -- callers stamp ``update_time`` from
    ``time.time()`` so per-node clock skew can let two writes arrive
    out-of-order. Use ``GREATEST`` on the timestamp and gate the
    value/message rewrites with the same comparison so the latest
    write always wins.
    """
    engine = _get_engine()
    table = _get_node_daemon_states_table()

    try:
        with engine.connect() as conn:
            stmt = sa.dialects.mysql.insert(table).values(
                node_uuid=node_uuid,
                daemon=daemon,
                value=value,
                update_time=update_time,
                message=message,
            )
            stmt = stmt.on_duplicate_key_update(
                value=sa.case(
                    (table.c.update_time <= update_time, stmt.inserted.value),
                    else_=table.c.value),
                message=sa.case(
                    (table.c.update_time <= update_time,
                     stmt.inserted.message),
                    else_=table.c.message),
                update_time=sa.func.greatest(
                    table.c.update_time, stmt.inserted.update_time),
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB write failed for node_daemon_states '
            f'{node_uuid}/{daemon}: {e}')
        return False


def _direct_get_node_daemon_state(
    node_uuid: UUID, daemon: str,
) -> Optional[NodeDaemonStateData]:
    """Read one (node, daemon) state row, or None if absent."""
    engine = _get_engine()
    table = _get_node_daemon_states_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                sa.and_(
                    table.c.node_uuid == node_uuid,
                    table.c.daemon == daemon,
                )
            )
            result = conn.execute(stmt).fetchone()
            if result is None:
                return None
            return NodeDaemonStateData(
                node_uuid=result.node_uuid,
                daemon=result.daemon,
                value=result.value,
                update_time=result.update_time,
                message=result.message,
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB read failed for node_daemon_states '
            f'{node_uuid}/{daemon}: {e}')
        return None


def _direct_get_all_node_daemon_states(
    node_uuid: UUID,
) -> Optional[list[NodeDaemonStateData]]:
    """Read every daemon state row for one node.

    Returns ``None`` on a database error, ``[]`` for no rows.
    """
    engine = _get_engine()
    table = _get_node_daemon_states_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.node_uuid == node_uuid)
            results = conn.execute(stmt).fetchall()
            return [
                NodeDaemonStateData(
                    node_uuid=row.node_uuid,
                    daemon=row.daemon,
                    value=row.value,
                    update_time=row.update_time,
                    message=row.message,
                )
                for row in results
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB read failed for node_daemon_states {node_uuid}: {e}')
        return None


def _direct_delete_node_daemon_state(
    node_uuid: UUID, daemon: str,
) -> bool:
    """Delete one (node, daemon) state row. Returns True if absent or removed."""
    engine = _get_engine()
    table = _get_node_daemon_states_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.node_uuid == node_uuid,
                    table.c.daemon == daemon,
                )
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for node_daemon_states '
            f'{node_uuid}/{daemon}: {e}')
        return False


# --- gRPC node client functions ---

def _grpc_create_node(
    node_uuid: UUID, fqdn: str, ip: str, version: int
) -> bool:
    """Create a node record via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateNodeRequest(
            node=database_pb2.NodeStaticData(
                uuid=str(node_uuid),
                fqdn=fqdn,
                ip=ip,
                version=version
            )
        )
        reply = _grpc_call(stub.CreateNode, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateNode failed for {node_uuid}: {e}'
        )
        return False


def _grpc_get_node(
    node_uuid: UUID,
) -> Optional[NodeData]:
    """Get node static values via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNodeRequest(
            uuid=str(node_uuid)
        )
        reply = _grpc_call(stub.GetNode, request)
        if not reply.found:
            return None
        return NodeData(
            uuid=reply.node.uuid,
            fqdn=reply.node.fqdn,
            ip=reply.node.ip,
            version=reply.node.version
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetNode failed for {node_uuid}: {e}'
        )
        return None


def _grpc_get_node_by_fqdn(
    fqdn: str,
) -> Optional[NodeData]:
    """Get node static values by FQDN via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNodeByFqdnRequest(
            fqdn=fqdn
        )
        reply = _grpc_call(stub.GetNodeByFqdn, request)
        if not reply.found:
            return None
        return NodeData(
            uuid=reply.node.uuid,
            fqdn=reply.node.fqdn,
            ip=reply.node.ip,
            version=reply.node.version
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetNodeByFqdn failed for {fqdn}: {e}'
        )
        return None


def _grpc_get_all_node_uuids() -> list[str]:
    """Get all node UUIDs via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllNodeUuidsRequest()
        reply = _grpc_call(stub.GetAllNodeUuids, request)
        return list(reply.uuids)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetAllNodeUuids failed: {e}'
        )
        return []


def _grpc_delete_node(node_uuid: UUID) -> bool:
    """Delete a node record via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNodeRequest(
            uuid=str(node_uuid)
        )
        reply = _grpc_call(stub.DeleteNode, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteNode failed for {node_uuid}: {e}'
        )
        return False


def _grpc_update_node(data: NodeData) -> bool:
    """Update a node record via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateNodeRequest(
            node=database_pb2.NodeStaticData(
                uuid=str(data.uuid),
                fqdn=data.fqdn,
                ip=data.ip,
                version=data.version
            )
        )
        reply = _grpc_call(stub.UpdateNode, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC UpdateNode failed for {data.uuid}: {e}'
        )
        return False


# --- gRPC node attributes client functions ---

def _grpc_create_node_attributes(
    data: NodeAttributesData,
) -> bool:
    """Create node attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateNodeAttributesRequest(
            data=_node_attrs_to_proto(data)
        )
        reply = _grpc_call(stub.CreateNodeAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            'gRPC CreateNodeAttributes failed for '
            f'{data.uuid}: {e}'
        )
        return False


def _grpc_get_node_attributes(
    node_uuid: UUID,
) -> Optional[NodeAttributesData]:
    """Get node attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNodeAttributesRequest(
            uuid=str(node_uuid)
        )
        reply = _grpc_call(stub.GetNodeAttributes, request)
        if not reply.found:
            return None
        return _node_attrs_from_proto(reply.data)
    except grpc.RpcError as e:
        LOG.error(
            'gRPC GetNodeAttributes failed for '
            f'{node_uuid}: {e}'
        )
        return None


def _grpc_update_node_attributes(
    data: NodeAttributesData,
) -> bool:
    """Update node attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateNodeAttributesRequest(
            data=_node_attrs_to_proto(data)
        )
        reply = _grpc_call(stub.UpdateNodeAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            'gRPC UpdateNodeAttributes failed for '
            f'{data.uuid}: {e}'
        )
        return False


def _grpc_delete_node_attributes(
    node_uuid: UUID,
) -> bool:
    """Delete node attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNodeAttributesRequest(
            uuid=str(node_uuid)
        )
        reply = _grpc_call(stub.DeleteNodeAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            'gRPC DeleteNodeAttributes failed for '
            f'{node_uuid}: {e}'
        )
        return False


# --- Proto conversion helpers for node attributes ---

def _node_attrs_to_proto(
    data: NodeAttributesData,
) -> database_pb2.NodeAttributesProto:
    """Convert NodeAttributesData to proto message."""
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
        daemon_states_json=json.dumps(
            data.daemon_states
        ),
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
        dependency_versions_json=json.dumps(
            data.dependency_versions
        ),
        process_metrics_json=json.dumps(
            data.process_metrics
        ),
    )


def _node_attrs_from_proto(
    d: database_pb2.NodeAttributesProto,
) -> NodeAttributesData:
    """Convert proto NodeAttributesProto to Pydantic model."""
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


# --- Public node API functions ---

def create_node(
    node_uuid: UUID, fqdn: str, ip: str, version: int
) -> bool:
    """Create a node record.

    Args:
        node_uuid: The UUID of the node.
        fqdn: The node's fully qualified domain name.
        ip: The node's mesh network IP address.
        version: The object version number.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_node(
            node_uuid, fqdn, ip, version
        )
    return _direct_create_node(
        node_uuid, fqdn, ip, version
    )


def get_node(node_uuid: UUID) -> Optional[NodeData]:
    """Get node static values.

    Args:
        node_uuid: The UUID of the node.

    Returns:
        A NodeData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_node(node_uuid)
    return _direct_get_node(node_uuid)


def get_node_by_fqdn(fqdn: str) -> Optional[NodeData]:
    """Get node static values by FQDN.

    Args:
        fqdn: The node's fully qualified domain name.

    Returns:
        A NodeData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_node_by_fqdn(fqdn)
    return _direct_get_node_by_fqdn(fqdn)


def get_all_node_uuids() -> list[str]:
    """Get all node UUIDs.

    Returns:
        List of node UUID strings.
    """
    if _use_database_service():
        return _grpc_get_all_node_uuids()
    return _direct_get_all_node_uuids()


def delete_node(node_uuid: UUID) -> bool:
    """Delete a node record.

    Args:
        node_uuid: The UUID of the node.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_node(node_uuid)
    return _direct_delete_node(node_uuid)


def update_node(data: NodeData) -> bool:
    """Update a node record.

    Args:
        data: The NodeData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_node(data)
    return _direct_update_node(data)


def create_node_attributes(
    data: NodeAttributesData,
) -> bool:
    """Create node attributes record.

    Args:
        data: The NodeAttributesData to create.

    Returns:
        True if created successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_create_node_attributes(data)
    return _direct_create_node_attributes(data)


def get_node_attributes(
    node_uuid: UUID,
) -> Optional[NodeAttributesData]:
    """Get node attributes.

    Args:
        node_uuid: The UUID of the node.

    Returns:
        NodeAttributesData if found, None otherwise.
    """
    if _use_database_service():
        return _grpc_get_node_attributes(node_uuid)
    return _direct_get_node_attributes(node_uuid)


def update_node_attributes(
    data: NodeAttributesData,
) -> bool:
    """Update node attributes.

    Args:
        data: The NodeAttributesData with updated values.

    Returns:
        True if updated successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_update_node_attributes(data)
    return _direct_update_node_attributes(data)


def delete_node_attributes(
    node_uuid: UUID,
) -> bool:
    """Delete node attributes.

    Args:
        node_uuid: The UUID of the node.

    Returns:
        True if deleted successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_node_attributes(node_uuid)
    return _direct_delete_node_attributes(node_uuid)


# --- gRPC node daemon state client functions ---

def _grpc_set_node_daemon_state(
    node_uuid: UUID, daemon: str, value: Optional[str],
    update_time: float, message: Optional[str],
) -> bool:
    """Atomically upsert one (node, daemon) state row via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.SetNodeDaemonStateRequest(
            data=database_pb2.NodeDaemonStateData(
                node_uuid=str(node_uuid),
                daemon=daemon,
                value=value or '',
                update_time=update_time,
                message=message or '',
            ),
        )
        reply = _grpc_call(stub.SetNodeDaemonState, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC SetNodeDaemonState failed for {node_uuid}/{daemon}: {e}')
        return False


def _grpc_get_node_daemon_state(
    node_uuid: UUID, daemon: str,
) -> Optional[NodeDaemonStateData]:
    """Read one (node, daemon) state row via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNodeDaemonStateRequest(
            node_uuid=str(node_uuid), daemon=daemon)
        reply = _grpc_call(stub.GetNodeDaemonState, request)
        if not reply.found:
            return None
        d = reply.data
        return NodeDaemonStateData(
            node_uuid=UUID(d.node_uuid),
            daemon=d.daemon,
            value=d.value if d.value else None,
            update_time=d.update_time,
            message=d.message if d.message else None,
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetNodeDaemonState failed for {node_uuid}/{daemon}: {e}')
        return None


def _grpc_get_all_node_daemon_states(
    node_uuid: UUID,
) -> Optional[list[NodeDaemonStateData]]:
    """Read every daemon state row for one node via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllNodeDaemonStatesRequest(
            node_uuid=str(node_uuid))
        reply = _grpc_call(stub.GetAllNodeDaemonStates, request)
        return [
            NodeDaemonStateData(
                node_uuid=UUID(d.node_uuid),
                daemon=d.daemon,
                value=d.value if d.value else None,
                update_time=d.update_time,
                message=d.message if d.message else None,
            )
            for d in reply.data
        ]
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetAllNodeDaemonStates failed for {node_uuid}: {e}')
        return None


def _grpc_delete_node_daemon_state(
    node_uuid: UUID, daemon: str,
) -> bool:
    """Delete one (node, daemon) state row via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNodeDaemonStateRequest(
            node_uuid=str(node_uuid), daemon=daemon)
        reply = _grpc_call(stub.DeleteNodeDaemonState, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteNodeDaemonState failed for {node_uuid}/{daemon}: {e}')
        return False


# --- Public node daemon state API functions ---

def set_node_daemon_state(
    node_uuid: UUID, daemon: str, value: Optional[str],
    update_time: float, message: Optional[str] = None,
) -> bool:
    """Atomically upsert one (node, daemon) state row.

    No Python-level locking is required: the composite primary key
    ``(node_uuid, daemon)`` plus ``INSERT ... ON DUPLICATE KEY UPDATE``
    serialise concurrent writes for the same daemon at the SQL layer
    while leaving writes for different daemons fully parallel.
    """
    if _use_database_service():
        return _grpc_set_node_daemon_state(
            node_uuid, daemon, value, update_time, message)
    return _direct_set_node_daemon_state(
        node_uuid, daemon, value, update_time, message)


def get_node_daemon_state(
    node_uuid: UUID, daemon: str,
) -> Optional[NodeDaemonStateData]:
    """Read one (node, daemon) state row, or None if absent."""
    if _use_database_service():
        return _grpc_get_node_daemon_state(node_uuid, daemon)
    return _direct_get_node_daemon_state(node_uuid, daemon)


def get_all_node_daemon_states(
    node_uuid: UUID,
) -> Optional[list[NodeDaemonStateData]]:
    """Read every daemon state row for one node."""
    if _use_database_service():
        return _grpc_get_all_node_daemon_states(node_uuid)
    return _direct_get_all_node_daemon_states(node_uuid)


def delete_node_daemon_state(node_uuid: UUID, daemon: str) -> bool:
    """Delete one (node, daemon) state row."""
    if _use_database_service():
        return _grpc_delete_node_daemon_state(node_uuid, daemon)
    return _direct_delete_node_daemon_state(node_uuid, daemon)


# =============================================================================
# Namespace Table Definitions
# =============================================================================

_namespaces_table: Optional[sa.Table] = None
_namespace_attributes_table: Optional[sa.Table] = None


def _get_namespaces_table() -> sa.Table:
    """Get or create the namespaces table definition."""
    global _namespaces_table
    if _namespaces_table is None:
        metadata = _get_metadata()
        _namespaces_table = sa.Table(
            'namespaces',
            metadata,
            sa.Column(
                'name', sa.String(255), primary_key=True
            ),
            sa.Column(
                'version', sa.Integer(), nullable=False
            ),
        )
    return _namespaces_table


def _get_namespace_attributes_table() -> sa.Table:
    """Get or create the namespace_attributes table definition."""
    global _namespace_attributes_table
    if _namespace_attributes_table is None:
        metadata = _get_metadata()
        _namespace_attributes_table = sa.Table(
            'namespace_attributes',
            metadata,
            sa.Column(
                'name', sa.String(255), primary_key=True
            ),
            sa.Column('keys', sa.JSON(), nullable=True),
            sa.Column('trust', sa.JSON(), nullable=True),
        )
    return _namespace_attributes_table


def _ensure_namespaces_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the namespaces table schema is up to date."""
    table_name = 'namespaces'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_namespaces_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NAMESPACES_VERSION,
        'migrated': start_ver != current_ver
    }


def _ensure_namespace_attributes_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the namespace_attributes table schema is up to date."""
    table_name = 'namespace_attributes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_namespace_attributes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NAMESPACE_ATTRIBUTES_VERSION,
        'migrated': start_ver != current_ver
    }


# =============================================================================
# Namespace Direct Access Functions
# =============================================================================

def _direct_create_namespace(name: str, version: int) -> bool:
    """Create a namespace record in MariaDB."""
    engine = _get_engine()
    table = _get_namespaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(name=name, version=version)
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB create failed for namespace {name}: {e}')
        return False


def _direct_get_namespace(name: str) -> Optional[NamespaceData]:
    """Get namespace static values from MariaDB."""
    engine = _get_engine()
    table = _get_namespaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.name == name)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return NamespaceData(name=result.name, version=result.version)
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for namespace {name}: {e}')
        return None


def _direct_get_all_namespace_names() -> list[str]:
    """Get all namespace names from MariaDB."""
    engine = _get_engine()
    table = _get_namespaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table.c.name)
            result = conn.execute(stmt).fetchall()
            return [row.name for row in result]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for namespace names: {e}')
        return []


def _direct_delete_namespace(name: str) -> bool:
    """Delete a namespace record from MariaDB."""
    engine = _get_engine()
    table = _get_namespaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.name == name
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for namespace {name}: {e}')
        return False


# =============================================================================
# Namespace Attributes Direct Access Functions
# =============================================================================

def _direct_create_namespace_attributes(data: NamespaceAttributesData) -> bool:
    """Create a namespace_attributes record in MariaDB."""
    engine = _get_engine()
    table = _get_namespace_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(name=data.name, keys=data.keys, trust=data.trust)
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB create failed for namespace_attributes {data.name}: {e}')
        return False


def _direct_get_namespace_attributes(name: str) -> Optional[NamespaceAttributesData]:
    """Get namespace attributes from MariaDB."""
    engine = _get_engine()
    table = _get_namespace_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.name == name)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return NamespaceAttributesData(
                name=result.name,
                keys=result.keys if result.keys else {'nonced_keys': {}},
                trust=result.trust if result.trust else ['system'],
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for namespace_attributes {name}: {e}')
        return None


def _direct_update_namespace_attributes(data: NamespaceAttributesData) -> bool:
    """Update namespace attributes in MariaDB."""
    engine = _get_engine()
    table = _get_namespace_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(table.c.name == data.name).values(
                keys=data.keys, trust=data.trust)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update failed for namespace_attributes {data.name}: {e}')
        return False


def _direct_delete_namespace_attributes(name: str) -> bool:
    """Delete namespace attributes from MariaDB."""
    engine = _get_engine()
    table = _get_namespace_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.name == name)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for namespace_attributes {name}: {e}')
        return False


# =============================================================================
# Namespace gRPC Client Functions
# =============================================================================

def _grpc_create_namespace(name: str, version: int) -> bool:
    """Create a namespace record via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateNamespaceRequest(
            namespace=database_pb2.NamespaceStaticData(name=name, version=version))
        reply = _grpc_call(stub.CreateNamespace, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC CreateNamespace failed for {name}: {e}')
        return False


def _grpc_get_namespace(name: str) -> Optional[NamespaceData]:
    """Get namespace static values via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNamespaceRequest(name=name)
        reply = _grpc_call(stub.GetNamespace, request)
        if not reply.found:
            return None
        return NamespaceData(name=reply.namespace.name, version=reply.namespace.version)
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetNamespace failed for {name}: {e}')
        return None


def _grpc_get_all_namespace_names() -> list[str]:
    """Get all namespace names via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllNamespaceNamesRequest()
        reply = _grpc_call(stub.GetAllNamespaceNames, request)
        return list(reply.names)
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetAllNamespaceNames failed: {e}')
        return []


def _grpc_delete_namespace(name: str) -> bool:
    """Delete a namespace record via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNamespaceRequest(name=name)
        reply = _grpc_call(stub.DeleteNamespace, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteNamespace failed for {name}: {e}')
        return False


def _grpc_create_namespace_attributes(data: NamespaceAttributesData) -> bool:
    """Create namespace attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateNamespaceAttributesRequest(
            data=database_pb2.NamespaceAttributesProto(
                name=data.name,
                keys_json=json.dumps(data.keys),
                trust_json=json.dumps(data.trust)))
        reply = _grpc_call(stub.CreateNamespaceAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC CreateNamespaceAttributes failed for {data.name}: {e}')
        return False


def _grpc_get_namespace_attributes(name: str) -> Optional[NamespaceAttributesData]:
    """Get namespace attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNamespaceAttributesRequest(name=name)
        reply = _grpc_call(stub.GetNamespaceAttributes, request)
        if not reply.found:
            return None
        return NamespaceAttributesData(
            name=reply.data.name,
            keys=json.loads(reply.data.keys_json) if reply.data.keys_json else {'nonced_keys': {}},
            trust=json.loads(reply.data.trust_json) if reply.data.trust_json else ['system'],
        )
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetNamespaceAttributes failed for {name}: {e}')
        return None


def _grpc_update_namespace_attributes(data: NamespaceAttributesData) -> bool:
    """Update namespace attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateNamespaceAttributesRequest(
            data=database_pb2.NamespaceAttributesProto(
                name=data.name,
                keys_json=json.dumps(data.keys),
                trust_json=json.dumps(data.trust)))
        reply = _grpc_call(stub.UpdateNamespaceAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpdateNamespaceAttributes failed for {data.name}: {e}')
        return False


def _grpc_delete_namespace_attributes(name: str) -> bool:
    """Delete namespace attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNamespaceAttributesRequest(name=name)
        reply = _grpc_call(stub.DeleteNamespaceAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteNamespaceAttributes failed for {name}: {e}')
        return False


# =============================================================================
# Namespace Public API Functions
# =============================================================================

def create_namespace(name: str, version: int) -> bool:
    """Create a namespace record.

    Args:
        name: The namespace name (primary key).
        version: The object version number.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_namespace(name, version)
    return _direct_create_namespace(name, version)


def get_namespace(name: str) -> Optional[NamespaceData]:
    """Get namespace static values.

    Args:
        name: The namespace name.

    Returns:
        A NamespaceData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_namespace(name)
    return _direct_get_namespace(name)


def get_all_namespace_names() -> list[str]:
    """Get all namespace names.

    Returns:
        List of namespace name strings.
    """
    if _use_database_service():
        return _grpc_get_all_namespace_names()
    return _direct_get_all_namespace_names()


def delete_namespace(name: str) -> bool:
    """Delete a namespace record.

    Args:
        name: The namespace name.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_namespace(name)
    return _direct_delete_namespace(name)


def create_namespace_attributes(data: NamespaceAttributesData) -> bool:
    """Create namespace attributes record.

    Args:
        data: The NamespaceAttributesData to create.

    Returns:
        True if created successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_create_namespace_attributes(data)
    return _direct_create_namespace_attributes(data)


def get_namespace_attributes(name: str) -> Optional[NamespaceAttributesData]:
    """Get namespace attributes.

    Args:
        name: The namespace name.

    Returns:
        NamespaceAttributesData if found, None otherwise.
    """
    if _use_database_service():
        return _grpc_get_namespace_attributes(name)
    return _direct_get_namespace_attributes(name)


def update_namespace_attributes(data: NamespaceAttributesData) -> bool:
    """Update namespace attributes.

    Args:
        data: The NamespaceAttributesData with updated values.

    Returns:
        True if updated successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_update_namespace_attributes(data)
    return _direct_update_namespace_attributes(data)


def delete_namespace_attributes(name: str) -> bool:
    """Delete namespace attributes.

    Args:
        name: The namespace name.

    Returns:
        True if deleted successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_namespace_attributes(name)
    return _direct_delete_namespace_attributes(name)


# =============================================================================
# Artifact Direct Access Functions
# =============================================================================

def _direct_create_artifact(artifact_uuid: UUID, artifact_type: str,
                            source_url: str, name: str, namespace: str,
                            version: int) -> bool:
    """Create an artifact record in MariaDB."""
    engine = _get_engine()
    table = _get_artifacts_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=artifact_uuid,
                artifact_type=artifact_type,
                source_url=source_url,
                name=name,
                namespace=namespace,
                version=version
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for artifact {artifact_uuid}: {e}')
        return False


def _direct_get_artifact(artifact_uuid: UUID) -> Optional[ArtifactData]:
    """Get artifact static values from MariaDB."""
    engine = _get_engine()
    table = _get_artifacts_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == artifact_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return ArtifactData(
                uuid=result.uuid,
                artifact_type=result.artifact_type,
                source_url=result.source_url,
                name=result.name,
                namespace=result.namespace,
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for artifact {artifact_uuid}: {e}')
        return None


def _direct_get_all_artifacts() -> list[ArtifactData]:
    """Get all artifacts from MariaDB."""
    engine = _get_engine()
    table = _get_artifacts_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table)
            result = conn.execute(stmt).fetchall()
            return [
                ArtifactData(
                    uuid=row.uuid,
                    artifact_type=row.artifact_type,
                    source_url=row.source_url,
                    name=row.name,
                    namespace=row.namespace,
                    version=row.version
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for all artifacts: {e}')
        return []


def _direct_find_artifacts(
        criteria: ObjectFilterCriteria) -> list[ArtifactData]:
    """Find artifacts matching the given filter criteria.

    Joins ``artifacts`` to ``object_states`` and applies the optional
    state/namespace/name filters from ``criteria``. On
    ``OperationalError`` logs the full criteria at WARNING level and
    returns an empty list.
    """
    engine = _get_engine()
    table = _get_artifacts_table()
    stmt = _build_object_filter_query(
        table, ObjectType.ARTIFACT, criteria)

    try:
        with engine.connect() as conn:
            result = conn.execute(stmt).fetchall()
            return [
                ArtifactData(
                    uuid=row.uuid,
                    artifact_type=row.artifact_type,
                    source_url=row.source_url,
                    name=row.name,
                    namespace=row.namespace,
                    version=row.version
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB find failed for artifacts '
            f'(states={criteria.states!r}, '
            f'namespace={criteria.namespace!r}, '
            f'name={criteria.name!r}): {e}')
        return []


def _direct_update_artifact(data: ArtifactData) -> bool:
    """Update an artifact record in MariaDB.

    This is used to persist version upgrades.

    Args:
        data: The ArtifactData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    engine = _get_engine()
    table = _get_artifacts_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                artifact_type=data.artifact_type,
                source_url=data.source_url,
                name=data.name,
                namespace=data.namespace,
                version=data.version
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB update failed for artifact {data.uuid}: {e}')
        return False


def _direct_delete_artifact(artifact_uuid: UUID) -> bool:
    """Delete an artifact record from MariaDB."""
    engine = _get_engine()
    table = _get_artifacts_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == artifact_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for artifact {artifact_uuid}: {e}')
        return False


# =============================================================================
# Artifact Attributes Direct Access Functions
# =============================================================================

def _direct_create_artifact_attributes(
        data: ArtifactAttributesData) -> bool:
    """Create an artifact_attributes record in MariaDB."""
    engine = _get_engine()
    table = _get_artifact_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                max_versions=data.max_versions,
                shared=data.shared,
                highest_index=data.highest_index
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for artifact_attributes '
            f'{data.uuid}: {e}')
        return False


def _direct_get_artifact_attributes(
        artifact_uuid: UUID) -> Optional[ArtifactAttributesData]:
    """Get artifact attributes from MariaDB."""
    engine = _get_engine()
    table = _get_artifact_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == artifact_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return ArtifactAttributesData(
                uuid=result.uuid,
                max_versions=result.max_versions,
                shared=result.shared,
                highest_index=result.highest_index
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for artifact_attributes '
            f'{artifact_uuid}: {e}')
        return None


def _direct_update_artifact_attributes(
        data: ArtifactAttributesData) -> bool:
    """Update artifact attributes in MariaDB."""
    engine = _get_engine()
    table = _get_artifact_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                max_versions=data.max_versions,
                shared=data.shared,
                highest_index=data.highest_index
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB update failed for artifact_attributes '
            f'{data.uuid}: {e}')
        return False


def _direct_delete_artifact_attributes(artifact_uuid: UUID) -> bool:
    """Delete artifact attributes from MariaDB."""
    engine = _get_engine()
    table = _get_artifact_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == artifact_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for artifact_attributes '
            f'{artifact_uuid}: {e}')
        return False


# =============================================================================
# Artifact Index Direct Access Functions
# =============================================================================

def _direct_create_artifact_index(artifact_uuid: UUID, index_number: int,
                                  blob_uuid: UUID) -> bool:
    """Create an artifact index record in MariaDB."""
    engine = _get_engine()
    table = _get_artifact_indexes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                artifact_uuid=artifact_uuid,
                index_number=index_number,
                blob_uuid=blob_uuid
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for artifact_index '
            f'{artifact_uuid}/{index_number}: {e}')
        return False


def _direct_get_artifact_index(
        artifact_uuid: UUID,
        index_number: int) -> Optional[ArtifactIndexData]:
    """Get a specific artifact index from MariaDB."""
    engine = _get_engine()
    table = _get_artifact_indexes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                sa.and_(
                    table.c.artifact_uuid == artifact_uuid,
                    table.c.index_number == index_number
                )
            )
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return ArtifactIndexData(
                artifact_uuid=result.artifact_uuid,
                index_number=result.index_number,
                blob_uuid=result.blob_uuid
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for artifact_index '
            f'{artifact_uuid}/{index_number}: {e}')
        return None


def _direct_get_all_artifact_indexes(
        artifact_uuid: UUID) -> list[ArtifactIndexData]:
    """Get all indexes for an artifact from MariaDB, ordered by
    index_number."""
    engine = _get_engine()
    table = _get_artifact_indexes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.artifact_uuid == artifact_uuid
            ).order_by(table.c.index_number)
            result = conn.execute(stmt).fetchall()
            return [
                ArtifactIndexData(
                    artifact_uuid=row.artifact_uuid,
                    index_number=row.index_number,
                    blob_uuid=row.blob_uuid
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for artifact_indexes '
            f'{artifact_uuid}: {e}')
        return []


def _direct_delete_artifact_index(artifact_uuid: UUID,
                                  index_number: int) -> bool:
    """Delete a specific artifact index from MariaDB."""
    engine = _get_engine()
    table = _get_artifact_indexes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                sa.and_(
                    table.c.artifact_uuid == artifact_uuid,
                    table.c.index_number == index_number
                )
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for artifact_index '
            f'{artifact_uuid}/{index_number}: {e}')
        return False


def _direct_delete_all_artifact_indexes(artifact_uuid: UUID) -> int:
    """Delete all indexes for an artifact from MariaDB."""
    engine = _get_engine()
    table = _get_artifact_indexes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.artifact_uuid == artifact_uuid
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete all failed for artifact_indexes '
            f'{artifact_uuid}: {e}')
        return 0


# =============================================================================
# Artifact gRPC Client Functions
# =============================================================================

def _grpc_create_artifact(artifact_uuid: UUID, artifact_type: str,
                          source_url: str, name: str, namespace: str,
                          version: int) -> bool:
    """Create an artifact record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateArtifactRequest(
            artifact=database_pb2.ArtifactStaticData(
                uuid=str(artifact_uuid),
                artifact_type=artifact_type,
                source_url=source_url,
                name=name,
                namespace=namespace,
                version=version
            )
        )
        reply = _grpc_call(stub.CreateArtifact, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateArtifact failed for {artifact_uuid}: {e}')
        return False


def _grpc_get_artifact(artifact_uuid: UUID) -> Optional[ArtifactData]:
    """Get artifact static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetArtifactRequest(uuid=str(artifact_uuid))
        reply = _grpc_call(stub.GetArtifact, request)
        if not reply.found:
            return None
        return ArtifactData(
            uuid=reply.artifact.uuid,
            artifact_type=reply.artifact.artifact_type,
            source_url=reply.artifact.source_url,
            name=reply.artifact.name,
            namespace=reply.artifact.namespace,
            version=reply.artifact.version
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetArtifact failed for {artifact_uuid}: {e}')
        return None


def _grpc_get_all_artifacts() -> list[ArtifactData]:
    """Get all artifacts via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllArtifactsRequest()
        reply = _grpc_call(stub.GetAllArtifacts, request)
        return [
            ArtifactData(
                uuid=a.uuid,
                artifact_type=a.artifact_type,
                source_url=a.source_url,
                name=a.name,
                namespace=a.namespace,
                version=a.version
            )
            for a in reply.artifacts
        ]
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetAllArtifacts failed: {e}')
        return []


def _grpc_find_artifacts(
        criteria: ObjectFilterCriteria) -> list[ArtifactData]:
    """Find artifacts matching criteria via the database microservice."""
    try:
        stub = _get_database_stub()
        proto_criteria = database_pb2.ObjectFilterCriteria(
            states=criteria.states if criteria.states is not None else []
        )
        if criteria.namespace is not None:
            proto_criteria.namespace = criteria.namespace
        if criteria.name is not None:
            proto_criteria.name = criteria.name
        request = database_pb2.FindArtifactsRequest(
            criteria=proto_criteria)
        reply = _grpc_call(stub.FindArtifacts, request)
        return [
            ArtifactData(
                uuid=a.uuid,
                artifact_type=a.artifact_type,
                source_url=a.source_url,
                name=a.name,
                namespace=a.namespace,
                version=a.version
            )
            for a in reply.artifacts
        ]
    except grpc.RpcError as e:
        LOG.error(f'gRPC FindArtifacts failed: {e}')
        return []


def _grpc_update_artifact(data: ArtifactData) -> bool:
    """Update an artifact record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateArtifactRequest(
            artifact=database_pb2.ArtifactStaticData(
                uuid=str(data.uuid),
                artifact_type=data.artifact_type,
                source_url=data.source_url,
                name=data.name,
                namespace=data.namespace,
                version=data.version
            )
        )
        reply = _grpc_call(stub.UpdateArtifact, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpdateArtifact failed for {data.uuid}: {e}')
        return False


def _grpc_delete_artifact(artifact_uuid: UUID) -> bool:
    """Delete an artifact record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteArtifactRequest(
            uuid=str(artifact_uuid))
        reply = _grpc_call(stub.DeleteArtifact, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteArtifact failed for {artifact_uuid}: {e}')
        return False


# =============================================================================
# Artifact Attributes gRPC Client Functions
# =============================================================================

def _grpc_create_artifact_attributes(
        data: ArtifactAttributesData) -> bool:
    """Create artifact attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateArtifactAttributesRequest(
            data=database_pb2.ArtifactAttributesProto(
                uuid=str(data.uuid),
                max_versions=data.max_versions,
                shared=data.shared,
                highest_index=data.highest_index
            )
        )
        reply = _grpc_call(stub.CreateArtifactAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateArtifactAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_get_artifact_attributes(
        artifact_uuid: UUID) -> Optional[ArtifactAttributesData]:
    """Get artifact attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetArtifactAttributesRequest(
            uuid=str(artifact_uuid))
        reply = _grpc_call(stub.GetArtifactAttributes, request)
        if not reply.found:
            return None
        d = reply.data
        return ArtifactAttributesData(
            uuid=UUID(d.uuid),
            max_versions=d.max_versions,
            shared=d.shared,
            highest_index=d.highest_index
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetArtifactAttributes failed for '
            f'{artifact_uuid}: {e}')
        return None


def _grpc_update_artifact_attributes(
        data: ArtifactAttributesData) -> bool:
    """Update artifact attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateArtifactAttributesRequest(
            data=database_pb2.ArtifactAttributesProto(
                uuid=str(data.uuid),
                max_versions=data.max_versions,
                shared=data.shared,
                highest_index=data.highest_index
            )
        )
        reply = _grpc_call(stub.UpdateArtifactAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC UpdateArtifactAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_delete_artifact_attributes(artifact_uuid: UUID) -> bool:
    """Delete artifact attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteArtifactAttributesRequest(
            uuid=str(artifact_uuid))
        reply = _grpc_call(stub.DeleteArtifactAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteArtifactAttributes failed for '
            f'{artifact_uuid}: {e}')
        return False


# =============================================================================
# Artifact Index gRPC Client Functions
# =============================================================================

def _grpc_create_artifact_index(artifact_uuid: UUID, index_number: int,
                                blob_uuid: UUID) -> bool:
    """Create an artifact index via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateArtifactIndexRequest(
            data=database_pb2.ArtifactIndexProto(
                artifact_uuid=str(artifact_uuid),
                index_number=index_number,
                blob_uuid=str(blob_uuid)
            )
        )
        reply = _grpc_call(stub.CreateArtifactIndex, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateArtifactIndex failed for '
            f'{artifact_uuid}/{index_number}: {e}')
        return False


def _grpc_get_artifact_index(
        artifact_uuid: UUID,
        index_number: int) -> Optional[ArtifactIndexData]:
    """Get a specific artifact index via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetArtifactIndexRequest(
            artifact_uuid=str(artifact_uuid),
            index_number=index_number
        )
        reply = _grpc_call(stub.GetArtifactIndex, request)
        if not reply.found:
            return None
        d = reply.data
        return ArtifactIndexData(
            artifact_uuid=UUID(d.artifact_uuid),
            index_number=d.index_number,
            blob_uuid=UUID(d.blob_uuid)
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetArtifactIndex failed for '
            f'{artifact_uuid}/{index_number}: {e}')
        return None


def _grpc_get_all_artifact_indexes(
        artifact_uuid: UUID) -> list[ArtifactIndexData]:
    """Get all indexes for an artifact via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllArtifactIndexesRequest(
            artifact_uuid=str(artifact_uuid))
        reply = _grpc_call(stub.GetAllArtifactIndexes, request)
        return [
            ArtifactIndexData(
                artifact_uuid=UUID(idx.artifact_uuid),
                index_number=idx.index_number,
                blob_uuid=UUID(idx.blob_uuid)
            )
            for idx in reply.indexes
        ]
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetAllArtifactIndexes failed for '
            f'{artifact_uuid}: {e}')
        return []


def _grpc_delete_artifact_index(artifact_uuid: UUID,
                                index_number: int) -> bool:
    """Delete a specific artifact index via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteArtifactIndexRequest(
            artifact_uuid=str(artifact_uuid),
            index_number=index_number
        )
        reply = _grpc_call(stub.DeleteArtifactIndex, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteArtifactIndex failed for '
            f'{artifact_uuid}/{index_number}: {e}')
        return False


def _grpc_delete_all_artifact_indexes(artifact_uuid: UUID) -> int:
    """Delete all indexes for an artifact via the database
    microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteAllArtifactIndexesRequest(
            artifact_uuid=str(artifact_uuid))
        reply = _grpc_call(stub.DeleteAllArtifactIndexes, request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteAllArtifactIndexes failed for '
            f'{artifact_uuid}: {e}')
        return 0


# =============================================================================
# Artifact Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def create_artifact(artifact_uuid: UUID, artifact_type: str,
                    source_url: str, name: str, namespace: str,
                    version: int) -> bool:
    """Create an artifact record.

    Args:
        artifact_uuid: The UUID of the artifact.
        artifact_type: One of 'snapshot', 'label', 'image', 'other'.
        source_url: Origin URL for the artifact content.
        name: Human-readable name.
        namespace: Owning namespace.
        version: The object version number.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_artifact(
            artifact_uuid, artifact_type, source_url, name,
            namespace, version)
    return _direct_create_artifact(
        artifact_uuid, artifact_type, source_url, name,
        namespace, version)


def get_artifact(artifact_uuid: UUID) -> Optional[ArtifactData]:
    """Get artifact static values.

    Args:
        artifact_uuid: The UUID of the artifact.

    Returns:
        An ArtifactData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_artifact(artifact_uuid)
    return _direct_get_artifact(artifact_uuid)


def get_all_artifacts() -> list[ArtifactData]:
    """Get all artifacts.

    Returns:
        List of ArtifactData objects.
    """
    if _use_database_service():
        return _grpc_get_all_artifacts()
    return _direct_get_all_artifacts()


def find_artifacts(
        criteria: ObjectFilterCriteria) -> list[ArtifactData]:
    """Find artifacts matching the given filter criteria.

    Args:
        criteria: Filter criteria (states, namespace, name).

    Returns:
        List of matching ArtifactData objects.
    """
    if _use_database_service():
        return _grpc_find_artifacts(criteria)
    return _direct_find_artifacts(criteria)


def update_artifact(data: ArtifactData) -> bool:
    """Update an artifact record.

    This is used to persist version upgrades.

    Args:
        data: The ArtifactData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_artifact(data)
    return _direct_update_artifact(data)


def delete_artifact(artifact_uuid: UUID) -> bool:
    """Delete an artifact record.

    Args:
        artifact_uuid: The UUID of the artifact.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_artifact(artifact_uuid)
    return _direct_delete_artifact(artifact_uuid)


# =============================================================================
# Artifact Attributes Public API Functions
# =============================================================================

def create_artifact_attributes(data: ArtifactAttributesData) -> bool:
    """Create artifact attributes record.

    Args:
        data: The ArtifactAttributesData to create.

    Returns:
        True if created successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_create_artifact_attributes(data)
    return _direct_create_artifact_attributes(data)


def get_artifact_attributes(
        artifact_uuid: UUID) -> Optional[ArtifactAttributesData]:
    """Get artifact attributes.

    Args:
        artifact_uuid: The UUID of the artifact.

    Returns:
        ArtifactAttributesData if found, None otherwise.
    """
    if _use_database_service():
        return _grpc_get_artifact_attributes(artifact_uuid)
    return _direct_get_artifact_attributes(artifact_uuid)


def update_artifact_attributes(data: ArtifactAttributesData) -> bool:
    """Update artifact attributes.

    Args:
        data: The ArtifactAttributesData with updated values.

    Returns:
        True if updated successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_update_artifact_attributes(data)
    return _direct_update_artifact_attributes(data)


def delete_artifact_attributes(artifact_uuid: UUID) -> bool:
    """Delete artifact attributes.

    Args:
        artifact_uuid: The UUID of the artifact.

    Returns:
        True if deleted successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_artifact_attributes(artifact_uuid)
    return _direct_delete_artifact_attributes(artifact_uuid)


# =============================================================================
# Artifact Index Public API Functions
# =============================================================================

def create_artifact_index(artifact_uuid: UUID, index_number: int,
                          blob_uuid: UUID) -> bool:
    """Create an artifact index record.

    Args:
        artifact_uuid: The UUID of the artifact.
        index_number: The version index number.
        blob_uuid: The UUID of the blob for this version.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_artifact_index(
            artifact_uuid, index_number, blob_uuid)
    return _direct_create_artifact_index(
        artifact_uuid, index_number, blob_uuid)


def get_artifact_index(artifact_uuid: UUID,
                       index_number: int) -> Optional[ArtifactIndexData]:
    """Get a specific artifact index.

    Args:
        artifact_uuid: The UUID of the artifact.
        index_number: The version index number.

    Returns:
        ArtifactIndexData if found, None otherwise.
    """
    if _use_database_service():
        return _grpc_get_artifact_index(artifact_uuid, index_number)
    return _direct_get_artifact_index(artifact_uuid, index_number)


def get_all_artifact_indexes(
        artifact_uuid: UUID) -> list[ArtifactIndexData]:
    """Get all indexes for an artifact, ordered by index_number.

    Args:
        artifact_uuid: The UUID of the artifact.

    Returns:
        List of ArtifactIndexData objects, ordered by index_number.
    """
    if _use_database_service():
        return _grpc_get_all_artifact_indexes(artifact_uuid)
    return _direct_get_all_artifact_indexes(artifact_uuid)


def delete_artifact_index(artifact_uuid: UUID,
                          index_number: int) -> bool:
    """Delete a specific artifact index.

    Args:
        artifact_uuid: The UUID of the artifact.
        index_number: The version index number.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_artifact_index(artifact_uuid, index_number)
    return _direct_delete_artifact_index(artifact_uuid, index_number)


def delete_all_artifact_indexes(artifact_uuid: UUID) -> int:
    """Delete all indexes for an artifact.

    Args:
        artifact_uuid: The UUID of the artifact.

    Returns:
        Number of indexes deleted.
    """
    if _use_database_service():
        return _grpc_delete_all_artifact_indexes(artifact_uuid)
    return _direct_delete_all_artifact_indexes(artifact_uuid)


# =============================================================================
# NetworkInterface Table Definitions
# =============================================================================

def _get_network_interfaces_table() -> sa.Table:
    """Get or create the network_interfaces table definition.

    This table stores static values for NetworkInterface objects.
    NetworkInterfaces represent virtual NICs attached to instances.

    The table schema is generated from the NetworkInterfaceData Pydantic
    model. The uuid is the primary key, with indexes on network_uuid,
    instance_uuid, and macaddr.
    """
    global _network_interfaces_table
    if _network_interfaces_table is None:
        metadata = _get_metadata()
        _network_interfaces_table = pydantic_to_sqlalchemy_table(
            NetworkInterfaceData,
            'network_interfaces',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
        # ``active`` is a server-managed flag, not part of the
        # NetworkInterfaceData DTO. It is TRUE while an interface is
        # live and NULLed when the interface enters the ``deleted``
        # state — see ``_direct_set_state``. The flag exists so that
        # the macaddr UNIQUE constraint below can ignore soft-deleted
        # rows: NULL values do not collide with each other in MariaDB
        # UNIQUE indexes, so a deleted interface keeps its row (for
        # audit) without blocking MAC reuse during the
        # ``CLEANER_DELAY`` window.
        _network_interfaces_table.append_column(
            sa.Column('active', sa.Boolean(), nullable=True))
        # MAC must be unique among ACTIVE interfaces on the same
        # network. Two soft-deleted rows (both with active=NULL) do
        # not collide, and an active row coexists with deleted rows
        # holding the same MAC. Cross-network MAC reuse is allowed —
        # different VXLAN networks are isolated broadcast domains.
        sa.UniqueConstraint(
            _network_interfaces_table.c.macaddr,
            _network_interfaces_table.c.active,
            _network_interfaces_table.c.network_uuid,
            name='uq_network_interfaces_macaddr_active_network')
    return _network_interfaces_table


def _get_network_interface_attributes_table() -> sa.Table:
    """Get or create the network_interface_attributes table definition."""
    global _network_interface_attributes_table
    if _network_interface_attributes_table is None:
        metadata = _get_metadata()
        _network_interface_attributes_table = pydantic_to_sqlalchemy_table(
            NetworkInterfaceAttributesData,
            'network_interface_attributes',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _network_interface_attributes_table


def _ensure_network_interfaces_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the network_interfaces table schema is up to date."""
    table_name = 'network_interfaces'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_network_interfaces_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver <= 1:
        LOG.info(f'Upgrading {table_name} table to version 2 '
                 '(add UNIQUE constraint on macaddr)')
        with engine.connect() as conn:
            try:
                conn.execute(sa.text(
                    'ALTER TABLE network_interfaces '
                    'ADD CONSTRAINT uq_network_interfaces_macaddr '
                    'UNIQUE (macaddr)'))
                conn.commit()
            except (IntegrityError, OperationalError) as e:
                LOG.debug(
                    f'UNIQUE constraint on macaddr already exists '
                    f'or could not be added: {e}')

        current_ver = 2
        _set_table_version(engine, table_name, current_ver)

    if current_ver <= 2:
        # Replace the global UNIQUE on (macaddr) with a partial-style
        # UNIQUE on (macaddr, active, network_uuid). The ``active``
        # column is NULL when the interface is soft-deleted, so
        # multiple deleted rows sharing a MAC do not collide and a
        # caller can reuse the MAC for a new interface immediately
        # rather than waiting for ``CLEANER_DELAY`` to elapse.
        LOG.info(f'Upgrading {table_name} table to version 3 '
                 '(scope macaddr UNIQUE to active interfaces)')
        with engine.connect() as conn:
            try:
                conn.execute(sa.text(
                    'ALTER TABLE network_interfaces '
                    'ADD COLUMN active BOOLEAN NULL'))
                conn.commit()
            except (IntegrityError, OperationalError) as e:
                LOG.debug(
                    f'active column already exists on '
                    f'{table_name} or could not be added: {e}')

            # Backfill: rows whose state is ``deleted`` get
            # active=NULL; everything else gets active=TRUE so the new
            # UNIQUE constraint is correct from the moment it lands.
            # ``object_states.object_uuid`` is VARCHAR-with-dashes,
            # ``network_interfaces.uuid`` is native UUID rendered as
            # 32-char hex, so the JOIN strips dashes — same trick as
            # ``_build_object_filter_query``.
            try:
                conn.execute(sa.text("""
                    UPDATE network_interfaces ni
                    LEFT JOIN object_states s
                      ON REPLACE(s.object_uuid, '-', '') = LOWER(HEX(ni.uuid))
                     AND s.object_type = 'interface'
                    SET ni.active = CASE
                        WHEN s.state_value = 'deleted' THEN NULL
                        ELSE 1
                    END
                """))
                conn.commit()
            except OperationalError as e:
                LOG.warning(
                    f'Backfill of active column on {table_name} '
                    f'failed: {e}')

            try:
                conn.execute(sa.text(
                    'ALTER TABLE network_interfaces '
                    'DROP CONSTRAINT uq_network_interfaces_macaddr'))
                conn.commit()
            except (IntegrityError, OperationalError) as e:
                LOG.debug(
                    f'Old UNIQUE constraint on macaddr could not be '
                    f'dropped (probably already gone): {e}')

            try:
                conn.execute(sa.text(
                    'ALTER TABLE network_interfaces '
                    'ADD CONSTRAINT '
                    'uq_network_interfaces_macaddr_active_network '
                    'UNIQUE (macaddr, active, network_uuid)'))
                conn.commit()
            except (IntegrityError, OperationalError) as e:
                LOG.debug(
                    f'Composite UNIQUE constraint already exists '
                    f'or could not be added: {e}')

        current_ver = 3
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NETWORK_INTERFACES_VERSION,
        'migrated': start_ver != current_ver
    }


def _ensure_network_interface_attributes_schema(
        engine: sa.Engine) -> dict[str, Any]:
    """Ensure the network_interface_attributes table schema is up to
    date."""
    table_name = 'network_interface_attributes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_network_interface_attributes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NETWORK_INTERFACE_ATTRIBUTES_VERSION,
        'migrated': start_ver != current_ver
    }


# =============================================================================
# NetworkInterface Direct Access Functions
# These are used by the database daemon for NetworkInterface object storage.
# =============================================================================

def _direct_create_network_interface(data: NetworkInterfaceData) -> bool:
    """Create a NetworkInterface record in MariaDB.

    Args:
        data: The NetworkInterfaceData to insert.

    Returns:
        True if the record was created, False if it already exists or
        error.
    """
    engine = _get_engine()
    table = _get_network_interfaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                network_uuid=data.network_uuid,
                instance_uuid=data.instance_uuid,
                macaddr=data.macaddr,
                ipv4=data.ipv4,
                order=data.order,
                model=data.model,
                version=data.version,
                # Marks the row as participating in the macaddr UNIQUE
                # constraint. ``_direct_set_state`` will null this out
                # when the interface transitions to ``deleted`` so the
                # MAC can be reused.
                active=True,
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for network_interface '
            f'{data.uuid}: {e}')
        return False


def _direct_get_network_interface(
        ni_uuid: UUID) -> Optional[NetworkInterfaceData]:
    """Get NetworkInterface static values from MariaDB.

    Args:
        ni_uuid: The UUID of the NetworkInterface.

    Returns:
        A NetworkInterfaceData object, or None if not found.
    """
    engine = _get_engine()
    table = _get_network_interfaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == ni_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return NetworkInterfaceData(
                uuid=result.uuid,
                network_uuid=result.network_uuid,
                instance_uuid=result.instance_uuid,
                macaddr=result.macaddr,
                ipv4=result.ipv4,
                order=result.order,
                model=result.model,
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for network_interface {ni_uuid}: {e}')
        return None


def _direct_get_network_interfaces_by_instance(
        instance_uuid: UUID) -> list[NetworkInterfaceData]:
    """Get NetworkInterfaces for an instance from MariaDB.

    Args:
        instance_uuid: The UUID of the instance.

    Returns:
        List of NetworkInterfaceData objects.
    """
    engine = _get_engine()
    table = _get_network_interfaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.instance_uuid == instance_uuid)
            result = conn.execute(stmt).fetchall()

            return [
                NetworkInterfaceData(
                    uuid=row.uuid,
                    network_uuid=row.network_uuid,
                    instance_uuid=row.instance_uuid,
                    macaddr=row.macaddr,
                    ipv4=row.ipv4,
                    order=row.order,
                    model=row.model,
                    version=row.version
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for network_interfaces by '
            f'instance {instance_uuid}: {e}')
        return []


def _direct_get_network_interfaces_by_network(
        network_uuid: UUID) -> list[NetworkInterfaceData]:
    """Get NetworkInterfaces for a network from MariaDB.

    Args:
        network_uuid: The UUID of the network.

    Returns:
        List of NetworkInterfaceData objects.
    """
    engine = _get_engine()
    table = _get_network_interfaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.network_uuid == network_uuid)
            result = conn.execute(stmt).fetchall()

            return [
                NetworkInterfaceData(
                    uuid=row.uuid,
                    network_uuid=row.network_uuid,
                    instance_uuid=row.instance_uuid,
                    macaddr=row.macaddr,
                    ipv4=row.ipv4,
                    order=row.order,
                    model=row.model,
                    version=row.version
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for network_interfaces by '
            f'network {network_uuid}: {e}')
        return []


def _direct_get_all_network_interfaces() -> list[NetworkInterfaceData]:
    """Get all NetworkInterface records from MariaDB.

    Returns:
        List of NetworkInterfaceData objects.
    """
    engine = _get_engine()
    table = _get_network_interfaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table)
            result = conn.execute(stmt).fetchall()

            return [
                NetworkInterfaceData(
                    uuid=row.uuid,
                    network_uuid=row.network_uuid,
                    instance_uuid=row.instance_uuid,
                    macaddr=row.macaddr,
                    ipv4=row.ipv4,
                    order=row.order,
                    model=row.model,
                    version=row.version
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for all network_interfaces: {e}')
        return []


def _direct_find_network_interfaces(
        criteria: ObjectFilterCriteria) -> list[NetworkInterfaceData]:
    """Find NetworkInterface records matching the given filter criteria.

    Joins ``network_interfaces`` to ``object_states`` and applies the
    optional state filter from ``criteria``. The ``namespace`` and
    ``name`` filters are silently ignored because the
    ``network_interfaces`` table has neither column. The
    ``network_uuid`` and ``instance_uuid`` filters ARE honoured —
    they correspond to indexed columns on the same table. On
    ``OperationalError`` logs the full criteria at WARNING level and
    returns an empty list.
    """
    engine = _get_engine()
    table = _get_network_interfaces_table()
    # network_interfaces has no namespace or name column; strip both
    # before building the query to avoid WHERE clauses that would error.
    safe_criteria = ObjectFilterCriteria(
        states=criteria.states,
        namespace=None,
        name=None,
        network_uuid=criteria.network_uuid,
        instance_uuid=criteria.instance_uuid,
    )
    stmt = _build_object_filter_query(
        table, ObjectType.INTERFACE, safe_criteria)
    # Order by the per-instance interface index so callers iterate
    # interfaces in user-specified order. Without this MariaDB returns
    # rows in an unspecified order, which makes ``Instance.interfaces``
    # iteration nondeterministic and breaks code that relies on the
    # first interface being the one the user listed first (e.g. the
    # default-route choice in
    # ``_make_config_drive_openstack_disk``).
    stmt = stmt.order_by(table.c.order)

    try:
        with engine.connect() as conn:
            result = conn.execute(stmt).fetchall()
            return [
                NetworkInterfaceData(
                    uuid=row.uuid,
                    network_uuid=row.network_uuid,
                    instance_uuid=row.instance_uuid,
                    macaddr=row.macaddr,
                    ipv4=row.ipv4,
                    order=row.order,
                    model=row.model,
                    version=row.version
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB find failed for network_interfaces '
            f'(states={criteria.states!r}, '
            f'namespace={criteria.namespace!r}, '
            f'name={criteria.name!r}, '
            f'network_uuid={criteria.network_uuid!r}, '
            f'instance_uuid={criteria.instance_uuid!r}): {e}')
        return []


def _direct_delete_network_interface(ni_uuid: UUID) -> bool:
    """Delete a NetworkInterface record from MariaDB.

    Args:
        ni_uuid: The UUID of the NetworkInterface.

    Returns:
        True if deleted, False if not found or error.
    """
    engine = _get_engine()
    table = _get_network_interfaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == ni_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for network_interface '
            f'{ni_uuid}: {e}')
        return False


def _direct_update_network_interface(
        data: NetworkInterfaceData) -> bool:
    """Update a NetworkInterface record in MariaDB.

    This is used to persist version upgrades.

    Args:
        data: The NetworkInterfaceData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    engine = _get_engine()
    table = _get_network_interfaces_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                network_uuid=data.network_uuid,
                instance_uuid=data.instance_uuid,
                macaddr=data.macaddr,
                ipv4=data.ipv4,
                order=data.order,
                model=data.model,
                version=data.version
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB update failed for network_interface '
            f'{data.uuid}: {e}')
        return False


# =============================================================================
# NetworkInterface Attributes Direct Access Functions
# =============================================================================

def _direct_create_network_interface_attributes(
        data: NetworkInterfaceAttributesData) -> bool:
    """Create a network_interface_attributes record in MariaDB."""
    engine = _get_engine()
    table = _get_network_interface_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                floating_address=data.floating_address)
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for '
            f'network_interface_attributes {data.uuid}: {e}')
        return False


def _direct_get_network_interface_attributes(
        ni_uuid: UUID) -> Optional[NetworkInterfaceAttributesData]:
    """Get NetworkInterface attributes from MariaDB."""
    engine = _get_engine()
    table = _get_network_interface_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == ni_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return NetworkInterfaceAttributesData(
                uuid=result.uuid,
                floating_address=result.floating_address,
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for '
            f'network_interface_attributes {ni_uuid}: {e}')
        return None


def _direct_update_network_interface_attributes(
        data: NetworkInterfaceAttributesData) -> bool:
    """Update NetworkInterface attributes in MariaDB."""
    engine = _get_engine()
    table = _get_network_interface_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(floating_address=data.floating_address)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB update failed for '
            f'network_interface_attributes {data.uuid}: {e}')
        return False


def _direct_delete_network_interface_attributes(
        ni_uuid: UUID) -> bool:
    """Delete NetworkInterface attributes from MariaDB."""
    engine = _get_engine()
    table = _get_network_interface_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == ni_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for '
            f'network_interface_attributes {ni_uuid}: {e}')
        return False


# =============================================================================
# NetworkInterface gRPC Client Functions
# These call the database microservice for NetworkInterface operations.
# =============================================================================

def _grpc_create_network_interface(
        data: NetworkInterfaceData) -> bool:
    """Create a NetworkInterface record via the database
    microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateNetworkInterfaceRequest(
            network_interface=database_pb2.NetworkInterfaceStaticData(
                uuid=str(data.uuid),
                network_uuid=str(data.network_uuid),
                instance_uuid=str(data.instance_uuid),
                macaddr=data.macaddr,
                ipv4=data.ipv4 or '',
                order=data.order,
                model=data.model or '',
                version=data.version
            )
        )
        reply = _grpc_call(stub.CreateNetworkInterface, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateNetworkInterface failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_get_network_interface(
        ni_uuid: UUID) -> Optional[NetworkInterfaceData]:
    """Get NetworkInterface static values via the database
    microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNetworkInterfaceRequest(
            uuid=str(ni_uuid))
        reply = _grpc_call(stub.GetNetworkInterface, request)
        if not reply.found:
            return None
        d = reply.network_interface
        return NetworkInterfaceData(
            uuid=d.uuid,
            network_uuid=d.network_uuid,
            instance_uuid=d.instance_uuid,
            macaddr=d.macaddr,
            ipv4=d.ipv4 or None,
            order=d.order,
            model=d.model or None,
            version=d.version
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetNetworkInterface failed for {ni_uuid}: {e}')
        return None


def _grpc_get_network_interfaces_by_instance(
        instance_uuid: UUID) -> list[NetworkInterfaceData]:
    """Get NetworkInterfaces for an instance via the database
    microservice."""
    try:
        stub = _get_database_stub()
        request = \
            database_pb2.GetNetworkInterfacesByInstanceRequest(
                instance_uuid=str(instance_uuid))
        reply = _grpc_call(stub.GetNetworkInterfacesByInstance, request)
        return [
            NetworkInterfaceData(
                uuid=d.uuid,
                network_uuid=d.network_uuid,
                instance_uuid=d.instance_uuid,
                macaddr=d.macaddr,
                ipv4=d.ipv4 or None,
                order=d.order,
                model=d.model or None,
                version=d.version
            )
            for d in reply.network_interfaces
        ]
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetNetworkInterfacesByInstance failed for '
            f'{instance_uuid}: {e}')
        return []


def _grpc_get_network_interfaces_by_network(
        network_uuid: UUID) -> list[NetworkInterfaceData]:
    """Get NetworkInterfaces for a network via the database
    microservice."""
    try:
        stub = _get_database_stub()
        request = \
            database_pb2.GetNetworkInterfacesByNetworkRequest(
                network_uuid=str(network_uuid))
        reply = _grpc_call(stub.GetNetworkInterfacesByNetwork, request)
        return [
            NetworkInterfaceData(
                uuid=d.uuid,
                network_uuid=d.network_uuid,
                instance_uuid=d.instance_uuid,
                macaddr=d.macaddr,
                ipv4=d.ipv4 or None,
                order=d.order,
                model=d.model or None,
                version=d.version
            )
            for d in reply.network_interfaces
        ]
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetNetworkInterfacesByNetwork failed for '
            f'{network_uuid}: {e}')
        return []


def _grpc_get_all_network_interfaces() -> list[NetworkInterfaceData]:
    """Get all NetworkInterface records via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllNetworkInterfacesRequest()
        reply = _grpc_call(stub.GetAllNetworkInterfaces, request)
        return [
            NetworkInterfaceData(
                uuid=d.uuid,
                network_uuid=d.network_uuid,
                instance_uuid=d.instance_uuid,
                macaddr=d.macaddr,
                ipv4=d.ipv4 or None,
                order=d.order,
                model=d.model or None,
                version=d.version
            )
            for d in reply.network_interfaces
        ]
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetAllNetworkInterfaces failed: {e}')
        return []


def _grpc_find_network_interfaces(
        criteria: ObjectFilterCriteria) -> list[NetworkInterfaceData]:
    """Find NetworkInterface records matching criteria via the database
    microservice."""
    try:
        stub = _get_database_stub()
        proto_criteria = database_pb2.ObjectFilterCriteria(
            states=criteria.states if criteria.states is not None else []
        )
        if criteria.namespace is not None:
            proto_criteria.namespace = criteria.namespace
        if criteria.name is not None:
            proto_criteria.name = criteria.name
        if criteria.network_uuid is not None:
            proto_criteria.network_uuid = criteria.network_uuid
        if criteria.instance_uuid is not None:
            proto_criteria.instance_uuid = criteria.instance_uuid
        request = database_pb2.FindNetworkInterfacesRequest(
            criteria=proto_criteria)
        reply = _grpc_call(stub.FindNetworkInterfaces, request)
        return [
            NetworkInterfaceData(
                uuid=d.uuid,
                network_uuid=d.network_uuid,
                instance_uuid=d.instance_uuid,
                macaddr=d.macaddr,
                ipv4=d.ipv4 or None,
                order=d.order,
                model=d.model or None,
                version=d.version
            )
            for d in reply.network_interfaces
        ]
    except grpc.RpcError as e:
        LOG.error(f'gRPC FindNetworkInterfaces failed: {e}')
        return []


def _grpc_delete_network_interface(ni_uuid: UUID) -> bool:
    """Delete a NetworkInterface record via the database
    microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNetworkInterfaceRequest(
            uuid=str(ni_uuid))
        reply = _grpc_call(stub.DeleteNetworkInterface, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteNetworkInterface failed for '
            f'{ni_uuid}: {e}')
        return False


def _grpc_update_network_interface(
        data: NetworkInterfaceData) -> bool:
    """Update a NetworkInterface record via the database
    microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateNetworkInterfaceRequest(
            network_interface=database_pb2.NetworkInterfaceStaticData(
                uuid=str(data.uuid),
                network_uuid=str(data.network_uuid),
                instance_uuid=str(data.instance_uuid),
                macaddr=data.macaddr,
                ipv4=data.ipv4 or '',
                order=data.order,
                model=data.model or '',
                version=data.version
            )
        )
        reply = _grpc_call(stub.UpdateNetworkInterface, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC UpdateNetworkInterface failed for '
            f'{data.uuid}: {e}')
        return False


# =============================================================================
# NetworkInterface Attributes gRPC Client Functions
# =============================================================================

def _grpc_create_network_interface_attributes(
        data: NetworkInterfaceAttributesData) -> bool:
    """Create NetworkInterface attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = \
            database_pb2.CreateNetworkInterfaceAttributesRequest(
                data=database_pb2.NetworkInterfaceAttributesProto(
                    uuid=str(data.uuid),
                    floating_address=data.floating_address or ''))
        reply = _grpc_call(stub.CreateNetworkInterfaceAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateNetworkInterfaceAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_get_network_interface_attributes(
        ni_uuid: UUID) -> Optional[NetworkInterfaceAttributesData]:
    """Get NetworkInterface attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = \
            database_pb2.GetNetworkInterfaceAttributesRequest(
                uuid=str(ni_uuid))
        reply = _grpc_call(stub.GetNetworkInterfaceAttributes, request)
        if not reply.found:
            return None
        return NetworkInterfaceAttributesData(
            uuid=reply.data.uuid,
            floating_address=(reply.data.floating_address
                              if reply.data.floating_address
                              else None),
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetNetworkInterfaceAttributes failed for '
            f'{ni_uuid}: {e}')
        return None


def _grpc_update_network_interface_attributes(
        data: NetworkInterfaceAttributesData) -> bool:
    """Update NetworkInterface attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = \
            database_pb2.UpdateNetworkInterfaceAttributesRequest(
                data=database_pb2.NetworkInterfaceAttributesProto(
                    uuid=str(data.uuid),
                    floating_address=data.floating_address or ''))
        reply = _grpc_call(stub.UpdateNetworkInterfaceAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC UpdateNetworkInterfaceAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_delete_network_interface_attributes(
        ni_uuid: UUID) -> bool:
    """Delete NetworkInterface attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = \
            database_pb2.DeleteNetworkInterfaceAttributesRequest(
                uuid=str(ni_uuid))
        reply = _grpc_call(stub.DeleteNetworkInterfaceAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteNetworkInterfaceAttributes failed for '
            f'{ni_uuid}: {e}')
        return False


# =============================================================================
# NetworkInterface Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def create_network_interface(data: NetworkInterfaceData) -> bool:
    """Create a NetworkInterface record.

    Args:
        data: The NetworkInterfaceData to insert.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_network_interface(data)
    return _direct_create_network_interface(data)


def get_network_interface(
        ni_uuid: UUID) -> Optional[NetworkInterfaceData]:
    """Get NetworkInterface static values.

    Args:
        ni_uuid: The UUID of the NetworkInterface.

    Returns:
        A NetworkInterfaceData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_network_interface(ni_uuid)
    return _direct_get_network_interface(ni_uuid)


def get_network_interfaces_by_instance(
        instance_uuid: UUID) -> list[NetworkInterfaceData]:
    """Get NetworkInterfaces for an instance.

    Args:
        instance_uuid: The UUID of the instance.

    Returns:
        List of NetworkInterfaceData objects.
    """
    if _use_database_service():
        return _grpc_get_network_interfaces_by_instance(instance_uuid)
    return _direct_get_network_interfaces_by_instance(instance_uuid)


def get_network_interfaces_by_network(
        network_uuid: UUID) -> list[NetworkInterfaceData]:
    """Get NetworkInterfaces for a network.

    Args:
        network_uuid: The UUID of the network.

    Returns:
        List of NetworkInterfaceData objects.
    """
    if _use_database_service():
        return _grpc_get_network_interfaces_by_network(network_uuid)
    return _direct_get_network_interfaces_by_network(network_uuid)


def get_all_network_interfaces() -> list[NetworkInterfaceData]:
    """Get all NetworkInterface records.

    Returns:
        List of NetworkInterfaceData objects.
    """
    if _use_database_service():
        return _grpc_get_all_network_interfaces()
    return _direct_get_all_network_interfaces()


def find_network_interfaces(
        criteria: ObjectFilterCriteria) -> list[NetworkInterfaceData]:
    """Find NetworkInterface records matching the given filter criteria.

    Args:
        criteria: Filter criteria (states). The namespace and name
            fields are accepted for proto-shape consistency but are
            silently ignored because network_interfaces has neither
            column.

    Returns:
        List of matching NetworkInterfaceData objects.
    """
    if _use_database_service():
        return _grpc_find_network_interfaces(criteria)
    return _direct_find_network_interfaces(criteria)


def delete_network_interface(ni_uuid: UUID) -> bool:
    """Delete a NetworkInterface record.

    Args:
        ni_uuid: The UUID of the NetworkInterface.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_network_interface(ni_uuid)
    return _direct_delete_network_interface(ni_uuid)


def update_network_interface(data: NetworkInterfaceData) -> bool:
    """Update a NetworkInterface record.

    This is used to persist version upgrades.

    Args:
        data: The NetworkInterfaceData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_network_interface(data)
    return _direct_update_network_interface(data)


def create_network_interface_attributes(
        data: NetworkInterfaceAttributesData) -> bool:
    """Create NetworkInterface attributes record.

    Args:
        data: The NetworkInterfaceAttributesData to create.

    Returns:
        True if created successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_create_network_interface_attributes(data)
    return _direct_create_network_interface_attributes(data)


def get_network_interface_attributes(
        ni_uuid: UUID) -> Optional[NetworkInterfaceAttributesData]:
    """Get NetworkInterface attributes.

    Args:
        ni_uuid: The UUID of the NetworkInterface.

    Returns:
        NetworkInterfaceAttributesData if found, None otherwise.
    """
    if _use_database_service():
        return _grpc_get_network_interface_attributes(ni_uuid)
    return _direct_get_network_interface_attributes(ni_uuid)


def update_network_interface_attributes(
        data: NetworkInterfaceAttributesData) -> bool:
    """Update NetworkInterface attributes.

    Args:
        data: The NetworkInterfaceAttributesData with updated values.

    Returns:
        True if updated successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_update_network_interface_attributes(data)
    return _direct_update_network_interface_attributes(data)


def delete_network_interface_attributes(ni_uuid: UUID) -> bool:
    """Delete NetworkInterface attributes.

    Args:
        ni_uuid: The UUID of the NetworkInterface.

    Returns:
        True if deleted successfully, False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_network_interface_attributes(ni_uuid)
    return _direct_delete_network_interface_attributes(ni_uuid)


# =============================================================================
# IPAM Table Definitions
# =============================================================================

def _get_ipams_table() -> sa.Table:
    """Get or create the ipams table definition.

    This table stores static values for IPAM objects. IPAMs track IP
    address allocation within a network's IP block.

    The table schema is generated from the IPAMData Pydantic model.
    The uuid is the primary key, with an index on network_uuid.
    """
    global _ipams_table
    if _ipams_table is None:
        metadata = _get_metadata()
        _ipams_table = pydantic_to_sqlalchemy_table(
            IPAMData,
            'ipams',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _ipams_table


def _ensure_ipams_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the ipams table schema is up to date."""
    table_name = 'ipams'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_ipams_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': IPAMS_VERSION,
        'migrated': start_ver != current_ver
    }


# =============================================================================
# IPAM Direct Access Functions
# These are used by the database daemon for IPAM object storage.
# =============================================================================

def _direct_create_ipam(data: IPAMData) -> bool:
    """Create an IPAM record in MariaDB.

    Args:
        data: The IPAMData to insert.

    Returns:
        True if created successfully, False if duplicate or error.
    """
    engine = _get_engine()
    table = _get_ipams_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                namespace=data.namespace,
                network_uuid=data.network_uuid,
                ipblock=data.ipblock,
                version=data.version
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB create failed for ipam {data.uuid}: {e}')
        return False


def _direct_get_ipam(ipam_uuid: UUID) -> Optional[IPAMData]:
    """Get IPAM static values from MariaDB.

    Args:
        ipam_uuid: The UUID of the IPAM.

    Returns:
        An IPAMData object, or None if not found.
    """
    engine = _get_engine()
    table = _get_ipams_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == ipam_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return IPAMData(
                uuid=result.uuid,
                namespace=result.namespace,
                network_uuid=result.network_uuid,
                ipblock=result.ipblock,
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for ipam {ipam_uuid}: {e}')
        return None


def _direct_delete_ipam(ipam_uuid: UUID) -> bool:
    """Delete an IPAM record from MariaDB.

    Args:
        ipam_uuid: The UUID of the IPAM to delete.

    Returns:
        True if deleted, False if not found or error.
    """
    engine = _get_engine()
    table = _get_ipams_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == ipam_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for ipam {ipam_uuid}: {e}')
        return False


def _direct_update_ipam(data: IPAMData) -> bool:
    """Update an IPAM record in MariaDB.

    This is used to persist version upgrades.

    Args:
        data: The IPAMData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    engine = _get_engine()
    table = _get_ipams_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                namespace=data.namespace,
                network_uuid=data.network_uuid,
                ipblock=data.ipblock,
                version=data.version
            )
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update failed for ipam {data.uuid}: {e}')
        return False


# =============================================================================
# IPAM gRPC Client Functions
# These are used by non-database daemons to access IPAM data via gRPC.
# =============================================================================

def _grpc_create_ipam(data: IPAMData) -> bool:
    """Create an IPAM record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateIPAMRequest(
            ipam=database_pb2.IPAMStaticData(
                uuid=str(data.uuid),
                namespace=data.namespace or '',
                network_uuid=str(data.network_uuid),
                ipblock=data.ipblock,
                version=data.version
            )
        )
        reply = _grpc_call(stub.CreateIPAM, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC CreateIPAM failed for {data.uuid}: {e}')
        return False


def _grpc_get_ipam(ipam_uuid: UUID) -> Optional[IPAMData]:
    """Get IPAM static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetIPAMRequest(uuid=str(ipam_uuid))
        reply = _grpc_call(stub.GetIPAM, request)
        if not reply.found:
            return None
        d = reply.ipam
        return IPAMData(
            uuid=d.uuid,
            namespace=d.namespace or None,
            network_uuid=d.network_uuid,
            ipblock=d.ipblock,
            version=d.version
        )
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetIPAM failed for {ipam_uuid}: {e}')
        return None


def _grpc_delete_ipam(ipam_uuid: UUID) -> bool:
    """Delete an IPAM record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteIPAMRequest(uuid=str(ipam_uuid))
        reply = _grpc_call(stub.DeleteIPAM, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC DeleteIPAM failed for {ipam_uuid}: {e}')
        return False


def _grpc_update_ipam(data: IPAMData) -> bool:
    """Update an IPAM record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateIPAMRequest(
            ipam=database_pb2.IPAMStaticData(
                uuid=str(data.uuid),
                namespace=data.namespace or '',
                network_uuid=str(data.network_uuid),
                ipblock=data.ipblock,
                version=data.version
            )
        )
        reply = _grpc_call(stub.UpdateIPAM, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(f'gRPC UpdateIPAM failed for {data.uuid}: {e}')
        return False


# =============================================================================
# IPAM Public API Functions
# =============================================================================

def create_ipam(data: IPAMData) -> bool:
    """Create an IPAM record.

    Args:
        data: The IPAMData to insert.

    Returns:
        True if successful, False otherwise.
    """
    if _use_database_service():
        return _grpc_create_ipam(data)
    return _direct_create_ipam(data)


def get_ipam(ipam_uuid: UUID) -> Optional[IPAMData]:
    """Get IPAM static values.

    Args:
        ipam_uuid: The UUID of the IPAM.

    Returns:
        An IPAMData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_ipam(ipam_uuid)
    return _direct_get_ipam(ipam_uuid)


def delete_ipam(ipam_uuid: UUID) -> bool:
    """Delete an IPAM record.

    Args:
        ipam_uuid: The UUID of the IPAM.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_ipam(ipam_uuid)
    return _direct_delete_ipam(ipam_uuid)


def update_ipam(data: IPAMData) -> bool:
    """Update an IPAM record.

    This is used to persist version upgrades.

    Args:
        data: The IPAMData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_ipam(data)
    return _direct_update_ipam(data)


# =============================================================================
# Network Table Definitions
# =============================================================================

def _get_networks_table() -> sa.Table:
    """Get or create the networks table definition.

    This table stores static values for Network objects. Networks are
    virtual L2 networks with optional DHCP, NAT, and DNS services.

    The table schema is generated from the NetworkData Pydantic model.
    The uuid is the primary key, with a UNIQUE constraint on vxid
    and an index on namespace.
    """
    global _networks_table
    if _networks_table is None:
        metadata = _get_metadata()
        _networks_table = pydantic_to_sqlalchemy_table(
            NetworkData,
            'networks',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
        # Add UNIQUE constraint on vxid for atomic VXLAN allocation
        sa.UniqueConstraint(
            _networks_table.c.vxid, name='uq_networks_vxid')
    return _networks_table


def _get_network_attributes_table() -> sa.Table:
    """Get or create the network_attributes table definition."""
    global _network_attributes_table
    if _network_attributes_table is None:
        metadata = _get_metadata()
        _network_attributes_table = pydantic_to_sqlalchemy_table(
            NetworkAttributesData,
            'network_attributes',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _network_attributes_table


def _ensure_networks_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the networks table schema is up to date."""
    table_name = 'networks'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_networks_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(
                        f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver <= 2:
        LOG.info(f'Upgrading {table_name} table to version 3 '
                 '(add index on name column)')
        with engine.connect() as conn:
            try:
                conn.execute(sa.text(
                    'CREATE INDEX idx_networks_name ON networks(name)'))
                conn.commit()
            except (IntegrityError, OperationalError) as e:
                LOG.debug(
                    f'Index idx_networks_name already exists '
                    f'or could not be added: {e}')

        current_ver = 3
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NETWORKS_VERSION,
        'migrated': start_ver != current_ver
    }


def _ensure_network_attributes_schema(
        engine: sa.Engine) -> dict[str, Any]:
    """Ensure the network_attributes table schema is up to date."""
    table_name = 'network_attributes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_network_attributes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(
                        f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver < NETWORK_ATTRIBUTES_VERSION:
        # Phase 7: drop the cached child-NI list. The
        # ``Network.networkinterfaces`` property now queries
        # network_interfaces live via an indexed
        # ``WHERE network_uuid = ?``. ``DROP COLUMN IF EXISTS`` is
        # idempotent so a re-run on a fresh deployment (where
        # ``create_all`` never created the columns) is a no-op.
        LOG.info(
            f'Upgrading {table_name} table to version '
            f'{NETWORK_ATTRIBUTES_VERSION} '
            '(drop cached networkinterfaces columns)')
        with engine.connect() as conn:
            for col in (
                    'networkinterfaces',
                    'networkinterfaces_initialized'):
                try:
                    conn.execute(sa.text(
                        f'ALTER TABLE {table_name} '
                        f'DROP COLUMN IF EXISTS {col}'))
                    conn.commit()
                except (IntegrityError, OperationalError) as e:
                    LOG.debug(
                        f'Column {col} drop skipped: {e}')
        current_ver = NETWORK_ATTRIBUTES_VERSION
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': NETWORK_ATTRIBUTES_VERSION,
        'migrated': start_ver != current_ver
    }


# =============================================================================
# Network Direct Access Functions
# These are used by the database daemon for Network object storage.
# =============================================================================

def _direct_create_network(data: NetworkData) -> bool:
    """Create a Network record in MariaDB.

    Args:
        data: The NetworkData to insert.

    Returns:
        True if created successfully, False if duplicate or error.
    """
    engine = _get_engine()
    table = _get_networks_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                name=data.name,
                namespace=data.namespace,
                netblock=data.netblock,
                provide_dhcp=data.provide_dhcp,
                provide_nat=data.provide_nat,
                provide_dns=data.provide_dns,
                vxid=data.vxid,
                egress_nic=data.egress_nic,
                mesh_nic=data.mesh_nic,
                version=data.version
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for network {data.uuid}: {e}')
        return False


def _direct_get_network(
        net_uuid: UUID) -> Optional[NetworkData]:
    """Get Network static values from MariaDB.

    Args:
        net_uuid: The UUID of the Network.

    Returns:
        A NetworkData object, or None if not found.
    """
    engine = _get_engine()
    table = _get_networks_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.uuid == net_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return NetworkData(
                uuid=result.uuid,
                name=result.name,
                namespace=result.namespace,
                netblock=result.netblock,
                provide_dhcp=result.provide_dhcp,
                provide_nat=result.provide_nat,
                provide_dns=result.provide_dns,
                vxid=result.vxid,
                egress_nic=result.egress_nic,
                mesh_nic=result.mesh_nic,
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for network {net_uuid}: {e}')
        return None


def _direct_get_all_networks() -> list[NetworkData]:
    """Get all Network records from MariaDB.

    Returns:
        List of NetworkData objects.
    """
    engine = _get_engine()
    table = _get_networks_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table)
            result = conn.execute(stmt).fetchall()

            return [
                NetworkData(
                    uuid=row.uuid,
                    name=row.name,
                    namespace=row.namespace,
                    netblock=row.netblock,
                    provide_dhcp=row.provide_dhcp,
                    provide_nat=row.provide_nat,
                    provide_dns=row.provide_dns,
                    vxid=row.vxid,
                    egress_nic=row.egress_nic,
                    mesh_nic=row.mesh_nic,
                    version=row.version
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for all networks: {e}')
        return []


def _direct_find_networks(
        criteria: ObjectFilterCriteria) -> list[NetworkData]:
    """Find networks matching the given filter criteria.

    Joins ``networks`` to ``object_states`` and applies the optional
    state/namespace/name filters from ``criteria``. On
    ``OperationalError`` logs the full criteria at WARNING level and
    returns an empty list.
    """
    engine = _get_engine()
    table = _get_networks_table()
    stmt = _build_object_filter_query(
        table, ObjectType.NETWORK, criteria)

    try:
        with engine.connect() as conn:
            result = conn.execute(stmt).fetchall()
            return [
                NetworkData(
                    uuid=row.uuid,
                    name=row.name,
                    namespace=row.namespace,
                    netblock=row.netblock,
                    provide_dhcp=row.provide_dhcp,
                    provide_nat=row.provide_nat,
                    provide_dns=row.provide_dns,
                    vxid=row.vxid,
                    egress_nic=row.egress_nic,
                    mesh_nic=row.mesh_nic,
                    version=row.version
                )
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB find failed for networks '
            f'(states={criteria.states!r}, '
            f'namespace={criteria.namespace!r}, '
            f'name={criteria.name!r}): {e}')
        return []


def _direct_delete_network(net_uuid: UUID) -> bool:
    """Delete a Network record from MariaDB.

    Args:
        net_uuid: The UUID of the Network to delete.

    Returns:
        True if deleted, False if not found or error.
    """
    engine = _get_engine()
    table = _get_networks_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.uuid == net_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for network {net_uuid}: {e}')
        return False


def _direct_create_network_attributes(
        data: NetworkAttributesData) -> bool:
    """Create a network_attributes record in MariaDB."""
    engine = _get_engine()
    table = _get_network_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                floating_gateway=data.floating_gateway,
                hosteddns=json.dumps(data.hosteddns))
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for '
            f'network_attributes {data.uuid}: {e}')
        return False


def _direct_get_network_attributes(
        net_uuid: UUID) -> Optional[NetworkAttributesData]:
    """Get Network attributes from MariaDB."""
    engine = _get_engine()
    table = _get_network_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.uuid == net_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            dns = result.hosteddns
            if isinstance(dns, str):
                dns = json.loads(dns)

            return NetworkAttributesData(
                uuid=result.uuid,
                floating_gateway=result.floating_gateway,
                hosteddns=dns if dns else {},
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for '
            f'network_attributes {net_uuid}: {e}')
        return None


def _direct_update_network_attributes(
        data: NetworkAttributesData) -> bool:
    """Update Network attributes in MariaDB."""
    engine = _get_engine()
    table = _get_network_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                floating_gateway=data.floating_gateway,
                hosteddns=json.dumps(data.hosteddns))
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB update failed for '
            f'network_attributes {data.uuid}: {e}')
        return False


def _direct_delete_network_attributes(
        net_uuid: UUID) -> bool:
    """Delete Network attributes from MariaDB."""
    engine = _get_engine()
    table = _get_network_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.uuid == net_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for '
            f'network_attributes {net_uuid}: {e}')
        return False


# =============================================================================
# Network gRPC Client Functions
# These call the database microservice for Network operations.
# =============================================================================

def _grpc_create_network(data: NetworkData) -> bool:
    """Create a Network record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateNetworkRequest(
            network=database_pb2.NetworkStaticData(
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
        )
        reply = _grpc_call(stub.CreateNetwork, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateNetwork failed for {data.uuid}: {e}')
        return False


def _grpc_get_network(
        net_uuid: UUID) -> Optional[NetworkData]:
    """Get Network static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNetworkRequest(
            uuid=str(net_uuid))
        reply = _grpc_call(stub.GetNetwork, request)
        if not reply.found:
            return None
        d = reply.network
        return NetworkData(
            uuid=d.uuid,
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
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetNetwork failed for {net_uuid}: {e}')
        return None


def _grpc_get_all_networks() -> list[NetworkData]:
    """Get all Network records via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllNetworksRequest()
        reply = _grpc_call(stub.GetAllNetworks, request)
        return [
            NetworkData(
                uuid=d.uuid,
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
            for d in reply.networks
        ]
    except grpc.RpcError as e:
        LOG.error(f'gRPC GetAllNetworks failed: {e}')
        return []


def _grpc_find_networks(
        criteria: ObjectFilterCriteria) -> list[NetworkData]:
    """Find networks matching criteria via the database microservice."""
    try:
        stub = _get_database_stub()
        proto_criteria = database_pb2.ObjectFilterCriteria(
            states=criteria.states if criteria.states is not None else []
        )
        if criteria.namespace is not None:
            proto_criteria.namespace = criteria.namespace
        if criteria.name is not None:
            proto_criteria.name = criteria.name
        request = database_pb2.FindNetworksRequest(
            criteria=proto_criteria)
        reply = _grpc_call(stub.FindNetworks, request)
        return [
            NetworkData(
                uuid=d.uuid,
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
            for d in reply.networks
        ]
    except grpc.RpcError as e:
        LOG.error(f'gRPC FindNetworks failed: {e}')
        return []


def _grpc_delete_network(net_uuid: UUID) -> bool:
    """Delete a Network record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNetworkRequest(
            uuid=str(net_uuid))
        reply = _grpc_call(stub.DeleteNetwork, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteNetwork failed for {net_uuid}: {e}')
        return False


def _grpc_create_network_attributes(
        data: NetworkAttributesData) -> bool:
    """Create Network attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateNetworkAttributesRequest(
            data=database_pb2.NetworkAttributesProto(
                uuid=str(data.uuid),
                floating_gateway=(
                    data.floating_gateway or ''),
                hosteddns_json=json.dumps(data.hosteddns)))
        reply = _grpc_call(stub.CreateNetworkAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateNetworkAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_get_network_attributes(
        net_uuid: UUID) -> Optional[NetworkAttributesData]:
    """Get Network attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNetworkAttributesRequest(
            uuid=str(net_uuid))
        reply = _grpc_call(stub.GetNetworkAttributes, request)
        if not reply.found:
            return None
        d = reply.data
        dns = json.loads(d.hosteddns_json) if d.hosteddns_json else {}
        return NetworkAttributesData(
            uuid=d.uuid,
            floating_gateway=(
                d.floating_gateway
                if d.floating_gateway else None),
            hosteddns=dns,
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetNetworkAttributes failed for '
            f'{net_uuid}: {e}')
        return None


def _grpc_update_network_attributes(
        data: NetworkAttributesData) -> bool:
    """Update Network attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateNetworkAttributesRequest(
            data=database_pb2.NetworkAttributesProto(
                uuid=str(data.uuid),
                floating_gateway=(
                    data.floating_gateway or ''),
                hosteddns_json=json.dumps(data.hosteddns)))
        reply = _grpc_call(stub.UpdateNetworkAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC UpdateNetworkAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_delete_network_attributes(
        net_uuid: UUID) -> bool:
    """Delete Network attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNetworkAttributesRequest(
            uuid=str(net_uuid))
        reply = _grpc_call(stub.DeleteNetworkAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteNetworkAttributes failed for '
            f'{net_uuid}: {e}')
        return False


# =============================================================================
# Network Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def create_network(data: NetworkData) -> bool:
    """Create a Network record.

    Args:
        data: The NetworkData to insert.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_network(data)
    return _direct_create_network(data)


def get_network(net_uuid: UUID) -> Optional[NetworkData]:
    """Get Network static values.

    Args:
        net_uuid: The UUID of the Network.

    Returns:
        A NetworkData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_network(net_uuid)
    return _direct_get_network(net_uuid)


def get_all_networks() -> list[NetworkData]:
    """Get all Network records.

    Returns:
        List of NetworkData objects.
    """
    if _use_database_service():
        return _grpc_get_all_networks()
    return _direct_get_all_networks()


def find_networks(
        criteria: ObjectFilterCriteria) -> list[NetworkData]:
    """Find networks matching the given filter criteria.

    Args:
        criteria: Filter criteria (states, namespace, name).

    Returns:
        List of matching NetworkData objects.
    """
    if _use_database_service():
        return _grpc_find_networks(criteria)
    return _direct_find_networks(criteria)


def delete_network(net_uuid: UUID) -> bool:
    """Delete a Network record.

    Args:
        net_uuid: The UUID of the Network.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_network(net_uuid)
    return _direct_delete_network(net_uuid)


def create_network_attributes(
        data: NetworkAttributesData) -> bool:
    """Create Network attributes record.

    Args:
        data: The NetworkAttributesData to create.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_network_attributes(data)
    return _direct_create_network_attributes(data)


def get_network_attributes(
        net_uuid: UUID) -> Optional[NetworkAttributesData]:
    """Get Network attributes.

    Args:
        net_uuid: The UUID of the Network.

    Returns:
        A NetworkAttributesData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_network_attributes(net_uuid)
    return _direct_get_network_attributes(net_uuid)


def update_network_attributes(
        data: NetworkAttributesData) -> bool:
    """Update Network attributes.

    Args:
        data: The NetworkAttributesData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_network_attributes(data)
    return _direct_update_network_attributes(data)


def delete_network_attributes(net_uuid: UUID) -> bool:
    """Delete Network attributes.

    Args:
        net_uuid: The UUID of the Network.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_network_attributes(net_uuid)
    return _direct_delete_network_attributes(net_uuid)


# =============================================================================
# AgentOperation Table Definitions
# =============================================================================

def _get_agent_operations_table() -> sa.Table:
    """Get or create the agent_operations table definition.

    This table stores static values for AgentOperation objects.
    AgentOperations represent in-guest agent tasks queued against
    an Instance.

    The table schema is generated from the AgentOperationData
    Pydantic model. The uuid is the primary key, with an index on
    instance_uuid.
    """
    global _agent_operations_table
    if _agent_operations_table is None:
        metadata = _get_metadata()
        _agent_operations_table = pydantic_to_sqlalchemy_table(
            AgentOperationData,
            'agent_operations',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _agent_operations_table


def _get_agent_operation_attributes_table() -> sa.Table:
    """Get or create the agent_operation_attributes table definition."""
    global _agent_operation_attributes_table
    if _agent_operation_attributes_table is None:
        metadata = _get_metadata()
        _agent_operation_attributes_table = pydantic_to_sqlalchemy_table(
            AgentOperationAttributesData,
            'agent_operation_attributes',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _agent_operation_attributes_table


def _ensure_agent_operations_schema(
        engine: sa.Engine) -> dict[str, Any]:
    """Ensure the agent_operations table schema is up to date."""
    table_name = 'agent_operations'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_agent_operations_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(
                        f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': AGENT_OPERATIONS_VERSION,
        'migrated': start_ver != current_ver
    }


def _ensure_agent_operation_attributes_schema(
        engine: sa.Engine) -> dict[str, Any]:
    """Ensure the agent_operation_attributes table schema is up to date."""
    table_name = 'agent_operation_attributes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_agent_operation_attributes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(
                        f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': AGENT_OPERATION_ATTRIBUTES_VERSION,
        'migrated': start_ver != current_ver
    }


# =============================================================================
# AgentOperation Direct Access Functions
# These are used by the database daemon for AgentOperation object storage.
# =============================================================================

def _direct_create_agent_operation(data: AgentOperationData) -> bool:
    """Create an AgentOperation record in MariaDB.

    Args:
        data: The AgentOperationData to insert.

    Returns:
        True if created successfully, False if duplicate or error.
    """
    engine = _get_engine()
    table = _get_agent_operations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                namespace=data.namespace,
                instance_uuid=data.instance_uuid,
                commands=json.dumps(data.commands),
                version=data.version
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for '
            f'agent_operation {data.uuid}: {e}')
        return False


def _direct_get_agent_operation(
        aop_uuid: UUID) -> Optional[AgentOperationData]:
    """Get AgentOperation static values from MariaDB.

    Args:
        aop_uuid: The UUID of the AgentOperation.

    Returns:
        An AgentOperationData object, or None if not found.
    """
    engine = _get_engine()
    table = _get_agent_operations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.uuid == aop_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            # Parse JSON commands field
            commands = result.commands
            if isinstance(commands, str):
                commands = json.loads(commands)

            return AgentOperationData(
                uuid=result.uuid,
                namespace=result.namespace,
                instance_uuid=result.instance_uuid,
                commands=commands if commands else [],
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for '
            f'agent_operation {aop_uuid}: {e}')
        return None


def _direct_delete_agent_operation(aop_uuid: UUID) -> bool:
    """Delete an AgentOperation record from MariaDB.

    Args:
        aop_uuid: The UUID of the AgentOperation to delete.

    Returns:
        True if deleted, False if not found or error.
    """
    engine = _get_engine()
    table = _get_agent_operations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.uuid == aop_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for '
            f'agent_operation {aop_uuid}: {e}')
        return False


def _direct_create_agent_operation_attributes(
        data: AgentOperationAttributesData) -> bool:
    """Create an agent_operation_attributes record in MariaDB."""
    engine = _get_engine()
    table = _get_agent_operation_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                results=json.dumps(data.results))
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for '
            f'agent_operation_attributes {data.uuid}: {e}')
        return False


def _direct_get_agent_operation_attributes(
        aop_uuid: UUID
) -> Optional[AgentOperationAttributesData]:
    """Get AgentOperation attributes from MariaDB."""
    engine = _get_engine()
    table = _get_agent_operation_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.uuid == aop_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            # Parse JSON results field
            results = result.results
            if isinstance(results, str):
                results = json.loads(results)

            return AgentOperationAttributesData(
                uuid=result.uuid,
                results=results if results else {},
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for '
            f'agent_operation_attributes {aop_uuid}: {e}')
        return None


def _direct_update_agent_operation_attributes(
        data: AgentOperationAttributesData) -> bool:
    """Update AgentOperation attributes in MariaDB."""
    engine = _get_engine()
    table = _get_agent_operation_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                results=json.dumps(data.results))
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB update failed for '
            f'agent_operation_attributes {data.uuid}: {e}')
        return False


def _direct_delete_agent_operation_attributes(
        aop_uuid: UUID) -> bool:
    """Delete AgentOperation attributes from MariaDB."""
    engine = _get_engine()
    table = _get_agent_operation_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.uuid == aop_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for '
            f'agent_operation_attributes {aop_uuid}: {e}')
        return False


# =============================================================================
# AgentOperation gRPC Client Functions
# These call the database microservice for AgentOperation operations.
# =============================================================================

def _grpc_create_agent_operation(data: AgentOperationData) -> bool:
    """Create an AgentOperation record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateAgentOperationRequest(
            data=database_pb2.AgentOperationStaticData(
                uuid=str(data.uuid),
                namespace=data.namespace or '',
                instance_uuid=str(data.instance_uuid),
                commands_json=json.dumps(data.commands),
                version=data.version
            )
        )
        reply = _grpc_call(stub.CreateAgentOperation, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateAgentOperation failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_get_agent_operation(
        aop_uuid: UUID) -> Optional[AgentOperationData]:
    """Get AgentOperation static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAgentOperationRequest(
            uuid=str(aop_uuid))
        reply = _grpc_call(stub.GetAgentOperation, request)
        if not reply.found:
            return None
        d = reply.data
        commands = json.loads(d.commands_json) if d.commands_json else []
        return AgentOperationData(
            uuid=d.uuid,
            namespace=d.namespace or '',
            instance_uuid=d.instance_uuid,
            commands=commands,
            version=d.version
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetAgentOperation failed for '
            f'{aop_uuid}: {e}')
        return None


def _grpc_delete_agent_operation(aop_uuid: UUID) -> bool:
    """Delete an AgentOperation record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteAgentOperationRequest(
            uuid=str(aop_uuid))
        reply = _grpc_call(stub.DeleteAgentOperation, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteAgentOperation failed for '
            f'{aop_uuid}: {e}')
        return False


def _grpc_create_agent_operation_attributes(
        data: AgentOperationAttributesData) -> bool:
    """Create AgentOperation attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateAgentOperationAttributesRequest(
            data=database_pb2.AgentOperationAttributesProto(
                uuid=str(data.uuid),
                results_json=json.dumps(data.results)))
        reply = _grpc_call(stub.CreateAgentOperationAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateAgentOperationAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_get_agent_operation_attributes(
        aop_uuid: UUID
) -> Optional[AgentOperationAttributesData]:
    """Get AgentOperation attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAgentOperationAttributesRequest(
            uuid=str(aop_uuid))
        reply = _grpc_call(stub.GetAgentOperationAttributes, request)
        if not reply.found:
            return None
        d = reply.data
        results = json.loads(d.results_json) if d.results_json else {}
        return AgentOperationAttributesData(
            uuid=d.uuid,
            results=results,
        )
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetAgentOperationAttributes failed for '
            f'{aop_uuid}: {e}')
        return None


def _grpc_update_agent_operation_attributes(
        data: AgentOperationAttributesData) -> bool:
    """Update AgentOperation attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateAgentOperationAttributesRequest(
            data=database_pb2.AgentOperationAttributesProto(
                uuid=str(data.uuid),
                results_json=json.dumps(data.results)))
        reply = _grpc_call(stub.UpdateAgentOperationAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC UpdateAgentOperationAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_delete_agent_operation_attributes(
        aop_uuid: UUID) -> bool:
    """Delete AgentOperation attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteAgentOperationAttributesRequest(
            uuid=str(aop_uuid))
        reply = _grpc_call(stub.DeleteAgentOperationAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteAgentOperationAttributes failed for '
            f'{aop_uuid}: {e}')
        return False


# =============================================================================
# AgentOperation Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def create_agent_operation(data: AgentOperationData) -> bool:
    """Create an AgentOperation record.

    Args:
        data: The AgentOperationData to insert.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_agent_operation(data)
    return _direct_create_agent_operation(data)


def get_agent_operation(
        aop_uuid: UUID) -> Optional[AgentOperationData]:
    """Get AgentOperation static values.

    Args:
        aop_uuid: The UUID of the AgentOperation.

    Returns:
        An AgentOperationData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_agent_operation(aop_uuid)
    return _direct_get_agent_operation(aop_uuid)


def delete_agent_operation(aop_uuid: UUID) -> bool:
    """Delete an AgentOperation record.

    Args:
        aop_uuid: The UUID of the AgentOperation.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_agent_operation(aop_uuid)
    return _direct_delete_agent_operation(aop_uuid)


def create_agent_operation_attributes(
        data: AgentOperationAttributesData) -> bool:
    """Create AgentOperation attributes record.

    Args:
        data: The AgentOperationAttributesData to create.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_agent_operation_attributes(data)
    return _direct_create_agent_operation_attributes(data)


def get_agent_operation_attributes(
        aop_uuid: UUID
) -> Optional[AgentOperationAttributesData]:
    """Get AgentOperation attributes.

    Args:
        aop_uuid: The UUID of the AgentOperation.

    Returns:
        An AgentOperationAttributesData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_agent_operation_attributes(aop_uuid)
    return _direct_get_agent_operation_attributes(aop_uuid)


def update_agent_operation_attributes(
        data: AgentOperationAttributesData) -> bool:
    """Update AgentOperation attributes.

    Args:
        data: The AgentOperationAttributesData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_agent_operation_attributes(data)
    return _direct_update_agent_operation_attributes(data)


def delete_agent_operation_attributes(aop_uuid: UUID) -> bool:
    """Delete AgentOperation attributes.

    Args:
        aop_uuid: The UUID of the AgentOperation.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_agent_operation_attributes(aop_uuid)
    return _direct_delete_agent_operation_attributes(aop_uuid)


# =============================================================================
# Instance Table Definitions
# =============================================================================

def _get_instances_table() -> sa.Table:
    """Get or create the instances table definition.

    This table stores static values for Instance objects. Instances
    represent virtual machines with CPU, memory, disk, and network
    configuration.

    The table schema is generated from the InstanceData Pydantic
    model. The uuid is the primary key, with an index on namespace.
    """
    global _instances_table
    if _instances_table is None:
        metadata = _get_metadata()
        _instances_table = pydantic_to_sqlalchemy_table(
            InstanceData,
            'instances',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _instances_table


def _get_instance_attributes_table() -> sa.Table:
    """Get or create the instance_attributes table definition."""
    global _instance_attributes_table
    if _instance_attributes_table is None:
        metadata = _get_metadata()
        _instance_attributes_table = pydantic_to_sqlalchemy_table(
            InstanceAttributesData,
            'instance_attributes',
            metadata,
            primary_key_fields=['uuid'],
            include_id_column=False
        )
    return _instance_attributes_table


def _ensure_instances_schema(
        engine: sa.Engine) -> dict[str, Any]:
    """Ensure the instances table schema is up to date."""
    table_name = 'instances'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_instances_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(
                        f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver <= 2:
        LOG.info(f'Upgrading {table_name} table to version 3 '
                 '(add index on name column)')
        with engine.connect() as conn:
            try:
                conn.execute(sa.text(
                    'CREATE INDEX idx_instances_name ON instances(name)'))
                conn.commit()
            except (IntegrityError, OperationalError) as e:
                LOG.debug(
                    f'Index idx_instances_name already exists '
                    f'or could not be added: {e}')

        current_ver = 3
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': INSTANCES_VERSION,
        'migrated': start_ver != current_ver
    }


def _ensure_instance_attributes_schema(
        engine: sa.Engine) -> dict[str, Any]:
    """Ensure the instance_attributes table schema is up to date."""
    table_name = 'instance_attributes'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_instance_attributes_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(
            engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(
                        f'Index {idx.name} creation skipped: {e}')

        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver < 2:
        # Add vsock_cids column (not in original v1 schema). Safe
        # to run repeatedly -- IF NOT EXISTS is a no-op when the
        # column already exists (e.g. new deployments where
        # create_all included it).
        #
        # NOTE: We do NOT bump the version here. The version is
        # bumped by the data migration in ensure_data_migrations()
        # which also migrates instance attribute data from etcd.
        with engine.connect() as conn:
            conn.execute(sa.text(
                'ALTER TABLE instance_attributes '
                'ADD COLUMN IF NOT EXISTS vsock_cids JSON NULL'))
            conn.commit()

    if current_ver < INSTANCE_ATTRIBUTES_VERSION:
        # Phase 7: drop the cached per-instance NI UUID list. The
        # ``Instance.interfaces`` property now queries
        # network_interfaces live via an indexed
        # ``WHERE instance_uuid = ?``. ``DROP COLUMN IF EXISTS`` is
        # idempotent so a re-run on a fresh deployment (where
        # ``create_all`` never created the column) is a no-op.
        LOG.info(
            f'Upgrading {table_name} table to version '
            f'{INSTANCE_ATTRIBUTES_VERSION} '
            '(drop cached interfaces column)')
        with engine.connect() as conn:
            try:
                conn.execute(sa.text(
                    'ALTER TABLE instance_attributes '
                    'DROP COLUMN IF EXISTS interfaces'))
                conn.commit()
            except (IntegrityError, OperationalError) as e:
                LOG.debug(f'Column interfaces drop skipped: {e}')
        current_ver = INSTANCE_ATTRIBUTES_VERSION
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': INSTANCE_ATTRIBUTES_VERSION,
        'migrated': start_ver != current_ver
    }


# =============================================================================
# Instance Direct Access Functions
# These are used by the database daemon for Instance object storage.
# =============================================================================

def _direct_create_instance(data: InstanceData) -> bool:
    """Create an Instance record in MariaDB.

    Args:
        data: The InstanceData to insert.

    Returns:
        True if created successfully, False if duplicate or error.
    """
    engine = _get_engine()
    table = _get_instances_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                cpus=data.cpus,
                disk_spec=_json_dumps(data.disk_spec),
                memory=data.memory,
                name=data.name,
                namespace=data.namespace,
                requested_placement=_json_dumps(
                    data.requested_placement),
                ssh_key=data.ssh_key or '',
                user_data=data.user_data or '',
                video=_json_dumps(data.video),
                uefi=data.uefi,
                configdrive=data.configdrive,
                nvram_template=data.nvram_template or '',
                secure_boot=data.secure_boot,
                machine_type=data.machine_type,
                side_channels=_json_dumps(data.side_channels),
                version=data.version
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for '
            f'instance {data.uuid}: {e}')
        return False


def _direct_get_instance(
        inst_uuid: UUID) -> Optional[InstanceData]:
    """Get Instance static values from MariaDB.

    Args:
        inst_uuid: The UUID of the Instance.

    Returns:
        An InstanceData object, or None if not found.
    """
    engine = _get_engine()
    table = _get_instances_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.uuid == inst_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            # Parse JSON fields
            disk_spec = result.disk_spec
            if isinstance(disk_spec, str):
                disk_spec = json.loads(disk_spec)

            requested_placement = result.requested_placement
            if isinstance(requested_placement, str):
                requested_placement = json.loads(
                    requested_placement)
            if not requested_placement:
                requested_placement = None

            video = result.video
            if isinstance(video, str):
                video = json.loads(video)

            side_channels = result.side_channels
            if isinstance(side_channels, str):
                side_channels = json.loads(side_channels)

            return InstanceData(
                uuid=result.uuid,
                cpus=result.cpus,
                disk_spec=disk_spec if disk_spec else [],
                memory=result.memory,
                name=result.name,
                namespace=result.namespace,
                requested_placement=requested_placement,
                ssh_key=result.ssh_key or None,
                user_data=result.user_data or None,
                video=video if video else {},
                uefi=result.uefi,
                configdrive=result.configdrive,
                nvram_template=(
                    result.nvram_template or None),
                secure_boot=result.secure_boot,
                machine_type=result.machine_type,
                side_channels=(
                    side_channels if side_channels else []),
                version=result.version
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for '
            f'instance {inst_uuid}: {e}')
        return None


def _direct_get_all_instances() -> list[InstanceData]:
    """Get all Instance static values from MariaDB.

    Returns:
        A list of InstanceData objects.
    """
    engine = _get_engine()
    table = _get_instances_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table)
            rows = conn.execute(stmt).fetchall()

            results = []
            for result in rows:
                disk_spec = result.disk_spec
                if isinstance(disk_spec, str):
                    disk_spec = json.loads(disk_spec)

                requested_placement = (
                    result.requested_placement)
                if isinstance(requested_placement, str):
                    requested_placement = json.loads(
                        requested_placement)
                if not requested_placement:
                    requested_placement = None

                video = result.video
                if isinstance(video, str):
                    video = json.loads(video)

                side_channels = result.side_channels
                if isinstance(side_channels, str):
                    side_channels = json.loads(side_channels)

                results.append(InstanceData(
                    uuid=result.uuid,
                    cpus=result.cpus,
                    disk_spec=disk_spec if disk_spec else [],
                    memory=result.memory,
                    name=result.name,
                    namespace=result.namespace,
                    requested_placement=requested_placement,
                    ssh_key=result.ssh_key or None,
                    user_data=result.user_data or None,
                    video=video if video else {},
                    uefi=result.uefi,
                    configdrive=result.configdrive,
                    nvram_template=(
                        result.nvram_template or None),
                    secure_boot=result.secure_boot,
                    machine_type=result.machine_type,
                    side_channels=(
                        side_channels
                        if side_channels else []),
                    version=result.version
                ))
            return results
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for all instances: {e}')
        return []


def _direct_find_instances(
        criteria: ObjectFilterCriteria) -> list[InstanceData]:
    """Find instances matching the given filter criteria.

    Joins ``instances`` to ``object_states`` and applies the optional
    state/namespace/name filters from ``criteria``. On
    ``OperationalError`` logs the full criteria at WARNING level and
    returns an empty list.
    """
    engine = _get_engine()
    table = _get_instances_table()
    stmt = _build_object_filter_query(
        table, ObjectType.INSTANCE, criteria)

    try:
        with engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()

            results = []
            for result in rows:
                disk_spec = result.disk_spec
                if isinstance(disk_spec, str):
                    disk_spec = json.loads(disk_spec)

                requested_placement = (
                    result.requested_placement)
                if isinstance(requested_placement, str):
                    requested_placement = json.loads(
                        requested_placement)
                if not requested_placement:
                    requested_placement = None

                video = result.video
                if isinstance(video, str):
                    video = json.loads(video)

                side_channels = result.side_channels
                if isinstance(side_channels, str):
                    side_channels = json.loads(side_channels)

                results.append(InstanceData(
                    uuid=result.uuid,
                    cpus=result.cpus,
                    disk_spec=disk_spec if disk_spec else [],
                    memory=result.memory,
                    name=result.name,
                    namespace=result.namespace,
                    requested_placement=requested_placement,
                    ssh_key=result.ssh_key or None,
                    user_data=result.user_data or None,
                    video=video if video else {},
                    uefi=result.uefi,
                    configdrive=result.configdrive,
                    nvram_template=(
                        result.nvram_template or None),
                    secure_boot=result.secure_boot,
                    machine_type=result.machine_type,
                    side_channels=(
                        side_channels
                        if side_channels else []),
                    version=result.version
                ))
            return results
    except OperationalError as e:
        LOG.warning(
            f'MariaDB find failed for instances '
            f'(states={criteria.states!r}, '
            f'namespace={criteria.namespace!r}, '
            f'name={criteria.name!r}): {e}')
        return []


def _direct_get_all_instance_uuids() -> list[str]:
    """Get all instance UUIDs from MariaDB.

    Returns only UUIDs (not full records) for efficient enumeration.
    """
    engine = _get_engine()
    table = _get_instances_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table.c.uuid)
            result = conn.execute(stmt)
            return [str(row[0]) for row in result]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get_all_instance_uuids failed: {e}')
        return []


def _direct_delete_instance(inst_uuid: UUID) -> bool:
    """Delete an Instance record from MariaDB.

    Args:
        inst_uuid: The UUID of the Instance to delete.

    Returns:
        True if deleted, False if not found or error.
    """
    engine = _get_engine()
    table = _get_instances_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.uuid == inst_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for '
            f'instance {inst_uuid}: {e}')
        return False


def _direct_create_instance_attributes(
        data: InstanceAttributesData) -> bool:
    """Create an instance_attributes record in MariaDB."""
    engine = _get_engine()
    table = _get_instance_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=data.uuid,
                placement=_json_dumps(data.placement),
                power_state=_json_dumps(data.power_state),
                ports=_json_dumps(data.ports),
                enforced_deletes=_json_dumps(
                    data.enforced_deletes),
                block_devices=_json_dumps(
                    data.block_devices),
                agent_state=_json_dumps(data.agent_state),
                agent_attributes=_json_dumps(
                    data.agent_attributes),
                agent_operations=_json_dumps(
                    data.agent_operations),
                kvm_pid=data.kvm_pid,
                error_message=data.error_message or '',
                vsock_cids=_json_dumps(data.vsock_cids))
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB create failed for '
            f'instance_attributes {data.uuid}: {e}')
        return False


def _direct_get_instance_attributes(
        inst_uuid: UUID
) -> Optional[InstanceAttributesData]:
    """Get Instance attributes from MariaDB."""
    engine = _get_engine()
    table = _get_instance_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.uuid == inst_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            def _parse_json(val: Any) -> Any:
                if isinstance(val, str):
                    return json.loads(val) if val else None
                return val

            placement = _parse_json(result.placement)
            power_state = _parse_json(result.power_state)
            ports = _parse_json(result.ports)
            enforced_deletes = _parse_json(
                result.enforced_deletes)
            block_devices = _parse_json(
                result.block_devices)
            agent_state = _parse_json(result.agent_state)
            agent_attributes = _parse_json(
                result.agent_attributes)
            agent_operations = _parse_json(
                result.agent_operations)
            vsock_cids = _parse_json(result.vsock_cids)

            return InstanceAttributesData(
                uuid=result.uuid,
                placement=placement,
                power_state=power_state,
                ports=ports,
                enforced_deletes=enforced_deletes,
                block_devices=block_devices,
                agent_state=agent_state,
                agent_attributes=agent_attributes,
                agent_operations=agent_operations,
                kvm_pid=result.kvm_pid,
                error_message=(
                    result.error_message or None),
                vsock_cids=vsock_cids,
            )
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for '
            f'instance_attributes {inst_uuid}: {e}')
        return None


def _direct_update_instance_attributes(
        data: InstanceAttributesData) -> bool:
    """Update Instance attributes in MariaDB."""
    engine = _get_engine()
    table = _get_instance_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == data.uuid
            ).values(
                placement=_json_dumps(data.placement),
                power_state=_json_dumps(data.power_state),
                ports=_json_dumps(data.ports),
                enforced_deletes=_json_dumps(
                    data.enforced_deletes),
                block_devices=_json_dumps(
                    data.block_devices),
                agent_state=_json_dumps(data.agent_state),
                agent_attributes=_json_dumps(
                    data.agent_attributes),
                agent_operations=_json_dumps(
                    data.agent_operations),
                kvm_pid=data.kvm_pid,
                error_message=data.error_message or '',
                vsock_cids=_json_dumps(data.vsock_cids))
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB update failed for '
            f'instance_attributes {data.uuid}: {e}')
        return False


def _direct_delete_instance_attributes(
        inst_uuid: UUID) -> bool:
    """Delete Instance attributes from MariaDB."""
    engine = _get_engine()
    table = _get_instance_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.uuid == inst_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for '
            f'instance_attributes {inst_uuid}: {e}')
        return False


def _direct_get_consumed_ports_for_node(
        node_uuid: str) -> list[int]:
    """Get all consumed console/VDI ports for instances on a node.

    Pushes both the placement filter and the port extraction into
    MariaDB. JSON_VALUE(placement, '$.node') is compared against
    the target node UUID in the WHERE clause so we never load rows
    for instances on other nodes, and the three port fields are
    extracted directly from the ports JSON column rather than
    being parsed in Python. Per the project guidance in CLAUDE.md,
    object/attribute filtering is pushed down to the SQL layer so
    it can later benefit from a generated-column index if port
    allocation becomes a hotspot.
    """
    engine = _get_engine()
    consumed: list[int] = []

    try:
        with engine.connect() as conn:
            stmt = sa.text('''
                SELECT
                    CAST(JSON_VALUE(ports, '$.console_port')
                         AS UNSIGNED) AS console_port,
                    CAST(JSON_VALUE(ports, '$.vdi_port')
                         AS UNSIGNED) AS vdi_port,
                    CAST(JSON_VALUE(ports, '$.vdi_tls_port')
                         AS UNSIGNED) AS vdi_tls_port
                FROM instance_attributes
                WHERE ports IS NOT NULL
                  AND placement IS NOT NULL
                  AND JSON_VALUE(placement, '$.node') = :node_uuid
            ''')
            for row in conn.execute(stmt, {'node_uuid': node_uuid}):
                for value in (row.console_port, row.vdi_port,
                              row.vdi_tls_port):
                    if value:
                        consumed.append(int(value))
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for consumed ports '
            f'on node {node_uuid}: {e}')
    return consumed


def _direct_is_vsock_cid_in_use(cid: int) -> bool:
    """Check if a vsock CID is in use by any instance.

    Pushes the search into MariaDB using JSON_CONTAINS over the
    extracted top-level values, so we don't need to deserialize and
    scan every vsock_cids row in Python. The vsock_cids column is a
    JSON object of the form {channel: cid, ...}; JSON_EXTRACT with
    the '$.*' path returns a JSON array of all top-level values,
    which JSON_CONTAINS can then test for the candidate CID.

    The candidate CID is passed as its decimal string form, which
    is itself valid JSON for a number. MariaDB does not implement
    CAST(... AS JSON) (its JSON type is an alias for LONGTEXT), so
    we hand JSON_CONTAINS a pre-serialised JSON literal directly.

    Returns True on database error as a fail-safe: a false positive
    just means the caller picks another CID from a 4-billion-wide
    range, while a false negative could allow two instances to grab
    the same CID.
    """
    engine = _get_engine()

    try:
        with engine.connect() as conn:
            stmt = sa.text('''
                SELECT 1 FROM instance_attributes
                WHERE vsock_cids IS NOT NULL
                  AND JSON_CONTAINS(
                          JSON_EXTRACT(vsock_cids, '$.*'),
                          :cid_json)
                LIMIT 1
            ''')
            result = conn.execute(stmt, {'cid_json': str(cid)}).first()
            return result is not None
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for vsock CID '
            f'{cid}: {e} (returning in_use=True as fail-safe)')
        return True


# =============================================================================
# Instance gRPC Client Functions
# These call the database microservice for Instance operations.
# =============================================================================

def _grpc_create_instance(data: InstanceData) -> bool:
    """Create an Instance record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateInstanceRequest(
            data=database_pb2.InstanceStaticData(
                uuid=str(data.uuid),
                cpus=data.cpus,
                disk_spec_json=_json_dumps(data.disk_spec),
                memory=data.memory,
                name=data.name,
                namespace=data.namespace,
                requested_placement_json=_json_dumps(
                    data.requested_placement),
                ssh_key=data.ssh_key or '',
                user_data=data.user_data or '',
                video_json=_json_dumps(data.video),
                uefi=data.uefi,
                configdrive=data.configdrive,
                nvram_template=(
                    data.nvram_template or ''),
                secure_boot=data.secure_boot,
                machine_type=data.machine_type,
                side_channels_json=_json_dumps(
                    data.side_channels),
                version=data.version
            )
        )
        reply = _grpc_call(stub.CreateInstance, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateInstance failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_get_instance(
        inst_uuid: UUID) -> Optional[InstanceData]:
    """Get Instance static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetInstanceRequest(
            uuid=str(inst_uuid))
        reply = _grpc_call(stub.GetInstance, request)
        if not reply.found:
            return None
        d = reply.data
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
            uuid=d.uuid,
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
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetInstance failed for '
            f'{inst_uuid}: {e}')
        return None


def _grpc_get_all_instances() -> list[InstanceData]:
    """Get all Instance static values via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllInstancesRequest()
        reply = _grpc_call(stub.GetAllInstances, request)
        results = []
        for d in reply.instances:
            disk_spec = (json.loads(d.disk_spec_json)
                         if d.disk_spec_json else [])
            requested_placement = (
                json.loads(d.requested_placement_json)
                if d.requested_placement_json else None)
            if not requested_placement:
                requested_placement = None
            video = (json.loads(d.video_json)
                     if d.video_json else {})
            side_channels = (
                json.loads(d.side_channels_json)
                if d.side_channels_json else [])
            results.append(InstanceData(
                uuid=d.uuid,
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
            ))
        return results
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetAllInstances failed: {e}')
        return []


def _grpc_find_instances(
        criteria: ObjectFilterCriteria) -> list[InstanceData]:
    """Find instances matching criteria via the database microservice."""
    try:
        stub = _get_database_stub()
        proto_criteria = database_pb2.ObjectFilterCriteria(
            states=criteria.states if criteria.states is not None else []
        )
        if criteria.namespace is not None:
            proto_criteria.namespace = criteria.namespace
        if criteria.name is not None:
            proto_criteria.name = criteria.name
        request = database_pb2.FindInstancesRequest(
            criteria=proto_criteria)
        reply = _grpc_call(stub.FindInstances, request)
        results = []
        for d in reply.instances:
            disk_spec = (json.loads(d.disk_spec_json)
                         if d.disk_spec_json else [])
            requested_placement = (
                json.loads(d.requested_placement_json)
                if d.requested_placement_json else None)
            if not requested_placement:
                requested_placement = None
            video = (json.loads(d.video_json)
                     if d.video_json else {})
            side_channels = (
                json.loads(d.side_channels_json)
                if d.side_channels_json else [])
            results.append(InstanceData(
                uuid=d.uuid,
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
            ))
        return results
    except grpc.RpcError as e:
        LOG.error(f'gRPC FindInstances failed: {e}')
        return []


def _grpc_get_all_instance_uuids() -> list[str]:
    """Get all instance UUIDs via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllInstanceUuidsRequest()
        reply = _grpc_call(stub.GetAllInstanceUuids, request)
        return list(reply.uuids)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetAllInstanceUuids failed: {e}')
        return []


def _grpc_delete_instance(inst_uuid: UUID) -> bool:
    """Delete an Instance record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteInstanceRequest(
            uuid=str(inst_uuid))
        reply = _grpc_call(stub.DeleteInstance, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteInstance failed for '
            f'{inst_uuid}: {e}')
        return False


def _grpc_create_instance_attributes(
        data: InstanceAttributesData) -> bool:
    """Create Instance attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateInstanceAttributesRequest(
            data=database_pb2.InstanceAttributesProto(
                uuid=str(data.uuid),
                placement_json=_json_dumps(
                    data.placement),
                power_state_json=_json_dumps(
                    data.power_state),
                ports_json=_json_dumps(data.ports),
                enforced_deletes_json=_json_dumps(
                    data.enforced_deletes),
                block_devices_json=_json_dumps(
                    data.block_devices),
                agent_state_json=_json_dumps(
                    data.agent_state),
                agent_attributes_json=_json_dumps(
                    data.agent_attributes),
                agent_operations_json=_json_dumps(
                    data.agent_operations),
                kvm_pid=data.kvm_pid or 0,
                error_message=(
                    data.error_message or ''),
                vsock_cids_json=_json_dumps(
                    data.vsock_cids)))
        reply = _grpc_call(stub.CreateInstanceAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC CreateInstanceAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_get_instance_attributes(
        inst_uuid: UUID
) -> Optional[InstanceAttributesData]:
    """Get Instance attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetInstanceAttributesRequest(
            uuid=str(inst_uuid))
        reply = _grpc_call(stub.GetInstanceAttributes, request)
        if not reply.found:
            return None
        d = reply.data

        def _parse(val: str) -> Any:
            return json.loads(val) if val else None

        return InstanceAttributesData(
            uuid=d.uuid,
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
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetInstanceAttributes failed for '
            f'{inst_uuid}: {e}')
        return None


def _grpc_update_instance_attributes(
        data: InstanceAttributesData) -> bool:
    """Update Instance attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateInstanceAttributesRequest(
            data=database_pb2.InstanceAttributesProto(
                uuid=str(data.uuid),
                placement_json=_json_dumps(
                    data.placement),
                power_state_json=_json_dumps(
                    data.power_state),
                ports_json=_json_dumps(data.ports),
                enforced_deletes_json=_json_dumps(
                    data.enforced_deletes),
                block_devices_json=_json_dumps(
                    data.block_devices),
                agent_state_json=_json_dumps(
                    data.agent_state),
                agent_attributes_json=_json_dumps(
                    data.agent_attributes),
                agent_operations_json=_json_dumps(
                    data.agent_operations),
                kvm_pid=data.kvm_pid or 0,
                error_message=(
                    data.error_message or ''),
                vsock_cids_json=_json_dumps(
                    data.vsock_cids)))
        reply = _grpc_call(stub.UpdateInstanceAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC UpdateInstanceAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_delete_instance_attributes(
        inst_uuid: UUID) -> bool:
    """Delete Instance attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteInstanceAttributesRequest(
            uuid=str(inst_uuid))
        reply = _grpc_call(stub.DeleteInstanceAttributes, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC DeleteInstanceAttributes failed for '
            f'{inst_uuid}: {e}')
        return False


def _grpc_get_consumed_ports_for_node(
        node_uuid: str) -> list[int]:
    """Get consumed ports for a node via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetConsumedPortsForNodeRequest(
            node_uuid=node_uuid)
        reply = _grpc_call(stub.GetConsumedPortsForNode, request)
        return list(reply.ports)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC GetConsumedPortsForNode failed for '
            f'{node_uuid}: {e}')
        return []


def _grpc_is_vsock_cid_in_use(cid: int) -> bool:
    """Check if a vsock CID is in use via the database service.

    Returns True on RPC failure as a fail-safe so the caller picks
    another CID from the 4-billion-wide range rather than risking a
    duplicate allocation.
    """
    try:
        stub = _get_database_stub()
        request = database_pb2.IsVsockCidInUseRequest(cid=cid)
        reply = _grpc_call(stub.IsVsockCidInUse, request)
        return bool(reply.in_use)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC IsVsockCidInUse failed for CID {cid}: {e} '
            f'(returning in_use=True as fail-safe)')
        return True


# =============================================================================
# Instance Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def create_instance(data: InstanceData) -> bool:
    """Create an Instance record.

    Args:
        data: The InstanceData to insert.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_instance(data)
    return _direct_create_instance(data)


def get_instance(
        inst_uuid: UUID) -> Optional[InstanceData]:
    """Get Instance static values.

    Args:
        inst_uuid: The UUID of the Instance.

    Returns:
        An InstanceData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_instance(inst_uuid)
    return _direct_get_instance(inst_uuid)


def get_all_instances() -> list[InstanceData]:
    """Get all Instance static values.

    Returns:
        A list of InstanceData objects.
    """
    if _use_database_service():
        return _grpc_get_all_instances()
    return _direct_get_all_instances()


def find_instances(
        criteria: ObjectFilterCriteria) -> list[InstanceData]:
    """Find instances matching the given filter criteria.

    Args:
        criteria: Filter criteria (states, namespace, name).

    Returns:
        List of matching InstanceData objects.
    """
    if _use_database_service():
        return _grpc_find_instances(criteria)
    return _direct_find_instances(criteria)


def get_all_instance_uuids() -> list[str]:
    """Get all instance UUIDs.

    Returns only UUIDs (not full records) for efficient enumeration.
    """
    if _use_database_service():
        return _grpc_get_all_instance_uuids()
    return _direct_get_all_instance_uuids()


def delete_instance(inst_uuid: UUID) -> bool:
    """Delete an Instance record.

    Args:
        inst_uuid: The UUID of the Instance.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_instance(inst_uuid)
    return _direct_delete_instance(inst_uuid)


def create_instance_attributes(
        data: InstanceAttributesData) -> bool:
    """Create Instance attributes record.

    Args:
        data: The InstanceAttributesData to create.

    Returns:
        True if created, False if already exists or error.
    """
    if _use_database_service():
        return _grpc_create_instance_attributes(data)
    return _direct_create_instance_attributes(data)


def get_instance_attributes(
        inst_uuid: UUID
) -> Optional[InstanceAttributesData]:
    """Get Instance attributes.

    Args:
        inst_uuid: The UUID of the Instance.

    Returns:
        An InstanceAttributesData object, or None if not found.
    """
    if _use_database_service():
        return _grpc_get_instance_attributes(inst_uuid)
    return _direct_get_instance_attributes(inst_uuid)


def update_instance_attributes(
        data: InstanceAttributesData) -> bool:
    """Update Instance attributes.

    Args:
        data: The InstanceAttributesData with updated values.

    Returns:
        True if updated, False if not found or error.
    """
    if _use_database_service():
        return _grpc_update_instance_attributes(data)
    return _direct_update_instance_attributes(data)


def delete_instance_attributes(inst_uuid: UUID) -> bool:
    """Delete Instance attributes.

    Args:
        inst_uuid: The UUID of the Instance.

    Returns:
        True if deleted, False if not found or error.
    """
    if _use_database_service():
        return _grpc_delete_instance_attributes(inst_uuid)
    return _direct_delete_instance_attributes(inst_uuid)


def get_consumed_ports_for_node(node_uuid: str) -> list[int]:
    """Get all consumed console/VDI ports for instances on a node.

    Args:
        node_uuid: The UUID of the node.

    Returns:
        List of consumed port numbers.
    """
    if _use_database_service():
        return _grpc_get_consumed_ports_for_node(node_uuid)
    return _direct_get_consumed_ports_for_node(node_uuid)


def is_vsock_cid_in_use(cid: int) -> bool:
    """Check if a vsock CID is in use by any instance.

    Args:
        cid: The vsock CID to check.

    Returns:
        True if the CID is in use.
    """
    if _use_database_service():
        return _grpc_is_vsock_cid_in_use(cid)
    return _direct_is_vsock_cid_in_use(cid)


# =============================================================================
# Node Metrics Direct Access Functions
# Ephemeral per-node resource metrics, upserted every 60s.
# =============================================================================

def _direct_upsert_node_metrics(
        node_uuid: UUID, fqdn: str, timestamp: float,
        metrics: dict[str, Any]) -> bool:
    """Upsert a node metrics record in MariaDB.

    Uses INSERT ... ON DUPLICATE KEY UPDATE because metrics are
    updated every 60 seconds — there is no separate create path.
    """
    engine = _get_engine()
    table = _get_node_metrics_table()

    try:
        with engine.connect() as conn:
            stmt = sa.dialects.mysql.insert(table).values(
                node_uuid=node_uuid,
                fqdn=fqdn,
                timestamp=timestamp,
                metrics_json=metrics
            )
            stmt = stmt.on_duplicate_key_update(
                fqdn=fqdn,
                timestamp=timestamp,
                metrics_json=metrics
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(f'MariaDB upsert failed for node_metrics {node_uuid}: {e}')
        return False


def _direct_get_node_metrics(node_uuid: UUID) -> Optional[dict[str, Any]]:
    """Get node metrics from MariaDB.

    Returns a dict matching the legacy etcd structure:
    {'node_uuid': ..., 'fqdn': ..., 'timestamp': ..., 'metrics': {...}}
    """
    engine = _get_engine()
    table = _get_node_metrics_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.node_uuid == node_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return {
                'node_uuid': str(result.node_uuid),
                'fqdn': result.fqdn,
                'timestamp': result.timestamp,
                'metrics': result.metrics_json or {}
            }
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for node_metrics {node_uuid}: {e}')
        return None


def _direct_get_all_node_metrics() -> list[dict[str, Any]]:
    """Get all node metrics from MariaDB."""
    engine = _get_engine()
    table = _get_node_metrics_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table)
            result = conn.execute(stmt).fetchall()

            return [
                {
                    'node_uuid': str(row.node_uuid),
                    'fqdn': row.fqdn,
                    'timestamp': row.timestamp,
                    'metrics': row.metrics_json or {}
                }
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for all node_metrics: {e}')
        return []


def _direct_delete_node_metrics(node_uuid: UUID) -> bool:
    """Delete a node metrics record from MariaDB."""
    engine = _get_engine()
    table = _get_node_metrics_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.node_uuid == node_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for node_metrics {node_uuid}: {e}')
        return False


# =============================================================================
# Node Metrics gRPC Client Functions
# =============================================================================

def _grpc_upsert_node_metrics(
        node_uuid: UUID, fqdn: str, timestamp: float,
        metrics: dict[str, Any]) -> bool:
    """Upsert a node metrics record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpsertNodeMetricsRequest(
            data=database_pb2.NodeMetricsData(
                node_uuid=str(node_uuid),
                fqdn=fqdn,
                timestamp=timestamp,
                metrics_json=_json_dumps(metrics)
            )
        )
        reply = _grpc_call(stub.UpsertNodeMetrics, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpsertNodeMetrics failed for {node_uuid}: {e}')
        return False


def _grpc_get_node_metrics(node_uuid: UUID) -> Optional[dict[str, Any]]:
    """Get node metrics via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNodeMetricsRequest(
            node_uuid=str(node_uuid))
        reply = _grpc_call(stub.GetNodeMetrics, request)
        if not reply.found:
            return None
        return {
            'node_uuid': reply.data.node_uuid,
            'fqdn': reply.data.fqdn,
            'timestamp': reply.data.timestamp,
            'metrics': json.loads(reply.data.metrics_json)
            if reply.data.metrics_json else {}
        }
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetNodeMetrics failed for {node_uuid}: {e}')
        return None


def _grpc_get_all_node_metrics() -> list[dict[str, Any]]:
    """Get all node metrics via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllNodeMetricsRequest()
        reply = _grpc_call(stub.GetAllNodeMetrics, request)
        return [
            {
                'node_uuid': item.node_uuid,
                'fqdn': item.fqdn,
                'timestamp': item.timestamp,
                'metrics': json.loads(item.metrics_json)
                if item.metrics_json else {}
            }
            for item in reply.items
        ]
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetAllNodeMetrics failed: {e}')
        return []


def _grpc_delete_node_metrics(node_uuid: UUID) -> bool:
    """Delete a node metrics record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNodeMetricsRequest(
            node_uuid=str(node_uuid))
        reply = _grpc_call(stub.DeleteNodeMetrics, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteNodeMetrics failed for {node_uuid}: {e}')
        return False


# =============================================================================
# Node Metrics Public API Functions
# =============================================================================

def _ensure_uuid(value: 'str | UUID') -> UUID:
    """Convert a string to UUID if needed."""
    if isinstance(value, UUID):
        return value
    return UUID(value)


def upsert_node_metrics(
        node_uuid: 'str | UUID', fqdn: str, timestamp: float,
        metrics: dict[str, Any]) -> bool:
    """Upsert a node metrics record.

    Args:
        node_uuid: The UUID of the node (str or UUID).
        fqdn: The node's fully qualified domain name.
        timestamp: Unix timestamp of the metrics collection.
        metrics: Dict of metric name -> value pairs.

    Returns:
        True if upserted successfully, False on error.
    """
    u = _ensure_uuid(node_uuid)
    if _use_database_service():
        return _grpc_upsert_node_metrics(u, fqdn, timestamp, metrics)
    return _direct_upsert_node_metrics(u, fqdn, timestamp, metrics)


def get_node_metrics(node_uuid: 'str | UUID') -> Optional[dict[str, Any]]:
    """Get node metrics.

    Args:
        node_uuid: The UUID of the node (str or UUID).

    Returns:
        Dict with keys node_uuid, fqdn, timestamp, metrics; or None.
    """
    u = _ensure_uuid(node_uuid)
    if _use_database_service():
        return _grpc_get_node_metrics(u)
    return _direct_get_node_metrics(u)


def get_all_node_metrics() -> list[dict[str, Any]]:
    """Get all node metrics.

    Returns:
        List of dicts, each with keys node_uuid, fqdn, timestamp,
        metrics.
    """
    if _use_database_service():
        return _grpc_get_all_node_metrics()
    return _direct_get_all_node_metrics()


def delete_node_metrics(node_uuid: 'str | UUID') -> bool:
    """Delete a node metrics record.

    Args:
        node_uuid: The UUID of the node (str or UUID).

    Returns:
        True if deleted, False if not found or error.
    """
    u = _ensure_uuid(node_uuid)
    if _use_database_service():
        return _grpc_delete_node_metrics(u)
    return _direct_delete_node_metrics(u)


# =============================================================================
# Cluster Operations Direct Access Functions
# Operation headers, insert-only. State lives in object_states, per-target
# tracking lives in cluster_operation_targets.
# =============================================================================

def _maybe_uuid(value: Any) -> Optional[UUID]:
    """Convert an optional string/UUID to UUID, returning None for missing."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(value)


def _cluster_operation_row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a cluster_operations row into the legacy-etcd payload shape.

    Phase 5's from_db() switch depends on this shape being identical
    to the dict previously stored at /sf/{operation_type}/{uuid} in
    etcd. The full metadata dict is flattened into the top level,
    with uuid/operation_type/created_at overlaid from the columnar
    fields.
    """
    md = dict(row.metadata_json or {})
    md['uuid'] = str(row.uuid)
    md['operation_type'] = row.operation_type
    md['created_at'] = row.created_at
    return md


def _direct_create_cluster_operation(
        uuid: UUID, operation_type: str, metadata: dict[str, Any],
        created_at: float) -> bool:
    """Insert a cluster operation header in MariaDB.

    Insert-only — returns False if a row with the same uuid already
    exists. Extracts node_uuid, instance_uuid, network_uuid and
    priority from the metadata dict into their own columns for
    indexed lookups. The full metadata dict is stored in
    metadata_json.
    """
    engine = _get_engine()
    table = _get_cluster_operations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=uuid,
                operation_type=operation_type,
                created_at=created_at,
                node_uuid=_maybe_uuid(metadata.get('node_uuid')),
                instance_uuid=_maybe_uuid(metadata.get('instance_uuid')),
                network_uuid=_maybe_uuid(metadata.get('network_uuid')),
                priority=metadata.get('priority'),
                metadata_json=metadata,
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        LOG.warning(
            f'MariaDB insert refused duplicate cluster_operation {uuid}')
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB insert failed for cluster_operation {uuid}: {e}')
        return False


def _direct_get_cluster_operation(
        uuid: UUID) -> Optional[dict[str, Any]]:
    """Get a cluster operation header from MariaDB.

    Returns the full metadata dict with uuid/operation_type/created_at
    overlaid, matching the legacy etcd payload shape. Returns None if
    not found.
    """
    engine = _get_engine()
    table = _get_cluster_operations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == uuid)
            result = conn.execute(stmt).fetchone()
            if result is None:
                return None
            return _cluster_operation_row_to_dict(result)
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get failed for cluster_operation {uuid}: {e}')
        return None


def _direct_get_cluster_operations_by_node(
        node_uuid: UUID) -> list[dict[str, Any]]:
    """Get all cluster operation headers targeting a specific node."""
    engine = _get_engine()
    table = _get_cluster_operations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(
                table.c.node_uuid == node_uuid
            ).order_by(table.c.created_at)
            result = conn.execute(stmt).fetchall()
            return [_cluster_operation_row_to_dict(row) for row in result]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for cluster_operations '
            f'by node {node_uuid}: {e}')
        return []


def _direct_list_cluster_operations_for_target(
        target_object_type: ObjectType,
        target_uuid: str) -> list[dict[str, Any]]:
    """List cluster operation headers targeting an object, newest first.

    Joins ``cluster_operation_targets`` against ``cluster_operations`` to
    return the full op metadata for every operation that has touched the
    given target, ordered by ``cluster_operations.created_at DESC``.
    Namespace scoping is the caller's responsibility (handled in the REST
    layer by validating access to the target object before issuing the
    query).
    """
    engine = _get_engine()
    targets_table = _get_cluster_operation_targets_table()
    ops_table = _get_cluster_operations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(ops_table).select_from(
                targets_table.join(
                    ops_table,
                    targets_table.c.operation_uuid == ops_table.c.uuid
                )
            ).where(
                sa.and_(
                    targets_table.c.target_object_type == target_object_type,
                    targets_table.c.target_uuid == target_uuid
                )
            ).order_by(ops_table.c.created_at.desc())
            result = conn.execute(stmt).fetchall()
            return [_cluster_operation_row_to_dict(row) for row in result]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for list_cluster_operations_for_target '
            f'{target_object_type}/{target_uuid}: {e}')
        return []


def _direct_delete_cluster_operation(uuid: UUID) -> bool:
    """Delete a cluster operation header from MariaDB."""
    engine = _get_engine()
    table = _get_cluster_operations_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for cluster_operation {uuid}: {e}')
        return False


def _direct_create_and_enqueue_cluster_operation(
        op_uuid: UUID,
        operation_type: str,
        metadata: dict[str, Any],
        created_at: float,
        queue_name: str,
        delay: float = 0.0) -> bool:
    """Atomically create a cluster operation and enqueue its work item.

    Writes three rows in a single MariaDB transaction:

    1. A cluster_operations row with the full operation metadata
       and the same indexed-column extraction as
       _direct_create_cluster_operation.
    2. An object_states row (INSERT ... ON DUPLICATE KEY UPDATE)
       with state='queued' and update_time=created_at, matching
       the _direct_set_state contract.
    3. A work_queue row with
       scheduled_at = created_at + delay, attempts = 0, and a
       payload dict of
       {'operation_type': operation_type,
        'operation_uuid': str(op_uuid)} -- the same shape
       phase 5's from_db() consumer will read via Dequeue.

    This is the only function in mariadb.py that writes to more
    than one table in a single transaction. The existing
    single-table _direct_* functions each own their own commit,
    so they cannot be composed here -- the statements are
    duplicated inline instead. That duplication is the price of
    getting the atomicity right.

    Returns True on success. Returns False if the cluster_operations
    insert hits a duplicate uuid (IntegrityError) or if any write
    raises OperationalError -- in both cases the `with` context
    rolls back the uncommitted transaction automatically. Audit
    events are out of scope; callers emit them via eventlog after
    the RPC returns successfully.
    """
    engine = _get_engine()
    cluster_ops_table = _get_cluster_operations_table()
    states_table = _get_object_states_table()
    queue_table = _get_work_queue_table()

    scheduled_at = created_at + delay
    work_item = {
        'operation_type': operation_type,
        'operation_uuid': str(op_uuid),
    }

    try:
        with engine.connect() as conn:
            cluster_stmt = sa.insert(cluster_ops_table).values(
                uuid=op_uuid,
                operation_type=operation_type,
                created_at=created_at,
                node_uuid=_maybe_uuid(metadata.get('node_uuid')),
                instance_uuid=_maybe_uuid(
                    metadata.get('instance_uuid')),
                network_uuid=_maybe_uuid(
                    metadata.get('network_uuid')),
                priority=metadata.get('priority'),
                metadata_json=metadata,
            )
            conn.execute(cluster_stmt)

            state_stmt = sa.dialects.mysql.insert(
                states_table
            ).values(
                object_uuid=str(op_uuid),
                object_type=operation_type,
                state_value='queued',
                update_time=created_at,
                message=None,
            ).on_duplicate_key_update(
                state_value='queued',
                update_time=created_at,
                message=None,
            )
            conn.execute(state_stmt)

            queue_stmt = sa.insert(queue_table).values(
                queue_name=queue_name,
                scheduled_at=scheduled_at,
                claimed_at=None,
                claimed_by=None,
                attempts=0,
                payload=work_item,
                created_at=created_at,
            )
            conn.execute(queue_stmt)

            conn.commit()
            return True
    except IntegrityError:
        LOG.warning(
            f'MariaDB atomic create+enqueue refused duplicate '
            f'cluster_operation {op_uuid}')
        return False
    except OperationalError as e:
        LOG.warning(
            f'MariaDB atomic create+enqueue failed for '
            f'cluster_operation {op_uuid}: {e}')
        return False


def _direct_set_cluster_operation_error(
        op_uuid: UUID, error_report: ErrorReport,
        created_at: float) -> bool:
    """Persist (or replace) the ErrorReport for a cluster operation.

    Uses ``INSERT ... ON DUPLICATE KEY UPDATE`` so a retry that
    fails again overwrites the prior row without the caller needing
    to check-then-write. The dispatcher only writes once per terminal
    failure today, but the upsert keeps the contract easy to reason
    about.

    The ErrorReport is JSON-serialised via Pydantic's
    ``model_dump(mode='json')`` so the dict has only JSON-native
    primitives (no datetimes/UUIDs sneaking through).
    """
    engine = _get_engine()
    table = _get_cluster_operation_errors_table()
    payload = error_report.model_dump(mode='json')

    try:
        with engine.connect() as conn:
            stmt = sa.dialects.mysql.insert(table).values(
                op_uuid=op_uuid,
                error_report=payload,
                created_at=created_at,
            )
            stmt = stmt.on_duplicate_key_update(
                error_report=stmt.inserted.error_report,
                created_at=stmt.inserted.created_at,
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB write failed for cluster_operation_error '
            f'{op_uuid}: {e}')
        return False


def _direct_get_cluster_operation_error(
        op_uuid: UUID) -> Optional[ErrorReport]:
    """Read the ErrorReport for a cluster operation, or None if absent."""
    engine = _get_engine()
    table = _get_cluster_operation_errors_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.op_uuid == op_uuid)
            result = conn.execute(stmt).fetchone()
            if result is None:
                return None
            return ErrorReport.model_validate(result.error_report)
    except OperationalError as e:
        LOG.warning(
            f'MariaDB read failed for cluster_operation_error '
            f'{op_uuid}: {e}')
        return None


def _direct_delete_cluster_operation_error(op_uuid: UUID) -> bool:
    """Delete the cluster_operation_errors row for an op.

    Idempotent: returns True whether or not a row existed, so callers
    can use it from ``hard_delete`` without checking first.
    """
    engine = _get_engine()
    table = _get_cluster_operation_errors_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.op_uuid == op_uuid)
            conn.execute(stmt)
            conn.commit()
            return True
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete failed for cluster_operation_error '
            f'{op_uuid}: {e}')
        return False


# =============================================================================
# Cluster Operations gRPC Client Functions
# =============================================================================

def _grpc_create_cluster_operation(
        uuid: UUID, operation_type: str, metadata: dict[str, Any],
        created_at: float) -> bool:
    """Insert a cluster operation header via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.CreateClusterOperationRequest(
            data=database_pb2.ClusterOperationData(
                uuid=str(uuid),
                operation_type=operation_type,
                created_at=created_at,
                metadata_json=_json_dumps(metadata),
            )
        )
        reply = _grpc_call(stub.CreateClusterOperation, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC CreateClusterOperation failed for {uuid}: {e}')
        return False


def _grpc_get_cluster_operation(
        uuid: UUID) -> Optional[dict[str, Any]]:
    """Get a cluster operation header via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetClusterOperationRequest(
            uuid=str(uuid))
        reply = _grpc_call(stub.GetClusterOperation, request)
        if not reply.found:
            return None
        return (
            json.loads(reply.data.metadata_json)
            if reply.data.metadata_json else {}
        )
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC GetClusterOperation failed for {uuid}: {e}')
        return None


def _grpc_get_cluster_operations_by_node(
        node_uuid: UUID) -> list[dict[str, Any]]:
    """Get all cluster operation headers for a node via gRPC."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetClusterOperationsByNodeRequest(
            node_uuid=str(node_uuid))
        reply = _grpc_call(
            stub.GetClusterOperationsByNode, request)
        return [
            json.loads(item.metadata_json)
            if item.metadata_json else {}
            for item in reply.items
        ]
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC GetClusterOperationsByNode failed '
            f'for {node_uuid}: {e}')
        return []


def _grpc_list_cluster_operations_for_target(
        target_object_type: ObjectType,
        target_uuid: str) -> list[dict[str, Any]]:
    """List cluster operation headers targeting an object via gRPC.

    Items are returned newest-first by ``created_at``.
    """
    try:
        stub = _get_database_stub()
        request = database_pb2.ListClusterOperationsForTargetRequest(
            target_object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_object_type.proto_id),
            target_uuid=target_uuid,
        )
        reply = _grpc_call(
            stub.ListClusterOperationsForTarget, request)
        return [
            json.loads(item.metadata_json)
            if item.metadata_json else {}
            for item in reply.items
        ]
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC ListClusterOperationsForTarget failed for '
            f'{target_object_type}/{target_uuid}: {e}')
        return []


def _grpc_delete_cluster_operation(uuid: UUID) -> bool:
    """Delete a cluster operation header via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteClusterOperationRequest(
            uuid=str(uuid))
        reply = _grpc_call(stub.DeleteClusterOperation, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC DeleteClusterOperation failed for {uuid}: {e}')
        return False


def _grpc_create_and_enqueue_cluster_operation(
        op_uuid: UUID,
        operation_type: str,
        metadata: dict[str, Any],
        created_at: float,
        queue_name: str,
        delay: float = 0.0) -> bool:
    """Atomic create+enqueue via the database microservice."""
    try:
        stub = _get_database_stub()
        request = (
            database_pb2
            .CreateAndEnqueueClusterOperationRequest(
                uuid=str(op_uuid),
                operation_type=operation_type,
                created_at=created_at,
                queue_name=queue_name,
                delay=delay,
                metadata_json=_json_dumps(metadata),
            )
        )
        reply = _grpc_call(
            stub.CreateAndEnqueueClusterOperation, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC CreateAndEnqueueClusterOperation failed for '
            f'{op_uuid}: {e}')
        return False


def _grpc_set_cluster_operation_error(
        op_uuid: UUID, error_report: ErrorReport,
        created_at: float) -> bool:
    """Persist the ErrorReport for an operation via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.SetClusterOperationErrorRequest(
            op_uuid=str(op_uuid),
            error_report_json=_json_dumps(
                error_report.model_dump(mode='json')),
            created_at=created_at,
        )
        reply = _grpc_call(stub.SetClusterOperationError, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC SetClusterOperationError failed for '
            f'{op_uuid}: {e}')
        return False


def _grpc_get_cluster_operation_error(
        op_uuid: UUID) -> Optional[ErrorReport]:
    """Read the ErrorReport for an operation via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetClusterOperationErrorRequest(
            op_uuid=str(op_uuid))
        reply = _grpc_call(stub.GetClusterOperationError, request)
        if not reply.found:
            return None
        return ErrorReport.model_validate(
            json.loads(reply.error_report_json))
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC GetClusterOperationError failed for '
            f'{op_uuid}: {e}')
        return None


def _grpc_delete_cluster_operation_error(op_uuid: UUID) -> bool:
    """Delete an ErrorReport row via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteClusterOperationErrorRequest(
            op_uuid=str(op_uuid))
        reply = _grpc_call(stub.DeleteClusterOperationError, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC DeleteClusterOperationError failed for '
            f'{op_uuid}: {e}')
        return False


# =============================================================================
# Work Queue gRPC Client Functions
# =============================================================================

def _grpc_work_queue_enqueue(
        queue_name: str, work_item: dict[str, Any],
        delay: float = 0.0) -> None:
    """Enqueue a work item via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.EnqueueRequest(
            queue_name=queue_name,
            work_item=_json_dumps(work_item),
            delay=delay,
        )
        reply = _grpc_call(stub.Enqueue, request)
        if not reply.success:
            LOG.error(
                f'gRPC Enqueue failed for {queue_name}: '
                f'{reply.error}')
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC Enqueue failed for {queue_name}: {e}')


def _grpc_work_queue_dequeue_batch(
        queue_names: list[str],
        limit: int) -> list[tuple[str, str, dict[str, Any]]]:
    """Claim up to ``limit`` available jobs via the database microservice.

    The database daemon uses its own NODE_NAME as worker_id; the gRPC
    contract does not carry a caller-supplied worker_id. Returns a
    list of ``(queue_name, job_name, payload)`` tuples in the
    server-supplied priority order (top-priority first).
    """
    if not queue_names or limit <= 0:
        return []
    try:
        stub = _get_database_stub()
        request = database_pb2.DequeueRequest(
            queue_names=queue_names, limit=limit)
        reply = _grpc_call(stub.Dequeue, request)
        return [
            (item.queue_name, item.job_name, json.loads(item.work_item))
            for item in reply.items
        ]
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC Dequeue failed across {len(queue_names)} queues: {e}')
        return []


def _grpc_work_queue_resolve(
        queue_name: str, job_name: str) -> None:
    """Mark a job complete via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.ResolveRequest(
            queue_name=queue_name, job_name=job_name)
        reply = _grpc_call(stub.Resolve, request)
        if not reply.success:
            LOG.error(
                f'gRPC Resolve failed for {queue_name}/{job_name}: '
                f'{reply.error}')
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC Resolve failed for {queue_name}/{job_name}: {e}')


def _grpc_work_queue_length(
        queue_name: str) -> tuple[int, int, int]:
    """Get queue length statistics via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.QueueLengthRequest(queue_name=queue_name)
        reply = _grpc_call(stub.GetQueueLength, request)
        return (
            int(reply.processing),
            int(reply.queued),
            int(reply.deferred),
        )
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC GetQueueLength failed for {queue_name}: {e}')
        return 0, 0, 0


def _grpc_work_queue_restart(queue_name: str) -> None:
    """Clear claims on a queue via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.RestartQueueRequest(queue_name=queue_name)
        reply = _grpc_call(stub.RestartQueue, request)
        if not reply.success:
            LOG.error(
                f'gRPC RestartQueue failed for {queue_name}: '
                f'{reply.error}')
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC RestartQueue failed for {queue_name}: {e}')


def _grpc_work_queue_list_stuck(
        threshold_seconds: float) -> list[dict[str, Any]]:
    """List stuck work_queue rows via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.ListStuckWorkQueueRowsRequest(
            threshold_seconds=threshold_seconds)
        reply = _grpc_call(stub.ListStuckWorkQueueRows, request)
        return [
            {
                'id': int(row.id),
                'queue_name': row.queue_name,
                'claimed_at': float(row.claimed_at),
                'claimed_by': row.claimed_by,
                'attempts': int(row.attempts),
                'payload': (
                    json.loads(row.payload_json)
                    if row.payload_json else {}),
            }
            for row in reply.rows
        ]
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC ListStuckWorkQueueRows failed: {e}')
        return []


def _grpc_work_queue_clear_claim(row_id: int) -> bool:
    """Clear a stuck claim via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.ClearWorkQueueClaimRequest(
            row_id=row_id)
        reply = _grpc_call(stub.ClearWorkQueueClaim, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC ClearWorkQueueClaim failed for row '
            f'{row_id}: {e}')
        return False


def _grpc_work_queue_delete_row(row_id: int) -> bool:
    """Delete a stuck work_queue row via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteWorkQueueRowRequest(
            row_id=row_id)
        reply = _grpc_call(stub.DeleteWorkQueueRow, request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC DeleteWorkQueueRow failed for row '
            f'{row_id}: {e}')
        return False


def _grpc_find_existing_coalescible_op(
        operation_type: str,
        target_column: str,
        target_uuid: str,
        task_name: str) -> Optional[str]:
    """Read-only enqueue-side dedup lookup via the database microservice.

    See ``_direct_find_existing_coalescible_op`` for semantics. A
    return of ``None`` either means no match or an RPC failure;
    enqueue callers fall back to inserting a new op in both cases
    (the dispatcher folds the duplicate at execute time).
    """
    try:
        stub = _get_database_stub()
        request = database_pb2.FindExistingCoalescibleOpRequest(
            operation_type=operation_type,
            target_column=target_column,
            target_uuid=target_uuid,
            task_name=task_name)
        reply = _grpc_call(stub.FindExistingCoalescibleOp, request)
        if not reply.op_uuid:
            return None
        return str(reply.op_uuid)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC FindExistingCoalescibleOp failed for '
            f'{operation_type}/{target_column}={target_uuid}/{task_name}: '
            f'{e}')
        return None


def _grpc_claim_coalescible_siblings(
        operation_type: str,
        target_column: str,
        target_uuid: str,
        task_names: list[str],
        exclude_op_uuid: str) -> list[str]:
    """Fold sibling pending coalescible ops via the database microservice.

    Returns the list of uuids that were folded (state transitioned
    to ``complete``). See
    ``_direct_claim_coalescible_siblings`` for the safety guards.
    """
    if not task_names:
        return []
    try:
        stub = _get_database_stub()
        request = database_pb2.ClaimCoalescibleSiblingsRequest(
            operation_type=operation_type,
            target_column=target_column,
            target_uuid=target_uuid,
            task_names=task_names,
            exclude_op_uuid=exclude_op_uuid)
        reply = _grpc_call(stub.ClaimCoalescibleSiblings, request)
        return list(reply.folded_op_uuids)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC ClaimCoalescibleSiblings failed for '
            f'{operation_type}/{target_column}={target_uuid}: {e}')
        return []


# =============================================================================
# Cluster Operations Public API Functions
# =============================================================================

def create_cluster_operation(
        uuid: 'str | UUID', operation_type: str,
        metadata: dict[str, Any], created_at: float) -> bool:
    """Insert a cluster operation header.

    Insert-only semantics: returns False if a row with the same uuid
    already exists (no update). State lives in the separate
    object_states table and is not touched by this function.

    Args:
        uuid: The operation's UUID (str or UUID).
        operation_type: Operation type name, e.g. 'instance_preflight'.
        metadata: The full operation metadata dict. node_uuid,
            instance_uuid, network_uuid and priority (if present) are
            extracted into indexed columns at insert time.
        created_at: Unix timestamp of operation creation.

    Returns:
        True if inserted, False on duplicate or error.
    """
    u = _ensure_uuid(uuid)
    if _use_database_service():
        return _grpc_create_cluster_operation(
            u, operation_type, metadata, created_at)
    return _direct_create_cluster_operation(
        u, operation_type, metadata, created_at)


def get_cluster_operation(
        uuid: 'str | UUID') -> Optional[dict[str, Any]]:
    """Get a cluster operation header.

    Returns a dict shaped {**metadata_json, uuid, operation_type,
    created_at} -- a drop-in replacement for the legacy etcd payload
    that phase 5's from_db() switch relies on.

    Args:
        uuid: The operation's UUID (str or UUID).

    Returns:
        The operation dict, or None if not found.
    """
    u = _ensure_uuid(uuid)
    if _use_database_service():
        return _grpc_get_cluster_operation(u)
    return _direct_get_cluster_operation(u)


def get_cluster_operations_by_node(
        node_uuid: 'str | UUID') -> list[dict[str, Any]]:
    """Get all cluster operation headers targeting a specific node.

    Results are ordered by created_at ascending.

    Args:
        node_uuid: The UUID of the target node (str or UUID).

    Returns:
        List of operation dicts (possibly empty).
    """
    u = _ensure_uuid(node_uuid)
    if _use_database_service():
        return _grpc_get_cluster_operations_by_node(u)
    return _direct_get_cluster_operations_by_node(u)


def list_cluster_operations_for_target(
        target_object_type: ObjectType,
        target_uuid: str) -> list[dict[str, Any]]:
    """List cluster operation headers targeting an object, newest-first.

    Joins ``cluster_operation_targets`` against ``cluster_operations``
    to return the full op metadata for every operation that has touched
    the given target. Ordered by ``cluster_operations.created_at DESC``.

    Namespace scoping is the caller's responsibility. The REST handler
    that consumes this helper validates the caller's access to the
    target object before issuing the query (Approach (b) from the
    Phase 7 plan), so this function does no namespace filtering itself.

    Args:
        target_object_type: The ObjectType of the target object.
        target_uuid: UUID of the target object.

    Returns:
        List of operation dicts ordered newest-first by ``created_at``.
    """
    if _use_database_service():
        return _grpc_list_cluster_operations_for_target(
            target_object_type, target_uuid)
    return _direct_list_cluster_operations_for_target(
        target_object_type, target_uuid)


def delete_cluster_operation(uuid: 'str | UUID') -> bool:
    """Delete a cluster operation header.

    Args:
        uuid: The operation's UUID (str or UUID).

    Returns:
        True if deleted, False if not found or error.
    """
    u = _ensure_uuid(uuid)
    if _use_database_service():
        return _grpc_delete_cluster_operation(u)
    return _direct_delete_cluster_operation(u)


def create_and_enqueue_cluster_operation(
        op_uuid: 'str | UUID',
        operation_type: str,
        metadata: dict[str, Any],
        created_at: float,
        queue_name: str,
        delay: float = 0.0) -> bool:
    """Atomically create a cluster operation and enqueue its job.

    Writes the cluster_operations header, an object_states row
    (state='queued'), and a work_queue row in a single MariaDB
    transaction. Replaces the legacy
    shakenfist/schema/operations/util.py:enqueue() path that used
    etcd.replace_many_raw.

    Audit events are NOT written by this function; callers should
    emit them via shakenfist.eventlog.add_event_multi() after this
    call returns True.

    Note: this is the low-level transactional primitive. The
    schema-layer helper shakenfist/schema/operations/util.py:
    enqueue_cluster_operation() should normally be preferred --
    it adds automatic cluster_operation_targets registration
    based on each schema's ``target_fields`` ClassVar. Calling
    this function directly bypasses that registration and
    should only be done when the caller has its own reason to
    skip it (e.g. internal bookkeeping migrations).

    Args:
        op_uuid: The operation's UUID (str or UUID).
        operation_type: Operation type name, e.g. 'node_net_op'.
        metadata: The full operation metadata dict.
        created_at: Unix timestamp of operation creation.
        queue_name: Target work queue name, e.g.
            '{target}-clusteroperation-{priority}'.
        delay: Seconds to defer the job (default 0).

    Returns:
        True on success. False if the operation uuid already
        exists (duplicate) or on MariaDB error.
    """
    u = _ensure_uuid(op_uuid)
    if _use_database_service():
        return _grpc_create_and_enqueue_cluster_operation(
            u, operation_type, metadata, created_at,
            queue_name, delay)
    return _direct_create_and_enqueue_cluster_operation(
        u, operation_type, metadata, created_at,
        queue_name, delay)


def set_cluster_operation_error(
        op_uuid: 'str | UUID',
        error_report: ErrorReport,
        created_at: Optional[float] = None) -> bool:
    """Persist the ErrorReport for a failed cluster operation.

    Routes through the database microservice when the caller is not
    the database daemon. Upserts the row keyed on op_uuid so a retry
    that fails again overwrites the prior report cleanly.

    Args:
        op_uuid: The operation's UUID (str or UUID).
        error_report: The structured failure record.
        created_at: Unix timestamp the report was written. Defaults
            to ``time.time()`` when omitted.

    Returns:
        True on success, False on MariaDB error.
    """
    u = _ensure_uuid(op_uuid)
    ts = created_at if created_at is not None else time.time()
    if _use_database_service():
        return _grpc_set_cluster_operation_error(u, error_report, ts)
    return _direct_set_cluster_operation_error(u, error_report, ts)


def get_cluster_operation_error(
        op_uuid: 'str | UUID') -> Optional[ErrorReport]:
    """Read the ErrorReport for a cluster operation.

    Args:
        op_uuid: The operation's UUID (str or UUID).

    Returns:
        The persisted ``ErrorReport`` or ``None`` if no report exists.
    """
    u = _ensure_uuid(op_uuid)
    if _use_database_service():
        return _grpc_get_cluster_operation_error(u)
    return _direct_get_cluster_operation_error(u)


def delete_cluster_operation_error(op_uuid: 'str | UUID') -> bool:
    """Delete the ErrorReport row for a cluster operation.

    Idempotent. Called from ``BaseClusterOperation.hard_delete`` when
    the cluster cleaner reaps a terminal-state op so the
    ``cluster_operation_errors`` table does not grow unbounded.

    Args:
        op_uuid: The operation's UUID (str or UUID).

    Returns:
        True on success (whether or not a row existed), False on
        MariaDB error.
    """
    u = _ensure_uuid(op_uuid)
    if _use_database_service():
        return _grpc_delete_cluster_operation_error(u)
    return _direct_delete_cluster_operation_error(u)


# =============================================================================
# Work Queue Direct Access Functions
# Replaces the /sf/queue/... and /sf/processing/... etcd prefixes with a
# single row-locked table. Claim state lives on the row itself. Public
# callers use the enqueue_work_item/dequeue_work_items/resolve_work_item/
# get_work_queue_length/restart_work_queue wrappers at the bottom of this
# module.
# =============================================================================

# Server-side ceiling on a single dequeue batch. The two production
# callers ask for far less today (``BATCH_SIZE = 10`` in sf-net,
# ``max(3, cpus/2)`` in the sf-queues pool), so this cap exists purely
# to bound the worst-case orphan window if any caller -- direct or via
# gRPC -- ever asks for an unreasonably large limit. Each claimed-but-
# not-yet-executed row stays invisible to other workers until the
# stuck-row reaper finds it (CLUSTER_OP_STUCK_THRESHOLD seconds), so
# the ceiling caps how much work can be stranded on a single worker
# crash. 256 is well above every legitimate caller and well below
# anything that would meaningfully exhaust the queue.
MAX_DEQUEUE_BATCH = 256


def _direct_work_queue_enqueue(
        queue_name: str, payload: dict[str, Any],
        delay: float = 0.0) -> None:
    """Insert a work item on the queue.

    Raises shakenfist.exceptions.CannotEnqueueWork on unrecoverable
    failure, matching the current etcd.enqueue() contract.
    """
    from shakenfist import exceptions
    engine = _get_engine()
    table = _get_work_queue_table()

    now = time.time()
    scheduled = now + delay
    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                queue_name=queue_name,
                scheduled_at=scheduled,
                claimed_at=None,
                claimed_by=None,
                attempts=0,
                payload=payload,
                created_at=now,
            )
            conn.execute(stmt)
            conn.commit()
    except OperationalError as e:
        LOG.warning(
            f'MariaDB insert failed for work_queue {queue_name}: {e}')
        raise exceptions.CannotEnqueueWork(
            f'work_queue insert failed for {queue_name}') from e


def _direct_work_queue_dequeue_batch(
        queue_names: list[str],
        worker_id: str,
        limit: int) -> list[tuple[str, str, dict[str, Any]]]:
    """Claim up to ``limit`` eligible jobs across ``queue_names``.

    Returns a list of ``(queue_name, job_name, payload_dict)`` tuples,
    possibly empty. Rows are ordered by the caller-supplied position
    of ``queue_name`` in ``queue_names`` (index 0 = top priority) and
    then by ``scheduled_at``, so a single SELECT returns the most
    important eligible work first. Uses ``FOR UPDATE SKIP LOCKED`` so
    parallel workers can't double-claim a row. ``attempts`` is
    incremented on the claim. ``job_name`` is the stringified
    autoincrement id, opaque to callers; pass it back -- with the
    matching ``queue_name`` -- to ``resolve_work_item``.

    The ``FIELD(queue_name, ...)`` order clause materialises the
    caller's priority order at the SQL layer. Lower priorities are
    only returned when the higher-priority queues yield fewer rows
    than ``limit``; sustained high-priority load can still starve
    them. Fairness (bounded staleness etc.) is left to the caller's
    queue_names composition for now.
    """
    if not queue_names or limit <= 0:
        return []
    # Defence-in-depth clamp: see MAX_DEQUEUE_BATCH above. Production
    # callers never reach this, but the gRPC handler is the trust
    # boundary and an unbounded ``limit`` would otherwise let any
    # caller stage an arbitrarily large in-flight batch.
    if limit > MAX_DEQUEUE_BATCH:
        limit = MAX_DEQUEUE_BATCH

    engine = _get_engine()
    table = _get_work_queue_table()

    try:
        with engine.connect() as conn:
            select_stmt = (
                sa.select(table.c.id, table.c.queue_name, table.c.payload)
                .where(table.c.queue_name.in_(queue_names))
                .where(table.c.claimed_at.is_(None))
                .where(
                    table.c.scheduled_at
                    <= sa.func.unix_timestamp(sa.func.now(6)))
                .order_by(
                    sa.func.field(table.c.queue_name, *queue_names),
                    table.c.scheduled_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            rows = conn.execute(select_stmt).fetchall()
            if not rows:
                return []

            row_ids = [r.id for r in rows]
            update_stmt = (
                sa.update(table)
                .where(table.c.id.in_(row_ids))
                .values(
                    claimed_at=sa.func.unix_timestamp(
                        sa.func.now(6)),
                    claimed_by=worker_id,
                    attempts=table.c.attempts + 1,
                )
            )
            conn.execute(update_stmt)
            conn.commit()
            return [
                (r.queue_name, str(r.id), dict(r.payload or {}))
                for r in rows
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB dequeue failed for work_queue across '
            f'{len(queue_names)} queues: {e}')
        return []


def _direct_work_queue_resolve(
        queue_name: str, job_name: str) -> None:
    """Mark a claimed job as complete by deleting the row.

    queue_name is accepted for parity with etcd.resolve() and for
    logging / safety; the id in job_name is already unique across
    queues.
    """
    engine = _get_engine()
    table = _get_work_queue_table()

    try:
        job_id = int(job_name)
    except ValueError:
        LOG.warning(
            f'work_queue resolve: non-numeric job_name '
            f'{job_name!r} for queue {queue_name}')
        return

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                (table.c.id == job_id)
                & (table.c.queue_name == queue_name))
            conn.execute(stmt)
            conn.commit()
    except OperationalError as e:
        LOG.warning(
            f'MariaDB resolve failed for work_queue '
            f'{queue_name}/{job_name}: {e}')


def _direct_work_queue_length(
        queue_name: str) -> tuple[int, int, int]:
    """Return (processing, queued, deferred) counts for a queue.

    - processing: claimed rows (claimed_at IS NOT NULL)
    - queued: unclaimed rows whose scheduled_at is now or in the past
    - deferred: unclaimed rows whose scheduled_at is in the future
    """
    engine = _get_engine()
    table = _get_work_queue_table()

    try:
        with engine.connect() as conn:
            now_expr = sa.func.unix_timestamp(sa.func.now(6))

            processing_stmt = (
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.queue_name == queue_name)
                .where(table.c.claimed_at.is_not(None))
            )
            queued_stmt = (
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.queue_name == queue_name)
                .where(table.c.claimed_at.is_(None))
                .where(table.c.scheduled_at <= now_expr)
            )
            deferred_stmt = (
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.queue_name == queue_name)
                .where(table.c.claimed_at.is_(None))
                .where(table.c.scheduled_at > now_expr)
            )

            processing = int(
                conn.execute(processing_stmt).scalar() or 0)
            queued = int(conn.execute(queued_stmt).scalar() or 0)
            deferred = int(
                conn.execute(deferred_stmt).scalar() or 0)
            return processing, queued, deferred
    except OperationalError as e:
        LOG.warning(
            f'MariaDB length query failed for work_queue '
            f'{queue_name}: {e}')
        return 0, 0, 0


def _direct_work_queue_restart(queue_name: str) -> int:
    """Clear all claims on a queue so workers re-pick up the jobs.

    Used by the queues daemon at startup to recover in-flight work
    after a crash. Does NOT reset attempts -- phase 7's reaper
    still needs to notice persistently-failing jobs eventually.

    Returns the number of rows whose claim was cleared.
    """
    engine = _get_engine()
    table = _get_work_queue_table()

    try:
        with engine.connect() as conn:
            stmt = (
                sa.update(table)
                .where(table.c.queue_name == queue_name)
                .where(table.c.claimed_at.is_not(None))
                .values(claimed_at=None, claimed_by=None)
            )
            result = conn.execute(stmt)
            conn.commit()
            return int(result.rowcount or 0)
    except OperationalError as e:
        LOG.warning(
            f'MariaDB restart failed for work_queue '
            f'{queue_name}: {e}')
        return 0


def _direct_work_queue_list_stuck(
        threshold_seconds: float) -> list[dict[str, Any]]:
    """Return rows whose claim is older than threshold_seconds.

    Used by the cluster daemon reaper. Each returned dict has id,
    queue_name, claimed_at, claimed_by, attempts, payload. Rows
    are ordered by claimed_at ascending so the oldest stuck row
    is handled first.
    """
    engine = _get_engine()
    table = _get_work_queue_table()

    try:
        with engine.connect() as conn:
            cutoff = sa.func.unix_timestamp(
                sa.func.now(6)) - threshold_seconds
            stmt = (
                sa.select(
                    table.c.id,
                    table.c.queue_name,
                    table.c.claimed_at,
                    table.c.claimed_by,
                    table.c.attempts,
                    table.c.payload,
                )
                .where(table.c.claimed_at.is_not(None))
                .where(table.c.claimed_at <= cutoff)
                .order_by(table.c.claimed_at.asc())
            )
            rows = conn.execute(stmt).fetchall()
            return [
                {
                    'id': int(r.id),
                    'queue_name': r.queue_name,
                    'claimed_at': float(r.claimed_at),
                    'claimed_by': r.claimed_by,
                    'attempts': int(r.attempts or 0),
                    'payload': dict(r.payload or {}),
                }
                for r in rows
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB list_stuck query failed for work_queue: {e}')
        return []


def _direct_work_queue_clear_claim(row_id: int) -> bool:
    """Clear claimed_at/claimed_by on one row.

    Returns True if the row existed and was still claimed (i.e.
    the UPDATE actually changed something). attempts is not
    reset so the reaper can still notice a persistently failing
    job via the attempts ceiling.
    """
    engine = _get_engine()
    table = _get_work_queue_table()

    try:
        with engine.connect() as conn:
            stmt = (
                sa.update(table)
                .where(table.c.id == row_id)
                .where(table.c.claimed_at.is_not(None))
                .values(claimed_at=None, claimed_by=None)
            )
            result = conn.execute(stmt)
            conn.commit()
            return bool(result.rowcount)
    except OperationalError as e:
        LOG.warning(
            f'MariaDB clear_claim failed for work_queue '
            f'row {row_id}: {e}')
        return False


def _direct_work_queue_delete_row(row_id: int) -> bool:
    """Delete a single work_queue row by id.

    Returns True if the row existed and was deleted. Used by the
    reaper's reject branch; the caller is responsible for
    transitioning the corresponding cluster operation to the
    error state after a successful delete.
    """
    engine = _get_engine()
    table = _get_work_queue_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.id == row_id)
            result = conn.execute(stmt)
            conn.commit()
            return bool(result.rowcount)
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete_row failed for work_queue '
            f'row {row_id}: {e}')
        return False


def _direct_find_existing_coalescible_op(
        operation_type: str,
        target_column: str,
        target_uuid: str,
        task_name: str) -> Optional[str]:
    """Read-only enqueue-side dedup lookup.

    Returns the uuid of the oldest pending cluster operation that:

    * has ``operation_type``
    * is targeted at ``target_uuid`` via the indexed
      ``{target_column}`` (one of ``network_uuid``,
      ``instance_uuid``, ``node_uuid``)
    * has a single-task list whose entry equals ``task_name``
    * is currently in state ``queued`` (not yet picked up)

    Returns ``None`` when there's no match. Callers use this to skip
    a new ``create_and_enqueue`` insert when an equivalent op is
    already in the queue: both eventual ``raise_for_error`` waiters
    block on the same op, the worker runs it once.

    There is a benign race: two concurrent callers can both look up
    and not find anything, and both create new rows. The
    dispatcher's ``claim_coalescible_siblings`` (step 4) catches the
    duplicate on the way out -- at most one extra row gets inserted
    per race window, never an unbounded fan-out.
    """
    if target_column not in {
            'network_uuid', 'instance_uuid', 'node_uuid'}:
        return None

    engine = _get_engine()
    cluster_ops_table = _get_cluster_operations_table()
    states_table = _get_object_states_table()
    target_col = getattr(cluster_ops_table.c, target_column)

    try:
        with engine.connect() as conn:
            stmt = (
                sa.select(cluster_ops_table.c.uuid)
                .select_from(
                    cluster_ops_table.join(
                        states_table,
                        sa.and_(
                            states_table.c.object_uuid == sa.cast(
                                cluster_ops_table.c.uuid, sa.String(36)),
                            states_table.c.object_type
                            == cluster_ops_table.c.operation_type,
                        )))
                .where(cluster_ops_table.c.operation_type == operation_type)
                .where(target_col == target_uuid)
                .where(states_table.c.state_value == 'queued')
                .where(
                    sa.func.json_length(
                        cluster_ops_table.c.metadata_json,
                        '$.tasks') == 1)
                .where(
                    sa.func.json_unquote(
                        sa.func.json_extract(
                            cluster_ops_table.c.metadata_json,
                            '$.tasks[0]')) == task_name)
                .order_by(cluster_ops_table.c.created_at.asc())
                .limit(1)
            )
            row = conn.execute(stmt).fetchone()
            if row is None:
                return None
            return str(row.uuid)
    except OperationalError as e:
        LOG.warning(
            f'MariaDB find_existing_coalescible_op failed for '
            f'{operation_type}/{target_column}={target_uuid}/{task_name}: '
            f'{e}')
        return None


def _direct_claim_coalescible_siblings(
        operation_type: str,
        target_column: str,
        target_uuid: str,
        task_names: list[str],
        exclude_op_uuid: str) -> list[str]:
    """Atomically transition sibling pending coalescible ops to COMPLETE.

    Used by the dispatcher (``BaseClusterOperation.execute``) before
    running a coalescible task: any other ops in the queue targeting
    the same object with the same single-task work get their state
    flipped to ``complete`` here, so when their work_queue row is
    eventually picked up the dispatcher's terminal-state branch
    (``shakenfist/daemons/network/workitem.py``) drops them cleanly.

    Returns the list of uuids that were folded so the caller can
    audit-log a "coalesced N siblings" event on the survivor.

    Safety guards baked into the SQL:

    * ``co.uuid != exclude_op_uuid`` -- never fold the survivor.
    * ``os.state_value = 'queued'`` -- skip anything already being
      executed by another worker, or already terminal. Combined with
      ``FOR UPDATE`` on the SELECT, this serialises against the
      dispatcher's own ``state = STATE_EXECUTING`` write.
    * ``JSON_LENGTH(metadata_json, '$.tasks') = 1`` -- only fold
      ops whose entire task list is one task. A multi-task sibling
      might also carry non-coalescible work that we mustn't drop.
    * Task name must be in the caller-supplied ``task_names``, which
      callers pre-filter to ``op_class.coalescible_tasks``.

    ``target_column`` is restricted to a small whitelist
    (``network_uuid``, ``instance_uuid``, ``node_uuid``) so it can
    be interpolated into the ORDER BY safely; SQLAlchemy's
    ``getattr(table.c, ...)`` does the column lookup and refuses
    unknown columns with ``AttributeError``.
    """
    if not task_names or target_column not in {
            'network_uuid', 'instance_uuid', 'node_uuid'}:
        return []

    # cluster_operations.uuid and its *_uuid columns are SQLAlchemy Uuid
    # columns, whose bind processor calls value.hex -- the filter values
    # therefore have to be uuid.UUID objects, but callers pass them as
    # plain strings. Coerce here (matching the insert path's _maybe_uuid
    # usage) before they reach the WHERE clause. A malformed uuid means
    # there is nothing meaningful to coalesce against, so skip the fold
    # rather than letting a StatementError kill the worker thread.
    try:
        target_uuid_val = _maybe_uuid(target_uuid)
        exclude_op_uuid_val = _maybe_uuid(exclude_op_uuid)
    except (ValueError, AttributeError, TypeError) as e:
        LOG.warning(
            f'claim_coalescible_siblings skipped, malformed uuid '
            f'({target_column}={target_uuid!r}, '
            f'exclude_op_uuid={exclude_op_uuid!r}): {e}')
        return []

    engine = _get_engine()
    cluster_ops_table = _get_cluster_operations_table()
    states_table = _get_object_states_table()
    target_col = getattr(cluster_ops_table.c, target_column)

    try:
        with engine.connect() as conn:
            select_stmt = (
                sa.select(cluster_ops_table.c.uuid)
                .select_from(
                    cluster_ops_table.join(
                        states_table,
                        sa.and_(
                            states_table.c.object_uuid == sa.cast(
                                cluster_ops_table.c.uuid, sa.String(36)),
                            states_table.c.object_type
                            == cluster_ops_table.c.operation_type,
                        )))
                .where(cluster_ops_table.c.operation_type == operation_type)
                .where(target_col == target_uuid_val)
                .where(cluster_ops_table.c.uuid != exclude_op_uuid_val)
                .where(states_table.c.state_value == 'queued')
                .where(
                    sa.func.json_length(
                        cluster_ops_table.c.metadata_json,
                        '$.tasks') == 1)
                .where(
                    sa.func.json_unquote(
                        sa.func.json_extract(
                            cluster_ops_table.c.metadata_json,
                            '$.tasks[0]')).in_(task_names))
                .with_for_update()
            )
            rows = conn.execute(select_stmt).fetchall()
            if not rows:
                return []

            folded_uuids = [str(r.uuid) for r in rows]
            update_stmt = (
                sa.update(states_table)
                .where(states_table.c.object_type == operation_type)
                .where(states_table.c.object_uuid.in_(folded_uuids))
                .values(
                    state_value='complete',
                    update_time=sa.func.unix_timestamp(sa.func.now(6)),
                    message='coalesced into sibling op')
            )
            conn.execute(update_stmt)
            conn.commit()
            return folded_uuids
    except OperationalError as e:
        LOG.warning(
            f'MariaDB claim_coalescible_siblings failed for '
            f'{operation_type}/{target_column}={target_uuid}: {e}')
        return []


# =============================================================================
# Cluster Locks Direct Access Functions
# =============================================================================

# InnoDB returns errno 1213 (ER_LOCK_DEADLOCK) when it picks a
# transaction as the deadlock victim. Retrying the same statement
# almost always wins on the next attempt because the conflicting
# transaction has either committed or moved on. Bound the retry
# count and use jittered exponential backoff so a deadlock storm
# from many simultaneous contenders doesn't synchronise on the
# next round.
_DEADLOCK_ERRNO = 1213
_DEADLOCK_MAX_ATTEMPTS = 4
_DEADLOCK_BASE_DELAY = 0.005

_T = TypeVar('_T')


def _is_innodb_deadlock(exc: OperationalError) -> bool:
    """Identify an InnoDB 1213 deadlock from a SQLAlchemy
    ``OperationalError``.

    SQLAlchemy wraps the underlying DB-API exception in
    ``exc.orig``; the mysqldb driver puts the MariaDB errno in
    ``orig.args[0]``.
    """
    orig = getattr(exc, 'orig', None)
    if orig is None:
        return False
    args: tuple[Any, ...] = getattr(orig, 'args', ())
    if len(args) == 0:
        return False
    return bool(args[0] == _DEADLOCK_ERRNO)


def _retry_on_deadlock(fn: Callable[[], _T], op_name: str) -> _T:
    """Run ``fn`` and retry on InnoDB 1213 deadlocks.

    Recovery from a deadlock is local to the database daemon:
    rerunning the rolled-back statement immediately is far cheaper
    than letting the error bubble out to the gRPC layer and consume
    the client's UNAVAILABLE retry budget. Without this, a deadlock
    storm on ``cluster_locks`` could push a release past the
    contender's 10s acquire timeout even though each individual
    deadlock resolves in milliseconds.

    Re-raises the last 1213 if every attempt deadlocks, so the
    caller's existing ``OperationalError`` handling still runs
    (warning + return-False for acquire, raise for release/refresh).
    Any non-deadlock ``OperationalError`` propagates immediately.
    """
    last_error: Optional[OperationalError] = None
    for attempt in range(_DEADLOCK_MAX_ATTEMPTS):
        try:
            return fn()
        except OperationalError as e:
            if not _is_innodb_deadlock(e):
                raise
            last_error = e
            if attempt < _DEADLOCK_MAX_ATTEMPTS - 1:
                # 5ms, 10ms, 20ms (* 0.5..1.5 jitter).
                delay = (_DEADLOCK_BASE_DELAY * (2 ** attempt)
                         * random.uniform(0.5, 1.5))
                time.sleep(delay)
    LOG.warning(
        f'{op_name}: {_DEADLOCK_MAX_ATTEMPTS} consecutive InnoDB '
        f'deadlocks, giving up: {last_error}')
    assert last_error is not None
    raise last_error


def _direct_acquire_cluster_lock(
        lock_key: str, holder_json: dict[str, Any],
        node_uuid: str, pid: int,
        lock_id: str, now: float) -> bool:
    """Atomically take or steal a leased cluster lock.

    A single ``INSERT ... ON DUPLICATE KEY UPDATE`` either inserts
    a new row (cold acquire) or rewrites the existing row in place
    when the previous holder's lease has expired (steal). When the
    existing lease is still valid the row is left untouched.

    The earlier two-statement (INSERT IGNORE, then UPDATE on PK
    collision) implementation was a classic InnoDB shared-shared
    upgrade deadlock vector: two concurrent acquires on the same
    ``lock_key`` each took an S-lock on the existing row during
    INSERT IGNORE's duplicate-key check, then both tried to
    upgrade to X via the steal UPDATE -- guaranteed deadlock,
    detected and rolled back as 1213. Collapsing the two
    statements removes the upgrade window entirely.

    Per-column ``IF(expires_at < NOW(), VALUES(col), col)`` lets a
    single SET clause express both "rewrite to me" and "leave
    alone" depending on lease state, but ``ON DUPLICATE KEY
    UPDATE`` rowcounts can't cleanly distinguish "stole it" from
    "left alone" across drivers. A primary-key SELECT for our
    ``lock_id`` after the upsert is the unambiguous test, and is
    cheap because it hits the PK we just touched.

    ``expires_at`` is set server-side to
    ``NOW() + CLUSTER_LOCK_LEASE_SECONDS`` so we never trust
    per-node clocks. Holders extend their lease via
    ``_direct_refresh_cluster_lock``; if they cannot, the row
    expires and a later candidate steals here.
    """
    engine = _get_engine()
    table = _get_cluster_locks_table()
    expires = sa.func.date_add(
        sa.func.now(),
        sa.text(f'INTERVAL {CLUSTER_LOCK_LEASE_SECONDS} SECOND'))

    insert_stmt = sa.dialects.mysql.insert(table).values(
        lock_key=lock_key,
        holder_json=holder_json,
        node_uuid=node_uuid,
        pid=pid,
        lock_id=lock_id,
        acquired_at=now,
        expires_at=expires,
    )
    inserted = insert_stmt.inserted
    expired = table.c.expires_at < sa.func.now()
    upsert_stmt = insert_stmt.on_duplicate_key_update(
        holder_json=sa.case(
            (expired, inserted.holder_json),
            else_=table.c.holder_json),
        node_uuid=sa.case(
            (expired, inserted.node_uuid),
            else_=table.c.node_uuid),
        pid=sa.case(
            (expired, inserted.pid),
            else_=table.c.pid),
        lock_id=sa.case(
            (expired, inserted.lock_id),
            else_=table.c.lock_id),
        acquired_at=sa.case(
            (expired, inserted.acquired_at),
            else_=table.c.acquired_at),
        expires_at=sa.case(
            (expired, inserted.expires_at),
            else_=table.c.expires_at),
    )
    confirm_stmt = sa.select(table.c.lock_id).where(
        table.c.lock_key == lock_key)

    def _do_acquire() -> bool:
        with engine.connect() as conn:
            conn.execute(upsert_stmt)
            row = conn.execute(confirm_stmt).first()
            conn.commit()
            return row is not None and row[0] == lock_id

    try:
        return _retry_on_deadlock(
            _do_acquire, f'acquire_cluster_lock({lock_key})')
    except OperationalError as e:
        LOG.warning(
            f'MariaDB acquire_cluster_lock failed for '
            f'{lock_key}: {e}')
        return False


def _direct_refresh_cluster_lock(
        lock_key: str, lock_id: str) -> bool:
    """Extend the lease for a lock we still hold.

    Returns True if the row was matched and refreshed, False if no
    row matched -- which means our lock has been stolen (or never
    existed). Callers must treat False as "lock lost" and abort
    whatever critical section they were in.

    InnoDB 1213 deadlocks are retried inline before propagating
    so a transient deadlock storm doesn't masquerade as a lost
    lease.
    """
    engine = _get_engine()
    table = _get_cluster_locks_table()
    expires = sa.func.date_add(
        sa.func.now(),
        sa.text(f'INTERVAL {CLUSTER_LOCK_LEASE_SECONDS} SECOND'))
    stmt = sa.update(table).where(
        sa.and_(
            table.c.lock_key == lock_key,
            table.c.lock_id == lock_id,
        )
    ).values(expires_at=expires)

    def _do_refresh() -> bool:
        with engine.connect() as conn:
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount == 1

    try:
        return _retry_on_deadlock(
            _do_refresh, f'refresh_cluster_lock({lock_key})')
    except OperationalError as e:
        LOG.warning(
            f'MariaDB refresh_cluster_lock failed for '
            f'{lock_key}: {e}')
        # Transient: caller should retry the refresh, not give up.
        raise


def _direct_release_cluster_lock(
        lock_key: str, lock_id: str) -> bool:
    """Release a lock by deleting the row, but only if the
    lock_id matches (CAS-equivalent).

    Returns True if a row was deleted, False if no row matched
    (the lease was stolen, or we never held it).

    Raises ``OperationalError`` for transient MariaDB issues
    (e.g. InnoDB deadlock, errno 1213). Callers must treat that
    as retryable, not as ``return False`` -- collapsing the two
    looks identical to "another holder stole the lease" and
    triggers a noisy ``LockNotHeld`` log on the next contender.

    1213 deadlocks are retried inline first; only a sustained
    storm propagates to the caller, where gRPC's UNAVAILABLE
    retry path takes over.
    """
    engine = _get_engine()
    table = _get_cluster_locks_table()
    stmt = sa.delete(table).where(
        sa.and_(
            table.c.lock_key == lock_key,
            table.c.lock_id == lock_id,
        )
    )

    def _do_release() -> bool:
        with engine.connect() as conn:
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount == 1

    try:
        return _retry_on_deadlock(
            _do_release, f'release_cluster_lock({lock_key})')
    except OperationalError as e:
        LOG.warning(
            f'MariaDB release_cluster_lock failed for '
            f'{lock_key}: {e}')
        raise


def _direct_get_cluster_lock(
        lock_key: str) -> Optional[dict[str, Any]]:
    """Get the current holder of a lock.

    Returns the holder_json dict if the lock is held,
    None otherwise.
    """
    engine = _get_engine()
    table = _get_cluster_locks_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table.c.holder_json).where(
                table.c.lock_key == lock_key
            )
            row = conn.execute(stmt).first()
            if row is None:
                return None
            holder: dict[str, Any] = row[0]
            return holder
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get_cluster_lock failed for '
            f'{lock_key}: {e}')
        return None


def _direct_clear_stale_cluster_locks(
        node_uuid: str,
        live_pids: list[int]) -> int:
    """Delete locks for a node whose pid is not in the live-pid set.

    Returns the number of rows deleted.
    """
    engine = _get_engine()
    table = _get_cluster_locks_table()

    try:
        with engine.connect() as conn:
            if live_pids:
                stmt = sa.delete(table).where(
                    sa.and_(
                        table.c.node_uuid == node_uuid,
                        table.c.pid.not_in(live_pids),
                    )
                )
            else:
                # No live pids means all locks for this node are stale
                stmt = sa.delete(table).where(
                    table.c.node_uuid == node_uuid,
                )
            result = conn.execute(stmt)
            conn.commit()
            deleted = result.rowcount
            if deleted > 0:
                LOG.info(
                    f'Cleared {deleted} stale lock(s) for '
                    f'node {node_uuid}')
            return deleted
    except OperationalError as e:
        LOG.warning(
            f'MariaDB clear_stale_cluster_locks failed: {e}')
        return 0


def _direct_get_all_cluster_locks() -> dict[str, dict[str, Any]]:
    """Return all locks as {lock_key: holder_json}.

    Used by GetExistingLocks for the admin snapshot endpoint.
    """
    engine = _get_engine()
    table = _get_cluster_locks_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(
                table.c.lock_key, table.c.holder_json)
            rows = conn.execute(stmt).fetchall()
            return {row[0]: row[1] for row in rows}
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get_all_cluster_locks failed: {e}')
        return {}


# =============================================================================
# Cluster Config Direct Access Functions
# =============================================================================

def _direct_get_all_cluster_config() -> dict[str, Any]:
    """Return all cluster config as {key_name: value}.

    value is the raw JSON-decoded value (str, int, float, bool).
    """
    engine = _get_engine()
    table = _get_cluster_config_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(
                table.c.key_name, table.c.value_json)
            rows = conn.execute(stmt).fetchall()
            return {row[0]: row[1] for row in rows}
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get_all_cluster_config failed: {e}')
        return {}


def _direct_set_cluster_config(
        key_name: str, value: Any) -> None:
    """Upsert a single config key.

    Uses INSERT ... ON DUPLICATE KEY UPDATE so concurrent
    writes to different keys don't conflict.
    """
    engine = _get_engine()
    table = _get_cluster_config_table()
    now = time.time()

    try:
        with engine.connect() as conn:
            stmt = sa.dialects.mysql.insert(table).values(
                key_name=key_name,
                value_json=value,
                updated_at=now,
            )
            stmt = stmt.on_duplicate_key_update(
                value_json=value,
                updated_at=now,
            )
            conn.execute(stmt)
            conn.commit()
    except OperationalError as e:
        LOG.warning(
            f'MariaDB set_cluster_config failed for '
            f'{key_name}: {e}')


def _direct_delete_cluster_config(
        key_name: str) -> bool:
    """Delete a single config key. Returns True if deleted."""
    engine = _get_engine()
    table = _get_cluster_config_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.key_name == key_name)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount == 1
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete_cluster_config failed for '
            f'{key_name}: {e}')
        return False


# =============================================================================
# Event DLQ Direct Access Functions
# =============================================================================

def _direct_enqueue_event_dlq(
        object_type: str, object_uuid: str,
        event_timestamp: float,
        event_json: dict[str, Any]) -> None:
    """Insert an event into the dead-letter queue."""
    engine = _get_engine()
    table = _get_event_dlq_table()
    now = time.time()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                object_type=object_type,
                object_uuid=str(object_uuid),
                event_timestamp=event_timestamp,
                event_json=event_json,
                enqueued_at=now,
            )
            conn.execute(stmt)
            conn.commit()
    except OperationalError as e:
        LOG.warning(
            f'MariaDB enqueue_event_dlq failed for '
            f'{object_type}/{object_uuid}: {e}')


def _direct_drain_event_dlq(
        limit: int = 10000) -> list[dict[str, Any]]:
    """Return up to limit DLQ rows ordered by id.

    Returns list of dicts with keys: id, object_type,
    object_uuid, event_json.
    """
    engine = _get_engine()
    table = _get_event_dlq_table()

    try:
        with engine.connect() as conn:
            stmt = (
                sa.select(
                    table.c.id,
                    table.c.object_type,
                    table.c.object_uuid,
                    table.c.event_json,
                )
                .order_by(table.c.id)
                .limit(limit)
            )
            rows = conn.execute(stmt).fetchall()
            return [
                {
                    'id': row[0],
                    'object_type': row[1],
                    'object_uuid': row[2],
                    'event_json': row[3],
                }
                for row in rows
            ]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB drain_event_dlq failed: {e}')
        return []


def _direct_get_event_dlq_count() -> int:
    """Return the current number of rows in the event_dlq table."""
    engine = _get_engine()
    table = _get_event_dlq_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(sa.func.count()).select_from(table)
            return int(conn.execute(stmt).scalar_one())
    except OperationalError as e:
        LOG.warning(
            f'MariaDB get_event_dlq_count failed: {e}')
        return 0


def _direct_delete_event_dlq(ids: list[int]) -> int:
    """Delete DLQ rows by id. Returns count deleted."""
    if not ids:
        return 0

    engine = _get_engine()
    table = _get_event_dlq_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(
                table.c.id.in_(ids))
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount
    except OperationalError as e:
        LOG.warning(
            f'MariaDB delete_event_dlq failed: {e}')
        return 0


# =============================================================================
# Event DLQ gRPC Client Functions
# =============================================================================

def _grpc_enqueue_event_dlq(
        object_type: str, object_uuid: str,
        event_timestamp: float,
        event_json: dict[str, Any]) -> None:
    """Enqueue an event via the database microservice."""
    from shakenfist.protos import database_pb2

    stub = _get_database_stub()
    if not stub:
        return

    request = database_pb2.EnqueueEventDlqRequest(
        object_type=object_type,
        object_uuid=str(object_uuid),
        event_timestamp=event_timestamp,
        event_json=json.dumps(event_json),
    )
    _grpc_call(stub.EnqueueEventDlq, request)


def _grpc_drain_event_dlq(
        limit: int = 10000) -> list[dict[str, Any]]:
    """Drain DLQ entries via the database microservice."""
    from shakenfist.protos import database_pb2

    stub = _get_database_stub()
    if not stub:
        return []

    request = database_pb2.DrainEventDlqRequest(limit=limit)
    response = _grpc_call(stub.DrainEventDlq, request)
    if response is None:
        return []

    return [
        {
            'id': entry.id,
            'object_type': entry.object_type,
            'object_uuid': entry.object_uuid,
            'event_json': json.loads(entry.event_json),
        }
        for entry in response.entries
    ]


def _grpc_get_event_dlq_count() -> int:
    """Fetch the event_dlq row count via the database microservice."""
    from shakenfist.protos import database_pb2

    stub = _get_database_stub()
    if not stub:
        return 0

    request = database_pb2.GetEventDlqCountRequest()
    response = _grpc_call(stub.GetEventDlqCount, request)
    if response is None:
        return 0
    return int(response.count)


def _grpc_delete_event_dlq(ids: list[int]) -> int:
    """Delete DLQ entries via the database microservice."""
    from shakenfist.protos import database_pb2

    stub = _get_database_stub()
    if not stub:
        return 0

    request = database_pb2.DeleteEventDlqRequest(ids=ids)
    response = _grpc_call(stub.DeleteEventDlq, request)
    if response is None:
        return 0
    return len(ids) if response.success else 0


# =============================================================================
# Cluster Lock gRPC Functions
# =============================================================================

def _grpc_acquire_cluster_lock(
        object_type: str, subtype: str, name: str,
        lock_data: dict[str, Any]) -> bool:
    """Acquire a lock via the database microservice."""
    stub = _get_database_stub()
    if not stub:
        return False

    request = database_pb2.ClusterLockRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name,
        lock_data=_json_dumps(lock_data),
    )
    response = _grpc_call(stub.AcquireLock, request)
    if response is None:
        return False
    return bool(response.acquired)


def _grpc_release_cluster_lock(
        object_type: str, subtype: str, name: str,
        lock_data: dict[str, Any]) -> bool:
    """Release a lock via the database microservice."""
    stub = _get_database_stub()
    if not stub:
        return False

    request = database_pb2.ClusterReleaseLockRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name,
        lock_data=_json_dumps(lock_data),
    )
    response = _grpc_call(stub.ReleaseLock, request)
    if response is None:
        return False
    return bool(response.success)


def _grpc_refresh_cluster_lock(
        object_type: str, subtype: str, name: str,
        lock_id: str) -> bool:
    """Extend the lease for a lock via the database microservice.

    Transient gRPC errors propagate as ``grpc.RpcError`` after
    ``_grpc_call`` exhausts its retries; the refresh loop in
    ``ClusterLock`` catches those and tries again rather than
    treating them as confirmed lock loss.
    """
    stub = _get_database_stub()
    request = database_pb2.ClusterRefreshLockRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name,
        lock_id=lock_id,
    )
    response = _grpc_call(stub.RefreshLock, request)
    return bool(response.success)


def _grpc_get_cluster_lock_holder(
        object_type: str, subtype: str,
        name: str) -> Optional[dict[str, Any]]:
    """Get the holder of a lock via the database microservice."""
    stub = _get_database_stub()
    if not stub:
        return None

    request = database_pb2.ClusterGetLockHolderRequest(
        object_type=object_type,
        subtype=subtype or '',
        name=name,
    )
    response = _grpc_call(stub.GetLockHolder, request)
    if response is None or not response.held:
        return None
    return cast(dict[str, Any], json.loads(response.holder))


def _grpc_clear_stale_cluster_locks(
        node_name: str, live_pids: list[int]) -> None:
    """Clear stale locks via the database microservice."""
    stub = _get_database_stub()
    if not stub:
        return

    request = database_pb2.ClusterClearStaleLocksRequest(
        node_name=node_name,
        live_pids=live_pids,
    )
    response = _grpc_call(stub.ClearStaleLocks, request)
    if response is not None and not response.success:
        LOG.error(
            f'Database clear_stale_locks failed: {response.error}')


def _grpc_get_all_cluster_locks() -> dict[str, dict[str, Any]]:
    """Return all cluster locks via the database microservice."""
    stub = _get_database_stub()
    if not stub:
        return {}

    request = database_pb2.ClusterGetExistingLocksRequest()
    response = _grpc_call(stub.GetExistingLocks, request)
    if response is None:
        return {}

    return {
        lock.key: json.loads(lock.holder)
        for lock in response.locks
    }


# =============================================================================
# Cluster Lock Public API Functions
# =============================================================================

def acquire_cluster_lock(
        object_type: str, subtype: str, name: str,
        lock_data: dict[str, Any]) -> bool:
    """Attempt to acquire a distributed lock.

    Routes to the database microservice or direct MariaDB depending
    on _use_database_service(). Callers running on the database node
    (MARIADB_HOST set) use direct access so that bootstrap-time
    commands work before the database daemon is started.
    """
    if _use_database_service():
        return _grpc_acquire_cluster_lock(
            object_type, subtype, name, lock_data)

    lock_key = _cluster_lock_key(object_type, subtype, name)
    return _direct_acquire_cluster_lock(
        lock_key=lock_key,
        holder_json=lock_data,
        node_uuid=lock_data.get('node', ''),
        pid=int(lock_data.get('pid', 0)),
        lock_id=lock_data.get('id', ''),
        now=time.time(),
    )


def release_cluster_lock(
        object_type: str, subtype: str, name: str,
        lock_data: dict[str, Any]) -> bool:
    """Release a distributed lock.

    Routes to the database microservice or direct MariaDB depending
    on _use_database_service().
    """
    if _use_database_service():
        return _grpc_release_cluster_lock(
            object_type, subtype, name, lock_data)

    lock_key = _cluster_lock_key(object_type, subtype, name)
    return _direct_release_cluster_lock(
        lock_key=lock_key,
        lock_id=lock_data.get('id', ''),
    )


def refresh_cluster_lock(
        object_type: str, subtype: str, name: str,
        lock_id: str) -> bool:
    """Extend the lease on a distributed lock we still hold.

    Returns True on a successful refresh, False if the lock has been
    stolen (no row matched lock_id) -- callers must treat False as
    "lock lost" and abort whatever critical section they were in.
    Transient backend failures propagate (``OperationalError`` on the
    direct path, ``grpc.RpcError`` on the gRPC path); the refresh
    loop in ``ClusterLock`` catches them and retries rather than
    treating them as confirmed loss.
    """
    if _use_database_service():
        return _grpc_refresh_cluster_lock(
            object_type, subtype, name, lock_id)

    lock_key = _cluster_lock_key(object_type, subtype, name)
    return _direct_refresh_cluster_lock(
        lock_key=lock_key,
        lock_id=lock_id,
    )


def get_cluster_lock_holder(
        object_type: str, subtype: str,
        name: str) -> dict[str, Any]:
    """Get the current holder of a lock.

    Returns {'holder': None} when the lock is free, matching the
    contract the locks module expects. Routes to the database
    microservice or direct MariaDB depending on
    _use_database_service().
    """
    if _use_database_service():
        holder = _grpc_get_cluster_lock_holder(
            object_type, subtype, name)
    else:
        lock_key = _cluster_lock_key(object_type, subtype, name)
        holder = _direct_get_cluster_lock(lock_key)

    if holder is None:
        return {'holder': None}
    return holder


def clear_stale_cluster_locks(
        node_name: str, live_pids: list[int]) -> None:
    """Clear locks held by dead processes on a node.

    Routes to the database microservice or direct MariaDB depending
    on _use_database_service().
    """
    if _use_database_service():
        _grpc_clear_stale_cluster_locks(node_name, live_pids)
        return
    _direct_clear_stale_cluster_locks(
        node_uuid=node_name, live_pids=live_pids)


def get_all_cluster_locks() -> dict[str, dict[str, Any]]:
    """Return every lock as {key: holder_json}.

    Routes to the database microservice or direct MariaDB depending
    on _use_database_service().
    """
    if _use_database_service():
        return _grpc_get_all_cluster_locks()
    return _direct_get_all_cluster_locks()


# =============================================================================
# Cluster Config gRPC Functions
# =============================================================================

def _grpc_get_all_cluster_config() -> dict[str, Any]:
    """Fetch all cluster config via the database microservice."""
    stub = _get_database_stub()
    if not stub:
        return {}

    request = database_pb2.ClusterConfigRequest()
    response = _grpc_call(stub.GetClusterConfig, request)
    if response is None:
        return {}

    return {
        entry.key_name: json.loads(entry.value_json)
        for entry in response.entries
    }


def _grpc_set_cluster_config(key_name: str, value: Any) -> None:
    """Set a cluster config key via the database microservice."""
    stub = _get_database_stub()
    if not stub:
        return

    request = database_pb2.SetClusterConfigRequest(
        key_name=key_name,
        value_json=json.dumps(value),
    )
    response = _grpc_call(stub.SetClusterConfig, request)
    if response is not None and not response.success:
        LOG.error(
            f'Database set_cluster_config failed: {response.error}')


# =============================================================================
# Cluster Config Public API Functions
# =============================================================================

def get_cluster_config() -> dict[str, Any]:
    """Return all cluster config as {key_name: value}.

    Routes to the database microservice or direct MariaDB depending
    on _use_database_service(). Callers running on the database node
    (MARIADB_HOST set) use direct access so that bootstrap-time
    commands work before the database daemon is started.
    """
    if _use_database_service():
        return _grpc_get_all_cluster_config()
    return _direct_get_all_cluster_config()


def set_cluster_config(key_name: str, value: Any) -> None:
    """Upsert a single cluster config key.

    Routes to the database microservice or direct MariaDB depending
    on _use_database_service(). Callers running on the database node
    (MARIADB_HOST set) use direct access so that bootstrap-time
    commands work before the database daemon is started.
    """
    if _use_database_service():
        _grpc_set_cluster_config(key_name, value)
        return
    _direct_set_cluster_config(key_name, value)


# =============================================================================
# Event DLQ Public API Functions
# =============================================================================

def enqueue_event_dlq(
        object_type: str, object_uuid: str,
        event_timestamp: float,
        event_json: dict[str, Any]) -> None:
    """Insert an event into the dead-letter queue.

    Routes to the database microservice or direct MariaDB depending
    on _use_database_service().
    """
    if _use_database_service():
        _grpc_enqueue_event_dlq(
            object_type, object_uuid, event_timestamp, event_json)
        return
    _direct_enqueue_event_dlq(
        object_type, object_uuid, event_timestamp, event_json)


def drain_event_dlq(limit: int = 10000) -> list[dict[str, Any]]:
    """Return up to limit DLQ rows for processing.

    Returns list of dicts with keys: id, object_type, object_uuid,
    event_json. Caller must call delete_event_dlq() after successful
    processing to preserve at-least-once delivery.
    """
    if _use_database_service():
        return _grpc_drain_event_dlq(limit)
    return _direct_drain_event_dlq(limit)


def delete_event_dlq(ids: list[int]) -> int:
    """Delete processed DLQ rows by id. Returns count deleted."""
    if _use_database_service():
        return _grpc_delete_event_dlq(ids)
    return _direct_delete_event_dlq(ids)


def get_event_dlq_count() -> int:
    """Return the current number of rows in the event_dlq table.

    This is a cheap SELECT COUNT(*) suitable for metrics; it does not
    deserialise any event payloads, unlike drain_event_dlq.
    """
    if _use_database_service():
        return _grpc_get_event_dlq_count()
    return _direct_get_event_dlq_count()


# =============================================================================
# Work Queue Public API Functions
# =============================================================================

def enqueue_work_item(
        queue_name: str, work_item: dict[str, Any],
        delay: float = 0.0) -> None:
    """Insert a work item on a queue.

    Routes to the database microservice or direct MariaDB depending
    on _use_database_service(). Raises
    shakenfist.exceptions.CannotEnqueueWork on unrecoverable failure
    in the direct path; the gRPC path logs and returns.
    """
    if _use_database_service():
        _grpc_work_queue_enqueue(queue_name, work_item, delay)
        return
    _direct_work_queue_enqueue(queue_name, work_item, delay)


def dequeue_work_items(
        queue_names: list[str],
        limit: int = 10,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Claim up to ``limit`` eligible jobs across ``queue_names``.

    Returns a list of ``(queue_name, job_name, payload)`` tuples,
    possibly empty. Items are returned in caller-supplied priority
    order: the first queue_name in the list is highest priority. The
    direct path uses ``config.NODE_NAME`` as worker_id; the gRPC
    path relies on the database daemon's NODE_NAME (the proto does
    not carry a caller-supplied worker_id).

    This single query replaces the previous per-queue dequeue loop;
    a sf-net or sf-queues worker that used to issue 10 sequential
    gRPC calls per idle iteration now issues one. The trade-off is
    that a worker which doesn't immediately execute every returned
    item holds the others claimed until it does -- crashes between
    receive and execute are recovered by the stuck-row reaper
    (``list_stuck_work_queue_rows``).
    """
    if _use_database_service():
        return _grpc_work_queue_dequeue_batch(queue_names, limit)
    return _direct_work_queue_dequeue_batch(
        queue_names, config.NODE_NAME, limit)


def resolve_work_item(queue_name: str, job_name: str) -> None:
    """Mark a claimed job complete."""
    if _use_database_service():
        _grpc_work_queue_resolve(queue_name, job_name)
        return
    _direct_work_queue_resolve(queue_name, job_name)


def get_work_queue_length(
        queue_name: str) -> tuple[int, int, int]:
    """Return (processing, queued, deferred) counts for a queue."""
    if _use_database_service():
        return _grpc_work_queue_length(queue_name)
    return _direct_work_queue_length(queue_name)


def restart_work_queue(queue_name: str) -> None:
    """Clear claims on a queue so workers re-pick up the jobs.

    Called at queue daemon startup to recover in-flight work after
    a crash.
    """
    if _use_database_service():
        _grpc_work_queue_restart(queue_name)
        return
    _direct_work_queue_restart(queue_name)


def list_stuck_work_queue_rows(
        threshold_seconds: float) -> list[dict[str, Any]]:
    """Return rows whose claim is older than threshold_seconds.

    Used by the cluster daemon reaper to find work items stuck
    in flight. Each dict carries id, queue_name, claimed_at,
    claimed_by, attempts and payload. Rows are ordered oldest
    claim first.
    """
    if _use_database_service():
        return _grpc_work_queue_list_stuck(threshold_seconds)
    return _direct_work_queue_list_stuck(threshold_seconds)


def clear_work_queue_claim(row_id: int) -> bool:
    """Clear claimed_at/claimed_by on one work_queue row.

    Returns True if the row existed and was still claimed.
    attempts is not reset, so a persistently-failing job
    eventually trips CLUSTER_OP_MAX_ATTEMPTS.
    """
    if _use_database_service():
        return _grpc_work_queue_clear_claim(row_id)
    return _direct_work_queue_clear_claim(row_id)


def delete_work_queue_row(row_id: int) -> bool:
    """Delete a single work_queue row by id.

    Returns True if the row existed and was deleted. Used by
    the reaper's reject branch; the caller flips the
    corresponding cluster operation to error afterwards.
    """
    if _use_database_service():
        return _grpc_work_queue_delete_row(row_id)
    return _direct_work_queue_delete_row(row_id)


def find_existing_coalescible_op(
        operation_type: str,
        target_column: str,
        target_uuid: str,
        task_name: str) -> Optional[str]:
    """Look up an existing pending coalescible op on the same target.

    Returns its uuid if found, otherwise ``None``. Used by
    ``create_and_enqueue`` for ops whose entire task list is a
    single coalescible task: if a sibling is already in the queue,
    skip the insert and return the existing op's uuid so all
    waiters block on the same op.
    """
    if _use_database_service():
        return _grpc_find_existing_coalescible_op(
            operation_type, target_column, target_uuid, task_name)
    return _direct_find_existing_coalescible_op(
        operation_type, target_column, target_uuid, task_name)


def claim_coalescible_siblings(
        operation_type: str,
        target_column: str,
        target_uuid: str,
        task_names: list[str],
        exclude_op_uuid: str) -> list[str]:
    """Atomically fold sibling pending coalescible ops.

    Used by ``BaseClusterOperation.execute`` before running a
    coalescible task. The server transitions every matching
    sibling's ``object_states`` row to ``complete`` in one
    statement, then returns the affected uuids. See
    ``_direct_claim_coalescible_siblings`` for the safety guards
    (matched only single-task siblings in state ``queued``, never
    folds the survivor).

    Callers pre-filter ``task_names`` to the op class's
    ``coalescible_tasks`` set and pre-supply
    ``coalescible_target_column`` so this helper stays generic
    across op types.
    """
    if _use_database_service():
        return _grpc_claim_coalescible_siblings(
            operation_type, target_column, target_uuid,
            task_names, exclude_op_uuid)
    return _direct_claim_coalescible_siblings(
        operation_type, target_column, target_uuid,
        task_names, exclude_op_uuid)
