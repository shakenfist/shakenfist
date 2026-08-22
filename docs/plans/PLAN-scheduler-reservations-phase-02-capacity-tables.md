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
`used_disk_gb` (all BIGINT, matching the int64 proto fields;
the cluster sums could overflow INT around 700 TB of
overcommitted RAM, and widening costs nothing while the
tables are new), `expected_demand` (DOUBLE), `updated_at`.

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
  `used_cpus` sums the allocated vCPUs of every placed,
  non-deleted instance. **This is not parity with
  `cpu_total_instance_vcpus`**, which an earlier draft of this
  plan claimed: the resources daemon counts only *active*
  libvirt domains, so a powered-off instance (or one placed
  but not yet defined in libvirt) is in the ledger and not in
  the measurement, and `used_cpus` reads higher than today's
  filter sees on a node with powered-off instances. The ledger
  is the right semantics for a reservation — a stopped
  instance still owns its resources — but it means phase 3
  must decide explicitly which number its guard reads, and a
  unit test asserting the two agree would be false comfort.
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

Capacity rows exist per **schedulable hypervisor**, not per
node. A row describing capacity the scheduler would never use
is worse than no row at all, because it inflates the cluster
totals that a phase 3 D14 guard reads. The invariant, learned
the hard way across five review rounds that each found the
same defect in a new guise (role, state, freshness, existence,
and — twice — filter polarity: a subtractive filter admits
whatever is in neither set), is: **a capacity row exists
only where the scheduler could place, every node-level
scheduler-side filter is automatically a reconciler-side
filter too, and each filter is expressed as membership in a
positive qualifying set, never as absence from a
disqualifying one.** By that rule one node-level filter remains
deliberately unmirrored: `_has_reasonable_queue_state()` (the
scheduler's `queue_state` stage, which drops a node whose
queue depth suggests it is wedged). It is excluded because
queue depth is a fast-moving liveness signal, not capacity —
mirroring it at a five-minute cadence would make rows flap on
transient queue spikes — but if phase 3's guard needs it, it
is a *decision* to take, not a gap to discover. Four filters
enforce the invariant today, each mirroring something
`scheduler.py` already does before it will consider a node:

* **Role.** The resources daemon runs on every cluster node
  and upserts `node_metrics` unconditionally, so a
  network-only or database-only node has a metrics row with
  perfectly good capacity columns. `scheduler.py` drops
  non-hypervisor candidates before any capacity arithmetic, so
  phase 2 adds `is_hypervisor` to
  `NODE_METRICS_EXTRACTION_SPEC` (`node_metrics` v3 → v4) and
  filters on the typed column in SQL. A NULL `is_hypervisor` —
  a metrics row not yet rewritten after the upgrade — is
  treated as no evidence either way: it neither creates nor
  destroys a row, and an existing row keeps its limits until
  the next 60-second upsert settles the question.
* **State.** The scheduler builds its candidate set from
  `Nodes([], prefilter='active')`, so an errored, missing,
  stopping, stopped or deleted node is not a placement
  candidate. A hypervisor that the node-health cascade has
  taken out of service must stop contributing its limits, not
  sit there advertising capacity nothing can be placed on. The
  filter is expressed positively — `state_value IN` the active
  set (`constants.NODE_ACTIVE_STATES`, which
  `Node.ACTIVE_STATES` is now defined from so the two cannot
  drift), with candidates intersected against the result — so
  everything outside the set is excluded by default: a state
  added to the state machine later, and a node with *no* state
  row at all. `prefilter='active'` resolves through
  `get_objects_by_state`, which only returns objects that have
  a state row, so the scheduler sees neither; and stateless
  zombies are a real condition here (the orphan reconciler
  exists for them). An earlier draft subtracted a
  `NOT IN`-derived inactive set instead, and a stateless node
  appeared in neither set and slipped through — the fifth
  instance of the recurring defect (fourth review, item 1).
* **Existence.** A node must have a row in the `nodes` table,
  gating creation as well as removal. Gating only removal
  leaves a permanent phantom: a `node_metrics` row that
  outlives its node's static and state rows reads as a fresh
  hypervisor, gets a row, and then no removal condition ever
  fires for it.
* **Freshness.** `node_metrics` rows are only deleted when the
  node is, so a node whose resources daemon has died would
  otherwise contribute its last-known limits forever. Rows
  older than `RECONCILE_METRICS_MAX_AGE_SECONDS` (15 minutes:
  three reconcile cadences, fifteen publish intervals) are
  ignored. Deliberately *not* the scheduler's 120-second
  window, which is tuned for an on-demand cache and would sit
  below this pass's five-minute period, making rows flap in
  and out between passes and rendering the reply's
  `nodes_added`/`nodes_removed` counts meaningless. Like the
  state filter, this is expressed positively — membership in a
  fresh set, and absence removes the row — because a node can
  also have *no* metrics row at all: sf-resources deletes its
  own node's rows at daemon startup before the first upsert,
  so a resources daemon dying in that window leaves a live,
  active node with no row, which a stale-set subtraction
  retained forever (fifth review, item 2 — the freshness twin
  of the state filter's polarity bug). The fresh set carries
  no `is_hypervisor` predicate, so a fresh row whose flag is
  still NULL mid-upgrade keeps qualifying for the no-evidence
  retention.

A node failing any of these loses its capacity row, which is
also how rows written by a release before the filters existed
get cleaned up on upgrade.

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
   namespaces without an active claim, restricted to nodes
   that hold a capacity row. The singleton is a *closed
   accounting over the schedulable cluster*: an instance
   stranded on an errored, demoted, stale-metrics or deleted
   node contributes to neither the total nor the used side,
   so a drained hypervisor shrinks both together instead of
   showing usage exceeding capacity on the soak dashboards.
   Per-claim `used_*` is the deliberate exception — it stays
   namespace-wide, because a quota covers a namespace's
   instances wherever they are stranded.

Two claim-accounting decisions this leaves for phase 4, both
inert while `namespace_claims` is empty but both fixed by the
semantics being built now:

* **Overage is currently invisible.** `unclaimed_used_*`
  skips any namespace with an active claim, and `claimed_*`
  sums those claims' *limits*. A namespace using more than it
  claimed contributes its overage to neither figure, so a
  D14 guard shaped `claimed + unclaimed_used + request <=
  total` would over-admit by exactly that overage. Phase 4
  must either prevent overage at admission, or have the
  reconciler attribute usage above a claim's limit into
  `unclaimed_used_*` so the cluster row stays a complete
  accounting of the cluster.
* **Two active claims for one namespace would double-count.**
  Each active claim row is written the same full namespace
  usage, and both limits sum into `claimed_*`. The claims API
  should enforce one active claim per namespace; a unique
  partial index would make that structural rather than
  procedural.
* **Duplicate placements double-count into per-claim
  `used_*`.** The stale-placement edge (a lost node's
  `instance_location` row surviving `place_instance()`'s
  best-effort removal, so one instance appears on two nodes)
  is benign *at cluster scope only*, because the unclaimed
  fold is restricted to nodes holding a capacity row.
  Per-claim `used_*` is deliberately namespace-wide (see
  above), so a duplicated placement adds that instance's
  resources to the claim twice, and a namespace could be
  denied capacity it is not using once `used_*` is a quota.
  Phase 4 must either de-duplicate by instance uuid in the
  usage query (one placement row per `target_uuid`) or rely
  on phase 3 having eliminated stale placement rows. The
  `_RECONCILE_USAGE_SQL` comment block carries the same note.

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

*Correction (2026-08-22, phase 4a):* 2.5 was not the 00a-1
seed. That appendix names ~0.33 steady / 0.6 conservative for
this constant; 2.5 is its allocated-vCPUs-per-thread packing
figure, a different quantity. The default is now 0.6 — see
issue #3813 and
[PLAN-scheduler-reservations-phase-04a-demand-guard.md](PLAN-scheduler-reservations-phase-04a-demand-guard.md).

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
| 7 | Operator review and PR; deploy to sfcbr and confirm gauges/rows during soak | — | operator | — | Complete — merged as PR #3614, 2026-08-08; reconciler soaking cleanly on sfcbr (5-minute passes, no drift) |
| 8 | Address automated review of PR #3614 | medium | management session | none | Complete — see Review response |
| 9 | Address second automated review of PR #3614 | medium | management session | none | Complete — see Second review response |
| 10 | Address third automated review of PR #3614 | medium | management session | none | Complete — see Third review response |
| 11 | Address fourth automated review of PR #3614 | medium | management session | none | Complete — see Fourth review response |
| 12 | Rebase onto develop; address fifth automated review of PR #3614 | medium | management session | none | Complete — see Fifth review response |
| 13 | Rebase again; record the sixth automated review's documentation batch | low | management session | none | Complete — see Sixth review response |

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

### Step 8: response to the automated review of PR #3614 (2026-08-03)

Ten items were raised. One was a real bug, two were
documentation corrections, five were adopted improvements, one
was investigated and rejected on measurement, and one was
informational.

**Fixed — capacity rows were being created per node, not per
hypervisor.** The reconciler keyed candidate rows off the
presence of a `node_metrics` row, but sf-resources runs on
every node and upserts unconditionally, so network-only and
database-only nodes were getting capacity rows and their
unschedulable capacity was being summed into
`cluster_capacity.total_*`. Inert this release, but it would
have made the soak dashboards — the entire point of the phase
— report wrong totals, and become an admission bug in phase 3.
Fixed by projecting `is_hypervisor` into `node_metrics`
(v3 → v4) and filtering in SQL; see the limit-derivation
section above for the NULL-during-upgrade handling. Four unit
tests plus a re-run of the step 4 validation with a
non-hypervisor node carrying good capacity columns and a
pre-existing capacity row (it is deleted, which is the upgrade
path).

**Rejected on measurement — restricting the disk `JSON_TABLE`
derived table to the placement set.** The review's premise was
that the derived table expands every instance row, including
the deleted-but-not-hard-deleted backlog, before the outer
join discards them. MariaDB does not do that: `EXPLAIN` shows
it already resolves the derived table as `LATERAL DERIVED`
correlated on `i.uuid`, so it only expands the instances the
outer query joins to. Measured with 73,640 instance rows of
which 1,205 were placed, adding the suggested
`i2.uuid IN (SELECT ... FROM object_references)` restriction
turned the plan into a materialised `DERIVED` with a semi-join
and made the query *slower* (10 ms → 13 ms) for identical
results. A reconcile pass over that database took 44 ms, the
same as with no backlog at all. The comment above the query now
records the measurement so the change is not re-proposed.

**Adopted:**

* The reconcile RPC now uses `BOUNDED_QUERY_TIMEOUT` with one
  retry rather than the default 3 × 30 s budget. Its caller is
  the elected loop that pets the systemd watchdog itself, and
  its server side is one analytical query — the exact shape
  that SIGABRTed non-database daemons in issue 3586. A skipped
  pass is harmless because the next one recomputes everything.
  That justification is release-scoped: once phase 3's guarded
  UPDATEs make the recompute a drift correction, a reconciler
  persistently exceeding its deadline means drift accumulates
  indefinitely with only a counter and a frozen last-success
  gauge as signal. Phase 3 must pair enabling the counter
  guard with a persistent-failure decision — either raise the
  reconciler's budget above `BOUNDED_QUERY_TIMEOUT` (it is not
  on a watchdog-critical path, since `_run_due_scheduled_jobs`
  pets before each job), or treat consecutive failures as a
  condition that disables the guard rather than admitting
  against stale counters.
* `SCHEDULER_CAPACITY_LAST_DURATION` is now set before the
  failure return, so a slow-then-failing pass reports its own
  duration instead of the last successful one.
* The task is decorated with `@util_general.recorded_method`
  like its five-minute siblings.
* The capacity gauges are cleared when the cluster daemon
  leaves the elected loop. They describe singleton cluster
  state, so a demoted node that kept publishing would
  contradict its successor. `docs/operator_guide/database.md`
  now tells operators to alert on the last-success timestamp
  going stale rather than on a gauge disappearing.
* Round-trip test coverage of the servicer's reply
  construction and the client's field-by-field unpacking, so a
  proto/dict key rename fails in CI rather than on a live
  cluster, plus the `result is None` and unexpected-exception
  branches.

**Documentation corrections:** the `used_cpus` parity claim
(above) and the missing `SCHEDULER_DEMAND_*` entries in
`docs/operator_guide/scheduler.md`.

**Found while fixing the gauge staleness, outside the review's
scope:** the elected loop registered its `schedule.every()`
jobs on every election without clearing them first, and
`schedule` keeps a module-global job list — so a node elected
twice ran every maintenance task twice per cadence, three
times after a third election, and so on. Fixed with a
`schedule.clear()` before registration. Pre-existing, not
introduced by this phase, but it would have doubled up the
reconcile pass this phase adds.

### Step 9: response to the second automated review (2026-08-04)

Nine items. One real bug of the same class as the first
round's, one stale documentation reference, and seven
improvements — all adopted this time.

**Fixed — capacity rows for nodes the scheduler would never
consider.** The only node-state exclusion was `deleted`, but
`scheduler.py` builds its candidates from
`Nodes([], prefilter='active')`, so errored, missing, stopping
and stopped nodes are not placement candidates either. A
hypervisor taken out of service by the node-health cascade
kept its row and its full limits stayed in
`cluster_capacity.total_*`. Exactly the defect the first
review found with `is_hypervisor`, in a second dimension — so
the fix generalises rather than patches: the filter is now
`NOT IN` the active set, and the active set moved to
`constants.NODE_ACTIVE_STATES` with `Node.ACTIVE_STATES`
defined from it, so the reconciler can read it in SQL without
importing `shakenfist.node` and the two definitions cannot
drift. See the limit-derivation section for all three filters.

**Also fixed — the same class again, one dimension further
out.** Review item 3 noted `node_metrics` rows are only
deleted when the node is, so a hypervisor whose resources
daemon has died keeps contributing its last-known limits
indefinitely while the node itself still looks healthy. The
review judged this largely subsumed by the state filter, but
it is not: a node whose resources daemon dies while its
sentinels keep reporting stays in an active state. Metrics
older than `RECONCILE_METRICS_MAX_AGE_SECONDS` (15 minutes)
are now ignored, with the window chosen well above the
five-minute cadence for the flapping reason the review itself
gave.

**Adopted:**

* README and `docs/operator_guide/installation.md` both
  advertised MariaDB 10.6.0+. The review found the README one;
  the installation guide was a second stale reference it
  missed.
* The `_derive_cpu_memory_limits` docstring claimed
  `limit_memory_mb` mirrors `_has_sufficient_ram`. It does
  not: it blends that check's overcommit ceiling with the
  reservation term from the separate free-memory check.
  Reworded here, in the docstring and in AGENTS.md.
* Both `object_references` queries now constrain
  `source_object_type` and `target_object_type` as well as
  `relationship`. Defensive only — nothing writes an
  `INSTANCE_LOCATION` row with other endpoint types — but the
  table's indexes lead with those columns so it is free.
* The servicer checks `context.is_active()` before starting a
  pass, so a caller that has already hit its deadline does not
  get work done on its behalf. The docstring records *why*
  overlapping passes are benign today (sole writer, idempotent
  recompute) and that phase 3 ends that property.
* `_disk_spec_virtual_gb`'s docstring no longer describes the
  JSON_TABLE fallback decision as open; it is now stated to be
  the executable specification, and a live test asserts it
  agrees with the SQL on the same payloads, making it an
  oracle rather than prose.
* Schedule registration is hoisted out of the election loop
  entirely, which was the review's preferred fix over the
  `schedule.clear()` added in step 8. `schedule.every()`
  computes each job's next run at registration, so
  re-registering on every election restarted every period from
  zero — on a cluster whose maintenance lock changes hands
  more often than daily, `prune_events` might never have run.
  Registering once at daemon start fixes the duplication and
  the timer reset together, and `run_pending()` is still only
  called while elected.

**New CI coverage (review item 7).** Every reconcile test ran
against a mocked connection and asserted on compiled SQL text,
which cannot catch the failure modes that matter: the
JSON_TABLE aggregation, the dashed/undashed uuid joins, the
nullable-BOOL and NOT-IN filters, and the two upserts. All of
those fail as *silently wrong numbers* rather than errors —
pitfall 6 in CLAUDE.md exists precisely because a mismatched
uuid comparison matches nothing instead of raising. Step 4's
docker validation covered them once, by hand, and would not
catch a regression six months from now.
`shakenfist/tests/test_mariadb_capacity_reconcile_live.py` now
runs the real SQL against a real MariaDB with the step 4
fixture (11 tests), driven by
`tools/ci-capacity-reconcile-test.sh` from a new "Scheduler
capacity reconciler" job gated by `can_merge`, following the
existing `schema_enum_widening` pattern. Verified as a real
guard rather than a passing formality: removing the
`REPLACE()` transform from the usage join fails 8 of the 11.

### Step 10: response to the third automated review (2026-08-04)

Ten items, all adopted.

**Fixed — the phantom capacity row (item 4), which is the fourth
instance of one pattern.** Existence in the `nodes` table gated
removal but not creation, so a `node_metrics` row that outlived its
node's static and state rows read as a fresh hypervisor: it got a
capacity row, then appeared in both `previous` and `metrics_rows` on
every later pass, so no removal condition ever fired and its limits sat
in the cluster totals permanently. `candidates` is now intersected
with `known_nodes` and the removal condition is unconditional.

That is now four rounds of the same defect — role, state, freshness,
existence — each one a filter the scheduler applies that the
reconciler did not. **The generalisation for phase 3 is that the rule
is "a capacity row exists only where the scheduler could place", and
any new scheduler-side filter is automatically a reconciler-side
filter too.** A live test seeds an orphaned metrics row and asserts no
row is created on the first pass or any later one; reverting the fix
fails it.

**Fixed — the alerting advice was self-defeating (item 1).**
`docs/operator_guide/database.md` told operators to alert on
`scheduler_capacity_reconcile_last_success_timestamp` going stale, but
that gauge is deliberately *not* cleared on demotion (it records this
node's own last pass, which is useful for debugging, and being
unlabelled it cannot be removed the way a label set can). A
per-instance staleness alert would therefore fire forever on every
node that has ever held the lock. The docs now give the cluster-scoped
expression, `time() - max(...) > 900`, and explain why the
aggregation is load-bearing rather than stylistic.

**Adopted:**

* Watchdog pets between scheduled jobs, not just around the batch
  (item 2). Hoisting registration in step 9 fixed the timer reset but
  guaranteed that a node elected after hours of idling finds all nine
  jobs due at once, including the three heaviest, inside a single
  un-petted `run_pending()` with `WatchdogSec` at 60s.
  `_run_due_scheduled_jobs()` mirrors `run_pending()`'s ordering and
  due-check while petting before each job, bounding the exposure to
  one job instead of nine.
* `DatabaseUnavailable` is caught in
  `_grpc_reconcile_scheduler_capacity` and logged at WARNING (item 3).
  The bounded budget adopted in step 8 makes exhausted retries the
  *expected* outcome of a loaded or restarting database tier, but it
  escaped the `except grpc.RpcError` and landed in the scheduled
  task's `ignore_exception()` — an ERROR with a traceback and a
  recorded-exception file every five minutes, for a condition the
  design calls harmless, against a phase whose stated functional gate
  is cluster CI's log-error checks.
* `_node_metric_to_bool('')` now raises rather than returning False
  (item 10), so an empty string extracts as NULL and routes into the
  same no-evidence handling as a missing key. Unreachable today, but
  the falsey list had quietly made "no value" mean "confirmed
  non-hypervisor", which deletes a capacity row.
* The mock router now reports the number of uuids the DELETE actually
  names rather than a hardcoded 2 (item 7), so three
  `nodes_removed` assertions test the reconciler rather than the
  fixture. They now assert 1, which is what their single-row fixtures
  can actually produce.
* Both live-test CI scripts assert that tests actually ran (item 8).
  Every test in those modules is `@unittest.skipUnless(DSN)`, so a
  broken export turned the job into a green tick over zero tests —
  the guard silently not guarding, which is precisely the failure mode
  these jobs exist to prevent. Fixed in
  `ci-enum-widening-test.sh` as well as the new script, since it has
  the identical shape and the same hole. Verified by running with the
  DSN unset: 12 skipped, and the guard fails the script.
* Documented: that the scheduler mirroring covers the arithmetic but
  not `_schedulable_threads()`/`_memory_reserved_mb()`'s pre-upgrade
  fallbacks, so mid-upgrade a node can be schedulable with no capacity
  row (item 5); and that the metrics-freshness comparison spans hosts,
  making severe clock skew a fifth way to lose a capacity row (item
  6), which is forced by `node_metrics.timestamp` being a
  client-written float rather than a server `TIMESTAMP`.

**Recorded for phase 4 (item 9).** Node capacity rows are
intentionally reaped by the reconciler rather than by
`Node.hard_delete()`: this phase's invariant is that the reconciler is
the sole writer of these tables, and a second writer would contradict
it for at most five minutes of staleness. That trade stops being
obviously right once `namespace_claims` holds real rows, so when the
claims API lands, `Namespace.hard_delete()` should clean up its
claims — a namespace's claim outliving the namespace is a leak, not a
staleness window. Do not read the current omission as an oversight.

### Step 11: response to the fourth automated review (2026-08-05)

Three action items and three optional suggestions. From this round on,
suggestions are explicitly *triaged* — accepted, deferred with an
owner, or declined with a reason recorded here — rather than
implicitly all-adopted, so a suggestion that resurfaces in a later
round can be answered by pointing at the record instead of by
re-litigating it.

**Fixed — a stateless node got a capacity row (item 1), the fifth
instance of the recurring defect.** The state filter was expressed
negatively (subtract nodes whose state is `NOT IN` the active set), so
a node with a `nodes` row but *no* `object_states` row appeared in
neither set and slipped through, while `prefilter='active'` makes it
invisible to the scheduler. The previous round predicted the fifth
instance would be `_has_reasonable_queue_state`; it was instead the
existing state filter's polarity — the defect class is about filter
*semantics*, not just filter *inventory*. The filter is now positive
(`state_value IN` the active set, candidates intersected against the
result), which excludes unknown states and missing state rows by the
same mechanism the scheduler does. The design section's State bullet
records the polarity rule; mock and live tests cover the stateless
case, and reverting the intersection fails the live test.

**Fixed — markdown code span split across a newline (item 4)** in
`database.md`'s clock-skew paragraph.

**Documented — the usage ledger cannot see legacy placements
(item 3).** During the one-release `node_attributes.instances`
dual-write transition, a placement written by a pre-cutover node
exists only in the legacy JSON column, which the reconciler does not
read, so mid-rolling-upgrade `used_*` under-counts. Recorded in the
`_RECONCILE_USAGE_SQL` comment block and AGENTS.md: phase 3 must not
enable the counter guard until the legacy column and its union are
removed.

**Suggestions triaged — all three accepted, because each was cheapest
now:**

* **Cluster singleton accounting (item 2): implemented, not just
  documented.** `unclaimed_used_*` was folded from the unfiltered
  usage query, so an instance stranded on an errored, demoted, stale
  or deleted node inflated the used side of a total its node
  contributed nothing to — the soak dashboards could show usage
  exceeding capacity, and phase 3 would have inherited the open
  semantics silently. The fold is now restricted to nodes holding a
  capacity row (see the recompute step 5 above); per-claim `used_*`
  deliberately stays namespace-wide, and
  `test_claim_usage_is_namespace_wide` pins the asymmetry as a
  decision. The related stale-placement edge (a lost node's
  `instance_location` row surviving `place_instance()`'s
  best-effort removal, double-counting the instance at node scope)
  is out of the reconciler's scope and now benign at cluster scope;
  it belongs to phase 3's placement work.
* **BIGINT counters (item 5): adopted.** The proto fields are int64
  and the tables are new in this release, so widening is free now and
  a migration later; `cluster_capacity.total_memory_mb` as INT would
  overflow around 700 TB of overcommitted RAM with an
  every-pass-fails-at-WARNING failure mode.
* **CancelJob (item 6): honoured.** `_run_due_scheduled_jobs()` now
  mirrors `Scheduler._run_job()`'s cancel handling, making its
  "semantics are unchanged" docstring true rather than amending the
  docstring to document a divergence. The raising-job behaviour
  (propagate, skip the rest of the batch, stay due) is pinned by a
  test as well.

**Test-coverage suggestions triaged:**

* Stateless-node live test: added (required by item 1).
* Cluster-asymmetry and namespace-wide-claim tests: added (item 2).
* Raising-job behaviour test: added (item 6).
* Registration-happens-once test: **declined.** Registration is
  inline at the top of `_run_inner()`, which never returns; asserting
  on it would require extracting a registration helper purely for the
  test's benefit, and `_run_inner` is only called once per process by
  construction (`Daemon.run`). The behaviour is documented in the
  registration comment instead.
* Namespace-over-claim-limit live test: **deferred to phase 4.** The
  overage gap is already recorded as a phase 4 obligation in the
  claim-accounting decisions above; a test asserting today's
  incomplete accounting would have to be rewritten by the same change
  that closes the gap, and nothing consumes the numbers until then.

### Step 12: rebase and the fifth automated review (2026-08-07)

The branch was rebased onto develop (which had gained the federation
abuse-resistance work and a generalised live-MariaDB CI job). Two
rebase reconciliations worth recording:

* develop added `reap_federation_records` to the old in-election-loop
  schedule registration; it now registers in the hoisted
  once-per-daemon block with the others.
* develop generalised `tools/ci-enum-widening-test.sh` to run *every*
  `test_mariadb_*_live` module behind one MariaDB install, precisely
  so a later live module needs no new job. That made this branch's
  dedicated "Scheduler capacity reconciler" job an exact duplicate
  (same runner class, same MariaDB install, same tests twice per
  merge), so the job and `tools/ci-capacity-reconcile-test.sh` are
  removed; `test_mariadb_capacity_reconcile_live.py` runs in the
  generalised job, which also carries the no-tests-actually-ran
  guard this branch added in round 3.

The review itself: two fix items (both genuinely latent bugs — the
round-over-round trend is 3 → 2 and nothing this round was fallout
from earlier fixes), three considers (all accepted, each cheapest
now), two informational.

**Fixed — enum bindings leaned on a case-insensitive collation
(item 1).** `_reconcile_fetch_usage()` bound enum member *names* for
all four predicates, but only `object_states.object_type` (a native
`sa.Enum`) stores names; the `object_references` type and relationship
columns are plain strings written by `_direct_record_relationship()`
as `str(member)`, which for these str-subclass enums is the *value*.
The two spellings differ only in case, so the query matched under the
default utf8mb4 collations and would silently produce zero usage under
`utf8mb4_bin` or any `_cs` collation — all of which
`verify_mariadb_compat()` accepts, and the same pass's demand query
already bound values, so usage and demand would have diverged
silently. The bindings now name each column's convention explicitly
(with a fourth parameter, since the object_states join and the
object_references predicate can no longer share one), and the live
suite now runs its tables under `utf8mb4_bin` so this whole class
fails loudly: mutating one binding back to `.name` fails 9 of the 15
live tests. A unit test also pins `str(member) == member.value`, the
property the write path and the bindings both depend on.

**Fixed — a node with no `node_metrics` row at all kept its capacity
row forever (item 2).** The sixth instance of the recurring class and
the freshness twin of round four's state-filter bug: the filter
subtracted a stale set, and a node with *no* metrics row was in
neither the fresh nor the stale set. Reachable because sf-resources
deletes its own node's rows at daemon startup before the first
upsert. The filter is now membership in a fresh set (derived without
the `is_hypervisor` predicate, so the NULL-flag mid-upgrade retention
is preserved), and the design invariant gains a third clause: filters
are expressed as membership in a positive qualifying set, never as
absence from a disqualifying one. Mock and live tests cover the
no-row case; disabling the freshness removal fails the live test.

**Suggestions triaged — all three accepted:**

* Per-claim recompute loop (item 3): a `# phase 4:` comment now marks
  the one-UPDATE-per-claim loop as a shape to replace with a set-based
  `UPDATE ... JOIN` when claims become real, not to inherit.
* Disk-size oracle rounding (item 4): probed MariaDB's
  JSON-number-to-BIGINT cast on a real server — it rounds half away
  from zero (10.5 → 11, -2.5 → -3), including numeric strings
  ('8.7' → 9), where the helper truncated integers and zeroed
  fractional strings. `_disk_spec_virtual_gb()` now matches via
  `decimal.ROUND_HALF_UP` (Python's `round()` is banker's and would
  itself diverge at .5), the messy fixture gained fractional sizes so
  the live oracle assertion exercises the divergence, and unit tests
  pin the probed values. Booleans (JSON true → 1) and JSON null
  (→ SQL NULL, ignored by SUM) were probed too and already agreed.
* Negative `limit_memory_mb` (item 5): clamped at zero, matching
  `_derive_disk_limit_gb()`'s headroom clamp, with the docstring
  explaining why zero is the right encoding of "admits nothing".

**Also repaired in passing:** a rebase-conflict resolution error left
a stray conflict marker in `protos/database.proto` in the rebased
history, which made `grpc_tools.protoc` fail — and `_make_stubs.sh`
has no `set -e`, so its sed import-fixing loops ran anyway and
stacked `from shakenfist.protos` prefixes onto the stale generated
files. The proto is fixed and the stubs regenerated at the head;
intermediate rebased commits retain the broken generated files, which
matters to `git bisect` but not to the PR diff or CI.

### Step 13: the sixth automated review (2026-08-08)

The sixth review raised **zero fix items** — the landing round under
the re-review exit rule. One documentation item, six considers (five
taken, one already-covered), two informational, and six "what's good"
entries including an endorsement of the positive-membership clause as
"the clause that actually kills the class".

**Documented — duplicate placements double-count into per-claim
`used_*` (item 1).** Round four's "benign" verdict on the
stale-placement edge was scoped to cluster accounting without saying
so; per-claim `used_*` is deliberately namespace-wide and therefore
exactly the counter the closed-accounting fix does not protect. Now
recorded as the third phase-4 claim-accounting bullet above and
mirrored in the `_RECONCILE_USAGE_SQL` comment block.

**Considers taken (items 2, 3, 5, 6, 7):** baseobject.py now imports
`NODE_ACTIVE_STATES` instead of carrying the third hardcoded copy of
the set (and its comment no longer claims errored nodes are included
when they were not — a pre-existing comment/code contradiction);
node.py's comment stops instructing maintainers to hand-sync a copy
that no longer exists; the schema tests assert `sa.BigInteger` rather
than `sa.Integer` (which BigInteger satisfies, leaving round four's
deliberate widening unpinned); `_reconcile_fetch_demand`'s docstring
records that its window comparison spans hosts like the metrics
freshness check, with the negative-age clamp as mitigation; and the
live test module docstring states the `--serial` requirement the
collation flip depends on.

**Consider recorded rather than coded (item 8):** the
`BOUNDED_QUERY_TIMEOUT` justification is release-scoped; the adopted
bullet above now carries the phase-3 pairing requirement (raise the
reconciler budget or disable the guard on consecutive failures).

**No action (items 4, 9):** the MariaDB 10.11 floor bump was verified
correct and fully documented by the reviewer; the lazy `should_run`
evaluation in `_run_due_scheduled_jobs` was judged benign and
arguably better (a job falling due mid-batch runs in the same batch).

No further re-review was requested: the round contained no fix items,
and the changes above are comments, docstrings, plan text and one
test-assertion tightening.

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
