# Scheduler reservations phase 3: claim primitive and placement

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
`shakenfist/scheduler.py` (the candidate filters, the issue-3498
stopgap `_committed_vcpus()`, and `summarize_resources()`),
`shakenfist/instance.py` (`place_instance()`, `_delete_globally()`,
`hard_delete()`, `enqueue_delete_due_error()`),
`shakenfist/node.py` (`Node.instances`, `add_instance()`,
`remove_instance()`, `_dual_write_legacy_instances()`),
`shakenfist/operations/node_inst_netdesc_op.py` (the preflight
redirect), `shakenfist/daemons/cleaner/scheduled_tasks.py` (the
placement rewrites), `shakenfist/daemons/queues/startup_tasks.py`
(the reference reconciliation), and the phase 2 capacity machinery
in `shakenfist/mariadb.py` (`_RECONCILE_USAGE_SQL` and its comment
block, `_derive_cpu_memory_limits()`, `_derive_disk_limit_gb()`,
`_decayed_demand_contribution()`,
`_direct_reconcile_scheduler_capacity()`). The decisions this phase
implements are D1, D3, D7, D13 and D14's unclaimed path in
`PLAN-scheduler-reservations-phase-00-decisions.md`; read them
verbatim before proposing changes.

Planning effort for this phase is **high**; implementation steps
carry their own effort and model recommendations in the step
table. This phase touches the instance-create hot path and
introduces the atomicity primitive the whole plan exists for, so
sub-agents should skew to opus at high effort for the RPC and
integration steps.

## Situation

Phases 1 and 2 landed the materialised capacity counters:
`scheduler_node_capacity`, `namespace_claims` and
`cluster_capacity` exist, and a reconciler recomputes every
counter from ground truth each five minutes on the elected
cluster node. Nothing consumes the counters for admission yet.
Placement is still decided by the in-process `Scheduler` against
a metrics snapshot up to sixty seconds stale, and the burst
window is closed only for CPU, only by the issue-3498 stopgap
(`Scheduler._committed_vcpus()`, PR #3724), which walks
placement rows in Python on every admission.

This phase makes admission atomic: a single `sf-database` RPC
performs the guarded capacity drawdown and the placement write in
one database transaction (D1, D3), the scheduler's callers move
to a pick-then-claim loop (D7), the demand feedforward term
starts accumulating (D13), and the stopgap is deleted by the same
change that supersedes it.

## Mission and problem statement

After this phase, two concurrent creates against one remaining
slot cannot both be admitted; a placed-but-not-yet-booted
instance holds its capacity through the counters rather than
through a Python walk; RAM and disk gain the same
allocation-denominated protection CPU got from the stopgap; and
every code path that writes placement does so through one atomic
primitive, so stale duplicate placement rows stop being
producible.

## What the survey found (2026-08-13)

Everything below was verified against develop at `0ea77f0d4`.
Stale claims found by the survey have been corrected at their
source (master plan phase table and stub text, `docs/plans/index.md`
rows, phase 2 step 7 status) as part of the planning commit.

1. **The master plan's claim that both `_committed_vcpus()`
   exclusions "match `_RECONCILE_USAGE_SQL`" is false for the
   second exclusion.** The reconciler's usage query
   (`shakenfist/mariadb.py:23755`) excludes only
   `state = 'deleted'`; it has no placement-attribute filter and
   charges an instance to every node holding an
   `INSTANCE_LOCATION` row for it — its own comment block
   (`mariadb.py:23703-23712`) documents duplicate counting as an
   open hazard. The stopgap's `placement_filter` exclusion
   therefore has no reconciler counterpart. The master plan stub
   has been corrected; the consequence for this phase is real:
   the counter ledger fail-closes on duplicate placement rows,
   so this phase must stop them being produced (the atomic
   move in the placement RPC) rather than assume they are
   filtered out.
2. **The legacy `node_attributes.instances` dual-write is still
   live** (`shakenfist/node.py:661,668,670-686`) and
   `Node.instances` still unions it in (`node.py:626-654`). The
   reconciler comment (`mariadb.py:23694-23701`) is explicit
   that the admission guard must not be enabled while the union
   exists, because a placement written by a pre-cutover node is
   invisible to the ledger — the non-conservative direction.
   Removing the legacy column is therefore a prerequisite step
   of this phase, not a leftover chore.
3. **Placement today is a non-atomic triple with six writers.**
   `Instance.place_instance()` (`instance.py:968-989`) writes
   the `placement` attribute (masked, single field), then
   best-effort removes the old node's `INSTANCE_LOCATION` row,
   then inserts the new one — three RPCs, no transaction, plus
   the legacy dual-write on each affected node. Callers:
   first placement at `external_api/instance.py:872`; preflight
   redirect at `operations/node_inst_netdesc_op.py:180`; cleaner
   rewrite-to-local at `daemons/cleaner/scheduled_tasks.py:121`
   and `:270`; and a reference-only reconciliation at
   `daemons/queues/startup_tasks.py:179,:189` that reads
   `inst.placement` as the authority and intentionally does not
   write the attribute.
4. **Release is asymmetric with acquire.** `_delete_globally()`
   (`instance.py:1080-1083`) removes the placement reference
   best-effort and conditionally; `hard_delete()`
   (`instance.py:1100-1119`) sweeps all `INSTANCE_LOCATION`
   references but bypasses `Node.remove_instance()` (so the
   legacy column keeps a ghost); the `placement` attribute is
   never cleared. Errored instances keep their placement and
   their ledger charge until hard-delete, deliberately
   (`mariadb.py:23670-23673`).
5. **No placement RPC exists.** Placement flows through the
   generic `UpdateInstanceAttributes` and `RecordRelationship` /
   `RemoveRelationship` RPCs; `grep` finds no `PlaceInstance`-ish
   function in `mariadb.py`. The phase 2 reconcile RPC
   (`protos/database.proto:385`, `mariadb.py:23886/24242/24318`)
   is the pattern to mirror for the new RPCs.
6. **Phase 2's disk limit already answers most of the
   addendum's disk-overcommit worry, but not all of it.**
   `_derive_disk_limit_gb()` (`mariadb.py:23575-23596`) sets
   `limit = used_virtual + max(0, floor(free/GiB) - reservation)`
   — an adaptive shape that refreshes upward each reconcile as
   sparse disks stay empty. What it does not cover is a burst
   *within* one reconcile period: the guard then requires the
   burst's summed virtual size to fit in the last-observed
   actual free space, which is tighter than today's per-request
   check and would reject routine CI concurrency (the addendum
   measured virtual claims at 40-140x actual usage). The
   `SCHEDULER_DISK_OVERCOMMIT` constant is therefore applied to
   the *headroom term* of the derived limit, not to a raw
   physical total (which does not exist as a metric) — see
   decision P3. The addendum's mechanism sentence has been
   refined at source to match.
7. **The demand constants already exist.**
   `SCHEDULER_TARGET_LOAD`, `SCHEDULER_DEMAND_PER_VCPU` and
   `SCHEDULER_DEMAND_DECAY_SECONDS` landed with phase 2
   (`config.py:292-327`), and the reconciler already recomputes
   `expected_demand` decay. Placement-time accumulation is the
   missing half. `SCHEDULER_DISK_OVERCOMMIT` does not exist yet.
8. **Related open flakes this phase bears on:** issues #3602 and
   #3670 (507 `sufficient_idle_cpu` races under suite
   concurrency) are the measurement-staleness class the guarded
   admission is expected to narrow; issue #3565 (affinity vs
   capacity exclusion) is explicitly phase 6, not this phase.

## Decisions

Numbered P1..P8 to avoid colliding with the phase 0 D-numbers.

**P1 — the legacy `node_attributes.instances` column, dual-write
and union are removed now, as step 1 of this phase.** The
transition was scoped as "one release cycle"; no tagged release
has shipped since the refs cutover (394336212, early July 2026),
but every deployment we operate tracks develop and has run the
refs-writing build for over a month, and the guard cannot be
enabled while the union exists (survey item 2). Waiting for a
formal release cycle with no schedule blocks the plan
indefinitely. The accepted cost is that rolling back to a
pre-cutover build (now >1 month old) loses fresh placements
until the cleaner rewrites them. This is the decision a reviewer
is most likely to argue with; the mitigation is that the
rollback floor is recorded in the commit message and the
operator (who is also the release manager) approves this plan
before implementation starts.

**P2 — the guard denominates in the allocation ledger, with the
demand term alongside it; measured-utilisation checks stay in
`find_candidates()` as pre-filters.** The reconciler comment
requires this phase to choose explicitly between `used_*`
(allocation over placed, non-deleted instances) and the resources
daemon's measurements. The guard uses the ledger: that is the
reservation semantics the whole design wants (a powered-off or
still-booting instance holds its capacity), and it is what the
reconciler recomputes, so guard and reconciler agree by
construction. Measured utilisation is not discarded: the D13
demand check (`cpu_load_1 + expected_demand <=
SCHEDULER_TARGET_LOAD x cpu_schedulable`, inputs read from the
typed `node_metrics` columns inside the same transaction) rides
in the admission WHERE clause, and `find_candidates()` keeps its
existing measurement-denominated filters as cheap pre-filters
that reduce guard misses. Deleting `_committed_vcpus()` reverts
`_has_sufficient_cpu()` to measurement-only, which is correct
once the guard exists: the pre-filter orders and prunes, the
guard admits.

**P3 — `SCHEDULER_DISK_OVERCOMMIT` (default 5.0) multiplies the
free-headroom term of `_derive_disk_limit_gb()`, not a physical
total.** `limit = used + max(0, floor(free/GiB) - reservation) x
SCHEDULER_DISK_OVERCOMMIT`. This preserves the property that a
genuinely full disk admits nothing (headroom goes to zero with
free space) while letting a within-period burst of sparse
virtual claims through, which is what the addendum's data says
routine CI needs. The addendum's "physical x overcommit" wording
assumed a total-physical metric that does not exist; the
refinement is recorded in the addendum itself. 5.0 remains
deliberately conservative against the measured median fill
sparsity of ~65x, because virtual size is the growth bound of a
qcow2, not a typical fill; the follow-up variability pass
(~2026-08-26) revisits it.

**P4 — the claim branch of the admission transaction is built
now, dormant.** The canonical-order transaction from D1
(`cluster_capacity`, then `namespace_claims` by uuid, then
`scheduler_node_capacity` by node uuid) is implemented in full:
if the instance's namespace has an active claim row, admission
draws it down; otherwise the cluster row's unclaimed guard
(`unclaimed_used + x <= total - claimed`) applies per D14.
`namespace_claims` is empty until phase 4 lands the API, so the
claim branch is exercised only by unit tests until then — but
building it now means phase 4 adds an object and an API without
reopening the transaction, and the retry/ordering behaviour is
soak-tested before any claim exists to get hurt by it.

**P5 — the RPC takes an `enforce` flag; ground-truth writers do
not enforce.** The scheduler-driven paths (first placement,
preflight redirect) enforce the guard, and a guard failure walks
to the next candidate — D3's "genuine reschedule". The cleaner
rewrites (`scheduled_tasks.py:121,:270`) and the startup-task
reconciliation record where a libvirt domain *already is*; a
guard cannot refuse reality, and refusing to record it would
leave the ledger wrong, which is strictly worse. Non-enforcing
admission still performs every counter update and emits a loud
event when it pushes a node over its limit. This narrows D3's
letter ("if the new node's guard fails, that is a genuine
reschedule") to the paths where a reschedule is possible; for
the cleaner it is not, and the reconciler would repair the
counters within five minutes anyway — the flag just keeps the
ledger honest in the interim.

**P6 — release happens where the reconciler's ground truth stops
counting: at `_delete_globally()`, with `hard_delete()` as the
sweep backstop.** The reconciler excludes instances in state
`deleted`, so the explicit guarded decrement fires when
`_delete_globally()` removes the placement reference (the
release RPC replaces the best-effort `Node.remove_instance()`
call there). A failed create that never placed
(`external_api/instance.py:862,:868`) has nothing to release.
Errored instances keep their charge until hard-delete, exactly
as the ledger already counts them. Decrements are floored at
zero (guarded `used >= x` in the WHERE; on miss, decrement to
zero and log) so a release racing the reconciler cannot drive a
counter negative.

**P7 — a node without a capacity row admits without a guard,
loudly.** Mid-upgrade, a node whose metrics row predates phase 1
has no `scheduler_node_capacity` row (the limit derivation
prefers no row to a guessed one, `mariadb.py:23542-23552`). The
admission RPC treats a missing node row as fail-open: placement
proceeds, counters that exist are still updated, and a warning
event records the unguarded admission. The reconciler creates
the row on its next pass. Fail-closed here would make a
mid-upgrade cluster refuse all creates, which is a worse failure
than one reconcile period of yesterday's behaviour.

**P8 — the `placement` attribute stays authoritative for
*where*; the counters are authoritative for *how much*; the
never-cleared attribute is left alone.** The survey confirmed
nothing ever clears `placement` after delete. Changing that is
tempting and out of scope: `enqueue_delete()` and the event
history both read it after deletion, and the ledger already
handles deleted state correctly. Recorded here so the next
reader does not "fix" it in passing.

## Design

### The admission RPC

`AdmitInstancePlacement` (proto naming per house style), direct
implementation in `sf-database` only — there is no fallback
Python path, because the entire point is one transaction. Request
carries: instance uuid (dashed), namespace, node uuid, cpus,
memory_mb, disk_gb (virtual, from `disk_spec` via the
`_disk_spec_virtual_gb` semantics), the old node uuid if this is
a move (or empty), `enforce`, and the placement JSON to write
(node + incremented `placement_attempts`). One transaction, in
D1's canonical order, retrying the whole transaction on MariaDB
errors 1213/1205/1020:

1. `cluster_capacity` guarded UPDATE (unclaimed branch:
   `unclaimed_used_* + x <= total_* - claimed_*`), or the claim
   branch per P4 when an active `namespace_claims` row exists
   for the namespace.
2. `scheduler_node_capacity` guarded UPDATE for the target node:
   `used_* + x <= limit_*` for the three dimensions, plus the
   D13 demand clause (P2) with `expected_demand` incremented by
   `cpus x SCHEDULER_DEMAND_PER_VCPU` on success. When
   `enforce` is false the WHERE keeps only the PK equality.
3. If a move: guarded decrement of the old node's row and, when
   crossing namespaces is impossible (it is — placement moves
   never change namespace), a wash on the cluster row.
4. `instance_attributes.placement` masked write (single field,
   same SQL the direct `update_instance_attributes` path uses).
5. `object_references`: delete *all* `INSTANCE_LOCATION` rows
   for this instance (not just the old node's — this is what
   makes duplicate placement rows stop being producible, survey
   item 1), then insert the new row.

`rowcount == 0` on a guarded UPDATE aborts the transaction and
returns denied with the failing stage named, so the caller's
walk and the D9 diagnostics both know why. A missing node
capacity row follows P7. The reply carries admitted/denied, the
failing dimension(s), and the post-claim counter values (via
follow-up PK SELECT in the same transaction — no `UPDATE ...
RETURNING` in MariaDB).

`ReleaseInstancePlacement` mirrors it: guarded floor-at-zero
decrements (P6) on node and cluster (or claim) rows, delete of
the instance's `INSTANCE_LOCATION` rows, no attribute write.

### Integration points

- `Instance.place_instance()` keeps its signature, early-out and
  attribute lock, and becomes the sole caller of the admission
  RPC for placement writes; it grows an `enforce` parameter
  defaulting True and raises a typed exception on denial. Its
  resource arguments come from the instance's static values.
- `external_api/instance.py` create path: walk
  `find_candidates()` output, `place_instance(candidate)`, on
  denial move to the next candidate, on exhaustion return the
  existing 507 with the denial detail in the audit event.
- `node_inst_netdesc_op.py` preflight redirect: same walk over
  its candidate list at `:179-180`.
- Cleaner (`scheduled_tasks.py:121,:270`) and
  `startup_tasks.py:179,:189`: `enforce=False`. The
  startup-tasks path switches from raw reference writes to the
  RPC so its reconciliation also cannot create duplicates.
- `_delete_globally()` calls the release RPC where it now calls
  `Node.remove_instance()`; `hard_delete()`'s reference sweep
  becomes a release call too (idempotent: zero rows deleted and
  floored decrements make a double release harmless).
- `Scheduler`: delete `_committed_vcpus()` and the `verified`
  machinery; `_has_sufficient_cpu()` reverts to
  measurement-only; `_placed_instances()` stays (the affinity
  pass uses it); `summarize_resources()` publishes
  `cpu_committed` from `scheduler_node_capacity.used_cpus`
  instead of the Python walk (one SQL read for all nodes), with
  the measured figure still published beside it. Check
  `instance.placement_filter()` for remaining users before
  removing it.

### Out of scope

- No claims API, objects, events or client verbs (phase 4).
- No removal of the in-Python capacity pre-filters and no
  candidate-query-in-SQL rework (phase 5, D11).
- No affinity changes (phase 6) and no diagnostic-mode rework
  beyond denial detail in existing audit events (phase 7).
- No clearing of the post-delete `placement` attribute (P8).

## Execution

| Step | Description | Effort | Model | Isolation | Status |
|------|-------------|--------|-------|-----------|--------|
| 1 | Remove the legacy `node_attributes.instances` column handling per P1: delete `_dual_write_legacy_instances()` and its calls (`node.py:661,:668,:670-686`), drop the union from `Node.instances` (`node.py:626-654`), remove the field from the node-attributes schema model, update the reconciler comment block (`mariadb.py:23694-23701`) to say the precondition is now met, fix affected unit tests. Commit message records the rollback floor (P1) | medium | sonnet | worktree | Not started |
| 2 | Add `SCHEDULER_DISK_OVERCOMMIT` (float, default 5.0) to `config.py` beside the other overcommit ratios; apply it to the headroom term in `_derive_disk_limit_gb()` per P3; thread it through the reconcile RPC request like the demand constants; unit tests for the scaled limit incl. zero-free and reservation-exceeds-free edges; document in `docs/operator_guide/database.md` | medium | sonnet | worktree | Not started |
| 3 | The admission and release RPCs: proto messages + `tox -e genprotos`, direct-layer implementation in `sf-database` per the Design section (canonical order, claim branch per P4, `enforce` per P5, P6 floors, P7 fail-open, named failing stage, retry on 1213/1205/1020), tri-layer wrappers in `mariadb.py`, servicer + Monitor registration in `daemons/database/main.py`. Unit tests: rowcount semantics, each guard dimension denying, claim vs unclaimed branch, move vs first placement, double release, missing node row | high | opus | worktree | Complete — see step 3 notes |
| 4 | Wire the non-scheduling paths onto the primitive: `place_instance()` rework (sole RPC caller, typed denial exception), `_delete_globally()` / `hard_delete()` release, cleaner and startup-tasks `enforce=False` calls. Unit tests for each path; check `placement_filter()` users | high | opus | worktree | Complete — see step 4 notes |
| 5 | Scheduler-side integration: pick-then-claim walk in the create path and preflight redirect; delete `_committed_vcpus()` and revert `_has_sufficient_cpu()`; `summarize_resources()` reads the counters. This is the commit that closes issue 3498's stopgap; "Fixes" trailers per the tracker | high | opus | worktree | Complete — see step 5 notes |
| 6 | Concurrency validation against a docker MariaDB (mirror phase 2 step 4): two threads racing one slot admit exactly once; a 50-create burst against known capacity admits exactly the fitting prefix; release/re-admit cycling leaves counters at reconciler ground truth. Record results in the Validation section. Add a functional smoke assertion to `shakenfist_ci` that a create emits the admission audit event | high | opus | worktree | Complete — see step 6 notes. Validation ran and **found a blocker**: correct under contention, but ER_CHECKREAD (1020) retry exhaustion on MariaDB 11.6.2+ turns concurrent creates into 500s. Left unfixed for operator direction; blocks step 9 |
| 7 | Docs: `docs/operator_guide/database.md` (counters now consumed; the two RPCs), scheduler sections of `docs/`, CLAUDE.md scheduler-capacity paragraph (counters consumed as of this phase; stopgap gone), ARCHITECTURE.md/AGENTS.md if warranted; master plan and `index.md` phase rows | low | sonnet | worktree | Not started |
| 8 | Management-session code review against the checklist below | medium | management session | none | Not started |
| 9 | Operator review and PR; deploy to sfcbr and soak: reconciler drift metric stays zero with admission live, no 507 regression in CI pass rates | — | operator | — | Not started |

## Risks and mitigations

- **Rolling upgrade under-count (survey item 2's cousin):** an
  old sf-api placing via the old triple while new nodes guard
  via counters. Window is one deploy cycle; the reconciler
  corrects within five minutes; sfcbr deploys all nodes in one
  ansible pass. Checked by: operator watches the drift metric
  during the step 9 soak.
- **False denials from stale limits:** `limit_*` refreshes only
  each reconcile pass, so a node whose real headroom grew
  mid-period can deny. The caller walks remaining candidates, so
  a single stale node cannot fail a create unless every node
  denies; the 507 carries the failing dimensions. Checked by:
  step 6's burst test and the step 9 CI soak.
- **Deadlock/livelock regression of the phase 0 benchmark
  findings:** mitigated by canonical order and whole-transaction
  retry; step 3's unit tests assert the retry path, and step 6
  runs the race against real MariaDB.
- **Hot-path latency:** admission adds one RPC to create, but
  removes the stopgap's per-candidate placement walks and
  `summarize_resources()`'s unconditional walk; net database
  load goes down. Checked by: step 6 timings recorded in
  Validation.
- **Sub-agent scope creep into phases 4-6:** the Out of scope
  list is explicit in every brief; the management review checks
  the diff touches no claims API, no affinity logic, no
  pre-filter removal.

## Validation

### Step 3: implementation notes (2026-08-14)

The RPCs were implemented as designed, with live-MariaDB tests run
against a docker MariaDB 11 under `utf8mb4_bin` during the step
itself (30 new live tests beside the existing 22; the full live
suite passes). Design refinements made during implementation, all
now reflected in the code's docstrings:

- **A move skips the cluster/claim stage entirely.** The design's
  "no cluster-row wash" was underspecified: the first cut
  incremented the namespace-side ledger on a move with nothing to
  decrement (the old node's row is on the node side), inflating
  the namespace by one instance per move until the next reconcile
  — caught by the live tests. Both the cluster singleton's
  unclaimed sums and a claim's `used_*` are namespace-denominated
  and node-independent, and a move never changes namespace, so a
  move consumes nothing on that side and is never refusable at
  that stage.
- **Canonical order extended.** Release also runs cluster/claim
  before node rows (the step 8 checklist's "everywhere" reading),
  and the two `scheduler_node_capacity` rows in a move are updated
  in uuid order — otherwise two moves crossing between the same
  node pair deadlock.
- **Fail-open also covers a missing `cluster_capacity` singleton**
  (a cluster whose reconciler has never run), same reasoning as
  P7's missing node row; `unguarded=true` in the reply either way.
- **`target_load <= 0` disables the demand clause** rather than
  denying everything, mirroring the disk-overcommit `<= 0`
  fallback — an unset proto3 double reads as 0.0 from a
  mid-upgrade caller.
- **Denial detail is double-typed and includes a `demand`
  pseudo-dimension**, since a D13 refusal would otherwise report
  no exceeded dimension at all.
- **`enforce=False` keeps the rowcount check** with a key-only
  WHERE: a zero rowcount then means the row was concurrently
  deleted, which is a legitimate abort rather than a denial.
- **D1's rowcount question is answered and pinned.** SQLAlchemy's
  mysqldb dialect sets `CLIENT_FOUND_ROWS`, so `rowcount` counts
  matched rows; a guarded UPDATE whose SET is a no-op reads as
  "guard passed". A live test asserts this against a real server.
- Retries use a new `_retry_transaction` (1213/1205/1020) rather
  than widening the lock paths' `_retry_on_deadlock`, which must
  keep 1205 non-retryable. Statements use SQLAlchemy core rather
  than `sa.text()` so `sa.Uuid` binding is handled by the dialect
  (the pitfall-6 hazard); no statement joins the dashed and
  undashed forms.

### Step 4: implementation notes (2026-08-14)

- **P5's over-limit event is derived by probe-then-force.** The
  reply of a non-enforced admission carries post-admit counters
  but not limits, so "did this push the node over?" cannot be read
  from it. `Instance._admit_placement()` therefore always calls
  the RPC guarded first: an admit is the common case and costs no
  extra RPC; a denial rolled back cleanly, names the exceeded
  dimensions, the loud event is emitted with that detail, and the
  placement is then recorded unguarded. Ground truth always wins;
  the ledger records reality either way.
- **An RPC failure is not a denial.** `success=False` (database
  unreachable, malformed input) raises `WriteException` for
  enforcing callers — it must not read as "the cluster is full" to
  a caller walking candidates — and for `enforce=False` callers
  logs loudly and returns, so a database blip cannot abort a
  cleaner pass; the attribute was not changed, and the next pass
  retries.
- **`Node.add_instance()` / `remove_instance()` are deleted.**
  After the rewiring their only callers were tests. The
  `Node.instances` read property stays.
- The startup reconciliation has four branches on the primitive:
  repair-in-place, move-to-authoritative-node (the RPC's
  delete-all-then-insert removes the stale local row),
  release-for-deleted, and a zero-amount release for a reference
  whose instance row is already gone (nothing to read sizes from;
  the reconciler trues up the counters within a pass).
- `instance.py` reuses `mariadb._disk_spec_virtual_gb()` (the
  reconciler's executable disk-sum specification) and
  `mariadb._json_dumps()` (so the placement column bytes match the
  generic attribute path); both uses carry NOTE comments and are
  candidates for promotion to public helpers in review.
- Stale prose found for step 7: `ARCHITECTURE.md` ~825-838,
  `AGENTS.md` ~437-461 and `docs/operator_guide/database.md` ~756
  still describe the legacy dual-write and/or the stopgap.

### Step 5: implementation notes (2026-08-14)

- **The walk needed a read API the plan had not scoped.**
  `summarize_resources()` had no way to read the counters, so this step
  adds `GetSchedulerNodeCapacity` (empty request, repeated row reply) and
  its tri-layer wrappers. It is an unfiltered `SELECT` of a table with
  one row per schedulable hypervisor; an error or an unreadable table
  reads as no rows, because a node without a row is charged nothing and
  guarded by nothing anyway (P7).
- **`cpu_committed_row_present` was added to the per-node summary.** A
  zero `cpu_committed` now has two meanings — a node holding nothing, or
  a node the reconciler has not sized — and only the second one also
  means "and this node is admitting unguarded". The cluster CI
  assertion in `cluster_ci_tests/test_nodes.py` skips on the second.
- **`_has_sufficient_cpu()` lost its `memo` parameter** along with the
  stopgap; nothing else in it wanted the placements. `placements` is
  still built in `find_candidates()`, moved down to the affinity pass
  which is now its only consumer, and `_placed_instances()` stays.
- **`instance.placement_filter()` stays.** After the deletion its
  remaining callers are `this_node_filter()`,
  `healthy_instances_on_node()` and `instance_blob_usage()`, all
  production, so neither it nor its tests were removed.
- **The requested-placement branch now walks the returned list** rather
  than reusing `placed_on`. It is a one-element list by construction, so
  the behaviour is unchanged, but there is now exactly one walk to
  reason about.
- The create path deliberately does not catch `WriteException`: an
  unreachable database is not a full cluster, and asking the next
  candidate would only ask it the same question. A unit test pins that a
  write failure surfaces as a 500 rather than as the 507.
- Stale prose remaining for step 7: `docs/operator_guide/scheduler.md`
  ~176, ~235 (names the retired `committed_cpus` rejection-reason field)
  and ~258, plus `ARCHITECTURE.md` ~825-838 and
  `docs/operator_guide/database.md` ~756 carried over from step 4.

### Step 6: docker-MariaDB concurrency validation (2026-08-14)

**Headline: the primitive is correct under contention and it is
not yet shippable on a current MariaDB.** No run of any scenario
ever over-admitted, lost a counter or left the reconciler
anything to repair. But on a server with
`innodb_snapshot_isolation` ON — the default from MariaDB 11.6.2,
which is what Debian 13, Ubuntu 24.04's `mariadb:11` image and
every recent container tag give you — three of the five
scenarios fail with the admission RPC returning
`success=False`, which `Instance._admit_placement()` raises as
`WriteException` and the create path turns into an HTTP 500.
Details and root cause below; **this is a blocker for step 9 and
is left unfixed deliberately, for the operator to direct.**

#### Environment and harness

Server: MariaDB `11.8.8-MariaDB-ubu2404` in a disposable
`mariadb:11` container, database collation `utf8mb4_bin` (the
strict collation the live suites deliberately test under, flipped
per test and restored in cleanup), `--max-connections=500`,
`innodb_lock_wait_timeout` 50, `transaction_isolation`
REPEATABLE-READ. Every scenario was run under both
`innodb_snapshot_isolation` ON (the server default) and OFF.

The harness is a new `PlacementAdmissionConcurrencyLiveTestCase`
in `shakenfist/tests/test_mariadb_capacity_admission_live.py`,
beside the existing single-threaded suite, which was refactored
onto a shared `_LiveCapacityFixture` so both share one database
setup. It is a kept, repeatable harness rather than a one-off
script, as the success criteria require. Every test starts a
`threading.Barrier` so the calls genuinely overlap, and every
test reports the server version, collation and snapshot-isolation
setting it ran under: a concurrency result that does not name the
regime is not a result. All 52 pre-existing live tests passed
before anything was added, and still do.

#### Scenario results (`innodb_snapshot_isolation` OFF)

These are the numbers for the regime the primitive currently
behaves correctly in.

* **Race for one slot** — 20 rounds x 8 threads = 160 admissions
  against a node seeded with room for exactly one more instance.
  Every round: exactly 1 admitted, 7 denied, all denials clean
  (`success=True`, `failing_stage='node'`, at least one dimension
  flagged exceeded) and no exception ever surfaced from a worker.
  Counters after each round exactly `used_cpus` 12,
  `used_memory_mb` 12288, `used_disk_gb` 120,
  `expected_demand` 10.0, cluster `unclaimed_used_*` 12 / 12288 /
  120, and exactly one `INSTANCE_LOCATION` row across all eight
  instances — so no denied transaction left its cluster-row
  increment behind. **Admission timing: median 15.7 ms, p99
  22.0 ms, max 23.0 ms.**
* **Burst admission** — 50 concurrent admissions, alternating
  between a node bound by cpus (`limit_cpus` 12, fits 3) and one
  bound by memory (`limit_memory_mb` 8192, fits 2), against a
  cluster singleton with room for all of them so the node rows
  are what refuse. Exactly 5 admitted (3 and 2) and 45 denied.
  Final counters exactly 12 / 12288 / 120 and 8 / 8192 / 80;
  cluster 20 / 20480 / 200. Every denial named `node` as the
  failing stage and flagged the dimension that node is actually
  bound by, with `used + requested > limit` re-checked against
  the live values. `expected_demand` accumulated to 30.0 and
  20.0, summing to 50.0 = 20 admitted vCPUs x
  `SCHEDULER_DEMAND_PER_VCPU` — once per admission, not once per
  attempt. This is the first demonstration that RAM binds as an
  allocation-denominated dimension. **Timing: median 98.1 ms, p99
  124.6 ms** (50 transactions serialising on one singleton row).
* **Move and duplicate elimination** — an instance placed on one
  node, a stale duplicate `INSTANCE_LOCATION` row then planted on
  a third node (the survivor a best-effort removal in the old
  non-atomic triple could leave), then a move with `old_node`
  set. Old node decremented to 0 / 0 / 0, new node 4 / 4096 / 40,
  and exactly one reference row survives — on the new node,
  taking the planted duplicate with it although no caller named
  that node. The namespace side is untouched: with an active
  `namespace_claims` row for the namespace, the claim's `used_*`
  is identical before and after (4 / 4096 / 40) and the cluster
  singleton stays at 0.
* **Crossing moves** — 8 simultaneous moves, 4 in each direction
  between the same pair of nodes, the case the uuid-ordered
  intra-table statement order exists for. All admitted, no
  clamps, both nodes ended at 16 / 16384 / 160.
* **Randomised cycling with reconciler agreement** — 6 threads x
  60 operations = 360 randomised admits, moves and releases over
  12 instances of three sizes and 4 deliberately uneven nodes
  (`cpu_schedulable` 16 / 8 / 4 / 2, so the small ones deny).
  A representative run: 125 admits, 97 moves, 116 releases, 22
  denials, no clamp and no RPC failure. Operations are serialised
  per instance to model the attribute lock `place_instance()`
  holds. The counters were then checked twice, against two
  independent oracles: a Python model built from the replies
  (exact on every node and on the cluster singleton), and a full
  reconcile pass — `delta_used_cpus`, `delta_used_memory_mb` and
  `delta_used_disk_gb` **zero on every node**, `nodes_added` 0,
  `nodes_removed` 0, and the rebuilt cluster `unclaimed_used_*`
  identical to the pre-reconcile row. P2's "guard and reconciler
  agree by construction" is now tested rather than argued.
  **Timing: median 8.8 ms, p99 16.9 ms.**

The double-release, dormant-claim-branch and fail-open cases
named in the step 6 brief are already covered by the
single-threaded suite written during step 3
(`test_double_release_is_harmless`,
`test_an_active_claim_is_drawn_down_instead_of_the_cluster` and
its siblings, `test_a_node_with_no_capacity_row_admits_unguarded`,
`test_a_cluster_with_no_singleton_admits_unguarded`); they were
re-run here rather than duplicated, and all 30 pass.

#### The finding: ER_CHECKREAD under snapshot isolation

With `innodb_snapshot_isolation` ON, the same harness gives:

| Scenario | Concurrency | Result |
|---|---|---|
| Race for one slot | 8 | passes, median 27.1 ms / p99 35.7 ms (1.7x the OFF regime — that is retry backoff, not database work) |
| Move and duplicate | 1 | passes |
| Crossing moves | 8 | **fails**: 1020 on `scheduler_node_capacity` |
| Burst | 50 | **fails**: 1020 on `cluster_capacity` |
| Randomised soak | 6 | **fails**: 1020 on `cluster_capacity` |

The error is `ER_CHECKREAD` (1020), *"Record has changed since
last read in table 'cluster_capacity'; try restarting
transaction"*. Under snapshot isolation a guarded `UPDATE` whose
target row moved since the transaction's snapshot does not block
and re-evaluate its `WHERE`; it aborts immediately and the client
must restart the transaction.

**Root cause, and phase 0 predicted it exactly.** The phase 0
findings' step 2 benchmark results say (in
`PLAN-scheduler-reservations-phase-00-findings.md`): "ER_CHECKREAD
(1020) never fired... The guarded UPDATE is the transaction's
first statement, so the snapshot is established by the DML itself
and there is no stale-snapshot window. **The risk returns if a
plain SELECT precedes the guarded UPDATE inside the same RR
transaction.**" That is what
`_direct_admit_instance_placement()` now does: it opens the
transaction with three non-locking `SELECT`s (the active claim
lookup, the node-row presence probe and the cluster-singleton
presence probe) before touching a guarded `UPDATE`. Those probes
exist for good reasons — the P4 branch select, and P7's fail-open
— but they establish the snapshot early, and every admission then
races every other admission on the `cluster_capacity` singleton,
the hottest row in the design.

**Correctness is not affected.** In no run, in either regime, at
any retry budget, did the guard admit more than the seeded
capacity or leave a counter wrong. The failure is availability
and latency: the transaction aborts and, once the retry budget is
gone, the RPC reports a hard error rather than an admission or a
denial. `Instance._admit_placement()` correctly refuses to read
that as "the cluster is full" and raises `WriteException`, so the
user-visible symptom is a 500 on instance create under
concurrency, not a wrong placement.

**The retry budget is one attempt short.** Instrumenting
`_retry_transaction` (diagnosis only, not committed) over the
50-way burst at the shipped `_TRANSACTION_MAX_ATTEMPTS = 4` gives
an attempts histogram of `{1: 2, 2: 1, 3: 1, 4: 46}` — 46 of 50
transactions hit the ceiling. Raising the budget to 8 makes all
five scenarios pass under snapshot isolation with counters exact
and reconciler drift still zero (histogram
`{1: 249, 2: 213, 3: 44, 4: 25, 5: 49, 6: 8}` across the whole
suite), but at a cost: the burst's median admission goes to
313.8 ms and the soak's p99 to 179.8 ms, against 98.1 ms and
16.9 ms with snapshot isolation off. A budget bump alone buys
correctness back by burning wall time on the instance-create hot
path, which is the opposite of the "net database load goes down"
claim in this plan's risk table. The principled fix is to stop
establishing the snapshot early — make a guarded `UPDATE` the
transaction's first statement and fold the presence probes into
it or into the retry path — which is a change to the primitive
and therefore out of scope for a validation step.

**CI would not have caught this.** The live suites run in the
`schema_enum_widening` job on a `debian-12` runner, whose
`mariadb-server` is 10.11, where `innodb_snapshot_isolation` does
not exist. The whole new suite passes there. Whatever fix is
chosen, the harness needs to run against a server with the
variable ON before it can be believed.

#### Everything else that failed first time

* The crossing-moves scenario failed its first run for a reason
  that turned out to be the test's own seeding, not the code:
  with the default `demand_add` of `cpus x
  SCHEDULER_DEMAND_PER_VCPU`, eight placements followed by eight
  moves push `expected_demand` past `SCHEDULER_TARGET_LOAD x
  cpu_schedulable` and D13 denies the moves. The test now passes
  `demand_add=0.0`, but the behaviour is worth recording as an
  operational property: a move adds the new node's feedforward
  term without crediting the old node's back (deliberately — see
  `test_a_move_does_not_credit_expected_demand_back`), so an
  instance churning between nodes inflates cluster-wide demand
  until the next reconcile pass recomputes it from placement
  ages. Self-healing within five minutes, but a node that sees
  heavy preflight-redirect traffic can talk itself out of
  admitting.
* Nothing else. The refactor of the existing suite onto the
  shared fixture was clean on the first run, and the previously
  existing 52 live tests passed unchanged throughout.

#### Functional smoke assertion

`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_events.py`'s
`test_instance_events` now also asserts that creating an instance
emitted the step 4 `instance placed` audit event (message string
checked against what `Instance._admit_placement()` actually
emits). It polls for up to 30 s, because events are eventually
consistent, and tolerates extra events and more than one
placement — a preflight redirect or a cleaner rewrite-to-local
legitimately places the same instance again. This runs in cluster
CI, not in the docker harness.

## Administration and logistics

### Success criteria

* Two concurrent admissions for one remaining slot admit exactly
  one (step 6's race test, kept as a repeatable harness).
* `git grep _committed_vcpus` and
  `git grep _dual_write_legacy_instances` both return nothing.
* Every production writer of placement reaches the database
  through the admission/release RPCs: `git grep -n
  "record_relationship.*INSTANCE_LOCATION"` shows no callers
  outside `mariadb.py`'s RPC implementation and the migration
  seeding path.
* A create on a full cluster returns 507 with the failing
  dimensions in the audit event, at detail equivalent to the
  stopgap's rejection reasons.
* The reconciler computes zero drift after step 6's randomised
  operation soak, and during the sfcbr soak (step 9).
* RAM and disk admission are allocation-denominated for the
  first time; the step 6 burst test demonstrates both binding.
* `pre-commit run --all-files` passes; mypy coverage of the new
  RPC surface is complete (no untyped defs).
* No fact about capacity accounting is stated differently in
  `CLAUDE.md`, `docs/operator_guide/database.md` and the master
  plan.

### Review checklist (management session, step 8)

- [ ] Guarded UPDATEs follow canonical order everywhere,
      including the release and move paths.
- [ ] The concurrent-scheduling test exercises real MariaDB, not
      mocks (master plan checklist item).
- [ ] `hard_delete()` accounts for capacity release (master plan
      checklist item); double release is harmless.
- [ ] `enforce=False` paths update counters and emit the
      over-limit event.
- [ ] No caller outside `mariadb.py` writes `INSTANCE_LOCATION`
      rows or the `placement` attribute directly.
- [ ] The claim branch is unreachable in production until phase
      4 (no API can create a `namespace_claims` row) but fully
      unit-tested.
- [ ] Diff contains no phase 4/5/6 material.
- [ ] mypy clean; single quotes; 120-char lines.

### Future work

- The follow-up addendum variability pass (~2026-08-26) revisits
  `SCHEDULER_DISK_OVERCOMMIT = 5.0` with a fortnight of
  generation-2 conductor data.
- Phase 5 removes the in-Python capacity pre-filters and
  considers moving candidate filtering into SQL (D7's read-only
  candidate query is only partially realised by this phase).
- Issues #3602/#3670 (507 races under suite concurrency) should
  be re-triaged after the sfcbr soak: the guard changes their
  mechanics, and they may resolve or need the phase 7
  diagnostics to progress.
- The `placement` attribute is never cleared after delete (P8);
  worth a small cleanup once nothing reads it post-delete.

### Bugs fixed during this work

*(populated during implementation)*

### Back brief

Before executing any step of this plan, back brief the operator
on this plan and its alignment with the master plan — in
particular P1 (legacy column removal now) and P5 (the `enforce`
flag's narrowing of D3), which are the two decisions most likely
to draw argument.
