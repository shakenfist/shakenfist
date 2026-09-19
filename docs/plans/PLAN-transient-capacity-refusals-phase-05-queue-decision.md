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
`shakenfist/deploy/shakenfist_ci/base.py:52`, polling every
`CAPACITY_POLL_INTERVAL = 10` s (`:58`) with
`MAX_CREATE_ATTEMPTS = CLUSTER_HEADROOM_WAIT // CAPACITY_POLL_INTERVAL + 2`
(`:68`).

This is the fact that makes the question a real question rather than a
formality. The waiting the queue would do is waiting the suite already
does, in the caller, where D8 said deferral belongs. A server-side
queue does not add the wait; it moves it, and for the duration of the
move both exist.

### F6 -- The reading tool is built and needs no new work

`tools/ci_headroom_report.py --waits <file>` (`:2648`) reads
`/srv/ci/traces/instance-waits.jsonl` from a downloaded bundle and
prints total seconds waited, the number of waits, the longest wait and
its test, and the informed/degraded split. `docs/developer_guide/ci.md:331`
documents it.

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
(`shakenfist_ci/base.py:52`). If phase 6 or a later change moves that
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
| 5c | medium | sonnet | none | **Gather (gated behind 5b).** For each qualifying `merge_group` run on the reshaped `slim-tier`, download the bundle and run `tools/ci_headroom_report.py --waits <bundle>/srv/ci/traces/instance-waits.jsonl`. Record per run: the number of waits, the total, the longest wait and its test id, the informed/degraded split, the `binding_dimension` of each wait, and whether the create was pinned (`node` set). A run whose trace is absent, empty or unparseable is recorded as **unknown** and excluded from the rule's denominator (F6) -- the tool already distinguishes these from zero, do not collapse them. Do **not** sum `attempt_number`; it is a 1-indexed position, not a count. Produce a table and the raw per-wait records, and nothing else: this step does not interpret. |
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
