# Phase 5: Enable the GitHub merge queue + live verification

Master plan: [PLAN-distro-matrix-ci.md](/components/instar/plans/PLAN-distro-matrix-ci/).
Planning effort: **low**. Operator-driven (not delegated). Depends on
phase 4.

## Objective

Turn on GitHub's "Require merge queue" branch protection on `develop`
(and `main`) so the `package-matrix` job actually gates merges, and
verify a real PR merges through the queue and is gated by the matrix.

## Why operator-driven

This flips a repo-wide branch-protection setting that changes how every
merge works, and it is visible to all contributors. Per the master
plan's decision D2 the merge queue IS in scope, but the switch itself
and the verification merge are Michael's to perform, not a sub-agent's.

## Grounding facts (verified 2026-08-11)

The original draft's assumptions did not survive contact with the live
repository configuration.

- **`develop` has no branch protection and no ruleset today.**
  `GET /repos/shakenfist/instar/branches/develop/protection` returns
  404. So 5a is not "enable a setting on the existing protection" — it
  **introduces** protection for the first time.
- **The fleet uses rulesets, not classic branch protection.**
  `shakenfist/shakenfist` also 404s on classic protection and carries a
  ruleset named "Develop branch" (id 2681531) with five rules:
  `deletion`, `merge_queue`, `pull_request`, `required_status_checks`,
  `non_fast_forward`; bypass actor is team
  `shakenfist/sf-can-skip-merge-queue` (id 11722172, mode `always`);
  required contexts are `Can see status`, `Can enqueue`, `Can merge`.
  Queue parameters: `ALLGREEN`, `max_entries_to_build: 1`,
  `max_entries_to_merge: 5`, `MERGE`, 360-minute check timeout.
- **instar's only ruleset is unrelated and disabled** ("Code Quality
  Copilot review for default branch", id 19389569).
- **`.github/exported-config/` tracks the live state correctly.** An
  earlier draft of this plan claimed it was stale, having compared the
  live API against a `matrix-ci` checkout that predated `3874ed7`
  (8 Aug, merged via #480). After rebasing onto `develop` the exported
  snapshot and the live API agree (`enforcement: disabled`). The export
  proposes updates as pull requests rather than committing directly,
  which is why a search for direct commits found none. The settings
  table in `docs/development.md` is a human-readable companion to it,
  not a replacement for it.
- **Ruleset `enforcement: evaluate` does not give a report-only
  matrix.** The risk note below suggests a non-gating trial period;
  evaluate mode is available (the repo is public) but a `merge_queue`
  rule in evaluate mode does not create merge groups, so the matrix
  would never run. Repeated `workflow_dispatch` runs are the real
  report-only path.

## Decisions (2026-08-11)

- **Mirror the sibling's ruleset** rather than a queue-only minimum.
  Since `develop` is currently unprotected, this also gains deletion and
  force-push protection. It introduces no review requirement — the
  sibling sets `required_approving_review_count: 0`.
- **`develop` only.** `main` receives infrequent release merges that
  often need to move quickly; queueing them adds latency for little
  gain.
- **Required contexts are `Can enqueue` and `Can merge`.** Phase 4 built
  only `can_merge`, because `develop` had no required checks to satisfy.
  Requiring a merge-queue-only check while leaving the PR side with
  nothing to satisfy risks a PR that cannot enqueue, so `can_enqueue`
  was added to `functional-tests.yml` as part of this phase — the same
  jq-over-`needs` shape, gated `always() && github.event_name !=
  'merge_group'`, aggregating the pull-request jobs.
  `oslo-crossval-master` is deliberately excluded from it, being
  `continue-on-error` and therefore unable to gate anything.

## Ordering (important)

A required status check must have appeared on `develop` before it is
required. Creating the ruleset first would block every merge on a
context that never shows up. The correct order is:

1. Merge the phase-4 work to `develop` normally, with no queue.
2. `workflow_dispatch` dry run — validates the seven-wide fan-out,
   yields the wall-clock numbers phase 4 could not measure, and proves
   `GITLAB_TESTDATA_TOKEN` reaches a non-`pull_request` event (5b).
   **Done 2026-08-11 on the `matrix-ci` branch** (run 31533536833): all
   seven distros PASS, 0 failures, 89 minutes for the matrix. Full
   table in the phase-4 plan.
3. Create the ruleset (5a).
4. Verification merge (5c).

**One consequence of that run for 5a.** `can_merge` has still never
executed — it is `merge_group`-only, so GitHub has never seen its check
context. `Can enqueue` *has* now reported (success). Requiring a context
that has never appeared is the classic way to jam every merge, so either:

- create the ruleset requiring only `Can enqueue`, take one PR through
  the queue so `Can merge` appears, then add it; or
- create it requiring both and be ready to `DELETE` the ruleset if the
  first merge hangs.

The first is safer and costs one extra step. Prefer it unless Michael
wants the gate complete from the first merge.

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 5a | low | (agent, on operator approval) | none | **Done 2026-08-12.** Ruleset "Develop branch" created (id `20783686`, enforcement `active`) via `POST /repos/shakenfist/instar/rulesets`, read back and diffed against intent — all five rules, queue parameters and bypass actor match. Required checks are `Can enqueue` **only**, per the safer of the two options below; `Can merge` is added in 5c once a merge group has made that context exist. |
| 5b | low | (operator) | none | Confirm `GITLAB_TESTDATA_TOKEN` and any registry credentials are available to `merge_group` runs (the master-plan risk item). A queue run that can't fetch testdata fails opaquely. |
| 5c | low | (operator) | none | Verification merge: take a trivial no-op PR through the queue end-to-end. Confirm the matrix runs in the `merge_group` context and the seven distros report, and that a `pull_request` push does NOT run the matrix (PR latency unchanged). **Then add `Can merge` to the ruleset's required checks** (recipe in `docs/development.md`) — until that is done the matrix runs in the queue but does not gate it, so this step is not finished when the first queue run goes green. |
| 5d | low | sonnet | none | Docs close-out: mark the master plan Complete in `docs/plans/index.md` (+ `order.yml` if adding rows), add the CHANGELOG entry for merge-queue matrix CI, and record the enabled-settings snapshot (queue config, required checks) in `docs/development.md` so the configuration is reproducible if the repo is re-created. **Partly done 2026-08-11**: the settings snapshot and the queue/required-check rationale are in `docs/development.md` ("Merge queue and the `develop` ruleset"), and the CHANGELOG entry landed with phase 4. Remaining: mark the master plan Complete once 5a-5c are done. |
| 5e | low | (operator) | none | After 5a lands, confirm the next nightly `export-repo-config` run proposes the new "Develop branch" ruleset — that is the export doing its job, and it makes the queue configuration reproducible without relying on the table in `docs/development.md`. (An earlier draft made this a defect report against the export; that was based on a stale checkout and is withdrawn.) |

## Acceptance

- Merge queue enabled on `develop` (and `main` if applicable), with the
  matrix as a required check.
- A real PR observed merging through the queue, gated by the matrix.
- `pull_request` events do not run the matrix.
- Master plan marked Complete; index/order/CHANGELOG updated.

## Notes / risks

- Merge-queue flakiness cascade (master plan): before enabling, be
  confident the matrix is stable — a flaky entry blocks ALL merges.
  Consider running the matrix in a non-gating "report-only" mode for a
  week (via `workflow_dispatch` / a temporary non-required check)
  before making it required, to measure flake rate on real load.
- The export-repo-config workflow (`export-repo-config.yml`) may need to
  learn the new branch-protection/queue settings so the exported repo
  config stays authoritative — check whether it captures merge-queue
  configuration and extend it if not.
