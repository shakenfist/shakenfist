# Phase 2 — the push audit, and the retirement it recommended

Parent plan: [PLAN-fuzz-autofix.md](/components/instar/plans/PLAN-fuzz-autofix/)

This phase began as the push audit that ends every master plan. The
audit ran in full. Its verdict is that the automated fuzzer bug fix
workflow should be removed rather than repaired, and the second half
of this phase removes it.

The file keeps its `push-audit` name because that is what the phase
did and how it reached its conclusion. The retirement is the finding,
not a change of subject.

## Goal

Run `PUSH-AUDIT.md` over everything this plan built, then act on what
it found. What it found is that the loop's safety boundary does not
hold, that its orchestration cannot be tested, and that in the two
days it was actually functional it produced one pull request whose
value came from the human review it received rather than from the
fix it generated. So: delete the machinery, keep the fuzzer's issue
*reporting*, and close the plan with the reasoning written down.

## Planning effort

High. The scoping was the hard part -- see *What the survey found* --
and the verdict is a reversal of the master plan's mission, which
deserves the evidence set out in full rather than asserted.

## Review effort

Medium, concentrated on two things: the evidence in *The verdict*,
and the removal boundary in *Scope*. The individual removal steps are
mechanical.

## Scope

**Removed** -- used by nothing but the autofix loop, verified with
`grep -rln` across the tree:

* `.github/workflows/fuzz-autofix.yml`
* `tools/ci/stage-autofix-changes.sh` and
  `tools/ci/test-stage-autofix-changes.sh`
* `tools/ci/autofix-artifact-patterns.sh` (sourced only by the stager)
* `tools/autofix-prompt-base.txt`
* `tools/fuzz-issue-schema.json` -- referenced by **no code at all**,
  even today (see finding H2); it has been dead since it was written
* the `Test the autofix stager` step in `functional-tests.yml`'s
  `ci-tooling` job
* the `autofix-failed`, `autofix-complex` and `autofix-attempted`
  labels, and the stale `autofix-failed` label on any open issue

**Kept, deliberately**

* `tools/ci/report-fuzz-crash.sh` and its test. The *reporting* half
  is where this plan's value actually landed: fuzzer findings still
  become issues automatically. Its structured-JSON body was designed
  as a contract with the workflow being deleted, but the format is
  good for a human reader too and rewriting it is churn for its own
  sake. The comments that describe it as a contract with
  `fuzz-autofix.yml` are retargeted, and
  `test-report-fuzz-crash.sh:185` -- which asserts "the body satisfies
  `fuzz-autofix.yml`'s validation predicate" -- is re-justified in its
  own terms or removed.
* `tools/ci/claude-result.sh` and `tools/ci/test-claude-result.sh`.
  Still used by `.github/workflows/test-drift-fix.yml`. Only its
  comments change.
* `tools/ci/prepare-testdata.sh`. Used by five workflows.
* Everything in `coverage-fuzz.yml` and `differential-fuzz.yml`.

**Out of scope**

* **Fixing any audit finding in the deleted code.** C1, C2, H2, H3 and
  the duplication findings all describe files that cease to exist.
  They are recorded as the evidence for removal, not as work.
* **`test-drift-fix.yml`.** Several findings touch it because it
  shares helpers and idioms with the autofix loop. It is a different
  automation with a different threat model -- it operates on this
  repository's own test failures, not on issue text a third party can
  write. The findings that apply to it are carried to the master
  plan's Future work for a separate decision, not actioned here.
* **PR #533 and the rebase bug behind it.** Merged as `aaee69b` and
  reviewed on its own merits.

## The verdict

### What the loop delivered

| Measure | Value |
|---|---|
| Workflow runs | 60 (58 scheduled, 2 dispatched) |
| Pull requests produced | 1 (#533) |
| Fixes merged | 1 (`aaee69b`) |
| `security-audit` issues closed | 30, of which 29 by hand |
| `security-audit` issues open | 2 |

**The honest caveat, stated first.** 58 of those 60 runs were made by
a workflow that could not have produced a pull request under any
circumstances: it never checked out `instar-testdata`, so
`make test-container-core` could never pass. The loop has only been
functional since `931b5a9` (#530) merged on 2026-08-30, and in that
window it went one for one. "60 runs, one pull request" overstates
the failure. The fair statement is that the sample is one.

### The argument that does not depend on the count

#533 still required a full human review round, and that review found
a wrong premise in the generated fix plus a **live** vmdk defect the
fix had missed -- the planner's unbounded descriptor offset, which the
review had initially misfiled as latent on the grounds that the path
was "not yet wired into `src/operations/rebase`", when it is wired in
and dispatched from `main.rs:1906`. The value in that episode was the
review, not the generation.

That generalises badly here. An autonomous fixer earns its keep when
its output can be accepted without close reading. These are
security-relevant parser bugs found by a fuzzer; they must be read
closely. The premise is in tension with the domain, independently of
any defect found below.

Supporting, from the audit and from phase 1:

* Verification is weaker than the pull request body implies: the
  reproducer is never executed (#529), so "verified" means the build
  and the core tests.
* Every attempt exhausts its turn budget (#534) -- 31/30, 31/30,
  41/40 -- so the commit summary, which the prompt asks for last, is
  never emitted and the message falls back.
* The safety boundary does not hold (C1, C2 below) and can be
  rewritten by the process it constrains (H3).
* The orchestration that holds it together cannot be tested at all
  (wave 2b), and it is where two of the three bug classes that
  actually bit this workflow live.
* There is no backlog. Two open `security-audit` issues.

### Why the seam is clean

`grep -rln` over the tree shows `stage-autofix-changes.sh`,
`autofix-artifact-patterns.sh` and `autofix-prompt-base.txt` are used
by nothing but `fuzz-autofix.yml`, and `fuzz-issue-schema.json` by
nothing at all. `claude-result.sh` and `prepare-testdata.sh` are
shared and stay. Every other reference in the tree is a comment.

There is precedent, in this plan's own history: `14e9cba` retired the
comment addresser on the same reasoning, and that retirement was
this plan's own Future work.

## What the survey found

Recorded before the pivot, and still true. The master plan's phase 2
section assumed a scope that did not exist in the form it described.

### There was no "union of merge ranges" to audit

Every phase had merged, so `git diff develop...HEAD` was empty, and
most of the work predated the Execution table. The scope was
reconstructed commit by commit; the command is in Decision 1 and
yields **7,887 lines** of patch, beginning with the creation of
`.github/workflows/fuzz-autofix.yml` in `2fcf75e`. Two rows were
re-derived independently before the audit began, which found the
commit counts for #509 and #520 understated -- eleven not nine, and
nine not five. The scope was unaffected, since both rows take the
whole merge, but wave 2b walks those commits one at a time and an
understated count would have let it stop early.

### The audit instrument was broken in three ways

`tools/audit/wave1.sh`'s inline-script check, which is the one
mechanical check with something to say about a YAML-and-bash diff:

1. It printed awk's `NR` where it meant `FNR`, so every line number it
   reported for any workflow but the alphabetically first was past
   that file's end. It claimed a finding at
   `fuzz-autofix.yml:2147` in a file of 1,125 lines.
2. `head -20` truncated 32 hits at a point that hid two of
   `fuzz-autofix.yml`'s four and all six of `test-drift-fix.yml`'s.
3. **Found during wave 2a, after the first two were fixed:** the awk
   exits block-tracking at the first blank line, and GitHub `run:`
   bodies routinely open with `cd $DIR` then a blank. Measured with a
   blank-line-tolerant variant: **16** oversized blocks in
   `fuzz-autofix.yml` against the 4 reported, and **15** in
   `test-drift-fix.yml` against 6.

(1) and (2) were fixed in `b9e6743`. (3) is fixed in this phase's
step 1. Every plan that has run this runbook has been reading
fictitious line numbers from an undercounted list.

### No unquoted-variable check runs on workflow `run:` blocks

`.github/actionlint.yaml:16-23` disables SC2086, SC2046, SC1090 and
SC2143 for `.github/workflows/*.yml`, and the `shellcheck` pre-commit
hook matches only `^(scripts|tools)/`. Verified by building the
pinned actionlint and running it directly, not by reading the config.
Both workflows' `run:` blocks are therefore unchecked. This survives
the retirement and applies to every remaining workflow.

## Audit findings

Kept in full: they are the evidence for the verdict, and the ones
touching surviving files still need dispositions. Every critical and
high was re-verified by the management session against the code
before being recorded, per the risk table.

### Wave 1 — mechanical

Exit **0**. `pre-commit` (including actionlint and shellcheck),
rustfmt/clippy, `make instar`, `make check-binary-sizes`,
`make test-rust` and `make fuzz-build` all passed. The inline-script
advisory reported 32 hits; the ten in scope were confirmed to be
`run: |` lines with `sed`.

### Wave 2 mechanical — empty, and why

`tools/audit/wave2-mechanical.sh` computes from `git diff
develop...HEAD` and greps for Rust constructs. This plan added no
Rust and the range holds only plan documents. Of its nine checks,
eight were vacuously empty and one (doc files touched) reported this
branch's own plan files. Recorded as empty rather than as a pass.

### Wave 2a — code quality (all advisory)

* **`fuzz-autofix.yml:160` — a live bug.** `grep -qF "Fixes #${NUM}"`
  is an unanchored substring match. Demonstrated: with one open pull
  request saying `Fixes #400`, issues **#4 and #40 are both silently
  skipped**, permanently. A plausible contributor to issues sitting
  unprocessed. *Removed with the workflow.*
* **Commit-summary extraction diverged between the two workflows.**
  `fuzz-autofix.yml:887-909` squeezes blanks with `cat -s`;
  `test-drift-fix.yml:512-535` uses `sed '/^$/N;/^\n$/d'`. Measured on
  `para one\n\n\npara two`: `cat -s` preserves the paragraph break,
  the sed idiom deletes both blank lines and merges the paragraphs.
  *Carried to Future work: the surviving copy is the wrong one.*
* The `STAGER_RC` check is duplicated verbatim four times
  (`:398`, `:487`, `:745`, `:826`), the issue-JSON field extraction
  twice (`:242-263`, `:622-644`), and the complexity computation twice
  (`:419-449`, `:765-791`). *Removed with the workflow.*
* `verify2` never sets `failure_reason` for the build and test cases
  that `verify1` sets. Harmless today, copy-paste drift.
  *Removed with the workflow.*
* The resparsify block is duplicated verbatim between the two
  workflows, and **this plan introduced the duplicate** in `931b5a9`
  (#530). *The surviving copy in `test-drift-fix.yml` is fine alone.*
* `STAGER_RC=$(cat ... || echo 0)` makes a read failure
  indistinguishable from success; the retry's reset is unverified;
  `stage-autofix-changes.sh:211` swallows the failure of the reset
  that implements its own security boundary. *Removed.*
* `test-drift-fix.yml:168-175` cannot distinguish "no prior failures"
  from "every log fetch failed", and can post "All tests pass! No
  fixes needed." on a pull request whose CI is red.
  *Carried to Future work -- this one survives.*
* Comment proportion on `stage-autofix-changes.sh`'s 87-line header:
  **justified**, no trimming recommended. It records three named prior
  incidents and an explicit warning against repeating the staging
  heuristic that grew defects across four review rounds.

### Wave 2b — tests

The single-invocation stager behaviour is genuinely well pinned:
walking all twenty commits in #509 and #520, nearly every defect has
a test that would have failed before its fix. The gaps are structural:

* **The cross-attempt orchestration is untested and lives only in
  YAML** -- refuse, read `--refused-file`, `rm -rf` those paths, take
  a fresh snapshot, run the baseline again. No test calls the script
  three times in that order with the intervening filesystem mutation.
* `autofix-artifact-patterns.sh` has no direct test; roughly six of
  its eleven pattern alternatives are never exercised.
* Step ordering, stale-runner-state cleanup, the `STAGER_RC` gate
  logic and the gzip guard are all YAML-only and unreachable.

One finding worth keeping after the code is gone: `ea036dd`'s commit
message records that **a previous version of this suite pinned the
wrong behaviour** -- "the test suite pinned the dropping as intended,
so the gap was locked in". A green suite meant only "matches current
assertions". That is a general lesson about this repository's tests,
not about the autofixer.

### Wave 2c — documentation

The four documents describing the machinery were accurate against the
code, including the 30-turn figure, which is correct rather than
stale. Two findings:

* The master plan's *Diagnosis* section describes the pre-`#509`
  workflow in the present tense and cites line numbers (285, 348, 505,
  561, 637) that no longer resolve. *Resolved by the retirement
  rewrite, which makes the whole section explicitly historical.*
* `CHANGELOG.md` has no entry for #226, #509, #520, #530 or #535.
  Wave 2c argued the project's own precedent favours entries. The
  management session checked and **narrowed that**: `grep -c
  '\.github/workflows' CHANGELOG.md` returns 2, and both are attached
  to user-visible artefacts rather than to CI plumbing. The precedent
  is thin, so no back-filled entries. The *removal* does get an entry,
  because it withdraws documented behaviour.

### Wave 2d — security

Re-briefed away from `PUSH-AUDIT.md`'s VMM threat model per
Decision 5. Every critical and high below was independently
re-measured or re-read by the management session.

* **C1 (critical) — `git add -N` defeats all three stager refusals.**
  `stage-autofix-changes.sh:195-201` computes new files from `git diff
  --cached --diff-filter=A`, which does not list an intent-to-add
  entry, and `git ls-files --others` does not either because it is in
  the index. The stager's own `git add -u` at `:217` then fills in the
  content. Measured in a throwaway repo against the real script:
  exit 0, no refusal, no report, payload staged. `git status
  --porcelain` does show it; the script does not look there.
* **C2 (critical) — nothing ever inspects `HEAD`.** No `merge-base`,
  no `rev-parse HEAD`, no comparison against `origin/develop` exists
  anywhere in the workflow or the stager; the only hit is the branch
  creation at `:196`. Measured: with the model's own commit on the
  branch the stager exits 0, the index shows only the tracked edit,
  and the payload is pushed. The retry reset does not undo it --
  `reset`/`checkout`/`clean -fd` do not touch committed history.
* **H1 (high) — the push token sits in the tree Claude edits.**
  `fuzz-autofix.yml:63-68` omits `persist-credentials: false`, while
  `pr-re-review.yml:142` sets it, with a comment at `:129` spelling
  out exactly this hazard for exactly this shape of job.
* **H2 (high) — the JSON schema is enforced nowhere.**
  `tools/fuzz-issue-schema.json` is referenced by no code in the
  repository. The live gate at `:104-109` is a four-key `jq -e`
  truthiness test, so the `source` enum, every `maxLength` and
  `additionalProperties: false` are unenforced. Issue title and body
  fields reach the prompt unbounded, unescaped and at column 0.
* **H3 (high) — the stager cannot protect the code that runs it.**
  It is invoked by relative path (`:306`, `:364`, `:934`) from the
  checkout the model just edited, as is `claude-result.sh`. Modifying
  a tracked file is always permitted, and `make test-container-core`
  runs unconditionally at `:528` and `:864` from the tree's own
  `Makefile`. This is why fixing C1 and C2 alone would not have been
  enough.
* **H4, H5 (high)** — the GitLab testdata token is persisted into
  `.git/config` with its path exported via `GITHUB_ENV`; the raw
  Claude stream and session transcripts are uploaded as artifacts on
  a **public** repository, giving an exfiltration channel that needs
  no network egress.
* **M1-M4, L1-L6** — incomplete stale-state cleanup that can post a
  previous run's output into this run's issue comment; a concurrency
  group that does not cover the two sibling workflows sharing the
  runner's workspace and its `claude-logs/` directory; a cancelled run
  permanently removing an issue from the queue via an
  `autofix-attempted` label nothing clears; `$GITHUB_OUTPUT`
  injection via issue title; argument injection via `--max-turns`;
  markdown injection into the pull request body.

All of these describe deleted files except the shared-runner
concurrency issue (M2) and the `$GITHUB_OUTPUT` delimiter habit,
which are carried to Future work.

## Decisions

1. **The audit read a reconstructed patch and the current state, not
   a range.** The command is below; it produced 7,887 lines.

   ```bash
   PATHS=".github/workflows/fuzz-autofix.yml \
       .github/workflows/test-drift-fix.yml \
       .github/workflows/functional-tests.yml \
       tools/autofix-prompt-base.txt tools/fuzz-issue-schema.json \
       tools/ci/stage-autofix-changes.sh \
       tools/ci/test-stage-autofix-changes.sh \
       tools/ci/claude-result.sh tools/ci/test-claude-result.sh \
       tools/ci/autofix-artifact-patterns.sh \
       tools/address-comments-with-claude.sh \
       tools/ci/reset-autofix-worktree.sh \
       tools/ci/test-reset-autofix-worktree.sh \
       tools/ci/test-address-comments-staging.sh"
   : > scope.patch
   for c in 2fcf75e 382c5bf 14a2680 b91511a 7205b2a 1775257 \
            a705cad 14e9cba; do
       git show --format="commit %h %s%n" "$c" -- $PATHS >> scope.patch
   done
   for m in 3d5a612 7b1afe4 b6b67a8 931b5a9 7b4e860; do
       git log -1 --format="commit %h %s%n" "$m" >> scope.patch
       git diff "${m}^1" "$m" -- $PATHS >> scope.patch
   done
   ```

2. **Retire rather than repair.** The reasoning is *The verdict*
   above. The decisive point is not the defect count -- C1 and C2 are
   each a few lines -- but that H3 makes the repairs rewritable by the
   process they constrain, that the orchestration needing the most
   trust is the part that cannot be tested, and that the one fix the
   loop produced drew its value from the review rather than the
   generation. **This is the decision most likely to be argued with**,
   and the fair counter-argument is that the loop has only been
   functional for two days and a sample of one is no sample at all.
   It is taken anyway because the cost of finding out is another
   audit-sized project, against a backlog of two issues.

3. **The reporting half stays.** Fuzzer findings still become issues
   automatically. That part has worked for months and is what the
   remaining `security-audit` workflow depends on.

4. **The structured JSON issue body stays too.** It was built as a
   contract with the workflow being deleted, but it reads well for a
   human and rewriting `report-fuzz-crash.sh` is churn. Its comments
   are retargeted rather than its format changed.

5. **Wave 1 ran in full, including the Rust legs that could say
   nothing.** Kept from the original plan: skipping the legs that
   would report nothing would reproduce, in the audit, the exact
   shape of the defect the audit was examining -- a gate that passes
   silently over an empty input.

6. **The third `wave1.sh` bug is fixed here**, like the first two, for
   the reason in the original Decision 7: the phase cannot honestly
   run an audit through an instrument it knows to be lying. It is
   recorded as a finding against the runbook, not against this plan.

   The PR review found a fourth: the terminator this step introduced
   ends a block only on a dedented *YAML key*, so it ran through the
   comments between steps and counted them as script. Fixed on the
   same reasoning, and this time the program moved to
   `tools/audit/inline-script-check.awk` with fixtures in
   `tools/audit/test-inline-script-check.sh` wired into the
   `ci-tooling` job. Four silent mis-counts in twenty lines is the
   pattern a test exists to break, and the previous verification
   baseline was a file this phase deletes.

7. **Findings that touch surviving files are carried to the master
   plan's Future work, not fixed here.** A retirement should not grow
   into a `test-drift-fix.yml` repair project. Named explicitly:
   the `sed` blank-line squash, the log-fetch conflation, the
   shared-runner concurrency and workspace collision, and the
   unchecked `run:` blocks.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | low | sonnet | none | Fix the third bug in `tools/audit/wave1.sh`'s inline-script check: the awk exits block-tracking at the first blank line (`in_run && /^[[:space:]]*$/ { in_run=0; ... }`), so any `run:` body containing a blank line is truncated to its prefix and usually falls under the five-line threshold. Treat blank lines as part of the block (`count++` on non-blank only, do not exit) and terminate only on a dedent to a YAML key at or below the `run:` key's indent. Verify against the measurement taken during the audit: the fixed check must find **16** blocks over five lines in `.github/workflows/fuzz-autofix.yml` and **15** in `.github/workflows/test-drift-fix.yml`, where the current check reports 4 and 6. Run `tools/audit/wave1.sh` afterwards and confirm it still exits 0. Do not act on the newly visible blocks -- `fuzz-autofix.yml` is about to be deleted and `test-drift-fix.yml` is out of scope. Commit subject: `Count whole run blocks in the inline check.` |
| 2 | low | sonnet | none | Delete the machinery: `.github/workflows/fuzz-autofix.yml`, `tools/ci/stage-autofix-changes.sh`, `tools/ci/test-stage-autofix-changes.sh`, `tools/ci/autofix-artifact-patterns.sh`, `tools/autofix-prompt-base.txt`, `tools/fuzz-issue-schema.json`, and the `Test the autofix stager` step in `.github/workflows/functional-tests.yml`'s `ci-tooling` job (around `:138-139`). Then `grep -rn 'fuzz-autofix\|stage-autofix\|autofix-prompt-base\|fuzz-issue-schema\|autofix-artifact-patterns' --include='*.yml' --include='*.sh' .` and confirm the only survivors are comments, which step 3 handles. Do not touch `tools/ci/claude-result.sh`, `tools/ci/test-claude-result.sh`, `tools/ci/report-fuzz-crash.sh`, `tools/ci/prepare-testdata.sh` or anything under `coverage-fuzz.yml` / `differential-fuzz.yml`. Run `pre-commit run --all-files`. Commit subject: `Retire the automated fuzzer bug fix workflow.` |
| 3 | medium | sonnet | none | Retarget the comments and tests left behind in surviving files. `tools/ci/report-fuzz-crash.sh` (`:33`, `:36`, `:132`, `:224`, `:262`) describes its JSON field names as "a contract with fuzz-autofix.yml" and its issue cadence as feeding "the queue fuzz-autofix.yml drains (one issue per day)" -- both are now false; rewrite them to say what the format is for now (a structured, human-readable crash report), keeping the reasoning about anchoring and field choice, which is still valid. `tools/ci/test-report-fuzz-crash.sh:185`-`:190` asserts "the body satisfies fuzz-autofix.yml's validation predicate" -- either delete that test or re-justify it in its own terms (the four fields are still the ones a reader needs); say which you chose and why. `tools/ci/claude-result.sh` (`:8`, `:48`, `:81`) and `tools/ci/test-claude-result.sh:120` name fuzz-autofix.yml as a live caller; it has one caller now, `test-drift-fix.yml`. `.github/workflows/rust-nightly-bump.yml:15` mentions the GitHub limitation "shared with fuzz-autofix" -- reword. Do not change any behaviour; comments and test titles only, except where you delete the one test. Commit subject: `Retarget the comments the autofixer left behind.` |
| 4 | medium | sonnet | none | Documentation. Remove or rewrite, so that no document describes a workflow that no longer exists: `docs/testing.md`'s *Automated bug fixes* section (around `:1297`-`:1355`); `docs/development.md`'s script index entries at `:778` and `:807`-`:811` (four of the six entries there go; `claude-result.sh` and its test stay); `docs/security-audits.md:503`-`:504`; `docs/commentary/reading-order.md` step 14 (`:464`-`:478`) -- note this is an ordered reading tour, so removing a step means renumbering what follows and checking no other step cross-references step 14. Each place should say what happens now: fuzzer findings still become issues automatically via `tools/ci/report-fuzz-crash.sh`, and they are fixed by hand. Do not turn any of these into an essay about why -- the reasoning lives in the plan; leave a link. Add a `CHANGELOG.md` entry under Removed, because this withdraws documented behaviour. Check `AGENTS.md` and `ARCHITECTURE.md` and change them **only** if they name the workflow. Commit subject: `Document the autofixer's retirement.` |
| 5 | low | sonnet | none | Repository hygiene, read-only first: report before acting. List the issues carrying `autofix-failed`, `autofix-complex` or `autofix-attempted` (`gh issue list --label <l> --state all`), and report what deleting each label would touch. Do not delete a label or edit an issue until the management session confirms -- these are outward-facing. Then, on confirmation, remove the three labels and strip them from any issue that still carries one. Report the result. No commit; this step changes no files. |
| 6 | low | sonnet | none | Close out. Rewrite `PLAN-fuzz-autofix.md` so a first-time reader is not misled: the *Status* heading, the *Diagnosis* section (which describes the pre-#509 workflow in the present tense with line numbers that no longer resolve -- make it explicitly historical), *Remaining work*, and the *Detailed plan* steps 1-8, which describe a workflow that no longer exists. The plan is a record of something built, run, measured and withdrawn; it should read that way front to back. Move the surviving findings named in Decision 7 into Future work with their evidence. Fill this phase's row in the Execution table and set the status; set the `docs/plans/index.md` row to match, with a description saying what the plan concluded rather than what it built. The status word is exactly one term from the `plan-status-vocabulary` block and must be identical in both places -- ask the management session which term, do not choose it yourself. Do not touch `docs/plans/order.yml`. Commit subject: `Close out the fuzz autofix plan.` |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The removal is partial -- something still references a deleted file and CI breaks on the next run. | Step 2 ends with a tree-wide `grep` and `pre-commit run --all-files`; step 3 handles every comment that grep finds. Definition of done item 3 re-runs the grep independently. |
| A surviving finding is lost because the code it described was deleted around it. | Decision 7 names the four that survive, and step 6 moves them to Future work with their evidence. Done item 5 checks each by name. |
| The retirement is regretted and the machinery is wanted back. | Nothing is lost: the code is in git history, and this plan records what it did, what it cost and exactly why it was withdrawn. `14e9cba` is the precedent and it has not been regretted. |
| `report-fuzz-crash.sh`'s structured body loses its reason for existing and is later "simplified" back to prose by someone who reads only the deleted-contract comments. | Step 3's brief requires the comments to state the format's *current* justification rather than merely deleting the stale one. |
| Deleting labels or editing issues is outward-facing and irreversible in a way file edits are not. | Step 5 reports first and acts only on confirmation. |

## Result

Steps 1 to 4 landed as `93eefb8` (#541). Steps 5 and 6, both gated on
the operator, landed in the follow-up pull request that carries this
section.

**Step 5.** The inventory before acting: `autofix-failed` was on 28
issues -- one open (#492) and 27 closed -- and `autofix-complex` and
`autofix-attempted` were on nothing at all, confirming the master
plan's note that the complexity guardrails never fired. No pull
request carried any of the three. All three labels were deleted from
the repository, which strips them from every issue in one action;
#492 now carries only `security-audit`.

**Step 6.** The operator's call on the status word was `Complete`:
the plan ran to a definite, documented end, and the retirement is the
conclusion the evidence supported rather than a decision to stop
partway. What was abandoned is the machinery, not the plan. The
master plan is rewritten as a record of something built, run,
measured and withdrawn -- the *Detailed plan*, *CI workflow
structure* and *Success criteria* sections, which were a
reproduction-quality specification for deleted code, are condensed
into a *What was built* summary that names `2fcf75e` and points at
git history, and the *Diagnosis* section keeps its line numbers with
an explicit note that they are as the file stood before #509 and
resolve nowhere now.

The four surviving findings from Decision 7 are in the master plan's
Future work with the lines they occupy **today**, not the lines the
audit recorded: the blank-line squash moved to
`test-drift-fix.yml:521`, and the log-fetch conflation to
`:167-183`, posting at `:247`. The concurrency finding is restated
for the pair that actually remains -- `test-drift-fix.yml:69-71` and
`pr-re-review.yml:93-95`, both on `[self-hosted, claude-code]` -- now
that `fuzz-autofix.yml` is not one of the three. A fifth, the
`$GITHUB_OUTPUT` delimiter habit, is carried alongside them.

Issues #529 and #534 describe defects in deleted code and are still
open. They are recorded under *Lapsed with the retirement* rather
than closed, because closing them is a separate call.

## Definition of done

Falsifiable, in order:

1. `tools/audit/test-inline-script-check.sh` passes, and fails
   against both of the terminators it replaced, and
   `tools/audit/wave1.sh` exits 0. (Relaxed from "reports 16
   oversized blocks in `fuzz-autofix.yml` and 15 in
   `test-drift-fix.yml`": step 2 deletes the first of those files, so
   half the baseline could not be re-run from the tree, and the
   review's fourth bug changed the counts on the second. Fixture
   tests are the durable form of the same check.)
2. The six files named in *Scope* under **Removed** do not exist, and
   `functional-tests.yml` has no `Test the autofix stager` step.
3. This returns hits only in `docs/plans/`:

   ```bash
   grep -rn 'fuzz-autofix\|stage-autofix\|autofix-prompt-base' \
       --include='*.yml' --include='*.sh' --include='*.md' .
   grep -rn 'fuzz-issue-schema\|autofix-artifact-patterns' \
       --include='*.yml' --include='*.sh' --include='*.md' .
   ```
4. No document under `docs/` describes the workflow as existing, and
   `CHANGELOG.md` carries a Removed entry.
5. Each of the four surviving findings named in Decision 7 appears in
   `PLAN-fuzz-autofix.md`'s Future work with its file and line.
6. The `autofix-failed`, `autofix-complex` and `autofix-attempted`
   labels do not exist on the repository, and no open issue carries
   one.
7. `PLAN-fuzz-autofix.md` describes the workflow in the past tense
   throughout; no section presents it as current behaviour.
8. The status term for this plan is identical in
   `PLAN-fuzz-autofix.md`'s Execution table and the
   `docs/plans/index.md` row, and is one term from the
   `plan-status-vocabulary` block.
9. `tools/audit/wave1.sh` exits 0 and `pre-commit run --all-files`
   passes on the final tree.

## Back brief

Before executing any step, back brief the operator on your
understanding of it and how the work aligns.

Two gates within the phase:

* **Before step 5 acts.** Label deletion and issue edits are
  outward-facing and not revertible with a `git checkout`. The step
  reports first and waits.
* **Before step 6 sets a status.** Whether this plan closes as
  `Complete` (it ran to a definite, documented end) or `Abandoned`
  (its mission was reversed) is the operator's call, and it is the
  one word a future reader will use to decide whether to read
  further.
