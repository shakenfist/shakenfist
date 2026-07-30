# Scheduler reservations phase 0: research and decisions

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read the
current scheduler (`shakenfist/scheduler.py`) including the
phase 00a load-aware ordering changes, its callers
(`shakenfist/external_api/instance.py`,
`shakenfist/external_api/admin.py`,
`shakenfist/operations/node_inst_netdesc_op.py`), the
`node_metrics` table and the resources daemon, the SQL-pushdown
pattern from `PLAN-sql-pushdown-filtering`, the cluster-lock
leasing pattern in `shakenfist/locks.py`, namespace handling in
`shakenfist/namespace.py` and `external_api/auth.py`, and the
instance lifecycle states. Read the conductor in
shakenfist/private-ci (`conductor/provisioner.py`,
`conductor/db.py`, `conductor/sizing.py`) to understand the
consumer this design must serve. Ground your answers in what
the code actually does today rather than guessing.

Where a question touches on external concepts (database
isolation levels, conditional-INSERT idioms, InnoDB row
locking, OpenStack's scheduler-vs-placement split, Kubernetes
ResourceQuota semantics), research as needed. Flag uncertainty
explicitly rather than guessing.

## Situation

The master plan (`PLAN-scheduler-reservations.md`) proposes
atomic scheduling via a `node_reservations` table. Since it was
drafted, operational experience and a design discussion
(2026-07-30) have sharpened the requirements:

1. **The dominant sfcbr CI failure mode is admission, not
   placement.** Workflows that build entire nested clouds
   allocate far more than their runner label suggests, and the
   conductor's `MAX_WORKERS` cap cannot see that. Jobs are
   admitted that cannot fit, and fail at instance-build time
   ("schedule N, fail on N-1").
2. **A conductor-side capacity ledger was considered and
   rejected.** The conductor is a single serial admission
   controller today, so it could hold reservations in its own
   database -- but its claims would be invisible to Shaken
   Fist's scheduler, so any other tenant (most concretely: the
   operator manually building a test cloud) races in-flight CI
   jobs' not-yet-created instances. Multiple uncoordinated
   schedulers is exactly the condition that justifies a
   DB-atomic primitive, and it is a real condition on sfcbr,
   not a hypothetical.
3. **The natural conductor-facing primitive is a
   namespace-scoped capacity claim**, not (only) the master
   plan's per-scheduling-decision reservation: created before
   any instance exists (at runner-namespace creation time,
   sized from the size label initially and from measured
   footprints later), drawn down incrementally as instances are
   created in that namespace, released at namespace teardown.
   Each conductor runner is confined to its own namespace by
   construction, so the claim boundary is enforceable.
4. **A namespace claim can unify reservation and quota.** One
   row is simultaneously a guaranteed floor (the scheduler
   treats the claim as consumed capacity cluster-wide) and an
   enforceable ceiling (creates in the namespace beyond the
   claim are rejected). The ceiling makes footprint
   declarations honest: a mis-sized workflow fails at its own
   quota with a clear error, instead of silently eating
   capacity other admitted jobs were counting on.
5. **Measurement groundwork is in place.** The conductor now
   records each namespace's peak concurrent allocated footprint
   (vCPUs, RAM, disk, instance count) at teardown
   (private-ci `workflow_costs.peak_allocated_*`, 2026-07-30).
   This accumulating data is the sizing input for claims and
   should inform the decisions below (e.g. realistic claim
   sizes, TTLs, headroom).

## Mission and problem statement

Produce a decisions document that resolves the master plan's
open questions 1-13 plus the namespace-claim questions below,
and re-cut the master plan's phase table accordingly. No
implementation in this phase beyond throwaway benchmark
scripts; the deliverables are decisions with reasoning, a
benchmark result for the atomicity-primitive choice, and the
re-cut phase plans' scopes.

## Additional open questions (beyond master plan 1-13)

14. **Namespace claims versus per-decision reservations.** Are
    these one table with two row shapes, two tables, or is the
    per-decision reservation subsumed (an instance create
    inside a claimed namespace draws down the claim atomically;
    an instance create outside any claim takes the per-decision
    path)? What does the scheduler's effective-capacity query
    look like when both exist? Does a namespace claim need node
    affinity (a claim pinned to specific nodes) or is it
    cluster-wide with placement decided per-instance at create
    time? Cluster-wide is simpler but can strand a claim that
    fits in aggregate yet not on any single node for a given
    instance -- probably acceptable for CI-sized instances,
    but decide explicitly.
15. **Claim lifecycle and API surface.** Who may create claims
    (admin only? namespace owners up to a cap?), how are they
    expressed in the REST API and client, what does
    `hard_delete()` of a namespace do to its claim, and what
    is the TTL / refresh story for a claim whose namespace
    outlives its workflow? The conductor creates and deletes
    namespaces already; the claim should ride that lifecycle
    with an expiry backstop modelled on `cluster_locks`.

    *Working position (2026-07-30 discussion), for phase 0 to
    validate rather than reopen:* claims are first-class,
    mutable SF objects (uuid, namespace, dimensions, expiry,
    events, `hard_delete()`) with normal REST CRUD and
    `sf-client reservation create/show/update/delete` verbs,
    because a wrong initial guess is the expected case, not an
    edge case. The subtlety is that **growing a claim is
    itself an admission decision**: the delta must fit exactly
    as if it were a new claim, so update uses the same
    conditional-write primitive with the same atomicity and
    the same "no candidate" failure mode as creation.
    Shrinking is always allowed, floored at the namespace's
    current drawn-down allocation. No auto-grow: a ceiling
    that stretches on demand stops keeping declared
    footprints honest, which is its job. For CI the
    correction loop is automatic anyway -- the conductor
    re-sizes the next run's claim from measured
    `peak_allocated_*` data.
16. **Ceiling enforcement semantics.** Is the quota ceiling
    hard (create rejected with a clear error naming the claim)
    or advisory-first for a transition release? What error does
    the API return, and what event is logged (this rejection
    event is the signal that a workflow's declared footprint
    needs revision)?

    *Working position (2026-07-30 discussion):* enforcement is
    **admission-time accounting only**, which is also all that
    Kubernetes ResourceQuota is (API-server accounting, no
    runtime mechanism). Runtime enforcement is mostly
    unnecessary for VMs because, unlike containers, VMs are
    not porous: a 4-vCPU guest physically cannot use more
    than ~4 host threads (plus small qemu emulator/iothread
    overhead) and qemu's RAM allocation is fixed at start, so
    the admitted allocation *is* the runtime ceiling by
    construction. Disk is the one leaky dimension (qcow2
    grows after admission) and is closed by claiming virtual
    size from `disk_spec`. If per-host noisy-neighbour
    shaping is ever wanted, libvirt `<cputune>` and cgroup
    resource partitions (`<resource><partition>` placing a
    namespace's domains in a named slice per host) exist --
    but that is QoS, not quota, is per-host only (cgroups
    cannot aggregate across machines; Kubernetes cannot
    either), and belongs in future work. `memtune` hard
    limits are ruled out outright: the host OOM-killing qemu
    is worse than any overcommit it prevents. Ship the
    ceiling advisory-first for one release (exceeding a claim
    logs a loud event but admits) so learned footprints
    calibrate against reality before rejections start; the
    rejection error must carry claim size, current drawn-down
    usage, and the shortfall so "grow the claim by X" is the
    mechanical next step.
17. **Unclaimed-namespace behaviour and operator UX.** What
    happens to creates in namespaces with no claim -- today's
    behaviour (admit if capacity appears free), or does the
    scheduler reserve headroom for claims only? The operator
    building a manual test cloud should have a one-line path
    (`sf-client reservation create --namespace test ...`) and a
    graceful degradation when they skip it.

    *Working position (2026-07-30 discussion):* claims are
    **opt-in**, and unclaimed namespaces become best-effort.
    The floor is protected without mandating claims: effective
    capacity for an unclaimed create is
    `total - sum(active claims) - allocations of unclaimed
    instances`, giving two service classes analogous to
    Kubernetes Guaranteed vs BestEffort -- claimed capacity is
    honoured absolutely, unclaimed workloads compete for the
    remainder and can be squeezed when claims cover the
    cluster. Opt-out (mandatory claims) is rejected: every
    existing namespace would need a migration default, and the
    only safe default is unlimited, which is opt-in with extra
    steps. Operator-*imposed* claims (an admin fencing an
    untrusted namespace with a ceiling it did not request) are
    the same object created by a different actor and are
    explicitly future work for multi-tenant deployments, not
    part of this plan.
18. **Conductor integration contract.** Exactly what the
    conductor calls at runner-namespace creation: claim
    sizing from size label vs measured `peak_allocated_*`
    (worst run plus headroom), behaviour when the claim cannot
    be granted (defer the runner, run smaller queued jobs
    first, anti-starvation aging), and what claim telemetry
    the conductor dashboard should show. This phase decides the
    contract; the conductor-side implementation lands in
    private-ci separately.
19. **Batch create inside a claim.** Does the claim make the
    master plan's batch-create API (phase 5) less urgent for
    CI (capacity is guaranteed before the job starts), and is
    batch-create then primarily the manual-tenant / general-API
    story? Re-scope phase 5 accordingly.

## Decisions

Drafted 2026-07-30 from the step 1 findings
(PLAN-scheduler-reservations-phase-00-findings.md) and the
step 2 benchmark. Status: **reviewed and approved by the
operator 2026-07-30** (step 7). Numbers that depend on step
3's `peak_allocated_*` data are marked *revisit when data
lands* and are recorded as provisional defaults, not
blockers; the step 3 analysis (~2026-08-13) lands as an
addendum revising those constants only.

### The design in one paragraph

Three materialised-counter tables replace the master plan's
`node_reservations` row-per-decision design:
`scheduler_node_capacity` (one row per hypervisor: limits
derived from promoted `node_metrics` columns minus per-host
reservations, times overcommit; `used_*` maintained by guarded
UPDATEs), `namespace_claims` (one row per claim: a first-class
SF object that is simultaneously guaranteed floor and quota
ceiling), and a `cluster_capacity` singleton (totals, sum of
claim limits, and best-effort usage, so both claim admission
and unclaimed-create admission are single-row guards). Every
admission — instance placement, claim creation, claim growth —
is a guarded single-row `UPDATE ... WHERE used + x <= limit`
(or a short canonical-order transaction of them inside one
`sf-database` RPC), with `rowcount == 0` meaning "did not
fit". There are **no per-decision reservation rows**: the
placed instance itself is the record of the drawdown, releases
are explicit guarded decrements at `hard_delete()` / failed
create, and a periodic reconciler recomputes counters from
placed instances to correct drift, so nothing can leak
capacity for longer than one reconciler period.

### Master plan questions 1-13

**D1 (Q1, atomicity primitive): guarded single-row UPDATE on
materialised counters — neither of the question's two
candidates.** The step 1 probes and step 2 benchmark disprove
both offered shapes: conditional `INSERT ... SELECT` is
silently wrong under READ COMMITTED (the aggregate guard reads
a snapshot) and livelocks under REPEATABLE READ (32-way: 6,730
deadlocks in 8 s, 15 grants); `SELECT SUM(...) FOR UPDATE` is
wrong under RC and deadlocks under RR at 2-way. The guarded
UPDATE has statement-level atomicity in autocommit, behaves
identically at RR and RC (a real property for BYO-MariaDB
operators), takes only an index-record lock (PK equality), and
is already house style (`_direct_acquire_cluster_lock`,
`_direct_refresh_cluster_lock`). Multi-row admission (node row
plus claim row) is a transaction of guarded UPDATEs in
canonical order (`cluster_capacity`, then `namespace_claims`
by uuid, then `scheduler_node_capacity` by node uuid) executed
entirely inside a single `sf-database` RPC, retrying on
1213/1205/1020. Implementation notes carried from findings:
no `UPDATE ... RETURNING` in MariaDB, so post-claim state is a
follow-up PK SELECT; unit-test the driver's `rowcount`
semantics; reject zero-sized claims.

**D2 (Q2, schema): no reservation rows, so the question
dissolves into the three-table schema.**
`scheduler_node_capacity(node_uuid PK, limit_cpus,
limit_memory_mb, limit_disk_gb, used_cpus, used_memory_mb,
used_disk_gb, expected_demand, updated_at)`;
`namespace_claims(uuid PK, namespace, limit_*, used_*, state,
expires_at)`; `cluster_capacity(id=1, total_*, claimed_*,
unclaimed_used_*)`. Affinity-relevant fields are not needed on
any capacity row: with no pending-reservation rows, affinity
is evaluated against placed instances (via
`object_references` and metadata), exactly as today. Disk is
claimed as virtual size from `disk_spec` (the one dimension a
running VM can grow). All three tables use the undashed
CHAR(32) uuid form for keys and the reconciler's SQL handles
the dashed/undashed join explicitly (pitfall 6).

**D3 (Q3, consumption point): at `place_instance()`, in the
same transaction as the placement write.** A dedicated
`sf-database` RPC performs the guarded capacity UPDATEs and
the placement attribute write atomically, so there is no
grant-then-crash leak window at all. Placement changes without
a scheduling decision go through the same primitive: a
preflight redirect or a cleaner placement rewrite
(`daemons/cleaner/scheduled_tasks.py`) releases the old node
row and claims the new one; if the new node's guard fails,
that is a genuine reschedule, not an error to paper over.
`preflight`- or `creating`-time consumption were weighed and
rejected: both add a second bookkeeping step between decision
and placement that the reconciler would have to understand,
for no additional guarantee.

**D4 (Q4, TTL): no per-reservation TTL exists because no
per-reservation rows exist.** The only lease is
`namespace_claims.expires_at`, and it is a crash backstop, not
the release mechanism (the Kubernetes assumed-pod-TTL
double-placement lesson). Provisional default 24 hours,
caller-settable; the conductor sets roughly twice its workflow
timeout. Extending expiry is a plain update (not an admission
decision — only growing dimensions is, per D15). On expiry the
claim lapses to best-effort: floor and ceiling both end, the
reconciler transitions the claim to an expired state and logs
a loud event. *Revisit defaults when step 3 data lands.*

**D5 (Q5, reaper): a reconciler, not a reaper.** A periodic
task in the cluster daemon's elected-leader loop (existing
pattern, SPOF-free via the lock lease) recomputes every
counter from ground truth in set-based SQL: node `used_*` from
placed non-dead instances, claim `used_*` from each
namespace's instances, `cluster_capacity` from both; corrects
drift and logs it loudly (drift indicates a bug); expires
claims past `expires_at`. The `cluster_locks` steal model is
inapplicable — there are no abandoned rows to steal, and
counters plus authoritative recompute is strictly simpler.
Provisional period: 5 minutes.

**D6 (Q6, affinity model): adopt the binary form; deprecate
arbitrary weights.** Hard `require_with_tag` /
`require_without_tag` become filters; soft `prefer_with_tag` /
`prefer_without_tag` contribute ±1 per matching co-located
instance. Existing weighted specs map mechanically (positive
weight → prefer_with, negative → prefer_without) for one
transition release, then the weighted form is removed. Soft
affinity is applied as the ranking term **above** load
ordering (hard filters → affinity score → load), which also
resolves the issue-3565 class of flake where soft affinity
loses to CPU-headroom ordering. *Operator confirmation
requested: is anything beyond the CI suite using numeric
weights?*

**D7 (Q7, SQL vs Python split): filter and order in SQL,
score affinity and tie-break in Python, admit via guarded
UPDATE.** The scheduling loop becomes: one read-only SQL query
returns candidate nodes (hard constraints as WHERE over the
promoted typed columns, load-bucket ORDER BY); Python scores
affinity over that small candidate list and picks; admission
is the optimistic guarded UPDATE on the chosen node; on
`rowcount == 0` move to the next candidate (bounded retries) —
the benchmark's P3 shape, which showed no pathology. Pushing
affinity scoring into SQL was rejected: it needs
metadata/tag joins against placed instances, candidate sets
are small, and the Python side preserves diagnosability.

**D8 (Q8 and Q19, batch create): the claim is the
all-or-nothing unit; the batch-create API is deferred.** CI —
the lead motivation — gets whole-job capacity assurance from
the namespace claim before the first instance is created, so
per-request batch admission is no longer on the critical path.
Partial-fill and hold-until-fittable are rejected outright
(each adds queue-state surface SF doesn't want; deferral
lives in the caller, and the conductor already has deferral
mechanics). A future all-or-nothing multi-instance create can
be added without schema change using the benchmarked
multi-row transaction shape, so deferring costs nothing.

**D9 (Q9, audit logging): confirmed as proposed.** Success
path logs "node N won" plus a drawdown event on the claim.
Failure path runs today's verbose Python diagnostic against
the same metrics snapshot to produce per-node,
per-resource reasons at the current depth. Ceiling rejections
(D16) additionally log a structured event on the claim
carrying limit, used and shortfall — that event is the signal
a workflow's declared footprint needs revision. No existing
audit consumer breaks; events remain the interface.

**D10 (Q10, generality): scheduling-specific tables; the
idiom is the reusable artifact.** Guarded-UPDATE +
materialised counters + reconciler gets a developer-guide
write-up so future finite-resource claims (per-session
floating IPs, etc.) can copy the pattern, but no generic
claims framework is built: VXLAN IDs already have a UNIQUE
constraint, IPAM has its own reservation path, and there is
no second consumer today to design against.

**D11 (Q11, caller migration): all three callers move in one
phase, to two different surfaces.** The queue worker
(`node_inst_netdesc_op.py`) is the real admission point and
migrates to the claim-consuming placement path.
`external_api/instance.py` keeps a read-only feasibility
precheck (fail obviously-unfittable requests early, claim
nothing). `admin.py`'s capacity view becomes a read over the
typed capacity columns. No transition period runs two
placement deciders — two uncoordinated schedulers is the
disease this plan cures.

**D12 (Q12, content-aware placement): explicitly deferred,
seam preserved.** Ranking lives in Python over a candidate
list (D7), so blob locality later becomes another ranking
term; no capacity-row context is needed because there are no
reservation rows — workload context stays on the instance and
artifact objects where it already lives.

**D13 (Q13, demand feedforward): the schema carries it from
day one; the learner stays future work.**
`scheduler_node_capacity.expected_demand` accumulates the
decayed demand estimate of recently-placed instances:
placement adds `vcpus × SCHEDULER_DEMAND_PER_VCPU` (seed 2.5
from the 00a-1 measurements), and the reconciler recomputes
the node's total each pass as a linear decay to zero over
`SCHEDULER_DEMAND_DECAY_SECONDS` (provisional 600) of
instance age. Admission asks `measured_load +
expected_demand <= SCHEDULER_TARGET_LOAD × schedulable
threads`. This is the feedforward term that closes the
actuation-to-observation gap for correlated CI bursts; the
per-namespace learned demand value replaces the constant
later without schema change.

### Namespace-claim questions 14-19

**D14 (claims vs per-decision reservations): the per-decision
reservation is subsumed; claims are cluster-wide.** An
instance create inside a claimed namespace draws down the
claim and the node row in one transaction; a create outside
any claim draws down the `cluster_capacity` best-effort
guard (`unclaimed_used + x <= total - claimed`) and the node
row. Claim admission itself is the mirror-image guard
(`claimed + delta <= total - unclaimed_used`), so claims can
never oversubscribe the cluster. Claims carry **no node
affinity**: a claim guarantees aggregate capacity, not a
placement, and the stranding case (fits in aggregate, not on
any single node) is accepted for CI-sized instances — the
create fails with the normal no-candidate diagnostic and the
capacity remains claimed. *Revisit if step 3 data shows
claim-sized instances approaching node size.*

**D15 (claim lifecycle and API): the 2026-07-30 working
position is confirmed.** Claims are first-class mutable SF
objects (uuid, namespace, dimensions, expiry, events,
`hard_delete()`) with REST CRUD and `sf-client reservation
create/show/update/delete`. Growing a claim is an admission
decision using the same guarded primitive with the same
failure mode as creation; shrinking is always allowed,
floored at current drawdown (guarded by `used <= new
limit`); no auto-grow, ever — a stretching ceiling stops
keeping declared footprints honest. Claim creation is
**admin-only initially** (cluster capacity is an operator
resource; the conductor already holds admin credentials);
delegated per-namespace caps are future work. Namespace
`hard_delete()` deletes the namespace's claims, returning the
capacity via the cluster-row decrement.

**D16 (ceiling enforcement): admission-time accounting only;
advisory for one release, then hard.** As per the working
position: no runtime mechanism (VMs are not porous; qemu RAM
is fixed at start; disk is closed by claiming virtual size;
`memtune` hard limits ruled out; cgroup partitions are
future per-host QoS, not quota). The advisory release admits
over-ceiling creates but logs the D9 structured event so
learned footprints calibrate before rejections start. The
hard form returns HTTP 403 with a structured body naming the
claim uuid, per-dimension limit, current drawdown and
shortfall, so "grow the claim by X" is mechanical.

**D17 (unclaimed namespaces): opt-in claims; unclaimed is
best-effort.** Confirmed as the working position: two service
classes (claimed capacity honoured absolutely; unclaimed
workloads compete for `total - claimed` and can be squeezed),
no migration default problem, operator one-liner for manual
test clouds, today's behaviour preserved when no claims
exist. Operator-imposed claims on tenants remain future work.

**D18 (conductor contract):** at runner-namespace creation
the conductor creates a claim sized
`max(size-label default, ceil(1.2 × worst observed
peak_allocated_* for that workflow))` with expiry about twice
the workflow timeout (*constants revisit when step 3 data
lands*). If the claim is denied, the runner is deferred via
the existing deferral mechanics and retried; anti-starvation
policy lives conductor-side (provisional: once a queued job
has waited 15 minutes, stop admitting larger-claim jobs
ahead of it). Teardown deletes the claim explicitly before
`remove_namespace()` for prompt release (namespace
`hard_delete()` is the backstop). The dashboard gains claim
size vs measured peak per workflow, denial counts, and
queue-wait age. SF exposes nothing conductor-specific: the
contract is claim CRUD plus the D9/D16 events.

**D19 (batch create rescope):** folded into D8 — the claim
is CI's all-or-nothing unit; the batch API is re-scoped to a
deferred manual-tenant convenience.

## Execution

| Step | Description | Status |
|------|-------------|--------|
| 1 | Codebase and conductor research pass; write up current-state findings | Complete — see PLAN-scheduler-reservations-phase-00-findings.md |
| 2 | Benchmark claim idioms under contention (throwaway harness) | Complete — 96-cell matrix run 2026-07-30 (findings Part 3, "Step 2 benchmark results"): guarded UPDATE 0 deadlocks / 0 violations everywhere and fastest; conditional INSERT silently violates under RC and livelocks under RR; FOR-UPDATE variants wrong or 9× slower |
| 3 | Analyse accumulated `peak_allocated_*` data from sfcbr for realistic claim shapes | Blocked on data — collection deployed 2026-07-30, analyse from ~2026-08-13. Outcome lands as an addendum revising only the *revisit when data lands* constants (D4, D14, D18); it does not gate phases 1-3 |
| 4 | Draft decisions for master-plan questions 1-13 | Drafted (Decisions section below) — pending step 7 review |
| 5 | Draft decisions for questions 14-19 (namespace claims) | Drafted (Decisions section below) — pending step 7 review |
| 6 | Re-cut the master plan phase table; write scope stubs for phases 1+ | Done — master plan Execution section re-cut with per-phase scope stubs |
| 7 | Operator review of the decisions document | Complete — reviewed and approved 2026-07-30 (D1-D19 as drafted; D6's "does anything use numeric affinity weights?" drew no known users, to be re-confirmed against real deployments during phase 6) |

## Administration and logistics

### Success criteria

* Every open question (master plan 1-13, this document 14-19)
  has a decision with recorded reasoning, or an explicit
  deferral with a trigger condition.
* The atomicity-primitive choice is supported by a benchmark
  under contention, not intuition.
* Claim sizing recommendations reference real
  `peak_allocated_*` data from sfcbr.
* The master plan's phase table is re-cut and each phase has a
  scope stub.
* The conductor integration contract is written down and has
  been sanity-checked against the conductor's actual
  provisioning loop.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
