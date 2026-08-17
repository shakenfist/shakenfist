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

Numbered P1..P9 to avoid colliding with the phase 0 D-numbers.

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

**P9 (added 2026-08-14, after the first smoke CI run) — the D13
demand clause can never fail a create on its own; walkers waive
it on a second pass.** The first smoke CI run of this branch
locked its single node out permanently: three suite workers
creating 1-vCPU instances pushed `expected_demand` to 8–12
against a bound of `SCHEDULER_TARGET_LOAD × cpu_schedulable =
0.75 × 8 = 6`, while measured load was 0.45 and every real
dimension read `used 0.0` — 13 tests failed with 507s from a
node that was essentially idle. No constant survives that
arithmetic on a small node under churn, so this is structural,
not tuning: demand is a spreader (close the
actuation-to-observation gap so bursts fan out across nodes),
not a capacity bound. Both walkers (create path and preflight
redirect) now re-walk once with `enforce_demand=False` when the
enforced walk admitted nowhere and at least one denial was
demand-only (`CapacityAdmissionDenied.demand_only`: node stage,
`demand` the only exceeded dimension). The waiver is expressed
as a zero `target_load`, which the guard already treats as
"clause disabled" (its mid-upgrade proto3 semantics), so the
RPC and transaction are unchanged; a waived admission still
guards every real dimension and still accumulates its demand
contribution. The trigger is *any* demand-only denial rather
than *all*, because a mixed exhaustion (one node genuinely
full, another merely demand-hot) also has free real capacity
that pre-D13 code would have admitted. The demand constants
remain provisional (D13's learner is still future work); the
waiver makes their miscalibration cost a second walk instead of
a failed create.

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

The branch select of step 1, and the presence probes P7 needs, run
on a separate autocommit connection *before* the transaction opens.
That is not an optimisation, it is a correctness requirement on
MariaDB 11.6.2+: a plain SELECT inside the transaction establishes
its read view early and every later guarded UPDATE against a
contended row then aborts with ER_CHECKREAD instead of blocking and
re-evaluating. See step 6a for the measurement and the TOCTOU
consequences.

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
| 1 | Remove the legacy `node_attributes.instances` column handling per P1: delete `_dual_write_legacy_instances()` and its calls (`node.py:661,:668,:670-686`), drop the union from `Node.instances` (`node.py:626-654`), remove the field from the node-attributes schema model, update the reconciler comment block (`mariadb.py:23694-23701`) to say the precondition is now met, fix affected unit tests. Commit message records the rollback floor (P1) | medium | sonnet | worktree | Complete — `git grep _dual_write_legacy_instances` finds nothing outside this plan |
| 2 | Add `SCHEDULER_DISK_OVERCOMMIT` (float, default 5.0) to `config.py` beside the other overcommit ratios; apply it to the headroom term in `_derive_disk_limit_gb()` per P3; thread it through the reconcile RPC request like the demand constants; unit tests for the scaled limit incl. zero-free and reservation-exceeds-free edges; document in `docs/operator_guide/database.md` | medium | sonnet | worktree | Complete — `SCHEDULER_DISK_OVERCOMMIT` is at `config.py:401` |
| 3 | The admission and release RPCs: proto messages + `tox -e genprotos`, direct-layer implementation in `sf-database` per the Design section (canonical order, claim branch per P4, `enforce` per P5, P6 floors, P7 fail-open, named failing stage, retry on 1213/1205/1020), tri-layer wrappers in `mariadb.py`, servicer + Monitor registration in `daemons/database/main.py`. Unit tests: rowcount semantics, each guard dimension denying, claim vs unclaimed branch, move vs first placement, double release, missing node row | high | opus | worktree | Complete — see step 3 notes |
| 4 | Wire the non-scheduling paths onto the primitive: `place_instance()` rework (sole RPC caller, typed denial exception), `_delete_globally()` / `hard_delete()` release, cleaner and startup-tasks `enforce=False` calls. Unit tests for each path; check `placement_filter()` users | high | opus | worktree | Complete — see step 4 notes |
| 5 | Scheduler-side integration: pick-then-claim walk in the create path and preflight redirect; delete `_committed_vcpus()` and revert `_has_sufficient_cpu()`; `summarize_resources()` reads the counters. This is the commit that closes issue 3498's stopgap; "Fixes" trailers per the tracker | high | opus | worktree | Complete — see step 5 notes |
| 6 | Concurrency validation against a docker MariaDB (mirror phase 2 step 4): two threads racing one slot admit exactly once; a 50-create burst against known capacity admits exactly the fitting prefix; release/re-admit cycling leaves counters at reconciler ground truth. Record results in the Validation section. Add a functional smoke assertion to `shakenfist_ci` that a create emits the admission audit event | high | opus | worktree | Complete — see step 6 notes. Validation found a blocker (ER_CHECKREAD retry exhaustion on MariaDB 11.6.2+ turning concurrent creates into 500s); **resolved in step 6a**, which is where the shipping numbers are |
| 6a | Fix the step 6 blocker in the primitive: move the branch select and presence probes out of both transactions so a guarded UPDATE is the first statement, per the phase 0 finding. Re-run the full live suite twice under `innodb_snapshot_isolation` ON and the concurrency class once under OFF | medium | opus | worktree | Complete — see step 6a notes |
| 7 | Docs: `docs/operator_guide/database.md` (counters now consumed; the two RPCs), scheduler sections of `docs/`, CLAUDE.md scheduler-capacity paragraph (counters consumed as of this phase; stopgap gone), ARCHITECTURE.md/AGENTS.md if warranted; master plan and `index.md` phase rows | low | sonnet | worktree | Complete — see step 7 notes |
| 8 | Management-session code review against the checklist below | medium | management session | none | Complete — 1 fix + 6 considers applied, 2 recorded; see step 8 notes |
| 9 | Operator review and PR; deploy to sfcbr and soak: reconciler drift metric stays zero with admission live, no 507 regression in CI pass rates | — | operator | — | Partially complete — reviewed and merged as PR #3754 on 2026-08-16; the sfcbr deploy and soak have not been run |

## Risks and mitigations

- **Rolling upgrade under-count (survey item 2's cousin):** an
  old sf-api placing via the old triple while new nodes guard
  via counters. Window is one deploy cycle; the reconciler
  corrects within five minutes; sfcbr deploys all nodes in one
  ansible pass. Checked by: operator watches the drift metric
  during the step 9 soak.
- **Mixed-version creates fail loudly (PR review, item 2):**
  the inverse window -- a new sf-api calling an old sf-database --
  gets UNIMPLEMENTED for AdmitInstancePlacement, which has no
  Python fallback and is deliberately a failed write rather than
  "cluster full", so every create 500s until the database tier is
  upgraded. Mitigated by ordering, not code: upgrade the database
  tier before the API nodes (now stated in
  `docs/operator_guide/database.md`), and the error string names
  the condition so a mis-ordered rollout diagnoses itself.
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
Details and root cause below. **This was a blocker for step 9; it
was fixed on 2026-08-14 by the restructure in step 6a below, and
the full live suite now passes twice back to back with
`innodb_snapshot_isolation` ON at the shipped retry budget. Read
this section for the diagnosis and 6a for the resolution.**

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

### Step 6a: snapshot-isolation fix (2026-08-14)

**Headline: the blocker is fixed, at the shipped retry budget, with
no cost in latency.** The full live suite passes twice back to back
with `innodb_snapshot_isolation` ON, ER_CHECKREAD (1020) never fires
at all across the whole concurrency class, and the timings are back
on the OFF-regime baseline rather than the 3x figures a retry-budget
bump bought.

#### The restructure

The fix is the principled one step 6 named and deliberately did not
take: **no plain `SELECT` may precede the first guarded `UPDATE`
inside the transaction**, because that `SELECT` is what establishes
the read view early. So the reads moved out rather than the budget
moving up.

* `_direct_admit_instance_placement()`'s three probes — the P4
  branch select via `_active_claim_for_namespace()`, the node-row
  presence probe and the cluster-singleton presence probe — are now
  a single `_probe_admission_rows()` on its own autocommit
  connection, run before `engine.begin()` and returning an
  `_AdmissionProbe` namedtuple. It runs *inside* the retried
  closure, so a transaction that loses a race re-reads the world on
  its next attempt rather than re-deciding on the losing attempt's
  view.
* Inside the transaction the statement order is unchanged and
  canonical: the cluster-or-claim guarded `UPDATE` first (a move
  still skips that stage, so its first statement is one of the two
  uuid-ordered `scheduler_node_capacity` writes; a fully fail-open
  admission's first statement is the placement attribute write — an
  `UPDATE` either way), then the node rows, then the placement
  attribute, then the reference `DELETE`/`INSERT`, then the
  post-admit counter `SELECT`. That last read is deliberately left
  where it is: reads *after* our own writes are safe, because those
  rows are locked by the `UPDATE`s we already issued. The D13 demand
  clause's subselect against `node_metrics` also stays inside the
  guarded `UPDATE`, which is exactly the shape phase 0 benchmarked
  clean — it is part of the DML that establishes the read view, not
  a statement before it.
* `_direct_release_instance_placement()` had the same defect and got
  the same treatment: `_instance_location_nodes()` and the claim
  branch select are now `_probe_release_rows()`, outside the
  transaction, whose first statement is therefore the floored
  namespace decrement. A release with nothing held now opens no
  transaction at all.
* `_TRANSACTION_MAX_ATTEMPTS` stays at 4 and 1020 stays in
  `_TRANSIENT_TRANSACTION_ERRNOS`, belt and braces: MDEV-39263
  reports the error firing "most of the time, but not every time",
  so its absence is not something to rely on.

The invariant is stated as a block comment above
`_probe_admission_rows()` citing the phase 0 step 2 finding, echoed
at the top of each transaction body, and repeated in `AGENTS.md`.

#### TOCTOU: what the moved probes now race, and why it is fine

Every one of the moved reads is time-of-check-to-time-of-use racy by
construction. Each resolves as either a spurious single-candidate
denial — the caller walks to its next candidate, and the state that
caused the denial is the new truth anyway — or an unguarded
admission the reconciler trues up within a pass. Neither violates
the ledger:

* **Claim branch.** A claim created or expired in the window sends
  one admission down the other branch. Both branches are
  namespace-denominated ledgers the reconciler recomputes from
  ground truth every pass, so the worst case is one instance charged
  to the cluster's unclaimed sums instead of the claim (or the
  reverse) for up to one reconcile period. Nothing can create a
  claim before phase 4 lands the claims API, so today the branch is
  dormant.
* **Node-row presence.** Present-then-deleted makes the node guard
  match no row, which reads as a denial — and a node whose capacity
  row just vanished is not a node this placement wanted.
  Absent-then-created admits unguarded and says so in the reply,
  which is precisely what P7 already does for a node the reconciler
  has not sized.
* **Cluster singleton presence.** Identically: a spurious denial, or
  one unguarded admission, both self-correcting.
* **Release's reference lookup.** This one was *already* racy before
  it moved: it was a plain non-locking read, so two concurrent
  releases of the same instance both saw the rows and both
  decremented. The floored decrements and the next reconcile pass
  are what has always made that safe. Moving it out widens the
  window by one round trip and changes nothing else; in production
  the instance's attribute lock serialises the callers.

None of the four can over-admit past a guard that was actually
evaluated. That is the trade, and it is a good one: the alternative
is a primitive that 500s under concurrency on every current MariaDB.

#### Results

Same harness, same disposable container as step 6: MariaDB
`11.8.8-MariaDB-ubu2404` (`mariadb:11`), `utf8mb4_bin`,
`--max-connections=500`, `innodb_lock_wait_timeout=50`,
REPEATABLE-READ, DSN
`mariadb+mysqldb://root:sfroot@127.0.0.1:33061/sf`.

**`innodb_snapshot_isolation` ON (the server default), shipped
`_TRANSACTION_MAX_ATTEMPTS = 4`.** The full live suite — all four
`test_mariadb_*_live` modules, 58 tests, `stestr run --serial` —
**passed twice back to back**, including
`PlacementAdmissionConcurrencyLiveTestCase`. Both soak runs reported
reconciler drift zero on every counter.

| Scenario | Run 1 | Run 2 |
|---|---|---|
| Race for one slot (160 calls, 20 x 8) | median 15.6 ms, p99 22.3 ms, max 22.5 ms | median 17.1 ms, p99 23.0 ms, max 23.9 ms |
| Burst (50 concurrent; 5 admitted / 45 denied both runs) | median 135.6 ms, p99 167.4 ms | median 98.1 ms, p99 129.4 ms |
| Randomised soak (360 ops) | median 10.9 ms, p99 20.4 ms, max 24.9 ms; 126 admits / 92 moves / 121 releases / 21 denials | median 10.3 ms, p99 24.3 ms, max 28.1 ms; 129 admits / 91 moves / 123 releases / 17 denials |

Run 1's burst is the cold figure (first run against a freshly
created schema); run 2 is the steady-state one and lands exactly on
the OFF-regime baseline.

**Retry instrumentation (diagnosis only, not committed).** The same
counter step 6 used, over the whole concurrency class under ON:

| | probes inside (pre-fix) | probes outside (6a) |
|---|---|---|
| 1020s observed | 673 | **0** |
| 1213s observed | 0 | 1 |
| Attempts histogram | `{1: 221, 2: 227, 3: 37, 4: 103}` | `{1: 587, 2: 1}` |
| Outcome | 3 of 5 scenarios fail | all 5 pass |

The pre-fix column is not step 6's recorded run: it is a *control*
executed the same day against the same container, because the
instrumentation script initially imported the stale pre-fix copy of
`shakenfist` installed in `.tox/py3/site-packages` instead of the
worktree. The accident is worth recording — it reproduced the
blocker exactly, on the same server, minutes apart from the passing
run, which is about as clean an A/B as this could have got.

**`innodb_snapshot_isolation` OFF, concurrency class only.** Still
green — the fix does not regress the regime the primitive already
worked in. Race median 16.2 ms / p99 22.0 ms; burst median 102.7 ms
/ p99 134.0 ms (5 admitted, 45 denied); soak median 10.6 ms / p99
21.6 ms, 124 admits / 99 moves / 117 releases / 20 denials, drift
zero.

#### Timing comparison against step 6

| | 6: OFF (pre-fix) | 6: ON, budget 4 | 6: ON, budget 8 | **6a: ON** | **6a: OFF** |
|---|---|---|---|---|---|
| Race median | 15.7 ms | 27.1 ms | — | **15.6-17.1 ms** | **16.2 ms** |
| Race p99 | 22.0 ms | 35.7 ms | — | **22.3-23.0 ms** | **22.0 ms** |
| Burst median | 98.1 ms | fails | 313.8 ms | **98.1-135.6 ms** | **102.7 ms** |
| Burst p99 | 124.6 ms | fails | — | **129.4-167.4 ms** | **134.0 ms** |
| Soak median | 8.8 ms | fails | — | **10.3-10.9 ms** | **10.6 ms** |
| Soak p99 | 16.9 ms | fails | 179.8 ms | **20.4-24.3 ms** | **21.6 ms** |

The reading: under snapshot isolation the restructured primitive
performs like the pre-fix code did with snapshot isolation *off*,
which is the point. The 1.7x race-latency penalty step 6 measured
under ON is gone entirely — it was retry backoff, and there are now
no retries to back off from. Against the OFF baseline the soak's
median moves 8.8 -> 10.6 ms and its p99 16.9 -> 21.6 ms; that is the
one extra autocommit round trip per operation, and it is the honest
cost of the fix. The budget-8 alternative cost 313.8 ms on the burst
median and 179.8 ms on the soak p99 — 3.2x and 8.3x worse
respectively, on the instance-create hot path.

#### Tests

* `SnapshotIsolationInvariantTestCase` in
  `test_mariadb_capacity_admission.py` is the structural regression
  test. The mocked engine now hands `engine.begin()` and
  `engine.connect()` *separate* connections over one router, so a
  test can assert which statements ran where — the previous mock
  routed both to the same connection and could not have caught this
  bug in either direction. Ten new cases: the transaction opens with
  an `UPDATE` on the unclaimed, claimed, move and fully-fail-open
  paths and on release; the probes and the release reference lookup
  ran in autocommit; the post-admit counter read is allowed because
  it follows our writes; a double release opens no transaction; and
  a probe that cannot reach the database is a failed RPC (`success`
  False) rather than an admission or a denial.
* No existing unit test encoded the old read-inside-transaction
  order, so none needed rewriting; the two error-path tests that
  mocked only `engine.begin()` were extended to wire the probe
  connection too, so they now fail for the reason they claim to.
* 85 unit tests in the module (was 75) and the whole 2,938-test
  suite pass; flake8 and all 34 mypy invocations are clean.

**CI still cannot catch a regression of this.** The live suites run
on a `debian-12` runner with MariaDB 10.11, where
`innodb_snapshot_isolation` does not exist. The structural unit tests
above are the CI-visible guard; the behavioural one needs a server
with the variable ON, which for now means running the harness by hand
as this step did. Worth raising in step 8 review as a candidate for a
second live-suite job on a newer MariaDB.

### Step 7: documentation (2026-08-14)

Every stale location named in the step 4/5 notes was rewritten, plus
two more the survey missed: `ARCHITECTURE.md`'s cluster-daemon material
("nothing consumes them for admission... that arrives with phase 3's
guarded-UPDATE path") and its "Instance Scheduling" section
("observable-but-inert... but the scheduler does not yet consult
them"), and `docs/operator_guide/scheduler.md`'s "Expected demand"
section, which still said the demand feedforward term "does not affect
placement" — false since D13's demand clause now rides in the guard's
`WHERE`.

**Files changed:**

* `docs/operator_guide/database.md` — the `node_attributes` table row
  and the capacity-tables section rewritten: counters are consumed for
  admission, not just observed; the two RPCs, the `enforce` flag and
  its ground-truth writers, fail-open, and the reconciler as drift
  healer, all at the operator level; a new paragraph on
  `innodb_snapshot_isolation` compatibility.
* `docs/operator_guide/scheduler.md` — stages 1-5 reframed as
  pre-filters rather than admission; the CPU-overcommit section's
  stopgap description replaced with the measurement-only pre-filter it
  reverted to; a new "Admission is a guarded capacity claim" section
  carrying the walk/507/`enforce`/`cpu_committed_row_present` detail;
  "Expected demand" corrected to say the term does affect placement
  since this phase; the `dropped`-map and admin-resources-API
  paragraphs corrected to drop the retired `committed_cpus` field.
* `ARCHITECTURE.md` — the `object_references` section's dual-write
  paragraph rewritten to record the removal and describe the two RPCs;
  the cluster-daemon reconciler paragraph and the "Instance
  Scheduling" section both corrected from "nothing consumes them yet"
  to describe the guarded claim, with pointers to the operator docs
  rather than duplicating them.
* `CLAUDE.md` — the "Scheduler capacity" bullet's closing sentence
  replaced (consumption landed, stopgap deleted); the bullet's
  "maintained solely by the reconciler" claim corrected, since the
  admission RPCs now also write these counters incrementally between
  reconciler passes. The "Instance placement" bullet was already
  correct from steps 1/4 and needed no change.
* `AGENTS.md` — checked (grep for `dual-write`, `committed_vcpus`,
  `add_instance`/`remove_instance`, `observable-but-inert`, `Nothing
  consumes`); already fully correct from steps 5 and 6a, no change
  needed.
* `docs/plans/PLAN-scheduler-reservations.md` — phase table row 3
  status only; the phase 2 and phase 3 scope stubs are untouched, per
  brief.
* `docs/plans/index.md` — phase 3 row only (status and description);
  no other row touched.
* This file — step 7 marked Complete.

**Cross-check pass** (the success criterion: no fact about capacity
accounting stated differently in `CLAUDE.md`, `docs/operator_guide/database.md`
and the master plan):

* *Counters consumed for admission as of phase 3, not merely
  observed.* CLAUDE.md's "Scheduler capacity" bullet, database.md's
  capacity-tables section, and the master plan's phase-3 row all say
  this now.
* *The reconciler recomputes counters wholesale every five minutes;
  the admission RPCs additionally draw them down/release them
  incrementally between passes.* Stated identically in CLAUDE.md and
  database.md; the master plan's phase-3 stub already described the
  RPC without claiming the reconciler as sole writer, so no
  contradiction there.
* *`namespace_claims` stays empty until phase 4; the claim branch is
  unit-tested but dormant in production.* Unchanged and consistent in
  CLAUDE.md, database.md and the master plan (phase 4 row still "Not
  started").
* *`enforce=False` ground-truth writers (cleaner, startup
  reconciliation) update counters without refusing, and a push over
  the limit is logged loudly.* Stated the same way in database.md and
  scheduler.md; CLAUDE.md doesn't restate the enforce mechanics
  (correctly deferring to the referenced docs rather than duplicating
  them).
* *A missing capacity row (node or cluster) fails open, and the
  reconciler creates it on its next pass.* Stated identically in
  database.md and (for the node case) AGENTS.md's existing "Scheduler
  and node capacity metrics" section; not restated in CLAUDE.md, which
  is a summary-level file by design.
* *The issue-3498 stopgap (`_committed_vcpus()`) was deleted by this
  phase.* Stated identically in CLAUDE.md, the master plan's phase-3
  row and description, and `index.md`'s phase-3 row; `git grep
  _committed_vcpus` confirms it is gone from all non-plan code.
* *The legacy `node_attributes.instances` column, its dual-write and
  the read-side union were removed by this phase (not "for one
  transition release").* Stated identically in database.md's
  `node_attributes` row, ARCHITECTURE.md and CLAUDE.md's "Instance
  placement" bullet; confirmed against `node.py`, which has no
  `add_instance()`/`remove_instance()` and no union in `Node.instances`.
* *Phase status.* The master plan's phase table, `index.md`'s phase-3
  row, and this file's own Execution table all now read "Implemented"
  (this file's steps 1-7 as "Complete", the master plan and index rows
  qualified "awaiting operator review and sfcbr soak, 2026-08-14") --
  none of the three says "Planned" or "Not started" for phase 3 any
  more.

No stale claim survived the sweep beyond the two named above
(`ARCHITECTURE.md`'s two locations and scheduler.md's "Expected
demand" section); `pre-commit run --files <changed files>` (anchor-link
check) passes on all seven changed files.

### Step 8: management-session code review (2026-08-14)

**Shape.** Two passes over the branch. A mechanical checklist pass
(greps for the success criteria: `_committed_vcpus`,
`_dual_write_legacy_instances`, `record_relationship.*INSTANCE_LOCATION`
callers, direct `placement` attribute writers, `namespace_claims`
writers, phase 4/5/6 material in the diff), and an independent
adversarial reviewer given the full diff with no prior context and asked
to find what the checklist would not. Eleven findings; adjudicated as one
gating fix, six taken considers, two recorded for future work, and two
which turned out to be already handled.

**Reviewer's verdict:** the primitive is correct and well tested, the
phase's own validation caught the one serious concurrency bug before it
shipped, and the remaining findings are a single capacity-accounting
correctness bug plus polish -- nothing which reopens a P-decision or the
transaction design.

#### The gating fix: named-node release must be reference-gated

`_probe_release_rows()` took `nodes = [named_node]` without consulting
the `INSTANCE_LOCATION` rows, so the "nothing held, no-op" guard could
only ever fire for the no-node call form. Concretely:
`Instance._delete_globally()` names the node from the `placement`
attribute, which is never cleared (P8); the delete path's only
re-entrancy guard is on state `deleted`; an instance which ends in
`error` therefore reaches the release on every repeat delete. Each
repeat decremented the node and namespace counters again, and the
floors could not catch it because other instances' usage keeps the
counters well above the amount being released. The capacity was handed
out twice until the next reconcile pass.

The fix makes the reference rows the sole authority for "is this
instance still charged" in both call forms. Since phase 3 the placement
attribute and the reference rows are written by one transaction, so
that authority is exact. `node_uuid` is now a *filter* over the located
rows: named and located releases that row, named and not located (or no
rows at all) releases nothing and returns `released=False` with no
transaction opened. `_delete_globally()` and `hard_delete()` keep their
call shapes; the startup-tasks release callers name nodes that came
from located references, so the filter admits them unchanged.

Tests: the live `test_release_of_a_named_node_needs_no_references` was
inverted and renamed `test_release_of_a_named_node_is_reference_gated`
(the no-op is now the asserted behaviour), joined by live tests for a
release naming a node which does not hold the instance and for the
repeated-named-release shape; `mock_mariadb.py`'s release double was
given the same semantics; and unit tests were added at both levels --
`test_mariadb_capacity_admission.py` for the filter's four cases and
`test_instance.py`'s
`test_repeated_delete_of_an_errored_instance_releases_once` for the
review's exact scenario. That last one was verified to fail against the
pre-fix mock double.

#### Considers taken

* **Placement dict mutated in place.** `place_instance()` mutated the
  dict `_db_get_attribute()` returned, which an enclosing
  `attribute_memo()` block may be caching, and invalidated the memo only
  on success -- so a denial left the memo holding the refused node with a
  bumped `placement_attempts`. Now deep-copied before mutation; a denial
  changes nothing observable. `test_placement_is_visible_inside_an_enclosing_memo`
  passed even with the invalidation deleted, so it was reworked to read
  through a second `Instance` object and assert the attribute-fetch
  count; it was verified to fail with the invalidation removed. A new
  `test_a_denial_leaves_no_trace_in_an_enclosing_memo` covers the
  mutation itself.
* **Doc contradictions.** `docs/operator_guide/database.md`'s
  "maintained solely by a reconciler" and `ARCHITECTURE.md`'s "the
  reconciler is the sole writer of the three capacity tables" were both
  falsified by this branch. Aligned with CLAUDE.md's wording: recomputed
  wholesale by the reconciler, drawn down and released incrementally by
  the RPCs, reconciler as drift corrector.
* **Stale docstring** in `daemons/network/maintain.py` still explained
  why the vxid query does not read the legacy union; rewritten to record
  that the reference rows have been the sole record since phase 3.
* **Discarded release replies** in `startup_tasks.py`. Both calls now go
  through a small `_release_placement()` helper which logs a warning on
  `success=False`, matching `_reconcile_placement()`'s failure logging.
* **The over-limit event preceded its write.** `_admit_placement()`
  emitted `placement recorded despite exceeding capacity guard` before
  the unguarded write it describes; if that write failed the audit trail
  lied. Moved to after a successful unguarded call, keeping the denial
  reply for the event detail.
* **Unguarded-admission counter.** P7 is unbounded in time, not just
  mid-upgrade -- a node the reconciler never sizes admits unguarded
  forever -- so the step 9 soak needs to tell "guard working" from "guard
  not running". `daemons/database/main.py` now increments a dedicated
  `database_admit_instance_placement_unguarded_total` counter whenever a
  reply comes back `unguarded`.
* **CLAUDE.md name-dropped `_committed_vcpus()`**, breaking the plan's
  own `git grep _committed_vcpus` success criterion for non-plan files.
  Reworded to "the issue-3498 Python stopgap in the scheduler".
* **The startup reconciliation does not emit P5's over-limit event.** It
  calls `admit_instance_placement(enforce=False)` directly rather than
  going through `Instance._admit_placement()`, so it never runs the
  probe the event is derived from. This is a deliberate asymmetry, not a
  bug -- the cleaner probes and events, the startup repair records
  without probing -- and is now documented as such in
  `docs/operator_guide/database.md` beside the `enforce=False`
  paragraph.

#### Recorded only

Two findings were judged real but not worth acting on in this phase, and
are in Future work above: `get_scheduler_node_capacity()` cannot
distinguish a read failure from an empty table (degrading the cluster CI
assertion to a skip), and `mock_mariadb.py` models no cluster, claim or
demand denial stage (bounding what caller-side unit tests can assert;
this becomes necessary with phase 4's claims API).

#### Verification

The full `test_mariadb_capacity_admission_live` suite (37 tests, up from
34) was run serially twice back to back against a disposable
`mariadb:11` container -- 11.8.8, `innodb_snapshot_isolation` ON,
REPEATABLE-READ -- both times all green, including the concurrency class.
The two capacity live suites together (52 tests) also pass. Unit:
`test_instance`, `test_mariadb_capacity_admission` and
`test_queues_startup_restore` -- 156 tests, all passing. `tox -eflake8`
on the changed files and the full `tox -emypy` suite are both clean.

**One review claim was inaccurate in detail.** The finding-1 write-up
said `_probe_release_rows()` "takes `nodes = [named_node]` without
consulting `INSTANCE_LOCATION` rows, so the *nothing held → no-op* guard
can never fire" -- correct -- but the fix note added that `hard_delete()`
would need no change *because* its call names no node. It does name no
node, so it was already reference-gated and genuinely needed no change;
the checklist's "double release is harmless" item was true before this
fix for the `hard_delete()` path specifically and false only for the
named-node path. The distinction matters for reading the existing
`test_hard_delete_release_behind_delete_globally_is_a_noop` test, which
was passing for the right reason all along.

## Administration and logistics

### Success criteria

* Two concurrent admissions for one remaining slot admit exactly
  one (step 6's race test, kept as a repeatable harness).
* The whole live suite passes with `innodb_snapshot_isolation` ON
  — the default on every current MariaDB — at the shipped retry
  budget, with no ER_CHECKREAD exhaustion (step 6a).
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

- [x] Guarded UPDATEs follow canonical order everywhere,
      including the release and move paths.
- [x] The concurrent-scheduling test exercises real MariaDB, not
      mocks (master plan checklist item).
- [x] `hard_delete()` accounts for capacity release (master plan
      checklist item); double release is harmless.
- [x] `enforce=False` paths update counters and emit the
      over-limit event.
- [x] No caller outside `mariadb.py` writes `INSTANCE_LOCATION`
      rows or the `placement` attribute directly.
- [x] The claim branch is unreachable in production until phase
      4 (no API can create a `namespace_claims` row) but fully
      unit-tested.
- [x] Diff contains no phase 4/5/6 material.
- [x] mypy clean; single quotes; 120-char lines.

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
- `mariadb.get_scheduler_node_capacity()` cannot distinguish a read
  failure from an empty table: `_direct_get_scheduler_node_capacity()`
  logs and returns `[]` on `OperationalError`, and the gRPC wrapper does
  the same, so `summarize_resources()` publishes
  `cpu_committed_row_present=False` for every node and the cluster CI
  assertion in
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_nodes.py`
  degrades from an assertion to a skip. Harmless for the summary (a node
  with no row is charged nothing and guarded by nothing either way), but
  it means a persistently unreadable capacity table would silently
  disable that CI check rather than failing it. The CPU pre-filter now
  reads the same helper, where an empty return degrades it to
  measurement-only -- the behaviour that caused the single-candidate
  lockout below, though there the guard still refuses correctly and
  only the pre-filter's pruning is lost. Worth a distinguishable error
  return once something depends on the read for more than display.
- `shakenfist/tests/mock_mariadb.py`'s
  `_mariadb_admit_instance_placement()` models only the node stage: it
  has no cluster singleton, no `namespace_claims` row and no
  expected-demand term, so a caller-side unit test can only produce a
  `failing_stage` of `node`. That bounds what
  `shakenfist/tests/test_instance.py` and
  `shakenfist/tests/test_external_api.py` can assert about denial
  handling to one of the four denial stages. Acceptable while the claim
  branch is dormant; **phase 4 makes it necessary**, since the claims API
  is the first thing that can produce a `claim`-stage denial in
  production and its callers will want unit coverage of that path.
  *Mostly discharged.* The demand term landed with the D13 guard later
  in this phase, and phase 4's step 3 added the claim stage: a
  `set_namespace_claim()` helper, the advisory over-limit reply fields,
  a symmetric release decrement, and a `claim`-stage denial under
  `mariadb.CLAIM_ENFORCEMENT_HARD`. The cluster singleton is still not
  modelled, deliberately: an unclaimed namespace's charge against it has
  no caller-observable effect, so there is nothing for a caller-side
  test to assert about it.
- The pick-then-claim walk (the `place_walk` closure, the P9
  demand-only re-walk and the exhaustion branch) exists verbatim in
  `shakenfist/external_api/instance.py` and
  `shakenfist/operations/node_inst_netdesc_op.py`, differing only in
  event sink and terminal action (PR review, item 5). Phase 5
  migrates a third `Scheduler()` call site; extract a shared helper
  (e.g. `scheduler.claim_first_available()`) as part of that
  migration, when the third caller makes the right parameterisation
  visible, rather than guessing it now from two.
- The demand-waived second walk re-tries every candidate, including
  those refused on `cpus`/`memory_mb`/`disk_gb`, which cannot admit on
  the second pass either (PR review round 5, item 2). Worst case is
  2N admission RPCs for one create. Not fixed here deliberately: the
  narrowing has to be made identically in both copies of the walk, and
  the entry above commits to extracting those into one helper during
  phase 5 -- doing it now doubles the divergence risk the extraction
  exists to remove, to save RPCs on a path that only runs when a create
  was about to fail outright. Fix it in the extracted helper, where
  `denials` already carries `demand_only` per candidate, and add the
  test the reviewer asks for (the second walk skips a
  real-dimension refusal) against that single implementation.
- The ER_CHECKREAD invariant has structural but not behavioural CI
  coverage (PR review, item 7): the live concurrency suite only
  bites against a server with `innodb_snapshot_isolation` ON, and the
  `schema_enum_widening` job's debian-12 runner ships MariaDB 10.11,
  which predates the variable. A second live-suite job against a
  `mariadb:11` container (or a debian-13 runner) would cover it with
  no new test code -- the harness already reports the server regime
  it ran under. Needs runner/DSN infrastructure, not code. Tracked
  as issue #3759 at the second review round's request.

### Bugs fixed during this work

* **ER_CHECKREAD retry exhaustion under `innodb_snapshot_isolation`**
  (found in step 6, fixed in step 6a). Both placement transactions
  opened with plain `SELECT`s, which established their read view
  early and made every guarded `UPDATE` against a contended row
  abort with 1020 instead of blocking. On MariaDB 11.6.2+ this
  turned concurrent instance creates into HTTP 500s; 46 of a 50-way
  burst exhausted the retry budget. Fixed by moving the probes onto
  an autocommit connection so a guarded `UPDATE` is each
  transaction's first statement. Never shipped — found by the
  phase's own validation step, before the PR.

* **Demand-clause lockout of small clusters** (found by the first
  smoke CI run of PR #3754, fixed as decision P9). The D13 demand
  clause, applied as a hard admission guard, permanently locked the
  single smoke node out under create churn: `expected_demand`
  reached 8–12 against a bound of 6 while measured load was 0.45
  and every real dimension was empty, failing 13 tests with 507s.
  Fixed structurally: walkers re-walk once with the demand clause
  waived when the enforced walk admits nowhere and at least one
  denial was demand-only, so demand spreads load but can never fail
  a create the cluster has real capacity for. Never shipped — caught
  by PR CI before merge.

* **Single-candidate lockout: a measurement-only pre-filter behind a
  ledger-denominated guard** (found by the first two merge-queue runs
  of PR #3754, fixed 2026-08-15). Five creates failed with `507 no
  node had capacity for this instance, 1 candidates refused it` in the
  `tier` and `cluster` topologies. Two changes combined to cause it.
  First, this phase replaced the issue-3498 `_committed_vcpus()`
  placement walk with a purely measurement-denominated
  `_has_sufficient_cpu()`, on the reasoning that the guard was now the
  real admission; a node whose ledger was full therefore still measured
  as idle (its instances had not booted) and stayed in the candidate
  list. Second, the pre-existing load-bucket stage *filtered* the
  candidate list to the lowest band rather than ordering it, so the
  full node became the only candidate. The guard refused it (`cpus
  limit 3.0, used 3.0`), the walk had nothing to fall through to, and
  a cluster with two idle nodes returned a 507. Fixed on both sides:
  the CPU pre-filter charges `max(measured, used_cpus)` again, read
  from the counters in one query rather than rebuilt per placed
  instance; and the bucketing now orders the whole candidate list
  best-band-first instead of discarding the rest. Never shipped —
  caught by merge CI before merge. The PR-level gate did not see it
  because PR CI runs only the single-node smoke job; the multi-node
  topologies run only in the merge queue.

### Back brief

Before executing any step of this plan, back brief the operator
on this plan and its alignment with the master plan — in
particular P1 (legacy column removal now) and P5 (the `enforce`
flag's narrowing of D3), which are the two decisions most likely
to draw argument.
