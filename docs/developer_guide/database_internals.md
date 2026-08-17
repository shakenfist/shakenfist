# Database internals

How Shaken Fist uses MariaDB beyond the deployment and schema concerns
covered in the [operator guide](../operator_guide/database.md): the
static object value cache, filter-pushdown discipline, gRPC reliability,
and the cluster operation tracking and work-queue machinery.

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

The `sf-database` box in the diagram represents a tier of N >= 1 replicas.
All replicas connect to the same MariaDB; none is elected. Every other SF
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
See [`database.md`](../operator_guide/database.md) —
"MARIADB_HOST vs MARIADB_GATEWAY_HOSTS" — for the operator-facing detail.

Schema management (`ensure_schema()` in `mariadb.py`, run via `sf-ctl
ensure-mariadb-schema`) is version-gated per table, plus one un-gated
pass: native MariaDB `ENUM` columns are reconciled against their Python
enums on every run, because a MariaDB `ENUM` freezes its value list at
`CREATE TABLE` time and a new Python enum member changes no table
version. Without this pass, an upgrade adding an enum member (e.g.
`ObjectType.NAMESPACE_KEY`) works on fresh installs but breaks existing
databases with "Data truncated for column" errors. Enum columns are
discovered from the SQLAlchemy metadata, so new `sa.Enum(...)` columns
are covered automatically; the live upgrade path is exercised by the
"Schema ENUM widening" CI job (`tools/ci-enum-widening-test.sh`).

## Static object value cache

The database client in `mariadb.py` carries a small read-through cache for
immutable static object values, restoring the etcd-era principle "objects are
cacheable, attributes are not". Each public `get_<type>()` returns a frozen
Pydantic model of static columns only (mutable fields — states, metadata,
attributes, IPAM, daemon states — come from separate `get_<type>_attributes()`
readers and are never cached). The cache sits above the direct/gRPC branch, so
it serves both compute nodes (avoiding a gRPC round trip) and the `sf-database`
daemon's own worker threads (avoiding a SQL query); it is a single
process-global dict keyed `(object_type, uuid)` under a lock.

Correctness rests on three rules: only present rows are cached (never a miss,
so a create-after-lookup or delete-then-lookup is never masked); every public
`update_<type>`/`delete_<type>` evicts, and because the lazy online-upgrade
persist routes through the public `update_<type>`, the cache self-heals after
an upgrade; and every entry is TTL-bounded, which is the only bound on
staleness from a write made by another process. Two tiers set the TTL —
`OBJECT_CACHE_TTL_IMMUTABLE` (default 300 s) for types with no post-creation
writer (instance, network, networkinterface, agentoperation) and
`OBJECT_CACHE_TTL_MUTABLE` (default 30 s) for the upgradeable types (node,
blob, artifact, upload, dnsmasq, namespace). Setting either to 0 disables that
tier. Effectiveness is visible in the `database_object_cache_{hits,misses,
evictions}_total` counters and in reduced `database_get_<type>_total` rates.

## SQL Filter-Pushdown Discipline

Object iteration uses one indexed SQL query per call rather than the older pattern of materialising all rows
and filtering them in Python. Every `find_artifacts`, `find_instances`, `find_networks`, and
`find_network_interfaces` call in `shakenfist/mariadb.py` JOINs the per-type static-values table to
`object_states` and applies the caller's state, namespace, name and FK predicates directly in the WHERE
clause. The two FK fields (`network_uuid`, `instance_uuid`) on `ObjectFilterCriteria` are honoured only
by `find_network_interfaces`, which is what makes `Network.networkinterfaces` and `Instance.interfaces`
query-backed properties returning hydrated `NetworkInterface` objects rather than the cached UUID lists
they used to be.

The composite index `idx_object_states_type_state` on `(object_type, state_value)` covers the JOIN condition
that is present in every query. Per-type `name` and `namespace` single-column indexes on the artifact,
instance, and network tables cover the optional equality predicates. This keeps the common REST-layer calls
— list-by-namespace, list-active, lookup-by-name — to an index scan with no full-table read.

Filter criteria are expressed as `ObjectFilterCriteria` in
`shakenfist/schema/object_filter.py`. The iterator base class
(`DatabaseBackedObjectIterator` in `baseobject.py`) builds criteria from its constructor arguments and
delegates to the appropriate `find_*` primitive, so callers such as `Artifacts(namespace=ns,
prefilter='active')` get SQL pushdown without any extra work at the call site. Filters that have no SQL
equivalent (e.g. predicates over lazily-loaded attribute columns) remain as Python callables passed through
the `filters=` argument and execute after the indexed scan.

See [`database.md`](../operator_guide/database.md) — "SQL Filter Pushdown" — for
per-API guidance and a code example.

## gRPC Reliability

All gRPC calls use `timeout=30` seconds. The `_grpc_call()` helper in
`mariadb.py` enforces this for all database service calls and retries up to
3 times on UNAVAILABLE/DEADLINE_EXCEEDED errors with channel reset between
attempts (`wait_for_ready` is deliberately left at the default of False so
a wedged subchannel fails fast into this retry path instead of parking the
caller). Once those retries are exhausted, `_grpc_call()` raises
`shakenfist.exceptions.DatabaseUnavailable` rather than the underlying
RpcError. The client wrappers in `mariadb.py` translate non-retryable
RpcErrors into "object not found" return values (`None`/`False`/`[]`), but
`DatabaseUnavailable` is deliberately not an RpcError subclass and
propagates through them: an unreachable database must not be
indistinguishable from a missing object. The few hot paths that
intentionally tolerate an unreachable database catch it explicitly --
`Daemon.check_daemon_state()` skips the check, `ClusterLock.__enter__`
keeps retrying inside the caller's timeout, and the queues daemon's health
loop treats it as unhealthy and waits.

The database gRPC channel uses HTTP/2 keepalive (ping every 10s, 5s
timeout) to detect stale connections before they cause failures. The
database gRPC server uses a 20-thread pool to handle concurrent requests
from all daemons. The database client in `mariadb.py` (`_grpc_call`)
retries `UNAVAILABLE` and `DEADLINE_EXCEEDED` failures, rebuilding the
channel on a wedged subchannel but keeping it on a refused connection so
`round_robin` can serve the retry from a surviving gateway. All gRPC
failures are logged at ERROR level.

`get_objects_by_state()` returns `None` on non-retryable errors (distinct
from `[]` for no matches). All object iterators handle this by falling back
to unfiltered scans, ensuring that such failures do not silently drop
objects from iteration results (e.g. interfaces during instance deletion).
A database outage instead raises `DatabaseUnavailable` out of the iterator.

Object iterators read from MariaDB via `get_all_*()` functions. All
object static values now live in MariaDB.

## Cluster Operation Tracking

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

## Cluster Operation Storage and Work Queues

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

The cluster daemon also runs two other reaping sweeps from
`daemons/cluster/scheduled_tasks.py`. `per_deleted_object_checks()`
(every 15 minutes) hard deletes objects that have been in a final
state (`deleted`, `complete`, `abort`) for longer than their grace
period; its work queue holds `(object_type, uuid)` tuples fetched
with the age filter pushed down to SQL, and objects are hydrated one
at a time at processing time inside a per-item exception guard.
`reconcile_orphaned_objects()` (hourly) removes "phantom"
`object_states` rows whose static-values row is gone (with an age
guard so mid-creation objects are never raced) and repairs "zombie"
static rows that have no state row by writing a `deleted` state row
once the zombie has been seen on two consecutive sweeps; node and
namespace objects are excluded from zombie repair. Both kinds of
orphan are otherwise invisible to every state-driven iterator.

The elected cluster node also runs
`reconcile_scheduler_capacity()` every five minutes. One pass is a single
`ReconcileSchedulerCapacity` RPC which expires stale namespace
claims, re-derives per-hypervisor limits from the typed
`node_metrics` columns, recomputes usage counters from placed
instances and the decaying expected-demand signal, and rebuilds
the `cluster_capacity` singleton. The reconciler recomputes the
three capacity tables (`scheduler_node_capacity`,
`namespace_claims`, `cluster_capacity`) wholesale; as of
scheduler-reservations phase 3 the atomic admission and release
RPCs also write them incrementally, and are the sole *drawdown*
path against them, so a divergence between what the reconciler
computes and what the counters hold is drift, healed on the next
pass rather than expected steady state. Phase 4 added the claim
CRUD RPCs as a third writer: they move capacity between
`namespace_claims` and `cluster_capacity` (a claim's limits into
`claimed_*`, its namespace's existing drawdown out of
`unclaimed_used_*` and onto the claim, and the reverse on
deletion) but consume none, so the same drift-healing property
holds. See
[subsystem internals](subsystem_internals.md#the-claim-admission-transaction).
Observability is the
`scheduler_capacity_*` gauges and counters exported on
`CLUSTER_METRICS_PORT`, plus one structured log line per pass.

## Batched, Priority-Aware Dequeue

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

## Coalescible Operations

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
  branch drops it cleanly. A `'coalesced sibling ops'` event
  on the survivor records the folded uuids.

The enqueue-side dedup is the cheaper of the two -- the row never
gets inserted -- but the worker-side fold is the safety net for the
race where two concurrent callers both lose the lookup.
