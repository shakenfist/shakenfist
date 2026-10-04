# CI cloud sizing and headroom

## How the clouds are sized

A CI cluster is sized against a number which has very little to do
with the machines it runs on. What runs out first in CI is the
scheduler's **admission ledger** -- an allocation figure derived from a
node's thread count and two configuration values -- and not real CPU or
memory. The conductor's per-instance samples put a `slim-primary`
cluster VM at 0.71 cores of its 4 vCPU allocation, about 18%, and peak
memory on a 12 GB node at 4.9-7.6 GB, with zero swap-out on every node
of every job ([PLAN-ci-cloud-sizing](../plans/PLAN-ci-cloud-sizing.md),
*Allocation is roughly double actual usage*). So a CI cloud can refuse
a create while four fifths idle, and the lever that relieves it is a
wider node rather than a quieter one.

The ledger is also the denominator of every band verdict in the rest of
this page, which is why it is described first.

### What admission actually tests

`Scheduler._has_sufficient_cpu` (`shakenfist/scheduler.py`) keeps a
node in the candidate set only while

```
max(measured, committed) + requested <= cpu_schedulable x CPU_OVERCOMMIT_RATIO
```

Both of the terms on the left count *vCPU*, not load: `measured` is the
running-domain census the resources daemon republishes once a minute
(`cpu_total_instance_vcpus`), and `committed` is the `used_cpus`
counter the placement transaction maintains. The node is charged
whichever is larger, so an instance which is placed but not yet running
counts against the cloud for the whole time it spends fetching images.

That filter is a pre-filter rather than the decision: the binding
guard is the atomic `UPDATE` `Instance.place_instance()` makes against
`scheduler_node_capacity` -- introduced by
[PLAN-scheduler-reservations](../plans/PLAN-scheduler-reservations.md) --
whose `limit_cpus` is `floor(cpu_schedulable x CPU_OVERCOMMIT_RATIO)`
(`_derive_cpu_memory_limits()` in `shakenfist/mariadb.py`). The two therefore test the same
arithmetic on purpose, and for sizing they can be read as one bound.
The full stage list, and the four other things admission can refuse on,
are in [the placement
pipeline](../operator_guide/scheduler.md#the-placement-pipeline).

Throughout this page, **a cluster's ledger** is that right-hand side
summed over the cluster's hypervisors.

### Where each term comes from

`cpu_schedulable` is published per node by the resources daemon as
`max(1, cpu_threads - cpu_reservation_threads)`
(`_compute_reservations()` in `shakenfist/daemons/resources/main.py`). The floor of one thread
is a clamp for over-large reservations, and in CI it is load-bearing
rather than theoretical -- see "Two things the topology files do not
show" below.

The reservation is `NODE_CPU_RESERVATION_THREADS`, a per-node value in
each host's `/etc/sf/config` rather than cluster config
([system reservations](../operator_guide/scheduler.md#system-reservations)
covers all three reservations and both clamps). Nothing in the
scheduler bumps it for a node's roles; what does is the deploy, which
computes a per-host *default* of `(1 + (network or database ? 1 : 0)) *
2` threads and fills it in only where the operator has not set the
value (the *Default the per-host CPU thread reservation* task in
`examples/_shared/site.yml`). In CI nobody sets it, so
the default is what runs: **2 threads on a plain hypervisor, 4 on a
network or database node**.

`CPU_OVERCOMMIT_RATIO` is cluster-wide and defaults to 3.0. What it
means, why it is 3.0 rather than the historic 16, and how to restore
the old behaviour are in [CPU
overcommit](../operator_guide/scheduler.md#cpu-overcommit) and the
[configuration
reference](../operator_guide/scheduler.md#configuration-reference);
they are not repeated here.

### The two CI topologies, and the ledgers they yield

Both cluster topologies are defined in the **`shakenfist/actions`**
repository, as `ansible/ci-topology-slim-tier.yml` and
`ansible/ci-topology-slim-primary.yml`. There is no copy of them in
this repository, and every caller reaches them at `@main` with no pin,
so an edit there is live for every run in flight. They are the source
of truth for the two tables below; if the tables and the files
disagree, the files are right and the tables are stale.

Every node is in `allsf`, which is elided below.

**`slim-tier`** -- three VMs, all of them hypervisors:

| Node | vCPU | Other groups | Reserved | Schedulable | Ledger |
|---|---|---|---|---|---|
| `primary` | 6 | `database_node`, `primary_node`, `network_node` | 4 | 2 | 6 |
| `sf1` | 6 | `database_node` | 4 | 2 | 6 |
| `sf2` | 6 | -- | 2 | 4 | 12 |
| **Total** | | | | | **24** |

**`slim-primary`** -- six VMs, five of them hypervisors:

| Node | vCPU | Groups | Reserved | Schedulable | Ledger |
|---|---|---|---|---|---|
| `primary` | 4 | `database_node`, `primary_node` -- not `hypervisors` | -- | -- | 0 |
| `sf1` | 4 | `hypervisors`, `network_node` | 4 | 1 (floored) | 3 |
| `sf2`-`sf5` | 4 | `hypervisors` | 2 | 2 | 6 each |
| **Total** | | | | | **27** |

24 is not a round number chosen for a test: it is exactly `slim-tier`'s
total, and it is `MINIMUM_HYPERVISOR_LEDGER` in
`shakenfist/deploy/shakenfist_ci/sizing.py`, which "The topology
assertion fails rather than skipping" below asserts against every
deployed cluster. The floor therefore sits directly on the topology
with no slack, so a change which takes capacity out of `slim-tier`
fails by name, in a test whose message says the cluster is too small,
rather than as a scheduling flake somewhere else in the suite. The
deliberate cost is that a smaller-on-purpose topology cannot be
deployed without editing that constant in the same change.

27 is not derived on paper either. The sizing plan's baseline window
([PLAN-ci-cloud-sizing](../plans/PLAN-ci-cloud-sizing.md)) read a
cluster ledger of exactly 27.0 in all 154 `slim-primary` job-runs,
which is what makes the arithmetic above a description of the system
rather than a model of it.

### Two things the topology files do not show

**`slim-primary`'s primary contributes nothing.** It carries
`database_node,primary_node` and *not* `hypervisors`, so it is not a
scheduling candidate at all, and `summarize_resources()` leaves it out
of `/admin/resources`'s `per_node` mapping entirely. Six VMs, five of
them in the ledger, and a report which counts nodes reads one fewer
than the topology creates.

**The reservation is a fixed per-node tax, so small nodes are
disproportionately expensive.** Two threads off a 4 vCPU node is half
of it; off a 6 vCPU node, a third. Worse, a 4 vCPU node carrying a
network or database role reserves all four threads and is clamped to
one schedulable thread, which is how `slim-primary`'s `sf1` yields a
ledger of 3 where its identically-sized siblings yield 6. Widening the
nodes of a topology therefore buys more ledger than adding nodes of the
same size does, per vCPU spent on the under-cloud, and that is why the
sizing plan reshaped `slim-tier` by widening its nodes rather than by
adding a fourth. It is the first thing to check against any proposed
new topology.

### Re-measuring this from scratch

Three steps, none of which depends on the others being believed.

**Harvest a window.** `tools/ci_headroom_harvest.py` enumerates
`merge_group` runs and writes one record per job per run; its flags,
its caching and the reasons it refuses to guess are under
[Where the output lands](#where-the-output-lands) below. Name both ends of the window with `--since` and
`--until` if the output is going to be committed -- `--since` alone
grows with every merge and `--limit` moves with the day the harvest is
run on, so neither reproduces its own dataset.

**Read the verdicts.** Each record's `summary.verdict` carries the
band, the p90 CPU fraction and whether the gate was allowed to judge
it; the script under
[Asking whether a cloud is oversized](#asking-whether-a-cloud-is-oversized)
counts those over a window by topology and job.

**Recompute a topology's ledger by hand**, from its file in
`shakenfist/actions`, applying the rules above: skip any node not in
`hypervisors`, reserve 2 threads (4 with `network_node` or
`database_node`), floor the remainder at 1, and multiply by 3.0. The
script below does exactly that and nothing else. Run it from a
`shakenfist/actions` checkout; it prints 24 and 27.

```python
import re
import sys

RATIO = 3.0  # CPU_OVERCOMMIT_RATIO default


def ledger(path):
    text = open(path).read()
    cpus = [int(m) for m in re.findall(r'^\s+cpu:\s*(\d+)\s*$', text, re.M)]
    groups = re.findall(r'^\s+groups:\s*(.+)$', text, re.M)
    if len(cpus) != len(groups):
        sys.exit('%s: %d cpu values but %d groups' % (path, len(cpus), len(groups)))
    total = 0
    for cpu, group in zip(cpus, groups):
        g = [x.strip() for x in group.split(',')]
        if 'hypervisors' not in g:
            continue
        network_or_database = 'network_node' in g or 'database_node' in g
        reservation = 2 * (1 + (1 if network_or_database else 0))
        total += int(max(1, cpu - reservation) * RATIO)
    return total


for path in sys.argv[1:]:
    print('%s: ledger %d' % (path, ledger(path)))
```

It pairs each `cpu:` with the `groups:` that follows because every
topology file creates an instance and adds it to ansible in that order,
and it exits loudly rather than mispairing silently if that stops being
true. `slim-primary`'s `sf-absent` is not an instance at all -- it is an
inventory entry in `absent_deploy_hypervisors`, deliberately never
deployed -- so it correctly never appears in either count.

### An open question: `slim-primary` reads OVERSIZED

The window the gate was armed against
([PLAN-ci-cloud-sizing](../plans/PLAN-ci-cloud-sizing.md)) read
`slim-primary` below the band's lower bound in 25 of 30 job-runs,
against `slim-tier`'s 5 of 10. The topology the sizing plan
deliberately left unreshaped is the emptier of the two, the lower bound
exists precisely to detect that, it has detected it, and nobody has
acted on it.

This is recorded rather than answered. Asking it properly means a
window and not a run --
[Asking whether a cloud is oversized](#asking-whether-a-cloud-is-oversized)
is the command -- and acting on it means shrinking a topology, which is
a change to the cloud every functional job runs on. The `slim-tier`
reshape set the bar for that: a prediction of what the reshape would do
to the band and to the refusal census, and a falsification criterion
stated before the window was harvested, so the reshape could be shown
to have failed. A shrink argued from these readings alone would have
neither, and the
`MINIMUM_HYPERVISOR_LEDGER` floor means `slim-tier` cannot be shrunk at
all without that argument being made in the same change.

## CI headroom instrumentation

Every functional cluster job carries two data-gathering instruments,
so that CI's clouds can be sized from a distribution instead of the
handful of hand-collected numbers
[PLAN-ci-cloud-sizing](../plans/PLAN-ci-cloud-sizing.md) started from;
`docs/plans/PLAN-ci-cloud-sizing-phase-01-headroom-probe.md` records
the decisions behind them. Neither instrument gates anything itself
-- see
[One bound gates, and everything else is information](#one-bound-gates-and-everything-else-is-information)
below -- but the poller's own traffic
does interact with a check that gates, which is the one reason a
reader troubleshooting a CI failure might need this section; see "The
probe's traffic is exempted from the idle-load check". Otherwise it
matters when reading a job's bundle afterwards, or when working on the
sizing plan itself.

### Two instruments, not one

**The headroom series** is a poll. `tools/ci_headroom_probe.py` (in
this repository) runs in the background on the cluster primary for the
whole functional test step, sampling `GET /admin/resources` and the
node roster (`GET /nodes`) every 15 seconds and appending one JSON
record per sample to a JSONL file.

**The refusal census** is a scrape. After the tests finish,
`tools/ci_headroom_collect.sh` (in `shakenfist/actions`) runs a
filtered Loki query for every scheduler admission event the run
produced. `Scheduler._log_and_raise_on_error()` logs one at *every*
stage, whether or not candidates survived, so a green run's refusals
are on the record too, not only a failing one's.

The two are not substitutes for each other. A fifteen-second poll
cannot see a refusal, which begins and ends between samples; a census
cannot see a cloud sitting half empty for an hour, which is the
oversizing case the whole plan exists to catch. Reporting one number
derived from both would hide which of the two produced it, so they
are built, collected and reported separately.

### Where the output lands

Both instruments write into `/srv/ci/traces/` on the cluster primary:
`headroom.jsonl` (the series) and `headroom-census.json` (the raw
Loki `query_range` response the census reads). Neither gets its own
`upload-artifact` step -- the existing `Gather logs` step already
scp's the whole of `/srv/ci/traces/` into the job's 90-day artifact
bundle, so a reader looking at a bundle finds both files sitting
beside the other logs, with no separate download to go and find.

`tools/ci_headroom_report.py` (also in this repository) turns the two
into a printed summary at the end of the job log: p90 and peak
committed vCPU and memory, cluster-wide and per node, both absolute
and as a fraction of the admission ledger, plus the refusal census
broken out by stage. It also has a machine-readable form: everything
it prints is computed once into a plain dict by `summary_record()`,
the prose is rendered from that dict rather than computed separately,
and `--json PATH` writes the dict out as one JSON object. Anything the
report can print, a program can read without parsing the tables --
which is what stops a change to the report's wording silently breaking
a consumer. Two conventions hold throughout the record: a figure that
was not measured is `null` and never `0`, and a ledger is recorded as
the range it moved over rather than averaged.

`tools/ci_headroom_harvest.py` is the batch counterpart, run by hand
from a checkout rather than in CI. It enumerates the `merge_group`
runs of `functional-tests.yml`, downloads each run's cluster bundles
through the `gh` CLI (which supplies the credentials), unpacks the
trace files -- an artifact download is a zip whose single member is
`bundle.zip`, and the traces are inside *that* -- calls
`summary_record()` on each, and writes one compact JSON object per job
per run. It needs at least one of `--since`, `--until` and `--limit`, because
an unbounded harvest downloads the whole ninety day retention window;
bundles are cached by artifact id under `~/.cache/shakenfist-ci-headroom`
by default, outside any checkout, and an already-cached bundle is never
re-fetched. Two things about it are deliberate and worth knowing before
changing it. A merge run builds six cluster bundles, five are
classified for harvest, and all five carry the probe. `Ansible modules`
was classified ahead of the `shakenfist/actions` change that instruments
it ([#4377](https://github.com/shakenfist/shakenfist/issues/4377)); that
change merged on 2026-10-01, and the bundle has recorded a real series
since. Harvests over the window before it read `series_present: false`
for that bundle, which is an expected absence rather than a probe
failure. The sixth, `Node
lifecycle`, is skipped *by name* with the reason recorded in the source,
so it never appears as missing data; and a
bundle whose artifact name the tool has no entry for raises
`UnknownBundleError` rather than being guessed at or skipped, so adding
a job to the merge matrix stops the harvest until someone says what the
new job is. A bundle with no series is written out with a reason
instead of being dropped, because a harvest that silently shrinks its
own window is the failure mode that looks most like success.

That last principle has been tested once. `--since` and `--until` are
applied as a `created=` filter on the API call *and* again on the
returned runs, and nothing in the tool infers anything from the order
the listing arrives in -- an earlier version stopped at the first
out-of-window run on the belief that the runs API returns newest
first, which it does not promise and on at least one occasion did not
do, so a harvest of a perfectly good window enumerated nothing and
exited zero. If you add another listing to this tool, filter it server
side and sort what comes back. Three rules fell out of that, and each
is pinned by a test: a boundary is normalised to UTC before it is
formatted, because `strftime` ignores `tzinfo` and a `+10:00` stamped
with a `Z` moves the window ten hours in the direction this project's
own timezone lies; an enumeration which finds no runs raises *before*
`--output` is opened, so re-running a harvest whose window has gone
wrong cannot be the thing that empties the last good dataset; and any
harvest whose output is going to be committed names both ends of its
window, because `--since` alone grows with every merge and `--limit`
moves with the day it is run on.

### A third file: the capacity-wait trace (`--waits`)

A third file lands beside the other two, written by a different
mechanism. The `self.create_instance()` wrapper (see [Creating instances in the functional
suite](ci.md#creating-instances-in-the-functional-suite)) appends one JSON line to
`/srv/ci/traces/instance-waits.jsonl` every time a create waits out a
transient 507. It reaches the bundle through the same "Gather logs"
scp as `headroom.jsonl` and `headroom-census.json`, with no separate
plumbing needed to get it there.

`tools/ci_headroom_report.py --waits <file>` summarises it: total
seconds waited, the number of waits, the longest wait and the test it
came from, and the split between waits that were informed by a
capacity read and waits that had to sleep blind because
`/admin/resources` itself could not be read (`mode: degraded`). Each
line also carries the create's three request dimensions, the
`binding_dimension` it was short of, and `attempt_number` -- a
1-indexed position, not a count, so a create refused three times
writes three lines carrying 1, 2 and 3. `node` is the placement the
test asked for, and `roster_key` is the `per_node` entry the wait
actually watched: `/admin/resources` keys that mapping by node UUID
while the suite pins by node name, so the two differ and the wait has
to resolve one to the other before it can read anything.
Because the report already runs over a downloaded bundle rather than
only inside a live job, this summary is available for any run that
wrote the trace -- the evidence does not wait for
`ci_headroom_collect.sh` to grow its own `--waits` plumbing and print
it into the job log, which is a separate, later change.

Since [#4337](https://github.com/shakenfist/shakenfist/issues/4337)
landed as `62bb1ddeb`, a trace file that exists and is empty is a
real zero for any run whose base contains that commit:
`BaseTestCase.setUp()` (`shakenfist/deploy/shakenfist_ci/base.py:214`)
calls `ensure_capacity_wait_trace()` (`:180`), which touches the file
into existence at suite start-up and swallows every failure exactly
as the append path does. `absent` and `unparseable` remain unknown,
and an absent file still cannot be read as zero, because the same
on-disk signature covers both a component ref predating the wrapper
and a run whose every write failed. `tools/ci_headroom_report.py`
still nulls an empty trace's counts, and that is correct: the report
runs over a downloaded bundle and cannot see the run's base, so the
four states it emits (`read`, `empty`, `unreadable`, `unparseable`)
are the raw reading, and applying the ancestry condition is the
caller's job.
[PLAN-transient-capacity-refusals-phase-05-queue-decision.md](../plans/PLAN-transient-capacity-refusals-phase-05-queue-decision.md)'s
D43 -- "`empty` is a real zero; `absent` and `unparseable` stay
unknown" -- is the decision this unlocked.
A file that was read but whose every line was malformed is reported
the same way, for the same reason, and carries its own `state` of
`unparseable` in the machine-readable record rather than an
`available` file with a count of zero -- the prose and the record have
to say the same thing, because the tooling that reads this consumes
the record, not the prose.

### The series record format

`headroom.jsonl` is parsed as a contract, so treat the shape below as
load-bearing rather than as prose to paraphrase; the tool's own
docstring is the source of truth if the two ever disagree. Each
line is one JSON object. A successful sample carries:

* `sampled_at` -- float, unix epoch seconds, wall clock at sample
  start.
* `resources` -- the verbatim `/admin/resources` payload
  (`client.get_cluster_resources()`).
* `nodes` -- the node roster at the time of the sample, reduced to
  `uuid`, `fqdn`, `is_hypervisor`, `is_network_node` and
  `is_database_node` for each entry.

A failed sample carries only `sampled_at` and `error` (the exception
text) -- `resources` and `nodes` are absent, not null. The probe never
raises: a failure just writes an error record and the polling loop
continues, so one bad sample never costs the run the rest of the
series.

The roster is recorded on *every* sample, not once at the start of the
run, and that is deliberate. `summarize_resources()` silently omits
from `per_node` any node that is not a hypervisor, whose metrics are
older than 120 seconds, or whose queue is unreasonably long, and does
not say which of those applied. A node missing from a given sample's
`per_node` therefore has several possible explanations, and only a
roster captured in that same sample can tell "not a hypervisor" apart
from "metrics gone stale" or "the node did not exist yet" -- a roster
fetched once at the top of the run cannot, because cluster membership
itself can change mid-run.

### Four capacity stages, and a naming trap

The scheduler's admission checks that can refuse a candidate node for
being full are `sufficient_idle_cpu`, `sufficient_idle_memory`,
`sufficient_free_disk` and `sufficient_idle_disk`. The last two are
easy to swap: `sufficient_free_disk` is disk *space*, but
`sufficient_idle_disk` is disk *bandwidth* -- a rate predicate on
disk-busy delta, not a capacity check at all, and not something that
more or bigger disks in the same shape would fix. The cloud-sizing
plan itself named the wrong one as "disk" until a survey of the
admission code caught the mistake, which is why it is worth calling
out here: read a `sufficient_idle_disk` row in a census as a disk I/O
problem, never as evidence the cloud needs more disk capacity.

### One bound gates, and everything else is information

The band verdict `ci_headroom_report.py` prints is committed vCPU as a
fraction of the admission ledger, against bounds of 0.35 and 0.70, plus a
per-node bound of 0.85. Those bounds are not provisional: they were
defended against a baseline distribution of 204 job-runs, recorded in
[PLAN-ci-cloud-sizing](../plans/PLAN-ci-cloud-sizing.md).

**Exactly one of them can fail a job: the cluster-wide upper bound of
0.70.** Above it, the report returns status 3 and
`shakenfist/actions`'s `tools/ci_headroom_verdict.sh` fails the step with
an annotation titled *Cluster headroom outside the CI sizing band*. That
is not a test failure and the annotation says so: the tests are whatever
the log says they are, and the verdict is a statement about the cluster
they ran on.

Nothing else gates, and the asymmetry is deliberate rather than
incidental:

* **The lower bound (0.35) never gates.** Being oversized is not urgent,
  no build is at risk, and the response is a topology change nobody makes
  from one run. It is also the *common* reading -- 30 of the 40 cluster
  job-runs in the warn window that preceded the gate were below it -- so
  returning a status for it would redden three quarters of cluster CI
  immediately. Ask the question over a window instead; the command is
  below.
* **The per-node bound (0.85) never gates.** The statistic saturates at
  its ceiling on plenty of passing runs, so it cannot tell a bad run from
  a good one at the top of its range. Read it as what a topology should
  achieve, not as an alarm about one run.
* **The refusal count never gates.** Reshaping a cluster to double its
  ledger left the guard refusing at essentially the same rate, because
  `expected_demand` accumulates until whatever bound it is given fills up.
  The count is the earliest sign the demand estimator has drifted, and
  nothing more; `PLAN-transient-capacity-refusals` is what reads it.
* **A broken report never gates.** An absent census or a bug in the tool
  itself is printed and exits 0. An instrument that can fail the job it
  measures changes the failure surface it exists to measure.
* **Nor does a verdict the report could not read.** An OVERSUBSCRIBED
  band is withheld, printed with the reason and exit 0, when the p90 rests
  on fewer than 20 samples which produced a cluster CPU fraction (five
  minutes at the probe's 15 second interval; the smallest real job-run in
  the 204-run baseline had 41), when any sample reported
  `capacity_degraded`, when any sample predates that flag (a bundle whose
  probe cannot say whether its read failed), or when any sample was
  ledger-unreadable *after* the warm-up prefix every run opens with. Each
  of those is the capacity read failing, not the cloud being full. The
  summary record carries the decision as `verdict.gates` and the reasons
  as `verdict.gate_withheld`. Because a single such sample withholds a
  whole run, the gate can go quiet without anything failing; the window
  command below counts withheld job-runs so that "the gate never fired"
  and "the gate was never allowed to fire" can be told apart.

Two switches sit in front of the gate, both of which fail towards *not*
gating. `ci_headroom_verdict.sh` believes a status of 3 only when the
string `BAND_VIOLATION_EXIT` appears in the report's source, so a cluster
running an older checkout cannot have its exit code misread -- the same
feature detection `ci_headroom_collect.sh` already does before passing a
flag. And the gate has an off switch that needs no commit: setting the
`CI_HEADROOM_GATE` **repository variable** to `false` (Settings, Secrets
and variables, Actions, Variables) turns it off for every run which starts
afterwards. Both switches matter because `functional-tests.yml` reaches
that workflow at `@main` with no pin to bump: a change there is live for
every run in flight and cannot be rolled back from here. The reverse
also holds: every call site here now passes `headroom_gate`, and GitHub
rejects a call passing an input the callee does not declare, so
reverting shakenfist/actions#94 would fail every functional job on an
undeclared input rather than ungate it. The rollbacks are the variable,
or removing the input from these call sites before the actions-side
revert.

The gate is armed only on job shapes a warn window has measured, which
today means the merge matrix in `functional-tests.yml` -- the four jobs
the warn window read. That call site passes
`headroom_gate: ${{ vars.CI_HEADROOM_GATE != 'false' }}`, so the variable
being unset leaves the gate on. The other three call sites of
`smoke-cluster.yml` pass `headroom_gate: false`: the smoke tier job (pull
requests only, a single node, never harvested because the harvest reads
`merge_group` runs), the Ansible modules job (measured since
[#4377](https://github.com/shakenfist/shakenfist/issues/4377), but never
fitted to the band, and the reusable workflow resolves the gate to false
for any `test_kind` but `functional` whatever the caller passes), and the
dispatch-only matrix in `scheduled-tests.yml` (whose
single machine entry has never been harvested). Every call site has to
pass one of those two values explicitly. The reusable workflow leaves
the gate off when nothing is passed (shakenfist/actions#102; before it,
the default gated), but that default lives in another repository at
`@main`, so a call site's policy is stated here where a change to it
shows up in review. `shakenfist/tests/test_headroom_gate_workflow_seams.py` enforces
both rules: it derives each call site's shape per matrix entry (topology,
tier, `test_kind`, `stestr_config`) and fails if an armed one is not in
its `MEASURED_SHAPES`. An armed call site must pass all four inputs
itself, since the defaults live in shakenfist/actions at `@main` and a
shape derived from one could drift without anything here changing. The
base image is deliberately outside the shape: the band measures the
cloud, and the window covered Debian 12 and Ubuntu 24.04 on the same
topology. Arming a new shape means adding it there, with a
window of its own behind it.

### Asking whether a cloud is oversized

A single OVERSIZED verdict is noise. The question is a window, and
`tools/ci_headroom_harvest.py` already computes the fraction, so it needs
a command rather than new infrastructure:

```bash
python3 tools/ci_headroom_harvest.py \
    --since 2026-09-21 --until 2026-09-24 \
    -o /tmp/window.jsonl
python3 - <<'EOF'
import collections, json, re
rows = [json.loads(l) for l in open('/tmp/window.jsonl')]
by = collections.defaultdict(list)
skipped = 0
pregate = 0
withheld = collections.Counter()
for r in rows:
    v = (r.get('summary') or {}).get('verdict') or {}
    if v.get('p90_cpu_fraction') is not None:
        by[(r['topology'], r['job'])].append(v)
    else:
        skipped += 1
    # Absent on records written before the gate existed, which is not
    # the same as a series the gate was allowed to judge.
    if 'gate_withheld' not in v:
        pregate += 1
        continue
    for reason in v['gate_withheld']:
        withheld[re.sub(r'\d+', 'N', reason)] += 1
for key, verdicts in sorted(by.items()):
    bands = collections.Counter(v['band'] for v in verdicts)
    fractions = sorted(v['p90_cpu_fraction'] for v in verdicts)
    print(key, len(verdicts), 'max %.3f' % fractions[-1], dict(bands))
print(skipped, 'job-runs had no fraction to judge and are not counted')
verdicts = [(r.get('summary') or {}).get('verdict') or {} for r in rows]
print(sum(1 for v in verdicts if v.get('gate_withheld')),
      'job-runs had a series which could not have supported a violation:',
      dict(withheld))
print(sum(1 for v in verdicts if v.get('gate_withheld')
          and v.get('band') == 'OVERSUBSCRIBED'),
      'of those read OVERSUBSCRIBED, so withholding changed the outcome')
print(pregate, 'job-runs predate the gate and say nothing about it')
EOF
```

Filter on the fraction rather than on `series_present`: a series which
exists but produced no usable CPU sample (an aborted job, a probe that
died early) has a `p90_cpu_fraction` of null, and sorting that among
floats raises.

Name both ends of the window. `--since` alone grows with every merge, and
`--limit` moves with the day the harvest is run on, so neither reproduces
the dataset it produced.

### The topology assertion fails rather than skipping

The other gate here is a test rather than a verdict, and it asks a
different question: not how full the cluster got, but whether it was ever
the right shape. `cluster_ci_tests/test_nodes.py`'s
`test_cluster_topology_meets_the_structural_minimum` fails the job when
the deployed cluster reports fewer than three nodes, three hypervisors,
two non-network hypervisors, or a summed hypervisor ledger below 24. It
fails rather than skipping on purpose: it exists because four other
tests in that directory skip on exactly these preconditions, and a skip
reports as a pass, so a topology edit that removed capacity would stop
proving scheduler affinity and network teardown while every job stayed
green. The single exception is a single-machine deployment -- one node
holding every role, which is what the `localhost` topology deploys and
what `scheduled-tests.yml` runs `cluster-ci.conf` against on purpose --
where it skips, because none of those minimums means anything on one
node.

Neither gate is the same thing as the instrumentation being invisible to
the checks that were already gating, which is what this part of the page
used to claim and what issue 3975 disproved -- see below.

### The probe's traffic is exempted from the idle-load check

The probe polls, and `test_no_unbudgeted_fixed_rate_database_polling`
in the functional suite exists to notice polling. Each sample's `GET
/nodes` hydrates each node from the iterator, which is one `GetNode`
each, then runs `Node.external_view()` per node, which is one
`GetNodeAttributes` and one `GetAllNodeDaemonStates` each, and its
`GET /admin/resources` reads node metrics per node, which is one
`GetNodeMetrics` each. On an N node cluster at the default 15 second
interval that is N/15 per second for all four, from the `api` caller,
flat across windows and indifferent to what the suite is doing --
which is exactly the signature of the server-side polling loop the
check is built to catch. It clears the unbudgeted ceiling, `max(0.25,
0.05N)` per second, from four nodes upwards; it was found on the six
node merge queue cluster, where three of the pairs read 0.3997/s
against 0.30/s and failed the build (issue 3975). The fix written for
that issue exempted only the three RPCs its body named, so the
`GetNode` the iterator issues came back as issue 4028 -- which of the
probe's pairs clears the check's activity-spread bar varies run to
run, and on those runs `GetNode` cleared it alone.

The iterator also has a read of its own, above all of that per-node
hydration: `Nodes` has no `_find` override, so both endpoints take
`baseobject.py`'s default, which opens with one `GetObjectsByState` to
fetch the uuid list the per-node reads then hydrate. That read is once
per call rather than once per node, so it does not scale with cluster
size and clears only the 0.25/s floor of the ceiling -- which only the
one-node smoke topology meets. It failed there as issue 4359, the
second omission of the same kind: the 4028 fix enumerated the reads
downstream of the iterator and missed the iterator's own.

Those five `(operation, caller)` pairs are therefore listed in
`HARNESS_DRIVEN_PAIRS` in
`shakenfist/deploy/shakenfist_ci/load_budget.py`, alongside the events
reads the suite's own await helpers make. The budget file is
deliberately not the home for them: no deployed cluster runs the
probe, so a budget entry would model load that exists nowhere outside
a CI job, and it would have to be a per-node term, raising every real
cluster's ceiling in proportion to its size.

Two consequences worth knowing:

* **The exemption costs coverage.** CI can no longer see a *new*
  fixed-rate poll of node state made through sf-api, whatever its
  rate, nor one which lists objects through an iterator with no
  `_find` override -- `GetObjectsByState` reaches sf-api through every
  such listing, not only the node roster. The blind spot is CI's alone
  -- none of the five pairs is budgeted for the `api` caller, so the
  `ShakenFistUnbudgetedDatabasePolling` alert, which reads its
  exclusions from the budget file rather than from that set, still
  watches all five at the unbudgeted ceiling on every real cluster.
  The pairs also stay visible in a run's `harness_driven` list rather
  than being dropped from the report.
* **Retiring the probe means trimming the exemption.** It outlives
  its justification otherwise, and no test can catch that on its own:
  the launcher (`ci_headroom_launch.sh`) and the workflow steps live
  in `shakenfist/actions`, so a decommission done there stops the
  probe without touching anything in this repository.
  `test_the_suite_still_probes_cluster_headroom` covers what it can --
  it fails if the probe is deleted here, stops sampling on a timer,
  stops reading one of the two endpoints, or loses the note in its
  docstring that records this obligation -- but a probe that is simply
  never launched again looks identical to a running one from here.

An absent census is not zero refusals. When the Loki query failed or
log shipping was unhealthy, the report says so explicitly rather than
printing "0 refusals", which would look exactly like a run that
refused nothing. Read a bundle's `headroom-census.json` the same way
by hand: missing or empty means "unknown", never "clean".

A *short* census is not zero refusals either. The query carries an
entry limit -- 5000, which is both Loki's default
`max_entries_limit_per_query` and what `ci_headroom_collect.sh` asks
for -- and Loki gives no signal when it cuts a response off at that
limit. A response holding exactly the limit is indistinguishable from
a complete one, and the scheduler emits an event per stage per
schedule, so a run creating a few hundred instances can reach it. The
report is told the limit via `--census-limit` and prints `CENSUS MAY
BE TRUNCATED` when the count reaches it; every figure below that line
is a lower bound.

One trap is worth stating because it has already been fallen into. Do
not add a `|= "Added event"` line filter to the census query. That is
the message `eventlog.add_event_multi` logs under, but pylogrus'
`JsonFormatter` merges the caller's fields over the record last and
one of those fields is `message` -- so the shipped JSON's `message` is
the *event's* message, and `Added event` never appears in the line at
all. Such a filter matches nothing, and the empty census that results
is reported honestly as "no schedule stage events at all", which reads
like an idle cluster rather than a broken query.

### The idle-load check measures the shape while it measures the load

`test_no_unbudgeted_fixed_rate_database_polling` evaluates the budget's
model, and two of that model's three terms are facts about the cluster
rather than about the code: node count and standing instances. The
instance count is sampled at every window boundary, not once when the
measurement is over.

The instance count is the one that matters, because the suite is what
creates and destroys instances and it tidies up after itself. A count
taken when the measurement is over is a count of what nobody got round
to deleting, and on the run in
[#4039](https://github.com/shakenfist/shakenfist/issues/4039) that was
zero -- on a cluster whose `sidechannel` daemon had visibly been
monitoring instances the whole time. Every `per_instance_qps` ceiling is
a multiple of that number, so the model predicted a cluster that was not
there and the build failed for load sitting exactly where the model says
it should. The shipped Prometheus rules had already met the same problem
and solved it the same way, by averaging the model over the window it
compares against rather than evaluating it at the instant of the alert.

Three things follow, and each is easy to get wrong:

* **The count comes from `instances_active`**, scraped from
  `sf-resources` on `RESOURCES_METRICS_PORT` on every hypervisor in the
  `created` state -- not from the API's `power_state` field, and not from
  every record in `GET /nodes`, a roster which keeps a node's record
  after the node has gone and would otherwise hold a node that never
  answers. The roster is re-read for every sample, so a node deleted
  part way through drops out rather than going silent. `_doc.method` in
  the budget says a consumer which counts standing instances any other
  way evaluates the model against a quantity it was never fitted
  against, and `sf-ctl database-load` and the Prometheus rules both read
  that gauge.
* **The largest sample wins, not the mean or the last.** Each pair is
  judged at its lowest observed rate, so the matching choice for the
  shape is the largest: a failure then means a pair ran high against
  every reasonable reading of the cluster it ran on, rather than against
  the least generous one. Every sample is in the run's
  `standing_instances_per_sample` detail.
* **A failed scrape unenforces the per-instance ceilings rather than
  failing the build**, because a sample at every boundary on every node
  would otherwise turn one refused connection into a red pull request.
  A gauge not yet published is retried like a refused connection, since
  `sf-resources` creates its gauges on its first update. The `unbudgeted`
  half of the check needs no shape and still runs, and the run's
  `shape_unread_nodes` detail names each node that did not answer, how
  often, and why its last attempt failed. A node which answered *none*
  of the samples does fail the build: that is a port which is never
  open, not a transient, and left alone it would turn the per-instance
  half of the check off on every run. The gauge going missing
  altogether is the quiet version of the same thing, so its name is
  pinned by a unit test.
