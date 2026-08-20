# Cross-Platform Packaging for Ryll

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table like this in this master plan under the
Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Message parsing | PLAN-thing-phase-01-parsing.md | Not started |
| 2. Decompression | PLAN-thing-phase-02-decomp.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll is a Rust SPICE VDI test client with both GUI
(egui/eframe) and headless modes. Today it builds and runs
only on Linux x86_64, using a Docker devcontainer for
reproducible builds. Distribution is manual (`scp` of a
release binary to the target machine).

### Current build infrastructure

- Docker devcontainer (`make build`, `make release`) based
  on Debian with Rust stable
- Pre-commit hooks for rustfmt, clippy, shellcheck
- No CI/CD pipeline (no `.github/` directory)
- No formal packaging for any platform
- No `Cargo.lock` in version control

### Platform portability issues in existing code

1. **Signal handling is Unix-only.** `src/main.rs` uses
   `libc::signal(libc::SIGINT, ...)` for graceful Ctrl+C
   shutdown. This will not compile on Windows.

2. **No conditional compilation.** There are zero
   `#[cfg(target_os)]` guards anywhere in the codebase.

3. **Dynamic linking to graphics libraries.** On Linux the
   binary links against libxcb, libX11, libGL, libEGL,
   libwayland at runtime. On macOS, eframe/egui uses Metal
   and AppKit natively. On Windows, it uses Direct3D/WinAPI.
   The egui/eframe crate handles this transparently — no
   ryll code changes are needed for the graphics backend.

4. **openh264 bundles its C library.** The `openh264` crate
   (used for `--capture` video encoding) includes Cisco's
   OpenH264 source and builds it via `cc`. This should work
   on all platforms with a C compiler, no system package
   needed.

### Dependencies by platform

**Linux (Debian/Ubuntu) build-time:**
```
build-essential pkg-config cmake libxcb-render0-dev
libxcb-shape0-dev libxcb-xfixes0-dev libxcb1-dev
libx11-dev libxkbcommon-dev libgl1-mesa-dev
libegl1-mesa-dev libwayland-dev libssl-dev
```

**Linux (Fedora/RHEL) build-time:**
```
gcc gcc-c++ pkg-config cmake libxcb-devel libX11-devel
libxkbcommon-devel mesa-libGL-devel mesa-libEGL-devel
wayland-devel openssl-devel
```

**macOS build-time:**
- Xcode Command Line Tools (provides clang, Metal SDK)
- No additional system packages needed — egui uses Metal
  backend and AppKit windowing natively

**Windows build-time:**
- MSVC Build Tools (Visual Studio)
- No additional system packages — egui uses Direct3D and
  WinAPI natively

## Mission and problem statement

Add cross-platform packaging so that ryll can be distributed
as pre-built packages for:

1. **Debian/Ubuntu** — `.deb` packages
2. **Fedora/RHEL** — `.rpm` packages
3. **macOS** — Homebrew formula (both Intel and Apple Silicon)
4. **Windows** — `.zip` archive with `.exe` (and optionally
   `.msi` installer)

This requires:
- Fixing platform portability issues in the source code
- Setting up GitHub Actions CI with a multi-platform build
  matrix
- Adding packaging configuration for each target format
- Creating a release workflow that builds and publishes
  packages when a git tag is pushed

## Open questions

1. **Cargo.lock in version control?** Rust best practice for
   binary crates is to commit `Cargo.lock`. This ensures
   reproducible builds across CI and developer machines. We
   should add it as part of this work. **Decision: yes, commit
   it.**

2. **Minimum macOS version?** eframe 0.29 requires macOS
   10.14+ (Mojave), released 2018 and long unsupported.
   Apple currently supports macOS 14 Sonoma (2023), 15
   Sequoia (2024), and 26 Tahoe (2025). **Decision: set
   `MACOSX_DEPLOYMENT_TARGET=14.0` (Sonoma) as the minimum
   supported version. This covers all Apple-supported
   releases.**

3. **Windows capture mode?** The `--capture` feature uses
   `openh264` which bundles its own C code via `cc`. Rather
   than debugging MSVC build issues for a marginal target,
   **Decision: disable `--capture` on Windows for now.**
   Feature-gate the capture dependencies (`openh264`, `mp4`,
   `pcap-file`, `etherparse`) and capture code behind a Cargo
   feature (e.g. `capture`) that is enabled by default on
   Unix but not on Windows. Can be revisited later.

4. **Universal macOS binaries?** **Decision: Apple Silicon
   (aarch64) only.** Apple is dropping security updates for
   all Intel Macs in 2026. No point shipping x86_64 macOS
   binaries for a platform with no supported users. Can
   revisit if someone asks.

5. **Homebrew tap vs core?** **Decision: create a tap
   (`shakenfist/homebrew-tap`).** homebrew-core requires
   popularity and review. Include installation instructions
   for the tap (and all other pre-compiled package formats)
   in `docs/installation.md`.

6. **RPM: spec file vs cargo-generate-rpm?** **Decision:
   use `cargo-generate-rpm`.** Generates RPMs directly from
   Cargo metadata, no rpmbuild infrastructure needed. Only
   revisit with a spec file if we hit a limitation.

7. **Should we keep the Docker-based build for Linux?**
   **Decision: yes, keep the Docker build.** The developer
   uses multiple Rust versions across projects, so the
   containerised build ensures the right toolchain without
   polluting the host. CI builds natively (GitHub runners
   provide the toolchain) to produce distributable artifacts,
   but the Makefile Docker targets remain the primary local
   development workflow.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Platform portability | [PLAN-packaging-phase-01-portability.md](/components/ryll/plans/PLAN-packaging-phase-01-portability/) | Complete |
| 2. GitHub Actions CI | [PLAN-packaging-phase-02-ci.md](/components/ryll/plans/PLAN-packaging-phase-02-ci/) | Complete |
| 3. Debian packaging | [PLAN-packaging-phase-03-debian.md](/components/ryll/plans/PLAN-packaging-phase-03-debian/) | Complete |
| 4. RPM packaging | [PLAN-packaging-phase-04-rpm.md](/components/ryll/plans/PLAN-packaging-phase-04-rpm/) | Complete |
| 5. macOS packaging | [PLAN-packaging-phase-05-macos.md](/components/ryll/plans/PLAN-packaging-phase-05-macos/) | Complete |
| 6. Windows packaging | [PLAN-packaging-phase-06-windows.md](/components/ryll/plans/PLAN-packaging-phase-06-windows/) | Complete |
| 7. Release automation | [PLAN-packaging-phase-07-release.md](/components/ryll/plans/PLAN-packaging-phase-07-release/) | Complete |

### Phase 1: Platform portability

Make the code compile on Linux, macOS, and Windows.

**Changes needed:**

- Add `Cargo.lock` to version control.
- Replace the raw `libc::signal(SIGINT, ...)` handler in
  `src/main.rs` with the `ctrlc` crate. It's pure Rust,
  works on both Unix and Windows, and lets us drop the
  `libc` dependency entirely (nothing else uses it).
- Verify the rest of the codebase compiles for
  `x86_64-apple-darwin`, `aarch64-apple-darwin`, and
  `x86_64-pc-windows-msvc` targets (may surface other
  issues).

### Phase 2: GitHub Actions CI

Set up continuous integration with a build matrix.

**Workflow: `ci.yml`**
- Trigger on push to `develop`, pull requests
- Matrix: `ubuntu-latest`, `macos-latest` (ARM),
  `windows-latest`
- Steps: install platform deps, `cargo build --release`,
  `cargo test`, `cargo fmt --check`, `cargo clippy`
- Cache `~/.cargo` and `target/` for speed
- Linux job also runs `pre-commit run --all-files`

### Phase 3: Debian packaging

Produce `.deb` packages for `amd64`.

**Approach:** Use `cargo-deb`. Add metadata to `Cargo.toml`:

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

`$auto` uses `dpkg-shlibdeps` to detect runtime library
dependencies automatically.

The CI workflow builds the `.deb` and uploads it as an
artifact.

### Phase 4: RPM packaging

Produce `.rpm` packages for `x86_64`.

**Approach:** Use `cargo-generate-rpm`. Add metadata to
`Cargo.toml`:

```toml
[package.metadata.generate-rpm]
assets = [
    { source = "target/release/ryll", dest = "/usr/bin/ryll", mode = "0755" },
]
```

Build in CI on `ubuntu-latest` (cargo-generate-rpm doesn't
need rpmbuild, it generates the RPM directly from the
binary).

### Phase 5: macOS packaging

Produce binaries for both Intel and Apple Silicon Macs,
distributed via Homebrew.

**Approach:**

- Build on `macos-latest` (Apple Silicon) GitHub runner
- Create tarball: `ryll-{version}-aarch64-apple-darwin.tar.gz`
- Create a Homebrew tap repository
  (`shakenfist/homebrew-tap`) with a formula that
  downloads the tarball

### Phase 6: Windows packaging

Produce a `.zip` archive containing `ryll.exe`.

**Approach:**

- Build on `windows-latest` with MSVC toolchain
- Package as `ryll-{version}-x86_64-pc-windows-msvc.zip`
- MSI installer is a nice-to-have for later (requires
  WiX toolset and more complexity)

### Phase 7: Release automation

Tie everything together with a release workflow.

**Workflow: `release.yml`**
- Trigger on push of a version tag (`v*`)
- Build all platform artifacts (reuse CI matrix)
- Create a GitHub Release with all artifacts attached:
  - `ryll_{version}_amd64.deb`
  - `ryll-{version}-1.x86_64.rpm`
  - `ryll-{version}-aarch64-apple-darwin.tar.gz`
  - `ryll-{version}-x86_64-pc-windows-msvc.zip`
- Update Homebrew tap formula with new version and SHA256s

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* New code follows existing patterns: channel handler
  structure, message parsing via `byteorder`, async tasks
  via tokio, event communication via mpsc channels.
* There are unit tests for new logic, and the existing tests
  still pass (`make test`).
* Lines are wrapped at 120 characters, single quotes for
  Rust strings where applicable.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` have been
  updated if the change adds or modifies channels, message
  types, or compression algorithms.
* Documentation in `docs/` has been updated to describe any
  new features or configuration options.
* `Cargo.lock` is committed and kept up to date.
* CI runs on every push to `develop` and on pull requests,
  building and testing on Linux, macOS, and Windows.
* A tagged release produces downloadable `.deb`, `.rpm`,
  macOS tarballs, and Windows `.zip` artifacts on the
  GitHub Releases page.
* The Homebrew tap formula installs a working `ryll` on
  macOS.

### Future work

* **Universal macOS binary** — use `lipo` to combine x86_64
  and aarch64 into a single universal binary.
* **MSI installer for Windows** — use WiX toolset to produce
  a proper Windows installer with Start Menu shortcuts.
* **AUR package** — Arch Linux user repository package.
* **Flatpak/Snap** — containerised Linux distribution.
* **ARM Linux builds** — aarch64 Linux binaries for
  Raspberry Pi / ARM servers.
* **Automated Homebrew tap updates** — GitHub Action that
  auto-updates the formula on new releases.
* **Code signing** — sign macOS and Windows binaries to
  avoid security warnings.
* **Static musl builds** — fully static Linux binaries for
  headless-only use (no GUI support with musl).
* **Reproducible builds** — ensure byte-identical output
  across build environments.

### Bugs fixed during this work

(None yet.)

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
