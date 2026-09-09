# CI cloud sizing baseline dataset

The measured baseline for
[PLAN-ci-cloud-sizing.md](../../PLAN-ci-cloud-sizing.md), harvested in
step 2d of
[PLAN-ci-cloud-sizing-phase-02-baseline.md](../../PLAN-ci-cloud-sizing-phase-02-baseline.md)
(D16, D17, D22). Every figure in the master plan's *Situation* section
that is described as measured is traceable to a record in
`records.jsonl`.

There are **two** files here, with the same record schema and different
jobs to do. `records.jsonl` is the baseline: the retrospective window,
and the source of every distribution the plan reasons about.
`records-addendum.jsonl` is step 2g's confirmation window, harvested
after the two instrument fixes this phase made had merged, and it exists
to close the two things the baseline could not see. It is a
classification, not a second baseline, and no distribution should be
recomputed from it -- it is an eighth the size and covers a day and a
half.

The bundles this was computed from expire ninety days after their
merge run, so this directory is the only durable copy. The raw series
and census are deliberately *not* committed -- D22 -- only the per-job
summary records the report tool derives from them.

## The window

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
| `record_version` | Schema version of the report record. `records.jsonl` is **1**; `records-addendum.jsonl` is **2**, which adds the four series counters below. A consumer that reads both must expect the older shape. |
| `series` | Sample counts (usable, failed, unparseable), the window start/end/duration, `ledger_unreadable_samples`, and -- from record version 2 -- `ledger_unreadable_prefix_samples`, `ledger_unreadable_prefix_seconds`, `capacity_degraded_samples` and `capacity_degraded_absent_samples`. The last four are what say whether an unreadable ledger was a warm-up or a fault. |
| `ledger_provenance` | Node-samples with a real capacity row, with a fallback to `cpu_hard_max`, with a fallback inside an unreadable sample, and with no ledger at all. |
| `cluster` | Committed vCPU and committed memory MB, cluster-wide: `n`, `p90`, `peak`, `ledger_min`, `ledger_max`, and the two as fractions of ledger. |
| `per_node` | The same, keyed by node uuid, each carrying that node's own ledger. |
| `per_node_max_cpu_fraction` | Per sample, the highest committed-over-ledger ratio any one node stood at; then `p90` and `peak` over samples. This is D21's statistic. |
| `absences` | What the node roster named that `/admin/resources` did not return, classified. |
| `census` | Per-stage tally: events, aborts, drops, shortage drops and drop reasons, plus `capacity_shortage_drops`, `unclassified_shortage_drops`, `disk_bandwidth_drops` and `missing_data_drops`. |
| `guard` | The capacity guard census. In this window its `state` is `not_collected` on every record -- see below. |
| `verdict` | The D3 band verdict against the provisional 0.35/0.70 bounds, and the per-node maximum D21 adds. |

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

## Reproducing an analysis over this file

The dataset is deliberately plain: each line is one JSON object and
nothing needs to be joined. To recover, say, the per-topology
distribution of the cluster-wide committed-CPU p90 fraction:

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
