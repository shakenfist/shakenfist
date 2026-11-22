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

import sys
import threading
from enum import Enum
from types import TracebackType
from typing import Annotated
from typing import Any
from typing import get_args
from typing import get_origin
from typing import Optional
from typing import Union

from pydantic import BaseModel
from pydantic import SecretStr
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql import mariadb
from sqlalchemy.engine.interfaces import Dialect
from shakenfist_utilities import logs

from shakenfist import exceptions


LOG, _ = logs.setup(__name__)


class _ContentionLoggingRLock:
    """RLock wrapper that logs contention to surface lock deadlocks.

    Same semantics as ``threading.RLock`` (re-entrant by the holding
    thread, ``with`` protocol, ``acquire`` / ``release``). On entry
    the wrapper first tries a non-blocking acquire; if that fails it
    logs the calling thread + caller frame, then blocks for the real
    acquire and logs again on success. A wrapping thread that ever
    logs "blocking" without a matching "acquired after block" is
    deadlocked on the lock -- the visibility the sf-database silent
    wedge needs to pin "table init deadlock" vs "wedge is elsewhere".

    Cheap when uncontended: one non-blocking acquire and two
    attribute lookups per ``with`` site. Logging only fires on real
    contention, so the steady-state cost is effectively zero.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> bool:
        if self._lock.acquire(blocking=False):
            return True
        tid = threading.get_ident()
        caller = _short_caller()
        LOG.debug(
            'TABLE_CREATION_LOCK blocking; thread=%d; caller=%s',
            tid, caller)
        self._lock.acquire()
        LOG.debug(
            'TABLE_CREATION_LOCK acquired after block; thread=%d; '
            'caller=%s', tid, caller)
        return True

    def __exit__(
            self,
            exc_type: Optional[type[BaseException]],
            exc: Optional[BaseException],
            tb: Optional[TracebackType]) -> None:
        self._lock.release()


def _short_caller() -> str:
    """Return ``filename:lineno`` for the frame two above this call.

    Skips the wrapper's own frame and the ``__enter__`` frame so the
    label points at the ``with TABLE_CREATION_LOCK:`` site. Best-effort
    -- returns ``<unknown>`` if sys._getframe is unavailable.
    """
    try:
        frame = sys._getframe(2)
        return f'{frame.f_code.co_filename.rsplit("/", 1)[-1]}:{frame.f_lineno}'
    except Exception:
        return '<unknown>'


# Lazy-init of SQLAlchemy Table objects from many ``_get_*_table()``
# helpers in ``mariadb.py`` is not thread-safe: two threads can both
# observe the module-level cache as ``None`` and both call this
# function, with the second one raising ``InvalidRequestError`` when
# its ``sa.Table(...)`` registration finds the name already in the
# metadata. Serialise table creation here, and short-circuit if the
# table is already registered, so the second thread gets the existing
# Table object instead of failing.
#
# Shared across this module and ``mariadb.py`` so manual ``sa.Table``
# registrations in mariadb (object_states, cluster_locks,
# node_daemon_states) get the same protection as the
# pydantic_to_sqlalchemy_table path. RLock because the ``_get_*_table``
# wrappers in ``mariadb.py`` take the lock and then call
# ``pydantic_to_sqlalchemy_table``, which re-enters it.
#
# Wrapped with contention logging so the sf-database silent-wedge
# investigation can see whether the wedge is a deadlock here vs.
# elsewhere -- see ``_ContentionLoggingRLock`` above.
TABLE_CREATION_LOCK = _ContentionLoggingRLock()


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
    """Marker to indicate this UUID field should use a uuid_column_type() column.

    By default, UUID fields are stored as VARCHAR(36) dashed strings. Use
    this marker to store them as undashed CHAR(32) hex instead, bound and
    read back as uuid.UUID objects. Despite the name this is not MariaDB's
    native UUID type -- see uuid_column_type() for why.

    Usage:
        uuid: Annotated[UUID4, SQLNativeUUID()]
    """
    pass


def uuid_column_type() -> sa.Uuid[Any]:
    """The column type for every uuid column stored as undashed CHAR(32).

    Every SQLAlchemy Uuid column in Shaken Fist must use this rather than a
    bare sa.Uuid(). The existing tables were all created as CHAR(32), and
    SQLAlchemy 2.1 made its MariaDB dialect report native UUID support: a
    bare sa.Uuid() then renders new columns as UUID, hands uuid.UUID binds
    to the driver unconverted rather than as the undashed hex the CHAR(32)
    columns hold (so nothing matches), and returns plain strings instead of
    UUID objects. That silently disabled coalescing in the SQLAlchemy 2.1 merge
    queue run. native_uuid=False keeps the CHAR(32) hex behaviour on every
    SQLAlchemy version, and test_uuid_column_type enforces its use.
    """
    return sa.Uuid(native_uuid=False)


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
#
# SecretStr must have an explicit entry here, and it is load bearing
# rather than bookkeeping. SecretStr is not a BaseModel subclass, so
# _is_complex_type() declines it, and 'uuid' does not appear in its
# str() so _is_uuid_type() declines it too -- without an entry it falls
# through to the unknown-type fallback below, which only logs a warning
# and returns LONGTEXT. Because the _ensure_*_schema() functions in
# mariadb.py create tables from the model only when the table is absent,
# and have no ALTER path, that would give a fresh install LONGTEXT while
# every upgraded cluster kept VARCHAR(255), with no schema version
# change for verify_schema_versions() to complain about. Mapping it to
# the same String(255) as str keeps the generated DDL byte-identical, so
# wrapping an existing column's field in SecretStr needs no migration.
PYTHON_TO_SQLALCHEMY: dict[type[Any], sa.types.TypeEngine[Any]] = {
    str: sa.String(255),
    SecretStr: sa.String(255),
    int: sa.BigInteger(),
    float: sa.Double(),
    bool: sa.Boolean(),
    bytes: sa.LargeBinary(),
}


# The width a str field gets when it declares nothing wider, which is the
# entry above and the number every existing VARCHAR column in the tree was
# created at.
DEFAULT_STRING_LENGTH = 255

# The widest VARCHAR a declaration may ask for. MariaDB's limit is a
# 65,535 byte maximum row size shared by every column in the row, and at
# utf8mb4 a character costs four bytes, so 16,383 characters is the most
# a single VARCHAR can occupy even in a row holding nothing else. A field
# which genuinely needs more is a LONGTEXT, which is stored away from the
# row and costs it only a pointer, and SQLLongText() is how a field says
# so. Refusing above this bound rather than silently emitting LONGTEXT
# keeps the decision with the person writing the model: a LONGTEXT column
# cannot be indexed without a prefix length, which is not a thing to
# acquire by accident.
MAX_VARCHAR_LENGTH = 16383

# The two limits MAX_VARCHAR_LENGTH cannot enforce, because each is a
# property of a whole table rather than of one field. A per-field bound
# lets two VARCHAR(16383) columns each pass while together they exceed
# the row, and lets a widened column pass while an index over it cannot
# be built. Both failures happen only when the table is created, which
# is only on a fresh install -- every upgraded cluster already has the
# table -- so _check_table_limits() refuses them when the definition is
# built instead, where the unit suite sees them.
#
# MAX_ROW_BYTES is MariaDB's server-level row size limit, which every
# engine shares. MAX_INDEX_KEY_BYTES is InnoDB's index key limit with
# the DYNAMIC row format MariaDB 10.11 defaults to: 3072 bytes, which at
# four bytes per utf8mb4 character is 768 characters for a lone
# VARCHAR, and is shared by every column of a compound index.
MAX_ROW_BYTES = 65535
MAX_INDEX_KEY_BYTES = 3072
UTF8MB4_BYTES_PER_CHARACTER = 4

# The numeric column types the builder makes, costed as an upper bound:
# BIGINT and DOUBLE are eight bytes and BOOLEAN is one. Over-counting
# here can only refuse a table that would have fitted, and by seven
# bytes at most per column.
FIXED_WIDTH_COLUMN_TYPES = (sa.BigInteger, sa.Integer, sa.Double, sa.Float,
                            sa.Boolean)
FIXED_WIDTH_COLUMN_BYTES = 8

# The types stored off the page, which cost the row only a pointer and
# length -- 9 to 12 bytes depending on the type; 12 is the LONGTEXT and
# LONGBLOB figure. Named rather than found through sa.Text, because
# mysql.LONGTEXT is not an sa.Text subclass: it derives from sa.String,
# and would otherwise be costed as a VARCHAR with no length.
OFF_PAGE_COLUMN_TYPES = (sa.Text, sa.LargeBinary, mysql.TINYTEXT,
                         mysql.TEXT, mysql.MEDIUMTEXT, mysql.LONGTEXT,
                         mysql.LONGBLOB, mysql.MEDIUMBLOB)
OFF_PAGE_COLUMN_ROW_BYTES = 12


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


def _declared_max_length(
        field_metadata: Optional[list[Any]]) -> Optional[int]:
    """The character limit a field's own declaration asks for.

    Field(max_length=...) arrives as an annotated_types.MaxLen in the
    field's metadata, and a field may carry more than one -- an
    Annotated chain can name two -- in which case the strictest is the
    one Pydantic enforces. None means the field declares no limit.
    """
    if not field_metadata:
        return None

    declared = None
    for item in field_metadata:
        candidate = getattr(item, 'max_length', None)
        if candidate is None:
            continue
        declared = candidate if declared is None else min(declared, candidate)
    return declared


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
            return uuid_column_type()
        return sa.String(36)

    # Enum fields -> VARCHAR to store the name
    if _is_enum_type(annotation):
        return sa.String(64)

    # Complex types (lists, dicts, nested models) -> JSON
    if _is_complex_type(annotation):
        return mysql.LONGTEXT()

    # Check for SQLLongText marker on str fields. SecretStr honours the
    # marker too, so that a secret too long for VARCHAR(255) -- a PEM
    # private key, say -- does not silently get a 255 byte column when it
    # has been marked otherwise.
    if annotation in (str, SecretStr) and field_metadata and (
            _has_longtext_marker(field_metadata)):
        return mysql.LONGTEXT()

    # A str field declaring more than the default width gets a column
    # wide enough to hold it. Without this the declared limit and the
    # column were independent numbers: the model accepted a value
    # MariaDB then refused, or -- with strict mode off -- truncated, and
    # the only thing holding them together was whoever remembered to add
    # SQLLongText() as well. The declaration is now what the column is
    # built from, so the two cannot disagree.
    #
    # Only upwards. A field declaring less than the default keeps its
    # VARCHAR(255), because narrowing a column is a migration on every
    # table in the tree to enforce a bound the model already enforces
    # first, and the model refuses the over-long value before MariaDB
    # ever sees it. stored_max_length() is how a validator asks which of
    # the two numbers is the real bound.
    if annotation in (str, SecretStr):
        declared = _declared_max_length(field_metadata)
        if declared is not None and declared > DEFAULT_STRING_LENGTH:
            if declared > MAX_VARCHAR_LENGTH:
                raise exceptions.InvalidSchemaDeclaration(
                    'a str field declares max_length=%d, which is wider '
                    'than the %d characters a VARCHAR can hold; mark it '
                    'SQLLongText() if it really needs that much'
                    % (declared, MAX_VARCHAR_LENGTH))
            return sa.String(declared)

    # Basic Python types
    if annotation in PYTHON_TO_SQLALCHEMY:
        return PYTHON_TO_SQLALCHEMY[annotation]

    # Fallback to JSON for anything we don't recognize
    LOG.warning(f'Unknown type annotation {annotation}, falling back to JSON')
    return mysql.LONGTEXT()


def _column_bytes(
        col_type: sa.types.TypeEngine[Any]) -> tuple[Optional[int], int]:
    """(index key bytes, row bytes) for one column type.

    The key bytes are None for a TEXT or BLOB column, which InnoDB will
    not index without a prefix length the builder has no way to say.

    A type this does not recognise is refused rather than given a
    guessed cost: a guess is a check that silently stops checking.
    """
    if isinstance(col_type, OFF_PAGE_COLUMN_TYPES):
        return None, OFF_PAGE_COLUMN_ROW_BYTES
    if isinstance(col_type, sa.Uuid):
        # uuid_column_type() is CHAR(32) of undashed hex.
        stored = 32 * UTF8MB4_BYTES_PER_CHARACTER
        return stored, stored
    if isinstance(col_type, sa.String) and col_type.length is not None:
        stored = col_type.length * UTF8MB4_BYTES_PER_CHARACTER
        # A VARCHAR spends one length byte on a value of up to 255
        # bytes and two above that.
        return stored, stored + (1 if stored <= 255 else 2)
    if isinstance(col_type, FIXED_WIDTH_COLUMN_TYPES):
        return FIXED_WIDTH_COLUMN_BYTES, FIXED_WIDTH_COLUMN_BYTES
    raise exceptions.InvalidSchemaDeclaration(
        'cannot cost a %r column against the row and index key limits; '
        'add its storage size to _column_bytes()' % col_type)


def _check_table_limits(
        table_name: str, columns: list[sa.Column[Any]],
        indexes: list[tuple[str, tuple[str, ...]]]) -> None:
    """Refuse a table over MariaDB's row or InnoDB's index key limit.

    ``indexes`` is every index the table will carry, the primary key
    included, as (name, column names). See MAX_ROW_BYTES for why this
    is checked here rather than left to CREATE TABLE.

    This catches the two limits a widened declaration reaches, and is
    not a complete model of what InnoDB refuses: its on-page row size
    bound (roughly half a page) and the extra byte it spends per
    nullable key column are not counted. Passing here is therefore
    necessary for CREATE TABLE to succeed, not sufficient.
    """
    by_name = {column.name: _column_bytes(column.type) for column in columns}

    # Plus the row's null bitmap, a bit per nullable column.
    nullable = sum(1 for column in columns if column.nullable)
    row_bytes = (sum(row for _, row in by_name.values())
                 + (nullable + 7) // 8)
    if row_bytes > MAX_ROW_BYTES:
        raise exceptions.InvalidSchemaDeclaration(
            'table %s would need %d bytes per row at utf8mb4, over the '
            '%d byte row limit MariaDB applies to the sum of its columns; '
            'lower a str field\'s max_length or mark it SQLLongText(), '
            'which is stored off the row'
            % (table_name, row_bytes, MAX_ROW_BYTES))

    for idx_name, idx_columns in indexes:
        key_bytes = 0
        for col in idx_columns:
            column_key_bytes = by_name[col][0]
            if column_key_bytes is None:
                raise exceptions.InvalidSchemaDeclaration(
                    'index %s on table %s includes %s, a TEXT or BLOB '
                    'column, which InnoDB cannot index without a prefix '
                    'length' % (idx_name, table_name, col))
            key_bytes += column_key_bytes
        if key_bytes > MAX_INDEX_KEY_BYTES:
            raise exceptions.InvalidSchemaDeclaration(
                'index %s on table %s (%s) would need a %d byte key at '
                'utf8mb4, over InnoDB\'s %d byte index key limit; lower '
                'the max_length of its str fields (%d characters is the '
                'most a lone VARCHAR may index) or index a shorter column'
                % (idx_name, table_name, ', '.join(idx_columns), key_bytes,
                   MAX_INDEX_KEY_BYTES,
                   MAX_INDEX_KEY_BYTES // UTF8MB4_BYTES_PER_CHARACTER))


def stored_max_length(
        model: type[BaseModel], field_name: str) -> Optional[int]:
    """How many characters this field's generated column can really hold.

    Field(max_length=...) constrains what the model accepts, and
    _get_sqlalchemy_type() now builds a column wide enough to hold it,
    so a declaration over the 255 character default is no longer a
    number the column knows nothing about. The two can still differ in
    the other direction, which is deliberate and harmless: a field
    declaring less than its column holds is refused by the model before
    MariaDB sees it.

    This returns the smaller of the two, taken from the same
    _get_sqlalchemy_type() call pydantic_to_sqlalchemy_table() builds the
    column with, so a validator written against it cannot drift from the
    schema. None means nothing applies: a LONGTEXT column whose field
    declares no limit.

    Validate operator or third party supplied strings against this
    rather than against either number in isolation.

    It answers from the column the *current model* would build, not from
    the column the database has, so it does not and cannot describe a
    cluster whose migration has not run yet. What protects that window
    is mariadb.verify_schema_versions(), which refuses to start a daemon
    against a table at the wrong version.
    """
    field_info = model.model_fields[field_name]
    column_limit = getattr(
        _get_sqlalchemy_type(field_info.annotation, field_info.metadata),
        'length', None)
    declared = _declared_max_length(field_info.metadata)

    limits = [limit for limit in (column_limit, declared) if limit is not None]
    return min(limits) if limits else None


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

    checked_indexes = [(idx_name, tuple(idx_columns))
                       for idx_name, idx_columns, _ in all_indexes]
    primary_key = tuple(
        column.name for column in columns if column.primary_key)
    if primary_key:
        checked_indexes.append(('PRIMARY', primary_key))
    _check_table_limits(table_name, columns, checked_indexes)

    # Serialise table creation; if the table was registered while we
    # were waiting on the lock, hand back the existing Table object
    # rather than re-registering and tripping SQLAlchemy's
    # already-defined check.
    with TABLE_CREATION_LOCK:
        if table_name in metadata.tables:
            return metadata.tables[table_name]

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
