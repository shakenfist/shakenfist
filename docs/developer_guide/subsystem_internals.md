# Subsystem internals

Developer-facing notes on how particular subsystems behave, complementing
the operator-facing pages they link to.

## Scheduler and node capacity metrics

Atomic reservation-table scheduling is being built per
[PLAN-scheduler-reservations](../plans/PLAN-scheduler-reservations.md).
The capacity tables, their reconciler, the guarded-UPDATE admission
path that draws them down and the namespace claims API have all landed;
hard claim ceilings, caller migration and further SQL pushdown are still
to come.

The scheduler ranks hypervisors by load per schedulable thread and
admits against reservation-adjusted capacity. The reservation
arithmetic lives in `shakenfist/daemons/resources/main.py`
(`_compute_reservations()`, `_get_hybrid_core_counts()`), which
publishes `cpu_cores`, `cpu_threads`, `cpu_cores_reserved`,
`cpu_schedulable`, `cpu_cores_schedulable`, `memory_reserved_mb`,
`disk_reservation_gb` (and `cpu_cores_performance` /
`cpu_cores_efficiency` on hybrid CPUs) into `node_metrics`. On the
consuming side, `Scheduler._schedulable_threads()` and
`Scheduler._memory_reserved_mb()` in `shakenfist/scheduler.py`
apply per-node fallbacks for metrics rows written by older
resources daemons (the CPU fallback subtracts this node's own
`NODE_CPU_RESERVATION_THREADS`, with no infra-role bump, so
un-upgraded nodes don't look artificially large) — admission,
ordering and `summarize_resources()` all go through these helpers,
so keep them in sync if you touch capacity arithmetic.
A third participant mirrors the same arithmetic: the scheduler
capacity reconciler's limit-derivation helpers in
`shakenfist/mariadb.py` (`_derive_cpu_memory_limits()`,
`_derive_disk_limit_gb()`) deliberately reproduce `scheduler.py`'s
admission *limits*, so a counter-based guard would bound capacity the
same way today's Python filter does — a change to one must change
both. The mirroring covers the arithmetic but not the inputs:
`_schedulable_threads()` and `_memory_reserved_mb()` substitute
per-node fallbacks for a metrics row that predates the typed capacity
columns, where the reconciler derives None and prefers to write no
capacity row at all rather than a guessed one. So mid-upgrade a node
can be schedulable while having no capacity row — the conservative
direction, but the two are not interchangeable while an upgrade is in
flight. The *usage* side is deliberately not a mirror: the reconciler's
`used_cpus` and `used_memory_mb` are allocation ledgers over every
placed, non-deleted instance, whereas the resources daemon's
`cpu_total_instance_vcpus` and `memory_total_instance_actual` count
only active libvirt domains. A powered-off instance holds its
reservation in the ledger and is absent from the measurement, so the
two legitimately disagree, and any counter-based admission has to
choose between them explicitly rather than assume parity. Phase 3 chose
the ledger. Admission is the guarded UPDATE the
`AdmitInstancePlacement` RPC makes against `scheduler_node_capacity`,
in the same transaction that writes the `placement` attribute and
rewrites the instance's `INSTANCE_LOCATION` reference rows — so a
placement cannot be recorded without the capacity it consumes, two
concurrent creates racing one remaining slot admit exactly once, and
duplicate placement rows cannot be produced.
`Instance.place_instance()` is the sole caller. A refusal raises
`exceptions.CapacityAdmissionDenied`, and every scheduler-driven
caller answers it by walking to its next candidate (the create path in
`external_api/instance.py` and the preflight redirect in
`operations/node_inst_netdesc_op.py`); an exhausted candidate list is
the ordinary "cluster full" outcome (507 on create,
`LowResourceException` in preflight). Ground-truth writers — the
cleaner's placement rewrites and the queues daemon's reference
reconciliation — pass `enforce=False`: they record where a libvirt
domain already is, which a guard cannot refuse.

The node stage carries a fifth clause beside the three allocation
dimensions: D13's demand feedforward, `_demand_guard_clause()` in
`mariadb.py`. It compares the node's *existing* state — `cpu_load_1 +
expected_demand <= SCHEDULER_TARGET_LOAD × cpu_schedulable`, with both
measured inputs read from the typed `node_metrics` columns inside the
transaction — and deliberately does not include the incoming
placement's charge, though the same `UPDATE` adds that charge to
`expected_demand` so it counts against the next decision.

That asymmetry is the fix for issue #3813 and is load bearing. Adding a
per-request term to a per-node budget made the clause unsatisfiable
below a threshold set by the instance size: at the original constants a
1-vCPU instance needed 3.34 schedulable threads, so nodes below four
threads admitted nothing whatever their real headroom. Comparing node
state alone is the only form with no such threshold. Check-then-charge
is safe because the comparison and the increment are one guarded
`UPDATE` in one transaction, so concurrent admissions against a node
serialise and the second sees the first's charge.

Two consequences to keep in mind when touching this code. The clause is
a spreader and never a bound — the three allocation dimensions are what
stop a node taking work it has no room for — and callers may therefore
waive it (decision P9: `enforce_demand=False` sends `target_load` as
zero, and both walkers re-walk with it waived when every candidate was
refused on demand alone). And `_capacity_dimension()` must build the
demand dimension with `charged=False`, so the reported `exceeded` flag
is the test the guard actually made:
`CapacityAdmissionDenied.demand_only` is derived from the set of
exceeded dimensions, so a demand dimension that reports `used +
requested > limit` would make denials look waivable that the clause
never made. The demand dimension additionally carries `cpu_load_1` and
`expected_demand` keys whose sum is `used` (issue #3913): measured load
and the feedforward estimate mean different things, and which one made
the node exceed its target is what tells a correct refusal from an
estimator defect. The proto fields are `optional` so a reply from an
older `sf-database` reads as "no breakdown available" rather than a
breakdown of zeroes; the three allocation dimensions never carry the
keys.

There used to be a second ledger here: `Scheduler._committed_vcpus()`,
a Python walk over each candidate's `INSTANCE_LOCATION` rows added as
a stopgap for the CPU stage (issue 3498). It was deleted by the same
change that added the guard, because admission consulting two ledgers
is exactly how they come to disagree. The in-Python filters in
`Scheduler.find_candidates()` remain cheap pre-filters that prune and
order the candidate list so the guard misses less often — they are not
the decision, and they are allowed to be up to a minute stale — but
the CPU one is denominated in both ledgers, charging `max(measured,
used_cpus)` with `used_cpus` read from `scheduler_node_capacity`,
because a node whose ledger is full measures as idle for as long as
its instances spend fetching images. A node with no capacity row is
charged nothing, matching the admission transaction's own fail-open on
an unsized node. `summarize_resources()` charges the same way, from the
same counters, so `/admin/resources` is not a second, independently
derived ledger. The two differ on the limit they measure that charge
against: the pre-filter uses the capacity row's `limit_cpus`, because
that is what the guard behind it will use, while `/admin/resources`
derives the limit live from the node's metrics. The arithmetic is the
same (`mariadb._derive_cpu_memory_limits()`), but the row refreshes
only once a reconcile period, so the published headroom can differ from
what admission would grant for up to that long. Both inputs to the
charge are published (`cpu_measured`, `cpu_committed`, and
`cpu_committed_row_present` to say whether a zero means "unsized" or
"idle"), so which of the two binds is answerable from the response.
The reconciler maintains the
`scheduler_node_capacity`, `namespace_claims` and `cluster_capacity`
tables from the elected cluster node every five minutes. Rows exist
per *schedulable hypervisor*, not per node, because a row that
describes capacity the scheduler would never use is worse than no row
at all: it inflates the cluster totals. Four filters enforce that,
each mirroring something `scheduler.py` already does — the
`is_hypervisor` column projected into `node_metrics` (sf-resources
publishes metrics from every node whatever its roles), node state
against `constants.NODE_ACTIVE_STATES` (the scheduler builds its
candidates from `Nodes([], prefilter='active')`; the filter is
expressed positively, as membership in the active set, so a node with
no state row at all is excluded exactly as `get_objects_by_state`
would exclude it), metrics freshness against
`RECONCILE_METRICS_MAX_AGE_SECONDS` (the scheduler discards metrics
older than 120s; the reconciler's window is much wider because its
cadence is), and existence in the `nodes` table. The
`cluster_capacity` singleton is a closed accounting over the nodes
that pass those filters: usage on a node without a capacity row counts
toward neither the total nor the unclaimed-used side (a claim's
`used_*` stays namespace-wide — a quota covers a namespace's instances
wherever they are stranded). If you add a capacity consumer, it
inherits these filters by reading the tables — do not re-derive
capacity from `node_metrics` directly. The tables are consumed for
admission as of phase 3
(`docs/plans/PLAN-scheduler-reservations-phase-03-primitive.md`), and
`namespace_claims` became writable in phase 4 — see [The claim admission
transaction](#the-claim-admission-transaction) below.
The SQL itself is covered by
`shakenfist/tests/test_mariadb_capacity_reconcile_live.py`, which runs
against a real MariaDB in the "Schema ENUM widening" CI job (whose
script runs every `test_mariadb_*_live` module behind one MariaDB
install); the mocked unit tests cannot catch a broken uuid join, a
JSON_TABLE change, or an enum binding that names the wrong storage
convention, because all of those fail as silently wrong numbers rather
than errors. The live suite runs under `utf8mb4_bin` for that last
reason: `object_states.object_type` is a native `sa.Enum` persisting
member *names* while the `object_references` type and relationship
columns store member *values* (written as `str(member)`), and only a
case-sensitive collation makes a binding that confuses the two fail.
Operator-facing documentation is
[`scheduler.md`](../operator_guide/scheduler.md).

### The claim admission transaction

Phase 4 made `namespace_claims` writable. A claim is a namespace's
promise of aggregate cluster capacity, so creating, growing or shrinking
one is an admission decision in its own right — against the
`cluster_capacity` singleton rather than against a node — and the five
CRUD RPCs (`CreateNamespaceClaim`, `GetNamespaceClaim`,
`GetNamespaceClaims`, `UpdateNamespaceClaim`, `DeleteNamespaceClaim`)
are built like `AdmitInstancePlacement` rather than like ordinary object
persistence. Everything the placement transaction has to obey applies
unchanged: probes on their own connection *outside* the transaction, a
guarded `UPDATE` as the transaction's first statement (the ER_CHECKREAD
invariant, [standards.md](standards.md#a-guarded-update-must-be-the-transactions-first-statement)),
the canonical write order `cluster_capacity` then `namespace_claims`
then `scheduler_node_capacity`, floored decrements, and a retry on
1213/1205/1020. A claim mutation touches the first two of those tables,
in that order, so it composes with instance admission without a new
deadlock class.

Creation and growth are guarded per dimension by

    claimed + limit + GREATEST(0, unclaimed_used - migrated) <= total

where `migrated` is the namespace's existing drawdown being moved onto
the claim, and rowcount zero is the refusal. The migration is the point
of the third term: a namespace's instances are counted in
`cluster_capacity.unclaimed_used_*` until it holds a claim, so
`_direct_create_namespace_claim()` seeds the new row's `used_*` with
that drawdown and takes the same amounts off `unclaimed_used_*` in the
statement the guard guards. Testing the state the statement *started*
from would count a namespace's usage against it on the unclaimed side
while the same statement is taking it off, and would refuse an operator
claiming capacity their namespace already holds — which is the primary
use case. A grow passes zero for `migrated` and must keep passing zero:
its usage is already on the claim's side of the ledger. `GREATEST(0,
...)` mirrors the flooring in the `SET`, because a guard that floored
differently from the write it guards would test a state that write
cannot reach.

The drawdown probe shares one SQL fragment with the reconciler's
per-claim usage recompute rather than being written twice. Two queries
that can disagree would make a new claim's counters flap on every
reconcile pass, forever; a create-then-reconcile test pins that they
cannot. The probe is time-of-check-to-time-of-use racy against a
concurrent create in the same namespace, by at most one instance's
allocation, in one direction, corrected by the next pass — the same
bargain the branch probe strikes.

Two decisions deliberately diverge from placement admission:

- **A missing `cluster_capacity` singleton refuses**, where placement
  fails open. Admitting an instance unguarded records where a machine
  already is and the reconciler agrees within a pass; creating a claim
  unguarded writes a promise against totals nothing has computed, which
  the next pass folds into `claimed_*` whether it fits or not.
- **Claim ceilings are advisory** for one release.
  `_direct_admit_instance_placement()` splits phase 3's single
  `guarded = enforce and node_present` into `node_guarded`,
  `cluster_guarded` (both unchanged) and `claim_guarded = enforce and
  CLAIM_ENFORCEMENT_HARD`, a module constant set `False`. The node and
  cluster fail-open is a statement about *this node's* limits being
  absent from the totals those guards test; a claim's limits are
  namespace-denominated and node-independent, so a missing node capacity
  row says nothing about whether a namespace has exceeded what it
  claimed. Over-limit is detected by re-reading the claim row inside the
  transaction that just wrote it — a read after our own write, on a row
  we hold the lock on, which the invariant permits — and returned in
  `claim_over_limit` / `claim_dimensions`, deliberately separate from
  `failing_stage` and `dimensions`, which both mean "this was refused".
  `Instance._event_claim_over_limit()` turns them into the audit event.
  Read-back rather than the probe-then-force idiom beside it because
  probe-then-force would make every create in a claimed namespace pay a
  probe round trip, and the namespaces that want claims are the ones
  creating instances hardest; it also leaves phase 5's flip as moving an
  existing predicate into a `WHERE` clause.

`NamespaceClaim` (`shakenfist/namespace_claim.py`) is an ordinary
`DatabaseBackedObject` over these rows, with two states that are two
different facts: `state` is existence in `object_states`, like every
other object, and `coverage_state` (`active` or `expired`) lives in the
`namespace_claims` row and is owned by the reconciler's expiry sweep.
Coverage is not routed through `object_states` because
`_active_claim_for_namespace()` runs on every instance admission and
filters `state = 'active' AND expires_at > NOW()` against the claims
table's own index; moving that predicate would make the hot probe join
across the two uuid storage conventions (dashed `String(36)` in
`object_states`, undashed `sa.Uuid` here). There is no soft delete: the
transaction that removes the row is the one that returns its capacity,
so `Namespace.hard_delete()` cascades to claims for the same reason it
cascades to keys and rules — a claim outliving its namespace holds
cluster capacity nothing can release.

A new object type has to join more registries than is obvious. Both
`mariadb._STATIC_TABLE_GETTERS` and `constants.OBJECT_NAMES_TO_CLASSES`
are load bearing for the orphan reconciler: zombie repair marks the
static row deleted and then hydrates it through `get_object_class()` to
collect it, so registering only the first trades one leak for another.
`NAMESPACE_KEY` missing from the first is issue 3588, and the same
defect is still live for `TRUSTED_ISSUER` and `MAPPING_RULE`.

## Node resource health

Node storage health drives `node.state`, on a different axis from the
daemon-liveness watchdog below. `shakenfist/resource_health.py` is the
reusable, timeout-guarded path-check primitive (a hung `hard`-NFS mount
blocks rather than erroring, so the deadline is the unhealthy signal).
`shakenfist/node_health.py` maps a node's role to the object types it
hosts, runs each type's declared `health_dependencies` paths, and marks
the node `STATE_ERROR` via a `health` event (`EVENT_TYPE_HEALTH`, a
channel separate from the audit log) carrying the affected object
types. **A new object type that lives on disk must declare its
`health_dependencies`**, or a node whose storage for it has failed will
keep being scheduled onto.

The declarations today are `Instance` → `instances`, `image_cache`,
`blobs`; `Blob` → `blobs`; `Upload` → `uploads`. `sf-resources` probes
the union of the types this node hosts, on its own thread rather than
the metrics loop, so a blocking mount trips the probe's timeout instead
of stalling the daemon. A failure moves the node to `error`, which
stops scheduling onto it and discounts its blob replicas; `sf-cluster`
then reads the affected types back
(`node_health.errored_node_affected_types`) and cascades from a
surviving node — erroring the node's instances and re-replicating its
blobs, gated on which object type was affected. This mirrors the
deleted-node path but errors rather than deletes.

Node error never clears automatically; `sf-ctl clear-node-error` is the
operator recovery path, documented in
[`node_health.md`](../operator_guide/node_health.md).

## Daemon liveness (systemd watchdog)

`Daemon.pet_watchdog()` in `shakenfist/daemons/daemon.py` is the
liveness seam for every non-trivial daemon. It writes
`sd_notify(WATCHDOG=1)` at most every ~10s, and `Daemon.idle()` (the
standard end-of-pass sleep) calls it automatically — so any daemon
whose main loop reaches `idle()` at the end of each pass pets the
watchdog with no extra instrumentation.

**Any daemon loop that performs a long pass without going through
`idle()` must call `pet_watchdog()` explicitly** — otherwise systemd
will kill the process once `WatchdogSec` elapses, even though the
daemon is working normally. The existing explicit callers are:

- the `sf-cluster` elected loop, which sleeps on
  `lock.lost_event.wait(5)` rather than `idle()` and so pets at the top
  of each iteration;
- `sf-cluster`'s `_cluster_wide_cleanup`, and `sf-cleaner`'s
  `update_power_states`, `_maintain_blobs` and `_find_missing_blobs`,
  which pet around inner-loop iterations that may each take several
  seconds. `update_power_states` runs as a scheduled task outside the
  cleaner's `idle()` loop, so it is petted per libvirt domain.

If you add a new long-running maintenance pass to any of the eight
armed daemons — `sf-database`, `sf-net`, `sf-cleaner`, `sf-cluster`,
`sf-queues`, `sf-resources`, `sf-transfers`, `sf-sidechannel` — add
`self.pet_watchdog()` calls inside its inner loop. Arming is configured
in `sf.service` at `WatchdogSec=60s`, except `sf-cluster` and
`sf-cleaner` at `300s`, whose maintenance passes legitimately run
longer. Four units are deliberately excluded: `sentinel-first`,
`sentinel-last`, `sf-privexec` and `sf-nodelock` are short-lived or
event-driven and never run the `idle()`-based keepalive loop, so arming
them would kill a healthy process that is simply waiting for its
trigger. `sf-api` is excluded too — gunicorn has its own `--timeout`
worker-liveness mechanism.

When a daemon stops petting, systemd delivers SIGABRT and restarts it
(`Restart=on-failure`). For the elected `sf-cluster` that doubles as
the cluster-lock failover trigger: the killed process takes its
in-process lease refresher with it, the `cluster/` lease lapses after
60s, and a standby steals the lock via `UPDATE ... WHERE expires_at <
NOW()`. Worst-case failover is about 360s (300s watchdog + 60s lease),
with no operator intervention. See
[`locks.md`](../operator_guide/locks.md) for the lease-expiry and
lock-steal protocol.

The watchdog tracks the **main (supervisor) loop only**. In the
`WorkerPoolDaemon`-style daemons (net, queues, resources, transfers,
sidechannel, database) the real work happens in spawned worker or gRPC
threads while the main loop dispatches and pets via `idle()`, so a
wedged *worker* under a healthy main loop keeps petting. `WATCHDOG`
detects a stuck supervisor loop, not a stuck worker; deeper per-worker
liveness (for example "is dnsmasq actually serving DHCP", issue #730)
is explicitly future work. Do not over-trust it as a signal that every
worker is healthy.

## sf-api health surface

`sf-api` exposes three unauthenticated HTTP endpoints on port 13000 for
load balancer probing. It is the only load-balancer-routable surface in
the cluster — every other daemon communicates internally over gRPC or
the MariaDB-backed work queue.

| Endpoint | Purpose |
|----------|---------|
| `GET /livez` | Liveness — always returns `200 ok`; the worker process is alive |
| `GET /readyz` | Readiness — `200 ready` when the worker can serve traffic, `503 not ready` when draining or when sf-database is unreachable |
| `GET /healthz` | Alias of `/readyz` |

`shakenfist/external_api/health.py` is the per-worker readiness
module. Each gunicorn worker runs a background checker thread (started
by the `post_fork` hook in `gunicorn_config.py`) that polls
sf-database's `grpc.health.v1.Health/Check` every 5 seconds and caches
the result, so `/readyz` and `/healthz` answer from `health.is_ready()`
in microseconds without an RPC on the request path. The cached flag
flips to False only after three consecutive failures
(`READINESS_FAIL_THRESHOLD`), debouncing transient blips, and a
staleness guard means a wedged checker is itself treated as not-ready.

The `post_worker_init` hook installs a SIGTERM handler that calls
`health.begin_drain()` — a one-way latch flipping `/readyz` to 503
immediately — and then keeps serving live requests for
`API_DRAIN_GRACE` seconds (default 25) before the normal worker
shutdown, giving the load balancer time to stop routing new
connections before the process exits.

Operator-facing probe guidance is in
[`load_balancing.md`](../operator_guide/load_balancing.md).

## VDI console token mint path

Shaken Fist mints short lived Ed25519 JWTs for the Kerbside VDI console
proxy. `shakenfist/external_api/instance.py`
(`InstanceVDIProxyConsoleHelperEndpoint`, `GET
/instances/<ref>/vdiconsoleproxy`) mints a token and returns a proxy URL;
`shakenfist/util/vdi_tokens.py` owns all key handling (mint, ensure, rotate,
public view); `shakenfist/external_api/admin.py`
(`AdminVDITokenPublicKeyEndpoint`, `GET /admin/vditokenpubkey`) publishes the
public verification keys. The signing key lives in a single `cluster_config`
row, `KERBSIDE_JWT_SIGNING_KEY` (two-key rotation window). The `sf-ctl`
`ensure-kerbside-signing-key` / `rotate-kerbside-signing-key` subcommands
bootstrap and rotate it. Operator runbook: the
[VDI console tokens operator guide](../operator_guide/vdi_console_tokens.md).

## Object References

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

### Relationship Types

| Type | Source | Target | Value |
|------|--------|--------|-------|
| `disk` | Instance | Blob | Disk index ("0", "1", ...) |
| `nvram_template` | Instance | Blob | NULL |
| `artifact_index` | Artifact | Blob | Index number ("000000000001") |
| `depends_on` | Blob | Blob | NULL |
| `transcode` | Blob | Blob | Style ("qcow2", "raw") |
| `agent_output` | AgentOperation | Blob | Output type ("stdout", "stderr") |
| `blob_location` | Node | Blob | NULL (blob is present on this node) |
| `instance_location` | Node | Instance | NULL (instance is placed on this node) |

This replaces the legacy `ref_count` and `locations` blob attributes with a
queryable, auditable reference system. Blob reference counts are computed
dynamically from this table via `mariadb.count_references_to()`. Blob locations
are queried via `mariadb.get_references_to()` filtered by `BLOB_LOCATION`.

`INSTANCE_LOCATION` references similarly replace the legacy `instances`
JSON list on `node_attributes`: the list was maintained by read-modify-write
of the whole attributes row, so concurrent full-row writers (for example the
sentinels' 15-second `observe_this_node()` heartbeat) could silently revert
a placement. References are single-row inserts and deletes, needing no
cross-writer coordination. `Node.instances` queries them via
`mariadb.get_references_from()` filtered by `INSTANCE_LOCATION`. Unlike
`BLOB_LOCATION`, these rows key the node by UUID, not FQDN. The dual-write and the
read-side union were removed in scheduler-reservations phase 3, once every
placement writer had moved onto the atomic admission primitive; the column
itself remains declared in the table (nullable, no longer read or written)
so upgraded databases keep a rollback fallback, and is dropped in a later
release — the same treatment as the legacy `daemon_states` column.

Placement rows are written only by two `sf-database` RPCs,
`AdmitInstancePlacement` and `ReleaseInstancePlacement`, each performing its
guarded capacity-counter update, the `placement` attribute write and the
`INSTANCE_LOCATION` row rewrite in a single database transaction, so a
placement can never be recorded without the capacity it consumes.
`Instance.place_instance()` is the sole caller; `Node` carries no
placement-writing methods.

The `last_active` column is updated whenever a reference is observed to still
be valid (e.g., when a node's cleaner daemon calls `observe()` on local blobs).
This enables detection of stale references for cleanup.

## REST API surface

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

**VDI console proxy endpoints.** `GET /instances/<ref>/vdiconsoleproxy`
(`external_api/instance.py`, `InstanceVDIProxyConsoleHelperEndpoint`) mints a
short lived Ed25519 JWT and returns `{url, expires_at}` where `url` is
`<KERBSIDE_URL>/sf-console.vv?token=<jwt>`. It returns 404 when the Kerbside
integration is unconfigured, 406 unless the instance is `created`, 409 unless
the console is SPICE, and 500 when no signing key exists. `GET
/admin/vditokenpubkey` (`external_api/admin.py`,
`AdminVDITokenPublicKeyEndpoint`) publishes the public verification keys. See
the VDI console token trust model in [security_model.md](security_model.md).
