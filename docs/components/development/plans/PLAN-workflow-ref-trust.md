# Workflow ref trust

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every morning
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a daily run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

Two things make planning here different from planning in a
repository that holds a product, and both should shape any plan
written from this template:

* **The blast radius is other people's repositories.** The daily
  workflow files and closes GitHub issues fleet-wide. A change
  that is merely wrong does not produce a red build; it produces
  issues in ten repositories, or silently closes ones that should
  have stayed open. Always pass `--dry-run` when running
  `audit-manage-issues.py` by hand.
* **This repository is in its own audit matrix.** A standard we
  exempt ourselves from is a standard we stop noticing the cost
  of. A change to a criterion is a change we are measured against
  the next morning, so a plan should say how many repositories --
  including this one -- it newly fails.

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

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

[#153](https://github.com/shakenfist/development/issues/153) named
two habits in the fleet's workflows: reusable workflows are called
at `@main` with `secrets: inherit`, and third-party actions are
referenced by a tag rather than a commit sha. Both mean that code
nobody reviewed in the calling repository runs inside its CI.

**The `secrets: inherit` half has already landed** (fe4b3ba,
2026-09-29). The `reusable-workflow-secrets` criterion reports any
job that passes `secrets: inherit` to a reusable workflow, and
leaves two callees to criteria that already owned them
(`export-repo-config`, `ci-review-automation`). The fix in the
calling repositories is phase 1 of this plan. These were the live
`secrets: inherit` lines on each repository's default branch at the
phase 1 survey, read through the GitHub API on 2026-10-02 before any
phase 1 pull request was opened (local clones were stale and
overcounted). The pull requests that remove them are listed in the
phase 1 section.

| Criterion | Repositories | Open issues |
|-----------|--------------|-------------|
| `reusable-workflow-secrets` | 4 | client-python#410, instar#610, occystrap#150, shakenfist#4380 |
| `export-repo-config` (its inherit finding) | 9 | agent-python#146, client-python#409, client-python-k3s#79, clingwrap#138, divergulent#121, instar#600, library-utilities#63, occystrap#148, sfui#38 |
| `ci-review-automation` (its inherit finding) | 0 | none; its open issues are about other findings |

fe4b3ba says no reusable workflow in the fleet read a secret. That is
true of the shared callees in `actions` (`smoke-cluster.yml` and
`export-repo-config.yml`) and of the `test-drift-fix.yml` template. It
is **not** true of instar's local `test-drift-fix.yml`, which reads
`secrets.GITLAB_TESTDATA_TOKEN` to fetch its test data. Deleting
instar's inherit would leave that token empty. Phase 1 handles this.

**The pinning half has not been started.** The spec of
`reusable-workflow-secrets` explicitly leaves it out. These
measurements come from local clones of the default branches, with
worktree copies excluded, on 2026-10-02:

- There are 633 remote `uses:` references, and none is pinned to a
  sha.
- 93 of them are `shakenfist/actions/...@main`, in 14 repositories.
- The other 540 are third-party references, all by tag except one
  branch, noted below. The largest
  groups are `actions/checkout` (272), `actions/upload-artifact`
  (93), `actions/download-artifact` (36), `dtolnay/rust-toolchain`
  (16), `renovatebot/github-action` (15), `github/codeql-action/*`
  (40), `dorny/paths-filter` (13), `softprops/action-gh-release`
  (11) and `pypa/gh-action-pypi-publish` (11). `pypa/...@release/v1`
  is a branch, not a tag.
- **Every renovate-managed repository already runs Renovate**, self
  hosted through `renovatebot/github-action`. Every config has
  `automerge: false` for minor, patch and digest updates, so each
  bump is a pull request that a person reviews. Two repositories
  have no `renovate.json` at all: visual-digest-rust and
  private-ci.

**What `@main` on `shakenfist/actions` actually trusts.** That
branch has a ruleset ("Protect default branch history") that blocks
deletion and non-fast-forward pushes, and nothing more. There is no
classic branch protection and no pull request requirement. Over the
last three months, 146 first-parent commits landed on it:

| How it landed | Count |
|---------------|-------|
| Merged pull request | 81 |
| Pushed directly, by a person | 21 |
| Pushed directly, by `shakenfist-bot` (review-mark prune or import) | 44 |

The bot pushes with `DEPENDENCIES_TOKEN`, a personal access token
that falls back to `github.token`
(`actions/.github/workflows/prune-reviews.yml:90`). Today, anything
that can push to `actions` main runs, at the next CI run, in every
one of the 14 consuming repositories. That includes anyone holding
that token. It runs with whatever those jobs can reach. After the
inherit rollout that is `github.token` under the caller's
`permissions:` block, but it is also everything on the self-hosted
runner the job lands on: `smoke-cluster.yml` authenticates to its
clusters with an on-disk key (its lines 18-20). Composite actions
from `actions` (for example `pr-bot-trigger@main`) are worse again:
they run inside the caller's own job, with its environment and any
secrets that job was given. This is the narrowest point of the whole
problem: one setting in one repository, rather than 93 references
spread over 14.

**What sha-pinning `shakenfist/actions` would cost.** At 146 changes
per quarter, about 1.6 a day, nearly every day would bring a change.
Renovate updates an open digest pull request in place and runs once
a night, so the cost is at most one pull request per consuming
repository per day: an upper bound of 14 a day across the fleet,
each one needing a human. The audit also has callers that hard-code `@main`
as the expected form: `CI_REVIEW_SHARED_ACTION` and
`CI_REVIEW_TRIGGER_ACTION` in
`scripts/audit/checks/ci_workflows.py:162-165`, and the rationale
in `REPO_OVERRIDES` (`scripts/audit/repo.py:33`). Those would all
have to learn to accept a sha.

## Mission and problem statement

Answer #153 with a settled fleet policy on which workflow references
may move, enforced by the audit wherever it can be decided
mechanically. In brief, the policy is:

1. **Finish the inherit rollout** that fe4b3ba started, so no
   callee can see a secret it was not given by name.
2. **Make `@main` on `shakenfist/actions` mean "landed through a
   pull request that passed CI".** Change that repository's ruleset
   so that changes reach main only that way. With one maintainer
   this is a record and a CI gate, not a second reviewer (open
   question 2), and the specs say so. First-party
   reusable workflows and composite actions keep the moving ref.
   The decision not to sha-pin them, and its cost, is recorded in
   the specs.
3. **Pin third-party actions to a sha**, with the version in a
   trailing comment. Renovate (`helpers:pinGitHubActionDigests`)
   keeps the pins current, and a new criterion enforces them.
4. **Express a repeated workflow once.** A template meant to be the
   same in every repository becomes a short caller of a reusable
   workflow or composite action in `actions`, at the moving ref
   that point 2 makes safe to trust, and the template's own
   criterion checks the caller. This belongs in this plan because
   it rests on the same trust decision: every job moved into
   `actions` widens what an unreviewed push to its main would reach,
   so it waits for phase 2.

Out of scope:

- Docker image digests in `container:` and `image:` keys. The
  `image-supply-chain` plan owns the images the fleet builds.
- Pinning the Python, cargo or npm dependencies that workflows
  install. Other criteria cover those.
- Harden-runner-style egress control.
- Renovate's own workflow. A shared workflow would still run once
  per repository, and the better answer is one central run for the
  whole fleet, which `PLAN-renovate-cadence.md` delivers.
- A fleet-wide "these workflows look alike" detector. It cannot tell
  drift from a legitimate per-repository difference, so it would
  file issues against copies that are right to differ. Phase 5
  enforces the policy through each template's own criterion
  instead.

## Open questions

1. **How does the review-mark bot keep landing on `actions` main
   once a pull request is required?** If `shakenfist-bot` is a
   ruleset bypass actor, the PAT path stays open. If it is not,
   `prune-reviews` has to open a pull request and auto-merge it,
   which is a template change across every adopted repository.
   A ruleset bypass cannot name a user, only an organisation admin,
   a repository role, a team, an app or a deploy key, so "bypass for
   the bot" means a team whose only member is `shakenfist-bot`, or a
   GitHub App. A repository-role bypass would let every writer
   through and is not acceptable.
   *Default:* a GitHub App, as the only bypass actor, rather than a
   team of one. `PLAN-bot-bypass-identity.md` built it:
   `shakenfist-review-marks` carries `contents: write` and the
   implicit `metadata: read`, and nothing more. Its key is an
   environment secret in a `review-marks` environment that admits
   only the repository's default branch. A token for it is minted
   per run, scoped to the one repository, and revoked when the job
   ends. `shakenfist-bot` has left the team that used to bypass, so
   its PATs bypass nothing; that plan's phase 3 record has two
   refused pushes as proof. Step 2a installs the App on `actions`,
   puts the key in the `review-marks` environment that already
   exists there, and names the App as the only bypass actor on the
   `actions` ruleset. The bypass is not narrowed to the
   review-tracking paths: a ruleset's bypass list covers the whole
   ruleset, branch rulesets have no per-path condition, and a
   file-path restriction is a push rule that binds every push, pull
   request merges included. Write phase 2's success criterion for
   an unnarrowed bypass. The spec states the residual risk the old
   default carried accurately: a `DEPENDENCIES_TOKEN` bypass moved
   the boundary from "can push to main" to "can run a workflow that
   has `DEPENDENCIES_TOKEN` in scope", which is wider than one
   setting in one repository. What closed that bypass residual is
   `PLAN-bot-bypass-identity.md` phase 3a taking `shakenfist-bot`
   out of `sf-can-skip-merge-queue` on 2026-11-08 (the refused
   pushes above); this is the work #211 tracked, done there rather
   than here. Step 2a's job is to install the App so the
   review-mark path keeps landing without re-opening it. After 2a
   the credential that bypasses the ruleset is a per-run App token,
   minted and revoked inside the job; the key that mints it is
   long-lived, and what bounds it is the `review-marks`
   environment's default-branch policy. The residuals that remain
   are the ones `docs/audits/bypass-actor-identity.md` keeps in its
   reviewer-confirmed half, and `DEPENDENCIES_TOKEN` itself is
   still in the scope of `pull_request`-triggered workflows in
   shakenfist, client-python and kerbside, which is why 2a's scope
   survey below stands. The `|| github.token` fallback stays
   harmless, because `github.token` is not a bypass actor and its
   push is simply refused; nobody should add the Actions app as a
   bypass actor to "fix" that refusal. `PLAN-review-import.md` does
   not move prune to pull requests, so that alternative remains a
   template change this plan does not make.
2. **What does "a pull request is required" mean with one
   maintainer?** A required approval count of one cannot be met by
   the author. *Default:* require a pull request with zero required
   approvals, plus required status checks. Rulesets require check
   contexts by job name, not workflow file, so step 2a names them
   from `actions`' `ci.yml`. Decision 1 settles them as `Check
   paths`, `Lint`, `Unit tests` and `gitleaks`, with `Automated
   reviewer` deliberately not required; read the decision rather
   than this default, which named three before the survey found
   five jobs. `Lint` and `Unit tests` skip on a review-only change,
   which 2a measured is enough to satisfy the rule. The gain is
   that nothing lands without CI and a visible record. It is not
   gated on a second person. And a
   required check that runs workflow code from the pull request
   itself can be edited by that pull request to pass, so step 2a
   also looks at an organisation "require workflows" rule, sourced
   from a protected ref, as the stronger form. The spec says all of
   this rather than implying more.
3. **Should GitHub-owned actions (`actions/*`, `github/*`) be exempt
   from sha-pinning?** They are the bulk of the references (about
   70% of all 633, and over 80% of the 540 third-party ones) and the
   lowest risk. *Default:* no exemption. One rule is
   easier to hold than one with a carve-out (the same reasoning
   `reusable-workflow-secrets` gives for local callees), and the
   Renovate preset makes the cost of the pins uniform.
4. **Is the extra Renovate pull request volume acceptable?** Today a
   `@v4` tag silently absorbs every v4.x release. Once pinned, each
   release becomes a digest pull request. *Default:* measure it in
   phase 3a on this repository before deciding the fleet rollout.
   If volume is the problem, the answer is a grouping rule (one
   pull request per repository for all action digests), not
   dropping the pins. Grouping interacts with #88 (standardise
   renovate groupings), so check that issue's state first.
5. **Does Renovate pin `owner/repo/.github/workflows/x.yml@ref`
   reusable-workflow references the same way it pins actions?** This
   only matters for third-party reusable workflows, and there are
   none today. *Default:* the criterion measures them anyway, and
   phase 3a confirms the Renovate behaviour against a fixture rather
   than assuming it.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Finish the inherit rollout | Complete | development 8df8179 (#209); instar bdc8905 (#617); shakenfist e4c7726 (#4404); occystrap c583d68 (#152); client-python 68f295f (#415); agent-python 4bf7763 (#148); client-python-k3s 03998e0 (#83); clingwrap 125eb20 (#141); divergulent 8a2852e (#125); library-utilities 832d82d (#67); sfui ad0d6a2 (#44) |
| 2. Make `actions` main require a pull request | In progress | |
| 3. Pin third-party actions to a sha | Not started | |
| 4. Record the policy | Not started | |
| 5. Shared workflows instead of copied templates | Not started | |
| 6. Push audit | Not started | |

Phase 2 was planned on 2026-11-11, once
`PLAN-bot-bypass-identity.md` phase 3 had merged (e460a2b and
38f15f1) and made the review-marks App the bypass actor its open
question 1 names. Nothing in this plan's phase 3 depends on phase
2, so the two can run in either order; the table keeps the phases
in the order the plan reasons about them.

### Phase 1: finish the inherit rollout

Planning effort: medium. Review effort: high for instar (step 1a),
which is the only repository where a callee reads a real secret.

**Scope.** Remove every live `secrets: inherit` in the fleet: at
the survey, 17 lines in 10 repositories, closing 13 issues. In this repository, one
correction to a spec and a docstring. Out of scope: the other
findings on the `export-repo-config` and `ci-review-automation`
issues, and any other drift between the local `test-drift-fix.yml`
copies and their template. Phase 1 changes only the token
references in those copies.

**What the survey found** (2026-10-02, against default branches
through the GitHub API; the master plan's Situation section was
corrected to match, and later steps should not redo that):

- There are no live `pr-auto-review.yml` inherits. Local clones of
  sfui and client-python-k3s still had them, but the default
  branches did not. `ci-review-automation` has no part in this
  phase.
- The live `export-repo-config.yml` inherits were in nine
  repositories (table in Situation). At the survey none of their
  issues had a pull request in flight.
- `actions/.github/workflows/smoke-cluster.yml` and
  `export-repo-config.yml` read no secret. Each already carries a
  comment saying so (lines 18 and 9), so for those two, deleting the
  line is the whole fix.
- The local `test-drift-fix.yml` copies in instar, occystrap and
  shakenfist have drifted from the template.
  `templates/test-drift-fix/test-drift-fix.yml` uses `github.token`
  (lines 169, 378, 394, 441). The copies still use
  `secrets.GITHUB_TOKEN`: instar at lines 145, 248, 644, 661 and
  711, occystrap at 173, 394, 410 and 457, shakenfist at 156, 375,
  391 and 438.
- **instar's copy reads `secrets.GITLAB_TESTDATA_TOKEN`** (line
  120, the "Prepare instar-testdata" step). That contradicts both
  the fe4b3ba commit message and `docs/audits/reusable-workflow-secrets.md`,
  which say no callee in the fleet reads a secret. The original
  brief told the implementer to stop and report if this happened,
  so the plan now decides it instead (decision 2).
- instar's `GITLAB_TESTDATA_TOKEN` is set in the `env:` of the one
  "Prepare instar-testdata" step, not job-wide, so passing it by
  name does not expose it to the later steps that run pull request
  code. The calling workflow is maintainer-triggered through
  `pr-bot-trigger`.

**Decisions.**

1. **This phase is planned on the master plan's branch, not a new
   worktree.** The master plan has not merged yet, a branch cut from
   main would not contain it, and phase 1's code lands in other
   repositories anyway. Only step 1f lands here, as its own commit
   on this branch.
2. **instar passes `GITLAB_TESTDATA_TOKEN` by name rather than
   losing it.** `test-drift-fix.yml` declares it under
   `on.workflow_call.secrets` with a description and
   `required: false`, so the copy still works when triggered
   directly. `pr-fix-tests.yml` passes
   `GITLAB_TESTDATA_TOKEN: ${{ secrets.GITLAB_TESTDATA_TOKEN }}`.
   This is the named form that the `reusable-workflow-secrets` spec
   asks for, and the first live use of it in the fleet, so it is
   the worked example phase 4 will cite.
3. **Every local `test-drift-fix.yml` moves from
   `secrets.GITHUB_TOKEN` to `github.token`**, matching the
   template. Whether `secrets.GITHUB_TOKEN` resolves in a called
   workflow that was passed nothing is a question nobody here has
   verified. `github.token` makes it moot, and it is what fe4b3ba
   already did to the template for the same reason.
4. **One pull request per repository, one commit per issue**, as
   `consistency-fix` prescribes. client-python, instar and occystrap
   each close two issues from one pull request.
5. **Merge order: low risk first, shakenfist last.** The six
   repositories with only an export-repo-config line go first. They
   have nothing to break: the callee reads nothing, and the workflow
   runs on its own schedule. Then client-python and occystrap, then
   instar, then shakenfist, whose four smoke-cluster callers sit on
   its most expensive lanes.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | worktree | instar, closing #610 and #600. (1) In `.github/workflows/export-repo-config.yml`, delete the `secrets: inherit` line under the job calling `shakenfist/actions/.github/workflows/export-repo-config.yml@main`. Commit, citing #600. (2) In `.github/workflows/test-drift-fix.yml`, add `secrets: GITLAB_TESTDATA_TOKEN: {description: 'Read access to the instar-testdata GitLab repository, used by Prepare instar-testdata.', required: false}` under `on.workflow_call`, and replace every `${{ secrets.GITHUB_TOKEN }}` (lines 145, 248, 644, 661, 711) with `${{ github.token }}`. In `.github/workflows/pr-fix-tests.yml`, replace `secrets: inherit` under job `fix-tests` with `secrets:` / `GITLAB_TESTDATA_TOKEN: ${{ secrets.GITLAB_TESTDATA_TOKEN }}`. Commit, citing #610. Then grep both files for any other `secrets.` reference; anything else must be declared and passed the same way. Run `actionlint` and `pre-commit run --all-files`. Re-run `scripts/audit-check.py` from shakenfist/development against the worktree, and confirm `reusable-workflow-secrets` and `export-repo-config` no longer report the inherit. Open one pull request whose body closes both issues. Do not merge. |
| 1b | medium | sonnet | worktree | shakenfist, closing #4380. Delete the `secrets: inherit` line under jobs `smoke_collection`, `functional_matrix_merge_collection` and `ansible_modules_collection` in `.github/workflows/functional-tests.yml`, under `functional_matrix_collection` in `scheduled-tests.yml`, and under `fix-tests` in `pr-fix-tests.yml`. The callee `smoke-cluster.yml` in shakenfist/actions reads no secret; its line 18 says so. In `test-drift-fix.yml`, replace `${{ secrets.GITHUB_TOKEN }}` at lines 156, 375, 391 and 438 with `${{ github.token }}`, then grep for any remaining `secrets.`; there should be none, and if there is one, stop and report. Run pre-commit and the audit check as in 1a. Open the pull request with "Fixes #4380". Only `smoke_collection` runs on a pull request; `functional_matrix_merge_collection` and `ansible_modules_collection` run in the merge queue, so the merge queue run is the verification for those two. |
| 1c | medium | sonnet | worktree | occystrap, closing #150 and #148. Same shape as 1b: delete the export-repo-config inherit (commit citing #148), delete the `fix-tests` inherit in `pr-fix-tests.yml`, and replace `${{ secrets.GITHUB_TOKEN }}` at `test-drift-fix.yml` lines 173, 394, 410 and 457 with `${{ github.token }}` (commit citing #150). Run pre-commit and the audit check, then open one pull request closing both issues. |
| 1d | medium | sonnet | worktree | client-python, closing #410 and #409. Delete the `secrets: inherit` under job `functional_matrix` in `.github/workflows/functional-tests.yml` (callee `smoke-cluster.yml`, reads no secret; commit citing #410), and the one in `export-repo-config.yml` (commit citing #409). Run pre-commit and the audit check, then open one pull request closing both issues, and let its functional-tests run pass. |
| 1e | low | sonnet | worktree | In each of agent-python (#146), client-python-k3s (#79), clingwrap (#138), divergulent (#121), library-utilities (#63) and sfui (#38): delete the `secrets: inherit` line in `.github/workflows/export-repo-config.yml`, run pre-commit, and open a pull request with "Fixes #N". One pull request per repository. Touch nothing else, even if the same issue lists other findings. |
| 1f | low | sonnet | none | In this repository (branch `reusable-workflow-secrets`), correct the claim that no callee reads a secret. The Why section of `docs/audits/reusable-workflow-secrets.md` says "no reusable workflow in the fleet read a secret" and that every inherit "is fixed by deleting the line". The `ReusableWorkflowSecrets.run` docstring in `scripts/audit/checks/ci_workflows.py` (around line 2186) says "on 2026-09-29 was every callee in the fleet". Both should say that instar's local `test-drift-fix.yml` read `GITLAB_TESTDATA_TOKEN` and was moved to the named form, and every other inherit was a deleted line. Do not add a `consistency-audit` marker or touch `compliance.md`. Run `pre-commit run --all-files`. |

**Risks and mitigations.**

- *instar's test data fetch fails silently after the change.* If
  the secret name is wrong, the step gets an empty token.
  `prepare-testdata.sh` should then fail at authentication rather
  than carry on. Once 1a has merged, the management session
  triggers `pr-fix-tests` once on a throwaway instar pull request
  and reads the "Prepare instar-testdata" step log. That trigger is
  maintainer-only, through `pr-bot-trigger`: comment
  `@shakenfist-bot please attempt to fix`
  (`templates/test-drift-fix/README.md:20`).
- *`pr-fix-tests` in occystrap and shakenfist is not exercised by
  pull request CI*, because it runs only on a maintainer comment.
  The same post-merge trigger, once per repository, is the check.
- *A smoke-cluster caller breaks.* The callee reads no secret and
  says so. client-python's pull request run and shakenfist's
  `smoke_collection` exercise a changed caller on the pull request.
  shakenfist's other two run in its merge queue before landing, and
  its scheduled caller runs that night. The management session
  checks the next scheduled run.

**Definition of done.**

- This prints one `checked` line per repository, with a non-zero
  count, and nothing else. A failed API call prints `API ERROR`
  rather than nothing, so an outage cannot read as a clean fleet.
  GitHub only runs workflows from `.ya?ml` files directly in
  `.github/workflows`, so the listing skips anything else rather
  than fetching a subdirectory as if it were a file:

  ```
  for r in agent-python client-python client-python-k3s clingwrap \
      divergulent instar library-utilities occystrap sfui shakenfist; do
    files=$(gh api repos/shakenfist/$r/contents/.github/workflows \
        --jq '.[] | select(.type == "file" and (.name | test("\\.ya?ml$"))) | .name') ||
      { echo "$r: API ERROR listing workflows"; continue; }
    n=0
    for f in $files; do
      body=$(gh api repos/shakenfist/$r/contents/.github/workflows/$f --jq .content) ||
        { echo "$r: $f: API ERROR"; continue; }
      n=$((n + 1))
      echo "$body" | base64 -d | grep -qE '^\s*secrets:\s*inherit' && echo "$r: $f"
    done
    echo "$r: checked $n"
  done
  ```

- The 13 issues are closed: client-python#410 and #409, instar#610
  and #600, occystrap#150 and #148, shakenfist#4380,
  agent-python#146, client-python-k3s#79, clingwrap#138,
  divergulent#121, library-utilities#63 and sfui#38. The next
  morning's audit closes any that the pull request bodies did not.
- `gh api repos/shakenfist/instar/contents/.github/workflows/test-drift-fix.yml --jq .content | base64 -d | grep -oE '\$\{\{ *secrets\.[A-Za-z_]+' | sort -u`
  prints exactly `${{ secrets.GITLAB_TESTDATA_TOKEN`. Matching the
  expression rather than `secrets.` keeps a comment that mentions
  `secrets.GITHUB_TOKEN` from reading as a failure.
- The post-merge `pr-fix-tests` triggers in instar, occystrap and
  shakenfist each got past their token-using steps.
- The `Merged` cell records each landing as `<repo> <sha> (#pr)`.

**Back brief gate.** Step 1a is implemented first, and its pull
request is reviewed before 1b to 1e start. This is about the order
of the work, not the order of merging; decision 5 still decides
when each pull request merges. Step 1a is the only step that adds
rather than deletes, and its shape (declared, optional, described)
is the pattern phase 4 documents.

**Pull requests.** Opened 2026-10-02. The `Merged` cell was filled
from this list at close-out on 2026-10-31. The
`reusable-workflow-secrets` spec still describes instar#617 as
proposed; step 4a puts it in the past tense. The development#209 merge
also carried this plan itself, so its diff is wider than step 1f.

| Step | Pull request | Closes | State |
|------|--------------|--------|-------|
| 1a | instar#617 | #600, #610 | Merged as bdc8905 (2026-10-04) |
| 1b | shakenfist#4404 | #4380 | Merged as e4c7726 |
| 1c | occystrap#152 | #148, #150 | Merged as c583d68 |
| 1d | client-python#415 | #409, #410 | Merged as 68f295f |
| 1e | agent-python#148 | #146 | Merged as 4bf7763 |
| 1e | client-python-k3s#83 | #79 | Merged as 03998e0 |
| 1e | clingwrap#141 | #138 | Merged as 125eb20 |
| 1e | divergulent#125 | #121 | Merged as 8a2852e |
| 1e | library-utilities#67 | #63 | Merged as 832d82d |
| 1e | sfui#44 | #38 | Merged as ad0d6a2 |
| 1f | development#209 | -- | Merged as 8df8179 (2026-10-03) |

Step 1f landed differently from its brief, deliberately. Rather than
updating the docstring's dated claim about the fleet's callees, it
removed it: the docstring now says what the check does, and the
spec's Why section is the single record of what callees were found
to read. A docstring survey goes stale every time a callee changes.

In review, divergulent#125 and library-utilities#67 also replaced a
comment that called the deleted `secrets: inherit` necessary, with
the template's comment. The comment was wrong: the callee reads no
secret.

**Close-out (2026-10-31).** Checked against the default branches
through the GitHub API: the definition of done's fleet script prints
one `checked` line per repository and no finding; the 13 issues are
closed; instar's `test-drift-fix.yml` reads exactly
`secrets.GITLAB_TESTDATA_TOKEN`. One item was not met and is
deferred rather than dropped: the post-merge `pr-fix-tests` trigger
was never run in instar, occystrap or shakenfist. Every run of that
workflow in the three repositories since 2026-10-04 was skipped,
because it runs only on a maintainer's comment and nobody has asked
for a fix since. It is the only proof that instar's named token
reaches its "Prepare instar-testdata" step, so it stays in Future
work until the first real trigger, whose "Prepare instar-testdata"
step log answers it.

### Phase 2: make `actions` main require a pull request

This covers everything `actions` serves at `@main`: reusable
workflows and composite actions alike. Composite actions are the
stronger case for it, since they run inside the caller's job.

Planning effort: high. This is what decides what the fleet trusts,
and it touches GitHub settings that this repository cannot test.

**Scope.** In: the operator change to `shakenfist/actions`' one
branch ruleset, 21132503 -- a pull-request rule, required status
checks, and the review-marks App as its only bypass actor (2a); and
the `moving-ref-source-protection` criterion, its specification and
its tests, so that the change cannot quietly regress (2b). Out:
pinning third-party references, which is phase 3; the policy
write-up, which is phase 4; any change to `actions`' `ci.yml`,
including the job names the ruleset will require and the path filter
that skips two of them; comparing the ruleset's bypass list in code,
which waits on #352 giving the audit a read identity that can see
it; narrowing `DEPENDENCIES_TOKEN`, which
`PLAN-bot-bypass-identity.md` owns; and an organisation-level
"require workflows" rule (decision 6).

**What the survey found** (2026-11-11, against this checkout at
bcd768d and the GitHub API with the operator's own credential. The
master plan's text for this phase was corrected where it was stale,
and a later step need not redo that.)

- Nothing of this phase has shipped: `moving-ref-source-protection`
  appears nowhere under `scripts/` or `docs/`.
- The ruleset has not drifted from what the 2a brief recorded on
  2026-11-09. `shakenfist/actions` has exactly one ruleset, 21132503,
  "Protect default branch history", `enforcement: active`, condition
  `~DEFAULT_BRANCH`, and two rules, `deletion` and
  `non_fast_forward`. There is no `pull_request` rule and no
  `required_status_checks` rule. Read with an admin credential the
  `bypass_actors` key is present and `[]`, so the App becomes the
  only entry, as the brief provides. No organisation ruleset reaches
  the branch.
- `actions` already carries the whole workflow half of the review
  mark path. Its `.github/workflows/prune-reviews.yml` has the
  App-token step
  (`actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510`,
  v3.3.0) guarded by `HAS_REVIEW_MARKS_KEY`, and pushes with
  `steps.app-token.outputs.token || github.token`. The `review-marks`
  environment exists, with exactly one custom branch policy, `main`.
  What is missing is the key: that environment holds no secret and
  `actions` holds no repository secret at all, which is why the push
  falls back to `github.token` today.
- So 2a adds the key and nothing else of that shape.
  `REVIEW_MARKS_APP_CLIENT_ID` is an **organisation** variable with
  `visibility: all`, so `actions` already resolves
  `vars.REVIEW_MARKS_APP_CLIENT_ID`; no repository or environment
  copy of it exists in andris, hunkydory, kerbside, ryll or actions.
- The App is as `PLAN-bot-bypass-identity.md` describes it:
  `shakenfist-review-marks`, app id 5538020, installation 176803138,
  permissions exactly `contents: write` and `metadata: read`,
  `repository_selection: selected`. Which repositories that
  installation covers cannot be listed from a personal access token
  -- `user/installations/176803138/repositories` answers 403, "You
  must authenticate with an access token authorized to a GitHub
  App" -- so that `actions` is not among them is read from the
  organisation's GitHub Apps page, or inferred from the environment
  secret listings: andris, hunkydory, kerbside and ryll each hold
  `REVIEW_MARKS_APP_KEY` and `actions` does not. 2a installs through
  that page rather than the API.
- **`ci.yml` has five jobs, not the three open question 2 names.**
  `Check paths` (line 63), `Lint` (121), `Unit tests` (180),
  `gitleaks` (231) and `Automated reviewer` (266). The trigger is
  `on: pull_request` with no `paths:` or `paths-ignore:`, so the
  brief's reasoning holds: the filter is `check_paths`' output,
  consumed by `Lint` and `Unit tests` through
  `needs.check_paths.outputs.code_changed != 'false'`, and a job
  skipped that way reports success to a required check. `Lint`,
  `Unit tests` and `gitleaks` additionally skip a fork pull request.
  `gitleaks` has no `check_paths` dependency, so it already runs on
  every non-fork pull request, a review-only one included -- the
  fork guard above still applies to it. Decision 1 settles the
  contexts.
- **A non-active ruleset is already absent from the effective
  rules.** instar's ruleset 19389569 is `enforcement: disabled`,
  targets `~DEFAULT_BRANCH` and carries a `copilot_code_review`
  rule, and `repos/shakenfist/instar/rules/branches/develop` returns
  only ruleset 20783686's five rules. Of the twelve audited
  repositories that have any ruleset, that is the only one not
  `active`. So `disabled` needs no measurement, and the enforcement
  read in decision 2 is there for `evaluate`, which nothing in the
  fleet uses and which the endpoint's documentation does not settle.
- **The helpers the criterion needs already exist, and the brief
  named the wrong model.** `branch_rules(repo, branch)` and
  `default_branch_rules(repo)` are at
  `scripts/audit/checks/github_config.py:47` and `:68`, and
  `MergeQueueConfig` (line 302) and `ReleaseMergeQueue` (line 321)
  read through them. `ReleaseMergeQueue` is the model: it is the one
  that pairs `default_branch_rules` with an `applies()` returning a
  reason. `DeleteBranchOnMerge` is at line 269 rather than 213, and
  is the wrong shape anyway -- it asks GraphQL for a repository
  setting. The brief was corrected.
- **The repository list belongs in a class constant, not a module
  one.** `ScopeCoverage` (line 710) is the existing pattern for a
  criterion that can only be measured somewhere particular:
  `SCOPE_REPO = 'development'` as a documented class attribute, with
  the reason returned from `applies()`. The brief said "a module
  constant"; corrected.
- Nothing in the audit reads `required_status_checks` today, so this
  criterion is the first. `BypassActorIdentity` (line 521) does read
  the default branch's `pull_request` rules, at line 580, but only
  on the path where the `review-marks` environment is absent.
  `actions` has that environment, so it is already measured on the
  other path and 2a does not change its verdict.
- **Adding a criterion is a documented six-file procedure**:
  `docs/consistency-audits.md`, "Adding a criterion" (line 465),
  which `PUSH-AUDIT.md` and `AGENTS.md` link to rather than copy.
  The 2b brief now cites it instead of carrying its own list. Two of
  its rules the brief had not carried: the applicability test goes
  in `applies(repo)` rather than at the top of `run()`, because the
  scheduler asks the cheap question first; and a specification page
  must carry no `consistency-audit` marker block, which
  `test_audit_update_docs.py` fails on.
- **The pinned tool-failure list does not change.**
  `test_an_empty_directory_is_a_tool_failure_exactly_where_expected`
  (`scripts/tests/test_metadata.py:656`) pins an exact list of the
  criteria that cannot reach a verdict without git or GitHub. Its
  fixture repository is named `testrepo` (`repo()`, line 540), so a
  criterion applying only to `actions` skips before any API call and
  does not join the list -- which is why `scope-coverage` is absent
  from it too.
- **The audit's read token can read a ruleset object; only
  `bypass_actors` is withheld.** `PLAN-bot-bypass-identity.md`'s
  phase 1 record has `AUDIT_READ_TOKEN` answering `has
  bypass_actors: False` for
  `repos/shakenfist/hunkydory/rulesets/23805020` on 2026-10-31 under
  the security manager role, run 41130157778. It answered, which is
  what decision 2 needs: `enforcement` is readable. Taken from that
  record rather than measured again, as the brief requires.
- **The `DEPENDENCIES_TOKEN` residual is narrower than open question
  1 implies.** An organisation-wide code search puts the token in
  six workflows in three repositories: shakenfist
  (`code-formatting.yml`, `functional-tests.yml`,
  `pin-indirect-dependencies.yml`, `sync-external-docs.yml`),
  client-python (`code-formatting.yml`) and kerbside
  (`pin-indirect-dependencies.yml`). Their triggers are
  `pull_request`, `workflow_dispatch`, `schedule` and `merge_group`;
  none uses `pull_request_target`, `issue_comment` or
  `workflow_run`, and a fork's `pull_request` run is handed no
  secrets. So the token reaches a job only for someone who can
  already push a branch to one of those three repositories.
  Recorded here so that 2a does not survey it again.
- **#153 is already closed**, on 2026-10-03, the day
  development#209 landed phase 1. Phase 4's brief still said to
  close it from its pull request body, and the Related issues list
  still called it the issue this plan closes; both were corrected.
  What phase 4 writes is unaffected.
- #352, the audit's read identity, is open, so 2b's shape is the
  fallback one this section describes.

Everything else the section claimed held: `actions` does serve
composite actions at `@main`, the key must be an environment secret
rather than a repository or organisation one, and a ruleset bypass
cannot be narrowed to a path.

**Decisions.**

1. **The required contexts are `Check paths`, `Lint`, `Unit tests`
   and `gitleaks`, and `Automated reviewer` is not one.** The first
   four either report or skip for every pull request this repository
   will see, and a skipped job satisfies a required check, so none
   of them wedges a review-only or a fork pull request. `gitleaks`
   is included although open question 2 did not name it: it is the
   secret scan, it already runs on every pull request, so requiring
   it costs nothing and not requiring it would be the odd choice.
   The requirement protects the first-party case only: `gitleaks`
   carries the same fork guard as `Lint` and `Unit tests`, so on a
   fork pull request it skips and the skip satisfies the required
   check. `secret-scan.yml` and the organisation's push protection
   are what cover forks, and a required check that could not run on
   one would wedge every fork pull request. Taken together with the
   fork guards on `Lint` and `Unit tests`, this means only `Check
   paths` actually reports on a fork pull request and the other
   three are satisfied by skipping: the required checks are a
   first-party gate, and phase 4 says so rather than describing them
   as the enforcement point for every contributor.
   `Automated reviewer` is excluded deliberately. It `needs` the
   other three, it writes to the pull request it is reviewing, and
   it is the one lane whose outcome depends on a service outside
   this fleet, so requiring it would let an outage there hold every
   merge. Required approvals stay at zero, per open question 2: with
   one maintainer a count of one cannot be met by the author.
2. **The criterion measures the two rules from the effective-rules
   endpoint, and `enforcement` from each ruleset that contributed
   them.** `repos/{org}/{repo}/rules/branches/{default}` is the only
   endpoint that resolves organisation and repository rulesets
   together, and the survey measured that it already excludes a
   `disabled` ruleset. It does not settle `evaluate`, where a rule
   is reported on but does not block, so the check collects the
   `ruleset_id` of each rule it matched, reads
   `repos/{org}/{repo}/rulesets/{id}`, and fails unless every one is
   `active`. The read token can do that (survey). It also fails when
   the `required_status_checks` rule's context list is empty, which
   is the shape an operator leaves behind by removing the contexts
   but not the rule: it satisfies "the rule exists" while enforcing
   nothing. Checking the contexts against `ci.yml`'s job names needs
   the workflow read and stays in Future work; checking that the
   list is not empty needs nothing. A ruleset whose
   `ruleset_source_type` is `Organization` -- the field is spelled
   that way on the effective-rules response, beside `ruleset_source`
   and `ruleset_id`, not `source_type` -- is read from
   `orgs/{org}/rulesets/{id}`. Under today's read token that read
   does not answer (decision 6's measurement), so an
   organisation-sourced ruleset is a `ToolFailure` rather than a
   verdict until #352 lands. Nothing in the fleet reaches that path:
   the survey measured that no organisation ruleset contributes a
   rule to `actions`' default branch.

   One thing the endpoint does *not* do, measured because the review
   of this phase asserted the opposite: it does not hide rules the
   calling credential can bypass. On instar's ruleset 20783686 the
   operator's own credential reads `current_user_can_bypass:
   "always"`, through the `sf-can-skip-merge-queue` team, and
   `repos/shakenfist/instar/rules/branches/develop` still returns
   all five of its rules including `pull_request` and
   `required_status_checks` (2026-11-11). So the criterion's fail
   verdict does not depend on the audit identity holding no bypass,
   and needs no fail-closed path for that case. Nothing in this
   repository ever recorded such a behaviour; the claim was
   checked rather than taken.
3. **The repositories the fleet consumes at a moving ref are a class
   constant, `MOVING_REF_REPOS = ('actions',)`, with the reason
   returned from `applies()`.** Follows `ScopeCoverage.SCOPE_REPO`.
   Everywhere else the criterion reports `not_applicable` naming the
   repository, which is what keeps it from paying for an API call on
   23 repositories that cannot fail it.
4. **The bypass list stays confirmed by a reviewer rather than
   measured**, and this rests on a measurement rather than an
   assumption: the audit's read token answers the ruleset endpoint
   but is not given `bypass_actors`, measured twice in
   `PLAN-bot-bypass-identity.md`'s phase 1, the second time under
   the security manager role. The token is not widened to make the
   check fit. The documented set -- one actor, the
   `shakenfist-review-marks` App, app id 5538020 -- goes in the
   specification's "Required, but confirmed by a reviewer" half, and
   becomes code when #352 lands, at which point a missing key is an
   error and never an empty set.
5. **2b lands in its own pull request, after 2a has been applied and
   proved.** The criterion fails `actions` until the ruleset change
   is in place, and it applies to no other repository, so landing it
   second means the first scheduled audit after it reports one more
   compliant row and files no issue. That is the inverse of phase
   3's problem (its decision 4), and it is cheap here because only
   one repository is measured.
6. **An organisation "require workflows" rule is out of scope**, and
   recorded as future work. Open question 2 names it as the stronger
   form, and it is one: a required check whose workflow the pull
   request can edit is a check the pull request can make pass. But
   it wants a ruleset on a protected ref to source the workflow
   from, which is what this phase creates rather than something it
   can assume; the audit's token could not read the organisation
   ruleset endpoint on 2026-10-29
   (`PLAN-bot-bypass-identity.md`, step 1a), so no criterion could
   measure it yet; and the residual it would close is bounded by the
   edit being visible in a pull request, which is the gain open
   question 2 claims. Phase 4 states that limit rather than implying
   more.
7. **The order inside 2a is install, key, bypass, then rule.** The
   brief already says so; it is restated as a decision because
   getting it wrong is silent. `actions` holds no key today, so the
   moment the pull-request rule is active its `prune-reviews` push
   with `github.token` is refused, and nothing outside `actions`
   reports that: `bypass-actor-identity` measures the environment,
   not the push.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Operator step, done by the management session with the operator, not by a sub-agent. Four changes, in this order (decision 7), each read back before the next is made. (1) Install `shakenfist-review-marks` (app id 5538020, installation 176803138) on `shakenfist/actions` from the organisation's GitHub Apps settings page, since a personal access token can neither list nor change an installation's repositories (survey). (2) `gh secret set REVIEW_MARKS_APP_KEY --repo shakenfist/actions --env review-marks` from the key file -- never without `--env`, never at organisation level. The `review-marks` environment and its `main`-only custom branch policy already exist and are not recreated, and `REVIEW_MARKS_APP_CLIENT_ID` is already an organisation variable visible to all repositories, so nothing is added for it. Record `repos/shakenfist/actions/environments/review-marks/secrets` and `gh secret list --repo shakenfist/actions`, which must name no repository-level copy. Adding a repository is a key rotation and not a new key for that repository alone: `docs/code-review-tracking.md` step 7.3 says to generate one key, set it in `actions`' environment and again in every environment that already holds the key, read each back, then delete the old key so the App keeps exactly one. This brief did not say so and the step did not do it; the record below says what the state is instead. (3) Add the App as the only bypass actor on ruleset 21132503, with the GET-then-PUT shape under "Template" in `docs/audits/merge-queue-config.md`, appending `{"actor_id": 5538020, "actor_type": "Integration", "bypass_mode": "always"}`. Assert `has("bypass_actors")` on the GET first and stop if it is false: that recipe's `(.bypass_actors // [])` is right for a caller who can see the key and silently destructive for one who cannot (risk 1). The key was present and `[]` on 2026-11-11; if anything else is in it by then, add the App beside it and report it rather than removing it -- taking a team bypass away would be `PLAN-bot-bypass-identity.md`'s open question 3 rather than this plan's, whose open question 3 is about sha-pinning exemptions. In the event the GET found the list empty, so this did not arise. (4) Only then add the `pull_request` rule, with `required_approving_review_count: 0`, and the `required_status_checks` rule with exactly the four contexts from decision 1 and the GitHub Actions `integration_id`, preserving the existing `deletion` and `non_fast_forward` rules and the `~DEFAULT_BRANCH` condition. Record the whole ruleset JSON in this phase. Then prove it, recording each: a direct push of a trivial commit to main from the operator's own credential is refused; the next `prune-reviews` run on main has `Mint the push token` running rather than skipped, its push attributed to `shakenfist-review-marks[bot]` in `repos/shakenfist/actions/rulesets/rule-suites?time_period=day&rule_suite_result=bypass` (the way `PLAN-bot-bypass-identity.md`'s phase 3 record proves it for kerbside), and `repos/shakenfist/actions/deployments?environment=review-marks` empty; and a throwaway review-only pull request reaches a mergeable state with all four contexts reported success or skipped, after which it and its branch are deleted. Do not measure whether the audit token sees `bypass_actors`: it does not, and 2b takes the fallback shape (decision 4). The `DEPENDENCIES_TOKEN` scope survey the brief used to ask for is done and recorded above; do not repeat it. |
| 2b | high | opus | worktree | Add a `MovingRefSourceProtection` class to `scripts/audit/checks/github_config.py`: id `moving-ref-source-protection`, `issue_title` `Moving ref source protection`, `spec` `docs/audits/moving-ref-source-protection.md`, `template` None, no `column`. Follow the procedure in `docs/consistency-audits.md`, under "Adding a criterion" -- it is the one list of what a criterion touches, so read it rather than working from a list in this brief. Seven files, not the six that section is usually quoted as: this criterion reaches the GitHub API, so its id also goes in `NETWORK_CHECKS` in `scripts/audit_snapshot.py`. That matters more than a red test. Without the id, `audit-raised.py` reads a tool failure from this check as a check that broke rather than as a network call that did not answer, and fails the audit leg -- holding back the whole fleet's compliance page and filing an `audit-failure` issue. It is also exactly the path decision 2 routes an organisation-sourced ruleset down. `NetworkCheckListTest` re-derives the list from the checks and fails until the id is there, and `PLAN-TEMPLATE.md`'s success criteria still say "five of its files", so neither the template nor a remembered file count is a safe guide. Model the check on `ReleaseMergeQueue` (`github_config.py:321`) and not on `DeleteBranchOnMerge`: read through the module's own `default_branch_rules(repo)` (line 68), and put the applicability test in `applies(repo)`, returning a reason that names the repository. The repositories are a class constant, `MOVING_REF_REPOS = ('actions',)`, documented with a `#:` comment (decision 3). Fail unless both a `pull_request` rule and a `required_status_checks` rule are in the effective rules; then, for each distinct `ruleset_id` those two rules came from, read `repos/{org}/{repo}/rulesets/{id}` and fail unless every one is `enforcement: active` (decision 2), reading a ruleset whose `ruleset_source_type` is `Organization` (that spelling, beside `ruleset_source` and `ruleset_id`) from `orgs/{org}/rulesets/{id}`, which under today's read token does not answer and so is a `ToolFailure`, as decision 2 now says. GitHub not answering is a `ToolFailure`, never a verdict. Write no `bypass_actors` comparison and no test of one: the audit's read token is not given the key (decision 4), so the one-actor documented set goes in the specification's "Required, but confirmed by a reviewer" half, with the measurement that says why. Tests in `scripts/tests/test_github_config.py`, with `CheckTestCase` (in `scripts/tests/base.py`) and a stubbed client: pass; fail on a missing `pull_request` rule; fail on a missing `required_status_checks` rule; fail on a contributing ruleset in `enforcement: evaluate`; fail on a `required_status_checks` rule whose context list is empty, with the message naming decision 1's four contexts so the issue body says what to restore; not-applicable for a repository outside `MOVING_REF_REPOS`, asserting the reason names the repository, since that reason is what an operator reads off the compliance page for 23 of the 24; and three tool-failure cases with `assert_tool_failure()` -- one where the `rules/branches` read does not answer, one where the per-ruleset `rulesets/{id}` read does not answer, and one where a matched rule's `ruleset_source_type` is `Organization` while the repository-level read answers normally, which is a tool failure by construction rather than from a failed read, and whose message should name the organisation endpoint and #352. The second path is new to the audit, so no existing test covers its shape, and `AGENTS.md` treats the tool-failure distinction as the one a criterion must never get wrong: a fail files an issue for our own outage and a not_applicable closes one. Cite symbols rather than line numbers when reading this brief -- the citations above were corrected once already between planning and execution. In `scripts/tests/test_metadata.py` add the criterion's lines to `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` only, and leave `test_an_empty_directory_is_a_tool_failure_exactly_where_expected`'s list alone: its fixture repository is `testrepo`, so this check skips before any API call (survey). The specification follows the structure in `docs/audits/README.md`, links `compliance.md#moving-ref-source-protection` under `## Projects`, carries no `consistency-audit` marker block, and cites this plan's Situation for the landing counts; add its line to the criterion index in `docs/audits/README.md`. Open the pull request only once 2a is applied and proved, so that the criterion passes on the day it lands (decision 5), and say in the commit what it measured against `actions`. |

**Risks and mitigations.**

- **A ruleset PUT built from a read that could not see
  `bypass_actors` empties the bypass list silently.** The
  GET-then-PUT recipe in `docs/audits/merge-queue-config.md`
  defaults the field with `(.bypass_actors // [])`, which is right
  for a caller who can see it and destructive for one who cannot.
  The operator checks `has("bypass_actors")` on the GET before
  editing and re-reads the list afterwards; their credential saw the
  key on 2026-11-11.
- **A required context that never reports wedges every pull
  request.** The operator opens a throwaway review-only pull request
  before trusting the rule, and confirms each of the four contexts
  resolves to success or skipped and that the merge button is
  offered. If one does not report, the rule comes off again rather
  than being left to wedge the next review-only pull request. The
  `pull_request` rule's
  `require_extra_approval_for_unattributed_changes` has the same
  symptom from a different cause -- it asks for one approving review
  on a pull request carrying a commit GitHub cannot attribute to an
  account. That is only unobtainable on a pull request the sole
  maintainer authored; on an automation's pull request the
  maintainer can approve it, and the fleet's bot commits are
  attributed anyway (`bot@shakenfist.com` resolves to
  `shakenfist-bot`). It is left `true` because every other ruleset
  in the fleet
  pairs it with zero required approvals (measured on andris,
  hunkydory, instar, kerbside and ryll, 2026-11-11) and none has
  wedged; the escape, if one ever does, is an admin edit to the
  ruleset, since the operator holds no bypass.
- **Renaming a job in `ci.yml` breaks the required context, and this
  criterion will not notice.** It measures that a
  `required_status_checks` rule exists, not that its contexts match
  the workflow's job names. Recorded as future work. The symptom is
  loud -- every pull request stops offering a merge -- and the fix is
  one ruleset edit.
- **Installing the App on `actions` widens what one key reaches**,
  from four repositories to five. The mitigations are unchanged from
  `PLAN-bot-bypass-identity.md`: the permission is `contents: write`
  only, the key is an environment secret whose branch policy admits
  only `main`, and the token is minted per run and revoked when the
  job ends. 2a adds no repository-level or organisation-level copy,
  and the definition of done reads the listings to show it.

  What is genuinely different, and was not written down until the
  review of this phase asked: the widening extends an accepted
  residual to the one repository this plan exists to protect.
  `docs/audits/bypass-actor-identity.md` records that the
  runner-controller App `shakenfist-k3s-ci-arc` holds
  `administration: write` on hunkydory, so whoever holds that App's
  key can widen hunkydory's `review-marks` branch policy, read the
  review-marks key from a branch workflow, and mint push tokens for
  every repository the App is installed on. Until 2a that set was
  four product repositories; it now includes `actions`, whose
  composite actions run inside every caller's job at `@main`, so
  the chain ends in a bypass of the ruleset 2a just created. It is
  accepted knowingly rather than made a prerequisite, because
  narrowing another App's permission is not this plan's work and
  blocking on it would leave `prune-reviews` unable to land on
  `actions` at all. The residual is recorded in the criterion's
  specification, phase 4 states it rather than implying the bypass
  is bounded by this phase alone, and narrowing the
  runner-controller App is Future work.

**Definition of done.**

- `repos/shakenfist/actions/rules/branches/main` returns a
  `pull_request` rule and a `required_status_checks` rule, and that
  rule's contexts are exactly `Check paths`, `Lint`, `Unit tests`
  and `gitleaks`.
- `repos/shakenfist/actions/rulesets/21132503` reads `enforcement:
  active`, and its `bypass_actors` holds exactly one entry:
  `actor_id` 5538020, `actor_type` `Integration`.
- `repos/shakenfist/actions/environments/review-marks/secrets` names
  `REVIEW_MARKS_APP_KEY`, and `gh secret list --repo
  shakenfist/actions` names no copy of it.
- A direct push of a trivial commit to `actions` main from the
  operator's own credential is refused, and the refusal is recorded
  here.
- The first `prune-reviews` run on `actions` main after 2a shows
  `Mint the push token` running rather than skipped;
  `rulesets/rule-suites?time_period=day&rule_suite_result=bypass`
  names `shakenfist-review-marks[bot]` as the actor of its push; and
  `deployments?environment=review-marks` is empty. The first and
  third are met; the second is **not met as of 2026-11-11** -- the
  run had nothing to push, so the listing is empty. Deferred to
  Future work; see "Not proved: the push attribution".
- A review-only pull request opened on `actions` reaches a mergeable
  state with all four required contexts reported.
- `python3 -m unittest discover -s scripts/tests -t scripts` passes,
  and `scripts/tests/test_metadata.py` passes without its pinned
  tool-failure list having been edited.
- The first scheduled consistency audit after 2b reports
  `moving-ref-source-protection` compliant on `actions` and
  not_applicable everywhere else, files no issue, and the commit
  that landed 2b states that prediction, following "Before and after
  a change to a check" in `docs/consistency-audits.md`.
- `docs/audits/moving-ref-source-protection.md` exists, is linked
  from `docs/audits/README.md`, carries no `consistency-audit`
  marker block, and its reviewer-confirmed half names the one bypass
  actor and says why it is not measured.
- #389 is closed: the App holds exactly one private key again,
  confirmed from its settings page. Added after the fact, because
  2a broke that property rather than finding it broken. It gates
  this phase's close-out rather than 2b, since 2b measures the
  ruleset and not the key, and until it is done the fleet cannot
  revoke a leaked key without breaking a working repository.
  Nothing in the API can measure it, so the reading is a person's.

**Back brief gate.** 2a changes a live setting the whole fleet
depends on, by hand, in an order whose mistakes are silent. Before
touching GitHub, back brief the four changes, their order, and what
will be read to prove each one. 2b needs no gate.

**Back brief outcome.** Four answers, all recorded here because
later steps read them. The browser half of 2a -- installing the App
and generating a key -- is the operator's, because this session's
token can neither list nor change an installation's repositories,
and the key is handled only by them. The key is a freshly generated
one rather than a reuse: the original was shredded in
`PLAN-bot-bypass-identity.md` step 1b, which the brief did not
account for. Decision 5's two pull requests stand as written, with
2a's record first and 2b off updated `main`, although the ruleset
change itself never passes through a pull request -- what the first
one lands is the record of it. And this session may push and open
both pull requests for this phase.

**Step 2a record (2026-11-11, UTC throughout; GitHub renders the
ruleset's own `updated_at` in +11:00).** The four changes were made
in decision 7's order, each read back before the next. The operator
made changes 1 and 2 in the browser and with `gh secret set`; the
management session made changes 3 and 4 through the API.

1. **Installed** `shakenfist-review-marks` on `shakenfist/actions`
   from the organisation's GitHub Apps page, taking the installation
   from four repositories to five. This is the one change nothing in
   this session can read back directly: a personal access token
   cannot list an installation's repositories (survey), and
   `user/installations/176803138/repositories` answers 403 to this
   session's token. It is attested twice instead, both indirectly:
   GitHub accepted app id 5538020 as an `Integration` bypass actor
   in change 3, and the `prune-reviews` run below minted a token
   from the key, which an App that is not installed cannot do.
2. **Set the key** as an environment secret.
   `repos/shakenfist/actions/environments/review-marks/secrets`
   names `REVIEW_MARKS_APP_KEY` with `created_at` and `updated_at`
   both `2026-11-11T18:48:11Z` -- equal, so this is a new secret
   rather than an overwrite, which is the reading that confirms
   `actions` held no key before (decision 7). `gh secret list --repo
   shakenfist/actions` prints nothing at all: there is no
   repository-level copy of this or any other secret.
   `REVIEW_MARKS_APP_CLIENT_ID` remains an organisation variable
   with `ALL` visibility, unchanged since 2026-10-30, and nothing
   was added for it. The operator shredded the key file afterwards.
   `repos/shakenfist/actions/environments/review-marks` still
   carries exactly one protection rule, `branch_policy`, whose one
   policy is the branch `main`; no reviewers and no wait timer were
   added, so a run on `main` is admitted without an approval and a
   run on any other ref is not admitted at all.
3. **Added the bypass actor.** The GET answered `has
   bypass_actors: true` with the list `[]`, so risk 1's guard passed
   and nothing was removed; the App was appended rather than
   substituted. The script that made the change refuses to continue
   when that assertion fails, which is the mitigation risk 1 asks
   for rather than a promise of care.
4. **Added the two rules**, preserving `deletion`,
   `non_fast_forward` and the `~DEFAULT_BRANCH` condition. The
   parameter sets were copied from instar's live ruleset rather than
   composed from the API reference, which is also where
   `integration_id: 15368` for GitHub Actions comes from. instar
   requires `Can enqueue` and `Can merge` because it has a merge
   queue; `actions` has none, so the four contexts here are job
   names.

**Departure from the documented key procedure.** Change 2 did half
of what `docs/code-review-tracking.md` step 7.3 requires. That step
exists because the App is meant to hold exactly one private key, the
one every repository's environment carries, so adding a repository
is a rotation: generate a key, set it in the new environment *and in
every environment that already holds the key*, read each back, then
delete the old key. Only the new environment was set. The App should
now hold two valid private keys -- the older one, carried by andris,
hunkydory, kerbside and ryll, and this one, carried only by
`actions` -- so the property `PLAN-bot-bypass-identity.md` phase 1
closed on, "the App's page lists exactly one private key", is
currently false. What the operator shredded was the downloaded file,
which is not the same as deleting the old key.

This is a hygiene regression rather than an exposure: both keys are
environment secrets behind a default-branch-only policy, both mint
per-run tokens that are revoked at job end, and the App's
permissions are unchanged. What is lost is the ability to invalidate
a leaked key by deleting it, because deleting either one now breaks
a working repository. Nothing measures it, and nothing can through
the API: `bypass-actor-identity` reads the environment and its
branch policy, and an App's key list is visible only on its settings
page. Completing the rotation is one pass over five repositories and
is tracked as #389, not deferred to a later phase of this plan,
because it is fleet state rather than plan state.

The whole ruleset as read back afterwards, with `_links` removed:

```json
{
  "id": 21132503,
  "name": "Protect default branch history",
  "target": "branch",
  "source_type": "Repository",
  "source": "shakenfist/actions",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "exclude": [],
      "include": [
        "~DEFAULT_BRANCH"
      ]
    }
  },
  "rules": [
    {
      "type": "deletion"
    },
    {
      "type": "non_fast_forward"
    },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "required_reviewers": [],
        "require_code_owner_review": false,
        "dismissal_restriction": {
          "enabled": false,
          "allowed_actors": []
        },
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "require_extra_approval_for_unattributed_changes": true,
        "allowed_merge_methods": [
          "merge",
          "squash",
          "rebase"
        ]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          {
            "context": "Check paths",
            "integration_id": 15368
          },
          {
            "context": "Lint",
            "integration_id": 15368
          },
          {
            "context": "Unit tests",
            "integration_id": 15368
          },
          {
            "context": "gitleaks",
            "integration_id": 15368
          }
        ]
      }
    }
  ],
  "node_id": "RRS_lACqUmVwb3NpdG9yec41biqPzgFCdNc",
  "created_at": "2026-08-21T17:25:43.123+10:00",
  "updated_at": "2026-11-12T05:49:25.598+11:00",
  "bypass_actors": [
    {
      "actor_id": 5538020,
      "actor_type": "Integration",
      "bypass_mode": "always"
    }
  ],
  "current_user_can_bypass": "never"
}
```

`current_user_can_bypass: "never"` is worth noting: the operator's
own credential has no bypass, so the only identity that can push to
`actions` main is the App's per-run token.
`repos/shakenfist/actions/rules/branches/main` reports the four
rules, all sourced from ruleset 21132503, and its
`required_status_checks` contexts are exactly `Check paths`, `Lint`,
`Unit tests` and `gitleaks`.

One parameter in that JSON deserves its own note, because it reads
against decision 1. `require_extra_approval_for_unattributed_changes`
is `true` while `required_approving_review_count` is `0`, and the
first asks for an approving review on a pull request carrying a
commit GitHub cannot attribute to an account -- the very approval
decision 1 says one maintainer cannot obtain. It came in with the
parameter set copied from instar. It is kept rather than cleared
because it is the fleet's settled shape, not an instar quirk: the
`pull_request` rule on andris, hunkydory, instar, kerbside and ryll
pairs the same `true` with the same zero count, measured on
2026-11-11, and none of those repositories has wedged. Clearing it
on `actions` alone would make it the one repository configured
differently.

The case it can actually wedge is narrower than "any unattributed
commit", and the narrowing is worth having written down. For a pull
request the sole maintainer did not author, the maintainer is not
the author and can supply the approval, so the rule costs a click
rather than wedging: that covers Renovate's, which are authored by
`shakenfist-bot`. For a maintainer-authored pull request the
approval cannot be obtained at all. And the commits the fleet's
automations make are attributed in any case --
`bot@shakenfist.com` resolves to the `shakenfist-bot` account, so
a `prune-reviews` or Renovate commit is not an unattributed change
to begin with. What remains is one shape: a maintainer-authored
branch carrying a commit whose author email is linked to no GitHub
account, which is a thing an agent branch with a misconfigured
`user.email` can produce. The escape is an admin edit to the
ruleset, since `current_user_can_bypass` is `never`. Risk 2 records
it beside the other cause of that symptom.

**Phase 1's `Merged` cells are merge commits.** The review asked
for this because `templates/shared-blocks/plan-push-audit-phase.md`
says a single commit is only ever enough when it is a merge commit,
and phase 6 reads that cell as its audit range. All eleven answer 2
to `gh api repos/shakenfist/<repo>/commits/<sha> --jq '.parents |
length'`, checked on 2026-11-11, so the cell is a usable range and
phase 6 need not re-derive it.

**What 2a proved, and the one thing it did not.** Each proof is
recorded with the reading that establishes it rather than a verdict.

- **A direct push to main is refused.** A trivial empty commit pushed
  from the operator's own credential was declined with `GH013`,
  naming both new rules: "Changes must be made through a pull
  request" and "4 of 4 required status checks are expected".
  `rulesets/rule-suites?time_period=day` records it as
  `actor_name: mikalstill`, `refs/heads/main`, `result: fail`.
- **A review-only pull request still merges.** actions#250 added one
  file under `docs/`, which the `Check paths` filter excludes from
  `code`. `Check paths` and `gitleaks` succeeded; `Lint` and `Unit
  tests` skipped, as their `needs.check_paths.outputs.code_changed
  != 'false'` condition intends; `Automated reviewer` skipped
  because its dependencies did. The pull request then read
  `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`. That is
  decision 1's load-bearing premise -- that a skipped job satisfies
  a required check -- measured rather than assumed, and it is the
  reason `Lint` and `Unit tests` can be required at all. The pull
  request was closed unmerged and its branch deleted; `actions` main
  is still c057113, unchanged by anything in 2a.
- **The App's token mints, which is also the only direct evidence
  the App is installed.** Run 43081577539, `workflow_dispatch` on
  `main`: `HAS_REVIEW_MARKS_KEY: true`, so the environment secret
  reached the job; `Mint the push token` ran and succeeded rather
  than skipping, reporting "Creating token for this repository
  (shakenfist/actions)"; and the post-job step logged "Token
  revoked". An App that is not installed on a repository cannot mint
  a token for it, so this closes change 1, which no API reading
  available to this session could confirm directly.
- **`deployments?environment=review-marks` is empty** (length 0).
  Reading an environment secret does not create a deployment record,
  so the environment's branch policy is acting as an access boundary
  and nothing else.
- **The job guard on other branches still holds.** Pushing the
  throwaway branch started run 43081548196, which skipped: the
  workflow triggers on any branch push, and its default-branch guard
  is what stops it running there.

**What else the ruleset now binds.** Decision 7 reasons about one
workflow, `prune-reviews`, and the question was not asked of the
others until the review of this phase asked it. Two separate
questions turn out to be worth keeping apart, because the ruleset
binds pushes to `main` and merges into `main` by different rules.

At push time, `prune-reviews` is the only automation subject to it.
`actions` contains four pushes, but the other three --
`tools/ci_sync_docs.sh`, `tools/ci_code_formatting.sh` and
`.github/workflows/export-repo-config.yml` -- do not run in
`actions` at all. `export-repo-config.yml` is `workflow_call:` only
and nothing in `actions` calls it (`canary.yml` names the file in a
path filter, which is not a call), and the two scripts are invoked
by reusable workflows that run in caller repositories. They push
and open pull requests there, against those repositories' default
branches, not this one.

At merge time the rules bind every pull request into `main`,
automated or not. In `actions` that means Renovate, which runs
hourly from `renovate.yml` and opens its pull requests as
`shakenfist-bot`. Nothing in the repository merges its own pull
request: there is no `gh pr merge` and no auto-merge anywhere in
`.github/workflows/` or `tools/`, and the 15 most recent pull
requests were all merged by `mikalstill`. So the new rules add "CI
must be green first" to Renovate's pull requests rather than
creating a merge nothing can perform. Recorded so the next reader
does not re-derive it.

**The export follow-up cannot be performed on `actions`.**
`docs/audits/merge-queue-config.md`, whose recipe shape 2a followed,
ends by saying to trigger the repository's `export-repo-config`
workflow so the change is captured in `.github/exported-config/`.
There is nothing to trigger: `actions` has no
`.github/exported-config/` directory and nothing in it calls the
reusable workflow, which lives there as the template the rest of the
fleet consumes. So this ruleset change is recorded in this plan and
in GitHub, and nowhere in the repository it applies to. Whether
`actions` should export its own configuration like its callers do is
a question for the `export-repo-config` criterion rather than this
phase; it is in Future work.

**Not proved: the push attribution.** The definition of done also
asks that
`rulesets/rule-suites?time_period=day&rule_suite_result=bypass` name
`shakenfist-review-marks[bot]` as the actor of the push. That
listing is empty, because the run had nothing to push: it logged
"review-import: imported 0 file(s), removed 0 import(s)" and "No
review marks to prune or import", so it never reached a push and no
rule suite was evaluated. The bypass is therefore exercised but not
yet observed. This is the same gap `PLAN-bot-bypass-identity.md`
step 2b recorded for kerbside, and it takes the same answer: wait
for a run with real work rather than manufacture one, since
manufacturing it would mean landing review-mark data through a pull
request for the sole purpose of giving the next run something to
delete. It is listed in Future work, and the first prune run that
does prune answers it. The bypass itself is not in doubt -- without
it that push would be refused exactly as the operator's was -- but
the reading that would show it has not been taken.

### Phase 3: pin third-party actions to a sha

Planning effort: high for 3a, which decides the rollout. Medium for
the rest, which follows `renovate.md`'s pre-commit manager
requirement as a worked example.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | worktree | Probe in this repository. Add `"extends": ["helpers:pinGitHubActionDigests"]` to `renovate.json`. The existing `managerFilePatterns` already cover `templates/`. Let one Renovate run produce its pinning pull request, and record: the comment format it writes; whether `pypa/gh-action-pypi-publish@release/v1` (a branch) pins cleanly; whether `actionlint` and the template byte-identity checks still pass; whether the github-actions manager also pins a composite `action.yml` (its default file patterns include one, so confirm it against a fixture rather than assuming); and the digest pull request count over the following two weeks. Answer open questions 4 and 5 in the plan, with numbers. |
| 3b | medium | sonnet | none | Add the same `extends` to `templates/renovate/renovate.json`, and say why in `templates/renovate/README.md`. Pin `renovatebot/github-action` first: it runs with `DEPENDENCIES_TOKEN` and write access to its repository, so it is the third-party action most worth pinning. Pin every other third-party `uses:` under `templates/` to a sha with a `# vX.Y.Z` comment, keeping template copies and their deployed twins here byte-identical. Grep `scripts/audit/` for any check that matches a `uses:` line by tag (for example `@v`), and update it to accept a pinned form. |
| 3c | medium | sonnet | worktree | Add the criterion `third-party-action-pinning` to `scripts/audit/checks/ci_workflows.py`, beside `ReusableWorkflowSecrets` (line 2162). It is measured: every remote `uses:` whose owner is not `shakenfist` ends in a 40-hex sha. It is confirmed by a reviewer: the trailing version comment, and the Renovate preset being on. It also scans every `action.yml` and `action.yaml` in the repository, wherever it sits, for `uses:` under `runs.steps`: a composite action's third-party step runs inside each caller's job, and the existing helpers only read `.github/workflows/`. Reuse the existing workflow-parsing helpers in that file rather than adding a YAML dependency. Give it the five criterion files as in 2b, plus tests for a tag ref, a sha ref, a branch ref, a commented-out line, a `docker://` ref (out of scope, not a finding), a local `./` ref (not a finding), a composite `action.yml` with an unpinned third-party step (a finding), and a third-party reusable workflow (`owner/repo/.github/workflows/x.yml@v1`, a finding). Run it against fresh clones of the fleet. The commit message states how many repositories it newly fails, including this one, which must already pass after 3b. |

**Coordination with `PLAN-renovate-cadence.md`.** That plan moves
the fleet's Renovate policy into one shared preset and runs Renovate
once for the whole fleet from this repository. If its phase 3 has
landed when this phase starts, step 3b adds
`helpers:pinGitHubActionDigests` to the preset once rather than to
`templates/renovate/renovate.json` and every copy of it. Its monthly
CI-tooling group (all `github-actions` and `pre-commit` updates,
digests included) is the grouping rule that open question 4's
default anticipates. Once its phase 3 has landed,
`renovatebot/github-action` appears only in this repository's
central workflow, so 3b pins it there. Land that plan's phase 3
before this phase's 3b: its phase 1 only publishes the preset, and
no repository but this one extends it until its phase 3 sweep, so
otherwise every pin arrives as a separate pull request in every
repository. Step 3a is not held, but it is affected: if that plan's
phase 1 has landed when 3a runs, this repository already extends the
preset, so its digest updates fall into the monthly CI-tooling group
and a two-week window can see none at all. In that case run 3a's
count across the first of a month and report it as grouped pull
requests, which is the number that matters once the preset governs
the fleet.

### Phase 4: record the policy

Planning effort: medium. It documents a policy that phases 2 and 3
have already decided.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | In `docs/audits/reusable-workflow-secrets.md`, replace "What this does not cover" with the settled policy: first-party refs move, and the `moving-ref-source-protection` criterion is what makes that acceptable; third-party refs are pinned, under `third-party-action-pinning`. Include the upper bound of 14 pull requests a day this avoids, what a moving ref can reach (Situation: the runner's filesystem, and the caller's job for composite actions), the "pull request and CI, not a second reviewer" limit from open question 2, and the residual from open question 1. State two more limits. The required status checks are a first-party gate: `Lint`, `Unit tests` and `gitleaks` all carry a fork guard, so on a fork pull request only `Check paths` reports and the other three are satisfied by skipping; `secret-scan.yml` and the organisation's push protection are what cover forks. And, which phase 2's risk list records: the review-marks App is installed on `actions` as of 2a, and `bypass-actor-identity`'s accepted residual -- `shakenfist-k3s-ci-arc` holding `administration: write` on hunkydory -- therefore reaches a bypass of `actions`' own ruleset. Say so plainly rather than describing the ruleset as the boundary. Link both new specs. In the Why section, put the instar#617 sentences in the past tense, describing what actually landed if review changed it. Do not close #153: phase 1 closed it on 2026-10-03, when development#209 landed. Link phase 1's landings instead. |

### Phase 5: shared workflows instead of copied templates

Planning effort: high. The classification in 5a decides how much of
the fleet changes, and renaming required checks can wedge merge
queues if it is done carelessly.

**Why.** `templates/` hands workflows to the fleet by copying, and
the copies drift. Read through the GitHub API on 2026-10-03:

| Template | Live copies | Distinct versions |
|---|---|---|
| `renovate.yml` (its README says "copied verbatim") | 17 | 14 |
| `codeql-analysis.yml` | 17 | 12 |
| `pr-retest.yml` | 17 | 10 |
| `export-repo-config.yml` (already a caller of `actions`) | 17 | 6 |
| `mermaid-lint.yml` | 6 | 3 |
| `prune-reviews.yml` | 7 | 1 |

`prune-reviews.yml` is the exception because `PLAN-review-import.md`
is actively keeping it converged. Even the caller stub
`export-repo-config.yml` has six versions, so the stubs need a
criterion too, not just the code they call.

**Why after phase 2.** Today a push to `actions` main reaches 93
`@main` references in 14 repositories. Each template moved there
adds to that. Phase 2 is what makes the moving ref acceptable, so
this phase must not start until phase 2's ruleset is live.

**Two costs to plan around.**

* A job inside a reusable workflow reports its status as `caller /
  callee`. Any repository whose ruleset or merge queue requires the
  old check name stops merging until the ruleset is updated. Each
  rollout pull request therefore changes the ruleset in the same
  step, and the required checks are listed before the change.
* A reusable workflow cannot read files from its own repository
  without a second checkout, whereas a composite action can, through
  `github.action_path`. Templates that ship a script into `tools/`
  (`mermaid-lint.sh`, `ci-prune-reviews.sh`,
  `pin-indirect-dependencies*.sh`, the `issue-fix` scripts) probably
  want a composite action for their steps, wrapped by a thin caller
  workflow. Step 5a confirms this rather than assuming it. A
  composite action runs inside the caller's job, with that job's
  environment, which is the wider exposure the Situation section
  describes, so phase 2's protection matters most for these.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | none | Classify every template directory under `templates/` except `shared-blocks/` and `renovate/` (which `PLAN-renovate-cadence.md` centralises). For each workflow file, fetch every live copy from each fleet repository's default branch through the GitHub API (local clones are stale), diff each one against the template, and sort the differences into drift and legitimate per-repository adaptation, citing the lines. Then decide one of three outcomes and say why: a reusable workflow in `actions`, a composite action in `actions` (when the steps need the caller's checkout or ship a script), or the template stays a template (when the differences are legitimate and cannot be expressed as `with:` inputs). For each repository and template, record which required status-check contexts its ruleset and merge queue name, because wrapping renames them. For `mermaid-lint`, check whether the 2026-10-13 re-sync pull requests listed for #151 under Related issues landed before classifying those copies. Write the result into this section as a table, and order the rollout by drift multiplied by copy count. Make no changes outside this plan file. |
| 5b | high | opus | worktree | Pilot on `mermaid-lint` (6 copies, ships `mermaid-lint.sh`, runs on a docker-capable runner). In `shakenfist/actions`, add the composite action or reusable workflow 5a chose, carrying the script. Reduce `templates/mermaid-lint/mermaid-lint.yml` to the caller stub, update `templates/mermaid-lint/README.md`, and change the `mermaid-lint-ci` criterion (`class MermaidLintCi` in `scripts/audit/checks/docs_content.py`, which today requires `MERMAID_LINT_SCRIPT = 'tools/mermaid-lint.sh'` from the same module) so it requires the caller (`uses: shakenfist/actions/...@main` with the documented inputs) and no longer requires `tools/mermaid-lint.sh`, with pass, fail and stale-copy tests. Dropping the requirement is not the same as deleting the file: decide, and record here, how a developer runs the lint by hand afterwards (a thin local wrapper, or the action's script from a checkout of `actions`), because `AGENTS.md` prescribes `tools/mermaid-lint.sh` after editing a diagram. If this repository's copy goes, everything that names it moves in the same pull request. Find those files with `git grep -l mermaid-lint.sh` at the time, not from a list written here, and leave out three kinds of hit: `docs/plans/`, which records history; `REVIEWS.md` and the `.vscode/` weAudit files, which `prune-reviews` reconciles on the default branch after the merge and which `AGENTS.md` says a pull request must not edit. Three hits need more than a path change: `test_the_skill_pins_the_same_image` in `MermaidLintDeploymentTest` reads the image digest from the script, so say where that pin lives afterwards; the `tools/` clause of the `audit-package-tests` hook in `.pre-commit-config.yaml` exists because the mermaid-lint tests read the script; and the "Using it by hand" section of `templates/mermaid-lint/README.md` is where the answer above is recorded. Convert this repository's own copy first, then roll out one pull request per repository, updating each repository's required-check names in the same step. Record each landing in this section. |
| 5c | medium | sonnet | none | Add a test under `scripts/tests/` asserting that every template 5a classified as shared is a caller stub: it calls `shakenfist/actions` at `@main`, and it has no `run:` steps of its own. Then document the convention, that a template meant to be identical everywhere is a caller and anything else says in its README why it is not, in `docs/consistency-audits.md` beside its description of templates (there is no `templates/README.md`). Mention it in `AGENTS.md` only as a one-line pointer, because it is a convention change. This step sets the condition that closes #151: with 5b, it answers that issue's question of whether template directories should carry a staleness marker like the shared blocks' `vN`, by removing the copies rather than versioning them. That answer only covers templates that become callers: a template 5a keeps as a template must also say in its README how drift in its copies is caught (a `vN` marker, an audit check, or a statement that drift is acceptable there). The condition is that every template 5a classified is either a caller stub or says in its README how drift in its copies is caught; a template still queued for 5d counts as neither. The two directories 5a leaves out are outside the condition: `shared-blocks/` already meets it, because its README describes the version marker and the audit that flags a repository carrying an old version, and `renovate/` is owned by `PLAN-renovate-cadence.md`, whose shared preset replaces the copies. 5c lands before 5d, so it does not close #151 itself: put "Fixes #151" in the pull request that makes the condition true, normally the last 5d landing. If 5d stops with templates still unconverted, file a follow-up issue naming them and cite it when closing #151. |
| 5d | medium | sonnet | worktree | For each remaining template in 5a's rollout order, repeat 5b's shape: callee in `actions`, stub template, criterion change with tests, then one pull request per repository using the `consistency-fix` skill, with required checks updated in the same step. Each template is its own pull request in this repository, and its landing is recorded here. The landing that makes 5c's condition for #151 true carries "Fixes #151"; if 5d stops short of it, file the follow-up issue 5c describes. |

### Phase 6: push audit

Planning effort: medium. The runbook and the ranges are fixed; the
work is running it.

Run `PUSH-AUDIT.md` once per landing recorded in the `Merged` cells
above, with `AUDIT_RANGE` set as its "How to use this runbook"
section says: `<sha>^1..<sha>` for a merge commit, `<first>^..<last>`
for a direct landing. Do not use `origin/main...`: once the phases
have merged that range is empty and reads as a clean audit. Phase
1's landings are in other repositories and were reviewed in their
own pull requests, so this phase cites those rather than re-running
them.

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
it rather than explaining its absence. Its diff commands use
`${AUDIT_RANGE:-origin/main...HEAD}`, so run `git fetch origin`
first: a stale `origin/main` widens the audit to unrelated history.
Phase 6 sets `AUDIT_RANGE` per landing, because once a phase has
merged the default range is empty and reads as a clean audit.

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

**In this plan.** Step 2a is the exception: it changes GitHub
settings on another repository, so the management session does it
with the operator rather than delegating it.

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

* `reusable-workflow-secrets` is compliant in every repository in
  scope.
* A direct push to `shakenfist/actions` main by a person is
  rejected, and `moving-ref-source-protection` reports compliant
  for `actions`.
* No third-party `uses:` in this repository or under `templates/`
  is unpinned. `third-party-action-pinning` has filed its issues
  fleet-wide, and the plan records how many.
* The specs say why first-party refs move and third-party refs do
  not. #153 itself closed on 2026-10-03 with phase 1, before the
  policy it asked for was written, so the specs rather than the
  issue state are what this criterion reads.

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

- `PLAN-TEMPLATE.md`'s "In this repository" note after the
  push-audit block says `PUSH-AUDIT.md`'s commands are written
  against `main...HEAD`. They now use
  `${AUDIT_RANGE:-origin/main...HEAD}`. Correct the template, which
  this plan copies but does not own.
- `scripts/audit-check.py` appears to write more than its JSON
  document to stdout when redirected to a file, so `json.load` of
  its output fails. Reproduce and file it.
- `visual-digest-rust` and `private-ci` have no `renovate.json`, so
  phase 3's pins would rot in those two repositories. The
  `renovate` criterion already owns that.
- Phase 1's post-merge `pr-fix-tests` trigger in instar, occystrap
  and shakenfist was never run (close-out, 2026-10-31). The first
  maintainer-triggered run in each is the check: in instar, its
  "Prepare instar-testdata" step must authenticate with the named
  `GITLAB_TESTDATA_TOKEN`; in the other two, the run must get past
  its token-using steps. Read the step log when one happens, and
  record it here.
- Narrow `shakenfist-k3s-ci-arc`'s `administration: write` on
  hunkydory to the runner registration it needs. Installing the
  review-marks App on `actions` in 2a extended that accepted
  residual to the fleet's moving-ref source, so the chain now ends
  in a bypass of `actions`' own ruleset. The residual is recorded
  in `docs/audits/bypass-actor-identity.md` and in phase 2's risk
  list; narrowing it is the fix, and it is outside this plan.
- `actions` exports no `.github/exported-config/`, so a ruleset
  change there is captured nowhere in the repository it applies to,
  and `merge-queue-config.md`'s closing instruction to trigger
  `export-repo-config` cannot be followed. The fleet's moving-ref
  source is thus the one repository whose configuration drift
  nothing detects. The decision belongs to the `export-repo-config`
  criterion and has two admissible answers: `actions` calls the
  reusable workflow it ships, or that criterion records `actions`
  as a stated exception with this reason. Whoever next touches that
  criterion picks one; leaving it unstated is not an answer.
- The count of files a criterion touches is stated three different
  ways: `PLAN-TEMPLATE.md`'s shared success-criteria block says
  five, `AGENTS.md` says six, and `docs/consistency-audits.md` says
  six plus a seventh line for a criterion that reaches the network.
  The 2b brief works around it by telling its executor not to trust
  the template, which protects 2b and leaves the next criterion's
  author reading the same stale count. Make the block and
  `AGENTS.md` non-numeric -- "every file the *Adding a criterion*
  list names, plus `NETWORK_CHECKS` for a criterion that reaches
  the network" -- which is a fleet-wide sweep, since the block is
  embedded in four plans.
- `actions` has no contributor-facing note about the one shape that
  cannot merge there: a maintainer-authored branch carrying a
  commit whose author email is linked to no GitHub account asks for
  an approving review the sole maintainer cannot give. The person
  who hits it will be in `actions` with a pull request that will
  not merge, while the diagnosis sits in this plan in another
  repository. One sentence in that repository's `AGENTS.md`, with
  re-authoring the commit as the fix.
- #389: the `shakenfist-review-marks` App holds two private keys
  after 2a, because change 2 set the new key only in `actions`'
  environment rather than rotating all five. Completing it is one
  pass over the five repositories, and the one-key property can only
  be confirmed by eye on the App's settings page.
- Phase 2 exercised the `actions` bypass but never observed it. Run
  43081577539 minted the App token and ran to success, but had no
  review marks to prune, so it never pushed and
  `rulesets/rule-suites?rule_suite_result=bypass` stayed empty. The
  first prune run on `actions` main that actually prunes answers it:
  read that listing and confirm it names
  `shakenfist-review-marks[bot]`, then record it in phase 2.
- `moving-ref-source-protection` measures that a
  `required_status_checks` rule exists, not that its contexts still
  name jobs `ci.yml` defines. Renaming a job in `actions` would
  break every pull request there and the criterion would report
  compliant. The workflow-reading helpers it would need
  (`workflow_job_blocks`) are already imported by
  `scripts/audit/checks/github_config.py`; it is a second criterion,
  not a widening of this one.
- An organisation-level "require workflows" rule, sourced from a
  protected ref, is the stronger form of what phase 2 does (its
  decision 6). It wants a protected ref to source from, which phase
  2 creates, and an audit token that can read
  `orgs/{org}/rulesets`, which #352 is about. Revisit once both
  hold.

Related issues, as of writing:

- #153 is the issue this plan answers. It was closed on
  2026-10-03 by phase 1 (development#209), ahead of the policy
  write-up in phase 4, so no phase closes it.
- #151 (templates/mermaid-lint/ has no staleness mechanism) is
  closed by phase 5: 5b stops the fleet carrying a copy of
  `mermaid-lint.sh`, and 5c makes "a caller, or a README saying
  how drift is caught" the answer for every template, closing
  once 5d has converted the rest. The issue's other drift path,
  this repository's `tools/mermaid-lint.sh` against the
  template, was open when it was filed and has since been
  pinned byte-identical by `MermaidLintDeploymentTest`; what
  remains open is drift in the fleet's copies. Its README
  symptom has no drift path: the template's `README.md` is not
  in its Files table and is not copied into adopting
  repositories, which carry only the script, the workflow and
  their own `tools/mermaid-lint-exclude`. Until phase 5 lands,
  fleet copies are re-synced by hand when they lag. On
  2026-10-13 the `tools/mermaid-lint.sh` copies in instar,
  occystrap and
  shakenfist lagged the template's exclusion-file hardening,
  the drift this phase removes. Re-sync pull requests were
  opened directly rather than left waiting for phase 5
  (instar#657, occystrap#160, shakenfist#4519); 5a should
  check whether they landed rather than assume it.
- #88 (standardise renovate groupings) interacts with open
  question 4's grouping rule.
- #58 (Dependency Dashboard) is where phase 3a's pinning pull
  request will show up.
