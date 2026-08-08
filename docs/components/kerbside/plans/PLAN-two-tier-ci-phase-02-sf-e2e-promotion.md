# Two-tier CI phase 2: promote sf-e2e to a PR smoke gate

Phase 2 of [PLAN-two-tier-ci.md](/components/kerbside/plans/PLAN-two-tier-ci/). Flips
`.github/workflows/sf-e2e-functional.yml` to also run on
`pull_request`, superseding the phase 9 "dispatch/schedule only"
caveat (decision 4 in
[PLAN-kerbside-vdi-tokens-phase-09-e2e.md](/components/kerbside/plans/PLAN-kerbside-vdi-tokens-phase-09-e2e/)).

## Prompt

"Please do the cleanup and then plan and implement phase 2 -- I
think the promotion precondition in hindsight is too harsh given
the experimental nature of kerbside as a project and my generally
underspeced CI cluster."

## Situation

Phase 9 decision 4 gated the sf-e2e lane on `workflow_dispatch` and
a nightly `schedule` only, "until it is shown stable", with an
explicit intent to promote it to a PR gate "once green and
reliable". The master plan's open question 1 proposed a promotion
criterion of ten consecutive green scheduled runs, consistently
under ~30 minutes.

The run history as of 2026-08-08 (nightlies unless noted):

| Date (UTC) | Result | Duration | Cause of failure |
|------------|--------|----------|------------------|
| 2026-08-08 | queued for hours | - | runner pool contention |
| 2026-08-07 | success | 16.9m | |
| 2026-08-06 | success | 29.1m | |
| 2026-08-05 | success | 16.2m | |
| 2026-08-04 | success | 35.1m | |
| 2026-08-03 | failure | 11.3m | kerbside-proxy compile break (tonic `Body` mismatch) in the proxy-wheel build |
| 2026-08-02 | failure | 11.2m | same compile break |
| 2026-08-01 | success | 16.2m | |
| 2026-07-31 | failure (sched), success (dispatch) | 19.9m / 46.9m | happy-path driver, during bring-up |

## The relaxed promotion criterion

Maintainer decision (2026-08-08): the N=10 criterion was too harsh
in hindsight, for three reasons.

1. **Kerbside is experimental.** The cost of a false-negative PR
   gate is a re-run, not a blocked release train. The bar for
   "trusted enough to gate PRs" should reflect that.
2. **The CI cluster is underspecced.** Nightly-only signal
   accumulates one data point per day at best — the 2026-08-08
   nightly sat queued for hours behind other work — so ten
   consecutive greens is weeks of calendar time for little extra
   confidence.
3. **The lane fails for the right reasons.** Every failure since
   bring-up completed has been a real kerbside defect (the
   2026-08-02/03 runs caught a kerbside-proxy compile break against
   a dependency bump — exactly the class of breakage a PR gate
   would have stopped before merge), not lane flake. Four
   consecutive green nightlies (2026-08-04..07, 16–35 minutes)
   since that fix is sufficient evidence the lane itself is sound.

Promotion on this evidence, rather than after ten greens, is the
decision this plan records.

## Mission

Make the sf-e2e lane a `pull_request` smoke gate without weakening
anything else: keep the nightly schedule and manual dispatch,
protect the underspecced runner pool from stacked superseded runs,
and skip review-only changes the lane cannot exercise.

## Execution

One commit, this branch (`sf-e2e-pr-promotion`):

1. **`.github/workflows/sf-e2e-functional.yml`**
   - Add a `pull_request` trigger (branches: develop) with the same
     review-only `paths-ignore` list the direct-qemu and
     functional-tests workflows use — keep the three lists in sync.
   - Add a job-level `concurrency` group
     (`${{ github.workflow }}-${{ github.ref }}`,
     `cancel-in-progress: true`), matching the style of the
     functional-tests matrix jobs, so a superseded push cancels the
     running lane instead of queueing another 120-minute-timeout
     job on the `l` pool.
   - Replace the phase 9 header caveat with a comment recording the
     promotion and why the schedule survives it:
     `build-smoke-cluster` deploys Shaken Fist at develop HEAD,
     which moves independently of kerbside PRs, so the nightly
     catches SF-side drift no kerbside PR would surface.
   - No other behaviour changes. The environment-retention step is
     already guarded by `github.event_name == 'workflow_dispatch'`,
     so the empty `inputs.retention` on PR runs is inert.
2. **Statements that become false** (minimal fixes only; the wider
   documentation consolidation is phase 4):
   - `AGENTS.md`: the sf-e2e section's "dispatch- and
     nightly-scheduled, NOT a PR gate — phase 9 decision 4".
   - `README.md`: the "dispatch- and nightly-scheduled"
     parenthetical in the sf-e2e paragraph.
   - `PLAN-kerbside-vdi-tokens-phase-09-e2e.md`: annotate decision
     4 as superseded (do not rewrite the historical decision).
3. **`PLAN-two-tier-ci.md`**: mark the precondition resolved,
   record the relaxed criterion under open question 1, and set
   phase 2 to Complete in the execution table.

## Risks considered

- **Runner pool load.** This adds a ~17–35 minute `l`-runner job to
  every non-review-only PR push, and renovate PRs are the bulk of
  PR volume. Mitigations: the concurrency group cancels superseded
  runs; review-only PRs skip entirely; and the direct-qemu lane
  already set the precedent of an `l` VM job on every PR. If the
  pool saturates in practice, the fallback is demotion back to
  schedule-only (the same escape hatch open question 2 records for
  the oVirt lane), not a weaker assertion set.
- **Fork PRs on self-hosted runners.** `pull_request` on
  self-hosted runners runs PR code on the pool. This is the
  repository's existing posture — direct-qemu and functional-tests
  already do it, gated by GitHub's approval-for-outside-contributor
  settings — so this change adds no new exposure class, only one
  more workflow inside it.
- **Required-check status.** Adding the trigger makes the lane run
  and report on PRs; making it a *required* check is manual branch
  protection work, deliberately deferred to phase 3 alongside the
  merge-queue re-wiring. Until then a red sf-e2e is visible but
  advisory.

## Out of scope

- Branch protection / required checks / merge queue: phase 3.
- `docs/testing.md`, `.claude/CLAUDE.md`'s CI workflow list (which
  predates this and does not list sf-e2e-functional.yml at all),
  and the broader AGENTS.md / ARCHITECTURE.md pass: phase 4.

## Back brief

Implemented 2026-08-08 as planned. The first `pull_request` run of
the lane is the PR that carries this plan — treat that run as the
promotion's own smoke test.
