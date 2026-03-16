# Utilities for converting Pydantic models to SQLAlchemy tables and managing
# database schema lifecycle.
#
# The key insight here is that Pydantic models already define our schema - we
# just need to introspect them to generate corresponding SQL tables. This keeps
# the schema definition in one place (Python) and avoids hand-writing SQL.
#
# Field annotations:
#   Use Annotated types to add SQL-specific markers to fields:
#
#   from shakenfist.schema.sqlalchemy import SQLIndex, SQLLongText, SQLNativeUUID
#   from shakenfist.schema.sqlalchemy import SQLUniqueIndex
#
#   class MyModel(BaseModel):
#       # Regular index
#       name: Annotated[str, SQLIndex()]
#
#       # Unique index
#       email: Annotated[str, SQLUniqueIndex()]
#
#       # Native UUID storage (MariaDB 10.7+) - more efficient than CHAR(36)
#       uuid: Annotated[UUID4, SQLNativeUUID()]
#
#   For compound indexes spanning multiple columns, use model_config:
#
#   class MyModel(BaseModel):
#       model_config = ConfigDict(
#           json_schema_extra={
#               'sql_indexes': [('object_type', 'state')]
#           }
#       )

from enum import Enum
from typing import Annotated
from typing import Any
from typing import get_args
from typing import get_origin
from typing import Optional
from typing import Union

from pydantic import BaseModel
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql import mariadb
from sqlalchemy.engine.interfaces import Dialect
from shakenfist_utilities import logs


LOG, _ = logs.setup(__name__)


# Marker classes for use with Annotated types
class SQLIndex:
    """Marker to indicate this field should have a database index.

    Usage:
        uuid: Annotated[str, SQLIndex()]
    """
    pass


class SQLUniqueIndex:
    """Marker to indicate this field should have a unique database index.

    Usage:
        email: Annotated[str, SQLUniqueIndex()]
    """
    pass


class SQLNativeUUID:
    """Marker to indicate this UUID field should use native MariaDB UUID type.

    By default, UUID fields are stored as CHAR(36) strings. Use this marker
    to store them using MariaDB's native UUID type instead, which provides
    better storage efficiency (16 bytes vs 36 bytes) and indexing performance.

    Usage:
        uuid: Annotated[UUID4, SQLNativeUUID()]

    Note: This requires MariaDB 10.7+ which has native UUID support.
    """
    pass


class SQLLongText:
    """Marker to indicate this str field should use LONGTEXT instead of VARCHAR.

    By default, str fields are stored as VARCHAR(255). Use this marker for
    fields that may contain more than 255 characters (e.g., SSH keys,
    cloud-init user data).

    Usage:
        ssh_key: Annotated[Optional[str], SQLLongText()] = None
    """
    pass


def _get_index_markers_from_metadata(
        metadata: list[Any]) -> tuple[bool, bool]:
    """Extract index markers from Pydantic field metadata.

    Returns (has_index, is_unique) tuple.
    """
    for item in metadata:
        if isinstance(item, SQLUniqueIndex):
            return True, True
        if isinstance(item, SQLIndex):
            return True, False
    return False, False


def _has_native_uuid_marker(metadata: list[Any]) -> bool:
    """Check if field metadata contains a SQLNativeUUID marker."""
    for item in metadata:
        if isinstance(item, SQLNativeUUID):
            return True
    return False


def _has_longtext_marker(metadata: list[Any]) -> bool:
    """Check if field metadata contains a SQLLongText marker."""
    for item in metadata:
        if isinstance(item, SQLLongText):
            return True
    return False


def _get_index_markers(annotation: Any) -> tuple[bool, bool]:
    """Extract index markers from an Annotated type.

    Returns (has_index, is_unique) tuple.

    Note: When used with Pydantic models, the metadata is stored separately
    in field_info.metadata. Use _get_index_markers_from_metadata for that.
    """
    # Check if this is an Annotated type
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        for arg in args[1:]:  # Skip the first arg (the actual type)
            if isinstance(arg, SQLUniqueIndex):
                return True, True
            if isinstance(arg, SQLIndex):
                return True, False
    return False, False


def _get_compound_indexes(model: type[BaseModel]) -> list[tuple[str, ...]]:
    """Extract compound index definitions from model config.

    Returns list of tuples, each containing column names for a compound index.
    """
    config = getattr(model, 'model_config', {})
    if not config:
        return []

    schema_extra = config.get('json_schema_extra', {})
    if not schema_extra:
        return []

    indexes: list[tuple[str, ...]] = schema_extra.get('sql_indexes', [])
    return indexes


# Mapping from Python types to SQLAlchemy column types. Note that we use
# mysql.LONGTEXT for JSON because MariaDB's JSON type is an alias for LONGTEXT
# with JSON validation, and we want explicit control.
PYTHON_TO_SQLALCHEMY: dict[type[Any], sa.types.TypeEngine[Any]] = {
    str: sa.String(255),
    int: sa.BigInteger(),
    float: sa.Double(),
    bool: sa.Boolean(),
    bytes: sa.LargeBinary(),
}


def _unwrap_annotated(annotation: Any) -> Any:
    """Unwrap an Annotated type to get the base type.

    Returns the base type (first arg of Annotated, or the original if not
    Annotated).
    """
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


def _is_optional(annotation: Any) -> tuple[bool, Any]:
    """Check if a type annotation is Optional[X].

    Returns (is_optional, inner_type).
    """
    # First unwrap Annotated if present
    annotation = _unwrap_annotated(annotation)

    if get_origin(annotation) is Union:
        args = get_args(annotation)
        if type(None) in args:
            # It's Optional[X], get the non-None type
            inner_types = [a for a in args if a is not type(None)]
            if len(inner_types) == 1:
                return True, inner_types[0]
    return False, annotation


def _is_uuid_type(annotation: Any) -> bool:
    """Check if a type annotation is a UUID type."""
    type_str = str(annotation)
    return 'UUID' in type_str or 'uuid' in type_str.lower()


def _is_enum_type(annotation: Any) -> bool:
    """Check if a type annotation is an Enum subclass."""
    try:
        return isinstance(annotation, type) and issubclass(annotation, Enum)
    except TypeError:
        return False


def _is_complex_type(annotation: Any) -> bool:
    """Check if a type should be stored as JSON (lists, dicts, nested models).
    """
    origin = get_origin(annotation)
    if origin in (list, dict, set, frozenset):
        return True
    try:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return True
    except TypeError:
        pass
    return False


def _get_sqlalchemy_type(
        annotation: Any,
        field_metadata: Optional[list[Any]] = None) -> sa.types.TypeEngine[Any]:
    """Convert a Python type annotation to a SQLAlchemy column type.

    Args:
        annotation: The Python type annotation.
        field_metadata: Optional list of Pydantic field metadata (from
            Annotated types). Used to check for markers like SQLNativeUUID.

    Returns:
        A SQLAlchemy column type.
    """
    # Unwrap Annotated types first
    annotation = _unwrap_annotated(annotation)

    # Handle Optional[X] by extracting the inner type
    is_optional, inner_type = _is_optional(annotation)
    if is_optional:
        annotation = inner_type

    # UUID fields - check for native UUID marker first
    if _is_uuid_type(annotation):
        if field_metadata and _has_native_uuid_marker(field_metadata):
            return sa.Uuid()
        return sa.String(36)

    # Enum fields -> VARCHAR to store the name
    if _is_enum_type(annotation):
        return sa.String(64)

    # Complex types (lists, dicts, nested models) -> JSON
    if _is_complex_type(annotation):
        return mysql.LONGTEXT()

    # Check for SQLLongText marker on str fields
    if annotation is str and field_metadata and _has_longtext_marker(
            field_metadata):
        return mysql.LONGTEXT()

    # Basic Python types
    if annotation in PYTHON_TO_SQLALCHEMY:
        return PYTHON_TO_SQLALCHEMY[annotation]

    # Fallback to JSON for anything we don't recognize
    LOG.warning(f'Unknown type annotation {annotation}, falling back to JSON')
    return mysql.LONGTEXT()


def pydantic_to_sqlalchemy_table(
        model: type[BaseModel],
        table_name: str,
        metadata: sa.MetaData,
        primary_key_fields: Optional[list[str]] = None,
        indexes: Optional[list[tuple[str, ...]]] = None,
        include_id_column: bool = True) -> sa.Table:
    """Convert a Pydantic model to a SQLAlchemy Table.

    Indexes can be defined in three ways:
    1. Using Annotated with SQLIndex() or SQLUniqueIndex() markers on fields
    2. Using model_config with json_schema_extra={'sql_indexes': [...]}
    3. Passing indexes parameter directly to this function

    Args:
        model: The Pydantic model class to convert.
        table_name: Name for the SQL table.
        metadata: SQLAlchemy MetaData to attach the table to.
        primary_key_fields: If set, use these fields as the primary key instead
            of adding an auto-increment id column. For single-column primary
            keys, pass a list with one element: ['uuid']. For compound primary
            keys, pass all columns: ['source_uuid', 'target_uuid'].
        indexes: List of tuples defining indexes. Each tuple contains column
            names to index together. For example: [('object_type', 'state')]
            These are merged with indexes from model annotations.
        include_id_column: If True and no primary_key_fields is set, add an
            auto-increment id column.

    Returns:
        A SQLAlchemy Table object with indexes attached.
    """
    columns: list[sa.Column[Any]] = []
    table_indexes: list[tuple[str, bool]] = []

    # Normalize primary key specification
    pk_fields: set[str] = set(primary_key_fields) if primary_key_fields else set()

    # Add auto-increment id column unless we're using field(s) as primary key
    if include_id_column and not pk_fields:
        columns.append(
            sa.Column('id', sa.BigInteger(), primary_key=True,
                      autoincrement=True)
        )

    # Process each field in the Pydantic model
    for field_name, field_info in model.model_fields.items():
        annotation = field_info.annotation
        is_optional, _ = _is_optional(annotation)

        # Pass field metadata to get correct SQL type (e.g., native UUID)
        col_type = _get_sqlalchemy_type(annotation, field_info.metadata)
        is_pk = field_name in pk_fields

        columns.append(
            sa.Column(
                field_name,
                col_type,
                primary_key=is_pk,
                nullable=is_optional and not is_pk
            )
        )

        # Check for index markers in field metadata (Pydantic stores Annotated
        # metadata here)
        has_index, is_unique = _get_index_markers_from_metadata(
            field_info.metadata)
        if has_index:
            table_indexes.append((field_name, is_unique))

    # Collect all index definitions
    all_indexes: list[tuple[str, tuple[str, ...], bool]] = []

    # Add single-column indexes from field annotations
    for field_name, is_unique in table_indexes:
        idx_prefix = 'uidx' if is_unique else 'idx'
        idx_name = f'{idx_prefix}_{table_name}_{field_name}'
        all_indexes.append((idx_name, (field_name,), is_unique))

    # Add compound indexes from model config
    compound_indexes = _get_compound_indexes(model)
    for idx_columns in compound_indexes:
        if isinstance(idx_columns, str):
            idx_columns = (idx_columns,)
        idx_name = f'idx_{table_name}_{"_".join(idx_columns)}'
        all_indexes.append((idx_name, idx_columns, False))

    # Add indexes passed as parameter
    if indexes:
        for idx_columns in indexes:
            if isinstance(idx_columns, str):
                idx_columns = (idx_columns,)
            idx_name = f'idx_{table_name}_{"_".join(idx_columns)}'
            all_indexes.append((idx_name, idx_columns, False))

    # Create the table with indexes
    table = sa.Table(table_name, metadata, *columns)

    # Attach indexes to the table
    for idx_name, idx_columns, is_unique in all_indexes:
        sa.Index(idx_name, *[table.c[col] for col in idx_columns],
                 unique=is_unique)

    return table


def get_table_creation_sql(
        table: sa.Table,
        dialect: Optional[Dialect] = None) -> str:
    """Generate the CREATE TABLE SQL for a table.

    This is useful for debugging and for understanding what will be created.
    """
    from sqlalchemy.schema import CreateTable
    if dialect is None:
        dialect = mariadb.MariaDBDialect()
    return str(CreateTable(table).compile(dialect=dialect))


def ensure_table_exists(engine: sa.Engine, table: sa.Table) -> None:
    """Ensure a table exists in the database, creating it if necessary.

    This is idempotent - it won't fail if the table already exists.
    Indexes attached to the table (via pydantic_to_sqlalchemy_table or
    directly) will also be created.

    Args:
        engine: SQLAlchemy engine connected to the database.
        table: The Table object to create.
    """
    # Create table if it doesn't exist
    table.metadata.create_all(engine, tables=[table], checkfirst=True)

    # Create indexes attached to this table
    with engine.connect() as conn:
        for idx in table.indexes:
            try:
                idx.create(conn, checkfirst=True)
            except Exception as e:
                # Index might already exist with different definition
                LOG.debug(f'Index {idx.name} creation skipped: {e}')


def table_exists(engine: sa.Engine, table_name: str) -> bool:
    """Check if a table exists in the database."""
    return sa.inspect(engine).has_table(table_name)


def get_table_columns(engine: sa.Engine, table_name: str) -> dict[str, Any]:
    """Get the columns of an existing table.

    Returns a dict mapping column name to column info.
    """
    inspector = sa.inspect(engine)
    if not inspector.has_table(table_name):
        return {}
    return {col['name']: col for col in inspector.get_columns(table_name)}


def compare_schemas(
        engine: sa.Engine,
        table: sa.Table) -> dict[str, list[str]]:
    """Compare a Table definition with the actual database table.

    Returns a dict describing differences:
        - 'missing_columns': columns in model but not in DB
        - 'extra_columns': columns in DB but not in model
        - 'type_mismatches': columns with different types

    This is useful for detecting when a schema migration might be needed.
    """
    existing = get_table_columns(engine, table.name)
    expected = {col.name: col for col in table.columns}

    return {
        'missing_columns': [
            name for name in expected if name not in existing
        ],
        'extra_columns': [
            name for name in existing if name not in expected
        ],
        # Type comparison is complex, skipping detailed check for now
        'type_mismatches': []
    }
