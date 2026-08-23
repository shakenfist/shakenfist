# Consistency audits v2

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly: `audits/`, the audit
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
it: `audits/` now holds 34 criteria rather than 13, backed by 37
registered checks (some criteria, such as `workflow-standards`,
decompose into several), and the audit matrix covers 17 repositories
rather than 10. `actions` and `development` were both moved off the
exempt list -- the fleet depends on `actions` for every composite action
it runs, and `development` is where these rules are written, so an
exemption there is an exemption the authors of the standard wrote for
themselves.

### Phase 2: CI-based audit runner -- MOSTLY DONE

1. ~~Write audit check scripts (shell or Python) for each
   criterion.~~ `scripts/audit-check.py` runs 37 registered checks
   across 34 criteria (4 judged by reading rather than matching:
   security-sanitization, console-logging, python-version,
   test-coverage).
2. ~~Create a scheduled workflow in `development` that runs all
   checks across all projects.~~
   `.github/workflows/consistency-audit.yml` runs daily at
   06:00 UTC with a matrix of 17 repos.
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
7. Check the audit matrix against the organisation's actual repository
   list, so a repository added to the org is not silently unaudited.
   Tracked as issue #40.

### Phase 3: Automatic review, and the fix/retest split -- MOSTLY DONE

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
   this repository and from the template. Eleven of the sixteen
   audited projects still carry the workflow (actions, agent-python,
   client-python, client-python-k3s, clingwrap, instar, kerbside,
   occystrap, ryll, shakenfist and sfui), so the command still answers
   across most of the fleet; the `ci-review-automation` check files an
   issue against each until it does not.
5. ~~Implement as a reusable workflow in `shakenfist/actions`.~~
6. ~~Pilot it.~~ Piloted on `actions` and on this repository, rather
   than on `shakenfist` as originally written.
7. Roll out to the remaining projects. Twelve of the sixteen audited
   projects now call `pr-auto-review.yml`; `cloudgood`, `divergulent`,
   `kerbside-patches` and `library-utilities` do not. Separately, and
   larger, ten projects still hand-roll the bot trigger handling in
   `pr-re-review.yml` instead of calling
   `shakenfist/actions/pr-bot-trigger@main`. That is a security gap
   rather than an untidiness: a hand-rolled copy does not inherit the
   action's refusal to act on fork pull requests, and the `pr-ref` it
   substitutes is a head-repository branch name that callers hand
   straight to `checkout` and `git push` against their own repository.
   The `standards-alignment` skill is the vehicle for this, one
   repository per commit.

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
