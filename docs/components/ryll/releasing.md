# Releasing

Releases are cut via [tools/cut-release.sh](../tools/cut-release.sh)
(wrapped by `make publish X.Y.Z`) and finished by GitHub Actions.
The tag push triggers
[.github/workflows/release.yml](../.github/workflows/release.yml),
which:

- Verifies the tag matches the workspace version.
- Builds release binaries on Linux, macOS (Apple Silicon), and
  Windows, running the test suite on each.
- Produces `.deb`, `.rpm`, macOS `.tar.gz`, and Windows `.zip`
  artifacts.
- Publishes all four workspace crates to crates.io in dependency
  order: `shakenfist-spice-protocol` → `shakenfist-spice-compression`
  → `shakenfist-spice-usbredir` → `ryll`.
- Creates a GitHub Release with auto-generated notes and all
  artifacts attached.
- Updates `shakenfist/homebrew-tap` with the new version and the
  SHA256 of the macOS tarball.

All four workspace crates share a single version, bumped together.
There is no way to release one crate at a new version without
releasing the others at the same version — this is intentional.

## Prerequisites

### Tag protection

A tag protection rule should be configured so only authorised
maintainers can push `v*` tags. Set this up in GitHub under
Settings → Rules → Rulesets (or the older Settings → Tags):

- **Pattern:** `v*`
- **Restrict who can create matching tags:** maintainers only

This prevents accidental or unauthorised releases.

### Repository secrets

Two secrets must be configured in the ryll repo settings:

- **`CARGO_REGISTRY_TOKEN`** — a crates.io API token with
  `publish-new` and `publish-update` scopes, generated at
  <https://crates.io/settings/tokens>. Without it, the
  `publish-crates` job fails and the four crates do not reach
  crates.io. The GitHub Release and binary artifacts are still
  created.
- **`HOMEBREW_TAP_TOKEN`** — a GitHub personal access token
  (classic with `repo` scope, or fine-grained with write access
  to `shakenfist/homebrew-tap`). Without it, the Homebrew tap
  update step fails; the GitHub Release and crates.io publishes
  still succeed.

### Host tools (for `make publish`)

`make publish` runs `tools/cut-release.sh` on the operator's
host (not in a container). The script requires:

- `cargo-release` — `cargo install --locked cargo-release`.
  Used for the workspace-wide version bump.
- `gh` — the GitHub CLI, signed in (`gh auth login`). Used to
  watch the release workflow and open the release page on
  completion.
- `jq`, `curl`, `git`, `pre-commit` — standard dev tooling.

## Release process

From a clean checkout of the current default branch (`develop`):

```bash
make publish 0.2.0
```

The script will:

1. Validate the version as `X.Y.Z`.
2. Confirm the working tree is clean, the branch is up to date
   with origin, and `v0.2.0` does not yet exist locally or on
   origin.
3. Query crates.io for `0.2.0` on all four crates; bail if any
   already exists.
4. Run `pre-commit run --all-files`.
5. Bump `[workspace.package].version` and the matching
   `version =` qualifiers on ryll's path dependencies via
   `cargo release version 0.2.0 --workspace --execute
   --no-confirm`.
6. Run `cargo test --workspace`.
7. Show `git diff --stat` and prompt `Release v0.2.0? [y/N]`.
8. On confirmation: create `Release 0.2.0.`, push, tag
   `v0.2.0` (annotated), push the tag.
9. Watch the release workflow via `gh run watch` and open the
   release page when it completes.

Nothing irreversible happens before the `y/N` prompt. If you
answer `N`, the working tree will be left with uncommitted
version bumps; revert with `git checkout -- .`.

## Artifacts produced

| Platform | Artifact | Contents |
|----------|----------|----------|
| Debian/Ubuntu | `ryll_{version}-1_amd64.deb` | Binary + auto-detected deps |
| Fedora/RHEL | `ryll-{version}-1.x86_64.rpm` | Binary + auto-detected deps |
| macOS (Apple Silicon) | `ryll-{version}-aarch64-apple-darwin.tar.gz` | Binary |
| Windows | `ryll-{version}-x86_64-pc-windows-msvc.zip` | Binary (no `--capture`) |
| crates.io | `shakenfist-spice-protocol`, `shakenfist-spice-compression`, `shakenfist-spice-usbredir`, `ryll` | Source crates |

## Troubleshooting

### Version mismatch

The `check-version` job reads `[workspace.package].version`
from the root `Cargo.toml` and compares it to the tag. A
mismatch fails the workflow and files a GitHub issue. This
should not happen when you release via `make publish`, since
the script bumps and tags from a single version string. If it
does (for example after a manual tag push):

```bash
git tag -d v0.2.0
git push origin :refs/tags/v0.2.0
# fix the workspace version, commit, then re-tag via make publish
```

### crates.io publish fails

A `publish-crates` job failure does not roll back the earlier
publishes. If the workflow publishes protocol and compression
then fails on usbredir, the first two are live on crates.io and
cannot be replaced — only yanked. Fix the underlying issue and
bump to the next patch version (`make publish 0.2.1`); do not
try to re-publish `0.2.0`.

Common causes:

- `CARGO_REGISTRY_TOKEN` missing, expired, or scoped too
  narrowly. Regenerate with `publish-new` + `publish-update`.
- A dependency inside ryll that still uses a `path = ...` only
  reference without a `version = ...` qualifier. `cargo publish`
  rejects this. The dual-spec qualifiers are wired up and
  maintained by `cargo release`; if you have added new
  workspace-member dependencies, make sure they follow the same
  pattern.

### Homebrew tap update fails

If `HOMEBREW_TAP_TOKEN` is missing or expired the tap update
job fails but the release and the crates.io publishes still
succeed. Update the formula manually:

```bash
cd homebrew-tap
# update version and sha256 in Formula/ryll.rb
git commit -am "Update ryll to 0.2.0"
git push
```

## First release after crate name reservations

The three sub-crates currently sit on crates.io as `0.0.0`
placeholder reservations from Phase 2 of the crate extraction
plan. The first release cut after the unified-versioning change
lands will be their first real publish, at the workspace
version (e.g. `0.1.4`), not `0.1.0`. The `0.0.0` placeholders
remain on crates.io; the Rust Forge crate ownership policy
recommends leaving them unyanked so the README explaining the
reservation stays visible to anyone who stumbles across them.
