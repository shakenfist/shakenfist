# Phase 2: the baseline measurement window

Parent plan: [PLAN-ci-cloud-sizing.md](PLAN-ci-cloud-sizing.md).

**Planning effort:** high, as the master plan specifies. Phase 1
built the instrument; this phase decides what its output means, and
the whole plan's remaining phases are argued from the numbers this
one publishes. The judgement is not in the arithmetic -- it is in
choosing which statistic the sizing decision is allowed to rest on,
and in being honest about the parts of the measurement that are
still blind.

Decision numbering continues the plan-wide sequence: phase 0 used
D1-D8 and phase 1 used D9-D15, so this phase begins at **D16**.

## Context

The master plan was written from three hand-collected data points
and says so. Phase 1 replaced them with an instrument: every
functional cluster job now samples `/admin/resources` and the node
roster every 15 seconds, takes a filtered Loki census of the
scheduler's per-candidate stage events, and prints a summary into
the job log, with the raw series and census landing in the 90-day
bundle.

Phase 2's job, in the master plan's words, is to "leave phase 1
running for an agreed number of merge runs, then publish the
distribution: what peak utilisation actually is per job, on both
topologies, and how it correlates with the failures". That
distribution is what turns the candidate shapes in the master plan
into a decision, and it is what phases 3, 4 and 5 all draw on.

The survey below found that the waiting is already done.

## Scope

**In scope:**

* A harvest tool in this repository which turns the banked bundles
  from many merge runs into one machine-readable dataset.
* A machine-readable summary record emitted by phase 1's report
  tool, so the harvest and phase 5's guardrail read the same
  contract rather than each parsing prose.
* Publishing the capacity read's `degraded` flag through
  `/admin/resources`, because without it roughly 8% of samples are
  discarded for a reason nobody can name.
* Correcting the refusal census filter in `shakenfist/actions` so
  the capacity guard events are actually collected.
* The baseline itself: the distribution, written into the master
  plan, with the band bounds either defended or replaced (D3), the
  D7 ledger reconciliation closed, and open question 5 answered
  against data rather than against a single run.
* Issues filed for anything the measurement exposes that sizing
  cannot fix.

**Out of scope, deliberately:**

* Any topology change. That is phase 4, and phase 3 gates it.
* Any change to what the scheduler admits. If the ledger-unreadable
  window turns out to be a real defect, this phase files it and
  phase 4 does not depend on it being fixed.
* Turning the band into a gate. Phase 5 owns that; this phase only
  supplies the numbers it will gate on.
* Documenting the sizing model in `docs/developer_guide/ci.md`.
  That is phase 6. The harvest *tool* gets a paragraph, because it
  is a tool a reader will otherwise not know exists.
* The under-cloud probe, per-suite concurrency, and the other
  master-plan *Future work* entries.

## What the survey found

Seven findings. Four contradict claims this phase inherited and are
corrected at source in the planning commit -- in the master plan's
Situation and phase 2 sections -- so the next reader does not trip
over them. Re-check those rather than redoing them.

**1. The waiting is already done: 66 merge runs are banked.** The
master plan's phase 2 section reads as though a window must be
opened and waited out. It need not be. The census-filter fix
(`shakenfist/actions` PR #45) merged 2026-08-30, and
`gh api "repos/shakenfist/shakenfist/actions/workflows/functional-tests.yml/runs?event=merge_group"`
returns **66 runs created since then**, each carrying five cluster
bundles at roughly 5 MB apiece. Artifact retention is 90 days
(`smoke-cluster.yml:584`), so the whole window is downloadable
today and none of it is downloadable in December. The measurement
this phase exists to publish is a harvest, not a wait. D16.

**2. The instrument works, on both topologies, and its first two
readings already disagree with the plan's headline statistic.**
Merge run 33944911413 (2026-09-05) printed a complete summary for
both shapes:

| | samples | cluster p90 frac | cluster peak frac | ledger |
|---|---|---|---|---|
| `slim-primary` | 131 | 0.407 | 0.481 | 27.0 |
| `slim-tier` | 133 | 0.667 | 0.833 | 12.0 |

Read cluster-wide, `slim-primary` looks comfortable. Read per node,
it is not: node `12958f0f` sat at **p90 1.000 and peak 1.000** of
its own six-vCPU ledger for the whole run, and `0c1c16b9` (the
three-vCPU network node) peaked at 1.000, while two other nodes
peaked at 0.500. The same run recorded **12 candidate drops at
`sufficient_idle_cpu`, reason "would exceed hard max CPUs"** -- real
refusals, in a job whose cluster-wide utilisation was 41%. On
`slim-tier` all three nodes peaked at 1.000.

A cluster-wide fraction averages a full node against an empty one
and reports the mean as headroom. That is the statistic D3's band
is currently written against. This is the evidential basis for D21,
and it is the most decision-relevant thing the instrument has said
so far.

**3. Roughly 8% of samples are discarded, and nothing can say
why.** Both runs report a `LEDGER UNREADABLE` block: 10 of 131
samples on `slim-primary` and 12 of 145 on `slim-tier` had
`cpu_committed_row_present` false for *every* node at once. Those
samples are excluded from every committed-CPU figure.

The cause is not knowable from the data. `_capacity_by_node()`
(`shakenfist/scheduler.py:234`) returns `(capacity, degraded)`, and
the degraded flag distinguishes a failed read from an unpopulated
table -- `GetSchedulerNodeCapacity`
(`shakenfist/daemons/database/main.py:2760`) forwards it precisely
so that the two are not confused. But `summarize_resources()`
throws it away at `scheduler.py:1005` (`capacity, _ =`), with a
comment explaining that the summary has no instance to record an
event against. That reasoning is sound for an event; it is not a
reason to omit the flag from the response body. Phase 1's D12 added
`cpu_limit` to the same block for the same class of reason.

This matters beyond the measurement. During such a window the CPU
pre-filter charges every node zero and compares against no limit,
so the cheap pruning in front of the admission guard is blind --
which is exactly the window in which a burst of concurrent creates
would be admitted and then refused by the guard. Whether that is
happening is not assertable today. **D19** publishes the flag;
**step 2f** files the issue if the flag says the reads are failing.

**4. The capacity guard census is structurally empty, and the
report says so itself.** `tools/ci_headroom_collect.sh` in
`shakenfist/actions` queries
`{job="shakenfist"} |~ "schedule (at stage|has no candidates at stage)"`.
That regex matches the scheduler's stage events and nothing else.
The two guard messages -- `instance placement denied`
(`shakenfist/instance.py:1303`) and `placement admitted over
namespace capacity claim` (`:1209`, `:1218`) -- do not match it, so
every census in the banked window carries **zero** guard events.
The report prints a *Capacity guard census* section which says
outright that this is a fact about the query before it is a fact
about the cluster, and names both missing strings. Phase 1's own
output caught this. **D20** fixes the filter; the retrospective
window keeps the hole and reports it as unknown rather than zero.

**5. The tier's ledger is 12, and the "10 by observation"
discrepancy no longer exists.** The master plan records the
`slim-tier` ledger as "12 by derivation, 10 by observation",
citing #3907, and phase 0's D7 made reconciling the two this
phase's job. Both halves now say 12. The live series reports a
cluster ledger of exactly 12.0 with per-node limits of 3, 6 and 3,
and 399 node-samples carrying a real capacity row against **zero**
fallbacks to `cpu_hard_max`. #3907 itself closed COMPLETED on
2026-08-27, and its final recurrence comment quotes the cluster
singleton refusing a claim with `cpus (limit 12, used 9, requested
4)` -- the singleton says 12 too. **D7 is closed by evidence
already in hand.** Corrected at source.

Two caveats the harvest must still honour. The singleton's total is
*not* published by `/admin/resources` (`summarize_resources()`
builds `total` from per-node arithmetic only), so the harvest
confirms the sum of the rows and cites #3907 for the singleton
rather than measuring it. And "zero fallbacks" is over two runs;
the harvest reports the fallback count across the window, because a
single node without a row would change what the ledger column
means.

**6. Memory is nowhere near binding, which puts D5 in question.**
Phase 0's D5 decided memory is "a real second dimension" on the
strength of one post-#3813 run refusing at
`sufficient_idle_memory`. In both survey runs the memory stage
recorded **144 events and zero drops**, and committed memory ran at
a p90 of 0.136 of ledger on `slim-primary` and 0.199 on
`slim-tier` -- against 0.407 and 0.667 for CPU. Two runs are not a
distribution, which is exactly why this is listed as a finding to
test rather than as a correction. But the master plan's phase 2
section says to "expect it to answer open question 5", and the
expected answer has flipped: the harvest is now testing whether D5
should be *narrowed*, not whether it holds.

**7. The report has no machine-readable output, and the series
carries no topology label.** `tools/ci_headroom_report.py` takes
`--series`, `--census`, `--label` and `--census-limit`
(`:1630-1656`) and prints prose. A harvest over 300-odd bundles
cannot parse that, and phase 5's guardrail should not either. D18
adds the record. Separately, the topology is passed to the *report*
at run time (`--label "${topology} ${stestr_config}"`) and never
written into the series, so a bundle on disk does not say which
shape produced it; the artifact name does
(`bundle-shakenfist-full-debian-12-slim-tier`), but not for
`guests` or `ansible-modules`, which are `slim-primary` per
`functional-tests.yml:462,511`. The harvest uses an explicit table
that fails loudly on an unknown bundle, and D20's actions change
writes a label file so future harvests do not need one.

## Decision items

### D16 -- Harvest the banked window; do not open a new one

**Decision:** the baseline is computed retrospectively from the
merge runs already banked since 2026-08-30, not by agreeing a
number of future runs and waiting for them.

**Reasoning:** the master plan wrote phase 2 as a waiting exercise
because, when it was written, no instrument existed. One has
existed for a week, and survey finding 1 counts 66 merge runs of
output sitting in artifact storage. Waiting would add weeks and no
information. It would also lose information: retention is 90 days,
so the early part of the window expires while we wait for the late
part. The one thing a prospective window buys -- data recorded
*after* D19 and D20 land, and therefore free of findings 3 and 4's
blind spots -- is bought much more cheaply by step 2g's short
confirmation window, which needs a handful of runs rather than a
statistically useful number.

**What a reviewer might say instead:** that a retrospective window
measures a moving target, since the tree changed under it across 66
runs. That is true and is the reason the harvest records the head
SHA per run and the report is run per job rather than pooled -- a
step change part-way through the window is visible as one, and
`git log` explains it. A prospective window has the same problem
with a smaller n.

### D17 -- Every *instrumented* cluster job, not only the two named topologies

**Decision:** the harvest covers every job that actually carries
the phase 1 probe, reports per job as well as per topology, and
treats the set as exactly these four per merge run:

| Job | Topology | Bundle artifact |
|-----|----------|-----------------|
| Debian 12 cluster | `slim-primary` | `bundle-shakenfist-full-debian-12-slim-primary` |
| Ubuntu 24.04 cluster | `slim-primary` | `bundle-shakenfist-full-ubuntu-2404-slim-primary` |
| Guests | `slim-primary` | `bundle-shakenfist-full-guests` |
| Debian 12 tier | `slim-tier` | `bundle-shakenfist-full-debian-12-slim-tier` |

**Corrected after this plan was committed.** This decision
originally named six jobs, adding `Ansible modules` and `Node
lifecycle`. Neither is instrumented, and both were checked
empirically against merge run 33944911413 rather than reasoned
about: their bundles contain no `traces/` directory at all. The two
causes are different. `Ansible modules` runs through the reusable
`smoke-cluster` workflow but with `test_kind: ansible-modules`
(`functional-tests.yml:514`), and every probe step is gated
`if: inputs.test_kind == 'functional'`. `Node lifecycle` never
reaches that workflow: it calls the `build-smoke-cluster` composite
action directly (`functional-tests.yml:554-557`), so the probe
steps, which live in the workflow rather than the action, are not
in its job at all.

**Consequence for the baseline.** The master plan's failure table
spans six jobs, from 92% for node lifecycle down to 19% for the
tier, and the harvest can speak to only four of them. Step 2d must
say so rather than presenting a four-job dataset against a six-job
table. In particular **node lifecycle, the best-performing job in
that table, is unmeasured**, so no claim of the form "utilisation
explains the pass-rate spread" can be made across the whole table.

**Reasoning for the decision itself, unchanged:** three of the four
instrumented jobs run the *same* topology. If `slim-primary`'s jobs
differ from each other in peak demand, the difference is the suite,
not the shape, and phase 4 must not respond to it by resizing the
cloud. Pooling by topology would hide that. It costs nothing, since
the bundles are downloaded per run anyway.

**Not in scope to fix:** instrumenting the other two. Node
lifecycle would need the probe steps moved into or duplicated
beside the composite action, which is a change to how every caller
deploys; `Ansible modules` would need the gate widened. Both are
worth doing and neither is this phase's business -- they are
recorded in the master plan's *Future work* by step 2f.

### D18 -- One summary record, two consumers

**Decision:** `tools/ci_headroom_report.py` gains a
`summary_record()` function returning a plain dict, and a `--json
PATH` option which writes it. The prose summary is rendered from
the same record. The harvest calls the function; phase 5's
guardrail will read the file.

**Reasoning:** the alternative is parsing the printed tables, which
makes every future change to the report's wording a silent break of
the harvest. Deriving both outputs from one record means the number
in the job log and the number in the dataset cannot disagree --
which is a real risk here, because the whole plan turns on people
trusting these figures. The function form rather than only a CLI
flag is so the harvest, which runs locally in this repository, need
not shell out 300 times.

**Constraint carried from phase 1:** the tool still has to run on
the CI runner under stock `python3` with no third-party import. The
record is built from standard-library types only.

### D19 -- Publish the capacity read's degraded flag

**Decision:** `summarize_resources()` publishes
`capacity_degraded` in the response's `total` block, sourced from
the second element of `_capacity_by_node()` which it currently
discards. The probe records it like everything else, and the report
classifies a ledger-unreadable sample as a *failed read* or an
*unpopulated table* accordingly.

**Reasoning:** survey finding 3. Eight percent of samples are being
dropped for a reason the response makes unknowable, and the flag
that would answer it is already computed one line above the point
where it is thrown away. This is the same shape of change as phase
1's D12, for the same reason: the field exists to let a reader see
which of two indistinguishable conditions actually happened.

**What this does not do:** it does not act on the flag, log
anything, or change admission. The discard comment at
`scheduler.py:1000-1004` is correct that this summary has no
instance to record an event against, and that stays true. Only the
response body changes.

### D20 -- Fix the census filter, and label the series

**Decision:** the LogQL filter in `tools/ci_headroom_collect.sh`
(`shakenfist/actions`) is widened to match the two capacity guard
messages alongside the stage events, and the collect script writes
the topology label into `/srv/ci/traces/headroom-label` so it lands
in the bundle. The retrospective window is reported with guard
refusals as **unknown**, never as zero.

**Reasoning:** survey finding 4. The report already tells the
reader this section is meaningless and names the exact strings
missing from the query; leaving it unfixed for another phase means
phase 3, which is about what happens at the capacity boundary,
plans against a census that cannot see the boundary being enforced.
The label is a two-line addition to the same script and removes the
one place where the harvest has to guess.

**Cost:** it lands in `shakenfist/actions`, which cannot exercise a
composite-action change in its own pull request, and only the
operator can push. Phase 1 D15's discipline applies unchanged --
`continue-on-error`, script always exits 0.

### D21 -- The band is per node as well as cluster-wide

**Decision:** D3's headroom band gains a per-node component. The
band is evaluated against **both** the cluster-wide committed
fraction and the *maximum per-node* committed fraction, and a run
is outside the band if either is. The numeric bounds for both come
from the harvest; the provisional 0.35/0.70 are treated as bounds
on the cluster-wide figure only until the distribution replaces
them.

**Reasoning:** survey finding 2. On `slim-primary`, a cluster-wide
p90 of 0.407 -- comfortably inside the provisional band -- coexisted
with one node pinned at 1.000 for the entire run and twelve real
`sufficient_idle_cpu` refusals. The cluster-wide statistic is an
average over nodes, and the scheduler does not admit against an
average: it admits against one node's ledger at a time. A band
written only cluster-wide would have declared that run healthy,
which is the precise failure mode this plan exists to stop making.

**This is the decision most likely to be argued with,** on the
grounds that phase 2 is meant to *supply* the distribution and let
phase 5 decide the band, not amend the band's form. The answer is
that D3 fixed the band's *form* before any distribution existed,
the first two samples of that distribution show the form is wrong,
and a phase which published a distribution against a statistic it
already knew to be misleading would be doing the reader a
disservice. The numbers still come from the harvest; only the shape
of the question changes. If the harvest shows the pinned node is an
artefact of those two runs, step 2d records that and D21 is
narrowed rather than defended.

**Secondary reasoning:** it also gives phase 4 a target it can act
on. "The cluster is 41% used but one node is full" argues for
widening nodes, which is D1's decision; "the cluster is 41% used"
argues for shrinking it, which is the opposite.

### D22 -- The deliverable is prose in the master plan, plus a dataset

**Decision:** the baseline lands as a rewritten *Situation* section
in the master plan -- replacing the hand-collected figures with
measured ones -- plus the harvest dataset committed under
`docs/plans/data/ci-cloud-sizing-baseline/`. The phase does not
propose a topology.

**Reasoning:** the master plan says phase 2's output "is what turns
the candidate shapes above into a decision", and phase 4 makes that
decision after phase 3 gates it. Writing a proposed shape here
would front-run both. Committing the dataset rather than only the
conclusion is what lets phase 4, phase 5 and a future re-measure
check the arithmetic instead of trusting it -- and the bundles it
came from expire in 90 days.

**Size constraint:** the dataset is the per-job summary records, not
the raw series. Roughly 400 records of a few hundred bytes each is
a file of low hundreds of kilobytes, which is reasonable to commit.
The raw series stays in the bundles and is not committed.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Publish the capacity read's degraded flag from `Scheduler.summarize_resources()` in `shakenfist/scheduler.py` (D19). The value is already computed and thrown away: line 1005 reads `capacity, _ = self._capacity_by_node()`. Bind the second element and set `resources['total']['capacity_degraded']` from it, in the `total` dict initialised at `:1008`. Do **not** log, raise an event, or change admission -- the comment at `:1000-1004` explaining why a degraded read is not acted on here stays true and should be extended, not deleted, to say that the flag is now published for the reader instead. `_capacity_by_node()` at `:234` documents the two-value contract; the scheduling path at `:661` shows the other caller. Add a unit test beside `test_summarize_resources_publishes_the_counters` (`shakenfist/tests/test_scheduler.py:805`) pinning both the degraded and non-degraded cases; look at how `test_summarize_resources_reports_a_node_with_no_row` (`:821`) seeds capacity state via `mock_mariadb` to find the hook for forcing a degraded read. The swagger example in `shakenfist/external_api/admin.py` is elided (`admin_resources_get_example` at `:98` is literally `{...}`), so it needs no change. Single quotes, 120 columns. |
| 2b | high | opus | none | Add a machine-readable summary record to `tools/ci_headroom_report.py` (D18). Factor the computation the printer already does into a `summary_record(series, census, label, census_limit)` returning a plain dict, and render the existing prose from that record so the two cannot disagree; add a `--json PATH` argument writing it as one JSON object. **Do not change a single line of the printed output** -- phase 1's tests pin behaviour and a reader comparing an old job log to a new one must see no difference. The tool is 1677 lines with 50 tests in `shakenfist/tests/test_ci_headroom_report.py`; run them. The record must carry, at minimum: the label, sample counts (usable, failed, unparseable), the window start/end/duration, cluster-wide p90 and peak for committed vCPU and memory both absolute and as a fraction, the same per node keyed by node uuid *including that node's ledger*, the ledger-provenance counts (rows present, fallbacks, fallbacks inside unreadable samples, no ledger at all), the ledger-unreadable sample count, the per-stage census tally with aborts and drops and the drop reasons, the guard census state (present versus not collected -- **never** zero when not collected, per D20), the D3 band verdict, and the new per-node maximum fraction D21 introduces. Two hard constraints from phase 1 carry over unchanged: standard library only, because this runs on the CI runner under stock python3; and the tool exits zero always (D15), so a `--json` path that cannot be written prints a warning and does not raise. Add tests for the record: that every number in the record matches the number in the printed prose for the same input, and that an absent census yields the not-collected state rather than zeros. |
| 2c | high | opus | none | Write `tools/ci_headroom_harvest.py` in this repository (D16, D17). It enumerates `merge_group` runs of `functional-tests.yml`, downloads each run's functional cluster bundles, extracts `headroom.jsonl` and `headroom-census.json`, calls 2b's `summary_record()` on each, and writes one JSON object per job per run to an output file. **The bundle is a nested zip and this was verified against a real artifact, not assumed:** the artifact download is a zip whose single entry is `bundle.zip`, and the files you want are inside *that* at `bundle/traces/headroom.jsonl` and `bundle/traces/headroom-census.json`. A sample record is `{'sampled_at': ..., 'resources': {'per_node': ..., 'total': ...}, 'nodes': [...]}`, and the census is a Loki `query_range` response with its streams under `data.result`. Use `gh api "repos/shakenfist/shakenfist/actions/workflows/functional-tests.yml/runs?event=merge_group&per_page=100"` for the run list and `.../runs/<id>/artifacts` for the bundles, both via `subprocess` on the `gh` CLI rather than a HTTP library -- this is a local developer tool, so unlike the report it may use third-party imports, but `gh` handles the auth. Bundles are zips at roughly 5 MB; download to a cache directory keyed by artifact id and **skip anything already cached**, because this will be run more than once. The topology is not in the series (survey finding 7): map it from the artifact name with the explicit four-row table in D17, sourced from the matrix at `.github/workflows/functional-tests.yml:436-480`, and **fail loudly on a bundle name the table does not cover** rather than guessing or skipping silently. Only four bundles per run carry the probe at all -- `bundle-shakenfist-full-ansible-modules` and `bundle-functional-node-lifecycle-collection` have no `traces/` directory, for the two different reasons D17 gives -- so the harvest should skip those two by name, deliberately and with a comment, rather than treating them as missing data. Record per record: run id, head SHA, run creation time, job name, topology, the run's conclusion and that job's conclusion, and the summary record. A bundle with no `headroom.jsonl` (a run predating phase 1, or one whose probe never started) is recorded as such and not dropped. Add unit tests over a fixture bundle zip; follow `shakenfist/tests/test_ci_headroom_report.py` for how it loads a `tools/` script by path with `importlib.util.spec_from_file_location`. Document the tool in one paragraph in `docs/developer_guide/ci.md`, beside phase 1's headroom section -- **the sizing model itself is phase 6, so do not document topologies here**. |
| 2d | xhigh | opus | worktree | Run the harvest over every merge run since 2026-08-30 and write the baseline (D22). This is the phase. Commit the dataset to `docs/plans/data/ci-cloud-sizing-baseline/` (summary records only, not raw series -- D22) with a README naming the window, the tool and the command. Then rewrite the master plan's *Situation* section so every figure in it is measured rather than hand-collected, keeping the section's structure and its cross-references intact. It must answer, with numbers and with n stated for each: what peak and p90 committed vCPU actually are per job and per topology; how the per-node maximum fraction relates to the cluster-wide one (D21 -- if the pinned-node pattern in survey finding 2 does not hold across the window, say so plainly and narrow D21 rather than defending it); whether committed-CPU utilisation correlates with the job failing, which is the master plan's central claim and is now testable -- **but only across the four instrumented jobs (D17), and node lifecycle, the best performer in the master plan's failure table, is not one of them**, so say what the correlation is computed over and do not present it as spanning that table; what the refusal census says per stage, with `sufficient_free_disk` and `sufficient_idle_disk` kept distinct; whether memory ever binds, which decides whether phase 0's D5 stands or narrows (survey finding 6); the ledger-provenance counts closing D7 (survey finding 5 -- the answer is already 12, so this is confirming a count of fallbacks across the window, not re-litigating); and the band bounds, either defending 0.35/0.70 for the cluster-wide figure or replacing them, plus a first proposal for the per-node bound. Be explicit about the two blind spots: guard refusals are **unknown** in this window, not zero (D20, survey finding 4), and the ledger-unreadable samples are excluded for a reason 2a only makes knowable prospectively (D19, survey finding 3). Do not propose a topology -- that is phase 4 and phase 3 gates it. |
| 2e | medium | sonnet | none | Fix the refusal census in `shakenfist/actions` (D20). In `tools/ci_headroom_collect.sh`, widen the LogQL line filter so it matches the two capacity guard messages as well as the scheduler stage events: the current regex `schedule (at stage\|has no candidates at stage)` misses `instance placement denied` (`shakenfist/instance.py:1303`) and `placement admitted over namespace capacity claim` (`:1209`, `:1218`), which is why `ci_headroom_report.py` prints a *Capacity guard census* section saying it has nothing to count. The script's own header comment explains at length why the filter is a regex and why there is **no** `\|= "Added event"` line filter -- keep both explanations and extend the first. Also write the label the script is already passed into `/srv/ci/traces/headroom-label` on the primary, so it lands in the bundle and a later harvest need not infer the topology from the artifact name. Observe the repository's conventions: at most about five lines of inline shell per workflow step, `shellcheck --severity=error` clean, and nothing in this script may fail the job (it must still always `exit 0`, per phase 1 D15). This lands untested -- a pull request against `actions` cannot exercise a composite-action change -- so write it to be inert on failure, and note in the pull request that only the operator can push it. |
| 2f | medium | opus | none | File the issues the measurement exposed, and only those. Two candidates, and each is filed **only if 2d's data supports it**: first, the ledger-unreadable window (survey finding 3) -- if 2a's flag, once running, shows these are failed reads rather than an unpopulated table, file that the CPU pre-filter is periodically blind and say what the observed rate is; if it shows an unpopulated table, file nothing and record the finding in the plan instead. Second, anything 2d finds that sizing cannot fix, which per the master plan's phase 3 framing must be recorded rather than silently absorbed when the clouds grow. Third -- not an issue but a *Future work* entry in the master plan -- that two jobs are uninstrumented for two different reasons (D17): `Ansible modules` because every probe step is gated `if: inputs.test_kind == 'functional'`, and `Node lifecycle` because it calls the `build-smoke-cluster` composite action directly and never reaches the workflow the probe steps live in. Note that the second is the same structural reason the master plan's *Future work* already gives for propagating the probe to downstream repositories, so the two entries should reference each other. Follow the repository's issue conventions and cross-reference #3772 where the signature matches. Add a *Bugs fixed during this work* entry to the master plan for each. Do not fix anything: this phase files and phase 4 does not depend on any of it being fixed. |
| 2g | medium | opus | none | After 2a and 2e have merged and a handful of merge runs have used them, re-harvest a short confirmation window and add an addendum to 2d's baseline. This closes the two blind spots the retrospective window could not: what the capacity guard census actually contains, and whether the ledger-unreadable samples are failed reads or an empty table. Five to ten merge runs is enough for both -- this is a classification, not a distribution, and it must not be allowed to grow into a second measurement window. If the addendum changes a conclusion in 2d, change the conclusion rather than appending a contradiction. Then hand 2f whatever it needs to decide the first issue. |
| 2h | low | haiku | none | Set the phase 2 row to `Complete` in the master plan's Execution table and the index arithmetic in `docs/plans/index.md` to `3 of 7`, then run `python3 tools/check-plan-status.py`. Do this only after 2a-2g are reviewed and the operator has confirmed the addendum in 2g is based on real post-fix runs. |

The survey corrections that would otherwise have been a step here
were made in the planning commit, per the note under *What the
survey found*. Note also that phase 1's row was still `Not started`
in both status tables when this phase was planned, despite its work
having merged on 2026-08-29; that is corrected in the same commit.

## Risks and mitigations

* **The harvest measures a moving target.** Sixty-six merge runs
  span a week of development, and something unrelated to sizing may
  shift the numbers part-way through. *Mitigation:* D16 requires
  the head SHA and creation time on every record and reporting per
  job rather than pooled, so a step change is visible as one; 2d is
  briefed to state n for every figure. This risk is smaller than
  the alternative's, since a prospective window has the same
  problem with less data.
* **Two blind spots are baked into the retrospective window.**
  Guard refusals are uncollected (D20) and 8% of samples are
  discarded for an unknown reason (D19). *Mitigation:* 2d must
  report both as *unknown*, never as zero -- the report tool
  already refuses to print zero for an absent census, and 2b's
  record is briefed to carry the not-collected state rather than a
  count. Step 2g closes both against real post-fix runs before 2h
  can close the phase.
* **D21 pre-empts phase 5.** Amending the band's form on two runs
  of evidence could be wrong. *Mitigation:* the numeric bounds
  still come from the harvest, 2d is explicitly briefed to narrow
  D21 rather than defend it if the pattern does not survive the
  window, and phase 5 remains the phase that turns any of it into a
  gate. The back brief gates this decision before 2d starts.
* **The `actions` half lands untested, again.** Identical to phase
  1's risk and identical mitigation: 2e is `continue-on-error`
  throughout, the script always exits zero, and 2g does not run
  until the operator confirms a real run used it.
* **The dataset commit grows without bound.** *Mitigation:* D22
  commits summary records only, not raw series; 2c is briefed to
  cache downloads outside the repository. If the file exceeds a
  megabyte, 2d should reduce what is committed rather than commit
  it anyway.
* **Phase 2 is tempted to propose a topology.** The numbers will
  make a shape look obvious, and phase 3 gates phase 4 for reasons
  that have nothing to do with the numbers. *Mitigation:* D22 puts
  it out of scope in writing, and 2d's brief ends by saying so.

## Definition of done

Falsifiable, in order:

1. `/admin/resources` publishes `capacity_degraded` in its `total`
   block, and a unit test pins both the degraded and non-degraded
   cases.
2. `tools/ci_headroom_report.py` exposes `summary_record()` and a
   `--json` option, still imports nothing outside the standard
   library, and still exits zero on every path. A test asserts that
   each figure in the printed prose equals the same figure in the
   record for one fixture input, so the two cannot drift.
3. Running `tools/ci_headroom_report.py` on a phase 1 fixture
   series produces byte-identical printed output before and after
   2b.
4. `tools/ci_headroom_harvest.py` exists, is unit tested over a
   fixture bundle, and fails with a named error -- not a silent
   skip -- on a bundle whose artifact name is not in its topology
   table. The two known-uninstrumented bundles are skipped by name
   with a comment, and a test pins that they are skipped rather
   than raising.
5. `docs/plans/data/ci-cloud-sizing-baseline/` holds the harvested
   records and a README naming the window, the tool, and the
   command that reproduces it. The directory is under one megabyte.
6. Every figure in the master plan's *Situation* section is
   traceable to a record in that dataset or to a cited issue. No
   figure in it is described as hand-collected.
7. The master plan no longer says the `slim-tier` ledger is "12 by
   derivation, 10 by observation", and phase 0's D7 is marked
   closed with the evidence. *(Done in the planning commit;
   re-check rather than redo.)*
8. The baseline states, for guard refusals and for
   ledger-unreadable samples, that the retrospective window does
   not know -- and neither is reported as zero anywhere in it.
9. Phase 0's D5 is either confirmed or narrowed against the
   window's memory figures, with n stated, rather than left as the
   single-run judgement it is today.
10. The census filter in `shakenfist/actions` matches both guard
    messages, and a real post-fix run's *Capacity guard census*
    section contains a count rather than the "not collected"
    notice. The operator confirms this; it cannot be checked from
    a worktree.
11. No step added or changed by this phase can fail a CI job:
    every `actions` step remains `continue-on-error` and every
    script still exits zero, verified by reading the diff.
12. `python3 tools/check-plan-status.py` passes, and `pre-commit
    run --all-files` passes in the main repository.

## What phase 3 inherits

* A distribution, so "the cluster really is full" is a measured
  claim rather than a sampled one, and the boundary phase 3 must
  write a test against is a known number.
* A per-stage refusal census over many runs, which is the corpus
  phase 3's inventory of signatures should be checked against --
  phase 0 built that inventory from triage history, and this is the
  first chance to see whether the frequencies match.
* A working capacity guard census, from D20, so a test which fills
  a cluster to its ledger can assert on the guard's own events and
  not only on the pre-filter's.
* An answer on whether memory binds, which decides whether phase
  3's saturation test needs a memory dimension at all.
* The knowledge that the cluster-wide fraction and the per-node
  maximum can disagree by a factor of two, so a saturation test
  which fills "the cluster" must say which of the two it means.

## Back brief

Before executing any step of this plan, back brief the operator on
the understanding of it, and in particular on:

* **D16**, which replaces the master plan's waiting window with a
  retrospective harvest. This is the decision that changes what the
  phase *is*.
* **D21**, which amends D3's band form on two runs of evidence.
  This is the one a reviewer is most likely to disagree with, and
  it should be agreed before 2d starts writing conclusions against
  it -- rewriting the baseline afterwards is expensive.
* **D22's** dataset commit, whose shape and location should be
  agreed before 2d generates it.

Step 2e lands in `shakenfist/actions`, where it cannot be tested
before it merges and where only the operator can push. Step 2g
cannot start until 2a and 2e have merged and real merge runs have
used them, so the phase has a hard pause in the middle that is not
a stall.
