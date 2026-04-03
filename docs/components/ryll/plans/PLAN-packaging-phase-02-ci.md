# Phase 2: GitHub Actions CI

Parent plan: [PLAN-packaging.md](/components/ryll/plans/PLAN-packaging/)

## Goal

Set up continuous integration that builds and tests ryll on
Linux, macOS (Apple Silicon), and Windows on every push to
`develop` and on pull requests. This validates that the Phase
1 portability changes actually work on native runners.

## Current state

- No `.github/` directory exists — ryll has no CI.
- Local linting runs via Docker (pre-commit hooks call
  `scripts/check-rust.sh` which invokes rustfmt and clippy
  inside the devcontainer).
- Tests pass on Linux with both default features and
  `--no-default-features`.

## Design decisions

### Two jobs, not one matrix

Split CI into two jobs rather than a single matrix:

1. **`lint`** — runs on `ubuntu-latest` only. Checks code
   formatting and linting. No point running this on every
   platform — Rust formatting and clippy output are
   platform-independent.

2. **`build`** — runs on a 3-element matrix (`ubuntu-latest`,
   `macos-latest`, `windows-latest`). Builds and tests on
   each platform.

This gives fast feedback on formatting issues without waiting
for all three platforms to build.

### Native Rust, not Docker

CI runs `cargo` directly on the GitHub runner, not inside the
devcontainer. The Docker build is for local development (where
the developer may not have Rust installed or wants a specific
version). GitHub runners have Rust pre-installed via
`dtolnay/rust-toolchain` and native platform SDKs.

### Pre-commit not used in CI

The pre-commit hook (`scripts/check-rust.sh`) runs rustfmt
and clippy inside Docker. In CI we run these directly since
the runner already has Rust. No need to build a Docker image
just to run `cargo fmt --check`.

### Feature flags per platform

| Platform | Feature flags | Reason |
|----------|---------------|--------|
| Linux | `--features capture` (default) | Full build |
| macOS | `--features capture` (default) | Full build |
| Windows | `--no-default-features` | Capture disabled |

### Caching

Use `Swatinem/rust-cache` for efficient Cargo and target
directory caching. This is the standard approach for Rust CI
and handles cache key generation, cleanup, and cross-job
sharing.

## Changes

### Step 1: Create `.github/workflows/ci.yml`

Single workflow file with two jobs.

**Trigger:**

```yaml
on:
  push:
    branches: [develop]
  pull_request:
    branches: [develop]
```

**Job 1: `lint`**

```yaml
lint:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: dtolnay/rust-toolchain@stable
      with:
        components: rustfmt, clippy
    - uses: Swatinem/rust-cache@v2
    - name: Install Linux dependencies
      run: |
        sudo apt-get update
        sudo apt-get install -y \
          pkg-config cmake \
          libxcb-render0-dev libxcb-shape0-dev \
          libxcb-xfixes0-dev libxcb1-dev \
          libx11-dev libxkbcommon-dev \
          libgl1-mesa-dev libegl1-mesa-dev \
          libwayland-dev libssl-dev
    - name: Check formatting
      run: cargo fmt --check
    - name: Run clippy
      run: cargo clippy -- -D warnings
```

The Linux deps are needed because clippy compiles the code
and eframe needs the graphics headers.

**Job 2: `build`**

```yaml
build:
  strategy:
    matrix:
      include:
        - os: ubuntu-latest
          target: x86_64-unknown-linux-gnu
          features: ""
        - os: macos-latest
          target: aarch64-apple-darwin
          features: ""
        - os: windows-latest
          target: x86_64-pc-windows-msvc
          features: "--no-default-features"
  runs-on: ${{ matrix.os }}
  env:
    MACOSX_DEPLOYMENT_TARGET: "14.0"
  steps:
    - uses: actions/checkout@v4
    - uses: dtolnay/rust-toolchain@stable
    - uses: Swatinem/rust-cache@v2
      with:
        key: ${{ matrix.target }}
    - name: Install Linux dependencies
      if: runner.os == 'Linux'
      run: |
        sudo apt-get update
        sudo apt-get install -y \
          pkg-config cmake \
          libxcb-render0-dev libxcb-shape0-dev \
          libxcb-xfixes0-dev libxcb1-dev \
          libx11-dev libxkbcommon-dev \
          libgl1-mesa-dev libegl1-mesa-dev \
          libwayland-dev libssl-dev
    - name: Build
      run: cargo build --release ${{ matrix.features }}
    - name: Run tests
      run: cargo test ${{ matrix.features }}
```

Notes:
- `MACOSX_DEPLOYMENT_TARGET=14.0` is set as a job-level env
  var. It's harmless on non-macOS runners.
- The `features` field is empty string for Linux/macOS
  (uses default features) and `--no-default-features` for
  Windows.
- `Swatinem/rust-cache` uses `matrix.target` as a cache key
  suffix so each platform has its own cache.
- No Linux-specific deps needed on macOS or Windows — eframe
  uses native backends there.

### Step 2: Verify the workflow

After creating the file, we can't fully test it locally (no
GitHub Actions runner). But we can:

- Validate the YAML syntax.
- Ensure the workflow triggers are correct.
- Push the branch and watch the first CI run.

The first run on macOS and Windows will be the real
validation of Phase 1's portability changes.

### Step 3: Update documentation

- Update `AGENTS.md` build section to mention CI.
- Update `README.md` to add a CI badge (optional, can be
  done after the first successful run).
- Mention CI in `docs/portability.md` if not already there
  (Phase 1 already added a reference).

## Step summary

| Step | Description | Files | Commit |
|------|-------------|-------|--------|
| 1 | Create CI workflow | `.github/workflows/ci.yml` | Yes |
| 2 | Verify workflow runs | (push and observe) | No |
| 3 | Update documentation | `AGENTS.md`, `README.md` | Yes |

## Risks and mitigations

- **macOS build fails on native deps:** eframe uses Metal
  backend on macOS, which is included in Xcode Command Line
  Tools (pre-installed on `macos-latest` runners). The
  `openh264` crate builds its bundled C code via `cc`, which
  uses the Xcode clang. Risk is low.

- **Windows build fails on aws-lc-sys:** The `reqwest` crate
  uses `aws-lc-rs` by default for TLS, which includes C code
  built via `cmake`. MSVC runners have cmake pre-installed.
  If this fails, we can switch reqwest to use `rustls` with
  the `ring` crypto backend instead (add
  `features = ["blocking", "rustls-tls"]` and
  `default-features = false` to reqwest in Cargo.toml). This
  is a known pain point with aws-lc-sys on Windows.

- **GitHub Actions minutes:** The three-platform matrix runs
  on every push and PR. macOS runners cost 10x and Windows
  runners cost 2x vs Linux. For a small project this is fine,
  but if costs become a concern, macOS/Windows builds could
  be moved to a release-only workflow. For now, keeping them
  in CI catches portability regressions early.

- **Stale cache:** `Swatinem/rust-cache` automatically
  handles cache invalidation based on `Cargo.lock` hash.
  No manual cache management needed.

## Open questions

None — all decisions were made in the master plan.
