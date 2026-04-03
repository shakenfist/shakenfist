# Phase 3: Debian Packaging

Parent plan: [PLAN-packaging.md](/components/ryll/plans/PLAN-packaging/)

## Goal

Produce `.deb` packages for ryll on `amd64` using
`cargo-deb`. The CI workflow (Phase 2) is extended to build
the `.deb` and upload it as an artifact on every run, so
packages are available for download from any PR or develop
push.

## Current state

- CI builds and tests ryll on Linux, macOS, and Windows.
- `Cargo.toml` has `name`, `version`, `description`,
  `license`, and `authors` fields, but no `homepage` or
  `repository`.
- No `[package.metadata.deb]` section exists.
- No packaging tooling is installed in CI.

## Design decisions

### cargo-deb, not native dpkg-buildpackage

`cargo-deb` generates `.deb` packages directly from
`Cargo.toml` metadata without needing a `debian/` directory,
`debhelper`, or a full Debian packaging environment. This is
simpler and sufficient for a single-binary package.

### $auto dependency detection

`cargo-deb` supports `depends = "$auto"` which runs
`dpkg-shlibdeps` on the built binary to detect dynamically
linked libraries. This automatically produces the correct
runtime dependency list (e.g. `libxcb1`, `libgl1`,
`libwayland-client0`). No manual dependency tracking needed.

### Build in CI, not locally

The `.deb` is built in the Linux CI job (which already
builds the release binary). We just add `cargo install
cargo-deb` and `cargo deb --no-build` (reuse the existing
release binary) as additional steps.

### Package naming

`cargo-deb` produces packages named
`ryll_{version}_{arch}.deb` by default (e.g.
`ryll_0.1.0_amd64.deb`). This follows Debian naming
conventions.

## Changes

### Step 1: Add repository URL to Cargo.toml

`cargo-deb` uses the `homepage` or `repository` field for
the package metadata. Add:

```toml
repository = "https://github.com/shakenfist/ryll"
```

This is good practice regardless of packaging — it helps
cargo, crates.io, and other tools link to the source.

**Files changed:**
- `Cargo.toml` — add `repository` field to `[package]`

**Commit:** combined with step 2 (both are Cargo.toml
metadata).

### Step 2: Add cargo-deb metadata to Cargo.toml

Add a `[package.metadata.deb]` section:

```toml
[package.metadata.deb]
maintainer = "Michael Still <mikal@stillhq.com>"
section = "utils"
priority = "optional"
depends = "$auto"
assets = [
    ["target/release/ryll", "usr/bin/", "755"],
]
```

Fields:
- `maintainer` — required by Debian policy, used in the
  control file.
- `section` — `utils` is appropriate for a general utility.
- `priority` — `optional` (standard for non-essential
  packages).
- `depends` — `$auto` runs `dpkg-shlibdeps` to detect
  runtime shared library dependencies.
- `assets` — maps the release binary to `/usr/bin/ryll`
  with mode 755.

`cargo-deb` automatically derives `Package`, `Version`,
`Architecture`, `Description`, and `License` from the
existing `[package]` fields.

**Files changed:**
- `Cargo.toml` — add `[package.metadata.deb]` section

**Commit:** standalone, together with step 1.

### Step 3: Add cargo-deb step to CI workflow

Extend the Linux build job in `ci.yml` to produce and
upload the `.deb` artifact.

New steps after the existing "Run tests" step, conditional
on `runner.os == 'Linux'`:

```yaml
- name: Install cargo-deb
  if: runner.os == 'Linux'
  run: cargo install cargo-deb --locked

- name: Build .deb package
  if: runner.os == 'Linux'
  run: cargo deb --no-build

- name: Upload .deb artifact
  if: runner.os == 'Linux'
  uses: actions/upload-artifact@v4
  with:
    name: deb-package
    path: target/debian/*.deb
    retention-days: 30
```

Notes:
- `cargo deb --no-build` reuses the release binary already
  built by the "Build" step, avoiding a redundant
  compilation.
- `--locked` ensures cargo-deb uses its own lockfile for
  reproducible installs.
- The artifact is uploaded with 30-day retention so it's
  available for download from the Actions run.
- `upload-artifact@v4` with a glob pattern picks up whatever
  `.deb` file cargo-deb produces.

**Files changed:**
- `.github/workflows/ci.yml` — add three steps to the
  build job, gated on `runner.os == 'Linux'`

**Commit:** standalone.

### Step 4: Test locally

Before pushing, verify the `.deb` builds correctly in the
devcontainer. This requires installing `cargo-deb` inside
the container:

```bash
docker run --rm \
    -v "$(pwd)":/workspace \
    -v "$(pwd)/.cargo-cache/registry":/build/.cargo/registry \
    -v "$(pwd)/.cargo-cache/git":/build/.cargo/git \
    -w /workspace \
    -u $(id -u):$(id -g) \
    -e HOME=/build \
    ryll-dev \
    sh -c "cargo install cargo-deb --locked && \
           cargo build --release && \
           cargo deb --no-build"
```

Verify:
- The `.deb` is created in `target/debian/`.
- `dpkg-deb --info target/debian/*.deb` shows correct
  metadata (package name, version, maintainer, depends).
- `dpkg-deb --contents target/debian/*.deb` shows
  `./usr/bin/ryll`.

If `dpkg-shlibdeps` is not available in the devcontainer
(it's part of `dpkg-dev`), it may need to be added to the
Dockerfile, or we can test with explicit depends instead of
`$auto`. In CI (`ubuntu-latest`), `dpkg-shlibdeps` is
available by default.

### Step 5: Update documentation

- Update `docs/installation.md` (create it — per the master
  plan decision on question 5, we need installation docs for
  all package formats).
- Update `README.md` to mention `.deb` availability.

**Files changed:**
- `docs/installation.md` — new file with Debian install
  instructions
- `README.md` — brief mention

**Commit:** standalone.

## Step summary

| Step | Description | Files | Commit |
|------|-------------|-------|--------|
| 1-2 | Add repo URL + deb metadata to Cargo.toml | `Cargo.toml` | Yes (combined) |
| 3 | Add cargo-deb to CI | `.github/workflows/ci.yml` | Yes |
| 4 | Test locally | (verify only) | No |
| 5 | Update documentation | `docs/installation.md`, `README.md` | Yes |

## Risks and mitigations

- **dpkg-shlibdeps not in devcontainer:** The `$auto`
  depends feature needs `dpkg-dev` installed. The GitHub
  `ubuntu-latest` runner has this. The devcontainer may not.
  If local testing fails, we can add `dpkg-dev` to the
  Dockerfile or just rely on CI. Not a blocker.

- **Missing runtime deps:** If `$auto` misses a library
  (e.g. one loaded via `dlopen` rather than linked), the
  `.deb` will install but ryll won't run. This is unlikely
  for ryll — all deps are directly linked. Can be caught by
  testing the installed package.

- **cargo-deb install time in CI:** Installing cargo-deb
  compiles it from source (~30-60s). This adds to CI time.
  Acceptable for now. If it becomes a problem, we can
  pre-build it or use a binary release. `Swatinem/rust-cache`
  will cache the installed binary on subsequent runs.

## Open questions

None — all decisions were made in the master plan and
Phase 2.
