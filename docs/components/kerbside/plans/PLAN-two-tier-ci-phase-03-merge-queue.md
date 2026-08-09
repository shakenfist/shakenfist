# Two-tier CI phase 3: merge queue adoption and tier split

Phase 3 of [PLAN-two-tier-ci.md](/components/kerbside/plans/PLAN-two-tier-ci/). This is the
phase that actually delivers the "two tier" in two-tier CI: phases 1
and 2 only strengthened and grew the lanes that run on pull requests;
nothing has yet moved *out* of the PR tier, and nothing actually
*gates* a merge.

## Prompt

"What is the next step with this plan? We've **added** CI, but we
haven't actually implemented a two tier approach to CI yet."

## Situation

### What gates merges today: nothing

Kerbside's only branch protection is the "Protect default branch
history" ruleset (id 20252051): `deletion` and `non_fast_forward`.
There are **no required status checks and no merge queue** — the PR
rollup is purely advisory and the maintainer merges by hand when it
looks green. Phase 3 is therefore not "re-wire required checks"; it
introduces merge gating to this repository for the first time.

### What runs on PRs today

- `functional-tests.yml`: `sanity_checks` (lint + unit, m runner)
  → `ovirt_matrix` (l, ~2h) and `openstack_matrix` (m, hours) in
  parallel → `automated_reviewer` (needs all three). Review-only
  changes are skipped via trigger-level `paths-ignore` (PR #254).
- `direct-qemu-functional.yml`: `direct-qemu-lane` (l, ~1h), same
  `paths-ignore`.
- `sf-e2e-functional.yml`: `sf-e2e` (l, ~17–35m), same
  `paths-ignore` (phase 2).
- `rust.yml`: path-scoped to `rust/**` + the proto, so it only runs
  when Rust is touched.
- `codeql-analysis.yml`, `pin-indirect-dependencies.yml`: advisory.

The slow feedback problem is the two cloud matrices: every PR pays
for a full oVirt and a full OpenStack deployment.

### The fleet precedent (grounded 2026-08-09)

shakenfist/shakenfist has run exactly this design in production
since 2024, and client-python-k3s adopted the same shape
(`docs/plans/functional-ci.md` there is the conversion recipe for a
smaller repo). The pattern:

- **One workflow file**, both `pull_request` and `merge_group`
  triggers, tiers separated by `if: github.event_name` job gating —
  not separate workflow files. Aggregation requires this: the gate
  jobs `needs:` the tier jobs, and `needs:` cannot cross workflows.
- **Three aggregate gate jobs**, whose display names are the only
  lane-related required status checks:
  - `can_see_status` ("Can see status"): runs `true`
    unconditionally on every event. Its job is to guarantee the
    workflow always reports *something*, so a required check can
    never sit in "Expected — waiting for status" forever.
  - `can_enqueue` ("Can enqueue"): `if: always() &&
    github.event_name != 'merge_group'`; needs the smoke-tier jobs;
    passes iff every need ended `success` or `skipped` (a jq
    expression over `toJSON(needs)`).
  - `can_merge` ("Can merge"): `if: always() && github.event_name
    == 'merge_group'`; needs the merge-tier jobs; same jq.
  On a PR, "Can merge" reports `skipped`, which **satisfies** a
  required check; in a merge group, "Can enqueue" reports `skipped`
  likewise. That skipped-satisfies property is what lets one
  required-check list serve both refs.
- **A `check_paths` job replaces trigger-level `paths-ignore`**
  (dorny/paths-filter@v4 with `predicate-quantifier: 'every'`,
  output defaulting to `code_changed=true` on `workflow_dispatch`).
  This is load-bearing, not style: a required check belonging to a
  `paths-ignore`'d workflow never reports on a filtered PR, and a
  required check that never reports blocks the merge forever. With
  `check_paths`, the workflow always runs; heavy jobs skip on the
  filter output and the skip satisfies the requirement.
- **The ruleset** (shakenfist/shakenfist "Develop branch",
  id 2681531): `deletion`, `non_fast_forward`, `pull_request`
  (0 required approvals), `merge_queue` (merge_method `MERGE`,
  grouping `ALLGREEN`, `max_entries_to_build` 1,
  `min_entries_to_merge` 1, `max_entries_to_merge` 5,
  `min_entries_to_merge_wait_minutes` 5,
  `check_response_timeout_minutes` 360), and
  `required_status_checks` = the three gate names, integration
  15368 (the GitHub Actions app — the gates are ordinary Actions
  jobs, not an external app).
- **The conductor needs no changes.** private-ci provisions runners
  from queued jobs by label, and merge-group jobs request the same
  labels as PR jobs. Kerbside is not special-cased there today and
  does not become so.

## Design decisions

1. **Single workflow file, event-gated tiers.** The master plan
   sketched "move ovirt_matrix and openstack_matrix out of
   functional-tests.yml into a merge-tier workflow"; that sketch
   predates studying the fleet and is superseded. Keeping one file
   is what makes the gate jobs possible (`needs:` is same-file
   only), matches shakenfist/shakenfist and client-python-k3s, and
   keeps the pr-retest bot's `gh workflow run functional-tests.yml`
   working unchanged.
2. **Tier assignment.**
   - Smoke tier (every PR): `sanity_checks`, `direct-qemu-lane`,
     `sf-e2e`, plus advisory rust/codeql as today.
   - Merge tier (`merge_group` + `workflow_dispatch`):
     `ovirt_matrix` and `openstack_matrix`. This resolves master
     plan open question 2: the oVirt lane joined the merge tier,
     which phase 1 made defensible by turning it into a genuine
     integration gate; the schedule-only demotion remains the
     escape hatch if its flake rate ever dominates.
   - `sanity_checks` runs in **both** tiers: it is minutes on an m
     runner, the matrices already `needs:` it (fail-fast ordering
     worth keeping in the queue), and it re-validates lint/units
     against the *merged* tree, which no smoke run saw.
3. **Gate `needs:` lists must name every job whose failure should
   block — including jobs other needs already depend on.** The jq
   treats `skipped` as satisfied (required for path-skips), so a
   failure that manifests downstream as a skip is invisible: if
   `sanity_checks` fails in a merge group, the matrices skip, and a
   `can_merge` that needed only the matrices would go green on a
   broken tree. `can_merge` therefore needs
   `[sanity_checks, ovirt_matrix, openstack_matrix, check_paths]`,
   and `can_enqueue` needs `[sanity_checks, check_paths]`.
4. **`check_paths` replaces `paths-ignore`** in
   `functional-tests.yml`, `direct-qemu-functional.yml`, and
   `sf-e2e-functional.yml`, filtering on the same four
   review-tracking paths PR #254 skipped (`REVIEWS.md`,
   `.vscode/*.weaudit`, `.vscode/*.weaudit-shas.json`,
   `.vscode/review-scope.toml`) — behaviour parity, expressed as an
   inverse filter with `predicate-quantifier: 'every'`. Review-only
   PRs then pass all required checks via skips and can merge
   through the queue.
5. **The direct-qemu and sf-e2e lanes gate PRs through their own
   aggregate gates.** Each workflow gains a `merge_group` trigger,
   its own `check_paths`, and an aggregate gate job ("Can enqueue:
   direct-qemu" / "Can enqueue: sf-e2e") that is the required
   check; the heavy job runs only on `pull_request` (with code
   changes), so a merge group costs them nothing — the gates report
   `skipped` there, which satisfies the requirement. The lane jobs
   themselves must NOT be the required checks (the first review of
   PR #268 caught this): if `check_paths` failed, GitHub would
   report the lane as `skipped`, and a skipped required check
   counts as satisfied — a filter hiccup would silently green the
   smoke gate. The gate's `always()` + jq turns that failure into
   a visible red, exactly as decision 3 does inside
   functional-tests.yml. Without any of this, a red smoke lane
   would not stop a merge, and the smoke tier would still gate
   nothing.
   The merge queue deliberately does not re-run these lanes against
   the merged tree — a queue entry already costs two cloud
   deployments, and adding the 1-hour direct-qemu lane to every
   entry is the wrong trade on this cluster. The accepted residual
   risk is a semantic conflict between independently-green PRs in
   the proxy-path coverage only these lanes have; it is bounded by
   nightly schedules on both (direct-qemu gains one in this phase —
   03:30 UTC, offset from sf-e2e's 02:30 so the two l-runner lanes
   do not contend), so such a conflict surfaces within a day.
6. **`rust.yml` stays advisory.** It is deliberately path-scoped to
   `rust/**`, so as a required check it would deadlock Python-only
   PRs, and un-scoping it wastes an xl runner per PR. Rust
   breakage still gates merges: the ovirt and openstack merge-tier
   lanes both build and install the PR's proxy wheel (that build is
   what caught the 2026-08-02 tonic break).
7. **`automated_reviewer` re-anchors to
   `needs: [sanity_checks]`.** This is the master plan's stated
   deliverable and the human payoff: the review posts after
   minutes, not after the slowest cloud lane. The shared workflow
   already guards itself to same-repo `pull_request` events, so it
   never fires in a merge group.
8. **`SF_HEAD_SHA` gains a fallback**:
   `${{ github.event.pull_request.head.sha || github.sha }}`. The
   current expression is empty outside `pull_request` events (it
   already is on dispatch today); the fleet standardises on
   `github.sha` for non-PR refs.
9. **Concurrency groups are already merge-queue-safe.** Every group
   in the repo keys on `${{ github.ref }}`, and each merge-group
   ref (`refs/heads/gh-readonly-queue/develop/pr-N-...`) is unique,
   so queue entries can never cancel each other or a PR run. The
   master plan's concurrency worry is resolved by inspection.
10. **Ruleset parameters are copied from shakenfist/shakenfist**
    (values above) rather than re-derived. Notably `ALLGREEN` with
    `max_entries_to_merge` 5 means a stack of queued renovate PRs
    shares **one** merge-tier run instead of serialising a cloud
    deployment per dependency bump — resolving master plan open
    question 3. Renovate has no automerge enabled in this repo
    (minor/patch are `automerge: false`), so queue entry remains a
    human act for now; if automerge is ever enabled, renovate
    merges become "merge when ready" and batch the same way.
    `check_response_timeout_minutes` 360 comfortably covers the
    2-hour oVirt lane.

## Execution

| Step | What | Status |
|------|------|--------|
| 1 | Restructure `functional-tests.yml`: `merge_group` trigger, `check_paths`, event-gate the matrices, gate jobs, reviewer re-anchor | Complete |
| 2 | `direct-qemu-functional.yml` + `sf-e2e-functional.yml`: `merge_group` trigger, `check_paths`, skip-in-queue | Complete |
| 3 | Documentation: falsified statements only (AGENTS.md CI sections, master plan cross-offs); the full docs pass stays phase 4 | Complete |
| 4 | Operator: apply the ruleset change (below), then dispatch `export-repo-config.yml` to archive it | Not started |
| 5 | Live validation: merge a scratch PR through the queue; watch the first real merge group end to end | Not started |

Rollout order matters and is safe: the workflow changes land first
and are inert without a queue (`merge_group` triggers never fire,
gate jobs run and report on PRs, nothing is required yet). The
ruleset flip is the activation step and is instantly revertible in
the UI, independent of any workflow revert.

### Step 4: the operator ruleset change (manual, GitHub UI or API)

Edit the existing "Protect default branch history" ruleset (or
create a "Develop branch" ruleset mirroring shakenfist/shakenfist
and retire the old one — preferred, for fleet-consistent naming).
Target: `~DEFAULT_BRANCH`. Rules:

- `deletion`, `non_fast_forward` (keep, as today)
- `pull_request`: 0 required approvals, dismiss stale reviews on
  push, all merge methods allowed
- `merge_queue`: merge_method `MERGE`, grouping `ALLGREEN`,
  max_entries_to_build 1, min_entries_to_merge 1,
  max_entries_to_merge 5, min_entries_to_merge_wait_minutes 5,
  check_response_timeout_minutes 360
- `required_status_checks` (all integration 15368 / GitHub
  Actions; strict policy off):
  - `Can see status`
  - `Can enqueue`
  - `Can merge`
  - `Can enqueue: direct-qemu`
  - `Can enqueue: sf-e2e`

  These are display names of gate jobs; renaming any of them
  without updating the ruleset produces a required check that
  never reports, which blocks all merges. Each gate job carries a
  comment saying so, and `tools/check-required-checks.sh` (run by
  `sanity_checks`) asserts every context in the exported ruleset
  matches a workflow job name — it passes trivially until this
  step's change is exported, which is the natural ordering.
- `bypass_actors` (the first review round caught that omitting a
  bypass breaks prune-reviews; applying the change corrected the
  mechanism):
  - The shakenfist org team "SF Can Skip Merge Queue" (the same
    team shakenfist/shakenfist's ruleset trusts, id 11722172),
    `bypass_mode: always` — and this is the ONLY bypass actor.
    The originally-planned GitHub Actions app bypass (integration
    15368) is impossible: the API rejects it with "Actor GitHub
    Actions integration must be part of the ruleset source or
    owner organization" — the built-in Actions app is not an
    installable org app and cannot be a bypass actor, which is
    also why shakenfist/shakenfist's ruleset carries only the
    team bypass.
  - `prune-reviews.yml` — which lands its bot commit with a direct
    `git push origin develop` after every merge, has no PR to
    route through the queue, and would otherwise be rejected by
    the "require a pull request" rule — therefore pushes as the
    `shakenfist-bot` user (checkout `token:
    secrets.DEPENDENCIES_TOKEN`), with shakenfist-bot added to the
    bypass team (2026-08-09). Unlike a `GITHUB_TOKEN` push, a PAT
    push retriggers the workflow once; that is safe, not a loop —
    the second run finds nothing to prune and pushes nothing.
    Accepted side effect: a prune push landing while entries are
    queued invalidates the in-flight merge group and it rebuilds.

  No bypass is needed for export-repo-config.yml: despite the
  direct-looking single-parent bot commits on develop, those are
  rebase-merged "Repository configuration changed" PRs (e.g.
  commit 7a9e4e7 is PR #222) — the reusable workflow pushes a
  branch and opens a PR with `github.token`, verified against its
  source. The post-flip consequence is different: PRs created (or
  pushed to) with a workflow token get no `pull_request` events,
  so the nightly config PR and any bot-fixup push carry zero check
  runs and cannot enqueue until the PR is closed and reopened (or
  merged by a bypass actor). pr-address-comments.yml now says this
  in the comment it posts after pushing.

Then dispatch `export-repo-config.yml` so
`.github/exported-config/` archives the new ruleset.

### Step 5: validation plan

The gate jobs' PR-side behaviour is proven by this phase's own PR
(gates appear in the rollup; "Can merge" reports skipped). The
merge-group path cannot execute until the ruleset flips, so after
step 4, in order:

1. Queue a trivial code-touching scratch PR. Confirm entry requires
   the five checks, that the merge group runs `sanity_checks` +
   both matrices + `can_merge` — specifically that the matrices
   *run* rather than skip (re-confirming the fleet's
   dorny-on-merge_group evidence on this repo, now with the
   explicit `base: develop`) — that it merges on green, and that
   the prune-reviews push after the merge still lands (proving the
   shakenfist-bot team bypass).
2. Prove the negative path once: push a deliberately-broken commit
   to a scratch PR (e.g. a lint failure) and confirm "Can enqueue"
   goes red on the PR, and — if queued with a bypass — that
   `can_merge` goes red in the group. Nothing currently
   demonstrates a gate turning red in this repo; the fleet run
   above proves the jq, but one local demonstration is cheap.
3. Merge a review-marks-only PR through the queue and confirm every
   required check is satisfied by skips (the acceptance criterion).
4. When the next nightly "Repository configuration changed" PR
   appears, confirm the close/reopen dance attaches its checks and
   it merges through the queue — the export flow itself needs no
   bypass (it lands via PRs), but its PRs are created with a
   workflow token and so start with zero check runs.

`workflow_dispatch` still runs the matrices directly for lane
debugging, but note a dispatch run never attaches checks to a PR —
for a wedged PR rollup, close/reopen remains the reliable
retrigger. This also means the pr-retest bot's contract has
narrowed: `@shakenfist-bot please retest` dispatches
functional-tests.yml, which after this phase runs the *merge* tier
(with the dispatch default target) and attaches nothing to the PR's
blocking checks. Re-pointing the bot (plausibly at `gh run rerun`,
which does re-attach) belongs in the shared ci-review-automation
template in shakenfist/development, not this repo; until then,
close/reopen is the honest retest. If the queue itself wedges,
eject via the UI and consult the failure with the merge-ci-triage
skill; the 360-minute check timeout bounds how long a dead entry
can block the queue.

## Risks considered

- **Skip-masking in the gates** — addressed by decision 3 (direct
  `needs:` on every blocking job).
- **Review-only PR deadlock under required checks** — addressed by
  decision 4; this is the sharpest edge of the whole design, since
  the failure mode is a PR that can never merge.
- **dorny/paths-filter behaviour on `merge_group` refs** — only
  functional-tests.yml consults the filter there (the smoke
  workflows' filter jobs skip on the event entirely), and the
  comparison base is explicit rather than inferred:
  `base: develop`, the queue's target branch. That makes the
  dangerous outcome — a wrong `code_changed=false` greening "Can
  merge" on skips — structurally impossible, because develop by
  definition does not yet contain the queued PRs' changes; develop
  advancing mid-queue only grows the diff, erring toward running
  the tier. Fleet production evidence agrees: in
  shakenfist/shakenfist run 31258644604 (2026-08-08, a
  `merge_group` event) "Check paths" succeeded, the merge-tier
  collections ran rather than skipping, and "Can merge" went red
  on their failure — which also live-proves the gate jq's negative
  path. dorny/paths-filter also hardcodes `dot: true` in its
  matcher options (src/filter.ts), so `'**'` matches dot paths and
  a PR touching only `.github/**` counts as code. Step 5
  re-confirms on this repo's first code-carrying queue entry that
  the matrices run rather than skip.
- **Runner supply for merge groups** — none needed beyond today:
  merge-tier jobs request the same labels PR runs already use, and
  the conductor is label-driven.
- **Bootstrap paradox** — this phase's PR merges *before* the
  queue exists, so its merge_group code paths first execute during
  step 5's scratch PR, not on this PR. Accepted; the PR-side paths
  are exercised on this PR.

## Review follow-ups (PR #268)

The automated review's first round (2 fix, 2 document, 6 consider,
2 info) shaped the design above; the substantive outcomes:

- **Skip-masking in the smoke workflows (fix, taken):** the
  required checks were originally the bare lane job names, so a
  `check_paths` failure would have greened them via the
  skipped-satisfies rule. Decision 5 now uses per-workflow
  aggregate gates, and the ruleset list in step 4 names the gates.
- **paths-filter on merge_group (fix, resolved by evidence):** the
  reviewer flagged the functional-tests filter running on
  `merge_group` as unvalidated with a possible silent-bypass
  outcome. Fleet production evidence (risk list) shows both feared
  outcomes do not occur; the filter is kept because it is what
  lets review-marks-only queue entries skip the cloud lanes, and
  step 5 re-confirms locally.
- **No post-merge coverage for direct-qemu (consider, taken):**
  the lane gained a nightly schedule; the
  no-smoke-revalidation-in-queue trade is now recorded in
  decision 5 instead of implicit.
- **pr-retest contract narrowed (consider, documented):** see step
  5; the bot fix belongs in the shared template.
- **Reviewer event gating (consider, taken):**
  `automated_reviewer` now carries a local
  `if: github.event_name == 'pull_request'` rather than relying
  solely on the cross-repo guard.
- **Gate rename hazard (consider, taken):** every required-check
  job carries a do-not-rename comment naming the ruleset.
- **Unquoted `$ALL_SUCCESS` in the gate jq (consider, declined):**
  deliberately byte-identical with the shakenfist/shakenfist
  template; it fails closed either way. Change it fleet-wide or
  not at all.

Round 2 (2 fix, 2 doc, 5 consider, 3 info) — the substantive
outcomes:

- **prune-reviews vs the pull_request rule (fix, taken):** the
  ruleset spec originally had no bypass actors, which would have
  broken `prune-reviews.yml`'s direct bot push on the first
  post-flip merge. Step 4 now specifies the Actions-app and
  skip-queue-team bypasses, and step 5 validates the prune push.
- **Implicit paths-filter base on merge_group (fix, taken in
  spirit):** the filter now passes an explicit `base: develop`
  with the direction-of-error argument recorded in the risk list.
  The suggested belt-and-braces guard in `can_merge` was declined:
  its trigger condition (code changed but matrices skipped) can
  only arise when a direct need already turned the gate red, and
  the wrong-`false` case it aimed at is unreachable with an
  explicit develop base.
- **check_paths no-op on merge_group (consider, taken with a
  correction):** both smoke workflows now skip the whole filter
  job on merge_group. The reviewer's suggested condition
  (`== 'pull_request'`) would have skipped it on schedule and
  dispatch too, and a skipped need cascades — the nightly lanes
  would never have run again. Implemented as `!= 'merge_group'`.
- **Ruleset/job name drift (consider, taken):**
  `tools/check-required-checks.sh`, run from `sanity_checks`,
  mutation-tested in both directions.
- **direct-qemu concurrency + overstated schedule comment
  (consider, taken):** the lane gained the same superseded-push
  cancellation sf-e2e has, and the offset comment no longer reads
  as a guarantee.
- **Merge throughput unstated (consider, taken):** recorded below
  with the acceptance criteria.
- **pr-retest and plan-supersession docs (both doc items, taken):**
  pr-retest.yml now says honestly what a dispatch does and does
  not do, and the phase-8 / test-harness dispositions carry dated
  supersession notes.
- **Gate permissions inconsistency (consider, resolved by
  comment):** the fleet-mirrored `actions: read` is annotated as
  non-load-bearing rather than churned.

Round 3 (2 fix, 6 consider, 2 info) — the substantive outcomes:

- **export-repo-config vs the ruleset (fix, premise corrected):**
  the reviewer read the single-parent bot commits on develop as
  direct pushes needing a bypass. They are rebase-merged
  "Repository configuration changed" PRs (commit 7a9e4e7 is
  PR #222); the reusable workflow pushes a branch and opens a PR
  with `github.token`, so no bypass is needed. The real post-flip
  consequence — workflow-token PRs start with zero check runs — is
  recorded in step 4 and validated in step 5 item 4.
- **Bot-fixup pushes leave PRs unenqueueable (fix, taken
  minimally):** pr-address-comments.yml's success comment now
  tells the operator at the moment it matters that the push did
  not re-run the blocking checks and close/reopen attaches them.
  The PAT alternative changes the template's deliberate
  workflow-token security posture and belongs in the shared
  template with the pr-retest rework.
- **Dot-path matching in the filter (consider, resolved by
  evidence):** dorny/paths-filter hardcodes `dot: true`
  (src/filter.ts, `MatchOptions`), so `'**'` matches
  `.github/...` paths and the feared silent code_changed=false on
  dot-only PRs cannot occur. The suggested verdict echo step was
  taken in all three check_paths jobs for log visibility.
- **Nightly failure alerting (consider, taken):** both scheduled
  lanes gained a `nightly_failure_issue` job
  (tools/file-nightly-failure-issue.sh) that files or updates a
  fixed-title tracking issue on scheduled failure — a separate
  job so `issues: write` is never granted to a job that runs PR
  code.
- **check-required-checks.sh matching (consider, taken):** the
  grep is now anchored to job-level indentation and accepts
  unquoted or quoted names; re-mutation-tested, including the
  step-name false-pass the reviewer found.
- **jq install hardening (consider, taken by removal):** the
  script now parses the ruleset with python3, so the apt install
  step is gone entirely.
- **Superseded runs reporting red gates (consider, taken):** both
  smoke workflows moved concurrency to workflow level, so a
  superseded push cancels the gate along with the lane.
- **rust.yml tier membership in AGENTS.md (consider, taken).**

## Acceptance criteria

- A pull request runs sanity, direct-qemu, sf-e2e (and rust when
  touched) but **no cloud matrices**; PR feedback time drops from
  ~2 hours to the slowest smoke lane.
- Merging requires the merge queue; a queue entry deploys oVirt
  and OpenStack against the merged tree and blocks on failure.
- A review-marks-only PR merges through the queue with every
  required check satisfied by skips.
- The automated reviewer posts after the smoke tier, not after the
  cloud lanes.

Steady-state throughput expectation, to make the phase 4 review
concrete: a queue entry costs roughly two hours (sanity plus the
slower cloud matrix), and `max_entries_to_build` 1 serialises
entries, so the ceiling is on the order of 12 merges/day — with
entries queued inside the same 5-minute window batching into one
group of up to 5. That comfortably covers this repository's actual
merge rate (a few PRs on a busy day). The symptom that says this
design has gone wrong is a queue depth that never drains; the
levers, in order, are raising `max_entries_to_build` so entries
build speculatively in parallel, and enabling renovate automerge so
dependency bumps batch into shared entries as decision 10
anticipates.
