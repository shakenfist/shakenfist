# Two-stage CI phase 3: merge queue enablement

Phase 3 of [PLAN-two-stage-ci.md](/components/ryll/plans/PLAN-two-stage-ci/). Phases 1 and
2 built the two tiers and their gates; nothing is required yet, so
`Can enqueue` and `Can merge` are advisory. This phase makes them
required and turns on the merge queue, which is the point of the
whole exercise: the expensive merge tier runs exactly once, against
the commit that is about to land.

This phase mutates live repository settings. The `gh api` call below
is not run until the operator has reviewed it.

## The prune-reviews problem, and how the fleet solves it

ryll's `prune-reviews.yml` pushes directly to develop. It runs on
`push: branches: [develop]`, regenerates review marks, and
`tools/ci-prune-reviews.sh` finishes with `git push origin develop`
using whatever credentials `actions/checkout` persisted. Three of the
last thirty develop commits were those bot commits.

A `pull_request` rule blocks direct pushes, so enabling the ruleset
without accommodation would break that workflow on every merge.

shakenfist/shakenfist has no prune workflow, so it never hit this.
kerbside did, and its solution is the one to copy: keep pushing
directly, but authenticate as shakenfist-bot via a PAT
(`DEPENDENCIES_TOKEN`), and make the bypass actor the "SF Can Skip
Merge Queue" team (id 11722172), which the bot belongs to alongside
mikalstill. GitHub does **not** accept the built-in Actions app as a
bypass actor, so authenticating as the Actions app and bypassing that
way is not an option — the PAT is load-bearing, not a preference.

A PAT push does retrigger the workflow, unlike a `GITHUB_TOKEN` push.
That is safe rather than a loop: the second run finds nothing to
prune and exits before committing.

`DEPENDENCIES_TOKEN` is a repository-level secret in both kerbside
and shakenfist — the only org-level secret ryll can see is
`RENOVATE_TOKEN` — so it was added to ryll separately on 2026-08-10.

## Target ruleset

ryll already has ruleset 18708684, "Protect default branch history":
`~DEFAULT_BRANCH`, active, rules `deletion` and `non_fast_forward`,
no bypass actors. kerbside's ruleset (20252051) has exactly the same
condition and target, plus the three rules this phase adds and the
team bypass. So this is an extension of the existing ruleset, not a
second one — which also avoids two rulesets disagreeing about the
same branch.

The name no longer describes the contents once merge queue and
pull request rules live there, so it is renamed to "Develop branch",
matching both siblings. The rename is visible in the
`export-repo-config` audit trail.

Note that `PUT /rulesets/{id}` replaces the `rules` array wholesale,
so `deletion` and `non_fast_forward` must be restated or they are
silently dropped. The same is true of `bypass_actors`.

Merge queue parameters are those resolved in open question 1 of the
master plan and now live on both siblings: ALLGREEN, `MERGE`,
`max_entries_to_build: 1` (no speculative stacking),
`max_entries_to_merge: 5`, `min_entries_to_merge: 1`, wait 5,
timeout 360.

The exact call, to be reviewed before it is run:

```bash
gh api --method PUT repos/shakenfist/ryll/rulesets/18708684 \
    --input ruleset.json
```

where `ruleset.json` is the payload below, written to a scratch file
rather than committed — the authoritative copy of the live state is
whatever `export-repo-config.yml` exports afterwards:

```json
{
  "name": "Develop branch",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
  },
  "bypass_actors": [
    {"actor_id": 11722172, "actor_type": "Team", "bypass_mode": "always"}
  ],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "merge_queue", "parameters": {
      "check_response_timeout_minutes": 360,
      "grouping_strategy": "ALLGREEN",
      "max_entries_to_build": 1,
      "max_entries_to_merge": 5,
      "merge_method": "MERGE",
      "min_entries_to_merge": 1,
      "min_entries_to_merge_wait_minutes": 5
    }},
    {"type": "pull_request", "parameters": {
      "allowed_merge_methods": ["merge", "squash", "rebase"],
      "dismiss_stale_reviews_on_push": true,
      "dismissal_restriction": {"allowed_actors": [], "enabled": false},
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_approving_review_count": 0,
      "required_review_thread_resolution": false,
      "required_reviewers": []
    }},
    {"type": "required_status_checks", "parameters": {
      "do_not_enforce_on_create": false,
      "required_status_checks": [
        {"context": "Can see status", "integration_id": 15368},
        {"context": "Can enqueue", "integration_id": 15368},
        {"context": "Can merge", "integration_id": 15368}
      ],
      "strict_required_status_checks_policy": false
    }}
  ]
}
```

`integration_id: 15368` is GitHub Actions. The three contexts are the
`name:` fields of the gate jobs in `ci.yml`; they must match exactly
or the branch blocks forever on a check that never reports.

## Steps

Ordering is the substance of this phase. The token must be proven
before the ruleset starts blocking `GITHUB_TOKEN` pushes, or ryll's
own automation is locked out of the branch it maintains.

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 3a | low | sonnet | none | Point `prune-reviews.yml`'s checkout at `DEPENDENCIES_TOKEN`, guard the job to `refs/heads/develop`, and rewrite the header comment to match kerbside's. Done in this branch. |
| 3b | — | — | — | Merge this branch's PR the normal way, while the ruleset is still permissive. The merge fires `prune-reviews` on develop with the new token, and that run has real work to do (editing `prune-reviews.yml` stales the `.github/workflows` directory mark), so it exercises `git push origin develop` as shakenfist-bot for real. |
| 3c | — | — | — | Confirm that post-merge run is green before going near the ruleset. If the PAT lacks write scope it fails here, with the branch still unlocked and `GITHUB_TOKEN` pushes still legal. |
| 3d | medium | opus | none | Operator approves and runs the `gh api` call above, then re-reads the ruleset to confirm it round-tripped. |
| 3e | low | sonnet | none | Trigger `export-repo-config.yml` so the ruleset lands under `.github/exported-config/` and the consistency audit sees it. |

## Validation

* The token was partly proven on 2026-08-10 by dispatching
  `prune-reviews` on this branch: `actions/checkout` and the
  development clone both succeeded, so the PAT is valid and can read.
  That dispatch then failed, and the failure is why the job now
  carries a `refs/heads/develop` guard — see below. Write scope
  remains unproven until step 3b.
* Do **not** try to prove the token by dispatching this workflow on a
  branch. `ci-prune-reviews.sh` rebases onto develop and pushes to
  develop regardless of the checked-out ref, so a dispatch on a
  branch attempts to push that branch's unmerged commits to develop.
  On 2026-08-10 only an add/add rebase conflict stopped exactly that
  from happening. The job's `if:` guard now makes such a dispatch a
  no-op, and step 3b uses the ordinary post-merge run instead.
* After 3d, a trivial PR should: run the smoke tier, go green on
  `Can enqueue`, enqueue, run the merge tier in the queue, go green
  on `Can merge`, and merge.
* A review-only PR (touching only `REVIEWS.md`) should pass both
  gates via the skipped-counts-as-success path without running a
  single tier job.
* After the first queued merge lands, `prune-reviews` should run and
  push as shakenfist-bot without being rejected by the ruleset.
* `gh api repos/shakenfist/ryll/rules/branches/develop` should list
  `merge_queue`, `pull_request`, `required_status_checks`,
  `deletion` and `non_fast_forward`.

## Risks and notes

* If the gate contexts are misspelled, every PR blocks on a check
  that never reports. The recovery is to re-run the `PUT` with a
  corrected array, or to set `enforcement: "evaluate"` temporarily.
* The bypass team includes mikalstill, so a human can always push
  past a wedged ruleset. That is the escape hatch, and it is the
  reason the team exists.
* `pr-retest.yml` dispatches `ci.yml`, which runs both tiers. That is
  unchanged by this phase, but a dispatch run does not report to a
  queued entry — retesting a queued PR means removing it from the
  queue and re-adding it.
* Every prune commit moves develop, and the queue rebuilds entries
  when the base moves. With `max_entries_to_build: 1` and a single
  developer this is mostly harmless, but it is the thing to watch if
  queue rebuild churn shows up later.
* The push-to-develop-from-any-ref hazard fixed here by a job guard
  exists in kerbside's copy of this workflow too, and the underlying
  `git push origin develop` lives in a script that mirrors one in
  shakenfist/development. Fixing it fleet-wide — most likely in the
  script rather than in each workflow — is worth raising separately;
  it is deliberately not done here to avoid diverging ryll's copy of
  a shared script mid-phase.
