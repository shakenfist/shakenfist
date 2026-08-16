# ARCHITECTURE.md - Shaken Fist System Architecture

## Overview

Shaken Fist is a minimal cloud orchestration platform for VM and network
management, designed to be understood in its entirety by a single developer.

This document is the map: the components, how they fit together, and the
decisions that shaped them. Subsystem detail lives in `docs/`, indexed
below.

The table below covers the shape of the system only. Working conventions
-- code style, the rules learned from past defects, CI and the bot
commands -- are indexed by [AGENTS.md](AGENTS.md) instead, so the two
tables do not need to be kept in step with each other.

## Where the detail lives

| Topic | Document |
|-------|----------|
| Database internals: object cache, filter pushdown, gRPC reliability, cluster operations | [docs/developer_guide/database_internals.md](docs/developer_guide/database_internals.md) |
| Deployment, table inventory, schema system | [docs/operator_guide/database.md](docs/operator_guide/database.md) |
| Network operation dispatch, queue families, error handling | [docs/developer_guide/network_dispatcher.md](docs/developer_guide/network_dispatcher.md) |
| REST API contracts, scheduler capacity arithmetic, node and API health surfaces, daemon watchdog, object references | [docs/developer_guide/subsystem_internals.md](docs/developer_guide/subsystem_internals.md) |
| Security model and trust boundaries | [docs/developer_guide/security_model.md](docs/developer_guide/security_model.md) |
| Scheduler pipeline and placement diagnosis | [docs/operator_guide/scheduler.md](docs/operator_guide/scheduler.md) |
| Object state machines | [docs/developer_guide/state_machine.md](docs/developer_guide/state_machine.md) |
| Authentication and federated identity | [docs/developer_guide/authentication.md](docs/developer_guide/authentication.md) |
| Logging and log shipping | [docs/operator_guide/logging.md](docs/operator_guide/logging.md) |
| Node health recovery and operator runbook | [docs/operator_guide/node_health.md](docs/operator_guide/node_health.md) |

Anything not in that table is still in `docs/`. The complete list of
pages, in reading order, is the `nav:` section of
[mkdocs.yml](mkdocs.yml), rendered at
[shakenfist.com](https://shakenfist.com/).

## System Components

### Daemons

Shaken Fist runs several daemons on each cluster node:

| Daemon | Purpose | Port |
|--------|---------|------|
| `sf-api` | REST API server (Flask/Gunicorn) | 13000 |
| `sf-database` | Database microservice (MariaDB access; runs on database-tier nodes) | 13005 |
| `sf-cleaner` | Resource cleanup | - |
| `sf-cluster` | Cluster maintenance | - |
| `sf-net` | Network daemon | - |
| `sf-queues` | Job queue processing | - |
| `sf-resources` | Resource tracking; also drives `node.state` from storage health ([node resource health](docs/developer_guide/subsystem_internals.md#node-resource-health)) | - |
| `sf-transfers` | Blob transfers | - |
| `sf-privexec` | Privileged execution | - |

### Database layer

Object state, cluster operations and work queues all live in MariaDB,
reached through the `sf-database` microservice. The static object value
cache, the SQL filter-pushdown discipline, gRPC reliability, and the
cluster operation tracking and work-queue machinery are documented in
[docs/developer_guide/database_internals.md](docs/developer_guide/database_internals.md);
deployment, the table inventory and the schema system are in
[docs/operator_guide/database.md](docs/operator_guide/database.md).

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

### Network operations

Network work is dispatched through queue families with dependency
waiting, exponential back-off, deferred re-queue and a terminal-state
check at dequeue. The dispatcher, its queue families and its error
handling at the queue boundary are documented in
[docs/developer_guide/network_dispatcher.md](docs/developer_guide/network_dispatcher.md).

### REST API surface

Network delete endpoints follow a 202+poll contract, cluster operations
are discoverable through `/clusteroperations/`, and the VDI console
proxy endpoints mint short-lived Ed25519 JWTs. The contracts, the
`redirect_to_network_node` status, and the matching client-python
behaviour are in
[docs/developer_guide/subsystem_internals.md](docs/developer_guide/subsystem_internals.md).

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

### Object references

Typed relationships between objects are stored in `object_references`
rather than as JSON columns on each object. See
[docs/developer_guide/subsystem_internals.md](docs/developer_guide/subsystem_internals.md).

### Logging and log shipping

Daemons log structured JSON via `shakenfist_utilities.logs`, one object
per line. Shaken Fist does not aggregate logs onto a primary node: when
`LOKI_BASE_URL` is configured each daemon ships its own logs to that
operator-provided Loki through an in-process, on-disk-spooled, batched
HTTP push; otherwise it logs to the local systemd journal. See
[docs/operator_guide/logging.md](docs/operator_guide/logging.md).

## Instance Scheduling

The scheduler (`shakenfist/scheduler.py`) is in-process in each `sf-api`
worker; there is no scheduler daemon. It filters candidate hypervisors
against the `node_metrics` table (hard pre-filters: hypervisor role, queue
health, CPU/RAM/disk headroom, disk bandwidth), scores affinity, then
ranks by **load per schedulable thread** in coarse buckets with
headroom-weighted selection so differently sized machines share work
proportionally. These pre-filters order and prune the candidate list from
a metrics snapshot up to a minute stale; they are not themselves the
admission decision.

Admission is a separate, atomic step. Once the pipeline has picked a
candidate, `Instance.place_instance()` makes one guarded capacity claim
against the `scheduler_node_capacity` counters, in the same database
transaction that writes the placement — so a placement cannot be recorded
without the capacity it consumes. A refusal walks the caller to its next
candidate; every candidate refusing is the ordinary 507 outcome.

Capacity is reservation-aware: the resources daemon reserves hardware
threads and RAM for the operating system on every hypervisor, and
publishes the schedulable remainder (`cpu_schedulable`,
`memory_reserved_mb`) in `node_metrics`. The pre-filters and the
`/admin/resources` API share the same arithmetic through common helpers.
`CPU_OVERCOMMIT_RATIO` is denominated in vCPUs per schedulable thread
(default 3.0, measured on a CI-dominated cluster).

See [`docs/operator_guide/scheduler.md`](docs/operator_guide/scheduler.md)
for the full pipeline, the configuration knobs, the admission RPCs at the
bottom of it, and how to diagnose a placement decision from audit events.
Atomic reservation-table scheduling is being built in phases per
`docs/plans/PLAN-scheduler-reservations.md`; the capacity tables and their
reconciler are described under
[Cluster Operation Storage and Work Queues](docs/developer_guide/database_internals.md#cluster-operation-storage-and-work-queues).
Placement admission consumes them as of phase 3, and phase 4 added
per-namespace capacity claims whose ceilings are advisory this release;
later phases enforce those ceilings and move more pre-filter logic into
SQL.

## State Machines

Objects follow defined state machines. Key states:

### Instance States
- `initial` -> `preflight` -> `creating` -> `created`
- `created` -> `deleted` (soft delete)
- `created` -> `error` (on failure)

### Network States
- `initial` -> `created`
- `created` -> `deleted`

### Namespace Key States
- `initial` -> `created`
- `created` -> `deleted` (soft delete)

A `NamespaceKey` (`shakenfist/namespace_key.py`) is the credential a namespace
authenticates with, and is a database-backed object owned by its namespace.
There is no error state, because key operations are atomic. Expiry is not a
state: it is enforced when the key is used, and the cluster daemon separately
soft-deletes long-expired keys so that the standard reaper hard-deletes them.

### Trusted Issuer and Mapping Rule States
- `initial` -> `created`
- `created` -> `deleted` (soft delete)

Both follow the `NamespaceKey` recipe. A `TrustedIssuer`
(`shakenfist/trusted_issuer.py`) is a cluster-level record of an external
identity provider whose tokens this cluster will accept, managed through
`/auth/issuers` by the `system` namespace only. A `MappingRule`
(`shakenfist/mapping_rule.py`) is owned by the namespace it targets, managed
under `/auth/namespaces/{namespace}/rules` by that namespace's owner, and hard
deleted with its namespace.

See `docs/developer_guide/state_machine.md` for complete documentation.

## Federated Identity Exchange

`POST /auth/federated` (`shakenfist/external_api/auth.py`) trades an
externally issued identity token for a scoped, expiring `NamespaceKey`. It is
one of the handful of `@api_base.public` routes, because the caller by
definition has no Shaken Fist credential yet.

Token validation lives in `shakenfist/federation.py`, which is deliberately
Flask-free: issuer resolution from an unverified `iss`, signature checking
against a `PyJWKClient` cache (one client and one lock per issuer, so a key
rotation does not stampede the provider), audience and lifetime checks, and
claim matching against a rule. The endpoint composes these in a fixed order —
cheap local rejections before anything that costs a network round trip — and
that order is a security property rather than a style, asserted by tests.

The JWKS fetch verifies TLS against the system trust store, plus whatever
`FEDERATION_JWKS_CA_BUNDLE` names, for a provider behind a private CA. Those
anchors are added to the system set rather than replacing it —
`ssl.create_default_context(cafile=...)` would replace it, and quietly stop a
public issuer verifying — and nothing else is relaxed: `jwks_uri` must be
`https://`, and there is no skip-verification option.

The rate limit is the dividing line in that order, and only the argument
checks sit above it. Issuer resolution in particular sits *below* it: it scans
the configured issuers and reads state and attributes per row, so although a
cluster only ever has a handful of issuers, leaving that above the meter gave
an anonymous caller a way to multiply one request into database work with
nothing counting it. The rule for anything added to this endpoint is that the
meter goes above whatever touches the database or the network, not merely
above whatever is slow.

Two plain (non-DBO) tables back the abuse resistance, both reaped by the
cluster daemon's `reap_federation_records`:

- `federation_replay`, keyed `(token_id, rule_uuid)`, makes an identity token
  single-use per rule. The composite primary key does the arbitration, so a
  failing insert *is* the replay detection.
- `federation_rate_limits`, keyed `(source, window_start)`, counts exchange
  attempts per source per minute in the database rather than in the worker, so
  the limit is cluster-wide.

Both fail closed: a database error refuses the exchange rather than being read
as "not seen before" or "under the limit".

The replay record is keyed on the token's `jti` when the issuer supplies one,
and otherwise on a hash of the token's *signed material* — header and payload.
Not the signature: base64url leaves four don't-care bits in the final
character of an RS256 signature and the padding is optional, so one signature
has dozens of spellings which all verify, and keying on the text would have
given an attacker one replay slot per spelling. The signature commits to the
signed material, so an attacker cannot vary it without invalidating the token.

Because the endpoint is unauthenticated, its input bound is enforced in an
`@app.before_request` hook (`limit_federated_body_size`) rather than in the
method. By the time a `flask_restful` method runs, `log_request` has already
parsed the body, so a check there cannot prevent the work it exists to
prevent.

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

The Kerbside VDI console proxy integration is configured here too:
`KERBSIDE_URL` (empty by default, which disables the integration; it is both
the returned console URL base and the token audience) and
`KERBSIDE_TOKEN_DURATION` (token lifetime in seconds, default 300). These are
Shaken Fist cluster settings and are distinct from the Kerbside proxy
daemon's own `KERBSIDE_`-prefixed environment. The signing key itself is
stored in `cluster_config` as `KERBSIDE_JWT_SIGNING_KEY`.

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
Operator-provided load balancer / reverse proxy (adds /api/ prefix)
   |  (probes /readyz on port 13000 for readiness)
   v
Gunicorn (port 13000)
   |
   v
Flask app (external_api/app.py)
   |
   +-> /livez    - Liveness probe (unauthenticated, always 200)
   +-> /readyz   - Readiness probe (unauthenticated, 200/503)
   +-> /healthz  - Alias of /readyz
   +-> /auth/* - Authentication endpoints
   +-> /instances/* - Instance management
   +-> /networks/* - Network management
   +-> /artifacts/* - Image management
   +-> /blobs/* - Blob storage
   +-> /nodes/* - Cluster management
```

## Security model

Trust boundaries, the authentication and authorisation model, namespace
isolation, and the VDI console token trust model are documented in
[docs/developer_guide/security_model.md](docs/developer_guide/security_model.md).

## Key directories

- `shakenfist/` - Core package
- `shakenfist/daemons/` - Background services
- `shakenfist/external_api/` - REST API
- `.github/workflows/` - CI workflows
- `.github/exported-config/` - Exported GitHub settings
