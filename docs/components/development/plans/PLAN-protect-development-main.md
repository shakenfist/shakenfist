# Protect development's default branch

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

`shakenfist/development` is a moving-ref source, and nothing stops a
direct push to its default branch. This was raised as #393 by the
automated review of #392 and is recorded as a *Known gap* in
`docs/audits/moving-ref-source-protection.md`.

**What the fleet runs from here.**
`templates/review-tracking/ci-prune-reviews.sh` clones this repository
with no ref pin, exports it as `SHAKENFIST_DEVELOPMENT`, and
`templates/review-tracking/review-tracking.sh` then `exec`s
`scripts/review-tracking.py` out of that clone. That happens in the
`prune-reviews` job of all eight adopters listed in
`templates/review-tracking/README.md`, so whatever `main` holds when
one of those jobs starts executes inside it.

**What protects `main` today.** One repository ruleset, read on
2026-11-18:

| Field | Value |
|-------|-------|
| id | `21132499` |
| name | Protect default branch history |
| enforcement | `active` |
| rules | `deletion`, `non_fast_forward` |
| bypass_actors | `[]` |

`shakenfist/actions`, the other moving-ref source, carries
`deletion`, `non_fast_forward`, `pull_request` and
`required_status_checks`, with the review-marks App
(`shakenfist-review-marks`, actor id 5538020) as its only bypass;
`PLAN-workflow-ref-trust.md` phase 2 put that there.

**Why the fix in #393 cannot simply be applied.** Adding the two rules
today would refuse three automated writers and wedge one class of
pull request:

| Writer | How it reaches `main` now | Under the two rules |
|--------|---------------------------|---------------------|
| `prune-reviews.yml` (every push, and daily at 04:23 UTC) | `PUSH_TOKEN` falls back to `github.token`. The workflow already supports the App: its `Mint the push token` step runs when `secrets.REVIEW_MARKS_APP_KEY` is set, and was `skipped` in run 44094308347 on 2026-11-17. The `review-marks` environment exists with the correct `main`-only branch policy (and `bypass-actor-identity` reports this repository compliant), but holds no key. The adopter table records development as pushing with `GITHUB_TOKEN`. | Refused: GitHub does not accept the built-in Actions app as a bypass actor. |
| `consistency-audit.yml` `update-docs` (Sunday 18:00 UTC) | `scripts/commit-audit-docs.sh` runs `git push origin main` with the `GITHUB_TOKEN` that `actions/checkout` persisted, in the `audit-write` environment. | Refused, and the compliance page silently stops updating -- which is the failure `report-failure` exists to make loud, so it would at least file an `audit-failure` issue. |
| `export-repo-config.yml` (daily 00:30 UTC) | The shared workflow in `shakenfist/actions` pushes `export-repo-config-<date>` and opens a pull request with `GITHUB_TOKEN`, only when the exported settings changed. The last one was #39 on 2026-08-22, merged by hand. | Its pull request opens, but events created by `GITHUB_TOKEN` do not trigger `pull_request` workflows, so no required check ever reports and the merge button never appears. The ruleset change itself changes the exported settings, so the first such pull request arrives the morning after phase 3. |
| Review-only pull requests (`reviews` branches such as #420 and #423) | `ci.yml` filters `REVIEWS.md` and the `.vscode` weaudit files at the trigger, so the workflow never starts. #420's check rollup holds no `Lint and test`. | Wedged: a required check that never reports blocks the merge forever. A skipped job satisfies a required check; a workflow that never starts does not. |

Two further facts shape the design:

* `expensive-lane-path-filter` already states the rule phase 1
  follows: a workflow backing a required status check must filter
  with a `dorny/paths-filter` job feeding job-level `if:`
  conditions, never trigger-level `paths-ignore`. So today's `ci.yml`
  becomes non-compliant with our own standard on the day its checks
  are required. `shakenfist/actions`' `ci.yml` is the worked example.
* The filter job has to be a required check too. If it fails, every
  job gated on it is skipped, and a skipped required check passes: an
  unrequired filter job that errors waves the pull request through
  unchecked. `actions` requires its `Check paths` for that reason.
  Here `codeql-analysis.yml` already has a job named `Check paths`,
  and a required context is matched by name, so `ci.yml`'s filter
  job needs a distinct name.

Other pull-request checks, for the record: `secret-scan.yml`'s
`gitleaks` runs on every pull request with no path filter;
`issue-link-check.yml`'s `Issue links` runs on
`opened`/`synchronize`/`reopened`/`edited`; `mermaid-lint.yml` is
trigger-filtered to markdown and so cannot be required as it stands;
`Automated reviewer` is a reusable workflow that costs credits and
fails on API outages (#399), so it should not be required.

Renovate does not push to `main`: `renovate-fleet.json` sets
`automerge: false`, and its pull requests already trigger CI.
`allow_auto_merge` is off on this repository.

## Mission and problem statement

Make `development`'s `main` a gate rather than a place anyone with
write access can push, as `actions`' already is, and measure it so
it cannot regress: a `pull_request` rule with zero required
approvals, a `required_status_checks` rule naming this repository's
own CI jobs, the review-marks App as the only bypass, and
`shakenfist/development` in `MOVING_REF_REPOS`. Every automated
writer keeps working, and every kind of pull request this repository
actually sees can still merge.

Out of scope: pinning the adopters' clone of this repository to a
sha (the spec's argument against pinning `actions` applies here
too); a merge queue, which would answer #411 completely but is a
separate decision about every landing's latency (see open question
3); the reviewer-failure visibility in #399; and the audit's read
identity in #352.

## Open questions

1. **Which identity pushes the weekly compliance page?** Options:
   (a) split `update-docs` so a new job in the `review-marks`
   environment mints a review-marks App token and pushes the page
   artifact the generating job produced; (b) open a pull request for
   the page each week with `AUDIT_TOKEN`, merged by hand or by
   auto-merge; (c) move the page off `main`. **Decided 2026-11-18:
   (a).** It
   keeps the page current with no weekly chore, and
   `bypass-actor-identity` already defines the review-marks App as
   the one automation identity allowed to bypass. The cost is that
   the App's key becomes readable by a second workflow on `main`, and
   the App's role widens from "lands review marks" to "lands
   generated files"; phase 2 records that in
   `docs/audits/bypass-actor-identity.md` rather than leaving it
   implicit. (b) puts a pull request in front of you every Sunday
   night and leaves the page stale until it merges; (c) breaks every
   link to `compliance.md#...` across the fleet's issues.
2. **Which contexts are required?** **Default:** the new filter job,
   `Lint and test`, `Audit smoke run` and `gitleaks`, all with
   `integration_id` 15368. Not `Automated reviewer` (cost, and
   #399), not `Issue links` (it re-runs on description edits and is
   advisory), not CodeQL's `Analyze` (its own path filter), not
   `mermaid-lint` (trigger-filtered). **Decided 2026-11-28: the
   default, as `CI paths`, `Lint and test`, `Audit smoke run` and
   `gitleaks`.** Phase 3's survey read all four reporting on a code
   pull request (#452) and on a review-only one (#428) before
   requiring any of them.
3. **Merge queue?** **Decided 2026-11-18: no, not in this plan.** Required
   checks narrow #411 (a red pull request can no longer merge) but do
   not close it (two green pull requests can still merge into a red
   `main`). A merge queue would close it at the cost of a
   `merge_group` trigger on `ci.yml` and a queue wait on every
   landing. Recorded under *Future work* with #411 as its issue.
4. **Does anything outside these workflows push straight to
   `main`?** Your own habits and tools -- a plan close-out pushed
   directly, a script that commits to `main`. **Default:** assume
   not; the ruleset makes any such push fail loudly rather than
   silently, and `current_user_can_bypass` will read `never` for
   your credential, as on `actions`. Say so before phase 3 if there
   is one. **Answered 2026-11-28: nothing does.** Mikal confirmed it
   in phase 3's back brief: everything of his reaches `main` through
   a pull request, so the `pull_request` rule refuses nothing he
   relies on.
5. **`export-repo-config` pull requests.** **Default:** document the
   workaround (close and reopen the pull request, which is an event
   from your account and so triggers CI) beside the ruleset template
   in `docs/audits/moving-ref-source-protection.md`, and do not
   change the shared workflow, which `actions` already lives with.
   A shared-workflow fix -- opening the pull request with an App
   token -- belongs to `shakenfist/actions` and is *Future work*.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. CI that reports on every pull request | Complete | `c020ce7` |
| 2. Push identities for automated writers | Complete | `c020ce7` |
| 3. Apply the ruleset | In progress | |
| 4. Measure it | Not started | |
| 5. Push audit | Not started | |

Phases 1 and 2 are independent and may be open at once (see
`plan-phase-landing` on concurrent branches). Phase 3 needs both
merged and their verification items done. Phase 4 needs phase 3,
because adding the table entry before the rules exist files an issue
no code change can close.

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

### Execution notes

* 2026-11-18 (AEDT): phases 1 and 2 land together in one pull request
  from `protect-development-main`, at Mikal's choice in the back
  brief, so both phases' `Merged` cells will record the same merge
  commit. This session may push and open that pull request.
* Phase 2's operator steps were done on 2026-11-18: the App was
  installed on this repository and `REVIEW_MARKS_APP_KEY` set in
  `review-marks`. `REVIEW_MARKS_APP_CLIENT_ID` needed no setting: it
  is an organisation variable this repository already sees. A
  dispatched `prune-reviews` run, 44154156673, showed `Mint the push
  token` as `success` (it was `skipped` in 44094308347); it had
  nothing to push, so the first App push is still to come.
* Steps ran in sequence in this worktree rather than 2a in an
  isolated worktree: every step runs `pre-commit` in the same tree.
* Step 1a departed from its brief in two ways, both kept and both
  corrected in the brief above: the gated jobs fail open
  (`!cancelled() && ... != 'false'`, as `actions` and
  `codeql-analysis.yml` do) rather than testing `== 'true'`, so a
  filter that errors costs a full run instead of a skip; and the
  `paths-filter` pin is this repository's own from
  `codeql-analysis.yml`, because `actions` refers to `@v4` by tag.
* Step 1b changed nothing: no document in this repository described
  its own `ci.yml` as filtered at the trigger. The `paths-ignore`
  advice in `docs/code-review-tracking.md` is adoption guidance for
  other repositories, which `expensive-lane-path-filter` already
  qualifies for workflows backing required checks.
* Step 2a went beyond its brief in three ways, all kept. `push-docs`
  commits in a clone checked out at the audited commit rather than at
  `main`'s tip, because the page carries that commit's copy of its own
  hand-written prose and would otherwise undo an edit landed during
  the run; the rebase then carries such an edit forward. It changed
  the workflow guard tests (`test_audit_workflow.py`,
  `test_registry.py`) to pin the new job's key, runner, token and
  gate, with four workflow mutations each caught. And
  `commit-audit-docs.sh` runs from a function called on its last line,
  because the rebase rewrites the clone the script runs from. The
  script was exercised against a local bare repository standing in for
  GitHub: missing or empty token, missing page, an unchanged page, and
  a concurrent prose edit, with the token seen only by `git push`.
* 2026-11-28 (AEDT): phases 1 and 2 closed out in the first commit of
  phase 3's branch, as `plan-phase-landing` provides, with their
  verification items read back from the live repository rather than
  taken from their own pull request's claims. Ten days ran between the
  merge and the close-out, which is what made the verification worth
  doing: both phases have since been exercised by real traffic -- four
  review-only pull requests, two audit runs and a fortnight of
  `prune-reviews` pushes -- rather than by one dispatched run.
* 2026-11-28 (AEDT): phase 3's ruleset PUT was made at 12:20 by this
  session with Mikal's `gh` credential, while #456 was open, as
  decision 5 provides. Two departures to note. The direct-push
  refusal, done item 2, could not be run from the session: Claude
  Code's auto-mode classifier refuses a push to a default branch,
  which is the harness rather than the ruleset, so that item is
  Mikal's to run. And the pull requests are held on `gitleaks` by a
  saturated ephemeral runner pool rather than by anything the rules
  do, which is recorded as a finding under phase 3 because it changes
  what requiring that context costs.

### Phase 1: CI that reports on every pull request

Planning effort: medium -- `shakenfist/actions`' `ci.yml` is the
worked example. Merged: `c020ce7`, the merge commit of #424, which
landed phases 1 and 2 together.

Replace `ci.yml`'s trigger-level `paths-ignore` with a filter job, so
that every required context reports on every pull request, success
or skipped.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | In `.github/workflows/ci.yml`, remove the `paths-ignore` list from the `pull_request` trigger and add a first job `ci_paths`, named `CI paths` (not `Check paths`: `codeql-analysis.yml` already has a job of that name and a required context matches by name). Mirror `check_paths` in `shakenfist/actions`' `.github/workflows/ci.yml` (read it with `gh api repos/shakenfist/actions/contents/.github/workflows/ci.yml --jq .content \| base64 -d`): static runner, `permissions: pull-requests: read`, no checkout, `dorny/paths-filter` pinned to the commit `codeql-analysis.yml` in this repository uses (`actions` refers to it by tag), an output that defaults to `'true'` when the filter step yields nothing (so `workflow_dispatch`, which has no pull request, runs everything). The filter's "code" set is everything except `REVIEWS.md`, `.vscode/*.weaudit` and `.vscode/*.weaudit-shas.json` -- exactly the three patterns being removed, and not `.vscode/review-scope.toml`, which the existing comment says is deliberately linted. Gate `lint-and-test` and `audit-smoke` on it with `needs:` and `if: !cancelled() && needs.ci_paths.outputs.code_changed != 'false'`, so that a filter that errors runs the gates rather than skipping them; `automated_reviewer` already needs both and so skips with them. Move the existing trigger comment's reasoning to the new job, and add a sentence saying why the filter job must itself be a required check (a failed filter skips the gated jobs, and a skipped required check passes). Keep every existing comment that is still true. Run `pre-commit run --all-files` (actionlint covers the workflow) and `python3 scripts/audit-check.py --repo-path . --repo-name development`, and confirm `expensive-lane-path-filter` and `ci-workflow`-family verdicts for this repository are unchanged or better; report any that moved. |
| 1b | medium | sonnet | none | Document the new shape where this repository describes its own CI: find it with `grep -rn "paths-ignore\|ci.yml" docs/ AGENTS.md`, and update any statement that review-only pull requests do not start `ci.yml` -- they now start it and skip its jobs. Do not touch `docs/audits/compliance.md` or `REVIEWS.md`. |

Verification before this phase is done, recorded here: on the phase's
own pull request (which changes code), `CI paths`, `Lint and test`
and `Audit smoke run` report success; on the next review-only pull
request after merge, `CI paths` reports success and the two gated
jobs report skipped. Record both pull request numbers.

**Both done, read on 2026-11-28.** The code pull request was #424:
`CI paths`, `Lint and test` and `Audit smoke run` all report success
(`gh pr checks 424`), alongside `gitleaks`, `Check paths`, `Analyze`,
`Issue links`, `Mermaid lint` and `Automated reviewer`. The next
review-only pull request after the merge was #428, a `reviews` branch
opened on 2026-11-20: `CI paths` and `gitleaks` report success while
`Lint and test`, `Audit smoke run`, `Automated reviewer` and CodeQL's
`Analyze` report skipping. That is the shape phase 3 needs -- a
required context that reports on a review-only pull request rather
than never starting -- and #428 is the evidence, two days before any
rule required it.

### Phase 2: push identities for automated writers

Planning effort: high -- it moves a bypass-holding key into a second
workflow. Merged: `c020ce7`, the merge commit of #424, which landed
phases 1 and 2 together.

Operator steps (Mikal, with an owner credential), done first and read
back:

1. Install `shakenfist-review-marks` on `shakenfist/development`
   (organisation settings, the App's repository access). GitHub
   refuses a ruleset bypass actor that is not installed, so phase 3
   depends on this too.
2. In the existing `review-marks` environment, set the secret
   `REVIEW_MARKS_APP_KEY` and the variable `REVIEW_MARKS_APP_CLIENT_ID`,
   as phase 1 of `PLAN-bot-bypass-identity.md` did for the other
   adopters. Do not change the environment's branch policy; it is
   already `main` only.
3. Read back: `gh api repos/shakenfist/development/environments/review-marks/deployment-branch-policies`
   still lists only `main`, and the next `prune-reviews` run on `main`
   shows `Mint the push token` as `success` rather than `skipped`.
   `prune-reviews.yml` itself needs no change.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | worktree | Implement open question 1's answer in `.github/workflows/consistency-audit.yml`. Default (a): keep `update-docs` generating the page in `audit-write`, but give its checkout `persist-credentials: false`, drop `contents: write` to `contents: read`, and upload `docs/audits/compliance.md` as an artifact instead of pushing. Add a job `push-docs`, `needs: update-docs`, guarded by `github.ref == 'refs/heads/main'` and the same success conditions, in `environment: review-marks`, on the ephemeral vm pool, which mints a token exactly as `prune-reviews.yml`'s `Mint the push token` step does (same pinned `actions/create-github-app-token` commit, `NODE_USE_ENV_PROXY`, no owner input, `permission-contents: write`) and runs `scripts/commit-audit-docs.sh` with that token. Follow `prune-reviews.yml`'s pattern of taking the script from a fresh clone of `main` in `RUNNER_TEMP` rather than the workspace, and change `commit-audit-docs.sh` to take the page path and a `PUSH_TOKEN` rather than relying on persisted checkout credentials -- it must still add only the one generated file, and must fail if `PUSH_TOKEN` is empty rather than fall back to `GITHUB_TOKEN`, which the ruleset will refuse. Add `push-docs` to `report-failure`'s `needs`. Run `pre-commit run --all-files`. |
| 2b | medium | sonnet | none | Documentation for 2a and the operator steps: in `docs/audits/bypass-actor-identity.md`, say that on `development` the review-marks key is also read by `consistency-audit.yml`'s `push-docs` job to land the compliance page, why (open question 1), and that the branch policy is what bounds it; in `templates/review-tracking/README.md`'s adopter table, change development's *Pushes as* to `App`; in `docs/consistency-audits.md`, wherever it says the weekly run pushes the page, say it pushes as the review-marks App. In `docs/audits/moving-ref-source-protection.md`, beside the ruleset template, add a paragraph on pull requests opened by `GITHUB_TOKEN` (the `export-repo-config` case): no workflow runs, so required checks never report; close and reopen the pull request to start CI. |

Verification, recorded here: a `workflow_dispatch` run of
`consistency-audit.yml` from `main` after merge pushes the page with
the App token (the commit's pusher is the App), or reports "No
changes" and the `push-docs` job's token step succeeds. A dispatched
run files and closes real issues fleet-wide exactly as the Sunday run
would, so take it only when that is acceptable, or wait for Sunday.

**Done, read on 2026-11-28.** Two runs exercised it. The scheduled
Sunday run 45080404513 (2026-11-22) and the dispatched run
45674865787 (2026-11-25) both succeeded with 24 `Audit <repo>` jobs,
`Manage consistency issues`, `Update the audit compliance page`, `Push
the audit compliance page`, and `Report audit failure` skipped. The
dispatched run pushed: `d04a24b`, *Regenerate the audit compliance
page*, authored `shakenfist-bot <bot@shakenfist.com>` as
`commit-audit-docs.sh` sets it. The push went through the App rather
than the persisted checkout credential, which the job log shows
rather than infers: `push-docs` ran with `HAS_REVIEW_MARKS_KEY: true`,
downloaded `actions/create-github-app-token@f418cff`, re-cloned into
`RUNNER_TEMP/audit-docs` and checked out `GITHUB_SHA` detached, and
`commit-audit-docs.sh` fails on an empty `PUSH_TOKEN` rather than
falling back -- so a successful push is an App push. The
`review-marks` environment still holds exactly one secret,
`REVIEW_MARKS_APP_KEY`, and exactly one branch policy, `main`.
`prune-reviews` has been pushing as the App since the key was set:
run 46081289818 on 2026-11-27 pushed from `main` with `Mint the push
token` succeeding.

### Phase 3: apply the ruleset

Planning effort: medium -- the template is in
`docs/audits/moving-ref-source-protection.md` and
`docs/audits/bypass-actor-identity.md`. Merged: not applicable (a
settings change; the date and the read-back are recorded here
instead). Planned 2026-11-28 on
`protect-development-main-phase-03-ruleset`.

Make `main` a gate: one PUT to ruleset `21132499` adding the
review-marks App as the only bypass actor and the `pull_request` and
`required_status_checks` rules, then read it back and prove that every
automated writer and every kind of pull request this repository sees
still works.

**Scope.** In: the PUT, the read-back, the proofs, and the record of
all three here. Out: adding `development` to `MOVING_REF_REPOS`, which
is phase 4 and has to follow rather than lead (the table entry before
the rules files an issue no code change can close); a merge queue
(open question 3, *Future work*); turning `allow_auto_merge` on, which
is off here and on for `actions` -- nothing in this phase needs it,
and leaving it off keeps every merge a button you press; and any
change to the shared `export-repo-config` workflow (open question 5).

#### What the survey found

Read on 2026-11-28 against the live repository, with `main` at
`6dcb186`.

* **Nothing in the phase's premises has gone stale.** Ruleset
  `21132499` is still exactly what the *Situation* table records: name
  "Protect default branch history", `enforcement: active`, condition
  `~DEFAULT_BRANCH`, rules `deletion` and `non_fast_forward`,
  `bypass_actors: []` present and visible, and
  `current_user_can_bypass: "never"`. It is still the only ruleset on
  the repository, and no organisation ruleset reaches the branch. All
  four contexts open question 2 settles still exist as job names:
  `CI paths` and `Lint and test` and `Audit smoke run` in `ci.yml`,
  `gitleaks` in `secret-scan.yml`.
* **Every one of those four already reports on both kinds of pull
  request**, so none of them is at risk of being a context that never
  reports. On #452, a Renovate pull request, all four are success --
  which also re-confirms that Renovate's pull requests trigger CI. On
  #428, a review-only pull request, `CI paths` and `gitleaks` are
  success while `Lint and test` and `Audit smoke run` are skipping.
  Worth knowing before anything else is ever required here: the two
  contexts that come from reusable workflows report under a
  two-part name, `Automated reviewer / Automated reviewer` and
  `Issue link check / Issue links`, and a required context is matched
  by the whole string.
* **The worked recipe for this exact change is not where the master
  plan's step 1 points.** `bypass-actor-identity.md`'s *Template*
  adds a bypass actor and passes `rules` through untouched; it does
  not add rules, and it carries no parameter sets for the two new
  ones. What does carry them is `PLAN-workflow-ref-trust.md` step 2a,
  which records the whole of `actions`' ruleset as read back on
  2026-11-11 -- `pull_request` parameters copied there from instar's
  live ruleset, which is also where `integration_id: 15368` comes
  from -- along with the ordering it used and two proof reads this
  phase borrows. Decision 1 builds the body from that record.
* **Phase 2's writers are running, and they are the only two that
  reach `main`.** `git push` across `.github/workflows/`, `scripts/`,
  `templates/` and `tools/` finds exactly two sites that push to this
  repository's default branch: `scripts/commit-audit-docs.sh`, run by
  `consistency-audit.yml`'s `push-docs`, and
  `tools/ci-prune-reviews.sh`, run by `prune-reviews.yml`. Everything
  else is a template destined for another repository, or pushes a
  branch or a tag. Both of the two now push with the App token
  (phase 2's verification records the runs).
* **The daily export will put the bypass list in the tree, where the
  audit cannot see it.**
  `.github/exported-config/ruleset-protect-default-branch-history.json`
  is tracked and holds a `bypass_actors` key -- the `GITHUB_TOKEN` the
  shared export workflow authenticates with can read what the audit's
  read-only token is refused. So the morning after the PUT, the daily
  export opens a pull request whose diff is this phase's whole change,
  bypass actor included, and merging it leaves a record in the tree
  that a reader can diff. That is not a measurement by the audit and
  does not close [#352](https://github.com/shakenfist/development/issues/352),
  but it is no longer only a reviewer's note either. Teaching the
  criterion to read the exported file is *Future work*, not this
  phase.
* **One live claim goes stale the moment the PUT lands, and it is
  phase 4's to fix:** the *Known gap* in
  `docs/audits/moving-ref-source-protection.md`, which says this
  repository's `main` "carries only `deletion` and `non_fast_forward`
  rules today". Phase 4's step 4b already replaces it. The same
  sentence appears three times in `PLAN-workflow-ref-trust.md`, each
  time inside a dated survey or step record, so those are history and
  are left alone -- rewriting them would make that plan's record of
  what it found untrue. `docs/code-review-tracking.md` step 3 names
  the same two rules as the *minimum* for a branch carrying review
  state, which stays true.
* Nothing else moved: no new workflow, no new push site, and no change
  to the `review-marks` environment, which still holds one secret and
  one branch policy.

#### Decisions

1. **One PUT, not two.** `actions` took its bypass actor and its two
   rules in separate PUTs. Here it is one, because two have an
   ordering hazard and one has none: rules first then bypass refuses
   every `prune-reviews` push in between, and bypass first then rules
   is harmless but is still two chances to mistype a `rules` array
   that a PUT replaces wholesale. A ruleset PUT is one resource and
   does not half-apply, so a rejection -- the symptom of an App that
   is not installed -- changes nothing. The installation is proven
   independently: `prune-reviews` minted an App token on run
   46081289818 on 2026-11-27.
2. **The body mirrors `actions`' ruleset parameter for parameter**,
   with this repository's four contexts in place of its four. Those
   parameter sets are already proven on a moving-ref source in this
   organisation and came from a live ruleset rather than from the API
   reference, and two moving-ref sources whose rulesets differ only in
   their context list is one less thing for the next reader to hold.
   The parameter that matters beyond consistency is
   `allowed_merge_methods`, which keeps `merge`: this plan's `Merged`
   column records merge commits read against their first parent, and a
   squash-only branch would quietly end that.
3. **`strict_required_status_checks_policy: false`** -- a branch need
   not be up to date with `main` to merge. Requiring it taxes every
   landing with a rebase, which this plan would have paid twice over
   already (phases 1 and 2 ran concurrently, and `size-ratchet` has a
   branch open now), and it does not buy what it appears to:
   [#411](https://github.com/shakenfist/development/issues/411), two
   green pull requests merging into a red `main`, is narrowed by
   required checks and closed only by a merge queue.
4. **`require_extra_approval_for_unattributed_changes: true`**, as on
   `actions`. This is the decision to argue with. It demands an
   approving review for commits GitHub cannot attribute to an account,
   which a sole maintainer with `required_approving_review_count: 0`
   cannot give, so on paper it can wedge a pull request. Kept, for
   three reasons: every commit that reaches a pull request here is
   authored by Mikal or by `shakenfist-bot`, and both resolve to
   accounts (checked on `d04a24b`, where the API names
   `shakenfist-bot` as author and committer); the bot's own writes to
   `main` arrive through the bypass rather than through a pull
   request; and `actions` has carried it since 2026-11-11 without
   trouble. Proof item 3 is what tests it here, and if it does wedge
   something the fix is to PUT the same body with the parameter
   `false` -- phase 4's criterion reads rule types and context names,
   not this parameter, so turning it off costs nothing that is
   measured. Mikal confirmed keeping it in the back brief on
   2026-11-28, with the escape understood. **It cost nothing:** both
   #456 and #457 reached `mergeStateStatus: CLEAN` with zero
   approvals, so the parameter admitted a code pull request and a
   review-only one without demanding a review neither could have.
5. **The PUT happens while this phase's pull request is open**, and
   that pull request merging is proof item 2. The pull request carries
   the record of the read-back, so it cannot land before the thing it
   records, and a real code pull request merging under the new rules
   is better evidence than a throwaway one. The consequence to accept:
   if a required context turns out never to report, the pull request
   that documents the way out is itself stuck behind the rule. The way
   out does not need it. If any required context never reports, the
   fix is an admin edit to the ruleset that removes the context, not
   the rule.

#### Step plan

Both steps are operator work, done by the management session together
with Mikal rather than by a sub-agent: a credential that can edit the
ruleset is involved, and a PUT replaces the ruleset wholesale.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Operator step. Pre-flight, in order, each read back before the next: (1) `gh api repos/shakenfist/development/rulesets/21132499 > ruleset-before.json` with the credential that will make the change, and confirm the file holds a `bypass_actors` key -- if GitHub omitted it, the credential cannot edit the ruleset and nothing else in this step may be run. Keep the file: PUTting it back restores the original verbatim, which is this phase's undo. (2) Confirm the four contexts still name jobs: `CI paths`, `Lint and test` and `Audit smoke run` in `.github/workflows/ci.yml`, `gitleaks` in `.github/workflows/secret-scan.yml`. Then build the body with the guarded transform below, diff it against `ruleset-before.json` to see that only `bypass_actors` and `rules` moved, and `gh api -X PUT repos/shakenfist/development/rulesets/21132499 --input ruleset-after.json`. Read back both `repos/shakenfist/development/rulesets/21132499` and `repos/shakenfist/development/rules/branches/main`, and check each of: four rule types present, `enforcement: active`, the `~DEFAULT_BRANCH` condition intact, exactly one bypass actor (`actor_id` 5538020, `actor_type` `Integration`, `bypass_mode` `always`), four contexts each with `integration_id` 15368, and `current_user_can_bypass: "never"` for the operator's own credential. Paste the read-back ruleset JSON, with `_links` removed, into this phase's section, and record the date and the time zone. |
| 3b | medium | sonnet | none | Record the proofs in this phase's section as each one lands, from the outputs the management session holds; do not re-derive them. Each proof gets a verdict and the identifier a reader can chase (a pull request number, a run id, a commit sha), or a sentence saying why it was declined. The six are listed under *Definition of done* below. The two with a clock on them: the `export-repo-config` pull request arrives the morning after the PUT and needs closing and reopening from Mikal's account before its checks report (`docs/audits/moving-ref-source-protection.md` says why), and the compliance-page push comes with the Sunday 18:00 UTC run or a `workflow_dispatch` -- a dispatch files and closes real issues fleet-wide, so take it only when that is acceptable. |

The transform, run against the live ruleset on 2026-11-28 and
confirmed to produce exactly the intended body. The guard is inside
it rather than before it: GitHub omits `bypass_actors` from a GET for
a credential it will not show them to, so appending to a key that is
not there would silently drop every actor the ruleset has, and
`error()` says so on stderr and exits non-zero where a separate
check would not. Dropping any existing `pull_request` or
`required_status_checks` rule before appending makes it idempotent,
so running it twice leaves one of each rather than two.

```bash
gh api repos/shakenfist/development/rulesets/21132499 > ruleset-before.json
jq -f ruleset.jq ruleset-before.json > ruleset-after.json
```

```jq
{name, target, enforcement, conditions,
 bypass_actors: (if has("bypass_actors")
                 then [.bypass_actors[] | select(.actor_id != 5538020)]
                      + [{actor_id: 5538020, actor_type: "Integration",
                          bypass_mode: "always"}]
                 else error("bypass_actors not visible to this credential: "
                        + "use one that can edit the ruleset") end),
 rules: ([.rules[] | select(.type != "pull_request"
                            and .type != "required_status_checks")]
         + [{type: "pull_request",
             parameters: {required_approving_review_count: 0,
                          dismiss_stale_reviews_on_push: true,
                          required_reviewers: [],
                          require_code_owner_review: false,
                          dismissal_restriction: {enabled: false,
                                                  allowed_actors: []},
                          require_last_push_approval: false,
                          required_review_thread_resolution: false,
                          require_extra_approval_for_unattributed_changes: true,
                          allowed_merge_methods: ["merge", "squash", "rebase"]}},
            {type: "required_status_checks",
             parameters: {do_not_enforce_on_create: false,
                          strict_required_status_checks_policy: false,
                          required_status_checks:
                            [{context: "CI paths", integration_id: 15368},
                             {context: "Lint and test", integration_id: 15368},
                             {context: "Audit smoke run", integration_id: 15368},
                             {context: "gitleaks", integration_id: 15368}]}}])}
```

#### Risks and mitigations

1. **A required context never reports and every pull request wedges.**
   The checked mitigation is the survey above rather than a promise:
   all four contexts were read reporting on a code pull request
   (#452) and on a review-only one (#428) before being required. If
   it happens anyway, Mikal edits the ruleset to remove the context,
   not the rule, and 3b records which context and why. **What
   happened was the softer variant this risk did not name:** a
   context that reports late rather than never, because the pool it
   runs on is contended. The survey proved each context *can* report
   and said nothing about when, which is the gap -- see *What the
   pool found*.
2. **The PUT drops a rule or the condition.** A ruleset PUT replaces
   rather than merges. Mitigated by building the body with the
   transform rather than by hand, diffing it against
   `ruleset-before.json` before sending, reading all four rule types
   and the condition back afterwards, and keeping
   `ruleset-before.json` as a verbatim undo. The management session
   checks each, in 3a.
3. **The App's key is cleared or expires and `prune-reviews` falls
   back.** `PUSH_TOKEN` is `steps.app-token.outputs.token ||
   github.token`, so an absent key means a `GITHUB_TOKEN` push, which
   the `pull_request` rule refuses because GitHub will not accept the
   built-in Actions app as a bypass actor. That fails the job and the
   workflow, so it is loud rather than silent -- which is the
   behaviour this phase wants, and is worth stating because the
   fallback reads like a safety net and is in fact a tripwire. The
   `bypass-actor-identity` criterion keeps measuring the environment
   and its branch policy weekly.
4. **A pull request carrying an unattributed commit cannot merge**
   (decision 4). Proof item 3 tests it; the fix is one PUT with the
   parameter `false`, and 3b records it if taken.

#### Definition of done

Each item is recorded in this section with the identifier a reader can
chase, or declined in writing with a reason.

1. `repos/shakenfist/development/rulesets/21132499` reads back with
   four rule types, `enforcement: active`, the `~DEFAULT_BRANCH`
   condition, exactly one bypass actor (5538020, `Integration`,
   `always`), the four contexts each with `integration_id` 15368, and
   `current_user_can_bypass: "never"`; and that JSON, less `_links`,
   is pasted here.
2. A direct `git push` of a trivial commit to `main` from Mikal's own
   credential is refused, with the refusal text recorded.
3. This phase's own pull request merged, with `CI paths`,
   `Lint and test`, `Audit smoke run` and `gitleaks` all reported.
4. A throwaway review-only pull request -- a branch touching only
   `REVIEWS.md` -- reached a mergeable state with `CI paths` and
   `gitleaks` success and `Lint and test` and `Audit smoke run`
   skipped, and was then closed unmerged with its branch deleted.
   Throwaway rather than the next real one because review-only pull
   requests here come from review sessions and may be weeks apart.
5. The first `prune-reviews` run on `main` after the PUT landed its
   push, and
   `repos/shakenfist/development/rulesets/rule-suites?time_period=day&rule_suite_result=bypass`
   names `shakenfist-review-marks[bot]` -- which is the only positive
   evidence that the bypass was used, as distinct from the rules not
   applying. `repos/shakenfist/development/deployments?environment=review-marks`
   is empty, as it is on `actions`.
6. The compliance page landed again -- the Sunday run, or a
   dispatched one -- and the `export-repo-config` pull request opened,
   reported its four contexts after a close and reopen, and merged,
   leaving
   `.github/exported-config/ruleset-protect-default-branch-history.json`
   holding the four rules and the bypass actor.

#### Back brief and gate

Two things were asked of Mikal before the PUT, both cheap to answer
and expensive to discover afterwards, and both answered on 2026-11-28
when this phase was planned:

* Open question 4, which this plan had been holding for phase 3: does
  anything of yours push straight to `main` -- a habit, a script, a
  plan close-out landed directly? **Nothing does.** So the only
  writers the rules have to accommodate are the two in the *Situation*
  table, both of which now push as the App.
* Decision 4, `require_extra_approval_for_unattributed_changes`.
  **Kept**, for consistency with `actions`, with the escape in that
  decision understood.

The PUT itself needs no gate beyond those: `ruleset-before.json` makes
it a reversible change, and 3a does not send it until the body has
been diffed against that file. What is left for the implementing
session is the back brief the template asks for -- a statement of what
it understands 3a and 3b to be -- before it touches the ruleset.

#### What was done

**Step 3a, 2026-11-28 12:20 AEDT.** Pre-flight first: the GET for
`ruleset-before.json` carried a `bypass_actors` key, visible and `[]`,
so the credential (`mikalstill`) can edit the ruleset and the
transform's guard had something to guard; the four contexts were
confirmed to name jobs at `ci.yml:68`, `ci.yml:115`, `ci.yml:159` and
`secret-scan.yml:29`; and the built body, diffed against
`ruleset-before.json`, moved `bypass_actors` and appended two rules
and nothing else -- `name`, `target`, `enforcement`, the
`~DEFAULT_BRANCH` condition and the `deletion` and `non_fast_forward`
rules came through untouched. The PUT was accepted. Read back, less
`_links`:

```json
{
  "id": 21132499,
  "name": "Protect default branch history",
  "target": "branch",
  "source_type": "Repository",
  "source": "shakenfist/development",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "exclude": [],
      "include": [
        "~DEFAULT_BRANCH"
      ]
    }
  },
  "rules": [
    {
      "type": "deletion"
    },
    {
      "type": "non_fast_forward"
    },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "required_reviewers": [],
        "require_code_owner_review": false,
        "dismissal_restriction": {
          "enabled": false,
          "allowed_actors": []
        },
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "require_extra_approval_for_unattributed_changes": true,
        "allowed_merge_methods": [
          "merge",
          "squash",
          "rebase"
        ]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          {
            "context": "CI paths",
            "integration_id": 15368
          },
          {
            "context": "Lint and test",
            "integration_id": 15368
          },
          {
            "context": "Audit smoke run",
            "integration_id": 15368
          },
          {
            "context": "gitleaks",
            "integration_id": 15368
          }
        ]
      }
    }
  ],
  "node_id": "RRS_lACqUmVwb3NpdG9yec5Ekk5MzgFCdNM",
  "created_at": "2026-08-21T17:25:37.853+10:00",
  "updated_at": "2026-11-28T12:20:18.421+11:00",
  "bypass_actors": [
    {
      "actor_id": 5538020,
      "actor_type": "Integration",
      "bypass_mode": "always"
    }
  ],
  "current_user_can_bypass": "never"
}
```

`repos/shakenfist/development/rules/branches/main` reports all four
rule types, each with `ruleset_id` 21132499,
`ruleset_source_type: "Repository"` and `ruleset_source:
"shakenfist/development"` -- no organisation ruleset reaches the
branch, which is what makes phase 4's criterion able to read the
enforcement at all.

**Step 3b, the proofs.** Numbered as the *Definition of done* above.

1. **Done.** The read-back is quoted in full above: four rule types,
   `enforcement: active`, the `~DEFAULT_BRANCH` condition, one bypass
   actor, the four contexts with `integration_id` 15368, and
   `current_user_can_bypass: "never"`.
2. **Left for Mikal, and why.** The direct-push refusal was to be
   proved by pushing this branch's tip to `main` -- the least harmful
   payload available, since that content is bound for `main` through
   #456 anyway. Claude Code's auto-mode classifier refused the
   command as a destructive action, which is the harness protecting
   the default branch rather than the ruleset doing so, and is not
   evidence either way. It is one line for Mikal to run, and the
   expected answer is GH013 and "Changes must be made through a pull
   request".
3. **Checks done; the merge is Mikal's.** This phase's pull request
   is #456. At head `5cf1a49` all four required contexts report
   success -- `CI paths`, `gitleaks`, `Lint and test` and
   `Audit smoke run` -- and GitHub reads it as `mergeable: MERGEABLE`
   with `mergeStateStatus: CLEAN`. It sat at `BLOCKED` for about forty
   minutes first, on `gitleaks` alone, for the reason under *What the
   pool found*; the rollup named the missing context throughout, so
   the `pull_request` rule and decision 4 were never what held it.
4. **Done.** The throwaway was #457, a branch touching only
   `REVIEWS.md`. `CI paths` and `gitleaks` report success and
   `Lint and test`, `Audit smoke run`, `Automated reviewer` and
   CodeQL's `Analyze` all report skipped, and it reached
   `mergeable: MERGEABLE` / `mergeStateStatus: CLEAN` -- a skipped
   required check satisfies the rule, which is the whole of what
   phase 1 bought and the thing a required-checks rule would
   otherwise have wedged forever. Closed unmerged on 2026-11-28, with
   its remote and local branches and its worktree removed.
5. **Pending**, until the next push to `main`. Worth recording early:
   `rulesets/rule-suites?time_period=day` already attributes this
   repository's review-mark pushes to `shakenfist-review-marks[bot]`
   -- most recently 2026-11-28 10:13 AEDT -- with `result: "pass"`,
   because before the PUT there was nothing to bypass. So what item 5
   is waiting to see is that `result` becoming `"bypass"`, not the
   actor name, which is already established.
6. **Pending.** Mikal chose the Sunday 18:00 UTC run over a dispatch,
   which files and closes real issues across 24 repositories.

#### What the pool found

A finding this phase produced rather than inherited, recorded here
because it changes what a required context costs.

`gitleaks` is the only one of the four required contexts that runs on
the ephemeral VM pool: `runs-on: [self-hosted, vm, debian-13, s]`, and
the workflow's own comment says why it cannot move -- `gitleaks` is
packaged from Debian 13 onward and bookworm has no package. The other
three run on `[self-hosted, static]`, where two persistent runners sit
idle.

That pool was saturated when this phase applied the ruleset, and had
been for hours: instar's `Functional Tests` had been `in_progress`
since 2026-11-27 23:46 UTC holding 11 of the 12 VM slots, exactly one
runner carried the `s` label and it was busy, and `Mermaid lint` for
`advisory-ignore-review` had been queued 2h16m. Five branches' secret
scans were queued behind it, three of them nothing to do with this
plan. So the contention predates the PUT and is not caused by it.

What the PUT changed is the consequence. Before it, a starved `s` pool
delayed an advisory check; after it, it holds every merge on this
repository, because the one check that cannot run on a static runner
is now required. The ruleset is working exactly as designed -- a
required check that has not reported is not a check that passed --
and the cost is that `main`'s availability now depends on that pool's.
Dropping `gitleaks` from the contexts would buy the availability back
at the price of the gate the `secret-handling` audit asks for, so it
is recorded as a cost to decide on rather than fixed here, and filed
as #461 with the directions that were considered.

### Phase 4: measure it

Planning effort: medium -- an entry in an existing table. Merged:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Add `'shakenfist/development'` to `MovingRefSourceProtection.MOVING_REF_REPOS` in `scripts/audit/checks/github_config.py` with the contexts phase 3 applied, and update the class comment that says the table is not the whole set. Add tests beside the existing `moving-ref-source-protection` tests (find them with `grep -rn MovingRefSourceProtection scripts/tests/`) covering development passing with its own contexts and failing when one is dropped, following the `CheckTestCase` convention in `docs/consistency-audits.md`. No frozen metadata changes: the id, spec and issue title are unchanged. |
| 4b | medium | sonnet | none | In `docs/audits/moving-ref-source-protection.md`: replace *Known gap* with a sentence that the table now holds both moving-ref sources; generalise the paragraph that names `actions`' four contexts so each repository's contexts are named, with development's; keep the hand-written structure and add no compliance table or marker. Then run `python3 scripts/audit-check.py --repo-path . --repo-name development` and confirm `moving-ref-source-protection` moves from N/A to pass and nothing else moved. The pull request body closes #393. |

### Phase 5: push audit

Planning effort: medium. Merged:

Run `PUSH-AUDIT.md` over the merge commits recorded in phases 1, 2
and 4, read against their first parents, and over phase 3's
read-back. Questions specific to this plan for the security
sub-agent: does any job other than `prune-reviews` and `push-docs`
reach the `review-marks` environment; can a pull request change what
`push-docs` pushes before it runs on `main`; and does any path leave
the compliance page silently stale rather than reported by
`report-failure`.

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

When creating a new master plan from this template, add one row to
the table in `docs/plans/index.md`: the date the plan was written,
a link to it, a one-line intent, and its status from the
vocabulary above. Rows run oldest first. One row per master plan,
never one per phase -- the phases are tracked in the plan's own
Execution table, and duplicating them in the index is how the two
drift apart.

There is no phase-arithmetic column and no `order.yml` here; both
belong to repositories whose documentation is published through a
generated navigation. The `plan-index` criterion checks the
columns this index actually has.

The index row carries the whole-plan status, so it only reaches
`Complete` once every phase has been completed, abandoned or
superseded. Update it as the plan progresses, not only at the end.

Once a plan has been finished for 90 days it is retired as
`docs/plan-retirement.md` describes: its reasoning is moved into
docs/ and its *Future work* is filed as issues. Write both so they
survive being read by someone who was not here.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

* A merge queue on this repository, which would close #411 rather
  than narrow it (open question 3).
* `export-repo-config` in `shakenfist/actions` opening its pull
  request with an App token, so that CI starts without a close and
  reopen, here and on `actions` (open question 5).
* #399: a failed re-review is invisible in the pull request's checks.
  Required checks make the visible checks matter more, which makes
  this gap matter more.
* #352: a read identity for the audit that can see `bypass_actors`,
  which would turn this plan's bypass read-back from a reviewer's
  confirmation into a measurement. Phase 3's survey found a cheaper
  half-measure worth considering with it: the daily export tracks
  `bypass_actors` in
  `.github/exported-config/ruleset-protect-default-branch-history.json`,
  because the export workflow's `GITHUB_TOKEN` can read the key the
  audit's token is refused, so a criterion reading the exported file
  could measure the bypass list without widening any token.
* #461: `main`'s merge gate is now coupled to the ephemeral runner
  pool, because `gitleaks` is the one required context that cannot run
  on a static runner (it needs trixie for the package). Filed from
  phase 3's *What the pool found*.

### Bugs fixed during this work

None yet. Related issues, as of writing: #393 is the issue this plan
answers and phase 4 closes; #411 is narrowed by phase 3 and stays
open for the merge-queue decision.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
