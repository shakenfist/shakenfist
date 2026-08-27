# Audit: Default branch naming

## What we check

All active repositories should use `develop` as the default branch.

Exceptions are allowed for:

* Documentation-only repos (may use `main`).
* GitHub Actions repos (conventionally use `main`).
* Archived/deprecated repos (listed in exceptional cases).

## Template

No template -- this is a one-time configuration change.

To change the default branch:

```bash
git branch -m main develop
git push -u origin develop
# In GitHub UI: Settings > General > Default branch > change to develop
git push origin --delete main
```

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#default-branch-naming).
