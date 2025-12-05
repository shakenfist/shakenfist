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

### Key Entry Points (CLI Tools)

- `sf-ctl` - Control CLI
- `sf-api` - REST API server
- `sf-cleaner`, `sf-cluster`, `sf-net`, `sf-queues`, `sf-resources` - Daemons

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
| `etcd.py` | Distributed state storage and locking |
| `config.py` | 100+ Pydantic settings with etcd/env overrides |

### Storage: etcd

All cluster state is stored in etcd:

```python
# Access pattern
etcd.get('object_type', 'parent_uuid', 'object_uuid')
etcd.put('object_type', 'parent_uuid', 'object_uuid', data)
etcd.delete('object_type', 'parent_uuid', 'object_uuid')
```

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

## Documentation

- MkDocs site: `mkdocs serve` from project root
- Key docs:
  - `docs/manifesto.md` - Design philosophy
  - `docs/developer_guide/` - Development guidance
  - `docs/components/` - Architecture docs
