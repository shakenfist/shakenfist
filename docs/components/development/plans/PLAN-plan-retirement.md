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
2026-10-28 (the age is the last commit of `docs/plans/index.md` in
which the plan's row changed into a terminal status from one that was
not; the instar clone was from 2026-08-17 and its index still held
prose statuses, so its row is a file count only. By 2026-11-09
instar's index carries a `Status` column, and it has 1 plan eligible
and 29 terminal inside the window):

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
| 3. Pilot | Complete | 623d72b (#364), ea741dd (#378); divergulent c7a980c (#143), kerbside d2971e6 (#686), ryll 0c42528 (#700); llm-setup 956b2fa (!21), dfebae1 (!22), c8a79de (!24) |
| 4. `plan-retirement` criterion | Complete | 4f7f587 (#386); divergulent 04a4eb1 (#152), kerbside 8330c3f (#712) |
| 5. Push audit | Complete | |

The criterion lands after the pilot on purpose. It files issues the
day it merges, in every repository with an eligible plan -- five of
the 24, by phase 4's survey, naming 34 plans -- and an issue pointing
at a skill nobody has run is an issue nobody can act on.

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
  the retired plans, and on ryll lists the seventeen not in the
  batch, plus `PLAN-two-stage-ci.md`, which crossed 90 days on
  2026-11-08: eighteen.
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
ryll `0c42528` (#700); llm-setup `956b2fa` (!21), `dfebae1` (!22),
`c8a79de` (!24). The ryll skill revision merged on 2026-11-08, which
released the hold this phase was held open under; phase 4's first
commit set the status to `Complete`, as `plan-phase-landing` has a
phase closed out by the next phase.

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
| ryll, #700, `0c42528` | 6 of 23, decision 2's batch: `initial`, `pip-distribution`, `hamburger-menu`, `pr31-followup`, `deferred-debt`, `crate-extraction` | 17 eligible when #700 merged, and 18 from 2026-11-08, when `PLAN-two-stage-ci.md` crossed 90 days; all of them for phase 4's audit issue. 12 further finished plans sit in a table with no Status column and can never be eligible | #693, #694, #697-#699 from this pilot, #706 and #707 from its review, unlabelled; ryll#684 is kerbside's | 3 rounds, 1 `fix` declined | !24, `c8a79de` |

Each pull request's body names the plans it left and the items it
dropped, and each commit carries its own plan's reasoning, so the
table above is an index rather than the record.

**The row was held at `Blocked` until !24 merged.** Every pilot had
run and merged, so the phase was finished as work; what it waited on
was the ryll skill revision, recorded as a plan-blocker against this
plan along with this pull request, as decision 4 requires. !24 merged
as `c8a79de` on 2026-11-08, nine minutes after #378, and phase 4's
first commit set the row to `Complete`. Phase 2 recorded the opposite
mistake about itself -- a merged plan change and an unpushed
llm-setup branch, so the plan read as further along than the fleet
was for two days. The installed `~/.claude/skills/retire-plans` is
still the !22 version: the llm-setup checkout it resolves into
carries an unrelated local commit and cannot fast-forward, so a
retirement run on this host should check that
`references/reasoning.md` exists before trusting the skill to be
current.

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
  Phase 4's survey found a second and larger instance, private-ci's
  twenty-four, where the absent column is a written decision rather
  than an omission; phase 4 decision 3 reports both and fails
  neither.

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
before starting. The two findings that change what the criterion does
are folded into step 4a and into decisions 2 and 3 below; the two
documentation ones are step 4d.

#### Scope

In: a `plan-retirement` criterion in this repository's audit, with
its specification page and its row in the criteria index; the change
to the audit's clone that gives the criterion the index's history;
the four rules `docs/plan-retirement.md` is behind the helper on; and
marking the already-retired rows in divergulent's and kerbside's
indexes.

Out: retiring any plan -- the criterion files an issue and the
`retire-plans` skill acts on it; ryll's eighteen and shakenfist's
thirteen eligible plans, which those issues are what tracks; the push
audit (phase 5); and any change to the helper or the skill in
llm-setup, which phase 3 finished.

#### What the survey found

Checked on 2026-11-09 against development `ea741dd`, llm-setup
`c8a79de`, and each matrix repository's freshly fetched
`origin/<default>`.

Every premise the phase's original text stated about this
repository's code holds. `scripts/audit/checks/plans.py` has
`PlanIndex` (line 1295), `plan_table_columns` (line 378) and
`PLAN_TERMINAL_STATUSES` (line 405); `iter_markdown_table_rows` is at
`scripts/audit/text/markdown.py:127`; `ToolFailure` at
`scripts/audit/check.py:37`; `api_answer` and `FakeGitHub` at
`scripts/audit/github.py:111` and `:129`; `PLAN_TEMPLATE_BLOCKS` at
`plans.py:1120`; `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` at
`scripts/tests/test_metadata.py:59` and `:328`, holding 68 titles for
68 registered criteria; `docs/audits/plan-retirement.md` does not
exist; and the audit still clones each target with `--depth 1`
(`.github/workflows/consistency-audit.yml:126`). Seven things it did
not say:

1. **What the criterion will do on the day it merges, measured.** The
   helper at `c8a79de` reads the same index and applies the same
   clock the criterion will, so `eligible` over all 24 matrix
   repositories is step 4c's dry run a phase early:

   | Verdict | Repositories | Plans |
   |---------|--------------|------:|
   | fail | instar 1, kerbside 1, ryll 18, shakenfist 13,
   uncalibrated-sextant 1 | 34 |
   | pass | actions, andris, client-python, client-python-k3s,
   client-python-ova, development, divergulent, images,
   kerbside-patches, library-utilities, occystrap, private-ci, sfui |
   0 |
   | not applicable | agent-python, clingwrap, cloudgood, hunkydory,
   kerbside-client, visual-digest-rust: no `docs/plans/index.md` | --
   |

   Five repositories, not the Execution preamble's "at least four",
   and 34 plans. development has 0 eligible and 16 terminal inside
   the window, so it will not fail its own criterion on day one; the
   first of its own plans qualifies on 2026-11-20, as phase 3's scope
   said.

2. **An API route would cost the check the audit's own regression
   net.** `audit_snapshot.py` compares two runs' details strings byte
   for byte, because those strings reach `docs/audits/compliance.md`
   and the bodies of issues filed in 24 repositories. Checks that
   reach the network are excused from that comparison, and
   `NETWORK_CHECKS` is not a list a check can opt out of by hand:
   `NetworkCheckListTest` re-derives it by scanning each check's
   source for `repo.github`, `_github(`, `GhCli(` or `'clone'`
   (`scripts/tests/test_audit_snapshot.py:248`) and fails if the two
   disagree. So reading the index's history through `api_answer()`
   does not merely cost requests -- it moves `plan-retirement` out of
   the net that protects the one thing it publishes. Reading it from
   the clone does not: `git` run against `repo.path` matches none of
   those patterns, which is why `packaging.py`'s `git check-ignore`
   check is not in `NETWORK_CHECKS`.

3. **What each history route costs, measured on the two worst
   repositories.** `docs/plans/index.md` has 1,202 revisions across
   the 18 matrix repositories that have one; shakenfist alone has
   356, instar 204 and kerbside 193.

   | Route | shakenfist | ryll |
   |-------|------------|------|
   | `--depth 1`, today | 2.8s, 59MB, 1 revision visible | 1.5s,
   14MB, 1 revision |
   | `--filter=blob:none` | 13.1s, 83MB | 2.2s, 17MB |
   | ... then reading every index revision | +179.1s (356 revisions)
   | +58.4s (117) |
   | ... bounded at the threshold (decision 2) | +~120s (242) | +~26s
   (51) |
   | full clone | 68.8s, 1,256MB, then 0.1s to walk | not measured |
   | `commits?path=` through `api_answer()` | ~360 requests | ~121
   requests |

   The promisor fetch is one round trip per revision, about 0.5s, and
   nothing batches it: piping all 356 `<sha>:docs/plans/index.md`
   revisions through a single `git cat-file --batch` took 174.5s
   against 179.1s for 356 separate `git show` calls (git 2.47.3). The
   blob:none clone costs no other check anything, because `clone`
   checks out HEAD and fetches its blobs in that one request: in the
   blob:none clone of shakenfist all 1,908 tracked files are present,
   and `git grep` across them with `GIT_NO_LAZY_FETCH=1` succeeds, so
   no check but this one can reach the network through the clone.

4. **A statusless table is not one repository's omission, and the
   larger one is deliberate.** Phase 3's correction named ryll's
   twelve. private-ci has twenty-five across two tables, the first at
   `docs/plans/index.md:19`, with four lines of prose above them
   saying why: "a status here would be inferred rather than recorded
   -- and an inferred status in the one page people read to decide
   what wants attention is worse than none. The column goes in as
   those plans are revisited and their real state confirmed." So the
   fleet has 37 finished-looking plans that eligibility cannot see,
   in three tables, one an omission and two a documented choice.
   `plan-index` passes both: it selects tables by their lead columns
   (`PLAN_INDEX_LEAD_COLUMNS`), and a `Date | Plan | Intent` table is
   a legal one it simply has no status to judge.

5. **The `(retired)` marker does not gate detection**, so step 4d is
   independent of step 4a rather than a precondition for it. On
   divergulent, `eligible` reports "0 eligible; 3 already retired"
   while `check` fails `retired-row-markers` on the same three rows:
   a row is recognised as retired by its permalink, and the marker is
   for the human reading the page. 4d still lands before 4c, because
   4c is the record of the finished state, but 4a does not wait on
   it.

6. **Step 4c's command does not exist as written.** `audit-check.py`
   takes `--repo-path` and `--repo-name` and has no `--dry-run`;
   `--dry-run` belongs to `audit-manage-issues.py --results-dir`,
   which decides what issues to file from the JSON `audit-check.py`
   prints. The dry run is therefore two commands, and
   `tools/audit-snapshot.sh <clones-dir> <out-dir>` is the first of
   them over a whole directory of clones.

7. **Two Situation claims have gone stale.** instar's index now
   carries a `Status` column, so its row is no longer a file count
   only: it has 1 eligible plan and 29 terminal inside the window.
   And `eligible` refuses a clone whose `origin/HEAD` is unset, which
   was eight of the 24 local clones; `gh repo clone` sets it, so this
   reaches a person running the skill locally and never the
   criterion. Both corrected below; the second is recorded as *Future
   work* rather than fixed here, since the helper is llm-setup's.

Corrected at source: the Execution preamble's "at least four"; the
Situation note on instar's prose statuses; phase 3's per-repository
notes row and definition-of-done bullet, which said ryll has
seventeen eligible plans left (`PLAN-two-stage-ci.md` qualified on
2026-11-08, making eighteen); and phase 3's statusless- table
correction, which named only ryll's twelve.

#### Decisions

1. **The index's history comes from a `--filter=blob:none` clone, not
   from the API.** Finding 2 is why, and finding 3 is the price: one
   line in `consistency-audit.yml`, about ten seconds and 24MB more
   on the worst repository, and nothing at all for the other 67
   criteria. The API route's ~1,250 requests across the matrix would
   be survivable on their own; losing the byte-for-byte snapshot
   comparison on a check whose details become issue bodies in 24
   repositories is not, and no amount of request budget buys it back.
   This is the decision that is cheap now and expensive later,
   because it changes the clone step every matrix leg shares.

2. **The history walk stops at the threshold, not at the first
   revision.** A row that is terminal in the oldest revision examined
   and terminal now has been terminal at least that long, so a row
   still unresolved at a revision older than `RETIREMENT_AGE_DAYS` is
   eligible without an exact date. The finding reports an exact age
   where the transition was found and a lower bound ("at least N
   days") where it was not, and never an unlabelled number. This
   makes the cost proportional to how much the index churned inside
   the window rather than to the repository's age: 242 revisions
   instead of 356 on shakenfist, 51 instead of 117 on ryll, 11
   instead of 77 on divergulent.

3. **A table with no `Status` column is reported and never failed,
   and nothing else fails it either.** Step 4a already said the
   criterion reports it; the open question it left to this phase was
   whether `plan-index` should fail it under its own title. The
   answer is no. Finding 4 is why: the larger of the two instances is
   a written decision not to record a status that would be invented,
   which is a better answer than an invented status, and a criterion
   cannot tell that apart from an omission. What the two cases have
   in common is not a defect but invisibility -- 36 plans that
   eligibility cannot see -- and a `findings` entry in every audit
   run, on a result that is not a `fail`, is what fixes invisibility.
   `docs/plan-retirement.md` gains the rule (step 4d) so the
   behaviour is written down rather than implied by a check. This is
   the decision most likely to be argued with: it leaves a repository
   able to hold finished plans out of retirement indefinitely by not
   giving them a status. The reason to take it anyway is that the
   alternative is an audit demanding a status somebody will make up
   to clear an issue, and open question 3 already settled that an
   opt-out is acceptable when it is written down and visible, which
   this is.

4. **There is no step 4b: the rule does not become a
   `PLAN-TEMPLATE.md` shared block.** The original step asked whether
   it should. It should not. A shared block changes how a plan is
   *written*, which is what `plan-phase-landing` and
   `plan-closeout-sections` do; retirement happens ninety days after
   a plan's last phase lands, when nobody is reading the template.
   Copying the rule into 24 templates would put it where it is never
   read at the moment it applies, and the criterion's issue arrives
   in the repository at exactly that moment instead. The step is
   dropped rather than renumbered, because phase 3's execution notes
   and this plan's corrections section both cite "step 4a" and "step
   4d" by letter.

5. **The criterion's own repository is not exempt.** It is registered
   for development like any other, and development passes it today
   with 0 eligible and 16 terminal inside the window (finding 1). The
   first plan here qualifies on 2026-11-20, which is after this phase
   lands, so the criterion files its first issue against this
   repository without anybody having to decide to let it.

#### Steps

The letters are not alphabetical, and 4b is absent: both are decision
4. Execution order is the table's order.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Add a `PlanRetirement` class to `scripts/audit/checks/plans.py`: id `plan-retirement`, spec `docs/audits/plan-retirement.md`, `issue_title = 'Completed plans awaiting retirement'`. N/A without `docs/plans/index.md`. Read the index with `iter_markdown_table_rows` and `plan_table_columns` as `PlanIndex` does, and reuse `PLAN_TERMINAL_STATUSES` (`plans.py:405`). For each row with a local plan link and a terminal status, date the commit where that row last changed into a terminal status from one that was not (open question 2), by walking `docs/plans/index.md` newest-first in the clone and stopping at the first revision older than `RETIREMENT_AGE_DAYS = 90` (decision 2): a row still terminal there is eligible with a lower-bound age. The history comes from the clone, not from GitHub (decision 1), so this step also changes `.github/workflows/consistency-audit.yml:126` from `--depth 1` to `--filter=blob:none` and says in a comment why. Do not touch `repo.github` or run a `clone` from inside the check: that would put `plan-retirement` in `NETWORK_CHECKS` and out of the snapshot net (finding 2), and `NetworkCheckListTest` enforces the list either way. Fail listing each plan over the threshold with its age, as `findings`; phase files play no part in the age. git failing to answer raises `ToolFailure` (status `error`), never `fail` or `not_applicable`. **Do not count**: a row whose plan link is a permalink into this repository (`blob/<40-hex>/docs/plans/`: retired already), a row linking a plan elsewhere or by branch (any other absolute link -- kerbside's index has one, by design), or a row the index reader cannot parse; report each of those instead. A table with no `Status` column is reported in `findings` on an otherwise passing result and never fails (decision 3). Register in `CHECKS` in `scripts/audit/registry.py` beside the plan checks; write the spec following `docs/audits/README.md`, naming the `retire-plans` skill and linking `docs/plan-retirement.md`; add its row to `docs/audits/README.md`; add `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` lines in `scripts/tests/test_metadata.py`. Tests in `scripts/tests/test_plans.py` over a synthetic index history, not a single mocked diff, covering: pass, fail, N/A, a retirement permalink row, a cross-repository row, a statusless table, a row that went terminal long ago although its files were touched recently (eligible), a plan reopened and finished again (the clock restarts), a terminal-to-terminal change such as `Complete` to `Superseded` (the clock does not restart), a row resolved only at the threshold boundary (lower-bound age), an index rename, and git failing. Prove them with the `mutation-check` skill: inverting the clock comparison, dropping the terminal-to-terminal guard and dropping the permalink exclusion must each fail a named test. |
| 4d | low | sonnet | none | Bring `docs/plan-retirement.md` up to what the helper now does, on the three points phase 3's notes list: a build input under `docs/plans/` (`order.yml`) has its entry deleted rather than pinned; `rewrite-index` marks a retired row `(retired)` and writes the index preamble, which needs reconciling with the page's "Retired is not a status" -- the marker sits in the `Plan` cell and is not a status, which is the reconciliation; and a repository's local convention for finished plans is reconciled by the retirement that contradicts it. Add decision 3's rule, which the page does not state today: a table with no `Status` column makes its plans ineligible, the audit reports them rather than failing, and a repository keeping one should say why, as private-ci does. Keep the page free of phase numbers, as phase 1's definition of done greps for. Then mark the already-retired rows in divergulent's and kerbside's indexes and add the preamble to each, one small pull request per repository, each running that repository's push audit as phase 3 decision 5 requires, and recorded in phase 4's `Merged` cell; `rewrite-index` puts the marker in the `Plan` cell after the link, although `check` accepts it anywhere on the row. Those two are why `retire-plans check` exits 1 on divergulent and kerbside today. This does not change what 4a detects (finding 5), so the two may run in either order; both land before 4c. |
| 4c | medium | sonnet | none | The dry run, in two commands, not one (finding 6): `tools/audit-snapshot.sh <clones-dir> <out-dir>` over fresh clones of all 24 matrix repositories, then `scripts/audit-manage-issues.py --results-dir <out-dir> --dry-run` to see the issues it would file. Record here the verdict for every repository, the plans each fail names, and the wall-clock time the new check spent per repository, and compare the verdicts against this phase's survey table and against `retire-plans eligible` on the same clones: a disagreement is a defect in the check or in the helper, and the notes say which. Also capture a snapshot before and after 4a's clone change and diff them with `tools/audit-snapshot.sh --diff`: nothing but `plan-retirement` may move. Land before merging. |

#### Risks and mitigations

* **The clone change breaks or slows a criterion that relied on
  shallowness.** 67 other checks share that clone. Mitigated by the
  audit's own regression net, which is what 4c's before-and-after
  snapshot diff is for, and by the measurement behind decision 1: a
  blob:none clone holds every blob at HEAD, so no other check can
  reach the network through it. The failure mode to watch for is the
  opposite of slow -- a check that silently starts reading history
  because it now can.

* **The walk costs more than the measurement suggests, because an
  index churns more than shakenfist's.** The cost is revisions inside
  the 90-day window, which is a number nobody controls. Mitigated by
  4c recording per-repository time, by the 30-minute leg timeout
  being the backstop, and by the walk being bounded at the threshold
  rather than at the repository's first commit (decision 2). If a leg
  does approach the timeout, the fix is a smaller window, not a
  deeper clone.

* **34 plans' worth of retirement arrives in five repositories at
  once.** Mitigated by what the pilot measured: six plans is one pull
  request a person can read deletion by deletion, so shakenfist's
  thirteen are three and ryll's eighteen are four, and the helper
  prints the retirement order that chooses the batches. The threshold
  is a constant and open question 5 revisits it after the first
  fleet-wide round; the issue is a backlog, not a deadline, and its
  body should say so.

* **The clock is subtly wrong on a shape the fixtures do not have.**
  1,202 real index revisions exist across the matrix, and the clock
  rule has four edge cases (reopened, terminal-to-terminal,
  boundary-resolved, renamed index). Mitigated by 4a's synthetic
  histories, by the mutation check it names, and by 4c cross-checking
  every verdict against the helper, which reached the same answers by
  different code.

* **A finding names a plan whose reasoning has nowhere to go.** Phase
  3 exercised the skill's refusal path, so this is expected rather
  than a defect; the criterion's issue stays open and the pull
  request that retires the rest names the plan it left. The risk is
  only that an issue nobody can fully close reads as a failure of the
  criterion; the spec page should say that a partially retired
  repository keeps the issue open on purpose.

#### Definition of done

* `plan-retirement` is in `CHECKS`, in `FROZEN_METADATA` and in
  `FROZEN_ISSUE_TITLES`, `docs/audits/plan-retirement.md` exists, and
  `docs/audits/README.md` has its row.

* `plan-retirement` is **not** in `NETWORK_CHECKS`, and
  `NetworkCheckListTest` passes: the criterion reads the index's
  history from the clone and never from GitHub.

* `tools/audit-snapshot.sh --diff` between snapshots captured before
  and after the clone change reports differences in
  `plan-retirement`'s rows and in nothing else.

* Over all 24 matrix repositories the criterion fails exactly
  instar, kerbside, ryll, shakenfist and uncalibrated-sextant,
  reports `not_applicable` for exactly agent-python, clingwrap,
  cloudgood, hunkydory, images, kerbside-client and
  visual-digest-rust, and passes the other twelve. images is not
  applicable because `only_checks` scopes it to `eol-distro` alone,
  which is a different reason from the other six and the reason its
  verdict is a sentence about scope rather than about plans. Any
  difference from this phase's survey table is explained in the
  execution notes, naming the commit that caused it.

* The fail details name 34 plans in total, and every age is either an
  exact day count or a stated lower bound.

* ryll's twelve-plan table and private-ci's two tables of
  twenty-five plans between them all appear in `findings` and in
  `details`, on results that are not `fail` for that reason.
  private-ci is measured at all only because this phase added
  `plan-retirement` to its `only_checks`.

* `$HOME/bin/retire-plans check` exits 0 on divergulent and kerbside.

* `docs/plan-retirement.md` states all four rules from step 4d, and
  `grep -niE 'phase [0-9]' docs/plan-retirement.md` prints nothing.

* `pre-commit run --all-files` passes, and the three mutations step
  4a names each fail a named test.

* The execution notes record the new check's wall-clock cost per
  repository from 4c, and no matrix leg exceeds its 30-minute
  timeout.

#### Back brief

Before step 4a, back brief Mikal on decision 1 (the audit clones with
`--filter=blob:none` instead of `--depth 1`, which every matrix leg
shares) and decision 3 (a statusless table is reported and never
failed, by this criterion or by `plan-index`). The first is one line
to write and expensive to reverse once 67 criteria have been
snapshotted against it; the second decides whether 37 finished plans
in two repositories can stay out of retirement indefinitely.
#### Phase 4 execution notes

Back brief answered on 2026-11-11, all three gates as planned:
decision 1 stands, so the audit clones with `--filter=blob:none`;
decision 3 stands, so a statusless table is reported and never
failed, by this criterion or by `plan-index`; and step 4d's two
index-marking pull requests are prepared and held rather than
pushed, which leaves the definition of done's `retire-plans check`
item verified in their branches and not yet on their default
branches.

The steps ran in the table's order, one sub-agent each: 4a at opus
and high effort, 4d at sonnet and low, 4c at sonnet and medium. 4a
and 4d are independent, as finding 5 said, but they share a worktree
and both run `pre-commit`, so they ran in sequence rather than
together.

Corrected at source while executing, each in the commit that found
it:

* `run_tool` is in `scripts/audit/check.py`, not in
  `scripts/audit/files.py` as the survey said.
* `findings` is read only when an issue is filed, so a report on a
  passing result reaches nobody through it. Decision 3's statusless
  tables, and the other kinds of row reported rather than counted,
  are therefore in the `details` string as well: `details` is the
  only field published on a result that is not a `fail`, and the
  only one `audit_snapshot.py` compares.
* Every eligible row's age is a lower bound, not sometimes an exact
  count. Because the walk stops where eligibility starts, an exact
  date only ever dates a row too recent to be eligible, so decision
  2's "exact age where the transition was found" describes the
  ineligible half. For an index that churns, every eligible row
  reads "at least 90 days", which is true and uninformative; a
  reader who wants the real age runs the helper, which walks the
  whole history.
* `private-ci` was scoped out of the criterion altogether by
  `only_checks`, so the definition of done's statusless-table item
  could not have been met: the criterion would never have run there.
  It is in scope as of this phase, and it holds 25 plans in two
  tables with no `Status` column rather than the twenty-four in one
  the survey recorded, which makes the fleet's invisible total 37.
* `images` is scoped to `eol-distro` alone, so the criterion reports
  `not_applicable` there, with the scope as its reason rather than
  an absent index. That is a seventh not-applicable repository and a
  twelfth passing one, and the definition of done's lists are
  corrected to match. The scope decision is deliberate and is not
  reopened here: `images` is excluded from the conventions so that
  the audit does not file work outward at a repository that did not
  ask for them.
* kerbside's index carries four unmarked retired rows, not three,
  and both pilot repositories' default branch is `develop`.
* The installed `retire-plans` skill is now the merged `c8a79de`
  version -- `references/reasoning.md` is present and the helper is
  1,296 lines -- so the stale-install warning recorded when phase 3
  was closed out no longer applies.
* `tools/audit-snapshot.sh` does not clone. It reads a directory of
  clones, so the dry run makes its own; a snapshot taken over
  shallow clones reports this criterion as `error` everywhere, which
  is the before half of its own regression check.
* `FixtureRepo` already had `init_git()` and `commit()`. 4a added
  `commit_files()` beside them rather than adding git support.

The criterion and the helper disagree about one row on purpose: a
terminal row whose plan file has already been deleted without its
link becoming a permalink is counted here and reported as unreadable
by the helper. The row is what retirement has to turn into a
permalink, and the helper has no reasoning left to move. No clone on
this host has such a row, so the dry run cannot see the difference;
`docs/audits/plan-retirement.md` records it so that a later reader
does not take it for a defect.

The two index-marking changes each had their repository's
`PUSH-AUDIT.md` applied as a read of the diff rather than a full
walk of its waves, because both are a one-cell edit to a markdown
table with no code, dependency, test or security surface. Neither
found anything. Phase 3 decision 5 asks what each push audit found,
and that is the answer; the pull request bodies say the same.

The dry run, on 2026-11-11, over fresh clones of all 24 matrix
repositories, in the two shapes the clone change is between:

| Verdict | Repositories | Plans |
|---------|--------------|------:|
| fail | instar 1, kerbside 1, ryll 18, shakenfist 13,
uncalibrated-sextant 1 | 34 |
| pass | actions, andris, client-python, client-python-k3s,
client-python-ova, development, divergulent, kerbside-patches,
library-utilities, occystrap, private-ci, sfui | 0 |
| not applicable | agent-python, clingwrap, cloudgood, hunkydory,
kerbside-client, visual-digest-rust: no index. images: scoped to
`eol-distro` | -- |

`tools/audit-snapshot.sh --diff` between the two snapshots reported
fourteen differences, every one of them `plan-retirement`: nine
`error -> pass` and five `error -> fail`, the `error` side being the
shallow-checkout refusal. No other criterion moved, which is the
whole of what decision 1's risk turned on.
`audit-manage-issues.py --dry-run` would create five issues, one per
failing repository, and close none.

Wall-clock cost of the criterion per repository, measured on cold
clones: shakenfist 120.0s, kerbside 73.6s, development 61.7s, instar
46.4s, ryll 21.3s, client-python-k3s 13.1s, kerbside-patches 6.5s,
divergulent 5.2s, client-python 3.0s, uncalibrated-sextant 1.0s,
occystrap 0.6s, client-python-ova 0.5s, and under a tenth of a second
for the rest; 355s across the matrix. The slowest leg is a fortieth
of its thirty-minute timeout. The first measurement was taken on the
clones the snapshot run had already used and read 0.0 to 0.8s
throughout, because the promisor fetches were already cached -- a
cost measured after paying it is not the cost, and the figures above
are from a third, untouched clone set.

The criterion and `retire-plans eligible` name the same plans in
every repository, and agree about the retired and cross-repository
rows as well. Where they differ is the age: the helper walks the
whole history and prints 213 days for ryll's oldest, where the
criterion stops at the threshold and prints "at least 90". Nothing
newly qualified in the two days since the survey; the two plans that
crossed 90 days on 2026-11-08, kerbside's `PLAN-two-tier-ci.md` and
ryll's `PLAN-two-stage-ci.md`, were already in it.
### Phase 5: push audit

Planning effort: medium, as the master plan set it. Review effort:
medium -- the phase changes no code, and its one real risk is an
audit that reads the wrong range and so reports a clean result over
an empty diff. Planned on 2026-11-12 (Australian Eastern Daylight
Time) against `3c73bc4`, on the branch
`plan-retirement-phase-05-push-audit`. This section replaces the
sketch the master plan carried for phase 5, two of whose three
sentences the survey found false.

#### Scope

In: running `PUSH-AUDIT.md` over the five landings this plan made in
this repository; auditing llm-setup's five landings against the
floor the `plan-push-audit-phase` block defines for a repository
with no runbook; the extra check phase 3 decision 5 reserved for
this phase, that no retirement deleted reasoning something still
pointed at; citing the three pilot audits and the two marker reads
rather than re-running them; the record of what the audit found; and
this phase's own close-out.

Out: re-running the three pilot repositories' runbooks, which phase 3
decision 5 and the block both forbid -- a runbook is written for one
repository's blast radius, and each pilot ran its own before landing;
fixing what the audit finds, which lands as its own pull request
after this one merges; the two Future work deferrals, which are
recorded deferrals rather than findings; the fleet's use of the
private GitLab hostname in public repositories, which survey item 7
measures and which predates this plan in five other repositories; and
the six fixture plan references survey item 11 found, which decision
9 leaves to the follow-up pull request.

#### What the survey found

Read on 2026-11-12 AEDT against `3c73bc4`, the llm-setup clone at
`6e60cfc`, and the three pilot repositories' default branches. The
sketch for this phase was three sentences and two of them were
wrong; both are corrected at source rather than only here.

1. **llm-setup has no `PUSH-AUDIT.md`,** so the sketch's "runs
   `PUSH-AUDIT.md` over every range recorded above, in this
   repository and `llm-setup`" cannot be done as written. Its root
   holds `AGENTS.md`, `ARCHITECTURE.md` and `README.md` and no
   runbook. The `plan-push-audit-phase` block
   (`templates/shared-blocks/plan-push-audit-phase.md`, v4) covers
   this case directly: such a repository still carries the phase,
   the phase says the runbook does not exist and what was done
   instead, and the floor is the block's own five-item checklist.
   The same passage forbids the obvious shortcut -- "Do not borrow
   another repository's runbook instead: a runbook is written for
   one repository's blast radius." Decision 2 follows it.

2. **"Every range recorded above" did not cover this repository's
   phase 3 work.** The Execution table's phase 3 cell recorded the
   three pilot pull requests and the three llm-setup merge
   requests, and none of this repository's own landings: `623d72b`
   (#364, phase 2's close-out and the phase 3 plan) and `ea741dd`
   (#378, phase 3's execution notes). Both are first-parent merges
   on `main` and both touch the plan file, so an audit taking the
   table literally would have read three of this repository's five
   landings. Corrected at source in this phase's close-out commit.

3. **Three landings arrived after the sketch was written.** Phase 4
   merged as `4f7f587` (#386) and its step 4d landed in two other
   repositories: divergulent `04a4eb1` (#152), eight insertions to
   one markdown table, and kerbside `8330c3f` (#712), nine. Phase
   4's execution notes record that each had its own repository's
   `PUSH-AUDIT.md` applied as a read of the diff rather than a walk
   of its waves, because neither touches code, dependencies, tests
   or a security surface, and that neither found anything. That is
   the citation the block asks for, and phase 4's `Merged` cell now
   carries all three.

4. **The three pilot audits are citable, as decision 5 required.**
   Each pilot repository has its own `PUSH-AUDIT.md`, and each pull
   request body records what its audit found: divergulent#143 ran
   the runbook in full, wave 1 clean, one blocking item fixed (two
   stale step citations in a tool README) and two security
   boundaries restored that had survived only in deleted text;
   kerbside#686 found one documentation error, now fixed, and no
   lost security constraint; ryll#700 ran wave 1 green and all four
   wave 2 agents, and the push audit changed the author's mind
   about one judgement. Nothing here needs re-running.

5. **A net-effect diff is only honest over twelve of the twenty
   paths.** The block wants the accumulated diff of every phase,
   not the last phase alone, but this plan's window on `main` is
   two weeks long and some of the files it touches are the fleet's
   busiest. Of the twenty paths the five landings touch, twelve
   have no unrelated first-parent commit in `f7b1219^1..3c73bc4`
   and eight do:

   | Path | Unrelated commits |
   |------|------------------:|
   | `docs/plans/index.md` | 8 |
   | `scripts/tests/test_metadata.py` | 2 |
   | `scripts/audit_snapshot.py` | 2 |
   | `scripts/audit/registry.py` | 2 |
   | `docs/consistency-audits.md` | 2 |
   | `docs/audits/README.md` | 2 |
   | `scripts/tests/test_registry.py` | 1 |
   | `README.md` | 1 |

   Decision 1 restricts the net-effect read to the clean twelve and
   names the eight, so that an agent reading a shared file does not
   attribute someone else's commit to this plan.

6. **Phase 4's definition of done holds on `main`,** including the
   one item its notes left verified only in branches.
   `plan-retirement` is registered (`scripts/audit/registry.py:52`)
   and frozen (`scripts/tests/test_metadata.py:129` and `:390`),
   `docs/audits/plan-retirement.md` exists with its row at
   `docs/audits/README.md:240`,
   `grep -niE 'phase [0-9]' docs/plan-retirement.md` prints
   nothing, and `NetworkCheckListTest` passes, so the criterion is
   still outside `NETWORK_CHECKS`. With the two marker pull
   requests merged, `$HOME/bin/retire-plans check` now exits 0 on
   all three pilots' default branches, `retired-row-markers`
   included: three marked rows in divergulent, four in kerbside,
   six in ryll.

7. **The private GitLab hostname is in this public repository three
   times, and all three lines are this plan's.** It appears at
   `docs/plans/PLAN-plan-retirement.md:605`, `:779` and `:908`,
   added by `8010a22` and `623d72b`. The block's floor item 3 names
   exactly this shape -- "no private hostname or IP address entered
   a repository more public than the one it came from" -- so the
   audit owes it a verdict. The measurement that decides the
   verdict is that five other public repositories in the
   organisation already carry the same hostname: `actions`,
   `instar`, `shakenfist`, `client-python-ova` and
   `kerbside-patches`. Scrubbing three lines here would not change
   what is disclosed, so this is a fleet question rather than a
   finding of this plan, and it is out of scope above. The audit
   records the measurement so the next reader does not have to
   repeat it.

8. **llm-setup carries a fifth landing this plan caused and no
   phase recorded.** `3707d70` (!23, `unpushed-branch-hygiene`,
   four files, 48 insertions) makes the session-start hook report
   branches whose commits are on no remote. It exists because phase
   3 lost an unpushed branch, as note n10 in the plan's blocker
   entry records; that note says it does not block the plan, which
   is not the same as saying it was audited. Nothing has audited
   it, and no other plan will ever record it. Decision 3 brings it
   into the llm-setup leg.

9. **Two conditions change how llm-setup's ranges must be read.**
   The clone's working tree is dirty (` M bin/mutation-check`,
   carried since phase 3 and reported in note n15), so a bare
   `$AUDIT_RANGE` there diffs uncommitted work rather than nothing;
   and `c8a79de` is an ancestor of `origin/main` but not on its
   first-parent path, so a first-parent walk does not list it. Both
   are reasons the five ranges are named explicitly per merge below
   rather than derived at audit time.

10. **One declined review item from #386 is recorded outside the
    plan.** Two rounds of automated review produced 21 items, 18
    taken; the one open disagreement -- whether a promisor fetch
    failure should surface inside the criterion as an `error`, as
    it does, or be moved into the authenticated clone step -- is in
    that commit's message and in the pull request comment, and not
    in the plan. The block says a declined finding says why "in the
    plan, where the next reader will find it". Step 5h writes it
    in.

11. **This plan has already failed one of the fleet's own criteria in
    this repository.** `plan-source-references` reports `fail` for
    `development`: six unresolvable plan references, all in
    `scripts/tests/test_audit_snapshot.py` lines 100 to 131, where
    phase 4's first review round (`08b7c0b`) wrote the fixture names
    `PLAN-a.md` and `PLAN-b.md` into expected `details` strings. They
    are fixtures rather than pointers, and the criterion's spec names
    the remedy for exactly that: `audit-ok: plan-reference` on each
    line, or `audit-ok: plan-reference-file` once at the top of a
    file whose plan paths are all fixtures. The audit runs weekly
    and last ran on 2026-11-08, before #386 merged, so the next run
    on
    2026-11-15 files `Consistency: Plan references in source` against
    this repository alongside the five retirement issues. No issue
    needs filing by hand -- the criterion is the filer. Decision 9
    says who fixes it. The only other `fail` on this branch,
    `review-coverage` at 29 files needing review, is byte-identical
    on `main` and belongs to #247.

#### Decisions

1. **Wave 1 runs once per range; wave 2 reads all five ranges and a
   net-effect diff over the twelve clean paths.** The block wants
   what the phases did to each other, which no single post-merge
   range spans without also spanning unrelated work. So wave 2 gets
   both halves: the five per-range diffs, which keep each change
   attributable, and
   `git diff f7b1219^1..3c73bc4 -- <the twelve paths>`, which shows
   the state those files ended in. Survey item 5's eight shared
   paths are read per range only, and each brief names them and
   their unrelated commit counts, so a reviewer of a shared file
   knows which commits are not this plan's.

2. **llm-setup is audited against the block's five-item floor, not
   against this repository's runbook.** Survey item 1. The floor is
   applied to each of its five ranges, and the phase records that
   llm-setup has no runbook, which is the thing the block says
   silently omitting let happen for too long. Writing llm-setup a
   runbook is a merge request there, not work in this plan.

3. **!23 is audited as llm-setup's fifth range, flagged as caused
   by this plan rather than a phase of it.** Survey item 8. This is
   the decision a reviewer is most likely to disagree with, and the
   counter-argument is real: the block scopes the audit to the
   plan's phases, !23 is not one, and auditing it widens the phase
   past its own plan. The reason to do it anyway is that the change
   exists only because this plan lost a branch, it alters a hook
   that runs at the start of every session on this host, it is 48
   insertions over four files, and it rides no plan of its own --
   so the choice is between auditing it here for almost nothing and
   it never being audited at all. The record says plainly that it
   was audited outside the plan's phases and why.

4. **The three pilot audits and the two marker reads are cited, not
   re-run.** Phase 3 decision 5 and survey items 3 and 4. Each
   citation names what that audit found, not merely that it ran: a
   citation a reader cannot evaluate is no better than an
   assertion.

5. **Findings land in a follow-up pull request.** The block and the
   `plan-phase-landing` block agree. Where the audit finds nothing,
   this phase's own pull request sets phase 5 to `Complete` with no
   `Merged` cell -- the one row permitted to omit one -- and marks
   the plan `Complete`. Where it finds something, the follow-up
   pull request records this phase's merge commit and marks the
   plan `Complete`, and this phase says what is outstanding.

6. **Wave 2 briefs carry concrete ranges, substituted by the
   management session.** The runbook's own warning: a sub-agent
   inherits no environment, so a brief handed over with
   `${AUDIT_RANGE:-origin/main...HEAD}` in it expands to the empty
   diff against already-landed work, and an empty diff reads as a
   clean review. Every agent is told which ranges it is reading and
   must report the insertion and deletion totals it measured; a
   report whose totals do not match is re-run rather than triaged.
   The totals to match are, per range: `f7b1219` 6 files +1077,
   `8010a22` 2 files +260/-33, `623d72b` 2 files +227/-20,
   `ea741dd` 1 file +213/-16, `4f7f587` 18 files +2007/-115.

7. **The reserved check greps the fleet, not just the pilot pull
   requests.** The sketch asked for it "by reading the pilot pull
   requests". Reading them is necessary and not sufficient:
   `retire-plans check` only looks inside one repository, and a
   pointer to a retired plan can sit in any of the other 23 --
   which is precisely why the criterion has a cross-repository
   bucket at all. So the check reads the three pull requests and
   greps every clone on this host for the thirteen retired plans'
   names, resolving each hit rather than counting it. llm-setup is
   one of those clones and is private, so its share of the grep is
   step 5g's rather than step 5f's, for decision 8's reason.

8. **The llm-setup leg runs in the management session, not in a
   sub-agent.** llm-setup is private and its diffs carry private
   hostnames and paths. The phase's record is public, so the leg
   that reads private content is read by the session that writes
   the record, and no sub-agent report carries it. This follows the
   precedent of `PLAN-bot-bypass-identity.md`'s phase 4, which ran
   its out-of-repository leg inline for the same reason.

9. **The `plan-source-references` failure is fixed in the follow-up
   pull request, not on this branch.** Survey item 11. The six lines
   sit inside `4f7f587`, one of the five ranges this phase audits.
   A new commit on this branch would not alter those ranges, which
   are merge commits and immutable, so the honest reason is the
   weaker one: the block says findings land as their own pull
   request, and an audit that edits what it is auditing makes its
   own record harder to check against the tree a reader has. The
   fix is six comment markers and nobody is pretending otherwise.
   So the finding is recorded, the weekly run files its own issue,
   and the follow-up pull request carries the markers with whatever
   else the audit turned up.

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | opus | none | Management session, inline, no sub-agent: wave 1 of `PUSH-AUDIT.md`. `git fetch origin` first, then every wave 1 command once per range with `AUDIT_RANGE` exported to each of `f7b1219^1..f7b1219`, `8010a22^1..8010a22`, `623d72b^1..623d72b`, `ea741dd^1..ea741dd` and `4f7f587^1..4f7f587`, plus `pre-commit run --all-files` and `python3 -m unittest discover -s scripts/tests -t scripts` once on the branch. Record each command's result per range in a table rather than as a summary: a grep that matched nothing is a result. Wave 2 is only worth running if wave 1 passes, so stop and report if it does not. |
| 5b | medium | sonnet | none | Section 2a of `PUSH-AUDIT.md`, code quality, read-only. Substitute the five concrete ranges and decision 1's net-effect diff into the brief; do not leave `${AUDIT_RANGE:-origin/main...HEAD}` in it. Name survey item 5's eight shared paths and their unrelated commit counts so that another plan's commit is not read as this one's. The code this plan added is one criterion in `scripts/audit/checks/plans.py` -- `PlanRetirement`, `plan_retirement_index()` and `plan_retirement_clocks()` -- which walks git history under a time budget; the questions are whether any of its early returns can produce the same value as a legitimate empty answer, and whether the budget and the 2 MB cap fail loudly. Report the insertion and deletion totals measured per range. |
| 5c | medium | sonnet | none | Section 2b of `PUSH-AUDIT.md`, test review, read-only, same substitution, same shared-path warning and same totals requirement. The tests this plan added are `scripts/tests/test_plans.py`'s `PlanRetirementTest` (about thirty cases over synthetic git histories) and `scripts/tests/base.py`'s `commit_files()`; phase 4 also ran fifteen mutants, all killed. The question is which properties would survive a regression, not whether the suite passes: in particular whether anything fails if the clock reads the author date instead of the committer date, if the threshold walk stops one revision early, or if a shallow clone is treated as an empty history rather than refused. |
| 5d | medium | sonnet | none | Section 2c of `PUSH-AUDIT.md`, documentation review, read-only, same substitution and totals. In scope: `docs/plan-retirement.md`, `docs/audits/plan-retirement.md`, `docs/audits/README.md`, `docs/consistency-audits.md`, `ARCHITECTURE.md`, `PLAN-TEMPLATE.md`, `README.md` and this plan. The specific question this plan raises is whether any fact about the ninety-day clock, which date it reads, what a statusless table does, or what the audit's clone can and cannot read is stated differently on two pages -- the criterion's spec page and `docs/plan-retirement.md` are the pair most likely to have drifted, since one describes the measurement and the other the convention. |
| 5e | high | opus | none | Section 2d of `PUSH-AUDIT.md`, security review, read-only, same substitution and totals. This plan deletes plan files across the fleet and added a criterion that walks git history in a clone the audit makes, so the brief adds: whether the criterion can be made to read or publish content from outside the index it measures; whether a repository name, plan name or index line reaches a shell, a `gh` argument or an issue body unquoted, given that `audit-manage-issues.py` writes `details` and `findings` into issue bodies in 24 repositories; whether the change from `--depth 1` to `--filter=blob:none` in `.github/workflows/consistency-audit.yml` widens what a workflow run can read or costs an unbounded number of promisor fetches; and whether any secret, token, private hostname or private path entered this public repository, measured against survey item 7 rather than from scratch. |
| 5f | medium | sonnet | none | Decision 7's reserved check, read-only, no writes to any clone. The thirteen plans retired by the pilots are the `(retired)` rows of `docs/plans/index.md` in divergulent (3), kerbside (4) and ryll (6); list them from those files rather than from this plan. For each, grep every repository clone under `~/src/shakenfist/` for the plan's name with and without `.md`, and for its phase files' names, excluding `.git`. Resolve every hit: a permalink in an index row is correct; a link in another plan is correct if it is a permalink; a bare name in `AGENTS.md`, `ARCHITECTURE.md`, a `README.md`, a `blob/main` URL or source is a dangling pointer and a finding. Then read the three pilot pull request bodies (divergulent#143, kerbside#686, ryll#700) and say, for each, whether anything they report deleting was reasoning that one of those surviving pointers wanted. Report one verdict per retired plan and the greps you ran. Do not read or grep anything under `src/private/`: llm-setup's share of this check is step 5g's, because it is a private repository. Do not fix anything. |
| 5g | medium | opus | none | Management session, inline: decision 2's floor over llm-setup's five ranges -- `3a9f268^1..3a9f268` (!20, 7 files +1586/-3), `956b2fa^1..956b2fa` (!21, 4 files +476/-49), `dfebae1^1..dfebae1` (!22, 4 files +352/-9), `c8a79de^1..c8a79de` (!24, 6 files +830/-151) and `3707d70^1..3707d70` (!23, 4 files +48/-4, decision 3). For each: the form of the recorded landing; whether anything it added runs unattended and reports its own failure where a human sees it; that no secret or token entered any repository and no private hostname reached a more public one; that nothing it removed or renamed has a consumer left, with the greps stated; and whether a broken change fails closed. `python3 -m unittest discover tests` is llm-setup's suite, per its `AGENTS.md`. Also do llm-setup's share of step 5f's reserved check: grep the clone for the thirteen retired plans' names and resolve each hit. Run it inline because llm-setup is private (decision 8), and keep the record to verdicts, paths, commands and counts. |
| 5h | medium | opus | none | Management session, inline: the record and the close-out. Write what each wave and each leg found into the Record below, including "nothing" where that is the result, which the block says is worth one sentence. Record the deviation in decision 3 and the declined review item in survey item 10. Then set phase 5's `Status` in the Execution table and the plan's row in `docs/plans/index.md` as decision 5 directs, and run the private-data probe set over the text this phase adds before committing. |

#### Record

Run on 2026-11-12 AEDT from the branch, against the five ranges in
decision 6 and the net-effect diff in decision 1, with `3c73bc4` as
the endpoint. Every reader measured the file and line totals the plan
predicted, so no report was re-run. Two readers substituted reading
the twelve clean paths as they stand for running the net-effect diff
as a command; the state they read is the same, and the shared-path
attribution neither did was done in the management session instead
(`git log --first-parent f7b1219^1..3c73bc4 -- <path>`): on each of
the eight, this plan's only commits are `f7b1219` in `README.md` and
`4f7f587` in the other three, and every other commit belongs to
`PLAN-bot-bypass-identity.md` or the pre-commit node version work.

**Wave 1.** `pre-commit run --all-files` clean and `python3 -m
unittest discover -s scripts/tests -t scripts` 2601 tests OK, both on
the branch. Fourteen greps over five ranges is seventy cells, of
which fifty-nine were empty. A dash is a grep that matched nothing,
which is a result rather than an absence of one:

| Grep | `f7b1219` | `8010a22` | `623d72b` | `ea741dd` | `4f7f587` |
|------|----------:|----------:|----------:|----------:|----------:|
| Python lines over 120 characters | - | - | - | - | - |
| Touched files over 800 lines | 1 | - | - | - | 3 |
| New imports | - | - | - | - | 4 |
| Hand-edited `compliance.md` | - | - | - | - | - |
| Generated block in a spec page | - | - | - | - | - |
| `FROZEN_ISSUE_TITLES` changed | - | - | - | - | - |
| `issue_title` declared | - | - | - | - | 1 |
| Retired issue title | - | - | - | - | - |
| Shared block touched | - | - | - | - | - |
| Shared block version bumped | - | - | - | - | - |
| TODO, FIXME, HACK, XXX | - | - | - | - | - |
| New suppression | - | - | - | - | - |
| Plan reference in code | 3 | - | - | - | 98 |
| Documentation touched | 5 | 2 | 2 | 1 | 7 |

The eleven cells with hits, each looked at and accepted. The three
fixture plan names in `f7b1219` and ninety-eight in `4f7f587` are
`test_plans.py` fixtures under a file-level `audit-ok:
plan-reference-file` marker, and references to the two
`plan-retirement.md` documentation pages, whose names the grep's
case-insensitive `PLAN-*.md` pattern also matches -- except six of
them, in `scripts/tests/test_audit_snapshot.py` lines 100 to 131,
which carry no marker and are F6 below. The four new imports in
`4f7f587` are all standard library (`time`, `re`, `locale`) plus the
package's own `audit.check`; the one new `issue_title` matches its
frozen line; and the files over 800 lines are what wave 2 judged.
Neither the frozen-issue-title nor the retired-title grep fired,
which is correct: both are written to catch a rename or a removal,
and this plan only added an entry.

**Wave 2 and the reserved check.** Six findings, none critical. Each
was reproduced in the management session before being recorded here;
one claim in a reader's report did not survive that and is corrected
below.

| Finding | Where it goes |
|---------|---------------|
| F1 A tool failure in this criterion is not excused, though its git reads are the only ones in the audit package that touch the network | follow-up pull request |
| F2 `docs/audits/plan-retirement.md` claims three `error` paths where the code has eight | follow-up pull request |
| F3 An unreadable index at HEAD reads as no index, which closes the backlog issue | follow-up pull request |
| F4 kerbside's `PLAN-test-harness.md` retirement deleted decisions five repositories still point at | work in kerbside, ryll and visual-digest-rust |
| F5 Bare cross-repository plan names and dead `blob/develop` links left behind by two retirements | work in shakenfist and ryll |
| F6 `plan-source-references` fails for this repository, as survey item 11 found before the run | follow-up pull request |

**F1, the finding that justified the phase.** `plan-retirement` is
not in `NETWORK_CHECKS`, so `github_did_not_answer()` in
`scripts/audit_snapshot.py` returns false for it, and
`scripts/audit-raised.py` exits 1 on any error it does not excuse --
failing that repository's matrix leg and holding back the compliance
page. That function's own docstring says a tool failure from any
other check "is the runner's own git, skillsaw or review-tracking.py
not answering, which the next run would meet too". For this criterion
the premise is false: its `git show` is the only history read in the
whole audit package, every other criterion's git call being
`ls-files`, `check-ignore` or `review.py`'s own clone, and in a
blob-filtered clone that read fetches from the promisor remote. A
transient network failure is therefore classified as a broken check.
The fix is not a line: `NetworkCheckListTest` asserts that the
derived set equals `NETWORK_CHECKS` exactly, deriving it by scanning
each check's source for a GitHub call or a clone, and decision 1 of
phase 4 needs this criterion outside that set to keep it under
`audit_snapshot.py`'s byte-for-byte net. This is also the item two
rounds of review on #386 left open, and it settles it against the
decline: the argument there was about where a fetch failure should
surface, and the defect is that it is misclassified when it does.

**F4, the loss the reserved check was reserved for.** Fifteen lines
in ten files across five repositories name kerbside's retired
`PLAN-test-harness.md` or its phase 1 file: `ryll/AGENTS.md:187`,
`ryll/docs/control-socket-protocol.md:1680`,
`ryll/ryll/Cargo.toml:28` and
`ryll/shakenfist-spice-renderer/Cargo.toml:135` (both `blob/develop`
URLs, which now return 404), `visual-digest-rust/AGENTS.md:108` and
`:111`, `visual-digest-rust/README.md:107-108`,
`uncalibrated-sextant/AGENTS.md:103`,
`uncalibrated-sextant-spice-repro/AGENTS.md:103`, and two in
`shakenfist/docs/components/`, which mirrors other repositories'
pages, so ryll's dangling line is duplicated there and a fix to ryll
reaches it only when the mirror is regenerated. The reserved check
reported seven in four repositories; the management session's own
grep found the Cargo comments, the second sextant repository and the
mirror, and the larger number is the one recorded. Two of them say
what they
wanted from it: ryll's protocol page says "the design rationale for
the control socket and the v1.1 additions lives in" that plan, and
visual-digest-rust's agent context calls the phase plan
authoritative. The retirement commit `kerbside 1ca6355` says those
decisions were "deliberately not kept: the visual-digest-rust crate
decisions and ryll's control-socket and Cargo-feature decisions
(phases 1, 3 and 6), which belong to those repositories and are
documented there". They are not: `visual-digest-rust/docs/` holds
only `visual-digest-format.md`, a wire-format specification, and
`ryll/docs/design-decisions.md` has no control-socket or digest entry
at all. Nothing in the pilot could have caught this -- the pointers
are in other repositories, which is why `retire-plans check` passes
in all three pilots and why decision 7 widened the check to the
fleet. The text is recoverable behind the index permalink.

**F3, and the class it belongs to.** `PlanRetirement.applies()`
treats an index it cannot read as an index that is not there, and
`scripts/audit-manage-issues.py` closes an open backlog issue on a
`not_applicable` verdict with "not applicable to this project". An
index over the 2 MB read cap, a symlink out of the checkout or a
directory therefore retires the issue rather than raising. The
direction is the benign one -- it hides a backlog rather than filing
work outward -- and the largest index in the fleet is 62 KB, three
per cent of the cap, so only the symlink and directory cases are
reachable in practice. What makes it worth recording is that phase
4's own first review round drew exactly this distinction for a
historical revision, in the commit "Tell a deleted index from an
unreadable one", and left the same conflation at HEAD. The class was
swept one level deep.

**What was reported and did not hold.** A reader grouped the `pass`
branch with F3, on the grounds that a verdict of `pass` also closes
the issue and this repository reports `pass` with "1 terminal row(s)
the history does not date: PLAN-llm-doc-structure.md". That row is
not a defect: the oldest revision of this repository's own
`docs/plans/index.md` is 81 days old against a 90-day threshold, so
the history cannot establish that the row has been terminal for long
enough, and the criterion reports it rather than counting it. The
report travels in `details`, which reaches the published results and
`audit_snapshot.py`'s comparison; it does not reach the compliance
page, which carries a criterion's details only on a `fail`. The
same reader's hypothesis that the row exercises the untested
working-tree branch below is also wrong, for the same reason.

**Test coverage.** Twenty-five mutants on copies of `scripts/`, each
under `timeout`, script at `scratchpad/p05/5c/mutate.py`. Every
property the plan named is killed: the committer date, the early
stop, the shallow refusal, the 2 MB cap in four variants, the walk
budget in two, the terminal-to-terminal clock, and the statusless
table in three. Three gaps survive, all in the follow-up pull
request's scope: a row that is terminal only in an uncommitted edit
is left undated by design and asserted nowhere; a walk that stops one
revision late changes only the lower-bound figure, which no test
asserts with two old revisions; and `PLAN_RETIREMENT_WALK_BUDGET`'s
value of 300 is asserted nowhere, because the budget test sets it to
zero.

**Security.** No critical or high finding. Nothing in the five ranges
widens a workflow's `permissions:`, adds a trigger, touches a
privileged workflow or ships a template. The criterion runs no shell:
every git call is an argv list whose only non-constant is a commit
sha, it makes no GitHub call at all, and a link's path becomes a
basename used as a dictionary key and quoted text, so a traversal
attempt publishes a word and reads nothing. Published length is
bounded by `plan_quote` and item count by `PLAN_INDEX_MAX_SHOWN`;
markdown structure is removed by `defuse()` on the page and by
`render_details` in an issue body. `gh repo clone --filter=blob:none`
was measured to leave only the remote URL, `promisor` and the filter
in `.git/config` -- no credential helper and no token -- so a lazy
fetch can only reach the audited repository's own origin. Whether the
runner carries a global credential helper is unestablished and does
not widen anything: the step's token is already read-only over that
history. Fail-closed was tested rather than reasoned, against three
clones: shallow gives `error`, filtered with an unreachable promisor
remote gives `error`, normal gives `pass`, and an `error` leaves
every issue untouched. Two informational notes: an error's details
carry git's stderr verbatim, which stays in the workflow log and
artifact because only a `fail` reaches the compliance page; and where
a plan is listed twice, the first row wins.

**Code quality.** No finding. `scripts/audit/checks/plans.py` at 2331
lines sits below `ci_workflows.py` at 2605 and `packaging.py` at
2375, which are the same shape, so the size is the cost of 69
criteria living in families rather than a defect; there is an
optional 535-line seam at the retirement block, which reads history
where everything above it reads the tree. Three advisories, none
taken here: no CI path exercises a filtered clone's lazy fetch,
because the smoke job was deliberately made hermetic in phase 4's
second review round; one `git show` per revision is a sequential
round trip where `git cat-file --batch` after one fetch would be
cheaper, which phase 4 measured and declined; and the 2 MB cap is
applied to the re-encoded string, so a revision of mangled bytes can
trip it slightly early.

**Documentation.** F2 is `docs/audits/plan-retirement.md:74`, "There
are three ways the criterion reports `error`", against seven `raise
ToolFailure` sites in `plan_retirement_clocks()` -- the shallow
check's own failure, a shallow checkout, a failed `git log`, the
spent budget, a failed `ls-tree`, a failed `git show` and the 2 MB
cap -- plus `run_tool` raising on a timeout or an `OSError`, which
is the eighth path and the one that cannot be read off the function.
The code's own docstring is accurate where the page is not: it says
"when git cannot answer". Plus one gap F1 subsumes: no page says
what an `error` from this criterion costs the audit leg. The six
facts the plan asked about agree across every page that states them
-- the ninety-day clock and what starts it, the committer date and
why, a statusless table, what the clone can read, that "retired" is
not a status, and the one place the criterion and the helper disagree
on purpose. `grep -niE 'phase [0-9]' docs/plan-retirement.md` prints
nothing. Two lines of the criterion's spec page run to 71 columns
against a 70-column file, which is a nit and is left.

**llm-setup, against the five-item floor.** llm-setup has no
`PUSH-AUDIT.md`, so decision 2's floor was applied to its five
ranges, inline, because the repository is private. All twenty-five
cells have a verdict. Item 1: all five landings are two-parent merge
commits and ancestors of `origin/main`, read against their first
parents. Item 2: nothing the ranges add runs unattended; the one
thing that runs on its own schedule is a session-start hook, whose
output goes into a session a person reads, and whose new block
suppresses git's stderr, so a git failure makes it under-report a
branch rather than complain -- worth knowing in a safety net added
because a branch was lost. Item 3: no token, key or credential in any
range, and nothing travelled from the private repository to a more
public one. Item 4: three functions changed signature rather than
being removed, all three still present, with no consumer left naming
a gone symbol in the helper, its tests, the skills or this
repository's documentation. Item 5: not applicable, llm-setup
publishes nothing -- no workflow, no package, no release; it is
installed by symlink. Its suite is 773 tests, OK. Its working tree
carries one unrelated uncommitted edit, which was left alone. The
reserved check over the clone found one hit for the thirteen retired
plans, an illustrative plan name in a skill reference page rather
than a pointer, left as an advisory.

**The citations this phase owes rather than re-runs.**
divergulent#143 ran its runbook in full: wave 1 clean, one blocking
item fixed (two stale step citations in a tool README), two security
boundaries restored that had survived only in deleted text, and one
advisory declined. kerbside#686: wave 1 and the mechanical half of
wave 2 green, one documentation error fixed, no lost security
constraint, with fail-closed host subject, ticket and token handling
checked. ryll#700: wave 1 green, all four wave 2 agents, and the
audit changed the author's mind about one judgement. Phase 4's two
index-marking changes, divergulent#152 and kerbside#712, each had
their repository's runbook applied as a read of the diff, which is
proportionate to an eight- and a nine-line edit of one markdown
table, and found nothing. `$HOME/bin/retire-plans check` exits 0 on
all three pilots' default branches, `retired-row-markers` included.

**The declined review item from #386.** Phase 4's two review rounds
raised 21 items and took 18. The one left open was whether a promisor
fetch failure should surface inside the criterion, where it does, or
move to the authenticated clone step; it was declined on the grounds
that the two measurable alternatives cost more than they saved, and
that record was a commit message and a pull request comment rather
than this plan. F1 is that item, found from the other end, and the
follow-up pull request takes it.

**The reserved check, plan by plan.** Ten of the thirteen are clean:
every pointer to them that survives is a permalink, and nothing they
deleted is wanted by a pointer that is still there.

| Retired plan | Repository | Verdict |
|--------------|------------|---------|
| `PLAN-full-machine-run.md` | divergulent | clean |
| `PLAN-faster-full-run.md` | divergulent | clean |
| `PLAN-generated-marking.md` | divergulent | clean; one orphaned prose citation at `tools/generated-marking/README.md:35`, whose answer is now in `docs/deterministic-rules.md`, so it reads oddly rather than pointing nowhere |
| `PLAN-host-subject.md` | kerbside | clean |
| `PLAN-rust-proxy.md` | kerbside | clean |
| `PLAN-test-harness.md` | kerbside | **F4**: reasoning deleted that fifteen lines in five repositories still want |
| `PLAN-kerbside-vdi-tokens.md` | kerbside | **F5**: bare phase names in `shakenfist/docs/plans/PLAN-kerbside-vdi-tokens.md:379-383`, `:579` and `:595` |
| `PLAN-initial.md` | ryll | clean |
| `PLAN-pip-distribution.md` | ryll | **F5**: a bare name at `shakenfist/docs/plans/PLAN-kerbside-vdi-tokens.md:377` |
| `PLAN-hamburger-menu.md` | ryll | clean |
| `PLAN-pr31-followup.md` | ryll | clean |
| `PLAN-deferred-debt.md` | ryll | clean |
| `PLAN-crate-extraction.md` | ryll | clean |

The greps behind those verdicts: the thirteen names and their
forty-one phase-file names, with and without `.md`, recovered from
each retirement commit with `git log --diff-filter=D --name-only`;
`grep -rnE -I` over every clone under `~/src/shakenfist/`, excluding
`.git`, `target`, `node_modules` and `.venv`, each directory under
`timeout 120`; a `git grep` pass over each non-worktree clone's
default branch, so that an uncommitted edit could not mask a hit; and
a third pass for the bare slugs and for prose citations such as "step
S3" and "the findings doc" outside `docs/plans/`. Deliberately out of
scope: the private clone, whose share ran inline, and the stale
`-wt-` worktree directories, two of which still carry pre-rewrite
pointers on unmerged branches. Vendored third-party copies under a
cargo cache were ignored as snapshots.

**What is outstanding.** Decision 5 holds: this phase closes itself
out, and the plan is not complete until the findings are resolved or
declined in writing.

F1, F2, F3 and F6 and the three test gaps are one follow-up pull
request against this repository. It records this phase's merge commit
in its own close-out text and marks the plan `Complete`; phase 5's
`Merged` cell stays empty, which the push-audit block permits for
this row alone, so the follow-up must not be read as owing that cell
a value.

F4 and F5 are work in kerbside, ryll, visual-digest-rust,
uncalibrated-sextant and shakenfist. Neither has an issue yet:
filing outward is the operator's call and was put to him with
the
record, so until he answers, this paragraph is their only tracked
home. F4 is a deletion to undo from history rather than a pointer to
rewrite, and it is the one a reader should look at first.

F4 also has a systemic cause worth fixing above the instance.
`retire-plans pointers` greps the repository being retired from, and
`docs/plan-retirement.md` tells the operator to rewrite "every
pointer" that reports -- which is every pointer the helper can see.
Nothing in the convention or the helper looks at the other
twenty-three repositories, so a plan whose readers are elsewhere can
pass every check in its own repository and still lose its reasoning.
That is what happened here, and it is a change to the convention and
the helper rather than to this repository.

#### Risks and mitigations

* **The audit reads an empty or wrong range and reports a clean
  result.** The failure the runbook was written to prevent, and the
  reason it keeps the range in one variable. Mitigation: decision 6
  -- concrete ranges substituted into every brief, the expected
  file and line totals stated in the plan, and every report
  required to state the totals it measured. The management session
  re-runs a mismatched report rather than triaging it.

* **Another plan's commit is read as this plan's.** Eight of the
  twenty paths carry unrelated work in the window. Mitigation:
  decision 1 keeps the net-effect read to the clean twelve, and
  each brief names the eight with their counts. The management
  session checks any finding against a shared path with
  `git log --first-parent f7b1219^1..3c73bc4 -- <path>` before
  accepting it.

* **Private content reaches the public record.** llm-setup is
  private, and this phase reads five of its diffs. Mitigation:
  decision 8 runs that leg inline, the record holds verdicts,
  paths, commands and counts only, and step 5h runs the
  private-data probe set over the text the phase adds before the
  commit.

* **The reserved check over-reports.** `retire-plans check` already
  passes in all three pilots, so most hits will be correct
  permalinks. Mitigation: decision 7 requires each hit to be
  resolved rather than counted, and the step reports one verdict
  per retired plan, so a clean result is visibly clean rather than
  an absence of output.

* **The audit finds something large.** Mitigation: decision 5 --
  findings land in a follow-up pull request, so a finding does not
  grow this phase. A finding that changes the criterion is also a
  finding about an audit that has already filed issues in five
  repositories, so the record says whether anything already filed
  is wrong.

#### Definition of done

* Wave 1 has a recorded result for every command against every one
  of the five ranges, including the greps that matched nothing, and
  `pre-commit run --all-files` and
  `python3 -m unittest discover -s scripts/tests -t scripts` pass
  on the branch.

* Each wave 2 report states insertion and deletion totals that
  match decision 6's table for the ranges it was given.

* The record carries, for each of llm-setup's five ranges, a
  verdict or a stated reason for not applying it, for all five of
  the block's floor items -- twenty-five cells, none blank.

* The record cites divergulent#143, kerbside#686 and ryll#700 by
  what each audit found, and the two marker reads from phase 4's
  notes, rather than by the fact that they ran.

* Every one of the thirteen retired plans has a stated verdict on
  whether anything still points at it, and the greps behind those
  verdicts are named. `$HOME/bin/retire-plans check` exits 0 on
  divergulent, kerbside and ryll at audit time.

* `grep -niE 'phase [0-9]' docs/plan-retirement.md` still prints
  nothing, and no text this phase adds contains a token, the private
  GitLab hostname, or a path to a file under `src/private/`. Naming
  that directory as the scope a step must not read is the rule
  itself, not a breach of it.

* The plan states why the #386 review item named in survey item 10
  was declined.

* The record states, for `plan-source-references` and
  `review-coverage`, whether each `fail` on this branch is this
  plan's or pre-existing, measured against `main` rather than
  asserted.

* Phase 5's Execution row reads `Complete` with an empty `Merged`
  cell and the plan's index row reads `Complete`, if the audit
  found nothing; otherwise both say what is outstanding and the
  follow-up pull request is named.

#### Back brief

Before step 5a, back brief Mikal on three points.

Decision 3 widens the audit to !23, a change this plan caused but no
phase of it owns. Agreeing now is one extra range in a leg that is
already running; disagreeing later means the leg is re-run or the
change stays unaudited for good.

Decision 1 restricts the net-effect diff to the twelve paths no
unrelated commit touched, which means the net effect of the plan on
`docs/plans/index.md`, `docs/audits/README.md`,
`docs/consistency-audits.md` and five other shared files is read per
range only. The alternative is a wider net-effect diff with other
plans' work in it, judged by an agent that cannot tell the
difference.

Survey item 7 found this plan put the private GitLab hostname into
this public repository three times, and that five other public
repositories already carry it. The audit's verdict as planned is to
record the measurement and leave it, because scrubbing three lines
changes nothing disclosed. Whether to scrub anyway, or to open a
fleet-wide question, is his call and not the audit's.

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
* `retire-plans eligible` refuses a local clone whose `origin/HEAD`
  is unset, with the right remedy in the message, but it was unset in
  eight of the 24 clones on this host, so a fleet-wide survey stops
  eight times before it starts. It could run `git remote set-head
  origin --auto` itself, as it already fetches. The helper is
  llm-setup's, so this is a merge request there rather than work in
  this plan, and the criterion is unaffected: the audit's `gh repo
  clone` sets `origin/HEAD`.

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
