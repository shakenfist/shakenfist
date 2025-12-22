# Database Architecture

Shaken Fist uses a combination of databases for different purposes. This page
describes the database architecture, how data is organized, and how the schema
system works.

## Overview

Shaken Fist currently uses two database backends:

- **etcd**: A distributed key-value store used for cluster coordination,
  configuration, locks, and object storage.
- **MariaDB**: A relational database being introduced for structured data that
  benefits from SQL queries and indexing.

## etcd

etcd is the primary database for Shaken Fist and is used for:

- **Object storage**: All Shaken Fist objects (instances, networks, blobs,
  artifacts, etc.) are stored in etcd.
- **Cluster coordination**: Node discovery, leader election, and distributed
  state.
- **Distributed locking**: See the [Locks](locks.md) documentation.
- **Configuration**: Cluster-wide configuration stored at `/sf/config`.
- **Event logs**: Audit trails and operational events for objects.
- **Queues**: Work queues for cluster operations.

### Key Structure

etcd keys follow a hierarchical structure:

```
/sf/                          # Root prefix for all Shaken Fist data
/sf/object/{type}/{uuid}      # Object definitions
/sf/attribute/{type}/{uuid}/  # Object attributes (state, placement, etc.)
/sf/event/{type}/{uuid}/      # Event logs for objects
/sf/queue/                    # Work queues
/sflocks/                     # Distributed locks
```

### Object Types

Each object type has a dedicated key prefix:

| Object Type | Key Prefix |
|-------------|------------|
| Instance | `/sf/object/instance/` |
| Network | `/sf/object/network/` |
| Network Interface | `/sf/object/networkinterface/` |
| Blob | `/sf/object/blob/` |
| Artifact | `/sf/object/artifact/` |
| Node | `/sf/object/node/` |
| Namespace | `/sf/object/namespace/` |

## MariaDB

MariaDB is being introduced for data that benefits from:

- Complex queries with filtering and sorting
- Indexed lookups on multiple columns
- Structured data with well-defined schemas
- Transaction support

MariaDB is deployed on etcd master nodes and uses Galera for multi-master
replication across the cluster.

### Connection

Shaken Fist connects to MariaDB using SQLAlchemy. The connection details are
configured during cluster deployment.

## Schema System

Shaken Fist uses Pydantic models for schema definition. These models serve
multiple purposes:

1. **Validation**: Ensuring data conforms to expected types and constraints
2. **Serialization**: Converting between Python objects and JSON for etcd
3. **SQL Generation**: Automatically generating SQLAlchemy tables for MariaDB

### Pydantic Models

Schema definitions live in `shakenfist/schema/`. For example, cluster operations
have their schemas defined in `shakenfist/schema/operations/`.

A typical schema looks like:

```python
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, UUID4

class model_tasks(Enum):
    verify_size_and_checksum = 1
    ensure_local = 2

class model(BaseModel):
    uuid: UUID4
    node_uuid: str
    blob_uuid: UUID4
    priority: PRIORITY
    request_id: Optional[str]
    tasks: List[model_tasks]
    version: int = Field(ge=1, le=1)
```

### SQLAlchemy Table Generation

The `shakenfist.schema.sqlalchemy` module provides utilities to automatically
convert Pydantic models to SQLAlchemy tables. This keeps the schema definition
in one place and avoids hand-writing SQL.

#### Basic Usage

```python
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
import sqlalchemy as sa

metadata = sa.MetaData()
table = pydantic_to_sqlalchemy_table(
    MyModel,
    'my_table',
    metadata,
    primary_key_field='uuid'
)
```

#### Type Mapping

Python types are mapped to SQL column types:

| Python Type | SQL Type |
|-------------|----------|
| `str` | `VARCHAR(255)` |
| `int` | `BIGINT` |
| `float` | `DOUBLE` |
| `bool` | `BOOLEAN` |
| `bytes` | `LARGEBINARY` |
| `UUID` | `CHAR(36)` |
| `Enum` | `VARCHAR(64)` |
| `list`, `dict`, nested models | `LONGTEXT` (JSON) |
| `Optional[X]` | Nullable column of type X |

### Index Annotations

Indexes can be defined directly in the Pydantic model using Python's
`Annotated` types. This keeps index definitions co-located with the schema.

#### Single-Column Indexes

Use `SQLIndex()` or `SQLUniqueIndex()` markers:

```python
from typing import Annotated
from pydantic import BaseModel
from shakenfist.schema.sqlalchemy import SQLIndex, SQLUniqueIndex

class User(BaseModel):
    uuid: Annotated[str, SQLIndex()]           # Creates idx_users_uuid
    email: Annotated[str, SQLUniqueIndex()]    # Creates uidx_users_email
    name: str                                   # No index
```

#### Compound Indexes

For indexes spanning multiple columns, use the model's configuration:

```python
from pydantic import BaseModel, ConfigDict

class Event(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                ('object_type', 'object_uuid'),  # Compound index
                ('timestamp',),                   # Single column via config
            ]
        }
    )

    object_type: str
    object_uuid: str
    timestamp: float
    message: str
```

#### Generated Index Names

Index names follow a predictable pattern:

- Single-column: `idx_{table}_{column}` or `uidx_{table}_{column}` (unique)
- Compound: `idx_{table}_{col1}_{col2}_{...}`

### Table Lifecycle

The `ensure_table_exists()` function handles idempotent table creation:

```python
from shakenfist.schema.sqlalchemy import (
    pydantic_to_sqlalchemy_table,
    ensure_table_exists
)

# Create table definition
table = pydantic_to_sqlalchemy_table(MyModel, 'my_table', metadata)

# Create table and indexes in database (idempotent)
ensure_table_exists(engine, table)
```

### Schema Comparison

To detect schema drift between the Pydantic model and the database:

```python
from shakenfist.schema.sqlalchemy import compare_schemas

differences = compare_schemas(engine, table)
# Returns: {
#     'missing_columns': [...],  # In model but not in DB
#     'extra_columns': [...],    # In DB but not in model
#     'type_mismatches': [...]   # Different types
# }
```

## Object State Storage

Object state (e.g., "created", "deleted", "error") is being migrated from etcd
attributes to a dedicated MariaDB table for improved query performance.

### The object_states Table

The `object_states` table stores state for all object types:

```python
from typing import Annotated, Optional
from pydantic import BaseModel, ConfigDict, Field
from shakenfist.schema.sqlalchemy import SQLIndex, SQLUniqueIndex

class ObjectState(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                ['object_type', 'state_value'],  # Efficient queries by type+state
            ]
        }
    )

    object_uuid: Annotated[str, SQLUniqueIndex(), Field(max_length=36)]
    object_type: Annotated[str, SQLIndex(), Field(max_length=32)]
    state_value: Annotated[str, SQLIndex(), Field(max_length=32)]
    update_time: float
    message: Optional[str] = None
```

### State Class

The `State` class is a Pydantic model that replaces the original `baseobject.State`
class. It provides the same interface for backwards compatibility:

```python
from shakenfist.schema.object_state import State

state = State(value='created', update_time=time.time(), message='optional msg')
print(state.value)        # 'created'
print(state.update_time)  # 1234567890.123
print(state.obj_dict())   # {'value': 'created', 'update_time': 1234567890.123}
```

### Migration from etcd

For existing deployments that stored state in etcd, use the migration command:

```bash
# Stop all Shaken Fist services first
sf-ctl migrate-state-to-mariadb
```

This command:
1. Reads state from etcd for all object types
2. Writes the state to MariaDB
3. Removes the state entries from etcd

Use `--dry-run` to preview what would be migrated without making changes.

MariaDB is now required for all deployments - state is stored only in MariaDB,
not in etcd.

## Best Practices

### Schema Evolution

When adding new fields:

1. Add the field to the Pydantic model with a default value
2. Use `Optional[X]` for fields that may not exist in old data
3. Include a version field to track schema versions
4. Handle missing fields gracefully in code

### Rolling Deployments

During rolling upgrades where nodes may run different versions:

1. New fields should be optional until all nodes are upgraded
2. Old code should ignore unknown fields
3. Use version fields to detect and handle schema differences

### Performance Considerations

- Use indexes for fields that are frequently queried
- Prefer compound indexes for queries that filter on multiple columns
- Keep JSON/LONGTEXT fields for data that doesn't need indexing
- Use MariaDB for data requiring complex queries; etcd for simple key-value
  lookups
