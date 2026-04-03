# Releasing

Releases are automated via GitHub Actions. Pushing a version tag
triggers the release workflow, which builds all platform packages,
creates a GitHub Release, and updates the Homebrew tap.

## Prerequisites

### Tag protection

A tag protection rule should be configured so only authorised
maintainers can push `v*` tags. Set this up in GitHub under
Settings → Rules → Rulesets (or the older Settings → Tags):

- **Pattern:** `v*`
- **Restrict who can create matching tags:** maintainers only

This prevents accidental or unauthorised releases.

### HOMEBREW_TAP_TOKEN

A repository secret `HOMEBREW_TAP_TOKEN` must be configured in the
ryll repo settings. This is a GitHub personal access token (classic
with `repo` scope, or fine-grained with write access to
`shakenfist/homebrew-tap`). Without it, the Homebrew tap update step
will fail — but the GitHub Release will still be created successfully.

## Release process

1. **Update the version** in `Cargo.toml`:

   ```toml
   version = "0.2.0"
   ```

2. **Commit the version bump:**

   ```bash
   git add Cargo.toml Cargo.lock
   git commit -m "Bump version to 0.2.0."
   ```

3. **Tag and push:**

   ```bash
   git tag v0.2.0
   git push origin develop
   git push origin v0.2.0
   ```

4. **Wait for the workflow** to complete. It will:
   - Verify the tag matches `Cargo.toml` (fails and files an
     issue if they don't match).
   - Build release binaries on Linux, macOS, and Windows.
   - Run tests on all platforms.
   - Package: `.deb`, `.rpm`, macOS `.tar.gz`, Windows `.zip`.
   - Create a GitHub Release with auto-generated release notes
     and all artifacts attached.
   - Update `shakenfist/homebrew-tap` with the new version and
     SHA256 for the macOS tarball.

5. **Verify the release** at
   https://github.com/shakenfist/ryll/releases

## Artifacts produced

| Platform | Artifact | Contents |
|----------|----------|----------|
| Debian/Ubuntu | `ryll_{version}-1_amd64.deb` | Binary + auto-detected deps |
| Fedora/RHEL | `ryll-{version}-1.x86_64.rpm` | Binary + auto-detected deps |
| macOS (Apple Silicon) | `ryll-{version}-aarch64-apple-darwin.tar.gz` | Binary |
| Windows | `ryll-{version}-x86_64-pc-windows-msvc.zip` | Binary (no `--capture`) |

## Troubleshooting

### Version mismatch

If the tag doesn't match `Cargo.toml`, the workflow fails and
creates a GitHub issue. Fix the version, commit, delete the bad
tag, and re-tag:

```bash
git tag -d v0.2.0
git push origin :refs/tags/v0.2.0
# fix Cargo.toml, commit, then re-tag
git tag v0.2.0
git push origin v0.2.0
```

### Homebrew tap update fails

If the `HOMEBREW_TAP_TOKEN` secret is missing or expired, the
tap update job fails but the release is still created. Update
the formula manually:

```bash
cd homebrew-tap
# update version and sha256 in Formula/ryll.rb
git commit -am "Update ryll to 0.2.0"
git push
```
