# Consistency audit phase 2: retire the comment addresser

Master plan:
[PLAN-consistency-audit.md](/components/kerbside/plans/PLAN-consistency-audit/)

**Planning effort:** medium. The deletion itself is trivial. The
work is in the residue: four files come out, but five more
mention them, and one of those mentions is the stated reason for
a guard condition that must survive the deletion it justifies.

## Scope

**In scope:**

- Issue #360 -- delete
  `.github/workflows/pr-address-comments.yml`,
  `tools/address-comments-with-claude.sh`,
  `tools/render-review.py` and `tools/review-schema.json`,
  in a single commit, as the audit spec requires.
- `kerbside/tests/unit/test_render_review.py`, which cannot
  outlive `render-review.py`.
- The five surviving references to the deleted files:
  one row in `.claude/CLAUDE.md`, three comments in
  `.github/workflows/pr-re-review.yml`, and one in
  `tools/shellcheck-wrap.sh`.
- The four review marks and their weAudit entries.

**Out of scope:**

- The rest of the `ci-review-automation` audit. Every other
  measured criterion already passes; see the survey below.
- The reviewer-confirmed half of that audit spec (prompt
  wording, `pr-fix-tests.yml`). Not measured, not failing, and
  not what #360 is about.
- Moving the CI workflow inventory out of `.claude/CLAUDE.md`.
  That list arguably belongs in `docs/testing.md` under the
  documentation discipline, but relocating it is a separate
  argument and would bury this phase's security change in a
  documentation reshuffle. Recorded as Future work.

## What the survey found

The master plan's phase 2 sketch was written on 2026-08-29 and
is accurate about the shape of the work. Two of its specifics
were wrong, and both are corrected at source in the master plan
as part of the planning commit, so this is the only place they
need reading twice.

**The four files and the test all exist**, at
`.github/workflows/pr-address-comments.yml` (237 lines),
`tools/address-comments-with-claude.sh` (592),
`tools/render-review.py` (356), `tools/review-schema.json` (96)
and `kerbside/tests/unit/test_render_review.py` (105). The test
resolves `parents[3] / 'tools' / 'render-review.py'` and calls
`_spec.loader.exec_module` at module scope, so it fails at
collection rather than as a test failure -- `tox -e py3` stops
producing results at all.

**`pr-re-review.yml` carries three references, not one.** The
sketch names line 10 only. The other two matter more than it
does:

- Line 10 -- "pr-address-comments.yml splits for the same
  reason", justifying why trigger and review are separate jobs.
  Becomes false; the reasoning stands on its own without it.
- Lines 34-37 -- the stated reason for the live
  `github.event.comment.user.type != 'Bot'` guard is that
  "pr-address-comments.yml quotes model-generated summaries back
  into the pull request, and a summary that mentions a trigger
  phrase would re-fire the lane." **The guard must stay**; only
  its justification is being deleted. `pr-auto-review.yml` still
  posts bot comments carrying model-generated prose, so the
  hazard survives its stated cause.
- Line 106 -- "pr-address-comments.yml has always done this;
  this file did not", a historical contrast explaining
  `persist-credentials: false`. The rationale above it is
  self-contained; the contrast dangles.

**`tools/shellcheck-wrap.sh` line 7 also refers to it**, and the
sketch does not mention this at all. Its header comment claims
"the only mentions in .github/workflows are comments in
pr-address-comments.yml explaining why it is deliberately
skipped there". After this phase there are no such mentions, so
the sentence describes a file that does not exist.

**Nothing in `docs/` refers to the addresser.** `docs/testing.md`
does not list any of the three bot-triggered workflows. The only
inventory is the `.claude/CLAUDE.md` row at line 139. Two
historical plan files mention it
(`PLAN-demo-install-phase-04-ci-lane.md` line 157,
`PLAN-two-tier-ci-phase-03-merge-queue.md` lines 271 and 488);
those are records of past work and must not be edited.

**The workflow is live, not dormant.** It triggers on every
`issue_comment` created, and job-level `if:` conditions skip it.
`gh run list` shows five runs in the six days to 2026-08-29, all
`skipped`. So "unused" means never authorised past the trigger
gate, not never invoked -- the `contents: write` at line 33-36
is granted on each of those runs.

**Every other measured criterion of the audit already passes**,
verified individually against the spec at
`development/docs/audits/ci-review-automation.md`:
`pr-re-review.yml` and `pr-retest.yml` both exist;
`pr-re-review.yml` reaches `shakenfist/actions/pr-bot-trigger@main`
(line 55) and `shakenfist/actions/review-pr-with-claude@main`
(line 115); the only `secrets: inherit` in the tree is on
`export-repo-config.yml` (line 15), which the spec names
explicitly as a correct use. The `automated_reviewer` job at
`functional-tests.yml` line 1076 is **not** the hand-written
kind the spec supersedes -- it is a call to
`shakenfist/actions/.github/workflows/pr-auto-review.yml@main`
with the right permissions block. Leave it alone.

## Decisions

**1. The deletion is one commit, and it carries the security
argument.** The audit spec says "Remove all four in one commit",
so the four files plus the orphaned test land together. The
commit message is where the reason for the phase lives: a
workflow holding `contents: write` while checking out pull
request code, for automation superseded by
`shakenfist/actions/review-pr-with-claude@main`. A reader six
months from now finds that in `git log`, not in a plan file.

**2. Reference repair is a separate commit from the deletion.**
Keeping them apart means the deletion commit is exactly the four
files and the test, and reviewable as such against the spec. The
alternative -- one commit for everything -- would satisfy the
audit equally, since the spec constrains the four files rather
than forbidding company. Separation is for the human reviewer,
not the checker.

**3. The bot-comment guard survives; only its justification
changes.** This is the decision most likely to be argued with,
because the tempting reading is that deleting the addresser
deletes the reason for the guard. It does not. The guard exists
because bot-posted comments can quote model prose containing a
trigger phrase, and `pr-auto-review.yml` posts exactly such
comments today. Removing the guard along with its stale comment
would introduce a self-retriggering lane, which is a worse
outcome than the audit failure this phase is fixing.

**4. Review marks are pruned in this branch, and the prune
commit is not signed.** Two things make this non-obvious.
First, `review-tracking.py`'s `cmd_prune` reads
`blob_sha('HEAD:%s' % path)`, so it only sees a deletion once
that deletion is committed -- the prune must run after step 2a,
never before. Second, the project convention that review-mark
commits are signed applies to marks being *added*:
`prune-reviews.yml`'s own header states that "prune only ever
removes marks, so the commit does not need to be signed -- the
attestations live in the signed commits that introduced the
stamps." Pruning by hand here rather than leaving it to the
post-merge workflow keeps the branch internally consistent, so
`REVIEWS.md` never names a file the same branch deleted.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | low | haiku | none | In the kerbside worktree at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/kerbside-wt-consistency-p2`, `git rm` exactly five files in one commit: `.github/workflows/pr-address-comments.yml`, `tools/address-comments-with-claude.sh`, `tools/render-review.py`, `tools/review-schema.json` and `kerbside/tests/unit/test_render_review.py`. Change nothing else -- the dangling references in other files are step 2b's job, and touching them here would break the audit's "all four in one commit" requirement into something harder to review. The commit message body must state the security reason: the workflow granted `contents: write` at workflow scope while checking out pull request code, for automation superseded by `shakenfist/actions/review-pr-with-claude@main` and never authorised past its trigger gate in practice. Then run `tox -e py3` and confirm it still collects and passes -- before this commit it would have failed at collection had `render-review.py` gone alone. |
| 2b | medium | sonnet | none | Repair the five references to files step 2a deleted. (1) `.claude/CLAUDE.md` line 139: delete the whole list row `` - `pr-address-comments.yml` - Bot-triggered comment addressing ``, leaving the `pr-re-review.yml` and `pr-retest.yml` rows. (2) `.github/workflows/pr-re-review.yml` line 10: delete the sentence "pr-address-comments.yml splits for the same reason." -- the paragraph reads correctly without it. (3) Same file, lines 34-37: the `if:` guard `github.event.comment.user.type != 'Bot'` **must stay exactly as it is**; rewrite only the comment above it so the hazard is attributed to what still causes it -- `pr-auto-review.yml` posts bot comments containing model-generated review prose, and a phrase inside one would re-fire the lane, since `contains()` does not care that the phrase is inside a quote. Do not remove the guard. (4) Same file, line 106: delete the trailing sentence "pr-address-comments.yml has always done this; this file did not." -- the four lines of rationale above it stand alone. (5) `tools/shellcheck-wrap.sh` line 7: the header claims "the only mentions in .github/workflows are comments in pr-address-comments.yml explaining why it is deliberately skipped there"; rewrite that clause so it says pre-commit is not invoked by any workflow or tox environment, without citing a file that no longer exists. Do **not** edit `docs/plans/PLAN-demo-install-phase-04-ci-lane.md` or `docs/plans/PLAN-two-tier-ci-phase-03-merge-queue.md`: those are historical records of completed work. Finish with `pre-commit run --all-files`, which includes actionlint over the edited workflow. |
| 2c | low | haiku | none | Prune the review marks for the deleted files. This must run **after** step 2a is committed, because `review-tracking.py` compares against `HEAD:<path>` and cannot see an uncommitted deletion. From the worktree run `tools/review-tracking.sh prune`. Expect it to report four paths as `gone`: `.github/workflows/pr-address-comments.yml`, `tools/address-comments-with-claude.sh`, `tools/render-review.py` and `tools/review-schema.json`. It rewrites `.vscode/mikal.weaudit`, `.vscode/mikal.weaudit-shas.json` and regenerates `REVIEWS.md`. Never hand-edit any of those three. Commit the result on its own; this commit does **not** need to be signed, because prune only removes marks (see `prune-reviews.yml`'s header comment for why). If prune reports any path beyond those four, stop and report -- that means develop moved under the branch and something unrelated went stale. |
| 2d | medium | sonnet | none | Verify the phase. From the worktree run, and report each result verbatim including failures: (1) `grep -rn -E 'pr-address-comments\|address-comments-with-claude\|render-review\|review-schema' --exclude-dir=.git .` and confirm the only remaining hits are in `docs/plans/`, which are historical records; (2) `pre-commit run --all-files`; (3) `tox -e py3` and `tox -e flake8`; (4) `python3 /srv/kasm_profiles/mikal/vscode/src/shakenfist/development/scripts/audit-check.py --repo-path <worktree> --repo-name kerbside`, confirming the failure count has dropped from 3 to 2 and that `ci-review-automation` now passes; (5) `grep -n "user.type != 'Bot'" .github/workflows/pr-re-review.yml` returns a hit, proving step 2b did not remove the guard along with its comment. Fix nothing you find here without reporting it first. |

Each step is its own commit:

- 2a: `Retire the pull request comment addresser.`
- 2b: `Repair references to the retired addresser.`
- 2c: `Prune review marks for the retired addresser.`
- 2d: no commit (verification).

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Step 2b removes the `user.type != 'Bot'` guard along with the stale comment that explains it, creating a lane that re-fires on its own review comments. | Decision 3 states the guard survives, 2b's brief says so twice in bold terms, and 2d greps for the guard as a done-criterion. This is the single most likely way to get this phase wrong. |
| The deletion commit includes the reference repairs, and a reviewer can no longer check it against the audit spec's "all four in one commit" wording. | Decision 2 splits them, and 2a's brief explicitly forbids touching anything else. |
| Prune runs before the deletion is committed and reports nothing, so `REVIEWS.md` keeps naming four files that no longer exist. | 2c's brief states the ordering and the reason (`HEAD:<path>`), and names the four paths it should report as `gone`. If it reports zero, the ordering was wrong. |
| `tox -e py3` fails at collection because `test_render_review.py` was left behind while `render-review.py` went. | The test is in 2a's deletion list, and 2a ends by running `tox -e py3`. The failure mode is loud rather than subtle -- no tests run at all. |
| A historical plan file gets "fixed" to remove a now-dangling reference, rewriting the record of completed work. | 2b names the two files and forbids editing them; 2d expects hits in `docs/plans/` and treats them as correct. |
| The phase lands but #360 stays open because nothing re-runs the audit. | It closes on the next daily audit run. Do not close it by hand -- the master plan's success criteria require closure by a passing run, so a hand-closed issue hides whether the fix worked. |

## Definition of done

- [ ] None of `pr-address-comments.yml`,
      `address-comments-with-claude.sh`, `render-review.py` or
      `review-schema.json` exists anywhere in the tree. This
      command prints only paths under `docs/plans/`:

```bash
grep -rln -E 'pr-address-comments|address-comments-with-claude|render-review|review-schema' \
    --exclude-dir=.git .
```

- [ ] The four deletions plus `test_render_review.py` are a
      single commit, and `git show --stat` on it lists exactly
      five files.
- [ ] `.github/workflows/pr-re-review.yml` still contains
      `github.event.comment.user.type != 'Bot'`, and the comment
      above it names a workflow that exists.
- [ ] `REVIEWS.md` names none of the four deleted files, and was
      regenerated by `tools/review-tracking.sh prune` rather than
      hand-edited.
- [ ] `tox -e py3` collects and passes; `tox -e flake8` passes;
      `pre-commit run --all-files` is clean.
- [ ] `audit-check.py --repo-path <worktree> --repo-name
      kerbside` reports 2 failures, not 3, and
      `ci-review-automation` is not among them.
- [ ] Issue #360 is closed by a passing consistency audit run,
      not by hand.

## Back brief

The phase deletes automation rather than adding any, so the
review question is not "does it work" but "what did it take with
it". Two gates:

- **Gate 1, before step 2b commits.** The rewritten comment above
  the `user.type != 'Bot'` guard in `pr-re-review.yml` is the one
  piece of new prose in this phase, and it is load-bearing: it is
  the only record of why that condition exists. Show the operator
  the before and after of that comment block, and the unchanged
  `if:` below it, before committing.
- **Gate 2, after step 2d.** Report the `audit-check.py` failure
  list. If `ci-review-automation` still fails after all four
  files are gone, the checker is matching something this survey
  did not find, and that is worth understanding before the phase
  is called done rather than after.

Phase 1's issues (#368, #370, #373) were still open when this
plan was written, waiting on the daily audit run that had not yet
fired since the merge. If they are still open when this phase
lands, that is a signal about the audit, not about phase 1 --
check whether the run happened before assuming either phase
failed.
