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

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#version-file-gitignore).
