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

Pull requests to `develop` run `.github/workflows/functional-tests.yml`.
Its `sanity_checks` job (flake8, a dependency install check, unit tests
and coverage) runs on an ephemeral VM runner, so a `check_paths` job
skips it when a pull request changes nothing outside `docs/`. The
automated reviewer still runs on those pull requests. Commenting
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

None of these checks is configured as a required status check on
`develop` today, so a failing gitleaks or agent context job is visible
on the pull request but does not by itself block a merge; `can_merge`
in `functional-tests.yml` only aggregates that workflow's own jobs.

The same workflow runs [skillsaw](https://skillsaw.org/) over the agent
context (`AGENTS.md` and anything else an agent is handed) by running
its pre-commit hook. `.pre-commit-config.yaml` carries that hook
alongside actionlint and shellcheck, so run `pre-commit install` once
per clone and `pre-commit run --all-files` before proposing a change.

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
