# Phase 5 -- Guardrails: the headroom band and the structural minimum

Part of [PLAN-ci-cloud-sizing.md](PLAN-ci-cloud-sizing.md). Phase 4
([PLAN-ci-cloud-sizing-phase-04-topologies.md](PLAN-ci-cloud-sizing-phase-04-topologies.md))
is Complete, and its *What phase 5 inherits* section is the brief this
phase answers.

**Planning effort:** high. The master plan asks for a band to be turned
into a check, but phase 2 left two of the band's three bounds with
open questions attached and phase 4 changed the fleet the band's
numbers were fitted to. Deciding what the check asserts, and against
which distribution, is the whole of the work; the code is small.

## Scope

**In scope:**

* Turning the D3 band from a printed observation into a check that
  warns, with the bounds phase 2 measured.
* Judging the per-node maximum, which the report currently carries
  unjudged.
* A cluster-CI assertion on the deployed topology's node counts and
  total hypervisor ledger, so a topology edit that removes capacity
  fails as itself.
* A warn window on the reshaped fleet, and the decision about whether
  to gate, taken with that window's data.
* Correcting the master plan claims the survey found stale.

**Out of scope:**

* Changing any band number. Phase 2 set them from a 204 job-run
  distribution; this phase adopts them and re-measures rather than
  re-deriving (D2).
* Changing any topology. Phase 4 settled the shapes.
* The demand guard's calibration. Phase 4 found the refusal rate is
  set by an accumulator rather than by node size; that is the sibling
  plan's work, and D5 says what this phase does with it instead.
* Phase 0's D4 (the hardcoded upload-target IP list), which F8 found
  dropped. It gets an issue, not a fix (D8).

## What the survey found

The master plan's phase 5 section is three sentences and every one of
them survives. What did not survive is scattered through the rest of
the master plan and through the report tool's own comments, all of it
written before phases 2 and 4 produced their results.

### F1 -- The cluster-wide band is already implemented, as a report

`tools/ci_headroom_report.py:164-165` defines `BAND_LOWER = 0.35` and
`BAND_UPPER = 0.70`, and `print_verdict()` already renders
`OVERSIZED` / `WITHIN BAND` / `OVERSUBSCRIBED` (`:1713-1719`) into the
job log. The machine-readable record carries `band`, `band_lower`,
`band_upper` and `band_provisional` (`:1725-1727`).

So phase 5 is not building the band. It is deciding what the band is
allowed to do, and removing the provisionality that phase 2 already
discharged.

### F2 -- The tool still says PROVISIONAL, and phase 2 defended the numbers

The comment at `tools/ci_headroom_report.py:158-163` reads "These have
never been checked against a distribution -- phase 2's job is to
replace them or defend them". The printed verdict repeats it three
times (`:2468`, `:2482`, `:2485`), and the record sets
`band_provisional: True` (`:1727`).

Phase 2 defended both numbers, in the master plan's *The headroom
band, with numbers*: the cluster-wide upper bound of 0.70 is never
reached by `slim-primary` in 154 job-runs (maximum 0.519) and is
exceeded by 37 of 50 `slim-tier` job-runs, so it separates the cloud
the plan agrees is too small from the one it does not with no false
positives in the window. The lower bound of 0.35 is called
"numerically right and operationally awkward".

Every one of those five sites is now false. They are this phase's
smallest and most mechanical change.

### F3 -- The per-node bound exists only as prose

The report's `verdict()` docstring says the per-node maximum "is
carried unjudged. Its bounds do not exist yet" (`:1706-1709`), and the
record publishes `per_node_max_p90_fraction` and
`per_node_max_peak_fraction` with no verdict attached. There is no
`0.85` anywhere in `tools/` or in `shakenfist/deploy/shakenfist_ci/`.

The master plan proposes 0.85 and shows why: of job-runs recording no
capacity refusal at all (n=44) only 1 exceeds it, while of those
recording at least one (n=160), 123 do. That is the cleanest
separation in the dataset.

It also carries its own correction, at master plan `:282-289`: the
per-node maximum is *saturated*, sitting at its ceiling in 48% of
`slim-primary` and 100% of `slim-tier` job-runs including plenty that
passed, so "a per-node band has to be read as a statement about what a
topology *should* achieve, not as a per-run alarm the current clouds
could pass". D4 takes that at its word.

### F4 -- The report's exit code is swallowed twice, in the other repository

`shakenfist/actions`'s `tools/ci_headroom_collect.sh:310` invokes the
report as `python3 "${report}" "${report_args[@]}" || true`, and the
workflow step that calls it (`smoke-cluster.yml`, *Collect the cluster
headroom series, refusal census and capacity waits*) carries
`continue-on-error: true`. Both are deliberate and both are
documented: the runs worth measuring are the ones whose tests failed.

This is the structural fact that shapes the whole phase. **A warning
can land in this repository alone; a gate cannot.** GitHub workflow
commands (`::warning::`) and `$GITHUB_STEP_SUMMARY` are read from the
step's stdout and environment regardless of exit code or
`continue-on-error`, and `ci_headroom_report.py` is in this
repository. Making the band *fail* a build requires removing a `||
true` and a `continue-on-error` in `shakenfist/actions`, which only
the operator can push -- phase 4's F7 again, and phase 4's PR #88 is
the worked precedent.

That maps exactly onto the warn-window-then-gate pattern the master
plan asks for, and D7 uses it as the phase boundary.

### F5 -- The actions repo defends against version skew by grepping

`ci_headroom_collect.sh:299` reads

    if [ -s "${waits}" ] && grep -q -- '--waits' "${report}" 2>/dev/null; then

because "the report comes from the triggering component ref and may
predate `--waits`". The report tool is checked out at the pull
request's ref while the collect script comes from `@main`, so any new
*flag* phase 5 adds must either be feature-detected the same way or be
absent from old refs harmlessly.

A warning that the report emits unconditionally needs no flag and is
therefore skew-safe by construction. D3 takes that route.

### F6 -- The structural minimums are real, still resolve, and are asymmetric

All four references in the master plan's structural-minimum list check
out against the tree today:

| Requirement | Site | Guard |
|---|---|---|
| Two hypervisors that are not the network node | `cluster_ci_tests/test_network_lifecycle.py:53` | `len(candidates) < 2` -> skip |
| Three nodes, counting every node | `cluster_ci_tests/test_scheduler.py:128` (`test_affinity`) | `len(nodes) < 3` -> skip |
| Three nodes, counting every node | `cluster_ci_tests/test_scheduler.py:291` (`test_binary_affinity_prefers_the_tagged_node`) | `len(nodes) < 3` -> skip |
| Two database nodes | `cluster_ci_tests/test_database_tier.py:37` | `len(database_nodes) < 2` -> skip |

The two cluster topologies do **not** satisfy these equally. Read from
`shakenfist/actions`:

| | `slim-primary` | `slim-tier` |
|---|---|---|
| Instances | 6 | 3 |
| Hypervisors | 5 | 3 |
| Non-network hypervisors | 4 | 2 |
| Database nodes | **1** | 2 |
| Per-hypervisor ledger | 3, 6, 6, 6, 6 | 6, 6, 12 |
| Total hypervisor ledger | 27 | 24 |

`slim-primary`'s primary carries `allsf,database_node,primary_node`
and is **not** in `hypervisors` (`:84`), which is why it has five
hypervisors from six instances and why its database tier is a single
node. `slim-tier`'s primary carries
`allsf,database_node,primary_node,network_node,hypervisors` and sf1
carries `hypervisors, database_node, allsf`, giving it two database
nodes from three instances.

So `test_database_tier` skips on `slim-primary` and runs on
`slim-tier`. A structural assertion that required two database nodes
would fail two of the three cluster jobs. D6 does not assert it.

### F7 -- There is a precedent for failing rather than skipping

`shakenfist_ci/database_tier.py:130-142` already does what this phase
needs, and explains itself:

> This deliberately fails rather than skipping. Every topology SF
> supports has a database tier -- `site.yml` asserts
> `database_tier_hosts | length > 0` and refuses to deploy without one
> -- so an empty list here is a broken cluster or a node role which
> stopped being reported, not a configuration this test does not apply
> to. Skipping would turn both of the assertions below into silent
> no-ops, which is the same vacuous pass [...] exists to prevent.

That is the exact argument for the structural-minimum assertion, and
the new test should be written in its idiom rather than inventing one.

`cluster_ci_tests/test_nodes.py:70-105` is where the per-node ledger
is already read from `/admin/resources`, including the
`cpu_reservation_clamped` consistency check and the infra-versus-plain
comparison. The new assertion belongs beside it, not in a new file.

### F8 -- Phase 0's D4 was assigned to phase 4 and vanished

Phase 0's D4 says the hardcoded `10.0.0.20`-`10.0.0.24`
upload-target list in `functional-tests.yml` "is replaced with a pick
derived from the API, in phase 4", because it "is a coupling between a
workflow file and a topology file that will break silently the moment
any topology changes, which is exactly what phase 4 does".

Phase 4 never touched it and never mentions it: `grep -n '10\.0\.0\.2'
docs/plans/PLAN-ci-cloud-sizing-phase-04-topologies.md` is empty. The
list is still at `.github/workflows/functional-tests.yml:569`, in the
`node_lifecycle_collection` job, which deploys `slim-primary`.

It did not break, because phase 4 changed `slim-tier`'s vCPU and not
`slim-primary`'s node count or addressing. It is still a live latent
coupling and it is in no Future work list. D8 files it.

### What the survey did not find

No claim in the master plan's phase 5 section itself is wrong, and
every file and line reference in the structural-minimum list resolves.
The staleness is entirely in the surrounding material, and every
correction is listed in 5a.

## Decisions

Numbered per phase, as phase 4 numbered its own.

### D1 -- The guardrail splits in two by where the fact lives

**Decision:** the band is checked in `tools/ci_headroom_report.py`;
the structural minimum is checked in `cluster_ci_tests/test_nodes.py`.
They are not unified.

**Reasoning:** the band is a p90 over a job's whole series, so it does
not exist until the job is over. A functional test runs *during* the
job and cannot compute it. The structural minimum is the opposite: a
point-in-time fact about the deployed cluster, available from
`/admin/resources` the moment the cluster is up, and worthless as a
post-hoc report because by then the job has already run its whole
suite against a cloud that was the wrong shape.

### D2 -- The numbers are adopted, not re-derived

**Decision:** this phase ships 0.70, 0.35 and 0.85 exactly as phase 2
set them, and flips `band_provisional` to `False`.

**Reasoning:** phase 2 fitted them to 204 job-runs and defended each
one in writing. Re-deriving them now would mean fitting to a window
that is both smaller and, since phase 4, mixed -- part pre-reshape and
part post-reshape. The right response to a changed fleet is to measure
the new one and say what moved, which is what the warn window in 5e
is for, not to guess a new number first.

**What this phase predicts, so the window can falsify it.** Phase 2
measured 37 of 50 `slim-tier` job-runs above 0.70 on the cluster-wide
figure. Phase 4 doubled that topology's ledger from 12 to 24 while its
workload did not change, so the cluster-wide figure should roughly
halve and `slim-tier` should stop reading `OVERSUBSCRIBED`. It may
begin reading `OVERSIZED`, because 0.35 is not far below half of what
it was scoring. **The cluster-wide figure for the reshaped tier has
not been computed** -- phase 4's 0.17 to 0.58 readings are the
*per-node* `p90 frac` column, not this statistic, and the two are not
interchangeable. If the window shows `slim-tier` sitting below 0.35,
that is the band working: the plan spent phase 4 arguing the tier was
too small, and a topology can be made too big.

### D3 -- The warning is a GitHub annotation the report emits unconditionally

**Decision:** `ci_headroom_report.py` emits `::warning::` workflow
commands for a band violation, and writes a short verdict block to
`$GITHUB_STEP_SUMMARY` when that variable is set. No new command-line
flag, and no change outside this repository.

**Reasoning:** F4 shows the report's exit code is discarded twice, so
a non-zero exit today is invisible. F5 shows a new flag would have to
be feature-detected from the other repository to work at all. An
unconditional annotation sidesteps both: it works on every ref, needs
nothing from `shakenfist/actions`, and lands the warning where a
reviewer sees it without opening a 50-minute job log. When
`GITHUB_STEP_SUMMARY` is unset -- an operator running the tool over a
downloaded bundle -- the tool behaves exactly as it does today.

### D4 -- The per-node bound is judged and published, and never gates

**Decision:** add `PER_NODE_BAND_UPPER = 0.85`, render a verdict for
it, carry it in the record, and warn on it. It is excluded from D7's
gate candidates permanently, not provisionally.

**Reasoning:** this is phase 2's own conclusion, not a new one. The
statistic is saturated at its ceiling in 100% of `slim-tier` and 48%
of `slim-primary` job-runs *including passing ones*, so it cannot
discriminate a bad run from a good one at the top of its range.
Phase 4 confirms it survived the reshape: 3 of 18 node-runs still
recorded `peak frac` 1.000 on the new shape, and one run's `p90 frac`
was 1.000. A gate on 0.85 would fail runs that are fine.

It is still worth publishing, because its *lower tail* is where its
information is: a topology that never approaches 0.85 on any node is
demonstrably not allocation-starved, which is precisely the claim
phase 4 wants to be able to make about the reshaped tier.

### D5 -- The refusal clause is kept, and relabelled

**Decision:** D3's second clause -- any capacity-stage refusal during
an otherwise-passing job is a warning in its own right -- stays in the
output, but is labelled as an observation about the demand estimator's
calibration rather than as evidence the cloud is too small. It is not
a gate candidate.

**Reasoning, and this is the decision most likely to be argued with.**
Phase 4 doubled a cluster's ledger and the guard refused at
essentially the same rate afterwards: 179/181/173/182 before against
147/153/164/176/185/172 after. `expected_demand` accumulates `cpus x
SCHEDULER_DEMAND_PER_VCPU` per admitted placement, so the guard admits
until demand fills whatever bound it is given and then refuses --
which makes the refusal count close to invariant under node size. The
load-alone-versus-feedforward split inverted across the same reshape
(85-94% load-alone before, 29-65% after), which is the mechanism
showing itself.

A reader who sees "capacity refusals occurred, therefore this cloud is
too small" will draw a conclusion phase 4 has already falsified. The
honest alternatives were to delete the clause or to relabel it;
relabelling wins because the count is still the best early signal that
the estimator has drifted, and because phase 0 committed to it in
writing and deleting a commitment silently is the failure mode F8 is
an example of.

**The argument against**, stated plainly so a reviewer can take it:
this leaves a warning in the output that nobody is expected to act on,
which is how warnings become noise. The counter is that the sibling
plan owns the guard and will want the series; the check is that 5f
must name who reads it, or delete it.

### D6 -- The structural assertion names counts and one ledger floor

**Decision:** a new test in `cluster_ci_tests/test_nodes.py` fails --
not skips -- when a cluster topology reports fewer than 3 nodes, fewer
than 3 hypervisors, fewer than 2 hypervisors that are not the network
node, or a total hypervisor ledger below 24. Database-node count is
**not** asserted.

**Reasoning:** the first three are exactly the preconditions the four
tests in F6 skip on, so a topology that violates one of them currently
turns those tests into silent passes -- the vacuous pass F7's
precedent exists to prevent. The ledger floor is the number the master
plan actually cares about and no existing test reads: node count alone
would not have caught `slim-tier` at 12, which had three nodes and was
the cloud this whole plan was written about.

24 is chosen because it is `slim-tier`'s present total, so the floor
sits exactly on the topology phase 4 argued has no slack, and any
reduction to it fails immediately and by name. `slim-primary` clears
it at 27. The cost is that the floor cannot be met by a future
deliberately-smaller topology without an explicit edit, which is the
intent.

Database-node count is excluded because F6 measured the asymmetry:
`slim-primary` has one. Asserting two would fail the `Debian 12
cluster` and `Ubuntu 24.04 cluster` jobs immediately.

**Where the constant lives:** `shakenfist_ci/sizing.py`, beside the
ledger arithmetic that is already there. Its docstring explains that
the module imports nothing from the rest of the suite so
`shakenfist/tests/test_ci_saturation.py` can load it by path -- which
means the floor and the counts get unit coverage that runs in
`pre-commit`, rather than only being exercised by a cluster job in the
merge queue.

### D7 -- Warn first, gate as a separate step, and the gate may not happen here

**Decision:** 5b through 5d land warn-only. 5e measures a window of at
least six merge runs per cluster topology on the reshaped fleet. 5f
decides whether to gate, and the gate itself is an operator change in
`shakenfist/actions`, prepared as a reviewable diff in this repository
the way phase 4's D6 prepared its topology diff. If the window says
the numbers moved, 5f records that and hands the gate to phase 6
rather than gating on a band that no longer describes the fleet.

**Reasoning:** this is the API-validation pattern, and it has a second
job here that it did not have there. There, the warn window existed to
find declaration bugs before rejection was turned on. Here it does
that *and* re-measures a band whose defence rests on a distribution
that predates phase 4. Gating first would be asserting a number
against a fleet it was never fitted to.

Only the two upper bounds are gate candidates: cluster-wide 0.70 by
D2, and neither 0.85 (D4) nor the refusal clause (D5).

### D8 -- The lower bound never gates, and is not a per-run check

**Decision:** 0.35 stays a warning in the per-job report, and the
question phase 2 left open -- per run, or against a job's median
across a window -- is answered **neither**: the per-run warning is
kept as information, and the operational reading of "this cloud is
oversized" is a `tools/ci_headroom_harvest.py` question over a window,
documented as a command in 5g rather than wired to a schedule.

**Reasoning:** phase 2 measured 104 of 154 `slim-primary` job-runs
below 0.35 and called a warning that fires on two runs in three noise.
That is an argument against *gating*, and against treating a single
run's OVERSIZED as actionable -- not against computing it. Being
oversized is not urgent: no build is at risk, and the response is a
topology change nobody makes from one run. The harvest tool already
reads many job-runs and already computes this fraction, so the window
question needs a documented command, not new infrastructure.
`scheduled-tests.yml` has its `schedule:` block commented out, so
wiring this to a cron would mean reviving a disabled workflow for a
non-urgent signal.

### D9 -- Phase 0's D4 gets an issue, not a fix

**Decision:** file an issue for the hardcoded upload-target IP list,
record the number in this plan and in the master plan's Future work,
and do not change `functional-tests.yml`.

**Reasoning:** it is a real dropped commitment (F8) and it deserves to
stop being invisible. It is also unrelated to guardrails, it is not
currently broken, and the fix -- deriving the target from the API --
touches a job this plan has otherwise left alone. Recording it is the
plan's job; fixing it is not.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | **Correct the stale claims the survey found, at their source.** In `docs/plans/PLAN-ci-cloud-sizing.md`: `:209-211` says "The `test_timeout_minutes: 70` and the comment in `functional-tests.yml` claiming the tier 'runs slower' are not borne out" -- phase 4 acted on that, so restate it in the past tense and cite phase 4's 4f. `:1207` says `slim-tier` carries `timeout_minutes: 70` against `slim-primary`'s 60; both are now 60 (`.github/workflows/functional-tests.yml:476-484`). `:1215` says `slim-tier`'s three hypervisors publish ledgers of 3, 3 and 6; they publish 6, 6 and 12 (phase 4's 4d). In Future work, `:1594` "Retire the `test_timeout_minutes: 70` special case" is done -- mark it discharged by phase 4 rather than deleting it, and add the F8 issue from 5f. Do not restate phase 4's reasoning; link to it. |
| 5b | medium | sonnet | none | **Retire the provisionality (D2) and judge the per-node bound (D4).** In `tools/ci_headroom_report.py`: rewrite the comment at `:158-163` to say the bounds were defended by phase 2 against 204 job-runs and cite the master plan's *The headroom band, with numbers*; set `band_provisional` (`:1727`) to `False`; remove "PROVISIONAL"/"provisional" from the printed verdict at `:2468`, `:2482`, `:2485` and replace the four-line PROVISIONAL paragraph after the verdict with one line saying nothing gates on it yet and 5f decides. Add `PER_NODE_BAND_UPPER = 0.85` beside the other two constants, judge `per_node_max['p90']` against it in `verdict()` (`:1704`), carry the verdict and the bound in the record, and print it -- with one sentence, drawn from master plan `:282-289`, saying the statistic is saturated and is read as what a topology should achieve rather than as a per-run alarm. Bump `RECORD_VERSION` (`:178`) to 3 and extend the version comment, because the harvest reads these records from builds older than itself. Add unit coverage in `shakenfist/tests/test_ci_headroom_report.py`. |
| 5c | medium | sonnet | none | **Emit the warning where it is seen (D3).** In `tools/ci_headroom_report.py`, emit a `::warning title=...::` workflow command for each band violation -- cluster-wide over 0.70, cluster-wide under 0.35, per-node over 0.85, and any capacity-stage refusal -- and when `GITHUB_STEP_SUMMARY` is set in the environment, append a short markdown verdict block to that file. **No new command-line flag** and no argument changes: `shakenfist/actions`'s `tools/ci_headroom_collect.sh:299` feature-detects new flags by grepping the report source, and an unconditional emission is skew-safe without that. Open `$GITHUB_STEP_SUMMARY` in append mode, tolerate it being unset or unwritable without failing (the tool is also run by hand over downloaded bundles), and keep the existing stdout prose unchanged so the log reads as it does today. Unit-test both the annotation text and the unset-variable path. |
| 5d | high | opus | none | **The structural-minimum assertion (D6).** Add the counts and the ledger floor to `shakenfist/deploy/shakenfist_ci/sizing.py` as named constants with a docstring in that module's established style, plus a pure function taking the `/admin/resources` `per_node` mapping and the `GET /nodes` list and returning the violations. Then add a test to `cluster_ci_tests/test_nodes.py` beside `test_cluster_resources_reservations` (`:50-105`) which **fails rather than skips** -- follow the idiom and the reasoning in `shakenfist_ci/database_tier.py:130-142` verbatim, including saying in the docstring why skipping would be a vacuous pass. Assert: at least 3 nodes; at least 3 hypervisors; at least 2 hypervisors that are not the network node; total hypervisor ledger at least 24. **Do not assert database-node count** -- `slim-primary` has exactly one (F6). Sum the ledger with the same `cpu_limit`-`None`-falls-back-to-`cpu_hard_max` rule `sizing.effective_cpu_ceiling()` already implements and explains, because a node whose capacity row has not been written yet reports `cpu_limit: None` and must not read as zero. Phase 2 measured a 9-to-14-sample, 135-to-210-second window at cluster start where every capacity row reads absent at once; use `shakenfist_ci/retries.py` so the assertion cannot fire inside it. Unit-test the pure function's boundaries in `shakenfist/tests/`, including the `None` fallback and a topology one unit below each bound. |
| 5e | medium | sonnet | none | **Measure the warn window (D7).** After 5b-5d merge, read at least six merge runs per cluster topology -- `Debian 12 cluster` and `Ubuntu 24.04 cluster` on `slim-primary`, `Debian 12 tier` on `slim-tier`. The matrix `name:` is not what the API returns; they arrive as `Debian 12 tier (collection) / Smoke tests (collection)`, and `merge_group` runs where `check_paths` reports `code_changed == 'false'` skip every functional job and must be filtered out. Record, in this plan's Outcome: the cluster-wide p90 fraction and its band verdict per run; the per-node maximum and its verdict; the refusal count; and whether the new structural assertion passed. Compare against D2's prediction that `slim-tier` should stop reading OVERSUBSCRIBED and may begin reading OVERSIZED, and say explicitly which half was right. |
| 5f | high | opus | none | **Decide the gate (D7), and file F8's issue (D9).** With 5e's window, decide whether to gate on the cluster-wide upper bound of 0.70. If yes, prepare -- as a *Prepared changes* section in this plan, not as an applied edit -- the diff for `shakenfist/actions`: removing `|| true` from `tools/ci_headroom_collect.sh:310` and `continue-on-error: true` from the collect step in `.github/workflows/smoke-cluster.yml`, together with whatever exit-code contract `ci_headroom_report.py` then needs. State what would have failed in the observed window, run by run; a gate that would have failed runs which were fine is not ready. If no, record why and hand it to phase 6. Separately, file the F8 issue against this repository for the hardcoded `10.0.0.20`-`10.0.0.24` list at `.github/workflows/functional-tests.yml:569`, citing phase 0's D4, and record the number here and in 5a's Future work edit. Also settle D5's open check: name who reads the refusal warning, or remove it. |
| 5g | medium | sonnet | none | Close-out: document the band and the structural minimum in `docs/developer_guide/ci.md` including D8's harvest command for the oversized question, set this phase `Complete` in the master plan's Execution table and the `docs/plans/index.md` row (5 of 8 becomes 6 of 8), append phase 4's merge commit `6856aad74` (#4289) to the phase 4 `Merged` cell if 5a has not, write *What phase 6 inherits*, run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`, and confirm every Definition of done item by running it rather than reading it. |

5a, 5b and 5d are independent and can run in parallel. 5c needs 5b.
5e needs 5b, 5c and 5d merged and a merge run on each cluster
topology. 5f needs 5e. 5g is last.

## Risks and mitigations

**The structural assertion fires on a cold cluster rather than a small
one.** Phase 2 measured a contiguous prefix of 9 to 14 samples, 135 to
210 seconds, in every one of 204 job-runs, where every node's capacity
row reads absent at once -- `total.capacity_degraded` was false on all
of them, so it is an unpopulated table during warm-up, not a failing
read. A ledger sum taken in that window reads far below 24. *Mitigated
by* 5d's brief requiring both the `cpu_hard_max` fallback that
`sizing.effective_cpu_ceiling()` already implements and a retry from
`retries.py`; *checked by* the management session reading the test,
and by 5e reporting the assertion's result on every run of the window
rather than only its failures.

**A gate lands that would have failed healthy runs.** *Mitigated by*
D7 making the gate a separate step taken after a measured window, and
by 5f's brief requiring a run-by-run statement of what the proposed
gate would have done to the window it is fitted to. *Checked by* the
operator, who has to push the actions-repo change by hand -- the same
review point that worked for phase 4's PR #88.

**The annotation change silently does nothing.** The report is
executed from the other repository through a script that feature-
detects flags and discards exit codes; a change that depends on
either would appear to work locally and be inert in CI. *Mitigated by*
D3 forbidding a new flag; *checked by* 5e, which reads real merge runs
and must report whether the annotations appeared, not whether the code
emits them.

**The band's numbers no longer describe the fleet.** Phase 4 changed
one of the two cluster topologies after the band was fitted.
*Mitigated by* D2 shipping the numbers unchanged and D7 refusing to
gate before a window on the new shape; *checked by* 5e's explicit
comparison against D2's stated prediction, which is falsifiable in
both directions.

**Warning fatigue.** Four warnings land in a log that already carries
a long summary, and D5 knowingly keeps one nobody is expected to act
on. *Mitigated by* D3 putting them in the step summary and as
annotations rather than only in the log body; *checked by* 5f, whose
brief requires naming a reader for the refusal warning or deleting it.

## Definition of done

Falsifiable, in order:

1. `grep -ci 'provisional' tools/ci_headroom_report.py` is 0.
2. `grep -c 'PER_NODE_BAND_UPPER' tools/ci_headroom_report.py` is at
   least 2 -- the definition and at least one use -- and the value is
   `0.85`.
3. `python3 tools/ci_headroom_report.py --json` over a series emits a
   record whose `verdict` carries a per-node verdict key and whose
   `record_version` is 3 (the key is written at
   `tools/ci_headroom_report.py:1744`), and
   `tools/ci_headroom_harvest.py` reads both version 2 and version 3
   records without error. No CI bundle is needed and none may be to
   hand: `shakenfist/tests/test_ci_headroom_report.py` already writes
   its own `headroom.jsonl` fixtures through the helper at `:201`, so
   build one from there. Run it, do not reason about it.
4. Running the report with `GITHUB_STEP_SUMMARY` unset produces
   byte-identical stdout prose to the same invocation on `develop`,
   modulo the band lines 5b changed. Capture the `develop` output over
   the same fixture *before* starting 5c and diff the two runs; do not
   assert it from reading.
5. `grep -c 'GITHUB_STEP_SUMMARY' tools/ci_headroom_report.py` is at
   least 1, and `git diff develop -- tools/ci_headroom_collect.sh`
   is empty because that file is in another repository and this phase
   does not need it.
6. No new command-line flag: the report's argument parser accepts
   exactly the flags it accepted on `develop` before this phase.
   Compare `--help` output.
7. The structural constants live in
   `shakenfist/deploy/shakenfist_ci/sizing.py` and are exercised by a
   test under `shakenfist/tests/` that runs in `pre-commit`, including
   a case where a node's `cpu_limit` is `None`.
8. The new `test_nodes.py` assertion calls `self.fail` and never
   `self.skipTest`, and its docstring says why. `grep -A40 '<new test
   name>' cluster_ci_tests/test_nodes.py | grep -c skipTest` is 0.
9. A merge run on `slim-primary` and one on `slim-tier` both report
   the new assertion passing, read from stestr output rather than
   inferred from job colour.
10. The band verdict appears as a GitHub annotation on at least one
    real merge run, verified by opening the run, not by reading the
    code.
11. Phase 4's `Merged` cell reads `870a5fbec` (#4202), `6856aad74`
    (#4289).
12. Every stale claim F1-F3 and F8 names is corrected at its source,
    and `python3 tools/check-plan-status.py` reports agreement.
13. The F8 issue exists and its number is recorded in this plan and in
    the master plan's Future work.
14. `pre-commit run --all-files` passes.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it and how the work you intend to do aligns
with it.

**Two gates for the operator, not the implementer:**

* **Before 5f prepares anything for `shakenfist/actions`,** agree
  whether the gate is wanted at all. Phase 4 showed that a change to
  that repository goes live immediately for every run in flight,
  because `functional-tests.yml:485` references
  `shakenfist/actions/.github/workflows/smoke-cluster.yml@main` with
  no pin to bump. A gate that turns out to be wrong cannot be rolled
  back by reverting here.
* **Before 5d is written,** confirm 24 is the floor wanted. It is
  exactly `slim-tier`'s current total, so it has no tolerance: any
  reduction to that topology fails the assertion, which is the intent,
  but it also means a deliberate future shrink needs an edit to this
  constant in the same change. The alternative -- a floor of 18 or 20,
  with slack -- would not have caught `slim-tier` at 12 either, but
  would tolerate a halving of one node.
