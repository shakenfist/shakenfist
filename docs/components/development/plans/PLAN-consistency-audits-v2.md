# Title for the plan

## Prompt

Before responding to questions or discussion points in this
document, explore the occystrap codebase thoroughly. Read relevant
source files, understand existing patterns (project structure,
command-line argument handling, input source abstractions, output
formatting, error handling), and ground your answers in what the
code actually does today. Do not speculate about the codebase when
you could read it instead. Where a question touches on external
concepts (OCI image specs, Docker/Podman compatibility, registry
APIs), research as needed to give a confident answer. Flag any
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

* Should the CI audit workflow run daily, weekly, or on-push to
  `development`? Daily seems right for drift detection without being
  noisy.

* Should audit issues be auto-assigned to anyone, or left unassigned
  for triage?

* For the combined review+fix workflow, should fixes be committed
  directly to the PR branch or proposed as review suggestions? Direct
  commits are simpler but suggestions give the author more control.

* Do we want a dashboard (e.g. a generated README table or GitHub
  project board) that shows compliance status across all repos at a
  glance?

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
   repos (ryll has no GitHub repo).
5. ~~Create GitHub issues for all known non-compliant items.~~ 35
   issues created across 10 repos.

### Phase 2: CI-based audit runner -- DONE

1. ~~Write audit check scripts (shell or Python) for each
   criterion.~~ `scripts/audit-check.py` checks 11 criteria
   (4 subjective criteria skipped: security-sanitization,
   console-logging, python-version, test-coverage).
2. ~~Create a scheduled workflow in `development` that runs all
   checks across all projects.~~
   `.github/workflows/consistency-audit.yml` runs daily at
   06:00 UTC with a matrix of 10 repos.
3. ~~Add issue creation/closure automation for audit results.~~
   `scripts/audit-manage-issues.py` creates issues for failures
   and closes them when checks pass, using exact title matching
   against existing manually-created issues.
4. Verify drift detection works after first CI run. Requires
   `AUDIT_TOKEN` secret to be configured on the development
   repo with cross-repo issue permissions.

### Phase 3: Combined review+fix workflow

1. Design the combined workflow preserving the two-checkout security
   model.
2. Implement the "already reviewed" gate (check for existing bot
   review comment).
3. Add the automatic trigger on successful functional test
   completion, gated by the above check.
4. Add the manual trigger via `@shakenfist-bot please review and
   fix`.
5. Implement as a reusable workflow in `shakenfist/actions`.
6. Pilot on `shakenfist` (the reference project).
7. Roll out to remaining projects.

### Phase 4: Cleanup

1. Retire `PLAN-consistency.md` once issue tracking is live.
2. Archive `PROJECT-CONSISTENCY-AUDITS.md` with a pointer to the
   new `audits/` directory.
3. Update all documentation in `docs/`.

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

We should list obvious extensions, known issues, unrelated bugs
we encountered, and anything else we should one day do but have
chosen to defer to here so that we don't forget them.

...

### Bugs fixed during this work

This section should list and bugs we encounter during development
that we fixed.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
