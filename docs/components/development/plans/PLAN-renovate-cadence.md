# Renovate cadence

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every week
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a weekly run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

Two things make planning here different from planning in a
repository that holds a product, and both should shape any plan
written from this template:

* **The blast radius is other people's repositories.** The weekly
  workflow files and closes GitHub issues fleet-wide. A change
  that is merely wrong does not produce a red build; it produces
  issues in ten repositories, or silently closes ones that should
  have stayed open. Always pass `--dry-run` when running
  `audit-manage-issues.py` by hand.
* **This repository is in its own audit matrix.** A standard we
  exempt ourselves from is a standard we stop noticing the cost
  of. A change to a criterion is a change we are measured against
  at the next weekly run, so a plan should say how many repositories --
  including this one -- it newly fails.

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

**In this repository.** Plans here keep their phases as sections
inside the master plan rather than as separate phase files, and
the Execution table's `Plan` column is dropped accordingly;
`docs/plans/index.md` says so. The shared convention above is the
fleet default, and a plan large enough to want phase files should
use them rather than argue with the block.

## Situation

The CI cluster is busy enough that queue time is a velocity cost,
and Renovate looks like a large part of it. Measured on 2026-10-03
with `gh`, it is a real part but not most of it.

**Pull requests.** Renovate opened about 227 of the roughly 1,030
pull requests created across the organisation between 2026-09-03
and 2026-10-03. Half of them bumped one dependency, and that
dependency is Renovate itself:

| Renovate pull requests, 30 days | Count |
|---|---|
| `renovatebot/github-action` | 111 |
| `stbenjam/skillsaw` pre-commit hook | 21 |
| Other pre-commit hooks | ~6 |
| Other GitHub Actions | 0 |
| Runtime dependencies (pip, cargo, npm, docker, lock file maintenance) | ~85 |

The last three rows split what was first counted as "everything
else, about 95". They were re-counted on 2026-10-10 from the titles
of the window's pull requests by `shakenfist-bot`, so they are
approximate. The 14 repositories that received a pre-commit hook
update in the window are the ones a monthly CI-tooling group would
reach. The runtime updates fall on 82 distinct repository,
dependency and ISO week triples, and on 62 distinct repository and
dependency pairs.

`renovatebot/github-action` is released several times a week, and
every repository runs its own copy of
`templates/renovate/renovate.yml`, so each release becomes about 16
identical pull requests. Each one runs that repository's full CI
and then its merge queue.

**CI time.** These figures are wall-clock run durations, so they
include queueing. They count each Renovate pull request's own runs
plus its merge-queue runs on `gh-readonly-queue/...` branches. The
runs API returns at most 1,000 runs, which is why the busiest
repositories have shorter windows:

| Repository | Renovate share of CI time | Window from |
|---|---|---|
| occystrap | 21.6% | 2026-09-03 |
| ryll | 19.8% | 2026-09-03 |
| kerbside | 16.9% | 2026-09-19 |
| kerbside-patches | 10.4% | 2026-09-23 |
| shakenfist | 9.1% | 2026-09-25 |
| instar | 8.4% | 2026-09-06 |

**The Renovate runs themselves.** The template schedules
`renovate.yml` hourly on the `static` runner. In the week to
2026-10-03, shakenfist, ryll, instar and occystrap each ran it 34 to
36 times (about five a day rather than 24), each run taking a
median of 0.5 to 1 minute. This is small next to the pull-request
CI, but it is roughly 85 runs a day across the fleet, all on one
runner label.

Why only five of the 24 hourly crons run is not runner contention.
On 2026-10-09 the last 100 runs in `ryll` and in `shakenfist` were
all `schedule` events and all succeeded; none was cancelled, so no
run was displaced from the `renovate` concurrency group while it
waited for a runner. Each run started the moment it was created, but
it was created anywhere from `:00` to `:59` past the hour. GitHub
delays scheduled events under load, documents the start of the hour
as the busiest time, and drops events when the load is high enough.
The template's `cron: '0 * * * *'` sits exactly there. The median gap
between runs was 4 hours 47 minutes and the longest 9 hours 35
minutes, and that gap is the floor on how soon Renovate can open a
security pull request.

**"Copied verbatim" is not what the fleet holds.**
`templates/renovate/README.md` says `renovate.yml` "is copied
verbatim". Read through the GitHub API on 2026-10-03, the 17 live
copies come in 14 different versions, and they pin three different
action versions (11 at v46.3.6, 2 at v46.3.5, 4 at v46.3.4).
`renovate.json` is a per-repository adaptation by design, so the
fleet-wide policy (schedule, `minimumReleaseAge`, the no-automerge
rule, the pre-commit manager) is restated in every copy. Changing
that policy is therefore a sweep of about 17 pull requests.

**Security updates already flow.** Renovate's token can read
Dependabot alerts: at least ten `[SECURITY]` pull requests were
opened in 2026 (for example kerbside's cryptography bump on
2026-08-05), and shakenfist has 30 Dependabot alerts with none
open. Renovate's documented defaults for `vulnerabilityAlerts` drop
both the schedule and `minimumReleaseAge`, so a security fix should
not wait for either. This plan relies on that, and phase 1 checks
it against the Renovate version the fleet actually runs, because
tightening the schedule is only safe if it holds.

**A plan already in flight raises the stakes.**
`PLAN-workflow-ref-trust.md` (development#209, merged 2026-10-03)
phase 3 will pin every third-party action to a commit sha, with
Renovate's `helpers:pinGitHubActionDigests` keeping the pins
current. The fleet has 540 third-party `uses:` references. Without
grouping, every upstream action release becomes a digest pull
request in each repository that uses it, and the churn this plan
removes comes straight back. That plan's phase 3 also edits
`renovate.json` in every repository, which is the same sweep this
plan makes.

What the audit holds the fleet to today
(`scripts/audit/checks/packaging.py`, `Renovate.run`): both
`.github/workflows/renovate.yml` and `renovate.json` must exist, and
`renovate_manages_pre_commit` must find the pre-commit manager
enabled in one of three local forms. That function reads only the
local file, so it does not recognise a manager enabled through a
shared preset.

## Mission and problem statement

Cut the CI load Renovate generates by about half, without making
security fixes slower and without losing the ability to tell which
update broke a build. The measure is the number of pull requests
Renovate opens across the organisation in 30 days, because each one
costs a full CI run plus a merge-queue run: 227 in the Situation's
window, and the target is 100 or fewer. The target is derived from
the Situation's breakdown rather than chosen: about 4 bumps of
`renovatebot/github-action` in `renovate-fleet.yml` (weekly, see
phase 2), about 14 monthly CI-tooling pull requests, and about 82
runtime pull requests, which stay one per pull request and which a
weekly window barely folds. Runtime dependencies are therefore
most of what remains, and the lever that would cut further is
grouping them, which this plan declines for the bisection reason
below. The Renovate
share of CI time is recorded alongside it, but it is not the target,
because the runs API's 1,000-run cap gives each repository a
different window. Three changes do this:

1. **Run one Renovate instead of seventeen.** A single scheduled
   workflow in this repository runs Renovate across the
   organisation. The per-repository `renovate.yml` copies go away,
   and with them about 111 of the 227 monthly pull requests and the
   roughly 85 daily Renovate runs on `static`.
2. **Put the fleet policy in one shared preset**, so that each
   repository's `renovate.json` holds only what is particular to it
   (lockstep groups, Python constraints, range strategy). The
   preset sets tiered schedules: security at any time, ordinary
   updates weekly, majors and CI tooling monthly.
3. **Group by risk.** GitHub Actions and pre-commit hook updates
   (CI tooling: low risk, not shipped, and the failing job names
   the culprit) go into one monthly pull request per repository.
   Runtime dependencies and majors stay one per pull request,
   because that is where being able to bisect a failure matters.

"Without making security fixes slower" holds for fixes that come
with an advisory, because only `vulnerabilityAlerts` takes the
immediate path, and it is fed by Dependabot alerts. A fixed release
of a GitHub Action or a pre-commit hook that carries no advisory, or
one in an ecosystem Dependabot does not alert on, is an ordinary
update to Renovate and waits for the monthly CI-tooling window: up
to a month, against up to about ten hours today. Pre-commit hooks
are the likely gap, since a hook is a git repository rather than a
package in an advisory ecosystem. That is the accepted worst case,
and the override is manual: the dependency dashboard lists updates
awaiting their schedule, and ticking one opens its pull request on
the next run. Step 1a records which of the fleet's ecosystems
Dependabot actually covers, so the gap is stated rather than
assumed.

Out of scope:

- Automerge. Fleet policy is that every update is reviewed, and
  this plan keeps it.
- The pinning policy itself, which `PLAN-workflow-ref-trust.md`
  owns. This plan provides the grouping that policy needs, and
  its phase 3 must land before that plan's step 3b.
- Turning other copied workflow templates into shared workflows.
  Renovate is a special case: a reusable workflow would still run
  once per repository, while a central runner runs once in total.
  The general question is recorded under Future work.

## Open questions

1. **Where does `RENOVATE_TOKEN` live, and what can it reach?** The
   central runner needs a token that can push branches and open pull
   requests in every repository Renovate manages, and read
   Dependabot alerts in each one. Listing organisation secrets needs
   `admin:org`, which the planning session did not have. *Default:*
   assume it is the `shakenfist-bot` personal access token with
   organisation-wide access. Phase 2's cutover confirms the read
   path with a `RENOVATE_DRY_RUN=lookup` run and the write path
   separately, because a lookup dry run creates no branch and opens
   no pull request, so an under-scoped token passes it. Both happen
   before any per-repository workflow is disabled.
2. **Which timezone do schedules use?** Renovate reads schedules in
   UTC unless `timezone` is set, and today's `after 9pm` is UTC.
   *Default:* the operator names one, and until then the preset sets
   `"timezone": "UTC"` explicitly so the choice is visible.
3. **Cadence.** *Default:* ordinary updates `on saturday`, majors
   and the CI-tooling group `on the first day of the month`, and
   `vulnerabilityAlerts` at any time. These are easy to change later
   because they live in one preset. Each window is a whole day on
   purpose: Renovate acts only when a run lands inside the window,
   and the Situation measured gaps of up to 9 hours 35 minutes
   between scheduled runs, so a six-hour window could see no run at
   all and slip a week's updates silently to the next.
4. **Where does the central runner live?** *Default:* this
   repository, beside `consistency-audit.yml`. This repository
   already holds the workflows that act on the whole fleet, while
   `actions` holds code that runs inside other repositories' jobs.
5. **Is OSV worth adding as a second source of security alerts?**
   Dependabot alerts already produce security pull requests (see
   Situation). `osvVulnerabilityAlerts` would add the OSV database
   as a backstop. *Default:* no, unless phase 1 finds an ecosystem
   the fleet uses that Dependabot does not cover.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Shared preset and its validation | Not started | |
| 2. Central runner and cutover | Not started | |
| 3. Fleet sweep and audit change | Not started | |
| 4. Re-measure | Not started | |
| 5. Push audit | Not started | |

The order matters in two places:

* **Phase 2 cuts over before phase 3 deletes anything.** Two
  Renovate instances working on the same repository race on branch
  creation; the comment in `templates/renovate/renovate.yml`
  records exactly that failure. Phase 2 therefore disables each
  per-repository workflow with `gh workflow disable`, which takes
  effect at once, needs no pull request and is undone by `gh
  workflow enable`, before the central runner is turned on. Phase 3
  then deletes the disabled files and moves each `renovate.json`
  onto the preset in a single pull request per repository, so the
  sweep costs one CI run per repository rather than two.
* **This plan's phase 3 must land before
  `PLAN-workflow-ref-trust.md` step 3b.** Phase 1 only publishes the
  preset; apart from this repository, nothing extends it until phase
  3's sweep. A line added to the preset before then reaches one
  repository, and the fleet's digest pull requests would arrive one
  per release per repository -- the churn this plan removes. Once
  the sweep has merged, that plan's switch to digest pinning is a
  one-line addition to the preset rather than a second fleet sweep,
  and its digest updates fall into the monthly CI-tooling group.
  development#209 has merged, so the in-repository record of the
  dependency is that plan's coordination paragraph, which this plan's
  pull request rewrote to name phase 3. (The operator's
  `plan-blockers` tool carries a matching note, but it lives outside
  this repository.)

Only phase 1 carries a step table. Phases 2 to 4 get theirs when
each is planned, from the scope written here, because what phase 1
finds (the limits, the token, the schedule semantics) changes their
briefs. Phase 3's audit-change brief in particular names the
packaging test module, the converged-templates test that the
deletions break, the spec rewrite, and `--dry-run` for any
issue-filing run.

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

**In this repository.** The same term is written twice: once in
this plan's own phase table, and once in the row the plan carries
in `docs/plans/index.md`. The index row is the whole-plan status,
so it only reaches `Complete` once every phase has been
completed, abandoned or superseded. The `plan-index` criterion
reads that table, and this repository is inside its own audit
matrix, so a status that drifts out of the vocabulary fails our
own tooling before it fails anybody else's.

<!-- shared-block: plan-push-audit-phase v3 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did.
<!-- shared-block-end -->

**In this repository.** `PUSH-AUDIT.md` exists at the repository
root and is referenced from `AGENTS.md`, so the final phase runs
it rather than explaining its absence. Note that every diff
command in it is written against `main...HEAD`: a stale local
`main` silently widens the audit to unrelated history, so fetch
before starting, or read it as `origin/main...HEAD`.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

**In this repository.** The plan index is `docs/plans/index.md`.
`review-tracking-tests` deliberately does not assert the `REVIEWS.md`
header count, so a phase that adds or removes an in-scope file needs
no regeneration commit; `prune-reviews` corrects the count on the
next push to main.

### Phase 1: shared preset and its validation

Planning effort: high. This phase sets the policy every repository
will inherit, and it is where the security-schedule assumption
either holds or does not.

**Scope.** Add a Renovate preset to this repository that holds the
fleet policy: `gitAuthor`, `assignees`, `dependencyDashboard`,
`rollbackPrs`, `minimumReleaseAge`, the pre-commit manager, the
schedules from open question 3, `timezone`, `prHourlyLimit` and
`prConcurrentLimit` (set explicitly, from step 1a), the no-automerge
rule, and the CI-tooling group (`matchManagers: ["github-actions",
"pre-commit"]`, all update types including `digest`, monthly).
The preset is `renovate-fleet.json` at the root of this repository,
and every repository references it as
`"extends": ["local>shakenfist/development:renovate-fleet"]`. Steps
1b and 1c, and phase 3's audit change, all work to that exact path
and string. This repository's own `github-actions` file patterns for
`templates/` stay in its local `renovate.json`, because they apply
only here.

Renovate reads a `local>` preset from the default branch of the
repository that holds it, so a change to the preset cannot be tried
from a pull request branch: it takes effect for the whole fleet when
it merges. Pre-commit's validator is the only check before that, and
phase 2's lookup dry run is the first live read of the preset.

The preset also carries per-package `versioning` rules for pre-commit
hooks whose tags are not semver, starting with
`shellcheck-py/shellcheck-py`. Upstream tags four components
(`v0.11.0.1` is shellcheck 0.11.0, packaging revision 1), which
the github-tags datasource behind the pre-commit manager cannot
parse. Renovate cannot resolve the current pin at all, so it
reports "Can't find version matching v0.11.0.1" on the dependency
dashboard and never proposes an update. On 2026-10-09 that warning
was on the dashboards of `development`, `instar`, `actions` and
`hunkydory` (pinned to `v0.11.0.1`) and `ryll` (pinned to
`v0.10.0.1`). About half the fleet is still on `v0.10.0.1`, so the
broken lookup is holding back a real bump. `kerbside` already
solved this locally, and its rule moves into the preset unchanged.
A versioning rule is fleet policy rather than a repository
adaptation: it is either right for the package or wrong for it, and
it is never right for one repository and wrong for another.

Steps:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Read Renovate's documentation for the `renovatebot/github-action` version `renovate-fleet.yml` will pin (the newest release when phase 2 starts; `templates/renovate/renovate.yml` pinned v46.3.7 on 2026-10-10, while the live copies the Situation measured pinned v46.3.4 to v46.3.6), and the Renovate version that action release runs, and confirm what `vulnerabilityAlerts` does with `schedule`, `minimumReleaseAge` and grouping by default. Report this with citations; do not assume. If security fixes would wait for the weekly window or join the CI-tooling group, the preset must override that explicitly. Also check whether every ecosystem the fleet uses (pip, cargo, npm, docker, github-actions, pre-commit) is covered by Dependabot alerts, which answers open question 5, and write the github-actions and pre-commit answers into the Mission's security-latency paragraph. Finally, confirm the defaults for `prHourlyLimit` and `prConcurrentLimit` in that version (recent versions appear to default to 2 and 10, but verify), and whether `vulnerabilityAlerts` pull requests are exempt from them. With a once-a-week window those limits decide how much of a week's backlog opens in it, so step 1b sets both explicitly in the preset rather than inheriting a default; record the values chosen and why. |
| 1b | medium | sonnet | none | Write the preset as `renovate-fleet.json` at the repository root, and reduce `templates/renovate/renovate.json` to `"extends": ["local>shakenfist/development:renovate-fleet"]` plus the repository-specific sections the README describes. Add Renovate's config validator (`renovatebot/pre-commit-hooks`, `renovate-config-validator`) to `.pre-commit-config.yaml`. Its default `files:` is `(^|/).?renovate(?:rc)?(?:\.json[c5]?)?$`, which matches both `renovate.json` files but not `renovate-fleet.json`, so the preset would be skipped and the hook would still pass: override `files:` to cover all three, then break the preset on purpose and confirm the hook fails and names it. The hook is `language: node` with `language_version: lts`, so pre-commit fetches Node and Renovate on first run; confirm `ci.yml`'s `lint-and-test` job on `[self-hosted, static]` runs it, not just a workstation. Reduce this repository's own root `renovate.json` the same way, to `$schema`, the `extends` entry and the `github-actions` `managerFilePatterns` for `templates/` with its description; every other key it holds today (`gitAuthor`, `assignees`, `dependencyDashboard`, `schedule`, `pre-commit`, the no-automerge rule, `minimumReleaseAge`, `rollbackPrs`) restates the preset and goes. This makes `development` the preset's first live user from the moment phase 1 merges, still run by its own `renovate.yml`, so its dependency dashboard is the canary for the preset's schedules before the rest of the fleet adopts it. Step 1c must land in the same pull request, or the `Renovate` check fails this repository on the local `pre-commit` key that has just been removed. Copy `kerbside`'s shellcheck-py rule into the preset verbatim, description included: `"matchPackageNames": ["shellcheck-py/shellcheck-py"]`, `"versioning": "regex:^v?(?<major>\\d+)\\.(?<minor>\\d+)\\.(?<patch>\\d+)\\.(?<build>\\d+)$"`. Do not rename the fourth group to `revision`. Renovate's regex versioning (`lib/modules/versioning/regex/index.ts`) only appends `revision` to the comparison array when `build` also matched, so with that name every tag would truncate to three components and a packaging-only release would never be proposed. Keep the `$` anchor, so that the `v0.11.0.1-1` re-tag, which has no matching PyPI release, is ignored rather than proposed. Do not copy `kerbside`'s `shellcheck` group: it pairs the hook with a `tox.ini` pin that only `kerbside` has. |
| 1c | medium | sonnet | none | Teach `renovate_manages_pre_commit` (`scripts/audit/checks/packaging.py:47`) to accept `local>shakenfist/development:renovate-fleet` in `extends` as a fourth enabling form (the existing `extends` form matches only entries ending in `:enablePreCommit`), with tests in the existing packaging test module covering pass, fail and the preset form. The function hard-codes `renovate.json` under `repo_path` (line 54), so first factor its three existing checks on the parsed dict (`pre-commit.enabled`, `enabledManagers`, an `extends` entry ending `:enablePreCommit`) into a helper that takes the config dict, and have the function call it; the new preset-name form stays in the function, not the helper, so the helper cannot pass the preset by trusting its own name. Add one more test that loads the real `renovate-fleet.json` from the repository root with `json.load` and asserts the helper accepts it on its own: the special case trusts the preset by name, and a `local>` preset takes effect fleet-wide on merge, so without this test an edit that dropped the manager from the preset would pass every repository silently. Update `docs/audits/renovate.md` and `templates/renovate/README.md` to describe the preset, and correct the README's claim that `renovate.yml` is copied verbatim. |

### Phase 2: central runner and cutover

Planning effort: high. This phase is the only one with live,
fleet-wide effect, and the cutover order is what prevents duplicate
pull requests.

**Scope.** Add `.github/workflows/renovate-fleet.yml` to this
repository on `[self-hosted, static]`, hourly, with
`concurrency: renovate-fleet` and no cancel-in-progress (the reasoning
in the template's comment carries over). Schedule it off the hour
(for example `cron: '23 * * * *'`), not at the template's `0 * * *
*`: the Situation section shows GitHub delaying and dropping
top-of-hour events, and one runner instead of seventeen means a
dropped run now delays every repository at once. An off-hour minute
is GitHub's documented mitigation, not a guarantee, which is why the
watch below counts the runs. Raise `timeout-minutes` to match a
serial pass over about 20 repositories; measure a dry run first
rather than guessing.

The runner's own configuration is a global (self-hosted) Renovate
configuration file, `renovate-runner.json` at the root of this
repository, passed to the action through its `configurationFile`
input; the workflow therefore checks out this repository's default
branch and nothing else. The file holds `autodiscover: true`, an
`autodiscoverFilter` of `["shakenfist/*"]`, `requireConfig:
"required"`, `onboarding: false` and the temporary package rule
below. Together the first four mean a repository is managed only if
it carries a `renovate.json`, and the runner never opens an
onboarding pull request in a repository that does not. The workflow
sets only the token, the log level, and two `workflow_dispatch`
inputs the cutover uses: a dry-run mode passed as
`RENOVATE_DRY_RUN`, and a narrower filter passed as
`RENOVATE_AUTODISCOVER_FILTER`. Confirm in the Renovate version the
action runs that an environment variable takes precedence over the
file, which is the documented order, before relying on the filter
input. Add `renovate-runner.json` to the validator hook's `files:`
from step 1b, and break it on purpose to prove the hook reads it.
Flagged uncertainty: `renovate-config-validator` validates files
passed as arguments as repository configuration, and global-only
options such as `autodiscoverFilter` may be reported as invalid
there; if they are, validate the file as global configuration
instead (the validator reads the file named by
`RENOVATE_CONFIG_FILE` as global), and record which invocation the
hook uses.

The workflow holds the one copy of a token that can push to every
repository, so it gets least privilege: top-level `permissions:
contents: read` (Renovate acts through `RENOVATE_TOKEN`, not
`GITHUB_TOKEN`), only `schedule` and `workflow_dispatch` triggers,
and no checkout of any ref but this repository's default branch.
The runner label matters as much: `[self-hosted, static]` also runs
`ci.yml`'s `lint-and-test` on pull-request code, so if `static`
runners are persistent rather than ephemeral, a pull request's job
could leave something behind (a modified tool cache, a background
process) for the next Renovate job to pick up along with the token.
The per-repository copies already carry that exposure, but this
phase puts the token's whole reach behind one job. Find out whether
`static` runners are ephemeral and record the answer here; if they
are not, run `renovate-fleet.yml` on a dedicated label, or record
the residual risk as accepted, before the cutover.

`renovate-runner.json` also carries a temporary
`packageRules` entry, `matchFileNames:
[".github/workflows/renovate.yml", "templates/renovate/renovate.yml"]`
with `enabled: false`. Between this phase and phase 3 the disabled
per-repository workflow files are still in the tree, and without the
rule every `renovatebot/github-action` release would open about 16
pull requests bumping workflows that no longer run -- the churn this
plan exists to remove. The second path is this repository's
template, which its `renovate.json` scans through the `templates/`
file pattern. The rule names files rather than disabling the
`renovatebot/github-action` package, because `renovate-fleet.yml`
uses that action too and must keep being bumped. Phase 3 deletes the
rule once no managed repository carries either file. A global
`packageRules` entry is a default that each repository's resolved
configuration (the preset, then its own `renovate.json`) is merged
on top of: the arrays are concatenated, and a later rule that
matches the same dependency and sets `enabled` would win. No rule in
the fleet does that today, and cutover step 1 checks that the
temporary rule actually takes effect rather than assuming it.

`renovate-fleet.yml` is the most privileged workflow in the fleet,
and the action that receives the token should not wait a month for
a fix that carries no advisory. This repository's own
`renovate.json` therefore gives `renovatebot/github-action` in
`.github/workflows/renovate-fleet.yml` (`matchFileNames` and
`matchPackageNames` together) its own `groupName` and the weekly
schedule, overriding the preset's CI-tooling group. That rule is
local because the file exists only here. It costs about four pull
requests a month, at the action's current release rate.

Cutover, in this order:

1. Dry run the central workflow with `RENOVATE_DRY_RUN=lookup` from
   `workflow_dispatch`. Confirm it discovers the expected
   repositories, that the token can reach each one and read its
   Dependabot alerts (open question 1), and that the
   `renovatebot/github-action` dependency in each repository's
   `renovate.yml`, and in this repository's template, is reported as
   disabled by the temporary rule. This proves the read path only.
2. Prove the write path before anything is disabled fleet-wide. The
   workflow's first step, before Renovate starts, calls `GET
   /repos/shakenfist/<repo>` with `RENOVATE_TOKEN` for every
   repository the dry run discovered and fails the job, naming the
   repository, if `permissions.push` is not true; record the result
   here. Then canary on `development`: `gh workflow disable
   renovate.yml` here (its copy is in the `renovate` concurrency
   group rather than `renovate-fleet`, so nothing else stops it
   racing the central runner), tick one held update on this
   repository's dependency dashboard, and dispatch the central
   workflow for real with the filter input set to
   `shakenfist/development`. Confirm it pushed that update's branch,
   opened or updated its pull request, and rewrote the dashboard.
3. `gh workflow disable renovate.yml` in every other managed
   repository. Record the full list, `development` included, in this
   section, so the rollback is mechanical.
4. Enable the central runner's schedule and watch a full day:
   dependency dashboards update, no duplicate branches appear, and
   any open Renovate pull requests are adopted rather than reopened.
   Record here how many times `renovate-fleet.yml` ran that day,
   each run's duration, and the longest gap between runs, against
   the Situation's median of 4 hours 47 minutes and longest of 9
   hours 35 minutes. A pass that approaches 60 minutes reduces the
   cadence silently: GitHub keeps one pending run per concurrency
   group and cancels the older one, so superseded runs count
   against the success criterion that the runner runs at least as
   often as the copies did. If the central runner is not clearly
   better, stop and investigate before phase 3: the plan's promise
   on security latency rests on Renovate actually running.

Rollback is `gh workflow enable` per repository and disabling the
central workflow. Nothing in this phase deletes a file elsewhere in
the fleet.

### Phase 3: fleet sweep and audit change

Planning effort: high for the audit change, medium for the sweep.
The audit change reverses what the `Renovate` criterion means -- a
file it requires today becomes a failure -- and files issues across
the fleet at the next weekly audit run (Sunday 18:00 UTC), which is
high effort by this
repository's own rule (see Planning effort below). The sweep that
follows is the mechanical `consistency-fix` pattern the fleet
already runs. Plan them as two steps, and land the audit change
first.

**Scope.** In this repository: the `Renovate` check now requires
`renovate.json` to extend `local>shakenfist/development:renovate-fleet`,
and treats a `.github/workflows/renovate.yml` as a failure, because
if it were re-enabled it would race the central runner. Tests in the
packaging test module cover both new failures (the workflow file
present, the `extends` missing) as well as the pass. The issue title
`Renovate` does not change, since it is the fleet-wide idempotency
key. Rewrite `docs/audits/renovate.md` with the check: its "What we
check" list and rationale describe the required `extends` and the
forbidden workflow file, where today they say the workflow must
exist, runs hourly and is copied verbatim. Specifications are
written for human review and nothing regenerates them, so a spec
left alone would contradict the check and every issue it files. The
rewrite stales the spec's review mark in `REVIEWS.md`; that is
expected, and per the phase-landing rules above it is left for
`prune-reviews` rather than edited here. Delete
`templates/renovate/renovate.yml` and this repository's own
`.github/workflows/renovate.yml`, and remove the template's row
from the template README. Deleting those two files breaks
`ConvergedTemplatesTest.test_this_repository_runs_renovate_verbatim`
in `scripts/tests/test_registry.py`, which reads both and asserts
they are byte-identical, so `pre-commit run --all-files` fails in
the same pull request unless the test goes with them. Do not stop at
that one: run `git grep -n 'renovate\.yml'` and settle every hit
outside `docs/plans/`. On 2026-10-10 the others were the
`ConvergedTemplatesTest` docstring, the actionlint comment in
`.pre-commit-config.yaml` that cites `renovate.yml` and counts "the
ten copy-directly templates", the supporting-workflows list in
`ARCHITECTURE.md` (which should name `renovate-fleet.yml` from
phase 2 instead), the `renovate.yml` fixture in the packaging test
module, and `docs/audits/renovate.md` itself; the mention in
`docs/audits/scheduled-workflow-health.md` is history and stays.
The audit change lands first, and the
`Renovate` issues it files at the next weekly run are the sweep's
work queue. That is deliberate: it is the fleet's issue-driven
`consistency-fix` pattern, and a transitional check that accepted
both forms would spare about 17 issues at the price of letting the
sweep stall unnoticed. Before the audit change merges, preview its
verdicts against fresh clones of the fleet with `audit-check.py`
and `audit-manage-issues.py --dry-run`, as
`docs/consistency-audits.md` describes, and record here that the
count matched the one below. A `workflow_dispatch` of
`consistency-audit.yml` has no dry-run mode -- it files issues for
real -- so it is only for starting the sweep after the merge
without waiting for Sunday.

In each managed repository, one pull request: delete
`.github/workflows/renovate.yml`, and reduce `renovate.json` to
`extends` plus the repository's own rules. Keep every lockstep
group, every `constraints.python` and every `rangeStrategy`.
Lockstep groups stay local throughout this plan, because the
`renovate-lockstep-groups` criterion (`RenovateLockstepGroups` in
`scripts/audit/checks/packaging.py`) reads only the local file and
would report a group moved into the preset as missing; promoting
them is under Future work. Delete only rules that restate the
preset. Once every sweep pull request has merged, remove the
runner's temporary `renovate.yml` package rule from phase 2. Two repositories need extra care:
`instar` (its custom manager for `cargo install` pins and its frozen
release base image) and `shakenfist` (the most groups). In
`kerbside`, delete the shellcheck-py `versioning` rule, which now
restates the preset, but keep its `shellcheck` group and its
`tox.ini` custom manager, which are repository-specific. State in
this section how many repositories the audit change newly fails:
every managed repository until its sweep pull request merges,
except `development`, which moved onto the preset in phase 1 and
loses its own `renovate.yml` in the audit change's pull request, so
it passes from the start.

Once the sweep has merged, the preset's schedules govern the fleet
for the first time. Watch the first Saturday after that and record
here that the weekly window got runs, and that the week's pending
updates opened or were visibly held by the preset's pull request
limits: phase 2's daily run count does not show whether a one-day
window was hit.

When no copy of `renovate.yml` remains, `RENOVATE_TOKEN` is needed
only by `renovate-fleet.yml`. If it is an organisation secret
(open question 1), restrict its repository access to
`shakenfist/development`; if it is a set of per-repository secrets,
delete the others. Either way, record which, so the credential's
exposure drops from 17 repositories to one.

### Phase 4: re-measure

Planning effort: medium.

**Scope.** Thirty days after phase 3 completes, repeat the
Situation section's measurements: Renovate pull request count by
package and by the Situation table's rows, Renovate share of CI time
including merge-queue runs, and Renovate runs on `static` per day
with the longest gap between them. Record before and after here, and
say whether the Mission's target -- 100 or fewer Renovate pull
requests in 30 days, down from 227 -- was met, and if not, which row
of the Mission's derivation missed its estimate. Record also the
latency of any `[SECURITY]` pull request opened in the window,
measured from the advisory's publication, and whether any
repository's dependency dashboard showed updates held by
`prHourlyLimit` or `prConcurrentLimit`, since a held update lowers
the count without being a saving. If grouping has made a failure
hard to attribute, say so here and adjust the preset; that is the
trade-off the operator asked to keep visible.

### Phase 5: push audit

Run `PUSH-AUDIT.md` over the union of the `Merged` ranges of
phases 1 to 3 in this repository. Phase 3's per-repository sweep
pull requests each run their own repository's push audit, where one
exists, as they land; cite those audits here rather than re-running
them.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

**In this repository.** High effort is anything that changes what
a criterion means, anything that touches the scheduler in
`audit-check.py`, and anything that reaches
`audit-manage-issues.py` -- those decide what the fleet is held
to and what lands in other people's issue trackers. Medium effort
covers adding a criterion that follows the shape of an existing
one, a documentation sweep, or a template change with a worked
example already in the tree.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

**In this repository.** A worked brief: instead of "add a check
that plans are indexed", write "add a `PlanIndex` class to
`scripts/audit/checks/plans.py` declaring the id `plan-index`, its
`spec` and its `issue_title` as class attributes and implementing
`run(repo)`, register the instance in `CHECKS` in
`scripts/audit/registry.py` beside the other plan checks, write
`docs/audits/plan-index.md` following the structure in
`docs/audits/README.md` and linking to `compliance.md#plan-index`,
add the file to `docs/audits/README.md`, add its lines to
`FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
`scripts/tests/test_metadata.py`, and add tests to
`scripts/tests/test_plans.py` covering pass, fail and
not-applicable." A brief that names four of the criterion's five
files is the characteristic defect here, and the frozen tables are
the one most often left out.

A check that does not apply reports
`not_applicable` with a reason rather than being omitted, because
an omitted check renders as `unknown`.

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

**In this repository.** The project-specific checks referred to
above are:

- [ ] `pre-commit run --all-files` passes. It runs actionlint,
      shellcheck, flake8, skillsaw and all five test suites, and
      `ci.yml` runs the same command on every pull request.
- [ ] `python3 scripts/audit-check.py --repo-path . --repo-name
      development` still reports what it reported before the
      change, or the plan says why the verdict moved.
- [ ] If the change touches issue filing, it was exercised with
      `--dry-run` only.

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* Renovate runs on an hourly schedule for the whole fleet from this
  repository, and no managed repository carries
  `.github/workflows/renovate.yml`.
* Every managed `renovate.json` extends the shared preset and holds
  only repository-specific rules, and `renovate-fleet.json` passes
  `renovate-config-validator` in pre-commit, which provably checks it:
  a deliberately broken preset fails the hook.
* No managed repository's dependency dashboard reports "Can't find
  version matching" for `shellcheck-py/shellcheck-py`, and the
  repositories pinned to `v0.10.0.1` have been offered the bump.
* `[SECURITY]` pull requests still open outside the weekly window,
  measured in phase 4.
* Phase 4 records the before and after numbers, and Renovate opened
  100 or fewer pull requests across the organisation in its 30-day
  window, down from 227.
* The central runner runs at least as often as the per-repository
  copies did, and phase 4 records its daily run count and longest
  gap.
* `pre-commit run --all-files` passes.
* `scripts/audit-check.py` run against this repository reports no
  new failures, or the plan states which verdicts moved and why.
* Any new or changed criterion has all five of its files in step:
  the `Check` subclass in `scripts/audit/checks/<family>.py`, its
  registration in `CHECKS` in `scripts/audit/registry.py`, the
  specification under `docs/audits/`, its row in
  `docs/audits/README.md`, and its frozen lines in
  `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
  `scripts/tests/test_metadata.py` -- plus a
  `FROZEN_COLUMN_NAMES` line where a spec page carries more than
  one check. `AUDIT_METADATA`, `ISSUE_TITLES` and `COLUMN_NAMES`
  are derived from the registry rather than tables anybody edits;
  the issue title is the fleet-wide idempotency key, so renaming
  one orphans every open issue for that check across the fleet.
* No `consistency-audit` marker block has been added to a
  criterion specification by hand, and the compliance tables in
  `docs/audits/compliance.md` have not been hand-edited.
* Any entry added to `REPO_OVERRIDES` carries a stated reason.
* Anything under `templates/` is judged as the code it will
  become in ten other repositories: placeholders consistent, no
  reference to paths that only exist here, and the README beside
  it saying what to substitute.
* Python is wrapped at 120 characters, single quotes for strings
  and double quotes for docstrings, and no script has grown a
  dependency outside the standard library.
* Documentation in `docs/` describes any user-visible change.
  `AGENTS.md` changes only if a convention changed;
  `ARCHITECTURE.md` only if the shape of the system changed;
  `README.md` only if the pitch, the install story or the
  documentation links changed.

### Documentation index maintenance

When creating a new master plan from this template, add one row to
the table in `docs/plans/index.md`: the date the plan was written,
a link to it, a one-line intent, and its status from the
vocabulary above. Rows run oldest first. One row per master plan,
never one per phase -- the phases are tracked in the plan's own
Execution table, and duplicating them in the index is how the two
drift apart.

There is no phase-arithmetic column and no `order.yml` here; both
belong to repositories whose documentation is published through a
generated navigation. The `plan-index` criterion checks the
columns this index actually has.

The index row carries the whole-plan status, so it only reaches
`Complete` once every phase has been completed, abandoned or
superseded. Update it as the plan progresses, not only at the end.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

* **Shared workflows instead of copied templates.** Renovate is the
  extreme case of a wider problem: on 2026-10-03, the 17 live copies
  of `codeql-analysis.yml` came in 12 versions. Converting the other
  verbatim-copied templates into callers of shared workflows in
  `actions` is phase 5 of `PLAN-workflow-ref-trust.md`, because it
  rests on that plan's decision to trust `actions` main.
* GitHub dropping top-of-hour scheduled events (see Situation) is
  not specific to Renovate. Phase 2 moves the central runner off the
  hour, but any other fleet workflow scheduled at minute `0` is
  exposed to the same delays and drops, which the
  `scheduled-workflow-health` criterion would want to know.
* Promoting lockstep groups into the preset, which is what
  [development#88](https://github.com/shakenfist/development/issues/88)
  ultimately asks for. A group that appears in more than one
  repository could live in `renovate-fleet.json`, but
  `renovate-lockstep-groups` would first have to resolve an
  `extends` of the preset as well as reading the local file, with
  tests showing it accepts a group defined there; until then it
  reports a promoted group as missing.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

* Related, and partly addressed here:
  [development#88](https://github.com/shakenfist/development/issues/88)
  asks for one global set of Renovate groupings instead of
  rediscovering each one through CI failures in each repository.
  The phase 1 preset carries the groupings that are fleet policy
  (the CI-tooling group, the shellcheck-py versioning rule). The
  lockstep groups stay local in this plan, for the reason phase 3
  gives, and their promotion is under Future work, so #88 stays
  open.
* `templates/renovate/README.md` and `docs/audits/renovate.md`
  describe `renovate.yml` as copied verbatim; 17 copies hold 14
  versions. Phase 1 corrects the README, and phase 3 removes the
  file and rewrites the spec around the preset.
* Renovate cannot look up `shellcheck-py/shellcheck-py`'s
  four-component tags anywhere except `kerbside`, so the hook is
  never bumped (found from `hunkydory#34`, the dependency dashboard,
  on 2026-10-09). No issue tracks it. Phase 1 fixes it in the
  preset, and phase 3 removes `kerbside`'s local copy.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
