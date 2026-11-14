# Scheduler reservations phase 5 — caller consolidation and the hard ceiling

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
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

Consult `ARCHITECTURE.md`, `AGENTS.md` and `GOALS.md`. Key
references: `shakenfist/scheduler.py` (the `Scheduler` class and
`find_candidates()`), `shakenfist/instance.py`
(`place_instance()` and the advisory claim accounting),
`shakenfist/external_api/instance.py` and
`shakenfist/operations/node_inst_netdesc_op.py` (the two
placement-walk call sites), and `shakenfist/mariadb.py`
(`admit_instance_placement()` and `CLAIM_ENFORCEMENT_HARD`).

## Planning effort

**High.** This phase turns on correctness: a shared admission
helper that two callers must use identically, and a flip from
advisory to hard ceiling enforcement whose edge cases (ground-truth
writers, the demand re-walk, transient-vs-permanent refusals) are
easy to get subtly wrong. Review at **high** effort for the same
reason.

## Scope

**In scope:**

1. Extract the duplicated placement walk (the `place_walk` closure
   plus the demand-waived re-walk) that exists verbatim in
   `external_api/instance.py` and `operations/node_inst_netdesc_op.py`
   into one shared helper, and route both callers through it. The
   in-code comments in both files name this as phase 5's job and cite
   this plan.
2. Remove `admin.py`'s `Scheduler()` construction so the capacity
   view is a plain read of the published capacity surface, not the
   construction of a placement decider. Phase 7a deliberately left this
   for phase 5 (7a's H7).
3. **(Gated on the calibration re-run — see Decision D5.)** Flip
   `CLAIM_ENFORCEMENT_HARD` from advisory to hard: turn the advisory
   over-claim *event* into a *refusal* (HTTP 403 with the structured
   body D16 specifies — claim uuid, per-dimension limit, current
   drawdown, shortfall), and split claim denials out of
   `TRANSIENT_CAPACITY_STAGES` so a namespace-quota refusal is not
   advertised as retryable.
4. Correct the master plan's phase 5 stub and the D11/D16 framing at
   source (done in this planning commit — see *What the survey found*),
   and document the retained in-Python pre-filters.

**Out of scope:**

- **Removing the in-Python capacity filters.** The stub says to; the
  survey shows they are not legacy (Decision D1). They stay.
- **A new read-only feasibility precheck in `instance.py`.** D11
  imagined one; the create path already claims synchronously, and a
  second decider is the disease this plan cures (Decision D2).
- **The ranking model.** `cpu_load_1`-vs-occupancy ordering is phase
  6's, already closed.
- **The push audit.** Phase 9, last.

## What the survey found

The phase 5 stub (`PLAN-scheduler-reservations.md:847-902`) and
decision D11 were written in 2026-07 and are substantially stale; the
staleness changes the phase's shape, so it is recorded here and
corrected at source in the planning commit.

**F1 — `instance.py` is not a "read-only feasibility precheck"; it
is a real claim-consuming admission point.** D11 says
"`external_api/instance.py` keeps a read-only feasibility precheck …
claim nothing." In fact the create path at
`external_api/instance.py:1125-1200` calls `find_candidates()` and
then walks the candidates calling `inst.place_instance()`, whose
guarded transaction (`instance.py:1285`) is the admission. The comment
at `external_api/instance.py:1163-1182` states this outright: "the
guarded capacity claim inside `place_instance()` is what actually
admits the instance." So two of the three callers already consume
claims; the migration D11 imagined has in effect already happened.

**F2 — the real caller work is de-duplication, not migration.** The
placement walk (a `place_walk(enforce_demand)` closure, then a
demand-waived second walk) exists verbatim in both
`operations/node_inst_netdesc_op.py:249-290` and the create path in
`external_api/instance.py`. Both carry the identical comment: "until a
shared helper is extracted (see PLAN-scheduler-reservations.md), a
semantic change here must be made there too." That duplication, not a
caller still on an old path, is what phase 5 removes.

**F3 — the in-Python capacity filters are not legacy.**
`_has_sufficient_cpu/_ram/_disk` (`scheduler.py:445-636`) were
re-engineered on 2026-08-14 into ledger-aware pre-filters that read the
same `scheduler_node_capacity` counters the guard draws down, charging
each node `max(measured, committed)`. Their docstring
(`scheduler.py:446-473`) is explicit: "This is not the admission
decision. The real guard is the atomic UPDATE … This filter's job is
to prune the candidate list cheaply so that the guard misses less
often." Removing them (as the stub's "remove the legacy in-Python
capacity filtering" instructs) reintroduces the exact failure that
re-engineering fixed: a full-but-idle-measuring node winning the load
ordering and then being refused by the guard, which cost merge CI a
whole suite of creates on 2026-08-14. They stay.

**F4 — `admin.py`'s capacity view already reads the typed columns.**
D11 says it "becomes a read over the typed capacity columns." That
already happened in phase 7a: `summarize_resources()`
(`scheduler.py:1138`) reads the counters through `_capacity_by_node()`
and `_cluster_capacity()` and publishes `cluster_capacity` and
`excluded` (`scheduler.py:1164-1200`). All that remains is that
`admin.py:177` still does `s = scheduler.Scheduler(); return
s.summarize_resources()` — constructing a placement decider to call
one read method. Removing that construction is the only `admin.py`
work left, and 7a's `test_nodes.py` tests pin the two keys the read
must keep.

**F5 — the hard-ceiling scaffolding is all in place; the flip is a
flag plus a refusal.** `CLAIM_ENFORCEMENT_HARD = False`
(`mariadb.py:27461`) is the single gate. The SQL layer already honours
it: `claim_guarded = enforce and CLAIM_ENFORCEMENT_HARD`
(`mariadb.py:28635`), and `admit_instance_placement()` already returns
`claim_over_limit`, `claim_dimensions` and `claim_uuid`. Today
`instance.py:_event_claim_over_limit()` (`instance.py:1206-1270`) fires
a warning on an admitted-but-over-claim placement; its own docstring
says "Phase 5 turns the same condition into a refusal." The flip
therefore has three parts: set the flag, convert that advisory event
into a 403 refusal carrying the structured body, and (F6) fix the
transient-stage classification.

**F6 — flipping the flag has a known side effect in `base.py`.**
`TRANSIENT_CAPACITY_STAGES` (`external_api/base.py:135-148`) includes
`CAPACITY_GUARD_STAGE` with a comment: "This holds only while
`mariadb.CLAIM_ENFORCEMENT_HARD` is False … Once that constant is
flipped … a claim denial becomes namespace quota exhaustion, which no
wait clears, and claim denials must be split out of this set at that
point." The flip step owns that split, or a claim refusal inherits a
`Retry-After` that will never succeed.

**F7 — the gate is a calibration re-run, recorded as plan-blocker
b13.** D16's "advisory for one release, then hard" was corrected on
2026-08-27 to gate on phase 4c's observation record rather than
elapsed releases, because nothing calibrates against an empty claim
population. As of this planning run that record needs re-running on a
populated cluster (plan-blocker b13). The flip step (step 3) must not
be implemented until b13 clears.

Nothing else in the stub was found wrong: the queue worker
(`node_inst_netdesc_op.py`) is indeed a placement caller, and the
three call sites are exactly the three the stub names.

## Decisions

**D1 — the in-Python capacity pre-filters stay, and the master plan
is corrected to say so.** This reverses the stub's "remove the legacy
in-Python capacity filtering." They are not a second uncoordinated
decider: since 2026-08-14 they read the same counters the guard draws
down and exist only to prune the candidate list so the guard misses
less often. *This is the decision a reviewer is most likely to
challenge*, because the stub is explicit and this contradicts it. The
rebuttal is in F3: the filters were rebuilt specifically to track the
ledger, their own docstring disclaims being the admission decision,
and removing them is a known regression with a dated merge-CI cost, not
a cleanup. The plan keeps them and adds one sentence to the master plan
recording why the instruction is discharged.

**D2 — no new read-only precheck is added to `instance.py`.** D11
wanted one; F1 shows the create path already finds candidates and
claims synchronously, and `find_candidates()`'s cheap filters already
"fail obviously-unfittable requests early." Adding a separate
read-only precheck now would be a second placement decider, which D11's
own last sentence forbids ("two uncoordinated schedulers is the
disease this plan cures").

**D3 — the shared placement helper is a function on `Instance`,
`place_on_candidates(candidates, …)`, in `shakenfist/instance.py`.**
Both call sites already hold an `Instance` and call
`inst.place_instance()` per candidate; the walk, the denial-collection
dict, the two-pass demand logic and the exhaustion outcome are the
natural second half of `place_instance`'s own contract and belong
beside it. It returns the chosen node or `None`, and records the
per-candidate denials the callers event today, so neither caller loses
an event. The callers keep their own divergent *before* (candidate
construction, forced lists) and *after* (HTTP response vs operation
redirect) handling; only the walk is shared. The alternative — a free
function in `scheduler.py` — was rejected because the walk mutates an
`Instance` and reads nothing from the `Scheduler`.

**D4 — `admin.py` calls `scheduler.summarize_cluster_resources()`, a
module-level function.** `summarize_resources()` reads no instance
state and only needs a fresh metrics/counter snapshot; it does not need
a `Scheduler` object. Make a module-level `summarize_cluster_resources()`
that does the metric refresh and the read, and have the bound method
delegate to it (or move the body and drop the method if no other caller
remains — the survey step confirms callers first). `admin.py` then
calls the function. The two 7a keys (`cluster_capacity`, `excluded`)
and 7a's `test_nodes.py` assertions are the regression guard.

**D5 — the ceiling flip is step 3, last, and gated on b13.** Steps 1
and 2 (de-duplication, `admin.py`) depend on nothing external and are
implementable and reviewable immediately. Step 3 (the advisory→hard
flip) must not be implemented until the calibration re-run (plan-blocker
b13) has produced the observation record D16 gates on. This phase may
land steps 1–2 first and step 3 in a later commit on the same branch,
or hold the whole phase until b13 clears — an implementation-time
choice for the operator. The plan is written so step 3 is a clean,
self-contained final commit.

**D6 — the hard refusal is a 403 with the D16 body, and is not
transient.** When `CLAIM_ENFORCEMENT_HARD` is True and a placement
would exceed the namespace claim, the create returns HTTP 403 (not the
507 `capacity_error` path) with a structured body naming the claim
uuid, the per-dimension limit, the current drawdown and the shortfall,
so "grow the claim by X" is mechanical (D16). Claim denials are removed
from `TRANSIENT_CAPACITY_STAGES` (F6) so they carry no `Retry-After`:
namespace quota exhaustion is not a momentary shortage.

## Step plan

All implementation is done by sub-agents per the execution model; the
management session reviews each against the files, not the summary.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | worktree | **Extract the shared placement walk.** Add `place_on_candidates(self, candidates, log_targets)` to `Instance` in `shakenfist/instance.py`, directly after `place_instance()` (ends ~`instance.py:1283`). It must reproduce, exactly, the two-pass walk currently duplicated in `operations/node_inst_netdesc_op.py:249-290` and the create path in `external_api/instance.py:1163-1205`: pass one walks candidates calling `self.place_instance(candidate, enforce_demand=True)`, collecting `{node: {failing_stage, dimensions, demand_only}}` on each `CapacityAdmissionDenied` and emitting the "candidate refused by capacity guard" audit event; if nothing admitted and at least one candidate was refused on demand alone, pass two re-walks with `enforce_demand=False`. Return the chosen node uuid or `None`. Do **not** catch `WriteException` inside the walk (both callers rely on it propagating). Then route both call sites through it, preserving each caller's own candidate-list construction, forced-candidate handling, and post-walk behaviour (instance.py returns an HTTP response / enqueues delete; node_inst_netdesc_op redirects the operation). Remove the now-shared comment block from both. This is behaviour-preserving: `test_scheduler.py` and the functional placement tests must pass unchanged. Add a unit test that the demand re-walk fires only when a demand-only denial occurred. |
| 2 | medium | sonnet | none | **De-Scheduler the admin capacity view.** First grep for every caller of `Scheduler.summarize_resources` (`grep -rn summarize_resources shakenfist/`). Add a module-level `summarize_cluster_resources()` to `shakenfist/scheduler.py` holding the body of `summarize_resources()` (the metric refresh + the `_capacity_by_node()`/`_cluster_capacity()` read + the `per_node`/`excluded`/`cluster_capacity`/`total` assembly), and make `admin.py`'s `AdminResourcesEndpoint.get` (`external_api/admin.py:177`) call it directly instead of constructing `scheduler.Scheduler()`. If `summarize_resources()` has no remaining callers, remove the method; otherwise leave it delegating. The response body must be byte-for-byte what it is now — 7a's `test_nodes.py` assertions on `cluster_capacity` and `excluded` are the guard; run them. |
| 3 | high | opus | worktree | **SUPERSEDED on 2026-11-07 by [phase 5a](PLAN-scheduler-reservations-phase-05a-hard-ceiling.md), which carries this step. Read that plan and not this row: its survey found seven of the premises below wrong or stale, and the line numbers drifted when step 1 of this phase moved the code.** **GATED ON PLAN-BLOCKER b13 — do not start until the calibration re-run has landed.** Flip the namespace claim ceiling from advisory to hard. (a) Set `CLAIM_ENFORCEMENT_HARD = True` in `shakenfist/mariadb.py:27461`. (b) In `shakenfist/instance.py`, where `_event_claim_over_limit()` fires today (the `result['admitted'] and result['claim_over_limit']` branch, ~`instance.py:1182`), the SQL guard now *refuses* over-claim placements when `enforce` is set, so an enforced over-claim surfaces as a `CapacityAdmissionDenied` with the claim stage instead. Confirm the mariadb layer (`admit_instance_placement`, ~`mariadb.py:28541-28640`) returns the claim denial with `claim_uuid`, per-dimension `limit`, `used` and `shortfall`, and that `place_instance()` raises it. (c) In `external_api/instance.py`, map a claim-stage `CapacityAdmissionDenied` to HTTP 403 with a structured body {claim, per-dimension limit, drawdown, shortfall} (D16/D6), distinct from the 507 `capacity_error` node-capacity path. (d) Split the claim stage out of `TRANSIENT_CAPACITY_STAGES` in `external_api/base.py:135-148` so a claim refusal carries no `Retry-After`. Add unit tests: an over-claim create returns 403 with the body, and the claim stage is absent from the transient set. Update the advisory wording in `docs/operator_guide/scheduler.md`, `docs/operator_guide/database.md`, the glossary and the release notes from "advisory this release" to enforced (grep the 10 lines phase 8 recorded in its Outcome, item 12). |
| 4 | medium | sonnet | none | **Docs and master-plan scope.** Update `docs/operator_guide/scheduler.md` and `ARCHITECTURE.md` to describe the consolidated single placement helper and that the in-Python filters are ledger-aware pre-filters, not a second decider (D1). Ensure no doc still describes `instance.py` as a read-only precheck. (The advisory→hard doc edits are step 3's, because they must land with the flip, not before it.) |

Steps 1, 2 and 4's non-flip parts may land together; step 3 is a
separate, gated final commit.

## Risks and mitigations

- **The shared helper diverges in behaviour from one of the two
  callers.** The walk has subtle invariants (the demand re-walk only
  on a demand-only denial; `WriteException` not caught inside; denial
  events per candidate). *Mitigation:* step 1 is behaviour-preserving
  and runs the existing `test_scheduler.py` and functional placement
  suites unchanged; the management session diffs the extracted helper
  against both original blocks line by line before accepting. A unit
  test pins the re-walk trigger.
- **Flipping `CLAIM_ENFORCEMENT_HARD` before the calibration exists
  refuses real creates on mis-sized claims.** That is the entire reason
  for the advisory window. *Mitigation:* Decision D5 gates step 3 on
  plan-blocker b13; the step brief repeats the gate in bold. The
  management session confirms b13 has cleared (`plan-blockers status
  --plan scheduler-reservations`) before dispatching step 3.
- **A claim refusal inherits `Retry-After` and clients retry forever.**
  *Mitigation:* F6/step 3(d) splits the claim stage out of
  `TRANSIENT_CAPACITY_STAGES`, with a unit test asserting its absence.
- **`admin.py`'s response body changes shape when de-Schedulered.**
  *Mitigation:* step 2 keeps 7a's `test_nodes.py` assertions as the
  guard and requires the body be byte-for-byte unchanged.

## Definition of done

Falsifiable; the management session runs each.

1. `grep -c 'def place_walk' shakenfist/external_api/instance.py
   shakenfist/operations/node_inst_netdesc_op.py` is 0 in both: the
   duplicated closure is gone.
2. `grep -c 'def place_on_candidates' shakenfist/instance.py` is 1, and
   both former call sites call it (`grep -c place_on_candidates` ≥ 1 in
   each of `external_api/instance.py` and
   `operations/node_inst_netdesc_op.py`).
3. `grep -c 'Scheduler()' shakenfist/external_api/admin.py` is 0; the
   capacity endpoint calls `summarize_cluster_resources`.
4. `scheduler.Scheduler()` is constructed in exactly the call sites
   that still decide placement:
   `grep -rn 'scheduler.Scheduler()' shakenfist/ | grep -v test` lists
   `external_api/instance.py` and `node_inst_netdesc_op.py` only (not
   `admin.py`).
5. `tox -e py3 -- shakenfist.tests.test_scheduler` and the
   `test_nodes.py` capacity-surface tests pass.
6. **Step 3 only, after b13:** `grep -c 'CLAIM_ENFORCEMENT_HARD = True'
   shakenfist/mariadb.py` is 1; an over-claim create in a unit test
   returns HTTP 403 with a body carrying `claim`, a per-dimension
   `limit` and a `shortfall`; and the claim stage is not in
   `TRANSIENT_CAPACITY_STAGES`
   (`grep -A15 'TRANSIENT_CAPACITY_STAGES' external_api/base.py` does
   not list the claim stage).
7. **Step 3 only:** no file under `docs/` still says the claim ceiling
   is "advisory this release" or equivalent — the 10 lines phase 8's
   Outcome item 12 recorded are each updated to say it is enforced.
8. `pre-commit run --all-files` passes.
9. The master plan's phase 5 stub no longer instructs removing the
   in-Python filters, and no doc describes `instance.py` as a read-only
   precheck (`grep -rn 'read-only feasibility precheck' docs/ shakenfist/`
   returns only this plan, if anything).

## Back brief

Before executing any step, back-brief the operator on the plan and how
the intended work aligns with it. In particular, **gate**: confirm
plan-blocker b13 (the calibration re-run) has cleared before step 3 is
dispatched, and agree the shape of the shared `place_on_candidates`
helper (Decision D3) before step 1's edit begins — it is cheap to
propose and expensive to redo once two callers depend on it.

## Execution notes

*2026-11-05 (+1100).* Back brief answered: the operator approved the
`Instance.place_on_candidates()` shape (D3) and chose to land the
ungated work now and hold the ceiling flip. Plan-blocker b13 (the
calibration re-run) was still WAITING at execution time, so step 3 was
**not** implemented; steps ran in the order 1, 2, 4, each by a
sub-agent and reviewed and committed in the management session.

- Step 1 — shared walk: `036f255758`.
- Step 2 — admin capacity view: `6da5a5ef83`.
- Step 4 — docs: `2464830e3a`.
- Step 3 — hard-ceiling flip: **deferred**, gated on b13, and on
  2026-11-07 **moved to
  [phase 5a](PLAN-scheduler-reservations-phase-05a-hard-ceiling.md)**.
  This note used to say the step lands as a separate commit on this
  branch; that is no longer possible, because the branch merged as
  #4800 and was deleted. Until phase 5a's flip lands the ceiling stays
  advisory (`CLAIM_ENFORCEMENT_HARD = False`) and every doc keeps its
  "advisory this release" wording. This phase is Complete against what
  it put on `develop`; the flip's status is phase 5a's row.

Refinements the steps forced:

- **D4's "reads no instance state" was imprecise.** `summarize_resources()`
  reads a node-metrics snapshot (`self.metrics`), though no per-instance
  state and no `Scheduler` behaviour. Step 2 extracted the body around a
  fresh `get_active_node_metrics()` snapshot rather than by constructing
  a throwaway `Scheduler`; the bound method and its four helpers remain
  as one-line delegators, so the ~25 scheduler-test callers are
  unchanged. The new path is behaviourally identical to the old
  `Scheduler().summarize_resources()` (a fresh scheduler has empty
  metrics and refreshes on the first call).
- **DoD item 5's `test_nodes.py` is a cluster-CI test**
  (`deploy/shakenfist_ci/cluster_ci_tests/`), not locally runnable. The
  response-body guard was exercised locally through
  `tests/external_api/test_admin.py` and `tests/test_scheduler.py`
  (both green); the cluster test runs in CI on push.
- Two review corrections the management session made itself: step 1's
  idle-node regression guard (issue 3813) was patching `Instance.add_event`,
  which the extracted walk no longer routes through — repointed at
  `add_event_multi` so the "no waiver fired" assertion is live again; and
  a step 4 doc paragraph was rewrapped.

Definition of done at this point: items 1–5, 8 and 9 pass locally
(item 5 less its cluster-CI portion); items 6 and 7 belong to the
deferred step 3 and are pending b13.
