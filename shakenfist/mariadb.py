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

import threading
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from shakenfist_utilities import logs

from shakenfist.config import config
from shakenfist.schema.object_state import ObjectState
from shakenfist.schema.object_state import State
from shakenfist.schema.sqlalchemy import ensure_table_exists
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table


LOG, _ = logs.setup(__name__)

# Thread-local storage for database connections
_local = threading.local()

# Module-level metadata for table definitions
_metadata: Optional[sa.MetaData] = None
_object_states_table: Optional[sa.Table] = None


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
        )
        # Add indexes
        sa.Index('idx_object_states_type_state',
                 _object_states_table.c.object_type,
                 _object_states_table.c.state_value)
    return _object_states_table


def ensure_schema() -> None:
    """Ensure the MariaDB schema exists.

    This creates the object_states table if it doesn't exist. Safe to call
    multiple times - it's idempotent.
    """
    if not config.MARIADB_HOST:
        LOG.warning('MariaDB not configured, skipping schema creation')
        return

    engine = _get_engine()
    table = _get_object_states_table()
    ensure_table_exists(engine, table)
    LOG.info('MariaDB schema verified')


def is_configured() -> bool:
    """Check if MariaDB is configured and available."""
    return bool(config.MARIADB_HOST)


def get_state(object_type: str, object_uuid: str) -> Optional[State]:
    """Read state for an object from MariaDB.

    Args:
        object_type: The type of object (e.g., 'blob', 'instance').
        object_uuid: The UUID of the object.

    Returns:
        A State object, or None if no state exists for this object.
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


def set_state(object_type: str, object_uuid: str, state: State) -> bool:
    """Write state for an object to MariaDB.

    Uses INSERT ... ON DUPLICATE KEY UPDATE for atomic upsert.

    Args:
        object_type: The type of object (e.g., 'blob', 'instance').
        object_uuid: The UUID of the object.
        state: The State object to store.

    Returns:
        True if the write succeeded, False otherwise.
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


def delete_state(object_type: str, object_uuid: str) -> bool:
    """Delete state for an object from MariaDB.

    Args:
        object_type: The type of object (e.g., 'blob', 'instance').
        object_uuid: The UUID of the object.

    Returns:
        True if the delete succeeded (or row didn't exist), False otherwise.
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
        LOG.warning(f'MariaDB delete failed for {object_type}/{object_uuid}: {e}')
        return False


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


def get_all_states_for_type(object_type: str) -> list[tuple[str, State]]:
    """Get all states for a given object type.

    Useful for migrations and debugging.

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
