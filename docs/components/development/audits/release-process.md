# Audit: Release process

## What we check

* There is no `release.sh` in the project directory.
* All Python projects use `pyproject.toml` (not `requirements.txt`
  or `test-requirements.txt`).
* If `pyproject.toml` exists, there must be a
  `.github/workflows/release.yml` and a `RELEASE-SETUP.md`.
* Releases use GitHub signed tags and Sigstore signing.
* A release job which attaches assets downloads them to a named
  destination (`name:` or `merge-multiple: true`) and sets
  `fail_on_unmatched_files: true`, so that a glob matching nothing
  fails the job instead of publishing an empty release.
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
  directory.
* A job running a known container action (`pypa/gh-action-pypi-publish`)
  takes its paths relative to the workspace, and therefore checks out.
  The runner does mount its directories into the container, but not
  where the workflow expressions point: `RUNNER_TEMP` appears at
  `/github/runner_temp` while `${{ runner.temp }}` expands to the host
  path. An absolute path is rejected even inside the workspace, because
  host and container disagree about where that is. This criterion takes
  precedence over the one above, which is why such a job must check out
  rather than use `runner.temp`.

## Template

Template: `templates/release-automation/`
See: `templates/release-automation/README.md`
Docs: `docs/release-automation.md`

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#release-process).
