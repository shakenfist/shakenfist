# Plan: Refactor Developer Automation Workflows to Shared Actions

## Summary

This plan describes how to extract reusable GitHub Actions from the `imago`
repository into the shared `shakenfist/actions` repository, then implement
those workflows in `occystrap` as the first consumer after imago.

## Current State

### imago (source of truth for automations)

| Workflow | Purpose | Trigger |
|----------|---------|---------|
| `pr-fix-tests.yml` | Trigger Claude to fix test failures | `@shakenfist-bot please attempt to fix` |
| `pr-address-comments.yml` | Address automated review comments | `@shakenfist-bot please address comments` |
| `pr-re-review.yml` | Re-run automated review | `@shakenfist-bot please re-review` |
| `pr-retest.yml` | Re-run functional tests | `@shakenfist-bot please retest` |
| `test-drift-fix.yml` | The test fixer implementation | Called by pr-fix-tests or scheduled |
| `functional-tests.yml` | Main CI with automated reviewer | Push/PR |

### occystrap (current state)

| Workflow | Purpose | Status |
|----------|---------|--------|
| `pr-re-review.yml` | Re-run automated review | Already using shared action |
| `functional-tests.yml` | Main CI with automated reviewer | Already using shared action |
| `pr-fix-tests.yml` | Trigger Claude to fix test failures | **Missing** |
| `pr-address-comments.yml` | Address automated review comments | **Missing** |
| `pr-retest.yml` | Re-run functional tests | **Missing** |

### shakenfist/actions (shared actions repository)

Currently has:
- `review-pr-with-claude/` - Composite action for PR reviews (simpler than
  imago's script)
- `setup-test-environment/` - Test environment setup
- `setup-kerbside-environment/` - Kerbside-specific setup

## Analysis

### What Can Be Shared vs What Must Be Project-Specific

| Component | Shareable? | Notes |
|-----------|------------|-------|
| PR trigger logic | Yes | Check permissions, react to comment, get PR details |
| Claude Code invocation | Yes | Run claude CLI with standard options |
| PR review posting | Yes | Already shared in `review-pr-with-claude` |
| Test fixing | **Partial** | Trigger/reporting is generic, but fix prompts are project-specific |
| Address comments | **Partial** | Framework is generic, scripts need project context |
| Retest trigger | Yes | Just triggers a workflow by name |

### Key Insight

The imago workflows have two layers:
1. **Trigger layer** - Generic: check permissions, react, post messages
2. **Implementation layer** - Often project-specific: prompts, test commands

We should extract the trigger layer into shared actions, while allowing
projects to customize the implementation layer.

## Implementation Phases

### Phase 1: Create New Shared Actions in shakenfist/actions

#### 1.1 Create `pr-bot-trigger` Composite Action

A reusable action that handles the common pattern of:
- Checking if a comment matches a trigger phrase
- Verifying commenter has write permissions
- Adding a reaction to the comment
- Posting unauthorized/starting messages
- Outputting PR details for downstream use

**File:** `shakenfist/actions/pr-bot-trigger/action.yml`

```yaml
name: 'PR Bot Trigger Handler'
description: >
  Handle @shakenfist-bot trigger comments. Validates permissions,
  reacts to the comment, and outputs PR details.

inputs:
  trigger-phrase:
    description: 'The phrase to look for (e.g., "please attempt to fix")'
    required: true
  reaction:
    description: 'Reaction to add (rocket, +1, etc.)'
    required: false
    default: 'rocket'
  starting-message:
    description: 'Message to post when starting (supports {run_url} placeholder)'
    required: false
    default: ''

outputs:
  authorized:
    description: 'Whether the user is authorized'
    value: ${{ steps.check.outputs.authorized }}
  pr-number:
    description: 'The PR number'
    value: ${{ steps.check.outputs.pr_number }}
  pr-ref:
    description: 'The PR branch ref'
    value: ${{ steps.details.outputs.pr_ref }}
  triggered:
    description: 'Whether the trigger phrase was found'
    value: ${{ steps.check.outputs.triggered }}

runs:
  using: "composite"
  steps:
    # Implementation handles permission check, reaction, messages
```

#### 1.2 Create `pr-retest-trigger` Composite Action

A simple action that triggers the `functional-tests.yml` workflow.

**File:** `shakenfist/actions/pr-retest-trigger/action.yml`

This wraps the common pattern of using `gh workflow run` to trigger the
functional tests workflow.

#### 1.3 Enhance `review-pr-with-claude` Action

The existing action is simpler than imago's script. Options:
1. Keep it simple for basic projects (current state)
2. Add optional parameters for structured JSON output, issue creation

For now, keep both:
- Simple version in actions repo for basic projects
- Imago can continue using its sophisticated script if needed

#### 1.4 Create `address-comments` Composite Action (Future)

This is more complex because it requires:
- Project-specific helper scripts (render-review.py, etc.)
- Understanding of project structure (AGENTS.md, etc.)

**Recommendation:** Defer this to Phase 3. For now, have projects copy the
pattern from imago with project-specific customizations.

### Phase 2: Update imago to Use Shared Actions

#### 2.1 Update `pr-re-review.yml`

imago currently calls `tools/review-pr-with-claude.sh` directly. Update to:
- Use `shakenfist/actions/review-pr-with-claude@main` like occystrap does
- OR keep using the local script if the extra features are needed

The imago script has extra features:
- Structured JSON output
- Issue creation
- Embedded JSON for address-comments

**Recommendation:** Keep the local script in imago for now, but update
occystrap's automated reviewer in functional-tests.yml to optionally use
the simpler shared action.

#### 2.2 Update `pr-retest.yml`

Update to use the shared trigger action:

```yaml
jobs:
  trigger-retest:
    if: |
      github.event.issue.pull_request &&
      contains(github.event.comment.body, '@shakenfist-bot please retest')
    runs-on: ubuntu-latest
    steps:
      - uses: shakenfist/actions/pr-bot-trigger@main
        id: trigger
        with:
          trigger-phrase: 'please retest'
          reaction: 'rocket'
          starting-message: |
            Triggered functional tests on branch `{pr_ref}`.
            [View workflow runs]({workflow_url})

      - name: Trigger functional tests
        if: steps.trigger.outputs.authorized == 'true'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh workflow run functional-tests.yml \
            --repo ${{ github.repository }} \
            --ref "${{ steps.trigger.outputs.pr-ref }}"
```

### Phase 3: Implement Workflows in occystrap

#### 3.1 Add `pr-retest.yml`

Create `.github/workflows/pr-retest.yml` using the shared trigger action.
This is the simplest workflow to add as it just triggers the existing
functional-tests.yml.

#### 3.2 Add `pr-fix-tests.yml` and Test Fixer Implementation

For occystrap, the test fixer is simpler than imago's because:
- No testdata repository to clone
- Simpler Python test suite (pytest/stestr)
- No Rust/cargo complexity

Create:
- `.github/workflows/pr-fix-tests.yml` - Trigger workflow
- `.github/workflows/test-drift-fix.yml` - Implementation (occystrap-specific)

The test fixer prompt needs to be customized for occystrap:
- Reference occystrap's AGENTS.md
- Use pytest/stestr commands
- Understand occystrap's directory structure

#### 3.3 Add `pr-address-comments.yml`

This requires:
1. Creating tools for occystrap:
   - `tools/review-pr-with-claude.sh` (can copy from imago, simplify)
   - `tools/render-review.py` (can copy from imago)
   - `tools/address-comments-with-claude.sh` (can copy from imago, simplify)

2. Creating the workflow that uses these tools

**Alternative approach:** Use the simpler shared action for reviews and skip
the structured JSON/address-comments flow initially. Add it later once the
pattern is proven.

### Phase 4: Documentation and Rollout

#### 4.1 Update PROJECT-CONSISTENCY-AUDITS.md

Add a section documenting:
- Which shared actions are available
- When to use each one
- How to customize for project-specific needs

#### 4.2 Update README/AGENTS in each repository

Document the bot commands available:
- `@shakenfist-bot please attempt to fix`
- `@shakenfist-bot please address comments`
- `@shakenfist-bot please re-review`
- `@shakenfist-bot please retest`

## Files to Create/Modify

### shakenfist/actions

| File | Action |
|------|--------|
| `pr-bot-trigger/action.yml` | Create |
| `pr-bot-trigger/check-and-trigger.sh` | Create |
| `pr-retest-trigger/action.yml` | Create (optional) |
| `README.md` | Update with new actions |

### shakenfist/imago

| File | Action |
|------|--------|
| `.github/workflows/pr-retest.yml` | Update to use shared action (optional) |
| `.github/workflows/pr-fix-tests.yml` | Update to use shared action (optional) |
| `tools/review-pr-with-claude.sh` | Keep (has features not in shared action) |
| `tools/address-comments-with-claude.sh` | Keep (project-specific prompts) |

### shakenfist/occystrap

| File | Action |
|------|--------|
| `.github/workflows/pr-retest.yml` | Create |
| `.github/workflows/pr-fix-tests.yml` | Create |
| `.github/workflows/test-drift-fix.yml` | Create |
| `.github/workflows/pr-address-comments.yml` | Create (Phase 3) |
| `tools/review-pr-with-claude.sh` | Create (copy from imago, simplify) |
| `tools/render-review.py` | Create (copy from imago) |
| `tools/address-comments-with-claude.sh` | Create (copy from imago, simplify) |

## Implementation Order

1. **Start with pr-retest.yml for occystrap**
   - Simplest workflow (no Claude, just triggers tests)
   - Validates the shared action approach
   - Immediate value for occystrap

2. **Add pr-fix-tests.yml for occystrap**
   - Requires creating test-drift-fix.yml with occystrap-specific prompts
   - More complex but provides significant value

3. **Enhance automated review in occystrap**
   - Add the sophisticated review script from imago
   - Enable pr-address-comments.yml

4. **Refactor imago to use shared actions**
   - Only after patterns are proven in occystrap
   - May choose to keep project-specific implementations

## Verification

For each added workflow:

1. Create a test PR in the target repository
2. Comment with the trigger phrase
3. Verify:
   - Unauthorized users get rejection message
   - Authorized users get acknowledgment
   - The automation runs successfully
   - Results are posted back to the PR

## Open Questions

1. **Should review-pr-with-claude be enhanced or kept simple?**
   - Simple version works for occystrap today
   - imago's version has structured JSON, issue creation
   - Could have two variants: `review-pr-with-claude` (simple) and
     `review-pr-with-claude-structured` (full)

2. **How project-specific should test-drift-fix be?**
   - The prompt is very project-specific (mentions make targets, directories)
   - The framework (checkout, run claude, commit, push) is generic
   - Maybe split into a generic shell + project-specific prompt template

3. **Should tools/ scripts be copied or shared?**
   - Copying allows project-specific customization
   - Sharing reduces maintenance but limits flexibility
   - Current recommendation: copy and customize
