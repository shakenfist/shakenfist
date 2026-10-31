# Bot bypass identity

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

[#211](https://github.com/shakenfist/development/issues/211) asks
for `DEPENDENCIES_TOKEN` to be replaced, because
`PLAN-workflow-ref-trust.md`'s step 2a would make `shakenfist-bot`
a bypass actor on `shakenfist/actions` main. It frames the problem
as one that step would create. Read through the GitHub API on
2026-10-07, it already exists in six repositories, and is wider
than the issue says in four ways. Everything below was re-read
on 2026-10-14 (Australian Eastern Daylight Time, UTC+11, which is
the evening of 2026-10-13 in UTC), before the plan went to review,
and the figures are from that day. The rulesets, the bypass team's
membership and the bot's role were read again later on 2026-10-14,
while the review was being answered, and had not changed. The
rulesets, the team, the bot's role and permissions, the secrets
each repository holds and the bypass history were read again on
2026-10-29, when phase 1 was planned; the tables below carry that
reading, and phase 1's survey records what had changed.

**The bypass is already in place.** Seven default-branch rulesets
require a pull request, status checks and the merge queue, and
every one of them names the same bypass actor, the team
`sf-can-skip-merge-queue` (id 11722172), with `bypass_mode:
always`. The 2026-10-14 reading found six: andris, whose ruleset
dates from 2026-10-08, was missed then and first read on 2026-10-29
(its bot permission checked 2026-10-30), and hunkydory's merge
queue arrived between the readings.

| Repository | Rules on the default branch | Bypass |
|------------|-----------------------------|--------|
| shakenfist | pull request, status checks, merge queue | `sf-can-skip-merge-queue` |
| kerbside | pull request, status checks, merge queue | `sf-can-skip-merge-queue` |
| ryll | pull request, status checks, merge queue | `sf-can-skip-merge-queue` |
| instar | pull request, status checks, merge queue | `sf-can-skip-merge-queue` |
| hunkydory | pull request, status checks, merge queue | `sf-can-skip-merge-queue` |
| client-python-k3s | pull request, status checks, merge queue | `sf-can-skip-merge-queue` |
| andris | pull request, status checks, merge queue | `sf-can-skip-merge-queue` |

The team has three members: `mikalstill`, `shakenfist-bot` and
`sfconductor`. A ruleset bypass belongs to an identity, not to a
token, so **every credential that authenticates as
`shakenfist-bot` can push straight to those seven default branches**,
skipping review, required checks and the merge queue.

**The bot is also an organisation owner** (as the plan found it;
phase 1 step 1b demoted it on 2026-10-30, since when it is a member
and, through `sf-security-managers`, an organisation security
manager; the rest of this paragraph is the state before that).
`orgs/shakenfist/memberships/shakenfist-bot` reported `role: admin`,
and `repos/shakenfist/<r>/collaborators/shakenfist-bot/permission`
reports `admin` in all eleven of shakenfist, kerbside, ryll,
instar, hunkydory, client-python-k3s, actions, development, sfui,
kerbside-client and andris. (`sfconductor` is an ordinary member.) No ruleset
names `OrganizationAdmin` or the admin repository role as a bypass
actor today, so ownership does not bypass anything by itself. But a
repository admin can rewrite the ruleset, and can widen an
environment's deployment branch policy. So a bot credential with
enough scope can add a bypass back, delete the ruleset, or open the
environment this plan puts the App's key in, whatever team the bot
is in. Taking the bot out of the team is necessary and
not sufficient (open question 5).

**More than one credential is the bot.** `DEPENDENCIES_TOKEN` is
not the only one. `RENOVATE_TOKEN` is a separate secret, but every
Renovate pull request in shakenfist, kerbside and ryll (the last
14 in each) is authored by `shakenfist-bot`, so it is the
bot's credential too. Live workflows on default branches that put
one of the two in scope:

| Secret | Workflows | Triggers |
|--------|-----------|----------|
| `RENOVATE_TOKEN` | `renovate.yml` in 21 repositories (17 on 2026-10-14), including all seven above | `schedule`, `workflow_dispatch` |
| `DEPENDENCIES_TOKEN` | `prune-reviews.yml` in andris, hunkydory, kerbside, ryll (in actions, development, kerbside-client and sfui it resolves to nothing, so the push uses `github.token`) | `push`, `schedule`, `workflow_dispatch` |
| `DEPENDENCIES_TOKEN` | `pin-indirect-dependencies.yml` in shakenfist, kerbside | `pull_request`, `schedule`, `workflow_dispatch` |
| `DEPENDENCIES_TOKEN` | `code-formatting.yml` in shakenfist, client-python | `pull_request`, `schedule`, `workflow_dispatch` |
| `DEPENDENCIES_TOKEN` | `functional-tests.yml` (claude-code delinter job) in shakenfist | `pull_request`, `merge_group` |
| `DEPENDENCIES_TOKEN` | `sync-external-docs.yml` in shakenfist | `schedule`, `workflow_dispatch` |

None runs on `pull_request_target`, `issue_comment` or
`workflow_run`, so nobody outside the organisation can fire one.
The exposure is to whoever and whatever can run code beside the
secret. That includes every Renovate run, which executes
`renovatebot/github-action` (referenced by tag, not sha, until
`PLAN-workflow-ref-trust.md` phase 3) and whatever package-manager
code a lockfile update runs. It also includes the pull-request jobs
above, which the templates already say are guarded against forks
and against accident, but not against a writer
(`templates/pin-indirect-dependencies/pin-indirect-dependencies.yml:58-65`).

**Where the secrets live is not visible to this repository.**
`actions` has no repository secrets at all, and its
`prune-reviews` commits are authored by `shakenfist-bot`, but that
is `ci-prune-reviews.sh` setting `git config user.name`, not the
token: its ruleset rule suites show those pushes made by
`github-actions[bot]`, as are sfui's and kerbside-client's, while
ryll's are made by `shakenfist-bot` (checked 2026-10-29). Checked
with `repos/<r>/actions/secrets` and
`repos/<r>/actions/organization-secrets` the same day:
`DEPENDENCIES_TOKEN` is a repository secret in shakenfist,
client-python, hunkydory, kerbside and ryll, and absent from
actions, development, kerbside-client and sfui. andris lacked it
until 2026-10-29 18:39 UTC, so its `prune-reviews` pushes, made by
`github-actions[bot]`, were rejected by its ruleset (six times that
month); since it was added they land as `shakenfist-bot`. The only
organisation secrets those ten can see are `RENOVATE_TOKEN`, and
`GITLAB_TESTDATA_TOKEN` in shakenfist. Their visibility beyond that
needs `admin:org` to read.

**Only one job needs the bypass.** The ruleset rule-suite history
for the month to 2026-10-29 shows what the bypass is actually used
for (the month to 2026-10-14 told the same story with smaller
numbers):

| Repository | `shakenfist-bot` | `sfconductor` | `mikalstill` |
|------------|------------------|---------------|--------------|
| shakenfist | 0 | 52 | 5 |
| kerbside | 37 | 7 | 3 |
| ryll | 76 | 1 | 6 |
| hunkydory | 28 | 0 | 5 |
| instar | 0 | 1 | 2 |
| client-python-k3s | 0 | 2 | 3 |
| andris | 0 | 0 | 5 |

Every `shakenfist-bot` bypass is a `prune-reviews` push of review
marks to `develop`: each one's commit subject is "Prune stale
review marks." or, since `PLAN-review-import.md` added the import
step, "Prune and import review marks.". The bot's other writers do
not need one:
Renovate, `pin-indirect-dependencies`, `code-formatting`, the
delinter and `sync-external-docs` (through
`actions/tools/ci_sync_docs.sh:59-65`) all push a branch and open a
pull request. No workflow in the fleet merges a pull request as the
bot. `sfconductor`'s bypasses are pull request merges that skip the
queue (for example shakenfist#4467, a Renovate bump); it is a
separate identity, outside this plan except as open question 3.

**Nothing in the fleet mints a GitHub App token today.** There is
no `actions/create-github-app-token` reference in any workflow or
template.

**How `prune-reviews` uses the token today.** The template and this
repository's deployed copy are byte-identical, and all eight live
copies still name `DEPENDENCIES_TOKEN`. Its one job,
`prune-and-import`, runs on `[self-hosted, static]` and has no
`actions/checkout`. Since #254 its first step, `Fetch the prune
script`, has no token. It clones the default branch into
`RUNNER_TEMP`, ignoring the runner's git configuration. The second
step, `Prune and import review marks`, runs
`prune-script/tools/ci-prune-reviews.sh` from that clone, and hands
the push credential to it as the `PUSH_TOKEN` environment variable
(`secrets.DEPENDENCIES_TOKEN || github.token`). The script gives it
to its one `git push`. `scripts/tests/test_ci_prune_reviews.py`
holds the workflow to that shape (see step 2a). The template
README's "Runner isolation" section says why: the static runners
are long-lived, share one home directory across jobs and
repositories, and run pull-request code. The commit's author and committer are
set by the script (`git config user.name 'shakenfist-bot'`), not by
the credential, so the commit metadata does not say which identity
pushed it; the rule-suite history's `actor_name` does.

## Mission and problem statement

Make the identity that can bypass a default-branch ruleset one that
only reviewed default-branch code can use, and take that power away
from every credential the bot carries elsewhere. Concretely:

1. A GitHub App, with `contents: write` and nothing else, becomes
   the only automation bypass actor on the rulesets whose
   repositories run `prune-reviews`, and on
   `actions` when `PLAN-workflow-ref-trust.md` step 2a adds one.
2. Its private key is an **environment** secret, in an environment
   whose deployment branch policy admits only the default branch.
   A job on any other ref, including a pull request that edits the
   workflow, cannot read it. That is the property a repository or
   organisation secret cannot have. The branch policy decides which
   job is given the key, not what else has run on the machine that
   job runs on, so the job also moves off the shared static runners
   (open question 6).
3. `prune-reviews` mints a short-lived installation token from it,
   and the template, its README and every adopted copy change to
   match.
4. `shakenfist-bot` leaves `sf-can-skip-merge-queue`, and stops
   being an organisation owner (open question 5). Its PATs keep
   working for what they actually do, pushing branches and opening
   pull requests that retrigger CI. They can no longer reach a
   protected branch directly, rewrite the ruleset that protects it,
   or widen the environment that holds the App's key.

This closes #211 and lets `PLAN-workflow-ref-trust.md` step 2a name
the App as its bypass actor rather than a team of one.

Out of scope:

- Replacing `DEPENDENCIES_TOKEN` and `RENOVATE_TOKEN` with
  fine-grained or App tokens. Once neither can bypass, what they can
  do is push branches and open pull requests, which still need
  review and CI to land. Narrowing them is worth doing and is listed
  in Future work.
- `sfconductor` and `mikalstill` as bypass actors (open question
  3).
- Moving `prune-reviews` to open pull requests instead of pushing.
  `PLAN-review-import.md` keeps the push, and this plan does too.
- Pinning `renovatebot/github-action` to a sha.
  `PLAN-workflow-ref-trust.md` phase 3 owns pinning. The one
  exception is the new `actions/create-github-app-token` reference.
  It handles the bypass identity's private key, so step 2a pins it
  to a sha with a `# vX.Y.Z` comment, in the form that criterion
  will accept, rather than waiting for phase 3.

## Open questions

1. **One App for the fleet, or one per repository?** One App
   installed on selected repositories, with the key duplicated into
   each repository's environment, is one thing to rotate and one
   bypass actor id for every ruleset and for the
   `moving-ref-source-protection` criterion to compare against. Per
   repository Apps contain a leaked key to one repository but
   multiply everything else by seven. *Default:* one App, installed
   only on the repositories whose `prune-reviews` must bypass a
   ruleset: today andris, hunkydory, kerbside and ryll, since
   shakenfist, instar and client-python-k3s carry no
   `prune-reviews`, plus `actions` once step 2a lands and any of
   the three that adopts
   review tracking later. It is
   named for what it does rather than for the bot, for example
   `shakenfist-review-marks`.
2. **What do repositories without a pull-request ruleset use?**
   development, sfui and kerbside-client push review marks today with
   `DEPENDENCIES_TOKEN || github.token`, and `actions` does until
   step 2a; in the month to 2026-10-29 all four in fact pushed with
   `github.token` (phase 1, survey item 2). A repository with no ruleset to bypass gains nothing from
   the App, and `github.token` with `contents: write` already pushes
   there. *Default:* they move to `github.token`, which needs no
   secret. They still get an empty `review-marks` environment with
   the default-branch policy (open question 4). The template mints the App token only
   when the environment's secret is present, and `PUSH_TOKEN` falls
   back to `github.token` otherwise, so one file still serves both
   kinds (as the `DEPENDENCIES_TOKEN` bullet in
   `templates/review-tracking/README.md` promises today). Note that
   `github.token` pushes do not start workflows, where PAT pushes
   did (an App installation token's pushes do): confirm nothing in
   those four repositories depended on the retrigger. The token
   comment above the job's `permissions:` block says the retrigger
   finds nothing to do, so this should be a no-op.
3. **What about `sfconductor`?** It is a user account in the same
   team, with more bypasses than the bot, and this repository does
   not know where its credential lives. *Default:* out of scope.
   Phase 1 records what it is and where its token is held, and files
   an issue if the same reasoning applies, rather than changing its
   access here.
4. **Does a missing environment fail closed?** GitHub creates an
   environment on first reference if it does not exist, with no
   branch policy. A repository adopted later, whose operator forgot
   to create the environment first, would run the job in a new,
   unprotected environment holding no secret, mint nothing, and fall
   back to `github.token`, whose push the ruleset then refuses.
   *Default:* that is failing closed, and acceptable. But an empty,
   unprotected environment is a trap for whoever later adds the key
   to it. So every repository that runs `prune-reviews` gets a
   `review-marks` environment with the default-branch policy before
   its first run of the new template, whether or not it will hold
   the key (steps 1b and 2b). Then "the environment exists" implies
   "it admits only the default branch" everywhere. The adoption skill
   and the template README say so. They also say the key is only
   ever an environment secret, never a repository or organisation
   secret: `secrets.REVIEW_MARKS_APP_KEY` resolves at all three
   levels, and the mint-when-present guard would work just as
   quietly with a key that any branch can read. Step 3c reports
   both an environment without the policy and the key held at
   repository level.

   **The default-branch policy**, wherever this plan uses the
   phrase, means exactly this, and nothing looser.
   `repos/{org}/{repo}/environments/review-marks` has
   `deployment_branch_policy` equal to
   `{"protected_branches": false, "custom_branch_policies": true}`.
   `.../deployment-branch-policies` holds exactly one entry, with
   `type: branch` and `name` equal to the default branch, containing
   no glob characters. "Admits the default branch" is not the test.
   A `protected_branches: true` policy admits any protected branch,
   a branch name is a glob, and a policy can be `type: tag`, so a
   set such as {`develop`, tag `*`} admits the default branch and
   also lets any writer push a tag at a commit with an edited
   workflow and read the key.
5. **Does `shakenfist-bot` stay an organisation owner?** It is one
   today (Situation), which makes it an admin of every repository,
   able to edit any ruleset and any environment's deployment branch
   policy. The second matters as much as the first. A bot credential
   that widens the `review-marks` policy lets a branch workflow read
   the App's key, which mints bypass tokens for every repository the
   App is installed on. `DEPENDENCIES_TOKEN` is reachable from
   pull-request jobs and Renovate runs. None of its known jobs needs that:
   Renovate, `pin-indirect-dependencies`, `code-formatting`, the
   delinter, `sync-external-docs` and, after phase 2, nothing that
   pushes to a protected branch only need to push branches and open
   pull requests. `AUDIT_TOKEN` is the bot's too (it authors the
   audit's issues; phase 1, survey item 3); it only files issues,
   since `update-docs` pushes the compliance page with the job's
   own `GITHUB_TOKEN` (`docs/consistency-audits.md`). Reading
   `bypass_actors` may need a caller that can edit the ruleset, and
   reading `security_and_analysis` does need admin access
   (`docs/consistency-audits.md`); both are `AUDIT_READ_TOKEN`'s,
   whose account phase 1 measures, and the second is what the
   demotion broke until the bot became a security manager.
   *Default:* demote it to an organisation member with write, not
   admin, on the repositories its credentials push to. Step 1a
   records whose account each of `DEPENDENCIES_TOKEN`,
   `RENOVATE_TOKEN` and `AUDIT_TOKEN` is, and its scopes. If any of
   them is the bot's and needs admin, the demotion waits until that
   credential moves to an identity of its own. That wait is a hard
   gate, not something to record and move past. Step 1b demotes the
   bot before it adds the key anywhere. No repository's environment
   is given the key while any automation identity is an admin there,
   because Mission item 2 would be false in that repository from the
   moment the key arrived.
6. **Which runner holds the key?** `prune-and-import` runs on the
   static pool, which runs pull-request code from every repository
   and shares one home directory across jobs (template README,
   "Runner isolation"). The environment's branch policy decides which
   ref's job is given the key. It does not decide what else has run
   on the machine: a process an earlier pull-request job left behind
   can read the key from the token step's environment, or the minted
   token from the script's. The PAT has the same exposure today, so
   this is not a new hole, but Mission item 2's claim is stronger
   than a static runner delivers. *Default:* move `prune-and-import`
   to the fleet's ephemeral vm runners, as `pin-indirect-dependencies`
   uses (`[self-hosted, vm, debian-13, m]`), in the one template.
   Step 1a confirms those labels serve all eight repositories that
   run `prune-reviews`. If they do not, or the boot time makes the
   job impractical on every push, keep `static` and say so instead,
   in Mission, the template comment and the criterion spec's
   reviewer-confirmed half: anyone who can run a pull-request job on
   the static pool can reach the key.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Operator inventory and App creation | In progress | |
| 2. `prune-reviews` mints an App token | Not started | |
| 3. Move the bypass and enforce it | Not started | |
| 4. Push audit | Not started | |

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

### Phase 1: operator inventory and App creation

Planning effort: medium. Review effort: high, because it changes
organisation settings that no test in this repository covers.
Planned on 2026-10-29 against `d2044c3`, on the branch
`bot-bypass-identity-phase-01-app-creation`.

This phase is done by the management session with the operator.
It needs `admin:org`, which neither the audit token nor a sub-agent
has (the operator's `gh` token carried `gist`, `read:org`, `repo`
and `workflow` on the day of planning), and nothing in it lands as
code here except what it records in this section. The phase's pull
request is therefore this section: the plan, and then the record
under *Record* below, filled in as each step is done.

#### Scope

In: the credential inventory, settling open questions 3, 5 and 6
from it; demoting `shakenfist-bot`; creating the App, the eight
`review-marks` environments, the four key secrets and the one
client-id variable; adding the App as a bypass actor beside the
team on the four rulesets whose `prune-reviews` must bypass them;
reopening #211.

Out: any change to a workflow, a template or a check (phases 2 and
3); removing the bot from the team (step 3a); anything about
`sfconductor` beyond recording what it is; narrowing the
`shakenfist-k3s-ci-arc` App (survey item 5, Future work).

#### What the survey found

Read through the GitHub API on 2026-10-29 (Australian Eastern
Daylight Time) with the operator's token, and against the tree at
`d2044c3`. Everything Situation says about the team, the bot's role
and its ten repository permissions is unchanged. Seven things are
not as the plan assumed.

1. **andris is an eighth adopter and a seventh ruleset, and its
   review marks are not landing.** `shakenfist/andris` was created
   on 2026-10-08, the day after Situation was first read, and its
   ruleset at 17:15 AEDT that day, so the 2026-10-14 re-read missed
   it and there were already seven then. It carries a
   `prune-reviews.yml` byte-identical to the template, a `Develop
   branch` ruleset (id 24700229) requiring a pull request, the merge
   queue and status checks with the same team bypass, no repository
   secrets, and no `review-marks` environment. Its last two
   `prune-reviews` runs on `develop`, on 2026-10-28 (runs
   40650167261 and 40748674829), failed: the push, as
   `github-actions[bot]`, was refused with "Changes must be made
   through a pull request" (rule suites 4764862516 and 4777473307).
   `templates/review-tracking/README.md:198` already lists it with
   `DEPENDENCIES_TOKEN`, but no secret of that name reaches it. The
   plan's seven repositories are eight, its three key holders are
   four, and andris is the natural first rollout in phase 2 because
   nothing there can regress. *Overtaken the same day:* #343 records
   that andris was given `DEPENDENCIES_TOKEN` as a repository secret
   at 2026-10-29 18:39 UTC, after which its pushes land as the bot,
   so andris now works the way hunkydory, kerbside and ryll do and
   the four key holders stand on the same footing.
2. **`DEPENDENCIES_TOKEN` no longer reaches the repositories without
   a pull-request ruleset.** Situation inferred an organisation
   secret from `actions` pushing as the bot 44 times in a quarter.
   In the month to 2026-10-29, every `prune-reviews` push to the
   default branch of actions (40), development (37), sfui (4) and
   kerbside-client (2) was by `github-actions[bot]`, which is
   `github.token`, and andris's two failed the same way. Only
   hunkydory (28), kerbside (37) and ryll (76) pushed as
   `shakenfist-bot`, and each of those holds `DEPENDENCIES_TOKEN` as
   a repository secret (so do shakenfist and client-python). So the
   organisation secret has gone, or its visibility has narrowed,
   since 2026-10-07; step 1a records which. Open question 2's
   default is already the live state in four repositories, and the
   retrigger question it raises is answered: those four have run on
   `github.token` for at least a month without anybody noticing.
3. **`AUDIT_TOKEN` is the bot's, and cannot be probed from a
   branch.** The audit's issues are authored by `shakenfist-bot`
   (#247, filed by `manage-issues`), so `AUDIT_TOKEN` is a bot
   credential. It is an environment secret of `audit-write` on this
   repository, whose deployment branch policy is exactly open
   question 4's default-branch policy for `main` (one policy, id
   64166004, `type: branch`, `name: main`). A throwaway workflow on a
   branch cannot read it, so the old 1a brief's "all three secrets
   are in scope here" was wrong for that one. `AUDIT_READ_TOKEN`, a
   repository secret here and a fine-grained read-only token
   (`docs/consistency-audits.md:107-120`), is the identity every
   audit leg reads with, and is what step 3c's `bypass_actors`
   question is really about; whose account minted it is recorded
   nowhere, and the probe settles it. `AUDIT_TOKEN` is documented
   on the same page as a fine-grained token with the organisation
   as resource owner, all repositories, and Issues read and write
   only; `update-docs` uses it to look up issues and pushes the
   page with the job's own `GITHUB_TOKEN`. *Corrected after
   review (2026-10-31):* this item first said neither audit job
   needs admin. The same page says `AUDIT_READ_TOKEN` needs
   Administration read for `security_and_analysis` and that a
   fine-grained token's reach follows its account's access, so the
   audit's dependence on the bot's owner role was knowable before
   the demotion; item 4 and decision 3 missed it, and the soak
   found it (execution notes, 06:30).
4. **The organisation's base permission is `write`.**
   `orgs/shakenfist` reports `default_repository_permission: write`,
   so an ordinary member can push branches, open pull requests and
   file issues in every repository. Demoting the bot from owner to
   member costs none of its credentials anything they are known to
   do, and no per-repository grant is needed. (*Wrong for
   `AUDIT_READ_TOKEN`*, see item 3.) The organisation has
   two owners, `mikalstill` and `shakenfist-bot`; `sfconductor` is a
   member; the two teams then, `sf-can-skip-merge-queue` and
   `sf-team`, have the same three members, and neither grants a
   repository permission (`sf-can-skip-merge-queue` has no
   repositories). A third, `sf-security-managers`, was added on
   2026-10-30 (execution notes, 06:30).
5. **A GitHub App with `administration: write` has been installed
   since the plan was written.** `orgs/shakenfist/installations`
   lists one installation: `shakenfist-k3s-ci-arc` (app id 5336982,
   installation 171876156, created 2026-10-16) with
   `administration: write` and `metadata: read` on selected
   repositories. Situation's "nothing in the fleet mints a GitHub
   App token" still holds for workflows (no
   `create-github-app-token` in any of the 30 repositories'
   workflows), but this App is an automation identity with
   repository-admin reach wherever it is installed, because
   `administration: write` is the permission that edits rulesets and
   environments. It is the Actions Runner Controller's registration
   identity for the k3s runners, and holds its key outside GitHub.
   Which repositories it is installed on needs an owner to read.
   Step 1a records them, and 1b's gate (no key where an automation
   identity is an admin) applies to it exactly as to the bot
   (decision 4).
6. **#211 is already closed.** It was closed on 2026-10-14 by the
   merge of #255, the pull request that added this plan, whose body
   referenced it with a closing keyword. Nothing it asks for has
   been done. Step 1a reopens it, so that phase 3's "closed from the
   pull request that lands 3c" is a real event (decision 5).
7. **Smaller corrections.** hunkydory's ruleset now requires the
   merge queue, so all seven pull-request rulesets do. The bypass
   history for the month to 2026-10-29 is in the corrected Situation
   table; every one of the bot's 141 bypasses is a "Prune and import
   review marks." push, so phase 3 still breaks nothing else. The
   ephemeral vm pool serves 20 of the 30 repositories, including all
   eight adopters, so open question 6's labels exist; the audit's own
   key-holding jobs use `[self-hosted, vm, debian-13, s]`, and that
   image has no `gh` (`docs/consistency-audits.md:206-209`), which
   phase 2 must allow for (the prune job needs git, python3 and the
   gitsign it downloads itself). `actions/create-github-app-token`
   v3.3.0 (2026-10-12) is commit
   `64ab729a6294000f5aa4a4bc0b73d1f32bc45510`.
   `docs/audits/merge-queue-config.md` has the "Template" section
   with the GET-then-PUT shape 1c cites. `sfconductor` is named in no
   document in this repository.

Corrected at source in the planning commit: the Situation table of
rulesets and the bypass-history table (re-read, andris added,
hunkydory's merge queue), the organisation-secret inference, the
paragraph on `AUDIT_TOKEN` in open question 5, open question 1's
default, the repository lists and counts in phases 2 and 3, phase
3's definition of done, the Related issues entry for #211, and the
`docs/plans/index.md` row.

#### Decisions

1. **andris joins the key-holding set.** Eight repositories run
   `prune-reviews`; four of them (andris, hunkydory, kerbside, ryll)
   have a pull-request ruleset and get the key, the App installation
   and the bypass actor; the other four (actions, development, sfui,
   kerbside-client) get the environment only. Phase 2's rollout
   starts with andris: it was failing when this was planned and is
   the smallest adopter, so it proves the App path before the three
   larger repositories change (its pushes have landed as the bot
   since 2026-10-29 18:39 UTC, per #343, so the order is now about
   size rather than regression).
2. **The probe covers the three secrets a branch can read, and the
   bot's own token list covers the rest.** `DEPENDENCIES_TOKEN`,
   `RENOVATE_TOKEN` and `AUDIT_READ_TOKEN` are probed from a
   throwaway workflow on a branch of this repository.
   `AUDIT_TOKEN`'s account is settled by authorship (survey item 3)
   and its type and scopes come from the bot's settings page, which
   lists every token the account has issued, with type, scopes and
   expiry. That list is the complete inventory the plan previously
   had no way to get. The probe prints, per secret, the login, the
   `X-OAuth-Scopes` header and the
   `github-authentication-token-expiration` header, and never the
   token; scopes plus expiry match a secret to a row in the list.
3. **The demotion is not held by `AUDIT_TOKEN`.** Open question 5
   waits only for a bot credential that needs admin. With base
   permission `write` (survey item 4), nothing the bot's credentials
   are known to do needs it, and the bot's token list shows whether
   there is a credential nobody knows about. A row no secret
   accounts for is recorded as unknown reach, but it does not hold
   the demotion either: an owner's token is only as powerful as the
   owner, and demotion narrows every one of them at once. What the
   demotion may cost is `AUDIT_READ_TOKEN`'s view of
   `bypass_actors`, if that token is the bot's; the record says
   which, and step 3c takes its fallback shape if so. This is the
   decision most likely to be argued with: the plan's wording reads
   as "any bot credential that is an admin today holds the gate",
   and this reads it as "any bot credential that needs admin". The
   second is what Mission item 2 needs, and the first would hold the
   gate forever, since every token of an owner is an admin today.
   *After the soak (2026-10-30):* `AUDIT_READ_TOKEN` did need the
   owner's reach, as `docs/consistency-audits.md` already said; the
   security manager role now stands in for it (execution notes).
4. **The runner-controller App is held to the same gate as the
   bot.** `shakenfist-k3s-ci-arc` has `administration: write`, which
   edits rulesets and environments. If 1a finds it installed on any
   of the four key-holding repositories, 1b stops before adding the
   key there, and the way out is uninstalling it from that
   repository: it needs admin only to register runners, and
   client-python-k3s is where the runners are. Narrowing its
   permissions is Future work, not this phase.
5. **#211 is reopened in 1a**, with a comment saying #255 closed it
   on merge and the work is this plan. Phase 3 closes it from the
   pull request that lands 3c, as written.
6. **Open question 6 is settled as the vm pool, size `s`.** The
   labels serve every adopter (survey item 7), and the audit's own
   jobs that hold `AUDIT_TOKEN` already use `[self-hosted, vm,
   debian-13, s]` for the same reason. 1a confirms, with
   `admin:org`, that the runner group behind `vm` is not restricted
   to selected repositories. Phase 2 checks the image has what the
   prune job needs.
7. **Names are as the plan proposed.** The App is
   `shakenfist-review-marks`, the environment `review-marks`, the
   secret `REVIEW_MARKS_APP_KEY`, the variable
   `REVIEW_MARKS_APP_CLIENT_ID`. `contents: write` only, no webhook,
   no user authorisation, installed on the four.
8. **1b waits for proof before 1c.** The demotion lands first and
   alone, and 1b does not add a key until the next scheduled audit
   run, the next Renovate run and the next `prune-reviews` push in
   each of hunkydory, kerbside and ryll have succeeded as the bot
   under its new role. Record the wait with `plan-blockers` as a
   soak.

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Operator step, with the operator's own `gh` after `gh auth refresh -s admin:org`. Fill the *Record* tables below in this order. (1) Organisation secrets and variables: `gh secret list --org shakenfist`, and for each `selected` secret its repositories (`gh api orgs/shakenfist/actions/secrets/<name>/repositories --jq '.repositories[].name'`); `gh variable list --org shakenfist`; and `gh secret list --repo shakenfist/<r>` for all 30 non-archived repositories, listing which hold `DEPENDENCIES_TOKEN`, `RENOVATE_TOKEN` or any `REVIEW_MARKS_APP_KEY` (there must be none of the last, at either level). (2) The bot's token list: logged in as `shakenfist-bot`, read Settings, Developer settings, Personal access tokens (both tabs) and record each token's name, type (classic or fine-grained), scopes or permissions, and expiry; never a token value. (3) The probe: on a branch of this repository, a workflow with one job on `[self-hosted, vm, debian-13, s]`, never `static`, with a step per secret for `DEPENDENCIES_TOKEN`, `RENOVATE_TOKEN` and `AUDIT_READ_TOKEN` that runs (with `curl` and `python3`, since the vm image has no `gh`) the equivalent of `gh api user --jq .login` and `gh api -i user \| grep -iE '^(x-oauth-scopes\|github-authentication-token-expiration):'`, plus, for `AUDIT_READ_TOKEN` alone, `gh api repos/shakenfist/hunkydory/rulesets/23805020 --jq 'has("bypass_actors")'`. A missing `X-OAuth-Scopes` header means fine-grained or App, not scopeless: record the type and work out its reach from the token list instead. Run it on a `push` trigger for its own branch (`workflow_dispatch` refuses a workflow that is not on the default branch), copy the three results into the record, then delete the workflow run (`gh run delete`), the branch and the file, and confirm `gh run list --branch <branch>` is empty. `AUDIT_TOKEN` is not probed: it lives in the `audit-write` environment, which admits only `main`; its account is `shakenfist-bot` (it authored #247) and its row in the token list gives the rest. (4) `sfconductor`: record what it is, who operates it, where its credential is held and what it is for (open question 3), from the operator's knowledge; it appears in no document here. (5) The runner-controller App: `gh api orgs/shakenfist/installations` and, as an owner, the installation's repository list from the organisation's GitHub Apps settings page; record them. (6) Runner groups: `gh api orgs/shakenfist/actions/runner-groups --jq '.runner_groups[] \| {name, visibility, selected_repositories_url}'` and record whether the group serving the `vm` label is visible to all repositories. (7) Re-run the rule-suite query from Situation for all seven pull-request rulesets (`repos/shakenfist/<r>/rulesets/rule-suites?time_period=month&rule_suite_result=bypass&per_page=100`, paginated), and for every `shakenfist-bot` entry resolve `after_sha` to its commit subject; if anything other than a prune commit appears, stop, because phase 3 would break it. (8) `gh api orgs/shakenfist/rulesets` and whether any applies to a default branch (none existed on 2026-10-14, and the token could not read the endpoint on 2026-10-29). (9) Reopen #211 with a comment that #255 closed it on merge and the work is this plan. Then settle open questions 3, 5 and 6 in the record, in a sentence each, citing the rows. The `bypass_actors` boolean decides which shape step 3c takes, and `PLAN-workflow-ref-trust.md` step 2a cites this record rather than measuring again. |
| 1b | high | opus | none | Operator step, after 1a is recorded. First, the demotion, alone: `gh api -X PUT orgs/shakenfist/memberships/shakenfist-bot -f role=member`, then record `gh api orgs/shakenfist/memberships/shakenfist-bot --jq .role` and `repos/shakenfist/<r>/collaborators/shakenfist-bot/permission` for the eleven repositories in the record (the ten in Situation plus andris); each must read `write`, from the base permission, not `admin`. If any still reads `admin`, there is a direct collaborator grant or a team grant to remove, and the key goes nowhere until none remains. Then wait (decision 8): record the URLs of the next successful scheduled `consistency-audit` run here, of the next Renovate run that opened or updated a pull request as the bot, and of the next `prune-reviews` push in each of hunkydory, kerbside and ryll whose rule-suite `actor_name` is still `shakenfist-bot`. If `AUDIT_READ_TOKEN` was the bot's, re-run 1a's `bypass_actors` probe now, since 3c runs under this state, and record the answer. Second, the gate for the runner-controller App: if 1a recorded `shakenfist-k3s-ci-arc` installed on andris, hunkydory, kerbside or ryll, stop and put decision 4 to the operator; the key is not added to that repository while it is. Third, create the App under the organisation (Settings, Developer settings, GitHub Apps): name `shakenfist-review-marks`, repository permission `contents: write` only, no webhook, no user authorisation, and install it on andris, hunkydory, kerbside and ryll only. Generate one private key and keep the file out of every repository and scratchpad that outlives the step; `shred` it once the four secrets are set. Put the App's client id in an organisation variable `REVIEW_MARKS_APP_CLIENT_ID` (`gh variable set REVIEW_MARKS_APP_CLIENT_ID --org shakenfist --visibility all`), since a client id is not a secret. Fourth, in each of the eight repositories that run `prune-reviews` (andris, hunkydory, kerbside, ryll, actions, development, sfui, kerbside-client), create the environment with the default-branch policy exactly as open question 4 defines it: `gh api -X PUT repos/shakenfist/<r>/environments/review-marks --input -` with `{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}`, then `gh api -X POST repos/shakenfist/<r>/environments/review-marks/deployment-branch-policies -f name=<default branch> -f type=branch`, where the default branch is `develop` everywhere except actions and development (`main`). Record the GET of both endpoints for every repository. Fifth, in andris, hunkydory, kerbside and ryll alone, `gh secret set REVIEW_MARKS_APP_KEY --repo shakenfist/<r> --env review-marks < key.pem`; never `gh secret set` without `--env`, and never at organisation level. Record the App's id, client id and installation id, the four environments' secret listings (`repos/shakenfist/<r>/environments/review-marks/secrets`, names only) and `gh secret list --repo` for all eight showing no repository-level copy. |
| 1c | high | opus | none | Operator step. On the pull-request rulesets of andris (24700229), hunkydory (23805020), kerbside (20252051) and ryll (18708684), add the App as a bypass actor beside the existing team, preserving every other field, with the GET-then-PUT shape under "Template" in `docs/audits/merge-queue-config.md`: fetch the ruleset JSON, append `{"actor_id": <app id>, "actor_type": "Integration", "bypass_mode": "always"}` to `bypass_actors`, and PUT it back. Do andris first and confirm from the GET that the actor is there before the other three: GitHub is expected to refuse an App that is not installed on the repository as a bypass actor, so if andris refuses it the installation in 1b is wrong, and that is the thing to fix. This changes nothing on the day, because every live copy still pushes with `DEPENDENCIES_TOKEN` or `github.token`, but it must be in place before phase 2 merges a copy into any of the four. Add the App to no other ruleset: a repository that adopts review tracking later gets the bypass when it gets its environment and installation. Record the GET of each ruleset's `bypass_actors` afterwards. |

#### Record

Filled in by the operator as the steps run. Every cell cites the
command or page it came from and the date it was read; a cell that
could not be read says so rather than staying blank. Dates are
Australian Eastern Daylight Time (UTC+11).

**Credentials (1a).** Read on 2026-10-30 (the probe itself ran on
2026-10-29 at 12:50 AEDT) from the probe run
(development run 40802724777, deleted once read), from
`gh secret list` with `admin:org`, and from issue authorship.

| Secret | Level and visibility | Repositories holding it | Account | Type | Scopes or permissions | Expiry |
|--------|----------------------|-------------------------|---------|------|-----------------------|--------|
| `DEPENDENCIES_TOKEN` | repository only; no organisation secret of that name exists | client-python, hunkydory, kerbside, ryll, shakenfist, and andris since 2026-10-29 18:39 UTC (#343) | `shakenfist-bot` (rule-suite `actor_name` of every push made with it) | *pending: the bot's token list* | *pending* | *pending* |
| `RENOVATE_TOKEN` | organisation, selected: the 22 repositories in the next table | none at repository level | `shakenfist-bot` (probe) | classic PAT | `read:org`, `read:project`, `repo`, `workflow` | none: no expiration header |
| `AUDIT_READ_TOKEN` | repository, development | development | `shakenfist-bot` (probe) | fine-grained | read-only, as `docs/consistency-audits.md` lists | 2027-10-24 21:29:44 UTC |
| `AUDIT_TOKEN` | environment `audit-write`, development; policy `main` only | development | `shakenfist-bot` (authored #247) | fine-grained (`docs/consistency-audits.md`) | Issues read and write, all repositories, organisation as resource owner (documented), plus Contents read since 2026-10-31 (#349; confirmed against the token list: *pending*) | *pending* |

The probe could not read `DEPENDENCIES_TOKEN` at all ("secret not
set in this context"): no copy is visible to this repository, which
is also why its `prune-reviews` pushes with `github.token`.

`AUDIT_READ_TOKEN` sees `bypass_actors` on hunkydory's ruleset
23805020 before the demotion (1a, probe of 2026-10-29): **yes**, `has
bypass_actors: True`. After the demotion (1b, 2026-10-30, run
40936327542, deleted once read): **no**, `has bypass_actors:
False`, with `current_user_can_bypass: always` because the bot is
still in the team. That run predates the security manager grant
(06:40), so it was measured again under that role on 2026-10-31
(run 41130157778, deleted once read): `has bypass_actors: False`
still, and `repos/shakenfist/hunkydory/actions/secrets` answers
"Resource not accessible by personal access token". So step 3c
takes its fallback shape on a current measurement: the audit
measures only the environment half, and both the bypass list and
the repository-level key check move to the spec's
reviewer-confirmed half, as the plan provides.

**The bot's token list (1a).** *Pending: to be read as
`shakenfist-bot` by the operator.* Secrets elsewhere in the fleet
that may also be rows in it: kerbside-patches'
`DAILY_REBASE_TOKEN`, ryll's `HOMEBREW_TAP_TOKEN`, and the two
`GITLAB_TESTDATA_*` tokens, which are GitLab's and not GitHub's.

| Token name | Type | Scopes or permissions | Expiry | Matched secret |
|------------|------|-----------------------|--------|----------------|
| *pending* | | | | |

**Organisation secrets and variables (1a).** `gh secret list --org
shakenfist` and `gh variable list --org shakenfist`, 2026-10-30.
There are no organisation variables, and no secret named
`REVIEW_MARKS_APP_KEY` at organisation level or in any of the 30
non-archived repositories (`gh secret list --repo` for each; the
nine repositories with any secret are agent-python, client-python,
development, hunkydory, instar, kerbside, kerbside-patches, ryll
and shakenfist).

| Name | Kind | Visibility | Repositories (if selected) |
|------|------|------------|----------------------------|
| `GITLAB_TESTDATA_TOKEN` | secret | selected | client-python-ova, shakenfist |
| `RENOVATE_TOKEN` | secret | selected (22; kerbside-client holds it without a `renovate.yml`, hence Situation's 21) | actions, agent-python, andris, client-python, client-python-k3s, client-python-ova, clingwrap, cloudgood, development, divergulent, hunkydory, images, instar, kerbside, kerbside-client, kerbside-patches, library-utilities, occystrap, ryll, sfui, shakenfist, visual-digest-rust |

**Other identities (1a).**

- `sfconductor`: Mikal's own merge automation, a user account he
  operates with a credential held outside GitHub Actions (back
  brief, 2026-10-30; where it is held was not stated). Open question
  3 stays out of scope: the exposure this plan closes is a credential
  that branch code and third-party actions can read, and that one is
  not in a workflow.
- `shakenfist-k3s-ci-arc`: installed on hunkydory only (Mikal, from
  the organisation's GitHub Apps page, 2026-10-30; the installation
  endpoints refuse every token that is not a GitHub App's, so the
  API could not read it). hunkydory also carries a repository-level
  runner `k3s-static-hunkydory-runner-q57jl`. It is the k3s CI
  plan's credential (33fl `PLAN-k3s-ci-runners-phase-03-runners.md`,
  decision 4 and open question 1), which accepted Administration on
  hunkydory on 2026-10-15 as the minimum for a repository-scoped
  scale set, to be narrowed to "Self-hosted runners" at an org-level
  scale set in its phase 5. Gate for decision 4: **hit on hunkydory,
  and relaxed for that one repository** (execution notes,
  2026-10-30 18:15 AEDT).
- Runner group serving `vm`: one group, `Default`, `visibility: all`
  (`orgs/shakenfist/actions/runner-groups`, 2026-10-30). The runners
  registered at the time carried `self-hosted, Linux, X64, debian,
  debian-13, vm, s` and `..., vm, s, debian-13-docker`: the vm pool
  registers ephemeral runners per job.
- Organisation rulesets: none can exist. The endpoint answers
  "Upgrade to GitHub Team to enable this feature" (2026-10-30), so
  only repository rulesets apply to the default branches.
- Rule-suite bypasses by `shakenfist-bot` in the month to
  2026-10-29, all seven pull-request rulesets: 141 (kerbside 37,
  ryll 76, hunkydory 28, none elsewhere), every one with the commit
  subject "Prune and import review marks.".
- #211 reopened on 2026-10-29 12:48 AEDT with a comment naming #255.

**Open questions settled (1a).** 3: `sfconductor` is Mikal's merge
automation and stays out of scope; nothing is filed. 5: the
demotion proceeds. All four known bot credentials are the bot's.
Written before the soak, this said none needs admin;
`AUDIT_READ_TOKEN` did (survey item 3, corrected) and reads with
the security manager role since 06:40, `AUDIT_TOKEN` has lost
issue filing in private-ci (#349), and `update-docs` pushes with
the job's own `GITHUB_TOKEN`, so it never depended on the bot.
6: the ephemeral vm pool, `[self-hosted, vm, debian-13, s]`, which
the one runner group serves to every repository.

**Demotion (1b).** Done 2026-10-30 06:01 AEDT with
`gh api -X PUT orgs/shakenfist/memberships/shakenfist-bot -f
role=member`. Role after: `member` (re-read). Permissions after,
on shakenfist, kerbside, ryll, instar, hunkydory,
client-python-k3s, actions, development, sfui, kerbside-client
and andris: `write` on every one, from the base permission.
`orgs/shakenfist/members?role=admin` lists `mikalstill` alone.
Soak evidence: audit run 40936371270, dispatched on `main` at
2026-10-30 06:03 AEDT, **failed in every leg** at "Check the read
token": `AUDIT_READ_TOKEN cannot see repository settings
(security_and_analysis: false, deleteBranchOnMerge: true)`. The
API returns `security_and_analysis` only to a repository admin,
so the audit's read token does need admin, and decision 3's
premise was wrong for that one credential; open question 5's
gate applies to it. See the execution notes for what was done.
Renovate run: development run 40936378714, dispatched
2026-10-30 06:03 AEDT after the demotion, finished as the bot
("Repository finished"). Prune runs dispatched at the same time in
hunkydory (40936384188), kerbside (40936389176) and ryll
(40936394700) all succeeded with "No review marks to prune or
import", which proves the job and not the push. The push proof came
at 06:56 AEDT: hunkydory's rule-suite history shows a `bypass` push
by `shakenfist-bot` to `develop` (d0a36f2f, "Prune and import
review marks.", run 40944610032) after the demotion, so
`DEPENDENCIES_TOKEN` still pushes through the team bypass with the
bot a member. By 2026-10-31 07:58 AEDT the rule-suite histories
also showed post-demotion bot prune pushes in kerbside (rule suites
4803835127 at 09:03 and 4811205885 at 19:20 on 2026-10-30, commits
5ba1d191 and 00796bb9) and ryll (rule suite 4813612660 at 21:56,
commit 21577cd6), all `bypass` by `shakenfist-bot`, so all three
are proven. In fairness to the gate: the key was set at 19:16 on
2026-10-30, after hunkydory's push and kerbside's first but before
kerbside's second and ryll's only one, so decision 8's prune-push
leg was waived at 07:45 on hunkydory's evidence alone and the
other two confirmed it afterwards.

The audit dispatched again under the security manager role
(40943433199, 06:40 AEDT): all 25 legs passed their read-token
check and their audit, `update-docs` pushed the compliance page to
`main` (with the job's own `GITHUB_TOKEN`, so that proves the job
and not a bot token), and `manage-issues` filed 14 issues in
eight public repositories, but failed on private-ci: both of its
creations there got `GraphQL: Resource not accessible by personal
access token (repository.defaultBranchRef)`. `gh issue create`
reads the repository's default branch (for issue templates) before
creating, and in a private repository that needs contents read,
which `AUDIT_TOKEN` evidently had as an owner and does not have as
a member with `write` (it filed private-ci#219 on 2026-10-24). So
the soak passed for Renovate, the prune push and the audit legs,
and found one more thing the owner role was quietly
serving: `AUDIT_TOKEN`'s read of private repositories. The fix is
in the token (grant it contents read, as the bot, when its token
list is read) or in the script (create issues through the REST API,
which needs only issues write); it is tracked as #349, and either
way the `AUDIT_TOKEN` paragraph of `docs/consistency-audits.md`,
which says the token needs no Contents permission, changes with
it. The mechanism is inferred from the symptom (public repositories
fine, the private one refused on `defaultBranchRef`) and from
private-ci#219 having been filed as an owner; it is not measured.

**App (1b).** `shakenfist-review-marks`, created by Mikal in the
web UI on 2026-10-30 at 18:00 AEDT: app id 5538020, client id
`Iv23li7GsD5p4UvnqE4Y`, installation id 176803138, no webhook, no
events, permissions `contents: write` and `metadata: read` (the
latter is implicit), installed on selected repositories: andris,
hunkydory, kerbside and ryll (read with a one-off installation
token minted from the key, which listed exactly those four and
expired within the hour). `apps/shakenfist-review-marks` and
`orgs/shakenfist/installations` agree. The client id is the
organisation variable `REVIEW_MARKS_APP_CLIENT_ID`, visibility
`all`, set 2026-10-30. Private keys held by the App: *pending the
operator's confirmation from the App's page that the first key
(lost at 18:19, after minting one live installation token) is
deleted and only the second remains; no API lists an App's keys.*

**Environments (1b).** For each of andris, hunkydory, kerbside,
ryll, actions, development, sfui, kerbside-client: the
`environments/review-marks` response's `deployment_branch_policy`
and the `deployment-branch-policies` response, verbatim; for the
first four, the environment's secret names; for all eight,
`gh secret list --repo` showing no `REVIEW_MARKS_APP_KEY`.

Created 2026-10-30 18:05 AEDT with `PUT
repos/shakenfist/<r>/environments/review-marks` (body
`{"deployment_branch_policy": {"protected_branches": false,
"custom_branch_policies": true}}`) and `POST
.../deployment-branch-policies` (`name=<default>`, `type=branch`).
Read back the same minute; every row is the definition of done's
script printing `ok`:

| Repository | `deployment_branch_policy` | `deployment-branch-policies` |
|------------|----------------------------|------------------------------|
| andris | `{"protected_branches": false, "custom_branch_policies": true}` | one: id 64885235, `branch`, `develop` |
| hunkydory | same | one: id 64885238, `branch`, `develop` |
| kerbside | same | one: id 64885239, `branch`, `develop` |
| ryll | same | one: id 64885244, `branch`, `develop` |
| actions | same | one: id 64885247, `branch`, `main` |
| development | same | one: id 64885248, `branch`, `main` |
| sfui | same | one: id 64885251, `branch`, `develop` |
| kerbside-client | same | one: id 64885253, `branch`, `develop` |

Environment secrets, set 2026-10-30 19:16 AEDT with `gh secret set
REVIEW_MARKS_APP_KEY --repo shakenfist/<r> --env review-marks` from
the regenerated key and read back the same minute:
`environments/review-marks/secrets` lists exactly
`REVIEW_MARKS_APP_KEY` in andris, hunkydory, kerbside and ryll, and
nothing in actions, development, sfui and kerbside-client.
`gh secret list --repo` for the eight shows no
`REVIEW_MARKS_APP_KEY` (andris and hunkydory: `DEPENDENCIES_TOKEN`;
kerbside: `ANSIBLE_GALAXY_TOKEN`, `DEPENDENCIES_TOKEN`; ryll:
`CARGO_REGISTRY_TOKEN`, `DEPENDENCIES_TOKEN`, `HOMEBREW_TAP_TOKEN`;
development: `AUDIT_READ_TOKEN`; actions, sfui, kerbside-client:
none), and the organisation holds only `GITLAB_TESTDATA_TOKEN` and
`RENOVATE_TOKEN`. The key file was shredded after the read-back.

The environment half is still readable by the audit after the
demotion: the compliance page regenerated by run 40943433199
(`main` e135d57, 2026-10-30 07:20 AEDT) reports
`release-environment-protection` compliant in 14 repositories and
not applicable in 10, with no error, so `AUDIT_READ_TOKEN` reads
`environments/<name>` and `deployment-branch-policies` under the
security manager role, which is the half step 3c measures.

**Rulesets (1c).** Done 2026-10-31 07:55 to 07:58 AEDT, andris
first and read back before the others, by GET, append and PUT with
`name`, `target`, `enforcement`, `conditions` and `rules` carried
over unchanged (diffed before and after). GitHub accepted the App
as an actor on all four, each of which it is installed on; whether
it refuses an uninstalled App was not tested. `bypass_actors`
after the PUT, identical on andris (24700229), hunkydory
(23805020), kerbside (20252051) and ryll (18708684):

```json
[{"actor_id": 5538020, "actor_type": "Integration", "bypass_mode": "always"},
 {"actor_id": 11722172, "actor_type": "Team", "bypass_mode": "always"}]
```

#### Execution notes

- 2026-10-29 12:40 AEDT: back brief answered. Decisions 3, 4 and 7
  stand; step 1a was approved in full; this session may push the
  branch and open the pull request when the definition of done
  passes. The bot's token list is held for later.
- 2026-10-29 12:50 AEDT: the probe could not be started by `workflow_dispatch`,
  which GitHub refuses (404) for a workflow that is not on the
  default branch, so it ran on a `push` trigger for its own branch
  instead. It read its secrets with `curl` and `python3` rather than
  installing `gh`. Its run, the two skipped `prune-reviews` runs the
  branch pushes caused, the branch and the file were deleted
  afterwards; `gh run list --branch bbi-p1-token-probe` is empty.
- 2026-10-30 05:55 AEDT: the last scheduled audit run (40229216560, 2026-10-25)
  failed in `manage-issues` on a transient GraphQL error updating
  one issue, after every leg passed. Not this plan's; noted because
  decision 8's soak reads the next run.
- 2026-10-30 06:10 AEDT: the post-demotion re-probe ran the same way as the
  first, on a `push` trigger, with one step for `AUDIT_READ_TOKEN`,
  and was deleted with its branch and the skipped `prune-reviews`
  run the push caused.
- 2026-10-30 06:30 AEDT: the dispatched audit (40936371270) failed
  in every leg because `AUDIT_READ_TOKEN` no longer sees
  `security_and_analysis`, which the API returns only to a
  repository admin. Decision 3 was wrong for that credential.
  Rather than restore the owner role, Mikal chose to try the
  organisation security manager role: a team
  `sf-security-managers` (id 20418434, no repositories, base
  permission `pull`) holding the bot was made a security manager
  (`PUT orgs/shakenfist/security-managers/teams/
  sf-security-managers`). That role grants no ruleset, environment
  or branch right, so Mission item 2 holds, but it is wider than
  first described here: a security manager reads every repository,
  writes every security alert (code scanning, Dependabot and secret
  scanning, including reading leaked values and dismissing alerts)
  and can change code security settings such as secret scanning
  and push protection, at organisation and repository level. It is
  a role on the account, so every bot token whose scopes allow it
  (`RENOVATE_TOKEN`'s `repo`, `DEPENDENCIES_TOKEN`) carries that
  reach too: narrower than the owner role it replaced, wider than
  the audit needs. The exit is a read-only App for the audit legs,
  #352. The bot's role (`member`) and
  permissions (`write`) were re-read and unchanged. The audit was
  dispatched again (40943433199) as the test; its result is in
  the soak evidence. The read-only App for the audit stays Future
  work, and the fallback if the role does not suffice is the
  plan's own: restore the owner role and move the credential
  first.
- 2026-10-30 07:30 AEDT: the second audit run shows the security
  manager role is enough for the legs, and that `AUDIT_TOKEN`
  cannot create issues in private-ci any more (see the soak
  evidence). The next scheduled run, 2026-11-02 05:00 AEDT, will
  fail the same way unless the token or the script changes first.
- 2026-10-30 07:45 AEDT: Mikal ruled the soak passed on the
  Renovate, prune-push, audit-leg and docs-push evidence, and chose
  to fix the private-ci gap by editing `AUDIT_TOKEN` as the bot to
  add contents read when its token list is read, rather than
  changing the script or restoring the owner role. Whichever
  lands, `docs/consistency-audits.md` changes with it; #349 tracks
  the failure (filed 2026-10-31 after review). The App,
  environments and key proceed once the token list and the
  runner-controller App's repositories are recorded.
- 2026-10-30 18:15 AEDT: decision 4 relaxed for hunkydory. The
  runner-controller App already defeats hunkydory's ruleset outright
  (Administration can delete it), a cost the k3s plan accepted on
  2026-10-15; what the review-marks key adds is that whoever holds
  the ARC key (the cluster kubeconfig or the SOPS key on kasm) could
  widen hunkydory's `review-marks` policy, read this key from a
  branch workflow, and mint push tokens for andris, kerbside and
  ryll too. Holding hunkydory's key instead would stop phase 2
  finishing and phase 3 starting until k3s phase 5 moves all 19
  repositories. Mikal chose to key all four and record the residual
  here and in `bypass-actor-identity`'s reviewer-confirmed half
  (step 3c); 33fl's plan now says at phase 5 and open question 1
  that the narrowing closes it (33fl commit 51d5eda8).
- 2026-10-30 18:19 AEDT: the first attempt to set the four
  environment secrets failed on the API rate limit (5,000 requests
  an hour, spent by the day's inventory), and the key file was
  shredded by the same command before the failure was read. No
  secret was set and no copy of the key exists, so the App's key is
  regenerated: Mikal generates a new one on the App's page and
  deletes the old, and the four secrets are set from it once the
  limit resets, with the read-back checked before the file is
  shredded.
- 2026-10-31 08:00 AEDT: definition of done run from the record and
  the API. Every GitHub-side item but one passed (role, owners, eight
  environments by script, four environment secrets and no
  repository or organisation copy across 30 repositories, the
  variable, the App's permissions, four rulesets with exactly the
  team and the App, #211 open, no probe branch or run, prune pushes
  as the bot in all three repositories after the demotion). The
  soak item is met by Mikal's 07:45 waiver, not by a run: neither
  dispatched audit succeeded end to end. `pre-commit` passes and
  the audit verdicts match `main`. Two record
  cells stay pending on the operator: the bot's token list, and
  with it the contents-read grant to `AUDIT_TOKEN` that Monday's
  scheduled audit needs to file in private-ci.
- 2026-10-31 08:25 AEDT: the automated review of #348 (7 fix, 1
  document, 4 consider) found the record overstating what the soak
  proved (`update-docs` pushes with the job's own `GITHUB_TOKEN`,
  not the bot's), the audit's dependence on the owner role already
  documented in `docs/consistency-audits.md`, and the
  `bypass_actors` re-probe predating the security manager grant.
  All corrected in this section; the re-probe was run again under
  the role (same answer), the security manager dependency is now
  in `docs/consistency-audits.md`, the private-ci failure is #349,
  and the hunkydory residual is written into step 3c's brief.
- 2026-10-31 08:45 AEDT: the second review round corrected the
  description of the security manager role above and in
  `docs/consistency-audits.md`, recorded that it widens what every
  bot token can reach, filed #352 for the read-only audit App that
  lets the role go, cited the kerbside and ryll push proofs by rule
  suite and commit, marked the soak item as met by waiver, dated
  the 2026-10-29 notes correctly, and put the notes in time order.
- 2026-10-31 09:45 AEDT: Mikal added Contents read to `AUDIT_TOKEN`
  (the existing token, same expiry) for #349, and
  `docs/consistency-audits.md` now says so. The test is audit run
  41140043392, dispatched on `main` at 09:44 AEDT: **its result is
  not yet recorded**. Next session: read its `manage-issues` job; if
  it filed in private-ci without the `defaultBranchRef` error,
  close #349 citing the run and resolve the `AUDIT_TOKEN` half of
  plan-blocker b2; if not, the docs paragraph and this note are
  wrong and the REST-creation route in #349 is next. Then read
  review round 4 on #348 if it has arrived.
- 2026-10-31 09:20 AEDT: the third review round: the probe's date
  in the record cells, step 3a's probe placed in hunkydory with an
  unset secret failing it, step 3c's brief leading with the
  measured fallback shape, the key-set order against the soak said
  plainly, the App's key count added to the record and the
  definition of done as an operator confirmation, the environment
  half confirmed readable from the regenerated compliance page,
  andris's ruleset dated, and the security manager role named in
  Situation. Two decisions recorded for Mikal with `plan-blockers`:
  the key confirmation, and whether #352 lands before phases 2 and
  3.

#### Risks and mitigations

* **The demotion breaks something the bot's ownership was quietly
  serving.** Decision 8: the demotion lands alone and the next
  audit, Renovate and prune runs are watched before any key exists.
  If one fails for a permission reason, restore the owner role,
  record what needed it, and that credential moves to its own
  identity before the demotion is retried. The management session
  checks the three run URLs in the record, not the operator's
  word.
* **The probe leaks a token.** It prints two headers and a login,
  runs on an ephemeral runner, and its run, branch and file are
  deleted; the management session confirms `gh run list --branch`
  is empty before 1a is recorded as done.
* **An environment is created without its policy.** The PUT and
  the POST are two calls, and a PUT alone leaves an environment any
  branch can use. The definition of done reads both endpoints back
  for all eight, by script, and 2b re-reads them before each merge.
* **The App is refused as a bypass actor.** 1c does andris first
  and reads the GET back; a refusal means the installation, not the
  ruleset, is wrong.
* **The key lands at the wrong level.** `gh secret set` without
  `--env` writes a repository secret. The definition of done lists
  repository secrets for all eight and the organisation's, and
  expects no `REVIEW_MARKS_APP_KEY` at either level.
* **The demotion hides `bypass_actors` from the audit.** It did:
  the token is the bot's and lost `bypass_actors` and the secret
  listing with the demotion (record, 1a and 1b), so 3c takes its
  fallback shape. Not a reason to keep the bot an owner.

#### Definition of done

Every item is checked by the management session from the record
and the API, not from the operator's summary.

* Every table and placeholder under *Record* is filled or says why
  it could not be, and open questions 3, 5 and 6 each have a
  settled sentence.
* `gh api orgs/shakenfist/memberships/shakenfist-bot --jq .role`
  prints `member`, and for each of the eleven repositories
  `repos/shakenfist/<r>/collaborators/shakenfist-bot/permission`
  prints `write`.
* `gh api 'orgs/shakenfist/members?role=admin' --jq '.[].login'`
  prints `mikalstill` alone.
* For each of the eight repositories, this prints `ok`:

  ```bash
  r=<repo>; d=$(gh api repos/shakenfist/$r --jq .default_branch)
  p=$(gh api repos/shakenfist/$r/environments/review-marks \
      --jq '.deployment_branch_policy | "\(.protected_branches) \(.custom_branch_policies)"')
  b=$(gh api repos/shakenfist/$r/environments/review-marks/deployment-branch-policies \
      --jq '[.branch_policies[] | "\(.type):\(.name)"] | join(",")')
  [ "$p" = "false true" ] && [ "$b" = "branch:$d" ] && echo ok || echo "FAIL $r $p $b"
  ```

* `repos/shakenfist/<r>/environments/review-marks/secrets` lists
  `REVIEW_MARKS_APP_KEY` in andris, hunkydory, kerbside and ryll,
  and lists nothing in actions, development, sfui and
  kerbside-client.
* `gh secret list --repo shakenfist/<r>` for all 30 repositories
  and `gh secret list --org shakenfist` show no
  `REVIEW_MARKS_APP_KEY`.
* `gh variable list --org shakenfist` shows
  `REVIEW_MARKS_APP_CLIENT_ID`, visible to all repositories.
* `gh api orgs/shakenfist/installations --jq '.installations[].app_slug'`
  lists `shakenfist-review-marks`, and its permissions are
  `contents: write` and `metadata: read` only.
* For each of the four rulesets, `bypass_actors` contains exactly
  two entries: the team (11722172, `Team`, `always`) and the App
  (`Integration`, `always`).
* `gh issue view 211 --json state --jq .state` prints `OPEN`.
* The App's page lists exactly one private key, the one the four
  secrets were set from, and the record says so.
* The probe branch does not exist, and `gh run list --repo
  shakenfist/development --branch <probe branch>` prints nothing.
* The three soak URLs in the record are runs that succeeded after
  the demotion, and the three prune pushes have `actor_name:
  shakenfist-bot` in their rule suites.
* `pre-commit run --all-files` passes, and
  `python3 scripts/audit-check.py --repo-path . --repo-name
  development` reports the same verdicts as on `d2044c3`.

#### Back brief

Before 1b, back brief Mikal on decisions 3 (the demotion is not
held by `AUDIT_TOKEN`), 4 (the runner-controller App is gated like
the bot) and 7 (the App's name and installation set). Creating an
App and a key is cheap to do and tedious to redo across four
repositories, and the demotion is the one step in the plan that
could break a scheduled job overnight.

### Phase 2: `prune-reviews` mints an App token

Planning effort: medium. The template change has a worked example
in its own token comment, and the rollout follows the
`review-tracking-adoption` skill.

**Order matters.** Until phase 3, the bot is still in the bypass
team, so a copy that still uses `DEPENDENCIES_TOKEN` keeps working.
A copy that has merged pushes as the App instead, which only works
because step 1c made the App a bypass actor first: no copy merges
into andris, hunkydory, kerbside or ryll until 1c is recorded. With
both bypasses in place, nothing breaks partway through. Phase 3
must not start until the copies in those four repositories have
merged.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | worktree | In this repository, change `templates/review-tracking/prune-reviews.yml` and its deployed twin `.github/workflows/prune-reviews.yml` (keep them byte-identical). The template is the post-#254 shape described in Situation: no checkout, a token-free `Fetch the prune script` step, then `Prune and import review marks`. Add `environment: review-marks` to the `prune-and-import` job; if `actionlint` and GitHub accept `environment: {name: review-marks, deployment: false}`, use that so each run does not record a deployment, and otherwise say in the README that every run appears in the Deployments view. Change `runs-on` as open question 6 settled, and revisit the `timeout-minutes` reasoning for a vm runner's boot time if it moved. If it did, also rewrite the comment above `Fetch the prune script` and the README's "Runner isolation" section, which both justify the no-checkout design in static-runner terms. Keep the design and the test that forbids `actions/checkout`, and say why: it is defence in depth, and a repository can still move the job back to `static`. Add a step `id: app-token` between the two, using `actions/create-github-app-token` pinned to a sha with a `# vX.Y.Z` comment (a v3 release or later: its `action.yml` at v3.3.0, read on 2026-10-14, accepts `client-id` and the `permission-<name>` inputs and deprecates `app-id`) with `client-id: ${{ vars.REVIEW_MARKS_APP_CLIENT_ID }}`, `private-key: ${{ secrets.REVIEW_MARKS_APP_KEY }}`, and `permission-contents: write`. Leave `owner` and `repositories` unset, which limits the token to the current repository, and say why in a comment: the App is installed on several repositories, and naming the owner would widen every token to all of them. Guard it on the key being present, but note that the `secrets` context is not available in a step's `if:`: expose presence through a job-level `env:` entry (for example `HAS_REVIEW_MARKS_KEY: ${{ secrets.REVIEW_MARKS_APP_KEY != '' }}`) and test that, and confirm with `actionlint` that the form you choose is accepted. The script step's `PUSH_TOKEN` becomes `${{ steps.app-token.outputs.token \|\| github.token }}`. The fetch step still has no token, and the push token still goes only to the script. `scripts/tests/test_ci_prune_reviews.py`'s `workflow_steps` and `test_the_workflow_never_checks_out_the_workspace` hold the old shape, and will fail. Restate each of their assertions, keeping what it protects rather than loosening it. (1) The exact step-name set gains the token step's name. (2) The `PUSH_TOKEN` line assertion becomes the new expression. (3) `text.count('secrets.') == 1` becomes: `secrets.REVIEW_MARKS_APP_KEY` appears only in the job-level presence flag and the token step, and neither the fetch step nor the script step contains `secrets.`. (4) No token or secret reaches `Fetch the prune script`, as now. Add assertions that the job carries `environment: review-marks` and that the token step has no `owner:` or `repositories:` input. `test_the_workflow_steps_land_the_review_state` runs the fetch and script steps by name, and must not try to run the token step. Rewrite the token comment above the job's `permissions:` block, saying that `contents: write` is there for `github.token` in the repositories with no pull-request ruleset, not for the App, whose token carries its own permission, (it begins "Repositories whose default branch ruleset requires a pull request") to say why: the App is the ruleset's bypass actor, its key lives in an environment only the default branch can use, a repository with no pull-request ruleset has neither and pushes with `github.token`, and an App push, like the PAT push before it, retriggers the workflow once into a run that finds nothing to do. Remember the `&&`/`\|\|` value semantics explained at `templates/pin-indirect-dependencies/pin-indirect-dependencies.yml:154-159`. Leave the script's `git config user.name 'shakenfist-bot'` alone: the commit identity is not the push identity, and changing it is outside this plan. Update `templates/review-tracking/README.md`: replace the "Optionally, a `DEPENDENCIES_TOKEN` secret" bullet and its "That fallback is silent" paragraph with the environment, its branch policy, the secret and the variable, in the order an operator must create them (open question 4), keeping the warning about a silent fallback in its new form. Also say that every repository running the workflow gets the environment, with its policy, before its first run, whether or not it holds the key, since one GitHub creates on first reference has no policy; and that the key is only ever an environment secret, never a repository or organisation one (open question 4); change the Token column of its projects table; and fix the top of the file and the workflow's header comment where they say the commit lands "as shakenfist-bot", so that they say who pushes. Add the environment setup, as an operator step before the first `prune-reviews` run, to `.claude/skills/review-tracking-adoption/SKILL.md`, which today says nothing about tokens, with the same two warnings. Run `actionlint` and `pre-commit run --all-files`. |
| 2b | medium | sonnet | worktree | Roll the 2a template out with the `review-tracking-adoption` skill, one pull request per repository, in this order: andris (the smallest adopter, and the one found failing before 2026-10-29), hunkydory, ryll, kerbside (the four whose pushes need the bypass), then the four without a pull-request ruleset (actions, sfui, kerbside-client, and development, whose own copy 2a already changed). Before each pull request merges, confirm its repository's `review-marks` environment exists with the default-branch policy (phase 1 step 1b created all eight), so the first run never creates an unprotected one. If shakenfist, instar or client-python-k3s has adopted review tracking by then, it joins the first group and phase 1 step 1b is repeated for it. Each pull request copies the template byte for byte and changes nothing else. After each merge, confirm the next `prune-reviews` run either pushed as the App or found nothing to do, and record the run URL here. On the first merge, andris's, also confirm from the run log that the `app-token` step ran rather than being skipped, before the hunkydory, ryll and kerbside pull requests are opened. A presence guard that always evaluates false falls back to `github.token` without saying so, and `actionlint` cannot show that. The commit's author and committer do not show this, because the script sets them; in the four repositories with a ruleset, the rule-suite history (`repos/shakenfist/<r>/rulesets/rule-suites?rule_suite_result=bypass`) shows the push's `actor_name`, and in the four without one the run's own log does. A run that fails to mint a token in a repository that has the environment means 1b's setup is wrong there: stop and report rather than adding a fallback. |

**Definition of done.** Every live `prune-reviews.yml` is
byte-identical to the template (compare `gh api` content against
it; a comment may still mention the PAT by name, so grep for
`secrets.DEPENDENCIES_TOKEN`, not the bare name, if grepping at
all); andris, hunkydory, kerbside and ryll each have a
`prune-reviews` push to its default branch whose rule-suite
`actor_name` is the App.

### Phase 3: move the bypass and enforce it

Planning effort: high. Steps 3a and 3b change who can push to seven
default branches, and a mistake either locks out review marks or
leaves the old path open without anything saying so.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Operator step. The App has been a bypass actor since step 1c, and phase 2's definition of done shows it landing. Remove `shakenfist-bot` from `sf-can-skip-merge-queue`, which takes its bypass away on all seven. Its role was already changed in 1b; re-record `repos/shakenfist/<r>/collaborators/shakenfist-bot/permission` for all eleven repositories (Situation's ten and andris) to confirm nothing restored it. Prove both halves. First, a push to `develop` in hunkydory is rejected by the ruleset, once authenticated with `DEPENDENCIES_TOKEN` and once with `RENOVATE_TOKEN`, from a throwaway workflow on a branch, deleted afterwards, built as phase 1's probe ran: on a `push` trigger for its own branch, since `workflow_dispatch` refuses a workflow that is not on the default branch, and on `[self-hosted, vm, debian-13, s]`, whose image has no `gh`, so with `git` and `curl` rather than `gh`. It runs on a branch of hunkydory itself, not of this repository: `DEPENDENCIES_TOKEN` is a repository secret of hunkydory and resolves to nothing here (phase 1's probe found it unset), while `RENOVATE_TOKEN` reaches hunkydory as an organisation secret. Each probe step fails the run if its secret resolves to an empty string, so an unset secret can never pass as a rejection. Each probe pushes an empty commit (`git commit --allow-empty`) on the current tip of `develop`, so an accepted push changes no file. If one is accepted, the bypass is still there: stop, record it from the rule-suite history, open a revert pull request at once, and do not go on to the second half until it is understood. A bypass belongs to an identity, no ruleset names the admin role or `OrganizationAdmin`, and 1b left the bot an admin nowhere, so one repository proves it for all seven. Second, the next `prune-reviews` run in andris, hunkydory, kerbside and ryll lands or finds nothing to do. Record the ruleset JSON's `bypass_actors` for each repository here. |
| 3b | medium | sonnet | none | In this repository, amend `docs/plans/PLAN-workflow-ref-trust.md`: open question 1's default becomes the App from this plan rather than a team of one, with a sentence saying the residual it described is closed; step 2a's brief installs the App on `actions`, adds the key to the `review-marks` environment that phase 1 step 1b already created in `actions` (as an environment secret, never a repository one), and names the App as the only bypass actor; the "documented set" that step 2b's criterion compares `bypass_actors` against is that App. Step 2a's question of whether the audit token sees `bypass_actors` cites this plan's step 1a record (and 1b's, if it re-ran the probe) rather than measuring again. Remove the #211 entry from that plan's Future work and its related-issues list, citing this plan. Touch nothing else in that plan. |
| 3c | high | opus | worktree | **Phase 1 measured the shape (record, 1a and 1b): `AUDIT_READ_TOKEN` sees neither `bypass_actors` nor a repository's secret list, under the security manager role too, so build the fallback shape described at the end of this brief: the check measures the `review-marks` environment and its branch policies only, and the bypass-actor rules and the repository-level key check live in the spec's reviewer-confirmed half, with the actor-type cases below as rows of the spec's table rather than tests of code, until a read identity that sees `bypass_actors` exists (#352).** Add a criterion `bypass-actor-identity` in `scripts/audit/checks/github_config.py`, following `DeleteBranchOnMerge` for the API-call shape, with id `bypass-actor-identity`, its `spec` and its `issue_title` (`Bypass actor identity`). For each ruleset that applies to the default branch and carries a `pull_request` rule, it judges every bypass actor by its `actor_type`, and reports each one that fails. `Team` fails. `RepositoryRole` passes only for the admin role (confirm its `actor_id` against a real ruleset GET rather than assuming it) and fails for any other. `OrganizationAdmin` passes. `Integration` passes only if it is the review-marks App (a module constant holding its id). `DeployKey` fails: a deploy key with write access is an automation credential, like the PATs this plan removes. Any other `actor_type` is an error, never a pass. The admin-role and `OrganizationAdmin` passes rest on 1b having left no automation identity as an owner or an admin, and in particular as an admin of any repository the App is installed on, where it could widen the environment's policy. The check cannot see that, so the spec puts it in its "Required, but confirmed by a reviewer" half. That half also records the residual phase 1 accepted on hunkydory (execution notes, 2026-10-30 18:15): the runner-controller App `shakenfist-k3s-ci-arc` holds `administration: write` there until 33fl's k3s CI plan narrows it at its phase 5, and whoever holds its key could widen hunkydory's `review-marks` policy, read the review-marks key from a branch workflow, and mint push tokens for all four repositories the App is installed on. Separately, in every repository whether or not it has a pull-request ruleset, the check reads `repos/{org}/{repo}/environments/review-marks`. Where the environment exists, it fails unless the policy is exactly open question 4's default-branch policy: `protected_branches` false, `custom_branch_policies` true, and one policy, `type: branch`, named for the default branch with no glob characters. It also fails if `REVIEW_MARKS_APP_KEY` appears in `repos/{org}/{repo}/actions/secrets` (names only), since a repository-level copy is readable from any branch. `AUDIT_READ_TOKEN` cannot list secrets (phase 1's record), so that half moves to the spec's reviewer-confirmed half, beside the organisation-level check, which the audit cannot make either way. A ruleset response without `bypass_actors` is an error, never a pass, as in `PLAN-workflow-ref-trust.md`'s phase 2. A repository with no pull-request ruleset and no `review-marks` environment reports `not_applicable` with that reason. **Step 1b recorded that `AUDIT_READ_TOKEN` cannot see `bypass_actors`, under the security manager role too**, so do not widen the token. Instead, the check measures only the environment and secret half above. The spec moves the bypass list to its reviewer-confirmed half, as `PLAN-workflow-ref-trust.md` step 2b does, and the actor-type tests below become tests of the spec's table rather than of code. Register it in `CHECKS` in `scripts/audit/registry.py`, write `docs/audits/bypass-actor-identity.md` following `docs/audits/README.md`, add its row there, add its lines to `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in `scripts/tests/test_metadata.py`, and add pass, fail and not-applicable tests to `scripts/tests/test_github_config.py` using `CheckTestCase` and a stubbed GitHub client, including a team bypass, a non-admin repository role, an `OrganizationAdmin` actor (a pass), a `DeployKey` actor, an unrecognised `actor_type` (an error), a missing `bypass_actors` key, an unknown App, an environment with no branch policy in a repository with no pull-request ruleset, an environment whose policies add a tag policy beside the default branch, one whose branch policy is a wildcard, one with `protected_branches: true` (each a fail), a repository-level `REVIEW_MARKS_APP_KEY`, and a repository with neither a pull-request ruleset nor the environment (`not_applicable`). Before writing the team rule, decide with the operator what `sfconductor`'s bypass becomes (open question 3). If it stays a team, the spec says the check is expected to fail there and why, rather than exempting it in code. But then 3c's first weekly run files seven issues that nothing in this plan can close. So 3c does not land with the team rule until open question 3 has an outcome. Until then, land the criterion without the team rule, reporting team bypass actors in the spec's reviewer-confirmed half, and say so in the commit message. State in the commit message how many repositories it newly fails, including this one, against this prediction. If open question 3 leaves `sf-can-skip-merge-queue` in place for `mikalstill` and `sfconductor`, all seven repositories in Situation fail on the team rule. This repository, actions (until `PLAN-workflow-ref-trust.md` step 2a), sfui and kerbside-client carry only the "Protect default branch history" ruleset, with no `pull_request` rule. They pass on the environment half, because 1b gave each one its policy. So seven newly fail, none of them this one. On the fallback shape, the team rule is not measured, and if 1b and 2b did their job nothing newly fails. |

**Definition of done.** `shakenfist-bot` is not a member of
`sf-can-skip-merge-queue`, and its role is what open question 5
settled; a push to a protected default branch with either bot PAT is
rejected; `bypass-actor-identity` runs in the
weekly audit and its results match what step 3a recorded; #211, reopened in
step 1a, is closed from the pull request that lands 3c, with a link to this
plan.

### Phase 4: push audit

Planning effort: medium. The runbook and the ranges are fixed; the
work is running it.

Run `PUSH-AUDIT.md` once per landing recorded in this repository's
`Merged` cells, with `AUDIT_RANGE` set as its "How to use this
runbook" section says. The rollout pull requests in step 2b land in
other repositories, and each is audited there as part of landing
it; this phase cites those audits rather than re-running them.
Phases 1 and 3a land no code anywhere, so the audit of them is a
review of what this plan recorded against what the GitHub API
reports on the day the audit runs.

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

**In this plan.** Steps 1a, 1b, 1c and 3a are the exception: they
change organisation and repository settings with `admin:org`, so
the management session does them with the operator rather than
delegating them. They are also the only steps whose effect no
test here covers, which is why each has its own proof in its
brief.

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

Specific to this plan:

* No user or team bypass actor on any default-branch ruleset
  belongs to an automation identity, other than `sfconductor`
  pending open question 3. The only other automation bypass is the
  review-marks App, and `bypass-actor-identity` reports compliant
  everywhere except where the plan says why not.
* A push to a protected default branch authenticated with
  `DEPENDENCIES_TOKEN` or `RENOVATE_TOKEN` is rejected.
* `shakenfist-bot` is not an organisation owner and holds no admin
  role on any repository. No exception is possible here: open
  question 5 makes it a gate on the key, not a recorded blocker.
* The App's key exists only as an environment secret, in
  environments with the default-branch policy (open question 4),
  and every `review-marks` environment has that policy, key or no
  key.
* `prune-and-import` runs where open question 6 settled, and the
  plan says what residual remains if that is still the static pool.
* `prune-reviews` keeps landing review marks in every adopted
  repository.
* #211 is closed, and `PLAN-workflow-ref-trust.md` step 2a names
  the App rather than a team of one.

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

...

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

...

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->

**In this repository.** Future work, as of writing:

- Narrow `DEPENDENCIES_TOKEN` and `RENOVATE_TOKEN`. After this plan
  neither can bypass a ruleset, but both still push branches and
  open pull requests in every repository the bot can write to, and
  `DEPENDENCIES_TOKEN` is in scope of pull-request jobs. A
  fine-grained token per use, or a second App for branch pushes, is
  the next step. Fine-grained tokens owned by a user expire within a
  year, so that is a rotation commitment, not a one-off change.
- `sfconductor` merges pull requests past the merge queue and
  required checks in four repositories (open question 3). Phase 1
  records where its credential lives; whatever follows from that is
  its own issue.
- If step 1a finds `AUDIT_TOKEN` is the bot's, 1b's demotion may
  stop it listing repository secrets or reading `bypass_actors`,
  and 3c then moves those halves to its reviewer-confirmed half. The
  way back to measuring them is a separate read-only identity for
  the audit, for example a second App with `secrets: read` and
  `administration: read`, rather than making the bot an admin
  again.
- `shakenfist-k3s-ci-arc`, the runner-controller App installed on
  2026-10-16, has `administration: write` on the repositories it is
  installed on (phase 1, survey item 5), which edits rulesets and
  environments there. It needs that only to register
  repository-level runners; registering them at organisation level
  (`organization_self_hosted_runners: write`) would take
  repository-admin reach away from an automation identity whose
  key lives outside GitHub.
- `PLAN-renovate-cadence.md` (registered 2026-10-03, not started)
  replaces the seventeen copies of `renovate.yml` with one central
  runner in this repository, and its open question 1 asks where
  `RENOVATE_TOKEN` lives and what it can reach. Once that lands,
  `RENOVATE_TOKEN` is in scope of one workflow rather than
  seventeen, which makes narrowing it (the first item above) a
  one-repository change. Neither plan waits on the other: this one
  removes the bot's bypass whichever repositories Renovate runs
  from.

Related issues, as of writing:

- #211 is the issue this plan closes. #255, which added this plan,
  closed it on merge by referencing it; phase 1 step 1a reopens it.
- #153, through `PLAN-workflow-ref-trust.md`, whose step 2a this
  plan unblocks.
- #88 (standardise renovate groupings) and #58 (Dependency
  Dashboard) are about the same Renovate runs, but neither changes
  which identity they run as.
