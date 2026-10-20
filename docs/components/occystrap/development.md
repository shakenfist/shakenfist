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

- `skillsaw` - Lints the agent context (`AGENTS.md` and the skills) for
  malformed frontmatter, smuggled unicode and pasted secrets
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

Arguments after `--` are passed to `stestr run` as a test filter, so a
single test or module runs with:

```
tox -epy3 -- occystrap.tests.test_quay
```

`tox -eflake8` lints only the Python files changed in the most recent
commit (it runs `tools/flake8wrap.sh -HEAD`), and the pre-commit hook
inherits that scope. To lint the whole tree:

```
flake8 --max-line-length=120 occystrap/
```

Functional tests are in `deploy/occystrap_ci/tests/` and are run in CI,
against a local registry at `localhost:5000` that the workflow populates
with test images.

Both CI lanes -- `Sanity checks` and `Functional tests` -- run on
ephemeral VM runners. A pull request that touches only `docs/**` or
markdown skips them, because nothing they run reads those files: a
`Check paths` job in each workflow decides, and the lane's jobs skip on
its output. A merge queue entry always runs both in full. To test a
documentation-only branch anyway, run the functional workflow by hand
(`workflow_dispatch`, which the filter does not apply to) or comment
`@shakenfist-bot please retest` on the pull request. The `Supply chain`
lane is deliberately not filtered.

Every lane that runs on a pull request checks out the pull request's own
tree and runs scripts from it, so a pull request can change what CI
executes -- `tools/gitleaks-scan.sh` and `tools/mermaid-lint.sh` as much
as the test suite. That is contained by the runner rather than by the
workflow: the `vm` label means an ephemeral virtual machine which is
discarded after the job, and the `debian-13-docker` image the mermaid
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

## Merging

Pull requests merge through a merge queue on `develop`, not with the
merge button directly. A pull request can be queued once "Can enqueue"
and "Can enqueue: unit" pass. The queue then runs `Sanity checks` and
`Functional tests` again on the commit it is about to merge, and merges
only when "Can merge" and "Can merge: unit" pass. Each workflow has a
pair of its own, because a job's `needs:` cannot reach across workflows
and the queue has to require both. That second run is the one a release
counts (see below), so a change merged around the queue, by an
administrator bypassing it, cannot be released until a later commit has
gone through the queue.

The queue builds one entry at a time (`max_entries_to_build: 1` in the
`develop` ruleset). The `concurrency` groups in `python-unit-tests.yml`
and `functional-tests.yml` depend on that: a queue run shares a group
with whatever the queue builds next, so that a superseded run is
cancelled. Raising the setting would make each entry cancel the one
ahead of it, so revisit those groups first.

A new job in either workflow goes in the `needs:` of that workflow's
two aggregator jobs.

The wheel check in the functional lane is
[`check-dist`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#check-dist).
It fails if the wheel holds a test directory, so a packaging change
that would ship the tests fails on its pull request rather than at
release time.

## Releasing

Pushing a `v*` tag triggers `release.yml`. Its jobs, in order:

1. `ci-gate` checks that `python-unit-tests.yml` and
   `functional-tests.yml` both succeeded on the tagged commit, in their
   merge queue runs. A run started by hand, as `pr-retest.yml` does
   with `workflow_dispatch`, does not count. A queue run that fails,
   flaky or not, ejects its entry, so the commit it built never reaches
   `develop` and re-running that run cannot make any tag pass: re-queue
   the pull request and tag the commit that merges. Re-running a queue
   run helps only when the tagged commit's run was re-run after it
   merged and that later attempt failed, because the gate judges each
   run at its latest attempt; then use "Re-run jobs" on it.
   How the gate decides is documented with the action, as
   [`require-ci-success`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#require-ci-success).
   It has no override.
2. `build` builds the sdist and wheel, then runs `twine check` and the
   same wheel check as CI. It runs only if the gate passed.
3. The publishing jobs wait for approval in the `release`
   environment, re-sign the tag with gitsign, and publish to PyPI with
   attestations.

So tag a commit that is on `develop` and went through the merge
queue, and wait for both workflows' runs on it to finish first. A gate
failure means nothing was signed or published, so every failure is
recoverable: the template's
[recovery table](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#recovering-from-a-gate-failure)
says what to do for each cause, which is usually moving the tag or
re-running CI and then the `ci-gate` job.

```
git tag -s v0.5.0 -m "Release v0.5.0"
git push origin v0.5.0
```

Running `release.yml` by hand with `workflow_dispatch` skips the gate
and the publishing jobs, and only builds and checks the packages. See
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
| `.github/workflows/pr-re-review.yml` | `templates/ci-review-automation/pr-re-review.yml` | `3b5142c` |
| `.github/workflows/pr-retest.yml` | `templates/ci-review-automation/pr-retest.yml` | `5e9a4b3` |
| `.github/workflows/pr-fix-tests.yml` | `templates/test-drift-fix/pr-fix-tests.yml` | `aec16db` |
| `.github/workflows/mermaid-lint.yml` | `templates/mermaid-lint/mermaid-lint.yml` | `7cadf3e` |
| `tools/mermaid-lint.sh` | `templates/mermaid-lint/mermaid-lint.sh` | `5279ac3` |

Fix them upstream and re-copy, rather than editing them here: the
comments they carry are the template's, and the reason the bot trigger
was rewritten was that the previous hand-rolled copy had quietly
diverged from the shared action's fork handling. Anything genuinely
occystrap-specific belongs in this file instead, which is why the note
about runner containment above is here and not in a workflow header.

`pr-retest.yml` dispatches the workflow named by the `RETEST_WORKFLOW`
repository variable, or `functional-tests.yml` when it is unset.
occystrap leaves it unset: `functional-tests.yml` is the suite a retest
is for, and it carries the `workflow_dispatch` trigger the dispatch
needs.

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
