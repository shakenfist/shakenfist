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

`sanity_checks` runs on an ephemeral virtual machine, the expensive
runner pool, so a `check_paths` job decides first whether the change
touches anything the lane exercises. A pull request or queue entry
that only edits `docs/` skips `sanity_checks`, and the automated
reviewer with it. Because "Can enqueue" and "Can merge" are required
status checks, the filtering has to be a job rather than a
trigger-level `paths-ignore`: a required check in a filtered-out
workflow never reports at all, and a check that never reports blocks
the merge forever, while a skipped one satisfies it. Both gates
therefore treat a skipped need as satisfied.

That makes each gate's `needs:` list load bearing in a second way. A
job that skipped because something it depended on failed is reported
as `skipped` too, so a gate must directly name every job whose failure
should block it -- `check_paths` included -- rather than reaching it
through `sanity_checks`.

A dispatched run has no diff to filter, so `check_paths` defaults its
verdict to `true` and the lane runs.

### Mermaid diagrams

Mermaid fails at render time, not at commit time: a diagram with a
syntax error commits cleanly, passes every other linter, and then
shows an error box on GitHub and nothing at all on a mkdocs site.
`mermaid-lint.yml` closes that gap by rendering every tracked markdown
file that contains a `mermaid` fence, through `tools/mermaid-lint.sh`.
Run the same check locally:

```bash
tools/mermaid-lint.sh                 # every tracked markdown file
tools/mermaid-lint.sh ARCHITECTURE.md # just this one
```

Running it locally wants bash 4.4 or newer, for `mapfile -d`, and GNU
coreutils, for `realpath --relative-to`: the Debian 13 runner CI uses,
in other words. On macOS the stock bash is 3.2 and `realpath` is BSD's,
so the script fails partway through with an error from the shell rather
than one of its own explanatory refusals. Use a container or a Linux
host there.

Both files are copies of `templates/mermaid-lint/` in
shakenfist/development and are synced from there rather than edited
here. The job needs a docker daemon, so it runs on a
`debian-13-docker` runner rather than a static one, and that label is
listed in `.github/actionlint.yaml`.

The lane is path-filtered to markdown and so is deliberately not a
required status check: a required check in a path-filtered workflow
never reports on the pull requests it filters out, and a check that
never reports blocks the merge forever. It is the one job that can
show red on a pull request without appearing in "Can enqueue" or "Can
merge". Check it by eye before merging a diagram change.

### Content scanning

`supply-chain.yml` scans the repository's own content rather than
testing it, and so is a workflow of its own. Its `gitleaks` job runs
`tools/gitleaks-scan.sh`, which scans every commit reachable from
`HEAD` -- on a pull request that is the branch under test plus all of
`develop` -- and fails if gitleaks reports anything. A secret committed
and then reverted is still in the history and still needs rotating, so
the checkout is a full one and the script refuses a shallow clone
rather than reporting a clean history it never read.

The script commits a GitHub token and an SSH private key to a scratch
git repository first and fails unless gitleaks reports both. A scanner
that finds nothing is otherwise indistinguishable from a scanner that
cannot find anything: a rule set that changed shape between releases,
or an allowlist grown wide enough to forgive everything. Green means
"scanned and found nothing", not "did nothing".

The control is a git repository, scanned with the same
`--log-opts="HEAD"` invocation as the real scan, rather than a
`--no-git` directory walk. Directory and history scanning are separate
code paths in gitleaks, so a directory-mode control would stay green
while the history scan read nothing -- and history is the mode this job
exists for. Run it locally the same way CI does:

```bash
tools/gitleaks-scan.sh
```

Its `agent-context` job lints `AGENTS.md` and anything under
`.claude/` with [skillsaw](https://skillsaw.org/), looking for
malformed structure, smuggled unicode and pasted credentials. It runs
the `skillsaw` pre-commit hook rather than invoking skillsaw directly,
so `.pre-commit-config.yaml`'s `rev` is the only place the version is
written down -- skillsaw's rule set moves between releases, and
renovate's pre-commit manager bumps the hook on its own, so a second
pin in the workflow would drift without anyone choosing it. It runs in
CI as well as in pre-commit because a hook can be skipped with
`--no-verify`, or by a clone that never ran `pre-commit install`.

The workflow is deliberately not path-filtered, unlike
`functional-tests.yml`. A credential pasted into a code sample is a
credential, and the one real secret this fleet's scanning has found was
published in a user guide; a document is also exactly where an
instruction aimed at an agent would be hidden, which is why the agent
context lint lives here rather than in `sanity_checks`. The workflow
backs no required status check, which is why it also runs on pushes to
`develop`: a commit that reached the branch without a pull request
would otherwise never be scanned. The weekly cron catches a rule set
that drifted under an unchanging tree.

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
automated reviewer stays out of both, because it is advisory. A new
job on a `vm` runner also belongs behind `check_paths`; one that reads
the repository's prose -- a content scanner, a documentation check --
belongs in `supply-chain.yml` instead, where nothing filters it.

Several jobs run a fork's own configuration on the self-hosted pool:
`sanity_checks` runs its `tox.ini`, and `supply-chain.yml`'s
`agent-context` job runs its `.pre-commit-config.yaml`, which chooses
the repository, rev and entry that execute. Those jobs carry no
same-repository guard, and the comments in both files say so. What
makes that acceptable is not anything in the workflow files -- it is
a repository setting, that a maintainer must approve a workflow run on
every outside contributor's pull request, not merely a first-time
one. Confirm it rather than assuming it:

```bash
gh api repos/shakenfist/clingwrap/actions/permissions/fork-pr-contributor-approval
```

It must report `all_external_contributors`; it was set to that on both
the repository and the `shakenfist` organization as of November 2026.
If it is ever relaxed to `first_time_contributors`, those two jobs
need a same-repository guard before anything else here is trusted.

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

## Documentation link conventions

`README.md`'s links to other files in this repository are absolute
`https://github.com/shakenfist/clingwrap/blob/develop/...` URLs.
Everything else -- `AGENTS.md`, `ARCHITECTURE.md`, the pages under
`docs/` -- uses relative links.

The split is not an oversight. `pyproject.toml` sets `readme =
"README.md"`, so README is the PyPI long description, and PyPI renders
it on a page where a relative link resolves against `pypi.org` and
404s. No other file in the repository is published anywhere but GitHub,
where relative links are the ones that survive a fork, a rename and a
tag.

`develop` rather than a tag because it is this repository's default
branch, so those links show the current documentation. A reader of a
released sdist therefore follows them to documentation newer than their
copy, which is the right trade for a link in a PyPI description: the
alternative is a link into a tag that will eventually describe
behaviour nobody runs any more.
