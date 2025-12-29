# CLAUDE.md - Shaken Fist Development Guide

## Project Overview

Shaken Fist is an opinionated, minimal cloud orchestration platform for VM and
network management. It prioritizes simplicity and maintainability over feature
completeness, designed to be understood in its entirety by a single developer.

- **Homepage**: https://shakenfist.com/
- **Repository**: https://github.com/shakenfist/shakenfist

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
- `sf-database` - Database microservice daemon (runs on etcd_master)
- `sf-cleaner`, `sf-cluster`, `sf-net`, `sf-queues`, `sf-resources` - Daemons

### Generating gRPC Stubs from Proto Files

Proto files are in `protos/` and generated Python stubs go to `shakenfist/protos/`.
To regenerate after modifying `.proto` files:

```bash
cd shakenfist/protos
../../protos/_make_stubs.sh
```

**Note:** Requires `grpcio-tools` installed. The script works on both macOS and
Linux.

## Code Style and Conventions

### Python Style

- Use **single quotes** for strings, **double quotes** for docstrings
- Wrap lines at **80 characters**
- Trim trailing whitespace
- Follow existing import ordering (stdlib, third-party, shakenfist)

### Import Organization

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
│   ├── etcd.py              # etcd client wrapper and cluster locks
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
│   │   ├── database/        # Database microservice (etcd wrapper)
│   │   ├── eventlog/        # Event logging service
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
│   ├── etcd_schema/         # etcd data schema (Pydantic models)
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
| `etcd.py` | Distributed state storage and locking (with database service shim) |
| `database.py` | Database microservice client library |
| `config.py` | 100+ Pydantic settings with etcd/env overrides |

### Storage: etcd, MariaDB, and the Database Service

Shaken Fist uses two database backends:
- **etcd**: Object storage, cluster coordination, locks, queues
- **MariaDB**: Object state storage (for efficient state-based queries)

The database microservice (`sf-database`) runs on the etcd_master node and
provides a gRPC interface for all database operations:

```python
# etcd access (works with both direct and via database service)
etcd.get('object_type', 'parent_uuid', 'object_uuid')
etcd.put('object_type', 'parent_uuid', 'object_uuid', data)
etcd.delete('object_type', 'parent_uuid', 'object_uuid')

# MariaDB state access (automatically routed through database service)
from shakenfist import mariadb
mariadb.get_state('instance', 'uuid-here')
mariadb.set_state('instance', 'uuid-here', state)
mariadb.get_objects_by_state('instance', ['created', 'error'])
```

This abstraction layer:
- Centralizes all database access to a single service
- Only the database daemon has direct access to etcd and MariaDB
- Provides prometheus metrics for all database operations
- Enables clean separation of concerns

Configuration options:
- `DATABASE_NODE_IP` - IP address of the database service node
- `DATABASE_API_PORT` - gRPC API port (default: 13005)
- `DATABASE_METRICS_PORT` - Prometheus metrics port (default: 13006)
- `DATABASE_USE_DIRECT_ETCD` - Set to false for all daemons except database

**Database daemon special case**: The database daemon uses direct etcd/MariaDB
access (implicit `DATABASE_USE_DIRECT_ETCD=True`) and uses
`etcd.set_force_direct_etcd(True)` during startup/shutdown to avoid a
chicken-and-egg problem.

### Configuration Bootstrap Order

**IMPORTANT**: Shaken Fist has a two-stage configuration system due to a
chicken-and-egg problem: some configuration values are stored in etcd, but
the etcd connection itself must be configured before etcd can be read.

**Stage 1 - Environment/File (before etcd):**
- `SHAKENFIST_ETCD_HOST` - **Must be set via environment variable** (cannot be
  stored in etcd because we need it to connect to etcd)
- `SHAKENFIST_DATABASE_USE_DIRECT_ETCD` - Should be set via environment to
  determine how to access the database during bootstrap

**Stage 2 - etcd-stored configuration:**
- All other `SHAKENFIST_*` settings can be stored in etcd at `/sf/config`
- These are loaded by `load_etcd_settings()` at module import time in `config.py`
- Settings from etcd are exported as environment variables for the process

**Implications for Ansible deployment:**
- When running `sf-ctl` commands that need etcd access, always pass
  `SHAKENFIST_ETCD_HOST` as an environment variable in the task
- Use Jinja2 templating (e.g., `"{{ etcd_host }}"`) not literal strings
- The database daemon registration must use `SHAKENFIST_DATABASE_USE_DIRECT_ETCD=True`
  because the database service isn't running yet during its own registration

**Implications for CLI tools (sf-ctl, sf-backup):**
- These tools read `/etc/sf/config` at startup to populate environment variables
- Environment variables set before running the tool take precedence over the
  config file values

### Systemd Service Ordering

Shaken Fist daemons are managed via systemd with careful ordering defined in
`deploy/ansible/files/sf.service`. The startup order is:

1. `database` - Starts first (after multi-user.target), provides gRPC access to
   etcd and MariaDB for all other daemons
2. `sentinel-first` - Starts after database, marks node as starting
3. `privexec`, `nodelock` - Start after sentinel-first
4. All other daemons - Start after privexec, nodelock, and database
5. `sentinel-last` - Starts after all other daemons, signals shutdown state

**Database Access Pattern**:
- The `sf-database` service is the **only** daemon with direct access to etcd
  and MariaDB
- All other daemons access databases through the database service's gRPC interface
- The database daemon uses `DATABASE_USE_DIRECT_ETCD=True` (implicit, by not
  setting the env var to false) to use direct database access
- Other daemons have `SHAKENFIST_DATABASE_USE_DIRECT_ETCD=false` set, routing
  their database operations through the gRPC interface

**Exceptions**:
- `eventlog` - Uses direct etcd for its own startup/shutdown to avoid deadlock
- `sentinel-last` - Marker service that doesn't need database access

This ordering is critical because daemons like `sf-api` will hang on startup
if they try to connect to the database microservice before it's running.

### Locking

Use `ClusterLock` for distributed operations:

```python
from shakenfist.etcd import ClusterLock

with ClusterLock('lock_name', timeout=30):
    # Critical section
```

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

- **When talking through Apache** (standard external access): The `/api/`
  prefix is added by the Apache reverse proxy configuration
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
- Heavy use of `mock.patch` for etcd and external dependencies
- Tests mirror module structure in `shakenfist/tests/`

## Key Dependencies

- **etcd3gw** - etcd client
- **grpcio/protobuf** - gRPC communication
- **Flask/Flask-RESTful/Flasgger** - REST API
- **Flask-JWT-Extended** - Authentication
- **Pydantic** - Configuration and schemas
- **shakenfist-utilities** - Shared utilities library

## Common Pitfalls

1. **Decorator order in API endpoints** - Read comments in `external_api/app.py`
2. **etcd connection management** - Use thread-local clients from `etcd.py`
3. **Lock timeouts** - Always specify reasonable timeouts for ClusterLock
4. **State machine transitions** - Follow documented state machines in
   `docs/developer_guide/state_machine.md`

## MariaDB State Storage

Object state (e.g., "created", "deleted", "error") is stored in MariaDB in the
`object_states` table. This is required for all deployments - MariaDB must be
configured.

### Migrating Existing Deployments

When upgrading an existing deployment to use MariaDB for state storage:

1. Stop all Shaken Fist services on all nodes
2. Run `sf-ctl migrate-state-to-mariadb` to migrate state data from etcd
3. Start services via getsf

The migration command copies all state from etcd attributes to MariaDB and
removes the old etcd entries.

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
