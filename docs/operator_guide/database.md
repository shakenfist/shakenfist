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

- **Object storage**: Shaken Fist objects not yet migrated to MariaDB
  (instances, networks, etc.) are stored in etcd.
- **Cluster coordination**: Node discovery, leader election, and distributed
  state.
- **Distributed locking**: See the [Locks](locks.md) documentation.
- **Configuration**: Cluster-wide configuration stored at `/sf/config`.
- **Event log DLQ**: Dead-letter storage for event log entries that could
  not be delivered to the eventlog gRPC service.

Cluster operation headers and the per-node work queues were originally
stored in etcd under `/sf/{op_type}/{uuid}`, `/sf/queue/{queue}/...`
and `/sf/processing/{queue}/...`. They have been moved to the
`cluster_operations` and `work_queue` MariaDB tables; the
corresponding data migrations run automatically on database daemon
startup and drain any residual etcd keys into MariaDB.

### Key Structure

etcd keys follow a hierarchical structure:

```
/sf/                          # Root prefix for all Shaken Fist data
/sf/object/{type}/{uuid}      # Object definitions
/sf/attribute/{type}/{uuid}/  # Object attributes (state, placement, etc.)
/sf/event/{type}/{uuid}/      # Event logs for objects
/sflocks/                     # Distributed locks
```

### Object Types

Each object type has a dedicated key prefix:

| Object Type | Key Prefix |
|-------------|------------|
| Instance | MariaDB `instances` table (migrated from etcd) |
| Network | MariaDB `networks` table (migrated from etcd) |
| Network Interface | MariaDB `network_interfaces` table (migrated from etcd) |
| AgentOperation | MariaDB `agent_operations` table (migrated from etcd) |
| Blob | `/sf/object/blob/` |
| Artifact | MariaDB `artifacts` table (migrated from etcd) |
| Node | MariaDB `nodes` table (migrated from etcd) |
| Namespace | MariaDB `namespaces` table (migrated from etcd) |

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

#### SQL Filter Pushdown

Object iteration uses a single indexed SQL query per call rather than materialising all rows and filtering in Python.

The filter criteria shape is `ObjectFilterCriteria` in
[`shakenfist/schema/object_filter.py`](../../shakenfist/schema/object_filter.py):

```python
from shakenfist.schema.object_filter import ObjectFilterCriteria

criteria = ObjectFilterCriteria(
    states=['created'],          # None means no state filter; [] is a no-op at the SQL layer
    namespace='tenant-a',        # None means no namespace filter
    name=None,                   # None means no name filter
    network_uuid=None,           # FK filter — see NetworkInterface special case below
    instance_uuid=None,          # FK filter — see NetworkInterface special case below
)
```

`None` on any field is "do not filter on this field". An empty list on `states` behaves the same as `None` at
the MariaDB layer, but callers may pass `[]` to express "no matching states" explicitly for future use.

**The `find_*` primitives.**
Four public functions in `shakenfist.mariadb` follow the naming convention `find_<type>`:
`find_artifacts`, `find_instances`, `find_networks`, and `find_network_interfaces`. Each one JOINs the
per-type static-values table to `object_states` on `uuid` and `object_type`, then applies whichever of the
three optional WHERE clauses the criteria specifies. The JOIN is always covered by the composite index
`idx_object_states_type_state` on `(object_type, state_value)`. The per-type `name` and `namespace` columns
each have their own single-column index on the type table.

**When to use which entry point.**

Name lookups from REST handlers should call the per-type `from_db_by_ref(name, namespace=ns)` class method
(e.g. `Artifact.from_db_by_ref(ref, namespace=ns)`). This override was added in phases 2 and 3 and pushes
the name equality predicate to SQL.

Bulk iteration scoped by state and/or namespace should use the iterator constructor directly:
`Artifacts(namespace=ns, prefilter='active')`, `Instances(namespace=ns)`, `Networks(namespace=ns)`.
The iterator's `_find` override builds an `ObjectFilterCriteria` from the constructor arguments and delegates
to the appropriate `find_*` primitive, so both state and namespace reach SQL without a second round-trip.

Arbitrary-predicate filtering — logic that has no simple SQL equivalent, such as
`namespace_or_shared_filter` which must JOIN the `artifact_attributes` table to check the `shared` flag —
should pass a callable to the `filters=` argument of the iterator, or call `.filter([predicate])` on the
class. These predicates execute in Python after the indexed SQL scan returns its rows.

**NetworkInterface special case.**
The `network_interfaces` table has no `namespace` or `name` column. `find_network_interfaces` therefore
strips both fields from the criteria before building the query; they are silently ignored. State pushdown
still works. The two FK filter fields `network_uuid` and `instance_uuid` *are* honoured — they map to
indexed columns on the `network_interfaces` table, and they are how `Network.networkinterfaces` and
`Instance.interfaces` resolve their per-parent NI list (phase 7 of the SQL-pushdown plan: those properties
return hydrated `NetworkInterface` objects rather than the cached UUID list that used to live on the
attribute table). The other `find_*` helpers leave the FK fields at their default of `None` because the
underlying tables have no matching column.

See the Future-work entry in
[`docs/plans/PLAN-sql-pushdown-filtering.md`](../plans/PLAN-sql-pushdown-filtering.md) ("NetworkInterface
namespace column") for the deferred discussion of whether to add the column or use a JOIN-based approach
once a concrete caller exists.

**Example.**

```python
from shakenfist import mariadb
from shakenfist.schema.object_filter import ObjectFilterCriteria

criteria = ObjectFilterCriteria(states=['created'], namespace='tenant-a')
for a in mariadb.find_artifacts(criteria):
    ...
```

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

For existing deployments that stored state in etcd, migration happens
automatically when the database daemon starts. The migration reads state
from etcd for all object types, writes it to MariaDB, and removes the old
etcd entries.

MariaDB is now required for all deployments - state is stored only in
MariaDB, not in etcd.

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

For existing deployments that stored IPAM reservations in etcd, migration
happens automatically when the database daemon starts. The migration reads
all reservations from etcd, writes them to the MariaDB `ipam_reservations`
table, and removes the original etcd entries.

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
# Run on the database node to initialize a remote node
SHAKENFIST_MARIADB_HOST=localhost \
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
# Run on the database node to register daemons on a remote node
SHAKENFIST_MARIADB_HOST=localhost \
sf-ctl register-daemon database --node-name sf-1
```

This allows all node and daemon registration to happen before the database
service starts, avoiding chicken-and-egg problems during bootstrap.

### Data Migrations

Data migrations from etcd to MariaDB (for object states, IPAM reservations,
uploads, blobs, nodes, and other types) run automatically when the database
daemon starts. No manual commands are needed -- simply upgrade and restart
the `sf-database` service. See the
[Automatic Data Migrations](#automatic-data-migrations) section for details.

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

## etcd to MariaDB Migration Strategy

Shaken Fist is progressively migrating data from etcd to MariaDB. This section
documents the overall strategy and table architecture for developers and
operators.

### Why Migrate?

etcd is excellent for distributed coordination but has limitations for object
storage:

- **Scan performance**: etcd is optimized for key lookups, not range scans
- **Query flexibility**: No support for filtering, sorting, or aggregation
- **Storage efficiency**: JSON serialization is less efficient than native types
- **Index support**: No secondary indexes for efficient lookups by attribute

MariaDB addresses these limitations while maintaining the distributed nature
of the cluster through Galera replication.

### Migration Phases

The migration is happening in phases:

| Phase | Data | Status |
|-------|------|--------|
| 1 | Object state | Complete - `object_states` table |
| 2 | IPAM reservations | Complete - `ipam_reservations` table |
| 3 | Upload objects | Complete - `uploads` table |
| 4 | Blob objects | Complete - `blobs`, `blob_attributes`, `blob_hashes` tables |
| 5 | Node objects | Complete - `nodes`, `node_attributes` tables |
| 6 | DnsMasq objects | Complete - `dnsmasq` table |
| 7 | Namespace objects | Complete - `namespaces`, `namespace_attributes` tables |
| 8 | Artifact objects | Complete - `artifacts`, `artifact_attributes`, `artifact_indexes` tables |
| 9 | NetworkInterface objects | Complete - `network_interfaces`, `network_interface_attributes` tables |
| 10 | IPAM objects | Complete - `ipams` table |
| 11 | Network objects | Complete - `networks`, `network_attributes` tables |
| 12 | AgentOperation objects | Complete - `agent_operations`, `agent_operation_attributes` tables |
| 13 | Instance objects | Complete - `instances`, `instance_attributes` tables |
| 14 | Object metadata | Complete - `object_metadata` table (user metadata). Schema v3 drops the legacy `last_cluster_operation_json` column via `ALTER TABLE ... DROP COLUMN IF EXISTS`; the column was unused since `last_cluster_operation` moved to `cluster_operation_targets`. |
| 15 | Cluster operation targets | Complete - `cluster_operation_targets` table (operation ordering per object) |
| 16 | Node metrics | Complete - `node_metrics` table (ephemeral per-node resource metrics, JSON payload) |
| 17 | Cluster operations | Complete - `cluster_operations` table (full operation metadata with indexed node/instance/network/priority columns) |
| 18 | Work queues | Complete - `work_queue` table (per-job row with claim state, replacing etcd two-prefix design) |
| 19 | Per-daemon state | Complete - `node_daemon_states` table; replaces the JSON `daemon_states` dict that used to live on `node_attributes` |
| 20 | Leased cluster locks | Complete - `cluster_locks` schema bumped to v3 to add `expires_at`; lock rows now self-expire on dead-holder partition rather than requiring a reaper |

### Table Architecture

The MariaDB schema uses different table patterns depending on the data
characteristics:

#### Shared Tables (DatabaseBackedObject level)

Data that has the same schema across all object types is stored in shared
tables with `(object_type, object_uuid)` keys:

| Table | Purpose |
|-------|---------|
| `object_states` | State value, update time, message for all objects |
| `object_metadata` | User-defined metadata for all objects |

These tables are efficient for cross-type queries (e.g., "find all objects
in error state").

#### High-Churn Dedicated Tables

Some data has high write frequency or requires atomic operations with database
constraints. These get dedicated tables optimized for their access patterns:

| Table | Purpose |
|-------|---------|
| `ipam_reservations` | IP address allocations with uniqueness constraints |
| `cluster_operations` | Full cluster operation metadata with indexed `node_uuid`, `instance_uuid`, `network_uuid` and `priority` columns extracted from JSON for dispatch-time filtering |
| `work_queue` | Per-job queue row with `queue_name`, `scheduled_at`, `claimed_at`, `claimed_by`, `attempts` and `payload`. Dequeue uses `SELECT ... FOR UPDATE SKIP LOCKED` |
| `cluster_operation_targets` | Operation-to-object targeting with AUTO_INCREMENT ordering |
| `cluster_operation_errors` | One row per failed cluster operation, keyed by `op_uuid`. Stores the structured `ErrorReport` (code, message, details, origin_class, traceback) JSON. Cleaned up alongside the `cluster_operations` row by `BaseClusterOperation.hard_delete()` when the cluster cleaner reaps a terminal-state op |
| `node_metrics` | Ephemeral per-node resource metrics with semi-schemaless JSON payload |
| `node_daemon_states` | Per-`(node, daemon)` state rows; atomic upsert per daemon, no Python-side coarse lock |
| `cluster_locks` | Leased distributed locks. `expires_at` lets candidates steal a dead holder's lock without external GC; holders refresh every ~20 s while alive |

IPAM reservations are stored separately because:

- **Atomic allocation**: Database uniqueness constraints prevent race conditions
- **High churn**: Addresses are frequently reserved and released
- **Cross-object queries**: Need to find all addresses for an IPAM, not just
  one object

Cluster operation headers (`cluster_operations`) and work queue rows
(`work_queue`) are stored in MariaDB so the create-and-enqueue step
can run in a single transaction (header row + state row + queue
row). The old etcd design split queued and claimed work items across
`/sf/queue/` and `/sf/processing/` prefixes; MariaDB row locking
allows a single table with claim fields instead, and
`SELECT ... FOR UPDATE SKIP LOCKED` gives race-safe dequeue.

The cluster daemon runs
`reap_stuck_cluster_operation_jobs()` from
`shakenfist/daemons/cluster/scheduled_tasks.py` on a one-minute
schedule. It re-queues or rejects rows whose claim has gone stale:

- **`CLUSTER_OP_STUCK_THRESHOLD`** — seconds before a claimed row is
  considered stuck (default `1800`). Lower values detect crashed
  workers faster at the cost of possibly re-queuing merely slow jobs.
- **`CLUSTER_OP_MAX_ATTEMPTS`** — maximum claim attempts before the
  reaper stops re-queuing and transitions the underlying cluster
  operation to `STATE_ERROR` (default `5`). Protects the queue from
  a "job of death" that crashes every worker.
- **`CLUSTER_METRICS_PORT`** — Prometheus scrape port exposed by the
  cluster daemon (default `13007`). Metrics
  `cluster_op_reaper_requeued_total` and
  `cluster_op_reaper_rejected_total` record reaper activity.

Cluster operation targets are stored separately because:

- **Append-only history**: Every operation enqueued against an object creates
  a row, giving full operation history per target
- **Automatic ordering**: AUTO_INCREMENT sequence_number replaces the implicit
  dependency chain traversal
- **Indexed queries**: Efficient lookups for "latest operation on this instance"
  and "all operations on this object in order"

Because the table is append-only, it is bounded by a periodic prune in the
cluster daemon (alongside the existing `delete_stale_transfers` cleanup).
The cluster daemon runs cluster-wide cleanup under `ClusterLock` election,
so the prune naturally runs from a single node at a time. The prune
removes rows whose `created_at` is older than
`CLUSTER_OPERATION_TARGET_RETENTION` seconds **and** whose operation is not
currently in an active state (`queued`, `preflight`, or `executing`) in
`object_states`. Operations still in flight are never pruned regardless of
age. Set `CLUSTER_OPERATION_TARGET_RETENTION` to 0 to disable pruning
entirely (the default is 7 days).

#### Cluster Operation Target Tracking

The `cluster_operation_targets` table holds one row per (operation, target
object) pair. Each row carries the target's object type and UUID, plus the
`operation_uuid` and an `AUTO_INCREMENT` `sequence_number` that gives
total ordering per target.

Two query shapes are exposed to the rest of the system:

- **`get_latest_cluster_operation_target`**: returns the highest-sequence
  row for a given `(object_type, uuid)` pair, regardless of state. Used
  by the `last_cluster_operation` property and `external_view()`
  projections to provide the familiar "which op ran last?" answer.
- **`has_pending_cluster_operation_target`**: returns `True` if any row
  for the object references an operation whose state is `queued`,
  `preflight`, or `executing`. Used by `Network.is_okay()` and any other
  gate that must defer while work is in flight. Because it checks all
  rows rather than only the latest one, a later terminal operation cannot
  mask an earlier in-flight one.

Rows are written automatically by `enqueue_cluster_operation`; operators
do not need to manage them. Pruning is performed by the cluster daemon
under `ClusterLock` election via
`_direct_delete_stale_cluster_operation_targets`: rows older than
`CLUSTER_OPERATION_TARGET_RETENTION` whose operation has reached a
terminal state are removed; in-flight operations are never pruned.

#### Per-Type Static Value Tables

Each concrete object type that is migrated gets its own table for static
values (immutable data set at creation time):

| Table | Object Type | Fields |
|-------|-------------|--------|
| `uploads` | Upload | uuid, node, created_at, version |
| `dnsmasq` | DnsMasq | uuid, namespace, owner_type, owner_uuid, provide_dhcp, provide_dns, version |
| `blobs` | Blob | uuid, modified, fetched_at, version |
| `nodes` | Node | uuid, fqdn (unique index), ip, version |
| `namespaces` | Namespace | name (VARCHAR PK), version |
| `artifacts` | Artifact | uuid, artifact_type, source_url, name, namespace, version |
| `network_interfaces` | NetworkInterface | uuid, network_uuid, instance_uuid, macaddr, ipv4, order, model, version |
| `ipams` | IPAM | uuid, namespace, network_uuid, ipblock, version |
| `networks` | Network | uuid, name, namespace, netblock, provide_dhcp, provide_nat, provide_dns, vxid (unique), egress_nic, mesh_nic, version |
| `agent_operations` | AgentOperation | uuid, namespace, instance_uuid (indexed), commands (JSON list), version |
| `instances` | Instance | uuid, cpus, disk_spec (JSON), memory, name, namespace (indexed), requested_placement (JSON), ssh_key, user_data, video (JSON), uefi, configdrive, nvram_template, secure_boot, machine_type, side_channels (JSON), version |

These tables use the object's UUID as the primary key, except for
`namespaces` which uses the namespace name (a string) as its primary key.

#### Per-Type Attribute Tables

Mutable attributes that are specific to an object type are stored in
dedicated attribute tables:

| Table | Object Type | Key Fields |
|-------|-------------|------------|
| `blob_attributes` | Blob | uuid, size, info, last_used, retention |
| `node_attributes` | Node | uuid, last_seen, installed_version, roles, daemons, versions, metrics. Per-daemon state lives in `node_daemon_states` since v19; the legacy `daemon_states` JSON column on this table is no longer read or written |
| `namespace_attributes` | Namespace | name, keys (JSON), trust (JSON) |
| `artifact_attributes` | Artifact | uuid, max_versions, shared, highest_index |
| `artifact_indexes` | Artifact | artifact_uuid + index_number (composite PK), blob_uuid |
| `network_interface_attributes` | NetworkInterface | uuid, floating_address |
| `network_attributes` | Network | uuid, floating_gateway, hosteddns (JSON dict) |
| `agent_operation_attributes` | AgentOperation | uuid, results (JSON dict) |
| `instance_attributes` | Instance | uuid, placement (JSON), power_state (JSON), ports (JSON), enforced_deletes (JSON), block_devices (JSON), agent_state (JSON), agent_attributes (JSON), agent_operations (JSON), kvm_pid, error_message, vsock_cids (JSON dict) |

Node attributes consolidate many separate etcd keys (observed, roles,
daemons, daemon:{name}, instances, versions, etc.) into a single row.

Namespace attributes consolidate keys (authentication) and trust
(namespace trust relationships) from separate etcd keys into a single row.

#### Node Identity and UUID Persistence

Each node in the cluster is assigned a real UUID (UUID version 4) when it
first registers with the cluster. Previously, nodes used their FQDN as a
fake UUID, but all nodes now have proper UUIDs stored in the `nodes`
MariaDB table with the FQDN as a separate uniquely-indexed column.

To avoid an FQDN-to-UUID database lookup on every daemon startup, the
node UUID is persisted locally to `{STORAGE_PATH}/node_uuid` (typically
`/srv/shakenfist/node_uuid`). On subsequent daemon starts, the UUID is
read from this local file for a direct database lookup by primary key.

The node UUID can also be set explicitly via the `SHAKENFIST_NODE_UUID`
environment variable or the `NODE_UUID` configuration field, which takes
precedence over the local file. This is useful for disaster recovery
scenarios where local storage has been lost but the node's UUID is known.

The lookup precedence order is:

1. `NODE_UUID` configuration field / `SHAKENFIST_NODE_UUID` environment
   variable
2. Local file at `{STORAGE_PATH}/node_uuid`
3. FQDN-based lookup in the `nodes` table (fallback)

If the persisted UUID does not match the current node's FQDN, it is
ignored and the FQDN-based fallback is used. This guards against stale
UUID files left over from a previous node installation.

Each attribute table follows the same pattern — typed scalar columns
for hot-path fields, JSON columns for complex structures, and one
indexed FK column per parent — for example:

```sql
CREATE TABLE node_attributes (
    uuid UUID PRIMARY KEY,
    last_seen DOUBLE,
    installed_version VARCHAR(64),
    -- Complex structures as JSON
    roles JSON,
    daemons JSON,
    metrics JSON
);
```

Cached lists of *child* object UUIDs are deliberately not stored on
the parent attribute table — querying the child table by an indexed
FK column is the source of truth. Phase 7 of the SQL-pushdown plan
removed the last two such caches (`network_attributes.networkinterfaces`
and `instance_attributes.interfaces`); see `PLAN-sql-pushdown-filtering-phase-07-denorm-lists.md`.

This approach:

- **Avoids wide generic tables**: Each type has exactly the columns it needs
- **Enables proper typing**: Native SQL types instead of JSON everywhere
- **Supports efficient indexes**: Can index frequently-queried columns
- **Keeps queries simple**: No joins needed for common operations

### Abstract Base Classes

Abstract base classes like `DatabaseBackedObject` and `ManagedExecutable` do
not get their own tables. Only concrete classes that are actually instantiated
have tables. For example:

- `ManagedExecutable` (abstract) - no table
- `DnsMasq` (concrete, inherits ManagedExecutable) - gets `dnsmasq` table

### Pydantic Models as Schema Source

Each table is defined by a Pydantic model that serves as the single source of
truth:

```python
from typing import Annotated
from pydantic import BaseModel, ConfigDict, UUID4
from shakenfist.schema.sqlalchemy import SQLIndex, SQLNativeUUID

class DnsMasqData(BaseModel):
    """Schema for DnsMasq static values in MariaDB."""
    model_config = ConfigDict(frozen=True)

    uuid: Annotated[UUID4, SQLNativeUUID()]
    namespace: Annotated[str, SQLIndex()]
    owner_type: Annotated[str, SQLIndex()]
    owner_uuid: Annotated[str, SQLIndex()]
    version: int
    provide_dhcp: bool
    provide_dns: bool
```

The table is then generated from this model:

```python
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table

table = pydantic_to_sqlalchemy_table(
    DnsMasqData, 'dnsmasq', metadata,
    primary_key_field='uuid', include_id_column=False
)
```

### Adding New Attributes

When adding a new attribute to an object type:

**For shared attributes (DatabaseBackedObject level):**

1. Consider if it belongs in an existing shared table (like `object_states`)
2. If it's a new shared concept, create a new shared table

**For type-specific attributes:**

1. Add the field to the Pydantic model
2. `ALTER TABLE` to add the column (with default if needed)
3. Bump the object's version number
4. Add an upgrade step (can be no-op if column has a DB default)

### Object Version Upgrades

Objects have version numbers that track schema changes. When an object is
read from the database with an older version:

1. **Lazy upgrade**: The `upgrade_pydantic_data()` method applies upgrade steps
2. **Persistence**: If the cluster minimum version equals current version, the
   upgraded data is written back to MariaDB
3. **Background migration**: A future background worker will upgrade objects
   that are never read

This allows rolling upgrades without requiring all objects to be migrated
immediately.

### Automatic Data Migrations

Data migrations from etcd to MariaDB run automatically when the database
daemon starts. The `ensure_data_migrations()` function checks each table's
version and runs any pending migrations. This includes migrations for object
states, IPAM reservations, uploads, blobs, nodes, and other object types.

No manual `sf-ctl` commands are needed for data migration -- simply
upgrading and restarting the database daemon is sufficient. Migrations are
idempotent and safe to re-run if the daemon restarts during migration.

If any objects fail to migrate, the table version is not bumped and the
migration will retry on the next daemon restart. This ensures all objects
are successfully migrated before the version advances.
