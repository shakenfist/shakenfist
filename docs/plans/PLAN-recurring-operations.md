# Recurring cluster operations framework

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(cron expression evaluation, scheduling semantics under
clustered failover, KVM/libvirt, VXLAN networking,
MariaDB/Galera, gRPC/protobuf), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/operations/baseoperation.py` (the
`BaseClusterOperation` framework and its dispatcher
semantics), `shakenfist/daemons/cluster/scheduled_tasks.py`
and `shakenfist/daemons/cleaner/scheduled_tasks.py` (the
existing ad-hoc scheduled-tasks code that this framework
would absorb), `shakenfist/daemons/network/maintain.py`
(the network-maintenance loop that will become a consumer
once the framework lands), and `shakenfist/mariadb.py` (the
three-layer database access pattern).

When we get to detailed planning, the convention is a
separate plan file per detailed phase, named
`PLAN-recurring-operations-phase-NN-descriptive.md` in the
same directory.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Several distinct callers in the codebase have a "do X on a
recurring schedule" requirement, each currently solved
ad-hoc:

1. **`scheduled_tasks.py`** at
   `daemons/cluster/scheduled_tasks.py` is the closest thing
   we have to a recurring-operations framework today. It is
   internal-only, tied to the cluster daemon's loop, and
   the schedule is hard-coded.
2. **`daemons/cleaner/scheduled_tasks.py`** is the per-node
   equivalent. The cleaner daemon runs `update_power_states`,
   `remove_stale_uploads_for_this_node`, and the
   blob-directory / image-cache sweeps on a hard-coded
   `schedule` inside its own loop rather than as queued
   operations. These passes do their work inline while
   holding per-object locks — notably the instance placement
   lock taken by `update_power_states` — so a pass that
   overruns the systemd watchdog is SIGABRTed mid-operation
   and strands that lock. That failure mode has already bitten
   production (see Future work), which makes the cleaner a
   prime candidate for the queue-item model this plan
   introduces.
3. **`maintain.py`** at `daemons/network/maintain.py` is the
   network reconciliation loop. It runs on a thread inside
   `sf-net`, ticking every interval and walking all
   networks. The network-facade plan
   (`PLAN-network-facade.md`) deliberately keeps it as a
   thread for now and gates its enqueues per-network,
   noting that the proper landing place for "maintain is a
   recurring CO" is here.
4. **User-driven recurrence is currently impossible.** A
   user who wants "snapshot this instance every 24 hours"
   has to run external cron + REST client. There is no way
   to express "this operation should recur" through the API.

The operation queue framework (`BaseClusterOperation`,
priorities, `cluster_operation_targets`, the dispatcher's
`depends_on` / `runs_after` semantics) gives us most of the
machinery we'd need for a unified recurrence framework. We
have a typed operation model, persistence, queue
infrastructure, priority lanes, namespace authz on the
target object, and history via `cluster_operation_targets`.
What we lack:

* A schedule type (cron expression and/or "every N seconds")
* A persisted `RecurringOperation` object that lives
  alongside other DBOs
* A tick mechanism that fires the recurring op on schedule
* Gating semantics ("don't tick if the previous tick is
  still in flight")
* User-facing REST API
* A migration path for the existing internal consumers
  (`daemons/cluster/scheduled_tasks.py`,
  `daemons/cleaner/scheduled_tasks.py`, `maintain.py`)
* Two specific dispatcher gaps the network-facade plan
  flagged: no max-wait semantics for `runs_after`
  (a stuck dep defers the dependent indefinitely); and
  `depends_on` aborts the dependent on dep failure, which
  is wrong for recurring tasks (a single failed reconcile
  should not stop the recurrence).

## Mission and problem statement

Introduce a `RecurringOperation` object type and the
supporting framework so that:

* Any internal subsystem that today runs a recurring loop
  can express it as a `RecurringOperation` instead. Initial
  consumers: `daemons/cluster/scheduled_tasks.py`,
  `daemons/cleaner/scheduled_tasks.py`, and
  `daemons/network/maintain.py`.
* Users can create `RecurringOperation` objects via the
  REST API for explicit recurring tasks. Initial supported
  template types include at least `snapshot_op` (matches
  the user-driven motivating use case) and `agent_op` for
  recurring agent commands.
* The framework respects the existing priority lanes,
  namespace authz, target tracking, and event logging — a
  recurring op is not a second-class citizen.
* Operational concerns are addressed up front:
  per-recurrence "don't double-fire" gating, a maximum-wait
  semantics for `runs_after` so a stuck dep doesn't break
  recurrence permanently, and explicit handling for failed
  ticks (do not break the recurrence on a single failure).
* When the framework lands, the cluster and cleaner
  `scheduled_tasks.py` and `daemons/network/maintain.py`
  migrate to it in subsequent
  phases. The network-facade plan's Q6 design (per-network
  gating + cooldown + circuit breaker) is preserved through
  the migration — it just lives inside a maintenance-pass
  recurring op rather than inside a free-standing thread.

Scope boundaries (preliminary — to be refined when this
plan moves out of stub status):

* **In scope:** the `RecurringOperation` object, its REST
  API, its tick mechanism, the dispatcher changes needed to
  support max-wait `runs_after` and continue-on-failure
  recurrence semantics, and migration of the cluster and
  cleaner `scheduled_tasks.py` and `maintain.py` as initial
  consumers.
* **Out of scope:** general workflow engines (we are not
  building Airflow). The recurrence vocabulary is
  deliberately small.
* **Out of scope:** changing the operation queue's
  priority taxonomy or the way the cluster elects an owner
  for cluster-wide operations.

## Open questions

These are preliminary sketches. Each will be tightened
significantly when this plan moves out of stub status.

1. **Schedule format.** Cron expressions are powerful but
   verbose and have many edge cases (timezones, DST,
   "every Wednesday in the third week"); simple "every N
   seconds" is sufficient for internal consumers
   (`maintain.py` is every 60 s) but inadequate for
   user-facing recurrences like "snapshot every 24 hours
   at 3 am UTC". Possible resolution: support both, with a
   strict subset of cron expressions to avoid the most
   painful edge cases (no timezone-aware scheduling for
   v1, evaluate all expressions in UTC).

2. **Tick owner and failover.** Some recurrence ticks are
   cluster-wide (run maintain on the elected network
   node); others are per-node (each node's local
   `sf-net` runs its local maintenance). The framework
   needs to express this. The existing cluster-wide vs
   per-node queue taxonomy is the right primitive to lean
   on — the recurring op simply enqueues at the
   appropriate queue.

3. **Gating semantics.** "Don't tick if the previous tick
   is still in flight" is the common case but not the
   only one. Some recurrences may want to overlap (e.g.
   metrics collection that's idempotent and cheap).
   Possible shape: a per-recurrence
   `overlap_policy: 'skip' | 'queue' | 'replace'`.

4. **Failure semantics.** A single failed tick must not
   stop the recurrence. But repeated failures should
   surface to operators. The network-facade Q6 design
   (cooldown + circuit breaker via the
   `cluster_operation_targets` history) generalises:
   `RecurringOperation` tracks recent tick history and
   pauses the recurrence after K consecutive failures
   with an operator-visible event. Manual operator action
   clears the pause.

5. **Dispatcher max-wait for `runs_after`.** Today
   `runs_after` defers the dependent indefinitely if the
   dep is non-terminal. For recurring tasks, a stuck dep
   should eventually time out and let the dependent
   proceed. Need a per-dep "deadline" or per-op "max wait
   on deps" setting. Affects the dispatcher (which today
   has no such notion).

6. **Continue-on-failure deps for recurrences.** Today
   `depends_on` aborts the dependent on dep failure. The
   "next tick of a recurrence" wants the opposite:
   "wait until the previous tick reaches *any* terminal
   state, then run regardless". The natural primitive is
   `runs_after` (which already has those semantics), but
   when combined with question 5 the deadline interaction
   needs to be clear.

7. **REST API shape.** New endpoints:
   * `POST /recurring_operations` — create
   * `GET /recurring_operations` — list (namespace-scoped)
   * `GET /recurring_operations/<uuid>` — read
   * `DELETE /recurring_operations/<uuid>` — delete
   * `POST /recurring_operations/<uuid>/pause`,
     `.../resume`, `.../trigger` — operator and user verbs
   The template-op vocabulary is constrained to a small
   set initially: `snapshot_op`, `agent_op`, possibly
   `network_maintain_pass_op` once that exists as a
   discrete op type.

8. **Persistence.** A `recurring_operations` table with
   the schedule, the template (op type + args as JSON),
   the gating policy, and the most-recent-tick history.
   Mutable attributes for paused / resumed state. Follow
   the existing pattern for DBO persistence in MariaDB
   (`namespaces` / `artifacts` / etc.).

9. **Migration sequencing.** Build the framework first,
   then migrate the cluster and cleaner `scheduled_tasks.py`
   (simpler, no user-facing surface), then migrate
   `maintain.py` (requires the network-maintain-pass op type
   to exist as a discrete CO, which is itself non-trivial).
   The cleaner is the more urgent of the two scheduled-task
   consumers: its inline passes hold per-object locks and
   have already caused watchdog-kill incidents (see Future
   work). The user-facing REST surface and the snapshot/agent
   template support can land in parallel with or after
   the internal migrations — they don't block each other.

## Execution

(Detailed phase plans will be drafted when this plan moves
out of stub status. Phases are tentatively expected to
look like:)

| Phase | Plan | Status |
|-------|------|--------|
| 1. `RecurringOperation` object and persistence | TBD | Not started |
| 2. Dispatcher max-wait for `runs_after` + continue-on-failure recurrence semantics | TBD | Not started |
| 3. Tick mechanism and gating policies | TBD | Not started |
| 4. Migrate cluster + cleaner `scheduled_tasks.py` as first internal consumers | TBD | Not started |
| 5. Network-maintain-pass op + migrate `maintain.py` | TBD | Not started |
| 6. REST API and user-facing template vocabulary | TBD | Not started |
| 7. Documentation and tests | TBD | Not started |
| 8. Push audit | PLAN-recurring-operations-phase-08-push-audit.md | Not started |

**Phase 8 — push audit.** Runs `PUSH-AUDIT.md` over the
accumulated diff of every phase in this plan against
`develop`, not the last phase's diff alone. Findings land as
their own pull request, and the plan is not complete until
each is resolved or declined in writing here. If the audit
finds nothing, that is recorded in one sentence.

This plan is currently in stub form. It exists primarily
to anchor a future-work reference in
`PLAN-network-facade.md` and to capture the framing for
when work begins.

## Agent guidance

(To be filled in when this plan moves out of stub status.
The structure will mirror `PLAN-network-facade.md`'s Agent
guidance section: execution model, planning effort,
step-level guidance table, management session review
checklist.)

## Administration and logistics

### Success criteria

When this plan is successfully implemented:

* A `RecurringOperation` object type exists, follows the
  existing DBO patterns, persists in MariaDB, and is
  documented in `ARCHITECTURE.md`.
* `daemons/cluster/scheduled_tasks.py` and
  `daemons/cleaner/scheduled_tasks.py` are gone (or are thin
  wrappers) — their contents have moved to internal
  `RecurringOperation` instances, so their passes enqueue
  bounded work items rather than doing all the work inline
  under a single watchdog budget while holding locks.
* `daemons/network/maintain.py` is gone — its contents
  have moved to a `network_maintain_pass` CO triggered by
  an internal `RecurringOperation`. The per-network
  gating + cooldown + circuit breaker behaviour from
  `PLAN-network-facade.md` Q6 is preserved.
* A user can `POST /recurring_operations` to create
  e.g. "snapshot instance X every 24 hours at 3 am UTC"
  and the snapshot fires on schedule.
* The dispatcher supports a max-wait deadline on
  `runs_after` so a stuck dep cannot permanently break
  recurrence.
* A `RecurringOperation` whose ticks repeatedly fail
  pauses itself after K failures with an operator-visible
  event, mirroring the network-facade circuit breaker.
* The code passes `pre-commit run --all-files`.
* Functional test coverage in
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests` exercises
  both internal consumers and at least one user-driven template.

### Future work

* **Motivating incident: cleaner watchdog kills (2026-07-16).**
  On the sfcbr cluster the `sf-cleaner` daemon was
  systemd-watchdog-killed (SIGABRT, result `watchdog`) eight
  times across sf-1..sf-4 in twelve hours. Each kill landed
  mid-`update_power_states`, aborting the daemon while it held
  an instance placement lock; the stranded lock then surfaced
  cluster-wide as "Lock held by missing process on this node"
  until its lease expired ~60-80s later, flaking overnight CI
  instance provisioning. Interim band-aids landed outside this
  plan (pet the watchdog per libvirt domain in
  `update_power_states`, and raise `sf-cleaner`'s `WatchdogSec`
  to 300s), but the structural fix is exactly what this plan
  proposes: the cleaner's passes should enqueue bounded work
  items processed by the queue rather than doing all the work
  inline under one watchdog budget while holding per-object
  locks. This is concrete evidence that the inline-loop model
  is fragile at scale and raises the priority of migrating the
  cleaner (see Migration sequencing, question 9).
* **Time-aware scheduling.** Cron expressions evaluated in
  the cluster's configured timezone (not just UTC), with
  proper DST handling. Initial implementation is UTC-only.
* **Cross-recurrence dependencies.** A
  `RecurringOperation` whose tick depends on another
  recurrence's most-recent successful tick. Possibly
  useful for "do nightly snapshots only if backup
  completed". Speculative.
* **Catch-up policy.** What to do when the system was
  offline through a scheduled tick: skip, fire once, fire
  for every missed slot. Initial implementation skips.

### Bugs fixed during this work

(none yet)

### Documentation index maintenance

When this plan is updated:

* `docs/plans/index.md` — the row for this plan should
  track its overall status. Phase rows are not added.
* `docs/plans/order.yml` — this master plan is registered;
  phase files are not.

### Back brief

Before executing any step of this plan, the implementing
sub-agent must back brief the operator as to its
understanding of the phase plan and how the work it
intends to do aligns with that plan.
