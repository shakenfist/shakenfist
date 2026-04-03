# Phase 4: RPM Packaging

Parent plan: [PLAN-packaging.md](/components/ryll/plans/PLAN-packaging/)

## Goal

Produce `.rpm` packages for ryll on `x86_64` using
`cargo-generate-rpm`. The Linux CI job is extended to build
the `.rpm` alongside the `.deb` and upload it as an artifact.

## Current state

- The Linux CI job already builds the release binary, runs
  tests, and produces a `.deb` via cargo-deb.
- `Cargo.toml` has `[package.metadata.deb]` but no RPM
  metadata.
- `docs/installation.md` has an RPM placeholder.

## Design decisions

### cargo-generate-rpm, not rpmbuild

Per the master plan decision (question 6), we use
`cargo-generate-rpm` which generates RPMs directly from the
binary and Cargo.toml metadata. No `rpmbuild`, spec files,
or Fedora build infrastructure needed. It runs on any Linux
system including Ubuntu CI runners.

### Automatic dependency detection

`cargo-generate-rpm` has a built-in `--auto-req builtin`
mode that uses `ldd` to detect shared library dependencies
and maps them to RPM package names. This is the RPM
equivalent of cargo-deb's `$auto`. We use the default
`--auto-req auto` which selects the best available method.

### Same CI job as .deb

Both `.deb` and `.rpm` are produced in the same Linux build
job. The release binary is built once and reused for both
packaging steps.

## Changes

### Step 1: Add cargo-generate-rpm metadata to Cargo.toml

Add a `[package.metadata.generate-rpm]` section:

```toml
[package.metadata.generate-rpm]
assets = [
    { source = "target/release/ryll", dest = "/usr/bin/ryll", mode = "0755" },
]
```

`cargo-generate-rpm` automatically derives `Name`, `Version`,
`License`, `Summary`, and `URL` from the existing `[package]`
fields (`name`, `version`, `license`, `description`,
`repository`).

**Files changed:**
- `Cargo.toml` — add `[package.metadata.generate-rpm]`

**Commit:** standalone.

### Step 2: Add cargo-generate-rpm steps to CI workflow

Add steps to the Linux build job, after the existing
cargo-deb steps:

```yaml
- name: Install cargo-generate-rpm
  if: runner.os == 'Linux'
  run: cargo install cargo-generate-rpm --locked

- name: Build .rpm package
  if: runner.os == 'Linux'
  run: cargo generate-rpm

- name: Upload .rpm artifact
  if: runner.os == 'Linux'
  uses: actions/upload-artifact@v4
  with:
    name: rpm-package
    path: target/generate-rpm/*.rpm
    retention-days: 30
```

Notes:
- `cargo generate-rpm` (no `--` prefix) uses the release
  binary already built. It looks for
  `target/release/<name>` by default.
- Output goes to `target/generate-rpm/`.
- Automatic dependency detection runs by default.

**Files changed:**
- `.github/workflows/ci.yml` — add three steps

**Commit:** standalone.

### Step 3: Test locally

Verify the RPM builds in the devcontainer:

```bash
docker run --rm \
    -v "$(pwd)":/workspace \
    ... \
    ryll-dev \
    sh -c "cargo install cargo-generate-rpm --locked && \
           cargo build --release && \
           cargo generate-rpm"
```

Verify the RPM contents with `rpm -qip target/generate-rpm/*.rpm`
(if rpm tools are available) or just check the file exists
and is non-trivial in size.

### Step 4: Update documentation

- Update `docs/installation.md` RPM section with real
  instructions.

**Files changed:**
- `docs/installation.md`

**Commit:** standalone.

## Step summary

| Step | Description | Files | Commit |
|------|-------------|-------|--------|
| 1 | Add RPM metadata to Cargo.toml | `Cargo.toml` | Yes |
| 2 | Add cargo-generate-rpm to CI | `.github/workflows/ci.yml` | Yes |
| 3 | Test locally | (verify only) | No |
| 4 | Update documentation | `docs/installation.md` | Yes |

## Risks and mitigations

- **Auto-req on Ubuntu runner:** The `builtin` auto-req uses
  `ldd` which works on any Linux. However, it may detect
  Ubuntu/Debian library names that differ from Fedora/RHEL
  RPM package names. In practice, `cargo-generate-rpm`'s
  builtin mode records the shared library `SONAME` directly
  (e.g. `libc.so.6`) rather than package names, which is
  portable across RPM-based distros. The RPM resolver on the
  target system maps SONAMEs to packages at install time.

- **cargo-generate-rpm install time:** Similar to cargo-deb,
  this compiles from source. `Swatinem/rust-cache` will cache
  the installed binary after the first run.

## Open questions

None.
