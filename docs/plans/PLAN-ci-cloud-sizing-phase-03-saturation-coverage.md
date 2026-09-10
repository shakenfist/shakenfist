# Phase 3: explicit saturation coverage

Parent plan: [PLAN-ci-cloud-sizing.md](PLAN-ci-cloud-sizing.md).

**Planning effort:** high, as the master plan specifies. The
arithmetic in this phase is trivial; the judgement is entirely in
*where* the boundary is asserted. The obvious reading of the master
plan -- "fill a cluster to its ledger" -- turns out to be hostile to
the suite it would run inside, and the survey below is what changes
the shape of the phase.

Decision numbering continues the plan-wide sequence: phase 0 used
D1-D8, phase 1 D9-D15 and phase 2 D16-D22, so this phase begins at
**D23**.

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Flag any uncertainty explicitly rather than guessing.

Key references for this phase are
`shakenfist/scheduler.py` (the four capacity stages, and
`summarize_resources()` which publishes what they refuse on),
`shakenfist/external_api/instance.py` (the `507` branches of
`POST /instances`), `shakenfist/operations/node_inst_netdesc_op.py`
(the asynchronous re-place path and its
`Requested node lacks resources` abort),
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_nodes.py`
(the zero-cost placement primitive this phase reuses),
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py`
(the impossible-request refusal pattern, and the retry policy that
sits beside it) and
`shakenfist/deploy/shakenfist_ci/retries.py`.

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

## Context

Phase 0 built the scarcity inventory: every distinct failure
signature the current clouds produce *because* they are small, each
classified as a defect to fix, behaviour to assert, or a test bug.
Phase 2 measured what "small" means -- `slim-primary` carries a 27
vCPU ledger over six nodes and `slim-tier` a 12 vCPU ledger over
three, and on `slim-tier` at least one node sat at or above its own
ledger ceiling in **100% of the 50 job-runs sampled**, including
runs that passed.

Phase 3's job, in the master plan's words, is to convert the
coverage we get from scarcity by accident into coverage we get on
purpose, so that phase 4's bigger clouds cannot quietly close a
defect by hiding it. It gates phase 4.

The phase is worth doing for a second reason phase 2 made visible.
The suite's exposure to the capacity boundary today is *ambient*:
whether any given merge run exercises a refusal depends on how the
five stestr workers happen to interleave. Coverage that depends on
timing is coverage that reports differently on every run, which is
indistinguishable from a flake, and the response to a flake is to
retry it. Deterministic assertions at the boundary are what let
phase 4 change the cloud without anyone having to argue about
whether the suite got quieter because the system got better.

## Scope

**In scope:**

* Functional tests that reach each capacity refusal stage
  deliberately and assert the refusal contract the server offers
  today.
* Unit coverage for the stage predicates that a functional test
  cannot force deterministically (D25).
* A single helper expressing the refusal contract, so that the
  sibling plan's phase 4 changes it in one place (D27).
* Widening the capacity guard census filter in `shakenfist/actions`
  by the two events that say what became of a denial -- phase 2
  handed this over explicitly (D29).
* Re-checking phase 0's inventory against phase 2's census and
  against the issue tracker as it stands today, and updating the
  dispositions in place (D30).
* Issues filed for anything these tests expose.

**Out of scope, deliberately:**

* Any topology change. That is phase 4, which this phase gates.
* Any change to what the scheduler admits, or to the refusal
  contract itself. Making a capacity refusal transient -- a
  `Retry-After`, a machine-readable body, a client retry, server
  side queueing -- belongs to
  [PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md),
  and the admission ledger and claims belong to
  [PLAN-scheduler-reservations.md](PLAN-scheduler-reservations.md).
  This phase asserts what exists and changes none of it.
* The headroom band and any gate built on it. Phase 5 owns those.
* Documenting the sizing model. Phase 6 owns that; new tests get
  their own docstrings and nothing more.
* Chasing #3565's scheduler question. It closed as a test bug on a
  test change (scheduler-reservations phase 6), and phase 0's row
  was corrected to say so on 2026-09-01.
* **The deterministic affinity reproduction phase 0's #3565 row
  still asks phase 3 for**, and this exclusion was missing until
  step 3f found it. The bullet above excludes *chasing the scheduler
  question*, which is not the same thing: phase 0's row, even after
  its 2026-09-01 correction, says "the deterministic reproduction
  phase 3 owes it is still worth having -- it is now coverage of a
  documented guarantee rather than a hunt for a bug". This phase
  does not write it, and the reason is D23. A reproduction of the
  kind phase 0 describes -- fill the affinity target, then place --
  has to fill a *named* node chosen by the affinity test rather than
  the roomiest one, so it cannot use D26's "skip unless this node is
  comfortably free" escape: if the target is busy the test has
  nothing to do but wait or fail. That is a different risk profile
  from 3c, and pricing it needs the merge-run evidence 3c is about
  to produce about how often a single-node fill skips. **Phase 4
  inherits it as an explicit debt, not as an oversight.**
* The master plan's other *Future work* entries -- the under-cloud
  probe, per-suite concurrency, `test_coalescing`'s burst, the two
  uninstrumented cluster jobs.

## What the survey found

The master plan's phase 3 section was written on 2026-08-27, before
phases 1 and 2 executed and before four of the five issues it
argues from were closed. Nine findings, and the four that are false
claims rather than new information were corrected at their source
in the planning commit -- do not redo them.

**F1 -- Four of the inventory's five issue anchors are now closed,
including the umbrella the phase is framed around.** #3772 closed
**2026-09-09T13:09:41Z** as `COMPLETED`, #3496 on 2026-08-29,
#3696 on 2026-09-05; #3813, #3565 and #3907 were already closed
when phase 0 wrote the table. Of the six, only #3718 (the second
shape of the runner-communication family, out of scope per D8) was
still open when this plan was written. The master plan's phase 3
section says "the issue records what it should do, so growing the
clouds cannot quietly close it" -- and at that point no open issue
held that record. **#3772 has since been reopened; see the
resolution below.**

Two further facts about #3772's closure, because they change what
this phase can assume:

* **No commit claims it.** No commit on `develop` carries `Fixes
  #3772`. The nearest change is `09a8975db` ("Force a capacity
  reconcile when a hypervisor is unguarded", the #4087 warm-up
  fix), whose message says only "Related to #3772 and #4087". The
  issue was closed by hand with no closing comment, roughly six
  hours after that fix merged.
* **Its last two comments are recurrences on the day it closed** --
  2026-09-09 at 10:08 and 11:22 UTC, both `507 sufficient_idle_cpu`
  in merge groups, one of them with the post-run census showing a
  node's committed vCPU at peak fraction 1.000. So the signature was
  still occurring hours before the issue was closed.

**Resolved 2026-09-10: the closure was an error, and #3772 is
reopened.** The back brief asked the operator which reading was
intended, because if #3772 had been closed as *fixed* then this
phase's tests would be asserting a contract someone expects to
change. It was not. Three recurrences postdate the closure --
[34398770295](https://github.com/shakenfist/shakenfist/actions/runs/34398770295)
on `fd617fe72`,
[34426395717](https://github.com/shakenfist/shakenfist/actions/runs/34426395717)
and
[34433668437](https://github.com/shakenfist/shakenfist/actions/runs/34433668437)
on `cd379c627` -- all three in merge groups for changes that cannot
reach admission (a keyed `cluster_config` read, and two Renovate
type-stub bumps). The second is decisive, because its census
separates the two candidate causes: capacity-row coverage was
complete **0 seconds** after the first sample, so #4087's warm-up
fix was live, and yet two of the three hypervisors sat at
committed-vCPU peak *and* p90 fraction 1.000 for the whole
1665-second window. That is genuine exhaustion of the admission
ledger, not a node recorded above its limit during an unguarded
window. The issue was reopened carrying that evidence, and the
`automated-fix-attempted` label was left in place so the issue-fix
workflow does not race this phase.

So the record is held by the issue *and* by
[PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md),
which owns making a refusal transient and which this plan does not
touch; that plan's *Related issues* section describing #3772 as
open is correct again, and needs no edit. **This phase's tests
assert today's behaviour unchanged, and 3a's helper docstring is
right about what will change it.**

One thing in the evidence did move, and it weakens an assumption
made elsewhere in this plan. The 2026-09-08 research into #3772
found that every post-#4106 507 was a `force_placement`
single-candidate create onto one of the 3-vCPU infra nodes
(`NODE_CPU_RESERVATION_THREADS=4` on a 4-thread node yields
`cpu_schedulable=1`, hence limit 3) *while the cluster still held
3-9 of its 12 vCPU*. The runs above have far less slack than that:
two nodes pinned for a whole window. D23 sizes its fill from the
node's own `cpu_limit - cpu_committed` and so still works, but the
risks table's reading -- that one node can be filled without
disturbing the other four workers -- is less safe than when it was
written, and D26's skip is doing more of the work than the table
credits it with.

**F2 -- A stale line reference.** Phase 0's inventory cites
`_has_idle_disk_bandwidth()` at `shakenfist/scheduler.py:329`; it is
at `shakenfist/scheduler.py:495`. Corrected at source.

**F3 -- The fill primitive already exists, and it is cheap.**
`test_cluster_resources_charges_unbooted_placements`
(`cluster_ci_tests/test_nodes.py:94`) reads `/admin/resources`
through the admin client, picks the hypervisor with the most
`cpu_available`, and creates a 1 vCPU / 128 MB instance with a
single empty 1 GB disk, no base image and `force_placement=` that
node -- then deliberately does *not* wait for it to boot, because
the point is to read the ledger before any domain exists. Nothing
is downloaded and nothing boots, so each unit of fill costs a
database write and a vCPU of ledger. Phase 3's fill loop is that
call repeated, which means its cost is already known rather than
guessed at.

**F4 -- The suite can size a fill exactly.**
`summarize_resources()` publishes, per node,
`cpu_limit` (the capacity row's own `limit_cpus`, or `None` when the
row is absent -- `scheduler.py:1078`), `cpu_committed`,
`cpu_available`, `cpu_hard_max` and `cpu_committed_row_present`, and
publishes `total.capacity_degraded` (phase 2's D19). The client
exposes it as `get_cluster_resources()`
(`client-python/shakenfist_client/apiclient.py:1646`), and
`base.BaseTestCase.system_client` is an admin client. So a test can
compute exactly how many one-vCPU instances a node will take, and
can tell "the table says this node is full" from "the table could
not be read" -- which is the distinction phase 2 added the flag for.

**F5 -- `sufficient_idle_disk` has no deterministic functional
trigger.** `_has_idle_disk_bandwidth()` refuses when the node's
`DISK_BUSY_PER_SECOND_METRIC` exceeds 1200 ms/s
(`scheduler.py:495-512`). That metric is measured, republished on
the resources daemon's 60 second cadence, and is not carried in
`/admin/resources` at all, so a test can neither force it nor
observe it. Phase 0's instruction that "phase 3's saturation test
must cover this stage directly" is not achievable as written.
Corrected at source, pointing at D25.

**F6 -- None of the four stage predicates has a unit test naming
it.** `shakenfist/tests/test_scheduler.py` mentions
`_has_sufficient_disk` only inside a comment (line 1199) and does
not name `_has_sufficient_cpu`, `_has_sufficient_ram` or
`_has_idle_disk_bandwidth` at all. The cheapest coverage in this
phase is the coverage nobody has written.

**F7 -- There is no serialisation seam in the suite, and this is
the finding that reshapes the phase.** `cluster-ci.conf` sets only
`test_path` and `top_dir`, so there is no `group_regex`: stestr
distributes the cluster suite freely across five workers on one
cluster. A test that fills the cluster to its ledger starves the
other four workers for as long as it holds the fill, and the
failure they would then report is `507 sufficient_idle_cpu` -- the
exact signature this plan exists to stop being accidental. On
`slim-tier` it is worse than hostile, it is close to a no-op: phase
2 measured a node at or above its ledger ceiling in 100% of
`slim-tier` job-runs already, so "fill the cluster" there means
"fill what four other workers have not filled yet", which is not a
quantity a test can name.

**F8 -- One inventory row is already discharged.**
`test_claim_lifecycle_and_refusals`
(`cluster_ci_tests/test_namespace_claims.py:440`) already asserts
507 on a claim the cluster cannot promise, using an
`IMPOSSIBLE_CPUS` request that consumes nothing, and the file's
header documents why success assertions retry 507 while refusal
assertions must not. That is precisely the "keep a test that
exercises the refusal path directly" the #3907 row asked phase 3
for. Phase 3 should record it as met rather than write a second
one -- and, more usefully, should copy its shape (D24).

**F9 -- Sibling-plan bookkeeping, reported not corrected.**
`PLAN-transient-capacity-refusals.md` lists its phase 1 (close the
warm-up window) as `Not started`, but that change is on `develop`
as `09a8975db`, having landed through the `issue-fix-4087` branch,
and a `transient-capacity-refusals-phase-01-warm-up` worktree still
exists. Not this plan's table to fix.

Everything else the survey checked held: the four stage names in
`scheduler.py` are as phase 1 corrected them, the topology matrix
and its concurrency of 5 are as phase 2 described
(`.github/workflows/functional-tests.yml:440-482`), and the ledger
figures in the master plan's *Situation* section still match the
committed dataset.

## Decision items

### D23 -- Assert at the node boundary, not by filling the cluster

The master plan says "fills a cluster to its ledger". Phase 3 fills
**one hypervisor** to its ledger instead, by repeated
`force_placement` creates of the F3 shape, sized from that node's
published `cpu_limit - cpu_committed`.

Three reasons, in order of weight:

1. **Blast radius.** F7: filling the cluster starves the other four
   workers and manufactures the failure signature the plan is
   trying to make deliberate. Filling one node of three or five
   leaves the rest of the suite a working cluster.
2. **It is where admission actually binds.** Phase 2's headline
   finding is that the cluster-wide committed fraction and the
   per-node maximum can differ by a factor of two, and that it is
   the per-node figure the scheduler refuses on. A test that fills
   "the cluster" is asserting against the statistic that does not
   decide anything.
3. **It is nameable.** A node's remaining ledger is a number the
   API publishes. The cluster's remaining ledger, in a suite where
   four other workers are creating and deleting, is not.

The cost of the decision: the phase does not directly assert "every
node in the cluster refused, therefore the create failed with 507".
D24 covers that case by another route.

### D24 -- The cluster-wide refusal is asserted with an impossible request

Copy F8's shape. A create whose requested resource exceeds any
node's published ceiling is refused at the corresponding stage, and
consumes nothing while being refused -- so it is deterministic, it
is independent of ambient load, it runs in every job on every
topology, and it can never starve another worker.

This gives deterministic coverage of three stages:

| Stage | Impossible request | Sized from |
|-------|--------------------|------------|
| `sufficient_idle_cpu` | vCPUs greater than `max(cpu_limit)` over all nodes | `/admin/resources` |
| `sufficient_idle_memory` | memory greater than `max(ram_max)` | `/admin/resources` |
| `sufficient_free_disk` | a disk larger than `max(disk_available) + sum(disk_available)` | `/admin/resources` |

The sizes are read from the API rather than hardcoded, because a
hardcoded "impossible" number is a number phase 4 can make possible.

*Correction (2026-09-10, found implementing 3b):* the memory and
disk rows of that table originally named `max(ram_available)` and
`max(disk_available)`. Those are **headroom** figures, and headroom
moves *upward* whenever a sibling stestr worker deletes an instance
or a blob. A request sized one unit beyond the largest headroom at
read time therefore becomes satisfiable the moment any worker frees
more than one unit on that node -- the create is admitted, no
exception is raised, and the test fails. That is the "the new tests
become the flake source" risk in the table below, introduced by this
decision's own sizing rule. The CPU row was already correct because
`cpu_limit` is a **ceiling**: it is what the node is guarded to,
whether it is idle or full.

The rows above are the corrected sizings. Memory has a published
ceiling, `ram_max` (`scheduler.py:1109-1111`,
`memory_max * RAM_OVERCOMMIT_RATIO`), and a guarded node can be
bounded below it by its row's `limit_memory_mb`, recoverable as
`ram_available + ram_committed`; the test takes the larger of the
two so the request exceeds whichever ceiling binds. Disk has **no
published ceiling at all** -- nothing in `summarize_resources()`
publishes a node's total disk -- so the disk test cannot be made
load-proof the way the other two are. It uses the cluster's whole
free-disk total as a margin, which exceeds what the largest node
could reach even if every other node's free space were released onto
it, and its docstring says plainly that this is a generous margin
rather than a proof of impossibility.

D23 and D24 are complementary and both are needed: D24 proves the
refusal *contract* (which status, which stage name, which event),
D23 proves the *ledger* is what produces it -- that a node with real
capacity, filled with real placements, starts refusing at exactly
the published limit. Only D23 would catch a regression where
admission stopped charging placements.

### D25 -- `sufficient_idle_disk` gets unit coverage, not functional

This overrules phase 0's inventory, which said phase 3's saturation
test "must cover this stage directly". F5 is the reason: the
predicate reads a measured rate on a 60 second cadence that the API
does not publish, so no functional test can force it and none can
observe why it did or did not fire. A test that tries would be a
test that passes for the wrong reason most of the time.

Instead: a unit test of `_has_idle_disk_bandwidth()` pinning both
sides of the 1200 ms/s threshold and the shape of the reason dict,
which is the part a later refactor can silently change. Phase 0's
row is corrected at source to say this.

The honest cost, written down rather than glossed: the stage that
phase 0 singled out as *the one sizing cannot fix* is the stage
this phase covers least. If it recurs after phase 4, the trace will
come from the phase 1 census, not from a test.

### D26 -- Every new test skips rather than fails when the cluster is already busy

A saturation test that fails because another worker took the
capacity first is exactly the flake this plan exists to remove, and
it would be a flake we introduced. Every test added by this phase:

* Reads `/admin/resources` first and calls `skipTest()` with a
  message naming the figure it wanted, when the headroom it needs
  is not there -- following the existing idiom at
  `test_nodes.py:111` (`'No hypervisor with two vCPUs of headroom'`).
* Skips when `total.capacity_degraded` is true, or when the target
  node reports `cpu_committed_row_present` false. A refusal from an
  unreadable ledger is not the refusal being asserted, and phase 2
  built the flag precisely so this is answerable rather than
  guessed.
* Releases its fill before returning, and relies on
  `BaseNamespacedTestCase`'s namespace teardown as the backstop for
  the case where it does not return.
* Carries a deadline. `retries.py`'s `retry_while_transient()` is
  the existing primitive for "wait while the answer is transient",
  and refusal assertions must **not** use it -- the claims file's
  header (`test_namespace_claims.py:35-52`) already explains why a
  caller asserting a refusal must not retry the refusal away.

### D27 -- One helper expresses the refusal contract

Today's contract, from `scheduler.py:540` and the create path:

* HTTP **507**, from `external_api/instance.py:901-906`, raised
  as `exceptions.LowResourceException`.
* Message `No nodes remaining at scheduling stage <stage>`.

*Correction (2026-09-10, found implementing 3c):* there is a
**second** 507 with a different message. When a stage pre-filter
prunes every candidate the message above is raised; when the
pre-filter passes a candidate and the atomic capacity guard inside
`Instance.place_instance()` then refuses it, the create path returns
507 carrying `no node had capacity for this instance, N candidates
refused it` (`external_api/instance.py:975-982`). The helper matches
only the first, deliberately -- the two mean different things, and
conflating them would let a guard refusal satisfy an assertion about
a stage refusal. See D28 for what a test does when it meets the
second.
* An audit event `schedule has no candidates at stage <stage>,
  aborting`, carrying `candidates` and `dropped` in its extra.

Every assertion in this phase goes through one helper --
`assertRefusedAtStage(response, stage)` or equivalent -- rather
than repeating the string. This is not tidiness. The sibling
plan's phase 4 adds `Retry-After` and a machine-readable transient
refusal to exactly this contract, and its phase 2 changes what the
suite does when it meets one. When that lands it should edit one
helper, not eight tests, and the helper is the place where "what
the contract was in September 2026" is written down for whoever
changes it.

### D28 -- The targeted-create-at-a-full-node test records the answer, it does not predict it

Phase 0's #3496 row asks phase 3 to "assert the documented
behaviour of a targeted create against a full node". There are two
paths and the plan deliberately does not guess which one a filled
node produces:

* The synchronous create path can refuse with 507 before the
  instance exists.
* The asynchronous re-place path aborts with
  `Requested node lacks resources`
  (`operations/node_inst_netdesc_op.py:207`), which surfaces as an
  errored instance rather than as an HTTP status.

*Correction (2026-09-10, found implementing 3c):* there are
**three** paths, not two. The first bullet above is really two, per
D27's correction: the stage pre-filter's 507 and the capacity
guard's 507 carry different messages. The distinction is not
cosmetic. `_has_sufficient_cpu()`'s docstring
(`scheduler.py:321-350`) says it is "a cheap CPU pre-filter (P2) ...
not the admission decision", and that it reads the capacity row's
`limit_cpus` and charges `max(measured_cpus, committed_cpus)`
precisely so it sees what the guard sees. A node deliberately filled
to its `cpu_limit` therefore fails the pre-filter, and the **stage**
message is the expected answer. Reaching the guard instead means the
pre-filter believed there was room -- that the ledger the test sized
itself from was already stale. That is not a refusal to assert and
not a failure: it is an invalid premise, and 3c skips on it, saying
so. The third path stays as this decision describes it.

The implementing step observes which occurs, asserts that, and
writes a sentence in the test explaining which path produced it. If
it turns out to be the second, that is worth a paragraph in the
phase's close-out: an operator asking for a specific node and
getting an errored instance several seconds later is a materially
different user experience from a refusal at request time, and the
sibling plan would want to know.

### D29 -- Widen the census filter in this phase

Phase 2's *What phase 3 inherits* hands this over by name, and the
master plan's *Future work* carries the entry. Two more
alternations in the LogQL filter in the `shakenfist/actions`
repository, beside the guard denial the filter already matches:

* `no candidate admitted and some refused on demand alone, waiving
  demand guard`
* `schedule failed, every candidate refused by capacity guard`

The second is the event that actually says the cluster refused a
create at the guard, which is what makes a saturation test's
observation checkable against the census rather than only against
its own assertions. Phase 2 measured 3,480 denials across 32
job-runs and could not say how many ended in a failed create; after
this it can.

This step lands in another repository, cannot be tested before it
merges, and only the operator can push it -- the same seam phase 2
met at its step 2e. It is sequenced early so a merge run has used
it before the phase closes.

### D30 -- The inventory is re-checked and updated in place

Phase 2's inheritance asks for this explicitly: phase 0 built the
inventory from triage history, and phase 2's census is the first
chance to see whether the frequencies match. Every row gets its
issue state refreshed (F1), its disposition confirmed or changed
against the census, and -- for the rows that name a test phase 3
owes -- a link to the test that now discharges it. A row whose
disposition survives contact with the data is a result worth one
sentence; a row that does not is the more valuable finding.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Add the refusal-contract helper (D27) to `shakenfist/deploy/shakenfist_ci/base.py`, beside the other `assert*` helpers at `base.py:1055-1099`. It takes the `InsufficientResourcesException` (or status/body pair) the client raised and the expected stage name, and asserts status 507 and the message `No nodes remaining at scheduling stage <stage>` produced at `scheduler.py:540`. Give it a docstring recording that this is the contract as of 2026-09, that `PLAN-transient-capacity-refusals.md` phase 4 will add `Retry-After` and a machine-readable body to it, and that this helper is the single place to change when it does. Do not use `retries.py` here: a refusal assertion must never retry the refusal away, for the reason `cluster_ci_tests/test_namespace_claims.py:35-52` gives. No test changes in this step. |
| 3b | medium | sonnet | none | New file `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_saturation.py` implementing D24: three tests, one per stage in D24's table, each reading `self.system_client.get_cluster_resources()`, computing a request one unit beyond the largest value any node publishes, issuing the create, and asserting through 3a's helper. Model the impossible-request shape on `IMPOSSIBLE_CPUS` in `test_namespace_claims.py:440-467`. These consume nothing, so they need no fill and no cleanup beyond the namespace teardown `BaseNamespacedTestCase` already does. Apply D26's skip rules: skip when `total.capacity_degraded` is true, and when `per_node` is empty. Use `base.BaseNamespacedTestCase`. |
| 3c | high | opus | worktree | The node-fill test (D23), added to `test_saturation.py`. Pick the hypervisor with the largest `cpu_available` from `/admin/resources`, skip per D26 if it does not have at least three vCPU of headroom or if `cpu_committed_row_present` is false for it. Fill it with `cpu_limit - cpu_committed` one-vCPU instances using exactly the zero-cost create shape at `test_nodes.py:116-122` (1 vCPU, 128 MB, no base image, a single `{'size': 1, 'type': 'disk'}`, `force_placement=` the node name, and no wait for boot). Re-read `/admin/resources` and assert the node now publishes `cpu_available` at or below zero and `cpu_committed` at its `cpu_limit`; then assert that one more `force_placement` create at that node is refused, through D28's rule -- observe which of the two paths answers (507 at request time, or an errored instance carrying `Requested node lacks resources` from `operations/node_inst_netdesc_op.py:207`), assert that, and say in a comment which one it was and why. Delete the fill in the test body, not only in teardown, and assert the ledger returns. This is the step where getting it wrong costs a merge-queue flake for everyone, so mutation-test each assertion: break the thing it claims to prove and confirm it fails. |
| 3d | medium | sonnet | none | Unit tests in `shakenfist/tests/test_scheduler.py` for the four stage predicates, which have none today (F6): `_has_sufficient_cpu` (`scheduler.py:321`), `_has_sufficient_ram` (`scheduler.py:391`), `_has_sufficient_disk` (`scheduler.py:467`) and `_has_idle_disk_bandwidth` (`scheduler.py:495`). For each, pin both sides of the boundary and the shape of the reason dict it returns, since the reason dict is what the audit event publishes and what a later refactor can silently change. `_has_idle_disk_bandwidth` is the one D25 makes load-bearing: pin the 1200 ms/s threshold from both directions. Follow the existing fixture style in that file; mock metrics the way the surrounding tests do. |
| 3e | low | sonnet | none | In the `shakenfist/actions` repository, widen the capacity guard census LogQL filter by the two alternations named in D29. Change nothing else. The operator pushes this; it cannot be tested before it merges. Report the exact diff for review rather than assuming it can be verified locally. |
| 3f | high | opus | none | Re-check phase 0's scarcity inventory against phase 2's dataset and today's issue tracker (D30), and rewrite the dispositions in `docs/plans/PLAN-ci-cloud-sizing-phase-00-decisions.md:253-298` in place. For each row: refresh the issue state, say whether the census frequency in `docs/plans/data/ci-cloud-sizing-baseline/` supports the disposition phase 0 gave it, and link the test from 3b/3c/3d that discharges it. Record that #3907's row was already discharged by `test_claim_lifecycle_and_refusals` (F8) rather than by anything this phase wrote. File issues for anything 3c exposed. Do not re-litigate #3565, which is out of scope. |
| 3g | medium | sonnet | none | Close-out: set this phase `Complete` in both the master plan's Execution table and the `docs/plans/index.md` row (3 of 7 becomes 4 of 7), write the *What phase 4 inherits* section against what was actually found, run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`, and confirm every Definition of done item below by running it rather than by reading it. |

Steps 3a, 3d and 3e are independent of each other. 3b needs 3a.
3c needs 3a and should follow 3b, because 3b proves the helper
against a refusal that costs nothing before 3c depends on it during
a fill. 3f needs 3b and 3c to have merged and a real merge run to
have used them. 3g is last.

## Risks and mitigations

| Risk | Mitigation | Who checks |
|------|-----------|------------|
| The fill in 3c starves the other four stestr workers and manufactures the very 507s this plan exists to remove. | D23 bounds the fill to a single hypervisor, and D26 skips when that node is not comfortably free. On `slim-tier` (three nodes, 12 vCPU) filling one 6 vCPU node is half the cluster, which is the worst case; if the merge-run evidence after 3c shows a rise in other tests' 507s, the test is restricted to `slim-primary` by a topology skip rather than kept and tuned. **Amended 2026-09-10:** F1's newest runs show two of three `slim-tier` hypervisors pinned at their ceiling for a whole run, so the headroom this row assumed is not reliably there and D26's skip, not the single-node bound, is the load-bearing mitigation. Expect 3c to skip often on `slim-tier`; a 3c that never skips there is evidence the skip predicate is wrong, not that the cluster is roomy. | The operator, over the merge runs following 3c, against the phase 1 census. |
| The new tests become the flake source. | Every test skips on ambient shortage (D26), and the Definition of done requires clean merge runs after landing, not just a green branch. | 3g, and the operator. |
| A test asserts a refusal produced by an unreadable capacity table rather than by a full one, and passes for the wrong reason. | D26 skips on `capacity_degraded` and on a missing capacity row -- the exact distinction phase 2's D19 added the flag to make answerable. | 3b and 3c briefs; mutation testing in 3c. |
| The refusal contract changes under us when the sibling plan's phase 4 lands, and eight tests need editing. | D27's single helper, and its docstring naming the plan that will change it. | 3a. |
| ~~#3772 was closed as *fixed* rather than *superseded*, so this phase asserts behaviour someone believes has changed.~~ **Retired 2026-09-10:** the closure was an error and the issue is reopened, so the contract this phase asserts is not expected to change. See F1. | n/a. | Resolved before 3a. |
| 3e cannot be verified before it merges, the same seam that made phase 2's 2e awkward. | Sequenced early, reviewed as a diff, and confirmed from a real merge run's census section before 3f reads the census. | The operator. |

## Definition of done

Falsifiable, in order:

1. `shakenfist/deploy/shakenfist_ci/base.py` carries one helper
   asserting the refusal contract, and `grep -c 'No nodes remaining
   at scheduling stage'` over `shakenfist/deploy/shakenfist_ci/`
   returns 1.
2. `cluster_ci_tests/test_saturation.py` exists and contains a test
   per stage in D24's table, each of which passes on a cluster with
   no free capacity at all -- because an impossible request is
   refused either way.
3. Every test added by this phase has an explicit `skipTest()` path
   for ambient shortage and for `capacity_degraded`, verified by
   reading the diff and not by the test passing once.
4. The node-fill test releases its fill in the test body, and a run
   with the release deleted leaves the node's `cpu_committed` above
   its starting value -- that is, the release is asserted, not
   assumed.
5. Each assertion added by 3c has been mutation-tested: the
   assertion fails when the property it claims to prove is broken.
   The step reports which mutation it applied for each.
6. `_has_sufficient_cpu`, `_has_sufficient_ram`,
   `_has_sufficient_disk` and `_has_idle_disk_bandwidth` are each
   named by at least one unit test, both sides of their boundary.
   `grep -c` over `shakenfist/tests/test_scheduler.py` for each name
   is at least 1, where today three of the four are 0.
7. A merge run after 3e shows the *Capacity guard census* section
   reporting a count for `schedule failed, every candidate refused
   by capacity guard`, not a "not collected" notice. The operator
   confirms this; it cannot be checked from a worktree.
8. Phase 0's inventory has no row whose issue state is stated
   wrongly, and no row citing a source line that has moved. Checked
   by re-reading each citation.
9. Every inventory row classified "behaviour to assert" names the
   test that now asserts it, or says explicitly why no test does
   (which for `sufficient_idle_disk` is D25).
10. No test added by this phase can fail because of another
    worker's load: for each, the failure modes are enumerated in
    its docstring and each is either asserted or skipped.
11. The master plan's phase 3 section describes what this phase did
    rather than what it was expected to do in August, and every
    statement it makes about #3772's state matches
    `gh issue view 3772 --json state,stateReason` on the day the
    phase closes out.
12. `python3 tools/check-plan-status.py` passes, and `pre-commit
    run --all-files` passes in the main repository.

## Outcome

Every step has landed except the parts which by the plan's own
ordering cannot: 3a, 3b, 3c, 3d and the reachable half of 3f are
committed; 3e is written but sits uncommitted in the
`shakenfist/actions` repository, because only the operator pushes
there.

**The phase is not Complete, and its status rows still say
`In progress` deliberately.** Two Definition of done items cannot be
satisfied before this branch merges and CI runs it, and marking the
phase done on a green branch would be exactly the "a status column is
a claim, not evidence" failure that the phase-completion check exists
to catch. What remains is named below, not implied.

Each Definition of done item was run rather than read:

| # | Result | How it was checked |
|---|--------|--------------------|
| 1 | **pass** | `grep -rc` over `shakenfist_ci/` returns the stage string once, in `base.py` alone |
| 2 | **partial** | The file exists with four tests covering all three of D24's stages. That they pass *on a cluster with no free capacity* is the half no worktree can check |
| 3 | **pass** | Read from the diff: 19 `skipTest()` paths, 8 `capacity_degraded` references |
| 4 | **pass** | Mutation tested: with the in-body release deleted, the ledger assertion fails rather than the test passing |
| 5 | **pass** | 3c reported 33 mutations across both CI topologies and a fractional-limit one, with no unexpected outcome |
| 6 | **pass** | All four predicates are named by unit tests; three of the four were zero before |
| 7 | **pending the operator** | Needs a merge run carrying 3e, which is unmerged. Unchanged from what the item already said |
| 8 | **pass** | Every `file:line` citation in the inventory re-resolved, and every issue state refreshed against `gh` |
| 9 | **pass** | 3f links a discharging test per row, or states why none exists |
| 10 | **pass** | Read from the diff: each test's docstring enumerates its failure modes, each marked asserted or skipped |
| 11 | **pass** | `gh issue view 3772` reports `OPEN`/`REOPENED`, which is what the master plan's phase 3 section now says |
| 12 | **pass** | `check-plan-status.py` agrees; `pre-commit run --all-files` passes all ten hooks |

What close-out still owes, once this merges and a merge run uses it:

* **Item 7**, the census section reporting a count rather than a
  "not collected" notice, which needs 3e pushed first.
* **Item 2's other half**, and with it the question 3c was written to
  answer and could not: which of the three refusal paths a real full
  node actually gives. 3c records it with `addDetail`, so the first
  merge run answers it -- read that detail rather than the reasoning
  in 3c's docstring, which is argument, not observation.
* **The skip rate on `slim-tier`.** The plan predicts 3c skips often
  there and says a 3c which never skips is evidence the predicate is
  wrong. Nobody can know yet.
* **Whether the fill disturbs the other four stestr workers**, which
  is D23's whole premise and which F1's amendment already weakened.
* **The cost of 3c's ownership listing** against
  `load_budget.py`'s database-load budget. It lists every instance in
  the cluster, once on the happy path and once per release poll, and
  was not measured.

Only after those are answered should the Execution table and
`docs/plans/index.md` move to `Complete` and `4 of 7`.

## What phase 4 inherits

* Deterministic assertions at three of the four capacity stages
  that run in every cluster job on every topology, so a topology
  change that accidentally makes a refusal unreachable fails a test
  rather than making the suite quieter.
* One place -- the D27 helper -- where the refusal contract is
  written down, so phase 4 can see at a glance what it must not
  change and the sibling plan can see what it is changing.
* A test that proves the per-node ledger is what admission refuses
  on, which is the property phase 4's resizing is arithmetic over.
  If phase 4 changes `cpu_limit` and the fill test still passes at
  the new number, the resize did what it claimed.
* An inventory whose dispositions have been checked against
  measured frequencies rather than against triage memory, and a
  census that can say what became of a guard denial.
* A written answer on `sufficient_idle_disk`: it is the stage
  sizing cannot fix *and* the stage no functional test can force,
  so if it recurs after phase 4 the evidence will be census data.
* **One debt, named rather than dropped:** the deterministic
  affinity reproduction phase 0's #3565 row asks for, which this
  phase declines in its Scope section with the reasoning. It is the
  only inventory row that names a test phase 3 owes and still has
  none; everything else in the table is discharged by a test that
  now exists, or explicitly cannot be.
* **Two questions only a merge run answers**, both recorded at their
  rows by 3f: which of the three refusal paths a real full node
  gives 3c (the #3496 row), and how often the demand guard's waive
  fails to save a create, which needs a run carrying 3e's widened
  census filter (the #3813 row). 3f assessed dispositions against
  measured frequencies, but no test this phase wrote has ever
  executed, and nothing in the inventory claims otherwise.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan, and in particular on:

* **F1 -- why #3772 was closed. Settled 2026-09-10, before 3a: the
  closure was an error and the issue is reopened.** The question was
  whether the #4087 warm-up fix was thought to have *fixed* it, in
  which case some of these tests would assert a contract expected to
  change and 3a's helper docstring would be wrong about what changes
  it. Three recurrences postdating the closure answer it -- one with
  the warm-up fix demonstrably live and two hypervisors at their
  ledger ceiling for the whole run. The tests assert today's
  behaviour unchanged, as planned. No gate remains here; F1 carries
  the evidence and the one assumption it weakened.
* **D23 and D24 together**, which replace the master plan's "fill a
  cluster to its ledger" with "fill one node, and prove the
  contract with a request no cluster could satisfy". This is the
  decision that changes what the phase *is*, and it is the one a
  reviewer is most likely to disagree with -- the objection being
  that nothing then asserts the whole-cluster exhaustion path end
  to end. The counter-argument is F7: on `slim-tier` that path is
  ambient in 100% of runs already, and a test which tries to own it
  is competing with four other workers for a quantity it cannot
  name.
* **D25**, which overrules phase 0 on the one stage phase 0
  singled out. Worth an explicit yes or no, because it is a
  reduction in scope against a decision the operator previously
  approved.
* **D29's** `shakenfist/actions` change, which only the operator can
  push and which cannot be verified before it merges.
