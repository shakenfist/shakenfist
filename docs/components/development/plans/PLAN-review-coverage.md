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
| 7 | both | end-to-end verification (phase 5) | In progress | |
| 8 | both | push audit over each PR (phase 6) | Not started | |

Step 7 stopped being blocked when both pull requests merged in
August; phase 5 above is the plan for it. The statuses were brought
into the shared vocabulary on 2026-09-17 -- see that phase's survey.
Step 7 reaches `Complete` when its own pull request merges and the
`Merged` column records that commit, not before.

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
* Revisit capping the issue-body file list if a repo much larger
  than ryll adopts the tooling. Unreached so far: ryll#304's body
  is 76 lines at 63 files, and because the body is never refreshed
  it does not grow with the backlog.
