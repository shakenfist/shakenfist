# Audit: Pre-commit Node version

## Why

pre-commit runs a `language: node` hook inside a Node environment it
builds with [nodeenv](https://github.com/ekalinin/nodeenv). Which Node
that environment gets is the hook's `language_version`, and left alone
that is `default`. `default` means "the system Node, if `node` and
`npm` are on the PATH, and otherwise whatever nodeenv downloads", and
nodeenv downloads the newest release listed in
<https://nodejs.org/dist/index.json>.

The newest release is not something a repository chose, and it is not
always one nodeenv can install. In October 2026 the index began with
`v27.0.0-alpha.0.0.0`, which nodeenv fails to parse
(`ValueError: invalid literal for int() with base 10: '0-alpha'`). On
every CI runner without a system Node, the lint job died before any
hook ran. That is how shakenfist/cloudgood's Lint job broke, and
`language_version: lts` is how it was fixed (shakenfist/cloudgood#21).
A workstation with Node installed never sees it, so the failure shows
up first in CI.

`lts` is the newest long-term-support release, which is never a
pre-release, so it keeps the hook on a line that nodeenv can install
and that moves only when a new LTS line ships.

## What we check

Every hook in `.pre-commit-config.yaml` that pre-commit builds with
nodeenv must resolve to a pinned Node.

**Which hooks are built with Node.** A hook whose language is `node`.

- A `repo: local` hook states its language in the config.
- A remote hook's language comes from the hook repository's
  `.pre-commit-hooks.yaml` at the pinned `rev`, unless the config
  overrides it with its own `language:`. The audit fetches that
  manifest from GitHub for every remote hook whose language, or whose
  Node version, the config alone does not settle. A hook the config
  already moves off Node, or already gives both `language: node` and
  a `language_version`, needs no fetch.
- `repo: meta` hooks are pre-commit's own and are never Node.

**Which Node they get.** The audit follows pre-commit's own order:

1. the hook's `language_version` in the config;
2. the `language_version` in the hook repository's manifest;
3. `default_language_version.node` at the top of the config;
4. otherwise `default`.

The manifest comes before `default_language_version` because that is
the order pre-commit applies them in: the top-level default fills in
only a hook still at `default` after the config and the manifest are
merged. renovatebot's `renovate-config-validator` hook is an example.
Its manifest already says `language_version: lts`, so it is pinned
without anything in the config.

**What passes and what fails.**

| Effective version | Verdict | Why |
|-------------------|---------|-----|
| `default` | fail | The system Node where there is one, otherwise the newest release. |
| `latest` | fail | Always the newest release. |
| `lts` | pass | The newest LTS release, never a pre-release. |
| an explicit version, such as `22.11.0` | pass | Exactly that release. |
| `system` | pass | No nodeenv at all: pre-commit uses the `node` on the PATH. |

`system` passes because nothing is downloaded, so the newest release
on nodejs.org cannot break the hook. It pays for that in two ways.
Every machine that runs the hook needs Node installed, a CI runner
included, and without it pre-commit cannot install the hook. The hook
also runs on whatever Node that machine has, so two developers, or a
developer and CI, can get different results. Prefer `lts` unless the
repository already depends on a system Node for other reasons.

A repository with no Node hooks is not applicable, and so is one with
no `.pre-commit-config.yaml`: whether one should exist is the
`pre-commit-config` check under
[workflow-standards](/components/development/audits/workflow-standards/).

A failure names each unpinned hook with the version it resolves to,
for example `markdownlint-cli2 (default)`.

## What we deliberately do not check

- **Remote hooks hosted anywhere but GitHub.** The manifest is read
  through the GitHub API. A hook repository on another host, or a
  remote entry with no `rev`, is named in the result as not measured
  and does not change the verdict. Nothing in the fleet uses one
  today.
- **A hook missing from its manifest.** pre-commit refuses to install
  such a config at all, so the failure is already loud. The audit
  names the hook as not measured rather than guessing its language.
- **Which LTS line, or which explicit version.** Pinning a version
  that is old, or about to reach end of life, is a separate question.
  This criterion is about whether the version is chosen by the
  repository or by whatever nodejs.org lists first.
- **Hooks built with another language's environment.** Python, Rust
  and Go hooks have their own `language_version` and their own ways to
  resolve `default`, but none of them has been seen to fetch an
  unparseable newest release. Extend this criterion if one does.
- **Node run any other way.** A workflow step that installs Node, or a
  `language: system` hook that calls `npx`, is outside pre-commit's
  nodeenv and outside this check.

If GitHub does not answer, or the pinned `rev` has no
`.pre-commit-hooks.yaml`, the check reports an error rather than a
verdict, and no issue is filed or closed. So does a
`default_language_version` the audit cannot read as a mapping, since
treating it as absent could fail a repository that pins Node there.

## Template

No template. Pin each Node hook:

```yaml
  - repo: local
    hooks:
      - id: markdownlint-cli2
        name: markdownlint-cli2
        entry: markdownlint-cli2
        language: node
        language_version: lts
        additional_dependencies: [markdownlint-cli2@0.24.0]
```

Or pin every Node hook in the file at once:

```yaml
default_language_version:
  node: lts
```

The one-line form, `default_language_version: {node: lts}`, is read
too.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#pre-commit-node-version).
