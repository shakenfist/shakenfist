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
  as well (see `shakenfist/deploy/cluster_ci` for functional coverage). We
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
│   └── tests/               # Test suite
├── docs/                    # MkDocs documentation
└── deploy/                  # Deployment configurations
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
| `database.py` | Database microservice client library |
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

**Database daemon special case**: The database daemon has `MARIADB_HOST`
set, which causes it to use direct MariaDB access for its own startup and
shutdown recording. All other daemons access MariaDB via the database
service's gRPC interface.

### Systemd Service Ordering

Shaken Fist daemons are managed via systemd with careful ordering defined in
`deploy/ansible/files/sf.service`. The startup order is:

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
example: it sleeps via `lock.lost_event.wait(60)` so it wakes
immediately on confirmed loss.

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
4. **State machine transitions** - Follow documented state machines in
   `docs/developer_guide/state_machine.md`

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
  for the schemaless metrics payload (~50+ fields). One row per node,
  upserted each update cycle. Primary key is `node_uuid`.
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

Object schema upgrade steps run automatically when the database daemon
starts. Simply upgrade and restart the `sf-database` service. Migrations
are idempotent and safe to re-run.

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
  - `ARCHITECTURE.md` - System architecture overview
  - `AGENTS.md` - Instructions for AI agents
  - `docs/manifesto.md` - Design philosophy
  - `docs/developer_guide/` - Development guidance
  - `docs/components/` - Architecture docs
  - `docs/operator_guide/database.md` - Database architecture and MariaDB migration
