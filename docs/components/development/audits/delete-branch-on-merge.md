# Audit: Delete branch on merge

## What we check

All active repositories should have "Automatically delete head
branches" enabled (Settings > General > Pull Requests), so that a
pull request's source branch is deleted automatically when the PR
merges. This keeps repositories free of stale merged branches.

The check queries the repository's `deleteBranchOnMerge` setting
through the GitHub GraphQL API. Not the REST API's
`delete_branch_on_merge`: the REST repository answer leaves that
field out for a fine-grained token with read-only Administration,
which is what the audit runs with, while GraphQL answers it. A null
answer, from a token that cannot see the repository's administration
settings, is reported as `error` rather than as a failure, because it
says nothing about the setting.

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
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#delete-branch-on-merge).
