# Audit: Workflow standards

## What we check

### Workflow permissions

Every workflow needs a top-level `permissions:` block restricting the
default `GITHUB_TOKEN` scope; GitHub Advanced Security flags its
absence. Read-only workflows use `permissions: contents: read`;
mixed-need workflows use `permissions: {}` at the top with per-job
overrides.

### Workflow and job naming

Display names are English sentences with correct capitalisation, not
kebab case. Job **ids** may be machine-friendly.

### Runner preferences

* Use `self-hosted` runners. GitHub-provided minutes are limited per
  month, so jobs must not leak onto GitHub-hosted runners without a
  documented reason.
* Legitimate exceptions -- Windows and macOS builds where we own no
  suitable hardware, as in ryll -- take an `audit-ok:
  github-hosted-runner` comment on, or immediately above, the line
  naming the label, ideally with a reason:

  ```yaml
      # audit-ok: github-hosted-runner -- no self-hosted macOS hardware
      runs-on: macos-latest
  ```

* The check flags any GitHub-hosted runner label (`ubuntu-latest`,
  `windows-2022`, `macos-15`, ...) without a marker, including matrix
  values feeding `runs-on: ${{ matrix.os }}`. It only counts labels
  where a runner can actually be named -- after `runs-on:`, inside a
  `[...]` list, or as a `- ` matrix item -- so the same text in an
  image label, a job name or an ssh command is not a finding.
* Claude Code automation jobs: `claude` runners only.

### Static runners for small, non-mutating jobs

* Jobs that do not change the state of the runner -- linting, metadata
  checks, audits, anything that installs no OS packages -- should use
  `runs-on: [self-hosted, static]`. Static runners are a
  pre-provisioned, long-lived pool and are much cheaper to start than a
  fresh VM. Reserve `vm` runners for jobs that genuinely need a
  throwaway machine.
* A static runner advertises exactly `self-hosted` and `static`, so a
  `static` job must request those two labels **only**. Adding a size
  (`s`, `l`), `vm`, or an OS (`debian-12`) asks for a runner that does
  not exist, and the job waits forever. The check flags any `runs-on:`
  combining `static` with additional labels.

### Job timeouts

Every job must set `timeout-minutes`. GitHub's default is 360 minutes,
which is never right on a self-hosted pool: a wedged job holds a runner
nothing else can use until it expires. This matters most on the
`claude-code` pool, where runners are few and an unattended Claude Code
run has no natural upper bound -- `--max-turns` caps turns, not wall
clock.

Values used in the templates:

* Trigger / dispatch jobs making a few `gh` API calls (`pr-retest.yml`,
  the `trigger-*` job in `pr-fix-tests.yml`): 10 minutes.
* Claude Code triage (`issue-fix.yml`'s `triage`): 30 minutes.
* Claude Code review (`pr-re-review.yml`): 60 minutes. Real re-reviews
  finish in three to six minutes, so this is ten times headroom.
* Claude Code fix runs that build and test (`issue-fix.yml`'s `fix`,
  `test-drift-fix.yml`): 120 minutes.

### Functional test workflow naming

Functional testing lives in `functional-test.yml`, and must include
`workflow_dispatch` as a trigger -- `pr-retest.yml` needs it.

### PIPESTATUS for piped commands

When piping through `tee` in a `run:` step, capture the upstream exit
code with `${PIPESTATUS[0]}`:

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

`$?` captures `tee`, which always succeeds, and `set -eo pipefail`
alone is unreliable on self-hosted runners. Exceptions: a pipeline
whose failure is deliberately ignored (`command | tee log.txt || true`)
and one whose upstream cannot fail (`echo ... | tee`).

### flake8wrap.sh correctness

`tools/flake8wrap.sh` runs flake8 over the files changed in the current
commit (via `tox -eflake8 -- -HEAD`), building a space-separated list
in `filtered_files`. That variable must **not** be quoted on the
diff/flake8 line: quoting makes the whole list one filename, which
breaks as soon as more than one Python file changed.

```sh
# Correct -- the word splitting is deliberate.
# shellcheck disable=SC2086
diff -u --from-file /dev/null ${filtered_files} | $FLAKE_COMMAND ${filtered_files}

# Wrong -- one argument named "a.py b.py".
diff -u --from-file /dev/null "${filtered_files}" | $FLAKE_COMMAND "${filtered_files}"
```

The `shellcheck disable=SC2086` directive is required, and its comment
should say the splitting is intentional. The script should also filter
to `.py` files, skip `_pb2` generated files, and handle paths in the
diff that no longer exist on disk.

### CI linting

`actionlint`, `shellcheck`, and a `.pre-commit-config.yaml` that runs
them; `kerbside` and `kerbside-patches` are the worked examples. Helper
shell scripts should have shellcheck pre-commit hooks.

### Review marks excluded from pre-commit

Repositories with human review tracking deployed (those carrying
`.vscode/review-scope.toml`) must exempt the weAudit state files from
any pre-commit hook that rewrites the files it is given:

```yaml
- id: end-of-file-fixer
  exclude: ^\.vscode/.*\.weaudit
```

Those files are generated without a trailing newline, so
`end-of-file-fixer` rewrites them on every `pre-commit run --all-files`
and reports a failure nobody can fix. Scope the exclude to the
rewriting hooks rather than the whole file --
[docs/code-review-tracking.md](/components/development/code-review-tracking/) explains why
a blanket exclude is worse than none.

The check applies only where a rewriting hook (`end-of-file-fixer`,
`trailing-whitespace`, `mixed-line-ending`, `pretty-format-json`,
`file-contents-sorter`) is configured. It tries each `exclude:` value
as the regex pre-commit would apply and passes if any one matches both
`.vscode/<user>.weaudit` and `.vscode/<user>.weaudit-shas.json`, so a
top-level exclude still counts. Repositories without the tooling,
without a `.pre-commit-config.yaml`, or running no rewriting hook are
not applicable.

### PyPI caching

Self-hosted runners should use the devpi PyPI cache at
`http://192.168.1.15:3141`. Any job pointing pip at it with
`PIP_INDEX_URL` must set a pypi fallback in the **same** `env` block:

```yaml
    env:
      PIP_INDEX_URL: http://192.168.1.15:3141/root/pypi/+simple/
      PIP_EXTRA_INDEX_URL: https://pypi.org/simple/
      PIP_TRUSTED_HOST: 192.168.1.15
```

devpi's `root/pypi` mirror is lazy: the first request for a package it
has never cached returns an empty index if the upstream fetch misses,
and pip reports `Could not find a version that satisfies the
requirement X (from versions: none)`. `PIP_INDEX_URL` replaces pypi
entirely, so without `PIP_EXTRA_INDEX_URL` there is no fallback. The
check flags any devpi-backed `env` block missing it.

### Retired devpi address

The cache moved from `192.168.1.4` to `192.168.1.15`. The old address
no longer resolves to a running server, so a job still pointing at it
-- in `PIP_INDEX_URL`, `PIP_TRUSTED_HOST`, or anywhere else -- fails
every install. The check flags any workflow line referencing
`192.168.1.4`.

## Template

No single template -- these are standards applied across all workflow
files. See `templates/ci-review-automation/` for examples of correctly
structured workflows.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

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
