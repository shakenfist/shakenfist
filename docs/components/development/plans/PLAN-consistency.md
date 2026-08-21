# Shaken Fist Project Consistency Plan

This document audits each Shaken Fist project against the criteria in
`PROJECT-CONSISTENCY-AUDITS.md` and lists the cleanups needed.

## Excluded projects

Per the "Exceptional cases" section of `PROJECT-CONSISTENCY-AUDITS.md`,
the following projects are excluded from these rules as they are
internal-only tooling or historical archive repositories:

- **actions** -- shared GitHub composite actions and Ansible playbooks
- **ansible-modules** -- historical archive
- **client-js** -- historical archive
- **client-go** -- historical archive
- **client-python-ova** -- historical archive
- **deploy** -- internal tooling
- **development** -- internal tooling
- **images** -- internal tooling
- **imago-testdata** -- test data repository
- **imago-testdata-quarantine** -- test data repository (malware samples)
- **jenkins-private** -- internal CI tooling
- **loadtest** -- internal tooling
- **occystrap-testdata** -- test data repository
- **ostrich** -- historical archive
- **performance** -- internal tooling
- **private-ci** -- internal CI tooling (legacy setup.py project)
- **reproducables** -- historical archive
- **sonobouy** -- historical archive
- **symbolicmode** -- historical archive
- **terraform-provider-shakenfist** -- historical archive
- **uefi-latency-guest** -- internal tooling
- **website** -- internal tooling

## Audit criteria

1. **LLM tooling**: `AGENTS.md`, `ARCHITECTURE.md`, and Claude skills
   for repetitive operations.
2. **Release process**: No `release.sh`, no `requirements.txt` /
   `test-requirements.txt`. Use `pyproject.toml`. If `pyproject.toml`
   exists, need `.github/workflows/release.yml` and `RELEASE-SETUP.md`.
   Use the templates in `templates/release-automation/` as the starting
   point.
3. **Claude Code automated review in CI**: Automated review job in CI
   workflow (runs after all other tests pass). Must use the shared
   action `shakenfist/actions/review-pr-with-claude@main` (not
   per-project scripts). The reviewer job needs `pull-requests: write`
   and `issues: write` permissions to post comments and create issues.
   Also need `.github/workflows/pr-re-review.yml` (with
   `pull-requests: write` and `issues: write`).
4. **Renovate**: `.github/workflows/renovate.yml` and `renovate.json`.
5. **Repo config export**: `.github/workflows/export-repo-config.yml`.
6. **GitHub CodeQL**: `.github/workflows/codeql-analysis.yml` for
   advanced security scanning. Reference:
   `occystrap/.github/workflows/codeql-analysis.yml`.
7. **Linting**: `actionlint`, `shellcheck`, and `.pre-commit-config.yaml`
   that runs them.
8. **Workflow permissions**: Every GitHub Actions workflow must have a
   top-level `permissions` block to restrict the default `GITHUB_TOKEN`
   scope. Workflows where every job only reads should use
   `permissions: contents: read`. Workflows with mixed needs should
   use `permissions: {}` at the top level and per-job overrides.
9. **Developer automation**: Workflow automations that respond to
   comments from authorized users. These include:
   - `pr-address-comments.yml` -- "@shakenfist-bot please address
     comments" triggers Claude Code to address review comments
   - `pr-re-review.yml` -- "@shakenfist-bot please re-review" triggers
     another automated review (already covered in criterion 3)
   - `pr-fix-tests.yml` + `test-drift-fix.yml` (optional) --
     "@shakenfist-bot please attempt to fix" triggers Claude Code
     to fix CI failures. Only suitable for projects with large test
     suites prone to drift (e.g. imago, occystrap). Use templates
     from `templates/test-drift-fix/`.
   Use templates from `templates/ci-review-automation/` for the
   core workflows.
10. **Workflow naming**: Workflow and job display names should be
    English sentences with correct capitalization (no kebab case).
    Prefer `self-hosted` runners; Claude Code jobs on `claude` runners;
    small non-mutating jobs on `self-hosted` `static` runners.
11. **flake8wrap.sh correctness**: Projects with `tools/flake8wrap.sh`
    must not quote `${filtered_files}` on the diff/flake8 invocation
    line. Quoting causes the space-separated list to be treated as a
    single argument, breaking flake8 when multiple files change. Add
    `shellcheck disable=SC2086` with an explanatory comment. The
    script should also filter to `.py` files, skip `_pb2` generated
    files, and handle deleted files.
12. **Pin indirect dependencies**: Python projects with `pyproject.toml`
    need `.github/workflows/pin-indirect-dependencies.yml`,
    `tools/pin-indirect-dependencies.sh`, and
    `# START_OF_INDIRECT_DEPS` / `# END_OF_INDIRECT_DEPS` markers in
    their `pyproject.toml` delimiting the reconciled block.
    Application projects (shakenfist, kerbside) put the markers in
    `[project] dependencies` and use the application template.
    Library projects (agent-python, client-python, clingwrap,
    occystrap, library-utilities) put the markers in
    `[project.optional-dependencies] pinned` and use the library
    template. See `templates/pin-indirect-dependencies/`.
13. **Human review coverage**: Projects with the whole-file human
    review tracking system deployed (`.vscode/review-scope.toml`
    present; currently only ryll) must have fewer than 5 in-scope
    files needing review, recomputed against HEAD by
    `scripts/review-tracking.py status`. See
    `audits/review-coverage.md`.

---

## DRY analysis: shared actions vs file copies

Before rolling these out, it is worth considering whether some of
these workflows should be consolidated into reusable workflows or
composite actions in the `actions/` repository.

### Candidates for reusable workflows

**`export-repo-config.yml`** (168 lines in shakenfist) -- Strong
candidate. The workflow is self-contained, uses `${{ github.repository }}`
which auto-resolves per repo, and needs no repo-specific parameters.
It could become a reusable workflow at
`actions/.github/workflows/export-repo-config.yml` that callers
invoke with:

```yaml
jobs:
  export-config:
    uses: shakenfist/actions/.github/workflows/export-repo-config.yml@main
    secrets: inherit
```

This eliminates 168 lines of duplication across 13 repos.

**`renovate.yml`** (20 lines) -- Marginal candidate. The only
difference per repo is `RENOVATE_AUTODISCOVER_FILTER`. Could become
a reusable workflow with an input parameter, but the file is so
small that copying is arguably simpler. Recommend: just copy it,
with each repo setting its own filter value.

### Candidates for composite actions

**PR review with Claude** -- The `tools/review-pr-with-claude.sh`
script (337 lines) is the biggest piece of duplicated logic. It is
currently in shakenfist's `tools/` directory, and every repo that
adds Claude review needs a copy. This could become a composite
action at `actions/review-pr-with-claude/action.yml` that:

1. Takes inputs: `pr-number`, `max-turns`, `force` (boolean)
2. Contains the review script inline or as a bundled script
3. Gets called from both the automated reviewer CI job and the
   `pr-re-review.yml` workflow

This would mean repos don't need their own copy of the 337-line
script. Their `pr-re-review.yml` would shrink to roughly:

```yaml
- name: Run automated reviewer
  uses: shakenfist/actions/review-pr-with-claude@main
  with:
    pr-number: ${{ github.event.issue.number }}
    force: true
```

### Recommend: just copy per repo

**`renovate.json`** -- Repo-specific dependency grouping rules.
Must be per-repo.

**`.pre-commit-config.yaml`** -- Could diverge per repo (e.g. Rust
repos need different hooks than Python repos). Keep per-repo.

**`pr-re-review.yml`** -- Even with a shared action for the review
logic, each repo still needs this workflow file to define the
trigger. It would be much simpler though (~20 lines vs 72).

**`pr-fix-tests.yml`** and **`pr-address-comments.yml`** -- These
developer automation workflows from imago should be moved to the
`actions/` repository as composite actions or reusable workflows,
then rolled out to all projects. This would standardize the bot
comment triggers across the organization.

**`codeql-analysis.yml`** -- Small workflow (~55 lines). CodeQL
auto-detects languages so the workflow is nearly identical across
repos. Simple copy.

### Recommendation

Build shared items in the `actions/` repository before rolling
out to individual projects:

1. **Reusable workflow**: `export-repo-config.yml` -- eliminates
   the most duplicated code (168 lines x 13 repos).
2. **Composite action**: `review-pr-with-claude` -- eliminates
   the 337-line review script from each repo and simplifies both
   the automated reviewer CI job and `pr-re-review.yml`.
3. **Developer automation actions**: Move `pr-fix-tests.yml` and
   `pr-address-comments.yml` logic from imago to shared actions,
   enabling consistent bot-triggered automations across all repos.

Then for each project, the per-repo files to add are:

- `renovate.yml` (20 lines, copy + edit filter)
- `renovate.json` (copy + edit package rules)
- `export-repo-config.yml` (3-5 lines calling reusable workflow)
- `pr-re-review.yml` (~20 lines calling shared action)
- `codeql-analysis.yml` (~55 lines, copy from occystrap)
- Automated reviewer job in CI (3-5 lines calling shared action)

### Tech debt: convert existing workflows to shared versions

The shared `export-repo-config` reusable workflow and
`review-pr-with-claude` composite action are now available in the
`actions/` repo. The shared action now produces structured JSON
reviews (validated against a schema), creates GitHub issues for
actionable items, and renders to markdown with embedded JSON for
the `address-comments` automation.

The following projects still have their own inline copies and
should be migrated to use the shared versions:

- ~~**shakenfist** -- has its own 168-line `export-repo-config.yml`
  and its own 337-line `tools/review-pr-with-claude.sh` plus inline
  `pr-re-review.yml`. These should be replaced with calls to the
  shared action/workflow.~~
  DONE -- migrated to shared action/workflow.
  `export-repo-config.yml` now calls the shared reusable workflow,
  `pr-re-review.yml` and `functional-tests.yml` reviewer job now use
  `shakenfist/actions/review-pr-with-claude@main`, and
  `tools/review-pr-with-claude.sh` has been removed.
- ~~**imago** -- calls its own `tools/review-pr-with-claude.sh`
  directly from `functional-tests.yml` and `pr-re-review.yml`.~~
  DONE -- migrated to shared action. The per-project
  `tools/review-pr-with-claude.sh`, `tools/render-review.py`,
  `tools/create-review-issues.py`, and `tools/review-schema.json`
  can be removed in a future cleanup.
- **kerbside-patches** -- has Claude review logic in
  `daily-rebase-checks.yml`. Evaluate whether this can use the
  shared action.

This migration is not blocking but should be done to avoid drift
between the shared and inline copies. Projects using the shared
action also need `issues: write` permission on their reviewer job
(for issue creation).

### Template bugs found during occystrap audit

- ~~**`templates/export-repo-config/export-repo-config.yml`** has
  `permissions: contents: read` but the reusable workflow it calls
  creates branches, pushes commits, and creates PRs -- it needs
  `contents: write` and `pull-requests: write`.~~ FIXED.

- **`actions/review-pr-with-claude`** shared action had a simple
  plain-markdown prompt instead of the structured JSON format used
  in per-project scripts (imago, occystrap). The shared action has
  now been updated to include `render-review.py`,
  `create-review-issues.py`, and `review-schema.json`, producing
  structured reviews with issue creation and embedded JSON for
  automation. FIXED.

---

## Per-project audit results

### agent-python

Python package with `pyproject.toml`. Has `functional-tests.yml` CI
workflow with Claude Code review integration and developer
automation.

**Needed cleanups:**

- [x] Add `AGENTS.md`
- [x] Add `ARCHITECTURE.md`
- [x] Remove `release.sh`
- [x] Add `.github/workflows/release.yml` (has `pyproject.toml`)
- [x] Add `RELEASE-SETUP.md`
- [x] Add Claude Code automated review to CI workflow
- [x] Add `.github/workflows/pr-re-review.yml`
- [x] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [x] Add `.github/workflows/renovate.yml` and `renovate.json`
- [x] Add `.github/workflows/export-repo-config.yml`
- [x] Add `.github/workflows/codeql-analysis.yml`
- [x] Add `.pre-commit-config.yaml` with `actionlint` and `shellcheck`
- [x] Add top-level `permissions` to `functional-tests.yml`
- [ ] Add `.github/workflows/pin-indirect-dependencies.yml` (library
      template)
- [ ] Add `[project.optional-dependencies] pinned` section with
      `# END_OF_INDIRECT_DEPS` marker to `pyproject.toml`

**Not applicable:** `pr-fix-tests.yml` / `test-drift-fix.yml`
(small test suite, not prone to drift).

**Note:** `renovate.json` includes `constraints.python: ">=3.8"`
matching the oldest supported distro (Ubuntu 20.04). See
`ARCHITECTURE.md` for the supported platforms table.

**flake8wrap.sh:** Has script with correct unquoted variable
expansion. Missing `shellcheck disable=SC2086` directive.

### client-python

Python package with `pyproject.toml`. Has `functional-tests.yml` and
`code-formatting.yml` CI workflows but no Claude Code review
integration.

**Needed cleanups:**

- [ ] Add `AGENTS.md`
- [ ] Add `ARCHITECTURE.md`
- [ ] Remove `release.sh`
- [ ] Add `.github/workflows/release.yml` (has `pyproject.toml`)
- [ ] Add `RELEASE-SETUP.md`
- [ ] Add Claude Code automated review to CI workflow
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`
- [ ] Add `.pre-commit-config.yaml` with `actionlint` and `shellcheck`
- [ ] Add top-level `permissions` to `code-formatting.yml`
- [ ] Add top-level `permissions` to `functional-tests.yml`
- [ ] Add `.github/workflows/pin-indirect-dependencies.yml` (library
      template)
- [ ] Add `[project.optional-dependencies] pinned` section with
      `# END_OF_INDIRECT_DEPS` marker to `pyproject.toml`

**flake8wrap.sh:** Has script with correct unquoted variable
expansion. Missing `shellcheck disable=SC2086` directive.

### clingwrap

Python package with `pyproject.toml`. Has `AGENTS.md` and
`ARCHITECTURE.md` already. Has `functional-tests.yml` CI workflow
with Claude Code review integration and full developer automation.

**Needed cleanups:**

- [x] Remove `release.sh`
- [x] Add `.github/workflows/release.yml` (has `pyproject.toml`)
- [x] Add `RELEASE-SETUP.md`
- [x] Add Claude Code automated review to CI workflow
- [x] Add `.github/workflows/pr-re-review.yml`
- [x] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [x] Add `.github/workflows/pr-retest.yml` (developer automation)
- [x] Add `.github/workflows/renovate.yml` and `renovate.json`
- [x] Add `.github/workflows/export-repo-config.yml`
- [x] Add `.github/workflows/codeql-analysis.yml`
- [x] Add `.pre-commit-config.yaml` with `actionlint` and `shellcheck`
- [x] Add top-level `permissions` to `functional-tests.yml`
- [x] Update `actions/checkout` from v4 to v6
- [x] Add `check-bot-commit` job to `functional-tests.yml`
- [x] Add `tools/address-comments-with-claude.sh`,
  `tools/render-review.py`, `tools/create-review-issues.py`,
  `tools/review-schema.json` (supporting tools for developer automation)
- [x] Fix shellcheck warnings in `tools/flake8wrap.sh`
- [x] Add `.github/actionlint.yaml` configuration
- [x] Add `constraints.python` to `renovate.json` matching
  `requires-python = ">=3.7"` in `pyproject.toml`
- [x] Fix `tools/flake8wrap.sh` quoting bug (`"${filtered_files}"`
  was quoted, breaking flake8 with multiple files). Added
  `shellcheck disable=SC2086` with explanatory comment.

- [ ] Add `.github/workflows/pin-indirect-dependencies.yml` (library
      template)
- [ ] Add `[project.optional-dependencies] pinned` section with
      `# END_OF_INDIRECT_DEPS` marker to `pyproject.toml`

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`.

**Not applicable:** `pr-fix-tests.yml` / `test-drift-fix.yml`
(small test suite, not prone to drift).

### cloudgood

Documentation and examples project. Not a Python package. Has
`AGENTS.md`, `ARCHITECTURE.md`, and `.pre-commit-config.yaml`. No
`.github/workflows/` directory.

**Needed cleanups:**

- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`,
`.pre-commit-config.yaml`.

**Not applicable:** `pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`
(this is a documentation project, not a Python package). Claude Code
automated review in CI may not apply as there are no CI test
workflows. `codeql-analysis.yml` not applicable (no source code to
scan).

### imago

Rust project (Cargo.toml in `src/`). Has `AGENTS.md`,
`ARCHITECTURE.md`, `.claude/skills/` (5 skills),
`.pre-commit-config.yaml`, `pr-re-review.yml`, `pr-fix-tests.yml`,
`pr-address-comments.yml`, and Claude Code review in CI
(`functional-tests.yml`). Well-configured overall with full developer
automation.

**Needed cleanups:**

- [x] Add `.github/workflows/renovate.yml` and `renovate.json`
- [x] Add `.github/workflows/export-repo-config.yml`
- [x] Add `.github/workflows/codeql-analysis.yml`
- [x] Add top-level `permissions` to `pr-re-review.yml`
- [x] Add top-level `permissions` to `functional-tests.yml`
- [x] Add top-level `permissions` to `test-drift-fix.yml`
- [x] Migrate `functional-tests.yml` and `pr-re-review.yml` from
  per-project `tools/review-pr-with-claude.sh` to the shared action
  `shakenfist/actions/review-pr-with-claude@main`
- [x] Add `issues: write` to top-level permissions in
  `functional-tests.yml` (needed for the shared action's issue
  creation feature)

**Already compliant:** All criteria. `AGENTS.md`, `ARCHITECTURE.md`,
`.claude/skills/`, `.pre-commit-config.yaml`, `renovate.yml`,
`renovate.json`, `export-repo-config.yml`, `codeql-analysis.yml`,
`pr-re-review.yml`, `pr-fix-tests.yml`, `pr-address-comments.yml`,
`pr-retest.yml`, Claude Code automated review in CI (using shared
action). All workflows have top-level `permissions` blocks.

**Not applicable:** `pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`
(this is a Rust project, not a Python package released to pypi).

### kerbside-patches

Non-Python project containing CI patches. Has `AGENTS.md`,
`ARCHITECTURE.md`, `.claude/skills/` (4 skills),
`.pre-commit-config.yaml`. Has CI workflows including Claude Code
integration in `daily-rebase-checks.yml` for automated rebasing.
Missing developer automation workflows.

**Needed cleanups:**

- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`
- [ ] Add top-level `permissions` to `auto-retry-infra-failures.yml`
- [ ] Add top-level `permissions` to `daily-rebase-checks.yml`
- [ ] Add top-level `permissions` to `functional-tests.yml`
- [ ] Add top-level `permissions` to `local-container-builds.yml`
- [ ] Add top-level `permissions` to `rebase-tests.yml`

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`,
`.claude/skills/`, `.pre-commit-config.yaml`, Claude Code
integration (for rebasing).

**Not applicable:** `pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`
(this is not a Python package).

### kerbside

Python package with `pyproject.toml`. Has `AGENTS.md`,
`ARCHITECTURE.md`, `release.yml`, and `RELEASE-SETUP.md`. One of
the reference projects mentioned in the audit document.

**Needed cleanups:**

- [x] Add Claude Code automated review to CI workflow
- [x] Add `.github/workflows/pr-re-review.yml`
- [x] Add `.github/workflows/pr-retest.yml` (developer automation)
- [x] Add `.github/workflows/pr-address-comments.yml`
      (developer automation)
- [x] Add `.github/workflows/renovate.yml` and `renovate.json`
- [x] Add `.github/workflows/export-repo-config.yml`
- [x] Add `.github/workflows/codeql-analysis.yml`
- [x] Add `.pre-commit-config.yaml` with `actionlint` and
      `shellcheck`
- [x] Add top-level `permissions` to `functional-tests.yml`
- [x] Add top-level `permissions` to
      `pin-indirect-dependencies.yml`
- [x] Add top-level `permissions` to `release.yml`

**Additional items addressed:**

- [x] Fix `pin-indirect-dependencies.yml` for `pyproject.toml`
      (was still referencing `requirements.txt`)
- [x] Add `# END_OF_INDIRECT_DEPS` marker to `pyproject.toml`
- [x] Pin indirect dependencies workflow and marker (criterion 12)
- [x] Add `check-bot-commit` job to `functional-tests.yml`
- [x] Add `tools/address-comments-with-claude.sh`,
      `tools/render-review.py`, `tools/review-schema.json`
- [x] Add `.github/actionlint.yaml` configuration
- [x] Add devpi pypi cache configuration to CI workflows
- [x] Fix `setup_console()` logging in
      `kerbside/utilities/main.py` (basicConfig + propagate)
- [x] Update action versions to v6 (checkout, upload-artifact,
      download-artifact)

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`,
`pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`.

**flake8wrap.sh:** Has script with correct unquoted variable
expansion. Uses `egrep` (deprecated, should be `grep -E`) and
`"x$1" = "x-HEAD"` (old sh compatibility, harmless). Missing
`shellcheck disable=SC2086` directive. Does not filter deleted
files (no existence check loop).

**Status:** Fully compliant as of 2026-02-22. Remaining item:
enable Dependabot and secret scanning in GitHub repo settings
(UI-only, not tracked here).

### library-utilities

Python package with `pyproject.toml`. No `.github/workflows/`
directory at all. Has `release.sh` that should be removed.

**Needed cleanups:**

- [ ] Add `AGENTS.md`
- [ ] Add `ARCHITECTURE.md`
- [ ] Remove `release.sh`
- [ ] Add `.github/workflows/release.yml` (has `pyproject.toml`)
- [ ] Add `RELEASE-SETUP.md`
- [ ] Add CI workflow with Claude Code automated review
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`
- [ ] Add `.pre-commit-config.yaml` with `actionlint` and `shellcheck`
- [ ] Add `.github/workflows/pin-indirect-dependencies.yml` (library
      template)
- [ ] Add `[project.optional-dependencies] pinned` section with
      `# END_OF_INDIRECT_DEPS` marker to `pyproject.toml`

**flake8wrap.sh:** Has script with correct unquoted variable
expansion. Uses simpler pattern (`tr '\n' ' '`) without `.py`
filtering, `_pb2` exclusion, or deleted file handling.

### occystrap

Python package with `pyproject.toml`. Has `AGENTS.md`,
`ARCHITECTURE.md`, `release.yml`, `RELEASE-SETUP.md`,
`.pre-commit-config.yaml`, `renovate.yml`, `renovate.json`,
`export-repo-config.yml`, `codeql-analysis.yml`, `pr-re-review.yml`,
`pr-retest.yml`, `pr-fix-tests.yml`, `pr-address-comments.yml`,
`test-drift-fix.yml`, and Claude Code automated review in
`functional-tests.yml`. All workflows have proper top-level
`permissions` blocks.

**Needed cleanups:**

- [x] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [x] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [x] Add `.github/workflows/pr-retest.yml` (developer automation)
- [x] Add `.github/workflows/test-drift-fix.yml` (developer automation)
- [x] Console script logging setup: add
  `logging.basicConfig(level=logging.INFO)` and
  `logging.getLogger(__name__).propagate = False` after
  `setup_console()` in `main.py` so that INFO messages from
  module loggers propagate to a configured root logger (see
  PROJECT-CONSISTENCY-AUDITS.md "Console script logging setup")
- [x] Add `constraints.python` to `renovate.json` matching
  `requires-python = ">=3.7"` in `pyproject.toml` (needed for
  projects supporting multiple Linux distributions)
- [x] Resync `pr-address-comments.yml` from template
  (`templates/ci-review-automation/`). Resynced; also fixes
  PIPESTATUS as the template is now the canonical source for
  that pattern.
- [x] Add `.claude/skills/` directory with Claude skills for
  repetitive operations (documentation-updates,
  testing-discipline, pr-preparation)
- [x] Enable GitHub security settings: Dependabot security updates,
  secret scanning, and push protection all now enabled.
- [x] Resync `codeql-analysis.yml` from template
  (`templates/codeql/`). Now uses `[self-hosted, static]` runner,
  clean template format, job-level permissions with `actions: read`.
- [x] Update `Co-Authored-By` in `test-drift-fix.yml` from
  `Claude Opus 4.5` to `Claude Opus 4.6` (3 places)
- [x] Check `export-repo-config.yml` permissions: occystrap's
  `contents: write` + `pull-requests: write` are correct -- the
  reusable workflow creates branches, pushes, and creates PRs.
  The template at `contents: read` is too restrictive and should
  be fixed there instead.
- [x] Fix comment in `release.yml` line 35: says `pbr versioning`
  but occystrap uses `setuptools_scm`
- [x] Add `issues: write` to automated reviewer job permissions
  in `functional-tests.yml` (needed for the shared action's issue
  creation feature)

- [ ] Add `.github/workflows/pin-indirect-dependencies.yml` (library
      template)
- [ ] Add `[project.optional-dependencies] pinned` section with
      `# END_OF_INDIRECT_DEPS` marker to `pyproject.toml`

**Already compliant:** LLM tooling (`AGENTS.md`, `ARCHITECTURE.md`),
release process (`pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`),
Claude Code automated review in CI, developer automation (all four
bot-triggered workflows), renovate, repo config export, CodeQL,
linting (`.pre-commit-config.yaml` with actionlint, shellcheck,
check-log-levels), workflow permissions, default branch (`develop`).

**flake8wrap.sh:** Has script with correct unquoted variable
expansion. Uses simpler pattern (`tr '\n' ' '`) without `.py`
filtering, `_pb2` exclusion, or deleted file handling.

### ryll

Rust project with `Cargo.toml` at root. Has `AGENTS.md`,
`ARCHITECTURE.md`, and `.pre-commit-config.yaml`. No
`.github/workflows/` directory at all.

**Needed cleanups:**

- [ ] Add `.github/workflows/` directory with CI workflow
- [ ] Add Claude Code automated review to CI workflow
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`,
`.pre-commit-config.yaml`.

**Not applicable:** `pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`
(this is a Rust project, not a Python package released to pypi).

### shakenfist

The reference project. Has `AGENTS.md`, `ARCHITECTURE.md`,
`.claude/skills/` (3 skills), `pyproject.toml`, `release.yml`,
`RELEASE-SETUP.md`, `pr-re-review.yml`, `renovate.yml`,
`renovate.json`, `export-repo-config.yml`,
`.pre-commit-config.yaml`, `codeql-analysis.yml`, and Claude Code
automated review in `functional-tests.yml`.

**Needed cleanups:**

- [x] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [x] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [x] Migrate `export-repo-config.yml` to shared reusable workflow
- [x] Migrate `pr-re-review.yml` to shared `review-pr-with-claude` action
- [x] Migrate `functional-tests.yml` reviewer to shared action (with
  job-level `permissions` and bot-commit check)
- [x] Remove `tools/review-pr-with-claude.sh` (replaced by shared action)
- [x] Add `tools/address-comments-with-claude.sh`, `render-review.py`,
  and `review-schema.json` (supporting tools for developer automation)
- [x] Add top-level `permissions` to `ci-dependencies.yml`
- [x] Add top-level `permissions` to `ci-images.yml`
- [x] Add top-level `permissions` to `ci-images-test.yml`
- [x] Add top-level `permissions` to `code-formatting.yml`
- [x] Add top-level `permissions` to `codeql-analysis.yml`
- [x] Add top-level `permissions` to `docs-tests.yml`
- [x] Add top-level `permissions` to `export-repo-config.yml`
- [x] Add top-level `permissions` to `functional-tests.yml`
- [x] Add top-level `permissions` to `functional-tests-skip.yml`
- [x] Add top-level `permissions` to `pin-indirect-dependencies.yml`
- [x] Pin indirect dependencies workflow and marker (criterion 12)
- [x] Add top-level `permissions` to `pr-re-review.yml`
- [x] Add top-level `permissions` to `publish-website.yml`
- [x] Add top-level `permissions` to `refresh-website.yml`
- [x] Add top-level `permissions` to `release.yml`
- [x] Add top-level `permissions` to `renovate.yml`
- [x] Add top-level `permissions` to `scheduled-tests.yml`
- [x] Add top-level `permissions` to `sync-external-docs.yml`

**flake8wrap.sh:** Has script with correct unquoted variable
expansion. Uses `egrep` (deprecated, should be `grep -E`) and
`"x$1" = "x-HEAD"` (old sh compatibility, harmless). Missing
`shellcheck disable=SC2086` directive.

**DONE** - All items complete.

---

## Summary

### Fully compliant projects

- **imago** -- fully compliant with all criteria including full
  developer automation (`pr-fix-tests.yml`, `pr-address-comments.yml`,
  `pr-retest.yml`). Not a Python project so criterion 12 is N/A.
- **shakenfist** -- fully compliant. Developer automation, shared
  action migration, workflow permissions, and indirect dependency
  pinning all complete.
- **kerbside** -- fully compliant as of 2026-02-22 (criterion 12
  fixed 2026-02-28). Indirect dependency pinning workflow updated
  for `pyproject.toml`.

### Nearly compliant projects (1-3 items)

- **occystrap** -- needs `pin-indirect-dependencies.yml` (library
  template) and `pinned` optional extra with marker (2 items).
- **agent-python** -- needs `pin-indirect-dependencies.yml` (library
  template) and `pinned` optional extra with marker (2 items).
- **clingwrap** -- needs `pin-indirect-dependencies.yml` (library
  template) and `pinned` optional extra with marker (2 items).

### Partially compliant projects (4-6 items)

- **cloudgood** -- needs export-repo-config, renovate,
  pr-re-review, and developer automation (5 items). No Python
  package so criterion 12 is N/A.

### Major work needed (7+ items)
- **ryll** -- needs entire `.github/workflows/` directory, renovate,
  export-repo-config, pr-re-review, developer automation, CodeQL,
  and CI workflows for Claude review (8 items).
- **kerbside-patches** -- needs pr-re-review, developer
  automation, renovate, export-repo-config, CodeQL, plus top-level
  `permissions` on all 5 workflows (11 items).
- ~~**kerbside**~~ -- fully compliant as of 2026-02-28.
- ~~**clingwrap**~~ -- nearly compliant (2 items: criterion 12).
- **client-python** -- 17 items (missing nearly everything
  including developer automation, plus permissions on 2 workflows,
  plus indirect dependency pinning).
- **library-utilities** -- 15 items (missing nearly everything
  including developer automation, no workflows directory, plus
  indirect dependency pinning).

### Most common missing items across non-excluded projects

| Item                                | Missing in  |
|-------------------------------------|-------------|
| Developer automation (pr-fix-tests, pr-address-comments) | 10 projects |
| Workflow top-level `permissions`    | 7 projects  |
| `renovate.yml` + `renovate.json`   | 8 projects  |
| `export-repo-config.yml`           | 8 projects  |
| `codeql-analysis.yml`              | 8 projects  |
| `pr-re-review.yml`                 | 8 projects  |
| Claude Code automated review in CI | 7 projects  |
| `.pre-commit-config.yaml`          | 4 projects  |
| `AGENTS.md`                        | 3 projects  |
| `ARCHITECTURE.md`                  | 3 projects  |
| `release.sh` removal               | 4 projects  |
| `release.yml`                      | 5 projects  |
| `RELEASE-SETUP.md`                 | 5 projects  |
| `pin-indirect-dependencies.yml`    | 5 projects  |

Note: The 6 remaining projects missing workflow permissions account
for 18 individual workflow files that need top-level `permissions`
blocks added.

Note: Developer automation includes `pr-fix-tests.yml`,
`pr-address-comments.yml`, `pr-retest.yml`, and optionally
`test-drift-fix.yml`. Imago, occystrap, agent-python, and
shakenfist currently have these workflows.

### Suggested approach

1. **Build shared infrastructure first** (in `actions/` repo):
   a. ~~Create reusable workflow for `export-repo-config`~~ DONE
   b. ~~Create composite action for `review-pr-with-claude`~~ DONE
   c. Move developer automation workflows (`pr-fix-tests.yml`,
      `pr-address-comments.yml`) from imago to shared actions

2. **Quick wins** (simple file copies/additions per repo):
   a. Add `renovate.yml` + `renovate.json` to remaining 9 repos
   b. Add `export-repo-config.yml` caller to remaining 9 repos
   c. Add `pr-re-review.yml` to remaining 8 repos
   d. Add `codeql-analysis.yml` to remaining 9 repos (copy from
      occystrap; CodeQL auto-detects languages so the workflow is
      nearly identical everywhere, except non-code repos like
      cloudgood where it's N/A)

3. **Developer automation**: Roll out `pr-fix-tests.yml` and
   `pr-address-comments.yml` to remaining 10 repos (using shared
   actions once available).

4. **Pre-commit configs**: Add `.pre-commit-config.yaml` to the 4
   projects missing it.

5. **AGENTS.md and ARCHITECTURE.md**: Create these for the 3
   non-excluded projects missing them (requires understanding each
   project).

6. **Release infrastructure**: Add `release.yml` and `RELEASE-SETUP.md`
   to the 5 Python projects missing them (use the templates in
   `templates/release-automation/`), and remove `release.sh` from
   the 4 remaining projects that still have it.

7. **Claude Code automated review**: Add to the 7 remaining CI
   workflows missing it (using the shared composite action).

8. **Workflow permissions**: Add top-level `permissions` blocks to
   all 38 workflow files across 8 projects. Most read-only workflows
   need `permissions: contents: read`. Workflows with mixed needs
   (e.g. `release.yml`, `codeql-analysis.yml`) should use
   `permissions: {}` at the top level with per-job overrides.
   This is a high-priority security item flagged by GitHub Advanced
   Security.
