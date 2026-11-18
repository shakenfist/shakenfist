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
   `mermaid-lint` (trigger-filtered).
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
   is one.
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
| 1. CI that reports on every pull request | In progress | |
| 2. Push identities for automated writers | In progress | |
| 3. Apply the ruleset | Not started | |
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

### Phase 1: CI that reports on every pull request

Planning effort: medium -- `shakenfist/actions`' `ci.yml` is the
worked example. Merged:

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

### Phase 2: push identities for automated writers

Planning effort: high -- it moves a bypass-holding key into a second
workflow. Merged:

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

### Phase 3: apply the ruleset

Planning effort: medium -- the template is in
`docs/audits/moving-ref-source-protection.md` and
`docs/audits/bypass-actor-identity.md`. Merged: not applicable (a
settings change; record the date and the read-back instead).

Operator steps, with a credential that can edit the ruleset:

1. Build the PUT body from a GET of ruleset `21132499` with the
   guarded `jq` transform in `bypass-actor-identity.md`'s *Template*,
   adding the review-marks App to `bypass_actors` and these rules:
   `pull_request` with `required_approving_review_count: 0`, and
   `required_status_checks` with the contexts open question 2
   settles, each with `integration_id: 15368`.
2. PUT it, then read back `rules/branches/main` and the ruleset:
   four rule types, `enforcement: active`, one bypass actor,
   `current_user_can_bypass: never` for your own credential.
3. Prove it: a direct `git push` to `main` from your credential is
   refused; a throwaway code pull request offers a merge once its
   checks finish; a throwaway review-only pull request (or the next
   real one) offers a merge with the gated jobs skipped; the next
   `prune-reviews` run lands its push; the next morning's
   `export-repo-config` pull request starts CI after close-and-reopen.
   Close the throwaway pull requests unmerged.

If any required context never reports, the way out is an admin edit
to the ruleset: remove the context, not the rule.

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
  confirmation into a measurement.

### Bugs fixed during this work

None yet. Related issues, as of writing: #393 is the issue this plan
answers and phase 4 closes; #411 is narrowed by phase 3 and stays
open for the merge-queue decision.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
