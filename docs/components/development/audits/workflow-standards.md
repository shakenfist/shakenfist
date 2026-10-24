# Audit: Workflow standards

## What we check

These are the standards every workflow file is held to. Several
checks measure parts of them, and each is a column in this
criterion's section of the compliance page; the rest are not read by
any check. Each rule below says which it is:

* **Measured** names the check, and what it reads.
* **Reviewed, not measured** is a rule no check reads. It holds as
  far as review of the change holds it, and a repository breaking it
  passes this criterion regardless.

A rule is not measured because it is written here, so the labels are
the place to look before relying on a green cell.

### Workflow permissions

**Measured** by `workflow-permissions`. Every workflow needs a
top-level `permissions:` block restricting the default `GITHUB_TOKEN`
scope; GitHub Advanced Security flags its absence. The check reads
each workflow for a line beginning `permissions:` at column zero.

**Reviewed, not measured:** what the block grants. Read-only
workflows use `permissions: contents: read`; mixed-need workflows use
`permissions: {}` at the top with per-job overrides. The check passes
any top-level block, whatever its scopes.

### Workflow and job naming

**Reviewed, not measured.** Display names are English sentences with
correct capitalisation, not kebab case. Job **ids** may be
machine-friendly.

### Runner preferences

**Measured** by `self-hosted-runners`, except the last bullet.

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
* **Reviewed, not measured:** Claude Code automation jobs run on
  `claude-code` runners only. No check reads which pool a Claude Code
  job asks for.

### Static runners for small, non-mutating jobs

* **Reviewed, not measured:** jobs that do not change the state of
  the runner -- linting, metadata checks, audits, anything that
  installs no OS packages -- should use `runs-on: [self-hosted,
  static]`. Static runners are a pre-provisioned, long-lived pool and
  are much cheaper to start than a fresh VM. Reserve `vm` runners for
  jobs that genuinely need a throwaway machine.
* **Measured** by `static-runner-tags`: a static runner advertises
  exactly `self-hosted` and `static`, so a `static` job must request
  those two labels **only**. Adding a size (`s`, `l`), `vm`, or an OS
  (`debian-12`) asks for a runner that does not exist, and the job
  waits forever. The check flags any `runs-on:` combining `static`
  with additional labels.

### VM runners must name a size

**Measured** by `vm-runner-size`, including the bound-runner form at
the end of the list.

This is the exact complement of the rule above, and the two are easy to
confuse: a `static` job must name **no** size because the static pool
advertises none, and a `vm` job must name **one** because the conductor
otherwise picks for it.

* Sizes are `xs`, `s`, `m`, `l`, `xl`, the `m-bigdisk` /
  `xl-bigdisk` variants, and the `s-cloud` / `s-cluster` variants.
  `s-cloud` and `s-cluster` have the same footprint as `s` (2 vCPU,
  4096 MB) and are for jobs which build a nested test cloud from a
  small runner: `s-cloud` for a cloud on one node, `s-cluster` for a
  multi-node cluster. Every `vm` `runs-on` must name one.
* The conductor matches the size out of the labels, and falls back to
  the first entry in its `CI_SIZES` table when it finds none -- `xs`,
  one vCPU and 2048 MB. An omitted size is therefore a silent downgrade
  to the smallest runner, not a job left free to take any runner.
* `xs` counts as naming a size. The rule is that the size is a
  decision, not that it is a large one, so a job which genuinely wants
  the smallest runner says so and passes.
* The check reads the labels which are statically known, so a
  `runs-on:` pairing a matrix expression with a literal size passes.
  A job whose size could only arrive through an expression is a
  finding: the fleet writes the size literally even when the operating
  system comes from the matrix.
* A job which genuinely cannot name a size marks the line
  `audit-ok: vm-runner-size`, on the `runs-on:` itself or the line
  above it, with the reason. This is the fleet's usual escape hatch
  and it is here for consistency with the other runner checks, not
  because a case is known: writing `xs` costs one word and states the
  same decision honestly, so a marker whose reason amounts to "we did
  not choose" is the wrong answer.
* Only the inline-list (`[self-hosted, vm, l]`) and scalar forms of
  `runs-on:` are examined. A block sequence spread over the following
  lines is not matched, so a job written that way is neither passed
  nor flagged -- it is simply not seen. No repository in scope writes
  one today, and the sibling runner checks share the blind spot; this
  is recorded so the coverage claim is honest rather than assumed.

* A job may instead **bind itself to a runner started for it alone**:
  `[self-hosted, bound-debian-13, bound-s, "bind-${{ github.run_id }}-${{ github.run_attempt }}-<slug>"]`.
  This form names no `vm`, plain image or plain size, so that no
  unbound job can take the bound runner. It satisfies this rule when it
  names `self-hosted`, exactly one `bind-` element, exactly one
  `bound-<image>` and exactly one `bound-<size>` with a size from the
  list above, and nothing else. The `bind-` element is always an
  expression including `${{ github.run_id }}` and
  `${{ github.run_attempt }}`, so that every run and every re-run binds
  its own runners, and needs no marker. A `bound-${{ ... }}` element
  beside a literal `bound-<size>` is read as the image; a `bound-<size>` that is itself an
  expression is treated like any other expression size, and is the
  only bound finding the marker excuses. Any `bound-` or `bind-`
  element without the full form is a finding: `bound-` with no `bind-`,
  `bind-` with no `bound-` labels, a missing image or size, two sizes,
  an unknown bound size, or any other label alongside them -- `vm`, a
  plain image or size, or an expression that could evaluate to one. See
  "Binding a job to its runner" in shakenfist/private-ci
  `docs/capacity-claims.md`.

The two failures look nothing alike, which is why this needs its own
check. An over-labelled `static` job is never scheduled and someone
notices within one run. An under-labelled `vm` job runs perfectly well
on a machine nobody chose -- until it does not.
[shakenfist/shakenfist#3696](https://github.com/shakenfist/shakenfist/issues/3696)
is the case that prompted this: seven CI jobs building wheels and
driving ansible deploys, all on a 2048 MB runner with roughly 110 MB
free and no swap device, for months. The conductor's own sizing
recommender could not see it either, because both its upsize triggers
are swap-based and the image has no swap.

### Job timeouts

**Reviewed, not measured.** Every job must set `timeout-minutes`.
GitHub's default is 360 minutes, which is never right on a self-hosted
pool: a wedged job holds a runner nothing else can use until it
expires. This matters most on the `claude-code` pool, where runners
are few and an unattended Claude Code run has no natural upper bound
-- `--max-turns` caps turns, not wall clock.

Values used in the templates:

* Trigger / dispatch jobs making a few `gh` API calls (`pr-retest.yml`,
  the `trigger-*` job in `pr-fix-tests.yml`): 10 minutes.
* Claude Code triage (`issue-fix.yml`'s `triage`): 30 minutes.
* Claude Code review (`pr-re-review.yml`): 60 minutes. Real re-reviews
  finish in three to six minutes, so this is ten times headroom.
* Claude Code fix runs that build and test (`issue-fix.yml`'s `fix`,
  `test-drift-fix.yml`): 120 minutes.

### Functional test workflow naming

**Reviewed, not measured.** Functional testing lives in
`functional-tests.yml`, and must include `workflow_dispatch` as a
trigger -- `pr-retest.yml` dispatches it. A repository whose test
workflow has another name sets the `RETEST_WORKFLOW` repository
variable instead; see `templates/ci-review-automation/README.md`.

### PIPESTATUS for piped commands

**Reviewed, not measured.** When piping through `tee` in a `run:`
step, capture the upstream exit code with `${PIPESTATUS[0]}`:

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

**Measured** by `flake8wrap`, in part, where `tools/flake8wrap.sh`
exists. The script runs flake8 over the files changed in the current
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

The check reads the script for two things: that it names `SC2086`
somewhere, and that no line mentioning both `filtered_files` and
`diff` or flake8 quotes `"${filtered_files}"`.

**Reviewed, not measured:** that the directive's comment says the
splitting is intentional, and that the script filters to `.py`
files, skips `_pb2` generated files, and handles paths in the diff
that no longer exist on disk.

### CI linting

`actionlint`, `shellcheck`, and a `.pre-commit-config.yaml` that runs
them; `kerbside` and `kerbside-patches` are the worked examples. Helper
shell scripts should have shellcheck pre-commit hooks.

**Measured** by `pre-commit-config`, and only as far as the file
existing: the check passes any `.pre-commit-config.yaml`. **Reviewed,
not measured:** which hooks it runs, including whether actionlint and
shellcheck are among them.

### Review marks excluded from pre-commit

**Measured** by `review-marks-pre-commit`. Repositories with human
review tracking deployed (those carrying `.vscode/review-scope.toml`)
must exempt the weAudit state files from any pre-commit hook that
rewrites the files it is given:

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
`file-contents-sorter`) is configured, and requires every such hook
to be protected: by an `exclude:` on its own entry or by the top-level
`exclude:`, since an exclude on any other hook (gitleaks, say) does
nothing for it. An exclude protects the hook if, tried as the regex
pre-commit would apply, it matches both `.vscode/<user>.weaudit` and
`.vscode/<user>.weaudit-shas.json`. A YAML block scalar
(`exclude: |`) is read as the text it holds, not its indicator. A
value that does not compile, or whose search does not finish within a
few seconds, counts as no exclude: pre-commit would fail or hang on it
too. Repositories without the tooling, without a
`.pre-commit-config.yaml`, or running no rewriting hook are not
applicable.

### Review scope changes are reviewed

**Measured** by `review-scope-ci`. In repositories with human review
tracking deployed, a pull request that changes only
`.vscode/review-scope.toml` must still reach the automated reviewer.
The review state (`REVIEWS.md`, `.vscode/*.weaudit` and
`.vscode/*.weaudit-shas.json`) may skip CI, as step 8 of
[adopting a repository](/components/development/code-review-tracking/#adopting-a-repository)
describes. The scope config may not. It is policy: it decides which
files are subject to review at all, so a pull request that narrows it
needs the same lint and review as one that changes code.

The check reads every workflow that runs the automated reviewer on
`pull_request`, or that calls a reusable reviewer workflow by a local
path. That is also where the fleet runs lint. A workflow run only by
the merge queue reviews nothing before it, and does not count. A
workflow fails if a pull request changing only the scope config would
skip it, in any of three ways:

* the `pull_request` trigger's `paths-ignore` names it;
* the trigger's `paths` does not name it;
* a `dorny/paths-filter` job in the workflow excludes it with a `!`
  pattern.

Both lists are read as GitHub reads them: the last pattern that
matches wins, and a `!` pattern takes back what an earlier one named.

Patterns are matched with `**` crossing directories and `*` not.
Every `!` list item in a workflow that uses `dorny/paths-filter` counts
as a filter exclusion. So an inclusion-style filter (one that lists
`src/**` and excludes nothing) is not seen, and a `!` re-inclusion in
that workflow's trigger `paths-ignore` is misread as an exclusion. No
repository in the fleet has either shape. Repositories without review
tracking, or with no automated reviewer workflow, are not applicable.

**Reviewed, not measured:** the other workflows. The adoption
procedure drops the scope config from every pull request filter except
the CodeQL template's, which skips it along with `docs/**` because
CodeQL analyses neither. A workflow that runs only after a merge, such
as a canary on push to the default branch, may still skip it: by then
the change has been linted and reviewed.

### PyPI caching

**Reviewed, not measured:** self-hosted runners should use the devpi
PyPI cache at `http://192.168.1.15:3141`. No check asks whether a job
that installs packages uses it.

**Measured** by `devpi-fallback`: any job pointing pip at the cache
with `PIP_INDEX_URL` must set a pypi fallback in the **same** `env`
block:

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

**Measured** by `devpi-stale-ip`. The cache moved from `192.168.1.4`
to `192.168.1.15`. The old address no longer resolves to a running
server, so a job still pointing at it -- in `PIP_INDEX_URL`,
`PIP_TRUSTED_HOST`, or anywhere else -- fails every install. The check
flags any workflow line referencing `192.168.1.4`.

## Template

No single template -- these are standards applied across all workflow
files. See `templates/ci-review-automation/` for examples of correctly
structured workflows.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#workflow-standards).
