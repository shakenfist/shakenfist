# CI cloud sizing baseline dataset

The measured baseline for
[PLAN-ci-cloud-sizing.md](../../PLAN-ci-cloud-sizing.md), harvested in
step 2d of
[PLAN-ci-cloud-sizing-phase-02-baseline.md](../../PLAN-ci-cloud-sizing-phase-02-baseline.md)
(D16, D17, D22). Every figure in the master plan's *Situation* section
that is described as measured is traceable to a record in
`records.jsonl`.

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
python3 tools/ci_headroom_harvest.py --since 2026-08-30 \
    --cache-dir /var/tmp/ci-headroom-harvest-cache \
    --output records.jsonl
```

That took 331 `gh api` calls and downloaded 217 bundles totalling
**1.1 GB**, which took a little over two hours of wall clock. The
cache directory is keyed by artifact id and anything already in it
is reused, so a re-run is cheap; any writable directory outside the
repository will do, and the run above used a scratch directory
rather than the path shown. Re-running it today will produce a
longer file than this one, because the window grows with every merge
run -- `--since` fixes only the start.

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
| `series` | Sample counts (usable, failed, unparseable), the window start/end/duration, and `ledger_unreadable_samples`. |
| `ledger_provenance` | Node-samples with a real capacity row, with a fallback to `cpu_hard_max`, with a fallback inside an unreadable sample, and with no ledger at all. |
| `cluster` | Committed vCPU and committed memory MB, cluster-wide: `n`, `p90`, `peak`, `ledger_min`, `ledger_max`, and the two as fractions of ledger. |
| `per_node` | The same, keyed by node uuid, each carrying that node's own ledger. |
| `per_node_max_cpu_fraction` | Per sample, the highest committed-over-ledger ratio any one node stood at; then `p90` and `peak` over samples. This is D21's statistic. |
| `absences` | What the node roster named that `/admin/resources` did not return, classified. |
| `census` | Per-stage tally: events, aborts, drops, shortage drops and drop reasons, plus `capacity_shortage_drops`, `unclassified_shortage_drops`, `disk_bandwidth_drops` and `missing_data_drops`. |
| `guard` | The capacity guard census. In this window its `state` is `not_collected` on every record -- see below. |
| `verdict` | The D3 band verdict against the provisional 0.35/0.70 bounds, and the per-node maximum D21 adds. |

## One thing this dataset does not know

It is a blind spot of the retrospective window, and it is not zero.

**Capacity guard refusals are unknown.** The census filter in
`shakenfist/actions` matches the scheduler's per-candidate stage
events and nothing else, so neither `instance placement denied` nor
`placement admitted over namespace capacity claim` was ever collected.
`summary.guard.state` reads `not_collected` on all 204 summarised
records. That is a fact about the query, not about the cluster. Step
2e fixes the filter and step 2g re-measures; until then, no count of
guard refusals over this window exists, and none should be inferred
from the absence of one.

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
it* in the master plan. Step 2g now confirms this rather than deciding
it.

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

Two figures in the master plan's *Situation* section are **not** in
this file, because the summary record does not carry them, and are
recomputed from the bundles themselves rather than from here: the
measured-versus-committed vCPU comparison, and the classification of
each `sufficient_idle_cpu` refusal by which of the two ledgers
actually refused. Both come from `cpu_measured`/`cpu_committed` in the
raw series and from the `measured_cpus`/`committed_cpus` fields of the
refusal payloads in the raw census. Both are named as such where they
are used.
