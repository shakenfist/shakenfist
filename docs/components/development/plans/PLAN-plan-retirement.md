# Plan retirement

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every week
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

Two things make planning here different from planning in a
repository that holds a product, and both should shape any plan
written from this template:

* **The blast radius is other people's repositories.** The weekly
  workflow files and closes GitHub issues fleet-wide. A change
  that is merely wrong does not produce a red build; it produces
  issues in ten repositories, or silently closes ones that should
  have stayed open. Always pass `--dry-run` when running
  `audit-manage-issues.py` by hand.
* **This repository is in its own audit matrix.** A standard we
  exempt ourselves from is a standard we stop noticing the cost
  of. A change to a criterion is a change we are measured against
  on the next run, so a plan should say how many repositories --
  including this one -- it newly fails.

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

**In this repository.** Plans here keep their phases as sections
inside the master plan rather than as separate phase files, and
the Execution table's `Plan` column is dropped accordingly;
`docs/plans/index.md` says so. The shared convention above is the
fleet default, and a plan large enough to want phase files should
use them rather than argue with the block.

## Situation

Every repository that plans the current way keeps every plan it has
ever written in `docs/plans/`, forever. Nothing removes a finished
one, so the directory only grows. A survey of the local clones on
2026-10-28 (the age is the last commit of `docs/plans/index.md`
in which the plan's row changed into a terminal status from one
that was not; the
instar clone is from 2026-08-17 and its index still holds prose
statuses, so its row is a file count only):

| Repository | Files in `docs/plans/` | Lines | Terminal (`Complete`, `Superseded`, `Abandoned`) | Terminal for over 90 days |
|------------|-----------------------:|------:|------------------:|--------------------------:|
| shakenfist | 183 | 107,405 | 23 | 14 |
| ryll | 190 | 76,018 | 27 | 23 |
| instar | 178 | 75,226 | 21+ | not measured |
| kerbside | 77 | 32,465 | 9 | 3 |
| development | 21 | 27,468 | 14 | 0 |
| divergulent | 59 | 11,094 | 7 | 3 |
| occystrap | 22 | 5,892 | 7 | 0 |

The cost is not disk. It is that every agent session that greps,
globs or surveys a repository wades through plans describing code
that has since been rewritten, and treats them as current; that
`plan-*` audits and the push audit read all of them; and that the
documentation a plan produced is never checked against the plan
again, so whatever the plan knew and the docs did not stays only
in the plan.

What a finished plan holds that is still worth having, in order of
how often it is lost:

1. **The reasoning.** Docs say what the system does; plans say why
   it is that shape and which alternatives were tried and rejected.
   `AGENTS.md` here sends agents to the analysis section of
   `PLAN-code-review-tracking.md` for exactly this ("sidecar rather
   than fields in weAudit's JSON, no stamping from CI ... exist for
   non-obvious verified reasons"). Deleting that plan without
   moving those reasons into `docs/` is how the next agent
   re-proposes a rejected design.
2. **Future work.** Every plan ends with the template's *Future
   work* section. Items there are deferred, not tracked; once the
   plan is `Complete` nobody reads it again. ryll's
   `PLAN-deferred-debt.md` was a one-off sweep that harvested
   exactly this backlog across ryll's finished plans by hand.
3. **The record of what was built.** Usually already in `docs/`,
   in the commit log and in the pull requests.

Mechanics that constrain the design, verified in the code:

* The audit clones every target with `--depth 1`
  (`.github/workflows/consistency-audit.yml:126`), so a criterion
  cannot read `git log`. Age needs the index's history, so it comes
  either from a deeper clone or from the `audit/github.py` seam's
  `api_answer()`, which raises `ToolFailure` (and so reports
  `error`) when GitHub does not answer; phase 4 decides which.
* `plan-index` (`scripts/audit/checks/plans.py`, `PlanIndex`)
  requires every master plan *file* to be linked from the index,
  and `plan_link_path()` returns `None` for an absolute URL. A row
  whose link is a permalink to a deleted file therefore already
  passes: it is not a local master, and it is not unregistered.
  No change to `plan-index` is needed for retired rows to be
  legal; a test should pin that.
* `plan-source-references` already requires a plan reference in
  source to resolve locally or be an absolute URL, so it will catch
  any source pointer a retirement forgets to rewrite. Markdown
  pointers are not checked by anything.
* ryll has a `docs/plans/completed/` directory holding four phase
  files, archived by hand in May 2026. `plan-index` matches links
  by basename on purpose so that layout works. It is the only
  instance. The four are phases of `PLAN-stream-caps-and-flap.md`,
  which is still `In progress`, so they retire with that plan;
  retirement replaces the practice, not yet the directory.
* The fleet's skills that act on audit issues (`consistency-fix`,
  `template-resync`) live in the private `llm-setup` repository
  under `dot-claude/skills/`, not in this one. A retirement skill
  belongs beside them.

## Mission and problem statement

Give finished plans an end of life. A plan whose index status has
been terminal (`Complete`, `Superseded` or `Abandoned`) for more
than 90 days is *retired*: its lasting content is moved to where it
will be maintained, and its files are deleted. Specifically:

* **What it built** is described in `docs/`, and any gap is filled.
* **Why it is that shape** -- the constraints, measurements and
  rejected alternatives a future change would otherwise rediscover
  -- is written into `docs/` (or `ARCHITECTURE.md` where it is
  about the shape of the system), not left in the plan.
* **Future work still worth doing** becomes a GitHub issue; what is
  dropped is listed with a reason.
* **Every pointer to the plan** -- in markdown, source, other plans,
  `AGENTS.md` -- is rewritten to the new home or to a permalink.
* **The plan's row stays in `docs/plans/index.md`**, status
  unchanged, its link replaced by a permalink to the last commit
  that held the file. The index remains the complete history of
  what the repository has planned; `git` keeps the text.

The split is deterministic where it can be and judgement where it
must be. A new criterion, `plan-retirement`, finds the candidates
and files one issue per repository. A new skill, `retire-plans`,
does the work and opens one pull request per repository, one commit
per plan, which Mikal reviews and merges. Nothing is retired
without that review: this is deliberately *not* part of the
`prune-reviews` workflow, which pushes straight to the default
branch and is safe only because what it does is computable.

Out of scope: retiring plans that are not terminal; changing the
status vocabulary (retired is not a status -- a retired plan is
still `Complete`, and its row says so); rewriting the history of
plans that already exist; and any automation that retires without a
human merging the pull request.

## Open questions

1. **When are the future-work issues filed?** Decided 2026-10-28
   by Mikal, taking the default: the skill
   files them while preparing the pull request, labelled
   `from-retired-plan`, and lists them in the pull request body;
   if the pull request is closed unmerged, the skill's instructions
   say to close them too. The alternative -- drafting them in the
   body and filing after merge -- keeps the issue tracker clean of
   anything unapproved but needs a second session after the merge.
2. **What starts the 90-day clock?** Decided 2026-10-29 by Mikal,
   reversing the original default: the commit where the plan's
   index row last changed into a terminal status. The default was
   the last commit touching the plan's files, but phase 2's helper
   showed that maintenance sweeps restart it constantly
   (divergulent `ddeaff6` touched six finished plans; one ryll sweep
   eight), and that retirement's own pointer rewrites would restart
   the clocks of the plans they edit. Under the last-touch clock,
   divergulent, kerbside and ryll had 1, 1 and 10 eligible rather
   than 3, 3 and 23.
3. **Is there an opt-out?** Default: no. A plan worth keeping past
   90 days is a plan whose content belongs in `docs/`, which is
   what retirement does. If one is needed later, it is a marker in
   the plan file, with a reason, like the other `audit-ok` markers.
4. **Do `Superseded` and `Abandoned` plans retire too?** Default:
   yes, on the same clock. For an abandoned plan, the reasoning
   worth keeping is usually why it was abandoned.
5. **Is 90 days right?** Default: yes, as a constant in the check,
   revisited after the first fleet-wide round.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Retirement convention | Complete | f7b1219 (#333) |
| 2. `retire-plans` helper and skill | Complete | 8010a22 (#342); llm-setup 3a9f268 (!20) |
| 3. Pilot | Blocked | divergulent c7a980c (#143), kerbside d2971e6 (#686), ryll 0c42528 (#700); llm-setup 956b2fa (!21), dfebae1 (!22) |
| 4. `plan-retirement` criterion | Not started | |
| 5. Push audit | Not started | |

The criterion lands after the pilot on purpose. It files issues the
day it merges, in every repository with an eligible plan -- by the
survey, at least four -- and an issue pointing at a skill nobody has
run is an issue nobody can act on.

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

**In this repository.** The same term is written twice: once in
this plan's own phase table, and once in the row the plan carries
in `docs/plans/index.md`. The index row is the whole-plan status,
so it only reaches `Complete` once every phase has been
completed, abandoned or superseded. The `plan-index` criterion
reads that table, and this repository is inside its own audit
matrix, so a status that drifts out of the vocabulary fails our
own tooling before it fails anybody else's.

<!-- shared-block: plan-push-audit-phase v4 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
<!-- shared-block-end -->

**In this repository.** `PUSH-AUDIT.md` exists at the repository
root and is referenced from `AGENTS.md`, so the final phase runs
it rather than explaining its absence. Note that every diff
command in it is written against `main...HEAD`: a stale local
`main` silently widens the audit to unrelated history, so fetch
before starting, or read it as `origin/main...HEAD`.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

**In this repository.** The plan index is `docs/plans/index.md`.
`review-tracking-tests` deliberately does not assert the `REVIEWS.md`
header count, so a phase that adds or removes an in-scope file needs
no regeneration commit; `prune-reviews` corrects the count on the
next push to main.

### Phase 1: retirement convention

Planning effort: medium. Lands on this plan's own branch,
`plan-retirement`, with the master plan, as `plan-release-gating`
carried release gating's plan and first phase.

#### Scope

In: one page in `docs/` that defines eligibility and what retiring
a plan involves, which both the skill and the criterion will cite; a
test that a retired index row is legal; a pointer from
`PLAN-TEMPLATE.md` and `README.md`.

Out: the skill (phase 2), any check (phase 4), retiring anything,
and any change to the fleet's `PLAN-TEMPLATE.md` copies. Nothing this
phase does files an issue or changes a verdict anywhere.

#### What the survey found

Checked against the tree on 2026-10-28, at `1426131`:

1. **A retired row already passes `plan-index`, and is already
   covered by a test.** `plan_link_path()`
   (`scripts/audit/checks/plans.py:345`) returns `None` for any
   target with a scheme, so a permalink registers nothing and is
   never an unregistered master.
   `PlanIndexTest.test_a_link_to_another_repository_does_not_register_a_local_plan`
   (`scripts/tests/test_plans.py:1155`) already pins URL handling;
   `PlanAuditPhaseTest.test_a_plan_linked_by_absolute_url_is_not_judged`
   (`:2095`) shows `plan-audit-phase` skips such rows too. The
   master plan's step 1c assumed a gap here; what is missing is a
   test whose fixture *is* a retired row (a permalink, a terminal
   status, no local file), named for the case it protects.
2. **A shared block is the wrong vehicle.** Every block in
   `templates/shared-blocks/` is required by `PLAN_TEMPLATE_BLOCKS`
   or `PUSH_AUDIT_BLOCKS`; none is optional. An embedded block that
   is not required is still validated for version and wording
   (`scripts/audit/text/shared_blocks.py:131-160`), so an optional
   one would work, but it would be the first, and it would buy
   nothing: the text is read by one skill and one spec page, both of
   which can link to a single page here. Shared blocks exist to be
   copied verbatim into many files. See decision 1.
3. **Dangling pointers are only partly caught.** `docs-external-links`
   (`scripts/audit/checks/docs_content.py:305`) reports a relative
   link inside `docs/` to a file that does not exist, and
   `plan-source-references` reports source pointers that do not
   resolve. Nothing checks `README.md`'s absolute `blob/main` links
   (this repository's README links `PLAN-code-review-tracking.md`
   that way, line 29) or backticked names in `AGENTS.md` (three
   here). After a retirement those are silent 404s and stale names.
   That is the skill's job (phase 2 brief already says
   `git grep -n <basename>`); the page should say so plainly.
4. **The permalink sha needs a rule.** "The last commit that held
   the file" is ambiguous until the deleting pull request merges.
   `git log -1 --format=%H origin/<default> -- <file>` names the
   last commit on the default branch that changed the file: it is
   on the default branch, so it is never garbage collected, and it
   holds the final text. See decision 2.

Corrected at source: the master plan's phase 1 table (replaced by
this section), and the references to a `plan-retirement` shared
block in steps 2a, 4a and 4b, which now name `docs/plan-retirement.md`.

#### Decisions

1. **A docs page, not a shared block.** `docs/plan-retirement.md`
   is the definition. Phase 4 may promote it into a block if the
   pilot shows plan authors in other repositories need it in front
   of them while writing; nothing before then reads it from a
   template. This is the decision most likely to be argued with: a
   block would put the rule in every repository's template, where
   the agent writing a plan's *Future work* would see that it will
   one day be filed as issues. The answer is that `PLAN-TEMPLATE.md`
   here gets a one-sentence pointer (step 1c), and the fleet copies
   get it in phase 4 if the pilot earns it.
2. **Permalinks pin the last commit that changed the file**, as in
   survey item 4, never `main`.
3. **Retirement keeps the status.** A retired row still reads
   `Complete` (or `Superseded`, `Abandoned`). Retired is a fact
   about where the text lives, which the link already shows; the
   status vocabulary block says a status answers only whether a plan
   wants attention, and a retired plan does not.
4. **A plan whose reasoning cannot be placed is not retired.** The
   page says the skill reports it and leaves it; deleting the only
   copy of a decision's justification to meet a 90-day rule is the
   failure this plan exists to prevent.

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Write `docs/plan-retirement.md` (title *Retiring finished plans*, under ~120 lines, no phase numbers -- `plan-phase-references` checks docs/). Sections: **Why** (plans accumulate; their reasoning and future work are lost when nothing reads them again; three or four sentences, using the numbers in this plan's Situation without citing the plan). **Eligibility**: index status `Complete`, `Superseded` or `Abandoned`, and no commit has touched the master plan or any of its phase files for 90 days. **What retiring involves**, as a numbered list: (1) docs/ describes what the plan built; (2) its reasoning -- constraints, measurements, rejected alternatives, push-audit declines -- is written into docs/ or ARCHITECTURE.md; (3) each still-valid *Future work* item becomes an issue labelled `from-retired-plan`, filed while the pull request is prepared and listed in its body, and closed again if the pull request is closed unmerged; dropped items are listed with a reason; (4) every pointer is rewritten -- relative links in docs/ (`docs-external-links` catches those), source (`plan-source-references` catches those), and the ones nothing catches: absolute `blob/<branch>` links in README.md, backticked names in AGENTS.md, ARCHITECTURE.md, other plans and `.claude/`; (5) the master and phase files are deleted; (6) the index row keeps its date, intent and status, and its link becomes `https://github.com/shakenfist/<repo>/blob/<sha>/docs/plans/<file>` where `<sha>` is `git log -1 --format=%H origin/<default> -- <file>`. **What it is not**: retired is not a status (decision 3 above, in a sentence); a plan whose reasoning cannot be placed is left in place and reported; it always goes through a reviewed pull request, one per repository, one commit per plan, and is never done by the `prune-reviews` workflow, which pushes straight to the default branch. **History**: ryll's `docs/plans/completed/` was an earlier archive-in-place approach; retirement replaces it. End with a one-line pointer to the `retire-plans` skill in llm-setup as what does this, noting it is not written yet. |
| 1b | medium | sonnet | none | In `scripts/tests/test_plans.py`, add `test_a_retired_row_is_legal` to `PlanIndexTest` (near `test_a_link_to_another_repository_does_not_register_a_local_plan`, line ~1155), using `self._check(plans=[...], index=...)` with `self.HEADER`. Fixture: one live plan `PLAN-live.md` linked normally with status `In progress`, and one row dated earlier linking `https://github.com/shakenfist/development/blob/0123456789abcdef0123456789abcdef01234567/docs/plans/PLAN-gone.md` with status `Complete`, and no `PLAN-gone.md` on disk. Assert `assert_pass`. Comment why in the file's style: a retired plan's row is the index's record that it existed, and a future check requiring every linked plan to exist locally would break every retired row. Run `python3 scripts/tests/test_plans.py`. |
| 1c | low | sonnet | none | In `PLAN-TEMPLATE.md`, in the *Documentation index maintenance* section, after the paragraph ending "Update it as the plan progresses, not only at the end.", add one paragraph: once a plan has been finished for 90 days it is retired as `docs/plan-retirement.md` describes -- its reasoning moved into docs/ and its *Future work* filed as issues -- so write both to survive being read by someone who was not here. Outside any shared block. In `README.md`, add a curated link to `https://github.com/shakenfist/development/blob/main/docs/plan-retirement.md` beside the other docs links, matching their one-line form. |

#### Risks and mitigations

* **The page drifts from what the skill does.** The skill (phase
  2) links the page rather than restating the rule; the management
  session checks in phase 2 review that the skill quotes no number
  the page also carries except 90.
* **README growth.** README.md is a pitch; one curated link is
  within its rules (`readme-structure` is run as part of the
  verification below).

#### Definition of done

* `docs/plan-retirement.md` exists and `grep -nE '[Pp]hase [0-9]'
  docs/plan-retirement.md` prints nothing.
* `python3 scripts/tests/test_plans.py` passes and includes
  `test_a_retired_row_is_legal`.
* `grep -n 'plan-retirement.md' PLAN-TEMPLATE.md README.md` finds
  one line in each.
* `pre-commit run --all-files` passes.
* `python3 scripts/audit-check.py --repo-path . --repo-name
  development` reports the same verdicts as on `1426131`, apart
  from `review-coverage` detail for the new, unreviewed page.
* No file under `templates/` changed.

#### Back brief

Before step 1a, back brief Mikal on decision 1 (docs page rather
than shared block). It is cheap to change now and touches phases 2
and 4 if it changes later.

#### Phase 1 execution notes

2026-10-28 (AEDT). The back brief's one gate, a docs page rather
than a shared block, was answered by Mikal before implementation
began; he also gave this session leave to push and open the pull
request once the phase was done.

* Steps 1a and 1b ran in parallel: they write separate files, and
  neither consumes the other. Step 1c ran after both, since it
  links the page 1a wrote.
* 1a's page carries two sentences beyond its brief, explaining why
  the clock is the last commit and that `Superseded` and
  `Abandoned` plans share it. Both restate this plan's open-question
  defaults, so they were kept.
* 1b's test was shown to fail on a copy of the tree in which
  `PlanIndex` rejected scheme links; the two neighbouring URL tests
  failed with it.
* `audit-check.py` reports `llm-context-lint` as `error` locally
  both before and after the phase: `skillsaw` is not installed on
  this host. It is a tooling gap here, not a verdict.

### Phase 2: `retire-plans` helper and skill

Planning effort: high. The skill is where the judgement lives, and a
weak brief here produces confident deletions of the reasoning the
mission exists to keep. Review effort: high, by the management
session reading every line of the skill.

#### Scope

In, all in `llm-setup` (the private `gitlab.home.stillhq.com`
repository whose `dot-claude/skills/` is symlinked as
`~/.claude/skills`):

* `bin/retire-plans`, a helper doing every deterministic part of a
  retirement;
* `tests/test_retire_plans.py` for it;
* `dot-claude/skills/retire-plans/SKILL.md`, the judgement;
* a hand-off paragraph in `dot-claude/skills/consistency-fix/SKILL.md`;
* a row for the skill in `llm-setup`'s `README.md` skills list, and
  for the helper in `docs/layout.md` and `docs/helper-scripts.md`
  (the README lists no helpers).

Out: retiring any plan (phase 3), the criterion (phase 4), and any
change to this repository beyond this plan's own record.

#### What the survey found

Checked on 2026-10-28 against development `1118f06` and llm-setup
`cf613af`:

1. **The master plan put deterministic work in skill prose, which
   llm-setup forbids.** Its `AGENTS.md` says "Deterministic work goes
   in a script, not in skill prose ... A skill tells the model which
   command to run and when to involve Mikal." Listing eligible plans,
   computing permalinks, finding pointers, rewriting index links and
   running the relevant criteria are all deterministic. The precedent
   is `template-resync`: `bin/template-resync` (582 lines) with
   `status` / `apply` / `check` subcommands, `tests/test_template_resync.py`,
   and a 229-line skill. Phase 2 is reshaped to match; the master
   plan's step 2a, which asked for all of it in the skill, is replaced
   by this section.
2. **The live skills are the llm-setup main checkout.** `install.sh`
   links `dot-claude/skills` into `~/.claude/skills`, so editing the
   primary clone changes every running session. The work happens in
   an llm-setup worktree.
3. **llm-setup lands both ways.** Recent skills (`template-resync`
   `c1d993d`, `import-bloat` `3407b07`) were committed directly to
   main; larger work went through merge requests
   (`session-events-phase-14-*`). This phase uses a merge request, so
   Mikal reviews the skill before it is live (decision 2).
4. **llm-setup has no pre-commit.** Its checks are
   `python3 -m unittest discover tests` and `bash -n` on changed
   shell. Its tests mock `gh` and use temporary git repositories.
5. **Index layouts differ across the fleet**, and the helper must
   cope rather than assume this repository's four columns:
   ryll's index has a fifth `Phases` column of links to phase files
   (`ryll/docs/plans/index.md:29`), which a retirement must also turn
   into permalinks or drop; instar's index (local clone of
   2026-08-17) has prose in its status column, which `plan-index`
   already fails. The helper finds the Status column by its header,
   as `plan_table_columns` does in `scripts/audit/checks/plans.py`,
   matches statuses exactly against the shared vocabulary, and
   reports a row it cannot read instead of guessing.
6. **`audit-check.py` has no per-criterion filter** (its only options
   are `--repo-path`, `--repo-name`, `--github-org`). Callers filter
   the JSON's `checks[]` by `id`, as `template-resync check` and
   `consistency-fix` already do.
7. **The `from-retired-plan` label exists nowhere yet.** Filing the
   first issue in a repository must create it.
8. **`plan-blockers` may hold entries for a retired plan.** It has a
   `clear` subcommand (`plan-blockers clear --plan <name>`), which the
   skill runs after the merge, not before.
9. **The `consistency-fix` hand-off names an issue that does not exist
   yet.** `Completed plans awaiting retirement` is filed only once the
   phase 4 criterion lands; the paragraph is written now so the
   llm-setup change is one review, and is inert until then.

Corrected at source: the master plan's phase 2 table, replaced by
this section, and the Execution table row's name.

#### Decisions

1. **Helper does the mechanics, skill does the judgement.** The
   helper never decides what is reasoning, what is still valid, or
   where text belongs; the skill never computes an age, a sha or a
   grep. This is the decision most likely to be argued with, because
   it roughly doubles the size of the phase. The answer is that every
   deterministic step the skill did by hand would be done slightly
   differently in each of some forty retirements, and the
   pointer search in particular is exactly what a model gets
   plausibly wrong: a missed `blob/main` link in a README is a silent
   404 nobody reports.
2. **Through a merge request**, branch `retire-plans`, so the skill is
   reviewed before it is live in every session. Recorded in `Merged`
   as `llm-setup <merge sha> (!<mr>)`.
3. **Helper subcommands**, each read-only unless stated:
   * `eligible [REPO_PATH]` -- fetch `origin`, read the index's plan
     table(s) by header, and for each row with a local link and a
     terminal status list the master and phase files and the date of
     the commit on `origin/<default>` where the row last changed
     into a terminal status, found by walking the index's history
     (open question 2). The last commit touching the files is shown
     for information only.
     Prints a table, and `--json`. Rows it cannot read are listed
     separately with why. `--days` defaults to 90.
   * `pointers PLAN` -- every line in the repository, outside the
     plan's own files, naming any of its files' basenames, by
     `git grep -n -F`, grouped as: index row, other plans, docs/,
     README.md, AGENTS.md / ARCHITECTURE.md, `.claude/`, source.
   * `permalink FILE` -- the URL, from
     `git log -1 --format=%H origin/<default> -- docs/plans/FILE`
     and the `origin` remote's owner and name.
   * `rewrite-index PLAN` (writes) -- replace every link in the index
     to the plan's master or phase files with its permalink, leaving
     every other byte of the row alone.
   * `check REPO_PATH` -- run development's `audit-check.py` against
     the checkout and report `plan-index`, `plan-source-references`,
     `plan-phase-references` and `docs-external-links`; exit 1 if any
     is `fail` or `error`.
4. **The skill's per-plan order** is fixed, because the cheap
   refusals come first: read the plan and phases whole; decide where
   the reasoning goes, and stop on that plan if nowhere fits; write
   the docs changes; triage *Future work*; file issues; rewrite
   pointers from `pointers`; `rewrite-index`; delete the files; one
   commit. Then `check` once over the branch.
5. **Issues are filed only once the plan's commit is ready**, so a
   plan abandoned mid-way leaves no orphaned issues; and the skill's
   pull request body lists them, as the open-question answer
   requires.
6. **The skill refuses repositories with a failing `plan-index`.**
   An index the audit cannot read is one the helper cannot rewrite
   safely; fix it first (it already has its own consistency issue).

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none (llm-setup worktree) | Write `bin/retire-plans` and `tests/test_retire_plans.py` in the llm-setup worktree, implementing decision 3 exactly. Read first: llm-setup `AGENTS.md`; `bin/template-resync` (argparse layout, `say()`, how it locates the development clone via `SHAKENFIST_DEVELOPMENT` and runs `audit-check.py` in `cmd_check`, line ~513) and `tests/test_template_resync.py` (temporary git repositories, mocked `gh`); development's `docs/plan-retirement.md` (the rules) and `scripts/audit/checks/plans.py` (`PLAN_STATUSES`, `plan_table_columns`, `PLAN_LINK_RE`, `plan_link_path`, `PLAN_PHASE_FILE_RE`) -- reimplement the small parts needed rather than importing development's package, and say so in a comment. Stdlib only. Tests cover: a four-column and a five-column (ryll-style `Phases`) index; a prose status reported as unreadable; age from the newest of master and phase files; a permalink row already retired is skipped; `pointers` finding a `blob/main` README link, a backticked AGENTS.md name and a phase-file basename; `rewrite-index` changing only link targets; `check` exiting 1 on a failing criterion (mock the audit run). Run `python3 -m unittest discover tests`. |
| 2b | xhigh | opus | none (llm-setup worktree) | Write `dot-claude/skills/retire-plans/SKILL.md`, after 2a has landed in the worktree. Read `consistency-fix/SKILL.md` and `template-resync/SKILL.md` for shape and tone, and development's `docs/plan-retirement.md`, which the skill links and must not restate. Frontmatter description says when to use it (asked to retire plans; a `Completed plans awaiting retirement` issue; `/retire-plans` bare means every eligible plan in the current repository). Body: invocation and authority (being asked is permission to create the worktree `<repo>-wt-retire-plans-<date>` and commit per plan; pushing and the PR need an ask, as in consistency-fix); refuse if `check` shows `plan-index` failing; skip plans an open PR already retires (`gh pr list --search retire`); per plan, decision 4's order, with the judgement spelled out: what counts as reasoning (constraints verified, measurements, rejected alternatives and why, back-brief corrections, push-audit declines), where it goes (the existing doc a reader of that code would open; ARCHITECTURE.md only for the shape of the system; never AGENTS.md unless it is a convention), how to triage *Future work* (still valid against the code? already an issue?), and that a plan with nowhere for its reasoning is left and reported. Issues labelled `from-retired-plan` (create the label if absent), filed per decision 5. PR body: per-plan table of docs changed, reasoning moved, issues filed, items dropped with reasons. After merge: `plan-blockers clear --plan <name>` where an entry exists. Never touches `REVIEWS.md`, never merges. Call the helper as `$HOME/bin/retire-plans`. Under 250 lines. |
| 2c | medium | sonnet | none (llm-setup worktree) | In `dot-claude/skills/consistency-fix/SKILL.md`, add a short paragraph where it decides how to fix each failing check: a `Completed plans awaiting retirement` issue (check id `plan-retirement`) is not fixed here; hand it to the `retire-plans` skill, whose pull request closes it. Add `retire-plans` (skill) and `retire-plans` (helper) to llm-setup's `README.md` in the lists where `template-resync` appears, matching their form. |

#### Risks and mitigations

* **The skill reads as plausible and retires badly.** No test can
  catch that; phase 3's pilot is the test. The management session
  reviews the skill against the four failure modes in decision 4's
  order before the merge request is opened.
* **Live-skill exposure.** Mitigated by decision 2: nothing reaches
  `~/.claude/skills` until Mikal merges.
* **Helper and development drift.** The helper reimplements a little
  of `plans.py`. Its tests use index layouts from the fleet, and
  `check` runs the real criteria, so a drifted reading is caught by
  the audit's verdict, not trusted.

#### Definition of done

* In the llm-setup worktree, `python3 -m unittest discover tests`
  passes and includes `tests/test_retire_plans.py`.
* `bin/retire-plans eligible ~/src/shakenfist/divergulent` and
  `.../kerbside` each list 3 plans, matching this plan's Situation
  table (which measured the same status-change clock), or the
  difference is explained in the execution notes.
* `bin/retire-plans eligible ~/src/shakenfist/ryll` reads the
  five-column index without listing any row as unreadable.
* `bin/retire-plans pointers PLAN-code-review-tracking.md`, run in
  this repository, finds the `README.md` link at line 29 and the
  `AGENTS.md` mentions.
* `grep -cwiE '90|ninety|three months' dot-claude/skills/retire-plans/SKILL.md`
  is at most 1, the skill links `docs/plan-retirement.md`, and on
  review it does not describe how the clock is computed: the rule
  lives in one place.
* The merge request is open, and Mikal has merged it.

#### Back brief

Before step 2a, back brief Mikal on decision 1 (helper plus skill,
not skill alone), since it doubles the phase, and confirm decision 2
(merge request rather than a direct commit to llm-setup main).

#### Phase 2 execution notes

2026-10-29 (AEDT). The back brief confirmed the helper-plus-skill
shape and a merge request. This shell has no credentials for
`gitlab.home.stillhq.com`, so the llm-setup branch was cut from the
local main (`cf613af`) and Mikal pushes it and opens the merge
request; the `Merged` cell records it once merged.

That hand-off was lost: #342 merged on this side while the
llm-setup branch was still unpushed, so for two days the plan read
as further along than the fleet was. It merged as `3a9f268` (!20)
on 2026-10-31, after `next-phase` found the skill missing from
`~/.claude/skills`.

llm-setup branch `retire-plans`:

| Commit | Step |
|--------|------|
| `85316be` | 2a: `bin/retire-plans` and its tests |
| `589d84d` | 2a follow-up: `pointers` matches plans cited without `.md` |
| `ced5395` | 2b: the `retire-plans` skill |
| `b088711` | 2c: `consistency-fix` hand-off; README and docs entries |

* **The clock changed.** 2a's first version used the last commit
  touching a plan, as open question 2's default said, and found it
  restarted by sweeps (divergulent 1 / kerbside 1 / ryll 10
  eligible, not 3 / 3 / 23). Mikal chose the status-change clock;
  corrected at source in `b7f1470` here (the docs page, open question
  2, decision 3 and phase 4's brief). With it, the helper's counts
  match the Situation table exactly. A change from one terminal
  status to another does not restart the clock, as the helper
  implements; the docs page says so.
* `permalink` and `rewrite-index` resolve a plan's file by name
  anywhere under `docs/plans/`, as `plan-index` does, so ryll's
  `completed/` phase files get their real path; tests cover it.
* 2a's extras, kept: `eligible` refuses a shallow clone, and never
  dates a row by an index rename or by the commit restoring a
  deleted index; `permalink` and `rewrite-index` fetch too, and take
  `--no-fetch`; `rewrite-index --dry-run`; `eligible --all`; a table
  with no Status column is reported as never eligible rather than
  unreadable (ryll has one, of 12 plans).
* 2b's author found that `pointers` missed plans cited by stem
  (divergulent `PLAN-published-cache.md:12,43,131`) and worked
  around it with a grep in skill prose; that went into the helper
  instead (`589d84d`) and the prose came out. Its own two
  conventions were kept: commit subject `Retire PLAN-<stem>.md.`, and
  `Fixes` on the audit issue only when every eligible plan retired.
* 2c added the helper to llm-setup's `docs/layout.md` and
  `docs/helper-scripts.md`, since its README lists no helpers; the
  management session added the `check` subcommand the entry omitted.
* Mutation checks, all on copies: `pointers` skipping README.md (2
  tests failed), `rewrite-index` rewriting a non-plan link (3), first
  rather than latest terminal transition (2), rename detection off
  (1), stem boundary dropped (2, and 4 with the names too). Suite:
  720 tests, OK.

### Phase 3: pilot

Planning effort: medium. Review effort: high, by Mikal reading every
deletion in the pull requests to divergulent and kerbside, and the
ryll batch commit by commit.

#### Scope

In: running the `retire-plans` skill, in the management session, on
three repositories in turn -- divergulent, then kerbside, then a
chosen batch of ryll's plans -- one pull request per repository;
the revisions to the skill and helper that each pilot shows are
needed, as llm-setup merge requests; and a record here, per
repository, of what the skill got right, what it got wrong, and
what changed because of it.

Out: the criterion (phase 4), retiring anything in this repository
(none is eligible before 2026-11-20), ryll's remaining seventeen
eligible plans, and ryll's `docs/plans/completed/` directory (survey
item 3). No issue is filed by any audit in this phase; the only
issues filed are the skill's own `from-retired-plan` ones.

#### What the survey found

Checked on 2026-10-31 against development `41a4c92`, llm-setup
`3a9f268`, divergulent `765d291`, kerbside `26ae364` and ryll
`f86cff1` (each the fetched `origin/develop`):

1. **The skill and helper are live.** `~/.claude/skills/retire-plans/`
   and `~/bin/retire-plans` both resolve into llm-setup's main
   checkout at `3a9f268`.
2. **Eligibility has moved since the master plan was written.**
   `retire-plans eligible`:

   | Repository | Eligible | Files | Lines |
   |------------|---------:|------:|------:|
   | divergulent | 3 | 18 | 3,125 |
   | kerbside | 4 | 27 | 10,031 |
   | ryll | 23 | 139 | 58,467 |

   kerbside has four, not three: `PLAN-kerbside-vdi-tokens.md`
   went terminal on 2026-07-31 and crossed 90 days on 2026-10-29.
   divergulent and ryll match the Situation table.
3. **ryll's `completed/` cannot be folded in.** Its four files are
   phases 1, 4, 5 and 12 of `PLAN-stream-caps-and-flap.md`
   (`ryll/docs/plans/index.md:56`), which is `In progress`. They
   retire with that plan, through the ordinary path, since
   `permalink` and `rewrite-index` already resolve files under
   `completed/`. The phase's original text, and the Situation bullet
   saying retirement supersedes the directory, are corrected here.
4. **The helper miscounts cross-repository rows as retired.**
   `eligible` reports kerbside as having one plan "already retired".
   It has none: `kerbside/docs/plans/index.md:30` links Shaken
   Fist's `PLAN-kerbside-deployer.md` at `blob/develop`, the
   cross-repository form, and the helper counts every absolute-URL
   row with a terminal status as retired
   (`llm-setup/bin/retire-plans:245,269`). The count is informative
   only, so nothing goes wrong yet, but phase 4's criterion and a
   reviewer reading `eligible` both need the distinction: a
   retirement permalink pins a forty-hex sha, a cross-repository
   link names a branch. Fixed in step 3b.
5. **The pilots' preconditions hold.** In the 2026-10-31 compliance
   page all three pass `plan-index`, `plan-source-references`,
   `plan-phase-references` and `docs-external-links`, so the skill
   will not refuse any of them. None has an open pull request
   matching `retire`, and none has a `from-retired-plan` label yet;
   the skill creates it.
6. **Review tracking is not disturbed.** kerbside and ryll exclude
   `docs/plans/*` from `.vscode/review-scope.toml` (lines 110 and
   100), so deleting plans moves no review marks or header counts.
   divergulent has no review scope.
7. **Each pilot repository has a `PUSH-AUDIT.md`.** The
   `plan-push-audit-phase` block says a phase landing in another
   repository is audited there, by the pull request that lands it,
   and this plan's push-audit phase cites that audit rather than
   re-running it. Phase 5's text assumed otherwise; see decision 5.
8. **This shell cannot push to llm-setup.** It has no credentials for
   `gitlab.home.stillhq.com`, which is how phase 2's merge request
   went unopened for two days after #342 merged (phase 2 execution
   notes). Each skill revision here is a branch Mikal pushes.

Corrected at source: this section replaces the phase's paragraph;
the Situation bullet on ryll's `completed/` (survey item 3); and
phase 5's text (decision 5).

#### Decisions

1. **The management session runs the skill itself**, as an ordinary
   invocation (`/retire-plans` in the target repository), not through
   a sub-agent with a brief. The pilot is testing the skill's prose
   as a session reads it; a brief would front-load exactly the
   judgement the skill is supposed to supply, and hide its gaps.
2. **ryll retires a batch of six, not all twenty-three.** This is
   the decision most likely to be argued with, since the skill's
   bare form retires every eligible plan and the fleet will use it
   that way. But 58,467 lines in one pull request cannot be read
   deletion by deletion, which is the only test of a retirement that
   exists, and the pilot's job is to find the plan shapes the skill
   handles badly, not to clear the backlog. The batch, 10,271 lines,
   is chosen for range rather than age:

   | Plan | Lines | Why it is in the batch |
   |------|------:|------------------------|
   | `PLAN-initial.md` | 197 | The port bring-up: almost all *Situation*, which the skill must recognise as now false |
   | `PLAN-pip-distribution.md` | 181 | Single file, no phases, the most recent to qualify |
   | `PLAN-hamburger-menu.md` | 571 | Small UI plan; tests whether user-facing docs already cover it |
   | `PLAN-pr31-followup.md` | 1,558 | A follow-up plan: its future work is likely already issues |
   | `PLAN-deferred-debt.md` | 2,776 | The hand-made harvest of other plans' future work; the hardest triage, and the evidence for the bulk-mode Future work item |
   | `PLAN-crate-extraction.md` | 4,988 | Reasoning about the shape of the system, so the one most likely to need `ARCHITECTURE.md` |

   It also exercises the skill's named-plan invocation, which the
   two small pilots do not. The other
   seventeen go through the issue phase 4's criterion files, as the
   rest of the fleet's will.
3. **The pilots run in sequence, each on the revised skill.** A
   repository starts only when the previous pull request has had its
   review and any skill revision it caused has merged, so each pilot
   tests the skill the previous one produced. A pilot that finds
   nothing to change says so in one line and needs no merge request.
4. **Each skill revision is its own llm-setup merge request**, on a
   branch named `retire-plans-pilot-<repo>`, cut in an llm-setup
   worktree, pushed by Mikal (survey item 8). The management session
   records the unpushed branch as a decision blocker
   (`plan-blockers block --plan plan-retirement`) the moment it is
   ready, and swaps it for the merge request (`plan-blockers add
   private/claude!N`) once Mikal has opened it, so the SessionStart
   hook keeps reporting it until it merges and the hand-off cannot
   be lost as phase 2's was. Step 3b's helper fix
   rides in the first revision.
5. **Each pilot pull request runs its repository's `PUSH-AUDIT.md`
   before it is pushed**, as the `plan-push-audit-phase` block
   requires of a phase landing in another repository, and the pull
   request body says what it found. Phase 5 then cites those audits
   and does only its own extra check, that no retirement deleted
   reasoning something still pointed at. This adds a sub-agent pass
   to each pilot, which is what the block prices in.
6. **Findings are recorded against the skill, not the plan.** For
   each pilot the execution notes say what the reviewer agreed was
   missing from `docs/` and was added, what the skill got wrong, and
   the skill change it led to. A wrong retirement is corrected in its
   own pull request before merge; what goes into the notes is why the
   skill produced it.

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | management session (divergulent worktree, as the skill cuts it) | In `~/src/shakenfist/divergulent`, invoke `/retire-plans` bare: it should retire `PLAN-full-machine-run.md`, `PLAN-faster-full-run.md` and `PLAN-generated-marking.md`. Follow the skill exactly as written; where it is silent or wrong, do what is right, and write down the gap as you go -- that list is the pilot's output. Before asking to push, run divergulent's `PUSH-AUDIT.md` over the branch (decision 5). Push and open the pull request on Mikal's answer. Stop for his review; do not start 3b's changes until it is done. |
| 3b | high | opus | llm-setup worktree, branch `retire-plans-pilot-divergulent` | Revise `dot-claude/skills/retire-plans/SKILL.md` and `bin/retire-plans` for what 3a and its review found. Always: `eligible` counts a row as retired only when its link is a GitHub `blob/<40-hex sha>/docs/plans/` URL to this repository; any other absolute link is a cross-repository row, reported in its own count (survey item 4), with a test using kerbside's line 30 as the fixture. Run `python3 -m unittest discover tests`. Commit; record the branch as a plan-blocker (decision 4) and ask Mikal to push it and open the merge request. |
| 3c | high | opus | management session (kerbside worktree) | As 3a, in `~/src/shakenfist/kerbside`, on the merged revision: `PLAN-test-harness.md`, `PLAN-rust-proxy.md`, `PLAN-host-subject.md`, `PLAN-kerbside-vdi-tokens.md`. Confirm `eligible` now reports 0 already retired and 1 cross-repository row. kerbside's merge queue runs full CI for any non-docs file, so a pointer rewritten in source costs a full run; that is expected, not a reason to leave the pointer. |
| 3d | high | opus | llm-setup worktree, branch `retire-plans-pilot-kerbside` | As 3b, for what 3c found. Skip, with one line in the notes, if nothing. |
| 3e | high | opus | management session (ryll worktree) | As 3a, in `~/src/shakenfist/ryll`, invoking `/retire-plans` with decision 2's six file names as arguments. ryll's index has a fifth `Phases` column; `rewrite-index` handles it, and the reviewer should see every phase link there become a permalink. The pull request body says seventeen eligible plans remain and are left for the audit issue phase 4 will file; there is no issue to name yet. |
| 3f | high | opus | llm-setup worktree, branch `retire-plans-pilot-ryll` | As 3b, for what 3e found, and decide from all three pilots whether the bulk mode in this plan's *Future work* is needed. Say so in the notes either way. |
| 3g | medium | opus | none | Write *Phase 3 execution notes* here: a per-repository table (plans retired, plans left and why, issues filed, reviewer-agreed docs additions, skill errors, skill change), the `Merged:` lines, and any correction phase 4's brief needs -- in particular, anything the pilots showed about which rows the criterion should and should not count. |

#### Risks and mitigations

* **A retirement deletes reasoning nothing else held.** The one
  failure this plan exists to prevent. Mitigated by Mikal reading
  every deletion in divergulent and kerbside, by the ryll batch being
  sized to be read the same way (decision 2), and by phase 5's
  pointer check. The management session flags, in each pull request
  body, any passage it judged not to be reasoning and is least sure
  of, so the review starts there.
* **Issues filed for a retirement that is then rejected.** The skill
  closes them with a link if its pull request closes unmerged; the
  management session checks the label's count against the body
  (definition of done) so none is orphaned.
* **A skill revision is lost between this shell and GitLab again.**
  Decision 4: each is a recorded plan-blocker from the moment its
  branch exists.
* **The target repositories move under a long review.** ryll is busy
  (145 commits ahead of the local clone today). The skill's own
  rebase rule handles a conflict on a deleted plan; the management
  session re-runs `check` after any rebase.
* **Pilot fatigue shapes the record.** If a pilot is cut short, the
  notes say what was not tried, rather than reporting the pilot as
  if it ran to the end.

#### Definition of done

* The divergulent, kerbside and ryll pull requests have merged, each
  retiring the plans named in its step or naming in its body each
  plan left and why.
* For each retired plan, on the merged default branch,
  `git grep -n -e '<stem>' | grep -v 'blob/[0-9a-f]\{40\}/'` prints
  nothing (only permalinks remain).
* `retire-plans eligible` on divergulent and kerbside lists none of
  the retired plans, and on ryll lists exactly the seventeen not in
  the batch (plus any that have newly qualified, named in the notes).
* For each repository, `gh issue list -R shakenfist/<repo> --label
  from-retired-plan --state all --json number -q length` equals the
  number of issues filed **into** it, which is not the number its own
  pull request filed: a retirement files an item's issue in the
  repository the work is in, so kerbside's pilot filed one of ryll's
  six (amended: see the execution notes).
* `retire-plans check` passes the four criteria on each merged
  default branch (amended: the 2026-11 compliance page carries the
  same four verdicts, but it is generated weekly and the one current
  here predates two of the three retirements).
* `retire-plans check` is also the branch's own verification
  (amended: this bullet asked that `eligible` report 0 already
  retired on kerbside, which was true before its retirement and is 4
  after it; `eligible` surveys `origin/<default>`, so it answers what
  is eligible in a repository and never what a branch did).
* Each skill revision is merged in llm-setup and has a `Merged:` line
  here, or the notes say that pilot needed none.
* *Phase 3 execution notes* exists with a row per repository.

#### Back brief

Before step 3a, back brief Mikal on decision 2 (a batch of six in
ryll, and which six) and decision 5 (a push audit in each pilot
pull request). Both are cheap to change before the pilots start and
expensive after: the batch is the ryll review's size, and the audits
are a sub-agent pass per repository.

Merged: divergulent `c7a980c` (#143); kerbside `d2971e6` (#686);
ryll `0c42528` (#700); llm-setup `956b2fa` (!21), `dfebae1` (!22).
The ryll skill revision is private/claude!24, still open. Phase 4's
first commit adds its merge sha here and sets phase 3 to `Complete`,
as `plan-phase-landing` has a phase closed out by the next phase.

#### Phase 3 execution notes

2026-10-31 to 2026-11-08 (AEDT). Three pilots, 13 plans retired,
23,600 deletions across the three pull requests' diffstats, and 19
`from-retired-plan` issues: 4, 10 and 5 counted by the pilot that
filed them, and 4, 9 and 6 counted by the repository they landed in,
since kerbside's pilot filed one of ryll's six. ryll's review asked
for two more, #706 and #707, which carry no label and are in neither
count. The skill
survived all three, and what it got wrong was almost never
mechanical: the helper's gaps were found by using it (a build input
it had never seen, an order computed over the wrong set), and the
skill's by nearly deleting something.

| Repository | Plans retired | Plans left, and why | Issues | Review | Skill revision |
|------------|---------------|---------------------|--------|--------|----------------|
| divergulent, #143, `c7a980c` | 3: `full-machine-run`, `faster-full-run`, `generated-marking` | none; 0 eligible after | #139-#142 | 3 rounds | !21, `956b2fa` |
| kerbside, #686, `d2971e6` | 4: `test-harness`, `rust-proxy`, `host-subject`, `kerbside-vdi-tokens` | none; 1 cross-repository row (Shaken Fist's deployer plan) stays by design | #677-#685 here, and ryll#684 | asked for nothing | !22, `dfebae1` |
| ryll, #700, `0c42528` | 6 of 23, decision 2's batch: `initial`, `pip-distribution`, `hamburger-menu`, `pr31-followup`, `deferred-debt`, `crate-extraction` | 17 eligible, for phase 4's audit issue; 12 further finished plans sit in a table with no Status column and can never be eligible | #693, #694, #697-#699 from this pilot, #706 and #707 from its review, unlabelled; ryll#684 is kerbside's | 3 rounds, 1 `fix` declined | !24, open |

Each pull request's body names the plans it left and the items it
dropped, and each commit carries its own plan's reasoning, so the
table above is an index rather than the record.

**The row says `Blocked`, not `Complete`.** Every pilot has run and
merged, so the phase is finished as work; what it waits on is !24,
the ryll skill revision, which is recorded as a plan-blocker against
this plan along with this pull request, as decision 4 requires. Phase
2 recorded the opposite mistake about itself -- a merged plan change
and an unpushed llm-setup branch, so the plan read as further along
than the fleet was for two days -- and the installed
`~/.claude/skills/retire-plans` is the !22 version until !24 lands.

**The two definition-of-done verifications the notes owe.** For each
of the 13 retired plans, on its repository's merged default branch,
`git grep -n -e '<stem>' | grep -v 'blob/[0-9a-f]\{40\}/'` prints
nothing: only permalinks remain. `retire-plans check` passes all four
audit criteria on all three merged default branches, and
`permalink-ancestry` confirms that the 4, 21 and 14 distinct commits
pinned by permalinks in each tree are ancestors of its default
branch. The 2026-11 compliance page records all three compliant on
the four criteria, but it was generated on 2026-11-01 and so predates
the kerbside and ryll retirements; `check` runs the same criteria
through the same `audit-check.py`, which is why it is quoted here
instead. On divergulent and kerbside `check` additionally reports
`retired-row-markers` and **exits 1**, until step 4d marks their
rows. A helper check has counted towards the exit status since !21
added `permalink-ancestry`, which decision 3's wording predates; it
is not an audit criterion, so it moves no compliance cell.

**Per pilot, as decision 6 asks.** What the review agreed was missing
from `docs/`, what the skill got wrong, and the change it led to:

| Pilot | What the review added | What the skill got wrong | The revision |
|-------|----------------------|--------------------------|--------------|
| divergulent | three rounds: the generated-mark boundary text tightened (`4c07190`), the autotools results file documented and its page reflowed (`d8153b7`), and the retired plan's phase numbers taken out of code (`e2d924a`) | 4 helper and 9 skill gaps. The pilot missed about 25 prose citations that name no file, and `eligible` counted any absolute link as a retired row | !21: `pointers --phases`, `permalink-ancestry`, permalink-only retired detection, and the skill's rules for prose citations, comments the plan made false, security boundaries and measurements |
| kerbside | nothing; its review asked for none, so every gap is one the pilot hit while following the skill | 10 gaps, of which **two are unrecoverable**: the pilot's scratchpad was reaped and the plan-blocker note lists only eight, so two of !22's revisions are reconstructed from where #686 departed from the skill rather than from the pilot's own list | !22: the retirement order and its cycles, entry-point plans, reasoning and issues that belong to another repository, conditional and speculative drops, and issue bodies written from the code |
| ryll | three rounds: a decision that claimed a badge the notifications work removed, the `0.0.0` dual-spec caveat, the token-scope sentence, the compression crate's doc comment, the `completed/README.md` contradiction, `OPEN-QUESTIONS.md` Q6, the release-token expiry warning, the index preamble and its `(retired)` markers, and the ungated platform decoders | 15 gaps, R1-R15, listed below | !24: `--plan` ordering, local conventions, build inputs, stem-less continuations, the row marker and preamble, and nine new cases in the skill's references |

**What the pilots nearly lost.** All four are the same shape --
reasoning whose only copy was in the plan, in a section that does not
read like a decision -- and three of the four were caught by
something other than the person doing the migration.

* Two security passages in kerbside, both in a phase's *Discoveries
  during execution*: a publishing token scoped to three crate names
  and passed by environment variable, where `docs/releasing.md`
  described a broader credential and called the narrow one a fault to
  fix. Found by decision 5's push audit, which earned its cost here.

* A back-brief correction in ryll: while a workspace member sits at
  `0.0.0`, the `version =` half of a dual dependency spec must be
  left off, because Cargo will not resolve `^0.1` against `0.0.0`.
  The plan recorded it as "the plan's recommendation was wrong on
  this point"; the docs still said the pre-correction version. Found
  by the review.

* Two dependency caveats in the same retirement, condensed past: the
  small unconditional set every consumer pays for, and the
  per-platform JPEG backends that are deliberately not feature-gated.
  Found by the review, twice, since the first narrowing was still
  incomplete.

* An inverted conclusion, the worst of the four.
  `docs/design-decisions.md` was given the three causes of a
  cross-frame corruption bug as where a hunt should start; the plan's
  own last status update is headed "three original bugs fixed,
  symptom persists" and ranks five surviving hypotheses. Reading the
  plan front to back put its superseded opening into the docs as
  current. Found by the review, and now
  `docs/plans/OPEN-QUESTIONS.md` Q6.

**The reviewer earned its rounds.** divergulent took three, ryll
three, kerbside none. Of ryll's rounds, three of the ten actionable
items were the losses above; the rest were a `completed/README.md`
that said plans "are kept (rather than deleted)", a
`docs/plans/index.md` that did not say what a pinned link means, and
prose about code the earlier rounds had added. The third round was
the stopping point: two of its five actionable items were about text
rounds one and two wrote, and its only `fix` item was a third request
for a permalink verification already performed and reported twice.
Stopping on the trajectory rather than on an empty item list is the
rule that held.

**Fifteen gaps, and where they went.** Thirteen became !24: the
helper's R1, R2, R5, R13 and R15, and the skill's R3, R4, R8, R9,
R10, R11, R12 and R14. R7 is split -- that `eligible` surveys the
default branch went into the skill, and that no brief should ask for
an `eligible` count as on-branch confirmation is below. R6, the
statusless table, is phase 4's alone. None was declined. The two most
general: a plan's dated status updates are read newest first, because
the correction is at the end and the superseded text is at the front;
and a repository's own convention for finished plans is reconciled in
the retirement, not left for the review to find.

**The skill's size, which Mikal asked 3f to settle.** SKILL.md had
reached 354 lines and was still growing a case per pilot. It is now
the procedure, and the case law lives in `references/reasoning.md`
and `references/pointers.md`, which the steps that need them say to
read. Those thirteen went in and the skill still lost 32 lines. The
rule for the next pilot: a new case goes in a reference unless it
changes the procedure.

**Bulk mode: not needed.** *Future work* asked whether ryll's
`PLAN-deferred-debt.md` pattern -- one sweep of future work across
many finished plans -- should become a mode of the skill. The only
problem it would solve is one item deferred by two plans in a run,
and !22 already handles that by triaging it once with the plan whose
docs own the area; the six-plan ryll run hit no duplicate at all. A
bulk mode would also have to give up one commit per plan, which is
what lets a reviewer read each deleted passage against its new home.
The bullet is struck from *Future work*. ryll's own sweep was a
paying-down exercise, not a retirement, and it is what made those
plans retirable.

**Corrections phase 4's brief needs.** The first two are folded into
step 4a and the last two into step 4d, which this pull request adds;
they stay here with the evidence behind them. Which rows the
criterion counts, first:

* **Count** a row with a terminal status, a local plan link, and a
  status-change clock older than the threshold. **Do not count** a
  row whose Plan link is a permalink into this repository (retired
  already), a row linking a plan elsewhere or by branch (kerbside has
  one, by design), or a row the index reader cannot parse -- those
  are reported, not counted.

* **A table with no Status column needs its own finding.** ryll holds
  twelve finished plans that way, four of them the source plans
  `PLAN-deferred-debt.md` harvested, and they can never be eligible
  because eligibility reads a column they do not have. As the
  criterion stands, a repository can keep finished plans indefinitely
  by holding them in a statusless table and still be reported clean.

* **`eligible` reads `origin/<default>`,** so it answers what is
  eligible in the repository and not what a branch has done: on a
  retirement branch it still reports every plan eligible and none
  retired. Phase 3's own definition of done asks that kerbside report
  "0 already retired", which was true before its retirement and is
  now 4. Do not write a brief that asks for an `eligible` count as
  on-branch confirmation; `check` is what reads the worktree.

* **The rules document is behind the helper** (step 4d) on three
  points, all of them things the criterion will want to assert:
  `docs/plans/order.yml` and any other build input under
  `docs/plans/`, whose entry is deleted rather than pinned; the
  `(retired)` marker and the preamble `rewrite-index` now writes; and
  that a local convention for finished plans is reconciled by the
  retirement that contradicts it.

* **An issue count is not per repository.** The definition of done
  compares each repository's `from-retired-plan` issues with what its
  pull request body claims, and ryll holds six while its own pilot
  filed five: kerbside's pilot filed ryll#684 there, as the skill
  tells it to. Phase 4's definition of done should count where an
  issue was filed from, not where it landed.

* **divergulent's three and kerbside's four retired rows predate the
  marker** (step 4d), so `retire-plans check` fails them on
  `retired-row-markers` until each index is marked and given the
  preamble. Two one-line documentation changes, and they should land
  before the criterion starts filing issues.

### Phase 4: `plan-retirement` criterion

Planning effort: high. It reaches `audit-manage-issues.py`.

Read *Corrections phase 4's brief needs* in phase 3's execution notes
before planning this phase. The two findings that change what the
criterion does are folded into 4a below, and the two documentation
ones are step 4d; the rest are about how to write the briefs, not
about the check.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Add a `PlanRetirement` class to `scripts/audit/checks/plans.py`: id `plan-retirement`, spec `docs/audits/plan-retirement.md`, `issue_title = 'Completed plans awaiting retirement'`. N/A without `docs/plans/index.md`. Read the index with `iter_markdown_table_rows` and `plan_table_columns` as `PlanIndex` does; for each row with a local link and a terminal status, date the commit where its row last changed into a terminal status (open question 2). That needs the index's history, which the audit's `--depth 1` clone lacks: phase 4 planning decides between cloning with `--filter=blob:none` (full history, blobs fetched on demand) and walking `repos/shakenfist/<repo>/commits?path=docs/plans/index.md` through `repo.github.api_answer()`, and must say what either costs across the matrix. Fail listing each plan over `RETIREMENT_AGE_DAYS = 90` with its age, as `findings`; phase files play no part in the age. GitHub not answering raises `ToolFailure` (status `error`), never `fail` or `not_applicable`. Register in `CHECKS` in `scripts/audit/registry.py` beside the plan checks; write the spec following `docs/audits/README.md`, naming the `retire-plans` skill and linking `docs/plan-retirement.md`; add its row to `docs/audits/README.md`; add `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` lines in `scripts/tests/test_metadata.py`; tests in `scripts/tests/test_plans.py` covering pass, fail, N/A, a retirement permalink row (a `blob/<40-hex>/docs/plans/` link into this repository: already retired, so not counted), a cross-repository row (any other absolute link, including `blob/<branch>`: reported, never counted as retired -- kerbside's index has one), a table with no `Status` column, which is reported in `findings` on an otherwise passing result and never fails under this check's title, since `issue_title` is frozen fleet-wide and 'Completed plans awaiting retirement' does not describe a table-shape defect -- phase 4 planning decides whether `plan-index`, which already judges the index's shape, should fail it under its own title, and the execution notes say why it matters that something does: ryll holds twelve finished plans that way and they can never be eligible, a row that went terminal long ago although its files were touched recently (eligible), a plan reopened and finished again (the clock restarts), a terminal-to-terminal change such as `Complete` to `Superseded` (the clock does not restart), an index rename, and API failure, using `FakeGitHub`. |
| 4b | medium | sonnet | none | Decide, with the pilot results, whether the rule in `docs/plan-retirement.md` should also become a shared block in every `PLAN-TEMPLATE.md` (added to `PLAN_TEMPLATE_BLOCKS`). If yes, it reaches the fleet through the issues `plan-template` files, as `plan-phase-landing` did. |
| 4c | medium | sonnet | none | Run the new check with `--dry-run` across the matrix and record here how many repositories it newly fails, including this one, before merging. |
| 4d | low | sonnet | none | Bring `docs/plan-retirement.md` up to what the helper now does, on the three points phase 3's notes list: a build input under `docs/plans/` (`order.yml`) has its entry deleted rather than pinned; `rewrite-index` marks a retired row `(retired)` and writes the index preamble, which needs reconciling with the page's "Retired is not a status"; and a repository's local convention for finished plans is reconciled by the retirement that contradicts it. Add the rule 4a asserts about a table with no `Status` column, which the page does not state today. Keep the page free of phase numbers, as phase 1's definition of done greps for. Then mark the already-retired rows in divergulent's and kerbside's indexes and add the preamble to each, one small pull request per repository, each running that repository's push audit as decision 5 requires and recorded in phase 4's `Merged` cell; the marker goes in the `Plan` cell, after the link, which is where `rewrite-index` puts it, although `check` accepts it anywhere on the row. Those two are why `retire-plans check` exits 1 on divergulent and kerbside today. Land before 4c's dry run, so the criterion is measured against a fleet the rule describes. |

### Phase 5: push audit

Runs `PUSH-AUDIT.md` over every range recorded above, in this
repository and `llm-setup`. The pilot pull requests in divergulent,
kerbside and ryll each ran their own repository's `PUSH-AUDIT.md`
before landing (phase 3, decision 5); cite those rather than
re-running them. In addition to the runbook: confirm, by reading
the pilot pull requests, that no retirement deleted reasoning that
`AGENTS.md` or another plan still pointed at.

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

**In this repository.** High effort is anything that changes what
a criterion means, anything that touches the scheduler in
`audit-check.py`, and anything that reaches
`audit-manage-issues.py` -- those decide what the fleet is held
to and what lands in other people's issue trackers. Medium effort
covers adding a criterion that follows the shape of an existing
one, a documentation sweep, or a template change with a worked
example already in the tree.

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

**In this repository.** A worked brief: instead of "add a check
that plans are indexed", write "add a `PlanIndex` class to
`scripts/audit/checks/plans.py` declaring the id `plan-index`, its
`spec` and its `issue_title` as class attributes and implementing
`run(repo)`, register the instance in `CHECKS` in
`scripts/audit/registry.py` beside the other plan checks, write
`docs/audits/plan-index.md` following the structure in
`docs/audits/README.md` and linking to `compliance.md#plan-index`,
add the file to `docs/audits/README.md`, add its lines to
`FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
`scripts/tests/test_metadata.py`, and add tests to
`scripts/tests/test_plans.py` covering pass, fail and
not-applicable." A brief that names four of the criterion's five
files is the characteristic defect here, and the frozen tables are
the one most often left out.

A check that does not apply reports
`not_applicable` with a reason rather than being omitted, because
an omitted check renders as `unknown`.

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

**In this repository.** The project-specific checks referred to
above are:

- [ ] `pre-commit run --all-files` passes. It runs actionlint,
      shellcheck, flake8, skillsaw and all five test suites, and
      `ci.yml` runs the same command on every pull request.
- [ ] `python3 scripts/audit-check.py --repo-path . --repo-name
      development` still reports what it reported before the
      change, or the plan says why the verdict moved.
- [ ] If the change touches issue filing, it was exercised with
      `--dry-run` only.

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

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `pre-commit run --all-files` passes.
* `scripts/audit-check.py` run against this repository reports no
  new failures, or the plan states which verdicts moved and why.
* Any new or changed criterion has all five of its files in step:
  the `Check` subclass in `scripts/audit/checks/<family>.py`, its
  registration in `CHECKS` in `scripts/audit/registry.py`, the
  specification under `docs/audits/`, its row in
  `docs/audits/README.md`, and its frozen lines in
  `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
  `scripts/tests/test_metadata.py` -- plus a
  `FROZEN_COLUMN_NAMES` line where a spec page carries more than
  one check. `AUDIT_METADATA`, `ISSUE_TITLES` and `COLUMN_NAMES`
  are derived from the registry rather than tables anybody edits;
  the issue title is the fleet-wide idempotency key, so renaming
  one orphans every open issue for that check across the fleet.
* No `consistency-audit` marker block has been added to a
  criterion specification by hand, and the compliance tables in
  `docs/audits/compliance.md` have not been hand-edited.
* Any entry added to `REPO_OVERRIDES` carries a stated reason.
* Anything under `templates/` is judged as the code it will
  become in ten other repositories: placeholders consistent, no
  reference to paths that only exist here, and the README beside
  it saying what to substitute.
* Python is wrapped at 120 characters, single quotes for strings
  and double quotes for docstrings, and no script has grown a
  dependency outside the standard library.
* Documentation in `docs/` describes any user-visible change.
  `AGENTS.md` changes only if a convention changed;
  `ARCHITECTURE.md` only if the shape of the system changed;
  `README.md` only if the pitch, the install story or the
  documentation links changed.

### Documentation index maintenance

This plan's row in `docs/plans/index.md` was added with the plan.
Update its status as phases land.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

* A `docs/plans/` size or count signal in the audit, if retirement
  turns out not to be enough on its own.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

...

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
