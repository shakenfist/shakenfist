# Audit: pyproject.toml usage

## What we check

All Python projects must use `pyproject.toml` for packaging and
dependency management:

* Any repository containing tracked Python files must have a
  `pyproject.toml` at the repository root.
* Legacy packaging files (`setup.py`, `setup.cfg`) must not exist
  alongside it.

Repositories where Python is incidental are excluded: Rust projects
(any Python present is helper scripts), docs-only repositories, the
`actions` library of composite actions and reusable workflows, this
repository (its Python is the audit scripts, which run from a
checkout and are never packaged), and `kerbside-patches` (a patch
archive with Python helper scripts, not a Python project). Rust
projects need no declaration -- the check reads a `Cargo.toml` at the
root of the clone and exempts the repository on that alone. The rest
are declared `not_python` or `is_docs_only` in `REPO_OVERRIDES` in
`scripts/audit-check.py`.

Note that `requirements.txt` / `test-requirements.txt` removal is
covered by the [release process audit](/components/development/audits/release-process/), and the
generated version file rules are covered by the
[generated version file audit](/components/development/audits/version-file-gitignore/).

## Template

No template -- use the `pyproject.toml` in `kerbside`, `occystrap`,
and `shakenfist` as examples of our implementation style.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-24T07:04:16.593679+00:00

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
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
<!-- consistency-audit:end -->
