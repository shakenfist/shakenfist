# Plan: shared blocks for PLAN-TEMPLATE.md, and a managed sub-agent model roster

## Context

Ten repositories carry a `PLAN-TEMPLATE.md`. Nothing audits any of
them, and they have drifted into three states:

| State | Repositories |
|-------|--------------|
| Full `## Agent guidance` section | shakenfist, ryll, kerbside, divergulent, instar |
| Truncated version of the same section | uncalibrated-sextant |
| No agent guidance at all | occystrap, client-python-k3s |

The six copies with agent guidance share an identical section
skeleton and near-identical prose. The differences are almost
entirely re-wrapping: `### Future work` says the same thing in all
six, wrapped to a different column in each. There is one real
semantic split -- divergulent says "the project's issue tracker
(once one exists)" where the others say "the relevant github bug
tracker".

The drift matters because the content is wrong in ways nobody
noticed:

* **No repository lists `fable`.** The rosters offer opus, sonnet
  and haiku only, so a planner has no way to recommend the most
  capable model for a step that needs it.
* **The context-window claim is stale.** Every copy says "opus has
  1M tokens, sonnet and haiku have 200K". Sonnet is 1M now. That
  paragraph actively steers planners towards opus for a reason that
  no longer holds.
* **The effort ladder stops at `high`.** `xhigh` and `max` exist
  and are the right settings for hard agentic work.

Each of these is a fleet-wide edit that today would mean editing
eight files in eight repositories by hand, which is exactly how the
drift happened in the first place.

## What "good" looks like

The mechanism already exists: `templates/shared-blocks/` plus the
version-marker discipline the `push-audit` check enforces. Editing
a canonical block and bumping its version marks every lagging
repository non-compliant on the next daily run and files the issues
automatically. Adding `fable` should be one file edit, one version
bump, one commit.

The organising rule for deciding what becomes a block: **a section
of the template is either wholly shared or wholly project-specific.**
Generic rules and local examples are not interleaved within a
section. Where a section is a generic rule plus a local example, the
rule is the block and the example follows it in an
`!!! note "In this project"` admonition.

The template itself is top-level and not published, so on GitHub
that admonition shows as literal `!!! note` text. That is the right
trade: nobody reads the template much, whereas master plans written
from it live in `docs/plans/` and *are* published through
mkdocs-material, where it renders properly. The material genuinely
does survive into those plans -- of shakenfist's 125 published
plans, 85 carry a Back brief, 76 carry Success criteria, and 44
carry Step-level guidance. A repeated `### In this project` heading
would render in all of them too, but would mint duplicate anchor
IDs and duplicate table-of-contents entries each time.

Only the three short runs that trail a shared block are marked.
Whole project-specific sections (`## Prompt`, `### Success
criteria`, `### Documentation index maintenance`) already announce
themselves by their headings, and the `...` placeholder sections are
specific to each plan rather than to the project.

That rule requires a restructure, not just markers. Today the
templates interleave at paragraph and bullet granularity --
`### Planning effort` opens with two generic sentences and then
talks about MariaDB accessors -- and `### Documentation index
maintenance` (project-specific) sits between `### Bugs fixed during
this work` and `### Back brief`, so the close-out sections are not
even contiguous.

## Implementation

### 1. Seven shared blocks -- `templates/shared-blocks/`

| Block | Covers | Churn driver |
|-------|--------|--------------|
| `plan-file-conventions` | where plans live, phase file naming, the phase tracking table, one commit per logical change | rare |
| `subagent-execution-model` | implementation happens in sub-agents; the plan/spawn/review/retry/commit loop; worktree isolation | rare |
| `plan-planning-effort` | master plan at high effort; each phase states its planning effort | rare |
| `subagent-step-guidance` | the step table, the effort ladder, what makes a good brief | rare |
| `subagent-model-roster` | which models exist, what each is for, context windows, skew-heavier rule | every model launch |
| `plan-review-checklist` | what the management session verifies after a sub-agent completes | rare |
| `plan-closeout-sections` | Future work, Bugs fixed, Back brief -- headings included | rare |

`subagent-model-roster` is separate from `subagent-step-guidance`
on purpose. It churns whenever a model ships or retires while the
effort ladder sits still for months, and separating them means the
auto-filed issue says the model roster is stale rather than that
step guidance is stale. Verified: bumping the roster to v2 produces
`shared block subagent-model-roster is stale (v1 embedded, v2
current)` and nothing else.

`plan-closeout-sections` includes its own `###` headings and the
`...` placeholders beneath them, because those are sections the plan
author fills in rather than instructions to read. That is safe
because real plans in `docs/plans/` adapt the template rather than
copying it byte-for-byte -- checked against shakenfist's existing
plans, which diverge substantially from the template's headings.

Content corrections folded into the blocks: `fable` added to the
roster with guidance to reserve it for steps that have defeated
opus; the context-window sentence corrected (fable, opus and sonnet
1M, haiku 200K); the effort ladder extended through `xhigh` and
`max`; and divergulent's better "the project's issue tracker, where
one exists" wording adopted fleet-wide over "the relevant github
bug tracker".

### 2. Automated check -- `scripts/audit-check.py`

`check_plan_template` mirrors `check_push_audit`: repositories with
no `PLAN-TEMPLATE.md` are `not_applicable`; those that have one must
carry all seven blocks, current and verbatim. Registered in
`CHECK_NAMES`, in the check list, and in `audit_common.py`
(`AUDIT_METADATA` and its names table).

Whether every repository should have a plan template is deliberately
left as a separate decision. Eight of the sixteen audited
repositories have none, and a template's project-specific half has
to be written with real knowledge of the project -- an empty one is
worse than none.

### 3. Audit spec -- `audits/plan-template.md`

Documents what is checked, which blocks are required, why the model
roster is separate, and what stays project-specific. Registered in
`audits/README.md` and `PROJECT-CONSISTENCY-AUDITS.md`.

## Migration (separate commits, one per repository)

Not started. For each of the eight repositories, in its own branch:

1. Restructure `PLAN-TEMPLATE.md` so no section mixes generic and
   project-specific text. In practice: move the project-specific
   examples out of `### Planning effort` and `### Step-level
   guidance` to sit *after* the block, wrapped in an
   `!!! note "In this project"` admonition; promote the model
   roster to its own `### Model choice` heading; move
   `### Documentation index maintenance` up next to
   `### Success criteria` so the close-out sections become
   contiguous.
2. Embed the seven blocks verbatim.
3. Confirm `audit-check.py` reports `plan-template` as pass.

occystrap and client-python-k3s have no `## Agent guidance` section
at all, so they gain the whole sub-agent workflow rather than having
it rewritten.

A reference restructure of shakenfist's template was assembled and
verified to pass, then reverted -- the canonical wording needs
review before eight copies of it exist.

## Verification

* `check_plan_template` returns `not_applicable` for clingwrap (no
  template) and `fail` with per-block detail for shakenfist
  (template present, no blocks yet). Confirmed.
* A restructured shakenfist template carrying all seven blocks
  returns `pass`. Confirmed against a scratch copy.
* Bumping a block version marks the embedding repository stale, and
  names the block that moved. Confirmed.
