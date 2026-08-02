# Scheduler reservations phase 2: capacity tables and reconciler

## Prompt

Before implementing any step, read the phase 0 decisions
(`PLAN-scheduler-reservations-phase-00-decisions.md`) — this
phase implements D2 (three-table schema), D5 (reconciler) and
the schema half of D13 (expected demand) exactly as decided
there; do not reopen them. Read the code this phase extends:
the schema-version machinery and tri-layer access pattern in
`shakenfist/mariadb.py` (`EXPECTED_SCHEMA_VERSIONS`,
`ensure_schema()`, and the `delete_stale_transfers` /
`_grpc_` / `_direct_` triple as the RPC template); the orphan
reconciliation queries in the same file (the index-friendly
`REPLACE(dashed, '-', '') = LOWER(HEX(binary))` join is the
required idiom for every dashed/undashed uuid join in this
phase); the cluster daemon's elected-leader loop and
`schedule`-based task registration
(`shakenfist/daemons/cluster/main.py`,
`daemons/cluster/scheduled_tasks.py`, including the
module-level prometheus counters); the typed `node_metrics`
columns phase 1 added (`NODE_METRICS_EXTRACTION_SPEC`); the
scheduler's current capacity arithmetic
(`shakenfist/scheduler.py` `_has_sufficient_cpu/_ram/_disk`)
which the limit-derivation formulas below must mirror; and
instance placement ground truth (`instance_location` rows in
`object_references`, written by `Instance.place_instance()`).
Ground the work in what the code does, not what this plan says
it does.

## Situation

Phase 0 decided the admission design (guarded single-row
UPDATEs over materialised counters, D1) and its schema (three
tables, no per-decision reservation rows, D2). Phase 1
promoted the capacity-relevant `node_metrics` fields to typed
columns, so limit derivation can now be done in SQL. Nothing
yet stores derived capacity, and nothing recomputes usage
from ground truth.

The master plan deliberately splits the storage-and-
reconciliation layer (this phase) from admission (phase 3) so
that the tables, the limit derivation and the reconciler can
soak on sfcbr — observable via prometheus and logs, consulted
by nothing — while phase 3 is built. Any bug found during
soak costs an annoyed operator a dashboard glance, not a
mis-placed instance.

## Mission and problem statement

Create `scheduler_node_capacity`, `namespace_claims` and
`cluster_capacity` per D2; implement the reconciler as a
periodic task in the cluster daemon's elected-leader loop per
D5, recomputing every counter from ground truth server-side;
carry the D13 `expected_demand` column and its decay
recompute from day one. **Nothing consumes any of it for
admission** — this phase is observable-but-inert.

## Design

### Tables (D2, verbatim)

All uuid key columns are `sa.Uuid` (undashed CHAR(32) on
MariaDB, matching the static tables); every join against the
dashed-form tables (`object_states`, `object_references`)
uses the orphan-reconciliation transform idiom (pitfall 6).
Timestamp columns follow the `cluster_locks` TIMESTAMP idiom
so expiry guards compare against server-side `NOW()`.

`scheduler_node_capacity` — one row per hypervisor:
`node_uuid` (PK), `limit_cpus`, `limit_memory_mb`,
`limit_disk_gb`, `used_cpus`, `used_memory_mb`,
`used_disk_gb` (all INTEGER), `expected_demand` (DOUBLE),
`updated_at`.

`namespace_claims` — one row per claim: `uuid` (PK),
`namespace` (type matching `namespaces.name`, indexed —
admission in phase 3 looks claims up by namespace),
`limit_*` and `used_*` (same three dimensions), `state`
(string), `expires_at`, `updated_at`. Created empty in this
phase: the claims API is phase 4. The reconciler's per-claim
recompute and expiry sweep are still implemented and
unit-tested now, so phase 4 lands onto proven machinery.

`cluster_capacity` — a singleton (`id` PK, always 1):
`total_*`, `claimed_*`, `unclaimed_used_*` for the three
dimensions, `updated_at`. The D14 admission guards run
against this row in phases 3/4; here it is only maintained.

New version constants (all 1, the new-table convention):
`SCHEDULER_NODE_CAPACITY_VERSION`, `NAMESPACE_CLAIMS_VERSION`,
`CLUSTER_CAPACITY_VERSION`, wired into
`EXPECTED_SCHEMA_VERSIONS`, `ensure_schema()` and the
`EXPECTED_TABLE_NAMES` drift guard in
`test_mariadb_schema_concurrency.py`. Fresh creation only —
there is no prior shape to migrate. The standard machinery
gives the rest: `sf-ctl ensure-mariadb-schema` creates the
tables; `verify_schema_versions` makes `sf-database` refuse
to start until it has run.

No new indexes beyond the PKs and the `namespace` index:
row counts are nodes, claims and one.

### Limit derivation (mirrors today's scheduler arithmetic)

Limits are recomputed each reconciler pass from the phase 1
typed `node_metrics` columns. The formulas deliberately
mirror `scheduler.py` so phase 3's guard admits exactly what
today's Python filter admits:

* `limit_cpus = floor(cpu_schedulable ×
  CPU_OVERCOMMIT_RATIO)` — the `_has_sufficient_cpu` bound.
  `used_cpus` counts allocated vCPUs, as
  `cpu_total_instance_vcpus` does today.
* `limit_memory_mb = floor(memory_max ×
  RAM_OVERCOMMIT_RATIO) - memory_reserved_mb` — the
  `_has_sufficient_ram` overcommit bound. `used_memory_mb`
  counts *allocated* MB (not the KSM-aware
  `memory_total_instance_actual`): the counter is an
  allocation ledger. The actual-free and actual-usage checks
  remain phase 3 candidate *filters* over the typed
  `node_metrics` columns per D7; the counter guard is the
  allocation backstop.
* `limit_disk_gb = used_disk_gb + max(0,
  floor(disk_free_instances/GiB) - disk_reservation_gb)`,
  recomputed together with `used_disk_gb`. There is no
  total-disk metric, and free space is the only ground truth
  that survives qcow2 growth, so the limit is expressed as
  "current virtual drawdown plus what the filesystem says
  still fits". The phase 3 guard `used + x <= limit` is then
  exactly today's `_has_sufficient_disk` free-space check,
  while accounting virtual size per D2.

Nodes gain a capacity row when they have a `node_metrics` row
with non-NULL capacity columns; rows are deleted when the
node is deleted. NULL-columned metrics rows (written by an
old resources daemon mid-upgrade) leave the previous limits
in place rather than zeroing them.

### The reconciler (D5)

A new `scheduled_tasks.reconcile_scheduler_capacity()`
registered in the elected-leader loop at
`schedule.every(5).minutes` (D5's provisional period, matching
the hardcoded cadence of its sibling tasks; tune when step 3
data lands). The task body is one public
`mariadb.reconcile_scheduler_capacity()` call — tri-layer,
modelled on `delete_stale_transfers`: a single new
`ReconcileSchedulerCapacity` RPC in `database.proto`
(regenerate with `tox -e genprotos`), a servicer method in
`daemons/database/main.py` with the standard counter and
error wrapper, and `_direct_reconcile_scheduler_capacity()`
doing all the work server-side in set-based SQL.

One pass, in order:

1. **Expire claims**: `UPDATE namespace_claims SET state =
   'expired' WHERE state = 'active' AND expires_at < NOW()`
   (the D4 crash backstop; a no-op until phase 4 creates
   claims).
2. **Refresh node rows**: upsert limits per the formulas
   above from `node_metrics`; delete rows for deleted nodes.
3. **Recompute usage**: node `used_*` from placed non-dead
   instances — `instance_location` rows in
   `object_references` joined to `instances` (cpus, memory)
   and to `object_states` excluding `deleted` (errored
   instances keep their placement and their resources until
   hard-delete, so they count). Disk sums virtual sizes from
   the `disk_spec` JSON: prefer `JSON_TABLE` (available from
   the MIN_MARIADB_VERSION floor of 10.6); if it proves
   unworkable against real payloads, aggregate per-instance
   in Python inside the `_direct_` function — still one RPC,
   still server-side. Claim `used_*` recomputes per
   namespace over the same instance set.
4. **Recompute `expected_demand`** (D13): per node,
   `SUM(cpus × SCHEDULER_DEMAND_PER_VCPU × (1 - age /
   SCHEDULER_DEMAND_DECAY_SECONDS))` over placements younger
   than the decay window, using the `instance_location`
   reference row's `created` timestamp as placement time.
   Phase 3's placement-time increment becomes the fast path
   over this authoritative recompute.
5. **Recompute the singleton**: `total_*` as sums of node
   limits, `claimed_*` as sums of active claim limits (zero
   for now), `unclaimed_used_*` as usage by instances in
   namespaces without an active claim (all usage, for now).

The reply carries a summary (per-node limits/used/demand, the
cluster row, expired-claim and added/removed node counts, and
per-counter deltas since the previous values) so the elected
node can log and export it without a second RPC.

Because nothing else writes these tables until phase 3, the
per-statement writes need no enclosing transaction; the
counter deltas the pass observes are ordinary between-pass
churn, not drift. The drift-is-a-bug loud-warning path (D5)
activates in phase 3, when guarded UPDATEs maintain the
counters between passes and the recompute becomes a
correction; the plumbing (deltas in the reply) is built now.

New config: `SCHEDULER_DEMAND_PER_VCPU` (default 2.5, the
00a-1 seed) and `SCHEDULER_DEMAND_DECAY_SECONDS` (default
600), both marked *revisit when step 3 data lands*.

### Observability (the point of the phase)

* Prometheus, from the elected cluster daemon (module-level
  metrics in `scheduled_tasks.py`, exported on the existing
  `CLUSTER_METRICS_PORT` server): per-node gauges for limit,
  used and expected demand (labelled by node and resource
  dimension), cluster-row gauges, a reconcile-duration
  summary, a pass counter and a last-success timestamp gauge.
* Logs: one structured `LOG.info` summary per pass (node
  count, deltas, expired claims, duration); failures go
  through the loop's existing `ignore_exception` path so a
  bad pass never kills maintenance, and CI's log-error checks
  surface any exception during cluster CI runs.
* The tables themselves, queryable on the database node.

D9's events (drawdown, ceiling rejection) are admission
events and land with admission (phases 3/7). No REST read
surface yet: the admin capacity view migrates in phase 5.

### Explicitly out of scope

- No admission or placement consumption (phase 3): the
  scheduler, `place_instance()` and the queue worker are
  untouched.
- No claims API, claim objects or claim events (phase 4).
- No guarded-UPDATE drawdown anywhere; the reconciler is the
  only writer.
- No REST/API surface and no client changes.
- No new events; observability is prometheus + logs.

## Execution

| Step | Description | Effort | Model | Isolation | Status |
|------|-------------|--------|-------|-----------|--------|
| 1 | Schema: three tables, version constants, `ensure_schema()` and `EXPECTED_SCHEMA_VERSIONS` wiring, `EXPECTED_TABLE_NAMES` drift-guard update, unit tests (creation, versions, idempotent re-run) | medium | sub-agent | worktree | Complete — 10 unit tests, pre-commit green |
| 2 | Reconcile RPC: proto + genprotos, servicer, tri-layer functions, the five-part recompute (limit formulas and demand decay as pure, unit-testable helpers), summary reply; unit tests incl. scheduler-parity cases for the limit formulas, decay arithmetic, dashed/undashed join shapes, empty-cluster and empty-claims passes | high | sub-agent | worktree | Complete — JSON_TABLE used for the disk sum (validated against real MariaDB 10.6 with the exact query; Python reference kept as `_disk_spec_virtual_gb`), 38 unit tests |
| 3 | Cluster daemon integration: scheduled task, prometheus metrics, structured pass logging; unit tests where practical | medium | sub-agent | worktree | Complete — `scheduler_capacity_*` metrics, stale node label sets removed, failure path cannot raise; 4 unit tests |
| 4 | Local validation: run the reconciler against a docker MariaDB seeded with realistic rows (incl. a multi-disk `disk_spec` and both uuid forms); record results in the Validation section | medium | management session | none | Complete — 36/36 checks, 42 ms at 32 nodes / 1,205 instances; see Validation |
| 5 | Docs: `docs/operator_guide/database.md` (three tables), CLAUDE.md MariaDB-storage list, correct CLAUDE.md's stale `shakenfist/deploy/` collection paths, ARCHITECTURE/AGENTS if warranted, master plan phase row | low | sub-agent | worktree | Complete — all four docs updated; verification showed the collection and `shakenfist_ci` still live in-repo at `shakenfist/deploy/`, so only CLAUDE.md's directory tree (which drew `deploy/` at the repo root) needed correcting |
| 6 | Management-session code review against the checklist | medium | management session | none | Complete — checklist verified 2026-08-02 |
| 7 | Operator review and PR; deploy to sfcbr and confirm gauges/rows during soak | — | operator | — | Not started |

## Validation

### Step 4: docker-MariaDB reconcile validation (2026-08-02)

Run against MariaDB 11.8 with the schema created by
`ensure_schema()`, seeded with four nodes (live, live,
new-with-partial-metrics, deleted), five instances with
deliberately messy `disk_spec` payloads (string sizes, null
sizes, a non-list document), dashed-uuid placement references
and states, and two claims (one live, one past expiry). All
36 checks passed:

* Limits match the hand-computed scheduler-parity values for
  both overcommit ratios; the partial-metrics node is skipped
  without a row and the deleted node gets none.
* Usage counts the errored instance, excludes the deleted
  one, and counts a stateless (zombie) instance; the
  malformed `disk_spec` contributes 0 without aborting the
  pass; `20 + '8'` sums to 28 GB.
* Expected demand matches the decay formula (4 vCPUs placed
  100 s ago at defaults → 8.333).
* The stale claim flips to `expired` and stops counting
  toward `claimed_*`; the live claim's `used_*` recomputes to
  the namespace's actual footprint.
* The cluster singleton sums node limits, active claim limits
  and unclaimed usage correctly; a second pass is idempotent
  (zero added/expired, zero deltas).
* Duration: 14 ms for the small case, 42 ms at 32 nodes /
  1,205 placed instances — far inside the watchdog budget.

Two incidental findings worth recording:

1. **`ensure_schema()` cannot run on MariaDB 10.6**: the
   pre-existing `ipam_reservations.address INET4` column
   requires 10.10+, so the documented
   `MIN_MARIADB_VERSION = (10, 6, 0)` floor is already
   unsatisfiable for fresh installs — not a phase 2 issue,
   but the constant is stale. JSON_TABLE itself was
   separately validated on a real 10.6 during step 2 with the
   exact reconcile query text. *Fixed on this branch: the
   floor is now `(10, 11, 0)` — the oldest in-support LTS
   above INET4's 10.10 requirement, and the version cluster
   CI actually exercises. sfcbr runs `mariadb:11.8` and is
   unaffected.*
2. **Claim expiry must be written server-relative.** The
   first validation run seeded `expires_at` from the client's
   local clock and the expiry sweep correctly did nothing —
   the timestamp was hours in the future in the server's
   timezone. Phase 4's claim create/update must always write
   expiry as `NOW() + INTERVAL`, the `cluster_locks` idiom,
   never a client-computed datetime.

## Administration and logistics

### Success criteria

* After `sf-ctl ensure-mariadb-schema` and a deploy, the
  first reconcile pass populates one `scheduler_node_capacity`
  row per hypervisor with limits matching a hand check of
  that node's `node_metrics` values, and `cluster_capacity`
  sums them; a manual sfcbr spot-check confirms `used_*`
  matches the actually-placed instances.
* Prometheus gauges are visible from the elected cluster
  node and move when instances are created and deleted.
* `sf-database` refuses to start against a schema missing the
  three tables; `ensure-mariadb-schema` is idempotent.
* An exception inside the reconciler is logged and skipped
  without breaking the maintenance loop, and cluster CI runs
  clean (its log-error checks are the functional gate for
  this phase; a REST-visible functional test waits for the
  phase 5 read surface).
* Zero behaviour change anywhere else: `tox` and cluster CI
  green, scheduler and placement code untouched.
* mypy coverage of touched code does not regress.

### Review checklist (management session, step 6)

- [x] Every dashed/undashed uuid join uses the index-friendly
      transform (pitfall 6); no join compares the two forms
      directly.
- [x] Limit formulas match `scheduler.py` arithmetic;
      deviations (allocation-ledger memory, virtual-size
      disk) are exactly the documented ones.
- [x] The reconciler cannot raise out of the scheduled task,
      and one malformed row (bad `disk_spec`, NULL metrics)
      cannot abort the whole pass.
- [x] Reconcile is a single RPC; its duration and reply size
      are bounded and sane at sfcbr scale (watchdog budget,
      gRPC message limits).
- [x] NULL-columned `node_metrics` rows (old resources
      daemon) do not zero existing limits.
- [x] Nothing outside the reconciler and its tests reads or
      writes the new tables.
- [x] `in_memory_only` objects cannot reach the new tables
      (the reconciler only reads static tables and writes its
      own).
- [x] New config options registered with descriptions and
      flagged provisional pending step 3 data.
- [x] The plan file rides in the PR branch; master plan row
      updated.

### Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan and how the
work you intend to do aligns with that plan.
