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

The reservation is consumed when the instance transitions
into the `building` state, explicitly released on instance
create failure, and auto-expired via a leased
`expires_at TIMESTAMP` modelled on the `cluster_locks`
pattern so stranded reservations cannot leak capacity.

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
  (instance enters `building`) and an explicit release on
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
decision depends on a phase 0 research pass. The open
questions include at least:

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
   reservation "consumed"? Proposed: when the instance
   transitions to `building`. Alternatives: at the moment of
   instance create success; at first heartbeat on the target
   node; at libvirt domain define. Each has different failure
   modes around partial creates.
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

## Execution

Provisional, to be re-cut after phase 0.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions document | PLAN-scheduler-reservations-phase-00-decisions.md | Not started |
| 1. `node_reservations` schema and migration | PLAN-scheduler-reservations-phase-01-schema.md | Not started |
| 2. Conditional-INSERT scheduling primitive | PLAN-scheduler-reservations-phase-02-primitive.md | Not started |
| 3. Reservation lifecycle (consume, release, reap) | PLAN-scheduler-reservations-phase-03-lifecycle.md | Not started |
| 4. Migrate existing scheduler callers | PLAN-scheduler-reservations-phase-04-callers.md | Not started |
| 5. Batch-create API | PLAN-scheduler-reservations-phase-05-batch.md | Not started |
| 6. Affinity model rework | PLAN-scheduler-reservations-phase-06-affinity.md | Not started |
| 7. Diagnostic-mode rejection logging | PLAN-scheduler-reservations-phase-07-diagnostics.md | Not started |
| 8. Documentation and operator guide | PLAN-scheduler-reservations-phase-08-docs.md | Not started |

## Dependencies on other plans

- **`PLAN-sql-pushdown-filtering` is the existing precedent**
  and pattern for SQL-side filtering. The reservations work
  applies that same rule to a new domain (scheduling decisions)
  rather than extending it. No hard ordering dependency, but
  phase 0 should read the pushdown plan's decisions document
  before deciding the conditional-INSERT shape so the two
  approaches stay coherent.
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
  `deploy/cluster_ci`.
* The affinity model is either preserved as today, simplified
  to the binary-soft form, or removed — with a clear documented
  rationale and a migration note for the user-facing API.
* Per-rejection audit logging in diagnostic mode produces
  detail at least equivalent to today's default. The
  day-to-day audit log is shorter than today's by design.
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

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

When creating a new master plan from this template, update the
following files in `docs/plans/`:

* **`index.md`** — add a row to the *Plan Status* table.
* **`order.yml`** — add an entry for the new master plan.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
