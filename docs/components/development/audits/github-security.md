# Audit: GitHub security settings and CodeQL

## What we check

### Repository security settings

All active repositories should have these settings enabled in
Settings > Code security and analysis:

| Setting | Recommended |
|---------|-------------|
| Dependabot security updates | Enabled |
| Secret scanning | Enabled |
| Secret scanning push protection | Enabled |

Additionally recommended:

| Setting | Recommended |
|---------|-------------|
| Allow auto-merge | Enabled |

The settings are read from the API's `security_and_analysis`, which
GitHub returns only to a token with admin access. A query that fails,
or answers without them, is reported as `error` rather than as a pass
or a failure: no verdict about the settings rests on not having seen
them.

Delete branch on merge is required rather than recommended, and is
checked by its own audit: see
[delete-branch-on-merge.md](/components/development/audits/delete-branch-on-merge/).

### GitHub CodeQL

All **public** projects should have a
`.github/workflows/codeql-analysis.yml` for advanced security
scanning.

**Private repos are excluded:** CodeQL requires a paid GHAS license
for private repos. Without GHAS, the workflow will fail.

The CodeQL workflow must have job-level permissions:

```yaml
jobs:
  analyze:
    permissions:
      actions: read
      contents: read
      security-events: write
```

The `actions: read` permission is required for workflow run
telemetry.

## Template

CodeQL template: `templates/codeql/`
See: `templates/codeql/README.md`

Security settings: UI-only configuration, no template needed.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#github-security).
