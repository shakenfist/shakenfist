# Pip-installable ryll (`ryll`)

This is **phase 3 of a cross-repository master plan** whose plan of
record lives in the Shaken Fist repository:

> `shakenfist/docs/plans/PLAN-kerbside-vdi-tokens.md`
> (branch `vdi-console-tokens` until merged)

It is self-contained and can land independently of the other phases.

## Prompt

Make ryll pip-installable so that `pip install ryll` (typically pulled
in via the client's `shakenfist-client[vdi]` extra in phase 4) puts a
working `ryll` executable on the venv's PATH, the way `pip install
kerbside` ships `kerbside-proxy`. Phase 4's `sf-client instance
vdiconsole` then launches ryll with the kerbside exchange URL and the
user lands in a SPICE session with none of the token plumbing visible.
This phase absorbs the packaging complexity so phase 4 sees only a
binary that is or is not on PATH.

## Situation

- **ryll is a GUI binary in a Cargo workspace.** The `ryll` crate
  (`ryll/Cargo.toml`) is the SPICE VDI client; default features
  `["capture", "gui", "audio"]` pull in `eframe` (winit/wayland/xkb),
  `arboard`, `rfd`, and `cpal`/`opus`. A `--no-default-features` build
  is headless, which is not what a user needs to see a console.
- **ryll builds in Docker**; host policy is no native Rust toolchain.
- **ryll already has a release pipeline** (`.github/workflows/release.yml`,
  on `v*` tags): per-target `.deb`/`.rpm`/macOS-`.tar.gz`/Windows-`.zip`
  attached to a GitHub Release, plus `make publish-crates` to crates.io.
  `check-version` enforces that the tag equals `[workspace.package].version`.
- **The kerbside-proxy precedent** ships a maturin `bindings = "bin"`
  wheel that lays the compiled binary into the wheel's `*.data/scripts/`
  so pip puts it on PATH. kerbside-proxy is headless, so its
  `--zig --compatibility manylinux_2_28` build both links and tags
  cleanly.

## Mission and problem statement

Deliver a PyPI package **`ryll`** such that, in a venv, `pip install
ryll` (or `pip install shakenfist-client[vdi]`) yields a `ryll` on PATH
that phase 4 can launch with `ryll --url <…>`, with no runtime download
and working offline immediately after install. Contain all packaging
complexity here; phase 4 sees only "ryll present or not" and degrades
to `remote-viewer` otherwise.

## Alternatives considered

### A. Embed the binary in per-arch manylinux wheels — **chosen**

Build the ryll GUI binary inside a `manylinux_2_28` container that has
the GUI/audio dev libraries installed, and ship it with maturin
`bindings = "bin"` as a per-arch platform wheel
(`manylinux_2_28_x86_64` / `_aarch64`). `pip install ryll` puts the
binary straight on PATH; nothing is fetched at runtime. Chosen for a
self-contained, offline-capable install (operator preference,
2026-07-20). The `--zig` shortcut kerbside-proxy uses does **not**
work here (zig's sysroot lacks the GUI/audio dev libs), so the build
runs natively inside the manylinux container instead — see Decisions.

### B. Fetch the release binary at first run — rejected

`ryll` as a pure-Python `py3-none-any` wheel whose console-script
downloads + SHA256-verifies the native release binary on first run.
Fully built and working at one point, then dropped in favour of A:
embedding avoids a runtime GitHub dependency, works offline right after
`pip install`, and needs no launcher/download/verify code on every
user's machine. The cost A carries instead — a manylinux build lane and
larger per-arch wheels — lives in our CI, not on users.

### C. Compile from source at pip-install time (maturin sdist) — rejected

Requires a Rust toolchain and every GUI/audio `-dev` library on the
user's machine at install; catastrophic UX and unusable in CI.

## Decisions

1. **Package name `ryll`** (confirmed available on PyPI, 2026-07-20),
   console-script `ryll`. Distributed via the `shakenfist-client[vdi]`
   extra in phase 4.
2. **maturin `bindings = "bin"`** from `ryll/pyproject.toml`, with an
   explicit `[[bin]] name = "ryll"` in `ryll/Cargo.toml`. Version is
   `dynamic = ["version"]`, inherited from the crate's
   `version.workspace = true` (maturin resolves the workspace
   inheritance) — **no stamping step**, and `check-version` already
   enforces tag == version.
3. **Build natively inside `quay.io/pypa/manylinux_2_28_<arch>`**
   (AlmaLinux 8, glibc 2.28), not via `--zig`. The spike (2026-07-20)
   proved the recipe end to end; it lives in
   `tools/build-ryll-wheel.sh` (host wrapper, per arch) +
   `tools/build-ryll-wheel-in-container.sh` (in-container steps):
   enable EPEL + PowerTools, `dnf install` the C/C++ toolchain and the
   GUI/audio `-devel` libs, install rustup stable + `maturin>=1.7,<2`,
   then `maturin build --release --compatibility manylinux_2_28
   --skip-auditwheel --out target/wheels`.
4. **`--skip-auditwheel` is correct, and the manylinux_2_28 tag is
   honest.** `readelf -d` on the built binary shows the only
   non-standard `DT_NEEDED` is `libasound.so.2`, and the max glibc
   symbol version is 2.28. The GUI/audio libraries (libGL/EGL, wayland,
   xcb, xkbcommon, X11, opus) are **dlopen'd at runtime**, not linked,
   so there is nothing for auditwheel to vendor and the tag does not
   overpromise. Proven: on a clean `ubuntu:22.04` (glibc 2.35), `pip
   install ryll-0.1.5-py3-none-manylinux_2_28_x86_64.whl` then `ryll
   --version` prints `ryll 0.1.5` and exits 0.
5. **Runtime system libraries.** ryll is a Linux GUI app, so the binary
   needs `libxcb1`, `libwayland-client0`, `libxkbcommon0`, `libasound2`,
   `libopus0`, `libgl1` present to run its GUI — pip cannot install
   these under any packaging model. Documented in the README; phase 4's
   `remote-viewer` fallback covers hosts that lack them.
6. **Per-arch, Linux only.** Wheels for `x86_64` and `aarch64`, built on
   native runners of each arch (x86_64 on the self-hosted docker VM,
   aarch64 on a github-hosted arm runner — no QEMU-emulated compile).
   Other platforms (macOS, Windows, musl, glibc < 2.28) get no wheel and
   use a system package / release artifact / build-from-source.
7. **Publish + release gating (parity with kerbside).** Publish via PyPI
   Trusted Publishers (OIDC, no tokens), staged TestPyPI → PyPI. All
   publishing (`publish-crates`, `github-release`, and the `ryll` PyPI
   jobs) is gated on a `sign-tag` job that keyless-Sigstore-signs the
   release tag behind a manual-approval `release` GitHub Environment, so
   nothing publishes before the approved, signed tag exists. Required
   one-time setup (cannot be done from code): (a) a `release`
   environment in the ryll repo settings with required reviewers, or the
   gate is a no-op; (b) a pending `ryll` Trusted Publisher on **both**
   pypi.org and test.pypi.org (Owner `shakenfist`, Repo `ryll`, Workflow
   `release.yml`, environment `release`).

## Execution

Implemented in the `ryll-wt-vdi-tokens` worktree (branch
`vdi-console-tokens`).

| Step | Status | What |
|------|--------|------|
| Embed recipe spike | Done 2026-07-20 | Proved the native manylinux_2_28 maturin `bindings=bin` build for the GUI binary; wheel installs and runs on a clean host (Decision 4). |
| Packaging | Done | `ryll/pyproject.toml`, `[[bin]]` in `ryll/Cargo.toml`, `tools/build-ryll-wheel.sh` + `tools/build-ryll-wheel-in-container.sh`. |
| CI/release | Done | `build-ryll-wheels` per-arch matrix; publish-testpypi → publish-pypi of the wheels, gated on `sign-tag` + `release` environment; fetch-only tarball/checksums/python-test jobs removed. |
| Docs | Done | README "Installing via pip" + `docs/installation.md` rewritten for the embed model. |

## Success criteria

* `pip install ryll` on Linux x86_64/aarch64 (glibc ≥ 2.28) installs a
  wheel that already contains the binary; `ryll --version` works with no
  network and no compile step.
* The wheel's `manylinux_2_28` tag is honest (glibc floor 2.28; only
  `libasound.so.2` beyond the standard set in `DT_NEEDED`).
* A missing runtime GUI library is the user's to install (documented);
  phase 4 falls back to `remote-viewer`.
* CI publishes the `ryll` wheels after the approved, Sigstore-signed
  tag, staged through TestPyPI, via Trusted Publishers.
* Phase 4 can rely on `ryll` being on PATH after the `[vdi]` extra, and
  on graceful absence otherwise.

## Agent guidance

- The binary is a GUI app: a headless CI runner can build and
  `--version`-smoke it (that only loads `DT_NEEDED`), but cannot open a
  window. Don't assert on GUI behaviour in CI.
- The aarch64 wheel must build on a native arm runner; do not
  QEMU-emulate the compile (far too slow).
- `--skip-auditwheel` is intentional (Decision 4); do not "fix" it by
  adding an auditwheel-repair step — it would reject the dlopen'd GUI
  libs.

## Administration and logistics

- Cross-repo sequencing: phase 4 (client) depends on this phase but
  degrades gracefully via `remote-viewer` if it slips. Nothing here
  depends on the Shaken Fist phases 1-2 already landed.
- The shakenfist master plan's phase-3 row is set to *In progress* while
  this lands.
