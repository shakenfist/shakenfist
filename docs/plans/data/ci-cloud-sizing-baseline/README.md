# CI cloud sizing baseline dataset

The measured baseline for
[PLAN-ci-cloud-sizing.md](../../PLAN-ci-cloud-sizing.md), harvested in
step 2d of
[PLAN-ci-cloud-sizing-phase-02-baseline.md](../../PLAN-ci-cloud-sizing-phase-02-baseline.md)
(D16, D17, D22). Every figure in the master plan's *Situation* section
that is described as measured is traceable to a record in
`records.jsonl`.

There are **three** files here, with three different jobs to do and
three different versions of the record schema. `records.jsonl` is the
baseline: the retrospective window, and the source of every
distribution the plan reasons about. `records-addendum.jsonl` is step
2g's confirmation window, harvested after the two instrument fixes
phase 2 made had merged, and it exists to close the two things the
baseline could not see. It is a classification, not a second baseline,
and no distribution should be recomputed from it -- it is an eighth
the size and covers a day and a half. `records-warn-window.jsonl` is
the window the band gate was *armed* on, harvested afterwards so that
the arming decision is checkable from this tree rather than from a
table somebody typed; see *The warn window* below.

Anything reading more than one of them must handle the version mix.
`records.jsonl` is record version 1, `records-addendum.jsonl` is 2 and
`records-warn-window.jsonl` is 4, and one field inside `verdict` does
not mean the same thing on both sides of version 4 -- see *Two fields
whose meaning changed after this was harvested*.

The bundles this was computed from expire ninety days after their
merge run, so this directory is the only durable copy. The raw series
and census are deliberately *not* committed -- D22 -- only the per-job
summary records the report tool derives from them.

## The baseline window

| | |
|---|---|
| Repository | `shakenfist/shakenfist` |
| Workflow | `.github/workflows/functional-tests.yml`, `merge_group` events |
| Nominal window | runs created at or after **2026-08-30** |
| Effective window | **2026-08-30T07:48:03Z** to **2026-09-05T07:15:07Z** |
| Harvested on | **2026-09-05** |
| Merge runs enumerated | 66 |
| Merge runs contributing a bundle | 55 |
| Merge runs contributing probe output | 52 |
| Records | **217** |
| Records carrying a usable committed-CPU series | **204** |
| Distinct head SHAs | 55 |

The nominal and effective windows differ for a reason worth knowing
before reading anything else. Eleven of the 66 enumerated runs banked
no functional cluster bundle at all: three were cancelled, and eight
had `Check paths` decide nothing relevant had changed, so every
functional job was skipped. Of the 55 that did, the three earliest --
`33283945854` (00:42), `33287041288` (02:02) and `33289851537`
(03:15), all on 2026-08-30 -- carry bundles with no
`traces/headroom.jsonl` in them: the `shakenfist/actions` change that
starts the probe had not landed yet when they ran. It landed between
03:15 and 07:48 that morning. Those twelve records, plus one job
cancelled mid-run, are the thirteen records whose `summary` is
`null`. They are kept rather than dropped, so that the count of
what could not be measured is itself in the dataset.

## The tool and the command

`tools/ci_headroom_harvest.py` in this repository (added in step 2c),
which calls `summary_record()` from `tools/ci_headroom_report.py`
(step 2b) on each bundle it extracts.

```
python3 tools/ci_headroom_harvest.py \
    --since 2026-08-30 --until 2026-09-05T07:15:07Z \
    --cache-dir /var/tmp/ci-headroom-harvest-cache \
    --output records.jsonl
```

That took 331 `gh api` calls and downloaded 217 bundles totalling
**1.1 GB**, which took a little over two hours of wall clock. The
cache directory is keyed by artifact id and anything already in it
is reused, so a re-run is cheap; any writable directory outside the
repository will do, and the run above used a scratch directory
rather than the path shown.

The harvest was run with `--since` alone; `--until` was added to the
tool afterwards, and the command above carries it because a window
with no end is not reproducible -- it grows with every merge run, so
the command as originally run enumerates more today than it did on
the day it was run. The boundary shown is the last run in the
dataset, and the two together name exactly the window this file
covers. Re-running it will still produce a shorter file eventually:
these bundles expire ninety days after their run.

## What was changed before committing

One normalisation, and only one. `summary.series.path` and
`summary.census.path` were **removed** from every record. They held
the temporary directory the harvest unpacked each bundle into
(`/tmp/ci-headroom-<random>/<artifact id>/...`), which is different on
every harvest of the same artifact, names nothing a reader of this
file can open, and identifies nothing that `artifact_id` and
`artifact_name` do not identify better. Leaving them in would have
made the dataset gratuitously irreproducible: two harvests of the same
window would differ on 408 fields and agree on every number. The
harvest tool itself is unchanged and still emits them; only this
committed copy is normalised.

`records-addendum.jsonl` was re-harvested once, after the report tool
gained the four series counters described below, so that the two
things step 2g concluded about the ledger-unreadable samples could be
recomputed from the committed records instead of from bundles which
expire. The re-harvest was verified record by record against the file
it replaced: same 32 records, same 8 runs, and no field different
except `record_version` and the four new counters.

Nothing else was pruned. D22 budgeted 2 MB and allowed
`absences.classifications[].nodes` and the guard block to be dropped
if that was exceeded; at just under one megabyte it was not, so both
are intact and a re-analysis can use them.

## Record schema

One compact JSON object per line. The framing fields come from the
harvest; `summary` is verbatim the report tool's `summary_record()`
output, minus the two path fields above.

| Field | Meaning |
|---|---|
| `harvest_version` | Schema version of the framing fields (currently 1). |
| `repo`, `run_id`, `run_attempt`, `run_url` | The merge run. |
| `head_sha`, `run_created_at`, `run_conclusion` | Which tree, when, and how the whole run ended. |
| `artifact_id`, `artifact_name` | The bundle this record was computed from. |
| `job` | The readable matrix name: `Debian 12 cluster`, `Ubuntu 24.04 cluster`, `Guests`, `Debian 12 tier`. |
| `github_job_name` | What the jobs API calls it, which is not the same string -- the reusable workflow contributes its own name. |
| `job_conclusion` | That job's own conclusion, or `null` if the prefix match failed. Never the run's, as a fallback. |
| `topology` | `slim-primary` or `slim-tier`, from D17's explicit artifact-name table. |
| `topology_source`, `topology_table_says`, `label` | How the topology was established, and what the series' own label said. |
| `series_present`, `census_present`, `absent_reason` | Whether the bundle carried each file, and why not. |
| `summary` | The report record, or `null` when there was no series. |

Inside `summary`:

| Field | Meaning |
|---|---|
| `record_version` | Schema version of the report record. `records.jsonl` is **1**; `records-addendum.jsonl` is **2**, which adds the four series counters below; `records-warn-window.jsonl` is **4**, which carries `n_fraction` in every metric block and `gates`/`gate_withheld` in `verdict` (both added at version 3, and no file here is version 3), and which changed what `verdict.refusal_warning` says. A consumer that reads more than one must expect the older shape, and must read `verdict.refusal_warning` through the derivation in *Two fields whose meaning changed after this was harvested* below rather than trusting the stored flag. |
| `series` | Sample counts (usable, failed, unparseable), the window start/end/duration, `ledger_unreadable_samples`, and -- from record version 2 -- `ledger_unreadable_prefix_samples`, `ledger_unreadable_prefix_seconds`, `capacity_degraded_samples` and `capacity_degraded_absent_samples`. The last four are what say whether an unreadable ledger was a warm-up or a fault. |
| `ledger_provenance` | Node-samples with a real capacity row, with a fallback to `cpu_hard_max`, with a fallback inside an unreadable sample, and with no ledger at all. |
| `cluster` | Committed vCPU and committed memory MB, cluster-wide: `n`, `p90`, `peak`, `ledger_min`, `ledger_max`, and the two as fractions of ledger. |
| `per_node` | The same, keyed by node uuid, each carrying that node's own ledger. |
| `per_node_max_cpu_fraction` | Per sample, the highest committed-over-ledger ratio any one node stood at; then `p90` and `peak` over samples. This is D21's statistic. |
| `absences` | What the node roster named that `/admin/resources` did not return, classified. |
| `census` | Per-stage tally: events, aborts, drops, shortage drops and drop reasons, plus `capacity_shortage_drops`, `unclassified_shortage_drops`, `disk_bandwidth_drops` and `missing_data_drops`. |
| `guard` | The capacity guard census. In this window its `state` is `not_collected` on every record -- see below. |
| `verdict` | The D3 band verdict against the provisional 0.35/0.70 bounds, and the per-node maximum D21 adds. |

### Two fields whose meaning changed after this was harvested

`records.jsonl` and `records-addendum.jsonl` were harvested before the
report tool reached `record_version` 4, and two fields inside `verdict`
do not mean in them what they mean today. Neither can be re-derived by
re-harvesting, because the bundles are gone.
`records-warn-window.jsonl` sits on the far side of that boundary --
it is version 4 -- so the three files in this one directory disagree
about the meaning of a field rather than merely about which fields
exist, and that is the harder half to notice.

`verdict.per_node_band` is the additive one, and version 3 is where it
changed: in the two older files it is the constant `null`, meaning *not
judged*. From version 3 on, `null` means *no sample produced a per-node
fraction*, and a real verdict is published otherwise -- which is how
`records-warn-window.jsonl` carries it.

`verdict.refusal_warning` is the one to be careful with. In the two
older files it reads `false` for a census which was read and matched no
scheduler stage event at all -- the same value a census which saw the
scheduler and tallied no capacity-stage drop gets. Version 4 separates
them and writes `null` for the first, because a filter which stopped
matching is the instrument failing to look, not the cluster failing to
refuse. An analysis pooling those two with `records-warn-window.jsonl`,
or with any later window, will count the older ones as observed-clean
unless it re-derives the warning itself:

```python
census = r['summary']['census']
observed = census['state'] == 'read' and census['stage_events']
warning = bool(census['capacity_shortage_drops']) if observed else None
```

That computes the version 4 reading from fields every version carries.
In practice it changes nothing about the datasets *here* -- `stage_events`
is non-zero on all 204 summarised records in `records.jsonl`, all 32 in
`records-addendum.jsonl` and all 40 in `records-warn-window.jsonl`, so
no record in this directory is actually in the ambiguous state. The
boundary matters for windows harvested across it, which is why it is
written down rather than left to be rediscovered.

`tools/ci_headroom_check_window.py` is where that derivation now lives
in code rather than in prose. It re-derives the warning for every
record in all three files, asserts that the derivation and the stored
flag agree wherever the stored flag can have an opinion, and reports
the count of records in the ambiguous state -- zero today. A fourth
dataset added to this directory goes in its `DATASETS` list, and if its
ambiguous count is not zero that needs saying out loud rather than
discovering.

## What this dataset does not know

**Nothing, now.** Both entries that stood here are answered, one by
step 2f from the shape of the baseline itself and one by step 2g's
confirmation window. They are kept below because a reader of
`records.jsonl` alone will meet both and should not have to rediscover
them.

### Resolved: capacity guard refusals

`summary.guard.state` reads `not_collected` on all 204 summarised
records in `records.jsonl`, and that is a fact about the LogQL query,
not about the cluster: the census filter in `shakenfist/actions`
matched the scheduler's per-candidate stage events and neither
`instance placement denied` nor `placement admitted over namespace
capacity claim`. **No count of guard refusals over the baseline window
exists, and none may be inferred from the absence of one.**

Step 2e widened the filter and step 2g re-measured. In
`records-addendum.jsonl` the state is `collected` on all 32 records,
and what it holds is in *The confirmation window* below.

### Resolved: the ledger-unreadable samples

This section was written listing a second unknown, and step 2f
resolved it from the data's shape rather than needing step 2a's flag.
It is kept here because `cluster.committed_cpu.n` is smaller than
`series.samples_usable` in every record and a reader will want to know
why.

2,276 of 21,517 usable samples (10.6%) had `cpu_committed_row_present`
false for every node at once, and are excluded from every
committed-CPU figure. They are **an unpopulated table during warm-up,
not a failing read.** In every one of the 204 job-runs those samples
are a contiguous prefix of 9 to 14 samples, never mid-run, never
scattered and never twice, and the prefix ends within seconds of the
capacity reconciler's first pass. A failed gRPC read is an independent
per-sample event; it would not land only on the head of 204
independent runs.

That warm-up window is itself the subject of the defect step 2f
drafted -- `scheduler_node_capacity` has no rows for the first 135 to
210 seconds of a cluster's life, so admission is unguarded throughout
it. See *A node can record twice its own ledger, and sizing would hide
it* in the master plan, and **#4087**.

**Step 2g confirmed it directly**, and the confirmation is in the
records rather than in bundles which expire. `/admin/resources` now
publishes `total.capacity_degraded`, and the report counts it:
summing `series.capacity_degraded_samples` over
`records-addendum.jsonl` gives **0** across all 3,359 samples, and
`series.capacity_degraded_absent_samples` is **0** as well, so the
flag was present on every sample and false on every sample --
including in all 367 whose capacity rows read as absent. A failed
capacity read would have set it.

The prefix pattern repeats exactly, and is counted the same way:
`series.ledger_unreadable_prefix_samples` equals
`series.ledger_unreadable_samples` in all 32 records, so every
unreadable sample is in the run the series opens with and none
follows it. The prefix is 9 to 14 samples and
`series.ledger_unreadable_prefix_seconds` spans 120 to 195 seconds
between its first and last sample, so 135 to 210 seconds of wall
clock at the 15 second interval. That is the same figure #4087 was
filed with, measured a second time from a different window with an
instrument that can now tell a failing read from an empty table.

The baseline's own 204 job-runs were classified before those counters
existed, from the raw series inside the bundles. `records.jsonl` is
record version 1 and does not carry them, so the contiguity claim
made about the baseline is not recomputable from this directory --
which is the reason the counters were added.

## The confirmation window

`records-addendum.jsonl`, harvested by step 2g on 2026-09-08.

| | |
|---|---|
| Why | Close the baseline's two blind spots against runs that used the fixed instrument |
| Nominal window | runs created between **2026-09-07T10:00:00Z** and **2026-09-08T02:00:00Z** |
| Effective window | **2026-09-07T10:25:26Z** to **2026-09-08T01:55:33Z** |
| Merge runs enumerated | 10 |
| Merge runs contributing a bundle | 8 (two had every functional job skipped by `Check paths`) |
| Records | **32**, all four instrumented jobs in every run |
| Records carrying a usable series | **32** |
| Distinct head SHAs | 8 |

Both fixes were live throughout: step 2a's `capacity_degraded` merged
to `develop` at 2026-09-06T06:17Z and step 2e's census filter merged to
`shakenfist/actions` at 2026-09-06T00:09Z.

```
python3 tools/ci_headroom_harvest.py \
    --since 2026-09-07T10:00:00Z --until 2026-09-08T02:00:00Z \
    --cache-dir /var/tmp/ci-headroom-harvest-cache \
    --output records-addendum.jsonl
```

This window was first harvested as `--since 2026-09-07 --limit 10`,
which is not a window at all: `--limit` takes the newest runs *as of
the day it runs*, and two more merge runs landed within hours of the
harvest, after which the same command enumerated a different ten. The
boundaries above are the same eight runs, named. The file was
re-harvested with them, and every record came back identical to the
one it replaced apart from the four series counters record version 2
adds.

### The guard census, which the baseline could not see

**3,480 denials over 32 job-runs** -- a median of 121 per
`slim-primary` job-run and 134.5 per `slim-tier` one. The CPU
pre-filter, over the same 32 runs, dropped 283 candidates in 3,862
evaluations and aborted 11 times. The composition of the denials is
almost entirely one thing:

| | count |
|---|---|
| Denials whose sole exceeded dimension is `demand` | 3,477 |
| Denials whose sole exceeded dimension is `cpus` | 3 (one at the `node` stage, two at `cluster`) |
| ... of the demand denials, `demand_measured_alone` | 1,789 |
| ... `demand_estimate_tipped` | 1,688 |
| `malformed`, `unenforced`, `empty_dimensions`, `nothing_exceeded`, `demand_unsplit` | 0 each |
| Unrecognised stages, unrecognised dimensions | none |

**A denial is not a failed create.** Both placement walks
(`external_api/instance.py` and `operations/node_inst_netdesc_op.py`)
catch `CapacityAdmissionDenied`, try the next candidate, and -- when
nothing admitted and every refusal was demand-only, which is what
3,477 of 3,480 of these are -- re-walk with the demand clause waived.
So this is overwhelmingly a count of the walk absorbing a refusal, not
of work the cluster refused.

There were also **24 claim exceedances**, one per namespace across 24
distinct `ci-claimaccount-*` namespaces, on all three dimensions. Those
are *admitted* placements reported over an advisory claim
(`CLAIM_ENFORCEMENT_HARD` is false), never refusals, and they are the
claims suite exercising exactly the path it was written for.

The widened filter did not cost the census its completeness:
`summary.census.truncated` is false on all 32 records, which is the
empirical form of the argument made when the filter was widened.

### What the census still cannot see

Named because it is the same shape of mistake the baseline made once.
The filter matches the two guard messages and the stage events; it does
**not** match the two events that say what became of a denial --
`no candidate admitted and some refused on demand alone, waiving demand
guard`, and `schedule failed, every candidate refused by capacity
guard`. So the number of creates that actually failed on the guard
cannot be derived from this file. It is not zero, and it is not 3,480.

### Comparability with the baseline

Stated so that a reader does not mistake the addendum for a shift.
Committed-CPU medians land where `records.jsonl` left them: cluster-wide
p90 fraction 0.333 median on `slim-primary` (n=24, max 0.481) against
0.296-0.333 per job in the baseline, and 0.792 on `slim-tier` (n=8, max
0.833) against 0.750. Memory dropped nothing in 3,851 evaluations, so
D5's narrowing holds. Ledger provenance is zero fallbacks and zero
missing ledgers over another 13,274 ledgered node-samples, so D7 stays
closed at zero. Of the 7 job-runs with a `sufficient_idle_cpu` abort,
**7 failed**, and 7 of the window's 8 job failures had one -- the same
relationship the baseline found over 204 runs, at a sample size that
could not have established it alone.

## The warn window

`records-warn-window.jsonl`, harvested on 2026-10-05 over the window
the cluster-wide band gate was armed against.

| | |
|---|---|
| Why | Make the arming decision checkable from this tree, before the bundles expire |
| Nominal window | runs created between **2026-09-21T11:10:53Z** and **2026-09-24T12:00:00Z** |
| Effective window | **2026-09-21T23:09:16Z** to **2026-09-24T04:08:22Z** |
| Merge runs enumerated | 20 |
| Merge runs contributing a bundle | 10 (ten had every functional job skipped by `Check paths`) |
| Records | **50** |
| Records carrying a usable series | **40** |
| Distinct head SHAs | 10 |
| Record version | **4**, unlike its two siblings |
| Size on disk | 247 KB |

```
python3 tools/ci_headroom_harvest.py \
    --since 2026-09-21T11:10:53Z --until 2026-09-24T12:00:00Z \
    -o docs/plans/data/ci-cloud-sizing-baseline/records-warn-window.jsonl
```

That took 41 `gh api` calls and about three minutes, not the couple of
hours the baseline took: the baseline enumerated 66 runs and downloaded
217 bundles, and this window enumerated 20 and downloaded 50.

**What it answers that the other two do not.** The band's *bounds*
recompute from `records.jsonl`, and always did. The decision to *arm* a
merge-blocking gate on the upper bound did not: it rested on a run-by-run
statement that the gate would have failed none of the job-runs in this
window, that every one of them carried enough samples for the gate to
have an opinion, and that none of them would have had the gate
withheld. That statement was written down as a table of numbers in
`PLAN-ci-cloud-sizing-phase-05-guardrails.md`, computed from bundles
GitHub keeps for ninety days. After roughly 2026-12-20 nobody could
have checked it. The records are now here and
`tools/ci_headroom_check_window.py` recomputes every figure from them,
in the test suite, on every run.

The recomputation agrees with the table in full: 40 job-runs, a highest
cluster-wide p90 fraction of 0.417 against an upper bound of 0.70, zero
job-runs the gate would have failed, zero with the gate withheld, a
smallest sample count of 67 against a floor of 20, and 30 of 40 below
the lower bound (25 of 30 on `slim-primary`, 5 of 10 on `slim-tier`).
The per-job minima, maxima, band tallies and per-node counts all match,
and so do all forty rows of the plan's per-run table, down to the
capacity-stage drop and guard denial counts.

**Where each of those figures comes from, because the three parts of
the claim are not equally checkable.** This file is record version 4,
so it banks `cluster.committed_cpu.n_fraction` and
`verdict.gates`/`verdict.gate_withheld` directly, and all three parts
of the claim can be read out of it. The checker reads none of them: it
recomputes the band from the fraction and the bounds, and the gate and
withholding conditions from the sample counters, and then compares its
own answer against the banked verdict -- so a stored verdict which
disagrees with its own inputs is a failure rather than the thing being
trusted. Nothing here is confirmed by a field agreeing with itself.

The two older files cannot answer the gate question at all. `n_fraction`
and the two `capacity_degraded` counters arrived at record version 3,
after both were harvested, so a check run over either reports its
job-runs as *unanswered* rather than as clean. That distinction is the
one a `.get(field, 0)` would quietly destroy, turning "nobody measured
this" into "nothing was wrong", most confidently about the oldest data;
`gate_withheld()` raises instead, and the test suite drives that path
with the real baseline records rather than a fixture.

**The ten records with no series are the Ansible modules job**, which
uploaded a bundle all along but carried no probe until the
`shakenfist/actions` change instrumenting it merged on 2026-10-01 --
after this window ran. They are kept rather than dropped, for the same
reason the baseline keeps its thirteen: the count of what could not be
measured belongs in the dataset. The other 40 are the four instrumented
entries of the merge matrix, ten runs each.

**Harvesting this needed a fix to the harvest tool, which is worth
knowing before harvesting any other old window.** The two Debian
under-cloud lanes were renamed from Debian 12 to Debian 13 on
2026-09-29, and the rename *replaced* their entries in
`BUNDLE_TOPOLOGIES` rather than adding to them. Since that table is
keyed by artifact bundle name, every window older than the rename
became unharvestable: the harvest dies on `UnknownBundleError` at the
first run it reaches. Both old names are now back in the table
alongside the new ones, and the table's comment says that a renamed
lane keeps its old entry until the last bundle carrying the old name
has expired. The `job` field of these records therefore reads `Debian
12 cluster` and `Debian 12 tier`, which is what the window measured.

**The boundaries are the plan's, not the convenient ones.** They are
the ones the arming harvest was run with, to the second, so this file
is the window the decision was made on rather than a window near it. A
harvest rounded out to whole days -- `--since 2026-09-21 --until
2026-09-25` -- enumerates 31 merge runs instead of 20 and would make a
larger, differently-shaped dataset, which could not be checked against
the plan's forty rows. If the wider window is ever wanted it belongs in
a file of its own.

**One normalisation, the same one.** `summary.series.path` and
`summary.census.path` were removed from every record, for the reason
given under *What was changed before committing*. Nothing else was
pruned. With those two fields taken out of both sides, a second harvest
of the same command reproduces this file record for record, which was
checked rather than assumed.

## Reproducing an analysis over these files

`tools/ci_headroom_check_window.py` is a worked example as well as a
check: it loads all three files, handles the version mix, re-derives the
band verdict and the gate conditions from the raw counters rather than
from the stored verdict, and prints the per-job table. Reading it is the
shortest route to a new analysis over this directory.

By hand, the datasets are deliberately plain: each line is one JSON
object and nothing needs to be joined. To recover, say, the
per-topology distribution of the cluster-wide committed-CPU p90
fraction:

```python
import json
records = [json.loads(line) for line in open('records.jsonl')]
usable = [r for r in records
          if r['summary'] and r['summary']['cluster']['committed_cpu']['n']]
tier = [r['summary']['cluster']['committed_cpu']['p90_fraction']
        for r in usable if r['topology'] == 'slim-tier']
```

Three figures in the master plan's *Situation* section are **not**
recomputable from this directory, and are named as such where they are
used. Two are not in the summary record at all: the
measured-versus-committed vCPU comparison, and the classification of
each `sufficient_idle_cpu` refusal by which of the two ledgers
actually refused. They come from `cpu_measured`/`cpu_committed` in the
raw series and from the `measured_cpus`/`committed_cpus` fields of the
refusal payloads in the raw census.

The third is in the record now but was not when the baseline was
harvested: the shape of the ledger-unreadable samples in
`records.jsonl`'s 204 job-runs -- a contiguous prefix, 9 to 14
samples, never mid-run. That classification was made by reading the
raw series. `records-addendum.jsonl` carries the counters which state
it, so the same claim about the confirmation window is recomputable
from the file, and a future re-measure will be too.
