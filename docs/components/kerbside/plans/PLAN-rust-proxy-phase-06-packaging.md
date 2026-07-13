# Rust proxy phase 6: packaging (maturin wheel + lockstep release)

Part of the [Rust SPICE proxy master plan](/components/kerbside/plans/PLAN-rust-proxy/). Phase 5
made the Python daemon supervise the Rust proxy, resolving the binary
via `kerbside/proxy_supervisor.py::find_proxy_bin()`
(`KERBSIDE_PROXY_BIN` env → `shutil.which('kerbside-proxy')` → the dev
build tree). Phase 6 makes `pip install kerbside` actually *put*
`kerbside-proxy` on `PATH`, so that `shutil.which` leg resolves in a
real deployment: the Rust crate is published as a separate
`kerbside-proxy` PyPI package built with maturin (`bindings = "bin"`),
and `kerbside` gains an exact-version pin on it, released in lockstep
from a single `v*` tag.

## Prompt

Ground every decision in the code and the existing release machinery.
Read `pyproject.toml` (the `kerbside` package: setuptools +
`setuptools_scm`, the `[project.scripts]` entry points, the pinned
dependency list and the `# END_OF_INDIRECT_DEPS` sentinel used by the
pin-indirect-dependencies workflow), `rust/kerbside-proxy/Cargo.toml`
(`[package] version`, the `[[bin]]` target, the ryll git dependency
pinned by rev), `rust/kerbside-proxy/build.rs` (it compiles
`../../kerbside/rpc/kerbside.proto` — a path *outside* the crate),
`rust/kerbside-proxy/{Makefile,Dockerfile}` (the Docker build wrapper and
why it mounts the whole repo), `kerbside/proxy_supervisor.py`
(`find_proxy_bin()` — the resolution order phase 6 must satisfy),
`.github/workflows/{release.yml,rust.yml,pin-indirect-dependencies.yml}`,
and `RELEASE-SETUP.md` (PyPI trusted publishers + the `release`
environment). Verify how maturin `bindings = "bin"` lays the binary into
a wheel's scripts directory and how that lands on `PATH` after `pip
install`. Flag uncertainty explicitly rather than guessing.

Planning effort: **medium** — packaging is a well-established pattern,
but two wrinkles push individual steps to high effort: cross-compiling
the ring/rustls/ryll dependency chain to `aarch64`, and keeping two PyPI
packages version-locked from one `setuptools_scm` tag.

## Repository and branch logistics

All work is in **kerbside** (the Rust crate, the new maturin project
files, the `kerbside` `pyproject.toml`, the CI workflows, and `tools/`
scripts); no other repo is involved. The ryll dependency is already a
merged, rev-pinned git dependency and does not change here. Phase 6
builds on phase 5, which is unmerged, so branch `rust-proxy-phase-6`
from the phase-5 tip (done); rebase onto `develop` once phases 2–5 land.
This plan file lives on the phase-6 branch.

## Situation (verified 2026-07-08)

- **`kerbside` is a setuptools + `setuptools_scm` package.**
  `pyproject.toml` declares `dynamic = ["version"]`, derives the version
  from `git describe --tags --match v*` (`[tool.setuptools_scm]`,
  `write_to = "kerbside/_version.py"`), ships two console entry points
  (`kerbside`, `kerbside-util`), and carries an exhaustively pinned
  dependency list terminated by a `# END_OF_INDIRECT_DEPS` sentinel that
  the `pin-indirect-dependencies` workflow `sed`-appends to. There is **no
  dependency on `kerbside-proxy` today** — that pin is net-new here.
- **The Rust crate is a plain binary crate, unversioned against git.**
  `rust/kerbside-proxy/Cargo.toml` hard-codes `[package] version =
  "0.1.0"`, defines a single `[[bin]] name = "kerbside-proxy"`, and pins
  `shakenfist-spice-protocol` to a **git rev** on the ryll repo (not a
  crates.io release — this matters for source builds). There is **no
  `pyproject.toml` in the crate** and no maturin machinery anywhere.
- **`build.rs` reaches outside the crate.** It compiles
  `../../kerbside/rpc/kerbside.proto` (resolved relative to the crate
  dir), asserting the file exists and pointing at the Makefile, which
  mounts the **repo root** at `/repo` and sets the workdir to
  `/repo/rust/kerbside-proxy` precisely so that `../../kerbside/rpc`
  resolves. `protoc` is vendored (`protoc-bin-vendored`), so no system
  protobuf is needed; `protoc` runs on the *build host*, not the target.
- **`find_proxy_bin()` (phase 5) already expects an installed binary.**
  `kerbside/proxy_supervisor.py` resolves in order: `KERBSIDE_PROXY_BIN`
  env (authoritative — raises if set-but-invalid) → `shutil.which(
  'kerbside-proxy')` → the in-repo dev target dirs. Phase 6's entire job
  on the Python side is to make the **`shutil.which` leg** succeed after
  `pip install kerbside`, i.e. land the binary on `PATH`. No change to
  `find_proxy_bin()` itself is required.
- **Release today publishes only `kerbside`.** `.github/workflows/
  release.yml` triggers on `v*` tags, runs on `[self-hosted, static]` /
  `[self-hosted, debian-12, static]`, `python -m build`s the sdist+wheel,
  `twine check`s it, signs the tag with Sigstore/gitsign, publishes to
  PyPI via a **trusted publisher** (OIDC, no tokens) gated on the
  `release` environment (required reviewer), and cuts a GitHub Release.
  `RELEASE-SETUP.md` documents the single `kerbside` trusted publisher.
- **Rust CI exists but builds no wheel.** `.github/workflows/rust.yml`
  runs on `[self-hosted, vm, debian-12]` (x86_64), installs the toolchain
  via `dtolnay/rust-toolchain@stable`, and runs fmt/clippy/test/`cargo
  build --release` from `rust/kerbside-proxy`. It never invokes maturin
  and never produces a distributable artifact.
- **Runners are x86_64.** Both `rust.yml` and the release jobs run on
  x86_64 self-hosted runners; there is **no aarch64 runner** in evidence.
  aarch64 wheels therefore require cross-compilation or emulation.
- **CLAUDE.md constraint:** CI step bodies must stay ≤5 lines; anything
  larger goes into a `tools/` script the workflow calls. The
  pin-indirect-dependencies workflow's inline `sed`/loop is the existing
  (grandfathered) exception; new logic here follows the rule.

## Mission

1. **`kerbside-proxy` is a maturin `bindings = "bin"` project** whose
   wheel contains the compiled `kerbside-proxy` binary, laid into the
   wheel's `*.data/scripts/` directory so `pip install` puts it on
   `PATH` and `find_proxy_bin()`'s `shutil.which` leg resolves it.
2. **`kerbside` exact-pins `kerbside-proxy`** at the same version, so
   `pip install kerbside` transitively installs a matching proxy and the
   gRPC contract matches by construction.
3. **Both packages release in lockstep from one `v*` tag**: the CI wheel
   build produces manylinux **x86_64 and aarch64** wheels, and the
   release workflow publishes both PyPI projects under the existing
   `release`-environment approval gate.
4. **Packaging breakage is caught on PRs, not at release**: `rust.yml`
   gains a cheap `maturin build` smoke step on the native runner.

Out of scope (later phases / future work): an sdist for `kerbside-proxy`
(operator chose **wheels-only** — see decision 1; the source-build path,
which would need a Rust toolchain and network access to the ryll git dep
plus making the crate self-contained for the proto, is future work);
musllinux wheels; making Rust the default proxy and removing the Python
proxy (phase 8); the direct-qemu CI lane on the Rust proxy (phase 7).

## Design decisions

Decisions 1 (wheel matrix) and 2 (sdist policy) are settled with the
operator. The rest are the planner's calls, open to revision at back
brief.

**1. Wheel matrix: manylinux x86_64 + aarch64 (settled).** The operator
confirmed OpenStack Kolla / Kolla-Ansible CI runs on both x86_64 and
arm64, and that manylinux **aarch64** wheels are sufficient for the arm64
CI workflows (aarch64 == arm64; a `manylinux_*_aarch64` wheel installs on
Linux arm64). So the matrix is two targets. Since the runners are x86_64
only, the aarch64 wheel is **cross-compiled**:
   - **Primary approach: maturin + zig.** `maturin build --release
     --target aarch64-unknown-linux-gnu --zig --compatibility
     manylinux_2_28` (with `rustup target add aarch64-unknown-linux-gnu`
     and `pip install ziglang`). zig supplies the cross linker and a
     pinned-glibc sysroot so the `manylinux_2_28` tag is honest, and it
     builds on the x86_64 host with no emulation. The build-host `protoc`
     is x86_64 (vendored) and runs on the host, so cross-compilation does
     not touch it. The risk is the `ring` crypto provider (via
     `rustls` feature `ring`) and the ryll git dependency cross-compiling
     cleanly; `ring` supports aarch64 cross with a clang/zig toolchain, so
     this is expected to work but must be proven (step 6c is high-effort
     for exactly this reason).
   - **Documented fallback: QEMU-emulated manylinux container.** If the
     zig cross of the ring/ryll chain fights back, build the aarch64
     wheel inside `quay.io/pypa/manylinux_2_28_aarch64` under binfmt/QEMU
     emulation (slower, but the canonical manylinux path). Recorded in
     the tools script and the plan so the sub-agent can pivot without
     re-planning.

**2. sdist policy: wheels only, no sdist (settled).** Publishing a
source distribution would let pip attempt a source build on an
unsupported platform — which needs a Rust toolchain **and** network
access to fetch the rev-pinned ryll git dependency at install time
(fragile, slow, confusing offline), and would require making the crate
self-contained for the proto (`build.rs` currently reaches out to
`../../kerbside/rpc`). Wheels-only means pip fails cleanly with "no
matching distribution" on an unsupported platform instead. This also
means the **crate-self-containment / proto-relocation problem does not
block phase 6** — the CI wheel build runs from a full repo checkout
(like `rust.yml` today), so `build.rs`'s `../../kerbside/rpc` path
resolves and `build.rs` needs no change. (Making the crate
self-contained is recorded as the prerequisite for a future sdist.)

**3. maturin `bindings = "bin"`, version driven from Cargo.toml.** Add
`rust/kerbside-proxy/pyproject.toml` with `[build-system] requires =
["maturin>=1.7,<2"]`, `build-backend = "maturin"`, `[project] name =
"kerbside-proxy"`, `requires-python = ">=3.9"` (match `kerbside`),
`dynamic = ["version"]`, and `[tool.maturin] bindings = "bin"`. With
`dynamic = ["version"]` maturin reads the version from Cargo.toml
`[package] version`, giving a **single mutable source of truth** (the
Cargo version) that the lockstep step stamps. `bindings = "bin"` packages
the `[[bin]]` target into the wheel's scripts dir; the resulting wheel is
platform-tagged (it carries a compiled binary), which is why the matrix
exists.

**4. Version lockstep: the `v*` tag is the single source of truth;
release stamps it into both packages.** `kerbside`'s version comes from
`setuptools_scm` (the tag). For the two packages to share a version and
for `kerbside` to exact-pin `kerbside-proxy`, a release-time
`tools/stamp-proxy-version.sh` derives the release version (from the tag
/ the `setuptools_scm` result) and writes it into (a) the crate's
`Cargo.toml` `[package] version` (which maturin then reports), and (b)
the `kerbside-proxy==X.Y.Z` pin in `kerbside`'s `pyproject.toml`, before
either package is built. Both build jobs check out the **same tag**, so
`setuptools_scm` and the stamped Cargo version agree by construction.
   - **Committed-pin policy (settled during 6b):** the source tree carries
     **no** `kerbside-proxy` pin — only a `# KERBSIDE_PROXY_PIN` marker
     comment in the dependency list — and the release *inserts* the exact
     pin before the marker (mirroring the `# END_OF_INDIRECT_DEPS`
     insertion idiom the pin-indirect-dependencies workflow uses). A
     committed `==` pin was tried first and rejected: it makes `tox -epy3`
     (and any `pip install .`) fail because it tries to install a
     `kerbside-proxy` release that does not exist on PyPI yet. Omitting it
     is also semantically right — a dev checkout resolves the proxy from
     the build tree (or `KERBSIDE_PROXY_BIN`) via `find_proxy_bin()`, not
     from this pin, so nothing in dev/CI needs the sibling package. The
     stamp script is idempotent: if a pin is already present it replaces
     the version, otherwise it inserts one. Considered and deferred: an
     in-tree PEP 517 build-backend shim injecting
     `kerbside-proxy==<setuptools_scm version>` dynamically (always-exact
     in-tree, but more machinery than the repo currently carries).
   - **Publish order:** publish `kerbside-proxy` before/with `kerbside`
     so the exact pin resolves for anyone installing at release time.
     (pip resolves at install, not publish, and both ship in one release,
     but proxy-first is the safe ordering.)

**5. CI shape: extend the existing workflows, don't fork the release.**
The single `v*` trigger, the Sigstore tag signing, and the `release`
environment approval gate stay in `release.yml`; phase 6 adds to it a
**matrix wheel-build job** (`target: [x86_64, aarch64]`) that calls
`tools/build-proxy-wheel.sh <target>`, a **stamp** step calling
`tools/stamp-proxy-version.sh`, and a **`publish-proxy-pypi`** job that
publishes the proxy wheels to the separate `kerbside-proxy` PyPI project
via its own trusted publisher. `rust.yml` gains a native-only `maturin
build` smoke step so packaging regressions surface on every `rust/**` PR.
All non-trivial logic lives in `tools/` scripts per the CLAUDE.md CI
rule.

## Open questions (to settle during the phase)

- ~~**Committed-pin policy**~~ (decision 4): **settled during 6b** — no
  committed pin (a `# KERBSIDE_PROXY_PIN` marker only), inserted at
  release. A committed `==` pin was tried and rejected because it breaks
  `tox -epy3` (the sibling is not on PyPI yet).
- **manylinux floor**: `manylinux_2_28` (glibc 2.28, EL8/Debian 10+;
  recommended, modern and matches the debian-12 build host) vs the older
  `manylinux2014` (glibc 2.17, widest reach). Kolla base images decide
  what is actually required — confirm against the Kolla-Ansible arm64 job
  image.
- **Whether the x86_64 wheel builds natively or also inside a manylinux
  container** for tag honesty. Native `--compatibility manylinux_2_28`
  audits the glibc floor; a container guarantees it. Lean: native with
  `--compatibility` for x86_64, revisit if `auditwheel`/maturin flags a
  symbol above the floor.
- **maturin invocation in Docker vs on the runner.** `rust.yml` installs
  the toolchain directly on the self-hosted runner today; the operator's
  "Rust builds in Docker" preference is about the dev *host*, not CI
  runners. Lean: match `rust.yml` (toolchain on the runner) for CI, keep
  a Docker path in the `tools/` script for local reproduction.
- **Second trusted publisher / PyPI project creation** for
  `kerbside-proxy` (a `RELEASE-SETUP.md` addition + a one-time operator
  action, like the pending-publisher note for `kerbside`).

## Execution

One commit per logical change. Every Rust/maturin step is proven to
build a wheel (in Docker locally, matching the operator's preference, and
mirrored by the CI smoke step); every Python step passes
`tox -eflake8`/`-epy3`; `pre-commit run --all-files` before each commit.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high | opus | worktree | **maturin `bindings = "bin"` project.** Add `rust/kerbside-proxy/pyproject.toml`: `[build-system] requires = ["maturin>=1.7,<2"]`, `build-backend = "maturin"`; `[project]` with `name = "kerbside-proxy"`, `description` (reuse the Cargo.toml one), `requires-python = ">=3.9"`, `dynamic = ["version"]`, `readme`, `license`, `[project.urls]` (Homepage/Bug Tracker matching `kerbside`), and useful classifiers; `[tool.maturin] bindings = "bin"`. Do NOT hand-set `[project] version` (maturin reads it from `Cargo.toml [package] version` under `dynamic`). Build a wheel in a venv/Docker (`pip install maturin`; `maturin build --release` from the crate dir, repo root available so `build.rs`'s `../../kerbside/rpc` resolves). **Verify**: the wheel is platform-tagged and contains `kerbside-proxy` under `*.data/scripts/`; `pip install`ing it into a fresh venv puts `kerbside-proxy` on `PATH` such that `shutil.which('kerbside-proxy')` (the phase-5 `find_proxy_bin` leg) returns it. Add wheel/target artifacts to `.gitignore`. Do not change `build.rs`, `Cargo.toml` deps, or `find_proxy_bin()`. |
| 6b | high | opus | none | **Version lockstep + the `kerbside` pin.** Write `tools/stamp-proxy-version.sh` (>5 lines, so a real script): given a version (arg or derived via `python -m setuptools_scm` / `git describe --tags --match 'v*'`, normalized to PEP 440), rewrite `rust/kerbside-proxy/Cargo.toml` `[package] version = "X.Y.Z"` and the `kerbside-proxy==X.Y.Z` pin line in `kerbside/pyproject.toml`, idempotently, with clear failure if either target is missing. Add the `kerbside-proxy==<current dev version>` pin to `kerbside/pyproject.toml`'s `dependencies` (a clearly commented block, NOT inside the `# END_OF_INDIRECT_DEPS` indirect region the pin-workflow manages). Document the single-source-of-truth (the `v*` tag) and the committed-pin policy in a script header comment. Prove: running the script with a sample version updates both files and `cargo metadata`/`maturin` report it; `tox -eflake8`/`-epy3` still pass (the pin must be a syntactically valid dependency even if `kerbside-proxy` is not yet on PyPI — installation resolution is a release-time concern). |
| 6c | high | opus | worktree | **aarch64 cross-build (the tricky bit).** Write `tools/build-proxy-wheel.sh <x86_64\|aarch64>` that builds a manylinux wheel for the given target. x86_64: native `maturin build --release --compatibility manylinux_2_28`. aarch64: `rustup target add aarch64-unknown-linux-gnu`, `pip install ziglang`, `maturin build --release --target aarch64-unknown-linux-gnu --zig --compatibility manylinux_2_28`. The script must run the repo-root-available build (like the Makefile) so `build.rs` resolves the proto, print the produced wheel path + its platform tag, and fail loudly if the tag is not manylinux. **Prove the aarch64 wheel actually builds** — this exercises cross-compiling `ring`/`rustls`/the ryll git dep, the one place this phase can genuinely break. If the zig cross fails on that chain after a real attempt, pivot to the documented fallback (build inside `quay.io/pypa/manylinux_2_28_aarch64` under QEMU/binfmt) and record which path was used and why in a script comment + the Outcome. Do not weaken to a non-manylinux `linux` tag. |
| 6d | medium | opus | none | **Release + PR CI wiring.** Extend `.github/workflows/release.yml`: add a matrix job `build-proxy-wheels` (`strategy.matrix.target: [x86_64, aarch64]`) that checks out the tag with `fetch-depth: 0`, installs the Rust toolchain (mirror `rust.yml`'s `dtolnay/rust-toolchain@stable`) + maturin, runs `tools/stamp-proxy-version.sh` then `tools/build-proxy-wheel.sh ${{ matrix.target }}`, and uploads each wheel as an artifact; and a `publish-proxy-pypi` job (`environment: release`, `id-token: write`) that downloads the proxy wheels and publishes them to the `kerbside-proxy` PyPI project via `pypa/gh-action-pypi-publish` (proxy publishes before/independently of the existing `kerbside` publish). Also add the stamp step to the existing `build` job so the `kerbside` wheel ships the exact pin. Keep every step body ≤5 lines (logic is in the `tools/` scripts). Separately, extend `.github/workflows/rust.yml` with a native-only `maturin build --release` smoke step (after the existing `Build (release)` step) so packaging regressions surface on `rust/**` PRs. Update `RELEASE-SETUP.md` with the second trusted publisher (project `kerbside-proxy`, workflow `release.yml`, environment `release`) + a note on the two-package lockstep and pending-publisher setup. |
| 6e | medium | sonnet | none | **Docs + Outcome.** Update `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `docs/proxy-architecture.md`, and `docs/configuration.md` to describe the two-package split (`kerbside` pure-Python + `kerbside-proxy` maturin bin wheel), the exact-version lockstep pin, the wheels-only x86_64+aarch64 matrix, and how `find_proxy_bin()` resolves the wheel-installed binary on `PATH`. Note the sdist / musllinux / self-containment items as future work. Write the phase-6 Outcome section in this plan after the pre-push audit; flip the phase-6 row in `PLAN-rust-proxy.md`'s Execution table and `docs/plans/index.md` to Complete. |

Sequencing: 6a is the foundation (the maturin project must exist before
anything builds a wheel). 6b (version lockstep) and 6c (aarch64 build)
both depend on 6a but are independent of each other and can proceed in
parallel worktrees. 6d wires 6b+6c into CI. 6e is last. 6a and 6c use
worktrees because they are build-experimental (wheel layout, cross
toolchain); 6b and 6d are safe in the main tree.

## Success criteria

- `maturin build` from `rust/kerbside-proxy/` produces a platform-tagged
  wheel that carries the `kerbside-proxy` binary; installing it puts the
  binary on `PATH` and `find_proxy_bin()` (phase 5) resolves it via
  `shutil.which` with no `KERBSIDE_PROXY_BIN` override.
- Both a manylinux **x86_64** and a manylinux **aarch64** wheel build
  successfully (aarch64 cross-compiled), each carrying a correct
  manylinux platform tag.
- `kerbside`'s `pyproject.toml` exact-pins `kerbside-proxy`, and
  `tools/stamp-proxy-version.sh` keeps the crate version and the pin
  equal to a given `v*` release version (single source of truth).
- `release.yml` builds and publishes both PyPI packages from one `v*`
  tag under the existing `release`-environment approval; `rust.yml`
  builds a wheel on `rust/**` PRs as a regression guard.
- No sdist is published for `kerbside-proxy` (wheels-only).
- Rust `fmt`/`clippy -D warnings`/`test` still green; Python
  `flake8`/`py3` green; `pre-commit` clean; docs updated; pre-push audit
  clean.

## Future work (recorded)

- **`kerbside-proxy` sdist** once the crate is self-contained (relocate
  or vendor `kerbside.proto` into the crate so `build.rs` needs no
  out-of-crate path) and, ideally, once `shakenfist-spice-protocol` is a
  crates.io release rather than a rev-pinned git dependency (so a source
  build needs no network git fetch).
- **musllinux wheels** (Alpine-based deployments) and additional
  architectures beyond x86_64/aarch64.
- **Dynamic in-tree pin** via a PEP 517 build-backend shim (always-exact
  `kerbside-proxy==<setuptools_scm version>` in the source tree) if the
  release-time stamp ever proves error-prone.
- Phase 8 (cutover) flips `PROXY_IMPLEMENTATION` to `rust` by default and
  removes the Python proxy; the packaging here is what makes the Rust
  binary present for that flip.

## Outcome

Completed 2026-07-08 on the kerbside `rust-proxy-phase-6` branch,
unmerged and unpushed pending operator review. All planned steps landed;
each was proven by building real wheels, not just by inspection.

- 6a (`8915675`): the maturin `bindings = "bin"` project
  (`rust/kerbside-proxy/pyproject.toml` + a crate `README.md`), version
  read dynamically from `Cargo.toml`. `build.rs` was left untouched —
  wheels-only means the CI build always runs from a full repo checkout,
  so its `../../kerbside/rpc/kerbside.proto` reference resolves. Verified:
  `maturin build` produces a platform-tagged wheel carrying
  `kerbside-proxy` under `*.data/scripts/`; installing it into a fresh
  venv puts the binary on `PATH` and `shutil.which('kerbside-proxy')`
  (phase 5's `find_proxy_bin` leg) returns it.
- 6b (`aba9bae`): `tools/stamp-proxy-version.sh` — the single-source-of
  -truth lockstep. It stamps the release version into the crate
  `Cargo.toml` and inserts the exact `kerbside-proxy==X.Y.Z` pin into
  `kerbside`'s `pyproject.toml` (before a `# KERBSIDE_PROXY_PIN` marker;
  idempotently replacing an existing pin). **Design decision 4 was
  revised during this step**: a *committed* `==` pin was tried first and
  broke `tox -epy3` (it resolves a `kerbside-proxy` release that does not
  exist on PyPI yet), so the committed tree carries only the marker and
  the release inserts the pin. Verified: insert then replace paths, a dev
  version rejected, and `tox -epy3` green with the marker-only tree.
- 6c (`1294d5e`): `tools/build-proxy-wheel.sh` — a manylinux_2_28 wheel
  per architecture via maturin `--zig` (pinned-glibc sysroot; aarch64
  cross-compiled from x86_64 with no emulation). Verified in the
  `rust:slim-bookworm` container: **both** `manylinux_2_28_x86_64` and
  `manylinux_2_28_aarch64` wheels build; the aarch64 wheel carries a
  genuine ARM aarch64 ELF whose highest referenced symbol is `GLIBC_2.28`,
  so the tag is honest. The risky part — cross-compiling the
  `ring`/`rustls`/`aws-lc-rs`/ryll dependency chain — worked with zig; the
  documented QEMU-container fallback was not needed.
- 6d (`3e9769b`): CI wiring. `release.yml` now stamps the pin in the
  `kerbside` build (tag builds only), builds the proxy wheels in a
  `[x86_64, aarch64]` matrix job, and publishes them to the separate
  `kerbside-proxy` PyPI project in a `publish-proxy-pypi` job that
  `publish-pypi` depends on (so the proxy is on PyPI before `kerbside`,
  which pins it). `rust.yml` gained a cheap `maturin build` smoke step as
  a per-PR packaging guard. `RELEASE-SETUP.md` documents the second
  trusted publisher and the lockstep model.
- 6e (`6075931`, this commit): docs (`README.md`, `AGENTS.md`,
  `docs/proxy-architecture.md`) describe the two-package split, and this
  Outcome + the status flips below.

Notable decisions, deliberate:

- **Wheels-only was a genuine simplification, not just a smaller scope.**
  It removed the need to make the crate self-contained for an sdist (the
  proto lives outside the crate); the CI wheel build sees the whole repo,
  so `build.rs` is unchanged. Making the crate self-contained (relocate
  the proto) and, ideally, a crates.io release of
  `shakenfist-spice-protocol` are recorded as the prerequisites for a
  future sdist.
- **Both architectures build with `--zig`, including x86_64.** Using zig
  for the native build too pins the glibc floor at 2.28, so a
  `manylinux_2_28` tag is honest even though the CI/build host has a newer
  glibc — avoiding a manylinux container for x86_64.
- The manylinux floor is `manylinux_2_28` (glibc 2.28). The other open
  questions (native-vs-container x86_64 — resolved to native+zig;
  Docker-vs-runner maturin — CI uses the runner toolchain, local proof
  used Docker) landed on their planned leans.

### Pre-push audit (2026-07-08)

Reviewed the phase-6 diff (`git diff rust-proxy-phase-5..HEAD`; 12 files,
all intended, no tracked build artifacts). Rust `fmt`/`clippy`/`test` are
untouched by this phase; `pre-commit` (flake8 + the 66-test py3 suite +
actionlint on the workflow YAML) is green. **No blocking, high, or medium
findings.**

- **Injection**: `stamp-proxy-version.sh` validates the version against
  `^[0-9]+\.[0-9]+\.[0-9]+$` before it reaches any `sed`, so no shell/sed
  metacharacters can be injected; both scripts are `set -euo pipefail`
  with explicit argument validation and clear failure on missing
  files/markers.
- **Release integrity**: publishing uses the existing OIDC trusted-
  publisher model (no tokens); a second trusted publisher for the
  `kerbside-proxy` project is required (documented in `RELEASE-SETUP.md`).
  The job graph gates both publishes behind `sign-tag` and the `release`
  environment, and orders the proxy publish before `kerbside` so the pin
  always resolves.
- **Correctness**: the wheel-detection glob is architecture-specific
  (`x86_64`/`aarch64` are not substrings of each other), so a matrix leg
  cannot mistake the other arch's wheel; the `build` job's incidental
  stamp of `Cargo.toml` (unused by the Python build) is harmless because
  each job checks out fresh and the proxy job stamps its own tree.

Observation (not a fix): each `environment: release` job triggers the
required-reviewer gate, so a release now has three gated jobs (`sign-tag`,
`publish-proxy-pypi`, `publish-pypi`) rather than two — the pre-existing
pattern, one more approval. Left as-is.

## Back brief

Before executing any step, back brief the operator on the approach and
its alignment with this plan and the master plan. The two operator
decisions are settled: **x86_64 + aarch64** manylinux wheels (arm64
wheels satisfy the Kolla-Ansible arm64 CI), and **wheels only, no
sdist** (clean install failure on unsupported platforms over a fragile
source build, and it sidesteps the proto-self-containment problem for
now). The remaining choices — the committed-pin policy, the manylinux
floor, native-vs-container x86_64 build, and Docker-vs-runner maturin
invocation — are the planner's leans recorded under Open questions and
are open to revision.
