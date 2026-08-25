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

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-25T06:54:21.186929+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
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
