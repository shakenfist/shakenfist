# Consistency audits v2

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly: `docs/audits/`, the audit
tooling in `scripts/`, `.github/workflows/consistency-audit.yml`, and
the reusable workflows and composite actions in `shakenfist/actions`.
Ground your answers in what the tooling does today rather than in what
this plan said it would do -- the two have diverged in several places,
and the divergences are the interesting part. Where a question touches
on GitHub Actions security (token scope, what a cross-repository
reusable workflow can and cannot grant itself, untrusted pull request
input), research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

## Situation

My Shaken Fist project consistency audits started out as a thought
bubble, but they've grown into something I think is really useful.
On the other hand, I think I have also outgrown the current process.

## Mission and problem statement

In terms of what I've learn from this process so far, I think there are
three main points:

* Listing the various things I audit for in the
  `PROJECT-CONSISTENCY-AUDITS.md` single markdown file is becoming
  unwieldy, as well as reducing the parallelism that I can apply -- if
  for example I had a directory of audit items then I could spawn an
  agent per item and perform the audits in parallel.

* Secondly, I think work item tracking in `PLAN-consistency.md` is
  similarly awkward and I'd be better off tracking outstanding items in
  github issues, although I am unsure if they should be issues on the
  shared `development` repository or on the target project itself. If
  they were tracked in each project I think we'd need a label like
  "consistency" to make them easier to surface.

* Finally, I think those issues should more strongly link to a
  consistent implementation approach -- the templates are a good idea,
  but often the model isn't aware they exist until its too late. If the
  issue linked to the specific template to use that would be helpful.

This is especially true because it occurs to me that my current automated
review / fix / retest flow is a bit weird. Specifically, its weird that
the reviewer isn't also the thing which proposes fixes for what it
finds and instead needs to somehow convey the problems to a new instance
of the model. Given we also only do one automated review per PR without
human intervention, we should also be safe to automatically kick off a
retesting run after the automated fixes have been proposed. That is,
I'd like to squash those three workflows into one, and I'd like to
improve how we both track the rollout for that, but also actually roll
it out. This should also include ensuring the absolutely maximum amount
of the repeated implementation is in the `actions` repository, and not
duplicated across the various projects.

I think it is also weird that the audits happen on copies of the repositories
that are my current working clones, which might not be representative
of the actual state of the commited code. Perhaps the audit jobs themselves
should be running on a CI worker with a fresh clone?

## Analysis and recommendations

### Architecture: modular audit items

The single `PROJECT-CONSISTENCY-AUDITS.md` file currently defines 12
audit criteria in ~24KB of prose. The proposed move to a directory of
individual audit items is sound and directly addresses the parallelism
bottleneck. I'd suggest the following structure:

```
development/
  audits/
    README.md              # overview, how to add a new audit
    llm-tooling.md         # one file per audit criterion
    release-process.md
    ci-review-automation.md
    renovate.md
    ...
```

Each audit file should have a consistent structure:

```markdown
# Audit: <name>

## What we check
<concise description of the audit criterion>

## Template
Template: `templates/<name>/`
See: `templates/<name>/README.md`

## Projects
| Project | Status | Issue |
|---------|--------|-------|
| shakenfist | compliant | - |
| imago | compliant | - |
| occystrap | non-compliant | #42 |
```

This gives us three concrete benefits:

1. **Parallelism** -- an agent per audit file, each checking all
   projects against one criterion. This is the natural grain for
   parallel work because each criterion has its own template and
   its own set of files to check.

2. **Discoverability** -- each audit file directly links its
   template directory, solving the problem of models not finding
   templates until too late.

3. **Incremental addition** -- adding a new audit criterion is just
   adding a new file. No merge conflicts with other in-progress
   audits.

### Work tracking: GitHub issues on target projects

Issues should live on the target project, not on `development`. The
reasoning:

* The person fixing the issue needs to work in that repo. Having
  the issue in the same repo means it shows up in their `gh issue
  list`, their project board, and their PR cross-references.

* A `consistency` label on each project is lightweight and lets us
  aggregate across repos with a GitHub search like
  `org:shakenfist label:consistency is:open`.

* The `development` repo remains the authority on *what* to audit
  and *how*, but the tracking of *where we are* for each project
  lives where the work happens.

Each issue should follow a template:

```
Title: Consistency: <audit name>
Labels: consistency
Body:
  This project is not yet compliant with the <audit name>
  consistency audit.

  Audit spec: development/audits/<name>.md
  Template: development/templates/<name>/README.md

  Steps to implement:
  <copied from the template README>
```

This directly links the issue to both the spec and the template,
so any agent or human picking up the issue has everything they need.

### Consolidating review / fix / retest

The current three-workflow dance (`pr-re-review`, `pr-address-comments`,
`pr-retest`) requires human intervention between steps. The observation
that "the reviewer should also propose fixes" is correct -- when a
review finds issues, the same context that identified the problem is
best positioned to propose a fix.

*(Written 2026-03. This turned out to be wrong, and usefully so: the
human intervention between review and fix was the feature, not the
friction. See Phase 3 item 4 below, which records the decision to
retire the comment addresser rather than combine it with anything.)*

This should remain a separate workflow rather than being folded into
the existing review or test workflows, but it should have two trigger
modes:

1. **Automatic** -- triggered when a PR's functional tests pass in
   CI, provided the PR has not already received an automated review.
   This is the primary mode: the review happens at the natural point
   where we know the code works and is ready for feedback, without
   any human having to remember to invoke it.

2. **Manual** -- triggered via a bot command
   (`@shakenfist-bot please review and fix`) for cases where a
   human wants to re-run the review after pushing changes, or where
   the automatic trigger didn't fire for some reason.

The "has this PR already been reviewed" gate is important to avoid
noise. The simplest implementation is to check for the presence of
a review comment from the bot -- if one exists, skip the automatic
trigger and require the manual command instead.

The combined workflow would then:

1. Run the Claude review (existing `review-pr-with-claude` action).
2. If the review produces actionable findings with `action: fix`,
   immediately apply fixes in a follow-up commit on the same PR.
3. Re-run tests after the fix commit to confirm the fixes don't
   break anything.

The key constraint is security -- the fix step needs write access to
the PR branch, which means the two-checkout security model from
`pr-address-comments.yml` must be preserved. The combined workflow
should still use the untrusted checkout for reading PR code and the
trusted checkout for the tools that write back.

Since the automatic trigger only fires once (gated by "no prior
review") and manual re-runs require explicit human action, the risk
profile is the same as today -- we just remove the manual step
between review and fix for the first pass.

This combined action should live in `shakenfist/actions` as a
reusable workflow so that each project only needs a thin trigger
workflow.

### Running audits on CI rather than local clones

Running audits on local working copies is problematic because:

* Uncommitted changes may mask or create false audit findings.
* The audit results aren't reproducible by others.
* There's no audit trail of when audits ran and what they found.

A scheduled GitHub Actions workflow in `development` that clones each
target repo fresh and runs the per-criterion checks would solve this.
The workflow could:

1. Clone each project repo at HEAD of its default branch.
2. Run each audit criterion check (one job per criterion per project,
   maximising parallelism).
3. For failures: create or update a GitHub issue on the target project
   using the template above.
4. For passes: close the corresponding issue if one exists.

This gives us automated drift detection -- if a project regresses on
a criterion it previously passed, an issue gets reopened automatically.

### Maximising reuse in `actions/`

The current shared actions (`pr-bot-trigger`, `review-pr-with-claude`,
`export-repo-config`) are the right pattern. The combined review+fix
workflow should follow the same approach. Additionally, audit check
scripts themselves could live in `actions/` as composite actions:

```yaml
# In each project's .github/workflows/consistency-audit.yml
jobs:
  audit:
    uses: shakenfist/actions/.github/workflows/consistency-audit.yml@main
    with:
      project: ${{ github.repository }}
```

This means adding a new audit criterion requires:

1. Adding the check logic to `actions/`.
2. Adding the audit spec file to `development/audits/`.
3. No changes to individual project repos.

### Migration path

Rather than a big-bang migration, I'd suggest:

1. **Create the `audits/` directory** -- extract each criterion from
   `PROJECT-CONSISTENCY-AUDITS.md` into its own file with the
   structure above. Keep the original file as a read-only reference
   until migration is complete.

2. **Add the `consistency` label** to all project repos and create
   issues for known non-compliant items using the issue template.

3. **Build the CI audit workflow** in `development` that checks one
   criterion (start with the simplest, like "has AGENTS.md") across
   all projects. Iterate until the pattern is solid.

4. **Build the combined review+fix action** in `shakenfist/actions`
   and pilot it on one project before rolling out.

5. **Retire `PLAN-consistency.md`** once all tracking has moved to
   GitHub issues.

## Open questions

Answered:

* **How often should the CI audit run?** Daily, at 06:00 UTC, after
  `export-repo-config` at 00:30. Noise has not been a problem, because
  the run only files an issue on a transition rather than every
  morning.

* **Should fixes be committed to the PR branch or proposed as review
  suggestions?** Neither, in the end: fixes stay behind an explicit
  `@shakenfist-bot please address comments`, so a human reads the
  review before any commit is authored. See Phase 3 item 4.

Still open:

* Should audit issues be auto-assigned to anyone, or left unassigned
  for triage? Unassigned by default today, with 116 open across the
  organisation.

* Do we want a dashboard (e.g. a generated README table or GitHub
  project board) that shows compliance status across all repos at a
  glance? The per-criterion tables answer "who fails this check"; there
  is nothing that answers "what is the state of the fleet".

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Modular audit specs | Complete | Reconstructed; see Phase 5 |
| 2. CI-based audit runner | Complete | Not recoverable; see Phase 5 |
| 3. Automatic review, and the fix/retest split | Complete | Not recoverable; see Phase 5 |
| 4. Cleanup | Complete | Reconstructed; see Phase 5 |
| 5. Push audit | Not started | |

This table was added on 2026-10-16, when phases 2 and 3 were closed
out; until then the phases were tracked only by the `-- DONE` suffix
on each heading below. The landing commits predate the
`plan-push-audit-phase` convention and were reconstructed after the
fact, so they sit in Phase 5's own section rather than in this table.

### Phase 1: Modular audit specs -- DONE

1. ~~Create `audits/` directory with README explaining the
   structure.~~
2. ~~Extract each of the 12 criteria from
   `PROJECT-CONSISTENCY-AUDITS.md` into individual files.~~ 13
   audit files created.
3. ~~Ensure each file links to its template and lists per-project
   status.~~
4. ~~Add `consistency` label to all project repos.~~ Added to 10
   repos initially; ryll (which had no GitHub repo at the time)
   gained one at shakenfist/ryll and the label was added in July
   2026, making 11.
5. ~~Create GitHub issues for all known non-compliant items.~~ 35
   issues created across 10 repos.

The structure has since outgrown those numbers, which is the point of
it: by August 2026 `audits/` held 34 criteria rather than 13, backed
by 37 registered checks (some criteria, such as `workflow-standards`,
decompose into several), and the audit matrix covered 17 repositories
rather than 10. On 2026-10-16 `docs/audits/` holds 55 criteria backed
by 62 registered checks, over a matrix of 24 repositories. `actions`
and `development` were both moved off the exempt list -- the fleet
depends on `actions` for every composite action it runs, and
`development` is where these rules are written, so an exemption there
is an exemption the authors of the standard wrote for themselves.

### Phase 2: CI-based audit runner -- DONE

1. ~~Write audit check scripts (shell or Python) for each
   criterion.~~ In August 2026 `scripts/audit-check.py` ran 37
   registered checks across 34 criteria (4 judged by reading rather
   than matching: security-sanitization, console-logging,
   python-version, test-coverage).
2. ~~Create a scheduled workflow in `development` that runs all
   checks across all projects.~~
   `.github/workflows/consistency-audit.yml` ran daily at 06:00 UTC
   over 17 repositories. Since #210 (2026-10-03) it runs weekly, at
   18:00 UTC on Sunday, because a daily run filed and closed issues
   faster than the fleet could absorb them.
3. ~~Add issue creation/closure automation for audit results.~~
   `scripts/audit-manage-issues.py` creates issues for failures
   and closes them when checks pass, using exact title matching
   against existing manually-created issues.
4. ~~Verify drift detection works after first CI run.~~ Done. The
   `AUDIT_TOKEN` secret is configured with cross-repo issue
   permissions, and the daily run has been green other than the
   2026-08-20 outage described under "Bugs fixed" below. Drift shows
   up two ways: a table row flipping to non-compliant, and a
   previously closed issue reopening.
5. ~~Regenerate the per-project compliance tables from the audit
   results rather than by hand.~~ `scripts/audit-update-docs.py`
   rewrites the marker blocks in `audits/*.md` and
   `scripts/commit-audit-docs.sh` pushes them back, so the published
   status cannot drift from what the audit actually measured.
6. ~~Make a failed scheduled run visible to a human.~~ The
   `report-failure` job files or updates an `audit-failure` issue on
   this repository. This matters more than it sounds: while the audit
   is down the tables keep showing the previous run's verdicts, so a
   broken audit looks like a healthy one from the outside.
7. ~~Check the audit matrix against the organisation's actual
   repository list, so a repository added to the org is not silently
   unaudited.~~ Done: issue #40 is closed, and the `scope-coverage`
   check (`docs/audits/scope-coverage.md`) compares the matrix with
   the in-scope and excluded lists in `docs/audits/README.md`. This
   item read as open until the phase 2 close-out on 2026-10-16.

### Phase 3: Automatic review, and the fix/retest split -- DONE

This phase was planned as one workflow doing review, fix and retest.
What was built automates the *review* half and deliberately leaves fix
and retest as explicit human commands, which is a better answer than the
one planned and is recorded here as a change of direction rather than as
outstanding work.

`shakenfist/actions/.github/workflows/pr-auto-review.yml` is a reusable
workflow. A calling project adds a job naming its own test jobs in
`needs:`, so "review only after the tests pass" is an ordinary job
dependency rather than a `workflow_run` trigger plus a gate: a job
skipped because a dependency failed never starts the workflow. The
caller supplies `pull-requests: write` and `issues: write`, because a
cross-repository reusable workflow cannot grant itself more token scope
than its caller has, and callers must not add `secrets: inherit` --
nothing in the chain reads a secret.

1. ~~Design the combined workflow preserving the two-checkout security
   model.~~ Superseded. The review half needs no write checkout at all:
   it authenticates with `github.token` under permissions the caller
   grants, and reads the diff through `gh pr diff`. There was nothing
   for the two-checkout model to protect here, and nothing left for it
   to protect anywhere once item 4 was done: it existed for the fix
   step's write access to the pull request branch, and that step was
   removed rather than combined.
2. ~~Implement the "already reviewed" gate.~~ It lives in the
   `review-pr-with-claude` action, which skips when it finds an existing
   `shakenfist-bot` review unless its `force` input is set.
   `pr-auto-review.yml` never passes `force` and `pr-re-review.yml`
   always does, so an explicit human request is the only route to a
   second review. Doing the check over the API rather than from a
   checkout also let callers delete their `check-bot-commit` job.
3. ~~Add the automatic trigger on successful functional test
   completion.~~ The caller's `needs:` list, as above.
4. ~~Add the manual trigger via `@shakenfist-bot please review and
   fix`.~~ Answered by deletion rather than by building it. The
   comment addresser was retired rather than combined with the
   reviewer: it went unused, because review findings are worked
   through interactively with the reviewer, and a bot authoring
   commits from a review no human had read is exactly what stopped
   anyone reaching for it. A retired addresser leaves no fix step for
   a review to be combined with, so the two commands that survive are
   `please re-review` and `please retest`. Removed in PR #43 -- from
   this repository and from the template. At the time eleven of the
   sixteen audited projects still carried the workflow; on the
   2026-10-11 run the `ci-review-automation` check flags only sfui
   and clingwrap for it, and files an issue against each until it is
   gone.
5. ~~Implement as a reusable workflow in `shakenfist/actions`.~~
6. ~~Pilot it.~~ Piloted on `actions` and on this repository, rather
   than on `shakenfist` as originally written.
7. ~~Roll out to the remaining projects.~~ Handed off to the audit
   on 2026-10-16 rather than finished here. That is not the same as
   done, and is recorded as a decision: the rollout is exactly what
   the `ci-review-automation` check measures, and it files and closes
   one issue per repository, so holding this plan open until the
   fleet complies would track the same work twice. That would also
   leave the plan unable to close for reasons that have nothing to do
   with its own work.

   On the 2026-10-11 compliance run, nine repositories fail the
   check, each with an open issue:

   | Repository | Issue | Gap |
   |------------|-------|-----|
   | sfui | shakenfist/sfui#26 | Hand-rolled trigger; retired addresser still deployed |
   | clingwrap | shakenfist/clingwrap#121 | Hand-rolled trigger; retired addresser still deployed |
   | agent-python | shakenfist/agent-python#145 | Unconfirmed checkout; same-repo output not exported |
   | client-python | shakenfist/client-python#408 | Unconfirmed checkout; same-repo output not exported |
   | divergulent | shakenfist/divergulent#120 | Unconfirmed checkout; same-repo output not exported |
   | library-utilities | shakenfist/library-utilities#62 | Unconfirmed checkout; same-repo output not exported |
   | cloudgood | shakenfist/cloudgood#1 | No `pr-re-review.yml` |
   | kerbside-client | shakenfist/kerbside-client#5 | No re-review, retest or shared reviewer |
   | uncalibrated-sextant | shakenfist/uncalibrated-sextant#16 | No re-review, retest or shared reviewer |

   sfui and clingwrap come first, because theirs is a security gap
   rather than an untidiness: a hand-rolled trigger does not inherit
   `shakenfist/actions/pr-bot-trigger@main`'s refusal to act on fork
   pull requests, and the `pr-ref` it substitutes is a
   head-repository branch name that callers hand straight to
   `checkout` and `git push` against their own repository. The
   retired addresser they also carry holds `contents: write` on the
   pull request branch. The `consistency-fix` skill is the vehicle,
   one issue per commit.

### Phase 4: Cleanup -- DONE

1. ~~Retire `PLAN-consistency.md` once issue tracking is
   live.~~ Done 2026-08-22. The file is now a record of what the
   plan was and why it was replaced; its per-project checklists
   were not carried over.
2. ~~Archive `PROJECT-CONSISTENCY-AUDITS.md` with a pointer to the
   new `audits/` directory.~~ Done 2026-08-23, by dissolving it
   rather than archiving it. The concern that held this up was that
   the file was the only place the *reasoning* behind a rule was
   written down, so archiving it would leave the machine checks with
   nothing to explain themselves against. That was a reason not to
   move the prose to an attic, not a reason to keep a second
   authority: each section went into the `docs/audits/<criterion>.md`
   it described, so the reasoning now sits beside the check it
   justifies, and the in-scope and excluded project lists went into
   `docs/audits/README.md`. `ARCHITECTURE.md` and `AGENTS.md` no
   longer point at a root file, and the whole tree moved under
   `docs/` in the same change so that it publishes.
3. ~~Move the operational documentation into `docs/`.~~ Done.
   `docs/consistency-audits.md` now describes the system: the three
   layers a criterion lives in, what each stage of a daily run does,
   how issues are filed and closed and why titles are an interface,
   how the compliance tables are regenerated, how to add a criterion,
   how to bring a repository into scope, and how to test a change
   before it reaches the fleet. `AGENTS.md` drops from 169 lines to
   101 and `ARCHITECTURE.md` from 125 to 112, each keeping a summary
   and a link -- which is what `llm-doc-structure` asks of them, and
   `AGENTS.md` is loaded into every session held here.

### Phase 5: Push audit -- NOT STARTED

Run `PUSH-AUDIT.md` over the whole plan's work rather than over the
last phase's diff alone -- the interactions between phases are most
of what a whole-plan audit is for. Planned on 2026-10-16, after
phases 2 and 3 were closed out (see the Execution table).

**Planning effort:** high. **Review effort:** high for wave 2, per
the runbook; the triage step is the management session's.

#### Scope

In:

- This repository's runbook, `PUSH-AUDIT.md`, over the reconstructed
  ranges of phases 1 and 4, and over the current state of the paths
  the plan owns for phases 2 and 3 (listed below).
- `shakenfist/actions`' own `PUSH-AUDIT.md` over the current state of
  the four phase 3 paths that live there. That runbook exists, so the
  shared block's "a phase that landed in another repository is audited
  against that repository's default branch" applies directly.
- Findings fixed in their own pull requests (one in this repository,
  one in `actions` if it has any), and each finding not fixed declined
  in writing here.

Out:

- The fleet rollout of review automation. Phase 3 item 7 handed that
  to the `ci-review-automation` issues, and this phase does not take
  it back.
- `scripts/review-tracking.py` and its tests. Code review tracking
  has a plan of its own.
- `templates/`. The audits point at it, but its contents are
  `PLAN-plan-template-blocks.md`'s and other plans' work.
- Future work entries. A finding that turns out to be one of them is
  cross-referenced, not fixed.

#### Landing record

The `plan-push-audit-phase` block asks for the commit each phase
landed under. This plan predates that rule, so the record below was
reconstructed afterwards. What could be recovered is given; what
could not is named as such.

| Phase | Merged |
|-------|--------|
| 1. Modular audit specs | `a56f5f6`, `7c5b540`, `200e007` (2026-03-08, direct to `main`) |
| 2. CI-based audit runner | `4a970a6`, `fed8aa4`, then not recoverable |
| 3. Automatic review and the fix/retest split | not recoverable |
| 4. Cleanup | `b8bf764` (#41), `ada14ef` (#44), `e3d1333` (#47) |

The phase 4 merges contain the plan's work, but not only the plan's
work: #47 landed `54df816`, which published the audit specs under
`docs/`, alongside unrelated review-tracking fixes. Recording a merge
commit buys a range that certainly contains the phase, at the cost of
a range that may contain more. That is the right trade against the
alternative, a range that reliably contains neither, but the audit
says which files it read rather than reporting the whole merge as
this plan's diff.

Phases 2 and 3 are the honest gap. Phase 2 shipped one runner in
March 2026 and then grew from 13 criteria to 55 over seven months,
in commits interleaved with everything else this repository did.
Phase 3 was built largely in `shakenfist/actions`, so it spans two
repositories. No commit range recovers either without also
recovering unrelated work, which is the failure v2 names. So the
audit for those two phases reads the current state of the paths the
plan owns rather than a diff, and says so in its result. An audit
that states what it could not scope is a result; one that quietly
diffs nothing is not.

This plan is the worked example for why v2 requires recording landings
as phases land rather than deriving them afterwards: seven months on,
the range genuinely is gone.

#### What the survey found

Every claim below was checked against `origin/main` at `a19f647`
on 2026-10-16.

1. **All nine recorded commits exist and are ancestors of `main`.**
   On the first-parent line, phase 1 and the start of phase 2 are
   five consecutive direct commits, so they reduce to two ranges with
   the runbook's caret: `a56f5f6^..200e007` (16 files, +743/-9) and
   `4a970a6^..fed8aa4` (5 files, +1052/-8). The phase 4 merges are
   `b8bf764^1..b8bf764` (+111/-791), `ada14ef^1..ada14ef`
   (+765/-241) and `e3d1333^1..e3d1333` (69 files, +1739/-1783).
2. **The owned-path list this section used to give was too short to
   audit against.** It named `scripts/audit-check.py`,
   `scripts/audit_common.py`, `scripts/audit-manage-issues.py`,
   `scripts/audit-update-docs.py`, `docs/audits/`,
   `.github/workflows/consistency-audit.yml` and
   `docs/consistency-audits.md`. Since then the checks have moved
   into a package. `scripts/audit/` (11,382 lines under `checks/`
   alone) is now most of the runner, and the list also missed
   `scripts/audit-raised.py`, `scripts/audit_snapshot.py`,
   `scripts/check-audit-smoke.py`, `scripts/commit-audit-docs.sh`,
   `tools/audit-snapshot.sh` and the audit tests in `scripts/tests/`.
   Auditing the old list would have skipped most of the code.
   Corrected below.
3. **The phase 3 paths in `actions` exist** and total 3,124 lines:
   `.github/workflows/pr-auto-review.yml`,
   `.github/workflows/pr-re-review.yml`, `review-pr-with-claude/`
   and `pr-bot-trigger/`. `actions` has a `PUSH-AUDIT.md` that
   reads the same `AUDIT_RANGE` variable this repository's does, so
   the empty-tree technique in decision 2 carries over unchanged.
   `actions` has no open pull requests. 5a and 5c read `actions`
   at its `origin/main` and touch no existing worktree or branch
   there.
4. **Several facts elsewhere in this plan had gone stale, and were
   corrected at their source in this planning commit**, so later
   steps need not redo it:
   - The Prompt said `audits/`; the tree is `docs/audits/`.
   - Phase 2 item 2 said the audit runs daily at 06:00 UTC. It has
     run weekly, at 18:00 UTC on Sunday, since #210 (2026-10-03).
   - Phase 1's closing paragraph and phase 2 item 1 gave 34
     criteria, 37 checks and 17 repositories. Today it is 55
     criteria, 62 registered checks and 24 repositories in the
     matrix. Those counts now say "in August 2026" where they are
     historical.
   - Phase 3 item 4 said eleven projects still carry the retired
     addresser. On the 2026-10-11 run only sfui and clingwrap are
     flagged for it.
5. **Some of the overlapping work is already planned elsewhere.**
   `PLAN-workflow-ref-trust.md` covers how callers pin
   `pr-bot-trigger@main` and the other `actions` references, and
   `PLAN-bot-bypass-identity.md` covers the review-marks App. An
   `actions` finding about either is cross-referenced to that plan
   rather than fixed here.

#### Owned paths

In this repository, read at `origin/main` for phases 2 and 3:

- Runner and tooling: `scripts/audit/` (the package, `checks/`
  included), `scripts/audit-check.py`, `scripts/audit_common.py`,
  `scripts/audit-manage-issues.py`, `scripts/audit-update-docs.py`,
  `scripts/audit-raised.py`, `scripts/audit_snapshot.py`,
  `scripts/check-audit-smoke.py`, `scripts/commit-audit-docs.sh`,
  `tools/audit-snapshot.sh` and `.github/workflows/consistency-audit.yml`.
  Outside `checks/` this is 4,824 lines: 2,940 in the rest of
  `scripts/audit/` and 1,884 in the other files listed.
- Tests: every file under `scripts/tests/` except
  `test_review_tracking.py` and `test_ci_prune_reviews.py`, which
  test `review-tracking.py` and belong to code review tracking. That
  is 22,065 lines. Exclusions are whole files, because a pathspec
  cannot exclude part of one. `test_review.py` stays in: every class
  in it is a `CheckTestCase` for an audit check (`review-marks-pre-commit`,
  `review-coverage`, `review-scope-completeness`, `sfui-vendor`).
- Documentation: `docs/consistency-audits.md` and the criterion
  specs under `docs/audits/`. `docs/audits/compliance.md` is
  generated, so the audit reads it but never edits it by hand.

In `shakenfist/actions`, read at its `origin/main`: the four paths in
survey item 3.

#### Decisions

1. **One current-state wave 2, not one wave 2 per range.** The
   block says that a plan which landed as several merges runs every
   command once per merge. Here that would mean six wave 2 passes,
   five of them over content that has since been rewritten: the
   phase 1 specs were dissolved and re-homed under `docs/` in phase
   4, and the March runner is unrecognisable in today's package. A
   finding against superseded text either duplicates a finding
   against current text or describes something that no longer
   exists. So wave 1, which is cheap, runs once per recorded range,
   and any hit is re-checked against `origin/main` before it is
   reported. Wave 2 reads the current state of the owned paths once.
   This is the decision a reviewer is most likely to push back on,
   because it reads as auditing less than the block asks. It is
   actually wider: it reads everything the plan owns today, not just
   what a range happens to contain. What it gives up is catching a
   regression that was introduced and then reverted, and there is
   nothing left in the tree to fix for one of those.
2. **Current state is diffed against the empty tree.** Every runbook
   command is written `git diff "${AUDIT_RANGE:-...}"`. Setting
   `AUDIT_RANGE="$(git hash-object -t tree /dev/null)..origin/main"`
   makes those commands see the current state as one large
   addition. Without the empty tree, the diff is the empty one the
   block warns about. On its own, though, the range covers the whole
   repository, and the owned paths cannot just be appended after
   `--`: every wave 1 command already carries a pathspec of its own,
   such as `'*.py'` or `'docs/audits/*.md'`. Git takes the union of
   pathspecs, so appending would widen each grep to the whole of
   both, `scripts/review-tracking.py` included. Instead, each
   command's own pathspec is *intersected* with the owned files, and
   the command runs over the literal file list that results:

   ```sh
   EMPTY=$(git hash-object -t tree /dev/null)
   AUDIT_RANGE="$EMPTY..origin/main"
   # OWNED: the *Owned paths* list as an array, its two test-file
   # exclusions written as ':!scripts/tests/...' entries.
   names() { git diff --name-only -z "$AUDIT_RANGE" -- "$@" | sort -z; }
   names "${OWNED[@]}" > owned.lst
   scope() {
       names "$@" | comm -z -12 - owned.lst | sed -z 's/^/:(literal)/'
   }
   # Per command: replace its pathspec with the intersection.
   mapfile -d '' S < <(scope '*.py')
   git diff "$AUDIT_RANGE" -- "${S[@]}" | grep -nE '^\+[^+].{120,}'
   ```

   An empty intersection means the command is not applicable, and
   it is recorded that way and not run, because `git diff` with no
   pathspec reads the whole repository. The `templates/shared-blocks`
   grep is the one that comes out empty today. Run on 2026-10-16,
   `scope '*.py'` returns 56 of the repository's 59 Python files:
   the other three are `review-tracking.py` and its two test files.
   The same helper does the job in `actions`, with the four phase 3
   paths as `OWNED`.

   Wave 1's greps come in two kinds, and only one of them survives
   the empty tree. *Content* greps ask whether something is present
   at all: long lines, file size, imports, TODO markers,
   suppressions, plan references in code, and generated blocks in a
   spec. They mean the same thing against current state as against a
   diff. *Change* greps ask whether something was edited, and against
   the empty tree every line is an addition, so they fire on all of
   the content and say nothing. There are five of them:

   - hand-edited `docs/audits/compliance.md` (`^\+`, which hits
     every line of the generated file);
   - the `FROZEN_ISSUE_TITLES` change (`^[-+]`);
   - the `issue_title` change in `scripts/audit/checks/` (`^[-+]`,
     62 hits on 2026-10-16, one per registered check);
   - the shared-block version bump, whose `templates/shared-blocks`
     paths are out of scope anyway;
   - "documentation touched at all", which lists every document.

   These five run only on the five historical ranges. On the
   current-state pass they are recorded as not applicable, *because
   they detect change*, rather than as passing or as hits to triage.

   All of this departs from running the runbook verbatim, and the
   result says so.
3. **Code quality gets three wave 2 agents, not one.** About 16,200
   lines of runner code is more than one agent reads with care. 2a
   is split by file: `packaging.py` and `ci_workflows.py` (5,042
   lines); the other checks modules (6,340); and everything outside
   `checks/` (4,824). The tests, documentation and security briefs
   stay one agent each, which makes six agents here. `actions`
   gets the runbook's four over its 3,124 lines, so ten in all.
   The count follows from the size of what is read, not from any
   session's configuration, so a session whose workflow size
   guideline is below ten needs the operator to raise it, or must
   run the two repositories one after the other.
4. **`actions` runs under its own runbook, and its findings land in
   `actions`.** Phase 3 was built there, and the fork-guard reasoning
   in phase 3 item 7 is a security claim about code in that
   repository, so leaving it unaudited would leave the plan's sharpest
   claim unchecked. This repository's briefs are written for this
   repository's blast radius, so they are not reused there.
5. **Triage happens before anything is fixed, and it is the
   management session's job.** Duplicates across agents are merged.
   A finding already covered by an open `Consistency:` issue, by
   another plan (survey item 5) or by Future work is cross-referenced
   instead of fixed. Every other finding is either fixed or declined
   with a stated reason. The triage table is written into this
   section before any fix branch is cut, so that the record does not
   depend on the fix pull request merging.
6. **The fix pull requests do not touch `REVIEWS.md`.** Fixing files
   that carry review marks stales those marks. Per `AGENTS.md`, the
   `prune-reviews` workflow corrects that on the next push to
   `main`, and a pull request runs neither `prune` nor `regen`. The
   pull request body names the reviewed files it touched, so that
   whoever re-reads them knows which ones to look at.
7. **This phase closes itself out in its own pull request**, as the
   `next-phase` convention requires of a push-audit phase, and
   records no `Merged` cell. If there is nothing to fix, that pull
   request is just this section's results plus the close-out.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | opus | none | Management session, inline. `git fetch origin` in this repository and in `../actions`. Run this repository's `pre-commit run --all-files` on this phase's branch, which differs from `origin/main` only in this plan, then wave 1 of `PUSH-AUDIT.md` once per recorded range: `a56f5f6^..200e007`, `4a970a6^..fed8aa4`, `b8bf764^1..b8bf764`, `ada14ef^1..ada14ef`, `e3d1333^1..e3d1333`. Re-check every hit against `origin/main` and drop the ones whose text no longer exists, keeping a count of how many were dropped. Then run wave 1 once more over current state, using decision 2's empty-tree range, with each command's own pathspec replaced by its `scope` intersection with the owned files (decision 2's snippet). Record an empty intersection as not applicable and do not run that command; record decision 2's five change greps as not applicable on this pass, and run the content greps as normal. In `actions`, run its `PUSH-AUDIT.md` wave 1 over the same empty-tree range, with the four phase 3 paths as `OWNED` and the same `scope` intersection. Record each command's insertion and deletion totals: they are the numbers the wave 2 reports are checked against. Wave 2 is only worth spending on if wave 1 passes, so stop and report if it does not. No commit. |
| 5b | high | opus | none | Six read-only wave 2 agents in this repository, spawned in parallel: 2a three times (split as in decision 3), then 2b, 2c and 2d, each with its brief copied from `PUSH-AUDIT.md`. Substitute the concrete range and pathspec into every brief rather than passing the shell expansion, because an agent inherits no environment and the default expands to the empty diff. Each agent states which files it read and the totals it measured; a report whose totals do not match 5a's is re-run rather than triaged. Agents report findings and change nothing. |
| 5c | high | opus | none | Four read-only wave 2 agents in `../actions`, spawned in parallel with 5b: `actions`' own 2a–2d briefs from its `PUSH-AUDIT.md`, over the four phase 3 paths at its `origin/main`, using the same range substitution and the same totals check. Point 2d at the claim in phase 3 item 7: does `pr-bot-trigger` refuse fork pull requests, and does every output a caller hands to `checkout` or `git push` come from something it has vouched for? Findings only. |
| 5d | high | opus | none | Management session. Triage per decision 5: merge duplicates; open `Consistency:` issues (`gh search issues --owner shakenfist --state open Consistency`), `PLAN-workflow-ref-trust.md`, `PLAN-bot-bypass-identity.md` and Future work are cross-referenced rather than fixed. Write a `#### Results` subsection here with one row per finding, giving source agent, file:line, verdict (fix / decline / cross-reference) and reason. If there are no findings, write that in one sentence. Commit on this phase's branch: "Record the consistency audits v2 push audit." |
| 5e | high | opus | worktree | Fix this repository's findings marked *fix* in 5d, one commit per finding, on a branch cut from `main` (e.g. `consistency-audits-v2-push-audit-fixes`). Every change to a `Check` updates its spec in `docs/audits/` in the same commit. Never rename an `issue_title`: `scripts/tests/test_metadata.py` freezes them, and a rename orphans issues fleet-wide. Keep `audit-manage-issues.py` to `--dry-run` if it is run at all. Run `pre-commit run --all-files`, plus `tools/mermaid-lint.sh` if a diagram changed. A change to what a check reports also needs the before/after fleet comparison with `tools/audit-snapshot.sh`, with the expected moves listed before measuring. Leave `REVIEWS.md` alone (decision 6). Skip this step if 5d found nothing to fix here. |
| 5f | high | opus | worktree | The same for `actions`' findings marked *fix*, in `actions`, under that repository's conventions (its `AGENTS.md` and `PLAN-TEMPLATE.md`), one commit per finding. If a fix changes a composite action's inputs or outputs, check every fleet caller (`gh search code --owner shakenfist <action>@main`) and say in the pull request which callers need a follow-up. Skip if there is nothing to fix there. |
| 5g | low | sonnet | none | Close-out, on this phase's branch once 5e and 5f have merged (or been skipped): set phase 5 to `Complete` in the Execution table, and this plan's row in `docs/plans/index.md` to `Complete`. Retitle this section `-- DONE`. Check each Success criteria bullet against the tree, and add a line under any that does not hold. Commit: "Close out consistency audits v2." |

#### Risks and mitigations

- **Agents audit an empty diff and call it clean.** This is the
  failure the runbook's totals rule exists for. Mitigation: 5a
  records the totals, and the management session compares every
  wave 2 report against them before reading any findings.
- **The empty-tree pathspec drifts from the owned-path list, or
  widens a grep it was meant to narrow.** Mitigation: 5a builds
  `OWNED` and `owned.lst` once, from *Owned paths*, and every
  command's files come from decision 2's `scope` intersection rather
  than from a pathspec typed by hand. Results quotes `OWNED` and
  each command's file count, so a count larger than `owned.lst`
  shows up as an error.
- **The `actions` fixes break callers fleet-wide**, since `@main`
  is what every caller uses. Mitigation: 5f's caller check, plus
  `actions`' own push audit on its pull request. Any change to an
  interface gets a note in the pull request listing which callers
  need follow-up.
- **Findings collide with work in flight in another plan.**
  Mitigation: 5d's cross-reference pass, run before any fix branch
  is cut.
- **Triage quietly declines inconvenient findings.** Mitigation:
  each decline records its reason in Results, where the review of
  5d's commit sees it. A decline that just says "out of scope"
  names the plan or issue the finding belongs to.

#### Definition of done

- The Results subsection exists and has one row per finding, or a
  sentence saying there were none. Each row's verdict is fix,
  decline or cross-reference, and every decline has a reason. Check:
  every row has a non-empty reason cell.
- Each wave 2 report quotes insertion and deletion totals that
  match the 5a range it was given.
- Every finding marked *fix* has a merged commit, and the Results
  row cites it.
- `pre-commit run --all-files` passes on this phase's final commit.
- `python3 scripts/audit-check.py --repo-path . --repo-name development`
  reports `plan-audit-phase` and `plan-index` as passing for this
  repository; both read this plan and its `index.md` row.
- The Execution table's phase 5 row and this plan's `index.md` row
  both read `Complete`.

#### Back brief

Before running 5a, back brief the operator: which ranges and which
pathspec will be audited, the line counts behind decision 3 re-measured
(re-split 2a if any has moved by more than about 10%), the agent
count, and whether 5b and 5c
run in parallel (ten agents) or one after the other (decision 3).
Before 5e and 5f,
there is a gate: the 5d Results table is reviewed and agreed first,
because fixing a finding that should have been declined is cheap to
propose and expensive to unwind across the fleet.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The project consistency audit is implemented in a scalable
  way with work items tracked in a more concrete manner.

* Code and configuration reuse is maximised across the
  repositories.

* It is easy to add new audit items and have them rolled out
  without a complete re-audit of all items.

* It is easy to semi-regularly re-audit all items looking for
  implementation drift.

* Documentation in `docs/` has been updated to describe these
  new features and how we use them.

### Future work

* A fleet-wide compliance dashboard, per the open question above.

* Machine checks for the four criteria `audit-check.py` does not
  measure -- `security-sanitization`, `console-logging`,
  `python-version` and `test-coverage`. These are the only audit files
  with no marker block, so they are also the only criteria whose
  per-project status is nobody's job to keep current. Either automate
  them or say in each file that it is judged by hand.

* Automatic assignment or triage of audit issues.

* Retire the four repositories that still have no automated review
  (`cloudgood`, `divergulent`, `kerbside-patches`, `library-utilities`)
  or adopt them properly, rather than leaving them permanently
  non-compliant in the tables.

### Bugs fixed during this work

* The daily audit began failing on 2026-08-20 because the runners
  enforce PEP 668 and the bare `pip install skillsaw` was refused with
  `externally-managed-environment`. Every leg of the matrix failed,
  taking issue filing and table regeneration with it. Fixed by
  installing into a venv and putting that venv on `PATH`.

* A skillsaw that could not run did not fail the audit.
  `skillsaw_errors()` caught `FileNotFoundError` and returned `None`,
  which `check_llm_context_lint` turned into `not_applicable`, so a
  broken install silently stopped measuring one criterion across the
  entire fleet, and the tables reported it in a way that looked
  deliberate. The workflow now asserts that skillsaw answers, at the
  pinned version.

* A failing scheduled run emailed whoever pushed last, which is nobody's
  inbox in particular, so the outage above ran for a full day with the
  tables still showing the previous morning's verdicts. Hence the
  `report-failure` job.

* Issue bodies built from an indented multi-line shell string rendered
  as a code block on GitHub, because four leading spaces are a code
  block in GitHub-flavoured Markdown. Rebuilt with `printf`.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
