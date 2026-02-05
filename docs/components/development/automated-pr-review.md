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
│                                        ┌────────────────┴────────────┐  │
│                                        │                             │  │
│                                        ▼                             ▼  │
│                              Upload review.json            Post markdown│
│                              as artifact                   comment      │
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
│  │ Download            │───▶│ Address Comments with Claude Code       ││
│  │ review.json artifact│    │ (one commit per actionable item)        ││
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
| `@shakenfist-bot please re-review` | Request a fresh automated code review |
| `@shakenfist-bot please attempt to fix` | Have Claude attempt to fix failing tests |
| `@shakenfist-bot please address comments` | Address automated review comments |

## How the Reviewer Works

The automated reviewer (`tools/review-pr-with-claude.sh`):

1. Fetches PR diff and file list using `gh` CLI
2. Reads AGENTS.md and ARCHITECTURE.md for project context
3. Prompts Claude Code to review the changes
4. Requests JSON output following the schema
5. Validates JSON against the schema using `tools/render-review.py --validate`
6. Renders JSON to human-readable markdown
7. Posts markdown as a PR comment
8. Uploads `review.json` as a workflow artifact

The validation step ensures the output is parseable. If validation fails, the
script can retry (in practice, Claude Code follows the schema reliably).

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

1. Downloads `review.json` artifact from the sanity-checks workflow
2. Validates JSON against the schema
3. Extracts items where `action == "fix"` or `action == "document"`
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

- `.github/workflows/sanity-checks.yml` - Main CI with automated review
- `.github/workflows/pr-re-review.yml` - Manual re-review trigger
- `.github/workflows/pr-fix-tests.yml` - Test failure fixing
- `.github/workflows/pr-address-comments.yml` - Review comment addressing

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

## Future Improvements

Potential enhancements to consider:

- **Confidence scores** - Add confidence field to review items
- **Learning from feedback** - Track which suggestions are accepted/rejected
- **Custom review focus** - Allow PR authors to request focus areas
- **Integration with issue tracker** - Auto-create issues for deferred items
- **Metrics dashboard** - Track review quality and fix rates over time
