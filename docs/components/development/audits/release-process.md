# Audit: Release process

## Where it applies

The criterion applies to a repository with a `pyproject.toml` or a
`.github/workflows/release.yml`, and to nothing else. Its rules come in
three sets with different scopes:

* The **packaging** rules need a `pyproject.toml`, because they are
  about how a Python project is laid out for release.
* The **workflow-safety** rules need a `release.yml`, because they are
  about what that workflow does. A Rust or TypeScript release signs and
  publishes the same way a Python one does, so hunkydory, ryll, instar
  and andris are measured by this set even though they have no
  `pyproject.toml` at the top of the tree.
* The **Rust** rules need a `release.yml` and a `Cargo.toml`, at the
  top of the tree or under `src/`. They hold the release to the Rust
  template's invariants, listed [below](#the-rust-invariants).

## What we check

The packaging rules, where there is a `pyproject.toml`:

* There is no `release.sh` in the project directory.
* All Python projects use `pyproject.toml` (not `requirements.txt`
  or `test-requirements.txt`).
* There must be a `.github/workflows/release.yml` and a
  `RELEASE-SETUP.md`.
* Releases use GitHub signed tags and Sigstore signing.

The workflow-safety rules, where there is a `release.yml`:

* A release job which attaches assets downloads them to a named
  destination (`name:` or `merge-multiple: true`) and sets
  `fail_on_unmatched_files: true`, so that a glob matching nothing
  fails the job instead of publishing an empty release. A quoted
  `"true"` is the same value: an action receives every input as a
  string.
* Where `release.yml` offers `workflow_dispatch`, its publishing jobs
  are confined to a pushed tag with
  `if: github.event_name == 'push' && startsWith(github.ref,
  'refs/tags/v')`. A dispatch aimed at a branch would otherwise have
  `sign-tag` force-push a `refs/tags/refs/heads/<branch>` tag; one aimed
  at an existing tag would re-sign and force-push it. Build jobs are
  exempt: running them is what the dispatch is for.
* A job which downloads artifacts and does not start from a cleaned
  checkout downloads into `${{ runner.temp }}`. Such a job inherits
  whatever the previous job left on that runner, and
  `download-artifact` adds to a directory rather than replacing it. A
  checkout earns the exemption only when it cleans, so `clean: false`
  does not count. `packages-dir` is not judged: the PyPI publish runs
  in a container that sees only the workspace, so that input must be
  relative.
* The steps reading the distribution (`subject-path` on the attestation,
  `files` on the release) name the directory the download actually
  filled. Moving one and not the other leaves a step globbing an empty
  directory. A glob is compared by the directory it reads, so
  `dist/*.tar.gz` agrees with a download to `dist/`. A block scalar
  (`files: |`, one path per line) is not read, and so is not judged.
* A job running a known container action (`pypa/gh-action-pypi-publish`)
  takes its paths relative to the workspace, and therefore checks out.
  The runner does mount its directories into the container, but not
  where the workflow expressions point: `RUNNER_TEMP` appears at
  `/github/runner_temp` while `${{ runner.temp }}` expands to the host
  path. An absolute path is rejected even inside the workspace, because
  host and container disagree about where that is. This criterion takes
  precedence over the one above, which is why such a job must check out
  rather than use `runner.temp`.
* **A release waits for CI.** Exactly one job runs
  `shakenfist/actions/require-ci-success`, which fails unless the
  named CI workflows succeeded on the tagged commit.
  - **The gate's failure is recorded as one.** The gate job has no
    `continue-on-error`, and neither has the gate step: either records
    a failed gate as a success, so the jobs waiting for it run anyway.
    The gate step has no `if:`, which could skip it while the job
    succeeds; the job's own `if:` is where the gate is confined to a
    pushed tag. Any value fails, `false` included, because the
    template has neither key and leaving them out is the one spelling
    that needs no reading.
  - **The gate checks the commit being released.** The gate step
    passes no `sha:`. Its default is the commit the workflow runs on,
    which is the only right answer in a release, so even an explicit
    `${{ github.sha }}` fails: allowing that one value would mean
    deciding which spellings of it are the same expression, and a
    mistake there lets the gate approve one commit while the release
    builds another.
  - **Everything that acts outside the run waits for the gate.** Every
    job that names an environment, attaches a GitHub release, or holds
    any write scope (any access but `read` or `none`, and `write-all`)
    is downstream of the gate through `needs:`. An environment's
    reviewer checks that a release is wanted, not that it was tested;
    `github-release` and a job that pushes an image with
    `packages: write` have no reviewer at all.
  - **A failed gate is not let through.** Being downstream is not
    enough when a job's `if:` calls a status function: `success()`,
    `failure()`, `cancelled()` or `always()`, in any case and with or
    without `!`. Any one of them replaces the implicit `success()`
    GitHub otherwise adds, and `(success() || failure())` runs after a
    failed gate as surely as `always()` does. So every job between the
    gate and a job that must wait for it, and calling one, also
    requires `needs.<job>.result == 'success'` for each job it needs.
  - The template's `build` guard also lets a skipped gate through, so
    that a `workflow_dispatch` dry run still builds. That is accepted
    only where it cannot reach a release. The condition must also say
    `github.event_name != 'push'`, and every job below it that must
    wait for the gate must be confined to a pushed tag. A pushed tag
    is the one event on which the gate always runs.

  The conditions are not parsed as boolean expressions, so these
  clauses are looked for rather than proved to be joined by `&&`.
  Strings are compared without regard to case, as GitHub compares
  them: `needs.ci-gate.result == 'Skipped'` admits a skipped gate, and
  `== 'SUCCESS'` is the success clause.
  A `needs:` that names a job which does not exist, or a set of jobs
  that need each other in a loop, is reported as a finding. GitHub
  will not run such a workflow, and which jobs wait for CI cannot be
  read from it.
* **The workflows the gate names can give it a run.** Each file in the
  gate's `workflows:` input exists under `.github/workflows/`. Each
  triggers on `merge_group`, or on a push that reaches `develop`, the
  two events whose runs the gate counts. Either trigger may be written
  as a block key, in a flow sequence (`on: [push]`) or as a bare scalar
  (`on: push`); the last two carry no branch filter, so a push written
  that way reaches `develop`. A workflow on neither has no
  run on any merged commit, so every release fails at the gate, after
  the tag is pushed.
  Each trigger brings its own way to pass a commit that was not
  tested, and the rule checks both:
  - **On a push**, a concurrency group keyed on `github.ref`, at the
    workflow or a job, puts every commit on `develop` in one group. A
    later run then cancels an earlier one, and a cancelled run is not
    a success. `cancel-in-progress: false` does not prevent this: a
    group holds one running and one pending run, so a third arrival
    cancels the pending one. The group has to branch on
    `github.event_name == 'push'` and key by sha there. The rule
    reads the group as text: it passes one that mentions `github.ref`
    only if it also names both the push event and `github.sha`, and it
    does not check how the expression combines them. The key the
    merge queue needs falls back to `github.ref`, so on a push
    workflow it fails here. How merge-group runs cancel is
    [merge-group-cancellation](/components/development/audits/merge-group-cancellation/)'s to
    judge, and is not repeated here.
  - **On a merge group**, every `dorny/paths-filter` step in a
    workflow triggered by `merge_group` either passes a `base:` that
    references `github.event.merge_group.base_sha`, or is confined to
    `github.event_name == 'pull_request'` by its own `if:` or its
    job's. A filter that diffs a merge group against the branch name
    finds no changes on a re-run after the entry has merged. It then
    passes untested, and the gate counts that run.
* **Every wheel a release uploads has been through `check-dist`.**
  `shakenfist/actions/check-dist` fails a wheel with a member under a
  directory named `tests` or `*_tests`. The release copy is the one
  that stops a bad wheel, because it checks the artefact that is
  uploaded. A job handles wheels if it builds with `python -m build`
  or `PyO3/maturin-action`, or uploads a path naming `*.whl`. In such
  a job, a `check-dist` step comes after every build step it
  recognises and before every `actions/upload-artifact` step. Its
  `path:` (`dist/` by default) names the directory each upload reads,
  compared as the distribution paths above are.

Each rule recognises an action by its `uses:` line, with or without a
trailing comment. Renovate pins a digest and records the release after
it (`uses: owner/action@<sha>  # v3.0.0`), and that step is the same
action as one pinned to the tag.

What these rules cannot see:

* **A named workflow whose runs conclude `skipped`.** The gate counts
  only a run that concluded `success`. So a workflow whose jobs all
  skip on a merge group fails every release, even though it triggers
  correctly. kerbside's `direct-qemu-functional.yml` does this on
  every merge group, which is why kerbside's gate names
  `functional-tests.yml` alone. Whether a run skips is decided by its
  jobs' conditions at run time, and the trigger rule does not read
  that.
* **A path filter written in shell.** The merge-group rule reads
  `dorny/paths-filter` steps only. A script that runs `git diff` is
  not told apart from any other script, and no gated workflow in the
  fleet has one.
* **A wheel job that builds through a script and uploads a bare
  directory.** It shows neither signal the `check-dist` rule looks
  for, so the rule does not judge it. ryll and andris build through
  scripts but upload `*.whl`, so they are judged; nothing in the fleet
  has the other shape today.

## The Rust invariants

[`templates/release-automation-rust/README.md`](https://github.com/shakenfist/development/blob/main/templates/release-automation-rust/README.md#invariants)
lists, under "Invariants", what every Rust release keeps. It is the
authority: the rules read the push and tag guard and the skipped-gate
guard as exact strings, and the build-job guard as exactly its clauses
in any order (see below), so a guard spelled any other way fails, even
where it would be safe. A respelling is fixed in the repository, or in
the README and then the rule, never by loosening the rule alone.

Each invariant is checked once. Where a workflow-safety rule above
already checks part of one, the Rust rule checks only the rest. It
also stays silent about a job whose `if:` or `needs:` that rule has
already reported, so that a defect is one finding. The table maps each
invariant to the functions in `scripts/audit/checks/release_rust.py`
and `scripts/audit/checks/release.py` that check it, and
`ReleaseRustInvariantTableTest` fails when the README gains an
invariant the table does not list.

| Invariant | Checked by |
|-----------|------------|
| 1 | `rust_trigger_issues` |
| 2 | `rust_permissions_issues` |
| 3 | `rust_concurrency_issues` |
| 4 | `rust_ci_gate_issues`; one gate job, and no `continue-on-error`, step `if:` or `sha:` on it, `release_ci_gate_issues`; the named workflows exist, `release_ci_trigger_issues` |
| 5 | `rust_check_version_issues`, `rust_build_root_issues`; waiting for the gate, `release_ci_gate_issues` |
| 6 | `rust_build_guard_issues`; a status override without the success clauses, `release_ci_gate_issues` |
| 7 | `rust_effect_guard_issues`; a publishing job a dispatch reaches, `release_dispatch_guard_issues` |
| 8 | `rust_sign_tag_issues`; its guard, `rust_effect_guard_issues` |
| 9 | `rust_upload_needs_issues` |
| 10 | `rust_sign_tag_needs_issues`; waiting for the gate, `release_ci_gate_issues` |
| 11 | `rust_write_permission_issues` |
| 12 | `rust_github_release_issues`; its guard, `rust_effect_guard_issues`; its need for `sign-tag`, `rust_sign_tag_needs_issues`; the download and `fail_on_unmatched_files`, `release_asset_issues` and `release_workspace_issues` |
| 13 | `release_check_dist_issues`; the `@main` version, `rust_check_dist_ref_issues` |
| 14 | `rust_script_issues` |
| 15 | `rust_git_tag_issues` |

How the rules read what the README leaves open:

* **"Needs" is transitive**, as the README defines it: a job needs
  another if it lists it, or lists a job that does.
* **The build-job guard's clauses may come in any order.** The README
  fixes which clauses there are, not their order, and the order changes
  nothing GitHub evaluates.
* **"No `branches`" on the push trigger** rules out `branches-ignore`
  as well. Either one makes a push to a branch start a run.
* **A job with an effect outside the run** (invariant 7) names an
  environment, holds any write scope (`contents: write`,
  `id-token: write`, `packages: write`, any access but `read` or
  `none`, or `write-all`), or reads a secret other than `GITHUB_TOKEN`,
  including through `secrets['...']` or `secrets: inherit`. Such a job
  needs `sign-tag`, and so does `github-release`. Needing `sign-tag`
  covers invariant 5's need for `check-version` too, so a job that
  needs neither is told about `sign-tag` alone.
* **Executable** (invariant 14) is the owner's execute bit in the
  checkout, which a clone takes from the mode git records.
* **The per-repository section** of `propose-release.sh` and
  `release-version.sh` is the lines strictly between its two markers.
  The marker lines themselves are compared with the template's, and a
  copy without exactly one of each fails. The template's own
  `release-version.sh` keeps its `{{CARGO_TOML}}`, `{{CARGO_LOCK}}` and
  `{{CRATE_NAME}}` placeholders in that section, so a copy nobody
  filled in fails on its `{{`.
* **A `git tag` command** (invariant 15) is `git tag`, or `git -C <dir>
  tag` and the like, outside a comment, in a tracked `Makefile` (or
  `GNUmakefile` or `makefile`), a tracked `*.sh`, or a workflow under
  `.github/workflows/`. Listing the tracked files needs git, so a
  checkout git cannot list reports `error` rather than a verdict.

If this repository's own copy of a template script cannot be read, the
criterion reports `error`: the audit's tree has failed, not the
repository's.

## Template

Template: `templates/release-automation/`
See: `templates/release-automation/README.md`
Rust template: `templates/release-automation-rust/`, whose README holds
the invariants
Docs: `docs/release-automation.md`

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#release-process).
