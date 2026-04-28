# ARCHITECTURE.md - Shaken Fist System Architecture

## Overview

Shaken Fist is a minimal cloud orchestration platform for VM and network
management, designed to be understood in its entirety by a single developer.

For detailed development guidance, see [CLAUDE.md](CLAUDE.md).

## System Components

### Daemons

Shaken Fist runs several daemons on each cluster node:

| Daemon | Purpose | Port |
|--------|---------|------|
| `sf-api` | REST API server (Flask/Gunicorn) | 13000 |
| `sf-database` | Database microservice (MariaDB access) | 13005 |
| `sf-eventlog` | Event logging service | 13009 |
| `sf-cleaner` | Resource cleanup | - |
| `sf-cluster` | Cluster maintenance | - |
| `sf-net` | Network daemon | - |
| `sf-queues` | Job queue processing | - |
| `sf-resources` | Resource tracking | - |
| `sf-transfers` | Blob transfers | - |
| `sf-privexec` | Privileged execution | - |

### Database Layer

```
+----------------+     +----------------+     +----------------+
|   sf-api       |     |   sf-cleaner   |     |   sf-queues    |
|   sf-net       |     |   sf-cluster   |     |   sf-resources |
+-------+--------+     +-------+--------+     +-------+--------+
        |                      |                      |
        +----------------------+----------------------+
                               |
                         gRPC (13005)
                               |
                    +----------+----------+
                    |    sf-database      |
                    +----------+----------+
                               |
                        +------+------+
                        |   MariaDB   |
                        | (state,     |
                        |  IPAM,      |
                        |  uploads,   |
                        |  blobs,     |
                        |  nodes,     |
                        |  namespaces,|
                        |  artifacts, |
                        |  networks,  |
                        |  instances, |
                        |  dnsmasq,   |
                        |  references,|
                        |  metadata,  |
                        |  cluster_   |
                        |  operations,|
                        |  work_queue,|
                        |  locks,     |
                        |  cluster    |
                        |  config,    |
                        |  event DLQ) |
                        +-------------+
```

The database microservice (`sf-database`) centralizes all database access:
- Only the database daemon has direct access to MariaDB
- All other daemons use the gRPC interface
- Provides Prometheus metrics for database operations

#### SQL Filter-Pushdown Discipline

Object iteration uses one indexed SQL query per call rather than the older pattern of materialising all rows
and filtering them in Python. Every `find_artifacts`, `find_instances`, `find_networks`, and
`find_network_interfaces` call in `shakenfist/mariadb.py` JOINs the per-type static-values table to
`object_states` and applies the caller's state, namespace, name and FK predicates directly in the WHERE
clause. The two FK fields (`network_uuid`, `instance_uuid`) on `ObjectFilterCriteria` are honoured only
by `find_network_interfaces`, which is what makes `Network.networkinterfaces` and `Instance.interfaces`
query-backed properties returning hydrated `NetworkInterface` objects rather than the cached UUID lists
they used to be (phase 7 of the SQL-pushdown plan).

The composite index `idx_object_states_type_state` on `(object_type, state_value)` covers the JOIN condition
that is present in every query. Per-type `name` and `namespace` single-column indexes on the artifact,
instance, and network tables cover the optional equality predicates. This keeps the common REST-layer calls
— list-by-namespace, list-active, lookup-by-name — to an index scan with no full-table read.

Filter criteria are expressed as `ObjectFilterCriteria` in
[`shakenfist/schema/object_filter.py`](shakenfist/schema/object_filter.py). The iterator base class
(`DatabaseBackedObjectIterator` in `baseobject.py`) builds criteria from its constructor arguments and
delegates to the appropriate `find_*` primitive, so callers such as `Artifacts(namespace=ns,
prefilter='active')` get SQL pushdown without any extra work at the call site. Filters that have no SQL
equivalent (e.g. predicates over lazily-loaded attribute columns) remain as Python callables passed through
the `filters=` argument and execute after the indexed scan.

See [`docs/operator_guide/database.md`](docs/operator_guide/database.md) — "SQL Filter Pushdown" — for
per-API guidance and a code example.

#### gRPC Reliability

All gRPC calls use `timeout=30` seconds and `wait_for_ready=True` to handle
transient service unavailability during startup or momentary congestion. The
`_grpc_call()` helper in `mariadb.py` enforces this for all database service
calls and retries up to 3 times on UNAVAILABLE/DEADLINE_EXCEEDED errors with
channel reset between attempts. The database gRPC channel uses HTTP/2
keepalive (ping every 10s, 5s timeout) to detect stale connections before
they cause failures. The database gRPC server uses a 20-thread pool to
handle concurrent requests from all daemons. The database client in
`database.py` uses the `_retry_database` decorator for exponential backoff
retries on transient failures. All gRPC failures are logged at ERROR level.

`get_objects_by_state()` returns `None` on error (distinct from `[]` for no
matches). All object iterators handle this by falling back to unfiltered
scans, ensuring that transient gRPC failures do not silently drop objects
from iteration results (e.g. interfaces during instance deletion).

Object iterators read from MariaDB via `get_all_*()` functions. All
object static values now live in MariaDB.

#### Cluster Operation Tracking

`set_last_cluster_operation()` records which cluster operation was most recently
enqueued for an object. API clients poll this to wait for operations to complete.
The write now raises `RuntimeError` on failure so API endpoints return 500
instead of silently losing the tracking data. Callers in deletion paths and
daemons catch this exception to ensure cleanup always proceeds.

The history is stored in the dedicated `cluster_operation_targets` MariaDB
table -- one append-only row per (operation, target object) pair, with an
AUTO_INCREMENT `sequence_number` providing total ordering per target.
`last_cluster_operation` reads the highest-sequence row for the target.
Because the table is append-only it is bounded by a periodic prune in the
cluster daemon, alongside the existing `delete_stale_transfers` cleanup.
The prune removes rows older than `CLUSTER_OPERATION_TARGET_RETENTION`
seconds whose operation has already reached a terminal state. In-flight
operations (`queued`/`preflight`/`executing`) are never pruned regardless
of age. Because the cluster daemon already runs cluster-wide cleanup
under `ClusterLock` election, no additional locking or master-node
gating is required.

#### Cluster Operation Storage and Work Queues

Cluster operation headers and the per-node work queues both live in
MariaDB. The `cluster_operations` table stores the full operation
metadata as JSON in `metadata_json`, with `node_uuid`,
`instance_uuid`, `network_uuid` and `priority` extracted into indexed
columns for dispatch-time filtering. The `work_queue` table stores
one row per queued job with claim fields (`claimed_at`, `claimed_by`,
`attempts`) on the same row -- MariaDB row locking replaces the
old etcd two-prefix (`/sf/queue/`, `/sf/processing/`) design. Dequeue
uses `SELECT ... FOR UPDATE SKIP LOCKED` so concurrent workers
either claim distinct rows or one gets nothing.

Creating a cluster operation is atomic: the `CreateAndEnqueueCluster`
gRPC RPC writes the `cluster_operations` row, the `object_states`
row, and the `work_queue` row in a single MariaDB transaction.
Audit events are published out-of-band through the normal eventlog
gRPC service path, which falls back to the MariaDB ``event_dlq``
table for failure recovery.

The cluster daemon runs
`reap_stuck_cluster_operation_jobs()` from
`daemons/cluster/scheduled_tasks.py` on a one-minute schedule.
For every row whose `claimed_at` is older than
`CLUSTER_OP_STUCK_THRESHOLD` seconds, the reaper either clears the
claim so a fresh worker picks the job up or -- if `attempts`
has reached `CLUSTER_OP_MAX_ATTEMPTS` -- deletes the row and
transitions the underlying cluster operation to `STATE_ERROR`.
Reaper activity is exported on
`cluster_op_reaper_requeued_total` and
`cluster_op_reaper_rejected_total`, scraped from
`CLUSTER_METRICS_PORT` on the cluster daemon.

### Protocol Buffers and gRPC

The gRPC interface is defined in `protos/*.proto` files. Generated Python code
and type stubs are stored in `shakenfist/protos/`.

To regenerate after modifying `.proto` files or Python enum definitions:

```bash
tox -e genprotos
```

This tox environment ensures the correct versions of `grpcio-tools` and
`mypy-protobuf` are used, matching the versions in `pyproject.toml`.

#### Enum Generation

Protobuf enums are auto-generated from Python enum definitions to avoid
duplication. The Python enums in `shakenfist/schema/` are the source of truth:

- `schema/object_types.py` defines `ObjectType` with both string values and
  stable protobuf integer IDs
- `schema/ipam_reservation.py` defines `ReservationType` similarly
- `schema/relationship_types.py` defines `RelationshipType` for object
  references

Each enum member uses a `NamedTuple` value type containing:
- `string`: The string value used in databases and APIs
- `proto_id`: The stable integer ID used in protobuf messages (never reordered)

The `protos/_generate_enums.py` script uses AST parsing to extract these values
and generates `shakenfist_enums.proto`. This is run automatically by
`_make_stubs.sh` before compiling the proto files.

To add a new enum value:
1. Add the member to the Python enum with the next available `proto_id`
2. Run `tox -e genprotos` to regenerate the protobuf definitions
3. Never change or reuse existing `proto_id` values

### Networking

Shaken Fist uses VXLAN mesh networking:

```
+------------------+          +------------------+
|     Node 1       |          |     Node 2       |
|  +------------+  |  VXLAN   |  +------------+  |
|  |   VM A     |  |<-------->|  |   VM B     |  |
|  +-----+------+  |  mesh    |  +-----+------+  |
|        |         |          |        |         |
|  +-----+------+  |          |  +-----+------+  |
|  | veth/tap   |  |          |  | veth/tap   |  |
|  +-----+------+  |          |  +-----+------+  |
|        |         |          |        |         |
|  +-----+------+  |          |  +-----+------+  |
|  | br-vxlan   |  |          |  | br-vxlan   |  |
|  +------------+  |          |  +------------+  |
+------------------+          +------------------+
```

### Storage

Content-addressable blob storage with replication:

- Blobs are stored by SHA512 hash
- Automatic deduplication
- Configurable replication factor
- Used for disk images, snapshots, etc.

### Object References

The `object_references` table in MariaDB tracks relationships between objects.
This is used primarily for blob reference counting but is generic enough to
track any object-to-object relationship.

| Column | Type | Description |
|--------|------|-------------|
| source_object_type | ObjectType | Type of the referencing object |
| source_uuid | UUID | UUID of the referencing object |
| relationship | RelationshipType | Type of relationship |
| relationship_value | VARCHAR(64) | Optional relationship-specific value |
| target_object_type | ObjectType | Type of the referenced object |
| target_uuid | UUID | UUID of the referenced object |
| created | FLOAT | When the reference was created |
| last_active | FLOAT | Last time the reference was verified |

#### Relationship Types

| Type | Source | Target | Value |
|------|--------|--------|-------|
| `disk` | Instance | Blob | Disk index ("0", "1", ...) |
| `nvram_template` | Instance | Blob | NULL |
| `artifact_index` | Artifact | Blob | Index number ("000000000001") |
| `depends_on` | Blob | Blob | NULL |
| `transcode` | Blob | Blob | Style ("qcow2", "raw") |
| `agent_output` | AgentOperation | Blob | Output type ("stdout", "stderr") |
| `blob_location` | Node | Blob | NULL (blob is present on this node) |

This replaces the legacy `ref_count` and `locations` blob attributes with a
queryable, auditable reference system. Blob reference counts are computed
dynamically from this table via `mariadb.count_references_to()`. Blob locations
are queried via `mariadb.get_references_to()` filtered by `BLOB_LOCATION`.

The `last_active` column is updated whenever a reference is observed to still
be valid (e.g., when a node's cleaner daemon calls `observe()` on local blobs).
This enables detection of stale references for cleanup.

## State Machines

Objects follow defined state machines. Key states:

### Instance States
- `initial` -> `preflight` -> `creating` -> `created`
- `created` -> `deleted` (soft delete)
- `created` -> `error` (on failure)

### Network States
- `initial` -> `created`
- `created` -> `deleted`

See `docs/developer_guide/state_machine.md` for complete documentation.

## Configuration

Configuration uses Pydantic with a two-stage bootstrap:

1. **Stage 1**: Environment/file configuration (for the initial MariaDB
   connection or database service gRPC address)
2. **Stage 2**: Cluster configuration stored in MariaDB (loaded after the
   database service is reachable)

Key configuration sources:
- `/etc/sf/config` - Local configuration file
- MariaDB `cluster_config` table - Cluster-wide configuration
- Environment variables (highest priority)

### Node Identity

Each node has a real UUID (not FQDN-based) stored in MariaDB. The UUID is
persisted locally to `{STORAGE_PATH}/node_uuid` on first run so that
subsequent daemon starts can look up the node directly by UUID rather than
performing an FQDN-to-UUID indirection. The UUID can also be set explicitly
via the `NODE_UUID` config field or `SHAKENFIST_NODE_UUID` environment
variable.

Node UUIDs are used throughout the system:
- **Metrics**: Stored in MariaDB `node_metrics` table (keyed by `node_uuid`), stale after 120s
- **Scheduler**: Returns node UUIDs as placement candidates
- **Instance placement**: `placement['node']` stores the node UUID
- **Operation queues**: Queue paths use node UUIDs
- **Operation schemas**: `node_uuid` field typed as `UUID4`

Note: `BLOB_LOCATION` references in `object_references` still use FQDNs
as the source identifier (separate from node UUID usage).

## API Architecture

REST API built with Flask-RESTful:

```
Client
   |
   v
Apache (reverse proxy, adds /api/ prefix)
   |
   v
Gunicorn (port 13000)
   |
   v
Flask app (external_api/app.py)
   |
   +-> /auth/* - Authentication endpoints
   +-> /instances/* - Instance management
   +-> /networks/* - Network management
   +-> /artifacts/* - Image management
   +-> /blobs/* - Blob storage
   +-> /nodes/* - Cluster management
```

## CI/CD

### GitHub Actions

- **functional-tests.yml**: Main CI workflow with merge queue support.
  Includes automated reviewer (via shared action), delinter, and
  exception fixer jobs.
- **export-repo-config.yml**: Exports GitHub settings for version
  control (uses shared reusable workflow from `actions/` repo).
- **pr-re-review.yml**: Bot-triggered PR re-review.
- **pr-address-comments.yml**: Bot-triggered comment addressing.
- **pr-fix-tests.yml** / **test-drift-fix.yml**: Bot-triggered unit
  test fixing.

### CI Caching

Workflows that download packages use environment variables to route
traffic through local caches:

- **HTTP proxy**: `http_proxy`/`https_proxy` set to
  `http://192.168.1.15:3128` (Squid cache) for apt, curl, and
  general HTTP downloads.
- **PyPI mirror**: `PIP_INDEX_URL` set to
  `https://devpi.home.stillhq.com/root/pypi/+simple/` (devpi) for
  pip and uv package installs.

CI VMs provisioned by the `shakenfist/actions` Ansible playbooks also
get system-level config files (`/etc/apt/apt.conf.d/01proxy` and
`/etc/pip.conf`) so that getsf and other tools use the caches.
- **Proxy bypass**: `no_proxy`/`NO_PROXY` set to
  `localhost,127.0.0.1,10.0.0.0/8` to prevent local service traffic from
  being routed through the proxy.

### Branch Protection

The develop branch uses:
- Required status checks: `Can see status`, `Can enqueue`
- Merge queue with ALLGREEN grouping strategy
- Configuration exported to `.github/exported-config/`

## Security Model

- Multi-tenant with namespace isolation
- JWT-based authentication
- RBAC with admin/user roles
- Network isolation via VXLAN
