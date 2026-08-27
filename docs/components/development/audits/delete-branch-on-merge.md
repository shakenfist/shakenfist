# Audit: Delete branch on merge

## What we check

All active repositories should have "Automatically delete head
branches" enabled (Settings > General > Pull Requests), so that a
pull request's source branch is deleted automatically when the PR
merges. This keeps repositories free of stale merged branches.

The check queries the `delete_branch_on_merge` repository setting
via the GitHub API. Note that the API only exposes this setting to
tokens with push access to the repository.

## Template

No template -- this is a one-time configuration change.

To enable via the CLI:

```bash
gh api -X PATCH repos/shakenfist/<repo> -F delete_branch_on_merge=true
```

Or in the GitHub UI: Settings > General > Pull Requests > check
"Automatically delete head branches".

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#delete-branch-on-merge).
