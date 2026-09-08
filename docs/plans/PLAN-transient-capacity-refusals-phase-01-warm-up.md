# Phase 1: close the warm-up window

Parent plan:
[PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md).

**Planning effort:** high, as the master plan specifies. The change
itself is a dozen lines in one file. What keeps it above mechanical
is that the check has to agree with a predicate that lives in
another process's SQL, and a check which disagrees with it does not
fail loudly -- it silently demands a reconcile pass forever, turning
a five-minute job into a one-minute one for the life of the cluster.

This phase opens the plan's decision sequence at **D1**. There is one
namespace across the whole plan, because later phases cite these by
number.

## Context

The master plan's Situation section establishes that the warm-up
window is *not* what fails CI runs -- every post-#4106
`sufficient_idle_cpu` refusal read from journals was a
`force_placement` create onto a node genuinely at its ledger limit.
The window is real, it accounts for the whole of the sub-three-minute
exposure, and it produces the `0 / 6 / 3` and `6 / 0 / 3` residue
rows in that section's table, but fixing it will not by itself make
a red run green.

It is first anyway, for three reasons. It is the smallest server-side
change in the plan. It is the one that finishes #4087, which is still
open. And phases 2 and 5 measure how long the suite waits for
capacity: if ten to seventeen placements per run are still being
admitted against nothing at all while that measurement is taken, the
numbers phase 5 reads are polluted by a known defect.

Concretely: `scheduler_node_capacity` has no rows until the
reconciler's first pass. A node with no row is admitted against
nothing (P7), so every early placement fails open, and the first real
pass then writes accumulated ground truth onto a row whose limit it
already exceeds. PR #4106 anchored the reconcile to election and
added a one-shot `_force_capacity_reconcile_if_unguarded()`
(`shakenfist/daemons/cluster/main.py:741`, called at `:879`). In all
six post-#4106 runs the one-shot fired 130-150 s *before* the test
step and the pass it forced logged `nodes=0 nodes_added=0`: no
hypervisor had published metrics yet. The table then stayed empty
until the five-minute cadence created rows at +155..+181 s.

The one-shot is not wrong. It is a property of *election*, and the
condition it tests is a property of *time*.

## Scope

**In scope:**

* Making the unguarded-table check something the elected loop repeats
  rather than something election does once.
* Making that check agree with the predicate the reconciler actually
  applies, so it cannot demand a pass which will never satisfy it.
* Unit tests extending the existing `ForcedCapacityReconcileTestCase`.
* One functional assertion in the CI suite, replacing a `skipTest`
  which this change makes unreachable.
* A non-gating time-to-first-capacity-row figure in the headroom
  report, so the improvement is visible in every run's log.
* Commenting on #4087 with the finding, and closing it.
* Documentation: `docs/operator_guide/scheduler.md` and
  `docs/developer_guide/subsystem_internals.md` both describe the
  unguarded state and must describe how long it now lasts.

**Out of scope:**

* Anything that changes admission itself. The pre-filters, the demand
  guard, `max(measured, committed)` and the guarded UPDATE all belong
  to scheduler-reservations (its D11, phase 5). This phase changes
  *when a capacity row exists*, never what admission does with one.
* The 60 s metrics publication lag. That is phase 3, and it is the
  dominant term in the remaining window -- see D1.
* Any client or suite retry behaviour. Phases 2 and 4.
* Reshaping the CI topologies. `PLAN-ci-cloud-sizing.md` phase 4.
* Making the refusal itself more informative. Phase 4.

## What the survey found

Seven findings. Four contradict claims this phase inherited. Finding
2 was corrected in the master plan before it merged (commit
`7d4cfbda0`); findings 1 and 6 are corrected at source in this
planning commit. Re-check all three rather than redoing them.
Findings 3, 4, 5 and 7 are new information rather than corrections,
and live here.

1. **The elected loop's maintenance body runs every 60 s, not every
   iteration.** The loop polls every `ELECTED_LOOP_POLL_SECONDS = 5`
   (`shakenfist/daemons/cluster/main.py:63`) but the maintenance
   block sits behind `if now - last_loop_run >= 60` (`:913`) and
   `_run_due_scheduled_jobs()` is inside it (`:918`). Marking the
   capacity reconcile due does not run it before the next 60 s tick.
   The master plan cites this as `:912`, off by one; corrected here.
   This is the finding D1 turns on.

2. **`cluster_stable()` reads no metric freshness.** It compares
   object versions across nodes (`shakenfist/daemons/daemon.py:377`)
   and catches a just-restarted cluster only incidentally, because
   no node has recorded a version yet and `minimum` is `inf`. The
   master plan's phase 1 section said the gate protects against "a
   pass with no fresh metrics"; that was corrected before merge.

3. **The unit tests already exist.** The master plan reads as though
   this phase writes the first one. `ForcedCapacityReconcileTestCase`
   (`shakenfist/tests/test_daemon_cluster_schedule_anchoring.py:244`)
   already pins five cases: an empty table forces the pass due, a
   populated table leaves the cadence alone, a degraded read is not
   an empty table, a failed read does not escape the election, and no
   registered job reads nothing. Extend that class; do not create a
   new file, and do not weaken any of the five.

4. **No new RPC is needed.** `mariadb.get_all_node_metrics()`
   (`shakenfist/mariadb.py:21075`) already exists and is already
   gRPC-backed, returning one dict per node. So this stays a
   one-file-plus-tests change and needs no proto regeneration. Note
   the shape: each dict is `{node_uuid, fqdn, timestamp, metrics}`,
   and `is_hypervisor` is *inside* the `metrics` JSON, not a
   top-level key -- the typed column exists in the table but
   `_direct_get_all_node_metrics()` (`:20909`) does not project it
   into the reply.

5. **The reconciler's predicate is richer than "hypervisor with
   fresh metrics", and this is the phase's main hazard.**
   `_direct_reconcile_scheduler_capacity()` (`:25022`) gives a node a
   capacity row only if it is `is_hypervisor IS TRUE`, has
   `timestamp > now - RECONCILE_METRICS_MAX_AGE_SECONDS`, is in
   `active_nodes` (an `object_states` row in `NODE_ACTIVE_STATES`)
   *and* is in `known_nodes` (a row in the `nodes` table). The code
   documents why the last one exists: a `node_metrics` row that
   outlived its node "reads as a fresh hypervisor" and would
   otherwise become a permanent phantom. A check testing only the
   first two conditions would see that phantom as an unguarded
   hypervisor on every pass, forever, and force the five-minute
   reconcile to run every 60 s for the life of the cluster. D2 and D3
   exist for this.

6. **#4087 is open, not closed.** It carries `bug` and
   `automated-fix-attempted`. The master plan said "(closed by
   #4106, incompletely) ... Phase 1 reopens or comments"; there is
   nothing to reopen. Corrected at source in this planning commit, in
   both the phase 1 section and the bugs list. This phase comments
   and closes.

7. **The freshness window is 900 s, not 60 s.**
   `RECONCILE_METRICS_MAX_AGE_SECONDS = 900` (`:636`). "Has published
   metrics" in this check must mean the reconciler's 900 s window,
   not the resources daemon's 60 s publication cadence. A tighter
   window in the check would make it and the pass disagree in the
   direction that matters least (missing a node), but a looser one
   would make them disagree in the direction that thrashes.

## Decisions

### D1 -- The check runs inside the 60 s maintenance gate

Not on the 5 s poll. The window closes to about a minute rather than
to seconds.

This is the decision a reviewer is most likely to argue with, so the
reasoning is set out in full. Three things push this way.

The check cadence is not the dominant term. The measured window is
135-210 s, and almost all of it is the wait for a hypervisor to
publish metrics at all -- the forced pass at election found
`nodes=0` because there was nothing to size, 130-150 s before the
test step. Once metrics exist, a 60 s worst case adds 60 s to a term
that was already 135-210 s. Going from 60 s to 5 s buys at most 55 s
on top of an improvement of roughly 100-150 s, and phase 3 attacks
the dominant term directly.

The 5 s option is not free. The check costs two reads
(`get_scheduler_node_capacity()` and `get_all_node_metrics()`). Inside
the 60 s gate that is 0.033/s, riding a maintenance pass that already
reads far more. On the 5 s poll it is 0.4/s of fixed-rate polling,
which needs a `cluster_base_qps` entry in
`shakenfist/data/database_load_budget.yaml` or
`test_no_unbudgeted_fixed_rate_database_polling` fails -- and it
would be *fixed-rate*, so it raises the idle-cluster floor the
database-load-reduction plan is trying to lower, permanently, to buy
55 s once per cluster lifetime.

Marking the job due from inside the gate runs it in the same pass.
`_run_due_scheduled_jobs()` is called at `:918`, immediately after
where the check goes, so setting `next_run` and then calling
`schedule.run_pending()` executes the reconcile without waiting for
another tick.

If a later phase shows the residual minute matters, the check is one
call and can move. Recorded as future work in the master plan rather
than pre-empted here.

### D2 -- The check mirrors the reconciler's predicate, including the active-node intersection

A node counts as an unguarded hypervisor only if it has metrics with
`is_hypervisor` true, a `timestamp` inside
`RECONCILE_METRICS_MAX_AGE_SECONDS`, is in the active node set, and
has no `scheduler_node_capacity` row. Import the constant from
`mariadb` rather than restating 900; a check with its own copy of
that number will drift from the pass it is trying to trigger.

The active-node intersection is the one that cannot be skipped
(finding 5). Use `Nodes([], prefilter='active')`, which is what both
the scheduler and the reconciler's `active_nodes` set resolve to, so
the check and the pass agree by construction rather than by
coincidence.

The `known_nodes` half of the reconciler's predicate is not mirrored:
a node with an `object_states` row in an active state but no row in
the `nodes` table is a stateless zombie, and the active-node
prefilter already excludes anything without a resolvable object. If
the implementer finds that assumption does not hold, treat it as a
finding and stop -- do not add a second-guess intersection without
saying why in the commit message.

### D3 -- Do not force twice for the same unguarded set

Remember the set of node uuids the last forced pass was issued for.
Force only when the current unguarded set is non-empty and differs
from that remembered set; clear the memory when the set becomes
empty.

This bounds the failure mode D2 is guarding against rather than
merely making it unlikely. If some node genuinely qualifies under
this check but the reconciler declines to size it anyway -- a P7
decline this phase has not anticipated -- the pass is forced once,
observed not to have helped, and then left alone until the set
changes. The ordinary five-minute cadence still runs, so nothing is
lost; the cluster just does not spend the rest of its life
reconciling every minute.

The remembered set is process-local state on the monitor object,
reset on election like the rest of the elected loop's state. It is
not persisted: a daemon restart re-forcing once is correct, not a
bug.

### D4 -- Delete the election-time call site

Generalise the existing helper and call it from the maintenance pass;
remove the call at `:879`.

Keeping both would leave two paths that can drift. Nothing is lost by
removing the election-time call: `last_loop_run` is initialised to 0
(`:813`) outside the outer election loop, so the first maintenance
pass after any election finds `now - last_loop_run >= 60` true and
runs immediately. The election-time call is also *upstream* of the
`cluster_stable()` gate, so on an unstable cluster it can force a job
due that will not run -- which is harmless but is exactly the kind of
"why is this due?" confusion the check is meant to remove.

The five existing unit tests call the helper directly and keep
working unchanged. That is deliberate: they pin the degraded-read and
empty-table semantics, which this phase must not alter.

### D5 -- One warning per distinct unguarded set, plus a counter

The existing helper logs `No scheduler capacity rows exist, so every
placement is being admitted unguarded` at warning. Keep that
wording for the all-empty case, and add a second warning naming the
node uuids for the partial case, emitted under the same
once-per-distinct-set rule as D3 so a stuck condition does not
produce a line a minute forever.

Export a counter for forced passes alongside the existing
`SCHEDULER_CAPACITY_*` metrics in
`shakenfist/daemons/cluster/scheduled_tasks.py`. Without it,
"how often is this firing in production?" is answerable only by
grepping logs, and a thrash introduced by a later change would be
invisible until someone looked.

### D6 -- The functional assertion replaces the skip, and the report measures

`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_nodes.py:136`
currently skips when the node under test has no capacity row. After
this change that condition is the bug, so a skip would hide exactly
the regression this phase exists to prevent. Convert it to an
assertion.

It must not race the cluster coming up. The suite already has
`tools/ci_wait_schedulable.py` gating the test step, so by the time
any functional test runs, a hypervisor exists and has been schedulable
-- but the implementer must confirm that gate runs before this test
in the workflow rather than assuming it, and if it does not, the
assertion waits with the suite's existing retry helper rather than
failing on first read.

Separately, `tools/ci_headroom_report.py` gains a time-to-first-
capacity-row figure computed from the series it already parses
(`cpu_committed_row_present` is in every sample). That is the
measurement of this phase's effect across the whole fleet, and per
ci-cloud-sizing D15 it is printed, never gating.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Generalise `_force_capacity_reconcile_if_unguarded()` in `shakenfist/daemons/cluster/main.py:741` so it forces the capacity reconcile when *any* active hypervisor with fresh metrics has no `scheduler_node_capacity` row, not only when the table is entirely empty, and call it from the elected loop's maintenance pass instead of from election. Read the whole docstring first: the degraded-versus-empty distinction it describes is load-bearing and must survive unchanged (`get_scheduler_node_capacity()` returns `(rows, degraded)`; `rows` is empty for both an empty table and an unreadable one, and only `degraded` says which -- see `shakenfist/mariadb.py:27215`). The unguarded set is: nodes with a `get_all_node_metrics()` entry whose `metrics['is_hypervisor']` is true -- note `is_hypervisor` is inside the `metrics` dict, not a top-level key of the returned record (`mariadb.py:20909`) -- whose `timestamp` is greater than `now - mariadb.RECONCILE_METRICS_MAX_AGE_SECONDS` (import the constant, do not restate 900), which appear in `Nodes([], prefilter='active')`, and which have no row in `rows`. Mirroring the reconciler's predicate is the whole point: `_direct_reconcile_scheduler_capacity()` (`mariadb.py:25022`, the refresh block from `:25115`) applies exactly these plus a `nodes`-table membership test, and a check that omits the active-node intersection will treat a `node_metrics` row that outlived its node as a permanently unguarded hypervisor and force the five-minute job every 60 s forever -- the code comment at `:25201` describes that phantom. Implement D3: keep the set of uuids the last force was issued for on the monitor, force only when the current set is non-empty and differs from it, clear it when the set empties. Implement D5: keep the existing all-empty warning text, add a second warning naming the uuids for the partial case under the same once-per-set rule, and add a forced-pass counter beside the `SCHEDULER_CAPACITY_*` metrics in `shakenfist/daemons/cluster/scheduled_tasks.py`. Per D4, call it from inside the `else:` branch at `main.py:911` before `_run_due_scheduled_jobs()` at `:918` so the forced job runs in the same pass, and delete the call at `:879`. Extend `ForcedCapacityReconcileTestCase` (`shakenfist/tests/test_daemon_cluster_schedule_anchoring.py:244`) rather than writing a new file, and do not weaken its five existing tests. Add: a hypervisor with fresh metrics and no row forces the pass; the same set on the next call does not force again; a changed set does force again; a stale-metrics hypervisor does not force; a non-hypervisor with no row does not force; a hypervisor with fresh metrics that is not in the active set does not force (this is the phantom -- assert it explicitly, and confirm that deleting the active-node intersection makes this one test fail); a degraded read still forces nothing. Single quotes, 120 columns, no trailing whitespace. |
| 1b | medium | sonnet | none | The CI-visible half. In `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_nodes.py`, convert the `skipTest` at `:136` into an assertion, per D6: after this phase a hypervisor without a capacity row is the defect, and skipping hides it. First confirm from `.github/workflows/` that `tools/ci_wait_schedulable.py` gates the functional-test step, so the assertion cannot race cluster start-up; if it does not, use the suite's existing retry helper (`shakenfist/deploy/shakenfist_ci/retries.py`) rather than asserting on the first read, and say which you found in the commit message. Leave the three assertions below it unchanged. Then add a time-to-first-capacity-row figure to `tools/ci_headroom_report.py`: the series already carries `cpu_committed_row_present` per node per sample, so report the offset from the first sample to the first sample in which every hypervisor in the roster has a row, and report it as absent rather than zero when it never happens. Print it; never gate on it (ci-cloud-sizing D15). Extend the tool's unit tests (`shakenfist/tests/test_ci_headroom_report.py`) with a series where the row never appears and one where it appears immediately. |
| 1c | medium | sonnet | none | Documentation. `docs/operator_guide/scheduler.md` describes the unguarded state at `:305` and `:768`, and `docs/developer_guide/subsystem_internals.md` at `:248`; all three now need to say how long a cluster spends in it and what ends it. State the bound honestly: about a minute after a hypervisor's metrics become visible to the elected node, not "immediately", and note that the wait for those metrics is the larger term and is phase 3's. Mention the forced-pass counter from 1a where the other `SCHEDULER_CAPACITY_*` metrics are documented. Do not touch `AGENTS.md` or `ARCHITECTURE.md`: no convention and no component boundary changes here. |
| 1d | low | haiku | none | Comment on #4087 and close it. The comment states what #4106 did fix (the missing-stamp case, and the MariaDB-outlived-its-nodes case), what it did not (the one-shot fires on the election path, before any hypervisor has published metrics, so on a fresh cluster it finds nothing and nothing re-checks -- observed in all six post-#4106 CI runs as a forced pass logging `nodes=0 nodes_added=0` 130-150 s before the test step), and what this phase changed. Do not reopen anything -- the issue is already open. Link the merged pull request. Wait for the operator's confirmation that 1a-1c have merged before commenting. |
| 1e | low | haiku | none | Set the phase 1 row to `Complete` in the master plan's Execution table and in `docs/plans/index.md`, update the index arithmetic to `1 of 6`, then run `python3 tools/check-plan-status.py`. Only after 1a-1d are reviewed and the operator has confirmed a real CI run shows the time-to-first-capacity-row figure from 1b. |

The survey corrections that would otherwise have been a step here
were made in the planning commit, per the note under *What the survey
found*.

## Risks and mitigations

* **The check disagrees with the reconciler and thrashes.** The
  headline risk, and the reason this phase is planned at high effort.
  A node that satisfies the check but which the reconciler declines
  to size makes the five-minute job run every 60 s for the life of
  the cluster, which is a permanent load regression that no test
  would catch. *Mitigation:* D2 mirrors the predicate and imports the
  reconciler's own freshness constant; D3 bounds the blast radius to
  one forced pass per distinct set even if the mirror is imperfect;
  1a's test list includes the phantom case explicitly and requires
  that removing the intersection makes it fail; D5's counter makes
  the condition visible in production. The management session checks
  the counter against a real run before 1e closes the phase.
* **Deleting the election-time call loses a case nobody enumerated.**
  D4's reasoning depends on `last_loop_run` being initialised outside
  the election loop, which is true today (`:813`) but is not
  obviously load-bearing to a future reader. *Mitigation:* 1a is
  briefed to leave a comment at the initialisation saying the
  capacity check depends on it. A reviewer who disagrees can keep
  both call sites at the cost of one extra read per election; say so
  in review rather than after merge.
* **The converted assertion makes CI flakier, not less flaky.** This
  plan exists because CI is unreliable; adding a hard assertion to a
  suite that already fails on capacity is a real way to make things
  worse. *Mitigation:* D6 requires the implementer to confirm the
  readiness gate runs first rather than assume it, with a retry
  fallback if it does not. If the assertion fires on a green run
  during review, revert it to a skip and treat that as a finding
  about this phase's fix, not about the test.
* **A minute is still too long and nobody notices.** *Mitigation:*
  1b's report figure is the measurement, printed on every run, so the
  question is answerable from any bundle rather than needing another
  journal dig like the one that produced this plan.
* **The phase is mistaken for a fix for #3772.** It is not, and the
  master plan says so. *Mitigation:* 1d's comment closes #4087 only;
  the commit messages say "Related to #3772", never "Fixes".

## Definition of done

Falsifiable, in order:

1. `_force_capacity_reconcile_if_unguarded()` forces the reconcile
   when an active hypervisor with fresh metrics has no capacity row,
   not only when the table is empty, and is called from the elected
   loop's maintenance pass rather than from election. The call at
   `main.py:879` is gone.
2. All five pre-existing tests in `ForcedCapacityReconcileTestCase`
   still pass unmodified.
3. A test asserts that a hypervisor with fresh metrics which is *not*
   in the active node set does not force a pass, and deleting the
   active-node intersection from the implementation makes that test
   fail. The second half is the check: run it and confirm, do not
   assert it from reading.
4. A test asserts that the same unguarded set on two consecutive
   calls forces exactly one pass, and that a changed set forces
   another.
5. No literal `900` appears in the new code; the freshness bound is
   `mariadb.RECONCILE_METRICS_MAX_AGE_SECONDS`.
6. `test_nodes.py` contains no `skipTest` for a missing capacity row,
   and the assertion that replaced it is either gated by
   `ci_wait_schedulable` running earlier in the workflow or wrapped
   in the suite's retry helper -- the commit message says which.
7. `tools/ci_headroom_report.py` prints a time-to-first-capacity-row
   figure, reports it as absent rather than zero when no sample has
   every hypervisor covered, and has unit tests for both.
8. A real CI run on `slim-tier` shows that figure under 60 s. The
   operator confirms this from a bundle; it cannot be checked from a
   worktree.
9. No statement in `docs/operator_guide/scheduler.md` or
   `docs/developer_guide/subsystem_internals.md` still implies the
   unguarded window lasts until the five-minute cadence, and neither
   claims it is closed immediately.
10. #4087 is closed with a comment recording what #4106 fixed and
    what it did not.
11. `python3 tools/check-plan-status.py` passes and
    `pre-commit run --all-files` passes.

## What later phases inherit

* A cluster whose capacity rows exist within about a minute of a
  hypervisor publishing metrics, so phase 5's measurement of how long
  the suite waits is not polluted by unguarded admissions.
* A printed per-run figure for how long the window actually was, so
  phase 3's effect on the dominant term is measurable against a
  before-and-after rather than argued.
* A forced-pass counter, which phase 3 should watch: publishing
  metrics on domain-set change will make hypervisors appear to the
  reconciler sooner, and this counter is where that shows up.
* The knowledge that the reconciler's qualifying predicate has four
  clauses and that mirroring three of them is a permanent load
  regression, recorded here so the next person to write a
  capacity-adjacent check does not rediscover it.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it: what you are about to change, which
decision you think is weakest, and what you expect to be true when
you are done. Do not begin until that is acknowledged.

There is one gate inside the phase. Step 1a's treatment of D2 and D3
is cheap to propose and expensive to redo, because getting the
predicate wrong is invisible until it is running in production.
Before writing the tests, state the exact predicate you intend to
implement -- clause by clause, against
`_direct_reconcile_scheduler_capacity()` -- and have it agreed. If
your reading of the reconciler differs from finding 5 in any
respect, stop and say so: the finding is what the rest of this plan
is built on.
