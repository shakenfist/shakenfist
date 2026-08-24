# Audit: Workflow standards

## What we check

### Workflow permissions

All GitHub Actions workflows must have a top-level `permissions`
block to restrict the default `GITHUB_TOKEN` scope. This is flagged
by GitHub Advanced Security if missing.

* Read-only workflows: `permissions: contents: read`.
* Mixed-need workflows: `permissions: {}` at top level with per-job
  overrides.

### Workflow and job naming

* Display names should be English sentences with correct
  capitalisation. No kebab case.
* The **id** of the job can be machine-friendly.

### Runner preferences

* Use `self-hosted` runners except under exceptional circumstances.
  GitHub-provided runner minutes are limited per month, so jobs must
  not leak onto GitHub-hosted runners without a documented reason.
* Legitimate exceptions (e.g. Windows and macOS builds where we own
  no suitable hardware, as in ryll) must be marked with an
  `audit-ok: github-hosted-runner` comment on -- or immediately
  above -- the line referencing the GitHub-hosted label, ideally
  with a reason:

  ```yaml
      # audit-ok: github-hosted-runner -- no self-hosted macOS hardware
      runs-on: macos-latest
  ```

* The automated check flags any workflow line referencing a
  GitHub-hosted runner label (`ubuntu-latest`, `windows-2022`,
  `macos-15`, etc.) without an exception marker, including matrix
  values that feed `runs-on: ${{ matrix.os }}`.
* The label must sit where a runner can actually be named: after
  `runs-on:`, as an element of a `[...]` list, or as a `- ` matrix
  item. The same text elsewhere is not a runner reference, and used
  to be reported as one -- a Shaken Fist image label
  (`sf://label/ci-images/ubuntu-2404`), a job name
  (`ubuntu-2404-slim-primary`) and an artifact name passed inside an
  ssh command all matched. Those findings could not be actioned: the
  only remedy the message offers is an `audit-ok` marker, which would
  have been a false statement about a line that never described a
  runner.
* Claude Code automation jobs: `claude` runners only.

### Static runners for small, non-mutating jobs

* Small jobs that do not change the state of the runner -- linting,
  metadata checks, audits, anything that does not install OS packages
  or otherwise mutate the machine -- should run on a `static` runner
  (`runs-on: [self-hosted, static]`). Static runners are a
  pre-provisioned, long-lived pool, so they are much cheaper to start
  than a fresh VM runner. Reserve `vm` runners for jobs that genuinely
  need a throwaway machine (for example because they install packages
  or need root that would dirty the host).
* A static runner advertises exactly the `self-hosted` and `static`
  labels. A job that requests a `static` runner must therefore ask for
  those two labels **only** -- `runs-on: [self-hosted, static]`.
  Adding an impossible extra label such as a size (`s`, `l`), `vm`, or
  an operating system (`debian-12`) asks for a runner that does not
  exist, so the job is never scheduled and waits forever. The
  automated check flags any `runs-on:` that combines `static` with
  additional labels.

### Job timeouts

Every job must set `timeout-minutes`. GitHub's default is 360
minutes (six hours), which is never the right answer on a
self-hosted pool: a wedged job holds a runner that nothing else can
use until it expires. This matters most for jobs on the
`claude-code` pool, where the runners are few and an unattended
Claude Code run has no natural upper bound of its own --
`--max-turns` caps turns, not wall clock.

Rules of thumb, matching the values used in the templates:

* Trigger / dispatch jobs that only make a few `gh` API calls
  (`pr-retest.yml`, and the `trigger-*` job in `pr-fix-tests.yml`):
  10 minutes.
* Claude Code triage runs (`issue-fix.yml`'s `triage` job): 30
  minutes.
* Claude Code review runs (`pr-re-review.yml`): 60 minutes. Real
  re-reviews finish in three to six minutes, so this is ten times
  headroom.
* Claude Code fix runs that build and test (`issue-fix.yml`'s `fix`
  job, `test-drift-fix.yml`): 120 minutes.

### Functional test workflow naming

* Functional testing must be in `functional-test.yml`.
* Must include `workflow_dispatch` as a trigger (needed by
  `pr-retest.yml`).

### PIPESTATUS for piped commands

When piping through `tee` in a `run:` step, always use the
`${PIPESTATUS[0]}` pattern to capture the upstream exit code:

```yaml
- name: Run something
  run: |
    set +e
    make something 2>&1 | tee output.txt
    exit_code=${PIPESTATUS[0]}
    set -e
    if [ ${exit_code} -ne 0 ]; then
      echo "Command failed with exit code ${exit_code}"
      exit ${exit_code}
    fi
```

Do not rely on `$?` (it captures the last command in the pipeline,
which is `tee`, which always succeeds) or on `set -eo pipefail`
alone (unreliable on self-hosted runners). The exceptions are a
pipeline whose failure is deliberately ignored
(`command | tee log.txt || true`) and one whose upstream cannot fail
(`echo ... | tee`).

### flake8wrap.sh correctness

`tools/flake8wrap.sh` runs flake8 over just the files changed in the
current commit (via `tox -eflake8 -- -HEAD`), building a
space-separated list in `filtered_files`. That variable must **not**
be quoted on the diff/flake8 invocation line: quoting makes the whole
list a single filename argument, which breaks as soon as more than
one Python file changed.

```sh
# Correct -- the word splitting is deliberate.
# shellcheck disable=SC2086
diff -u --from-file /dev/null ${filtered_files} | $FLAKE_COMMAND ${filtered_files}

# Wrong -- one argument named "a.py b.py".
diff -u --from-file /dev/null "${filtered_files}" | $FLAKE_COMMAND "${filtered_files}"
```

The `shellcheck disable=SC2086` directive is required because
shellcheck otherwise flags that splitting; keep the comment saying it
is intentional. The script should also filter to `.py` files, skip
`_pb2` generated files, and handle deleted files (paths in the diff
that no longer exist on disk).

### CI linting

* `actionlint`, `shellcheck`, and `.pre-commit-config.yaml` that
  runs them. `kerbside` and `kerbside-patches` are the worked
  examples.
* Helper shell scripts should have shellcheck pre-commit hooks.

### Review marks excluded from pre-commit

Repositories with human review tracking deployed (those carrying
`.vscode/review-scope.toml` -- see
[docs/code-review-tracking.md](/components/development/code-review-tracking/))
must exempt the weAudit state files from any pre-commit hook that
rewrites the files it is given:

```yaml
- id: end-of-file-fixer
  exclude: ^\.vscode/.*\.weaudit
```

Those files are generated, and the generator emits no trailing
newline, so `end-of-file-fixer` rewrites them on every
`pre-commit run --all-files`. That reports a failure nobody can fix:
committing the newline only means the next regen drops it again, so
the hook warns over and over until people learn to ignore it, which
is the opposite of what a lint gate is for. Scope the exclude to the
rewriting hooks rather than the whole file --
[docs/code-review-tracking.md](/components/development/code-review-tracking/) explains
why a blanket exclude is worse than none.

The check therefore applies only where a rewriting hook
(`end-of-file-fixer`, `trailing-whitespace`, `mixed-line-ending`,
`pretty-format-json`, `file-contents-sorter`) is configured. It
tries each `exclude:` value as the regex pre-commit would apply and
passes if any one matches both `.vscode/<user>.weaudit` and
`.vscode/<user>.weaudit-shas.json`, so a top-level exclude still
counts where a repo has one. Repositories without the tooling,
without a `.pre-commit-config.yaml`, or running no rewriting hook
at all are not applicable.

### PyPI caching

Self-hosted runners should use the devpi PyPI cache at
`http://192.168.1.15:3141` to reduce network load.

Any job that points pip at the devpi cache with `PIP_INDEX_URL` must
also set a pypi fallback in the **same** `env` block:

```yaml
    env:
      PIP_INDEX_URL: http://192.168.1.15:3141/root/pypi/+simple/
      PIP_EXTRA_INDEX_URL: https://pypi.org/simple/
      PIP_TRUSTED_HOST: 192.168.1.15
```

devpi's `root/pypi` mirror is lazy: the first request for a package it
has never cached returns an empty index if the upstream fetch misses,
and pip then reports `Could not find a version that satisfies the
requirement X (from versions: none)` and the job fails. Because
`PIP_INDEX_URL` replaces pypi entirely, there is no fallback without
`PIP_EXTRA_INDEX_URL`; adding it lets pip fall back to pypi for that
cold-cache miss. The automated check flags any devpi-backed `env`
block missing `PIP_EXTRA_INDEX_URL`.

### Retired devpi address

The devpi cache used to live at `192.168.1.4` but moved to
`192.168.1.15` some time ago. The old address no longer resolves to a
running server, so a job that still points pip at `192.168.1.4` -- in
`PIP_INDEX_URL`, `PIP_TRUSTED_HOST`, or anywhere else -- fails every
install. The automated check flags any workflow line referencing the
retired `192.168.1.4` address; update it to `192.168.1.15`.

## Template

No single template -- these are standards applied across all
workflow files. See `templates/ci-review-automation/` for examples
of correctly structured workflows.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-24T07:04:16.593679+00:00

| Project | Permissions | Linting | Review marks | flake8wrap | Runners | Static tags | devpi fallback | devpi IP | Issue |
|---------|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| actions | compliant | compliant | compliant | N/A | compliant | compliant | N/A | compliant | - |
| agent-python | compliant | compliant | N/A | non-compliant | non-compliant | compliant | N/A | compliant | shakenfist/agent-python#105, shakenfist/agent-python#82 |
| client-python | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| client-python-k3s | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| clingwrap | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| cloudgood | N/A | compliant | N/A | N/A | N/A | N/A | N/A | N/A | - |
| development | compliant | compliant | N/A | N/A | compliant | compliant | N/A | compliant | - |
| divergulent | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| instar | compliant | compliant | N/A | N/A | compliant | compliant | N/A | compliant | - |
| kerbside | compliant | compliant | compliant | compliant | compliant | compliant | compliant | compliant | - |
| kerbside-patches | compliant | compliant | N/A | N/A | compliant | compliant | N/A | compliant | - |
| library-utilities | compliant | compliant | N/A | compliant | compliant | compliant | N/A | compliant | - |
| occystrap | compliant | compliant | N/A | non-compliant | compliant | compliant | N/A | compliant | shakenfist/occystrap#67 |
| private-ci | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | - |
| ryll | compliant | compliant | N/A | N/A | compliant | compliant | N/A | compliant | - |
| sfui | compliant | compliant | N/A | N/A | compliant | compliant | compliant | compliant | - |
| shakenfist | compliant | compliant | N/A | non-compliant | compliant | compliant | non-compliant | compliant | shakenfist/shakenfist#3057, shakenfist/shakenfist#3418 |

Details for non-compliant projects:

- **agent-python** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **agent-python** (Runners): 2 unmarked GitHub-hosted runner reference(s): functional-tests.yml:103 (ubuntu-latest), functional-tests.yml:114 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **occystrap** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **shakenfist** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **shakenfist** (devpi fallback): 9 devpi-backed env block(s) missing a PIP_EXTRA_INDEX_URL pypi fallback: code-formatting.yml:27, codeql-analysis.yml:20, docs-tests.yml:19, functional-tests.yml:26, issue-fix.yml:102, publish-website.yml:17, release.yml:26, scheduled-tests.yml:24, test-drift-fix.yml:78. Add "PIP_EXTRA_INDEX_URL: https://pypi.org/simple/" alongside PIP_INDEX_URL so a devpi cold-cache miss (empty index for a first-touch package) falls back to pypi instead of failing with "from versions: none"
<!-- consistency-audit:end -->
