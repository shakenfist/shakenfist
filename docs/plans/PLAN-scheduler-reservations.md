# Atomic scheduling via a reservations table

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read the
current scheduler (`shakenfist/scheduler.py`), its callers
(`shakenfist/external_api/instance.py`,
`shakenfist/external_api/admin.py`,
`shakenfist/operations/node_inst_netdesc_op.py`), the
`node_metrics` table and how it is populated (the resources
daemon under `shakenfist/daemons/resources/`), the existing
SQL-pushdown pattern delivered by `PLAN-sql-pushdown-filtering`,
the cluster-lock leasing pattern in `shakenfist/locks.py`, and
the instance lifecycle states. Ground your answers in what the
code actually does today rather than guessing.

Where a question touches on external concepts (database
isolation levels, conditional-INSERT idioms, row-locking
behaviour under MariaDB / InnoDB, OpenStack's
scheduler-vs-placement-API split), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture overview
and the existing object / state subsystems. Consult `CLAUDE.md`
for build commands, project conventions, the existing "push
filtering down to the SQL layer" rule, and the lease /
`expires_at` pattern already used by `cluster_locks`.

This plan is a **placeholder**. It captures intent and the
known open questions and is intentionally light on detail.
Phase 0 will resolve the open questions into a decisions
section and the phase table below will be re-cut accordingly.

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named for the master plan with
`-phase-NN-descriptive` appended before the `.md` extension.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a single
commit.

## Situation

Today's scheduler (`shakenfist/scheduler.py`) is **in-process
and distributed**: every sf-api worker instantiates its own
`Scheduler` object and consults the shared `node_metrics`
table to decide where to place a new instance. The metrics
table is refreshed by the resources daemon every 60 seconds,
so the scheduler's view of cluster capacity is always somewhat
stale, and — more importantly — there is no coordination
between two scheduling decisions in flight on different sf-api
processes at the same instant.

This produces two concrete pain points:

1. **The "schedule N, fail on N-1" pattern.** A bulk create
   (most painfully, a CI job that wants 50 VMs) is issued as N
   sequential `POST /instances` calls. Each consults the
   scheduler against essentially the same metrics snapshot,
   the early creates pass, and somewhere around N-1 the actual
   capacity runs out — but only at instance-build time, after
   all the upstream work has been done. The cluster has wasted
   substantial effort and the operator sees a half-built job.
2. **Concurrent races on tight clusters.** Two scheduling
   decisions made simultaneously on different sf-api processes
   can both pick the same target node, since neither sees the
   other's choice. This races on capacity, but it also races
   on affinity / anti-affinity correctness — today's affinity
   logic (`scheduler.py:364-445`) scores nodes against the
   *currently placed* instances, not pending decisions.

The cleaner shape is to make "pick a node" and "claim capacity
on that node" a single atomic operation. The natural primitive
is a `node_reservations` table that the scheduler's effective
capacity view is computed against:

```
effective_capacity(node) = node_metrics(node) - SUM(active reservations on node)
```

A scheduling decision becomes a conditional INSERT into
`node_reservations` whose WHERE clause expresses every hard
constraint (fits the capacity? matches required affinity? not
on an anti-affinity-forbidden node?) in SQL — pushed down to
MariaDB exactly as the project's existing pushdown rule
prescribes. If the INSERT places a row, the reservation is
claimed atomically against every concurrent scheduler. If it
places zero rows, the candidate set was empty and the request
can be held, retried, or rejected.

The reservation is consumed when the instance is durably
placed (`Instance.place_instance()`) — note the instance
state machine is `initial → preflight → creating → created`;
there is no `building` state, and the 2026-07-30 phase 0
review decided against adding one (it would not improve
atomicity, which comes from doing the claim consumption in
the same database transaction as an existing transition).
Reservations are explicitly released on instance create
failure, with a leased `expires_at TIMESTAMP` modelled on
the `cluster_locks` pattern as a crash backstop only — not
the routine release mechanism — so stranded reservations
cannot leak capacity.

This design was chosen in preference to two alternatives:

- **Centralising scheduling in the cluster daemon
  post-election**, running serially in a single process so it
  can maintain an in-memory overlay of pending decisions.
  Rejected because it walks back the direction of
  `PLAN-remove-primary` (reducing critical-path single-point-
  of-failure roles), introduces a throughput ceiling, and
  couples a hot user-request path to a daemon whose other
  duties are background-shaped.
- **Keeping the scheduler in-process and adding a separate
  reservation log that callers read before deciding.**
  Rejected because it's the worst of both worlds — adds the
  reservation-table complexity without the atomicity that
  makes it worth having.

## Mission and problem statement

Shaken Fist scheduling becomes atomic: capacity and constraint
checks are pushed down into a single conditional INSERT
against a `node_reservations` table, with reservations
consumed at instance-build time and auto-expired via a leased
TTL. The user-facing `POST /instances` flow gains a sibling
batch-create primitive that maps "I want N instances together
or not at all" to a single transaction.

Concretely, after this plan lands:

- A `node_reservations` table holds per-decision capacity
  claims (cpus, memory, disk) plus enough context for
  constraint queries (namespace, tags / affinity intent), with
  `expires_at` for lease semantics.
- The scheduler's effective capacity view subtracts active
  reservations from `node_metrics` in SQL.
- Scheduling decisions are conditional INSERTs that either
  place a reservation atomically or return "no candidate."
- The instance lifecycle gains a "reservation consumed" point
  (working position: at `place_instance()`, with
  allocation-denominated accounting from the database, so the
  reservation window is seconds in the normal case and only
  stretches for batch creates) and an explicit release on
  create failure.
- A reservation reaper handles abandoned reservations whose
  `expires_at` has passed without consumption.
- A new batch-create API accepts a list of N instance specs
  and either places all N reservations in one transaction or
  fails the batch atomically. The shape of the user-facing
  endpoint (`POST /instances/batch`? a multi-instance
  variant of the existing endpoint?) is decided in phase 0.
- The existing in-process `Scheduler` callers
  (`external_api/instance.py:792`, `external_api/admin.py:80`,
  `operations/node_inst_netdesc_op.py:144`) are ported to
  the new primitive.
- Per-rejection audit logging is preserved as a diagnostic
  mode that runs the verbose Python-side "why didn't this fit
  anywhere?" query *on demand* or *on failure*, not on every
  successful schedule. The day-to-day audit log records "node
  N won, reservation R" and nothing else.

The principle is: **atomicity through the database, not
through serialisation**. The DB already has the primitives;
the existing project rule already says to use them; this is an
overdue application of both.

## Open questions

This plan is light on detail because almost every concrete
decision depends on a phase 0 research pass. A design
discussion on 2026-07-30 added a further set of questions
(14-19, recorded in the phase 0 plan): the conductor and
manual-tenant use cases want a **namespace-scoped capacity
claim** -- created before any instance exists, drawn down as
instances are created, doubling as an enforceable quota
ceiling -- alongside or instead of the per-decision
reservation described here. A conductor-side capacity ledger
was considered as a stopgap and rejected because its claims
would be invisible to SF's scheduler, so any second scheduler
(most concretely the operator hand-building a test cloud)
races in-flight claims; that multi-scheduler condition is
what justifies the DB-atomic primitive. The questions below
include at least:

1. **Conditional INSERT vs SELECT FOR UPDATE.** Both shapes
   work for the atomicity guarantee. Conditional INSERT is the
   more honest expression of "filter and claim in one
   operation" and probably scales better. SELECT FOR UPDATE is
   easier to read. Phase 0 picks one with explicit reasoning
   and benchmarks the chosen shape under contention.
2. **Reservation row schema.** Minimum is `(node_uuid, cpus,
   memory, disk, expires_at, owner_uuid, reservation_uuid)`.
   For affinity correctness against pending reservations the
   row probably also needs `(namespace, tags JSON)` or
   equivalent. Phase 0 decides exactly what affinity-relevant
   fields the row carries and how they participate in the
   constraint query.
3. **Reservation lifecycle states.** When precisely is a
   reservation "consumed"? The instance state machine is
   `initial → preflight → creating → created` — the
   `building` state this plan originally named does not
   exist, and the 2026-07-30 review decided against adding
   one: consumption atomicity comes from doing the claim
   decrement in the same database transaction as an existing
   write, so a new state adds upgrade and test churn without
   improving the guarantee. Working position: consume at
   `place_instance()`, with allocation-denominated
   accounting from the database (placed, non-dead instances
   count as allocation). Alternatives phase 0 must still
   weigh: at `preflight` (target node re-admission) or
   `creating` (hypervisor build start). Whatever is chosen
   must tolerate placement changing without a scheduling
   decision — preflight can redirect to another node, and
   the cleaner rewrites placement for locally-found domains
   (`daemons/cleaner/scheduled_tasks.py`) — and each option
   has different failure modes around partial creates.
4. **Reservation TTL.** What's the right default lease? Long
   enough that a slow-starting instance doesn't lose its
   capacity claim mid-create; short enough that abandoned
   reservations don't strand capacity for long. Probably
   minutes, refreshable if needed, decided in phase 0.
5. **Reaper design.** Modelled on `cluster_locks`
   self-recovery (any candidate steals an expired row), or a
   dedicated background task in the cluster daemon, or a
   trigger from the resources daemon's metrics refresh? The
   `cluster_locks` model is simpler and SPOF-free, which
   argues for repeating it.
6. **Affinity model simplification.** Today's affinity is
   arbitrary signed integer weights summed per matching
   co-located instance. There is reason to believe nobody uses
   the weighted form in practice. Phase 0 decides between
   three options: keep arbitrary numeric weights; drop
   affinity entirely; or compromise on binary soft affinity
   (`prefer_with_tag=[...]` and `prefer_without_tag=[...]`
   contributing ±1 per match, plus optional hard
   `require_with_tag=[...]` / `require_without_tag=[...]`).
   Hard constraints become WHERE clauses; soft preferences
   become ORDER BY terms. The binary-soft option drops the
   "what does weight=7 mean operationally" cognitive load
   without losing the use case of "place near my web tier."
7. **Soft preference scoring in SQL vs Python.** Hard
   constraints push down. Soft preferences (CPU load
   ordering, affinity ranking under the binary model) can
   push down too, but as the heuristic surface grows it may
   be cleaner to ORDER BY in SQL for the simple cases and
   tie-break in Python over a small filtered set. Phase 0
   picks the split.
8. **Batch-create API shape and semantics.** All-or-nothing
   is the easy case. Partial-fill ("place as many as you
   can") and hold-until-fittable ("keep the request pending
   in a queue until capacity exists") are tempting but each
   add their own state surface. Phase 0 decides what the
   user-facing primitive offers, with the CI-job-fits-as-a-
   whole use case as the lead motivation.
9. **Per-rejection audit logging.** Today's scheduler logs
   per-node per-resource rejection reasons. The pushdown
   query produces "no candidate" with no per-node story.
   Proposed: on a failed batch, run the verbose Python-side
   diagnostic against the same snapshot to produce the audit
   detail. On a successful schedule, log only "node N won,
   reservation R." Phase 0 confirms this is the right
   tradeoff and identifies any audit consumers that would
   break.
10. **Generality of the reservations primitive.** A
    capacity-style reservation table could plausibly serve
    other "claim a finite resource atomically" use cases —
    floating IPs from a pool, VXLAN IDs, network IDs, even
    the per-session floating IP idea floated in the sticky-
    transfers discussion. Phase 0 decides whether the table
    is instance-scheduling-specific or designed as a generic
    primitive from the start. The cost of generic-from-day-1
    is real; the cost of retrofitting later is also real.
11. **Migration path for existing callers.** The three
    in-process `Scheduler()` call sites are not all on the
    instance-create hot path —
    `node_inst_netdesc_op.py` runs from the queue worker, not
    the API. Phase 0 confirms each caller's expectations and
    decides whether they all migrate together or whether the
    queue-worker callers keep the old shape for now.
12. **Interaction with content-aware placement.** Future
    blob-storage work may want placement decisions to prefer
    nodes that already hold a given blob. A reservation table
    that carries enough context to express "this instance
    wants blob X" composes; a capacity-only table doesn't.
    Worth deciding whether to lay the groundwork or
    explicitly defer.
13. **Demand-denominated capacity and a learned overcommit.**
    The static `CPU_OVERCOMMIT_RATIO` (default 16, inherited
    from OpenStack's `cpu_allocation_ratio` folklore) encodes
    an assumption of many mostly-idle, uncorrelated VMs. CI
    workloads are few, large and *correlated* — every VM in a
    job compiles at full tilt simultaneously — so the honest
    admission model is load-denominated: each hypervisor has
    a target sustained load per schedulable core, and
    admission asks whether *effective load* would exceed it.
    A purely reactive controller cannot deliver this: a CI
    burst places 50 VMs in seconds, each VM contributes zero
    load while booting and ramps over minutes, `cpu_load_1`
    is a one-minute average, and the metrics snapshot is up
    to 60 seconds stale — the actuation-to-observation lag
    exceeds the burst, so a reactive scheme admits everything
    and discovers the overload minutes later. The reservation
    row is the natural feedforward term: it carries an
    *expected demand* estimate (initially vCPUs × a
    demand-per-vCPU constant, later a per-namespace learned
    value) whose contribution to effective load decays as the
    instance ages and its real demand becomes visible in
    measured load — a demand claim consumed over time,
    analogous to the capacity claim consumed at placement.
    Phase 0 must decide whether the reservation schema
    carries an expected-demand field and a decay /
    consumption rule from day one (cheap now, painful to
    retrofit), even though the learning loop that tunes
    demand estimates is explicitly future work. Phase 00a
    delivers the static stopgap (load-per-core ordering,
    core-denominated system reservations, a measured
    overcommit default) and the tracking groundwork the
    learner will need. The 00a-1 sfcbr measurements (see the
    Measurements appendix in the phase 00a plan) chose
    `CPU_OVERCOMMIT_RATIO = 3.0` — observed viable packing on
    plain nodes was 2.3-3.0 vCPUs per thread, with RAM binding
    first — and confirmed `SCHEDULER_TARGET_LOAD = 0.75`; the
    observed demand-per-vCPU range is the seed constant for
    the learner. None of this is exotic: it is demand-based
    scheduling of the kind VMware DRS and Borg have run for
    years (2026-07-17 design discussion), arrived at from the
    CI failure mode rather than the literature, and the
    static ratio is the degenerate case of the learned model,
    so nothing phase 00a shipped is thrown away when the
    learner arrives.

## Execution

Re-cut 2026-07-30 from the phase 0 decisions
(PLAN-scheduler-reservations-phase-00-decisions.md, Decisions
section). The headline change from the original provisional
cut: there is no `node_reservations` row-per-decision table
and no conditional-INSERT primitive — phase 0's benchmark
disproved both — so the schema phases now build materialised
capacity counters, namespace claims, and a reconciler
instead, and the batch-create phase is deferred out of the
table entirely (decision D8).

| Phase | Plan | Status |
|-------|------|--------|
| 00a. Load-aware ordering and system reservations (static quick wins) | [PLAN-scheduler-reservations-phase-00a-load-aware-ordering.md](PLAN-scheduler-reservations-phase-00a-load-aware-ordering.md) | Complete |
| 0. Research and decisions document | [PLAN-scheduler-reservations-phase-00-decisions.md](PLAN-scheduler-reservations-phase-00-decisions.md) | Complete |
| 1. Promote node capacity fields to typed columns | [PLAN-scheduler-reservations-phase-01-node-metrics-columns.md](PLAN-scheduler-reservations-phase-01-node-metrics-columns.md) | Complete |
| 2. Capacity tables, reconciler and migration | [PLAN-scheduler-reservations-phase-02-capacity-tables.md](PLAN-scheduler-reservations-phase-02-capacity-tables.md) | Complete |
| 3. Claim primitive and placement integration | [PLAN-scheduler-reservations-phase-03-primitive.md](PLAN-scheduler-reservations-phase-03-primitive.md) | Complete |
| 4. Namespace claims object and API | [PLAN-scheduler-reservations-phase-04-claims-api.md](PLAN-scheduler-reservations-phase-04-claims-api.md) | Complete |
| 4a. A satisfiable demand guard, and the phase 3/4 soaks | [PLAN-scheduler-reservations-phase-04a-demand-guard.md](PLAN-scheduler-reservations-phase-04a-demand-guard.md) | Complete |
| 4b. Client support for claims | [PLAN-scheduler-reservations-phase-04b-client.md](PLAN-scheduler-reservations-phase-04b-client.md) | Complete |
| 4c. Conductor claim integration | [PLAN-scheduler-reservations-phase-04c-conductor-claims.md](PLAN-scheduler-reservations-phase-04c-conductor-claims.md) | Not started |
| 5. Caller migration and hard ceiling | PLAN-scheduler-reservations-phase-05-callers.md | Not started |
| 6. Affinity model rework | PLAN-scheduler-reservations-phase-06-affinity.md | Not started |
| 7. Diagnostic-mode rejection logging | PLAN-scheduler-reservations-phase-07-diagnostics.md | Not started |
| 8. Documentation and operator guide | PLAN-scheduler-reservations-phase-08-docs.md | Not started |
| 9. Push audit | PLAN-scheduler-reservations-phase-09-push-audit.md | Not started |

### Phase status notes

Where a phase's status does not tell the whole story, the rest of it
is here.

- **Phase 00a** is implemented but has not yet soaked on sfcbr, which
  is what its status turns on.
- **Phase 0**'s decisions were approved on 2026-07-30, and the step 3
  data addendum landed on 2026-08-13: D4 unchanged, D14 upgraded to
  required, D18's sizing key sharpened, and the disk-overcommit
  constant flagged for phase 3.
- **Phase 1** merged as PR #3578 on 2026-07-31.
- **Phase 2** merged as PR #3614 on 2026-08-08. Its reconciler has
  been soaking cleanly on sfcbr since, on 5-minute passes which have
  reported no corrections. Read that as "nothing observed", not
  "nothing happened": until the phase 4a close-out the pass logged
  membership and timing only, so a corrected `used_*` counter left no
  trace. See the phase 3 note below.
- **Phase 3** merged as PR #3754 on 2026-08-16. Its D13 demand clause
  shipped the #3813 defect, fixed by phase 4a. Its step 9 sfcbr soak
  was discharged by phase 4a's step 5, one soak covering phases 3, 4
  and 4a (its decision E6); see that plan's *Soak observations*.
  One qualification on its exit criteria: **the "reconciler reports
  zero drift" criterion was accepted on healthy-pass evidence rather
  than on measured deltas**, because the reconcile pass did not log
  drift magnitudes at the time. Complete here does not mean that
  criterion was checked. The phase 4a close-out added the missing
  instrumentation, so the next soak can check it properly.
- **Phase 4** has landed in full, management and operator review
  included. Its claim soak was discharged by phase 4a's step 5, by
  deliberate exercise rather than by waiting: nothing on sfcbr creates
  claims on its own, so `tools/exercise-namespace-claims.py` walks the
  pathway end to end, drawdown and expiry included. `NamespaceClaim`
  is a first-class object with admin-only REST CRUD at
  `/auth/namespaces/<namespace>/claims`, and creation migrates the
  namespace's existing drawdown out of the cluster's unclaimed sums and
  onto the claim. Ceilings are **advisory** this release: an exceedance
  is admitted and recorded as an audit event, and phase 5 flips
  `CLAIM_ENFORCEMENT_HARD`. Client verbs moved out of scope (D7 in the
  phase plan).
- **Phase 4b** landed its client verbs (client-python#375,
  merged 2026-08-28) and its functional coverage (PR #3930,
  merged 2026-08-28), and completed on 2026-08-29. Its step 3 -- cut a
  `v0.8.4` release -- was **superseded on 2026-08-29 by its
  decision D7**: no consumer this plan depends on installs a
  release, so no release is cut for phases 4b to 7 and phase 8
  owns the one an operator needs. One caveat on #3930: its
  merge-queue run failed on twelve unrelated
  `507 ... sufficient_idle_cpu` refusals (issue #3772) and it was
  merged by hand. The three claims tests passed on all three
  cluster platforms inside that failed run, so the evidence is
  sound, but the gate was bypassed rather than met.
- **Phase 4a** was inserted on 2026-08-22, between phases 4 and 5,
  because #3813 was a live defect in phase 3's shipped admission code
  and phase 5 makes that code the sole gate on placement. It completed
  on 2026-08-24. The soak measured the fix: demand-only refusals fell
  from 100% of all refusals to a residue, and the P9 waiver rate from
  62% to 4.1%. Its survey found that the D13 seed constant was transcribed from the wrong row
  of its own measurements, which reclassifies the fix from a deferred
  tuning question to a correction; see the phase plan's *What the
  survey found*.

### Phase scope stubs

Each stub is the seed for that phase's plan file; decisions
referenced as D-numbers are in the phase 0 decisions document.

**Phase 1 — typed capacity columns.** `node_metrics` stores
capacity in a schemaless `metrics_json` column; SQL-side
capacity arithmetic needs the ~11 capacity-relevant fields
(cpu counts, load, memory totals/available, disk
totals/available, per-host reservations, overcommit inputs)
promoted to typed columns maintained by the resources daemon.
Includes fixing the dead disk-bandwidth checks found in phase
0 (the `_per_sec` / `_per_second` / `_seconds` spelling
three-way) or removing them explicitly. Pure widening: no
behaviour change to scheduling.

**Phase 2 — capacity tables.** Create
`scheduler_node_capacity`, `namespace_claims` and
`cluster_capacity` per D2, the reconciler in the cluster
daemon's elected-leader loop per D5, and the
`ensure-mariadb-schema` migration. Counters are maintained
and reconciled but **nothing consumes them for admission
yet** — this phase is observable-but-inert, so it can soak on
sfcbr while phase 3 is built.

**Phase 3 — claim primitive and placement.** The guarded-
UPDATE admission RPC in `sf-database` (D1), consumption at
`place_instance()` in the same transaction as the placement
write (D3), release on `hard_delete()` and failed create,
preflight-redirect and cleaner placement-rewrite paths moved
onto the primitive, the demand feedforward term (D13), and
the scheduler's pick-then-claim loop (D7). The concurrent-
scheduling test from the review checklist lands here. Disk
admission is overcommitted by `SCHEDULER_DISK_OVERCOMMIT`
(provisional 5.0), applied to the free-space headroom term of
phase 2's `_derive_disk_limit_gb()` — there is no
total-physical-disk metric to multiply: the step 3 addendum
measured virtual disk claims at 40–140× actual usage, so
admitting a burst's summed virtual size against unscaled
free space would reject routine CI concurrency (see the
addendum and phase 3 decision P3).

Phase 3 also **inherits and removes** the CPU stopgap landed
for issue 3498 (PR 3724): `Scheduler._committed_vcpus()`
walks each candidate's `INSTANCE_LOCATION` rows in Python and
admits on `max(measured, committed)`, which is this plan's
allocation-denominated answer reached without this plan's
tables. It closed the burst window on sfcbr while phase 3 was
unwritten, and it must be deleted by the change which
introduces the guarded UPDATE, not left underneath it: two
ledgers over the same ground truth will eventually disagree,
and the Python one is the unmaintained one. Of its two
exclusions, only the first (skip deleted instances) matches
`_RECONCILE_USAGE_SQL`; the second (charge an instance only
to the node its own placement attribute names) has no
reconciler counterpart — the usage query charges every node
holding an `INSTANCE_LOCATION` row, and documents duplicate
counting as an open hazard. The phase 4 de-duplication
obligation is therefore answered by phase 3 making duplicate
placement rows unproducible (the atomic move deletes all
prior rows), not by the stopgap's filter, which dies with the
stopgap. Two smaller consequences to pick up: RAM and disk
admission were knowingly left measurement-denominated, so
phase 3 closes their burst window for the first time; and
`summarize_resources()` gained the same walk, unconditionally,
where admission pays for it only on a node it would otherwise
reject -- the counters remove both rather than optimise them.

That conditional shape is the concession this stopgap makes
to `PLAN-database-load-reduction`, whose phase 2 cache
deliberately excludes the states and attributes the
exclusions read. It is a workaround for not having the
counters, not a pattern to carry forward: phase 3 reads one
maintained row per node and needs no such trick.

**Phase 4 — namespace claims API.** The claim as a
first-class object with REST CRUD (D15), advisory-mode
ceiling enforcement with structured events (D16), opt-in
semantics and best-effort accounting for unclaimed
namespaces (D14/D17). The conductor-side integration (D18) is
phase 4c; this stub used to assert it would "land in
private-ci once this phase ships", which was an assumption
about work happening elsewhere rather than a tracked phase,
and nothing had implemented it ten days later. Superseded in
detail by the phase plan, which moved client verbs out of
scope (D7 there) and discharged this stub's prerequisite:
the admission transaction's P7 fail-open (`guarded = enforce
and node_present`) dropped *every* guard when the target
node had no capacity row, including the namespace-claim
branch, whose limits are namespace-denominated and
node-independent. It is now three flags, and the claim one
is gated on `CLAIM_ENFORCEMENT_HARD` rather than on the
node's row.

**Phase 4b — client support for claims.** Add `apiclient`
verbs for the claims API in `shakenfist/client-python`,
tracked as client-python#364, and then move this repository's
functional coverage onto them.

Phase 4's decision D7 put client verbs out of scope for a
real reason: CI installs the released client from PyPI, so a
test written against new `apiclient` methods cannot pass
until a client release exists, and no server pull request can
produce one. That reasoning was sound and is now spent --
the API has shipped, so the release can happen.

Two obligations fall out of it, and the phase is not done
until both are met. The first is the verbs themselves, in the
other repository; issue #364 already carries the full surface
including the field-mask semantics of `PUT`, the status codes
worth typed exceptions, and the `state` /
`coverage_state` distinction a client view must not collapse.
The second is here:
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py`
currently reaches past the public surface via
`apiclient.Client._request_url()`, with a docstring saying
not to "fix" it onto verbs until a release exists. Once one
does, the test moves onto the verbs, because the verbs are
then what an operator actually uses and so what is worth
defending.

The server-side prerequisite is met: the API advertises
`auth: namespace-claims` in its capability list as of the
phase 4a close-out, so a client can feature-detect claims and
an un-upgraded one degrades rather than failing. Phase 4
shipped without that string; it was found by a question at
close-out rather than by any review, and the miss is recorded
in phase 4's Future work.

*Correction (2026-08-27), from the phase 4b survey.* The
premise above -- that a test written against new `apiclient`
methods cannot pass in CI until a client release exists -- is
false, and has been since 2026-06-24. Cluster CI does not
install the released client: it builds a wheel from a
`client-python` checkout at `develop` and installs that, via
`sf_build_local_wheels` in `examples/_shared/site.yml`. A verb
merged to the client's `develop` is in this repository's
cluster CI on the next merge-queue run. D7's reasoning was
already stale when phase 4 wrote it down.

*Second correction (2026-08-29).* The paragraph above used to
continue: "A release is still needed, but by phase 4c rather
than by this phase's own functional coverage: the conductor
installs a released client from PyPI." That is false too. The
conductor's deployment playbook pip-installs
`git+https://github.com/shakenfist/client-python@develop` with
`state: latest`, and has since 2026-07-12 (`f4d0e48e`), for the
same reason cluster CI builds from a checkout -- `develop` moves
API contracts faster than releases do, and a released client
behind a server contract once wedged the conductor's main loop
overnight. **No consumer this plan depends on installs a
release.**

The strategy is now explicit rather than accidental: while
scheduling iterates through phases 5 to 7, the client's
`develop` is what CI and the conductor track, and no release is
cut per phase. Phase 8 owns the release, because an operator
guide documenting `sf-client namespace claim` cannot ship
against a PyPI version that lacks it. See decision D7 in the
phase 4b plan for the reasoning and the costs.

Twice now this stub has asserted where a consumer gets its
client from, and twice it has been wrong in the same direction
-- assuming a release where a branch was already being tracked.
Both assumptions were inherited by restatement rather than
checked. The general lesson is worth more than either
correction: check the install path, do not read it off another
plan.

The plan is
[PLAN-scheduler-reservations-phase-04b-client.md](PLAN-scheduler-reservations-phase-04b-client.md).

**Phase 4c — conductor claim integration.** Implement D18 in
the private CI conductor (the `shakenfist/private-ci`
repository): a claim per runner namespace, sized from the
workflow cost data the conductor already collects, created
before the runner is provisioned and deleted at teardown, with
a capacity refusal handled as back-pressure that leaves the
job queued rather than as a failure.

The phase exists for two reasons and the second is the more
important. The first is the integration itself. The second is
that D16 makes the ceiling advisory for one release *so that
exceedances are observed before they are refused*, and that
observation window is worthless without a consumer: between
phase 4 merging on 2026-08-17 and this phase being written on
2026-08-27, every claim that had ever existed on sfcbr was
created by a test and deleted by the same test. Phase 5 would
otherwise flip `CLAIM_ENFORCEMENT_HARD` on the strength of a
measurement period that measured nothing.

The plan is
[PLAN-scheduler-reservations-phase-04c-conductor-claims.md](PLAN-scheduler-reservations-phase-04c-conductor-claims.md),
and it deviates from the usual convention that a plan lives
with the code it plans: the plan is here, in the public
repository beside its master plan and its sibling phases, and
the implementation lands in private-ci. The reasoning is in
that plan's decision E1.

**Phase 5 — caller migration and hard ceiling.** Migrate the
three `Scheduler()` call sites per D11 (queue worker to the
claim-consuming path; API-side feasibility precheck;
admin capacity view), remove the legacy in-Python capacity
filtering, and flip the ceiling from advisory to hard one
release after phase 4 (D16).

*Correction (2026-08-27):* "one release after phase 4" is the
wrong gate, and phase 4c replaces it. The advisory release
exists to collect calibration data, so what phase 5 waits on is
that data existing, not time elapsing. Phase 5 does not start
until phase 4c's observation record is written.

*Input from phase 00a's post-deploy observation (2026-08-27),
corrected 2026-08-29:* this paragraph used to say the load stage
"can discard nodes with far more room than the ones it keeps",
citing 8 of 60 observed decisions and 34 leaving a single
survivor. **That is withdrawn.** The load stage discards
nothing: `find_candidates()` extends its result with every
bucket's tier (`shakenfist/scheduler.py:741-756`), which is the
merge-CI single-candidate lockout fix `108a0cdbd` from
2026-08-15 -- twelve days before the observation. The counts came
from the `schedule have lowest cpu load` audit event, which
publishes the lowest bucket alone by construction; the full
ranking is in `schedule final candidates`. The finding also
quoted headroom weights for the nodes it called eliminated, and
a node only has a weight if it is in the returned list, so its
own evidence refutes it.

What survives for phase 6 is real but weaker: `cpu_load_1`
measures activity rather than occupancy, so a node packed with
idle CI runners *ranks ahead of* a busier node with more room.
That is a mis-ordering, not a mis-elimination -- the emptier node
is still reachable, later in a list the caller walks only on
refusal. Phase 6 owns the ranking model and should decide
whether an activity metric belongs in it now that the capacity
counters supply an occupancy one. Detail and the withdrawn text
are in
[PLAN-scheduler-reservations-phase-00a-load-aware-ordering.md](PLAN-scheduler-reservations-phase-00a-load-aware-ordering.md).

*Not evidence for phase 6:* the recurring
`507 ... sufficient_idle_cpu` refusals in merge CI (#3772) come
from the admission stage, which runs before ranking and reads
the capacity counters. When it refuses every candidate no
ranking model would have helped, so that family belongs to
`PLAN-ci-cloud-sizing` and phase 6 should not adopt it.

**Phase 6 — affinity rework.** Binary soft affinity plus
hard require constraints, weighted-form deprecation mapping,
ranking precedence above load ordering (D6). Also takes the
decision D6 deferred: whether a soft affinity preference may
bid against a hard admission ceiling. Issue 3565 turns on
that decision and not on the ranking precedence, which
already landed as PR 3722 — see the D6 correction of
2026-08-16 in the phase 0 decisions document for the audit
trail and the three positions on offer. Phase 6 closes 3565
only if it picks one of them.

*Correction (2026-08-19, restated 2026-08-22):* there is now
a competing explanation for 3565, and phase 6 must rule it
out before claiming the flake. Issue #3813 (Bugs fixed,
below) showed the D13 demand clause refusing **every**
candidate on the CI hypervisors.

The 2026-08-19 wording of this correction said the create
then "places through a single forced candidate and the
affinity stage has nothing left to rank". Phase 4a's survey
established that this is not the mechanism: the P9 re-walk
iterates the same candidate list in the same order
(`external_api/instance.py:881-921`,
`operations/node_inst_netdesc_op.py:194-232`), so the
scheduler's ranking — affinity included — is preserved
exactly and the waived walk takes the top-ranked candidate.

What is actually lost is the **spreading**. Because the
clause can never pass, nothing makes the top-ranked
candidate less attractive to the next create in a burst, and
the ranking it competes against is `cpu_load_1 /
cpu_schedulable` from a metrics row up to 60 seconds stale.
A burst piles onto one node until a real allocation
dimension bites. That is a plausible contributor to 3565 —
soft affinity bidding against a candidate set skewed by
pile-up — but it is not "affinity is never reached", so
D6's three positions remain live rather than moot.
Establish which mechanism is operating **before** spending
phase 6's decision budget, now against the corrected
premise. Phase 4a fixes the clause, so phase 6 can measure
3565 on a cluster where the spreader actually spreads.

**Phase 7 — diagnostics.** Failure-path verbose diagnostic
against the same snapshot, success-path drawdown events,
ceiling-rejection events (D9). Confirm CI triage tooling
reads the new events.

**Phase 8 — documentation.** Operator guide for claims and
capacity (including the two service classes and the
reconciler), developer-guide write-up of the guarded-UPDATE
idiom (D10), user-facing affinity migration notes.

This phase also owns **the client release**, added here on
2026-08-29 by phase 4b's D7. Phases 4b through 7 deliberately
cut none: CI and the conductor track client-python's `develop`,
so a release buys them nothing and would put a tag and a human
approval on each phase's critical path. An operator is a
different matter -- this phase's guide documents `sf-client
namespace claim`, which no released client has. So phase 8
cuts one release covering everything phases 4b to 7 accumulate,
and the guide must not merge claiming a command an operator
cannot install. That ordering is this phase's obligation and
nobody else's.

**Phase 9 — push audit.** Run
the repository's pre-push audit over everything this plan
built, as a single body of work rather than one branch at a
time.

The audit template is written for a branch about to be
pushed, and this plan does not fit that shape: its phases
were pushed and merged one at a time over months, each
audited only against its own diff. Two classes of problem
survive that. The first is drift between phases -- a
convention followed in phase 2 and quietly abandoned by
phase 5, or documentation that was true when its phase
landed and was falsified by a later one. The second is
anything whose absence is only visible from the whole: the
capability string that phase 4 never advertised was found by
a passing question at close-out, not by any phase's own
review, and nothing in a per-phase audit would have caught
it. That defect is the reason this phase exists.

So the diff under audit is the plan's cumulative diff, not
`develop...HEAD`. Every wave-1 and wave-2 command in
`PUSH-AUDIT.md` that names `develop...HEAD` is rewritten to
`ea3c9bf63..develop` -- the commit immediately before phase
0 merged (`87a58a81e^1`, 2026-07-30) -- restricted to the
paths this plan touches, so the range does not drag in
unrelated work merged over the same months. The phase plan
pins the path list.

Two adaptations follow from the range being months of merged
history rather than a pending branch:

* The management checklist's "commit history is clean" and
  "branch is up to date" items do not apply and are struck
  rather than ticked. Nothing here is rebaseable.
* Findings cannot block a push that already happened. They
  are triaged instead: anything security-critical or high is
  fixed in this phase, anything else is filed as an issue or
  recorded as Future work, and the phase records the triage
  rather than silently absorbing it.

Findings land as their own pull request, and the plan is not
complete until each is resolved or declined in writing here.
If the audit finds nothing, that is recorded in one sentence.

Run this last, after every other phase, so the documentation
the audit checks is the documentation the plan intended to ship
and the client surface it reviews is the final one.

## Dependencies on other plans

- **`PLAN-sql-pushdown-filtering` is the existing precedent**
  and pattern for SQL-side filtering. The reservations work
  applies that same rule to a new domain (scheduling decisions)
  rather than extending it. No hard ordering dependency, but
  phase 0 should read the pushdown plan's decisions document
  before deciding the conditional-INSERT shape so the two
  approaches stay coherent.
- **`PLAN-per-host-resource-reservations` (complete) is landed
  groundwork.** It generalised phase 00a's cluster-global
  reservation knobs into per-host settings:
  `NODE_RAM_RESERVATION_GB`, `NODE_CPU_RESERVATION_THREADS`
  (thread-denominated — a semantics change from 00a's cores)
  and `NODE_DISK_RESERVATION_GB` (which took over
  `MINIMUM_FREE_DISK` and is published as a
  `disk_reservation_gb` node metric, so remote evaluators
  judge a node by that node's own reservation rather than
  their local config). The reservations table's
  effective-capacity query must subtract these published
  per-host reservations; conceptually this plan extends the
  same "capacity the scheduler may not use" idea from static
  per-host configuration to dynamic per-claim rows.
- **`PLAN-remove-primary` does not block this plan** and this
  plan does not block `PLAN-remove-primary`. They are
  compatible by design — the reservations-via-DB-atomicity
  shape was chosen specifically to avoid adding a new
  critical-path role that `PLAN-remove-primary` would then
  have to undo.
- **OpenTelemetry instrumentation (not yet drafted) would
  inform phase 0** by giving real numbers for current
  scheduling latency and contention. If OTel lands first, use
  it. If not, phase 0 includes a one-off benchmark of the
  current scheduler under contention as input to the
  conditional-INSERT vs SELECT-FOR-UPDATE choice.
- **The future content-aware placement work** in the
  blob-storage roadmap is a natural successor — reservations
  that carry workload context (blob affinity) are the
  substrate. Out of scope here; phase 0 should consider how
  the schema choice today would or wouldn't compose later.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The workflow mirrors
`PLAN-remove-primary.md` and `PLAN-sticky-transfers.md`: plan
in the management session, spawn a sub-agent per implementation
step, review in the management session, fix or retry, commit
when satisfied.

This work touches the instance-create hot path and a piece of
infrastructure (atomic capacity claim) that is hard to retrofit
once committed. Sub-agents working on phases 0-2 should be
skewed toward **opus at high effort** because the schema and
atomicity-model choices are costly to undo. Phases 4-8 are more
mechanical and can use lower-effort sub-agents.

### Planning effort

The master plan itself is **medium effort** — it's a
placeholder converging on a direction. Phase 0 (research and
decisions, including the affinity simplification decision and
the generic-vs-specific reservation table decision) is high
effort. Subsequent phases will be re-evaluated once phase 0
lands.

### Step-level guidance

Each phase plan should include a step table in the same format
as `PLAN-remove-primary.md`, with effort, model, isolation, and
brief columns.

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`, plus:

- [ ] The atomicity guarantee is exercised by a concurrent-
      scheduling test, not just asserted in docs. Two
      simultaneous batch reservations against a tight cluster
      must produce a consistent outcome.
- [ ] The reaper's behaviour against an abandoned reservation
      is exercised end-to-end, not stubbed.
- [ ] Per-rejection audit logging in diagnostic mode produces
      the same depth of detail as today's scheduler did by
      default. Operators must not lose the ability to debug a
      failed schedule, even if they have to ask for the detail
      explicitly.
- [ ] The affinity behaviour after the model rework is
      documented in terms a user can read, with a clear
      migration note if the existing weighted form is changing
      or being removed.
- [ ] Object cleanup (`hard_delete()`) accounts for
      reservation rows owned by a deleted instance.
- [ ] mypy coverage for the new scheduling primitive is at
      least as good as today's scheduler, ideally better.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* Scheduling decisions are atomic with respect to capacity:
  two concurrent batch creates against a tight cluster cannot
  both succeed when only one batch fits.
* The "schedule N, fail on N-1" pattern is no longer
  reachable for batch creates. Either the whole batch is
  reserved up front or it fails up front.
* The `node_reservations` table is the single source of
  truth for pending capacity claims, and stranded reservations
  are reaped without operator intervention.
* The existing in-process `Scheduler()` callers are gone, or
  explicitly justified for staying on the old shape with a
  documented reason.
* The new batch-create API exists, is documented, and is used
  end-to-end in at least one functional test under
  `deploy/shakenfist_ci/cluster_ci_tests`.
* The affinity model is either preserved as today, simplified
  to the binary-soft form, or removed — with a clear documented
  rationale and a migration note for the user-facing API.
* Per-rejection audit logging in diagnostic mode produces
  detail at least equivalent to today's default. The
  day-to-day audit log is shorter than today's by design.
* The D13 demand clause admits placements on a node that has
  real room for them, at every node size this project supports
  — or it has been deliberately retired. **Met by phase 4a**,
  which asserts admission on an idle node at every one of 16
  node sizes (clause-level) and at every one of five instance
  sizes on the smallest node size the CI fleet runs
  (behavioural); issue #3813 closes once that phase's soak has
  been observed.
* `pre-commit run --all-files` passes.

### Future work

- **Generic resource-claim primitive.** If phase 0 chooses to
  keep the reservation table instance-scheduling-specific,
  later work may want to extend it to other finite resources
  (floating IPs, VXLAN IDs, network IDs). Out of scope here;
  the phase 0 generic-vs-specific decision should leave a
  comment about which way to extend.
- **Hold-until-fittable batch creates.** If the batch-create
  API ships as all-or-nothing, a later iteration could add a
  queue for batches that don't fit right now but might once
  other reservations expire or consume. Useful for CI burst
  smoothing.
- **Content-aware placement.** Reservations that carry blob
  affinity intent slot into the broader blob-storage roadmap.
- **Reservation-aware autoscaling signals.** A persistent
  reservation backlog (batches waiting on capacity) is a real
  scale-out signal an external system could consume.
- **Network bandwidth as a scheduling input.** Today's
  scheduler considers CPU, memory, and disk capacity but not
  ingress / egress bandwidth. With the smeared-carrier model
  from `PLAN-network-carrier-model`, network bandwidth on
  carrier nodes becomes a meaningful constraint — a carrier
  hosting many high-traffic networks can saturate its NIC
  while showing plenty of CPU / RAM / disk headroom. Worth
  *tracking* as a reservation dimension (so placement can
  avoid worsening hot spots) even if actively *limiting*
  network throughput is out of scope (rate-limiting at the
  hypervisor is operationally complicated and probably not
  worth the effort versus capacity-aware placement). Out of
  scope here pending the carrier model and OpenTelemetry
  measurements; revisit once those land.
- **Real host load and node-role awareness as scheduling
  inputs.** CPU admission today counts only allocated VM vCPUs
  (times `CPU_OVERCOMMIT_RATIO`, default 16), so it almost never
  rejects a node, and the only real-utilisation signal is a
  `math.floor(cpu_load_1)` tie-break. This ignores three things:
  (a) actual CPU utilisation; (b) the service load a combined
  network / database node carries — neither `is_network_node`
  nor `is_database_node` is consulted anywhere in
  `scheduler.py`, and there is no CPU analogue of
  `RAM_SYSTEM_RESERVATION`; and (c) heterogeneous core counts,
  because the `floor()` quantisation collapses every sub-1.0
  node into a single uniform-random bucket. Observed on sfcbr
  2026-07-17: a CI burst stacked three 16 GB VMs onto the
  12-core network+DB node (load ~15) while two idle 24-core
  nodes sat ~90% free. Cheapest high-value change is to rank by
  **load-per-core** instead of `floor(raw load)`, which fixes
  both the bucket collapse and the heterogeneity blindness in
  one move; the fuller fix de-weights infra-role nodes and/or
  adds a CPU service reservation. These are soft-preference
  ordering inputs, so they should compose with the reservation
  model's ORDER BY / tie-break surface (open questions 6-7)
  rather than being bolted on as a parallel heuristic. Diagnose
  with `tools/sfcbr-capacity.sh` in the 33fl repo (per-node
  load-per-core plus infra-role tags). **Status: delivered as
  phase 00a** (load-per-core ordering, coarse buckets to
  preserve burst spreading, core-denominated system
  reservations for the OS and infra-role daemons, headroom-
  weighted selection, CPU topology tracking, and a measured
  overcommit default); the reservation knobs were subsequently
  generalised per host by
  `PLAN-per-host-resource-reservations` (see Dependencies).
- **Demand-based adaptive overcommit (the learning loop).**
  The end-state sketched in open question 13: each node's
  expected demand-per-vCPU is *learned* from observed
  `cpu_load_1 / cpu_total_instance_vcpus` over time, probably
  tracked per namespace (a CI namespace learns ~0.8-1.0 per
  vCPU; a namespace of idle pet VMs learns ~0.05), with
  damping, floor / ceiling clamps, and a bias toward
  recent-window *max* rather than mean because correlated
  bursts are the failure mode that matters. The learned
  estimate replaces the static demand constant that phase 00a
  ships and feeds the expected-demand field on reservation
  rows (if phase 0 adopts it). Validate the model offline
  first — an analysis report over recorded sfcbr metrics —
  before anything trusts it in the placement path.
- **Nothing treats a capacity refusal as transient.** A create
  that finds no node with capacity fails immediately with a
  507, even though in CI the capacity it wanted frees within
  minutes as other tests delete their instances. This plan
  makes admission correct and race-free; it does not make a
  full cluster not-full, so a suite that bursts past cluster
  capacity still fails rather than waits. Where the retry
  belongs is undecided: the client SDK (`client-python`), the
  CI base class (`shakenfist_ci`), or server-side admission
  queueing — the hold-until-fittable bullet above is the
  server-side end of the same question, but scoped to batch
  creates only. Deliberately *not* bundled with phase 3: a
  retry would mask whether atomic admission actually reduced
  the failure rate, so this should wait until issue #3772 has
  soak data from a `develop` carrying that phase.
- **CI tier topology and sizing as a capacity consumer.** The
  Debian 12 tier runs three "hypervisors", of which `primary`
  is also the network *and* database node and `sf1` is also a
  database node. With `NODE_CPU_RESERVATION_THREADS=4` each
  has a `cpu_schedulable` of 1-2, so at the default overcommit
  `limit_cpus` is 3 and three 1-vCPU instances fill a node —
  while the suite runs at stestr concurrency 5 and several of
  the tests that fail this way use `force_placement`, which by
  construction cannot fall back to another node. The per-host
  reservations are arithmetically right; they just make an
  already small cluster genuinely small, which is why the
  role-awareness work landed in phase 00a made these failures
  *more* likely rather than less. Open questions, none of them
  scheduler code: whether infra-role nodes should host
  instances at all in CI, whether the tier needs a fourth
  node, and whether suite concurrency should be denominated in
  cluster capacity rather than runner cores. Tracked with the
  bullet above under issue #3772.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

* **The D13 demand clause was arithmetically unsatisfiable on
  small nodes** (issue #3813, found 2026-08-19 during merge CI
  triage of run 32227047799; fixed by phase 4a). The clause
  compared a per-request charge against a per-node budget --
  `cpu_load_1 + expected_demand + demand_add <=
  SCHEDULER_TARGET_LOAD × cpu_schedulable`, where the budget is
  denominated per schedulable thread and `demand_add` per
  requested vCPU. At the seed constants a node needed 3.34
  schedulable threads before it could admit a 1-vCPU instance at
  zero load, so the CI hypervisors (`cpu_schedulable: 2`)
  refused every candidate, every time, on demand alone. The P9
  waiver then admitted anyway, so the visible cost was two
  candidate sweeps per create and a spreader that never
  operated, rather than a failed create.

  Two causes, both fixed. The seed constant was transcribed from
  the wrong row of the 00a-1 measurements -- 2.5 is that
  appendix's allocated-vCPUs-per-thread packing figure, while its
  demand-per-vCPU row reads 0.12-0.35 steady with a ~0.6 burst
  peak -- so `SCHEDULER_DEMAND_PER_VCPU` is now 0.6. And the
  clause itself now compares only the node's existing state,
  leaving `demand_add` to accumulate in `expected_demand` for the
  next decision, which is the only form satisfiable at every
  combination of node size and instance size. Two live tests pin
  the property -- a clause-level sweep over 16 node sizes, and a
  real admission at each of five instance sizes on a two-thread
  node -- which between them fail 54 of those 80 combinations
  against the pre-fix code. They are deliberately not one
  80-cell grid: the clause no longer takes the placement's
  charge, so sweeping instance size against it would evaluate
  one expression five times and claim evidence it did not
  produce.
* **KSM metrics were never published** (pre-existing, found by
  the phase 00a code review): the resources daemon's KSM block
  skipped every sysfs file (trailing-newline filter), used a
  literal `'memory_ksm_{ent}'` key (missing f-prefix), and
  re-read an exhausted file handle so the swallowed ValueError
  hid it all. No `memory_ksm_*` field had ever reached
  `node_metrics`.
* **ZeroDivisionError on metrics rows lacking `memory_max`**
  (pre-existing, found by the same review): the KSM overcommit
  admission check divided by `memory_max` with no guard, so a
  partially-written hypervisor row crashed `find_candidates()`
  instead of excluding the node with a recorded reason.

### Documentation index maintenance

When creating a new master plan from this template, update the
following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master plans* table.
* **`order.yml`** — add an entry for the new master plan.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
