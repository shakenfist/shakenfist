# Phase 7 -- Push audit

Part of [A capacity refusal is transient](PLAN-transient-capacity-refusals.md).

**Planning effort:** high. The judgement in this phase is almost
entirely in its scoping -- what the baseline is, which ranges carry
code, and what to do about the half that landed in another repository
with no audit document to cite. The auditing itself is mechanical
once those are settled.

## Context

Six phases have merged. Each was reviewed on its own pull request, and
each phase plan records what its review found and how it was disposed
of. Nobody has read the six together.

That is the gap this phase exists to close, and the master plan says
why it matters more here than it usually does: phases 1, 2 and 3 are
explicitly allowed to run in parallel, and phase 4's opt-in client
retry is meant to carry the same semantics phase 2 proved in the
suite wrapper. A divergence between those two is exactly the kind of
defect no single phase's review can see, because neither phase's diff
contains both halves.

## Scope

**In scope.** `PUSH-AUDIT.md` run across the ten in-repository merge
ranges and the one `client-python` range in D52, under that document's
own headings: wave 1's mechanical checks, and wave 2's code quality,
test coverage, documentation and security reviews, plus the
cross-phase lens in D58.

**In scope, unusually.** The `client-python` half, audited from here.
See D53; this is the decision most likely to be argued with.

**Out of scope.** Re-litigating the per-pull-request reviews. Each
phase plan records what its review found and what was done about it.
The audit may disagree with a disposition, but it starts by reading
what was decided rather than rediscovering it.

**Out of scope.** Fixing what the audit finds, unless the fix is
smaller than the finding's description. The master plan is explicit:
findings land as their own pull request, and the plan is not complete
until each is resolved or declined in writing.

**Out of scope.** Re-reading the capacity-wait data, extending the
measurement window, or revisiting open question 8. Phase 5 decided it
and phase 6 published the answer. D51 already declined a third
reading, and an audit is not the place to reopen a pre-registered
decision.

**Out of scope.** The two pre-registration debts -- D37's unfixed
scoping and B1's unsoundness. They are recorded in phase 6's Outcome
as conditions on any *future* reading. The audit restates them where
a reader will meet them; it does not fix them, because fixing them
means amending a rule whose whole point was to be fixed before the
data was seen. See D57.

**Out of scope.** The three issues this plan deliberately left open --
[#4438](https://github.com/shakenfist/shakenfist/issues/4438),
[#4403](https://github.com/shakenfist/shakenfist/issues/4403) and
[#4197](https://github.com/shakenfist/shakenfist/issues/4197). All
three are filed, two are labelled `automated-fix-attempted` to reserve
them for a human, and all three were declined in writing. An audit
finding that re-reports them is reporting the filing, not a defect.

## What the survey found

The master plan's phase 7 section was written before any phase ran.
Four of its claims are wrong, and two of the four would have sent the
audit to the wrong place.

**F1 -- `client-python` has no `PUSH-AUDIT.md`, so the audit this
section tells us to cite does not exist.** The section says the
out-of-repository half "is audited against that repository's default
branch as part of the pull request that lands it, with this phase
citing that audit rather than re-running it". There is no
`PUSH-AUDIT.md` in `client-python`, so no such audit was run and none
can be cited. This is the same shape the sibling sizing plan's phase 7
hit with `shakenfist/actions`, and it resolved it by pulling the
external half into scope and auditing it from here
(`PLAN-ci-cloud-sizing-phase-07-push-audit.md`, its D1 and D2). D53
follows that precedent.

**F2 -- the section names the wrong phase as the one that landed
outside this repository.** It says "Phase 2 may land partly outside
this repository: its suite wrapper touches the CI harness. Where it
does, its row names the repository." Phase 2's `Merged` cell names no
repository: both its merges, `5ad9651ee` (#4166) and `2c6206941`
(#4187), are in this repository, because the CI harness it touches
(`shakenfist/deploy/shakenfist_ci/`) lives here. The phase that landed
partly elsewhere is **phase 4**, whose cell reads `565e36e6e` (#4241),
`client-python` `74d6e129b` (client-python#399). Taken literally the
section would have the audit look for an external half of phase 2 that
does not exist, and treat phase 4's as in-repository.

**F3 -- phase 5's conditional describes an outcome that did not
happen, and reaches the right answer by the wrong route.** The section
says phase 5 "may produce no code at all, and if it closes as
Abandoned there is nothing here for the audit to read". Phase 5 closed
**Complete**, not Abandoned -- what was abandoned is the queue, which
is open question 8's answer rather than a phase status, and the phase
records that distinction deliberately. It is separately true that it
produced no code: all three of its merges touch only
`docs/plans/PLAN-transient-capacity-refusals-phase-05-queue-decision.md`.
So the audit does have nothing to read *as code* in those three
ranges, but not for the reason the section gives. D54 states the right
reason.

**F4 -- phase 6's `Merged` cell was blank, which `PUSH-AUDIT.md`
treats as a stopping condition.** That document says: "If a plan's
`Merged` column is missing or a cell is blank, stop and say so -- that
is an unauditable plan." Phase 6's cell read `—` until this phase's
close-out commit filled it with `c08196b19`. The planning commit for
this phase is therefore load-bearing for the audit's ability to run at
all, which is worth saying because it is the one piece of bookkeeping
no phase can do for itself.

**What the survey did not find.** The section's central instruction is
right, and is the part worth keeping: the baseline is the `Merged`
column rather than `develop...HEAD`, and auditing one phase at a time
would miss what the phases did to each other. `PUSH-AUDIT.md` agrees
independently and in more detail than the section does -- it specifies
`<sha>^1..<sha>` per merge, one pass per range rather than one pass
over a union git cannot express, and it names the blank-cell stopping
condition in F4. Nothing in the audit document contradicts the master
plan here.

**Corrected at source.** F2 and F3 are corrected in the master plan's
phase 7 section as part of this phase's planning commit, so the next
reader does not re-derive them. A later step must not redo that.

## Decisions

### D52 -- The baseline is the plan's merge ranges, and there are eleven

`git diff develop...HEAD` on this branch contains this plan document
and nothing else. Every command in `PUSH-AUDIT.md` would report
success against it while reading nothing.

In this repository:

| Phase | PR | Merge | Range | Size |
|-------|----|-------|-------|------|
| 1 | #4147 | `7cc93750d` | `7cc93750d^1..7cc93750d` | 12 files, +1418/-63 |
| 1 | #4153 | `e20dd7d4b` | `e20dd7d4b^1..e20dd7d4b` | 3 files, +65/-2 |
| 2 | #4166 | `5ad9651ee` | `5ad9651ee^1..5ad9651ee` | 51 files, +3669/-139 |
| 2 | #4187 | `2c6206941` | `2c6206941^1..2c6206941` | 3 files, +183/-2 |
| 3 | #4200 | `03cd7be3a` | `03cd7be3a^1..03cd7be3a` | 15 files, +1416/-71 |
| 4 | #4241 | `565e36e6e` | `565e36e6e^1..565e36e6e` | 20 files, +1851/-30 |
| 5 | #4362 | `b398cb890` | `b398cb890^1..b398cb890` | 3 files, +471/-26 |
| 5 | #4390 | `a5e4a5e8c` | `a5e4a5e8c^1..a5e4a5e8c` | 3 files, +138/-13 |
| 5 | #4406 | `48584e589` | `48584e589^1..48584e589` | 3 files, +763/-59 |
| 6 | #4448 | `c08196b19` | `c08196b19^1..c08196b19` | 8 files, +868/-50 |

10,842 insertions and 455 deletions. In `client-python`, per D53:

| Phase | PR | Merge | Size |
|-------|----|-------|------|
| 4 | client-python#399 | `74d6e129b` | 7 files, +737/-12 |

Eleven ranges, run one pass each and pooled. Not one pass over a
union, which git cannot express across two repositories in any case.

### D53 -- The `client-python` half is audited from here

The master plan says to cite that repository's own audit. F1 found
there is none to cite: `client-python` has no `PUSH-AUDIT.md`, so the
instruction is unexecutable rather than merely inconvenient.

Three options. Cite nothing and record the gap, which leaves phase 4's
client retry -- the half most likely to have diverged from phase 2's
semantics, and the thing the master plan singles out as what a
per-phase review cannot see -- unaudited, and would be reporting the
filing rather than doing the work. Add `PUSH-AUDIT.md` to
`client-python` and run it there, which is a change to another
repository, needs its own pull request, and makes this phase wait on
it. Or audit that range from here, against the audit document this
repository has.

The third. It is what the sibling sizing plan's phase 7 decided for
`shakenfist/actions` under the same constraint, so the fleet stays
consistent, and the alternative leaves the one divergence this phase
exists to look for unexamined.

**This is the decision most likely to be argued with**, on the grounds
that a repository's code should be audited under its own conventions
and by whoever maintains it. The counter is that `client-python`'s
conventions do not include a push audit at all, so "its own
conventions" is an empty set here, and the semantics being checked are
this plan's rather than that repository's. The audit reads that range
and reports; it does not push a fix there. Proposing `PUSH-AUDIT.md`
for `client-python` is worth doing and is recorded under Future work
rather than done here.

### D54 -- Phase 5's three ranges are audited as documentation, not as code

All three contain exactly one changed file, the phase 5 plan document.
Running the code-quality, test-coverage and security lenses over them
and recording a pass would be reporting an empty range, which is the
precise failure `PUSH-AUDIT.md`'s range rule exists to prevent.

So those three ranges get the documentation lens only, and the audit
says so explicitly rather than leaving three silent passes for a
reader to misread as clean code. This is the F3 situation reached by
the right route: not "the phase was Abandoned so there is nothing to
read", but "the phase was Complete and produced a decision rather than
a diff".

An audit that says what it had no diff to scope over is a result. One
that reports a clean run over an empty range is not.

### D55 -- Phase 6's range is audited like any other

Phase 6 is documentation, and its range is eight files of prose. It
would be easy to treat it as exempt on the same reasoning as D54, and
that would be wrong: phase 6 made substantive factual claims about the
system -- what an empty capacity-wait trace means, what the tool's
state vocabulary is, what the queue answer is -- and two of those were
wrong when first written and were corrected only because its review
round caught them. Documentation that asserts system behaviour is
auditable, and this range is where the plan's conclusions meet a
reader.

### D56 -- The audit reads the review record before looking for defects

Every phase plan has an Outcome recording what its review found. Four
of the six phases found a defect in the plan rather than in the tree,
and phases 4, 5 and 6 each found definition-of-done items that were
wrong as written. That is a map of where this plan's reasoning was
weakest, and an audit that ignores it will rediscover the same ground
slowly.

So each step reads the relevant Outcome sections first and states what
it expects to find before it looks. A finding that merely restates a
disposition already recorded is not a finding.

### D57 -- The pre-registration debts are restated, not fixed

D37's scoping was never pre-registered, and B1 scores on the longest
single wait so a create refused repeatedly can exhaust the 420 s
deadline with no single line reaching the threshold. Both are real,
both are recorded in phase 6's Outcome, and both are conditions on a
future reading rather than defects in merged code.

Fixing them means amending a decision rule after seeing the data it
was written to judge, which is what pre-registration exists to
prevent. The audit's job is to make sure a reader meets them before
taking a third reading, not to rewrite them now. Its finding, if any,
is about where they are recorded and whether that is where a future
reader will actually look.

### D58 -- The cross-phase lens is a named step, not a hope

The master plan's reason for an accumulated audit is that the phases
may have done something to each other. That will not happen as a
side-effect of auditing eleven ranges one at a time; each pass sees
one range.

So it is its own step, with two named questions rather than a general
instruction to look for interactions:

1. **Phase 4's client retry against phase 2's wrapper semantics.** Do
   they agree on what makes a refusal worth retrying, on the deadline,
   and on what is recorded? Phase 4's survey reversed a master-plan
   instruction here, deciding the CI suite must *not* turn the client
   retry on, so the two halves are deliberately not exercised
   together anywhere.
2. **Phases 1, 2 and 3 ran in parallel.** Phase 1 closed the warm-up
   window, phase 3 made metrics publish on domain-set change, and
   phase 2 measures waits that both of those shorten. Does any of the
   three assume a behaviour another changed underneath it?

### D59 -- This phase records no `Merged` cell

A push-audit phase closes itself out in its own pull request, because
there is no later phase whose planning would fill it in. Its row
reaches `Complete` and its `Merged` cell stays `—`, and the Execution
table's note already explains the column well enough that this needs
no new wording there.

The plan as a whole reaches `Complete` when this phase does, which
makes this the commit that closes a plan opened on 2026-09-08.

## Step plan

Steps 7b through 7f are independent and read disjoint ranges, so they
can run concurrently. 7g depends on nothing but reads across all of
them, and 7h pools.

**Each step writes its findings into its own section of this file and
commits that. No step edits another step's section, and no step runs
`pre-commit run --all-files`** -- pre-commit's stash is repository-wide,
so concurrent agents in one worktree silently revert each other. The
management session runs the hooks centrally between waves. Steps that
need a check may call `tools/check-doc-anchors.py` and
`tools/check-plan-phase-references.py` directly, which take no stash.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|---|---|---|---|---|
| 7a | medium | sonnet | none | **Wave 1, mechanical, across all eleven ranges.** `PUSH-AUDIT.md`'s wave 1 is `pre-commit run --all-files` and `tox`. Those run against a *tree*, not a range, so running them on `develop` tells you only that today passes. What wave 1 actually asks here is whether each range passed when it landed and whether anything has rotted since. For each of the ten in-repository ranges in D52, check out the merge commit in a scratch worktree (**not** this one -- `git worktree add --detach`), run `pre-commit run --all-files`, and record pass or fail with the hook names. Expect failures on older ranges from hooks added later; that is information, not a defect, so separate "failed because a hook did not exist yet" from "failed against its own contemporaneous config". Do the `client-python` range in a scratch worktree of `../client-python` with that repository's own pre-commit config. Do not fix anything. Record a table: range, hooks run, result, and whether the failure is anachronistic. |
| 7b | high | opus | none | **Wave 2 code quality over the four code-bearing ranges.** Ranges: `7cc93750d`, `e20dd7d4b`, `5ad9651ee`, `2c6206941`, `03cd7be3a`, `565e36e6e` -- phases 1 to 4. Read `PUSH-AUDIT.md`'s code-quality heading and apply it. Read each phase plan's Outcome first, per D56, and say what you expect before you look. The specific things this plan's history makes likely: phase 1's reconciliation path and whether it can fire twice; phase 2's wrapper swallowing a refusal it should not; phase 3's publish-on-change and the fifteen-round-trip sweep its own survey found uncosted. Do not audit phase 5's or phase 6's ranges -- D54 and the documentation step cover those. |
| 7c | high | opus | none | **Wave 2 test coverage over the same four phases' ranges.** Same ranges as 7b. The question `PUSH-AUDIT.md` asks is whether the tests would have failed, not whether they pass. This plan has a documented history of assertions that passed for the wrong reason: phase 3's closeout found its rise predicate satisfiable by a sibling instance, which is a false pass recorded under Future work and *not* fixed, and phase 4's review found the transient marker keyed on the exception class so impossible refusals were published as retryable. Mutate the tree to check the guards that matter -- break the property on purpose, confirm the test fails and names the right thing -- and keep the mutations in a script under `/tmp` so the set is runnable rather than described. State the mutation count. Do not commit the script. |
| 7d | medium | sonnet | none | **Wave 2 documentation over all eleven ranges.** This is the only lens that reads phase 5's three ranges and phase 6's, per D54 and D55. The question is whether any fact about this plan's subject is stated differently in two places: the 420 s deadline, the `Retry-After` contract, the `stage`/`transient` fields, what an empty capacity-wait trace means, the queue answer, and the state vocabulary (`not requested`, `unreadable`, `empty`, `read`, `unparseable` -- note the tool's `unreadable` is D43's `absent`). Phase 6's review round found the operator page and the tool's record disagreeing, so that class has already produced one defect. Check `docs/operator_guide/capacity_refusals.md`, `docs/developer_guide/ci_cloud_sizing.md`, `docs/developer_guide/ci.md`, `docs/operator_guide/scheduler.md` and the API reference against each other and against `tools/ci_headroom_report.py`. |
| 7e | medium | sonnet | none | **Wave 2 security over the code-bearing ranges, plus the `client-python` range.** Ranges as 7b plus `74d6e129b` in `../client-python`. `PUSH-AUDIT.md`'s security heading is the spec. The shapes worth looking for here specifically: a refusal body or a log line carrying something it should not now that `stage` and `transient` are published; the opt-in client retry replaying a request with credentials attached; and `/admin/resources` reads from the suite wrapper, which is an admin endpoint being called by test code. Grep for the shape, not the symbol. |
| 7f | high | opus | none | **The `client-python` half, audited from here (D53).** Range `74d6e129b^1..74d6e129b` in `../client-python`, 7 files, +737/-12. That repository has no `PUSH-AUDIT.md`, so apply this one's wave 2 headings. The thing to establish, and the reason this range is in scope at all: whether the opt-in retry honours the semantics phase 4 published -- retry only where `transient` is true, respect `Retry-After`, and never replay a structurally impossible refusal such as `cpu_max_per_instance`. Phase 4's review found the server side had this wrong once already, keyed on the exception class rather than the scheduler stage. Confirm the client does not have the mirror-image defect. Record whether `client-python` should carry its own `PUSH-AUDIT.md` as a Future work item; do not add one. |
| 7g | high | opus | none | **The cross-phase lens (D58).** Answer D58's two questions, in order, and do not generalise beyond them. For the first, read phase 2's wrapper (`shakenfist/deploy/shakenfist_ci/base.py`) and phase 4's client retry (`../client-python`, and the server's `stage`/`transient` publication) side by side and say whether they agree on what is retryable, on the deadline, and on what gets recorded -- phase 4's survey deliberately left them un-exercised together, so nothing in CI would catch a divergence. For the second, read phases 1, 2 and 3's ranges and ask whether any assumes behaviour another changed: phase 2's wait measurements are the denominator phase 5 read, and both phase 1 and phase 3 shorten the waits it measures. State plainly if the answer to either is "they agree", which is a result. |
| 7h | high | opus | none | **Pool, dispose, close out.** Collect every finding from 7a to 7g into a single ranked table in this file's Outcome: finding, range, lens, severity, disposition. Disposition is one of fixed here (only where the fix is smaller than the description), filed (with the issue number, and `--label automated-fix-attempted` where a same-day automated patch would be wrong), or declined in writing with the reason. The master plan requires each finding resolved or declined in writing before the plan is complete -- that sentence is this step's contract. Then: set phase 7 `Complete` in the master plan's Execution table with `Merged` left as `—` (D59), set the plan's own row to `Complete` and `7 of 7` in `docs/plans/index.md`, and make that row's description say what the audit found rather than that it ran. Run the definition-of-done items below and report each as Met, Met-with-discrepancy, or Not met, with the command and its output. If the audit found nothing, say so in one sentence rather than padding it. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| **Wave 1 reports failures that are only hooks added after the range landed**, and the audit reads as eleven broken ranges. | 7a's brief requires separating anachronistic failures from contemporaneous ones, and the definition of done asks for that split explicitly rather than for a pass count. |
| **Phase 5's three docs-only ranges get the code lenses and pass**, producing three clean rows that a reader takes as audited code. | D54 scopes them to documentation, 7b's brief names the ranges it must *not* read, and definition-of-done item 4 asserts no code finding cites a phase 5 range. |
| **The `client-python` range is skipped** because the master plan says to cite an audit, and the citation is easier than the work. | F1 establishes there is nothing to cite, D53 puts it in scope, and it is 7f's whole step rather than a clause in another brief. Definition-of-done item 5 requires a finding or an explicit "none" naming that range. |
| **Concurrent steps in one worktree revert each other** via pre-commit's repository-wide stash. | No step runs `pre-commit run --all-files`; the management session runs hooks between waves. Each step writes only its own section. This bit phase 6, where the plan's briefs told each step to run it and the deviation had to be made at execution time. |
| **The audit re-reports the three issues this plan declined in writing**, inflating the finding count with filings. | Named out of scope above with their numbers, and definition-of-done item 6 asserts none of the three appears as a new finding. |
| **7h marks a definition-of-done item Met on a generous reading.** Phases 4, 5 and 6 each had items that were wrong as written, found only by running them. | Item 10 requires each item's command and output quoted, and a wrong item recorded as a discrepancy rather than silently reinterpreted. Expect at least one to be wrong; three phases running is a rate, not a coincidence. |

## Definition of done

Every item is falsifiable and must be **run**, not read. Phase 4 had
three wrong items, phase 5 two, and phase 6 two. Finding another is
the expected outcome rather than a surprise.

1. `grep -c '^| [0-9] |' docs/plans/PLAN-transient-capacity-refusals.md`
   finds phase 7's Execution row reading `Complete` with a `Merged`
   cell of `—`, and phase 6's reading `Complete` with `c08196b19`.
2. `docs/plans/index.md`'s row for this plan reads `Complete` and
   `7 of 7`, and `python3 tools/check-plan-status.py` exits 0.
3. This file's Outcome contains a findings table with one row per
   finding, each carrying a disposition of fixed, filed with a number,
   or declined with a reason. If there are no findings, it contains
   one sentence saying so and no table.
4. No finding in that table cites `b398cb890`, `a5e4a5e8c` or
   `48584e589` under a code-quality, test-coverage or security lens.
   (D54: those three ranges are documentation only.)
5. The table contains at least one row citing `74d6e129b`, or an
   explicit sentence recording that the `client-python` range was
   audited and produced nothing. (D53: it must not be silently
   absent.)
6. `grep -c '4438\|4403\|4197' ` over the findings table returns 0, or
   each occurrence is a cross-reference rather than a new finding.
7. Both of D58's two questions have a stated answer, including
   "they agree" where that is the answer.
8. 7a's table covers all eleven ranges in D52, with each failure
   classified anachronistic or contemporaneous.
9. 7c states a mutation count greater than zero, and names at least
   one guard that failed to fail.
10. Every item above is reported in the Outcome with the command run
    and its output, and any item wrong as written is recorded as a
    discrepancy rather than reinterpreted to pass.
11. `pre-commit run --all-files` passes, run once by the management
    session after the last step.

## Back brief

Before starting, restate: the eleven ranges and which carry code; why
the `client-python` range is audited here rather than cited; and which
three ranges get the documentation lens only. If any of those three
comes back wrong, the brief was insufficient -- say so rather than
proceeding.

**Gate on 7h's dispositions.** Filing an issue and declining a finding
in writing are both hard to undo: a filed issue reserves work and may
attract an automated fix, and a written decline becomes the record.
7h must propose its disposition table and stop for review before
filing anything or writing a decline. Everything up to that point --
the findings themselves, their ranking, the bookkeeping -- needs no
gate.

No gate on 7a to 7g: they read and write their own sections, and a
wrong finding is cheap to correct before 7h pools it.

## Future work

* **`client-python` has no `PUSH-AUDIT.md`.** D53 audits its range
  from here as a one-off. The general fix is for that repository to
  carry the document, which is a change to another repository and
  needs its own pull request. Recorded here rather than done.
* **D37's scoping and B1's unsoundness** must both be pre-registered
  before any third reading of the capacity-wait data. Phase 6's
  Outcome is the current record; D57 keeps it there.
