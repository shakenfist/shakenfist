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
| 6. Human review onboarding | Complete | hunkydory e216f93 (#8) |
| 7. Marketplace release | In progress | 7a: hunkydory 43b7f59 (#9) |
| 8. Push audit | Not started | |

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
audit against hunkydory now reports 28 pass, 1 fail, 26
not-applicable, the single failure being the `review-coverage`
backlog phase 6 deliberately opened.

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
there. See *Open question: does the publish lane have node?*; step
7a.6 exists to settle it.

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
merged on 2026-09-14, so 7a.5 and 7a.6 are now unblocked; 7b waits
on the decisions in 7b.0.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a.1 | low | sonnet | none | In hunkydory: add `@vscode/vsce` to `devDependencies` and refresh `package-lock.json` with `npm install`. Verify `npm ci && npm run package` produces `hunkydory-0.1.0.vsix` on a clean checkout with no network fetch of vsce itself. Check vsce's own `engines.node` against the repository's `engines.node: ">=20"` and against the node the static runners carry (Debian 13's packaged node, per D1) -- if vsce needs newer, say so and stop rather than raising `engines.node`. Re-run the three npm criteria: they read imports, so they should not move. |
| 7a.2 | medium | sonnet | none | Write `.github/workflows/release.yml` with the three jobs from D7.2 and D7.4. Adapt `templates/release-automation/release.yml` rather than copying it: the publish job moves off `[self-hosted, static]`, and the header comment says the template is the source and why this file deviates. `permissions: {}` at the top, least privilege per job. Scripts longer than about five lines go in `tools/`, per the fleet convention. actionlint runs from pre-commit against `.github/actionlint.yaml`; 7a needs no new labels, because `[self-hosted, vm, debian-13, s]` is already declared there, so do not add any. (This scopes 7a, and is not a standing prohibition: 7b's option 2 adds a label deliberately.) |
| 7a.3 | low | sonnet | none | Write `RELEASE-SETUP.md` covering every one-time step: the Azure DevOps publisher account for the `shakenfist` publisher id already in `package.json`, generating a `VSCE_PAT` with Marketplace publish scope, creating the tag-protected `release` environment, and adding the secret to that environment rather than to the repository. Model the structure on `templates/release-automation/RELEASE-SETUP.md`, but write the VS Code Marketplace steps rather than the PyPI trusted-publisher ones. The file goes at the repository root, `RELEASE-SETUP.md`, as it is in every other repository in the fleet -- `ReleaseProcess` tests `repo.exists('RELEASE-SETUP.md')` (`scripts/audit/checks/packaging.py:819`) and the template is root-destined. That criterion skips for hunkydory today, so nothing would catch a divergence, which is exactly why the path is stated here rather than inferred. Separately: reference it from `AGENTS.md` only if a convention changes. |
| 7a.4 | low | sonnet | none | Re-run `pre-commit run --all-files` in hunkydory and the audit (`scripts/audit-check.py --repo-path <clone> --repo-name hunkydory --github-org shakenfist`). The verdict must still be 28 pass / 1 fail / 26 not-applicable, the failure being `review-coverage`. Anything else moved is a finding, not a rounding error. |
| 7a.5 | low | sonnet | none | Dispatch `release.yml` on `develop` once 7a has merged. Confirm the `build` job produces the `.vsix` artifact on `[self-hosted, static]`, that `vscode:prepublish` compiles on a real runner with a real `npm_config_cache`, and that both publishing jobs correctly decline to run. Record the run URL here. This is the run D7.1's argument rests on, and it cannot answer the publish-lane question above, because the guard stops a dispatch reaching that job by design. |
| 7a.6 | low | sonnet | none | **Measure whether `[self-hosted, vm, debian-13, s]` carries node, and record the answer in *Open question: does the publish lane have node?* above.** Add a dispatch-only throwaway workflow to hunkydory -- `permissions: {}`, **no `environment:` key**, so it can reach no secret -- whose single job runs on that lane and executes `node --version; npm --version; command -v node npm` without `set -e` stopping at the first absence. `secret-scan.yml` already uses this lane and `workflow_dispatch`, so no new actionlint label is needed. Dispatch it, record the output and the run URL, then delete the workflow in the same PR chain: it is a probe, not a fixture. The point is to reduce 7b.0's question (b) from a three-way guess to either "nothing to do" or "option 2". Do not fold this into `release.yml`'s publish job -- the dispatch guard means that job cannot run until 7b, which is the whole reason the question is open. |
| 7b.0 | -- | operator | -- | **Decide two things before 7b.1 does any work.** (a) PAT or Entra federated credential: Azure DevOps retires global PATs on 1 December 2026, so a PAT bought now lasts about ten weeks and the Entra path has to be walked either way. The plan leans to going straight to `--azure-credential` and never minting a PAT, on the grounds that the setup cost is paid once rather than twice. (b) How the publish lane gets node. **Read 7a.6's measurement first**: if the lane already carries node and npm, there is nothing to decide and option 2's label and container work is unnecessary. If it does not, the plan leans to option 2, the `debian-13-docker` lane with a pinned `node:22` container, because it settles the `engines.node` risk in the same move and needs no work in another repository. |
| 7b.1 | -- | operator | -- | **Hold.** Execute what 7b.0 chose: create the publisher account, create the tag-protected `release` environment, and add the credential to it. Nothing in a repository can do this. |
| 7b.2 | low | sonnet | none | Once 7b.1 is done: tag `v0.1.0`, watch the run, and record the outcome in this section -- either the Marketplace listing URL, or what failed. If the account never arrives, record that instead and cite the attached `.vsix`. **If it fails, the recovery is a version bump, not a re-tag**: `43b7f59`'s build job asserts the tag matches `package.json`, and the Marketplace rejects a republished version, so a deleted and re-pushed `v0.1.0` either fails the same way or is refused on the far side. Bump to `0.1.1` and tag that. The jobs can also disagree -- `github-release` needs `publish-marketplace`, so Marketplace-succeeded-and-release-failed is the only split possible, and it is repaired by attaching the `.vsix` to the existing release by hand rather than by re-running anything. Add both to `RELEASE-SETUP.md`'s troubleshooting while the reasoning is fresh. |

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
7b.1 creates the environment with its tag rule, and 7a.2 must not
pretend the workflow is safe before that exists -- `RELEASE-SETUP.md`
states the ordering.

#### Definition of done

* `npm ci && npm run package` produces a `.vsix` on a clean checkout
  of `develop`, with no `npx` download of vsce.
* `@vscode/vsce` appears in `package.json` `devDependencies` and in
  `package-lock.json`.
* `pre-commit run --all-files` passes in hunkydory, actionlint
  included, against the committed `.github/actionlint.yaml`.
* No `runs-on` in `release.yml`'s publish job contains `static`,
  and the job that references `secrets.VSCE_PAT` runs no install
  without `--ignore-scripts`, and no `npm run` script.
* `publish-marketplace` is the only job carrying
  `environment: release`, and no job whose `runs-on` contains
  `static` references `secrets.VSCE_PAT`. The second half is the
  one that matters: GitHub scopes an environment secret to the
  environment, not to the job, so an `environment: release` job on
  the static pool would be one line of YAML away from holding the
  token. See *Why the publish job stays off the static pool*.
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
  the PAT scope and the tag rule, and states that the environment
  must exist before the first tag is pushed.
* The audit against hunkydory reports one failure and it is
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
* **The audit after 7a reports 28 pass, 1 fail, 26 not-applicable**
  -- identical to the pre-7a figures, with `review-coverage` still
  the only failure. Nothing moved, which for this phase is the
  expected result: 7a added a workflow, a runbook and a dependency,
  and the criteria that would notice any of those are the Python
  ones that skip, and the five release-safety helpers that go dark
  behind the same `pyproject.toml` skip.

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

#### Open question: does the publish lane have node?

**This blocks 7b and is not settled.** Phase 4 put `nodejs` and
`npm` on the **static** runners -- `33fl` `bc50c52a` in
`static_runner.yml` -- and said nothing about the ephemeral VM lane.
D7.2 then put the publish job on `[self-hosted, vm, debian-13, s]`,
and implementation found that job needs `npm ci --ignore-scripts` to
get the vsce binary. Nothing establishes that lane has node.

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

The options, for a decision rather than a guess:

1. **Put node on the VM image.** Correct, and matches what phase 4
   did for the static pool, but it is work in another repository and
   D5 territory.
2. **Move the publish job to `debian-13-docker` and run vsce in a
   pinned `node:22` container.** Guarantees the runtime and disposes
   of the `engines.node` risk below at the same time. Costs a label
   in hunkydory's `.github/actionlint.yaml` and a `tools/` script.
3. **Have the build job ship vsce.** Upload `node_modules` beside
   the `.vsix` so the publish job installs nothing. Removes npm but
   not node, so it only helps if the lane has node but not npm --
   the least likely shape. It is also the worst of the three on
   D7.2's own terms, and that is the stronger objection: it moves
   305 packages of executable code, materialised on the shared
   static pool, into the job that holds the token, and runs it. The
   lockfile pin stops bounding what executes, because what executes
   is the pool's tarball rather than the reviewed and pinned tree --
   and *What implementation found* is explicit that the pin is
   load-bearing for security, not only for reproducibility.

`43b7f59` runs `node --version && npm --version` as the first step of
the publish job, so that whatever happens, the diagnosis is in the
log rather than an unexplained `npm: command not found`. That was the
right instinct in the wrong place: the step sits inside the one job
the dispatch guard keeps unreachable until 7b, so it reports the
answer only once it is too late to choose differently. Step 7a.6
measures the same two commands on the same lane from a dispatch-only
job that holds no secret, which is available now, so 7b.0 decides
with the fact rather than around it.

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
`RELEASE-SETUP.md` leads with it. **This wants a decision before
7b.1 rather than after**: standing up a PAT now buys about ten weeks,
and the Entra path has to be walked eventually either way.

**vsce's transitive Azure dependencies already ask for a newer node
than the runners have.** `@vscode/vsce` declares `engines.node
">= 20"` and the fleet carries Debian 13's node 20, but
`@azure/identity` and its neighbours declare `">=22.0.0"`. npm warns
`EBADENGINE` and installs anyway, and `@vscode/vsce/out/auth.js` was
verified to load and run on node 20.19.2, so this works today.
*Mitigation:* none available in this repository -- the fix is the
fleet moving to a newer node, and the failure would surface at
publish time. Recorded so the next reader is not surprised.

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

The gate that is still ahead is **7b.0**, and it is a harder one,
because both of its questions change `release.yml` rather than
merely configuring around it. Going to `--azure-credential` removes
the `VSCE_PAT` secret this workflow is shaped around, and option 2
for the publish lane moves that job to another label and into a
container. Agree both before 7b.1 stands anything up, or the account
gets configured for an architecture that then changes.

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
* **Packaging is a concern no criterion owns.** Phases 2, 5 and 6
  each added files to hunkydory with no reason to think about what
  ships to a user, and the `.vsix` ended up carrying 21 files of
  repository infrastructure alongside the 6 that belonged in it. The
  `.vscodeignore` allow-list fixes this repository; nothing stops the
  next one.

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
