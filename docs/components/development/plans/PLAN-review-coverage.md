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
   backlog against HEAD, and opens a
   `Consistency: Human review coverage` issue on ryll when 5 or
   more in-scope files need review, closing it again when the
   backlog drops below 5. The issue body lists the files needing
   review, split into stale and never-reviewed, as a session work
   queue as it stood when the issue was filed. *Corrected on
   2026-09-17*: this said "opens (or updates)", which phase 5's
   survey found the issue machinery has never done -- see that
   phase's survey and D5.2.
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

* `audit-manage-issues.py` does not file duplicates, and closes
  the issue when the check passes. It was assumed here to *update*
  an open issue's body too; it does not, and never did --
  `process_results()` creates, dedupes and closes, and edits
  nothing (`scripts/audit-manage-issues.py:249-266`). The body is
  therefore the work queue as of the day the issue was filed. Phase
  5's survey measured how far that drifts; the fix belongs to
  whoever owns the issue machinery, not here. Expect routine
  churn: a single feature PR can easily
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
  run is a no-op and commits nothing. *Corrected on 2026-09-17*:
  implementation could not use `GITHUB_TOKEN`, because develop's
  ruleset requires a pull request and GitHub will not make the
  Actions app a bypass actor, so the push authenticates as
  shakenfist-bot via `DEPENDENCIES_TOKEN`. A PAT push *does*
  retrigger the workflow -- observed on 2026-09-11 -- so only the
  second half of this sentence holds the loop shut, and it does:
  the retriggered run finds nothing to prune and commits nothing.
  The same follow-up added an `if: github.ref ==
  'refs/heads/develop'` guard, after a `workflow_dispatch` on a
  branch tried to push that branch's unmerged commits to develop on
  2026-08-10. Both changes are in ryll `a0227e05` and `196db2f6`
  and are documented in that workflow's header comment.
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

Planned in detail on 2026-09-17; the rest of this section is that
plan. Planning effort: medium -- the phase changes no criterion, no
scheduler and nothing in `audit-manage-issues.py`; what it needed
was the survey, and the survey is done.

#### What the survey found

The premise of this section is that verification happens by staging
it: dispatch the audit, watch the ryll merge, simulate a backlog.
Six weeks passed between the phases landing and this planning, and
in that time production ran every one of those experiments by
itself. **Nothing in items 1--4 still needs to be staged.** What the
evidence shows, and the three claims it falsifies:

**The audit half works, and the lifecycle is verified including the
close.** The daily audit has run continuously (most recently
`https://github.com/shakenfist/development/actions/runs/35087963103`,
2026-09-16). The `review-coverage` table in
`docs/audits/compliance.md` regenerates with it. On ryll the issue
has opened and closed four times: #242 opened 2026-08-03 and closed
2026-08-05, #275 opened 2026-08-14 and closed 2026-08-15, #282
opened 2026-08-16 and closed 2026-08-17, #304 opened 2026-08-21 and
still open. #242 and #275 carry shakenfist-bot's "This check is now
passing in the automated consistency audit. Closing automatically."
comment, so the close path is machine-verified and not merely
plausible; #282 a human closed by hand. hunkydory#10 is the same
lifecycle in a third repository. Item 3's suggestion to lower the
threshold in a test run is therefore not just unnecessary but
unwanted: it would file real issues in other people's repositories
to demonstrate something already demonstrated.

**Item 1 names a file that no longer carries the table, and a state
of the fleet that has not been true since day one.** Compliance
moved out of the criterion specs into `docs/audits/compliance.md`
(`PLAN-audit-compliance-split`), so the row to look at is
`docs/audits/compliance.md#review-coverage`, not
`audits/review-coverage.md`. And "every other repo shows N/A" was
wrong the day it was written: the pull request that landed steps
1--5 was branch `kerbside-review-tracking`, so kerbside has had a
scope config since 2026-08-03. Today five repositories are in scope
for this criterion -- actions, development and hunkydory compliant,
kerbside and ryll not -- and sixteen are N/A.

**The prune half works, and holds the loop shut for a different
reason than this plan and the documentation give.** ryll's develop
carries 35 `Prune stale review marks.` commits by shakenfist-bot.
The workflow runs on every push and commits only when something went
stale. On 2026-09-11 a prune commit did retrigger the workflow (run
at 04:57:20, `displayTitle` "Prune stale review marks.") and that
run committed nothing: the push authenticates as a PAT, not
`GITHUB_TOKEN`, so the first half of phase 4's loop-safety sentence
is false and only the second half is load-bearing. Corrected in
phase 4 above. `docs/code-review-tracking.md` states the false half
as fact and step 5.1 fixes it.

**The issue body is never refreshed, and the drift is large.**
`process_results()` in `scripts/audit-manage-issues.py` creates,
dedupes and closes; it never edits an open issue
(`scripts/audit-manage-issues.py:249-266`). So the work queue is
frozen at the moment of filing. ryll#304 has said "63 need review"
since 2026-08-21 while the audit now measures 120 needing review,
and its spec link points at `development/audits/review-coverage.md`,
a path that stopped existing when the specs moved under `docs/`.
kerbside#227 has said "0 of 152 in-scope files reviewed" since
2026-08-03 while the audit now measures 114 reviewed of 229. This
plan's Mission claimed the audit "opens (or updates)" the issue and
phase 2 recorded the update as a behavioural note to verify rather
than assume; it was assumed. Both corrected at source above, and
D5.2 says what phase 5 does about the defect itself.

**Both non-compliant repositories are far past the threshold, and
that is not this plan's automation failing.** As the compliance page
was last regenerated before this planning, on 2026-09-16, ryll
needed review on 120 of its 214 in-scope files and kerbside on 115
of its 229; step 5.3 re-measured both a day later from clean clones
and found 105 and 106 needing review, which is the ordinary movement
of a backlog as commits land rather than a discrepancy. The
compliance page is regenerated daily, so its figures will match
neither of these dated measurements for long -- read it as the
current position, and these as what was measured on the days named.
The Situation section opens with "ryll is about to reach 100%
human-reviewed", which has not been true for weeks -- but the cause
is that the scope deliberately grew, driven by the
`review-scope-completeness` criterion and by hand (ryll `cf5f6b1`
"name every tracked file in the scope config", `1f22ac8` adding
`.devcontainer`), not that marks are being lost. A backlog alert
whose subject grew its own denominator is working as designed. D5.3
declines to retune on this evidence.

**Review sessions still work end to end.** ryll's develop carries
`review:` commits as recently as 2026-09-17 (`fde4f54`, `ba84ee3`),
landing through pull requests alongside the bot's prunes, which is
item 4 and needs nothing staged either.

**The Execution table's statuses are outside the shared
vocabulary.** It used `Done` and `Blocked on merge`, where
`plan-status-vocabulary` allows exactly one term from a fixed list.
Nothing audits a master plan's own phase table -- `PlanIndex` reads
only `docs/plans/index.md` -- so this was invisible rather than
absent. Corrected in this planning commit.

**Two things this survey deliberately did not correct.** The phase
1--4 sections name `scripts/audit-check.py`,
`scripts/test_audit_check.py`, `audits/` and
`PROJECT-CONSISTENCY-AUDITS.md`. Every one of those has moved:
the check is now `ReviewCoverage` in
`scripts/audit/checks/review.py:173`, registered at
`scripts/audit/registry.py:78`, with `REVIEW_BACKLOG_THRESHOLD` at
`scripts/audit/checks/review.py:97`; the tests are under
`scripts/tests/`; the specs are under `docs/audits/`; and the
criteria list is `docs/consistency-audits.md`. Those sections are
the record of work that has landed, and rewriting their paths every
time the tree moves turns a record into a maintenance burden, so
they stand and the current locations are named here once. The
*claims* that were wrong -- not the paths that merely aged -- were
corrected at source.

#### Decisions

**D5.1. Verify from what production already did, and stage nothing.**
Every experiment this section proposes has a real counterpart with a
date, a commit or an issue number attached, recorded in the survey
above. Re-running them would add nothing, and item 3's version --
temporarily lowering `REVIEW_BACKLOG_THRESHOLD` -- would file issues
in ryll and kerbside to prove a lifecycle that ryll#242 and #275
already prove. The phase's remaining work is therefore repair and
record-keeping, not experiment.

**D5.2. The never-refreshed issue body is recorded and filed, not
fixed here.** This is the decision most likely to be argued with,
because the Mission asserted the update behaviour and the "session
work queue" framing rests on it. Against fixing it in this phase:
`audit-manage-issues.py` serves all 55 criteria across every
repository in the matrix, so teaching it to rewrite bodies changes
what lands in other people's issue trackers on the next daily run --
every open consistency issue rewritten at once, notifications with
it, and any hand-edited body clobbered. That is a change to the
issue machinery, which `PLAN-consistency-audits-v2` owns, at this
repository's own definition of high effort, and it does not belong
inside a verification phase of the plan that merely noticed it. So
phase 5 files an issue against shakenfist/development with both
measurements, corrects the Mission and the documentation to say what
the machinery does, and adds it to Future work. If that reading is
wrong the alternative is cheap to take later: it is one `gh issue
edit` in `process_results()`, guarded on the rendered body differing.
Filed as shakenfist/development#138.

**D5.3. Do not retune the threshold or add hysteresis on this
evidence.** Two repositories sit 20x over the threshold with an
issue permanently open, which looks like an alert that has stopped
carrying signal. But 5 was chosen as "how much review work has piled
up before a session is warranted", and 120 files genuinely is work
piled up; hysteresis (open at 5, close at 2) would not close either
issue. The alert is telling the truth about a backlog that grew
because the scope grew. Hysteresis stays in Future work against the
churn case it was written for -- #242, #275 and #282, each open a
day or two -- which is the evidence that would justify it.

**D5.4. Do not add the "scope config without prune workflow" check
here.** All five adopting repositories carry
`.github/workflows/prune-reviews.yml` today, verified by hand on
2026-09-17 against the GitHub API. The check exists to catch a drift
that has not happened, it is a new criterion with fleet blast
radius, and this phase verifies. It stays in Future work, now with a
measurement beside it.

**D5.5. Phase 5 repairs the documentation this plan produced.** Phase
3 wrote the steady-state section of `docs/code-review-tracking.md`,
and three of its statements are now false or misleading: the loop
argument, the location of `REVIEW_BACKLOG_THRESHOLD`, and the work
queue's freshness. Fixing the output of an earlier phase in this plan
is repair, not scope creep -- a verification phase that finds a
documented claim false and leaves it documented has not verified
anything.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5.1 | medium | sonnet | none | In `docs/code-review-tracking.md`, fix three statements in the "Steady state" section (from line 489). (a) The loop argument at lines 519-521 says "pushes made with the workflow's own token do not trigger workflows, and a second prune would find nothing to do anyway". The first clause is false: ryll's workflow pushes as shakenfist-bot via `DEPENDENCIES_TOKEN`, because develop's ruleset requires a pull request and GitHub will not make the Actions app a bypass actor, and a PAT push does retrigger the workflow -- observed 2026-09-11 04:57:20 UTC, a run whose `displayTitle` is "Prune stale review marks." and which committed nothing. Say that the loop terminates because the second run finds nothing to prune, and that a repository pushing with `GITHUB_TOKEN` instead never retriggers at all. ryll's own `.github/workflows/prune-reviews.yml` header comment already says this correctly; do not contradict it. (b) Line 542 places `REVIEW_BACKLOG_THRESHOLD` in `scripts/audit-check.py`; it is `scripts/audit/checks/review.py:97`. (c) The "ready-made session work queue" sentence at line 545 must say that the list is written when the issue is filed and is never refreshed afterwards, so a long-lived issue understates the backlog -- name `scripts/audit-manage-issues.py` as the reason and link the issue filed in step 5.2. Also: the section says the workflow "runs on every push to main", but ryll's default branch is develop; write it so it is true of an adopting repository whatever its default branch is called. Do not touch `AGENTS.md` (no convention changed) or `ARCHITECTURE.md` (the system's shape did not change). *Superseded on 2026-09-19*: review found the same false claim in both files, and a brief that excludes a file cannot make a false statement in it true -- both were corrected. See "What verification found". Wrap at the file's existing width. |
| 5.2 | low | sonnet | none | File one issue on shakenfist/development titled "Consistency audit issues are never updated after they are filed". Body: `process_results()` in `scripts/audit-manage-issues.py` (lines 249-266) creates, dedupes and closes but never edits an open issue, so every issue body is frozen at filing time; the two measurements are ryll#304, filed 2026-08-21 saying "63 need review" while the audit measured 120 on 2026-09-17, and kerbside#227, filed 2026-08-03 saying "0 of 152 in-scope files reviewed" while the audit measured 114 of 229. Note the second-order effect: ryll#304's spec link still points at `development/audits/review-coverage.md`, a path that stopped existing when the specs moved under `docs/`, and nothing will ever fix it in place. Say it affects every criterion, not only `review-coverage`, that the suggested fix is a `gh issue edit` guarded on the rendered body differing from the current one, and that the trade-off to think about first is a fleet-wide body rewrite on the next daily run plus the loss of any hand-edited body. Reference `docs/plans/PLAN-review-coverage.md` phase 5 D5.2 and `docs/plans/PLAN-consistency-audits-v2.md`. Record the issue number in this section and in Future work below. Do not fix the script. |
| 5.3 | low | sonnet | none | Reproduce the audit's numbers locally, which is the one claim in the survey nobody has re-derived from a clone rather than from the compliance page: clone or update a checkout of shakenfist/ryll at `origin/develop` and one of shakenfist/kerbside at its default branch, then run `python3 scripts/audit-check.py --repo-path <clone> --repo-name <name> --github-org shakenfist` from this repository and compare the `review-coverage` entry against `docs/audits/compliance.md#review-coverage` (ryll: 94 of 214 reviewed, 120 needing review; kerbside: 114 of 229, 115 needing). Numbers move as commits land, so the test is that the check's arithmetic agrees with `python3 scripts/review-tracking.py status --json` run in the same clone at the same commit, not that it matches the digits written here. Record the commit audited and the result in this section. Do not run `audit-manage-issues.py` at all, not even with `--dry-run`, and do not commit any clone. |
| 5.4 | low | sonnet | none | Record the outcome in this section under a "What verification found" heading: the evidence table from the survey (audit run, prune commit count, the four ryll issues with their dates and which were closed by the bot, the 2026-09-11 retrigger run), step 5.3's reproduction, and step 5.2's issue number. Then set this phase's row in the Execution table to `Complete` and fill its `Merged` column when the pull request lands, and update the plan's row in `docs/plans/index.md` so the Intent line says the steady state is verified in production and names what it found. Leave the index status at `In progress`: phase 6 has not run. |

#### What verification found

Per D5.1 the phase verified from evidence production had
already produced, and nothing was staged. The table below is
what was re-checked on 2026-09-17, against `gh` and `git`
rather than copied from the survey above:

| Checked | Evidence |
|---------|----------|
| Daily audit | Most recent run at writing: `https://github.com/shakenfist/development/actions/runs/35087963103` (2026-09-16); the review-coverage table in `docs/audits/compliance.md` regenerates alongside it. |
| Automatic pruning on ryll | 35 `Prune stale review marks.` commits on `origin/develop`. |
| The retrigger | Run at 2026-09-11 04:57:20 UTC, `displayTitle` "Prune stale review marks.", committed nothing -- the evidence that a PAT push retriggers the workflow once into a no-op. |
| Issue lifecycle on ryll | #242 opened 2026-08-03, closed 2026-08-05; #275 opened 2026-08-14, closed 2026-08-15; #282 opened 2026-08-16, closed 2026-08-17; #304 opened 2026-08-21, still open. #242 and #275 carry shakenfist-bot's automated closing comment, so the close path is machine-verified; #282 a human closed. hunkydory#10 is the same lifecycle in a third repository. |
| Review sessions | ryll's develop carries `review:` commits as recently as 2026-09-17, so the manual half still works alongside the automation. |

Step 5.3 re-derived the numbers from clean clones rather than
from `docs/audits/compliance.md`, on 2026-09-17. ryll was
cloned at `develop`, full commit
`c868d8b7ee5c982da79588a7396a4a7da7dd6f61` (short `c868d8b`);
kerbside was cloned at its default branch, also `develop`,
full commit `9996305745a5f291a8178ad2c6960cd8682786db` (short
`9996305`). Both clones live under a scratch directory outside
this repo and were left uncommitted; neither existing local
checkout under `~/src/shakenfist/` was touched.

`scripts/audit-check.py` reported `review-coverage` as `fail`
for both, at the threshold of 5:

| Repo | Commit | Status | Details |
|------|--------|--------|---------|
| ryll | `c868d8b` | fail | 109 of 214 in-scope files reviewed at HEAD; 105 need review (threshold 5) |
| kerbside | `9996305` | fail | 123 of 229 in-scope files reviewed at HEAD; 106 need review (threshold 5) |

At each of those same commits, `scripts/review-tracking.py
status --json`, run with the clone as cwd, agreed exactly:
`in_scope`, `reviewed` and `needing_review` matched the
details string above for both repositories, and the union of
`stale` plus `never_reviewed` matched the check's `missing`
list file for file (105 of 105 entries for ryll, 106 of 106
for kerbside; both repositories have zero `stale` files, so
the whole backlog is `never_reviewed`). No disagreement was
found between the two tools at either commit, and
`audit-manage-issues.py` was not run.

What the phase changed, in total, is three corrections in
`docs/code-review-tracking.md` -- the loop-safety argument, the
location of `REVIEW_BACKLOG_THRESHOLD`, and the claim that a filed
issue is a ready-made work queue -- plus the four sentences that
said the prune workflow pushes to "main", corrected to "default
branch" since ryll's and kerbside's is develop; and
shakenfist/development#138, filed against the issue body never being
refreshed. No criterion, check or issue-management code was touched,
and `audit-manage-issues.py` was never run.

Review of the pull request added three more edits of the same kind.
The "pushes to main" claim survived in two places step 5.1's brief
had ruled out of scope: `AGENTS.md` and `ARCHITECTURE.md` both say
it about *adopting* repositories, so both were wrong for exactly the
reason the four corrected sentences were, and both now say "default
branch". The brief's reasoning -- no convention and no system shape
changed -- does not reach a statement that is simply false.
(`ARCHITECTURE.md`'s other two mentions describe this repository's
own `ci-prune-reviews.sh`, whose default branch really is main, and
are left alone.)

`AGENTS.md` also claimed that editing a file carrying a review mark
makes `review-tracking-tests` fail. It does not: `regen` counts
marks rather than validating them against HEAD, so `REVIEWS.md`
regenerates unchanged and the suite passes. This pull request proved
it -- editing `docs/code-review-tracking.md` staled its mark, the
suite stayed green, and the review caught what CI could not.
`AGENTS.md` now says that nothing in the pull request catches this
and that `prune-reviews` heals it only after the merge. The three
marks these corrections staled -- `docs/code-review-tracking.md`,
and `AGENTS.md` and `ARCHITECTURE.md` from the corrections above --
were pruned here rather than left for that workflow, so all three
return to the human review queue. The totals in `REVIEWS.md` are not
quoted here: they move with every merge to main, which is the same
reason the compliance figures above carry their dates.

A second review round reached the criterion spec itself.
`docs/audits/review-coverage.md` still named ryll and kerbside as
the only repositories carrying the tooling, which this phase's own
survey had already disproved -- five carry it -- and it still
described the filed issue as a standing work queue, the framing step
5.1 removed from `docs/code-review-tracking.md`. Both corrected, and
the spec now points at the compliance page rather than carrying a
fleet list that has to be maintained by hand. The issue reference in
`docs/code-review-tracking.md` also moved from `development#138` to
`shakenfist/development#138`: GitHub autolinks `owner/repo#N` and a
bare `#N` but renders `repo#N` as plain text, so the short form the
first round asked for was not a link at all. Editing the spec staled
its mark too, pruned here like the other three, which is what takes
this repository past its own threshold.

Taken together, the two rounds say step 5.1's brief was wrong to
scope the repair to one file. The "pushes to `main`" claim lived in
three -- `docs/code-review-tracking.md`, `AGENTS.md` and
`ARCHITECTURE.md` -- and the criterion spec carried two stale
statements of its own, so repairing one of them left the tree
disagreeing with itself until review caught it. Both rounds landed
inside #139, so the merge commit recorded for step 7 covers them.

**`review-coverage` fails against this repository, and that is the
criterion working.** Rebasing onto main on 2026-09-19 put
development itself over its own backlog threshold: main already
needed review on two files (`.github/workflows/renovate.yml` and
`templates/renovate/renovate.yml`) and this branch prunes four more,
so six in-scope files need review against a threshold of five.
`audit-check.py` now reports 30 pass, 1 fail, 24 not-applicable
rather than the 31/0/24 of 2026-09-17. Nothing regressed: the check
recomputes against HEAD exactly as phase 2 built it to, and the
repository that owns the tooling has landed in its own backlog
queue. The daily audit will file a `Consistency: Human review
coverage` issue against development once this merges, and it closes
itself when a review session clears the six files -- the lifecycle
phase 5 verified on ryll, now demonstrated at home without staging
anything, which is what D5.1 and D5.3 declined to manufacture
elsewhere.

#### Risks and mitigations

**The survey's numbers age while the phase is in review.** The
backlog on ryll and kerbside moves with every merge, so a reviewer
checking 120 against the compliance page a week later will see a
different number and may read the plan as wrong. *Mitigation:* every
measurement in this section carries its date, and step 5.3's
acceptance test is internal agreement between two tools at one
commit rather than a literal digit. The reviewer checks the shape,
not the value.

**Fixing the documentation's loop argument could leave two
repositories disagreeing.** ryll's workflow header and this
repository's `docs/code-review-tracking.md` both explain the loop,
and step 5.1 rewrites only the second. *Mitigation:* the brief names
ryll's header as the authority and requires the two to agree; the
management session re-reads ryll's header when reviewing 5.1 rather
than trusting that it was consulted.

**Filing an issue about the audit machinery invites someone to fix
it in this branch.** *Mitigation:* D5.2 states the reasoning and the
step brief ends with "Do not fix the script". If the operator wants
it fixed, it is a phase in `PLAN-consistency-audits-v2`, not a
commit here.

#### Definition of done

- [x] `docs/code-review-tracking.md` contains no statement that a
      prune push cannot retrigger the workflow, and its explanation
      of why the loop terminates matches ryll's
      `.github/workflows/prune-reviews.yml` header comment.
- [x] `grep -rn 'REVIEW_BACKLOG_THRESHOLD' docs/ | grep -v
      '^docs/plans/'` names `scripts/audit/checks/review.py` and no
      other file. The plans are excluded deliberately: they record
      the old path as part of saying it was wrong.
- [x] `docs/code-review-tracking.md` says, where it describes the
      issue as a work queue, that the list is not refreshed after
      filing.
- [x] An issue exists on shakenfist/development describing the
      never-updated issue body, and its number appears both in this
      section and in Future work.
- [x] Step 5.3's run is recorded with the commit it audited, and
      `review-tracking.py status` and `audit-check.py` agreed at that
      commit.
- [x] No issue was filed, edited or closed by anything this phase
      ran: `audit-manage-issues.py` was not invoked.
- [x] Every status cell in this plan's Execution table is one term
      from `templates/shared-blocks/plan-status-vocabulary.md`.
- [x] `python3 scripts/review-tracking.py status` reports no
      `stale:` file: any review mark this phase staled by editing a
      file has been pruned in the branch rather than left for
      `prune-reviews` to heal after the merge.
- [x] `pre-commit run --all-files` passes, and `python3
      scripts/audit-check.py --repo-path . --repo-name development`
      still reports 31 pass, 0 fail, 24 not-applicable as it did on
      2026-09-17, or this section says which verdict moved and why.
      One moved: see below. Run the command with audit credentials
      -- without a token, `delete-branch-on-merge` and
      `scope-coverage` degrade to `fail` on permissions rather than
      on anything in the tree, which is a different two failures
      from the one recorded here.

#### Back brief

Before executing any step of this phase, back brief the operator on
what the phase is for and what it will not do -- in particular that
it fixes documentation and files an issue, and changes no criterion,
no check and nothing in `audit-manage-issues.py`. Step 5.1 is the
one to raise before editing if its three fixes cannot be made
without restructuring the "Steady state" section, since a
restructure is cheap to propose and expensive to redo.

### Phase 6: Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of every phase in
this plan against `main`, not over the last phase's diff alone --
the interactions between phases are most of what a whole-plan
audit is for. This plan spans two repositories, so the audit runs
once per pull request, each against its own default branch.
Findings land as their own pull request; the plan is not complete
until each is resolved or declined in writing, with the reason
recorded here. If the audit finds nothing, say so in one sentence.

**Planning effort:** high. The work is six weeks old, it landed in
two repositories, and one of the two repositories has been
restructured underneath it since -- so the judgement in this phase
is almost entirely about what range to read and against which tree
to report, and getting that wrong produces a confident audit of
nothing. **Review effort:** high for the wave 2 agents, which is
what `PUSH-AUDIT.md` already specifies for 2d and what a 1,000-line
accumulated diff justifies for 2a to 2c.

In scope: running `PUSH-AUDIT.md` over the three development
merges and ryll's `PUSH-AUDIT.md` over the two ryll ranges;
closing the two records this plan still leaves blank (its missing
`Bugs fixed during this work` and `Back brief` sections); and
correcting the stale file name in Success criteria. Out of scope:
fixing anything wave 2 finds -- findings land as their own pull
request, and this plan is not complete until each is resolved or
declined in writing in this section; development#138, which phase
5 D5.2 declined on blast-radius grounds and which nothing here
reopens; and giving `PUSH-AUDIT.md` an explicit range variable,
which `PLAN-push-audit-phase` step 5a already owns (D6.6).

#### What the survey found

Phase 5 needs no further closeout. Its `Status` and `Merged` cells,
and the plan's row in `docs/plans/index.md`, were all set in #146;
the only thing missing was a record of #146's own merge commit,
added in this branch's first commit. That is the whole of the
close-out this phase carried.

**All four recorded landings verify as merge commits**, which
matters because `plan-push-audit-phase` says a single commit is
only ever enough when it is one. Checked with `git log -1
--format=%p`, and the diffstats the audit must reproduce are:

| Range | Files | Diffstat |
|-------|-------|----------|
| `b677b61^1..b677b61` (#11, 2026-08-03) | 13 | +884 -27 |
| `ced6fef^1..ced6fef` (#139, 2026-09-19) | 6 | +519 -97 |
| `02924fe^1..02924fe` (#146, 2026-09-19) | 1 | +20 -3 |
| ryll `1e94d00f^1..1e94d00f` (#236, 2026-08-02) | 5 | +102 -14 |
| ryll `a0227e05^..196db2f6`, scoped | 1 | +17 -3 |

**This repository has been restructured since steps 1--5 landed,
and most of the paths in the Execution table no longer exist.**
Phase 5's survey found this first and said so, under "Two things
this survey deliberately did not correct", where it decided the
historical sections stand rather than be rewritten every time the
tree moves. That decision holds and nothing here reopens it. What
is new is the consequence for an *audit*: a documentation record
may age harmlessly, but a range cannot, and D6.3 turns on this.
Of the thirteen files `b677b61` touched, checked one by one
against the tree at `5861c0a`:

* `audits/review-coverage.md` is now `docs/audits/review-coverage.md`.
* `audits/README.md` is now `docs/audits/README.md`.
* `PROJECT-CONSISTENCY-AUDITS.md` is gone, dissolved into `docs/`
  by `1276ff2`; the criteria list it held is now
  `docs/consistency-audits.md`.
* `PLAN-consistency.md` is now `docs/plans/PLAN-consistency.md`,
  moved by `3b546d6`, and `docs/plans/index.md:20` still links it as
  `Superseded`. *Corrected on 2026-09-20: this bullet said the two
  files were "both gone". Only the first is. That is precisely the
  failure D6.3 was written to prevent -- a file resolved to no path
  at all when it has a current one -- and it is the survey making
  the mistake it wrote the decision against. It also contradicts
  phase 5's survey above, which says of the same set of names
  "Every one of those has moved".*
* `PLAN-review-coverage.md` is now `docs/plans/PLAN-review-coverage.md`.
* `scripts/test_audit_check.py` is gone; the criterion's tests are
  now `scripts/tests/test_review.py`.
* The `check_review_coverage` function step 2 added to
  `scripts/audit-check.py` is gone from it: that file is 71 lines
  and holds no check functions. The criterion is now the
  `ReviewCoverage` class at `scripts/audit/checks/review.py:173`,
  registered at `scripts/audit/registry.py:78`.
* `scripts/review-tracking.py`, `scripts/audit_common.py` and
  `scripts/test_review_tracking.py` are still where step 1 put
  them. The last of those is a correction to phase 5's note, which
  says "the tests are under `scripts/tests/`":
  `scripts/test_review_tracking.py` did *not* move, and neither did
  the five other test files still sitting in `scripts/`. Only the
  audit criterion's own tests did.

`PLAN-audit-scripts-restructure` did that, and its own push audit
(`76975d9..42565c2`) read the moved code in its new shape. That is
not the same as auditing what this plan wrote, but it does mean a
finding reported against an August path has probably already been
overtaken.

**One Success criteria bullet is stale at source**: it requires
`scripts/test_audit_check.py` to pass, and that file no longer
exists. Corrected in this phase's commit rather than left for the
audit to rediscover, because a criterion that names a deleted file
cannot be met and would read as a finding about this plan when it
is a consequence of the restructure.

**This plan has no `Bugs fixed during this work` section and no
`Back brief` section**, both of which `plan-closeout-sections`
requires. Phase 5's review rounds fixed real defects in
`AGENTS.md`, `ARCHITECTURE.md` and `docs/audits/review-coverage.md`
and they are recorded in phase 5's own section rather than in the
section the block asks for.

**The ryll follow-up range is contaminated.** `a0227e05` and
`196db2f6` are single-parent commits that reached `develop` inside
ryll#262, an unrelated merge-queue pull request, so
`a0227e05^..196db2f6` carries 190 lines of `PLAN-two-stage-ci` work
that has nothing to do with this plan. Only
`.github/workflows/prune-reviews.yml` in that range is ours: 20
lines changed -- 12 in `a0227e05` and 8 in `196db2f6`, for a
diffstat of +17 -3. D6.4 scopes it by path.

**`PUSH-AUDIT.md` still writes every diff command as
`main...HEAD`.** `grep -c AUDIT_RANGE PUSH-AUDIT.md` returns 0, so
`PLAN-push-audit-phase` step 5a has not landed. Against work that
merged six weeks ago every one of those commands is empty and
returns nothing, which is indistinguishable from a clean audit.
Every brief below therefore states its range explicitly and is
required to report the file count it saw. *Corrected on 2026-09-20:
step 5a has since landed, as `4873e95`; see D6.6.*

**ryll's wave 1 cannot see this diff.** `tools/audit/wave1.sh`
runs `pre-commit`, rustfmt and clippy through Docker, and
`cargo test --workspace`. This plan changed one workflow YAML, one
shell script and two markdown files in ryll and no Rust at all, so
the Rust portions would exercise today's `develop` rather than
anything in the range. D6.5 scopes it and records the skip.

**Review marks.** Of the files in scope, two carried human review
marks in `REVIEWS.md` when the survey ran:
`scripts/audit/checks/review.py`, reviewed 2026-09-03, and
`scripts/review-tracking.py`, reviewed 2026-09-04. Both post-dated
this plan's code, so the survey concluded that a person had read
the current form of the criterion and the `status` subcommand.
`docs/code-review-tracking.md` and `docs/audits/review-coverage.md`
carry none. This phase edits only `docs/plans/PLAN-review-coverage.md`,
which `.vscode/review-scope.toml` excludes, so nothing here stales
a mark -- and per `plan-phase-landing` no step prunes or
regenerates `REVIEWS.md` in any case.

*Corrected on 2026-09-21, after review: that conclusion is
withdrawn for the criterion.* Development#158 changed
`scripts/audit/checks/review.py`, and `8a2750b` duly pruned its
mark, so `REVIEWS.md` on `main` now carries only
`scripts/review-tracking.py | mikal | 2026-09-04`. Nobody has read
the criterion in its current form, so the conclusion above stands
for the `status` subcommand alone. The backlog that creates is the
`review-coverage` audit's to report -- this plan's own machinery,
working as designed on this plan's own code, and the reason the
paragraph is corrected rather than the mark restored.

Nothing else in either runbook disagreed with its tree.

#### Decisions

**D6.1. The development range is three merges, read separately.**
`b677b61^1..b677b61`, `ced6fef^1..ced6fef`, `02924fe^1..02924fe`.
They are not concatenated into one diff: six weeks of unrelated
work sits between the first and the second, so any single range
spanning them audits most of the repository. Each agent runs its
checks once per range and reports per range, which is what the
shared block means by auditing the accumulated diff of every phase
rather than the last phase alone.

**D6.2. `02924fe` is inside the range.** It is one file and twenty
lines of this plan's own prose. It is audited anyway, because the
rule `plan-phase-landing` states is that a landing is recorded and
audited, not that it is recorded when somebody judges it large
enough. The cost is one extra `git diff` per agent.

**D6.3. Findings are reported against today's tree, not against
the August layout.** This is the decision most likely to be argued
with, and the argument against it is good: a push audit reviews a
diff, and re-reading the diff's subject matter in its current form
is a code review of the current tree, which is a different and
larger job. Three things decide it the other way. The diff is the
only statement of *intent* available -- what the plan meant to add
is legible there and nowhere else -- so it is still what each agent
reads first. But a finding is only actionable where a fix would
land, and for seven of thirteen files that is a different path or
no path at all; a report saying `scripts/audit-check.py` should
validate its input would send a reader to a file that has not held
a check function since the restructure. And the restructure's own
audit has already read the moved code, so a finding that survives
into the current tree is one two audits have now missed, which is
the more interesting result. Each agent therefore reads the
recorded range for intent, resolves every file it wants to report
on to its current path, and states for each finding either that
current path or that the code no longer exists. A finding the
restructure already resolved is reported as resolved, not dropped
silently.

**D6.4. The ryll range is `1e94d00f^1..1e94d00f` plus
`a0227e05^..196db2f6 -- .github/workflows/prune-reviews.yml`.**
The path filter is the only honest way to read the follow-ups: they
are mixed commits and the unscoped range is 90% somebody else's
plan. The caret in `a0227e05^` is deliberate -- `A..B` excludes
`A`, and `a0227e05` is where the `DEPENDENCIES_TOKEN` switch lives,
which is the change phase 5 spent most of its verification on.

**D6.5. ryll's wave 1 runs `pre-commit`, actionlint and shellcheck
only; the Rust build and `cargo test` are skipped, and the skip is
recorded in the Outcome.** A green `cargo test --workspace` on
today's `develop` is evidence about today's `develop` and about
nothing in this range, and it costs a full Docker toolchain build
to obtain. The runbook's own framing -- wave 1 exists to make wave
2 worth spending on -- is satisfied by the checks that can actually
see a YAML and a shell script. An audit that says what it declined
to run is a result; one that quietly runs the wrong thing is not.

**D6.6. `PUSH-AUDIT.md` is not given a range variable here.**
Substituting the range by hand in each brief is worse than fixing
the runbook, and phase 5's equivalent decision went the other way
-- `PLAN-scope-coverage` D5.3 repaired the runbook mid-audit. The
difference is ownership: there the stale prose was unowned and
described the exact thing being audited, whereas here
`PLAN-push-audit-phase` step 5a is already written, names
`AUDIT_RANGE`, and enumerates all sixteen literals. Two plans
editing the same sixteen lines in the same week is how one of them
silently loses. This phase's briefs carry their ranges inline and
the Outcome notes that step 5a would have removed the need.

*Corrected on 2026-09-20: step 5a landed as `4873e95` ("Give the
push audit an explicit range") after this branch forked.
`PUSH-AUDIT.md` now reads `git diff "${AUDIT_RANGE:-origin/main...HEAD}"`
throughout, `AUDIT_RANGE` appears on 21 lines, and the by-hand
substitution this decision chose is not needed by the next audit.
The decision itself stands as the record of what was true when the
phase ran, and the ownership reasoning was right -- what did not
need making was the prediction that two plans would collide, since
they did not. The same correction applies to the survey above, to
the "Archaeology" risk's sibling below, and to "What the audit did
not do".*

**D6.7. The ryll audit is run now rather than cited.**
`plan-push-audit-phase` says a phase that landed in another
repository is audited as part of the pull request that lands it,
and that this plan's push-audit phase cites that audit rather than
re-running it. ryll#236 merged on 2026-08-02, before the block
existed, and no audit was run. There is nothing to cite, so the
phase runs it -- against `origin/develop`, which is ryll's default
branch, and from a clone checked out there rather than from
whatever branch the working clone happens to be on. The clone used
for this run was on `gitleaks-scope-head` when the phase started,
which is the reason the instruction is explicit.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Wave 1 of `PUSH-AUDIT.md` over the three development ranges named in D6.1: `b677b61^1..b677b61`, `ced6fef^1..ced6fef` and `02924fe^1..02924fe`. Read every `git diff main...HEAD` in the runbook as `git diff <range>` and run each check once per range -- a literal `main...HEAD` is empty here because all three merged weeks ago, and an empty grep reads exactly like a clean audit. Run `pre-commit run --all-files` first, on a clean tree: it is the whole of lint and test in this repository, and `review-tracking.py stamp` takes its SHAs from the git *index*, so an unstaged edit is attested at the staged content rather than at what is on disk. Then run each grep in the wave 1 block (lines 44--113) with the range substituted, and report every hit with a verdict -- hit, looked at, accepted or blocking -- and why. State the file count you saw for each range; they must be 13, 6 and 1, and a count that does not match means the range was read wrong, not that the code changed. Expect and explain rather than ignore: `b677b61` predates `docs/audits/` and its paths will look wrong, which is D6.3's subject and not a finding in itself; `REVIEWS.md` moves inside these ranges and that is the convention working. Report, do not fix. **Do not prune, regenerate or commit `REVIEWS.md`**, per `plan-phase-landing`. Commit subject: none -- this step writes a report into the phase section, not code. |
| 6b | high | sonnet | none | Wave 2a, code quality, per the brief in `PUSH-AUDIT.md`, over the three ranges in D6.1. Take 6a's grep report as input and triage each hit. Apply D6.3: read the recorded diffs for intent, then resolve every file you want to report on to its path in the tree at `5861c0a` before writing a finding, and say for each finding either the current path or that the code no longer exists. The map you need is in "What the survey found" above -- in particular the `check_review_coverage` function added to `scripts/audit-check.py` in step 2 is now the `ReviewCoverage` class at `scripts/audit/checks/review.py:173`, registered at `scripts/audit/registry.py:78`. The highest-value reading is the coverage arithmetic itself: `ReviewCoverage.run()` and the `status` subcommand in `scripts/review-tracking.py` must agree on what "effective coverage" means, because the criterion files fleet-wide issues off that number and phase 5 D5.3 checked the two agree at one commit in two repositories -- one sample is not a proof. `REVIEW_BACKLOG_THRESHOLD` is at `scripts/audit/checks/review.py:97`; ask what happens at exactly 5 and what happens when the scope file lists a path that no longer exists. Do not read the four-file rule for adding a criterion out of `PUSH-AUDIT.md` lines 125--127 -- `PLAN-scope-coverage` step 5a corrected it, but verify the corrected text against `scripts/audit/registry.py` and `scripts/tests/test_metadata.py` before relying on it. |
| 6c | high | sonnet | none | Wave 2b, test review, per the brief in `PUSH-AUDIT.md`, over the three ranges in D6.1. Apply D6.3. Step 1 and step 2 added tests to `scripts/test_review_tracking.py` and to `scripts/test_audit_check.py`; the second file no longer exists and the criterion's tests are now `scripts/tests/test_review.py`, so the first question is whether every assertion the plan promised survived that move. Name each assertion that did not. The Success criteria section promises cases covering the scenarios in phases 1 and 2 -- read those two sections and check each promised scenario has a test that actually tests what its name says, not merely a test with a matching name. Read hardest on the boundaries the criterion turns on: a repository with no `.vscode/review-scope.toml` (must be N/A, not pass), a scope file that excludes everything, a stamped SHA that no longer matches its blob, and a file in scope that has never been stamped at all. Note that `scripts/test_review_tracking.py` stayed at `scripts/` while the rest moved under `scripts/tests/`; say whether that is deliberate or drift, but do not move it. |
| 6d | high | sonnet | none | Wave 2c, documentation review, per the brief in `PUSH-AUDIT.md`, over the three ranges in D6.1. Apply D6.3. The question is whether the documents this plan touched now say the same thing as each other and as the code: `docs/code-review-tracking.md` (rewritten by step 5 and repaired by step 5.1), `docs/audits/review-coverage.md` (moved from `audits/`), `docs/audits/README.md`, `AGENTS.md` and `ARCHITECTURE.md` (both corrected during #139's review). Check specifically that no page still says the prune workflow runs on `main` or on "the repository's own main branch" -- ryll's default branch is `develop` and step 5.1 changed four such references; that `REVIEW_BACKLOG_THRESHOLD` is cited at `scripts/audit/checks/review.py` and nowhere else outside `docs/plans/`; that the loop-termination argument in the Steady state section still matches ryll's `.github/workflows/prune-reviews.yml` header comment, which is the authority; and that the work-queue caveat added by step 5.1 names development#138. Also check that the two files step 4 edited left no dangling reference anywhere in `docs/`: `PROJECT-CONSISTENCY-AUDITS.md` was deleted and can dangle, while `PLAN-consistency.md` merely moved to `docs/plans/` and is still linked from the index, so a reference to it is not dangling. |
| 6e | high | opus | none | Wave 2d, security review, per the brief in `PUSH-AUDIT.md`, over the three ranges in D6.1 **and** the two ryll ranges in D6.4 -- the security surface of this plan is mostly in ryll, so this is the one wave 2 dimension that reads both repositories. Read the actual code. In development: `scripts/review-tracking.py` and `scripts/audit/checks/review.py` read paths out of `.vscode/review-scope.toml` and out of `REVIEWS.md`, both of which are repository content, and feed them to `git` and to the filesystem -- ask what a crafted scope pattern or a crafted mark row reaches, bearing in mind both files require a merged pull request to change. Apply the `path-traversal-review` shared block. In ryll: `.github/workflows/prune-reviews.yml` runs with `contents: write` and checks out with `secrets.DEPENDENCIES_TOKEN`, a personal access token belonging to shakenfist-bot, and `tools/ci-prune-reviews.sh` runs inside that job and pushes. Ask whether the token can reach a log line or a commit message on any path, whether `concurrency: prune-reviews` and the `if: github.ref == 'refs/heads/develop'` guard together actually prevent the retrigger loop from running unbounded, and what the job does on a fork pull request. `196db2f6` added that guard and `a0227e05` made the token switch; both are in the scoped range. |
| 6f | high | opus | none | The ryll half of the audit: wave 1 scoped per D6.5, then all four wave 2 dimensions except security, which 6e covers. Work in a clone of shakenfist/ryll checked out at `origin/develop` -- a local clone, wherever it is, may well be sitting on a feature branch (this one was on `gitleaks-scope-head`), so check out `origin/develop` or clone afresh rather than auditing whatever branch you find. Ranges are D6.4's: `git diff 1e94d00f^1 1e94d00f` (5 files, +102 -14) and `git diff a0227e05^ 196db2f6 -- .github/workflows/prune-reviews.yml` (+20). State both file counts in the report. Wave 1: run `pre-commit run --all-files` and, separately, `actionlint` on `.github/workflows/prune-reviews.yml` and `shellcheck` on `tools/ci-prune-reviews.sh`. **Do not run `tools/audit/wave1.sh`** -- it builds the Rust toolchain in Docker and runs `cargo test --workspace`, which is evidence about today's `develop` and not about a YAML and a shell script; record the skip and D6.5's reasoning in the report. Wave 2 over the same two ranges, using ryll's own `PUSH-AUDIT.md` briefs for 2a, 2b and 2c: the substance is whether `tools/ci-prune-reviews.sh` is correct when `prune` finds nothing to drop (it regenerates `REVIEWS.md` regardless, so an unconditional commit would push an empty change every run), whether `AGENTS.md` and `docs/development.md` describe the workflow as it is now rather than as `1e94d00f` first wrote it, and whether `tools/review-tracking.sh` and the CI script have drifted apart. ryll has no test suite that touches any of this; say so rather than reporting thin coverage as if a suite existed. Report, do not fix, and do not open or edit any issue. |
| 6g | high | opus | none | Management triage, in this session rather than a sub-agent. Read all six reports. Work the management checklist at the end of both `PUSH-AUDIT.md` files. Decide each finding blocking, advisory or declined, and write the outcome into this section under an **Outcome** heading: the ranges audited and the file counts seen, what wave 1 found in each repository, what each wave 2 agent found, and for every finding either where it was fixed or why it was declined, in writing. Record D6.5's skip and D6.6's substitution there too -- an audit's Outcome says what it did not run. Fixes land as their own pull request against each repository's default branch, per the shared block; this branch carries the plan record only. Then close the plan: add the `Bugs fixed during this work` and `Back brief` sections `plan-closeout-sections` requires, set step 8 to `Complete` in the Execution table, and set the plan's row in `docs/plans/index.md` to `Complete`. Per `plan-phase-landing`, step 8 is the last row and records **no** `Merged` cell where the audit found nothing -- it is the one row permitted to omit one, and no follow-up pull request is opened for the sake of that cell. Where the audit did raise findings, the findings pull request is the carrier and records the cell after this phase merges, and the index row does not reach `Complete` until those findings have landed or been declined here in writing. |

Steps 6a and 6b to 6f are sequential in one respect only: the
runbook requires wave 1 to pass before wave 2 is worth spending
on, and 6a's grep report is 6b's input. 6b to 6f are independent
of each other and are spawned in parallel once 6a passes. 6e and
6f split ryll between them -- 6e takes its security surface
because that is where this plan's security surface actually is,
and 6f takes the rest -- so they read the same two ranges and must
not contradict each other on what those ranges contain; 6g
reconciles them if they do. `Isolation` is `none` throughout: 6a
to 6f write no code at all, and 6g edits only this plan file.

#### Risks and mitigations

* **The range trap: an empty diff reads as a clean audit.** Every
  diff command in `PUSH-AUDIT.md` was written `main...HEAD` when
  this phase was planned (see D6.6's correction), which returns
  nothing for work that merged in August. *Mitigation:*
  every brief carries its range inline, and every report must
  state the file count it saw. 6g rejects any report whose counts
  are not 13, 6, 1 for development and 5, 1 for ryll.
* **Archaeology reported as a finding.** Seven of the thirteen
  files in `b677b61` have moved or been deleted -- four moved, two
  deleted, and `scripts/audit-check.py`, whose path survives but
  whose check function does not -- so an agent
  reading that diff alone will write findings against paths that
  no longer exist. *Mitigation:* D6.3 requires every finding to
  resolve to a current path or to be marked as already resolved;
  6g declines any finding whose subject is the August layout
  itself, citing `PLAN-audit-scripts-restructure`.
* **A retrospective audit has no gate.** This work merged six
  weeks ago and the criterion has been filing and closing real
  issues across five repositories since. A blocking finding is a
  bug in production, not a push that does not happen.
  *Mitigation:* 6g treats a blocking finding as a same-day pull
  request; the blast radius is bounded because `ReviewCoverage`
  returns N/A for any repository without `.vscode/review-scope.toml`,
  which is all but five.
* **The ryll scoping hides something.** D6.4's path filter and
  D6.5's skipped Rust build both narrow what gets read, and a
  narrowing is how an audit misses the thing it was for.
  *Mitigation:* both are written down as decisions rather than
  taken silently, 6f states the skip in its report, and 6g copies
  both into the Outcome. The filtered-out content is identifiable
  -- it is `PLAN-two-stage-ci` work, which has its own plan and
  its own audit phase.
* **An agent files or edits a GitHub issue.** `review-coverage`
  has open issues in ryll and kerbside whose bodies are frozen at
  filing time (development#138), and an audit that touched them
  would be indistinguishable from the bug. *Mitigation:* no brief
  invokes `audit-manage-issues.py` at all, not even with
  `--dry-run`, 6f is told not to open or edit an issue, and 6g
  verifies afterwards that ryll#304 and kerbside#227 still have
  `updatedAt == createdAt`.
* **`REVIEWS.md` is pruned on this branch.** Editing a reviewed
  file stales its mark, and the reflex is to prune. *Mitigation:*
  this phase edits only `docs/plans/PLAN-review-coverage.md`,
  which `.vscode/review-scope.toml` excludes, so no mark stales;
  and `plan-phase-landing` forbids it regardless -- the
  `prune-reviews` workflow heals it on the next push to `main`.

#### Definition of done

* `git diff --stat b677b61^1 b677b61 | tail -1` reports 13 files,
  `ced6fef^1..ced6fef` reports 6 and `02924fe^1..02924fe` reports
  1, and the Outcome states those same three figures as the ranges
  it audited.
* In ryll, `git diff --stat 1e94d00f^1 1e94d00f | tail -1` reports
  5 files and `git diff --stat a0227e05^ 196db2f6 --
  .github/workflows/prune-reviews.yml | tail -1` reports 1, and
  the Outcome states both.
* Every wave 2 finding appears in the Outcome with a disposition,
  every disposition that is "declined" says why, and every finding
  that is not declined names a path that exists in the tree it is
  reported against.
* The Outcome records D6.5's skipped Rust build and D6.6's
  by-hand range substitution as things the audit did not do.
* No line in this plan requires `scripts/test_audit_check.py` to
  pass. The name still appears in the phase 1--2 sections, which
  phase 5 decided should stand as the record of what was written at
  the time, and in phase 5's own note about them; the test is that
  Success criteria no longer names it.
* The plan carries a `Bugs fixed during this work` section and a
  `Back brief` section, and phase 6's **Outcome** is written rather
  than deferred. *Corrected on 2026-09-20: this was written as
  `grep -c 'To be filled in' ... returns 0`, which can never hold,
  because the criterion quotes the string it forbids and so matches
  itself. The same self-reference defeated a definition-of-done item
  in phase 5; a grep that names its own needle is not a test.*
* `python3 scripts/audit-check.py --repo-path . --repo-name
  development --github-org shakenfist` reports `plan-audit-phase`
  and `plan-index` passing, and `review-coverage` reporting the same
  figure on this branch as it reported at `5861c0a` -- this phase's
  edits move it by zero. *Corrected on 2026-09-21, after review:
  this read "no worse than it reported at `5861c0a`", which is a
  standing comparison against a tree that CI keeps changing rather
  than a measurement. By 2026-09-21 `main` reported worse, for
  reasons -- development#158 and the prune that followed it -- that
  have nothing to do with this phase, so the criterion as first
  written was unmet by a result that vindicates it.*
* `gh issue view 304 --repo shakenfist/ryll --json
  createdAt,updatedAt` and the same for kerbside#227 still show
  `updatedAt == createdAt`, proving no step filed, edited or
  closed an issue.
* `pre-commit run --all-files` passes.

#### Back brief

One gate, before 6a runs anything: the sub-agent restates, in its
own words, the three development ranges and why `main...HEAD`
cannot be used, and names the file count it expects from each. An
agent that has not internalised that will run seven greps against
an empty diff and report a clean audit, and the report will look
exactly like a real one -- it is the single cheapest thing to get
wrong here and the single most expensive to catch afterwards.

A second gate, before 6g edits the Execution table: it states
whether the audit produced findings, because that decides the
shape of the close-out. No findings means step 8 goes to
`Complete` with no `Merged` cell and no follow-up pull request;
findings mean the index row stays `In progress` until they land or
are declined in writing.

#### Outcome

Run 2026-09-20 over the five ranges D6.1 and D6.4 name. Wave 1
passed in both repositories and every agent reported the file count
it saw: 13, 6 and 1 in development, 5 and 1 in ryll, matching the
survey's table exactly. The audit produced six findings, all of
which have since landed. The one worth leading with is a defect
that two rounds of review had already passed over, because it is
the result that argues for the phase: a whole-plan audit found
what per-phase review did not.

*Self-audit.* `python3 scripts/audit-check.py --repo-path .
--repo-name development --github-org shakenfist` on this branch
reports `plan-audit-phase` and `plan-index` passing, along with
`plan-template`, `plan-phase-references` and `plan-source-references`.
`review-coverage` reports 175 of 190 in-scope files reviewed with
15 needing review at `475689e`, which is what it reports at
`5861c0a` as well, so this phase's edits leave it unchanged rather
than merely no worse. *Anchored on 2026-09-21, after review:* the
figure has since moved, and a measurement with no tree attached is
the same class of problem the citation-drift bullet in Future work
describes. On `main` at `8617f7b` the same command reports 157 of
191 with 34 needing review; the drift is development#158 and the
prune `8a2750b` that followed it, not this phase, and the
definition-of-done item this answers is a measurement at a commit
rather than a standing claim.
The definition-of-done item that measures the phase against its own
audit is therefore met as reworded -- zero movement, at `475689e` --
rather than as a standing comparison against a `main` that has since
moved, and now says so rather than being left to be inferred.

*Scheduling.* 6f ran in parallel with 6a rather than after it. The
gate exists because the runbook wants wave 1 to pass before wave 2 is
paid for, and 6f runs ryll's wave 1 itself; development's wave 1 does
not gate ryll. Written down because the step plan says otherwise.

#### What wave 1 found

Nothing blocking, in either repository. `pre-commit run --all-files`
passed here (12 hooks) and in ryll (10 hooks); `actionlint` and
`shellcheck` passed on ryll's workflow and script. 6f did not trust
`actionlint`'s exit code: run against a copy without
`.github/actionlint.yaml` it correctly flags `label "static" is
unknown`, so the clean result is the repository's configuration
rather than a linter doing nothing.

Two wave 1 greps could not be substituted for `b677b61` at all,
because the paths they search postdate it: the `FROZEN_ISSUE_TITLES`
grep against `scripts/tests/test_metadata.py`, and the `issue_title`
grep against `scripts/audit/checks/*.py`. They are recorded as
unsubstitutable rather than reported as clean, which is the
distinction D6.3 exists to keep.

#### Findings, with dispositions

**F1. `AGENTS.md:132-134` stated the false claim phase 5 chased.
Blocking; fixed in development#158, merged as `3859865`.** The
sentence read "CI pruning of a repo's own main branch is the
steady-state design, not a violation of it". It was a *generic*
claim about any adopting repository, and it was false: ryll's
default branch is `develop`. Sixteen lines earlier, in the same
file, `AGENTS.md:116-117` already said "on pushes to its default
branch" -- corrected during #139's review. The same range on `main`
now reads "of a repo's own default branch". This is the fifth
instance of the claim step 5.1 was scoped away from, and the only one
that survived in the live documentation; the closeout note above says
"the same false claim lived in four files", and the honest count is
five. Two more survive in this plan itself -- the Situation section,
where phase 5's decision that historical sections stand as records
leaves it, and a Success criteria bullet, which is a live claim and
is corrected there. Neither is in the document set step 5.1 was
scoped over, which is why F1 counts five and not seven. `AGENTS.md` is
loaded into every session in this repository, so a false generic
design claim there has more reach than its one-word fix suggests.
The other `main` references in `AGENTS.md` (92, 98) and
`ARCHITECTURE.md` (20, 30, 58) were checked and are correct: they
describe *this* repository, whose default branch really is `main`.

**F2. `ReviewCoverage.run()` can take every other criterion down with
it. Advisory; fixed in development#158, merged as `3859865`.**
`scripts/audit/checks/review.py:195-200` wrapped its
`subprocess.run` in `try: ... except
subprocess.TimeoutExpired` and caught nothing else, where every
other shelling-out check in the package already caught
`FileNotFoundError` as well and `SfuiVendor` in the same file did
it correctly. On `main` since `3859865` the same `try` carries an
`except OSError` beside the timeout handler.
`scripts/audit/registry.py:135` appends
`run_check(...)` with no handler, so anything raised costs the
repository all fifty-five criteria rather than one.

*Corrected on 2026-09-20, while fixing it in development#158.* Both
6b and 6e said the
trigger is a moved `review-tracking.py`. It is not: `subprocess`
looks for `sys.executable`, so the interpreter starts, exits
non-zero on the missing file, and lands in the `returncode` branch
which already handles it. What raises is `cwd` -- a checkout that is
absent or not a directory -- and since the scope file is checked
before the exec, reaching even that needs the checkout to become
unusable between the two. F2 is therefore hardening that brings two
outliers into line with the house convention, not a fix for a live
defect, and it ships with a test in each direction so the two
failure modes are not conflated again. The repository has been
bitten by the wider class twice and says so in place:
`scripts/audit/checks/packaging.py:1872-1874` ("the AttributeError
this used to throw propagated out of run_checks and cost the
repository every other check as well") and
`scripts/audit/text/python_source.py:129-131`. Both times the fix was
to guard inside the check and return `fail()`, so the shape is
settled. In scope: the subprocess call is step 2's own code, carried
across the restructure unchanged.

**F3. A filename can forge content in an auto-filed issue body.
Advisory; fixed in development#158, merged as `3859865`.**
`never_reviewed` is the list of tracked files as `git ls-files -z`
returns them, and `render_issue_items` -- at
`scripts/audit-manage-issues.py:194` on
`main` today, called at 288 and 299 -- rendered each as
`` `- `{item}`` `` with no escaping. A git path may
contain a backtick, which closes the code span, or a newline, which
injects raw lines into a body authored by shakenfist-bot. There is no
state-change primitive -- GitHub does not close issues from body
keywords -- so this is content forgery and notification abuse, not
escalation. `defuse()`, at `scripts/audit_common.py:121`, exists for
exactly this class, and nothing defused `missing` or `findings` on
the issue path, where `review-coverage` and
`review-scope-completeness` are the widest producers of
repository-derived `missing` in the check set. This
was first declined and referred, on the D5.2 boundary: escaping
`render_issue_items` changes every criterion's issue bodies
fleet-wide, which is `PLAN-consistency-audits-v2`'s machinery
rather than this plan's. The operator overrode that when asking for
every finding to be addressed, so it was fixed in the findings pull
request, development#158, instead, and the blast radius is stated in
that commit rather than used as a reason not to act. The referral stands for
development#138, which is a different problem in the same file.

*Corrected on 2026-09-20, after review.* This finding first called
the asymmetry "confirmed": that `defuse()` was applied only on the
compliance-page path, to `details`, and not on the issue path at
all. That was true of the tree the audit read at 07:37 AEST and
false twenty-nine minutes later. `PLAN-push-audit-phase.md`'s own
push audit found the same asymmetry independently and closed it:
`d60b58a` routes `details` through `defuse()`, now at
`scripts/audit-manage-issues.py:285`, and `2766610` adds the
regression test -- `test_the_details_are_defused_before_they_reach_the_body`
in `scripts/tests/test_manage_issues.py`, whose docstring names that
audit. Both merged to `main` as development#154 at 11:42, before
this section's own correction pass at 12:14, which revisited F3 and
did not re-read the file. So the asymmetry is closed rather than
confirmed, and citing it as live understated a fix that had already
landed. The finding survives narrowed, and that narrower form is
what development#158 fixed: `render_issue_items` escaped nothing, so
`missing` and `findings` items reached the body raw while `details`
no longer did. Both go through `defuse_item()` on `main` since
`3859865`. Two audits of two plans reaching the same file in one
morning is the argument for re-reading the tree at
correction time and not only at audit time.

**F4-F6, ryll, advisory; fixed in ryll#389, merged as `205ef7d`.**
*Dispositioned on 2026-09-21, after review, and corrected later the
same day after the round that followed.* These were first recorded
as "no pull request opened yet", which is deferral rather than
either of the two exits this plan's close-out allows, and left the
index row's stated exit condition unreachable. The next round
declined them in writing, arguing that all three were advisory, all
three were in ryll rather than in the repository whose machinery
this plan built, and -- the load-bearing clause -- that no pull
request carried them. That clause was already false when it was
written. Ryll#389 merged as `205ef7d` at 19:56 on 2026-09-20,
eleven minutes before this branch's head commit and twenty before
the review round that read the declining and approved of it.
Neither the round that wrote it nor the round that reviewed it
looked at ryll's `develop`; it was found by re-resolving this
phase's one citation into ryll, which is the whole argument for the
citation criterion in Future work. The declining is withdrawn. The
exit is the carrier, not the reasoning for not needing one, and the
index row reaches `Complete` because all six findings landed rather
than because three of them were argued away.

The findings themselves, as audited, and where each was fixed --
all three verified directly at audit time rather than taken on
report, and all three fixes verified the same way against
`origin/develop` at `005fe42`. The prune job granted
`GITHUB_TOKEN` `contents: write` at `prune-reviews.yml:40-41` as
audited, at `196db2f6`, although since `a0227e05` every write goes
through `DEPENDENCIES_TOKEN` and the `permissions:` block does not
constrain a PAT at all -- leftover from the pre-PAT design, and
replaced with `permissions: {}` by `7c6bc1e`. "`REVIEWS.md` is generated; never
edit it by hand" had disappeared from every agent-facing document:
`1e94d00f` put it only in `AGENTS.md`, `d1b2f60` deleted that
section when moving detail into `docs/`, and `docs/development.md`
never carried an equivalent, so it survived only in `REVIEWS.md`'s
own generated header, visible to somebody who had already opened
the file to edit it; `0fb0d8f` put it into `docs/development.md`.
And `scope-orphans` appeared in neither `tools/review-tracking.sh`'s
header nor `docs/development.md`, although it had existed upstream
since 2026-08-31; `9904770` documents it in the script header and
`0fb0d8f` in the document.

#### Findings declined, with reasons

**D-1 and D-2: an audited repository can zero its own review
obligation.** One `exclude` entry covering the source tree takes
`in_scope` to 0, so `0 >= 5` is false and `review-coverage` passes;
`review-scope-completeness` passes too, because `scope_orphans()`
skips excluded files by design. Deleting `.vscode/review-scope.toml`
does the same thing more quietly, via `not_applicable`. This is
declined as a defect because it is not one: `scope_orphans()`'s
docstring states the assumption in terms -- an `exclude` "is a
decision somebody made and can defend in a comment beside it" -- and
the `include` route, which is not a decision, *is* caught. The
mitigating control is human review of the pull request that narrows
the scope, which is circular, since human review is what the audit
measures. That circularity is real and is recorded in Future work
rather than dismissed. The proposed fix -- noticing a sharp drop in
a repository's in-scope count between runs -- needs cross-run state
the audit does not keep, which is a plan, not a finding.

**R-1, R-2 and R-3: ryll's credential and loop-safety surface.**
`actions/checkout` is given the PAT without `persist-credentials:
false`, so it lands in `.git/config` as a reversible base64
`extraheader` that GitHub's secret masker does *not* cover, on a
persistent self-hosted runner -- and this fleet documents that idiom
and applies it in `pr-re-review.yml` in both repositories. The job
then clones `shakenfist/development` unpinned and executes its
`review-tracking.py`. And the retrigger loop is bounded by an
argument rather than a mechanism: it terminates because
`render_reviews_md()` is a deterministic fixpoint, which is a
property of a file in another repository cloned at `main`. Neither
`concurrency: prune-reviews` nor the `if: github.ref` guard bounds
chain length; only the `git diff --quiet` emptiness check does. The
empirical record is good -- 34 prune commits on `develop`, none with
another prune commit as its parent -- so the property has held every
time. Declined here and referred, because `196db2f6`'s own commit
message already anticipated exactly this: "the underlying push lives
in a script that mirrors one in shakenfist/development and kerbside
carries the same hazard, so a fleet-wide fix is worth raising
separately rather than diverging ryll's copy here." That referral was
made in August and this audit is the second time it has been raised.
Recorded in Future work so the third time is not needed.

**Observations, recorded and not acted on.** A crafted weAudit entry
can crash `review-tracking.py` and put a traceback into an issue body
and the compliance page (hygiene, no steerable content -- 6e tried
and could not drive repository content into the message). An
oversized weAudit file guarantees the 60-second timeout, which then
files a misleading "timed out" issue. A scope entry naming a deleted
path matches nothing, silently and forever, with no diagnostic.
ryll's prune job has no `timeout-minutes` where development's has
ten. No ryll job runs `actionlint`, so a typo in the `if:` guard
would make the job *skip* -- a green run, not a red one -- and that
guard is the only thing standing between the workflow and pushing an
unreviewed branch to `develop`. ryll has no test at all for this
machinery, against five in-repository precedents for exactly that
kind of smoke test. `in_scope == 0` is untested, and
`test_status_mutates_nothing` is weaker than its sibling
`test_scope_orphans_mutates_nothing`, which diffs `git status
--porcelain` rather than re-reading three named paths.

#### What the audit did not do

D6.5's skip did not buy what it claimed. Not running
`tools/audit/wave1.sh` avoided `cargo test --workspace`, but
`pre-commit run --all-files` -- which the same brief requires --
runs ryll's `rust-check` hook, which builds the devcontainer image
and runs rustfmt and clippy in Docker anyway. The decision's
reasoning about *evidence* stands: a green Rust suite says nothing
about a YAML file and a twenty-line shell script. Its reasoning about
*cost* was wrong, and a future phase reusing this decision should
scope pre-commit too or drop the cost argument.

D6.6 held: the ranges were substituted by hand in every brief, and
at the time the audit ran `PUSH-AUDIT.md` still wrote `main...HEAD`
sixteen times with `PLAN-push-audit-phase` step 5a as the owner.
Every agent reported its file count, which is the compensating
control, and every count matched. *Corrected on 2026-09-20: step 5a
has since landed as `4873e95`, so a reader of the current runbook
will find `AUDIT_RANGE` rather than the sixteen literals this
describes. The substitution is a record of the run, not a
description of the runbook as it now stands; see D6.6.*

#### What the audit corrected about this plan

Five claims in the briefs above were wrong and are corrected at
source rather than left for the next reader.

* The 6a brief said `REVIEWS.md` "moves inside these ranges". It does
  not -- 6a checked, including for renames, and `REVIEWS.md` is in
  none of the three diffs. Its churn is on separate automated prune
  commits.
* The 6e brief said `scripts/review-tracking.py` reads paths "out of
  `REVIEWS.md`". It does not: `REVIEWS.md` is write-only output from
  `render_reviews_md()`, opened again only to compare for equality
  before writing. Marks come from `.vscode/*.weaudit`. The stamp
  validation that made the crafted-mark question uninteresting is the
  design's strongest property and it holds -- a mark whose stamp does
  not match the blob at HEAD counts as needing review.
* The 6b brief asked whether `ReviewCoverage.run()` and
  `review-tracking.py status` agree on what effective coverage means.
  They cannot disagree: there is one implementation. `run()` shells
  out and parses the JSON, and `review_status()` is the only
  arithmetic. Phase 5 D5.3's "one sample is not a proof" worry does
  not apply as phrased; the real risk at that seam is the subprocess
  boundary, which is F2.
* The survey called `scripts/test_review_tracking.py` staying in
  `scripts/` worth remarking on. 6c settled it as deliberate:
  `scripts/tests/` holds tests for modules inside the
  `scripts/audit/` package and runs as one `unittest discover` hook,
  while `scripts/test_*.py` are standalone-script tests with one
  pre-commit hook each. The dividing line is exact and
  `review-tracking.py` is on the right side of it.

* The 6d brief said `PROJECT-CONSISTENCY-AUDITS.md` and
  `PLAN-consistency.md` "no longer exist", inheriting the survey's
  error. Only the first was deleted; the second moved to
  `docs/plans/PLAN-consistency.md` and is linked live from
  `docs/plans/index.md:20`. 6d's conclusion survives the correction,
  and was re-checked on 2026-09-20 rather than assumed: the only
  remaining references to `PROJECT-CONSISTENCY-AUDITS.md` are in the
  historical sections of `PLAN-consistency-audits-v2.md`,
  `PLAN-plan-template-blocks.md`, `PLAN-consistency.md`,
  `PLAN-llm-doc-structure.md` and this
  plan, which phase 5's decision says stand as records of what was
  written at the time; and every reference to `PLAN-consistency.md`
  resolves, because the file is there. So nothing dangles -- but the
  brief asked the question of a file that could not have dangled,
  which is half the check it thought it was making.

One smaller correction, recorded because a later reader will chase
it: 6b cited the `REVIEW_TRACKING_SCRIPT` computation at
`scripts/audit/checks/review.py:97-100`; it is at 100-104. Line 97 is
`REVIEW_BACKLOG_THRESHOLD`, as this plan says elsewhere.

*Added on 2026-09-20, after review.* Review reported two stale
citations in F3 and they are fixed above. Rather than fix the two,
every `path:line` citation in this phase was re-resolved -- against
`main` here, and against `origin/develop` for the one into ryll's
`prune-reviews.yml`. Thirteen resolved exactly. One more was stale
that review did not find. The **Review marks** paragraph gave
`REVIEWS.md` line numbers for the two reviewed files -- 106 and 122,
correct at
`5861c0a`, 99 and 114 at `3859865`, and neither pair on `main`: the
prune `8a2750b` dropped the criterion's row a minute after
`3859865` merged, so `main` carries `scripts/review-tracking.py`
alone, at 112 as of `9977681`. Five figures for two rows across
three trees in two days, and one of the two rows no longer exists.
Line numbers into `REVIEWS.md` are the one citation that
cannot be kept right: the file is generated, it is rewritten by CI
on every prune, and `plan-phase-landing` forbids this phase from
regenerating it to check. The dates were correct and are stable, so
the paragraph now cites those and the entries carry themselves.
Two citations that are *not* fixed, deliberately: 6a and 6b cite
`PUSH-AUDIT.md` lines 44--113 and 125--127, which have moved since
the briefs were issued. A brief is a record of what was asked, not
a claim about the tree, and rewriting one to match today's runbook
would falsify the record. Follow the section headings instead.

*Corrected on 2026-09-21, after the next round of review.*
"Thirteen resolved exactly" was measured against a tree that no
longer exists, and one of the thirteen was resolved against a tree
that never will. Review found that F3's four numbers into
`scripts/audit-manage-issues.py` -- 138, 232, 243 and 229 -- match
neither `5861c0a`, nor this branch, nor `main`. They match
`771b074` alone: an intermediate commit on development#158's own
branch, which `b34734e` later reworked. The re-resolution pass had
that branch checked out and resolved against it, so the commit
whose stated purpose was eliminating stale citations produced one
that was wrong in every tree the repository keeps. They are
re-resolved above against `main` after `3859865` -- 194, 288, 299
and 285 -- and the rule that would have prevented it is in Future
work: resolve against the default branch, never against a branch
that has not merged.

The same round found five statements describing development#158 as
unmerged, when it merged as `3859865` at 19:38 on 2026-09-20, nine
minutes before `475689e` was written. Sweeping the class rather
than the five turned up three more the review did not report: F1's
"the sentence reads", F2's "catches nothing else" and F3's "nothing
defuses `missing` or `findings`" were all present-tense claims
about a tree the fix had already changed. Every `path:line`
citation in this phase into a file development#158 touched was
re-resolved against `main` afterwards, and the dispositions now
name the merge commit rather than a pending pull request.

*Corrected on 2026-09-21, after the round that followed.* The note
above fixed the **Review marks** citation by dropping the line
numbers from that paragraph, and then reported the fix here using
the numbers it had just abandoned: "106 and 122 ... and now 99 and
114", where "now" named no tree. It named three, in fact, and none
of them was `main`: 106 and 122 hold at `5861c0a`, 99 and 114 at
`3859865`, and on `main` the prune `8a2750b` removed the
criterion's row outright a minute after `3859865` merged, leaving
one row at 112. So the correction contradicted the correction it
was reporting: the **Review marks** paragraph in the survey says
only one row survives. The figures are now written with the tree
each belongs to.

Re-resolving every `path:line` citation in this phase mechanically
rather than by eye -- fifteen into `main` at `9977681`, a tree two
commits newer than the `8617f7b` the round before measured, and one
into ryll's `origin/develop` at `005fe42` -- found no further drift
in the fifteen and something larger in the one. The ryll citation
is F4's, and it no longer resolved: the `contents: write` F4
reports had been dropped. Following that turned up ryll#389, which
fixed all three of F4, F5 and F6 and merged eleven minutes before
this branch's head commit, while the Outcome, the Future work
bullet, the step 8 argument and the Back brief all said the three
were declined because no pull request carried them. Four statements
of the same false claim, in a phase whose two preceding rounds were
spent correcting exactly this shape in the other repository.
Corrected in all four places, with the superseded text kept. What
found it was a dozen-line script, and that is the argument for the
citation criterion in Future work: three rounds of re-deriving
citations by hand had left both the `REVIEWS.md` figures and F4-F6
wrong, and the first mechanical pass caught both.

The same round narrowed F1's "the only one that survived", which
review found contradicted by this plan's Situation section;
sweeping this plan for the shape rather than the reported instance
turned up a second, in Success criteria, which review did not
report and which is corrected there because a success criterion is
a live claim rather than a historical record. The definition-of-done
item measuring `review-coverage` was reworded in the same pass, for
the reason given against it.

#### The survival audit

Every assertion phase 1 and phase 2 wrote survived the restructure.
`scripts/test_audit_check.py` was deleted, and its four
`ReviewCoverageTest` methods and eleven assertions are present
byte-for-byte in `scripts/tests/test_review.py:207-241`, with only
the call site changed. The four `status` tests in
`scripts/test_review_tracking.py` survived verbatim. Nothing was
lost and nothing was silently replaced. Three of the four boundaries
the brief named are properly tested, including the important one --
a repository with no scope config asserts the literal
`not_applicable`, not merely "not pass".

#### Issue hygiene

No step filed, edited or closed a GitHub issue, and no step invoked
`scripts/audit-manage-issues.py` in any form. Verified afterwards:
ryll#304 and kerbside#227 both still report `updatedAt ==
createdAt`, at 2026-08-21T07:02:07Z and 2026-08-03T09:53:59Z. No
step pruned, regenerated or committed `REVIEWS.md`, which
`plan-phase-landing` forbids and which nothing here needed anyway,
since `.vscode/review-scope.toml` excludes `docs/plans/*`.

## Execution

One commit per logical change; the development-repo work is one
PR on this branch, the ryll work a separate PR.

| Step | Repo | Description | Status | Merged |
|------|------|-------------|--------|--------|
| 1 | development | `status` subcommand + tests | Complete | `b677b61` (#11) |
| 2 | development | `check_review_coverage` + registration + tests | Complete | `b677b61` (#11) |
| 3 | development | `audit_common.py` metadata + `audits/review-coverage.md` | Complete | `b677b61` (#11) |
| 4 | development | `PROJECT-CONSISTENCY-AUDITS.md` + `PLAN-consistency.md` entries | Complete | `b677b61` (#11) |
| 5 | development | `docs/code-review-tracking.md` steady-state rewrite | Complete | `b677b61` (#11) |
| 6 | ryll | prune workflow + `tools/ci-prune-reviews.sh` + docs | Complete | ryll `1e94d00f` (#236) |
| 7 | both | end-to-end verification (phase 5) | Complete | `ced6fef` (#139) |
| 8 | both | push audit over each PR (phase 6) | Complete | |

Step 7 stopped being blocked when both pull requests merged in
August; phase 5 above is the plan for it. The statuses were brought
into the shared vocabulary on 2026-09-17 -- see that phase's survey.
Step 7 merged on 2026-09-19 as `ced6fef` (#139), which is what moved
it to `Complete`; this plan does not write that term without a
landing commit beside it.

That cell records step 7 and nothing else. Phase 5's closeout went
out as its own pull request, #146, merged as `02924fe` -- which is
the shape `plan-phase-landing` exists to prevent, and that block
landed an hour afterwards (#145, `ad6c8fa`, 18:00 AEST on
2026-09-19, against #146 at 16:56). The rule is followed from
here: this paragraph is the first commit of phase 6's branch
rather than a seventh pull request, and phase 5 needs no further
closeout -- its `Status` and `Merged` cells and the plan's index
row were all set in #146.

`02924fe` is written down anyway, because phase 6 audits it. The
earlier draft of this paragraph argued the omission was free,
since the closeout changes this plan file and no tooling. That
reasoning is the one `plan-phase-landing` rejects: a landing is
recorded because it landed, not because somebody judged it big
enough to matter, and D6.2 folds `02924fe` into the development
range on exactly that basis.

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

Step 8's `Merged` cell is empty and stays empty. *Rewritten on
2026-09-21, after review: the paragraph this replaces nominated a
carrier that had already landed.* `plan-phase-landing` lets the
push-audit row omit the cell because it is the last row and nothing
ever reads it -- the column exists so that the push-audit phase can
reconstruct what to audit, and there is no phase after this one.
But the carve-out it grants for *free* applies where the audit
found nothing, and this audit found F1 to F6, so the block
nominates the findings pull request as the carrier: it is supposed
to land after the audit phase merges, when that merge commit is
known, and to record it.

That ordering inverted. Development#158 merged as `3859865` at
19:38 on 2026-09-20, while this phase's own pull request was still
in review rounds and unmerged. The block assumes the findings are
fixed *after* the audit phase lands; here they were fixed during
its review cycle, at the operator's direction, so the carrier went
first and none remains. The cell is therefore left empty
deliberately, and the carve-out claimed outside the conditions the
block states -- in preference to spending a pull request and a CI
run on one cell that nothing ever reads, which is the round trip
that same block exists to avoid. F1 to F3's landing commit is
recorded against each finding in the Outcome instead, where a
reader looking for it will be. The gap in the block is written up
in Future work.

Step 8's `Status` is `Complete` deliberately, and it is the audit
that finished, not the work the audit found: every wave brief ran,
every finding is written down with a disposition, and nothing
further is learned by running it again. The plan's index row is
`Complete` too, because every finding now has an exit -- F1 to F3
landed in development#158 (`3859865`) and F4 to F6 in ryll#389
(`205ef7d`) -- rather than because the rows say so. *Corrected on
2026-09-21: this read "F4 to F6 are declined in writing in the
Outcome", which was the exit claimed for them before anyone checked
ryll's `develop`. They had landed. The conclusion is unchanged and
the argument for it is stronger; see their disposition in the
Outcome.*
Recorded here because the resulting row, `Complete` with an empty
`Merged` cell, is indistinguishable from the carve-out the
paragraphs above disclaim, and only this prose separates them.
Nothing mechanical does: `plan-audit-phase` passes either way.

`pre-commit run --all-files` must pass before each commit is
proposed; the Python follows the house style (single quotes,
120-column wrap).

## Success criteria

* `review-tracking.py status` reports correct effective coverage
  on a fixture repo and on ryll itself, and mutates nothing.
* `scripts/test_review_tracking.py` and the criterion's own tests
  pass, with new cases covering the scenarios listed above.
  *Corrected on 2026-09-19: this named `scripts/test_audit_check.py`,
  which `PLAN-audit-scripts-restructure` deleted; the criterion's
  tests are now `scripts/tests/test_review.py`. See phase 6's
  survey.*
* A daily audit run on the current matrix produces: ryll pass (or
  fail with a correct file list), all other repos N/A, and a
  correctly-managed issue lifecycle.
* A merge to ryll's default branch is followed by exactly one bot
  prune commit when marks went stale, and none when they did not.
  *Corrected on 2026-09-21, after review: this read "ryll main".
  ryll's default branch is `develop`, so this is another instance of
  the claim F1 records -- found by sweeping this plan for the shape
  rather than reported.*
* The attestation verification story in
  `docs/code-review-tracking.md` remains true end to end.

## Future work

* ~~Adopt the tooling (and prune workflow) in kerbside once the
  ryll experiment stabilises.~~ Done, and earlier than this
  anticipated: kerbside's scope config landed in the same pull
  request as steps 1--5 (branch `kerbside-review-tracking`,
  2026-08-03). Five repositories now carry it -- actions,
  development, hunkydory, kerbside and ryll.
* An issue against `audit-manage-issues.py`, which never updates an
  open issue's body, so every consistency issue is frozen at filing
  time and a long-lived `review-coverage` issue understates the
  backlog it was filed about. Phase 5 D5.2 explains why the fix is
  not made here; filed as development#138.
* A consistency check that repos with
  `.vscode/review-scope.toml` also carry the prune workflow, so
  the two halves of the steady state cannot drift apart as more
  repos adopt. Not yet needed: all five adopters carried it when
  checked on 2026-09-17 (phase 5 D5.4).
* Hysteresis on the threshold (open at 5, close lower) if the
  issue churn from routine merges proves noisy. The churn case is
  real -- ryll#242, #275 and #282 each lived a day or two -- but
  phase 5 D5.3 declines to act on the *current* evidence, where two
  repositories sit far above the threshold for a reason hysteresis
  would not touch.
* Extend `plan-index` (or add a sibling criterion) to apply the
  shared status vocabulary to every status column in a master plan's
  own phase table, not only the index row. `plan-status-vocabulary`
  governs both, but `PlanIndex` reads `docs/plans/index.md` and
  nothing else, which is why this plan's own Execution table drifted
  to `Done` and `Blocked on merge` unnoticed. Not yet urgent:
  scanning during review on 2026-09-19 -- two days after the phase
  itself, which is why the date differs from the measurements above
  -- no other `docs/plans/PLAN-*.md` phase table carried an
  out-of-vocabulary status.
* A criterion that resolves the `path:line` citations a plan file
  writes, so citation drift is caught by CI rather than by a
  reviewer. Phase 6 produced three stale ones and review found two
  of them; the third -- `REVIEWS.md` line numbers in **Review
  marks** -- was found only by re-resolving all thirteen by hand
  afterwards. Nothing mechanical notices: a mutation setting a
  citation to line 99999 leaves every `plan-*` check passing. Not
  attempted here, because the hard part is not the resolving but
  telling a live citation from the several kinds this plan writes
  that must *not* resolve against `main` -- a path at a named
  historical commit, a line range in another repository's runbook,
  and a verbatim subagent brief, which is a record of what was asked
  rather than a claim about the tree. A check that cannot tell those
  apart would be noise, and the honest first step is a convention
  for marking a citation as historical. A fourth instance arrived
  from the commit that set out to eliminate the first three: F3's
  citations into `scripts/audit-manage-issues.py` were resolved
  against `771b074`, an intermediate commit on the findings branch
  that `b34734e` later reworked, so they were wrong in every tree
  the repository has rather than merely drifted. That says the
  convention needs three states and not two -- live, historical at
  a named commit, and resolved against an unmerged branch -- and
  the third has no honest rendering at all, which is the argument
  for resolving against the default branch and nothing else.
  Review of this phase then bounded what the criterion would buy,
  which is the more useful result: the `docs/plans/index.md` row
  still
  said F4 to F6 were declined after the Outcome, the Future work
  bullet, the step 8 argument and the Back brief had all been
  corrected. A citation criterion would not have caught it, because
  the drift is in prose rather than in a `path:line`. What would is
  a criterion relating an index row's claims to the plan body it
  summarises, and the cheaper half of that -- no `Complete` row
  asserting a disposition the plan body has withdrawn -- may be
  worth more per line of check than resolving citations.
  The same shape covers review item 4 in the round that found the
  index row: `Bugs fixed during this work` had lost F2, F3 and F4
  to F6, and nothing noticed either, because
  `plan-closeout-sections` is a block `plan-template` requires
  `PLAN-TEMPLATE.md` to carry rather than a check that reads a
  plan's own sections. Six mutations of this branch's two files
  confirm the boundary: only a status outside the shared vocabulary
  is caught, by `plan-index`. A withdrawn disposition left in the
  index row, a `path:line` citation sent to line 99999, a falsified
  survey diffstat, and a `Bugs fixed` bullet that stops short all
  leave every `plan-*` check passing.
* Revisit capping the issue-body file list if a repo much larger
  than ryll adopts the tooling. Unreached so far: ryll#304's body
  is 76 lines at 63 files, and because the body is never refreshed
  it does not grow with the backlog.
* An audited repository can end its own review obligation with one
  `exclude` line, and both `review-coverage` and
  `review-scope-completeness` report pass; deleting
  `.vscode/review-scope.toml` does the same via `not_applicable`.
  Phase 6 declines this as a defect -- `scope_orphans()` trusts an
  `exclude` by design and says so in its docstring -- but the
  mitigating control is human review of the narrowing pull request,
  which is the thing the audit measures. Closing it needs the audit
  to notice a sharp drop in a repository's in-scope count between
  runs, and therefore needs cross-run state it does not keep. ryll
  compounds it: `ci.yml` classifies a `review-scope.toml` change as
  a review artefact, so such a pull request skips every test tier.
* ~~Escape `render_issue_items` in
  `scripts/audit-manage-issues.py`.~~ Done in the findings pull
  request, development#158, merged as `3859865`, rather than
  deferred, at the operator's direction. The fleet-wide caveat that
  made it a deferral still applies and is
  recorded in that commit: it changes every criterion's issue
  bodies, not only the two review checks'.
* ~~Catch `OSError` alongside `subprocess.TimeoutExpired` wherever
  a `Check` shells out.~~ No sweep was needed: a survey found
  `ReviewCoverage` and `ReviewScopeCompleteness` were the only two
  sites in the package not already catching it, and both are fixed
  in the findings pull request, development#158, merged as
  `3859865`. The reachable trigger is `cwd` rather than a missing
  script -- see F2's correction.
* The test gaps phase 6's observations name, none of which have a
  test today: `in_scope == 0` is untested, so the boundary that
  decides whether a repository has zeroed its own obligation is the
  one boundary nothing exercises; `test_status_mutates_nothing` is
  weaker than its sibling `test_scope_orphans_mutates_nothing`,
  which diffs `git status --porcelain` rather than re-reading three
  named paths; and ryll has no test at all touching the prune
  machinery, against five in-repository precedents for exactly that
  kind of smoke test. See phase 6's "Observations, recorded and not
  acted on" for what each one was found by.
* `plan-phase-landing` assumes a plan's findings are fixed *after*
  its push-audit phase merges, so that the findings pull request is
  available to carry that phase's merge commit. Phase 6 inverted
  it: the findings landed as development#158 (`3859865`) while the
  audit phase was still going through review rounds, which left the
  last row's `Merged` cell with no carrier and the carve-out
  claimed outside the conditions the block states. The block should
  say what to do when the findings land first -- the common case
  whenever the audit phase runs more than one review round. Not
  fixed here: editing a shared block is
  `PLAN-plan-template-blocks`'s work and fans out to every
  repository carrying it; filed as development#163 so the next plan
  to hit the inverted ordering finds the amendment rather than
  re-deriving the exception.
* ~~Phase 6's F4, F5 and F6, all in ryll and all advisory, declined
  for this plan and recorded here because no pull request carries
  them.~~ Done, and already done when the bullet was written: they
  landed in ryll#389 (`205ef7d`) on 2026-09-20, as `7c6bc1e`,
  `0fb0d8f` and `9904770`. The bullet is struck rather than deleted
  because it is the clearest instance of this plan's recurring
  failure -- a present-tense claim about another repository's tree,
  made without reading that tree -- and the Outcome's disposition
  for F4-F6 is where the corrected record lives. The CI-hygiene
  bullet below was the one it proposed pairing them with, and that
  one is still open.
* ryll's CI hygiene on the prune job, also from phase 6's
  observations: the job has no `timeout-minutes` where
  development's has ten, and no ryll job runs `actionlint`, so a
  typo in the `if: github.ref` guard would make the job *skip* --
  green, not red -- and that guard is the only thing standing
  between the workflow and an unreviewed push to `develop`. The
  second is the one worth doing first, because its failure mode is
  silent.
* The fleet-wide credential and pinning fix `196db2f6` deferred in
  August, raised again by phase 6 and recorded here so it need not
  be raised a third time: `persist-credentials: false` on the
  PAT-bearing checkout, and a pinned clone of the tooling the prune
  job executes. kerbside carries the same script.

## Bugs fixed during this work

* Phase 5's review rounds on #139 corrected the loop-termination
  argument in `docs/code-review-tracking.md`, which claimed a
  workflow-token push does not retrigger the workflow. ryll pushes
  with a PAT and it does retrigger, once. The same false claim was
  found and fixed in `AGENTS.md`, `ARCHITECTURE.md` and
  `docs/audits/review-coverage.md`.
* Phase 5 corrected `REVIEW_BACKLOG_THRESHOLD`'s documented location
  from `scripts/audit-check.py` to `scripts/audit/checks/review.py`,
  where the restructure had left it.
* Phase 5 added the caveat that a filed issue's file list is never
  refreshed, so a long-lived `review-coverage` issue understates the
  backlog it names, and filed development#138 for the cause.
* Phase 6 found a fifth instance of the default-branch claim, at
  `AGENTS.md:132-134`, which two rounds of review had passed over.
  It is recorded as F1 in phase 6's Outcome and landed in that
  phase's findings pull request, development#158 (`3859865`), not
  here.
* Phase 6 hardened `ReviewCoverage.run()`, which wrapped its
  `subprocess.run` in a handler for `subprocess.TimeoutExpired`
  alone where every other shelling-out check in the package also
  caught `FileNotFoundError`, and `registry.py:135` calls
  `run_check()` with no handler at all -- so one raise costs the
  repository all fifty-five criteria rather than one. The `try` now
  carries `except OSError`, with a test in each direction. Recorded
  as F2 in phase 6's Outcome and landed in development#158
  (`3859865`).
* Phase 6 escaped `render_issue_items` in
  `scripts/audit-manage-issues.py`, which rendered `git ls-files`
  paths into issue bodies with no escaping: a path containing a
  backtick closes the code span, and one containing a newline
  injects raw lines into a body authored by shakenfist-bot.
  `missing` and `findings` now go through `defuse_item()` as
  `details` already did. This is the audit's most consequential
  finding, and the widest -- it is every criterion's issue bodies
  fleet-wide, not this plan's machinery, which is why it was first
  declined and referred before the operator overrode that. Recorded
  as F3 in phase 6's Outcome and landed in development#158
  (`3859865`).
* Phase 6's three ryll findings landed in ryll#389 (`205ef7d`):
  `prune-reviews.yml` granted `GITHUB_TOKEN` `contents: write` that
  had been dead since the job moved to a PAT, now `permissions: {}`
  (`7c6bc1e`); "`REVIEWS.md` is generated; never edit it by hand"
  had disappeared from every agent-facing document and survived
  only in the generated header, now back in `docs/development.md`
  (`0fb0d8f`); and `scope-orphans` was undocumented in both
  `tools/review-tracking.sh` and `docs/development.md` (`9904770`,
  `0fb0d8f`). Recorded as F4 to F6 in phase 6's Outcome, whose
  disposition also records that these were declined in writing
  *after* the pull request fixing them had already merged, and
  that the declining is withdrawn.
* Phase 6 corrected the factual errors in its own briefs at source,
  listed under "What the audit corrected about this plan". The
  largest was the premise that `ReviewCoverage.run()` and
  `review-tracking.py status` might disagree about coverage: there
  is one implementation, so they cannot.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

Phase 6 used two gates rather than one. The first, before any agent
ran a command, required it to restate its ranges and the file count
it expected, because every diff in `PUSH-AUDIT.md` was at that point
written `main...HEAD` and returns nothing against work that merged
weeks ago -- a report from an empty range is indistinguishable from a
clean audit. All six agents passed it and all six file counts
matched. The second, before the Execution table moved, required a
statement of whether the audit had produced findings, because that
decides the shape of the close-out: findings mean the index row
stays `In progress` until they land or are declined in writing. It
did produce findings, and all six landed ahead of this phase rather
than after it: F1 to F3 in development#158 (`3859865`) and F4 to F6
in ryll#389 (`205ef7d`), so the row reaches `Complete` here.
*Corrected on 2026-09-21: this said F4 to F6 were declined, which
is what the Outcome said before ryll's `develop` was re-read.*
