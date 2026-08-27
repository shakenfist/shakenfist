# Automated PR Review with Claude Code

This document describes the Claude Code-powered automated PR review system used
in Shaken Fist projects. It reviews pull requests once CI passes and posts its
findings as a structured comment.

## Overview

The automated reviewer reviews a pull request once CI passes and posts
structured feedback as a comment. It runs Claude Code on a self-hosted GitHub
Actions runner with the `--dangerously-skip-permissions` flag for autonomous
operation.

A second component, the comment addresser, used to act on that feedback when a
maintainer asked it to. It was retired in August 2026 -- see [The comment
addresser](#the-comment-addresser-retired) below.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Pull Request Created                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Sanity Checks Workflow                           │
│  ┌─────────────┐    ┌─────────────────┐    ┌─────────────────────────┐ │
│  │ Build/Test  │───▶│ Integration     │───▶│ Automated Reviewer      │ │
│  │             │    │ Tests           │    │ (Claude Code)           │ │
│  └─────────────┘    └─────────────────┘    └───────────┬─────────────┘ │
│                                                         │               │
│                                                         ▼               │
│                                              Post PR comment with       │
│                                              markdown + embedded JSON   │
│                                              (in <details> section)     │
└─────────────────────────────────────────────────────────────────────────┘
```

## JSON-Based Review Format

The key design decision is using structured JSON output from the reviewer instead
of parsing markdown. This provides:

- **Deterministic validation** via JSON schema
- **No regex parsing** of natural language output
- **Iteration until valid** - reviewer can retry if JSON is malformed
- **Self-contained comments** - JSON is embedded in the PR comment itself

The JSON is embedded in a collapsed `<details>` section at the end of the
human-readable markdown. This keeps the comment readable while leaving the
findings machine-parseable.

### Review Schema

The review output follows a strict JSON schema, which ships with the reviewer
in `shakenfist/actions/review-pr-with-claude/review-schema.json`:

```json
{
  "summary": "Brief overall assessment of the PR",
  "items": [
    {
      "id": 1,
      "title": "Short issue title",
      "category": "security|bug|performance|documentation|style|testing|other",
      "severity": "critical|high|medium|low",
      "action": "fix|document|consider|none",
      "description": "Detailed explanation of the issue",
      "location": "path/to/file.rs:42",
      "suggestion": "Suggested fix or improvement",
      "rationale": "Why this matters"
    }
  ],
  "positive_feedback": ["List of things done well"],
  "test_coverage": {
    "assessment": "adequate|needs_improvement|insufficient",
    "suggestions": ["Specific test recommendations"]
  }
}
```

### Action Types

Each review item has an `action` field indicating what should be done:

| Action | Meaning |
|--------|---------|
| `fix` | Must be fixed before merging |
| `document` | Documentation should be added |
| `consider` | Optional improvement (reviewer suggestion) |
| `none` | Informational observation only |

`fix` and `document` used to be the two the comment addresser acted on
automatically. Nothing consumes the field automatically now, so it is a triage
aid for whoever reads the review.

### Category Types

Items are categorized for easier filtering and prioritization:

- `security` - Security vulnerabilities or concerns
- `bug` - Logic errors or incorrect behavior
- `performance` - Performance issues or optimizations
- `documentation` - Missing or incorrect documentation
- `style` - Code style or formatting issues
- `testing` - Test coverage or test quality
- `other` - Anything that doesn't fit above

### Severity Levels

- `critical` - Must be fixed, blocks merge
- `high` - Should be fixed before merge
- `medium` - Should be considered
- `low` - Nice to have

## Bot Commands

Comment on a PR with these commands (requires write access to the repository):

| Command | Description |
|---------|-------------|
| `@shakenfist-bot please retest` | Re-run the functional test suite |
| `@shakenfist-bot please re-review` | Request a fresh automated code review |
| `@shakenfist-bot please attempt to fix` | Have Claude attempt to fix failing tests |

These commands are processed by GitHub Actions workflows that use shared actions
from the [shakenfist/actions](https://github.com/shakenfist/actions) repository.

## How the Reviewer Works

The automated reviewer (`review-pr-with-claude.sh`, in the
`review-pr-with-claude` action in shakenfist/actions):

1. Fetches PR diff and file list using `gh` CLI
2. Reads AGENTS.md and ARCHITECTURE.md for project context
3. Prompts Claude Code to review the changes
4. Requests JSON output following the schema
5. Validates JSON against the schema using `render-review.py --validate`
6. Renders JSON to human-readable markdown with `--embed-json` flag
7. Posts the combined markdown (human-readable + embedded JSON) as a PR comment

The validation step ensures the output is parseable. If validation fails, the
script can retry (in practice, Claude Code follows the schema reliably).

The embedded JSON appears in a collapsed `<details>` section at the end of the
comment, keeping the review readable while preserving machine-parseable data.

### Example Prompt Structure

```
You are reviewing PR #123 for the Shaken Fist imago project.

First, read AGENTS.md and ARCHITECTURE.md to understand the project.

Review the following changes and output your review as JSON following
this exact schema:

[schema here]

Focus on:
- Security issues (especially input validation, sandboxing)
- Logic errors and bugs
- Performance concerns
- Missing documentation
- Test coverage

Files changed:
[file list]

Diff:
[PR diff]
```

## The comment addresser, retired

Nothing acts on a review automatically any more. `pr-address-comments.yml`
used to take each item with an `action` of `fix` or `document`, prompt Claude
Code with it, and push a commit per item; it is retired, and
[`docs/audits/ci-review-automation.md`](/components/development/audits/ci-review-automation/) has the
reasoning and the list of files a repository must not still carry.

What matters for the review format is that the `action` field survives its
consumer. It is still worth setting accurately, because it is how a human
triages the review -- but it is read by people now, not by a workflow.

## Workflow Files

- `.github/workflows/sanity-checks.yml` or `functional-tests.yml` - Main CI with
  automated review
- `.github/workflows/pr-retest.yml` - Manual re-run of functional tests
- `.github/workflows/pr-re-review.yml` - Manual re-review trigger
- `.github/workflows/pr-fix-tests.yml` - Test failure fixing trigger
- `.github/workflows/test-drift-fix.yml` - Test failure fixing implementation

## Shared Actions

The trigger logic for bot commands is extracted into a reusable action in the
[shakenfist/actions](https://github.com/shakenfist/actions) repository:

### pr-bot-trigger

This composite action handles the common pattern of:
- Checking if a comment matches a trigger phrase
- Verifying commenter has write/admin permissions
- Adding a reaction to the comment
- Posting unauthorized/starting messages
- Outputting PR details for downstream use

**Usage in workflows:**

```yaml
- uses: shakenfist/actions/pr-bot-trigger@main
  id: trigger
  with:
    trigger-phrase: 'please retest'
    reaction: 'rocket'
    starting-message: |
      Starting tests on branch `{pr_ref}`...
      [View workflow run]({run_url})

- name: Do something if authorized
  if: steps.trigger.outputs.authorized == 'true'
  run: |
    echo "PR branch: ${{ steps.trigger.outputs.pr-ref }}"
```

This reduces duplication across projects and ensures consistent security checks
and user experience.

## Scripts

| Script | Purpose |
|--------|---------|
| `review-pr-with-claude.sh` | Performs automated PR reviews (outputs JSON) |
| `render-review.py` | Validates JSON schema, renders to markdown |
| `review-schema.json` | JSON schema for review output |

All three live in the `review-pr-with-claude` action in
[shakenfist/actions](https://github.com/shakenfist/actions), not in the projects
they review. A project carrying its own copy of `render-review.py` is a leftover
of the retired comment addresser, and the consistency audit reports it.

## Self-Hosted Runner Requirements

The automation requires self-hosted runners with:

- `claude-code` label for Claude Code access
- Claude Code CLI installed and authenticated
- `gh` CLI installed and authenticated
- `jq` for JSON processing
- Python 3 with `jsonschema` package for validation

## Not Reviewing The Bot's Own Commits

The shared `pr-auto-review.yml` detects whether the last commit was made by the
bot, and skips the reviewer if it was. Callers get this by calling the reusable
workflow; there is nothing to add.

A bot push -- from the test fixer -- triggers CI like any other push, so without
the guard the reviewer would spend a claude-code run reviewing commits no human
wrote, on a branch whose human-authored changes it has already reviewed. That
waste is what the guard is for. It is not a loop any more: the comment addresser
was the only thing that turned a review back into a commit, and it is retired.

The check looks for commits with author email `bot@shakenfist.com`.

The legacy form of the same guard was a `check-bot-commit` job written out in
the project's own CI workflow, which the reviewer job then listed in `needs:`.
The reusable workflow replaced it with an API call it makes itself, so a project
still carrying that job should delete it. Nothing measures that: the
`ci-review-automation` audit checks the shape of the reviewer call and the
retired comment addresser, but not for a leftover `check-bot-commit`, so
migrating is a step somebody has to remember. The template README has the
procedure, including the case where another job depends on its output.

## Cost and Rate Limiting

Each review session uses Claude Code API calls. To manage costs:

- Reviews only run after CI passes (not on every push)
- Reviews skip bot-authored commits
- Concurrency groups cancel in-progress runs when new commits are pushed
- The `--max-turns` flag limits Claude iterations per item

## Local Development

You can run the tools locally for testing, from a checkout of
[shakenfist/actions](https://github.com/shakenfist/actions):

```bash
# Review a PR
review-pr-with-claude.sh --pr 123 --output-dir ./review-output

# Validate review JSON
render-review.py --validate review.json

# Render review JSON to markdown
render-review.py review.json
```

## Projects Using This System

Most of them, and the list moves: see the `ci-review-automation`
section of
[the compliance page](/components/development/audits/compliance/#ci-review-automation).
imago was the original implementation and occystrap the first
adaptation of it, which is why both turn up in the history above.

A project does **not** carry its own copy of the scripts. They live in
the `review-pr-with-claude` action in shakenfist/actions and are shared
from there; only the trigger workflows are per-project. A project with
its own `render-review.py` is a leftover of the retired comment
addresser, and the consistency audit reports it.

## Future Improvements

Potential enhancements to consider:

- **Confidence scores** - Add confidence field to review items
- **Learning from feedback** - Track which suggestions are accepted/rejected
- **Custom review focus** - Allow PR authors to request focus areas
- **Metrics dashboard** - Track review quality and fix rates over time

Automatic issue creation for deferred items was on this list and is
done: `create-review-issues.py` in the shared action files every `fix`
and `document` item. Having the reviewer also apply its own fixes was
tried, as the comment addresser, and retired -- see
[`ci-review-automation.md`](/components/development/ci-review-automation/).
