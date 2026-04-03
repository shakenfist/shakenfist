# Phase 7: Release Automation

Parent plan: [PLAN-packaging.md](/components/ryll/plans/PLAN-packaging/)

## Goal

Create a `release.yml` GitHub Actions workflow that triggers
on version tags (`v*`), builds all platform artifacts, creates
a GitHub Release with them attached, and updates the Homebrew
tap formula with the new version and SHA256.

## Current state

- `ci.yml` builds, tests, and packages all four artifact
  types on every push/PR: `.deb`, `.rpm`, macOS `.tar.gz`,
  Windows `.zip`.
- No `release.yml` workflow exists.
- The Homebrew formula at `shakenfist/homebrew-tap` has
  placeholder URL and SHA256.
- `Cargo.toml` version is `0.1.0`.

## Design decisions

### Separate workflow, not extending ci.yml

The release workflow is a separate file (`release.yml`)
triggered only on version tags. It duplicates the build
matrix from `ci.yml` rather than trying to call/reuse it.
This is intentional:

- CI runs on every push/PR — fast feedback, artifacts are
  ephemeral (30-day retention).
- Release runs on tags only — produces permanent release
  artifacts attached to a GitHub Release.
- Keeping them separate means CI changes don't accidentally
  break releases and vice versa.

### Version tag triggers

The workflow triggers on `push` of tags matching `v*`. The
release process is:

1. Bump `version` in `Cargo.toml`.
2. Commit and push.
3. Tag: `git tag v0.2.0 && git push origin v0.2.0`.
4. The workflow runs, builds everything, creates the release.

### Version consistency check

Before building, verify that the tag matches the version
in `Cargo.toml`. If they don't match, the workflow fails
and files an issue (following the imago convention).

### Artifact naming with version

Release artifacts include the version number, unlike CI
artifacts which use the target triple only:

- `ryll_0.2.0-1_amd64.deb` (cargo-deb generates this)
- `ryll-0.2.0-1.x86_64.rpm` (cargo-generate-rpm generates
  this)
- `ryll-0.2.0-aarch64-apple-darwin.tar.gz`
- `ryll-0.2.0-x86_64-pc-windows-msvc.zip`

The macOS tarball and Windows zip are renamed during the
packaging step to include the version extracted from the tag.

### Homebrew tap update

After the release is created, a final job:

1. Downloads the macOS tarball from the just-created release.
2. Computes its SHA256.
3. Clones `shakenfist/homebrew-tap`.
4. Updates `Formula/ryll.rb` with the new version and SHA256.
5. Commits and pushes.

This requires a personal access token (PAT) or deploy key
with write access to `shakenfist/homebrew-tap`, stored as a
repository secret (e.g. `HOMEBREW_TAP_TOKEN`). The default
`GITHUB_TOKEN` cannot push to a different repository.

### No Sigstore signing

The imago release workflow uses Sigstore/gitsign for tag
signing. For ryll, we skip this initially — it adds
complexity and requires the `release` environment with
approval gates. Can be added later if needed.

### softprops/action-gh-release

Use `softprops/action-gh-release@v2` (same as imago) to
create the GitHub Release. It supports auto-generated
release notes and file glob patterns for attaching assets.

## Changes

### Step 1: Create `.github/workflows/release.yml`

**Trigger:**

```yaml
on:
  push:
    tags:
      - 'v*'
```

**Job 1: `check-version`**

Runs on `ubuntu-latest`. Extracts the version from the tag
and from `Cargo.toml`, fails if they don't match. Files a
GitHub issue on mismatch.

**Job 2: `build`**

Same three-element matrix as `ci.yml` (Linux, macOS,
Windows). Each element:

1. Checks out the code.
2. Installs Rust and platform deps.
3. Builds the release binary.
4. Runs tests.
5. Packages the artifact (platform-specific).
6. Uploads the artifact.

Key differences from `ci.yml`:
- Artifact names include the version from the tag.
- macOS tarball and Windows zip are named with the version.
- cargo-deb and cargo-generate-rpm produce version-stamped
  packages automatically (from `Cargo.toml` version).

**Job 3: `github-release`**

Depends on `build`. Downloads all artifacts, creates a
GitHub Release with auto-generated release notes, and
attaches all files.

**Job 4: `update-homebrew`**

Depends on `github-release`. Downloads the macOS tarball
from the release, computes SHA256, clones the tap repo,
updates the formula, commits and pushes.

```yaml
update-homebrew:
  needs: github-release
  runs-on: ubuntu-latest
  permissions:
    contents: read
  steps:
    - name: Get release version
      run: echo "VERSION=${GITHUB_REF#refs/tags/v}" >> "$GITHUB_ENV"

    - name: Download macOS tarball from release
      run: |
        curl -sL -o ryll-macos.tar.gz \
          "https://github.com/shakenfist/ryll/releases/download/v${VERSION}/ryll-${VERSION}-aarch64-apple-darwin.tar.gz"

    - name: Compute SHA256
      run: |
        SHA=$(sha256sum ryll-macos.tar.gz | awk '{print $1}')
        echo "SHA256=${SHA}" >> "$GITHUB_ENV"

    - name: Update Homebrew tap
      env:
        TAP_TOKEN: ${{ secrets.HOMEBREW_TAP_TOKEN }}
      run: |
        git clone "https://x-access-token:${TAP_TOKEN}@github.com/shakenfist/homebrew-tap.git"
        cd homebrew-tap
        sed -i "s/version \".*\"/version \"${VERSION}\"/" Formula/ryll.rb
        sed -i "s/sha256 \".*\"/sha256 \"${SHA256}\"/" Formula/ryll.rb
        git config user.name "github-actions[bot]"
        git config user.email "github-actions[bot]@users.noreply.github.com"
        git add Formula/ryll.rb
        git commit -m "Update ryll to ${VERSION}"
        git push
```

**Files changed:**
- `.github/workflows/release.yml` — new file

**Commit:** standalone.

### Step 2: Document the release process

Add release instructions so the process is clear:

- How to bump the version.
- How to tag and push.
- What the workflow does automatically.
- The `HOMEBREW_TAP_TOKEN` secret requirement.

**Files changed:**
- `docs/releasing.md` — new file
- `README.md` — brief mention of the release process

**Commit:** standalone.

### Step 3: Create the HOMEBREW_TAP_TOKEN secret

This is a **manual step** — the operator needs to:

1. Create a personal access token (classic) with `repo`
   scope, or a fine-grained token with write access to
   `shakenfist/homebrew-tap`.
2. Add it as a repository secret named `HOMEBREW_TAP_TOKEN`
   in `shakenfist/ryll` settings.

No code change needed.

## Step summary

| Step | Description | Files | Commit |
|------|-------------|-------|--------|
| 1 | Create release workflow | `.github/workflows/release.yml` | Yes |
| 2 | Document release process | `docs/releasing.md`, `README.md` | Yes |
| 3 | Create HOMEBREW_TAP_TOKEN secret | (manual) | No |

## Risks and mitigations

- **HOMEBREW_TAP_TOKEN not set:** If the secret is missing,
  the `update-homebrew` job fails but the GitHub Release is
  already created with all artifacts. The formula can be
  updated manually. The workflow should use
  `continue-on-error: true` on this job so the overall
  release isn't marked as failed.

- **Race condition on tap push:** If two releases are tagged
  in quick succession, the second tap push may conflict with
  the first. This is unlikely for a small project and can be
  handled with `git pull --rebase` before push.

- **cargo-deb / cargo-generate-rpm install time:** Both
  tools compile from source. In the release workflow (which
  runs less frequently than CI), this is acceptable. The
  Rust cache helps on repeated runs.

- **Release artifacts are permanent:** Unlike CI artifacts
  (30-day retention), GitHub Release assets persist
  indefinitely. Ensure the version check passes before
  building to avoid publishing artifacts for a mismatched
  tag.

## Open questions

None.
