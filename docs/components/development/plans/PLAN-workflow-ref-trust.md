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

Close #153 with a settled fleet policy on which workflow references
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
   *Default:* a team of one, `shakenfist-bot`, as the only bypass
   actor, with the bypass narrowed to the review-tracking paths if
   GitHub can express that for this repository (step 2a finds out).
   It probably cannot: a ruleset's bypass list covers the whole
   ruleset, branch rulesets have no per-path condition, and a
   file-path restriction is a push rule that binds every push,
   pull request merges included. Expect an unnarrowed bypass, and
   write phase 2's success criterion for that outcome. The spec
   states the residual risk accurately: the bypass moves
   the boundary from "can push to main" to "can run a workflow that
   has `DEPENDENCIES_TOKEN` in scope", which is wider than one
   setting in one repository. The `|| github.token` fallback stays
   harmless, because `github.token` is not a bypass actor and its
   push is simply refused; nobody should add the Actions app as a
   bypass actor to "fix" that refusal. Replacing the PAT with a
   GitHub App token or a fine-grained token limited to `actions`
   (#211) is what actually closes that residual. It is outside this
   plan because it changes every adopted repository's prune
   workflow, not because it matters less. `PLAN-review-import.md`
   does not move prune to pull requests, so that alternative remains
   a template change this plan does not make.
2. **What does "a pull request is required" mean with one
   maintainer?** A required approval count of one cannot be met by
   the author. *Default:* require a pull request with zero required
   approvals, plus required status checks. Rulesets require check
   contexts by job name, not workflow file, so step 2a names them
   from `actions`' `ci.yml` (today `Check paths`, `Lint` and
   `Unit tests`, the latter two skipped by its path filter on
   review-only changes). The gain is that nothing lands without CI
   and a visible record. It is not gated on a second person. And a
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
| 1. Finish the inherit rollout | In progress | |
| 2. Make `actions` main require a pull request | Not started | |
| 3. Pin third-party actions to a sha | Not started | |
| 4. Record the policy and close #153 | Not started | |
| 5. Shared workflows instead of copied templates | Not started | |
| 6. Push audit | Not started | |

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

**Pull requests.** Opened 2026-10-02. The `Merged` cell is filled
from this list at close-out. States are as of 2026-10-03. The
`reusable-workflow-secrets` spec describes the merged pull requests
in the past tense and instar#617 as proposed, so the spec and this
table move together.

| Step | Pull request | Closes | State |
|------|--------------|--------|-------|
| 1a | instar#617 | #600, #610 | Open |
| 1b | shakenfist#4404 | #4380 | Merged as e4c7726 |
| 1c | occystrap#152 | #148, #150 | Merged as c583d68 |
| 1d | client-python#415 | #409, #410 | Merged as 68f295f |
| 1e | agent-python#148 | #146 | Merged as 4bf7763 |
| 1e | client-python-k3s#83 | #79 | Merged as 03998e0 |
| 1e | clingwrap#141 | #138 | Merged as 125eb20 |
| 1e | divergulent#125 | #121 | Merged as 8a2852e |
| 1e | library-utilities#67 | #63 | Merged as 832d82d |
| 1e | sfui#44 | #38 | Merged as ad0d6a2 |
| 1f | this branch | -- | -- |

Step 1f landed differently from its brief, deliberately. Rather than
updating the docstring's dated claim about the fleet's callees, it
removed it: the docstring now says what the check does, and the
spec's Why section is the single record of what callees were found
to read. A docstring survey goes stale every time a callee changes.

In review, divergulent#125 and library-utilities#67 also replaced a
comment that called the deleted `secrets: inherit` necessary, with
the template's comment. The comment was wrong: the callee reads no
secret.

### Phase 2: make `actions` main require a pull request

This covers everything `actions` serves at `@main`: reusable
workflows and composite actions alike. Composite actions are the
stronger case for it, since they run inside the caller's job.

Change the existing "Protect default branch history" ruleset on
`shakenfist/actions` (or add a second ruleset beside it) to:

- require a pull request, with the approval count from open
  question 2;
- require the status check contexts named in open question 2;
- give only the bypass decided in open question 1.

Do this by hand, as an operator, and record the ruleset JSON in the
phase. Then add a criterion so that this does not quietly regress.
The criterion is `moving-ref-source-protection`, in
`scripts/audit/checks/github_config.py`. It applies to the
repositories that the fleet consumes at a moving ref, named in a
module constant (today only `actions`). Everywhere else it reports
`not_applicable` with that reason. It reads
`repos/{org}/{repo}/rules/branches/{default}` and fails unless a
`pull_request` rule and a `required_status_checks` rule are both
present. That endpoint does not list bypass actors, so the check also
reads each contributing ruleset (`repos/{org}/{repo}/rulesets/{id}`)
and fails unless `enforcement` is `active` and `bypass_actors` is
exactly the documented set. GitHub only returns `bypass_actors` to a
caller that can edit the ruleset, and omits the key otherwise, so a
missing key is an error, never an empty set. If step 2a finds that
the audit's token cannot see the key, the check takes the fallback
shape 2a describes instead, and the token is not widened to make it
fit. A ruleset inherited from the organisation is read from the
organisation's endpoint.

Planning effort: high. This is what decides what the fleet trusts,
and it touches GitHub settings that this repository cannot test.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Operator step, done by the management session with the operator, not by a sub-agent. Settle how open question 1's bypass can be expressed (team of one or app; path-narrowed if possible) and which check contexts open question 2 requires, then apply the ruleset change. Then prove it on a throwaway branch: a direct push to main is rejected, and a `prune-reviews` run still lands (or opens its pull request, per open question 1). Open a review-only throwaway pull request too, and confirm that every required check resolves: `ci.yml` filters at job level through `Check paths`, and a skipped job reports success, but a trigger-level `paths:` or `paths-ignore:` would never report and would wedge every such pull request. Record which token the audit runs with, and confirm that it sees `bypass_actors` in `repos/shakenfist/actions/rulesets/{id}`; if it does not, do not widen the audit token to make it. Instead, 2b drops the `bypass_actors` comparison and measures only the two rules and `enforcement`, and the spec moves the bypass list to its "Required, but confirmed by a reviewer" half. Record which shape 2b is to take in this phase. For the residual in open question 1, record which repositories put `DEPENDENCIES_TOKEN` in a workflow's scope, and whether any of those workflows runs on a trigger that someone other than a maintainer can fire (`pull_request_target`, `issue_comment`, `workflow_run`). |
| 2b | high | opus | worktree | Add a `MovingRefSourceProtection` class to `scripts/audit/checks/github_config.py`, with the id `moving-ref-source-protection`, its `spec` and its `issue_title` (`Moving ref source protection`), following `DeleteBranchOnMerge` at line 213 for the API-call shape. Register it in `CHECKS` in `scripts/audit/registry.py`. Write `docs/audits/moving-ref-source-protection.md`, following the structure in `docs/audits/README.md`; its Why section cites the landing counts from this plan's Situation. Add the file to `docs/audits/README.md`, and add its lines to `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in `scripts/tests/test_metadata.py`. Add pass, fail and not-applicable tests to `scripts/tests/test_github_config.py`, using `CheckTestCase` and a stubbed GitHub client, including fail cases for a missing status-check rule, `enforcement: evaluate`, and an extra bypass actor, and a case where the ruleset response has no `bypass_actors` key, which must be an error rather than a pass. If 2a recorded the fallback shape, drop the bypass comparison and its two cases, and say so in the spec. |

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
once for the whole fleet from this repository. If its phase 1 has
landed when this phase starts, step 3b adds
`helpers:pinGitHubActionDigests` to the preset once rather than to
`templates/renovate/renovate.json` and every copy of it. Its monthly
CI-tooling group (all `github-actions` and `pre-commit` updates,
digests included) is the grouping rule that open question 4's
default anticipates. Once its phase 3 has landed,
`renovatebot/github-action` appears only in this repository's
central workflow, so 3b pins it there. Land that plan's phase 1
before this phase's 3b; otherwise every pin arrives as a separate
pull request in every repository.

### Phase 4: record the policy and close #153

Planning effort: medium. It documents a policy that phases 2 and 3
have already decided.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | In `docs/audits/reusable-workflow-secrets.md`, replace "What this does not cover" with the settled policy: first-party refs move, and the `moving-ref-source-protection` criterion is what makes that acceptable; third-party refs are pinned, under `third-party-action-pinning`. Include the upper bound of 14 pull requests a day this avoids, what a moving ref can reach (Situation: the runner's filesystem, and the caller's job for composite actions), the "pull request and CI, not a second reviewer" limit from open question 2, and the residual from open question 1. Link both new specs. In the Why section, put the instar#617 sentences in the past tense, describing what actually landed if review changed it. Close #153 from the pull request body, and link phase 1's landings. |

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
| 5a | high | opus | none | Classify every template directory under `templates/` except `shared-blocks/` and `renovate/` (which `PLAN-renovate-cadence.md` centralises). For each workflow file, fetch every live copy from each fleet repository's default branch through the GitHub API (local clones are stale), diff each one against the template, and sort the differences into drift and legitimate per-repository adaptation, citing the lines. Then decide one of three outcomes and say why: a reusable workflow in `actions`, a composite action in `actions` (when the steps need the caller's checkout or ship a script), or the template stays a template (when the differences are legitimate and cannot be expressed as `with:` inputs). For each repository and template, record which required status-check contexts its ruleset and merge queue name, because wrapping renames them. Write the result into this section as a table, and order the rollout by drift multiplied by copy count. Make no changes outside this plan file. |
| 5b | high | opus | worktree | Pilot on `mermaid-lint` (6 copies, ships `mermaid-lint.sh`, runs on a docker-capable runner). In `shakenfist/actions`, add the composite action or reusable workflow 5a chose, carrying the script. Reduce `templates/mermaid-lint/mermaid-lint.yml` to the caller stub, update `templates/mermaid-lint/README.md`, and change the `mermaid-lint-ci` criterion (`scripts/audit/checks/docs_content.py:444`, which today requires `MERMAID_LINT_SCRIPT = 'tools/mermaid-lint.sh'` at `:143`) so it requires the caller (`uses: shakenfist/actions/...@main` with the documented inputs) and no longer requires `tools/mermaid-lint.sh`, with pass, fail and stale-copy tests. Convert this repository's own copy first, then roll out one pull request per repository, updating each repository's required-check names in the same step. Record each landing in this section. |
| 5c | medium | sonnet | none | Add a test under `scripts/tests/` asserting that every template 5a classified as shared is a caller stub: it calls `shakenfist/actions` at `@main`, and it has no `run:` steps of its own. Then document the convention, that a template meant to be identical everywhere is a caller and anything else says in its README why it is not, in `docs/consistency-audits.md` beside its description of templates (there is no `templates/README.md`). Mention it in `AGENTS.md` only as a one-line pointer, because it is a convention change. |
| 5d | medium | sonnet | worktree | For each remaining template in 5a's rollout order, repeat 5b's shape: callee in `actions`, stub template, criterion change with tests, then one pull request per repository using the `consistency-fix` skill, with required checks updated in the same step. Each template is its own pull request in this repository, and its landing is recorded here. |

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
* #153 is closed, and the specs say why first-party refs move and
  third-party refs do not.

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

- Replace `DEPENDENCIES_TOKEN` with a GitHub App token or a
  fine-grained token limited to what it pushes. While it can bypass
  the `actions` ruleset, anything that can run a workflow with it in
  scope can put unreviewed code into every consumer's CI (open
  question 1). Tracked as #211.
- `PLAN-TEMPLATE.md`'s "In this repository" note after the
  push-audit block says `PUSH-AUDIT.md`'s commands are written
  against `main...HEAD`. They now use
  `${AUDIT_RANGE:-origin/main...HEAD}`. Correct the template, which
  this plan copies but does not own.
- `scripts/audit-check.py` appears to write more than its JSON
  document to stdout when redirected to a file, so `json.load` of
  its output fails. Reproduce and file it.
- `visual-digest-rust` and `private-ci` have no `renovate.json`.
  Phase 3's pins would rot in those two repositories. The
  `renovate` criterion already owns that.

Related issues, as of writing:

- #153 is the issue this plan closes.
- #211 replaces `DEPENDENCIES_TOKEN`, the residual from open
  question 1.
- #88 (standardise renovate groupings) interacts with open
  question 4's grouping rule.
- #58 (Dependency Dashboard) is where phase 3a's pinning pull
  request will show up.
