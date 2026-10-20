# Development

How changes reach `develop`, and how a release is cut from it.

## Continuous integration

`functional-tests.yml` is the CI workflow. It runs on pull requests to
`develop`, on merge queue entries, and when dispatched by hand (which
is what the retest bot comment listed in `AGENTS.md` does, through
`pr-retest.yml`).
Its `sanity_checks` job lints with flake8 and checks that the
dependencies in `pyproject.toml` install. On a pull request the
automated reviewer then runs, and the "Can enqueue" check collects the
result (it also runs on a dispatched run). On a merge queue entry the
"Can merge" check collects it instead.

## Merging

Pull requests merge through a merge queue on `develop`, not with the
merge button directly. A pull request can be queued once its "Can
enqueue" check passes. The queue then runs `functional-tests.yml`
again on the commit it is about to merge, and merges only when "Can
merge" passes. That second run is the one a release counts (see
below), so a change merged around the queue, by an administrator
bypassing it, cannot be released until a later commit has gone
through the queue.

The queue builds one entry at a time (`max_entries_to_build: 1` in the
`develop` ruleset). `functional-tests.yml`'s concurrency groups depend
on that: a queue run shares a group with whatever the queue builds
next, so that a superseded run is cancelled. Raising the setting would
make each entry cancel the one ahead of it, so revisit those groups
first.

A new job in `functional-tests.yml` goes in the `needs:` of both "Can
enqueue" and "Can merge", or the queue will merge without it. The
automated reviewer stays out of both, because it is advisory.

Enqueueing a fork's pull request is also the point at which its code
first runs unreviewed by GitHub's approval setting, which does not
apply to merge queue runs. A queued commit runs all of its own code --
workflows, `tools/` scripts, `tox.ini` and packaging -- on the
project's self-hosted runners. Review the whole diff with that in mind
before enqueueing it, and read changes under `.github/` and `tools/`
most closely.

## Releasing

Pushing a `v*` tag triggers `release.yml`. Its jobs, in order:

1. `ci-gate` checks that `functional-tests.yml` succeeded on the
   tagged commit, in its merge queue run. A run started by hand, as
   `pr-retest.yml` does with `workflow_dispatch`, does not count. A
   queue run that fails, flaky or not, ejects its entry, so the commit
   it built never reaches `develop` and re-running that run cannot make
   any tag pass: re-queue the pull request and tag the commit that
   merges. Re-running a queue run helps only when the tagged commit's
   run was re-run after it merged and that later attempt failed,
   because the gate judges each run at its latest attempt; then use
   "Re-run jobs" on it. How the gate decides is documented with the
   action, as
   [`require-ci-success`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#require-ci-success).
   It has no override.
2. `build` builds the sdist and wheel, then runs `twine check` and
   [`check-dist`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#check-dist),
   which fails if the wheel holds a test directory or nothing at all.
   It runs only if the gate passed.
3. The publishing jobs wait for approval in the `release`
   environment, re-sign the tag with gitsign, publish to PyPI with
   attestations, and create a GitHub release.

So tag a commit that is on `develop` and went through the merge
queue, and wait for its `functional-tests.yml` run to finish first. A
gate failure means nothing was signed or published, so every failure
is recoverable: the template's
[recovery table](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#recovering-from-a-gate-failure)
says what to do for each cause, which is usually moving the tag or
re-running CI and then the `ci-gate` job.

Running `release.yml` by hand with `workflow_dispatch` skips the gate
and the publishing jobs, and only builds and checks the packages.

The version comes from the tag through setuptools_scm. The one-time
setup, and how to verify a release, are in
[RELEASE-SETUP.md](https://github.com/shakenfist/clingwrap/blob/develop/RELEASE-SETUP.md).

## Packaging

The wheel ships the `clingwrap` package and the example configurations
in `clingwrap/examples/*.cwd`, which `clingwrap gather --target <name>`
loads by name. `include-package-data` is off in `pyproject.toml`, so
any other data file the code needs at runtime has to be listed under
`[tool.setuptools.package-data]`, or it will be missing from the wheel.
