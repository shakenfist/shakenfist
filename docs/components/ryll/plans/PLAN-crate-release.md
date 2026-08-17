# Plan: Publish workspace crates on release

## Problem

The current [release.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/release.yml) tags, builds
binaries, creates a GitHub Release, and updates Homebrew. It does **not**
publish any of the four workspace crates to crates.io. The three
sub-crates (`shakenfist-spice-protocol`, `shakenfist-spice-compression`,
`shakenfist-spice-usbredir`) are currently parked on crates.io at
`0.0.0` as name reservations, and ryll's path dependencies on them
carry no `version =` qualifier, so `cargo publish -p ryll` would fail
today regardless of workflow changes.

## Goals

1. Publish all four crates to crates.io on every tag.
2. Keep all four crates on a single, shared version number, bumped
   together with each release tag.
3. Make cutting a release a one-command operation that a human
   confirms before anything irreversible happens.
4. Fail fast if the release tag and the workspace version disagree.

## Design

### Version unification via `[workspace.package]`

The workspace root [Cargo.toml](https://github.com/shakenfist/ryll/blob/develop/Cargo.toml) gains
`version = "0.1.3"` in `[workspace.package]`. All four members switch
from their current per-crate `version = "..."` lines to
`version.workspace = true`. Bumping the workspace version in one
place now updates all four crates.

Ryll's path dependencies on the three sub-crates gain explicit
`version = "0.1.3"` qualifiers so `cargo publish -p ryll` succeeds
against crates.io's rules about published crates declaring versions
on their dependencies.

Sub-crate descriptions are cleaned up to drop the "Reserved crate
name; the first published release will follow as 0.1.0" language.
The first real publish will be `0.1.4`, matching the ryll release,
rather than `0.1.0`. There is no behavioural reason to renumber
ryll and no value in having the sub-crates at different versions
from ryll.

### Release-cutting workflow

The cut is split into two phases so the version bump goes through
the same PR review gate as every other change, rather than landing
directly on `develop`.

**Phase 1** — [tools/propose-release.sh](https://github.com/shakenfist/ryll/blob/develop/tools/propose-release.sh),
invoked as `make propose-release 0.1.4`. The script:

1. Parses and validates the version argument as `X.Y.Z`.
2. Verifies git: on `develop`, working tree clean, in sync with
   `origin/develop`.
3. Verifies neither the tag `v0.1.4` nor the branch
   `release-0.1.4` already exists locally or on `origin`.
4. Verifies crates.io does not already have `0.1.4` for any of the
   four crates (HTTP GET to the crates.io API — 404 means free).
5. Creates and switches to `release-0.1.4`.
6. Runs `pre-commit run --all-files`.
7. Runs `cargo release version 0.1.4 --workspace --execute --no-confirm`
   to bump `[workspace.package].version` and the path-dep
   `version =` qualifiers in one pass.
8. Runs `cargo test --workspace` as a final gate.
9. Shows `git diff --stat`, then prompts
   "Commit and push release-0.1.4? [y/N]".
10. On `y`: commits `Release 0.1.4.` and pushes
    `release-0.1.4` to `origin`. On `n`: switches back to
    `develop` and deletes the release branch — nothing reaches
    `origin`.
11. Prints the PR-creation URL so the operator can open the PR
    manually. The script never creates PRs itself (per the
    repository convention that PR creation stays with the
    operator).

**Phase 2** — [tools/tag-release.sh](https://github.com/shakenfist/ryll/blob/develop/tools/tag-release.sh),
invoked as `make tag-release 0.1.4` after the phase-1 PR has been
merged. The script:

1. Fetches `origin/develop` and the latest tags.
2. Verifies no `v0.1.4` tag exists locally or on `origin`.
3. Reads `[workspace.package].version` from `origin/develop` and
   checks it is `0.1.4`; bails otherwise (means the PR has not
   merged yet or a different version landed).
4. Shows the target SHA and subject line, spells out that pushing
   the tag will publish all four crates to crates.io irreversibly,
   and prompts "Create and push tag v0.1.4? [y/N]".
5. On `y`: creates an annotated tag `v0.1.4` at the fetched
   `origin/develop` SHA and pushes it (this is what triggers
   [release.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/release.yml)).
6. Runs `gh run watch` on the triggered workflow, then
   `gh release view v0.1.4 --web` once it completes.

`cargo-release` is installed on the host (not inside Docker). It is
a pure Rust CLI and does not interact with the project's toolchain;
running it on the host is simpler than the alternative because the
scripts also need git, gh, and SSH key access. Hosts running older
rustc (e.g. Debian's cargo 1.85) should pin
`cargo-release@0.25.18`, which is the last release that supports
1.85.

### Release workflow (release.yml)

Three changes to [release.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/release.yml):

1. `check-version` reads the version from root `[workspace.package]`
   instead of `ryll/Cargo.toml`. Because all four crates now inherit,
   a single check covers the entire workspace.
2. New `publish-crates` job runs after `build` succeeds. Uses
   `CARGO_REGISTRY_TOKEN`. Publishes in dependency order:
   - `shakenfist-spice-protocol`
   - `shakenfist-spice-compression`
   - `shakenfist-spice-usbredir`
   - `ryll`
   The three sub-crates are independent of each other and could
   publish in any order or in parallel, but serial keeps the logs
   simple. Ryll must be last because it depends on all three.
3. `github-release` is unchanged (`generate_release_notes: true`
   already produces the autogenerated changelog that previously
   came from the web UI — no `gh release create` step is needed).

### Required secret

`CARGO_REGISTRY_TOKEN` must exist as a repo secret before the first
release after this lands. Generated at
https://crates.io/settings/tokens with "publish new crates" and
"publish updates" scopes. A human has to do this once — not
something the workflow or this PR can automate.

## Out of scope

- **Deriving version from the git tag.** `cargo publish` reads
  `Cargo.toml` at publish time; version-from-tag tricks either
  require rewriting Cargo.toml in CI (breaks `cargo install --git`
  by making the repo source disagree with the published crate) or
  use third-party build-info tools that don't integrate with
  `cargo publish`. The `check-version` step catches mismatches
  early enough that this isn't a real problem.
- **Automating bumps in response to conventional commits.** The
  human picks the version. `cargo release` supports `--bump patch`
  / `--bump minor`, but the script requires an explicit version to
  keep the intent auditable.
- **Major version discipline for 0.x crates.** Under semver, `0.y.z`
  → `0.(y+1).0` is a breaking change. The user is aware and will
  apply this convention manually when bumping.

## Rollout

The changes in this branch land as a single commit. The first tag
after merge (`v0.1.4`) will be the first real release of the
sub-crates — they go from `0.0.0` placeholder to `0.1.4` alongside
ryll.
