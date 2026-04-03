# Phase 5: macOS Packaging

Parent plan: [PLAN-packaging.md](/components/ryll/plans/PLAN-packaging/)

## Goal

Produce a tarball of the macOS (Apple Silicon) binary and
upload it as a CI artifact. Create the `shakenfist/homebrew-tap`
repository with a formula so macOS users can install ryll
via `brew install shakenfist/tap/ryll`.

## Current state

- The macOS CI job already builds the release binary and
  runs tests on `macos-latest` (Apple Silicon).
- No tarball artifact is produced from the macOS build.
- No Homebrew tap repository exists.
- `docs/installation.md` has a macOS placeholder.

## Design decisions

### Tarball in CI, formula in a separate repo

The CI workflow produces a tarball artifact from the macOS
build. The Homebrew formula lives in a separate repository
(`shakenfist/homebrew-tap`) — this is the standard Homebrew
tap pattern. The formula points to the tarball URL from
GitHub Releases.

For now (CI only, no releases yet), the tarball is a CI
artifact. Phase 7 (release automation) will attach it to
GitHub Releases and update the formula with the real URL
and SHA256.

### Apple Silicon only

Per the master plan decision (question 4), we only build
for `aarch64-apple-darwin`. No Intel macOS binaries.

### Formula structure

The formula is minimal since we distribute a pre-built
binary (not building from source). It downloads the tarball,
extracts the binary, and installs it to `bin/`.

```ruby
class Ryll < Formula
  desc "A Rust SPICE VDI test client"
  homepage "https://github.com/shakenfist/ryll"
  version "0.1.0"
  license "Apache-2.0"

  on_macos do
    on_arm do
      url "https://github.com/shakenfist/ryll/releases/download/v#{version}/ryll-#{version}-aarch64-apple-darwin.tar.gz"
      sha256 "PLACEHOLDER"
    end
  end

  def install
    bin.install "ryll"
  end

  test do
    assert_match "ryll", shell_output("#{bin}/ryll --version")
  end
end
```

The SHA256 and URL are placeholders until Phase 7 (release
automation) produces actual release artifacts.

### Tap repository naming

Homebrew requires tap repos be named `homebrew-<name>`. The
convention `shakenfist/homebrew-tap` means users install with
`brew tap shakenfist/tap` or directly via
`brew install shakenfist/tap/ryll`.

## Changes

### Step 1: Create macOS tarball in CI

Add steps to the macOS build job to package the binary and
upload it as an artifact:

```yaml
- name: Create macOS tarball
  if: runner.os == 'macOS'
  run: |
    cd target/release
    tar czf ryll-${{ matrix.target }}.tar.gz ryll
    cd ../..

- name: Upload macOS tarball
  if: runner.os == 'macOS'
  uses: actions/upload-artifact@v4
  with:
    name: macos-tarball
    path: target/release/ryll-*.tar.gz
    retention-days: 30
```

The tarball name includes the target triple for clarity.
Phase 7 will rename it to include the version number for
releases.

**Files changed:**
- `.github/workflows/ci.yml` — add two steps gated on
  `runner.os == 'macOS'`

**Commit:** standalone.

### Step 2: Create the Homebrew tap repository

Create `shakenfist/homebrew-tap` on GitHub with:

```
Formula/
  ryll.rb
README.md
```

The formula uses placeholder URL and SHA256 values. It
will be functional once Phase 7 creates a release with
the actual tarball attached.

**This step is manual** — it requires creating a new GitHub
repo. We can prepare the formula file locally and document
the steps.

**Files changed (in this repo):**
- None directly, but we prepare a template formula in
  `tools/homebrew-formula.rb` for reference.

**Commit:** standalone (the template file).

### Step 3: Update documentation

- Update `docs/installation.md` macOS section with real
  Homebrew install instructions.
- Mention that the tap is available once releases are
  published.

**Files changed:**
- `docs/installation.md`

**Commit:** standalone.

## Step summary

| Step | Description | Files | Commit |
|------|-------------|-------|--------|
| 1 | Create macOS tarball in CI | `.github/workflows/ci.yml` | Yes |
| 2 | Prepare Homebrew formula template | `tools/homebrew-formula.rb` | Yes |
| 3 | Update documentation | `docs/installation.md` | Yes |

## Risks and mitigations

- **Tarball size:** The macOS release binary is likely
  15-25MB. This is normal for a Rust GUI binary with
  eframe/egui. Homebrew handles large bottles routinely.

- **Formula won't work until Phase 7:** The placeholder URL
  and SHA256 mean `brew install` will fail until a real
  release is published. This is expected — the formula is
  ready to go, just waiting for release artifacts.

- **macOS Gatekeeper:** Unsigned binaries trigger a
  "cannot be opened because the developer cannot be
  verified" warning on macOS. Users must right-click and
  Open, or run `xattr -d com.apple.quarantine ryll` after
  download. Code signing is listed as future work in the
  master plan. For Homebrew installs, the quarantine
  attribute is typically not set.

- **Tap repo creation:** This requires org-level permissions
  on GitHub. The operator (Michael) will need to create the
  `shakenfist/homebrew-tap` repo manually.

## Open questions

None — all decisions were made in the master plan.
