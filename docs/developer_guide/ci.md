# Continuous integration

How Shaken Fist's CI is put together: the workflows, the merge queue,
the automated jobs that open their own pull requests, and the bot
commands available on a PR.

## GitHub Actions workflows

Every workflow in `.github/workflows/`:

| Workflow | Purpose | Trigger |
|----------|---------|---------|
| `functional-tests.yml` | Main CI: lint, unit tests, functional tests, credential scanning, and the automated reviewer, delinter and exception fixer jobs. The functional jobs deploy nested test clusters via the `shakenfist.shakenfist` Ansible collection (`shakenfist/deploy/collection/`), driven by the reusable `smoke-cluster` workflow in the `shakenfist/actions` repository | PR, merge_group |
| `docs-tests.yml` | Build and test documentation | PR touching `docs/**` or `mkdocs.yml` |
| `code-formatting.yml` | Whole-tree formatting sweep | Daily schedule, manual, self-test PR |
| `codeql-analysis.yml` | CodeQL static analysis | Push, PR, weekly schedule |
| `pin-indirect-dependencies.yml` | Reconcile pinned indirect dependencies, adding new ones and removing obsolete ones (runs `tools/pin-indirect-dependencies.sh`) | Daily schedule, PR self-test |
| `renovate.yml` | Self-hosted Renovate dependency updates | Hourly schedule, manual |
| `export-repo-config.yml` | Export GitHub repo settings to version control, via a shared reusable workflow in the `actions/` repository | Daily schedule |
| `pr-re-review.yml` | Re-review PR on bot command | `@shakenfist-bot please re-review` |
| `pr-address-comments.yml` | Address review comments on bot command | `@shakenfist-bot please address comments` |
| `pr-fix-tests.yml` | Fix test failures on bot command | `@shakenfist-bot please attempt to fix` |
| `test-drift-fix.yml` | Unit test fixer (called by `pr-fix-tests.yml`) | workflow_call, workflow_dispatch |
| `issue-fix.yml` | Triage open issues, propose a fix as a draft PR | workflow_dispatch |
| `scheduled-tests.yml` | Longer-running test sweep (schedule currently disabled) | workflow_dispatch |
| `publish-website.yml` | Publish the mkdocs site | Push to `develop`, manual |
| `refresh-website.yml` | Trigger a GitHub Pages rebuild | Daily schedule, manual |
| `sync-external-docs.yml` | Import the sibling repositories' documentation into `docs/components/` | Hourly schedule, manual |
| `release.yml` | Build and publish a release | Tag push, manual |

## Merge Queue Pattern

The CI uses a two-stage merge queue pattern (see [this blog post](https://boinkor.net/2023/11/neat-github-actions-patterns-for-github-merge-queues/)):

1. **`Can enqueue`** - Runs on `pull_request` events, gates entry to merge queue
2. **`Can merge`** - Runs on `merge_group` events, gates the actual merge

**Important**: Only `Can see status` and `Can enqueue` are required status checks
in branch protection. `Can merge` is evaluated by the merge queue itself, not as
a required check.

## Exported Repository Configuration

Repository settings (rulesets, branch protection, merge queue config) are
exported to `.github/exported-config/` for version control and audit purposes:

- `repository-settings.json` - Repo-level settings
- `rulesets-summary.json` - List of all rulesets
- `ruleset-*.json` - Full details for each ruleset

If the `export-repo-config` workflow creates a PR, it means GitHub UI settings
have changed and should be reviewed.

## Credential scanning

The `credential_scan` job in `functional-tests.yml` runs
`tools/gitleaks-scan.sh`, which scans every commit reachable from `HEAD`
for leaked credentials. On a pull request that is the whole of `develop`
plus the branch under test: about three seconds over five and a half
thousand commits. It is one of `Can enqueue`'s dependencies, so a
credential cannot be merged, and unlike most jobs it is not skipped for
documentation-only changes -- a credential pasted into a code sample is
still a credential, and the one real key secret this scan found in our
own history had been published in the user guide.

The scan is scoped to `HEAD` rather than every ref because `gh-pages`
carries the built documentation site, whose search index is a single
enormous JSON blob quoting every code sample we have. Scanning it takes
five minutes instead of three seconds, produces around a hundred and
fifty findings which are all duplicates of source files already
scanned, and -- in gitleaks 8.16 -- attributes them to unrelated
`develop` merge commits, so they cannot even be triaged by commit.

The scan carries a positive control: it plants a key secret and an SSH
private key in a scratch directory and fails if gitleaks does not report
both. An empty result is otherwise indistinguishable from a broken
scanner, and the allowlists described below could in principle grow
until they forgive everything.

To reproduce a CI failure, run `tools/gitleaks-scan.sh` yourself, passing
`--gitleaks PATH` if the available binary is not the pinned 8.16.0. It
does not matter which directory you run it from: it changes to the top of
the working tree first, because both `.gitleaks.toml` and
`.gitleaksignore` are resolved relative to the working directory, and
from a subdirectory the ignore file would be missed silently and the
three accepted historical findings reported as new. It does need a full
clone -- a shallow one cannot see the history the scan claims to cover,
so the script says so and exits rather than passing over a fraction of
it.

Two rules are ours rather than upstream's:

* `shakenfist-key-secret` matches the `sfk_` credential format. Unit
  tests in `shakenfist/tests/test_credentials.py` read the rule's regex
  out of `.gitleaks.toml` and assert it matches what
  `credentials.generate()` actually produces, so the format and the
  scanner cannot drift apart silently.
* `shakenfist/tests/test_no_committed_credentials.py` walks the working
  tree for the same format but *verifies the checksum*, which
  distinguishes a real credential from a documented example. It runs in
  the unit suite and needs no allowlist at all.

### Accepting a finding

There are two places to record a finding you have decided to accept, and
they are not interchangeable.

Content which will recur -- a documentation placeholder, a test fixture,
an upstream default -- goes in the `[allowlist]` `regexes` list in
`.gitleaks.toml`, keyed on the text itself. Editing the paragraph around
a placeholder creates a new finding in a new commit, so anything keyed
on a commit would need replacing every time. Do not use `paths` for
this: blinding a whole file also blinds a real credential added to it
later. Note that 8.16 matches these regexes against the whole match
rather than the secret alone, so anchoring one with `^...$` quietly
stops it matching.

A specific historical event goes in `.gitleaksignore` as a
`commit:path:rule-id:line` fingerprint, which forgives that one
occurrence and nothing else -- the same secret in a new commit fails the
scan again. History cannot be rewritten to make such an entry
unnecessary: this repository is public, so anything committed here has
been world-readable since the day it landed. An entry therefore asserts
that the credential has been dealt with *where it was trusted*, not that
it has been tidied out of sight. Write down which credential, and what
was done. A unit test enforces that every entry is well formed and
carries a comment.

## Automated CI Jobs

`functional-tests.yml` carries three jobs which act on the pull request
themselves rather than only reporting on it.

### Automated Delinter

When flake8 fails, the `automated_delinter` job runs Claude Code to fix lint
errors automatically. It skips if the last commit was from the bot to prevent
loops.

### Automated Exception Fixer

When functional tests detect exceptions in logs, the `automated_exception_fixer`
job downloads the test bundles and runs Claude Code to analyze and fix the
issues.

### Automated Reviewer

After successful tests, the `automated_reviewer` job calls the shared
`shakenfist/actions/.github/workflows/pr-auto-review.yml@main` reusable
workflow, which reviews the PR with the `review-pr-with-claude` action.
All the gating other than "CI passed" lives in that shared workflow: the
runner, the 60 minute timeout, the pull-request-event and
same-repository restrictions, its own concurrency group, and the
bot-commit check which keeps a bot push from triggering a review which
triggers another bot push. What this repository supplies is the `needs:`
list naming the test jobs and the token `permissions`, which a
cross-repository reusable workflow cannot grant itself.

The `@shakenfist-bot please re-review` command in `pr-re-review.yml`
still uses the `shakenfist/actions/review-pr-with-claude@main` action
directly, because it deliberately passes `force` to review a PR the bot
has already reviewed.

The reviewer produces structured JSON reviews, creates GitHub issues for
actionable items, and embeds the JSON in the PR comment for automation.

### Developer Automation (Bot Commands)

Authorized users can trigger automation by commenting on PRs:

- **`@shakenfist-bot please re-review`** - Triggers a fresh automated
  review of the PR using the shared review action.
- **`@shakenfist-bot please address comments`** - Runs Claude Code to
  address actionable items from the automated review. Uses
  `tools/address-comments-with-claude.sh` with dual-checkout security
  (trusted tools from base branch, PR code separately).
- **`@shakenfist-bot please attempt to fix`** - Runs Claude Code to fix
  unit test failures (`tox -ecover`). Uses `test-drift-fix.yml` with
  structured commit summaries.

`issue-fix.yml` is required to check a proposed fix against the plans
in `docs/plans/` before writing any code. Triage skims
`docs/plans/index.md` and deprioritises issues an unlanded plan
already owns; the fix job reads the plan files covering the code it
means to change, follows the pattern established by phases which have
landed, and declines with `NO_FIX` when an outstanding phase is the
proper home for the fix. The plans are read from the checkout at run
time rather than summarised into the workflow, because they change
constantly. This exists because one-off automated fixes had been
landing across partially implemented plans and having to be unpicked
(see the step 3 note in
`docs/plans/PLAN-scheduler-reservations-phase-01-node-metrics-columns.md`).

`issue-fix.yml` runs its fix attempt through
`tools/claude-model-fallback.sh`, which takes a comma-separated
preference list (`--models`, default `claude-fable-5,claude-opus-5`) and
moves to the next model when one reports its subscription credit is
exhausted. That case arrives as an HTTP 429 in the `--output-format json`
payload (`api_error_status`), which the claude CLI's own
`--fallback-model` flag does not handle -- it only covers overloaded or
unavailable models. A refused request is free, so the wrapper attempts
the real job rather than paying for a pre-flight probe.

## CI Caching

Workflows that download packages use environment variables to route
traffic through local caches:

- **HTTP proxy**: `http_proxy`/`https_proxy` set to
  `http://192.168.1.15:3128` (Squid cache) for apt, curl, and
  general HTTP downloads.
- **PyPI mirror**: `PIP_INDEX_URL` set to
  `https://devpi.home.stillhq.com/root/pypi/+simple/` (devpi) for
  pip package installs.
- **uv mirror**: `uv` does not read pip's `PIP_*` variables, so
  workflows that resolve with `uv` must also set `UV_INDEX_URL` (and
  `UV_EXTRA_INDEX_URL` if a fallback index is wanted) to the same
  values. Setting only `PIP_INDEX_URL` silently sends the uv resolve
  straight to pypi.

CI VMs provisioned by the `shakenfist/actions` Ansible playbooks also
get system-level config files (`/etc/apt/apt.conf.d/01proxy` and
`/etc/pip.conf`) so that the collection deploy and other tools use the
caches.
- **Proxy bypass**: `no_proxy`/`NO_PROXY` set to
  `localhost,127.0.0.1,10.0.0.0/8` to prevent local service traffic from
  being routed through the proxy.

## Branch Protection

The develop branch uses:
- Required status checks: `Can see status`, `Can enqueue`
- Merge queue with ALLGREEN grouping strategy
- Configuration exported to `.github/exported-config/`
