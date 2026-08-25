# Audit: Plan template

## What we check

`PLAN-TEMPLATE.md` is the starting point for every master plan a
repository writes into `docs/plans/`. Most of what it says is not
project-specific at all: how phase files are named, that sub-agents
do the implementation work, what the effort levels mean, which
models are available and when to reach for each. That wording was
copied between repositories by hand and has drifted -- six copies of
the same paragraphs, differing in line wrapping, in which effort
levels they know about, and in whether they mention a model that has
since shipped.

Repositories that carry a `PLAN-TEMPLATE.md` must embed the current
version of each of these shared blocks:

* **`plan-file-conventions`** -- where plans live, how phase files
  are named, how phases are tracked, and the one-commit-per-logical
  -change rule;
* **`plan-status-vocabulary`** -- the fixed set of terms a status
  cell may hold, in the plan's own phase table and in
  `docs/plans/index.md` (the `plan-index` audit enforces the index
  half);
* **`subagent-execution-model`** -- implementation happens in
  sub-agents, the plan/spawn/review/retry/commit loop, and when to
  use worktree isolation;
* **`plan-planning-effort`** -- the master plan is planned at high
  effort, and each phase plan states its own planning effort;
* **`subagent-step-guidance`** -- the step table, the effort ladder
  (`low` through `max`), and what makes a good sub-agent brief;
* **`subagent-model-roster`** -- which models sub-agents may be
  given, what each is for, their context windows, and the
  skew-to-the-more-capable-model rule;
* **`plan-review-checklist`** -- what the management session
  verifies after a sub-agent completes;
* **`plan-closeout-sections`** -- the Future work, Bugs fixed and
  Back brief sections; and
* **`plan-push-audit-phase`** -- that every master plan ends with a
  phase running the repository's `PUSH-AUDIT.md` over the whole
  plan's work. This is the block that gives the pre-push audit a
  trigger; see the `push-audit` audit for the runbook it starts.

Every embedded block must be verbatim and at the current version.

Repositories with no `PLAN-TEMPLATE.md` at all are reported as N/A.
Whether every project should have one is a separate decision, not
smuggled in here -- a template's project-specific half (what to read
before planning, what the success criteria are) has to be written
with real knowledge of the project, and an empty one is worse than
none.

### Why the model roster is its own block

The roster changes on a different cadence to everything else here:
new models ship and old ones retire, while the effort ladder and the
review checklist sit still for months. Keeping it separate means a
model launch bumps one small block, and the issue filed against each
lagging repository says the model roster is stale rather than that
step guidance is stale.

This is also the mechanism for *managing* which models planning
sessions may use. To add, remove or re-describe a model fleet-wide:
edit `templates/shared-blocks/subagent-model-roster.md`, bump its
version, and commit. The next daily audit run marks every repository
still carrying the old roster non-compliant and files the issues
automatically.

### What stays project-specific

The rule is that a section of `PLAN-TEMPLATE.md` is either wholly
shared or wholly project-specific -- generic rules and local
examples are not interleaved within a section. Where a section is a
generic rule plus a local example, the rule is the shared block and
the example follows it in an `!!! note "In this project"`
admonition.

The admonition is deliberate rather than a heading. Master plans
written from this template live in `docs/plans/` and are published
through mkdocs-material, so a template section that survives into a
plan renders as a proper callout there -- and a good deal of this
material does survive: of shakenfist's 125 published plans, 85 carry
a Back brief, 76 carry Success criteria and 44 carry Step-level
guidance. Repeating a `### In this project` heading instead would
also mint duplicate anchor IDs and duplicate table-of-contents
entries in every one of those plans.

Only the short runs that trail a shared block need the admonition.
Whole sections that are project-specific in their entirety --
`## Prompt`, `### Success criteria`, `### Documentation index
maintenance` -- already announce themselves by their headings and
are left alone. So are the `...` placeholder sections, which are
specific to each individual plan rather than to the project.

Project-specific, and therefore never a shared block:

* the `## Prompt` preamble -- which codebase to explore, which files
  to read first, which external concepts may need research;
* `### Success criteria` -- the project's own build, lint, test and
  documentation gates;
* the worked examples under `### Planning effort` and the
  project-specific entries under the review checklist; and
* `### Documentation index maintenance`, where the project has a
  documentation index to maintain.

### Shared blocks

A shared block is canonical wording embedded verbatim across
repositories, delimited by versioned markers:

```markdown
<!-- shared-block: <name> v<N> -->
...canonical wording...
<!-- shared-block-end -->
```

Canonical copies live in `templates/shared-blocks/<name>.md` in this
repository (markers included); see
`templates/shared-blocks/README.md` for the mechanism. The check
fails when an embedded block is missing where required, carries a
stale version, has drifted from the canonical wording, is unknown
(no canonical file), or is missing its end marker.

## Template

`templates/shared-blocks/` holds the canonical copy of each block
listed above. Copy each one verbatim, markers included, into the
matching section of the repository's `PLAN-TEMPLATE.md`.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-25T06:54:21.186929+00:00

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
