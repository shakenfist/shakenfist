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
  plan. Phase 3's decision 3 resolved it in favour of decision 4 --
  divergulent is in, and phase 4 sweeps it.

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
| 2. Fleet sweep | Complete | `ff92357` (#50), `ac34b76..240d278` |
| 3. Review point | Complete | `81dc421` (#83) |
| 4. Fleet backfill | Complete | `fd0678c` (#113), `a19b706` (#133) |
| 5. Push audit | Complete | `1806a7c` (#154), `fc723f3` (#155) |

The `Merged` column is the convention this plan introduces, applied
to the plan that introduces it. It goes last so that a row which
omits it still reaches `Status`, and it is not the `Status` cell,
which `plan-status-vocabulary` reserves for a single term. Phase 2
carries a range beside its merge because four of its commits landed
on `main` directly rather than through its pull request, which is
the "every commit of the phase, or its `first..last` range" form
the v3 block already allows. That form is a record of which commits
landed, not a diff range: `A..B` excludes `A`, so the diff covering
the cell is `ac34b76^..240d278`, one commit wider than a literal
`ac34b76..240d278` and identical to the `ff92357..240d278` decision
1 lists. The excluded commit is `ac34b76` itself, which is the one
that bumped the shared block from v1 to v2. Phase 5 audits the
accumulated diff of those commits against `main`.

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

### 3. Review point -- after five real runs

**Planning effort:** high, because the phase's whole product is
judgment: four decisions taken against evidence that did not exist
when the section was written. **Review effort:** medium.

In scope: reading what the executed audits found, settling the four
decisions this section has carried, and building the one mechanical
check those decisions justify. Out of scope: the fleet backfill,
which decision 4 makes phase 4, and this plan's own audit, which
decision 5 renumbers to phase 5.

#### What the survey found

This section was written expecting a single measurement -- queue
performance, "6 of 7 at the sweep count, step 7 in flight". Six
audit phases now exist and five have executed:

| Audit | Outcome |
|-------|---------|
| shakenfist `PLAN-queue-performance` phase 8 | One blocking defect: cluster operation coalescing had never worked since #3194 merged on 2026-05-26 -- the coalescing half of a plan named for it, inert for three months. Filed as #3878, with #3879 for the coverage gap that hid it; review of the fix then found #3884, a fold that would have merged per-node mesh operations across nodes. |
| shakenfist `PLAN-database-load-reduction` phase 8 | Three defects, one blocking. The floating IP reaper still issues one whole-table read per address (`floating_ip_reaper.py:55,70` through `IPAM.is_free()`); the functional-CI assertion that phase's own Definition of done cited as holding the fix in place cannot hold it, because its fake overrides precisely the call that costs the round trip; and the load budget then recorded the residue as expected load with a note that is false as written. |
| ryll `PLAN-idle-cpu-and-latency` phase 6 | Twenty-two findings, triaged against current `develop` with nothing dropped as "already fixed" without naming the fix. The most serious was in the audit harness: `wave1.sh`'s only *fatal* style check scanned four of six crates, leaving 46% of the workspace (28,754 of 62,024 lines) invisible to it, including the crate that had grown larger than `ryll` itself. Wave 1 also failed outright, because `test-audit-range.sh` inherited the `AUDIT_BASE`/`AUDIT_HEAD` the phase was required to export. |
| ryll `PLAN-stream-caps-and-flap` phase 18 | Wave 1 clean, and it confirmed the previous audit's harness fix: the scan set now derives from the Cargo workspace members and covers all six crates. One process finding worth more than its severity -- the plan's four-way sub-patch split silently covered 57 of 64 files, five of them the plan's own work, so the judgment agents reported on what they were given and nobody would have noticed what they were not. |
| kerbside `PLAN-proxy-dev-releases` phase 6 | No critical, high or blocking findings; five fixes, three PR review rounds, and the audit tooling repaired. `tools/audit/plan-range.sh` now derives `AUDIT_RANGE`/`AUDIT_PATHS` from a plan's merge commits, which is what makes auditing an accumulated merged range possible at all rather than a thing the runbook asked for and the scripts could not do. |
| instar `PLAN-fuzz-autofix` phase 2 | Planned, in flight in the `instar-wt-push-audit` worktree. Not counted below. |

Two things follow that this section could not have anticipated.

**The audits keep finding defects in the audit machinery.** Three of
the five found something wrong with the tooling itself, and one of
those meant the fleet's only build-failing style check had been
passing vacuously across nearly half a workspace. Nothing else in
the fleet looks at the audit harness: the consistency audits check
that `PUSH-AUDIT.md` exists and carries current blocks, and review
tracking checks coverage of source files. Whatever else the phase
is, it is the only mechanism that has ever audited the auditor.

**The chain closes.** ryll's phase 18 verified phase 6's harness
fix rather than re-finding it, and shakenfist's coalescing finding
produced a fix whose review produced a second finding. Audits are
feeding each other rather than each terminating in a list nobody
revisits.

The phase 1 deliverables, verified rather than assumed:

* `PushAudit.run()` fails a repository whose `AGENTS.md` does not
  name the runbook (`scripts/audit/checks/plans.py:558-570`). It is
  working: of the repositories carrying a `PUSH-AUDIT.md`, only
  occystrap and sfui now fail that clause, and every other
  `push-audit` failure in the compliance table is a different shared
  block. That is the gap this plan opened on -- three of eight
  `AGENTS.md` files mentioning the runbook, one saying when to run
  it -- substantially closed.
* `plan-push-audit-phase` is the ninth entry of
  `PLAN_TEMPLATE_BLOCKS` (`scripts/audit/checks/plans.py:191`), so
  `check_plan_template` requires it. It is failing shakenfist
  (#3892), divergulent (#79) and occystrap (#117).
* `development` is `compliant` for both `push-audit` and
  `plan-template` in `docs/audits/compliance.md`, generated
  2026-09-01. The Definition of done item asking that it no longer
  be `N/A` is met.

Nothing checks that a master plan carries the phase. `plan-index`
checks columns, dates, plan coverage and the status vocabulary;
`plan-phase-references` checks that phase links resolve;
`plan-source-references` checks references from code and
configuration. Decision 2 settles this.

**The backfill is partly done, and not where the plan assumed it
would be.** Measured across master plans (excluding phase files)
that carry a push-audit phase, against whether the plan file records
landing commits at all:

| Repository | Carry the phase | Record landing commits |
|------------|-----------------|------------------------|
| shakenfist | 21 | 0 |
| instar | 9 | 1 |
| ryll | 8 | 6 |
| kerbside | 2 | 1 |
| development | 9 | 8 |
| divergulent | 0 | -- |
| occystrap | 0 | -- |

What landed did so opportunistically rather than as a sweep: the
`Merged` column arrived in plans that were being edited anyway once
the block went to v2. ryll and this repository largely caught up,
instar and kerbside caught one plan each, and shakenfist's
twenty-one are untouched. development's one omission is
`PLAN-stestr-testtools.md`, which is `Blocked` with no merged
phases and so has nothing to record. Decision 4 and phase 4.

**The scope question is wider than divergulent.** occystrap also
carries a `PLAN-TEMPLATE.md` and six master plans, and is failing
`plan-template` on this very block. sfui carries three master plans
but has neither a `PLAN-TEMPLATE.md` nor a `docs/plans/index.md`.
Decision 3.

Corrected at source in the planning commit, so the next reader does
not trip over them: this section's opening premise about queue
performance being the first and only run, and the Execution table's
phase numbering, which decision 5 changes.

#### Decisions

**1. The phase stays mandatory.** Five executed runs; five that
found something; two blocking defects in production code that had
already merged and been marked complete; three findings against the
audit harness, one of which had silently disabled the fleet's only
build-failing style check across 46% of a workspace. The risk this
review point existed to catch -- a mandatory phase that finds
nothing and becomes a recurring cost that reads as diligence -- did
not materialise, and it is not close.

Making it conditional on plan size is declined, and declined
specifically on this evidence: the largest findings were in tooling
and process rather than in feature code, and every plausible size
threshold exempts precisely the small, tooling-shaped plans where
those findings lived. ryll's `idle-cpu-and-latency` was a 26-file,
~2,000-line plan and it found the 46% blind spot.

The cost is real and belongs in the record next to the benefit: each
run is roughly six sub-agents plus a management session, and
kerbside's took three PR review rounds to land. That is what bought
#3878, #3884, the floating IP reaper defect, and a coverage hole in
the one check that can fail a ryll build.

**2. Build the mechanical check.** Decision 1 is what was blocking
it: building a check to enforce a convention that might have been
withdrawn would have been the same ceremony this plan guards
against. With the convention confirmed, the thirty-six-plus plans
the phase 2 sweep edited are held in place by the sweep alone, and
nothing stops the next plan from omitting the phase.

A new criterion, `plan-audit-phase`, spec
`docs/audits/plan-audit-phase.md`, registered in
`scripts/audit/registry.py` immediately after `PlanIndex()` so the
plan family stays grouped and the results JSON ordering only ever
grows at a family boundary. It gets its own check id rather than
folding into `plan-index` because the audit's issue identity is per
check: a repository that drops the phase should get an issue about
the phase, not a second paragraph inside its index issue.

What it checks, and the constraints that shape it:

* It reads the plan files that `docs/plans/index.md` links, not the
  index's own columns. Index formats differ across the fleet by
  design -- this repository's index deliberately carries a one-line
  status and no phase column, and divergulent's `Phases` column is
  an inline `✓`/`◐` list rather than a per-phase table -- so
  anything keyed on index columns would be unimplementable in half
  the fleet.
* It applies the block's carve-out verbatim: a plan whose status is
  `Complete` and that does not carry the phase is not reopened to
  acquire one. The carve-out has to be decidable from the plan file
  and its index row alone, which is exactly what the v2 wording was
  written to allow.
* It looks for a phase naming `PUSH-AUDIT.md` as the *last* phase,
  since "it is the last row of the Execution table" is the part of
  the rule that stops the audit being scheduled in the middle and
  then outrun by later phases.
* A repository with no `docs/plans/index.md` is `N/A`, which keeps
  sfui and the repositories with no plan practice out of it without
  a special case.

**3. Widen the sweep to divergulent; exclude occystrap and sfui,
with the reasons recorded.**

* **divergulent: in.** Nine master plans, four incomplete, a
  `PLAN-TEMPLATE.md` that the `plan-template` audit is already
  failing on this exact block (divergulent#79), and an `index.md`
  with a real status column. There is no coherent position in which
  the audit demands the block in a repository's template while the
  plan sweep skips its plans. The one thing its sweep must handle
  differently: its index tracks phases as an inline `✓`/`◐` list in
  a `Phases` cell, so the phase is appended in the plan file and the
  index cell extended, not added as a table row.
* **occystrap: template block yes, plan sweep no.** The template
  block is already tracked as occystrap#117 and is the
  `plan-template` audit's job, not this plan's. The plan sweep waits:
  `docs/plans/index.md` there is a seven-line bullet list naming two
  of its six master plans, with no status recorded anywhere. A sweep
  whose first question is "which plans are incomplete" cannot answer
  it from that index, and guessing is worse than waiting. Recorded
  as a dependency rather than a decision deferred indefinitely --
  occystrap's plan sweep becomes possible when its index becomes a
  status table, which is what `plan-index` is already asking of it.
* **sfui: out.** Three master plans, no `PLAN-TEMPLATE.md`, no
  `docs/plans/index.md`. Its `push-audit` failure is that `AGENTS.md`
  does not reference `PUSH-AUDIT.md` (sfui#15), which the existing
  check already tracks. Nothing here to sweep.

**4. Do the fleet backfill, as its own phase.** The reasoning that
deferred it -- "if phase 3's decision 1 makes the phase conditional
then some of those plans stop needing a range at all" -- is
discharged by decision 1. It is now also worth more than when it was
deferred: kerbside's `tools/audit/plan-range.sh` turns a plan's
recorded merge SHAs into `AUDIT_RANGE`/`AUDIT_PATHS`, so a landing
commit is an input to a script rather than a note for a reader, and
shakenfist's twenty-one plans currently record none.

**5. Renumber: backfill is phase 4, push audit becomes phase 5.**
This is the decision most likely to be argued with. The alternative
is one long phase 3 carrying the decisions, the check and a
six-repository sweep, and the argument for it is that renumbering a
plan mid-flight is churn.

Taken anyway, for two reasons. The sweep could not be briefed until
decision 1 was settled, so the two halves are genuinely sequential
rather than merely large. And a phase whose product is judgment
should be reviewable as judgment; folding a sweep of roughly
thirty-two plans across five repositories into it makes the review
about the diff instead. The churn was measured rather than assumed:
`plan-source-references` reports no source or configuration
reference to this plan anywhere in the fleet, and grepping the
fleet for `push-audit-phase` finds only `development/AGENTS.md`,
`docs/plans/index.md`, `PLAN-plan-template-blocks.md` and
shakenfist's regenerated docs mirror -- none of which name a phase
number.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | worktree | Add the `plan-audit-phase` criterion. New `Check` subclass in `scripts/audit/checks/plans.py`, `id = 'plan-audit-phase'`, `spec = 'docs/audits/plan-audit-phase.md'`, `template = None`, `issue_title = 'Push audit phase in master plans'`. Follow `PlanIndex` and `PlanPhaseReferences` in that file for the `run(self, repo)` shape, the `self.skip`/`self.fail`/`self.ok` return convention and the message style -- a failure message names the offending plans and says what to do, not just that something is wrong. Behaviour: `skip('No docs/plans/index.md')` when the repository has no plan index. Otherwise parse the index for the master plans it links (`PLAN-*.md`, excluding `*-phase-*`) and each plan's status; for every plan whose status is not `Complete`, open the plan file and require a phase whose text names `PUSH-AUDIT.md`, appearing last among that plan's phases. A `Complete` plan that lacks the phase passes -- that is the block's carve-out and it must not be reopened; a `Complete` plan that *carries* the phase is not this check's business either, since whether the phase ran is a human judgement. Do not key on index columns: this repository's index has no phase column and divergulent's `Phases` column is an inline `✓`/`◐` list. Register the instance in `scripts/audit/registry.py` immediately after `plans.PlanIndex()` -- the list order is the results JSON order, so append at the family boundary rather than reshuffling. Tests in `scripts/tests/test_plans.py` matching the fixture style already there, covering at minimum: no index (N/A), a compliant repository, an incomplete plan missing the phase (fail, named), a `Complete` plan missing the phase (pass), a plan carrying the phase but not last (fail), and an index in each of the three shapes the fleet actually uses (a phase-count column, no phase column, an inline `Phases` list). Commit subject: "Check master plans carry a push audit phase." |
| 3b | medium | sonnet | worktree | Write `docs/audits/plan-audit-phase.md` and index it. Match the structure of the existing specifications -- `docs/audits/plan-index.md` is the closest neighbour: what is checked, why it exists, what it deliberately does not cover, and which template implements it (`templates/shared-blocks/plan-push-audit-phase.md`). The "deliberately does not cover" section carries real content and is the point of the page: the check cannot tell whether an audit was *run*, only that the phase is present; it does not reopen `Complete` plans; and it says nothing about repositories with no plan practice. Add the row to the table in `docs/audits/README.md` beside the other plan criteria. Do not hand-edit `docs/audits/compliance.md` -- it is generated and committed by the daily workflow. Commit subject: "Document the plan audit phase criterion." |
| 3c | medium | sonnet | worktree | Run the new criterion across the fleet with the existing audit entry point and record the verdicts in this plan under a *What the check found* heading, then reconcile them against the survey table above. The expected shape, from the survey: divergulent and occystrap fail (no plan carries the phase), sfui is N/A, and shakenfist, ryll, kerbside, instar and development pass. Any verdict that disagrees with that is either a bug in 3a or a gap in this survey -- say which, with the plan and line that decides it. Do not file issues by hand; the daily workflow does that. Commit subject: "Record what the plan audit phase check found." |

Steps 3a-3c share one worktree and land as one pull request; the
isolation column says `worktree` because that is where the phase is
being executed, not because the steps run concurrently. They are
sequential: 3b documents what 3a built and 3c runs it.

#### What the check found

Run 2026-09-02, one invocation per repository, against local clones
fast-forwarded to their remote tracking branch that morning. The
real entry point, not the test helper 3a and 3b used:

```
python3 scripts/audit-check.py --repo-path <path-to-clone> --repo-name <repo>
```

executed from this repository's `scripts/` directory. That prints
the full registry's results as JSON; the verdicts below are the
`plan-audit-phase` entry from each run. `tests.base.run_check` was
not needed -- the CLI targets a local clone directly.

| Repository | Verdict | Plans named |
|------------|---------|-------------|
| shakenfist | fail | `PLAN-ci-cloud-sizing.md`, `PLAN-kerbside-vdi-tokens.md`, `PLAN-queue-performance.md` |
| ryll | pass | -- |
| kerbside | pass | -- |
| instar | pass | -- |
| development | pass | -- |
| divergulent | fail | `PLAN-published-cache.md`, `PLAN-release-1.0.md`, `PLAN-patch-classification.md` |
| occystrap | fail | `PLAN-quay-label-search.md` |
| sfui | N/A | -- |

Seven of eight match the survey table exactly: divergulent and
occystrap fail, sfui is N/A, ryll, kerbside, instar and development
pass. shakenfist does not -- the survey predicted pass and the run
fails it, on three plans, none of them a parser artefact.

The divergulent row is not the one the first run produced. That run
named two plans, against four incomplete plans none of which carry
the phase, and the discrepancy was worth chasing rather than
accepting: `PLAN-release-1.0.md` files eight numbered phases under a
heading reading `## Must-do workstreams`, and the check recognised
only execution, implementation and phases sections, so it declined
to judge the plan and passed it in silence. The recognised-heading
list gained `workstream`, and more importantly the check now *names*
the plans whose phases it cannot read instead of counting them, on
the passing path as well as the failing one -- because "I could not
read this plan" and "this plan is fine" had been producing the same
verdict. `PLAN-curation-cli-ergonomics.md` is genuinely unphased and
is named as unjudged rather than failed. Measured across every
non-terminal plan in seven repositories, `PLAN-release-1.0.md` is
the only one whose state the widening changes, and no ryll or
shakenfist plan is pulled into being judged.

The lesson is recorded in `docs/audits/plan-audit-phase.md` rather
than only here: the heading list is empirical, there will be another
shape, and the durable defence is that an unreadable plan is visible
in the verdict rather than that the list is complete.

One repository outside the table is worth recording. Running the
criterion over every local clone, rather than over the audited
fleet, fails `uncalibrated-sextant` on five of five incomplete
plans. It has a `PLAN-TEMPLATE.md` and a `docs/plans/index.md` and
no `PUSH-AUDIT.md` at all, which the shared block covers -- the
phase is carried anyway and says the runbook does not exist yet.
It is not a verdict, because `uncalibrated-sextant` is not in the
matrix in `.github/workflows/consistency-audit.yml`, so no daily
run will ever check it and no issue will be filed. That is the gap
issue #40 already tracks, "Nothing checks the audit scope against
the organisation", and this is a concrete instance of it rather
than a new finding: a repository that plans the way the fleet
plans, and is invisible to the audit that would say so.

**This is a gap in the survey, not a bug in 3a.** The survey's
"backfill is partly done" table, above, counted how many master
plans *carry the phase* against how many *record landing commits*,
built by finding the phase's text in each plan. That method cannot
see a plan whose audit phase used to be last and has since been
overtaken by later phases: overtaking does not remove the phase, it
only stops it being the last one, which is exactly what this check
looks for and the survey's grep-for-presence method could not. All
three plans below are already counted among shakenfist's
twenty-one "carry the phase" plans in that table.

The three, each verified against the tree:

* **`PLAN-ci-cloud-sizing.md`** carries no push audit phase at all
  -- `grep -ci 'push.audit'` returns 0. Its index row
  (`docs/plans/index.md:113`) records "1 of 7", `In progress`; it is
  a six-phase plan whose last phase, "6. Documentation and downstream
  propagation" (`docs/plans/PLAN-ci-cloud-sizing.md:382`), is `Not
  started`. It postdates phase 2's sweep and was never touched by
  it.
* **`PLAN-queue-performance.md`** ran its audit as phase 8, which is
  `Complete` (`docs/plans/PLAN-queue-performance.md:72`). The plan
  was reopened on 2026-08-25 (line 7: "Reopened on 2026-08-25 with
  three further phases") and phases 9-11 were appended after the
  audit; phase 11, "Multi-column coalescing key", is `Not started`
  (line 75) and sits last. The audit ran and the plan grew past it
  -- outrun, not skipped.
* **`PLAN-kerbside-vdi-tokens.md`** schedules its audit as phase 10
  (`docs/plans/PLAN-kerbside-vdi-tokens.md:584`, "### Phase 10: Push
  audit"), but phase 11, "Close out the post-completion defects"
  (line 593), sits after it, and the plan's index row
  (`docs/plans/index.md:105`) still records "10 of 12", `In
  progress` -- the audit phase itself has not run yet.

**Two remedies, and the check's fix instructions
(`docs/audits/plan-audit-phase.md`) already distinguish them, with
these two as the worked examples.** Where the audit **has not run**,
as in `PLAN-kerbside-vdi-tokens.md`, the phases are simply in the
wrong order: reorder, moving phase 10 after phase 11, leaving one
audit phase. Where the audit **ran and the plan was reopened
afterwards**, as in `PLAN-queue-performance.md`, reordering would
misrepresent history -- phase 8 already audited phases 1-8's diff,
and moving it to the end would claim it audited phases 9-11 too,
which it did not. The fix there is to append a *second* audit phase
covering the reopened work, leaving two audit phases on the record
rather than one that quietly claims more coverage than it has.
`PLAN-ci-cloud-sizing.md` needs neither remedy -- it never had an
audit phase to place or duplicate, so the fix is adding one as its
seventh and last phase.

These three are shakenfist's to fix, and the daily consistency audit
files the issue once this lands, the same as any other
`plan-audit-phase` finding -- nothing here files it by hand. Nor are
they phase 4's backfill: phase 4 records landing commits on plans
that already carry a well-placed audit phase, while these three need
the phase itself moved, appended, or added before there is a
well-placed phase to record a commit against.

#### Risks and mitigations

* **The check enforces presence and is read as enforcing the
  audit.** A plan can carry the phase, never run it, and stay green.
  Mitigated by saying so in the specification's "does not cover"
  section rather than in a comment, and by decision 2 scoping the
  check to presence deliberately. The thing that catches an unrun
  audit is the plan not being markable `Complete`, which is a human
  gate and stays one.
* **`Complete` plans get reopened by a parser bug.** The carve-out
  is the difference between a check that files three issues and one
  that files two hundred. Mitigated by 3a's test list naming that
  case explicitly, and by 3c reconciling the fleet run against the
  survey table above before anything is trusted -- a run that fails
  far more repositories than the survey predicts is a parser bug,
  not a discovery.
* **Index-format variation defeats the parser.** Three shapes are
  known and all three are in 3a's test list. A fourth would show up
  in 3c as an unexpected verdict rather than as silence, because the
  survey table gives the expected answer per repository.
* **The renumbering strands a reference.** Measured in decision 5:
  no reference in the fleet names a phase number of this plan.

#### Definition of done

* `plan-audit-phase` is in `CHECKS`, has a specification page, is
  indexed in `docs/audits/README.md`, and `scripts/tests/test_plans.py`
  covers the six cases named in 3a.
* Running the criterion across the fleet produces exactly the
  verdicts the survey table predicts, or this plan says which
  repository disagreed and why.
* This section records what the five executed audits found, which
  it now does, and the four decisions are answered in writing:
  mandatory (1), checkable and checked (2), divergulent in with
  occystrap and sfui excluded for stated reasons (3), backfill
  scheduled as phase 4 (4).
* The Execution table renumbers, the `index.md` row describes what
  the review point concluded rather than what it intended to
  measure, and no reference in the fleet points at an old phase
  number.
* `pre-commit run --all-files` passes.

#### Back brief

Before 3a starts, one gate: the sub-agent restates what the
carve-out means in its own words and names the test that proves it,
because that is the single behaviour whose failure mode is two
hundred spurious issues filed against the fleet overnight rather
than a failing test. The rest of the phase is cheap to redo.

### 4. Fleet backfill

**Planning effort:** high, because the phase's scope is a
measurement and phase 3's estimate of it no longer reproduces.
**Review effort:** medium.

In scope: recording a landing commit for every merged phase of every
fleet plan that names `PUSH-AUDIT.md`, including the four that
acquire the phase during this sweep; fixing the three plans the
criterion now fails; bumping the shared block to v3 so its
carve-out names all three terminal statuses; and correcting the one
place where the check disagrees with this plan's own decision 3. Out
of scope: this plan's own audit, which is phase 5; occystrap's and
sfui's plan sweeps, which decision 3 excluded and decision 7 below
keeps excluded; and the `plan-template` block installations tracked
as shakenfist#3892, divergulent#79 and occystrap#117, which are the
`plan-template` criterion's business except where a sweep is already
editing that file.

#### What the survey found

This section was written before phase 3's check existed, and said so:
"planned when phase 3 lands, so that the briefs can quote what phase
3's check actually enforces rather than what this section guesses it
will." The guess was wrong in every one of its five numbers, and the
reason is instructive rather than clerical -- the estimate, the
check and this phase's own scope count three different things. The
first draft of this section conflated the last two and got three of
the five corrections wrong in turn; what follows is the measurement
on the basis decision 1 actually states.

Measured 2026-09-04 against each repository's committed default
branch, using the check's own `plan_index_entries` and
`plan_audit_phase_state` rather than a grep, so that the scope is the
one the criterion enforces. The `development` row was re-measured on
2026-09-05 and moved, then corrected again on 2026-09-09 once
`PLAN-scope-coverage.md`'s own closeout (PR #107) filled the cells
this section had counted as missing; the bullet below says why, and
it is the fourth time this section's arithmetic has been corrected:

| Repository | Names `PUSH-AUDIT.md` | Carries an audit phase | Section estimated | Needs a landing record | Fails the check |
|------------|----------------------|------------------------|-------------------|------------------------|-----------------|
| shakenfist | 21 | 19 | 21 | 19 | 3 |
| instar | 2 | 1 | 8 | 0 | 0 |
| ryll | 7 | 5 | 2 | 0 | 0 |
| kerbside | 2 | 2 | 1 | 0 | 0 |
| divergulent | 0 | 0 | 4 incomplete plans need the phase | 0 | 3 |
| development | 10 | 8 | not mentioned | 1 | 0 |

**Three bases, not two, and only one of them is the backfill set.**
The estimate, the criterion and this phase's own scope each count a
different thing, and the first version of this section conflated
them:

* **The looser phrase `push[-\s]audit`** is what the estimate
  counted. It matches a Future work note, a deferred-items heading
  and a record of an audit that already ran.
* **Naming `PUSH-AUDIT.md`** is what the *last phase* must satisfy
  for the criterion to pass, and it is the middle column above. It
  is a file-naming grep over the whole plan, so it also matches a
  plan that ran an audit and wrote the findings up, without that
  plan carrying a phase.
* **Carrying an audit phase** -- some phase of the plan is the push
  audit phase, whether or not it is last, or a trailing
  `## Push audit` section sits after the last phase's content -- is
  the backfill set, and it is what decision 1 states. A plan with no
  phases the check can read carries nothing and has no ranges to
  record; a plan that names the runbook only in prose has not
  scheduled an audit at all.

The criterion's own scope is wider than any of them: it judges every
plan the index links whose status is not terminal and whose phases
it can read, whether or not the plan mentions the runbook -- which
is why divergulent shows no plan carrying the phase and three
failing the check.

Measured on the backfill basis the estimate is wrong in five of its
five numbers rather than four, and two of the corrections go the
other way from the first draft of this section:

* **instar needs nothing, not eight and not one.** Its nine phrase
  matches -- the estimate said eight -- split three ways, and they
  add up: `PLAN-fuzz-autofix.md` is the single carrier and already
  has a `Merged` column; `PLAN-release-v0.2.md` names the file but
  is `Complete` and *unphased*, so it has no phases whose ranges
  could be recorded; and the remaining seven are `Complete` plans
  mentioning a push audit only in prose, which the carve-out
  exempts.

  **This measurement went stale the day after it was taken, and
  step 4e caught it.** `PLAN-differencing.md` landed on 2026-09-05
  carrying a push audit phase, making instar a two-carrier
  repository with one empty cell against a phase that had shipped.
  instar#560 filled it. Nothing was reconstructed: the pull request
  was known and the cell had simply not been typed. The lesson is
  the one decision 1 already states -- the in-scope set is derived
  from each repository's own index at sweep time, never from this
  table -- and 4e's brief said so, which is why the sweep found it
  rather than trusting the line above.
* **ryll needs nothing, not two.** Its five carriers all already
  carry a `Merged` column. The two plans the naming grep adds are
  `PLAN-web-frontend.md` and `PLAN-streaming-test-automation.md`,
  and neither belongs in the sweep --
  `PLAN-streaming-test-automation.md` is unphased, and
  `PLAN-web-frontend.md` is worth stating in full because the first
  draft of this section made it decision 1's worked example: it is
  `Complete`, its last phase is "8. Operator docs + systemd
  example", its two mentions of the runbook are headings recording
  audits that *ran* after phases 3 and 8, and its Status cells
  already carry per-phase commit SHAs. Sweeping it would add a
  column to a finished plan that already holds the information, to
  satisfy a rule its status exempts it from.
* **kerbside needs nothing, and one of its two is a format gap
  rather than a missing record.** `PLAN-proxy-dev-releases.md` is
  `Complete`, carries the phase, and records every phase's landing
  pull request in its Status cells ("Complete (merged in PR #314,
  2026-08-16)") rather than in a `Merged` column. The information
  phase 5 needs is there. Decision 8 declines to migrate the shape.
* **shakenfist is nineteen, not twenty-one and not twenty.**
  Twenty-one plans name the runbook; nineteen carry a phase, and
  *none* of the nineteen records a landing commit in any shape. The
  two the naming grep adds are `PLAN-netserv.md` (`Proposed`,
  unphased) and `PLAN-sql-pushdown-filtering.md` (`Complete`, no
  audit phase).
* **development needs two, and two drafts of this section said it
  needed a different number.** Ten of its plans name the runbook and
  eight carry a phase. Five of the eight record their ranges in a
  `Merged` column; a sixth, `PLAN-plan-template-blocks.md`, carries
  a plain `Merged:` line, at line 213 rather than 212, naming
  `2468dda`, `5918f5b`, `5b1fb74` (#49) and `ff92357` (#50), which
  between them cover all three of its implementation sections. The
  first draft reported that plan as recording nothing, because the
  detection matched `**Merged:**` and the file writes `Merged:`. A
  regex that answers "no record" for "record in a shape I did not
  anticipate" is the silent-skip failure this plan's own risks
  section warns about, found in the section written to correct the
  previous count.

  The correction to that draft then overshot in the opposite
  direction, and reported the repository as needing nothing. It
  asked which carriers have a `Merged` *record* and stopped there;
  the question decision 1 actually poses is whether each merged
  phase has a `Merged` *cell*. Two plans have a column and leave it
  empty for phases that have landed, and both are this repository's
  own:

  * `PLAN-audit-compliance-split.md` -- all four phases `Complete`,
    all four cells empty. They shipped as one pull request, merge
    commit `7843932` (#57).
  * `PLAN-scope-coverage.md` -- phases 2, 3 and 4 `Complete`, all
    three cells empty. They shipped as one pull request, merge
    commit `8b77b32` (#93). It reached `main` on 2026-09-04, after
    this section's first measurement and before its second, so
    neither measurement saw it.

  Both plans are `In progress`, so neither is carved out, and both
  already carry the column -- the backfill fills cells rather than
  adding a column, which is why step 4b absorbs it rather than
  development needing a sweep step of its own.

  **A column that exists and a range that is recorded are different
  claims**, and the scan that answered the first was read as
  answering the second. Re-run on the right basis -- every plan with
  a `Merged` column, every row whose phase status is terminal and
  whose cell is empty -- across fresh default-branch exports of
  shakenfist, ryll, instar, kerbside and divergulent as well, those
  two rows are the only ones in the fleet. kerbside's
  `PLAN-consistency-audit.md` has two empty cells, both for phases
  that have not landed. shakenfist's nineteen carriers have no
  column at all, which the table above already says, and the other
  repositories' carriers have no empty cell against a landed phase.
  So the correction is confined to development.

  The residual deviation decision 8 governs is unchanged:
  `PLAN-plan-template-blocks.md` records one aggregate line in the
  `## Push audit` section rather than a per-phase `Merged:` line in
  each numbered section, which is the shape the block asks for where
  phases are prose sections. Kerbside's Status-cell records are the
  same class of deviation and are deferred, so this one is too. That
  plan does more than record the range -- it documents two
  corrections to its own first attempt, including that a
  path-filtered `git log` conflated a commit with the pull request
  that carried it, which is decision 6's rule derived independently
  and is worth reading before reconstructing anything elsewhere.

**divergulent is three, not four.** `PLAN-published-cache.md`,
`PLAN-release-1.0.md` and `PLAN-patch-classification.md` are its
incomplete plans and none carries the phase.
`PLAN-curation-cli-ergonomics.md` has no phases the check can read
and is not judged, which is the fourth plan the estimate counted.
Its index still has the inline `✓`/`◐` `Phases` cell decision 3
described, so the sweep there appends the phase in the plan file and
extends the cell rather than adding a table row.

**development was never listed, and is not compliant yet.** Six of
its eight carriers record landing commits, one of them in a shape
decision 8 accepts; `PLAN-audit-compliance-split.md` has four landed
phases whose `Merged` cells are empty, and step 4b fills them. That
is why this repository carries a backfill of its own rather than
only the block bump, and it is the one place where this phase's own
repository is in the set it sweeps. `PLAN-scope-coverage.md` had the
same gap at measurement time, but its own closeout filled its three
empty cells in PR #107 (commits `31411ad` and `0cb5a0e`) before step
4b ran, so it needs nothing further and step 4b leaves it alone --
one plan and four cells, not two plans and seven. Two plans a phrase
grep flags are correctly untouched:
`PLAN-stestr-testtools.md` carries a `## Push audit` section but has
no phases, so there is no range to record, and
`PLAN-llm-doc-structure.md` names `PUSH-AUDIT.md` only in prose, has
no audit phase, and is `Complete`, so the carve-out exempts it --
that plan is decision 1's worked case for why naming the file is not
carrying the phase.

**Two Future work bullets in this plan are stale, and this survey is
where they were found.** `development` *does* have a
`PLAN-TEMPLATE.md` -- 23KB carrying nine shared blocks on `main`,
`plan-push-audit-phase` among them at v2 -- so the bullet saying it
has none, and that its new plans will not inherit the phase
automatically, is false and is struck below. (Nine blocks, not the
27 a `grep -c shared-block` returns: each block has a begin and an
end marker, and its prose names its canonical path as well. This
section's whole subject is the cost of counting with the wrong
pattern, so it should not do it in its own supporting figures.)
And shakenfist's `PLAN-TEMPLATE.md` carries eight blocks, all `v1`,
and *not* `plan-push-audit-phase` at all, so step 4c
installs the block there rather than refreshing it; the same is
true of occystrap. **It is not true of divergulent**, which this
section originally listed here: step 4d found the block already
embedded at v2 and refreshed it, and divergulent#109 records the
check. So instar, ryll, kerbside, client-python-k3s, divergulent
and development embed it today, all at v2, and they are the set
decision 3's bump restales.

**The check fails three plans, and one of them is this plan's own
headline evidence.** The criterion names each with the fix it needs:

* `shakenfist/PLAN-ci-cloud-sizing.md` -- no push audit phase; its
  last phase is "6. Documentation and downstream propagation".
* `shakenfist/PLAN-kerbside-vdi-tokens.md` -- the audit phase is not
  last, so phase 11 ("11. Close out the post-completion defects
  (#4003, #4009)") is unaudited.
* `shakenfist/PLAN-queue-performance.md` -- phase 8 is the audit
  phase and is `Complete`, but phases up to 11 ("11. Multi-column
  coalescing key") come after it.

The third is worth pausing on. `PLAN-queue-performance` phase 8 is
the audit that found the coalescing defect (#3878), which is the
first row of phase 3's evidence table and a load-bearing part of
decision 1. That plan has since grown three phases past its own
audit, so the mechanism whose value it demonstrated has been outrun
in the very plan that demonstrated it. It is the clearest available
argument that the criterion earns its place, and it is exactly the
case the criterion was built to catch.

**A statusless index entry is read as an incomplete plan, which
contradicts decision 3.** `plan_index_entries` returns `None`
wherever the index records no status for a link -- a table row with
no status cell, but equally a link in prose above the table or in a
bullet list in a repository whose index is not a table yet
(`scripts/audit/checks/plans.py:388-421`). `plan_status_is_terminal`
is false for `None`, so the plan is judged. occystrap is the
bullet-list case rather than the missing-column one, which is why
decision 2's wording below is about the index not recording a
status rather than about a row. Two consequences, both
measured:

* occystrap fails the criterion on `PLAN-quay-label-search.md`.
  Decision 3 excluded occystrap's plan sweep with a stated reason:
  its index is "a seven-line bullet list naming two of its six
  master plans, with no status recorded anywhere. A sweep whose
  first question is 'which plans are incomplete' cannot answer it
  from that index, and guessing is worse than waiting." The index
  is unchanged, and the check answers that question by guessing --
  the specific guess decision 3 declined to make. Its index links
  exactly two plans, `PLAN-info-check.md` and
  `PLAN-quay-label-search.md`, so after 4a both are named as
  statusless rather than one being failed and the other reported
  unphased. Decision 7 records why that exemption has no re-entry
  condition.
* ryll's `## Standalone plans` table has columns `Date | Plan |
  Intent` and no status by design -- "plans that track issues,
  follow-ups, or deferred work without phased execution". Its ten
  entries are all read as incomplete master plans. Every one of
  them is currently `unphased`, so nothing is judged and ryll
  passes; it passes by luck rather than by design, and the first
  standalone plan to grow a numbered phase table would be failed
  for lacking a phase it was never meant to carry.

Nothing else in the survey disagreed with the section. The shared
block is at v2 and its carve-out does name `Complete` alone, as the
section says; `PLAN_TERMINAL_STATUSES` in
`scripts/audit/checks/plans.py:205` does list all three; kerbside's
`tools/audit/plan-range.sh` exists and this repository has no
`tools/audit/` directory -- it does have `tools/`, including
`tools/audit-snapshot.sh`, which is what the fleet before/after
verdict diffs below are produced with.

**Corrected at source, so the next reader does not re-derive it:**
the Execution table now records phase 3 as `Complete` with its merge
commit; the plan-level Definition of done bullet that carried the
21/8/2/1/4 estimate now carries the measured figures, names the
basis, and no longer claims this repository is compliant; and the
Future work bullet asserting `development` has no `PLAN-TEMPLATE.md`
is struck, because it does. This section is where the arithmetic
lives; a later step should not redo it.

#### Decisions

**1. The backfill basis is "carries an audit phase", at any status.**
A plan is in the backfill set when some phase of it is the push
audit phase -- whether or not it is last -- or a trailing
`## Push audit` section sits after its last phase's content. The
shared block says a plan that has the phase runs it "even if it
reaches `Complete` before the phase does", so a `Complete` plan
carrying the phase still needs a range; the carve-out is only about
plans that do not carry it.

Two things that look like carrying it are not, and both were got
wrong in the first draft of this section:

* **Naming `PUSH-AUDIT.md` in prose is not carrying the phase.**
  `PLAN-llm-doc-structure.md` in this repository is the worked case:
  it names the runbook once, is `Complete`, and
  `plan_audit_phase_state` returns `('problem', 'no push audit
  phase; phase 6 is "Regenerate and document"')`. It has no audit
  phase, so the carve-out exempts it and it is out of scope. The
  same reading takes ryll's `PLAN-web-frontend.md` out -- its two
  mentions are records of audits that ran, not a phase -- which the
  first draft used as the example putting a plan *in*.
* **Having no phases the check can read is not carrying it either.**
  `PLAN-stestr-testtools.md` and instar's `PLAN-release-v0.2.md`
  both name the runbook and are unphased; there are no phases whose
  ranges could be recorded.

This test is not one call to an existing helper --
`plan_audit_phase_state` returns `problem` both for a plan whose
audit phase is outrun and for a plan that has no audit phase at all,
so `ok`-or-`problem` is *not* the test and would sweep
`PLAN-llm-doc-structure.md` in. A sweep determines it by reading the
plan's phases, and the post-condition is mechanical: after 4c-4e
every in-scope plan returns `ok`.

**2. A missing status makes a plan unjudgeable, not incomplete.**
This is the decision most likely to be argued with, so the argument
against it first: occystrap's failure is real pressure toward a
status column, and softening the check removes a lever. The reason
it loses is that the lever points at the wrong criterion.
`plan-index` fails occystrap today with a message about its index;
`plan-audit-phase` fails it with a message about a plan's phases,
which tells occystrap to add a push
audit phase to a plan nobody has said is still open. If that plan is
finished, the block's carve-out says explicitly not to reopen it --
so the check may be demanding the one thing the block forbids, and
it cannot tell which. An unjudgeable plan is named in the verdict,
the way `unphased` and unresolved plans already are, so this
converts a possibly-wrong failure into a visible silence rather than
into nothing. It also retires ryll's latent trap without ryll
changing anything.

The silence is wider than a table row with an empty status cell: a
plan linked from prose, or from a bullet list in an index that is
not a table yet, records no status either and stops being judged
too. That is the occystrap case rather than an edge of it, so the
verdict wording says the index records no status rather than
naming a row, and the fleet-wide before/after diff 4a requires is
read with this in mind rather than as an unexplained regression.

**3. The shared block goes to v3.** Its carve-out names `Complete`
where `plan-status-vocabulary` gives three terminal statuses, and
the check has implemented all three since phase 3. The block's
silence is a gap rather than a decision, as this section already
recorded. The bump restales every embedded copy fleet-wide, which is
why it belongs here: the sweep is visiting those repositories
anyway.

**4. The three failing plans are fixed by the sweep, and
`PLAN-queue-performance` gains a phase rather than moving one.** Its
phase 8 audit ran and found real defects; moving that section to the
end would misrepresent a completed audit as covering phases 9-11,
which it did not read. The check's own message says the same. A new
final phase is appended, and it cites phase 8's audit as prior
coverage of the range it already read.

**5. One sub-agent per repository, as phase 2 did.** Each sweep
lands as its own pull request in its own repository. Cross-repo
work is the shape this plan has used since phase 2 and the shape
the block prescribes for a phase that lands elsewhere.

**6. Reconstructed ranges follow the block's own instruction.**
`gh pr list --state merged` and `git rev-list --first-parent`, never
a path-filtered `git log` on its own. A range that cannot be
recovered is recorded as unrecoverable, naming the paths the audit
should read instead, rather than left blank.

**7. occystrap and sfui stay out, and nothing will tell us when
that stops being right.** Decision 3's reasons are intact and
decision 2 removes the accidental inclusion rather than converting
it into a sweep. But the re-entry condition an earlier draft stated
-- "occystrap re-enters scope when its index becomes a status
table" -- is not enforced by anything. `plan-index` requires a
table, not a status column: `docs/audits/plan-index.md` says "A
`Status` column is optional -- a standalone plan listing that tracks
no status is registered, just not tracked." So occystrap can satisfy
`plan-index` with a `Date | Plan | Intent` table and never re-enter
`plan-audit-phase` scope, and after 4a *any* repository can opt out
of this criterion by omitting a status column.

The opt-out is finer-grained than that, and worth stating at its
real size: `plan_index_entries` returns `status=None` for any
individual link that records no status, so blanking one cell in an
otherwise-compliant status table -- shakenfist's, this
repository's -- silently removes that one plan from judgement while
every other row keeps working. There is a narrower rule that would
close it: bucket a plan as statusless only where the table it is
linked from carries no status column at all, which still covers
occystrap's bullet list and ryll's `## Standalone plans` while
leaving a blanked cell a failure. It is not taken here because
`plan_index_entries` returns `(filename, target, status)` and would
have to carry which table each link came from, which is a change to
the shape every caller reads for a hole nobody has fallen into
yet. Recorded so the decision is available rather than
rediscovered.

That is a real cost of decision 2 and it is not closed here. It is
recorded in Future work, because the thing that would close it is a
criterion that reads the `Merged` column -- the same missing
criterion recorded there already -- rather than a condition this
phase can assert. What phase 4 does instead is make the silence
visible: every statusless plan is named in the verdict, so a
repository opting out says so on the compliance page every morning
rather than quietly passing.

**8. A landing record in the Status cell counts; migrating it to a
`Merged` column does not belong in this phase.** kerbside's
`PLAN-proxy-dev-releases.md` records every phase's landing pull
request as Status-cell prose, and ryll's `PLAN-web-frontend.md`
records per-phase SHAs the same way. The information phase 5 needs
is present; only the shape differs. Migrating those to a column is
cheap, but it is a format decision that belongs with the criterion
that would read the column, and making it here would mean editing
`Complete` plans to satisfy a rule no check states. Recorded in
Future work with the criterion.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | opus | worktree | In `scripts/audit/checks/plans.py`, make `PlanAuditPhase.run` treat an index entry whose status cell is absent as unjudgeable rather than incomplete. `plan_index_entries` yields `status=None` for such a row; today that falls through `plan_status_is_terminal` (false) into the judged set. Add a third bucket beside `unphased` and `unresolved`, named in the verdict through `plan_index_summarise` in the same style, because a plan silently walked past is indistinguishable from one that passed, which is the rule the rest of this check already follows. Word it provenance-neutrally -- "N plan(s) the index links without recording a status, not judged: ..." -- because `plan_index_entries` returns `None` for a prose or bullet-list link as well as for a row with no status cell, and occystrap, the repository this change is for, is the bullet-list case. The early return at `scripts/audit/checks/plans.py:1295` (`if not judged and not terminal and not unphased:` -> `skip('docs/plans/index.md links no master plans')`) must account for the new bucket as well: a repository whose only linked plans are statusless links two plans and must report pass-with-them-named, not skip as N/A claiming it links none. That is exactly occystrap once this lands, so getting it wrong silently inverts the step's own verification. Do not change `plan_status_is_terminal`; a missing status is not a terminal status, and conflating them would exempt the plan instead of declining to judge it. Tests in `scripts/tests/test_plans.py` in the existing fixture style: an index row with no status cell whose plan lacks the phase is not failed but is named; the same row with a status is failed as before; an index every one of whose entries is statusless, which must pass with those plans named rather than skip as N/A; a bullet-list index with no table at all, which is occystrap's shape; a statusless entry whose target resolves to no file, asserted to be reported as *unresolved* rather than statusless, which is the order the code already has (`path is None` is tested first, at `scripts/audit/checks/plans.py:1272`) and which a careless insertion would invert; a statusless entry whose plan is also unphased, with the intended bucket named here rather than left to fall out of where the check happens to sit -- it belongs in the statusless bucket, because not knowing whether a plan is open is the stronger reason not to judge it; and a two-table index where one table has a status column and the other does not, which is ryll's actual shape (`## Master plans` with `Date \| Plan \| Intent \| Status \| Phases`, `## Standalone plans` with `Date \| Plan \| Intent`). Update the criterion's specification in the same commit, which AGENTS.md requires of any change to a `Check`: `docs/audits/plan-audit-phase.md` carries a "What this deliberately does not cover" list of six bullets, and this step appends a seventh -- plans the index links without recording a status -- with decision 2's reasoning, namely that the check would otherwise demand a push audit phase for a plan nobody has said is open, which the block's carve-out may forbid. Say there that `plan-index` is the criterion that speaks to the index's shape, and that it requires a table rather than a status column, so this exclusion is an opt-out nothing detects. Produce the before/after fleet comparison with the existing helper rather than a hand-rolled loop: `tools/audit-snapshot.sh <clones-dir> <out-dir>` for each side and `tools/audit-snapshot.sh --diff <old> <new>`. Enumerate the expected moves in advance rather than discovering them, because `audit_snapshot.py` counts a details-only change as a firm difference (`scripts/audit_snapshot.py:102-108`; `plan-audit-phase` is not in `NETWORK_CHECKS`, so it is not advisory). Expect exactly two: occystrap moves `fail` to `pass` with both of the plans its index links named as statusless, and **ryll stays `pass` but its details string changes**, because its ten `## Standalone plans` entries move from the unphased bucket to the new one -- decision 2 calls that out as a benefit, so it is the change succeeding rather than a regression to investigate. Capture the pair across **4a's commit alone**, before 4b's bump, because 4a and 4b share a pull request: measured across the merged pull request instead, 4b's v3 bump additionally flips `plan-template` to non-compliant for instar, ryll, kerbside and client-python-k3s until 4e refreshes them, and this assertion would read as failed. Scoped to 4a's commit: no repository's pass/fail status other than occystrap's may change, and no repository other than ryll and occystrap may differ at all.  All four files these two steps edit carry human review marks in `REVIEWS.md` today -- `scripts/audit/checks/plans.py` (line 97), `scripts/tests/test_plans.py` (126), `docs/audits/plan-audit-phase.md` (58) and `templates/shared-blocks/plan-push-audit-phase.md` (164) -- so editing them stales those marks and `pre-commit run --all-files` fails until `python3 scripts/review-tracking.py prune` has run. Run it, commit the regenerated `REVIEWS.md` alongside the change, and say in the pull request body which marks were dropped. **Do not re-stamp them**: the mark attests that a person read that exact content, so a pruned file needs a human to read it again, and there is no version of this a sub-agent can finish alone. Commit subject: "Do not judge plans whose index records no status." |
| 4b | medium | sonnet | worktree | Bump `templates/shared-blocks/plan-push-audit-phase.md` to v3. The block's first bullet says `Complete` twice in one sentence and only the first is the carve-out, so quote the change precisely: "a plan that is already `Complete` and does not carry the phase is not reopened to acquire one" becomes "a plan that is already `Complete`, `Abandoned` or `Superseded` and does not carry the phase is not reopened to acquire one", and the trailing clause "and a plan that has the phase runs it even if it reaches `Complete` before the phase does" is left verbatim -- it is about a plan finishing before its own audit runs, and decision 1 of this phase leans on it directly. Keep every other line byte-identical: this is a wording gap, not a rule change, and the check has behaved this way since phase 3. Update the version marker in the block's own opening comment and in this repository's embedded copy in `PLAN-TEMPLATE.md`. `templates/shared-blocks/README.md` describes the versioning process but carries no per-block version list, so there is nothing to change there -- do not spend a search on it. Then refresh this repository's own embedded copies so `plan-template` still passes here. Update `docs/audits/plan-audit-phase.md` where it quotes the carve-out, and the comment above `PLAN_TERMINAL_STATUSES` at `scripts/audit/checks/plans.py:196-204`, which was written to point at this step. Only its **last two** sentences become false -- the one beginning "The plan-push-audit-phase block still words the carve-out as `Complete` alone" and the closing "the block catches up there". Keep the first two verbatim: they say why all three terminal terms carve out and the four live ones bind, which is the non-obvious part and is not something this step changes. Replace the two that go stale with a note that the block names all three statuses from v3 onwards. Do not touch other repositories in this step -- the restale is deliberate and each sweep step below refreshes its own copy. In the same pull request, correct one stale claim in `docs/plans/PLAN-plan-template-blocks.md`, which is the file this repository's own compliance story runs through and which a draft of this section misread. **Do not reconstruct anything there**: it already records its range, as a plain `Merged:` line at line 212 naming `2468dda`, `5918f5b`, `5b1fb74` (#49) and `ff92357` (#50), followed by two documented corrections to its own first attempt that are worth preserving verbatim. What is stale is its Migration section at lines 147-155, which says the blocks landed in "instar, kerbside, ryll and shakenfist" and are "outstanding for client-python-k3s, divergulent and occystrap". `docs/audits/compliance.md` disagrees on two of those: client-python-k3s is compliant, and shakenfist is **non**-compliant on `plan-template` for missing this very block (shakenfist#3892) -- which is what step 4c relies on when it installs rather than refreshes. Correct the two lists against the compliance page and leave the rest of the section alone. Then do this repository's own backfill, which is two plans and seven cells: `PLAN-audit-compliance-split.md` has all four phases `Complete` with all four `Merged` cells empty, and they landed as one pull request, merge commit `7843932` (#57); `PLAN-scope-coverage.md` has phases 2, 3 and 4 `Complete` with empty cells, landed as `8b77b32` (#93). Both already carry the column, so this fills cells rather than adding one, which is why it rides here instead of development needing a sweep step. Assert both SHAs are merge commits (`git rev-list --merges -1 <sha>` returns them) in the pull request body, as 4c and 4e do. Leave `PLAN-scope-coverage.md`'s phase 1 cell alone -- it reads "n/a -- GitHub settings, no commit", which is decision 6's unrecoverable-range shape already applied. All four files these two steps edit carry human review marks in `REVIEWS.md` today -- `scripts/audit/checks/plans.py` (line 97), `scripts/tests/test_plans.py` (126), `docs/audits/plan-audit-phase.md` (58) and `templates/shared-blocks/plan-push-audit-phase.md` (164) -- so editing them stales those marks and `pre-commit run --all-files` fails until `python3 scripts/review-tracking.py prune` has run. Run it, commit the regenerated `REVIEWS.md` alongside the change, and say in the pull request body which marks were dropped. **Do not re-stamp them**: the mark attests that a person read that exact content, so a pruned file needs a human to read it again, and there is no version of this a sub-agent can finish alone. Commit subjects: one for the block bump, one for the correction, one for the backfill. |
| 4c | high | opus | worktree | Sweep shakenfist: the largest and the only one with check failures. Nineteen of its plans carry an audit phase and *none* of the nineteen records a landing commit in any shape; the twenty-one that name `PUSH-AUDIT.md` include two that carry no phase (`PLAN-netserv.md`, `Proposed` and unphased, and `PLAN-sql-pushdown-filtering.md`, `Complete` with no audit phase) and are out of scope by decision 1. For each of the nineteen, add a `Merged` column as the last column of the Execution table (last so a row omitting it still reaches `Status`, per the shared block) and fill it by reconstruction -- `gh pr list --state merged` plus `git rev-list --first-parent`, never a path-filtered `git log` alone, and say in each plan that the range was reconstructed. Where a phase's range is unrecoverable, say so and name the paths, rather than leaving the cell blank. Then fix the three failures the criterion names, with the fix it names: `PLAN-ci-cloud-sizing.md` gains a final push audit phase. It is measurably *outside* the nineteen today -- it does not name `PUSH-AUDIT.md` anywhere and carries no audit phase -- so appending the phase makes it the twentieth carrier, and its already-merged phases need ranges reconstructed as well. Twenty plans carry a `Merged` record when this step is done, not nineteen and not twenty-one; `PLAN-kerbside-vdi-tokens.md` has its audit phase moved after phase 11; `PLAN-queue-performance.md` gains a *new* final phase citing phase 8's completed audit as prior coverage of phases 1-8, and does not move phase 8 (decision 4). shakenfist's `PLAN-TEMPLATE.md` carries eight blocks, all at v1, and does not carry `plan-push-audit-phase` at all -- so *install* the v3 block there rather than refreshing it, which is also what `plan-template` is failing shakenfist for. List every reconstructed SHA in the pull request body and assert each is a merge commit (`git rev-list --merges -1 <sha>` returns it) or an explicit `first..last` range, since no criterion reads the `Merged` column and review is the only thing that will. shakenfist's pre-commit carries a "plan statuses and index arithmetic agree" hook -- run it, and reconcile any index phase counts the new phases change. One pull request. Commit subjects per plan group, not one commit per plan. **Corrected by 4f after the step ran: it is twenty-one, and the sentence above rules out the right answer for the wrong reason.** The nineteen were right when counted, but `PLAN-transient-capacity-refusals.md` acquired an audit phase between the survey and the sweep -- 4c gave it one on the branch, as it did `PLAN-ci-cloud-sizing.md` -- so twenty-one plans carry a `Merged` record and every one of them names `PUSH-AUDIT.md`. Both exclusions the brief names held: `PLAN-netserv.md` and `PLAN-sql-pushdown-filtering.md` are still correctly outside the set. Each of 4c, 4d and 4e found a count in its own brief that had not survived to its sweep, which is why decision 1 makes every sweep re-derive its set from the repository's own index rather than read it here. |
| 4d | high | opus | worktree | Sweep divergulent: three incomplete plans (`PLAN-published-cache.md`, `PLAN-release-1.0.md`, `PLAN-patch-classification.md`) that carry no push audit phase at all. Append the phase to each and extend its `index.md` row -- its index tracks phases as an inline `✓`/`◐` list in a `Phases` cell, so the phase is appended in the plan file and the cell extended, not added as a table row (decision 3 of phase 3). divergulent has no `PUSH-AUDIT.md`; per the shared block the phase is still carried, and it says the runbook does not exist yet and what was done instead. **That is false and step 4d did not follow it**: divergulent has a 638-line `PUSH-AUDIT.md` at its root, landed in divergulent#60 (`7b45c11`), and its `PLAN-TEMPLATE.md` already carries an "In this project" note saying so. Following this sentence would have written a falsehood into a plan, so the appended phase cites and runs the real runbook. Corrected here in 4f rather than silently, because the same sentence would otherwise be copied into the next repository a sweep decides has no runbook. All three then name `PUSH-AUDIT.md`, so they join the backfill set: reconstruct a landing commit for each of their already-merged phases by the same rules as 4c, or say per phase that the range is unrecoverable and name the paths. `PLAN-curation-cli-ergonomics.md` has no phases the check can read -- leave it, and say in the pull request that it was left and why. Refresh the `plan-push-audit-phase` block to v3 in its `PLAN-TEMPLATE.md`. An earlier draft of this step called that an install, on the strength of `PLAN-plan-template-blocks.md`'s Migration section listing divergulent as outstanding; that entry was stale and is corrected in step 4b's pull request. `docs/audits/compliance.md` has divergulent `compliant` on `plan-template`, and `plan-push-audit-phase` is a required member of `PLAN_TEMPLATE_BLOCKS`, so the block is already there at v2 and this step bumps the version marker and the carve-out sentence. Re-check the compliance page before starting rather than trusting either statement. divergulent#79 is therefore not this step's to close: it was the audit's missing-block issue and it is already closed, which is how the block got there. One pull request. **Corrected by 4f after the step ran: two of the three plans named above had moved**, which 4d re-derived rather than assumed. `PLAN-published-cache.md` closed out in divergulent#88 (`5288f4a`) and is `Complete` without the phase, so the carve-out leaves it alone; `PLAN-patch-classification.md` closed out in divergulent#102 (`9f303be`), whose closeout added both the phase and a full `Merged` column, leaving one empty cell to fill. Only `PLAN-release-1.0.md` needed the phase appended and its ranges reconstructed. |
| 4e | low | sonnet | worktree | Refresh the v3 block in ryll, instar, kerbside and client-python-k3s, one pull request each. **None of these needs a backfill**, which is a correction to this section's first draft rather than a claim to take on trust -- verify it before concluding the step, by the test in decision 1 rather than by grepping for the runbook. ryll's five carriers all already have a `Merged` column; the two extra plans a naming grep flags (`PLAN-web-frontend.md`, `PLAN-streaming-test-automation.md`) carry no audit phase. instar's single carrier has a column; its `PLAN-release-v0.2.md` is `Complete` and *unphased*, and its other eight push-audit mentions are prose in `Complete` plans that must not be reopened (decision 1). kerbside's two carriers are covered, one by a column and one by Status-cell pull request numbers that decision 8 accepts as recorded. Leave ryll's ten `## Standalone plans` entries alone -- they are deliberately statusless and 4a makes them unjudgeable. If any repository turns out to need a backfill after all, do it here by 4c's rules and say in the pull request that this section was wrong. **Corrected by 4f after the step ran: two of the four did need one**, which is what verifying rather than trusting found. instar gained a second carrier, `PLAN-differencing.md`, the day after this section was measured, with one empty cell (instar#560); and client-python-k3s, which this section does not describe at all, had a stale phase 1 row in `library-api-and-collection.md` whose work had merged as `7fb29e5` (#55), filled by client-python-k3s#57. Neither was a reconstruction -- both pull requests were known and the cells had simply not been typed. Commit subject: "Refresh the push audit block at v3." |
| 4f | medium | sonnet | worktree | Opens its own pull request in this repository, after the last of 4c-4e has merged. Re-run the criterion across the fleet over fresh default-branch checkouts -- capture a fresh baseline of its own with `tools/audit-snapshot.sh <clones-dir> <out-dir>` before touching anything, then the same again after, then `tools/audit-snapshot.sh --diff <before> <after>`. Do not try to reuse 4a's snapshot: those are deliberately uncommitted, live in a scratch directory the worktree-isolated 4a discards, and predate 4c-4e, so a diff against them would conflate the check change with five sweeps. The expected verdicts below are absolute and do not need a diff at all; the diff is there to catch a repository nobody expected to move. Record the verdicts in this section under a *What the sweep found* heading. Expected after 4a-4e: shakenfist, ryll, instar, kerbside, divergulent and development all pass; occystrap passes with *both* the plans its index links named as unjudged -- `PLAN-info-check.md` and `PLAN-quay-label-search.md`, which is its whole bullet list, not just the one failing today; sfui stays N/A. Any verdict that disagrees is a bug in an earlier step or a gap in this survey -- say which, with the plan and line that decides it. The snapshot diff covers the whole fleet, not just the repositories expected to move; read it that way. Do not file issues by hand; the daily workflow does that. Commit subject: "Record what the fleet backfill found." |

Three of the steps run in this repository, across two pull
requests. 4a and 4b land together in the first: 4a because the
sweeps should be measured against the scope the criterion will
actually have, and 4b because a sweep that installs v2 has to be
visited again after the bump. 4c, 4d and 4e then each land as their
own pull request in their own repository. 4f lands last, in the
second pull request here, once the last sweep has merged -- its
whole job is to record what the fleet says afterwards, so it cannot
ride with 4b ahead of the sweeps without inventing the verdicts it
reports.

#### What the sweep found

Re-run on 2026-09-14 over fresh default-branch clones of all
twenty-one repositories the daily matrix audits, with `skillsaw`
pinned at 0.18.0 as that workflow pins it. The clones are throwaway
and shallow; nothing here was measured against a working tree.

| Repository | Expected | Verdict | What the criterion said |
|------------|----------|---------|-------------------------|
| shakenfist | pass | pass | 18 incomplete plans end with a `PUSH-AUDIT.md` phase; 19 terminal-status not judged; `PLAN-netserv.md` has no readable phases |
| ryll | pass | pass | 4 incomplete; 27 terminal-status; `PLAN-streaming-test-automation.md` unphased; 10 statusless, named |
| kerbside | pass | pass | 1 incomplete; 8 terminal-status; `PLAN-use-case-docs.md` unphased |
| divergulent | pass | pass | 1 incomplete; 7 terminal-status; `PLAN-curation-cli-ergonomics.md` unphased |
| development | pass | pass | 8 incomplete; 4 terminal-status; `PLAN-stestr-testtools.md` unphased |
| occystrap | pass, two plans named statusless | pass, seven named terminal-status | 7 terminal-status plan(s) not judged |
| sfui | N/A | N/A | No `docs/plans/index.md` |
| instar | pass | **fail** | `PLAN-differencing.md` ends with a push audit phase that never names `PUSH-AUDIT.md` |

Ten of the other thirteen repositories are `not_applicable`, on the
three grounds the criterion already reports: no
`docs/plans/index.md` (actions, agent-python, clingwrap, cloudgood,
hunkydory, kerbside-client, visual-digest-rust), an index linking no
master plans (client-python-k3s, kerbside-patches), or a
`REPO_OVERRIDES` scope that excludes this check (private-ci).
`client-python` and `library-utilities` pass with one
terminal-status plan each. The thirteenth, `uncalibrated-sextant`,
fails; it is discussed below.

Six of the eight expectations hold exactly. Two do not, and a
ninth repository fails that no expectation covered. None of the
three is a bug in 4a-4e.

**occystrap passes, but no longer for decision 2's reason.** The
expectation was that its two linked plans would be named as
statusless -- the whole observable outcome of 4a. Instead all seven
of its plans are named as terminal-status. occystrap rewrote its
index into a status table on 2026-09-10 (`7600cc6`, "Register
every plan in the plan index."), registering every plan it had and
marking all seven `Complete`. The bullet list decision 2 was written
for is gone, so the statusless bucket is no longer exercised there
at all. It is still exercised, by ryll's ten `## Standalone plans`
entries, which the survey also predicted and which the table above
confirms. Decision 2 is therefore still load-bearing, but its
worked example has moved repositories. It changes no verdict
anywhere in the fleet today: occystrap would pass without 4a now
that all seven of its plans are terminal-status, and ryll passed
before it, so the statusless bucket moves a details string and
nothing else. 4a is preventative, which is the shape the Future
work bullet about the statusless opt-out already describes.

**instar fails, on a plan that did not exist when the survey ran.**
`PLAN-differencing.md` landed on 2026-09-05, the day after this
section's measurement, and carries a push audit phase at phase 17
that cites `PLAN-TEMPLATE.md` rather than naming `PUSH-AUDIT.md`,
which is what the criterion reads. This is a gap in the survey, not
a defect in 4e: step 4e's brief asked instar to refresh the block
and verify that no backfill was needed, and instar#560 did both --
finding, and recording, a landing the survey had missed. Nothing
asked it to add the runbook's name to a second carrier, and the
daily workflow had already filed the failure as
[instar#554](https://github.com/shakenfist/instar/issues/554) on
2026-09-06, five days before 4e merged. The fix is one sentence in
instar's phase 17 and it belongs to instar.

**uncalibrated-sextant fails, and was never in this phase's scope.**
Five of its five incomplete plans have no push audit phase at all,
and it has no `PUSH-AUDIT.md` either. Both were filed by the daily
workflow on 2026-09-04, as
[uncalibrated-sextant#10](https://github.com/shakenfist/uncalibrated-sextant/issues/10)
and
[#11](https://github.com/shakenfist/uncalibrated-sextant/issues/11),
before this phase's survey was written. The survey enumerated the
repositories it swept and this was not among them: the phase 2 and
phase 3 tables reach eight repositories, and the fleet is
twenty-one. That is a real hole in the survey rather than a
regression, and it is recorded in Future work rather than fixed
here, because appending a phase to five plans in a repository this
phase never examined is a sweep, not a closeout.

**The mechanical post-condition holds, with one row that has gone
stale since the sweeps.** No criterion reads the `Merged` column, so
the claim that every landed phase records a range is checked by
reading the tables directly. Reading only the tables that carry both
a `Status` and a `Merged` column answers half of it, and the half it
misses is the one the sweeps exist to fix: a plan whose Execution
table never gained the column is skipped rather than named. So the
scan has two buckets, over every master plan in every clone:

```python
TERMINAL = {'complete', 'abandoned', 'superseded'}
# A. rows of a table carrying both columns:
if status in TERMINAL and not merged:
    print(repo, plan, lineno, phase)
# B. plans carrying a push audit phase -- decision 1's in-scope
#    test, taken from the criterion's own plan_phases() rather
#    than re-implemented -- with no Status-and-Merged table:
if carries_audit_phase(content) and not has_merged_column(plan):
    print(repo, plan)
```

Bucket A names exactly one row: instar's `PLAN-differencing.md`
phase 4, which merged as
[instar#563](https://github.com/shakenfist/instar/pull/563)
(`f981374`) on 2026-09-14, three days after 4e. instar's own plan
says the column "is filled in as each phase lands, not reconstructed
afterwards", so this is a one-day-old piece of that repository's
housekeeping rather than a backfill this phase missed. It is in the
same plan file as the failure above, so one instar change could
close both -- an expectation rather than a fact, since instar#554 is
filed against the phase wording alone and no criterion reads the
column to notice the cell.

Bucket B names six of the forty-four in-scope plans, and each was
read rather than counted: every one records its landings in a shape
decision 8 already accepts, not in no shape at all. This
repository's `PLAN-code-review-tracking.md` and
`PLAN-consistency-audits-v2.md` carry a `Phase`/`Merged` table with
no status column beside it, which is why a scan keyed on both
columns cannot see them; divergulent's `PLAN-release-1.0.md`
records a `**Merged:**` line per phase section, and
`PLAN-plan-template-blocks.md` the single aggregate one decision 8
accepts; kerbside's `PLAN-proxy-dev-releases.md` carries
its landings as Status-cell prose, which is the case the first
Future work bullet leaves open; and shakenfist's
`PLAN-agent-operation-dependencies.md` has the column with every
cell an em-dash because none of its phases has landed yet. With
both buckets run, every landed phase in the fleet records a range.

**The snapshot diff moved nothing.** `tools/audit-snapshot.sh` over
the twenty-one clones before and after this step's edits reports no
firm differences, which is the expected result: 4f edits one plan
file in this repository and changes no check. The before/after pair
is what catches a repository nobody expected to move, and none did.

**The false claims are corrected at source, not only here**, so a
later reader does not trip over them. Each edit says that it is a
correction and what it replaces:

* the backfill counts in the 4c, 4d and 4e briefs, all three of
  which had moved between the survey and the sweep;
* the 4d brief's instruction to write that divergulent has no
  `PUSH-AUDIT.md`, which it does -- following it would have put a
  falsehood into another repository's plan;
* the claim that divergulent needed the block installed, when it
  was already embedded at v2 and 4d refreshed it;
* the survey's "instar needs nothing" bullet, and the Future work
  entry repeating the same per-repository figures;
* the Definition of done's occystrap and instar clauses, restated
  to what was measured;
* the master Definition of done's claim that seven `Merged` cells
  in this repository stand empty against landed phases, which step
  4b and `PLAN-scope-coverage.md`'s own push audit had already
  filled between them.

The last of those was found by the review of 4f's own pull request
rather than by the sweep, along with the second bucket of the
post-condition scan above. Nothing further needs re-editing in a
later step.

#### Risks and mitigations

* **Reconstruction records the wrong commit.** A path-filtered `git
  log` lists commits that touched a path without saying which
  arrived inside a pull request, so recording one that came in under
  a merge audits a single commit instead of the whole change. The
  block already forbids it; decision 6 repeats it, and every brief
  that reconstructs a range names the two commands that are allowed.
  Checked by spot-reading three reconstructed ranges per repository
  in review and confirming each recorded SHA is a merge commit or an
  explicit `first..last` range.
* **Twenty backfills in one pull request is a reviewer's worst
  case.** shakenfist's sweep is mostly mechanical and entirely in
  markdown, and a reviewer cannot check twenty reconstructed ranges
  by reading, and re-running the criterion does not help: no check
  reads the `Merged` column at all -- `plan-audit-phase` measures
  only that the last phase names the runbook, and nothing in
  `scripts/audit/` looks at the column. So the mitigation is the
  one named in the risk above, made mechanical: 4c and 4e list every
  reconstructed SHA in their pull request bodies, and each is
  asserted to be a merge commit (`git rev-list --merges -1 <sha>`
  returns it) or an explicit `first..last` range. That the column is
  load-bearing for phase 5 and enforced by no criterion is a real
  gap; it is recorded in Future work rather than closed here,
  because a check that reads the column is a criterion of its own.
* **The v3 bump restales the fleet on a wording change.** Every
  repository embedding the block goes non-compliant the next
  morning, for a sentence that changes no behaviour. Accepted, and
  timed: 4b lands before the sweeps, so the sweeps carry the refresh
  and the window is one working day rather than open-ended. The
  alternative -- folding the wording into some later bump -- leaves
  the block saying something the check does not do, which is the
  defect being fixed.
* **This section's own arithmetic has been wrong twice, in two
  different ways, and review caught both.** The first draft counted
  the backfill set with a file-naming grep, which put four plans in
  scope that decision 1 excludes and left one out that it includes.
  The second miscounted `development` by detecting only
  `**Merged:**` when the plan in question writes `Merged:` -- a
  matcher that answers "no record" for "record in a shape I did not
  anticipate", which is the silent-skip failure exactly. Six
  sub-agents derive their scope from the table above, and neither
  error would have been caught by running the check, because no
  check reads the `Merged` column.

  So the mitigation cannot be re-running anything. Every sweep step
  re-derives its own list by decision 1's test before editing and
  says in its pull request whether the count matched; and where a
  step expects to find no record, it must confirm that by reading
  the plan rather than by a pattern, because a plan that records its
  range in an unanticipated shape is the case that has now bitten
  twice. 4e in particular is a step whose whole content is "verify
  this section was wrong in your favour".
* **Decision 2 softens a check that is currently catching
  something.** occystrap's failure disappears. It is replaced by a
  named unjudged plan in the same verdict, and by `plan-index`'s
  existing failure, which is the criterion that can actually say
  what is wrong there. What this risk does *not* have is a
  re-entry condition: `plan-index` requires a table, not a status
  column, so occystrap can satisfy it and stay unjudged here
  indefinitely. Decision 7 states that plainly rather than
  pretending otherwise, and the standing mitigation is that the
  unjudged plan is named on the compliance page every morning.

#### Definition of done

* Every fleet plan that carries an audit phase (decision 1, not the
  naming grep) records a landing commit for each merged phase, or
  says in the plan that the range is unrecoverable and names the
  paths instead. Verified by re-deriving the in-scope list from each
  repository's own `index.md`, not from this section's table.
* Every plan in scope returns `ok` from `plan_audit_phase_state`
  afterwards. This is the mechanical post-condition the two-part
  in-scope test reduces to, and it is what 4f asserts.
* `plan-audit-phase` passes in shakenfist, ryll, kerbside,
  divergulent and development, and occystrap passes with the plans
  its index links named as unjudged, which is the whole observable
  outcome of 4a and of decision 2, the phase's most contested. The
  `tools/audit-snapshot.sh` before/after diff names every repository
  that moved and why.

  **Met in every repository except instar**, which *What the sweep
  found* records in full. Two clauses of this criterion as first
  written turned out to describe a fleet that had moved: occystrap
  is named as seven terminal-status plans rather than two
  statusless ones, because it rewrote its index on 2026-09-10 and
  the statusless bucket is now exercised by ryll instead; and
  instar fails on a second carrier that appeared the day after the
  survey. instar was swept, and its sweep did what its brief asked
  -- 4e's instar#560 refreshed the block and filled the one
  backfill cell the survey had missed. The failure is phase wording
  in a different plan, already filed as instar#554 and outside
  every sweep step's brief. The criterion is restated above to what
  was measured rather than left claiming a pass that is not there.

  4a's own before/after assertion -- only occystrap changes status
  and only ryll changes details -- is not restated here because 4f
  could not re-measure it: 4f's diff is a fresh before/after pair
  around 4f, which moved nothing. It was measured across 4a's
  commit alone, as 4a's brief required, and both predicted moves
  are recorded in the body of
  [#113](https://github.com/shakenfist/development/pull/113).
* Every SHA recorded by a sweep is a merge commit or an explicit
  `first..last` range, listed in that sweep's pull request body so
  the assertion can be re-run rather than taken on trust.
* `PLAN-queue-performance.md` has a push audit phase after phase 11,
  and phase 8's section is unchanged.
* `templates/shared-blocks/plan-push-audit-phase.md` is v3, its
  carve-out names `Complete`, `Abandoned` and `Superseded`, and no
  other line of the block differs from v2.
* A plan the index links without recording a status -- a row with no
  status cell, but equally a prose or bullet-list link -- is named in
  the criterion's verdict and is not counted as a failure, and
  `scripts/tests/test_plans.py` covers the bullet-list shape occystrap
  uses and the two-table shape ryll uses.
* `docs/audits/plan-audit-phase.md` records that exclusion, and says
  that `plan-index` requires a table rather than a status column, so
  a reader can see that the exclusion is an opt-out nothing detects.
* `docs/plans/PLAN-plan-template-blocks.md`'s Migration section
  agrees with `docs/audits/compliance.md` about which repositories
  have landed the template blocks, so this repository's own plans do
  not contradict the survey that feeds the sweep.
* `PLAN-audit-compliance-split.md` and `PLAN-scope-coverage.md`
  record `7843932` (#57) and `8b77b32` (#93) against their landed
  phases, so no plan in this repository carries a `Merged` column
  with an empty cell against a phase that has shipped.
* Every mark `prune` dropped is listed in the pull request that
  dropped it, and none was re-stamped by a sub-agent.
* `pre-commit run --all-files` passes in this repository.

#### Back brief

Before 4c begins, the management session confirms with Mikal that
shakenfist's twenty backfills -- nineteen plans that carry the phase
today, plus `PLAN-ci-cloud-sizing.md` once 4c appends one -- land as
one pull request rather than split by plan family, and that
`PLAN-queue-performance` gains a phase rather than moving phase 8.
Both are cheap to agree and expensive to redo across twenty plans.

**Merged:** `fd0678c` (#113), carrying steps 4a and 4b, and
`a19b706` (#133), carrying step 4f. The second was written in by the
phase 5 planning commit, the way phase 3's `81dc421` (#83) was
recorded by phase 4's -- a phase cannot name the commit that lands
it.

Steps 4c, 4d and 4e landed in the repositories they swept, so they
are not in this repository's history and phase 5's range cannot
reach them. They are listed for provenance rather than as a range;
each was asserted a merge commit with `git rev-list --merges -1
<sha>` returning itself, and each was reviewed by its own
repository's checks. The SHAs are abbreviated to seven characters as
the rest of this plan abbreviates them, each checked to resolve
uniquely in the repository it belongs to:

| Step | Repository | Pull request | Merge commit |
|------|------------|--------------|--------------|
| 4c | shakenfist | #4160 | `108987e` |
| 4d | divergulent | #109 | `2deb2af` |
| 4e | ryll | #371 | `c52c106` |
| 4e | instar | #560 | `3a297f7` |
| 4e | kerbside | #419 | `1d9d28d` |
| 4e | client-python-k3s | #57 | `0e88d41` |

### 5. Push audit

**Planning effort:** medium. The runbook exists, three plans in this
repository have already run it, and the four phases being audited
are on `main` -- so the judgement here is about deriving the right
range and briefing the wave 2 agents for this repository's blast
radius, not about design. **Review effort:** high for the wave 2
agents, which is what the runbook specifies for 2d and what a
5,526-line diff touching a fleet-wide criterion and a shared block
justifies for 2a and 2c. 2b is raised from the runbook's medium as
well, on `scripts/tests/test_plans.py` alone being 1,568 of the
union's insertions.

In scope: running `PUSH-AUDIT.md` over the accumulated work of
phases 1 to 4, fixing the `main...HEAD` defect three previous
audits have now recorded and none has fixed, and recording the
outcome here. Out of scope: fixing what wave 2 finds -- findings
land as their own pull request, and this plan is not complete until
each is resolved or declined in writing in this section; porting
kerbside's `tools/audit/` scripts, for the reason decision 2 gives;
and the fleet-wide widening the phase 4 sweep found, which Future
work already holds. If the audit finds nothing, that is recorded
here in one sentence.

#### What the survey found

The section this replaces made two claims that are no longer true,
corrected here at source rather than left for the run to trip over.

**This is the fourth run of the runbook in this repository, not the
first.** The old section said the runbook "is the one phase 1 wrote
for this repository, which nobody has run yet", and that this run
"adds the first evidence about a repository whose product is
automation rather than a service". Three plans here have completed
a push audit phase since: `PLAN-audit-compliance-split.md` phase 4
(`7843932`), `PLAN-audit-scripts-restructure.md` phase 6
(`76975d9..42565c2`) and `PLAN-scope-coverage.md` phase 5
(`c24636a, 0759e46`). The evidence the old section wanted has
already been gathered three times, and it is written down: this
phase starts from those records rather than rediscovering them.

**The runbook's range defect has been reported three times and
fixed none.** Every diff command in `PUSH-AUDIT.md` is written
`git diff main...HEAD`. `PLAN-audit-compliance-split.md:656` filed
it as Future work after a stale local `main` silently widened its
first wave 1 run; `PLAN-audit-scripts-restructure.md:564` recorded
running against `origin/main...HEAD` instead; and
`PLAN-scope-coverage.md:384` hit the other half of it -- against
already-merged work `main...HEAD` is *empty*, which reads as a
clean audit rather than as no audit. Three plans, three records,
one unfixed sentence. Step 5a fixes it.

**The range is six diffs -- five merges and one direct landing --
and a span over them is wrong.** The `Merged` column gives
`5b1fb74` (#49), `ff92357` (#50), `81dc421` (#83), `fd0678c`
(#113) and `a19b706` (#133), spanning 2026-08-24 to 2026-09-16.
Those five are not the whole of it. Four commits chained off
`ff92357` landed on `main` directly on 2026-08-25 -- `ac34b76`
"Record plan phase merge commits in a column.", `179e6a3`,
`3576714` and `240d278` -- and none of them is reachable from any
of the five merge diffs. Together they are 340 insertions against
55 deletions over 8 files, and `ac34b76` is the commit that put
the `Merged` column itself into
`templates/shared-blocks/plan-push-audit-phase.md`, taking the
block from v1 to v2. Auditing the five merges alone would skip the
fleet-wide change this plan exists to make, so phase 2's cell now
records `ac34b76..240d278` beside its merge -- as a record of the
commits, not as a diff range -- and decision 1 takes six diffs, the
sixth written `ff92357..240d278` because `ac34b76..240d278` as a
diff excludes `ac34b76` and with it the v1 to v2 bump, giving
336/65 rather than 340/55. The six total 30 distinct files and
5,526 insertions against 411 deletions -- the sixth touches no path the five did
not already reach. Spanning them instead --
`5b1fb74^1..a19b706` filtered to those 30 paths, which is what a
path-union tool naturally produces -- gives 11,579 insertions and
9,279 deletions across 213 commits that touch those paths and are
not merges, because
`PLAN-audit-scripts-restructure` moved roughly 13,000 lines through
`scripts/audit/` in the middle of the window. Eight of the thirty
files are created by *other* plans inside the window and do not
exist at `5b1fb74^1` at all -- `PLAN-TEMPLATE.md`,
`docs/plans/PLAN-audit-compliance-split.md`,
`scripts/audit/checks/plans.py`, `scripts/audit/registry.py`,
`scripts/audit/text/markdown.py`, `scripts/tests/test_markdown.py`,
`scripts/tests/test_metadata.py` and `scripts/tests/test_plans.py`
-- so a span counts each one's entire creation as this plan's work.
Four more are absent at that base only because this plan's own
merges create them -- `PUSH-AUDIT.md`,
`docs/plans/PLAN-push-audit-phase.md` and
`templates/shared-blocks/plan-push-audit-phase.md` at `5b1fb74`,
and `docs/audits/plan-audit-phase.md` at `81dc421` -- which is
expected and is evidence of nothing. Decision 1 takes the union of
the six diffs instead.

**kerbside's `tools/audit/plan-range.sh` exists, and would produce
exactly the wrong range here.** It is real --
`tools/audit/plan-range.sh`, `wave1.sh` and `wave2-mechanical.sh`
are in kerbside today -- and the old section's "if this repository
has adopted it by then" resolves to: not adopted, and
`PLAN-scope-coverage.md:295` declared porting it out of scope.
Reading it settles the question rather than deferring it again: it
"spans the range from the first SHA's parent to the last SHA" with
a path filter, which is the 11,579-line span above. It is right for
kerbside, whose plan phases land close together, and wrong for a
plan whose phases are three weeks apart with a 13,000-line
restructure between them. Decision 2 declines the port and Future
work records what a correct one would have to do.

**The `Merged` convention this plan introduced holds for its own
phases.** Each of the five SHAs is a merge commit: `git rev-list
--merges -1 <sha>` returns each one, and phase 2's added range is
the direct-landing form the same block names for work that did not
arrive through a pull request. Phase 4's cell was still
empty for 4f when this was planned; recording `a19b706` (#133) in
it is part of this planning commit, which is the convention phase 4
described and phase 3 was recorded by.

**Phase 4's one deferred item has closed itself.** The five review
marks `prune` dropped in #113 needed a human to read those files
again, which no agent could do. `REVIEWS.md` now reports 189 of 189
in-scope files reviewed, and `review-coverage` passes with 0 needing
review, so the phase leaves nothing outstanding on that front.

**The repository is nearly clean going in, and this claim went
stale between planning and running -- see A4 under *Outcome*.** As
planned, `scripts/audit-check.py` against this tree with `gh`
authenticated reported 55 checks: 31 pass, 0 fail, 24
`not_applicable`. When the audit actually ran, five days later, it
reported 30 pass, 1 fail and 24 `not_applicable`: `review-coverage`
had begun failing, at 180 of 189 in-scope files reviewed with 9
needing review against a threshold of 5, measured on `main` at
`0e16ef1` on 2026-09-19. `0e16ef1` "Prune stale review marks."
landed that backlog, and 5a's own prune of the `PUSH-AUDIT.md` mark
took it to 179 of 189 the same day. Every count here is quoted with
the commit and date it was taken at, because this paragraph has now
gone stale twice: by `5861c0a` on 2026-09-20 `main` reported 175 of
190 with 15 needing review. The direction is what is durable -- the
criterion fails, and it failed before this phase touched anything --
and an undated count in a plan is a claim with a shelf life. Two other checks --
`delete-branch-on-merge` and `scope-coverage` -- query the GitHub
API and fail closed without credentials, so an unauthenticated
re-run reporting two further failures is the environment rather
than a contradiction. `push-audit` passes -- "PUSH-AUDIT.md carries
current shared blocks and is referenced from AGENTS.md" -- which is
phase 1's own done-criterion still holding six weeks later.

Everything else the old section said is intact: the runbook is the
one phase 1 wrote, findings land as their own pull request, and
phase 3's decision no longer waits on this run.

#### Decisions

**1. The range is the union of six diffs, not a span.** Five are
the merge commits, read as `git diff <sha>^1 <sha>`; the sixth is
`git diff ff92357 240d278`, the four commits phase 2 landed on
`main` directly -- equivalently `ac34b76^..240d278`, the caret
being what makes it reach `ac34b76` and so the block's v1 to v2
bump. Every grep is run six times. It is more commands
than a single range and it is the only formulation that audits
this plan's work, all of it, and nothing else -- the measurement
above is the argument, 5,526 lines against 11,579, and the four
direct commits carry the `Merged` column's own introduction. The
cost is that a sub-agent must carry six diffs rather than one, so
every brief spells the list out rather than referring to it:

```
AUDIT_RANGE=5b1fb74^1..5b1fb74
AUDIT_RANGE=ff92357^1..ff92357
AUDIT_RANGE=81dc421^1..81dc421
AUDIT_RANGE=fd0678c^1..fd0678c
AUDIT_RANGE=a19b706^1..a19b706
AUDIT_RANGE=ff92357..240d278
```

5b to 5f each set that variable once per diff and read all six.
Naming them is what makes this typing rather than judgement, and
it is why no wave 2 brief can quietly fall back on 5a's
`origin/main...HEAD` default, which against merged work is the
empty diff this phase exists to avoid.

**2. kerbside's `tools/audit/` scripts are not ported here.** This
is the decision most likely to be argued with, because the Future
work bullet "Move the mechanical waves out of the runbook" wants
exactly that and the scripts already exist one repository away.
Three reasons. The tool is wrong for this range, as the survey
shows, so porting it would mean porting and then fixing it inside
an audit phase whose job is to run an audit. Its base branch is
`develop`, hard-coded in a resolution loop, and this repository is
`main`. And a phase that ports tooling and then audits with the
tooling it just wrote has no independent check on either. Future
work records what a correct `plan-range.sh` for this repository
needs: a union mode for phases that do not land contiguously, and
the base branch taken from the repository rather than assumed.

**3. The `main...HEAD` fix lands before the audit runs, as its own
step.** It is a two-line correction to a runbook that three plans
have now worked around, and running a fourth audit against a
runbook everyone patches in their head is how it stays unfixed. It
goes first because 5c to 5f read the runbook 5a corrects.

**4. Wave 2 is briefed for what this plan actually changed, not
for the repository in general.** The four phases changed a fleet
criterion (`scripts/audit/checks/plans.py`, +899 in this range
and 1,495 lines now, with `scripts/tests/test_plans.py` at +1,568
and 2,754 lines now), a shared block that sixteen repositories
embed, a runbook, and 1,688 lines of plan prose across
`docs/plans/`, 1,429 of them in this file. Every `+N` here is an
insertion count over the union of all six diffs, measured the same
way as the 5,526 figure above; where a whole-file size is useful
to an agent that will read the file rather than only its diff, it
is named as one. So 2a reads the criterion's bucket logic, 2b reads whether
the tests test what their names say, 2c reads whether four
documents about the same convention now agree, and 2d reads the
one thing here with a blast radius -- what a shared-block bump and
an issue-title change do to sixteen repositories at 06:00 UTC.

**5. Findings land as their own pull request against `main`.** The
shared block requires it and the three previous audits here all did
it. That applies to wave 2's findings: this branch carries the
plan record and 5a's runbook fix, and 5b runs from this branch
with 5a's commit already in it rather than waiting on a separate
pull request.

**6. The phase is not complete when the audit runs.** It is
complete when every finding is resolved or declined in writing in
this section, and `docs/plans/index.md` moves to `Complete` only
after the findings pull request merges -- not when the audit
finishes.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | low | sonnet | none | Fix the range defect in `PUSH-AUDIT.md`, and nothing else in that file. Every diff command in the wave 1 block and in the four wave 2 briefs is written `git diff main...HEAD`; the "How to use this runbook" section at line 27 states that as the rule. Two failure modes, both already recorded against this repository: a stale local `main` silently widens the audit (`PLAN-audit-compliance-split.md:656`), and against work that has already merged `main...HEAD` is empty and reads as a clean audit rather than as no audit (`PLAN-scope-coverage.md:384`). Replace the rule with one that says the audit runs over an explicit range held in a single variable, `AUDIT_RANGE`: the default is `origin/main...HEAD` for unmerged work, and work that has already landed sets `AUDIT_RANGE=<sha>^1..<sha>`, with one sentence saying a plan whose phases landed as several merges runs each command once per merge, and one saying that a phase which landed on `main` directly sets `AUDIT_RANGE=<first>^..<last>`, with the caret: `first..last` is the label the `Merged` column records, and because `A..B` excludes `A` the diff that covers that label is one commit wider than the bare range. Say why in the runbook rather than only stating the form -- a reader who drops the caret silently loses the first commit of the phase, which is where a version bump or a column addition tends to live. Have the rule also say to run `git fetch origin` before the checks: `origin/main` is itself a cached ref that only advances on fetch, so a clone left alone for a week reproduces `PLAN-audit-compliance-split.md:656`'s silent widening in a milder form, and the new default does not close that on its own. Use that one name everywhere. `git diff A..B` and `git diff A B` are the same thing, so a second `AUDIT_BASE`/`AUDIT_HEAD` pair would buy nothing and would give a later reader two names to choose between. Write every command as `git diff "${AUDIT_RANGE:-origin/main...HEAD}"`, defaulted in the expansion rather than relying on the reader to export it: an unset bare `$AUDIT_RANGE` diffs the working tree, which on the clean tree wave 1 requires is empty, and every check then passes over nothing. Do this rather than leaving literal `main...HEAD` strings for every future reader to substitute by hand -- there are sixteen: eleven in the wave 1 block's eight checks, one in the rule at line 27, and one in each of the four wave 2 briefs. Do not touch any `<!-- shared-block: -->` region: a bump files issues in sixteen repositories. Do not change what any check looks for. Wrap at the file's existing width. `PUSH-AUDIT.md` may carry a human review mark in `REVIEWS.md`; if it does, this edit stales it. Do not go looking for a specific row: this pull request edits `PUSH-AUDIT.md` itself, so `prune-reviews` drops that mark on the merge and by the time this step runs the file may carry no mark at all. **Do not prune, regenerate or commit `REVIEWS.md`** -- the `plan-phase-landing` shared block makes that the `prune-reviews` workflow's job, and `review-tracking-tests` does not fail on a staled mark or a moved header count, so nothing here goes red. Say in the pull request body which mark the edit stales. **Do not re-stamp it**: the mark attests that a person read that exact content, so a staled file needs a human to read it again. Commit subject: "Give the push audit an explicit range." |
| 5b | medium | sonnet | none | Wave 1 of `PUSH-AUDIT.md`, over this plan's six diffs. Run `pre-commit run --all-files` first, on a clean tree -- `git status --short` empty before you start, because `review-tracking.py stamp` takes its SHAs from the git **index**, so an unstaged edit is attested at the staged content and the run gives a false pass for exactly the check that guards the attestations. Then run every check in the wave 1 block six times, once per diff, setting `AUDIT_RANGE` to each of the six ranges listed in decision 1 in that order -- the one variable 5a leaves in the runbook, not a second one. Before the checks for each diff, run `git diff --numstat "$AUDIT_RANGE" | awk '{i+=$1; d+=$2} END {print i, d, NR}'` and record the three numbers. Insertions and deletions sum across the six to 5,186 and 356 for the five merges plus 340 and 55 for the direct landing, so 5,526 and 411 in total, which is measured under *What the survey found*. **`NR` does not sum to 30.** It is a per-diff file count, and the six are 16, 7, 14, 11, 2 and 8, totalling 58; the 30 is the size of the *union* of the six path sets, which no sum of that awk's output can produce. Check it separately and once, with `for r in <the six ranges>; do git diff --numstat $r; done | cut -f3 | sort -u | wc -l`, which must print 30. A run that does not reproduce the insertion and deletion figures, or whose union is not 30 paths, is reading the wrong range and stops there rather than reporting a clean audit over nothing -- but a per-diff `NR` that is not 30 is the arithmetic working, not a mismatch. Do not use a single spanning range: `5b1fb74^1..a19b706` sweeps in 213 commits that touch those paths and are not merges, and roughly 9,000 deletions from `PLAN-audit-scripts-restructure`'s move through `scripts/audit/`, which is measured under *What the survey found*. Report every hit with a verdict -- hit, looked at, accepted or blocking, and why -- and say explicitly which checks were empty in all six, because a silent check is indistinguishable from an unrun one. Expect and explain rather than ignore: `templates/shared-blocks/plan-push-audit-phase.md` changed in three of the five merges and in the direct landing, and the version marker moved in three of those four -- `5b1fb74` creates it carrying v1, `ac34b76` takes it to v2 and `fd0678c` to v3, while `ff92357` adds six lines of rule text to it with **no** bump. So wave 1's "a shared block edited without its version bumped" check has a genuine hit on `ff92357`, and it is the check whose blast radius is sixteen repositories. Report it with a verdict rather than treating it as expected; the mitigating facts, which are for the verdict rather than instead of it, are that `ff92357` is 32 minutes after `5b1fb74` on 2026-08-24, before any repository had embedded the block, and that `ac34b76` superseded the wording the next day. `REVIEWS.md` changed in three of them and the prunes it records are the convention working; it will **not** have changed on this branch, because 5a staled the `PUSH-AUDIT.md` mark and deliberately left it for `prune-reviews`; `docs/audits/compliance.md` is not in this range at all. Report, do not fix. |
| 5c | high | sonnet | none | Wave 2a, code quality, per the brief in `PUSH-AUDIT.md` -- read the runbook as 5a leaves it, not a cached copy. Diff is the six ranges of decision 1. Set `AUDIT_RANGE` to each of the six ranges in decision 1 in turn -- do not leave it unset, because 5a's `origin/main...HEAD` default against already-merged work is the empty diff this phase exists to avoid -- and report the insertion total you saw for each. The code in them is almost entirely `scripts/audit/checks/plans.py` (+899 over the union, 1,495 lines now) and `scripts/audit/text/markdown.py` (+136 over the union, 387 lines now); neither is touched by the sixth range, so those two figures are the same for five diffs or six. Take 5b's grep report as input. The highest-value reading is `plan_audit_phase_state()` and `plan_index_entries()`: phase 4 added a fourth bucket, plans the index links without recording a status, and the criterion now has four ways to decline to judge a plan -- unphased, terminal-status, statusless, and unresolved link. Ask what happens when two apply at once, whether any plan can fall through all four and be silently counted as passing, and whether the fenced-code blanking that `plan_phases()` does is applied on every path that searches for `PUSH-AUDIT.md` rather than most of them. `iter_markdown_table_rows()` in `text/markdown.py` is read by several criteria; a behaviour change there is a fleet-wide change, so check its callers against what it now returns for a malformed table. |
| 5d | high | sonnet | none | Wave 2b, test review, per the brief in `PUSH-AUDIT.md`, over the six ranges of decision 1. Set `AUDIT_RANGE` to each of the six ranges in decision 1 in turn -- do not leave it unset, because 5a's `origin/main...HEAD` default against already-merged work is the empty diff this phase exists to avoid -- and report the insertion total you saw for each. `scripts/tests/test_plans.py` gained 1,568 lines over the union and is 2,754 lines now, and `scripts/test_audit_check.py` gained 238 over the union, 49 of them in the sixth range alone -- that is the pre-restructure path, which `PLAN-audit-scripts-restructure` has since carved up into `scripts/tests/`, so it exists in the diff but not in the tree, and `scripts/tests/test_markdown.py` and `scripts/tests/test_metadata.py` are two of the files carved out of it. Establish that the tests test what their names say rather than that they pass: phase 4's decision 2 is the contested one and its worked example has moved repositories since, so read `test_bullet_list_index_records_no_status_and_is_not_judged`, `test_an_index_of_only_statusless_plans_is_not_n_a`, `test_a_statusless_link_to_no_file_is_named_as_unresolved` and `test_a_statusless_unphased_plan_is_named_as_statusless` against the real shapes they claim to cover -- occystrap's bullet list, which the fleet sweep found has since been rewritten into a status table, and ryll's `## Standalone plans` second table, which is now the only live example. A test whose fixture no longer matches any repository is not wrong, but it is worth knowing which of these are the last copy of a shape. Name any assertion in the diff that would still pass if the behaviour it names were removed. |
| 5e | high | sonnet | none | Wave 2c, documentation review, per the brief in `PUSH-AUDIT.md`, over the six ranges of decision 1. Set `AUDIT_RANGE` to each of the six ranges in decision 1 in turn -- do not leave it unset, because 5a's `origin/main...HEAD` default against already-merged work is the empty diff this phase exists to avoid -- and report the insertion total you saw for each. Four documents describe the same convention and the question is whether they agree: `PUSH-AUDIT.md`, `templates/shared-blocks/plan-push-audit-phase.md` (now v3), `docs/audits/plan-audit-phase.md` and `AGENTS.md`. Check specifically that the v3 carve-out sentence -- a plan already `Complete`, `Abandoned` or `Superseded` is not reopened -- says the same thing in the block, in the criterion spec and in the audit documentation; that `docs/audits/plan-audit-phase.md` records the statusless exclusion *and* says `plan-index` requires a table rather than a status column, which is what makes the exclusion an opt-out nothing detects; that `docs/audits/README.md`'s criterion row and `PLAN-TEMPLATE.md`'s block list agree on nine blocks; and that `AGENTS.md`'s "drop this qualifier once the sweep has landed" is still accurate, given that phase 4's Future work now says explicitly that phase 4 was not that sweep. 1,429 of the union's insertions are plan prose in `PLAN-push-audit-phase.md` itself, out of 1,688 across `docs/plans/`; read this file for claims about other repositories that were true when written and are not now, which is the failure mode phase 4 spent a whole step on. |
| 5f | high | opus | none | Wave 2d, security review, per the brief in `PUSH-AUDIT.md`, over the six ranges of decision 1. Set `AUDIT_RANGE` to each of the six ranges in decision 1 in turn -- do not leave it unset, because 5a's `origin/main...HEAD` default against already-merged work is the empty diff this phase exists to avoid -- and report the insertion total you saw for each. Read the actual code. This repository's blast radius is not a running service: it is sixteen repositories audited at 06:00 UTC and a shared block copied into each of them. So the surface is what this diff does to other people's repositories. Three things to read hardest. `FROZEN_ISSUE_TITLES` and the issue-title interface: `plan-audit-phase` is a new criterion that files and closes issues fleet-wide, and the title is the idempotency key -- establish that a rename in this range cannot orphan open issues, and that the detail strings it files, which quote plan filenames and phase names read from another repository's markdown, cannot carry a mention, a closing keyword or a markdown injection into an issue body. `scripts/audit/checks/plans.py` reads arbitrary markdown from sixteen repositories: consider what a crafted `index.md` or plan file does to the table iterator and the link parser, including a link target that escapes `docs/plans/`. And the shared-block versioning: confirm the version marker moved everywhere the wording did. The range contains one place it did not -- `ff92357` adds rule text to the block and leaves it at v1 -- so the question for you is not whether it happened but what the detection gap is, given that a wording change without a bump leaves sixteen repositories on the old text with nothing detecting it. Apply the `path-traversal-review` shared block. |
| 5g | high | opus | none | Management triage, in the session rather than a sub-agent: read all five reports, decide each finding blocking, advisory or declined, and write the outcome into this section under an **Outcome** heading -- what wave 1 found, what each wave 2 agent found, and for every finding either where it was fixed or why it was declined, in writing. Read 5a's commit yourself before anything else: it is the only change in this phase the audit range does not contain, so no wave reads it, and what it must not have done is alter what any wave 1 check matches. Then, before reading a single finding, check that each of the five reports states the per-range diff totals it saw, that wave 1's six sum to 5,526 insertions and 411 deletions over a union of 30 paths, and that no wave 2 report shows the 11,579-line span or an empty `origin/main...HEAD`. A report that does not is re-run rather than triaged -- triaging findings from an unverified range is how a vacuous audit gets written up as a clean one. Work the `PUSH-AUDIT.md` management checklist, including the two items this plan's own work bears on: that the shared-block versioning in this range was deliberate -- including `ff92357`, which changed the block without a bump -- and its fleet-wide consequence is understood, and that `REVIEWS.md` and `docs/audits/compliance.md` are generated rather than hand-edited. Fixes land as their own pull request against `main`, not on this branch. Then set phase 5 to `Complete` in the Execution table, and move `docs/plans/index.md` to `Complete` only once the findings pull request has merged -- decision 6. Phase 5 cannot name the commit that lands it any more than phase 4 could, and it is the last phase, so no later planning commit exists to do it for them: the findings pull request is the carrier, and it writes phase 5's merge SHA into both the Execution table and this section's `**Merged:**` line. If the audit finds nothing worth fixing, a one-line follow-up commit does it instead -- the cell does not stay blank, in the plan that introduced the column. |

Steps 5a and 5b are sequential: 5c to 5f read the runbook 5a
corrects, and 5b's grep report is 5c's input. The runbook requires
wave 1 to pass before wave 2 is worth spending on, so 5c to 5f are
spawned in parallel only once 5b reports clean. All steps run in
this phase's worktree; `Isolation` is `none` because only 5a writes
anything, and 5b to 5f write no code at all.

#### Risks and mitigations

* **The audit reads the wrong range and passes vacuously.** This
  is the failure this phase is most likely to have, because it is
  the failure the last three had: `main...HEAD` against merged work
  is empty, and an empty diff produces a clean report from every
  agent. Mitigated by 5a fixing the runbook before anything runs,
  by every brief naming the six ranges explicitly, and by 5b being
  required to state which checks were empty in all six rather than
  reporting only hits -- a check that found nothing and a check
  that ran against nothing look identical otherwise. The management
  session confirms in 5g that each agent's report quotes a non-empty
  diff.
* **A sub-agent audits the span instead of the union.** Six diffs
  is more tedious than one and the tempting simplification is
  exactly the wrong one. Mitigated by the measurement being in the
  plan rather than the reasoning: 5,526 lines against 11,579, named
  in 5b's brief with the reason, and by decision 1 listing the six
  ranges verbatim so no brief has to derive them. Checked in 5g by
  asking each agent what its diff totalled.
* **The runbook fix changes what a check looks for.** 5a touches
  sixteen command lines; a substitution that also alters a pattern
  would weaken wave 1 permanently, in this repository and in every
  repository that copies the runbook later. Mitigated by 5a's brief
  forbidding any change to what a check matches, and by 5g diffing
  5a's commit for pattern changes rather than trusting the subject
  line.
* **Findings pull request never lands and the phase looks done.**
  The audit finishing is not the phase finishing. Mitigated by
  decision 6 and by the Definition of done requiring the index row
  to move only after the findings pull request merges.
* **A shared-block edit escapes into sixteen repositories.** 5a
  edits a file that sits beside shared blocks and 5e reads them.
  Mitigated by 5a's brief forbidding any edit inside a
  `<!-- shared-block: -->` region, and by 5g reading 5a's commit
  directly -- which is the only thing that reads it, because 5a's
  commit lands after `a19b706` and every audit range ends at a SHA
  already on `main`. Wave 1's shared-block-without-a-version-bump
  check runs over all six diffs, but that is the historical
  instance of the same risk, not a check on 5a.

#### Definition of done

* `PUSH-AUDIT.md` contains no *unqualified* `main...HEAD`, and its
  "How to use this runbook" section says how to audit work that has
  already merged. Checked by
  `grep -nE '(^|[^/])main\.\.\.HEAD' PUSH-AUDIT.md` producing no
  output. The qualifier is load-bearing: 5a's own default is
  `origin/main...HEAD`, which carries the forbidden string as a
  substring, so a bare `grep -c 'main\.\.\.HEAD'` returning 0 would
  mean 5a had disobeyed its brief rather than followed it.
* Each of the eight wave 1 checks was run against all six diffs,
  and 5b's report states a verdict for each of the forty-eight
  check-range pairs, including the empty ones. The report also
  carries the six per-diff `--numstat` totals; their insertions and
  deletions sum to 5,526 and 411, and the union of their paths --
  counted once, not summed, because the per-diff file counts are
  16, 7, 14, 11, 2 and 8 and total 58 -- is 30. That is an
  arithmetic check that the range was right, rather than an
  attestation from the agent whose mistake it is meant to catch.
* 5b reports a verdict on the unbumped shared-block edit in
  `ff92357` rather than recording it as expected, because it is a
  true positive from the one wave 1 check with a sixteen-repository
  blast radius.
* Each wave 2 report names the six diffs it read and the line
  count it saw for each, and none of the four is the 11,579-line
  span or an empty `origin/main...HEAD`.
* Every wave 2 finding appears in this section under **Outcome**
  with one of: the commit that fixed it, or the reason it was
  declined. A finding that is neither is not done.
* The findings pull request has merged, or this section says in one
  sentence that the audit found nothing.
* Phase 4's `Merged` cell names `a19b706` (#133) and phase 2's
  names `ac34b76..240d278` beside `ff92357`, both recorded by this
  planning commit.
* 5a's own commit is reviewed by the management session in 5g
  rather than by any wave. It is the one change in this phase that
  the audit range does not contain, so nothing else reads it.
* `pre-commit run --all-files` passes in this repository.
* `push-audit` and `plan-audit-phase` still pass on `development`
  after the runbook edit, and the `tools/audit-snapshot.sh`
  before/after diff over the fleet reports no firm differences --
  5a edits a runbook and changes no check. `push-audit` is the
  criterion that reads `PUSH-AUDIT.md`, so it is the one 5a can
  break, by disturbing a shared-block region; `plan-audit-phase`
  reads this file instead.

#### Back brief

Before 5b begins, the management session confirms with Mikal that
the audit runs over the six diffs as a union rather than as a
span, and that 5a's rewrite of the range convention in
`PUSH-AUDIT.md` is wanted here rather than deferred to the Future
work bullet that would move the mechanical waves into `tools/`
altogether. Both are cheap to agree now and expensive to redo after
four agents have read the wrong diff.

#### Outcome

The audit ran on 2026-09-19 over the six diffs of decision 1: 5a as
one commit, wave 1, and the four wave 2 agents in parallel. It found
**two blocking defects, both in `scripts/audit/checks/plans.py`, both
reachable from a commit in any of the sixteen audited repositories**.
The disposition table below carries ten rows: those two, wave 1's
one true positive, and seven advisory items. The runbook defect 5a
fixed is not among them -- it was found by the survey that planned
this phase rather than by the run, and it was fixed before anything
ran.

**The range gate passed.** Every one of the five reports states the
per-diff totals it read, and all five match the figures the
management session measured independently before any agent started:
1,060 / 289 / 3,161 / 402 / 274 / 340 insertions, summing to 5,526
against 411 deletions over a union of 30 paths. No report quoted the
11,579-line span as its range and none ran against an empty
`origin/main...HEAD`, so no report needed re-running. Wave 2c
reproduced the 11,579 / 9,279 span figure as well, as a contrast
rather than as its range.

One report's arithmetic did not survive the gate, which is the
argument for the gate being arithmetic rather than an attestation.
Wave 2b reported `scripts/tests/test_plans.py` at 1,576 insertions
against the plan's 1,568, and `scripts/test_audit_check.py` at 239
against 238, then declined to reconcile the difference -- "close
enough that I'm confident this is the right file". Re-measured here:
1,294 in `81dc421` plus 274 in `fd0678c` is 1,568, and 189 in
`5b1fb74` plus 49 in the sixth range is 238. The plan's figures are
right and the agent's were wrong. The step that waved away its own
discrepancy is the step whose numbers did not reproduce.

**Wave 1** ran all eight checks against all six diffs and returned a
verdict for each of the forty-eight pairs. Five checks were empty
across all six -- long lines, hand-edited `compliance.md`, a
generated block in a criterion spec, `FROZEN_ISSUE_TITLES` renames,
and TODO markers -- and for two of those the agent went past "the
grep found nothing" to confirm the files were genuinely in the diff:
`docs/audits/compliance.md` is absent from all six ranges, while
three ranges touch `docs/audits/*.md` non-trivially and the check
read them. That is the empty-versus-unrun distinction this phase
required, done properly rather than asserted.

Wave 1's one true positive is recorded as W1 below. Two checks
over-fired and are recorded as A5 and A6.

**Findings, with a disposition for each.** B1, B2, A1, A3 and A7 are fixed
in the findings pull request, [#155]. A2 was declined on reading
the code, and A5 and A6 on the merits.

| # | Finding | Disposition |
|---|---|---|
| B1 | Symlink escape in `plan_index_target_path` | Fixed in [#155] |
| B2 | Quadratic `PLAN_LINK_RE` with no size cap and no job timeout | Fixed in [#155] |
| W1 | `ff92357` edits the shared block with no version bump | Accepted, no fix |
| A1 | Raw `details` spliced into issue bodies | Fixed in [#155] |
| A2 | `iter_markdown_table_rows` carries a header across a table boundary | Declined, test added |
| A3 | A docstring that is false about its own code path | Fixed in [#155] |
| A4 | Two stale claims in this plan's own survey | Fixed here |
| A5 | New-third-party-import check over-fires on first-party imports | Declined |
| A6 | New-suppression check over-fires on carried-over `# noqa` | Declined |
| A7 | `markdown_table_cells()` has no direct test | Fixed in [#155] |

**B1 -- the audit reads files outside the checkout, and quotes them
into an issue it files.** `plan_index_target_path`
(`scripts/audit/checks/plans.py:513-518`) proves containment with
`os.path.normpath`, which is textual. It correctly rejects a
`../../` link target -- confirmed -- and does not stop a symlink
committed inside `docs/plans/`, because `os.path.isfile()` follows
it. The `paths.get(name)` fallback on the next line has no
containment check at all. Reproduced directly: a symlink at
`docs/plans/PLAN-leak.md` pointing outside the tree is accepted and
resolves outside `docs/plans/`, and the file's content reaches the
criterion's details string, which `audit-manage-issues.py` posts to
GitHub. Exfiltration is narrow, and wave 2d was careful to say so:
the target must parse as a plan for its content to be quoted, and
`/proc/self/environ` and `.git/config` were both read and neither
parsed. The primitive is real; weaponising it for the `AUDIT_TOKEN`
is not straightforward.

What makes this blocking rather than theoretical is that the fix is
already in the process and was not used. `Repo.contains()`
(`scripts/audit/repo.py:206-208`) is `realpath`-based and its
docstring gives this exact reasoning -- "Public because `read()` is
not the only way a check opens a file... One implementation of it,
called from both, rather than a second realpath comparison that
drifts." It is called from one place in the entire checks package,
`npm_dependencies.py:448`. `PlanAuditPhase.run` already holds the
`Repo`. This is the `path-traversal-review` block's own third bullet
-- a helper that cannot be forgotten -- forgotten at three sites.

**B2 -- one markdown file can stop the whole fleet's audit.**
`PLAN_LINK_RE` (`plans.py:148`) is `\[([^\]]*)\]\(([^)]+)\)`, which
backtracks quadratically on a run of `[` with no closing paren.
Measured here at 0.057s, 0.159s and 0.676s for n of 2,000, 4,000 and
8,000 -- 2.8x then 4.3x per doubling, against the 4x a quadratic
predicts. Wave 2d measured the same curve end to end through the
real checks and extrapolated a 2 MB `index.md` to roughly five and a
half hours per check, with both `plan-index` and `plan-audit-phase`
reading it.

Neither read is bounded: `plans.py:422` and `plans.py:1306` are both
a bare `open(path, 'r', errors='replace')`. The bound exists --
`PLAN_SOURCE_MAX_BYTES` is defined at `plans.py:58` and used at
`plans.py:1004` by a sibling check in the same module. The
amplification is what raises this above a slow check:
`consistency-audit.yml` sets no `timeout-minutes` on the `audit`
job, so the ceiling is the 360-minute default; `manage-issues` is
`needs: audit` with no `if:`, and `update-docs` needs both. One slow
matrix leg therefore skips issue filing and compliance-page
regeneration for all sixteen repositories. `report-failure` does
fire, so the outage is loud rather than silent, but the page still
shows yesterday's verdicts.

Both blocking findings share a root cause worth naming: `plans.py`
reimplements two bounds this codebase already has -- containment and
a read size cap -- and gets both weaker than the originals.

**W1 -- the unbumped shared block, accepted.** `ff92357` adds six
lines of rule text to
`templates/shared-blocks/plan-push-audit-phase.md` and leaves the
marker at v1. Wave 1 reported it as a genuine hit rather than as
expected, which is what its brief demanded, and it is the one check
in wave 1 whose blast radius is sixteen repositories. Accepted, for
reasons that are a verdict rather than a dismissal: `ff92357` lands
32 minutes after `5b1fb74` on 2026-08-24, before any repository had
embedded the block; `ac34b76` superseded that wording the next day
at 02:33 UTC; and the 06:00 UTC run on 25 August therefore already
saw v2. Zero mornings of wrong issues -- by luck rather than design.

Wave 2d was asked what the detection gap is, and corrected the
question's premise, which is the most useful thing any agent did
here. An unbumped wording change is **not** undetected.
`validate_shared_blocks` (`scripts/audit/checks/shared_blocks.py:109-118`)
compares versions and wording in an `if`/`elif`, so when versions
are equal it compares text -- and every repository already carrying
v1 would be told its copy had **drifted**. The failure is loud but
misattributed: it accuses sixteen maintainers of editing a block
they never touched, and points them at a local edit that does not
exist, when the true fix is to pull the canonical file. Nothing in
this repository compares a canonical block's text against its own
version, and the wave 1 tripwire is two independent greps a human
must correlate -- the runbook never says that a non-empty first
output beside an empty second one *is* the finding. Recorded in
Future work rather than fixed here: it is a new check, not an audit
finding.

**A1 -- attacker-controlled text reaches issue bodies unescaped.**
`audit-manage-issues.py:169` splices `check_result['details']`
directly into the body. This range adds a criterion whose details
quote plan filenames and heading text read from another
repository's markdown, uncapped and unescaped -- `plans.py:874`
builds `the plan has a "{near[-1]}" heading` from any heading
matching `^push[-\s]audit\b`. The mitigation already exists on the
other publication path: `defuse()` in `audit-update-docs.py:179-206`
collapses newlines and escapes comment markers, and its docstring
states this threat precisely. The compliance page gets it; the issue
body does not. Bounded honestly by wave 2d: `@org/team` in an issue
body does notify and the issue is authored by the `AUDIT_TOKEN`
identity, but closing keywords do not work in issue bodies -- tested,
a verified negative rather than an assumption -- HTML is sanitised
by GitHub's renderer, and the issue lands on the attacker's own
repository. Advisory rather than blocking because it widens a
pre-existing class rather than opening one: `PlanIndex` already
quoted uncapped cell text. Fixed in the findings pull request
alongside B1 and B2, since it is the same file and the same review.

**A2 -- declined on reading the code.** Wave 2a raised
`iter_markdown_table_rows` not resetting `header` at a table
boundary: a `|`-prefixed line directly under a table, with no blank
line between, is read as a data row of it. That is not a
misattribution, because it is what the renderer does -- a table
ends at the first line that is not a row, so there is no second
table for the header to leak into, and GitHub renders those two
lines as one table. Fixing it would have moved the parser away from
the renderer. [#155] pins the behaviour and the blank-line boundary
in a test instead, so the next reader does not make that change.
This is the one finding whose disposition changed between triage
and fix, which is an argument for writing the fix before writing
that a finding is real.

**A3 and A7 -- fixed in the findings pull request.** A3:
`test_abandoned_plan_without_the_phase_passes` carries a docstring
that is false about its own code path -- it claims "the check
genuinely reads it and passes on the status rather than passing
because there was nothing to judge", and wave 2b proved by mutation
that making `plan_audit_phase_state()` raise unconditionally leaves
the test passing, because the terminal-status check short-circuits
before the file is opened. The test is sound; its docstring will
mislead the next person debugging a phase-ordering regression. A7:
`markdown_table_cells()` has no direct unit test.

**A4 -- this plan's own survey had gone stale, fixed here.** The
paragraph "The repository is clean going in" claimed `REVIEWS.md`
reported 189 of 189 in-scope files reviewed with `review-coverage`
passing at 0 needing review, and 31 pass / 0 fail / 24
`not_applicable`. Both were true when phase 5 was planned and
neither is true now: `main` reports 180 of 189 with 9 needing review
against a threshold of 5, so `review-coverage` **fails**, and the
audit reports 30 pass / 1 fail / 24 `not_applicable`. Found
independently by the management session and by wave 2c. This is the
failure mode phase 4 spent an entire step on, recurring one section
later in the same plan, which is the argument for the survey step
rather than against it. Corrected at source above.

**A5 and A6 -- declined.** Wave 1's new-third-party-import check
fires on an expanded first-party import
(`from audit.text.markdown import ...`) and its new-suppression
check fires on a pre-existing `# noqa: E402` riding along on a line
rewritten for an unrelated reason. Both are real limitations of
line-based greps and both were correctly triaged by the agent that
hit them. Declined rather than fixed: a grep that over-fires costs a
reviewer one glance, and tightening these to distinguish first-party
from third-party, or a new suppression from a moved one, would make
wave 1 harder to read and easier to make silently wrong. The
runbook's greps are deliberately blunt and the verdict column is
where the judgement goes.

**5a's commit, reviewed by the management session.** `4873e95` is
the one change in this phase no audit range contains, so nothing
else reads it. It was first recorded here as `4a7c4f2`, which was
the same work before this branch was rebased and is now a dead
object -- resolvable in the clone that wrote it and nowhere else.
That is the defect this plan's own `Merged` convention exists to
prevent, so the rule it implies is written down rather than left
as an embarrassment: a SHA measured on a branch is re-checked with
`git merge-base --is-ancestor <sha> origin/main` before it is
recorded, because a rebase silently invalidates every one already
written. Verified directly rather than from its subject line:
all sixteen `main...HEAD` occurrences became
`"${AUDIT_RANGE:-origin/main...HEAD}"`; every grep pattern and path
filter is byte-identical on both sides, so the "the runbook fix
changes what a check looks for" risk did not fire; and none of the
49 changed lines falls inside any of the file's eight
`<!-- shared-block: -->` regions, so the fleet-wide risk did not
fire either. The wave 2 brief prose survived the rewrapping intact.
`grep -nE '(^|[^/])main\.\.\.HEAD' PUSH-AUDIT.md` produces no
output, and `push-audit` -- the criterion that reads the file 5a
edited -- still passes.

Two notes on how 5a got there, recorded because this phase's whole
argument is that measurements are reproducible. It committed the two
`.vscode/` prune artifacts beyond its brief's file list, which is
the right action -- they are the state `REVIEWS.md` is generated
from -- but justified it by citing a test,
`test_reviews_md_is_reproducible_from_the_committed_state`, that
does not exist in this repository. And it briefly used
`--no-verify` on a throwaway commit before undoing it; 5a's own
commit is a single one that went through the hooks, so nothing
escaped, but the runbook sanctions `--no-verify` nowhere.

**Management checklist.** Wave 1 passed with `pre-commit run
--all-files` clean on a clean tree. Wave 2 findings are reviewed and
dispositioned above. The blocking findings are security findings and
are not fixed on this branch by decision 5; they are the findings
pull request, and the phase does not close until it merges. Every
shared-block version movement in the range was checked: `5b1fb74`
creates the block at v1, `ac34b76` takes it to v2, `fd0678c` to v3,
and `ff92357` is the one edit without a bump, accepted as W1 with
its fleet-wide consequence set out above. Generated files are
generated: `docs/audits/compliance.md` is absent from all six ranges
and was not hand-edited, and `REVIEWS.md` moved only through
`review-tracking.py prune`. Stale review marks were pruned and said
so -- 5a dropped `PUSH-AUDIT.md | mikal | 2026-09-09 | 30723ce58eff`,
which now needs a human to read that file again; it was not
re-stamped, because the mark attests that a person read that exact
content. Commit history is clean: three commits -- the runbook fix
and two that record this outcome -- with no fixups, no WIP and
nothing accidental. The branch was rebased onto `main`, which is
what moved 5a's SHA above.

**What the audit says about the runbook.** Four executed audits in
this repository have now each found something, which retires the
"the phase becomes ceremony" risk phase 3 was built to test. This
run is the first to find a defect in the runbook rather than in the
work under review, and the first whose most valuable output was an
agent correcting the brief it was given -- wave 2d on the detection
gap. Both are arguments for the wave 2 briefs being written against
what a plan actually changed rather than against the repository in
general, which was decision 4.

**Merged:** `1806a7c` (#154), carrying steps 5a to 5g and the audit
record, and `fc723f3` (#155), carrying the fixes the audit produced.
Decision 6 named the findings pull request as the carrier of this
cell, on the assumption that it would land second. It landed first,
and so could not name a commit that did not yet exist; the fallback
the step plan wrote for the no-findings case -- a follow-up commit --
carried it instead. A phase cannot name the commit that lands it, and
this is the last phase, so no later planning commit exists to do it.
That is what the fallback was for.

## Risks and mitigations

* **The audit finds nothing and the phase becomes ceremony.** This
  was the real risk, and phase 3 was the mitigation -- with a named
  measurement rather than an intention to review later. Retired: five
  executed audits, five that found something, two blocking defects in
  merged production code and three findings against the audit harness
  itself. Recorded in phase 3's decision 1.
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
* `plan-push-audit-phase` is at v3, and this repository's own
  master plans each record a landing commit for every merged phase,
  or say why no range is recoverable. **Met since 4b**: six of its
  eight plans that carry an audit phase already did, one of those
  with a single aggregate `Merged:` line rather than a per-phase
  one, which decision 8 of phase 4 accepts as recorded. The other
  two -- `PLAN-audit-compliance-split.md` and
  `PLAN-scope-coverage.md` -- carried the column with seven cells
  empty against phases that had landed when this was measured.
  Both are filled, and not by the same hand: step 4b filled
  `PLAN-audit-compliance-split.md`'s four in `1ee048d`, part of
  `fd0678c` (#113), all four naming `7843932`, while
  `PLAN-scope-coverage.md`'s were filled by that plan's own push
  audit as its phases landed -- `8b77b32` for phases 2 to 4, and
  `c24636a, 0759e46` for phase 5 in `a1a2635` (#111). Step 4f read
  both files rather than inferring this from 4b's pull request. The
  plans
  elsewhere in the fleet that still carry v1's retracted wording are
  backfilled in phase 4, which phase 3's decision 4 scheduled once
  decision 1 confirmed the phase is staying. Measured 2026-09-04 on
  the basis this plan's phase 4 backfills on -- the plan carries a
  push audit phase, which is narrower than the file-naming grep and
  much narrower than the phrase match the estimate used, and
  narrower again than the criterion's own scope: shakenfist nineteen
  plans need a landing commit; development, instar, ryll and
  kerbside need none; and divergulent's three incomplete plans need
  the phase as well as the record. Two of those figures did not
  survive the sweep: instar needed one cell and client-python-k3s,
  unlisted here, needed another, both for phases that landed after
  this measurement. Phase 4's survey records how each
  figure was reached and why several moved between drafts.
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
  were outside this criterion until phase 3 settled the scope;
  decision 3 puts them in, and phase 4 sweeps them. occystrap and
  sfui are excluded, for the reasons that decision records.
* `pre-commit run --all-files` passes in this repository.
* Phase 3 records, in this plan, what the executed audits found and
  what was decided about the phase remaining mandatory, and the
  `plan-audit-phase` criterion it decided on is registered, specified,
  tested and run across the fleet.

## Future work

* **No criterion reads the `Merged` column.** `plan-audit-phase`
  measures that the last phase names the runbook and nothing in
  `scripts/audit/` looks at the column at all, so a plan can record
  an empty column, a wrong SHA, or a commit that arrived under a
  merge and stay green. Phase 5 is the first consumer that would
  notice. A criterion that checks each recorded value is a merge
  commit or a `first..last` range is mechanical and would close it;
  phase 4 verifies the values in review instead, which is a
  one-time answer to a recurring question. Two further things wait
  on that same criterion. It is what would decide whether a landing
  record kept as Status-cell prose -- kerbside's
  `PLAN-proxy-dev-releases.md`, ryll's `PLAN-web-frontend.md` --
  must migrate to a `Merged` column, which phase 4's decision 8
  declines to settle. And after phase 4's decision 2 a repository
  can leave `plan-audit-phase` scope entirely by omitting a status
  column from its index, because `plan-index` requires a table and
  not a status column -- and a single plan can leave it by having
  one blank cell in an otherwise-compliant table. Nothing detects
  either today beyond the unjudged plans being named in the verdict
  every morning. Decision 7 of phase 4 records the narrower bucket
  rule that would close it and why it was not taken there.
* **The survey enumerated eight repositories; the fleet is
  twenty-one.** Step 4f's sweep found `uncalibrated-sextant` failing
  `plan-audit-phase` on five of five incomplete plans, with no
  `PUSH-AUDIT.md` either. Both were filed by the daily workflow on
  2026-09-04, before this plan's phase 4 survey was written, and
  neither appears anywhere in it: the phase 2 and phase 3 tables
  reach shakenfist, ryll, instar, kerbside, divergulent, occystrap,
  sfui and development, and stop. That is a hole in how the survey
  was scoped rather than a regression, and it is left here rather
  than closed in 4f because appending a phase to five plans in a
  repository this plan never examined is a sweep of its own, with a
  survey of its own. The next plan to widen this convention should
  take its repository list from `.github/workflows/consistency-audit.yml`,
  which is the fleet's actual roster, rather than from the set a
  previous phase happened to visit. `AGENTS.md` says of the runbook
  that it "is being made the last phase of every master plan [...]
  drop this qualifier once the sweep has landed". Phase 4 is not
  that sweep: it reached eight repositories of twenty-one, left
  uncalibrated-sextant failing and instar carrying a plan no sweep
  step covered. The qualifier stays until the widening described
  here lands, and the plan that does it is the one that should drop
  it.
* ~~**`development` has no `PLAN-TEMPLATE.md`**~~ -- struck. Phase
  4's survey found one on `main`, 23KB carrying nine shared blocks
  including `plan-push-audit-phase` at v2, so new plans here do
  inherit the phase. The bullet was stale.
* **Move the mechanical waves out of the runbook.** Wave 1 and the
  wave-2 sweep are grep and shell. They belong in a `tools/` script
  that pre-commit and CI both call, where they cannot be skipped and
  cost nothing. The judgment agents are the only part that needs a
  human trigger. kerbside has already written them --
  `tools/audit/wave1.sh`, `tools/audit/wave2-mechanical.sh` and
  `tools/audit/plan-range.sh` -- so the work here is a port rather
  than a design, but it is not a copy. Phase 5's decision 2 records
  why: `plan-range.sh` resolves its base branch against `develop`,
  which this repository does not have, and it derives a range by
  spanning from the first merge's parent to the last with a path
  filter. That is right for phases that land within days of each
  other and wrong for this plan, whose six diffs span three weeks
  with another plan's 13,000-line restructure between them --
  measured at 11,579 insertions for the span against 5,526 for the
  union. A port needs a union mode, a base branch read from the
  repository, and the ability to take a phase's range as well as
  its merge, since phase 2 of this plan landed partly direct.
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
* **Check a canonical shared block's text against its own version.**
  Phase 5's wave 2d found that an unbumped wording change is not
  undetected, which is what everyone assumed -- it is
  *misattributed*. `validate_shared_blocks`
  (`scripts/audit/checks/shared_blocks.py:109-118`) compares
  versions and wording in an `if`/`elif`, so when the versions match
  it compares text and every repository still carrying the old
  wording is told its copy has **drifted**: accused of editing a
  block it never touched, and pointed at a local edit that does not
  exist, when the fix is to pull the canonical file. Nothing
  compares a canonical block's text against the version in its own
  marker. A criterion that did would name the real fault -- an
  unbumped canonical block -- and would also catch an edit to a
  block no repository has adopted yet, which the drift path is
  structurally blind to because every repository reports "missing"
  either way. `ff92357` is the worked example, recorded as W1 in
  phase 5's Outcome.
* **The range rule reaches one of the eight runbooks.** Phase 5's
  step 5a fixed `main...HEAD` in this repository's `PUSH-AUDIT.md`.
  Seven other repositories carry their own copy, and the range rule
  is prose in each rather than one of the files in
  `templates/shared-blocks/` -- `push-audit` verifies only that the
  eight required blocks are present and current
  (`PUSH_AUDIT_BLOCKS`, `scripts/audit/checks/plans.py:104-110`).
  So the other seven still say every diff command is against
  `main...HEAD`, and each will audit an empty diff the first time it
  is pointed at landed work, which reads as a clean audit rather
  than as no audit. Two shapes: sweep the seven, or promote the
  range rule to a shared block so `push-audit` carries it to the
  fleet the way it carries the other eight. The second is the reason
  this is a Future work item rather than a seven-repository sweep
  appended to phase 5.

## Back brief

Before phase 2 begins, the management session confirms with Mikal:
the canonical wording of the shared block, and `development`'s
`PUSH-AUDIT.md`. Both are cheap to propose and expensive to redo
across thirty-six plan files and eight repositories.

[#155]: https://github.com/shakenfist/development/pull/155
