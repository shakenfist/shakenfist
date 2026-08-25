# Review coverage steady state

Ryll is about to reach 100% human-reviewed under the code review
tracking system (`docs/code-review-tracking.md`). This plan covers
the steady state that follows: automatically pruning review marks
that go stale as PRs merge, and alerting when the review backlog
grows large enough that a review session is warranted.

## Situation

The review tracking tooling (`scripts/review-tracking.py`) is
deliberately manual today: `prune` is run by hand after a pull as
part of the review session discipline. That was the right call for
the build-up phase, where every session started with a prune
anyway. In steady state it has two gaps:

* Between review sessions, nothing prunes. `REVIEWS.md` and the
  coverage number on ryll's main are only accurate as of the last
  session, and quietly overstate coverage as PRs merge.
* Nothing tells Mikal that the backlog has grown. The whole point
  of steady state is to come back and re-review changed files, but
  there is no signal for when.

Decisions already made in discussion (2026-08-02):

* **Threshold is absolute, not percentage**: alert when **5 or
  more** in-scope files need review. A percentage threshold at
  ryll's size (145 files) implied a ~14-file buffer, which is too
  large; "how much review work has piled up" is naturally an
  absolute number and survives repo-size changes and adoption by
  differently-sized repos.
* **Alerting rides the existing consistency audit** rather than a
  new bespoke workflow: `scripts/audit-check.py` already runs
  daily over a repo matrix including ryll, and
  `scripts/audit-manage-issues.py` already creates, updates,
  dedupes, and closes issues.
* **Coverage is computed against HEAD, not trusted from committed
  state**: the audit check must count marks whose stamped blob SHA
  still matches HEAD, independently of whether prune has run. This
  decouples alerting from pruning — if the prune automation ever
  breaks, the coverage number cannot be silently inflated.
* **Gating is structural**: the check applies only to repos where
  the review tooling is deployed, detected by the presence of
  `.vscode/review-scope.toml`. Currently that is only ryll.
  Kerbside is the likely next adopter, but only after the ryll
  experiment stabilises — nothing in this plan should hardcode
  ryll.
* **Prune is triggered by pushes to the default branch**, not a
  cron: staleness only ever appears when a commit lands there. The
  daily audit acts as a backstop signal even if a prune run is
  missed.
* **Prune commits directly to main** (as the bot, unsigned) rather
  than raising a PR. Prune is fail-safe by construction — it can
  only remove marks, never add or refresh them — and the
  attestations live in the signed stamp commits already in
  history, which a later unsigned commit cannot retroactively
  forge. This matches the existing pattern of the consistency
  audit's "Regenerate audit compliance tables." auto-commits. An
  automated PR was considered and rejected: it leaves `REVIEWS.md`
  wrong while waiting for merge, and an open PR touching
  `.vscode/*.weaudit*` conflicts with any review session committed
  in the meantime.

## Mission

When this plan is complete:

1. A merge to ryll's default branch (develop) that changes
   reviewed files results, within minutes, in an automated commit
   pruning the stale marks and regenerating `REVIEWS.md`.
2. The daily consistency audit computes ryll's effective review
   backlog against HEAD, and opens (or updates) a
   `Consistency: Human review coverage` issue on ryll when 5 or
   more in-scope files need review, closing it again when the
   backlog drops below 5. The issue body lists the files needing
   review, split into stale and never-reviewed, as a session work
   queue.
3. Repos without `.vscode/review-scope.toml` report the check as
   not applicable and are otherwise unaffected.
4. The documentation (`docs/code-review-tracking.md`, a new
   `audits/review-coverage.md`) describes the steady state,
   including why an unsigned prune commit does not weaken the
   attestation story.

## Design

### Phase 1: a `status` subcommand for review-tracking.py

Add a read-only `status` subcommand to
`scripts/review-tracking.py` alongside `stamp`/`prune`/`regen`/
`next`. It computes, without mutating any state:

* the set of in-scope tracked files (existing `load_scope()` /
  `in_scope()` / `tracked_files()` helpers);
* for each, whether it carries a **currently-valid** full-file
  review mark: marked in a `.weaudit` state file, stamped in the
  sidecar, and the stamped SHA equals `blob_sha('HEAD:path')`.
  This is exactly the staleness predicate `cmd_prune()` uses;
* the files needing review, categorised:
  - **stale**: marked reviewed, but the stamp is missing or no
    longer matches HEAD (a mark without a stamp cannot be
    verified, so it is conservatively treated as needing review);
  - **never reviewed**: in scope with no full-file mark at all.
  Partial (region) marks do not count as reviewed, consistent
  with how `generate_reviews_md()` counts coverage.

Output: human-readable summary by default; `--json` emits a
machine-readable object for the audit check, e.g.:

```json
{
  "in_scope": 145,
  "reviewed": 140,
  "needing_review": 5,
  "stale": ["ryll/src/app.rs", "..."],
  "never_reviewed": ["ryll/src/new_thing.rs"]
}
```

Notes:

* `status` differs from the `REVIEWS.md` header line ("N of M in
  scope files are currently reviewed"), which counts marks
  without checking them against HEAD and is therefore only
  accurate immediately after a prune. That difference is the
  point — see the design decision above. Do not change the
  `REVIEWS.md` computation.
* Files with a stamp but no longer tracked at HEAD (deleted)
  simply drop out of both the denominator and the needing-review
  list; prune cleans up their marks as it does today.
* `load_scope()` imports `tomllib`, so `status` (like the other
  subcommands) needs Python 3.11+. The static self-hosted runners
  are Debian 12 (Python 3.11), so this is already satisfied, but
  verify during implementation.

Tests go in `scripts/test_review_tracking.py`, following its
existing fixture patterns: valid mark counted, stale mark counted
as needing review, unstamped mark counted as needing review,
partial mark not counted as reviewed, scope filtering, JSON
output shape.

### Phase 2: the `review-coverage` audit check

In `scripts/audit-check.py`:

* New `check_review_coverage(repo_path, props)`:
  - If `.vscode/review-scope.toml` does not exist in the target
    repo: `not_applicable`, details "Human review tracking not
    deployed (no .vscode/review-scope.toml)".
  - Otherwise run `review-tracking.py status --json` as a
    subprocess with `cwd=repo_path`, locating the script relative
    to `__file__` (the audit always runs from a development-repo
    checkout, so the script is a sibling). Do not go through the
    target repo's `tools/review-tracking.sh` wrapper — the
    wrapper searches for a development clone the runner does not
    have in the expected places.
  - `fail` when `needing_review >= REVIEW_BACKLOG_THRESHOLD`
    (a module-level constant, 5, with a comment recording that
    the value is a tuning knob agreed 2026-08-02).
  - `pass` otherwise. Details always include the counts, e.g.
    "3 of 145 in-scope files need review (threshold 5)".
  - On failure, include a `missing` key listing the files needing
    review, stale first, each prefixed with its category (e.g.
    `stale: ryll/src/app.rs`). `build_issue_body()` in
    `audit-manage-issues.py` already renders `missing` as a
    bullet list, so the issue body becomes the session work
    queue with no changes to the issue machinery.
* Register the check in `run_all_checks()`.

The depth-1 clone made by the audit workflow contains the full
tree and blobs at HEAD, which is all `status` reads — no workflow
change is needed.

In `scripts/audit_common.py`:

* `AUDIT_METADATA['review-coverage'] = {'spec':
  'audits/review-coverage.md', 'template': None}`.
* `ISSUE_TITLES['review-coverage'] = 'Human review coverage'`.

New `audits/review-coverage.md`, modelled on the existing single-
check specs (e.g. `audits/delete-branch-on-merge.md`):

* What we check: repos with review tracking deployed must have
  fewer than 5 in-scope files needing review, computed against
  HEAD via `review-tracking.py status`.
* How to become compliant: run a review session (link to
  `docs/code-review-tracking.md`).
* The `<!-- consistency-audit:begin/end -->` marker block so
  `audit-update-docs.py` maintains the compliance table. Expected
  steady state: ryll compliant or non-compliant as backlog moves,
  every other repo N/A.

Also add the new audit to the criteria lists in
`PROJECT-CONSISTENCY-AUDITS.md` and `PLAN-consistency.md`,
matching how the other audits are described there.

Tests in `scripts/test_audit_check.py`, following its fixture
patterns: not_applicable without a scope config; pass/fail either
side of the threshold (fixture repo needs `git init`, a scope
config, weaudit state, sidecar, and committed files so blob SHAs
resolve); `missing` list contents and ordering.

Behavioural notes, to verify rather than assume during
implementation:

* `audit-manage-issues.py` updates an existing open issue rather
  than filing duplicates, and closes it when the check passes.
  This gives the desired open/update/close lifecycle with no code
  changes. Expect routine churn: a single feature PR can easily
  touch 5 in-scope files, so the issue will often open shortly
  after a merge and close after the next session. That is
  accepted behaviour (a standing work-queue nudge); if it proves
  noisy the threshold is one constant, and hysteresis (open at 5,
  close below some smaller number) is noted as future work.
* The full needing-review list goes in the issue uncapped. At
  ryll's scale the worst case (~145 lines) is an acceptable issue
  body; revisit if a much larger repo adopts the tooling.

### Phase 3: documentation (development repo)

`docs/code-review-tracking.md`:

* Rewrite the "run by hand -- deliberately not from git hooks"
  framing: manual invocation remains the story for *stamp* (and
  for prune during review sessions), but prune now also runs from
  CI on pushes to main in adopting repos. The objection to git
  hooks (state changing mid-operation in a developer's clone)
  does not apply to a CI run against the repo's own main.
* New "Steady state" section covering: the prune workflow
  (trigger, what it commits, loop safety); the coverage audit and
  its threshold; and the attestation argument — prune only
  removes marks, attestation lives in the signed commits that
  introduced the stamps, and verifying a mark means verifying the
  signed commit that introduced it, so an unsigned automation
  commit removing marks weakens nothing.
* Session-discipline note: automated prunes now land on
  origin/main, so pulling (and reloading the weAudit view) before
  marking is load-bearing, not just hygiene.
* Adoption section: deploying to a new repo (kerbside next, after
  ryll stabilises) now also means copying the prune workflow and
  its tools/ script.

Update `AGENTS.md` / `ARCHITECTURE.md` in the development repo if
their descriptions of the audit or review tooling enumerate
checks or subcommands.

### Phase 4: the prune workflow (ryll repo, separate PR)

This phase lands in shakenfist/ryll, referencing this plan. Note
ryll's default branch (and the branch carrying review state) is
`develop`, not `main` -- discovered during implementation; the
workflow and script below target develop.

* `.github/workflows/prune-reviews.yml`:
  - `on: push: branches: [develop]` plus `workflow_dispatch`.
  - Top-level `permissions: {}`; job-level
    `permissions: contents: write` (workflow-standards audit).
  - `runs-on: [self-hosted, static]`.
  - A `concurrency` group (e.g. `prune-reviews`) so overlapping
    merges serialise rather than race the push.
  - Steps: checkout ryll; clone shakenfist/development at depth 1
    into a temp path and export `SHAKENFIST_DEVELOPMENT`; run
    `tools/ci-prune-reviews.sh`.
* `tools/ci-prune-reviews.sh` (scripts over five lines do not
  live inline in workflow steps), mirroring the development
  repo's `scripts/commit-audit-docs.sh`:
  - `./tools/review-tracking.sh prune`
  - if `git diff --quiet -- .vscode/ REVIEWS.md` shows no
    changes, exit 0;
  - otherwise commit as shakenfist-bot
    (`user.name 'shakenfist-bot'`, `user.email
    'bot@shakenfist.com'`) with message:

    ```
    Prune stale review marks.

    Automated commit by the prune-reviews workflow.
    ```

  - `git pull --rebase origin develop` then
    `git push origin develop` (same landing pattern as
    commit-audit-docs.sh).
* Loop safety: pushes made with the default `GITHUB_TOKEN` do not
  trigger workflows; even if the trigger changed, a second prune
  run is a no-op and commits nothing.
* Interaction with review sessions: a push of session commits
  (stamps) triggers a prune run that finds nothing stale and
  exits quietly. No conflict.
* Update ryll's `tools/review-tracking.sh` header comment and
  ryll's `AGENTS.md` (and `ARCHITECTURE.md` if it mentions the
  review tooling) to note that prune now also runs automatically
  on main. `REVIEWS.md` needs no change (generated).
* Shellcheck the new script (`tools/run-shellcheck.sh` /
  pre-commit), and actionlint via pre-commit for the workflow.

### Phase 5: end-to-end verification

After the development PR merges:

1. `workflow_dispatch` the consistency audit; confirm ryll's row
   in `audits/review-coverage.md` regenerates, every other repo
   shows N/A, and no issue is filed while the backlog is under 5.

After the ryll PR merges:

2. The merge itself should trigger the first prune run (the PR
   touches in-scope files: `tools/`, `AGENTS.md`), pruning their
   marks and committing. Verify the bot commit and regenerated
   `REVIEWS.md` look right, and that no workflow loop occurs.
3. Simulate backlog: after enough merges (or by temporarily
   lowering the threshold in a test run), confirm the issue is
   created with the file list, and closes after a review session
   restores coverage.
4. Confirm a normal review session still works end to end: pull
   (picking up bot prunes), review, stamp, signed commit, push.

### Phase 6: Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of every phase in
this plan against `main`, not over the last phase's diff alone --
the interactions between phases are most of what a whole-plan
audit is for. This plan spans two repositories, so the audit runs
once per pull request, each against its own default branch.
Findings land as their own pull request; the plan is not complete
until each is resolved or declined in writing, with the reason
recorded here. If the audit finds nothing, say so in one sentence.

## Execution

One commit per logical change; the development-repo work is one
PR on this branch, the ryll work a separate PR.

| Step | Repo | Description | Status | Merged |
|------|------|-------------|--------|--------|
| 1 | development | `status` subcommand + tests | Done | `b677b61` (#11) |
| 2 | development | `check_review_coverage` + registration + tests | Done | `b677b61` (#11) |
| 3 | development | `audit_common.py` metadata + `audits/review-coverage.md` | Done | `b677b61` (#11) |
| 4 | development | `PROJECT-CONSISTENCY-AUDITS.md` + `PLAN-consistency.md` entries | Done | `b677b61` (#11) |
| 5 | development | `docs/code-review-tracking.md` steady-state rewrite | Done | `b677b61` (#11) |
| 6 | ryll | prune workflow + `tools/ci-prune-reviews.sh` + docs | Done | ryll `1e94d00f` (#236) |
| 7 | both | end-to-end verification (phase 5) | Blocked on merge | |
| 8 | both | push audit over each PR (phase 6) | Not started | |

The `Merged` column is what `plan-push-audit-phase` asks each phase
to record as it lands, so that phase 6 has a range to audit once
`git diff main` for this plan is empty. Steps 1--5 were one pull
request, which is why they share a commit; step 6 is in a different
repository against a different default branch, so it names its own.
These were reconstructed after the fact rather than recorded live,
and reconstructing step 6 turned up the reason v2 stopped trusting
derivation: the two follow-ups to the prune workflow
(`a0227e05`, `196db2f6`) reached `develop` inside ryll#262, an
unrelated merge-queue pull request, so no range anchored on this
plan's own commits contains them.

`pre-commit run --all-files` must pass before each commit is
proposed; the Python follows the house style (single quotes,
120-column wrap).

## Success criteria

* `review-tracking.py status` reports correct effective coverage
  on a fixture repo and on ryll itself, and mutates nothing.
* `scripts/test_review_tracking.py` and
  `scripts/test_audit_check.py` pass, with new cases covering the
  scenarios listed above.
* A daily audit run on the current matrix produces: ryll pass (or
  fail with a correct file list), all other repos N/A, and a
  correctly-managed issue lifecycle.
* A merge to ryll main is followed by exactly one bot prune
  commit when marks went stale, and none when they did not.
* The attestation verification story in
  `docs/code-review-tracking.md` remains true end to end.

## Future work

* Adopt the tooling (and prune workflow) in kerbside once the
  ryll experiment stabilises.
* A consistency check that repos with
  `.vscode/review-scope.toml` also carry the prune workflow, so
  the two halves of the steady state cannot drift apart as more
  repos adopt.
* Hysteresis on the threshold (open at 5, close lower) if the
  issue churn from routine merges proves noisy.
* Revisit capping the issue-body file list if a repo much larger
  than ryll adopts the tooling.
