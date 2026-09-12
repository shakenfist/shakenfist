# Plan: onboarding hunkydory, and what TypeScript means for the fleet

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every morning
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a daily run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

This plan is unusual for this repository in that most of its work
lands somewhere else: in `hunkydory`, and in the `33fl` repository
in the other organisation, which owns the static runner fleet. What
lands here is the scope registration, the npm dependency criteria,
and this document.

## Situation

`shakenfist/hunkydory` was created on 2026-09-11. It is a VS Code
extension that keeps the `@@` hunk headers in a patch file correct
while the file is edited, written in TypeScript with no runtime
dependencies. It is the fleet's **first TypeScript repository**, and
nothing in the audit, the templates or the runner images has met the
language before.

Three measurements frame the work.

**The audit already runs against it, and mostly passes.** Running
the checker by hand against a clone, as
`docs/consistency-audits.md` instructs before onboarding:

```
python3 scripts/audit-check.py --repo-path ~/src/shakenfist/hunkydory \
    --repo-name hunkydory --github-org shakenfist
```

reports 51 checks: **6 pass, 9 fail, 36 not applicable**. The nine
failures are all infrastructure rather than anything about the
code:

| Check | Why it fails |
|-------|--------------|
| `pre-commit-config` | No `.pre-commit-config.yaml` |
| `llm-context-lint-ci` | skillsaw runs from neither pre-commit nor CI |
| `renovate` | No `renovate.json`, no `renovate.yml` |
| `ci-review-automation` | No `pr-re-review.yml`, no `pr-retest.yml`, no reviewer action |
| `export-repo-config` | No `export-repo-config.yml` |
| `github-security` | No CodeQL workflow; secret scanning and push protection off |
| `delete-branch-on-merge` | Not enabled on the repository |
| `readme-absolute-links` | Three relative links in `README.md` |
| `docs-external-links` | One relative link in `docs/` leaving `docs/` |

The documentation criteria -- `llm-tooling`, `llm-doc-structure`,
`readme-structure`, `diagram-format`, `plan-phase-references` --
already pass, as does `default-branch-naming`: the repository was
created with `develop` as its default branch.

**Nine of the 36 not-applicable results are not-applicable because
the criterion is Python-specific**, not because the concern does not
exist. `release-process`, `pin-indirect-dependencies`,
`dependency-name-normalization`, `unused-declared-dependency`,
`undeclared-direct-dependency`, `renovate-lockstep-groups`,
`version-file-gitignore` and `python-version-targeting` all key off
`pyproject.toml`, and `pyproject-usage` off the presence of Python.
A TypeScript repository with a `package.json` and a
`package-lock.json` raises the same questions about pinning and
unused dependencies, and today the audit simply does not ask them.

**The runners cannot run npm.** No workflow anywhere in the fleet
uses `setup-node`, `npm ci` or `npm install`; the mermaid-lint
template records the reason, that running it "from the upstream
container keeps chromium and a node toolchain off the runners", and
that jsdom "pulls in an undici that needs a newer node than the
runners carry". The static runners boot `debian:12`
(`33fl/static_runner.yml:229`), which packages `nodejs 18.20.4`.
Debian 13 packages `nodejs 20.19.2`. Both were confirmed against the
archive rather than assumed.

hunkydory itself was verified to build and pass its 20 unit tests on
Debian 12's `nodejs 18.20.4` inside a container, and Biome 2.5.13 was
verified to install and run there too. So node 18 is *sufficient*;
the decision to move to Debian 13 below is taken for other reasons.

## Mission and problem statement

Bring `hunkydory` under the consistency audits and the human review
tracking, and in doing so decide what the fleet's conventions mean
for TypeScript: how npm runs in CI, what lints a TypeScript project,
what a pre-commit configuration looks like for it, and how its
dependencies are audited.

The plan deliberately does not try to make TypeScript a first-class
citizen of every criterion. It answers the questions hunkydory
actually raises, and leaves a second TypeScript repository to
generalise from two examples rather than one.

It also does not cover the extension's own functionality, which is
complete and tested, or its publication to the VS Code Marketplace
beyond the mechanics of a release workflow.

## Decisions

### D1. npm runs on static runners, from Debian packages, on Debian 13

Three options were considered: `actions/setup-node` on the existing
static pool, a pinned `node:20` container on an ephemeral VM runner,
and installing Debian's `nodejs` on the runner image.

`setup-node` is the wrong shape here. The static runners are not
ephemeral -- private-ci describes them as "a static shared runner
rather than an ephemeral per-job VM" -- so `setup-node` leaves a
tool cache under `_work/_tool/node/` that grows without bound and is
shared by every repository using the pool. It also downloads a node
that Debian already packages. The PATH change itself is harmless and
job-scoped, via `GITHUB_PATH`; the disk state is the problem.

The container option touches no shared state at all, and matches
what ryll and mermaid-lint already do, but it spends the scarcest
runner pool (`l`, six workers fleet-wide) on a thirty-second build.

So: `nodejs` and `npm` from Debian, installed on the static runner
image. That makes npm a first-class fleet capability rather than a
hunkydory workaround, keeps the runtime on Debian's security
support, and leaves CI as `npm ci` with nothing to download.

Taken together with the version question, this means moving the
static runners from `debian:12` to `debian:13`. hunkydory runs fine
on Debian 12's node 18.20.4, so this is not forced by hunkydory.
It is taken because node 18 reached upstream end of life in April
2025 and Debian 13's node 20.19.2 both matches what the project is
developed against and buys years rather than months. The conductor
already builds a `debian-13` label from a `debian:13` base image, so
the image is cached on the cluster, which is the precondition
`static_runner.yml` documents at its line 39.

The blast radius is the reason this is its own phase: it re-images
every `static` and `claude-code` runner in **both** the shakenfist
and mach33labs organisations.

### D2. Biome, not ESLint

One devDependency that both lints and formats, against roughly six
for `eslint` + `typescript-eslint` + `prettier` and their configs.
hunkydory has no runtime dependencies and the smaller surface keeps
it that way, and keeps Renovate quiet. Biome 2.5.13 was confirmed to
run on node 18.20.4, so this decision does not depend on D1 landing.

The cost is a smaller rule set and a smaller ecosystem than ESLint,
which is the conventional choice for a VS Code extension. If a
second TypeScript repository wants ESLint specifically, that is the
point to revisit rather than now.

Biome needs a `biome.json` matching the conventions `AGENTS.md`
already states -- 100 character lines, single quotes, semicolons --
because its defaults disagree with all three.

### D3. Publish to the VS Code Marketplace

Rather than deferring releases or attaching a `.vsix` to a GitHub
release, hunkydory publishes with `vsce publish`. The extension is
meant to be installed by people who are not us, and an extension
nobody can install from inside VS Code is one nobody installs.

This is the one decision with a prerequisite outside any repository:
an Azure DevOps publisher account for the `shakenfist` publisher id
already named in `package.json`, and a `VSCE_PAT` repository secret.
Until both exist the release phase cannot be completed, which is why
it is sequenced last and marked `Blocked`.

### D4. Write npm dependency criteria now

The three Python dependency criteria -- `pin-indirect-dependencies`,
`unused-declared-dependency`, `undeclared-direct-dependency` -- get
npm equivalents in this plan rather than being recorded as not
applicable.

The weaker option was available and was rejected: `package-lock.json`
does pin the full transitive tree and `npm ci` does enforce it, so
`pin-indirect-dependencies` is arguably satisfied by construction.
But that reasoning covers one of the three. Nothing checks that a
declared dependency is actually imported, or that an import is not
resting on a transitive pin, and those are the two that catch real
drift. Writing all three keeps the npm story symmetric with the
Python one instead of leaving two thirds of it unmeasured.

The Python criteria that stay not applicable need no override to say
so. `release-process`, `pin-indirect-dependencies`,
`dependency-name-normalization`, `unused-declared-dependency`,
`undeclared-direct-dependency`, `renovate-lockstep-groups`,
`version-file-gitignore` and `python-version-targeting` each begin by
skipping when `has_pyproject_toml` is false, and `pyproject-usage`
falls through to `skip('No Python code')` when `git ls-files -- '*.py'`
comes back empty. That is already why they are among the 36
not-applicable results in the Situation section.

Nor is there a facility to say it. `detect_repo_properties()`
(`scripts/audit/repo.py:108-133`) reads exactly six override keys --
`is_private`, `is_docs_only`, `not_python`, `default_branch_exception`,
`only_checks` and `doc_content_excludes` -- and discards anything else
silently, so a per-criterion not-applicable-with-reason entry would be
invented shape that no test would catch and no check would read. If
such a facility is ever wanted it is a change to
`detect_repo_properties()` with its own phase and its own test, not a
line in this plan's phase 1.

### D5. 33fl is not touched by this plan's author

Another session is editing `33fl` concurrently. The runner phase
below specifies the change and its risks but is not to be executed
until that work has landed and the operator says so. Nothing else in
the plan writes to that repository.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Register hunkydory in the audit scope | Not started | |
| 2. hunkydory adopts the local tooling | Not started | |
| 3. npm dependency criteria | Not started | |
| 4. Static runners gain node | Blocked | |
| 5. hunkydory CI and the fleet workflows | Not started | |
| 6. Human review onboarding | Not started | |
| 7. Marketplace release | Blocked | |
| 8. Push audit | Not started | |

Phase 4 is blocked on D5: `33fl` has another session working in it.
Phase 5 depends on phase 4, because a CI workflow that runs `npm ci`
on a runner without npm is a workflow that fails on arrival. Phase 7
is blocked on the publisher account and `VSCE_PAT` from D3.

Phases 1, 2, 3 and 6 have no such dependency and can proceed in any
order. Phase 1 should go first regardless: see its section.

### 1. Register hunkydory in the audit scope

The `scope-coverage` criterion reconciles the audit lists against
the organisation every morning and reports a repository that appears
in neither the matrix nor the excluded list. hunkydory has existed
since 2026-09-11 and appears in neither, so this criterion is
failing against `development` now, and will keep failing until this
phase lands. That is why it goes first and alone rather than waiting
for the rest of the plan.

Following `docs/consistency-audits.md`:

- Add `hunkydory` to the matrix in
  `.github/workflows/consistency-audit.yml`.
- Add it to the in-scope list in `docs/audits/README.md`, and confirm
  it does not appear in the excluded list.

Those are the three statements `audit/scope.py` parses -- the matrix,
and the in-scope and excluded lists -- and
`AuditScopeIsStatedOnceTest` holds them to each other, so they change
together. `REPO_OVERRIDES` is a separate, fourth statement, read only
against the partial-scope paragraph and only for `only_checks`; this
phase does not touch it.

No `REPO_OVERRIDES` entry is needed at all: per D4, the Python
criteria already report not-applicable from the absence of
`pyproject.toml`, and there is no key that would carry a
per-criterion reason if one were wanted.

The nine failures from the Situation section become nine issues on
`hunkydory` at the next run. That is the intended behaviour -- they
are the backlog the rest of this plan works through -- but it should
be a deliberate choice rather than a surprise, and the phase says so
here so the next reader knows the issues were expected.

### 2. hunkydory adopts the local tooling

Everything that does not need a runner:

- Biome as a devDependency, with `biome.json` set to 100 character
  lines, single quotes and semicolons per D2, and the existing
  source brought into compliance.
- `tools/check-node.sh`, running the build, the tests and Biome,
  following the pattern ryll uses for `scripts/check-rust.sh`: one
  script called by both pre-commit and CI, so the two cannot drift.
- `.pre-commit-config.yaml` with that script as a `language: script`
  local hook, plus shellcheck, gitleaks and skillsaw from the fleet
  configuration. skillsaw here closes *half* of
  `llm-context-lint-ci`: the check requires both a pre-commit entry
  and a CI route, and its issue title says so -- "LLM context linting
  in pre-commit and CI". Phase 5's `ci.yml` closes the other half by
  running `pre-commit run --all-files`, which the check accepts via
  `PRE_COMMIT_RUN_RE`. Until phase 5 lands, this criterion stays
  red.
- Fix the three relative links in `README.md` and the one in
  `docs/`, closing `readme-absolute-links` and
  `docs-external-links`.
- Align `@types/node` with whatever runtime D1 lands on, and declare
  `engines.node`. Today the package declares `^20` while the runner
  it is destined for would have had 18, which is how a type
  definition ends up describing an API the runtime lacks.
- `PUSH-AUDIT.md`, copied from this repository, and a line in
  hunkydory's `AGENTS.md` saying when to run it. The `push-audit`
  criterion measures both the shared blocks and the reference, and
  reports not-applicable while the file is absent -- so this is the
  phase that gives phase 8 a runbook to cite rather than a gap to
  explain.

### 3. npm dependency criteria

Three checks per D4, following the shape
`docs/audits/README.md` and the template's worked brief describe. A
criterion has six parts here, not four:

- a `Check` subclass with `id`, `spec` and `issue_title` as class
  attributes;
- registration in `scripts/audit/registry.py`;
- a specification page under `docs/audits/`;
- a line in `docs/audits/README.md`;
- a line each in `FROZEN_METADATA`, `FROZEN_ISSUE_TITLES` and the
  frozen column table in `scripts/tests/test_metadata.py`, which
  `test_audit_metadata_matches_the_frozen_table`,
  `test_issue_titles_match_the_frozen_table` and
  `test_column_names_match_the_frozen_table` assert equality
  against;
- tests in `scripts/tests/test_packaging.py`, where the existing
  dependency criteria are tested, covering pass, fail and
  not-applicable. `test_metadata.py` also asserts that every check
  is reachable from some test module.

Adding three checks without touching the frozen tables fails
`pre-commit run --all-files`, which is the first item on this plan's
own review checklist, so the phase would fail its own gate. This is
the same staleness `PLAN-scope-coverage.md:433` corrected in
`PUSH-AUDIT.md`; the four-part framing survived into an earlier draft
of this plan.

**How many repositories this newly fails: one.** Every default-branch
tree of all twenty in-scope repositories was listed recursively
through the GitHub API, and `package.json` appears in exactly one of
them -- `hunkydory`, at the root. No tree was truncated, so the survey
is complete rather than a sample. The other nineteen, `development`
included, report not-applicable, and `33fl` is outside the audit
fleet. So phase 3 files at most three issues, all on hunkydory, and
they join the backlog phase 1 already accounts for. If a second
repository grows a `package.json` before this phase runs, re-run the
survey before landing: the answer is what makes this a safe change to
land in one step rather than a scoped rollout.

They apply when `package.json` is present and report
`not_applicable` with a reason otherwise.

The false-positive surface is where the work is, and hunkydory
exhibits all of it today. `unused-declared-dependency` and
`undeclared-direct-dependency` must exempt:

- **Node builtins, in both spellings.** `test/corpus.ts` has `import
  fs from 'node:fs'` and `import path from 'node:path'`; the bare
  `fs` and `path` spellings are equally valid and equally not
  packages.
- **Host-provided modules.** `src/extension.ts` has `import * as
  vscode from 'vscode'`, which the extension host injects and which
  must never be declared as a dependency. A naive undeclared-direct
  check flags it.
- **Relative imports.** `./diff`, `../src/recount` are not packages.
- **`@types/*` packages.** Consumed by `tsc`, never imported by name.
  hunkydory declares `@types/node` and `@types/vscode` today.
- **`typescript` itself**, and any devDependency invoked from
  `scripts` in `package.json` rather than from an `import` --
  `@biomejs/biome` after phase 2, and `vsce` after phase 7.

Without those five, the first run files two false issues on
hunkydory: `unused-declared-dependency` against all three of its
current devDependencies (`@types/node`, `@types/vscode`,
`typescript`), none of which is imported by name, and
`undeclared-direct-dependency` against `vscode` and the two
`node:`-prefixed builtins. Note also that `import type` and
`export ... from` are import forms and must be counted as such.

For `pin-indirect-dependencies`: if it reports on lockfile entries
rather than on `package.json`, Biome contributes several rather than
one -- `@biomejs/biome` resolves platform-specific binaries
(`@biomejs/cli-linux-x64` and siblings) as optional dependencies.
That is not drift and must not be read as drift.

Note that this repository is inside its own audit matrix: these
checks will run against `development` too, find no `package.json`,
and must report not-applicable rather than failing.

### 4. Static runners gain node

**Do not execute without the operator's say-so; see D5.**

In `33fl/static_runner.yml`:

- Line 229, the disk specification in "Create the missing runner
  instances", moves from `@debian:12` to `@debian:13`.
- `nodejs` and `npm` join the base package list at approximately
  line 337.
- The comment at line 39, which documents the cached `debian:12`
  image as a precondition, is updated to say `debian:13`.

Three things to verify before proposing that change, none of which
this plan has checked:

- The playbook installs `yq` with `pip --break-system-packages`,
  installs docker through a shared `docker.yml`, and installs the
  claude CLI for the claude flavor. All three need confirming on
  trixie.
- Line 630 sets `--docker-image debian:12` for the GitLab docker
  executor. That is a different thing from the runner's own image
  and is deliberately left alone here, but somebody should decide
  whether it moves too.
- Whether `python3-venv` and the rest of the base list behave the
  same on trixie.

Landing node on the runners also falsifies half of the mermaid-lint
rationale this plan quotes as evidence in the Situation section, in
four files where it is load-bearing prose:
`templates/mermaid-lint/README.md:28,36`,
`templates/mermaid-lint/mermaid-lint.sh:16`,
`tools/mermaid-lint.sh:16` and
`docs/audits/mermaid-lint-ci.md:108,116`. A node toolchain goes onto
the runners deliberately, and node 20.19.2 is no longer "older than
jsdom wants". The chromium half of the argument survives and the
decision does not change, so this is a rewording rather than a
reversal: keep the chromium argument, drop or restate the
node-version one. It is part of this phase rather than future work
because `templates/` is copied into ten repositories, and a template
that justifies itself with a fact the fleet has reversed is judged as
the code it becomes.

The rollout is gradual rather than a re-image: line 229 is inside
the loop over `missing_runners`, so it affects newly created
instances only, and the weekly retire and rebuild cycle replaces the
fleet over about a week. That is a feature -- a bad image shows up
on one runner rather than all of them -- but it means "landed" and
"rolled out" are a week apart, and phase 5 waits for the latter.

### 5. hunkydory CI and the fleet workflows

Once the runners have npm:

- `ci.yml` running `tools/check-node.sh` on `[self-hosted, static]`,
  with `npm_config_cache` pointed into `${{ runner.temp }}` so the
  shared `~/.npm` on a non-ephemeral runner is not written by a
  repository's build. It also runs `pre-commit run --all-files` as a
  job, the way this repository's own `ci.yml` does, so the shellcheck,
  gitleaks and skillsaw hooks gate a pull request rather than only a
  clone where somebody ran `pre-commit install`. That is what closes
  the CI half of `llm-context-lint-ci` from phase 2.
- `codeql-analysis.yml`, `export-repo-config.yml`, `renovate.yml`
  and `renovate.json`, `pr-re-review.yml`, `pr-retest.yml`,
  `secret-scan.yml` from the fleet templates, closing
  `github-security`, `export-repo-config`, `renovate` and
  `ci-review-automation`.
- Secret scanning, push protection and delete-branch-on-merge
  enabled through the GitHub API, closing `github-security`'s
  remaining two findings and `delete-branch-on-merge`.

CodeQL supports JavaScript and TypeScript directly, so that workflow
is a language substitution rather than a new pattern.

**This phase activates four criteria that are dormant today.**
`workflow-permissions`, `self-hosted-runners`, `static-runner-tags`
and `secret-scanning-ci` all begin with `if not
repo.props['has_workflows_dir']: return self.skip(...)`, so they are
among hunkydory's 36 not-applicable results purely because it has no
`.github/workflows/`. Creating that directory makes all four live.
Three are satisfied by what this phase writes: the fleet templates
carry `permissions:` blocks, and `ci.yml` names `[self-hosted,
static]`. The fourth is why `secret-scan.yml` is in the list above --
`secret-scanning-ci` requires one of `gitleaks`, `trufflehog` or
`detect-secrets` invoked *from a workflow*, and reads workflows only,
so the gitleaks hook in `.pre-commit-config.yaml` would not have
counted on its own. Phase 1 names the failures it creates so they are
a deliberate choice rather than a surprise; this phase creates none,
and that is the point of saying so here.

### 6. Human review onboarding

Deploy the review tracking from `docs/code-review-tracking.md` so
the operator can work through the code: `.vscode/review-scope.toml`,
`tools/review-tracking.sh`, a `prune-reviews.yml` workflow, and a
generated `REVIEWS.md`. This turns `review-marks-pre-commit`,
`review-coverage` and `review-scope-completeness` from
not-applicable into real verdicts.

The scope config should cover `src/` and `test/`. The point of this
phase is that the operator has not read code written entirely by an
agent, and the review queue is how that gets fixed.

This phase deliberately opens a `review-coverage` issue, the way
phase 1 opens nine. `review-coverage` fails once five or more
in-scope files need review (`REVIEW_BACKLOG_THRESHOLD = 5` in
`scripts/audit/checks/review.py`), and hunkydory's `src/` and `test/`
hold five files between them with none marked, so the criterion goes
red the moment `.vscode/review-scope.toml` lands and stays red until
the operator has worked the queue. That is the criterion doing its
job, not a regression, and it is why the success criteria below
exempt it.

### 7. Marketplace release

**Blocked on the publisher account and `VSCE_PAT`; see D3.**

A `release.yml` that packages and publishes on a tag, and the
`RELEASE-SETUP.md` the fleet's release criterion expects.

**`release.yml` does not run on `[self-hosted, static]`.** D1's own
argument is that the static pool is "a static shared runner rather
than an ephemeral per-job VM", shared by every repository in both the
shakenfist and mach33labs organisations, with filesystem and process
state that outlives a job. `VSCE_PAT` can publish under the
`shakenfist` publisher id, so putting it in a job's environment on
that pool exposes it to every other repository's jobs on the same
machine -- and `npm ci` runs dependency lifecycle scripts there, so a
typosquatted transitive package in any repository using the pool runs
code where the token has been. So:

- the publish job runs on an ephemeral VM runner, not the static
  pool;
- `VSCE_PAT` is a GitHub *environment* secret on a tag-protected
  environment, not a plain repository secret, so it is not readable
  from a job on a branch;
- build and publish are separate jobs: the build runs `npm ci` and
  produces the `.vsix` as an artifact with no access to the secret,
  and the publish job uploads that artifact without running install
  scripts.

If the static pool is used anyway, this phase says why the exposure
is acceptable, here, where the next reader finds it.

Note that
`release-process` as written measures a Python package and will stay
not applicable; whether it grows a TypeScript arm is future work
rather than part of this plan.

### 8. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of every phase against
`main`, per the shared block. Phases landing in `hunkydory` and
`33fl` record `<repo> <sha> (#pr)` in the `Merged` column.

**Neither hunkydory nor 33fl carries a `PUSH-AUDIT.md` today,** so
as this plan is written there is no runbook in either to cite. The
`push-audit` criterion reports not-applicable when the file is
absent, which is why it is not among
the nine failures the Situation section enumerates, and `33fl` is
outside the audit fleet entirely. The shared block anticipates this:
"A repository with no `PUSH-AUDIT.md` still carries the phase, and
the phase says that the runbook does not exist yet and what was done
instead."

What is done instead: phase 2 deploys this repository's
`PUSH-AUDIT.md` to hunkydory as part of the local tooling it adopts,
since it is a template the fleet copies, so by the time this phase
runs hunkydory has a runbook and its pull requests are audited
against `develop` like any other repository's. For `33fl`, this phase
runs *this* repository's `PUSH-AUDIT.md` briefs over the runner diff
directly and records that it did so, rather than citing an audit
nothing obliges anyone to perform.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session, per the `subagent-execution-model` shared block
in `PLAN-TEMPLATE.md`. The management session plans, reviews the
actual files rather than the summary, and commits.

### Planning effort

Phase 3 is high effort: it changes what the fleet is measured
against, and `docs/consistency-audits.md` warns that a wrong
criterion files issues in ten repositories rather than producing a
red build. Phase 4 is high effort for the same reason turned
outward -- it changes the machine every static job runs on, in two
organisations. Phases 1, 2, 5, 6 and 7 are medium: they follow
patterns already worked out elsewhere in the fleet.

### Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | Add `hunkydory` to the matrix in `.github/workflows/consistency-audit.yml` and to the in-scope list in `docs/audits/README.md`, and confirm it is absent from the excluded list. Those are the three statements `audit/scope.py` parses and `AuditScopeIsStatedOnceTest` holds them to each other, so all three change together. Do **not** add a `REPO_OVERRIDES` entry: per D4 the Python criteria already skip on the absence of `pyproject.toml`, and `detect_repo_properties()` has no per-criterion not-applicable key to carry a reason in. |
| 2 | medium | sonnet | none | In hunkydory: add Biome with a `biome.json` set to 100 columns, single quotes, semicolons; write `tools/check-node.sh` mirroring ryll's `scripts/check-rust.sh`; add `.pre-commit-config.yaml` calling it as a `language: script` hook alongside shellcheck, gitleaks and skillsaw; fix four relative links; align `@types/node` and add `engines.node`; copy `PUSH-AUDIT.md` in and reference it from `AGENTS.md`. |
| 3 | high | opus | worktree | Add three `Check` subclasses for npm dependency auditing to `scripts/audit/checks/`, following the worked brief in `PLAN-TEMPLATE.md`. Register in `scripts/audit/registry.py`, write a spec page each under `docs/audits/`, add them to the index in `docs/audits/README.md`, add their lines to `FROZEN_METADATA`, `FROZEN_ISSUE_TITLES` and the frozen column table in `scripts/tests/test_metadata.py`, and add tests in `scripts/tests/test_packaging.py` covering pass, fail and not-applicable. They must report not-applicable with a reason where there is no `package.json`, including against this repository -- hunkydory is the only repository in the fleet that has one. Read the phase 3 section for the five exemptions the dependency checks must carry (node builtins in both spellings, the host-provided `vscode` module, relative imports, `@types/*`, and devDependencies invoked from `scripts`); without them the first run files three false issues on hunkydory. |
| 4 | high | opus | worktree | **Hold.** See D5. Includes rewording the node half of the mermaid-lint rationale in the four files the phase 4 section names. |
| 5 | medium | sonnet | none | Copy the fleet workflow templates into hunkydory, including `secret-scan.yml`, substituting TypeScript for Python in CodeQL, and write `ci.yml` calling `tools/check-node.sh` on `[self-hosted, static]` with `npm_config_cache` under `runner.temp`, plus a job running `pre-commit run --all-files`. Read the phase 5 section for the four criteria that go live when `.github/workflows/` first appears. |
| 6 | medium | sonnet | none | Deploy review tracking per `docs/code-review-tracking.md`, scoped to `src/` and `test/`. |
| 7 | medium | sonnet | none | **Hold.** See D3, and the runner and secret-scoping constraints in the phase 7 section -- `release.yml` does not run on the static pool. |
| 8 | high | opus | none | Run `PUSH-AUDIT.md` over the accumulated diff, citing the other repositories' audits. |

### Model choice

Per the `subagent-model-roster` shared block. Phases 3, 4 and 8 take
opus for the reasons in Planning effort; the rest are well-briefed
mechanical work where sonnet with the briefs above should succeed.

### Management session review checklist

Per the `plan-review-checklist` shared block, plus this
repository's own checks:

- [ ] `pre-commit run --all-files` passes.
- [ ] `python3 scripts/audit-check.py --repo-path . --repo-name
      development` still reports what it reported before the change,
      or this plan says why the verdict moved. Phase 3 is expected
      to add three not-applicable results here and nothing else.
- [ ] Issue filing was exercised with `--dry-run` only.
- [ ] `python3 scripts/audit-check.py --repo-path <hunkydory clone>
      --repo-name hunkydory --github-org shakenfist` was re-run after
      the phase, and the verdict moved the way the phase said it
      would. The Situation section established the start state by
      measurement; a phase that closes criteria should establish the
      end state the same way rather than asserting it. Phase 5 in
      particular moves four criteria from not-applicable to live, and
      phases 2 and 5 close `llm-context-lint-ci` between them, so
      "which criteria changed" is the check that catches a phase
      claiming more than it delivered.

## Risks and mitigations

The material below is stated in the phases too; it is gathered here
because somebody deciding whether to approve phase 4 should not have
to reassemble it from three sections.

**Trixie regressions the runner phase has not verified.** Phase 4
moves the static runner image from `debian:12` to `debian:13` without
having confirmed that the playbook's `yq` install via `pip
--break-system-packages`, the shared `docker.yml`, the claude CLI
install, or `python3-venv` and the rest of the base package list
behave the same on trixie. *Mitigation:* those four are named as
must-verify in phase 4 and are a precondition for proposing the
change, not a follow-up. The gradual rollout means a bad image
surfaces on one runner rather than all of them.

**A mixed node-18 and node-20 pool for about a week.** Line 229 is
inside the loop over `missing_runners`, so only newly created
instances get the new image and the weekly retire-and-rebuild cycle
replaces the fleet over roughly a week. A job could land on either.
*Mitigation:* hunkydory was verified to build and pass its 20 tests
on Debian 12's node 18.20.4, so both halves of the pool can run it;
and phase 5 waits for the rollout to finish rather than for phase 4
to land. "Landed" and "rolled out" are a week apart and the plan says
so.

**Phase 3's criteria are measured against the whole fleet the next
morning.** A wrong criterion files issues in twenty repositories
rather than producing a red build. *Mitigation:* the survey in phase 3
establishes that hunkydory is the only repository with a
`package.json`, so the blast radius today is one repository and three
issues. The five exemptions in phase 3 are the guard against those
three being false. Issue filing is exercised with `--dry-run` only,
per the review checklist.

**Another session is editing `33fl` concurrently** (D5), so phase 4
could collide with work in flight. *Mitigation:* phase 4 is marked
`Blocked` in the Execution table, restated as **Hold** in the step
guidance, and carries "Do not execute without the operator's say-so"
at the head of its section. Nothing else in the plan writes to that
repository.

**Phase 7 handles a Marketplace publish token.** `VSCE_PAT` can
publish under the `shakenfist` publisher id. *Mitigation:* the
constraints in phase 7 -- an ephemeral runner, a tag-protected
environment secret, and build separated from publish so `npm ci`
never runs in the job that holds the token.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `scripts/audit-check.py` against `hunkydory` reports no failures
  other than `review-coverage`, which phase 6 deliberately opens and
  which stays open until the human review queue has been worked.
* `scope-coverage` passes against `development` again.
* `pre-commit run --all-files` passes in both repositories.
* The three npm criteria have every part in step: the check, its
  registration in `scripts/audit/registry.py`, the specification
  under `docs/audits/`, the line in `docs/audits/README.md`, their
  lines in `FROZEN_METADATA`, `FROZEN_ISSUE_TITLES` and the frozen
  column table in `scripts/tests/test_metadata.py`, and tests in
  `scripts/tests/test_packaging.py`.
* No `REPO_OVERRIDES` entry was needed, or any that was added
  carries a stated reason.
* A push to `hunkydory` runs `npm ci` and its tests on a static
  runner without a node download.
* hunkydory appears on the VS Code Marketplace, or phase 7 records
  why it does not.
* The human review queue for hunkydory is live and the operator can
  work through `src/` and `test/`.

### Documentation index maintenance

One row added to `docs/plans/index.md`, dated 2026-09-11, linking
this plan, with a one-line intent and a status from the shared
vocabulary. The row carries the whole-plan status and reaches
`Complete` only once every phase has completed, been abandoned or
been superseded.

### Future work

* A second TypeScript repository is the point to generalise from:
  whether Biome stays the choice, whether `tools/check-node.sh`
  becomes a template under `templates/`, and whether the npm
  dependency criteria need to handle workspaces or monorepos.
* `release-process` measures a Python package. A TypeScript arm --
  or a language-neutral restatement -- is worth considering once
  there is more than one non-Python release to describe.
* The GitLab docker executor image at `33fl/static_runner.yml:630`
  is still `debian:12` after phase 4. Somebody should decide whether
  it follows.
* hunkydory's `test/corpus.ts` points by default at a sibling
  `kerbside-patches` checkout, so the corpus check's verdict depends
  on which branch that checkout happens to be on. It degrades
  gracefully and says so, but a fixture inside the repository would
  be better.
* `recount-patch.py` in `kerbside-patches` and hunkydory's
  `src/diff.ts` implement the same counting rules in two languages.
  They were verified to agree on 175 patches, but nothing keeps them
  agreeing.

### Bugs fixed during this work

Two patches in `kerbside-patches` were found to have hunk headers
that disagreed with their bodies while the counting rules were being
developed: `patch137-horizon-requires-setuptools.patch` was rejected
outright by `git apply` with "corrupt patch at line 25", and
`patch097-kolla-ansible-fixed-proxy-cert.patch` carried a trailing
context line git was silently ignoring. Neither was referenced by an
`ORDER` file. Both were corrected in
`shakenfist/kerbside-patches#1683`, which is where the recounter
itself landed.

No issue tracker entries in this repository relate to this plan;
`scope-coverage`'s failure against `development` is expected to
arrive as an audit-filed issue rather than a hand-written one, and
phase 1 closes it.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
