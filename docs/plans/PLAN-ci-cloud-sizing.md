# Right-size the CI test clouds

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

This plan additionally spans the `shakenfist/actions`
repository, where the CI topologies and the reusable
`smoke-cluster` workflow live, and reads two external data
sources: GitHub Actions job and step timings (via `gh api` or
the `ci-status` helper), and the private-ci conductor's
per-instance resource samples. Both are described under
*Reproducing the measurements* below. Do not restate a number
from this document without checking it still holds -- the whole
point of the plan is that these numbers were never being
watched.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/scheduler.py` (the admission filters whose
arithmetic this plan is about), `shakenfist/mariadb.py`
(`_derive_cpu_memory_limits`, the capacity counters and the
demand guard), `shakenfist/config.py` (`CPU_OVERCOMMIT_RATIO`,
`NODE_CPU_RESERVATION_THREADS`), `examples/_shared/site.yml`
(where the per-host reservation defaults are computed at deploy
time) and `shakenfist/deploy/shakenfist_ci/` (the functional
suites the clouds exist to run).

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

The nested test clouds that merge-queue CI builds are sized by a
decision nobody recorded. Five hypervisors plus a primary, each
node 4 vCPU and 12 GB, is what `ci-topology-slim-primary.yml` has
always said. Until phase 1 there was no measurement either way, and
no mechanism that would notice if the sizing stopped being right.

There is now, and this section is written from it. Every figure
below is either measured, with the sample it was measured over
stated beside it, or marked as **not measured**. Nothing in it is
hand-collected without saying so.

The measured figures come from the phase 2 baseline, which is
committed under
[`docs/plans/data/ci-cloud-sizing-baseline/`](data/ci-cloud-sizing-baseline/README.md):
217 harvested records over **55 merge runs** between
2026-08-30T07:48:03Z and 2026-09-05T07:15:07Z, of which **204 carry
a usable committed-CPU series**. The harvest tool is
`tools/ci_headroom_harvest.py` and the exact command is in that
directory's README. Three caveats on the sample apply everywhere
below and are not repeated:

* Only **four** jobs carry the phase 1 probe (phase 2, D17):
  `Debian 12 cluster`, `Ubuntu 24.04 cluster` and `Guests` on
  `slim-primary`, and `Debian 12 tier` on `slim-tier`. `Node
  lifecycle` and `Ansible modules` are unmeasured, for the two
  different structural reasons D17 records.
* Roughly **10.6%** of samples (2,276 of 21,517) had every node's
  capacity row read as absent at once, and are excluded from every
  committed-CPU figure. This is why an `n` quoted in samples is
  smaller than the sample count. Whether those are failed reads or
  an unpopulated table is **not known** for this window; phase 2's
  D19 publishes the flag that answers it, prospectively.
* Capacity **guard** refusals were never collected in this window,
  because the census filter did not match their messages (phase 2,
  D20). They are **unknown**, not zero, everywhere below.

Two sources remain hand-collected because this instrument cannot
reach them: the private-ci conductor's per-instance CPU and memory
samples, and the under-cloud's own capacity. Both are labelled
where they are used.

### What actually binds is the admission ledger

The scheduler admits a create only if
`max(measured, committed) + requested <= cpu_schedulable x CPU_OVERCOMMIT_RATIO`
(`Scheduler._has_sufficient_cpu`, `shakenfist/scheduler.py`), and
the resources daemon publishes
`cpu_schedulable = max(1, threads - reservation)`
(`shakenfist/daemons/resources/main.py`). `examples/_shared/site.yml`
defaults that reservation to **2 threads, or 4 on a network or
database node**.

On a 4 vCPU CI node that is 2 schedulable threads, so a ledger of
**6 admitted vCPU**; on a 4 vCPU *network* node it floors at 1
thread, so **3**. The reservation is a fixed per-node tax, which
makes small nodes disproportionately expensive: a 4 vCPU node
gives away half its threads, a 6 vCPU node a third.

| Topology | VMs | Under-cloud cost | Inner ledger | Measured over |
|----------|-----|------------------|--------------|---------------|
| `slim-primary` | 6 (1 database + 5 hypervisors) | 24 vCPU / 64 GB | **27 vCPU** (1x3 + 4x6) | 154 job-runs, every one |
| `slim-tier` | 3 (all hypervisors) | 12 vCPU / 36 GB | **12 vCPU** (3+3+6) | 50 job-runs, every one |

Those ledgers are not derived on paper. Across the whole baseline
window the cluster ledger read exactly 27.0 in all 154
`slim-primary` job-runs and exactly 12.0 in all 50 `slim-tier`
job-runs, with no job-run in which the figure moved during the run;
the per-node limits were 6.0 in 616 `slim-primary` node-job-runs
and 3.0 in the other 154, and 3.0 in 100 `slim-tier` node-job-runs
and 6.0 in the other 50. The under-cloud cost column is **not
measured** -- it is read off the topology files.

This document previously recorded the tier's ledger as "12 by
derivation, 10 by observation", on the strength of issue #3907
reporting the `cluster_capacity` singleton at `limit 10`. **That
discrepancy no longer exists and the tier's ledger is 12.** #3907
closed COMPLETED on 2026-08-27, and its final recurrence comment
quotes the singleton itself refusing a claim with `cpus (limit 12,
used 9, requested 4)`. Phase 0's D7 asked phase 2 to confirm the
fallback count across a whole window, because a node without a
capacity row would change what the ledger column means. It is
confirmed: of **85,563** node-samples whose ledger was readable,
**zero** fell back to `cpu_hard_max` and **zero** had no ledger at
all. Every node in both topologies carried a real capacity row for
the entire window. D7 is closed.

The one qualification is that the singleton's own total is not
published by `/admin/resources` -- `summarize_resources()` builds
`total` from per-node arithmetic only -- so the 12 above is the sum
of the rows, and the singleton is cited from #3907 rather than
measured here.

### The three-node topology is demonstrably too small

Over the 66 merge runs the baseline harvest enumerated, counting
only the ones in which a job actually ran:

| Job | Topology | Passed | Instrumented |
|-----|----------|--------|--------------|
| Ansible modules | `slim-primary` | 53 / 55 (96%) | no |
| Node lifecycle | `slim-primary` | 51 / 54 (94%) | no |
| Guests | `slim-primary` | 47 / 54 (87%) | yes |
| Ubuntu 24.04 cluster | `slim-primary` | 43 / 54 (80%) | yes |
| Debian 12 cluster | `slim-primary` | 42 / 54 (78%) | yes |
| **Debian 12 tier** | **`slim-tier`** | **13 / 54 (24%)** | **yes** |

Only 14 of the 66 merge groups merged (21%). This table previously
held five rows measured over a different, earlier window of 98 runs;
it now holds the same six jobs a merge run actually builds, measured
over the window the rest of this section is measured over, and the
ordering and the spread are unchanged. `Ansible modules` was missing
from it before, which is also why phase 2's D17 describes it as a
six-job table when it had five rows.

The tier's failures are not spread across causes. That was
established from sampled logs -- 69 of the earlier window's tier
failures died in the `Run functional tests` step, showing `507 No
nodes remaining at scheduling stage sufficient_idle_cpu` with
`current_cpus: 3` against `limit_cpus: 3`, nodes genuinely at their
ledger. **The step attribution is not re-measured here**, but the
refusal itself now is: `sufficient_idle_cpu` aborted in 35 of the
tier's 50 instrumented job-runs, and 33 of those 35 failed. This is
the same failure family as the #3772 umbrella and #3907.

One claim in the same paragraph does **not** survive measurement.
This document previously said the sampled `slim-primary` failures
"contain no such refusal at all". They do. Across 154 instrumented
`slim-primary` job-runs the CPU stage dropped 676 candidates in 110
of them, and aborted outright in 14. `slim-primary` refuses work
routinely; it just has four other hypervisors to fall through to,
which is exactly the difference the per-node subsection below is
about.

Two things this is *not*:

- It is not slowness. The functional-test step's probe window --
  which brackets the suite itself, not the deploy -- has a median of
  **28.8 minutes** on the tier (n=50) against **29.3** for the
  Debian 12 cluster (n=52) and **25.0** for Ubuntu (n=51). The tier
  is, if anything, marginally the faster of the two Debian jobs.
  Within the tier, failing job-runs are not longer than passing ones
  (28.8 against 29.3 minutes). The `test_timeout_minutes: 70` and
  the comment in `functional-tests.yml` claiming the tier "runs
  slower" are not borne out.
- It is not the #3813 demand guard, which was fixed on 2026-08-22.
  This claim is **not re-measured**: the baseline window lies
  entirely after that fix, so it cannot split on it. The earlier
  hand-collected window put the tier at 17% before and 23% after,
  unchanged within noise, and the 24% measured here is consistent
  with that.

### How full the clouds actually get

Committed vCPU as a fraction of the cluster ledger, one figure per
job-run, `n` in job-runs. `p90` and `peak` are computed within a
job-run across its samples; the columns then report the median and
the extremes of those per-run figures across the window.

| Job | Topology | n | p90 fraction (med / max) | peak fraction (med / max) | peak vCPU (med / max) |
|-----|----------|---|--------------------------|---------------------------|-----------------------|
| Guests | `slim-primary` | 51 | 0.296 / 0.370 | 0.370 / 0.519 | 10 / 14 |
| Debian 12 cluster | `slim-primary` | 52 | 0.333 / 0.444 | 0.444 / 0.593 | 12 / 16 |
| Ubuntu 24.04 cluster | `slim-primary` | 51 | 0.333 / 0.519 | 0.444 / 0.593 | 12 / 16 |
| **Debian 12 tier** | **`slim-tier`** | **50** | **0.750 / 1.000** | **0.917 / 1.000** | **11 / 12** |

Pooled by topology: `slim-primary` (n=154) has a median p90 fraction
of 0.333 and **never once exceeded 0.519** in 154 job-runs;
`slim-tier` (n=50) has a median of 0.750 and **never once fell below
0.500**. The two distributions overlap only in the interval
[0.500, 0.519].

The three `slim-primary` jobs differ from each other by less than
they differ from the tier, which is the answer to the question D17
was written to make askable: the gap is the *shape*, not the suite.
`Guests` is the lightest of the three and `Debian 12 cluster` and
`Ubuntu 24.04 cluster` are indistinguishable.

The tier reached a cluster-wide p90 of **1.000** in one job-run
(33613379424, failed): every node at its ledger for ninety percent
of the run.

The window is stationary. Splitting each job's records into an
earlier and a later half and comparing shifts nothing at any
conventional threshold (smallest p = 0.08, on the per-node maximum
for `Guests`), so the 55 head SHAs the window spans did not move the
numbers.

### The cluster-wide figure hides the node that refuses

This is D21, and the pattern the phase 2 survey saw in two runs
**holds across the window**.

The scheduler admits against one node's ledger at a time and never
against an average, so the statistic that matters is the highest
committed-over-ledger ratio any single node stood at. Per job-run,
that per-node maximum exceeds the cluster-wide figure by a median
of **0.412** and a median *ratio* of **2.25x** (n=204, max 3.38x).

| Topology | n | job-runs with a node at 1.000 (peak) | with a node at 1.000 (p90) |
|----------|---|--------------------------------------|-----------------------------|
| `slim-primary` | 154 | 100 (65%) | 74 (48%) |
| `slim-tier` | 50 | 50 (100%) | 50 (100%) |

Read cluster-wide, `slim-primary` looks comfortable at a median p90
of 0.333. Read per node, **two out of three of its job-runs contain
a node that was completely full**, and half contain one that was
full for ninety percent of the run. On the tier every single
job-run does.

It is disproportionately, but not only, the small node.
`slim-primary`'s 3 vCPU network node peaked at or above its ledger
in 68 of 154 node-job-runs (44%); its four 6 vCPU nodes did so in
59 of 616 (10%).

**D21 is defended, with one correction that matters for phase 5.**
The per-node maximum is the right statistic and the cluster-wide
one is genuinely misleading -- but the per-node maximum is
*saturated*, sitting at its ceiling in 48% of `slim-primary` and
100% of `slim-tier` job-runs, including plenty that passed. It
cannot discriminate a bad run from a good one at the top of its
range; all of its information is in its lower tail. A per-node band
has to be read as a statement about what a topology *should*
achieve, not as a per-run alarm the current clouds could pass.

### Does utilisation predict the failure?

This is the central claim of this plan, and it is now testable. The
answer is more precise than the claim was, and it is partly a null
result.

**What this is computed over.** 204 job-runs of **four** jobs:
`Debian 12 cluster`, `Ubuntu 24.04 cluster`, `Guests` and `Debian 12
tier`. It is **not** computed over the six-job table above. `Node
lifecycle`, the best performer in that table at 94%, carries no
probe, so no claim of the form "utilisation explains the pass-rate
spread across the fleet" is made or supportable here.

Pooled across all four jobs, failing job-runs are much fuller than
passing ones: a median cluster-wide p90 fraction of 0.667 against
0.333 (Mann-Whitney, p < 0.001). **That comparison is confounded
and should not be quoted.** Three of the four jobs share a topology,
and the fourth -- the tier -- is simultaneously the fullest cloud
and the one that fails three times out of four, so the pooled test
is measuring the topology, not the utilisation.

Within each job, where the topology and the suite are held fixed,
the cluster-wide committed fraction separates passing from failing
job-runs **not at all**:

| Job | pass / fail n | median p90 fraction, pass vs fail | p |
|-----|---------------|-----------------------------------|---|
| Debian 12 cluster | 39 / 12 | 0.333 vs 0.370 | 0.27 |
| Ubuntu 24.04 cluster | 40 / 11 | 0.333 vs 0.370 | 0.59 |
| Guests | 44 / 7 | 0.296 vs 0.296 | 0.92 |
| Debian 12 tier | 13 / 37 | 0.750 vs 0.750 | 0.74 |

The per-node maximum does no better within a job (smallest p =
0.09), for the saturation reason above.

What *does* separate them, in every job and strongly, is whether the
scheduler ran out of candidates at all -- a `sufficient_idle_cpu`
**abort**, which is the event a 507 is raised from, as opposed to a
drop that merely removed one node from a list which still had
others:

| Job | job-runs with an abort | of those, failed | of those without, failed |
|-----|------------------------|------------------|--------------------------|
| Debian 12 cluster | 6 / 52 | 4 (67%) | 8 / 46 (17%) |
| Ubuntu 24.04 cluster | 1 / 51 | 1 (100%) | 10 / 50 (20%) |
| Guests | 7 / 51 | 6 (86%) | 1 / 44 (2%) |
| Debian 12 tier | 35 / 50 | 33 (94%) | 4 / 15 (27%) |
| **All four** | **49 / 204** | **44 (90%)** | **23 / 155 (15%)** |

So the honest form of this plan's central claim is: **how full a
cloud gets does not predict whether its job fails; the cloud
actually running out does, overwhelmingly.** The two are not the
same statement. The first would say the tier, at a median p90 of
0.75, is at risk throughout; the measurement says a tier job-run
which never aborted passed 11 times out of 15, and one which aborted
failed 33 times out of 35. What sizing has to move is the abort
rate, and utilisation is a lagging proxy for it -- useful for
choosing a shape, useless as a per-run verdict.

### What the refusals say, per stage

Aggregated over the 204 job-runs, counting the four capacity stages
separately as phase 0's D5 requires. `sufficient_free_disk` is disk
*space*; `sufficient_idle_disk` is disk *bandwidth*, a rate
predicate that no amount of sizing can address. They are never
merged.

| Stage | `slim-primary` evaluations / drops | `slim-tier` evaluations / drops |
|-------|-----------------------------------|--------------------------------|
| `sufficient_idle_cpu` | 16,421 / **676** | 6,749 / **1,189** |
| `sufficient_idle_memory` | 16,407 / 0 | 6,684 / 0 |
| `sufficient_free_disk` | 16,407 / 0 | 6,684 / 0 |
| `sufficient_idle_disk` | 16,330 / 0 | 6,647 / 0 |

Every capacity refusal in the entire window happened at
`sufficient_idle_cpu`, and every one of the 1,865 of them gave the
reason `would exceed hard max CPUs`. Memory, disk space and disk
bandwidth refused **nothing at all** in 23,091, 23,091 and 22,977
evaluations respectively. Aborts, again only at CPU: 14 across 154
`slim-primary` job-runs, 65 across 50 `slim-tier` job-runs.

Two non-capacity stages are worth recording so that nobody counts
them as capacity later. `is_hypervisor` dropped 7,326 candidates on
`slim-primary` and **none** on `slim-tier`: `slim-primary` carries a
node the roster names and `/admin/resources` reports as not a
hypervisor, in all 154 of its job-runs, and the tier's three nodes
are all hypervisors. That is structural, not scarcity. `affinity_constraints` aborted in 114
job-runs with the reason `no co-located instance carries a required
tag`, which is `test_affinity` reaching its documented skip after
the 2026-09-01 correction below, not a capacity event.

Capacity **guard** refusals -- `instance placement denied` and
`placement admitted over namespace capacity claim` -- are
**unknown** for this window. The census query never matched them.
This is a hole in the measurement, not a zero.

### Memory does not bind, but is not spare either

Phase 0's D5 decided memory was "a real second dimension" on one
observed `sufficient_idle_memory` refusal. **That rationale does not
survive the window: memory refused nothing in 23,091 stage
evaluations across 204 job-runs.** Not one drop, on either topology,
in either direction.

The reason is arithmetic. A node's memory ledger is
`memory_max x RAM_OVERCOMMIT_RATIO`, and that ratio defaults to
**3.0**, so a 12 GB CI node carries a 35,880 MB ledger over 11,960 MB
of physical RAM. Committed memory against that ledger looks tiny --
a median per-job-run p90 of 0.125 on `slim-primary` (n=154) and
0.216 on the tier (n=50). Against the node's *physical* RAM it does
not:

Each node's own p90 committed vRAM, summarised across node-job-runs
(770 on `slim-primary`, 150 on the tier):

| Topology | per-node p90 committed vRAM (med / p90 / max) | as a share of that node's physical RAM |
|----------|-----------------------------------------------|----------------------------------------|
| `slim-primary` | 5,120 / 8,028 / 9,052 MB | 43% / 67% / 76% |
| `slim-tier` | 8,028 / 9,052 / 12,124 MB | 67% / 76% / 101% |

The tier's worst node committed **more vRAM than it physically
has**, which the 3x overcommit permits. That is committed
allocation, not memory in use; the conductor's hand-collected
7.5-10.2 GB actually in use on a tier node sits below it, as it
should.

**So D5 narrows, and its operative clause survives.** Memory is not
a binding *admission* dimension in either current shape and phase 3
does not need a memory dimension in its saturation test. But per-node
RAM is not headroom to reclaim: the measured per-node p90 commitment
is 8.0 GB on `slim-primary` and 9.1 GB on the tier, and D5's rule
that no topology drops per-node RAM below the measured p90 plus a
margin now has those numbers behind it.

### Allocation is roughly double actual usage

This subsection has two sources and they measure different things.

**Hand-collected, and not re-measured here.** From the conductor's
per-instance samples (`workflow_cost_samples`, filtered to
`is_runner = 0`, n ~ 550 VMs per job), gathered 2026-08-27:

- **CPU**: a 4 vCPU cluster VM averages **0.71 cores** across a
  `slim-primary` run (p90 1.03, max 1.46) -- about 18% of its
  allocation. The same VM in the tier averages 1.22 cores.
- **Memory**: peak memory in use on a 12 GB node is
  **4.9-7.6 GB** on `slim-primary` and **7.5-10.2 GB** on the
  tier. Swap-out is **zero on every node of every job**.
- The 4 GB primary in `slim-primary` is the tightest node in the
  fleet at 3.9 GB in use. It never swaps, but it has no slack.

The probe cannot reproduce these: it publishes vCPU counts, not
core-seconds. They are the reason to believe real CPU is idle, and
they remain the only such evidence.

**Measured, from the baseline bundles.** What the probe *can*
answer is which of the scheduler's two ledgers refuses. Over 85,563
ledgered node-samples, summed committed vCPU is only **1.04x**
summed measured vCPU -- the capacity counters charge barely more
than the running-domain census does. Committed exceeded measured in
12.4% of node-samples and measured exceeded committed in 10.3%. Of
the 1,965 `sufficient_idle_cpu` refusals whose payload could be
classified, **30.6% would have been admitted on the measurement
alone**, 27.4% would have been admitted on the counters alone, and
42.0% were refused by both. In 15.1% the node measured *exactly
zero* running vCPU at the moment it refused.

(Both of these are computed from the raw series and census inside
the bundles rather than from the committed summary records, which do
not carry the underlying fields. The 1,965 exceeds the 1,865 counted
in the stage table above because it is taken over all 217 bundles,
including the thirteen whose series was absent but whose census was
not.)

Read together, the finding of this subsection is unchanged but
sharper. What runs out is an allocation figure and not real CPU --
both `measured` and `committed` count vCPU, and the conductor says
those vCPU are 82% idle. But it is **not** specifically the
capacity counters: nearly seven refusals in ten would have happened
without them, from the running-domain count alone. Both are compared
against the same `cpu_schedulable x CPU_OVERCOMMIT_RATIO`, so a
wider node relieves both, and no fix to the counters would relieve
either.

### A node can record twice its own ledger, and sizing would hide it

The instrument found one thing the plan was not looking for. A
capacity row can record a node as committing more vCPU than its own
limit allows. Because `_has_sufficient_cpu()` admits on
`max(measured, committed) + requested <= limit`, a node in that
state refuses **every** subsequent create no matter how idle it is,
and the cluster silently loses a hypervisor. That is the #3772 507
signature arrived at from the counters rather than from real
exhaustion, and it is a defect a bigger cloud would mask rather than
fix.

Quantified over the window, it is **real, reproducible, and rare**:

- **18 of 85,563** ledgered node-samples (0.021%) recorded a node
  above its own limit.
- All 18 fall in **2 of 204** job-runs (1.0%), both `Debian 12 tier`
  on `slim-tier`. The node's limit did not move in either run.
- Run 33752413862 (2026-09-03): node `750651c4`, limit 3.0
  throughout, committed p90 **6.0** and peak **7.0** -- a fraction
  of 2.0 and 2.33, held for most of the run. **That job passed.**
- Run 33948911843 (2026-09-05): node `2060c55b`, limit 3.0
  throughout, committed peak **6.0**. That job failed.
- 3 of the 18 samples had `cpu_measured` of exactly zero.

It is therefore not a driver of the 507 family's *frequency*: at one
percent of job-runs it cannot explain a 76% tier failure rate, and
the phase 2 survey's expectation that it might be the most
decision-relevant number in the dataset is not borne out. It remains
a real defect, and phase 2 files it rather than fixing it.

**Step 2f established the mechanism, and it is not a breach of the
guarded UPDATE.** All 18 node-samples are the *first* samples after
the capacity rows appear, and the journals put the reconciler's
first pass between the last no-row sample and the first over-limit
one, to within seconds, in both runs. `scheduler_node_capacity` has
no rows until that pass, which is registered
`schedule.every(5).minutes` (`daemons/cluster/main.py:750`) and so
first runs five minutes after the cluster daemon starts. Until then
`admit_instance_placement()` takes P7's fail-open branch on every
node at once -- it writes the placement and the `instance_location`
row and touches no counter -- and the CPU pre-filter charges
`committed_cpus` zero, leaving only `cpu_total_instance_vcpus`,
which counts *running* domains and republishes once a minute. So a
burst of concurrent creates that are still fetching images all
measure zero and all land. The first reconcile pass then recomputes
`used_cpus` from those reference rows and faithfully writes 6 or 7
onto a row whose `limit_cpus` is 3. The counter is a correct reading
of an incorrect placement.

That window is universal, not rare: in **all 204** job-runs the
capacity table is empty for a contiguous prefix of **135 to 210
seconds** (median 165), and in **176 of 204 (86%)** instances were
already running before any capacity row existed. What is rare is the
overshoot, and its rarity is the ledger's size -- both occurrences
are `slim-tier`, whose network node has a `limit_cpus` of 3.
Ruled out with the same evidence: the P5 forced ground-truth write
(its `placement recorded despite exceeding capacity guard` event
appears in neither bundle), a lowered limit, `_reconcile_placement()`'s
documented restart overcount, and a mid-run loss of the rows.

### The under-cloud budget this spends

**Not measured by this instrument.** These figures are read from
`sfcbr`'s own capacity and from the topology files, and the probe
runs inside the nested clouds rather than the under-cloud.

`sfcbr` publishes a 234 vCPU admission ledger across six
hypervisors and has 376 GB of physical RAM. One merge run builds
**six** nested clouds (`Debian 12 cluster`, `Ubuntu 24.04
cluster`, `Guests`, `Ansible modules` and `Node lifecycle` on
`slim-primary`; `Debian 12 tier` on `slim-tier`), so it allocates
about 132 vCPU and 356 GB of guest RAM, plus a 1 vCPU / 2 GB
runner apiece.

That is ~95% of the under-cloud's physical memory for a single
merge run, which is why the queue is throttled to two parallel
builds and why contention shows up as unrelated-looking flakes.
**RAM, not vCPU, is the scarce under-cloud resource**, and the CI
clouds are the largest consumer of it. Closing the loop with the
measured figures above: an inner node's committed vRAM reaches
43-76% of its *physical* RAM on `slim-primary` and 67-101% on the
tier, so that 356 GB is not obviously over-provisioned per node --
any saving has to come from having fewer nodes, which is what phase
4 will weigh.

### The headroom band, with numbers

Phase 0's D3 fixed the band's *form* and left its numbers to this
phase, with provisional bounds of 0.35 and 0.70. Phase 2's D21 added
a per-node component. Both are now set from the distribution.

**Cluster-wide upper bound: keep 0.70.** In 154 `slim-primary`
job-runs the cluster-wide p90 fraction never reached it -- the
maximum observed is 0.519 -- and 37 of 50 `slim-tier` job-runs
exceed it. It separates the cloud this plan agrees is too small from
the ones it does not, with no false positives in the window.

**Cluster-wide lower bound: 0.35 is numerically right and
operationally awkward.** 104 of 154 `slim-primary` job-runs fall
below it, and none of the tier's do. That is the correct *finding*
-- `slim-primary` really is running at a third of its ledger -- but
a per-run warning that fires on two runs in three is noise. The
number stands; phase 5 has to decide whether the lower bound is
evaluated per run or against a job's median across a window, and
this phase does not decide that for it.

**Per-node upper bound: 0.85, as a first proposal.** Taking the
p90 of the per-node maximum, job-runs which recorded *no* capacity
refusal at all (n=44) sit at a median of 0.667 and exceed 0.85 in
only **1 of 44** cases; job-runs which recorded at least one
(n=160) sit at a median of 1.000 and exceed 0.85 in **123 of 160**.
That is the cleanest separation any statistic in the dataset
achieves, and 0.85 is where it falls. It fires on 48% of
`slim-primary` and 100% of `slim-tier` job-runs today, which is the
point of proposing it: the cluster-wide figure says those clouds
are fine and they are not.

**No per-node lower bound is proposable from this window.** The
statistic is saturated at the top, so its distribution says nothing
about what "too empty per node" would look like.

### What the baseline does not know

One thing, stated once more because it is easy to read as zero and
is not:

* **Capacity guard refusals: unknown.** Not collected in this
  window, because the census filter matched only the scheduler's
  stage events. Phase 2's D20 fixes it and phase 2's step 2g
  re-measures over a short post-fix window.

The second entry that stood here -- the ledger-unreadable samples --
**is now known**, and step 2f answered it from the shape of the data
rather than from D19's flag. 2,276 of 21,517 usable samples (10.6%),
in every one of the 204 job-runs, have every node's capacity row read
as absent at once; every committed-CPU figure above excludes them. In
**every** job-run those samples are exactly a contiguous *prefix* of
the series, 9 to 14 samples long, ending at the reconciler's first
pass -- never mid-run, never scattered, never twice. A failed gRPC
read is an independent per-sample event and would not land only on
the head of 204 independent job-runs. So this is an **unpopulated
table during cluster warm-up, not a failing read**, and there is no
read-reliability defect to file.

What the window does expose is the other side of the same fact: for
those 135 to 210 seconds the admission guard does not exist, which is
where the 18 refusal payloads that fired with `capacity_row_present`
false come from, and which is #4087, recorded in *Bugs fixed
during this work* below. Step 2g should confirm the classification
prospectively once D19's flag is running -- `capacity_degraded`
should read false throughout the prefix -- but that is a
confirmation, not an open question.

### Reproducing the measurements

The measured figures now come from a tool we own, which is what
this plan existed to fix. The hand-collected ones still do not.

- **The baseline itself**: `tools/ci_headroom_harvest.py`, with the
  exact command and window in
  [`docs/plans/data/ci-cloud-sizing-baseline/README.md`](data/ci-cloud-sizing-baseline/README.md).
  The dataset in that directory is the source for every figure above
  marked as measured, except the two named as coming from the raw
  bundles.
- **A single job's own numbers**: `tools/ci_headroom_report.py`
  against the `traces/headroom.jsonl` and
  `traces/headroom-census.json` in that job's bundle, or `--json` for
  the machine-readable record the harvest consumes. Every cluster job
  also prints the prose summary into its own log.
- **Job pass rates**: `gh api
  "repos/shakenfist/shakenfist/actions/workflows/functional-tests.yml/runs?event=merge_group"`
  then `.../runs/<id>/jobs`. The job names the API returns are not
  the matrix names -- the reusable workflow contributes its own, so
  `Debian 12 cluster` arrives as `Debian 12 cluster (collection) /
  Smoke tests (collection)`.
- **Ledger and refusal evidence for one run**: `ci-status
  shakenfist/shakenfist logs <job id>`, then grep for
  `cpu_schedulable`, `limit_cpus` and `No nodes remaining at
  scheduling stage`. The scheduler's per-candidate audit payload is
  written on *every* schedule, not only failing ones:
  `_log_and_raise_on_error()` emits `schedule at stage {stage}`
  carrying `extra['dropped']` whenever candidates survived, and
  `schedule has no candidates at stage {stage}, aborting` when none
  did. A green run therefore does record its refusals.
- **Per-instance utilisation (hand-collected)**: the conductor
  database, copied as described in the private-ci access notes; join
  `workflow_cost_samples` to `workflow_costs` on `namespace` and
  filter `is_runner = 0`. The conductor's own published *sizing
  recommendations* remain untrustworthy (they aggregate a whole
  nested cloud into the runner's namespace and read a guest's RSS
  as its working set); the raw per-instance samples used here are
  not affected by that, because they are not aggregated.
- **Live under-cloud capacity (hand-collected)**: `GET
  /admin/resources` on `sfcbr`, which is also the endpoint phase 1
  makes CI sample from inside each nested cloud.

## Mission and problem statement

Make the size of a CI test cloud a measured, continuously-checked
property rather than a historical accident, and re-shape the
topologies so that every job has enough admission ledger for its
assertions to mean what they say -- at lower under-cloud memory
cost than today.

The problem has three parts, and they must be solved in this
order:

1. **We cannot see headroom.** No CI job records how full its
   cloud got. A topology can drift into being too small, or stay
   twice as large as it needs to be, and the only symptom is a
   flake attributed to whichever test drew the short straw. That
   ambiguity is what turned one capacity shortage into six
   per-test issues before #3772 unified them.
2. **We would lose coverage by growing.** A cloud that is too
   small exercises the system's behaviour under exhaustion for
   free. Some of what it catches are real defects -- #3772's own
   verdict is that "nothing in the system, server or client,
   treats 'no capacity right now' as the transient condition it
   is". Growing the clouds would silence that without fixing it.
3. **The shape is wrong before the count is.** Fewer, wider nodes
   buy more ledger for the same spend, and 12 GB per node buys
   nothing at all on a five-hypervisor topology.

### Yes, the tier should get bigger -- with a condition

`slim-tier` exists to give multi-instance `sf-database` coverage:
two `database_node` members, and `test_database_tier` asserting
both see a share of inbound gRPC. Being the smallest cloud in the
fleet is incidental to that purpose, and at a 19% pass rate it is
not delivering the coverage it was built for -- a job that fails
four runs in five teaches the reader to ignore it, and every
merge it ejects costs a full rebuild of six clouds.

Spending more per run to lower the failure rate is the right call
here, and it is cheap: three 6 vCPU nodes double the tier's ledger
to 24 for **+6 vCPU and no extra RAM**. But it is only defensible
with a condition attached, so this plan makes that condition
structural rather than a promise:

> **Phase 3 must land before phase 4.** Every failure signature
> the small clouds currently produce is either (a) reproduced by
> an explicit test that fills a cluster deliberately and asserts
> the documented behaviour, or (b) written down as a known defect
> with an open issue, before any topology grows.

Concretely, the coverage at risk is: the `507
sufficient_idle_cpu` path itself (#3772); the claim-admission
refusal path (#3907, now tolerated by a retry wrapper); and
`test_affinity`, which is the most frequent `slim-primary` failure
and whose signature -- "instances that should share a node do
not" -- is exactly what a full cluster produces when affinity
loses to load ordering (#3565). Growing the cloud will make that
test greener without anyone having decided whether the scheduler
was right. That is the lemon squeezed too far, and phase 3 is
where we stop squeezing and start asserting.

That "greener" claim needs a caveat, though. A 2026-08-26 comment
on #3565 corrects the earlier diagnosis on the same run: the
affinity target node survived `sufficient_idle_cpu` and was then
dropped at `sufficient_idle_memory`, so the most recent
fully-traced occurrence is memory-bound, not CPU-bound. The
candidate shapes below buy their RAM saving by consolidating onto
fewer, larger hypervisors, which raises instances per node even
with per-node RAM unchanged -- so the same reshape can relax the
CPU filter while tightening the memory one. Whether the test
actually gets greener is therefore not settled by this plan; it is
one more thing phase 2's per-stage refusal counts must show.

*Those counts are now in, and they answer the memory half of it:*
across 204 instrumented job-runs the `sufficient_idle_memory` stage
was evaluated 23,091 times and dropped **nothing**, on either
topology -- see *What the refusals say, per stage* above. The
tightening this paragraph worried about is not visible in the
current shapes; whether a reshape creates it is phase 4's to check
against the same counts.

*Correction (2026-09-01), from scheduler-reservations phase 6:*
the worry above is discharged, and `test_affinity` should be
dropped from the corpus of signatures this plan has to worry
about masking. It no longer asserts co-location at all. #3565's
disposition established that the traced failure had the candidate
set collapsing to a single node before affinity was scored, so the
test was asserting a guarantee the product never made; it now
asserts that the scheduler *scored* the affine node highest among
the candidates it had, and **skips** when the affine node was
ejected by an admission filter before scoring. That skip is
exactly the condition a small cloud produces, so growing the
cloud can no longer turn this test green by hiding the question --
the question has been answered, and the test reports "no
information" rather than passing. #3772 and #3907 are unaffected
and remain the live masking risk.

### Candidate shapes

Illustrative only. Phase 2 has now supplied the peak-demand figure
-- *How full the clouds actually get* and *The cluster-wide figure
hides the node that refuses* above -- and deliberately does **not**
turn it into a proposal. Phase 4 chooses a shape, and phase 3 gates
phase 4.

| Topology | Ledger | vCPU | RAM GB |
|----------|--------|------|--------|
| `slim-primary` today | 27 | 24 | 64 |
| `slim-tier` today | ~12 | 12 | 36 |
| primary as 4 x 5 vCPU plus a database node | 30 | 24 | 54 |
| primary as 3 x 6 vCPU plus a database node | 30 | 22 | 42 |
| tier as 3 x 6 vCPU | 24 | 18 | 36 |
| tier as 3 x 8 vCPU | 42 | 24 | 36 |

A plausible landing point -- `slim-primary` at four 5 vCPU
hypervisors with the database node bumped to 6 GB, `slim-tier` at
three 6 vCPU nodes -- costs a merge run **+6 vCPU and -50 GB of
RAM**, raises every cloud's ledger, and spends the saving on the
resource the under-cloud actually runs out of.

## Open questions

These were the phase 0 agenda, and phase 0 has answered them.
Each is kept with its reasoning intact and a pointer to the
decision that settled it, in
[PLAN-ci-cloud-sizing-phase-00-decisions.md](PLAN-ci-cloud-sizing-phase-00-decisions.md);
two of the six had a premise the phase 0 survey corrected, and
those are corrected here as well.

1. **Widen the nodes, or lower the reservation?**
   `examples/_shared/site.yml` honours a pre-set
   `node_cpu_reservation_threads`, so lowering it to 1 would gain
   50% more ledger. It is not free, though: CI's inventory is
   generated by `tools/ci-make-inventory.py` in
   `shakenfist/actions`, whose `render_node_vars()` emits a fixed
   block with no hook for arbitrary host vars, so the override
   needs a generator change or a cluster-wide `--extra-vars`. The
   deeper cost is that CI would stop exercising the production
   reservation arithmetic, which is precisely where #3813 lived.
   **Decided in phase 0 as D1: widen the nodes, keep the default.**
2. **What is `slim-tier` for?** If it is `sf-database` tier
   coverage, it should be sized for parity and stop being the
   scarcity topology. If it is deliberately the small one, that
   should be written down and its suite trimmed to what a small
   cloud can run.
   **Decided in phase 0 as D2: it is database-tier coverage, sized
   for parity.**
3. **What is the right headroom band?** Proposed starting point:
   peak committed vCPU at or below 70% of ledger at p90 across a
   run, and zero `sufficient_idle_cpu` refusals in a green run.
   Phase 2 supplies the distribution that makes those numbers
   honest, or replaces them. **Decided in phase 0 as D3: the form
   now, the numbers in phase 2.** *Answered by the phase 2
   baseline: the cluster-wide upper bound of 0.70 is defended, the
   lower bound of 0.35 is kept with an open question about how it
   is evaluated, and a per-node upper bound of 0.85 is proposed for
   the first time -- see* The headroom band, with numbers *above.*
4. **Does anything still need five hypervisors?**
   `nodelifecycletests.sh` needs a script host, a network node and
   two distinct victims, and `functional-tests.yml` hardcodes
   `10.0.0.20`-`10.0.0.24` when picking a random upload target.
   That job may keep its current shape while the others shrink.
   **Decided in phase 0 as D4: it keeps five hypervisors; the
   hardcoded IP list goes anyway.**
5. **Is memory a second binding constraint?** One post-#3813 run
   refused with `sufficient_idle_memory`, which `_has_sufficient_ram`
   measures against live available memory rather than against a
   ledger. The suspicion that page cache inflates that denominator
   does not survive checking: the resources daemon publishes
   `psutil.virtual_memory().available`, which is `MemAvailable` and
   already excludes reclaimable cache, so the refusal was honest.
   **Decided in phase 0 as D5: memory is a real second dimension.**
   *Narrowed by the phase 2 baseline: memory refused nothing in
   23,091 stage evaluations across 204 job-runs, so it is not a
   binding admission dimension in either current shape. D5's
   operative clause survives and now has numbers -- committed vRAM
   already reaches 76% of a `slim-primary` node's physical RAM and
   101% of a tier node's -- see* Memory does not bind, but is not
   spare either *above.*
6. **How much of the phantom stays?** `slim-primary` deliberately
   lists an unreachable `sf-absent` hypervisor as the regression
   guard for the 2026-07-20 absent-node deploy failure. Any
   reshaping keeps it; the phase plans must say so explicitly so
   that nobody "tidies" it away. **Decided in phase 0 as D6.**

## Execution

!!! note "In this project"

    A status cell holds exactly one term from the vocabulary in
    `PLAN-TEMPLATE.md` and nothing else. The same term is written
    twice: once in the phase table below, and once in the row this
    plan carries in `docs/plans/index.md`. Keep them in step --
    the index row is the whole-plan status, so it only reaches
    `Complete` once every phase has been completed, abandoned or
    superseded.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Decisions: what each topology is for, widen-versus-reservation, and an inventory of what scarcity currently catches | [PLAN-ci-cloud-sizing-phase-00-decisions.md](PLAN-ci-cloud-sizing-phase-00-decisions.md) | Complete |
| 1. Headroom instrumentation: sample `/admin/resources` through every cluster job and publish the series | [PLAN-ci-cloud-sizing-phase-01-headroom-probe.md](PLAN-ci-cloud-sizing-phase-01-headroom-probe.md) | Complete |
| 2. Baseline measurement window: the peak-demand distribution that has never existed | [PLAN-ci-cloud-sizing-phase-02-baseline.md](PLAN-ci-cloud-sizing-phase-02-baseline.md) | In progress |
| 3. Explicit saturation coverage, so that growing a cloud cannot silence a defect | PLAN-ci-cloud-sizing-phase-03-saturation-coverage.md | Not started |
| 4. Re-shape the topologies against the phase 2 data | PLAN-ci-cloud-sizing-phase-04-topologies.md | Not started |
| 5. Guardrails: the headroom band, and a structural-minimum assertion that names the ledger | PLAN-ci-cloud-sizing-phase-05-guardrails.md | Not started |
| 6. Documentation and downstream propagation | PLAN-ci-cloud-sizing-phase-06-docs.md | Not started |

### Phase 0 -- Decisions and scarcity inventory

Settle the open questions above, and produce the inventory that
phase 3 is built from: every distinct failure signature the
current clouds produce because they are small, each classified as
*defect we must fix*, *behaviour we must assert*, or *test bug*.
The #3772 umbrella, #3907, #3565 and the closed #3813 are the
starting corpus; the merge-CI triage history is the source.

Deliverable: the decisions written into this plan, and a table of
signatures with a disposition each.

### Phase 1 -- Headroom instrumentation

Sample `GET /admin/resources` (admin-only, already implemented by
`AdminResourcesEndpoint` -> `Scheduler.summarize_resources()`)
throughout the functional-test step, and emit a per-run summary:
peak and p90 committed vCPU cluster-wide and per node, both as
absolute numbers and as a fraction of ledger; the same for memory;
and a per-stage count of candidate refusals. Upload the raw series
as a job artifact and print the summary in the log.

There are four capacity stages, not three: `sufficient_idle_cpu`,
`sufficient_idle_memory`, `sufficient_free_disk` (disk *space*)
and `sufficient_idle_disk` (disk *bandwidth*, a rate predicate).
Phase 1's survey found this plan and phase 0 naming only three,
with the bandwidth stage standing in for disk capacity.

No gating, no topology change. The endpoint publishes
`cpu_measured`, `cpu_committed` and `cpu_hard_max`, so "this node
measures idle but is refusing work" is answerable, which is what
makes it the right probe. It does *not* publish the capacity row's
`limit_cpus`, which is what admission actually compares against
and which D7 needs; phase 1 adds it.

Most of the work lives in the main repository's `tools/`, following
`ci_wait_schedulable.py`; the invocation lives in
`shakenfist/actions` (the reusable `smoke-cluster` workflow and
`build-smoke-cluster`), so it still needs an operator push and a
real CI run to prove.

### Phase 2 -- Baseline measurement window

Publish the distribution: what peak utilisation actually is per
job, on both topologies, and how it correlates with the failures.
This is the number that has never existed, and it is what turns
the candidate shapes above into a decision.

This section previously described phase 2 as leaving phase 1
running for an agreed number of merge runs and waiting. **The
waiting is already done.** The phase 2 survey counted 66
`merge_group` runs of `functional-tests.yml` banked since the
census fix merged on 2026-08-30, each carrying five cluster
bundles, all still inside the 90 day artifact retention. Phase 2
harvests them retrospectively (phase 2, D16) rather than opening a
new window, because waiting adds weeks and no information while
the early part of the window expires.

Two things the phase 1 output already changes about what phase 2
must look for. The cluster-wide committed fraction and the
*per-node maximum* can disagree by a factor of two -- one sampled
`slim-primary` run sat at a cluster-wide p90 of 0.407 with one node
pinned at 1.000 and twelve real `sufficient_idle_cpu` refusals --
so D3's band gains a per-node component (phase 2, D21). And open
question 5's expected answer has flipped: memory recorded zero
drops and a p90 of 0.14-0.20 of ledger in both sampled runs, so
phase 2 is testing whether D5 should be *narrowed*, not whether it
holds.

The tier's ledger question (D7) is closed by evidence already in
hand -- see the Situation section above -- so phase 2 confirms the
fallback count across its window rather than reconciling two
figures. It is confirmed at zero over 85,563 ledgered node-samples.

The baseline is now published: the *Situation* section above is
written entirely from it, and the dataset it was computed from is
committed under
[`docs/plans/data/ci-cloud-sizing-baseline/`](data/ci-cloud-sizing-baseline/README.md).
Its headline findings, including the ones that corrected this
plan's expectations, are recorded in the phase 2 plan.

### Phase 3 -- Explicit saturation coverage

**Gate on this phase before phase 4.** Convert the scarcity
coverage we get by accident into coverage we get on purpose: a
functional test that deliberately fills a cluster to its ledger
and asserts the documented behaviour at the boundary, and issues
filed for every signature phase 0 classified as a defect. Where
the documented behaviour is itself wrong -- #3772 argues a bare
507 is the wrong answer to a transient condition -- the test
asserts what the system does today and the issue records what it
should do, so growing the clouds cannot quietly close it.

### Phase 4 -- Re-shape the topologies

Apply the phase 2 decision to `ci-topology-slim-primary.yml` and
`ci-topology-slim-tier.yml` (and the `-released` variants), keeping
the `sf-absent` phantom, honouring the structural minimums
(`test_network_lifecycle` needs two hypervisors that are not the
network node; `test_affinity` needs three nodes;
`test_database_tier` needs two database nodes), and updating any
hardcoded node lists in `functional-tests.yml`. Land one topology
at a time so a regression is attributable.

### Phase 5 -- Guardrails

Turn phase 1's summary into a check: warn outside the agreed
headroom band, and add a cluster-CI assertion on the deployed
topology's hypervisor count and total ledger so that a future
topology edit which halves capacity fails as itself rather than as
a flake in an unrelated test. Follow the warn-window-then-gate
pattern the API-validation plan used.

### Phase 6 -- Documentation and downstream propagation

Document the sizing model in `docs/developer_guide/ci.md` --
the ledger arithmetic, the band, and how to re-measure -- and
propagate the reshaped topologies to the downstream repositories
that consume the reusable workflow, per the copy-paste-drift
finding in `project-sf-ecosystem-ci`.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

!!! note "In this project"

    Phases 1, 4 and 6 touch `shakenfist/actions`, which only the
    operator pushes, and which cannot be proven green except by a
    real CI run. Those phases follow the pattern
    `PLAN-remove-primary-phase-06-step5b-ci-design.md`
    established: design and review the change in full here, hand
    the operator a reviewed diff, then verify against a live run
    rather than treating a merged commit as done.

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases 0, 2, 3 and 5 are high effort: they turn on judgment
    about what CI is for and what a failure means. Phases 1, 4 and
    6 are medium -- the arithmetic is settled by then and the
    edits are mechanical, but they are cross-repository, so the
    brief must name the files in `shakenfist/actions` explicitly.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    A worked brief for this plan: instead of "sample cluster
    capacity during the test run", write "add a step to
    `.github/workflows/smoke-cluster.yml` in `shakenfist/actions`
    which, before the `Run functional tests` step, starts a
    background poller on the primary that curls
    `http://localhost:13000/admin/resources` with a system-namespace
    bearer token every 15 seconds into
    `/srv/ci/headroom.jsonl`; the endpoint is
    `AdminResourcesEndpoint` in
    `shakenfist/external_api/admin.py` and its shape is whatever
    `Scheduler.summarize_resources()`
    (`shakenfist/scheduler.py`) returns, so read that for the
    field names rather than guessing. Summarise the series in a
    step after the tests and upload it with the other artifacts."

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] The code passes `pre-commit run --all-files` (flake8,
          stestr unit tests, mypy).
    - [ ] If proto files changed, stubs were regenerated with
          `tox -e genprotos` and committed.
    - [ ] Any claim about a pass rate, a duration or a ledger was
          recomputed, not copied from this document. Every number
          here has a date on it for that reason.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* Every merge-queue cluster job publishes, as a job artifact and
  a log summary, how full its cloud got -- peak and p90 committed
  vCPU and memory against ledger, and a count of capacity
  refusals.
* A topology change that materially reduces a cloud's ledger
  fails a check that names the ledger, rather than surfacing as a
  flake in an unrelated test.
* The `Debian 12 tier` job's pass rate is comparable to the other
  cluster jobs, and its remaining failures are not
  `sufficient_idle_cpu`.
* Every capacity-related failure signature that the pre-change
  clouds produced is either covered by an explicit test or has an
  open issue naming it. No issue was closed merely because a
  bigger cloud stopped reproducing it.
* A merge run's under-cloud memory footprint is lower than the
  356 GB it was on 2026-08-27, and the sizing of each topology is
  justified in `docs/developer_guide/ci.md` by a measurement
  rather than by history.
* The code passes `pre-commit run --all-files` (flake8, stestr
  unit tests, and mypy type checking).
* Lines are wrapped at 120 characters, single quotes for strings,
  double quotes for docstrings.
* Documentation in `docs/` has been updated. `ARCHITECTURE.md`
  and `AGENTS.md` are updated only if a convention or the shape
  of the system changed -- a CI sizing model is a `docs/` matter.

### Documentation index maintenance

This plan is registered in `docs/plans/index.md` (one row, in the
*Master plans* table) and in `docs/plans/order.yml`. Phase files
are linked from the Execution table above and appear in neither,
which is what `tools/check-plan-status.py` enforces.

### Future work

- **Sample the under-cloud too.** This plan measures the *inner*
  cloud's headroom. The outer one -- `sfcbr` -- is the resource
  actually being competed for, and the same probe pointed at it
  would say whether a merge run is contending with a deploy or
  with another merge run. That is the mechanism behind the
  redeploy-races-merge-CI family, so it is worth doing once the
  inner probe exists.
- **Per-suite concurrency rather than a fleet-wide 5.**
  `test_concurrency` is 5 for every cluster job regardless of
  topology. Demand and ledger could be reconciled from the other
  end: derive the worker count from the deployed ledger instead of
  hardcoding it. Deferred because it makes runs less comparable
  across topologies, which phase 2 needs.
- **`test_coalescing` is the peak.** It bursts `BURST = 6`
  instances at once, which is a whole node's ledger on the current
  shape, while four other workers are creating. If phase 2 shows
  it dominates the peak, isolating it (or lowering `BURST` and
  raising it again once the topology grows) is a cheap lever.
- **Retire the `test_timeout_minutes: 70` special case.** The
  tier's extra ten minutes was granted for slowness the data does
  not show. It is harmless, but it encodes a belief that is
  false, and beliefs like that are why the sizing went unexamined.
- **The merge queue's throttle counts groups, not clouds.** #3696
  records two merge groups forming for the same PR sixty seconds
  apart, both running their full matrix, for roughly eleven nested
  clusters on the under-cloud at once. The throttle is set to two
  parallel builds, but each build launches five or six clouds, so the
  quantity actually bounded is not the one that exhausts the
  under-cloud. Reducing per-cloud footprint helps; bounding the right
  quantity would help more, and is a change to queue configuration
  rather than to anything in this plan.
- **Generalise to the other repositories' clouds.** The
  downstream repositories fork these topologies. Phase 6
  propagates the shapes; making the headroom probe part of the
  reusable workflow means they inherit the measurement too. Same
  structural cause as the entry below: a cloud built by something
  other than the workflow the probe steps live in is unmeasured.
- **Instrument the two cluster jobs the probe cannot see.** Phase
  2's D17 found that only four of the six clouds a merge run builds
  carry the phase 1 probe, for two different reasons. `Ansible
  modules` runs through the reusable `smoke-cluster` workflow but
  with `test_kind: ansible-modules`
  (`functional-tests.yml:514`), and every probe step is gated `if:
  inputs.test_kind == 'functional'`; widening that gate is the whole
  fix. `Node lifecycle` never reaches that workflow at all -- it
  calls the `build-smoke-cluster` composite action directly
  (`functional-tests.yml:554-557`), and the probe steps live in the
  workflow rather than in the action, so moving them into (or
  duplicating them beside) the action is a change to how *every*
  caller deploys. That is the same seam as the entry above, and the
  two should be done together. The cost of leaving it is concrete
  and already paid: `Node lifecycle` is the best performer in the
  failure table, and the baseline's utilisation-versus-failure
  correlation cannot speak to it.

### Bugs fixed during this work

Related issues to resolve or be aware of while planning. None of
these is closed by growing a cloud, and phase 3 exists to make
sure none of them is closed by accident:

- **#3772** (open, umbrella) -- instance creates refused with
  `507 sufficient_idle_cpu` under suite concurrency. Its own
  verdict is that a bare 507 is the wrong answer to a transient
  condition. Growing the clouds reduces how often it fires without
  addressing that; the issue must stay open and gain an explicit
  test.
- **#3907** (closed 2026-08-26) -- the claims suite asserted
  cluster headroom it never reserved. Fixed by tolerating the
  transient refusal, which is the right fix for the test and is
  also evidence that `slim-tier` is at its ledger routinely.
- **#3813** (closed 2026-08-22) -- the demand guard was
  arithmetically unsatisfiable below four schedulable threads.
  Directly caused by the CI node shape this plan is changing, and
  the reason open question 1 leans towards widening nodes rather
  than lowering the reservation.
- **#3565** (closed 2026-08-31) -- `test_affinity`. Its title
  said "soft affinity loses to resource filters under suite
  concurrency". The most frequent `slim-primary` failure in the
  sampled window, always the same signature: instances that
  should share a node do not. Its most recent traced occurrence
  binds at the memory stage rather than the CPU one, so a bigger
  cloud may quieten it, may not, and either way decides nothing
  about whether the scheduler was right. **Its disposition is
  written**, by scheduler-reservations phase 6, and the title was
  wrong: in the traced run the candidate set had collapsed to one
  node before affinity was scored, so affinity lost no tiebreak
  and was never consulted. It closed on a test change rather than
  a scheduler change. This plan's requirement that it get a
  disposition in phase 0 before phase 4 is therefore satisfied,
  and nothing here waits on it.
- **#3975** (closed) -- the phase 1 headroom probe failed a merge
  queue build. Its per-sample `GET /nodes` and `GET
  /admin/resources` read node state once per node, so at the default
  15s interval it produces N/15 per second of `GetNode`,
  `GetNodeAttributes`, `GetAllNodeDaemonStates` and `GetNodeMetrics`
  from the `api` caller, which clears the idle-load check's
  unbudgeted ceiling from four nodes upwards. D15 said nothing in
  this phase can fail a build; that was true of the phase's own
  verdicts and false of its traffic. Fixed by exempting the pairs in
  `HARNESS_DRIVEN_PAIRS`, not by lengthening the interval or
  widening the budget -- in two steps, because the #3975 fix
  exempted only the three RPCs that issue's body named, and the
  `GetNode` the roster iterator's hydration issues came back as
  #4028. **This carries an obligation into whichever phase retires
  the probe: trim those four pairs from
  `shakenfist/deploy/shakenfist_ci/load_budget.py` at the same
  time.** Nothing enforces it, because the launcher and the workflow
  steps are in `shakenfist/actions` and a decommission done there
  leaves this repository untouched.
- **#3882** (open) -- reconciler drift is not provable from logs.
  Phase 2 wants to compare the live ledger derivation against the
  reconciled `cluster_capacity` figure; if they disagree, this is
  why it is hard to tell which is wrong.
- **Unguarded placements in a cluster's first minutes**
  (**#4087**, filed by phase 2 step 2f).
  `scheduler_node_capacity` has no rows until the
  reconciler's first pass, which `schedule.every(5).minutes` puts
  five minutes after the cluster daemon starts, so for a measured
  135 to 210 seconds of every one of the 204 job-runs in the
  baseline window every admission takes P7's fail-open branch and
  the CPU pre-filter sees only a once-a-minute count of *running*
  domains. The first pass then recomputes `used_cpus` from the
  placement rows that accumulated and writes a node above its own
  `limit_cpus`, after which that node refuses every create while
  measuring idle -- the #3772 signature without exhaustion. This is
  the mechanism behind *A node can record twice its own ledger*
  above, it is not a breach of the guarded UPDATE, and **growing
  the cloud would mask it rather than fix it**, which is exactly
  what phase 3 exists to prevent.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
