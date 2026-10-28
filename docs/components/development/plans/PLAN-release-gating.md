# Release gating

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
what a run does, how to add a criterion, how to bring a
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
  on the next run, so a plan should say how many repositories --
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

Measured on 2026-10-18 against `origin/main` at `e0beea8`, and
against GitHub for the fleet. The prompt was client-python-ova#19,
"The release workflow publishes untested and ungated", which the
push audit of that repository's OVF parser plan raised against its
own `release.yml`. Every gap it names is a gap in
`templates/release-automation/release.yml`, so every repository
that copied the template has it too.

Thirteen repositories carry a `.github/workflows/release.yml`:
agent-python, client-python, client-python-k3s, client-python-ova,
clingwrap, divergulent, hunkydory, instar, kerbside,
library-utilities, occystrap, ryll and shakenfist. All thirteen use
`develop` as the default branch and cut their tags from it. A
fourteenth, andris, was missed when this plan was written, and phase
3's survey found it. It is a Rust project that publishes
maturin-built wheels to PyPI. It already has a merge queue, and its
`release` environment already has a reviewer and a tag-only policy.

**1. Nothing checks that a release waits for a human.** The template
puts `sign-tag` and `publish-pypi` behind `environment: release`,
and `RELEASE-SETUP.md:75-91` says to give that environment a
required reviewer and restrict it to `v*` tags. Whether anyone did
is invisible to the audit: no criterion reads `environments` or
`deployment-branch-policies` (the only planned readers are
`bypass-actor-identity` in `PLAN-bot-bypass-identity.md:639` and
`moving-ref-source-protection` in `PLAN-workflow-ref-trust.md:519`,
neither started). On the morning of this plan:

| Repository | `release` environment | `v*` tags | Exposure |
|---|---|---|---|
| shakenfist | existed with no protection rules and no deployment policy, holding one secret | 112 | every release unreviewed, and any branch's workflow could name the environment and read the secret |
| clingwrap | did not exist | 43 | the next tag would create it unprotected, which is GitHub's behaviour for a referenced environment that does not exist |
| client-python-ova | did not exist | 0 | the same, on its first tag |
| divergulent, instar, kerbside, ryll | required reviewer, no deployment policy | | gated by the reviewer, but deployable from any ref |
| the other six | required reviewer and a `tag: v*` policy | | the shape `RELEASE-SETUP.md` asks for |

The first three were repaired by hand the same morning, before this
plan was written, to match client-python's environment: `mikalstill`
as required reviewer, `custom_branch_policies: true`, and a single
`tag: v*` policy, each read back afterwards. They are recorded under
Bugs fixed. The four in the fourth row are left for phase 1, so the
criterion that would have caught them is the thing that confirms
them fixed.

**2. No release waits for CI.** The template's `build` job
(`release.yml:24-56`) builds, runs `twine check` and uploads; no job
runs tests or depends on a CI result. Across the fleet, ryll alone
runs tests in its release (`make test` and `cargo test --workspace`);
instar's and ryll's `check-version` jobs compare the tag with the
`Cargo.toml` version and nothing else. So the only thing between a
broken commit and PyPI is whoever approves the environment, reading
a deployment review page that does not show CI status.

That this matters is not hypothetical. kerbside v0.7.0 (`2d9ee91`)
was released at 03:08 today. Its pull request, #576, was merged
outside the merge queue at 02:59 after the queue's functional test
run failed. GitHub holds no `push` or `merge_group` CI run for that
commit; the one push-triggered workflow that ran on it, Demo
compose, failed.

Would "require a successful CI run on the tagged commit" have
passed each repository's most recent tag?

| Repository | CI triggers that reach the tagged commit | Last tag | CI on that exact commit |
|---|---|---|---|
| agent-python | none (`pull_request` and dispatch only) | v1.0.1 | none |
| client-python | none (`pull_request` only) | v0.8.3 | none |
| clingwrap | none (`pull_request` only) | v2.1 | none |
| shakenfist | `merge_group` | v0.7.2 (2024, a `v0.7-releases` branch) | none |
| client-python-k3s | `merge_group` | v0.3.0 | success |
| divergulent | `push: develop` | v0.1 | success |
| library-utilities | `push: develop` | v0.8.9 | success |
| occystrap | `push: develop`, `paths-ignore: docs/**, **.md` | v0.4.17 | success |
| client-python-ova | `push: develop` | no tags | |
| hunkydory | `push: develop`, `paths-ignore` the review files | v0.2.0, on a `prune-reviews` commit | skipped by `paths-ignore`; the parent passed |
| ryll | `merge_group` | v0.3.0, on a `prune-reviews` commit pushed directly | none; the parent (#573) passed in the queue |
| instar | `merge_group` | v0.3.0, a release commit pushed directly | none |
| kerbside | `merge_group` | v0.7.0 | none; the queue run failed |

Four of twelve would have passed. The failures are of three kinds,
and each shapes the design. A `pull_request` run's `head_sha` is the
pull request head, never the merge commit, so a repository whose CI
runs only on pull requests has no signal to read at all. A commit
that `prune-reviews.yml` pushes changes only `REVIEWS.md` and
`.vscode/*.weaudit*`, gets no CI run, and is often the tip of
`develop` when someone tags. And kerbside and instar are exactly the
cases a gate exists for. One implementation trap: a `head_sha=`
query also returns `schedule` runs (Renovate, CodeQL, export-config)
while the tagged commit is still the tip, so "any successful run" is
the wrong test.

**3. Nothing checks what a distribution contains.** The latest wheel
of each Python package on PyPI, downloaded and listed:

| Package | Version | Members under a `tests/` directory |
|---|---|---|
| divergulent | 0.1 | 57 |
| kerbside | 0.7.0 | 30 |
| shakenfist | 0.7.2 | 29 |
| occystrap | 0.4.17 | 17 (`occystrap.tests` is listed in `packages` deliberately) |
| shakenfist-client | 0.8.3 | 4 |
| shakenfist-agent | 1.0.1 | 2 |
| shakenfist-utilities | 0.8.9 | 2 |
| clingwrap | 2.1 | 1 |
| shakenfist-client-k3s | 0.3.0 | 0 |

The mechanism is the one client-python-k3s's `tools/check-dist.sh`
documents: under `pyproject.toml`, `include-package-data` defaults to
true, and setuptools_scm's file finder offers every git-tracked file
as package data, so a `packages.find` exclude drops the tests
*package* and the tests come back as data. k3s and client-python-ova
both set `include-package-data = false`; only k3s inspects its wheel,
in CI and again in its release. kerbside's `tools/check-wheel.py`
checks that runtime files are present, not that tests are absent.
The existing `release-artifacts-exclude-plans` criterion
(`packaging.py:2016`) guards the sdist against `docs/plans/` and
nothing else.

**What the audit already measures.** `release-process`
(`scripts/audit/checks/packaging.py:831-858`) skips repositories
without `pyproject.toml` and fails on `release.sh` or
`requirements.txt`, or on a missing `release.yml` or
`RELEASE-SETUP.md`. It calls five helpers: `release_asset_issues`
(:433), `release_dispatch_guard_issues` (:771),
`release_workspace_issues` (:545), `dist_agreement_issues` (:695) and
`release_container_path_issues` (:626). That is the natural home for
the workflow-shape rules this plan adds. Environment protection is
GitHub configuration, not file content, and belongs beside
`DeleteBranchOnMerge` (`github_config.py:217-242`) and
`MergeQueueConfig` (:245-280).

**The token.** Checks run under `AUDIT_READ_TOKEN`
(`.github/workflows/consistency-audit.yml:120,140,192`), whose
documented permissions are Metadata, Contents, Actions and
Administration, all read (`docs/consistency-audits.md:107-125`).
Nothing records whether that is enough to read an environment or its
deployment policies. `PLAN-bot-bypass-identity.md` step 3c assumes it
can and names `AUDIT_TOKEN` while doing so, which is the wrong token
since #278 moved `AUDIT_TOKEN` into the `audit-write` environment.

**Relation to other plans.** `PLAN-workflow-ref-trust.md` phase 5
(:564-615, not started) may turn copied templates into callers of
shared workflows. It never names `release.yml`; step 5a classifies
"every template directory under `templates/`", so the release
template is in scope only by implication. `PLAN-typescript-onboarding.md`
met the unprotected-environment problem in hunkydory as HD-1
(:1934) and fixed it by hand in 7b.2, without proposing a check.
`PLAN-bot-bypass-identity.md` owns the `develop` ruleset bypass that
let kerbside's merge skip the queue; this plan does not touch it.

## Mission and problem statement

Make three things true of every repository that publishes through a
`release.yml`, and make the audit say so every week, so that they
stop depending on someone remembering `RELEASE-SETUP.md`:

1. **A release waits for a human.** Every environment a release job
   names exists, has a required reviewer, and can only be deployed
   from a tag.
2. **A release waits for CI.** Nothing is signed or published unless
   the repository's CI succeeded on the commit the tag points at, or
   on the nearest ancestor that differs from it only by review
   marks.
3. **A wheel contains what was meant.** No test package or fixture
   reaches a published wheel, checked in CI on every pull request and
   again in the release.
4. **A release flow has one shape per language.** Every release
   repository merges through a serialized merge queue. A Python
   release follows `templates/release-automation/`, and a Rust release
   follows a Rust template that this plan adds. The audit measures
   both.

Out of scope, each for a stated reason:

* **Tag rulesets** (who may push a `v*` tag). Five repositories have
  one. A tag push is what starts a release, and the reviewer gate
  now sits after it, so the ruleset is defence in depth rather than
  the gate. Future work.
* **The `develop` ruleset bypass** that kerbside's merge used.
  `PLAN-bot-bypass-identity.md` owns it. This plan makes that bypass
  unable to reach PyPI, which is the half of the problem that is
  ours.
* **Turning `release.yml` into a shared workflow.** That is
  `PLAN-workflow-ref-trust.md` phase 5's decision to make. This plan
  puts the new logic in shared composite actions, so whichever way
  phase 5 goes, the logic exists once.
* **sdist contents.** An sdist carrying its tests is conventional
  and harmless, and `release-artifacts-exclude-plans` already guards
  the one thing in it that should not ship.
* **hunkydory's unsigned tags**, which `templates/release-automation/README.md`
  already records as a deliberate per-repository choice.

## Decisions

1. **Gate on the CI result for the tagged commit; do not re-run the
   tests in the release.** This is the decision a reviewer is most
   likely to push back on, because running the tests is the obvious
   fix and is what client-python-ova#19 half-suggests. It is the
   wrong fix here. CI is already the fleet's definition of "tested",
   and a second copy of it in the release would drift from the
   first. The functional tiers need infrastructure that only CI
   workflows set up, and run for 25 to 100 minutes (instar 100,
   client-python-k3s 76, kerbside 73), so a release would either
   carry that cost or run a cheaper tier and call it tested. Neither
   is better than asking CI. Reading the result also catches the case
   that actually happened: kerbside's release would have stopped at
   the gate, because its merge had no passing CI. The cost is that
   three repositories (agent-python, client-python, clingwrap) need
   a CI trigger that reaches the merged commit before they can
   release, and phase 3 adds one.
2. **Accept an ancestor only across review-mark commits, with a fixed
   allowlist.** The gate walks first-parent ancestors from the tagged
   commit and stops at the first one with a qualifying CI result. It
   accepts that result only if every commit it crossed changed
   nothing but `REVIEWS.md` and `.vscode/*.weaudit*`, the files
   `prune-reviews.yml` writes. The allowlist is a constant in the
   action, not an input. A per-repository input would grow "docs
   only" and then "anything that is not code", and a release would
   be gated by whatever the last person to edit the list thought was
   harmless. A tag on a docs-only commit that `paths-ignore` skipped
   (occystrap) therefore fails closed, and the failure message says
   to tag the last commit CI ran on. That is a one-command recovery
   before anything is published.
3. **A qualifying result is the latest attempt of each named
   workflow, run on `push` or `merge_group`, concluding `success`.**
   The caller names the CI workflow files that must have passed. The
   input is required and has no default, because "CI" is a different
   set of files in each repository (occystrap has two). Runs from any
   other event are ignored, which removes the scheduled-run trap. A
   `push` run counts only if its `head_branch` is the default branch,
   and a `merge_group` run only if its ref is the default branch's
   queue (`gh-readonly-queue/<default>/...`). A push to any other
   branch runs the workflow file as that branch has it. So anyone who
   can push a branch could edit CI there to succeed trivially, then tag
   the commit. A workflow with no run for the commit is a failure, not
   a pass, and the message names it. So is a partial set: if some of
   the named workflows qualify on a commit and others have no run
   there, the gate fails naming the missing ones, rather than walking
   past a commit that CI half-tested. Every qualifying run is judged
   at its latest attempt, so a failed `merge_group` run is not
   outranked by a later successful `push` run on the same commit.
   Runs from a fork do not count. And before any run is judged, the
   tagged commit must be on the default branch's first-parent
   history, because a run's metadata cannot tell a tag from a branch:
   a tag named `develop` produces `push` runs whose `head_branch` is
   `develop`. Phase 2 added these three rules in review; its
   execution notes say why.
4. **No override input.** A CI run that failed for infrastructure
   reasons is re-run in CI, and then the gate job is re-run through
   "Re-run jobs", which keeps the original `push` event, the same
   path `release.yml:56-66` already documents for the publish guard.
   An override would be one more thing the environment reviewer has
   to notice.
5. **The gate and the wheel check are composite actions in
   `shakenfist/actions`**, as `require-ci-success` and `check-dist`,
   called at `@main` like the fleet's other first-party actions.
   Thirteen hand-adapted copies of a gate are thirteen places for it
   to rot, and the template README already says copies drift
   (`templates/release-automation/README.md:6-14`). The actions are
   the single implementation; each `release.yml` gains a few lines
   calling them. This also keeps scripts out of workflow steps.
6. **`ci-gate` is its own job, and `build` needs it.** Every
   publishing job already needs `build`, so the gate sits upstream
   of all of them without editing each `needs:`. It runs on the
   static pool with `actions: read` and nothing else, and carries the
   same `push` and tag guard as the publishing jobs, so a
   `workflow_dispatch` dry run still exercises the build.
7. **`check-dist` checks wheels only, and fails on any member with a
   directory component named `tests` or ending in `_tests`.** That
   catches every case in the table above without a per-repository
   count. client-python-k3s's member-count ceiling stays where it is,
   as that repository's stricter local check. A `forbid` input takes
   extra globs for a repository that wants them. Rust repositories
   call it only where they publish Python wheels (kerbside's proxy
   wheels, ryll's `build-ryll-wheels`); instar and hunkydory publish
   none and skip it.
8. **The environment criterion is `release-environment-protection`,
   in `github_config.py`.** It applies where
   `.github/workflows/release.yml` exists, and otherwise reports
   `not_applicable` with that reason. For every environment that a
   job in `release.yml` names, it requires that the environment
   exists; that it has a `required_reviewers` rule with at least one
   reviewer; that `deployment_branch_policy.custom_branch_policies` is
   true; and that the deployment policies are non-empty and all of
   type `tag`. A 404 on the environment is a fail with the reason
   that GitHub would create it unprotected, read through
   `http_status()` the way `ScopeCoverage` does
   (`github_config.py:283-291,447-467`). Any other API failure is a
   `ToolFailure`. It does not judge `prevent_self_review`, which must
   stay off in an organisation with one maintainer or nobody could
   release, nor `can_admins_bypass`; the spec puts both in its
   reviewer-confirmed half.
9. **The workflow-shape rules join `release-process` as helpers, not
   new criteria**, and land last (phase 4), after the fleet has
   adopted the gate. Phase 3 makes each rule true, and phase 4 starts
   measuring it. The reverse order would file "Release process"
   failures in twelve repositories on the first weekly run after
   merge, for work that is already scheduled. There are three
   helpers: `ci-gate` exists, uses `require-ci-success`, and is
   upstream of every job that names an environment; every workflow it
   names exists and triggers on `push` to the default branch or on
   `merge_group`; and the `build` job runs `check-dist` on what it
   uploads, where it uploads a wheel. The CI half of the wheel check,
   which runs on pull requests, is a recommendation in the template
   rather than a measured rule, because the release copy is the one
   that stops a bad wheel.
10. **Phase 3 is one pull request per repository, carrying all of
    that repository's changes.** These are the gate, the wheel check,
    any CI trigger the gate needs, and the packaging fix. One review
    per repository is cheaper than three, and the changes test each
    other: the packaging fix is exactly what makes `check-dist` pass.
    The merge queue and the Rust template's convergence join that
    pull request too (decision 11).
11. **Every release repository gets a merge queue, and the Rust
    releases converge on a template; phase 4 measures both.** The
    operator added this during phase 3's back brief.
    - The queue requirement covers repositories with a `release.yml`
      only. That is where the gate needs a merged commit to carry a
      run, and requiring it fleet-wide would newly fail about eleven
      repositories that release nothing.
    - Phase 3 gives the queue to the eight release repositories that
      lack one, so the criterion fails nobody when it lands.
    - The Rust template is written from ryll's release, which already
      tags through the queue. ryll, instar and andris converge on it in
      their phase 3 pull requests.

## Open questions

1. **Does `AUDIT_READ_TOKEN` read environments?** The criterion
   needs `GET repos/{o}/{r}/environments/{name}` and
   `.../deployment-branch-policies`. Fine-grained tokens list
   Environments as a separate permission from Administration. Step 1a
   probes it. **Default:** if it cannot, the operator adds
   Environments: read to the token before step 1c merges. Widening a
   read token by one read permission is cheaper than an audit that
   cannot see the thing it exists to check.
2. **Does occystrap still need `occystrap.tests` in its wheel?** It
   lists the package deliberately. **Default:** remove it, and run the
   functional tests from the checkout, as every other repository
   does. If there is a consumer of the installed tests that this plan
   cannot see, the operator says so in step 3b and occystrap carries a
   `forbid`-free `check-dist` call with an `audit-ok` reason instead.
   **Settled by phase 3's survey.** The entry was copied from pbr's
   layout in occystrap 8a5fa01, with no reason recorded. Its functional
   tests install the wheel but run `deploy/occystrap_ci/tests` from the
   checkout, and no repository imports `occystrap.tests`. The default
   stands.
3. **shakenfist's `v*-releases` branches.** Its last tag came from
   one, and its CI triggers on `merge_group` for `develop` only.
   **Default:** none exist today, so the gate is adopted against
   `develop`. **Corrected by phase 3's survey:** the default's second
   half, adding a release branch to the CI triggers, would not make
   that branch releasable. The gate counts only runs on the default
   branch, and only commits on its first-parent history. No repository
   has a release branch today. Releasing from one again needs a change
   to the gate, which Future work records.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Release environment criterion | Complete | b87012d (#288) |
| 2. Shared `require-ci-success` and `check-dist` actions | Complete | actions 69d4589 (#182) |
| 3. Template and fleet adoption | Complete | development f3f8472 (#294), 8d032a5 (#295), 10e3145 (#299), d668b98 (#300), 3e0ced1 (#304); actions 658d767 (#184), e1e2c85 (#185); client-python-ova 4a8eeed (#29), 3b40144 (#30), 4b1764d (#31); library-utilities 0940cbe (#74); divergulent 70ae4c3 (#132); occystrap e9e66d9 (#163); client-python-k3s 6d40cee (#154); clingwrap f4966df (#145); hunkydory b421cef (#103); agent-python 65a1778 (#155); client-python d789e0b (#432); kerbside d30ff34 (#582); shakenfist b3dc0c6 (#4597), 6a3f307 (#4581), 1ec1a6c (#4607); ryll d70ff4d (#591), 20e3cd4 (#599); instar a31e0d1 (#796); andris 288aba3 (#34) |
| 4. Release-flow and merge-queue criteria | Complete | development e7a34fc (#328); ryll f8b99b3 (#623); occystrap 199f5b8 (#174) |
| 5. Push audit | Complete | development ebcab8b (#337) |

Phases run in order. Phase 2 lands in `shakenfist/actions` and
phase 3 lands in the template and in fourteen other repositories, so
their `Merged` cells take the `<repo> <sha> (#pr)` form, one entry
per repository.

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

<!-- shared-block: plan-push-audit-phase v4 -->
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
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
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

### Phase 1: release environment criterion

Planning effort: medium. The criterion follows `DeleteBranchOnMerge`
and `ScopeCoverage`; what needs care is the 404 meaning and the
token.

Blast radius: after step 1b, newly fails **zero** repositories,
including this one, which has no `release.yml` and reports
`not_applicable`. Without 1b, it would newly fail four: divergulent,
instar, kerbside and ryll.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | low | operator | none | **Operator, not a sub-agent.** With `AUDIT_READ_TOKEN` exported as `GH_TOKEN`, run `gh api repos/shakenfist/client-python/environments/release --jq .protection_rules` and `gh api repos/shakenfist/client-python/environments/release/deployment-branch-policies --jq .branch_policies`. Both must return data. If either returns 403, add Environments: read to the token and repeat. Record the outcome in this section and in `docs/consistency-audits.md:107-125`'s permission list. |
| 1b | low | operator | none | **Operator, with approval at execution time.** For divergulent, instar, kerbside and ryll, `PUT repos/shakenfist/<r>/environments/release` with `{"reviewers":[{"type":"User","id":1530643}],"prevent_self_review":false,"wait_timer":0,"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}`, then `POST .../deployment-branch-policies` with `name=v*`, `type=tag`, and read both back. Before that, check that each repository's release really tags `v*`: instar's and ryll's `check-version` jobs read the tag, so confirm the prefix in their `release.yml` `on.push.tags`. kerbside also publishes proxy wheels from the same environment, so confirm it names no second environment. |
| 1c | medium | opus | none | Add a `ReleaseEnvironmentProtection` class to `scripts/audit/checks/github_config.py` beside `MergeQueueConfig` (:245-280), with `id='release-environment-protection'`, `spec='docs/audits/release-environment-protection.md'`, `template=None` and `issue_title='Release environment protection'`. `applies(repo)` returns a skip reason when `repo.workflow('release.yml')` is None (see `packaging.py:784-786` for the call). `run(repo)` collects every `environment:` value from the jobs of the parsed workflow (a string, or a mapping with `name`), then for each one GETs `repos/{org}/{name}/environments/{env}` using raw `repo.github.api()` and `http_status()` as `ScopeCoverage` does (:283-291, :447-467). A 404 fails, saying GitHub would create the environment unprotected on first use. Any other non-zero status is `ToolFailure`. On success, fail unless there is a `protection_rules` entry of type `required_reviewers` with a non-empty `reviewers` list, and unless `deployment_branch_policy` is non-null with `custom_branch_policies` true. Then GET `.../deployment-branch-policies` and fail unless `branch_policies` is non-empty and every entry has `type == 'tag'`. Each failure names the environment and the missing property, and points at `RELEASE-SETUP.md`. Register it in `CHECKS` in `scripts/audit/registry.py` beside the other `github_config` entries (:90-95). Add the id to `NETWORK_CHECKS` in `scripts/audit_snapshot.py:34`. It does not join the empty-directory tool-failure list in `scripts/tests/test_metadata.py:633-637`, because `applies()` skips an empty directory before any API call. Write `docs/audits/release-environment-protection.md` following `docs/audits/README.md`, with a "Required, but confirmed by a reviewer" half covering `prevent_self_review` and `can_admins_bypass`, and why neither is measured. Add its row to the README table (:180-), and its lines to `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in `scripts/tests/test_metadata.py`. In `scripts/tests/test_github_config.py`, using `GithubConfigTestCase` (:65-81) and `FakeGitHub`, test: a pass; no `release.yml` (`not_applicable`); a 404 environment; no reviewers rule; a reviewers rule with an empty list; a null deployment policy; `protected_branches` instead of custom; an empty policy list; a `branch`-type policy beside a tag one; two environments with one failing; a 403 (`ToolFailure`); and a timeout. Follow the six-file list in `docs/consistency-audits.md:403-`. |
| 1d | low | sonnet | none | Run `pre-commit run --all-files`, then `python3 scripts/audit-check.py --repo-path . --repo-name development` and confirm `release-environment-protection` is `not_applicable` and nothing else moved. Then run the single check against fresh clones of the thirteen release repositories, all listed in Situation, and confirm all thirteen pass. Report any that do not, with the failure text. Do not run `audit-manage-issues.py` except with `--dry-run`. |

#### Phase 1 execution notes

Run on 2026-10-19 (AEDT).

* **1a.** The operator probed `AUDIT_READ_TOKEN` against
  client-python's `release` environment. Both endpoints returned data,
  so the token's existing permissions are enough, and open question 1
  is settled without widening it. `docs/consistency-audits.md` records
  this beside the token's permission list.
* **1b.** The operator approved the change, and the management session
  made it. divergulent, instar, kerbside and ryll each now have a
  single `tag: v*` deployment policy and `mikalstill` as required
  reviewer, and all four were read back. Each `release.yml` was first
  confirmed to tag `v*` and to name only `release`. None of the four
  environments holds a secret. The `PUT` replaced each reviewer list
  without reading it first, and the organisation audit log is not
  available on this plan, so the previous lists cannot be confirmed.
  Every other release environment in the fleet lists only
  `mikalstill`.
* **1c.** opus at medium effort. Departures from the brief:
  * The empty-directory tool-failure list was left alone, because
    `applies()` skips before any call. The brief has been corrected
    above.
  * The agent made two choices beyond the brief, and both were kept
    because they fail closed. A job whose `environment:` is an
    expression fails, asking for a literal name. A `release.yml` that
    names no environment fails.

  It added 20 tests, and the first review round added five more. A mutation script flipped the tag-type test and
  the 404 branch, and the tests caught each. The full suite passes.
* **1d.** sonnet at low effort, against fresh shallow clones. All
  thirteen release repositories pass, sfui (no `release.yml`) reports
  `not_applicable`, and so does this repository. The management
  session reproduced the shakenfist and sfui verdicts. The predicted
  blast radius of zero newly failing repositories holds.

### Phase 2: shared `require-ci-success` and `check-dist` actions

Planning effort: high. This is the gate itself, and a gate that
passes when it should not is worse than none, because it is trusted.

Lands in `shakenfist/actions` (default branch `main`), as composite
actions in top-level directories beside `pr-review-ref/`, with tests
under its `tests/`, following that repository's own conventions and
`AGENTS.md`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | worktree | Add `require-ci-success/action.yml` and its script, in Python, standard library only, and run with `python3 -I`. Inputs: `workflows` (required, a newline-separated list of workflow file names, empty is an error), `sha` (default `github.sha`), and `token` (default `github.token`; the caller grants `actions: read` and `contents: read`). Behaviour: starting at `sha`, for each candidate commit, query `repos/{repo}/actions/workflows/{file}/runs?head_sha={c}` for each named workflow. Keep only runs whose `event` is `push` with `head_branch` equal to the repository's default branch (read it from `repos/{repo}`), or `merge_group` with `head_branch` starting `gh-readonly-queue/<default>/`. Take the one with the highest `run_attempt` of the newest `run_number`, and require `status == completed` and `conclusion == success`. If every named workflow qualifies, pass, and print which commit and which run URLs did. If some named workflows qualify on the commit and others have no qualifying run there, fail without walking, naming the missing ones. If no named workflow has any qualifying-event run for the commit, step to its first parent, but only if the commit's diff against that parent touches nothing outside the fixed allowlist `REVIEWS.md` and `.vscode/*.weaudit*` (read via `repos/{repo}/commits/{c}`). Otherwise fail. Stop and fail after 20 steps. Any run that exists and did not succeed (failed, cancelled, still in progress) fails immediately, without walking. Each failure message names the commit, the workflow and the observed state, and for the walk case says to tag the last commit CI ran on. Write the allowlist as a module constant with a comment saying why it is not an input: a per-repository list grows. Tests in `tests/test_require_ci_success.py` with a stubbed HTTP layer, covering: success on the exact commit; a scheduled success ignored, then a fail; a `pull_request` success ignored; a `push` success on a feature branch ignored, then a fail; a `merge_group` success on another branch's queue ignored; a failed latest attempt after an earlier success; success after a re-run attempt; one of two workflows passing and the other with no run on the commit (fails without walking, naming the missing one); a walk across one and across two review-mark commits; a walk refused because a crossed commit touched `README.md`; the 20-step cap; an in-progress run; empty `workflows`. Document the action in `docs/` following the repository's pattern for its other actions. |
| 2b | medium | sonnet | none | Add `check-dist/action.yml` and a standard-library Python script generalising `shakenfist/client-python-k3s/tools/check-dist.sh` (read it first; keep its reasoning about using `zipfile` names rather than parsing `-l` output). Inputs: `path` (default `dist/`), and `forbid` (optional newline-separated globs). For every `*.whl` under `path`, fail if any member path has a directory component equal to `tests` or ending in `_tests`, or matches a `forbid` glob. Print every offending member, and a one-line hint that `include-package-data = false` in `pyproject.toml` is the usual fix, citing the setuptools_scm file-finder mechanism. Having no wheel under `path` is an error. Tests in `tests/test_check_dist.py` build small zip files in a temp directory: clean; `pkg/tests/test_x.py`; `pkg/corpus_tests/x`; a `forbid` hit; a member named `contests.py` (must pass, since only directory components count); no wheels; a non-zip `.whl`. Document it beside 2a. |
| 2c | low | sonnet | none | In `shakenfist/actions`, run its full check (`pre-commit run --all-files`, plus the test command its `AGENTS.md` names) and confirm both new test files run in CI, not just locally. |

#### Phase 2 execution notes

Run on 2026-10-19 (AEDT), in a worktree of `shakenfist/actions`, and
merged as actions#182 at 69d4589.

* **2a.** opus at high effort, as briefed; commit 6b47526. It shipped
  with 46 tests, and thirteen mutations each failed the suite.
  Departures from the brief, all kept because they fail closed:
  * The allowlist is the three exact patterns `REVIEWS.md`,
    `.vscode/*.weaudit` and `.vscode/*.weaudit-shas.json`, matched
    within one directory, rather than the looser `.vscode/*.weaudit*`.
    A renamed file is judged by its old name as well as its new one.
  * The walk refuses a merge commit, a commit with no files listed,
    and a commit whose file list may be truncated at the commits API's
    300-file cap.
  * Only runs whose `head_repository` is this repository count, so a
    fork's run does not.
  * Every qualifying run is judged at its latest attempt, not only the
    newest `run_number`, so a failed `merge_group` run is not
    outranked by a later successful `push` run. Decision 3 has been
    corrected above to say so.
* **2b.** sonnet at medium effort; commit b111e0d, with 21 tests. Two
  extras were kept. It fails on a backslash in a member name, which no
  correct wheel has. It also fails on an unreadable `.whl`, rather
  than skipping it.
* **2c.** The full check passed, and both test files run under the
  repository's CI test step.
* **A run still in progress fails the gate immediately.** The
  operator chose this over waiting. The recovery is to wait for the
  run, then re-run the gate job.
* **The push audit ran before the pull request opened.** The operator
  chose this order. Wave 1 used gitleaks v8.28.0, downloaded for the
  run because the runner had none, with a planted secret as the
  positive control. Wave 2's security reader found that a tag named
  like the default branch spoofs a run's `head_branch`. This was
  confirmed against kerbside's `v0.7.0` tag run, whose `head_branch`
  is `v0.7.0`. Commit 43b6850 fixed it by requiring the tagged commit
  to be reachable from the default branch.
* **Review round 1** found that reachability still admits a commit
  from a feature branch that was merged with a merge commit. Commit
  5b11e51 requires the default branch's first-parent history instead.
  It reads that history from the tip's sha, not the branch name,
  which a branch-named tag would make ambiguous. It reads at most 500
  listed commits, and fails closed beyond that.
* **Review round 2** raised no `fix` items. Commit f53cd9b took two
  `consider` items, both defects in code this phase added:
  * `check-dist` now escapes workflow-command characters in member
    names;
  * a merge queue `head_branch` must match
    `gh-readonly-queue/<default>/pr-<n>-<sha>` whole.
* **The final tree** has 758 tests, and all 42 mutants are caught. It
  gave these verdicts when run read-only against live repositories:
  * kerbside `v0.7.0` (2d9ee91) fails: it is a merge commit with no CI
    run, and the gate exists to stop exactly this release;
  * client-python-k3s `v0.3.0` passes on the exact commit;
  * ryll `v0.3.0` passes, after stepping across one review-mark commit;
  * kerbside 664068f, a second-parent commit, is refused.
* **The docs recommend `on: push: branches:` for gated CI.** Gated CI
  workflows should restrict pushes to branches, so that a tag push
  starts no CI at all. The `actions` docs say so, and phase 3 applies
  it where a repository's CI triggers on every push.

### Phase 3: template and fleet adoption

Planning effort: high, for the per-repository packaging judgement and
for what the survey found about how the fleet actually merges. The
workflow edits themselves are mechanical.

This phase spans seventeen pull requests:
* two in this repository: the plan and the Python template, then the
  Rust template;
* one in `shakenfist/actions`;
* one in each of the fourteen release repositories (decision 10).

Its `Merged` cell takes one `<repo> <sha> (#pr)` entry per
repository.

#### What the survey found

Two read-only surveys ran on 2026-10-19 against fresh clones of each
default branch. The first read every `release.yml`, the CI workflows,
and the branch rules. It ran the merged `require-ci-success` against
each repository's newest `v*` tag, its default-branch tip, and its
newest fifteen first-parent commits. The second built every Python
wheel, ran the merged `check-dist` on it, and tested a packaging fix
in a scratch copy. Every PyPI test-member count in the table below
matches the one this plan's Situation section gave. The rest of the
old phase 3 section was wrong or incomplete in these places, and the
section has been rewritten rather than patched:

1. **Every default branch is `develop`**, including client-python-ova's,
   so every gate reads `develop`.
2. **Merges that go around the queue cannot be released.**
   - The queue rulesets give one team `bypass_mode: always`. A merge
     made that way is a merge commit with no `merge_group` run.
   - The gate does not walk across a merge commit (decision 2), so it
     fails such a commit, and it fails the review-mark commits that
     `prune-reviews` adds above it.
   - Of the newest fifteen commits, that took out:
     - 4 in shakenfist;
     - 5 in kerbside;
     - 2 in client-python-k3s;
     - 1 in instar.
   - kerbside's `v0.7.0` and client-python-k3s's current tip are both
     bypass merges.
   - The gate is right to refuse them. A tag on one has to move to a
     later commit that went through the queue. Decision 3.6 covers
     the documentation, and `PLAN-bot-bypass-identity.md` owns the
     bypass itself.
3. **A `cancel-in-progress` group keyed on `github.ref` cancels push
   runs on `develop`.**
   - On a push, the ref is the same for every commit, and a cancelled
     run fails the gate.
   - Measured on `develop`: 12 of 74 `ci.yml` push runs in hunkydory,
     and 3 of 23 in library-utilities.
   - client-python-ova's `ci.yml` has the same groups (:47, :108,
     :186).
   - Even `cancel-in-progress: false` would not be enough. A group
     holds one running and one pending run, and a third arrival
     cancels the pending one. So the key has to be unique per commit
     on a push, which is the special-cased key that client-python-k3s,
     instar and kerbside already use.
4. **A `skipped` run is not a success.** kerbside's ruleset requires
   checks from `direct-qemu-functional.yml` and `sf-e2e-functional.yml`,
   but every one of their `merge_group` runs concludes `skipped`.
   Naming them in `workflows` would fail every release, so kerbside
   names `functional-tests.yml` alone. hunkydory, for a different
   reason, names `ci.yml` alone:
   - its `secret-scan.yml` has no `paths-ignore`, so it runs on every
     prune commit;
   - `ci.yml` does not, and a commit with one named workflow but not
     the other fails as part-tested, without walking;
   - its `codeql-analysis.yml` would make every docs-only commit
     part-tested.
5. **instar cannot pass the gate as it releases today.**
   - `make release` (`docs/development.md:478-480`) commits the
     version bump and pushes it straight to `develop` through the
     bypass. That leaves a commit with no `functional-tests.yml` run.
   - Its `release.yml` also has the dispatch hole the template was
     written to close:
     - `build` uses `always()` (:86);
     - neither `sign-tag` (:175) nor `github-release` (:218) has an
       `if:`;
     - so a `workflow_dispatch` reaches the `release` environment
       with only the reviewer in the way.
6. **ryll's `release.yml` has no `workflow_dispatch`**, and no job in
   it has an `if:`. Its root job is `check-version` (:11), so
   `ci-gate` goes above that, and the template's dispatch-aware
   `build` guard does not apply. Its release already runs `make test`
   and `cargo test`, and those stay. Its Python wheels come from
   maturin with `bindings = "bin"`, and on PyPI they are clean.
7. **shakenfist's runtime code reaches its wheel only by accident.**
   - `packages = ["shakenfist"]` (`pyproject.toml:207-208`) names only
     the top-level package. 158 of its 188 runtime modules ship as
     data, through the setuptools_scm file finder.
   - Setting `include-package-data = false` alone would leave 30
     modules. A build without `.git` gives that broken wheel today.
   - `deploy/**` package-data ships another 165 members:
     - the Ansible collection;
     - the `*_ci_tests` suites;
     - CI scripts and configuration.
   - Nothing reads any of `deploy/**` from an installed wheel. The
     collection installs from the checkout. Shipping `galaxy.yml` has
     already caused one daemon-restart bug
     (`tools/build-collection.py:12-17`).
   - The current wheel from `develop` has 371 test members, against
     29 in 0.7.2 on PyPI.
8. **`include-package-data = false` is never enough on its own.**
   Wherever `tests/` has an `__init__.py`, discovery still ships it as
   a package. So every fix pairs the setting with a `packages.find`
   exclude (or, for occystrap, removes the entry from its explicit
   list).
9. **The default `check-dist` rule has no false positives.** Every
   directory named `tests` or `*_tests` across the eleven wheel
   repositories is a real test directory, and no repository needs a
   `forbid` glob. But the action passes a wheel with no members, which
   client-python-k3s's `tools/check-dist.sh` (:88-92) rejects. That is
   a gap in phase 2's code, and step 3b closes it.
10. **client-python-k3s's `check-dist.sh` runs in both places.** The
    release calls it at `release.yml:52-54`, and CI calls it through
    `tools/check-wheel-build.sh`. The release-side call also enforces
    the 12-member ceiling on the exact artefact that is uploaded, so it
    stays, beside the action.
11. **Open questions 2 and 3** are answered above, under Open
    questions. occystrap's tests go. A release branch would need a
    change to the gate.
12. **Drift that is not this plan's.** Eight copies run `sign-tag`
    without the `vm` label. shakenfist's `release.yml` sets workflow
    permissions to `contents: read` rather than `{}`. hunkydory's
    `reviews` merges get no `ci.yml` run, because of its `paths-ignore`,
    and so cannot be tagged. Future work records each; none of them
    stops the gate working.
13. **andris is a fourteenth release repository**, added to this
    phase after the back brief. It is Rust.
    - Its `ci.yml` backs "Can see status", "Can enqueue" and "Can
      merge" behind a serial queue.
    - Its `release.yml` runs `check-version`, then `build-wheels` (a
      maturin matrix), then `sign-tag` on `ubuntu-latest`, then
      `github-release` and `publish-pypi`. The publishing jobs already
      carry the push and tag guard.

#### Decisions

* **3.1. Every gated workflow must give each commit a run that cannot be
  cancelled by the next one.**
  - Where a gated workflow has a `concurrency` group keyed on
    `github.ref` with `cancel-in-progress`, key it by sha on a
    push: for example `${{ github.workflow }}-${{ github.event_name
    == 'push' && github.sha || github.ref }}-<job>`.
  - Keep cancelling on pull requests and merge groups, where a
    newer run really does supersede the older one.
  - On a merge group, `github.ref` is a queue branch minted for each
    entry, so a group keyed on it never cancels a superseded queue
    run. A group a `merge_group` event can reach keys on the base
    branch on a run's first attempt, and on `github.ref` otherwise:

    ```yaml
    group: >-
      ${{ github.workflow }}-<job>-${{
      github.event_name == 'merge_group' && github.run_attempt == '1'
      && format('merge_group-{0}', github.event.merge_group.base_ref)
      || github.ref }}
    ```

    A re-run keeps its original event, so without the attempt guard a
    "Re-run jobs" on an old queue run would cancel the live entry or
    be cancelled by it. On a re-run the fallback, `github.ref`, is
    that old entry's own queue branch, which nothing else shares.
    client-python-ova commit 349b417 carries the comment every
    repository copied.
  - A path filter that filters merge groups diffs against
    `github.event.merge_group.base_sha`, never the branch name. Once
    the entry has merged the branch contains it, so a re-run would
    find no changes and pass untested (client-python-k3s commit
    e3b93a0).

  This is what makes a tag on any merged commit releasable, rather
  than only a tag on the tip.
* **3.2. Every release repository gets a merge queue.**
  - Eight lack one:
    - agent-python, client-python and clingwrap, whose
      `functional-tests.yml` runs only on pull requests, so no merged
      commit has a run the gate counts;
    - client-python-ova, divergulent, hunkydory, library-utilities
      and occystrap, whose CI runs on push.
  - The planned alternative for the first three was a `push` trigger.
    The operator chose a queue instead, then required one on every
    release repository (decision 11). Phase 3 therefore gives all
    eight a queue, so that phase 4's criterion fails nobody.
  - Each gated workflow gains a `merge_group` trigger, plus "Can
    enqueue" and "Can merge" aggregator jobs in the shape of
    client-python-k3s's (`functional-tests.yml:266-310`). Each
    aggregator's `needs:` lists that repository's own jobs, and the
    automated reviewer stays out of it. "Can enqueue" runs under
    `${{ always() && github.event_name != 'merge_group' }}` and "Can
    merge" under `${{ always() && github.event_name == 'merge_group'
    }}`, each with `permissions: {}` (client-python-ova's
    `ci.yml:309-350`). Under `!cancelled()`, a cancelled queue run
    would skip "Can merge", and a skipped required check reads as a
    pass. These are CI workflows; the Definition of done's ban on
    `always()` covers `release.yml` only.
  - Every workflow that `ci-gate` names must be required by the queue.
    A job's `needs:` cannot reach across workflows, so a repository
    that names two workflows gives each its own suffixed pair of
    aggregators, and its ruleset requires all of them. Otherwise the
    queue could merge a commit that one of them failed, and the gate
    would only find out at release time.
  - Once that pull request has merged, step 3h gives the default
    branch a ruleset copied from client-python-k3s's "Develop branch"
    ruleset (20595671): a serial queue that `merge-queue-config`
    accepts, with "Can enqueue" and "Can merge" required.
    - The ruleset comes second because, before the jobs exist, a
      queue would wait for checks that never report.
    - hunkydory already has a ruleset (23805020), so 3h edits that
      one instead. Its four required checks must then report on a
      merge group, or move into its aggregators.
    - Once the ruleset is on, bring every open pull request up to
      date with the default branch. A pull request run uses the
      workflows of the merge ref it started on, so one whose CI ran
      before the conversion merged reports the old check names and is
      blocked for ever; a re-run reuses the same merge ref. The
      template's `RELEASE-SETUP.md` has this as a step.
    - Copying the ruleset copies its bypass team too.
      `PLAN-bot-bypass-identity.md` owns that bypass. A bypass merge
      here fails the gate as it does everywhere else.
  - The gated workflow's push trigger, where it has one, is removed in
    the same pull request.
    - With a queue, every commit that lands on `develop` is one a
      `merge_group` run tested, so a push run would only test the
      exact commit its merge queue run already tested.
    - Decision 3.1's re-keying then has nothing left to protect in
      these repositories, and is not made.
    - The cost is a short window between the merge and step 3h's
      ruleset, in which a merge gets no run and cannot be released.
    - The pilot settled this; see the execution notes.
  - The cost is one more full run per merge. For client-python that
    run includes a smoke cluster on the shared under-cloud.
* **3.3. The packaging fix is `include-package-data = false` plus an
  explicit exclude of the test packages**, with explicit
  `package-data` for everything the code loads at runtime.
  - Each pull request states every member it removes.
  - Each pull request also shows that a build without `.git`
    gives the same members as one with it, which is the check that
    would have caught kerbside#326 and shakenfist's latent copy of
    it.
* **3.4. shakenfist moves to `packages.find` and stops shipping
  `deploy/**`.**
  - This is the decision a reviewer is most likely to argue with.
    It removes 489 members, including the Ansible collection and
    the CI suites, where the narrower option would remove 398 and
    keep `deploy/**`.
  - Nothing installed reads any of it. Keeping it ships CI scripts
    and a `galaxy.yml` that has already caused one bug.
  - It also makes the restart-on-change signature
    (`bootstrap.yml:228-238`) stop restarting every daemon on a
    commit that only touched tests.
  - The back brief asks the operator to confirm it, because the
    narrower option is a legitimate choice and the change is hard
    to observe until someone misses a file.
* **3.5. instar's release goes through the queue, as ryll's does.**
  - Its `make release` becomes the pair of
    `tools/propose-release.sh`, which opens a version-bump pull
    request, and `tools/tag-release.sh`, which tags the merged
    commit, after ryll's.
  - Its `release.yml` gains the template's guards: `sign-tag` and
    `github-release` take the push and tag guard, and `build` takes
    the tighter `ci-gate` guard in place of `always()`.
  - Adding a push trigger to its functional tests instead would run
    100 minutes of CI on a bump commit that is still pushed around
    review.
  - This is a process change for whoever releases instar, so the
    back brief confirms it.
* **3.6. The template's README carries the recovery table, including the
  bypass case.** A gate failure means nothing was published.
  - A bypass merge, or a tag off the first-parent history: move the
    tag to a commit that went through the queue.
  - A docs-only commit that `paths-ignore` skipped: move the tag.
  - A flaky queue run: it ejects its entry, so the commit it built
    never reaches `develop` and no tag can be on it. Re-queue the
    pull request and tag the commit that merges.
  - A merged commit whose run was re-run later, and whose later
    attempt failed: the gate judges each run at its latest attempt,
    so re-run that run until it passes, then re-run the gate job.

  The gate does not change.
* **3.7. check-dist runs wherever a wheel is built for publishing, and
  in CI only where a CI job already builds one.**
  - The CI jobs are:
    - client-python-ova's `lint-and-build`, on `${RUNNER_TEMP}/dist`;
    - occystrap's `functional`;
    - client-python-k3s's `sanity_checks`, through its existing
      script;
    - kerbside's `sanity_checks`.
  - Elsewhere the template recommends it, and phase 4 does not
    measure it (decision 9).
  - kerbside calls it twice in its release: on `dist/` in `build`,
    and on the proxy wheels in `build-proxy-wheels`.
  - ryll calls it on `target/wheels/` in `build-ryll-wheels`.
* **3.8. client-python-ova goes first, as a pilot.** The ten Python
  repositories in waves 1 and 2 start only after it has merged and
  passed a `workflow_dispatch` dry run of its `release.yml`. The Rust
  wave waits for the Rust template (3i) instead.
  - It is the repository the issue came from, it needs no
    packaging change, and its `release.yml` is the template's.
  - A defect in the job shape is then found once, not in twelve
    pull requests.
  - Its pull request closes client-python-ova#19.

* **3.9. The Rust template is ryll's release, generalised.**
  - The new `templates/release-automation-rust/` holds:
    - a `release.yml` whose fixed jobs are `ci-gate`, `check-version`,
      `sign-tag` on a throwaway runner, and `github-release`, with the
      template's guards;
    - a marked section for the repository's own build and publish
      jobs, because ryll, instar and andris build very different
      things;
    - the queued release scripts, `propose-release.sh` and
      `tag-release.sh`, with a README that lists the invariants.
  - The invariants are what phase 4 measures. The build section is
    not measured.
  - Its README says how a repository that publishes wheels adds
    `check-dist`.

#### Common edits

Each repository's pull request makes these changes, adapted by hand
to its `release.yml`, never by copying the template over it:

* A `ci-gate` job in the shape of `docs/actions.md`'s
  `require-ci-success` example in `shakenfist/actions`:
  - on `[self-hosted, static]`, with `permissions: {actions: read,
    contents: read}`;
  - with the publishing jobs' push and tag guard;
  - with a single `uses: shakenfist/actions/require-ci-success@main`
    step, given `timeout-minutes: 5` and the repository's
    `workflows`.
* The job (or jobs) at the root of the build graph `needs: ci-gate`,
  with the guard `${{ !cancelled() && (needs.ci-gate.result ==
  'success' || (github.event_name != 'push' && needs.ci-gate.result ==
  'skipped')) }}`. Where the root job has a guard of its own, the
  two are combined. ryll has no dispatch trigger, so its
  `check-version` just needs `ci-gate`.
* Every job that names an environment carries `if:
  github.event_name == 'push' && startsWith(github.ref,
  'refs/tags/v')`, and no job in `release.yml` uses `always()`.
* A `uses: shakenfist/actions/check-dist@main` step:
  - in every release job that builds a wheel, after `twine check`
    where there is one;
  - in the CI job named under decision 3.7, where there is one.
* The CI change the table below names, following decisions 3.1 and
  3.2.
* The packaging fix the table names, following decision 3.3. The
  pull request body lists the before-and-after member diff, and
  says that a build without `.git` matches.

Per-repository facts. `workflows` is what `ci-gate` names.

| Repository | `workflows` | CI change | `release.yml` | Packaging | `check-dist` calls |
|---|---|---|---|---|---|
| client-python-ova | `ci.yml` | add `merge_group` and the aggregator jobs, and drop the push trigger; then the ruleset (3h) | the template's | none; 18 members, clean | `build`; CI `lint-and-build` |
| library-utilities | `ci.yml` | add `merge_group` and the aggregator jobs, and drop the push trigger; then the ruleset (3h) | template-shaped | the decision 3.3 pattern for `shakenfist_utilities`; removes 2 | `build` |
| divergulent | `unit-tests.yml` | add `merge_group` and the aggregator jobs, and drop the push trigger; then the ruleset (3h) | template-shaped | add the exclude to its existing `packages.find` (:71-77); removes 61, three of them fixtures | `build` |
| occystrap | `python-unit-tests.yml`, `functional-tests.yml` | add `merge_group` to both, give each its own aggregator jobs ("Can enqueue" and "Can merge" in `functional-tests.yml`, "Can enqueue: unit" and "Can merge: unit" in `python-unit-tests.yml`, after kerbside's suffixed names), and drop both push triggers; then the ruleset (3h), requiring all four. A merge group has no paths filter, so docs-only merges become releasable | template-shaped | remove `occystrap.tests` from `packages` (:63), and turn off `include-package-data`; removes 17 | `build`; CI `functional`, after its build (:91-96) |
| clingwrap | `functional-tests.yml` | add `merge_group` and the aggregator jobs; then the ruleset (3h) | template-shaped | the decision 3.3 pattern; keep `examples/*.cwd`, which `main.py:213` loads; removes 1 | `build` |
| agent-python | `functional-tests.yml` | add `merge_group` and the aggregator jobs, with `check_paths` working on a merge group as k3s's does; then the ruleset (3h) | template-shaped | the decision 3.3 pattern; removes 3 tests, plus `protos/*.proto` and `protos/_copy_stubs.sh`, which nothing loads | `build` |
| client-python | `functional-tests.yml` | add `merge_group`, both to the workflow and to `functional_matrix`'s `smoke-cluster` call, and the aggregator jobs; then the ruleset (3h) | template-shaped | the decision 3.3 pattern; removes 6 | `build` |
| shakenfist | `functional-tests.yml` | none (`merge_group`) | adds `build-collection` and `publish-collection`; `ci-gate` goes above both `build` and `build-collection` | decision 3.4; keep `data/*.yaml`, which `schema/database_load_budget.py:204` loads; removes 489 | `build` |
| client-python-k3s | `functional-tests.yml` | none (`merge_group`) | adds `build-collection` and `publish-collection`; `ci-gate` goes above both | none; 12 members, clean | `build`, beside the `check-dist.sh` call, which stays |
| kerbside | `functional-tests.yml` | none (`merge_group`) | adds `build-collection`, `build-proxy-wheels` and `publish-proxy-pypi`; `ci-gate` goes above all three build jobs | add `kerbside.tests*` to its `packages.find` exclude, and turn off `include-package-data`; removes 31; keep the templates, static files and migrations; `tools/check-wheel.py` must still pass | `build` on `dist/`; `build-proxy-wheels` on `dist/proxy-wheels/`; CI `sanity_checks` |
| ryll | `ci.yml` | none (`merge_group`) | no dispatch; `check-version` is the root; converge on the Rust template (3i) | none; clean on PyPI | `build-ryll-wheels`, on `target/wheels/` |
| hunkydory | `ci.yml` | add `merge_group` and the aggregator jobs, and drop the push trigger; then edit its existing ruleset (3h) | builds a `.vsix`; publishes to the Marketplace | no wheel | none |
| instar | `functional-tests.yml` | none (`merge_group`); the release process changes (decision 3.5) | `build` uses `always()`; `sign-tag` and `github-release` are unguarded; converge on the Rust template (3i) | no wheel | none |
| andris | `ci.yml` | none (`merge_group`) | `check-version` is the root; maturin `build-wheels`; converge on the Rust template (3i) | its wheels are maturin-built; `check-dist` decides | `build-wheels`, on its wheel output directory |

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | In this repository, on this phase's branch. In `templates/release-automation/release.yml`, add the `ci-gate` job from Common edits above, with `workflows:` set to a `{{CI_WORKFLOWS}}` placeholder. Make `build` need it under the guard given there, with a comment explaining the guard: a skipped gate may only accompany a run that publishes nothing. The `${{ }}` wrapper is required, because YAML reads a bare value starting with `!` as a tag. Add a `check-dist` step after `twine check`. In `templates/release-automation/README.md`: add the placeholder row; add a section on the gate covering what counts as CI, the review-mark walk and why there is no override (link `shakenfist/actions`' `docs/actions.md`, do not restate it); add the recovery table from decision 3.6; and add prerequisites for gated CI. Those prerequisites are: CI runs on `push` to the default branch with a `branches:` filter, or on `merge_group`; a concurrency group never cancels a push run, keyed as decision 3.1 says; no named workflow may conclude `skipped` on the event that counts; and `paths-ignore` means a tag on a skipped commit fails. State that a converted template must still run `ci-gate` upstream of `build`; the cross-reference to `PLAN-workflow-ref-trust.md` phase 5 stays in this plan. Update `templates/release-automation/RELEASE-SETUP.md` to say the reviewer approves a release whose CI the gate has already checked, and to name the `release-environment-protection` criterion. Do not cite plan phases or decision numbers in the template or its docs. Run `pre-commit run --all-files`, which runs actionlint over the template. |
| 3b | medium | sonnet | own worktree of `shakenfist/actions` | In `tools/check-dist.py`, fail a wheel that holds nothing outside its `*.dist-info/` directory, the way client-python-k3s's `tools/check-dist.sh:88-92` rejects an empty wheel. A wheel whose only payload is under `*.data/` passes: ryll's wheels have just `<name>.data/scripts/ryll` and their dist-info. Add tests in `tests/test_check_dist.py` for an empty wheel, a dist-info-only wheel and a `.data`-only wheel, and update the `check-dist` section of `docs/actions.md`. Run the repository's pre-commit and `python3 -m unittest discover -s tests -t .`, and mutate the new branch on a copy to show that the tests catch it. Leave the work uncommitted. This lands as its own pull request in `shakenfist/actions`, before 3d. |
| 3c | high | opus | own clone of the repository | The pilot: client-python-ova, with the Common edits and its table row, including decision 3.2's merge-queue CI change, which the later steps copy. Decision 3.2 drops the push trigger, so the re-keying the brief once asked for is not made. Its `release.yml` is the template's, so the diff should be the template's 3a diff, with `ci.yml` as `workflows`, plus the `merge_group` trigger and aggregator jobs, and the CI `check-dist` step. Build the wheel with the release's path (sdist, then the wheel from it) and run `check-dist` on it. Run the repository's pre-commit. Leave the change uncommitted on a branch named `release-gating`; the management session reviews, commits and pushes, and the commit body says it closes client-python-ova#19. After it merges, the management session asks the operator before dispatching `release.yml` on `develop`. The dry run must show `ci-gate` and the publishing jobs skipped, and `build` and `check-dist` succeeded. |
| 3i | high | opus | none | In this repository, as its own pull request. Write `templates/release-automation-rust/` from ryll's `release.yml`, `tools/propose-release.sh` and `tools/tag-release.sh`, following decision 3.9. Read instar's and andris's `release.yml` too, so that the fixed jobs suit all three, and say in the README where each of the three differs inside the build section. The README lists the invariants as a checklist, which phase 4's helpers turn into rules. Run pre-commit, which lints the template. |
| 3d | high | sonnet | own clone per repository | Wave 1: library-utilities, divergulent and occystrap, each also making decision 3.2's CI change after the pilot's pattern. One sub-agent each, run in parallel. Each takes its table row, the Common edits, and the merged pilot as its pattern. Each applies the packaging fix from its row, then confirms four things: `check-dist` passes on a wheel built through the sdist; the member diff matches the row and is stated whole; a build from a copy with no `.git` gives the same members; and the repository's own tests pass. Run pre-commit, and leave the change uncommitted on a `release-gating` branch. Any difference from the table row is reported, not worked around. |
| 3e | high | opus | own clone per repository | Wave 2: shakenfist, client-python-k3s, kerbside, hunkydory, clingwrap, agent-python and client-python. One sub-agent each, run in parallel. The same checks as 3d, and also these. hunkydory, clingwrap, agent-python and client-python: decision 3.2's CI change, copying client-python-k3s's `merge_group` trigger, its `check_paths` handling of a merge group, and its "Can enqueue" and "Can merge" jobs (`functional-tests.yml:266-310`), with each aggregator's `needs:` listing that repository's own jobs; the automated reviewer stays out of `needs:`. shakenfist: decision 3.4, unless the operator chose the narrower option in the back brief; all 188 runtime modules must be in the fixed wheel, and the test in `shakenfist/tests/test_database_load_budget.py` that asserts the data file is declared must pass. kerbside: `tools/check-wheel.py` must pass on the fixed tree. Any job graph with more than one root needs `ci-gate` above every root that leads to an environment job. Run pre-commit, and leave the change uncommitted on a `release-gating` branch. |
| 3f | high | opus | own clone per repository | The Rust wave: ryll, instar and andris, after 3i has merged. One sub-agent each, in parallel. Each converges its `release.yml` on `templates/release-automation-rust/`, keeping its own build and publish jobs in the marked section and never copying the template over the file, and adds `ci-gate` with its table row's `workflows`. ryll and andris put `check-dist` on their wheel output, checked by reading. andris was added after the wheel survey, so its agent first downloads andris's newest PyPI wheels and runs the merged `check-dist` on them, and reports a failure rather than working around it. instar also takes decision 3.5: adapt the template's `propose-release.sh` and `tag-release.sh`, change `make release` and `docs/development.md:478-480` to match, and make the `release.yml` guard changes. Never install a native Rust toolchain; run each repository's pre-commit through its Docker wrapper where it has one. Leave each change uncommitted on a `release-gating` branch. |
| 3h | low | operator | none | **Operator, with approval at execution time.** After each of the eight repositories in decision 3.2 has merged its pull request, and a run on `develop` shows the new jobs reporting, `POST repos/shakenfist/<r>/rulesets` with client-python-k3s's "Develop branch" ruleset (20595671) as the body, minus its `id`, `source` and timestamps, and read it back. For hunkydory, `PUT` its existing ruleset 23805020 with the queue added instead. occystrap's ruleset requires all four of its aggregator checks. Then run `merge-queue-config` against the repository, and watch the next pull request through the queue. |
| 3g | low | sonnet | none | After each repository's pull request merges, run the merged gate read-only against that repository's `develop` tip, using the command in Definition of done. A failure is reported with its first line, not fixed: a bypass merge at the tip is expected, per the survey. Ask the operator before any `workflow_dispatch`. |

The rows are in execution order. The labels follow the order the steps were added, not the order they run, and the execution notes cite them, so they are not renumbered. The order is:
1. 3a and 3b run first;
2. 3c, the pilot, runs alongside 3i, the Rust template;
3. 3d and 3e wait for the pilot's dry run and for 3b;
4. 3f waits for 3i;
5. 3h and 3g follow each merge.

The management session pushes each branch and opens each pull
request after reviewing it, with the operator's approval at execution
time. Each pull request's body lists its member diff and its gate
verdicts.

#### Risks and mitigations

* **The first release after adoption fails at the gate.** Most likely
  this is a bypass merge, or a tag on a commit that `paths-ignore`
  skipped. Nothing has been published at that point, and the
  README's recovery table says what to do. Step 3g runs the gate
  against each tip after merge, so the operator learns which
  repositories would fail before a release finds out. The management
  session reports those.
* **A packaging fix drops a file the code loads at runtime.** The
  survey found three such sets: clingwrap's examples, shakenfist's
  load budget, and kerbside's templates, static files and migrations.
  Each 3d and 3e agent states every removed member, and the
  management session reads that list against the table before
  committing. kerbside's `check-wheel.py` and shakenfist's
  declared-data test fail if the known ones go.
* **A new queue blocks merging** in one of the eight repositories that
  gain one, if a required check never reports. The ruleset is applied
  only after the aggregator jobs have merged and run, and step 3h reads
  the next pull request through the queue. Deleting the ruleset undoes
  it in one call.
* **The merge queue doubles CI load** in the three that ran CI only on
  pull requests, and client-python's merge group includes a smoke
  cluster. The operator
  accepted that in the back brief.
* **An agent copies the template over a `release.yml`** that differs
  from it, and loses kerbside's proxy wheels or shakenfist's
  collection. The brief forbids it. The management session also diffs
  each job list before and after: no job may disappear.
* **The pilot's job shape is wrong.** 3c runs alone, and the dry run
  must pass before 3d starts.
* **A change to `actions@main` alters what is published.**
  `check-dist` runs inside `build`, after the wheel is built and
  before it is uploaded, so a bad commit to `shakenfist/actions@main`
  could change the artefact that is then signed and published, in
  every repository at once. Moving the check into its own job that
  downloads the artefact would narrow that, but the build job already
  runs other actions by mutable reference. Pinning first-party actions
  is `PLAN-workflow-ref-trust.md`'s to decide, and this risk is named
  for it rather than worked around here.
* **A merge to `actions@main` breaks every release at once**, because
  everything calls it at `@main`. 3b is a strict tightening, it lands
  before the fleet calls `check-dist`, and its tests include ryll's
  `.data`-only shape.

#### Definition of done

* Every one of the fourteen `release.yml` files on `develop` has a
  `ci-gate` job that uses `shakenfist/actions/require-ci-success@main`,
  and every job naming an environment is downstream of it.
  `!cancelled()` appears only in the guard for a job that needs
  `ci-gate`, directly or through another job as the Rust template's
  build jobs do, and `always()` appears nowhere. The script below checks
  the gate's presence and the absence of `always()`, and lists every
  `!cancelled()` line. Each listed line is then read against its
  job's `needs:`, and the downstream property is read from the job
  graph, until phase 4's helper checks it mechanically. Run it from a
  directory of fresh clones:

  ```bash
  for r in */; do f="$r.github/workflows/release.yml"; [ -f "$f" ] || continue
    grep -q 'require-ci-success@main' "$f" || echo "$r: no ci-gate"
    grep -n 'always()' "$f" | sed "s|^|$r: |"
    grep -n '!cancelled()' "$f" | sed "s|^|$r: cancelled guard: |"; done
  ```
* Every repository that publishes a wheel calls `check-dist` in each
  release job that builds one. A wheel built through the sdist from
  each merged `develop` passes it.
* The member diffs in the shakenfist, kerbside and clingwrap pull
  requests still list their runtime data as shipped.
* Every gated workflow that still runs on `push` and has a
  `concurrency` group keys it by sha on a push.
* The merged gate passes against each repository's `develop` tip, or
  the plan's execution notes name the failing tip and why:

  ```bash
  GH_TOKEN=$(gh auth token) GITHUB_REPOSITORY=shakenfist/<r> \
    WORKFLOWS=$'<files>' COMMIT_SHA=$(gh api repos/shakenfist/<r>/commits/develop --jq .sha) \
    python3 -I <actions-clone>/tools/require-ci-success.py
  ```
* `merge-queue-config` passes for all fourteen release repositories,
  and each of the eight that gained a queue has merged at least one
  pull request through it.
* ryll's, instar's and andris's `release.yml` each satisfy every item
  on the Rust template README's invariants checklist, read item by
  item.
* The pilot's `workflow_dispatch` dry run shows `ci-gate` and the
  publishing jobs skipped, and `build` and `check-dist` succeeded.
* client-python-ova#19 is closed by the pilot's pull request.
* `pre-commit run --all-files` passes here, and
  `scripts/audit-check.py` against this repository reports what it
  did before.

#### Back brief

Before 3a starts, the operator confirms:

1. **shakenfist's `deploy/**`** (decision 3.4). Drop it, or keep it
   and exclude only `deploy/shakenfist_ci/*`.
2. **instar's release process** (decision 3.5). Move it to a version
   pull request through the queue, or hold instar out of this phase.
3. **The CI change for agent-python, client-python and clingwrap**
   (decision 3.2): a push trigger, or a merge queue.
4. **Pushing and opening the pull requests.** Each is asked
   at the point it is ready, or granted once for the phase.

#### Phase 3 execution notes

Started on 2026-10-19 (AEDT).

* **Back brief.** The operator chose:
  - shakenfist drops `deploy/**` from its wheel (decision 3.4);
  - instar moves to a queued release, after ryll's (decision 3.5);
  - agent-python, client-python and clingwrap get a merge queue
    rather than a push trigger. That rewrote decision 3.2, moved those
    three from wave 1 to wave 2, and added step 3h;
  - the management session may push and open each pull request once
    it has reviewed it, without asking each time. Merging stays with
    the operator, and every `workflow_dispatch` or ruleset change
    still needs an explicit yes at the time.
* **Scope added after the back brief.** The operator asked for an
  audit of the standardized Python and Rust release flows, and for a
  merge queue wherever there is CI. In a second round the operator
  chose:
  - a queue on release repositories only;
  - a Rust release template, rather than invariants alone;
  - widening phase 4, rather than a separate plan.

  That added decision 11 and decisions 3.2 (widened) and 3.9, step
  3i, andris, and phase 4's new scope. The option the operator picked
  had said that after phase 3 all but one or two release repositories
  would pass. In fact five would have failed: client-python-ova,
  divergulent, hunkydory, library-utilities and occystrap. Each was
  already getting a phase 3 pull request, so decision 3.2 gives them a
  queue in it, matching the operator's earlier choice of queues for
  the other three.
* **3c** opus at high effort, as client-python-ova#29 (ca8d9a5). The
  management session removed `ci.yml`'s push trigger, which the queue
  makes redundant, and reverted the re-keying with it. Decision 3.2
  was amended to match. The agent's extras were kept:
  - `RELEASE-SETUP.md` takes the template's changes, because it is
    the repository's copy of the template;
  - an `AGENTS.md` line says to merge through the queue.

  `appliance-corpus` now sees its read-only test-data token on a
  merge queue entry, which may carry a fork's change once someone
  with write access has enqueued it. The job's comment says so, and
  the pull request asks the reviewer to look.
* **3i** opus at high effort, as development#295. Departures from the
  brief, all kept:
  - a fifth script, `release-version.sh`, keeps `check-version`'s
    logic out of the workflow step;
  - `check-version` no longer files an issue on a mismatch, so ryll
    and instar lose `issues: write`;
  - `tag-release.sh` tags the merge commit the queue made for the
    version bump, rather than the branch tip;
  - the template joins the actionlint exclusion list, as
    `ConvergedTemplatesTest` requires of any template that carries a
    placeholder.

  Its README's invariants checklist has fifteen items, which phase 4
  turns into rules.
* **3a** sonnet at medium effort, commit 87f2023. The management
  session tightened the `build` guard's comment and rewrapped two
  paragraphs.
* **3b** sonnet at medium effort, as actions#184. Directory-only
  entries do not count as payload. That is stricter than
  client-python-k3s's script, and was kept.
* **Three corrections from the pilot, made in every later wave.**
  Each is now in decisions 3.1, 3.2 and 3.6, and the waves' briefs
  carried them:
  - On a merge group `github.ref` is minted for each entry, so the
    pilot's concurrency groups never cancelled a superseded queue
    run. They key on the base branch, on a run's first attempt only
    (client-python-ova#30, 3b40144; the same guard reached the shared
    `smoke-cluster.yml` as actions#185, e1e2c85).
  - A path filter on a merge group diffs against
    `merge_group.base_sha`, not the branch name, or a re-run after
    the merge passes untested (client-python-k3s commit e3b93a0).
  - The aggregators run under `always()`, because a cancelled run
    would otherwise skip "Can merge" into a pass
    (client-python-ova#31, 4b1764d).

  The recovery advice was wrong in the same way everywhere: a failed
  queue run never puts its commit on `develop`, so the answer is to
  re-queue and tag the commit that merges, not to re-run. The
  template README's recovery table and every wave's docs were
  corrected.
* **3d**, wave 1, as library-utilities#74 (0940cbe), divergulent#132
  (70ae4c3) and occystrap#163 (e9e66d9). occystrap's gate names two
  workflows, so its ruleset requires four checks: "Can enqueue",
  "Can merge" and their `: unit` pair.
* **3e**, wave 2, as client-python-k3s#154 (6d40cee), clingwrap#145
  (f4966df), hunkydory#103 (b421cef), agent-python#155 (65a1778),
  client-python#432 (d789e0b), kerbside#582 (d30ff34) and
  shakenfist#4581 (6a3f307). shakenfist's merged two days after the
  rest. Its queue runs failed three times, all on the cluster smoke
  test in shakenfist#4394, which had been failing about half of all
  shakenfist queue runs since 2026-10-01. The cause was in
  `sf-resources`: it published on a change in the running-domain set,
  and a poll during `create()` saw the new domain before the start
  returned, so the one publish carried a timestamp the test rejected.
  shakenfist#4597 (b3dc0c6) fixed it with a follow-up publish on the
  next poll, and #4581 merged on the next attempt. Its follow-up,
  shakenfist#4607, adds the release check that the wheel carries
  `shakenfist/data/*.yaml`, and forbids `shakenfist/deploy/*` in
  check-dist.
* **3f**, the Rust wave, as ryll#591 (d70ff4d), instar#796 (a31e0d1)
  and andris#34 (288aba3). A `workflow_dispatch` dry run on instar's
  branch (run 39346550167) found a defect in the template: on a dry
  run `ci-gate` is skipped, and a build job with no `if:` takes the
  implicit `success()`, which fails when any upstream job was skipped,
  so every build was skipped too. Each build job now carries the
  build-job guard, `!cancelled()` and one `result == 'success'`
  clause per need (development#299, 10e3145, which also made it the
  README's invariant 6; ryll#599, 20e3cd4; instar and andris in their
  own pull requests). A dry run on instar's merged `develop` (run
  39476356008) then built its binaries and skipped every publishing
  job.
* **3h**, with the operator's approval for each repository. Rulesets:
  client-python-ova 25425412, divergulent 25434544, clingwrap
  25434545, library-utilities 25434755, occystrap 25443422,
  agent-python 25443431, client-python 25444487, and hunkydory's
  23805020 edited in place. Each read back identical, and
  `merge-queue-config`, `merge-group-cancellation` and
  `release-environment-protection` pass on all of them.
  client-python's nine other audit failures predate this work.

  Adding a ruleset left every open pull request whose CI ran before
  the conversion blocked for ever: it reports the old check names,
  and a re-run reuses the same merge ref. Five Renovate pull requests
  were unblocked by ticking their rebase checkbox, and the template's
  `RELEASE-SETUP.md` now has the step (development#300, d668b98).
* **3g**, read-only, on 2026-10-23 (AEDT). The gate passes on
  `develop` in client-python, client-python-ova, divergulent,
  hunkydory, instar, andris, ryll and shakenfist. ryll's passed
  across one review-mark commit, as designed. It fails on six tips,
  each a merge that went around the queue, which the gate refuses as
  designed: library-utilities#76, clingwrap#146, agent-python#157 and
  occystrap#165 (daily `export-repo-config` pull requests merged
  directly), client-python-k3s#155 (dequeued by hand, then merged),
  and kerbside#588, which `sfconductor` merged after its queue run
  failed. A release from any of them tags the last commit the queue
  merged.
* **A queue entry can wait out the check timeout.** shakenfist#4581's
  first queue run (39361900921) failed its smoke test at 06:07, but
  "Can merge" waits for every need, and two lanes only got runners at
  08:50 and 08:59, so the entry hit the 360-minute timeout instead of
  ejecting. See Future work.
* **occystrap's queue has never built a merge group.** The completion
  check on 2026-10-25 (AEDT) found no `merge_group` run in occystrap
  at all. The other seven new queues had each merged a pull request
  through the queue by then.
  - occystrap#164 waited in the queue from 2026-10-21 until it was
    dequeued by hand and merged around the queue (487a2e2).
  - occystrap#166 was closed unmerged: #168 had removed the action it
    bumped.

  The repository settings, the Actions permissions and the workflow
  states all match divergulent's, whose queue works. The ruleset
  differs from divergulent's only in also requiring the
  `Can enqueue: unit` and `Can merge: unit` pair, and all four checks
  reported on #164's head. The cause is not known yet. occystrap's
  gated workflows have no push trigger, so no commit on its `develop`
  carries a run the gate accepts, and it cannot release at all until
  its queue works. The 3g note above is therefore wrong for occystrap.
  With the operator's agreement, phase 3 closes with this open, and
  phase 4 owns finding the cause and holds its own merge on the fix
  (plan-blockers b1).
* **Merge commits.** The note on 3c gives `ca8d9a5` for
  client-python-ova#29, which is the commit rather than the merge.
  The Execution table records its merge commit, 4a8eeed.

### Phase 4: release-flow and merge-queue criteria

Planning effort: high. This phase decides what all fourteen release
repositories are held to, adds a criterion, and changes which
repositories an existing criterion applies to. The workflow reading
is fiddly, but the hard part is deciding what each rule means.

This phase spans three pull requests:
* this repository's, which carries the criteria;
* one in ryll (step 4f);
* whatever fix step 4e finds for occystrap's queue, tracked as
  occystrap#169.

This repository's pull request merges last. It does not merge until
both others have landed, and until occystrap has merged a pull request
through its queue (plan-blockers b1).

#### Scope

In scope:
* `release-process` runs its workflow-safety rules on every repository
  with a `release.yml`. Today it runs them only where there is a
  `pyproject.toml`. Its Python packaging rules keep that skip
  (decision 4.1).
* Three new rules in that same workflow-safety set: the CI gate, the
  workflows the gate names (including how their path filters read a
  merge group), and `check-dist` (decisions 4.3, 4.4 and 4.8).
* The Rust rules: one for each item on the Rust template README's
  invariants checklist that the new rules do not already enforce
  (decision 4.5).
* The `release-merge-queue` criterion (decision 4.6).
* Moving the release rules out of `packaging.py` into modules of their
  own (decision 4.9).
* Finding out why occystrap's queue has never built a merge group, and
  fixing it, or failing that, moving occystrap back to a single tier of
  CI (decision 4.7).
* ryll's two findings (decision 4.2).

Out of scope:
* Whether a queue actually runs, as opposed to how it is configured
  (decision 4.6).
* Measuring the Python template's own copies job by job. The Rust
  template gets that treatment because its README lists invariants.
  The Python template does not, and the drift Future work lists
  stays there.
* Merges that go around the queue: `PLAN-bot-bypass-identity.md`.

#### What the survey found

Two planning sessions surveyed independently on 2026-10-25 (AEDT).
Both read `main` at afab0ab and fresh clones of the fourteen release
repositories' `develop`, and their findings agree. They ran the five
existing workflow-safety helpers on those clones, plus a throwaway
prototype of the three new rules. The old phase 4 section was right
about the shape of the work, but wrong or out of date in the places
below, so it has been rewritten rather than patched.

1. **`ReleaseProcess` has no `applies()`.** The `pyproject.toml` skip
   is the first statement of `run()`
   (`scripts/audit/checks/packaging.py:839-840`). The old brief said the
   skip was in `applies`. `docs/consistency-audits.md` ("Adding a
   criterion", item 1) says an applicability test belongs in
   `applies()`. The line references the old brief gave are right: the
   class is at :831, `run()` at :837-859, and
   `release_dispatch_guard_issues` at :771.
2. **The skip hides four release repositories, not three:** hunkydory,
   ryll, instar and andris. The old brief left out andris, which joined
   phase 3 after it was written. Like ryll, andris keeps its
   `pyproject.toml` under a subdirectory (`andris/pyproject.toml`), and
   that stops mattering once applicability depends on `release.yml`.
   `PLAN-typescript-onboarding.md`'s Future work entry on splitting
   `release-process` says to revisit the split at the second
   non-Python release. There are now four.
3. **Run on those four, the five existing helpers find one
   problem.** ryll's `publish-ryll-pypi` job runs
   `pypa/gh-action-pypi-publish` without checking out, so
   `release_container_path_issues` fails it. The rule's reason
   (persistent-pool residue) does not apply to that job, because it
   runs on `[self-hosted, vm, debian-13-docker, l]`, which is
   ephemeral. andris's matching job checks out anyway (`release.yml:207`).
4. **No repository fails the three new rules in the prototype.**
   - Every job that names an environment is downstream of `ci-gate`.
   - Every `!cancelled()` guard has a `needs.<job>.result == 'success'`
     clause for each job it needs.
   - `always()` appears only in a comment, in ryll.
   - Every workflow named in `workflows` exists and triggers on
     `merge_group`.
   - **None of them triggers on `push`.** Decision 3.2 removed every
     push trigger, so the old brief's push-concurrency rule has nothing
     to judge in today's fleet (decision 4.3).
   - Every job that builds or uploads a wheel runs `check-dist` before
     the upload. The Python repositories build with `python -m build`
     and upload `dist/`. ryll and andris build through a script
     (`tools/build-ryll-wheel.sh`, `scripts/build-andris-wheel.sh`) and
     upload `target/wheels/*.whl`. So no single signal finds every
     wheel job (decision 4.4).
5. **Merge-group path filters come in two shapes, and both are safe.**
   Ten repositories run `dorny/paths-filter@v4` in a gated workflow.
   - Six pass `base: ${{ github.event.merge_group.base_sha || 'develop' }}`:
     agent-python, client-python, client-python-k3s, kerbside, ryll
     (`ci.yml:67`) and shakenfist.
   - Four run the filter step only when
     `github.event_name == 'pull_request'`, and default its output to
     `'true'`: occystrap, hunkydory, instar
     (`functional-tests.yml:100-102`) and andris. Then a merge group
     always runs in full.
   - client-python-ova, library-utilities, divergulent and clingwrap
     have no path filter in a gated workflow.

   Future work asked phase 4 for this rule. A rule that reads the
   step's `base:` and `if:` from the text can decide it, so the
   rule does not have to guess from shell (decision 4.8).
6. **ryll fails Rust invariant 14** in the mechanical spot checks of
   invariants 8, 14 and 15. The differences are both in comments:
   - its `tools/tag-release.sh` reflows one comment (lines 158-160);
   - its `tools/sign-release-tag.sh` fills in the `<repository>`
     placeholder in the comment on line 15.

   instar's and andris's copies match byte for byte. In all three,
   `propose-release.sh` and `release-version.sh` match outside their
   per-repository markers, and all three pass 8 and 15. The other
   twelve invariants were not measured. The template-resync helper
   cannot make ryll's fix: it copies all five files, and would
   overwrite `release.yml` and the per-repository sections.
7. **The invariants overlap the new rules.** Rule 1 already enforces
   the downstream-of-the-gate parts of invariants 4 to 6 and 10, and
   rule 3 already enforces invariant 13.
8. **`packaging.py` is 2,777 lines, and `test_packaging.py` is
   4,012.** The `source-file-size` block wants a stated reason to keep
   a file over about 1,500 lines whole, and to split one along an
   existing seam. `ReleaseProcess`, with the helpers only it uses, is
   one such seam: about 450 lines, from :405 to :859.
9. **The helpers the new rules need are spread across three
   modules.**
   - `job_environment` is in `github_config.py` (:311).
   - The trigger readers are in `ci_workflows.py`: `trigger_re`
     (:607), `MERGE_GROUP_TRIGGER_RE` (:641), `concurrency_block`
     (:718) and `workflow_header` (:851). d140ac8 has just made
     trigger reading follow GitHub's own rules.
   - `job_needs` is in `audit/text/workflows.py` (:614).

   Invariant 14 has a precedent: `review.py` compares copies with this
   repository's templates through `DEVELOPMENT_ROOT` (:222) and
   `read_within` (:507-512).
10. **No criterion reads GitHub Actions run history or pull request
    history.** `MergeQueueConfig` reads the branch rules and skips a
    branch with no queue (`github_config.py:289-290`). That skip is the
    gap `release-merge-queue` closes. A configuration criterion would
    pass occystrap, whose queue is configured but has never run.
11. **The fourteen are the whole release set.** None of the other
    thirteen repositories in the audit matrix has a `release.yml` on
    its default branch, so the applicability change in decision 4.1
    reaches only hunkydory, ryll, instar and andris.
12. **occystrap's queue has never run**, as phase 3's execution notes
    record. That makes occystrap unreleasable today, and it is filed
    as occystrap#169.
13. **The old section's repository counts were wrong.** Its phase 5
    and Execution text said "twelve" adopting repositories; phase 3
    ended with fourteen. Both are corrected at source in this planning
    commit.

#### Decisions

* **4.1. `release-process` splits by applicability, but stays one
  criterion.**
  - `applies()` is true for a repository with a `pyproject.toml` or a
    `release.yml`.
  - The packaging rules run only where there is a `pyproject.toml`, as
    today: `release.sh` and `requirements.txt` must be gone, and
    `release.yml` and `RELEASE-SETUP.md` must exist.
  - The workflow-safety rules run wherever there is a `release.yml`.
    These are the five existing helpers and the three in decision 4.4.
  - The Rust rules (decision 4.5) run where both `Cargo.toml` and
    `release.yml` exist.

  Why one criterion rather than a new one: an issue title is the
  idempotency key for filing and closing issues, and "Release process"
  already describes these failures. A second criterion would add a
  second issue per repository for the same workflow file, and a line in
  every frozen table. Decision 9 already said the shape rules join
  `release-process`. This is the same call, made for every language.
* **4.2. ryll gets a checkout; the helper does not learn about runner
  labels.** `release_container_path_issues` fails a job that runs
  `pypa/gh-action-pypi-publish` without checking out. It does so on
  every runner, though the residue it guards against only builds up
  on persistent ones. Exempting a `vm` label would make the helper
  trust a label to mean the runner is ephemeral, and that could stop
  being true when the pool changes without anything telling the audit.
  A checkout costs seconds, matches andris, and keeps the rule simple.
  Step 4f makes the change in ryll.
* **4.3. Rule 2 accepts either trigger the gate counts, and keeps the
  push-concurrency branch.** `require-ci-success` counts a run from a
  `push` to the default branch as well as one from a `merge_group`, so
  the rule accepts either: rejecting a trigger the gate accepts would
  hold a repository to something stricter than its own release. Nothing
  in the fleet triggers on `push` any more (survey 4). The push branch
  stays anyway, tested on fixtures, because it is what fails a
  repository that adds a push trigger back with a ref-keyed
  concurrency group, whatever its `cancel-in-progress` says: a group
  with `false` still drops the pending run when a newer one queues, as
  phase 3's survey found. `merge-group-cancellation` already judges
  concurrency on merge groups, and this rule does not repeat it.
* **4.4. The three new rules, as built:**
  - **Rule 1, the gate:** exactly one job has a step that uses
    `shakenfist/actions/require-ci-success@…`. Every job that names an
    environment is downstream of it, through `needs:`. Every job on
    that path either has no `always()` or `!cancelled()` in its `if:`,
    or has a `needs.<job>.result == 'success'` clause for each job it
    needs. The template's `build` guard admits a skipped gate only
    off `push` events. The rule accepts that only because the
    environment jobs downstream are confined to `push`, and the rule
    checks that through the existing `job_is_push_guarded`.
  - **Rule 2, the named workflows:** each file in that step's
    `workflows` input exists under `.github/workflows/`, triggers as
    decision 4.3 says, and reads a merge group's paths as decision 4.8
    says.
  - **Rule 3, `check-dist`:** a job handles wheels if it has a step
    that runs `python -m build` or uses `PyO3/maturin-action`, or a
    step that uploads a path naming `*.whl`. In such a job, a step
    using `shakenfist/actions/check-dist@…` comes after every
    recognised build step and before every upload step. Its `path`
    names the same directory as the upload, compared with
    `dist_directory()`. A job that builds through a script and uploads
    only a directory is invisible to this rule. The spec says so, and
    nothing in the fleet has that shape today.

  A job graph the rule cannot read (a cycle, or a `needs:` naming a job
  that does not exist) is reported as an issue rather than raised.
  These rules run on parsed text, and a reading that crashes would be
  an error on every run.
* **4.5. Each Rust invariant is checked once.** Where a
  workflow-safety rule already enforces an invariant, the Rust rules do
  not repeat it, so one defect produces one message. A table in
  `docs/audits/release-process.md` maps each of the fifteen invariants
  to the rule that checks it. A test reads the README's numbered list
  and fails if the table misses a number. Without that, the sixteenth
  invariant would be added to the README and never measured.
  Invariant 14 compares the repository's scripts with the template's.
  It reads the template the way `review.py` does, through
  `DEVELOPMENT_ROOT`. If the template copy cannot be read, that is a
  `ToolFailure`, not a fail.
* **4.6. `release-merge-queue` judges configuration, not liveness.**
  It applies wherever there is a `release.yml`. It fails when the
  default branch's rules have no `merge_queue` rule, reusing
  `MergeQueueConfig`'s read, and leaves judging the queue's settings to
  `merge-queue-config`. It would not have caught occystrap.

  It does not check liveness, for example "a pull request merged in
  the last 30 days, so a `merge_group` run should exist in that
  window", for three reasons:
  - No criterion reads history (survey 10). A verdict that depends on
    traffic flips for a quiet repository with no change to the
    repository.
  - A queue that never runs is loud on its own. Every pull request
    sits in it, as occystrap#164 did for three days. The operator saw
    it within that window, and the gate still fails safe: a release
    then publishes nothing.
  - The audit is weekly, which is the wrong cadence for an incident.

  This is the decision a reviewer is most likely to dispute, and the
  back brief asks the operator to confirm it. If it holds, Future work
  records liveness as declined, with these reasons.
* **4.7. occystrap goes back to a single tier of CI until its queue
  works.** When phase 3 closed, the operator chose to fix occystrap's
  queue before this phase's criteria merged, so that
  `release-merge-queue` would not pass a repository that cannot
  release. Step 4e and the probe that followed found nothing on our
  side to fix (see the execution notes). The operator then chose to
  disable the queue and gate releases on push runs to `develop`
  instead, keeping occystrap#169 open to try the queue again.
  occystrap keeps its `merge_group` triggers and "Can merge"
  aggregators, so turning the queue back on is only a ruleset change.
  Until then `release-merge-queue` fails occystrap, and the audit's
  issue keeps that gap visible alongside #169. This repository's pull
  request waits for occystrap's single-tier pull request rather than
  for the queue. Every ruleset or settings change needs the operator's
  yes at the time, as in phase 3.
* **4.8. Rule 2 checks how a gated workflow's path filter reads a merge
  group.** It applies to a named workflow that triggers on
  `merge_group` and has a `dorny/paths-filter` step. That step must
  either pass a `base:` that references
  `github.event.merge_group.base_sha`, or be confined by its own `if:`
  or its job's `if:` to `github.event_name == 'pull_request'`. A filter
  that diffs a merge group against the branch name passes untested on
  a re-run after the merge. The gate then counts that run, so this is a
  gate rule, not a general CI one, and it is limited to the named
  workflows. A path filter written in shell is invisible to it. The
  spec says so, and no gated workflow in the fleet has one (survey 5).
  The Future work entry that asked for this rule is removed.
* **4.9. The release rules leave `packaging.py`, along the check's
  seam.**
  - `ReleaseProcess`, with the five helpers and constants only it
    uses, moves to `scripts/audit/checks/release.py`.
  - Its tests move to `scripts/tests/test_release.py`.
  - The Rust rules go in `scripts/audit/checks/release_rust.py`, with
    `scripts/tests/test_release_rust.py`.
  - `job_environment` moves from `github_config.py`, and the trigger
    and concurrency readers from `ci_workflows.py`, into
    `audit/text/workflows.py`. Every check module then imports them
    from there, and no check module imports another.

  `CHECKS` keeps `release-process` in its current position, because
  that list orders the results.

  The moves come first, as commits with no change in behaviour, so the
  diffs for the new rules stay readable. Leaving the rules in
  `packaging.py` would take it past 3,500 lines. Every edit to it
  discards the review of the whole file, and the release rules are
  the part of it that changes most.

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Pure moves, in this repository, as two uncommitted changes the management session commits separately. **(1)** Move `job_environment` (with `FLOW_ENVIRONMENT_NAME_RE` and `unquote`) from `scripts/audit/checks/github_config.py`, and `trigger_re`, `flow_trigger_re`, the `*_TRIGGER_RE` and `*_FLOW_RE` constants built from them, `concurrency_block` and `workflow_header` from `scripts/audit/checks/ci_workflows.py`, into `scripts/audit/text/workflows.py`. **(2)** Move `ReleaseProcess`, and the helpers and constants only it uses (`packaging.py` :405-859; check each name with grep before moving it), into a new `scripts/audit/checks/release.py` with a module docstring in the style of its neighbours. Move `ReleaseProcessTest` and the tests of those helpers into a new `scripts/tests/test_release.py`. Update `scripts/audit/registry.py` without changing `release-process`'s position in `CHECKS`, and update every import, including the test modules and `scripts/tests/test_containment.py`'s allowlist if it names modules. Keep every docstring and comment with its code. Behaviour must not change. Afterwards, `python3 -m unittest discover -s scripts/tests -t scripts` passes with no test edits other than imports and moves. `scripts/audit-check.py` against this repository gives the same id-to-status map before and after. Run `pre-commit run --all-files`. |
| 4b | high | opus | none | In `scripts/audit/checks/release.py`, after 4a. **(1)** Give `ReleaseProcess` an `applies()` that is true when `repo.props['has_pyproject_toml']` or `repo.isfile('.github/workflows/release.yml')`. Inside `run()`, gate the four packaging rules on `pyproject.toml` (decision 4.1), and run the five existing workflow-safety helpers wherever `release.yml` exists. **(2)** Add three helpers that return issue strings, shaped like `release_dispatch_guard_issues`, implementing decisions 4.3, 4.4 and 4.8 exactly: `release_ci_gate_issues`, `release_ci_trigger_issues` and `release_check_dist_issues`. Use `job_needs`, `job_level_keys`, `job_environment`, `job_is_push_guarded`, `step_action`, `step_with_inputs` and the moved trigger and concurrency readers; do not parse YAML another way. Read workflows only through `repo.workflow()`. **(3)** Tests in `scripts/tests/test_release.py`. For each rule: a pass, a fail, and the edge cases. Rule 1: a cycle, an unknown need, `!cancelled()` with and without the success clause, `always()`, and an environment job that is not downstream. Rule 2: a missing file, push with and without a `branches` filter, a ref-keyed `cancel-in-progress` group on a push workflow, the same group keyed by sha on push, merge_group only, and a paths-filter step that passes, that has a `base:` without `merge_group.base_sha`, and that is confined to pull_request at step and at job level. Rule 3: check-dist after the upload, no check-dist, a mismatched path, a maturin-action job, a script build that uploads `*.whl`, and a job that only uploads. Also test applicability: a non-Python repository with a `release.yml` gets the workflow-safety findings and not the packaging ones, and a repository with neither is not applicable. **(4)** Update `docs/audits/release-process.md`: the new applicability, and each new rule with its reason. Say that the gate fails a named workflow whose runs conclude `skipped` (kerbside's `direct-qemu-functional.yml` does on every merge group), and that rule 2 cannot see that case. Also name the two shapes rules 2 and 3 cannot see. Run pre-commit and the unit tests. Then use the mutation-check skill: delete each rule and each paths-filter clause in turn, and show that a test fails. Leave the change uncommitted. |
| 4c | high | opus | none | After 4b. Add the Rust rules in a new `scripts/audit/checks/release_rust.py`, called from `ReleaseProcess.run()` where `Cargo.toml` and `release.yml` both exist, following decision 4.5. Read `templates/release-automation-rust/README.md`'s "Invariants" section; it is the authority, and its definitions of the three guards are exact strings. Write one rule per invariant, except where 4b's rules already enforce it: rule 1 covers the downstream-of-the-gate parts of 4 to 6 and 10, and rule 3 covers 13. Invariant 14 reads the template's scripts through `DEVELOPMENT_ROOT` and `read_within`, as `scripts/audit/checks/review.py` does (:222, :507-512), and the repository's copies through `repo.read_blob()`. An unreadable template is a `ToolFailure`. Invariant 15 searches `Makefile`, `*.sh` and `.github/workflows/*` through `repo.read()`. Add a table to `docs/audits/release-process.md` mapping invariants 1 to 15 to rules. Add a test that reads the README's numbered list and fails if the table lacks any number. Put each rule's pass and fail tests in `scripts/tests/test_release_rust.py`, along with tests for the template's own `release.yml`, which must pass with its placeholders left in. Run pre-commit, the unit tests and mutation-check, as in 4b. Leave the change uncommitted. |
| 4d | medium | opus | none | Add `release-merge-queue` to `scripts/audit/checks/github_config.py`, beside `MergeQueueConfig` (:260), as decision 4.6 describes. `applies()` reads the repository for `release.yml`, so no API call is made for a repository without one. `run()` reuses `MergeQueueConfig`'s default-branch and rules reads; factor them into a shared function rather than copying them. It fails, naming the branch, when there is no `merge_queue` rule, and passes otherwise. It does not judge the queue's settings. Write all six files in `docs/consistency-audits.md`'s "Adding a criterion" list: the check, its `registry.py` line, tests in `scripts/tests/test_github_config.py`, `docs/audits/release-merge-queue.md`, the `docs/audits/README.md` index line, and the frozen lines in `scripts/tests/test_metadata.py`. The issue title is `Release merge queue`. The spec explains why the criterion checks configuration and not whether the queue runs, by citing the decision's reasons rather than its number. Run pre-commit and the unit tests. Leave the change uncommitted. This step can run alongside 4b and 4c, since it touches other files. |
| 4e | high | opus | none | Read-only diagnosis of occystrap's merge queue (occystrap#169). It has never built a merge group: no `merge_group` run exists, and #164 sat queued from 2026-10-21 to 2026-10-24. Phase 3's execution notes in `docs/plans/PLAN-release-gating.md` list what has been ruled out; start from there, and do not repeat those checks. Compare occystrap with divergulent, whose queue works. Look at the ruleset's history, the PR timeline events (GraphQL `AddedToMergeQueueEvent` and `RemovedFromMergeQueueEvent`), GitHub's merge-queue state for occystrap's `develop`, organisation rulesets, the required check names against the job names that report them, and any app or integration setting that differs. Do not change anything in GitHub: no ruleset edits, no enqueueing, no pull requests, no comments. Report the cause with its evidence, or the remaining candidates with how to tell them apart, and the smallest fix. The management session takes the fix to the operator and records it on occystrap#169. |
| 4f | low | sonnet | own clone of ryll | In ryll, on a branch named `release-gating-phase-04`. Add an `actions/checkout` step at the start of `publish-ryll-pypi` in `.github/workflows/release.yml`, as andris's `publish-pypi` does (andris `release.yml:207`). Copy `tools/tag-release.sh` and `tools/sign-release-tag.sh` byte for byte, with `cp`, from this repository's `templates/release-automation-rust/`. Copy those two files only: the template-resync helper copies all five and would overwrite `release.yml` and the per-repository sections. Confirm with `cmp` and check that the mode is executable. Run ryll's pre-commit through its Docker wrapper; never install a Rust toolchain. Leave the change uncommitted. The management session commits, pushes and opens the pull request, which lands before this repository's. |
| 4g | low | sonnet | none | Verification, after 4a to 4d are committed, 4f has merged, and occystrap's single-tier pull request has merged. Run `pre-commit run --all-files` and the unit tests here. Run `scripts/audit-check.py` against this repository and diff its id-to-status map against `main`'s: only `release-process` and `release-merge-queue` may differ, and both must be `not_applicable`. Then run the Definition of done script against fresh clones of the fourteen, and report any failure with its full text. Read by hand the job graphs of kerbside, shakenfist and ryll, the three most complex, and confirm that rule 1's verdict matches. Do not file or close issues; do not run `audit-manage-issues.py` without `--dry-run`. |

Order:
1. 4a first; 4e and 4f can start at once.
2. 4b after 4a, then 4c; 4d alongside them.
3. 4g last.

The management session reviews each step and commits it with its
own subject. For the ryll pull request, and for each operator action
4e leads to, it asks first unless the back brief granted it.

Commit subjects:
* 4a(1): "Share the workflow job and trigger readers."
* 4a(2): "Move release-process into its own module."
* 4b: "Gate every release on CI in release-process."
* 4c: "Measure the Rust release invariants."
* 4d: "Require a merge queue where there are releases."

#### Risks and mitigations

* **The criteria file issues the first week after merge.** Measured
  before merge, the rules fail ryll (survey 3 and 6). The Rust rules
  that were not spot-checked could fail more. Mitigation: 4f lands
  ryll's fix first, and 4g runs the merged criteria against all
  fourteen. Any failure holds this pull request. The management
  session reads every failure, and either a repository pull request
  fixes it or the rule changes. A rule is never weakened just to make
  it pass.
* **A text reading of a job graph is wrong**, so a job reached through
  a spelling the helpers do not read counts as not downstream, or as
  downstream when it is not. Mitigation: the tests cover every spelling
  of `needs:` that `job_needs` documents, and 4g reads the three most
  complex graphs by hand.
* **A move in 4a changes behaviour.** For example, an import cycle, or
  a constant left behind that two modules then define differently.
  Mitigation: 4a's own done criteria are an unchanged test suite and an
  unchanged id-to-status map, and the management session checks the
  diff with `git diff --color-moved`, so only the import lines show as
  changed.
* **The Rust rules are too literal.** The README defines the guards
  as exact strings, so a harmless respelling fails. Mitigation: the
  README is the authority. A mismatch is fixed in the repository, or
  in the README and then the rule. The management session decides
  which, case by case, and records each decision in the execution
  notes.
* **The template's own `release.yml` fails its own rules**, because of
  its placeholders. 4c tests the template directly, so this shows up
  before any repository does.
* **occystrap's cause lies outside what the API shows**, such as a
  GitHub-side defect. Then 4e reports candidates rather than a cause.
  Mitigation: the operator decides whether to contact GitHub, or to
  remove and re-add the ruleset. The block stays recorded until a pull
  request merges through the queue.
* **`release-merge-queue` calls the API for repositories that never
  release.** `applies()` checks for `release.yml` first, so the API
  is called only for the fourteen.

#### Definition of done

* `pre-commit run --all-files` and
  `python3 -m unittest discover -s scripts/tests -t scripts` pass.
* mutation-check shows that deleting each new rule, and each clause of
  the paths-filter rule, fails a named test.
* `audit-check.py` against this repository differs from `main` only
  in `release-process` and `release-merge-queue`, and both are
  `not_applicable`.
* `scripts/audit/checks/packaging.py` no longer defines
  `ReleaseProcess` or any of its helpers, and no check module imports
  another, except that `release.py` imports `release_rust.py`, one
  check split across two files for size:
  `grep -n 'from audit.checks' scripts/audit/checks/*.py` prints only
  that line.
* Run from a directory of fresh clones of the fourteen release
  repositories, this prints only occystrap's `release-merge-queue`
  failure (decision 4.7):

  ```bash
  for r in */; do r=${r%/}
    python3 <development>/scripts/audit-check.py --repo-path "$r" --repo-name "$r" |
      python3 -I -c 'import json, sys
  for c in json.load(sys.stdin)["checks"]:
      if c["id"] in ("release-process", "release-merge-queue") and c["status"] != "pass":
          print(sys.argv[1], c["id"], c["status"], c["details"])' "$r"
  done
  ```
* hunkydory, ryll, instar and andris report `release-process` as
  `pass`, not `not_applicable`.
* `docs/audits/release-process.md` maps all fifteen Rust invariants
  to rules, and the test that reads the README passes.
* occystrap gates its releases on push runs: its single-tier pull
  request has merged, its "Develop branch" ruleset has no
  `merge_queue` rule, and `develop`'s tip has a successful `push` run
  of both gated workflows. This prints two lines, each `success`:

  ```bash
  tip=$(gh api repos/shakenfist/occystrap/commits/develop --jq .sha)
  for w in python-unit-tests.yml functional-tests.yml; do
    gh api "repos/shakenfist/occystrap/actions/workflows/$w/runs?event=push&head_sha=$tip" \
      --jq '.workflow_runs[0].conclusion'
  done
  ```

  plan-blockers b1 is resolved, and occystrap#169 is open and listed
  in Future work.
* Future work no longer asks for the merge-group path-filter rule,
  and records queue liveness as declined or, if the operator chose it,
  no longer lists it.

#### Back brief

Before 4a starts, the operator confirms:

1. **Decision 4.6:** `release-merge-queue` checks configuration only,
   or it also checks that the queue has run recently.
2. **Decision 4.2:** ryll adds a checkout, or the container-path rule
   learns to exempt ephemeral runners.
3. **Decision 4.7:** this repository's pull request waits for
   occystrap's queue to be fixed, or it merges with occystrap#169 still
   open, and occystrap is left failing nothing while it cannot
   release.
4. **Pushing and opening pull requests:** ask each time, or the same
   standing grant as phase 3. Merging, rulesets and anything 4e leads
   to are always asked at the time.

#### Phase 4 execution notes

Started on 2026-10-25 (AEDT).

* **Back brief.** The operator chose:
  - `release-merge-queue` checks configuration only (decision 4.6);
  - ryll adds a checkout, rather than the container-path rule
    exempting ephemeral runners (decision 4.2);
  - this repository's pull request waits for occystrap's queue to be
    fixed (decision 4.7);
  - the same standing grant as phase 3 to push and open pull
    requests. Merging, rulesets and anything 4e led to were asked at
    the time.
* **Order.** 4a, 4e and 4f ran in parallel: 4e only read, and 4f
  worked in its own clone of ryll. 4d ran after 4c rather than
  alongside it, because both run pre-commit and the unit tests in
  this worktree.
* **4a** sonnet at medium effort, as two commits: the shared
  workflow readers (2ba7e1f), then `release-process` in its own
  module (05df069).
* **4b** opus at high effort (248946e). The first version's
  push-concurrency rule flagged only a `cancel-in-progress: true`
  group. That followed decision 4.3 as written, but phase 3's survey
  had already shown that `false` drops a pending run too, so the rule
  was sent back and widened, and decision 4.3 is corrected at its
  source.
* **4c** opus at high effort (844eeff). The first version had
  `release_rust.py` import its helpers from `release.py`, and
  `release.py` call it through an import inside `run()`, so that the
  two imported each other. The 4c brief's "called from `run()`" and
  the Definition of done's empty import grep could not both hold. The
  operator chose to move the shared helpers into
  `audit/text/release_jobs.py` and keep one import, `release.py`'s of
  `release_rust.py`. The Definition of done's import item is corrected
  to allow exactly that line.
* **4d** opus at medium effort (9cad4f1). The criterion also had to
  join `NETWORK_CHECKS` in `scripts/audit_snapshot.py`, which
  "Adding a criterion" in `docs/consistency-audits.md` did not list;
  that page now says so.
* **4f** sonnet at low effort, as ryll#623 (67cc32e3). Its `cargo
  audit` and `cargo deny` jobs failed, and so did ryll#621's and #622's,
  so the failure was not this change's. The operator was told. A later
  run failed `cargo deny` on a yanked `crypto-bigint` 0.7.7 that
  `develop`'s lock file held. Rebased onto a `develop` holding 0.7.9,
  the pull request passed, and it merged on 2026-10-27 as f8b99b38.
* **4e** opus at high effort, read only. occystrap's event feed has
  never had a `gh-readonly-queue` branch created, while divergulent's
  appear within a minute of each enqueue. The queue's settings match
  divergulent's exactly, and toggling the ruleset on 2026-10-24 did
  not help. The diagnosis is posted on occystrap#169.
* **The occystrap probe.** The operator approved occystrap#170, an
  empty commit, to test the queue directly:
  - enqueued at 2026-10-24 23:32:14Z, it sat `QUEUED` at position 1
    with every check reported, and no merge group was built. It was
    never `AWAITING_CHECKS`, which rules out an unreported required
    check;
  - with the operator's approval, the "Develop branch" ruleset's
    `Can enqueue: unit` and `Can merge: unit` checks were dropped at
    00:29:05Z, leaving the same required checks as divergulent. The
    entry stayed `QUEUED`, with no group;
  - the operator re-enqueued #170 at 02:37:04Z. By 02:48:06Z it was
    still `QUEUED` at position 1, with no queue branch, and occystrap
    still had no `merge_group` run. The required checks are not the
    cause;
  - with the operator's approval, the ruleset was deleted and
    recreated from its JSON at 02:52:27Z (new ruleset 25807190, new
    merge queue `MQ_kwDOFKiz484ABO4F`). Re-enqueued at 03:40:16Z, #170
    was still `QUEUED` with no group at 03:51. A runner shortage is
    ruled out too: GitHub creates the queue branch before any
    workflow runs, and occystrap had no run waiting for a runner. The
    repository's merge methods, Actions permissions, protection and
    fork status match divergulent's. A GitHub support ticket was
    drafted for the operator, and the results are posted on #169.
* **occystrap goes single tier.** The operator chose to disable the
  queue rather than wait on GitHub, and decision 4.7 is rewritten at
  its source. The merge_queue rule and the two "Can merge" required
  checks came out of the ruleset at 04:46:40Z, and #170 was closed.
  An occystrap pull request adds the push trigger the gate then
  needs. The operator also chose to let `release-merge-queue` fail
  occystrap rather than exempt it, with #169 kept open.
* **Review rounds.** Writing occystrap's single-tier change (90eadd6)
  showed that `push_concurrency_issues` passed a push group keyed by
  `github.ref` behind an `event_name == 'push'` branch. It now also
  requires `github.sha` in such a group (0cab7d4). A review round on
  this pull request moved the gate's job selection into one shared
  function, so the gate rule and the Rust rules cannot disagree, and
  taught the triggers to read a scalar `on:` (dd1a20a).
* **occystrap#174 merged** on 2026-10-25 at 22:49Z (199f5b8e). The
  first push runs of both gated workflows on `develop` succeeded; the
  functional job waited about 90 minutes for a runner.
* **4g** sonnet at low effort, read only, on 2026-10-27, against
  fresh clones of the fourteen and this branch at dd1a20a:
  - pre-commit passes, and 2411 unit tests pass;
  - this repository's id-to-status map differs from `main`'s only by
    the new `release-merge-queue`, which is `not_applicable`;
    `release-process` is `not_applicable` on both;
  - the Definition of done loop printed one line, occystrap's
    expected `release-merge-queue` failure. `release-process` passes
    for all fourteen, and `release-merge-queue` for the other
    thirteen;
  - by hand, kerbside's, shakenfist's and ryll's `release.yml` reach
    every `release` environment job and every publishing job only
    through a job whose `if` checks the gate's result, which matches
    rule 1's pass for all three. In ryll the gate is checked once, by
    `check-version`, and the builds need `check-version`'s success;
  - the import grep, the fifteen-invariant table, occystrap's push
    runs and ruleset, b1, #169 and Future work all hold as the
    Definition of done states.

### Phase 5: push audit

Runs each repository's `PUSH-AUDIT.md` over the merges phases 3 and
4 landed there, and checks that every release still fails closed by
dispatching `release.yml` as a dry run. Planned on 2026-10-28
(AEDT), after phase 4 was closed out.

**Planning effort:** high. **Review effort:** high for the security
reading, as every runbook asks; the triage is the management
session's.

#### Scope

In:

* The 29 merges that phases 3 and 4 landed, in sixteen repositories
  (the landing record below), each read with its own repository's
  runbook, or with the shared block's floor checklist where the
  repository has none.
* A `workflow_dispatch` dry run of `release.yml` on `develop` in each
  release repository whose current `release.yml` has not had one.
* The record: a verdict per landing, per runbook brief or checklist
  item, in this section.

Out:

* Phases 1 and 2. Each ran its push audit before its pull request
  opened (development#288's body, and phase 2's execution notes), so
  this phase cites them, as the shared block asks.
* Fixing findings. They land after this phase merges, as their own
  pull requests in the repositories they concern.
* Problems that predate this plan. They are filed as issues, not
  findings.
* occystrap's queue (occystrap#169) and every other Future work
  entry. A finding that turns out to be one is cross-referenced.
* Adding a runbook to the five repositories without one. The
  `push-audit` criterion measures that, and it predates this plan.

#### Landing record

Every landing is a merge commit with two parents, so every range is
`<sha>^1..<sha>`, read against that repository's default branch. Each
merge carries its `git diff --shortstat` insertions and deletions,
which every audit report must match.

| Repository | Merges | Runbook |
|---|---|---|
| development | f3f8472 (#294, +968/-87), 8d032a5 (#295, +1363/-3), 10e3145 (#299, +53/-22), d668b98 (#300, +16/-4), 3e0ced1 (#304, +159/-6), e7a34fc (#328, +5625/-1378) | yes |
| actions | 658d767 (#184, +69/-6), e1e2c85 (#185, +32/-8) | yes |
| andris | 288aba3 (#34, +1064/-690) | yes |
| client-python-k3s | 6d40cee (#154, +198/-32) | yes |
| divergulent | 70ae4c3 (#132, +269/-23) | yes |
| hunkydory | b421cef (#103, +457/-82) | yes |
| instar | a31e0d1 (#796, +939/-146) | yes |
| kerbside | d30ff34 (#582, +214/-39) | yes |
| occystrap | e9e66d9 (#163, +487/-59), 199f5b8 (#174, +161/-104) | yes |
| ryll | d70ff4d (#591, +879/-421), 20e3cd4 (#599, +23/-6), f8b99b3 (#623, +10/-4) | yes |
| shakenfist | b3dc0c6 (#4597, +94/-30), 6a3f307 (#4581, +202/-37), 1ec1a6c (#4607, +76/-7) | yes |
| agent-python | 65a1778 (#155, +315/-41) | no |
| client-python | d789e0b (#432, +391/-21) | no |
| client-python-ova | 4a8eeed (#29, +240/-35), 3b40144 (#30, +35/-3), 4b1764d (#31, +25/-13) | no |
| clingwrap | f4966df (#145, +302/-18) | no |
| library-utilities | 0940cbe (#74, +300/-27) | no |

#### What the survey found

Checked on 2026-10-28 against GitHub, the default branch of each
repository, and fresh clones of the fourteen taken on 2026-10-27.

1. **Every recorded landing is a two-parent merge on its default
   branch.** For each of the 31 in the Execution table, GitHub's
   commit has two parents, its compare against the default branch is
   `ahead`, and its pull request's merge commit is the recorded sha.
   The diffs run from +10/-4 (ryll#623) to +5625/-1378
   (development#328, 24 files). Per repository, the changed lines
   outside this one total between 115 (`actions`) and 1,754
   (andris); this repository's six merges total 9,684.
2. **No landing of phase 3 or phase 4 was push-audited.** The shared
   block says a landing in another repository "is audited against that
   repository's default branch, as part of the pull request that lands
   it", and that the plan's own phase cites that audit. No body or
   comment of those 29 pull requests mentions one, and the execution
   notes record none. Phases 1 and 2 did run one. So this phase runs
   the audits that those pull requests should have carried, rather
   than citing them.
3. **Nine of the fourteen have a `PUSH-AUDIT.md`**: andris,
   client-python-k3s, divergulent, hunkydory, instar, kerbside,
   occystrap, ryll and shakenfist. agent-python, client-python,
   client-python-ova, clingwrap and library-utilities have none, nor a
   legacy `PUSH-TEMPLATE.md`. `actions` has one. Seven of the nine,
   and `actions`, split the audit into a mechanical wave 1 and four
   wave 2 briefs, the last of them security at opus and high effort.
   client-python-k3s's and occystrap's are a single checklist of six
   sections, with no waves and no model named.
4. **Only two release repositories have had a dry run since they
   adopted the gate.** client-python-ova's (run 39303533446, at
   4a8eeed on 2026-10-19) and instar's (run 39476356008, at a31e0d1
   on 2026-10-20). Neither repository's `release.yml` has changed
   since, and nor have `check-dist` or `require-ci-success` in
   `actions`, whose last changes (actions#184, and f53cd9b in
   actions#182) predate both runs. The other twelve have no dispatch
   run since their phase 3 merge, and ryll, andris, divergulent,
   library-utilities, clingwrap, agent-python, client-python and
   shakenfist have never had one.
5. **A dry run cannot publish.** All fourteen `release.yml` files have
   a `workflow_dispatch` trigger. ryll gained one when it converged on
   the Rust template, although this plan's phase 3 survey said it had
   none. On 2026-10-27, step 4g found that `release-process` passes
   all fourteen, and its dispatch-guard rule keeps every publishing
   job off a dispatch. `release-environment-protection` passes all
   fourteen too, so a job that slipped through would still wait for a
   reviewer.
6. **That `ci-gate` is upstream of every environment is now
   measured.** The old text of this section asked for it to be
   confirmed by reading. Rule 1 of `release-process` checks it every
   week for all fourteen, and 4g read kerbside's, shakenfist's and
   ryll's graphs by hand and agreed with the rule. The audits still
   read it, as a check on the rule, but they are not its only
   evidence.
7. **This section's old text was stale in two places, and was
   rewritten when this phase was planned.** It said the audit covers
   "whatever fixed occystrap's queue". Nothing fixed it: occystrap#174
   moved occystrap to a single tier of CI, and is audited here like any
   other landing. It also asked for every repository to be dry-run,
   which survey item 4 narrows to twelve.

#### Decisions

5.1. **Phases 3 and 4 are audited per repository, with that
     repository's runbook, one range per merge.** Each agent sets
     `AUDIT_RANGE=<sha>^1..<sha>` for each of its merges, in a fresh
     clone of the repository's default branch. Before a hit is
     reported as a finding, the agent checks it against the default
     branch's tip, because a later merge may already have fixed it.
     A finding fixed since is recorded as such, not dropped.
5.2. **Outside this repository, one agent runs a repository's whole
     runbook.** Eight runbooks split wave 2 into four independent
     readers. For 115 to 1,754 changed lines that is four agents
     re-reading the same few files, about forty agents across the
     fleet, and independence buys little at that size. So for
     `actions` and the nine, one agent per repository runs wave 1 and
     then each wave 2 brief in turn, or each section of a single-pass
     runbook, reporting under each brief's or section's own heading.
     It runs at opus and high effort, the strongest any runbook names.
     This repository keeps the four separate wave 2 readers that its
     runbook asks for. Its six merges change 9,684 lines, and its
     blast radius is the fleet. This is the decision a reviewer is
     most likely to push back on, because it bends what nine runbooks
     say. The guard against what it gives up is the
     totals rule. Every report states the insertion and deletion
     totals it measured for each range, and a report whose totals do
     not match the landing record is re-run rather than triaged.
5.3. **The five repositories without a runbook get the shared block's
     floor checklist, with this plan's own questions, from one
     agent.** The block forbids borrowing another repository's
     runbook. The five landed the same Python template change, so one
     reader comparing them side by side is more likely to see a
     difference between them than five readers who each see one. The
     plan's questions are:
     - Can any job that names an environment, or that publishes, run
       when `ci-gate` fails or is skipped on a tag push?
     - Is every check that the default branch's ruleset requires
       reported by some job, on both `pull_request` and
       `merge_group`?
     - Can a merge group's run be cancelled by a newer entry?
     - Does `check-dist` read the same directory that the upload
       step uploads?
     - Did the change remove or rename anything, such as a trigger, a
       job name, a script or a step, that something else still
       names?
     The other eleven repositories' agents (this repository's,
     `actions`' and the nine in 5g) ask these questions too, after
     their runbook's own.
5.4. **Twelve dry runs, dispatched by the management session after
     the operator's yes at the time.** Dispatching runs live CI, so it
     needs a yes, as phase 3's did. One yes covers the batch. The
     twelve are every release repository except client-python-ova and
     instar, whose runs survey item 4 cites. A dry run passes when
     `ci-gate` and every publishing job are skipped, and every build
     job, and every job with a `check-dist` step, succeeds. A
     deployment review request during a dry run means a job reached
     an environment. The operator rejects it, and it is a blocking
     finding.
5.5. **Findings land after this phase, and the plan completes in the
     pull request that carries them.** This phase's own pull request
     carries the record and the plan bookkeeping it implies: phase 4's
     close-out, Future work and the index row. Each repository's fixes are
     one pull request in that repository, made after the operator has
     read the record. A finding not fixed is declined here, in a
     sentence that says why. The follow-up pull request in this
     repository records this phase's merge commit and sets the plan to
     `Complete`. If the audit finds nothing, this phase sets itself to
     `Complete` in its own pull request, as the `plan-phase-landing`
     block says.

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | low | sonnet | none | Wave 1 of this repository's `PUSH-AUDIT.md`, read only, in a detached worktree of `origin/main` after `git fetch origin`. Run `pre-commit run --all-files` once. Then run every grep under "Then the grep-level checks on the diff" once for each range: `f3f8472^1..f3f8472`, `8d032a5^1..8d032a5`, `10e3145^1..10e3145`, `d668b98^1..d668b98`, `3e0ced1^1..3e0ced1` and `e7a34fc^1..e7a34fc`, with `AUDIT_RANGE` exported for each. Report each range's `git diff --shortstat`, and every hit with its range, file and line. Say for each hit whether it still holds at `origin/main`. Change nothing. |
| 5b | medium | sonnet | none | Wave 2 brief 2a (code quality) of this repository's `PUSH-AUDIT.md`, verbatim, over the six ranges in 5a, with 5a's report as input. Read only. State each range's shortstat. For each finding give the range, the file, the line, blocking or advisory, and whether it still holds at `origin/main`. |
| 5c | medium | sonnet | none | Wave 2 brief 2b (tests), as 5b. Ranges and rules as in 5b. |
| 5d | medium | sonnet | none | Wave 2 brief 2c (documentation), as 5b. Ranges and rules as in 5b. Also check that `templates/release-automation/README.md`, `templates/release-automation-rust/README.md` and `docs/audits/release-process.md` state each invariant and each rule the same way. |
| 5e | high | opus | none | Wave 2 brief 2d (security), as 5b. Ranges and rules as in 5b. Add these, because the templates ship to fourteen repositories. Can a tag, a branch name, a fork, a `workflow_dispatch` input or a merge group reach a publishing job without the gate? Does any template or rule fail open when GitHub returns something unexpected? |
| 5f | high | opus | own fresh clone of `shakenfist/actions` | `actions`' own `PUSH-AUDIT.md`, all of it, over `658d767^1..658d767` and `e1e2c85^1..e1e2c85`, as decision 5.2 describes: wave 1, then each wave 2 brief in turn, reported under its own heading, with each range's shortstat. Then the questions in decision 5.3 that apply to a shared action or workflow. Read only. |
| 5g | high | opus | own fresh clone per repository | One agent for each of andris, client-python-k3s, divergulent, hunkydory, instar, kerbside, occystrap, ryll and shakenfist, run in parallel. Each runs its repository's whole `PUSH-AUDIT.md` over that repository's ranges in the landing record, as decision 5.2 describes, then the questions in decision 5.3. Where a runbook's wave 1 needs a toolchain, use its Docker wrapper or report the step as not run. Never install a Rust toolchain on the host. Read only: no pull requests, comments or issues. Each report states each range's shortstat. |
| 5h | high | opus | own fresh clone per repository | One agent for agent-python, client-python, client-python-ova, clingwrap and library-utilities, as decision 5.3 describes. For each repository and range, give a verdict on each of the five items in the `plan-push-audit-phase` block's floor checklist, or a sentence on why one does not apply, and then on each of decision 5.3's questions. Item 4's greps run across all fourteen release repositories and `actions`, and the report states them. Then compare the five diffs, and report every place where one repository did something differently from the others, saying whether that is a defect or a deliberate per-repository choice. Read only. Each report states each range's shortstat. |
| 5i | low | sonnet | none | After the management session has dispatched the twelve dry runs (decision 5.4), read each run with `gh api repos/shakenfist/<r>/actions/runs/<id>/jobs --paginate --jq '.jobs[] \| "\(.name)\t\(.conclusion)"'`. Classify each job against that repository's `release.yml` as gate, build, `check-dist` carrier or publishing, and report the repository, the run URL, the head sha, and pass or fail against decision 5.4's test. For a failure, report the failing step's log tail. Do not re-run or cancel anything. |
| 5j | high | management session | none | Triage every report: blocking or advisory, a finding or something that predates the plan, fixed since or still live. Write the record into this section as *Phase 5 results*: one row per landing, a verdict per runbook brief or checklist item, the dry-run table, and the findings list with the fate of each. Run the private-data probes over the added text. Commit the record and open the pull request under the standing grant. |

Order:
1. 5a first, then 5b to 5e in parallel, which read 5a's report.
2. 5f, 5g and 5h at once, alongside 5a. They work in their own clones
   and read nothing that 5a writes.
3. The dry runs as soon as the operator says yes, then 5i.
4. 5j last.

Commit subject: "Record the release gating push audit."

#### Risks and mitigations

* **A brief expands to the empty diff, and an audit of nothing reads
  as clean.** Every brief names its ranges literally, and every report
  states its totals. The management session compares them with
  `git diff --shortstat <sha>^1..<sha>` before triaging, and re-runs a
  report whose totals differ.
* **One reader per repository misses what four independent ones would
  have caught** (decision 5.2). The security brief, which is where a
  miss costs most, runs at opus high in every repository, and the
  sixteen repositories' answers to decision 5.3's questions are
  compared with each other in 5j. A question answered differently for
  two repositories that landed the same change is re-read by hand.
* **A dry run publishes.** Survey item 5 says it cannot. If one does,
  the operator sees a deployment review request and rejects it, and
  that is a blocking finding against the dispatch-guard rule as well
  as against the repository.
* **The dry runs cost CI capacity.** They build only, and they share
  the static and VM pools with live work. The management session
  dispatches them in one batch and waits for them, rather than
  re-running any that fail. A failure is triaged, not retried.
* **The audit grows into a fleet review.** Hits on code these merges
  did not touch are backlog, not findings, as the runbooks say.
  Problems that predate the plan become issues, filed only after the
  operator has seen the list.
* **A runbook asks for something the host cannot do**, such as a
  native Rust toolchain or a running cluster. The agent reports the
  item as not run, with the reason. 5j records it as not checked
  rather than passed.

#### Definition of done

* The landing record lists all 29 merges, and *Phase 5 results*
  gives each a verdict for every runbook brief, or for every floor
  checklist item and decision 5.3 question.
* Every report's totals match:

  ```bash
  git -C <clone> diff --shortstat <sha>^1..<sha>
  ```

  for each range it read.
* Each of the twelve dry runs exists on `develop`, concluded, with
  `ci-gate` and every publishing job `skipped`, and every build and
  `check-dist` job `success`. This prints one line per job for
  reading against the table in *Phase 5 results*:

  ```bash
  for id in <the twelve run ids>; do
    gh api "repos/shakenfist/<r>/actions/runs/$id/jobs" --paginate \
      --jq '.jobs[] | "\(.name)\t\(.conclusion)"'
  done
  ```

* No deployment review request was raised by a dry run:
  `gh api repos/shakenfist/<r>/actions/runs/<id>/pending_deployments`
  returns `[]` for each.
* Every finding is fixed by a linked pull request or declined in a
  sentence here. Every pre-plan problem is a linked issue.
* `pre-commit run --all-files` passes, and `scripts/audit-check.py`
  against this repository gives the same id-to-status map as `main`.
* The plan's Execution row for phase 5, and its `docs/plans/index.md`
  row, read `Complete` in this pull request if there were no
  findings. Otherwise they are `Complete` in the follow-up pull
  request, which records this phase's merge commit.

#### Back brief

Before 5a starts, the operator confirms:

1. **Decision 5.2:** one agent per repository runs the whole runbook
   outside this repository, or every runbook's four wave 2 readers
   run as written, about forty agents.
2. **Decision 5.4:** the twelve dry runs, dispatched together after a
   yes at the time, or a smaller set, or none.
3. **Issues for pre-plan problems:** the management session files
   them after the operator has seen the list, or the operator files
   them.
4. **Pushing and opening this phase's pull request:** the same
   standing grant as phases 3 and 4, or ask.

#### Phase 5 execution notes

Started on 2026-10-28 (AEDT).

* **Back brief.** The operator chose:
  - one agent per repository running its whole runbook, outside this
    repository (decision 5.2);
  - all twelve dry runs, dispatched together; the answer was the yes
    at the time (decision 5.4);
  - the management session files the issues for pre-plan problems,
    after the operator has seen the list;
  - the same standing grant as phases 3 and 4 to push and open this
    phase's pull request.
* **Order.** The twelve dry runs were dispatched at 05:27Z, before
  any agent started. 5a, 5f, the nine 5g agents and 5h ran at once.
  5b to 5e started when 5a reported, and 5i started when the last dry
  run finished at 06:03Z. 5b was sent back once, to read the e7a34fc
  diff line by line for the 2a items it had first judged from the
  diff stat alone.
* **Totals.** Every report's insertion and deletion totals matched
  the landing record, for all 29 ranges, so no report was re-run.
  That proves each range was opened, not that it was read: 5b's
  totals matched before it was sent back. Reading its report against
  its brief caught it, and no rule would have: a lesson for
  `PLAN-push-audit-phase.md`.
* **Host load.** With up to sixteen agents running at once (twelve
  from the start, and 5b to 5e while the 5g agents were still
  building), the host's load average reached 78, and andris's
  timing-bound `make test` timed out locally. The merge changed no
  Rust, and andris's `merge_group` run on `develop`'s tip succeeded,
  so this is recorded as not cleanly run rather than as a finding.
* **The record's own checks.** `pre-commit run --all-files` passed,
  `scripts/audit-check.py` gave the same status for all 66 checks as
  on `main`, and a probe of the added text for addresses, private
  hostnames, tokens and email addresses found nothing.
* **Review of #337.** A second session answered the first automated
  review (636006f) while this one waited; the operator then handed
  #337 back. Rounds two and three took one and two fix items, all of
  them wording in this record, so no fourth round was asked for. #337
  merged as ebcab8b.
* **Issues.** The operator read the pre-plan list and the management
  session filed all six, the three security items first.
* **Fixes.** Each finding's fix is listed under *Fixes* below. D1 was
  fixed by opus at high effort and the rest by sonnet at medium,
  except the template documentation, opus at medium. D1's change
  kills 23 of 23 mutants, and every check gives the same status and
  details for all fourteen release repositories before and after.
  #339's own review found that invariant 11 read a write scope more
  narrowly than the new gate rule did, and #339 aligns them.
* **instar's tag ruleset.** R3 said instar's `RELEASE-SETUP.md` asks
  for a tag ruleset; instar has no `RELEASE-SETUP.md`, and the step
  is the release template's. With the operator's yes, instar now has
  a "Restrict releases" tag ruleset (26021750), copied from ryll's
  14655450 and read back identical.
* **Order.** #339 changes `sign-release-tag.sh`, so andris, instar
  and ryll fail invariant 14 from its merge until their resync pull
  requests merge. Each is held on #339 in plan-blockers.

#### Phase 5 results

**Every landing fails closed.** For all fourteen release repositories
the answer to decision 5.3's first question is no: no job that names
an environment or publishes can run when `ci-gate` fails or is
skipped on a tag push. Every required check is reported on both
`pull_request` and `merge_group` (occystrap's queue is off, so only
the first applies there). A newer entry does cancel an older queue
run, as decision 3.1 intends: on a first attempt every consumer's
concurrency group keys on the base branch. A re-run keys on its own
queue branch, so it neither cancels a live entry nor is cancelled by
one. Every `check-dist` step reads the directory its job uploads.
No report found a blocking finding in the fleet's workflows. The
live findings are in this repository's rules and templates, and in
documentation.

| Repository | Ranges | Runbook | Verdict |
|---|---|---|---|
| development | 6 | wave 1 (5a), 2a to 2d (5b to 5e) | findings D1 to D6 |
| actions | 2 | whole runbook (5f) | finding X1; one pre-plan issue |
| andris | 1 | whole runbook | finding R1; `make test` not cleanly run here |
| client-python-k3s | 1 | single-pass runbook | advisory only; one pre-plan issue |
| divergulent | 1 | whole runbook | finding R2 |
| hunkydory | 1 | whole runbook | advisory only; one pre-plan issue |
| instar | 1 | whole runbook | finding R3; tag ruleset missing |
| kerbside | 1 | whole runbook | finding D4 started here |
| occystrap | 2 | single-pass runbook | advisory only |
| ryll | 3 | whole runbook | finding R4; D3 started here |
| shakenfist | 3 | whole runbook | advisory only; one pre-plan issue |
| agent-python | 1 | floor checklist (5h) | clean |
| client-python | 1 | floor checklist (5h) | finding R5 |
| client-python-ova | 3 | floor checklist (5h) | one defect, fixed since by #31 |
| clingwrap | 1 | floor checklist (5h) | finding R5 |
| library-utilities | 1 | floor checklist (5h) | clean |

The floor checklist's five items passed for all seven of 5h's ranges:
every landing is a two-parent merge; nothing unattended was added; no
secret, private hostname or address entered a repository; nothing
removed or renamed is still named (5h grepped all fourteen release
repositories, `actions` and this one); and every release fails
closed. R5 and client-python-ova's fixed defect came from decision
5.3's questions, not from the floor checklist.

**Dry runs.** All twelve were `workflow_dispatch` runs on `develop`
that concluded `success`, with `ci-gate` and every publishing job
skipped, every build job successful, every `check-dist` step run and
successful, and no pending deployment or approval.

| Repository | Run | Head |
|---|---|---|
| agent-python | 40638078010 | ce31934e |
| andris | 40638081095 | d508b778 |
| client-python | 40638084091 | 05d04ebb |
| client-python-k3s | 40638087137 | 74c69893 |
| clingwrap | 40638090421 | 8a579925 |
| divergulent | 40638093494 | 2cc81773 |
| hunkydory | 40638096377 | 1ed3acba |
| kerbside | 40638099406 | 9d8bf6f1 |
| library-utilities | 40638102507 | 70cbf5dc |
| occystrap | 40638105643 | 6a86cc84 |
| ryll | 40638108706 | e49ddce5 |
| shakenfist | 40638111603 | 9f7d908f |

client-python-ova (39303533446) and instar (39476356008) are cited, as
survey item 4 says. hunkydory publishes a `.vsix`, not a wheel, so it
has no `check-dist` step; its digest step and upload read the same
directory.

**Findings to fix.** Each lands after this phase merges, as one pull
request per repository (decision 5.5).

* **D1. `release-process` fails open in six ways** (5e, e7a34fc). The
  management session reproduced each with 5e's fixture script: thirteen
  workflows that should fail all pass, and the control fails. No
  release workflow in the fleet is spelled any of these ways today.
  - The gate step's `continue-on-error: true`, or `if: ${{ false }}`
    on it, or the Rust gate job's `continue-on-error`, passes.
  - A `sha:` input on the gate step passes, so the gate can approve a
    commit other than the one released.
  - The Python rules' status-function check (`release_jobs.py:63`)
    knows only `always()` and `!cancelled()`, so a `sign-tag` guarded
    by `(success() || failure()) && ...` passes. Result literals are
    also matched case-sensitively, where GitHub is not.
  - The Rust rules' `job_effects` (`release_rust.py:159`) misses
    `packages: write` and other write scopes, so such a job under the
    build-job guard runs on a dispatch with no gate, and passes.
  - The Python gate rule requires only environment jobs to be
    downstream of the gate, so a `github-release` job with
    `contents: write` and no environment can need only an ungated job.
  - `STEP_USES_RE` (`workflows.py:439`) misses a `uses:` with a trailing
    comment, the form Renovate writes for a digest pin, so an
    unguarded publishing step stops counting as publishing.
* **D2. The Rust template README is narrower than the rules it says
  it governs** (5d). Invariant 7 does not mention `write-all`,
  `secrets['...']` or `secrets: inherit`; invariant 13 says "uploads a
  path naming `*.whl`" where the rule finds wheel jobs by their build
  and requires the check after it; invariant 1 does not say
  `branches-ignore`. `release-process.md:159-160` says the guards are
  read "as exact strings", which its own lines 195 to 197 contradict
  for the build-job guard. The Python template README's prerequisites
  omit the `paths-filter` `base_sha` rule and the named-workflow-exists
  rule, and say "the default branch" where the rule reads `develop`.
* **D3. The Rust template's `sign-release-tag.sh` carries a
  `<repository>` placeholder** in its verify-command comment (ryll
  5g). Invariant 14 requires the script byte for byte, so phase 4's
  copy into ryll (ryll#623) undid ryll's filled-in name, and no
  repository now says how to verify a release tag.
* **D4. A bypassed merge can be released through a later docs-only
  queue entry** (kerbside 5g). In a repository whose CI filters by
  path, a docs-only entry on top of a bypassed code merge skips the
  code tests and still succeeds, so the gate accepts it. The template
  README's recovery table says to "tag a later commit that went
  through the queue", which is that commit, and kerbside's
  `docs/development.md` says the same. client-python-k3s already says
  "a later non-docs queue entry". The fix rewords the template's
  recovery table and kerbside's `docs/development.md` the same way;
  the gate's blindness is in Future work.
* **D5. Duplicated and fragile helpers** (5b). `step_uses()` in
  `release_rust.py:284` repeats `step_action()` in `workflows.py:442`
  without its version split; `test_release_rust.py:189,194` split
  READMEs on bare heading literals.
* **D6. `README.md`'s Rust template bullet** (5d) runs to four lines
  where its siblings take one; the detail is already in
  `docs/release-automation.md`.
* **X1. `actions`' `docs/consuming.md` Mode 2 snippet** uses `<job>`
  inside the concurrency group expression, so two jobs pasted
  unchanged share a group and cancel each other (5f).
* **R1. andris:** `AGENTS.md:115` calls every script in `tools/` a
  template copy, but `tools/test-release-version.sh` is andris's own;
  `docs/releasing.md`'s "the tag names a different version" recovery
  runs `tag-release.sh` without the `COMMIT` that the template's
  recovery gives.
* **R2. divergulent:** `docs/development.md:56-60` still calls the
  reviewer the job at the bottom of `unit-tests.yml`, which #132 made
  false; `RELEASE-SETUP.md` section 4 does not say that a pull request
  opened with `GITHUB_TOKEN` never reports "Can enqueue".
* **R3. instar:** `docs/development.md:543` assumes a tag ruleset
  that instar does not have, and `CHANGELOG.md` has no entry for the
  new release process. The operator decides whether to create the
  tag ruleset the release template's `RELEASE-SETUP.md` recommends, a
  settings change. (Corrected: instar has no `RELEASE-SETUP.md` of its
  own.)
* **R4. ryll:** `fuzz.yml:300-302` and
  `tools/report-fuzz-failure.sh:377-379` cite the version-mismatch
  issue step that #591 removed;
  `publish-ryll-pypi`'s checkout (`release.yml:426`) has no comment
  saying why it is there. Its verify command comes back with D3's
  fix.
* **R5. client-python and clingwrap** keep a workflow-level
  `pull-requests: write` in `functional-tests.yml` (:21 and :10) after
  adding `merge_group`, so queue runs install dependencies with a
  write-scoped token. agent-python removed it in the same change for
  that reason. client-python's `functional-tests.yml:161-163` also says
  `smoke-cluster.yml`'s key lacks the re-run guard, which `actions`
  added before #432 merged.

**Fixes.**

| Finding | Pull request |
|---|---|
| D1, D2, D3, D4 (template), D5, D6 | development#339 |
| D4 (kerbside) | kerbside#628 |
| X1 | actions#214, which also fixes actions#213 |
| R1, and D3's resync | andris#53 |
| R2 | divergulent#137 |
| R3, and D3's resync | instar#847, and the tag ruleset above |
| R4, and D3's resync | ryll#657 |
| R5 | client-python#443, clingwrap#148 |

**Fixed since.** client-python-ova#29's aggregators used
`!cancelled()`, so a cancelled run skipped them into a pass; #31
changed them to `always()`. ryll#591's build jobs had no `if:`, so a
dry run skipped them; #599 fixed it. shakenfist#4581's "Can merge"
did not need `sanity_checks` and accepted any skipped job; 896b2aca8
fixed both (shakenfist#4582, closed). occystrap had no
gate-countable CI between #163 and #174; #174 fixed it, and no tag
was cut in the window.

**Declined, with reasons.**
* Files over 800 lines: `release_rust.py` (989), `test_release.py`
  (1,355) and `test_release_rust.py` (860) are new, but e7a34fc moved
  that code out of two files over 2,000 lines, and each mirrors one
  seam already.
* Long comments: hunkydory's concurrency comment, repeated in three
  workflows, and client-python-k3s's `release.yml:55-64` and
  `functional-tests.yml` comments explain contracts that are easy to
  break, which `comment-proportion` allows.
* Exact-string assertions in `test_release_rust.py` and
  `test_release.py:943` pin which rule produced a finding, which is
  their purpose.
* `audit/text/release_jobs.py` is tested through the checks that use
  it, as the rest of `audit/text/` is.
* occystrap's lines over 120 characters are in YAML comments and in a
  `jq` line copied from the template.
* shakenfist's `tools/check-wheel-data.py` lacks type hints on a
  40-line release tool, and its sf-resources fix has no unit test for
  a raising poll; the cluster test it fixed is the proof.
* andris's `make test` is not re-run: see the execution notes.

**Pre-plan problems, filed as issues** once the operator had read
this list. Items 1, 4 and 5 are security problems in what this plan
protects, so they were filed first. None is in a recorded range:
item 5's lines date from f093fb0 (February 2026) and item 4's from
shakenfist a335760 (June 2026), and the merges that touched those
files left the lines alone.
1. Security, hunkydory#127. hunkydory: four required checks ("Build,
   test and lint", "Pre-commit hooks", "Analyze", "gitleaks") carry
   no `integration_id`, so any app could satisfy them.
2. client-python-k3s#159: `release.yml:272` cites a plan step in a
   comment.
3. actions#213: `docs/consuming.md:60,85` and `smoke-cluster.yml:199`
   link to `development/blob/main/audits/...`, which moved to
   `docs/audits/` in August.
4. Security, shakenfist#4711. shakenfist: `release.yml:294`
   interpolates a secret into a `run:` line.
5. Security, development#338. The Python template's `sign-tag` checks
   gitsign against a `checksums.txt` from the same release, fetched with
   `curl -sLO` and no `-f`, where the Rust template pins a hash; and
   its `build` checks out without `persist-credentials: false` on the
   static pool.
6. instar#846: the release `build` job runs on a persistent runner
   rather than a `vm` one.

`sign-tag` on a non-`vm` runner in the Python repositories is
already in Future work, and clingwrap's missing path filter is
clingwrap#118.

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

* **Tag rulesets** for the eight release repositories without one
  (agent-python, clingwrap, divergulent, instar, kerbside,
  library-utilities, shakenfist, client-python-ova), and a criterion
  for them. Defence in depth behind the reviewer gate.
* **The `develop` ruleset bypass** that let kerbside#576 merge with a
  failed queue run: `PLAN-bot-bypass-identity.md`.
* **The token named in `PLAN-bot-bypass-identity.md` step 3c** is
  `AUDIT_TOKEN`, but the checks run under `AUDIT_READ_TOKEN`. Step 1a
  here settles whether that token reads environments. 3c should be
  corrected when that plan starts.
* **A `release.yml` that calls a reusable workflow** names its
  environments in the called workflow. `release-environment-protection`
  reads only `release.yml`, so it would report that no environment is
  named. That is the closed direction, but the message is wrong. If
  `PLAN-workflow-ref-trust.md` phase 5 converts the template into that
  shape, the criterion must follow the `uses:` reference, in the same
  change that carries the `ci-gate` job.
* **`check-dist` is tested only by its unit tests** in
  `shakenfist/actions`. Running the action itself in that
  repository's `ci.yml`, against a small fixture wheel, would catch a
  broken `action.yml` before the fleet does. The same goes for
  `require-ci-success`, though that needs a real repository's runs to
  read.
* **A tag ruleset that refuses branch-named tags** would close the
  `head_branch` spoof at its source. The gate already refuses such a
  tag unless it sits on the default branch's first-parent history, so
  this is defence in depth, alongside the tag rulesets above.
* **Releasing from a branch other than the default** needs a gate
  change. A `branch` input would name the branch whose runs and
  first-parent history count, and would need to say how a reviewer
  knows that branch's CI is trustworthy. No repository has a release
  branch today.
* **The gate could cross a merge commit whose diff against its first
  parent touches only review marks.** hunkydory's `reviews` merges get
  no `ci.yml` run, because of its `paths-ignore`, so a tag on one
  fails today. The diff against the first parent is the whole of what
  the merge changed, so crossing it is as safe as crossing a prune
  commit. That is a change to `require-ci-success` in
  `shakenfist/actions`.
* **Template drift found by phase 3's survey**:
  - eight copies of `sign-tag` (agent-python, client-python,
    clingwrap, divergulent, library-utilities, occystrap, shakenfist,
    client-python-k3s) run without the `vm` label;
  - shakenfist's `release.yml` sets workflow-level `contents: read`
    rather than `{}`.
* **Re-publishing the affected packages** without their tests is not
  worth a version bump on its own. The next ordinary release of each
  carries the fix.
* **A failed lane should eject a queue entry quickly.** "Can merge"
  runs under `always()` and waits for every need, so one failed lane
  holds the entry until the slowest lane gets a runner, or until the
  check timeout. A fail-fast aggregator, or per-lane timeouts, would
  free the queue sooner. It matters most for shakenfist, whose lanes
  share the under-cloud.
* **Merges around the queue are routine.** Phase 3's gate checks
  found six develop tips merged directly: daily `export-repo-config`
  pull requests, a hand-dequeued Renovate update, and a kerbside
  update that `sfconductor` merged after its queue run failed. None of
  those can be released. Either the `export-repo-config` and Renovate
  pull requests go through the queue, or the gate learns to cross a
  merge commit that changed no gated file, as hunkydory's review marks
  need too (above). The conductor merging past a failed queue run is
  `PLAN-bot-bypass-identity.md`'s to close.
* **occystrap's merge queue has never run** (occystrap#169). Phase 4
  found no cause on our side, so the queue is disabled and occystrap
  gates releases on push runs (decision 4.7). Turning it back on is a
  ruleset change, with the settings in occystrap's `RELEASE-SETUP.md`,
  once GitHub support or a later attempt shows the queue builds
  groups. Until then `release-merge-queue` fails occystrap.
* **Scalar `on:` triggers in the CI criteria.** `release-process`
  reads `on: push` and `on: merge_group` written as a bare scalar,
  through `scalar_trigger_re()` in `audit/text/workflows.py`.
  `merge-group-cancellation` (for `merge_group` and `workflow_call`),
  `fuzz-nightly-reporting` (for `merge_group`) and
  `expensive-lane-path-filter` (for `pull_request`) still read only
  the block and flow forms, so a workflow spelled the scalar way reads
  to them as not triggered. No workflow in the fleet uses that
  spelling. This predates the plan.
* **CodeQL on merge groups.** `codeql-analysis.yml` runs on push
  only. It only needs `merge_group` if it becomes a required check.
* **The gate cannot tell a queue run that tested the code from one
  that skipped it.** A docs-only queue entry on top of a bypassed code
  merge succeeds without running the code tests, and the gate accepts
  it (phase 5 finding D4). Closing it needs either no bypass, which is
  `PLAN-bot-bypass-identity.md`'s, or a gate that refuses a run whose
  code lanes were skipped.
* **`merge-group-cancellation` does not require the first-attempt
  term** in a base-branch concurrency key, which phase 3 added fleet
  wide so that a re-run cannot cancel a live entry. All eleven
  consumers carry it today, and nothing tests it in `actions`'
  `smoke-cluster.yml` (phase 5).
* **The Rust template's shell scripts are never run by a test.**
  `release-version.sh` is; `propose-release.sh`, `tag-release.sh` and
  `sign-release-tag.sh` are only compared with their copies (phase 5).

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

* **Unprotected `release` environments** in shakenfist (no rules and
  no deployment policy, with a secret in it), clingwrap (no
  environment) and client-python-ova (no environment). These were
  repaired by hand on 2026-10-18, before this plan was written, to
  match client-python's: `mikalstill` as required reviewer and a
  single `tag: v*` deployment policy. Each was read back afterwards.
  Phase 1's criterion is what keeps them repaired.
* Defects in this plan's own landings that later merges fixed are
  listed under *Fixed since* in the Phase 5 results.
* Related issues: client-python-ova#19 is the prompt, and its phase 3
  pull request closes it. client-python-k3s's `tools/check-dist.sh`
  is the prior art for `check-dist`.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
