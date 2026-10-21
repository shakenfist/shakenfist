# Developer Guide

## Prerequisites

- Python 3.10 or later
- `tox` for running tests and linting

## Project Layout

```
shakenfist_agent/
    main.py              # CLI entry point
    log.py               # Console logging
    commandline/
        daemon.py        # vsock listener and command handlers
    protos/
        agent.proto      # Protobuf definitions
        common.proto     # Shared protobuf definitions
        *_pb2.py         # Generated stubs (do not edit)
    tests/
        test_daemon.py   # Unit tests for the daemon
        test_main.py     # Unit tests for CLI logging setup
```

## Building

The project uses `pyproject.toml` with `setuptools_scm` for
version management. Versions are derived from git tags:

```bash
pip install -e .
```

## Running Tests

```bash
# Unit tests
tox -epy3

# Linting
tox -eflake8

# Coverage report
tox -ecover
```

## Continuous Integration

Pull requests to `develop`, and merge queue entries, run
`.github/workflows/functional-tests.yml`. Its `sanity_checks` job
(flake8, a dependency install check, unit tests and coverage) runs on
an ephemeral VM runner, so a `check_paths` job skips it when a change
touches nothing outside `docs/`. The automated reviewer still runs on
those pull requests. Commenting
`@shakenfist-bot please retest` re-runs the workflow by dispatch, which
always runs the full set.

Two bot commands are honoured on pull requests, from collaborators
with write access and on same-repository pull requests only:
`@shakenfist-bot please re-review` asks the automated reviewer for
another pass (`pr-re-review.yml`), and `@shakenfist-bot please retest`
re-runs the functional tests (`pr-retest.yml`). The comment addressing
bot that used to push fixes for review items has been retired fleet
wide, and its workflow removed; address review items by hand.

`.github/workflows/supply-chain.yml` scans the git history for leaked
credentials with gitleaks on every pull request, every push to
`develop` and weekly. It is deliberately not path filtered: a
credential in a documentation sample is still a credential. The scan
lives in `tools/gitleaks-scan.sh`, which also plants two credentials in
a scratch directory and fails unless gitleaks reports both, so a clean
result means "scanned and found nothing" rather than "could not find
anything". Run it locally with `tools/gitleaks-scan.sh`, or
`tools/gitleaks-scan.sh --gitleaks PATH` to use a specific binary; it
needs a full (not shallow) clone.

If the positive control fails while nothing in the repository changed,
the likely cause is a gitleaks upgrade (the job installs Debian's
package, which is not pinned) renaming or splitting one of the two rules
it expects, `github-pat` and `private-key`. The failure message lists
the rules that did fire; update the expected list in the script to
match, after checking that the new rule still catches the planted
credential.

The only required status checks on `develop` are "Can enqueue" and
"Can merge", which collect `functional-tests.yml`'s own jobs (see
Merging below). So a failing gitleaks or agent context job is visible
on the pull request but does not by itself block a merge.

The same workflow runs [skillsaw](https://skillsaw.org/) over the agent
context (`AGENTS.md` and anything else an agent is handed) by running
its pre-commit hook. `.pre-commit-config.yaml` carries that hook
alongside actionlint and shellcheck, so run `pre-commit install` once
per clone and `pre-commit run --all-files` before proposing a change.

### Merging

Pull requests merge through a merge queue on `develop`, not with the
merge button directly. A pull request can be queued once its "Can
enqueue" check passes. The queue then runs `functional-tests.yml`
again on the commit it is about to merge, and merges only when "Can
merge" passes. That second run is the one a release counts (see
below), so a change merged around the queue, by an administrator
bypassing it, cannot be released until a later queue entry that
changes something outside `docs/` has gone through the queue. The gate
reads only the tagged commit's own runs, and a docs-only entry skips
`sanity_checks`, so its successful run would vouch for the bypassed
change without testing it. Both checks fail if any job failed, or was skipped for any
reason other than `check_paths` finding a docs-only change. A new job
in `functional-tests.yml` goes in the `needs:` of both.

The queue builds one entry at a time (`max_entries_to_build: 1` in the
`develop` ruleset). `functional-tests.yml`'s concurrency groups depend
on that: a queue run shares a group with whatever the queue builds
next, so that a superseded run is cancelled. Raising the setting would
make each entry cancel the one ahead of it, so revisit those groups
first.

Enqueueing a fork's pull request runs its version of the workflow on
the merge queue without the approval GitHub asks for on a fork's pull
request, so review the whole diff, and changes under `.github/` most
closely, before enqueueing it.

## Releasing

Pushing a `v*` tag triggers `release.yml`. Its jobs, in order:

1. `ci-gate` checks that `functional-tests.yml` succeeded on the tagged
   commit, in its merge queue run. A run started by hand, as
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
   which fails if the wheel holds a test directory. It runs only if the
   gate passed.
3. The publishing jobs wait for approval in the `release` environment,
   re-sign the tag with gitsign, and publish to PyPI with attestations.

So tag a commit that is on `develop` and went through the merge queue,
and wait for its `functional-tests.yml` run to finish first. A gate
failure means nothing was signed or published, so every failure is
recoverable: the template's
[recovery table](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#recovering-from-a-gate-failure)
says what to do for each cause, which is usually moving the tag or
re-running CI and then the `ci-gate` job.

Running `release.yml` by hand with `workflow_dispatch` skips the gate
and the publishing jobs, and only builds and checks the packages.

The wheel ships the `shakenfist_agent` package and nothing else: the
tests, the `.proto` sources and `_copy_stubs.sh` stay in the checkout.
The version comes from the tag through setuptools_scm. The one-time
setup, and how to verify a release, are in `RELEASE-SETUP.md`.

## Adding a New Command

1. **Define the protobuf messages.** Add request and reply
   message types to `shakenfist_agent/protos/agent.proto`. Add
   the request to the `HypervisorToAgentCommand.request` oneof
   and the reply to `AgentToHypervisorCommand.reply`.

2. **Regenerate stubs.** The proto files and generated stubs are
   maintained in the main
   [shakenfist](https://github.com/shakenfist/shakenfist)
   repository. Use `_copy_stubs.sh` to sync them.

3. **Implement the handler.** Add a `_handle_<command>` method
   to `VSockAgentJob` in `commandline/daemon.py`. Use
   `self._send_responses()` to send the reply.

4. **Register the dispatch.** Add a `HasField` check for your
   new request type in `_attempt_decode()`.

5. **Write tests.** Add a test method to `DaemonAgentV2TestCase`
   in `tests/test_daemon.py`. Tests construct protobuf messages,
   serialize them into the job's buffer, call `_attempt_decode()`,
   and verify the mocked `_send_responses()` calls.

## Dependencies

The agent runs inside guest VMs with minimal environments, so
the dependency list is kept deliberately small:

| Package | Purpose |
|---------|---------|
| click | CLI framework |
| distro | OS distribution detection |
| psutil | Boot time, I/O priority |
| symbolicmode | Symbolic chmod (e.g. `ugo+rw`) |
| setproctitle | Set process name in `ps` output |
| protobuf / grpcio | Protocol buffer serialization |

Several dependencies that were previously used (`linux-utils`,
`oslo.concurrency`, `shakenfist-utilities`) have been replaced
with lightweight inline implementations to reduce install size
and improve compatibility with newer Python versions.
