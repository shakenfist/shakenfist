# A capacity refusal is transient

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

This plan spans three repositories: this one (the scheduler, the
cluster and resources daemons, and the `shakenfist_ci` functional
suite), `client-python` (`shakenfist_client.apiclient`, which is
what the suite and every operator script calls), and, for one
step only, `shakenfist/actions` (where the reusable
`smoke-cluster` workflow collects the per-run summary this plan
adds). It is a sibling of
[Right-size the CI test clouds](PLAN-ci-cloud-sizing.md) and
[Atomic scheduling via reservations](PLAN-scheduler-reservations.md),
and deliberately does not own anything either of those already
owns: topology shape belongs to the first, the admission ledger
and claims to the second. What is left, and what this plan is, is
the piece both of them explicitly deferred -- that nothing in the
system treats "no capacity right now" as the transient condition
it is.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/scheduler.py` (`_has_sufficient_cpu`, the
pre-filter that raises the refusal this plan is about),
`shakenfist/external_api/instance.py` (the two `507` branches of
`POST /instances`), `shakenfist/daemons/cluster/main.py`
(`_force_capacity_reconcile_if_unguarded` and the anchored
reconcile), `shakenfist/daemons/resources/main.py` (the 60 s
metrics cadence that `measured_cpus` comes from),
`shakenfist/deploy/shakenfist_ci/base.py` and `retries.py` (the
suite's await and retry helpers), and
`shakenfist/deploy/shakenfist_ci/load_budget.py`
(`HARNESS_DRIVEN_PAIRS`, which any new suite-side polling must be
declared in).

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

### The cost

Issue #3772 is the signature in essentially every failing merge
group. Over the sixteen days to 2026-09-08 the `Functional tests`
workflow's `merge_group` runs split **108 failures, 45 successes,
14 cancelled**. Of the thirty most recent failing runs, twenty-four
failed the `Debian 12 tier` job (the `slim-tier` topology). Of
eighteen of those runs whose failed-job logs were read, **all
eighteen contain**

```
shakenfist_client.apiclient.InsufficientResourcesException: ('API request failed', 'POST',
'http://localhost:13000/instances', 507,
'{"error": "No nodes remaining at scheduling stage sufficient_idle_cpu", "status": 507}')
```

and in fourteen a known victim of that refusal is the test that
failed: `test_network_plumbing_lifecycle` (8),
`test_disappearing_source_instance` (3),
`test_stray_torn_down_while_a_hosted_network_survives` (3), then
`test_artifact_show`, `test_lifecycle_power_cycle` and the
affinity tests. The next-largest cause, the database load-budget
check, appeared in seven of the eighteen -- and in six of those a
507 was in the same run, so fixing the budget family alone would
not have turned one of them green.

### What the refusal actually is

The evidence below comes from the full CI bundles -- the 15 s
headroom series, the Loki refusal census and every node's
`journalctl -u 'sf-*'` export -- of six failing merge runs after
PR #4106 landed (34163288637, 34171977552, 34178278720,
34119030297, 34125365386 on `slim-tier`; 34168326220 on
`slim-primary`), two before it (33991296717, 33948911843), and the
50 usable `slim-tier` records of the sizing plan's baseline
dataset. The reproduction recipe and the analysis script are under
[`docs/plans/data/transient-capacity-refusals/`](data/transient-capacity-refusals/README.md).
`+Ns` is seconds after the `Run functional tests` step started.

**Every `sufficient_idle_cpu` abort in the six post-#4106 runs was
a `force_placement` single-candidate create onto a node whose
capacity ledger was genuinely at its limit, while the cluster as a
whole held 3-9 of its 12 vCPU.** The ledger reconstructed from the
`instance placed` and `instance placement released` events in
every node's journal matched the pre-filter's `committed_cpus`
exactly in all ten cases. It is not over-counting, not a race, and
not the warm-up window.

| run | topology | first capacity row | cpu abort(s) | after first row | forced onto | measured / committed / limit |
|---|---|---|---|---|---|---|
| 34163288637 | tier | +181s | +309s | +128s | sf1 | 2 / 3 / 3 |
| 34171977552 | tier | +166s | +512s | +346s | primary | 3 / 3 / 3 |
| 34178278720 | tier | +181s | +200, +201, +202, +314s | +20..+133s | sf1, sf1, sf2, sf1 | 0/3/3, 0/3/3, 1/6/6, 3/3/3 |
| 34119030297 | tier | +165s | +481s | +316s | sf1 | **6 / 0 / 3** |
| 34125365386 | tier | +166s | +696s | +530s | sf2 | 3 / 6 / 6 |
| 34168326220 | primary | +166s | +824s | +658s | sf1 | 3 / 3 / 3 |
| 33991296717 (pre) | tier | +166s | +519, +542, +565, +725s | +354..+559s | *none: all three nodes* | sf2 6/6, sf1 3/3, primary 3/3 |
| 33948911843 (pre) | tier | +181s | +176, +177, +523s | -5s, -4s, +342s | primary, primary, primary | **0 / 6 / 3**, 0/6/3, 3/3/3 |

(The +200 s trio and the +176 s pair are
`test_duplicate_network_work_is_coalesced`, which pins a burst of
six across the hypervisors and tolerates its own create errors; the
test that failed each run was a later one.)

Three things follow from the table.

1. **The binding constraint is the 3 vCPU ledger on the two
   infra hypervisors.** `primary` (hypervisor, network node and
   database node) and `sf1` (hypervisor and database node) are
   4-thread VMs carrying the deploy-time reservation of 4 threads
   (`examples/_shared/site.yml`, `(1 + infra_role) * 2`), so
   `cpu_schedulable = max(1, 4 - 4) = 1`
   (`shakenfist/daemons/resources/main.py:126`) and
   `limit_cpus = floor(1 x 3.0) = 3` (`shakenfist/mariadb.py`,
   `_derive_cpu_memory_limits`). `sf2` gets 6. Cluster 12,
   confirmed at exactly 12.0 in all 50 baseline records.
2. **The victim tests pick those nodes deterministically.**
   `test_network_plumbing_lifecycle` takes the first two
   non-network hypervisors (`sf1`, `sf2`);
   `test_disappearing_source_instance` the first hypervisor;
   `test_stray_torn_down_while_a_hosted_network_survives` the first
   non-network hypervisor (`sf1`); `test_artifact_show` its first
   instance's node. Five stestr workers queue their pinned creates
   on a node three 1-vCPU instances fill, and the instances that
   fill it are still fetching images (measured 0, committed 3) when
   the next one is refused.
3. **The refusals are not concentrated in the warm-up.** They land
   between +176 s and +824 s, through the first fourteen minutes
   of a 25-35 minute step, after the ledger rows exist. In the
   baseline, the ledger-3 nodes sat at 100% of ledger at p90 in
   **65%** of node-records (peak at or above 1.0 in 96%); 33 of the
   37 `slim-tier` failures had at least one abort; `slim-tier`
   passed 13 of 50.

Per-node saturation across the six post-#4106 runs
(`max(measured, committed) >= limit`):

| node (ledger) | share of the run at ledger | longest stretch |
|---|---|---|
| sf1 (3) | 7-23% | 90-330 s |
| primary (3) | 1-12% | 15-255 s |
| sf2 (6) | 0-15% | 0-240 s |

### Three real mechanisms that are not the driver

**The warm-up window is still open.** Issue #4087 found that
`scheduler_node_capacity` has no rows until the reconciler's first
pass, so every early placement takes P7's fail-open branch and the
first pass then writes the accumulated ground truth onto a row
whose limit it exceeds. PR #4106 anchored the reconcile to
election and added a one-shot
`_force_capacity_reconcile_if_unguarded()`
(`shakenfist/daemons/cluster/main.py:741-778`, called at `:879`).
In all six post-#4106 runs that one-shot fired 130-150 s *before*
the test step, and the pass it forced logged `nodes=0
nodes_added=0`: no hypervisor had published metrics yet. The table
then stayed empty until the five-minute cadence created rows at
+155..+181 s, exactly the 135-210 s the baseline measured before
the fix. Ten to seventeen placements per run were admitted
`node_not_sized`, and the first real pass recorded `drift_cpus` of
2-8 (34178278720: sf1 +6 on a limit of 3). The bold rows above are
this residue. It is real, it is the whole of the sub-three-minute
exposure, and it is not what fails the runs.

**Measurement lags a delete burst by a metrics period.** The
34119030297 refusal was `measured 6 / committed 0 / limit 3`: six
warm-up instances had been deleted and released 32 s earlier and
the ledger was correctly zero, but `cpu_total_instance_vcpus` is
republished every 60 s (`resources/main.py:645, 704`) and still
counted six running domains. The next sample read zero. Because
`_has_sufficient_cpu()` charges `max(measured, committed)`, a node
refuses forced creates for up to a minute after a teardown even
though it is empty. The same rule is why "release the ledger
earlier in `Instance.delete()`" -- a proposal the research
considered -- would not help: the measured side keeps charging the
node until the domain is actually gone, and the journals show no
deleted instance holding ledger at any refusal.

**The demand guard is pure overhead on this topology.** 114-140
`schedule candidate refused by capacity guard` events per run,
**100% demand-only**: the D13 bound is `0.75 x cpu_schedulable`,
which is 0.75 on a 1-thread node against a `cpu_load_1` near 3.
Every one was waived on the second walk, so roughly 40% of creates
pay two guarded transactions at the moment the cluster is busiest.
It never produced a 507 in these runs. It is owned by the
scheduler-reservations plan and recorded there.

### What has been tried

- **#3724** added the committed-vCPU ledger so placed-but-unbooted
  instances are charged immediately. Admission became more
  accurate; an accurate "no" is still a failed test. Three
  recurrences followed and the six per-test issues were absorbed
  into #3772.
- **#3722** reordered the load-shedding filters below affinity.
  Cannot help: the node is removed at admission before ranking.
- **#4106** closed #4087 in principle and, as above, not in CI.
- **#3813** made the demand guard satisfiable; the waiver rate fell
  from 62% to 4%. Not a 507 cause.
- **#3907** and **#3565** were closed test-side, by tolerating the
  transient refusal and by skipping when the candidate set had
  collapsed. Both are precedents for the shape of this plan and
  neither generalised.

### What this is not

Claims are not the answer to a forced placement. A namespace claim
is cluster-wide and carries no node affinity (scheduler-reservations
D14), `shakenfist/scheduler.py` never reads `namespace_claims`, and
the stage that refuses is per-node. Per-test namespaces holding
claims would starve one another's unclaimed pool, and phase 5's
hard ceiling caps the holder rather than helping it. The one
legitimate claim in CI is the conductor holding one per run against
*other* merge groups on the under-cloud, which is
scheduler-reservations phase 4c. Node-scoped claims are considered
under open question 5 below and declined for now.

## Mission and problem statement

Make a capacity refusal something a caller can wait out rather
than a failure it has to report, without hiding the refusal from
the people who need to see it. Concretely:

- A cluster's first guarded placement should happen within seconds
  of its hypervisors publishing metrics, not five minutes after its
  cluster daemon started.
- A node should stop charging for instances that no longer exist
  within seconds of their deletion, not within a metrics period.
- The functional suite should treat a 507 at `create_instance` as
  "not yet" -- waiting, informed by the cluster's own published
  headroom, for the node it needs -- and should say, per test and
  per run, how long it waited, so that a topology that makes the
  suite wait is visible rather than merely slower.
- The API should tell a client that a refusal is transient and
  when to try again, and the Python client should be able to act
  on that when asked to.
- Whether the server itself should queue a create until it fits is
  decided from measurement, not argued from first principles, and
  the decision is written down either way.

The merge-queue outcome this is measured by is the `Debian 12
tier` job's pass rate, which is 24% in the baseline and should be
comparable to the other cluster jobs (78-96%) once this plan and
the sizing plan's phase 4 have both landed.

## Open questions

These were settled at planning time from the research recorded in
the Situation section; the reasoning is kept so that a later
reader can disagree with it on the evidence rather than
re-deriving it.

### 1. Where does the retry live?

**In the suite first, in the client second, in the server only
if the data says so.** The scheduler-reservations plan left this
undecided ("the client SDK, the CI base class, or server-side
admission queueing") and deliberately held it until #3772 had soak
data from a `develop` carrying atomic admission. That soak data
now exists -- the phase 2 baseline and the journals above -- and
it says the refusals are correct against the ledger. A suite-side
wait is the smallest change that stops the bleeding, it is where
`retry_while_transient` already lives, and it is the only place a
wait can be *informed*: `GET /admin/resources` publishes per node
`cpu_available = cpu_hard_max - max(measured, committed)`, which is
the pre-filter's own arithmetic, so a pinned test can wait for the
node it needs rather than for the cluster. The client change is
next because it is what makes the behaviour available to operators
and to the downstream repositories' suites. A server-side queue
reverses scheduler-reservations D8 and is phase 5's decision.

### 2. Does a retry hide the problem?

**Only if it is silent, so it will not be.** Both sibling plans
say a retry would mask whether a bigger cloud or atomic admission
actually changed the failure rate. The wrapper in phase 2 therefore
records every wait as a test detail (how long, on which node, what
the headroom looked like) and the run publishes a summary -- total
seconds waited, waits per test, longest wait -- through the same
bundle the headroom probe uses, so the sizing plan's phase 5
guardrails can warn on it. A cloud that makes the suite wait
becomes a number in every run instead of a flake in some. The
sizing plan's phase 3 saturation tests, which assert what a full
cluster does *today*, call the raw client and are exempt from the
wrapper by an explicit marker.

### 3. Why is allocation less reliable in the first minutes, and can the window be shortened?

**Because the one-shot fires before there is anything to
reconcile; yes, to seconds.** `_force_capacity_reconcile_if_unguarded()`
runs once, on the election path, and tests `if rows:`. On a fresh
cluster the cluster daemon wins its election within 2.5-7.5 s of
starting, before `sf-resources` on any hypervisor has published --
the pass finds nothing, and nothing re-checks. Phase 1 makes the
check a property of the elected loop rather than of election: on
each iteration, compare the set of active hypervisors that have
published metrics against the set with a capacity row, and make the
reconcile due when they differ. That also closes the narrower hole
#4106 left -- a node that publishes late, or whose row the
reconciler removed on stale metrics, admits unguarded for up to
five minutes today. The stability gate stays in front of the pass,
because a pass with no fresh metrics deletes rows rather than
creating them (issue #4087, correction 2).

### 4. Why does a node refuse for a minute after its instances are deleted?

**Because measurement is a 60 s poll of libvirt and the pre-filter
charges the larger of measurement and ledger.** The rule is right:
it is what makes the ledger safe against instances the reconciler
has not yet counted. What is wrong is the cadence. Phase 3 has
`sf-resources` notice a change in the running-domain set cheaply
(a `listAllDomains` every few seconds costs nothing and touches no
database) and publish immediately when it changes, keeping the
full 60 s publish for everything else. That is activity-coupled
rather than fixed-rate, so it does not move the database load
budget's idle figure, and it is declared in
`database_load_budget.yaml` as such.

### 5. Should there be node-scoped claims?

**Not now, and not for this problem.** The question was raised at
planning time because a claim "on hypervisor X for N vCPU" is the
shape a pinned test would want: hold the reservation, then create
against it, with no window between "the node has room" and "my
create landed" for another worker to take it. Against that:

- For CI it buys one thing over an informed wait -- closing that
  window -- and the window is small at concurrency 5. Every other
  property (waiting for the right node, bounded by a deadline,
  visible in the run) the phase 2 wrapper has already.
- It is a second ledger dimension on every `scheduler_node_capacity`
  row (`claimed_cpus` beside `used_cpus`, with the unclaimed
  admission guarded against `limit - claimed`), which the guarded
  UPDATE, the reconciler, the cluster singleton's migration on
  claim create and delete, and the pre-filter all have to learn.
  That is scheduler-reservations phases 3 and 4 again, one level
  down, while phase 5 has not decided what enforcement of the
  existing claims even means.
- The pre-filter is claim-blind today; a node claim only works if
  `_has_sufficient_cpu()` consults it, which makes the pre-filter a
  ledger reader in a way D1 chose not to.

Where it *would* earn its place is an operator need, not a test
need: evacuating or draining a node (#1364) has to know there is
room on the destinations before it starts moving instances, and a
live migration wants the same guarantee. When that lifecycle is
built, a per-node reservation is the primitive it needs, and it
should be designed then, against the claim machinery as it stands
after phase 5. Recorded under Future work in the
scheduler-reservations plan so it is not lost.

### 6. Should the demand guard be changed here?

**No; it is scheduler-reservations' and is recorded there.** The
bound of `0.75 x cpu_schedulable` cannot be met on a 1- or 2-thread
node under any real load, so on the CI topologies the first walk
never admits and the waiver walk always does. Rescaling or waiving
it below some `cpu_schedulable` is a one-line change to the demand
clause, but it is phase 4a's clause and the load it saves is the
scheduler-reservations plan's to measure. This plan cites the
evidence and moves on.

### 7. Should the topology change here?

**No; it is the sizing plan's phase 4, and the evidence here
sharpens what that phase must choose.** Any shape that leaves an
infra hypervisor at `limit_cpus` 3 leaves this failure in place,
because the pinned tests select those nodes. The sizing plan's
candidate "tier as 3 x 6 vCPU" gives `primary` and `sf1` a ledger
of 6 each and `sf2` 12, which is the smallest shape in its table
that changes the number that binds. The sizing plan is updated to
say so.

### 8. Should the server queue a create that does not fit?

**Decide in phase 5, from phase 2's wait data.** The machinery
exists -- `BaseClusterOperation.defer_with_backoff()` already
re-enqueues with a delay for artifact fetches and network
operations -- and a `202` with the instance held in `initial` (or a
new `scheduling` state) until placement succeeds or a deadline
passes is a contained change. Against it: it is exactly the
"hold-until-fittable" that scheduler-reservations D8 rejected as
queue-state surface the project does not want; it has no fairness
model (a waiting 4-vCPU create starves behind a stream of 1-vCPU
ones, and a pinned create starves worst); waiting instances hold
IPAM allocations, so a CPU shortage can become an address shortage;
and the client's `_await_instance_create` has a 900 s ceiling that
bounds any useful deadline. If phase 2 shows the suite waits are
short and few once the topology is right, the answer is no and the
phase closes as Abandoned with the numbers. If they are long or
many, the phase designs the queue -- FIFO by request time, pinned
placements admitted against their node only -- and reverses D8 in
writing.

### 9. What should the API say?

**That it is transient, and when to try again.** Both `507`
bodies from `POST /instances` (`external_api/instance.py:906` for
the pre-filter, `:976-981` for the guard) are bare strings. Phase 4
adds a `Retry-After` header and a machine-readable `stage` and
`transient: true` to the error body, so a client does not have to
parse prose to know which refusal it got. The honest hint is a
fixed conservative constant (15 s, matching `defer()`'s default)
rather than a computed one: the server has no pending-release
horizon and a number that implies knowledge it lacks is worse than
one that does not. The client's opt-in retry reuses the shape of
its existing 406 loop and is bounded by the same deadline. The
affinity `409` is deliberately ordered first in the handler and
must never be retried.

## Execution

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

!!! note "In this project"

    The same term is written twice: once in the phase table
    below, and once in the row this plan carries in
    `docs/plans/index.md`. Keep them in step -- the index row is
    the whole-plan status, so it only reaches `Complete` once
    every phase has been completed, abandoned or superseded.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Close the warm-up window: reconcile when a hypervisor has metrics and no capacity row | PLAN-transient-capacity-refusals-phase-01-warm-up.md | Not started |
| 2. The suite waits, and says so: an informed `create_instance` wrapper and a per-run wait summary | PLAN-transient-capacity-refusals-phase-02-suite-wait.md | Not started |
| 3. Publish metrics when the running-domain set changes | PLAN-transient-capacity-refusals-phase-03-metrics-on-change.md | Not started |
| 4. `Retry-After` and a machine-readable transient refusal, with an opt-in client retry | PLAN-transient-capacity-refusals-phase-04-retry-after.md | Not started |
| 5. Decide on server-side queued placement from the phase 2 data | PLAN-transient-capacity-refusals-phase-05-queue-decision.md | Not started |
| 6. Documentation and close-out | PLAN-transient-capacity-refusals-phase-06-docs.md | Not started |

Phases 1, 2 and 3 are independent of one another and can run in
parallel. Phase 4 follows 2, because the client retry should match
the semantics the suite has already proven. Phase 5 needs phase 2
to have reported over a window of merge runs *after* the sizing
plan's phase 4 has reshaped `slim-tier`; until then its data would
be measuring the wrong cloud. Phase 6 is last.

The ordering against the sibling plans: the sizing plan's phase 3
(saturation coverage) does not gate any phase here, because none
of them changes what a full cluster does -- but its phase 4
(reshape) should land before phase 5 here reads its numbers.
Nothing here touches the demand guard, the pre-filters, claim
enforcement or the guarded UPDATE, all of which are
scheduler-reservations' (D11, phase 5).

### Phase 1 -- Close the warm-up window

Make `_force_capacity_reconcile_if_unguarded()` a check the
elected loop repeats rather than a one-shot at election. On each
iteration inside the stability gate, read the capacity rows and
the node metrics; if any active hypervisor has published metrics
and has no capacity row, make the reconcile due now. Keep the
distinction between a degraded read and an empty result that the
existing code is careful about (`rows` is empty for both; only
`degraded` says which), and keep the pass itself behind
`cluster_stable()` -- issue #4087's second correction explains why
a pass with no fresh metrics deletes rows.

Prove it two ways. A unit test in `shakenfist/tests/` drives the
elected loop with a fake capacity table and a fake node roster and
asserts the reconcile becomes due on the iteration a hypervisor
first appears without a row, and not on later iterations where
every hypervisor has one. A functional assertion in the CI
harness reads the headroom probe's own series and requires that
`cpu_committed_row_present` is true for every hypervisor before
the first `instance placed` event of the run -- the baseline
dataset already carries both fields, so the assertion's premise
can be checked against it before it is written. Phase 1 also
comments on #4087 with the finding and reopens it if that is the
convention the tracker follows for an incomplete fix.

Small, server-side, one file plus tests. Plan at high effort: the
interaction with the stability gate and the degraded-read
distinction are the kind of thing a light brief gets wrong.

### Phase 2 -- The suite waits, and says so

Add `BaseTestCase.create_instance()` to
`shakenfist/deploy/shakenfist_ci/base.py`, route every raw
`test_client.create_instance(...)` call site in `cluster_ci_tests/`
and `guest_ci_tests/` through it (there are 115 call sites across
39 files; a list is a mechanical `grep`), and add a unit test in
`shakenfist/tests/` that walks the suite's source with `ast` and
fails on any raw call outside an explicit allowlist marker, the way
`test_ci_claims_headroom.py` already asserts call sites by name.

The wrapper: on `InsufficientResourcesException`, if the deadline
(420 s, the claims suite's `CLUSTER_HEADROOM_WAIT`) has not passed,
emit a tracing event, then wait *informed*: poll
`system_client.get_cluster_resources()` at 10 s and proceed when
`per_node[target]['cpu_available'] >= cpus` for a `force_placement`
create, or `total['cpu_available'] >= cpus` otherwise, falling back
to a blind sleep when `total['capacity_degraded']` is set. Re-create
with a fresh uniquified name, because the refused instance is
`enqueue_delete_due_error`'d and its name is not reusable
synchronously. Reuse `retries.retry_while_transient` by converting
the exception to a `(status, body)` pair so the loop stays
unit-testable against a fake clock. Every wait is attached to the
test as a detail: seconds waited, the node waited for, and the
`per_node` headroom at the first refusal and at admission.

The run summary: total seconds waited, number of waits, longest
wait and its test, written to the bundle beside the headroom
probe's output and printed in the job log. The collection step is
in `shakenfist/actions` (the reusable `smoke-cluster` workflow),
so this phase carries the same operator-push obligation the
sizing plan's phase 1 did, and the summary is designed so that the
sizing plan's phase 5 guardrail can read it.

Budget the poll. `GET /admin/resources` reads `GetNodeMetrics` from
the `api` caller, which is already in `HARNESS_DRIVEN_PAIRS`
(`load_budget.py:309-323`) -- but that exemption's prose names the
headroom probe as the producer and
`test_the_suite_still_probes_cluster_headroom` holds it up. Extend
the comment to name the wrapper as a second, activity-coupled
producer (it polls only while a create is being retried), or the
load-budget test's premise rots silently.

Two test-side changes ride along because they are cheap and reduce
the number of pinned creates: `test_commandline_artifacts.py`'s
second instance and `test_imagefetch.py`'s first are pinned for
convenience rather than for the assertion, and can target
`inst1['node']` only where co-location is actually load-bearing.
And `test_system_namespace.py` subclasses `BaseTestCase` rather than
the namespaced base, so a failure between its inline create and
delete strands a charged instance in the system namespace for the
rest of the run; give it an `addCleanup`.

The sizing plan's phase 3 saturation tests must call the raw client
and assert the refusal; the allowlist marker exists for them.

Plan at high effort. The wrapper is straightforward; the AST guard,
the load-budget declaration and the bundle plumbing across two
repositories are where a light brief goes wrong.

### Phase 3 -- Publish metrics when the running-domain set changes

In `shakenfist/daemons/resources/main.py`, beside the 60 s publish,
poll libvirt's domain list every few seconds (no database access)
and, when the set of running domains or their vCPU total differs
from what was last published, publish immediately. Keep the 60 s
full publish unchanged. Declare the new publish rate in
`shakenfist/data/database_load_budget.yaml` as `activity_coupled`
so the load-budget check models it correctly, and add a unit test
that a domain disappearing between polls produces a publish before
the 60 s tick.

This is what makes "the node is empty" true within seconds of a
teardown rather than within a minute, and it is the only change in
this plan that touches a daemon other than the cluster daemon.
Plan at medium effort; the pattern is the existing loop.

### Phase 4 -- `Retry-After` and a machine-readable transient refusal

Server: on both `507` branches of `POST /instances`
(`external_api/instance.py:906`, `:976-981`), set `Retry-After: 15`
and extend the error body with `stage` (`sufficient_idle_cpu`, or
`capacity_guard`) and `transient: true`. `sf_api.error()` returns
a bare `flask.Response`, so the header is set on the returned
object. The `409` affinity branch is untouched. Update the OpenAPI
declaration and the API-validation plan's error contract if it
describes the body shape.

Client (`client-python`): carry response headers on
`APIException`, and add an opt-in retry policy for `507` bodies
carrying `transient: true`, bounded by the existing async-strategy
deadline and reusing the shape of the 406 loop in `_request_url`.
Off by default; the suite turns it on, and its phase 2 wrapper then
becomes the *informed* layer over the client's blind one.

Plan at medium effort; each half is small, and the coordination is
a version pin between the two repositories.

### Phase 5 -- Decide on server-side queued placement

A decision phase, not a build phase. Read the phase 2 wait
summaries over at least twenty merge runs after the sizing plan's
phase 4 has landed. If total wait per run is small and no test
waits near its deadline, close this phase as Abandoned with the
numbers and the reasoning in the phase file. Otherwise, design the
queue against open question 8's constraints -- FIFO by request
time, pinned creates admitted against their node only, IPAM
allocated at placement rather than at request, a deadline the
client's 900 s create ceiling can contain -- and record the
reversal of scheduler-reservations D8 in that plan's decisions
file before any code is written.

Plan at high effort if it goes ahead; the state-machine and
fairness questions are the expensive kind.

### Phase 6 -- Documentation and close-out

Document the transient-refusal contract in
`docs/operator_guide/scheduler.md` and the API reference, the suite
wrapper and its allowlist marker in `docs/developer_guide/ci.md`,
and the wait summary beside the headroom probe's documentation.
Update `docs/plans/index.md` and the sibling plans' cross-references
to their final state. Comment on #3772 with the before-and-after
pass rate and close it only if the `Debian 12 tier` job's failures
are no longer `sufficient_idle_cpu`; otherwise leave it open with
the numbers.

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

    Phases 1, 2 and 5 are planned at high effort: the first for
    its interaction with the stability gate and the degraded-read
    distinction, the second for its two-repository plumbing and
    the load-budget declaration, the fifth because it may be a
    state-machine design. Phases 3, 4 and 6 follow patterns that
    already exist and are planned at medium effort.

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

    The invariants a brief in this plan must carry, because none
    of them is inferable from the code being edited:

    - Placement transactions open with a guarded `UPDATE`, never a
      `SELECT` (the snapshot-isolation invariant, `AGENTS.md`).
      No phase here touches one, and a brief should say so
      explicitly so a sub-agent does not "improve" one in passing.
    - Attribute writes carry a field mask (`CLAUDE.md`, common
      pitfall 3).
    - Any new suite-side or daemon-side polling is declared in
      `shakenfist/data/database_load_budget.yaml` or
      `HARNESS_DRIVEN_PAIRS`, with prose naming the producer, or
      `test_no_unbudgeted_fixed_rate_database_polling` will fail
      the next merge group (#3975, #4028).
    - The reconcile pass stays behind `cluster_stable()`.
    - The `409` affinity refusal is never retried.

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
    - [ ] `python3 tools/check-plan-status.py` passes after any
          edit to a plan or to `docs/plans/index.md`.
    - [ ] A change that adds polling has its budget entry, and
          `test_no_unbudgeted_fixed_rate_database_polling` has
          been reasoned about, not merely run.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* In every merge-run bundle, every hypervisor has a capacity row
  before the run's first `instance placed` event, and no first
  reconcile pass records a `drift_cpus` above zero.
* A node's published `cpu_total_instance_vcpus` falls within ten
  seconds of its last instance being undefined.
* No functional test fails with `507 sufficient_idle_cpu` at
  `create_instance`; a test that waits for capacity records how
  long, and the run's summary reports it.
* `POST /instances` refusals for capacity carry `Retry-After`,
  `stage` and `transient: true`, and `shakenfist_client` can be
  told to honour them.
* The decision on server-side queued placement is written down in
  the phase 5 file with the data it was made from, whichever way
  it went.
* The `Debian 12 tier` job's pass rate is comparable to the other
  cluster jobs, and its remaining failures are not
  `sufficient_idle_cpu`.
* The code passes `pre-commit run --all-files` (flake8, stestr
  unit tests, and mypy type checking).
* Lines are wrapped at 120 characters, single quotes for strings,
  double quotes for docstrings.
* Documentation in `docs/` has been updated. `ARCHITECTURE.md`
  and `AGENTS.md` are updated only if a convention or the shape
  of the system changed; a transient-refusal contract is an
  operator-guide and API-reference matter.

### Documentation index maintenance

This plan is registered in `docs/plans/index.md` (one row, in the
*Master plans* table) and in `docs/plans/order.yml`. Phase files
are linked from the Execution table above and appear in neither,
which is what `tools/check-plan-status.py` enforces.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

- **Stop re-scheduling an already-charged placement.**
  `NodeInstNetdescOp._instance_preflight()`
  (`shakenfist/operations/node_inst_netdesc_op.py:156-162`)
  constructs a fresh `Scheduler` -- a full `refresh_metrics()`,
  one `get_node_metrics` RPC per node -- and re-runs
  `find_candidates(inst, candidates=[config.NODE_UUID])` for an
  instance the API already admitted on this node. The ledger term
  cannot refuse it (the self-charge is subtracted), but the
  60-second-old `measured_cpus` can, and when it does the op
  redirects to another node and re-enqueues the artifact fetches.
  Replacing that with "is my placement still recorded here and is
  the node healthy" removes a distinct CI failure family (`Too
  many start attempts`) and halves per-create scheduler load. Not
  a 507 cause, so not in this plan.
- **Suite concurrency denominated in ledger.** The sizing plan's
  Future work already records this; phase 2's wait summary is the
  measurement that would justify it.
- **The queue-depth stage.** `_has_reasonable_queue_state`
  refuses any node with more than twenty waiting jobs. Under
  suite concurrency queue depth is exactly what spikes; it did not
  fire in the runs read here but it is untracked and worth a
  census line.
- **Every refused create is a full create-and-delete.**
  `enqueue_delete_due_error` at the 507 site means each refusal
  costs an object, IPAM allocations, an event trail and a delete
  op, at the moment the cluster is busiest. Phase 5's queue, if it
  is built, removes this; if it is not, the cost stands and should
  be measured.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

- **#3772** (open, umbrella) -- the refusal this plan is about.
  Stays open until phase 6 has the before-and-after numbers.
- **#4087** (closed by #4106, incompletely) -- the warm-up window.
  Phase 1 reopens or comments, and fixes it.
- **#3498, #3602, #3670, #3728, #3749, #3767** (closed into #3772)
  -- the per-test victims. Do not file another; the umbrella exists
  because per-test tracking stopped paying for itself.
- **#3907, #3565** (closed test-side) -- precedents for tolerating
  a transient refusal in the suite; phase 2 generalises what they
  did once.
- **#3975, #4028** (closed) -- the headroom probe failing the
  load-budget check. The obligation they left is why phase 2 and
  phase 3 each carry a budget declaration.
- **#1364** (open) -- lame-duck and evacuate. Where node-scoped
  reservations would earn their place; see open question 5.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
