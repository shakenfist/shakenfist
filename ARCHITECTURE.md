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
| `sf-database` | Database microservice (MariaDB access; runs on database-tier nodes) | 13005 |
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
                        |  events,    |
                        |  event_     |
                        |  objects,   |
                        |  node_      |
                        |  daemon_    |
                        |  states)    |
                        +-------------+
```

The database microservice (`sf-database`) centralizes all database access:
- Only the database daemon has direct access to MariaDB
- All other daemons use the gRPC interface
- Provides Prometheus metrics for database operations

The `sf-database` box in the diagram represents a tier of N >= 1 instances.
All instances connect to the same MariaDB; none is elected. Every other SF
daemon reaches the tier through a client-side load-balanced gRPC channel
constructed over the `MARIADB_GATEWAY_HOSTS` list of endpoints. Dead
endpoints are skipped via subchannel connectivity state and client
keepalives (10 s ping / 5 s timeout). `sf-database` also publishes the
`grpc.health.v1.Health` protocol for external monitoring via unary `Check`
calls; Watch-based client-side health checking is deliberately not enabled
because the synchronous health servicer can deadlock the gRPC server's
event thread (see `shakenfist/util/grpc_channel.py`).
The overall (`''`) service status is dependency-aware: while `sf-database`
is running it reports `SERVING` only while it can reach MariaDB, and flips
to `NOT_SERVING` (on the ~10 s background loop) when MariaDB becomes
unreachable. Schema currency is a refuse-to-start precondition enforced at
startup, not a runtime health signal.
See [`docs/operator_guide/database.md`](docs/operator_guide/database.md) —
"MARIADB_HOST vs MARIADB_GATEWAY_HOSTS" — for the operator-facing detail.

#### sf-api HTTP health surface

`sf-api` exposes three unauthenticated HTTP endpoints on port 13000 for load
balancer probing:

| Endpoint | Purpose |
|----------|---------|
| `GET /livez` | Liveness — always returns `200 ok`; indicates the worker process is alive |
| `GET /readyz` | Readiness — returns `200 ready` when the worker can serve traffic, or `503 not ready` when draining or when sf-database is unreachable |
| `GET /healthz` | Alias of `/readyz` |

Readiness is evaluated by a per-worker background checker thread
(`shakenfist/external_api/health.py`) that polls sf-database's
`grpc.health.v1.Health/Check` every 5 seconds and caches the result. The
cached flag flips to False only after three consecutive failures
(`READINESS_FAIL_THRESHOLD`), debouncing transient blips. A staleness guard
means a wedged checker is also treated as not-ready.

On SIGTERM the worker latches a one-way draining flag (`begin_drain()`), which
causes `/readyz` to return 503 immediately. The worker then continues serving
live requests for `API_DRAIN_GRACE` seconds (default 25 s) before shutting
down. This allows the load balancer to detect the draining node and stop
routing new connections before the process exits.

**Load balancer guidance**: probe `/readyz` (or `/healthz`) on port 13000 for
the readiness signal. `sf-api` is the only LB-routable surface — all other
daemons communicate internally via gRPC or the MariaDB-backed work queue.

#### Daemon liveness (systemd watchdog)

The eight non-trivial daemons — `sf-database`, `sf-net`, `sf-cleaner`,
`sf-cluster`, `sf-queues`, `sf-resources`, `sf-transfers`, and
`sf-sidechannel` — are armed with `WatchdogSec=60s` in `sf.service`.
Four units are deliberately excluded: `sentinel-first`, `sentinel-last`,
`sf-privexec`, and `sf-nodelock`. Those are short-lived or event-driven
processes that do not run the `idle()`-based keepalive loop; arming them
would kill a healthy daemon that is simply waiting for its trigger.
`sf-api` is also excluded — Gunicorn has its own `--timeout` worker-liveness
mechanism.

The keepalive is emitted by `Daemon.pet_watchdog()` in
`shakenfist/daemons/daemon.py`, which writes `sd_notify(WATCHDOG=1)` at
most every ~10s. `Daemon.idle()` (the standard end-of-pass sleep) calls
it automatically, so every daemon whose main loop reaches `idle()` pets the
watchdog without additional instrumentation.

Daemon passes that do substantial work **without** returning to `idle()`
must call `pet_watchdog()` explicitly:

- `sf-cluster` elected loop: this loop sleeps on
  `lock.lost_event.wait(5)` rather than `idle()`, so it calls
  `pet_watchdog()` explicitly at the top of each iteration.
- `sf-cluster` `_cluster_wide_cleanup` and `sf-cleaner`
  `_maintain_blobs` / `_find_missing_blobs`: call `pet_watchdog()` around
  inner-loop iterations that may each take several seconds.

If a daemon's main loop wedges and stops petting, systemd delivers SIGABRT
after `WatchdogSec` (60s) and restarts the process (`Restart=on-failure`).

For the **elected `sf-cluster`** this also acts as the cluster-lock
failover trigger: when the wedged process is killed, its in-process lease
refresher thread dies with it. The `cluster/` lease has a 60s lifetime
(refreshed every ~20s). Once it lapses a standby `sf-cluster` node steals
the lock via `UPDATE ... WHERE expires_at < NOW()` and resumes the
maintenance loop. Worst-case failover time is approximately 120s (60s
watchdog timeout + 60s lease expiry). No manual operator intervention is
needed.

See [`docs/operator_guide/locks.md`](docs/operator_guide/locks.md) for
the full lease-expiry and lock-steal protocol.

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

Every operation schema's `model` class declares its target objects via a
`target_fields: ClassVar[dict[str, ObjectType]]` class variable. When
`enqueue_cluster_operation` (in `schema/operations/util.py`) writes the
`cluster_operations` row, it reads that declaration and writes one
`cluster_operation_targets` row per non-None target field. Callers have
no per-target bookkeeping obligation — targets are recorded automatically.

The setter `_set_last_cluster_operation` is private and used only by the
internal enqueue plumbing. It is not a public API and should not be called
directly.

`DatabaseBackedObjectWithOperations` exposes two read shapes over the
history:

- **`last_cluster_operation` (property)**: returns the most recent target
  row regardless of its state. Consumed by `external_view()` projections
  and `runs_after=[...]` chains, which want the latest pointer
  independent of whether it has reached a terminal state.
- **`has_pending_cluster_operation()` (method)**: returns `True` if any
  target row's operation is in `{queued, preflight, executing}`. Consumed
  by `Network.is_okay()` and any future history-aware gate. The query
  joins `cluster_operation_targets` against `object_states`, so a later
  terminal operation cannot mask an earlier in-flight one.

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
Audit events are written directly into MariaDB via the local
spool drainer's `mariadb.record_event_batch` call.

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

#### Batched, Priority-Aware Dequeue

`sf-net` and the `sf-queues` worker pool both call a single
`mariadb.dequeue_work_items(queue_names, limit)` primitive. The
caller passes the queue names in priority order (index 0 = top
priority); MariaDB honours that order via
`ORDER BY FIELD(queue_name, ...), scheduled_at` so one SELECT
returns the most important eligible work first across an arbitrary
number of queues. The previous one-RPC-per-queue polling loop is
replaced by one RPC per iteration regardless of how many priority
lanes the worker drains.

Lower-priority rows only spill in when the higher-priority queues
yield fewer rows than `limit`, so sustained heavy load on
`user_facing` can still crowd `background` out -- explicit fairness
(bounded staleness or reserved-slot) is intentionally deferred to a
follow-up step. Worker crash recovery is unchanged: any claimed-
but-not-yet-executed rows that the worker doesn't run are picked up
by the stuck-row reaper described above.

#### Coalescible Operations

Some operation tasks are idempotent reconciliation work whose effect
depends only on current DB state, not on the count of pending ops
asking for it. The canonical example is
`network_apply_update_dnsmasq`: six instance starts on the same
network each enqueue one, but the resulting dnsmasq config covers
every lease no matter whether the worker ran it once or six times.

Op classes that have such tasks declare them on the class:

```python
class NetOp(BaseClusterOperation):
    coalescible_tasks = schema.COALESCIBLE_TASKS
    coalescible_target_column = 'network_uuid'
```

`coalescible_target_column` names the indexed column on
`cluster_operations` used to group sibling ops. The fold runs at
two layers, both controlled by this metadata:

* **Enqueue-side dedup**
  (`mariadb.find_existing_coalescible_op`): `create_and_enqueue`
  in the schema module checks for an existing pending single-task
  coalescible op on the same target before inserting a new row. If
  found, the new caller's `op_uuid` is the existing op's `op_uuid`.
  All `raise_for_error` waiters then block on the same op and the
  worker runs it once.

* **Worker-side fold**
  (`mariadb.claim_coalescible_siblings`): inside
  `BaseClusterOperation.execute`, the survivor atomically
  transitions every other pending coalescible op on the same target
  to `STATE_COMPLETE` in one SQL statement. When the dispatcher
  surfaces a folded sibling's `work_queue` row, the terminal-state
  branch drops it cleanly. A `'coalesced sibling ops'` audit event
  on the survivor records the folded uuids.

The enqueue-side dedup is the cheaper of the two -- the row never
gets inserted -- but the worker-side fold is the safety net for the
race where two concurrent callers both lose the lookup.

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

### Network Operation Error Handling

#### ErrorReport — structured failure at the queue boundary

`ErrorReport` (`shakenfist/operations/error_report.py`) is the on-the-wire
representation of a failed cluster operation. When an `_apply_*` method raises
inside the dispatcher, `dispatch_task` catches the exception, calls
`ErrorReport.from_exception(e)`, persists the result via
`mariadb.set_cluster_operation_error`, and then transitions the op to
`STATE_ERROR`. The report is stored in its own `cluster_operation_errors` table
(one row per op UUID) and is never written to the `object_states` or
`cluster_operations` tables.

The crucial architectural property is that **errors are data, never rehydrated
Python exception classes**. This converges with gRPC's status-code model (and
JSON-RPC's error object model) and is an explicit rejection of the
`oslo.messaging` rehydration pattern, which made exception types load-bearing
across process boundaries. The stable `code` field (e.g.
`'network.ensure_mesh.failed'`) is the contract; `message`, `details`,
`origin_class`, and `traceback` are diagnostic and not part of the contract.

The registry `_EXCEPTION_CODE_REGISTRY` in `error_report.py` is the single
canonical place for the exception-to-code mapping. Extending support for a new
typed exception is a one-line change: add a row to the dict.

#### BridgedVXLanNetwork — worker-only mutation surface

`BridgedVXLanNetwork` (`shakenfist/network/bridged_vxlan_network.py`) is the
worker-side counterpart of `Network`. `Network` is the public facade that
external callers interact with; it enqueues operations rather than mutating
host state directly. `BridgedVXLanNetwork` wraps a `Network` instance and
exposes `_apply_*` methods that actually mutate per-hypervisor state (VXLAN
FDB table, dnsmasq, etc.).

The constructor is called **only** inside the workitem dispatcher (via the
`NetOp` task handlers in `net_op.py`). This makes re-entrancy through the
queue structurally impossible: the only way to bypass the queue and run a
mutation inline is to construct a `BridgedVXLanNetwork`, and that is gated to
the dispatcher. External callers always hold `Network`; in-worker callers
always hold `BridgedVXLanNetwork`.

All 15 host-mutating `Network` methods now enqueue rather than mutate
directly: `ensure_mesh`, `add_floating_ip`, `remove_floating_ip`,
`route_address`, `unroute_address`, `remove_nat`, `update_dnsmasq`,
`remove_dnsmasq`, `remove_dhcp_lease`, `update_dns_entry`,
`remove_dns_entry`, `create_on_hypervisor`, `delete_on_hypervisor`,
`create_on_network_node`, `delete_on_network_node`. Note that
`Network.enable_nat` no longer exists as a public method; it is now the
private internal helper `BridgedVXLanNetwork._apply_enable_nat`, called
only from within `_apply_create_on_network_node`.

The retired NetOp handler bodies (`_network_deploy` task 1,
`_network_destroy` task 2, `_network_update_dnsmasq` task 3) have been
removed. Their task-enum values are retained in
`shakenfist/schema/operations/net_op.py` for on-disk record compatibility;
any in-flight op referencing these tasks now raises `InvalidStateForTask`,
which the dispatcher's outer exception handler converts to `STATE_ERROR`
via `ErrorReport`. Active op-type dispatchers are: `net_op`, `net_ip_op`,
`net_iface_op`, `net_iface_ip_op`, `net_macaddr_ip_op`, plus
`node_net_op` and `node_inst_op` / `node_inst_netdesc_op`. All route
through `BridgedVXLanNetwork` and persist `ErrorReport` on their outer
exception branch.

The worker-only mutation surface is also why cross-daemon serialisation
can be queue-based rather than lock-based. The 13 per-network
`NodeLock(global_scope=False)` wrappers that existed inside
`BridgedVXLanNetwork._apply_*` methods originated in stability-branch
commit `bd9e1869`, which added them as a short-term fix to serialise
concurrent host-mutating callers from four daemons (`sf-net`, `sf-queues`,
`sf-api`, and `instance.py`). With all 15 `Network` methods now enqueuing
exclusively through `BridgedVXLanNetwork`, the net-worker dispatcher loop
in `shakenfist/daemons/network/workitem.py` is the only caller of every
`_apply_*` method, and that loop is single-threaded by construction — so
those locks became provably redundant and were removed (commit `277b0572`).
Cross-daemon serialisation is now provided by the queue itself: only one
daemon (`sf-net`) dequeues and executes work for any given network, making
concurrent invocation across daemons structurally impossible. The
single-threaded-dispatcher argument is specific to
`NodeLock(global_scope=False)`; it does not extend to `ClusterLock`s,
which serialise across the cluster and remain in use elsewhere.

#### op.error_report / op.raise_for_error — consumer-side API

`BaseClusterOperation` exposes two methods for callers that need to observe an
op's outcome:

- `op.error_report` — property that reads the `ErrorReport` from MariaDB on
  every access (no caching). Returns `None` for COMPLETE/ABORT ops.
- `op.raise_for_error(timeout=None)` — blocks until the op reaches a terminal
  state (using the `poll_until_terminal` helper underneath), then raises
  `NetworkOperationFailed(error_report=...)` if the state is `STATE_ERROR`, or
  returns silently on any other terminal state. The timeout defaults to
  `config.API_ASYNC_WAIT` (15 seconds); callers can override for long-running
  ops. Raises `OperationTimeout` if the deadline elapses.

`poll_until_terminal` is the generic free function underneath. It polls
`cls.from_db(op.uuid)` at a 0.1 s cadence until the state is in
`{STATE_COMPLETE, STATE_ABORT, STATE_DELETED, STATE_ERROR}`.

#### Per-method migration pattern

Each `Network` method is implemented as "enqueue a `NetOp` and return the
op handle" rather than mutating host state inline. External callers preserve
synchronous-with-exception semantics by wrapping the call:

```python
op = n.ensure_mesh()
op.raise_for_error()
```

In-worker callers in `net_op.py` use `BridgedVXLanNetwork(n)._apply_*`
directly to avoid enqueueing from inside the dispatcher (which would deadlock
the net-worker). `depends_on` chains may replace some of the per-call
`raise_for_error` waits where proper async pipelines are needed. All 15
host-mutating `Network` methods are now enqueue-only; the per-network
`NodeLock` guards that formerly serialised concurrent callers are no longer
needed and have been removed — the single-worker queue is the sole
serialisation point (see the `BridgedVXLanNetwork` subsection above).


#### In-worker sibling call pattern

When a `Network` method needs to invoke another host-mutating operation from
inside an already-executing worker context — for example, `create_on_network_node`
calling `update_dnsmasq` as part of `_network_deploy`, or `delete_on_network_node`
calling `remove_dnsmasq` — re-enqueueing through the normal `Network.X()` facade
would deadlock: the worker is already holding the queue slot and cannot dequeue
its own dependency. The correct pattern is to construct a `BridgedVXLanNetwork`
wrapper and call the `_apply_*` method directly:

```python
BridgedVXLanNetwork(self)._apply_update_dnsmasq(context)
```

This keeps host mutation inside `BridgedVXLanNetwork` (the worker-only surface),
avoids a queue round-trip, and eliminates the deadlock-by-timeout that existed in
Phase 3 where `create_on_network_node` enqueued `update_dnsmasq` and then waited
for it to complete — but the network-node queue has only one worker, so the
dependency could never be dequeued while the parent op was still executing. The
Phase 3 latent bug was fixed when this pattern was adopted in Phase 4.

#### Dual-event emission pattern

Each migrated `Network` method emits two audit events:

1. **Requesting event** — emitted synchronously inside `Network.X()` on the
   caller's thread, before the op is enqueued. Uses `affected_objects=` so that
   the event is recorded against all relevant objects (e.g. both the network and
   the floating network for floating-IP ops).
2. **Dispatch-time event** — emitted by the dispatcher once the op actually
   executes. The dispatcher has access to only the objects it has in scope: the
   `Network` itself plus `('network', FLOATING_NETWORK_UUID)` for floating-IP
   ops; the `NetworkInterface` for `net_iface_op` / `net_iface_ip_op` ops.

This split is intentional: the requesting event gives operators an immediate
audit trail that the call was received, while the dispatch-time event records
when the work actually ran and on which worker node.

### Network Operation Queue Families

Network operations are dispatched through two distinct queue families, both
in the same priority taxonomy (`user_waiting`, `user_facing`,
`user_facing_high_io`, `background`, `background_high_io`):

| Family | Queue name pattern | Drained by | Used for |
|--------|--------------------|------------|----------|
| Per-node network | `{node_uuid}-network-{priority}` | net-worker on that node only | `create_on_hypervisor`, `ensure_mesh` — operations that mutate per-hypervisor state |
| Network-node | `networknode-clusteroperation-{priority}` | net-worker on the elected network node only | `create_on_network_node`, `add_floating_ip`, `route_address` — operations that only the elected network node owns |

The `enqueue_cluster_operation()` helper selects the family via its
`family='network'` keyword argument. Passing `family='network'` produces
`{node_uuid}-network-{priority}` queue names; the default
`family='clusteroperation'` produces the existing node/cluster-operation queues.

**Single-worker-per-queue safety property.** The net-worker's in-memory
exponential back-off map (see the developer guide) is correct only because each
queue it drains is serviced by exactly one worker. Per-node queues are drained
by the net-worker on that specific node; cluster-wide `networknode-*` queues are
drained only by the elected network node's net-worker. Introducing a second
worker on the same queue would break the back-off schedule — see the prominent
comment at the map's declaration in
`shakenfist/daemons/network/workitem.py` for the authoritative statement of
valid mitigation strategies.

### REST API surface

**202+poll contract for delete endpoints.** `DELETE /networks/<uuid>` and
`DELETE /networks` return HTTP 202 (Accepted). The response body carries
the cluster-operation handle so clients can poll for completion:

- Single delete: `{'op_type': 'net_op', 'op_uuid': '<uuid>'}`.
- Bulk delete: a list of `{'network_uuid': '...', 'op_type': 'net_op',
  'op_uuid': '...'}` entries, one per network.

**Cluster-operation discovery endpoints.** Two endpoints under
`/clusteroperations/` allow callers to inspect op history:

- `GET /clusteroperations/<op_uuid>/chain` — walks the `depends_on` graph
  from `<op_uuid>` and returns the full transitive ancestor closure as a
  list of op-summary dicts. Namespace-scoped: admin callers see everything;
  non-admin callers receive HTTP 403 if any chain member belongs to a
  foreign namespace. The op uuid is sufficient (no `<op_type>` segment)
  because op uuids are globally unique.
- `GET /clusteroperations?target_object_type=<type>&target_uuid=<uuid>` —
  returns all ops that targeted the given object. Namespace filtering is
  applied at the SQL layer (via a JOIN on `cluster_operation_targets`
  against namespace-carrying static-values tables) so large result sets
  are never materialised in Python.

**`redirect_to_network_node` status.** The `@redirect_to_network_node`
decorator (which proxies HTTP requests from the receiving API server to the
network node's gunicorn) has been removed from three of its four historical
call sites: `InterfaceEndpoint.get` (synchronous DB read — no proxy needed),
and the two network delete endpoints (now 202+poll, dispatched via the
queue). The decorator remains on `NetworkPingEndpoint.get` because the ping
handler executes `ip netns exec <network_uuid> ping` directly and the
network namespace exists only on the elected network node. Migrating the
ping endpoint to be queue-based requires new op-output infrastructure
(today the queue carries only error reports, not command output) and is
deferred to future work. The decorator definition in
`shakenfist/external_api/base.py` is retained for this one remaining use.

**Client-python.** `delete_network` and `delete_all_networks` in
`apiclient.py` (sibling `client-python` repo) handle the 202 response
transparently by default: they detect 202, extract the op UUID, and poll
`GET /clusteroperations/<op_type>/<op_uuid>` until the op reaches a
terminal state, raising `ClusterOperationFailed` on error. Advanced callers
can opt out of polling with `wait=False` to receive the op handle directly.
Two client methods `get_cluster_operation_chain` and
`list_cluster_operations_for_target` expose the discovery endpoints.

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
