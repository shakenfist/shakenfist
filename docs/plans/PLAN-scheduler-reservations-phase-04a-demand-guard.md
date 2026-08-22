# Scheduler reservations phase 4a: a satisfiable demand guard

## Prompt

This is a phase plan under
[PLAN-scheduler-reservations.md](PLAN-scheduler-reservations.md).
The master plan's Prompt section applies unchanged; the
decisions it refers to as D-numbers live in
[PLAN-scheduler-reservations-phase-00-decisions.md](PLAN-scheduler-reservations-phase-00-decisions.md)
and the P-numbers in
[PLAN-scheduler-reservations-phase-03-primitive.md](PLAN-scheduler-reservations-phase-03-primitive.md).
This phase's own decisions are numbered E1..E7 so they collide
with neither.

Planning effort: **high**. The phase changes the shape of a
guard inside the admission transaction that phase 3 spent a
whole step stabilising against `innodb_snapshot_isolation`,
and it settles a tuning constant that two earlier documents
disagree about. Review effort: **high**.

## Situation

Phase 4a exists because of issue #3813, and because phase 4
cannot be closed out honestly without it.

Phase 3 shipped the D13 demand feedforward clause inside the
node guard of `_direct_admit_instance_placement()`. The clause
asks

```
cpu_load_1 + expected_demand + demand_add
    <= SCHEDULER_TARGET_LOAD x cpu_schedulable
```

The budget on the right is denominated per schedulable thread.
The charge `demand_add` on the left is denominated per
requested vCPU, at `cpus x SCHEDULER_DEMAND_PER_VCPU` with the
constant seeded at 2.5. The two were never reconciled, so at
the seed constants a node needs `cpu_schedulable >= 3.34`
before it can admit a 1-vCPU instance *at zero measured load
and zero expected demand*. A 2-vCPU instance needs seven
threads; a 4-vCPU instance fourteen.

The CI hypervisors publish `cpu_schedulable: 2`. Every
candidate is refused, every time, on demand alone.

This phase makes the clause satisfiable, and then discharges
the two soaks that phases 3 and 4 left outstanding -- in that
order, because soaking phase 4's claim accounting on sfcbr
while every placement takes the waiver path would soak the
wrong system.

## Mission and problem statement

Make the D13 demand clause a spreader that can never refuse a
placement a node has real room for, at every node size this
project supports; correct the constant it was seeded with and
the documents that disagree about its provenance; then run the
outstanding phase 3 and phase 4 soaks and close phase 4.

The phase is done when a single-thread node admits an 8-vCPU
instance at idle, when a second placement into a burst is
spread rather than piled, and when the master plan's phase 3
and phase 4 rows both read `Complete` without a footnote.

## Scope

**In scope**

* The shape of `_demand_guard_clause()`
  (`shakenfist/mariadb.py:24976`).
* The default and description of
  `SCHEDULER_DEMAND_PER_VCPU` (`shakenfist/config.py:353`).
* Correcting D13's provenance sentence, the master plan's
  phase 6 correction, the phase 3 row flag and the master
  plan's Future work entry.
* Operator-facing documentation of what the clause now means.
* Phase 3's outstanding step 9 sfcbr soak and phase 4's
  outstanding step 10 operator review and sfcbr soak.
* Closing #3813, and flipping the phase 3, phase 4 and phase
  4a statuses.

**Out of scope**

* Flipping `CLAIM_ENFORCEMENT_HARD`, migrating the
  `Scheduler()` callers, or extracting the duplicated
  `place_walk` helper. That is all phase 5, and this phase
  must not anticipate it. (Scope guard: no diff to
  `mariadb.py:24815`, and the two `place_walk` copies stay
  two copies.)
* Removing the P9 waiver. See E3.
* The `SCHEDULER_DEMAND_DECAY_SECONDS` decay model, the
  per-namespace learned demand value D13 defers, and the
  affinity questions phase 6 owns.
* Phase 00a's outstanding post-deploy validation. It is a
  separate observation on a different question (does the
  network+database node still take a disproportionate share)
  and it belongs to phase 00a's own close-out, though the
  sfcbr burst this phase runs is the natural occasion to
  collect it. Noted in Future work rather than claimed here.

## What the survey found (2026-08-22)

Four findings, three of which change what this phase does.

**1. The seed constant was transcribed from the wrong row of
its own measurements.** D13
(`PLAN-scheduler-reservations-phase-00-decisions.md:439-443`)
says `SCHEDULER_DEMAND_PER_VCPU` is "seed 2.5 from the 00a-1
measurements". The 00a-1 Measurements appendix
(`PLAN-scheduler-reservations-phase-00a-load-aware-ordering.md:316-320`)
records the observed demand-per-vCPU -- `cpu_load_1 /
allocated_vcpus` -- as **0.12-0.35 in steady CI with a burst
peak estimated at ~0.6**, and says in terms: "Seed constant
for open question 13's expected-demand model: ~0.33 steady /
0.6 conservative."

The only 2.5-shaped number in that appendix is on a different
row entirely: "busy plain nodes run 2.3-3.0 **allocated vCPUs
per thread**", the packing figure that produced
`CPU_OVERCOMMIT_RATIO = 3.0`. That is a vCPUs-per-thread
quantity being used as a load-per-vCPU quantity. The seed is
not merely untuned; it is four to eight times the measured
conservative figure, and the units error is the whole of
#3813's arithmetic.

This upgrades the fix from "a tuning decision pending the
phase 0 step 3 data analysis" -- which is how the master plan
currently defers it -- to "the data analysis already answered
this and the answer was mis-copied". The plan corrects the
master plan at source.

**2. The P9 waiver does cover scheduled creates, so #3813
does not 507 on its own.** Both walkers -- the create path at
`shakenfist/external_api/instance.py:881-921` and the
queue-worker reschedule at
`shakenfist/operations/node_inst_netdesc_op.py:194-232` --
run `place_walk(True)`, and if every denial was
`demand_only`, re-run `place_walk(False)`. So the live
symptom is not a refused create. It is:

* every create paying two full candidate sweeps, one RPC per
  candidate per sweep, plus a denial-detail read per refusal
  (`_admission_denial_dimensions()`, `mariadb.py:25354`) and
  an audit event per refusal;
* the spreader never operating, because the clause cannot
  pass and the waiver ignores it;
* `expected_demand` still being incremented on every
  admission (the `SET` at `mariadb.py:25694` is unconditional,
  outside the `node_guarded` branch), so the column is
  maintained at write cost and read by nothing that can act
  on it.

**3. The master plan's phase 6 correction is wrong about the
mechanism.** It says that after every candidate is refused
"the create places through a single forced candidate and the
affinity stage has nothing left to rank"
(`PLAN-scheduler-reservations.md:497-509`). The re-walk
iterates the same `candidates` list in the same order, so the
scheduler's ranking -- affinity included -- is preserved
exactly; the waived walk takes the top-ranked candidate.

What is actually lost is the spreading. Because the clause
never passes, nothing makes the top-ranked candidate less
attractive to the *next* create in a burst, and the ranking
it competes against is `cpu_load_1 / cpu_schedulable` from a
metrics row up to 60 seconds stale (`scheduler.py:131`). A
burst therefore piles onto one node until a real allocation
dimension bites. That is a plausible contributor to #3565 and
it is a different claim from the one the master plan makes,
so phase 6 inherits a corrected premise rather than a wrong
one.

**4. Nothing outside the admission transaction reads
`expected_demand`.** `git grep` finds no occurrence in
`shakenfist/scheduler.py`. The term is written by the
reconciler and by admission, and read only by
`_demand_guard_clause()` and the denial-detail builder. So
changing the clause's shape cannot perturb candidate ranking,
`summarize_resources()`, or the admin resources API -- which
is what makes E2 a contained change rather than a scheduler
rework.

The survey found no other stale claim in the master plan's
phase 5 or phase 6 stubs.

**Corrected already, in the planning commit** -- do not redo
these in a later step: D13's provenance sentence and its
2026-08-22 amendment
(`PLAN-scheduler-reservations-phase-00-decisions.md`), the
phase 6 correction's mechanism and the Future work entry's
deferral (`PLAN-scheduler-reservations.md`), the phase 3 and
phase 4 status notes, the new Execution row, and
`docs/plans/index.md`'s phase arithmetic (4 of 10 becomes 4
of 11). One further drift was found and fixed while
registering: the master plan's phase 4 note said the
management review was outstanding, but the phase 4 plan
records step 9 as complete.

Finding 4 is recorded here only. What step 3 still owes is
the post-fix half: statements that are only true once the
code has changed.

## Decisions

**E1. Retune `SCHEDULER_DEMAND_PER_VCPU` from 2.5 to 0.6, and
say where 0.6 comes from.** The 00a-1 appendix's conservative
burst figure, not its steady-state 0.33: the term exists to
cover the actuation-to-observation gap during correlated
bursts, which is exactly the regime the 0.6 estimate was taken
from, and a spreader that under-charges stops spreading. The
description in `config.py` stops saying "a provisional seed
pending the scheduler reservations phase 0 step 3 data
analysis" -- that analysis is the 00a-1 appendix and it has
landed -- and cites the appendix instead.

`SCHEDULER_DEMAND_DECAY_SECONDS` keeps its provisional wording.
Nothing measured it, and this phase does not.

**E2. Test the node's existing state, not the incoming
placement, against the budget.** The clause becomes

```
cpu_load_1 + expected_demand <= SCHEDULER_TARGET_LOAD x cpu_schedulable
```

with `demand_add` removed from the comparison but still added
to `expected_demand` by the same UPDATE, exactly as today.

The reasoning, and this is the decision a reviewer is most
likely to want to argue with:

* It is dimensionally honest. Both sides are now node state
  in units of runnable threads. The old form added a
  per-request term to a per-node budget, which is the defect,
  and E1 alone does not remove it -- at 0.6 a 4-vCPU instance
  still charges 2.4 against a 2-thread node's budget of 1.5
  and is refused on an idle node. Retuning moves the
  unsatisfiability threshold; it does not abolish it -- 22
  of the 80 cells in E5's sweep still fail with the constant
  corrected and the clause unchanged. Only a
  form with no per-request term on the left is
  unsatisfiability-proof for every combination of node size
  and instance size, which is what the master plan's success
  criterion demands.
* The question it now asks -- "is this node already at or
  above its target load?" -- is the question a spreader
  should ask. The old form asked whether the node would be
  over target *after* this placement, which conflates
  spreading with bounding, and D13 is explicit that the term
  is a spreader and never a capacity bound.
* Check-then-charge is safe here because the guard is a real
  serialisation point. The comparison and the
  `expected_demand` increment are the same guarded UPDATE in
  the same transaction, so two concurrent admissions against
  one node serialise: the second sees the first's increment.
  The window this form permits is one over-target placement
  per node per decay period, not per burst.
* Real capacity is still bounded. The three allocation
  dimensions in the same WHERE clause are untouched, and
  `CPU_OVERCOMMIT_RATIO` still caps vCPUs per schedulable
  thread. Nothing here lets a node accept work it has no
  room for; it lets a node accept the *first* piece of work
  when it is idle, which it must.

The runner-up was flooring the budget --
`... <= max(target_load x schedulable, demand_add)`. It is a
smaller diff and it does fix the idle-node case, but it keeps
the per-request term on the left, so a 4-vCPU instance is
still refused by a 2-thread node carrying any measured load
at all, and the floor has to be re-derived every time either
denomination changes. Rejected as a patch over a units error
rather than a correction of it.

**E3. Keep the P9 waiver.** It is still reachable and still
correct: when every candidate is genuinely at or above target
load, refusing the create would turn a spreader into a rate
limit, which is what P9 exists to prevent. What changes is
that it stops being the only path a placement ever takes. The
waiver's audit event (`'no candidate admitted and some
refused on demand alone, waiving demand guard'`) becomes the
signal that the cluster is actually saturated rather than the
signal that the guard is broken, and step 4's soak reads it
that way.

**E4. No new configuration knob.** The temptation is a
`SCHEDULER_DEMAND_ENFORCE` boolean so an operator can switch
the clause off. `SCHEDULER_TARGET_LOAD <= 0` already disables
it (`mariadb.py:24999`), that path is tested
(`test_demand_clause_is_skipped_for_a_non_positive_target_load`),
and a second switch for one clause is a knob that exists
because we were unsure, not because an operator needs it.

**E5. The regression test is a property test over sizes, not
an example.** #3813 is a statement about a family: for every
node size and every instance size, an idle node admits. One
example at `cpu_schedulable: 2` would have passed against the
pre-phase-3 code and would pass against a floor that is
wrong at some other size. The test sweeps
`cpu_schedulable` in 1..16 against instance sizes in
{1, 2, 4, 8, 16} vCPU on an idle node and asserts admission
in every cell, and it must be mutation-tested against the
current clause rather than asserted to fail by inspection.
Computed at planning time: the current clause and seed admit
26 of the 80 cells, so the sweep must fail 54 of them before
the fix and none after. Retuning the constant alone (E1
without E2) admits 58 and still fails 22 -- which is the
arithmetic behind E2's claim that a retune moves the
threshold rather than abolishing it.

**E6. Phase 4's close-out is this phase's step 4, and phase
3's soak rides with it.** Both outstanding soaks want the
same sfcbr deployment and the same CI burst, and neither is
meaningful before E2 lands: soaking claim accounting while
every placement takes the waiver path measures the waiver,
not the accounting. Running them as one management-session
step is cheaper and more truthful than running them twice.

**E7. Close #3813 in this phase, not in phase 5.** The master
plan's success criteria make the whole plan uncloseable while
it is open, and the phase 6 correction makes phase 6
unplannable while the mechanism is unsettled. It is closed by
step 5, after the soak has been observed and not before --
the arithmetic is provable in a unit test but "the spreader
actually spreads on real hardware" is not.

## Design

### The clause

`_demand_guard_clause()` (`shakenfist/mariadb.py:24976-25013`)
keeps its signature, its `None` returns and both of its
fail-open behaviours:

* `target_load <= 0` still returns `None` and skips the
  clause entirely (the proto3 unset-double case, and E4's
  disable path).
* `schedulable IS NULL` still passes, for a node whose
  resources daemon has not yet published typed columns.
* A NULL `cpu_load_1` with a known thread count still
  coalesces to zero.

Only the comparison changes:

```python
return sa.or_(
    schedulable.is_(None),
    sa.func.coalesce(load, 0.0) + capacity.c.expected_demand
    <= target_load * schedulable)
```

`demand_add` is no longer read by the clause. It stays a
parameter of `_direct_admit_instance_placement()` and of the
RPC, because the UPDATE's `SET` still adds it to
`expected_demand`; the parameter simply stops being consulted
by the WHERE. Do not remove it from the signature, and do not
remove it from `_admission_denial_dimensions()`, where it
remains the `requested` figure the demand dimension reports.

### What a denial now reports

`_admission_denial_dimensions()` (`mariadb.py:25413-25429`)
builds the demand dimension as `limit = target_load x
cpu_schedulable`, `used = cpu_load_1 + expected_demand`,
`requested = demand_add`, and `_capacity_dimension()`
recomputes `exceeded` as `used + requested > limit`. Under E2
that would report `exceeded` for a denial the new clause did
not make, and -- worse -- `CapacityAdmissionDenied.demand_only`
(`shakenfist/exceptions.py:134-149`) is derived from the
`exceeded` set, so a mis-set demand flag changes whether the
P9 waiver fires.

So the demand dimension's `exceeded` must be computed the way
the clause is: `used > limit`, with `requested` reported for
diagnosis but not added. That is a deliberate divergence from
the three allocation dimensions, and it needs a comment
saying why, because every other dimension in that function is
a before-and-after triple.

### What does not change

The canonical statement order, the guarded-UPDATE-first
ER_CHECKREAD invariant, the `node_guarded` / `cluster_guarded`
/ `claim_guarded` split, the floored decrements, and the
reconciler's decay recompute. This phase touches one boolean
expression, one constant, and the `exceeded` derivation for
one dimension.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent | Status |
|------|--------|-------|-----------|---------------------|--------|
| 1 | high | opus | worktree | The clause and the constant. In `shakenfist/mariadb.py`, change `_demand_guard_clause()` (`:24976`) per the Design section: drop `demand_add` from the comparison, keep it as a parameter, keep both fail-open branches and the `target_load <= 0` skip. Rewrite the docstring's opening formula sentence to the new form and say why the incoming charge is not tested (E2). In `_admission_denial_dimensions()` (`:25354`, demand dimension at `:25413-25429`), compute the demand dimension's `exceeded` as `used > limit` rather than letting `_capacity_dimension()` add `requested` -- read `_capacity_dimension()` first and either pass a zero `requested` with the real figure carried separately or set `exceeded` explicitly, whichever keeps the reply shape unchanged for its consumers (`exceptions.py:134-149` derives `demand_only` from it, and `test_node_denial_reports_the_demand_dimension_too` at `tests/test_mariadb_capacity_admission.py:586` pins the triple). Comment the divergence. In `shakenfist/config.py`, change `SCHEDULER_DEMAND_PER_VCPU` (`:353`) from 2.5 to 0.6 and rewrite its description per E1, citing the 00a-1 Measurements appendix rather than "pending the phase 0 step 3 data analysis"; leave `SCHEDULER_DEMAND_DECAY_SECONDS` alone. Tests: the E5 property sweep (`cpu_schedulable` 1..16 x instance sizes {1,2,4,8,16} vCPU, idle node, admission expected in all 80 cells) as a new test in `tests/test_mariadb_capacity_admission.py`, mutation-tested against the pre-change clause; a spreading test that a second placement into a node already carrying `expected_demand` above its budget is refused with `demand_only` true; and the existing demand tests at `:440-465`, `:586-598` and `:1467` updated where the formula changed and left alone where it did not. Do not touch `CLAIM_ENFORCEMENT_HARD` or either `place_walk`. Commit subject: `scheduler: make the demand guard satisfiable.` | Complete -- the E5 sweep moved to step 2, see below |
| 2 | medium | opus | worktree | Live coverage, in `tests/test_mariadb_capacity_admission_live.py` beside `test_the_demand_clause_refuses_on_measured_load` (`:416`) and `test_the_demand_clause_passes_on_null_metrics` (`:436`). Two tests against a real server: a node with `cpu_schedulable = 1` and zero load admits an 8-vCPU instance (the #3813 case at the smallest supported size), and a node whose `expected_demand` already exceeds `target_load x cpu_schedulable` refuses, with the reply's demand dimension reporting `exceeded` true and the allocation dimensions all false so `demand_only` is true. Follow the file's existing fixture pattern for seeding `node_metrics` and `scheduler_node_capacity` rows; note the suite reports the server regime it ran under, and issue #3759 means CI does not exercise MariaDB 11 here. Commit subject: `tests: live coverage for the demand guard.` | Complete -- two existing live tests needed their premises restated, and the sweep landed here |
| 3 | low | sonnet | worktree | The post-fix half of the plan corrections; the provenance corrections already landed in the planning commit and must not be redone (see *What the survey found*). In `PLAN-scheduler-reservations.md`, move the #3813 Future work entry -- including its 2026-08-22 correction -- into "Bugs fixed during this work", condensed to what a reader needs after the fact: the units error, the fix, and the phase that made it. Clear the "carries an outstanding defect" clause from the phase 3 status note now that it is false. Check the master plan's success criterion for D13 (`The D13 demand clause admits placements on a node that has real room for them, at every node size this project supports`) reads true against what shipped, and say so rather than deleting it. Commit subject: `docs: record the demand guard defect as fixed.` | Complete |
| 4 | low | sonnet | worktree | Operator and developer documentation. In `docs/operator_guide/scheduler.md`, state what the demand term now does in one paragraph: it spreads correlated bursts by refusing nodes already at or above `SCHEDULER_TARGET_LOAD` per schedulable thread, it never refuses a node with real allocation room, and when every node is over target the waiver admits anyway rather than failing the create. Give the two constants and their measured provenance. In `docs/developer_guide/subsystem_internals.md`, update the admission-transaction description beside the placement one to carry the new clause and the `exceeded` divergence from step 1. Check `CLAUDE.md`'s scheduler capacity paragraph for anything the change falsifies and correct it if so; `ARCHITECTURE.md` and `AGENTS.md` only if the component inventory or a convention actually changed, which it should not have. Commit subject: `docs: the demand guard is a spreader, not a bound.` | Complete |
| 5 | n/a | management session | none | Deploy to sfcbr and soak, discharging three outstanding obligations at once (E6): phase 3's step 9 soak, phase 4's step 10 operator review and soak with a real claim on a real namespace, and this phase's own validation. Run a CI burst and record, in this plan's Soak observations section: whether the demand clause now passes for some candidates and refuses others (read the `'schedule candidate refused by capacity guard'` audit events and check `enforce_demand` is true on refusals that were then admitted elsewhere); how often the P9 waiver event fires, which under E3 should be rare and only under genuine saturation; whether a burst spreads across hypervisors rather than piling on the top-ranked node; and that the reconciler reports zero drift across the burst. Then the phase 4 claim soak proper: create a claim for a namespace, run instances in it, confirm the drawdown and that `/admin/resources` and the tables agree. Phase 00a's own post-deploy question -- whether the network+database node still takes a disproportionate share -- can be observed from the same burst; record it in phase 00a's plan, not this one. | Not started -- operator |
| 6 | low | sonnet | worktree | Close-out, after step 5 has been recorded. Set the phase 3, phase 4 and phase 4a rows to `Complete` in the master plan Execution table, remove the phase status notes that describe the soaks as outstanding, and confirm `docs/plans/index.md`'s row arithmetic is right for the new phase count (the phases column is arithmetic over the Execution table; adding 4a changes the denominator). Close #3813 with a comment naming the fix and the soak observation. Commit subject: `scheduler: close out phases 3, 4 and 4a.` | Not started -- gated on step 5 |

### Step notes

* **Step 1** was planned to carry the E5 satisfiability sweep. It could
  not: `test_mariadb_capacity_admission.py` runs against a mocked
  connection and asserts on compiled statement text, so it can show the
  charge has left the WHERE clause but cannot evaluate whether the
  resulting arithmetic admits anything. The sweep moved to step 2 and is
  evaluated as SQL against a real server, which is where the arithmetic
  that broke actually lives. What step 1 proves instead is that
  `demand_add` binds nowhere in the compiled comparison and still binds
  in the SET.
* **Step 2** found two existing live tests whose premises the fix
  invalidated, neither of which was in the plan.
  `test_the_demand_clause_refuses_on_measured_load` published a load
  that only exceeded the budget once the placement's charge was added,
  and `test_admit_release_cycling_returns_to_the_seeded_counters`
  crossed the budget on its fifth round under the old arithmetic. Both
  were restated rather than relaxed: they assert the same facts, at
  loads and round counts that are true of the new clause. This is the
  reason the phase wanted live coverage at all -- the unit suite passed
  throughout, because the live modules skip without a database.
* **Step 2** also ran the whole 364-test capacity suite against MariaDB
  11.8, which is past the 11.6.2 boundary where
  `innodb_snapshot_isolation` turns a transaction's leading `SELECT`
  into `ER_CHECKREAD`. CI runs 10.11 and cannot see that (#3759), so
  this is the first time this phase's transactions have been exercised
  under the regime the ER_CHECKREAD invariant exists for.
* The planning commit's mutation prediction held: the sweep refuses
  exactly 54 of the 80 cells against the pre-fix clause and seed, which
  is the figure computed from the arithmetic before any code was
  written.

## Risks and mitigations

* **The new clause lets a burst over-commit a node before
  the reconciler catches up.** Check-then-charge admits one
  placement onto an at-target node, and only the next
  admission sees the increment. Bounded by the guarded UPDATE
  serialising within a node, and by the three allocation
  dimensions which are unchanged. Checked by: step 1's
  spreading test, and step 5 reading the burst distribution
  on real hardware rather than trusting the unit test.
* **The `exceeded` divergence silently changes waiver
  eligibility.** `demand_only` is derived from the `exceeded`
  set, so getting the demand dimension's derivation wrong
  makes the waiver fire when it should not (masking real
  denials) or not fire when it should (507ing creates the
  cluster has room for). Checked by: step 1 updating
  `test_mariadb_capacity_admission.py:1627`'s waiver-
  eligibility tests explicitly, step 2's live assertion that
  a demand-only refusal reports exactly that, and the
  management review reading `_capacity_dimension()` and
  `exceptions.py:134-149` together.
* **0.6 is still wrong.** It is an estimate from one incident
  on one cluster, and this phase promotes it from provisional
  to cited. Mitigated by it now being dimensionally
  consistent, so being wrong makes the spreader too eager or
  too lax rather than inert; and by step 5 measuring the
  achieved figure again during the burst. If the soak
  contradicts it, the constant moves and the clause does not.
* **The soak conflates three questions.** Step 5 discharges
  obligations from three phases at once, and a single "it
  looked fine" observation would let all three through
  without evidence. Checked by: the step brief enumerating
  what must be separately recorded, and step 6 refusing to
  flip a status whose observation is not written down.
* **Scope creep into phase 5.** A satisfiable guard makes
  flipping the hard ceiling look easy. Checked by: the Scope
  section's explicit guard, and a `git diff` review that
  `mariadb.py:24815` and both `place_walk` copies are
  untouched.

## Definition of done

Falsifiable, and mostly runnable:

* For every `cpu_schedulable` in 1..16 and every instance
  size in {1, 2, 4, 8, 16} vCPU, an idle node with zero
  `expected_demand` admits -- 80 of 80. Against the
  pre-change clause and seed the same test fails 54 cells,
  and against the corrected seed with the old clause shape it
  fails 22 (both mutation-tested, not asserted by
  inspection).
* A node whose `cpu_load_1 + expected_demand` already exceeds
  `SCHEDULER_TARGET_LOAD x cpu_schedulable` refuses, the
  denial's demand dimension reports `exceeded` true, every
  allocation dimension reports false, and
  `CapacityAdmissionDenied.demand_only` is true.
* `git grep -n "demand_add" shakenfist/mariadb.py` shows no
  occurrence inside `_demand_guard_clause()`'s returned
  expression, and the parameter is still in its signature.
* `grep -n "2\.5" shakenfist/config.py` returns nothing.
  Checked at planning time: the only occurrence in the file
  today is `SCHEDULER_DEMAND_PER_VCPU`'s default at line 354,
  so this is an absolute check rather than a field-scoped
  one. Its description must also contain no occurrence of
  "provisional seed pending".
* `git diff develop -- shakenfist/mariadb.py | grep -E
  '^[+-].*CLAIM_ENFORCEMENT_HARD'` returns nothing -- changed
  lines only, since context lines around an unrelated edit
  would otherwise trip it (phase 5 scope guard).
* A live test admits an 8-vCPU instance onto a node with
  `cpu_schedulable = 1` at zero load, against a real server.
* No fact about the demand clause is stated differently in
  `docs/operator_guide/scheduler.md`,
  `docs/developer_guide/subsystem_internals.md`, `CLAUDE.md`,
  the master plan, and the phase 0 decisions document.
* The phase 0 decisions document no longer attributes 2.5 to
  the 00a-1 measurements, and the master plan's phase 6
  correction no longer claims the affinity stage has nothing
  to rank.
* Soak observations for the demand clause, the P9 waiver
  frequency, the burst distribution, reconciler drift, and
  the phase 4 claim drawdown are each written into the Soak
  observations section below -- five separate observations,
  not one summary.
* The master plan's phase 3, phase 4 and phase 4a rows read
  `Complete`, the phase status notes no longer describe an
  outstanding soak, and `docs/plans/index.md`'s phase
  arithmetic matches the Execution table.
* #3813 is closed.
* `pre-commit run --all-files` passes.

## Soak observations

_(Filled by step 5.)_

## Future work

* Phase 00a's post-deploy validation -- whether the
  network+database node still takes a disproportionate share
  of a CI burst -- remains outstanding against phase 00a, and
  is the last thing keeping that phase `In progress`. Step 5's
  burst is the natural occasion to collect it, but it is
  recorded there, not here.
* The per-namespace learned demand value D13 defers. Nothing
  in this phase makes it harder; the constant it replaces is
  now at least dimensionally correct.
* `SCHEDULER_DEMAND_DECAY_SECONDS = 600` is still an unmeasured
  provisional seed. If step 5's burst gives a usable
  time-to-visible-load figure, it belongs in the 00a
  Measurements appendix.
* The duplicated `place_walk` in `external_api/instance.py`
  and `operations/node_inst_netdesc_op.py` is still two
  copies with a comment asking that changes be made in both.
  Phase 5 owns extracting it; this phase deliberately leaves
  it, having touched neither.
* Issue #3759 (a MariaDB 11 CI job for the ER_CHECKREAD
  invariant) is unchanged by this phase but gates how much
  the step 2 live tests actually prove in CI.

## Back brief

Before executing, back-brief the operator on:

1. **E2, the clause's new shape.** This is the decision most
   likely to be argued with: dropping the incoming
   placement's charge from the comparison changes the
   question the guard asks, and the runner-up (flooring the
   budget) is a smaller diff. Confirm the reasoning about
   check-then-charge being safe inside a guarded UPDATE
   before any code moves.
2. **E1, 0.6 rather than 0.33.** Conservative burst figure
   over steady-state, on the grounds that the term exists for
   bursts.
3. **E6, folding three soaks into one step.** Confirm the
   operator is willing to run one sfcbr burst that discharges
   phase 3, phase 4 and phase 4a, and to record five separate
   observations from it.
4. **E7, closing #3813 only after the soak.** The alternative
   is closing it when step 1 lands, which is defensible and
   faster.
