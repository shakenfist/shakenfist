# A capacity refusal is transient: phase 6 -- documentation and close-out

Phase 6 of
[A capacity refusal is transient](PLAN-transient-capacity-refusals.md).

**Planning effort: medium.** The master plan sets this phase at medium
on the grounds that it "follows a pattern that already exists", and
that is the right call for the writing. The survey is where the effort
went, and it found that three of the four documentation targets this
phase was given have already shipped -- two of them in pages that did
not exist when the phase was written -- so the plan below is mostly a
narrowing of scope plus one correction that matters.

## Context

Phase 5 answered open question 8: the server should not queue a create
that does not fit. It reached that by reading two measurement windows,
the second over a fixed instrument, and it selected **Abandon**
unanimously across three candidate scopings but by the rule's
middle-ground default rather than by Abandon's own clauses. Phase 5's
*What phase 6 inherits, after the second reading* is the brief this
plan executes, and the sentences it asks to be carried across are
carried verbatim rather than paraphrased.

Phases 1 to 4 shipped code. This phase ships prose, two issue
comments, one new issue, and the plan's close-out. Phase 7 is the push
audit and runs after it.

## Scope

**In scope.**

* The one documentation statement that is now false:
  `docs/developer_guide/ci_cloud_sizing.md` still says an empty
  capacity-wait trace reads as unknown. Since #4337 it means a real
  zero, and phase 5's D43 is built on that.
* The two things that are true and documented nowhere:
  `ensure_capacity_wait_trace()`, and the answer to open question 8.
* Correcting this plan's own forward-looking references to a CI job
  that has been renamed, and its bug list, at source.
* Two sibling plans' cross-references that read as open questions and
  are now settled.
* A comment on #3772 carrying the before-and-after numbers, leaving it
  open.
* Filing phase 5's F9.
* Close-out: this phase to `Complete`, the index row, and *What phase 7
  inherits*.

**Out of scope, with reasons.**

* **Changing `tools/ci_headroom_report.py`.** Its four-state report is
  correct and deliberately conservative; see D46. The divergence is in
  the prose, not the tool.
* **Re-harvesting a window.** Phase 5 gathered two. D51.
* **Closing #3772.** D48.
* **Fixing F8 (#4403) or F9.** Neither is this plan's work. Phase 5
  said so and nothing here changes it.
* **Re-tuning `CLUSTER_HEADROOM_WAIT`.** It is still 420
  (`shakenfist/deploy/shakenfist_ci/base.py:61`). The longest wait ever
  recorded is 69% of it. Phase 5 left this to phase 6 and phase 6
  declines it, in writing, rather than silently.
* **Renaming `Debian 12 tier` wherever it appears.** D47; most of those
  occurrences are historical readings and renaming them would falsify
  the record.
* **`ARCHITECTURE.md` and `AGENTS.md`.** No convention and no component
  boundary changed. The master plan's success criteria already say a
  transient-refusal contract is an operator-guide and API-reference
  matter.

## What the survey found

Phase 6's section in the master plan (`:918-944`) was written on
2026-09-08, before phases 1 to 5 executed. Every factual claim in it
was checked. Four of its instructions are already discharged, one names
a job that no longer exists, and one documentation statement elsewhere
has become false. The corrections to the master plan's own phase 6
section and to the `docs/plans/index.md` row are made in this
planning commit; later steps must not redo them.

### F1 -- The transient contract is documented, on a page this phase does not name

Phase 6 is told to document the contract "in
`docs/operator_guide/scheduler.md` and the API reference". Phase 4
instead created `docs/operator_guide/capacity_refusals.md` (202 lines,
commit `0c24a7149`, "Document the transient refusal contract.",
2026-09-17), covering which refusals are transient, the response body,
both fields, the `Retry-After` header, why 15 seconds, the client-side
retry, how to enable it and why it is off by default.
`scheduler.md:375-377` links to it rather than restating it, which is
the right shape. So this instruction is discharged, somewhere other
than where it says.

What is *not* discharged is the API reference half:
`docs/developer_guide/api_reference/instances.md` has no mention of
`507`, `Retry-After` or `transient` -- its only capacity hit is a `400`
for a negative disk size at `:90`. That is a one-link gap, not a page.

### F2 -- The suite wrapper and its allowlist marker are documented

`docs/developer_guide/ci.md` carries `## Creating instances in the
functional suite` at `:133`, `CLUSTER_HEADROOM_WAIT` and its 420
seconds at `:146`, `retries.wait_for_capacity()` at `:155`, and the
allowlist marker's "a reason fails the same as no marker at all, so the
allowlist cannot grow by accident" at `:203`. Discharged by phase 2.

### F3 -- The wait summary is documented, on a page created after this phase was written

Phase 6 is told to put the wait summary "beside the headroom probe's
documentation". When that was written the probe was documented in
`ci.md`. The sizing plan's own phase 6 (its D2) moved that material to
a new page, and the wait summary went with it:
`docs/developer_guide/ci_cloud_sizing.md:341`, "### A third file: the
capacity-wait trace (`--waits`)", runs to `:375` and covers the
wrapper, the JSONL path, how it reaches the bundle, what
`--waits` summarises, the informed/degraded split, the request
dimensions, `binding_dimension`, `attempt_number` as a 1-indexed
position, and the `node`/`roster_key` resolution. Discharged, on a page
whose name phase 6 could not have known.

### F4 -- That page now states the one thing phase 5 overturned

`ci_cloud_sizing.md:370-375`:

> An absent or empty file reports as unknown, never as zero waits, for
> the same reason an absent or empty census reports as unknown rather
> than as zero refusals [...]: "no one collected this" and "the wrapper
> never had to wait" are different

That was true when written and is now the opposite of what the project
believes. #4337 landed as `62bb1ddeb`: `BaseTestCase.setUp()`
(`shakenfist/deploy/shakenfist_ci/base.py:214`) calls
`ensure_capacity_wait_trace()` (`:180`), which touches the file into
existence, so for any run whose base carries that commit an empty file
means exactly "the wrapper never had to wait". Phase 5's D43 admits it
to the denominator as a real zero on precisely that condition, and the
second window's census -- 92 `empty` against 2 `absent`, 2.1% unknown
where the first window read 92.3% -- is what that unlocked.

The *tool* is not wrong. `read_waits()`
(`tools/ci_headroom_report.py:1069-1096`) already returns `empty` as
its own state, distinct from `unreadable`, and `waits_record()`
(`:1712-1730`) nulls its counts on purpose, because the tool runs over
a downloaded bundle and cannot know whether that run's base carried
`62bb1ddeb`. So the tool reports the state and leaves the reading to
the reader; the documentation states only the conservative half and
presents it as the whole answer. That is the correction.

### F5 -- The fix that made the second reading possible is documented nowhere

`grep -rn 'ensure_capacity_wait_trace' docs/` returns hits only in plan
files. A reader of `ci.md` or `ci_cloud_sizing.md` has no way to learn
that the trace is created at suite start-up, which is the single fact
that makes an empty trace readable.

### F6 -- The job this phase is told to judge #3772 on does not exist

Phase 6's section said to close #3772 "only if the `Debian 12 tier`
job's failures are no longer `sufficient_idle_cpu`" -- corrected in
this planning commit, where the section now reads `Debian 13 tier` and
records why -- and the plan's success criteria repeat the stale name at
`:1216`, which step 6c corrects. Commit `e9e8c86658d`
("Rename the Debian 12 matrix lanes.") renamed them; the lane is now
`Debian 13 tier` / `debian-13-slim-tier`
(`.github/workflows/functional-tests.yml`, the
`functional_matrix_merge_collection` matrix), and
`grep -i 'debian.12' .github/workflows/functional-tests.yml` returns
nothing. As written the instruction is unexecutable.

That same commit is the cause of phase 5's F8 (#4403), which is not a
coincidence: a point-in-time name used as a key for a retrospective
read breaks the same way whether the reader is a script or a plan.

### F7 -- Two sibling cross-references read as open questions

* `PLAN-scheduler-reservations.md:1241-1243`: "server-side queueing
  only if the measured waits say so -- which would be a reversal of D8
  and is written down as such there." The waits have been measured and
  they said no, so D8 is **confirmed by measurement**, not reversed.
  That is a materially better outcome for that plan than the sentence
  anticipates, and it is the sentence a reader of D8 would reach for.
* `PLAN-ci-cloud-sizing-phase-05-guardrails.md:754`: its D5 open check
  is discharged by naming this plan, "whose phase 5 **is** the decision
  on server-side queued placement and is fed by exactly this series".
  Present tense; phase 5 has decided, and that plan's guard warning was
  kept alive specifically because this reader existed.

`PLAN-scheduler-reservations.md:1244` also describes "the Debian 12
tier" in the present tense as a live topology claim. It is the only
sibling occurrence that is a claim rather than a record.

### F8 -- The master plan's bug list has drifted

`:1393` reads `**#4087** (open; #4106 attempted it and did not close
it)`. #4087 is CLOSED -- phase 1 closed it, as the same bullet says it
would. Separately, three issues this plan produced or depends on are
absent from *Bugs fixed during this work* entirely: #4337 (closed, the
trace-cannot-express-zero fix the second reading rests on), #4197
(open, phase 3's load-budget re-derivation) and #4403 (open, phase 5's
F8).

### F9 -- Phase 5's F9 was never filed

Phase 5 recorded two instrument findings needing issues. F8 became
#4403. F9 -- `bundle-shakenfist-full-ansible-modules` cannot carry a
wait trace, because that suite is six Ansible playbooks with no Python
harness and therefore never calls the wrapper -- has no issue. It is
the finding behind D44, so the plan's own decision depends on it.

### What the survey did not find

* **The fairness assertion never reached `docs/`.** "A pinned create
  starves worst" appears in no non-plan page.
  `grep -rn -i 'starv' docs/operator_guide/` returns one hit,
  `scheduler.md:466`, about an oversized reservation claim starving
  unclaimed namespaces -- a different mechanism in a different plan. So
  there is nothing to retract; there is only something this phase must
  not introduce.
* **`CLUSTER_HEADROOM_WAIT` is untouched** at 420 (`base.py:61`), as
  phase 5 said it should be.
* **The plan status machinery agrees with itself.**
  `python3 tools/check-plan-status.py` prints "Plan statuses, index
  arithmetic and phase links agree."

## Decisions

Numbering continues the plan's sequence; phase 5 ended at D44.

### D45 -- This phase's documentation scope is one correction and two additions, not a sweep

F1, F2 and F3 are discharged. Re-documenting them would duplicate
pages that already say it better, and duplication is how two pages come
to disagree. What is left is: fix F4, add F5, add the queue answer
(D49), and add the one missing API-reference link from F1.

Stated plainly because the phase's own section reads like four pages of
work: it is roughly forty lines of prose in three files, plus the
close-out. A reviewer expecting a large diff should expect a small one.

### D46 -- `empty` means zero in the prose; the tool stays conservative

`ci_headroom_report.py` is not changed. It cannot know whether the run
it is reading carried `62bb1ddeb`, so nulling an empty trace's counts
is the only honest thing it can do from inside a downloaded bundle. The
documentation instead gains the condition: an empty trace is a real
zero when the run's base contains `62bb1ddeb`, and `absent` and
`unparseable` remain unknown.

The alternative -- teaching the tool to resolve the run's base -- is a
real option and is rejected here rather than ignored. It would need the
harvest to pass ancestry down into the report, which is the same seam
#4403 is about, and doing it as a side effect of a documentation phase
would put a measurement change inside a commit nobody would review as
one.

### D47 -- Only forward-looking `Debian 12 tier` references are corrected

`Debian 12 tier` appears in about thirty lines across eleven plan
files. Almost all of them are **records of readings taken when the job
was called that**: the sizing plan's baseline tables, its band
verdicts, phase 4's timeout change, the agent-deadlines plan's flake
list. Renaming those would falsify the record, which is worse than the
staleness.

So the rename is applied only where the string is an **instruction or a
live claim**:

* this plan's own phase 6 section (corrected in the planning commit)
  and its success criteria at `:1216`, and
* `PLAN-scheduler-reservations.md:1244`, which describes the topology
  in the present tense (step 6d, which is already editing that file).

`PLAN-transient-capacity-refusals.md:83` is left alone: it is the
Situation's account of what was read in 2026-09-08 journals, and the
job was called `Debian 12 tier` then.

**This is the decision most likely to be argued with**, because the
obvious move is a global replace and it would make every page read
consistently. The argument against is that these plans are used as
evidence -- phase 5 answered an objection with `git log` rather than
with reasoning -- and a record that silently acquires today's names
cannot be used that way. A reader who hits `Debian 12 tier` in a 2026-08
reading should be able to find `e9e8c86658d` and understand why.

### D48 -- #3772 gets the numbers and stays open

The master plan says to close #3772 only if the tier job's failures are
no longer `sufficient_idle_cpu`. Two things stop this phase from
closing it.

First, the evidence for "the failures stopped" is currently *silence*:
the last occurrence comment is 2026-09-19, which predates both the
sizing plan's reshape and #4337. Absence of triage comments is not
absence of failures, and the autofixer and the triage workflow both
write to that issue, so a gap is as consistent with nobody looking as
with nothing happening.

Second, phase 5's second window is not silence -- it found three real
waits, at 41%, 55% and 69% of the 420 s deadline, and **two of the
three got longer after the reshape**. Those are waits rather than
failures, which is the contract working, but an umbrella issue about
`507 sufficient_idle_cpu` under suite concurrency should not be closed
in the same week the measurement says the underlying pressure grew.

So the step measures the pass rate, posts it, states both confounders
(the lane rename and the reshape landed inside the comparison window),
and leaves the issue open with the numbers -- which is the branch the
master plan's own sentence already provides for.

### D49 -- The queue answer goes in `capacity_refusals.md`, with phase 5's qualifications

The operator-facing answer belongs beside the contract a client sees,
not in `scheduler.md`, which links there already (F1). Phase 5 is
explicit about the wording and it is binding here: the page must
describe an **Abandon selected by the rule's default**, with two of the
window's three waits past the 210 s line, and must not say "a placement
queue is unnecessary". The three evidence limits -- CI-only,
post-reshape, client retry off -- are carried across as phase 5 wrote
them.

The fairness assertion is recorded as **untestable on this evidence**,
not as disproved and not as confirmed: every refused create the
functional suite issues is pinned, so the instrument cannot produce the
unpinned comparison arm.

### D50 -- F9 is filed, not fixed, and labelled to keep the autofixer off it

Same position #4403 is in. F9's fix is a design question -- whether a
suite with no Python harness should be excluded by name, by a
capability probe, or by the absence of a trace being legal for it --
and a same-day automated patch would pick one silently. Filed with
`automated-fix-attempted`.

### D51 -- No new measurement window

Phase 6 reads phase 5's two windows and the CI run history. It does not
harvest. The 1.4 GB of cached bundles from phase 5 are not a phase 6
input, and if a third reading is ever wanted, D37's unfixed scoping must
be pre-registered first -- which is a decision phase, not a
documentation one.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | **Fix F4 and add F5, in `docs/developer_guide/ci_cloud_sizing.md`.** Do not touch `tools/ci_headroom_report.py` (D46). Replace the paragraph at `:370-375` -- "An absent or empty file reports as unknown, never as zero waits..." -- with the post-#4337 reading: a trace file that exists and is empty is a **real zero** for any run whose base contains `62bb1ddeb`, because `BaseTestCase.setUp()` (`shakenfist/deploy/shakenfist_ci/base.py:214`) calls `ensure_capacity_wait_trace()` (`:180`), which touches it into existence and swallows every failure exactly as the append path does; `absent` and `unparseable` remain unknown, and absent still cannot be read as zero for the reasons the old paragraph gives (a component ref predating the wrapper, a run whose writes all failed). Then state the part that is easy to get wrong: `ci_headroom_report.py` still nulls an empty trace's counts and that is **correct**, because the report runs over a downloaded bundle and cannot see the run's base -- the four states it emits (`read`, `empty`, `unreadable`, `unparseable`) are the raw reading and the ancestry condition is the caller's to apply. Cite [#4337](https://github.com/shakenfist/shakenfist/issues/4337) and link phase 5's D43 by name and section, **not** by anchor -- the doc-link pre-commit hook resolves anchors and a guessed mkdocs slug will fail it. Keep it to four or five sentences; do not restate the census numbers here, they live in phase 5 (DoD item 14). Run the `Check documentation links and anchors resolve` hook specifically. |
| 6b | medium | opus | none | **Write the queue answer into `docs/operator_guide/capacity_refusals.md` (D49).** A new `## Is the refusal queued server-side?` section, placed after `## Which refusals are transient` and before `## The response body`, because it answers the question a reader of the first section asks next. Required content, and the wording constraints are phase 5's rather than yours: (1) **No** -- the server refuses a create that does not fit and does not hold it; (2) that this was decided from measurement, over two windows of merge-CI data, against a decision rule fixed in writing before the data was read; (3) that it is an **Abandon selected by the rule's middle-ground default, not by its Abandon clauses** -- two of the second window's three waits exceeded the 210 s line while none approached the frequency a Build needed; (4) the three evidence limits, as three short labelled sentences, from phase 5's *What the evidence could not cover (D40)*: the evidence is CI-only, it is post-reshape, and it was gathered with the client retry off; (5) the retry that *does* exist, as a link to the `## Client-side retry` section below it. **Forbidden**: any sentence saying or implying a placement queue is unnecessary, is not needed, or was ruled out on principle; and any form of "a pinned create starves worst", which phase 5 found untestable on CI data because every refused create the suite issues is pinned. State that untestability in one sentence rather than omitting it. Link to the phase 5 plan file by filename and section name, not by anchor. Also add the one missing link from F1: in `docs/developer_guide/api_reference/instances.md`, beside the existing capacity text near `:90`, a sentence pointing at `/operator_guide/capacity_refusals/` for the `507` format, `stage`, `transient` and `Retry-After` -- a link, not a restatement. Do not start until the back brief clears the wording. |
| 6c | medium | sonnet | none | **Correct this plan's remaining stale references at source (F6, F8), under D47's scope rule.** Phase 6's own section and the `docs/plans/index.md` row were corrected by the planning commit -- **do not touch either**. What is left in `docs/plans/PLAN-transient-capacity-refusals.md` is the success criteria at `:1216`: replace `` `Debian 12 tier` `` with `` `Debian 13 tier` ``. **Leave `:83` alone** -- it is a record of a 2026-09-08 reading taken when the job had that name -- and leave every occurrence in every other plan file alone, except `PLAN-scheduler-reservations.md:1244`, which step 6d handles. In *Bugs fixed during this work*: change `:1393`'s `**#4087** (open; ...)` to record it as closed by phase 1, keeping the rest of the bullet; and add three bullets -- **#4337** (closed) the capacity-wait trace could not express zero, which made 92.3% of phase 5's first window unreadable and whose fix is what the second window rests on; **#4403** (open) `ci_headroom_harvest`'s point-in-time bundle tables reinterpreting historical windows, phase 5's F8, left for a human and labelled `automated-fix-attempted`; and **#4197** (open) the load-budget re-derivation phase 3 left behind. Do not renumber or reorder the existing bullets. |
| 6d | medium | sonnet | none | **Settle the two sibling cross-references (F7).** In `docs/plans/PLAN-scheduler-reservations.md:1236-1243`, rewrite the tail of that bullet: the retry question is no longer "server-side queueing only if the measured waits say so -- which would be a reversal of D8". Phase 5 measured it over two windows and the answer is no, so **D8 is confirmed by measurement rather than reversed** -- say that, name the result (Abandon by the rule's default, three waits in the second window, longest 290.83 s against a 420 s deadline), and link `PLAN-transient-capacity-refusals-phase-05-queue-decision.md` by filename and section name. Note in one clause that D8 rejected hold-until-fittable on state-surface grounds and the measurement agrees for a different reason -- there was not enough waiting to pay for it -- because two independent reasons reaching the same place is worth recording. In `docs/plans/PLAN-ci-cloud-sizing-phase-05-guardrails.md:752-758`, put D5's open check into the past tense: the reader it named has read the series and decided, so the refusal warning's purpose is discharged rather than pending; keep the sentence that the warning stays as an observation about the demand estimator's calibration, which is still true. Then, back in `PLAN-scheduler-reservations.md` -- not the guardrails file -- apply D47's one sibling rename at `:1244`. Both files have merged, and editing a merged phase plan is deliberate here: these are sentences addressed to this phase, and leaving them would have the next reader re-derive F7. |
| 6e | medium | sonnet | none | **File F9 (D50), and comment on #3772 (D48).** File against `shakenfist/shakenfist`, `--label automated-fix-attempted`: `bundle-shakenfist-full-ansible-modules` can never carry a capacity-wait trace, because the Ansible modules suite is six playbooks (`shakenfist/deploy/ansible_module_ci/001.yml` through `006.yml`) with no Python harness and so never calls the `self.create_instance()` wrapper; the body must say that this is **not** the same as #4377's gate problem and not a plumbing failure, that it is the finding phase 5's D44 rests on (a bundle that cannot carry a wait trace is not a unit, which excluded 25 bundles and is load-bearing -- rejecting it makes the per-bundle reading 28.4% unknown and unreadable), that `classify_artifact()` returning `skip` for `UNINSTRUMENTED_BUNDLES` is the existing mechanism and the lane was moved *out* of that set by `3723216d7`, and that the design question is which of three answers is right (exclude by name, probe for the capability, or make an absent trace legal for a non-Python suite). Cross-reference #4403, which is the same class of problem in the same file. Record the number in this plan's Outcome and in the master plan's *Bugs fixed during this work*. Then comment on [#3772](https://github.com/shakenfist/shakenfist/issues/3772) -- **do not close it**. The comment carries: the second window's numbers (39 ancestry-qualifying merge runs, 95 instrumented units, 3 waits, longest 290.83 s against a 420 s deadline, census 92 `empty` / 2 `absent` / 2.1% unknown against the first window's 92.3%); the `Debian 13 tier` pass rate before and after, measured with `ci-status shakenfist/shakenfist runs` over merge_group events and the failure reasons for the failures, **not just the rate**; and the two confounders stated plainly -- the lane rename (`e9e8c86658d`) and the sizing plan's reshape both land inside the comparison window, so a change in rate cannot be attributed to this plan alone. Say that two of the three waits got *longer* after the reshape. Close nothing, and say in the comment why it stays open: the master plan's close condition is about the *failure reasons*, and the last recorded occurrence (2026-09-19) predates both the reshape and #4337, so the quiet period is as consistent with nobody triaging as with nothing failing. |
| 6f | medium | sonnet | none | **Close-out.** Write this plan's `## Outcome` -- what each step actually did, F9's issue number, the #3772 numbers as posted, and every Definition of done item **run** rather than read, item by item in a table, which is how three of phase 4's and two of phase 5's wrong items were found. Set phase 6 to `Complete` in the master plan's Execution table and update the `docs/plans/index.md` row: the count becomes `6 of 7`, which `tools/check-plan-status.py` recomputes from the Execution rows rather than taking on trust, and the row's description gains what the survey found (three of four documentation targets already discharged, one documentation statement overturned by #4337) and what shipped. The `Merged` cell stays `—`; phase 7's planning fills it. Then write `### What phase 7 inherits`, naming: that phase 7's baseline is the `Merged` column rather than `develop...HEAD`, per the master plan's phase 7 section; that phase 5 produced no code, so the audit records what it had no diff to scope over rather than reporting a clean run over an empty range, which that section already requires; that phase 2 landed partly in `shakenfist/actions` and is audited there, cited not re-run; and the four open items -- #4403, F9's number, #4197, and D37's unfixed scoping plus B1's unsoundness (it scores on the longest single wait, so a create refused repeatedly can exhaust the 420 s deadline with no single line reaching the threshold), both of which must be pre-registered before any third reading. Run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`. |

6a, 6c, 6d and 6e are independent of each other and of 6b. 6b is gated
on the back brief, for its wording. 6f is last and depends on all of
them. Nothing here needs a worktree: every step edits documentation in
this worktree, and the `shakenfist/actions` repository is not touched.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| **6b writes "a queue is unnecessary"**, which is the one sentence phase 5 forbids, because it is the natural way to summarise an Abandon. | The back brief gates 6b on its wording, and DoD item 6 greps for the forbidden forms. The reviewer to check it is whoever reads the back brief, before any editing starts. |
| **The #3772 before-and-after comparison spans two confounders** -- the lane rename and the topology reshape -- so any change in pass rate is unattributable. | 6e's brief requires both confounders stated in the comment, and requires the *failure reasons* rather than the rate alone. D48 keeps the issue open regardless of what the rate shows, so a flattering number cannot close it. |
| **A global `Debian 12 tier` rename falsifies thirty lines of recorded readings.** | D47 scopes it to three lines. DoD items 1 and 2 count occurrences before and after in the files that must not change, so an over-broad `sed` fails a check rather than merging. |
| **Editing two merged sibling plans (6d) reads as scope creep.** | Both sentences are addressed to this phase by name, which is why they are in scope; 6d's brief says so, and the commit message should repeat it. If a reviewer disagrees, the right outcome is dropping 6d, not rewording it. |
| **Two pages come to state the second window's numbers differently.** | DoD item 14: the three-waits figures appear in `capacity_refusals.md` and in phase 5's Outcome and nowhere else, and are compared literally. |
| **A guessed mkdocs anchor fails the doc-link hook late**, after the prose is written. This happened during phase 5's amendment. | 6a and 6b are both told to link by filename and section name rather than by anchor, and to run the link hook specifically rather than waiting for the full pre-commit run. |

## Definition of done

Each item is falsifiable and must be **run**. Phase 4 had three wrong
items and phase 5 had two, each found by executing them.

1. `grep -n 'Debian 12 tier' docs/plans/PLAN-transient-capacity-refusals.md`
   returns exactly one hit, `:83`, and `git diff` shows that line
   unchanged.
2. For each of `PLAN-ci-cloud-sizing*.md`,
   `PLAN-agent-operation-deadlines-phase-04-enforcement.md`,
   `PLAN-scheduler-reservations-phase-0{4b,4c,6}*.md` and
   `PLAN-transient-capacity-refusals-phase-04-retry-after.md`, the count
   of `Debian 12 tier` is identical before and after this phase.
3. `grep -n 'absent or empty file reports as unknown' docs/developer_guide/ci_cloud_sizing.md`
   returns nothing, and `grep -c '4337' docs/developer_guide/ci_cloud_sizing.md`
   returns at least 1.
4. `grep -rn 'ensure_capacity_wait_trace' docs/ --include='*.md' | grep -v '^docs/plans/'`
   returns at least one hit.
5. `docs/operator_guide/capacity_refusals.md` contains a section
   answering the queue question, and all three of `CI-only`,
   `post-reshape` and the client-retry limit appear in it.
6. In `docs/operator_guide/capacity_refusals.md`,
   `grep -ic 'unnecessary\|not needed\|starv'` returns 0.
7. `docs/developer_guide/api_reference/instances.md` links to
   `/operator_guide/capacity_refusals/`, and the doc-link hook passes.
8. #3772 has a comment from this phase carrying the second window's
   three-wait numbers and both confounders, and
   `gh issue view 3772 --json state` is still `OPEN`.
9. F9's issue exists, carries the `automated-fix-attempted` label, and
   its number appears in this plan's Outcome and in the master plan's
   *Bugs fixed during this work*.
10. In the master plan's bug list, `#4087` no longer reads `(open`, and
    each of `#4337`, `#4197` and `#4403` appears exactly once.
11. `PLAN-scheduler-reservations.md` states D8 is confirmed by
    measurement, and `grep -c 'would be a reversal of D8'` returns 0.
12. `PLAN-ci-cloud-sizing-phase-05-guardrails.md`'s D5 paragraph reads
    in the past tense and names the result.
13. `python3 tools/check-plan-status.py` passes, and
    `docs/plans/index.md` reads `6 of 7`.
14. The second window's wait figures (3 waits, longest 290.83 s, 95
    units) appear in `capacity_refusals.md` and in phase 5's Outcome,
    agree literally, and appear nowhere else.
15. `pre-commit run --all-files` passes all twelve hooks.

## Back brief

Before executing any step, back brief: restate what this phase is for,
which three documentation targets the survey found already discharged
and why that is not an argument for skipping the phase, and what the
one false statement is.

**Gate on 6b.** Do not edit `capacity_refusals.md` until the queue
section's wording has been agreed. It is cheap to propose and expensive
to redo, because the constraint on it is a judgement phase 5 recorded
rather than a fact that can be checked: an Abandon selected by a
default reads, to anyone summarising it, exactly like a conclusion that
a queue is a bad idea. Propose the section's sentences first.

If any step's brief looks wrong, say so and then follow it, or ask. Do
not quietly substitute a different approach -- particularly on D47,
where the tempting move is the one the decision rejects.

## Outcome

Complete. 6a, 6c, 6d and 6e ran independently against this one
worktree; 6b was gated on its back brief; 6f is this close-out. Every
step's edit was checked against the tree rather than taken on trust
before this section was written.

### What each step did

**6a** replaced the stale paragraph in
`docs/developer_guide/ci_cloud_sizing.md`. It ran `:370-377`, not
`:370-375` as F4's quote had it -- the quote elided an internal link
line -- and no other cited line number had drifted:
`ensure_capacity_wait_trace()` is still `base.py:180`, called from
`setUp()` at `:214`, and `read_waits()`/`waits_record()` are still
`ci_headroom_report.py:1069`/`:1712`. The replacement states the
post-#4337 reading -- an empty trace is a real zero conditioned on the
run's base carrying `62bb1ddeb` -- cites
[#4337](https://github.com/shakenfist/shakenfist/issues/4337), links
phase 5's D43 by name and section rather than by anchor, and documents
`ensure_capacity_wait_trace()` for the first time anywhere in `docs/`.

**6b** added `## Is the refusal queued server-side?` to
`docs/operator_guide/capacity_refusals.md` at `:55`, and the one
cross-link `docs/developer_guide/api_reference/instances.md` was
missing, beside the capacity text near `:90`. Two things from
executing it rather than reading the brief: the section's wording was
reviewed and approved before it was written, then adapted because
`tools/check-plan-phase-references.py` forbids `\bphase\s+\d+` outside
`docs/plans/`, so the page names phase 5 by linking the plan file and
its section rather than saying "phase 5"; and one approved sentence
was corrected for accuracy after review. It had said no build clause
"came close to the frequency required", which is only true on the
narrowest unit counting -- phase 5's own fragility table has B2 at
8.3% against a 10% threshold under per-run scoping, one long-wait run
from Build. The page now says no build clause fired, and that how
close that was depends on how units are counted: wide on the
narrowest counting, one long-wait run short on the broadest.

**6c** changed `Debian 12 tier` to `Debian 13 tier` at the success
criteria line only (`:1216`), left `:83`'s historical reading alone,
changed `#4087`'s bug-list entry to `(closed by phase 1; ...)`, and
appended bullets for
[#4337](https://github.com/shakenfist/shakenfist/issues/4337),
[#4403](https://github.com/shakenfist/shakenfist/issues/4403),
[#4197](https://github.com/shakenfist/shakenfist/issues/4197) and
[#4438](https://github.com/shakenfist/shakenfist/issues/4438) to
*Bugs fixed during this work*, without reordering the existing six.
The fourth bullet, for #4438, was reassigned to 6c from 6e -- see
*Deviations*, below.

**6d** rewrote `PLAN-scheduler-reservations.md`'s retry bullet so D8
reads as confirmed by measurement rather than reversed, naming the
result (Abandon by the rule's default, three waits in the second
window, longest 290.83 s against the 420 s deadline) and noting that
D8's state-surface objection and the measurement reach the same place
for different reasons. It renamed that file's one present-tense
`Debian 12 tier` claim to `Debian 13 tier`, and put
`PLAN-ci-cloud-sizing-phase-05-guardrails.md`'s D5 open check into the
past tense: the reader D5 named has since read the series and
decided, so the refusal warning's purpose is discharged rather than
pending, while the warning itself stays. It confirmed the guardrails
file's own twelve `Debian 12 tier` occurrences are all historical
readings and left them alone.

**6e** filed F9 as
[#4438](https://github.com/shakenfist/shakenfist/issues/4438)
("ci_headroom_harvest: bundle-shakenfist-full-ansible-modules cannot
carry a capacity-wait trace", `shakenfist/shakenfist`, OPEN, labelled
`automated-fix-attempted`), after searching several phrasings and
reading #4377 and #4403 to confirm it is not a duplicate. It then
commented on
[#3772](https://github.com/shakenfist/shakenfist/issues/3772#issuecomment-5977267873)
and did not close it. Its pass-rate measurement used three windows
rather than two, splitting at the reshape as well as the rename,
which is the step's material finding beyond its brief: `Debian 12
tier` ran 56.8% (25/44) pre-reshape, with 13 of 18 readable failures
carrying `sufficient_idle_cpu`; 74.0% (37/50) post-reshape/pre-rename,
with 0 of 7 sampled failures carrying it; and `Debian 13 tier` ran
88.9% (16/18) post-rename, with 0 of 2. It sampled 9 of 15
post-reshape failures rather than all 15 and said so in the comment
rather than claiming zero across the period. It also caught and
corrected its own mis-dating of the reshape mid-measurement, which
had shifted the middle figure from 75.0% to 74.0%.

### F9 and #3772, as posted

F9 is [#4438](https://github.com/shakenfist/shakenfist/issues/4438),
open, labelled `automated-fix-attempted`.

The second window's numbers, as posted to
[#3772](https://github.com/shakenfist/shakenfist/issues/3772#issuecomment-5977267873):
39 ancestry-qualifying `merge_group` runs (2026-09-27 -- 2026-10-02),
95 instrumented bundle-units, census 92 `empty` / 3 `read` / 2
`absent` -- 2.1% unknown against the first window's 92.3%. Three
capacity waits, at 41%, 55% and 69% of the 420 s deadline, longest
290.83 s, two of the three longer after the reshape than in the first
window. The `Debian 12/13 tier` pass rate across three real windows,
by job-level conclusion over `merge_group` events: 56.8% (25/44)
before the reshape, 74.0% (37/50) after the reshape but before the
rename, 88.9% (16/18) after the rename -- with the `sufficient_idle_cpu`
signature present in 13 of 18 readable "before" failures and absent
from all 9 sampled "after" failures. The comment states the two
confounders (the lane rename `e9e8c86658d` and the sizing plan's
reshape both land inside the comparison window) and leaves the issue
open: the master plan's close condition is about failure reasons, and
the last occurrence comment (2026-09-19) predates both the reshape
and #4337, so the quiet period is as consistent with nobody triaging
as with nothing failing.

### Deviations from this plan

1. **Pre-commit was not run by each step.** The step briefs told each
   step to run its own hooks. Because all six steps shared this one
   worktree and pre-commit's stash is repo-wide, parallel hook runs
   would have silently reverted each other's uncommitted work, so
   hooks were instead run centrally between waves by the orchestrating
   session. Steps ran the standalone checker scripts
   (`tools/check-doc-anchors.py`,
   `tools/check-plan-phase-references.py`) directly where their brief
   needed one. `pre-commit run --all-files` has since been run once,
   centrally, over the whole set of changes: all twelve hooks pass.

2. **The step table collided with itself.** Both 6c and 6e were told
   to edit the master plan's *Bugs fixed during this work*. Recording
   #4438's number was reassigned from 6e to 6c for that reason, and 6e
   made no file edits at all -- its work is the issue filing and the
   #3772 comment, both external to this repository's tree.

### Definition of done, item by item

Every item below was **run**, not read. Item 15 was run by the
operator rather than by this step, directly, and is cited rather than
re-run: `pre-commit run --all-files` is unsafe to run twice from two
sessions against one shared worktree, for the same reason deviation 1
exists.

| Item | Result | Note |
|---|---|---|
| 1 | **Met** | Ran `grep -n 'Debian 12 tier' docs/plans/PLAN-transient-capacity-refusals.md`: one hit, `:83`, unchanged by `git diff`. |
| 2 | **Met** | Checked `git diff --stat` against each named file: only `PLAN-ci-cloud-sizing-phase-05-guardrails.md` has a diff, and its own `Debian 12 tier` count is 12 before and after. Every other named file is untouched. |
| 3 | **Met** | `grep -n 'absent or empty file reports as unknown' docs/developer_guide/ci_cloud_sizing.md` returns nothing; `grep -c '4337' docs/developer_guide/ci_cloud_sizing.md` returns `1`. |
| 4 | **Met** | `grep -rn 'ensure_capacity_wait_trace' docs/ --include='*.md' \| grep -v '^docs/plans/'` returns one hit, `ci_cloud_sizing.md:374`. |
| 5 | **Met** | `capacity_refusals.md` carries `## Is the refusal queued server-side?`; `CI-only` and `post-reshape` both appear as bullet headings in it, and the client-retry limit appears as `**Client retry off.**`. |
| 6 | **Met** | `grep -ic 'unnecessary\|not needed\|starv' docs/operator_guide/capacity_refusals.md` returns `0`. |
| 7 | **Met** | `instances.md` links `/operator_guide/capacity_refusals/`; `python3 tools/check-doc-anchors.py` exits 0. |
| 8 | **Met** | `gh issue view 3772 --json state` returns `OPEN`; [comment 5977267873](https://github.com/shakenfist/shakenfist/issues/3772#issuecomment-5977267873), posted 2026-10-04, carries the three-wait numbers and both confounders (see above). |
| 9 | **Met** | `gh issue view 4438 --json labels,state` returns `OPEN` with `automated-fix-attempted`; the number appears in this Outcome and in the master plan's bug list. |
| 10 | **Met, with a stated discrepancy** | `#4087` reads `(closed by phase 1; ...)`, not `(open`. Within *Bugs fixed during this work*, `#4337` and `#4197` each appear exactly once; `#4403` appears **twice** -- its own bullet, and a cross-reference inside #4438's bullet, which 6e's brief explicitly asked for. The item as written is wrong: it should have said "has its own bullet" rather than "appears exactly once". This is the third consecutive phase to find a wrong definition-of-done item by executing it, after phase 4's three and phase 5's two. |
| 11 | **Met** | `PLAN-scheduler-reservations.md` now reads "D8 is confirmed by measurement, not reversed"; `grep -c 'would be a reversal of D8' docs/plans/PLAN-scheduler-reservations.md` returns `0`. |
| 12 | **Met** | `PLAN-ci-cloud-sizing-phase-05-guardrails.md`'s D5 paragraph reads in the past tense ("was named", "was the decision", "has since read the series and decided") and names the result (an Abandon, by the rule's middle-ground default). |
| 13 | **Met** | `python3 tools/check-plan-status.py` prints "Plan statuses, index arithmetic and phase links agree."; `docs/plans/index.md` reads `6 of 7`. |
| 14 | **Not met as written** | The figures (3 waits, longest 290.83 s, 95 units) do agree literally between `capacity_refusals.md` and phase 5's Outcome, but `290.83` also appears in two places the item says it should not: `PLAN-scheduler-reservations.md:1243`, because 6d's own brief explicitly required naming "longest 290.83 s against a 420 s deadline" there, and this plan file's own step table and this item's own text, which must state the figure to check for it. The item's "and appear nowhere else" is incompatible with 6d's brief as written for the sibling-plan figure; the self-reference in this file is arguably exempt but was not excluded by the item's wording either. Reported as a second wrong item rather than marked Met on a generous reading. |
| 15 | **Met, cited rather than re-run** | The operator ran `pre-commit run --all-files` once, centrally, after all five preceding steps' edits; all twelve hooks pass. Running it a second time from this step, against the same shared, unstaged worktree, risks exactly the stash collision deviation 1 exists to avoid. |

Two of fifteen items are wrong as written, both found by running
rather than reading them: item 10's "appears exactly once" should read
"has its own bullet", and item 14's "appear nowhere else" collides
with 6d's own brief. Three consecutive phases -- 4, 5 and 6 -- have
now found a wrong definition-of-done item by executing it.

### What phase 7 inherits

* **The baseline is the `Merged` column, not `develop...HEAD`.** By
  the time phase 7 runs, every phase here will have merged, and a diff
  against `develop` reads as empty -- which would look like a clean
  audit rather than like the absence of one. The master plan's phase 7
  section already requires reading the `Merged` column in the
  Execution table above, phase by phase, rather than the branch tip.
* **Phase 5 produced no code.** It is a decision phase that closed
  Abandoned on the queue -- Complete as a phase, nothing built. The
  master plan's phase 7 section already requires that this be recorded
  as an audit with no diff to scope over, not as a clean run across an
  empty range; phase 7 should say so in the same one sentence that
  section asks for, rather than silently skipping phase 5's row.
* **Phase 2 is partly audited elsewhere.** Its suite wrapper landed
  partly in `shakenfist/actions`; that half is audited against that
  repository's default branch as part of the pull request that landed
  it there. Phase 7 cites that audit by reference and does not re-run
  it.
* **Four open items, two of which must be pre-registered before any
  third reading of the wait data:**
    * [#4403](https://github.com/shakenfist/shakenfist/issues/4403) --
      `ci_headroom_harvest`'s point-in-time bundle tables
      reinterpreting historical windows.
    * [#4438](https://github.com/shakenfist/shakenfist/issues/4438) --
      the Ansible modules bundle cannot carry a capacity-wait trace.
    * [#4197](https://github.com/shakenfist/shakenfist/issues/4197) --
      the load-budget re-derivation phase 3 left behind.
    * **D37's unfixed scoping, and B1's unsoundness.** B1 scores on
      the longest single wait recorded, so a create that is refused
      repeatedly, each wait falling short of the 420 s deadline, can
      exhaust the deadline in aggregate with no single line ever
      reaching the threshold the rule reads. Neither of these is fixed
      by anything in this plan, and D51 already declines to harvest a
      third window from inside phase 6. If a third reading is ever
      wanted, both must be pre-registered -- as a decision, in writing,
      before the data is read -- not discovered by re-running the same
      rule over new numbers and hoping the scoping gap does not matter
      this time.

### What the review round changed

The pull request carried a defect of its own making, which the
automated review found and which is worth recording because D47 is the
decision it came from.

D47's rule is that only forward-looking live claims get the
`Debian 12 tier` -> `Debian 13 tier` rename, and that historical
readings keep the name the job had when they were taken. Step 6d
applied it to `PLAN-scheduler-reservations.md:1244`, which *is* a
present-tense sentence -- and that is exactly why renaming it was
wrong. The sentence names the lane and then states its arithmetic:
`cpu_schedulable` of 1-2, `limit_cpus` of 3, three 1-vCPU instances
filling a node. That arithmetic is the pre-reshape ledger-3 shape.
Renaming the lane alone turned a correct statement about the old tier
into a false one about the live tier, which now publishes 24
schedulable vCPU across three 6 vCPU nodes
(`docs/developer_guide/ci_cloud_sizing.md`). D47 sorts sentences by
tense; the discriminator it actually needed is whether the *claim*
still holds, and a present-tense sentence can carry a stale
measurement. The bullet is now explicitly a reading taken on the
`Debian 12 tier`, with a pointer to the live figures rather than a
second copy of them.

The other three rename sites were checked for the same shape and do
not have it: they name the job in a success criterion or a close
condition and attach no arithmetic to it.

Two smaller defects in the shipped text, both introduced by this
phase: 6a's rewrite left the sentence after it referring back to
"the same way, for the same reason" when the paragraph it pointed at
had stopped saying "unknown, never as zero", so the back-reference
resolved to the opposite of its intended meaning; and 6a cited
`base.py:214` and `:180` in a living developer guide, where no checker
validates them and any edit above those lines makes them silently
wrong. The citations are now by symbol name.
