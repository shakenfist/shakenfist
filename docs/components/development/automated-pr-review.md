# Automated PR Review with Claude Code

This document describes the Claude Code-powered automated PR review system used in
Shaken Fist projects. The system provides automated code review on pull requests
and can automatically address review comments with one commit per issue.

## Overview

The automated review system consists of two main components:

1. **Automated Reviewer** - Reviews PRs after CI passes and posts structured feedback
2. **Comment Addresser** - Addresses review comments when triggered by a bot command

Both components use Claude Code running on self-hosted GitHub Actions runners with
the `--dangerously-skip-permissions` flag for autonomous operation.

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
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│            User comments: "@shakenfist-bot please address comments"     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     PR Address Comments Workflow                        │
│  ┌─────────────────────┐    ┌─────────────────────────────────────────┐│
│  │ Extract JSON from   │───▶│ Address Comments with Claude Code       ││
│  │ PR review comment   │    │ (one commit per actionable item)        ││
│  └─────────────────────┘    └───────────┬─────────────────────────────┘│
│                                         │                               │
│                           ┌─────────────┴─────────────┐                 │
│                           ▼                           ▼                 │
│                    Push commits              Post summary comment       │
└─────────────────────────────────────────────────────────────────────────┘
```

## JSON-Based Review Format

The key design decision is using structured JSON output from the reviewer instead
of parsing markdown. This provides:

- **Deterministic validation** via JSON schema
- **Clean data handoff** between reviewer and addresser
- **No regex parsing** of natural language output
- **Iteration until valid** - reviewer can retry if JSON is malformed
- **Self-contained comments** - JSON is embedded in the PR comment itself

The JSON is embedded in a collapsed `<details>` section at the end of the
human-readable markdown. This keeps the comment clean while making the data
easily extractable by the address-comments automation.

### Review Schema

The review output follows a strict JSON schema (`tools/review-schema.json`):

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

| Action | Meaning | Addressed by Bot |
|--------|---------|------------------|
| `fix` | Must be fixed before merging | Yes |
| `document` | Documentation should be added | Yes |
| `consider` | Optional improvement (reviewer suggestion) | No |
| `none` | Informational observation only | No |

The comment addresser only processes items with `action: fix` or `action: document`.

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
| `@shakenfist-bot please address comments` | Address automated review comments |

These commands are processed by GitHub Actions workflows that use shared actions
from the [shakenfist/actions](https://github.com/shakenfist/actions) repository.

## How the Reviewer Works

The automated reviewer (`tools/review-pr-with-claude.sh`):

1. Fetches PR diff and file list using `gh` CLI
2. Reads AGENTS.md and ARCHITECTURE.md for project context
3. Prompts Claude Code to review the changes
4. Requests JSON output following the schema
5. Validates JSON against the schema using `tools/render-review.py --validate`
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

## How the Comment Addresser Works

The comment addresser (`tools/address-comments-with-claude.sh`):

1. Fetches PR review comments and finds the most recent automated review
2. Extracts JSON from the `<details>` section in the review comment
3. Validates JSON against the schema
4. Extracts items where `action == "fix"` or `action == "document"`
4. For each actionable item:
   - Prompts Claude Code with the specific issue details
   - Claude either makes a fix or explains why it disagrees
   - If changes are made, creates a commit with attribution
5. Pushes all commits
6. Posts a summary comment with results

### One Commit Per Issue

Each valid fix gets its own commit. This allows:

- Easy review of individual fixes
- Cherry-picking or dropping specific commits
- Clear attribution of what each commit addresses

Commit messages include:
- Short summary of the change
- Reference to the review item ID and title
- Category and severity
- The original bot trigger command
- Attribution to Claude Code

### Disagreement Handling

Claude may disagree with a review suggestion if:
- The suggestion is incorrect
- The issue is not actually a problem
- The fix would break something else
- The change is out of scope

When Claude disagrees, it explains its rationale instead of making changes.
This is tracked in the summary table posted to the PR.

## Workflow Files

- `.github/workflows/sanity-checks.yml` or `functional-tests.yml` - Main CI with
  automated review
- `.github/workflows/pr-retest.yml` - Manual re-run of functional tests
- `.github/workflows/pr-re-review.yml` - Manual re-review trigger
- `.github/workflows/pr-fix-tests.yml` - Test failure fixing trigger
- `.github/workflows/test-drift-fix.yml` - Test failure fixing implementation
- `.github/workflows/pr-address-comments.yml` - Review comment addressing

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
| `tools/review-pr-with-claude.sh` | Performs automated PR reviews (outputs JSON) |
| `tools/address-comments-with-claude.sh` | Addresses review comments (reads JSON) |
| `tools/render-review.py` | Validates JSON schema, renders to markdown |
| `tools/review-schema.json` | JSON schema for review output |

## Self-Hosted Runner Requirements

The automation requires self-hosted runners with:

- `claude-code` label for Claude Code access
- Claude Code CLI installed and authenticated
- `gh` CLI installed and authenticated
- `jq` for JSON processing
- Python 3 with `jsonschema` package for validation

## Preventing Infinite Loops

The sanity-checks workflow includes a `check-bot-commit` job that detects if the
last commit was made by the bot. If so, the automated reviewer is skipped to
prevent infinite loops where:

1. Bot makes a commit
2. CI runs
3. Reviewer reviews the bot's commit
4. Someone triggers "address comments"
5. Bot makes another commit
6. Repeat...

The check looks for commits with author email `bot@shakenfist.com`.

## Cost and Rate Limiting

Each review and comment-addressing session uses Claude Code API calls. To manage
costs:

- Reviews only run after CI passes (not on every push)
- Reviews skip bot-authored commits
- Concurrency groups cancel in-progress runs when new commits are pushed
- The `--max-turns` flag limits Claude iterations per item

## Local Development

You can run the tools locally for testing:

```bash
# Review a PR
tools/review-pr-with-claude.sh --pr 123 --output-dir ./review-output

# Validate review JSON
tools/render-review.py --validate review.json

# Render review JSON to markdown
tools/render-review.py review.json

# Address comments (dry run)
tools/address-comments-with-claude.sh --pr 123 --review-json review.json --dry-run

# Address comments for real
tools/address-comments-with-claude.sh --pr 123 --review-json review.json
```

## Projects Using This System

The following Shaken Fist projects have implemented this automation:

- **[imago](https://github.com/shakenfist/imago)** - Disk image management tool
  (Rust). The original implementation, with sophisticated structured review
  output and issue creation.
- **[occystrap](https://github.com/shakenfist/occystrap)** - Container image
  tools (Python). Adapted from imago with Python-specific test commands.

Each project has its own `tools/` directory with the scripts, customized for
the project's build system and test framework. The shared action handles the
common trigger logic.

## Future Improvements

Potential enhancements to consider:

- **Confidence scores** - Add confidence field to review items
- **Learning from feedback** - Track which suggestions are accepted/rejected
- **Custom review focus** - Allow PR authors to request focus areas
- **Integration with issue tracker** - Auto-create issues for deferred items
- **Metrics dashboard** - Track review quality and fix rates over time
