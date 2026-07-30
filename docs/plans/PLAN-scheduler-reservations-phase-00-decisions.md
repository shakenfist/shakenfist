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

## Execution

| Step | Description | Status |
|------|-------------|--------|
| 1 | Codebase and conductor research pass; write up current-state findings | Complete — see PLAN-scheduler-reservations-phase-00-findings.md |
| 2 | Benchmark claim idioms under contention (throwaway harness) | Designed (findings Part 3: threaded-with-barrier harness, 5 patterns, isolation matrix); indicative 2-way and 32-way probes already run — guarded UPDATE won, conditional INSERT broken under RC and 34% deadlocks under RR |
| 3 | Analyse accumulated `peak_allocated_*` data from sfcbr for realistic claim shapes | Blocked on data — collection deployed 2026-07-30, needs ~2 weeks of runs |
| 4 | Draft decisions for master-plan questions 1-13 | Not started |
| 5 | Draft decisions for questions 14-19 (namespace claims) | Not started |
| 6 | Re-cut the master plan phase table; write scope stubs for phases 1+ | Not started |
| 7 | Operator review of the decisions document | Not started |

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
