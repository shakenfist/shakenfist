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

### 1. Nine shared blocks -- `templates/shared-blocks/`

| Block | Covers | Churn driver |
|-------|--------|--------------|
| `plan-file-conventions` | where plans live, phase file naming, the phase tracking table, one commit per logical change | rare |
| `plan-status-vocabulary` | the fixed set of terms a status cell may hold | rare |
| `subagent-execution-model` | implementation happens in sub-agents; the plan/spawn/review/retry/commit loop; worktree isolation | rare |
| `plan-planning-effort` | master plan at high effort; each phase states its planning effort | rare |
| `subagent-step-guidance` | the step table, the effort ladder, what makes a good brief | rare |
| `subagent-model-roster` | which models exist, what each is for, context windows, skew-heavier rule | every model launch |
| `plan-review-checklist` | what the management session verifies after a sub-agent completes | rare |
| `plan-closeout-sections` | Future work, Bugs fixed, Back brief -- headings included | rare |
| `plan-push-audit-phase` | every master plan ends with a phase running `PUSH-AUDIT.md` over the whole plan's work | rare |

This started as seven. `plan-status-vocabulary` was added when the
`plan-index` audit needed the same wording on both sides, and
`plan-push-audit-phase` by `PLAN-push-audit-phase.md`, which needed
somewhere to put the pre-push audit's trigger and found that this
plan's migration was the only pass that would carry it into eight
templates without editing them twice. The migration below covers
all nine.

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
carry all nine blocks, current and verbatim. Registered in
`check_calls()` and in `audit_common.py` (`AUDIT_METADATA` and
`ISSUE_TITLES`).

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

Landed in four of the eight repositories: instar, kerbside, ryll
and shakenfist are `compliant` on the `plan-template` check.
Outstanding for client-python-k3s, divergulent and occystrap
(sfui has no template and is N/A). For each remaining repository,
in its own branch:

1. Restructure `PLAN-TEMPLATE.md` so no section mixes generic and
   project-specific text. In practice: move the project-specific
   examples out of `### Planning effort` and `### Step-level
   guidance` to sit *after* the block, wrapped in an
   `!!! note "In this project"` admonition; promote the model
   roster to its own `### Model choice` heading; move
   `### Documentation index maintenance` up next to
   `### Success criteria` so the close-out sections become
   contiguous.
2. Embed the nine blocks verbatim.
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
  returns `pass`. Confirmed against a scratch copy -- at seven,
  before `plan-status-vocabulary` and `plan-push-audit-phase` were
  added. Re-confirm at nine as part of the migration.
* Bumping a block version marks the embedding repository stale, and
  names the block that moved. Confirmed.

## Push audit

This is the last phase of the plan and it is not optional. It comes
after *Migration* and *Verification* because those are where the
bulk of the work is: a push audit run at the end of *Implementation*
would cover none of the eight repositories the migration touches.

There are two obligations here and they are not the same one:

* **Each migration pull request** runs its own repository's
  `PUSH-AUDIT.md` over that pull request, against that repository's
  default branch, as part of that pull request. That is a per-repo
  obligation discharged eight times.
* **This plan's final phase** runs this repository's
  `PUSH-AUDIT.md` over the accumulated diff of the whole plan --
  the shared blocks, `check_plan_template` and the audit spec --
  against `main`, not over the last commit alone. It runs once the
  migration is complete.

The blocks half of that diff did not land in one place, so the range
is recorded here rather than derived when the phase runs.
`plan-push-audit-phase` asks for a `Merged:` line per phase where a
plan's phases are prose sections rather than a table; this plan's
implementation sections were built in four landings, so the line
names the set:

Merged: `2468dda` and `5918f5b` direct to `main`; `5b1fb74` (#49)
and `ff92357` (#50). Between them those cover all nine blocks,
`check_plan_template`, and `docs/audits/plan-template.md` --
`2468dda` carried seven blocks plus the check and the spec page,
`5918f5b` added `plan-status-vocabulary`, and #49 and #50 added and
then revised `plan-push-audit-phase`. The migration commits in the
other four repositories are named in their own pull requests and are
audited there.

**Two corrections to that line, both worth reading before doing this
for another plan.** It first read as seven commits "all direct to
`main`", derived from `git log -- templates/shared-blocks/`. That
was wrong twice over. Two of the seven were not direct: `51d872f`
arrived inside #21 and `e2585e3` inside #49, so anchoring on either
would have audited one commit of a pull request rather than the pull
request, and `e2585e3` was double-counted against `5b1fb74` besides.
A path-filtered log does not distinguish the two cases;
`git rev-list --first-parent` does, and is now what the block tells
you to use.

The larger error was scope. Four of the seven -- `9a74046`
(`readme-discipline`), `fbcd759` (`comment-proportion`), `3abb973`
(`plan-phase-references`) and `51d872f` (`llm-doc-discipline`) --
are not this plan's work at all. They touch
`templates/shared-blocks/` but none of them is one of the nine
blocks `PLAN_TEMPLATE_BLOCKS` names, so an audit anchored on them
would have swept in four unrelated consistency audits. Recording a
range by the directory a plan happens to touch is the same mistake
as deriving it from a date: both answer "what else was going on"
rather than "what did this plan do".

Findings from either land as their own pull request; the plan is not
complete until each is resolved or declined in writing, with the
reason recorded here. If an audit finds nothing, say so in one
sentence.
