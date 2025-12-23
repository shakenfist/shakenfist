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

import time
import threading
from typing import Any, Optional

import grpc
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from shakenfist_utilities import logs

from shakenfist.config import config
from shakenfist.protos import database_pb2
from shakenfist.protos import database_pb2_grpc
from shakenfist.schema.object_state import State


LOG, _ = logs.setup(__name__)

# Thread-local storage for database connections and gRPC channels
_local = threading.local()

# Module-level metadata for table definitions
_metadata: Optional[sa.MetaData] = None
_schema_versions_table: Optional[sa.Table] = None
_object_states_table: Optional[sa.Table] = None

# Current schema versions for each table. Increment when making schema changes.
OBJECT_STATES_VERSION = 1


def _use_database_service() -> bool:
    """Check if we should use the database microservice instead of direct access.

    Returns True if the database service is configured and enabled.
    Returns False if DATABASE_USE_DIRECT_ETCD is True (database daemon mode).
    """
    # The database daemon sets DATABASE_USE_DIRECT_ETCD=true (not via the
    # environment variable mechanism, but by not setting the env var that
    # would set it to false). When this is true, we use direct MariaDB access.
    if config.DATABASE_USE_DIRECT_ETCD:
        return False
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
        # DatabaseServiceStub is generated untyped code
        _local.database_stub = database_pb2_grpc.DatabaseServiceStub(
            _local.database_channel)  # type: ignore[no-untyped-call]
    return _local.database_stub


# =============================================================================
# Direct MariaDB Access Functions
# These are used by the database daemon which needs direct access.
# =============================================================================

def _get_connection_url() -> str:
    """Build the MariaDB connection URL from config."""
    if not config.MARIADB_HOST:
        raise RuntimeError('MARIADB_HOST not configured')

    # Use mysqldb driver which is available via python3-mysqldb
    return (
        f'mysql+mysqldb://{config.MARIADB_USER}:{config.MARIADB_PASSWORD}'
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
            sa.Column('object_type', sa.String(32), nullable=False),
            sa.Column('state_value', sa.String(32), nullable=True),
            sa.Column('update_time', sa.Double(), nullable=False),
            sa.Column('message', sa.String(255), nullable=True),
            # Composite primary key
            sa.PrimaryKeyConstraint('object_type', 'object_uuid'),
            # Index for efficient queries by type and state
            sa.Index('idx_object_states_type_state', 'object_type', 'state_value'),
        )
    return _object_states_table


def _ensure_object_states_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the object_states table schema is up to date.

    Applies any necessary migrations based on the current version.
    Returns a dict with migration status information.
    """
    table_name = 'object_states'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_object_states_table()

    # Version 0 or -1 means table doesn't exist yet - create it
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

    # Future migrations would go here, following this pattern:
    # if current_ver == 1:
    #     LOG.info('Upgrading object_states from v1 to v2')
    #     with engine.connect() as conn:
    #         conn.execute(sa.text('ALTER TABLE object_states ADD COLUMN ...'))
    #         conn.commit()
    #     current_ver = 2
    #     _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': OBJECT_STATES_VERSION,
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

    # Log summary
    migrated = [r for r in results if r['migrated']]
    if migrated:
        LOG.info(f'MariaDB schema updated: {len(migrated)} table(s) migrated')
    else:
        LOG.info('MariaDB schema verified (no migrations needed)')

    return results


def _direct_get_state(object_type: str, object_uuid: str) -> Optional[State]:
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


def _direct_set_state(object_type: str, object_uuid: str, state: State) -> bool:
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


def _direct_delete_state(object_type: str, object_uuid: str) -> bool:
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


def _direct_get_objects_by_state(object_type: str,
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

def _grpc_get_state(object_type: str, object_uuid: str) -> Optional[State]:
    """Read state for an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetObjectStateRequest(
            object_type=object_type,
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


def _grpc_set_state(object_type: str, object_uuid: str, state: State) -> bool:
    """Write state for an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.SetObjectStateRequest(
            object_type=object_type,
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


def _grpc_delete_state(object_type: str, object_uuid: str) -> bool:
    """Delete state for an object via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.DeleteObjectStateRequest(
            object_type=object_type,
            object_uuid=object_uuid
        )
        reply = stub.DeleteObjectState(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC DeleteObjectState failed for {object_type}/{object_uuid}: {e}')
        return False


def _grpc_get_objects_by_state(object_type: str,
                               state_values: list[str]) -> list[str]:
    """Get all object UUIDs of a given type in specified states via gRPC."""
    try:
        stub = _get_database_stub()
        request = database_pb2.GetObjectsByStateRequest(
            object_type=object_type,
            state_values=state_values
        )
        reply = stub.GetObjectsByState(request)
        return list(reply.object_uuids)
    except grpc.RpcError as e:
        LOG.warning(
            f'gRPC GetObjectsByState failed for {object_type}: {e}')
        return []


# =============================================================================
# Public API Functions
# These route to either direct or gRPC access based on configuration.
# =============================================================================

def get_state(object_type: str, object_uuid: str) -> Optional[State]:
    """Read state for an object.

    Args:
        object_type: The type of object (e.g., 'blob', 'instance').
        object_uuid: The UUID of the object.

    Returns:
        A State object, or None if no state exists for this object.
    """
    if _use_database_service():
        return _grpc_get_state(object_type, object_uuid)
    return _direct_get_state(object_type, object_uuid)


def set_state(object_type: str, object_uuid: str, state: State) -> bool:
    """Write state for an object.

    Args:
        object_type: The type of object (e.g., 'blob', 'instance').
        object_uuid: The UUID of the object.
        state: The State object to store.

    Returns:
        True if the write succeeded, False otherwise.
    """
    if _use_database_service():
        return _grpc_set_state(object_type, object_uuid, state)
    return _direct_set_state(object_type, object_uuid, state)


def delete_state(object_type: str, object_uuid: str) -> bool:
    """Delete state for an object.

    Args:
        object_type: The type of object (e.g., 'blob', 'instance').
        object_uuid: The UUID of the object.

    Returns:
        True if the delete succeeded (or row didn't exist), False otherwise.
    """
    if _use_database_service():
        return _grpc_delete_state(object_type, object_uuid)
    return _direct_delete_state(object_type, object_uuid)


def get_objects_by_state(object_type: str,
                         state_values: list[str]) -> list[str]:
    """Get all object UUIDs of a given type in specified states.

    This is the primary use case for MariaDB state storage - efficient
    queries across object states without scanning all objects in etcd.

    Args:
        object_type: The type of object (e.g., 'blob', 'instance').
        state_values: List of state values to match.

    Returns:
        List of object UUIDs matching the criteria.
    """
    if _use_database_service():
        return _grpc_get_objects_by_state(object_type, state_values)
    return _direct_get_objects_by_state(object_type, state_values)


def get_all_states_for_type(object_type: str) -> list[tuple[str, State]]:
    """Get all states for a given object type.

    Useful for migrations and debugging. This function always uses direct
    access as it's only called by admin tools and the database daemon.

    Args:
        object_type: The type of object (e.g., 'blob', 'instance').

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
