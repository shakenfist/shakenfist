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

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#pyproject-usage).
