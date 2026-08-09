# Two-stage CI phase 1: two-tier ci.yml

Phase 1 of [PLAN-two-stage-ci.md](/components/ryll/plans/PLAN-two-stage-ci/). Restructure
`.github/workflows/ci.yml` into a smoke tier (runs on
`pull_request`, gates `Can enqueue`) and a merge tier (runs on
`merge_group`, gates `Can merge`), absorbing the scanner jobs from
`supply-chain.yml` and converting the workflow-level `paths-ignore`
into a `check_paths` job. This phase lands before any ruleset
change, so it must be behaviourally safe on its own: no check is
required by branch protection yet, and nothing in this phase blocks
merging.

Planned at high effort in the management session on 2026-08-09; the
research below is front-loaded so implementation steps can execute
from the briefs alone.

## Target design

### Triggers

```yaml
on:
  pull_request:
    branches: [develop]
  merge_group:
  workflow_dispatch:
```

Changes from today: `merge_group` is added; the `push:
branches: [develop]` trigger is dropped (master plan open question
4 — the queue tests the exact merge commit that lands, so post-merge
re-runs are redundant, and once phase 3 adds a `pull_request`
ruleset rule direct pushes stop existing); both workflow-level
`paths-ignore` blocks are removed in favour of `check_paths`.

`workflow_dispatch` deliberately runs *both* tiers (matching
shakenfist) so `pr-retest.yml`'s `gh workflow run ci.yml` remains a
full retest and the merge tier can be exercised on a branch before
phase 3 enables the queue.

### Job inventory

Every job in today's `ci.yml` and `supply-chain.yml` must be
accounted for:

| Today | Tier | Notes |
|-------|------|-------|
| ci.yml `lint` | smoke | unchanged steps |
| ci.yml `build-linux` | smoke | unchanged steps (build, web smokes, tests, deb, rpm) |
| supply-chain.yml `cargo-audit` | smoke | folded in; also stays in slimmed supply-chain.yml for the weekly cron |
| supply-chain.yml `cargo-deny` | smoke | folded in; also stays for the weekly cron |
| supply-chain.yml `gitleaks` | smoke | folded in; removed from supply-chain.yml |
| supply-chain.yml `shellcheck` | smoke | folded in; removed from supply-chain.yml |
| supply-chain.yml `bidi-check` | smoke | folded in; removed from supply-chain.yml |
| ci.yml `fuzz` (matrix x4) | merge | unchanged steps; keeps its concurrency group |
| ci.yml `build` (matrix x4) | merge | unchanged steps, env, and `audit-ok` comments |
| ci.yml `automated_reviewer` | smoke-adjacent | `needs` shrinks to smoke jobs |
| (new) `check_paths` | both | replaces `paths-ignore` |
| (new) `can_see_status`, `can_enqueue`, `can_merge` | gates | copied from shakenfist |

### check_paths

Copy shakenfist's `check_paths` job (`functional-tests.yml`) with
ryll's skip list. On `[self-hosted, static]`, `timeout-minutes: 10`:

```yaml
check_paths:
  name: "Check paths"
  runs-on: [self-hosted, static]
  timeout-minutes: 10
  outputs:
    code_changed: ${{ steps.filter.outputs.code || 'true' }}
  steps:
    - uses: actions/checkout@v7
      if: github.event_name != 'workflow_dispatch'
    - uses: dorny/paths-filter@v4
      id: filter
      if: github.event_name != 'workflow_dispatch'
      with:
        predicate-quantifier: 'every'
        filters: |
          code:
            - '**'
            - '!REVIEWS.md'
            - '!.vscode/*.weaudit'
            - '!.vscode/*.weaudit-shas.json'
            - '!.vscode/review-scope.toml'
```

`predicate-quantifier: 'every'` is load-bearing: the default ANY
semantics would make `'**'` match everything and defeat the
exclusions (see the comment in shakenfist's copy). The `|| 'true'`
fallback makes `workflow_dispatch` (where the filter is skipped)
run everything. shakenfist runs this job on `merge_group` as well,
so that path is precedented.

The current `paths-ignore` header comment in `ci.yml` ("Safe
because no status checks are required...") is rewritten to describe
the new scheme: review-only changes skip all tier jobs via
`check_paths`, and the gates pass because skipped dependencies
count as success. Keep the note about keeping the skip list in sync
with the `.vscode` whitelist in `.gitignore` and with
`codeql-analysis.yml` (which keeps its `paths-ignore` — it is not a
required check and is not part of the gate scheme), and update
`codeql-analysis.yml`'s comment to reference ci.yml's `check_paths`
filter rather than its former `paths-ignore`.

### Tier conditions

Smoke jobs (`lint`, `build-linux`, `cargo-audit`, `cargo-deny`,
`gitleaks`, `shellcheck`, `bidi-check`):

```yaml
needs: [check_paths]
if: >-
  github.event_name != 'merge_group'
  && needs.check_paths.outputs.code_changed != 'false'
```

Merge-tier jobs (`fuzz`, `build`):

```yaml
needs: [check_paths]
if: >-
  (github.event_name == 'merge_group'
   || github.event_name == 'workflow_dispatch')
  && needs.check_paths.outputs.code_changed != 'false'
```

The scanner jobs are copied from `supply-chain.yml` with their
runner labels and concurrency groups intact (the group strings
embed `${{ github.workflow }}` and so change value from "Supply
chain" to "CI"; that is fine and needs no edit). Add
`timeout-minutes: 15` to each scanner job while folding — they run
in single-digit minutes and currently inherit GitHub's six-hour
default.

### Gate jobs

Copied from shakenfist's `functional-tests.yml` (`can_see_status`,
`can_enqueue`, `can_merge`), all on `[self-hosted, static]` with
`timeout-minutes: 5`. The `name:` fields — "Can see status", "Can
enqueue", "Can merge" — are the status check contexts phase 3 will
mark required, and must match shakenfist's exactly.

```yaml
can_see_status:
  name: "Can see status"
  runs-on: [self-hosted, static]
  timeout-minutes: 5
  steps:
    - name: "Immediate success"
      run: true

can_enqueue:
  name: "Can enqueue"
  needs:
    - check_paths
    - lint
    - build-linux
    - cargo-audit
    - cargo-deny
    - gitleaks
    - shellcheck
    - bidi-check
  if: always() && github.event_name != 'merge_group'
  permissions:
    actions: read
  runs-on: [self-hosted, static]
  timeout-minutes: 5
  steps:
    - env:
        NEEDS_JSON: "${{toJSON(needs)}}"
      name: Transform outcomes
      run: |
        echo "ALL_SUCCESS=$(echo "$NEEDS_JSON" | jq '. | to_entries | map([.value.result == "success", .value.result == "skipped"] | any) | all')" >>$GITHUB_ENV
    - name: Check outcomes
      run: "[ $ALL_SUCCESS == true ]"

can_merge:
  name: "Can merge"
  needs:
    - check_paths
    - fuzz
    - build
  if: always() && github.event_name == 'merge_group'
  # ... identical body to can_enqueue
```

Mechanics worth preserving in comments: `always()` keeps the gate
running when a dependency fails or is skipped; the jq expression
maps each dependency to "success or skipped" and requires all of
them, which is what lets review-only changes (all tier jobs
skipped) pass both gates; `cancelled` and `failure` results fail
the gate.

### automated_reviewer

`needs` changes from `[lint, build, build-linux]` to the smoke set:

```yaml
needs: [lint, build-linux, cargo-audit, cargo-deny, gitleaks, shellcheck, bidi-check]
```

`build` (now merge-tier) never runs on `pull_request`; a `needs`
on it would leave the reviewer permanently skipped on PRs. The
scanners join the gate so the reviewer continues to mean "review
only once CI has passed". On review-only PRs the smoke jobs are
skipped and so is the reviewer — matching today, where the
workflow-level `paths-ignore` skipped it. Everything else
(permissions block, `uses`, `secrets: inherit`) is unchanged.

### supply-chain.yml after the fold

Keeps: name, `schedule` (weekly Monday 09:00 UTC cron),
`workflow_dispatch`, permissions, and the `cargo-audit` and
`cargo-deny` jobs unchanged. Loses: `pull_request` and `push`
triggers, and the `gitleaks`, `shellcheck`, and `bidi-check` jobs
(their content only changes when the tree changes, and every PR now
runs them in the smoke tier). The header comment is rewritten: this
workflow now exists solely to catch advisory drift against develop
between PRs; PR-time scanning lives in `ci.yml`.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Restructure `.github/workflows/ci.yml` per the "Target design" section of `docs/plans/PLAN-two-stage-ci-phase-01-workflow-tiers.md` (this file — read it in full first): add the `merge_group` trigger, drop `push` and `paths-ignore`, add `check_paths` and the three gate jobs (copy them from `../shakenfist/.github/workflows/functional-tests.yml`, jobs `check_paths`, `can_see_status`, `can_enqueue`, `can_merge`, adjusting the filter list and needs lists as specified), fold in the five scanner jobs from `.github/workflows/supply-chain.yml` verbatim plus `timeout-minutes: 15`, apply the smoke/merge tier `if:` conditions, update `automated_reviewer`'s `needs`, and rewrite the header comment plus the cross-reference comment in `codeql-analysis.yml`. Preserve every existing step, env block, runner label, concurrency group, and `audit-ok` annotation. Do not modify `supply-chain.yml` in this step. Validate with `pre-commit run --all-files` (actionlint must pass). |
| 1b | medium | sonnet | none | Slim `.github/workflows/supply-chain.yml` per the "supply-chain.yml after the fold" section of `docs/plans/PLAN-two-stage-ci-phase-01-workflow-tiers.md`: remove the `pull_request` and `push` triggers and the `gitleaks`, `shellcheck`, and `bidi-check` jobs; keep `schedule`, `workflow_dispatch`, permissions, `cargo-audit`, and `cargo-deny` unchanged; rewrite the header comment to say this workflow only catches advisory drift between PRs and that PR-time scanning lives in ci.yml's smoke tier. Validate with `pre-commit run --all-files`. |

Step 1a must land (be committed) before 1b: between the two
commits every scanner runs twice on a PR, which is safe; the
reverse order would leave a window where gitleaks, shellcheck, and
bidi-check run nowhere.

## Commit checkpoints

One commit per step:

1. "Split CI into smoke and merge tiers." — step 1a.
2. "Slim supply-chain.yml to scheduled advisory drift." — step 1b.

## Validation

Performed by the management session after both commits:

* `pre-commit run --all-files` passes (covers actionlint).
* Job-inventory review: diff old `ci.yml` + `supply-chain.yml`
  against the new files; every job in the inventory table above is
  accounted for.
* After the operator pushes the branch and opens the PR: the PR
  run shows `check_paths`, the seven smoke jobs, `Can see status`,
  and `Can enqueue` (green); `fuzz`, `build`, and `Can merge` show
  as skipped. The automated reviewer fires after the smoke jobs
  pass.
* `gh workflow run ci.yml --ref two-stage-ci` exercises both tiers
  including the merge-tier jobs and both gates, proving the
  merge tier works before phase 3 turns the queue on.
* A throwaway review-only test (e.g. `workflow_dispatch` is not
  suitable here — instead verify via a scratch branch PR touching
  only `REVIEWS.md`) shows all tier jobs skipped and `Can enqueue`
  green. This validation may also be deferred to phase 3's
  end-to-end checks if a scratch PR feels heavyweight before the
  queue exists.

## Risks and notes

* The gate jq treats `skipped` as success. This is what makes
  review-only PRs work, but it also means a job accidentally
  dropped from a `needs` list is silently not gating — the
  job-inventory review in Validation is the guard.
* `dorny/paths-filter` is a new third-party action for this repo
  (shakenfist already uses it); Renovate will manage its version
  like the rest.
* Until phase 3, nothing enforces the gates: a red `Can enqueue`
  on a PR is informational only. That is intentional — it gives a
  soak window where gate wiring bugs are visible but harmless.
* Artifact provenance changes: PR runs stop producing
  macOS/Windows/aarch64 artifacts (they come from merge-group or
  `workflow_dispatch` runs, or `manual-build.yml`). The .deb/.rpm
  from `build-linux` still appear on every PR run.
