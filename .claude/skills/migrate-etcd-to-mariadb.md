# Skill: Migrate Data from etcd to MariaDB

This skill guides the migration of object data from etcd to MariaDB in Shaken
Fist. It follows established patterns from successful migrations of Upload,
DnsMasq, IPAM, and blob-related data.

## When to Use This Skill

Use this skill when:
- Migrating static object values from etcd to MariaDB
- Migrating object attributes from etcd to MariaDB
- Creating new tables for high-churn or frequently-queried data
- Replacing etcd iteration patterns with efficient MariaDB queries

## Prerequisites

Before starting, ensure you understand:
- The data model of the object being migrated
- Which fields are immutable (static values) vs mutable (attributes)
- Access patterns (how the data is queried/filtered)
- Whether the data has high churn or requires atomic operations

## Static Values vs Attributes

Per the architecture documented in `docs/operator_guide/database.md`, data is
stored in **separate tables** based on mutability:

| Type | Table Pattern | Pydantic Config | Description |
|------|---------------|-----------------|-------------|
| Static Values | `{objects}` | `frozen=True` | Immutable after creation |
| Attributes | `{object}_attributes` | `frozen=False` | Can change during lifetime |

**When to use static values:**
- UUID, version, created_at timestamps
- Configuration set at creation time
- Data that never changes after the object is created

**When to use attributes:**
- Usage timestamps (last_used, last_accessed)
- Counters or metrics that update
- Metadata discovered after creation (e.g., file size, image info)
- Expiration times that can be extended

**Why separate tables?**
- Avoids wide generic tables - each type has exactly the columns it needs
- Enables proper typing - native SQL types instead of JSON everywhere
- Supports efficient indexes - can index frequently-queried attribute columns
- Keeps queries simple - no joins needed for common operations
- Separates concerns - static values are cacheable, attributes are not

## Migration Pattern Overview

The standard migration involves these files (in order of implementation):

| Step | File | Purpose |
|------|------|---------|
| 1 | `shakenfist/schema/{object}.py` | Pydantic schema definition |
| 2 | `protos/database.proto` | gRPC message and RPC definitions |
| 3 | `shakenfist/mariadb.py` | Table definition, direct functions, gRPC clients |
| 4 | `shakenfist/daemons/database/main.py` | gRPC handler implementations |
| 5 | `shakenfist/{object}.py` | Update object class to use MariaDB |
| 6 | `shakenfist/client/ctl.py` | Migration command for existing deployments |
| 7 | `shakenfist/tests/mock_etcd.py` | Update test mocks |
| 8 | `docs/operator_guide/database.md` | Document the migration command |

## Detailed Implementation Steps

### Step 1: Create Pydantic Schema

Create `shakenfist/schema/{object}.py`:

```python
# Pydantic schema for {object} object storage in MariaDB.
#
# This schema defines the structure for storing {object} static values.
# [Document the purpose and access patterns here]

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class {Object}Data(BaseModel):
    """Schema for {object} static values in MariaDB.

    This model represents the static (immutable) values for a {object} object.
    It replaces the dict-based static_values pattern with a type-safe Pydantic
    model.

    Table: {objects}
    Primary key: uuid

    Attributes:
        uuid: The {object}'s unique identifier.
        [document other fields]
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # Primary key - stored as native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # [Other fields with appropriate annotations]
    # Use SQLIndex() for fields that need indexes
    field_name: Annotated[str, SQLIndex()]

    # Object version for schema migrations
    version: int
```

Key patterns:
- Use `ConfigDict(frozen=True)` for immutable static values
- Use `SQLNativeUUID()` for UUID primary keys
- Use `SQLIndex()` for frequently-queried fields
- Use `ConfigDict.json_schema_extra['sql_indexes']` for compound indexes

### Step 2: Define gRPC Protocol

Add to `protos/database.proto`:

```protobuf
service DatabaseService {
  // ... existing RPCs ...

  // {Object} Operations (MariaDB)
  rpc Create{Object} (Create{Object}Request) returns (StatusReply) {}
  rpc Get{Object} (Get{Object}Request) returns (Get{Object}Reply) {}
  rpc GetAll{Object}s (GetAll{Object}sRequest) returns (GetAll{Object}sReply) {}
  rpc Delete{Object} (Delete{Object}Request) returns (StatusReply) {}
  // Add Update{Object} if mutable fields exist
}

// {Object} Operations (MariaDB)
// [Document purpose]

message {Object}Data {
  string uuid = 1;        // UUID as string
  string field1 = 2;      // [describe field]
  double timestamp = 3;   // Unix timestamp
  int32 version = 4;      // Object version number
}

message Create{Object}Request {
  {Object}Data data = 1;
}

message Get{Object}Request {
  string uuid = 1;
}

message Get{Object}Reply {
  bool found = 1;
  {Object}Data data = 2;
}

message GetAll{Object}sRequest {}

message GetAll{Object}sReply {
  repeated {Object}Data items = 1;
}

message Delete{Object}Request {
  string uuid = 1;
}
```

After editing, regenerate stubs:
```bash
tox -e genprotos
```

### Step 3: Implement MariaDB Functions

Add to `shakenfist/mariadb.py`:

#### 3.1 Add Table Global and Version

```python
_{objects}_table: Optional[sa.Table] = None

# Add to version history comment:
#   {objects} v1: Initial schema for {object} objects
{OBJECTS}_VERSION = 1
```

#### 3.2 Add Table Definition Function

```python
def _get_{objects}_table() -> sa.Table:
    """Get or create the {objects} table definition.

    [Document table purpose and access patterns]
    """
    global _{objects}_table
    if _{objects}_table is None:
        metadata = _get_metadata()
        _{objects}_table = sa.Table(
            '{objects}',
            metadata,
            sa.Column('uuid', sa.Uuid(), primary_key=True),
            sa.Column('field1', sa.String(255), nullable=False),
            sa.Column('timestamp', sa.Double(), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            # Add indexes for query efficiency
            sa.Index('idx_{objects}_field1', 'field1'),
        )
    return _{objects}_table
```

#### 3.3 Add Schema Ensure Function

```python
def _ensure_{objects}_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the {objects} table schema is up to date."""
    table_name = '{objects}'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_{objects}_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version {{OBJECTS}_VERSION})')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)

        with engine.connect() as conn:
            for idx in table.indexes:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception as e:
                    LOG.debug(f'Index {idx.name} creation skipped: {e}')

        current_ver = {OBJECTS}_VERSION
        _set_table_version(engine, table_name, current_ver)

    # Future migrations: if current_ver == 1: migrate to v2...

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': {OBJECTS}_VERSION,
        'migrated': start_ver != current_ver
    }
```

Add call to `ensure_schema()`:
```python
results.append(_ensure_{objects}_schema(engine))
```

#### 3.4 Add Direct Access Functions

```python
# =============================================================================
# {Object} Direct Access Functions
# =============================================================================

def _direct_create_{object}(obj_uuid: UUID, field1: str, timestamp: float,
                            version: int) -> bool:
    """Create a {object} record in MariaDB."""
    engine = _get_engine()
    table = _get_{objects}_table()

    try:
        with engine.connect() as conn:
            stmt = sa.insert(table).values(
                uuid=obj_uuid,
                field1=field1,
                timestamp=timestamp,
                version=version
            )
            conn.execute(stmt)
            conn.commit()
            return True
    except IntegrityError:
        return False
    except OperationalError as e:
        LOG.warning(f'MariaDB create failed for {object} {obj_uuid}: {e}')
        return False


def _direct_get_{object}(obj_uuid: UUID) -> Optional[dict[str, Any]]:
    """Get {object} static values from MariaDB."""
    engine = _get_engine()
    table = _get_{objects}_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table).where(table.c.uuid == obj_uuid)
            result = conn.execute(stmt).fetchone()

            if result is None:
                return None

            return {
                'uuid': str(result.uuid),
                'field1': result.field1,
                'timestamp': result.timestamp,
                'version': result.version
            }
    except OperationalError as e:
        LOG.warning(f'MariaDB get failed for {object} {obj_uuid}: {e}')
        return None


def _direct_get_all_{objects}() -> list[dict[str, Any]]:
    """Get all {objects} from MariaDB."""
    engine = _get_engine()
    table = _get_{objects}_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table)
            result = conn.execute(stmt).fetchall()

            return [
                {
                    'uuid': str(row.uuid),
                    'field1': row.field1,
                    'timestamp': row.timestamp,
                    'version': row.version
                }
                for row in result
            ]
    except OperationalError as e:
        LOG.warning(f'MariaDB query failed for all {objects}: {e}')
        return []


def _direct_delete_{object}(obj_uuid: UUID) -> bool:
    """Delete a {object} record from MariaDB."""
    engine = _get_engine()
    table = _get_{objects}_table()

    try:
        with engine.connect() as conn:
            stmt = sa.delete(table).where(table.c.uuid == obj_uuid)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB delete failed for {object} {obj_uuid}: {e}')
        return False
```

#### 3.5 Add gRPC Client Functions

```python
# =============================================================================
# {Object} gRPC Client Functions
# =============================================================================

def _grpc_create_{object}(obj_uuid: UUID, field1: str, timestamp: float,
                          version: int) -> bool:
    """Create a {object} record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.Create{Object}Request(
            data=database_pb2.{Object}Data(
                uuid=str(obj_uuid),
                field1=field1,
                timestamp=timestamp,
                version=version
            )
        )
        reply = stub.Create{Object}(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC Create{Object} failed for {obj_uuid}: {e}')
        return False


def _grpc_get_{object}(obj_uuid: UUID) -> Optional[dict[str, Any]]:
    """Get {object} static values via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.Get{Object}Request(uuid=str(obj_uuid))
        reply = stub.Get{Object}(request)
        if not reply.found:
            return None
        return {
            'uuid': reply.data.uuid,
            'field1': reply.data.field1,
            'timestamp': reply.data.timestamp,
            'version': reply.data.version
        }
    except grpc.RpcError as e:
        LOG.warning(f'gRPC Get{Object} failed for {obj_uuid}: {e}')
        return None


# [Add _grpc_get_all_{objects} and _grpc_delete_{object} similarly]
```

#### 3.6 Add Public API Functions

```python
# =============================================================================
# {Object} Public API Functions
# =============================================================================

def create_{object}(obj_uuid: UUID, field1: str, timestamp: float,
                    version: int) -> bool:
    """Create a {object} record."""
    if _use_database_service():
        return _grpc_create_{object}(obj_uuid, field1, timestamp, version)
    return _direct_create_{object}(obj_uuid, field1, timestamp, version)


def get_{object}(obj_uuid: UUID) -> Optional[dict[str, Any]]:
    """Get {object} static values."""
    if _use_database_service():
        return _grpc_get_{object}(obj_uuid)
    return _direct_get_{object}(obj_uuid)


# [Add get_all_{objects} and delete_{object} similarly]
```

### Step 4: Implement gRPC Handlers

Add to `shakenfist/daemons/database/main.py`:

```python
# {Object} Operations (MariaDB)

def Create{Object}(
    self,
    request: database_pb2.Create{Object}Request,
    context: grpc.ServicerContext
) -> database_pb2.StatusReply:
    """Create a {object} record in MariaDB."""
    try:
        self.monitor.counters['create_{object}'].inc()
        success = mariadb._direct_create_{object}(
            UUID(request.data.uuid),
            request.data.field1,
            request.data.timestamp,
            request.data.version
        )
        return database_pb2.StatusReply(success=success, error='')
    except Exception as e:
        util_exceptions.ignore_exception('database Create{Object} failed', e)
        return database_pb2.StatusReply(success=False, error=str(e))


def Get{Object}(
    self,
    request: database_pb2.Get{Object}Request,
    context: grpc.ServicerContext
) -> database_pb2.Get{Object}Reply:
    """Get {object} static values from MariaDB."""
    try:
        self.monitor.counters['get_{object}'].inc()
        data = mariadb._direct_get_{object}(UUID(request.uuid))
        if data is None:
            return database_pb2.Get{Object}Reply(found=False)
        return database_pb2.Get{Object}Reply(
            found=True,
            data=database_pb2.{Object}Data(
                uuid=data['uuid'],
                field1=data['field1'],
                timestamp=data['timestamp'],
                version=data['version']
            )
        )
    except Exception as e:
        util_exceptions.ignore_exception('database Get{Object} failed', e)
        context.set_code(grpc.StatusCode.INTERNAL)
        context.set_details(str(e))
        return database_pb2.Get{Object}Reply(found=False)

# [Add GetAll{Object}s and Delete{Object} similarly]
```

Add Prometheus counters to the Monitor class:
```python
'create_{object}', 'get_{object}', 'get_all_{objects}', 'delete_{object}'
```

### Step 5: Update Object Class

Update `shakenfist/{object}.py`:

```python
from shakenfist import mariadb
from shakenfist.schema.{object} import {Object}Data

class {Object}(dbo):
    current_version = N + 1  # Bump version

    @classmethod
    def _db_create(cls, object_uuid, static_values):
        """Create object in MariaDB instead of etcd."""
        return mariadb.create_{object}(
            uuid.UUID(object_uuid),
            static_values['field1'],
            static_values['timestamp'],
            static_values.get('version', cls.current_version)
        )

    @classmethod
    def _db_get(cls, object_uuid: str) -> Optional[{Object}Data]:
        """Get object from MariaDB instead of etcd."""
        data = mariadb.get_{object}(uuid.UUID(object_uuid))
        if not data:
            return None
        return {Object}Data(**data)

    def hard_delete(self):
        """Clean up MariaDB record on deletion."""
        mariadb.delete_{object}(uuid.UUID(self.uuid))
        super().hard_delete()
```

### Step 6: Add Migration Command

Add to `shakenfist/client/ctl.py`:

```python
@click.command()
@click.option('--dry-run', is_flag=True, default=False,
              help='Show what would be migrated without making changes')
def migrate_{objects}_to_mariadb(dry_run):
    """Migrate all {object} objects from etcd to MariaDB.

    This command should be run once during an upgrade to move {object} static
    values from etcd to the MariaDB {objects} table. All Shaken Fist services
    should be stopped before running this command.

    After migration, the {object} entries are removed from etcd.
    """
    import uuid as uuid_module
    from shakenfist.{object} import {Object}

    if not dry_run:
        click.echo('Ensuring MariaDB schema exists...')
        mariadb.ensure_schema()

    total_migrated = 0
    total_skipped = 0
    total_errors = 0

    click.echo('\nScanning for {object} objects in etcd...')

    for objkey, data in etcd.get_all('{object}', None):
        obj_uuid = objkey.split('/')[-1]

        if dry_run:
            click.echo(f'  Would migrate {obj_uuid}')
            total_migrated += 1
            continue

        try:
            success = mariadb.create_{object}(
                uuid_module.UUID(obj_uuid),
                data['field1'],
                data['timestamp'],
                data.get('version', {Object}.current_version)
            )
            if success:
                etcd.delete('{object}', None, obj_uuid)
                total_migrated += 1
            else:
                click.echo(f'  Skipping {obj_uuid}: already in MariaDB')
                etcd.delete('{object}', None, obj_uuid)
                total_skipped += 1
        except Exception as e:
            click.echo(f'  Error migrating {obj_uuid}: {e}')
            total_errors += 1

        if (total_migrated + total_skipped + total_errors) % 100 == 0:
            click.echo(
                f'  ... {total_migrated + total_skipped + total_errors} '
                '{objects} processed')

    click.echo(f'\nTotal: {total_migrated} migrated, {total_skipped} skipped, '
               f'{total_errors} errors')

    if dry_run:
        click.echo('\nThis was a dry run. No changes were made.')
    else:
        click.echo('\nMigration complete.')


cli.add_command(migrate_{objects}_to_mariadb)
```

### Step 7: Update Test Mocks

Add to `shakenfist/tests/mock_etcd.py`:

```python
# Add mock functions for the new MariaDB operations
def mock_create_{object}(uuid, field1, timestamp, version):
    # Store in mock dictionary
    pass

def mock_get_{object}(uuid):
    # Retrieve from mock dictionary
    pass

# Add to MockEtcd class setup
```

### Step 8: Update Documentation

Add to `docs/operator_guide/database.md`:

```markdown
### migrate-{objects}-to-mariadb

Migrates {object} objects from etcd to MariaDB:

\`\`\`bash
# Preview what would be migrated
sf-ctl migrate-{objects}-to-mariadb --dry-run

# Perform the migration
sf-ctl migrate-{objects}-to-mariadb
\`\`\`
```

Update the migration phases table and per-type tables section.

---

## Migrating Attributes (Separate Table Pattern)

When migrating **mutable attributes** (as opposed to static values), use a
separate `{object}_attributes` table. This section covers the differences
from the static values pattern above.

### Attribute Schema (Step 1 variation)

Create `shakenfist/schema/{object}_attributes.py`:

```python
# Pydantic schema for {object} attributes storage in MariaDB.
#
# This schema defines the structure for storing {object} mutable attributes.
# Attributes are values that can change during the object's lifetime, unlike
# static values which are immutable after creation.
#
# This is separate from {Object}Data (static values) per the architecture
# decision to keep mutable and immutable data in separate tables.

from typing import Annotated, Any, Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class {Object}AttributesData(BaseModel):
    """Schema for {object} attributes in MariaDB.

    This model represents the mutable attributes for a {object} object.
    Unlike {Object}Data (static values), these can be updated after creation.

    Table: {object}_attributes
    Primary key: uuid (references {objects}.uuid)
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # Primary key - references {objects}.uuid
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Example: timestamp attribute that updates frequently
    last_used: Annotated[Optional[float], SQLIndex()] = None

    # Example: JSON metadata discovered after creation
    info: dict[str, Any] = Field(default_factory=dict)

    # Example: expiration time (0 = never)
    expires_at: Annotated[float, SQLIndex()] = 0.0
```

Key differences from static values:
- `ConfigDict(frozen=False)` - model is mutable
- Table name is `{object}_attributes` (not `{objects}`)
- Primary key references the static values table
- Fields have defaults for lazy creation

### Attribute Table Definition (Step 3 variation)

```python
def _get_{object}_attributes_table() -> sa.Table:
    """Get or create the {object}_attributes table definition.

    This table stores mutable attributes, separate from the {objects}
    table which stores immutable static values.
    """
    global _{object}_attributes_table
    if _{object}_attributes_table is None:
        metadata = _get_metadata()
        _{object}_attributes_table = sa.Table(
            '{object}_attributes',
            metadata,
            sa.Column('uuid', sa.Uuid(), primary_key=True),
            sa.Column('last_used', sa.Double(), nullable=True),
            sa.Column('info', sa.JSON(), nullable=True),
            sa.Column('expires_at', sa.Double(), nullable=False, default=0.0),
            # Indexes for query optimization
            sa.Index('idx_{object}_attrs_last_used', 'last_used'),
            sa.Index('idx_{object}_attrs_expires_at', 'expires_at'),
            # Note: Foreign key not enforced for flexible migration ordering
        )
    return _{object}_attributes_table
```

### Optimized Single-Column Updates

For frequently-updated attributes (like `last_used`), add optimized functions:

```python
def _direct_update_{object}_last_used(obj_uuid: UUID, last_used: float) -> bool:
    """Update only the last_used attribute (optimized for frequent updates)."""
    engine = _get_engine()
    table = _get_{object}_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.update(table).where(
                table.c.uuid == obj_uuid
            ).values(last_used=last_used)
            result = conn.execute(stmt)
            conn.commit()
            return result.rowcount > 0
    except OperationalError as e:
        LOG.warning(f'MariaDB update last_used failed for {obj_uuid}: {e}')
        return False
```

### Lazy Loading in Object Class (Step 5 variation)

Attributes should be loaded lazily (on first access) rather than eagerly:

```python
class {Object}(dbo):
    def __init__(self, data: {Object}Data, ...):
        # ... existing static value handling ...

        # Lazy-load attributes from MariaDB
        self.__attributes: {Object}AttributesData | None = None
        self.__attributes_loaded = False

    def _load_attributes(self) -> {Object}AttributesData | None:
        """Load attributes from MariaDB."""
        if not self.__attributes_loaded:
            self.__attributes = mariadb.get_{object}_attributes(
                uuid.UUID(self.uuid))
            self.__attributes_loaded = True
        return self.__attributes

    def _ensure_attributes(self) -> {Object}AttributesData:
        """Ensure attributes record exists, creating with defaults if needed."""
        attrs = self._load_attributes()
        if attrs is None:
            attrs = {Object}AttributesData(uuid=uuid.UUID(self.uuid))
            mariadb.create_{object}_attributes(attrs)
            self.__attributes = attrs
        return attrs

    @property
    def last_used(self) -> Optional[float]:
        attrs = self._load_attributes()
        return attrs.last_used if attrs else None

    def record_usage(self) -> None:
        now = time.time()
        self._ensure_attributes()
        # Use optimized single-column update
        mariadb.update_{object}_last_used(uuid.UUID(self.uuid), now)
        # Update local cache
        if self.__attributes:
            self.__attributes.last_used = now  # Mutable model

    def hard_delete(self) -> None:
        # Delete attributes first (child record)
        mariadb.delete_{object}_attributes(uuid.UUID(self.uuid))
        # Delete static values
        mariadb.delete_{object}(uuid.UUID(self.uuid))
        super().hard_delete()
```

### Database-Level Query Functions

One key benefit of attributes in MariaDB is pushing filtering to the database:

```python
def get_expired_{object}_uuids(current_time: float = None) -> list[str]:
    """Get UUIDs of {objects} that have expired.

    Returns {objects} where expires_at > 0 (has expiration) AND
    expires_at < current_time (past expiration).

    This pushes filtering to the database, avoiding loading each object.
    """
    if current_time is None:
        current_time = time.time()

    engine = _get_engine()
    table = _get_{object}_attributes_table()

    try:
        with engine.connect() as conn:
            stmt = sa.select(table.c.uuid).where(
                sa.and_(
                    table.c.expires_at > 0,
                    table.c.expires_at < current_time
                )
            )
            result = conn.execute(stmt)
            return [str(row.uuid) for row in result]
    except OperationalError as e:
        LOG.warning(f'MariaDB query for expired {objects} failed: {e}')
        return []
```

This replaces patterns like:
```python
# Before (loads every object):
for obj_uuid in mariadb.get_active_{object}_uuids():
    obj = {Object}.from_db(obj_uuid)
    if obj and obj.expires_at > 0 and time.time() > obj.expires_at:
        obj.state = State({Object}.STATE_DELETED, 'expired')

# After (database-level filtering):
for obj_uuid in mariadb.get_expired_{object}_uuids():
    obj = {Object}.from_db(obj_uuid)
    if obj:
        obj.state = State({Object}.STATE_DELETED, 'expired')
```

### Attribute Migration Function

Attribute migration reads from etcd and creates rows in the attributes table:

```python
def _migrate_{object}_attributes_from_etcd(engine: sa.Engine) -> dict[str, Any]:
    """Migrate {object} attributes from etcd to MariaDB.

    This creates {object}_attributes records for existing {objects}.
    """
    migrated_count = 0
    skipped_count = 0
    error_count = 0

    # Get all {object} UUIDs from the static values table
    static_table = _get_{objects}_table()
    with engine.connect() as conn:
        stmt = sa.select(static_table.c.uuid)
        result = conn.execute(stmt)
        obj_uuids = [str(row.uuid) for row in result]

    for obj_uuid in obj_uuids:
        try:
            # Check if attributes already exist
            existing = _direct_get_{object}_attributes(UUID(obj_uuid))
            if existing:
                skipped_count += 1
                continue

            # Read attributes from etcd
            last_used_data = etcd.get('attribute/{object}', obj_uuid, 'last_used')
            info_data = etcd.get('attribute/{object}', obj_uuid, 'info')
            retention_data = etcd.get('attribute/{object}', obj_uuid, 'retention')

            # Extract values with defaults
            last_used = last_used_data.get('last_used') if last_used_data else None
            info = info_data if info_data else {}
            expires_at = (retention_data.get('expires_at', 0.0)
                          if retention_data else 0.0)

            # Create attributes record
            attrs = {Object}AttributesData(
                uuid=UUID(obj_uuid),
                last_used=last_used,
                info=info,
                expires_at=expires_at
            )
            success = _direct_create_{object}_attributes(attrs)

            if success:
                # Delete etcd attributes after successful migration
                etcd.delete('attribute/{object}', obj_uuid, 'last_used')
                etcd.delete('attribute/{object}', obj_uuid, 'info')
                etcd.delete('attribute/{object}', obj_uuid, 'retention')
                migrated_count += 1
            else:
                error_count += 1

        except Exception as e:
            LOG.warning(
                f'Failed to migrate attributes for {object} {obj_uuid}: {e}')
            error_count += 1

    return {
        'migrated': migrated_count,
        'skipped': skipped_count,
        'errors': error_count
    }
```

Register in `DATA_MIGRATIONS`:
```python
DATA_MIGRATIONS = {
    # Static values migration
    '{objects}': {
        2: _migrate_etcd_{objects},
    },
    # Attributes migration (separate entry, also version 2)
    '{object}_attributes': {
        2: _migrate_{object}_attributes_from_etcd,
    },
}
```

Note: Both static values and attributes migrations use version 2 because version 1
represents the table creation step (handled by `ensure_schema`).

---

## Verification Checklist

After implementation:

1. **Run tests**: `tox` (all tests should pass)
2. **Check types**: `tox -emypy -- shakenfist/{object}.py`
3. **Check formatting**: `tox -eflake8 -- -HEAD`
4. **Verify proto generation**: `tox -e genprotos` completes without error
5. **Manual testing**:
   - Create a new {object} and verify it appears in MariaDB
   - Query the {object} from another daemon
   - Delete the {object} and verify cleanup

## Common Patterns and Pitfalls

### Do

- Use `SQLNativeUUID()` for UUID columns (efficient storage)
- Add indexes for frequently-queried columns
- Bump the object's `current_version` when changing storage
- Use atomic database operations where possible
- Follow existing naming conventions (`_direct_*`, `_grpc_*`, public API)
- Use `Optional[float]` for parameters with `None` defaults (mypy requires explicit
  Optional for type safety - PEP 484 prohibits implicit Optional)

### Don't

- Don't store complex nested structures - use JSON columns or separate tables
- Don't forget to update `hard_delete()` to clean up MariaDB records
- Don't skip the migration command - existing deployments need it
- Don't forget Prometheus counters for observability
- Don't use `param: float = None` - this causes mypy errors; use
  `param: Optional[float] = None` instead

## Related Documentation

- [CLAUDE.md](../../CLAUDE.md) - Project-level development guidelines
- [docs/operator_guide/database.md](../../docs/operator_guide/database.md) - Full database documentation
- [docs/plans/blob-storage-roadmap.md](../../docs/plans/blob-storage-roadmap.md) - Example migration roadmap
