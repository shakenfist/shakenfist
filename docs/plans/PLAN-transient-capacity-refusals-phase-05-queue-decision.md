# Phase 5: Decide on server-side queued placement

Parent plan:
[PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md).

**Planning effort:** high, as the master plan specifies -- "the
fifth because it may be a state-machine design". It may also be no
design at all, and which of those it is cannot be known until the
data arrives. What this plan does, therefore, is fix the rule that
will read the data *before* the data exists, so the decision is made
by the rule rather than by whoever is reading the numbers on the day.

This phase continues the plan's decision sequence at **D36**; phases 1
to 4 used D1-D35.

## Context

Master plan open question 8 asks whether the server should queue a
create that does not fit, and defers the answer to this phase, to be
read off phase 2's wait data. The master plan's own framing is that
the machinery already exists, that the change is contained, and that
the arguments against are fairness, IPAM, and a client deadline that
bounds any useful server-side wait.

The phase is a decision, not a build. Its two possible outcomes are
stated in the master plan: close as `Abandoned` with the numbers and
the reasoning, or design the queue and record the reversal of
scheduler-reservations D8 before any code is written.

## Scope

**In scope:**

* The gate: establishing that the data this phase reads describes the
  cloud the project intends to run, rather than the one it is
  replacing (D36).
* Pre-registering the rule that converts wait data into a decision,
  with numeric thresholds, before the data is read (D37).
* Reading the phase 2 capacity-wait traces over the qualifying merge
  runs with `tools/ci_headroom_report.py --waits`, and recording the
  distribution in this plan.
* Taking the decision, and writing it and its evidence into this plan
  and into the master plan's open question 8.
* Correcting, at source, the four claims in open question 8 and the
  phase 5 section that the survey below found to be false (D41).

**Out of scope, explicitly:**

* **Designing the queue.** If the decision is to build, the design is
  its own phase, planned at high effort against the state machine.
  This plan decides; it does not design (D42). A plan that both
  decided and designed would have written the design before knowing
  whether it was wanted.
* **Implementing anything.** No code change is in scope on either
  outcome. The only file edits this phase makes are to plan documents.
* **The demand guard, the pre-filters, claim enforcement and the
  guarded `UPDATE`.** These are scheduler-reservations' (its D11 and
  phase 5), as the master plan's ordering note says.
* **The topology shape.** That is the sizing plan's phase 4, which
  this phase waits on rather than participates in.
* **Re-tuning `CLUSTER_HEADROOM_WAIT`.** The suite's client-side
  deadline is phase 2's, and changing it would move the denominator
  this phase's rule is expressed in. If the data argues for a
  different value, that is recorded as a finding for phase 6, not
  applied here.

## What the survey found

The survey checked every factual claim open question 8 and the phase 5
section make. **Four of them are wrong**, one of them in the direction
that matters: the constraint the master plan names as the binding
ceiling on any queue deadline is 300 seconds larger than the real one.

### F1 -- The client's ceiling is 600 s, not 900 s, and the method has a different name

Open question 8 says "the client's `_await_instance_create` has a
900 s ceiling that bounds any useful deadline".

The method is `await_instance_create()` -- no leading underscore --
at `client-python/shakenfist_client/apiclient.py:1797`, and its
default timeout is **600**, not 900. There is no 900 anywhere in
`shakenfist_client`. The docstring explains the number: "The default
of ten minutes is generous because on a slow morning it can take over
two minutes just to download a Ubuntu image."

The correction is not cosmetic. Any server-side hold must fit inside
the caller's budget, and the real budget is a third smaller than the
master plan reasons from.

Worse for the queue proposal, the same docstring says the budget does
not compose the way the master plan assumes: "This is a budget for
this call alone. A caller that created the instance itself should pass
`timeout=0` to `create_instance`, or the two waits run back to back on
the same condition and the task takes the sum of them while reporting
only this one." `create_instance(timeout=...)` at `:737-749` bounds
"the whole call -- the dependency retries on the POST as well as the
wait for the instance to leave its transitional states".

So the client already has a documented hazard about two budgets on one
condition, and a server-side queue would add a third.

### F2 -- `defer_with_backoff()` exists, but its budget is shorter than every wait ever measured

Open question 8 says "the machinery exists --
`BaseClusterOperation.defer_with_backoff()` already re-enqueues with a
delay for artifact fetches and network operations".

It exists, at `shakenfist/operations/baseoperation.py:708`, with four
callers (`artifact_fetch_op.py:148` and `:217`, `net_op.py:180` and
`:242`, `node_blob_op.py:151`). But its default schedule is
`delays=(15, 30, 60)` -- **three defers totalling 105 seconds**, after
which it returns `False` and the caller must error the operation out.

Phase 2 measured three real waits of **190.5 s, 160.4 s and 140.6 s**.
Every one of them is longer than the entire default budget. Reusing
this machinery unchanged would give up before the *shortest* wait the
project has ever recorded had cleared.

"The machinery exists" is therefore true and misleading in the same
sentence. A queue would need its own schedule and its own budget, and
the sizing of that budget is the expensive question, not the
re-enqueue.

### F3 -- IPAM really is allocated before scheduling, and the refusal path is what frees it

Open question 8's IPAM concern -- "waiting instances hold IPAM
allocations, so a CPU shortage can become an address shortage" -- is
**confirmed**, and more tightly than the sentence suggests.

`_netdesc_allocate_address()` (`shakenfist/external_api/instance.py:436`)
reserves the address, at `:455` for a random free one and `:466` for a
requested one. It is called from the `POST /instances` handler at
`:1027`. `SCHEDULER.find_candidates()` is called at `:1049` -- **22
lines later**. Every interface is therefore reserved before placement
is even attempted.

What frees it today is the refusal itself: every scheduling refusal
branch calls `inst.enqueue_delete_due_error('scheduling failed')`
before returning -- `:1062` affinity (`409`), `:1069` low resource
(`507`), `:1082` candidate not found (`404`) and `:1177` the capacity
guard (`507`). Deleting the instance is what returns its addresses.

A queue that holds the instance instead of deleting it therefore holds
its addresses for the whole hold, by construction. This is not a side
effect to be engineered around; it is the current design's only release
path.

### F4 -- An address shortage already returns a 507, and phase 4 deliberately did not mark it transient

The survey found the consequence of F3 that open question 8 does not
state. `POST /instances` has four `507` branches, not two. Two are the
scheduling refusals phase 4 routed through `capacity_error()`
(`:1076`, `:1184`). The other two are `exceptions.CongestedNetwork`
at `:477` and `:509`, and both return a bare
`sf_api.error(507, str(e), suppress_traceback=True)` -- no
`Retry-After`, no `stage`, no `transient` field.

That is correct as it stands: phase 4's D29 marked only the scheduling
refusals. But it means a queue which converted CPU pressure into
address pressure would convert a refusal a client is told to retry into
one it is told nothing about. The failure would move from a surface
that phase 4 just made machine-readable to one that is still prose.

### F5 -- The suite already waits, client side, for 420 s

`CLUSTER_HEADROOM_WAIT = 420` at
`shakenfist/deploy/shakenfist_ci/base.py:61`, polling every
`CAPACITY_POLL_INTERVAL = 10` s (`:67`) with
`MAX_CREATE_ATTEMPTS = CLUSTER_HEADROOM_WAIT // CAPACITY_POLL_INTERVAL + 2`
(`:77`).

This is the fact that makes the question a real question rather than a
formality. The waiting the queue would do is waiting the suite already
does, in the caller, where D8 said deferral belongs. A server-side
queue does not add the wait; it moves it, and for the duration of the
move both exist.

### F6 -- The reading tool is built and needs no new work

`tools/ci_headroom_report.py --waits <file>` (`:2910`) reads the
capacity-wait trace from a downloaded bundle and prints total seconds
waited, the number of waits, the longest wait and its test, and the
informed/degraded split. `docs/developer_guide/ci.md:331` documents it.

Two mechanical corrections, both found while step 5c tried to run this
invocation, and both recorded here rather than fixed in the tool
because no code change is in scope (see the Outcome, and #4337).
`--series` is `required=True` (`:2879`), so `--waits` cannot be passed
on its own: a reading of the trace must also hand the tool that
bundle's `headroom.jsonl`. And the trace's path *inside a bundle* is
`bundle/traces/instance-waits.jsonl`; `/srv/ci/traces/instance-waits.jsonl`
-- which the `--waits` help text and an earlier draft of this plan both
gave as the in-bundle path -- is where the suite writes it on the CI
node, before the "Gather logs" step relocates it.

Two properties of it matter to this phase's rule. An absent or empty
file reports as **unknown, never as zero** -- so a run that never
collected the trace cannot be silently counted as a run with no waits.
And `attempt_number` is a 1-indexed position rather than a count, so a
create refused three times writes three lines carrying 1, 2 and 3, and
summing them would double-count. The rule below is written against
these two properties.

### F7 -- The gate is closed, and it is closed on an operator action

The master plan's ordering note says phase 5 "needs phase 2 to have
reported over a window of merge runs *after* the sizing plan's phase 4
has reshaped `slim-tier`; until then its data would be measuring the
wrong cloud".

That reshape has **not been applied**.
`shakenfist/actions/ansible/ci-topology-slim-tier.yml` still carries
`cpu: 4` on all three nodes, at `:38`, `:75` and `:105`. The sizing
plan's phase 4 has the complete diff prepared -- all three to `cpu: 6`
-- in its *Prepared changes* section, and its own Outcome records 4d
as "**Blocked** on the operator applying 4c to `shakenfist/actions`
and on three merge runs after it", with 4e, 4f and 4g blocked behind
it. That plan is `In progress`, 4 of 8, in `docs/plans/index.md:115`.

So the measurements available today describe the ledger-3 infra nodes
that the reshape exists to fix. Reading them would answer a question
about a cloud the project is in the middle of replacing.

**What the survey did not find wrong:** the master plan's
characterisation of the *arguments* -- fairness, starvation of pinned
and large creates, IPAM, and D8's queue-state objection -- all hold,
and D8's text is as quoted (`PLAN-scheduler-reservations-phase-00-decisions.md:431-432`:
"Partial-fill and hold-until-fittable are rejected outright (each adds
queue-state surface SF doesn't want; deferral lives in the caller, and
the conductor already has deferral mechanics)").

## Decisions

### D36 -- The gate is a step of this phase, not a precondition to planning it

The reshape being unapplied blocks the *reading*, not the *rule*. This
plan is written now, with its data steps blocked, exactly as the sizing
plan's own phase 4 is written with 4d-4g blocked on the operator.

The alternative -- wait, then plan -- would put the rule and the data
in the same session, which is the failure mode D37 exists to prevent.

The gate is discharged when all three hold:

1. The *Prepared changes* diff is applied to `shakenfist/actions` and
   merged.
2. At least **20** `merge_group` runs of `Functional tests` have
   completed on the reshaped `slim-tier`, as the master plan requires.
3. The sizing plan's step 4d has recorded its three-run reading, so
   this phase knows whether the reshape did what it predicted. If 4d
   reports the reshape did **not** move the per-node ledgers, this
   phase stops and reports rather than reading 20 runs of a change
   that did not take.

### D37 -- The rule is fixed now, in numbers, before the data is read

This is the decision the phase turns on, and the reason the plan is
written while the gate is closed.

The master plan's rule is "if total wait per run is small and no test
waits near its deadline". Neither "small" nor "near" is a number, and a
phase which reads 20 runs and *then* decides what those words mean is
not making a decision, it is narrating one. Phase 2's closeout already
warned which way the reading will tempt a reader: "Phase 5 should read
that as the central number rather than the totals, because a deadline
reached is a failed run and the margin here is smaller than the totals
suggest."

The rule, fixed here:

**Denominator.** `CLUSTER_HEADROOM_WAIT`, 420 s
(`shakenfist_ci/base.py:61`). If phase 6 or a later change moves that
constant, the thresholds move with it and are re-expressed as
fractions, not frozen as seconds.

**Unit of observation.** One `merge_group` run of a topology that
collected a trace. A run whose trace is absent, empty or unparseable is
recorded as **unknown** and excluded from both numerator and
denominator (F6). The count of unknowns is reported; if unknowns exceed
a quarter of qualifying runs, the window is not readable and the phase
extends it rather than deciding on the rest.

**Statistic.** The **longest single wait in a run**, as a fraction of
the denominator. Totals per run are recorded but are not the
discriminator, because a run of four 40-second waits and a run of one
160-second wait are the same total and a very different distance from
failure.

**The three outcomes:**

* **Abandon** if *all* of: no single wait in the window reaches
  0.5 x 420 s = **210 s**; fewer than **25%** of qualifying runs record
  any wait at all; and **no** wait reached the deadline.
* **Build** if *any* of: a wait reached the deadline in a qualifying
  run (that is a failed run, and the suite's own wait was not enough);
  or the longest wait exceeds **210 s** in more than **10%** of
  qualifying runs; or more than **50%** of qualifying runs record any
  wait.
* **Extend the window** if the data falls between the two, **once**,
  by a further 20 runs. On the second reading the middle ground
  resolves to Abandon, because a condition that has not shown itself in
  40 runs is not the condition a new state machine is built for.

The single extension is bounded deliberately. An unbounded "gather
more data" is how a decision phase never closes.

**The unreadable case is not one of the three.** Too many unknown runs
is a collection defect, not a quiet cluster, and it does not resolve to
Abandon on the second reading the way the middle ground does. If the
window is still unreadable after the extension, the phase reports that
the trace is not reaching the bundles reliably, and that becomes a
finding against phase 2's plumbing rather than an answer to open
question 8. Deciding "no queue needed" from runs that never reported is
the one outcome here that would be worse than not deciding.

**Recorded either way:** the full distribution, the informed/degraded
split, the `binding_dimension` census, and whether pinned
(`force_placement`) creates are over-represented among the long waits.
That last one is recorded whatever the decision, because it is the
fairness question's only empirical input and open question 8 asserts
it ("a pinned create starves worst") without evidence.

### D38 -- The burden of proof is on building the queue, not on abandoning it

**This is the decision a reviewer is most likely to disagree with**, so
the reasoning is given at length.

D37's thresholds are not symmetric: Abandon requires a quiet window,
Build requires a single clear signal, and the middle resolves to
Abandon. That asymmetry is deliberate, and it rests on three things the
survey established rather than on a preference for doing less.

1. **The wait already exists, in the caller, which is where D8 put
   it.** F5: the suite waits 420 s client-side and records every wait.
   A server-side queue does not introduce the wait, it relocates it
   -- and during the relocation both exist, on the same condition, in
   the shape `create_instance`'s own docstring warns about (F1). The
   project would be running two deferral mechanisms against one
   shortage.

2. **Phase 4 just chose the other direction, one phase ago.** It
   shipped `Retry-After` and a machine-readable `transient` marker
   precisely so the *client* can decide to retry, and its D33
   deliberately ships the client retry switched off. Adopting a
   server-side hold now would reverse an architectural direction the
   plan committed to within the same plan, on evidence gathered before
   that direction had a chance to be used by anything.

3. **The cheap fix is being applied concurrently and has not been
   measured.** F7: the reshape exists, is written, and is waiting on
   one pull request. The refusals this plan is about were traced to
   infra nodes with a ledger of 3. Building a state machine to survive
   a shortage that a three-line YAML change may remove is the
   expensive answer to a question the cheap answer has not been allowed
   to attempt.

The counter-argument, stated fairly: a CI suite is not the only
client, and an operator script hitting a genuinely full production
cluster gets a refusal where a queue would get an instance. That is
true, and it is the strongest case for building. It is not answered by
this phase's data, which is all CI. If the decision is Abandon, that
limitation is written into the record explicitly (D40), because an
Abandon justified by CI numbers must not be read later as a finding
about production.

### D39 -- If the decision is Build, IPAM moves first, and that is the phase's real cost

F3 and F4 together mean the queue is not the contained change open
question 8 describes. Holding an instance holds its addresses, because
deletion is the only path that releases them today, and converting a
CPU shortage into an address shortage converts a refusal phase 4 made
machine-readable into one that is still prose.

So a Build outcome's first step is not the queue. It is moving address
allocation from `:1027` to placement time, which means unpicking the
ordering in `POST /instances` and finding a new release path for the
refusal branches that currently rely on `enqueue_delete_due_error()`.

This is recorded as a decision rather than left to the design phase
because it changes the *cost estimate* the Build/Abandon choice is made
against. A reader weighing D37's thresholds should weigh them knowing
that Build is an IPAM re-ordering plus a state machine, not a
`defer_with_backoff()` call.

### D40 -- Whichever way it goes, the record says what the evidence could not cover

The phase writes its outcome into three places: this plan's Outcome,
the master plan's open question 8 (which currently says "Decide in
phase 5"), and the `docs/plans/index.md` row.

On an Abandon, the record states the three limits of the evidence: it
is CI-only (D38's counter-argument); it is post-reshape, so it says
nothing about whether the queue would have helped the cloud that
produced #3772; and it was gathered with the client retry switched off
(phase 4 D33), so no client in the window was retrying on its own
behalf.

An Abandon that does not say what it did not measure reads, a year
later, as "we established a queue is unnecessary". It would have
established that one topology of one test suite did not need one.

### D41 -- Correct open question 8 at source, in this phase's first commit

**Done, in the same commit that registers this phase.** F1, F2, F3 and
F4 are corrected in the master plan's open question 8 and its phase 5
section rather than deferred to phase 6's documentation sweep. The next
reader of open question 8 should not have to rediscover that the 900 s
ceiling is 600 s.

What changed there: the method name and its 600 s default, plus the
two-budgets warning (F1); the 105 s default budget of
`defer_with_backoff()` and the note that every measured wait exceeds it
(F2); the two line numbers that make the IPAM ordering checkable, and
the fact that `enqueue_delete_due_error()` on the refusal path is the
only thing that releases an address today (F3); and the two
`CongestedNetwork` `507`s, so the IPAM argument names its real
consequence (F4). The *arguments* in open question 8 are untouched --
they survived the survey. F5, F6 and F7 are true as written and needed
no source change.

**No step below redoes this.** The Definition of done still checks it,
because a correction made at planning time is as capable of being wrong
as one made later.

### D42 -- This plan does not design the queue

If the decision is Build, this phase produces the decision, the
reversal of scheduler-reservations D8 written into that plan's
decisions file, and a statement of what the design phase must cover
(the IPAM re-ordering of D39, the fairness model, the deadline
arithmetic against F1's 600 s, and the interaction with the suite's
existing 420 s wait). The design itself is a new phase, inserted into the
master plan's Execution table as a row of its own -- not a step here.

Writing the design here would mean writing it before knowing it is
wanted, at high effort, with a better-than-even chance of discarding
it.

## Step plan

The source corrections D41 describes are already made, in the commit
that registers this phase, so they are not a step here.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5b | low | sonnet | none | **Gate check (D36).** Establish whether the gate is open. Read `shakenfist/actions/ansible/ci-topology-slim-tier.yml` and check whether the three `cpu:` values at `:38`, `:75` and `:105` are 6 rather than 4. Read the sizing plan's phase 4 Outcome table and check whether 4d is still Blocked. If either says the reshape has not landed, stop and report -- do not proceed to 5c. If it has landed, count the completed `merge_group` runs of `Functional tests` since the merge commit and report whether the count has reached 20. This step is expected to run more than once, at intervals, and to report "not yet" most times. |
| 5c | medium | sonnet | none | **Gather (gated behind 5b).** For each qualifying `merge_group` run on the reshaped `slim-tier`, download the bundle and read `bundle/traces/instance-waits.jsonl` from it with `tools/ci_headroom_report.py --series bundle/traces/headroom.jsonl --waits bundle/traces/instance-waits.jsonl` -- `--series` is a required argument, so `--waits` alone cannot run, and inside a bundle the trace is at `bundle/traces/`, not at the `/srv/ci/traces/` path the tool's own help text gives (F6). Record per run: the number of waits, the total, the longest wait and its test id, the informed/degraded split, the `binding_dimension` of each wait, and whether the create was pinned (`node` set). A run whose trace is absent, empty or unparseable is recorded as **unknown** and excluded from the rule's denominator (F6) -- the tool already distinguishes these from zero, do not collapse them. Do **not** sum `attempt_number`; it is a 1-indexed position, not a count. Produce a table and the raw per-wait records, and nothing else: this step does not interpret. |
| 5d | high | opus | none | **Decide (D37).** Apply the pre-registered rule in D37 to 5c's table -- do not re-derive or adjust the thresholds, and if you believe one is wrong, say so and apply it anyway, recording the objection. Report which of Abandon, Build or Extend the data selects, with the arithmetic shown for each of the three clauses. Additionally report, whatever the outcome: the pinned-versus-unpinned split among waits longer than 210 s (D37's fairness input), and the count of unknown runs. If the outcome is Extend, say so and stop; the window extends once only. |
| 5e | high | opus | none | **Record the outcome (D40, D42).** Write this plan's Outcome section: the distribution, the rule's arithmetic, the decision, and the three evidence limits D40 requires (CI-only, post-reshape, client retry off). Answer open question 8 in the master plan -- replace "Decide in phase 5, from phase 2's wait data" with the answer and a link here. On an **Abandon**: set this phase `Abandoned` in the master plan's Execution table and note in `docs/plans/index.md` that the plan's phase count now reads against six live phases. On a **Build**: set this phase `Complete`, write the reversal of D8 into `PLAN-scheduler-reservations-phase-00-decisions.md` as an amendment beside D8 (do not edit D8's text in place -- amend below it, which is how that file records reversals), add a design-phase row to the Execution table, and state what the design phase must cover per D42. Do not design it. |

5b gates 5c, which gates 5d, which gates 5e. 5b is expected to be
re-run over weeks, and to report "not yet" most times.

## Risks and mitigations

| Risk | Mitigation | Who checks |
|---|---|---|
| The reshape is never applied, and this phase blocks the plan indefinitely. | 5b reports "not yet" cheaply and repeatedly rather than silently stalling, and this plan names the single pull request that unblocks it (the sizing plan's *Prepared changes*). If the operator decides not to reshape, that is a different input and D36's gate condition 3 catches it: the phase would then read the current cloud and say so. | The operator, at 5b. |
| The rule is applied loosely once real numbers are in front of a reader. | D37 fixes the thresholds before the data exists, and 5d is instructed to apply them even over its own objection, recording the objection rather than acting on it. The step split puts gathering (5c, sonnet, no interpretation) and deciding (5d, opus, no re-derivation) in different agents. | 5d's report, read against D37. |
| The window is dominated by runs with no trace, and the quiet result is an artefact of collection rather than of capacity. | F6: the tool reports absent, empty and unparseable as *unknown*, never zero. D37 excludes them from the denominator and aborts the reading if they exceed a quarter of runs. Phase 2's closeout already saw this: both `slim-primary` bundles in its two runs had no waits file at all. | 5c records the unknown count; 5d reports it. |
| Abandon is later read as "a placement queue is unnecessary" rather than "CI did not need one". | D40 requires the three evidence limits in the Outcome, and 5e's brief names them. | 5e, and the phase 6 documentation sweep which reads this. |
| A Build outcome is costed as a `defer_with_backoff()` call and turns out to be an IPAM re-ordering. | D39 states the real first step and the reason (F3, F4), before the choice is made rather than after. | 5d's cost framing; 5e's statement of what the design phase covers. |
| The reshape removes the refusals entirely, and phase 6 then has nothing to document about a transient contract that no longer fires in CI. | That is a good outcome, not a risk to this phase, but it is a real input to phase 6 and to the #3772 comment phase 6 owes. Recorded here so phase 6 inherits it rather than rediscovering it. | 5e, in *What phase 6 inherits*. |

## Definition of done

Each item is checked by running it, not by reading it. Three of phase
4's items were wrong and executing them is what found that, the second
phase in a row -- and two of the items below were wrong when first
written here, found the same way while this plan was being verified:
item 1 was falsified by this plan's own correction text repeating the
name it was checking for, and item 2 by the sentence it greps for
being line-wrapped.

1. `grep -n '_await_instance_create\|900 s ceiling' docs/plans/PLAN-transient-capacity-refusals.md`
   returns nothing.
2. The master plan says `await_instance_create()`'s ceiling is 600 s.
   Checked with whitespace flattened, because the sentence wraps and a
   line-oriented grep returns a misleading nothing -- the same trap
   phase 4's item 13 hit:
   `tr '\n' ' ' < docs/plans/PLAN-transient-capacity-refusals.md | tr -s ' ' | grep -c 'default ceiling is \*\*600 s\*\*'`
   returns 1. The `tr -s ' '` is not optional: flattening newlines
   alone leaves the markdown indent as a run of spaces and the match
   fails.
3. Open question 8 names `defer_with_backoff()`'s 105 s budget, and
   `grep -c '105' ` over that section is at least 1.
4. Open question 8 names four `507` branches, not two:
   `awk '/^### 8\./,/^### 9\./' docs/plans/PLAN-transient-capacity-refusals.md | grep -c 'has \*\*four\*\* `507` branches'`
   returns 1.
5. Open question 8's answer is no longer "Decide in phase 5" -- it
   states the decision and links to this plan.
6. This plan's Outcome contains a per-run table whose row count equals
   the qualifying-run count 5c reported, and the unknown count is
   stated as a number rather than omitted.
7. For each of D37's three clauses, the Outcome shows the arithmetic
   that selected or rejected it.
8. The Outcome states the pinned-versus-unpinned split among waits
   longer than 210 s, even if that split is "no wait exceeded 210 s".
9. The Outcome contains all three of D40's evidence limits, findable
   by grep for `CI-only`, `post-reshape` and `retry` in that section.
10. The master plan's Execution table row for phase 5 reads exactly
    one of `Abandoned` or `Complete`, and the `docs/plans/index.md`
    row's phase arithmetic agrees.
11. On a Build outcome only:
    `grep -n 'D8' docs/plans/PLAN-scheduler-reservations-phase-00-decisions.md`
    shows an amendment recording the reversal, and D8's original text
    is unchanged.
12. `python3 tools/check-plan-status.py` passes.
13. `pre-commit run --all-files` passes.

## Back brief

**Gate before 5c.** Do not begin gathering until 5b has confirmed all
three of D36's conditions. Reading the pre-reshape cloud would produce
a number that looks like an answer and is not one.

**Gate before 5e on a Build outcome.** Reversing a project-level
decision from another plan is the expensive-to-undo kind of change, and
D38 argues against it at length. If 5d selects Build, stop and get
agreement before 5e writes the reversal into
`PLAN-scheduler-reservations-phase-00-decisions.md`. An Abandon needs
no such gate; it is the direction the project is already pointed.

**No design.** D42. If the temptation arises during 5e to sketch the
state machine "while it is fresh", it is out of scope and belongs to
the design phase this one would create.

**Invariants a step here must not violate**, from the master plan's
step-level guidance: placement transactions open with a guarded
`UPDATE`; attribute writes carry a field mask; new polling is declared
in `database_load_budget.yaml` or `HARNESS_DRIVEN_PAIRS`; the
reconcile pass stays behind `cluster_stable()`; the `409` affinity
refusal is never retried. No step in this phase touches any of them --
the phase edits plan documents only -- and that is itself worth
stating so a sub-agent does not improve one in passing.

## Outcome

**The window is not readable, and the phase extends it -- once.** This
section is step 5e.

96 of 104 qualifying units carry no capacity-wait trace at all. The
unknown fraction is **92.3%** against D37's ceiling of 25%, so D37's
readability gate fires before any of the three outcome clauses are
consulted. That is the *unreadable* case, which D37 states explicitly
is **not one of the three outcomes** and explicitly does **not**
resolve to Abandon.

Therefore:

* **Open question 8 is not answered.** The phase stays `In progress` in
  the master plan's Execution table and in `docs/plans/index.md`, whose
  arithmetic remains `4 of 7`. It is neither `Abandoned` nor
  `Complete`.
* **Nothing was designed** (D42), and **no code changed** -- not even
  the instrument defect this reading found, which is filed as
  [#4337](https://github.com/shakenfist/shakenfist/issues/4337) and is
  deliberately outside this phase's scope.
* **scheduler-reservations D8 is untouched.** Reversing it is a
  Build-only action, and this is not a Build.

### The window

45 `merge_group` runs of `Functional tests`, from
2026-09-19T22:00:54Z -- the first run on the reshaped `slim-tier`, per
the sizing plan's step 4d -- to 2026-09-25T08:12:01Z.

19 of them ran no topology at all (17 with no artifacts, 2 with
artifacts but no instrumented cluster bundle) and are excluded from the
qualifying set rather than counted as unknown: a run that never ran the
suite is not a run whose trace went missing.

That leaves **26 runs x 4 instrumented bundles = 104 qualifying
units**. The unit is the *bundle*, not the `(run, topology)` pair,
because three of the four instrumented jobs run `slim-primary` and one
runs `slim-tier` (`tools/ci_headroom_harvest.py:133`) -- so a run
contributes four observations, not two.

### The distribution

| State | Units |
|---|---|
| `absent` -- no trace file in the bundle | 96 |
| `read` | 8 |
| `empty` | 0 |
| `unparseable` | 0 |

**Unknown fraction 96/104 = 92.3%.** The gate is 25%. Every
alternative scoping is also far past it, so the finding does not turn
on how the denominator is drawn:

| Scoping | Unknown | Total | Fraction |
|---|---|---|---|
| Qualifying bundles (the unit D37 reads) | 96 | 104 | 92.3% |
| `slim-tier` bundles alone | 23 | 26 | 88.5% |
| `slim-primary` bundles alone | 73 | 78 | 93.6% |
| Pooled per `(run, topology)` | 44 | 52 | 84.6% |
| Counting the 19 no-topology runs as unknown | 115 | 123 | 93.5% |

Per qualifying run, with the bundle-level census stated as counts
because 104 rows is not a table anyone reads. `Waits` and the two
second columns are over that run's readable bundles only; `longest` is
the largest single wait anywhere in the run.

| Run | Started | Read | Absent | Readable topologies | Waits | Total s | Longest s |
|---|---|---|---|---|---|---|---|
| [35472090760](https://github.com/shakenfist/shakenfist/actions/runs/35472090760) | 2026-09-19 22:00 | 1 | 3 | tier | 1 | 10.163 | 10.163 |
| [35478084378](https://github.com/shakenfist/shakenfist/actions/runs/35478084378) | 2026-09-20 00:10 | 0 | 4 | — | — | — | — |
| [35482487953](https://github.com/shakenfist/shakenfist/actions/runs/35482487953) | 2026-09-20 01:51 | 2 | 2 | primary, tier | 2 | 180.526 | 180.490 |
| [35485863474](https://github.com/shakenfist/shakenfist/actions/runs/35485863474) | 2026-09-20 03:09 | 0 | 4 | — | — | — | — |
| [35490705886](https://github.com/shakenfist/shakenfist/actions/runs/35490705886) | 2026-09-20 05:03 | 1 | 3 | primary | 1 | 50.210 | 50.210 |
| [35495432148](https://github.com/shakenfist/shakenfist/actions/runs/35495432148) | 2026-09-20 06:54 | 0 | 4 | — | — | — | — |
| [35533932764](https://github.com/shakenfist/shakenfist/actions/runs/35533932764) | 2026-09-20 19:56 | 0 | 4 | — | — | — | — |
| [35539199464](https://github.com/shakenfist/shakenfist/actions/runs/35539199464) | 2026-09-20 21:35 | 0 | 4 | — | — | — | — |
| [35547540638](https://github.com/shakenfist/shakenfist/actions/runs/35547540638) | 2026-09-21 00:23 | 0 | 4 | — | — | — | — |
| [35554184855](https://github.com/shakenfist/shakenfist/actions/runs/35554184855) | 2026-09-21 02:26 | 0 | 4 | — | — | — | — |
| [35560502830](https://github.com/shakenfist/shakenfist/actions/runs/35560502830) | 2026-09-21 04:18 | 0 | 4 | — | — | — | — |
| [35570212416](https://github.com/shakenfist/shakenfist/actions/runs/35570212416) | 2026-09-21 06:50 | 0 | 4 | — | — | — | — |
| [35580135033](https://github.com/shakenfist/shakenfist/actions/runs/35580135033) | 2026-09-21 08:52 | 1 | 3 | primary | 1 | 10.079 | 10.079 |
| [35666222479](https://github.com/shakenfist/shakenfist/actions/runs/35666222479) | 2026-09-21 23:09 | 0 | 4 | — | — | — | — |
| [35677335839](https://github.com/shakenfist/shakenfist/actions/runs/35677335839) | 2026-09-22 01:51 | 2 | 2 | primary, tier | 2 | 361.145 | 270.858 |
| [35713308961](https://github.com/shakenfist/shakenfist/actions/runs/35713308961) | 2026-09-22 09:57 | 0 | 4 | — | — | — | — |
| [35781045381](https://github.com/shakenfist/shakenfist/actions/runs/35781045381) | 2026-09-22 20:33 | 0 | 4 | — | — | — | — |
| [35792467388](https://github.com/shakenfist/shakenfist/actions/runs/35792467388) | 2026-09-22 22:27 | 0 | 4 | — | — | — | — |
| [35804030364](https://github.com/shakenfist/shakenfist/actions/runs/35804030364) | 2026-09-23 00:54 | 0 | 4 | — | — | — | — |
| [35851169069](https://github.com/shakenfist/shakenfist/actions/runs/35851169069) | 2026-09-23 10:51 | 0 | 4 | — | — | — | — |
| [35940203974](https://github.com/shakenfist/shakenfist/actions/runs/35940203974) | 2026-09-24 00:50 | 0 | 4 | — | — | — | — |
| [35946925675](https://github.com/shakenfist/shakenfist/actions/runs/35946925675) | 2026-09-24 02:21 | 0 | 4 | — | — | — | — |
| [35954362019](https://github.com/shakenfist/shakenfist/actions/runs/35954362019) | 2026-09-24 04:08 | 0 | 4 | — | — | — | — |
| [36048955931](https://github.com/shakenfist/shakenfist/actions/runs/36048955931) | 2026-09-24 19:33 | 0 | 4 | — | — | — | — |
| [36076487106](https://github.com/shakenfist/shakenfist/actions/runs/36076487106) | 2026-09-25 00:12 | 0 | 4 | — | — | — | — |
| [36084308218](https://github.com/shakenfist/shakenfist/actions/runs/36084308218) | 2026-09-25 01:58 | 1 | 3 | primary | 1 | 120.408 | 120.408 |

Twenty of the 26 runs produced no readable bundle at all.

### The eight readable waits

Every readable unit contains exactly **one** wait, so its total equals
its longest. `attempt_number` is 1 for all eight and is not summed
(F6). The mode split is **8 informed, 0 degraded, 0 other**. The
`binding_dimension` census is **`cpus` x 7, `null` x 1**.

| Longest s | % of 420 s | Topology | Test |
|---|---|---|---|
| 270.858 | 64.5% | slim-primary | `test_lifecycle_reboot_powered_off` |
| 180.490 | 43.0% | slim-tier | `test_vanished_source_server_instance` |
| 120.408 | 28.7% | slim-primary | `test_vanished_source_server_instance` |
| 90.287 | 21.5% | slim-tier | `test_network_plumbing_lifecycle` |
| 50.210 | 12.0% | slim-primary | `test_vanished_source_server_instance` |
| 10.163 | 2.4% | slim-tier | `test_network_plumbing_lifecycle` |
| 10.079 | 2.4% | slim-primary | `test_vanished_source_server_instance` |
| 0.036 | 0.0% | slim-primary | `test_vanished_source_server_instance` |

### The rule's arithmetic

Recorded because D37 requires it and because the Definition of done
checks it -- **not** because it selected the outcome. It did not: the
readability gate fires first, and an outcome read off eight units out
of 104 would be exactly the "deciding from runs that never reported"
that D37 names as the worst available result. Computed on the 8
readable units, denominator `CLUSTER_HEADROOM_WAIT` = 420 s.

**Abandon** needs all three of its clauses:

| Clause | Threshold | Observed | Holds? |
|---|---|---|---|
| A1 | no single wait reaches 210 s | max 270.858 s | **no** |
| A2 | fewer than 25% of qualifying runs record any wait | 8/8 = 100% | **no** |
| A3 | no wait reached the deadline | 0 of 8 reached 420 s | yes |

Two of three fail, so Abandon is **rejected**.

**Build** needs any one of its clauses:

| Clause | Threshold | Observed | Fires? |
|---|---|---|---|
| B1 | a wait reached the deadline | 0 of 8 | no |
| B2 | longest > 210 s in more than 10% of runs | 1/8 = 12.5% | **yes** |
| B3 | more than 50% of runs record any wait | 8/8 = 100% | **yes** |

Two clauses fire, so on the readable set alone Build **would be
selected**.

`slim-tier` alone -- the topology the reshape was for, 3 readable units
-- reads differently and is worth writing down: A1 **holds** (max
180.490 s < 210 s), A2 fails, A3 holds; B1 no, B2 no (0/3), B3 yes. On
the reshaped topology the longest observed wait is *below* the 210 s
line.

### Three objections to D37, recorded rather than acted on

D37 instructs 5d to apply the rule even over its own objection and to
record the objection instead of acting on it. There are three, and they
are one defect wearing three faces.

The mechanism, verified directly: the trace is opened `'a'`
(`shakenfist/deploy/shakenfist_ci/base.py:516`) and so is created on
first write. A unit that *has* a trace file therefore necessarily
contains at least one wait. D37 excludes unknowns from the denominator,
so "qualifying runs" in A2 and B3 means the *readable* set -- the set
in which the wait count is guaranteed non-zero. Hence:

1. **A2 is structurally unsatisfiable.** "Fewer than 25% of qualifying
   runs record any wait" is permanently 100% and can never be below
   25%. Abandon is unreachable under this instrument no matter how
   quiet the cluster is.
2. **B3 is the same artefact with its sign flipped.** "More than 50% of
   qualifying runs record any wait" is permanently 100% > 50%, so Build
   fires on any readable window whatever it contains. Taken with (1),
   D37 can only ever return Build or unreadable. It has no path to
   Abandon at all, which sits badly against D38's argument that the
   burden of proof rests on building.
3. **The readability gate collides with the same defect.** A quiet
   cluster produces absent traces; absent reads as unknown; unknowns
   trip the 25% gate. So quiet and broken are indistinguishable *in
   aggregate*, not merely per record -- and the quieter the cluster,
   the more certainly the window is declared unreadable.

**The outcome is robust to the defect.** Under the alternative
denominator -- all 104 qualifying units, absent counted as a run with
no wait -- A1 is still false (max 270.858 s), A2 becomes true
(8/104 = 7.7% < 25%) and A3 true, so **Abandon is still rejected**;
B1 false, B2 = 1/104 = 0.96% (not > 10%), B3 = 7.7% (not > 50%), so
**Build is rejected**. The data fall between the two, and between is
D37's middle ground, which selects **Extend**. Both readings extend.

They differ only in what a *second* reading does: the middle ground
resolves to Abandon on the second reading, and the unreadable case
explicitly does not. **That question must be settled before the second
reading is taken, and it is deliberately left open here.** Amending a
pre-registered rule after seeing the data is the exact failure
pre-registration exists to prevent, so this section states the question
and does not answer it:

> **Open for the second reading.** Is `absent` to be counted as
> `unknown` (D37 as written), or as an observation of zero waits once
> #4337 makes zero expressible? The two give the same answer today and
> different answers on the second reading. Settle it, in writing,
> *before* the second window's data is looked at.

**Answered by D43**, under *Decisions taken after the first reading*
at the end of this plan, written 2026-09-30 before any second-window
data was read. The answer also corrects the question: post-#4337 it is
`empty`, not `absent`, that carries "nothing was refused", and the two
states now mean different things. `empty` is admitted to the
denominator as a real zero; `absent` stays unknown.

### The fairness input (D37 requires it whatever the outcome)

All 8 readable waits were **pinned** creates -- `pinned_to` set from
the test's `force_placement`. **Zero unpinned.** The single wait longer
than 210 s (270.858 s, `test_lifecycle_reboot_powered_off`) was pinned,
so the pinned-versus-unpinned split above 210 s is **1 pinned, 0
unpinned**.

The absence is real rather than an artefact of the instrument:
`pinned_to` is set unconditionally from `force_placement`
(`base.py:432`) and `_record_capacity_wait()` is called unconditionally
(`:435`), so an unpinned refusal would have been recorded had one
occurred.

But with no comparison arm there is nothing to compare against. These
data can neither support nor refute open question 8's assertion that "a
pinned create starves worst": **the assertion remains unevidenced, and
this window could not test it.** A window in which every observed
refusal is pinned tells you what the suite does, not what the scheduler
prefers.

### The diagnosis is narrower than D37 predicted

D37 says the unreadable case means "the trace is not reaching the
bundles reliably". That prediction is **wrong**, and the correction
matters because it changes what the extension can achieve.

The trace *is* reaching them. Every absent bundle carries a fully
populated `bundle/traces/` -- `headroom.jsonl`, `headroom-census.json`,
`headroom-label`, `headroom-probe.log`, `headroom-start` and the
per-test JSON files -- and only `instance-waits.jsonl` is missing. That
is exactly the signature create-on-first-write predicts for a run in
which nothing was refused. The eight readable units are spread across
the whole window, from its first run to its last but one, so the
`shakenfist/actions` collection step demonstrably worked throughout.

The real diagnosis: **the instrument cannot express zero.** An absent
file conflates "nothing was refused", "the component ref predates the
wrapper" and "every write failed", and a consumer reading many bundles
cannot tell them apart.

**So extending the window cannot, on its own, resolve the
unreadability.** The cause is structural, not sampling: twenty more
runs of this instrument produce the same ~90% absent rate. This is
filed as
[#4337](https://github.com/shakenfist/shakenfist/issues/4337), which
also names the fix -- create the file empty at suite start-up, so
`empty` means zero and `absent` retains only the other two meanings.
The tool already distinguishes the three states, so nothing downstream
needs to change to benefit. That issue also carries the in-bundle path
error in `ci_headroom_report.py`'s `--waits` help text (F6), which this
phase could not fix because it changes code.

### Other observations from the window

Recorded because a later reader of this window will meet them, not
because any of them implies an action here.

* **Every one of the 26 runs that ran cluster jobs concluded
  `failure`**, and `Node lifecycle (collection)` failed in 25 of the 25
  the API would serve job detail for. The window sits almost entirely
  before the `Node lifecycle` shutdown-check false positive
  ([#4209](https://github.com/shakenfist/shakenfist/issues/4209)) was
  fixed, which landed on `develop` at 2026-09-25T03:49Z in `45021093d`,
  so #4209 is plausibly most of it. Phase 4's own closeout described
  the same failure without filing it.
* **Seven runs also had an instrumented cluster job fail**, none of
  them attributable to a capacity wait: no wait in the window came
  within 149 s of its deadline.
* **One refusal the ledger cannot explain.** The 0.036 s wait has
  `binding_dimension: null` and `headroom_at_first_refusal` showing 6
  CPUs free -- a refusal with headroom, which is the #3813 / #3772
  family's shape rather than a capacity shortage. One occurrence, no
  action implied, and it is the only unit in the window whose binding
  dimension is not `cpus`.

### Gate thinness

D36 condition 2 wanted 20 `merge_group` runs on the reshaped tier. 45
runs were enumerated, but only 26 of them ran a topology, so the gate
passed with **six** runs of margin rather than twenty-five. That is
still a pass on its own terms -- the condition counts runs, and 26 > 20
-- but it is thinner than the enumeration makes it look, and the second
reading should count qualifying runs rather than runs.

### What the evidence could not cover (D40)

D40 requires these three whatever the outcome, and they apply with more
force to an Extend than to a decision, because an extension invites a
reader to assume the next reading will be better-founded on all three
counts. It will not be; these are properties of the measurement, not of
the window's length.

1. **CI-only.** Every observation here is the functional suite on
   `slim-primary` and `slim-tier`. An operator script against a
   genuinely full production cluster is not represented, and that is
   D38's strongest counter-argument. Nothing in this window speaks to
   it.
2. **post-reshape.** The window opens at the first run on the reshaped
   `slim-tier`, by construction (D36). It therefore says nothing about
   whether a queue would have helped the ledger-3 cloud that produced
   #3772 -- the cloud the reshape exists to replace.
3. **Client retry off.** Phase 4's D33 ships the client `retry`
   switched off, and the suite deliberately does not turn it on, so no
   client in this window was retrying on its own behalf. Every wait
   recorded here is the suite's own wrapper waiting, not a client
   exercising the phase 4 contract.

### Source corrections made in this commit

D41 corrected four claims at source when this phase was registered.
Executing steps 5c and 5d found three more, each verified against the
tree, and all three are corrected above:

1. **The in-bundle trace path.** This plan and
   `tools/ci_headroom_report.py`'s `--waits` help text both gave
   `/srv/ci/traces/instance-waits.jsonl` as the path "in the bundle".
   Inside a bundle it is at `bundle/traces/instance-waits.jsonl`;
   `/srv/ci/traces/` is where the suite writes it on the CI node.
   Corrected in F6 and in 5c's brief. **The tool is not edited** --
   that is a code change and out of scope -- so the error is left
   stated here and on #4337 for whoever fixes it.
2. **5c's invocation could not run.** `--series` is `required=True`
   (`tools/ci_headroom_report.py:2879`), so
   `ci_headroom_report.py --waits <file>` exits on a usage error. The
   brief now passes both files.
3. **`CLUSTER_HEADROOM_WAIT` is at `base.py:61`, not `:52`.** Value
   unchanged at 420. F5's other two citations had drifted by the same
   six lines and are corrected with it (`CAPACITY_POLL_INTERVAL` at
   `:67`, `MAX_CREATE_ATTEMPTS` at `:77`), as is D37's denominator
   citation and F6's argparse line (`:2910`, from `:2648`).
   *This item has now been corrected twice. It was written as `:58`,
   `:64` and `:74`, which is where the first reading found them; #4337
   then inserted `ensure_capacity_wait_trace()` above all three and
   pushed each down by three lines. The line numbers above are the
   post-#4337 ones -- see* Source corrections made for the second
   reading, *below.*

### Definition of done, item by item

Several items assume a decision was taken and cannot be met by an
Extend. They are recorded as deferred rather than reinterpreted into a
pass, because an item bent to fit the outcome it was written to test is
worse than an item plainly marked unmet.

| Item | Result | Note |
|---|---|---|
| 1 | **Met** | Run: `grep -n '_await_instance_create\|900 s ceiling'` over the master plan returns nothing. |
| 2 | **Met** | Run, with `tr -s ' '` as the item insists: the count is 1. |
| 3 | **Met** | Run: open question 8's section names the 105 s budget. |
| 4 | **Met** | Run: the `awk` range check returns 1. |
| 5 | **Partly met** | Open question 8 no longer reads "Decide in phase 5" and links here. It does **not** state a decision, because there is none to state; it states that the first reading was unreadable and that the question is open. Deferred in full to the second reading. |
| 6 | **Met, on the run reading of the item** | The item says "the qualifying-run count 5c reported". 5c reported 26 qualifying *runs* and 104 qualifying *units*, the unit being the bundle -- an ambiguity the item did not anticipate, because the plan was written assuming one bundle per run. The table above has 26 rows, one per qualifying run, with the bundle-level census given as counts; the 104-row bundle table is in the step 5c dataset rather than here, because it is 96 identical rows. The unknown count is stated as a number, `96`. |
| 7 | **Met** | All three clauses' arithmetic is above, for both denominators, with the explicit note that it did not select the outcome. |
| 8 | **Met** | 1 pinned, 0 unpinned above 210 s; 8 pinned, 0 unpinned overall. |
| 9 | **Met** | `CI-only`, `post-reshape` and `retry` all appear in *What the evidence could not cover*. |
| 10 | **Deferred** | Cannot be met. The item requires exactly one of `Abandoned` or `Complete`; the phase is correctly neither. It reads `In progress` in both places and the index arithmetic (`4 of 7`) agrees -- which was checked, and is what `tools/check-plan-status.py` verifies. The item is met on the second reading. |
| 11 | **Not applicable** | Build-only, and this is not a Build. `PLAN-scheduler-reservations-phase-00-decisions.md` is untouched. |
| 12 | **Met** | `python3 tools/check-plan-status.py` passes. |
| 13 | **Met** | `pre-commit run --all-files` passes. |

### What the second reading must do

Not a design, and not a step plan -- the phase's step table already
ends at 5e. This is what the extension owes, so that the second reading
is not a rerun of the first.

1. **The `absent` question is settled** -- D43, written 2026-09-30
   before any second-window data was read. `empty` is a real zero and
   enters the denominator; `absent` and `unparseable` stay unknown.
   Nothing further is owed here; it is listed because the ordering
   (rule first, data second) is the point.
2. **#4337 has landed** -- `62bb1ddeb`, merged 2026-09-27T19:10:31Z,
   which adds `ensure_capacity_wait_trace()` and calls it from
   `BaseTestCase.setUp()`. It opens the file `'a'`, so it creates
   without ever truncating and concurrent stestr workers cannot lose a
   sibling's line. The second window therefore starts at the first
   `merge_group` run whose base contains that commit; identify it the
   way sizing 4d identified the first window's start, rather than
   assuming the next run after the merge carries it. This condition is
   met and needs no further action -- it is recorded because the
   extension is bounded to one by D37, and opening the second window
   on the old instrument would spend it for nothing.
3. **Count qualifying runs, not enumerated runs**, against D36
   condition 2. See *Gate thinness*.
4. **A2 and B3 are unpinned, by D43 rather than by #4337.** They were
   structurally pinned at 100% under the instrument this window was
   read with, and #4337 landing did not by itself unpin them: D37
   discards `empty` alongside `absent`, so a zero-wait run that now
   writes an empty file would still have been thrown out of the
   denominator. D43 admits `empty` to it, which is what unpins them.
   Report A2 and B3 over a denominator of `read` + `empty`, and show
   the census that denominator was drawn from.

### What phase 6 inherits

* Open question 8 is **still open**, so phase 6's documentation sweep
  cannot describe a settled position on server-side queued placement.
* The transient contract does fire in CI, but rarely and only under an
  instrument that cannot count its own silences: 8 waits over 26 runs
  of the suite, all pinned, all `informed`, one above half the
  deadline. Phase 6's #3772 comment should quote those numbers with the
  92.3% unknown fraction beside them, or it will overstate what is
  known.
* #4337 is a phase 2 instrument defect found by phase 5 and owned by
  neither: it is a candidate for phase 6's sweep or for the issue-fix
  workflow, but it is not this phase's work.

## Decisions taken after the first reading

The Decisions above were all fixed before the first window was read.
This section is not, and says so in its own heading so that no later
reader has to work out which side of the data a decision falls on.

### D43 -- `empty` is a real zero; `absent` and `unparseable` stay unknown

**Written 2026-09-30, before any second-window trace data was read.**
This amends a pre-registered rule, which is worth doing carefully or
not at all, so the record of what was and was not known when it was
written comes first.

**What had been looked at when this was written.** The gate
conditions, and nothing else. That `62bb1ddeb` is on `develop`; that
`ensure_capacity_wait_trace()` exists at
`shakenfist/deploy/shakenfist_ci/base.py:180`, opens the file `'a'`
and is called from `BaseTestCase.setUp()` at `:214`; and a count of
`merge_group` runs of `Functional tests` whose base contains that
commit -- 26 enumerated, of which 18 ran a topology, against D36
condition 2's threshold of 20. No capacity-wait trace from any of
those runs has been opened, no bundle from the second window has been
downloaded, and no wait figure from the second window appears anywhere
in this plan. The first window's data is of course already read and
written up above, which is precisely why this paragraph is needed.

**The question**, as the Outcome's blockquote leaves it: is a run that
records no wait an observation, or an absence? D37 answers absence for
all three of its silent states at once -- "A run whose trace is
absent, empty or unparseable is recorded as **unknown** and excluded
from both numerator and denominator". #4337 pulls those three states
apart, and this amendment follows the split rather than the sentence.

**The rule, from the second window onward:**

* **`empty` is an observation of zero waits.** It enters both
  numerator and denominator as a qualifying run with a longest single
  wait of 0 s which recorded no wait. This applies only to units whose
  run base contains `62bb1ddeb`. Before that commit an empty file had
  no defined meaning, and in fact no unit produced one -- the first
  window's census is 96 `absent`, 8 `read`, 0 `empty`, 0
  `unparseable`.
* **`absent` remains unknown.** After #4337 an absent file no longer
  means "nothing was refused". It means no test ever reached `setUp()`
  -- a cluster that died before testing -- or a component ref
  predating the wrapper. Neither is a quiet cluster, and reading
  either as a zero would be the same error D37's closing paragraph
  warns against, made in the opposite direction.
* **`unparseable` remains unknown**, for the reason it always was: a
  file that cannot be read says nothing about what it contains.
* **A qualifying unit is one whose trace file is present**, empty or
  not. D37's "a topology that collected a trace" is read that way, and
  an empty file is a trace that was collected.
* **The first window is not re-scored.** Its 104 units were collected
  under the old instrument and its reading stands as written above.
  The second reading reads the second window, not both.

**Everything else in D37 is unchanged**: the 420 s denominator, the
longest-single-wait statistic, the 25% readability ceiling, the three
outcome clauses exactly as worded, the single bounded extension, and
the rule that a still-unreadable window is a finding against phase 2's
plumbing rather than an Abandon.

**What this unpins.** The three objections recorded above are one
defect wearing three faces, and admitting `empty` answers all three at
once. A zero-wait run now lands in the denominator, so A2 ("fewer than
25% of qualifying runs record any wait") and B3 ("more than 50%") stop
being pinned at 100%; and a quiet cluster stops being
indistinguishable from a broken one at the readability gate. D37
regains a path to Abandon, which it did not have.

**The objection a reviewer should make**, and should weigh rather than
accept: this amendment makes Abandon reachable, D38 argues the burden
of proof sits on building rather than abandoning, and so it moves the
rule toward the outcome the plan already leans toward. That is the
exact shape of a post-hoc amendment. Three things answer it:

1. **A rule with an unreachable branch is not a rule.** D37 as written
   could return only Build or unreadable. That is a pre-registration
   of a foregone conclusion rather than of a decision, and repairing
   it is a different act from tuning it.
2. **It does not only help Abandon.** B3 becomes satisfiable in the
   Build direction too. Under D37 it fired on every readable window
   whatever that window contained, which told a reader nothing;
   admitting `empty` makes "more than half the qualifying runs waited"
   a claim about the cloud. The clause becomes informative in both
   directions, and it is the clause most likely to select Build.
3. **It is written against no data.** What had been looked at is
   recorded above, and it is a run count and two source line numbers.
   The amendment cannot have been fitted to a result nobody has seen.

**What would falsify its premise.** If the second window's units come
back predominantly `absent` rather than `empty`, then
`ensure_capacity_wait_trace()` is not reaching the bundles, this
amendment has changed nothing, and the window is unreadable again --
which is D37's still-unreadable case and resolves as D37 says, to a
finding against phase 2's plumbing. The second reading must therefore
report the `read`/`empty`/`absent`/`unparseable` census *before*
applying any outcome clause, so that this is visible rather than
inferred after the fact.

**Check.** The second reading honours this decision if its census
table carries a non-zero `empty` row and its A2 and B3 arithmetic use
a denominator equal to `read` + `empty`.

### D44 -- A bundle that cannot carry a wait trace is not a unit

**Written 2026-10-02, after the second window had been gathered.**
That ordering is the first thing to say about this decision, because it
is the opposite of D43's. The exclusion recorded here was *implemented
in the gather script* -- as a fifth state, `not-wait-capable` -- and
written up as a decision afterwards. Step 5d objected to exactly that,
and **the objection is correct in form**: a state D37 never named
appeared while the data was being collected, and no reader of D37 would
have predicted it from D37's text.

**The rule.** An artifact bundle which cannot execute
`shakenfist_ci.base.BaseTestCase.setUp()` is not a unit of observation.
It is recorded as `not-wait-capable` and excluded from the measurement
entirely: it is in neither the numerator, nor the denominator, nor the
unknown column. In this window that is the 25
`bundle-shakenfist-full-ansible-modules` bundles described as F9 below,
whose suite is six Ansible playbooks
(`shakenfist/deploy/ansible_module_ci/001.yml` .. `006.yml`) with no
Python test harness at all.

**Why this restores D37's denominator rather than amending it**, which
is the substance that answers 5d's objection, and it is checkable in
git:

* D37 was written **2026-09-19**, in `240b761e1` ("Plan phase 5: decide
  on queued placement").
* `bundle-shakenfist-full-ansible-modules` was moved out of
  `UNINSTRUMENTED_BUNDLES` and into `BUNDLE_TOPOLOGIES` by
  `3723216d7` ("Instrument the Ansible modules cluster job"), which
  landed on `develop` on **2026-09-30** in `174c0b819` (#4373) -- the
  sizing plan's phase 6, see
  [#4377](https://github.com/shakenfist/shakenfist/issues/4377).
  (`3723216d7`'s author date is 2026-09-28; it is the landing date that
  matters here.)
* `classify_artifact()` returns `('skip', reason)` for anything in
  `UNINSTRUMENTED_BUNDLES` (`tools/ci_headroom_harvest.py:519`).

So on the day D37 was written, this bundle family could not have become
a unit: the harvester skipped it by name. D37's four states were fixed
against a denominator that already excluded it, and it entered the
enumerable set eleven days later for a reason that has nothing to do
with capacity waits -- it banks a *headroom* series. **Excluding these
bundles restores the denominator D37 was pre-registered against;
including them would be the amendment.**

**It is load-bearing, and it is the only choice in the exercise that
could make the three scopings disagree.** Counted as `absent`, the 25
bundles put the per-bundle reading at **28.4% unknown**, past D37's 25%
readability ceiling, so the per-bundle scoping becomes *unreadable*
while the two pooled scopings stay readable at 4.3% and 4.2%. A
reviewer who rejects this decision therefore does not get Build; they
get one scoping saying "collection defect" and two saying Abandon. That
is worth stating plainly, because it is the shape of a disagreement a
later reader could otherwise mistake for an argument about the queue.

**What would falsify its premise.** If one of these bundles ever reads
`empty` rather than `absent`, the lane has acquired a Python harness,
`setUp()` is running, and the bundles are wait-capable after all. In
this window it is 25 of 25 `absent`, on both sides of #4337; post-#4337
a lane that reached `setUp()` would read `empty` at minimum.

**Check.** The second reading honours this decision if its census
reports `not-wait-capable` as a count outside the four D37 states, and
if the per-bundle unknown fraction it gates on is 2.1% rather than
28.4%.

## Outcome -- second reading

**Abandon: the server should not queue a create that does not fit.**
The word was selected by D37's *middle-ground* rule -- "on the second
reading the middle ground resolves to Abandon" -- and **not by any of
Abandon's own clauses**. A1 failed, because two waits in this window
exceed 210 s, and no Build clause fired either, so the data fell
between the two sets for a second time and the extension was already
spent. This is an Abandon **by default rule, not on the evidence**, and
that distinction is the most important sentence in this section.

**It does not mean no waits were observed.** Three capacity waits were
recorded, at 41%, 55% and 69% of the suite's 420 s deadline, in both
topologies, by the same three tests that produced the first window's
long waits -- and the longest of them is the longest wait either window
has produced. The finding is **rare but substantial**, not absent. A
reader who takes "Abandon" to mean "CI does not wait" has the record
exactly backwards; what the window shows is that the condition is real
and that it does not occur at the *frequency* D37's Build clauses
require.

Therefore:

* **Open question 8 is answered: no.** The answer, with this
  qualification and the fairness null result below, is written into the
  master plan.
* **This phase is `Complete`, not `Abandoned`.** This is a deliberate
  departure from step 5e's own brief above, which says to write
  `Abandoned` on this outcome. The shared status vocabulary defines
  `Abandoned` as "deliberately dropped without being done", and this
  phase *was* done: it made the decision it existed to make. What is
  abandoned is the **queue**, and that is recorded as open question 8's
  answer rather than as a phase status. Marking the phase `Abandoned`
  because its finding was negative would misuse the vocabulary and make
  the index arithmetic imply a phase dropped undone. Definition of done
  item 10 permits either word, and `Complete` satisfies it;
  `docs/plans/index.md` reads `5 of 7`.
* **Phases 6 and 7 remain live**, so the plan's own index row stays
  `In progress`.
* **Nothing was designed** (D42) and **no code changed.** The two
  instrument findings this reading produced, F8 and F9 below, are
  recorded for filing rather than fixed, as #4337 was by the first
  reading.
* **scheduler-reservations D8 is untouched.** Reversing it is a
  Build-only action.

### The window

| | |
|---|---|
| Opens | **2026-09-27T19:13:58Z** (run 36343591915) |
| Closes | 2026-10-02T02:47:33Z (run 36957255218) |
| `merge_group` runs of `Functional tests` enumerated | 47 |
| Excluded because the run's base does not contain `62bb1ddeb` | 8 |
| Qualifying runs kept | **39** |
| Of those, runs that produced at least one in-scope unit | **25** |

The window opens **3 minutes 27 seconds** after `62bb1ddeb` merged at
2026-09-27T19:10:31Z, which is as tight a start as the instrument
allows: the first `merge_group` run whose base contains #4337's fix.
This discharges item 2 of *What the second reading must do* -- the
start was identified by base-commit ancestry rather than by assuming
the next run after the merge carried it.

**The figure to discard is 2026-09-28T00:27Z.** That date appeared in
the brief for this step and in the gather note F8 below was drafted
from, and it is not the window start: it is the *second* run, and it
comes from the cheap `created>=` timestamp pre-filter the gather
applies before it checks ancestry. The ancestry result is 19:13:58Z on
2026-09-27, and the later date would have silently dropped the window's
first four units.

Against D36 condition 2's threshold of 20, counting qualifying units
rather than enumerated runs as item 3 of *What the second reading must
do* requires:

| Slice | Qualifying | At least 20? |
|---|---|---|
| Per bundle | 95 | yes |
| Per (run, topology) | 47 | yes |
| Per run | 24 | yes |
| `slim-tier` bundles alone -- the reshaped topology | 23 of 24 | yes |
| `slim-primary` bundles alone | 72 of 73 | yes |

The thinness is one layer down rather than at the gate: **14 of the 39
ancestry-qualifying runs (36%) produced no instrumented cluster bundle
at all**, so 39 runs yield only 25 run-units. That is why the per-run
denominator below is granular enough to matter.

### The four-state census, and what #4337 changed

Three scopings are reported, because D37's "one `merge_group` run of a
topology" does not say whether a run that ran four instrumented bundles
contributed one observation or four. Pooling rule: a group with any
`read` member is `read`, and its longest wait is the maximum over its
members; otherwise a group with any `empty` member is `empty`, with a
longest wait of 0 s; otherwise it is unknown.

| Scoping | read | empty | absent | unparseable | Qualifying | Unknown | Unknown % | Readable (gate 25%) |
|---|---|---|---|---|---|---|---|---|
| Per bundle | 3 | 92 | 2 | 0 | 95 | 2 | 2.1% | yes |
| Per (run, topology) | 3 | 44 | 2 | 0 | 47 | 2 | 4.3% | yes |
| Per run | 2 | 22 | 1 | 0 | 24 | 1 | 4.2% | yes |

The percentages are unknowns over *qualifying* units, which is how D37
words its gate ("if unknowns exceed a quarter of qualifying runs"). Over
qualifying plus unknown they are 2.1%, 4.1% and 4.0% -- the same side
of the gate by a wide margin either way.

Outside the four states: **25 `not-wait-capable`**, excluded from the
measurement by D44; 11 runs with no cluster jobs and 3 with artifacts
but no instrumented cluster bundle, which are not units for the same
reason the first reading gave -- a run that never ran the suite is not
a run whose trace went missing.

**#4337 worked, and this is the headline provenance fact of the second
reading.** Beside the first window:

| | First window | Second window |
|---|---|---|
| `empty` units | **0** | **92** |
| `absent` units | **96** | **2** |
| Unknown fraction | **92.3%** | **2.1%** |
| Readable? | no | yes |

The first window could not be read because the trace was created on
first write and so could not express "nothing was refused". With
`ensure_capacity_wait_trace()` called from `setUp()`, a quiet run now
says so. This also discharges item 4 of *What the second reading must
do*: A2 and B3 are reported over a denominator of `read` + `empty`, and
the census that denominator is drawn from is the table above. Both are
scored on the same numerator, "units recording any wait", and it is no
longer pinned at 100%: it reads 3/95 = 3.2% per bundle, which is a
claim about the cloud rather than an artefact of the instrument. And
it discharges D43's falsification test: the units came back
predominantly `empty`, not `absent`, so `ensure_capacity_wait_trace()`
is reaching the bundles and D43's premise holds.

### Per qualifying run

One row per qualifying run, 39 of them, with the unit census as counts.
`Waits`, `Total s` and `Longest s` are over that run's `read` units
only. The `Note` column names the topologies that waited, or the reason
a run produced no unit.

| Run | Started | Concluded | Units | read | empty | absent | Waits | Total s | Longest s | Note |
|---|---|---|---|---|---|---|---|---|---|---|
| [36343591915](https://github.com/shakenfist/shakenfist/actions/runs/36343591915) | 2026-09-27 19:13 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36352739123](https://github.com/shakenfist/shakenfist/actions/runs/36352739123) | 2026-09-27 21:42 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36355421965](https://github.com/shakenfist/shakenfist/actions/runs/36355421965) | 2026-09-27 22:28 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36362308091](https://github.com/shakenfist/shakenfist/actions/runs/36362308091) | 2026-09-28 00:27 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36368946187](https://github.com/shakenfist/shakenfist/actions/runs/36368946187) | 2026-09-28 02:12 | failure | 4 | 1 | 3 | 0 | 1 | 230.77 | 230.77 | slim-tier |
| [36373802370](https://github.com/shakenfist/shakenfist/actions/runs/36373802370) | 2026-09-28 03:27 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36379873986](https://github.com/shakenfist/shakenfist/actions/runs/36379873986) | 2026-09-28 04:57 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36388539886](https://github.com/shakenfist/shakenfist/actions/runs/36388539886) | 2026-09-28 06:51 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36397054248](https://github.com/shakenfist/shakenfist/actions/runs/36397054248) | 2026-09-28 08:23 | success | 0 | 0 | 0 | 0 | — | — | — | no-instrumented-bundle |
| [36397900038](https://github.com/shakenfist/shakenfist/actions/runs/36397900038) | 2026-09-28 08:31 | failure | 4 | 0 | 3 | 1 | — | — | — | — |
| [36412633283](https://github.com/shakenfist/shakenfist/actions/runs/36412633283) | 2026-09-28 10:56 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36420992021](https://github.com/shakenfist/shakenfist/actions/runs/36420992021) | 2026-09-28 12:18 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36471427507](https://github.com/shakenfist/shakenfist/actions/runs/36471427507) | 2026-09-28 19:19 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36486693407](https://github.com/shakenfist/shakenfist/actions/runs/36486693407) | 2026-09-28 21:31 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36501176601](https://github.com/shakenfist/shakenfist/actions/runs/36501176601) | 2026-09-29 00:03 | cancelled | 1 | 0 | 0 | 1 | — | — | — | — |
| [36501556771](https://github.com/shakenfist/shakenfist/actions/runs/36501556771) | 2026-09-29 00:07 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36513297089](https://github.com/shakenfist/shakenfist/actions/runs/36513297089) | 2026-09-29 02:35 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36525933640](https://github.com/shakenfist/shakenfist/actions/runs/36525933640) | 2026-09-29 05:23 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36534339321](https://github.com/shakenfist/shakenfist/actions/runs/36534339321) | 2026-09-29 07:02 | cancelled | 0 | 0 | 0 | 0 | — | — | — | no-instrumented-bundle |
| [36534621642](https://github.com/shakenfist/shakenfist/actions/runs/36534621642) | 2026-09-29 07:05 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36546002898](https://github.com/shakenfist/shakenfist/actions/runs/36546002898) | 2026-09-29 08:57 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36619029130](https://github.com/shakenfist/shakenfist/actions/runs/36619029130) | 2026-09-29 19:24 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36621572925](https://github.com/shakenfist/shakenfist/actions/runs/36621572925) | 2026-09-29 19:46 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36642349504](https://github.com/shakenfist/shakenfist/actions/runs/36642349504) | 2026-09-29 22:55 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36643979237](https://github.com/shakenfist/shakenfist/actions/runs/36643979237) | 2026-09-29 23:12 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36651832849](https://github.com/shakenfist/shakenfist/actions/runs/36651832849) | 2026-09-30 00:45 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36661716274](https://github.com/shakenfist/shakenfist/actions/runs/36661716274) | 2026-09-30 02:50 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36669390897](https://github.com/shakenfist/shakenfist/actions/runs/36669390897) | 2026-09-30 04:33 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36691412068](https://github.com/shakenfist/shakenfist/actions/runs/36691412068) | 2026-09-30 08:42 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36766640177](https://github.com/shakenfist/shakenfist/actions/runs/36766640177) | 2026-09-30 19:34 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36779210572](https://github.com/shakenfist/shakenfist/actions/runs/36779210572) | 2026-09-30 21:24 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36787346038](https://github.com/shakenfist/shakenfist/actions/runs/36787346038) | 2026-09-30 22:45 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36796852976](https://github.com/shakenfist/shakenfist/actions/runs/36796852976) | 2026-10-01 00:34 | success | 4 | 0 | 4 | 0 | — | — | — | — |
| [36805751208](https://github.com/shakenfist/shakenfist/actions/runs/36805751208) | 2026-10-01 02:25 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |
| [36806873191](https://github.com/shakenfist/shakenfist/actions/runs/36806873191) | 2026-10-01 02:39 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36830399584](https://github.com/shakenfist/shakenfist/actions/runs/36830399584) | 2026-10-01 07:27 | success | 0 | 0 | 0 | 0 | — | — | — | no-instrumented-bundle |
| [36938755972](https://github.com/shakenfist/shakenfist/actions/runs/36938755972) | 2026-10-01 23:04 | failure | 4 | 0 | 4 | 0 | — | — | — | — |
| [36949102882](https://github.com/shakenfist/shakenfist/actions/runs/36949102882) | 2026-10-02 01:03 | success | 4 | 2 | 2 | 0 | 2 | 461.34 | 290.83 | slim-primary, slim-tier |
| [36957255218](https://github.com/shakenfist/shakenfist/actions/runs/36957255218) | 2026-10-02 02:47 | success | 0 | 0 | 0 | 0 | — | — | — | no-cluster-jobs |

### The rule's arithmetic

D37's thresholds are applied as written, under all three scopings, with
no re-derivation. The denominator is `CLUSTER_HEADROOM_WAIT` = 420 s;
the statistic is the longest single wait in a unit.

**Abandon** needs **all three** of its clauses:

| Scoping | A1 no wait reaches 210 s | A2 fewer than 25% record any wait | A3 none reached 420 s | Abandon? |
|---|---|---|---|---|
| Per bundle | **no** -- max 290.83 s (0.692 x 420) | yes, 3/95 = 3.2% | yes, 0/95 | **no** |
| Per (run, topology) | **no** -- max 290.83 s | yes, 3/47 = 6.4% | yes, 0/47 | **no** |
| Per run | **no** -- max 290.83 s | yes, 2/24 = 8.3% | yes, 0/24 | **no** |

**Build** needs **any one** of its clauses:

| Scoping | B1 a wait reached 420 s | B2 longest > 210 s in > 10% | B3 > 50% record any wait | Build? |
|---|---|---|---|---|
| Per bundle | no, 0 | no, 2/95 = 2.1% | no, 3.2% | **no** |
| Per (run, topology) | no, 0 | no, 2/47 = 4.3% | no, 6.4% | **no** |
| Per run | no, 0 | no, 2/24 = 8.3% | no, 8.3% | **no** |

Neither set fires under any scoping, so the data fall in D37's middle
ground. The single extension D37 allows was spent on the first reading,
so the middle ground resolves to **Abandon**. The three scopings are
unanimous, and they are unanimous in selecting the default rather than
a clause.

### How fragile that is, recorded verbatim

The reading is not close to Build on the scoping D37's text supports
best, and it is one observation from Build on the scoping D37's text
supports least. Both halves of that belong in the record.

| Scoping | B2 now | Units > 210 s needed to pass 10% | Extra units to select Build |
|---|---|---|---|
| Per bundle | 2/95 = 2.1% | 10 | 8 |
| Per (run, topology) | 2/47 = 4.3% | 5 | 3 |
| Per run | 2/24 = 8.3% | 3 (12.5%) | **1** |

Under per-run scoping the reading is **one long-wait run from Build**.
With 24 units B2 evaluates only at multiples of 4.17%, so it cannot
land near its own 10% threshold at all: it is 8.3% or it is 12.5%, and
there is nothing in between.

**The mitigation, and it is a real one.** Per run is the scoping D37's
text supports *least*. D37's unit of observation is "one `merge_group`
run of a topology", which is the pooled (run, topology) scoping, and
that one needs **three** more long-wait units to reach Build, not one.
The thinnest margin in the table belongs to the loosest reading of the
rule. A third reading, if the project ever takes one, should fix the
scoping in advance the way D37 fixed the thresholds -- the one
ambiguity D37 left is the one that now carries the fragility.

### The three waits in full

| Seconds | % of 420 s | Topology | Test | Pinned | Mode | `binding_dimension` | Attempt | `cpus` headroom, refusal -> admission |
|---|---|---|---|---|---|---|---|---|
| 230.77 | 54.9% | slim-tier | `test_network_plumbing_lifecycle` | yes | informed | `cpus` | 1 | 0.0 -> 6.0 |
| 290.83 | 69.2% | slim-primary | `test_lifecycle_reboot_powered_off` | yes | informed | `cpus` | 1 | 0.0 -> 2.0 |
| 170.51 | 40.6% | slim-tier | `test_vanished_source_server_instance` | yes | informed | `cpus` | 1 | 0.0 -> 6.0 |

* **`binding_dimension` census: `cpus` x 3, no `null`.** The first
  window had one `null` -- a refusal with headroom, of the #3813/#3772
  shape rather than a shortage. This window has none.
* **Informed/degraded split: 3 informed, 0 degraded.** The degraded
  path has now gone 11 recorded waits across two windows without
  firing. That is either genuinely unnecessary or untested, and the
  record cannot tell which.
* **`attempt_number` is 1 for all three**, and every `read` unit holds
  exactly one wait, so each unit's total equals its longest. It is not
  summed; it is a 1-indexed position (F6).
* **0 malformed lines** across the whole window.
* The 6.0 `cpus` headroom at admission on `slim-tier` **is** the
  reshape, visible in the measurement itself: that is the `cpu: 6` the
  sizing plan's phase 4 applied.

### The fairness input: a null result that is untestable, not confirmed

D37 requires this whatever the decision, because it is the fairness
argument's only empirical input and open question 8 asserts "a pinned
create starves worst" without evidence.

| | Waits > 210 s | All waits |
|---|---|---|
| Pinned | **2** | **3** |
| Unpinned | **0** | **0** |

Across both windows that is **11 pinned waits and zero unpinned**:
eleven observations of one arm.

**The claim is untestable on this data, not confirmed, and the reason
is structural rather than statistical.** Every refused create in the
suite is a pinned create, so there is no unpinned arm to compare
against, however long the window runs. The mechanism is worth stating
because it is not "the suite only pins": **20** call sites across 11
files pass `force_placement`, out of the **117** wrapped creates phase 2
counted, so the suite issues far *more* unpinned creates than pinned
ones -- and not one of them has produced a wait in either window. An
unpinned create is refused only when the whole cluster is full, which
outside the saturation tests' deliberately impossible requests does not
happen in CI; a pinned create is refused as soon as its one node is
full. The selection is done by the scheduler and the
cloud's size, not by the harness choosing to pin. Either way the
comparison arm does not exist.

This refines rather than contradicts the first reading, which said an
unpinned refusal "would have been recorded had one occurred". It would
have been -- `pinned_to` is set unconditionally and
`_record_capacity_wait()` is called unconditionally. What the
instrument cannot do is *cause* one.

Said plainly, because this is the easiest sentence in the whole plan to
misread: **citing "all long waits were pinned" as support for "a pinned
create starves worst" would be reading a harness artefact as a
scheduler finding.** It is evidence about what the suite does, not
about what the scheduler prefers. Open question 8's fairness assertion
remains unevidenced after two windows, and testing it needs an
instrument that issues unpinned creates against a full cluster -- which
is not CI data and not this phase's work.

### What the evidence could not cover (D40)

D40 requires these three whatever the outcome, and they bite harder on
an Abandon than on an Extend, because an Abandon is what a later reader
will quote. All three were re-verified against the tree for this
window.

#### Evidence limit 1 -- CI-only

Every observation here is the functional suite on `slim-primary` and
`slim-tier`. An operator script against a genuinely full production
cluster is not represented, and that is D38's own strongest
counter-argument: such a caller gets a refusal where a queue would get
an instance. Nothing in either window speaks to it. This Abandon
establishes that two topologies of one test suite did not need a
server-side queue; it does not establish that no cluster does.

#### Evidence limit 2 -- post-reshape

The window opens on the reshaped `slim-tier` by construction (D36), and
the reshape is visible in the measurement rather than only in the
gate: both `slim-tier` waits, one near each end of the window, record
6.0 `cpus` of headroom at admission, which is the `cpu: 6` the sizing
plan's phase 4 applied. So the reading says nothing about whether a
queue would have helped the ledger-3 cloud that produced
[#3772](https://github.com/shakenfist/shakenfist/issues/3772) -- the
cloud the reshape exists to replace. The question "would a queue have
saved those runs?" is not answered here and cannot be: that cloud no
longer exists to measure.

#### Evidence limit 3 -- client retry off

Phase 4's D33 ships the client retry switched off, and the suite still
does not turn it on. Re-verified for this window:
`grep -riE 'retry[_a-z]*=' shakenfist/deploy/shakenfist_ci/` matches
nothing anywhere in the suite, and `base.py:215` still builds
`apiclient.Client(async_strategy=apiclient.ASYNC_PAUSE)`. So no client
in either window was retrying on its own behalf, and every wait
recorded here is the suite's own wrapper waiting. The phase 4 contract
has not been exercised by a retrying client in any data this decision
rests on.

### Four objections to D37, recorded rather than acted on

D37 instructs step 5d to apply the rule even over its own objection,
and to record the objection instead of acting on it. There are four,
in descending order of how much they bear on this decision. None of
them was acted on; the arithmetic above is D37 as written.

1. **The default did all the work, and the reason D37 gives for that
   default does not fit this window.** D37 justifies
   middle-ground-resolves-to-Abandon with "a condition that has not
   shown itself in 40 runs is not the condition a new state machine is
   built for". The condition *has* shown itself -- three times in this
   window, at 41%, 55% and 69% of the deadline, in both topologies, and
   eight times in the first. What has not shown itself is the condition
   at the **frequency** B2 and B3 require. "Rare but substantial" and
   "absent" are different findings, and D37's default collapses them
   into one word. The record must not let `Abandon` be read as "no
   waits were observed"; that is why this section of the plan says so
   three times.
2. **B1 cannot detect its own trigger in the multi-wait case.**
   `_record_capacity_wait()` is called once per wait and *before* the
   deadline is tested (`base.py:435`, the check at `:437`). So a create
   refused three times at 150 s each exhausts the 420 s deadline, fails
   the run, and leaves three trace lines none of which reaches 420 s.
   D37 scores B1 on "the longest single wait", so B1 reads **false on a
   run that died on the deadline** -- the exact event B1 exists to
   catch. The correct statistic for B1 is per-create *cumulative* wait.
   This is harmless in this window, because every `read` unit holds
   exactly one wait, but it is unsound as worded and should be fixed
   before any third reading.
3. **B2's threshold is unusable at the per-run denominator.** With 24
   units the clause evaluates only at multiples of 4.17%, so it can
   read 8.3% or 12.5% and nothing near its own 10% line. A clause whose
   granularity is almost half its threshold is not measuring what it
   claims to. See *How fragile that is* above.
4. **`not-wait-capable` was introduced by the gathering step rather
   than by a decision.** Correct in form, and answered in substance by
   **D44** above, which records the exclusion, states honestly that it
   was implemented before it was written up, and gives the git evidence
   that it restores D37's pre-registered denominator rather than
   amending it.

### Surprises in the second window

Recorded because a later reader will meet them, and because three of
them cut against the comfort of the decision.

* **The long waits are the same three tests in both windows, and two
  of the three got *longer* after the reshape.**

  | Test | Topology | First window | Second window |
  |---|---|---|---|
  | `test_lifecycle_reboot_powered_off` | slim-primary | 270.86 s | **290.83 s** |
  | `test_network_plumbing_lifecycle` | slim-tier | 90.29 s | **230.77 s** |
  | `test_vanished_source_server_instance` | slim-tier | 180.49 s | 170.51 s |

  Frequency fell sharply; **magnitude did not**. The window's longest
  wait is the longest wait either window has produced. Whatever the
  reshape did, it did not shorten the tail.
* **The signal is concentrated on the window's final day rather than
  decaying.** Wait-bearing qualifying units by day: 09-27 0/4, 09-28
  1/27, 09-29 0/24, 09-30 0/24, 10-01 0/12, **10-02 2/4**. Both waits
  over 210 s come from two runs, and the window's last unit-producing
  run -- 36949102882, 2026-10-02T01:03Z, conclusion **success** --
  produced two waits across two topologies, including the 290.83 s one.
  (The one run later than that, 36957255218 at 02:47Z, ran no cluster
  jobs.) An Abandon whose largest observation falls on its final day is
  less comfortable than one whose signal died out early.
* **50 of the 92 `empty` units come from runs whose conclusion is
  `failure`.** D43 admits them as real zeros, correctly under its own
  rule -- the trace file exists, nothing was refused in what ran -- but
  a run that aborted early is "zero waits in a truncated suite", and
  that biases A2 and B3 **downward**, which is toward the outcome
  selected. Success-only sensitivity:

  | Scoping | Qualifying | Any wait | Over 210 s |
  |---|---|---|---|
  | Per bundle | 44 | 2/44 = 4.5% | 1/44 = 2.3% |
  | Per (run, topology) | 22 | 2/22 = 9.1% | 1/22 = 4.5% |
  | Per run | 11 | 1/11 = 9.1% | 1/11 = 9.1% |

  Same direction, and no Build clause fires on any of them. But the
  per-run success-only slice has **only 11 qualifying units, below
  D36's threshold of 20, so it cannot carry a reading** and is recorded
  as a sensitivity check rather than as a result. The bias running
  toward the selected outcome is the uncomfortable direction, and it is
  worth a reader's attention even though correcting for it does not
  change the answer.
* **Zero degraded waits and zero null binding dimensions**, where the
  first window had one of the latter. Every wait in both windows is
  `informed`.
* **The v1 gather would have lost the second-longest wait in the
  window.** The 230.77 s `slim-tier` wait sits in a pre-rename
  `debian-12-*` bundle. `debian-12` units run 2026-09-27T19:13Z to
  2026-09-29T08:57Z (25 units); `debian-13` units run 2026-09-29T19:46Z
  to 2026-10-02T01:03Z (24 units). See F8.

### Two instrument findings from the second gather

Both are properties of the instrument rather than of the cloud, and
both need filing. Neither was fixed, because no code change is in this
phase's scope.

#### F8 -- `BUNDLE_TOPOLOGIES` is a point-in-time map applied retroactively to all history

`tools/ci_headroom_harvest.py`'s `BUNDLE_TOPOLOGIES` and
`UNINSTRUMENTED_BUNDLES` are keyed on the artifact bundle *name*, and a
harvest reads them as they stand today whatever date the artifact comes
from. **This window was hit by that twice, in opposite directions:**

* `e9e8c86658d` ("Rename the Debian 12 matrix lanes", merged
  2026-09-29T19:19Z in `2a94e582f`) retargeted the two Debian keys from
  `debian-12-*` to `debian-13-*`. Every pre-rename Debian bundle in the
  window then raised `UnknownBundleError` and was skipped: **25 bundles
  across 13 runs**, including the `slim-tier` lane the reshape was
  measured on. The v1 gather lost all of them, and the loss is silent
  by the tool's own design.
* `3723216d7` ("Instrument the Ansible modules cluster job", landed
  2026-09-30) moved `bundle-shakenfist-full-ansible-modules` *into*
  `BUNDLE_TOPOLOGIES`, which silently **added 25 bundles** to the
  enumerable set as units that could only ever read `absent` -- see F9
  and D44.

So one commit silently dropped 25 bundles and another silently added
25, and both edits were correct for harvesting forward. `e9e8c86658d`
said so in its own message: "an unmatched bundle is simply not
harvested, so the symptom of getting this wrong is a dataset which
quietly stops growing". The mirror image -- a *retrospective* dataset
that quietly starts at the rename -- is what happened here, and nothing
downstream treats `classify_artifact()`'s refusal to guess as a gap in
a measurement. `classify_artifact()` is right to refuse; the consumer
is wrong to be silent about it.

**Worked around in memory only**, in the 5c gather script, using the
topologies from `e9e8c86658d`'s own diff rather than guesses. **No repo
change:** a shared instrument that CI's own headroom gate loads is not
something to patch from inside an analysis step.

#### F9 -- `bundle-shakenfist-full-ansible-modules` cannot carry a wait trace

It is in `BUNDLE_TOPOLOGIES` because it banks a *headroom* series. It
cannot carry a *capacity-wait* trace: the suite is six Ansible
playbooks, `shakenfist/deploy/ansible_module_ci/001.yml` .. `006.yml`,
with no Python test harness, so
`shakenfist_ci.base.BaseTestCase.setUp()` -- where #4337 put
`ensure_capacity_wait_trace()` -- never executes. Empirically, 25 of 25
such bundles read `absent`, zero `empty`, on both sides of #4337.

This is **distinct from
[#4377](https://github.com/shakenfist/shakenfist/issues/4377)**, which
is about the three `test_kind == 'functional'` gates in
`shakenfist/actions`' `smoke-cluster.yml` keeping the *headroom probe*
from running. Even with those gates widened, a YAML-only suite still
writes no wait trace. The wait-capable set is a **subset** of
`BUNDLE_TOPOLOGIES`, and the tool has no way to say so. D44 records the
exclusion this justifies.

### Source corrections made for the second reading

Four line-number citations in this plan had drifted, all by the same
three lines: #4337 inserted `ensure_capacity_wait_trace()` and its
docstring above the constants and the wait path. Each is corrected at
source above.

| Was | Is | Where |
|---|---|---|
| `base.py:58` `CLUSTER_HEADROOM_WAIT` | **`:61`** | F5, D37's denominator, and the first reading's own source-correction record |
| `base.py:482` trace opened `'a'` | **`:516`** | the first reading's objections |
| `base.py:398` `pinned_to` | **`:432`** | the first reading's fairness input |
| `base.py:405` `_record_capacity_wait()` call | **`:435`** | the first reading's fairness input |

Two more drifted the same way and are corrected with them, which the
step's brief did not list: `CAPACITY_POLL_INTERVAL` `:64` -> **`:67`**
and `MAX_CREATE_ATTEMPTS` `:74` -> **`:77`**, both in F5. Six
citations, not four.

**A trap worth naming.** `base.py:204` is now *also* an
`open(CAPACITY_WAIT_TRACE_FILE, 'a')` -- the touch inside
`ensure_capacity_wait_trace()`. So a bare grep for that call returns
**two** hits, and the one the objections section means, the append that
writes a wait, is the **second**, at `:516`.

Three citations in the master plan had drifted too, by rather more,
and are corrected there: the phase 2 call-count amendment's
`base.py:1227` is now `:1794` and its `database_tier.py:275` is now
`:283`, and `assertRefusedAtStage()` is at `base.py:1552`, not
`:1464`.

**Worth saying out loud, as an observation rather than a change.** This
plan's first reading already corrected `CLUSTER_HEADROOM_WAIT` once,
from `:52` to `:58`, and #4337 moved it again within days. Line-number
citations in plan files drift whenever anything above them changes, so
correcting them is a treadmill rather than a fix. No convention is
changed here; the observation is left for whoever next writes a plan
that cites a line number.

### What phase 6 inherits, after the second reading

The first reading's list above is superseded on its first and third
points -- open question 8 is answered, and #4337 has landed as
`62bb1ddeb` -- and stands on the second, with better numbers.

* **Open question 8 is answered: no server-side queue.** Phase 6's
  documentation sweep can now describe a settled position -- but it
  must describe it the way this section does, as an Abandon selected by
  the rule's default with two waits past the 210 s line in the window,
  not as "a placement queue is unnecessary". The three evidence limits
  above are the sentences to carry across.
* **The transient contract does fire in CI, rarely and substantially.**
  3 waits over 95 instrumented bundles in this window, 8 over 104 in
  the first, all 11 pinned, all 11 `informed`, the longest 290.83 s
  against a 420 s deadline. Phase 6's #3772 comment should quote the
  second window's numbers rather than the first's, because the first
  window's unknown fraction makes its rates meaningless -- and it
  should say that two of the three long-wait tests got *longer* after
  the reshape, because a reader of the sizing plan's phase 4 closeout
  would not predict that.
* **The fairness assertion cannot be documented as a finding.** "A
  pinned create starves worst" is untestable on CI data. If phase 6
  touches the scheduler documentation, that assertion should be
  described as untested rather than quietly inherited.
* **Two instrument findings need filing, F8 and F9**, neither of which
  is this phase's work and neither of which was fixed here. They are
  candidates for phase 6's sweep or for the issue-fix workflow, in the
  same position #4337 was in after the first reading. F8 in particular
  affects any future retrospective read of the headroom or wait series,
  not just this phase.
* **The deadline arithmetic is untouched.** `CLUSTER_HEADROOM_WAIT`
  stays at 420 s. Nothing in either window argues for moving it: the
  longest wait ever recorded is 69% of it, and the first reading's
  note that re-tuning it is phase 6's business rather than phase 5's
  still holds.

### Definition of done, item by item -- second reading

Every item below was **run**, not read. That matters: two of the items
in this plan were wrong when first written and were found by executing
them, and three of phase 4's were wrong the same way.

| Item | Result | Note |
|---|---|---|
| 1 | **Met** | Ran `grep -n '_await_instance_create\|900 s ceiling' docs/plans/PLAN-transient-capacity-refusals.md`: no output, exit 1. |
| 2 | **Met** | Ran `tr '\n' ' ' < docs/plans/PLAN-transient-capacity-refusals.md \| tr -s ' ' \| grep -c 'default ceiling is \*\*600 s\*\*'`: returns `1`. |
| 3 | **Met** | Ran the `awk '/^### 8\./,/^### 9\./'` range over the master plan and `grep -c '105'`: returns `1`. The rewritten answer does not disturb the `defer_with_backoff()` correction, which is in the arguments section the reading left alone. |
| 4 | **Met** | Ran the same `awk` range, piped to `grep -c` for item 4's literal four-`507`-branches sentence: returns `1`. |
| 5 | **Met** | Open question 8 now leads `**No.**`, states the decision, carries both qualifications, and links to *Outcome -- second reading* here. The first reading could only meet this partly; it is met in full now. |
| 6 | **Met** | The item says "the qualifying-run count 5c reported", which was ambiguous on the first reading and is ambiguous again: 5c reported 39 qualifying runs, 25 of which produced a unit, and 97 bundle-level units. *Per qualifying run* above has exactly **39** rows -- `grep -c` over the table confirms it -- one per qualifying run, including the 14 that produced no unit, so the literal reading of the item is satisfied rather than argued with. The unknown count is stated as a number in the census table (`2` per bundle, `2` pooled per (run, topology), `1` per run), alongside the `25` excluded by D44. |
| 7 | **Met** | *The rule's arithmetic* shows all six clauses under all three scopings, with the figures that selected or rejected each. It also states which clause selected the outcome: none of them did. |
| 8 | **Met** | 2 pinned, 0 unpinned above 210 s; 3 pinned, 0 unpinned overall; 11 pinned, 0 unpinned across both windows. Recorded with the reason the split cannot be read as a scheduler finding. |
| 9 | **Met** | Ran the `awk` range over *What the evidence could not cover (D40)*: `CI-only` 3 hits, `post-reshape` 3, `retry` 8. Each of the three is also a `####` heading inside that section, so the grep cannot pass on a passing mention alone. |
| 10 | **Met** | The master plan's phase 5 row reads exactly `Complete`, and `docs/plans/index.md` reads `5 of 7` -- which `tools/check-plan-status.py` recomputes from the Execution table rather than taking on trust. The item permits either `Abandoned` or `Complete`; the choice of `Complete`, and the departure from step 5e's own brief that it represents, is argued at the top of this section. |
| 11 | **Not applicable** | Build-only, and this is not a Build. `PLAN-scheduler-reservations-phase-00-decisions.md` is untouched; D8 stands. |
| 12 | **Met** | Ran `python3 tools/check-plan-status.py`: "Plan statuses, index arithmetic and phase links agree." |
| 13 | **Met** | Ran `pre-commit run --all-files`: all hooks pass, including `check-plan-phase-references` and `check-plan-status`. |
