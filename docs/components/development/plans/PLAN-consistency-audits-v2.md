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
| 5. Push audit | Complete | |

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

### Phase 5: Push audit -- DONE

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

#### Results

Run on 2026-10-16 and 2026-10-17 against development `3240530` and
actions `e0bb086`. The raw outputs of 5a, and the ten wave 2 reports,
are not committed; this section is the record.

**5a (wave 1).** Passed in both repositories.

- development: `pre-commit` was clean. The five historical ranges
  gave 641 hits. 113 were for text no longer on `main`. The rest were
  accepted: stdlib imports, plan references inside the plan checks
  themselves, and 502 spec table rows that now live only in the
  generated `compliance.md`, which `PLAN-audit-compliance-split.md`
  already dealt with.
- The current-state pass ran the content greps over 117 owned files
  (+46,571) and recorded the five change greps as not applicable, per
  decision 2. Every hit was accepted, and the 12 files over 800 lines
  went to 2a.
- actions: `pre-commit` was clean, 510 unit tests passed, and gitleaks
  (v8.18.4 in docker, because it is not installed on the host) passed
  its positive control and scanned 535 commits clean.
  `ansible-syntax-check` was not applicable, since no phase 3 path
  touches `ansible/`.
- Shellcheck at default severity found six unused variables in
  `review-pr-with-claude.sh`; they went to triage as 5c-2a-4.
- The decision 3 line counts were re-measured and had not moved, so
  the planned split held.

**5b and 5c (wave 2).** Ten agents ran in parallel, as the operator
chose at the back brief. Every report reproduced its expected totals:

| Agent | Files read | Totals |
|-------|------------|--------|
| 2a-i | `packaging.py`, `ci_workflows.py` | 2 files, +5,042 |
| 2a-ii | the other `checks/` modules | 9 files, +6,340 |
| 2a-iii | runner outside `checks/` | 23 files, +4,824 |
| 2b | tests | 25 files, +22,065 |
| 2c | `docs/consistency-audits.md`, `docs/audits/` | 58 files, +8,300 |
| 2d | runner code | 34 files, +16,206 |
| 5c-2a, 2b, 2c, 2d | actions phase 3 paths | 10 files, +3,152 each |

One brief was narrowed before it ran. 2d's first file list also took
in the tests (59 files, +38,271), so it was cut to the runner's own
code, with the tests given as context. One wave 1 observation was
withdrawn by 2b: `test_docs_content.py` and `test_llm_docs.py` do carry
the plan-reference marker, at file scope.

**Operator decisions taken at triage (2026-10-17):**

1. **Fast-tracks.** Two findings were live exposures rather than audit
   defects, and were fixed ahead of this table:
   - 5c-2d-1, -2 and -3 in shakenfist/actions#168;
   - 2d-1 in #263.
2. **Tool failures report `error`.** When a check's own tooling fails
   (a GitHub API error, a failed `git ls-files`), it reports `error`
   rather than `fail` or `not_applicable`. `error` leaves issues
   alone, and turns the leg red through `audit-raised.py`. The one
   exception is a network check that could not ask GitHub. That is a
   warning, so that one transient 502 does not hold back the page (#279
   review, confirmed by Michael). The policy is written down once, in
   `docs/consistency-audits.md`.
3. **The reviewer's issue filing in actions is dropped.** That means
   the filing step, the "Related Issues / Closes" section and the
   `issues: write` requirement. Callers that keep granting it are
   harmless.
4. **The token split is taken in this phase.** The clone and audit
   steps get a separate read-only fine-grained token, and
   `AUDIT_TOKEN` stays only in `manage-issues` and `update-docs`. The
   operator mints the new token; 5e does the workflow side.

**Verdicts.** Each *fix* row names the fix group it lands in. The
development groups land as one pull request per group, in 5e, and the
actions groups the same way, in 5f:

| Group | Repository | What |
|-------|------------|------|
| D1 | development | Every read of an audited repository's files goes through `Repo.read`, or one helper on `Repo.contains`, so nothing outside the clone is read, and a directory or dangling link is "no file" rather than an `error` |
| D2 | development | The tool-failure policy (decision 2 above) |
| D3 | development | The issue lifecycle: open issues fetched once per repository rather than searched per check, write failures counted and failing the job, and tests for every branch |
| D4 | development | `consistency-audit.yml`: the token split, a `main`-only guard on the steps that write, `runner.temp` and `--clear`, a concurrency group, skillsaw in its own environment, `persist-credentials: false`, and the failure-report script moved to `tools/` |
| D5 | development | Parser and matcher defects in individual checks, each with the test 2b found missing |
| D6 | development | Duplicated helpers that have drifted |
| D7 | development | Stale comments and documentation |
| A1 | actions | Fixed in actions#168 |
| A2 | actions | Drop review issue filing (decision 3 above) |
| A3 | actions | Tests that execute the security guards rather than search their text |
| A4 | actions | Review JSON extraction and validation |
| A5 | actions | Documentation and comments |
| T | development templates | `templates/ci-review-automation/`, which actions' `pr-re-review.yml` copies byte for byte; fixed in development, then re-synced |

| ID | Location | Sev | Verdict | Landed | Group / reason |
|----|----------|-----|---------|--------|----------------|
| 2a-i-1 | `ci_workflows.py:1290-1331` | med | fix | #281 | D5. Read `paths`/`paths-ignore` from the `pull_request` trigger, and test comments stripped |
| 2a-i-2 | `packaging.py:1541-1557` | med | fix | #279 | D2: use `tracked_paths`, and report `error` when it fails |
| 2a-i-3 | `packaging.py:531-540,727-737` | med | fix | #281 | D5: compare a glob's directory, and skip block scalars |
| 2a-i-4 | `packaging.py:486,497` | low | fix | #281 | D5: one helper for a quoted boolean |
| 2a-i-5 | `ci_workflows.py:1443` | low | fix | #281 | D5: flow-form `workflow_call`, read from the header |
| 2a-i-6 | `ci_workflows.py:1993-1998` | low | fix | #281 | D5: a fuzz callee invoked from a `merge_group` workflow |
| 2a-i-7 | `ci_workflows.py:687-711` | low | fix | #282 | D6: use `mapping_entries` |
| 2a-i-8 | `packaging.py`, `ci_workflows.py` raw `open()` ×9 | low | fix | #271 | D1 |
| 2a-i-9 | `ci_workflows.py:977-1002` | low | fix | #281 | D5: accumulate the docs-only findings, and use the constant. Tested by 2b-7 |
| 2a-i-10 | `ci_workflows.py:162-230` | low | cross-reference | - | `PLAN-workflow-ref-trust.md`. Matching `@main` is that plan's to change when it moves callers to pins |
| 2a-i-11 | `packaging.py:2671` | low | fix | #281 | D5: strip the comment before matching |
| 2a-i-12 | `packaging.py:1039-1053` | low | fix | #281 | D5: read the arrays through `tomllib` |
| 2a-i-13 | `ci_workflows.py:613-629` | low | fix | #285 | D7: narrow the docstring to what is measured |
| 2a-i-14 | `ci_workflows.py:1957` | low | fix | #281 | D5 |
| 2a-i-15 | `ci_workflows.py:153,957,964` | low | fix | #285 | D7 |
| 2a-i-16 | `ci_workflows.py:236-259` | low | fix | #285 | D7: cut the survey narrative |
| 2a-i-17 | `packaging.py:2460-2500` | low | fix | #281 | D5: hoist the masking out of the loop |
| 2a-i-18 | file size, both files | adv | decline | - | The seams are real, but a split moves about 2,000 reviewed lines and re-stales their marks for no behaviour change. Future work below |
| 2a-ii-1 | raw `open()` across five modules, `files.py` walkers | med | fix | #271 | D1. This is the same class as 2a-i-8, 2a-iii-10 and 2d-4, and here it was reproduced: a dangling link gives `error`, and a link out of the clone is quoted into an issue |
| 2a-ii-2 | `github_config.py:169-210` | med | fix | #279 | D2. A failed or null security query is `error`, not pass |
| 2a-ii-3 | tool-failure policy | med | fix | #279 | D2: decision 2 above |
| 2a-ii-4 | `github_config.py:121-150` | low | fix | #279 | D2: the exemption moves to `applies()` |
| 2a-ii-5 | `github_config.py:386-390`, `review.py:203-206` | low | fix | #285 | D7 |
| 2a-ii-6 | four docstrings naming removed functions | low | fix | #285 | D7 |
| 2a-ii-7 | `llm_docs.py:437-446` | low | fix | #281 | D5: report orphans even without skillsaw. Tested by 2b-3 |
| 2a-ii-8 | `npm_dependencies.py:856-872` | low | fix | #279 | D2: use `tracked_paths` |
| 2a-ii-9 | `plans.py:1148-1163` | low | fix | #282 | D6: `iter_lines_outside_fences` |
| 2a-ii-10 | `docs_content.py:201-206` | low | fix | #282 | D6: `tracked_paths`, falling back to `WALK_SKIP` |
| 2a-ii-11 | `npm_dependencies.py`, `packaging.py` result keys | low | fix | #282 | D6: move the lists to `findings=`, with a before/after snapshot |
| 2a-ii-12 | two block-scalar regexes | low | fix | #282 | D6: one constant in `text/workflows.py` |
| 2a-ii-13 | small duplicates and history in comments | low | fix | #285 | D7 |
| 2a-ii-14 | `plans.py`, `npm_dependencies.py` size | adv | decline | - | As 2a-i-18 |
| 2a-iii-1 | `audit_common.py:109-123` | med | fix | #267 | D3: fetch open issues once, and treat a failed listing as an error rather than as "none" |
| 2a-iii-2 | `audit-manage-issues.py` writes | med | fix | #267 | D3: count write failures and exit non-zero; close before commenting |
| 2a-iii-3 | `consistency-audit.yml:15` | med | fix | #268 | D4: `manage-issues` and `update-docs` run only from `refs/heads/main` |
| 2a-iii-4 | `text/workflows.py:198` | med | fix | #281 | D5: any indentation deeper than `jobs:`, quoted keys, trailing comments, each tested |
| 2a-iii-5 | `check-audit-smoke.py:258-264` | med | fix | #267 | D3: an `error` status fails the smoke run |
| 2a-iii-6 | fixed `/tmp` paths | med | fix | #268 | D4, together with 2d-2 |
| 2a-iii-7 | no concurrency group | low | fix | #268 | D4 |
| 2a-iii-8 | `--limit 10` search | low | fix | #267 | D3: removed by fetching once |
| 2a-iii-9 | issue helpers bypass `GitHubClient`; the title key is built twice | low | fix | #267 | D3: one `issue_title_for()`, and the helpers move onto the seam |
| 2a-iii-10 | `files.py` primitives | low | fix | #271 | D1. This is the root of the class |
| 2a-iii-11 | `NON_SOURCE_DIRS` | low | fix | #282 | D6 |
| 2a-iii-12 | three more fence loops in `markdown.py` | low | fix | #282 | D6 |
| 2a-iii-13 | retired checks leave issues open | low | fix | #267 | D3: a `RETIRED_ISSUE_TITLES` list that `manage-issues` closes, plus documentation |
| 2a-iii-14 | `repo.py:23` | low | fix | #285 | D7: give cloudgood's override a reason |
| 2a-iii-15 | nine stale cross-references | low | fix | #285 | D7 |
| 2a-iii-16 | `consistency-audit.yml:261-303` | low | fix | #268 | D4: `tools/report-audit-failure.sh` |
| 2a-iii-17 | `tomllib`, no stated floor | low | fix | #266, #268 | D7: state Python 3.11 in `docs/consistency-audits.md`, and assert it in the workflow |
| 2b-1 | issue lifecycle untested | high | fix | #267 | D3: the fail-create, pass-close, N/A-close and exact-title cases, including the ten prefix titles |
| 2b-2 | `ExpensiveLanePathFilter` tests | med | fix | #281 | D5, with 2a-i-1 |
| 2b-3 | `LlmContextLint` tests | med | fix | #281 | D5, with 2a-ii-7 |
| 2b-4 | `GithubSecurity` with rc≠0 | med | fix | #279 | D2, with 2a-ii-2 |
| 2b-5 | `VersionFileGitignoreTest` | med | fix | #281 | D5 |
| 2b-6 | about 15 error paths never asserted | med | fix | #279 | D2: each becomes an `error` case |
| 2b-7 | `CiReviewAutomation` docs-only fail | low | fix | #281 | D5, with 2a-i-9 |
| 2b-8 | empty workflows directory, six checks | low | fix | #281 | D5 |
| 2b-9 | stale "no handler" test comments | low | fix | #285 | D7 |
| 2b-10 | `audit-raised.py`, `audit-check.py` CLIs | low | fix | #267 | D3: one subprocess test each |
| 2c-1 | `review-coverage.md:45-47` | med | fix | #266, #285 | D7 |
| 2c-2 | `consistency-audits.md:384-385` | med | fix | #266 | D7: seed a missing `--page` from `COMPLIANCE_PAGE`, with a test |
| 2c-3 | `workflow-standards.md`, `llm-tooling.md` | med | fix | #266 | D7: mark each rule Measured or Reviewed |
| 2c-4 | `consistency-audits.md:424-431,86-88` | med | fix | #266 | D7: point at `NETWORK_CHECKS`. 5e relies on this before/after procedure, so this lands first in D7 |
| 2c-5 | "Adding a criterion" file count | low | fix | #266 | D7: one list, which `PUSH-AUDIT.md` and `AGENTS.md` link to |
| 2c-6 | "other 35 files" | low | fix | #266 | D7 |
| 2c-7 | names `ISSUE_TITLES`, not `issue_title` | low | fix | #266 | D7 |
| 2c-8 | whitespace claim | low | fix | #266 | D7 |
| 2c-9 | artifact name | low | fix | #266 | D7, including `ARCHITECTURE.md` |
| 2c-10 | hook trigger descriptions | low | fix | #266, #285 | D7 |
| 2c-11 | this plan's Future work, "four criteria unmeasured" | low | fix | #264 | Done already. The entry is updated in this commit (see Future work) |
| 2c-12 | `'at the time of writing'` parse | low | fix | #266, #285 | D7: a named constant, plus a line in `AGENTS.md` |
| 2c-13 | stale comments | low | fix | #285 | D7, merged with 2a-iii-15 |
| 2d-1 | `review.py:369-428` `SfuiVendor` | med | fix | #263 | Fast-tracked: #263 |
| 2d-2 | `/tmp/audit-venv` reused and on `PATH` | med | fix | #268 | D4 |
| 2d-3 | skillsaw dependencies unpinned in the token's interpreter | low | fix | #268 | D4: its own environment, and no token |
| 2d-4 | about 45 unguarded `open()`, the `python_source.py:170` join | med | fix | #271 | D1 |
| 2d-5 | regexes and reads controlled by the audited repository | low | fix | #281 | D5: patterns become literal or fnmatch where the spec allows; reads are size-capped |
| 2d-6 | `defuse()` leaves mentions and markdown | low | fix | #281 | D5: `details` items rendered with `defuse_item`, and the comment corrected |
| 2d-7 | `packaging.py:1608` no `--` | info | fix | #281 | D5 |
| 2d-8 | tag-pinned actions; the audit checkout's credentials | info | cross-reference + fix | #268 | Pinning is `PLAN-workflow-ref-trust.md` phase 3. `persist-credentials: false` goes in D4 |
| 5c-2a-1 | review issue filing never works | med | fix | actions#175 | A2 (decision 3 above) |
| 5c-2a-2 | complete reviews labelled incomplete | med | fix | actions#174 | A4: a closed outermost object is complete, with tests for trailing prose, `}```` and `~~~` |
| 5c-2a-3 | the fallback validator diverges | low | fix | actions#174 | A4: read the required fields and enums from `review-schema.json` with stdlib `json` |
| 5c-2a-4 | unused variables; `ci_output` | low | fix | actions#175 | A5: delete them; `ci_output` writes `$GITHUB_OUTPUT` and the outputs are declared. SC2002 is already fixed in actions#168 |
| 5c-2a-5 | about 115 lines of inline shell in `pr-re-review.yml` | med | fix | #273, actions#176 | T: a composite action so the logic is tested once; then the fleet re-syncs |
| 5c-2a-6 | `pr-re-review.yml` superseded copy | low | cross-reference | - | shakenfist/actions#126 |
| 5c-2a-7 | the two lanes harden their checkouts differently | low | fix | actions#168 | A1: actions#168 |
| 5c-2a-8 | the lanes do not share a concurrency group | low | fix | #273, actions#176 | T: reword the comment. Mutual exclusion is not wanted, since the gate already prevents a second automatic review |
| 5c-2a-9 | history in comments | low | fix | actions#175, #273, actions#176 | A5, and T for `pr-re-review.yml` |
| 5c-2b-1 | no issue ever filed | high | fix | actions#175 | A2. Dropped rather than tested |
| 5c-2b-2 | `check_origin`/`check_permission` never executed | high | fix | actions#172 | A3: run both shipped steps against a stub `gh` for each case |
| 5c-2b-3 | no structural test of the review workflows' guards | high | fix | actions#172 | A3: every `claude-code` job carries a same-repository condition, `force` is routed as phase 3 item 2 says, and the bot-comment guard, `persist-credentials` and `hooksPath` are each present |
| 5c-2b-4 | `review-pr-with-claude.sh` gate untested; it fails open on a `gh` error | med | fix | actions#172, actions#179 | A3: the stub harness from actions#168 extended to the gate, max-turns and dedupe; a failed reviews query skips rather than reviews |
| 5c-2b-5 | model-supplied `issue_number` trusted | med | fix | actions#175 | A2. Gone with issue filing; `additionalProperties: false` goes on the schema items |
| 5c-2b-6 | resolve and confirm shell untested | med | fix | #273, actions#176 | T, with 5c-2a-5 |
| 5c-2b-7 | jsonschema branch not pinned | low | fix | actions#174 | A4: install it in CI and import it unconditionally in the test |
| 5c-2c-1 | `docs/consuming.md:298-338` example | high | fix | actions#175 | A5: point at `templates/ci-review-automation/pr-retest.yml` |
| 5c-2c-2 | "forks reviewed on human request" | med | fix | actions#168, #266 | actions half in actions#168. The development half, `docs/audits/ci-review-automation.md:99-100`, goes in D7 |
| 5c-2c-3 | the "will be closed when this PR merges" text | med | fix | actions#175 | A2 |
| 5c-2c-4 | the diff-too-large advice | low | fix | actions#175 | A5 |
| 5c-2c-5 | examples without timeouts | low | fix | actions#175 | A5 |
| 5c-2c-6 | caller requirements missing | low | fix | actions#175 | A5. `issues: write` is no longer one, after A2 |
| 5c-2c-7 | stale `pr-ref` descriptions | low | fix | actions#175 | A5 |
| 5c-2c-8 | same-repository `pull_request` only | low | fix | actions#175 | A5 |
| 5c-2c-9 | `ARCHITECTURE.md:62-63` | low | fix | actions#175 | A5 |
| 5c-2c-10 | `docs/ci-review-automation.md:3-4,165` | low | fix | #266, #285 | D7 |
| 5c-2d-1 | reviewer with all tools and the token, on a shared pool | high | fix | actions#168 | Fast-tracked: actions#168 |
| 5c-2d-2 | the PR tree's `.claude/settings.json` hooks ran | med | fix | actions#168 | Fast-tracked: actions#168. Confirmed on a scratch repository with CLI 2.1.301 |
| 5c-2d-3 | `pr-auto-review.yml` checkout credentials | low | fix | actions#168 | Fast-tracked: actions#168 |
| 5c-2d-4 | issue spam; mentions in the rendered review | low | fix | actions#175, actions#174 | A2 removes the issues. Breaking mentions in `render-review.py` goes in A4 |
| 5c-2d-5 | a fork PR can bring its own job to the self-hosted pool | info | cross-reference | - | An operator check, not code. Confirm org-wide approval for every outside collaborator, and that the `claude-code` runner group is restricted to selected workflows. Confirmed by the operator on 2026-10-24. Both settings need `admin:org` to read, so this records the operator's confirmation rather than an observed state |

**Summary.** 111 findings:

- 105 to fix:
  - five in the fast-track pull requests, both merged on 2026-10-16:
    2d-1 in #263, and 5c-2d-1, -2, -3 and 5c-2a-7 (group A1) in
    actions#168;
  - one, 2c-11, closed by this commit's Future work update;
  - the remaining 99 spread across the twelve groups still to land:
    D1-D7, A2-A5 and T. 5c-2c-2 is among them: its actions half
    landed in actions#168, and its development half is in D7.
- 1 split: 2d-8. Its pinning half is cross-referenced, and its
  credential half is fixed in D4.
- 2 declined: the advisory file-size splits.
- 3 cross-referenced: `PLAN-workflow-ref-trust.md` (2a-i-10),
  shakenfist/actions#126 (5c-2a-6), and the operator (5c-2d-5).

No finding was declined for being out of scope without naming where it
belongs.

**Gate.** Per the back brief, 5e and 5f wait until this table is
agreed.

#### Close-out

Closed out on 2026-10-24, once every fix group had merged.

- Each *fix* row's Landed column names the pull request it merged in.
  It comes from the row's fix group, and where a group landed as more
  than one pull request, from whichever of them names the finding in
  its description. The two that no description names are 2c-11, which
  landed with this table (#264), and 5c-2a-7, in actions#168, which
  predates the finding IDs.
- D4's review produced two follow-ups, which merged as their own pull
  requests: #278, which moves `AUDIT_TOKEN` into a `main`-only
  `audit-write` environment (#269), and #280, which runs the writing
  jobs on ephemeral runners (#270).
- D4's token split stopped the weekly audit from 2026-10-18 (#290),
  and #308 fixed it. See Bugs fixed below.
- #278 needs repository settings that the pull request cannot make,
  and at the start of the close-out none of them were in effect: the
  environment held no secret and had no branch policy, and the
  repository secret still existed. #269 was reopened, and the operator
  completed the settings on 2026-10-24, minting a new `AUDIT_TOKEN`
  with Issues read and write as its only permission. The API then
  showed one environment secret, a single branch rule for `main`, and
  no repository `AUDIT_TOKEN`. A dispatched run of `main` at `c39b06e`
  passed, with both writing jobs on VM runners, which was also #280's
  owed check. No issue changed state in that run, so it shows the new
  token can list issues fleet-wide; the next run that files or closes
  one is the first to use it for writing. A refused write fails
  `manage-issues`, and `report-failure`, which holds only
  `GITHUB_TOKEN`, then files its issue, so that run reports the
  outcome either way.
- `docs/consistency-audits.md` said `AUDIT_TOKEN` pushes the compliance
  page. It does not: `update-docs` pushes with the job's own
  `GITHUB_TOKEN`. That sentence, and the matching workflow comment,
  are corrected, and the token's permissions are now written down
  beside `AUDIT_READ_TOKEN`'s.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The project consistency audit is implemented in a scalable
  way with work items tracked in a more concrete manner.

* Code and configuration reuse is maximised across the
  repositories.

  Not yet fleet-wide. On 2026-10-24 eight repositories still fail
  `ci-review-automation`, each with an open issue that
  `docs/audits/compliance.md` links. Phase 3 item 7 handed that
  rollout to those issues rather than holding this plan open, and
  sfui#26 and clingwrap#121 stay first, for the reason given there.

* It is easy to add new audit items and have them rolled out
  without a complete re-audit of all items.

* It is easy to semi-regularly re-audit all items looking for
  implementation drift.

* Documentation in `docs/` has been updated to describe these
  new features and how we use them.

### Future work

* A fleet-wide compliance dashboard, per the open question above.

* ~~Machine checks for the four criteria `audit-check.py` does not
  measure.~~ Done by 2026-10-16, as the phase 5 push audit found
  (2c-11). `console-logging`, `security-sanitization` and
  `python-version` are now registered checks, each spec marks its
  measured and delegated halves, and `test-coverage.md` says it is
  judged by hand. No spec carries a marker block any more.

* Splitting the largest check modules along the seams the phase 5 push
  audit named (2a-i-18, 2a-ii-14):
  - from `packaging.py`, the release-artifact exclusion model and the
    dependency criteria;
  - from `ci_workflows.py`, fuzz reporting and review automation;
  - from `plans.py`, the plan-structure parser, into `audit/text/`.

  Each split moves reviewed lines and stales their marks for no change
  in behaviour, so it is worth doing only alongside a change that
  touches the module anyway.

* Automatic assignment or triage of audit issues.

* Retire the four repositories that still have no automated review
  (`cloudgood`, `divergulent`, `kerbside-patches`, `library-utilities`)
  or adopt them properly, rather than leaving them permanently
  non-compliant in the tables.

### Bugs fixed during this work

* The push audit's own token split (D4, #268) broke the weekly audit
  from 2026-10-18 (#290). The legs' new read-only fine-grained token,
  `AUDIT_READ_TOKEN`, gets null from the REST repository answer for
  `delete_branch_on_merge` and every other merge setting, so every leg
  failed at the read-token probe, and with the probe out of the way
  `delete-branch-on-merge` would have reported `error` for every
  repository. #308 reads the setting through GraphQL, which answers
  it. The fix was pushed on 2026-10-23 but not opened as a pull
  request until this plan's close-out found it.

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
