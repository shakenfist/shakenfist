# Plan: give the pre-push audit a trigger

## Context

`PUSH-AUDIT.md` is a two-wave sub-agent runbook -- build and style
checks, then code-quality, test, documentation and security review by
four parallel judgment agents. Eight repositories carry one and the
`push-audit` consistency check keeps their shared blocks current.

Nothing runs it.

The check verifies the file's *contents* with some care -- correct
name, four shared blocks, current versions -- and never verifies that
anything *references* it. Measured across the eight repositories that
carry one:

| Referencing surface | Repositories that point at `PUSH-AUDIT.md` |
|---------------------|--------------------------------------------|
| `AGENTS.md` | 3 of 8 -- ryll, divergulent, client-python-k3s |
| `CLAUDE.md` | none |
| `PLAN-TEMPLATE.md` | none |
| git hooks | none |
| CI | none |

Of the three, only client-python-k3s says *when*: "Before pushing,
work through the checks in `PUSH-AUDIT.md`." ryll and divergulent
carry passive index entries -- this file exists, here is what it is
for. The other five say nothing at all, shakenfist and kerbside
included, which are the two repositories the audit is most often
wanted in.

The audit has only ever run because Mikal remembered it. Shipping a
PR per phase through `/next-phase` raised how often he has to
remember, which is what surfaced the gap, but per-phase shipping is
not the cause -- the runbook has never had a trigger.

The fleet already has three review mechanisms and this is the only
one without automation behind it:

| Mechanism | Scope | Trigger | Dedup |
|-----------|-------|---------|-------|
| Consistency audits | Whole fleet, mechanical | Daily cron | Stable per-check issue identity |
| Code review tracking | Whole codebase, human, file by file | Review sessions, coverage alerting | Blob SHA stamps in `REVIEWS.md` |
| Pre-push audit | The delta, judgment | *nothing* | *none* |

**Intended outcome:** every master plan ends with a phase that runs
the pre-push audit, that phase arrives in new plans automatically
because it is part of `PLAN-TEMPLATE.md`, and the consistency audit
fails a repository whose plan template or `AGENTS.md` has lost the
reference.

## What "good" looks like

* **The trigger lives where the implementing session is already
  reading.** A phase in the plan beats a rule in `CLAUDE.md`, because
  the session executing a plan is following its phase table step by
  step. It also beats a git hook, which would block pushes during
  CI-watching loops and get `--no-verify`'d.
* **The wording is shared, not copied.** The phase text becomes a
  block in `templates/shared-blocks/`, so improving it is one file
  edit and one version bump rather than eight hand-edits.
* **The reference gap is mechanically checked.** A repository that
  drops the reference is non-compliant the next morning.
* **The mechanism proves itself before it is trusted.** One plan runs
  its audit phase for real, and what it finds decides whether the
  phase stays mandatory.

## Decisions

1. **A final phase per master plan, not a step per phase plan.** A
   step at the end of every phase would audit each delta at its
   cheapest moment, but multiplies the fixed cost of five agents
   orienting on `AGENTS.md` by the phase count, and most phases are
   narrow enough that most agents would find nothing. A final phase
   pays that cost once per plan.

   The cost is that findings arrive after the phases have merged, so
   the fix is a new PR against the landed feature rather than a
   change to an unmerged branch. Measured (below) that exposure is
   two plans fleet-wide, and a normal PR against merged work is a
   cheaper shape than a rebase across live phase branches. Accepted,
   with decision 5 as the check on it.

2. **The phase text is a shared block, and `PLAN-TEMPLATE.md` gains
   it as a ninth block.** `PLAN-plan-template-blocks` built eight
   blocks, `check_plan_template` and `docs/audits/plan-template.md`.
   A ninth there is one canonical edit rather than a hand-edit of
   every template, which is the drift that plan exists to remove.

   **Correction.** This was first written on the premise that the
   migration into the repositories' templates "has not started",
   taken from that plan's own Migration heading, which still says
   so. The generated compliance table in
   `docs/audits/plan-template.md` says otherwise: instar, kerbside,
   ryll and shakenfist are `compliant`, so they already carry all
   eight blocks. The migration has landed in four of the eight
   repositories and the plan text is stale; that stale line is
   corrected as part of this work.

   The premise was wrong and the decision survives it -- if four
   templates are already migrated, hand-editing them in parallel
   would have been worse rather than better, because it would race
   the mechanism designed to update them.

3. **`development` gets its own `PUSH-AUDIT.md`.** It is `N/A` today
   -- no pre-push audit file, no plan template -- while being the
   repository that defines the standard. It has 5,000 lines of audit
   script, 3,200 lines of tests, a pre-commit running five suites,
   and workflow templates shipped to the fleet. Wave 1 and the
   documentation and security briefs all apply. Its five in-progress
   plans get the phase like everyone else's.

   `PLAN-TEMPLATE.md` for `development` is deliberately *not* in
   scope: `PLAN-plan-template-blocks` already ruled that whether every
   repository should have a template is a separate decision, and an
   empty one is worse than none. Consequence: until `development` has
   a template, its new plans do not inherit the phase automatically.
   Recorded in Future work rather than solved here.

4. **The sweep covers every incomplete master plan, including the
   not-started ones.** Twenty-one of the thirty-seven have no landed
   phases at all, so appending the phase costs nothing and shapes
   work that has not been written yet.

5. **The mechanism gets a review point before it is trusted.**
   Phase 3 exists because a mandatory phase written into thirty-seven
   plans, that turns out to find nothing, is worse than no phase at
   all -- it is a recurring cost that reads as diligence.

## The churn question, measured

The worry motivating decision 1 is landing several phases of a plan
and then rewriting them all to satisfy an audit that runs at the end.
Counted against the fleet on 2026-08-24, with 0 open PRs everywhere
except shakenfist (two: a bot fix and queue performance step 7):

**Thirty-seven incomplete master plans** -- shakenfist 22, ryll 6,
development 5, kerbside 3, instar 1. occystrap, divergulent, sfui and
client-python-k3s have none.

| Exposure | Plans | What the audit phase means there |
|----------|-------|----------------------------------|
| No phases landed (Not started / Proposed / Blocked) | 21 | Purely prospective; every phase is written knowing the audit is coming |
| Early or middle (1 of 3, 4 of 11, 2 of 6, 3 of 7, 2 of 8, ...) | 14 | Most phases still ahead of the audit |
| Near complete | 2 | shakenfist's Kerbside VDI tokens (9 of 10) and Queue performance (6 of 7) |

The retrospective-rework scenario applies to two plans, not
thirty-seven. Fifty-seven percent of incomplete plans have no landed
work, so the mechanism mostly shapes future phases rather than
reworking past ones.

The sweep itself -- 37 plan files, ~6 index files, one section and one
table row each -- is a one-time mechanical migration with no rework in
it. It is a large file count, not churn.

## Implementation

Work happens in a worktree off `shakenfist/development`; this plan
file lands with the change (per `CLAUDE.md`).

### Execution

Phases are the sections below rather than separate files, following
this repository's convention.

| Phase | Status |
|-------|--------|
| 1. Foundations | Complete |
| 2. Fleet sweep | Not started |
| 3. Review point | Not started |

### 1. Foundations -- this repository

* **`templates/shared-blocks/plan-push-audit-phase.md`** (new, v1).
  The canonical wording of the final phase: what it is, that it runs
  `PUSH-AUDIT.md` against the accumulated diff of the whole plan
  rather than one phase's, that findings land as their own PR, and
  that a plan whose repository has no `PUSH-AUDIT.md` says so
  explicitly rather than omitting the phase silently.
* **`scripts/audit-check.py`** -- extend `check_push_audit` with the
  reference checks: `AGENTS.md` must mention `PUSH-AUDIT.md`, and
  where the repository has a `PLAN-TEMPLATE.md` it must carry the new
  block. Add the block to `PLAN_TEMPLATE_BLOCKS` so
  `check_plan_template` requires it too.
* **`docs/audits/push-audit.md`** -- document the reference checks in
  "What we check" and the fix instructions.
* **`scripts/test_audit_check.py`** -- cases for: `AGENTS.md` with no
  reference fails; with a reference passes; a repository with no
  `PUSH-AUDIT.md` stays `not_applicable` regardless of `AGENTS.md`;
  the new block missing from `PLAN-TEMPLATE.md` fails
  `plan-template`.
* **`docs/plans/PLAN-plan-template-blocks.md`** -- update the block
  count and table to include the ninth block, so the pending
  migration carries it.
* **`PUSH-AUDIT.md`** (new, for `development`) -- written for this
  repository rather than copied: wave 1 is `pre-commit run
  --all-files` (actionlint on workflows and templates, shellcheck,
  flake8, skillsaw, four test suites); the judgment agents cover the
  audit scripts, the shared-block canon, and the workflow templates
  shipped to other repositories. Carries the four required shared
  blocks.
* **`AGENTS.md`** -- the reference that makes this repository pass
  its own new check.

**Blast radius of the reference check**, measured against local
clones before landing: of the eight repositories carrying a
`PUSH-AUDIT.md`, three already reference it from `AGENTS.md` (ryll,
divergulent, client-python-k3s) and five do not. Two of those five
were otherwise compliant and so become non-compliant on the next
daily run purely because of this check: **shakenfist and kerbside**.
The other three (instar, occystrap, sfui) are already non-compliant
on shared blocks and gain one more line of detail. Local clones lag
their remotes, so the daily run is the authority on the exact
number; the shape -- two newly failing, three gaining a line -- is
what to expect. The fix in each case is one line in `AGENTS.md`, and
phase 2's sub-agents do it while they are in the repository anyway.

**Blast radius of the ninth `PLAN-TEMPLATE.md` block**, which the
first draft of this plan missed entirely. `instar`, `kerbside`,
`ryll` and `shakenfist` are `compliant` on `plan-template` today,
meaning they carry all eight existing blocks. Naming a ninth in
`PLAN_TEMPLATE_BLOCKS` marks all four non-compliant on the next
daily run and files four issues.

Combined with the two above, **the next run after this lands files
six new issues, not two.** That is the shared-block mechanism
working as designed -- edit the canonical copy, the fleet is told,
the issues are the worklist -- but an unstated fleet effect is
exactly the defect this repository's own `PUSH-AUDIT.md` brief names
under "blast radius of a changed check", so it is stated here rather
than discovered at 06:00 UTC.

Deliberately not deferred. Splitting the block file from its entry
in `PLAN_TEMPLATE_BLOCKS`, to spare four repositories an issue until
the wording settles, would mean two fleet-wide notifications instead
of one: the issues now, and a re-file after any version bump. One
round of six issues against a worklist that already exists is
cheaper than two rounds against the same four repositories.

### 2. Fleet sweep -- one sub-agent per repository

One sub-agent per repository, each in its own branch, each reviewed
by the management session before its commit is proposed. The sweep
adds the phase to every incomplete master plan and its `index.md`
row.

Per-repository variance the briefs must handle, rather than letting
six agents invent six shapes:

* shakenfist keeps 17 `PLAN-*.md` at the repository root as well as
  129 in `docs/plans/`; the sweep covers what `index.md` tracks.
* Index formats differ: shakenfist is
  `Date|Plan|Intent|Status|Phases`, development is four columns,
  ryll lists phase files inline in the row.
* kerbside, occystrap and divergulent have no `order.yml`; phase
  files are not registered there anyway.
* development's plans have no separate phase files -- phases are
  sections inside the master plan, so the phase is a section and a
  row in that plan's own Execution table.
* sfui has no `docs/plans/index.md` and three plans; it is
  out of scope for the sweep and recorded as such.

### 3. Review point -- after the first real run

Queue performance (shakenfist, 6 of 7, step 7 in flight) reaches its
audit phase first, over six merged phases of database and queue work.
Phase 3 reads what that audit actually found and decides one thing:
whether the phase stays mandatory for every plan, becomes conditional
on plan size, or is withdrawn. A mandatory phase that finds nothing
is a recurring cost that reads as diligence, and this is the phase
that catches that.

## Risks and mitigations

* **The audit finds nothing and the phase becomes ceremony.** This is
  the real risk, and phase 3 is the mitigation -- with a named
  measurement (queue performance) rather than an intention to review
  later.
* **Thirty-seven hand-edited plan files drift into thirty-seven
  wordings.** Mitigated by the shared block being the source and the
  sub-agent briefs quoting it, and by the management session
  reviewing each repository's diff before its commit.
* **Colliding with `PLAN-plan-template-blocks`.** Mitigated by
  decision 2: this plan adds the block and updates that plan's block
  list, and touches no repository's `PLAN-TEMPLATE.md` itself.
* **`development`'s new `PUSH-AUDIT.md` is written blind.** It is the
  first pre-push audit written for a repository whose product is
  automation rather than a service. Mitigated by running it once, on
  this plan's own phase 1, before phase 2 depends on it.

## Definition of done

* `check_push_audit` fails a repository whose `AGENTS.md` does not
  reference `PUSH-AUDIT.md`, and the fleet table shows which ones.
* `plan-push-audit-phase` is in `templates/shared-blocks/`, listed in
  its README, and required by `check_plan_template`.
* `PLAN-plan-template-blocks.md` names nine blocks, not eight.
* `development` has a `PUSH-AUDIT.md` that its own `push-audit` check
  passes, and it is no longer `N/A` in the compliance table.
* Every incomplete master plan in shakenfist, ryll, kerbside, instar
  and development ends with a push-audit phase, and its `index.md`
  row reflects the new phase count.
* `pre-commit run --all-files` passes in this repository.
* Phase 3 records, in this plan, what queue performance's audit found
  and what was decided about the phase remaining mandatory.

## Future work

* **`development` has no `PLAN-TEMPLATE.md`**, so its new plans will
  not inherit the phase automatically. Either it gains one or
  `/next-phase` grows the check; deliberately not decided here.
* **Move the mechanical waves out of the runbook.** Wave 1 and the
  wave-2 sweep are grep and shell. They belong in a `tools/` script
  that pre-commit and CI both call, where they cannot be skipped and
  cost nothing. The judgment agents are the only part that needs a
  human trigger.
* **Path-gate the judgment agents.** A docs-only phase does not need
  the opus/high security review. `git diff --name-only` can skip
  agents whose inputs the diff does not touch -- the same idea as the
  existing `expensive-lane-path-filter` audit.
* **Promote the audit's mechanical invariants to whole-tree checks.**
  `state_machine.md` against the `state_targets` maps is a script;
  `mariadb.get_all_*(` without a `# nopushdown:` tag is a grep. Today
  they only ever see added lines, so pre-existing violations are
  invisible.
* **A CI lane that fixes rather than complains.** The more
  interesting long-term shape, and a better fit as a separate job
  than as part of the automated reviewer, which already has a context
  problem on large diffs.
* **Whole-codebase runs of the judgment agents are explicitly not
  wanted.** The briefs are diff-scoped, findings have no dedup
  identity so every run re-reports the same pile, and the
  whole-codebase niche is deliberately occupied by
  `docs/code-review-tracking.md` -- human, file by file, attested.

## Back brief

Before phase 2 begins, the management session confirms with Mikal:
the canonical wording of the shared block, and `development`'s
`PUSH-AUDIT.md`. Both are cheap to propose and expensive to redo
across thirty-seven plan files and eight repositories.
