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
   six plans of thirty-six, and a normal PR against merged work is a
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
   documentation and security briefs all apply. Its own incomplete
   plans get the phase like everyone else's -- six of them, counting
   this one, which carries the phase it asks of every other plan.

   `PLAN-TEMPLATE.md` for `development` is deliberately *not* in
   scope: `PLAN-plan-template-blocks` already ruled that whether every
   repository should have a template is a separate decision, and an
   empty one is worse than none. Consequence: until `development` has
   a template, its new plans do not inherit the phase automatically.
   Recorded in Future work rather than solved here.

4. **The sweep covers every incomplete master plan, including the
   not-started ones.** Nineteen of the thirty-six have no landed
   phases at all, so appending the phase costs nothing and shapes
   work that has not been written yet.

5. **The mechanism gets a review point before it is trusted.**
   Phase 3 exists because a mandatory phase written into thirty-six
   plans, that turns out to find nothing, is worse than no phase at
   all -- it is a recurring cost that reads as diligence.

## The churn question, measured

The worry motivating decision 1 is landing several phases of a plan
and then rewriting them all to satisfy an audit that runs at the end.

Two counts sit behind the figures below and they are not the same
event, which is what makes the correction dates read oddly until the
distinction is drawn. The **planning estimate** was read from the
local clones on this machine, several of which had not been fetched
for some time -- one of them from before kerbside's
`PLAN-demo-install` closed out on 2026-08-22. The **sweep count** was
taken on 2026-08-24 in fresh worktrees off each default branch, with
0 open PRs everywhere except shakenfist (two: a bot fix and queue
performance step 7). Both happened on 2026-08-24; only the data
behind the estimate was old.

The estimate was thirty-seven. The sweep counted **thirty-six**:
shakenfist 22, ryll 6, development 6 (five, plus this plan),
kerbside 1, instar 1.

Thirty-six is a count of the plans **in scope for the sweep** -- the
incomplete master plans an `index.md` tracks in the five repositories
phase 2 covered -- and not a count of every plan in the organisation.
Four repositories contributed nothing, for three different reasons:

* **client-python-k3s** has two planning documents and neither is a
  master plan.
* **occystrap** and **sfui** have master plans but no index a
  scope list can be derived from: sfui has three plans and no
  `docs/plans/index.md` at all, and occystrap's index is a bullet
  list naming two of its seven master plans, with no status column.
* **divergulent** has both, and was missed. See the fourth
  correction below.

Four of the planning figures were wrong, and the corrections are
recorded here rather than quietly overwritten:

* **kerbside is 1, not 3.** The estimate counted rows from
  kerbside's *Standalone plans* table alongside its master plans,
  and `PLAN-demo-install` closed out on 2026-08-22 -- after the
  local clone's last fetch, so the estimate still saw it open.
* **shakenfist has one root-level `PLAN-*.md`, not seventeen.** The
  estimate was taken from a local clone well behind `develop`; at
  `develop` HEAD only `PLAN-TEMPLATE.md` sits at the root, and
  nothing at the root is tracked by `index.md`. The warning written
  into the phase 2 brief was therefore unnecessary, though harmless.
* **ryll's shared blocks are current.** The estimate had ryll
  failing two blocks; that too was clone staleness. It passes
  `push-audit` outright.
* **divergulent has four incomplete master plans, not none.** The
  estimate's repository list said it had none and the sweep
  inherited that without rechecking. It has nine master plans in a
  conforming `docs/plans/index.md` -- `PLAN-published-cache`,
  `PLAN-release-1.0`, `PLAN-patch-classification` and
  `PLAN-curation-cli-ergonomics` are the incomplete four -- and it
  carries a `PUSH-AUDIT.md`, so it is exactly the shape the
  mechanism is for. This is a gap in phase 2, not a scope
  exclusion: phase 2's definition of done names five repositories,
  while decision 4 says the sweep covers *every* incomplete master
  plan. Phase 3 decides which of those two is right before the
  count is quoted anywhere else.

| Exposure | Plans | What the audit phase means there |
|----------|-------|----------------------------------|
| No phases landed (Not started / Proposed / Blocked) | 19 | Purely prospective; every phase is written knowing the audit is coming |
| Early or middle | 11 | Most phases still ahead of the audit |
| Near complete (70% or more of phases landed) | 6 | shakenfist's Kerbside VDI tokens (9 of 10), Queue performance (6 of 7) and Database load reduction (5 of 7); kerbside's proxy dev releases (5 of 5); development's Consistency audits v2 (4 of 4) and Review coverage (4 of 5) |

The unit is **phases landed out of the phases the plan carried
before this sweep appended its audit phase**, counted from the
plan's own phase list rather than from an execution table -- the two
disagree for `PLAN-review-coverage`, whose table has eight rows
because two phases split across repositories. Two of the six are at
100% and still incomplete, which is the point of counting phases
rather than statuses: kerbside's proxy dev releases has all five
phases landed and an operator-driven Gerrit recheck outstanding, and
Consistency audits v2 has all four landed with two of them recorded
as `MOSTLY DONE`.

**The near-complete bucket is 6, not the 2 first claimed.** That
figure came from reading shakenfist's index alone and never counting
development's or kerbside's own plans -- an error of scope, not of
arithmetic. Six plans of thirty-six will meet their audit over work
that has already merged.

That is still a minority, and the conclusion in decision 1 survives:
just over half the incomplete plans have no landed work at all, so
the mechanism mostly shapes phases that have not been written yet.
But six is enough that the review point in phase 3 is doing real
work rather than confirming a foregone result.

The sweep itself -- 36 plan files across five repositories, four
index files, one section and one table row each -- was a one-time
mechanical migration with no rework in it. It is a large file count,
not churn.

## Implementation

Work happens in a worktree off `shakenfist/development`; this plan
file lands with the change (per `CLAUDE.md`).

### Execution

Phases are the sections below rather than separate files, following
this repository's convention.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Foundations | Complete | `5b1fb74` (#49) |
| 2. Fleet sweep | Complete | `ff92357` (#50) |
| 3. Review point | Not started | |
| 4. Push audit | Not started | |

The `Merged` column is the convention this plan introduces, applied
to the plan that introduces it. It goes last so that a row which
omits it still reaches `Status`, and it is not the `Status` cell,
which `plan-status-vocabulary` reserves for a single term. Phase 4
audits the accumulated diff of those commits against `main`.

### 1. Foundations -- this repository

* **`templates/shared-blocks/plan-push-audit-phase.md`** (new; v1,
  now v2). The canonical wording of the final phase: what it is,
  that it runs `PUSH-AUDIT.md` against the accumulated diff of the
  whole plan rather than one phase's, that findings land as their
  own PR, that a plan whose repository has no `PUSH-AUDIT.md` says
  so explicitly rather than omitting the phase silently, and where
  each phase's landing commit is recorded so that the audit has a
  range to run over once the phases have merged.

  **Correction, v1 to v2.** v1 said to derive that range: from the
  merge base of the plan's first phase commit to the default branch,
  restricted to the paths the plan touched. Measured against ryll's
  real history that is 338 files and 118k insertions for the five
  phases of `PLAN-idle-cpu-and-latency`, because unrelated work
  lands on the default branch between a plan's phases and any anchor
  of the form "since the plan file appeared" sweeps all of it in.
  ryll's embedded copy dropped the bullet rather than following it,
  which would have read as drift on the next daily run. v2 replaces
  derivation with recording: the commit that put each phase on the
  default branch goes into the plan as the phase lands, in a
  `Merged` column or a `Merged:` line depending on the plan's shape,
  and never in `Status`, which `plan-status-vocabulary` reserves for
  a single term. Reconstructing this repository's own plans (below)
  then found two shapes v2's first draft did not cover -- phases
  that landed as direct commits with no merge commit at all, and
  phases that accreted across many pull requests -- so v2 says to
  record every commit a phase landed under and to say when no range
  is recoverable.

  The v1 wording arrived from review during phase 2 and edited v1 in
  place; the reasoning then was that no `PLAN-TEMPLATE.md` embedded
  the block yet, so there was no copy to mark stale. That is why
  this correction is written here rather than being visible only as
  a version number: the in-place edit left no record of what changed
  or why.

  **Blast radius of the bump.** None, measured: no repository's
  default branch embeds this block at any version -- checked against
  `PLAN-TEMPLATE.md` on shakenfist, ryll, kerbside and instar via
  the GitHub contents API, and `docs/audits/plan-template.md` shows
  those four `compliant` only because its last regeneration
  (2026-08-24T07:04Z) predates phase 1's merge, which added the
  block to `PLAN_TEMPLATE_BLOCKS`. Phase 1's bump is what marks them
  non-compliant; v2 marks nothing newly stale on top of that. The
  one copy of v2 anywhere is shakenfist/ryll#319, which is still
  open. The ordering that keeps that true is manual and nothing
  records it on the ryll side, so it is stated here: this pull
  request lands first, and ryll#319 re-copies the block from this
  repository's `main` rather than from a branch, because the wording
  was revised twice in review. If ryll#319 lands first, or carries a
  copy taken mid-review, the next daily run files a stale-block issue
  against ryll -- self-correcting, but it arrives as an audit failure
  rather than as a known consequence.
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

* shakenfist was believed to keep 17 `PLAN-*.md` at the repository
  root as well as 129 in `docs/plans/`. It does not: at `develop`
  HEAD only `PLAN-TEMPLATE.md` sits at the root, and the figure came
  from a stale clone (see *The churn question, measured*). The rule
  the brief carried is still the right one -- the sweep covers what
  `index.md` tracks -- it just had nothing to exclude.
* Index formats differ: shakenfist is
  `Date|Plan|Intent|Status|Phases`, development is four columns,
  ryll lists phase files inline in the row.
* kerbside, occystrap and divergulent have no `order.yml`; phase
  files are not registered there anyway.
* development's plans have no separate phase files -- phases are
  sections inside the master plan, so the phase is a section and a
  row in that plan's own Execution table.
* sfui has three plans and no `docs/plans/index.md`, so there is no
  in-scope list to derive; it is out of scope for the sweep and
  recorded as such rather than counted as having no plans.
  occystrap is the same shape with a weaker index, and divergulent
  should have been in scope and was not -- both recorded under *The
  churn question, measured*.

### 3. Review point -- after the first real run

Queue performance (shakenfist, 6 of 7 at the sweep count, step 7 in
flight) reaches its audit phase first, over six merged phases of
database and queue work.

That premise moved while phase 2 was in review: step 7 landed and
`index.md` on `develop` now reads `Complete | 7 of 7`, while the
sweep that gives the plan its audit phase is still open as
shakenfist#3873. So the first real run will be an audit of a plan
that is already complete -- the merged-range case the shared block
now describes, arrived at by accident rather than by design. It is
still the right measurement, and it makes phase 3's decision 2 more
pressing rather than less: nothing stopped a plan being marked
complete without its audit.

v2's carve-out is scoped so that this measurement survives it. The
carve-out exists so that plans which were already `Complete` when
the sweep reached their repository -- the great majority of the
fleet's plans, and none of them touched by the sweep -- are not
reopened to acquire a phase; it binds on whether a plan *carries*
the phase, not on the date it reached `Complete`. Queue performance
carries the phase -- shakenfist#3873 put it there -- so it runs its
audit even though `index.md` marked it complete while that sweep
was in review. Phrasing the carve-out as "before this convention landed"
would have exempted it, and would also have been uncheckable from
an embedded copy, which cannot tell which version of the block its
repository received or when.

Phase 3 reads what that audit actually found and decides four
things:

1. Whether the phase stays mandatory for every plan, becomes
   conditional on plan size, or is withdrawn. A mandatory phase that
   finds nothing is a recurring cost that reads as diligence, and
   this is the phase that catches that.
2. Whether a passing verdict should be mechanically checkable per
   plan -- a check that every master plan an `index.md` tracks
   carries the phase -- or left to `PLAN-TEMPLATE.md` to deliver.
   Nothing checks it today: `check_push_audit` looks at `AGENTS.md`,
   `check_plan_template` looks at the template, and
   `check_plan_index` checks columns, dates and status vocabulary.
   The thirty-six plans this sweep edited are held in place by the
   sweep alone. Building the check before phase 3's decision 1 is
   settled would be the same ceremony this plan is guarding against,
   which is why it is a decision here and not a phase 1 deliverable.
3. Whether the sweep's repository scope was right. The plan's own
   decision 4, under `## Decisions` above, says the sweep covers
   every incomplete master plan; phase 2's definition of done names
   five repositories and divergulent's four are outside both. Either
   the scope widens and divergulent is swept, or the plan says why an
   `index.md`-tracking repository with a `PUSH-AUDIT.md` is excluded.
4. What to do about the thirty-six plans the phase 2 sweep already
   edited. They carry v1's "derive the range from the merge base"
   sentence, which v2 retracts, and none of them records a landing
   commit for the phases that have already merged. This repository's
   own five are fixed in the same change that bumps the block -- they
   are the canon's own plans and could not be left contradicting it
   -- but the fleet-wide backfill is deliberately not done here. It
   touches five repositories and needs the same sub-agent sweep phase
   2 ran, and if phase 3's decision 1 makes the phase conditional
   then some of those plans stop needing a range at all. Doing it
   before that decision would be re-sweeping thirty-six plans to
   install a convention that might be withdrawn a fortnight later.
   Until it happens, a reader of one of those plans gets retracted
   guidance, which is the cost of the deferral and is recorded here
   rather than discovered.

### 4. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of all three phases
against `main`. This plan carries the phase it asks of every other
plan, which is also the second real test of the mechanism after
queue performance -- and the more interesting one, because the
runbook it exercises is the one phase 1 wrote for this repository
and nobody has run yet. Findings land as their own pull request;
the plan is not complete until each is resolved or declined in
writing, with the reason recorded here. If the audit finds nothing,
say so in one sentence, and feed that into phase 3's decision.

## Risks and mitigations

* **The audit finds nothing and the phase becomes ceremony.** This is
  the real risk, and phase 3 is the mitigation -- with a named
  measurement (queue performance) rather than an intention to review
  later.
* **Thirty-six hand-edited plan files drift into thirty-six
  wordings.** Mitigated by the shared block being the source and the
  sub-agent briefs quoting it, and by the management session
  reviewing each repository's diff before its commit. Outcome: the
  wording is consistent in substance and deliberately varied in
  form, because each sub-agent matched its own repository's idiom --
  bold-lead paragraphs in shakenfist, a bullet in ryll's per-phase
  intent list, a table cell where the table's own column carries the
  description. That was the right call and is worth keeping.
* **Colliding with `PLAN-plan-template-blocks`.** Mitigated by
  decision 2: this plan adds the block and updates that plan's block
  list, and touches no repository's `PLAN-TEMPLATE.md` itself.
* **`development`'s new `PUSH-AUDIT.md` is written blind.** It is the
  first pre-push audit written for a repository whose product is
  automation rather than a service. Mitigated by running it once, on
  this plan's own phase 1, before phase 2 depends on it.
* **The `Merged` column collides with a plan-table parser.** The
  column changes the shape of every plan phase table that adopts it,
  and shakenfist's `tools/check-plan-status.py` parses those tables
  in `pre-commit`. Checked: `status_tables()` recognises a header by
  the separator row beneath it and takes the status column by name
  (`names.index('status')`), not by position, so an extra column is
  invisible to it. The one way to break it is a row with too few
  cells to reach the status index, which the parser reports as a
  `short` row rather than dropping -- so `Merged` goes last, where a
  row that omits it still reaches `Status`. That is why the block
  says "added last" rather than leaving the position open.

## Definition of done

* `check_push_audit` fails a repository whose `AGENTS.md` does not
  reference `PUSH-AUDIT.md`, and the fleet table shows which ones.
* `plan-push-audit-phase` is in `templates/shared-blocks/`, listed in
  its README, and required by `check_plan_template`.
* `PLAN-plan-template-blocks.md` names nine blocks, not eight.
* `plan-push-audit-phase` is at v2, and this repository's own five
  master plans each record a landing commit for every merged phase,
  or say why no range is recoverable. The thirty-six plans across
  shakenfist, ryll, kerbside and instar still carry v1's retracted
  wording; backfilling them is phase 3's decision 4, so this plan is
  not done on that count until the decision is made.
* `development` has a `PUSH-AUDIT.md` that its own `push-audit` check
  passes, and it is no longer `N/A` in the compliance table.
* Every incomplete master plan in shakenfist, ryll, kerbside, instar
  and development ends with a push-audit phase and, in the
  repositories whose index carries phase counts, its `index.md` row
  reflects the new count. Done: 36 plans across the five
  repositories and four of the five indexes, verified by re-deriving
  the in-scope list from each repository's own `index.md` and
  confirming every plan on it was touched. development is the fifth:
  its index deliberately carries a one-line status and no phase
  column, `check_plan_index` enforces that it has no arithmetic to
  recompute, and it is correctly untouched here. shakenfist's
  `pre-commit` carries a "plan statuses and index arithmetic agree"
  hook, which independently confirmed its 18 recomputed counts --
  18 rather than 22 because four of its incomplete plans carry an
  em-dash in the phases column, having no phase list yet.
  Divergulent's four, recorded under *The churn question, measured*,
  are outside this criterion until phase 3 settles the scope.
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
across thirty-six plan files and eight repositories.
