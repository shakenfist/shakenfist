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
import time
import threading
from typing import Any, Callable, cast, Optional
from uuid import UUID

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
from shakenfist.schema.blob_hash import BlobHash
from shakenfist.schema.dnsmasq import DnsMasqData
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
_blob_hashes_table: Optional[sa.Table] = None

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
BLOB_HASHES_VERSION = 2


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
    results.append(_ensure_object_references_schema(engine))
    results.append(_ensure_blob_hashes_schema(engine))

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
            # Node UUIDs are node names (strings like "sf-1"), not UUIDs
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
    'object_references': {
        2: _migrate_etcd_object_references,
    },
    'blob_hashes': {
        2: _migrate_etcd_blob_hashes,
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
