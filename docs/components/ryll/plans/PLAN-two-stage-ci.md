# Two-stage CI with a merge queue

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll CI configuration thoroughly. Read
`.github/workflows/` (especially `ci.yml`, `supply-chain.yml`,
`codeql-analysis.yml`, `pr-retest.yml`, and
`export-repo-config.yml`), the `Makefile` (all cargo work is
wrapped in the devcontainer — see the `feedback_makefile_for_cargo`
convention), and `.devcontainer/Dockerfile`. Ground your answers
in what the workflows actually do today. Do not speculate when
you could read the file instead.

The reference implementation for the two-tier scheme is
`shakenfist/shakenfist`'s `.github/workflows/functional-tests.yml`
(a sibling clone usually exists at `../shakenfist`), which
implements the can_enqueue / can_merge pattern described at
<https://boinkor.net/2023/11/neat-github-actions-patterns-for-github-merge-queues/>.
The reference ruleset is that repository's "Develop branch"
ruleset (`gh api repos/shakenfist/shakenfist/rulesets/2681531`).

All planning documents should go into `docs/plans/`.

When we get to detailed planning, I prefer a separate plan file
per detailed phase. These separate files should be named for the
master plan, in the same directory as the master plan, and simply
have `-phase-NN-descriptive` appended before the `.md` file
extension. Tracking of these sub-phases should be done via the
table in the Execution section below.

I prefer one commit per logical change, and at minimum one commit
per phase. Do not batch unrelated changes into a single commit.
Each commit should be self-contained: it should build, pass
tests, and have a clear commit message explaining what changed
and why.

## Situation

A ryll pull request currently runs approximately fifteen CI jobs
across three workflows:

* `ci.yml` — lint (fmt + clippy), four fuzz jobs (each a full
  independent build of the detached `fuzz/` workspace with a
  90-minute timeout), the self-hosted Linux x86_64 build (release
  build, two `--web` smoke tests, unit tests, .deb, .rpm), four
  GitHub-hosted cross-platform builds (aarch64 Linux, macOS
  aarch64, Windows x86_64, Windows aarch64 — the Windows builds
  use `--no-default-features`), and the automated reviewer.
* `supply-chain.yml` — cargo-audit, cargo-deny, gitleaks,
  shellcheck, and the bidi/zero-width scan, all on small
  self-hosted runners, plus a weekly cron for advisory drift.
* `codeql-analysis.yml` — CodeQL analysis.

The recurring failure mode is that the cheap checks fail (most
often `cargo deny` on advisory drift) while the eight heavy jobs
— fuzz and the cross-platform builds — run to completion anyway.
Most of the compute in a failed CI run is wasted.

`shakenfist/shakenfist` solves this with a two-tier scheme built
on GitHub merge queues: cheap jobs run on `pull_request` and gate
a `Can enqueue` check; expensive jobs run only on `merge_group`
and gate a `Can merge` check. Both gates use `always()` plus a jq
expression over the `needs` context that treats skipped
dependencies as success, so docs-only changes pass through
quickly. The `develop` ruleset requires three status checks (`Can
see status`, `Can enqueue`, `Can merge`) and enables the merge
queue with ALLGREEN grouping.

ryll-specific constraints that shape the port:

* ryll's `develop` has **no required status checks and no merge
  queue today** — its ruleset (`Protect default branch history`)
  only blocks deletion and force-push. `ci.yml` and
  `codeql-analysis.yml` skip review-only changes with a
  workflow-level `paths-ignore`, and the header comment in
  `ci.yml` is explicit that this is only safe *because* nothing
  is required. Adding required checks without converting the
  paths filter would leave review-only PRs blocked forever on
  "expected" checks.
* The Linux x86_64 jobs run inside the devcontainer via `make`
  on self-hosted runners (issue #201 tracks workflow-standards
  consistency; the GitHub-hosted matrix jobs carry `audit-ok`
  annotations that must survive the restructure).
* `pr-retest.yml` re-triggers CI via `gh workflow run ci.yml`,
  so `ci.yml` must keep `workflow_dispatch` and a dispatch run
  should exercise both tiers (matching shakenfist, where
  `workflow_dispatch` runs everything).
* The Windows builds have unique signal (the
  `--no-default-features` graph and windows-sys churn break in
  ways Linux cannot see), but the runners are the slowest in the
  pipeline, which makes a cross-target `cargo check` from the
  Linux devcontainer a plausible cheap smoke substitute. (This
  bullet originally claimed `--no-default-features` drops the
  opus/cmake native build. The phase 2 spike disproved that:
  only `gui` is feature-gated in `ryll/Cargo.toml`, and
  `shakenfist-spice-webrtc` — an unconditional dependency —
  depends on `opus` directly. The cross-check still works, it
  just cross-compiles more than expected.)
* `PLAN-ci-platform-matrix.md` (not started) plans macOS/Windows
  *runtime* smoke coverage; any jobs it adds later should land in
  the merge tier defined here.

## Mission and problem statement

Restructure ryll CI into two tiers modelled on
`shakenfist/shakenfist`, so that pull request iteration runs only
the cheap, high-signal checks on our own hardware, and the
expensive jobs run exactly once per change, in the merge queue:

* **Smoke tier** (`pull_request`, gates `Can enqueue`): lint, the
  self-hosted Linux build (which already carries the unit tests,
  `--web` smokes, and .deb/.rpm packaging), the five supply-chain
  scanners folded in from `supply-chain.yml`, and — if the phase
  2 spike succeeds — a cross-target `cargo check` for Windows.
* **Merge tier** (`merge_group`, gates `Can merge`): the four
  fuzz jobs and the four cross-platform builds.

Enable the merge queue on `develop` with required checks, without
breaking the review-only-change fast path, the retest bot, or the
`export-repo-config` audit trail. CodeQL remains a separate,
non-required workflow (matching shakenfist).

## Open questions

1. **Merge queue parameters.** *Resolved 2026-08-09.* ryll
   copies shakenfist's live ruleset (2681531) as updated that
   day: ALLGREEN, `merge_method: MERGE`,
   `max_entries_to_build: 1`, `max_entries_to_merge: 5`,
   `min_entries_to_merge: 1`,
   `min_entries_to_merge_wait_minutes: 5`,
   `check_response_timeout_minutes: 360`.

   Two mechanics inform these values. First, with
   `min_entries_to_merge: 1` the wait timer never engages — it
   is the timeout for *reaching the minimum group size*, and a
   single entry already satisfies a minimum of 1 — so each
   entry merges as soon as its merge group is green; the queue
   builds one merge group and one CI run per entry regardless,
   so batched merging (`min_entries_to_merge >= 2`) would add
   latency and save nothing. Second, `max_entries_to_build: 1`
   deliberately disables speculative stacking: with concurrency
   2, a second queued entry builds stacked on the first, and any
   failure ahead of it ejects that work and rebuilds the group on
   a new SHA. shakenfist's merge-tier failures are mostly
   CI-cluster load, so stacked speculative builds both wasted
   runs (observed: entries rebuilt five times in a day) and
   added the very load that causes the failures. A serialized
   queue trades peak throughput for no wasted runs and lower
   cluster load; that is the right trade for a single-developer
   project with a loaded CI cluster, and applies equally to
   ryll's self-hosted merge-tier jobs.
2. **Scanner cron scope.** The weekly cron exists to catch
   advisory drift, which only affects cargo-audit and cargo-deny.
   Recommendation: the residual scheduled workflow keeps only
   those two jobs; gitleaks/shellcheck/bidi only change when the
   tree changes, and every PR runs them.
3. **Windows cross-check feasibility.** *Resolved 2026-08-09 by
   the phase 2 spike.* The answer was neither of the two outcomes
   anticipated here. `x86_64-pc-windows-msvc` — the triple CI
   actually builds — cannot be checked from Linux: `cargo check`
   runs build scripts, and `aws-lc-sys` compiles vendored
   BoringSSL C for the target, which needs an MSVC toolchain
   (`xwin` plus `clang-cl`) to work. `x86_64-pc-windows-gnu`
   does work, in about 24 seconds, once mingw-w64 is added to
   the devcontainer image. That triple is a proxy: it shares the
   `cfg(windows)`/`windows-sys` surface that breaks in practice,
   but not `target_env = "msvc"` breakage, link failures, or
   anything aarch64-specific. We take the proxy as a smoke-tier
   step and leave the merge tier as the authoritative Windows
   signal. See
   [PLAN-two-stage-ci-phase-02-windows-check.md](/components/ryll/plans/PLAN-two-stage-ci-phase-02-windows-check/).
4. **Post-merge builds.** With a `pull_request` ruleset rule and
   the merge queue, direct pushes to `develop` stop happening, and
   the queue tests the exact merge commit that lands. The `push:
   branches: [develop]` trigger on `ci.yml` then only re-runs
   what the queue just ran. Recommendation: drop it; artifacts
   for a develop SHA come from its merge-group run. CodeQL keeps
   its push trigger (the security tab wants a default-branch
   baseline).
5. **Docs-only fast path.** shakenfist's `check_paths` also skips
   heavy tests for docs-only changes. ryll's current skip list is
   only the review-artefact files. Extending it to `docs/**` is
   attractive but changes behaviour; deferred to Future work
   rather than bundled into this restructure.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Two-tier ci.yml | [PLAN-two-stage-ci-phase-01-workflow-tiers.md](/components/ryll/plans/PLAN-two-stage-ci-phase-01-workflow-tiers/) | Complete (PR #255, merged 2026-08-09) |
| 2. Windows cross-check spike | [PLAN-two-stage-ci-phase-02-windows-check.md](/components/ryll/plans/PLAN-two-stage-ci-phase-02-windows-check/) | Complete (PRs #256 and #257, merged 2026-08-10) |
| 3. Merge queue enablement | [PLAN-two-stage-ci-phase-03-merge-queue.md](/components/ryll/plans/PLAN-two-stage-ci-phase-03-merge-queue/) | In progress: token change written, ruleset change awaiting approval |
| 4. Documentation | PLAN-two-stage-ci-phase-04-docs.md | Not started |

### Phase 1: Two-tier ci.yml

Restructure `ci.yml` into the two tiers and gates; absorb the
scanner jobs from `supply-chain.yml`; convert the paths filter.
This phase lands before any ruleset change and must be a no-op
for merge behaviour (nothing is required yet).

* Triggers become `pull_request` (develop), `merge_group`, and
  `workflow_dispatch`; the `push` trigger and the workflow-level
  `paths-ignore` are removed (see open questions 4 and the
  `check_paths` conversion below).
* Add `check_paths` (per shakenfist: `dorny/paths-filter@v4`,
  `predicate-quantifier: 'every'`, output defaulting to `'true'`
  on `workflow_dispatch` and `merge_group`) with the current
  review-artefact skip list: `REVIEWS.md`, `.vscode/*.weaudit`,
  `.vscode/*.weaudit-shas.json`, `.vscode/review-scope.toml`.
  Rewrite the now-stale "safe because no status checks are
  required" header comment.
* Smoke tier (condition: not `merge_group`, and
  `check_paths.outputs.code_changed != 'false'`): `lint`,
  `build-linux`, and the five scanners folded in from
  `supply-chain.yml` unchanged (runner labels, timeouts, and
  concurrency groups preserved).
* Merge tier (condition: `merge_group` or `workflow_dispatch`,
  same `check_paths` guard): the four `fuzz` matrix jobs and the
  four GitHub-hosted `build` matrix jobs, moved as-is including
  their `audit-ok` annotations.
* Gates copied from shakenfist: `can_see_status` (unconditional
  `run: true`), `can_enqueue` (`needs` all smoke jobs plus
  `check_paths`, `if: always() && github.event_name !=
  'merge_group'`, jq skipped-counts-as-success), `can_merge`
  (`needs` all merge-tier jobs plus `check_paths`, `if: always()
  && github.event_name == 'merge_group'`). All three on
  `[self-hosted, static]` with short timeouts.
* `automated_reviewer` keeps its `needs` gate but the list
  shrinks to smoke-tier jobs (`lint`, `build-linux`, scanners) —
  merge-tier jobs never run on `pull_request`, and a `needs` on a
  skipped job would skip the reviewer.
* `supply-chain.yml` slims to `schedule` + `workflow_dispatch`
  with only cargo-audit and cargo-deny (open question 2), with a
  header comment pointing at `ci.yml` for the PR-time scanners.
* Existing concurrency groups key on `github.ref`; merge-group
  refs (`gh-readonly-queue/...`) are unique per queue entry, so
  no change is needed, but verify none of the moved jobs can
  cancel a queue run.

### Phase 2: Windows cross-check spike

Answer open question 3 empirically, then implement or document.
The spike ran on 2026-08-09 and its results, together with the
resulting design, are in
[PLAN-two-stage-ci-phase-02-windows-check.md](/components/ryll/plans/PLAN-two-stage-ci-phase-02-windows-check/).

* The msvc triple is not cross-checkable from Linux without
  `xwin` and `clang-cl` (aws-lc-sys compiles vendored BoringSSL
  C for the target). Rejected as not cheap.
* The gnu triple works in ~24 seconds once mingw-w64 is in the
  devcontainer image, and exercises every native build in the
  graph including the cmake/libopus one.
* Implemented as: mingw-w64 plus the `x86_64-pc-windows-gnu`
  target in `.devcontainer/Dockerfile`, a `check-windows`
  Makefile target, and a `Cross-check Windows` smoke-tier job. It
  is a proxy for the merge tier's msvc builds, not a replacement
  for them.
* It started as a step inside `build-linux` and moved to its own
  job after PR #256 measured that folding it in cost three
  minutes of pull request feedback latency. See the phase plan.

### Phase 3: Merge queue enablement

Only after phase 1 has merged to develop.

* Extend the ryll `develop` branch protection modelled on
  shakenfist's "Develop branch" ruleset (ruleset 2681531):
  `merge_queue` rule with the parameters from open question 1, a
  `pull_request` rule (0 required approvals, merge methods as
  shakenfist), and `required_status_checks` naming exactly `Can
  see status`, `Can enqueue`, `Can merge` (integration_id 15368,
  GitHub Actions). Applied via `gh api`; decide during detailed
  planning whether to extend the existing history ruleset or add
  a second ruleset mirroring shakenfist's split.
* Trigger `export-repo-config.yml` (workflow_dispatch) so the
  ruleset change is captured under `.github/exported-config/` and
  the consistency audit sees it.
* Validate end-to-end with a trivial PR: smoke tier runs and
  `Can enqueue` goes green; enqueue; merge tier runs in the
  queue and `Can merge` goes green; the PR merges; a review-only
  PR (touching only `REVIEWS.md`) also passes through both gates
  via the skipped-counts-as-success path.

### Phase 4: Documentation

* Describe the two-tier model in `docs/development.md` (or a new
  `docs/ci.md` if the CI material has outgrown it): what runs on
  PR vs queue, how to read a queue ejection, how `@shakenfist-bot
  please retest` interacts with the tiers, where develop-SHA
  artifacts now come from.
* Update `AGENTS.md` (CI expectations for agents) and
  `ARCHITECTURE.md` only if it describes CI today; note the
  changed artifact provenance in `docs/releasing.md` if it
  references push-to-develop builds.
* Annotate `PLAN-ci-platform-matrix.md` so its future platform
  runtime smokes are specified as merge-tier jobs.
* Update this plan's phase table and `docs/plans/index.md`
  status.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this conversation) is
reserved for planning, review, and decision-making. This keeps
the management context lean and avoids drowning it in
implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and
   model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files — the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too
   light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with the
   result.

This applies to all steps, including high-effort ones. If a
sub-agent can't succeed even with a detailed brief and the right
model, that's a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental. The worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.

Two-stage-CI-specific notes:

* Workflow YAML edits are hard to test before they run. Every
  phase-1 change must pass `pre-commit run --all-files`
  (actionlint is configured via `.github/actionlint.yaml`), and
  the phase-1 review should diff job-by-job against both the old
  `ci.yml`/`supply-chain.yml` and shakenfist's
  `functional-tests.yml` gate jobs.
* Phase 3 mutates live repository settings. The sub-agent
  prepares and prints the exact `gh api` invocations; the
  management session reviews them and the operator approves
  before they are run. Nothing in phase 3 is committed until the
  exported config lands.

### Planning effort

The master plan itself should always be created at **high
effort**. Phase 1 should be planned at high effort (the gate
semantics — `always()`, skipped-as-success, event conditions —
have subtle failure modes that block merges repo-wide when wrong).
Phases 2 and 3 can be planned at medium effort with this master
plan as input; phase 4 is mechanical and can be planned at medium
effort.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making judgment
  calls, understanding non-obvious invariants, or researching
  external references. The sub-agent needs to think carefully
  about edge cases.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read a few
  files but the approach is well-defined.
- **low** — Purely mechanical changes (rename, reformat, add a
  log line). The brief is a complete instruction.

**Model choice:** The planner should recommend which model is
best suited for each step. For this plan specifically: the
phase-1 `ci.yml` restructure is a single large file where getting
event conditions wrong is costly — skew to opus there. The
phase-2 spike and phase-4 docs are well-briefed, bounded tasks
where sonnet is appropriate. When in doubt, skew to the more
capable model: a failed or low-quality implementation wastes more
time than a heavier model costs.

**Brief for sub-agent:** This is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include:
what to change, which files to touch, what patterns to follow,
and any non-obvious constraints. Front-load the research this
master plan already did — for example, name the exact shakenfist
jobs (`can_see_status`, `can_enqueue`, `can_merge` in
`functional-tests.yml`) a gate job should be copied from, rather
than asking the agent to rediscover the pattern.

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] `pre-commit run --all-files` passes (this covers
      actionlint for the workflow files).
- [ ] For workflow changes: every job present before the
      restructure is accounted for — moved, folded, or
      deliberately removed with the removal recorded in this
      plan.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions (including the
      Co-Authored-By line with model, context window, effort
      level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* A pull request touching source code runs only the smoke tier:
  lint, the self-hosted Linux build, the five scanners, and (if
  phase 2 succeeded) the Windows cross-check. No fuzz or
  cross-platform build jobs start on `pull_request`.
* A merge queue entry runs the merge tier (fuzz ×4, platform
  builds ×4) and merges only when `Can merge` passes.
* A failing `cargo deny` blocks enqueue while consuming only
  small self-hosted runners plus the one Linux build.
* A PR touching only review artefacts (`REVIEWS.md`,
  `.vscode/*.weaudit*`, `.vscode/review-scope.toml`) passes both
  gates via skipped jobs and can merge.
* `@shakenfist-bot please retest` still triggers a full
  `workflow_dispatch` run covering both tiers.
* The `develop` ruleset changes are captured under
  `.github/exported-config/` by the export workflow.
* `pre-commit run --all-files` passes on every commit.
* `docs/` describes the two-tier model, and `AGENTS.md` reflects
  the new CI expectations.

### Future work

* Extend `check_paths` to a docs-only fast path (`docs/**`,
  `mkdocs`-adjacent files) as shakenfist does, once the two-tier
  scheme has bedded in (open question 5).
* The macOS/Windows *runtime* smoke coverage planned in
  `PLAN-ci-platform-matrix.md` — to land as merge-tier jobs.
* A real coverage-guided fuzz campaign (issue #135) remains
  separate from the merge-tier build-and-smoke fuzz jobs.
* Consider automating cargo-deny advisory-drift handling (the
  weekly cron currently just fails; a bot-filed issue with the
  advisory details would be friendlier).
* Add an actionlint hook to `.pre-commit-config.yaml`: the repo
  carries a `.github/actionlint.yaml` config but nothing in-repo
  invokes actionlint (phase 1 validated the workflow edits by
  running `rhysd/actionlint` via Docker by hand). Possibly a
  fleet-wide gap worth a shakenfist/development consistency
  audit.
* If merge-queue ejections for Windows breakage become common
  and the phase-2 spike failed, revisit a Windows-native smoke
  job (e.g. `cargo check` on a GitHub-hosted Windows runner,
  which is still far cheaper than the full build).

### Bugs fixed during this work

Nothing yet. Related tracker state reviewed at planning time:

* Issue #201 (workflow-standards consistency, self-hosted
  runners) — the restructure must preserve the `audit-ok`
  annotations on the GitHub-hosted matrix jobs and keep new gate
  jobs on self-hosted runners, so the consistency audit stays
  green. This plan does not close #201 but must not regress it.
* Issue #135 (broaden fuzz coverage) — unaffected; noted under
  Future work.

### Documentation index maintenance

`docs/plans/index.md` and `docs/plans/order.yml` were updated
when this plan was created. When all phases are complete, update
the status column in `index.md` to *Complete*.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
