# Phase 7: CI build + packaging

## Prompt

Before responding to questions or making changes, read the
master plan at `docs/plans/PLAN-web-frontend.md` (Phase 7
section in the Execution table and the prose summary), the
Phase 6 plan to understand what just shipped, and the existing
CI/release workflows. Key files:

- `.github/workflows/ci.yml` — the main per-PR workflow.
  Builds `ryll` on Linux/macOS/Windows, runs
  `cargo test --workspace`, builds `.deb` + `.rpm` on Linux,
  tarball on macOS, zip on Windows. Linux uses
  `--default-features`; Windows uses `--no-default-features`
  (this disables the `capture` feature only).
- `.github/workflows/release.yml` — runs on tag push.
  Mirrors ci.yml's build matrix, publishes selected
  workspace crates to crates.io, creates GitHub Release,
  bumps Homebrew tap.
- `Cargo.toml` (workspace root) — lists all six member
  crates. `version = "0.1.4"` is used everywhere via
  `version.workspace = true`.
- `ryll/Cargo.toml` — depends on `shakenfist-spice-renderer`
  and `shakenfist-spice-webrtc` (added Phases 1 and 3); both
  use the `path + version` dual-spec so cargo resolves
  locally during dev and from crates.io when consumed by
  third parties.
- `shakenfist-spice-renderer/Cargo.toml` — the renderer
  crate. Pulls in `openh264 = "0.6"` (H.264 encoding for
  the web encoder pipeline). openh264 0.6 downloads a
  precompiled Cisco binary at build time and links to it
  cross-platform; no system library required.
- `shakenfist-spice-webrtc/Cargo.toml` — the WebRTC bridge
  crate. Pulls in `webrtc = "0.17.1"` (pure Rust),
  `opus = "0.3"` (libopus via the `audiopus_sys` crate,
  which probes `pkg-config` for libopus and otherwise
  builds it from source — needs a C compiler, CMake, and
  optionally NASM for the asm path).
- `ryll/src/main.rs::run_web` — the entry point invoked
  when `--web` is passed. Takes `--web-host` (default
  `127.0.0.1`) and `--web-port` (default `8080`). Prints
  the token-bearing URL to stdout on startup.

External: cargo-deb auto-detects shared-library deps via
`$auto`; cargo-generate-rpm uses an explicit `requires` list
in `[package.metadata.generate-rpm]` (check
`ryll/Cargo.toml`).

Flag any uncertainty rather than guessing.

## Goal

Verify the new web-frontend dependencies build and link
cleanly on every supported platform; ship the two new
workspace crates (`shakenfist-spice-renderer`,
`shakenfist-spice-webrtc`) through the existing
publish-to-crates.io pipeline; surface any cross-platform
gaps as either fixes or documented limitations.

After Phase 7:

- `cargo build --release -p ryll` succeeds on Linux,
  macOS, and Windows on CI runners with no human
  intervention. Windows still uses
  `--no-default-features` to skip the `capture` feature
  but `--web` builds in.
- `cargo test --workspace` is green on every platform.
- `cargo deb` and `cargo generate-rpm` produce installable
  packages whose runtime dependencies cover libopus
  (the only new dynamic-link dep introduced by this
  series — openh264 statically links its blob).
- `.github/workflows/release.yml`'s `publish-crates` step
  publishes `shakenfist-spice-renderer` and
  `shakenfist-spice-webrtc` in dependency-correct order on
  the next tag push.
- A lightweight smoke test in CI launches `ryll --web` for
  a few seconds, verifies the process stays alive, then
  SIGTERMs cleanly. This catches regressions where
  startup-time wiring (rustls provider install, axum bind,
  reaper spawn) breaks on a platform.

Out of scope:

- Runtime functional testing on macOS or Windows beyond
  startup. The MVP target is Linux; cross-platform link is
  enough.
- Container image (`Dockerfile`) for ryll-as-a-service —
  deferred to Phase 8 (operator docs + systemd example).
- An MSRV bump or pinning beyond what cargo already
  resolves.
- A separate publish job for the renderer + webrtc crates
  decoupled from the ryll release cadence — they ship in
  lockstep via the workspace version.

## Scope

In:

- `.github/workflows/ci.yml` — system-dep tweaks if any
  Phase 7 verification step needs them (e.g. nasm / cmake
  on macOS or Windows runners). Add the `--web` smoke
  test step on Linux.
- `.github/workflows/release.yml` — add the two new
  crates to `publish-crates` (in dependency order: protocol
  → compression → renderer → usbredir → webrtc → ryll).
- `ryll/Cargo.toml` — `[package.metadata.deb]` and
  `[package.metadata.generate-rpm]` updated if libopus or
  any other runtime dep needs to be declared explicitly.
- `tools/web-smoke.sh` — new script the CI step calls.
  Launches `ryll --web` with a stub `.vv` (or `--web-only`
  if such a flag exists; otherwise mock target), polls the
  process, sends SIGTERM, asserts clean exit.

Out:

- All items in "Out of scope" above.
- Refactoring the Cargo.toml structure or the workspace
  layout.
- Adding a new release artifact format.

## Approach

### Step 7a: New crates in `publish-crates`

`release.yml`'s publish-crates step currently lists four
crates:

```
shakenfist-spice-protocol
shakenfist-spice-compression
shakenfist-spice-usbredir
ryll
```

After Phase 7a, the order becomes:

```
shakenfist-spice-protocol      # leaf
shakenfist-spice-compression   # depends on protocol
shakenfist-spice-renderer      # depends on protocol + compression
shakenfist-spice-usbredir      # depends on protocol
shakenfist-spice-webrtc        # depends on renderer
ryll                           # depends on all of the above
```

Each `cargo publish -p <name>` step is idempotent on
already-published versions (returns an error which we'll
treat as a hard failure on the first tag-push and a
no-op-with-warning on retries — same semantics as today).

Cross-check: read each crate's `[dependencies]` section to
confirm the order. webrtc depends on renderer (for
`EncoderControl` + `EncodedFrame`); renderer does not
depend on webrtc.

### Step 7b: Cross-platform build verification

Smoke-test `cargo build` on each platform locally (via the
devcontainer + via a one-off manual run on the user's macOS
hardware if accessible — otherwise via the CI matrix
itself, which is the canonical answer). Surface and address
any issues:

- **Windows**: `opus` crate may need a C compiler and
  CMake on the runner. The default Windows runner has
  MSVC + cmake but the `audiopus_sys` build script also
  expects pkg-config to find libopus first. If pkg-config
  fails, `audiopus_sys` falls back to compiling libopus
  from source — which works but adds ~30 s to CI.
  Document the fallback; do not pin a libopus path.
- **macOS**: same `opus` story. macOS runners have brew,
  but we don't want to require `brew install opus` on
  every run — the source-build fallback is acceptable.
- **Linux**: libopus-dev must be installed. CI currently
  does not install it; add to the apt-get list in both
  ci.yml and release.yml.
- **openh264**: 0.6 downloads its blob via a build script.
  Verify on each platform; if a runner blocks outbound
  HTTPS to Cisco's CDN, document and choose a vendoring
  alternative. Phase 2 verified this works on Linux; the
  CI matrix is the next checkpoint.

### Step 7c: --web smoke test

A bash script at `tools/web-smoke.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Smoke-test that ryll --web starts, binds, and shuts down
# cleanly. Does NOT verify a full WebRTC handshake — that's
# what tests/loopback.rs covers.

BIN="${1:-target/release/ryll}"
PORT="${WEB_PORT:-18080}"

# Use a synthetic .vv file pointing at a non-existent SPICE
# server. ryll --web should still start the HTTP server even
# if the SPICE side fails to connect; we're testing the web
# entry point. If ryll requires a real SPICE target before
# binding the HTTP listener, this test needs to be reshaped.
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT
cat > "$TMPDIR/test.vv" <<EOF
[virt-viewer]
type=spice
host=127.0.0.1
port=15900
EOF

"$BIN" --web --web-port "$PORT" --vv "$TMPDIR/test.vv" &
PID=$!
sleep 3

if ! kill -0 "$PID" 2>/dev/null; then
    echo "ryll --web exited prematurely"
    exit 1
fi

# Send SIGTERM and verify it shuts down within 5s.
kill -TERM "$PID"
WAIT_START=$SECONDS
while kill -0 "$PID" 2>/dev/null; do
    if (( SECONDS - WAIT_START > 5 )); then
        echo "ryll --web did not exit within 5s of SIGTERM"
        kill -9 "$PID" 2>/dev/null || true
        exit 1
    fi
    sleep 0.2
done

wait "$PID" || true
echo "smoke test passed"
```

Two concerns to verify against `run_web`:

1. **Does `--web` require a working SPICE target before
   binding the HTTP port?** If yes, the smoke test needs
   either a tiny stub SPICE listener (a `nc -l` would work
   for a few bytes) or a `--web-only` flag that binds the
   HTTP port without attempting SPICE.
2. **Does `--web` reject SIGTERM the way Phase 6 fixed for
   SIGINT?** `ctrlc` crate handles both; Phase 6's
   `with_graceful_shutdown` should drain on either.

If (1) blocks the test, fall back to `nc -l 15900 &` as a
prerequisite step in the CI workflow. This is a fixture, not
production code.

### Step 7d: cargo-deb / RPM metadata audit

Read `ryll/Cargo.toml`'s `[package.metadata.deb]` and
`[package.metadata.generate-rpm]` blocks. The current
`depends = "$auto"` for deb means cargo-deb scans the binary
for shared-library imports — including libopus.so.0 if the
opus crate dynamic-links it. For rpm, the `requires` list is
explicit; check whether libopus.so.0 needs to be added.

If `audiopus_sys` static-links libopus on the CI runner
(because pkg-config didn't find one), then no runtime
dependency on libopus exists. If it dynamic-links (because
libopus-dev was present), the `.deb` needs `libopus0`. We
want consistent behaviour: pin to source-build (static link)
or pin to dynamic link — pick one and document.

**Decision rule:** prefer source-build (static link). Do
not install `libopus-dev` in CI; the audiopus_sys fallback
gives us a self-contained binary that doesn't depend on the
target system's libopus version. Cost: ~30 s extra build
time, recouped by zero runtime-dep surface.

If decisions in 7b mean libopus-dev IS installed on Linux,
revisit and add `libopus0` to the deb/rpm metadata.

### Step 7e: Documentation + status flips

- `docs/plans/PLAN-web-frontend.md` — flip Phase 7 row to
  Complete with the four commit SHAs.
- `docs/plans/index.md` — Phase 7 marker.
- `README.md` — bump "0–6 of 8 complete" to "0–7 of 8".
- `docs/web-frontend.md` — short note on the CI smoke
  test as a sanity check.
- `ARCHITECTURE.md` — extend the Phase 6 section with a
  brief Phase 7 paragraph (CI gate + crate publishing).
- `docs/portability.md` — record that Linux is the
  verified target; macOS and Windows compile-only.
- `AGENTS.md` — note `tools/web-smoke.sh` if it has a
  Tools section.

## Prerequisites

- Phase 6 complete on `thought-bubble`. (It is — last
  commit `0988558f`.)

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a   | low    | sonnet | none     | Add `shakenfist-spice-renderer` and `shakenfist-spice-webrtc` to the `publish-crates` step in `.github/workflows/release.yml`, in dependency order (renderer between compression and usbredir; webrtc between usbredir and ryll). Verify by reading each new crate's `[dependencies]` section. Single commit. |
| 7b   | medium | sonnet | none     | Read `Cargo.toml` for openh264 + opus + webrtc; document the build-script behaviour in a code comment in the workflow. Update `ci.yml` and `release.yml` to install only the deps that are *required* (likely no addition — the audiopus_sys fallback covers libopus from source). Verify the matrix builds locally where possible; otherwise rely on a CI run for confirmation. Single commit; if no changes are needed, the commit just adds clarifying comments. |
| 7c   | medium | sonnet | none     | Write `tools/web-smoke.sh` per the plan. Verify locally that `ryll --web --vv <stub>` starts and shuts down cleanly on SIGTERM (this requires checking against `run_web`'s actual behaviour — does it bind HTTP before SPICE, does it accept SIGTERM, does it require `--web-only`-style flag). Add a CI step on the Linux matrix entry only that runs `tools/web-smoke.sh target/release/ryll`. Single commit. |
| 7d   | low    | sonnet | none     | Read `[package.metadata.deb]` and `[package.metadata.generate-rpm]` in `ryll/Cargo.toml`. Confirm the source-build-libopus decision means no runtime dep changes. Add a code comment recording the decision. Single commit; likely a comment-only change. |
| 7e   | medium | sonnet | none     | Documentation sweep per the "Documentation + status flips" section. Single commit. |

After 7e, Phase 7 is done. Push the branch and observe a
real CI run; if it surfaces a platform issue, follow up
with a focused fix commit (do not pre-emptively fix
hypothetical issues).

## Step details

### Step 7a expanded brief

Open `.github/workflows/release.yml`. Find the
`publish-crates` job (around line 188). The existing
sequence is protocol → compression → usbredir → ryll.
Insert two new steps:

```yaml
- name: Publish shakenfist-spice-renderer
  env:
    CARGO_REGISTRY_TOKEN: ${{ secrets.CARGO_REGISTRY_TOKEN }}
  run: cargo publish -p shakenfist-spice-renderer

- name: Publish shakenfist-spice-webrtc
  env:
    CARGO_REGISTRY_TOKEN: ${{ secrets.CARGO_REGISTRY_TOKEN }}
  run: cargo publish -p shakenfist-spice-webrtc
```

renderer goes after compression (it depends on protocol
and compression). webrtc goes after usbredir but before
ryll (it depends on renderer).

Verify dependency direction by `grep -nE
"shakenfist-spice-(protocol|compression|renderer|usbredir|webrtc)"
shakenfist-spice-*/Cargo.toml`.

### Step 7c expanded brief

The script's tricky bit is verifying behaviour against
`run_web` without paying for a real SPICE target. Read
`ryll/src/main.rs::run_web` (around line 340). Look for:

- Order of `web::run` (HTTP bind) vs `run_connection`
  (SPICE attach). If HTTP binds first, the smoke test
  works as designed.
- SIGTERM handling. The `ctrlc` crate intercepts SIGINT
  and SIGTERM together by default, so this is likely
  fine.

If `run_web` connects to SPICE before binding HTTP, two
options:

1. Reshape `run_web` so HTTP binds first (probably the
   right architectural call anyway — the operator wants
   the URL printed before SPICE may have failed).
2. Add a tiny `nc -l 15900 &` listener in CI and clean it
   up on exit.

Option 1 is cleaner if the change is small. If it touches
many things, defer to a follow-up and use Option 2 for
Phase 7c.

### Step 7e expanded brief

Mechanical doc updates. Match the format used in Phase 6's
6d commit: parity matrix + master plan + index + README +
ARCHITECTURE + web-frontend.md. Don't write essays — the
reconnect/lifecycle prose from 6d is the right tone.

## Acceptance criteria

- `make lint` and `make test` pass after each step.
- After 7a: `release.yml` lists both new crates.
- After 7b: comments document the build-script decision;
  CI matrix steps install only what's needed.
- After 7c: `tools/web-smoke.sh` exits 0 locally; CI step
  invokes it on Linux.
- After 7d: comment in `ryll/Cargo.toml` documents the
  source-build-libopus decision.
- After 7e: master plan + parity matrix + README reflect
  Phase 7 complete.
- `pre-commit run --all-files` passes after each commit.

## Risks

- **Real CI run surprises.** None of the 7a–7e changes
  exercise CI directly; the user pushes the branch and
  observes the result. If a new platform fails, that's a
  follow-up fix commit. Document this in 7e: Phase 7 ships
  the *gates*; the *actual cross-platform health* is
  observed on the next push, not before.
- **opus build time on Windows/macOS.** The audiopus_sys
  source fallback adds ~30 s. If the runners are
  CPU-limited, total CI time grows. Acceptable for now.
- **openh264 binary download.** Its build script reaches
  out to a Cisco CDN. If that endpoint is flaky or blocked,
  cache the blob in CI (a known Phase 7 follow-up). Not
  pre-empted in this plan.
- **`run_web` startup ordering.** If 7c reveals that HTTP
  binds after SPICE attach, a small refactor falls into
  Phase 7's scope. Capped at ~50 LoC; if larger, defer
  to a focused follow-up.
- **Crate publish failures.** Publishing the new crates
  requires the `CARGO_REGISTRY_TOKEN` secret to remain
  valid. The existing pipeline already uses it, so no
  change there.

## Documentation updates

After 7e:

- `docs/plans/PLAN-web-frontend.md` — Phase 7 row Complete.
- `docs/plans/index.md` — Phase 7 entry.
- `README.md` — progress marker → 0–7/8.
- `docs/web-frontend.md` — CI smoke test note.
- `ARCHITECTURE.md` — Phase 7 paragraph.
- `docs/portability.md` — Linux verified, macOS/Windows
  compile-only.
- `AGENTS.md` — `tools/web-smoke.sh` if relevant.

## Estimated total scope

~300–500 lines across five small commits. 7a is ~10 LoC
of YAML. 7b is mostly comments unless libopus-dev needs
adding. 7c is ~80 LoC of bash + ~20 LoC of YAML. 7d is
~10 LoC of comments. 7e is ~150 LoC of doc edits.

## Back brief

Before executing 7a, the implementing agent should
back-brief: confirm the dependency order
(protocol → compression → renderer → usbredir → webrtc →
ryll) by reading each crate's `[dependencies]` section,
and confirm there are no circular deps.

For 7c, the agent should back-brief on `run_web`'s
startup ordering before writing the script — if HTTP
binds after SPICE, that needs to be flagged for a
decision (refactor vs nc fixture) before any code is
written.
