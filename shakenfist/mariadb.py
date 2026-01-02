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
from typing import Any, cast, Optional
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
from shakenfist.schema.dnsmasq import DnsMasqData
from shakenfist.schema.ipam_reservation import IPAMReservation
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
from shakenfist.schema.upload import UploadData


LOG, _ = logs.setup(__name__)

# Thread-local storage for database connections and gRPC channels
_local = threading.local()

# Module-level metadata for table definitions
_metadata: Optional[sa.MetaData] = None
_schema_versions_table: Optional[sa.Table] = None
_object_states_table: Optional[sa.Table] = None
_ipam_reservations_table: Optional[sa.Table] = None
_uploads_table: Optional[sa.Table] = None
_dnsmasq_table: Optional[sa.Table] = None

# Current schema versions for each table. Increment when making schema changes.
# Version history:
#   object_states v1: Initial schema with VARCHAR(32) for object_type
#   object_states v2: Changed object_type from VARCHAR(32) to ENUM(ObjectType)
#   ipam_reservations v1: Initial schema with VARCHAR(32) for reservation_type
#                         and VARCHAR(45) for address
#   ipam_reservations v2: Changed reservation_type from VARCHAR(32) to
#                         ENUM(ReservationType)
#   ipam_reservations v3: Changed address from VARCHAR(45) to INET4
#   ipam_reservations v4: Changed user_type from VARCHAR(32) to ENUM(ObjectType)
#   ipam_reservations v5: Changed ipam_uuid and user_uuid from VARCHAR(36) to UUID
#   uploads v1: Initial schema for upload objects
#   dnsmasq v1: Initial schema for DnsMasq objects
OBJECT_STATES_VERSION = 2
IPAM_RESERVATIONS_VERSION = 5
UPLOADS_VERSION = 1
DNSMASQ_VERSION = 1


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

    # Version 0 or -1 means table doesn't exist yet - create it with current
    # schema (which includes the ENUM type)
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version {OBJECT_STATES_VERSION})')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = OBJECT_STATES_VERSION
        _set_table_version(engine, table_name, current_ver)

    # Migration from v1 to v2: Convert object_type from VARCHAR(32) to ENUM
    if current_ver == 1:
        LOG.info('Upgrading object_states from v1 to v2: '
                 'converting object_type to ENUM')
        enum_values = _build_object_type_enum_values()
        with engine.connect() as conn:
            # ALTER TABLE to change column type from VARCHAR to ENUM
            # MariaDB will automatically convert existing string values to
            # enum values if they match
            conn.execute(sa.text(
                f'ALTER TABLE object_states '
                f'MODIFY COLUMN object_type ENUM({enum_values}) NOT NULL'
            ))
            conn.commit()
        current_ver = 2
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

    # Version 0 or -1 means table doesn't exist yet - create it with current
    # schema (which includes the ENUM and INET4 types)
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version {IPAM_RESERVATIONS_VERSION})')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = IPAM_RESERVATIONS_VERSION
        _set_table_version(engine, table_name, current_ver)

    # Migration from v1 to v2: Convert reservation_type from VARCHAR(32) to ENUM
    if current_ver == 1:
        LOG.info('Upgrading ipam_reservations from v1 to v2: '
                 'converting reservation_type to ENUM')
        enum_values = _build_reservation_type_enum_values()
        with engine.connect() as conn:
            # ALTER TABLE to change column type from VARCHAR to ENUM
            # MariaDB will automatically convert existing string values to
            # enum values if they match
            conn.execute(sa.text(
                f'ALTER TABLE ipam_reservations '
                f'MODIFY COLUMN reservation_type ENUM({enum_values}) NOT NULL'
            ))
            conn.commit()
        current_ver = 2
        _set_table_version(engine, table_name, current_ver)

    # Migration from v2 to v3: Convert address from VARCHAR(45) to INET4
    if current_ver == 2:
        LOG.info('Upgrading ipam_reservations from v2 to v3: '
                 'converting address to INET4')
        with engine.connect() as conn:
            # ALTER TABLE to change column type from VARCHAR to INET4
            # MariaDB will automatically convert existing IP string values
            # to INET4 format
            conn.execute(sa.text(
                'ALTER TABLE ipam_reservations '
                'MODIFY COLUMN address INET4 NOT NULL'
            ))
            conn.commit()
        current_ver = 3
        _set_table_version(engine, table_name, current_ver)

    # Migration from v3 to v4: Convert user_type from VARCHAR(32) to ENUM
    if current_ver == 3:
        LOG.info('Upgrading ipam_reservations from v3 to v4: '
                 'converting user_type to ENUM')
        enum_values = _build_object_type_enum_values()
        with engine.connect() as conn:
            # ALTER TABLE to change column type from VARCHAR to ENUM
            # MariaDB will automatically convert existing string values to
            # enum values if they match. NULL values remain NULL.
            conn.execute(sa.text(
                f'ALTER TABLE ipam_reservations '
                f'MODIFY COLUMN user_type ENUM({enum_values}) NULL'
            ))
            conn.commit()
        current_ver = 4
        _set_table_version(engine, table_name, current_ver)

    # Migration from v4 to v5: Convert UUID columns from VARCHAR(36) to UUID
    if current_ver == 4:
        LOG.info('Upgrading ipam_reservations from v4 to v5: '
                 'converting UUID columns to native UUID type')
        with engine.connect() as conn:
            # ALTER TABLE to change ipam_uuid and user_uuid from VARCHAR to UUID
            # MariaDB will automatically convert existing UUID string values
            conn.execute(sa.text(
                'ALTER TABLE ipam_reservations '
                'MODIFY COLUMN ipam_uuid UUID NOT NULL, '
                'MODIFY COLUMN user_uuid UUID NULL'
            ))
            conn.commit()
        current_ver = 5
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
            primary_key_field='uuid',
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

    # Version 0 or -1 means table doesn't exist yet - create it with current
    # schema
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version {UPLOADS_VERSION})')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = UPLOADS_VERSION
        _set_table_version(engine, table_name, current_ver)

    # Future migrations would go here (if current_ver == 1: ...)

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
            primary_key_field='uuid',
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

    # Version 0 or -1 means table doesn't exist yet - create it with current
    # schema
    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version {DNSMASQ_VERSION})')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        # Create indexes
        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = DNSMASQ_VERSION
        _set_table_version(engine, table_name, current_ver)

    # Future migrations would go here (if current_ver == 1: ...)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': DNSMASQ_VERSION,
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

    # Log summary
    migrated = [r for r in results if r['migrated']]
    if migrated:
        LOG.info(f'MariaDB schema updated: {len(migrated)} table(s) migrated')
    else:
        LOG.info('MariaDB schema verified (no migrations needed)')

    return results


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
