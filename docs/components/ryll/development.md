# Development

How to build, test, and contribute to ryll. For macOS-specific
setup see [development-macos.md](/components/ryll/development-macos/); for AI
coding assistant conventions see
[AGENTS.md](https://github.com/shakenfist/ryll/blob/develop/AGENTS.md).

## Building with the devcontainer (recommended)

The project includes a devcontainer for consistent builds:

```bash
# Build debug version
make build

# Build release version
make release

# Run tests
make test

# Run linting (rustfmt + clippy)
make lint

# Run linting with auto-fix
make lint-fix

# Start a test QEMU SPICE server (UEFI latency guest, downloads on first run)
make test-qemu

# Stop the test QEMU instance
make test-qemu-stop
```

## Building with a local Rust installation

If you have Rust installed locally with the required dependencies:

```bash
cargo build --release
```

**Required system dependencies** (Debian/Ubuntu):

```bash
apt-get install -y \
    libxcb-render0-dev libxcb-shape0-dev libxcb-xfixes0-dev libxcb1-dev \
    libx11-dev libxkbcommon-dev libgl1-mesa-dev libegl1-mesa-dev \
    libwayland-dev libssl-dev pkg-config
```

**macOS** (Apple Silicon): No additional system libraries are needed --
just Xcode Command Line Tools and Rust. See
[development-macos.md](/components/ryll/development-macos/) for full setup
instructions.

## Cargo features

ryll ships several default-on Cargo features that can be opted
out at build time:

- **`gui`** (default-on) — eframe, egui, arboard, rfd, and the
  whole interactive UI.  Disabling it produces a `--headless`
  / `--web`-only binary that does not link the X11/Wayland/
  winit runtime and the runtime image drops libgl1, libx11-6,
  libxcb1, libxkbcommon0, libwayland-client0.  Running such a
  binary without `--headless` or `--web` exits with a clear
  "this binary was built without the `gui` feature" message.
- **`audio`** (default-on) — cpal, opus-decoder, rtrb, and the
  SPICE playback channel in `shakenfist-spice-renderer`.
  Disabling it drops libasound2 from the runtime image and
  skips the SPICE playback channel at connect time (the rest
  of the session is unaffected).
- **`capture`** (default-on) — pcap-file + etherparse + mp4 for
  `--capture` recording.
- **`digest-decode`** (default-off) — adds the
  shakenfist-visual-digest crate as a git dependency and
  enables a polling task that scans the primary surface for a
  QR-encoded visual digest and emits a `digest_updated`
  control-socket event on each frame counter change.  Built
  only for the kerbside test harness; not in production ryll.

The slim test-harness binary is built with
`cargo build --release --no-default-features -p ryll`.  See
[control-socket-protocol.md](/components/ryll/control-socket-protocol/)
for the `surface_drawn` and `digest_updated` event shapes.

## Workspace dependency convention

Every workspace crate carries `version.workspace = true`, so
the single version in the root `Cargo.toml` is the only place
a release bump has to happen.

Dependencies between workspace crates must declare **both** a
path and a version:

```toml
shakenfist-spice-renderer = { path = "../shakenfist-spice-renderer", version = "0.1.7" }
```

The path wins for local builds, so day-to-day development sees
the working tree rather than a published crate. The version is
what `cargo publish` requires — a path-only dependency cannot
be published to crates.io. Omitting it therefore costs nothing
until release day, and then fails the publish, which is why it
is easy to get wrong. Bump the version alongside the workspace
version whenever the depended-on crate is released; see
[releasing.md](/components/ryll/releasing/).

## Debugging async hangs

A set of diagnostic hooks exists for debugging tokio task hangs
and channel wedges. They were built during the K1 idle-wedge
investigation (an abandoned-receiver deadlock in the session
orchestrator, fixed in `370d8ce5`) and remain in tree:

- **Per-channel run-loop exit logs** — every SPICE channel task
  logs when its run loop exits, cleanly or with error. Always
  on; a channel that never logs an exit is still running (or
  wedged).
- **`RYLL_WATCHDOG_GDB=1`** — an in-process gdb watchdog on the
  main channel's run loop. If the loop goes silent for 5 s it
  dumps `thread apply all bt` for the whole process to
  `/tmp/ryll-watchdog-bt-<pid>-<ts>.txt`. Requires `gdb` on the
  PATH.
- **`RYLL_DISABLE_CLIPBOARD_POLL=1`** — disables the main
  channel's clipboard-polling `select!` arm, for isolating
  whether clipboard integration is implicated in a hang.
- **`--debug-single-thread-runtime`** — CLI flag that forces a
  single-threaded tokio runtime, for ruling scheduler
  interactions in or out.
- **`make build-tokio-console`** — builds ryll with the
  `tokio-console` Cargo feature plus
  `RUSTFLAGS="--cfg tokio_unstable"`. Run the resulting binary
  with `RYLL_TOKIO_CONSOLE=1` and it serves the
  console-subscriber endpoint on `127.0.0.1:6669` for the
  `tokio-console` TUI viewer — per-task waker counts, poll
  times, and last-woken ages. Do not apply
  `--cfg tokio_unstable` to a release build; the regular
  `make build` does not need it.

### Idle-wedge regression test

`make test-k1-idle` (driver: `tools/test-k1-idle.sh`) guards
against the K1 deadlock regressing. It launches ryll headless
against a SPICE server (start one with `make test-qemu` first),
idles for 540 s — well past the historical ~T+466 s wedge point
— and fails on early exit, `event_tx.send()` timeout warnings,
channel errors, or a lower pong count than the idle window
implies. The `IDLE_SECS`, `HOST_PORT`, and `RYLL` environment
variables override the defaults, and the test sets
`RYLL_K1_MAIN_ONLY=1` to run the main channel alone — cheaper,
and the historical wedge fingerprint shows up there regardless.

## Pre-commit hooks

The project uses pre-commit hooks to enforce code quality:

```bash
# Install pre-commit hooks
pre-commit install

# Run checks manually on all files
pre-commit run --all-files

# Or use the script directly
./scripts/check-rust.sh check   # Check mode
./scripts/check-rust.sh fix     # Auto-fix mode
```

The pre-commit hooks run:

- **rustfmt** - Code formatting
- **clippy** - Linting with warnings as errors
- **shellcheck** - Shell script linting

## Review tracking

Whole-file human review state (`REVIEWS.md`, `.vscode/*.weaudit*`)
is maintained with `tools/review-tracking.sh`, a wrapper around the
shared helper in the
[shakenfist/development](https://github.com/shakenfist/development/blob/main/docs/code-review-tracking.md)
repository. In a clone it is run by hand, not from git hooks:
`prune` after a pull to discard reviews of files that have since
changed, `stamp` before committing new review marks, `regen` to
rebuild `REVIEWS.md`, `next` to pick an unreviewed file, and
`status` to report effective coverage at HEAD. On develop itself
the `prune-reviews` workflow runs `prune` automatically after every
push, committing the result back as shakenfist-bot, and the daily
consistency audit in shakenfist/development files an issue when
five or more in-scope files need review.

## CI and automation

GitHub Actions CI builds and tests ryll on Linux (x86_64 + aarch64),
macOS (Apple Silicon), and Windows (x86_64 + aarch64) in two tiers. A
smoke tier runs on pull requests — lint, the self-hosted Linux x86_64
build and tests, a Windows cross-check, and the supply-chain scanners
— while a merge tier runs in the merge queue with the fuzz targets and
the cross-platform build matrix, so the expensive jobs run once,
against the commit that is about to land. Linux x86_64 jobs run on
self-hosted runners with the build wrapped in the devcontainer (via
the same Makefile targets used locally); macOS, Windows, and aarch64
Linux use GitHub-hosted runners because we own no matching hardware.
PRs also receive an automated code review via Claude Code. Changes
that only touch code-review artifacts (`REVIEWS.md`,
`.vscode/*.weaudit*`, `.vscode/review-scope.toml`) skip every CI job
and the CodeQL workflow.

Because `develop` is behind a merge queue, merging a pull request
enqueues it rather than merging it immediately, and the merge tier's
results appear on the queue's run rather than on the pull request.
[ci.md](/components/ryll/ci/) is the full reference: the job inventory, the three
gate checks, how to read a queue ejection, how retesting interacts
with the tiers, and where binaries for a given commit come from.

The commands CI runs on Linux x86_64 are the local ones — `make lint`,
`make check-windows`, `make test`, `make web-smoke` and
`make web-smoke-tls` — so a smoke-tier failure can normally be
reproduced verbatim. `make check-windows` cross-compiles the
`x86_64-pc-windows-gnu` triple from the Linux devcontainer as a cheap
proxy for the msvc builds in the merge tier: it catches `cfg(windows)`
and windows-sys breakage, while msvc-specific and link-time breakage
still surface only in the merge tier.

## Key dependencies

- **eframe/egui** - Immediate mode GUI
- **tokio** - Async runtime
- **tokio-rustls** - TLS support
- **clap** - CLI parsing
- **rsa/sha1** - Authentication encryption
- **image** - JPEG decoding (via the `image` crate with jpeg feature)
- **cpal** - Cross-platform audio output
- **rtrb** - Lock-free ring buffer for audio sample passing
- **opus-decoder** - Pure-Rust Opus audio decoding
- **openh264** - H.264 encoding in `shakenfist-spice-renderer`
  (the encoder pipeline). Capture mode in ryll consumes it
  transitively via the renderer.
- **nusb** - USB device access (pure Rust, no libusb)
- **dav-server** - WebDAV server (RFC 4918, LocalFs backend)
- **hyper** - HTTP/1.1 framing for WebDAV byte-stream transport
- **webrtc = "0.20.2"** - DTLS/SRTP/ICE/SCTP/STUN stack for
  `shakenfist-spice-webrtc` (browser-bridge crate); an async
  shim over the sans-io `rtc = "0.20.2"` core, which is a
  direct dependency in its own right because webrtc's public
  API takes `rtc` types it does not re-export. `if-addrs`
  enumerates host interfaces to pick UDP bind addresses, and
  `async-trait` is required to implement
  `PeerConnectionEventHandler`.
- **opus = "0.3"** - libopus bindings for the synthetic Opus
  pump in the webrtc crate; `audiopus_sys` builds libopus from
  source in the devcontainer
- **ctrlc** - Cross-platform Ctrl+C handling for graceful shutdown

## Test suite specifics

Beyond `make test`, a few tests are worth knowing about individually:

- **Decompression unit tests** cover the LZ / GLZ / LZ4 / QUIC
  algorithms directly.
- **Encoder smoke test** (`shakenfist-spice-renderer/tests/encoder_smoke.rs`)
  runs for ~3 seconds and writes `target/encoder_smoke.h264`. Run
  `ffplay target/encoder_smoke.h264` after `make test` to visually
  verify encoder output.
- **WebRTC H.264 packetiser test**
  (`shakenfist-spice-renderer/tests/webrtc_h264_smoke.rs`) verifies
  `H264Payloader` accepts the encoder's Annex-B NAL output.
- **Loopback integration test** (`shakenfist-spice-webrtc/tests/loopback.rs`)
  drives a production `WebrtcBridge` against a `TestPeer` in one process
  and asserts video, audio and datachannel all flow end to end. A second
  case offers a narrow codec set (one H.264 fmtp, browser-chosen payload
  numbers) to prove the pumps stamp the *negotiated* payload type rather
  than a constant.
- **Control socket integration tests**
  (`shakenfist-spice-renderer/tests/control_socket.rs`) exercise every v1
  verb and event without a real SPICE session, using a stub
  `StatusProvider` and an in-process broadcast channel. New verbs or
  events should ship with a matching test here.
- **ICE gathering soak**: `RYLL_GATHERING_SOAK=1 make test` runs the
  20-iteration invariant-candidate-count soak in
  `accept_offer_answer_carries_all_candidates`. Off by default because
  exact cross-run candidate-count equality is coupled to host interface
  churn (docker/veth appearing, IPv6 temporary addresses rotating). Run
  it on a quiet host when touching the ICE gathering signal.

Integration testing against real traffic needs a SPICE server;
`make test-qemu` starts one locally. Headless mode is what CI uses for
protocol-level testing.

## Helper tools

### Inspecting a `--capture` pcap

`tools/pcap-inspect.py` is a pure-Python helper (no tshark
or scapy dependency) for sifting through a ryll capture.
Three subcommands:

```
tools/pcap-inspect.py opcodes   <path>                 # histogram of SPICE message types
tools/pcap-inspect.py draw-copy <path>                 # DRAW_COPY breakdown by surface / image type
tools/pcap-inspect.py timeline  <path> [--since-last N]  # server-side messages in order
```

Typical use: when investigating a rendering artefact,
`opcodes` tells you whether the problem window even
contains the draw ops you thought it did (this is how we
established that a "static" artefact was 100% DRAW_COPY
rather than missing draw ops); `draw-copy` narrows further
to the image types involved; `timeline --since-last 5`
dumps the last five seconds of traffic when the capture
was stopped right after the artefact appeared.

ryll's pcap files are big-endian libpcap format carrying
synthetic TCP frames around the raw post-link SPICE
stream. The helper handles that without any extra flags.

### Smoke-testing `--web` mode

`tools/web-smoke.sh` verifies that `ryll --web` starts,
binds the HTTP server, and shuts down cleanly on SIGTERM.
Usage:

```
tools/web-smoke.sh [path-to-ryll-binary]
```

Defaults to `target/release/ryll`; `WEB_PORT` env var
overrides the port (default `18080`). The script creates a
temporary stub `.vv`, launches ryll, waits 3 seconds,
SIGTERMs, and asserts clean exit within 5 seconds. CI runs
this in the `build-linux` job via `make web-smoke` and
`make web-smoke-tls`, inside the devcontainer.

### Example control-socket client

`examples/control-socket-demo.py` is a stdlib-only Python script,
runnable directly, demonstrating the full hello → status → subscribe
→ send_key → paste → screenshot → disconnect sequence. It is the
starting point for downstream test-harness drivers. The wire contract
it implements is [`control-socket-protocol.md`](/components/ryll/control-socket-protocol/).
