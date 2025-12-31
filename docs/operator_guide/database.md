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

MariaDB is used for object state storage and IPAM reservation tracking,
providing:

- Efficient queries by object type and state value
- Indexed lookups for state-based filtering
- Better performance than etcd for scanning large numbers of objects
- Atomic IP address reservation with database-level uniqueness constraints

MariaDB is deployed on etcd master nodes and uses Galera for multi-master
replication across the cluster.

### MariaDB Required (Not MySQL)

Shaken Fist requires **MariaDB** specifically, not MySQL. While MariaDB is
largely compatible with MySQL at the protocol level, Shaken Fist uses
MariaDB-specific features that are not available in MySQL:

- **INET4 column type**: Provides efficient 4-byte storage for IPv4 addresses
  (vs 15 bytes for VARCHAR) with native comparison and indexing support. This
  type was introduced in MariaDB 10.10 and is not available in MySQL.

SQLAlchemy is configured to use the `mariadb://` dialect (not `mysql://`) to
ensure proper support for these MariaDB-specific types. The underlying driver
(`mysqlclient`) remains the same since MariaDB maintains MySQL protocol
compatibility.

### Access Pattern

**Important**: Only the database service daemon (`sf-database`) has direct
access to MariaDB. All other daemons access MariaDB through the database
service's gRPC interface.

This architecture:

- Centralizes database access in a single service
- Provides consistent Prometheus metrics for all database operations
- Enables clean separation of concerns
- Simplifies connection management

The `shakenfist.mariadb` module automatically routes requests:

- If `DATABASE_USE_DIRECT_ETCD=True` (database daemon): Direct MariaDB access
- If `DATABASE_USE_DIRECT_ETCD=False` (all other daemons): gRPC to database service

### Connection

The database service connects to MariaDB using SQLAlchemy. Connection details
are configured during cluster deployment.

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
| `IPv4Address` | `INET4` (MariaDB-specific) |
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

Object state (e.g., "created", "deleted", "error") is stored in a dedicated
MariaDB table for improved query performance. Access is routed through the
database service's gRPC interface for all daemons except the database daemon
itself.

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

## IPAM Reservation Storage

IPAM (IP Address Manager) reservations are stored in MariaDB for atomic address
allocation. This provides:

- **Atomic reservation**: Uses database uniqueness constraints to prevent race
  conditions when multiple nodes try to allocate the same address
- **Efficient queries**: Indexes on ipam_uuid and address for fast lookups
- **Deletion halo**: Supports the deletion-halo pattern where recently released
  addresses are temporarily unavailable to prevent reuse conflicts

### The ipam_reservations Table

The `ipam_reservations` table uses a composite primary key on (ipam_uuid, address):

```python
from ipaddress import IPv4Address

class IPAMReservation(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                ['ipam_uuid', 'address'],      # Composite unique key
                ['user_type', 'user_uuid'],    # Query by user
            ]
        }
    )

    ipam_uuid: Annotated[str, SQLIndex(), Field(max_length=36)]
    address: Annotated[IPv4Address, SQLIndex()]  # Maps to INET4 column
    reservation_type: ReservationType            # Enum stored as VARCHAR
    user_type: Optional[str] = Field(default=None, max_length=32)
    user_uuid: Optional[str] = Field(default=None, max_length=36)
    reserved_at: float
    comment: Optional[str] = None
```

The `address` field uses Python's `ipaddress.IPv4Address` type, which maps to
MariaDB's `INET4` column type. This provides efficient 4-byte storage and native
IP address comparison operations.

### Reservation Types

IPAM supports several reservation types:

| Type | Description |
|------|-------------|
| `network` | The network address (e.g., 10.0.0.0) |
| `broadcast` | The broadcast address (e.g., 10.0.0.255) |
| `gateway` | The gateway address for the network |
| `floating` | A floating IP that can be moved between instances |
| `routed` | A routed IP address for external connectivity |
| `instance` | An IP assigned to an instance interface |
| `deletion-halo` | A recently-released address in the deletion halo |

### Migration from etcd

For existing deployments that stored IPAM reservations in etcd, use the
migration command:

```bash
# Stop all Shaken Fist services first
sf-ctl migrate-ipam-to-mariadb --dry-run

# Perform the migration
sf-ctl migrate-ipam-to-mariadb
```

This command:
1. Reads all reservations from `/sf/ipam_reservations/` in etcd
2. Writes each reservation to the MariaDB ipam_reservations table
3. Removes the reservation entries from etcd

## Administrative Commands

The `sf-ctl` command provides several database-related administrative functions.
These commands are typically used during cluster bootstrap and maintenance.

### ensure-mariadb-schema

Ensures the MariaDB schema exists and is up to date. This command must be run
on an etcd_master node (which has `MARIADB_HOST` configured):

```bash
sf-ctl ensure-mariadb-schema
```

This is automatically run during cluster deployment before any nodes are
initialized.

### initialise-node

Creates a node record in the database. By default, it uses the local node's
configuration:

```bash
sf-ctl initialise-node
```

For cluster bootstrap, this command can initialize any node when run from an
etcd_master with direct database access:

```bash
# Run on etcd_master to initialize a remote node
SHAKENFIST_DATABASE_USE_DIRECT_ETCD=True \
sf-ctl initialise-node --node-name sf-2 --node-mesh-ip 10.0.0.2
```

This is useful during deployment when the database service isn't running yet.

### register-daemon

Registers one or more daemons on a node. By default, it registers on the local
node:

```bash
sf-ctl register-daemon sentinel-first privexec nodelock
```

For cluster bootstrap, daemons can be registered on any node when run from an
etcd_master with direct database access:

```bash
# Run on etcd_master to register daemons on a remote node
SHAKENFIST_DATABASE_USE_DIRECT_ETCD=True \
sf-ctl register-daemon database --node-name sf-1
```

This allows all node and daemon registration to happen before the database
service starts, avoiding chicken-and-egg problems during bootstrap.

### migrate-state-to-mariadb

Migrates object state from etcd to MariaDB for existing deployments:

```bash
# Preview what would be migrated
sf-ctl migrate-state-to-mariadb --dry-run

# Perform the migration
sf-ctl migrate-state-to-mariadb
```

### migrate-ipam-to-mariadb

Migrates IPAM reservations from etcd to MariaDB for existing deployments:

```bash
# Preview what would be migrated
sf-ctl migrate-ipam-to-mariadb --dry-run

# Perform the migration
sf-ctl migrate-ipam-to-mariadb
```

This command scans all `/sf/ipam_reservations/` entries in etcd, converts them
to the new IPAMReservation format, writes them to MariaDB, and removes the
original entries from etcd.

### migrate-uploads-to-mariadb

Migrates upload objects from etcd to MariaDB for existing deployments:

```bash
# Preview what would be migrated
sf-ctl migrate-uploads-to-mariadb --dry-run

# Perform the migration
sf-ctl migrate-uploads-to-mariadb
```

This command scans all upload objects in etcd, writes them to the MariaDB
uploads table, and removes the original entries from etcd. Uploads are
temporary objects used during artifact creation.

## Upload Object Storage

Upload objects (temporary objects that receive streamed data during artifact
creation) are stored in MariaDB. This provides:

- **Efficient iteration**: Fast queries for cleanup of stale uploads
- **Node-based lookups**: Indexed queries to find uploads by node for routing

### The uploads Table

The `uploads` table stores static values for upload objects:

| Column | Type | Description |
|--------|------|-------------|
| uuid | UUID | Primary key - the upload's unique identifier |
| node | VARCHAR(255) | The node where the upload data is stored |
| created_at | DOUBLE | Unix timestamp when the upload was created |
| version | INTEGER | Object version number |

Indexes:
- Primary key on `uuid`
- Index on `node` for efficient routing of upload requests
- Index on `created_at` for finding old uploads during cleanup

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
