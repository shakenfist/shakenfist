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

**Correction, made while executing this plan.** D54 below originally
said three of these ten ranges are documentation-only. Six are. Phase
1's closeout (`e20dd7d4b`) and phase 2's closeout (`2c6206941`) touch
exactly three files each -- the phase plan, the master plan and
`docs/plans/index.md` -- and no code at all, so only four ranges carry
code: `7cc93750d`, `5ad9651ee`, `03cd7be3a` and `565e36e6e`. Steps 7b,
7c and 7e were briefed against six and found nothing to read in two of
them, which is how this was caught.

The trap worth recording, because it nearly inverted the finding:
`git show --name-only <merge>` prints *nothing* for a merge commit,
because `git show` defaults to a combined diff that lists only paths
differing from both parents. It is not evidence that a merge is empty.
The size figures in the table above were computed with
`git diff <sha>^1..<sha>`, which is the form `PUSH-AUDIT.md` specifies,
and they are unaffected.

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

### D54 -- The documentation-only ranges are audited as documentation, not as code

Phase 5's three ranges contain exactly one changed file each, the
phase 5 plan document; phase 1's and phase 2's closeout ranges contain
three apiece and no code, as the correction under D52 records.
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

## Outcome

Seven steps ran: wave 1 mechanical (7a), code quality (7b), test
coverage by mutation (7c), documentation (7d), security (7e), the
`client-python` half (7f), and the cross-phase lens (7g). Between them
they read all eleven ranges in D52 -- ten here and one in
`client-python` -- and this section pools them.

**Twenty-eight findings: 2 High, 5 Medium, 15 Low, 6 Informational.**
Two convergences were merged rather than double-counted, and both
convergences are themselves evidence: the suite wrapper's transient
boundary was reached independently by 7b (through the two unmarked
`CongestedNetwork` 507s that no wait can clear) and by 7g (through the
permanent scheduler stages), and the stale "exists only on the branch"
claim in the operator guide was reached independently by 7d and 7f.

The audit also found defects in this plan: two in its design (D54's
range count, and 7a's cost model), three definition-of-done items wrong
as written, and two findings its own steps overstated and which were
re-verified before being ranked. All seven are under Discrepancies
below.

### Findings, ranked

**Every disposition in this table is PROPOSED.** Per the Back brief's
gate, nothing has been filed and nothing has been declined in writing:
filing reserves work and may attract an automated fix, and a written
decline becomes the permanent record. Both are for the management
session to take to review.

| # | Finding | Range(s) | Lens | Severity | Proposed disposition |
|---|---|---|---|---|---|
| 1 | The CI `create_instance()` wrapper decides what is worth waiting out from the exception class, not from the `transient` field phase 4 publishes (`shakenfist/deploy/shakenfist_ci/base.py:391`). Two of `POST /instances`' four 507 branches are `CongestedNetwork` address exhaustion, which carries no marker and which no cpu/memory/disk predicate can see, so the wait is satisfied instantly and the create is replayed to `MAX_CREATE_ATTEMPTS` (44); a `cpu_max_per_instance` refusal instead burns the full 420 s. Either way the failure message blames headroom. This is the boundary phase 4's D35 identified as a defect and fixed on the server *and* the client, on the one half D35 did not look at. Found twice independently. | `5ad9651ee` × `565e36e6e` | code quality + cross-phase | **High** | **Filed** as [#4474](https://github.com/shakenfist/shakenfist/issues/4474), with `automated-fix-attempted`. The code change is one condition, but the failure message and its test carry the value, and a same-day automated patch would add the condition without the diagnosis. |
| 2 | `RunInnerCadenceTestCase` drives the resources daemon's loop with `clock_step=5` only (`shakenfist/tests/test_daemon_resources.py:605`), so the "no poll this tick" branch -- four of every five real iterations, since `self.idle(1)` runs against `DOMAIN_POLL_INTERVAL_SECONDS = 5` -- is never driven. Dropping `if polled_domains is not None` before `last_domains = polled_domains` (`shakenfist/daemons/resources/main.py:850-851`) survives the **whole** unit suite. In production that publishes a metrics row every 5 s on every node forever: fifteen database round trips per publish, which is the load regression phase 3 exists to avoid. | `03cd7be3a` | test coverage | **High** | **Filed** as [#4475](https://github.com/shakenfist/shakenfist/issues/4475); one issue covers all five mutation survivors, named for this one. |
| 3 | Nothing asserts that `_force_capacity_reconcile_if_unguarded()` is called from the maintenance pass (`shakenfist/daemons/cluster/main.py:1127`). Deleting the call survives the whole unit suite, so phase 1's central behavioural change -- moving the check off the election path, which is what closes the cold-cluster window -- could be reduced to dead code with the suite green. | `7cc93750d` | test coverage | Medium | **Filed** as [#4475](https://github.com/shakenfist/shakenfist/issues/4475), with `automated-fix-attempted`. Covers #2, #3, #4, #5, #6 and the drive-bys at #26 and #27; `7c-mutations.py` re-runs the proof. The label matters here: this plan's history is assertions that passed for the wrong reason, so each fix must be mutation-verified rather than merely added. |
| 4 | Only two of the three sides of the node-uuid `str()` join are covered. Dropping `str()` on the **metrics** side (`shakenfist/daemons/cluster/main.py:962`) passes; the capacity-rows and active-nodes sides both fail, so the test's intent is right and its coverage is not. The test's own comment names the cost: "the first spelling mismatch forces the five minute pass every sixty seconds forever, the second silently forces nothing." | `7cc93750d` | test coverage | Medium | **Filed** as [#4475](https://github.com/shakenfist/shakenfist/issues/4475). |
| 5 | The wait record's "no information" values are never distinguished from its "no room" values, at two sites. A poll that raises before the first readable roster can report `headroom_at_start = {cpus: 0, ...}` and `binding_dimension = 'cpus'` (`retries.py:519-521`), and a pinned target absent from the roster likewise (`retries.py:200-203`); both mutations pass. `binding_dimension` is the figure phase 5 reads as "what the create was short of", so an `/admin/resources` blip silently contributes a fabricated `cpus` shortfall to that dataset. One assertion pair fixes both. | `5ad9651ee` | test coverage | Medium | **Filed** as [#4475](https://github.com/shakenfist/shakenfist/issues/4475). |
| 6 | The operator guide's client-retry warning says `retry_transient_capacity` "exists only on" a `client-python` branch (`docs/operator_guide/capacity_refusals.md:172-174`). It has been on that repository's `develop` since 2026-09-18T16:21:32+10:00, about two hours *before* the server-side phase 4 merge the sentence belongs to, so it was true for roughly ten hours and false ever since -- including through phase 6's documentation sweep, which touched the same file. Phase 4's Future work excuses the missing *version number*; it does not excuse the claim that the code is unmerged. The client's own `docs/transient-capacity-retry.md` carries no unreleased warning at all. Found twice independently. | `565e36e6e` written, `c08196b19` swept past; `74d6e129b` for the client half | documentation + client-python | Medium | **Fixed here** for the server sentence -- replace "exists only on \[a branch]" with "merged to `develop`, not yet in a tagged release". One sentence, smaller than its description; this step is scoped to three bookkeeping files, so the management session applies it. The client-side half belongs with #7. |
| 7 | `APIException.headers` and `retry_transient_capacity` are in no release. `git tag --contains 74d6e129b` is empty, the commit describes as `v0.8.3-83-g74d6e12`, and v0.8.3 (2026-07-24) predates it by two months on both GitHub and PyPI. Phase 4's half of the contract is therefore correct and unreachable: its `Retry-After` functional assertion stays dark, and an installed client raises `TypeError` on the flag. | `74d6e129b` | client-python | Medium | **Filed** as [client-python#419](https://github.com/shakenfist/client-python/issues/419), no label (a release is a human action). On release, both unreleased-flag warnings (#6 and the client doc) become one minimum-version statement each. |
| 8 | The client retry's effective budget is ~60 s (`TRANSIENT_RETRY_MAXIMUM_ATTEMPTS = 5` × the server's fixed `Retry-After: 15`, under both async strategies) and was sized on phase 3's 5.3-5.6 s domain-destroy figure, which measures a mechanism rather than how long a refused create waits. Against the nine waits this plan has recorded (10.1 s to 290.8 s) the shipped retry would have cleared 3 of 9, and the master plan separately calls a 105 s budget too short for every wait phase 2 measured. The family argues both sides without noticing. | `565e36e6e` + `74d6e129b` × `2c6206941` | cross-phase | Low | **Declined**, with the reason recorded in Future work: the flag is off by default and in no release, so nothing is exposed today; the moment to re-size it is the release in #7, and re-sizing now would change another repository on no data newer than phase 5's second window. Not a correctness defect -- a sizing argument the plan should stop making on both sides. |
| 9 | The `transient` marker is an unqualified remote replay trigger: the clause gates on the flag and the marker with no restriction on method or URL (`shakenfist_client/apiclient.py:504-521`). A 507 marked `transient: true` on any endpoint makes the client resend that request byte for byte; `send_upload()` passes already-read bytes, and `transient_attempts` is per `_request_url` call, so a replay there could append a chunk twice, up to five times per chunk. No server path can do this today (two `capacity_error()` call sites, both scheduler), and the obligation is correctly documented as the server's -- but the client has no defence in depth against a future server mistake. | `74d6e129b` | security + client-python | Low | **Filed** as [client-python#420](https://github.com/shakenfist/client-python/issues/420), with `automated-fix-attempted` (a method/path allowlist is a design decision, not a patch). |
| 10 | `tools/check-plan-phase-references.py` sets `DOCS_DIR = 'docs'` and cannot see `.py` files at all, so the `plan-references-in-code` rule has no mechanical enforcement anywhere. `client-python` has no document carrying the block, so its citations are additionally unresolvable from that repository (bare relative paths to this repository's plan files, `apiclient.py:185-186`, `:208`, `test_client_apiclient.py:2319-2320`). | all four code ranges; `74d6e129b` | code quality + client-python | Low | **Already tracked** by [#4457](https://github.com/shakenfist/shakenfist/issues/4457), filed 2026-10-05; not duplicated. The audit's measured counts were added there as a comment. The structural gap is the finding; see the Discrepancies note on why the plan's own 25 added occurrences are not. |
| 11 | The invariant that makes the client retry fit the 600 s `await_instance_create` ceiling -- `(TRANSIENT_RETRY_MAXIMUM_ATTEMPTS - 1) * TRANSIENT_RETRY_MAXIMUM <= 600`, holding at 240 -- is implicit, cross-repository and untested. Raising `TRANSIENT_RETRY_MAXIMUM` for an unrelated reason would silently let one library call outlast the ceiling the whole plan is written against. | `74d6e129b` | client-python | Low | **Filed** as [client-python#421](https://github.com/shakenfist/client-python/issues/421) -- bundled with #12 and #13 as one low-cost hardening issue, no label. One assertion or one sentence closes it. |
| 12 | `test_the_sleep_is_clamped_to_the_remaining_deadline` asserts against the real wall clock (`deadline=time.time() + 5`, `assertGreater(slept, 4)`). A >1 s stall makes it fail. Every other time-sensitive test in that class drives a fake clock. | `74d6e129b` | client-python | Low | **Filed** as [client-python#421](https://github.com/shakenfist/client-python/issues/421). |
| 13 | The refusal `stage` is never read or logged by the client (`apiclient.py:521`, `:559-561`). Reading `transient` rather than re-deriving from `stage` is the right call, but the server publishes `stage` precisely so a caller can say *why*, and one format argument is the difference between a diagnosable and an opaque 60 s pause. | `74d6e129b` | client-python | Low | **Filed** as [client-python#421](https://github.com/shakenfist/client-python/issues/421). |
| 14 | The stage-exhaustiveness guard excludes any call that *names* `exception_class`, not one that names a non-507 class (`shakenfist/tests/test_capacity_error.py:177`). The skip exists for the single affinity site, which is answered 409. As written it would also skip a future 507-answered subclass, whose stage would fall silently into the non-transient default -- precisely the failure the test exists to prevent and which phase 4's Outcome claims it cannot have. Correct today; weaker than its claim. | `565e36e6e` | code quality | Low | **Fixed here** -- narrow the skip to the affinity class by name. One edit, smaller than the description. |
| 15 | The forced-reconcile counter's documented diagnostic is unreachable for the case it names. `docs/developer_guide/subsystem_internals.md:220-228` says the check "forces at most once per distinct unguarded set" and then tells an operator that "a count that keeps climbing in an otherwise steady cluster" means check and reconciler disagree -- but under the once-per-condition memory a *stable* disagreement increments once and never again. Only a churning unguarded set makes the counter climb. `docs/operator_guide/database.md:808-810` inherits the reading. | `7cc93750d` | code quality | Low | **Fixed here** -- one paragraph in two files. |
| 16 | `stage=CAPACITY_GUARD_STAGE` on the preflight exhaustion raise has no reader (`shakenfist/operations/node_inst_netdesc_op.py:289-292`). The only production reader of `.stage` is the create path; this raise happens inside an `except LowResourceException` in a cluster operation and never reaches the API. The comment says "the stage is carried alongside it so no handler has to parse it back out" -- there is no handler. Verified by grepping every `.stage` read. | `565e36e6e` | code quality | Low | **Declined**: harmless, and the field is the right shape for the handler a later phase would add. The comment's claim should lose its present tense, which is a drive-by on #15's edit rather than a finding of its own. |
| 17 | Four verbatim copies of the same nine-line `# raw-create:` justification (`cluster_ci_tests/test_object_names.py:120,137,222,234`). The guard requires a non-empty reason specifically so the allowlist "cannot grow by copy-pasting an existing marker line without writing a new sentence"; the same diff then copy-pasted one paragraph four times. Satisfied in the letter, defeated in the spirit. | `5ad9651ee` | code quality | Low | **Fixed here** -- a short marker on each site plus one shared explanation above the class. |
| 18 | Admin-scoped capacity data -- node UUIDs and per-node headroom figures from `/admin/resources` -- is written to `instance-waits.jsonl` on every wait and ships into the downloadable CI artifact bundle unconditionally (`base.py:467-520`). The repository is public and the traces are read from inside downloaded bundles, so the audience widens from "holds `system_client` credentials" to "anyone who can read an Actions run". D14 chose the mechanism deliberately but discusses *how* the file reaches the bundle, not *whether* admin-scoped data belongs there. | `5ad9651ee` | security | Low | **Declined**, with the reason written down rather than assumed: the cluster is ephemeral and torn down after the run, and momentary headroom figures and per-run node UUIDs have no standing value. The point of the finding is that this is now a conscious "yes, and that is fine because ephemeral" instead of an implicit one. |
| 19 | The new Prometheus counter is unpinned in both its name and its increment: renaming `scheduler_capacity_reconcile_forced_total` (`scheduled_tasks.py:284-288`) or deleting the `.inc()` (`main.py:986`) survives the whole unit suite. A metric name is an operator-visible contract, published in two guides, and a dashboard dependency. | `7cc93750d` | test coverage | Low | **Filed** as [#4475](https://github.com/shakenfist/shakenfist/issues/4475). |
| 20 | `AGENTS.md` in `client-python` grows 55 lines for one feature (`:126-180`), three of six bullets near-equivalents of `docs/transient-capacity-retry.md`. `llm-doc-discipline` says growth there is itself a finding, and that file is loaded into every session on every task. The don't-tidy-this framing earns its place; the restatement does not. | `74d6e129b` | client-python | Low | **Filed** as [client-python#422](https://github.com/shakenfist/client-python/issues/422) -- bundled with #21 as one documentation-discipline issue, no label. Or leave to the daily consistency audit, which enforces the same block fleet-wide. |
| 21 | `client-python`'s `CLAUDE.md:64-83` is a changelog entry, and cites a plan phase at `:66` ("the client half of phase 4 of shakenfist's `PLAN-transient-capacity-refusals.md`"). The `Recent Changes` section predates this diff; the phase reference is new, and a reader wants to know whether the feature exists. | `74d6e129b` | client-python | Low | **Filed** as [client-python#422](https://github.com/shakenfist/client-python/issues/422). |
| 22 | Four long files grown further past the `source-file-size` thresholds with no stated reason: `tools/ci_headroom_report.py` (3181), `shakenfist/external_api/base.py` (2606), `shakenfist/deploy/shakenfist_ci/base.py` (1883), `shakenfist/tests/test_ci_capacity_wait.py` (1318). All were over before these ranges, which added +350, +121, +418 and +1,224. | `7cc93750d`, `5ad9651ee`, `565e36e6e` | code quality | Low | **Declined** as a finding against these ranges -- all four were over the threshold before the plan opened, and splitting `ci_headroom_report.py` (which has now taken material additions from three separate plans, and whose `--waits` is an existing seam) is its own change with its own plan. Worth a Future work bullet, not an issue. |
| 23 | Phase 3's recorded sibling false pass no longer exists, so the master plan's Future work entry describing it is stale. Commit `e7d8aaa36` (issue 4214, outside this plan) replaced the count-based rise/drop predicate with a timestamp comparison; `_await_cpu_measured`, `idle_measured + cpus` and `baseline_measured - cpus` are all absent from the tree, and the prescribed fix has nowhere to apply. The replacement has its own weaker false pass, which it names honestly, and the compensating `_should_publish_metrics` unit tests are sound (M30, M31, M32 all fail). | `03cd7be3a`, test since rewritten outside the plan | test coverage (documentation) | Informational | **Fixed here** -- mark the bullet `Resolved:` and describe the replacement's own weaker false pass. The adjacent `get_all_domains()` bullet already uses that convention, so the plan has the form. |
| 24 | The "95 instrumented bundle-units" figure loses the distinction that makes it add up when restated outside phase 5's own table: `92 empty / 3 read / 2 absent` reads as a sum of 95 and totals 97. At source, `Qualifying` = `read` + `empty` is D37's own denominator and `Unknown` = `absent` + `unparseable` is counted *in addition*. It changes nothing (2/95 and 2/97 land on the same side of a 25% gate), but the flattened form appears in two downstream places and will trip the next auditor exactly as it tripped this one. | `48584e589` (source table) restated in `c08196b19` | documentation | Informational | **Fixed here** -- one clause in phase 6's Outcome. The posted GitHub comment carries the same wording and is left alone; an audit does not rewrite a record of what was said. |
| 25 | `_should_publish_metrics()`'s docstring describes a fallback its call site does not take (`shakenfist/daemons/resources/main.py:183-186` against `:829-842`): "a failed poll falls through to the interval gate" is wrong, because the poll sits inside the same `try` as the gate, so a raising poll abandons the iteration. Harmless -- four in five iterations still reach the gate -- but the sentence names the wrong mechanism. | `03cd7be3a` | code quality | Informational | **Fixed here** -- one sentence. |
| 26 | `test_a_cold_cluster_forces_again_once_metrics_appear` ends in `self.assertTrue(self._became_due(m))` (`test_daemon_cluster_schedule_anchoring.py:639`), so M41 reports only `AssertionError: False is not true`. The test *name* carries the diagnosis, which is most of the value; `_became_due` is used in nine tests in the class and a message naming the condition would be cheap. | `7cc93750d` | test coverage | Informational | **Filed** as [#4475](https://github.com/shakenfist/shakenfist/issues/4475), as a drive-by. |
| 27 | The blind wait path increments `degraded_polls` while leaving `polls` at zero (`retries.py:299-303`), so that record claims more degraded polls than polls. Neither field reaches the JSONL, so nothing reads it. | `5ad9651ee` | code quality | Informational | **Filed** as [#4475](https://github.com/shakenfist/shakenfist/issues/4475), as a drive-by. Recorded because a later harvester could start reading these fields. |
| 28 | The master plan's `defer_with_backoff()` argument closes on "would give up before the shortest recorded wait had cleared" (`PLAN-transient-capacity-refusals.md:503-509`). The sentence is explicitly scoped to phase 2's three waits (190.5, 160.4, 140.6 s) and is accurate for them; the trailing phrase is loose in a document that later records 10.079 s and 10.163 s waits. A wording imprecision, not a false claim. | `03cd7be3a` × phase 5's D41 edit | cross-phase (documentation) | Informational | **Fixed here** -- three words: "the shortest wait phase 2 measured". |

### The three findings that carry judgement

**The wrapper, and why its convergence is the result rather than its
severity.** Finding 1 is the only thing in this audit that no single
phase's review could have seen, which is the premise D58 was written
on, and it is the premise being paid off. Phase 4's review round found
that the exception class was the wrong discriminator, wrote that down
as D35 in language that could not be clearer -- "a client that retried
one would replay a structurally impossible request until its deadline
for nothing" -- and fixed the server and the client. Phase 2 was
already closed, so nobody applied D35 to the wrapper, and no phase's
Outcome records the gap. Two steps with disjoint briefs then walked
into it from opposite directions: 7b asked whether phase 2's *catch*
was too wide (everything its own review had worried about was the
opposite failure, a predicate too narrow) and arrived via the two
unmarked `CongestedNetwork` 507s; 7g compared the two halves
side by side as D58 instructed and arrived via the permanent scheduler
stages. Neither knew of the other. That is the strongest evidence in
this audit that the finding is real, and it is why it is ranked above a
finding whose consequence is arguably larger. It is also mostly latent:
`test_saturation.py`'s four permanent-stage assertions deliberately
carry `# raw-create:` markers, so no test drives a permanent stage
through the wrapper today -- but the AST guard forces every *other*
create through it, so the exposure is one new test or one shifted
topology away, and the address-exhaustion half is not latent at all.

**What mutation testing was for.** Finding 2 is the audit's best
argument for its own method. The resources daemon's code is correct
today; what 7c establishes is that it could stop being correct and
nothing would notice. Reading that test suite would not have found it --
`test_no_poll_this_tick_falls_through_to_the_interval` exists and
passes, and covers the gate in isolation; what is uncovered is the
loop's bookkeeping, and the only reason the gap is visible is that the
harness drives the loop at `clock_step=5` while production drives it at
`self.idle(1)` against a 5 s interval, so the branch taken in four of
every five real iterations is taken in none of the test's. Fifty-three
mutations over four ranges, forty-seven caught, five survivors, every
survivor re-run against the whole unit suite before being called a
finding -- and one defective mutation (M13) reported as defective
rather than as a pass. That ratio is the useful number: the guards this
plan wrote on purpose, after being burned, are sound (phase 4's stage
parser catches a stage added, dropped or renamed in either direction;
phase 2's raw-create allowlist catches both shapes its own history
records it having missed, and refuses to pass vacuously). What survives
is the bookkeeping nobody thought of as a guard, which is exactly where
a mutation pass earns its cost.

**Correct and unreachable.** Findings 6 and 7 are one situation seen
from two sides, and the judgement is about what an audit owes a reader
rather than about any defect. The client half of phase 4 is well built:
it gates on the server's published `transient` marker instead of
re-deriving the classification, so it inherits one source of truth and
*cannot* reproduce the mirror image of the server's own defect; it
degrades to a clamped 15 s on every malformed `Retry-After`; it fits
the 600 s ceiling; and seven deliberate mutations all fail. None of it
is reachable. It is in no tag and no release, two months of releases
behind, so phase 4's `Retry-After` functional assertion is still dark
and the only user-visible consequence of the whole client half is an
operator-guide warning that has been false since ten hours after it was
written. Phase 4's Future work records the missing version number and
reads, reasonably, as though that were the whole gap -- it is not; the
weaker fact was recorded and the stronger one was not. The disposition
asks for a release, which is the one thing that turns all of this into
working software and retires both warnings at once.

### Definition of done

Each item run, not read. Three were wrong as written; they are reported
here and again under Discrepancies rather than reinterpreted to pass.

**1. Phase 7's Execution row reads `Complete` with `Merged` of `—`, and
phase 6's reads `Complete` with `c08196b19`.**
**Met-with-discrepancy** -- the assertion holds, the command does not.

```
$ grep -c '^| [0-9] |' docs/plans/PLAN-transient-capacity-refusals.md
0
$ echo $?
1
```

The Execution rows begin `| 1. Close the warm-up window...`, so
`^| [0-9] |` cannot match any of them and the item as written fails
against a correct table. The corrected form:

```
$ grep -n '^| [67]\.' docs/plans/PLAN-transient-capacity-refusals.md \
    | cut -d'|' -f2,4,5
 6. Documentation and close-out | Complete | `c08196b19` (#4448)
 7. Push audit | Complete | —
```

Both halves of the assertion are true. Recorded as a discrepancy.

**2. `docs/plans/index.md`'s row reads `Complete` and `7 of 7`, and
`python3 tools/check-plan-status.py` exits 0.** **Met.**

```
$ sed -n '116p' docs/plans/index.md | tail -c 60
 since ten hours after it was written | Complete | 7 of 7 |
$ python3 tools/check-plan-status.py
Plan statuses, index arithmetic and phase links agree.
$ echo $?
0
```

**3. The Outcome contains a findings table with one row per finding,
each carrying a disposition of fixed, filed with a number, or declined
with a reason.** **Met-with-discrepancy.** The table above has 28 rows,
one per finding, each with a disposition. None carries an issue number
and none is declined, because the item contradicts this plan's own Back
brief: "7h must propose its disposition table and stop for review
before filing anything or writing a decline." An issue number cannot
exist before filing, and a decline in writing is the thing the gate
exists to prevent. Every disposition is marked **PROPOSED**. The item
should have said "a proposed disposition of fix, file or decline, with
the issue title rather than a number". Recorded as a discrepancy.

**4. No finding cites `b398cb890`, `a5e4a5e8c` or `48584e589` under a
code-quality, test-coverage or security lens.** **Met.**

```
$ grep -c 'b398cb890\|a5e4a5e8c' <findings table>
0
$ grep -n '48584e589' <findings table>
| 24 | ... | `48584e589` (source table) restated in `c08196b19` | documentation | Informational | ...
```

The one citation of the three is finding 24, under the documentation
lens, which D54 and D55 permit and D54 specifically asks for. No code,
test or security row cites any of the three: 7b, 7c and 7e each read
only the four code-bearing ranges and said so.

**5. At least one row cites `74d6e129b`, or a sentence records that the
range was audited and produced nothing.** **Met.**

```
$ grep -c '74d6e129b' <findings table>
10
```

Findings 6, 7, 8, 9, 10, 11, 12, 13, 20 and 21 cite it. The range was
audited in full by 7f and in part by 7a, 7d
and 7e; it produced ten of the twenty-eight findings and nothing
blocking on correctness.

**6. `grep -c '4438\|4403\|4197'` over the findings table returns 0, or
each occurrence is a cross-reference.** **Met.**

```
$ grep -c '4438\|4403\|4197' <findings table>
0
```

None of the three appears in the table at all. #4197 is referred to
once in this Outcome, outside the table, under "what came back clean":
7b confirmed that phase 3's `activity_coupled` marks switch budget
enforcement off rather than refining the model, found it already
recorded in the master plan's Future work and already filed as #4197,
and therefore did not report it. That is a cross-reference to a
disposition, which D56 says is not a finding.

**7. Both of D58's questions have a stated answer, including "they
agree" where that is the answer.** **Met.** Question 1 (does phase 4's
client retry agree with phase 2's wrapper?): **no on what is
retryable** -- finding 1, the audit's highest-ranked result; **no on
the deadline**, by about 7x (420 s and 44 attempts against ~60 s and 5),
which finding 8 reports as a sizing argument rather than a defect; and
**agreed-to-differ on what is recorded**, which is not a finding
because it is disposed of twice over, in phase 4's survey finding 5 and
in the client's own documentation, which tells a caller who needs to
record a wait to retry in their own code instead. Question 2 (do phases
1, 2 and 3 assume behaviour another changed?): **no -- they agree**,
with six days of margin. Phase 5's first measurement window opens
2026-09-19T22:00:54Z, six days after the last of the three merged
(`03cd7be3a`, 2026-09-13T22:19:43Z), so no behaviour change lands
inside either window; phase 2's own three waits are correctly scoped to
a pre-phase-3 cloud and claim nothing about the post-phase-3 one; and
phase 2's ledger predicate still models the server's admission
correctly after phase 3, which made the two ledgers agree sooner
without changing what either means. The predicate-level interaction was
looked for specifically and is not there.

**8. 7a's table covers all eleven ranges, with each failure classified
anachronistic or contemporaneous.** **Met-with-discrepancy.** All
eleven rows are present. There are no failures to classify: every
range that was executed passed every hook in its own
`.pre-commit-config.yaml`, 8 of 11 ranges in total
(`7cc93750d`, `e20dd7d4b`, `5ad9651ee`, `2c6206941`, `03cd7be3a`,
`565e36e6e`, `b398cb890`, and `client-python`'s `74d6e129b`). Three
(`a5e4a5e8c`, `48584e589`, `c08196b19`) are classified from each
commit's own config without being executed, for the cost reason under
Discrepancies, and 7a claims neither a pass nor a failure for them. The
structural result that replaces the classification: hook counts run
10, 10, 10, 10, 11, 11, 11, 12, 12, 14 across the ten in-repository
ranges against today's 14, so every range predates `test-internal-ca`
and `test-kerbside-cluster-config` except `c08196b19`, and a
hypothetical failure in either would be anachronistic by construction.
`client-python`'s config at `74d6e129b` is identical to its current
`develop` HEAD, because that commit *is* its HEAD, so there is no drift
to check there. The item asked for a split of failures rather than a
pass count, which was the right thing to ask for; what it did not
anticipate is zero failures and three unexecuted ranges.

**9. 7c states a mutation count greater than zero and names at least
one guard that failed to fail.** **Met.** 53 distinct mutations, 57
runs, 47 producing a failure. Five guards failed to fail, all five
re-confirmed against the whole unit suite rather than a narrow filter:
findings 2, 3, 4, 5 and 19 above. A sixth survivor (M13) is reported as
a defective mutation rather than as a finding -- the 507 description
names `Retry-After` twice, so removing one proves nothing, and M52 and
M53 show the guard is sound.

**10. Every item reported with the command run and its output, and any
item wrong as written recorded as a discrepancy rather than
reinterpreted to pass.** **Met.** Items 1, 3 and 8 are reported as
Met-with-discrepancy above and again under Discrepancies. The plan
predicted this ("Expect at least one to be wrong; three phases running
is a rate, not a coincidence"); three is the rate holding.

**11. `pre-commit run --all-files` passes, run once by the management
session after the last step.** **Not yet run.** This step did not run
it, by instruction: pre-commit's stash is repository-wide, and the plan
makes the hooks the management session's to run centrally. The item is
correct as written and is the one thing between this Outcome and a
commit.

### Discrepancies -- what execution found wrong with this plan

**Six of the ten in-repository ranges are documentation-only, not
three.** D54 said three. Phase 1's closeout (`e20dd7d4b`) and phase 2's
closeout (`2c6206941`) touch exactly three files each -- the phase plan,
the master plan and `docs/plans/index.md` -- and no code, so only four
ranges carry code. Steps 7b, 7c and 7e were briefed against six and
each independently found nothing to read in two of them, which is how
it was caught. D52 now records the correction, and with it the trap that
nearly inverted it: `git show --name-only <merge>` prints *nothing* for
a merge commit, because `git show` defaults to a combined diff listing
only paths that differ from both parents. That is not evidence a merge
is empty. The size figures in D52 were computed with
`git diff <sha>^1..<sha>`, the form `PUSH-AUDIT.md` specifies, and are
unaffected.

**7a's cost model ignored what this repository's pre-commit hooks
are.** The brief told 7a to run `pre-commit run --all-files` in a
scratch worktree for each of eleven ranges. In this repository the `py3`
and `flake8` hooks *are* `tox -epy3` and `tox -eflake8`, so each range
meant a dependency install and a full `stestr` suite -- eleven venv
builds and eleven suite runs for a wave-1 check. 7a executed 8 of 11
and classified 3 from configuration only, and reported that split
honestly rather than implying coverage it did not have. A bare `tox`
was not attempted at all, on the same reasoning: `envlist = py3,flake8,cover`
would re-run the same suite twice more per range for no new signal. The
plan should have costed the hooks before committing a step to eleven
runs of them, and should have specified a cheaper wave 1 -- the per-range
question is "did this range pass its own contemporaneous config", which
`tox -eflake8` plus the non-tox hooks answers for a fraction of the cost.

**Definition-of-done item 1 cannot match a correct table.**
`grep -c '^| [0-9] |'` returns 0 against the Execution table, because
every row begins `| N. <title> |`. Written from a memory of the table
rather than against it.

**Definition-of-done item 3 contradicts the Back brief.** It requires
every disposition to be "fixed, filed with a number, or declined with a
reason", while the Back brief requires 7h to propose and stop before
filing anything or writing a decline. Both cannot be satisfied; the
gate wins, so the table carries proposed dispositions and issue titles
instead of numbers.

**Definition-of-done item 8 assumed there would be failures to
classify.** There were none in the eight executed ranges, and three
ranges were not executed. The item is not wrong so much as
unsatisfiable in the shape it expects; what it should have asked for is
the hook-count drift against today's config, which is the result that
actually carries information.

**7b's plan-reference count was overstated, and its framing with it.**
7b reported 42 added `PLAN-`/`phase N`/`D<n>` occurrences in production
code. The verified figure is 25, across the four code ranges, against
239 live in `shakenfist/**.py` today -- so this plan contributed about a
tenth of a repository-wide pre-existing pattern. That reframes the
finding: the 25 are not what matters, the absence of any mechanical
check is, which is why finding 10 is scoped to
`tools/check-plan-phase-references.py` and its `DOCS_DIR = 'docs'`
rather than to the lines this plan added.

**7g's F-7g-3 was overstated.** It reported
`PLAN-transient-capacity-refusals.md:503-509` as a stale fact. Read in
place, the sentence is explicitly scoped to phase 2's three waits and
is accurate for them; only the trailing phrase "the shortest recorded
wait" is loose, in a document that later records 10 s waits. Finding 28
ranks it as a wording imprecision, which is what it is.

### What came back clean, and is worth not re-deriving

* **Wave 1.** No contemporaneous hook failure in any executed range:
  8 of 11 ranges passed every hook defined in their own config, with
  zero failures of any kind.
* **The guards this plan wrote on purpose.** Phase 4's
  stage-classification parser catches a stage added, a classification
  dropped and a stage renamed, in both set directions, and refuses to
  pass vacuously. Phase 2's raw-create allowlist guard catches an
  unmarked create in a real suite file, a marker emptied of its reason,
  and both shapes its own history records it having missed, and names
  the file and line every time -- the strongest guard in the four
  ranges. The client's retry survives seven deliberate mutations.
* **Phase 1's reconciliation path cannot fire twice.** The
  once-per-condition memory keys on the observation rather than a
  boolean, the `CAPACITY_TABLE_EMPTY` sentinel makes an empty table
  structurally incomparable to a partly-populated one, and
  "read failed" is distinguished from "nothing unguarded" in three
  separate places. A repeated-force loop was constructed for and not
  found. Only the elected cluster daemon runs it, so there is no second
  reconciler to race.
* **Phase 3's publish is bounded and correctly paced.** At most one
  publish per 5 s poll; `last_metrics` is reset on every publish
  whichever gate caused it, so a change publish suppresses the
  following tick rather than being followed by a near-duplicate; and
  the libvirt handle is opened and closed per poll rather than cached.
  What the phase gives up is enforcement, via the four
  `activity_coupled` marks -- already recorded in the master plan's
  Future work and filed as #4197, so not a finding here.
* **The six documented facts agree with the code and with each
  other.** The 420 s deadline, the `Retry-After` contract and which
  refusals carry it, the `stage`/`transient` fields and the four 507
  branches, what an empty capacity-wait trace means after #4337, the
  five-state trace vocabulary against D43's `absent`, and open question
  8's answer. Two of these had already produced a defect in phase 6's
  review round; both are now consistent, and the vocabulary is stated
  in only one place, so it has nowhere to disagree with itself.
* **Security.** No new endpoint, no new `mariadb` or `text()` call
  site, no new `subprocess`/`os.system`/`shell=True`, no new lock or
  shared state, and no filesystem path built from request data in any
  of the five code-bearing ranges across both repositories. The 507
  body publishes `stage` from a closed, hand-reviewed vocabulary of
  filter names, and `capacity_error()` replaces the whole body with
  `set_data()`, so even a `TESTING`-flagged traceback from
  `shakenfist-utilities` could not escape. The client retry rebuilds
  headers from the cached bearer token rather than re-authenticating,
  masks the token in debug logging, and honours a clamped
  `Retry-After`. The one pre-existing leak noticed -- the
  `%d candidates refused it` count in the capacity-guard refusal
  message -- is outside every audited range and is recorded here only
  so the next audit does not rediscover it as new.
* **D58's second question.** Phases 1, 2 and 3 did nothing to each
  other, and nothing moved under phase 5's denominator. See
  definition-of-done item 7.

### What the dispositions became

All 28 findings are disposed of. Eight were fixed in this phase's own pull
request, four were declined in writing with the reason recorded, and sixteen are
tracked:

| Issue | Repository | Findings | Label |
|---|---|---|---|
| [#4474](https://github.com/shakenfist/shakenfist/issues/4474) | `shakenfist` | 1 | `automated-fix-attempted` |
| [#4475](https://github.com/shakenfist/shakenfist/issues/4475) | `shakenfist` | 2, 3, 4, 5, 19, 26, 27 | `automated-fix-attempted` |
| [#4457](https://github.com/shakenfist/shakenfist/issues/4457) | `shakenfist` | 10 | -- (pre-existing) |
| [client-python#419](https://github.com/shakenfist/client-python/issues/419) | `client-python` | 7 | none |
| [client-python#420](https://github.com/shakenfist/client-python/issues/420) | `client-python` | 9 | none |
| [client-python#421](https://github.com/shakenfist/client-python/issues/421) | `client-python` | 11, 12, 13 | none |
| [client-python#422](https://github.com/shakenfist/client-python/issues/422) | `client-python` | 20, 21 | none |

Two things about that table are worth more than the numbers in it.

**Finding 10 was already filed, and the audit nearly duplicated it.**
[#4457](https://github.com/shakenfist/shakenfist/issues/4457) was raised on
2026-10-05, a day before this audit ran, and cites the same `DOCS_DIR = 'docs'`
evidence. It was found only because two transient GitHub API failures forced a
search before a third attempt. The audit's contribution is the measured scale --
239 occurrences live in `shakenfist/**/*.py`, of which this plan added 25 -- which
was added there as a comment instead. An audit which files a duplicate is adding
noise to the thing it is supposed to be clarifying, and the habit that prevents it
is searching before filing rather than after.

**No label was applied in `client-python`, because that repository has no
autofixer.** `automated-fix-attempted` exists in `shakenfist` to reserve an issue
against `issue-fix.yml`; `client-python` has no such workflow, and the label does
not exist there. Applying the fleet's convention without checking it applies is
how a convention becomes cargo cult, so it was checked and dropped.
