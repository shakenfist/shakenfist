# Scheduler reservations phase 0: current-state findings

This document is the output of step 1 of
`PLAN-scheduler-reservations-phase-00-decisions.md` — the
research pass over the Shaken Fist server, the CI conductor,
and external prior art. It is input material for the decisions
in that plan, not a decisions document itself. Findings were
produced by high-effort research sub-agents on 2026-07-30 and
are grounded in file:line references to the code as of that
date.

## Part 1: Shaken Fist server current state

Worktree at `817051585` (develop plus the plan seed). All
line numbers from that tree.

### Scheduler today (post phase 00a)

`shakenfist/scheduler.py` has exactly two public entry points
and **no `place_instance`** — that name is
`Instance.place_instance()` (`shakenfist/instance.py:912`), a
separate persistence step callers invoke after the scheduler
returns. `Scheduler.find_candidates(inst, candidates=None)`
(`scheduler.py:277-557`) returns an ordered list of node
UUIDs; callers take `[0]`. `summarize_resources()`
(`:559-638`) feeds `GET /admin/resources`.

`Scheduler.__init__` (`:84-94`) calls `refresh_metrics()`
eagerly; metrics re-refresh inside `find_candidates` when
older than `SCHEDULER_CACHE_TIMEOUT` (5s, `config.py:180`).
Candidate set = nodes in `Node.ACTIVE_STATES` with a metrics
row younger than 120s (`scheduler.py:56-58`).

Admission checks, in execution order — all Python loops over
`self.metrics[node]`, each followed by
`_log_and_raise_on_error` (`:259-275`) raising
`LowResourceException` when the candidate list empties:

| Stage | Code | Fields consulted |
|---|---|---|
| `is_hypervisor` | `:337-343` | `is_hypervisor` |
| `queue_state` | `:100-112` | `node_queue_waiting` vs 20 |
| `cpu_max_per_instance` | `:356-368` | `cpu_max_per_instance` |
| `sufficient_idle_cpu` | `:148-166` | `cpu_schedulable` × `CPU_OVERCOMMIT_RATIO` vs `cpu_total_instance_vcpus` |
| `sufficient_idle_memory` | `:168-210` | `memory_available`, `memory_reserved_mb`, `memory_total_instance_actual`, `memory_max` |
| `sufficient_free_disk` | `:212-238` | `disk_free_instances` (bytes), `disk_reservation_gb` |
| `sufficient_idle_disk` | `:240-257` | `disk_busy_time_delta_per_sec` — **dead, see Surprises** |

`_schedulable_threads` (`:114-138`) prefers published
`cpu_schedulable`, falls back to local config for stale rows;
`_memory_reserved_mb` (`:140-146`) mirrors it. Defaults:
`SCHEDULER_TARGET_LOAD=0.75`, `CPU_OVERCOMMIT_RATIO=3.0`,
`RAM_OVERCOMMIT_RATIO=3.0`, `NODE_RAM_RESERVATION_GB=2.0`,
`NODE_CPU_RESERVATION_THREADS=2`,
`NODE_DISK_RESERVATION_GB=20.0`.

**Nothing in admission accounts for a pending instance.**
`cpu_total_instance_vcpus` / `memory_total_instance_actual`
come from libvirt's *active* domains
(`daemons/resources/main.py:315-327`), `memory_available` is
psutil. A just-placed instance is invisible until it boots
AND the target's next 60s metrics cycle publishes it. This is
the race the master plan targets.

**Affinity** (`:414-496`): `inst.affinity` is the `affinity`
metadata dict, validated at
`external_api/instance.py:1303-1312`. Per candidate:
`Node.from_db()`, `n.instances` (an `object_references` query
unioned with the legacy JSON column, `node.py:623-652`), then
`Instance.from_db()` per co-located instance — O(candidates ×
instances) DB round trips on the API hot path, unindexed.
Score = sum of weights for matching tags, then applied as a
**hard filter**: `candidates = by_affinity[max(scores)]`
(`:484-485`). Functional test
`cluster_ci_tests/test_scheduler.py:19-110` asserts hard
co-location for +100 and hard separation for -100 — a
behavioural contract the affinity rework must preserve or
explicitly migrate.

**Load ordering (00a)** (`:498-556`): bucket by
`floor((cpu_load_1 / schedulable_threads) / 0.25)`, keep
lowest bucket, Efraimidis-Spirakis weighted shuffle with
weight `max(0.1, TARGET_LOAD × threads − cpu_load_1)`. The
whole ordered list is returned.

**Audit events**: a successful decision emits 14 audit events
via spooled `add_event_multi` (enqueue, not DB write, on the
hot path): `started scheduling`, `schedule inputs` (with
metrics_age_seconds), initial candidates, one per stage with
`{candidates, dropped}`, affinity detail, load detail, final
candidates with weights. A failed decision emits
`schedule has no candidates at stage {stage}` then raises.
Today's per-rejection audit is the per-stage `dropped` dicts.

### Scheduler callers (exactly three)

1. **`external_api/instance.py:789-816`** — API hot path.
   Module-global `SCHEDULER` reused per gunicorn worker.
   Ordering: validation → `Instance.new()` (state
   `initial`) → metadata written (**affinity known before
   placement**) → IPs reserved → `find_candidates` →
   `inst.place_instance(placement)` → artifact ops →
   `node_inst_netdesc_op` enqueue. On
   `LowResourceException`: audit, `enqueue_delete_due_error`,
   HTTP **507**. No retry, no walking the candidate list.
2. **`external_api/admin.py:106-117`** — `GET
   /admin/resources`, admin-only, **fresh Scheduler per
   request** (full metrics fan-out), read-only.
   `cluster_ci_tests/test_nodes.py:40-70` asserts against it.
3. **`operations/node_inst_netdesc_op.py:141-204`** —
   `_instance_preflight` on the *target node's* queue worker.
   Sets state `preflight`, re-runs admission for this node
   only. The only retrying caller: on LowResource, if
   `placement_attempts > 3` → abort; if `requested_placement`
   → abort; else re-schedule excluding this node, re-place,
   mint a new op on the new node, abort this op. No
   requeue-on-LowResource path — redirect once or give up.

### node_metrics

Written by the resources daemon every **60s**
(`daemons/resources/main.py:606-616`, gate at `:659-661`).
Table (`mariadb.py:1493-1519`): `node_uuid PK, fqdn,
timestamp, metrics_json JSON` — one row per node,
deliberately schemaless (docstring `:1496-1503`: "SQL
queryability of individual fields is not needed"). Worst-case
snapshot age at a decision ≈ 65s (60s cadence + 5s cache);
`schedule inputs` records `metrics_age_seconds`.

Capacity-relevant fields: `cpu_max`, `cpu_max_per_instance`,
`cpu_cores`/`cpu_threads`, `cpu_load_1/5/15`,
`cpu_total_instance_vcpus`; reservations published via
`_compute_reservations()` (`:100-127`) — `cpu_schedulable =
max(1, cpu_threads - NODE_CPU_RESERVATION_THREADS)`,
`memory_reserved_mb = min(GB×1024, memory_total_mb // 2)`
(**half-machine cap**), `disk_reservation_gb` raw;
`memory_max`, `memory_available` (psutil MB), KSM fields,
`memory_total_instance_{max,actual}`;
`disk_free_{instances,...}` (bytes), busy-time counters and
deltas; role flags, queue depths.

### Instance create lifecycle

Sizing (`cpus`, `memory`, `disk_spec`) is frozen by
`Instance.new()` before `find_candidates` is called —
**everything a claim needs is known before scheduling**.
State machine (`instance.py:181-233`): `initial → preflight →
creating → created` plus `<state>-error` and
`delete_wait`/`deleted`. **There is no `building` state**
(see Surprises). Failure/cleanup paths after placement but
before running — each a required claim-release point:
API-side failures after `place_instance` (no try/except at
`external_api/instance.py:816-865`); preflight redirect
(release old node's claim, take new); preflight aborts
(attempts>3, requested-node, reschedule-failure);
`_instance_start` network failures; `_instance_create`
invalid-state/image-shrink; dispatcher catch-all; power-on
failure (`instance.py:976-977`); dependency abort; and the
cleaner **rewriting placement outside the scheduler**
(`daemons/cleaner/scheduled_tasks.py:121,218` call
`place_instance(config.NODE_UUID)` for locally-found
domains). `hard_delete()` (`instance.py:1044-1063`) is driven
by the cluster daemon's deleted-object sweep every 15
minutes; **a claims table needs a hook there**.

### cluster_locks leasing pattern (the template)

`CLUSTER_LOCK_LEASE_SECONDS = 60`; `expires_at` written
server-side as `NOW() + INTERVAL n SECOND` so clock skew
cannot enable steals (`mariadb.py:1755-1761`). Acquire
(`_direct_acquire_cluster_lock`, `:19698-19786`) is a
*single* `INSERT ... ON DUPLICATE KEY UPDATE` with per-column
`CASE WHEN expires_at < NOW()` steal semantics, followed by a
PK SELECT to disambiguate (rowcounts unreliable across
drivers); its docstring records the earlier two-statement
shape was a guaranteed S→X upgrade deadlock. Refresh: guarded
UPDATE, rowcount-as-CAS, 20s cadence (lease/3, two missable).
Release: DELETE-as-CAS. `_retry_on_deadlock` wraps all three
(4 attempts, jittered 5/10/20ms). `lost_event` protocol for
long holders. **No reaper at all** — recovery happens on the
next acquire. **v4 deliberately dropped the `expires_at`
index** because under RR it widened gap-lock footprints and
caused 1213 deadlocks (`:1853-1871`) — in direct tension with
a reap-by-expiry query (see Surprises).

### Adding a new table + RPC + object, end to end

Best precedents: `namespace_key` (full first-class object)
and `node_daemon_states` (JSON-to-table migration). The
checklist: (1) Pydantic schema in `shakenfist/schema/` with
`sql_indexes` config; (2) table registration in `mariadb.py`
under `TABLE_CREATION_LOCK` + `register_all_tables()`; (3)
version constant + `EXPECTED_SCHEMA_VERSIONS` + the
concurrency test's `EXPECTED_TABLE_NAMES`; (4)
`_ensure_*_schema()` appended to `ensure_schema()` (run only
via `sf-ctl ensure-mariadb-schema`); (5) three access layers
(`_direct_*`, `_grpc_*`, public router); (6)
`protos/database.proto` + `tox -e genprotos`; (7)
`DatabaseServicer` handlers + Monitor counters; (8) if
first-class: `ObjectType` member (fresh proto_id; latest is
`NAMESPACE_KEY = 29`), `OBJECT_NAMES_TO_CLASSES`, a `dbo`
subclass, REST endpoints in `external_api/app.py`, optional
cluster-daemon reap task (template:
`reap_expired_namespace_keys`,
`daemons/cluster/scheduled_tasks.py:345-380`); (9) enum
widening is automatic; (10) `verify_schema_versions` means
sf-database refuses to start on mismatch, so **no dual-read
window is ever needed** (argument made explicitly in
`PLAN-auth-federation-phase-02-key-objects.md:87-92`).

### Namespace lifecycle and authz

Namespaces use their **name** as identifier, not a UUID
(`namespace.py:49-66`). `state_targets = {None: created,
created: deleted}` — no error state. Create is admin-only
(`external_api/auth.py:181-248`). Soft delete
(`auth.py:263-310`) refuses `system`, refuses if any non-dead
instance or network remains — **a claim must be added to that
emptiness gate or torn down explicitly there**.
`hard_delete()` (`namespace.py:302-314`) is the cascade hook.
Authz: `caller_is_admin` vs `arg_is_namespace` +
`requires_namespace_ownership` (trusted parent namespaces can
already act on children). **There is no existing per-namespace
quota or limit object of any kind.**

### SQL pushdown precedent constraints

Typed Pydantic criteria (not predicate rewriting);
three-layer discipline mandatory; **errors return empty and
log, never raise** — a hazard for claims, where "did not fit"
and "database broke" must differ (see Surprises); indexes on
existing tables need explicit `CREATE INDEX IF NOT EXISTS`;
two UUID formats (dashed String(36) vs undashed CHAR(32) —
JOINs must cast, else silently never match, `CLAUDE.md`).
In-tree prior art: `_direct_claim_coalescible_siblings`
(`mariadb.py:19512-19624`) is a real `SELECT ... FOR UPDATE`
+ bulk UPDATE claim; `_direct_reserve_address` (`:5231-5268`)
claims via plain INSERT + `IntegrityError` = "taken" (unique
key, not aggregate capacity).

### Surprises (server side)

1. **There is no `building` state.** The master plan's
   consumption point ("reservation consumed at `building`")
   names a state that does not exist. Real machine: `initial
   → preflight → creating → created`. Nearest analogues:
   `preflight` (target node's queue worker) and `creating`
   (hypervisor, immediately before disk config). Phase 0 must
   pick one; they differ by a full network-reconcile op chain
   and an image fetch.
2. **`node_metrics` capacity data is an opaque JSON column by
   design** (`mariadb.py:1496-1503`). The master plan's
   `effective_capacity(node)` SQL cannot be written cleanly
   today — phase 1 must promote ~11 capacity fields to typed
   columns (or indexed generated columns). Confirms the
   prior-art finding independently.
3. **`_has_idle_disk_bandwidth` has never worked**: scheduler
   reads `disk_busy_time_delta_per_sec`, daemon publishes
   `disk_busy_time_delta_per_second`; `.get(..., '0')` makes
   it silently pass. The queue worker's `high_io` gate has a
   third spelling (`_seconds`, `daemons/daemon.py:650`) and
   is also dead. The unit test bakes the wrong key in
   (`tests/test_scheduler.py:178`). Two live bugs, neither in
   the plan's bug list.
4. **Affinity is a hard filter, not a soft preference**
   (`by_affinity[max]` discards all non-maximal buckets), and
   the functional test asserts strict semantics from ±100.
   Question 6's "soft preferences become ORDER BY terms"
   would weaken observable behaviour and break that test
   unless the binary model keeps `require_*` semantics for
   today's large weights.
5. **The affinity pass is the most expensive thing on the
   create path** (per-candidate per-instance object loads,
   unindexed, plus a legacy JSON union). Pushing affinity
   into SQL is a latency win independent of atomicity.
6. **cluster_locks v4 removed the `expires_at` index as a
   deadlock vector** — but a reservation reaper wants exactly
   that index. The lock table resolved the tension by having
   every hot path be a PK lookup and no reaper at all. The
   claims design must resolve it too (aligns with prior-art
   recommendation 8: explicit release primary, expiry as
   backstop — which keeps the reap query rare).
7. **The pushdown error convention conflates "no rows" with
   "database broke"** (`[]` for both). A claim primitive must
   break with the convention or return a tri-state — "no
   candidate" (reject) and "DB unreachable" (retry) must
   differ. Note `DatabaseUnavailable` now exists precisely to
   keep this distinction; the claim path must use it.
8. **The hard-delete hook is behind cluster-daemon election**
   — claim cleanup on instance hard-delete inherits that
   SPOF regardless of the reaper-model choice.
9. **Placement is rewritten outside the scheduler**: the
   cleaner calls `place_instance(config.NODE_UUID)` for
   locally-found domains, incrementing the same
   `placement_attempts` counter preflight uses as its retry
   budget. A claim keyed to placement must tolerate placement
   changing without a scheduling decision.
10. **`GET /admin/resources` must subtract active claims**
    and `cluster_ci_tests/test_nodes.py:49-70` will notice if
    it disagrees with admission.
11. **`memory_reserved_mb` is capped at half the machine**
    and `cpu_schedulable` floored at 1 — an
    effective-capacity query recomputing reservations from
    config instead of reading published metrics will disagree
    with admission on small deployments.
12. **Unit mismatches are endemic**: `disk_free_instances`
    is bytes, `disk['size']` and `disk_reservation_gb` are
    GB; sizeless disks (CD-ROMs) are excluded from admission
    entirely — so "claim virtual size from disk_spec"
    under-counts CD-ROMs and base-image-sized disks. The
    claim arithmetic must define its units once and convert
    at the edges.

## Part 2: CI conductor current state

Repo: `shakenfist/private-ci`. All references `file:line`.

### Admission today

**Budget.** `MAX_WORKERS = 7` at `conductor/provisioner.py:139`;
`BUILDER_RESERVED_SLOTS = 2` at `provisioner.py:147`.

`create_workers()` (`provisioner.py:697-947`) computes, at
`provisioner.py:745-758`:

```
permitted_additional = MAX_WORKERS - len(active_instances)
    - len(preflight_instances) - len(idle_instances)
    - builder_count - reserved
reserved = max(0, BUILDER_RESERVED_SLOTS - builder_count)
    if builder_busy else 0
```

One instance = one slot regardless of size label — the budget
is a **count**, not a resource sum. There is no
cluster-capacity input anywhere in the conductor: no
`get_nodes`/cluster-resource call exists in `conductor/*.py`.
`MAX_WORKERS` is a hand-tuned constant, exactly as
`PLAN-workflow-cost-tracking.md:20-27` describes ("we
basically hope that it all fits").

Two hard gates precede the arithmetic: `quarantined_labels is
None` → provision nothing (`provisioner.py:728-733`);
`'dependencies' in quarantined_labels` → provision nothing
(`provisioner.py:738-743`). Per-label quarantine skip at
`provisioner.py:779-785`.

The three counted lists: `active_instances`,
`idle_instances`, `preflight_instances` are lists of **GitHub
runner names** built in `collector.py:274-306` from
`github.list_workers()` — `active` = online+busy, `idle` =
online+not busy, `preflight` = registered-but-offline and too
young to delete, plus cloud instances GitHub has never seen
adopted as preflight at `collector.py:325-340`.
`builder_instances` is the cloud-side list of live
image-builder instances in the `ci-images` namespace
(`collector.py:166-176`).

**Per-(os,size) demand.** `provisioner.py:776-834`: outer
loop over `CI_IMAGES` (`provisioner.py:42-96`), inner over
`CI_SIZES.keys()` (`provisioner.py:100-137`, insertion order
xs,s,m,m-bigdisk,l,xl,xl-bigdisk). For each combo present in
`jobs_by_label_and_size`:

```
additional = min(jobs_for_combo - idle - building,
                 max_additional - requested)
```

where `max_additional = min(permitted_additional,
queued_jobs)` (`provisioner.py:774`), `hit_max` breaks both
loops at `provisioner.py:820-823`. Ordering consequence:
**label order dominates size order**, so it is not a
smallest-first global policy — `debian-11/xl` is attempted
before `rocky-10/xs`.

**Cadence.** `main.py:142` `while True`, `main.py:327`
`time.sleep(10)` per cycle. When saturated, `create_workers()`
itself does `time.sleep(30)` and returns
(`provisioner.py:759-771`), i.e. a ~40s cycle under load.
Watchdog: `Heartbeat` (`main.py:45-88`), stale window 300s
(`main.py:42`) — anything a claim call adds must stay inside
that.

### Namespace lifecycle hooks

**Creation sequence** — all inside the innermost
`for i in range(additional)` loop, `provisioner.py:836-947`,
with no `try`/`except` anywhere in the function:

| Step | Line | Call |
|---|---|---|
| ids minted | `provisioner.py:840-841` | `sf_random.random_id()` × 2 |
| namespace | `provisioner.py:869-871` | `system_namespace.create_namespace('sfcbr-%s')` |
| key | `provisioner.py:872-875` | `add_namespace_key(ns, 'conductor', ns_key)` |
| trust | `provisioner.py:876-878` | `add_namespace_trust('ci-images', ns)` |
| per-ns client | `provisioner.py:879-887` | `sfclient.build_client(namespace=..., key=ns_key)` |
| network | `provisioner.py:889-892` | `ci_namespace.allocate_network('10.0.0.0/24', ...)` |
| userdata | `provisioner.py:894-906` | `render_userdata()` + `github.new_worker_token()` |
| instance | `provisioner.py:907-943` | `create_instance(...)` with metadata `{'os','size'}` |
| bookkeeping | `provisioner.py:944-947` | `instance_ages[name] = time.time()` |

**Claim create slots at `provisioner.py:871-876`** — after
`create_namespace`, before `allocate_network` /
`create_instance`. Claim size is fully known there:
`CI_SIZES[ci_size]` cpu/ram, boot disk +
`CI_DEPENDENCIES_DISK_SIZE = 60` (`provisioner.py:98`,
`918-936`).

**Error handling today: none on this path.** An
`APIException` from any of those calls propagates to the
catch-all at `main.py:321-325`, abandoning the rest of the
provisioning pass. The half-built namespace is
garbage-collected a cycle later as a stray namespace
(`collector.py:222-231`, `main.py:258-262`). `SFTimeout` gets
its own arm at `main.py:312-319`. A failing claim create that
raises would abort the whole cycle — a claim-aware loop must
catch locally.

**Teardown.** `remove_namespace()`
(`provisioner.py:525-605`), each step independently
`try`-wrapped by deliberate design (docstring
`provisioner.py:527-537`): costs, `delete_all_instances`,
`delete_all_networks`, per-network fallback,
`remove_namespace_trust`, `delete_namespace`. **Claim delete
slots at `provisioner.py:595`** (just before
`delete_namespace`), in its own broad `try`. Three teardown
entry points live in `collector.py` (`collector.py:288`,
`292`, `327`), so claim release must hang off
`remove_namespace()`, not off the main loop. Stray-namespace
teardown (`main.py:259`) is the path that would release a
claim whose instance never came up. If the claim is a child
of the namespace object with its own expiry (Q15 working
position), all of this is covered by `delete_namespace` plus
the TTL backstop.

### Footprint data available

Schema `workflow_costs` at `db.py:154-196`. The
reservation-denominated columns are `peak_allocated_cpus`,
`peak_allocated_ram_mb`, `peak_allocated_disk_gb`,
`peak_concurrent_instances`, `total_instances`
(`db.py:96-100`), produced at teardown by
`_peak_concurrent_allocation()` (`provisioner.py:279-316`, an
event sweep over instance lifetime intervals). Namespace-wide,
so it **includes the nested under-cloud instances a
cluster-CI workflow creates**. Documented undercount:
instances hard-deleted before teardown are invisible
(`provisioner.py:463-467`).

`get_cost_observations(min_runs=3)` (`db.py:1062-1173`)
groups by **(repo, workflow_name, runner_size)** and returns
`MAX(peak_allocated_*)` per group — the worst run — plus
averages. This is already a `footprint(repo, workflow)`
estimator input; the headroom convention to reuse is
`sizing.py:28-30` (`RAM_HEADROOM 1.3`, `DISK_HEADROOM 1.2`,
`CPU_HEADROOM 2.0`). The grouping key is not `job_name`, even
though it is stored (`db.py:160`); a per-job estimator needs
a new query.

**The conductor knows repo/workflow/job at creation time —
confirmed.** `queued_jobs_by_label_and_size` maps
`(label, size) → [{repo, run_id, workflow_name, job_name,
labels, html_url, created_at}]` (`github.py:990-1003`),
passed through `collector.py:582-584` → `main.py:286-288` →
`create_workers()`. Inside the loop it is popped per worker
at `provisioner.py:828-848` (`triggering_job`) and today used
only for a log line. So a claim can be sized
per-(repo, workflow, job) at creation. Caveats: (a) the
association is heuristic — GitHub does not say which runner
picks up which job, so the popped job is only the
*triggering* job; (b) `pending_jobs` may be empty
(`triggering_job=None` branch at `862-868`), so the estimator
needs a size-label fallback; (c) label→size resolution is
itself defaulted when a job carries no size label
(`github.py:956-974`).

Post-hoc attribution is already solved and durable:
`runner_jobs` (`github.py:1005-1021`) → `worker_metadata`
(`main.py:171-204`) → `runner_attribution` table
(`db.py:267-277`), merged at `provisioner.py:479-484`.

### Deferral mechanics

**No "tried and failed" state exists.** `create_workers()` is
stateless across cycles; a claim rejection would be forgotten
in 10s and retried identically. Deferral needs new state; the
durable-state precedent is `runner_attribution`
(`db.py:255-266`) and `bugfix_dispatches`. Nothing today ages
or prioritises: `pending_jobs` order is GitHub iteration
order, but `created_at` is carried per queued job
(`github.py:1000-1002`), so FIFO aging is available for free.
"Run smaller queued jobs first" requires inverting the loop
nesting at `provisioner.py:776-786` (size-major rather than
label-major) — a small, contained change. Hysteresis
precedents: the 600s reap cooling-off
(`provisioner.py:616-619`) and the imagebuilder's
`next_attempt` backoff (`collector.py:121-122`).

**Dashboard surface.** The snapshot is assembled in one
literal at `collector.py:547-568`; natural claim telemetry:
a `claim` key per entry in `queued_jobs_list`/`workers` plus
counters in `summary` (touch `collector.py:547` and defaults
at `state.py:5-26`; `web.py` needs no change). Prometheus:
add a `Gauge` in `metrics.py:16-63`, read in
`observe_snapshot()` (`metrics.py:78-89`); `HEADROOM`
(`metrics.py:47-49`) is the existing analogue.

### SF client usage

Every SF call goes through `sfclient.build_client()`
(`sfclient.py:106-117`) → `TimeoutClient`
(`sfclient.py:81-103`), a `__getattr__` proxy bounding any
call at 90s. **A new `reservation_create()` client method
needs zero changes in `sfclient.py`** — proxied
automatically, raising `SFTimeout` on hang, which
`main.py:312-319` already handles.

Two client identities: the **system** client
(`main.py:101-104`) used for namespace lifecycle, and a
short-lived per-runner-namespace client
(`provisioner.py:879-887`) used only for `allocate_network`
and `create_instance`. A claim is a privileged/quota object,
so it belongs on the system client.

The `ci-images` namespace (`provisioner.py:634-654`) hosts
image-builder instances — if claims are how capacity is
guaranteed, `ci-images` needs one too, and
`BUILDER_RESERVED_SLOTS` becomes redundant with it.

**Client dependency.** `requirements.txt:2` is bare
`shakenfist-client` — no pin. The real pin is the deploy:
33fl `conductor.yml:81-87` force-installs
`git+https://github.com/shakenfist/client-python@develop`
(sfcbr tracks shakenfist develop). New client methods are
available on next deploy with no pin bump. Client
conventions: thin `_request_url` wrappers, and new server
features gated by `check_capability()`
(`apiclient.py:253-255`). The conductor already codes
defensively for client skew (`CLEANUP_EXCEPTIONS` assembled
with `getattr`/`hasattr`, `provisioner.py:157-166`); expect a
capability string plus `hasattr(client, 'reservation_create')`
so an un-upgraded client degrades to unclaimed behaviour.

### Surprises / contradictions

1. **`MAX_WORKERS` is 7 in code but 6 in the docs**
   (`ARCHITECTURE.md:847`). Don't quote the doc number.
2. **The budget is a slot count, not a resource sum.**
   Claims make the conductor resource-aware for the first
   time, and `MAX_WORKERS` then becomes a second, coarser
   limiter that will silently dominate (7 × xs is nothing;
   7 × xl-bigdisk is 56 vCPU / 112 GB / 4.2 TB). The
   integration contract must say what happens to
   `MAX_WORKERS` — retired, raised, or kept as a
   GitHub-API-cost guard.
3. **Per-workflow claim sizing at creation is already
   plumbed** (`triggering_job`), but the association is
   heuristic — which argues for the mutable claim of Q15
   rather than one-shot sizing.
4. **Three teardown call sites live in `collector.py`**, so
   claim-release logic must live in `remove_namespace()`.
5. **`create_workers()` has no exception handling.** A claim
   create that raises kills the rest of the cycle — the
   opposite of "defer this runner and try a smaller one".
6. **Dashboard `headroom` and the provisioning budget
   disagree** (`collector.py:488-494` omits the builder
   reserve), and **stray instances are counted in neither**
   — under claims, a stray namespace becomes a claim leak.
7. `conductor/dashboard.py` is dead code; the live dashboard
   is `web.py` + `templates/dashboard.html` + `state.py`.
8. **`get_cost_observations()` requires `min_runs=3`** and
   drops unattributed rows — the size-label fallback is the
   default for anything new, not an edge case.
9. `peak_allocated_disk_gb` sums *virtual* `disk_spec` sizes
   — already the right denomination for the Q16 claim
   semantics.
10. No test covers `create_workers()`; claim-aware admission
    arrives with no harness to extend.

## Part 3: External prior art

**Method note.** Section A combines MySQL/MariaDB reference
documentation with an **empirical probe run against
`mariadb:10.6.27`** (matching SF's `MIN_MARIADB_VERSION =
(10, 6, 0)`, `shakenfist/mariadb.py:354`), because the
load-bearing question — does an aggregate-guarded
`INSERT ... SELECT` actually block a concurrent claimer? — is
not answerable from the docs alone. Sections B and C are
documentation/source-grounded.

### A. MariaDB/InnoDB atomic-claim idioms

#### What InnoDB actually guarantees

| Fact | Source |
|---|---|
| `INSERT INTO T SELECT ... FROM S WHERE ...` sets an X index-record lock (no gap lock) on each inserted row. "If the transaction isolation level is READ COMMITTED, InnoDB does the search on S as a consistent read (no locks). Otherwise, InnoDB sets shared next-key locks on rows from S." | MySQL manual, innodb-locks-set |
| Under RR, locking reads / `UPDATE` / `DELETE` take **only an index-record lock** when the search uses "a unique index with a unique search condition"; **gap or next-key locks** for any other search condition. | innodb-transaction-isolation-levels |
| Under RC, locking reads/`UPDATE`/`DELETE` "lock only index records, not the gaps before them ... Gap locking is only used for foreign-key constraint checking and duplicate-key checking." | ibid |
| The RR snapshot **does not apply to DML**: "The snapshot ... applies to SELECT statements within a transaction, not necessarily to DML statements." A searched `UPDATE` evaluates its `WHERE` against the latest committed rows. | innodb-consistent-read |
| `SELECT ... FOR UPDATE` reads latest data with X locks; MySQL explicitly warns the `FOR SHARE`-then-update counter pattern deadlocks. | innodb-locking-reads |
| Gap locks are "purely inhibitive ... Gap locks can co-exist." Two holders of the same gap lock that both then insert into it **deadlock**. | innodb-locking |
| MariaDB: "Gap locks are disabled if ... the isolation level is set to `READ COMMITTED`." | MariaDB InnoDB lock modes |

#### Empirical results (mariadb:10.6.27, innodb_lock_wait_timeout=3)

**Test 1 — two claimers, guard read before either write,
capacity 4, each claims 3 (only one may win):**

| Idiom | REPEATABLE READ | READ COMMITTED |
|---|---|---|
| 1. `INSERT ... SELECT` aggregate guard, explicit txn | correct: A=1 row, B blocked → 1205, SUM=3 | **BROKEN: A=1, B=1, SUM=6 vs capacity 4** |
| 2. `SELECT SUM(...) FOR UPDATE` then INSERT | correct but **1213 deadlock** on one session | **BROKEN: both read 0, both inserted, SUM=6** |
| 3. guarded single-row `UPDATE` | correct, SUM=3 | correct, SUM=3 |

**Test 2 — 32 truly-concurrent single-statement claims,
capacity 20, 1 unit each:**

| Idiom | REPEATABLE READ | READ COMMITTED |
|---|---|---|
| 1. `INSERT ... SELECT` aggregate guard | SUM=20 but **11/32 attempts (34%) failed with deadlock 1213** | SUM=20, 0 errors — *see caveat* |
| 3. `UPDATE claims SET used = used + 1 WHERE used + 1 <= limit` | **exactly 20 granted, 12 rejected (rowcount 0), 0 errors, 0 deadlocks** | identical |

Caveat on the RC/idiom-1 "pass": the shell harness spawned 32
`docker exec` clients whose startup jitter (~100-300 ms) far
exceeds the statement window — the statements barely
overlapped. Test 1's RC result (SUM=6) is the reliable
demonstration and matches the documented behaviour. The
proper benchmark harness must generate real overlap
(threaded Python with a barrier), or it cannot validate
anything.

#### Analysis per idiom

**Idiom 1 — conditional `INSERT ... SELECT` with
aggregate/`NOT EXISTS` guard.** Correct *only* under
REPEATABLE READ (RR turns the read into shared next-key
locks). Under READ COMMITTED it is **silently and unboundedly
wrong** — the guard evaluates against a snapshot that cannot
see a concurrent uncommitted claim. The price of RR
correctness is a 34% deadlock rate at 32-way contention on a
hot key (S-then-gap-then-insert upgrade pattern). Worse, the
interesting form ("pick the best node that fits",
`ORDER BY headroom LIMIT 1`) must scan and S-lock *every*
candidate node's reservation range, so the lock footprint and
deadlock window grow linearly with cluster size. SF has
already been burned by this shape:
`_direct_acquire_cluster_lock`'s docstring records that the
earlier INSERT-IGNORE-then-UPDATE version was "a classic
InnoDB shared-shared upgrade deadlock vector", fixed by
collapsing to one statement with no shared-lock read.

**Idiom 2 — `SELECT ... FOR UPDATE` then INSERT/UPDATE.**
Safe for a single row by primary key at any isolation level.
Unsafe for an aggregate/range guard under RC (no gap locks ⇒
phantom; empirically SUM=6/4). Under RR safe but deadlocked
immediately at 2-way contention (co-existing gap locks on the
empty range, then conflicting insert intentions). Genuine
advantages: lock-order control (`ORDER BY node_uuid`) and
`NOWAIT` / `SKIP LOCKED` (both available at SF's 10.6 floor).
Architectural constraint: a multi-statement transaction must
execute entirely inside one `sf-database` RPC — spreading it
over gRPC round trips holds InnoDB locks across the network.
So "easier to read" (master plan Q1) only holds as a single
server-side function, at which point the readability
advantage largely evaporates.

**Idiom 3 — single-row `UPDATE` with arithmetic guard. The
clear winner, and it is not close.** Statement-level
atomicity (autocommit suffices); current read, not snapshot
read; PK equality ⇒ index-record lock only ⇒ no phantom, no
gap-lock deadlock, **identical behaviour under RR and RC**
(a real robustness property for operator-tuned MariaDBs, per
the BYO-mariadb direction). Multi-dimensional claims (cpu AND
memory AND disk) live in one row, so "every constraint holds
or nothing changes" is free. `rowcount == 0` is an
unambiguous, cheap "did not fit". House style already:
`_direct_acquire_cluster_lock` (guarded
`INSERT ... ON DUPLICATE KEY UPDATE`) and
`_direct_refresh_cluster_lock` (guarded UPDATE + rowcount as
CAS) are this idiom, proven under production contention.

Costs and caveats: requires **materialised** `used_*`
counters (not derived `SUM()`), which needs a
drift-correcting reconciler. Claiming from *two* rows (node
AND namespace) breaks single-statement atomicity — needs a
transaction with canonical lock ordering. MariaDB has **no
`UPDATE ... RETURNING`**, so reading back post-claim usage is
a follow-up PK SELECT (as `_direct_acquire_cluster_lock`
already does). Verify the driver's `rowcount` semantics
(`CLIENT_FOUND_ROWS` vs affected-rows) in a unit test; reject
zero-sized claims so "no change" cannot be confused with "did
not fit".

**What real systems use:** guarded decrement +
check-affected-rows is the canonical inventory pattern.
OpenStack Placement and Kubernetes ResourceQuota both use
idiom 3's generalisation — CAS on a version/generation
integer inside one transaction. No serious system builds a
hot claim path on `INSERT ... SELECT SUM(...)`.

#### MariaDB-specific caveats

- **`innodb_snapshot_isolation` is ON by default from 11.6.2
  / 11.8** and makes RR `UPDATE`/`DELETE` fail with
  **ER_CHECKREAD (1020)** when the target row changed since
  the snapshot. SF's supported range spans 10.6 (absent) to
  11.8+ (ON), so any explicit-transaction claim path must
  handle 1020 as a retryable conflict; benchmarks must run
  with it both ON and OFF. Idiom 3 in autocommit is
  unaffected. MDEV-39263 reports it fires "most of the time,
  but not every time" — never rely on it for correctness.
- `INSERT ... RETURNING` (10.5+) exists; `UPDATE ...
  RETURNING` does not.
- `NOWAIT`/`WAIT n` since 10.3, `SKIP LOCKED` since 10.6.
- `Innodb_deadlocks` global status variable is a useful
  benchmark and production metric.

#### Benchmark design (step 2)

Benchmark **idiom 3 first**, idiom 2-as-one-server-side-RPC
(`NOWAIT`, canonical lock order) as the multi-row fallback,
and treat idiom 1 as the thing to disprove. **This reverses
the master plan's Q1 lean, with evidence.** Corollary: Q1 and
Q2 are one decision — materialised counters make atomicity
trivial; derived `SUM()` forces a locking read and inherits
every hazard above. **Prerequisite for phase 1:**
`node_metrics` stores capacity in a schemaless `metrics_json`
column (docstring: "SQL queryability of individual fields is
not needed" — no longer true); capacity fields must be
promoted to typed columns before SQL-side capacity arithmetic
is possible.

Harness: throwaway threaded Python (one connection per
worker, `threading.Barrier` for real overlap — not shell),
against dockerised `mariadb:10.6` AND `11.8`. Schema:
`bench_claims(claim_key PK, limit_{vcpus,mem,disk},
used_{vcpus,mem,disk}, expires_at)` for idiom 3;
`bench_reservations(uuid PK, claim_key, vcpus, mem, disk,
expires_at, KEY(claim_key, expires_at))` for idioms 1/2.
Patterns, 60 s each, W ∈ {1,2,4,8,16,32,64}: P1 single hot
key (~50% rejects — the tight-cluster design case); P2 spread
over 20 keys; P3 pick-best-of-20 by headroom then claim (the
separator — idiom 1 must lock all 20); P4 P1 + background
reaper deleting expired rows every 1 s; P5 batch all-or-
nothing (N=10). Matrix: {RR, RC} ×
{snapshot_isolation ON, OFF}. Metrics: (1) invariant
`used <= limit` and `used == Σ granted` — any violation
disqualifies; (2) granted/s and rejected/s; (3) latency
p50/p95/p99/max; (4) `Innodb_deadlocks` delta + app-side
1213/1205/1020 counts; (5) row-lock wait deltas; (6)
`Handler_read_*` deltas (exposes idiom 1's lock footprint
growth with K).

### B. OpenStack Placement

Providers form a tree; resource classes (`VCPU`,
`MEMORY_MB`, `DISK_GB`, `CUSTOM_*`) are names with semantics
in inventory rows: `total`, `reserved`, `min_unit`,
`max_unit`, `step_size`, `allocation_ratio`. The capacity
check (verbatim from
`placement/objects/research_context.py`):

```python
sa.and_(
    sql.func.coalesce(usage.c.used, 0) + amount <= (
        (inv_tbl.c.total - inv_tbl.c.reserved)
        * inv_tbl.c.allocation_ratio),
    inv_tbl.c.min_unit <= amount,
    inv_tbl.c.max_unit >= amount,
    amount % inv_tbl.c.step_size == 0)
```

Usable capacity is exactly `(total - reserved) *
allocation_ratio` — `reserved` is subtracted before the
ratio. `reserved == total` became legal (microversion 1.26)
as node soft-disable / drain.

`GET /allocation_candidates` is **advisory** — no lock, no
reservation, no TTL; the caller claims and must handle
losing. The atomic claim is `PUT /allocations/{consumer}`:
the consumer's entire allocation set across many providers
replaced in one DB transaction
(`@wrap_db_retry(retry_on_deadlock=True)`), with
`_check_capacity_exceeded()` inside the same transaction and
a generation bump on every touched provider/consumer.
**Two meanings of 409** are load-bearing in nova's client:
capacity exhaustion (do not retry that provider) vs
`placement.concurrent_update` generation mismatch (re-read
and retry, bounded at 4).

Buys: N active-active schedulers, no distributed lock
(correctness is one row-level generation check); capacity
accounting decoupled from placement policy. Costs: a second
service + DB + microversions; retry storms; **allocation
leaks** (claim and instance record are separate writes —
hence `nova-manage placement heal_allocations` and
`audit --delete`).

Worth stealing: (1) one atomic multi-row allocation write
keyed by consumer, the DB adjudicating via a single SQL
predicate; (2) **generation CAS over row locks** — serialise
the commit, not the scheduler's think time, and return
distinguishable errors; (3) `reserved` as a first-class field
distinct from `total`, mapping directly onto the landed
`NODE_*_RESERVATION_*` settings, with `reserved == total` as
free node drain; (4) resource classes as strings
(`(provider, class, amount)` rows) so a new dimension is data
not migration — answers Q10/Q12 cheaply; (5) candidates never
reserve. **Do not copy:** provider trees, traits, granular
request groups, sharing-via-aggregate, and the
separate-service split itself — atomicity comes from one
transaction, not another process, and staying in-process
eliminates the entire leak/heal category. If the reservation
row and instance row are ever written in separate
transactions, you will leak; write them together or build the
orphan-reaper query up front.

### C. Kubernetes ResourceQuota + friends

**Admission is synchronous; the controller reconciles.** On a
fitting request the admission plugin CAS-updates
`ResourceQuota.status.used` via resourceVersion (the design
proposal calls the bottleneck intentional). Waiters are
batched **by namespace** (concurrent creates coalesce into
one status write), retries hardcoded at 3, exhaustion fails
closed. A look-aside cache of the apiserver's own last write
took one benchmark from 12 conflicts to 0. Behind it, the
quota controller does a full recalculation every 5 minutes
plus event-driven replenishment — and it is *not optional*:
"admission control is incapable of guaranteeing a DELETE
request actually succeeded." One documented over-admission
hole: a request matching several quota objects updates them
non-atomically; upstream's advice is one quota per namespace.

**What is counted: declared intent, never observation** —
requests/limits from the pod spec, summed over non-terminal
pods. Quota never evicts; "neither contention nor changes to
quota will affect already created resources." **LimitRange**
mutates defaults in *before* quota checks — a ceiling
without defaults is a wall every unannotated create hits.
QoS classes are *derived from declarations* (Guaranteed
requires limit == request) — precisely the claimed-namespace
shape of Q17.

**kube-scheduler assume/reserve:** scheduling cycles run
serially over an in-memory cache that assumes pods before
binding (the feedforward analogue). **The 30 s assumed-pod
TTL is gone**: issue #106361 found a bind exceeding the TTL
let the assumption expire while the bind still landed —
double-placement — and the fix was expiry = never, then the
whole TTL machinery was deleted. Assumed state is released
only by explicit Forget or by observing the pod — never by a
clock.

Transferable lessons: (1) count declared intent, not measured
usage (validates the Q16 virtual-disk-size position); (2)
admission-time-only accounting is a complete design — but
decide explicitly whether shrink-below-current-usage is
allowed as a pure ceiling change (K8s semantics) versus
floored at usage (current Q15 wording); (3) the hot counter
row is an intentional serialisation point — masked
conditional UPDATE, never full-row read-modify-write (cf.
CLAUDE.md pitfall 3's lost-update history), batch by
namespace if it ever gets hot; (4) **exactly one claim row
per namespace as a UNIQUE constraint** — closes K8s's one
real over-admission hole and answers Q14's shape; (5) ship
the drift reconciler with the counter, not after (deletes are
invisible to admission); (6) ship namespace-level defaulting
with any ceiling; (7) **explicit release primary, expiry as
crash backstop only** — a TTL-driven release of provisional
capacity actively causes double-allocation (#106361); this
tempers master plan Q4/Q5's cluster_locks-style lease
framing.

### Prior-art recommendations for phase 0

1. Reverse the Q1 lean: guarded single-row UPDATE, not
   conditional INSERT ... SELECT (empirical: only idiom
   correct at both isolation levels; zero deadlocks at
   32-way).
2. Decide Q2 before Q1 is meaningful — they are one
   decision. Choose materialised `used_*` counters.
3. One claim row per namespace, enforced by UNIQUE (Q14).
4. Adopt `total` / `reserved` as separate fields; `reserved
   == total` is node drain (maps onto NODE_* reservations).
5. Model dimensions as `(claim, resource_class, amount)`
   rows if Q10/Q12 generality is wanted — new dimensions
   become data, not migrations.
6. Two distinguishable failures: "out of capacity" (no
   retry) vs "concurrent update" (bounded retry).
7. Ship the drift reconciler and orphan-reaper query in the
   same phase as the counter.
8. Explicit release primary; `expires_at` generous crash
   backstop only (k8s #106361).
9. Handle MariaDB 1020 (ER_CHECKREAD) as retryable conflict;
   benchmark on 10.6 and 11.8, snapshot isolation ON and OFF.
10. Phase 1 prerequisite: promote node_metrics capacity
    fields out of `metrics_json` into typed columns.
11. Benchmark harness must be threaded-with-barrier, not
    shell (the shell probe produced a false pass for the
    unsafe idiom).
12. Reuse `_direct_acquire_cluster_lock` as the reference
    implementation — same idiom, already proven here under
    production contention, deadlock post-mortem in its
    docstring.
