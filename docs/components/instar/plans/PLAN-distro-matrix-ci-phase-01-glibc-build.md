# Phase 1: Build/dev container split + lower glibc floor

Master plan: [PLAN-distro-matrix-ci.md](/components/instar/plans/PLAN-distro-matrix-ci/).
Planning effort: **high**. Isolation: **worktree** (a wrong change
here breaks every build and every release).

## Objective

Lower the released `instar` binary's glibc floor to run on all seven
matrix distros (down to Rocky 9 / Ubuntu 22.04) by building it on
**`debian:bullseye`** (glibc 2.31), without dragging the test/fuzz
tooling onto that old base. Achieve this by splitting the current
all-in-one devcontainer into:

- a minimal **build image** (`instar-build`, bullseye, toolchain only)
  that produces the release binary and the `.deb`/`.rpm`, and
- the existing fat **dev/test image** (renamed `instar-dev`) that keeps
  `qemu-utils`, the libyal parsers, `cargo-fuzz`, `cargo-audit`, `gh`
  for the test and fuzz suites and the VS Code devcontainer.

Also close issue #474 (manual real-VM validation of the *published*
v0.3.0 artifacts) as the human baseline the automation reproduces.

## Why bullseye (recap of D1)

glibc is forward-compatible only: a binary runs on hosts whose glibc is
≥ the build glibc. The matrix floor is Rocky 9 at **2.34**; bullseye's
**2.31** clears it with ~3 minor-versions of margin and clears every
other distro (Ubuntu 22.04 = 2.35, Debian 12 = 2.36, …). bullseye is
Debian oldstable (supported), keeps the toolchain apt-based, and — the
key point — the build never leaves Debian. Non-Debian coverage comes
only from installing and running the produced package inside the real
target-distro containers (phases 3–4), never from building there.

## Verified starting state (grounding facts)

- **One image today.** `INSTAR_IMAGE := instar-build` (`Makefile:108`)
  is built from `src/.devcontainer/Dockerfile` (base
  `mcr.microsoft.com/devcontainers/base:debian`, floating → glibc
  2.41) and used by **every** container target: `instar`, `deb`,
  `rpm`, `metadata`, `audit`, `build-prototype`, and the test targets.
- **The build path.** `make instar` (`Makefile:121-134`) runs `bash
  build.sh` in `/workspace/src` as the host uid, `HOME=/build`,
  `CARGO_HOME=/build/.cargo`, with `.cargo-cache/{registry,git}`
  bind-mounted. `build.sh` runs `cargo build --release` per crate and
  `rust-objcopy` (from `cargo-binutils`) to flatten each guest ELF.
- **The `+nightly` trap (memory: instar_devcontainer_nightly_ice).**
  `build.sh` deliberately calls `cargo` with **no** `+nightly`
  override and documents why: the container's *default* toolchain must
  BE the pinned nightly (`ARG RUST_NIGHTLY=nightly-2026-07-22` +
  `rust-src` + `llvm-tools-preview`). A literal `+nightly` would
  auto-install the floating nightly without `rust-src`. **The build
  image must set the same pinned nightly as its default toolchain.**
- **Guest cross-builds are glibc-independent.** The guest ops target
  `x86_64-unknown-none` (freestanding, build-std, needs `rust-src`);
  their glibc floor is irrelevant. Only the host `instar` VMM binary
  links glibc, so only *its* build image sets the floor.
- **Packaging is compile-free.** `make deb` = `cargo deb --no-build -p
  instar` (`Makefile:199`); `make rpm` = `cargo generate-rpm -p vmm`
  (`Makefile:224`). They only package the artifacts `make instar`
  produced, so the build image (not the packaging step) fixes the
  glibc floor — but the packaging tools (`cargo-deb`,
  `cargo-generate-rpm`) must live on the build image.
- **CI artifact path.** `release.yml` does `docker image rm -f
  instar-build` → `make instar` → `make package` (lines 119/122/143).
  `functional-tests.yml` `package-smoke` (line 256) does the same rm +
  `make instar && make deb`. Keeping the **build** image named
  `instar-build` leaves both correct.
- **proto toolchain.** `crates/guest-protocol/build.rs` drives
  `micropb-gen` 0.6, which shells to system `protoc`
  (`protobuf-compiler`). The proto is `proto3` using `oneof` and
  message-typed fields — **no `optional` keyword** — so bullseye's
  protoc 3.12 is *expected* to suffice, but this must be **verified at
  build**, not assumed (see Risk R1).

## Design: the two-image split

| Concern | Image | Dockerfile | Make targets |
|---------|-------|------------|--------------|
| Produce release binary + packages | `instar-build` (bullseye) | `src/.devcontainer/build/Dockerfile` (new) | `instar`, `deb`, `rpm`, `package` |
| Tests, fuzz, audit, manifests, prototypes, VS Code | `instar-dev` (Debian, pinned) | `src/.devcontainer/Dockerfile` (existing, base pinned) | `test`, `audit`, `metadata`, `build-prototype`, fuzz targets |

Makefile variables become `INSTAR_BUILD_IMAGE := instar-build` and
`INSTAR_DEV_IMAGE := instar-dev`; the two devcontainer-build targets
are `build-devcontainer` (build image) and the existing
`instar-devcontainer` repurposed to the dev image (keep its name so
prototype/test targets need no rename beyond the variable). The
`docker run` invocation body (uid, HOME, CARGO_HOME, mounts, workdir)
is identical for both — only the image tag differs, so factor it if it
reduces duplication, but a faithful copy is acceptable.

The **build image** installs only: `protobuf-compiler` (or a pinned
protoc, per R1), `curl`/`ca-certificates`/`git`, rustup with the
**same** `${RUST_NIGHTLY}` pin as default toolchain + `rust-src` +
`llvm-tools-preview`, and `cargo-binutils`, `cargo-deb`,
`cargo-generate-rpm`. It does **not** install `qemu-utils`, the libyal
`*-utils` parsers, `cargo-fuzz`, `cargo-audit`, or `gh` — those stay on
`instar-dev`. Replicate the existing world-writable `/build`
CARGO_HOME/RUSTUP_HOME `umask 0000` pattern exactly (memory:
rust_devcontainer_permissions_investigation,
instar_worktree_target_ownership — any docker run writing into the
bind-mounted tree runs as host uid with a writable CARGO_HOME).

The VS Code `devcontainer.json` (if present under `src/.devcontainer/`)
stays pointed at the **dev** Dockerfile — humans get the full toolchain,
not the stripped build image.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | low | (operator) | none | **Done 2026-08-11 — see "Step 1a outcome" below. Close #474 (manual, Michael-run).** Produce a copy-paste validation script + expected-output checklist: on a clean KVM-capable Debian/Ubuntu VM, `apt install ./instar_0.3.0-1_amd64.deb`, then `instar info` + `instar create` + `instar map` on a sample qcow2; on Fedora-latest or Rocky 10, the same with the published `.rpm`. The sub-agent writes the script + checklist only; Michael runs it on real VMs, posts results to #474, and closes it. This is the human baseline the matrix must reproduce; it is independent of the bullseye work (the shipped v0.3.0 artifacts are glibc-2.41 trixie builds). |
| 1b | high | opus | worktree | **Create `src/.devcontainer/build/Dockerfile`** — the bullseye build image. `FROM debian:bullseye`. Install `protobuf-compiler`, `curl ca-certificates git`, then rustup with `--default-toolchain ${RUST_NIGHTLY}` (`ARG RUST_NIGHTLY=nightly-2026-07-22`, matching the dev image byte-for-byte), `rustup component add rust-src llvm-tools-preview`, and `cargo install cargo-binutils cargo-deb cargo-generate-rpm`. Reproduce the `/build` world-writable CARGO_HOME/RUSTUP_HOME/`umask 0000`/PATH pattern from the current Dockerfile. End with a verify line: `rustc --version && rust-objcopy --version && cargo deb --version && cargo generate-rpm --version && protoc --version`. Do **not** add qemu/libyal/fuzz/audit/gh. Build the image and confirm it builds clean. |
| 1c | high | opus | worktree | **Split the Makefile.** Introduce `INSTAR_BUILD_IMAGE := instar-build` and `INSTAR_DEV_IMAGE := instar-dev`; add a `build-devcontainer` target that `docker build`s `src/.devcontainer/build/`; point `instar`, `deb`, `rpm` (and thus `package`) at the build image + `build-devcontainer`; repoint `metadata`, `audit`, `build-prototype`, and the test/fuzz targets at `instar-dev` via the existing `instar-devcontainer` target (now building the dev image). Keep every `docker run` body (uid/HOME/CARGO_HOME/mounts/workdir) unchanged. Verify end-to-end: `make instar && make deb && make rpm` produce `src/target/release/instar`, `.deb`, `.rpm`; `make check-binary-sizes` passes; `make metadata` still works on the dev image. |
| 1d | high | opus | worktree | **Write `tools/verify-glibc-floor.sh <deb> <rpm>`** — the empirical floor gate. For each matrix distro image (`debian:12`, `debian:13`, `ubuntu:22.04`, `ubuntu:24.04`, `fedora:latest`, `rockylinux:9`, `rockylinux/rockylinux:10` — Rocky 10 is
not in the Docker Official `rockylinux` library, which stops at 9):
`docker run` the image, install the appropriate package (apt vs dnf), and run `instar info` + `instar create` + `instar map` on a fixture (needs `--device /dev/kvm`; skip-with-loud-warning if KVM absent locally, but CI must run it with KVM). Assert clean exit on every distro. If the **bullseye-built** binary fails on any distro, STOP and report to management — do NOT silently switch to the rockylinux:9 contingency without review. `shellcheck` clean. |
| 1e | medium | sonnet | worktree | **Pin the dev image base.** Change `src/.devcontainer/Dockerfile`'s `FROM mcr.microsoft.com/devcontainers/base:debian` to a pinned Debian tag (a specific digest or `:bookworm`/`:trixie`-dated tag — confirm the exact pin with Michael) so dev-image rebuilds are reproducible, matching the nightly-pin rationale already documented in that file. Confirm `devcontainer.json` (if any) still references this Dockerfile and that `make test` still builds/runs on the repinned dev image. |
| 1f | medium | sonnet | none | **Audit every workflow + script reference to the image names and make targets.** `release.yml` (`docker image rm -f instar-build` + `make instar`/`make package` → build image, should stay correct — confirm), `functional-tests.yml` `package-smoke` (build image — confirm) and `build-and-test` / test jobs (must target `instar-dev` now — fix any `instar-build` references that meant the dev image), and **`rust-nightly-bump.yml`** (must test-build **both** images against a candidate nightly so a nightly that breaks either image blocks the bump — today it builds one). Grep the whole tree for `instar-build` and `instar-devcontainer` and reconcile each hit. |
| 1g | low | sonnet | none | **Docs.** CHANGELOG (`[Unreleased]`): lowered glibc floor → Debian/Ubuntu/Fedora/Rocky coverage incl. Rocky 9 & Ubuntu 22.04, via the build/dev container split. `docs/development.md`: the two-image model, which make targets use which image, the bullseye rationale, and the R1 protoc note. `ARCHITECTURE.md` / `AGENTS.md`: brief pointer to the split (not duplicating docs/). README install section: state the new minimum glibc (2.31) if it names one. |

## Execution results

**The image names shipped the other way round from this plan.** The
design section below says the bullseye build image keeps the name
`instar-build` and the dev/test image is renamed `instar-dev`. What
landed is the reverse:

| Role | Plan said | Shipped as |
|------|-----------|------------|
| Minimal bullseye release build | `instar-build` | **`instar-release`** |
| Fat dev/test image (qemu, libyal, fuzzers) | `instar-dev` | **`instar-build`** |

`INSTAR_BUILD_IMAGE := instar-release` and `INSTAR_DEV_IMAGE :=
instar-build` (`Makefile:116-117`), and AGENTS.md, `docs/development.md`
and the Makefile comments all describe the shipped scheme. The reason
is in the Makefile: many existing CI steps run `docker run ...
instar-build ...` to execute tests, and every one of them means the
*dev* image. Keeping `instar-build` pointing at the dev image left all
of those correct without edits, and gave the genuinely new thing a new
name.

Read the rest of this file with that substitution in mind. Following it
literally means running `docker run instar-build` expecting the minimal
bullseye image and silently getting the fat dev image, which has a
different glibc and different tooling.

## Step 1a outcome (2026-08-11)

`tools/validate-published-release.sh` ran on two real KVM VMs against
the **published** v0.3.0 assets. Both ended in `PASS`, with every
checklist item present: a clean install with no unmet dependencies,
`/usr/bin/instar` plus `/usr/lib/instar/*.bin`, `--help`, `info`
reporting `file format: qcow2`, `create` printing `Created:`, and
`map`'s table header.

| VM | Package | Distro qemu-img | Result |
|----|---------|-----------------|--------|
| Debian 13 (trixie) | `.deb` | 10.0.11 | PASS |
| Rocky Linux 10.1 | `.rpm` | 10.1.0 | PASS |

Transcripts are recorded on #474.

**Why trixie and Rocky 10 rather than the matrix floor.** The
published v0.3.0 `.deb` declares `Depends: libc6 (>= 2.39)` and its
highest referenced symbol version is `GLIBC_2.39` — so it *cannot*
install on Debian 12 (2.36), Ubuntu 22.04 (2.35) or Rocky 9 (2.34).
That is the historical record this step exists to capture, not a
regression: those three distros are precisely what the bullseye
rebuild in 1b-1c exists to reach, and the rebuilt binary's floor is
`GLIBC_2.30`. Note also that the master plan's phrasing "the shipped
v0.3.0 artifacts are glibc-2.41 trixie builds" describes the *build
image's* glibc, not the binary's requirement — the artifact's actual
floor is 2.39, which is what constrains where it can be validated.

## Acceptance

- `make instar && make deb && make rpm` build working artifacts from
  the bullseye `instar-build` image; `make check-binary-sizes` passes.
- `tools/verify-glibc-floor.sh` passes on **all seven** matrix distros
  with the bullseye-built packages (the real floor gate).
- `make test` (and fuzz/audit) still pass on `instar-dev`.
- `release.yml` and `functional-tests.yml package-smoke` still green
  (they build the same artifacts via the same make targets).
- `rust-nightly-bump.yml` now test-builds both images.
- #474 closed with recorded real-VM results. **Done 2026-08-11**:
  PASS on Debian 13 (.deb) and Rocky Linux 10.1 (.rpm).
- `pre-commit run --all-files` clean; `shellcheck` clean.
- One commit per logical change: (1b) build Dockerfile, (1c) Makefile
  split, (1d) verify script, (1e) dev-base pin, (1f) workflow audit,
  (1g) docs. 1a lands via #474, not a code commit.

## Risks

- **R1 — bullseye protoc version.** protoc 3.12 is *expected* to
  compile the proto (proto3, `oneof`, no `optional` keyword), but if
  the `guest-protocol` build fails on protoc in 1b, install a pinned
  upstream `protoc` release binary in the build Dockerfile instead of
  the apt `protobuf-compiler` (download + checksum-verify a fixed
  version), and record it. Do not silently accept a version skew from
  the dev image's protoc — note any difference.
- **R2 — the floor is empirical, not nominal.** 2.31 ≤ 2.34 is
  necessary but not sufficient; a distro could fail for a non-glibc
  reason (missing runtime dep, SELinux, package scriptlet). 1d is the
  gate; a failure there triggers management review, not an automatic
  contingency switch.
- **R3 — nightly on bullseye.** rustup is distro-agnostic and the pin
  protects against a broken nightly, but if a toolchain component fails
  to install on bullseye, that is a 1b blocker to surface immediately
  (the awkward-on-old-base tooling — fuzz/audit — is deliberately NOT
  in this image, which minimises this surface).
- **R4 — CARGO_HOME/target ownership.** The build image runs as host
  uid writing into the bind-mounted tree; get the `/build` writable
  CARGO_HOME + `umask 0000` exactly right or rebuilds poison ownership
  (memory notes). Verify a second `make instar` (cache warm) works.

## Out of scope for this phase

- The runner script, matrix job, and merge queue (phases 3–5).
- Any change to what the binary *does* — this phase changes only where
  it is built and proves where it runs.
- The rockylinux:9 contingency build — only revisited if 1d fails.
