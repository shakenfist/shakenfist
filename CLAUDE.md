# CLAUDE.md - Shaken Fist Development Guide

## Project Overview

Shaken Fist is an opinionated, minimal cloud orchestration platform for VM and
network management. It prioritizes simplicity and maintainability over feature
completeness, designed to be understood in its entirety by a single developer.

- **Homepage**: https://shakenfist.com/
- **Repository**: https://github.com/shakenfist/shakenfist

## Development goals

There is some discussion of overall project goals in `GOALS.md`, and roadmap
items are documented in `docs/plans/*.md`. However, we should always:

* Ensure objects are correctly cleaned up in `hard_delete()` when appropriate.
* Seek to improve mypy coverage, especially for code we've added.
* Ensure there is unit test coverage, and preferably functional test coverage
  as well (see `shakenfist/deploy/shakenfist_ci` for functional coverage). We
  prefer functional tests to unit tests if we can only have one.
* Preserve event logging -- the exact logging can change if required, but we
  like having good coverage of events for audit and debugging.
* Object or attribute filtering should be "pushed down" to the mariadb SQL
  layer where indexes can improve its performance where possible. Also ensure
  that we have the appropriate indicies for query efficiency when adding
  queries.
* Perform a code review a the end of a plan and address any concerns raised.

## Quick Reference

### Running Tests

```bash
tox                              # Run all tests
stestr run {test_name}           # Run specific test
tox -eflake8 -- -HEAD            # Style check on changed files only
tox -ecover                      # Generate coverage report
```

### Pre-commit Hooks

```bash
pip install pre-commit           # Install pre-commit
pre-commit install               # Set up git hooks
pre-commit run --all-files       # Run all hooks manually
```

The repository uses `actionlint` to validate GitHub Actions workflow files.
Install `actionlint`:
- macOS: `brew install actionlint`
- Linux: Download from https://github.com/rhysd/actionlint/releases

### Key Entry Points (CLI Tools)

- `sf-ctl` - Control CLI
- `sf-api` - REST API server
- `sf-database` - Database microservice daemon (runs on database-tier nodes)
- `sf-cleaner`, `sf-cluster`, `sf-net`, `sf-queues`, `sf-resources` - Daemons

### Generating gRPC Stubs from Proto Files

Proto files are in `protos/` and generated Python stubs go to `shakenfist/protos/`.
To regenerate after modifying `.proto` files or Python enum definitions:

```bash
tox -e genprotos
```

This tox environment:
1. Uses the exact library versions from `pyproject.toml` (avoiding version mismatch
   errors at runtime)
2. Generates protobuf enum definitions from Python source files (using AST parsing
   of `schema/object_types.py` and `schema/ipam_reservation.py`)
3. Compiles all `.proto` files to Python code and type stubs
4. Fixes import statements in generated code (e.g., `import foo_pb2` becomes
   `from shakenfist.protos import foo_pb2`)

**IMPORTANT:** Always use `tox -e genprotos` - never run `grpc_tools.protoc`
directly. The tox environment ensures correct versions and the underlying script
performs essential post-processing.

**Note:** The underlying script works on both macOS and Linux.

## Code Style and Conventions

### Python Style

- Use **single quotes** for strings, **double quotes** for docstrings
- Wrap lines at **120 characters**
- Trim trailing whitespace
- Follow existing import ordering (stdlib, third-party, shakenfist)

### Import Organization

**All imports must be at the top of the file.** Late imports inside functions
or methods should only be used when absolutely necessary to avoid circular
import issues, and must include a comment explaining why. If you see a late
import without such a comment, move it to the top of the file.

```python
# 1. Standard library
import base64
import json
import os

# 2. Third-party
import jinja2
from shakenfist_utilities import logs  # noreorder

# 3. Shaken Fist internal
from shakenfist import artifact
from shakenfist import baseobject
from shakenfist.config import config
```

### Abbreviated Imports (Common Pattern)

```python
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectWithOperations as dbowo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
```

### Logging Pattern

Every module should set up logging like this:

```python
from shakenfist_utilities import logs

LOG, _ = logs.setup(__name__)

# Usage with structured fields:
log = LOG.with_fields({'instance_uuid': uuid})
log.info('Instance created')
```

### Copyright Header

All files should start with:

```python
# Copyright 2019 Michael Still and contributors
```

## Architecture Overview

### Directory Structure

```
shakenfist/
├── shakenfist/               # Core package
│   ├── instance.py          # VM instance management (~2000 lines)
│   ├── network/             # Virtual networking (VXLAN)
│   │   ├── network.py       # Network implementation
│   │   └── interface.py     # Network interfaces
│   ├── artifact.py          # Disk images and artifacts
│   ├── blob.py              # Content-addressable blob storage
│   ├── baseobject.py        # Base framework for all persistable objects
│   ├── locks.py             # Distributed cluster locks (MariaDB-backed)
│   ├── eventlog.py          # Event logging (gRPC)
│   ├── cache.py             # In-memory caching layer
│   ├── config.py            # Pydantic-based configuration
│   ├── constants.py         # Constants and object type mappings
│   ├── exceptions.py        # Custom exception hierarchy
│   ├── scheduler.py         # Instance placement
│   ├── ipam.py              # IP address management
│   ├── namespace.py         # Multi-tenancy
│   ├── node.py              # Cluster node management
│   ├── daemons/             # Background daemons
│   │   ├── daemon.py        # Daemon base utilities
│   │   ├── api/             # External API server (Flask)
│   │   ├── cleaner/         # Resource cleanup
│   │   ├── cluster/         # Cluster maintenance
│   │   ├── database/        # Database microservice (MariaDB gRPC wrapper)
│   │   ├── network/         # Network daemon
│   │   ├── queues/          # Job queue processing
│   │   ├── resources/       # Resource tracking
│   │   ├── transfers/       # Blob transfer
│   │   └── privexec/        # Privileged execution
│   ├── external_api/        # REST API endpoints
│   │   ├── app.py           # Flask app setup
│   │   ├── base.py          # API utilities and decorators
│   │   └── [resource].py    # Resource-specific endpoints
│   ├── operations/          # Task operation definitions
│   │   ├── baseoperation.py # Base operation class
│   │   └── *_op.py          # Specific operations
│   ├── schema/              # Pydantic models
│   ├── util/                # Utility modules
│   ├── client/              # CLI tools
│   ├── tests/               # Test suite
│   └── deploy/              # Ansible collection (collection/) and
│                            # functional CI suite (shakenfist_ci/)
└── docs/                    # MkDocs documentation
```

### Core Components

| Component | Purpose |
|-----------|---------|
| `instance.py` | VM lifecycle management (create, delete, power, network, disks) |
| `network/` | VXLAN mesh networking, DHCP, NAT, floating IPs |
| `artifact.py` | Versioned disk images with labeling |
| `blob.py` | Content-addressable binary storage with replication |
| `baseobject.py` | State machine, versioning, DB sync for all objects |
| `locks.py` | Distributed cluster locks (MariaDB-backed) |
| `mariadb.py` | Three-layer (direct/gRPC/public) database client and sf-database RPC access |
| `config.py` | 100+ Pydantic settings with env overrides |

### Storage: MariaDB and the Database Service

Shaken Fist uses MariaDB as its sole datastore. Object state, queues,
locks, and cluster config all live in MariaDB.

The database microservice (`sf-database`) provides a gRPC interface for all
database operations. One or more `sf-database` instances can run as a tier;
clients reach them through the gateway-hosts list.

```python
# MariaDB state access (automatically routed through database service)
from shakenfist import mariadb
mariadb.get_state('instance', 'uuid-here')
mariadb.set_state('instance', 'uuid-here', state)
mariadb.get_objects_by_state('instance', ['created', 'error'])
```

This abstraction layer:
- Centralizes all database access to a single service
- Only the database daemon has direct access to MariaDB
- Provides prometheus metrics for all database operations
- Enables clean separation of concerns
- Raises `shakenfist.exceptions.DatabaseUnavailable` once gRPC retries are
  exhausted against an unreachable database service, so "not found" return
  values (`None`/`False`/`[]`) always mean the object genuinely is not
  there. Code that intentionally tolerates an unreachable database must
  catch `DatabaseUnavailable` explicitly (see `Daemon.check_daemon_state()`
  and `ClusterLock.__enter__` for examples)

Configuration options:
- `MARIADB_GATEWAY_HOSTS` - List of `sf-database` gRPC endpoints that clients
  connect to (e.g. `['10.0.0.1']`). A single-instance deployment sets this to a
  one-element list. Phase 3 of PLAN-byo-mariadb will add client-side load
  balancing across multiple entries.
- `MARIADB_GATEWAY_PORT` - gRPC API port on each gateway host (default: 13005)
- `MARIADB_GATEWAY_METRICS_PORT` - Prometheus metrics port on each gateway host
  (default: 13006)
- `MARIADB_HOST` - Set only on nodes that run `sf-database` or where
  `sf-ctl ensure-mariadb-schema` is executed; enables direct MariaDB access.
  Do **not** set this on ordinary cluster nodes.

**Database daemon special case**: Direct MariaDB access requires *both*
`MARIADB_HOST` and a caller identity in `mariadb.DIRECT_MARIADB_CALLERS`
(`database`, `ctl`). `MARIADB_HOST` alone is not enough, and it is not a
per-node switch: it is rendered into `/etc/sf/config`, the shared systemd
`EnvironmentFile`, so on a database-tier node every daemon can see it. Only
`sf-database` (which would otherwise call itself) and `sf-ctl` (which runs
`ensure-mariadb-schema` and `initialise-node` before `sf-database` starts)
may act on it. Every other daemon uses the gRPC tier even when co-located
with MariaDB -- going direct hides its load from the tier's metrics and
connection accounting.

Because that decision reads a process-global identity, an entry point must
call `set_caller_identity()` before anything which might dispatch. An unset
identity reads as `unknown`, which correctly routes to the tier for ordinary
daemons but would make `sf-database` call itself; it therefore claims its
identity as the first statement of `main()`, before `write_pid_file()` starts
the eventlog drainer. The one path exempt from all of this is
`config.load_cluster_config()`, which runs at import time before any identity
exists -- see its docstring.

### Systemd Service Ordering

Shaken Fist daemons are managed via systemd with careful ordering defined in
`shakenfist/deploy/collection/roles/node/templates/sf.service`. The startup
order is:

1. `database` - Starts first (after multi-user.target), provides gRPC access
   to MariaDB for all other daemons
2. `sentinel-first` - Starts after database, marks node as starting
3. `privexec`, `nodelock` - Start after sentinel-first
4. All other daemons - Start after privexec, nodelock, and database
5. `sentinel-last` - Starts after all other daemons, signals shutdown state

**Database Access Pattern**:
- The `sf-database` service is the **only** daemon with direct access to
  MariaDB (via `MARIADB_HOST` being set)
- All other daemons access the database through the database service's gRPC
  interface
- The database daemon records its own startup/shutdown state via direct
  MariaDB access to avoid a chicken-and-egg problem

This ordering is critical because daemons like `sf-api` will hang on startup
if they try to connect to the database microservice before it's running.

### Locking

Use `ClusterLock` for distributed operations:

```python
from shakenfist.locks import ClusterLock

with ClusterLock('lock_name', timeout=30):
    # Critical section
```

Locks are leased: every `cluster_locks` row has a server-side
`expires_at` and the holder's `acquire()` starts a daemon thread
that refreshes the lease every 20s. If the holder dies, the row
expires after 60s and another candidate may steal it. There is no
manual cleanup or stale-lock reaper required.

For long-held locks (anything held for more than a few seconds), the
holder must poll `lock.lost_event` between iterations of its critical
section and abort cleanly when it fires -- the refresher sets it on
confirmed loss. The cluster maintainer's inner loop is the canonical
example: it sleeps via `lock.lost_event.wait(ELECTED_LOOP_POLL_SECONDS)`
so it wakes
immediately on confirmed loss (and keeps the elected loop inside the
systemd `TimeoutStopSec` and watchdog windows).

`ClusterLock.release()` raises `shakenfist.exceptions.LockNotHeld` if
the database has no record of the caller holding the lock. The
context-manager `__exit__` swallows that exception (a body exception
is more important) but the noisy log emitted from inside `release()`
is preserved so CI checks still catch it.

### API Pattern

Flask-based REST API with decorators:

```python
from shakenfist.external_api import base as sf_api

class InstanceEndpoint(Resource):
    @caller_is_admin
    @swag_from(api_base.swagger_helper(...))
    @use_kwargs(schema)
    def get(self, instance_ref):
        # Handler
        pass
```

**IMPORTANT**: Decorator order matters! See comments in `external_api/app.py`.

#### Parameter declarations are enforced

`swagger_helper()` validates every declaration when the module is
imported, so a malformed one raises `InvalidAPIDeclaration` and **sf-api
does not start**. The rules, in full in
`docs/developer_guide/writing_an_endpoint.md`:

1. Every handler carries a `swag_from(swagger_helper(...))`, even one
   that takes no parameters. An empty parameter list is a valid
   declaration; no declaration at all means the endpoint is absent from
   the published API.
2. `location` is one of `api_base.SWAGGER_PARAMETER_LOCATIONS`, and must
   be where the parameter actually arrives: a route segment is `path`, a
   `@use_kwargs(..., location='query')` schema key or a
   `flask.request.args` read is `query`, everything else is `body`
   (`log_request` merges the JSON body into kwargs).
3. A `path` parameter must be `required=True`.
4. A raw request body is declared as `api_base.RAW_BODY_PARAMETER`, and
   cannot be combined with named body parameters.
5. Every kwarg the handler accepts is declared; decorator-injected
   `*_from_db` objects are not parameters.
6. The type is a token from `api_base.ARGTYPES`. Objects and arrays of
   objects can only be declared in the body, since outside one there is
   no schema object to nest a structure in.
7. A declaration is five elements plus an optional sixth constraints
   dict with keys from `minimum`/`maximum`/`pattern`, also validated at
   import time.

Declare the token that matches what the handler actually accepts —
nothing derives a type, so the check is `test_openapi_spec.py`'s
`STRUCTURED_PARAMETERS` table, where every structured or bounded
parameter is registered with the shape its handler really accepts. The
table's *completeness* is derived from the published specification, so
a new structure or bound fails CI until it has an entry; what the entry
says still has to be written by hand against the handler. Publish what
the server backs: a tighter bound belongs in the specification only
where the server already coerces or rejects outside it.

`python3 tools/fix-api-parameter-locations.py --apply` corrects
location drift mechanically. The check runs as a pre-commit hook and,
because no workflow runs pre-commit, as
`test_parameter_declarations.py` in CI.

### REST API URL Structure

The REST API is documented via OpenAPI at https://openapi.shakenfist.com. Key
points about URL structure:

- **When talking directly to gunicorn** (port 13000): The base URL is `/`
  - Example: `http://localhost:13000/auth/namespaces`
  - Example: `http://localhost:13000/instances`

- **When talking through the operator's reverse proxy / load balancer**
  (standard external access): The `/api/` prefix is added by that proxy
  - Example: `http://host/api/auth/namespaces`

- **There is no version prefix** (no `/v1/` or `/2/`) in the API paths

All endpoints require JWT authorization via Bearer token header (except
`/auth` POST for obtaining tokens). A request to a protected endpoint
without authentication returns HTTP 401 Unauthorized.

### Operation Queue System

Operations use a state machine:
- `queued` -> `preflight` -> `executing` -> `complete`/`abort`

Queue priorities (per-node and global):
- `user-waiting` - Low latency, user interactivity
- `user-facing` - Standard user operations
- `background` - Non-urgent operations
- `-high-io` variants for I/O intensive work

## Key Design Decisions

1. **KVM via libvirt only** - No other hypervisors
2. **VXLAN only** - Networking uses VXLAN meshes exclusively
3. **Single machine clusters possible** - Scales from 1 to many nodes
4. **Ubuntu/Debian only** - Limited OS support for maintainability
5. **Multi-tenant by default** - Namespace-based isolation
6. **Content-addressable blobs** - Deduplication and replication

## Testing

- Framework: `stestr` with `testtools.TestCase`
- Base class: `ShakenFistTestCase`
- Heavy use of `mock.patch` for MariaDB and external dependencies
- Tests mirror module structure in `shakenfist/tests/`

## Key Dependencies

- **grpcio/protobuf** - gRPC communication
- **Flask/Flask-RESTful/Flasgger** - REST API
- **Flask-JWT-Extended** - Authentication
- **Pydantic** - Configuration and schemas
- **shakenfist-utilities** - Shared utilities library

## Common Pitfalls

1. **Decorator order in API endpoints** - Read comments in `external_api/app.py`
2. **Lock timeouts** - Always specify reasonable timeouts for ClusterLock
3. **Attribute updates must pass a field mask** - The
   `update_*_attributes` functions in `mariadb.py` require a `fields`
   argument naming exactly the model fields the caller changed; only
   those columns are written. Never pass `fields=None` outside row
   creation or pydantic-upgrade persistence: an unmasked write pushes a
   stale snapshot of every other column over concurrent writers'
   committed changes (a cross-attribute lost update — the cause of both
   the vanished-agent-operation flake and the scheduler-affinity CI
   flake). Never store relational data (like "which instances are on
   this node") as a JSON list on an attributes row; use a table with
   per-row inserts and deletes such as `object_references`.
4. **State machine transitions** - Follow documented state machines in
   `docs/developer_guide/state_machine.md`
5. **In-memory only objects must never touch the database** - Objects
   constructed with `in_memory_only=True` (the IPAM built when hydrating
   a deleted network, blob-reference image artifacts) keep state,
   attributes and events in process memory. A database row written for
   one is orphaned forever: `hard_delete()` early-returns for in-memory
   objects and state-driven iterators skip objects with no static row.
   This actually happened (issue 3532: 3,577 leaked ipam state rows and
   ~22k artifact rows on sfcbr, a constant ~56 QPS of uncacheable
   GetIPAM). If you add a new persistence path to `baseobject.py` or an
   object class with its own MariaDB access, guard it on
   `self.in_memory_only`.
6. **Two uuid formats in MariaDB** - `object_states.object_uuid` (and
   other `sa.String(36)` columns) store the dashed uuid form;
   `sa.Uuid` columns in the static tables store undashed CHAR(32). SQL
   joining the two must transform one side (see the orphan
   reconciliation queries in `mariadb.py` for the index-friendly
   pattern); ad-hoc queries that compare them directly silently never
   match. `object_states.object_type` sits on the same join and has the
   same shape of trap: it is `sa.Enum(ObjectType)`, stored by enum
   *name*, so bind an `ObjectType` rather than comparing it to a
   `operation_type` column holding the enum *value*. A column-to-column
   comparison never reaches the type's bind processor -- see
   `docs/developer_guide/coding_rules.md` for why binding a plain string
   works and comparing two columns does not.

## MariaDB Storage

Several object types have their data stored in MariaDB for improved query
performance. This is required for all deployments - MariaDB must be configured.

### Data Stored in MariaDB

- **Object state** (`object_states` table): State values like "created", "deleted",
  "error" for all object types
- **IPAM reservations** (`ipam_reservations` table): IP address allocations
- **Uploads** (`uploads` table): Upload object static values (uuid, node,
  created_at, version)
- **Namespaces** (`namespaces`, `namespace_attributes` tables): Namespace
  static values and mutable attributes (keys, trust). Uses namespace name
  (string) as primary key, not UUID.
- **Artifacts** (`artifacts`, `artifact_attributes`, `artifact_indexes` tables):
  Artifact static values (uuid, type, source_url, name, namespace), mutable
  attributes (max_versions, shared, highest_index), and version indexes.
- **Network Interfaces** (`network_interfaces`, `network_interface_attributes`
  tables): Network interface static values (uuid, network_uuid, instance_uuid,
  macaddr, ipv4, order, model) and mutable attributes (floating_address).
- **IPAMs** (`ipams` table): IPAM static values (uuid, namespace,
  network_uuid, ipblock). No mutable attributes.
- **Networks** (`networks`, `network_attributes` tables): Network static
  values (uuid, name, namespace, netblock, provide_dhcp/nat/dns, vxid,
  egress_nic, mesh_nic) and mutable attributes (floating_gateway,
  hosteddns). VXLAN ID uniqueness enforced by UNIQUE constraint on
  vxid column.
- **AgentOperations** (`agent_operations`, `agent_operation_attributes`
  tables): AgentOperation static values (uuid, namespace, instance_uuid,
  commands) and mutable attributes (results). Commands and results stored
  as JSON.
- **Instances** (`instances`, `instance_attributes` tables): Instance
  static values (uuid, cpus, disk_spec, memory, name, namespace,
  requested_placement, ssh_key, user_data, video, uefi, configdrive,
  nvram_template, secure_boot, machine_type, side_channels) and mutable
  attributes (placement, power_state, ports, enforced_deletes,
  block_devices, interfaces, agent_state, agent_attributes,
  agent_operations, kvm_pid, error_message, vsock_cids). Complex fields stored as
  JSON.
- **Object Metadata** (`object_metadata` table): User-defined metadata
  key-value pairs for all object types.
  Uses composite primary key (object_type, object_uuid) following the
  same pattern as object_states.
- **Cluster Operation Targets** (`cluster_operation_targets` table):
  Records every cluster operation targeting an object (Instance,
  Artifact, Network, Blob) with AUTO_INCREMENT sequence numbering
  for ordering. Replaced the single-pointer `last_cluster_operation`
  in `object_metadata` with a full append-only history. Primary key
  is `sequence_number` (AUTO_INCREMENT), with a UNIQUE constraint on
  `operation_uuid`. Indexed on `(target_object_type, target_uuid)`
  and `created_at`. Target rows are written automatically by
  `enqueue_cluster_operation`; callers have no per-target bookkeeping
  obligation. `has_pending_cluster_operation()` exposes the
  history-aware "any in-flight op?" query used by gating logic.
- **Node Metrics** (`node_metrics` table): Ephemeral per-node resource
  metrics (CPU, memory, disk, network, queue depths) updated every 60
  seconds by the resources daemon. Uses a JSON column (`metrics_json`)
  for the schemaless metrics payload (~50+ fields); `metrics_json`
  remains authoritative for readers. Capacity-relevant fields (CPU,
  memory, disk counts, disk-busy rate) are additionally projected into
  typed nullable columns at upsert time (see
  `NODE_METRICS_EXTRACTION_SPEC` in `shakenfist/mariadb.py`) so SQL-side
  capacity arithmetic doesn't need to unpack JSON. `is_hypervisor` is
  projected the same way: sf-resources publishes metrics from every node
  whatever its roles, so anything doing capacity arithmetic in SQL has
  to filter on the role the way `scheduler.py` does. One row per node,
  upserted each update cycle. Primary key is `node_uuid`.
- **Scheduler capacity** (`scheduler_node_capacity`, `namespace_claims`,
  `cluster_capacity` tables): Materialised capacity counters for
  scheduler reservations. `scheduler_node_capacity` has one row per
  hypervisor (limit and used counters for cpus/memory_mb/disk_gb plus a
  decaying `expected_demand`); `namespace_claims` has one row per
  capacity claim (limits, usage, coverage state, server-side
  `expires_at`) and is also the `NamespaceClaim` object's
  static-values table; `cluster_capacity` is a singleton (id always 1)
  of total/claimed/unclaimed-used sums. Recomputed wholesale from
  ground truth by the reconciler (a single `ReconcileSchedulerCapacity`
  RPC run every 5 minutes on the elected cluster node); as of
  scheduler-reservations phase 3 also drawn down and released
  incrementally on every placement by the
  `AdmitInstancePlacement`/`ReleaseInstancePlacement` RPCs, each of
  which performs a guarded `UPDATE` against these counters in the same
  transaction as the placement write (see the Instance placement
  bullet below and `docs/plans/PLAN-scheduler-reservations.md`). The
  reconciler's job since phase 3 is drift correction rather than the
  sole write path. The `used_*` counters are allocation ledgers over
  placed, non-deleted instances, so they deliberately differ from the
  resources daemon's active-domain measurements whenever instances are
  powered off. The issue-3498 Python stopgap in the scheduler was
  deleted by the same change that wired admission onto these counters.
  Phase 4 added the five `*NamespaceClaim` RPCs behind admin-only REST
  at `/auth/namespaces/<namespace>/claims`. Creating or growing a claim
  is itself a guarded admission against the cluster singleton
  (`claimed + limit + GREATEST(0, unclaimed_used - migrated) <= total`,
  per dimension); creation migrates the namespace's existing drawdown
  onto the claim and deletion migrates it back, so the same capacity is
  never counted on both sides. Claim ceilings are **advisory** this
  release: an instance placement that exceeds its namespace's claim is
  admitted and reported through the reply's `claim_over_limit` /
  `claim_dimensions` fields as a `placement admitted over namespace
  capacity claim` audit event, never refused. `CLAIM_ENFORCEMENT_HARD`
  in `mariadb.py` is the constant phase 5 flips; do not add an
  enforcement knob or a 403 path before it does. A claim carries two
  states which are two different facts: `state` is object existence in
  `object_states`, `coverage_state` (`active`/`expired`) lives in the
  claim row. See `docs/operator_guide/scheduler.md` and
  `docs/developer_guide/subsystem_internals.md`.
- **Per-daemon state** (`node_daemon_states` table): One row per
  `(node_uuid, daemon)` carrying the daemon's `value`, `update_time`
  and optional `message`. Replaces the JSON `daemon_states` dict that
  used to live on `node_attributes`; the dict required a coarse
  per-node lock for every transition which serialised every daemon's
  startup/shutdown through one hot path. The new table uses
  `INSERT ... ON DUPLICATE KEY UPDATE` so writes for different daemons
  on the same node run fully in parallel. The legacy JSON column on
  `node_attributes` is no longer read or written but remains for one
  release cycle as a rollback fallback.
- **Instance placement** (`object_references` table): Which instances
  are on which node is recorded as `instance_location` reference rows
  (source: node UUID, target: instance UUID). They are written only by
  the atomic `admit_instance_placement()` and
  `release_instance_placement()` RPCs, which move the capacity
  counters, write the `placement` attribute and rewrite the reference
  rows in one transaction; `Instance.place_instance()` is the sole
  caller for placement, and `Node` has no placement-writing helpers.
  This replaced the `instances` JSON list on
  `node_attributes`, whose full-row read-modify-write maintenance lost
  updates to concurrent writers (observed as scheduler affinity
  failures in CI). Reference rows are now the sole record of
  placement; the dual-write and the union in `Node.instances` were
  removed in scheduler-reservations phase 3, while the column itself
  remains in place (nullable, unread) as a rollback fallback until a
  later release drops it.
- **Cluster Locks** (`cluster_locks` table): Distributed locks with
  a server-side `expires_at TIMESTAMP`. Holders refresh the lease
  every ~20s while alive; if a holder dies (or is partitioned for
  >60s), a candidate steals the row by issuing
  `UPDATE ... WHERE expires_at < NOW()`. There is no garbage-
  collection step or external reaper -- a dead holder's lock recovers
  on the next acquire attempt. See
  `docs/operator_guide/locks.md` for the operator view and
  `shakenfist/locks.py` for the refresher and `lost_event` protocol.

### Migrating Existing Deployments

Schema creation and migration are run **explicitly** via `sf-ctl
ensure-mariadb-schema` (which requires `MARIADB_HOST` and runs on a
database-tier node). As of `PLAN-byo-mariadb.md`, `sf-database` no longer
migrates at startup: it runs `verify_mariadb_compat` and
`verify_schema_versions` (`shakenfist/daemons/database/main.py`) and
**refuses to start** if the schema is not at the version its build
expects. After an upgrade that includes schema changes, run `sf-ctl
ensure-mariadb-schema` against your MariaDB *before* rolling the daemons.
Migrations are idempotent and safe to re-run.

### State Class

The `State` class is a Pydantic model:
```python
from shakenfist.schema.object_state import State

state = State(value='created', update_time=123.0, message='optional')
```

Use keyword arguments and float for `update_time` (not int).

## CI/CD and Repository Configuration

### Merge Queue Pattern

The CI uses a two-stage merge queue pattern:
- **`Can enqueue`** - Runs on `pull_request` events, gates entry to merge queue
- **`Can merge`** - Runs on `merge_group` events, gates the actual merge

Only `Can see status` and `Can enqueue` are required status checks in branch
protection. `Can merge` is evaluated by the merge queue itself.

### Exported Repository Configuration

GitHub repository settings (rulesets, branch protection, merge queue config)
are exported daily to `.github/exported-config/` for version control:

- `repository-settings.json` - Repo-level settings
- `rulesets-summary.json` - List of all rulesets
- `ruleset-*.json` - Full details for each ruleset

If settings change in the GitHub UI, the `export-repo-config` workflow creates
a PR to track the change. This provides an audit trail for configuration drift.

## Documentation

- MkDocs site: `mkdocs serve` from project root
- Key docs:
  - `ARCHITECTURE.md` - System architecture overview and index into `docs/`
  - `AGENTS.md` - Instructions for AI agents, and the same index
  - `docs/manifesto.md` - Design philosophy
  - `docs/developer_guide/standards.md` - Code conventions, testing, the
    authoritative style guide this file summarises
  - `docs/developer_guide/coding_rules.md` - Rules that each came out of a
    real defect
  - `docs/developer_guide/database_internals.md` - Object cache, filter
    pushdown, gRPC reliability, cluster operations, work queues
  - `docs/developer_guide/subsystem_internals.md` - Scheduler capacity, node
    and API health surfaces, daemon watchdog, REST contracts
  - `docs/developer_guide/security_model.md` - Trust boundaries and
    authorisation
  - `docs/developer_guide/ci.md` - Workflows, merge queue, bot commands
  - `docs/components/` - Architecture docs
  - `docs/operator_guide/database.md` - Database architecture and MariaDB migration
