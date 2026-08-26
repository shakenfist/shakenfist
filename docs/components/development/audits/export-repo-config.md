# Audit: Exporting repo configuration changes

## What we check

* `.github/workflows/export-repo-config.yml` exists.
* The workflow delegates to the shared reusable workflow in
  `shakenfist/actions` and runs daily at 00:30 UTC.
* The workflow is project-agnostic and can be copied directly with
  no modifications.

## Template

Template: `templates/export-repo-config/`
See: `templates/export-repo-config/README.md`

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#3 |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#35 |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **cloudgood** (Status): Missing .github/workflows/export-repo-config.yml
- **library-utilities** (Status): Missing .github/workflows/export-repo-config.yml
<!-- consistency-audit:end -->
