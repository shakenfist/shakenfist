# Development

How to set up, test, and release occystrap.

## Install for development

```
pip install -e ".[test]"
```

## Pre-commit hooks

This project uses pre-commit hooks to validate code before commits. Install them
with:

```
pip install pre-commit
pre-commit install
```

The hooks run:

- `actionlint` - GitHub Actions workflow validation
- `shellcheck` - Shell script linting
- `check-log-levels` - Enforces max LOG.info() calls per file
- `tox -eflake8` - Python code style checks
- `tox -epy3` - Unit tests

To run the hooks manually:

```
pre-commit run --all-files
```

## Running tests

Unit tests are in `occystrap/tests/` and can be run with:

```
tox -epy3
```

Functional tests are in `deploy/occystrap_ci/tests/` and are run in CI.

## Releasing

Releases are automated via GitHub Actions. Push a version tag to trigger the
pipeline:

```
git tag -s v0.5.0 -m "Release v0.5.0"
git push origin v0.5.0
```

The workflow builds the package, signs the tag with Sigstore, publishes to
PyPI, and creates a GitHub Release. See
[RELEASE-SETUP.md](https://github.com/shakenfist/occystrap/blob/develop/RELEASE-SETUP.md)
for one-time configuration steps.

## Developer automation

This project supports automated CI helpers via PR comments. To use these
commands, comment on a pull request with one of the following:

- `@shakenfist-bot please retest` - Re-run the functional test suite
- `@shakenfist-bot please attempt to fix` - Have Claude Code attempt to fix
  test failures
- `@shakenfist-bot please re-review` - Request another automated code review
- `@shakenfist-bot please address comments` - Have Claude Code address the
  automated review comments

These commands are only available to repository collaborators with write access.

## Claude Code skills

The `.claude/skills/` directory contains guidance for AI agents working on
this codebase, covering documentation updates, testing discipline, and PR
preparation.
