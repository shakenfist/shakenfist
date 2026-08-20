# Plan: First Public Release of Instar

## Status: Complete

The umbrella goal — "prepare instar for its first public release"
— was met on 2026-05-09 when v0.2.0 was tagged and published, and
has since been exercised a second time end-to-end by v0.3.0
(published 2026-08-02). All five phases of this plan are done:

* **Phase 1 (Cargo.toml metadata)** — shipped. `src/vmm/Cargo.toml`
  carries the full published-crate metadata block; library crates
  carry `repository` / `authors` / `keywords` / `categories`;
  internal crates carry `publish = false`.
* **Phase 2 (release workflow)** — shipped as
  `.github/workflows/release.yml`: `check-version` → `build` →
  `sign-tag` (Sigstore, gated on the `release` GitHub environment)
  → `github-release`. It has run for real twice. The build job also
  went beyond the original sketch: it enforces the shipped binary's
  glibc floor via `tools/ci/check-glibc-floor.sh`, and builds and
  uploads `.deb` and `.rpm` packages alongside the tarball — the
  packaging that this plan originally deferred.
* **Phase 3 (documentation gaps)** — shipped: `CHANGELOG.md`,
  `SECURITY.md`, and the README `Installation` section all exist
  and are maintained per release.
* **Phase 4 (version and tagging)** — shipped: `make release`,
  `make check-version`, `make package`, the `release` environment,
  and the documented tag-and-push sequence. v0.2.0 and v0.3.0 both
  went out through it.
* **Phase 5 (pre-release audit)** — discharged as a v0.2.0 gate;
  the execution record is in `PLAN-release-v0.2.md`. The recurring
  items have since become standing CI rather than per-release
  checklists: `cargo audit` runs in `supply-chain.yml`, clippy in
  `lint.yml`, the functional suite in `functional-tests.yml`, and
  `pre-commit` on every push. One checklist item is simply stale
  and was never true in the form written — plan documents are
  versioned in `docs/plans/`, not gitignored; the release tarball
  is built from an explicit file list (`instar`, the guest `.bin`
  files, `LICENSE`, `README.md`), so no plan document has ever
  been published in an artifact.

The "phase 6 coverage fuzzing" referenced by the old status line
was never a phase of this plan. Coverage-guided fuzzing and the
security audit are separately registered master plans
(`PLAN-coverage-fuzzing.md`, `PLAN-audit.md`) that continue on
their own; they are release-quality work, not first-release
blockers, and holding this plan open for them was what kept it
marked in-progress long after it was finished.

Per-release execution lives in its own plan
(`PLAN-release-v0.2.md`, `PLAN-release-v0.3.md`, future
`PLAN-release-vN.M.md`). Future releases need this plan only as
background; they do not reopen it.

## Goal

Prepare instar for its first public release as a compiled Rust CLI
tool. This covers Cargo.toml metadata, release workflow automation,
binary distribution, packaging, and documentation gaps.

## Context: How Rust releases differ from Python

The shakenfist ecosystem uses a Python release template
(`development/templates/release-automation/`) that publishes to PyPI
via trusted publishers, signs tags with Sigstore, and creates GitHub
Releases. For Rust the flow changes:

- **No PyPI** -- the equivalent is crates.io, but `cargo install
  instar` requires Docker + nightly Rust for the guest binary
  cross-compilation, so it will fail for most users. crates.io
  publishing is optional and secondary.
- **Pre-compiled binaries are the primary distribution channel.**
  Most popular Rust CLI tools (ripgrep, bat, fd, delta) ship
  pre-compiled binaries via GitHub Releases. Users download a
  tarball, extract, and run.
- **instar is Linux-only** (requires KVM). Skip macOS and Windows
  targets entirely. This is normal for KVM-dependent tools
  (Firecracker, cloud-hypervisor do the same).
- **Sigstore tag signing and GitHub Releases** carry over directly
  from the Python template.

## Phase 1: Cargo.toml metadata

Add required fields to all workspace crates for crates.io
compatibility and good `cargo metadata` output.

### VMM crate (the published binary)

Add to `src/vmm/Cargo.toml`:
```toml
[package]
name = "instar"
version = "0.1.0"
edition = "2021"
description = "Safe, sandboxed disk image operations using KVM isolation"
license = "Apache-2.0"
repository = "https://github.com/shakenfist/instar"
homepage = "https://github.com/shakenfist/instar"
readme = "../../README.md"
authors = ["Michael Still <mikal@stillhq.com>"]
keywords = ["disk-image", "qcow2", "kvm", "vmdk", "vhd"]
categories = ["command-line-utilities", "virtualization"]
```

### Library crates (qcow2, vmdk, vhd, vhdx, raw, luks)

These are potentially useful standalone. Add `repository`, `authors`,
`keywords`, and `categories` to each. Example for qcow2:
```toml
repository = "https://github.com/shakenfist/instar"
authors = ["Michael Still <mikal@stillhq.com>"]
keywords = ["qcow2", "disk-image", "no-std", "parser"]
categories = ["no-std", "parser-implementations"]
```

### Internal crates (shared, core, guest-protocol, operations/*)

These are not useful outside the workspace. Add to each:
```toml
publish = false
```

This prevents accidental `cargo publish` of internal crates.

## Phase 2: Release workflow

Create `.github/workflows/release.yml` adapted from the shakenfist
Python template. The key differences from the Python template are:

### Triggers

Same as Python template -- tag push matching `v*` plus manual
`workflow_dispatch`.

### Build job

Replace the Python build with:

```yaml
build:
  name: Build Linux binaries
  runs-on: [self-hosted, debian-12, s]
  permissions:
    contents: read
  strategy:
    matrix:
      target:
        - x86_64-unknown-linux-gnu
        # Add later if there is demand:
        # - x86_64-unknown-linux-musl
        # - aarch64-unknown-linux-gnu
  steps:
    - uses: actions/checkout@v6
      with:
        fetch-depth: 0

    - name: Build instar (VMM + guest binaries)
      run: make instar

    - name: Package binary
      run: |
        TAG_NAME="${GITHUB_REF#refs/tags/}"
        ARCHIVE="instar-${TAG_NAME}-${{ matrix.target }}.tar.gz"
        mkdir staging
        cp target/release/instar staging/
        cp LICENSE README.md staging/
        tar -czf "${ARCHIVE}" -C staging .
        echo "ARCHIVE=${ARCHIVE}" >> "$GITHUB_ENV"

    - uses: actions/upload-artifact@v6
      with:
        name: binary-${{ matrix.target }}
        path: ${{ env.ARCHIVE }}
```

Notes on this approach:
- `make instar` builds the guest binaries via Docker (nightly Rust,
  bare-metal cross-compile) and then builds the VMM binary. This
  is the existing build path and already works in CI.
- Start with `x86_64-unknown-linux-gnu` only. The musl static
  build would need changes to the Makefile/Dockerfile and can be
  added later.
- aarch64 targets can be added once there is demand. The guest
  binaries are always x86-64 bare-metal (they run inside KVM),
  so only the VMM binary needs cross-compilation for aarch64.

### Sign-tag job

Carry over directly from the Python template (Sigstore/gitsign).

### No PyPI job

Remove the `publish-pypi` job entirely.

### GitHub Release job

Adapt to upload binary tarballs instead of Python wheels:

```yaml
github-release:
  name: Create GitHub Release
  needs: [build, sign-tag]
  runs-on: [self-hosted, static]
  permissions:
    contents: write
  steps:
    - uses: actions/download-artifact@v7
      with:
        pattern: binary-*
        merge-multiple: true

    - uses: softprops/action-gh-release@v2
      with:
        generate_release_notes: true
        files: "*.tar.gz"
      env:
        GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### Optional: crates.io publish job

If we decide to publish the library crates (qcow2, vmdk, etc.)
to crates.io, add a job that runs `cargo publish` for each
publishable crate in dependency order. This requires:
- A crates.io API token stored as a GitHub secret, OR
- crates.io trusted publishing (OIDC-based, similar to PyPI --
  this is available but newer)

**Recommendation:** Skip crates.io for v0.1.0. The library crates
use path dependencies on `shared` which would need to be converted
to published crate references first. Revisit when there is user
demand for the crates as standalone libraries.

## Phase 3: Documentation gaps

### CHANGELOG.md

Create a CHANGELOG.md for v0.1.0 documenting:
- Initial public release
- Operations: info, copy, check, compare, convert
- Input formats: raw, QCOW2 (v2/v3, zlib/zstd compression,
  extended L2, encryption, external data files, snapshots),
  VMDK (monolithicSparse, streamOptimized), VHD (fixed, dynamic,
  differencing), VHDX (with CRC-32C), LUKS (v1/v2)
- Output formats: raw, QCOW2 v3, VMDK, VHD
- Security model: KVM sandbox isolation, backing chain allowlist
- qemu-img CLI compatibility

### SECURITY.md

Create a SECURITY.md with:
- Vulnerability reporting instructions (email? GitHub security
  advisories?)
- Supported versions table
- Security model overview (link to docs/security.md)
- Response timeline expectations

### Installation instructions in README

The README already exists and is comprehensive but needs an
"Installation" section covering:
1. Download pre-compiled binary from GitHub Releases
2. `cargo install instar` (with note about build requirements)
3. System requirements: Linux, KVM access (`/dev/kvm`), user in
   `kvm` group

## Phase 4: Version and tagging

### Versioning strategy

- v0.1 was tagged 2026-01-28 as an internal pre-release
  (info + copy only, no public binary distribution)
- First public release will be v0.2.0
- `0.x.y` signals "works but interface may change"
- Increment minor (`0.3.0`) for new features/operations
- Increment patch (`0.2.1`) for bug fixes
- Move to `1.0.0` only when the CLI interface and format handling
  are stable

### Release process

1. Run `make release VERSION=0.2.0` (bumps all Cargo.toml
   files, commits, and tags)
2. Update CHANGELOG.md (move Unreleased to dated section)
3. Review the commit and tag
4. Push: `git push origin HEAD && git push origin v0.2.0`
5. The release workflow runs automatically:
   - Verifies Cargo.toml version matches the tag (files
     a GitHub issue on mismatch)
   - Builds the binary via `make instar`
   - Packages into tarball with guest binaries
   - Signs the tag with Sigstore
   - Creates a GitHub Release with the tarball
6. Approve the release in the GitHub `release` environment
7. Verify: GitHub Release appears with binary artifacts

### GitHub setup (one-time)

- Create a `release` environment with required reviewers
- Optionally: protect `v*` tags so only the release workflow
  can create them

## Phase 5: Pre-release audit

Discharged as a v0.2.0 gate — the execution record, including which
items passed and which were carried forward, is in
`PLAN-release-v0.2.md`. The recurring items are now standing CI
(see the Status section). The checklist below is retained as the
historical statement of what the gate required.

Before tagging v0.2.0:

- [ ] Complete VMM boundary audit (PLAN-audit.md Phase 5) --
  this is the most important remaining security item
- [ ] Run `cargo clippy --all-targets` with no warnings
- [ ] Run `cargo audit` for known vulnerable dependencies
- [ ] Run full test suite (`make test`)
- [ ] Run `pre-commit run --all-files`
- [ ] Verify no sensitive paths/secrets in the codebase
- [ ] Verify PLAN-*.md files are excluded from published artifacts
  (already in .gitignore)
- [ ] Review README.md for accuracy with current feature set

## What to defer

These are explicitly NOT needed for v0.1.0:

- **crates.io publishing** -- the build requirements (Docker,
  nightly Rust) make `cargo install` impractical for most users.
  Pre-compiled binaries are the right primary channel.
- **musl static builds** -- the gnu build works on all major
  distros. Add musl later for minimal/container environments.
- **aarch64 builds** -- add when there is demand.
- **`.deb` / `.rpm` packaging** -- nice to have but adds
  complexity. GitHub Releases tarballs are sufficient for v0.1.0.
- **Homebrew / AUR / Nix** -- community-driven, add based on
  demand.
- **Additional qemu-img subcommands** (create, resize, snapshot,
  rebase, commit, map, measure) -- info, check, compare, and
  convert cover the core use case.
- **cargo-dist / release-plz** -- more automation than needed for
  a first release. The custom workflow gives full control over
  the unusual build process (Docker guest cross-compilation).

## Execution order

1. Phase 1: Cargo.toml metadata (small, mechanical)
2. Phase 3: Documentation gaps (CHANGELOG, SECURITY.md,
   README install section)
3. Phase 5: Pre-release audit checklist
4. Phase 2: Release workflow (needs testing via workflow_dispatch)
5. Phase 4: Tag and release
