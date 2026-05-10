# Automated fuzzer bug fix workflow

## Status: In Progress (workflow scaffolding merged, not yet exercised end-to-end)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (CI workflows, Claude
Code automation, issue labelling, PR creation), and ground your
answers in what the code actually does today. Do not speculate
about the codebase when you could read it instead.

## Situation

The coverage-guided fuzzing (Phase 6) and differential fuzzing
(Phase 3) workflows automatically file GitHub Issues with the
`security-audit` label when they find crashes or divergences.
Each issue includes a minimised reproducer input, the fuzz target
name, a stack trace, and a reproduction command.

Currently these issues sit until a human picks them up. Many
fuzzer findings (panics from missing bounds checks, unwrap on
None, index out of bounds) are straightforward fixes that Claude
Code can handle autonomously. This plan adds a scheduled CI job
that picks up unfixed fuzzer issues and proposes fixes as PRs.

## Mission and problem statement

Create a `workflow_dispatch` + scheduled CI workflow that:

1. Finds open GitHub Issues with the `security-audit` label.
2. For each eligible issue, invokes Claude Code to diagnose
   the crash, implement a fix, verify the fix, and create a PR.
3. Limits scope and complexity so that Claude attempts only
   tractable fixes and escalates the rest to humans.

## Design decisions

### Regular PRs, not drafts

Fixes are submitted as regular PRs assigned to the maintainer
for review. The verification step (re-running the reproducer)
provides confidence that the fix actually resolves the crash.
Draft PRs add friction without value here since the reproducer
serves as the acceptance test.

### Two attempts per issue

If the first fix attempt fails verification (the reproducer
still crashes), the workflow retries once with additional context
from the first failure. After two failed attempts the issue is
labelled `autofix-failed` and left for human attention. This
avoids wasting CI time on issues that need architectural changes.

### Complexity guardrails

The following rules limit Claude to tractable fixes:

* **Turn limit:** 30 turns maximum. This is enough for reading
  the crash site, understanding the parser logic, implementing
  a bounds check or early return, and verifying the fix. It is
  not enough for large refactors, which is intentional.
* **File count:** If the fix touches more than 3 files (excluding
  test images and documentation), the attempt is abandoned and
  the issue is labelled `autofix-complex`.
* **No cross-crate changes:** If the fix requires modifying both
  a parser crate and the VMM or core binary, it is beyond scope.
  Parser-only fixes and shared-crate fixes are in scope.
* **No new dependencies:** The fix must not add new crate
  dependencies or feature flags.

### One issue at a time

The workflow processes issues sequentially, not in parallel. This
avoids conflicting branches and keeps resource usage predictable.
A concurrency group ensures only one instance runs at a time.

## Detailed plan

### Step 1: Issue discovery

The workflow queries open issues:

```bash
gh issue list \
    --label "security-audit" \
    --state open \
    --json number,title,body,labels \
    --limit 10
```

An issue is **eligible** if:
* It has the `security-audit` label.
* It does NOT have the `autofix-failed` or `autofix-complex`
  label (already attempted and abandoned).
* It does NOT have an open PR referencing it (check for
  `Fixes #N` in open PR bodies to avoid duplicate work).
* Its body contains a reproduction command (presence of
  `cargo fuzz run` or `differential-fuzz.py` as a heuristic).

The workflow processes the oldest eligible issue first.

### Step 2: Branch setup

For each eligible issue, create a branch:

```bash
git checkout -b autofix/issue-${ISSUE_NUMBER} origin/develop
```

### Step 3: Build the prompt

Construct a prompt for Claude Code containing:

1. **The issue body** (crash signature, stack trace, reproducer
   command, fuzz target name).
2. **Task instructions:**
   - Read the fuzz target source to understand what parser
     function is being exercised.
   - Read the stack trace to identify the crash site.
   - Read the parser code at the crash site and understand
     why the input causes a panic or crash.
   - Implement a fix that addresses the root cause (not just
     suppressing the panic). Prefer returning `None`/`false`
     for invalid input over adding arbitrary limits.
   - Run `pre-commit run --all-files` (via Make) to validate
     formatting.
   - Stage changed files and provide a commit summary between
     `COMMIT_SUMMARY_START` and `COMMIT_SUMMARY_END` markers.
3. **Complexity rules** (from the design decisions above).
4. **What NOT to do:**
   - Do not modify `instar-testdata`.
   - Do not run cargo or docker directly (use Make targets).
   - Do not add the crash reproducer as a test image in this
     PR (that is a separate step after the fix merges).

### Step 4: First fix attempt

Invoke Claude Code:

```bash
claude -p "$(cat ${GITHUB_WORKSPACE}/autofix-prompt.txt)" \
    --dangerously-skip-permissions \
    --max-turns 30 \
    --output-format text \
    2>&1 | tee ${GITHUB_WORKSPACE}/claude-output.txt || true
```

### Step 5: Verification

After Claude finishes:

1. **Check file count:** If more than 3 non-doc/non-test files
   were changed, label the issue `autofix-complex` and stop.
2. **Build:** Run `make instar` to verify the fix compiles.
3. **Run reproducer:** Execute the fuzz target with the crash
   input. If the target no longer crashes (exit code 0), the
   fix is verified.
4. **Run existing tests:** Run `make test-container-core` to
   ensure the fix doesn't break existing functionality.

If verification passes, proceed to Step 7 (PR creation).
If verification fails, proceed to Step 6 (retry).

### Step 6: Retry (second attempt)

Reset the branch to `origin/develop` and construct a new prompt
that includes:

1. Everything from Step 3.
2. The diff from the first attempt.
3. The verification failure output (build error, reproducer
   still crashes, or test failure).
4. An explicit instruction: "The previous fix attempt failed.
   The diff and failure output above show what was tried and
   why it didn't work. Try a different approach."

Invoke Claude Code again with the same turn limit. Run
verification again (Step 5).

If the second attempt also fails, label the issue
`autofix-failed` and add a comment summarising the two
attempts and their failure modes. Stop processing this issue.

### Step 7: PR creation

If verification passes:

1. **Commit** the changes with a message following the project
   conventions (extracted from Claude's COMMIT_SUMMARY markers
   or a fallback message).
2. **Push** the branch.
3. **Create a PR** targeting `develop`:

```bash
gh pr create \
    --assignee mikalstill \
    --reviewer mikalstill \
    --title "Fix fuzzer crash: ${ISSUE_TITLE}" \
    --body "$(cat <<EOF
## Summary

Automated fix for #${ISSUE_NUMBER}.

${COMMIT_BODY}

## Verification

- Reproducer no longer crashes after fix
- Existing tests pass (make test-container-core)

## Reproduction

\`\`\`bash
${REPRODUCER_COMMAND}
\`\`\`

Fixes #${ISSUE_NUMBER}

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

The `Fixes #N` line auto-closes the issue when the PR merges.

### Step 8: Post-fix follow-up (manual)

After the PR merges, a human should:

1. Add the minimised crash input to `instar-testdata/custom/
   fuzz-corpus/` as a regression test.
2. Register it in `tests/manifest.json` if appropriate.
3. Update `docs/security-audits.md` with the finding and fix.

These steps are intentionally manual because they touch the
private `instar-testdata` repo and require judgement about
severity classification.

## CI workflow structure

### Triggers

```yaml
on:
  schedule:
    - cron: '0 6 * * *'    # Daily at 06:00 UTC
  workflow_dispatch:
    inputs:
      issue_number:
        description: 'Specific issue number (empty = oldest eligible)'
        required: false
        default: ''
        type: string
      max_turns:
        description: 'Max Claude turns per attempt'
        required: false
        default: '30'
        type: string
```

### Runner

```yaml
runs-on: [self-hosted, claude-code]
```

Matches the existing Claude Code automation runners.

### Concurrency

```yaml
concurrency:
  group: fuzz-autofix
  cancel-in-progress: false
```

Do not cancel in-progress runs — let the current fix attempt
finish rather than interrupting mid-fix.

### Permissions

```yaml
permissions:
  contents: write
  issues: write
  pull-requests: write
```

### Labels

The workflow creates these labels if they don't exist:

* `autofix-failed` — two fix attempts failed, needs human.
* `autofix-complex` — fix exceeded complexity guardrails.
* `autofix-attempted` — added during processing to prevent
  concurrent duplicate work. Removed on success or replaced
  by one of the above on failure.

## Success criteria

The workflow is complete when:

* It can discover eligible `security-audit` issues.
* It successfully fixes at least one fuzzer-found crash
  end-to-end (issue to merged PR).
* Failed attempts are properly labelled and commented.
* Complexity guardrails prevent runaway fixes.
* The workflow integrates cleanly with existing CI (same
  runner labels, artifact patterns, concurrency groups).

## Future work

* **Regression test automation:** After a fix PR merges,
  automatically add the crash reproducer to instar-testdata
  and update manifest.json. Requires write access to the
  GitLab testdata repo (same token as corpus push).
* **Severity classification:** Parse the crash type (panic
  vs. OOM vs. infinite loop) and set priority labels on the
  PR accordingly.
* **Batch processing:** Process multiple issues per run
  (sequentially) to reduce CI overhead from the build step.
* **Cross-reference with differential fuzzing:** If a
  coverage fuzzer crash also manifests as a differential
  fuzzing divergence, link the issues.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
