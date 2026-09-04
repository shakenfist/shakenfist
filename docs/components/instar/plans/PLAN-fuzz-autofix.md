# Automated fuzzer bug fix workflow

> **Retired.** This plan built an automated fuzzer bug fix workflow,
> scheduled it, ran it, measured what it produced, and withdrew it.
> Nothing described below is current behaviour: the workflow, its
> staging helpers and its prompt were deleted in phase 2. Fuzzer
> crashes and differential divergences still become GitHub issues
> automatically; they are fixed by hand. The measurement and the
> reasoning are in
> [phase 2](/components/instar/plans/PLAN-fuzz-autofix-phase-02-push-audit/).

## Status: Complete

The plan ran to a definite, documented end. It is `Complete` rather
than `Abandoned` because the work was done -- built, scheduled, run,
audited and measured -- and the retirement is the conclusion that
evidence supported, not a decision to stop partway. What was
abandoned is the machinery, not the plan.

## What it was for

The coverage-guided fuzzing and differential fuzzing workflows file
GitHub issues with the `security-audit` label when they find crashes
or divergences. Each issue carries a minimised reproducer input, the
fuzz target name, a stack trace and a reproduction command. Those
issues sat until a human picked them up. The premise of this plan was
that many fuzzer findings -- panics from missing bounds checks,
`unwrap` on `None`, index out of bounds -- are straightforward enough
that Claude Code could fix them autonomously, so a scheduled job
should pick up unfixed fuzzer issues and propose fixes as pull
requests.

That premise did not survive measurement. See *The verdict* in
[phase 2](/components/instar/plans/PLAN-fuzz-autofix-phase-02-push-audit/).

## What was built

`.github/workflows/fuzz-autofix.yml`, created in `2fcf75e`, ran daily
on `[self-hosted, claude-code]` and also on `workflow_dispatch`. Each
run picked the oldest eligible open `security-audit` issue -- one
that carried no `autofix-failed` or `autofix-complex` label, had no
open pull request saying `Fixes #N`, and had a reproduction command
in its body -- branched `autofix/issue-N` from `develop`, built a
prompt from the issue and handed it to `claude -p`. If the attempt
verified, the workflow committed, pushed and opened a pull request
assigned to the maintainer with `Fixes #N` in the body.

The design decisions, all as built:

* **Regular pull requests, not drafts.** Review was expected to be
  the acceptance gate, and drafts add friction without value.
* **Two attempts per issue.** The second attempt was given the first
  attempt's diff and its verification failure. After two failures the
  issue was labelled `autofix-failed` and left for a human.
* **Complexity guardrails.** A turn limit (30, later 40); at most
  three non-doc, non-test files; no cross-crate changes; no new
  dependencies or feature flags. Exceeding them labelled the issue
  `autofix-complex`.
* **One issue at a time**, under a `fuzz-autofix` concurrency group
  with `cancel-in-progress: false`, to avoid conflicting branches and
  keep runner usage predictable.
* **Three labels** -- `autofix-failed`, `autofix-complex` and
  `autofix-attempted` -- created by the workflow if missing.

Verification was always weaker than the pull request body implied.
The workflow read the issue's `.reproducer` into the prompt and into
the pull request body but never executed it, so "verified" meant
`make instar` built and `make test-container-core` passed. That gap
is issue #529, and it is more than a missing line: the crash input
was never in the issue body, only in the `coverage-fuzz-logs`
artifact of the run named by `.ci_run`, and those expire after 90
days.

The code is in git history. This section is a summary, not a
specification; nothing should be rebuilt from it without reading the
audit first.

### Diagnosis: the gate read the index, the safety net staged too late

Recorded on 2026-08-19, when the workflow had been running daily for
months and had **never produced a pull request**. No `autofix/*`
branch had ever reached origin, no pull request had ever been opened,
and 28 `security-audit` issues carried the `autofix-failed` label.
`autofix-complex` had never been applied, so the complexity
guardrails were not what stopped it.

The workflow decided whether Claude had produced a fix by inspecting
the *staged* tree. Line numbers below are as `fuzz-autofix.yml` stood
immediately before PR #509 and no longer resolve in any live file:

* line 285 (`Check complexity (attempt 1)`) -- `git diff --cached --name-only`
* line 348 (`Verify fix (attempt 1)`) -- the same command again, and
  an empty result set `fix_succeeded=false`
* lines 505 and 561 -- the same two checks for attempt 2

The compensating `git add -u`, commented in the workflow as a "safety
net" for exactly this case, did not run until line 637, inside the
`Create PR` step -- which only executed once verification had already
passed. The safety net sat downstream of the gate that needed it.

Claude Code edits the working tree and does not necessarily stage. So
a correct fix was written, the gate saw an empty index, both attempts
reported "No changes staged by Claude", and the issue was labelled
`autofix-failed` with a report whose own text gave it away: a real
source file under "Changes not staged for commit" above an empty
`=== Staged Changes ===` block. Issues #492, #485 and #426 all show
that shape.

Staging was the dominant failure mode but not the only one: #438 did
stage a substantive fix and still failed, later in verification. Both
had to be true before a pull request could appear, so fixing the gate
was necessary but not obviously sufficient.

### Resolution (2026-08-20, PR #509)

`tools/ci/stage-autofix-changes.sh` ran immediately after each Claude
attempt, upstream of every gate that read the index. It staged
tracked modifications and deletions, and **nothing else**.

The interesting half was what it refused. A file the attempt
*created* stopped the run by name, with the issue left
`autofix-failed` for a human, in three cases: an untracked file; a
file `.gitignore` hid that was not in the pre-attempt baseline (git
omits ignored paths from the untracked listing entirely, so these
otherwise vanished without a trace, and `**/*.bin` is exactly what a
crash fixture gets called); and any change under `.github/workflows/`,
which could not be pushed with the token CI held -- that one was
actively unstaged, because an exclude pathspec does not remove what
Claude may already have staged.

Staging created files instead would have meant classifying them, and
a wrong guess ships a branch that does not compile behind a pull
request saying "Build succeeded", because the verify build ran
against the working tree where the file was present. A wrong refusal
cost a look at an issue that was already going to get one. The review
history on #509 is worth reading before anyone reaches for this shape
again: the first four rounds built the classification -- a
source-root allowlist, an artifact denylist, gitignored-file
reporting, collapsed-directory handling -- and each refinement
introduced the next defect.

Telling a created file from build output needed a before picture.
`pre-run-ignored.txt` was snapshotted after `Build instar` and before
attempt 1; `Prepare retry` deleted the paths named in
`stager-refused-1.txt` (its `git clean -fd` had no `-x`, so a refused
file would otherwise have survived and refused attempt 2 whatever
attempt 2 did) and snapshotted again into `pre-retry-ignored.txt`. A
single baseline would have judged attempt 2 against a tree from
before the verify build and the full test run, and refused it for
their output.

The behaviour was covered by `tools/ci/test-stage-autofix-changes.sh`
in the `ci-tooling` job, for the same reason `pick-fuzz-artifact.sh`
is a script with tests: logic that only runs inside a live daily run
cannot be tested there, and the bugs in this area all hid in inline
YAML. Phase 2's wave 2b found that this pinned the *single*
invocation well and the cross-attempt orchestration not at all.

### Derived trailers, and the end-to-end proof (phase 1)

Phase 1 closed two things out.

The hardcoded `Co-Authored-By: Claude Opus 4.6 (1M context)` trailer
in the Create PR step no longer named the model that ran. This plan
had previously recorded that as a call for a human, on the grounds
that the workflow could not introspect which model the `claude` CLI
resolved to. That was false, and phase 1's survey measured it:
`claude -p --output-format json` reports `.modelUsage` keyed by the
resolved model with its `contextWindow`, so the trailer could be
derived per run and could not go stale again. The same defect existed
in `.github/workflows/test-drift-fix.yml` and
`tools/address-comments-with-claude.sh`, which carried a *different*
stale name; phase 1 fixed all three behind one tested helper,
`tools/ci/claude-result.sh`. Confirmed live: run 33297854229's pushed
commit for PR #533 carries `Co-Authored-By: Claude claude-opus-5 (1M
context) <noreply@anthropic.com>`, the derived form.

Phase 1's step 7 then dispatched the workflow against #485 twice. The
first run, 33219527764 on 2026-08-28, exposed a workflow defect
instead of proving the loop: the workflow never checked out
`instar-testdata`, so `make test-container-core` could never pass and
no run could ever have reached `Commit, push, and create PR`. Fixed
in PR #530. The second, run 33297854229 on 2026-08-30, proved the
loop end to end -- it reached `Commit, push, and create PR` and
opened PR #533 against #485, which merged as `aaee69b`. That is the
only pull request the loop ever produced.

`tools/address-comments-with-claude.sh` had the same staging defect
in the review-comment loop rather than the fuzz loop (issue #510): it
instructed Claude to stage, then reported an unstaged fix as a
skipped review item. Fixed in PR #511 (`7b1afe4`), reusing the stager
in `--tracked-only` mode. It was not the one-line change it looked
like: the loop's Claude-failed and disagreement branches do not reset
the tree, so staging on Claude's behalf would attribute one item's
leftovers to the next item's commit. The addresser has since been
retired outright, in `14e9cba`.

## Execution

The workflow, and the staging fix that made it able to open a pull
request, predate this table and were tracked inline in the sections
above. Phase 1 was the close-out; phase 2 was the push audit that
ends every master plan, and it is where the retirement was decided.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Derived trailers and an end-to-end proof | [PLAN-fuzz-autofix-phase-01-closeout.md](/components/instar/plans/PLAN-fuzz-autofix-phase-01-closeout/) | Complete | `b6b67a8` (#520), `931b5a9` (#530), `7b4e860` (#535) |
| 2. Push audit, and the retirement it recommended | [PLAN-fuzz-autofix-phase-02-push-audit.md](/components/instar/plans/PLAN-fuzz-autofix-phase-02-push-audit/) | Complete | `93eefb8` (#541) |

The `Merged` column is the one `PLAN-TEMPLATE.md` requires of a plan
carrying a push audit phase: phase 2 ran the audit over the union of
the earlier phases' merge ranges, because `git diff develop...HEAD`
is empty once they have landed. Phase 1 landed across three pull
requests rather than one -- #520 built the helper and switched the
automations to it, #530 gave the workflow its test data and corrected
this plan's account of verification, and #535 recorded the result of
the end-to-end run. Phase 2 landed as #541, which carried its steps 1
to 4; its steps 5 and 6, the label hygiene and this close-out, landed
in a follow-up pull request.

The column only covers the two phases the table tracks. Most of this
plan's work predates the table and was recorded in the sections above
rather than as merge commits, so phase 2 had to reconstruct that part
of the scope from the merge history; the reconstruction is a table in
[PLAN-fuzz-autofix-phase-02-push-audit.md](/components/instar/plans/PLAN-fuzz-autofix-phase-02-push-audit/)
under *What the survey found*, and it is the authority on what the
audit read.

### 2. Push audit, and the retirement it recommended

This phase ran `PUSH-AUDIT.md` over the plan's accumulated work --
every phase together, because the workflow, the stager and its tests
were built across separate branches and what they did to each other
is only visible in the sum. There was no single range to audit, so the
scope was reconstructed commit by commit; that reconstruction, and
everything the audit found, is in
[PLAN-fuzz-autofix-phase-02-push-audit.md](/components/instar/plans/PLAN-fuzz-autofix-phase-02-push-audit/).

**The audit's verdict was that the workflow should be removed rather
than repaired, and the phase removed it.** The reasoning is set out
in full in the phase plan under *The verdict*; in short, the loop ran
60 times and produced one pull request, the safety boundary the
stager existed to provide did not actually hold, the boundary could
be rewritten by the process it constrained, the orchestration that
needed the most trust was the part no test could reach, and the one
fix the loop did produce drew its value from the human review it
received rather than from the fix it generated. The fair
counter-argument -- that the workflow had only been functional since
#530 merged on 2026-08-30, so the sample is one -- is recorded there
too.

The fuzzers' *reporting* half stays. Crashes and divergences still
become GitHub issues automatically through
`tools/ci/report-fuzz-crash.sh`; they are now fixed by hand, which is
how 29 of the 30 closed `security-audit` issues were fixed anyway.

Planning the phase also found three bugs in the runbook's own wave 1
inline-script check, all in `tools/audit/wave1.sh`: it printed `NR`
where it meant `FNR`, so every line number it reported for any
workflow but the alphabetically first was past that file's end; its
`head -20` hid the hits for this plan's second workflow entirely; and
it stopped counting a `run:` block at the block's first blank line,
undercounting `fuzz-autofix.yml` at 4 blocks where there were 16.
Review found a fourth: the replacement terminator ended a block only
on a dedented YAML key, so it ran through the comments between steps
and counted them as script. All four are fixed, and the program now
lives in `tools/audit/inline-script-check.awk` with fixture tests in
`tools/audit/test-inline-script-check.sh` wired into the `ci-tooling`
job -- because an audit cannot be run honestly through an instrument
known to be lying, and four silent mis-counts in twenty lines is the
pattern a test exists to break.

## What was removed, and what survives

Removed in phase 2 (`881e5e9`): `.github/workflows/fuzz-autofix.yml`,
`tools/ci/stage-autofix-changes.sh`,
`tools/ci/test-stage-autofix-changes.sh`,
`tools/ci/autofix-artifact-patterns.sh`,
`tools/autofix-prompt-base.txt`, `tools/fuzz-issue-schema.json`, and
the `Test the autofix stager` step in `functional-tests.yml`. The
`autofix-failed`, `autofix-complex` and `autofix-attempted` labels
were deleted from the repository, which stripped `autofix-failed`
from the 28 issues carrying it.

Kept: `tools/ci/report-fuzz-crash.sh` and its test, which are where
this plan's value actually landed -- fuzzer findings still become
issues automatically, and their structured JSON body reads well for a
human even though it was designed as a contract with the deleted
workflow. `tools/ci/claude-result.sh` and its test survive with one
caller, `.github/workflows/test-drift-fix.yml`.
`tools/ci/prepare-testdata.sh` is used by five workflows. Everything
in `coverage-fuzz.yml` and `differential-fuzz.yml` is untouched.

## Future work

Findings the audit raised against files that survive the retirement.
Phase 2's Decision 7 deliberately left these unfixed: a retirement
should not grow into a `test-drift-fix.yml` repair project. Each is
recorded with the evidence, at the line it occupies today.

* **The surviving commit-summary squash is the wrong one.**
  `.github/workflows/test-drift-fix.yml:521` uses
  `sed '/^$/N;/^\n$/d'`; the deleted `fuzz-autofix.yml` used
  `cat -s`. Measured on `para one\n\n\npara two`: `cat -s` preserves
  the paragraph break, the `sed` idiom deletes both blank lines and
  merges the paragraphs. The copy that survived is the one that
  mangles commit messages.
* **A red pull request can be told its tests pass.**
  `.github/workflows/test-drift-fix.yml:167-183` fetches each failed
  run's logs with `gh api ... 2>/dev/null | grep ... || true`, so
  every fetch failing is indistinguishable from there being no
  failures: `has_prior_failures` is set false either way, and `:247`
  then posts "✅ All tests pass! No fixes needed." on the pull
  request.
* **The concurrency groups do not cover the shared runner.**
  `test-drift-fix.yml:69-71` and `pr-re-review.yml:93-95` each scope
  their group to their own workflow, but both jobs run on
  `[self-hosted, claude-code]` (`:67` and `:84`) and share that
  runner's workspace, including the `claude-logs/` directory
  `test-drift-fix.yml:495` writes into. Nothing stops the two running
  at once.
* **No unquoted-variable check runs on any workflow's inline
  scripts.** `.github/actionlint.yaml:22-27` disables SC1090, SC2046,
  SC2086 and SC2143 for `.github/workflows/*.yml`, and the
  `shellcheck` pre-commit hook at `.pre-commit-config.yaml:46-48`
  matches only `^(scripts|tools)/`. Verified by building the pinned
  actionlint and running it directly. This applies to every workflow
  in the repository, not just the ones this plan touched.
* **The `$GITHUB_OUTPUT` delimiter habit.** The deleted workflow
  allowed `$GITHUB_OUTPUT` injection through an issue title. The
  pattern -- writing untrusted text into `$GITHUB_OUTPUT` without a
  random heredoc delimiter -- is worth a sweep across the surviving
  workflows.

One lesson worth keeping after the code is gone, from `ea036dd`'s
commit message: a previous version of the stager's test suite pinned
the *wrong* behaviour -- "the test suite pinned the dropping as
intended, so the gap was locked in". A green suite meant only
"matches current assertions". That is a general caution about this
repository's tests, not about the autofixer.

### Lapsed with the retirement

Two open issues describe defects in code that no longer exists, and
five earlier Future work items describe enhancements to it. They are
recorded here rather than silently dropped; closing #529 and #534 is
a separate call.

* **#529** -- the workflow never ran the crash reproducer it claimed
  to verify against.
* **#534** -- every attempt exhausted its turn budget (31/30, 31/30,
  41/40) before reaching the `COMMIT_SUMMARY_START`/`END` block the
  prompt asked for last, so every pull request it opened, including
  #533, used the fallback title and commit body.
* Regression test automation, severity classification, batch
  processing, and cross-referencing coverage crashes against
  differential divergences. All were enhancements to the deleted
  loop.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (CI workflows, Claude
Code automation, issue labelling, PR creation), and ground your
answers in what the code actually does today. Do not speculate
about the codebase when you could read it instead.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
