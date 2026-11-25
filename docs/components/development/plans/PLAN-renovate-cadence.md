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

The action itself is gone since this was measured: on 2026-10-15 the
template stopped running Renovate as a container and began installing
the npm `renovate` package, pinned in `PINNED_RENOVATE_VERSION`. The
measurement above stands as history, but the dependency it names is
now that package, and it churns harder than the action did --- 204
pull requests in the 30 days to 2026-11-11 against the 111 here. Phase
1's survey, item 2, has the figures, and phase 4 re-establishes both
ends of the comparison with one method rather than measuring against
this table.

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
the post-change shape rather than chosen, and so is unaffected by
the baseline having moved (phase 1's survey, item 2, measures it at
roughly twice the Situation's figure): about 4 bumps of the npm
`renovate` pin in `renovate-fleet.yml` (weekly, see phase 2), about
14 monthly CI-tooling pull requests, and about 82 runtime pull
requests, which stay one per pull request and which a weekly window
barely folds. Phase 4 measures the before and the after with one
method, because the Situation's number predates both the move to npm
and the drift audit. Runtime dependencies are therefore most of what
remains, and the lever that would cut further is grouping them, which
this plan declines for the bisection reason below. The Renovate share
of CI time is recorded alongside it, but it is not the target,
because the runs API's 1,000-run cap gives each repository a
different window. Three changes do this:

1. **Run one Renovate instead of 23.** A single scheduled
   workflow in this repository runs Renovate across the
   organisation. The per-repository `renovate.yml` copies go away,
   and with them the roughly 85 daily Renovate runs on `static`
   and the pull requests that bump each copy's own Renovate pin:
   204 in the 30 days to 2026-11-11, where the Situation's window
   saw 111 for the container action the copies ran then. The copy
   count was seventeen when this plan was written; phase 2's
   survey, item 3, counted 23 on 2026-11-12.
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
to a month, against up to about ten hours today. Step 1a measured
which of the fleet's ecosystems Dependabot covers, and the gap is
wider than this paragraph assumed. Covered: pip, cargo, npm and
github-actions. Not covered at all: **docker**, which is absent from
GitHub's supported-ecosystem table, and **pre-commit**, which is not
an ecosystem --- a hook is a git repository rather than a package in
an advisory database. The github-actions coverage also holds only for
actions referenced by semantic version, not by commit sha, so
`PLAN-workflow-ref-trust.md`'s digest pinning moves that ecosystem
into the gap as well; that plan should know, and phase 1's Future
work says so. Those three are the accepted worst case, and the
override is manual: the dependency dashboard lists updates awaiting
their schedule, and ticking one opens its pull request on the next
run.

Out of scope:

- Automerge. Fleet policy is that every update is reviewed, and
  this plan keeps it.
- The pinning policy itself, which `PLAN-workflow-ref-trust.md`
  owns. This plan provides the grouping that policy needs, and
  its phase 3 must land before that plan's step 4a.
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
   before any per-repository workflow is disabled. *Mostly
   answered* by cutover step 1 (2026-11-14, with the secret scopes
   read on 2026-11-15): the token pushes to all 23 managed
   repositories, and it lives in an organisation secret with
   `selected` visibility plus an environment copy on this
   repository, with no repository-level copy anywhere in the 23.
   Phase 2's Record holds the detail, including the one managed
   repository the organisation list is missing. Whether the runner
   reads Dependabot alerts is still unconfirmed, because neither dry
   run logs it at INFO, and that half is carried to phase 3.
2. **Which timezone do schedules use?** Renovate reads schedules in
   UTC unless `timezone` is set, and today's `after 9pm` is UTC.
   *Answered* on 2026-11-11: the operator chose `Australia/Sydney`,
   which the preset sets explicitly (phase 1, decision 5).
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
   as a backstop. *Answered* by step 1a: **no**. Phase 1 did find two
   ecosystems Dependabot does not cover, docker and pre-commit, but
   OSV cannot reach either: Renovate's `datasourceToOsvEcosystem`
   map has no entry for `docker` or for the `github-tags` datasource
   that the pre-commit manager resolves hooks to, so the option would
   duplicate the pip, cargo and npm coverage Dependabot already gives
   and add nothing. It is also still marked experimental.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Shared preset and its validation | Complete | `47086db` |
| 2. Central runner and cutover | Complete | `fc75514` (#396), `013c9c4` (#400), `e259e24` (#432), private-ci `250a8ba` (#321), `d23c5ad` (#440) |
| 3. Fleet sweep and audit change | In progress | |
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
* **`PLAN-workflow-ref-trust.md`'s digest pinning lands in the
  preset before this plan's phase 3, not after it.** This paragraph
  said the opposite until 2026-11-13, reasoning that a line added
  to the preset before the sweep reaches one repository while the
  fleet's digest pull requests would arrive one per release per
  repository. The second half does not follow from the first: that
  churn is prevented by the monthly CI-tooling group, which lives
  in this same preset, rather than by the ordering. What the
  ordering decides is where the change gets reviewed. Before the
  sweep it changes one repository, and this plan's own sweep pull
  request then carries the *policy* into each repository
  reviewably, alongside everything else that repository receives.
  The pin diff is not in that pull request: it arrives afterwards,
  as one grouped Renovate pull request per repository on the next
  monthly window, which is also the volume to expect from the
  sweep -- roughly one grouped pull request per repository, not one
  per action per release. After the sweep the
  same one-line edit changes every repository's behaviour with no
  pull request in any of them -- which is what the comment above
  `RENOVATE_FLEET_PRESET` in `scripts/audit/checks/packaging.py`
  warns about. The matching reasoning is in that plan's phase 3.
  (The operator's `plan-blockers` tool carries a matching note, but
  it lives outside this repository.)

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

#### Scope

Add a Renovate preset to this repository that holds the fleet
policy: `gitAuthor`, `assignees`, `dependencyDashboard`,
`rollbackPrs`, `minimumReleaseAge`, the pre-commit manager, the
`customManagers:githubActionsVersions` extend, the schedules from
open question 3, `timezone`, `prHourlyLimit` and
`prConcurrentLimit` (set explicitly, from step 1a), the
no-automerge rule, the two rules that keep `node-version` and the
pinned Renovate in step, the `shellcheck-py/shellcheck-py`
versioning rule, and the CI-tooling group (`matchManagers:
["github-actions", "pre-commit"]`, all update types including
`digest`, monthly).

The preset is `renovate-fleet.json` at the root of this
repository, and every repository references it as
`"extends": ["local>shakenfist/development:renovate-fleet"]`.
Steps 1b, 1c and 1d, and phase 3's audit change, all work to that
exact path and string. This repository's own `github-actions`
`managerFilePatterns` for `templates/`, and the `customManagers`
regex entry that gives the built-in preset's match string the same
reach, stay in its local `renovate.json`, because they apply only
here (`docs/audits/renovate.md`, "Repositories that ship workflow
templates").

Renovate reads a `local>` preset from the default branch of the
repository that holds it, so a change to the preset cannot be
tried from a pull request branch: it takes effect for the whole
fleet when it merges. Pre-commit's validator is the only check
before that, and phase 2's lookup dry run is the first live read
of the preset.

Out of scope for this phase: the central runner and the cutover
(phase 2), deleting any per-repository `renovate.yml` or touching
any other repository (phase 3), and the lockstep groups, which
stay local for the reason phase 3 gives. Also out of scope: the
criterion that would *require* the shellcheck-py rule rather than
merely permit it, which survey item 7 records as a separate plan.

#### What the survey found

The phase's premises were written on 2026-10-03. Seven changes have
landed in this repository since, and four of them make a claim in
this section or its step table false. Each is corrected at its
source in the same commit as this plan, so a later reader does not
have to find this list first.

1. **The dependency this plan is mostly about no longer exists.**
   `templates/renovate/renovate.yml` stopped using
   `renovatebot/github-action` on 2026-10-15 (`7682dec`, "Run
   Renovate from npm, not in Docker"), because the action runs
   Renovate as a container and the static pool's ephemeral runner
   pods have no Docker daemon. The template now sets up Node 24,
   installs `renovate` from npm with `--ignore-scripts` and
   rebuilds `re2`, and runs the binary; the pin is
   `PINNED_RENOVATE_VERSION` with a `# renovate:` comment above it,
   read by `customManagers:githubActionsVersions`. Two further
   commits added conditional Rust (`b24d693`) and `hashin`
   (`38092c4`) steps for lock-file refresh. Step 1a was written to
   research an action version and the Renovate version inside it;
   there is no action and no container, so it now researches the
   npm `renovate` release the template pins. The Situation's
   measurement window closed on 2026-10-03, before any of this, so
   its table is accurate history and is left alone with a note
   pointing here.

2. **The churn nearly doubled, and all of the growth is the one
   dependency phase 2 deletes.** Counted on 2026-11-11 with `gh pr
   list --search 'created:>=2026-10-12 in:title Update dependency
   renovate'` over the twenty matrix repositories that have the
   workflow: 204 pull requests in 30 days for the Renovate pin
   alone, against the 111 the Situation measured for the action.
   Per repository it runs 7 to 21, and the three that report zero
   (`agent-python`, `client-python`, `client-python-k3s`) are the
   ones still running the container action without
   `customManagers:githubActionsVersions`, so their pin is
   invisible rather than current. Org-wide, pull requests by
   `shakenfist-bot` whose title begins `Update` number 417 in the
   same window, plus 10 `Lock file maintenance`, against the
   Situation's 227 for all of Renovate. The Mission's baseline is
   therefore stale by roughly a factor of two. Its *target* is not:
   `100 or fewer` was derived from the post-change shape (a handful
   of bumps of one central pin, about 14 monthly CI-tooling pull
   requests, about 82 runtime ones), not as a fraction of the
   baseline, so the number stands and only the thing it is measured
   down from has moved. Phase 4 re-establishes both ends with one
   method rather than comparing against the Situation's.

3. **The `shellcheck-py` rule is already fleet policy here.**
   `dc2f6ed` (#275, 2026-10-18) put it into
   `templates/renovate/renovate.json` and this repository's root
   `renovate.json`, with the `build` group name and the `$` anchor
   the step table specifies and a fuller description than
   `kerbside`'s, and documented it in
   `templates/renovate/README.md`. So the rule moves into the
   preset from this repository's own template, verbatim, and not
   from `kerbside`; `kerbside`'s surviving local copy and its
   `tox.ini` group are phase 3's business. The scope paragraph that
   said `kerbside` had solved it alone, and listed the five
   dashboards carrying the warning on 2026-10-09, is corrected.

4. **"Copied verbatim" is now true and enforced, so there is
   nothing to correct in the README.** `d2298b0` (2026-10-31) added
   `renovate_workflow_drift` (`scripts/audit/checks/packaging.py`),
   which holds every copy of `renovate.yml` byte-identical to the
   template with each `PINNED_*_VERSION` value and each `uses:` ref
   blanked, and `95f3492` extended the blanking to action refs.
   `templates/renovate/README.md` and `docs/audits/renovate.md`
   both describe that rule. This repository's
   `.github/workflows/renovate.yml` is byte-identical to the
   template today. Step 1d therefore drops the instruction to
   correct the README's verbatim claim, and the Bugs-fixed entry
   that raised it records how it was actually fixed.

5. **A second local-only check will fail this repository, and the
   step table did not name it.** `renovate_reads_version_comments`
   (`scripts/audit/checks/packaging.py:173`) reads only the local
   `renovate.json` and requires `customManagers:githubActionsVersions`
   in `extends`; the `Renovate` check calls it at line 486. It is
   the same shape of bug as `renovate_manages_pre_commit`, which
   step 1c exists to fix, and it has the same blast radius: if the
   preset carries the extend and step 1b strips it from the root
   file, `development` fails its own `renovate` criterion on the
   next scheduled run. Decision 1 resolves it, decision 2 shapes
   the fix. `renovate_manages_pre_commit` is also at line 46 now,
   not 47, and the `renovate.json` path it reads is resolved by
   `renovate_config` at line 702 rather than inline at line 54.

6. **The root `renovate.json` has grown three keys the step table
   does not account for.** Beside what it held in October it now
   carries a `customManagers` regex entry for `templates/` (added
   `c6446d2`), a `prBodyNotes` rule warning that a Renovate major
   can raise `engines.node` past the workflow's `node-version`
   (`91721b7`), and a rule disabling Node majors for
   `.github/workflows/renovate.yml` and
   `templates/renovate/renovate.yml` (`46e8d3e`, after a Node 26
   bump broke nine repositories' hourly runs at once). The last two
   are in the template as well, so they are fleet policy; decision
   4 places them. Step 1b's list of what stays and what goes is
   rewritten accordingly.

7. **Everything else the phase assumes still holds.**
   `renovate-fleet.json` does not exist and nothing in the tree
   references the preset. `.pre-commit-config.yaml` has no Renovate
   hook. `renovatebot/pre-commit-hooks` exists, its newest tag is
   `44.227.4`, and its `renovate-config-validator` hook is
   `language: node`, `language_version: lts`, with
   `additional_dependencies: [renovate@44.227.4]` and exactly the
   `files:` default the step table quotes --- which matches both
   `renovate.json` files and not `renovate-fleet.json`. `ci.yml`'s
   `lint-and-test` job runs `pre-commit run --all-files` on
   `[self-hosted, static]`, so the hook is exercised in CI and not
   only on a workstation. `*.json` is in `.vscode/review-scope.toml`,
   so the preset joins the review queue; `review-tracking-tests`
   does not assert the `REVIEWS.md` header count, so no
   regeneration commit is needed. No criterion compares any
   `renovate.json` against the template, so changing the template's
   copy fails nobody. One thing the survey found that is *not* in
   this phase's scope: a criterion requiring the shellcheck-py rule
   in every repository using the hook. When this was written it was
   recorded only in the operator's `plan-blockers` notes; it is now
   `docs/plans/PLAN-renovate-shellcheck-audit.md`, written on
   2026-11-21, and it reads the preset form too, for the reason
   decision 1 gives. Future work has the rest.

**Blast radius.** This phase changes no criterion's requirements ---
it only widens what satisfies two of them --- so it newly fails no
repository. The one repository it can break is this one, and only
if step 1b lands without 1c.

#### Decisions

1. **The preset carries `customManagers:githubActionsVersions`,
   and the audit is taught to see it there.** The alternative is to
   leave the extend in every local `renovate.json`, which costs
   nothing today and avoids touching a second check. It is rejected
   because it splits the fleet policy for no reason other than the
   audit's convenience: the entry is either right for every
   repository or wrong for every repository, which is the same test
   the shellcheck rule was admitted under. The audit reading only
   the local file is the defect, and a plan whose whole purpose is
   to move policy into a preset should fix it once rather than
   route around it twice. This is the decision most likely to be
   argued with, so the counter-argument is in the back brief.

2. **One helper for both checks, and the preset is trusted by name
   in neither of them.** Factor each check's existing tests on the
   parsed dict into a helper that takes the config dict ---
   `renovate_manages_pre_commit`'s three forms, and
   `renovate_reads_version_comments`'s preset tuple --- and have a
   single new predicate, `extends_fleet_preset(config)`, answer the
   preset question for both call sites. The per-check helpers must
   not know the preset's name, so neither can pass a repository by
   trusting a string; the name is matched once, in one place, and
   what it vouches for is proven by the test in decision 3.

3. **The preset is proven by loading the real file, and the proof
   is mutation-tested.** A `local>` preset takes effect fleet-wide
   the moment it merges, so a commit that dropped the pre-commit
   manager or the version-comment extend from
   `renovate-fleet.json` would make every repository in the fleet
   pass a check on a promise the preset no longer keeps. Step 1c
   therefore loads the real `renovate-fleet.json` from the
   repository root with `json.load` and asserts each per-check
   helper accepts it on its own, with no preset special case in
   play, and the step runs `mutation-check` over the new tests so
   that the guard is shown to fail when the thing it guards is
   removed.

4. **The two Node rules go into the preset, and the disable rule
   names both paths.** They are in the template as well as here, so
   they are fleet policy by the test decision 1 uses. The disable
   rule's `matchFileNames` lists `.github/workflows/renovate.yml`
   and `templates/renovate/renovate.yml`; the second path exists
   only in this repository and is inert everywhere else, which is
   the precedent the shellcheck rule already set ("Harmless in a
   repository that does not use the hook"). Phase 2 adds
   `renovate-fleet.yml` to the same rule when the central runner
   arrives, and phase 3's sweep leaves both rules naming a file the
   fleet no longer has, which is a no-op rather than a finding.

5. **`timezone` is `Australia/Sydney`** (open question 2, answered
   by the operator on 2026-11-11), with open question 3's cadence
   defaults: ordinary updates `on saturday`, majors and the
   CI-tooling group `on the first day of the month`,
   `vulnerabilityAlerts` at any time. The windows are whole days,
   so the zone decides which 24 hours count as Saturday rather than
   a boundary of minutes; in a local zone the weekly batch arrives
   across Saturday instead of straddling Saturday night.

6. **The validator hook is pinned at `44.227.4`, its `files:` is
   overridden, and it is passed `--no-global`.** The default pattern
   skips `renovate-fleet.json` while still passing, which is the
   failure mode the hook is being added to prevent, so the override
   and the deliberate-break proof are both definition-of-done items
   rather than suggestions. Step 1a found that the flag matters for
   the same reason: a file given as an argument is validated as a
   global self-hosted configuration, which accepts a
   self-hosted-only key in a file that is neither, and the Record
   has the measurement. The
   hook pins `renovate@44.227.4` in `additional_dependencies`,
   which is a second Renovate version in the repository, bumped by
   the pre-commit manager rather than by the workflow's
   `# renovate:` comment. Accepted: under the preset's monthly
   CI-tooling group that is one pull request a month, and the two
   pins validating and running the same configuration can drift a
   few releases apart without consequence.

7. **The documentation is a step of its own.** The step table had
   1c carrying the audit change, its tests, and rewrites of two
   documentation pages. Splitting the prose into 1d gives each a
   commit of its own, which is the convention, and keeps a
   documentation rewrite from being reviewed as part of a diff
   whose correctness argument is about Python.

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Research only; change no files, report with citations, do not assume. The subject is the npm `renovate` package, **not** `renovatebot/github-action`: survey item 1 records that `templates/renovate/renovate.yml` stopped using the action on 2026-10-15 and now installs `renovate` from npm, pinned in `PINNED_RENOVATE_VERSION` (44.222.2 in the tree today; 44.227.4 is the newest tag of `renovatebot/pre-commit-hooks`, which tracks the same release train). Answer four questions against that version's documentation and source. (i) What `vulnerabilityAlerts` does by default with `schedule`, `minimumReleaseAge` and grouping --- the plan relies on it dropping all three, and tightening the schedule is only safe if it does. If a security fix would wait for the weekly window or join the CI-tooling group, say exactly which key the preset must override. (ii) Which of the ecosystems the fleet uses (pip, cargo, npm, docker, github-actions, pre-commit) GitHub Dependabot raises alerts for, since `vulnerabilityAlerts` is fed by them; this answers open question 5, and the github-actions and pre-commit answers are the accepted worst case the Mission's security-latency paragraph states. (iii) The defaults for `prHourlyLimit` and `prConcurrentLimit` in that version (recent versions appear to default to 2 and 10, but verify), and whether `vulnerabilityAlerts` pull requests are exempt from them. With a once-a-week window those limits decide how much of a week's backlog opens in it, so recommend values and say why; step 1b sets both explicitly rather than inheriting. (iv) Whether `renovate-config-validator` validates a preset file as an ordinary configuration, including one whose own `extends` names a built-in preset, so that step 1b's hook genuinely checks `renovate-fleet.json`. Report to the management session; 1b does not start until the operator has seen the answers (see the back brief gate). |
| 1b | medium | sonnet | none | Write `renovate-fleet.json` at the repository root and move the fleet policy into it, then reduce both `renovate.json` files to it. The preset holds: `$schema`; `extends` with `customManagers:githubActionsVersions` (decision 1); `gitAuthor`, `assignees`, `dependencyDashboard`, `rollbackPrs`, `minimumReleaseAge`; `"pre-commit": {"enabled": true}` with its existing description; `"timezone": "Australia/Sydney"`; the schedules from decision 5 and `prHourlyLimit: 2` with `prConcurrentLimit: 10` from step 1a's Record, which are Renovate's own defaults written down rather than inherited; **no** `vulnerabilityAlerts` object and **no** `osvVulnerabilityAlerts`, because step 1a established that the defaults already drop `schedule`, `minimumReleaseAge` and grouping for a security fix and that OSV cannot reach either ecosystem Dependabot misses; the no-automerge rule; the Renovate-major `prBodyNotes` rule and the Node-major disable rule, the latter with `matchFileNames` listing both `.github/workflows/renovate.yml` and `templates/renovate/renovate.yml` (decision 4); the `shellcheck-py/shellcheck-py` versioning rule copied **verbatim from this repository's own `templates/renovate/renovate.json`**, description included, not from `kerbside` (survey item 3) --- keep the `build` group name and the `$` anchor, because Renovate's regex versioning (`lib/modules/versioning/regex/index.ts`) only appends `revision` to the comparison array when `build` also matched, so renaming the group would truncate every tag to three components and hide packaging-only releases, and the anchor is what ignores the `v0.11.0.1-1` re-tag that has no matching PyPI release; and the CI-tooling group (`matchManagers: ["github-actions", "pre-commit"]`, every update type including `digest`, scheduled monthly). Then reduce `templates/renovate/renovate.json` to `$schema`, `"extends": ["local>shakenfist/development:renovate-fleet"]`, and the per-repository sections the README describes, and reduce this repository's root `renovate.json` to `$schema`, that same `extends`, the `github-actions` `managerFilePatterns` for `templates/` with its description, and the `customManagers` regex entry for `templates/` with its description --- those last two apply only here (survey item 6). Everything else either of them holds today is in the preset and goes. Add `renovatebot/pre-commit-hooks` rev `44.227.4` to `.pre-commit-config.yaml` with the `renovate-config-validator` hook, override its `files:` --- the default `(^|/).?renovate(?:rc)?(?:\.json[c5]?)?$` matches both `renovate.json` files but not `renovate-fleet.json`, so the preset would be skipped and the hook would still pass --- and pass it `args: ['--no-global']`, because a file given as an argument is otherwise validated as a global self-hosted configuration, which accepts a self-hosted-only key in a file that is not one (step 1a's Record has the measurement; the flag costs nothing and both modes still catch unknown keys and bad values). Follow the file's own convention of a comment saying why each hook is there. Then prove it: break the preset on purpose (an unknown top-level key is enough), run `pre-commit run renovate-config-validator --all-files`, confirm it fails and names `renovate-fleet.json`, paste the output into your report, and restore the file. Do not commit the broken state. This makes `development` the preset's first live user from the moment phase 1 merges, still run by its own `renovate.yml`, so its dependency dashboard is the canary for the preset's schedules before the rest of the fleet adopts it. Step 1c must land in the same pull request, or this repository fails the `Renovate` check on the two local keys that have just been removed. |
| 1c | medium | sonnet | none | Teach two checks in `scripts/audit/checks/packaging.py` that the fleet preset satisfies them, which it must do before 1b's reduced configs can pass. `renovate_manages_pre_commit` (line 46) tests three forms on the parsed dict: `pre-commit.enabled` is `True`, `pre-commit` appears in `enabledManagers`, or an `extends` entry ends `:enablePreCommit`. `renovate_reads_version_comments` (line 173) tests one: an `extends` entry in `RENOVATE_VERSION_COMMENT_PRESETS`. Both read the local file only, through `renovate_config` (line 702). Factor each one's dict tests into a helper that takes the config dict --- so `renovate_config_manages_pre_commit(config)` and `renovate_config_reads_version_comments(config)`, or whatever reads best beside the existing names --- add one new predicate `extends_fleet_preset(config)` that looks for `local>shakenfist/development:renovate-fleet` in `extends`, and have each public function return `helper(config) or extends_fleet_preset(config)`. The per-check helpers must not know the preset's name (decision 2), so neither can pass a repository by trusting a string. Tests go in `scripts/tests/test_packaging.py` beside `RenovatePreCommitManagerTest` (line 265) and in a new class for the version-comment check, following `CheckTestCase` as the neighbours do: for each check, a passing local form, a failing config, and the preset form; and, per decision 3, a test that loads the real `renovate-fleet.json` from the repository root with `json.load` and asserts each per-check *helper* accepts it with no preset special case in play --- that test is the only thing standing between a future edit of the preset and every repository in the fleet passing on a promise the preset no longer keeps. Then run the `mutation-check` skill over the new tests and report which mutants were killed; a test that survives the removal of what it guards is not finished. Verify with `python3 -m unittest discover -s scripts/tests -t scripts`, and with `python3 scripts/audit-check.py --repo-path . --repo-name development`, which must report `renovate` compliant on the branch after 1b. Do not touch `docs/` --- that is 1d. |
| 1d | medium | sonnet | none | Document the preset. In `docs/audits/renovate.md`: the "What we check" bullets for `renovate.json` extending `customManagers:githubActionsVersions` and enabling the `pre-commit` manager now have a fourth satisfying form each, the fleet preset, so say that both are satisfied either locally or through `local>shakenfist/development:renovate-fleet`, and add a short section on the preset --- what it holds, that Renovate reads it from this repository's default branch so it cannot be tried from a branch, that pre-commit's `renovate-config-validator` is the only check before it merges, and that the audit trusts it by name with a test that loads the real file. Leave the "Repositories that ship workflow templates" section as it stands: its `github-actions` and `customManagers` patterns are still local to `development` (survey item 6). In `templates/renovate/README.md`: the `renovate.json` row and the "Copying these files" section now describe a file that extends the preset and adds only what is particular to the repository, and the three-form list under "The pre-commit manager" gains the preset. Keep the shellcheck-py section but say the rule now lives in the preset rather than in each copy. Add one sentence, in whichever page the preset's contents are described on, recording step 1a's exception: a `packageRules` entry with `enabled: false` suppresses even a vulnerability fix, which the preset's Node-major disable rule is, so Node majors in the Renovate workflows are the one update type the immediate security path does not reach. State it; do not change the rule. Do **not** correct the claim that `renovate.yml` is copied verbatim: survey item 4 records that `renovate_workflow_drift` has since made it true and that both pages already describe the rule enforcing it. No fact about the preset may be stated differently on the two pages; the definition of done checks that by reading both. |

#### Record

Every entry cites the command or file it came from, and one that
could not be read says so. Dates are Australian Eastern Daylight
Time (UTC+11).

**Security schedule (1a).** Ran 2026-11-12. `vulnerabilityAlerts`
drops all three of `schedule`, `minimumReleaseAge` and `groupName`
by default, so the preset needs no `vulnerabilityAlerts` object at
all: `lib/config/options/index.ts` at tag `44.222.2` gives the
option a default of `groupName: null`, `schedule: []`,
`minimumReleaseAge: null`, `prCreation: 'immediate'` and
`prConcurrentLimit: 0`, read directly by the management session as
well as by the step. The forcing is not merely a default that a
top-level key could beat: the Dependabot path appends one
`packageRules` entry per alerted dependency with
`force: {...config.vulnerabilityAlerts}`, and `mergeChildConfig`
returns `{ ...config, ...config.force }`, so the forced keys beat
the top level and every earlier rule. Renovate's documentation says
the same of `schedule` and `prHourlyLimit` and adds that it cannot
be overridden. One real exception, recorded because it applies to
this preset: a `packageRules` entry with `enabled: false` does still
suppress a vulnerability fix, and the preset has one --- the
Node-major disable rule. It is narrow (two file names, `node`,
`major`), so the exposure is Node majors in the Renovate workflows
alone, and 1d states it rather than the plan changing to avoid it.

**Dependabot coverage (1a).** Covered for the fleet: pip, cargo,
npm, github-actions. Not covered: docker, absent from GitHub's
supported-ecosystem table, and pre-commit, which is not an
ecosystem. Corroborated from Renovate's side by its GitHub ecosystem
enum (`actions, composer, go, maven, npm, nuget, pip, rubygems,
rust`), which logs and skips anything else. The github-actions
coverage holds only for actions referenced by semantic version and
not by commit sha. The Mission's security-latency paragraph is
corrected accordingly, and open question 5 is answered no, with the
reason: OSV's ecosystem map reaches neither `docker` nor the
`github-tags` datasource the pre-commit manager uses, so it would
duplicate the coverage the fleet already has.

**Pull request limits (1a, and a decision against the step's
recommendation).** Defaults confirmed at `prHourlyLimit: 2` and
`prConcurrentLimit: 10`, both enforced per repository: the hourly
count filters the repository's own pull requests by `branchPrefix`
and `createdAt` within the clock hour, and both counters are
re-seeded per repository, so one central multi-repository run still
gives each repository its own budget. Vulnerability pull requests
are exempt from the hourly limit unconditionally and have a separate
concurrent budget, unlimited by the forced `prConcurrentLimit: 0`;
they do still increment the shared hourly counter. The step
recommended `prHourlyLimit: 0`, reasoning that a limit of 2 against
a once-a-week window silently defers a repository's backlog. The
management session put the opposite case to the operator, who chose
**2 and 10, stated explicitly** on 2026-11-12. The reasoning: phase
2's runner is hourly, and the Situation measured about five
scheduled runs actually landing per day, so a whole-day window
offers each repository roughly ten pull requests against a measured
need of about one a week; the limit binds only at cold start, which
is where smoothing a burst into the CI cluster is the wanted
behaviour rather than a cost; and a held update is visible on the
dependency dashboard and already measured by phase 4, which
`prHourlyLimit: 0` would quietly remove.

**The validator needs `--no-global` (1a).** A new finding, and it
changes step 1b. `renovate-config-validator` treats a file given as
an argument as a *global* self-hosted configuration, and global mode
accepts a self-hosted-only key in what is meant to be a repository
preset. Reproduced independently by the management session with
`renovate@44.222.2` in a `node:24` container: a preset containing
`"onboarding": false` exits 0 in the default mode and 1 under
`--no-global`, with `The "onboarding" option is a global option
reserved only for Renovate's global configuration`. Both modes catch
an unknown key (`notARealRenovateKey`, exit 1) and a bad value, so
the flag is about the one class of mistake global mode misses. A
preset holding `$schema`, an `extends` of a built-in preset,
ordinary keys and `packageRules` validates offline in both modes, as
does a consumer config whose only `extends` is the `local>` preset
--- no preset resolution is attempted, so the hook needs no network
beyond its own install.

**The preset (1b).** Written 2026-11-12. Top-level keys, in file
order: `$schema`, `extends` (`customManagers:githubActionsVersions`),
`gitAuthor`, `dependencyDashboard`, `assignees`, `timezone`
(`Australia/Sydney`), `schedule` (`on saturday`), `prHourlyLimit`
(2), `prConcurrentLimit` (10), `pre-commit` (enabled, with its
description), `packageRules`, `minimumReleaseAge` (`3 days`),
`rollbackPrs`. No `vulnerabilityAlerts` and no
`osvVulnerabilityAlerts`, per 1a. Six `packageRules`, in order: the
no-automerge rule; majors on the monthly window; the CI-tooling
group (`matchManagers: ["github-actions", "pre-commit"]`,
`groupName: "CI tooling"`, no `matchUpdateTypes` so every type
including `digest`, monthly); the Renovate-major `prBodyNotes`
rule; the Node-major disable rule, now naming
`templates/renovate/renovate.yml` as well; and the shellcheck-py
versioning rule. Checked by the management session against the
template config the preset replaces: no policy key was lost, and
the preset adds exactly `timezone` and the two limits. The
shellcheck rule compares equal to the template's parsed rule, with
the `build` group and the `$` anchor intact.

Two rules the plan named a schedule for but no rule: majors, and
the CI-tooling group's name. Both were the step's judgement and
both were kept.

**The validator proof (1b).** With `"notARealRenovateKey": true`
added to the preset, `pre-commit run renovate-config-validator
--all-files` failed as follows, naming the file, which is the whole
point of the override:

```
renovate-config-validator................................................Failed
- hook id: renovate-config-validator
- exit code: 1

INFO: Validating renovate-fleet.json as repo config
ERROR: Found errors in configuration
       "file": "renovate-fleet.json",
       "errors": [
         {
           "topic": "Configuration Error",
           "message": "Invalid configuration option: notARealRenovateKey"
         }
       ]
```

`as repo config` in that output is `--no-global` taking effect; the
default mode says `as global config` and is what would have let a
self-hosted-only key through. The preset was restored from a copy
and the hook passes again.

**The audit change (1c).** Done 2026-11-12. `extends_fleet_preset`
is the only place the preset's name is matched, by exact membership
of `extends`; `renovate_config_manages_pre_commit` and
`renovate_config_reads_version_comments` take the parsed dict and
know nothing of any preset; each public function is the helper or
the predicate. 2579 tests pass, up from 2569, and
`python3 scripts/audit-check.py --repo-path . --repo-name
development` reports `renovate` as `pass`, "Renovate workflow and
config exist", with both findings gone.

Mutation check: seven mutants, seven killed, none surviving and
none timing out. The preset losing `pre-commit.enabled`, losing the
`pre-commit` key, and losing the version-comment extend; each of the
two call sites losing its predicate; the predicate loosened to
substring matching; and a per-check helper taught the preset's name.
The substring mutant survived the step's first set of tests and a
test was added for it. The management session re-ran the substring
and dropped-manager mutants on copies of the tree and confirms both
fail.

**The documentation (1d).** Done 2026-11-12.
`docs/audits/renovate.md` and `templates/renovate/README.md` each
gained a section on the preset, and the inverted `renovate.json`
story --- the schema and one `extends`, with a repository adding only
what is particular to it --- replaced the per-repository adaptation
both pages described. The five facts that appear on both pages were
compared mechanically rather than by reading: the cadence, the
limits, the CI-tooling group's coverage, the security paragraph and
the `enabled: false` exception each appear exactly once on each page
and are identical. Nothing had to be reconciled, and neither page's
verbatim-copy claim for `renovate.yml` was touched, which survey
item 4 is the reason for.

Three corrections by the management session rather than the step.
The specification's "What we check" list had come to say that
`renovate.json` extends the preset, which the check does not verify
--- a repository keeping the policy locally still passes --- and a
criterion page that overstates its own check misleads the one reader
who consults it, the person working out what their repository is
measured on. The new section described the audit's preset handling
in terms of "tests" where the code has helper functions, which also
risks this repository's own documentation test holding prose that
names a test to naming one that exists. The rest were rewraps and
one sentence in which "the preset" meant both the built-in
`customManagers:githubActionsVersions` and the local fleet preset in
the same breath.

**Open questions settled.** 2 (`Australia/Sydney`) and 5 (no OSV);
3 stands as planned, confirmed by the operator on 2026-11-12.

#### Execution notes

Dates are Australian Eastern Daylight Time (UTC+11).

**Step 1c touched three files, not the two it was scoped to**
(2026-11-12). `scripts/tests/test_hooks.py` holds every suite hook
in `.pre-commit-config.yaml` to firing on everything its suite
reads, and decision 3's test reads `renovate-fleet.json`, so the
`audit-package-tests` hook's `files:` pattern had to name the
preset. The step was fenced off from that file and reported the
failure rather than reaching for it; the management session made
the one-line change. The invariant is right and the step's scope
was wrong: the preset is trusted fleet-wide by name, so a commit
that guts it has to be a commit that fails, including one that
touches nothing else.

**The survey's item 5 was demonstrated, not merely argued**
(2026-11-12). With 1b landed and 1c not yet, `audit-check.py`
against this repository reported both findings --- the
version-comment extend and the pre-commit manager --- and the unit
suite failed on `test_the_template_itself_passes` for the
version-comment extend alone, because that fixture has no
`.pre-commit-config.yaml` for the manager check to apply to. Had
step 1c shipped as the master plan first wrote it, covering only
the pre-commit manager, this repository would have started failing
its own `renovate` criterion at the next scheduled audit run.

**The operator overrode step 1a on the pull request limits**
(2026-11-12), choosing `prHourlyLimit: 2` and
`prConcurrentLimit: 10` over the step's recommendation of `0` and
`10`. The Record's limits entry has the reasoning on both sides.

**Decision 1's nested merge was measured, not assumed** (2026-11-12).
The review round asked what happens now that
`customManagers:githubActionsVersions` arrives through the preset
while this repository keeps a local `customManagers` entry: if
Renovate replaced the array rather than concatenating it, the pinned
Renovate in `templates/renovate/renovate.yml` would stop being
bumped, which is the defect the local entry exists to prevent, and
neither the validator nor any criterion would see it. Renovate
44.227.4 flags `customManagers` `mergeable` with type `array`, and
`mergeChildConfig` concatenates such an option parent-first, at every
level of preset resolution. Confirmed against the real files by
registering `renovate-fleet.json` in the resolver's internal preset
registry and resolving this repository's `renovate.json` through it:
two entries come back, the built-in first and the `templates/` one
second, with all six `packageRules`, the schedule and the timezone
intact. Not left as a test, because reproducing it needs Node and the
`renovate` package, which the audit suite does not have.

#### Risks and mitigations

1. **The preset cannot be tried before it is live.** Renovate
   reads a `local>` preset from the default branch, so the merge
   is the first fleet-wide effect and there is no staging. Checked
   by: the validator hook, which is proven to fail on a broken
   preset before anyone relies on it (step 1b); phase 1 leaving
   `development` as the preset's only user, so its dependency
   dashboard is the canary; and phase 2's `RENOVATE_DRY_RUN=lookup`
   run being the first live read. The management session reads the
   preset's key list against the scope paragraph before the commit
   is proposed.
2. **The audit now trusts a preset by name.** Widening two checks
   to accept `local>shakenfist/development:renovate-fleet` means a
   preset that silently dropped the pre-commit manager or the
   version-comment extend would be invisible to the fleet's own
   measurement. Checked by: decision 3's test, which loads the real
   file and asserts the per-check helpers accept it without the
   special case, and the mutation check that shows the test fails
   when the preset is gutted. The management session reads that
   test rather than trusting the step's summary.
3. **Step 1b without 1c fails this repository.** Removing the
   `pre-commit` key and the `customManagers` extend from the root
   `renovate.json` makes `development` non-compliant on its own
   `renovate` criterion at the next scheduled run, which files an
   issue against this repository. Checked by: both steps landing in
   one pull request, and the definition of done requiring
   `audit-check.py` to report `renovate` compliant on the branch
   before the commit is proposed.
4. **A week's updates could slip silently.** `on saturday` means
   Renovate acts only when a run lands inside the window, and the
   Situation measured gaps of up to 9 hours 35 minutes between
   scheduled runs. Mitigated by the window being a whole day (open
   question 3) rather than six hours, and by this repository still
   running hourly in phase 1, so the canary has roughly 24 chances
   to land in it. Phase 2 records the central runner's longest gap
   against the same window.
5. **The validator adds a Node toolchain fetch to every
   pre-commit run.** `language: node` with `language_version: lts`
   means pre-commit installs Node and Renovate into a hook
   environment on first use, on workstations and on the
   `[self-hosted, static]` runner that `ci.yml`'s `lint-and-test`
   job uses. Checked by: the step reporting the hook running
   successfully, and CI on the pull request being the second place
   it runs. If the runner cannot reach the npm registry the step
   reports that rather than working around it, because a hook that
   cannot run in CI is not a check.

#### Definition of done

Every item is checked by the management session against the files
and the commands, not against a sub-agent's summary.

* `renovate-fleet.json` exists at the repository root, and every
  key the Scope paragraph lists is present in it.
* `pre-commit run --all-files` passes, and the `Record` holds the
  output of a `renovate-config-validator` run against a
  deliberately broken `renovate-fleet.json` that failed and named
  that file. A hook that passed on a broken preset is the one
  outcome this item exists to exclude.
* The hook passes `--no-global`, and the preset sets
  `prHourlyLimit: 2`, `prConcurrentLimit: 10` and no
  `vulnerabilityAlerts` object, which is what step 1a's Record
  concluded.
* `templates/renovate/renovate.json` and the root `renovate.json`
  each extend `local>shakenfist/development:renovate-fleet`, and
  neither restates a key the preset holds. Checked by comparing
  their top-level keys and `packageRules` descriptions against the
  preset's, not by reading for intent.
* The root `renovate.json` still carries the `github-actions`
  `managerFilePatterns` and the `customManagers` regex entry for
  `templates/`, both with their descriptions.
* `python3 -m unittest discover -s scripts/tests -t scripts`
  passes, and `python3 scripts/audit-check.py --repo-path .
  --repo-name development` reports `renovate` compliant on the
  branch.
* Both `renovate_manages_pre_commit` and
  `renovate_reads_version_comments` accept a config whose only
  `extends` entry is the fleet preset, and reject a config with
  neither the local form nor the preset. Both have a test for each
  of those three cases.
* A test loads the real `renovate-fleet.json` and asserts each
  per-check helper accepts it with no preset special case in play,
  and `mutation-check` reports that test failing when the preset is
  edited to drop what it checks.
* No fact about the preset is stated differently in
  `docs/audits/renovate.md` and `templates/renovate/README.md`, and
  neither page still describes the fleet policy as living in each
  repository's own `renovate.json`.
* Neither page's description of `renovate.yml` as copied verbatim
  has been changed, because survey item 4 found it true.
* Open questions 2 and 5 are answered in the plan, and open
  question 3 either stands or is changed with step 1a's reason
  recorded.
* The four stale claims in survey items 1, 3, 4 and 6 are corrected
  in the master plan's own prose --- the Mission's target
  derivation, the phase 1 scope paragraph, the step table and the
  two Bugs-fixed entries --- in the commit that adds this phase
  plan.

#### Back brief

Before step 1a, back brief the operator on decision 1, which is
the one with a real counter-argument. Putting
`customManagers:githubActionsVersions` in the preset means a second
audit helper has to learn about presets, and the cheap alternative
--- leave that one entry in every repository's local
`renovate.json`, where the audit already sees it --- costs nothing
this phase and nothing in phase 3's sweep. The argument for doing
it anyway is that the entry is fleet policy by exactly the test the
shellcheck rule was admitted under, and that a plan whose purpose
is to move policy into one file should not leave a line behind
because the measuring tool cannot read the new location. Confirm
also decision 5's timezone and cadence, since they are fleet-wide
on merge.

Then stop again after step 1a, before 1b writes the preset:
`vulnerabilityAlerts`' defaults, the Dependabot ecosystem coverage
and the two pull request limits all change what the preset must
say, and the preset is the one file in this plan that cannot be
tried before it is live. Report 1a's four answers and the preset's
intended key list, and get agreement on them before any file is
written.

### Phase 2: central runner and cutover

Planning effort: high. This phase is the only one with live,
fleet-wide effect, and the cutover order is what prevents duplicate
pull requests.

#### Scope

In scope: `.github/workflows/renovate-fleet.yml` in this repository,
its global configuration file `renovate-runner.json`, the validator
hook change that makes a global file checkable at all, the two
preset rules that have to learn about the new workflow, and the
cutover of all 23 managed repositories in the order below. Since
2026-11-23 it also covers cutover step 5, which moves the runner's
trigger from GitHub's cron to the private-ci conductor after step
4's watch day failed its gate; that half lands in private-ci.

Out of scope: deleting any repository's `.github/workflows/renovate.yml`
or moving its `renovate.json` onto the preset, which is phase 3;
re-measuring pull request volume, which is phase 4; and changing any
schedule the preset already sets, which phase 1 settled.

#### What the survey found

The master plan's phase 2 section was written on 2026-10-03, before
the fleet left `renovatebot/github-action` on 2026-10-15 and before
phase 1 shipped the preset. Six of its claims had moved. All of them
are corrected at their source in the commit that adds this plan, so
the prose above no longer disagrees with the tree.

1. **The central runner cannot use `renovatebot/github-action`.**
   The section passed the runner's configuration "to the action
   through its `configurationFile` input", named the action as the
   dependency the temporary rule suppresses, and gave it a local
   weekly override. Phase 1's survey item 1 already recorded that
   `templates/renovate/renovate.yml` stopped using the action on
   2026-10-15 and installs the npm `renovate` package instead.
   `docs/audits/renovate.md` gives the reason, and it applies to
   this workflow with full force: "The static runner pool is
   gaining ephemeral pods on a Kubernetes cluster, and they have no
   Docker daemon, so the action fails on every run a pod takes."
   Three repositories still run the action (`agent-python`,
   `client-python`, `client-python-k3s`) and are non-compliant for
   it, so the action survives in the fleet but not as anything this
   phase should adopt.

2. **The runner-label question is already answered, and the answer
   is no.** The section asked whether `static` runners are ephemeral
   and said to record the answer. This repository recorded it in
   `.github/workflows/consistency-audit.yml`, which runs its
   issue-writing job on `[self-hosted, vm, debian-13, s]` rather
   than `static` because the static runners "share one user and one
   home directory between jobs, so code a leg runs could leave a
   `.pth` file or a `~/.gitconfig` hook there for this job to run
   with `AUDIT_TOKEN`. A VM starts from the image and is thrown away
   after one job." `RENOVATE_TOKEN` pushes branches to every
   repository, so it is strictly more powerful than `AUDIT_TOKEN`,
   which can write none of them.

3. **The discovery surface is 23 repositories, and it adds nothing
   silently.** The section sized the timeout for "about 20". Counted
   on 2026-11-12 across the 30 unarchived repositories in the
   organisation: exactly 23 carry both `renovate.json` and
   `.github/workflows/renovate.yml`, and the other seven
   (`divergulent-reviews`, `homebrew-tap`, `performance`,
   `private-ci`, `reproducables`, `sonobouy`,
   `uefi-latency-guest`) carry neither. Because `requireConfig:
   "required"` manages only a repository with a config, and because
   the config and the workflow coincide exactly, `autodiscover` over
   `shakenfist/*` discovers precisely the set that Renovate already
   runs in. The cutover list is therefore knowable now rather than
   from the dry run, and it is in *Cutover* below. Two of the 23,
   `images` and `private-ci`, are N/A for the `renovate` audit
   criterion while still being managed; `private-ci` is one of the
   seven without a config, so the managed N/A is `images` alone.

4. **`renovate-runner.json` needs no `files:` edit, and the hook
   rejects it.** The section said to add the file to the validator
   hook's `files:` and break it on purpose. Phase 1 made that
   pattern derived rather than a list --
   `^(.*/)?(\.renovaterc|\.?renovate[^/]*\.json[c5]?)$` -- so the
   file is picked up with no edit at all, which is what phase 1
   intended. The consequence the section only flagged as a
   possibility is real and was measured on 2026-11-12: with a
   `renovate-runner.json` holding `autodiscover`,
   `autodiscoverFilter`, `requireConfig` and `onboarding` in the
   tree, `pre-commit run renovate-config-validator --all-files`
   exits 1 and reports all four as "a global option reserved only
   for Renovate's global configuration", because phase 1 passes
   `--no-global` and the hook validates every match as repository
   configuration. One hook cannot check both kinds of file, so
   decision 3 splits it.

5. **The preset's Node-major rule does not cover the new
   workflow.** `renovate-fleet.json`'s fifth rule disables major
   `node` updates with `matchFileNames` naming
   `.github/workflows/renovate.yml` and
   `templates/renovate/renovate.yml` only. A
   `renovate-fleet.yml` that sets up Node is read by the same
   `github-actions` manager as a dep named `node`, so the moment
   this phase adds the file, major Node updates become proposable
   against the fleet's own runner, and merging one stops Renovate
   with "Unsupported node environment detected" for every
   repository at once. Confirmed against the pinned version:
   `renovate@44.227.4` declares `engines.node` of `^24.11.0 ||
   ^26.13.0`, so Node 25 is genuinely refused. The preset's
   Renovate-major `prBodyNotes` entry points the reader at the same
   two files and so becomes incomplete in the same commit.

6. **Four mechanical claims hold, and one is a no-op.** Checked
   against `renovate@44.227.4` in the pre-commit hook's own
   installation. `dryRun` is `globalOnly` with
   `allowedValues: ["extract", "lookup", "full"]`, so
   `RENOVATE_DRY_RUN=lookup` is valid. Global parsing merges the
   environment over the file
   (`dist/workers/global/config/parse/index.js:81`), and
   `autodiscoverFilter` carries no `mergeable` flag, so the
   `workflow_dispatch` filter input replaces the file's value
   rather than concatenating with it -- which is what makes a
   single-repository canary possible. `packageRules` *is*
   `mergeable: true`, so the section's claim that the global rule
   concatenates with each repository's resolved rules, parent
   first, is right, and a later rule setting `enabled` would win.
   `requireConfig: "required"` is already Renovate's default, so
   setting it documents rather than changes behaviour.

Five stale figures elsewhere in the plan are corrected with this
phase rather than left for a later one to trip over. The Success
criteria and the phase 4 section both still said the target was
"down from 227", which phase 1's survey item 2 superseded by
measuring 417 org-wide `Update` pull requests in 30 days; the
Mission was corrected then and these two were missed. The Mission's
first change, and two counts in phase 3, still said seventeen
copies of `renovate.yml`, which was true on 2026-10-03 and is 23
today by item 3 above. The Situation's own dated statements of the
same number are left alone: they are accurate history, and phase 1
set the convention of pointing forward from them rather than
rewriting them.

#### Decisions

1. **Run Renovate from npm, the way the template does.** Survey
   item 1 leaves no choice: an action that needs a Docker daemon
   fails on every run that lands on a Kubernetes pod. The workflow
   therefore follows `templates/renovate/renovate.yml` --
   `actions/setup-node`, `npm install --ignore-scripts` of a pinned
   `renovate`, `npm rebuild re2`, and the binary invoked directly
   -- with the global config named by `RENOVATE_CONFIG_FILE`
   instead of an action input. The install step holds no token, as
   in the template, so an install script in Renovate's dependency
   tree cannot reach `RENOVATE_TOKEN`.

2. **The job runs on an ephemeral VM in a protected environment,
   not on `[self-hosted, static]`.** This is the decision most
   worth arguing with, so the argument is in *Risks* as well. The
   case for it is survey item 2: this repository has already
   decided that a job holding a fleet-write token does not run on a
   pool that shares a home directory between jobs, and it decided
   that for a token that can write nothing. The case against is
   that the 23 per-repository copies run on `static` today, so
   `static` is not a regression, and that an ephemeral VM pays the
   Node and npm install on every hourly run with no cache to reuse.
   The deciding consideration is that this phase is the last cheap
   moment: after cutover there is one workflow holding one token
   with organisation-wide push, dispatchable by anyone who can
   dispatch a workflow, and moving it later means re-proving the
   cutover. The environment also buys the `main`-only deployment
   rule that `audit-write` relies on, which matters more here than
   there: a branch that edits this workflow must not be able to
   dispatch it with the organisation's push token. Named
   `renovate-write`, deploying only from `main`.

3. **Keep the name `renovate-runner.json` and split the validator
   hook in two.** The alternative is to name the file so phase 1's
   pattern misses it, which buys a green hook by making the fleet's
   most privileged configuration the one file nobody validates --
   the exact failure phase 1's derived pattern was written to
   prevent. So the hook becomes two entries on the same pinned
   repository: the existing one, with the runner config excluded,
   keeping `--no-global` so that a *repository* config holding a
   global option is still rejected; and a second naming only the
   runner config, in default mode, where global options are
   accepted. `RenovateConfigHookTest` then stops asserting that one
   hook matches every config and asserts the stronger invariant:
   every Renovate configuration in the tree is validated by exactly
   one of the two, neither both nor neither. That keeps the
   property derived rather than listed, so the next config added is
   still covered without anyone remembering this.

4. **The preset learns about `renovate-fleet.yml` in the same pull
   request that adds it.** Survey item 5 is a defect this phase
   creates, so this phase fixes it: `.github/workflows/renovate-fleet.yml`
   joins the Node-major rule's `matchFileNames`, and the
   Renovate-major `prBodyNotes` entry names it as the file whose
   `node-version` has to move with the pin. Both are preset edits,
   so they are live fleet-wide on merge, and both are tested.

5. **Hourly, at minute 23.** Unchanged from the section, and the
   reasoning stands: the Situation measured GitHub delaying and
   dropping top-of-hour scheduled events, and one runner means a
   dropped run delays all 23 repositories at once.

6. **The cutover list is the 23 repositories measured in survey
   item 3**, written into *Cutover* below rather than derived from
   the dry run. The dry run still has to agree with it; a
   disagreement is a finding, not a correction to make silently.

7. **The temporary disable rule stays file-based, for a rewritten
   reason.** The section justified `matchFileNames` over disabling
   the `renovatebot/github-action` package by saying
   `renovate-fleet.yml` uses that action too. It will not. The rule
   is still right, and now for a better reason: between this phase
   and phase 3 the disabled `renovate.yml` files remain in 23
   trees, each pinning a `renovate` version that the
   `custom.regex` manager bumps and three also pinning the action,
   so a package-based rule would have to name three different
   dependencies to suppress churn in files that no longer run,
   while one `matchFileNames` entry covers every dependency in
   them. The file list is the two the preset already names.

8. **No local weekly override for the central pin.** The section
   gave `renovatebot/github-action` in `renovate-fleet.yml` a local
   `groupName` and weekly schedule, to keep the most privileged
   workflow off the monthly window. Survey item 6's manager check
   makes most of that unnecessary: the npm `renovate` pin is found
   by `custom.regex`, not `github-actions`, so the preset's
   CI-tooling group never matched it and it is already weekly. What
   is left on the monthly window is `actions/checkout` and
   `actions/setup-node` in this one file, so a local rule gives
   just those two the weekly schedule, scoped by `matchFileNames`
   to `.github/workflows/renovate-fleet.yml`. That rule is local
   because the file exists only here.

#### Steps

Steps 2a to 2d write files and are sub-agent work. The cutover is
not: every one of its actions changes a live system in other
people's repositories, so it is run by the management session with
the operator watching, in the order under *Cutover*. Steps 2e and
2f were added with cutover step 5 and are in its section.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Write `.github/workflows/renovate-fleet.yml` and `renovate-runner.json`. Model the workflow on `templates/renovate/renovate.yml`, which runs a pinned npm `renovate` rather than `renovatebot/github-action` (decision 1), keeping its comments where they still apply and rewriting them where they do not -- the concurrency comment carries over, `RENOVATE_AUTODISCOVER_FILTER: ${{ github.repository }}` does not. Triggers are `schedule` with `cron: '23 * * * *'` and `workflow_dispatch` only; top-level `permissions: contents: read`; `concurrency: {group: renovate-fleet, cancel-in-progress: false}`; `runs-on: [self-hosted, vm, debian-13, s]` with `environment: renovate-write` (decision 2); `timeout-minutes` set high enough for a serial pass over 23 repositories, with a comment saying the number is provisional until the dry run measures it. Two `workflow_dispatch` inputs, passed as `RENOVATE_DRY_RUN` and `RENOVATE_AUTODISCOVER_FILTER`: survey item 6 confirms `dryRun` accepts `extract`/`lookup`/`full` and that the environment overrides the file for `autodiscoverFilter`, so the filter input narrows rather than widens. The checkout is this repository at the default branch and nothing else; `persist-credentials: false`, since Renovate clones with `RENOVATE_TOKEN` into `RENOVATE_BASE_DIR`. Install Renovate in a step holding no token, exactly as the template does, pinned behind a `# renovate: datasource=npm depName=renovate` comment so `customManagers:githubActionsVersions` bumps it. Before Renovate starts, add the write-path probe cutover step 2 needs: a script in `tools/` (not inline, per the repository's convention on workflow steps longer than a few lines) that calls `GET /repos/shakenfist/<repo>` with `RENOVATE_TOKEN` for each of the 23 repositories and fails naming the repository if `permissions.push` is not true. `renovate-runner.json` holds `$schema`, `autodiscover: true`, `autodiscoverFilter: ["shakenfist/*"]`, `requireConfig: "required"` (already the default; set to document it), `onboarding: false`, and the one temporary `packageRules` entry from decision 7. Do not edit `.pre-commit-config.yaml` -- step 2b owns it, and the hook fails on this file until it lands, so report that rather than working around it. |
| 2b | medium | sonnet | none | Split the `renovate-config-validator` hook in `.pre-commit-config.yaml` per decision 3, and move `scripts/tests/test_hooks.py`'s `RenovateConfigHookTest` onto the stronger invariant. The existing entry keeps `args: ['--no-global']` and gains an `exclude:` for the runner config; a second entry on the same `rev` names only that file and passes no `--no-global`. Give each a distinct `name:` and `alias:` so `pre-commit run <alias>` reaches either. Keep the file's convention of a comment saying why, and say why there are two: a repository config holding a global option must still fail, and the runner config is not a repository config. The test currently asserts that one hook's pattern matches every Renovate configuration in the tree; rewrite it to assert that each is matched by exactly one of the two hooks, keeping the derivation (the set comes from `git ls-files`, not a list) and the guard that the derived set is non-empty. Prove both halves: break `renovate-runner.json` with an unknown top-level key and confirm the global-mode hook fails naming that file; put `autodiscover: true` into `renovate.json` and confirm the repository-mode hook still rejects it. Paste both outputs into your report and restore the files; do not commit either broken state. |
| 2c | medium | sonnet | none | Teach `renovate-fleet.json` about the new workflow (decision 4) and add the local rule from decision 8, then test all three. In the preset: add `.github/workflows/renovate-fleet.yml` to the Node-major disable rule's `matchFileNames`, and extend the Renovate-major `prBodyNotes` entry so it names that file as the one whose `node-version` moves with the pin, without losing the two files it already names -- phase 3 removes those, not this phase. In this repository's own `renovate.json`: a rule scoped by `matchFileNames` to `.github/workflows/renovate-fleet.yml` and `matchPackageNames` to `actions/checkout` and `actions/setup-node`, giving them the weekly schedule, with a description saying why the npm pin needs no such rule (it is found by `custom.regex`, which the CI-tooling group's `matchManagers` never matched). Extend `RenovateFleetPresetTest` in `scripts/tests/test_packaging.py` with a test per change, using the existing `_rule(**match)` helper; the Node rule's test must assert the full `matchFileNames` set rather than membership, so dropping one file fails it. Then mutation-test: break each of the three additions in turn, confirm a test fails and names the right thing, keep the script under this session's scratchpad, and report the count. Restore from copies, never `git checkout <dir>`, and run each mutant under `timeout`. |
| 2d | medium | sonnet | none | Document the central runner. `docs/audits/renovate.md` is the fleet's Renovate specification and currently describes a per-repository workflow as the arrangement; add a section for the central runner that says what it is, where its global config lives, why it runs on a VM in a protected environment, and that the per-repository copies are disabled but not yet deleted, which phase 3 does. Do not rewrite the "What we check" bullets: the criterion still checks the per-repository files, which still exist, and changing it belongs to phase 3. `templates/renovate/README.md` gets a short note that the template is on its way out and why it is still there. Keep the fact in one place and point at it from the other, as phase 1 did: no fact about the runner stated differently on two pages. `AGENTS.md` and `ARCHITECTURE.md` only if a convention or the shape of the system changed -- a new fleet-wide workflow is arguably the latter, so make the judgement and say which way you went. |

#### Cutover

Operator-gated, in this order. Nothing here is a sub-agent step,
and nothing in it is done without an explicit yes.

1. **Dry run, read path only.** Dispatch with
   `RENOVATE_DRY_RUN=lookup`. Confirm it discovers exactly the 23
   repositories below, that it reads each one's Dependabot alerts
   (open question 1), and that every dependency in a
   `renovate.yml` is reported as disabled by the temporary rule --
   which is also the check that a global `packageRules` entry
   reaches each repository's resolved config at all. Record the
   run's duration: it sizes `timeout-minutes` and tells us whether
   an hourly cadence is achievable at all.
2. **Write path, before anything is disabled.** The probe step from
   2a fails the job naming any repository whose `permissions.push`
   is not true; record its result here. Then canary on this
   repository alone: `gh workflow disable renovate.yml` here, tick
   one held update on this repository's dependency dashboard, and
   dispatch the central workflow for real with the filter input set
   to `shakenfist/development`. Confirm it pushed the branch,
   opened or updated the pull request, and rewrote the dashboard.
   Tick an update to a workflow file specifically, because pushing one
   needs a capability the probe cannot see: push permission is not the
   `workflow` scope, and most of what this fleet's Renovate proposes
   touches `.github/workflows/`. A canary that only moved a
   requirements pin would leave that untested.
3. **Disable the rest.** `gh workflow disable renovate.yml` in each
   of the other 22. The full list, `development` included, is here
   so that the rollback is mechanical: `actions`, `agent-python`,
   `andris`, `client-python`, `client-python-k3s`,
   `client-python-ova`, `clingwrap`, `cloudgood`, `development`,
   `divergulent`, `hunkydory`, `images`, `instar`, `kerbside`,
   `kerbside-client`, `kerbside-patches`, `library-utilities`,
   `occystrap`, `ryll`, `sfui`, `shakenfist`,
   `uncalibrated-sextant`, `visual-digest-rust`.

   **Not before the rust-toolchain pin lands, due about
   2026-11-18.** `renovate-runner.json` disables every dependency
   in `.github/workflows/renovate.yml` and
   `templates/renovate/renovate.yml` for the central runner, so
   the `dtolnay/rust-toolchain@stable` reference in both --- held
   by `minimumReleaseAge` because the `stable` branch moved on
   2026-11-15 --- can only ever be pinned by each repository's own
   hourly workflow, which step 2 and this step disable. Disabling
   first strands those two references unpinned, and
   `PLAN-workflow-ref-trust.md` step 3a's definition-of-done grep
   then fails on them. Either wait for that pin, or narrow this
   rule to drop `templates/renovate/renovate.yml` first, which
   phase 3 has to do anyway. The reasoning is in that plan's step
   3a record.

   **Lifted 2026-11-21.** The pin was declined rather than waited
   for. Adding the `with: toolchain: stable` block it needed to the
   template would have made 16 fleet copies of `renovate.yml` drift
   from it. So this repository's root `renovate.json` keeps
   `dtolnay/rust-toolchain` unpinned in exactly these two files
   until this plan's phase 3 deletes them, and disabling strands
   nothing that is owed. The record is at the end of that plan's
   phase 3.
4. **Turn the schedule on, and watch a full day.** The workflow
   lands with `workflow_dispatch` only, so this step is a pull
   request of its own adding the `schedule` block its header comment
   records, and it comes after step 3 because a schedule with the
   copies still enabled is the duplicate pull request race. Then
   watch: dependency dashboards update, no duplicate branches appear,
   and open Renovate pull requests are adopted rather than reopened. Record how many times
   `renovate-fleet.yml` ran that day, each run's duration, and the
   longest gap, against the Situation's median of 4 hours 47
   minutes and longest of 9 hours 35 minutes. A pass approaching 60
   minutes reduces the cadence silently, because GitHub keeps one
   pending run per concurrency group and cancels the older one. If
   the central runner is not clearly better, stop and investigate
   before phase 3: this plan's promise on security latency rests on
   Renovate actually running. **Not met, 2026-11-23**: see *Record*.
5. **Move the trigger to the conductor.** Added after step 4's
   watch day failed; it has its own section below.

Rollback is `gh workflow enable` per repository and disabling the
central workflow. Nothing in this phase deletes a file elsewhere in
the fleet.

#### Cutover step 5: the conductor replaces GitHub's cron

Planning effort: high. It moves the trigger of the fleet's most
privileged workflow into another repository, and both halves are
live the moment they merge.

Added on 2026-11-23, after step 4's watch day failed the gate step 4
set itself. *Record* has the reading. In short, GitHub started 5
runs in the 35.6 hours after #432 turned the hourly schedule on: a
median gap of 6h58m, against the per-repository copies' 4h47m. The
longest gap, 7h53m, beat the copies' 9h35m, and every run that did
start succeeded. Step 4 said that a central runner which is not
clearly better stops phase 3 until the cause is understood. The
cause is GitHub's delivery, not the runner. The five runs were
created at minutes :27, :44, :22, :01 and :54 past the hour, so
moving the cron off the hour -- the remedy the Situation proposed
-- made no measurable difference to how many hourly events GitHub
delivered or when.

The operator chose on 2026-11-23 to have the private-ci conductor's
periodic tier trigger the runner. That tier already dispatches
scheduled work in shakenfist, kerbside and instar by
`workflow_dispatch`, and is described in private-ci's
`docs/periodic-ci.md`. He chose to remove the GitHub cron entirely
rather than keep it as a fallback (decision 10). He also asked for
a criterion here that forbids GitHub scheduled workflows fleet-wide
in favour of the conductor. That is a master plan of its own,
written after this step, and this step is its pilot. That is why
decision 9 builds a general target kind rather than a special case
for Renovate.

Phase 2 lands in five pull requests rather than the two *Execution
notes* records: #396, #400 and #432 so far, then 2e's pull request
in private-ci, then this step's own pull request here. Its `Merged`
cell names all five, the private-ci one as `private-ci <sha> (#pr)`.

##### What the survey found

Read against private-ci `master` at `8c5281e` and this repository's
`main` at `91df689`. Numbered on from the phase's own survey.

7. **To the conductor, a producer *is* the bug fixer.** Four places
   assume there is only one. `dispatch_now()`
   (`conductor/periodic.py:720`), behind the dashboard's "Run fix
   now" button, dispatches every producer in turn until one
   succeeds. The bug fixer card takes its budget and verdict from
   the first producer it meets in `TARGETS` (`conductor/main.py:711-729`).
   A producer's verdict is never escalated
   (`conductor/periodicoutcomes.py:235`). The dashboard's starved
   list skips producers (`conductor/templates/dashboard.html:4013`).
   Registered as a second producer, Renovate would be started by
   "Run fix now" whenever a fixer run was in flight, could take
   over the fixer's card depending on list order, and could fail
   for ever without reaching the Action Items panel.

8. **Each of those comparisons names its kind, so a third kind
   slips past all of them.** `main.py:713` and `:723` test
   `== 'producer'`, `periodic.py:750` skips `!= 'producer'`, and
   `periodicoutcomes.py:235` escalates `!= 'producer'`. On the
   test-lane side, `periodic.py:227` builds the provisioner's
   match set from `== 'test'`, and `periodic.py:431` gives deadline
   service only to `== 'test'`. A target of a new kind is therefore
   left alone by the fixer's code, escalated, never matched as a
   queued lane and never owed deadline service, with no edit to any
   of those lines. The one exception is the admission loop
   (`periodic.py:500` onwards). It branches on producer, and
   everything else falls through to the test-lane gates. A new kind
   would reach those gates with no claim and no lifetime, so it
   needs its own branch.

9. **The dispatch sends no inputs, and that now matters.**
   `github.dispatch_workflow()` (`conductor/github.py:3465`) accepts
   inputs, but `_enforce_dispatch()` passes none. The conductor's
   runs therefore get the defaults `renovate-fleet.yml` declares:
   `dry_run: write-for-real` and an empty `autodiscover_filter`. A
   default changed to `lookup` would turn every conductor dispatch
   into a dry run, and every one of those runs would show green.
   Nothing tests the defaults today.

10. **The conductor dispatches as the operator, on `main`, which
    is how every cutover dispatch so far was made.**
    `_enforce_dispatch()` uses the default `OPERATOR` identity
    (`conductor/github.py:115`). Steps 1 to 3 were all dispatched
    with the operator's own credential on `main`, and the job's
    `if:` and the `renovate-write` deployment rule accepted them.
    The conductor's dispatch is the same request from a different
    process, so the environment needs no change.

11. **This survey did not establish whether the collector sweeps
    this repository's runs.** `periodicoutcomes` matches a dispatch
    to its run only among the runs the collector has stored, and
    the collector reads workflow runs at `collector.py:1731`.
    Whether that sweep includes `shakenfist/development` was not
    traced. If it does not, the dispatches still happen, but each
    one settles as `unknown` after 48 hours, so a red Renovate never
    reaches the panel decision 9 relies on. Step 2e checks, and
    reports rather than widening the sweep, because the sweep's cost
    is charged against the conductor's REST budget. **Answered by
    step 2e: it does.** `list_workflow_runs_and_jobs()` sweeps every
    unarchived repository in the organisation (private-ci
    `conductor/github.py:5397-5426`), reading each one's 40 most
    recent runs (`:4199`, `:4329-4341`). On 2026-11-23, 40 runs of
    this repository spanned about 10.8 hours. A run that finishes
    after 40 newer runs have been created settles as `unknown`;
    private-ci's `docs/periodic-ci.md` now says so.

12. **Nothing outside the conductor would notice it stopping.**
    `conductor/heartbeat.py` is a systemd watchdog: it restarts a
    loop that has wedged, but nothing notices a host that is down.
    Today a dead conductor delays periodic test lanes. After this
    step it also stops dependency updates and security pull requests
    for 23 repositories, and the overdue gauge that would report the
    gap is published by the process that has stopped. That is
    recorded under *Risks*, not solved here. It is the first question
    the fleet-wide plan has to answer, because that plan makes every
    scheduled job in the fleet depend on the same process.

13. **This repository's half is small.** It is:
    - the `schedule` block (`.github/workflows/renovate-fleet.yml:36-37`);
    - the header comment that explains the block (`:1-26`);
    - `test_the_workflow_runs_hourly_at_minute_23` in
      `scripts/tests/test_renovate_fleet_workflow.py:101`, and that
      module's docstring (`:21`);
    - *The central runner* in `docs/audits/renovate.md` (`:36-57`);
    - `ARCHITECTURE.md`'s entry for the runner (`:32-36`).

Corrected at source in the commit that adds this section: the
Success criteria's first bullet, which said "an hourly schedule";
phase 4's "Renovate runs on `static` per day", wrong since this
phase moved the runner to a VM; the Definition of done item that
asserts the cron; and the Future work item on top-of-hour events,
which this measurement supersedes.

##### Decisions

9. **A third target kind, `scheduled`, rather than a second
   producer.** Registering a second producer means auditing the
   four places that assume a producer is the fixer (survey item 7),
   and the fleet plan would face the same audit dozens of times over. A
   new kind passes all of them untouched by construction (item 8)
   and adds one branch to the admission loop. A `scheduled` target:
   - is admitted when `min_interval_seconds` has elapsed and no run
     of it is queued or in progress, and on nothing else: no claim,
     no room, no dwell, no morning boundary and no daily cap;
   - counts towards the overdue gauge;
   - is escalated, so a red run reaches the Action Items panel and
     `conductor_periodic_consecutive_failures`;
   - is never in the provisioner's match set, so its job queues as
     ordinary work, exactly as a cron-triggered run did;
   - is never dispatched by "Run fix now".

   That is the shape a cron replacement needs. The argument against
   is a third path through the admission loop. Neither existing path
   changes, though, and the new one is the simplest of the three.

10. **No GitHub cron, not even as a fallback.** This is the
    operator's decision, taken on 2026-11-23. The case for keeping
    a sparse fallback cron is survey item 12: a conductor outage
    would then degrade to today's cadence rather than to nothing.
    The case against, which the operator took, is threefold. GitHub's
    cron is the thing just measured as unreliable. A fallback would
    hide a conductor outage behind a cadence that looks merely poor.
    And the fleet plan is going to forbid the trigger anyway. The
    cost is accepted in writing under *Risks*.

11. **Hourly floor, two-hour deadline.** `min_interval_seconds:
    3600`, so a run starts an hour after the last dispatch, or when
    the run in flight ends if that is later. `deadline_seconds:
    7200`, so the target counts as overdue after two hours without
    a dispatch. The dashboard's starved list fires at twice the
    deadline, so it shows the target at four hours: before the
    runner has slipped back to the cron's median gap of 4h47m. The
    minute-23 offset goes too, since it only ever meant something
    to GitHub's scheduler.

12. **The tests pin what the conductor relies on.** The cron
    assertion is replaced, not just deleted. One test asserts that
    the workflow has no `schedule` trigger and still accepts
    `workflow_dispatch`. A new test asserts the dispatch defaults,
    `dry_run: write-for-real` and an empty `autodiscover_filter`,
    because a bare dispatch is the only kind the conductor makes
    (survey item 9).

13. **private-ci lands and deploys first, and this repository
    second.** In between, the cron and the conductor both trigger
    the same workflow. That overlap is harmless: the concurrency
    group serialises the runs, and the conductor will not dispatch
    while a cron run is queued or in progress. The other order
    leaves the fleet with no trigger at all until the conductor
    deploys. Merging this step's pull request is therefore gated on
    seeing one conductor dispatch (cutover step 5.1).

14. **This repository describes the conductor only from the
    outside.** This repository is public and private-ci is not.
    `docs/audits/renovate.md` says:
    - that the private-ci conductor's periodic tier dispatches the
      runner, and how often;
    - what stops when the conductor is down;
    - where its own documentation is.

    It names no host, says nothing about deployment, and does not
    restate the tier's admission rules.

##### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2e | high | opus | private-ci worktree | Work in the private-ci worktree the management session creates from `master`, on branch `scheduled-target-kind`; private-ci's default branch is `master`, not `main`. Read `docs/periodic-ci.md` and the module docstring of `conductor/periodic.py` first. Add a third target kind, `'scheduled'`, with exactly the semantics of decision 9 in shakenfist/development's `docs/plans/PLAN-renovate-cadence.md` (phase 2, *Cutover step 5*). The only new branch is in the admission loop in `periodic.py` (from about line 500, beside the producer branch): cadence alone, after the interval floor that already applies to every target, with no claim, room, dwell, morning-boundary or daily-cap gate, and with the existing in-flight check in `_enforce_dispatch()` still applied. Document the kind in the module docstring's numbered admission rules (about lines 36-60) and in the `TARGETS` field comment (about lines 103-130). Then add the target: `owner: 'shakenfist'`, `repo: 'development'`, `workflow: 'renovate-fleet.yml'`, `ref: 'main'`, `kind: 'scheduled'`, `min_interval_seconds: 3600`, `deadline_seconds: 7200`. Give it a comment saying what it is and why it is cadence-only: security pull requests wait on it, and its job runs on a VM rather than as a tiered test lane. Do not cite the development plan or its phases in code or comments; this repository forbids plan references in source. Do not trust the survey: open each comparison listed in survey item 8 and confirm it still reads as stated, then run `git grep -n "kind"` over `conductor/` (Python and `templates/dashboard.html`) for any read of a target's kind that the survey missed. Check that the periodic card in `dashboard.html` (about line 2521) renders a row with no `claim`, `lifetime_seconds` or `dispatches_24h`, and fix it if it does not. Find out whether the collector's workflow-run sweep (`collector.py` about line 1731) covers `shakenfist/development` (survey item 11). Report the answer with file and line, and do **not** widen the sweep: that is a REST-budget decision for the management session. Tests go in `conductor/tests/test_periodic.py`; follow `_producer()` (about line 69) with a `_scheduled()` helper, and cover: admitted with a job queued, no room, the dwell unmet and the morning boundary near; refused inside the interval floor; refused with a run in flight on the enforce path; absent from the match set; never owed deadline service; counted by the overdue gauge; escalated in `periodicoutcomes`; never dispatched by `dispatch_now()`, including with a producer in flight; the bug fixer card reads the producer even when a `scheduled` target comes before it in `TARGETS` (in `test_main.py` if that is where the card is tested); and the real `TARGETS` entry holds the values above. Update `docs/periodic-ci.md`: the `kind` row of the Targets table, the Admission rule list, a *Scheduled targets* section beside *Producers*, and any sentence that a third kind makes false. Run private-ci's own checks (`pre-commit run --all-files` and its tox environments, per its `AGENTS.md`). Mutation-test with the `mutation-check` skill, each mutant under `timeout`: remove the admission branch, let `dispatch_now()` take a scheduled target, drop it from the overdue count, and make it unescalated. Report every mutant and the test that killed it. Do not push and do not open a pull request. |
| 2f | medium | sonnet | none | Work in this worktree. The private-ci conductor now dispatches `.github/workflows/renovate-fleet.yml` hourly, so the GitHub cron goes; see decisions 10, 12 and 14 in phase 2's *Cutover step 5* of `docs/plans/PLAN-renovate-cadence.md`. Delete the `schedule:` block (lines 36-37). Rewrite the header comment (lines 1-26) to say the conductor's periodic tier in shakenfist/private-ci dispatches the workflow about once an hour, and why: GitHub delivered roughly one hourly event in seven over the two days the schedule was on, at whatever minute it chose. Keep the warning that re-enabling any per-repository `renovate.yml` races this runner, and drop what applied only to cron. Source must not cite a plan or narrate its history (`plan-source-references`, `plan-history-in-source`), and `docs/` and `ARCHITECTURE.md` must not cite phase numbers (`plan-phase-references`), so state the measurement itself rather than where it was taken. In `scripts/tests/test_renovate_fleet_workflow.py`, replace `test_the_workflow_runs_hourly_at_minute_23` (line 101) with a test asserting that no `schedule` trigger exists and `workflow_dispatch` does, and add one asserting the dispatch defaults `dry_run: write-for-real` and `autodiscover_filter: ''`, with a comment saying the conductor sends no inputs, so these defaults are what every scheduled run gets. Update the module docstring (line 21). Rewrite *The central runner* in `docs/audits/renovate.md` (lines 36-57) per decision 14: who dispatches the runner and how often, that the runner stops if the conductor is down, and a pointer to private-ci's `docs/periodic-ci.md`, with no host names and none of the tier's internals. Keep the ordering history short. Update `ARCHITECTURE.md`'s runner entry (lines 32-36) to say the runner is dispatched by the conductor, since this adds a dependency between components. Do not edit the plan. Mutation-test three mutants, each under `timeout`: the cron restored, the `dry_run` default changed to `lookup`, and the filter default made non-empty. Restore from copies and report each killing test. Then run `pre-commit run --all-files` and `python3 scripts/audit-check.py --repo-path . --repo-name development`, and report every verdict that differs from `main`. |

##### Cutover step 5

Operator-gated, like steps 1 to 4. Nothing here is done without an
explicit yes.

1. **The conductor first.** Merge 2e's private-ci pull request and
   confirm the conductor has deployed it. The periodic card lists
   `renovate-fleet.yml`, and within the hour `gh run list --workflow
   renovate-fleet.yml` shows a `workflow_dispatch` run under the
   operator's identity that nobody started by hand. Record its run
   id. If a cron run is in flight at the time, record that the
   conductor waited for it, as decision 13 predicts.
2. **Then the cron goes.** Merge this step's pull request (2f).
3. **Watch at least 24 hours from that merge.** Record:
   - every run of `renovate-fleet.yml`: its event, its queue time
     and its job duration. Any `schedule` event means the cron
     survived;
   - the median and the longest gap between run starts;
   - that dependency dashboards were updated and no duplicate
     branch appeared.

   The gate is met when the median gap is at most 90 minutes and no
   gap is longer than 4h47m, the median of the copies it replaces.
   The reading needs a day after this pull request merges, so under
   `plan-phase-landing` it is recorded in the close-out commit that
   opens phase 3. If it fails, phase 2 stays `In progress` and phase
   3 is not planned.

Rollback: remove the target from private-ci's `TARGETS`, and revert
this step's pull request to restore the cron. Turning `ENFORCE` off
instead would stop every periodic target, not just this one.

##### Risks and mitigations

* **A conductor that is down means no Renovate, and nobody is
  told.** This follows from survey item 12 and decision 10, and the
  operator accepted it on 2026-11-23. While the conductor runs, a
  stalled runner reaches the starved list after four hours (decision
  11). When the conductor is down, the only signal is Renovate
  pull requests and dependency dashboards going quiet, and nothing
  watches for that. The remedy belongs to the fleet plan and is its
  first question. Future work records it here as well, so that it
  is not lost if that plan is slow to arrive.
* **A maintenance drain holds the tier.** No dispatches happen
  while the cluster is drained. The `vm` pool the runner needs is
  drained at the same time (*Execution notes*, 2026-11-14), so a
  cron-triggered run would have waited too. This adds no cost.
* **A dry-run default would make every run green and change
  nothing.** Decision 12's test fails any change to the dispatch
  defaults.
* **The conductor dispatches with the operator's credential.** If
  that token loses access to this repository, each pass records
  `the dispatch failed` on the dashboard and tries again on the next
  cycle, and the overdue gauge rises after two hours. So it is
  visible on the dashboard, though not elsewhere.
* **REST budget.** A due target costs one active-runs call and one
  dispatch, about 24 of each a day. Dispatch is gated on the
  periodic reserve, so an exhausted budget delays the runner rather
  than failing it.
* **Merging in the wrong order leaves no trigger at all.**
  Decision 13 and cutover step 5.1 gate it. The management session
  also records this step's pull request in `plan-blockers` as
  waiting on 2e's.

##### Definition of done

* private-ci's `TARGETS` holds exactly one entry for
  `shakenfist/development` `renovate-fleet.yml`, with `kind:
  'scheduled'`, `ref: 'main'`, `min_interval_seconds: 3600` and
  `deadline_seconds: 7200`, and a test asserts each value.
* A `scheduled` target is admitted with a job queued, no room, the
  dwell unmet and the morning boundary near. It is refused inside
  its interval floor and while a run is in flight. Each property has
  a test, and each test kills a named mutant.
* `dispatch_now()` never dispatches a `scheduled` target, the bug
  fixer card reads the producer whatever the order of `TARGETS`, and
  a `scheduled` verdict is escalated, each asserted by a test.
* private-ci's `docs/periodic-ci.md` describes the kind, and no
  sentence in it still says the bug fixer is the only target outside
  the test lanes.
* `grep -n 'schedule:' .github/workflows/renovate-fleet.yml` prints
  nothing. Tests assert both that the trigger is absent and what
  the dispatch defaults are.
* Every hit for `git grep -n -i conductor -- docs/audits
  ARCHITECTURE.md .github` describes what the tier does for this
  runner or points at private-ci's documentation. None names a host.
* *Record* holds the run id of the first conductor dispatch
  (step 5.1). The close-out holds the 24-hour reading (step 5.3),
  with the gate stated as met or not met.
* `pre-commit run --all-files` passes in both repositories, and
  `audit-check.py` reports no verdict here that is worse than on
  `main`.

##### Back brief

Before step 2e, back brief the operator on decisions 9 and 11. The
admission semantics of a new conductor kind are cheap to propose and
expensive to change once the fleet plan has registered dozens of
targets of that kind. Confirm also the private-ci branch name, and
that its pull request lands before this one.

#### Record

**Both validator modes were proved against a broken file, not
read** (2026-11-13). With an unknown key added to
`renovate-runner.json`, the global-mode hook fails and names the
file:

```
Validate Renovate global configuration...................................Failed
- hook id: renovate-config-validator
- exit code: 1

INFO: Validating renovate-runner.json as global config
ERROR: Found errors in configuration
       "file": "renovate-runner.json",
       "errors": [
         {
           "topic": "Configuration Error",
           "message": "Invalid configuration option: notARealRenovateKey"
         }
       ]
```

And with `"autodiscover": true` placed in the root `renovate.json`,
the repository-mode hook still rejects it, which is the property
`--no-global` was added for and the one the split had to preserve:

```
Validate Renovate repository configuration...............................Failed
- hook id: renovate-config-validator
- exit code: 1

INFO: Validating renovate.json as repo config
 WARN: Found errors in configuration
       "file": "renovate.json",
       "warnings": [
         {
           "topic": "Configuration Error",
           "message": "The \"autodiscover\" option is a global option
             reserved only for Renovate's global configuration and
             cannot be configured within a repository's config file."
         }
       ]
```

Both files were restored from copies and verified byte-identical
afterwards.

**Mutation testing, 29 mutants and 29 killed** (2026-11-13). Each
step kept a runnable script under this session's scratchpad rather
than improvising: `step-2a/mutate.py` for the push probe (12,
including the two defects its first run found -- an assertion reading
the wrong stream, and the bytecode cache serving a previous
same-length mutant), `step-2b/mutate.py` for the hook split (4: the
exclude removed, the global pattern widened, the global hook deleted,
and the global hook given `--no-global`), and `step-2c/mutate.py` for
the preset and the local rule (10: three file drops from the Node
rule, three from the note, and four ways of breaking the local rule).
The management session killed three more by hand, one per step, to
check the scripts rather than trust them: disabling the probe's
empty-set guard fails `test_a_token_that_finds_nothing_to_manage_fails`
with "the token can push to all 0 repositories", removing the hook's
`exclude:` makes the runner config matched by both hooks, and dropping
`renovate-fleet.yml` from the Node rule fails the whole-set assertion.
No mutant timed out.

**The probe answers its own question, but not open question 1 yet**
(2026-11-13). Run read-only against the organisation with an operator
token rather than `RENOVATE_TOKEN`, it reported 23 of the
organisation's repositories as managed and named exactly the 23 in
*Cutover* above, then reported the token could push to all of them.
That confirms the derivation and the 23, and says nothing about what
`RENOVATE_TOKEN` is or can reach: the probe's first real run, in
cutover step 1, is what answers that.

The cutover's four steps each produce a measurement that phase 3 and
phase 4 depend on, so they are recorded here as they happen: the dry
run's discovered repository list and duration, the push-permission
probe's output, the canary's branch and pull request, and the
watch day's run count, durations and longest gap. The answer to
open question 1 -- what `RENOVATE_TOKEN` actually is and what it can
reach -- is recorded here too, since the probe is the first thing in
this plan that establishes it rather than assuming it.

**Cutover step 1: the fleet is discovered exactly, and a pass costs
under ten minutes** (2026-11-14). Two dispatches, both read-only.
Run 43543283195 with `dry_run: lookup` took 9m22s for the job and
7m34s for Renovate itself; run 43557260170 with `dry_run: full`,
which computes every branch and pull request without writing any of
them, took 10m05s and 8m12s. Either leaves a wide margin under the
50-minute `timeout-minutes`, so the number set in 2a stands. Nothing
here settles the cadence, though: a dry run writes nothing, so 8m12s
is a floor rather than the cost of a real pass, and the queue delay
recorded under *Execution notes* sits outside `timeout-minutes`
entirely. The Situation's measurement of the hourly cron still holds,
and has drifted slightly down: re-read on 2026-11-15, the last 100
scheduled runs of `renovate.yml` in `development`, `actions`,
`shakenfist` and `ryll` span 23 to 24 days each, every one succeeding
and none cancelled, which is about four of the 24 hourly crons a day
where the Situation measured five. Step 4's watch day is what
measures the cadence.

The probe ran first on both, with the job's own `RENOVATE_TOKEN`
rather than an operator token, and reported:

```
40 repositories in shakenfist, 23 of them managed by Renovate
  shakenfist/actions
  shakenfist/agent-python
  shakenfist/andris
  shakenfist/client-python
  shakenfist/client-python-k3s
  shakenfist/client-python-ova
  shakenfist/clingwrap
  shakenfist/cloudgood
  shakenfist/development
  shakenfist/divergulent
  shakenfist/hunkydory
  shakenfist/images
  shakenfist/instar
  shakenfist/kerbside
  shakenfist/kerbside-client
  shakenfist/kerbside-patches
  shakenfist/library-utilities
  shakenfist/occystrap
  shakenfist/ryll
  shakenfist/sfui
  shakenfist/shakenfist
  shakenfist/uncalibrated-sextant
  shakenfist/visual-digest-rust
the token can push to all 23 repositories
```

The 23 are the 23 in *Cutover* above, name for name, with nothing to
explain. Both runs predate step 2's canary and step 3, so every
per-repository `renovate.yml` was still enabled when the probe
reported, which is the second clause of the definition-of-done item
and the reason the probe runs before anything is disabled. The two
counts reconcile as 40 in the organisation, 10 of them archived, 30
autodiscovered by Renovate, 7 of those without a Renovate
configuration, 23 managed. Renovate logged each of the 7 as
`disabled-no-config` and onboarded none of them, so `requireConfig`
and `onboarding: false` behave as decision 5 intended. The probe's
own line is the weak part of that chain rather than its arithmetic:
the 40 it prints is the raw organisation listing, taken before the
archived filter in `managed_repositories()`
(`scripts/renovate-check-push.py:191`), so it invites a comparison
with Renovate's 30 that needs this paragraph to resolve. Filed as
issue #401 rather than fixed here, so that the probe's own log stops
needing a plan document to read.

**The global rule reaches every repository's resolved config, and
will close four pull requests** (2026-11-14, run 43557260170). The
`full` pass proposed no new branch anywhere -- the per-repository
copies have been running hourly and are current -- but it would
retire four pull requests that are already open:

| Repository | PR | Title |
|---|---|---|
| `actions` | 251 | Update dependency renovate to v44.227.4 |
| `andris` | 81 | Update dependency renovate to v44.227.4 |
| `client-python-k3s` | 171 | Update renovatebot/github-action action to v46.3.13 |
| `kerbside-client` | 24 | Update dependency renovate to v44.227.4 |

Each of the four changes `.github/workflows/renovate.yml` and nothing
else, which is exactly the file the temporary `packageRules` entry
disables. That is the proof decision 4 wanted, and a stronger one
than an absence of output: a global entry matched by `matchFileNames`
reaches each repository's resolved configuration, demonstrated by
Renovate retiring work it had already started on the strength of it.

The demonstration is positive for those four repositories and no
more. The other 19 had no pending `renovate.yml` update for the rule
to act on, so the run is consistent with the rule applying there
without evidencing it, and the 23 `Would ensure Dependency Dashboard`
lines show that each repository's configuration resolved rather than
that this entry matched in each. The first real pass, or a
debug-level log, is what would.

The four closures are intended and are recorded here so that whoever
finds them closed can see why. They update a workflow phase 3
deletes, so merging them would be churn. The canary in step 2 is
filtered to this repository and will not touch them; step 3 or step 4
is where they close. It also means the dependency dashboards in all
23 repositories would be rewritten by the central runner on its first
real pass, which the `full` pass shows as 23 `Would ensure Dependency
Dashboard` lines.

**`RENOVATE_TOKEN` lives in two places, and one managed repository
can see neither** (2026-11-15). Open question 1 asked where the token
lives and recorded that answering it needs `admin:org`, which the
planning session did not have. It is available now. There is an
organisation secret named `RENOVATE_TOKEN` with `selected`
visibility over 22 repositories, and an environment secret of the
same name on this repository's `renovate-write` environment, created
2026-11-13T04:27:19Z. No repository-level `RENOVATE_TOKEN` exists in
any of the 23 -- each was checked -- so phase 3's choice resolves to
restricting the organisation secret's repository list, with nothing
to delete per repository. Which of the two copies the fleet runner's
job received is not determinable from the run: both hold a working
token, and neither of GitHub's two secrets pages -- the concept page
and the how-to -- states a precedence between an environment secret
and an organisation secret of the same name, which is the same gap
*Two claims about the environment were wrong* records one level down.
**Corrected 2026-11-25, planning phase 3:** a third page does. The
*Secrets reference*
(<https://docs.github.com/en/actions/reference/security/secrets>,
under *Naming your secrets*) says "If a secret with the same name
exists at multiple levels, the secret at the lowest level takes
precedence", and its example ends with the environment-level secret
taking precedence over both. So the fleet runner's job receives the
`renovate-write` environment copy, and phase 3's survey item 6 takes
the consequence: the organisation secret can be deleted rather than
narrowed.

The 22 are not the 23. `uncalibrated-sextant` is managed by Renovate,
is not on the organisation secret's list, and has no repository
secrets of its own, so `secrets.RENOVATE_TOKEN` is empty there. Its
hourly `renovate.yml` has failed every scheduled run it has ever had
-- 12 of 12, from 2026-11-11T15:40:59Z, which is its whole run
history -- each ending `FATAL: Initialization error`
with `"You must configure a GitHub token"`. The other 22 each
succeeded on all of their last 10 scheduled runs. That correlation is
also the clearest evidence available that the per-repository
workflows read the organisation secret rather than something else:
every repository on the list works, and the one repository off it has
never worked. The cutover fixes it as a side effect, since the fleet
runner uses this repository's token and the probe reports it can push
to all 23, so the first real central pass would have been the first
dependency update `uncalibrated-sextant` ever had. It was fixed on
its own terms instead, on 2026-11-15 with the operator's approval:
the repository was added to the organisation secret's selected list
with the single-repository endpoint, taking it from 22 to 23, and the
selected set is now exactly the 23 Renovate manages. The run was not
forced, so its own cron is what proves it.

What is left is why it happened, and it is why issue #402 stays open:
nothing checks the organisation secret's selected list against the
set of repositories carrying a `renovate.json`. The probe is the
closest thing and cannot do it, because it reads `permissions.push`
for the token it already holds and cannot see secret visibility. The
next repository onboarded without being added to the list fails the
same way, silently. Both candidate homes for the check -- the
consistency audit's `renovate` criterion, or the probe itself --
need `admin:org`, which neither has today.

**Open question 1's read path is still open after both dry runs**
(2026-11-14). Neither pass mentions Dependabot or vulnerability alerts
at the default INFO log level, in either mode, so neither confirms nor
denies that the central runner reads them. Absence of a log line is
not absence of the fetch, and nothing here should be read as either.
Answering it needs debug logging, which the workflow does not expose
as an input; it is carried to phase 3 rather than left looking
settled.

**Cutover step 2: the write path works, and the global rule is now
proved positively** (2026-11-21). `development`'s own hourly
`renovate.yml` was disabled first -- `gh workflow disable` reports it
`disabled_manually` -- and it stays disabled going into step 3. The
update ticked on dashboard #58 was
`unpend-branch=renovate/renovate-44.x`, the `renovate` npm pin: a
workflow-file dependency, and the one available to tick, because it
is found by `custom.regex` and the CI-tooling group's `matchManagers`
never matched it, so unlike the actions it is not held to the monthly
window. Run 44862446166 then dispatched with `dry_run:
write-for-real` and `autodiscover_filter: shakenfist/development`:
8m54s wall, 6m54s of job, 5m16s inside Renovate, with two minutes of
queue against step 1's 2h37m and 27m, so the drained pool recorded
there was transient.

The probe ran first and reported the same 40 repositories, 23 managed
and `the token can push to all 23 repositories`, naming the same 23.
This run's probe came after `development`'s own workflow was
disabled, so it is step 1's two runs that satisfy the
definition-of-done clause about running before anything was disabled;
this one confirms the token has not changed. Renovate then logged
exactly one repository, `repository=shakenfist/development`, so the
filter input narrows as survey item 6 said, and logged both `Branch
created` and `PR created` for `renovate/renovate-44.x`.

The result is pull request #431, head `fe58da5`, opened by
`shakenfist-bot`, and it changes
`.github/workflows/renovate-fleet.yml` and nothing else: one line,
`PINNED_RENOVATE_VERSION` from 44.251.0 to 44.257.5. The same pin
exists in `.github/workflows/renovate.yml` and
`templates/renovate/renovate.yml` and was not touched. So the central
runner can push a file under `.github/workflows/`, which is the
capability the probe cannot see and the reason this step ticks a
workflow-file update rather than a requirements pin, and the
temporary rule holds on the write path as well as in a dry run. The
canary's own pull request merged as `d1cfabc` at 04:53, so the branch
the central runner pushed was mergeable on its own terms and the
fleet runner now installs the version it proposed.

The dashboard was rewritten at 03:50:40. The ticked entry moved to
*Open* against #431, the stale reference to #426 -- merged at 01:48,
while the dashboard still showed it open -- is gone, and
`.github/workflows/renovate.yml` and
`templates/renovate/renovate.yml` have disappeared from *Detected
Dependencies* altogether, where the local runner's dashboard listed
four github-actions and two regex dependencies for each. That is the
evidence step 1's record said it did not have. There, the `full` pass
could only show the rule acting on the four repositories that
happened to have a pending `renovate.yml` update, and the 23 `Would
ensure Dependency Dashboard` lines showed each config resolving
rather than this entry matching. Here the entry is visibly removing
dependencies from a repository that had no pending update in either
file.

Two smaller findings. `renovate/stability-days` was still pending on
#431 when it merged, and is pending now: ticking the dashboard box
forces creation without satisfying `minimumReleaseAge`, and because
`main` carries no branch protection -- `gh api
repos/shakenfist/development/branches/main/protection` returns
`Branch not protected`, which is what
`PLAN-protect-development-main.md` is about -- nothing stopped the
merge. So the age gate is advisory in this repository rather than
enforced, and a tick does get around it. That is worth knowing before
phase 3 starts ticking dashboards to drive the sweep. And
the run's one WARN is spurious -- Renovate warns at startup that the
default `gitAuthor` `renovate@whitesourcesoftware.com` is not
recommended, because `renovate-runner.json` sets none, but
`renovate-fleet.json` does and #431's commit is authored
`shakenfist-bot <bot@shakenfist.com>`. The local runner's last
scheduled run, 44832835607, emits the same line, so it belongs to the
template rather than to the central runner. Commits are unsigned
either way.

One consequence to carry into step 3: `development` now has no
scheduled Renovate at all, since its own copy is disabled and the
central runner has no schedule until step 4. Step 3 puts the other 22
in the same position, which is why step 4 follows it immediately
rather than after a pause.

**Cutover step 3: the fleet is disabled, and the first full write
pass costs ten minutes of job and twenty-one of queue** (2026-11-21).
The remaining 22 copies were disabled with `gh workflow disable`, and
all 23 were then verified by reading each workflow's state back
rather than trusting the command's exit status: 23 of 23 report
`disabled_manually`.

Run 44868757936 then dispatched `write-for-real` with no filter, so
the runner config's `shakenfist/*` governed: 32m11s wall, 10m33s of
job, and 9m03s inside Renovate over the 30 autodiscovered
repositories. Against the 50-minute `timeout-minutes` set in 2a that
is comfortable, and it is the first measurement of a real pass rather
than the dry floor of 8m12s step 1 recorded. The cadence risk is not
the job, though: 21m37s of that wall time was spent waiting for a VM,
which sits outside `timeout-minutes` and outside what the concurrency
group protects. An hourly cron that queues for 21 minutes and runs
for 11 still fits the hour, but with little margin, and GitHub keeps
only one pending run per concurrency group. Step 4's watch day is
what says whether that margin holds.

The pass was predicted before it ran, against the live state rather
than step 1's week-old list, and both halves held. Five Renovate pull
requests were open fleet-wide beforehand. The two whose change lay in
a file `renovate-runner.json` disables were retired:
`client-python-k3s#176` (`renovatebot/github-action` v46.3.14) closed
at 05:24:36 and `kerbside-patches#2136` (the `renovate` pin) at
05:29:19, both inside the run's window. The three ordinary dependency
pull requests were adopted rather than reopened -- `instar#947`,
`kerbside#734` and `kerbside#733` are still open and still carry the
previous evening's `updatedAt`, so the pass left them untouched.
Afterwards the only `renovate/` branches anywhere in the fleet are
those three, so nothing was duplicated.

Beside those two closures the pass wrote only two deletions, both
`Deleting orphan branch` for `renovate/renovate-44.x` in `instar` and
in `uncalibrated-sextant`: branches whose dependency now sits in a
disabled file and which had no pull request to retire. Step 1's
predicted four retirements -- `actions#251`, `andris#81`,
`client-python-k3s#171` and `kerbside-client#24` -- had all merged or
closed under the hourly copies in the week between, which is why the
prediction was re-derived rather than carried forward.

Eleven WARN lines, and every one is pre-existing rather than a
property of the central runner. The `gitAuthor` line is the same one
step 2 recorded. `Package lookup failures` appears for seven
repositories; in `shakenfist` it is `Can't find version matching
ec30cf07... for github-tags package stbenjam/skillsaw` in
`.pre-commit-config.yaml`, and that repository's own last hourly run
emits the same warning. `andris` reports three `Could not determine
new digest` warnings where its own last hourly run reports seven, the
difference being the `renovate.yml` dependencies the temporary rule
now disables. So on both counts the central runner is no worse, and
on one it is quieter.

Step 3 also brought on the condition `docs/audits/renovate.md` warns
about: with every copy disabled, nothing maintains the pins in
`templates/renovate/renovate.yml` any more, because the central
runner's temporary rule disables that path and the hourly copy that
used to read it through `renovate.json` is off. The warning assumes
the template outlives the copies, and under this plan it does not --
phase 3 deletes the template along with them -- so the rule is left
alone rather than narrowed, and those two files simply stop receiving
pins for the weeks before they go. The drift criterion is unaffected,
because it blanks pinned refs before comparing.

Open question 1 is still unanswered, and a real pass has now failed
to answer it. A full write pass over 23 repositories logs nothing
about Dependabot or vulnerability alerts at INFO -- the only
`security` matches in the log are npm deprecation notices from the
install step. It needs debug logging, as the dry runs already said,
and it stays carried to phase 3.

**Cutover step 4: the watch day was not met, because GitHub
delivered few of the hourly events** (2026-11-23). #432 merged at
06:45 UTC on 2026-11-21. The reading was taken at 18:21 UTC on
2026-11-22, 35.6 hours later, from `gh run list --workflow
renovate-fleet.yml`. That is longer than the day step 4 asked for.

| Run | Event | Created (UTC) | Run wall time |
|---|---|---|---|
| 44919281741 | schedule | 11-21 15:27 | 13m13s |
| 44953056269 | schedule | 11-21 21:44 | 15m46s |
| 44975765932 | schedule | 11-22 02:22 | 12m39s |
| 45010355511 | schedule | 11-22 10:01 | 13m11s |
| 45052043985 | schedule | 11-22 17:54 | 13m16s |

All five succeeded. The jobs of the first and the last, which were
read individually, took 10m43s and 10m51s; the rest of each run's
wall time was the wait for a VM. The gaps between starts were 6h17m,
4h38m, 7h39m and 7h53m, and the first run came 8h42m after the
merge.

| | Per-repository copies | Central runner |
|---|---|---|
| Runs a day | about 5 (Situation), about 4 (2026-11-15) | about 3.4 over 35.6 hours; 4 in the last 24 |
| Median gap | 4h47m | 6h58m |
| Longest gap | 9h35m | 7h53m |

Step 4's gate asked whether the central runner was clearly better.
It is not: its median gap is worse, and only its longest gap
improves. The job is not the constraint. Each run took about 11
minutes of an available hour and none queued for long. What is
missing is the runs themselves. Of the roughly 35 hourly events
due at minute 23, GitHub created five, at minutes :27, :44, :22,
:01 and :54. Moving off the top of the hour, which the Situation
proposed because GitHub documents the hour as its busiest time, did
not change the delivery rate the copies saw on `0 * * * *`. Cutover
step 5 replaces the trigger.

**Cutover step 5.1: the conductor's first dispatch came after the
cron's removal had merged, not before** (recorded on 2026-11-25, in
the close-out that opens phase 3). private-ci #321 merged at
19:18:49 UTC on 2026-11-22. GitHub's cron then created two more
runs, at 23:40 UTC on 2026-11-22 and 07:54 UTC on 2026-11-23, and
#440 (step 2f) merged at 09:25:27 UTC on 2026-11-23. The first
`workflow_dispatch` run after the cutover is 45147312399, created at
09:28:16 UTC, 2m49s after that merge, and every run since has been a
`workflow_dispatch` created 60.0 to 60.5 minutes after the one
before it. So decision 13's gate -- see one conductor dispatch
before merging 2f -- is not shown by this record, which shows the
reverse order. Nothing was lost by it: the gap from the last cron
run to the first dispatch was 1h34m, inside the cron's own median.
Attributing 45147312399 and its successors to the conductor rests
on that cadence rather than on the run record, which names
`mikalstill` as the triggering actor of every run, hand dispatches
and conductor dispatches alike, because the conductor dispatches
with the operator's identity (survey item 10). The conductor is
deployed by hand, so its deploy time is not in GitHub either.

**Cutover step 5.3: the gate is met** (read at 18:34 UTC on
2026-11-24, from the workflow runs and jobs API). In the 24 hours
from #440's merge:

| | Value |
|---|---|
| Runs | 24, every one `workflow_dispatch` and `success` |
| `schedule` events | none after 07:54 UTC on 2026-11-23 |
| Gap between dispatches | median 60.1 minutes, longest 60.5, shortest 60.0 |
| Queue, dispatch to job start | median 1.8 minutes, longest 3.8 |
| Job duration | median 11.0 minutes, longest 15.0, shortest 10.2 |

The gate asked for a median gap of at most 90 minutes and no gap
over 4h47m. Both hold by a wide margin: the longest gap is shorter
than the cron's median by a factor of almost five. Read on past the
window to 34 runs, the picture holds, with one exception worth
knowing: 45415169726, dispatched at 14:32 UTC on 2026-11-24, queued
22m43s for a VM, and the next dispatch still came an hour after the
previous *dispatch* rather than an hour after that run ended. The
floor is measured from dispatch, so a slow queue shortens the time
between two passes rather than stretching the cadence.

Dashboards were rewritten in 5 of the 23 repositories inside the
window (`actions`, `cloudgood`, `ryll`, `shakenfist`,
`uncalibrated-sextant`) and in 9 by the time of the reading.
Renovate rewrites a dashboard only when its content changes, so the
other 14 being untouched since 2026-11-21 or 2026-11-22 is what a
quiet week looks like rather than a sign that they were skipped;
all 23 were rewritten by the first full pass in cutover step 3. No
duplicate branch exists. The fleet holds seven `renovate/` branches,
six of them carrying one open pull request each; the seventh,
`uncalibrated-sextant`'s `renovate/rust-1.x`, belongs to its #36,
which merged at 01:12 UTC on 2026-11-23 and left the branch behind.

Phase 2 is therefore complete, and its five pull requests are in the
Execution table's `Merged` cell.

#### Execution notes

Dates are Australian Eastern Daylight Time (UTC+11).

**The back brief's four answers** (2026-11-13). Decision 2 stands: the
runner is an ephemeral VM in the `renovate-write` environment, with
the operator creating the environment himself, since it is a
repository setting rather than a file in a pull request. Naming it in
the workflow does not create that protection -- see *Two claims about
the environment were wrong* below -- so the restriction binds only
once the operator has configured it.

**Phase 2 lands in two pull requests, not one** (2026-11-13). GitHub
offers `workflow_dispatch` only for a workflow that exists on the
default branch, so none of the four cutover steps can run from the
phase branch. This pull request is steps 2a to 2d; the cutover runs
against `main` afterwards and its measurements land as a second pull
request on this phase. Four Definition of done items -- the dry run's
discovered list, the probe's output, the canary and the watch day --
are therefore unreachable in the first pull request and are checked
in the second.

**The probe derives its repository set rather than reading the dry
run's** (2026-11-13, step 2a). The phase's cutover text said the
probe checks "every repository the dry run discovered", which it
cannot know: the probe runs before Renovate, on every run. It
instead applies the rule `requireConfig: "required"` applies --
every unarchived repository in the organisation whose default branch
carries a `renovate.json` -- so it stays correct as repositories are
added and retired, where a list of 23 names would be a second copy
of the fleet. It reads only `renovate.json` of the longer list
Renovate accepts, and says so: covering that list partway would buy
confidence it has not earned, and the `renovate` audit criterion is
what keeps a repository from configuring Renovate another way
unnoticed.

**Two departures from the template in `renovate-fleet.yml`**
(2026-11-13, step 2a). The Rust and `hashin` setup steps are
unconditional, where the per-repository template guards each with
`hashFiles()`. The condition asks whether the workspace holds a
`Cargo.lock` or a `requirements*.txt`, and the repositories this
workflow manages are not in the workspace at all -- Renovate clones
them later -- so the guards would never fire and lock file refreshes
would fail at cutover for the fleet's Rust and hashed-requirements
repositories. The cost is a rustup install and a venv on every
hourly run. The probe also lives in `scripts/` rather than the
`tools/` the phase text named, because every Python file in this
repository is in `scripts/` and every shell script in `tools/`.

**`timeout-minutes` is bounded by the cadence, not the work**
(2026-11-13, step 2a). 50 minutes, because GitHub keeps one pending
run per concurrency group: a pass that regularly ran past the hour
would quietly halve the schedule rather than fail anything, and
timing out under the hour makes it loud. The number is provisional
until the cutover's dry run measures a full pass.

**The first review round caught a live-on-merge defect** (2026-11-13).
The workflow was written with its hourly `cron` in place, so merging it
would have started real fleet-wide runs at the next scheduled tick
while all 23 per-repository copies were still running -- the duplicate
pull request race this phase exists to avoid, which the runner's own
concurrency group cannot prevent because the racing instances are
different workflows in different repositories. The cause was in the
planning, not the implementation: rewriting this phase's Cutover
section dropped the master plan's original "enable the central
runner's schedule" step, so nothing remained to turn the schedule on
and it shipped already on. The workflow now lands with
`workflow_dispatch` only, the block to add is recorded in its header
comment, and cutover step 4 turns it on in a pull request of its own.

**Two claims about the environment were wrong** (2026-11-13). This
phase said the workflow was inert until `renovate-write` existed.
GitHub's documentation says the opposite: "Running a workflow that
references an environment that does not exist will create an
environment with the referenced name", and that environment "will not
have any protection rules or secrets configured". Naming an
environment therefore protects nothing by itself, and the main-only
deployment rule binds only once an administrator has configured it.
Whether a job naming an environment still inherits a repository-level
secret of the same name is not documented either way, so nothing in
this plan or the spec asserts it. Both pages now say the protection is
verified out of band before the cutover.

**The probe cannot see the capability that matters most**
(2026-11-13). `permissions.push` is not the `workflow` scope, and most
of what this fleet's Renovate proposes touches `.github/workflows/`,
so the probe can report that the token reaches all 23 repositories
while every workflow-file branch is refused. It is documented rather
than probed, and cutover step 2's canary now ticks a workflow-file
update specifically, which is what establishes the capability.

**The local weekly rule covers majors too** (2026-11-13, step 2c).
The rule decision 8 asks for gives `actions/checkout` and
`actions/setup-node` in `renovate-fleet.yml` the weekly window, and it
is not narrowed by update type, so it also takes those two actions out
of the preset's first-of-the-month major window: a local rule is
concatenated after the preset's and the later match wins. Kept rather
than narrowed, because it is the same thing decision 8 wants for the
same reason -- the most privileged workflow in the fleet should not be
the slowest to pick up a fix -- and neither action is automerged, so a
major still waits for a human. Nothing else in the fleet is affected,
because the rule names a file that exists only here.

**The validator hook fails between 2a and 2b, by design**
(2026-11-13). `renovate-runner.json` is a global configuration and
phase 1's hook validates every Renovate file as a repository
configuration under `--no-global`, so the hook fails from the moment
2a creates the file until 2b splits it in two. Measured and recorded
rather than worked around: renaming the file to dodge the hook's
derived pattern would have left the fleet's most privileged
configuration the one file nobody validates.

**The VM runner pool was drained under step 1** (2026-11-14). The
first dispatch queued at 01:54 and did not start until 04:21, because
sfcbr was drained for maintenance and nothing with the `vm` label was
answering. Three unrelated jobs were queued behind the same shortage
-- `Secret scan`, `Mermaid lint` and `Prune and import review marks`
-- so the cause was the pool rather than the labels or the
environment, and `secret-scan.yml` and `prune-reviews.yml` have used
the identical label set for months. The queued dispatch ran itself
when the pool returned; no retry was needed. Worth knowing for the
watch day in step 4: a drained pool does not fail the runner, it
delays it silently, and `timeout-minutes` does not cover queue time.

**The runner's Renovate version moved between step 1's two runs**
(2026-11-14). `lookup` ran 44.222.2 and `full` ran 44.227.4, because
#398 merged `ac1b8a6` between them, bumping
`PINNED_RENOVATE_VERSION` in `renovate-fleet.yml`. That is this
repository's own hourly copy finding the new file within hours of the
merge, which incidentally confirms the custom manager matches the
fleet runner as well as the template, but it means the two durations
differ in version as well as in mode.

**Cutover step 5's back brief** (2026-11-23). The operator confirmed
decision 9 as written: a `scheduled` kind with no daily cap. He
confirmed decision 11 as an hourly floor and a two-hour deadline. He
also authorised this session to push both branches and open both
pull requests once the definition of done passed. The private-ci
branch is `scheduled-target-kind`.

**Steps 2e and 2f ran in parallel** (2026-11-23). They write to
different repositories, and neither reads the other's output. 2f
landed as `cc40987` here and 2e as `38cd5c0` on the private-ci
branch. Review changed one thing in 2e, a rewrapped line in
`docs/periodic-ci.md`. It kept one extra: a line in private-ci's
`ARCHITECTURE.md` naming the third kind in the tier's inventory,
which would otherwise have been wrong. private-ci's suite needs a
`HOME` holding `.ssh/id_general.pub` on a host without one, because
`provisioner.py` reads that key at import. That is a property of
the suite, not of this change.

**The runner's VM is on the cluster** (2026-11-23, step 2e). The plan
said the runner's job queues as ordinary work rather than as a
periodic lane. That is right, but it left unsaid that
`[self-hosted, vm, debian-13, s]` is a VM the conductor provisions on
the cluster. So an hourly run takes cluster capacity without a
claim, exactly as each cron-triggered run did. That cost is
unchanged by this step. Private-ci's comments and documentation now
say so.

#### Risks and mitigations

* **Disabling a repository's own Renovate strands what the central
  runner is configured not to see.** `renovate-runner.json`'s
  temporary rule disables every dependency in
  `.github/workflows/renovate.yml` and
  `templates/renovate/renovate.yml`, so those two files are
  maintained only by the per-repository workflow this phase turns
  off. The live case is
  `PLAN-workflow-ref-trust.md`'s step 3a: its
  `dtolnay/rust-toolchain@stable` pin is held by `minimumReleaseAge`
  until about 2026-11-18, and disabling first means it never
  arrives. Mitigation: cutover steps 2 and 3 carry the date, and
  the alternative is to narrow the rule to drop the template path,
  which phase 3 has to do anyway --- the template outlives the
  deployed copies the rule exists for. **Lifted 2026-11-21:** that
  pin was declined, as cutover step 3 now records, so the live case
  is gone.

* **The VM image may not have what Renovate needs.** The
  `audit-write` job's comment says the image "has git and python3
  but not gh", and says nothing about Node. `actions/setup-node`
  supplies Node, and the template's own steps supply npm, Rust when
  a repository has a `Cargo.lock`, and `hashin` when it has a
  `requirements*.txt`, so a VM should be able to do everything a
  static runner does -- but it pays the whole install on every run.
  The dry run in cutover step 1 measures it. If an hourly cadence
  cannot be met on a VM, the fallback is a dedicated runner label
  rather than the shared-home static pool, and if neither is
  available the residual risk is recorded and accepted explicitly
  rather than by default. Checked by the management session against
  the dry run's duration, before step 3 disables anything.
* **A pass longer than an hour halves the cadence in silence.** One
  pending run per concurrency group means a slow pass drops runs
  without failing anything. The watch day counts runs rather than
  assuming them, and the success criterion is comparative: at least
  as often as the per-repository copies managed.
* **The token may not reach every repository.** Disabling 23
  workflows and then discovering the central runner cannot write to
  one of them leaves that repository unmanaged and quiet about it.
  The probe runs in the job, on every run and not just at cutover,
  and fails naming the repository; cutover step 2 runs it before
  step 3 disables anything.
* **The preset edits in 2c are live on merge.** They are
  fleet-wide the moment the pull request lands, like everything in
  `renovate-fleet.json`, and before the central runner is turned
  on. Both edits are conservative in that direction: adding a file
  to a disable rule can only remove update paths, and a
  `prBodyNotes` change only alters prose in a pull request body.
* **A branch could dispatch the organisation's push token.** The
  `renovate-write` environment deploying only from `main` is what
  prevents a branch that has edited this workflow from running it
  with `RENOVATE_TOKEN`; the workflow's own `if` on the ref is
  defence in depth, as `consistency-audit.yml` describes.

#### Definition of done

Every item is checked by the management session against the files
and the commands, not against a sub-agent's summary.

* `.github/workflows/renovate-fleet.yml` exists, carries top-level
  `permissions: contents: read`, names the `renovate-write`
  environment, runs on an ephemeral VM label, and contains no `uses:`
  of `renovatebot/github-action`. It landed triggering on
  `workflow_dispatch` and **nothing else**, because the absence of a
  `schedule` is what stopped the runner working the same repositories
  as the copies that were still running. Cutover step 4 added the
  minute-23 `schedule` its header comment recorded, once every copy
  was disabled, and the test moved with it: where it asserted that no
  `schedule` existed, it now asserts the cron itself, so a schedule
  silently moved to the top of the hour fails it. Cutover step 5
  removes the schedule again, once the private-ci conductor
  dispatches the workflow. The test then asserts that no `schedule`
  exists and what the dispatch defaults are, since the conductor
  sends no inputs.
  `scripts/tests/test_renovate_fleet_workflow.py` asserts each of
  these, including that no third-party action beside the token runs
  on a ref that can move.
* The workflow installs Renovate in a step whose `env:` does not
  mention `RENOVATE_TOKEN`, and the step that holds the token runs
  only what that step installed.
* `renovate-runner.json` exists with `autodiscover`,
  `autodiscoverFilter`, `requireConfig`, `onboarding` and the one
  temporary `packageRules` entry, and no other key.
* `pre-commit run --all-files` passes with both validator hooks
  present, and the `Record` holds two failing runs: the global-mode
  hook failing on a broken `renovate-runner.json` and naming it,
  and the repository-mode hook rejecting a global option placed in
  `renovate.json`. A hook that passed on either is the outcome
  these two items exist to exclude.
* Every file matching `^(.*/)?(\.renovaterc|\.?renovate[^/]*\.json[c5]?)$`
  in `git ls-files` is validated by exactly one of the two hooks,
  asserted by a test that derives the file set rather than listing
  it.
* `renovate-fleet.json`'s Node-major rule names all three
  workflow files, and a test asserts the whole set, so removing one
  fails it. The Renovate-major `prBodyNotes` entry names
  `renovate-fleet.yml` without having dropped the two files it
  already named.
* This repository's `renovate.json` gives `actions/checkout` and
  `actions/setup-node` in `renovate-fleet.yml` the weekly schedule,
  and no rule in it restates anything the preset holds.
* `python3 -m unittest discover -s scripts/tests -t scripts` passes
  with more tests than before, and `python3 scripts/audit-check.py
  --repo-path . --repo-name development` reports no verdict worse
  than it does on `main` today, which is `renovate` compliant and
  the two pre-existing failures `plan-source-references` and
  `review-coverage`.
* Mutation testing covers each of 2c's three preset and config
  changes, every mutant is killed, and the run names the expected
  string. The script's path and the count are in the commit
  message.
* The dry run's discovered repository list is recorded and equals
  the 23 in *Cutover*, or the difference is explained there.
* The push-permission probe's output is recorded for all 23
  repositories, and it ran before any workflow was disabled.
* No fact about the central runner is stated differently in
  `docs/audits/renovate.md` and `templates/renovate/README.md`, and
  neither page claims the per-repository workflow has been deleted,
  because phase 3 deletes it.
* The watch day's run count, durations and longest gap are
  recorded, and the comparison against the per-repository copies'
  median of 4 hours 47 minutes is stated as met or not met. It was
  not met (2026-11-23, in *Record*). Cutover step 5's own
  definition of done carries the item from there, and phase 2 is
  not complete until that reading meets its gate.

#### Back brief

Before step 2a, back brief the operator on decision 2, the runner
label, which is the one with a real counter-argument and the one
that cannot be changed cheaply afterwards. The argument against it
is that the 23 copies run on `[self-hosted, static]` today, so
`static` is no worse than the status quo, and that an ephemeral VM
pays a full Node and npm install every hour with nothing cached.
The argument for it is that this repository has already ruled that
a job holding a fleet-write token does not run on a pool sharing a
home directory, for a token that can write nothing, and that after
cutover this workflow is the single point through which every
repository in the organisation is written to. Confirm also that the
`renovate-write` environment may be created, since that is a
repository setting rather than a file in a pull request.

Then stop again before cutover step 3, the only irreversible-feeling
step in the phase: 22 repositories stop running Renovate for
themselves, and the central runner has to be known good first.
Report the dry run's discovered list against the 23, the probe's
output, the canary's result and the measured pass duration, and get
agreement before disabling anything beyond this repository.

### Phase 3: fleet sweep and audit change

Planning effort: high for the audit change, medium for the sweep.
The audit change reverses what the `Renovate` criterion means -- a
file it requires today becomes a failure -- and files issues across
the fleet at the next weekly audit run (Sunday 18:00 UTC), which is
high effort by this repository's own rule (see Planning effort
below). The sweep that follows is mechanical once its rules are
fixed, and decision 6 fixes them. Planned on 2026-11-25; the
original scope below was written on 2026-10-03 and corrected at
source where the survey found it stale, as the survey says item by
item.

#### Scope

**In this repository**, in one pull request that lands first: the
`Renovate` check requires `renovate.json` to extend
`local>shakenfist/development:renovate-fleet`, and treats a
`.github/workflows/renovate.yml` as a failure, because if it were
re-enabled it would race the central runner. It also fails a
`renovate.json` that sets, at its top level, a key the preset sets
(decision 3). Tests in `scripts/tests/test_packaging_renovate.py`
(split out of the packaging test module by #414) cover each new
failure as well as the pass. The issue title `Renovate` does not
change, since it is the fleet-wide idempotency key. Rewrite
`docs/audits/renovate.md` with the check: its "What we check" list
and rationale describe the required `extends`, the forbidden
workflow file and the forbidden overrides, where today they say the
workflow must exist, runs hourly and is copied verbatim.
Specifications are written for human review and nothing regenerates
them, so a spec left alone would contradict the check and every
issue it files. The rewrite stales the spec's review mark in
`REVIEWS.md`; that is expected, and per the phase-landing rules
above it is left for `prune-reviews` rather than edited here.
Delete `templates/renovate/renovate.yml` and this repository's own
`.github/workflows/renovate.yml`, and settle every reference to
either outside `docs/plans/` -- survey item 4 lists them as they
stand today. The same pull request adds a `log_level` input to
`renovate-fleet.yml`, so that open question 1's last half can be
answered (decision 8).

Before the audit change merges, preview its verdicts against fresh
clones of the fleet with `audit-check.py` and
`audit-manage-issues.py --dry-run`, as `docs/consistency-audits.md`
describes, and record here that the count matched survey item 3. A
`workflow_dispatch` of `consistency-audit.yml` has no dry-run mode
-- it files issues for real -- so it is only for starting the sweep
after the merge without waiting for Sunday. The `Renovate` issues
the audit files are the sweep's work queue. That is deliberate: it
is the fleet's issue-driven `consistency-fix` pattern, and a
transitional check that accepted both forms would spare about 15
issues at the price of letting the sweep stall unnoticed.

**In each of the 22 other managed repositories**, one pull request:
delete `.github/workflows/renovate.yml`, and reduce `renovate.json`
to `extends` plus the repository's own rules, by the rules in
decision 6. Keep every lockstep group, every `constraints.python`,
every `rangeStrategy`, every custom manager and every rule about a
path or package only that repository has. Lockstep groups stay
local throughout this plan, because the `renovate-lockstep-groups`
criterion (`RenovateLockstepGroups` in
`scripts/audit/checks/packaging.py`) reads only the local file and
would report a group moved into the preset as missing; promoting
them is under Future work. Two repositories need extra care:
`instar` (its custom manager for `cargo install` pins and its frozen
release base image) and `shakenfist` (the most groups). In
`kerbside`, keep its `shellcheck` group and its `tox.ini` custom
manager, which are repository-specific.

**Then, in this repository again**, in a closing pull request once
every sweep pull request has merged: remove the runner's temporary
`renovate.yml` package rule from phase 2; record the first Saturday
after the sweep, when the preset's schedules govern the fleet for
the first time, showing that the weekly window got runs and that
the week's pending updates opened or were visibly held by the
preset's pull request limits; and record the `RENOVATE_TOKEN`
change below.

When no copy of `renovate.yml` remains, `RENOVATE_TOKEN` is needed
only by `renovate-fleet.yml`, which reads the `renovate-write`
environment's copy (survey item 6). The organisation secret is then
read by nothing, so it is deleted rather than narrowed, which takes
the credential's exposure from 23 repositories to one environment.

Out of scope: promoting lockstep groups into the preset (Future
work, #88); splitting the Renovate criteria out of `packaging.py`
(#434, decision 4); the fleet-wide criterion against GitHub
`schedule:` triggers (Future work); and `images`' disabled
Dependabot alerts (survey item 9).

#### What the survey found

Read against `main` at `8d89c14` and the fleet's default branches on
2026-11-25. Numbered on from phase 2's survey, which ended at 13.
The false claims are corrected at their source in the scope above in
the commit that adds this section, so the prose no longer disagrees
with the tree.

14. **Every managed `renovate.json` overrides the preset's schedule,
    and the original brief would have kept it.** All 22 set
    `"schedule": ["after 9pm"]` at the top level. A repository's own
    configuration is resolved after the presets it extends, so a
    local `schedule` wins over the preset's `on saturday`, and a
    sweep that only deleted "rules that restate the preset" -- the
    original brief's words -- would have left the fleet on a daily
    window with this plan's whole volume reduction quietly undone.
    `after 9pm` does not restate the preset: it contradicts it. The
    other top-level keys every repository carries --
    `assignees`, `dependencyDashboard`, `gitAuthor`,
    `minimumReleaseAge`, `rollbackPrs` and a `pre-commit` block --
    do restate it, and 19 also carry
    `customManagers:githubActionsVersions` in `extends`, which the
    preset extends too. Decision 3 makes the criterion catch the
    whole class rather than trusting the sweep to.

15. **The `shellcheck-py` versioning rule is in 15 repositories, not
    one.** The original scope said to delete it from `kerbside`;
    `PLAN-renovate-shellcheck-audit.md` found that fourteen more had
    acquired it since. They are `actions`, `andris`, `clingwrap`,
    `cloudgood`, `divergulent`, `hunkydory`, `instar`, `kerbside`,
    `kerbside-client`, `kerbside-patches`, `library-utilities`,
    `occystrap`, `ryll`, `sfui` and `uncalibrated-sextant`. Each
    restates the preset and goes. Three more rules are
    near-universal and go for the same reason: `automerge: false`
    for minor, patch and digest updates (all 22), the Renovate-major
    `prBodyNotes` entry (19), and the Node-major disable naming
    `.github/workflows/renovate.yml` (19), whose file the same pull
    request deletes.

16. **The newly failing count is 15, not "every managed
    repository".** The `renovate` column of `docs/audits/compliance.md`
    today: `development` compliant; `images` and `private-ci` N/A,
    `images` because `REPO_OVERRIDES` scopes it to `eol-distro` alone
    (`scripts/audit/repo.py:143`); six non-compliant
    (`agent-python`, `client-python`, `client-python-k3s`, which
    still run `renovatebot/github-action`; `client-python-ova`, for
    the shellcheck-py rule; `cloudgood` and `divergulent`, for
    template drift), each with an open `Consistency: Renovate`
    issue; and 15 compliant. The audit change fails all 21 in scope
    except `development`: 15 newly, each with a new issue, and six
    whose existing issues are rewritten. `images` gets a sweep pull
    request with no issue behind it, since nothing audits its
    Renovate configuration.

17. **The `renovate.yml` references have moved since 2026-10-10.**
    `git grep -n 'renovate\.yml'` outside `docs/plans/`,
    `docs/audits/compliance.md`, `REVIEWS.md` and `.vscode/` (the
    last two are the review tooling's, healed by `prune-reviews`)
    finds, beyond the two files themselves:
    - `scripts/audit/checks/packaging.py:189-300` and the
      `Renovate` check at `:552-620` -- the drift and container
      machinery this phase removes;
    - `scripts/tests/test_packaging_renovate.py`, which reads the
      template at module level (`:23`) and uses it as a fixture in
      five classes, plus three tests naming the two paths
      (`:386`, `:446`, `:576`, `:653`);
    - `scripts/tests/test_registry.py`:
      `test_this_repository_runs_renovate_verbatim` (`:1288`), the
      `ConvergedTemplatesTest` docstring (`:1242`), and
      `test_a_pinned_rust_toolchain_states_its_toolchain`
      (`:1296-1335`), which reads all three workflow files;
    - `scripts/tests/test_npm_dependencies.py:270`, a fixture named
      `renovate.yml` whose docstring calls it "this template";
    - `scripts/tests/test_ci_workflows.py:3296`, a fixture name only,
      which stays;
    - `renovate-fleet.json:64-83`, the Node-major rule's
      `matchFileNames` and the Renovate-major note, both naming the
      two doomed files beside `renovate-fleet.yml`;
    - `renovate-runner.json:11-14`, phase 2's temporary rule;
    - `renovate.json:27-30`, the `dtolnay/rust-toolchain` rule, and
      its `customManagers` entry (`:38-49`), which exists only to
      read `# renovate:` comments under `templates/` -- once the
      template goes, `git grep -l '# renovate:' -- templates`
      finds only `templates/renovate/README.md`, which is prose;
    - `.pre-commit-config.yaml:44-50`, the actionlint comment that
      cites `renovate.yml` and counts "the ten copy-directly
      templates";
    - `ARCHITECTURE.md:52`, the supporting-workflows list;
    - `templates/renovate/README.md`, most of whose 357 lines
      describe the workflow, and `docs/audits/renovate.md`, the
      spec, both rewritten by step 3d;
    - `.github/workflows/renovate-fleet.yml:4` and `:22`, header
      comments on the copies.

    The mention in `docs/audits/scheduled-workflow-health.md` is
    history and stays.

18. **The organisation secret can be deleted, not narrowed.** The
    original scope said to restrict the organisation secret to this
    repository. Phase 2's record established that there are two
    copies of the token, the organisation secret and an environment
    secret on `renovate-write`, and said no GitHub page stated which
    one a job sees. One does: the *Secrets reference* says the
    lowest level takes precedence, environment over repository over
    organisation (phase 2's *Record*, corrected in this commit). So
    the fleet runner reads the environment copy, and after the sweep
    nothing reads the organisation secret at all. Deleting it also
    ends #402, which asks for a check of the organisation secret's
    selected list: there will be no list. The only `RENOVATE_TOKEN`
    reads in this repository are the two in `renovate-fleet.yml`
    (`:138` and `:249`, both inside the environment's job) and the
    one in `.github/workflows/renovate.yml`, which this phase
    deletes.

19. **`packaging.py` is exactly at its size-ratchet baseline.** 2,493
    lines against an entry of 2,493 (`tools/source-size-baseline:14`),
    and #434 asks for the Renovate criteria to be split out of it.
    This phase removes the drift and container machinery, about 170
    lines, and decision 3 adds back fewer, so the file shrinks. The
    ratchet's own `--update` lowers the entry, which keeps the gain.

20. **Nothing can answer open question 1 from the alerts
    themselves.** Every managed repository has zero open Dependabot
    alerts today, so the absence of `[SECURITY]` pull requests
    proves nothing either way, and Renovate's INFO log does not
    mention the alerts query. A debug log is the only reading left,
    and the workflow has no input for one.

21. **`images` has Dependabot alerts disabled** -- the alerts API
    answers 403 "Dependabot alerts are disabled for this
    repository". It is managed by Renovate, so the central runner
    reads no alerts there whatever its token can do, and it never
    gets a security pull request. Out of scope here, since enabling
    alerts is a repository setting rather than anything this plan
    changes, and recorded under Future work.

22. **Twelve of the 22 carry a `PUSH-AUDIT.md`, and 14 have a merge
    queue.** The push-audit block says a phase that lands in another
    repository is audited against that repository's default branch
    as it lands. Twelve runbooks of four judgment sub-agents each,
    over the same two-file change, is the cost decision 7 weighs.
    Default branches are `develop` everywhere except `actions` and
    `cloudgood`, which use `main`.

Two claims held: `ConvergedTemplatesTest.test_this_repository_runs_renovate_verbatim`
is where the scope said, and the audit's issue title, spec path and
template directory (`scripts/tests/test_metadata.py:137-139` and
`:348`) need no change, because `templates/renovate/` survives,
holding `renovate.json` and its README.

#### Decisions

1. **One pull request here first, then 22 sweep pull requests, then
   one closing pull request here.** The audit change has to land
   before the sweep so that its issues are the work queue; the
   temporary runner rule has to outlive every sweep pull request,
   because a repository not yet swept still carries a disabled
   `renovate.yml` whose dependencies would otherwise be proposed;
   and the organisation secret cannot go until the last copy that
   reads it has gone. Phase 3's `Merged` cell therefore names 24
   pull requests, the 22 as `<repo> <sha> (#pr)`.

2. **The criterion requires the preset and nothing it implies.** The
   `Renovate` check keeps `renovate.json` must exist, and becomes:
   `.github/workflows/renovate.yml` must not exist; `renovate.json`
   must extend the preset; and decision 3's override rule. The three
   findings that test what the preset carries -- the version-comment
   preset, the `pre-commit` manager and the `shellcheck-py`
   versioning rule -- go from the check, together with their
   repository-level wrappers, the drift comparison and the container
   detection, because a repository that extends the preset has all
   three by construction. Their *config-level* predicates stay:
   `RenovateFleetPresetTest` runs them over the real
   `renovate-fleet.json`, and once the criterion trusts the preset to
   carry those three properties, those tests are what hold the
   preset to them. This retires the finding
   `PLAN-renovate-shellcheck-audit.md` added a week ago, which is
   the reason to say so plainly: that plan's goal, a hook revision
   Renovate cannot look up being reported rather than discovered,
   still holds, because a repository without the rule is now a
   repository without the preset, and the test that the preset
   carries the rule is unchanged.

3. **The criterion also fails a top-level key the preset sets.** This
   is the decision most likely to be argued with. Survey item 14
   found every repository overriding the preset's schedule, and the
   override is invisible -- the configuration validates, Renovate
   runs, and the cadence simply does not change. A check that
   accepted it would report a repository as on the preset while the
   preset governs nothing there. The rule is read, not listed: the
   check loads `renovate-fleet.json` from the audit's own checkout,
   the way it reads the template today (`read_within` on
   `DEVELOPMENT_ROOT`), and fails any key in the repository's
   `renovate.json` that also appears at the preset's top level,
   other than `$schema`, `extends` and `packageRules`, naming each.
   The argument against is that it forbids a repository from
   deliberately setting, say, its own `minimumReleaseAge`. A
   repository that genuinely needs a different policy can still say
   so in a `packageRules` entry scoped to what it is about, which is
   reviewable and narrower; the top level is the fleet's.
   `RenovateReducedConfigTest.test_neither_config_restates_a_fleet_key`
   already holds this repository's own configs to the same rule.

4. **No split of `packaging.py` in this phase.** #434 asks for the
   Renovate criteria to move to their own module. Doing that in the
   same pull request as a change of meaning puts a 600-line move
   beside a 100-line semantic diff, which is the review #434 itself
   declined to ask for. This phase shrinks the file and lowers the
   ratchet entry; #434 gets a comment with the new size and stays
   open.

5. **This repository moves its runner-facing references, not just
   its deletions.** The preset's Node-major rule and its
   Renovate-major note name only `renovate-fleet.yml` from the first
   pull request, not the last, which is safe because phase 2's
   temporary runner rule already disables every dependency in the
   fleet's remaining `renovate.yml` files. The runner rule itself is
   narrowed to `.github/workflows/renovate.yml` in the first pull
   request, since `templates/renovate/renovate.yml` is gone, and
   removed in the last. The root `renovate.json` loses both its
   `dtolnay/rust-toolchain` rule and its `templates/` custom manager
   (survey item 17), so it holds one local rule, the weekly window
   for the runner's own two actions.

6. **The sweep's rules are fixed here, so 22 pull requests are one
   review.** Each repository's `renovate.json` is reduced by these
   rules and no others:
   - **Drop** every top-level key the preset sets: `schedule`,
     `assignees`, `dependencyDashboard`, `gitAuthor`,
     `minimumReleaseAge`, `rollbackPrs`, `pre-commit`; and
     `customManagers:githubActionsVersions` from `extends`.
   - **Drop** the `packageRules` entries that restate the preset or
     name the deleted workflow: `automerge: false` for minor, patch
     and digest; the Renovate-major `prBodyNotes` entry; the
     Node-major disable naming `.github/workflows/renovate.yml`; and
     the `shellcheck-py/shellcheck-py` versioning rule.
   - **Keep** everything else verbatim and in order, including
     deprecated spellings such as `matchPackagePatterns` in
     `client-python-k3s` and `occystrap`: migrating them is not this
     plan's change, and Renovate still migrates them on read.
   - **Add** `local>shakenfist/development:renovate-fleet` as the
     first entry of `extends`, keeping any other preset there
     (`andris` keeps `customManagers:dockerfileVersions`).

   The rules are applied by a script, not by hand, so that one
   reading of the script and its output table is the review of all
   22; any entry the rules do not classify is a stop, not a
   judgement call. `ryll` and `visual-digest-rust` keep their
   `lockFileMaintenance` schedule, `before 4am on monday`, which the
   preset's `timezone` now reads in Sydney time rather than UTC --
   recorded in each pull request's description rather than changed.

7. **The sweep pull requests get their repository's mechanical
   push-audit tier each, and one judgment audit between them.** The
   push-audit block wants each to be audited against its own
   repository as it lands. Every one is the same two-file change by
   the same script, so four judgment sub-agents in each of the
   twelve repositories with a runbook would read the same diff
   twelve times. Instead each pull request runs its repository's
   `pre-commit` and the runbook's diff-level greps, and the judgment
   tier runs once, in this repository, over the script and the
   staged table of all 22 diffs, before any is pushed (step 3f).
   Phase 5 cites that audit. This is a departure from the block's
   letter, and its spirit is kept by auditing the one artefact that
   produced every diff; the back brief asks for agreement on it.

8. **Answer open question 1 with a debug pass, then delete its
   log.** Step 3c adds a `log_level` input to `renovate-fleet.yml`,
   a choice of `info` and `debug` defaulting to `info` and passed as
   `LOG_LEVEL`, so the conductor's bare dispatches are unchanged and
   the defaults test (phase 2, decision 12) gains a line. After the
   first pull request merges, one dispatch with `debug` and the
   filter set to `shakenfist/development` reads what Renovate logs
   about the vulnerability-alerts query: alerts fetched (even none),
   or a permission error. This repository is public, and a debug
   log carries the resolved configuration and every request URL;
   Renovate redacts the token, but the run's logs are deleted with
   `gh run delete` once the answer is recorded, since nothing else
   needs them.

9. **Two canaries before the rest.** `actions` first -- no merge
   queue, default branch `main`, and the repository whose workflows
   the fleet consumes -- then `instar`, the most complex
   configuration. After each merges, the next hourly pass must
   succeed and that repository's dependency dashboard must show the
   preset's schedule taking effect (updates reported as awaiting
   schedule rather than opened). Then the other 20, in any order the
   merge queues allow.

#### Steps

Steps 3a to 3d write files in this repository and are sub-agent
work; they are one pull request. Step 3e and everything under
*Sweep* are run by the management session with the operator
watching, since they change live state in other repositories, and
nothing there is pushed, opened or deleted without an explicit yes.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Rewrite the `Renovate` check in `scripts/audit/checks/packaging.py` (`class Renovate`, about line 552) per decisions 2 and 3 of phase 3 in `docs/plans/PLAN-renovate-cadence.md`. The check fails when `renovate.json` is missing; when `.github/workflows/renovate.yml` exists (say that the fleet runner in shakenfist/development manages the repository and a re-enabled copy races it, so delete the file); when `renovate.json` does not extend `RENOVATE_FLEET_PRESET` (use `extends_fleet_preset`); and when `renovate.json` sets a top-level key that `renovate-fleet.json`'s top level also sets, other than `$schema`, `extends` and `packageRules`, naming every such key in one finding. Read the preset from the audit's own checkout with `read_within(DEVELOPMENT_ROOT, ...)`, exactly as the check reads the template today, and raise `ToolFailure` if it cannot be read or parsed -- an unreadable preset is our outage, never the audited repository's `fail` (see *What each status means* in `docs/consistency-audits.md`). A malformed `renovate.json` must not abort the run: follow what `renovate_config()` (about line 820) already does and test it. Report all findings at once so one fix closes the issue. Remove what the new check no longer calls: `RENOVATE_CONTAINER_ACTION`, `RENOVATE_VERSION_COMMENT_PRESETS` only if nothing else reads it, `RENOVATE_WORKFLOW_TEMPLATE`, `RENOVATE_PIN_RE`, `RENOVATE_USES_RE`, `RENOVATE_BUMPED_RES`, `RENOVATE_DRIFT_SHOWN`, `renovate_workflow_drift`, `_without_renovate_bumps`, `_quote_line`, `renovate_workflow_runs_container`, the repository-level wrappers `renovate_reads_version_comments`, `renovate_manages_pre_commit` and `renovate_versions_shellcheck_py`, `uses_shellcheck_py_hook` and `uses_remote_pre_commit_hooks` -- but **keep** the config-level predicates `renovate_config_reads_version_comments`, `renovate_config_manages_pre_commit` and `renovate_config_versions_shellcheck_py`, and say in a comment above them that `RenovateFleetPresetTest` runs them over the real preset, which is what now holds the preset to carrying those properties. `git grep` each removed name across `scripts/` first and keep anything another check imports. Rewrite the comment above `RENOVATE_FLEET_PRESET` (about line 40-54), whose warning about preset edits becomes more true, not less. In `scripts/tests/test_packaging_renovate.py`: remove the module-level read of `templates/renovate/renovate.yml` (line 23) so that the tests no longer depend on a file step 3b deletes; delete the classes that test removed findings (`RenovatePreCommitManagerTest`, `RenovateShellcheckVersioningTest`, `RenovateVersionCommentsTest`, `RenovateWithoutDockerTest`, the drift tests near line 759) but first move any test of a kept config-level predicate into `RenovateFleetPresetTest` or a small new class, so no kept function loses its coverage; and add a class for the new check covering: pass (preset extended, no workflow, no overridden key); workflow present; extends missing; extends present but not a list; one overridden key; several overridden keys named together; `$schema`, `extends` and `packageRules` never reported; a key the preset does not set (for example `lockFileMaintenance`, `constraints`, `customManagers`) passing; all findings reported together; `renovate.json` missing; malformed `renovate.json`; and the preset unreadable raising. Follow the `CheckTestCase` convention in *Testing a change* of `docs/consistency-audits.md`. Then run `python3 tools/source-size-ratchet.py --update` and keep its lowered `tools/source-size-baseline` entry for `packaging.py`; never raise one. Mutation-test with the `mutation-check` skill, each mutant under `timeout`: drop the workflow finding, drop the extends finding, drop the override finding, report only the first overridden key, report `extends` as an override, and swallow the preset read failure. Keep the script under this session's scratchpad and report every mutant with the test that killed it. Do not touch the spec, the templates or any other file; 3b and 3d own them. |
| 3b | medium | sonnet | none | Delete `.github/workflows/renovate.yml` and `templates/renovate/renovate.yml` with `git rm`, then settle every reference listed in survey item 17 of phase 3 in `docs/plans/PLAN-renovate-cadence.md`, except `docs/audits/renovate.md`, `templates/renovate/README.md` and `ARCHITECTURE.md`, which step 3d owns. In `scripts/tests/test_registry.py`: delete `test_this_repository_runs_renovate_verbatim`; narrow `test_a_pinned_rust_toolchain_states_its_toolchain` to `.github/workflows/renovate-fleet.yml` alone, rewriting its docstring and failure message so neither mentions the deleted files; correct the `ConvergedTemplatesTest` docstring (about line 1242). In `.pre-commit-config.yaml`, rewrite the actionlint comment (about lines 44-50) so it no longer cites `renovate.yml`, and count the copy-directly templates that remain with `ls` rather than subtracting one. In `scripts/tests/test_npm_dependencies.py` (about line 262-276), keep the test, since what it proves about `npm install --prefix` is still true of `renovate-fleet.yml`, but rename the fixture and fix the docstring's "this template". In `renovate-fleet.json`: the Node-major rule's `matchFileNames` becomes `.github/workflows/renovate-fleet.yml` alone, and the rule's `description` and the Renovate-major `prBodyNotes` entry name only that file; update `test_the_real_preset_disables_node_majors_in_the_workflows` and `test_the_renovate_major_note_names_each_workflow_to_move` in `scripts/tests/test_packaging_renovate.py` to assert the whole new set, so adding a file back fails them. In `renovate-runner.json`, narrow the temporary rule's `matchFileNames` to `.github/workflows/renovate.yml` and rewrite its description to say it lasts until the fleet's last copy is deleted; update `test_the_runner_suppresses_the_renovate_workflows_and_nothing_else`. In the root `renovate.json`, delete the `dtolnay/rust-toolchain` rule and the whole `customManagers` block, after confirming `git grep -l '# renovate:' -- templates` lists only `templates/renovate/README.md`; then delete `test_only_the_doomed_renovate_copies_keep_rust_toolchain_unpinned` and change `test_the_root_config_holds_two_local_rules` to one, renamed to match. In `.github/workflows/renovate-fleet.yml`, rewrite the two header comments (lines 4 and 22) about the per-repository copies so they say what is true once this pull request has merged: the copies are being deleted, and a re-enabled one races this runner. Source must not cite a plan or narrate its history (`plan-source-references`, `plan-history-in-source`). Do not touch `REVIEWS.md` or `.vscode/`. Run `git grep -n 'renovate\.yml' -- ':!docs/plans' ':!docs/audits/compliance.md' ':!REVIEWS.md' ':!.vscode'` at the end and report every remaining hit with why it stays. |
| 3c | low | sonnet | none | Add a `log_level` input to `.github/workflows/renovate-fleet.yml`'s `workflow_dispatch`: `type: choice`, options `info` and `debug`, default `info`, with a description saying the conductor sends no inputs so scheduled runs log at `info`. Pass it to the Renovate step's `env:` as `LOG_LEVEL: ${{ inputs.log_level \|\| 'info' }}`, beside the two existing inputs and in their style; check how those two are passed and match it. Extend `test_the_dispatch_defaults_are_the_scheduled_run` in `scripts/tests/test_renovate_fleet_workflow.py` (about line 116) to assert the `log_level` default is `info`, and add a test that the input offers exactly `info` and `debug`. Mutation-test two mutants under `timeout`: default `debug`, and a third option added. Run `pre-commit run --all-files`. |
| 3d | high | opus | none | Rewrite the Renovate documentation around the preset and the central runner. Read phase 3's decisions in `docs/plans/PLAN-renovate-cadence.md` and the new `Renovate` check from step 3a first. `docs/audits/renovate.md`: rewrite *What we check* to the four rules of the new check, with the rationale for each, especially why a top-level key the preset sets is a failure (survey item 14: every repository overrode the schedule); delete *The workflow is the template's*; move what is still true of the workflow -- *Renovate without Docker*, and from `templates/renovate/README.md` the *Lock files*, *The version pin* and *Installing without the token* sections -- under *The central runner*, rewritten to describe `.github/workflows/renovate-fleet.yml` rather than a per-repository copy, since every fact there is now a fact about the one runner; and rewrite *The central runner* so it no longer says the copies are disabled but present -- say the criterion fails a repository that still carries one. `templates/renovate/README.md`: it now describes one file, `renovate.json`, which extends the preset; keep *Customisation* and its subsections, *Prerequisites* and *Projects using these templates*, delete *This template is being retired*, and point at the spec for everything about the runner rather than restating it. Keep each fact in one place and point at it from the other; no fact about the runner is stated on both pages. `ARCHITECTURE.md:52`: drop `renovate.yml` from the supporting-workflows list, since the runner already has its own entry above it. `AGENTS.md` changes only if a convention changed, and none did. `docs/` and `ARCHITECTURE.md` must not cite phase numbers (`plan-phase-references`). Run `tools/mermaid-lint.sh` if you touch a diagram, and `pre-commit run --all-files`. |
| 3e | high | opus | none | Management session, not a sub-agent. Preview the audit change against the fleet before the pull request is marked ready. Clone each of the 23 managed repositories fresh into the scratchpad at its default branch, run `python3 scripts/audit-check.py --repo-path <clone> --repo-name <repo>` from this worktree for each, then `scripts/audit-manage-issues.py --dry-run` over the results as `docs/consistency-audits.md` describes -- never without `--dry-run`. Expect, per survey item 16: `development` compliant, `images` N/A, and the other 21 non-compliant, of which 15 would get a new `Consistency: Renovate` issue and six would have theirs rewritten; and every finding naming `schedule` among the overridden keys. Any other count is a finding to explain before merging, not a number to record. Then stage the sweep, below, and record both here. |

#### Sweep

Operator-gated, in this order. The management session runs each
step and stops where a step says so.

1. **Merge the first pull request.** Then, with the operator's yes,
   dispatch `consistency-audit.yml` to file the issues now rather
   than at Sunday's run, and record the issue numbers against the
   table in step 3.
2. **Answer open question 1** (decision 8): dispatch
   `renovate-fleet.yml` with `log_level: debug` and
   `autodiscover_filter: shakenfist/development`; record what the log
   says about vulnerability alerts, quoting the line; then delete the
   run with `gh run delete` and record that it was deleted.
3. **Stage all 22** (step 3f). A script in the scratchpad clones
   each repository, applies decision 6's rules to `renovate.json`,
   deletes `.github/workflows/renovate.yml`, and writes a table:
   repository, each key or rule dropped with the rule that dropped
   it, each kept rule, and any entry the rules do not classify. An
   unclassified entry stops the script for that repository. For each
   staged clone, run the repository's own `pre-commit run
   --all-files` where it has a config, `renovate-config-validator`
   on the new `renovate.json` (the development hook's installed
   environment will do), and `audit-check.py`, which must report
   `renovate` and `renovate-lockstep-groups` compliant. Then the
   judgment tier of this repository's `PUSH-AUDIT.md`, run once over
   the script and the table (decision 7). **Stop**: the operator
   reads the table and the audit before anything is pushed.
4. **Canaries** (decision 9). `actions`, then `instar`: branch,
   commit, push and open the pull request with `Fixes
   shakenfist/<repo>#<n>` for its `Renovate` issue, with the
   operator's yes for each. Merging stays with the operator. After
   each merges, read the next hourly pass's result and that
   repository's dashboard, and record both.
5. **The other 20**, the same way, `images` with no issue to close.
   Record each pull request's merge commit for the `Merged` cell as
   it lands.
6. **The closing pull request** (step 3h, below the table), once the
   fleet has no `renovate.yml`: verify that with the API rather than
   trusting the merge list, then remove the temporary rule.
7. **Delete the organisation secret**, with the operator's yes, since
   it needs `admin:org`: `gh secret delete RENOVATE_TOKEN --org
   shakenfist`. The next hourly pass must succeed, probe included;
   record its run id. Close #402 with a comment saying why it no
   longer has a subject.
8. **Watch the first Saturday** (Sydney) after the last sweep pull
   request merges, and record it in the closing pull request.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3f | medium | sonnet | none | Write the staging script for *Sweep* step 3 into this session's scratchpad, not the repository. Input: the 22 repository names in phase 2's *Cutover* step 3, minus `development`. For each, clone `shakenfist/<repo>` at its default branch (`develop` except `actions` and `cloudgood`, which use `main`) into the scratchpad, `git rm .github/workflows/renovate.yml`, and rewrite `renovate.json` by decision 6's rules in phase 3 of `docs/plans/PLAN-renovate-cadence.md` and no others. Match each dropped `packageRules` entry by its matchers and effect, never by its description text, and print, per repository, every dropped and kept entry with its reason. An entry no rule classifies stops that repository with the entry printed. Preserve key order and the file's indentation, and end the file with a newline. Then for each clone run its own `pre-commit run --all-files` if `.pre-commit-config.yaml` exists (report failures that predate the change separately, by running it on an untouched clone too), the Renovate validator on the new `renovate.json`, and `python3 scripts/audit-check.py --repo-path <clone> --repo-name <repo>` from the development worktree, reporting the `renovate` and `renovate-lockstep-groups` verdicts. Commit nothing, push nothing. Report the table and every verdict. |
| 3g | medium | sonnet | none | Management session, not a sub-agent: *Sweep* steps 4 and 5. Per repository, commit the staged change with the subject `Move Renovate onto the fleet preset.`, push the branch `renovate-fleet-preset`, and open the pull request, each with the operator's yes. The body says what was dropped and why, in a sentence per rule, links this plan's decision 6 by permalink, notes the `lockFileMaintenance` timezone shift where it applies, and closes the repository's `Renovate` issue. |
| 3h | medium | sonnet | none | The closing pull request, in a new worktree from `main` once *Sweep* step 6's API check shows no `renovate.yml` in any managed repository. Remove the temporary `packageRules` entry from `renovate-runner.json`, leaving no `packageRules` key, and update `RenovateRunnerConfigTest` (`test_the_runner_config_holds_exactly_these_keys` and `test_the_runner_suppresses_the_renovate_workflows_and_nothing_else`, which becomes a test that the runner config holds no package rules). Rewrite the sentence in `docs/audits/renovate.md` that describes the temporary rule. Mutation-test one mutant under `timeout`: the rule restored. The management session adds the Saturday watch, the secret deletion and the closing records to this plan in the same pull request. |

#### Record

**Step 3e: the preview matches survey item 16 exactly**
(2026-11-25). Each of the 23 managed repositories and `private-ci`
was cloned fresh at its default branch's tip, and `audit-check.py`
run over each from this branch at `0935894`; `development`'s result
was taken from this branch rather than from its `main` clone, since
`main` still carries the workflow this branch deletes. The
`renovate` verdicts: `development` passes, `images` and
`private-ci` are N/A, and the other 21 fail. Every one of the 21
fails on all three of the new findings at once -- the workflow
present, the preset not extended, and the same seven top-level keys
overridden, `assignees`, `dependencyDashboard`, `gitAuthor`,
`minimumReleaseAge`, `pre-commit`, `rollbackPrs` and `schedule` --
so survey item 14's `schedule` is named in all 21.

`audit-manage-issues.py --dry-run` over those results would create
15 `Consistency: Renovate` issues and update the body of six:
`agent-python#159`, `client-python#437`, `client-python-k3s#158`,
`client-python-ova#64`, `cloudgood#26` and `divergulent#150`. That is
the count survey item 16 predicted, so nothing stops the merge.

#### Execution notes

Dates are Australian Eastern Daylight Time (UTC+11).

**The back brief's answers** (2026-11-25). The operator agreed
decisions 2 and 7 as written and had already kept decision 3 when
the plan was committed. He authorised this session to push the
branch and open the first pull request once its definition of done
had been checked. Everything under *Sweep* stays gated as written.

**Steps 3a to 3d ran in sequence, and 3e beside 3d** (2026-11-25).
All four write this worktree and 3b and 3d read what 3a produced,
so none ran in parallel. Step 3e's clones and audit runs are
read-only and live outside the worktree, and 3d changed only
documentation, so the preview ran while 3d was still writing.

**Three departures, all kept** (2026-11-25). Step 3a's
`source-size-ratchet.py --update` also lowered `test_registry.py`'s
entry from 1,582 to 1,581, its length on `main`; the tool only ever
lowers, so it stays. Step 3b found
`test_renovate_installs_without_the_token` reading the deleted
template and pointed it at `renovate-fleet.yml`, where two steps
hold `RENOVATE_TOKEN` -- the probe and the Renovate run -- so it now
asserts that no holder runs `npm` or `npx` rather than that there is
exactly one holder. And the management session, reviewing 3d,
rewrote five comments in `renovate-fleet.yml` that described state
that no longer exists: two about the per-repository copies and
template, and three left over from phase 2's cron, among them a
timeout still called provisional until a dry run measured a pass.

**A test failure that did not reproduce** (2026-11-25, step 3c).
The 3c sub-agent reported four failures in `test_audit_update_docs`
and said they failed on a clean tree too. The management session's
run of the whole suite straight afterwards, 2,827 tests, passed, as
did every run since, so nothing was changed for them.

#### Risks and mitigations

* **A sweep that keeps a local override changes nothing, quietly.**
  Survey item 14 is the live case: every repository's `after 9pm`
  would have outlived a sweep briefed to drop only restatements.
  Mitigation: decision 3 makes the criterion fail any top-level key
  the preset sets, so a missed override is an open issue, and the
  staging script's `audit-check.py` run must report `renovate`
  compliant before anything is pushed. The management session checks
  both in the 3f report.
* **The audit change files 15 issues at once.** That is the intended
  work queue, not a side effect, and step 3e previews the exact count
  with `--dry-run` before merging. A count that differs from survey
  item 16 stops the merge.
* **A preset edit is live fleet-wide on merge.** Decision 5 edits the
  preset's Node-major rule and its note in the first pull request.
  Both changes only drop file names whose dependencies the runner's
  temporary rule already disables in every repository, so nothing
  Renovate proposes changes; the 3b tests assert the whole new sets.
* **Deleting the organisation secret cannot be undone by
  re-reading it.** GitHub never returns a secret's value. The
  mitigation is the order: the secret goes only after the API shows
  no `renovate.yml` anywhere, and the next hourly pass, whose probe
  checks push access to every managed repository, proves the
  environment copy is what the runner uses. If that pass failed, the
  recovery is a new token from the bot account, not the old value;
  the operator confirms before step 7 that he can mint one.
* **A debug log in a public repository.** Decision 8 limits it to one
  run filtered to this repository and deletes the run once read. The
  management session reads the log for anything token-shaped before
  recording a quote from it.
* **The preset's schedule starts on merge, one repository at a
  time.** Each swept repository moves from a daily window to
  Saturday, majors and CI tooling to the first of the month, and
  takes digest pinning from the preset for the first time, which
  arrives as one grouped CI-tooling pull request on the next first
  of the month. That is the volume this plan exists to produce, and
  phase 4 measures it; `vulnerabilityAlerts` keep their own
  schedule throughout, which phase 1 established.
* **The temporary rule could be removed early.** If it went before a
  repository was swept, that repository's disabled `renovate.yml`
  would receive update pull requests. *Sweep* step 6 checks every
  managed repository through the API before 3h starts, rather than
  trusting a list of merged pull requests.

#### Definition of done

Every item is checked by the management session against the files,
the API and the commands, not against a sub-agent's summary.

* No managed repository carries `.github/workflows/renovate.yml`:
  ```
  for r in actions agent-python andris client-python \
      client-python-k3s client-python-ova clingwrap cloudgood \
      development divergulent hunkydory images instar kerbside \
      kerbside-client kerbside-patches library-utilities occystrap \
      ryll sfui shakenfist uncalibrated-sextant visual-digest-rust; do
    gh api "repos/shakenfist/$r/contents/.github/workflows/renovate.yml" \
      >/dev/null 2>&1 && echo "$r still has it"
  done
  ```
  prints nothing. The list is the 23 in phase 2's *Cutover* step 3;
  a repository onboarded since is checked too.
* Every managed `renovate.json` extends the preset and sets no
  top-level key the preset sets, checked by fetching each one and
  comparing keys with `renovate-fleet.json`, and the first
  consistency audit after the last sweep pull request merges reports
  `renovate` compliant for every repository in scope.
* `templates/renovate/` holds `renovate.json` and `README.md` and
  nothing else, and `git grep -n 'renovate\.yml' -- ':!docs/plans'
  ':!docs/audits/compliance.md' ':!REVIEWS.md' ':!.vscode'` prints
  only the history in `docs/audits/scheduled-workflow-health.md`,
  the fixture name in `scripts/tests/test_ci_workflows.py`, and lines
  that name the file in order to refuse it: the check's
  `RENOVATE_REPOSITORY_WORKFLOW` constant, its fixtures in
  `scripts/tests/test_packaging_renovate.py`, the warning at the head
  of `renovate-fleet.yml`, and the spec's lines describing the
  refusal. (This item first listed only the history, the fixture name
  and the spec, which missed the three that must name the file;
  corrected on 2026-11-25 when the first pull request was checked
  against it.) Until step 3h lands, `renovate-runner.json`'s
  temporary rule, its test and the spec's sentence describing it are
  hits too.
* `renovate-runner.json` has no `packageRules` key, and
  `renovate-fleet.json`'s Node-major rule names
  `.github/workflows/renovate-fleet.yml` alone.
* The `Renovate` check's three new findings and its preset-read
  failure each kill a named mutant, and the script's path and count
  are in the commit message.
* `tools/source-size-baseline`'s entry for `packaging.py` is lower
  than 2,493 and equals the file's length.
* `python3 -m unittest discover -s scripts/tests -t scripts` passes,
  `pre-commit run --all-files` passes, and `python3
  scripts/audit-check.py --repo-path . --repo-name development`
  reports `renovate` compliant and no verdict worse than on `main`.
* *Record* holds: step 3e's preview counts against survey item 16;
  the staged table and the judgment audit's result; the canaries'
  passes and dashboards; open question 1's answer with its run id
  and the statement that the run was deleted; the run id of the
  first pass after the organisation secret was deleted, and that it
  succeeded; and the first Saturday's runs and pull requests.
* `gh secret list --org shakenfist` lists no `RENOVATE_TOKEN`, and
  #402 is closed with its reason.
* Open question 1 reads *Answered*, and the Execution table's
  `Merged` cell for phase 3 names all 24 pull requests.

#### Back brief

Before step 3a, back brief the operator on decisions 2 and 3: what
the criterion stops checking, and the one new rule it adds, which
fails a repository for a top-level key the preset sets. The second
is cheap to propose and expensive to change once fifteen issues
carry its wording. Confirm also decision 7, the single judgment
audit in place of one per repository, since it departs from the
push-audit block's letter.

Then stop at *Sweep* step 3, before anything is pushed in another
repository, with the staged table and the audit; and again before
step 7, the secret deletion, which cannot be undone.

### Phase 4: re-measure

Planning effort: medium.

**Scope.** Thirty days after phase 3 completes, repeat the
Situation section's measurements: Renovate pull request count by
package and by the Situation table's rows, Renovate share of CI time
including merge-queue runs, and runs of `renovate-fleet.yml` per day
with the longest gap between them. Those runs are on a VM since
phase 2, and dispatched by the conductor since its cutover step 5. Record before and after here, and
say whether the Mission's target -- 100 or fewer Renovate pull
requests in 30 days, against the baseline this phase
re-establishes -- was met, and if not, which row
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
pull requests each run their own repository's mechanical push-audit
tier as they land, and the judgment tier ran once over the script
and the staged table that produced all 22 (phase 3, decision 7);
cite both here rather than re-running them.

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

* Renovate runs hourly for the whole fleet from this repository,
  dispatched by the private-ci conductor rather than by GitHub's
  cron, and no managed repository carries
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
  window, measured against a baseline taken with the same method
  (417 org-wide in the 30 days to 2026-11-11, by phase 1's survey
  item 2, which superseded the Situation's 227).
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
  `actions` is phase 6 of `PLAN-workflow-ref-trust.md`, because it
  rests on that plan's decision to trust `actions` main.
* **GitHub's cron is unreliable at any minute, not only on the
  hour.** The Situation blamed the top of the hour. Phase 2's
  watch day moved the central runner to minute 23, and GitHub then
  delivered 5 of roughly 35 hourly events (phase 2, *Record*).
  Every scheduled workflow in the fleet is exposed in the same way.
  On 2026-11-23 the operator asked for a criterion here that
  forbids `schedule:` triggers fleet-wide in favour of the private-ci
  conductor. That is a master plan of its own, to be written after
  phase 2's cutover step 5, which is its pilot. Its first question
  is what notices the conductor stopping (cutover step 5, survey
  item 12), since that plan makes every scheduled job in the fleet
  depend on it.
* Digest pinning moves github-actions out of Dependabot's reach.
  Step 1a found that GitHub raises Dependabot alerts for actions
  referenced by semantic version but not by commit sha, so
  `PLAN-workflow-ref-trust.md`'s digest pinning (its phase 3, which
  this plan's sweep carries to each repository) takes the fleet's 540
  third-party `uses:` references out of the one ecosystem that feeds
  `vulnerabilityAlerts`. Nothing in this plan changes because of it
  --- an action fix then waits for the monthly CI-tooling window,
  which is the worst case the Mission already accepts for docker and
  pre-commit --- but that plan should record the trade as its own,
  since it is the change that causes it.
* A criterion requiring the `shellcheck-py/shellcheck-py`
  versioning rule, rather than merely permitting it, is now
  `docs/plans/PLAN-renovate-shellcheck-audit.md`, written on
  2026-11-21. It accepts the rule reached through
  `local>shakenfist/development:renovate-fleet` as well as a local
  copy, for the same reason phase 1's decision 1 gives: a criterion
  that reads only the local file holds the fleet to a line the
  policy has moved out of -- and with only `development` on the
  preset today, a local-file-only check would fail 23 repositories
  rather than the four it does. That plan deliberately lands before
  this plan's phase 3 rather than after it, because the sweep gives
  all four failing repositories the rule as a side effect and a
  criterion that has only ever passed has never been shown to fail;
  its decision 4 argues the case. The other half of what was
  recorded against it -- a test that the rule is identical in
  `renovate.json` and the template -- is discharged rather than
  deferred: phase 1 left one copy of the rule, in the preset, and
  `test_the_template_holds_nothing_but_the_extend` holds the
  template to carrying nothing to compare.
* Promoting lockstep groups into the preset, which is what
  [development#88](https://github.com/shakenfist/development/issues/88)
  ultimately asks for. A group that appears in more than one
  repository could live in `renovate-fleet.json`, but
  `renovate-lockstep-groups` would first have to resolve an
  `extends` of the preset as well as reading the local file, with
  tests showing it accepts a group defined there; until then it
  reports a promoted group as missing.
* `images` has Dependabot alerts disabled (phase 3, survey item 21).
  It is managed by Renovate, so it can never receive a `[SECURITY]`
  pull request however the runner's token is scoped. Enabling alerts
  there is a repository setting and the operator's call; nothing in
  this plan depends on it. A fleet criterion that alerts are enabled
  wherever Renovate manages a repository would catch the next one.

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
  described `renovate.yml` as copied verbatim while 17 copies held
  14 versions. Fixed outside this plan, and in the other direction:
  `d2298b0` (2026-10-31) made the claim true by auditing every copy
  against the template, so phase 1 has nothing to correct and phase
  3 removes the file and rewrites the spec around the preset. Phase
  1's survey, item 4, has the detail.
* Renovate cannot look up `shellcheck-py/shellcheck-py`'s
  four-component tags, so the hook is never bumped (found from
  `hunkydory#34`, the dependency dashboard, on 2026-10-09). No issue
  tracks it. Fixed for the fleet by `dc2f6ed` (#275, 2026-10-18),
  which put the versioning rule in `templates/renovate/renovate.json`
  and in this repository's own config; phase 1 moves that rule into
  the preset, and phase 3 removes `kerbside`'s local copy, which
  predates it.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
