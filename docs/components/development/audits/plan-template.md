# Audit: Plan template

## What we check

`PLAN-TEMPLATE.md` is the starting point for every master plan a
repository writes into `docs/plans/`. Most of what it says is not
project-specific -- how phase files are named, that sub-agents do the
implementation work, what the effort levels mean, which models are
available. That wording was copied between repositories by hand and
drifted, so it is now embedded as versioned shared blocks.

Repositories carrying a `PLAN-TEMPLATE.md` must embed the current
version of each of:

* **`plan-file-conventions`** -- where plans live, how phase files are
  named, how phases are tracked, and the one-commit-per-logical-change
  rule;
* **`plan-status-vocabulary`** -- the fixed set of terms a status cell
  may hold, in the plan's phase table and in `docs/plans/index.md`
  (the `plan-index` audit enforces the index half);
* **`subagent-execution-model`** -- implementation happens in
  sub-agents, the plan/spawn/review/retry/commit loop, and when to use
  worktree isolation;
* **`plan-planning-effort`** -- the master plan is planned at high
  effort, and each phase plan states its own planning effort;
* **`subagent-step-guidance`** -- the step table, the effort ladder
  (`low` through `max`), and what makes a good sub-agent brief;
* **`subagent-model-roster`** -- which models sub-agents may be given,
  what each is for, their context windows, and the
  skew-to-the-more-capable-model rule;
* **`plan-review-checklist`** -- what the management session verifies
  after a sub-agent completes;
* **`plan-closeout-sections`** -- the Future work, Bugs fixed and Back
  brief sections; and
* **`plan-push-audit-phase`** -- that every master plan ends with a
  phase running the repository's `PUSH-AUDIT.md` over the whole plan's
  work. This is what gives the pre-push audit a trigger; see the
  `push-audit` audit for the runbook it starts. Since v2 it also
  carries a plan-authoring requirement: because a diff against the
  default branch is empty once a plan's phases have merged, and
  unrelated work landing between phases makes the range underivable
  afterwards, each phase records what put it on the default branch as
  it lands -- the merge commit of its pull request, or every commit of
  the phase where it landed directly. That goes in a `Merged` column
  where the Execution phases are a table, added last so a row which
  omits it still reaches `Status`, or a `Merged:` line where they are
  prose sections. It never goes in the `Status` cell, which
  `plan-status-vocabulary` reserves for a single term.

Every embedded block must be verbatim and at the current version.

Repositories with no `PLAN-TEMPLATE.md` are N/A. Whether every project
should have one is a separate decision, not smuggled in here: a
template's project-specific half has to be written with real knowledge
of the project, and an empty one is worse than none.

The roster is its own block because it changes on a different cadence
-- models ship and retire while the effort ladder sits still. That is
also how models are *managed* fleet-wide: edit
`templates/shared-blocks/subagent-model-roster.md`, bump its version,
commit, and the next daily run files an issue against every lagging
repository.

### What stays project-specific

A section of `PLAN-TEMPLATE.md` is either wholly shared or wholly
project-specific; generic rules and local examples are not interleaved
within a section. Where a section is a generic rule plus a local
example, the rule is the shared block and the example follows it in an
`!!! note "In this project"` admonition.

The admonition is deliberate. Template sections survive into the plans
written from them, which publish through mkdocs-material, so it renders
as a proper callout -- whereas a repeated `### In this project` heading
would mint duplicate anchor IDs and duplicate table-of-contents entries
in every one of those plans.

Only the short runs trailing a shared block need the admonition. Whole
sections that are project-specific in their entirety -- `## Prompt`,
`### Success criteria`, `### Documentation index maintenance` --
announce themselves by their headings and are left alone, as are the
`...` placeholder sections.

Project-specific, and therefore never a shared block:

* the `## Prompt` preamble -- which codebase to explore, which files to
  read first, which external concepts may need research;
* `### Success criteria` -- the project's own build, lint, test and
  documentation gates;
* the worked examples under `### Planning effort` and the
  project-specific entries under the review checklist; and
* `### Documentation index maintenance`, where the project has one.

### Shared blocks

Shared blocks are canonical wording embedded verbatim across
repositories between `<!-- shared-block: <name> v<N> -->` and
`<!-- shared-block-end -->` markers; the canonical copies live in
`templates/shared-blocks/`, whose `README.md` describes the
mechanism. The check fails when a required block is missing, stale,
drifted from the canonical wording, unknown, or missing its end marker.

## Template

`templates/shared-blocks/` holds the canonical copy of each block
listed above. Copy each one verbatim, markers included, into the
matching section of the repository's `PLAN-TEMPLATE.md`.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-25T06:54:21.186929+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#33 |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#79 |
| instar | non-compliant | shakenfist/instar#523 |
| kerbside | non-compliant | shakenfist/kerbside#368 |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#117 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#321 |
| sfui | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3892 |

Details for non-compliant projects:

- **client-python-k3s** (Status): missing shared block plan-status-vocabulary (copy it verbatim from templates/shared-blocks/plan-status-vocabulary.md in the development repository); missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **divergulent** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **instar** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **kerbside** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **occystrap** (Status): missing shared block plan-status-vocabulary (copy it verbatim from templates/shared-blocks/plan-status-vocabulary.md in the development repository); missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **ryll** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
- **shakenfist** (Status): missing shared block plan-push-audit-phase (copy it verbatim from templates/shared-blocks/plan-push-audit-phase.md in the development repository)
<!-- consistency-audit:end -->
