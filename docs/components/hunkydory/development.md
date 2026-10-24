# Merging and releasing

How a change reaches `develop`, and how a release decides that a commit
on `develop` is fit to ship. [Testing](/components/hunkydory/testing/) covers what the checks
themselves run.

## The CI workflows

| Workflow | Runs on | What it does |
|----------|---------|--------------|
| `ci.yml` | Pull requests, merge queue entries, dispatch | The build, unit tests and Biome; the pre-commit hooks; the editor tests; the automated reviewer on pull requests; and the "Can enqueue" and "Can merge" checks the merge queue requires. |
| `secret-scan.yml` | Pull requests, merge queue entries, pushes to `develop`, dispatch | gitleaks over the history. |
| `codeql-analysis.yml` | Pull requests, merge queue entries, pushes, weekly | CodeQL. |
| `mermaid-lint.yml` | Pull requests and pushes to `develop` touching markdown, dispatch | Renders every mermaid diagram with `tools/mermaid-lint.sh`. Advisory; see below. |
| `release.yml` | A `v*` tag, dispatch | The release, as below. |

On a pull request, `ci.yml`'s jobs skip a change that touches only review
marks, and skip a pull request from a fork altogether. The editor tests,
which run on an ephemeral VM rather than the static pool, also skip a change
that touches only review marks and `docs/`; the other jobs still run for
one, so that it gets its automated review. A merge queue entry skips
nothing.

## Merging

Pull requests merge through a merge queue on `develop`, not with the
merge button directly. A pull request can be queued once its "Can
enqueue" check passes. The queue then runs `ci.yml` again on the commit
it is about to merge, and merges only when "Can merge" passes, which
requires every job to have run and succeeded. That second run is the one
a release counts (see below), so a change merged around the queue, by an
administrator bypassing it, cannot be released until a later commit has
gone through the queue.

One thing does go around the queue routinely: `prune-reviews.yml` pushes
its daily "Prune and import review marks." commit straight to `develop` as
`shakenfist-bot`, which is why that bot stays on the ruleset's bypass list.
Those commits change only `REVIEWS.md` and the weAudit files under
`.vscode/`, and `ci.yml` does not run on them. The release gate steps
across exactly such a commit to its first parent, so a tag on one that sits
on top of a queue merge is judged by that merge's queue run.

The queue builds one entry at a time (`max_entries_to_build: 1` in the
`develop` ruleset). The concurrency groups in `ci.yml`,
`secret-scan.yml` and `codeql-analysis.yml` depend on that: a queue run
shares a group with whatever the queue builds next, so that a superseded
run is cancelled. Raising the setting would make each entry cancel the
one ahead of it, so revisit those groups first.

A new job in `ci.yml` goes in the `needs:` of both "Can enqueue" and
"Can merge", or the queue will merge without it.

`mermaid-lint.yml` is the deliberate exception, and is neither in those
`needs:` lists nor a required check. It runs only when markdown changes,
and a path-filtered workflow made required never reports on the pull
requests it filters out, so it would hold them in the queue forever. A red
"Mermaid lint" on a pull request blocks nothing automatically; fix it
before enqueueing anyway. `tools/mermaid-lint.sh` runs it by hand. Both
files are copies of shakenfist/development's `templates/mermaid-lint/`, so
change them there rather than here; the workflow keeps two local changes,
named in its header -- like `ci.yml`, it skips a pull request from a fork,
and its checkout keeps no token.

Enqueueing a fork's pull request is the point at which its code first
runs here. On the pull request itself `ci.yml` skips a fork, but a merge
queue entry is not a pull request event: the queue builds the commit on a
branch of this repository, from the workflow files in that commit. So the
queued commit runs all of its own code -- workflows, `package-lock.json`,
the pre-commit config and the tests -- on the persistent static runners,
and it runs with this repository's secrets. Among them is
`DEPENDENCIES_TOKEN`, which `prune-reviews.yml` uses to push to `develop`
as `shakenfist-bot`, around the queue. A workflow the fork edits can also
ask for any `GITHUB_TOKEN` permission the repository allows. Treat a
fork's change under `.github/workflows/` as a request for those
credentials, and review the whole diff with that in mind before
enqueueing it; changes under `.github/` and `tools/` deserve the closest
reading.

## Releasing

Pushing a `v*` tag triggers `release.yml`. Its first job, `ci-gate`,
checks that `ci.yml` succeeded on the tagged commit in its merge queue
run, and nothing is built or published unless it did. How the gate
decides is documented with the action, as
[`require-ci-success`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#require-ci-success).
It has no override.

So tag the merge commit the queue made, after its `ci.yml` run has
finished. A run started by hand, as `pr-retest.yml` does with
`workflow_dispatch`, does not count. A queue run that fails, flaky or
not, ejects its entry, so the commit it built never reaches `develop`
and re-running that run cannot make any tag pass: re-queue the pull
request and tag the commit that merges. Re-running a queue run helps
only when the tagged commit's run was re-run after it merged and that
later attempt failed, because the gate judges each run at its latest
attempt; then use "Re-run jobs" on it, then re-run `ci-gate`. A gate
failure means nothing was published, so every failure is recoverable;
the template's
[recovery table](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#recovering-from-a-gate-failure)
says what to do for each cause.

Running `release.yml` by hand with `workflow_dispatch` skips the gate
and the publishing jobs, and only builds the `.vsix`.

The one-time setup, the jobs after the gate, and how to cut a release
step by step are in
[RELEASE-SETUP.md](https://github.com/shakenfist/hunkydory/blob/develop/RELEASE-SETUP.md).
