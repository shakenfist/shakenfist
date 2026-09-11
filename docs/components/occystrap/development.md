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

- `skillsaw` - Lints the agent context (`AGENTS.md`, `CLAUDE.md`, the
  skills) for malformed frontmatter, smuggled unicode and pasted secrets
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

Both CI lanes -- `Sanity checks` and `Functional tests` -- run on
ephemeral VM runners and skip changes that touch only `docs/**` or
markdown at the top of the tree, because nothing they run reads those
files. To test a documentation-only branch anyway, run the functional
workflow by hand (`workflow_dispatch`, which path filters do not apply
to) or comment `@shakenfist-bot please retest` on the pull request. The
`Supply chain` lane is deliberately not filtered.

Every lane that runs on a pull request checks out the pull request's own
tree and runs scripts from it, so a pull request can change what CI
executes -- `tools/gitleaks-scan.sh` and `tools/mermaid-lint.sh` as much
as the test suite. That is contained by the runner rather than by the
workflow: the `vm` label means an ephemeral virtual machine which is
discarded after the job, and the `debian-12-docker` image the mermaid
lane needs has its docker daemon inside that VM. Fork pull requests
additionally need a maintainer to approve the run before any of it
starts, which is a repository setting rather than something these files
can assert. A lane which needed more than that would have to run from
the base branch's copy of the script instead.

## Supply chain checks

The `Supply chain` workflow runs the two checks which look at the
content of the repository itself rather than at the code it builds:
credential scanning, and the agent context lint.

### Credential scanning

It scans every commit reachable from `HEAD` for
leaked credentials with [gitleaks](https://github.com/gitleaks/gitleaks),
on every pull request, on pushes to `develop`, and weekly. It is
deliberately not path filtered: a credential pasted into a documentation
code sample is still a credential.

To run the same scan locally:

```
sudo apt-get install -y gitleaks    # Debian 13 or later
tools/gitleaks-scan.sh
```

The script needs a full clone, not a shallow one -- a secret which was
committed and later reverted is still in the history, and still needs
rotating. Before it trusts a clean result it plants a GitHub token and
an SSH private key in a scratch directory and fails if gitleaks does not
report both, so a pass means "scanned and found nothing" rather than
"the scanner is broken". Pass `--gitleaks PATH` to use a downloaded
binary instead of the packaged one.

If the scan reports something, the credential needs rotating wherever it
was trusted -- history cannot be rewritten to unpublish it. A recurring
false positive (a documentation placeholder, a test fixture) is
allowlisted by adding a `.gitleaks.toml` keyed on the text; there is no
such file yet, because there has been nothing to forgive.

### Agent context lint

The `agent context` job runs the `skillsaw` pre-commit hook over the
files an agent obeys. CI runs the hook rather than the linter, so
`.pre-commit-config.yaml` stays the only place the skillsaw version is
written down, and running it in both places is deliberate: a hook can be
skipped with `--no-verify`, or by a clone which never ran `pre-commit
install`.

## Diagrams

Diagrams of structure or flow are written as fenced `mermaid` blocks
rather than drawn in characters: GitHub renders them natively, and one
source is then a picture everywhere rather than a picture nowhere.
Character art that is not a diagram -- a file tree, an on-disk byte
layout, captured terminal output -- stays in a plain code fence, because
converting it would destroy the column alignment that carries its
meaning.

Mermaid fails at render time rather than at commit time, so a broken
diagram commits cleanly and then shows an error box on the rendered
page. The `Mermaid lint` workflow renders every tracked markdown file
that contains a diagram and fails if any of them does not parse. To run
it locally, with a docker daemon available:

```
tools/mermaid-lint.sh              # every tracked markdown file
tools/mermaid-lint.sh docs/foo.md  # just this one
```

It is not in the `Supply chain` workflow because it needs a docker
daemon and therefore a different runner image.

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
- `@shakenfist-bot please attempt to fix` - Have Claude Code attempt to
  fix unit test failures
- `@shakenfist-bot please re-review` - Request another automated code review

These commands are only available to repository collaborators with write
access, on pull requests from this repository rather than from a fork.

`please attempt to fix` runs `tox -epy3`, and only that. It lands on
the shared `claude-code` runner pool, which has docker but no root, so
the functional suite in `deploy/occystrap_ci/` -- which mounts overlays
and drives runc against a local registry -- cannot run there at all. A
functional failure needs a person, or `please retest` if it looked like
a flake.

There used to be a `please address comments` command, which had Claude
Code push fixes for the review's findings onto the branch. It is retired
across the fleet: it held write access to the pull request branch for a
feature nobody used, and applying a review is work for whoever wrote the
change.

## Workflows taken from the fleet templates

Five files here are byte-identical copies of templates in
[shakenfist/development](https://github.com/shakenfist/development),
kept that way so that drift is a `diff` rather than a judgement:

| This repository | Template | Taken at |
|-----------------|----------|----------|
| `.github/workflows/pr-re-review.yml` | `templates/ci-review-automation/pr-re-review.yml` | `c6f3a88` |
| `.github/workflows/pr-retest.yml` | `templates/ci-review-automation/pr-retest.yml` | `c6f3a88` |
| `.github/workflows/pr-fix-tests.yml` | `templates/test-drift-fix/pr-fix-tests.yml` | `aec16db` |
| `.github/workflows/mermaid-lint.yml` | `templates/mermaid-lint/mermaid-lint.yml` | `ff991f8` |
| `tools/mermaid-lint.sh` | `templates/mermaid-lint/mermaid-lint.sh` | `b83f1f9` |

Fix them upstream and re-copy, rather than editing them here: the
comments they carry are the template's, and the reason the bot trigger
was rewritten was that the previous hand-rolled copy had quietly
diverged from the shared action's fork handling. Anything genuinely
occystrap-specific belongs in this file instead, which is why the note
about runner containment above is here and not in a workflow header.

`test-drift-fix.yml` is the one copy that cannot be byte-identical:
the template ships `{{PLACEHOLDER}}` markers for the dependency
install, the test command and the Claude prompt, because those are the
project's to write. It tracks
`templates/test-drift-fix/test-drift-fix.yml` at `4cb0c43` and differs
only at those three points, each marked in the file, so a diff against
the template still reads cleanly.

## Claude Code skills

The `.claude/skills/` directory contains guidance for AI agents working on
this codebase, covering documentation updates, testing discipline, and PR
preparation.

Each skill lives in its own directory as `.claude/skills/<name>/SKILL.md`,
with `name` and `description` frontmatter. That layout is what an agent
discovers -- a bare markdown file directly in `.claude/skills/` is never
loaded, and is not linted by skillsaw either, so it looks like guidance
while doing nothing.
