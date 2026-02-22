# AGENTS.md - AI Agent Instructions for Shaken Fist

This file provides instructions for AI agents (Claude Code, GitHub Copilot, etc.)
working on the Shaken Fist codebase.

## Project Context

Shaken Fist is a minimal cloud orchestration platform for VM and network
management. See [CLAUDE.md](CLAUDE.md) for detailed development guidance.

## CI/CD Architecture

### GitHub Actions Workflows

The repository uses several GitHub Actions workflows:

| Workflow | Purpose | Trigger |
|----------|---------|---------|
| `functional-tests.yml` | Main CI: lint, unit tests, functional tests | PR, merge_group |
| `documentation-tests.yml` | Build and test documentation | PR |
| `pin-indirect-dependencies.yml` | Keep indirect dependencies pinned | Daily schedule |
| `export-repo-config.yml` | Export GitHub repo settings to version control | Daily schedule |
| `pr-re-review.yml` | Re-review PR on bot command | `@shakenfist-bot please re-review` |
| `pr-address-comments.yml` | Address review comments on bot command | `@shakenfist-bot please address comments` |
| `pr-fix-tests.yml` | Fix test failures on bot command | `@shakenfist-bot please attempt to fix` |
| `test-drift-fix.yml` | Unit test fixer (called by pr-fix-tests) | workflow_call, workflow_dispatch |

### Merge Queue Pattern

The CI uses a two-stage merge queue pattern (see [this blog post](https://boinkor.net/2023/11/neat-github-actions-patterns-for-github-merge-queues/)):

1. **`Can enqueue`** - Runs on `pull_request` events, gates entry to merge queue
2. **`Can merge`** - Runs on `merge_group` events, gates the actual merge

**Important**: Only `Can see status` and `Can enqueue` are required status checks
in branch protection. `Can merge` is evaluated by the merge queue itself, not as
a required check.

### Exported Repository Configuration

Repository settings (rulesets, branch protection, merge queue config) are
exported to `.github/exported-config/` for version control and audit purposes:

- `repository-settings.json` - Repo-level settings
- `rulesets-summary.json` - List of all rulesets
- `ruleset-*.json` - Full details for each ruleset

If the `export-repo-config` workflow creates a PR, it means GitHub UI settings
have changed and should be reviewed.

## Automated CI Jobs

### Automated Delinter

When flake8 fails, the `automated_delinter` job runs Claude Code to fix lint
errors automatically. It skips if the last commit was from the bot to prevent
loops.

### Automated Exception Fixer

When functional tests detect exceptions in logs, the `automated_exception_fixer`
job downloads the test bundles and runs Claude Code to analyze and fix the
issues.

### Automated Reviewer

After successful tests, the `automated_reviewer` job uses the shared
`shakenfist/actions/review-pr-with-claude@main` action to review the PR.
The reviewer produces structured JSON reviews, creates GitHub issues for
actionable items, and embeds the JSON in the PR comment for automation.

### Developer Automation (Bot Commands)

Authorized users can trigger automation by commenting on PRs:

- **`@shakenfist-bot please re-review`** - Triggers a fresh automated
  review of the PR using the shared review action.
- **`@shakenfist-bot please address comments`** - Runs Claude Code to
  address actionable items from the automated review. Uses
  `tools/address-comments-with-claude.sh` with dual-checkout security
  (trusted tools from base branch, PR code separately).
- **`@shakenfist-bot please attempt to fix`** - Runs Claude Code to fix
  unit test failures (`tox -ecover`). Uses `test-drift-fix.yml` with
  structured commit summaries.

## Working with This Codebase

### Code Style

- Single quotes for strings, double quotes for docstrings
- 80 character line wrap
- Trim trailing whitespace
- See [CLAUDE.md](CLAUDE.md) for detailed style guide

### Testing

```bash
tox                              # Run all tests
tox -eflake8                     # Lint check
tox -emypy                       # Type checking
tox -ecover                      # Coverage report
stestr run {test_name}           # Run specific test
```

### Pre-commit Hooks

The repository uses pre-commit hooks to validate code before commits:

```bash
pip install pre-commit           # Install pre-commit
pre-commit install               # Set up git hooks
pre-commit run --all-files       # Run all hooks manually
```

Current hooks:
- `actionlint` - Validates GitHub Actions workflow files
- `ansible-lint` - Validates Ansible playbooks in the deployer
- `mypy` - Type checking via tox (incremental rollout)

### Key Directories

- `shakenfist/` - Core package
- `shakenfist/daemons/` - Background services
- `shakenfist/external_api/` - REST API
- `.github/workflows/` - CI workflows
- `.github/exported-config/` - Exported GitHub settings
