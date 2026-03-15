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
import time
import threading
from typing import Any, Callable, cast, Optional
from uuid import UUID
from uuid import uuid4

import grpc
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import INET4
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError
from shakenfist_utilities import logs

from shakenfist.config import config
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
from shakenfist.schema.object_reference import ObjectReference
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
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
ARTIFACTS_VERSION = 1
ARTIFACT_ATTRIBUTES_VERSION = 1
ARTIFACT_INDEXES_VERSION = 1
NETWORK_INTERFACES_VERSION = 2
NETWORK_INTERFACE_ATTRIBUTES_VERSION = 2
NETWORKS_VERSION = 2
NETWORK_ATTRIBUTES_VERSION = 2
IPAMS_VERSION = 2
AGENT_OPERATIONS_VERSION = 2
AGENT_OPERATION_ATTRIBUTES_VERSION = 2
INSTANCES_VERSION = 1
INSTANCE_ATTRIBUTES_VERSION = 1


def _use_database_service() -> bool:
    """Check if we should use the database microservice instead of direct access.

    Returns True if the database service is configured and we should use it.
    Returns False if we should use direct MariaDB access (database daemon mode).

    The logic is:
    1. If DATABASE_USE_DIRECT_ETCD is True AND MARIADB_HOST is configured,
       use direct access (this is the database daemon on an etcd_master node).
    2. If DATABASE_NODE_IP is configured, use the database service.
    3. Otherwise we have no way to access the database.
    """
    # The database daemon sets DATABASE_USE_DIRECT_ETCD=true. When this is true
    # AND we have MariaDB configured, we use direct MariaDB access. This only
    # happens on etcd_master nodes which have the MariaDB credentials.
    if config.DATABASE_USE_DIRECT_ETCD and config.MARIADB_HOST:
        return False

    # For all other cases, try to use the database service via gRPC
    if not config.DATABASE_NODE_IP:
        return False
    return True


def _get_database_stub() -> Any:
    """Get or create a gRPC stub for the database service.

    Returns Any because the generated protobuf stubs are untyped.
    """
    if not hasattr(_local, 'database_channel') or _local.database_channel is None:
        _local.database_channel = grpc.insecure_channel(
            f'{config.DATABASE_NODE_IP}:{config.DATABASE_API_PORT}')
        _local.database_stub = database_pb2_grpc.DatabaseServiceStub(
            _local.database_channel)
    return _local.database_stub


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
        metadata = _get_metadata()
        # Build the table manually to support composite primary key
        _object_states_table = sa.Table(
            'object_states',
            metadata,
            sa.Column('object_uuid', sa.String(36), nullable=False),
            sa.Column('object_type', sa.Enum(ObjectType), nullable=False),
            sa.Column('state_value', sa.String(32), nullable=True),
            sa.Column('update_time', sa.Double(), nullable=False),
            sa.Column('message', sa.String(255), nullable=True),
            # Composite primary key
            sa.PrimaryKeyConstraint('object_type', 'object_uuid'),
            # Index for efficient queries by type and state
            sa.Index('idx_object_states_type_state', 'object_type', 'state_value'),
        )
    return _object_states_table


def _build_object_type_enum_values() -> str:
    """Build the ENUM values string for ObjectType.

    Returns a comma-separated list of quoted enum values for use in
    ALTER TABLE statements.
    """
    return ', '.join(f"'{ot.value}'" for ot in ObjectType)


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
                    f'migrated {stats.get("migrated_count", "?")} items, '
                    f'{stats.get("error_count", 0)} errors'
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
                ipv4=data.get('ipv4', ''),
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
            ni_data = etcd.get(
                'attribute/network', net_uuid,
                'networkinterfaces')
            dns_data = etcd.get(
                'attribute/network', net_uuid, 'hosteddns')

            # Extract values with defaults
            floating_gateway = None
            if routing_data:
                floating_gateway = routing_data.get(
                    'floating_gateway')

            networkinterfaces: list[str] = []
            networkinterfaces_initialized = False
            if ni_data:
                networkinterfaces = ni_data.get(
                    'networkinterfaces', [])
                networkinterfaces_initialized = ni_data.get(
                    'initialized', False)

            hosteddns: dict[str, Any] = {}
            if dns_data:
                hosteddns = dns_data

            attrs = NetworkAttributesData(
                uuid=UUIDType(net_uuid),
                floating_gateway=floating_gateway,
                networkinterfaces=networkinterfaces,
                networkinterfaces_initialized=(
                    networkinterfaces_initialized),
                hosteddns=hosteddns,
            )
            success = _direct_create_network_attributes(attrs)

            if success:
                # Delete etcd attributes after successful migration
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
                etcd.delete('instance', None, inst_uuid)
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
            interfaces_data = etcd.get(
                'attribute/instance', inst_uuid, 'interfaces')
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
            interfaces = []
            if interfaces_data and isinstance(
                    interfaces_data, list):
                interfaces = interfaces_data
            elif interfaces_data and isinstance(
                    interfaces_data, dict):
                interfaces = interfaces_data.get(
                    'interfaces', [])

            kvm_pid = None
            if kvm_pid_data and isinstance(
                    kvm_pid_data, dict):
                kvm_pid = kvm_pid_data.get('pid')

            error_message = None
            if error_data and isinstance(
                    error_data, dict):
                error_message = error_data.get('message')

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
                interfaces=interfaces,
                agent_state=agent_state if isinstance(
                    agent_state, dict) else None,
                agent_attributes=agent_attributes if isinstance(
                    agent_attributes, dict) else None,
                agent_operations=agent_operations if isinstance(
                    agent_operations, dict) else None,
                kvm_pid=kvm_pid,
                error_message=error_message,
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
                ]:
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
    return {'migrated_count': migrated_count, 'error_count': error_count}


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
                                 state_values: list[str]) -> list[str]:
    """Get all object UUIDs of a given type in specified states.

    This is the direct access version used by the database daemon.
    """
    engine = _get_engine()
    table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table.c.object_uuid).where(
                sa.and_(
                    table.c.object_type == object_type,
                    table.c.state_value.in_(state_values)
                )
            )
            result = conn.execute(stmt).fetchall()
            return [row.object_uuid for row in result]
    except OperationalError as e:
        LOG.warning(
            f'MariaDB query failed for {object_type} in {state_values}: {e}')
        return []


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
        reply = stub.GetObjectState(request)
        if not reply.found:
            return None
        return State(
            value=reply.state_value if reply.state_value else None,
            update_time=reply.update_time,
            message=reply.message if reply.message else None
        )
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.SetObjectState(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteObjectState(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC DeleteObjectState failed for {object_type}/{object_uuid}: {e}')
        return False


def _grpc_get_objects_by_state(object_type: ObjectType,
                               state_values: list[str]) -> list[str]:
    """Get all object UUIDs of a given type in specified states via gRPC."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetObjectsByStateRequest(
            object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType, object_type.proto_id),
            state_values=state_values
        )
        reply = stub.GetObjectsByState(request)
        return list(reply.object_uuids)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC GetObjectsByState failed for {object_type}: {e}')
        return []


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
        reply = stub.ReserveAddress(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.ReleaseAddress(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC ReleaseAddress failed for {ipam_uuid}/{address}: {e}')
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
        reply = stub.GetReservation(request)
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
        LOG.warning(f'gRPC GetReservation failed for {ipam_uuid}/{address}: {e}')
        return None


def _grpc_get_reservations_for_ipam(ipam_uuid: str) -> list[IPAMReservation]:
    """Get all reservations for an IPAM via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetReservationsForIPAMRequest(
            ipam_uuid=ipam_uuid)
        reply = stub.GetReservationsForIPAM(request)
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
        LOG.warning(f'gRPC GetReservationsForIPAM failed for {ipam_uuid}: {e}')
        return []


def _grpc_delete_reservation(ipam_uuid: str, address: str) -> bool:
    """Delete a single reservation via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteReservationRequest(
            ipam_uuid=ipam_uuid,
            address=address
        )
        reply = stub.DeleteReservation(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC DeleteReservation failed for {ipam_uuid}/{address}: {e}')
        return False


def _grpc_delete_reservations_for_ipam(ipam_uuid: str) -> int:
    """Delete all reservations for an IPAM via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteReservationsForIPAMRequest(
            ipam_uuid=ipam_uuid)
        reply = stub.DeleteReservationsForIPAM(request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.ReleaseHaloedAddresses(request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC ReleaseHaloedAddresses failed for {ipam_uuid}: {e}')
        return 0


def _grpc_get_addresses_in_use(ipam_uuid: str) -> set[str]:
    """Get all addresses in use for an IPAM via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAddressesInUseRequest(
            ipam_uuid=ipam_uuid)
        reply = stub.GetAddressesInUse(request)
        return set(reply.addresses)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetAddressesInUse failed for {ipam_uuid}: {e}')
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
                         state_values: list[str]) -> list[str]:
    """Get all object UUIDs of a given type in specified states.

    This is the primary use case for MariaDB state storage - efficient
    queries across object states without scanning all objects in etcd.

    Args:
        object_type: The type of object.
        state_values: List of state values to match.

    Returns:
        List of object UUIDs matching the criteria.
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
        reply = stub.CreateUpload(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC CreateUpload failed for {upload_uuid}: {e}')
        return False


def _grpc_get_upload(upload_uuid: UUID) -> Optional[UploadData]:
    """Get upload static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetUploadRequest(uuid=str(upload_uuid))
        reply = stub.GetUpload(request)
        if not reply.found:
            return None
        return UploadData(
            uuid=reply.upload.uuid,
            node=reply.upload.node,
            created_at=reply.upload.created_at,
            version=reply.upload.version
        )
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetUpload failed for {upload_uuid}: {e}')
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
        reply = stub.GetUploads(request)
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
        LOG.warning(f'gRPC GetUploads failed: {e}')
        return []


def _grpc_delete_upload(upload_uuid: UUID) -> bool:
    """Delete an upload record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteUploadRequest(uuid=str(upload_uuid))
        reply = stub.DeleteUpload(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteUpload failed for {upload_uuid}: {e}')
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
        reply = stub.UpdateUpload(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpdateUpload failed for {data.uuid}: {e}')
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
        reply = stub.CreateBlob(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC CreateBlob failed for {blob_uuid}: {e}')
        return False


def _grpc_get_blob(blob_uuid: UUID) -> Optional[BlobData]:
    """Get blob static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetBlobRequest(uuid=str(blob_uuid))
        reply = stub.GetBlob(request)
        if not reply.found:
            return None
        return BlobData(
            uuid=reply.blob.uuid,
            modified=reply.blob.modified,
            fetched_at=reply.blob.fetched_at,
            version=reply.blob.version
        )
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetBlob failed for {blob_uuid}: {e}')
        return None


def _grpc_get_all_blob_uuids() -> list[str]:
    """Get all blob UUIDs via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllBlobUuidsRequest()
        reply = stub.GetAllBlobUuids(request)
        return list(reply.uuids)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetAllBlobUuids failed: {e}')
        return []


def _grpc_delete_blob(blob_uuid: UUID) -> bool:
    """Delete a blob record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteBlobRequest(uuid=str(blob_uuid))
        reply = stub.DeleteBlob(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteBlob failed for {blob_uuid}: {e}')
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
        reply = stub.UpdateBlob(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpdateBlob failed for {data.uuid}: {e}')
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
        List of blob UUID strings in active states.
    """
    active_states = ['initial', 'created']
    return _direct_get_objects_by_state(ObjectType.BLOB, active_states)


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
            stmt = sa.select(table).where(table.c.uuid == dnsmasq_uuid)
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
                stmt = stmt.where(table.c.owner_uuid == owner_uuid)

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
            stmt = sa.delete(table).where(table.c.uuid == dnsmasq_uuid)
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
                table.c.uuid == data.uuid
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
        reply = stub.CreateDnsMasq(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC CreateDnsMasq failed for {data.uuid}: {e}')
        return False


def _grpc_get_dnsmasq(dnsmasq_uuid: UUID) -> Optional[DnsMasqData]:
    """Get DnsMasq static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetDnsMasqRequest(uuid=str(dnsmasq_uuid))
        reply = stub.GetDnsMasq(request)
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
        LOG.warning(f'gRPC GetDnsMasq failed for {dnsmasq_uuid}: {e}')
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
        reply = stub.GetDnsMasqs(request)
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
        LOG.warning(f'gRPC GetDnsMasqs failed: {e}')
        return []


def _grpc_delete_dnsmasq(dnsmasq_uuid: UUID) -> bool:
    """Delete a DnsMasq record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteDnsMasqRequest(uuid=str(dnsmasq_uuid))
        reply = stub.DeleteDnsMasq(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteDnsMasq failed for {dnsmasq_uuid}: {e}')
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
        reply = stub.UpdateDnsMasq(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpdateDnsMasq failed for {data.uuid}: {e}')
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
        reply = stub.RecordRelationship(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC RecordRelationship failed: {e}')
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
        reply = stub.RemoveRelationship(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC RemoveRelationship failed: {e}')
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
        reply = stub.GetReferencesTo(request)
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
        LOG.warning(f'gRPC GetReferencesTo failed: {e}')
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
        reply = stub.GetReferencesFrom(request)
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
        LOG.warning(f'gRPC GetReferencesFrom failed: {e}')
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
        reply = stub.CountReferencesTo(request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC CountReferencesTo failed: {e}')
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
        reply = stub.RemoveAllReferencesFrom(request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC RemoveAllReferencesFrom failed: {e}')
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
        reply = stub.UpdateLastActive(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpdateLastActive failed: {e}')
        return False


def _grpc_get_stale_references(older_than: float) -> list[ObjectReference]:
    """Get stale references via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetStaleReferencesRequest(older_than=older_than)
        reply = stub.GetStaleReferences(request)
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
        LOG.warning(f'gRPC GetStaleReferences failed: {e}')
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
        reply = stub.UpsertBlobHash(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpsertBlobHash failed: {e}')
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
        reply = stub.GetBlobHashes(request)
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
        LOG.warning(f'gRPC GetBlobHashes failed: {e}')
        return []


def _grpc_find_blob_by_hash(algorithm: str, hash_value: str) -> Optional[str]:
    """Find a blob UUID by hash via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.FindBlobByHashRequest(
            algorithm=algorithm,
            hash_value=hash_value
        )
        reply = stub.FindBlobByHash(request)
        if reply.found:
            return str(reply.blob_uuid)
        return None
    except grpc.RpcError as e:
        LOG.warning(f'gRPC FindBlobByHash failed: {e}')
        return None


def _grpc_get_stale_blob_hashes(older_than: float) -> list[BlobHash]:
    """Get stale blob hashes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetStaleBlobHashesRequest(older_than=older_than)
        reply = stub.GetStaleBlobHashes(request)
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
        LOG.warning(f'gRPC GetStaleBlobHashes failed: {e}')
        return []


def _grpc_delete_blob_hashes(blob_uuid: str) -> bool:
    """Delete blob hash records via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteBlobHashesRequest(blob_uuid=blob_uuid)
        reply = stub.DeleteBlobHashes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteBlobHashes failed: {e}')
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
        reply = stub.CreateBlobTransfer(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetBlobTransfer(request)
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
        LOG.warning(
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
        reply = stub.GetBlobTransfersForNode(request)
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
        LOG.warning(f'gRPC GetBlobTransfersForNode failed for {source_node}: {e}')
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
        reply = stub.GetBlobTransfersForBlob(request)
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
        LOG.warning(f'gRPC GetBlobTransfersForBlob failed for {blob_uuid}: {e}')
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
        reply = stub.UpdateBlobTransfer(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteBlobTransfer(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteStaleTransfers(request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteStaleTransfers failed: {e}')
        return 0


def _grpc_delete_blob_transfers_for_blob(blob_uuid: str) -> int:
    """Delete all transfers for a blob via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteBlobTransfersForBlobRequest(
            blob_uuid=blob_uuid
        )
        reply = stub.DeleteBlobTransfersForBlob(request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteBlobTransfersForBlob failed: {e}')
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
                        states_table.c.object_uuid
                        == sa.cast(attrs_table.c.uuid, sa.String),
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
                        states_table.c.object_uuid
                        == sa.cast(
                            attrs_table.c.uuid, sa.String),
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
        reply = stub.CreateBlobAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC CreateBlobAttributes failed for {data.uuid}: {e}')
        return False


def _grpc_get_blob_attributes(blob_uuid: UUID) -> Optional[BlobAttributesData]:
    """Get blob attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetBlobAttributesRequest(uuid=str(blob_uuid))
        reply = stub.GetBlobAttributes(request)
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
        LOG.warning(f'gRPC GetBlobAttributes failed for {blob_uuid}: {e}')
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
        reply = stub.UpdateBlobAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpdateBlobAttributes failed for {data.uuid}: {e}')
        return False


def _grpc_update_blob_last_used(blob_uuid: UUID, last_used: float) -> bool:
    """Update blob last_used via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.UpdateBlobLastUsedRequest(
            uuid=str(blob_uuid),
            last_used=last_used
        )
        reply = stub.UpdateBlobLastUsed(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpdateBlobLastUsed failed for {blob_uuid}: {e}')
        return False


def _grpc_delete_blob_attributes(blob_uuid: UUID) -> bool:
    """Delete blob attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteBlobAttributesRequest(uuid=str(blob_uuid))
        reply = stub.DeleteBlobAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteBlobAttributes failed for {blob_uuid}: {e}')
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
        reply = stub.GetExpiredBlobUuids(request)
        return list(reply.uuids)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetExpiredBlobUuids failed: {e}')
        return []


def _grpc_get_stale_transcoded_blob_uuids(idle_seconds: float) -> list[str]:
    """Get stale transcoded blob UUIDs via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetStaleTranscodedBlobUuidsRequest(
            idle_seconds=idle_seconds
        )
        reply = stub.GetStaleTranscodedBlobUuids(request)
        return list(reply.uuids)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetStaleTranscodedBlobUuids failed: {e}')
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
        reply = stub.CreateNode(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetNode(request)
        if not reply.found:
            return None
        return NodeData(
            uuid=reply.node.uuid,
            fqdn=reply.node.fqdn,
            ip=reply.node.ip,
            version=reply.node.version
        )
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetNodeByFqdn(request)
        if not reply.found:
            return None
        return NodeData(
            uuid=reply.node.uuid,
            fqdn=reply.node.fqdn,
            ip=reply.node.ip,
            version=reply.node.version
        )
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC GetNodeByFqdn failed for {fqdn}: {e}'
        )
        return None


def _grpc_get_all_node_uuids() -> list[str]:
    """Get all node UUIDs via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllNodeUuidsRequest()
        reply = stub.GetAllNodeUuids(request)
        return list(reply.uuids)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteNode(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.UpdateNode(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.CreateNodeAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetNodeAttributes(request)
        if not reply.found:
            return None
        return _node_attrs_from_proto(reply.data)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.UpdateNodeAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteNodeAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.CreateNamespace(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC CreateNamespace failed for {name}: {e}')
        return False


def _grpc_get_namespace(name: str) -> Optional[NamespaceData]:
    """Get namespace static values via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNamespaceRequest(name=name)
        reply = stub.GetNamespace(request)
        if not reply.found:
            return None
        return NamespaceData(name=reply.namespace.name, version=reply.namespace.version)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetNamespace failed for {name}: {e}')
        return None


def _grpc_get_all_namespace_names() -> list[str]:
    """Get all namespace names via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllNamespaceNamesRequest()
        reply = stub.GetAllNamespaceNames(request)
        return list(reply.names)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetAllNamespaceNames failed: {e}')
        return []


def _grpc_delete_namespace(name: str) -> bool:
    """Delete a namespace record via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNamespaceRequest(name=name)
        reply = stub.DeleteNamespace(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteNamespace failed for {name}: {e}')
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
        reply = stub.CreateNamespaceAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC CreateNamespaceAttributes failed for {data.name}: {e}')
        return False


def _grpc_get_namespace_attributes(name: str) -> Optional[NamespaceAttributesData]:
    """Get namespace attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNamespaceAttributesRequest(name=name)
        reply = stub.GetNamespaceAttributes(request)
        if not reply.found:
            return None
        return NamespaceAttributesData(
            name=reply.data.name,
            keys=json.loads(reply.data.keys_json) if reply.data.keys_json else {'nonced_keys': {}},
            trust=json.loads(reply.data.trust_json) if reply.data.trust_json else ['system'],
        )
    except grpc.RpcError as e:
        LOG.warning(f'gRPC GetNamespaceAttributes failed for {name}: {e}')
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
        reply = stub.UpdateNamespaceAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpdateNamespaceAttributes failed for {data.name}: {e}')
        return False


def _grpc_delete_namespace_attributes(name: str) -> bool:
    """Delete namespace attributes via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNamespaceAttributesRequest(name=name)
        reply = stub.DeleteNamespaceAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteNamespaceAttributes failed for {name}: {e}')
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
        reply = stub.CreateArtifact(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC CreateArtifact failed for {artifact_uuid}: {e}')
        return False


def _grpc_get_artifact(artifact_uuid: UUID) -> Optional[ArtifactData]:
    """Get artifact static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetArtifactRequest(uuid=str(artifact_uuid))
        reply = stub.GetArtifact(request)
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
        LOG.warning(
            f'gRPC GetArtifact failed for {artifact_uuid}: {e}')
        return None


def _grpc_get_all_artifacts() -> list[ArtifactData]:
    """Get all artifacts via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllArtifactsRequest()
        reply = stub.GetAllArtifacts(request)
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
        LOG.warning(f'gRPC GetAllArtifacts failed: {e}')
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
        reply = stub.UpdateArtifact(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpdateArtifact failed for {data.uuid}: {e}')
        return False


def _grpc_delete_artifact(artifact_uuid: UUID) -> bool:
    """Delete an artifact record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteArtifactRequest(
            uuid=str(artifact_uuid))
        reply = stub.DeleteArtifact(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.CreateArtifactAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetArtifactAttributes(request)
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
        LOG.warning(
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
        reply = stub.UpdateArtifactAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC UpdateArtifactAttributes failed for '
            f'{data.uuid}: {e}')
        return False


def _grpc_delete_artifact_attributes(artifact_uuid: UUID) -> bool:
    """Delete artifact attributes via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteArtifactAttributesRequest(
            uuid=str(artifact_uuid))
        reply = stub.DeleteArtifactAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.CreateArtifactIndex(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetArtifactIndex(request)
        if not reply.found:
            return None
        d = reply.data
        return ArtifactIndexData(
            artifact_uuid=UUID(d.artifact_uuid),
            index_number=d.index_number,
            blob_uuid=UUID(d.blob_uuid)
        )
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetAllArtifactIndexes(request)
        return [
            ArtifactIndexData(
                artifact_uuid=UUID(idx.artifact_uuid),
                index_number=idx.index_number,
                blob_uuid=UUID(idx.blob_uuid)
            )
            for idx in reply.indexes
        ]
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteArtifactIndex(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteAllArtifactIndexes(request)
        return int(reply.count)
    except grpc.RpcError as e:
        LOG.warning(
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
                version=data.version
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
                ipv4=data.ipv4,
                order=data.order,
                model=data.model or '',
                version=data.version
            )
        )
        reply = stub.CreateNetworkInterface(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetNetworkInterface(request)
        if not reply.found:
            return None
        d = reply.network_interface
        return NetworkInterfaceData(
            uuid=d.uuid,
            network_uuid=d.network_uuid,
            instance_uuid=d.instance_uuid,
            macaddr=d.macaddr,
            ipv4=d.ipv4,
            order=d.order,
            model=d.model or None,
            version=d.version
        )
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetNetworkInterfacesByInstance(request)
        return [
            NetworkInterfaceData(
                uuid=d.uuid,
                network_uuid=d.network_uuid,
                instance_uuid=d.instance_uuid,
                macaddr=d.macaddr,
                ipv4=d.ipv4,
                order=d.order,
                model=d.model or None,
                version=d.version
            )
            for d in reply.network_interfaces
        ]
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetNetworkInterfacesByNetwork(request)
        return [
            NetworkInterfaceData(
                uuid=d.uuid,
                network_uuid=d.network_uuid,
                instance_uuid=d.instance_uuid,
                macaddr=d.macaddr,
                ipv4=d.ipv4,
                order=d.order,
                model=d.model or None,
                version=d.version
            )
            for d in reply.network_interfaces
        ]
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC GetNetworkInterfacesByNetwork failed for '
            f'{network_uuid}: {e}')
        return []


def _grpc_delete_network_interface(ni_uuid: UUID) -> bool:
    """Delete a NetworkInterface record via the database
    microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNetworkInterfaceRequest(
            uuid=str(ni_uuid))
        reply = stub.DeleteNetworkInterface(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
                ipv4=data.ipv4,
                order=data.order,
                model=data.model or '',
                version=data.version
            )
        )
        reply = stub.UpdateNetworkInterface(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.CreateNetworkInterfaceAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetNetworkInterfaceAttributes(request)
        if not reply.found:
            return None
        return NetworkInterfaceAttributesData(
            uuid=reply.data.uuid,
            floating_address=(reply.data.floating_address
                              if reply.data.floating_address
                              else None),
        )
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.UpdateNetworkInterfaceAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteNetworkInterfaceAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.CreateIPAM(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC CreateIPAM failed for {data.uuid}: {e}')
        return False


def _grpc_get_ipam(ipam_uuid: UUID) -> Optional[IPAMData]:
    """Get IPAM static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetIPAMRequest(uuid=str(ipam_uuid))
        reply = stub.GetIPAM(request)
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
        LOG.warning(f'gRPC GetIPAM failed for {ipam_uuid}: {e}')
        return None


def _grpc_delete_ipam(ipam_uuid: UUID) -> bool:
    """Delete an IPAM record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteIPAMRequest(uuid=str(ipam_uuid))
        reply = stub.DeleteIPAM(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC DeleteIPAM failed for {ipam_uuid}: {e}')
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
        reply = stub.UpdateIPAM(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC UpdateIPAM failed for {data.uuid}: {e}')
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
                networkinterfaces=json.dumps(
                    data.networkinterfaces),
                networkinterfaces_initialized=(
                    data.networkinterfaces_initialized),
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

            # Parse JSON fields
            nis = result.networkinterfaces
            if isinstance(nis, str):
                nis = json.loads(nis)
            dns = result.hosteddns
            if isinstance(dns, str):
                dns = json.loads(dns)

            return NetworkAttributesData(
                uuid=result.uuid,
                floating_gateway=result.floating_gateway,
                networkinterfaces=nis if nis else [],
                networkinterfaces_initialized=(
                    result.networkinterfaces_initialized),
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
                networkinterfaces=json.dumps(
                    data.networkinterfaces),
                networkinterfaces_initialized=(
                    data.networkinterfaces_initialized),
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
        reply = stub.CreateNetwork(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC CreateNetwork failed for {data.uuid}: {e}')
        return False


def _grpc_get_network(
        net_uuid: UUID) -> Optional[NetworkData]:
    """Get Network static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetNetworkRequest(
            uuid=str(net_uuid))
        reply = stub.GetNetwork(request)
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
        LOG.warning(
            f'gRPC GetNetwork failed for {net_uuid}: {e}')
        return None


def _grpc_get_all_networks() -> list[NetworkData]:
    """Get all Network records via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllNetworksRequest()
        reply = stub.GetAllNetworks(request)
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
        LOG.warning(f'gRPC GetAllNetworks failed: {e}')
        return []


def _grpc_delete_network(net_uuid: UUID) -> bool:
    """Delete a Network record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteNetworkRequest(
            uuid=str(net_uuid))
        reply = stub.DeleteNetwork(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
                networkinterfaces=data.networkinterfaces,
                networkinterfaces_initialized=(
                    data.networkinterfaces_initialized),
                hosteddns_json=json.dumps(data.hosteddns)))
        reply = stub.CreateNetworkAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetNetworkAttributes(request)
        if not reply.found:
            return None
        d = reply.data
        dns = json.loads(d.hosteddns_json) if d.hosteddns_json else {}
        return NetworkAttributesData(
            uuid=d.uuid,
            floating_gateway=(
                d.floating_gateway
                if d.floating_gateway else None),
            networkinterfaces=list(d.networkinterfaces),
            networkinterfaces_initialized=(
                d.networkinterfaces_initialized),
            hosteddns=dns,
        )
    except grpc.RpcError as e:
        LOG.warning(
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
                networkinterfaces=data.networkinterfaces,
                networkinterfaces_initialized=(
                    data.networkinterfaces_initialized),
                hosteddns_json=json.dumps(data.hosteddns)))
        reply = stub.UpdateNetworkAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteNetworkAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.CreateAgentOperation(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetAgentOperation(request)
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
        LOG.warning(
            f'gRPC GetAgentOperation failed for '
            f'{aop_uuid}: {e}')
        return None


def _grpc_delete_agent_operation(aop_uuid: UUID) -> bool:
    """Delete an AgentOperation record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteAgentOperationRequest(
            uuid=str(aop_uuid))
        reply = stub.DeleteAgentOperation(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.CreateAgentOperationAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetAgentOperationAttributes(request)
        if not reply.found:
            return None
        d = reply.data
        results = json.loads(d.results_json) if d.results_json else {}
        return AgentOperationAttributesData(
            uuid=d.uuid,
            results=results,
        )
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.UpdateAgentOperationAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteAgentOperationAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
                disk_spec=json.dumps(data.disk_spec),
                memory=data.memory,
                name=data.name,
                namespace=data.namespace,
                requested_placement=json.dumps(
                    data.requested_placement),
                ssh_key=data.ssh_key or '',
                user_data=data.user_data or '',
                video=json.dumps(data.video),
                uefi=data.uefi,
                configdrive=data.configdrive,
                nvram_template=data.nvram_template or '',
                secure_boot=data.secure_boot,
                machine_type=data.machine_type,
                side_channels=json.dumps(data.side_channels),
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
                interfaces=_json_dumps(data.interfaces),
                agent_state=_json_dumps(data.agent_state),
                agent_attributes=_json_dumps(
                    data.agent_attributes),
                agent_operations=_json_dumps(
                    data.agent_operations),
                kvm_pid=data.kvm_pid,
                error_message=data.error_message or '')
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
            interfaces = _parse_json(result.interfaces)
            agent_state = _parse_json(result.agent_state)
            agent_attributes = _parse_json(
                result.agent_attributes)
            agent_operations = _parse_json(
                result.agent_operations)

            return InstanceAttributesData(
                uuid=result.uuid,
                placement=placement,
                power_state=power_state,
                ports=ports,
                enforced_deletes=enforced_deletes,
                block_devices=block_devices,
                interfaces=(
                    interfaces if interfaces else []),
                agent_state=agent_state,
                agent_attributes=agent_attributes,
                agent_operations=agent_operations,
                kvm_pid=result.kvm_pid,
                error_message=(
                    result.error_message or None),
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
                interfaces=_json_dumps(data.interfaces),
                agent_state=_json_dumps(data.agent_state),
                agent_attributes=_json_dumps(
                    data.agent_attributes),
                agent_operations=_json_dumps(
                    data.agent_operations),
                kvm_pid=data.kvm_pid,
                error_message=data.error_message or '')
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
                nvram_template=(
                    data.nvram_template or ''),
                secure_boot=data.secure_boot,
                machine_type=data.machine_type,
                side_channels_json=json.dumps(
                    data.side_channels),
                version=data.version
            )
        )
        reply = stub.CreateInstance(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetInstance(request)
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
        LOG.warning(
            f'gRPC GetInstance failed for '
            f'{inst_uuid}: {e}')
        return None


def _grpc_get_all_instances() -> list[InstanceData]:
    """Get all Instance static values via the database service."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetAllInstancesRequest()
        reply = stub.GetAllInstances(request)
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
        LOG.warning(
            f'gRPC GetAllInstances failed: {e}')
        return []


def _grpc_delete_instance(inst_uuid: UUID) -> bool:
    """Delete an Instance record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteInstanceRequest(
            uuid=str(inst_uuid))
        reply = stub.DeleteInstance(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
                interfaces_json=_json_dumps(
                    data.interfaces),
                agent_state_json=_json_dumps(
                    data.agent_state),
                agent_attributes_json=_json_dumps(
                    data.agent_attributes),
                agent_operations_json=_json_dumps(
                    data.agent_operations),
                kvm_pid=data.kvm_pid or 0,
                error_message=(
                    data.error_message or '')))
        reply = stub.CreateInstanceAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.GetInstanceAttributes(request)
        if not reply.found:
            return None
        d = reply.data

        def _parse(val: str) -> Any:
            return json.loads(val) if val else None

        interfaces = _parse(d.interfaces_json)
        return InstanceAttributesData(
            uuid=d.uuid,
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
        )
    except grpc.RpcError as e:
        LOG.warning(
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
                interfaces_json=_json_dumps(
                    data.interfaces),
                agent_state_json=_json_dumps(
                    data.agent_state),
                agent_attributes_json=_json_dumps(
                    data.agent_attributes),
                agent_operations_json=_json_dumps(
                    data.agent_operations),
                kvm_pid=data.kvm_pid or 0,
                error_message=(
                    data.error_message or '')))
        reply = stub.UpdateInstanceAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
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
        reply = stub.DeleteInstanceAttributes(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC DeleteInstanceAttributes failed for '
            f'{inst_uuid}: {e}')
        return False


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
