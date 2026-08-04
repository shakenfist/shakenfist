# CI Review Automation

Shaken Fist projects use Claude Code-powered automation for PR
reviews, test fixing, and comment addressing. This page describes
the workflow templates and how to add them to a new project.

## How It Works

The automation consists of several GitHub Actions workflows that
respond to PR events and bot commands:

```
PR opened/updated
      |
      v
CI tests run (functional-tests.yml)
      |
      v
Tests pass ──> Automated reviewer (Claude Code)
                    |
                    v
              Posts structured review comment
                    |
                    v
Maintainer comments: "@shakenfist-bot please address comments"
                    |
                    v
              Claude addresses each actionable item
              (one commit per fix)
```

### Bot Commands

Repository collaborators with write access can trigger these
commands by commenting on a PR:

| Command | Workflow | Description |
|---------|----------|-------------|
| `@shakenfist-bot please retest` | `pr-retest.yml` | Re-run functional tests |
| `@shakenfist-bot please re-review` | `pr-re-review.yml` | Fresh automated review |
| `@shakenfist-bot please address comments` | `pr-address-comments.yml` | Address review comments |
| `@shakenfist-bot please attempt to fix` | `pr-fix-tests.yml` | Fix failing tests (separate template) |

## Security Model

These workflows use `issue_comment` triggers, which run with
elevated permissions. Security is enforced through multiple layers:

1. **Authorization** -- only repository collaborators with write
   access can trigger commands (enforced by
   `shakenfist/actions/pr-bot-trigger`)
2. **Trusted tools** -- scripts are checked out from the base branch,
   not the PR, preventing execution of malicious PR code
3. **No credential persistence** -- `persist-credentials: false`
   prevents tokens from being stored in the checkout
4. **Git hooks disabled** -- `core.hooksPath=/dev/null` prevents
   malicious git hooks from the PR
5. **No pre-commit** -- pre-commit hooks execute repository code and
   are skipped in privileged workflows
6. **Just-in-time auth** -- `gh auth setup-git` is used only when
   pushing, not during the entire workflow

See the [GitHub Security Lab article](https://securitylab.github.com/research/github-actions-preventing-pwn-requests/)
for background on `issue_comment` trigger security.

## Workflow Templates

Templates are in
[`templates/ci-review-automation/`](https://github.com/shakenfist/development/tree/main/templates/ci-review-automation):

| Template | Customisation | Description |
|----------|---------------|-------------|
| `pr-re-review.yml` | None | Manual re-review trigger |
| `pr-retest.yml` | None | Manual test re-run |
| `pr-address-comments.yml` | None | Address review comments |

All three files are project-agnostic and can be copied directly.

For projects with large test suites that would benefit from
automatic test fixing, see the separate
[`templates/test-drift-fix/`](https://github.com/shakenfist/development/tree/main/templates/test-drift-fix)
templates which provide `pr-fix-tests.yml` and
`test-drift-fix.yml`.

## Adding CI Review Automation to a Project

### Step 1: Copy the Workflow Files

```bash
# From the target project root:
cp /path/to/development/templates/ci-review-automation/pr-re-review.yml \
    .github/workflows/
cp /path/to/development/templates/ci-review-automation/pr-retest.yml \
    .github/workflows/
cp /path/to/development/templates/ci-review-automation/pr-address-comments.yml \
    .github/workflows/
```

For projects with large test suites, also copy from
`templates/test-drift-fix/`:

```bash
cp /path/to/development/templates/test-drift-fix/pr-fix-tests.yml \
    .github/workflows/
cp /path/to/development/templates/test-drift-fix/test-drift-fix.yml \
    .github/workflows/
# Then customise test-drift-fix.yml for your project
```

### Step 2: Add Automated Reviewer to CI

Modify your main CI workflow (e.g. `functional-tests.yml`) to add:

1. A top-level `permissions` block with `pull-requests: write`
2. A `check-bot-commit` job to prevent infinite review loops
3. An `automated_reviewer` job that runs after tests pass

See the
[template README](https://github.com/shakenfist/development/tree/main/templates/ci-review-automation/README.md)
for the exact YAML snippets.

### Step 3: Ensure Runner Labels

Your self-hosted runners need these labels:

- `claude-code` -- runners with Claude Code CLI installed
- `static` -- small runners for non-mutating jobs (bot trigger
  parsing, permission checks)

## Preventing Infinite Loops

The `check-bot-commit` job detects if the last commit was authored
by `bot@shakenfist.com`. If so, the automated reviewer is skipped.
This prevents loops where:

1. Bot makes a commit (from test fixing or comment addressing)
2. CI runs on the new commit
3. Automated reviewer reviews the bot's commit
4. Maintainer triggers "address comments"
5. Bot makes another commit
6. Repeat forever

## Shared Actions

The trigger and review logic lives in the
[shakenfist/actions](https://github.com/shakenfist/actions)
repository:

- **pr-bot-trigger** -- parses `@shakenfist-bot` commands, checks
  permissions, adds reactions, posts status messages
- **review-pr-with-claude** -- runs automated code reviews with
  structured JSON output and embedded review data

## Projects Using This Automation

| Project | Automated Review | Test Fixer | Comment Addresser | Retest |
|---------|------------------|------------|-------------------|--------|
| [imago](https://github.com/shakenfist/imago) | Yes | Yes | Yes | Yes |
| [occystrap](https://github.com/shakenfist/occystrap) | Yes | Yes | Yes | Yes |
| [agent-python](https://github.com/shakenfist/agent-python) | Yes | No | Yes | Yes |
