# Phase 2: the suite waits, and says so

Parent plan:
[PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md).

**Planning effort:** high, as the master plan specifies. The wrapper
itself is thirty lines. What keeps this above mechanical is that the
wrapper has to decide *when to stop waiting*, and the obvious
predicate -- the headroom figure `/admin/resources` publishes -- is
not the quantity admission refuses on. A wrapper that waits on the
wrong number does not fail loudly: it stops waiting early, re-issues
a create that refuses again, and turns one 507 into six while
reporting that it waited. The second hard part is that this phase's
output is evidence phase 5 will make a decision from, so a summary
which quietly loses waits is worse than no summary.

This phase continues the plan's decision sequence at **D7**. There is
one namespace across the whole plan; phase 1 used D1-D6.

## Context

The master plan's Situation section establishes that a capacity
refusal in CI is usually a statement about a moment rather than about
the cluster: the instance the refusing node is holding is often
already being deleted by a sibling test. The suite treats every one
of them as a hard failure, because `create_instance()` is called raw
at every site and a 507 raises.

Phase 1 removed one of the two causes. A cluster's first minutes no
longer admit placements against nothing: the measured
time-to-first-capacity-row went from 165 s to 0 s on the same probe
(see phase 1's Outcome section). That matters here in a way worth
stating, because it changes what this phase measures. Before phase 1,
a wait early in a run could be a cold ledger. After it, a wait is
what it appears to be -- some other test is holding the capacity --
which is exactly the quantity phase 5 needs and could not previously
have read cleanly.

What this phase does *not* do is make refusals rarer. It makes them
survivable, and it writes down how long survival took.

## Scope

**In:**

* A `create_instance()` wrapper on `BaseTestCase` which waits out a
  transient capacity refusal, and every call site in
  `shakenfist/deploy/shakenfist_ci/` routed through it.
* A unit test which walks the suite's source with `ast` and fails on
  a raw call outside an explicit allowlist marker.
* A per-wait record written where the CI bundle already collects it,
  and a summary in `tools/ci_headroom_report.py`.
* The load-budget declaration for the wrapper's polling.
* Two ride-along test fixes the survey confirmed are real.

**Out:**

* Any change to what the server does about a refusal. `Retry-After`
  and the machine-readable transient marker are phase 4; the wrapper
  here reads only what `/admin/resources` already publishes.
* Any change to the scheduler, its pre-filters, the demand guard or
  the guarded `UPDATE`. Those are scheduler-reservations' (D11).
* Deciding anything about a server-side placement queue. This phase
  produces the numbers phase 5 decides from and takes no position.
* Making the suite use less capacity. Reducing the number of pinned
  creates is a ride-along where the survey found the pin was not
  load-bearing, and is not a goal in itself.

## What the survey found

The master plan's phase 2 section was written on 2026-09-08, before
phase 1 executed. Most of it holds. Five claims do not, and two of
those change the design rather than the wording.

**1. The call-site count is wrong, and so is the scope it names.**
The section says "115 call sites across 39 files" in
`cluster_ci_tests/` and `guest_ci_tests/`. Those two directories hold
**88 calls in 32 files** today. The count that matches 115 is the one
over all of `shakenfist/deploy/shakenfist_ci/`: **117 calls in 41
files**. The difference is `smoke_ci_tests/` (27 calls in 7 files),
plus one call each in `base.py:1227` (`TestDistroBoots`) and
`database_tier.py:275`. The smoke tier is not a rounding error: it is
the tier that runs on the smallest cloud, where a single sibling
instance is a larger fraction of the cluster. Corrected at source in
the master plan.

**2. The published headroom figure is not what admission refuses
on.** `summarize_resources()` computes
`per_node[n]['cpu_available'] = cpu_hard_max - max(measured,
committed)` (`shakenfist/scheduler.py:1086-1088`), where
`cpu_hard_max` is the *live* overcommit arithmetic. Admission refuses
against the capacity row's `limit_cpus`, which is the same arithmetic
refreshed once a reconcile period. The endpoint publishes both
deliberately and says why: the comment at `:1071-1078` is explicit
that `cpu_limit` has no fallback precisely so a reader can see the
two ledgers disagree. A wait predicate reading only `cpu_available`
therefore returns "there is room" during exactly the window where the
two differ -- which is the window this plan exists to describe. See
D8.

**3. `total['cpu_available']` is a sum, and an instance is not.**
The same function accumulates `total['cpu_available']` by adding each
node's headroom, flooring negatives at zero (`:1091-1093`). An
instance must fit on one node. `total['cpu_available'] >= cpus` is
therefore satisfied by a cluster of ten nodes with one spare thread
each, which will refuse a two-vCPU create every time. See D8.

**4. The target node can be absent from `per_node`, not merely
short of headroom.** `per_node` skips any node whose metrics say it
is not a hypervisor and any node over `UNREASONABLE_QUEUE_LENGTH`
(`scheduler.py:1039-1044`), and a node with no metrics row at all
never appears. Phase 1 hit this in `test_nodes.py` and had to assert
`assertIsNotNone(per_node)` separately from the headroom check. A
predicate written as `per_node[target]['cpu_available'] >= cpus`
raises `KeyError` inside the wait loop. See D9.

**5. `retry_while_transient` is a blind re-issue, and its module is
deliberately import-free.** `shakenfist/deploy/shakenfist_ci/retries.py`
calls `request()` every 10 s until the status stops being transient.
Reusing it directly means issuing a *create* every 10 s, and every
refused create builds an instance object and then
`enqueue_delete_due_error`s it (`shakenfist/external_api/instance.py:901-906`)
-- so a 420 s wait would leave 42 error-deleted instances behind and
add its own load to the cluster it is waiting for. Worse, the
module's docstring records that it imports nothing from the suite or
from `shakenfist_client` so the unit tests can load it by path; a
poll of `system_client.get_cluster_resources()` cannot live in it.
See D11.

**6. The bundle plumbing needs no change in the actions
repository.** The master plan says this phase "carries the same
operator-push obligation the sizing plan's phase 1 did". Half of that
is true. `/srv/ci/traces` on the primary is created and chowned to
the base-image user by `smoke-cluster.yml:212`, and the *entire*
directory is scp'd into the 90-day artifact bundle by the "Gather
logs" step at `:550`. The functional suite runs on the primary as
that user. So a file the suite writes to `/srv/ci/traces` reaches the
bundle with no change to `shakenfist/actions` at all. Only *printing*
the summary in the job log needs one, because that happens in
`tools/ci_headroom_collect.sh`. See D14, which splits the phase so
the evidence does not depend on the cross-repo push.

**7. Claims the survey confirmed.** `BaseTestCase`
(`base.py:119`) has no `create_instance()`. `_uniquifier()` exists at
`base.py:193`. `CLUSTER_HEADROOM_WAIT = 420` is at
`test_namespace_claims.py:134`, with the existing transient-retry
usage at `:324`. `InsufficientResourcesException` is exported by
`shakenfist_client.apiclient` and is what a 507 raises.
`HARNESS_DRIVEN_PAIRS` is at `load_budget.py:309`, and
`test_the_suite_still_probes_cluster_headroom` -- which holds its
prose up -- is at `shakenfist/tests/test_database_tier_harness.py:374`.
The `ast` precedent is `shakenfist/tests/test_ci_claims_headroom.py`
(`:41`, helpers at `:171-190`). `test_system_namespace.py:9`
subclasses `base.BaseTestCase`, creates inline at `:27` and deletes
at `:56` with no `addCleanup`. `capacity_degraded` is published on
`total`.

**8. One ride-along is already done, and the other is
mis-described.** The master plan asks for
`test_commandline_artifacts.py`'s second instance to target
`inst1['node']` "only where co-location is actually load-bearing" --
but `:182-190` already pins it to `inst1['node']`, which is the shape
being asked for, and `inst1` at `:139` is not pinned at all. There is
nothing to change there. `test_imagefetch.py` has **four** pinned
creates, not one. In the first test (`:130`, `:152`) both are pinned
to `n`, the first hypervisor in the node list; the source URL is
deleted between them, so `inst2` must land where `inst1` did, but
`inst1`'s pin to an arbitrary node is pure convenience. In the second
test (`:222`, `:247`) the `first`/`second` pins are load-bearing by
construction -- the comment says "on a node which has not seen this
image before" -- and must stay. Net: one pin is removable and one
becomes `inst1['node']`. See D17.

Corrections 1, 6 and 8 have been made at their source in the master
plan's phase 2 section as part of the planning commit, so a later
step does not need to redo them.

## Decisions

### D7 -- The wrapper covers every call site in `shakenfist_ci`

All 117, not the 88 in the two directories the master plan named.
`smoke_ci_tests/` gets the wrapper for the same reason the others do,
and more so: it runs on the smallest cloud. `base.py:1227` and
`database_tier.py:275` are inside the harness rather than in a test,
but they are creates against the same cluster and a refusal fails
them the same way.

The two harness call sites route through the wrapper by calling it as
`self.create_instance(...)`, which is available to them because both
are on classes descending from `BaseTestCase`. No call site outside a
`BaseTestCase` subclass exists today; the AST guard in D15 fails if
one appears.

### D8 -- Wait on the ledger the guard reads, not the headroom published

The predicate for "there is now room for this create" is, per node:

```
available = per_node[n]['cpu_available']
if per_node[n]['cpu_limit'] is not None:
    available = min(available,
                    per_node[n]['cpu_limit'] - per_node[n]['cpu_committed'])
```

and the create proceeds when `available >= cpus` for the pinned
target, or when **any** node satisfies it for an unpinned create --
`max` over nodes, never `total['cpu_available']`, per survey finding
3.

`cpu_limit` is `None` exactly when the node has no capacity row
(`scheduler.py:1078`), which is also when `cpu_committed_row_present`
is false. After phase 1 that state should last about a minute at
cluster start and never recur; treating it as "the published headroom
is all we know" is the right reading, and it is what the fallback to
`cpu_available` alone does.

This is the decision a reviewer is most likely to argue with, because
it makes the wrapper wait *longer* than the master plan's predicate
would in exactly the case where the two ledgers disagree. That is the
point. Stopping early does not avoid the wait, it converts it into
another refused create, another error-deleted instance, and a wait
record that understates what the run actually spent. The cost of
being wrong in this direction is bounded by the 420 s deadline; the
cost of being wrong in the other direction is the measurement phase 5
reads.

### D9 -- A node missing from `per_node` is "not yet", not "never"

The wait loop treats an absent `per_node[target]` as zero available
and keeps waiting, rather than raising. Survey finding 4 gives the
three ways a node goes missing, two of which -- a queue over the
unreasonable length, and metrics not yet published -- are transient
and are precisely what the wrapper is waiting out. The third, a node
that is genuinely not a hypervisor, is a test bug, and the deadline
turns it into a failure with the `per_node` roster attached, which is
the diagnosis a reader needs.

The same treatment applies to a `get_cluster_resources()` call that
raises: log it into the wait record and keep waiting. The endpoint
being briefly unavailable is not evidence about capacity.

### D10 -- A degraded capacity read falls back to a blind wait

When `total['capacity_degraded']` is set, the capacity mapping the
whole predicate rests on could not be read, so `cpu_limit` is `None`
for every node for a reason that has nothing to do with capacity.
Waiting informed on that is waiting on noise. Fall back to sleeping
the poll interval and retrying the create at the deadline's cadence,
and record `mode: degraded` on the wait so a reader can tell a wait
that was informed from one that was not.

This is the same distinction phase 1 drew between an empty answer and
an unknown one (D2, D3), for the same reason.

### D11 -- The informed wait is a new function, with the poll injected

`retries.py` keeps its property of importing nothing from the suite.
The new `wait_for_capacity(...)` lives beside `retry_while_transient`
in the same module and takes the poll as a callable returning the
resources dict, plus `clock` and `sleep` as `retry_while_transient`
already does. The suite passes
`self.system_client.get_cluster_resources`; the unit tests pass a
fake returning a scripted sequence of dicts.

`retry_while_transient` is *not* reused as the outer loop. Its
contract is "re-issue the request until its status stops being
transient", and this loop's shape is "poll a different thing, then
re-issue once". Bending one into the other would leave a helper whose
docstring no longer describes either caller.

### D12 -- Only an insufficient-resources refusal is waited on

The wrapper catches `apiclient.InsufficientResourcesException` and
nothing else. In particular it does not catch the 409 from
`AffinityConstraintUnsatisfiable`, which the API path deliberately
orders *above* the 507 clause because the exception is a subclass and
the ordering is what keeps them distinct
(`external_api/instance.py:890-899`). An affinity constraint that
cannot be satisfied does not become satisfiable by waiting, and a
wrapper that retried it for 420 s would convert a clear failure into
a slow one.

### D13 -- Re-create under a fresh uniquified name

The refused instance is created and then `enqueue_delete_due_error`ed
(`external_api/instance.py:901-906`), so its name is in use until the
delete completes asynchronously. Every retry appends
`'-%s' % self._uniquifier()` to the caller's name. The wrapper
returns the instance it finally created, so the caller never sees the
name it did not choose except in the failure message and the wait
record, both of which state it.

### D14 -- The wait record lands in the bundle without a cross-repo push

Each wait appends one JSON object as a line to
`/srv/ci/traces/instance-waits.jsonl` on the machine running the
suite, opened `'a'` per write. One line per wait, not per run, so
concurrent stestr workers do not have to agree on anything and a
crashed worker loses at most its own in-flight line. Per survey
finding 6 this reaches the artifact bundle through the existing
"Gather logs" scp with no change to `shakenfist/actions`.

`tools/ci_headroom_report.py` grows a `--waits <file>` argument that
prints total seconds waited, number of waits, the longest wait and
its test, and the split between informed and degraded waits. The
report already runs over a downloaded bundle -- that is how phase 1
verified its own definition-of-done item 9 -- so **the evidence is
available from the moment this phase merges**, whether or not the
job log ever prints it.

Printing it in the job log is a one-line addition to
`ci_headroom_collect.sh` in `shakenfist/actions` (scp the file, pass
the flag) and is step 2f. It is deliberately last and deliberately
not depended on by anything else in this phase. If the directory is
unwritable the wrapper still waits and the test still passes: nothing
about recording a wait may fail a test, for the same reason
`ci_headroom_collect.sh`'s header gives for never failing a job.

### D15 -- The AST guard names an allowlist marker

A unit test in `shakenfist/tests/` walks every `.py` file under
`shakenfist/deploy/shakenfist_ci/` and fails on any
`*.create_instance(` call whose line is not preceded by a
`# raw-create:` comment giving a reason. It follows
`test_ci_claims_headroom.py`'s pattern of parsing the suite's source
rather than importing it, because the suite imports
`shakenfist_client`, which is not a test dependency of this
repository.

The marker exists for callers that must see the refusal: the sizing
plan's phase 3 saturation tests, and the wrapper's own call inside
`base.py`. The test asserts the marker's *reason* is non-empty, so
the allowlist cannot grow by copy-paste.

### D16 -- The load-budget prose names the wrapper as a second producer

`GetNodeMetrics`/`api` is already in `HARNESS_DRIVEN_PAIRS`
(`load_budget.py:309`), but the prose there names the headroom probe
as the producer, and
`test_the_suite_still_probes_cluster_headroom`
(`test_database_tier_harness.py:374`) holds that prose up. Extend the
comment to name the wrapper as a second, activity-coupled producer --
it polls only while a create is being retried, so it adds nothing to
an idle cluster and adds load in proportion to how full the cluster
already is.

Not a new budget entry: the pair is already exempt, and adding a
second entry for the same pair would make the exemption look like two
separate allowances.

### D17 -- Only the ride-alongs the survey confirmed

`test_imagefetch.py`'s first test drops `inst1`'s pin and changes
`inst2`'s to `inst1['node']`, per survey finding 8. Its second test
is left alone. `test_commandline_artifacts.py` is left alone: it
already has the shape the master plan asked for.
`test_system_namespace.py` gains an `addCleanup` for its inline
instance, so a failure between `:27` and `:56` no longer strands a
charged instance in the system namespace for the rest of the run.

Nothing else. A phase which is about measuring how long the suite
waits should not quietly change how much capacity the suite uses
beyond what it can justify, or the numbers it reports are about a
different suite than the one that produced the baseline.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Add `wait_for_capacity()` to `shakenfist/deploy/shakenfist_ci/retries.py` beside `retry_while_transient`, and `create_instance()` to `BaseTestCase` in `shakenfist/deploy/shakenfist_ci/base.py:119`. Read `retries.py`'s docstring first: it imports nothing from the suite or from `shakenfist_client` so `shakenfist/tests/test_ci_claims_headroom.py` can load it by path, and that must survive -- take the resources poll as a callable, with `clock` and `sleep` injected exactly as `retry_while_transient` does (D11). Implement the predicate in D8 literally: per node, `available = per_node[n]['cpu_available']`, and where `per_node[n]['cpu_limit']` is not `None`, `available = min(available, cpu_limit - cpu_committed)`; proceed when the pinned target satisfies `available >= cpus`, or when any node does for an unpinned create. Do **not** use `total['cpu_available']`: it is a sum across nodes (`shakenfist/scheduler.py:1091-1093`) and an instance must fit on one. An absent `per_node[target]` is zero available and keeps waiting, never a `KeyError` (D9); a poll that raises is logged into the record and the wait continues. When `total['capacity_degraded']` is set, sleep the interval instead of evaluating the predicate and mark the wait `mode: degraded` (D10). The wrapper catches only `apiclient.InsufficientResourcesException` -- not the 409 from `AffinityConstraintUnsatisfiable`, which is a subclass of `LowResourceException` on the server and is kept distinct only by clause ordering at `shakenfist/external_api/instance.py:890-899` (D12). Deadline 420 s, the `CLUSTER_HEADROOM_WAIT` the claims suite already uses (`cluster_ci_tests/test_namespace_claims.py:134`); poll interval 10 s. Each retry uses a fresh name suffixed with `self._uniquifier()` (`base.py:193`), because the refused instance is created and then `enqueue_delete_due_error`ed and its name is in use until that completes (D13). On exhausting the deadline, fail with the refusal body, the node waited for, and the `per_node` roster in the message. Attach every wait to the test as a detail: seconds waited, node, mode, and the headroom at first refusal and at admission. Unit tests in `shakenfist/tests/` against a fake clock and a scripted poll: an immediate success, a wait that ends informed, a wait that ends at the deadline, a degraded wait, a target absent from `per_node` that later appears, a node whose `cpu_available` is generous while `cpu_limit - cpu_committed` is not (the D8 case -- assert it does not proceed, and confirm that deleting the `cpu_limit` clause makes this test fail), and a 409 passing straight through. Single quotes, 120 columns, no trailing whitespace. |
| 2b | medium | sonnet | none | Route every call site through the wrapper. There are 117 across 41 files in `shakenfist/deploy/shakenfist_ci/` -- `grep -rn '\.create_instance(' shakenfist/deploy/shakenfist_ci/` is the list, and note it includes `smoke_ci_tests/` (27 in 7 files), `base.py:1227` and `database_tier.py:275`, which the master plan's section omitted (D7). Each becomes `self.create_instance(...)` with the same arguments. Then add the AST guard per D15 as a new test in `shakenfist/tests/`: parse every `.py` under `shakenfist/deploy/shakenfist_ci/` with `ast` -- do not import the package, it imports `shakenfist_client`, which is not a test dependency here -- and fail on any `create_instance` attribute call not preceded by a `# raw-create: <reason>` comment with a non-empty reason. Follow the file-walking and call-finding helpers in `shakenfist/tests/test_ci_claims_headroom.py:171-190` rather than inventing new ones. The wrapper's own call inside `base.py` carries the marker. Run the full unit suite; it takes about ten minutes. |
| 2c | medium | sonnet | none | The record and the summary, per D14. The wrapper appends one JSON object per wait as a line to `/srv/ci/traces/instance-waits.jsonl`, opened `'a'` per write, with keys: test id, instance name requested, node waited for or `null`, cpus, seconds waited, mode (`informed`/`degraded`), attempts, and the headroom at first refusal and at admission. Every failure to write is swallowed -- an instrument may never fail the thing it measures, exactly as `ci_headroom_collect.sh`'s header argues. The directory already exists and is writable by the suite's user: `smoke-cluster.yml:212` in `shakenfist/actions` creates and chowns it, and `:550` scp's the whole directory into the bundle, so this needs no change there. Then add `--waits <file>` to `tools/ci_headroom_report.py`, printing total seconds waited, number of waits, longest wait and its test, and the informed/degraded split; absent or empty file prints one line saying so, never zero. Extend `shakenfist/tests/test_ci_headroom_report.py` with a file of several waits, an empty file, an absent file and a file with a malformed line among good ones (which is skipped, counted and reported, not fatal). Finally D16: extend the prose above `HARNESS_DRIVEN_PAIRS` at `shakenfist/deploy/shakenfist_ci/load_budget.py:309` to name the wrapper as a second activity-coupled producer of `GetNodeMetrics`/`api`, and check `test_the_suite_still_probes_cluster_headroom` (`shakenfist/tests/test_database_tier_harness.py:374`) still passes -- it holds that prose up. No new budget entry. |
| 2d | low | haiku | none | The two ride-alongs D17 allows, and no others. In `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_imagefetch.py`, the first test pins both creates to `n`, an arbitrary first hypervisor (`:130-138`, `:152-160`): remove `inst1`'s `force_placement` entirely and change `inst2`'s to `inst1['node']`, which is what the test actually needs since the source URL is deleted between them. Leave the second test's `first`/`second` pins at `:222` and `:247` exactly as they are -- its comment says the second instance must land on a node which has *not* seen the image, so those pins are the assertion. In `cluster_ci_tests/test_system_namespace.py`, the instance created inline at `:27` is deleted at `:56`; add an `addCleanup` immediately after the create so a failure between the two does not strand a charged instance in the system namespace. Do **not** touch `test_commandline_artifacts.py`: `:182-190` already pins to `inst1['node']`, which is the shape the master plan asked for. |
| 2e | medium | sonnet | none | Documentation. `docs/developer_guide/` is the home for how to write a functional test; find where it describes creating instances in the suite and say that `self.create_instance()` is the call, that a raw client call needs a `# raw-create:` marker and why, and what the 420 s deadline means for a test that is genuinely asking for more than the cloud has. Document the wait summary beside the headroom report's own documentation, including that it can be read from a downloaded bundle with `tools/ci_headroom_report.py --waits`. Do not touch `AGENTS.md` unless the `# raw-create:` marker counts as a convention an agent could not infer -- it does, so add one line there and no more. `ARCHITECTURE.md` does not change: no component boundary moves. |
| 2f | low | haiku | none | The operator push, and the only step outside this repository. In `shakenfist/actions`, add to `tools/ci_headroom_collect.sh` an scp of `/srv/ci/traces/instance-waits.jsonl` beside the two it already fetches, and pass `--waits` to the report when the file is non-empty -- guarded by the same `grep -q -- '--waits' "${report}"` pattern the script already uses for `--census-limit`, because the report comes from the triggering component ref and may predate the flag. Nothing here may fail the job. This step depends on 2c having merged and nothing depends on it: the waits reach the bundle without it. |
| 2g | low | haiku | none | Set the phase 2 row to `Complete` in the master plan's Execution table and in `docs/plans/index.md`, update the index arithmetic, then run `python3 tools/check-plan-status.py`. Only after 2a-2e are reviewed and merged, 2f is pushed or explicitly deferred, and a real CI run's bundle has been read with `tools/ci_headroom_report.py --waits` to confirm the summary is populated. Record that reading in an Outcome section, as phase 1 did. |

The survey corrections that would otherwise have been a step here
were made in the planning commit, per the note at the end of *What
the survey found*.

## Risks and mitigations

* **The wrapper waits on the wrong quantity and stops early.** The
  headline risk, and the reason for the high planning effort. It
  fails silently: the create is retried, refuses again, and the run
  looks like the status quo with extra latency. *Mitigation:* D8
  writes the predicate against the ledger the guard reads, and 2a
  requires a unit test for exactly the disagreement case plus a
  confirmation that deleting the `cpu_limit` clause makes that test
  fail. Mutation-checking an assertion that claims to prove a fix is
  the standing lesson from a previous phase where five of seven
  definition-of-done checks turned out not to test what they said.

* **The wrapper hides a real capacity regression.** If the cloud is
  genuinely too small, waiting turns a fast, loud failure into a slow
  one, and the suite stops being a signal about sizing. *Mitigation:*
  the wait summary is the signal instead, and it is strictly more
  informative than the failure was -- but only if someone reads it.
  The sizing plan's phase 5 guardrail is the intended reader, and D14
  makes the data available from the bundle whether or not the job log
  prints it. The 420 s deadline bounds the hiding.

* **117 mechanical edits break a test in a way unit tests do not
  see.** The suite is functional; nothing in `shakenfist/tests/`
  executes these call sites. *Mitigation:* the edits are uniform and
  the AST guard proves completeness rather than correctness; the real
  check is a full CI run before 2g, which is why 2g requires one.

* **The wait record fails the tests it is measuring.** A full disk,
  an unwritable directory, a worker crash mid-line. *Mitigation:*
  every write is swallowed, one line per wait so a partial line costs
  one record, and the report skips and counts malformed lines rather
  than failing on them (2c).

* **The cross-repo step never happens.** `shakenfist/actions` needs
  an operator push, which has stalled phases before. *Mitigation:*
  D14 restructures the phase so nothing depends on it. 2f is the last
  step and 2g accepts an explicit deferral.

## Definition of done

Falsifiable, in order:

1. `BaseTestCase.create_instance()` exists and every one of the 117
   `create_instance` call sites under `shakenfist/deploy/shakenfist_ci/`
   either goes through it or carries a `# raw-create:` marker with a
   non-empty reason. `grep -rn '\.create_instance(' shakenfist/deploy/shakenfist_ci/`
   and the AST test agree on the count.
2. The AST test fails when a raw call is added without a marker, and
   fails when a marker is added with an empty reason. Both halves
   confirmed by running them, not by reading them.
3. A unit test asserts that a node publishing generous
   `cpu_available` but a binding `cpu_limit - cpu_committed` does not
   satisfy the wait predicate, and deleting the `cpu_limit` clause
   from `wait_for_capacity()` makes that test fail.
4. A unit test asserts that a target absent from `per_node`
   continues the wait rather than raising, and that it proceeds when
   the target appears.
5. A unit test asserts that `total['capacity_degraded']` produces a
   blind wait recorded as `mode: degraded`, and that an unpinned
   create never consults `total['cpu_available']`.
6. A unit test asserts a 409 affinity refusal is not retried.
7. `retries.py` still imports nothing from the suite or from
   `shakenfist_client`, and the tests which load it by path still
   pass.
8. `tools/ci_headroom_report.py --waits` prints the five figures in
   D14 for a populated file, says so for an empty or absent one, and
   counts rather than dies on a malformed line. Unit tests for all
   four.
9. A real CI bundle read with `--waits` shows a populated summary, or
   shows zero waits with the run's own evidence that no create was
   refused. Read from a downloaded artifact, as phase 1's item 9
   ultimately was -- this is checkable from a worktree and is not to
   be written off as operator-only.
10. `test_imagefetch.py`'s first test has one pin, not two, and its
    second test still has both. `test_system_namespace.py`'s inline
    instance has an `addCleanup`. `test_commandline_artifacts.py` is
    unchanged.
11. The prose above `HARNESS_DRIVEN_PAIRS` names the wrapper, and
    `test_the_suite_still_probes_cluster_headroom` passes.
12. No document states the wait deadline as a number different from
    `CLUSTER_HEADROOM_WAIT`, and none implies the wrapper makes
    refusals rarer.
13. `python3 tools/check-plan-status.py` passes and
    `pre-commit run --all-files` passes.

## What later phases inherit

* A suite that survives a transient refusal, so a run's pass/fail
  stops being a coin flip on sibling timing and phase 5's data is not
  truncated by the runs that failed.
* A per-wait record in every bundle, which is the input phase 5
  decides the server-side-queue question from, and which the sizing
  plan's phase 5 guardrail can read.
* `wait_for_capacity()` with an injected poll, which phase 4's
  client-side retry should match the semantics of rather than
  reinvent -- the master plan's ordering puts phase 4 after this one
  for exactly that reason.
* A `# raw-create:` convention the sizing plan's phase 3 saturation
  tests need in order to assert a refusal.

## Back brief

Before implementation begins, the implementer confirms in writing:

* Which of D8's two halves they think is most likely to be wrong --
  the `cpu_limit` clause or the per-node `max` -- and what they would
  need to see to change it. This is the decision the phase rests on
  and the one a reviewer should push back on.
* That they have read `retries.py`'s docstring and understand why
  `wait_for_capacity()` cannot import the client.
* The exact call-site count they measured, before editing. If it is
  not 117 across 41 files, the tree has moved and the survey needs
  re-running before 2b starts.

**Gate:** step 2b touches 41 files mechanically and is expensive to
redo. It does not start until 2a has merged, or at minimum until
`create_instance()`'s signature is agreed -- a signature change after
2b means editing 117 call sites twice.
