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
| `sf-database` | Database microservice (etcd/MariaDB access) | 13005 |
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
              +----------------+----------------+
              |                                 |
       +------+------+                   +------+------+
       |    etcd     |                   |   MariaDB   |
       | (objects,   |                   | (state,     |
       |  locks,     |                   |  IPAM,      |
       |  queues)    |                   |  uploads,   |
       +-------------+                   |  blobs,     |
                                         |  nodes,     |
                                         |  namespaces,|
                                         |  dnsmasq,   |
                                         |  object_    |
                                         |  references)|
                                         +-------------+
```

The database microservice (`sf-database`) centralizes all database access:
- Only the database daemon has direct access to etcd and MariaDB
- All other daemons use the gRPC interface
- Provides Prometheus metrics for database operations

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

1. **Stage 1**: Environment/file configuration (for etcd connection)
2. **Stage 2**: etcd-stored configuration (loaded after connection)

Key configuration sources:
- `/etc/sf/config` - Local configuration file
- etcd `/sf/config` - Cluster-wide configuration
- Environment variables (highest priority)

### Node Identity

Each node has a real UUID (not FQDN-based) stored in MariaDB. The UUID is
persisted locally to `{STORAGE_PATH}/node_uuid` on first run so that
subsequent daemon starts can look up the node directly by UUID rather than
performing an FQDN-to-UUID indirection. The UUID can also be set explicitly
via the `NODE_UUID` config field or `SHAKENFIST_NODE_UUID` environment
variable.

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
  `localhost,127.0.0.1,10.0.0.0/8` to prevent local service traffic
  (e.g. etcd API calls) from being routed through the proxy.
  Additionally, `WrappedEtcdClient` sets `trust_env = False` on its
  requests session as defense in depth.

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
