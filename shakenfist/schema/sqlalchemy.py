# Utilities for converting Pydantic models to SQLAlchemy tables and managing
# database schema lifecycle.
#
# The key insight here is that Pydantic models already define our schema - we
# just need to introspect them to generate corresponding SQL tables. This keeps
# the schema definition in one place (Python) and avoids hand-writing SQL.

from enum import Enum
from typing import get_args
from typing import get_origin
from typing import Union

from pydantic import BaseModel
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from shakenfist_utilities import logs


LOG, _ = logs.setup(__name__)


# Mapping from Python types to SQLAlchemy column types. Note that we use
# mysql.LONGTEXT for JSON because MariaDB's JSON type is an alias for LONGTEXT
# with JSON validation, and we want explicit control.
PYTHON_TO_SQLALCHEMY = {
    str: sa.String(255),
    int: sa.BigInteger(),
    float: sa.Double(),
    bool: sa.Boolean(),
    bytes: sa.LargeBinary(),
}


def _is_optional(annotation) -> tuple:
    """Check if a type annotation is Optional[X].

    Returns (is_optional, inner_type).
    """
    if get_origin(annotation) is Union:
        args = get_args(annotation)
        if type(None) in args:
            # It's Optional[X], get the non-None type
            inner_types = [a for a in args if a is not type(None)]
            if len(inner_types) == 1:
                return True, inner_types[0]
    return False, annotation


def _is_uuid_type(annotation) -> bool:
    """Check if a type annotation is a UUID type."""
    type_str = str(annotation)
    return 'UUID' in type_str or 'uuid' in type_str.lower()


def _is_enum_type(annotation) -> bool:
    """Check if a type annotation is an Enum subclass."""
    try:
        return isinstance(annotation, type) and issubclass(annotation, Enum)
    except TypeError:
        return False


def _is_complex_type(annotation) -> bool:
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


def _get_sqlalchemy_type(annotation):
    """Convert a Python type annotation to a SQLAlchemy column type."""
    # Handle Optional[X] by extracting the inner type
    is_optional, inner_type = _is_optional(annotation)
    if is_optional:
        annotation = inner_type

    # UUID fields -> CHAR(36)
    if _is_uuid_type(annotation):
        return sa.String(36)

    # Enum fields -> VARCHAR to store the name
    if _is_enum_type(annotation):
        return sa.String(64)

    # Complex types (lists, dicts, nested models) -> JSON
    if _is_complex_type(annotation):
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
        primary_key_field: str = None,
        indexes: list[tuple] = None,
        include_id_column: bool = True) -> sa.Table:
    """Convert a Pydantic model to a SQLAlchemy Table.

    Args:
        model: The Pydantic model class to convert.
        table_name: Name for the SQL table.
        metadata: SQLAlchemy MetaData to attach the table to.
        primary_key_field: If set, use this field as the primary key instead
            of adding an auto-increment id column.
        indexes: List of tuples defining indexes. Each tuple contains column
            names to index together. For example: [('object_type', 'state')]
        include_id_column: If True and no primary_key_field is set, add an
            auto-increment id column.

    Returns:
        A SQLAlchemy Table object.
    """
    columns = []

    # Add auto-increment id column unless we're using a field as primary key
    if include_id_column and not primary_key_field:
        columns.append(
            sa.Column('id', sa.BigInteger(), primary_key=True,
                      autoincrement=True)
        )

    # Process each field in the Pydantic model
    for field_name, field_info in model.model_fields.items():
        annotation = field_info.annotation
        is_optional, _ = _is_optional(annotation)

        col_type = _get_sqlalchemy_type(annotation)
        is_pk = (field_name == primary_key_field)

        columns.append(
            sa.Column(
                field_name,
                col_type,
                primary_key=is_pk,
                nullable=is_optional and not is_pk
            )
        )

    # Create the table
    table = sa.Table(table_name, metadata, *columns)

    return table


def get_table_creation_sql(table: sa.Table, dialect=None) -> str:
    """Generate the CREATE TABLE SQL for a table.

    This is useful for debugging and for understanding what will be created.
    """
    from sqlalchemy.schema import CreateTable
    if dialect is None:
        dialect = mysql.dialect()
    return str(CreateTable(table).compile(dialect=dialect))


def ensure_table_exists(engine: sa.Engine, table: sa.Table,
                        indexes: list[tuple] = None):
    """Ensure a table exists in the database, creating it if necessary.

    This is idempotent - it won't fail if the table already exists.

    Args:
        engine: SQLAlchemy engine connected to the database.
        table: The Table object to create.
        indexes: List of index definitions as tuples of column names.
    """
    # Create table if it doesn't exist
    table.metadata.create_all(engine, tables=[table], checkfirst=True)

    # Create indexes if specified
    if indexes:
        with engine.connect() as conn:
            for idx_columns in indexes:
                idx_name = f'idx_{table.name}_{"_".join(idx_columns)}'
                # Check if index exists before creating
                try:
                    idx = sa.Index(
                        idx_name,
                        *[table.c[col] for col in idx_columns]
                    )
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    # Index might already exist with different definition
                    LOG.debug(f'Index {idx_name} creation skipped: {e}')


def table_exists(engine: sa.Engine, table_name: str) -> bool:
    """Check if a table exists in the database."""
    return sa.inspect(engine).has_table(table_name)


def get_table_columns(engine: sa.Engine, table_name: str) -> dict:
    """Get the columns of an existing table.

    Returns a dict mapping column name to column info.
    """
    inspector = sa.inspect(engine)
    if not inspector.has_table(table_name):
        return {}
    return {col['name']: col for col in inspector.get_columns(table_name)}


def compare_schemas(engine: sa.Engine, table: sa.Table) -> dict:
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
