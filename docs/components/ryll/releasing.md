# Releasing

Cutting a release is a two-phase operation so the version bump
goes through the normal PR review gate rather than landing
directly on `develop`:

1. **Phase 1 — propose:**
   [tools/propose-release.sh](../tools/propose-release.sh)
   (wrapped by `make propose-release X.Y.Z`) creates a
   `release-X.Y.Z` branch from `develop`, bumps the workspace
   version, runs tests, and pushes the branch. You open a PR
   from it into `develop` and get it reviewed and merged like
   any other change.
2. **Phase 2 — tag:** after the PR has merged,
   [tools/tag-release.sh](../tools/tag-release.sh) (wrapped by
   `make tag-release X.Y.Z`) fetches `develop`, verifies its
   tip has the expected workspace version, creates an annotated
   `vX.Y.Z` tag at that commit, and pushes the tag.

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

### Host tools

Both scripts run on the operator's host (not in a container).
Between them they require:

- `cargo-release` — `cargo install --locked cargo-release`.
  Used for the workspace-wide version bump in phase 1. If your
  host toolchain is older than 1.91 (for example Debian's
  packaged cargo 1.85), pin the last version that supports it:
  `cargo install --locked cargo-release@0.25.18`.
- `gh` — the GitHub CLI, signed in (`gh auth login`). Used in
  phase 2 to watch the release workflow and open the release
  page on completion.
- `jq`, `curl`, `git`, `pre-commit` — standard dev tooling.

## Release process

### Phase 1: propose the release

From a clean checkout of `develop`, up to date with origin:

```bash
make propose-release 0.2.0
```

The script will:

1. Validate the version as `X.Y.Z`.
2. Confirm the working tree is clean, the branch is `develop`
   and in sync with `origin/develop`, and neither `v0.2.0` nor
   `release-0.2.0` exist locally or on origin.
3. Query crates.io for `0.2.0` on all four crates; bail if any
   already exists.
4. Create and switch to `release-0.2.0`.
5. Run `pre-commit run --all-files`.
6. Bump `[workspace.package].version` and the matching
   `version =` qualifiers on ryll's path dependencies via
   `cargo release version 0.2.0 --workspace --execute
   --no-confirm`.
7. Run `cargo test --workspace`.
8. Show `git diff --stat` and prompt
   `Commit and push release-0.2.0? [y/N]`.
9. On confirmation: commit `Release 0.2.0.` and push
   `release-0.2.0` to origin.

Nothing goes to origin before the `y/N` prompt. If you answer
`N`, the script switches back to `develop` and deletes the
release branch; nothing is committed.

Once the script finishes, open a PR from `release-0.2.0` into
`develop`, get it reviewed, and merge it using the repository's
usual merge strategy. No release artefacts are produced yet —
this PR is the audit trail for the version bump itself.

### Phase 2: tag the merged commit

After the PR has merged:

```bash
make tag-release 0.2.0
```

The script will:

1. Fetch `origin/develop` and the latest tags.
2. Verify no `v0.2.0` tag exists locally or on origin.
3. Verify the `[workspace.package].version` on
   `origin/develop` is `0.2.0` (i.e. the release PR has
   landed). If it still reports the previous version, the PR
   has not merged yet — the script bails.
4. Show the target SHA and subject line, spell out that
   pushing the tag will publish all four crates to crates.io
   irreversibly, and prompt `Create and push tag v0.2.0? [y/N]`.
5. On confirmation: create the annotated tag at
   `origin/develop` and push it.
6. Watch the triggered release workflow via `gh run watch` and
   open the GitHub Release page when it completes.

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
should not happen when you release via the `propose-release`
→ `tag-release` flow, since `tag-release` refuses to run
unless `origin/develop` already carries the matching
workspace version. If it does (for example after a manual tag
push):

```bash
git tag -d v0.2.0
git push origin :refs/tags/v0.2.0
# fix the workspace version on develop via a new release PR,
# then re-run make tag-release X.Y.Z.
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
