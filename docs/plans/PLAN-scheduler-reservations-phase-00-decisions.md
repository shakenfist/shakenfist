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

Drafted 2026-07-30 from the [step 1 findings](PLAN-scheduler-reservations-phase-00-findings.md) and the
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
ordering (hard filters → affinity score → load). *Operator
confirmation requested: is anything beyond the CI suite using
numeric weights?*

**Correction, 2026-08-16: this decision does not close issue
3565, and the earlier text here claiming it did was wrong.**
The ranking precedence described above already landed, as PR
3722 (`scheduler.py:611-631`), and 3565 has recurred five
times since. The audit trail from the 2026-08-15 occurrence
([run 31911823880](https://github.com/shakenfist/shakenfist/actions/runs/31911823880))
shows why: the node affinity wanted was removed by
`sufficient_idle_cpu`, an **admission** filter at
`scheduler.py:473-481`, thirty lines before affinity scoring
begins at `scheduler.py:505`. Ranking cannot rank a candidate
that is no longer in the candidate list. "Hard filters →
affinity score → load" is already the ordering; the
hard-filter step is what eats the node.

*Line references in the paragraph above are as of 2026-08-16.
The current locations are `:489-502` and `:529-600`; the hard
ceiling cited below is now `:187-256`; and PR 3722's ranking
precedence, cited above as `:611-631`, is now the `narrowed`
block at `:637-658` -- that one moved* and *is a different
block from the affinity scoring at `:529-600`, so do not read
the two as interchangeable. Each is named by symbol as well as
by number, because a set of line numbers is what went stale
here in the first place and a second set carries the same
decay: `:489-502` is the `sufficient_idle_cpu` stage inside
`find_candidates()`, `:529-600` is the block under the comment
"Filter by affinity, if any has been specified", `:187-256` is
`_has_sufficient_cpu()` where `hard_max_cpus` is applied, and
`:637-658` is the block whose comment begins "The two filters
above are load shedding, not admission". Re-derive from the
names when the numbers next move. Also, per a 2026-08-26
comment on 3565, the stage which binds has moved from
`sufficient_idle_cpu` to `sufficient_idle_memory` -- the
mechanism is unchanged, an admission filter emptying the set
before ranking, but a fix aimed only at CPU admission would
now miss.*

Closing 3565 therefore needs a decision this document has not
taken: **may a soft affinity preference bid against a hard
admission ceiling?** Today `hard_max_cpus` is absolute
(`scheduler.py:260`).

*All three positions below were disposed of on 2026-08-29 by
the phase 6 plan's F2 and F7, which found the traced mechanism
to be a candidate set of one, and none of the three to address
it. They are kept as written, because a decision record should
show what was on offer.*

Three positions, for phase 6 to choose between:

1. **Hard require only.** `require_with_tag` turns silent
   mis-placement into an explicit no-candidate refusal.
   Honest and diagnosable, but a co-location request against
   a node at its ceiling still fails rather than succeeding.
2. **Soften the ceiling above a threshold affinity score.**
   Admission consults the affinity score, so a strongly
   preferred node may be admitted past `hard_max_cpus` by
   some bounded margin. This is the only option that makes
   the co-location case work. It costs the property that
   admission is a pure capacity question, and needs a bound
   nobody has chosen.
3. **Accept that co-location is not guaranteed under
   concurrency**, and change what `test_affinity` asserts.

Two things that look like they should help and do not.
Namespace claims (D14) guarantee aggregate capacity and
explicitly carry no node affinity, so they close the 3772
507 family without touching this. And the CPU committed
ledger (PR 3724) made admission *more* accurate, which moves
this failure mode in the wrong direction.

Independently of the decision above, the CI tier topology is
sized well below the concurrency it runs at: the occurrence
above recorded `cpu_schedulable: 1` on a nested hypervisor,
so with `CPU_OVERCOMMIT_RATIO` 3.0 each node admits three
vCPUs and the three-hypervisor tier admits nine, against a
suite at `concurrency=5`. That is why the affinity target is
reliably full rather than occasionally full. The topology
lives in the `shakenfist/actions` repository and is out of
scope for this plan, but no scheduler change will make
`test_affinity` reliable while it stands.

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
placement adds `vcpus × SCHEDULER_DEMAND_PER_VCPU` (seed 0.6
from the 00a-1 measurements; *corrected 2026-08-22* — this
said 2.5, which was transcribed from that appendix's
*packing* row of 2.3-3.0 allocated vCPUs per thread rather
than its demand-per-vCPU row of 0.12-0.35 steady with a
~0.6 burst peak. The units error is the whole of issue
#3813, and phase 4a fixes it), and the reconciler recomputes
the node's total each pass as a linear decay to zero over
`SCHEDULER_DEMAND_DECAY_SECONDS` (provisional 600) of
instance age. Admission asks `measured_load +
expected_demand <= SCHEDULER_TARGET_LOAD × schedulable
threads`. This is the feedforward term that closes the
actuation-to-observation gap for correlated CI bursts; the
per-namespace learned demand value replaces the constant
later without schema change.

*Amendment (2026-08-14, phase 3):* the demand clause is a
spreader, not a capacity bound, and is never allowed to fail
a create on its own. When a candidate walk admits nowhere
and at least one candidate was refused on demand alone
(every real dimension had room), the walker re-walks with
the demand clause waived and the placement proceeds. Without
this the clause acts as a hard rate limit on small clusters:
phase 3's first smoke CI run locked its single node out
permanently (`expected_demand` 8–12 against a bound of
`0.75 × 8 = 6`) while the node sat essentially idle, failing
13 tests with 507s. See the phase 3 plan's decision P9.

*Amendment (2026-08-22, phase 4a):* the waiver stays, but it
is no longer the only path a placement ever takes. With the
seed corrected and the clause tested against the node's
existing state rather than the incoming placement (phase 4a
decision E2), the clause is satisfiable at every node size,
so the waiver firing becomes a signal that the cluster is
genuinely saturated rather than a signal that the guard is
broken.

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

*Correction (2026-08-27): the advisory release needs a
consumer, and "one release" is the wrong unit.* The point of
the advisory window is that learned footprints calibrate
before rejections start. Nothing calibrates against an empty
population: phase 4 merged on 2026-08-17 and reached sfcbr on
2026-08-22, and as of 2026-08-27 every claim that had ever
existed on the cluster was created by a test and deleted by
the same test. The gate on the hard flip is therefore phase
4c's observation record, not elapsed releases.

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

## Step 3 addendum: measured claim shapes (2026-08-13)

### Data basis, and why it is one day rather than two weeks

The conductor's 2026-08-12 deploy of per-instance cost
attribution ("Size runners from the runner, not its
namespace") bumped the cost-data generation and deliberately
discarded every generation-1 row: the old samples mixed the
runner and the nested cloud it built with no way to attribute
them after the fact. The analysis below therefore rests on 56
namespace teardowns collected over ~10 hours on 2026-08-12,
not the planned two weeks. That is enough, because the
headline empirical result is that **per-job peaks are
deterministic** — every run of the same job produced
identical `peak_allocated_*` values, since the peak is a
function of the job's topology definition, not of runtime
behaviour. Shape conclusions are therefore robust on a small
sample; tail and variability questions (and the anti-
starvation constant) are re-examined in a follow-up pass
around 2026-08-26 once a fortnight of generation-2 data has
accumulated.

Cluster context for the numbers below: sfcbr has six
hypervisors — four 12-thread and two 24-thread nodes, all
with 62 GB RAM and ~920 GB of `/srv` — so roughly 96
threads (~288 schedulable vCPUs at `CPU_OVERCOMMIT_RATIO`
3.0, before per-host reservations), 372 GB RAM and 5.5 TB
disk cluster-wide.

### Observed claim shapes

| Job (worst observed run) | vCPU | RAM GB | Virtual disk GB | Instances |
|---|---|---|---|---|
| kerbside-patches multinode | 35 | 56 | 3360 | 9 |
| shakenfist collection smoke (xl runner) | 32 | 80 | 1420 | 7 |
| shakenfist collection smoke (xs runner) | 25 | 66 | 1120 | 7 |
| kerbside-patches all-in-one | 13 | 18 | 560 | 2 |
| instar / kerbside single-VM jobs | 8 | 16 | 460 | 1 |

The runner's own allocation is included in the peak (the xl
vs xs variants of the same smoke job differ by exactly the
runner-size delta: 7 vCPU / 14 GB), which is correct — the
claim must cover the runner instance too. Workflow durations
ranged 3–70 minutes. RAM is the binding dimension for
concurrency: five concurrent cluster-sized jobs allocate
300+ GB against the cluster's 372 GB, while the same mix
uses well under half the vCPU limit.

### Constant revisions

**D4 (claim TTL): unchanged.** Observed workflow durations
top out at 70 minutes, so the conductor's "roughly twice the
workflow timeout" expiry and the 24-hour caller default both
hold comfortably as crash backstops. No revision.

**D14 (cluster-wide claims): upgraded from "simpler" to
"required".** The two largest observed RAM claims (66 and
80 GB) exceed any single sfcbr node's 62 GB — a node-pinned
claim of the most common cluster-CI shape would be
undeliverable by construction, so cluster-wide is not a
simplification but the only shape that works. The stranding
acceptance is confirmed by data: the largest single instance
in any observed topology is 8 vCPU / 16 GB (an xl runner;
cluster topologies use 4 vCPU / 12 GB nodes), at most ~26%
of a node's RAM and ~22% of its vCPU limit, so "fits in
aggregate but on no single node" requires a node already
near-full, which the normal no-candidate diagnostic handles.

**D18 (conductor sizing formula): confirmed, with the sizing
key sharpened.** Because peaks are topology-deterministic, a
single generation-2 observation is a sufficient seed for
`worst observed peak_allocated_*`, and the 1.2× headroom is
retained to absorb what actually varies: runner-size changes
and topology drift in the job definition, not run-to-run
noise. The sizing key must be **(repo, job_name)**, not
workflow name — peaks within one workflow name span 1–32
vCPU across its jobs, so workflow-level sizing would claim
the worst job's footprint for every job. The 15-minute
anti-starvation constant stays provisional; no deferral data
can exist until claims are enforced.

*Correction (2026-08-27), from the phase 4c survey.* Three
things D18 assumes are not true of the conductor as it stands,
and phase 4c decides them differently:

* **There are no "existing deferral mechanics" to defer a
  refused runner through.** The conductor's only deferral is of
  namespace *deletion*, while queued network deletes settle
  (`conductor/provisioner.py:717`). Provisioning has no
  deferral queue. The working analogue is the image-builder
  quarantine path in `create_workers()`, which skips a label
  and leaves its jobs queued for a later cycle -- simpler than
  what this decision imagined, and sufficient.
* **Expiry cannot be per-workflow.** The conductor does not
  know a job's `timeout-minutes`: GitHub's queued-job data
  carries repo, workflow, job name, labels and URL, and not
  the timeout. Phase 4c uses a flat six hours -- twice the
  longest `timeout-minutes` in this repository -- as a leak
  backstop, and relies on explicit deletion at teardown for
  prompt release. An expiry set too short is a silent fault:
  `coverage_state` flips to `expired` under a still-running
  job and its instances stop being charged to the claim.
* **The sizing data needs a new accessor, not the existing
  one.** `db.get_cost_observations()` groups by `(repo,
  workflow_name, job_name, runner_size)` with a three-run
  minimum, which is right for the runner-size recommender it
  serves and wrong for claims: this decision's sharpened key is
  `(repo, job_name)`, and the topology-determinism finding
  above is what makes a single observation a sufficient seed.

One thing D18 did not anticipate is now the main risk. Claim
*creation* is a hard guarded admission against the cluster
singleton even while the *ceiling* is advisory, and issue
#3907 recorded the functional claims tests failing three times
in one day with 507 because sibling tests held the cluster's
cpus. On a busy cluster the conductor will be refused
routinely -- which is the back-pressure this design wants, but
only if a refusal leaves the job queued rather than failing
it. Phase 4c's decision E6 turns on that.

### New finding for phase 3: disk needs an overcommit ratio

D2 claims disk as virtual size from `disk_spec`, and the
data shows virtual size over-claims actual usage by 40–140×
(median ~65×): a single collection-smoke job claims 1.1 TB
and the multinode job 3.4 TB against a 5.5 TB cluster whose
worst observed *actual* per-job fill is 49 GB. Claiming
virtual size against physical disk would reject today's
routine concurrency outright while the disks sit ~98% empty.
Phase 3 must therefore overcommit disk admission by
`SCHEDULER_DISK_OVERCOMMIT`, provisional seed **5.0** —
enough to admit the observed concurrent mix with about 2×
margin, while still bounding runaway growth two orders of
magnitude below the virtual sum the guests could
theoretically write. *(Mechanism refined during phase 3
planning, 2026-08-13: there is no total-physical-disk metric,
and phase 2's `_derive_disk_limit_gb()` already sets
`limit = used_virtual + free-space headroom`, so the constant
multiplies the headroom term of that derivation rather than a
raw physical total — see decision P3 in
PLAN-scheduler-reservations-phase-03-primitive.md. A full
disk still admits nothing.)* The physical backstop remains
`NODE_DISK_RESERVATION_GB` plus the reconciler's drift
correction; virtual size stays the claimed quantity because
it is the only number known at admission time.

## Execution

| Step | Description | Status |
|------|-------------|--------|
| 1 | Codebase and conductor research pass; write up current-state findings | Complete — see PLAN-scheduler-reservations-phase-00-findings.md |
| 2 | Benchmark claim idioms under contention (throwaway harness) | Complete — 96-cell matrix run 2026-07-30 (findings Part 3, "Step 2 benchmark results"): guarded UPDATE 0 deadlocks / 0 violations everywhere and fastest; conditional INSERT silently violates under RC and livelocks under RR; FOR-UPDATE variants wrong or 9× slower |
| 3 | Analyse accumulated `peak_allocated_*` data from sfcbr for realistic claim shapes | Complete — see "Step 3 addendum: measured claim shapes (2026-08-13)". D4 unchanged, D14 upgraded to required, D18 sizing key sharpened to (repo, job_name), and a new disk-overcommit constant flagged for phase 3. Follow-up variability pass ~2026-08-26 on a fortnight of generation-2 data |
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
