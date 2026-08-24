# Consistency Audit Specifications

Every Shaken Fist project is expected to be packaged, documented,
tested and automated the same way. This directory is the statement of
what that means: one file per criterion, each defining what we check
and why, linking the template that implements it, and carrying a
per-project compliance table regenerated every morning.

It sits under `docs/` so that it publishes to shakenfist.com with
everything else. What we hold a project to is documentation, and a
criterion nobody outside the fleet can read is a criterion nobody
outside the fleet can meet.

## How audits work

Each file here is independently checkable, so an agent can be spawned
per criterion to check every project against it in parallel.

`docs/consistency-audits.md` is the working reference for the system as
a whole: what the daily run does, how issues are filed and closed, how
the compliance tables are regenerated, how to add a criterion, and how
to bring a repository into scope. Read it before adding a file here --
a new criterion touches five files (six if it shares a spec file with
another check), and a spec file on its own does not measure anything.

## File structure

Each audit file follows this structure:

```markdown
# Audit: <name>

## What we check
<concise description of the audit criterion>

## Template
Template: `templates/<name>/`
See: `templates/<name>/README.md`

## Projects

<!-- consistency-audit:begin -->
*(Awaiting the first automated regeneration by the consistency
audit workflow.)*
<!-- consistency-audit:end -->
```

## In-scope projects

The following projects are subject to consistency audits:

- actions
- agent-python
- client-python
- client-python-k3s
- clingwrap
- cloudgood
- development
- divergulent
- instar
- kerbside
- kerbside-patches
- library-utilities
- occystrap
- ryll
- shakenfist
- sfui

One project is in scope for part of the audit only:

- private-ci -- the `sfui-vendor` check, and nothing else. It is
  internal tooling and excluded from the conventions, but it vendors
  sfui and a vendored copy drifts silently. The scoping lives in
  `REPO_OVERRIDES` in `scripts/audit-check.py`; every other check
  reports N/A for it.

### Excluded projects

The following projects are **excluded** from these criteria, because
they are internal only tooling or historical archive repositories:

* ansible-modules
* client-js
* client-go
* client-python-ova
* deploy
* images
* imago-testdata
* imago-testdata-quarantine
* jenkins-private
* loadtest
* occystrap-testdata
* ostrich
* performance
* private-ci
* reproducables
* sonobouy
* symbolicmode
* terraform-provider-shakenfist
* uefi-latency-guest
* website

The `actions` repository is audited despite being tooling: the whole
fleet depends on it for its composite actions and reusable workflows,
so it is held to the same standards as anything else. Two criteria do
not apply to it -- it has no Python to package, and it keeps `main` as
its default branch because every consumer pins to `@main`.

`development`, this repository, is audited for the same reason turned
around: it is where these criteria and the tooling that enforces them
are written, so an exemption here is an exemption the authors of the
standard write for themselves. The same two criteria do not apply --
its Python is the audit scripts, which run from a checkout and are
never packaged, and it keeps `main` because it publishes no releases
and so has no release branch for `develop` to be the integration
branch against.

Both exemptions are stated reasons in `REPO_OVERRIDES` in
`scripts/audit-check.py`, which means they are reported as N/A with
the reason attached rather than quietly disappearing from the table.

`private-ci` stays excluded and is still audited for one thing,
because a vendored copy is the one kind of problem an exclusion
cannot make safe: nothing in the consumer fails when the copy falls
behind, or when someone edits it in place and the next sync discards
the edit. Exclusion means what it says for the rest -- `private-ci`
is not expected to grow a `pyproject.toml`, a renovate config,
release workflows, or a `develop` branch.

## Audit index

| File | Criterion |
|------|-----------|
| [llm-tooling.md](/components/development/audits/llm-tooling/) | AGENTS.md, ARCHITECTURE.md, Claude skills |
| [llm-doc-structure.md](/components/development/audits/llm-doc-structure/) | AGENTS.md and ARCHITECTURE.md are a summary and an index, detail lives in docs/ |
| [llm-context-lint.md](/components/development/audits/llm-context-lint/) | Agent context passes skillsaw at error severity, and every skill actually loads |
| [llm-context-lint-ci.md](/components/development/audits/llm-context-lint-ci/) | skillsaw runs in pre-commit and CI, not just in the daily audit |
| [release-process.md](/components/development/audits/release-process/) | pyproject.toml, release.yml, RELEASE-SETUP.md |
| [ci-review-automation.md](/components/development/audits/ci-review-automation/) | Automated review, developer automation workflows |
| [renovate.md](/components/development/audits/renovate/) | Renovate for dependency bumps |
| [pin-indirect-dependencies.md](/components/development/audits/pin-indirect-dependencies/) | Pinning transitive dependencies |
| [dependency-name-normalization.md](/components/development/audits/dependency-name-normalization/) | One spelling per pinned distribution |
| [export-repo-config.md](/components/development/audits/export-repo-config/) | Repo configuration export |
| [default-branch-naming.md](/components/development/audits/default-branch-naming/) | Default branch conventions |
| [github-security.md](/components/development/audits/github-security/) | Dependabot, secret scanning, CodeQL |
| [delete-branch-on-merge.md](/components/development/audits/delete-branch-on-merge/) | Branches are deleted automatically when a PR merges |
| [merge-queue-config.md](/components/development/audits/merge-queue-config/) | Merge queues process entries serially, without speculative stacking or merge batching |
| [merge-group-cancellation.md](/components/development/audits/merge-group-cancellation/) | Superseded merge group runs are cancelled, not left building clouds |
| [security-sanitization.md](/components/development/audits/security-sanitization/) | HTTP header and file path sanitization |
| [workflow-standards.md](/components/development/audits/workflow-standards/) | Permissions, naming, self-hosted runners, static runner tags, devpi cache fallback, devpi cache address, linting, PIPESTATUS, flake8wrap |
| [expensive-lane-path-filter.md](/components/development/audits/expensive-lane-path-filter/) | Expensive PR lanes skip docs-only and review-marks-only changes |
| [console-logging.md](/components/development/audits/console-logging/) | Console script logging setup |
| [python-version.md](/components/development/audits/python-version/) | Python version targeting and type hints |
| [pyproject-usage.md](/components/development/audits/pyproject-usage/) | Python projects use pyproject.toml |
| [version-file-gitignore.md](/components/development/audits/version-file-gitignore/) | Generated version files are gitignored |
| [rust-unwrap-lint.md](/components/development/audits/rust-unwrap-lint/) | Rust projects lint against production unwrap() |
| [readme-absolute-links.md](/components/development/audits/readme-absolute-links/) | Top-level README.md links are absolute |
| [docs-external-links.md](/components/development/audits/docs-external-links/) | Links out of docs/ resolve inside docs/, or else are absolute |
| [readme-structure.md](/components/development/audits/readme-structure/) | Top-level README.md is a pitch, detail lives in docs/ |
| [plan-phase-references.md](/components/development/audits/plan-phase-references/) | Docs describe current behaviour, not plan phase history |
| [plan-source-references.md](/components/development/audits/plan-source-references/) | Plan references in source and configuration still resolve |
| [plan-index.md](/components/development/audits/plan-index/) | docs/plans/index.md layout, date ordering, plan coverage and the status vocabulary |
| [push-audit.md](/components/development/audits/push-audit/) | PUSH-AUDIT.md naming, versioned shared blocks, and an AGENTS.md reference to it |
| [plan-template.md](/components/development/audits/plan-template/) | PLAN-TEMPLATE.md shared blocks, including the sub-agent model roster and the push-audit phase |
| [test-coverage.md](/components/development/audits/test-coverage/) | Unit and functional test coverage |
| [secret-handling.md](/components/development/audits/secret-handling/) | Secret scanner in CI, credentials kept out of logs |
| [review-coverage.md](/components/development/audits/review-coverage/) | Human review backlog stays under threshold in repos with review tracking |
| [sfui-vendor.md](/components/development/audits/sfui-vendor/) | Vendored sfui copies are verbatim and current |

## Beyond the audits

Everything here is a criterion because it can be stated plainly and,
mostly, measured. That is not the whole of what we want from a
project. We should be proud of our shared work: a regular holistic
review of each project should ask what could be improved or tightened
up with a refactor, and we should not be scared of a large refactor
that delivers a large benefit -- while equally avoiding change for
change's sake.
