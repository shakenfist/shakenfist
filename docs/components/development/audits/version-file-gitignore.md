# Audit: Generated version file

## What we check

Python projects using `setuptools_scm` write a generated version file
(conventionally `<package>/_version.py`, configured by `write_to` or
`version_file` in the `[tool.setuptools_scm]` section of
`pyproject.toml`) into the source tree at build time. That file must
never be committed to git:

* The configured version file path must be covered by `.gitignore`
  (verified with `git check-ignore`).
* No file matching `*_version.py` may be tracked by git. If one is,
  remove it with `git rm --cached <path>` and add the `.gitignore`
  entry.

This audit exists because a generated `_version.py` was accidentally
committed on a `client-python` pull request. A tracked copy shadows
the build-time version, causing stale or wrong version numbers in
releases.

## Template

No template -- add the configured path (or a bare `_version.py`
pattern) to `.gitignore` and `git rm --cached` any tracked copy.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-23T06:45:38.740880+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | non-compliant | shakenfist/agent-python#103 |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#106 |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): shakenfist_agent/_version.py is not covered by .gitignore
- **clingwrap** (Status): clingwrap/_version.py is not covered by .gitignore
<!-- consistency-audit:end -->
