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
2026-10-28 (the age is the first commit of `docs/plans/index.md`
in which the plan's row carried its current terminal status; the
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
  cannot read `git log`. Anything about age goes through the
  `audit/github.py` seam's `api_answer()`, which raises
  `ToolFailure` (and so reports `error`) when GitHub does not
  answer.
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
  instance; retirement supersedes it.
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
2. **What starts the 90-day clock?** Default: the date of the last
   commit touching the plan file or any of its phase files, read
   through the GitHub API. It needs no new field, and it means a
   plan whose push-audit follow-ups are still landing is not yet a
   candidate. The alternative is an explicit completion date in the
   plan, which every repository would have to start writing.
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
| 1. Retirement convention | In progress | |
| 2. `retire-plans` skill | Not started | |
| 3. Pilot | Not started | |
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
   never an unregistered master. `PlanIndexTest.
   test_a_link_to_another_repository_does_not_register_a_local_plan`
   (`scripts/tests/test_plans.py:1155`) already pins URL handling;
   `PlanAuditPhaseTest.test_a_plan_linked_by_absolute_url_is_not_judged`
   (`:2077`) shows `plan-audit-phase` skips such rows too. The
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

### Phase 2: `retire-plans` skill

Planning effort: high. The skill is where the judgement lives, and
a weak brief here produces confident deletions of the reasoning the
mission exists to keep.

Lands in `llm-setup` (`dot-claude/skills/retire-plans/SKILL.md`),
beside `consistency-fix`, as its own pull request; record it in
`Merged` as `llm-setup <sha> (#pr)`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | xhigh | opus | none | Write the `retire-plans` skill. Read `consistency-fix/SKILL.md` and `template-resync/SKILL.md` for shape and tone first. Invoked in a repository (optionally with plan filenames), it: lists eligible plans by the rule in `docs/plan-retirement.md` here (link to it; do not restate it) (computing age locally with `git log -1 --format=%cs -- <plan and phase files>` against `origin/<default>`); skips the run if an open pull request already retires any of them; cuts one worktree and branch; and, per plan, in one commit each: reads the whole plan and its phases; compares against `docs/`, `ARCHITECTURE.md` and `AGENTS.md`, filling gaps; extracts the reasoning (analysis, *Situation* measurements, rejected alternatives, back-brief corrections, push-audit declines) and writes what a future change needs into docs/; triages each *Future work* item against the code and open issues (`gh issue list --search`) into filed / already tracked / done / dropped-with-reason; rewrites every pointer (`git grep -n <basename>` across the repo, including other plans and `.claude/`); deletes the files; rewrites the index link to a permalink. The pull request body carries a per-plan table: docs changed, reasoning moved (with links), issues filed, items dropped and why. It runs `pre-commit run --all-files` and the repo's audit checks for `plan-index` and `plan-source-references` before pushing. It never merges, and it never runs `prune` or touches `REVIEWS.md`. State explicitly that a plan whose reasoning cannot be placed in docs/ is left unretired and reported, rather than deleted. |
| 2b | medium | sonnet | none | Teach `consistency-fix` to hand a `Completed plans awaiting retirement` issue to `retire-plans` instead of fixing it itself, and to close the issue from the retirement pull request body. |

### Phase 3: pilot

Planning effort: medium.

Run `retire-plans` by hand, in the management session, on
divergulent (3 eligible) then kerbside (3 eligible), one pull
request each, and review them with Mikal. These are small enough to
read every deletion. For each, record here: what the skill moved
into docs/ that a reviewer agreed was missing, what it got wrong,
and what the skill change was. Then run it on one large repository
-- ryll, which also has the `completed/` directory to fold in --
before the criterion lands. Expect at least one round of skill
changes in `llm-setup` from each pilot; record their merges.

This repository has no eligible plan until late November 2026; it
is retired by the issue the criterion files, like any other.

Merged: (one line per pilot pull request and per skill revision)

### Phase 4: `plan-retirement` criterion

Planning effort: high. It reaches `audit-manage-issues.py`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Add a `PlanRetirement` class to `scripts/audit/checks/plans.py`: id `plan-retirement`, spec `docs/audits/plan-retirement.md`, `issue_title = 'Completed plans awaiting retirement'`. N/A without `docs/plans/index.md`. Read the index with `iter_markdown_table_rows` and `plan_table_columns` as `PlanIndex` does; for each row with a local link and a terminal status, find its phase files with `PLAN_PHASE_FILE_RE`, and ask `repo.github.api_answer()` for `repos/shakenfist/<repo>/commits?path=<file>&per_page=1` per file (the clone is `--depth 1`, so `git log` is not available), taking the newest date. Fail listing each plan over `RETIREMENT_AGE_DAYS = 90` with its age, as `findings`. GitHub not answering raises `ToolFailure` (status `error`), never `fail` or `not_applicable`. Register in `CHECKS` in `scripts/audit/registry.py` beside the plan checks; write the spec following `docs/audits/README.md`, naming the `retire-plans` skill and linking `docs/plan-retirement.md`; add its row to `docs/audits/README.md`; add `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` lines in `scripts/tests/test_metadata.py`; tests in `scripts/tests/test_plans.py` covering pass, fail, N/A, a permalink row (ignored), phase-file ages, and API failure, using `FakeGitHub`. |
| 4b | medium | sonnet | none | Decide, with the pilot results, whether the rule in `docs/plan-retirement.md` should also become a shared block in every `PLAN-TEMPLATE.md` (added to `PLAN_TEMPLATE_BLOCKS`). If yes, it reaches the fleet through the issues `plan-template` files, as `plan-phase-landing` did. |
| 4c | medium | sonnet | none | Run the new check with `--dry-run` across the matrix and record here how many repositories it newly fails, including this one, before merging. |

### Phase 5: push audit

Runs `PUSH-AUDIT.md` over every range recorded above, in this
repository and `llm-setup`. In addition to the runbook: confirm, by
reading the pilot pull requests, that no retirement deleted
reasoning that `AGENTS.md` or another plan still pointed at.

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
* Folding ryll's `PLAN-deferred-debt.md` pattern -- a sweep of
  future work across many finished plans -- into the skill as a
  bulk mode, if the first fleet-wide round shows per-plan triage
  missing cross-plan duplicates.

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
