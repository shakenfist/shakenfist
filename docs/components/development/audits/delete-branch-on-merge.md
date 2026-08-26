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

<!-- consistency-audit:begin -->
*Generated 2026-08-25T06:54:21.186929+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
<!-- consistency-audit:end -->
