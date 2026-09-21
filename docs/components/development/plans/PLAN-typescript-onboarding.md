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

**The runners could not run npm** when this plan was written on
2026-09-11. No workflow anywhere in the fleet used `setup-node`,
`npm ci` or `npm install`; the mermaid-lint template recorded the
reason, that running it "from the upstream container keeps chromium
and a node toolchain off the runners", and that jsdom "pulls in an
undici that needs a newer node than the runners carry". The static
runners booted `debian:12`, which packages `nodejs 18.20.4`. Debian
13 packages `nodejs 20.19.2`. Both were confirmed against the archive
rather than assumed.

Both halves of that have since gone: the fleet was replaced on
2026-09-12 and boots the release named by
`static_runner_debian_release`, and took `nodejs` and `npm` as base
packages on 2026-09-13. See phase 4. This paragraph is left as the
statement of the problem the plan was written to solve, dated rather
than rewritten, and the rationale it quotes has been reworded in the
files it quotes from.

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

Taken together with the version question, this means the static
runners must be on `debian:13`. That move landed separately in
`33fl` before this phase began -- see phase 4 -- so what this plan
contributes is the packages, not the release. hunkydory runs fine
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
already named in `package.json`, and a `VSCE_PAT` secret.

Until both exist nothing can be *published*, which is why this phase
is sequenced last. D7.1 later split the phase on exactly that seam:
the packaging half needs neither, and landed; only 7b is held.

Phase 7 revised this decision in two further ways, recorded here so a
reader citing D3 is not misled. `VSCE_PAT` is an **environment**
secret on a tag-protected `release` environment, not a repository
secret: see D7.2. And D7.4 attaches the `.vsix` to a GitHub release
*alongside* Marketplace publishing; what D3 rejected was attaching it
as the *only* channel.

Phase 7b then superseded the first of those revisions entirely.
There is no `VSCE_PAT` and no environment secret of any kind: the
publish job mints a short-lived Entra token from its own GitHub OIDC
identity. See *Decided: Entra, and the publish job moves into a
container*. The decision D3 records -- that publishing to the
Marketplace is in scope -- is unaffected.

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

### D5. 33fl is not touched by this plan's author until released

Another session was editing `33fl` concurrently when this plan was
written, so the runner phase below specified the change and its
risks but was not to be executed until that work had landed and the
operator said so. **The operator released it on 2026-09-13.** Phase
4 is executable; it still runs in plan order, after phases 2 and 3.
Nothing else in the plan writes to that repository.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Register hunkydory in the audit scope | Complete | a7f4798 (#125) |
| 2. hunkydory adopts the local tooling | Complete | hunkydory 73cdca7 (#1) |
| 3. npm dependency criteria | Complete | b8e8fd2 (#126) |
| 4. Static runners gain node | Complete | 33fl bc50c52a, deployed; b86f2bb (#127) |
| 5. hunkydory CI and the fleet workflows | Complete | hunkydory 3d556a6 (#7) |
| 6. Human review onboarding | Complete | hunkydory e216f93 (#8), onto `develop` via 3d556a6 (#7) |
| 7. Marketplace release | In progress | 7a: hunkydory 6da49c1 (#9) |
| 8. Push audit | In progress | |

Phase 4 was blocked on D5 and was released on 2026-09-13; see that
decision for what changed. Phase 5 depended on phase 4, because a CI
workflow that runs `npm ci` on a runner without npm is a workflow
that fails on arrival.

Phases 2, 3 and 6 had no such dependency and could proceed in any
order. Phase 1 went first regardless: see its section.

Phase 7 was marked `Blocked` on the publisher account and
`VSCE_PAT` from D3. Its planning survey on 2026-09-14 confirmed the
prerequisite is still unmet, and split the phase accordingly: D7.1
takes the half that does not need the account and lands it now, and
holds the half that does. The phase section carries the split.

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

**Landed 2026-09-13.** The audit went from six passes and nine
failures to fourteen passes and five failures against hunkydory. The
five that remain all need a `.github/workflows/` directory or a
repository setting, so they are phase 5 and phase 7 work.
`llm-context-lint-ci` is the one to watch: this phase closed its
pre-commit half, and it stays red until phase 5 adds the CI route.

`tools/check-node.sh` deliberately does not run the corpus check,
because that needs a sibling `kerbside-patches` checkout which will
not exist in CI. The strongest test hunkydory has is therefore the
one CI will never run, which is worth revisiting in phase 5.

### 3. npm dependency criteria

Three checks per D4, following the shape
`docs/audits/README.md` and the template's worked brief describe. A
criterion is five files, and there is a sixth here because the tests
are their own module:

- a `Check` subclass with `id`, `spec` and `issue_title` as class
  attributes;
- registration in `CHECKS` in `scripts/audit/registry.py`;
- a specification page under `docs/audits/`;
- a line in `docs/audits/README.md`;
- a line each in `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
  `scripts/tests/test_metadata.py`, which
  `test_audit_metadata_matches_the_frozen_table` and
  `test_issue_titles_match_the_frozen_table` assert equality
  against. No `FROZEN_COLUMN_NAMES` line: that table is keyed off
  `Check.column`, which a criterion declares only where it shares a
  spec page with another, and each of these three has its own;
- tests in `scripts/tests/test_npm_dependencies.py`, their own
  module rather than an addition to `test_packaging.py`, covering
  pass, fail and not-applicable. `test_metadata.py` also asserts
  that every check is reachable from some test module.

Adding three checks without touching the frozen tables fails
`pre-commit run --all-files`, which is the first item on this plan's
own review checklist, so the phase would fail its own gate. This is
the same staleness `PLAN-scope-coverage.md:433` corrected in
`PUSH-AUDIT.md`; the four-part framing survived into an earlier draft
of this plan, and into `PLAN-TEMPLATE.md` until phase 8's findings
pull request corrected it there too.

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

**Landed 2026-09-13.** All three criteria pass against hunkydory
and report not-applicable with a reason everywhere else, so the phase
filed no issues. Review found two false-failure bugs before merge: a
workflow was read as text, so a step named "do not use npm install
here" failed a repository whose only npm command was `npm ci`; and
`npm-shrinkwrap.json` was skipped as a foreign lockfile when it is
npm's own format and takes precedence over `package-lock.json`. Both
are fixed and pinned by tests. The suite went from 1070 to 1093.

### 4. Static runners gain node

**Released by the operator on 2026-09-13; see D5.** It was held
until then because another session was editing `33fl`.

**Most of this phase was already done by that other session, and
this plan described it wrongly.** It called the work a re-image. It
is not: `33fl`'s own `worktree-debian-13-static-runners` branch had
already landed the Debian move, parameterised as
`static_runner_debian_release: 13` in
`group_vars/all/static_runners.yml`, and the operator replaced every
static runner on 2026-09-12. The fleet was on Debian 13 before this
phase started.

So the line numbers above were stale and only one item remained:
`nodejs` and `npm` joining the base package list. That is
`33fl` `bc50c52a`, pushed to `master` and deployed on 2026-09-13.

The three verification questions are answered:

- The GitLab docker executor's image is no longer hardcoded. It
  reads `--docker-image debian:{{ static_runner_debian_release }}`,
  so it moves with the fleet rather than needing its own decision.
- `yq` via `pip --break-system-packages`, the shared `docker.yml`
  and the claude CLI install all work on trixie, and so does the
  rest of the base package list. The fleet was rebuilt on Debian 13
  and is serving jobs, which answers this empirically rather than by
  inspection.

**What `apt` installs follows the runner's release, not this
plan's wish.** `nodejs` on Debian 13 is node 20.19.2; on Debian 12
it would have been 18.20.4, which reached upstream end of life in
April 2025. Because the fleet was replaced first, the package change
lands on Debian 13 everywhere and the mixed pool this plan would
otherwise have created does not arise. A future release bump
reopens that window, so `static_runner.yml` carries the warning
beside the package list rather than only here.

Landing node on the runners also falsifies half of the mermaid-lint
rationale this plan quotes as evidence in the Situation section, in
four files where it is load-bearing prose:
`templates/mermaid-lint/README.md`,
`templates/mermaid-lint/mermaid-lint.sh`, `tools/mermaid-lint.sh`
and `docs/audits/mermaid-lint-ci.md`. A node toolchain goes onto the
runners deliberately, and node 20.19.2 is no longer "older than jsdom
wants".

Calling that a rewording rather than a reversal was too glib. The
node-version claim was what ruled out the *lighter* path -- a
parse-only checker with a supplied DOM, no rendering and no browser
-- so with node 20 on the runners that blocker is gone and "jsdom is
not viable" moves from settled to untested. The chromium argument
still justifies rendering, but it does not by itself justify
rendering over parsing. The DOMPurify argument survives untouched,
since it is about needing a DOM at all rather than about node's
version, so a DOM-free checker stays excluded. The decision does not
change today and the container stays; what the four files must say
is that the jsdom option is no longer ruled out and that nobody has
measured it -- neither that it would work nor that it would not.

It is part of this phase rather than future work because
`templates/` is copied into ten repositories, and a template that
justifies itself with a fact the fleet has reversed is judged as the
code it becomes.

**Phase 5 now waits on a deploy rather than on a rebuild.** The
week of runner recycling this plan budgeted for has already been
spent: the fleet is on Debian 13, so the only thing between here and
npm on the runners is running `static_runner.yml`. The package task
is ordinary `apt` state and applies to existing runners at the next
playbook run, unlike the release variable, which only governs
instances the reconcile creates.

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

A `release.yml` that packages and publishes on a tag, and a
`RELEASE-SETUP.md` recording the one-time configuration. Planned in
detail on 2026-09-14; the rest of this section is that plan.

#### What the survey found

Seven checks against the tree, of the claims this section and D3
make.

**The prerequisite is still unmet, and it is the only real block.**
As of 2026-09-14 `shakenfist/hunkydory` has no repository secrets,
no GitHub environments and no tags. Nothing has quietly appeared
since D3 was written, so the account and `VSCE_PAT` remain the
operator's to create.

**`vsce` is installed by nothing.** `package.json` declares a
`package` script that runs `vsce package`, but `@vscode/vsce`
appears in neither `package.json` nor `package-lock.json` -- the
lockfile pins 13 packages and `node_modules/.bin` holds only `tsc`
and `tsserver`. `npm run package` therefore fails on a clean
checkout, which is exactly what a runner has. The checked-in
`hunkydory-0.1.0.vsix` was built out of band. **No criterion sees
this**, because all three npm checks from phase 3 read *imports*:
`npm-undeclared-direct-dependency` reports "None of the 9
transitive packages is imported directly" and passes while the
repository's own packaging command is broken. This section did not
mention it; step 1 below fixes it first, because nothing downstream
can produce a `.vsix` until it is fixed.

**The template contradicts this section.** The publish job in
`templates/release-automation/release.yml` runs on
`[self-hosted, static]` with `environment: release`, which is the
one thing the argument below forbids. Phase 7 therefore adapts the
template rather than copying it, and says so in the workflow's
header comment where the fleet's convention is that a copied
template names its source.

**The ephemeral lane is already reachable from this repository.**
`secret-scan.yml` runs on `[self-hosted, vm, debian-13, s]` and
those labels are declared in `.github/actionlint.yaml`, so "an
ephemeral VM runner" needs no new fleet work and no new label.

**No criterion will ever ask hunkydory for `RELEASE-SETUP.md`.**
`ReleaseProcess` in `scripts/audit/checks/packaging.py` returns
not-applicable on `not repo.props['has_pyproject_toml']`. The
original wording here -- "the `RELEASE-SETUP.md` the fleet's release
criterion expects" -- contradicted this section's own closing note
and has been corrected above: the file is written because a human
needs the runbook, not because anything measures it.

**That same skip switches off five release-safety checks that have
nothing to do with Python.** `ReleaseProcess.check` calls
`release_asset_issues`, `release_dispatch_guard_issues`,
`release_workspace_issues`, `dist_agreement_issues` and
`release_container_path_issues`, all of them after the
`pyproject.toml` skip. They are language-neutral: the first exists
because a `download-artifact` with no `name`/`path`, plus an
`action-gh-release` defaulting `fail_on_unmatched_files` to false,
shipped an empty release; the second because an unguarded dispatch
reaches a job that an `environment:` key marks as publishing.
D7.4's `github-release` job is exactly the shape the first was
written for. So hunkydory is the one repository in the fleet with a
release workflow, a dispatch trigger and a high-value publish
secret, and none of the fleet's controls for that combination apply
to it. D7.5's "one release is not a pattern" is sound for the
*packaging* half of `release-process` and does not reach this half.
Four of the five apply here. The definition of done carries
stand-in assertions for `release_asset_issues`,
`release_dispatch_guard_issues`, `release_workspace_issues` and
`dist_agreement_issues`; `release_container_path_issues` is
genuinely inapplicable, being specific to `gh-action-pypi-publish`'s
container mounts. Future work carries the generalisation.

**Phases 4, 5 and 6 had landed but the Execution table still read
`In progress`, `In progress` and `Not started`.** Corrected at
source as part of this planning commit, with merge references. The
audit against hunkydory reported 28 pass, 1 fail, 26
not-applicable when that was written on 2026-09-14, the single
failure being the `review-coverage` backlog phase 6 deliberately
opened. That count moves with the review queue rather than with
this plan; see phase 8's Audit outcome.

#### Decisions

**D7.1. Split the phase; land the half that is not blocked.** The
account gates *publishing*, not packaging. So phase 7a -- the vsce
dependency, `release.yml`, `RELEASE-SETUP.md` -- lands now and is
exercised by `workflow_dispatch`, and phase 7b -- create the
account, add the environment secret, push the tag -- waits for the
operator. The alternative is to hold the whole phase, which was
rejected because every defect this phase can contain lives in the
build half, and that half is testable today. It also keeps the
plan's last executable phase, the push audit, from being hostage to
an Azure DevOps signup.

The split still stands, but its claim that "every defect this phase
can contain lives in the build half" did not survive implementation.
That sentence is left above rather than quietly edited, so the
argument can be judged against what happened. Whether the publish
lane carries node at all is a defect in the held half that no 7a run
can reach, because the dispatch guard stops a dispatch getting
there. See *Answered: the publish lane has neither node nor npm*;
step 7a.6 settled it, and the answer is that the held half contains
a defect 7a merged.

**D7.2. Publish off the static pool, exactly as argued below.**
Concretely: a `build` job on `[self-hosted, static]` running
`npm ci` and `npm run package`, uploading the `.vsix` as an
artifact; a `publish` job on `[self-hosted, vm, debian-13, s]` with
`environment: release`, which downloads that artifact and runs
`vsce publish --packagePath`.

The invariant for the publish job is that **no lifecycle script
executes in the job that holds the token**. The original wording was
"runs no `npm ci`", which implementation found unimplementable -- the
job needs the vsce binary -- so it runs `npm ci --ignore-scripts`.
See *What implementation found*.

Both publishing jobs carry `if: github.event_name == 'push' &&
startsWith(github.ref, 'refs/tags/v')`. Both clauses are needed: an
unguarded `workflow_dispatch` on a branch reaches a job whose
`environment:` key makes it a publishing job, and publishes whatever
is on that branch. The fleet has a criterion for exactly this --
`release_dispatch_guard_issues` in
`scripts/audit/checks/packaging.py`, which reports ref-only guards
separately from missing ones -- but it is switched off here; see the
five release-safety helpers behind the `pyproject.toml` skip, in the
survey above.

**D7.3. `vsce publish --packagePath`, not bare `vsce publish`.**
Bare `vsce publish` repackages from the working tree, so the
artifact that ships is not the artifact that was built and
inspected. Passing the built `.vsix` makes the two the same object.

**D7.4. Attach the `.vsix` to a GitHub release as well.** The
fleet template already creates a GitHub release, so this is nearly
free, and it is what makes the plan's success criterion -- "appears
on the VS Code Marketplace, **or** phase 7 records why it does not"
-- survivable: if the account never happens, there is still a
downloadable artifact rather than nothing. This is the decision most
likely to be argued with, because D3 chose Marketplace publishing
*instead of* attaching a `.vsix`. D3 was rejecting it as the
*only* distribution channel; adding it alongside costs one job that
the template supplies anyway. Drop it if that reading is wrong.

**D7.5. `release-process` does not grow a TypeScript arm here.**
Already in Future work; one release is not a pattern, and the
criterion would be written against a single example.

#### Step plan

Steps 7a.1 to 7a.4 are done; see *What implementation found*. 7a
merged on 2026-09-14, and 7a.5 and 7a.6 both ran on 2026-09-16, so
all of 7a is now done. 7b.0 was answered on 2026-09-21 -- Entra,
and the publish job into a pinned container -- so nothing waits on
a decision any more. **7b.1 is code and is next**; 7b.2 is the
operator work that follows it, and 7b.3 the tag. See *Decided:
Entra, and the publish job moves into a container* for what those
two answers turned out to mean.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a.1 | low | sonnet | none | In hunkydory: add `@vscode/vsce` to `devDependencies` and refresh `package-lock.json` with `npm install`. Verify `npm ci && npm run package` produces `hunkydory-0.1.0.vsix` on a clean checkout with no network fetch of vsce itself. Check vsce's own `engines.node` against the repository's `engines.node: ">=20"` and against the node the static runners carry (Debian 13's packaged node, per D1) -- if vsce needs newer, say so and stop rather than raising `engines.node`. Re-run the three npm criteria: they read imports, so they should not move. |
| 7a.2 | medium | sonnet | none | Write `.github/workflows/release.yml` with the three jobs from D7.2 and D7.4. Adapt `templates/release-automation/release.yml` rather than copying it: the publish job moves off `[self-hosted, static]`, and the header comment says the template is the source and why this file deviates. `permissions: {}` at the top, least privilege per job. Scripts longer than about five lines go in `tools/`, per the fleet convention. actionlint runs from pre-commit against `.github/actionlint.yaml`; 7a needs no new labels, because `[self-hosted, vm, debian-13, s]` is already declared there, so do not add any. (This scopes 7a, and is not a standing prohibition: 7b's option 2 adds a label deliberately.) |
| 7a.3 | low | sonnet | none | Write `RELEASE-SETUP.md` covering every one-time step: the Azure DevOps publisher account for the `shakenfist` publisher id already in `package.json`, generating a `VSCE_PAT` with Marketplace publish scope, creating the tag-protected `release` environment, and adding the secret to that environment rather than to the repository. Model the structure on `templates/release-automation/RELEASE-SETUP.md`, but write the VS Code Marketplace steps rather than the PyPI trusted-publisher ones. The file goes at the repository root, `RELEASE-SETUP.md`, as it is in every other repository in the fleet -- `ReleaseProcess` tests `repo.exists('RELEASE-SETUP.md')` (`scripts/audit/checks/packaging.py:818`) and the template is root-destined. That criterion skips for hunkydory today, so nothing would catch a divergence, which is exactly why the path is stated here rather than inferred. Separately: reference it from `AGENTS.md` only if a convention changes. |
| 7a.4 | low | sonnet | none | Re-run `pre-commit run --all-files` in hunkydory and the audit (`scripts/audit-check.py --repo-path <clone> --repo-name hunkydory --github-org shakenfist`). The verdict must not move, and the only failure must still be `review-coverage`. Anything else moved is a finding, not a rounding error. |
| 7a.5 | low | sonnet | none | Dispatch `release.yml` on `develop` once 7a has merged. Confirm the `build` job produces the `.vsix` artifact on `[self-hosted, static]`, that `vscode:prepublish` compiles on a real runner with a real `npm_config_cache`, and that both publishing jobs correctly decline to run. Record the run URL here. This is the run D7.1's argument rests on, and it cannot answer the publish-lane question above, because the guard stops a dispatch reaching that job by design. |
| 7a.6 | low | sonnet | none | **Measure whether `[self-hosted, vm, debian-13, s]` carries node, and record the answer in *Answered: the publish lane has neither node nor npm*.** Add a dispatch-only throwaway workflow to hunkydory -- `permissions: {}`, **no `environment:` key**, so it can reach no secret -- whose single job runs on that lane and executes `node --version; npm --version; command -v node npm` without `set -e` stopping at the first absence. `secret-scan.yml` already uses this lane and `workflow_dispatch`, so no new actionlint label is needed. Dispatch it, record the output and the run URL, then delete the workflow in the same PR chain: it is a probe, not a fixture. The point is to reduce 7b.0's question (b) from a three-way guess to either "nothing to do" or "option 2". Do not fold this into `release.yml`'s publish job -- the dispatch guard means that job cannot run until 7b, which is the whole reason the question is open. |
| 7b.0 | -- | operator | -- | **Done, 2026-09-21.** Both questions answered: (a) **Entra, and no PAT is ever minted** -- a PAT bought now lasts about ten weeks against the 1 December 2026 global-PAT retirement, and the Entra path has to be walked either way, so the setup cost is paid once rather than twice; (b) **option 2**, the `debian-13-docker` lane with a pinned `node:22` container, which settles the `engines.node` risk in the same move and needs no work in another repository. Reading the pinned vsce rather than trusting the flag's description then changed the mechanism both answers imply: see *Decided: Entra, and the publish job moves into a container*, which is the authority on what 7b.1 and 7b.2 execute. |
| 7b.1 | medium | opus | worktree | **The repair 7b.0's answers carry, in hunkydory, before any account exists.** `release.yml`'s publish job runs `npm ci` on a lane with neither node nor npm, and carries a comment claiming "this runner carries Debian 13's node 20, so that's satisfied today" -- a defect in merged code, not a gap in future work. Move the job to `[self-hosted, vm, debian-13-docker, s]`, declare that label in `.github/actionlint.yaml` (7a.2's brief deliberately withheld it pending this decision), add `id-token: write` to the job's `permissions`, and drop `VSCE_PAT` from the workflow entirely. The install and the publish move into `tools/publish-marketplace.sh`, per the fleet's CI-script rule (`AGENTS.md` in this repository: anything longer than about five lines goes in `tools/` and is called from the workflow), pinning `node:22-trixie-slim` **by digest as well as tag** the way `tools/mermaid-lint.sh` pins mermaid-cli. Use `docker run` from that script rather than a job-level `container:` key, matching mermaid-lint, and note the consequence the comparison hides: a job-level `container:` would inherit the runner's environment, while `docker run` does not, so the script must pass `-e ACTIONS_ID_TOKEN_REQUEST_URL -e ACTIONS_ID_TOKEN_REQUEST_TOKEN -e AZURE_CLIENT_ID -e AZURE_TENANT_ID` explicitly. Both the exchange and the publish run inside the container, because the host lane has no node to run them with. The token comes from the client-assertion exchange set out in *Decided* and reaches vsce through the `VSCE_PAT` environment variable rather than `-p`, keeping it out of the process table; that is a different thing from `secrets.VSCE_PAT`, which goes. Say in the script's header why the flag is not used, and put the next-vsce-bump warning in `renovate.json` as a `prBodyNotes` entry on `@vscode/vsce` as well as in the header -- a bump touches `package.json`, not `tools/`, so a header comment is read by nobody at the moment it matters. Add a `customManagers` entry there too, for the image digest, which none of Renovate's stock managers read out of a shell script. Order inside the container matters and is the falsifiable part: mint **before** installing, into an unexported shell variable, `::add-mask::` the result immediately, then strip `ACTIONS_ID_TOKEN_REQUEST_URL` and `ACTIONS_ID_TOKEN_REQUEST_TOKEN` from the environment of both the install and the publish. The pair mints publishing tokens for the life of the job, so the code to keep it away from is vsce's dependency tree, not `npm ci --ignore-scripts`, which runs no package code at all. `RELEASE-SETUP.md` is rewritten for the Entra path: the PAT steps go, because leaving them as an alternative is how an operator stands up the architecture 7b.0 just declined. **Nothing here is reachable by CI** -- the job is gated on a tag and the `release` environment -- so verify what can be verified locally (the container runs `npm ci --ignore-scripts` and `vsce --version`; actionlint and pre-commit pass) and say plainly in the pull request that the exchange itself is unexercised until 7b.3. Commit subject: `Publish through Entra, in a pinned container.` |
| 7b.2 | -- | operator | -- | **Hold.** Nothing in a repository can do this. Create the Entra app registration; add a **federated** credential on it with issuer `https://token.actions.githubusercontent.com`, subject `repo:shakenfist/hunkydory:environment:release` and audience `api://AzureADTokenExchange` -- the portal's GitHub Actions scenario fills the issuer in, `az ad app federated-credential create` and the Graph API do not, and getting it wrong surfaces as an opaque Entra rejection during a real release; add that identity as a member of the `shakenfist` Marketplace publisher; create the `release` environment restricted to `v*` tags, carrying `AZURE_CLIENT_ID` and `AZURE_TENANT_ID` as environment **variables** (neither is a secret). While in the repository settings, also add the two missing token secrets that hunkydory #22 is about -- `prune-reviews.yml` and `renovate.yml` have never succeeded without them, and HD-9 has no other owner. And resolve hunkydory #21, by adding a ruleset restricting `v*` tag creation -- that is the work, and closing the issue is its consequence. It matters more than its `high` grading suggests: with the environment's tag rule it is one of only two things standing between a push and a publish. `RELEASE-SETUP.md` as rewritten by 7b.1 is the step-by-step. |
| 7b.3 | low | sonnet | none | Once 7b.2 is done, and **before pushing the tag**, check the environment 7b.2 built rather than trusting it: `gh api repos/shakenfist/hunkydory/environments/release/secrets` must report `total_count: 0`, and the matching `/variables` call must report exactly `AZURE_CLIENT_ID` and `AZURE_TENANT_ID`. A secret added by hand is the regression this architecture exists to remove, and catching it after the tag is too late, because recovery is a version bump. Then tag `v0.1.0`, watch the run, and record the outcome in this section -- either the Marketplace listing URL, or what failed. If the account never arrives, record that instead and cite the attached `.vsix`. **If it fails, the recovery is a version bump, not a re-tag**: `43b7f59`'s build job asserts the tag matches `package.json`, and the Marketplace rejects a republished version, so a deleted and re-pushed `v0.1.0` either fails the same way or is refused on the far side. Bump to `0.1.1` and tag that. The jobs can also disagree -- `github-release` needs `publish-marketplace`, so Marketplace-succeeded-and-release-failed is the only split possible, and it is repaired by attaching the `.vsix` to the existing release by hand rather than by re-running anything. Add both to `RELEASE-SETUP.md`'s troubleshooting while the reasoning is fresh. |

#### Risks and mitigations

**`@vscode/vsce` drags a large dependency tree into a repository
whose pitch is that it has no runtime dependencies.** It is a
`devDependency`, so nothing reaches a user's VS Code, and
`.vscodeignore` already governs what enters the `.vsix`. But the
lockfile goes from 13 packages to something much larger, Renovate
starts proposing updates to all of it, and
`npm-pin-indirect-dependencies` now has real work to do. *Mitigation:*
step 7a.1 reports the new package count so the change is visible
rather than discovered later, and the README's "no runtime
dependencies" claim is checked for whether it is still true as
written.

**A PAT with Marketplace publish rights is the highest-value secret
in either organisation's repositories.** *Mitigation:* D7.2 in full
-- environment secret, tag-protected environment, ephemeral runner,
and no `npm ci` in the job that can read it. The check is
falsifiable and is in the definition of done.

**Tag protection may not exist on this repository.** The
`environment: release` protection rule is what makes "not readable
from a job on a branch" true; without it the secret is readable from
any workflow run that names the environment. *Mitigation:* step
7b.2 creates the environment with its tag rule, and 7a.2 must not
pretend the workflow is safe before that exists -- `RELEASE-SETUP.md`
states the ordering. 7b.0's answer does **not** relax this. With no
secret on the environment there is nothing sitting at rest to leak,
but the tag rule remains the only gate on the ref: an
environment-scoped OIDC subject carries no ref, so Entra will mint a
publishing token for any run that GitHub let into the environment.
The protection rule is load-bearing for the same reason it always
was.

#### Definition of done

* `npm ci && npm run package` produces a `.vsix` on a clean checkout
  of `develop`, with no `npx` download of vsce.
* `@vscode/vsce` appears in `package.json` `devDependencies` and in
  `package-lock.json`.
* `pre-commit run --all-files` passes in hunkydory, actionlint
  included, against the committed `.github/actionlint.yaml`.
* No `runs-on` in `release.yml`'s publish job contains `static`,
  and **neither** `release.yml`'s `publish-marketplace` job **nor**
  `tools/publish-marketplace.sh` runs an install without
  `--ignore-scripts`, or runs an `npm run` script. Both surfaces,
  deliberately. This bullet has now been rewritten twice for the
  same reason in opposite directions -- anchored on the job it
  passed vacuously once 7b.1 moved the install into the script, and
  anchored on the script alone it would pass again the moment
  somebody put an install back into the workflow step. Two greps
  cannot be satisfied by the install moving between them.
* `publish-marketplace` is the only job carrying
  `environment: release`, **and the only place in `release.yml`
  granting `id-token: write`** -- checked against the workflow-level
  `permissions:` block as well as each job's, since a workflow-level
  grant would hand it to every job without appearing in any of
  them. The second half is the one that matters, and
  it is the successor to the `secrets.VSCE_PAT` clause 7b.1 made
  vacuous: `id-token: write` is now what lets a job obtain a
  publishing credential, so a second job holding it -- on the static
  pool especially -- would be one line of YAML away from being able
  to publish. See *Why the publish job stays off the static pool*.
* The `release` environment carries no secrets, and carries
  `AZURE_CLIENT_ID` and `AZURE_TENANT_ID` as variables. Checked
  against the live environment once 7b.2 has run, not against the
  plan: a secret added by hand is exactly the regression this
  replaces. 7b.3 runs it, before the tag rather than after.
* The `ACTIONS_ID_TOKEN_REQUEST_*` pair is absent from the
  environment of both the install and the publish, and the exchanged
  token is present only for the publish. That ordering is deliberate
  and is the falsifiable form of the `--ignore-scripts` argument
  under federation: the pair mints publishing tokens for the life of
  the job, and the code worth keeping it away from is vsce's
  dependency tree, not `npm ci --ignore-scripts`, which executes no
  package code at all. So the exchange runs **first**, into an
  unexported shell variable, and the pair is stripped from
  everything after it.
* The minted token is passed to the Actions runner as
  `::add-mask::` before anything else runs. GitHub auto-redacts only
  values that came from the `secrets` context, and this one
  deliberately never does.
* Both publishing jobs are guarded on
  `github.event_name == 'push'` *and* on a `refs/tags/v` ref, so a
  `workflow_dispatch` on a branch cannot reach either.
  `github-release` carries the guard for a different reason from
  `publish-marketplace`: it holds no publishing secret, but it has
  `contents: write` and creates a public release. This stands in
  for `release_dispatch_guard_issues`.
* The `github-release` job's `download-artifact` names both `name:`
  and `path:`, and its upload sets `fail_on_unmatched_files: true`.
  These stand in for `release_asset_issues`, which does not run
  against this repository.
* `github-release` downloads into `${{ runner.temp }}` rather than
  into the workspace, and its `files:` glob names that same
  directory. This is load-bearing rather than stylistic: the job
  runs on the persistent static pool and does not check out, so a
  workspace glob could attach a `.vsix` some earlier run left
  behind, and `fail_on_unmatched_files` does not catch that -- it
  catches zero matches, not extra or wrong ones. These stand in for
  `release_workspace_issues` and `dist_agreement_issues`.
* `RELEASE-SETUP.md` names the publisher id, the environment name,
  the federated credential's subject and audience, and the tag rule,
  and states that the environment must exist before the first tag is
  pushed. It names no PAT scope: 7b.0 declined that architecture and
  7b.1 removed the steps, so a document still describing how to mint
  one is a document an operator can follow into it.
* The audit against hunkydory reports no failure other than
  `review-coverage`.
* Either hunkydory is listed on the VS Code Marketplace, or this
  section records why it is not and the `.vsix` is attached to a
  GitHub release.

#### What implementation found

Phase 7a landed as hunkydory `43b7f59` (#9), recorded in the
Execution table's `Merged` column because phase 8 assembles the
accumulated diff from that column. Four things the planning
survey did not reach, found by building the thing:

**Adding vsce was not enough to make `npm run package` work.**
Nothing built the TypeScript before `vsce` ran, so packaging failed a
second time on a clean tree even once the dependency existed. The
`README` documents `npm install && npm run package` as the whole
flow, so this was a real gap in a promised path rather than an
artefact of testing. Fixed with `vscode:prepublish`, vsce's own
convention, which needed no new script.

**The `.vsix` shipped the repository to every user.** `vsce ls`
listed 27 files. Six belonged in an extension: the three compiled
modules under `out/src/`, plus `package.json`, `README.md` and
`LICENSE`. The other 21 were repository infrastructure -- the nine
workflows, `.github/actionlint.yaml`, `.pre-commit-config.yaml`, the
three `tools/` scripts, `AGENTS.md`, `ARCHITECTURE.md`,
`PUSH-AUDIT.md`, `REVIEWS.md`, `RELEASE-SETUP.md`, `renovate.json`
and `biome.json` -- most of them put there by phases 2, 5 and 6,
none of which had reason to think about packaging. `.vscodeignore`
is now an allow-list, which takes `vsce ls` to those same six files
-- 8 entries and 14,682 bytes in the archive, which adds
`extension.vsixmanifest` and `[Content_Types].xml` -- and makes a
new file have to be named before it can reach a user. This is the
failure mode phase 6's `review-scope.toml` was deliberately shaped
to avoid, in a file nobody thought to apply the same reasoning to.

An allow-list inverts the failure mode rather than removing it: it
can now ship too little, and a missing file fails at a user's
install rather than in CI. What bounds that here is the form it
takes. The allow rule is `!out/src/*.js`, a glob over the compiled
output directory, so a new module under `src/` is packaged the
moment it compiles -- no commit has to remember to name it. The
exception is a new *kind* of shipped file (an icon, a
`CHANGELOG.md`, a bundled grammar), which does have to be added by
hand. Note the rule names `*.js` rather than `out/src/**` on
purpose: a trailing `**/*.map` deny does **not** override an earlier
negation in vsce's matcher. That was tried, and the maps shipped
anyway.

**The publish job cannot run vsce without installing it.** D7.2 says
the job "runs no `npm ci`", which is unimplementable as written: the
job needs the binary. It runs `npm ci --ignore-scripts`, which
removes the lifecycle-script execution D7.2 is actually guarding
against, and keeps the lockfile-pinned version rather than the
floating one `npx` would fetch at publish time. The deviation is
commented in the workflow.

`--ignore-scripts` narrows the exposure; it does not close it. The
job then *runs* vsce, so vsce's whole newly-enlarged, Azure-flavoured
dependency tree executes with the token in the environment.
Install-time execution is removed; run-time execution is what
publishing is. What bounds the residual risk is the rest of D7.2
taken together -- the lockfile pin, so the code that runs is the code
that was reviewed; Renovate watching that pin; the ephemeral runner;
and the tag-protected environment. **The pin is load-bearing for
security, not only for reproducibility**, which is what the next
person who proposes floating the vsce version needs to know.

**Nothing tied the tag to the shipped version.** `vsce` publishes
the version inside the `.vsix`, and unlike the template's
`setuptools_scm` nothing here derives that from the tag, so a
mismatched tag would quietly republish the old version. The build job
now compares the two and fails.

The three figures the step briefs asked to be reported rather than
discovered later:

* **`package-lock.json` went from 13 packages to 305.** That is the
  cost of vsce, all of it `devDependencies`, none of it in the
  `.vsix`. Renovate now has 305 packages to watch rather than 13.
* **hunkydory never claimed to have no runtime dependencies.** The
  first risk asked for that claim to be re-checked; grepping
  `README.md`, `AGENTS.md`, `ARCHITECTURE.md` and `docs/` for
  "dependenc" returns nothing, so no wording became false. The
  extension does still have no runtime dependencies as a matter of
  fact.
* **The audit after 7a reported 28 pass, 1 fail, 26
  not-applicable** -- identical to the pre-7a figures, with
  `review-coverage` still the only failure. Nothing moved, which for
  this phase is the expected result: 7a added a workflow, a runbook
  and a dependency, and the criteria that would notice any of those
  are the Python ones that skip, and the five release-safety helpers
  that go dark behind the same `pyproject.toml` skip.

**`github-release` was already clean of the workspace.** `43b7f59`
downloads the artifact into `${{ runner.temp }}/vsix/` and points
`files:` at that directory, so the two assertions the definition of
done adds for `release_workspace_issues` and `dist_agreement_issues`
are met by the implementation rather than pending against it. The
job's header comment reaches the same conclusion from the other
direction -- it does not check out because downloading into
`runner.temp` is the cheaper way to get a directory nothing else has
written to -- which is worth noting because it means the property
holds by reasoning that was written down, not by luck.

**Both publishing jobs are dispatch-guarded.** `43b7f59` carries
`if: github.event_name == 'push' && startsWith(github.ref,
'refs/tags/v')` on `publish-marketplace` and on `github-release`, so
a `workflow_dispatch` on a branch reaches neither. This is enforced
by review rather than by measurement, since
`release_dispatch_guard_issues` does not run against this
repository.

**Step 7a.5 dispatched `release.yml` and confirmed the above by
measurement rather than review.** Run:
https://github.com/shakenfist/hunkydory/actions/runs/35078839428,
`develop` at `b2d3014`, 2026-09-16. The `build` job ("Build the
.vsix") succeeded in 24s on `[self-hosted, static]`.
`vscode:prepublish` ran `npm run build` (`tsc -p .`) and compiled
cleanly before `vsce package` ran, with `npm_config_cache` pointed
at a real `runner.temp` path rather than the default. The `.vsix`
packaged eight files at 14.33 KB, and the `vsix` artifact uploaded
at 14,253 bytes. The "Check the tag matches package.json" step
was skipped, as it is guarded on `github.event_name == 'push'` and
this run is a `workflow_dispatch`. Both publishing jobs -- "Create
GitHub Release" and "Publish to the VS Code Marketplace" -- also
skipped, exactly as designed. As the step brief anticipated, this
run says nothing about the publish lane's node question below: the
guard stops a dispatch reaching that job, so
`[self-hosted, vm, debian-13, s]` was never touched by this run.
That is step 7a.6's job.

The run did settle one adjacent question by measurement. The static
pool carries node v20.19.2 and npm 9.2.0, and `npm ci` there emitted
`EBADENGINE` warnings from the vsce Azure dependencies that want node
22 -- warnings, not failures, and the build succeeded. So the
`engines.node` risk recorded below is real rather than theoretical,
and it is now known to be live on the pool phase 4 provisioned as
well as on whatever lane 7a.6 finds.

#### Answered: the publish lane has neither node nor npm

**Settled by measurement on 2026-09-16.** Step 7a.6's throwaway
probe ran on `[self-hosted, vm, debian-13, s]`, on ephemeral runner
`sfcbr-OO1IC0zAPJiDuZ2a`:
https://github.com/shakenfist/hunkydory/actions/runs/35141854203

```
node: command not found
npm: command not found
neither on PATH
```

The history, because it explains how the gap opened. Phase 4 put
`nodejs` and `npm` on the **static** runners -- `33fl` `bc50c52a` in
`static_runner.yml` -- and said nothing about the ephemeral VM lane.
D7.2 then put the publish job on `[self-hosted, vm, debian-13, s]`,
and implementation found that job needs `npm ci --ignore-scripts` to
get the vsce binary. Nothing established that lane has node, and it
does not.

**This is a defect in what 7a merged, not merely a gap in what 7b
must build.** `release.yml`'s publish job runs `npm ci
--ignore-scripts` as its first command, so the job fails on its
first real invocation. The same job carries a comment asserting that
"this runner carries Debian 13's node 20, so that's satisfied
today". That is false, and it is load-bearing: it is the sentence
that made the `engines.node` risk below look bounded. Both the code
and the comment are hunkydory's to repair, and whichever option
7b.0 picks has to carry that repair with it. **7b.0 picked option 2
and 7b.1 carries the repair**; the rest of this section is the
state of the question before it was answered, kept because the
measurement and the elimination of option 3 are still the record.
See *Decided: Entra, and the publish job moves into a container*.

What the search found, so the next person does not repeat it:

* `33fl` has no `nodejs` outside `static_runner.yml`. The VM images
  are not built there at all -- `private-ci`'s
  `conductor/imagebuilder.py` builds them from checkouts of
  `shakenfist/actions` and `shakenfist`.
* `shakenfist/actions` installs no `nodejs` package. Its many hits
  for "node" are cluster nodes.
* The only two consumers of `[self-hosted, vm, debian-13, *]` in
  this repository are `secret-scan.yml`, which wants gitleaks, and
  `templates/pin-indirect-dependencies/`, which wants pip.
* Docker-capable VM runners carry a **separate** `debian-13-docker`
  label, used by `mermaid-lint.yml`. Plain `debian-13` is not
  docker-capable, so "run it in a `node:22` container" is not
  available on the lane D7.2 named.

This is the failure phase 5 was sequenced after phase 4 to avoid --
this plan's own words at the Execution table are that "a CI workflow
that runs `npm ci` on a runner without npm is a workflow that fails
on arrival" -- reintroduced on a different lane. It is worse here,
because the publish job is gated on a tag and the `release`
environment, so it cannot run until 7b: the failure would surface on
the first real release, after the operator has stood up the account.
That falsifies D7.1's claim that "every defect this phase can contain
lives in the build half".

The options, for a decision rather than a guess. **Option 2 was
chosen on 2026-09-21**; this list is kept as the reasoning it was
chosen from:

1. **Put node on the VM image.** Correct, and matches what phase 4
   did for the static pool, but it is work in another repository and
   D5 territory.
2. **Move the publish job to `debian-13-docker` and run vsce in a
   pinned `node:22` container.** Guarantees the runtime and disposes
   of the `engines.node` risk below at the same time. Costs a label
   in hunkydory's `.github/actionlint.yaml` and a `tools/` script.
3. **Have the build job ship vsce.** Upload `node_modules` beside
   the `.vsix` so the publish job installs nothing. **Eliminated by
   7a.6**: it removes the need for npm but not for node, so it would
   only have helped had the lane carried node without npm. The lane
   carries neither. It was in any case the worst of the three on
   D7.2's own terms, and that is the stronger objection: it moves
   305 packages of executable code, materialised on the shared
   static pool, into the job that holds the token, and runs it. The
   lockfile pin stops bounding what executes, because what executes
   is the pool's tarball rather than the reviewed and pinned tree --
   and *What implementation found* is explicit that the pin is
   load-bearing for security, not only for reproducibility.
4. **`actions/setup-node` in the publish job.** The measurement
   newly admits this one: with nothing at all on the lane the
   toolchain has to come from somewhere, and this is the
   conventional answer. It needs no work in another repository and
   no new actionlint label. Against it, the runner is ephemeral, so
   every publish pays a fresh toolchain download through
   `cache.home.stillhq.com`; and it fetches and executes a toolchain
   inside the one job holding `VSCE_PAT`, which is the property
   D7.2 spent the `--ignore-scripts` argument protecting. Option 2
   pins the same runtime without that.

A diagnostic step running `node --version && npm --version` as the
first step of the publish job was written for exactly this, so that
whatever happens the diagnosis is in the log rather than an
unexplained `npm: command not found`. **It never landed.** The
commit, `8351a5b`, was authored eight minutes after #9 merged and is
orphaned on `origin/typescript-onboarding-phase7`; the merged
`release.yml` has no such step. An earlier revision of this section
asserted that `43b7f59` carries it, which phase 8's survey found to
be false. It was in any case the right instinct in the wrong place:
the step sits inside the one job the dispatch guard keeps
unreachable until 7b, so it would report the answer only once it is
too late to choose differently. Step 7a.6 measured the same two
commands on the same lane instead, from a dispatch-only job holding
no secret, which is why the answer above arrived before 7b.0 rather
than after the first release. The probe,
`.github/workflows/runner-probe.yml`, landed as hunkydory #14
(`19733c2`) and is still on `develop`: 7a.6's brief required it be
removed in the same pull request chain, and that did not happen.
**Phase 8's step 8.0 owns the deletion**, and D8.2's hunkydory range
covers both `19733c2` and the merge that removes it.

#### Decided: Entra, and the publish job moves into a container

Answered by the operator on 2026-09-21: **(a) Entra, and no PAT is
ever minted. (b) Option 2 -- `debian-13-docker` with a pinned
`node:22` container.** What follows is what those two answers turned
out to mean once the pinned vsce was read rather than trusted,
because the mechanism is not the one the question assumed.

**`--azure-credential` cannot use GitHub's workload identity
federation in vsce 3.9.2.** `@vscode/vsce/out/auth.js` builds a
fixed `ChainedTokenCredential` of five credentials:
`EnvironmentCredential`, `AzureCliCredential`,
`ManagedIdentityCredential`, `AzurePowerShellCredential`,
`AzureDeveloperCliCredential`. `WorkloadIdentityCredential` -- the
one that reads `AZURE_FEDERATED_TOKEN_FILE`, and the only credential
in `@azure/identity` that consumes a federated token directly -- is
**not in that chain**, although the pinned `@azure/identity` 4.13.2
does ship it. `EnvironmentCredential` does not fill the gap: its
`credentialEnvironmentVariables` are `AZURE_CLIENT_SECRET`,
`AZURE_CLIENT_CERTIFICATE_PATH` and `AZURE_USERNAME`, and nothing
else. So on the paths vsce actually offers, "Entra" is either an
`az login` that `AzureCliCredential` picks up, or a long-lived
client secret read by `EnvironmentCredential`.

Neither survives the two answers above. A client secret is a stored
credential that expires, which is the property that disqualified the
PAT -- it would buy two years instead of ten weeks and change
nothing structural. The `az` CLI is on neither the VM image nor a
`node:22` container, and putting it on the image is option 1: work
in another repository, which is exactly what option 2 was chosen to
avoid. Taken together the two answers appear to contradict each
other, and that is worth stating plainly rather than discovering at
publish time.

**The way through is that vsce does not care where the token came
from.** `out/publish.js`'s `getPAT()` returns `options.pat` when
`-p` is given and `getAzureCredentialAccessToken()` when
`--azure-credential` is -- into the same variable, used the same
way. An Entra access token and a PAT are interchangeable strings at
that seam. So the publish job performs the client-assertion exchange
itself. In full, because two of these details decide whether it
works at all and neither is guessable:

* `GET "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=api://AzureADTokenExchange"`
  with `Authorization: Bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN`.
  **The audience is not optional.** GitHub's default audience is the
  repository owner URL, so a request that omits it yields a token
  Entra rejects on audience, and the string must match the federated
  credential 7b.2 creates. The request token is the other half: the
  URL alone authenticates nothing, and it is not named anywhere else
  in this plan.
* `POST https://login.microsoftonline.com/$AZURE_TENANT_ID/oauth2/v2.0/token`
  with `grant_type=client_credentials`, `client_id=$AZURE_CLIENT_ID`,
  `scope=499b84ac-1321-427f-aa17-267ca6975798/.default` (the scope
  `auth.js` names, read from there rather than from documentation so
  the two cannot drift), `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`
  and the JWT from the first call as `client_assertion`.
* The result goes to vsce in the **`VSCE_PAT` environment
  variable**, which is what `-p` defaults to, rather than on the
  command line where it would sit in the container's process table.
  Two senses of that name now coexist and the plan means both: no
  `secrets.VSCE_PAT` in the workflow is the architectural decision,
  while `VSCE_PAT` as vsce's token-passing mechanism survives and
  carries the exchanged token.

No `az`, no stored credential, and no dependency beyond node 22's
global `fetch` -- which means the exchange runs **inside the
container**, since 7a.6 measured that the host lane has no node.

**Re-read two files when vsce is next bumped**, not one.
`auth.js`, for whether `WorkloadIdentityCredential` has joined the
chain, in which case this exchange can be deleted in favour of
`--azure-credential`. And `publish.js`'s `getPAT()`, for whether
`-p` still accepts an Entra access token at all: that seam is the
more fragile of the two, because a release could split the auth
handler so the flag sends a bearer token and `-p` sends a PAT,
leaving the flag surface identical and this design broken with no
signal from `auth.js`. Reading both is the check, rather than
reading release notes, and it is the same discipline 7a established
for the flag surface itself.

That discipline has to live where the person who triggers the risk
will see it. A vsce bump touches `package.json` and
`package-lock.json`, not `tools/`, so a header comment in a script
is read by nobody at the moment it matters -- and once HD-9 is
fixed, Renovate will propose those bumps automatically. 7b.1
therefore puts it in `renovate.json` as a `prBodyNotes` entry on
`@vscode/vsce`, so the warning appears in the body of the bump pull
request itself. The script headers keep their copy for a reader who
arrives from the other direction.

This changes what 7b.2 asks the operator for. There is no PAT and no
environment secret. What has to exist is an Entra app registration,
a **federated** credential on it whose subject is
`repo:shakenfist/hunkydory:environment:release` and whose audience
is `api://AzureADTokenExchange`, and that identity added as a member
of the Marketplace publisher. The `release` environment is still
created and still restricted to `v*` tags, and **that tag rule is
the only thing gating the ref**. An earlier revision of this section
claimed the environment gated the job twice over, on the reasoning
that Entra would refuse a token minted on the wrong ref. It would
not. When a job declares `environment:`, the subject GitHub mints is
`repo:<org>/<repo>:environment:<name>` and carries **no ref
component at all** -- which is exactly why the federated credential
can name the environment, and exactly why Entra cannot tell one ref
from another. The two gates are one gate.

So HD-1 keeps its severity in full, and HD-2 gains rather than
loses. On an auto-created unprotected `release` environment, any run
that reaches the job mints a real publishing token, and with no
ruleset on `v*` tags that is any tag on any ref. The worst case is a
*successful* publish of arbitrary content under the `shakenfist`
publisher id -- the same blast radius the PAT had, not a smaller
one.

What federation does buy is narrower and still worth having: there
is no long-lived credential at rest. Nothing sits in GitHub to be
exfiltrated by a job on the same runner, read out of a misconfigured
environment months later, or rotated on a calendar; the token exists
for about an hour, inside one container, and only for a run GitHub
already admitted to the environment. That is a real improvement on
the PAT, and it is a different improvement from the one first
claimed here. The environment carries `AZURE_CLIENT_ID` and
`AZURE_TENANT_ID` as non-secret variables.

The `engines.node` risk recorded above closes on the publish lane
and stays open elsewhere. The container is `node:22-trixie-slim`,
pinned by digest as well as tag the way `tools/mermaid-lint.sh`
pins mermaid-cli, so `@azure/identity`'s `">=22.0.0"` is satisfied
where it is actually asked. `ci.yml` and the `build` job keep
running on the static pool's node 20, where nothing loads the auth
path, so the fleet-wide question in *Future work* is unchanged.

#### Risks found during implementation

**The `VSCE_PAT` architecture has a deadline.** Azure DevOps retires
*global* personal access tokens -- the "all accessible organizations"
scope vsce requires -- on **1 December 2026**. A token minted before
then stops working on that date regardless of its own expiry. The
announcement is
<https://devblogs.microsoft.com/devops/retirement-of-global-personal-access-tokens-in-azure-devops/>;
`microsoft/vsmarketplace#2121`, "Support publishing extensions with
organization-scoped PATs due to global PATs being retired", tracks
vsce's lack of support for the organisation-scoped tokens that
replace them; it was open when checked on 2026-09-15. (An earlier
revision of this section cited it as `microsoft/vscode#322741`,
which is where the issue started before it was transferred.) The
replacement in the pinned vsce 3.9.2 is `publish --azure-credential`,
"Use Microsoft Entra ID for authentication"; there is no `--oidc`
flag in this version, whatever the surrounding commentary says. That
flag surface was established by running
`node_modules/.bin/vsce publish --help` against the pinned version,
which is the check to repeat when vsce is next bumped. It needs an
Entra app registration, a GitHub federated credential, and
that identity added to the Marketplace publisher. *Mitigation:*
`RELEASE-SETUP.md` leads with it. **Decided on 2026-09-21: Entra,
and no PAT is minted at all.** See *Decided: Entra, and the publish
job moves into a container* for what that turned out to require,
which is not `--azure-credential` on its own.

**vsce's transitive Azure dependencies already ask for a newer node
than the runners have.** `@vscode/vsce` declares `engines.node
">= 20"`, while `@azure/identity` and its neighbours declare
`">=22.0.0"`. npm warns `EBADENGINE` and installs anyway, and
`@vscode/vsce/out/auth.js` was verified to load and run on node
20.19.2. An earlier revision of this paragraph concluded "so this
works today" and put the failure at publish time; both halves of
that need narrowing. The node-20 premise holds on the **static
pool** only, which is where that measurement was taken and where
`ci.yml` runs. The publish lane carries no node at all -- see
*Answered: the publish lane has neither node nor npm* -- so on that
lane the `engines.node` question does not arise yet, and whichever
option 7b.0 picks decides which version it is asked against. 7b.0
picked option 2, so on that lane the answer is now node 22 and this
risk is closed there; see *Decided: Entra, and the publish job moves
into a container*. *Mitigation:* none available in this repository
for the lanes that remain -- the fix is the fleet moving to a newer
node. Recorded so the next reader is not
surprised, and so the two lanes are not read as one.

**The `getPAT()` seam is first exercised after the operator work.**
The whole design rests on `-p`/`VSCE_PAT` and `--azure-credential`
resolving into the same value, which was read out of pinned vsce
3.9.2 rather than assumed -- but reading is not running, and nothing
between here and 7b.3 runs it. If that seam has moved, the signal is
a failed publish at the one moment a failure is most expensive.
*Mitigation:* the re-read discipline in `renovate.json`'s
`prBodyNotes` keeps it true across bumps, which is the drift case;
the pin keeps it true today, which is the version case. What neither
covers is the reading having been wrong in the first place. Recorded
rather than mitigated, because the only real mitigation is
hunkydory #26, a verify-only dispatch mode, which needs the account
to exist before it can verify anything against it.

**The container pull is unverified on the lane that will do it.**
7b.1's verification is local: the image runs, the install works and
the exchange forms a request Entra parses. None of that exercises
`[self-hosted, vm, debian-13-docker, s]` pulling
`node:22-trixie-slim` from Docker Hub. That is a different registry
from the `ghcr.io` one `tools/mermaid-lint.sh` established on the
same lane, a different egress path, and Docker Hub rate-limits
anonymous pulls per source IP -- and the runners share an IP.
*Mitigation:* none applied, and this is the same shape as the defect
7a.6 was invented to pre-empt, so it is recorded rather than
dismissed. The cheap version of 7a.6's answer applies if it is
wanted: a dispatch-only job on that lane that pulls the image and
exits. Left undone because the failure is loud, immediate and
recoverable -- a failed pull fails the job before anything is
published, unlike the node defect, which would have burned a version
number. 7b.3 is where it is first exercised for real.

**hunkydory's release tags are unsigned.** The fleet template has a
fourth job that Sigstore-signs the tag with gitsign; D7.2 decided a
three-job shape without considering it. Not implemented, recorded in
the workflow header as a known gap rather than an oversight. Whether
the fleet's tag-signing convention should apply here is an open
question, not a decided omission.

#### Why the publish job stays off the static pool

D1's own argument is that the static pool is "a static shared runner
rather than an ephemeral per-job VM", shared by every repository in
both the shakenfist and mach33labs organisations, with filesystem and
process state that outlives a job. `VSCE_PAT` can publish under the
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

7b.0 changed what this argument is about without weakening it, but
the three bullets do not all survive a blanket substitution, so take
them one at a time.

* The ephemeral-runner bullet carries over unchanged. The reason a
  publishing credential should not be obtainable on the shared pool
  does not depend on what kind of credential it is.
* The second bullet's mechanism **moves from GitHub to Entra**.
  There is no environment secret to be unreadable from a branch, and
  the `ACTIONS_ID_TOKEN_REQUEST_*` pair is not its successor: GitHub
  injects that pair into any job declaring `id-token: write`, on any
  ref, environment or not. What stops a branch job publishing is
  that the federated credential's subject is
  `repo:shakenfist/hunkydory:environment:release`, so the token a
  job without `environment:` receives -- subject
  `...:ref:refs/heads/...` -- is refused by Entra. The environment's
  tag rule is what decides which runs reach the environment at all.
  The two are complementary rather than redundant, which is the same
  distinction *Decided* makes when it says the two gates are one
  gate for a job that already declares the environment.
* The `--ignore-scripts` bullet carries over as written, and 7b.1
  sharpened it: the exchange now happens before the install, and the
  pair is stripped from the environment of both the install and the
  publish.

One conclusion is sharper than before: the build job's `.vsix` is
still produced on the shared pool -- tracked as hunkydory #23 --
while the thing that can publish it now lives for an hour inside a
container on an ephemeral VM.

Note that `release-process` as written measures a Python package and
will stay not applicable; whether it grows a TypeScript arm is future
work rather than part of this plan.

#### Back brief gate

The gate before 7a.2 has been passed. What was agreed and built is
three jobs: `build` on `[self-hosted, static]`,
`publish-marketplace` on `[self-hosted, vm, debian-13, s]` holding
the token, and `github-release` on `[self-hosted, static]`. D7.4 --
the question this plan expected to be argued with -- was accepted
without argument, so the GitHub release job stays.

The second gate, **7b.0**, was passed on 2026-09-21: Entra with no
PAT, and the publish job into a pinned `node:22` container on
`debian-13-docker`. The gate earned its place. Both answers change
`release.yml` rather than merely configuring around it, and the
reason for agreeing them before 7b.2 stands anything up -- that the
account otherwise gets configured for an architecture that then
changes -- turned out to be the live risk rather than a
precaution: reading `auth.js` showed the two answers pulling against
each other, and the resolution changes what the operator creates
from a token to a federated credential. Had the account been stood
up first, it would have been stood up wrong. No gate remains; 7b.1
is code and 7b.2 is the operator's, in that order.

### 8. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of every phase, per
the shared block. Phases landing in `hunkydory` and `33fl` record
`<repo> <sha> (#pr)` in the `Merged` column.

Planned at high effort on 2026-09-16; the rest of this section is
that plan.

#### Scope

**In scope.** Three repositories, because this plan landed work in
three. Everything the `Merged` column records, plus the two
development pull requests the column omits (below). The audit is of
the accumulated diff of the whole plan, not of any one phase.

**Out of scope.** Phase 7b, which is operator-held and may never
run -- D7.1 split the phase precisely so that this one would not be
"hostage to an Azure DevOps signup". If 7b later lands, it is
audited in its own pull request against hunkydory's `develop`, and
this section is not reopened. Also out: fixing anything the audit
finds. Findings land as their own pull request, per the shared
block.

#### What the survey found

Eight checks against the three trees, of the claims this section
makes and of the `Merged` column this phase reads. Six found
something. The false claims are corrected at source as part of the
planning commit, so a later step does not rediscover them.

**The `Merged` column records a head commit where it needs a merge
commit.** Phase 7's cell reads `hunkydory 43b7f59 (#9)`.
`43b7f59` has one parent: it is #9's head, not its merge. The
merge is `6da49c1`. The shared block is explicit -- "A single
commit is only ever enough when it is a merge commit" -- because
`git diff 43b7f59^1 43b7f59` is the last commit of the pull
request rather than the pull request. Corrected above.

**And phase 6's cell records a merge into a feature branch.** The
check that found the cell above is not "does the sha have two
parents" but "is it what put the phase on the default branch", and
only the second one catches this. `e216f93` (#8) has two parents,
but its base was `typescript-onboarding-phase5`, not `develop`; it
reached `develop` as the second parent of `3d556a6` (#7), and
`compare/e216f93...3d556a6` is ahead 1 / behind 0. So phase 6's
entire diff -- `prune-reviews.yml`, `.gitignore`,
`.vscode/review-scope.toml`, `AGENTS.md`, `REVIEWS.md`,
`tools/ci-prune-reviews.sh`, `tools/review-tracking.sh` -- is
already inside `git diff 73cdca7 3d556a6`. The cell now names both
commits, and D8.2's hunkydory range drops `e216f93` because listing
it alongside `3d556a6` would audit a whole phase twice. Checked the
right way, the remaining five cells hold: `a7f4798`, `b8e8fd2` and
`b86f2bb` merged to `main` here, `73cdca7` and `6da49c1` to
hunkydory's `develop`.

**Two development pull requests this plan landed are not in the
`Merged` column at all.** `decaa4d` (#118, which created the plan
file) and `9fe50ee` (#130, the phase 7 plan). Both are plan-document
changes rather than phases, which is why no cell claimed them, but
the audit's documentation wave reads plan prose and they are this
plan's work on this repository. They are named in the ranges below
rather than added to the table, which tracks phases.

This survey said three, and named `d102e9f` (#128, "Correct what
phase 2 asserts, and record phase 1") as the third on the strength
of its branch name, `phase2-correction`. It is not this plan's
work: it touches only `PLAN-image-supply-chain.md` and
`docs/plans/index.md`, and this plan's real phase-2 correction is
`b86f2bb` (#127), separately in the range. The error reached the
range table and stayed there; see DEV-1 in the Audit outcome.

The rule that puts them there, stated so the next reader can check
the range against it rather than against a list: **every merge to a
default branch that this plan caused, phase or prose alike, is in
the range.** Applying it means reading what each merge changed, not
what its branch was called: a sha that exists and is an ancestor of
the default branch has been shown to be a merge, not to be *this
plan's* merge, and `d102e9f` above is what the difference costs.
Two further consequences worth naming, because each looks like an
omission otherwise. The review-mark merges `aa2c55d` (#129),
`9b158f0` (#131) and `0210d1c` (#134) are *not* in the range: each
touches only `.vscode/mikal.weaudit`, its shas file and `REVIEWS.md`,
which is the review-tracking tooling recording that a human read
something, not work this plan did. And the merge of this planning
pull request itself cannot be listed, because it does not exist when
this table is written; step 8.1 adds it once it does, on the same
rule. What it adds is plan prose, which wave 2c reads from the
current tree anyway.

**hunkydory now has a `PUSH-AUDIT.md`, as this section predicted.**
The claim above -- "Neither hunkydory nor 33fl carries a
`PUSH-AUDIT.md` today" -- was true when written and is now false
for hunkydory: phase 2 deployed it in `35b5e3e`, and the
`push-audit` criterion **passes** against hunkydory today rather
than reporting not-applicable. The prediction held, so the
paragraph is retensed rather than deleted; the 33fl half is still
true.

**No hunkydory pull request was push-audited when it landed.**
This is the finding that changes the work. The shared block says a
phase landing in another repository "is audited against that
repository's default branch, as part of the pull request that
lands it", and that "the plan's own push-audit phase cites that
audit rather than re-running it". Checking the bodies of hunkydory
#1, #7, #8 and #9 finds no audit record in any of them -- #1
mentions `PUSH-AUDIT.md` only as a file it is adding. So there is
nothing to cite, and this phase runs hunkydory's audit itself
rather than pretending the citation exists.

**33fl's phase 4 commit is a legitimate direct landing.**
`bc50c52a` has one parent and touches one file,
`static_runner.yml`, +16 lines. That is the shared block's "where
the phase landed directly, every commit of the phase" case rather
than the defect the first finding describes. 33fl remains outside
the audit fleet -- it appears in neither `scripts/audit/registry.py`
nor `docs/audits/` -- and carries no `PUSH-AUDIT.md`, so the
section's plan for it stands unchanged.

**Every diff command in the runbook is written against
`main...HEAD`.** `PUSH-AUDIT.md:27` says so outright. That shape
assumes an unmerged branch, and everything this phase audits is
already on a default branch, so the ranges have to be reconstructed
from merge commits and the commands rewritten. D8.2 says how.

**Phase 7 is `In progress`, and one of its statements is false.**
7a.5 and 7a.6 have since both run, and 7b is operator-held. Phase
7's *What implementation found* asserted that "`43b7f59` runs `node
--version && npm --version` as the first step of the publish job".
It does not: the commit that added that step, `8351a5b`, was
authored eight minutes after #9 merged and sits orphaned on
`origin/typescript-onboarding-phase7`. Corrected at source, which
is why no line here cites a line number for it. This phase does not
wait on 7b -- see Scope -- but it does depend on 7a.5 and 7a.6
having run, because their results are text this audit reads. D8.4.

**The Situation section's figures are start-state and still
correct as history.** "51 checks: 6 pass, 9 fail, 36 not
applicable" was the pre-plan verdict. Today hunkydory reports 55
checks with no failure other than `review-coverage`, which phase 6
opened deliberately and which reopens whenever a reviewed file
changes. Nothing to correct; recorded so the next reader does not
think the Situation has drifted.

#### Decisions

**D8.1. Three audits, not one.** The work landed in three
repositories with three default branches and, now, two runbooks.
Merging the diffs into a single review would apply this
repository's briefs -- written for audit automation with a
sixteen-repository blast radius -- to a VS Code extension and to an
Ansible role. Each repository is audited against its own runbook
where it has one, and findings are collected centrally.

**D8.2. Reconstruct each range as a list of merge commits, and
audit a concatenated diff.** Note that 33fl's default branch is
`master`, not `main`; an earlier revision of this table said `main`,
and the planning survey caught it -- no step of this phase has run,
so this is a planning check, not an audit result. The runbook's
`main...HEAD` does not work on merged history. For each
repository, produce

```
for m in <merges>; do git diff "$m^1" "$m"; done > phase8-<repo>.diff
```

and run wave 1's greps over that file instead of over a range.

**Each mechanical check keeps its own pathspec.** Most of the greps
in `PUSH-AUDIT.md`'s Mechanical checks section are scoped by
pathspec rather than by pattern: `'*.py'` for the 120-column check,
`'scripts/*.py'` for new imports, `'docs/audits/compliance.md'` for
the compliance page, and `'docs/audits/*.md'` with
`':!docs/audits/compliance.md'` for the generated-block check.
Flattened into one file those scopes vanish, and the checks stop
meaning what they say: the compliance.md grep matches every added
line in the diff, and the criterion-spec grep matches
compliance.md's own regenerated rows. So build one file per scope,
carrying the check's pathspec through the same loop

```
for m in <merges>; do git diff "$m^1" "$m" -- <pathspec>; done \
  > phase8-<repo>-<scope>.diff
```

and run each grep over its own file. This keeps the runbook's
scoping without reintroducing a range. A check with no pathspec
reads the unscoped file above.

The concatenation is a superset rather than a net diff: a file
touched by two phases appears twice, and a line a later phase
corrected shows both states. That is the right bias for an audit,
which is looking for what was introduced, and it is why wave 2 reads
the *current* tree for the same paths rather than the concatenated
patch. The
alternative -- a scratch branch replaying every phase onto the
merge-base -- was rejected as archaeology that can itself be wrong.

The ranges, from the `Merged` column plus the omissions above:

| Repository | Default | Merges to audit |
|---|---|---|
| `shakenfist/development` | `main` | `decaa4d` (#118), `a7f4798` (#125), `b8e8fd2` (#126), `b86f2bb` (#127), `d102e9f` (#128), `9fe50ee` (#130), and `254bb83` (#137) |
| `shakenfist/hunkydory` | `develop` | `73cdca7` (#1), `3d556a6` (#7), `6da49c1` (#9), `19733c2` (#14), and `43beb85` (#17) |
| `mach33labs/33fl` | `master` | `bc50c52a` (direct, not a merge -- diff against its single parent) |

`d102e9f` (#128) is in the development row **in error**: its branch
name reads like this plan's phase 2 but it belongs to the
image-supply-chain plan. The row is left as the range the audit
actually used, because that is a fact about what was covered; see
DEV-1 in the Audit outcome below.

`e216f93` (#8) is deliberately absent from hunkydory's row: it
merged into `typescript-onboarding-phase5`, so `3d556a6` already
carries all of it, and listing both would audit phase 6 twice. The
probe, #14, is there because the plan says its landing and its
deletion are in scope; step 8.0 makes the deletion exist.

**D8.3. hunkydory's audit is run now, not cited.** The survey found
no audit was performed on #1, #7, #8 or #9. Re-running four pull
requests' worth of work in one pass is what the accumulated-diff
rule asks for anyway, so this costs little beyond honesty about why
it is happening. Record in this section that the per-pull-request
audits the shared block expects did not occur, because that is a
process finding about this plan, not about hunkydory's code, and it
is the kind of thing that recurs silently.

**D8.4. 7a.5 and 7a.6 run before this phase, not after.** Both have
now done so, on 2026-09-16, and both wrote text into the plan that
this audit's documentation wave reads -- 7a.5 a dispatch run URL,
7a.6 the answer to the open question, which turned out to be a
defect rather than a clean bill of health. Auditing the plan before
they landed would have meant auditing prose known to be incomplete.
This is the decision
most likely to be argued with: it makes phase 8 wait on two steps
of a phase whose other half may never finish, and the counter-
argument is that the audit should simply take the plan as it
stands. The case for waiting is that both steps are hours of work,
not weeks, and that the audit's whole value is reading the final
text.

**D8.5. 33fl gets the mechanical wave only.** Sixteen lines of
Ansible in a repository outside the audit fleet does not warrant
four judgment sub-agents. Wave 1's greps, plus a single reader
checking the change against `static_runner.yml`'s surrounding
conventions and against what D1 said it would do. Say so in the
record, rather than implying a full audit ran.

**And 33fl needs the operator's own checkout.** It is in a different
organisation, `mach33labs`, and the token the fleet automation runs
with cannot read it -- `gh api repos/mach33labs/33fl` returns 404
from this environment. Steps 8.1, 8.2 and 8.5 therefore depend on a
local clone the operator supplies; nothing in the phase can fetch
one. If it is unavailable when the phase runs, 8.9 records that
33fl was not scoped and why, which is the shared block's "say what
it could not scope" case, and the other two repositories proceed
unchanged. This is a gap in the record, not a blocked phase.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8.0 | low | sonnet | none | In hunkydory, delete `.github/workflows/runner-probe.yml` as its own pull request onto `develop`. Step 7a.6's brief required the probe be removed in the same pull request chain that landed it, and it was not: the file is still on `develop`, and its header tells the next reader to delete it once the answer is recorded in a section this plan has since renamed to *Answered: the publish lane has neither node nor npm*, so the instruction now points at nothing. The deletion is unconditional -- 7a.6 has reported and the probe holds no secret -- and it must merge before 8.2 builds the diffs, because its merge is in D8.2's hunkydory range. Record the merge sha in that table. Commit subject: `Delete the step 7a.6 runner probe.` |
| 8.1 | low | sonnet | none | **Prerequisite gate, not an audit step.** Confirm 7a.5 and 7a.6 have landed and that this plan records their results. If either is outstanding, stop and report rather than proceeding -- D8.4. Also confirm `git fetch` has run in all three repositories and that each local default branch is at its remote: `PUSH-AUDIT.md`'s own note is that a stale local `main` silently widens the audit, and this session has hit that failure three times. Finally, fill in the two shas D8.2's table cannot carry until they exist: this planning pull request's merge into `main`, and step 8.0's probe-deletion merge into hunkydory's `develop`. If 8.0 has not merged, stop. Commit subject: none bar the two shas; this step produces a go/no-go, not a change. |
| 8.2 | low | sonnet | none | Build the three concatenated diffs per D8.2 into a scratch directory, plus one per-scope file for each pathspec-scoped mechanical check as D8.2 requires, and report each one's size and the file list it touches. Use the merge list in D8.2's table verbatim; do not rederive it from `git log`, which cannot tell a phase merge from an unrelated one. Sanity-check that `shakenfist/development`'s diff contains `scripts/audit/checks/npm_dependencies.py` and `scripts/tests/test_npm_dependencies.py` (phase 3, 2,277 insertions), that hunkydory's contains `.github/workflows/release.yml` and `.vscodeignore`, and that 33fl's is 16 lines of `static_runner.yml`. If any is missing, the range is wrong -- stop. |
| 8.3 | medium | sonnet | none | Wave 1 of `PUSH-AUDIT.md` against `shakenfist/development`'s diff: lint and the full test suite on `main`, then every grep in the Mechanical checks section rewritten to read 8.2's diff files rather than `git diff main...HEAD` -- each pathspec-scoped grep reading its own per-scope file, per D8.2, because a grep run against the flat file has silently lost its scope. Pay particular attention to the `FROZEN_ISSUE_TITLES` and shared-block-version checks, because phase 3 added criteria and phase 4 edited `templates/mermaid-lint/`. Report findings; fix nothing. |
| 8.4 | medium | sonnet | none | Wave 1 against hunkydory's diff, using **hunkydory's own** `PUSH-AUDIT.md`, whose greps phase 2 rewrote for a VS Code extension with no server. Run from a hunkydory checkout at `origin/develop`. `npm ci && npm run lint && npm test` is the lint-and-test half; `npm run corpus` needs a sibling kerbside-patches checkout and is expected to skip without one -- say which happened. Report findings; fix nothing. |
| 8.5 | low | sonnet | none | 33fl, mechanical only, per D8.5, from the operator-supplied checkout that decision describes -- if there is none, record that and skip, do not try to fetch one. Wave 1's language-agnostic greps over the 16-line diff, plus a read of `bc50c52a` against `static_runner.yml`'s surrounding conventions and against what D1 of this plan said phase 4 would do. There is no `PUSH-AUDIT.md` in 33fl and it is outside the audit fleet, so state which runbook was used and that no judgment wave ran. Report findings; fix nothing. |
| 8.6 | high | opus | none | Wave 2a and 2d (code quality, security) against `shakenfist/development`, reading the current tree for the paths 8.2 listed rather than the concatenated patch -- D8.2 says why. The blast radius framing in `PUSH-AUDIT.md`'s preamble applies in full: phase 3 added criteria that file and close issues fleet-wide. Check in particular that the npm criteria cannot file against a repository with no `package.json`, and that nothing phase 3 added reads the network. Spawn 2a and 2d as the runbook intends. Report findings; fix nothing. |
| 8.7 | high | opus | none | Wave 2b and 2c (tests, documentation) against `shakenfist/development`. 2c has the most to do: this plan changed `docs/plans/index.md`, `PLAN-TEMPLATE.md` adjacent prose, four `docs/audits/` specs and its own 1,300-line plan file, and the documentation brief is the one that catches a page phase 2 made wrong and phase 5 never revisited. Check the plan's own internal consistency too -- the survey above found a head commit recorded as a merge and a false claim about `43b7f59`, both of which a documentation wave should have caught. Report findings; fix nothing. |
| 8.8 | high | opus | none | Wave 2 against hunkydory, all four briefs, using hunkydory's `PUSH-AUDIT.md`. This is where D8.3's four unaudited pull requests actually get read: phases 2, 5, 6 and 7a built the entire repository's fleet integration and none of it has had a judgment pass. Highest-value targets are `release.yml` (a token-holding publish job), `.vscodeignore` (an allow-list that can ship too little), and `tools/`. Report findings; fix nothing. |
| 8.9 | medium | opus | none | **Management session, not a sub-agent.** Collect every finding from 8.3 to 8.8, deduplicate across repositories, and triage each into fix / decline / defer-to-Future-work. Write the outcome into this section: what was audited, what the ranges were, what was found, and -- if nothing was -- say so in one sentence, which the shared block calls a real result. Record that hunkydory's per-pull-request audits did not happen (D8.3) and that 33fl got the mechanical wave only (D8.5). |
| 8.10 | high | opus | worktree | **Findings pull request, `shakenfist/development`.** Fix DEV-1 to DEV-13 and DEV-15 from the Audit outcome table as one pull request onto `main`, separate from this planning commit. Start with DEV-3, `PLAN-TEMPLATE.md:508-513` -- the bullet beginning "Any new or changed criterion has all four of its parts in step", which is the text to grep for if the template has shifted again by the time you read this: it is the root cause and DEV-4 is it reappearing, so correct the template first and then make this plan's `:369`, `:374`, `:1725` and `:1828` agree with it -- a criterion is five files, and `AUDIT_METADATA`, `ISSUE_TITLES` and the column table are derived views rather than tables anyone edits. DEV-11 and DEV-12 are code with tests: in `scripts/test_audit_snapshot.py`, widen `:245`'s `class (\w+)\(Check\):` so an intermediate base class such as `NpmPackageCheck` is matched, or derive the map from `registry.CHECKS` instead of by regex, and **add** a coverage assertion that `set(scheduled)` equals `{check.id for check in registry.CHECKS}`, which is the assertion that would have caught this -- add it alongside `:250`'s read guard and `:255`'s existing `assertEqual(derived, set(audit_snapshot.NETWORK_CHECKS))`, replacing neither, because the existing equality is not what is broken; route `scripts/audit-manage-issues.py:169`'s `details` append through the same `ISSUE_BODY_BUDGET` accounting `render_issue_items` already uses. DEV-15 is test-only. DEV-5 touches two fleet templates, so word it as "the static runners" and say nothing about the VM lanes, which is what `mermaid-lint.yml:86` actually uses. DEV-1 corrects the rule's application, not the range history: D8.2's table stays as the range the audit used. Every `:NNNN` pointer in this table was resolved against the merge result and is correct at that commit, but your own edits shift every line below the first one you make: re-resolve each pointer by grepping the quoted text rather than trusting the number, and work bottom-up through the file. DEV-10 is this defect in its previous incarnation. Also add DEV-14, DEV-16 and hunkydory's five deferred findings -- HD-1, HD-2, HD-9, HD-11 and HD-12, but not HD-14, whose repair 7b.1 already carries -- to this plan's Future work section, filing each issue against the repository that carries the defect -- Future work lives here even where the defect does not. Leave the hunkydory fixes to 8.11. Commit subject: `Fix what the phase 8 audit found.` |
| 8.11 | high | opus | worktree | **Findings pull request, `shakenfist/hunkydory`.** Fix HD-3 to HD-8, HD-10 and HD-13 from the Audit outcome table as one pull request onto `develop`. HD-7 is the only source change: `src/diff.ts:8`'s `HUNK_RE` ends `@@(.*)$`, and neither `.` nor `$` copes with a `\r`, so every CRLF patch file is a silent no-op -- fix the expression and add a CRLF case, because the corpus has none and a fix with no test here is how this survived four pull requests. HD-4 needs a glob that crosses a directory separator, plus a test that a nested module survives `vsce ls`, since 8.8 proved the current one drops it. HD-5 and HD-6 are the two documents that instruct or ship: reconcile `RELEASE-SETUP.md` against `release.yml` as it actually is -- three jobs, `publish-marketplace`, and `npm ci --ignore-scripts` rather than no `npm ci` -- and delete `README.md:55-57`'s claim of a git oracle rather than softening it, because no test invokes git at all. HD-3 wants the fork guard `pr-re-review.yml` already carries. HD-10 is a hand edit of `REVIEWS.md:31`; `prune-reviews` cannot do it, because HD-9 is why that workflow has never once succeeded. Leave HD-1, HD-2, HD-9, HD-11, HD-12 and HD-14 alone: they are deferred, and each needs an operator decision or an issue rather than a patch. Commit subject: `Fix what the push audit found.` |

#### Risks and mitigations

**The concatenated diff double-counts, and an auditor reads an
intermediate state as the shipped one.** Phase 7a's `.vscodeignore`
is the live example: the deny-list and the allow-list both appear
in hunkydory's diff. *Mitigation:* D8.2 puts wave 2 on the current
tree rather than the patch, and 8.2's sanity checks name the files
where this is most likely. The management session in 8.9 rejects
any finding whose evidence is only a superseded hunk.

**Auditing this plan's own prose is self-review.** The same
session that wrote the phase 7 section audits it in 8.7.
*Mitigation:* 8.7 is a sub-agent with the runbook's brief and not
the management session, and it is pointed at two defects the
survey already found so its calibration can be checked against a
known answer. If it misses both, its other findings are worth
less.

**Four unaudited hunkydory pull requests is a lot of surface for
one pass.** *Mitigation:* 8.8 is the only step given all four
judgment briefs and an opus budget, and D8.3 records the situation
so that a finding-heavy result reads as expected rather than
alarming.

**7b may never land, leaving the plan permanently `In progress`
with a completed push audit.** *Mitigation:* Scope says 7b is
audited in its own pull request if it happens. The success criteria
already allow the Marketplace outcome to be "or phase 7 records why
it does not", so a plan that ends with 7b abandoned is a
contemplated ending rather than a failure.

#### Definition of done

* Phase 7's `Merged` cell names `6da49c1`, a two-parent commit, not
  `43b7f59`.
* No statement in this plan says `43b7f59` carries a `node
  --version` step.
* `.github/workflows/runner-probe.yml` no longer exists on
  hunkydory's `develop`, and the merge that removed it is in D8.2's
  hunkydory range.
* The three concatenated diffs exist and each passes 8.2's
  file-presence sanity check, and every pathspec-scoped mechanical
  check read a per-scope file rather than the flat one.
* Wave 1 has run over all three, and hunkydory's used hunkydory's
  runbook rather than this repository's.
* Wave 2 has run over `shakenfist/development` and `hunkydory`;
  this section states that 33fl received the mechanical wave only,
  and why.
* This section records that hunkydory #1, #7, #8 and #9 were not
  audited when they landed.
* Every finding is fixed, declined in writing here, or in Future
  work -- or this section says in one sentence that the audit found
  nothing.
* `pre-commit run --all-files` passes in this repository, and the
  audit against hunkydory reports no failure other than
  `review-coverage`. This bullet originally pinned "one failure,
  and it is `review-coverage`", which is the wrong shape: the count
  moves whenever the review queue does, and it moved twice while
  this phase ran. What has to hold is that nothing else fails.

#### Back brief gate

**Before 8.3 runs, confirm the ranges in D8.2's table.** They are
cheap to agree and expensive to redo: every subsequent step reads
the diffs built from them, and a wrong range produces an audit that
looks complete and is not. That is the failure the shared block
spent a paragraph on, and the survey found one instance of it
already in the column this table is built from.

#### Audit outcome

Run on 2026-09-19. Steps 8.0 to 8.8 executed as written, with the
sub-agents and models the step table names. This section is step
8.9's record, written by the management session rather than by a
sub-agent, as that step requires.

**What was audited.** Three repositories against the ranges in
D8.2's table, which step 8.1 completed with `254bb83` (#137) and
`43beb85` (#17). Step 8.2 built seven diff files rather than
three, because four of the mechanical checks are pathspec-scoped
and D8.2 requires each to read its own file: development
277,778 bytes flat plus `*.py` (88,217), `scripts/*.py`,
`docs/audits/compliance.md` (empty) and `docs/audits/*.md`
(20,945); hunkydory 243,739 bytes; 33fl 1,475 bytes over 34
lines. All three of 8.2's file-presence sanity checks passed.
Wave 1 then ran over all three trees -- 8.3 development, 8.4
hunkydory against hunkydory's own runbook, 8.5 33fl mechanical
only -- and wave 2 over development in two sub-agent pairs (8.6
code quality and security, 8.7 tests and documentation) and over
hunkydory with all four briefs (8.8).

**The headline is that the code is sound and the documentation is
not.** No defect was found in any shipped logic this plan
introduced. development's 1,093 tests pass, at 92% branch
coverage over the new module; hunkydory's suite and its 187 of
187 corpus cases pass; 33fl produced no findings at all. Both
questions 8.6 was given came back clean with proof: the npm
criteria cannot file against a repository with no
`package.json`, because all three inherit a shared
`NpmPackageCheck.applies()` gate that `registry.run_check` calls
before `run()` (`scripts/audit/checks/npm_dependencies.py:754`;
verified against twenty fleet repositories, zero failures), and
nothing phase 3 added reads the network -- its one subprocess
call is `git ls-files` with an argv list, no shell, a `--`
terminator and a timeout.

What the audit did find is a documentation-integrity problem with
a single root cause, plus the pre-existing defects in hunkydory's
fleet integration that D8.3 predicted would be there.

**The root cause is one stale paragraph in `PLAN-TEMPLATE.md`.**
`PLAN-TEMPLATE.md:508-513` still carries the "all four of its
parts" rule for a criterion, which `PUSH-AUDIT.md` was corrected
away from: a criterion spans five files, and `AUDIT_METADATA`,
`ISSUE_TITLES` and the column table are derived views rather than
tables anyone edits. This plan *diagnosed* that staleness at line
355 and fixed it downstream without fixing the template it came
from, so the template kept emitting it -- three of the findings
below are that paragraph reappearing in this plan's own prose.
Fixing the template is therefore the highest-value item in 8.10,
because it is the only one that stops the next plan inheriting
the same error.

**The range this audit used was itself wrong, and the audit
caught it.** `d102e9f` (#128) is in D8.2's development row on the
strength of its branch name, `phase2-correction`, which reads
like this plan's phase 2. It is not this plan's work: it touches
only `PLAN-image-supply-chain.md` (+155/-13) and `index.md`, and
belongs to the image-supply-chain plan. This plan's real phase-2
correction is `b86f2bb` (#127), which is separately in the row.
So `phase8-development.diff` carried 155 lines of another plan's
prose, in violation of D8.2's own rule that the range holds every
merge *this plan* caused. The consequence is a superset, not a
gap: the audit read more than it needed to and no finding arose
from those lines. The range is left as it was used, with a
pointer above, because what a completed audit actually covered is
a fact worth recording accurately; 8.10 corrects the rule's
application, not the history. The management session had verified
that every sha existed and was an ancestor of the default branch
and called the ranges sound -- existence and ancestry, but never
provenance. That is the check the back brief gate should have
made and did not.

**One Definition of done item is unsatisfiable because the
outcome was better than planned.** The last bullet required that
"the audit against hunkydory still reports one failure and it is
`review-coverage`". Re-run on 2026-09-19 the audit reports 55
checks, **29 pass, 0 fail, 26 not-applicable**: the operator
worked the review queue and `review-coverage` now passes at 25 of
25 files reviewed. The bullet is corrected in place below, since
leaving a knowingly false criterion in the section that records
the audit would be the same defect this audit is reporting. The
five other places that assert the old figures are elsewhere in the
plan and are 8.10's work. The plan-wide success criterion -- "no
failures other than `review-coverage`" -- stays true and needs no
change.

**And the count moved again before the findings landed.** Step
8.11 changed nine files and ran the repository's own review prune,
which expired their marks: `REVIEWS.md` now reads 16 of 27 and
`review-coverage` fails again, putting hunkydory back at 28 pass,
1 fail, 26 not-applicable -- the figure this plan asserted all
along, arrived at from the other direction. That is the review
system working rather than a regression, and it is the argument
for pinning no verdict triple anywhere: what is durable is that
nothing other than `review-coverage` fails.

**D8.3: hunkydory #1, #7, #8 and #9 were not push-audited when
they landed, and this phase audited them instead.** The shared
block expects a phase landing in another repository to be audited
against that repository's default branch as part of the pull
request that lands it, with this phase citing that audit rather
than re-running it. No such record exists in any of the four pull
request bodies. Four pull requests built hunkydory's entire fleet
integration -- the local tooling, CI, review onboarding and the
release workflow -- and none had a judgment pass until 8.8 today.
That is recorded here, as D8.3 asked, because it is a process
finding about this plan rather than a defect in hunkydory's code,
and because it is the kind of omission that recurs silently: the
step that would have caught it is the one nobody is blocked by.
The critical finding and two of the three high ones below are in
code those four pull requests shipped; the third is a repository
setting none of them could have set.

**D8.5: 33fl received the mechanical wave only, and found
nothing.** Wave 1's language-agnostic greps over the 34-line
diff, plus one reader checking `bc50c52a` against
`static_runner.yml`'s surrounding conventions and against what D1
said phase 4 would do. No judgment sub-agent ran and there is no
`PUSH-AUDIT.md` in that repository, so the runbook used was this
one's mechanical section. The sixteen added lines are
alphabetical, unpinned, idempotent, match the file's conventions
and do exactly what D1 promised. One piece of context the reader
surfaced for 7b.0 rather than as a finding: the commit's own
comment ties the node version to `static_runner_debian_release`,
so a static runner still on Debian 12 carries node 18, which
reached end of life in April 2025. Node on the static pool is
rollover-dependent rather than guaranteed.

##### Findings

Thirty-one findings survived deduplication, across two
repositories. Severities are the auditing sub-agent's, kept
rather than renormalised so that a reader can tell which brief
raised what. Every bare `:NNN` below is a line of this file as it
stood at `6db89f6`, the commit that recorded this section; 8.10's
fixes moved them, and the table is left citing what the audit
actually read.

| ID | Repo | Severity | Finding | Disposition |
|---|---|---|---|---|
| DEV-1 | development | factual | `d102e9f` (#128) is in D8.2's range on the strength of a misleading branch name; it belongs to the image-supply-chain plan. Range covered a superset; no finding arose from it. | Fix (8.10) |
| DEV-2 | development | factual | The hunkydory verdict is asserted as 28 pass / 1 fail / 26 not-applicable at `:661`, `:744`, `:906` and `:1294`, and `:819` asserts the single `review-coverage` failure without counts. Phase 8's DoD required that failure to exist. It is now 29 / 0 / 26 -- so the four sites need their arithmetic corrected and `:819` needs its meaning changed, since dropping the failure empties the bullet rather than renumbering it. | Fix: DoD bullet corrected here, the other five in 8.10 |
| DEV-3 | development | blocking-grade | `PLAN-TEMPLATE.md:508-513` -- the bullet beginning "Any new or changed criterion has all four of its parts in step" -- carries the superseded four-part criterion rule. Root cause of DEV-4, and still propagating into new plans. | Fix (8.10) |
| DEV-4 | development | factual | Inherited from DEV-3: `:369` and `:1725` require a frozen column-table line that correctly does not exist, and `:374`, `:1725` and `:1828` say the npm tests live in `test_packaging.py` when they are in a new 777-line `test_npm_dependencies.py`. | Fix (8.10) |
| DEV-5 | development | cross-page, ships to fleet | Four mermaid-lint pages promise the runners carry node, in two wordings: `templates/mermaid-lint/README.md:44` and `docs/audits/mermaid-lint-ci.md:120` say "node 20", while `templates/mermaid-lint/mermaid-lint.sh:21` and `tools/mermaid-lint.sh:21` say "the runners carry node" with no version. Both wordings imply a pool-wide guarantee phase 4 did not create, so grepping for "node 20" finds only half the sites. But `mermaid-lint.yml:86` runs on `[self-hosted, vm, debian-13-docker, s]` and phase 4 put node on the **static** pool only. Two of the four are fleet templates. | Fix (8.10) |
| DEV-6 | development | factual | Future-work bullet `:1855` says `33fl/static_runner.yml:630` is still `debian:12`. There is no `debian:12` anywhere in that file; the executor image is at line 736 and reads `debian:{{ static_runner_debian_release }}`. The plan's own phase 4 contradicts the bullet at `:479`. | Fix (8.10) |
| DEV-7 | development | factual | The consolidated Risks section restates two risks phase 4 records as resolved, and `:1776` cites line 229 of `static_runner.yml`, which is a `set_fact`; the loop it means is at 268. | Fix (8.10) |
| DEV-8 | development | spec vs reality | Three criterion specs diverge from their code: the undeclared-dependency spec claims all four false-positive kinds appear in hunkydory when only three do; the pin spec documents `npm install` while the regex also matches `npm i` and `npm add`, and is silent on workspace roots where both siblings are explicit; the `README.md` index row says the lockfile is checked "current" while the spec says currency is explicitly not checked. | Fix (8.10) |
| DEV-9 | development | factual | "sixteen other repositories" at `PUSH-AUDIT.md:7` and `AGENTS.md:82`, and "access to sixteen repositories" at `PUSH-AUDIT.md:449`. The matrix in `consistency-audit.yml:21-46` has 21 entries, so 20 others. `:449` is a different sentence from the other two -- it is about a token's blast radius, not about what this repository audits -- so it needs its own rewording. Phase 1 added hunkydory and left the prose. | Fix (8.10) |
| DEV-10 | development | factual | Stale self-citations: `:1283` narrates a pre-correction line 987, and `:743` cites `packaging.py:819` where the expression is at 818. | Fix (8.10) |
| DEV-11 | development | medium, in-window | `scripts/test_audit_snapshot.py:245` derives the network-check set with `class (\w+)\(Check\):`, which matches **zero** classes in `npm_dependencies.py` because all three inherit `NpmPackageCheck` (`npm_dependencies.py:754`). It covers 52 of 55 criteria. The test does assert `derived == set(NETWORK_CHECKS)` at `:255`, but the three missing ids are absent from **both** sides of that equality, so it holds vacuously for them and the test passes green. `:250`'s `assertTrue(scheduled)` is a could-we-read-the-schedule guard, not the coverage assertion. This window opened the hole: phase 3 is the first criteria family to use an intermediate base class. | Fix (8.10): widen the regex **and** add a coverage assertion that the id map covers every registered check |
| DEV-12 | development | low, in-window | `scripts/audit-manage-issues.py:169` appends `check_result['details']` unbounded and unfenced; `ISSUE_BODY_BUDGET` (`:118`, added the same window) guards only `render_issue_items`. The npm criteria route per-item lists through `details` and embed a workflow's `run:` line verbatim, so a body can exceed GitHub's 65,536-character limit, `gh_create_issue` returns `None`, and the criterion silently stops filing while the audit reports success. (This row says "unbounded **and unfenced**"; only the bound was fixed. Fencing `details` would change every issue body, and most criteria write markdown into it deliberately -- backticked paths, bold headings -- so a fence would break the ones that are correct today to harden against a repository crafting its own issue text, which is DEV-17's ground. The escaping half is declined below rather than left implied.) | Fix (8.10), bound only |
| DEV-13 | development | runbook defect | `PUSH-AUDIT.md:50` scopes the new-imports grep with `'scripts/*.py'`. Git's fnmatch without `:(glob)` magic lets `*` cross `/`, so that pathspec selects exactly what `'*.py'` selects and the two scopes are indistinguishable -- both return all 56 files. `:(glob)scripts/*.py` matched nothing over this audit's range, because every changed `.py` file sat in a subdirectory, but against the tree it matches the 13 files directly under `scripts/`: the intended scope is `:(glob)scripts/**/*.py`. | Fix (8.10) |
| DEV-14 | development | design gap | `templates/mermaid-lint/` carries no staleness or version mechanism, unlike `templates/shared-blocks/*.md`, which carry a `shared-block: <name> vN` marker and a `validate_shared_blocks()` check. `MermaidLintCi` checks presence and wiring, never content freshness. Phase 4 edited that template's prose and no adopting repository can detect it. `tools/mermaid-lint.sh` and the template copy are byte-identical today with nothing enforcing it. | Future work + issue |
| DEV-15 | development | test coverage | `manifest_line()` is untested -- replacing its body with `return 1` leaves all 91 tests passing, yet that number is published into every filed issue. `npm i` and `npm add` are enforced but untested. Two `not_applicable` branches are uncovered in two of three criteria. There is no `lockfileVersion: 1` case for the unused-dependency criterion, which would false-fail. Two tests are byte-identical and one assertion is loose. | Fix (8.10) |
| DEV-16 | development | medium, pre-existing | `scripts/audit/checks/docs_content.py:183` opens files bare inside an `os.walk`. A dangling committed `*.md` symlink raises an uncaught `FileNotFoundError` and every one of the 55 criteria dies for that repository. Predates this plan (`c46afd5`, before `decaa4d~1`), so it is outside the audit range. | Future work + issue |
| DEV-17 | development | informational | `tsconfig` `outDir` join is not proved contained (no traversal found, but self-evasion is possible); a `RecursionError` can escape `read_json`. | Declined -- see below |
| HD-1 | hunkydory | critical | No `release` environment exists (`gh api ... /environments` returns `total_count: 0`) and no secrets are set, while `release.yml:106` declares `environment: release`. GitHub auto-creates a referenced environment **unprotected** on first use, so the first tag push creates exactly the unprotected state `RELEASE-SETUP.md:165-174` warns against -- and that document's recovery path is to add `VSCE_PAT` to it. | Issue filed; decided in 7b.0 |
| HD-2 | hunkydory | high | Zero rulesets, `develop` unprotected (branch-protection API returns 404), repository public. Any `v*` tag on any ref starts a publish under the `shakenfist` publisher id. | Issue filed; decided in 7b.0 |
| HD-3 | hunkydory | high | `ci.yml` runs `on: pull_request` and executes untrusted pull request code -- its own lockfile, its own pre-commit hooks -- on `[self-hosted, static]`, the shared persistent pool serving both `shakenfist` and `mach33labs`. `pr-re-review.yml` guards against fork pull requests; `ci.yml` does not. | Fix (8.11) |
| HD-4 | hunkydory | high | `.vscodeignore`'s `!out/src/*.js` is a single-segment glob. Proven empirically by 8.8: `out/src/sub/probe.js` is excluded from `vsce ls`. The first nested module under `src/` ships a broken extension, silently. | Fix (8.11) |
| HD-5 | hunkydory | blocking | `RELEASE-SETUP.md` contradicts `release.yml` on the job count (two versus three), the job name (`publish` versus `publish-marketplace`, including in the troubleshooting section) and, at `:131-136`, on the security control: it states the job "runs no `npm ci` and no package lifecycle scripts of any kind" when `release.yml:151` runs `npm ci --ignore-scripts`. The security property holds; the described mechanism is wrong. | Fix (8.11) |
| HD-6 | hunkydory | blocking | `README.md:55-57` claims a test suite that uses git as an oracle. No test invokes git at all; every fixture is hand-typed. This README ships as the Marketplace listing. | Fix (8.11) |
| HD-7 | hunkydory | medium | CRLF patch files are a silent no-op through the string API. `src/diff.ts:8`'s `HUNK_RE` ends `@@(.*)$`; `.` does not match `\r` and `$` is not multiline. Verified: a CRLF header does not match. No CRLF test exists. (This row originally said *every* CRLF patch file is a no-op. 8.11 established that the editor paths were fine -- they all read `document.lineAt(i).text`, which excludes the terminator -- so what was broken is `recountText` and `looksLikeDiff`, which is to say the regression harness and any non-editor caller. Root cause was `recountText` splitting on `\n` alone, not the expression.) | Fix (8.11), with a regression test |
| HD-8 | hunkydory | medium | `isPatch` matches any `*.patch` by filename regardless of language id, which is wider than `activationEvents: onLanguage:diff`; under `hunkydory.mode: onSave` that rewrites bytes on disk in a file the user never opened as a diff. | Fix (8.11) |
| HD-9 | hunkydory | medium | `prune-reviews.yml` has failed 6 of 6 runs since 2026-09-15 and `renovate.yml` 4 of 4, both for missing token secrets. Review pruning and dependency updates are inert, and have been since they were installed. | Issue filed; 7b.2 adds the secrets |
| HD-10 | hunkydory | factual | `REVIEWS.md:31` still attests `.github/workflows/runner-probe.yml`, deleted by #17, and claims "26 of 26 in-scope files are currently reviewed". Confirmed still present on `develop`. This corrects PR #17's body, which said `prune-reviews` would drop the row automatically: that run (`35413757830`) failed, per HD-9. | Fix (8.11), by hand |
| HD-11 | hunkydory | medium | `release.yml`'s `github-release` job calls a reusable workflow at `@main` with `secrets: inherit`, and third-party actions are unpinned. This is fleet convention rather than a hunkydory choice, so fixing it here alone would diverge without improving the fleet. | Future work + issue, fleet-wide |
| HD-12 | hunkydory | medium | The `build` job runs a bare `npm ci` on the shared static pool. The token is not present in that job, but the `.vsix` the publish job later signs and ships is produced there. Tightening it needs the devDependency install-script question settled first. | Future work + issue |
| HD-13 | hunkydory | low | The publish step's `.vsix` glob guard passes when the glob matches nothing. | Fix (8.11) |
| HD-14 | hunkydory | latent | `release.yml`'s publish job runs on `[self-hosted, vm, debian-13, s]`, a lane that carries neither node nor npm, and the workflow header's comment claiming otherwise is false. Found by 7a.6, already recorded; the repair is 7b.1's, not this phase's. | 7b.1 (already assigned) |

##### Declined, with reasons

**DEV-17, the two informational items from 8.6.** The `outDir`
join is not *proved* contained, but no traversal exists and the
only way to exploit it is for a repository to evade its own
audit, which it can do by deleting the file. A `RecursionError`
escaping `read_json` requires a hand-crafted several-thousand-deep
`package.json`; the audit already fails loudly rather than
silently passing in that case. Both are hardening against an
adversary who is auditing themselves, which is not the threat
model -- `PUSH-AUDIT.md`'s preamble frames the blast radius as
accidental fleet-wide damage, not a hostile repository.

**The escaping half of DEV-12.** The row names `details` as
unbounded *and* unfenced, and 8.10's brief scoped the repair to the
bound. Escaping it is declined on DEV-17's ground and for a second
reason of its own: `details` is written by the criteria as markdown
on purpose, so fencing or stripping it would degrade every issue the
fleet files today in order to harden against a repository injecting
markdown into an issue on itself. If it is worth doing it is a change
to how every criterion writes `details`, which is a separate pull
request rather than a wider version of this one.

**The remaining advisory items from 8.8.** 8.8 raised a further
set of stylistic and consistency observations in hunkydory's
documentation below the severity of HD-5 and HD-6. They are
declined as a group: each is a wording preference rather than a
statement that is false, and 8.11 is already rewriting the two
documents most of them touch. If any survives that rewrite it is
a new finding against the new text, which is the right place to
catch it.

**No finding was rejected under the superseded-hunk rule.** The
risks section anticipated that the concatenated diff would show
an auditor an intermediate state as the shipped one --
`.vscodeignore`'s deny-list and allow-list both appear in
hunkydory's diff. 8.8 read the current tree for that file, as
D8.2 requires, and HD-4 is against the shipped allow-list. One
apparent finding *was* rejected: 8.7 reported the range table's
sha placeholders as still present, which was an artefact of
reading `main` while step 8.1's fill-in was an uncommitted edit in
the audit worktree.

**Calibration.** 8.7 was pointed at the two defects the survey had
already found -- the head commit recorded as a merge, and the
false claim about `43b7f59` running `node --version` -- and
confirmed both are correctly fixed in the current tree. Its other
findings are therefore worth what they claim to be, which is the
mitigation the risks section named.

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
| 3 | high | opus | worktree | Add three `Check` subclasses for npm dependency auditing to `scripts/audit/checks/`, following the worked brief in `PLAN-TEMPLATE.md`. Register in `CHECKS` in `scripts/audit/registry.py`, write a spec page each under `docs/audits/`, add them to the index in `docs/audits/README.md`, add their lines to `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in `scripts/tests/test_metadata.py` -- but not to `FROZEN_COLUMN_NAMES`, which only carries a criterion that shares a spec page -- and add tests covering pass, fail and not-applicable in their own module, `scripts/tests/test_npm_dependencies.py`. They must report not-applicable with a reason where there is no `package.json`, including against this repository -- hunkydory is the only repository in the fleet that has one. Read the phase 3 section for the five exemptions the dependency checks must carry (node builtins in both spellings, the host-provided `vscode` module, relative imports, `@types/*`, and devDependencies invoked from `scripts`); without them the first run files three false issues on hunkydory. |
| 4 | high | opus | worktree | Done bar this plan file; most of it had already landed in `33fl`, see the phase 4 section. Includes rewording the node half of the mermaid-lint rationale in the four files the phase 4 section names. |
| 5 | medium | sonnet | none | Copy the fleet workflow templates into hunkydory, including `secret-scan.yml`, substituting TypeScript for Python in CodeQL, and write `ci.yml` calling `tools/check-node.sh` on `[self-hosted, static]` with `npm_config_cache` under `runner.temp`, plus a job running `pre-commit run --all-files`. Read the phase 5 section for the four criteria that go live when `.github/workflows/` first appears. |
| 6 | medium | sonnet | none | Deploy review tracking per `docs/code-review-tracking.md`, scoped to `src/` and `test/`. |
| 7 | medium | sonnet | none | Planned in detail on 2026-09-14; the phase 7 section carries its own step table and supersedes this row. In short: D7.1 splits the phase, 7a.1-7a.4 land the packaging half now, and 7b **holds** on the operator creating the publisher account and the tag-protected `release` environment. `release.yml` does not run its publish job on the static pool. |
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

**Trixie regressions the runner phase has not verified. Resolved.**
Phase 4 planned to move the static runner image from `debian:12` to
`debian:13` without having confirmed that the playbook's `yq`
install via `pip --break-system-packages`, the shared `docker.yml`,
the claude CLI install, or `python3-venv` and the rest of the base
package list behave the same on trixie. *Outcome:* another session
had already made the move, the operator replaced every static runner
on 2026-09-12, and the fleet has been serving jobs on Debian 13
since -- which answers all four empirically rather than by
inspection. See phase 4.

**A mixed node-18 and node-20 pool for about a week. Resolved, and
it never arose.** The instance-creation task takes its image from
`33fl/static_runner.yml:249` and loops over `missing_runners` at
`:268`, so only newly created instances would have got the new image
and the weekly retire-and-rebuild cycle would have replaced the
fleet over roughly a week, with a job able to land on either half.
*Outcome:* the fleet was replaced with Debian 13 before `nodejs`
joined the package list, so the package change landed on node 20
everywhere at once. The mitigation held anyway -- hunkydory was
verified to build and pass its 20 tests on Debian 12's node 18.20.4
-- and a future release bump reopens the window, which is why
`static_runner.yml` carries the warning beside the package list.

**Phase 3's criteria are measured against the whole fleet the next
morning.** A wrong criterion files issues in twenty repositories
rather than producing a red build. *Mitigation:* the survey in phase 3
establishes that hunkydory is the only repository with a
`package.json`, so the blast radius today is one repository and three
issues. The five exemptions in phase 3 are the guard against those
three being false. Issue filing is exercised with `--dry-run` only,
per the review checklist.

**Another session was editing `33fl` concurrently** (D5), so phase 4
could have collided with work in flight. *Mitigation:* phase 4 was
marked `Blocked` in the Execution table and restated as **Hold** in
the step guidance until the operator released it on 2026-09-13.
Nothing else in the plan writes to that repository, and phase 4 still
runs in plan order rather than being pulled forward.

**Phase 7 handles a Marketplace publish token.** `VSCE_PAT` can
publish under the `shakenfist` publisher id. *Mitigation:* the
constraints in phase 7 -- an ephemeral runner, a tag-protected
environment secret, and build separated from publish so `npm ci`
runs in the job that holds the token only with
`--ignore-scripts`, so no dependency lifecycle script executes
there. Implementation revised this from "never runs"; see phase 7's
*What implementation found* for why.

7b.0 superseded the middle constraint and kept the other two. There
is no stored token to protect: the job mints an hour-long Entra
token from its own OIDC identity, so the risk changes from "a
long-lived credential could leak" to "a run that reaches the job can
publish", and what bounds it is the `release` environment's tag
rule plus a tag ruleset on the repository. See *Decided: Entra, and
the publish job moves into a container*.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `scripts/audit-check.py` against `hunkydory` reports no failures
  other than `review-coverage`, which phase 6 deliberately opens and
  which stays open until the human review queue has been worked.
* `scope-coverage` passes against `development` again.
* `pre-commit run --all-files` passes in both repositories.
* The three npm criteria have all five of their files in step: the
  check, its registration in `CHECKS` in
  `scripts/audit/registry.py`, the specification under
  `docs/audits/`, the line in `docs/audits/README.md`, and their
  lines in `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
  `scripts/tests/test_metadata.py`. None carries a
  `FROZEN_COLUMN_NAMES` line, because each has its own spec page.
  Tests are their own module,
  `scripts/tests/test_npm_dependencies.py`.
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
* ~~The GitLab docker executor image is still `debian:12` after
  phase 4.~~ Not true, and it was not true when written. There is no
  `debian:12` anywhere in `33fl/static_runner.yml`: the executor
  image is at `:736` and reads `--docker-image debian:{{
  static_runner_debian_release }}`, so it follows the fleet rather
  than needing a decision. Phase 4's own verification questions say
  so. Kept struck through rather than deleted, because a future-work
  item nobody can find again reads as work that was silently
  dropped.
* hunkydory's `test/corpus.ts` points by default at a sibling
  `kerbside-patches` checkout, so the corpus check's verdict depends
  on which branch that checkout happens to be on. It degrades
  gracefully and says so, but a fixture inside the repository would
  be better.
* `recount-patch.py` in `kerbside-patches` and hunkydory's
  `src/diff.ts` implement the same counting rules in two languages.
  They were verified to agree on 175 patches, but nothing keeps them
  agreeing.
* **An npm criterion that a declared `script` actually runs.** Phase
  7 found `npm run package` broken on a clean checkout while all
  three npm criteria passed, because they read imports and no
  criterion reads `scripts`. Resolving what a script invokes, or
  running it, would have caught it. This repository is where criteria
  live, so the gap has no other home.
* **Splitting `release-process` into a Python-packaging arm and a
  language-neutral release-workflow-safety arm.** Five safety checks
  go dark behind the `pyproject.toml` skip for any non-Python
  repository with a release workflow; see phase 7's survey. D7.5
  declined to generalise from one release, and the second non-Python
  release is the point to revisit.
* **Whether the fleet's node baseline should move to 22.** vsce's
  transitive Azure dependencies already declare `engines.node
  ">=22.0.0"` against a fleet running Debian 13's node 20; npm warns
  and installs anyway today. See phase 7's risks.
* **Whether hunkydory's release tags should be Sigstore-signed**
  like the rest of the fleet's. Phase 7 decided a three-job shape
  without considering the template's `sign-tag` job; the omission is
  recorded in `release.yml`'s header as an open question.
* **hunkydory's merged `release.yml` cannot publish, and says the
  opposite.** Its `publish-marketplace` job ran `npm ci
  --ignore-scripts` on `[self-hosted, vm, debian-13, s]`, which
  7a.6 measured as carrying neither node nor npm, under a comment
  asserting "this runner carries Debian 13's node 20, so that's
  satisfied today". **7b.1 now carries the full repair.** This
  entry stays because 7b.1 sits inside 7b, and 7b has a hard
  operator dependency in 7b.2 that may never arrive -- which is the
  branch of the future this bullet was written to survive. If 7b is
  abandoned, deleting the false comment remains a one-line pull
  request that waits on no decision, and that is the fallback to
  take.
* **A check that a `Merged` cell names a merge into the default
  branch.** Two errors of exactly this kind landed in one column and
  were both found by hand: `43b7f59`, a head commit recorded as a
  merge, and `e216f93`, a merge into a feature branch. The
  `plan-audit-phase` criterion already parses plan phase tables, so
  the shape exists; what it needs is, for each `<repo> <sha> (#pr)`
  cell, an assertion that the sha has two parents *and* that the
  pull request's base was that repository's default branch.
  Cross-repository cells need the GitHub seam, so this may have to
  start same-repository only.
* **Packaging is a concern no criterion owns.** Phases 2, 5 and 6
  each added files to hunkydory with no reason to think about what
  ships to a user, and the `.vsix` ended up carrying 21 files of
  repository infrastructure alongside the 6 that belonged in it. The
  `.vscodeignore` allow-list fixes this repository; nothing stops the
  next one.

The phase 8 audit deferred seven findings to here rather than
fixing them in 8.10 or 8.11, because each needs an operator
decision, a repository setting, or a change wider than this plan.
Each names the repository its issue belongs against, which is the
one carrying the defect rather than the one carrying this plan.

* **DEV-14: `templates/mermaid-lint/` has no staleness mechanism**
  (`shakenfist/development`). `templates/shared-blocks/*.md` carry a
  `shared-block: <name> vN` marker and `validate_shared_blocks()`
  measures adopting repositories against it. The mermaid-lint
  template has nothing equivalent: `MermaidLintCi` checks presence
  and wiring, never content freshness. Phase 4 edited that
  template's prose and no adopting repository can detect it.
  `tools/mermaid-lint.sh` and the template copy are byte-identical
  today with nothing enforcing it, and phase 8 had to check that by
  hand.
* **DEV-16: one dangling symlink kills the whole audit for a
  repository** (`shakenfist/development`).
  `scripts/audit/checks/docs_content.py:183` opens files bare inside
  an `os.walk`, and `registry.run_check` has no handler, so a
  committed `*.md` symlink to a missing target raises
  `FileNotFoundError` and all 55 criteria die for that repository.
  It predates this plan (`c46afd5`, before `decaa4d~1`) and so is
  outside the audit range, which is the only reason it is here
  rather than in 8.10.
* **HD-1: the `release` environment does not exist**
  (`shakenfist/hunkydory`). `release.yml` declares `environment:
  release`, `gh api .../environments` returns `total_count: 0`, and
  no secrets are set. GitHub auto-creates a referenced environment
  **unprotected** on first use, so the first tag push creates
  exactly the state `RELEASE-SETUP.md` warns against -- and that
  document's recovery path is to add `VSCE_PAT` to it. 7b.0 has
  since decided it, and the finding survives at full severity:
  there is no `VSCE_PAT` to leak, but an environment-scoped OIDC
  subject carries no ref, so an auto-created unprotected `release`
  environment lets any run that reaches the job mint a real
  publishing token. Taken with HD-2 that is a successful publish of
  arbitrary content under the `shakenfist` publisher id. Creating
  the environment with its tag rule is 7b.2's, and the issue remains
  so that an abandoned 7b leaves a record.
* **HD-2: the repository has no branch protection and no rulesets**
  (`shakenfist/hunkydory`). `develop` is unprotected (the
  branch-protection API answers 404), there are zero rulesets, and
  the repository is public, so any `v*` tag on any ref starts a
  publish under the `shakenfist` publisher id. This is a repository
  setting; nothing in a pull request can fix it. **7b.2 now carries
  the repair**, and 7b.0 raised the stakes rather than lowering
  them: an environment-scoped OIDC subject carries no ref, so Entra
  cannot distinguish one, and the authority to create a `v*` tag is
  the authority to publish. The entry stays because 7b may never
  run, and because the setting is worth making whether or not the
  Marketplace account ever exists.
* **HD-9: `prune-reviews.yml` and `renovate.yml` have never
  succeeded** (`shakenfist/hunkydory`). Six of six runs and four of
  four runs have failed since 2026-09-15, both for missing token
  secrets. Review pruning and dependency updates have been inert
  since they were installed, which is also why `REVIEWS.md` still
  attests a file #17 deleted. Needs an operator to add the secrets.
* **HD-11: reusable workflows at `@main` with `secrets: inherit`**
  (`shakenfist/development`). `release.yml`'s `github-release` job
  calls a reusable workflow at a moving ref while inheriting every
  secret, and third-party actions are unpinned. This is fleet
  convention rather than a hunkydory choice -- the templates here
  are where it comes from -- so fixing it in one repository would
  diverge without improving the fleet.
* **HD-12: a bare `npm ci` on the shared static pool**
  (`shakenfist/hunkydory`). The `build` job runs it on
  `[self-hosted, static]`, the persistent pool serving both
  `shakenfist` and `mach33labs`. The publish token is not present
  in that job, but the `.vsix` the publish job later signs and
  ships is produced there. Tightening it needs the devDependency
  install-script question settled first, which is a decision rather
  than a patch.

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
