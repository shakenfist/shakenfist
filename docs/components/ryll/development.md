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
repository. It is run by hand, not from git hooks: `prune` after a
pull to discard reviews of files that have since changed, `stamp`
before committing new review marks, `regen` to rebuild `REVIEWS.md`,
and `next` to pick an unreviewed file.

## CI and automation

GitHub Actions CI builds and tests ryll on Linux (x86_64 + aarch64),
macOS (Apple Silicon), and Windows (x86_64 + aarch64) on every push to
`develop` and on pull requests. Linux x86_64 jobs run on self-hosted
runners with the build wrapped in the devcontainer (via the same
Makefile targets used locally); macOS, Windows, and aarch64 Linux use
GitHub-hosted runners because we own no matching hardware. PRs also
receive an automated code review via Claude Code. Changes that only
touch code-review artifacts (`REVIEWS.md`, `.vscode/*.weaudit*`,
`.vscode/review-scope.toml`) skip the CI and CodeQL workflows
entirely; the supply-chain content scanners still run on them.

Workflows in `.github/workflows/`:

| Workflow | Purpose |
|----------|---------|
| `ci.yml` | Lint, fuzz smoke, build, test (multi-platform), automated PR review |
| `manual-build.yml` | On-demand binary builds of arbitrary branches |
| `release.yml` | Build and publish release artifacts |
| `codeql-analysis.yml` | CodeQL security scanning |
| `supply-chain.yml` | Dependency advisories, license policy, secret scanning, bidi/unicode checks |
| `renovate.yml` | Automated dependency updates (hourly) |
| `export-repo-config.yml` | Daily repository configuration export |
| `pr-re-review.yml` | Bot-triggered PR re-review (`@shakenfist-bot please re-review`) |
| `pr-address-comments.yml` | Bot-triggered comment addressing (`@shakenfist-bot please address comments`) |
| `pr-retest.yml` | Bot-triggered CI re-run (`@shakenfist-bot please retest`) |

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
- **webrtc = "0.17.1"** - DTLS/SRTP/ICE/SCTP/STUN stack for
  `shakenfist-spice-webrtc` (browser-bridge crate; also pulls
  in `rtp = "0.17.1"` for H.264 RTP packetisation)
- **opus = "0.3"** - libopus bindings for the synthetic Opus
  pump in the webrtc crate; `audiopus_sys` builds libopus from
  source in the devcontainer
- **ctrlc** - Cross-platform Ctrl+C handling for graceful shutdown
